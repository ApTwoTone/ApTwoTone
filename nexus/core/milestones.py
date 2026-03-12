"""
Nexus Milestone Tracker — Sends Telegram notifications at key achievements.

Tracks vendor counts, outreach stats, bookings, and system milestones.
Only fires once per milestone (persisted to disk).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("milestones")

STATE_PATH = Path.home() / ".nexus" / "milestones_state.json"

# ── Milestone Definitions ─────────────────────────────────────────────────────

VENDOR_MILESTONES = [50, 100, 250, 500, 1000, 2500, 5000, 7500, 10000, 15000, 20000, 25000, 30000]
OUTREACH_MILESTONES = [10, 25, 50, 100, 250, 500, 1000]
BOOKING_MILESTONES = [1, 2, 3, 5, 10, 15, 20, 25]

MILESTONE_MESSAGES = {
    # Vendor milestones
    "vendors_50": "First 50 vendors in the database! Research engine warming up.",
    "vendors_100": "100 vendors! The referral network is growing.",
    "vendors_250": "250 vendors discovered. Geographic coverage expanding.",
    "vendors_500": "500 vendors! Half a thousand referral partners identified.",
    "vendors_1000": "1,000 vendors! Research daemon is crushing it.",
    "vendors_2500": "2,500 vendors. Major milestone — network effects kicking in.",
    "vendors_5000": "5,000 vendors! One sixth of the way to 30K.",
    "vendors_7500": "7,500 vendors. Halfway to the 30-day target.",
    "vendors_10000": "10,000 vendors! Two thirds of the 30-day target.",
    "vendors_15000": "15,000 VENDORS! 30-day target ACHIEVED!",
    "vendors_20000": "20,000 vendors. On track for the 60-day target.",
    "vendors_25000": "25,000 vendors. Almost at 30K!",
    "vendors_30000": "30,000 VENDORS! 60-day target COMPLETE!",
    # Outreach milestones
    "outreach_10": "First 10 outreach messages drafted!",
    "outreach_25": "25 partnership messages sent.",
    "outreach_50": "50 outreach messages! Response rate looking good.",
    "outreach_100": "100 outreach messages. Partnership pipeline growing.",
    "outreach_250": "250 messages sent. Building serious referral momentum.",
    "outreach_500": "500 outreach messages! Major outreach milestone.",
    "outreach_1000": "1,000 outreach messages! The network is massive.",
    # Booking milestones
    "booking_1": "FIRST BOOKING! The system is generating revenue!",
    "booking_2": "Second booking confirmed!",
    "booking_3": "Third booking! Momentum building.",
    "booking_5": "5 bookings! The pipeline is working.",
    "booking_10": "10 bookings! Double digits!",
    "booking_15": "15 bookings — on pace for the 90-day goal.",
    "booking_20": "20 bookings! The 90-day goal is within reach.",
    "booking_25": "25 bookings! Goal exceeded!",
}


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {"fired": {}, "last_check": None}


def _save_state(state: dict):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str))


async def check_milestones() -> list:
    """Check current stats against milestone thresholds and fire notifications.

    Returns list of milestone keys that were triggered this check.
    """
    import sqlite3

    db = Path.home() / ".nexus" / "memory.db"
    if not db.exists():
        return []

    state = _load_state()
    fired = state.get("fired", {})
    triggered = []

    try:
        conn = sqlite3.connect(str(db))

        # Vendor count
        vendor_count = 0
        try:
            row = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()
            vendor_count = row[0] if row else 0
        except Exception:
            pass

        # Outreach count
        outreach_count = 0
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM vendor_outreach WHERE status IN ('sent', 'approved')"
            ).fetchone()
            outreach_count = row[0] if row else 0
        except Exception:
            pass

        # Booking count
        booking_count = 0
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE status = 'booked'"
            ).fetchone()
            booking_count = row[0] if row else 0
        except Exception:
            pass

        conn.close()

        # Check vendor milestones
        for threshold in VENDOR_MILESTONES:
            key = f"vendors_{threshold}"
            if vendor_count >= threshold and key not in fired:
                fired[key] = datetime.now(timezone.utc).isoformat()
                triggered.append(key)

        # Check outreach milestones
        for threshold in OUTREACH_MILESTONES:
            key = f"outreach_{threshold}"
            if outreach_count >= threshold and key not in fired:
                fired[key] = datetime.now(timezone.utc).isoformat()
                triggered.append(key)

        # Check booking milestones
        for threshold in BOOKING_MILESTONES:
            key = f"booking_{threshold}"
            if booking_count >= threshold and key not in fired:
                fired[key] = datetime.now(timezone.utc).isoformat()
                triggered.append(key)

        # Fire Telegram notifications for new milestones
        if triggered:
            await _send_milestone_notifications(triggered, vendor_count, outreach_count, booking_count)

        state["fired"] = fired
        state["last_check"] = datetime.now(timezone.utc).isoformat()
        state["counts"] = {
            "vendors": vendor_count,
            "outreach": outreach_count,
            "bookings": booking_count,
        }
        _save_state(state)

    except Exception as e:
        log.warning("Milestone check failed: %s", e)

    return triggered


async def _send_milestone_notifications(
    triggered: list, vendor_count: int, outreach_count: int, booking_count: int
):
    """Send Telegram notification for each triggered milestone."""
    try:
        from telegram.bot import get_bot
        bot = get_bot()
        if not bot:
            return

        for key in triggered:
            message = MILESTONE_MESSAGES.get(key, f"Milestone reached: {key}")
            is_major = any(k in key for k in ["1000", "5000", "10000", "15000", "30000", "booking_1"])

            text = (
                f"{'🏆' if is_major else '📊'} *MILESTONE: {key.replace('_', ' ').upper()}*\n\n"
                f"{message}\n\n"
                f"Current counts:\n"
                f"  Vendors: {vendor_count:,}\n"
                f"  Outreach sent: {outreach_count:,}\n"
                f"  Bookings: {booking_count}"
            )

            for chat_id in bot.allowed_chat_ids:
                await bot.send(chat_id, text)
                log.info("Milestone notification sent: %s", key)

    except Exception as e:
        log.warning("Failed to send milestone notification: %s", e)


async def send_hourly_progress():
    """Send hourly progress update to Telegram."""
    import sqlite3

    db = Path.home() / ".nexus" / "memory.db"
    if not db.exists():
        return

    try:
        conn = sqlite3.connect(str(db))

        # Get counts
        vendor_count = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
        new_today = 0
        try:
            new_today = conn.execute(
                "SELECT COUNT(*) FROM vendors WHERE DATE(created_at) = DATE('now')"
            ).fetchone()[0]
        except Exception:
            pass

        outreach_sent = 0
        try:
            outreach_sent = conn.execute(
                "SELECT COUNT(*) FROM vendor_outreach WHERE status = 'sent'"
            ).fetchone()[0]
        except Exception:
            pass

        conn.close()

        # Calculate progress toward 30K target
        pct = round((vendor_count / 30000) * 100, 1)
        bar_filled = int(pct / 5)
        bar = "█" * bar_filled + "░" * (20 - bar_filled)

        from telegram.bot import get_bot
        bot = get_bot()
        if not bot:
            return

        text = (
            f"📈 *Hourly Progress*\n\n"
            f"Vendors: *{vendor_count:,}* / 30,000\n"
            f"[{bar}] {pct}%\n"
            f"New today: +{new_today}\n"
            f"Outreach sent: {outreach_sent}"
        )

        for chat_id in bot.allowed_chat_ids:
            await bot.send(chat_id, text)

    except Exception as e:
        log.warning("Hourly progress update failed: %s", e)


def get_milestone_status() -> dict:
    """Get current milestone status for API endpoint."""
    state = _load_state()
    return {
        "fired": state.get("fired", {}),
        "total_milestones_hit": len(state.get("fired", {})),
        "last_check": state.get("last_check"),
        "counts": state.get("counts", {}),
        "next_milestones": _get_next_milestones(state),
    }


def _get_next_milestones(state: dict) -> dict:
    """Get the next upcoming milestone for each category."""
    counts = state.get("counts", {})
    fired = state.get("fired", {})
    result = {}

    vendor_count = counts.get("vendors", 0)
    for t in VENDOR_MILESTONES:
        if f"vendors_{t}" not in fired:
            result["vendors"] = {"target": t, "current": vendor_count, "remaining": t - vendor_count}
            break

    outreach_count = counts.get("outreach", 0)
    for t in OUTREACH_MILESTONES:
        if f"outreach_{t}" not in fired:
            result["outreach"] = {"target": t, "current": outreach_count, "remaining": t - outreach_count}
            break

    booking_count = counts.get("bookings", 0)
    for t in BOOKING_MILESTONES:
        if f"booking_{t}" not in fired:
            result["bookings"] = {"target": t, "current": booking_count, "remaining": t - booking_count}
            break

    return result
