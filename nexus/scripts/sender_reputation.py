#!/usr/bin/env python3
"""Sender Reputation Dashboard — tracks metrics that Gmail uses to judge us.

Key metrics Gmail cares about:
- Bounce rate (MUST be < 2%, currently 6.7% after Day 1)
- Spam complaint rate (MUST be < 0.1%)
- Send volume consistency (steady = good, spiky = bad)
- Domain age (new sender = more scrutiny)

Usage:
    python scripts/sender_reputation.py           # Show dashboard
    python scripts/sender_reputation.py --json     # JSON output
    python scripts/sender_reputation.py --telegram  # Send via Telegram
"""

import argparse
import json
import logging
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sender_reputation")

DB_PATH = Path.home() / ".nexus" / "memory.db"
WARMUP_FILE = Path.home() / ".nexus" / "warmup_start.txt"


def calculate_reputation():
    """Calculate sender reputation score 0-100."""
    if not DB_PATH.exists():
        return {"score": 0, "grade": "F", "error": "DB not found"}

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA query_only = ON")

    # Total sends all time
    total_sent = conn.execute(
        "SELECT COUNT(*) FROM outbound_log WHERE result = 'sent'"
    ).fetchone()[0]

    # Total bounces (vendors with outreach_status='bounced' or email_valid=0 after being sent to)
    total_bounced = conn.execute(
        "SELECT COUNT(*) FROM vendors WHERE outreach_status = 'bounced'"
    ).fetchone()[0]

    # Bounce rate
    bounce_rate = (total_bounced / max(total_sent, 1)) * 100

    # Spam complaints (from contact_blocklist)
    try:
        spam_complaints = conn.execute(
            "SELECT COUNT(*) FROM contact_blocklist WHERE reason LIKE '%spam%'"
        ).fetchone()[0]
    except Exception:
        spam_complaints = 0
    spam_rate = (spam_complaints / max(total_sent, 1)) * 100

    # Days with sends (consistency metric)
    days_active = conn.execute(
        "SELECT COUNT(DISTINCT DATE(timestamp)) FROM outbound_log WHERE result = 'sent'"
    ).fetchone()[0]

    # Warm-up day
    warmup_day = 0
    if WARMUP_FILE.exists():
        try:
            start = datetime.strptime(WARMUP_FILE.read_text().strip(), "%Y-%m-%d")
            warmup_day = max(1, (datetime.now() - start).days + 1)
        except (ValueError, OSError):
            pass

    # Today's sends
    sent_today = conn.execute(
        "SELECT COUNT(*) FROM outbound_log "
        "WHERE DATE(timestamp) = DATE('now', 'localtime') AND result = 'sent'"
    ).fetchone()[0]

    # Blocked today
    blocked_today = conn.execute(
        "SELECT COUNT(*) FROM outbound_log "
        "WHERE DATE(timestamp) = DATE('now', 'localtime') AND result = 'blocked'"
    ).fetchone()[0]

    # Unsubscribes
    try:
        unsubscribes = conn.execute(
            "SELECT COUNT(*) FROM vendors WHERE unsubscribed = 1"
        ).fetchone()[0]
    except Exception:
        unsubscribes = 0

    conn.close()

    # Score calculation (start at 100, deduct for issues)
    score = 100

    # Bounce penalty (most important)
    if bounce_rate > 5:
        score -= 40  # Critical
    elif bounce_rate > 2:
        score -= 25  # Over Gmail threshold
    elif bounce_rate > 1:
        score -= 10
    elif bounce_rate > 0:
        score -= 5

    # Spam complaint penalty
    if spam_rate > 0.1:
        score -= 30
    elif spam_rate > 0.05:
        score -= 15
    elif spam_rate > 0:
        score -= 5

    # Volume consistency bonus
    if days_active >= 14:
        score += 5
    elif days_active >= 7:
        score += 3

    # New sender penalty (warm-up period)
    if warmup_day < 7:
        score -= 5  # Still in early warm-up

    score = max(0, min(100, score))

    # Grade
    if score >= 90:
        grade = "A"
    elif score >= 75:
        grade = "B"
    elif score >= 60:
        grade = "C"
    elif score >= 40:
        grade = "D"
    else:
        grade = "F"

    # Action recommendation
    if score < 40:
        action = "CRITICAL: Pause outreach. Fix bounce rate before sending more."
    elif score < 60:
        action = "WARNING: High bounce rate. Validate all emails before next batch."
    elif score < 75:
        action = "CAUTION: Monitor closely. Improve email validation."
    elif score < 90:
        action = "GOOD: Continue warm-up. Bounce rate needs improvement."
    else:
        action = "EXCELLENT: Sending reputation is healthy."

    return {
        "score": score,
        "grade": grade,
        "action": action,
        "bounce_rate": round(bounce_rate, 2),
        "spam_rate": round(spam_rate, 3),
        "total_sent": total_sent,
        "total_bounced": total_bounced,
        "spam_complaints": spam_complaints,
        "unsubscribes": unsubscribes,
        "days_active": days_active,
        "warmup_day": warmup_day,
        "sent_today": sent_today,
        "blocked_today": blocked_today,
        "gmail_thresholds": {
            "bounce_rate_max": "2%",
            "spam_rate_max": "0.1%",
            "our_bounce_rate": "%.1f%%" % bounce_rate,
            "our_spam_rate": "%.3f%%" % spam_rate,
            "bounce_status": "OVER" if bounce_rate > 2 else "OK",
            "spam_status": "OVER" if spam_rate > 0.1 else "OK",
        },
    }


def format_dashboard(rep):
    """Format reputation as human-readable dashboard."""
    lines = [
        "SENDER REPUTATION DASHBOARD",
        "=" * 45,
        "",
        "  Score: %d/100 (Grade: %s)" % (rep["score"], rep["grade"]),
        "  %s" % rep["action"],
        "",
        "  GMAIL THRESHOLDS:",
        "    Bounce rate:  %.1f%% (threshold: 2%%) — %s" % (
            rep["bounce_rate"], rep["gmail_thresholds"]["bounce_status"]),
        "    Spam rate:    %.3f%% (threshold: 0.1%%) — %s" % (
            rep["spam_rate"], rep["gmail_thresholds"]["spam_status"]),
        "",
        "  VOLUME:",
        "    Total sent:     %d" % rep["total_sent"],
        "    Bounced:        %d" % rep["total_bounced"],
        "    Spam complaints: %d" % rep["spam_complaints"],
        "    Unsubscribes:   %d" % rep["unsubscribes"],
        "    Days active:    %d" % rep["days_active"],
        "    Warm-up day:    %d" % rep["warmup_day"],
        "",
        "  TODAY:",
        "    Sent:    %d" % rep["sent_today"],
        "    Blocked: %d" % rep["blocked_today"],
        "=" * 45,
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Sender reputation dashboard")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--telegram", action="store_true", help="Send via Telegram")
    args = parser.parse_args()

    rep = calculate_reputation()

    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        print("\n" + format_dashboard(rep))

    if args.telegram:
        text = format_dashboard(rep)
        try:
            subprocess.run(
                [sys.executable, str(Path(__file__).parent / "notify_telegram.py"), text],
                timeout=15, capture_output=True,
            )
        except Exception as e:
            log.error("Telegram failed: %s", e)


if __name__ == "__main__":
    main()
