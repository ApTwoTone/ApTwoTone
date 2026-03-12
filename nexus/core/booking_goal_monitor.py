"""
Booking Goal Monitor — Enforces CLAUDE.md Rule #1.

Rule #1: "Get Zoar booked at least once every 5 days."

This module runs every hour (via launchd com.zoar.booking-goal-monitor).
- Day 5 since last booking: warning alert + top leads needing action
- Day 6+: urgent alert, escalating every day

Also exposes get_booking_goal_status() for use by morning_report.py.

Usage:
    python -m core.booking_goal_monitor          # run check once
    python -m core.booking_goal_monitor --check  # dry-run (print, don't send)
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from core.telegram_policy import should_send_notification

log = logging.getLogger("booking_goal_monitor")
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

DB_PATH = Path.home() / ".nexus" / "memory.db"
NEXUS_DIR = Path.home() / ".nexus"
GOAL_DAYS = 5
TELEGRAM_CHAT_ID = "8540603351"


def _db():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def days_since_last_booking() -> int:
    """Return integer days since the most recent confirmed booking. 9999 if never booked."""
    conn = _db()
    try:
        # Check bookings table first (most reliable)
        row = conn.execute(
            "SELECT MAX(updated_at) as last_ts FROM bookings WHERE status IN ('confirmed','deposit_paid','completed')"
        ).fetchone()
        last_ts = row["last_ts"] if row else None

        if not last_ts:
            # Fall back to leads table
            row = conn.execute(
                "SELECT MAX(updated_at) as last_ts FROM leads WHERE status='booked' OR booking_status='booked'"
            ).fetchone()
            last_ts = row["last_ts"] if row else None

        if not last_ts:
            return 9999

        last_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(0, (now - last_dt).days)
    except Exception as e:
        log.debug("days_since_last_booking error: %s", e)
        return 9999
    finally:
        conn.close()


def get_actionable_leads(limit: int = 3) -> list:
    """Return top leads most likely to convert right now."""
    conn = _db()
    try:
        rows = conn.execute(
            """
            SELECT id, full_name, phone, event_type, event_date, event_city,
                   booking_status, total_quote_amount, last_contacted_at, updated_at
            FROM leads
            WHERE booking_status NOT IN ('booked','completed','lost','cancelled','')
              AND (requires_manual_approval = 0 OR requires_manual_approval IS NULL)
            ORDER BY
              CASE booking_status
                WHEN 'qualifying' THEN 1
                WHEN 'auto_contacted' THEN 2
                WHEN 'new_lead' THEN 3
                ELSE 4
              END,
              updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        log.debug("get_actionable_leads error: %s", e)
        return []
    finally:
        conn.close()


def get_booking_goal_status() -> dict:
    """
    Returns booking goal data for use in morning_report.py and other summaries.
    """
    days = days_since_last_booking()
    leads = get_actionable_leads(limit=3)

    status = "on_track" if days < GOAL_DAYS else ("warning" if days == GOAL_DAYS else "overdue")
    return {
        "days_since_last_booking": days,
        "goal_days": GOAL_DAYS,
        "status": status,
        "top_leads": leads,
        "leads_needing_action": len(leads),
    }


def _send_telegram(message: str) -> None:
    import urllib.request
    import urllib.parse

    cfg_path = NEXUS_DIR / "config.json"
    try:
        cfg = json.loads(cfg_path.read_text())
        token = cfg.get("telegram_bot_token", "") or cfg.get("telegram_token", "")
    except Exception:
        log.warning("Could not load Telegram token")
        return

    if not token:
        log.warning("telegram_bot_token not set — skipping Telegram alert")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    decision = should_send_notification(
        message,
        source="core.booking_goal_monitor",
        category="booking",
        recipient=TELEGRAM_CHAT_ID,
    )
    if not decision.allowed:
        log.info("Booking goal alert suppressed: %s", decision.reason)
        return

    data = urllib.parse.urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": message}).encode()
    try:
        urllib.request.urlopen(url, data=data, timeout=10)
    except Exception as e:
        log.warning("Telegram send failed: %s", e)


def _build_alert(days: int, leads: list) -> str:
    if days >= 9999:
        prefix = "No bookings recorded yet."
    elif days > GOAL_DAYS:
        prefix = f"OVERDUE: {days} days since last booking (goal: every {GOAL_DAYS} days)."
    else:
        prefix = f"WARNING: {days} days since last booking (goal: every {GOAL_DAYS} days)."

    lines = [prefix]

    if leads:
        lines.append(f"Top {len(leads)} leads needing action:")
        for lead in leads:
            name = lead.get("full_name", "Unknown")
            status = lead.get("booking_status", "?")
            city = lead.get("event_city", "")
            date = lead.get("event_date", "")
            amount = lead.get("total_quote_amount")
            parts = [f"  - {name} ({status})"]
            if city:
                parts.append(city)
            if date:
                parts.append(date)
            if amount:
                parts.append(f"${int(amount):,}")
            lines.append(" | ".join(parts))

    lines.append("Action: Reply /pipeline in Telegram to review all leads.")
    return "\n".join(lines)


def run_check(dry_run: bool = False) -> dict:
    """
    Main check logic. Sends Telegram alert if goal threshold reached.
    Returns status dict.
    """
    data = get_booking_goal_status()
    days = data["days_since_last_booking"]
    leads = data["top_leads"]
    status = data["status"]

    log.info("Booking goal check: %d days since last booking (goal: %d)", days, GOAL_DAYS)

    if status in ("warning", "overdue"):
        alert = _build_alert(days, leads)
        log.info("Sending booking goal alert:\n%s", alert)
        if not dry_run:
            _send_telegram(alert)
    else:
        log.info("Goal on track — no alert needed.")

    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Booking goal monitor")
    parser.add_argument("--check", action="store_true", help="Dry run — print without sending")
    args = parser.parse_args()
    result = run_check(dry_run=args.check)
    if args.check:
        print(json.dumps(result, indent=2))
