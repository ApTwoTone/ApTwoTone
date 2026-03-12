"""
Nexus TCPA Compliance Module

Telephone Consumer Protection Act (TCPA) enforcement:
- Consent tracking (express written, inquiry-based, transactional)
- Quiet hours (8 AM - 9 PM recipient local time)
- Opt-out processing (STOP, QUIT, END, CANCEL, UNSUBSCRIBE, OPT OUT, REVOKE)
- Message content validation (business name + opt-out instructions)

TCPA violations: $500/message ($1,500 willful). No cap.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path.home() / ".nexus" / "memory.db"

# Opt-out keywords per FCC 2025 rules
OPT_OUT_KEYWORDS = {"STOP", "QUIT", "END", "REVOKE", "OPT OUT", "CANCEL", "UNSUBSCRIBE"}

BUSINESS_NAME = "Zoar Bathroom Rentals"
OPT_OUT_INSTRUCTION = "Reply STOP to unsubscribe"


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_tcpa():
    """Create TCPA tables."""
    conn = _db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS consent_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            phone TEXT,
            email TEXT,
            consent_type TEXT,
            status TEXT DEFAULT 'active',
            consented_at TEXT,
            consent_source TEXT,
            consent_ip TEXT,
            consent_language TEXT,
            revoked_at TEXT,
            revocation_keyword TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS opt_out_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT (datetime('now')),
            phone TEXT,
            keyword TEXT,
            channel TEXT,
            confirmation_sent INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


# Ensure tables exist on import
init_tcpa()


# ── Consent Management ────────────────────────────────────────────────────────

def record_consent(lead_id: int, phone: str = "", email: str = "",
                   consent_type: str = "inquiry_based",
                   source: str = "facebook_lead_form",
                   ip: str = "", language: str = "en") -> int:
    """Record consent for a lead. Returns consent_log ID."""
    conn = _db()
    cur = conn.execute(
        "INSERT INTO consent_log (lead_id, phone, email, consent_type, status, "
        "consented_at, consent_source, consent_ip, consent_language) "
        "VALUES (?,?,?,?,'active',?,?,?,?)",
        (lead_id, phone, email, consent_type,
         datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
         source, ip, language),
    )
    conn.commit()
    consent_id = cur.lastrowid
    conn.close()
    return consent_id


def check_consent(lead_id: int) -> dict:
    """Check if a lead has active consent."""
    conn = _db()
    row = conn.execute(
        "SELECT * FROM consent_log WHERE lead_id=? AND status='active' "
        "ORDER BY id DESC LIMIT 1",
        (lead_id,),
    ).fetchone()
    conn.close()
    if not row:
        return {"has_consent": False, "reason": "no_consent_recorded"}
    return {
        "has_consent": True,
        "consent_type": row["consent_type"],
        "source": row["consent_source"],
        "consented_at": row["consented_at"],
    }


# ── Quiet Hours ───────────────────────────────────────────────────────────────

def check_quiet_hours(phone: str) -> bool:
    """Check if it's quiet hours for the recipient. Returns True if BLOCKED.

    TCPA: No calls/texts before 8 AM or after 9 PM recipient local time.
    Uses phonenumbers library for timezone lookup, falls back to US/Pacific.
    """
    try:
        import phonenumbers
        from phonenumbers import timezone as pn_timezone
        from zoneinfo import ZoneInfo

        # Parse phone number
        clean = re.sub(r"\D", "", phone)
        if not clean.startswith("1"):
            clean = "1" + clean
        parsed = phonenumbers.parse(f"+{clean}", "US")
        zones = pn_timezone.time_zones_for_number(parsed)
        if zones:
            tz = ZoneInfo(str(zones[0]))
        else:
            tz = ZoneInfo("America/Los_Angeles")

        local_now = datetime.now(tz)
        return local_now.hour < 8 or local_now.hour >= 21
    except ImportError:
        # phonenumbers not installed — use LA timezone as conservative default
        try:
            from zoneinfo import ZoneInfo
            la = ZoneInfo("America/Los_Angeles")
            now = datetime.now(la)
            return now.hour < 8 or now.hour >= 21
        except Exception:
            return False  # Don't block if we can't determine
    except Exception:
        return False


# ── Opt-Out Processing ────────────────────────────────────────────────────────

def check_opt_out(phone: str) -> bool:
    """Check if a phone number has opted out. Returns True if BLOCKED."""
    clean = re.sub(r"\D", "", phone)
    conn = _db()
    row = conn.execute(
        "SELECT id FROM opt_out_log WHERE phone=? OR phone=?",
        (clean, phone),
    ).fetchone()
    conn.close()
    return row is not None


def is_opt_out_keyword(text: str) -> bool:
    """Check if a message contains an opt-out keyword."""
    upper = text.strip().upper()
    return upper in OPT_OUT_KEYWORDS


def process_opt_out(phone: str, keyword: str, channel: str = "sms") -> dict:
    """Process an opt-out request.

    Records the opt-out and revokes consent.
    Returns {"processed": True, "confirmation": str} for the ONE allowed confirmation.
    """
    clean = re.sub(r"\D", "", phone)

    conn = _db()
    # Record opt-out
    conn.execute(
        "INSERT INTO opt_out_log (phone, keyword, channel) VALUES (?,?,?)",
        (clean, keyword, channel),
    )
    # Revoke all active consent for this phone
    conn.execute(
        "UPDATE consent_log SET status='revoked', revoked_at=?, revocation_keyword=? "
        "WHERE (phone=? OR phone=?) AND status='active'",
        (datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), keyword, clean, phone),
    )
    conn.commit()
    conn.close()

    # ONE confirmation message is allowed per FCC rules (within 5 minutes, no marketing)
    confirmation = (
        f"You've been unsubscribed from {BUSINESS_NAME} messages. "
        f"You will not receive any further texts. "
        f"Questions? Call (424) 235-8979."
    )
    return {"processed": True, "confirmation": confirmation}


# ── Message Content Validation ────────────────────────────────────────────────

def validate_message_content(message: str) -> tuple[bool, list[str]]:
    """Validate that a message includes required TCPA elements.

    Returns (valid: bool, issues: list[str]).
    """
    issues = []
    # Must include business name
    if BUSINESS_NAME.lower() not in message.lower() and "zoar" not in message.lower():
        issues.append("Missing business name")
    # Must include opt-out instructions for marketing messages
    opt_out_present = any(
        kw.lower() in message.lower()
        for kw in ["stop", "unsubscribe", "opt out", "reply stop"]
    )
    if not opt_out_present:
        issues.append("Missing opt-out instructions (e.g., 'Reply STOP to unsubscribe')")
    return len(issues) == 0, issues
