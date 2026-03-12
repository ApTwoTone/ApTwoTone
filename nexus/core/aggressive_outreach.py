"""Aggressive but compliant outreach research pipeline."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from core.email_queue_manager import evaluate_vendor_candidate
from core.outreach_targeting import (
    booking_priority_score,
    booking_priority_sort_key,
    call_opening_line,
    contact_first_name,
    has_phone,
    is_fast_booking_category,
    looks_human_contact_name,
    next_step_hint,
    normalized_contact_name,
    recommended_outreach_angle,
)

log = logging.getLogger("aggressive_outreach")

DB_PATH = Path.home() / ".nexus" / "memory.db"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?1[\s.\-]?)?(?:\(?\d{3}\)?[\s.\-]?)\d{3}[\s.\-]?\d{4}")
TITLE_NAME_RE = re.compile(
    r"(?:owner|founder|manager|director|coordinator|event manager|general manager|sales manager)"
    r"[:\s,\-]+([A-Z][a-z]+(?: [A-Z][a-z]+){0,2})",
    re.IGNORECASE,
)
ASSET_TLDS = {
    "png",
    "jpg",
    "jpeg",
    "gif",
    "webp",
    "svg",
    "avif",
    "ico",
    "css",
    "js",
    "map",
    "woff",
    "woff2",
    "ttf",
    "eot",
    "otf",
}
BLOCKED_EMAIL_DOMAINS = {
    "domain.com",
    "domainmarket.com",
    "email.com",
    "godaddy.com",
    "latofonts.com",
    "pixelspread.com",
    "typemade.mx",
    "indiantypefoundry.com",
    "wixpress.com",
}
BLOCKED_EMAIL_EXACT = {
    "user@domain.com",
    "filler@godaddy.com",
}
BLOCKED_EMAIL_LOCALS = {"user", "filler", "example", "test", "your"}
FREE_EMAIL_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "hotmail.com",
    "outlook.com",
    "icloud.com",
    "me.com",
    "aol.com",
    "live.com",
    "msn.com",
    "sbcglobal.net",
}

CONTACT_LINK_HINTS = (
    "contact",
    "about",
    "team",
    "events",
    "weddings",
    "private-events",
    "private_events",
    "services",
    "service",
    "packages",
    "pricing",
    "faq",
    "preferred-vendor",
    "preferred_vendor",
    "vendor",
    "venue",
)

SIGNAL_KEYWORDS = (
    "wedding",
    "outdoor",
    "private event",
    "banquet",
    "reception",
    "ceremony",
    "venue",
    "quince",
    "celebration",
    "vendor list",
)
TEST_BUSINESS_RE = re.compile(r"\b(test|dummy|sample|placeholder)\b", re.IGNORECASE)
CONTACT_NAME_PATTERNS = (
    (
        "role_before_name",
        re.compile(
            r"(?:founder|owner|director|coordinator|planner|designer|manager|ceo|president|sales manager|general manager)"
            r"(?:\s+and\s+\w+)?(?:\s+of|\s*[:,-])\s+([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+){0,2})",
            re.IGNORECASE,
        ),
    ),
    (
        "name_before_role",
        re.compile(
            r"([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+){0,2}),?\s+"
            r"(?:founder|owner|director|coordinator|planner|designer|manager|ceo|president|sales manager|general manager)",
            re.IGNORECASE,
        ),
    ),
    (
        "intro_phrase",
        re.compile(
            r"(?:my name is|i am|i'm|ask for)\s+"
            r"([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+){0,2})",
            re.IGNORECASE,
        ),
    ),
)
FIT_SIGNAL_RULES = (
    ("weddings", ("wedding", "bridal", "ceremony", "reception")),
    ("outdoor events", ("outdoor", "garden", "ranch", "equestrian", "open air")),
    ("private events", ("private event", "private events", "private party", "private parties")),
    ("banquet events", ("banquet", "ballroom")),
    ("corporate events", ("corporate event", "conference", "meeting", "summit")),
    ("destination weddings", ("destination wedding", "destination weddings")),
    ("quinceaneras", ("quince", "quinceanera")),
    ("preferred-vendor programs", ("preferred vendor", "vendor list", "vendor partner")),
)
LOW_QUALITY_HINT_RULES = (
    ("parked_domain", ("domain for sale", "buy this domain", "premium domain names", "domain names for sale")),
    ("coming_soon_site", ("coming soon", "under construction", "launching soon")),
    ("template_site", ("default web site page", "placeholder", "lorem ipsum")),
)

SMS_BLOCK_REASON = "Cold SMS is blocked until explicit consent is captured."
TERMINAL_VENDOR_STATUSES = {
    "replied",
    "bounced",
    "blacklisted",
    "booked",
    "converted",
    "closed",
    "do_not_contact",
}
RESEARCH_STACK = [
    "discovery_agent",
    "contact_agent",
    "fit_agent",
    "offer_agent",
    "copy_agent",
]


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: Dict[str, str]) -> None:
    existing = {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN "{name}" {ddl}')


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def ensure_tables() -> None:
    conn = _conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS aggressive_outreach_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_id INTEGER NOT NULL UNIQUE,
            business_name TEXT DEFAULT '',
            category TEXT DEFAULT '',
            city TEXT DEFAULT '',
            website TEXT DEFAULT '',
            official_email TEXT DEFAULT '',
            official_phone TEXT DEFAULT '',
            contact_name TEXT DEFAULT '',
            contact_role TEXT DEFAULT '',
            source_url TEXT DEFAULT '',
            source_title TEXT DEFAULT '',
            evidence_snippet TEXT DEFAULT '',
            discovered_emails TEXT DEFAULT '[]',
            discovered_phones TEXT DEFAULT '[]',
            message_subject TEXT DEFAULT '',
            message_body TEXT DEFAULT '',
            call_script TEXT DEFAULT '',
            sms_draft TEXT DEFAULT '',
            sms_allowed INTEGER DEFAULT 0,
            sms_reason TEXT DEFAULT '',
            email_ready INTEGER DEFAULT 0,
            call_ready INTEGER DEFAULT 0,
            status TEXT DEFAULT 'researched',
            queue_email_id INTEGER DEFAULT 0,
            notes TEXT DEFAULT '',
            last_researched_at TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS aggressive_outreach_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id INTEGER NOT NULL,
            source_url TEXT DEFAULT '',
            page_title TEXT DEFAULT '',
            extracted_emails TEXT DEFAULT '[]',
            extracted_phones TEXT DEFAULT '[]',
            evidence_snippet TEXT DEFAULT '',
            fetched_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS aggressive_outreach_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id INTEGER DEFAULT 0,
            vendor_id INTEGER DEFAULT 0,
            agent_name TEXT DEFAULT '',
            event_type TEXT DEFAULT '',
            detail TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_aggressive_targets_status ON aggressive_outreach_targets(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_aggressive_sources_target ON aggressive_outreach_sources(target_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_aggressive_activity_target ON aggressive_outreach_activity(target_id)")
    _ensure_columns(
        conn,
        "aggressive_outreach_targets",
        {
            "sales_brief": "TEXT DEFAULT ''",
            "offer_strategy": "TEXT DEFAULT ''",
            "qualifying_questions": "TEXT DEFAULT '[]'",
            "fit_signals": "TEXT DEFAULT '[]'",
            "research_stack": "TEXT DEFAULT '[]'",
            "site_quality_flags": "TEXT DEFAULT '[]'",
            "contact_confidence": "INTEGER DEFAULT 0",
        },
    )
    conn.commit()
    conn.close()


def _log_activity(
    conn: sqlite3.Connection,
    vendor_id: int,
    target_id: int,
    agent_name: str,
    event_type: str,
    detail: str,
) -> None:
    conn.execute(
        """
        INSERT INTO aggressive_outreach_activity (target_id, vendor_id, agent_name, event_type, detail, created_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        """,
        (int(target_id or 0), int(vendor_id or 0), agent_name or "", event_type or "", (detail or "")[:1000]),
    )


def _normalize_email(raw: str) -> str:
    text = str(raw or "").strip().lower()
    match = EMAIL_RE.search(text)
    return match.group(0) if match else ""


def _is_plausible_email(raw: str) -> bool:
    email = _normalize_email(raw)
    if not email:
        return False
    if email in BLOCKED_EMAIL_EXACT:
        return False
    local, _, domain = email.partition("@")
    if not local or "." not in domain:
        return False
    if local in BLOCKED_EMAIL_LOCALS:
        return False
    tld = domain.rsplit(".", 1)[-1]
    if tld in ASSET_TLDS:
        return False
    if "@2x" in email or "/@" in email:
        return False
    if domain in BLOCKED_EMAIL_DOMAINS or domain.endswith(".wixpress.com"):
        return False
    if "sentry" in domain or "sentry" in local:
        return False
    return True


def _normalize_phone(raw: str) -> str:
    digits = re.sub(r"[^\d]", "", str(raw or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return ""
    if digits[0] in {"0", "1"} or digits[3] in {"0", "1"}:
        return ""
    if len(set(digits)) <= 2:
        return ""
    return digits


def _phone_display(raw: str) -> str:
    digits = _normalize_phone(raw)
    if len(digits) != 10:
        return str(raw or "").strip()
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def _json_list(values: Sequence[str]) -> str:
    return json.dumps(list(values), ensure_ascii=False)


def _parse_json_list(raw: Any) -> List[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [str(item).strip() for item in data if str(item).strip()]


def _clean_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    return cleaned


def _domain_from_url(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower().strip()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _extract_emails(*chunks: str) -> List[str]:
    found = set()
    for chunk in chunks:
        for email in EMAIL_RE.findall(chunk or ""):
            normalized = _normalize_email(email) if _is_plausible_email(email) else ""
            if normalized:
                found.add(normalized)
    return sorted(found)


def _extract_phones(*chunks: str) -> List[str]:
    found = set()
    for chunk in chunks:
        for phone in PHONE_RE.findall(chunk or ""):
            normalized = _normalize_phone(phone)
            if normalized:
                found.add(normalized)
    return sorted(found)


def _email_domain(email: str) -> str:
    normalized = _normalize_email(email)
    if "@" not in normalized:
        return ""
    return normalized.partition("@")[2]


def _clean_discovered_emails(emails: Sequence[str], website_url: str, fallback_email: str) -> List[str]:
    candidates: List[str] = []
    seen = set()
    for item in emails:
        normalized = _normalize_email(item)
        if normalized and _is_plausible_email(normalized) and normalized not in seen:
            seen.add(normalized)
            candidates.append(normalized)

    fallback = _normalize_email(fallback_email)
    if fallback and _is_plausible_email(fallback) and fallback not in seen:
        candidates.append(fallback)
        seen.add(fallback)

    if not candidates:
        return []

    domain = _domain_from_url(website_url)

    def sort_key(email: str) -> Tuple[Any, ...]:
        local, _, dom = email.partition("@")
        generic_penalty = 1 if local in {"info", "hello", "contact", "sales", "events", "support", "team"} else 0
        domain_match_penalty = 0 if domain and dom.endswith(domain) else 1
        free_domain_penalty = 1 if dom in FREE_EMAIL_DOMAINS else 0
        return (domain_match_penalty, generic_penalty, free_domain_penalty, email)

    ordered = sorted(candidates, key=sort_key)
    if fallback and fallback in ordered:
        ordered = [fallback] + [item for item in ordered if item != fallback]

    if len(ordered) > 6:
        if fallback:
            return [fallback]
        domain_matches = [item for item in ordered if domain and item.endswith(f"@{domain}")]
        if domain_matches:
            return domain_matches[:3]
        return ordered[:3]
    return ordered[:5]


def _clean_discovered_phones(phones: Sequence[str], fallback_phone: str) -> List[str]:
    candidates: List[str] = []
    seen = set()
    for item in phones:
        normalized = _normalize_phone(item)
        if normalized and normalized not in seen:
            seen.add(normalized)
            candidates.append(normalized)

    fallback = _normalize_phone(fallback_phone)
    if fallback and fallback in candidates:
        candidates = [fallback] + [item for item in candidates if item != fallback]
    elif fallback:
        candidates = [fallback] + candidates

    if len(candidates) > 12:
        if fallback:
            return [fallback]
        return candidates[:3]
    return candidates[:6]


def _name_from_email(email: str) -> str:
    normalized = _normalize_email(email)
    if not _is_plausible_email(normalized):
        return ""
    local = normalized.partition("@")[0]
    if any(ch.isdigit() for ch in local):
        return ""
    if local in BLOCKED_EMAIL_LOCALS or local in {"hello", "info", "contact", "events", "sales", "bookings"}:
        return ""
    parts = [part for part in re.split(r"[._\-]+", local) if part]
    if len(parts) == 1 and len(local) > 10:
        return ""
    if not parts or len(parts) > 2:
        return ""
    candidate = " ".join(part.capitalize() for part in parts)
    clean = normalized_contact_name(candidate)
    if not looks_human_contact_name(clean):
        return ""
    return clean


def _extract_contact_identity(
    *chunks: str,
    fallback: str = "",
    fallback_email: str = "",
    fallback_confidence: Any = 0,
    fallback_verified: Any = 0,
) -> Tuple[str, int]:
    candidates: List[Tuple[str, int]] = []

    fallback_name = normalized_contact_name(fallback)
    fallback_verified_bool = int(fallback_verified or 0) == 1
    if fallback_name:
        base_conf = 95 if fallback_verified_bool else max(72, min(92, int(fallback_confidence or 0)))
        if looks_human_contact_name(fallback_name, confidence=base_conf, verified=fallback_verified_bool):
            candidates.append((fallback_name, base_conf))

    email_name = _name_from_email(fallback_email)
    if email_name:
        candidates.append((email_name, 72))

    for chunk in chunks:
        text = str(chunk or "")
        for pattern_name, pattern in CONTACT_NAME_PATTERNS:
            for match in pattern.findall(text):
                raw_candidate = match[-1] if isinstance(match, tuple) else match
                clean = normalized_contact_name(raw_candidate)
                if not clean:
                    continue
                confidence = 84 if pattern_name in {"role_before_name", "name_before_role"} else 78
                if len(clean.split()) == 1:
                    confidence -= 6
                if not looks_human_contact_name(clean, confidence=confidence):
                    continue
                candidates.append((clean, confidence))

    if not candidates:
        return "", 0

    ranked = sorted(
        candidates,
        key=lambda item: (
            -int(item[1]),
            -len(item[0].split()),
            -len(item[0]),
            item[0],
        ),
    )
    best_name, best_confidence = ranked[0]
    if best_confidence < 70:
        return "", 0
    return best_name, int(best_confidence)


def _extract_contact_name(*chunks: str, fallback: str = "", fallback_email: str = "", fallback_confidence: Any = 0, fallback_verified: Any = 0) -> str:
    name, _confidence = _extract_contact_identity(
        *chunks,
        fallback=fallback,
        fallback_email=fallback_email,
        fallback_confidence=fallback_confidence,
        fallback_verified=fallback_verified,
    )
    return name


def _extract_evidence_snippet(*chunks: str) -> str:
    for chunk in chunks:
        text = re.sub(r"<[^>]+>", " ", str(chunk or ""))
        lines = re.split(r"[\n\r]+", text)
        for line in lines:
            cleaned = _clean_text(line)
            if len(cleaned) < 24 or len(cleaned) > 220:
                continue
            if "@2x" in cleaned or re.search(r"\.(png|jpg|jpeg|gif|svg|webp)\b", cleaned, re.IGNORECASE):
                continue
            if "<" in cleaned or ">" in cleaned:
                continue
            lower = cleaned.lower()
            if any(keyword in lower for keyword in SIGNAL_KEYWORDS):
                return cleaned
    for chunk in chunks:
        cleaned = _clean_text(re.sub(r"<[^>]+>", " ", str(chunk or "")))
        if len(cleaned) >= 24:
            return cleaned[:220]
    return ""


def _collect_signal_labels(text: str) -> List[str]:
    haystack = str(text or "").lower()
    labels = []
    if "wedding" in haystack:
        labels.append("weddings")
    if "outdoor" in haystack:
        labels.append("outdoor events")
    if "private event" in haystack or "private events" in haystack:
        labels.append("private events")
    if "vendor list" in haystack:
        labels.append("vendor-list partnerships")
    if "banquet" in haystack:
        labels.append("banquet events")
    if "quince" in haystack:
        labels.append("quinceanera events")
    return labels[:3]


def _fit_signals(text: str, vendor: Dict[str, Any], source_pages: Sequence[Dict[str, Any]]) -> List[str]:
    haystack = str(text or "").lower()
    signals: List[str] = []
    for label, keywords in FIT_SIGNAL_RULES:
        if any(keyword in haystack for keyword in keywords):
            signals.append(label)
    category = str(vendor.get("category") or "").replace("_", " ").strip().lower()
    if category and category not in signals:
        signals.insert(0, category)
    for page in source_pages:
        url = str(page.get("url") or "").lower()
        if "preferred-vendor" in url or "vendor-list" in url:
            signals.append("preferred-vendor programs")
        if "wedding" in url:
            signals.append("weddings")
    deduped = []
    for signal in signals:
        clean = str(signal).strip()
        if clean and clean not in deduped:
            deduped.append(clean)
    return deduped[:5]


def _site_quality_flags(text: str, website: str, discovered_emails: Sequence[str], discovered_phones: Sequence[str]) -> List[str]:
    haystack = str(text or "").lower()
    flags: List[str] = []
    for label, hints in LOW_QUALITY_HINT_RULES:
        if any(hint in haystack for hint in hints):
            flags.append(label)
    domain = _domain_from_url(website)
    if "facebook.com" in domain:
        flags.append("social_profile_source")
    if "domainmarket.com" in domain or "godaddy.com" in domain:
        flags.append("brokered_domain_source")
    if len(discovered_phones) > 12:
        flags.append("phone_noise")
    if any(not _is_plausible_email(email) for email in discovered_emails):
        flags.append("email_noise")
    deduped = []
    for flag in flags:
        if flag not in deduped:
            deduped.append(flag)
    return deduped


def _target_needs_cleanup(row: Dict[str, Any]) -> bool:
    current_name = str(row.get("existing_contact_name") or row.get("contact_name") or "").strip()
    current_confidence = int(row.get("existing_contact_confidence") or row.get("contact_confidence") or 0)
    if current_name and not looks_human_contact_name(current_name, confidence=current_confidence):
        return True

    discovered_emails = _parse_json_list(row.get("discovered_emails"))
    discovered_phones = _parse_json_list(row.get("discovered_phones"))
    flags = set(_parse_json_list(row.get("site_quality_flags")))
    if len(discovered_phones) > 6 or len(discovered_emails) > 5:
        return True
    if flags.intersection({"phone_noise", "email_noise"}):
        return True

    official_email = str(row.get("official_email") or "").strip()
    official_phone = str(row.get("official_phone") or "").strip()
    if official_email and not _is_plausible_email(official_email):
        return True
    if official_email or official_phone:
        decision = evaluate_vendor_candidate(
            {
                "name": row.get("name", ""),
                "category": row.get("category", ""),
                "website": row.get("website", ""),
                "email": official_email or row.get("email", ""),
                "phone": official_phone or row.get("phone", ""),
                "source": row.get("source", "aggressive_outreach"),
                "email_valid": 1 if official_email else row.get("email_valid", 0),
                "website_status": row.get("website_status", 1),
                "phone_valid": row.get("phone_valid", 0),
            }
        )
        if official_email and not decision.get("eligible"):
            return True
    return False


def _sales_brief_for_vendor(vendor: Dict[str, Any], fit_signals: Sequence[str], evidence: str, quality_flags: Sequence[str]) -> str:
    business = str(vendor.get("name") or "This business").strip()
    city = str(vendor.get("city") or "the area").strip()
    category = str(vendor.get("category") or "event partner").replace("_", " ").strip()
    fit_text = ", ".join(fit_signals[:3]) if fit_signals else category
    brief = f"{business} looks like a {category} in {city} with visible fit around {fit_text}."
    if evidence:
        brief += f" Best on-site clue: {evidence}"
    if quality_flags:
        brief += f" Review carefully because the site also showed: {', '.join(quality_flags)}."
    return brief


def _offer_strategy_for_vendor(vendor: Dict[str, Any], fit_signals: Sequence[str], quality_flags: Sequence[str]) -> str:
    category_rank = booking_priority_score(vendor, {"quality_score": 70, "generic_inbox": False, "eligible": True, "quality_flags": []})
    category = str(vendor.get("category") or "").lower()
    if any(flag in quality_flags for flag in {"parked_domain", "coming_soon_site", "template_site"}):
        return "Treat this as a manual-review lead first. Verify the real operator and direct contact before sending anything."
    if "venue" in category or "banquet" in category:
        return "Lead with a preferred-vendor / overflow-capacity offer. If they already have a primary restroom vendor, pitch Zoar as approved backup for peak dates. If they do not, offer preferred placement plus a referral payout on booked jobs."
    if "planner" in category:
        return "Lead with a white-glove backup-vendor offer for outdoor and private-property events. Stress fast quote turnaround, reliable setup, and a referral structure when their client books through them."
    if "rental" in category or "cater" in category:
        return "Lead with an add-on partner angle. Position Zoar as the restroom trailer vendor they can confidently bundle or refer when clients need a full outdoor-event package."
    if category_rank >= 90:
        return "Push for a direct partnership conversation and ask who controls preferred-vendor decisions."
    return "Lead with the fastest path to becoming their backup or preferred restroom trailer vendor, then ask how they currently handle outdoor-event restroom coverage."


def _qualifying_questions_for_vendor(vendor: Dict[str, Any], fit_signals: Sequence[str], quality_flags: Sequence[str]) -> List[str]:
    category = str(vendor.get("category") or "").lower()
    if any(flag in quality_flags for flag in {"parked_domain", "coming_soon_site", "template_site"}):
        return [
            "Who is the active operator behind this business right now?",
            "Is this still a live booking business with outdoor-event volume?",
            "What is the best direct email and mobile number for vendor partnerships?",
        ]
    if "venue" in category or "banquet" in category:
        return [
            "Do you already have a primary restroom trailer vendor on your preferred list?",
            "When outdoor dates or overflow capacity come up, who approves a secondary vendor?",
            "If you do not have a primary partner, are you open to preferred-vendor pricing plus a referral payout on booked jobs?",
        ]
    if "planner" in category:
        return [
            "For outdoor or private-property events, do you already have a restroom trailer vendor you trust?",
            "When your main option is booked, would a fast-response backup vendor help you close more dates?",
            "If a client books through your recommendation, are you open to a referral structure on completed jobs?",
        ]
    return [
        "Who currently handles outdoor-event restroom coverage for your clients?",
        "What usually triggers the need for a restroom trailer instead of standard restrooms?",
        "If Zoar proves reliable on one job, what would it take to become your default backup vendor?",
    ]


def _preferred_email(emails: Sequence[str], website_url: str, fallback_email: str) -> str:
    candidates = [_normalize_email(item) for item in emails if _is_plausible_email(item)]
    normalized_fallback = ""
    if fallback_email:
        normalized_fallback = _normalize_email(fallback_email)
        if _is_plausible_email(normalized_fallback) and normalized_fallback not in candidates:
            candidates.append(normalized_fallback)
    if not candidates:
        return ""
    if normalized_fallback and len(candidates) > 4:
        return normalized_fallback

    domain = _domain_from_url(website_url)

    def sort_key(email: str) -> Tuple[Any, ...]:
        local, _, dom = email.partition("@")
        generic_penalty = 1 if local in {"info", "hello", "contact", "sales", "events"} else 0
        domain_match_penalty = 0 if domain and dom.endswith(domain) else 1
        free_domain_penalty = 1 if dom in {"gmail.com", "yahoo.com", "hotmail.com", "icloud.com"} else 0
        return (domain_match_penalty, generic_penalty, free_domain_penalty, email)

    return sorted(candidates, key=sort_key)[0]


def _preferred_phone(phones: Sequence[str], fallback_phone: str) -> str:
    candidates = [_normalize_phone(item) for item in phones if _normalize_phone(item)]
    fallback = _normalize_phone(fallback_phone)
    if fallback and fallback in candidates:
        return _phone_display(fallback)
    if fallback and len(candidates) > 8:
        return _phone_display(fallback)
    if fallback and fallback not in candidates:
        candidates.append(fallback)
    return _phone_display(candidates[0]) if candidates else _phone_display(fallback_phone)


def _subject_for_vendor(vendor: Dict[str, Any], evidence: str) -> str:
    business = str(vendor.get("name") or "your business").strip()
    category = str(vendor.get("category") or "").strip().lower()
    if "venue" in category or category in {"banquet_hall", "quinceanera_venue"}:
        return f"Quick vendor list question for {business}"
    if "planner" in category:
        return f"Backup restroom vendor for {business}"
    return f"Restroom trailer partner for {business}"


def _body_for_vendor(vendor: Dict[str, Any], evidence: str) -> str:
    first_name = contact_first_name(vendor)
    greeting = f"Hi {first_name}," if first_name else "Hi there,"
    business = str(vendor.get("name") or "your business").strip()
    city = str(vendor.get("city") or "the area").strip()
    angle = recommended_outreach_angle(vendor)
    sales_brief = str(vendor.get("sales_brief") or "").strip()
    offer_strategy = str(vendor.get("offer_strategy") or "").strip()
    qualifying_questions = _parse_json_list(vendor.get("qualifying_questions"))
    evidence_line = ""
    if evidence:
        evidence_line = f"I was looking at {business}'s website and saw: \"{evidence}\".\n\n"
    brief_line = f"{sales_brief}\n\n" if sales_brief else ""
    offer_line = f"Best-fit partnership angle from our side: {offer_strategy}\n\n" if offer_strategy else ""
    question_line = qualifying_questions[0] if qualifying_questions else angle

    return (
        f"{greeting}\n\n"
        f"{evidence_line}"
        f"{brief_line}"
        f"Quick question: {question_line}\n\n"
        f"{offer_line}"
        f"Zoar Bathroom Rentals covers {city} and nearby LA/SFV events with fast availability checks plus full delivery, setup, and pickup.\n\n"
        "If you already have a primary vendor, we would like to be the backup option for peak dates. "
        "If you do not, we can discuss preferred-vendor pricing plus a referral structure on booked jobs.\n\n"
        "Best,\n"
        "Zoar Bathroom Rentals\n"
        "(424) 235-8979\n"
        "zoarbathroomrental.com\n"
    )


def _call_script_for_vendor(vendor: Dict[str, Any], evidence: str) -> str:
    opener = call_opening_line(vendor)
    sales_brief = str(vendor.get("sales_brief") or "").strip()
    offer_strategy = str(vendor.get("offer_strategy") or "").strip()
    qualifying_questions = _parse_json_list(vendor.get("qualifying_questions"))
    detail = f"Website note: {evidence}" if evidence else "Lead with the backup-vendor question and ask who handles vendor partnerships."
    question_block = ""
    if qualifying_questions:
        question_block = "Ask next:\n- " + "\n- ".join(qualifying_questions[:3])
    offer_block = f"Offer angle: {offer_strategy}" if offer_strategy else ""
    brief_block = f"Sales read: {sales_brief}" if sales_brief else ""
    extras = "\n\n".join(part for part in [detail, brief_block, offer_block, question_block] if part)
    return f"{opener}\n\n{extras}\n\nNext step: {next_step_hint(vendor)}"


def _html_from_plain(text: str) -> str:
    return "<p>" + html.escape(text or "").replace("\n", "<br>") + "</p>"


def _active_batch_date() -> str:
    try:
        from zoneinfo import ZoneInfo
    except ImportError:  # pragma: no cover
        from backports.zoneinfo import ZoneInfo  # type: ignore

    now_pt = datetime.now(ZoneInfo("America/Los_Angeles"))
    today = now_pt.strftime("%Y-%m-%d")
    tomorrow = (now_pt + timedelta(days=1)).strftime("%Y-%m-%d")

    conn = _conn()
    try:
        active_today = conn.execute(
            "SELECT COUNT(*) FROM email_queue WHERE scheduled_date = ? AND status IN ('queued','edited','approved')",
            (today,),
        ).fetchone()[0]
        active_tomorrow = conn.execute(
            "SELECT COUNT(*) FROM email_queue WHERE scheduled_date = ? AND status IN ('queued','edited','approved')",
            (tomorrow,),
        ).fetchone()[0]
    finally:
        conn.close()

    if active_today > 0 and active_tomorrow == 0:
        return today
    return tomorrow


def _candidate_rows(limit: int, force_refresh: bool = False) -> List[Dict[str, Any]]:
    ensure_tables()
    conn = _conn()
    rows = conn.execute(
        """
        SELECT v.id, v.name, v.category, v.city, v.website, v.email, v.phone,
               v.outreach_status, v.referral_score, v.phone_valid, v.email_valid,
               v.website_status, v.campaign_quality, v.source, v.notes,
               v.contact_name, v.contact_name_source, v.contact_name_confidence, v.contact_name_verified,
               t.id AS target_id, t.last_researched_at
        FROM vendors v
        LEFT JOIN aggressive_outreach_targets t ON t.vendor_id = v.id
        WHERE COALESCE(v.website, '') != ''
          AND COALESCE(v.name, '') != ''
          AND COALESCE(v.outreach_status, 'none') NOT IN ('replied','bounced','blacklisted','booked','converted','closed','do_not_contact')
        ORDER BY v.campaign_quality DESC, v.referral_score DESC, v.id ASC
        LIMIT ?
        """,
        (max(int(limit) * 12, 300),),
    ).fetchall()
    conn.close()

    candidates = []
    for row in rows:
        vendor = dict(row)
        if TEST_BUSINESS_RE.search(str(vendor.get("name") or "")):
            continue
        if not is_fast_booking_category(vendor.get("category")):
            continue
        if not force_refresh and str(vendor.get("last_researched_at") or "").strip():
            try:
                last = datetime.fromisoformat(str(vendor["last_researched_at"]).replace("Z", "+00:00"))
                if (datetime.utcnow() - last.replace(tzinfo=None)).total_seconds() < 7 * 86400:
                    continue
            except Exception:
                pass
        decision = evaluate_vendor_candidate(vendor)
        if "category_hard_block" in decision.get("quality_flags", []):
            continue
        vendor["_decision"] = decision
        vendor["_priority_score"] = booking_priority_score(vendor, decision)
        candidates.append(vendor)

    candidates.sort(key=lambda vendor: booking_priority_sort_key(vendor, vendor["_decision"]))
    return candidates[:limit]


async def _fetch_page(page, url: str) -> Optional[Dict[str, Any]]:
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        if not response or response.status >= 400:
            return None
        await asyncio.sleep(1)
        title = await page.title()
        body_text = await page.inner_text("body")
        html_text = await page.content()
        links = await page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href]')).map(a => ({
                href: a.href || a.getAttribute('href') || '',
                text: (a.innerText || a.textContent || '').trim()
            }))"""
        )
        return {
            "url": url,
            "title": _clean_text(title),
            "body_text": body_text,
            "html": html_text,
            "links": links or [],
        }
    except Exception:
        return None


def _candidate_contact_urls(base_url: str, links: Sequence[Dict[str, Any]]) -> List[str]:
    parsed = urlparse(base_url)
    base_host = parsed.netloc.lower()
    urls = []
    for item in links:
        href = str(item.get("href") or "").strip()
        text = str(item.get("text") or "").strip().lower()
        href_lower = href.lower()
        if not href or href.startswith("mailto:") or href.startswith("tel:"):
            continue
        if not any(hint in text or hint in href_lower for hint in CONTACT_LINK_HINTS):
            continue
        full = urljoin(base_url, href)
        if urlparse(full).netloc.lower() != base_host:
            continue
        if full not in urls:
            urls.append(full)
        if len(urls) >= 3:
            break
    return urls


async def _research_vendor(browser, vendor: Dict[str, Any], agent_name: str) -> Dict[str, Any]:
    page = await browser.new_page()
    website = str(vendor.get("website") or "").strip()
    if website and not website.startswith(("http://", "https://")):
        website = "https://" + website

    source_pages = []
    combined_text = []
    combined_html = []
    extracted_emails = []
    extracted_phones = []

    try:
        homepage = await _fetch_page(page, website)
        if homepage:
            source_pages.append(homepage)
            combined_text.append(homepage["body_text"])
            combined_html.append(homepage["html"])
            extracted_emails.extend(_extract_emails(homepage["body_text"], homepage["html"]))
            extracted_phones.extend(_extract_phones(homepage["body_text"], homepage["html"]))

            for contact_url in _candidate_contact_urls(website, homepage.get("links", [])):
                if len(source_pages) >= 4:
                    break
                sub_page = await _fetch_page(page, contact_url)
                if not sub_page:
                    continue
                source_pages.append(sub_page)
                combined_text.append(sub_page["body_text"])
                combined_html.append(sub_page["html"])
                extracted_emails.extend(_extract_emails(sub_page["body_text"], sub_page["html"]))
                extracted_phones.extend(_extract_phones(sub_page["body_text"], sub_page["html"]))
    finally:
        await page.close()

    text_blob = "\n".join(combined_text)
    html_blob = "\n".join(combined_html)
    raw_emails = _extract_emails(*combined_text, *combined_html)
    raw_phones = _extract_phones(*combined_text, *combined_html)
    evidence = _extract_evidence_snippet(text_blob, html_blob)
    signals = _collect_signal_labels(text_blob)
    signal_detail = ", ".join(signals)
    if signal_detail and evidence:
        evidence = f"{evidence} ({signal_detail})"

    quality_flags = _site_quality_flags(text_blob, website, raw_emails, raw_phones)
    discovered_emails = _clean_discovered_emails(raw_emails, website, str(vendor.get("email") or ""))
    discovered_phones = _clean_discovered_phones(raw_phones, str(vendor.get("phone") or ""))
    official_email = _preferred_email(discovered_emails, website, str(vendor.get("email") or ""))
    official_phone = _preferred_phone(discovered_phones, str(vendor.get("phone") or ""))
    contact_name, contact_confidence = _extract_contact_identity(
        text_blob,
        html_blob,
        fallback=str(vendor.get("contact_name") or ""),
        fallback_email=official_email or str(vendor.get("email") or ""),
        fallback_confidence=vendor.get("contact_name_confidence") or 0,
        fallback_verified=vendor.get("contact_name_verified") or 0,
    )
    fit_signals = _fit_signals(text_blob, vendor, source_pages)

    enriched_vendor = dict(vendor)
    enriched_vendor["email"] = official_email or str(vendor.get("email") or "")
    enriched_vendor["phone"] = official_phone or str(vendor.get("phone") or "")
    enriched_vendor["website"] = website
    if contact_name:
        enriched_vendor["contact_name"] = contact_name
    enriched_vendor["sales_brief"] = _sales_brief_for_vendor(vendor, fit_signals, evidence, quality_flags)
    enriched_vendor["offer_strategy"] = _offer_strategy_for_vendor(vendor, fit_signals, quality_flags)
    enriched_vendor["qualifying_questions"] = _qualifying_questions_for_vendor(vendor, fit_signals, quality_flags)
    email_decision = evaluate_vendor_candidate(enriched_vendor)

    status = "needs_review" if any(flag in quality_flags for flag in {"parked_domain", "coming_soon_site", "template_site"}) else "researched"

    return {
        "vendor": vendor,
        "agent_name": agent_name,
        "source_pages": source_pages,
        "discovered_emails": discovered_emails,
        "discovered_phones": discovered_phones,
        "official_email": official_email,
        "official_phone": official_phone,
        "contact_name": contact_name,
        "contact_role": "site contact" if contact_name else "",
        "contact_confidence": int(contact_confidence or 0),
        "evidence_snippet": evidence,
        "sales_brief": enriched_vendor["sales_brief"],
        "offer_strategy": enriched_vendor["offer_strategy"],
        "qualifying_questions": enriched_vendor["qualifying_questions"],
        "fit_signals": fit_signals,
        "research_stack": RESEARCH_STACK,
        "site_quality_flags": quality_flags,
        "message_subject": _subject_for_vendor(enriched_vendor, evidence),
        "message_body": _body_for_vendor(enriched_vendor, evidence),
        "call_script": _call_script_for_vendor(enriched_vendor, evidence),
        "sms_draft": "",
        "sms_allowed": 0,
        "sms_reason": SMS_BLOCK_REASON,
        "email_ready": 1 if official_email and email_decision.get("eligible") else 0,
        "call_ready": 1 if official_phone else 0,
        "status": status,
    }


def _upsert_target(conn: sqlite3.Connection, result: Dict[str, Any]) -> int:
    vendor = result["vendor"]
    vendor_id = int(vendor["id"])
    primary_source = (result.get("source_pages") or [{}])[0] or {}
    row = conn.execute(
        "SELECT id, status FROM aggressive_outreach_targets WHERE vendor_id = ?",
        (vendor_id,),
    ).fetchone()
    existing_status = str(row["status"] or "").strip() if row else ""
    next_status = str(result.get("status", "researched") or "researched").strip()
    if existing_status in TERMINAL_VENDOR_STATUSES or existing_status in {"queued_email", "called", "do_not_contact"}:
        next_status = existing_status

    payload = (
        vendor_id,
        vendor.get("name", ""),
        vendor.get("category", ""),
        vendor.get("city", ""),
        vendor.get("website", ""),
        result.get("official_email", ""),
        result.get("official_phone", ""),
        result.get("contact_name", ""),
        result.get("contact_role", ""),
        primary_source.get("url", ""),
        primary_source.get("title", ""),
        result.get("evidence_snippet", ""),
        _json_list(result.get("discovered_emails", [])),
        _json_list(result.get("discovered_phones", [])),
        result.get("message_subject", ""),
        result.get("message_body", ""),
        result.get("call_script", ""),
        result.get("sms_draft", ""),
        int(result.get("sms_allowed", 0)),
        result.get("sms_reason", SMS_BLOCK_REASON),
        int(result.get("email_ready", 0)),
        int(result.get("call_ready", 0)),
        next_status,
        result.get("sales_brief", ""),
        result.get("offer_strategy", ""),
        _json_list(result.get("qualifying_questions", [])),
        _json_list(result.get("fit_signals", [])),
        _json_list(result.get("research_stack", [])),
        _json_list(result.get("site_quality_flags", [])),
        int(result.get("contact_confidence", 0)),
        _now(),
        _now(),
    )

    if row:
        target_id = int(row["id"])
        conn.execute(
            """
            UPDATE aggressive_outreach_targets
            SET business_name = ?, category = ?, city = ?, website = ?, official_email = ?,
                official_phone = ?, contact_name = ?, contact_role = ?, source_url = ?, source_title = ?,
                evidence_snippet = ?, discovered_emails = ?, discovered_phones = ?, message_subject = ?,
                message_body = ?, call_script = ?, sms_draft = ?, sms_allowed = ?, sms_reason = ?,
                email_ready = ?, call_ready = ?, status = ?, sales_brief = ?, offer_strategy = ?,
                qualifying_questions = ?, fit_signals = ?, research_stack = ?, site_quality_flags = ?,
                contact_confidence = ?, last_researched_at = ?, updated_at = ?
            WHERE id = ?
            """,
            payload[1:] + (target_id,),
        )
    else:
        cur = conn.execute(
            """
            INSERT INTO aggressive_outreach_targets (
                vendor_id, business_name, category, city, website, official_email, official_phone,
                contact_name, contact_role, source_url, source_title, evidence_snippet,
                discovered_emails, discovered_phones, message_subject, message_body, call_script,
                sms_draft, sms_allowed, sms_reason, email_ready, call_ready, status, sales_brief,
                offer_strategy, qualifying_questions, fit_signals, research_stack, site_quality_flags,
                contact_confidence,
                last_researched_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        target_id = int(cur.lastrowid)

    conn.execute("DELETE FROM aggressive_outreach_sources WHERE target_id = ?", (target_id,))
    for source in result.get("source_pages", []):
        emails = _extract_emails(source.get("body_text", ""), source.get("html", ""))
        phones = _extract_phones(source.get("body_text", ""), source.get("html", ""))
        conn.execute(
            """
            INSERT INTO aggressive_outreach_sources (
                target_id, source_url, page_title, extracted_emails, extracted_phones, evidence_snippet, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                target_id,
                source.get("url", ""),
                source.get("title", ""),
                _json_list(emails),
                _json_list(phones),
                _extract_evidence_snippet(source.get("body_text", ""), source.get("html", "")),
            ),
        )
    _log_activity(
        conn,
        vendor_id=vendor_id,
        target_id=target_id,
        agent_name=result.get("agent_name", ""),
        event_type="researched",
        detail="Research complete. email_ready=%s call_ready=%s sms_allowed=%s"
        % (
            int(result.get("email_ready", 0)),
            int(result.get("call_ready", 0)),
            int(result.get("sms_allowed", 0)),
        ),
    )
    return target_id


async def _run_research_batch(vendors: Sequence[Dict[str, Any]], concurrency: int = 5) -> List[Dict[str, Any]]:
    if not vendors:
        return []
    from playwright.async_api import async_playwright

    processed: List[Dict[str, Any]] = []
    semaphore = asyncio.Semaphore(max(1, min(int(concurrency), 5)))

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)

        async def worker(index: int, vendor: Dict[str, Any]) -> None:
            agent_name = f"research-{(index % max(1, min(int(concurrency), 5))) + 1}"
            async with semaphore:
                result = await _research_vendor(browser, vendor, agent_name)
                processed.append(result)

        await asyncio.gather(*(worker(idx, vendor) for idx, vendor in enumerate(vendors)))
        await browser.close()
    return processed


def _persist_research_results(processed: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    if not processed:
        return {"processed": 0, "created": 0, "updated": 0, "targets": []}

    conn = _conn()
    created = 0
    updated = 0
    output_targets: List[Dict[str, Any]] = []
    for result in processed:
        existing = conn.execute(
            "SELECT id FROM aggressive_outreach_targets WHERE vendor_id = ?",
            (int(result["vendor"]["id"]),),
        ).fetchone()
        target_id = _upsert_target(conn, result)
        if existing:
            updated += 1
        else:
            created += 1
        output_targets.append({"target_id": target_id, "vendor_id": int(result["vendor"]["id"]), "business_name": result["vendor"]["name"]})
    conn.commit()
    conn.close()

    return {"processed": len(processed), "created": created, "updated": updated, "targets": output_targets}


def _existing_target_rows(limit: int, noisy_only: bool = True) -> List[Dict[str, Any]]:
    ensure_tables()
    conn = _conn()
    rows = conn.execute(
        """
        SELECT
            v.id, v.name, v.category, v.city, v.website, v.email, v.phone,
            v.outreach_status, v.referral_score, v.phone_valid, v.email_valid,
            v.website_status, v.campaign_quality, v.source, v.notes,
            COALESCE(v.contact_name, '') AS contact_name,
            COALESCE(t.contact_name, '') AS existing_contact_name,
            COALESCE(v.contact_name_source, '') AS contact_name_source,
            COALESCE(v.contact_name_confidence, 0) AS contact_name_confidence,
            COALESCE(t.contact_confidence, 0) AS existing_contact_confidence,
            COALESCE(v.contact_name_verified, 0) AS contact_name_verified,
            t.id AS target_id, t.last_researched_at, t.discovered_emails, t.discovered_phones,
            t.official_email, t.official_phone, t.site_quality_flags
        FROM aggressive_outreach_targets t
        INNER JOIN vendors v ON v.id = t.vendor_id
        ORDER BY t.updated_at DESC, t.id DESC
        """
    ).fetchall()
    conn.close()

    selected: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if noisy_only and not _target_needs_cleanup(item):
            continue
        selected.append(item)
        if len(selected) >= max(1, int(limit)):
            break
    return selected


async def refresh_existing_targets(limit: int = 25, concurrency: int = 5, noisy_only: bool = True) -> Dict[str, Any]:
    vendors = _existing_target_rows(limit=max(1, int(limit)), noisy_only=bool(noisy_only))
    if not vendors:
        return {"ok": True, "processed": 0, "created": 0, "updated": 0, "targets": [], "scope": "existing_targets"}

    processed = await _run_research_batch(vendors, concurrency=concurrency)
    result = _persist_research_results(processed)
    result.update({"ok": True, "scope": "existing_targets", "noisy_only": bool(noisy_only)})
    return result


async def run_research(limit: int = 15, concurrency: int = 5, force_refresh: bool = False) -> Dict[str, Any]:
    ensure_tables()
    vendors = _candidate_rows(limit=max(1, int(limit)), force_refresh=bool(force_refresh))
    if not vendors:
        return {"ok": True, "processed": 0, "created": 0, "updated": 0, "targets": []}

    processed = await _run_research_batch(vendors, concurrency=concurrency)
    result = _persist_research_results(processed)
    result["ok"] = True
    return result


def get_summary() -> Dict[str, Any]:
    ensure_tables()
    conn = _conn()
    total = conn.execute("SELECT COUNT(*) FROM aggressive_outreach_targets").fetchone()[0]
    email_ready = conn.execute("SELECT COUNT(*) FROM aggressive_outreach_targets WHERE email_ready = 1").fetchone()[0]
    call_ready = conn.execute("SELECT COUNT(*) FROM aggressive_outreach_targets WHERE call_ready = 1").fetchone()[0]
    sms_allowed = conn.execute("SELECT COUNT(*) FROM aggressive_outreach_targets WHERE sms_allowed = 1").fetchone()[0]
    queued_email = conn.execute("SELECT COUNT(*) FROM aggressive_outreach_targets WHERE queue_email_id > 0").fetchone()[0]
    last_run = conn.execute("SELECT MAX(last_researched_at) FROM aggressive_outreach_targets").fetchone()[0]
    by_status_rows = conn.execute(
        "SELECT status, COUNT(*) AS cnt FROM aggressive_outreach_targets GROUP BY status"
    ).fetchall()
    conn.close()
    return {
        "total_targets": int(total or 0),
        "email_ready": int(email_ready or 0),
        "call_ready": int(call_ready or 0),
        "sms_allowed": int(sms_allowed or 0),
        "queued_email": int(queued_email or 0),
        "last_run": last_run or "",
        "by_status": {str(row["status"] or "researched"): int(row["cnt"] or 0) for row in by_status_rows},
    }


def get_targets(status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    ensure_tables()
    conn = _conn()
    query = """
        SELECT t.*, v.phone AS vendor_phone, v.email AS vendor_email
        FROM aggressive_outreach_targets t
        LEFT JOIN vendors v ON v.id = t.vendor_id
    """
    params: List[Any] = []
    if status:
        query += " WHERE t.status = ?"
        params.append(status)
    query += " ORDER BY t.updated_at DESC, t.id DESC LIMIT ?"
    params.append(max(1, int(limit)))
    rows = conn.execute(query, params).fetchall()
    results = []
    for row in rows:
        target = dict(row)
        target_id = int(target["id"])
        target["discovered_emails"] = _parse_json_list(target.get("discovered_emails"))
        target["discovered_phones"] = [_phone_display(item) for item in _parse_json_list(target.get("discovered_phones"))]
        target["qualifying_questions"] = _parse_json_list(target.get("qualifying_questions"))
        target["fit_signals"] = _parse_json_list(target.get("fit_signals"))
        target["research_stack"] = _parse_json_list(target.get("research_stack"))
        target["site_quality_flags"] = _parse_json_list(target.get("site_quality_flags"))
        target["contact_confidence"] = int(target.get("contact_confidence") or 0)
        target["sources"] = [
            {
                "source_url": src["source_url"],
                "page_title": src["page_title"],
                "extracted_emails": _parse_json_list(src["extracted_emails"]),
                "extracted_phones": [_phone_display(item) for item in _parse_json_list(src["extracted_phones"])],
                "evidence_snippet": src["evidence_snippet"],
                "fetched_at": src["fetched_at"],
            }
            for src in conn.execute(
                "SELECT * FROM aggressive_outreach_sources WHERE target_id = ? ORDER BY id ASC",
                (target_id,),
            ).fetchall()
        ]
        target["activity"] = [
            dict(item)
            for item in conn.execute(
                "SELECT agent_name, event_type, detail, created_at FROM aggressive_outreach_activity WHERE target_id = ? ORDER BY id DESC LIMIT 12",
                (target_id,),
            ).fetchall()
        ]
        results.append(target)
    conn.close()
    return results


def update_target_status(target_id: int, status: str, notes: str = "") -> Dict[str, Any]:
    ensure_tables()
    conn = _conn()
    row = conn.execute(
        "SELECT vendor_id FROM aggressive_outreach_targets WHERE id = ?",
        (int(target_id),),
    ).fetchone()
    if not row:
        conn.close()
        raise ValueError("Target not found")
    conn.execute(
        "UPDATE aggressive_outreach_targets SET status = ?, notes = ?, updated_at = datetime('now') WHERE id = ?",
        (status, notes, int(target_id)),
    )
    _log_activity(conn, int(row["vendor_id"]), int(target_id), "operator", "status", f"Status set to {status}")
    conn.commit()
    conn.close()
    return {"ok": True, "target_id": int(target_id), "status": status}


def queue_email_draft(target_id: int, scheduled_date: Optional[str] = None) -> Dict[str, Any]:
    ensure_tables()
    conn = _conn()
    row = conn.execute(
        "SELECT * FROM aggressive_outreach_targets WHERE id = ?",
        (int(target_id),),
    ).fetchone()
    if not row:
        conn.close()
        raise ValueError("Target not found")
    target = dict(row)
    if not target.get("official_email") or not target.get("message_body"):
        conn.close()
        raise ValueError("Target is missing a usable email draft")

    decision = evaluate_vendor_candidate(
        {
            "name": target.get("business_name", ""),
            "category": target.get("category", ""),
            "website": target.get("website", ""),
            "email": target.get("official_email", ""),
            "phone": target.get("official_phone", "") or target.get("vendor_phone", ""),
            "source": "aggressive_outreach",
            "email_valid": 1,
            "website_status": 1,
        }
    )
    if not decision.get("eligible"):
        conn.close()
        raise ValueError(
            "Target email failed queue quality checks: "
            + ", ".join(decision.get("quality_flags", []) or decision.get("reasons", []) or ["email_not_ready"])
        )

    if not scheduled_date:
        scheduled_date = _active_batch_date()

    existing = conn.execute(
        """
        SELECT id FROM email_queue
        WHERE vendor_id = ? AND scheduled_date = ? AND status IN ('queued','edited','approved')
        ORDER BY id DESC LIMIT 1
        """,
        (int(target["vendor_id"]), scheduled_date),
    ).fetchone()

    batch_number = conn.execute(
        "SELECT COALESCE(MAX(batch_number), 0) + 1 FROM email_queue WHERE scheduled_date = ?",
        (scheduled_date,),
    ).fetchone()[0]

    tags = json.dumps(
        {
            "source": "aggressive_outreach",
            "sms_allowed": False,
            "sms_reason": target.get("sms_reason", SMS_BLOCK_REASON),
            "source_url": target.get("source_url", ""),
        }
    )

    html_body = _html_from_plain(target.get("message_body", ""))
    if existing:
        queue_id = int(existing["id"])
        conn.execute(
            """
            UPDATE email_queue
            SET recipient_email = ?, recipient_name = ?, category = ?, subject = ?, body_plain = ?,
                body_html = ?, template_type = 'aggressive_curated', status = 'edited',
                priority_score = ?, tags = ?
            WHERE id = ?
            """,
            (
                target["official_email"],
                target["business_name"],
                target["category"],
                target["message_subject"],
                target["message_body"],
                html_body,
                int(max(0, decision.get("quality_score", 0))),
                tags,
                queue_id,
            ),
        )
    else:
        cur = conn.execute(
            """
            INSERT INTO email_queue (
                vendor_id, recipient_email, recipient_name, category, subject, body_plain,
                body_html, template_type, status, scheduled_date, batch_number, priority_score, tags
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'aggressive_curated', 'edited', ?, ?, ?, ?)
            """,
            (
                int(target["vendor_id"]),
                target["official_email"],
                target["business_name"],
                target["category"],
                target["message_subject"],
                target["message_body"],
                html_body,
                scheduled_date,
                int(batch_number),
                int(max(0, decision.get("quality_score", 0))),
                tags,
            ),
        )
        queue_id = int(cur.lastrowid)

    conn.execute(
        "UPDATE aggressive_outreach_targets SET queue_email_id = ?, status = 'queued_email', updated_at = datetime('now') WHERE id = ?",
        (queue_id, int(target_id)),
    )
    _log_activity(
        conn,
        vendor_id=int(target["vendor_id"]),
        target_id=int(target_id),
        agent_name="operator",
        event_type="queue_email",
        detail=f"Queued curated email draft into email_queue #{queue_id} for {scheduled_date}",
    )
    conn.commit()
    conn.close()
    return {"ok": True, "target_id": int(target_id), "queue_id": queue_id, "scheduled_date": scheduled_date}


def register_aggressive_outreach_routes(app: FastAPI, verify_action_auth=None) -> None:
    write_deps = [Depends(verify_action_auth)] if verify_action_auth else []

    @app.get("/api/aggressive-outreach/summary")
    async def api_aggressive_outreach_summary():
        try:
            return JSONResponse({"ok": True, **get_summary()})
        except Exception as exc:
            log.error("Aggressive outreach summary error: %s", exc)
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.get("/api/aggressive-outreach/targets")
    async def api_aggressive_outreach_targets(status: Optional[str] = None, limit: int = 50):
        try:
            targets = get_targets(status=status, limit=limit)
            return JSONResponse({"ok": True, "count": len(targets), "targets": targets})
        except Exception as exc:
            log.error("Aggressive outreach targets error: %s", exc)
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.post("/api/aggressive-outreach/run", dependencies=write_deps)
    async def api_aggressive_outreach_run(request: Request):
        try:
            body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
            limit = int(body.get("limit") or 15)
            concurrency = int(body.get("concurrency") or 5)
            force_refresh = bool(body.get("force_refresh", False))
            result = await run_research(limit=limit, concurrency=concurrency, force_refresh=force_refresh)
            return JSONResponse(result)
        except Exception as exc:
            log.error("Aggressive outreach run error: %s", exc)
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.post("/api/aggressive-outreach/targets/{target_id}/queue-email", dependencies=write_deps)
    async def api_aggressive_outreach_queue_email(target_id: int, request: Request):
        try:
            body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
            scheduled_date = body.get("scheduled_date")
            result = queue_email_draft(target_id=target_id, scheduled_date=scheduled_date)
            return JSONResponse(result)
        except Exception as exc:
            log.error("Aggressive outreach queue email error: %s", exc)
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.post("/api/aggressive-outreach/targets/{target_id}/status", dependencies=write_deps)
    async def api_aggressive_outreach_update_status(target_id: int, request: Request):
        try:
            body = await request.json()
            status = str(body.get("status") or "researched").strip()
            notes = str(body.get("notes") or "").strip()
            result = update_target_status(target_id=target_id, status=status, notes=notes)
            return JSONResponse(result)
        except Exception as exc:
            log.error("Aggressive outreach update status error: %s", exc)
            return JSONResponse({"error": str(exc)}, status_code=500)
