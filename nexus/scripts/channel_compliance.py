#!/usr/bin/env python3
"""Channel compliance checker — enforces platform posting limits.

Importable by Builder's posting scripts. Call can_post_to() before every post.

Usage:
    from scripts.channel_compliance import can_post_to
    allowed, reason = can_post_to('facebook_page')

    python scripts/channel_compliance.py --status   # Show all channel status
    python scripts/channel_compliance.py --json      # JSON output
"""

import argparse
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("channel_compliance")

DB_PATH = Path.home() / ".nexus" / "memory.db"

PLATFORM_RULES = {
    "email": {
        "max_per_day": 5,  # Day 1-3 warm-up; updated dynamically
        "min_seconds_between": 270,
        "send_window_start": "08:00",
        "send_window_end": "17:00",
        "no_sundays": True,
    },
    "facebook_page": {
        "max_per_day": 2,
        "min_hours_between": 4,
    },
    "facebook_marketplace": {
        "max_per_day": 1,
        "min_hours_between": 24,
    },
    "facebook_groups": {
        "max_total_per_day": 3,
        "max_per_group_per_week": 1,
    },
    "craigslist": {
        "max_per_48h": 1,
    },
}


def _get_warm_up_limit():
    """Get current email daily limit from warm-up schedule."""
    warmup_file = Path.home() / ".nexus" / "warmup_start.txt"
    if not warmup_file.exists():
        return 5
    try:
        start = datetime.strptime(warmup_file.read_text().strip(), "%Y-%m-%d")
        day = max(1, (datetime.now() - start).days + 1)
    except (ValueError, OSError):
        return 5

    if day <= 3:
        return 5
    elif day <= 7:
        return 15
    elif day <= 14:
        return 30
    return 50


def can_post_to(channel, content=None, target=None):
    """
    Check if it's safe to post to a channel right now.
    Returns (allowed: bool, reason: str).
    """
    rules = PLATFORM_RULES.get(channel)
    if not rules:
        return True, "No rules defined for channel"

    conn = sqlite3.connect(str(DB_PATH))

    # Count today's posts for this channel
    if channel == "email":
        today_count = conn.execute(
            "SELECT COUNT(*) FROM outbound_log WHERE channel = 'email' "
            "AND result = 'allowed' AND DATE(timestamp) = DATE('now')"
        ).fetchone()[0]
        max_daily = _get_warm_up_limit()
    else:
        today_count = conn.execute(
            "SELECT COUNT(*) FROM outbound_channels WHERE channel = ? "
            "AND DATE(posted_at) = DATE('now')",
            (channel,)
        ).fetchone()[0]
        max_daily = rules.get("max_per_day", rules.get("max_total_per_day", 999))

    if today_count >= max_daily:
        conn.close()
        return False, "Daily limit reached (%d/%d)" % (today_count, max_daily)

    # Check minimum spacing
    if channel == "email":
        last = conn.execute(
            "SELECT timestamp FROM outbound_log WHERE channel = 'email' "
            "AND result = 'allowed' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    else:
        last = conn.execute(
            "SELECT posted_at FROM outbound_channels WHERE channel = ? "
            "ORDER BY posted_at DESC LIMIT 1",
            (channel,)
        ).fetchone()

    if last and last[0]:
        try:
            last_time = datetime.strptime(last[0][:19], "%Y-%m-%d %H:%M:%S")
            elapsed = (datetime.now() - last_time).total_seconds()

            min_seconds = rules.get("min_seconds_between", 0)
            min_hours = rules.get("min_hours_between", 0)
            if min_hours:
                min_seconds = max(min_seconds, min_hours * 3600)

            if min_seconds and elapsed < min_seconds:
                conn.close()
                return False, "Too soon — %d seconds since last post (min %d)" % (int(elapsed), min_seconds)
        except ValueError:
            pass

    # Craigslist: 48-hour rule
    if channel == "craigslist":
        recent_48h = conn.execute(
            "SELECT COUNT(*) FROM outbound_channels WHERE channel = 'craigslist' "
            "AND posted_at > datetime('now', '-48 hours')"
        ).fetchone()[0]
        if recent_48h >= rules.get("max_per_48h", 1):
            conn.close()
            return False, "Craigslist: already posted in last 48 hours"

    # Facebook Groups: per-group weekly limit
    if channel == "facebook_groups" and target:
        group_week = conn.execute(
            "SELECT COUNT(*) FROM outbound_channels WHERE channel = 'facebook_groups' "
            "AND target = ? AND posted_at > datetime('now', '-7 days')",
            (target,)
        ).fetchone()[0]
        max_per_group = rules.get("max_per_group_per_week", 1)
        if group_week >= max_per_group:
            conn.close()
            return False, "Already posted to %s this week" % target

    # Email: send window check
    if channel == "email":
        now = datetime.now()
        start_h, start_m = map(int, rules.get("send_window_start", "08:00").split(":"))
        end_h, end_m = map(int, rules.get("send_window_end", "17:00").split(":"))
        if now.hour < start_h or now.hour >= end_h:
            conn.close()
            return False, "Outside send window (%s-%s)" % (rules["send_window_start"], rules["send_window_end"])
        if rules.get("no_sundays") and now.weekday() == 6:
            conn.close()
            return False, "No emails on Sundays"

    conn.close()
    return True, "OK"


def log_post(channel, post_type=None, content_preview=None, target=None,
             platform_id=None, status="sent", notes=None):
    """Log a post to the outbound_channels table."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "INSERT INTO outbound_channels "
        "(channel, post_type, content_preview, target, status, platform_id, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (channel, post_type, (content_preview or "")[:200], target, status, platform_id, notes)
    )
    conn.commit()
    conn.close()


def get_channel_status():
    """Get current posting status for all channels."""
    conn = sqlite3.connect(str(DB_PATH))
    status = {}

    for channel, rules in PLATFORM_RULES.items():
        if channel == "email":
            today = conn.execute(
                "SELECT COUNT(*) FROM outbound_log WHERE channel = 'email' "
                "AND result = 'allowed' AND DATE(timestamp) = DATE('now')"
            ).fetchone()[0]
            limit = _get_warm_up_limit()
        else:
            today = conn.execute(
                "SELECT COUNT(*) FROM outbound_channels WHERE channel = ? "
                "AND DATE(posted_at) = DATE('now')",
                (channel,)
            ).fetchone()[0]
            limit = rules.get("max_per_day", rules.get("max_total_per_day", "n/a"))

        allowed, reason = can_post_to(channel)
        status[channel] = {
            "today": today,
            "limit": limit,
            "can_post": allowed,
            "reason": reason,
        }

    conn.close()
    return status


def main():
    parser = argparse.ArgumentParser(description="Channel compliance checker")
    parser.add_argument("--status", action="store_true", help="Show channel status")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    status = get_channel_status()

    if args.json:
        print(json.dumps(status, indent=2))
    else:
        print("\n  Channel Compliance Status")
        print("  " + "=" * 55)
        for ch, s in status.items():
            icon = "OK" if s["can_post"] else "BLOCKED"
            print("  [%s] %-25s %d/%s today  %s" % (
                icon, ch, s["today"], s["limit"], s["reason"] if not s["can_post"] else ""))
        print("  " + "=" * 55)


if __name__ == "__main__":
    main()
