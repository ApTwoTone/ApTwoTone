"""
Nexus Rate Limiter — Per-recipient + global rate limiting + anomaly detection.

Limits:
- SMS: 1 per recipient per 24 hours
- Email: 3 per recipient per 24 hours
- Cooldown: 1 hour between messages to same recipient on same channel
- Global: 50 messages per hour, 500 per day (all channels)
- Anomaly: Auto-halt if current hour > 3x the 7-day hourly average
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path.home() / ".nexus" / "memory.db"

# Limits
RECIPIENT_LIMITS = {
    "sms": {"daily": 1, "cooldown_minutes": 60},
    "email": {"daily": 3, "cooldown_minutes": 60},
    "facebook_dm": {"daily": 1, "cooldown_minutes": 60},
}
GLOBAL_HOURLY = 50
GLOBAL_DAILY = 500
ANOMALY_MULTIPLIER = 3  # Alert if current hour > 3x average


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_rate_limiter():
    """Create rate limiting tables."""
    conn = _db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rate_limit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT (datetime('now')),
            recipient_hash TEXT,
            channel TEXT,
            result TEXT
        )
    """)
    conn.commit()
    conn.close()


# Ensure tables exist on import
init_rate_limiter()


def _hash_recipient(recipient: str) -> str:
    import hashlib
    return hashlib.sha256(recipient.encode()).hexdigest()


# ── Per-Recipient Rate Limiting ───────────────────────────────────────────────

def check_recipient_limit(recipient: str, channel: str) -> tuple[bool, str]:
    """Check if sending to this recipient is within limits.

    Returns (allowed: bool, reason: str).
    """
    import re
    clean = re.sub(r"[^\w@.\-+]", "", recipient)
    rh = _hash_recipient(clean)
    limits = RECIPIENT_LIMITS.get(channel, {"daily": 1, "cooldown_minutes": 60})
    conn = _db()

    # Check daily limit
    daily_count = conn.execute(
        "SELECT COUNT(*) FROM rate_limit_log WHERE recipient_hash=? AND channel=? "
        "AND result='allowed' AND timestamp > datetime('now', '-24 hours')",
        (rh, channel),
    ).fetchone()[0]

    if daily_count >= limits["daily"]:
        conn.close()
        return False, f"recipient_daily_limit: {daily_count}/{limits['daily']} {channel} in 24h"

    # Check cooldown
    cooldown_mins = limits["cooldown_minutes"]
    recent = conn.execute(
        "SELECT COUNT(*) FROM rate_limit_log WHERE recipient_hash=? AND channel=? "
        "AND result='allowed' AND timestamp > datetime('now', ?)",
        (rh, channel, f"-{cooldown_mins} minutes"),
    ).fetchone()[0]

    conn.close()
    if recent > 0:
        return False, f"recipient_cooldown: message sent within last {cooldown_mins}min"

    return True, "within_limits"


# ── Global Rate Limiting ──────────────────────────────────────────────────────

def check_global_limit(channel: str = None) -> tuple[bool, str]:
    """Check global rate limits (all channels combined).

    Returns (allowed: bool, reason: str).
    """
    conn = _db()

    # Hourly limit
    hourly = conn.execute(
        "SELECT COUNT(*) FROM rate_limit_log WHERE result='allowed' "
        "AND timestamp > datetime('now', '-1 hour')"
    ).fetchone()[0]
    if hourly >= GLOBAL_HOURLY:
        conn.close()
        return False, f"global_hourly_limit: {hourly}/{GLOBAL_HOURLY}"

    # Daily limit
    daily = conn.execute(
        "SELECT COUNT(*) FROM rate_limit_log WHERE result='allowed' "
        "AND timestamp > datetime('now', '-24 hours')"
    ).fetchone()[0]
    if daily >= GLOBAL_DAILY:
        conn.close()
        return False, f"global_daily_limit: {daily}/{GLOBAL_DAILY}"

    conn.close()
    return True, "within_global_limits"


# ── Record Send ───────────────────────────────────────────────────────────────

def record_send(recipient: str, channel: str, result: str = "allowed"):
    """Record a send for rate tracking."""
    import re
    clean = re.sub(r"[^\w@.\-+]", "", recipient)
    rh = _hash_recipient(clean)
    conn = _db()
    conn.execute(
        "INSERT INTO rate_limit_log (recipient_hash, channel, result) VALUES (?,?,?)",
        (rh, channel, result),
    )
    conn.commit()
    conn.close()


# ── Anomaly Detection ─────────────────────────────────────────────────────────

def check_anomaly() -> tuple[bool, str]:
    """Check if current volume is anomalous vs 7-day average.

    Returns (is_anomalous: bool, detail: str).
    """
    conn = _db()

    # Current hour count
    current = conn.execute(
        "SELECT COUNT(*) FROM rate_limit_log WHERE result='allowed' "
        "AND timestamp > datetime('now', '-1 hour')"
    ).fetchone()[0]

    # 7-day hourly average (exclude current hour)
    avg_row = conn.execute(
        "SELECT COUNT(*) * 1.0 / MAX(168, 1) FROM rate_limit_log "
        "WHERE result='allowed' AND timestamp > datetime('now', '-7 days') "
        "AND timestamp <= datetime('now', '-1 hour')"
    ).fetchone()
    conn.close()

    avg = avg_row[0] if avg_row and avg_row[0] else 0
    threshold = max(avg * ANOMALY_MULTIPLIER, 5)  # Minimum threshold of 5

    if current > threshold:
        return True, f"anomaly: {current} sends this hour vs avg {avg:.1f}/hr (threshold {threshold:.0f})"
    return False, f"normal: {current} sends this hour, avg {avg:.1f}/hr"
