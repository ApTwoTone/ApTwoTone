#!/usr/bin/env python3
"""Sender reputation guard — enforces email warm-up schedule.

Warm-up schedule:
    Day 1-3:   max 30 emails/day
    Day 4-7:   max 30 emails/day
    Day 8-14:  max 40 emails/day
    Day 15+:   max 50 emails/day

Usage:
    python scripts/sender_reputation_guard.py --status   # Show warm-up state
    python scripts/sender_reputation_guard.py --check    # Go/no-go for next send
    python scripts/sender_reputation_guard.py --start    # Start warm-up timer
    python scripts/sender_reputation_guard.py --reset    # Reset warm-up (requires confirmation)
    python scripts/sender_reputation_guard.py --json     # JSON output

Importable:
    from scripts.sender_reputation_guard import can_send, get_warmup_status
"""

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("reputation_guard")

DB_PATH = Path.home() / ".nexus" / "memory.db"
WARMUP_FILE = Path.home() / ".nexus" / "warmup_start.txt"

WARMUP_SCHEDULE = [
    (1, 3, 30),     # Day 1-3: max 30/day (aligned with email_campaign.py)
    (4, 7, 30),     # Day 4-7: max 30/day
    (8, 14, 40),    # Day 8-14: max 40/day
    (15, 9999, 50), # Day 15+: max 50/day
]


def get_warmup_day() -> int:
    """Get the current warm-up day number (1-based)."""
    if not WARMUP_FILE.exists():
        return 0  # Not started
    try:
        start = datetime.strptime(WARMUP_FILE.read_text().strip(), "%Y-%m-%d")
        return max(1, (datetime.now() - start).days + 1)
    except (ValueError, OSError):
        return 0


def get_daily_limit(day: int) -> int:
    """Get the email limit for a given warm-up day."""
    if day <= 0:
        return 0
    for lo, hi, limit in WARMUP_SCHEDULE:
        if lo <= day <= hi:
            return limit
    return 50  # Fallback


def get_sent_today() -> int:
    """Count emails sent today from outbound_log."""
    if not DB_PATH.exists():
        return 0
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("PRAGMA query_only = ON")

        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='outbound_log'"
        ).fetchone()
        if not table:
            conn.close()
            return 0

        today = datetime.now().strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT COUNT(*) FROM outbound_log "
            "WHERE result='allowed' AND channel='email' AND timestamp LIKE ?",
            (f"{today}%",),
        ).fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception as e:
        log.error("Error counting today's sends: %s", e)
        return 0


def get_warmup_status() -> dict:
    """Get full warm-up status."""
    day = get_warmup_day()
    limit = get_daily_limit(day)
    sent = get_sent_today()
    remaining = max(0, limit - sent)

    phase = "not_started"
    if day == 0:
        phase = "not_started"
    elif day <= 3:
        phase = "initial"
    elif day <= 7:
        phase = "ramp_up"
    elif day <= 14:
        phase = "building"
    else:
        phase = "established"

    return {
        "warmup_day": day,
        "phase": phase,
        "daily_limit": limit,
        "sent_today": sent,
        "remaining": remaining,
        "allowed": day > 0 and sent < limit,
        "start_date": WARMUP_FILE.read_text().strip() if WARMUP_FILE.exists() else None,
    }


def can_send() -> dict:
    """Check if sending is allowed right now.

    Returns:
        {"allowed": bool, "reason": str, "sent_today": int, "limit": int, "day": int}
    """
    status = get_warmup_status()

    if status["warmup_day"] == 0:
        return {"allowed": False, "reason": "Warm-up not started. Run --start first.",
                "sent_today": 0, "limit": 0, "day": 0}

    if status["sent_today"] >= status["daily_limit"]:
        return {"allowed": False,
                "reason": f"Daily limit reached ({status['sent_today']}/{status['daily_limit']})",
                "sent_today": status["sent_today"], "limit": status["daily_limit"],
                "day": status["warmup_day"]}

    return {"allowed": True,
            "reason": f"OK — {status['remaining']} sends remaining today",
            "sent_today": status["sent_today"], "limit": status["daily_limit"],
            "day": status["warmup_day"]}


def start_warmup():
    """Start or verify warm-up timer."""
    if WARMUP_FILE.exists():
        existing = WARMUP_FILE.read_text().strip()
        day = get_warmup_day()
        print(f"  Warm-up already started on {existing} (day {day})")
        return False

    today = datetime.now().strftime("%Y-%m-%d")
    WARMUP_FILE.write_text(today)
    log.info("Warm-up started: %s", today)

    # Log to audit trail
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from audit_helpers import log_audit
        log_audit("warmup_started", "reputation_guard", "email", f"Start date: {today}")
    except ImportError:
        pass

    print(f"  Warm-up started: {today}")
    print(f"  Day 1-3: max 30/day")
    print(f"  Day 4-7: max 30/day")
    print(f"  Day 8-14: max 40/day")
    print(f"  Day 15+: max 50/day")
    return True


def reset_warmup():
    """Reset warm-up timer (requires confirmation)."""
    if not WARMUP_FILE.exists():
        print("  No warm-up in progress — nothing to reset.")
        return

    print(f"  Current warm-up started: {WARMUP_FILE.read_text().strip()}")
    print(f"  Type 'CONFIRM RESET' to reset:")
    confirm = input("  > ").strip()
    if confirm != "CONFIRM RESET":
        print("  Reset cancelled.")
        return

    WARMUP_FILE.unlink()
    log.info("Warm-up reset")

    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from audit_helpers import log_audit
        log_audit("warmup_reset", "reputation_guard", "email", "Manual reset")
    except ImportError:
        pass

    print("  Warm-up reset. Run --start to begin again.")


def main():
    parser = argparse.ArgumentParser(description="Sender reputation guard")
    parser.add_argument("--status", action="store_true", help="Show warm-up status")
    parser.add_argument("--check", action="store_true", help="Go/no-go for next send")
    parser.add_argument("--start", action="store_true", help="Start warm-up timer")
    parser.add_argument("--reset", action="store_true", help="Reset warm-up timer")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.start:
        start_warmup()
        return

    if args.reset:
        reset_warmup()
        return

    if args.check:
        result = can_send()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            icon = "GO" if result["allowed"] else "STOP"
            print(f"\n  [{icon}] {result['reason']}")
            print(f"  Sent today: {result['sent_today']}/{result['limit']} (day {result['day']})")
        sys.exit(0 if result["allowed"] else 1)

    # Default: show status
    status = get_warmup_status()
    if args.json:
        print(json.dumps(status, indent=2))
    else:
        print(f"\n  Sender Reputation Guard")
        print(f"  {'=' * 40}")
        if status["warmup_day"] == 0:
            print(f"  Warm-up: NOT STARTED")
            print(f"  Run: python {__file__} --start")
        else:
            print(f"  Warm-up day: {status['warmup_day']} (phase: {status['phase']})")
            print(f"  Start date: {status['start_date']}")
            print(f"  Daily limit: {status['daily_limit']}")
            print(f"  Sent today: {status['sent_today']}")
            print(f"  Remaining: {status['remaining']}")
            print(f"  Allowed: {'YES' if status['allowed'] else 'NO'}")
        print(f"  {'=' * 40}")


if __name__ == "__main__":
    main()
