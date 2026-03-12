from __future__ import annotations

import hashlib
import html
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DB_PATH = Path.home() / ".nexus" / "memory.db"

ALLOWED_CATEGORIES = {"lead", "booking"}

CATEGORY_COOLDOWNS = {
    "lead": 3 * 60 * 60,
    "booking": 12 * 60 * 60,
    "other": 6 * 60 * 60,
}

BOOKING_TERMS = (
    "booking",
    "booked",
    "deposit",
    "availability",
    "calendar",
)

LEAD_TERMS = (
    "new lead",
    "lead replied",
    "lead going cold",
    "new event lead",
    "quote ready",
    "follow-up",
    "follow up",
    "approval #",
    "quote #",
    "lead #",
    "reply from",
    "customer",
)

OTHER_TERMS = (
    "vendor replied",
    "vendor",
    "discovery cycle",
    "new vendors",
    "outbound monitor",
    "watchdog",
    "task complete",
    "spawning ",
    "bug hunter",
    "open bugs",
    "critical bugs",
    "digest",
    "system health",
    "daily briefing",
    "morning report",
    "safety report",
    "pre-launch",
    "campaign",
    "ad alert",
    "sender reputation",
    "server not responding",
    "all ai providers unreachable",
    "resource pressure",
)


@dataclass(frozen=True)
class NotificationDecision:
    allowed: bool
    category: str
    reason: str
    signature: str


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS telegram_notification_guard (
            signature TEXT PRIMARY KEY,
            recipient TEXT DEFAULT '',
            category TEXT DEFAULT 'other',
            source TEXT DEFAULT '',
            preview TEXT DEFAULT '',
            last_seen_ts INTEGER DEFAULT 0,
            last_sent_ts INTEGER DEFAULT 0,
            sent_count INTEGER DEFAULT 0,
            suppressed_count INTEGER DEFAULT 0,
            last_reason TEXT DEFAULT ''
        )
        """
    )
    return conn


def _match_text(text: str) -> str:
    text = html.unescape(str(text or ""))
    text = text.replace("\r", "\n")
    text = re.sub(r"\*|`|_", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def normalize_notification(text: str) -> str:
    normalized = _match_text(text)
    normalized = re.sub(r"https?://\S+", "<url>", normalized)
    normalized = re.sub(
        r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm|pt|pst|pdt|utc)?\b",
        "<time>",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"\b20\d{2}-\d{2}-\d{2}\b", "<date>", normalized)
    normalized = re.sub(r"\b\d{4,}\b", "<num>", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def infer_category(text: str, explicit_category: Optional[str] = None) -> str:
    if explicit_category:
        category = str(explicit_category).strip().lower()
        if category in {"lead", "booking", "other"}:
            return category

    haystack = _match_text(text)
    if not haystack:
        return "other"

    if any(term in haystack for term in OTHER_TERMS):
        if any(term in haystack for term in BOOKING_TERMS):
            return "booking"
        if any(term in haystack for term in LEAD_TERMS) and "vendor" not in haystack:
            return "lead"
        return "other"

    if any(term in haystack for term in BOOKING_TERMS):
        return "booking"

    if "vendor" in haystack:
        return "other"

    if any(term in haystack for term in LEAD_TERMS):
        return "lead"

    return "other"


def should_send_notification(
    text: str,
    *,
    source: str = "unknown",
    category: Optional[str] = None,
    force: bool = False,
    recipient: Optional[str] = None,
) -> NotificationDecision:
    derived_category = infer_category(text, explicit_category=category)
    normalized = normalize_notification(text)
    recipient_key = str(recipient or "*")
    signature = hashlib.sha256(
        f"{recipient_key}|{derived_category}|{normalized}".encode("utf-8")
    ).hexdigest()
    now_ts = int(time.time())

    allowed = force or derived_category in ALLOWED_CATEGORIES
    reason = "forced" if force else ("category_allowed" if allowed else f"blocked_category:{derived_category}")

    conn = _db()
    try:
        row = conn.execute(
            "SELECT last_sent_ts, sent_count, suppressed_count FROM telegram_notification_guard WHERE signature = ?",
            (signature,),
        ).fetchone()
        last_sent_ts = int(row["last_sent_ts"]) if row and row["last_sent_ts"] else 0
        cooldown = CATEGORY_COOLDOWNS.get(derived_category, CATEGORY_COOLDOWNS["other"])
        if allowed and last_sent_ts and (now_ts - last_sent_ts) < cooldown:
            allowed = False
            reason = f"duplicate_within_{cooldown}s"

        conn.execute(
            """
            INSERT INTO telegram_notification_guard (
                signature, recipient, category, source, preview,
                last_seen_ts, last_sent_ts, sent_count, suppressed_count, last_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(signature) DO UPDATE SET
                recipient=excluded.recipient,
                category=excluded.category,
                source=excluded.source,
                preview=excluded.preview,
                last_seen_ts=excluded.last_seen_ts,
                last_sent_ts=CASE
                    WHEN excluded.last_sent_ts > 0 THEN excluded.last_sent_ts
                    ELSE telegram_notification_guard.last_sent_ts
                END,
                sent_count=telegram_notification_guard.sent_count + excluded.sent_count,
                suppressed_count=telegram_notification_guard.suppressed_count + excluded.suppressed_count,
                last_reason=excluded.last_reason
            """,
            (
                signature,
                recipient_key,
                derived_category,
                source[:160],
                str(text or "").strip()[:200],
                now_ts,
                now_ts if allowed else 0,
                1 if allowed else 0,
                0 if allowed else 1,
                reason[:120],
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return NotificationDecision(
        allowed=allowed,
        category=derived_category,
        reason=reason,
        signature=signature,
    )
