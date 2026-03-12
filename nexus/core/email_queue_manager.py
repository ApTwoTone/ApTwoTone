"""
Email Queue Manager — generates, stages, and sends email batches.

Workflow:
  1. Evening (8 PM): generate_queue() creates next day's batch → email_queue table
  2. Morning: Kai reviews in frontend, edits/removes emails
  3. 8 AM: send_approved() reads from email_queue and sends
  4. After send: status updated to 'sent' or 'failed'

CRITICAL: All sends go through outbound_gate() from integrations/messaging.py (Rule 0).
"""
from __future__ import annotations

import logging
import json
import re
import hashlib
import sqlite3
import socket
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any
from urllib.parse import urlparse
try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

log = logging.getLogger("email_queue_manager")
DB_PATH = Path.home() / ".nexus" / "memory.db"
LA_TZ = ZoneInfo("America/Los_Angeles")

from core.outreach_targeting import (
    booking_priority_score,
    booking_priority_sort_key,
    should_skip_fast_booking_email_candidate,
)

GENERIC_INBOX_LOCAL_PARTS = {
    "info", "hello", "contact", "sales", "support", "admin", "office", "team",
    "events", "event", "weddings", "wedding", "bookings", "booking", "inquiry",
    "inquiries", "reservations", "frontdesk", "mail", "service",
}

LOW_INTENT_NAME_KEYWORDS = (
    "golf club", "golf course", "country club", "resort", "marriott", "hilton",
    "hyatt", "sheraton", "ritz", "playground", "performing arts",
    "university", "campus", "cowork", "workspace",
)

LOW_INTENT_CATEGORY_KEYWORDS = (
    "dessert",
    "churro",
    "bakery",
    "cake",
    "candy",
    "ice_cream",
    "construction",
    "balloon",
    "coffee_cart",
)

HARD_BLOCK_CATEGORY_KEYWORDS = (
    "construction",
    "balloon",
    "dessert",
    "bakery",
    "cake",
    "candy",
    "ice_cream",
    "churro",
    "coffee_cart",
)

EVENT_FIT_CATEGORY_HINTS = (
    "venue", "wedding", "planner", "event", "rental", "catering", "dj",
    "photography", "florist", "tent", "bartending", "quince",
)

GENERIC_INBOX_HIGH_TRUST_HINTS = (
    "venue",
    "planner",
    "catering",
    "rental",
    "party_rental",
    "community_center",
    "quince",
    "event_venue",
    "wedding_venue",
    "banquet_hall",
    "hotel_venue",
    "country_club",
    "winery",
)

EMAIL_ADDR_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
BAD_EMAIL_DOMAINS = {
    "example.com",
    "test.com",
    "fake.com",
    "temp-mail.org",
    "guerrillamail.com",
    "mailinator.com",
    "throwaway.email",
    "hmail.com",
    "putlook.com",
}
_MX_CACHE: Dict[str, bool] = {}
_DOMAIN_BOUNCE_CACHE: Dict[str, int] = {}

TRUSTED_FB_NAME_SOURCES = {
    "facebook_lead_form",
    "facebook_sheet",
    "facebook",
    "facebook_ad",
    "facebook_test",
}


def _conn():
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _pt_now() -> datetime:
    return datetime.now(LA_TZ)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _parse_tag_payload(raw: Any) -> Dict[str, Any]:
    text = (raw or "").strip() if isinstance(raw, str) else ""
    if not text or not text.startswith("{"):
        return {}
    try:
        data = json.loads(text)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _stable_pick(options: List[str], seed: str) -> str:
    if not options:
        return ""
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    idx = int(digest, 16) % len(options)
    return options[idx]


def _ab_variant_for_vendor(vendor_id: Any, email: str, step_number: int) -> str:
    seed = f"{_safe_int(vendor_id, 0)}:{(email or '').strip().lower()}:{int(step_number)}:ab"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    return "A" if (int(digest, 16) % 2 == 0) else "B"


def _subject_for_variant(step: Dict[str, Any], vendor: Dict[str, Any], variant: str) -> str:
    options = [s for s in step.get("subject_options", []) if "script" not in (s or "").lower()]
    if not options:
        options = list(step.get("subject_options", []))
    if not options:
        return ""
    variant_key = (variant or "").strip().upper()
    if variant_key == "A":
        pool = options[::2] or options
    elif variant_key == "B":
        pool = options[1::2] or options
    else:
        pool = options
    seed = f"{_safe_int(vendor.get('id'), 0)}:{vendor.get('email', '')}:{step.get('step', 1)}:{variant_key}:subject"
    return _stable_pick(pool, seed)


def _apply_body_variant(
    plain_body: str,
    step_number: int,
    variant: str,
    sequence_segment: str,
) -> Tuple[str, str]:
    body = (plain_body or "").replace("\r\n", "\n")
    variant_key = (variant or "").strip().upper()
    body_variant = "baseline"
    if variant_key == "B" and int(step_number) == 1:
        body_variant = "direct_value"
        replacement = "If a client books through your referral, we pay $200 per booked event."
        if "quick partner idea: we pay $200 for each booked event referral." in body.lower():
            body = re.sub(
                r"(?i)quick partner idea: we pay \$200 for each booked event referral\.",
                replacement,
                body,
                count=1,
            )
        elif "typically $200" in body.lower():
            body = re.sub(
                r"(?i)typically \$200",
                "$200",
                body,
                count=1,
            )
        elif sequence_segment in {"vendor", "planner", "venue"} and "$200" not in body:
            lines = body.split("\n")
            insert_idx = 1 if lines else 0
            lines[insert_idx:insert_idx] = ["", replacement, ""]
            body = "\n".join(lines)
    return body.strip() + "\n", body_variant


def _step_from_template_type(template_type: str) -> int:
    text = (template_type or "").strip().lower()
    if text.startswith("follow_up_"):
        return max(1, _safe_int(text.split("_")[-1], 2))
    return 1


def _row_step_number(row: Dict[str, Any]) -> int:
    tags = _parse_tag_payload(row.get("tags"))
    step = _safe_int(tags.get("step_number"), 0)
    if step > 0:
        return step
    return _step_from_template_type(str(row.get("template_type") or ""))


def _date_from_row_timestamp(*values: Any) -> Optional[datetime]:
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            continue
        base = text[:10]
        try:
            dt = datetime.strptime(base, "%Y-%m-%d")
            return dt
        except Exception:
            continue
    return None


def _normalize_phone_digits(raw: str) -> str:
    digits = re.sub(r"[^\d]", "", (raw or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def _email_local_part(email: str) -> str:
    text = (email or "").strip().lower()
    if "@" not in text:
        return ""
    return text.split("@", 1)[0].strip()


def _normalize_email_address(raw: str) -> str:
    text = (raw or "").strip().lower()
    if not text:
        return ""
    text = text.replace("mailto:", "").strip()
    text = re.sub(r"^[<\"'\\s]+|[>\"'\\s]+$", "", text)
    match = EMAIL_ADDR_RE.search(text)
    if not match:
        return ""
    candidate = match.group(0).strip().rstrip(">").rstrip(".")
    local = _email_local_part(candidate)
    if not local:
        return ""
    # Reject obvious phone+local-part contamination like:
    # "ca818-896-0001info@domain.com"
    digit_count = sum(ch.isdigit() for ch in local)
    if digit_count >= 7 and re.search(r"(info|sales|contact|office|events?|weddings?|bookings?)$", local):
        return ""
    if len(local) > 48:
        return ""
    return candidate


def _email_domain(email: str) -> str:
    text = (email or "").strip().lower()
    if "@" not in text:
        return ""
    return text.split("@", 1)[1].strip()


def _is_generic_inbox(email: str) -> bool:
    local = _email_local_part(email)
    if not local:
        return True
    token = re.sub(r"[^a-z]", "", local)
    if token in GENERIC_INBOX_LOCAL_PARTS:
        return True
    return any(token.startswith(prefix) for prefix in ("info", "sales", "support", "admin", "contact", "hello"))


def _domain_has_deliverability_dns(domain: str) -> bool:
    dom = (domain or "").strip().lower()
    if not dom:
        return False
    if dom in _MX_CACHE:
        return _MX_CACHE[dom]

    has_dns = False
    try:
        # Prefer MX if available.
        mx = subprocess.run(
            ["dig", "+short", "MX", dom],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if mx.stdout and mx.stdout.strip():
            has_dns = True
    except Exception:
        has_dns = False

    if not has_dns:
        # RFC fallback: if A/AAAA resolves, domain can still accept mail.
        try:
            socket.getaddrinfo(dom, 25)
            has_dns = True
        except Exception:
            has_dns = False

    _MX_CACHE[dom] = bool(has_dns)
    return bool(has_dns)


def _recent_domain_bounces(conn: sqlite3.Connection, domain: str, days: int = 30) -> int:
    dom = (domain or "").strip().lower()
    if not dom:
        return 0
    if dom in _DOMAIN_BOUNCE_CACHE:
        return _DOMAIN_BOUNCE_CACHE[dom]
    row = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM outbound_log
        WHERE channel = 'email'
          AND result = 'bounced'
          AND lower(recipient) LIKE ?
          AND timestamp >= datetime('now', ?)
        """,
        (f"%@{dom}", f"-{max(1, int(days))} day"),
    ).fetchone()
    count = int(row["c"] if row else 0)
    _DOMAIN_BOUNCE_CACHE[dom] = count
    return count


def _email_preflight_reason(conn: sqlite3.Connection, email: str) -> str:
    normalized = _normalize_email_address(email)
    if not normalized:
        return "invalid_email_format"
    domain = _email_domain(normalized)
    if not domain:
        return "invalid_email_domain"
    if domain in BAD_EMAIL_DOMAINS:
        return "known_bad_domain"
    if _recent_domain_bounces(conn, domain, days=30) >= 2:
        return "domain_recent_bounces"
    if not _domain_has_deliverability_dns(domain):
        return "domain_no_mx_or_a_record"
    return ""


def _website_is_valid(raw: str, website_status: Any = None) -> bool:
    text = (raw or "").strip()
    if not text:
        return False
    if text.startswith(("http://", "https://")):
        host = text.split("//", 1)[1]
    else:
        host = text
    host = host.split("/", 1)[0].strip().lower()
    if "." not in host or "example" in host:
        return False
    if website_status is not None and _safe_int(website_status, 1) == 0:
        return False
    return True


def _root_domain(host_or_domain: str) -> str:
    value = (host_or_domain or "").strip().lower()
    if not value:
        return ""
    value = value.split(":", 1)[0].strip(".")
    parts = [part for part in value.split(".") if part]
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return value


def _website_host(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    if not re.match(r"^https?://", text, flags=re.IGNORECASE):
        text = f"https://{text}"
    try:
        return (urlparse(text).hostname or "").strip().lower()
    except Exception:
        return ""


def _email_matches_website_domain(email: str, website: str) -> bool:
    domain = _root_domain(_email_domain(email))
    host = _root_domain(_website_host(website))
    if not domain or not host:
        return True
    return domain == host


def _proof_snippet(vendor: Dict[str, Any]) -> str:
    notes = (vendor.get("notes") or "").strip()
    if notes:
        m = re.search(r"(preferred vendor|vendor list|outside vendors|outdoor events|wedding events)", notes, flags=re.IGNORECASE)
        if m:
            return f"Noted from available details: {m.group(1)}."
    name = (vendor.get("name") or "").strip()
    city = (vendor.get("city") or "").strip()
    if name and city:
        return f"I came across {name} in {city}."
    if name:
        return f"I came across {name}."
    return ""


def _extract_first_name(raw_name: str) -> str:
    name = (raw_name or "").strip()
    if not name:
        return ""
    token = re.sub(r"[^A-Za-z'\-]", "", name.split()[0] or "")
    if len(token) < 2:
        return ""
    if token.lower() in GENERIC_INBOX_LOCAL_PARTS:
        return ""
    return token.capitalize()


def _is_fb_name_source(source: str) -> bool:
    s = (source or "").strip().lower()
    return s in TRUSTED_FB_NAME_SOURCES


def evaluate_vendor_candidate(vendor: Dict[str, Any], category_learning_bonus: int = 0) -> Dict[str, Any]:
    """Evaluate vendor quality and queue eligibility.

    Returns:
      {
        eligible: bool,
        quality_score: int,
        reasons: [..],
        quality_flags: [..],
        contactability_signals: int,
        generic_inbox: bool,
        trusted_first_name: str,
        name_source: str,
        name_confidence: float,
        name_verified: bool,
        proof_snippet: str,
      }
    """
    reasons: List[str] = []
    flags: List[str] = []

    raw_email = (vendor.get("email") or "").strip()
    email = _normalize_email_address(raw_email)
    phone = (vendor.get("phone") or "").strip()
    website = (vendor.get("website") or "").strip()
    category = (vendor.get("category") or "").strip().lower()
    source = (vendor.get("source") or "").strip().lower()
    name = (vendor.get("name") or "").strip().lower()
    campaign_quality = _safe_int(vendor.get("campaign_quality"), 0)
    referral_score = _safe_int(vendor.get("referral_score"), 0)

    email_valid = ("@" in email) and (_safe_int(vendor.get("email_valid"), 1) != 0)
    phone_digits = _normalize_phone_digits(phone)
    phone_valid = (_safe_int(vendor.get("phone_valid"), 0) != 0) or len(phone_digits) >= 10
    website_valid = _website_is_valid(website, vendor.get("website_status"))
    email_domain_match = _email_matches_website_domain(email, website)
    generic_inbox = _is_generic_inbox(email)
    low_intent = any(k in name for k in LOW_INTENT_NAME_KEYWORDS)
    if any(k in category for k in LOW_INTENT_CATEGORY_KEYWORDS):
        low_intent = True
    hard_blocked_category = any(k in category for k in HARD_BLOCK_CATEGORY_KEYWORDS)
    event_fit = any(h in category for h in EVENT_FIT_CATEGORY_HINTS)
    trusted_inbound = source in {"facebook_lead_form", "facebook_lead_ad", "facebook_sheet", "manual", "crm_import"}

    contactability = int(email_valid) + int(phone_valid) + int(website_valid)
    if contactability < 2:
        reasons.append("Failed contactability gate (needs 2/3 of email+phone+website)")
        flags.append("contactability_low")

    if not email_valid:
        reasons.append("Invalid/missing email")
        flags.append("email_invalid")
    if not website_valid:
        reasons.append("Invalid or unreachable website")
        flags.append("website_invalid")
    if generic_inbox:
        reasons.append("Generic inbox (info@/weddings@/sales@)")
        flags.append("generic_inbox")
    if email_valid and website_valid and not email_domain_match:
        reasons.append("Email domain does not match website domain")
        flags.append("email_domain_mismatch")
    if low_intent:
        reasons.append("Low-intent target type")
        flags.append("low_intent")
    if hard_blocked_category:
        reasons.append("Category blocked for morning cold outreach")
        flags.append("category_hard_block")
    if not event_fit and not trusted_inbound:
        reasons.append("Category does not match event/referral target profile")
        flags.append("category_not_event_fit")

    # Baseline quality floor so older rows with missing campaign_quality
    # still get fairly evaluated by live contactability/category signals.
    score = float(max(campaign_quality, 35))
    score += min(referral_score, 100) * 0.12
    score += contactability * 10
    if event_fit:
        score += 10
    else:
        score -= 18

    if generic_inbox:
        score -= 10
    if email_valid and website_valid and not email_domain_match:
        score -= 14
    if not website_valid:
        score -= 18
    if not phone_valid:
        score -= 4
    if low_intent:
        score -= 25
    if hard_blocked_category:
        score -= 35
    if campaign_quality < 25:
        score -= 5

    score += int(category_learning_bonus)
    score = max(0.0, min(100.0, score))

    # Name confidence policy:
    # Personalized greeting allowed only when source is trusted FB + verified/high confidence.
    contact_name = (vendor.get("contact_name") or "").strip()
    first_name = _extract_first_name(contact_name)
    src = (vendor.get("contact_name_source") or source or "").strip().lower()
    conf = float(vendor.get("contact_name_confidence") or 0.0)
    verified = bool(_safe_int(vendor.get("contact_name_verified"), 0))
    if _is_fb_name_source(src) and first_name:
        conf = max(conf, 0.99)
        verified = True
    trusted_first_name = first_name if (_is_fb_name_source(src) and first_name and (verified or conf >= 0.90)) else ""
    if not trusted_first_name:
        src = "none"
        conf = 0.0
        verified = False
        flags.append("name_unverified")

    allow_generic_inbox = (
        generic_inbox
        and any(h in category for h in GENERIC_INBOX_HIGH_TRUST_HINTS)
        and score >= 68
        and contactability >= 2
        and not hard_blocked_category
        and email_domain_match
    )

    eligible = (
        email_valid
        and website_valid
        and contactability >= 2
        and not low_intent
        and not hard_blocked_category
        and (event_fit or trusted_inbound)
        and score >= 50
        and (not generic_inbox or allow_generic_inbox)
    )

    if not eligible and not reasons:
        reasons.append("Did not pass queue quality threshold")

    return {
        "eligible": bool(eligible),
        "quality_score": int(round(score)),
        "reasons": reasons,
        "quality_flags": flags,
        "contactability_signals": int(contactability),
        "generic_inbox": bool(generic_inbox),
        "trusted_first_name": trusted_first_name,
        "name_source": src or "none",
        "name_confidence": round(conf, 2),
        "name_verified": bool(verified and trusted_first_name),
        "proof_snippet": _proof_snippet(vendor),
        "normalized_email": email,
    }


def render_sequence_step_for_vendor(
    vendor: Dict[str, Any],
    sequence_segment: str,
    step_number: int,
    subject_variant: Optional[str] = None,
    sender_name: str = "Kai",
) -> Optional[Dict[str, Any]]:
    """Render one sequence step with deterministic A/B subject/body variants."""
    try:
        from core.email_sequences import get_sequence, get_step, map_category_to_segment, render_email
    except Exception:
        return None

    segment = (sequence_segment or "").strip().lower()
    if not segment:
        segment = map_category_to_segment(vendor.get("category", "other"))

    seq = get_sequence(segment)
    if not seq:
        return None

    step = get_step(segment, int(step_number))
    if not step:
        step = seq[0]
        step_number = int(step.get("step", 1))

    variant = (subject_variant or "").strip().upper()
    if not variant:
        variant = _ab_variant_for_vendor(vendor.get("id"), vendor.get("email", ""), int(step_number))

    rendered = render_email(step, vendor, sender_name=sender_name)
    subject = _subject_for_variant(step, vendor, variant) or rendered.get("subject", "")
    plain_body, body_variant = _apply_body_variant(
        rendered.get("plain_body", ""),
        int(step_number),
        variant,
        segment,
    )
    html_body = rendered.get("html_body", "")

    return {
        "segment": segment,
        "step_number": int(step_number),
        "subject_variant": variant,
        "body_variant": body_variant,
        "subject": subject,
        "plain_body": plain_body,
        "html_body": html_body,
        "name_source": rendered.get("name_source", "none"),
        "name_confidence": rendered.get("name_confidence", 0.0),
        "name_personalized": bool(rendered.get("name_personalized", False)),
    }


def _latest_sent_sequence_state(conn: sqlite3.Connection, vendor_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        """
        SELECT id, tags, template_type, category, sent_at, scheduled_date, created_at
        FROM email_queue
        WHERE vendor_id = ? AND status = 'sent'
        ORDER BY COALESCE(sent_at, created_at) DESC, id DESC
        LIMIT 1
        """,
        (int(vendor_id),),
    ).fetchone()
    if not row:
        # Backward-compat: if older sends were logged only in vendor_outreach,
        # treat that as step 1 history.
        vo = conn.execute(
            """
            SELECT vo.sent_at, vo.created_at, v.category
            FROM vendor_outreach vo
            JOIN vendors v ON v.id = vo.vendor_id
            WHERE vo.vendor_id = ? AND vo.channel = 'email' AND vo.status = 'sent'
            ORDER BY COALESCE(vo.sent_at, vo.created_at) DESC, vo.id DESC
            LIMIT 1
            """,
            (int(vendor_id),),
        ).fetchone()
        if not vo:
            # Final fallback: infer from outbound_log for legacy send paths.
            ol = conn.execute(
                """
                SELECT o.timestamp, v.category
                FROM outbound_log o
                JOIN vendors v ON lower(v.email) = lower(o.recipient)
                WHERE v.id = ? AND o.channel = 'email' AND o.result IN ('sent', 'allowed')
                ORDER BY o.timestamp DESC, o.id DESC
                LIMIT 1
                """,
                (int(vendor_id),),
            ).fetchone()
            if not ol:
                return None
            try:
                from core.email_sequences import map_category_to_segment
            except Exception:
                return None
            sent_dt = _date_from_row_timestamp(ol["timestamp"])
            if not sent_dt:
                return None
            return {
                "step_number": 1,
                "segment": map_category_to_segment(ol["category"] or "other"),
                "sent_date": sent_dt.date(),
            }
        try:
            from core.email_sequences import map_category_to_segment
        except Exception:
            return None
        sent_dt = _date_from_row_timestamp(vo["sent_at"], vo["created_at"])
        if not sent_dt:
            return None
        return {
            "step_number": 1,
            "segment": map_category_to_segment(vo["category"] or "other"),
            "sent_date": sent_dt.date(),
        }

    try:
        from core.email_sequences import map_category_to_segment
    except Exception:
        return None

    data = dict(row)
    tags = _parse_tag_payload(data.get("tags"))
    step_number = _safe_int(tags.get("step_number"), 0)
    if step_number <= 0:
        step_number = _step_from_template_type(str(data.get("template_type") or ""))

    segment = (tags.get("sequence_segment") or "").strip().lower()
    if not segment:
        segment = map_category_to_segment(data.get("category", "other"))

    sent_dt = _date_from_row_timestamp(data.get("sent_at"), data.get("scheduled_date"), data.get("created_at"))
    if not sent_dt:
        return None

    return {
        "step_number": max(1, int(step_number)),
        "segment": segment,
        "sent_date": sent_dt.date(),
    }


def _next_due_followup_step(
    conn: sqlite3.Connection,
    vendor_id: int,
    target_date: str,
) -> Optional[Dict[str, Any]]:
    state = _latest_sent_sequence_state(conn, vendor_id)
    if not state:
        return None
    try:
        from core.email_sequences import get_sequence
    except Exception:
        return None

    segment = state.get("segment", "vendor")
    seq = get_sequence(segment)
    if not seq:
        return None

    current_step = int(state.get("step_number", 1))
    if current_step >= len(seq):
        return None

    next_step = current_step + 1
    current_idx = max(0, min(len(seq) - 1, current_step - 1))
    next_idx = max(0, min(len(seq) - 1, next_step - 1))
    current_offset = _safe_int(seq[current_idx].get("day_offset"), 0)
    next_offset = _safe_int(seq[next_idx].get("day_offset"), current_offset + 3)
    delay_days = max(1, next_offset - current_offset)

    try:
        target = datetime.strptime(target_date, "%Y-%m-%d").date()
    except Exception:
        return None
    due_on = state["sent_date"] + timedelta(days=delay_days)
    if target < due_on:
        return None
    return {
        "segment": segment,
        "step_number": next_step,
        "due_on": due_on.isoformat(),
        "delay_days": delay_days,
    }


def _ensure_vendor_identity_columns(conn: sqlite3.Connection) -> None:
    """Ensure optional identity quality columns exist on vendors."""
    cols = {
        r["name"] if isinstance(r, sqlite3.Row) else r[1]
        for r in conn.execute("PRAGMA table_info(vendors)").fetchall()
    }
    alters = []
    if "contact_name_source" not in cols:
        alters.append("ALTER TABLE vendors ADD COLUMN contact_name_source TEXT DEFAULT ''")
    if "contact_name_confidence" not in cols:
        alters.append("ALTER TABLE vendors ADD COLUMN contact_name_confidence REAL DEFAULT 0")
    if "contact_name_verified" not in cols:
        alters.append("ALTER TABLE vendors ADD COLUMN contact_name_verified INTEGER DEFAULT 0")
    for sql in alters:
        try:
            conn.execute(sql)
        except Exception:
            pass
    if alters:
        conn.commit()


def _category_learning_bonus(conn: sqlite3.Connection) -> Dict[str, int]:
    """Closed-loop learning: derive a small bonus/penalty from reply rates by category."""
    rows = conn.execute(
        """
        SELECT lower(COALESCE(v.category, 'other')) AS category,
               SUM(CASE WHEN vo.status = 'sent' THEN 1 ELSE 0 END) AS sent_count,
               SUM(CASE WHEN vo.status = 'replied' THEN 1 ELSE 0 END) AS reply_count
        FROM vendor_outreach vo
        JOIN vendors v ON v.id = vo.vendor_id
        GROUP BY lower(COALESCE(v.category, 'other'))
        """
    ).fetchall()
    out: Dict[str, int] = {}
    for r in rows:
        sent = _safe_int(r["sent_count"], 0)
        replied = _safe_int(r["reply_count"], 0)
        if sent < 5:
            out[r["category"]] = 0
            continue
        rate = replied / max(1, sent)
        # Small bounded learning adjustment to avoid unstable swings.
        bonus = int(round((rate - 0.08) * 40))
        out[r["category"]] = max(-6, min(8, bonus))
    return out


def _vendor_terminal_for_followup(conn: sqlite3.Connection, vendor_id: int, recipient_email: str) -> bool:
    """True when this contact should not receive further follow-up touches."""
    email = (recipient_email or "").strip().lower()
    status_row = conn.execute(
        "SELECT lower(COALESCE(outreach_status, '')) AS s FROM vendors WHERE id = ?",
        (int(vendor_id),),
    ).fetchone()
    vendor_status = (status_row["s"] if status_row else "")
    if vendor_status in {
        "replied", "bounced", "unsubscribed", "do_not_contact",
        "booked", "converted", "closed",
    }:
        return True
    if email:
        if conn.execute("SELECT 1 FROM email_unsubscribes WHERE lower(email) = ? LIMIT 1", (email,)).fetchone():
            return True
    return bool(conn.execute(
        """
        SELECT 1
        FROM vendor_outreach
        WHERE vendor_id = ?
          AND status IN ('replied', 'bounced', 'unsubscribed', 'stop')
        LIMIT 1
        """,
        (int(vendor_id),),
    ).fetchone())


def generate_queue(
    scheduled_date: Optional[str] = None,
    count: int = 30,
) -> Dict[str, Any]:
    """Generate tomorrow's email batch from unsent vendors.

    Pulls vendors that haven't been emailed, generates category-specific
    drafts using email_sequences, inserts into email_queue.
    """
    if not scheduled_date:
        scheduled_date = (_pt_now() + timedelta(days=1)).strftime("%Y-%m-%d")

    conn = _conn()
    today_pt = _pt_now().strftime("%Y-%m-%d")

    _ensure_vendor_identity_columns(conn)

    # Roll over stale unsent rows from past dates so they do not block future
    # queue generation forever.
    stale_removed = conn.execute(
        """
        UPDATE email_queue
        SET status = 'removed',
            send_result = CASE
                WHEN send_result IS NULL OR trim(send_result) = '' THEN 'stale_batch_rollover'
                ELSE send_result || ' | stale_batch_rollover'
            END
        WHERE status IN ('queued', 'edited', 'approved')
          AND COALESCE(scheduled_date, '') != ''
          AND scheduled_date < ?
        """,
        (today_pt,),
    ).rowcount
    if stale_removed:
        conn.commit()

    # Existing active rows for target date remain untouched; top up toward `count`.
    existing_rows = conn.execute(
        """
        SELECT recipient_email
        FROM email_queue
        WHERE scheduled_date = ?
          AND status IN ('queued', 'edited', 'approved')
        """,
        (scheduled_date,),
    ).fetchall()
    existing_active = len(existing_rows)
    seen_recipients = {
        (r["recipient_email"] or "").strip().lower()
        for r in existing_rows
        if (r["recipient_email"] or "").strip()
    }
    slots_remaining = max(0, int(count) - existing_active)
    if slots_remaining == 0:
        conn.close()
        return {
            "status": "already_exists",
            "scheduled_date": scheduled_date,
            "count": existing_active,
            "generated": 0,
            "initial_generated": 0,
            "follow_up_generated": 0,
            "stale_archived": stale_removed,
        }

    learning_bonus = _category_learning_bonus(conn)
    rejected_reasons: Dict[str, int] = {}
    generated_initial = 0
    generated_followup = 0
    next_batch = conn.execute(
        "SELECT COALESCE(MAX(batch_number), 0) + 1 AS n FROM email_queue WHERE scheduled_date = ?",
        (scheduled_date,),
    ).fetchone()["n"]

    def _enqueue(
        vendor_row: Dict[str, Any],
        decision: Dict[str, Any],
        rendered: Dict[str, Any],
        step_number: int,
        sequence_segment: str,
        template_type: str,
    ) -> None:
        nonlocal next_batch
        recipient_email = (decision.get("normalized_email") or vendor_row.get("email") or "").strip().lower()
        priority_score = int(max(0, booking_priority_score(vendor_row, decision)))
        tag_payload = {
            "category": vendor_row.get("category", "other"),
            "quality_score": priority_score,
            "quality_flags": decision.get("quality_flags", []),
            "contactability_signals": decision.get("contactability_signals", 0),
            "generic_inbox": decision.get("generic_inbox", False),
            "name_source": rendered.get("name_source", "none"),
            "name_confidence": rendered.get("name_confidence", 0.0),
            "name_personalized": rendered.get("name_personalized", False),
            "sequence_segment": sequence_segment,
            "step_number": int(step_number),
            "ab_subject_variant": rendered.get("subject_variant", ""),
            "ab_body_variant": rendered.get("body_variant", "baseline"),
        }
        conn.execute(
            """
            INSERT INTO email_queue
               (vendor_id, recipient_email, recipient_name, category,
                subject, body_plain, body_html, template_type,
                status, scheduled_date, batch_number, priority_score, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
            """,
            (
                int(vendor_row["id"]),
                recipient_email,
                vendor_row.get("name", ""),
                vendor_row.get("category", ""),
                rendered.get("subject", ""),
                rendered.get("plain_body", ""),
                rendered.get("html_body", ""),
                template_type,
                scheduled_date,
                int(next_batch),
                priority_score,
                json.dumps(tag_payload),
            ),
        )
        next_batch += 1

    # Stage due follow-up steps first.
    followup_limit = max(200, int(slots_remaining) * 30)
    followup_candidates = conn.execute(
        """
        SELECT DISTINCT v.id, v.name, v.email, v.category, v.city, v.phone,
               v.outreach_status, v.campaign_eligible, v.email_valid,
               v.campaign_quality, v.referral_score, v.phone_valid,
               v.website_status, v.website, v.notes, v.source,
               v.contact_name, v.contact_name_source, v.contact_name_confidence,
               v.contact_name_verified
        FROM vendors v
        LEFT JOIN email_queue eq
               ON eq.vendor_id = v.id
              AND eq.status IN ('queued', 'approved', 'edited')
              AND COALESCE(eq.scheduled_date, '') >= ?
        LEFT JOIN contact_blocklist cb ON (cb.email = v.email AND cb.active = 1)
        LEFT JOIN email_unsubscribes eu ON eu.email = v.email
        WHERE eq.id IS NULL
          AND cb.id IS NULL
          AND eu.id IS NULL
          AND v.email != '' AND v.email IS NOT NULL
          AND v.email_valid != 0
          AND (
              EXISTS (
                  SELECT 1 FROM email_queue sq
                  WHERE sq.vendor_id = v.id
                    AND sq.status = 'sent'
              )
              OR EXISTS (
                  SELECT 1 FROM vendor_outreach vo
                  WHERE vo.vendor_id = v.id
                    AND vo.channel = 'email'
                    AND vo.status = 'sent'
              )
              OR EXISTS (
                  SELECT 1 FROM outbound_log ol
                  WHERE ol.channel = 'email'
                    AND ol.result IN ('sent', 'allowed')
                    AND lower(ol.recipient) = lower(v.email)
              )
          )
        ORDER BY v.campaign_quality DESC, v.referral_score DESC, v.id ASC
        LIMIT ?
        """,
        (today_pt, followup_limit),
    ).fetchall()

    staged_followups = []
    staged_followup_recipients = set()
    for raw in followup_candidates:
        vendor = dict(raw)
        decision = evaluate_vendor_candidate(vendor, category_learning_bonus=learning_bonus.get((vendor.get("category") or "other").strip().lower(), 0))
        recipient_email = (decision.get("normalized_email") or "").strip().lower()
        if not recipient_email:
            rejected_reasons["Invalid/missing normalized email"] = rejected_reasons.get("Invalid/missing normalized email", 0) + 1
            continue
        preflight_reason = _email_preflight_reason(conn, recipient_email)
        if preflight_reason:
            rejected_reasons[f"Email preflight: {preflight_reason}"] = (
                rejected_reasons.get(f"Email preflight: {preflight_reason}", 0) + 1
            )
            if preflight_reason in {"invalid_email_format", "invalid_email_domain", "known_bad_domain", "domain_no_mx_or_a_record"}:
                try:
                    conn.execute(
                        "UPDATE vendors SET email_valid = 0, campaign_eligible = 0, updated_at = datetime('now') WHERE id = ?",
                        (int(vendor["id"]),),
                    )
                except Exception:
                    pass
            continue
        skip_reason = should_skip_fast_booking_email_candidate(vendor, decision)
        if skip_reason:
            rejected_reasons[skip_reason] = rejected_reasons.get(skip_reason, 0) + 1
            continue
        if recipient_email in seen_recipients or recipient_email in staged_followup_recipients:
            continue
        if _vendor_terminal_for_followup(conn, int(vendor["id"]), recipient_email):
            continue
        due = _next_due_followup_step(conn, int(vendor["id"]), scheduled_date)
        if not due:
            continue
        if recipient_email != (vendor.get("email") or "").strip().lower():
            try:
                conn.execute("UPDATE vendors SET email = ? WHERE id = ?", (recipient_email, int(vendor["id"])))
            except Exception:
                pass

        v_render = dict(vendor)
        v_render["email"] = recipient_email
        v_render["trusted_first_name"] = decision.get("trusted_first_name", "")
        v_render["contact_name_source"] = decision.get("name_source", "none")
        v_render["contact_name_confidence"] = decision.get("name_confidence", 0.0)
        v_render["contact_name_verified"] = 1 if decision.get("name_verified") else 0
        v_render["proof_snippet"] = decision.get("proof_snippet", "")
        staged_followups.append(
            (
                booking_priority_sort_key(v_render, decision),
                v_render,
                decision,
                due,
            )
        )
        staged_followup_recipients.add(recipient_email)

    for _, v_render, decision, due in sorted(staged_followups, key=lambda item: item[0]):
        if slots_remaining <= 0:
            break
        rendered = render_sequence_step_for_vendor(
            v_render,
            sequence_segment=str(due.get("segment") or "vendor"),
            step_number=int(due.get("step_number") or 2),
        )
        if not rendered:
            continue
        step_number = int(rendered.get("step_number", 2))
        _enqueue(
            vendor_row=v_render,
            decision=decision,
            rendered=rendered,
            step_number=step_number,
            sequence_segment=rendered.get("segment", "vendor"),
            template_type=f"follow_up_{step_number}",
        )
        seen_recipients.add((decision.get("normalized_email") or v_render.get("email") or "").strip().lower())
        generated_followup += 1
        slots_remaining -= 1

    # Fill remaining slots with initial outreach.
    if slots_remaining > 0:
        candidate_limit = max(200, int(slots_remaining) * 12)
        vendors = conn.execute(
            """
            SELECT v.id, v.name, v.email, v.category, v.city, v.phone,
                   v.outreach_status, v.campaign_eligible, v.email_valid,
                   v.campaign_quality, v.referral_score, v.phone_valid,
                   v.website_status, v.website, v.notes, v.source,
                   v.contact_name, v.contact_name_source, v.contact_name_confidence,
                   v.contact_name_verified
            FROM vendors v
            LEFT JOIN email_queue eq
                   ON eq.vendor_id = v.id
                  AND eq.status IN ('queued', 'approved', 'edited')
                  AND COALESCE(eq.scheduled_date, '') >= ?
            LEFT JOIN contact_blocklist cb ON (cb.email = v.email AND cb.active = 1)
            LEFT JOIN email_unsubscribes eu ON eu.email = v.email
            WHERE v.email != '' AND v.email IS NOT NULL
              AND eq.id IS NULL
              AND cb.id IS NULL
              AND eu.id IS NULL
              AND v.email_valid != 0
              AND COALESCE(v.outreach_status, 'none') IN ('none', 'new', '')
            ORDER BY v.campaign_quality DESC, v.referral_score DESC, v.id ASC
            LIMIT ?
            """,
            (today_pt, candidate_limit),
        ).fetchall()

        if not vendors and generated_followup == 0 and existing_active == 0:
            conn.close()
            return {
                "status": "no_vendors",
                "scheduled_date": scheduled_date,
                "generated": 0,
                "initial_generated": 0,
                "follow_up_generated": 0,
                "stale_archived": stale_removed,
            }

        staged_initial = []
        staged_initial_recipients = set()
        for raw in vendors:
            vendor = dict(raw)
            category_key = (vendor.get("category") or "other").strip().lower()
            decision = evaluate_vendor_candidate(
                vendor,
                category_learning_bonus=learning_bonus.get(category_key, 0),
            )
            if not decision.get("eligible"):
                for reason in decision.get("reasons", []) or ["Filtered by quality gate"]:
                    rejected_reasons[reason] = rejected_reasons.get(reason, 0) + 1
                continue

            recipient_email = (decision.get("normalized_email") or "").strip().lower()
            if not recipient_email:
                rejected_reasons["Invalid/missing normalized email"] = (
                    rejected_reasons.get("Invalid/missing normalized email", 0) + 1
                )
                continue
            preflight_reason = _email_preflight_reason(conn, recipient_email)
            if preflight_reason:
                rejected_reasons[f"Email preflight: {preflight_reason}"] = (
                    rejected_reasons.get(f"Email preflight: {preflight_reason}", 0) + 1
                )
                if preflight_reason in {"invalid_email_format", "invalid_email_domain", "known_bad_domain", "domain_no_mx_or_a_record"}:
                    try:
                        conn.execute(
                            "UPDATE vendors SET email_valid = 0, campaign_eligible = 0, updated_at = datetime('now') WHERE id = ?",
                            (int(vendor["id"]),),
                        )
                    except Exception:
                        pass
                continue
            skip_reason = should_skip_fast_booking_email_candidate(vendor, decision)
            if skip_reason:
                rejected_reasons[skip_reason] = rejected_reasons.get(skip_reason, 0) + 1
                continue
            if recipient_email in seen_recipients or recipient_email in staged_initial_recipients:
                rejected_reasons["Duplicate recipient in batch"] = (
                    rejected_reasons.get("Duplicate recipient in batch", 0) + 1
                )
                continue
            if recipient_email != (vendor.get("email") or "").strip().lower():
                try:
                    conn.execute("UPDATE vendors SET email = ? WHERE id = ?", (recipient_email, int(vendor["id"])))
                except Exception:
                    pass

            v_render = dict(vendor)
            v_render["email"] = recipient_email
            v_render["trusted_first_name"] = decision.get("trusted_first_name", "")
            v_render["contact_name_source"] = decision.get("name_source", "none")
            v_render["contact_name_confidence"] = decision.get("name_confidence", 0.0)
            v_render["contact_name_verified"] = 1 if decision.get("name_verified") else 0
            v_render["proof_snippet"] = decision.get("proof_snippet", "")
            staged_initial.append(
                (
                    booking_priority_sort_key(v_render, decision),
                    v_render,
                    decision,
                )
            )
            staged_initial_recipients.add(recipient_email)

        for _, v_render, decision in sorted(staged_initial, key=lambda item: item[0]):
            if slots_remaining <= 0:
                break
            rendered = render_sequence_step_for_vendor(
                v_render,
                sequence_segment="",
                step_number=1,
            )
            if not rendered:
                continue
            _enqueue(
                vendor_row=v_render,
                decision=decision,
                rendered=rendered,
                step_number=1,
                sequence_segment=rendered.get("segment", "vendor"),
                template_type="initial_outreach",
            )
            seen_recipients.add((decision.get("normalized_email") or v_render.get("email") or "").strip().lower())
            generated_initial += 1
            slots_remaining -= 1

    conn.commit()
    conn.close()

    generated = int(generated_initial + generated_followup)
    log.info(
        "Generated %d queue rows for %s (initial=%d follow_up=%d existing=%d)",
        generated, scheduled_date, generated_initial, generated_followup, existing_active,
    )
    if generated == 0:
        status = "already_exists" if existing_active > 0 else "no_qualified_vendors"
        return {
            "status": status,
            "scheduled_date": scheduled_date,
            "generated": 0,
            "initial_generated": 0,
            "follow_up_generated": 0,
            "existing_active_count": existing_active,
            "rejected_reasons": rejected_reasons,
            "stale_archived": stale_removed,
        }
    return {
        "status": "ok",
        "scheduled_date": scheduled_date,
        "generated": generated,
        "initial_generated": generated_initial,
        "follow_up_generated": generated_followup,
        "existing_active_count": existing_active,
        "rejected_reasons": rejected_reasons,
        "stale_archived": stale_removed,
    }


def get_vendor_queue_email(
    vendor_id: int,
    scheduled_date: Optional[str] = None,
    active_only: bool = True,
) -> Optional[Dict[str, Any]]:
    """Get the latest queued email for a vendor/date."""
    conn = _conn()
    where = ["vendor_id = ?"]
    params: List[Any] = [vendor_id]

    if scheduled_date:
        where.append("scheduled_date = ?")
        params.append(scheduled_date)
    if active_only:
        where.append("status IN ('queued', 'edited', 'approved')")

    row = conn.execute(
        f"""SELECT * FROM email_queue
            WHERE {' AND '.join(where)}
            ORDER BY id DESC
            LIMIT 1""",
        params,
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def stage_email(
    vendor_id: Optional[int],
    recipient_email: str,
    recipient_name: str,
    category: str,
    subject: str,
    body_plain: str,
    body_html: str = "",
    template_type: str = "initial_outreach",
    scheduled_date: Optional[str] = None,
    priority_score: int = 0,
    tags: str = "",
) -> Dict[str, Any]:
    """Create a queued email row for a target date (default tomorrow PT)."""
    if not scheduled_date:
        scheduled_date = (_pt_now() + timedelta(days=1)).strftime("%Y-%m-%d")

    conn = _conn()
    next_batch = conn.execute(
        "SELECT COALESCE(MAX(batch_number), 0) + 1 as n FROM email_queue WHERE scheduled_date = ?",
        (scheduled_date,),
    ).fetchone()["n"]

    cur = conn.execute(
        """INSERT INTO email_queue
           (vendor_id, recipient_email, recipient_name, category,
            subject, body_plain, body_html, template_type,
            status, scheduled_date, batch_number, priority_score, tags)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)""",
        (
            vendor_id,
            recipient_email,
            recipient_name,
            category,
            subject,
            body_plain,
            body_html,
            template_type,
            scheduled_date,
            next_batch,
            priority_score,
            tags,
        ),
    )
    qid = cur.lastrowid
    conn.commit()
    row = conn.execute("SELECT * FROM email_queue WHERE id = ?", (qid,)).fetchone()
    conn.close()
    return dict(row)


def remove_vendor_from_queue(vendor_id: int, scheduled_date: Optional[str] = None) -> int:
    """Soft-remove active queue entries for a vendor (optionally scoped to date)."""
    conn = _conn()
    where = ["vendor_id = ?", "status IN ('queued', 'edited', 'approved')"]
    params: List[Any] = [vendor_id]
    if scheduled_date:
        where.append("scheduled_date = ?")
        params.append(scheduled_date)
    cur = conn.execute(
        f"UPDATE email_queue SET status = 'removed' WHERE {' AND '.join(where)}",
        params,
    )
    removed = cur.rowcount
    conn.commit()
    conn.close()
    return removed


def get_queue(
    scheduled_date: Optional[str] = None,
    status: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    """Get emails in the queue with optional filters."""
    conn = _conn()
    where, params = [], []

    if scheduled_date:
        where.append("scheduled_date = ?")
        params.append(scheduled_date)
    if status:
        where.append("status = ?")
        params.append(status)
    if category:
        where.append("category = ?")
        params.append(category)

    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) as cnt FROM email_queue {clause}", params).fetchone()["cnt"]
    rows = conn.execute(
        f"SELECT * FROM email_queue {clause} ORDER BY priority_score DESC, id ASC LIMIT ? OFFSET ?",
        params + [limit, offset]
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows], total


def prune_queue_quality(scheduled_date: str) -> Dict[str, int]:
    """Remove queued rows that now fail quality/preflight checks."""
    conn = _conn()
    try:
        from core.vendor_db import get_vendor
    except Exception:
        conn.close()
        return {"scanned": 0, "removed": 0, "kept": 0}

    rows = conn.execute(
        """
        SELECT id, vendor_id, recipient_email, status
        FROM email_queue
        WHERE scheduled_date = ?
          AND status IN ('queued', 'edited', 'approved')
        ORDER BY id ASC
        """,
        (scheduled_date,),
    ).fetchall()

    scanned = 0
    removed = 0
    kept = 0

    for raw in rows:
        scanned += 1
        row = dict(raw)
        queue_id = int(row["id"])
        vendor_id = _safe_int(row.get("vendor_id"), 0)
        recipient = _normalize_email_address(row.get("recipient_email") or "")
        preflight_reason = _email_preflight_reason(conn, recipient)

        decision = {"eligible": True, "reasons": []}
        if vendor_id > 0:
            vendor = get_vendor(vendor_id) or {}
            if vendor:
                decision = evaluate_vendor_candidate(vendor)

        should_remove = False
        reason_parts: List[str] = []
        if not recipient:
            should_remove = True
            reason_parts.append("invalid_email_format")
        if preflight_reason in {
            "invalid_email_format",
            "invalid_email_domain",
            "known_bad_domain",
            "domain_no_mx_or_a_record",
            "domain_recent_bounces",
        }:
            should_remove = True
            reason_parts.append(f"preflight:{preflight_reason}")
        if not decision.get("eligible", True):
            flags = set(decision.get("quality_flags", []) or [])
            hard_fail = bool(flags.intersection({"category_hard_block", "website_invalid", "email_invalid"}))
            if not hard_fail:
                hard_fail = any(
                    x in (decision.get("reasons", []) or [])
                    for x in (
                        "Category blocked for morning cold outreach",
                        "Invalid or unreachable website",
                        "Invalid/missing email",
                    )
                )
            if not hard_fail:
                continue
            should_remove = True
            reasons = decision.get("reasons", []) or []
            reason_parts.append("quality_gate")
            if reasons:
                reason_parts.append(";".join(str(x) for x in reasons[:2]))

        if should_remove:
            send_result = "queue_prune:" + "|".join(reason_parts[:3])
            conn.execute(
                "UPDATE email_queue SET status = 'removed', send_result = ? WHERE id = ?",
                (send_result[:500], queue_id),
            )
            removed += 1
        else:
            kept += 1

    conn.commit()
    conn.close()
    return {"scanned": scanned, "removed": removed, "kept": kept}


def backfill_vendor_outreach_from_outbound(days_back: int = 14, max_rows: int = 5000) -> Dict[str, int]:
    """Backfill vendor_outreach sent rows from outbound_log.

    This is useful when legacy send paths wrote to outbound_log but not
    vendor_outreach, which blocks sequence follow-up staging.
    """
    conn = _conn()
    rows = conn.execute(
        """
        SELECT o.id, o.recipient, o.timestamp, v.id AS vendor_id
        FROM outbound_log o
        JOIN vendors v ON lower(v.email) = lower(o.recipient)
        WHERE o.channel = 'email'
          AND o.result IN ('sent', 'allowed')
          AND o.timestamp >= datetime('now', ?)
        ORDER BY o.timestamp DESC, o.id DESC
        LIMIT ?
        """,
        (f"-{max(1, int(days_back))} day", int(max_rows)),
    ).fetchall()

    inserted = 0
    skipped = 0
    for row in rows:
        vendor_id = _safe_int(row["vendor_id"], 0)
        if vendor_id <= 0:
            skipped += 1
            continue
        day_key = str(row["timestamp"] or "")[:10]
        exists = conn.execute(
            """
            SELECT 1 FROM vendor_outreach
            WHERE vendor_id = ?
              AND channel = 'email'
              AND status = 'sent'
              AND substr(COALESCE(sent_at, created_at), 1, 10) = ?
            LIMIT 1
            """,
            (vendor_id, day_key),
        ).fetchone()
        if exists:
            skipped += 1
            continue
        conn.execute(
            """
            INSERT INTO vendor_outreach (vendor_id, channel, message_draft, status, sent_at, response, created_at)
            VALUES (?, 'email', '', 'sent', ?, ?, datetime('now'))
            """,
            (
                vendor_id,
                row["timestamp"],
                json.dumps({"detail": "backfill_outbound_log", "outbound_log_id": row["id"]}),
            ),
        )
        inserted += 1

    conn.commit()
    conn.close()
    return {"inserted": inserted, "skipped": skipped, "scanned": len(rows)}


def normalize_unsent_queue_copy(scheduled_date: str) -> Dict[str, int]:
    """Normalize queued/approved unsent copy with latest safety rules.

    - Applies only to non-manually-edited rows (edited_by empty)
    - Preserves status/schedule
    - Ensures greeting/name rules and proof-line logic stay current
    """
    try:
        from core.email_sequences import map_category_to_segment
        from core.vendor_db import get_vendor
    except Exception:
        return {"updated": 0, "skipped": 0}

    conn = _conn()
    rows = conn.execute(
        """
        SELECT id, vendor_id, category, status, edited_by, template_type, tags
        FROM email_queue
        WHERE scheduled_date = ?
          AND status IN ('queued', 'approved')
        ORDER BY id ASC
        """,
        (scheduled_date,),
    ).fetchall()

    updated = 0
    skipped = 0
    for raw_row in rows:
        row = dict(raw_row)
        qid = int(row["id"])
        edited_by = (row["edited_by"] or "").strip().lower()
        if edited_by and edited_by not in {"presend_gate_autofix", "system_autofix", "autofix"}:
            skipped += 1
            continue
        vid = _safe_int(row["vendor_id"], 0)
        if vid <= 0:
            skipped += 1
            continue
        vendor = get_vendor(vid)
        if not vendor:
            skipped += 1
            continue

        decision = evaluate_vendor_candidate(vendor or {})
        v_render = dict(vendor)
        v_render["trusted_first_name"] = decision.get("trusted_first_name", "")
        v_render["contact_name_source"] = decision.get("name_source", "none")
        v_render["contact_name_confidence"] = decision.get("name_confidence", 0.0)
        v_render["contact_name_verified"] = 1 if decision.get("name_verified") else 0
        v_render["proof_snippet"] = decision.get("proof_snippet", "")

        raw_tags = _parse_tag_payload(row.get("tags"))
        segment = (raw_tags.get("sequence_segment") or "").strip().lower()
        if not segment:
            segment = map_category_to_segment(v_render.get("category", row["category"] or "other"))
        step_number = _safe_int(raw_tags.get("step_number"), 0)
        if step_number <= 0:
            step_number = _step_from_template_type(str(row.get("template_type") or ""))
        if step_number <= 0:
            step_number = 1

        subject_variant = (raw_tags.get("ab_subject_variant") or "").strip().upper()
        rendered = render_sequence_step_for_vendor(
            v_render,
            sequence_segment=segment,
            step_number=step_number,
            subject_variant=subject_variant,
        )
        if not rendered:
            skipped += 1
            continue
        step_number = int(rendered.get("step_number", step_number))
        template_type = "initial_outreach" if step_number <= 1 else f"follow_up_{step_number}"
        tag_payload = {
            "category": v_render.get("category", "other"),
            "quality_score": decision.get("quality_score", 0),
            "quality_flags": decision.get("quality_flags", []),
            "contactability_signals": decision.get("contactability_signals", 0),
            "generic_inbox": decision.get("generic_inbox", False),
            "name_source": rendered.get("name_source", "none"),
            "name_confidence": rendered.get("name_confidence", 0.0),
            "name_personalized": rendered.get("name_personalized", False),
            "sequence_segment": rendered.get("segment", segment),
            "step_number": step_number,
            "ab_subject_variant": rendered.get("subject_variant", subject_variant or _ab_variant_for_vendor(vid, v_render.get("email", ""), step_number)),
            "ab_body_variant": rendered.get("body_variant", raw_tags.get("ab_body_variant", "baseline")),
        }
        conn.execute(
            """
            UPDATE email_queue
            SET subject = ?, body_plain = ?, body_html = ?, template_type = ?, tags = ?
            WHERE id = ?
            """,
            (
                rendered.get("subject", ""),
                rendered.get("plain_body", ""),
                rendered.get("html_body", ""),
                template_type,
                json.dumps(tag_payload),
                qid,
            ),
        )
        updated += 1

    conn.commit()
    conn.close()
    return {"updated": updated, "skipped": skipped}


def get_queue_email(queue_id: int) -> Optional[Dict[str, Any]]:
    """Get a single email from the queue."""
    conn = _conn()
    row = conn.execute("SELECT * FROM email_queue WHERE id = ?", (queue_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_queue_stats(scheduled_date: Optional[str] = None) -> Dict[str, Any]:
    """Get summary statistics for a batch."""
    conn = _conn()
    where = "WHERE scheduled_date = ?" if scheduled_date else ""
    params = [scheduled_date] if scheduled_date else []

    total = conn.execute(f"SELECT COUNT(*) as cnt FROM email_queue {where}", params).fetchone()["cnt"]

    status_rows = conn.execute(
        f"SELECT status, COUNT(*) as cnt FROM email_queue {where} GROUP BY status", params
    ).fetchall()
    by_status = {r["status"]: r["cnt"] for r in status_rows}

    cat_rows = conn.execute(
        f"SELECT category, COUNT(*) as cnt FROM email_queue {where} GROUP BY category", params
    ).fetchall()
    by_category = {r["category"]: r["cnt"] for r in cat_rows}

    conn.close()
    return {
        "total": total,
        "scheduled_date": scheduled_date,
        "by_status": by_status,
        "by_category": by_category,
    }


def edit_email(
    queue_id: int,
    subject: Optional[str] = None,
    body_plain: Optional[str] = None,
    body_html: Optional[str] = None,
    editor: str = "kai",
) -> Optional[Dict[str, Any]]:
    """Edit an email in the queue. Preserves original body."""
    conn = _conn()
    row = conn.execute("SELECT * FROM email_queue WHERE id = ?", (queue_id,)).fetchone()
    if not row:
        conn.close()
        return None

    # Save original if not already saved
    if not row["original_body"]:
        conn.execute(
            "UPDATE email_queue SET original_body = ? WHERE id = ?",
            (row["body_plain"], queue_id)
        )

    updates, vals = ["edited_by = ?", "edited_at = datetime('now')", "status = 'edited'"], [editor]

    if subject is not None:
        updates.append("subject = ?")
        vals.append(subject)
    if body_plain is not None:
        updates.append("body_plain = ?")
        vals.append(body_plain)
    if body_html is not None:
        updates.append("body_html = ?")
        vals.append(body_html)

    vals.append(queue_id)
    conn.execute(f"UPDATE email_queue SET {', '.join(updates)} WHERE id = ?", vals)
    conn.commit()

    result = conn.execute("SELECT * FROM email_queue WHERE id = ?", (queue_id,)).fetchone()
    conn.close()
    return dict(result)


def remove_email(queue_id: int, reason: str = "") -> Dict[str, Any]:
    """Soft-remove an email from the batch."""
    conn = _conn()
    conn.execute(
        "UPDATE email_queue SET status = 'removed' WHERE id = ? AND status NOT IN ('sent', 'failed')",
        (queue_id,)
    )
    conn.commit()
    conn.close()
    return {"removed": True, "id": queue_id, "can_undo": True}


def restore_email(queue_id: int) -> Dict[str, Any]:
    """Undo a removal — set status back to queued."""
    conn = _conn()
    conn.execute(
        "UPDATE email_queue SET status = 'queued' WHERE id = ? AND status = 'removed'",
        (queue_id,)
    )
    conn.commit()
    conn.close()
    return {"restored": True, "id": queue_id}


def approve_email(queue_id: int, approved_by: str = "kai") -> Optional[Dict[str, Any]]:
    """Approve a single email for sending."""
    conn = _conn()
    conn.execute(
        "UPDATE email_queue SET status = 'approved' WHERE id = ? AND status IN ('queued', 'edited')",
        (queue_id,)
    )
    conn.commit()
    row = conn.execute("SELECT * FROM email_queue WHERE id = ?", (queue_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def approve_all(scheduled_date: Optional[str] = None) -> int:
    """Bulk approve all queued/edited emails for a date."""
    conn = _conn()
    where = "AND scheduled_date = ?" if scheduled_date else ""
    params = [scheduled_date] if scheduled_date else []

    cursor = conn.execute(
        f"UPDATE email_queue SET status = 'approved' WHERE status IN ('queued', 'edited') {where}",
        params
    )
    count = cursor.rowcount
    conn.commit()
    conn.close()
    return count


def send_approved(
    scheduled_date: Optional[str] = None,
    max_count: int = 30,
    approved_only: bool = True,
) -> Dict[str, Any]:
    """Send all approved emails. MUST check blocklist via outbound_gate().

    Uses SMTP via integrations/messaging.py patterns.
    """
    import time

    if not scheduled_date:
        scheduled_date = _pt_now().strftime("%Y-%m-%d")

    conn = _conn()
    where = "AND scheduled_date = ?"
    params: List[Any] = [scheduled_date]
    status_filter = "status = 'approved'" if approved_only else "status IN ('queued', 'approved', 'edited')"

    emails = conn.execute(
        f"""SELECT * FROM email_queue
            WHERE {status_filter}
            {where}
            ORDER BY batch_number ASC
            LIMIT ?""",
        params + [max_count]
    ).fetchall()

    if not emails:
        conn.close()
        return {"status": "no_emails", "sent": 0}

    # Import outbound gate for Rule 0 compliance
    try:
        from integrations.messaging import outbound_gate
    except ImportError:
        log.error("Cannot import outbound_gate — aborting send")
        conn.close()
        return {"status": "error", "reason": "outbound_gate not available"}

    # Import SMTP sender
    try:
        from integrations.messaging import Messenger
        config_path = Path.home() / ".nexus" / "config.json"
        config = json.loads(config_path.read_text())
        gmail_addr = config.get("gmail_address", "zoarbathrooms@gmail.com")
        gmail_pass = config.get("gmail_app_password", "")
        if not gmail_pass:
            conn.close()
            return {"status": "error", "reason": "gmail_app_password not configured"}
        messenger = Messenger(gmail_addr, gmail_pass)
    except Exception as e:
        conn.close()
        return {"status": "error", "reason": f"Messenger init failed: {e}"}

    sent = 0
    failed = 0
    blocked = 0

    def _log_vendor_outreach(vendor_id: Any, status: str, message: str, detail: str, metadata: Dict[str, Any]) -> None:
        vid = _safe_int(vendor_id, 0)
        if vid <= 0:
            return
        try:
            payload = {"detail": detail}
            if metadata:
                payload.update(metadata)
            conn.execute(
                """
                INSERT INTO vendor_outreach (vendor_id, channel, message_draft, status, sent_at, response, created_at)
                VALUES (?, 'email', ?, ?, datetime('now'), ?, datetime('now'))
                """,
                (vid, (message or "")[:4000], status, json.dumps(payload)[:1000]),
            )
        except Exception:
            # Do not fail send pipeline if outreach logging fails.
            pass

    for email in emails:
        email = dict(email)
        queue_id = email["id"]
        recipient = email["recipient_email"]
        normalized_recipient = _normalize_email_address(recipient)
        if not normalized_recipient:
            conn.execute(
                "UPDATE email_queue SET status = 'failed', send_result = ? WHERE id = ?",
                ("preflight:invalid_email_format", queue_id),
            )
            _log_vendor_outreach(
                email.get("vendor_id"),
                status="failed",
                message=email.get("body_plain", ""),
                detail="preflight:invalid_email_format",
                metadata={"queue_id": queue_id},
            )
            failed += 1
            conn.commit()
            continue
        recipient = normalized_recipient
        preflight_reason = _email_preflight_reason(conn, recipient)
        if preflight_reason:
            conn.execute(
                "UPDATE email_queue SET status = 'failed', send_result = ? WHERE id = ?",
                (f"preflight:{preflight_reason}", queue_id),
            )
            _log_vendor_outreach(
                email.get("vendor_id"),
                status="failed",
                message=email.get("body_plain", ""),
                detail=f"preflight:{preflight_reason}",
                metadata={
                    "queue_id": queue_id,
                    "template_type": email.get("template_type", "initial_outreach"),
                    "step_number": _step_from_template_type(email.get("template_type", "")),
                },
            )
            failed += 1
            conn.commit()
            continue

        tags = _parse_tag_payload(email.get("tags"))
        step_number = _safe_int(tags.get("step_number"), 0)
        if step_number <= 0:
            step_number = _step_from_template_type(email.get("template_type", ""))
        sequence_segment = (tags.get("sequence_segment") or "").strip().lower()
        if not sequence_segment:
            try:
                from core.email_sequences import map_category_to_segment
                sequence_segment = map_category_to_segment(email.get("category", "other"))
            except Exception:
                sequence_segment = "vendor"

        # RULE 0: Check blocklist via outbound_gate
        gate_result = outbound_gate(
            channel="email",
            recipient=recipient,
            recipient_name=email.get("recipient_name", ""),
            message=email["body_plain"],
            code_path="email_queue_manager.send_approved",
        )

        if not gate_result.get("ok"):
            log.warning("Blocked by outbound_gate: %s — %s", recipient, gate_result.get("reason"))
            conn.execute(
                "UPDATE email_queue SET status = 'failed', send_result = ? WHERE id = ?",
                (f"blocked: {gate_result.get('reason', 'unknown')}", queue_id)
            )
            _log_vendor_outreach(
                email.get("vendor_id"),
                status="failed",
                message=email.get("body_plain", ""),
                detail=f"blocked:{gate_result.get('reason', 'unknown')}",
                metadata={
                    "queue_id": queue_id,
                    "template_type": email.get("template_type", "initial_outreach"),
                    "step_number": step_number,
                    "sequence_segment": sequence_segment,
                },
            )
            blocked += 1
            continue

        # Send the email
        try:
            messenger.send_email(
                to_email=recipient,
                subject=email["subject"],
                body=email["body_plain"],
            )
            conn.execute(
                "UPDATE email_queue SET status = 'sent', sent_at = datetime('now'), send_result = 'ok' WHERE id = ?",
                (queue_id,)
            )

            # Update vendor outreach status
            if email.get("vendor_id"):
                conn.execute(
                    "UPDATE vendors SET outreach_status = 'sent', updated_at = datetime('now') WHERE id = ?",
                    (email["vendor_id"],)
                )
                _log_vendor_outreach(
                    email.get("vendor_id"),
                    status="sent",
                    message=email.get("body_plain", ""),
                    detail="queue_send_ok",
                    metadata={
                        "queue_id": queue_id,
                        "template_type": email.get("template_type", "initial_outreach"),
                        "step_number": step_number,
                        "sequence_segment": sequence_segment,
                        "subject": email.get("subject", "")[:180],
                        "ab_subject_variant": tags.get("ab_subject_variant", ""),
                        "ab_body_variant": tags.get("ab_body_variant", ""),
                    },
                )

            sent += 1
            conn.commit()

            # Throttle: 3 seconds between emails
            time.sleep(3)

        except Exception as e:
            log.error("Failed to send to %s: %s", recipient, e)
            conn.execute(
                "UPDATE email_queue SET status = 'failed', send_result = ? WHERE id = ?",
                (str(e)[:500], queue_id)
            )
            _log_vendor_outreach(
                email.get("vendor_id"),
                status="failed",
                message=email.get("body_plain", ""),
                detail=f"send_error:{str(e)[:180]}",
                metadata={
                    "queue_id": queue_id,
                    "template_type": email.get("template_type", "initial_outreach"),
                    "step_number": step_number,
                    "sequence_segment": sequence_segment,
                },
            )
            failed += 1
            conn.commit()

    conn.close()
    log.info("Email queue send complete: sent=%d failed=%d blocked=%d", sent, failed, blocked)
    return {"status": "ok", "sent": sent, "failed": failed, "blocked": blocked}
