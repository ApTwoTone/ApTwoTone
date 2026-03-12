"""
Email Outreach Helpers — Deliverability, validation, rate limiting, logging.

These are additive helper functions that improve email deliverability.
The actual send_email function lives in integrations/zoar_bot.py.
These helpers should be called before sending to validate and rate-limit.

Usage:
    from core.email_outreach import validate_email_address, check_send_limits, log_send_attempt
"""
import re
import sqlite3
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from email.utils import make_msgid

log = logging.getLogger("email_outreach")

DB_PATH = Path.home() / ".nexus" / "memory.db"
SEND_LOG = Path.home() / ".nexus" / "email_send_log.txt"
INVALID_LOG = Path.home() / ".nexus" / "invalid_emails.log"

# Max sends per day
MAX_DAILY_SENDS = 50
# Min seconds between sends
MIN_SEND_INTERVAL = 3
# Min days between emails to same vendor
VENDOR_COOLDOWN_DAYS = 30

# Simple email regex
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

# Known disposable email domains
_DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "tempmail.com", "throwaway.email",
    "yopmail.com", "sharklasers.com", "guerrillamailblock.com", "grr.la",
    "dispostable.com", "10minutemail.com", "trashmail.com", "fakeinbox.com",
    "temp-mail.org", "getnada.com", "maildrop.cc",
}

# Track last send time for rate limiting
_last_send_time = 0.0


def validate_email_address(email: str) -> dict:
    """
    Validate an email address for deliverability.

    Returns {"valid": True/False, "reason": str}
    """
    if not email or not isinstance(email, str):
        return {"valid": False, "reason": "Empty or non-string email"}

    email = email.strip().lower()

    # Regex check
    if not _EMAIL_RE.match(email):
        _log_invalid(email, "Failed regex validation")
        return {"valid": False, "reason": "Invalid email format"}

    # Domain extraction
    domain = email.split("@")[1]

    # Disposable domain check
    if domain in _DISPOSABLE_DOMAINS:
        _log_invalid(email, f"Disposable domain: {domain}")
        return {"valid": False, "reason": f"Disposable email domain: {domain}"}

    # MX record check
    try:
        import dns.resolver
        answers = dns.resolver.resolve(domain, "MX")
        if not answers:
            _log_invalid(email, "No MX records")
            return {"valid": False, "reason": "Domain has no MX records"}
    except ImportError:
        # dnspython not installed — skip MX check
        log.debug("dnspython not installed, skipping MX check for %s", email)
    except Exception as e:
        _log_invalid(email, f"MX lookup failed: {e}")
        return {"valid": False, "reason": f"MX lookup failed: {str(e)[:100]}"}

    return {"valid": True, "reason": "ok"}


def check_send_limits(vendor_id: int) -> dict:
    """
    Check if we can send to this vendor right now.

    Returns {"can_send": True/False, "reason": str}
    """
    global _last_send_time

    # Rate limit: min interval between sends
    now = time.time()
    elapsed = now - _last_send_time
    if elapsed < MIN_SEND_INTERVAL:
        return {"can_send": False, "reason": f"Rate limit: {MIN_SEND_INTERVAL - elapsed:.1f}s remaining"}

    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()

        # Daily limit check
        today = datetime.now().strftime("%Y-%m-%d")
        c.execute(
            "SELECT COUNT(*) FROM vendor_outreach WHERE created_at LIKE ?",
            (f"{today}%",)
        )
        daily_count = c.fetchone()[0]
        if daily_count >= MAX_DAILY_SENDS:
            conn.close()
            return {"can_send": False, "reason": f"Daily limit reached: {daily_count}/{MAX_DAILY_SENDS}"}

        # Vendor cooldown check
        cutoff = (datetime.now() - timedelta(days=VENDOR_COOLDOWN_DAYS)).isoformat()
        c.execute(
            "SELECT COUNT(*) FROM vendor_outreach WHERE vendor_id = ? AND created_at > ?",
            (vendor_id, cutoff)
        )
        recent = c.fetchone()[0]
        if recent > 0:
            conn.close()
            return {"can_send": False, "reason": f"Vendor cooldown: emailed within last {VENDOR_COOLDOWN_DAYS} days"}

        conn.close()
    except Exception as e:
        log.warning("Error checking send limits: %s", e)

    return {"can_send": True, "reason": "ok"}


def check_unsubscribe(vendor_id: int) -> bool:
    """Check if vendor has unsubscribed. Returns True if unsubscribed (should NOT send)."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute("SELECT unsubscribed FROM vendors WHERE id = ?", (vendor_id,))
        row = c.fetchone()
        conn.close()
        if row and row[0]:
            return True
    except Exception as e:
        log.warning("Error checking unsubscribe for vendor %d: %s", vendor_id, e)
    return False


def generate_email_headers(vendor_id: int) -> dict:
    """
    Generate additional email headers for better deliverability.

    Returns dict of header name -> value to add to outgoing emails.
    """
    return {
        "X-Mailer": "Zoar Bathroom Rentals CRM 1.0",
        "Message-ID": make_msgid(domain="zoarbathroomrental.com"),
        "List-Unsubscribe": f"<mailto:zoarbathrooms@gmail.com?subject=unsubscribe&body=vendor_id={vendor_id}>",
    }


def mark_send_time():
    """Record that a send just happened (for rate limiting)."""
    global _last_send_time
    _last_send_time = time.time()


def log_send_attempt(vendor_id: int, vendor_name: str, email: str,
                     subject: str, status: str, error: str = ""):
    """Log every send attempt to email_send_log.txt."""
    timestamp = datetime.now().isoformat()
    line = f"{timestamp} | {vendor_id} | {vendor_name} | {email} | {subject} | {status}"
    if error:
        line += f" | ERROR: {error}"
    line += "\n"

    try:
        SEND_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(SEND_LOG, "a") as f:
            f.write(line)
    except Exception as e:
        log.error("Failed to write send log: %s", e)


def _log_invalid(email: str, reason: str):
    """Log invalid email to invalid_emails.log."""
    timestamp = datetime.now().isoformat()
    try:
        INVALID_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(INVALID_LOG, "a") as f:
            f.write(f"{timestamp} | {email} | {reason}\n")
    except Exception as e:
        log.error("Failed to write invalid email log: %s", e)


def pre_send_check(vendor_id: int, email: str) -> dict:
    """
    Combined pre-send validation. Call this before every email send.

    Returns {"ok": True/False, "reason": str}
    """
    # 1. Unsubscribe check
    if check_unsubscribe(vendor_id):
        return {"ok": False, "reason": "Vendor has unsubscribed"}

    # 2. Email validation
    validation = validate_email_address(email)
    if not validation["valid"]:
        return {"ok": False, "reason": f"Invalid email: {validation['reason']}"}

    # 3. Send limits
    limits = check_send_limits(vendor_id)
    if not limits["can_send"]:
        return {"ok": False, "reason": limits["reason"]}

    return {"ok": True, "reason": "All checks passed"}
