"""
Lead Service — CRUD operations for leads with dedup, UUID, phone normalization.
"""
from __future__ import annotations
import json
import logging
import re
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

log = logging.getLogger("lead_service")

DB_PATH = Path.home() / ".nexus" / "memory.db"
SMS_GATEWAY_DOMAINS = {
    "tmomail.net",
    "txt.att.net",
    "vtext.com",
    "messaging.sprintpcs.com",
    "mms.cricketwireless.net",
    "mymetropcs.com",
    "sms.myboostmobile.com",
    "msg.fi.google.com",
    "vmobl.com",
}

# Fields that can be updated via API
UPDATABLE_FIELDS = {
    "first_name", "last_name", "full_name", "email", "phone", "carrier", "source",
    "notes", "status", "next_action_at", "sms_method",
    "ad_id", "ad_set_id", "campaign_id",
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "form_id", "form_name",
    "event_date", "event_start_time", "event_end_time",
    "event_city", "event_address", "guest_count", "event_type",
    "terrain_notes", "power_water_notes",
    "deposit_status", "deposit_amount", "total_quote_amount",
    "booking_status", "business_name",
    "job_title", "venue_location", "website",
    "source_detail", "source_group_name",
    "is_vendor", "vendor_category", "is_venue", "venue_name",
    "joined_referral_program", "referral_partner_type",
    "referrals_given_count", "referrals_booked_count", "referral_payout_total",
    "lead_temperature_override", "last_profile_update",
}


def _fire_brain_feedback(lead_id: int, outcome: str) -> None:
    """
    Background thread: POST lead outcome to /api/brain/feedback.
    Non-blocking — swallows all exceptions so it never disrupts the booking flow.
    outcome: "booked" | "lost"
    """
    import urllib.request
    import urllib.error

    payload = json.dumps({
        "lead_id": lead_id,
        "outcome": outcome,
        "specialist_used": "lead_ranker",
        "model_version": "v1",
    }).encode()
    try:
        req = urllib.request.Request(
            "http://localhost:7860/api/brain/feedback",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as exc:
        # Feedback failure must never surface to callers
        log.debug("brain feedback skipped (lead_id=%s outcome=%s): %s", lead_id, outcome, exc)


class LeadService:
    def __init__(self, db_path=None, timeline_service=None):
        self.db_path = str(db_path or DB_PATH)
        self.timeline = timeline_service

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def create_lead(self, data: dict) -> dict:
        """Create a new lead with dedup, phone normalization, UUID generation."""
        phone = _clean_phone(data.get("phone", ""))
        email = (data.get("email", "") or "").lower().strip()
        incoming_uuid = (data.get("lead_uuid") or "").strip()
        alias_phone = _phone_from_email_alias(email)
        if not phone and alias_phone:
            phone = alias_phone

        # Hard dedup by external lead UUID when provided (e.g. Facebook leadgen_id).
        if incoming_uuid:
            conn = self._conn()
            existing_uuid = conn.execute(
                "SELECT * FROM leads WHERE lead_uuid = ? LIMIT 1",
                (incoming_uuid,),
            ).fetchone()
            conn.close()
            if existing_uuid:
                return {**dict(existing_uuid), "_deduplicated": True}

        # Deduplicate
        existing = self.deduplicate(phone, email)
        if existing:
            # Merge new data into existing lead. Fill blanks by default, and
            # allow trusted sources (FB/web forms) to overwrite placeholder names.
            updates = {}
            incoming_source = (data.get("source", "") or "").strip().lower()
            existing_source = (existing.get("source", "") or "").strip().lower()
            existing_email = (existing.get("email", "") or "").strip().lower()
            existing_phone = _clean_phone(existing.get("phone", ""))
            incoming_email = email or existing_email
            incoming_phone = phone or existing_phone

            for key in UPDATABLE_FIELDS:
                if key not in data:
                    continue
                incoming_val = data.get(key)
                if not _has_value(incoming_val):
                    continue
                existing_val = existing.get(key)
                if not _has_value(existing_val):
                    updates[key] = incoming_val
                    continue

                # Replace low-confidence placeholder names with real names.
                if key in {"first_name", "last_name", "full_name"}:
                    if _is_placeholder_name(str(existing_val), existing_email, existing_phone) and not _is_placeholder_name(
                        str(incoming_val), incoming_email, incoming_phone
                    ):
                        updates[key] = incoming_val
                        continue

                # Prefer higher-confidence source labels over auto-generated ones.
                if key == "source":
                    if _should_upgrade_source(existing_source, incoming_source):
                        updates[key] = incoming_val
                        continue

            if updates:
                self.update_lead(existing["id"], actor="system", **updates)
                existing = self.get_lead(existing["id"])
            return {**existing, "_deduplicated": True}

        first = data.get("first_name", "").strip()
        last = data.get("last_name", "").strip()
        full = f"{first} {last}".strip() or data.get("full_name", "")

        lead_uuid = incoming_uuid or uuid.uuid4().hex
        ghl_id = data.get("ghl_contact_id", "") or lead_uuid  # unique fallback

        conn = self._conn()
        conn.execute(
            """INSERT INTO leads (lead_uuid, ghl_contact_id, first_name, last_name, full_name, email, phone,
               carrier, source, booking_status, status,
               ad_id, ad_set_id, campaign_id,
               utm_source, utm_medium, utm_campaign, utm_content, utm_term,
               form_id, form_name,
               event_date, event_start_time, event_end_time,
               event_city, event_address, guest_count, event_type,
               terrain_notes, power_water_notes, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                lead_uuid, ghl_id, first, last, full, email, phone,
                data.get("carrier", "tmobile"), data.get("source", "manual"),
                "new_lead", "new",
                data.get("ad_id", ""), data.get("ad_set_id", ""), data.get("campaign_id", ""),
                data.get("utm_source", ""), data.get("utm_medium", ""),
                data.get("utm_campaign", ""), data.get("utm_content", ""), data.get("utm_term", ""),
                data.get("form_id", ""), data.get("form_name", ""),
                data.get("event_date", ""), data.get("event_start_time", ""),
                data.get("event_end_time", ""),
                data.get("event_city", ""), data.get("event_address", ""),
                int(data.get("guest_count", 0) or 0), data.get("event_type", ""),
                data.get("terrain_notes", ""), data.get("power_water_notes", ""),
                data.get("notes", ""),
            )
        )
        lead_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()

        if self.timeline:
            self.timeline.log_event(lead_id, "lead_created",
                f"Lead created: {full} ({phone or email})", "system",
                {"source": data.get("source", "manual")})

        return self.get_lead(lead_id)

    def get_lead(self, lead_id: int) -> dict | None:
        conn = self._conn()
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_lead_by_uuid(self, lead_uuid: str) -> dict | None:
        conn = self._conn()
        row = conn.execute("SELECT * FROM leads WHERE lead_uuid = ?", (lead_uuid,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def update_lead(self, lead_id: int, actor: str = "user", **fields) -> dict | None:
        safe = {k: v for k, v in fields.items() if k in UPDATABLE_FIELDS}
        if not safe:
            return self.get_lead(lead_id)

        # Auto-update full_name if first/last changed
        if "first_name" in safe or "last_name" in safe:
            current = self.get_lead(lead_id)
            if current:
                fn = safe.get("first_name", current.get("first_name", ""))
                ln = safe.get("last_name", current.get("last_name", ""))
                safe["full_name"] = f"{fn} {ln}".strip()

        if "phone" in safe:
            safe["phone"] = _clean_phone(safe["phone"])
        if "email" in safe:
            safe["email"] = safe["email"].lower().strip()

        safe["updated_at"] = datetime.utcnow().isoformat(timespec="seconds")

        sets = ", ".join(f"{k} = ?" for k in safe)
        vals = list(safe.values()) + [lead_id]

        conn = self._conn()
        conn.execute(f"UPDATE leads SET {sets} WHERE id = ?", vals)
        conn.commit()
        conn.close()

        # Fire brain feedback when a lead is marked booked or lost
        new_status = safe.get("status")
        if new_status in ("booked", "lost"):
            threading.Thread(
                target=_fire_brain_feedback,
                args=(lead_id, new_status),
                daemon=True,
            ).start()

        if self.timeline:
            changed = ", ".join(f"{k}={v}" for k, v in fields.items() if k in UPDATABLE_FIELDS and k != "updated_at")
            if changed:
                self.timeline.log_event(lead_id, "lead_updated", f"Updated: {changed}", actor)

        return self.get_lead(lead_id)

    def search_leads(self, query: str = "", booking_status: str = "",
                     sort: str = "updated_at", limit: int = 25, offset: int = 0):
        """Search leads with filtering and pagination. Returns (leads, total_count)."""
        conditions = []
        params = []

        if query:
            conditions.append("(full_name LIKE ? OR phone LIKE ? OR email LIKE ? OR event_city LIKE ?)")
            q = f"%{query}%"
            params.extend([q, q, q, q])

        if booking_status:
            conditions.append("booking_status = ?")
            params.append(booking_status)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        # Validate sort column
        allowed_sorts = {"updated_at", "discovered_at", "full_name", "event_date",
                         "last_contacted_at", "booking_status", "deposit_status", "total_quote_amount"}
        if sort.lstrip("-") not in allowed_sorts:
            sort = "updated_at"

        desc = " DESC" if not sort.startswith("-") else ""
        sort_col = sort.lstrip("-")
        if sort_col in ("updated_at", "discovered_at", "last_contacted_at", "event_date"):
            desc = " DESC"  # dates default newest first

        conn = self._conn()
        total = conn.execute(f"SELECT COUNT(*) FROM leads {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM leads {where} ORDER BY {sort_col}{desc} LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()
        conn.close()

        return [dict(r) for r in rows], total

    def add_internal_note(self, lead_id: int, note: str, author: str = "Kai"):
        conn = self._conn()
        row = conn.execute("SELECT internal_notes FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not row:
            conn.close()
            return
        try:
            notes = json.loads(row["internal_notes"] or "[]")
        except:
            notes = []
        notes.append({
            "ts": datetime.utcnow().isoformat(timespec="seconds"),
            "author": author,
            "note": note
        })
        conn.execute("UPDATE leads SET internal_notes = ?, updated_at = ? WHERE id = ?",
                      (json.dumps(notes), datetime.utcnow().isoformat(timespec="seconds"), lead_id))
        conn.commit()
        conn.close()

        if self.timeline:
            self.timeline.log_event(lead_id, "note_added", note, author)

    def deduplicate(self, phone: str, email: str) -> dict | None:
        conn = self._conn()
        alias_phone = _phone_from_email_alias(email)
        if not phone and alias_phone:
            phone = alias_phone
        candidates = []
        seen_ids = set()
        if phone and len(phone) == 10:
            rows = conn.execute(
                "SELECT * FROM leads WHERE phone != '' AND phone IS NOT NULL"
            ).fetchall()
            for row in rows:
                if _clean_phone(row["phone"]) != phone:
                    continue
                rid = int(row["id"])
                if rid in seen_ids:
                    continue
                seen_ids.add(rid)
                candidates.append(dict(row))
        if email:
            rows = conn.execute("SELECT * FROM leads WHERE LOWER(email) = ?", (email.lower(),)).fetchall()
            for row in rows:
                rid = int(row["id"])
                if rid in seen_ids:
                    continue
                seen_ids.add(rid)
                candidates.append(dict(row))
        if candidates:
            conn.close()
            return _pick_best_match(candidates)
        conn.close()
        return None

    def match_phone_to_lead(self, phone: str) -> int | None:
        digits = _clean_phone(phone)
        if not digits:
            return None
        conn = self._conn()
        rows = conn.execute("SELECT * FROM leads WHERE phone != '' AND phone IS NOT NULL").fetchall()
        conn.close()
        matches = [dict(r) for r in rows if _clean_phone(r["phone"]) == digits]
        best = _pick_best_match(matches)
        return int(best["id"]) if best else None

    def match_email_to_lead(self, email: str) -> int | None:
        if not email:
            return None
        alias_phone = _phone_from_email_alias(email)
        if alias_phone:
            by_phone = self.match_phone_to_lead(alias_phone)
            if by_phone:
                return by_phone
        conn = self._conn()
        rows = conn.execute("SELECT * FROM leads WHERE LOWER(email) = ?", (email.lower().strip(),)).fetchall()
        conn.close()
        best = _pick_best_match([dict(r) for r in rows])
        return int(best["id"]) if best else None

    def get_stats(self) -> dict:
        conn = self._conn()
        rows = conn.execute("SELECT booking_status, COUNT(*) FROM leads GROUP BY booking_status").fetchall()
        by_stage = {r[0]: r[1] for r in rows}
        total = sum(by_stage.values())

        upcoming = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE event_date != '' AND event_date >= date('now') AND booking_status IN ('booked','deposit_requested')"
        ).fetchone()[0]

        deposits_pending = by_stage.get("deposit_requested", 0)

        new_24h = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE discovered_at >= datetime('now', '-24 hours')"
        ).fetchone()[0]

        conn.close()
        return {
            "total": total,
            "by_stage": by_stage,
            "upcoming_events": upcoming,
            "deposits_pending": deposits_pending,
            "new_24h": new_24h,
        }


def _clean_phone(p: str) -> str:
    d = re.sub(r'\D', '', str(p or ""))
    if len(d) == 11 and d[0] == "1":
        d = d[1:]
    return d


def _has_value(v) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, tuple, dict, set)):
        return len(v) > 0
    return True


def _email_local(email: str) -> str:
    s = (email or "").strip().lower()
    if "@" not in s:
        return ""
    return s.split("@", 1)[0]


def _phone_from_email_alias(email: str) -> str:
    s = (email or "").strip().lower()
    if "@" not in s:
        return ""
    local, domain = s.split("@", 1)
    if domain not in SMS_GATEWAY_DOMAINS:
        return ""
    digits = re.sub(r"\D", "", local)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else ""


def _is_sms_gateway_email(email: str) -> bool:
    s = (email or "").strip().lower()
    if "@" not in s:
        return False
    _, domain = s.split("@", 1)
    return domain in SMS_GATEWAY_DOMAINS


def _source_rank(source: str) -> int:
    s = (source or "").strip().lower()
    if not s:
        return 0
    if s.startswith("facebook"):
        return 7
    if s.startswith("website"):
        return 6
    if s.startswith("referral"):
        return 6
    if s.startswith("ghl") or s.startswith("api"):
        return 5
    if s.startswith("manual"):
        return 4
    if s.startswith("google_voice") or s.startswith("sms"):
        return 3
    if s.startswith("email"):
        return 2
    return 1


def _match_sort_key(row: dict) -> tuple:
    phone = _clean_phone(row.get("phone", ""))
    email = (row.get("email", "") or "").strip().lower()
    has_phone = bool(phone)
    has_email = bool(email)
    has_non_gateway_email = has_email and not _is_sms_gateway_email(email)
    name = " ".join((row.get("full_name", "") or "").split()).strip() or (
        f"{row.get('first_name', '')} {row.get('last_name', '')}".strip()
    )
    has_real_name = bool(name and not _is_placeholder_name(name, email, phone))
    source_rank = _source_rank(row.get("source", ""))
    rid = int(row.get("id") or 0)
    return (
        1 if has_real_name else 0,
        1 if has_phone else 0,
        1 if has_non_gateway_email else 0,
        1 if has_email else 0,
        source_rank,
        -rid if rid else 0,  # prefer older canonical rows when tied
    )


def _pick_best_match(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    return max(rows, key=_match_sort_key)


def _is_placeholder_name(name: str, email: str = "", phone: str = "") -> bool:
    n = " ".join((name or "").strip().split()).lower()
    if not n:
        return True
    if n in {"unknown", "lead", "contact", "n/a", "na", "none", "null", "test"}:
        return True
    if re.fullmatch(r"[\d\W_]+", n):
        return True
    local = _email_local(email)
    if local and n == local:
        return True
    digits = _clean_phone(phone)
    if digits and n == digits:
        return True
    return False


def _should_upgrade_source(existing_source: str, incoming_source: str) -> bool:
    existing = (existing_source or "").strip().lower()
    incoming = (incoming_source or "").strip().lower()
    if not incoming:
        return False
    if not existing:
        return True

    low_confidence = {
        "manual",
        "email_inbound",
        "email_outbound",
        "google_voice_inbound",
        "google_voice",
        "sms_inbound",
    }
    high_confidence_prefixes = ("facebook", "website", "referral", "ghl", "api")

    if existing in low_confidence and incoming not in low_confidence:
        return True
    if existing.startswith("email_") and incoming.startswith("facebook"):
        return True
    if existing.startswith("google_voice") and incoming.startswith("facebook"):
        return True
    if any(incoming.startswith(p) for p in high_confidence_prefixes) and not any(
        existing.startswith(p) for p in high_confidence_prefixes
    ):
        return True
    return False
