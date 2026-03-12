#!/usr/bin/env python3
"""Vendor quality monitor — detects rogue vendor inserts and fake data patterns.

STAGED ONLY — do not start as a daemon without Kai's approval.

Usage:
    python scripts/vendor_quality_monitor.py              # One check cycle, then exit
    python scripts/vendor_quality_monitor.py --daemon      # Continuous monitoring (60s interval)
    python scripts/vendor_quality_monitor.py --json        # JSON output for scripting
"""

import argparse
import json
import logging
import re
import signal
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("vendor_monitor")

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"
MONITOR_LOG = Path.home() / ".nexus" / "vendor_monitor.log"
COUNT_FILE = Path.home() / ".nexus" / "vendor_monitor_count.txt"

# Detection thresholds
COUNT_ALERT_THRESHOLD = 5       # Alert if count increases by this much
CHECK_INTERVAL_SECONDS = 60     # How often to check in daemon mode

# Approved vendor categories (from CLAUDE.md target events)
APPROVED_CATEGORIES = {
    "Wedding Venue", "Event Venue", "Banquet Hall", "Catering",
    "Wedding Planner", "Event Planner", "Party Rental",
    "Country Club", "Restaurant", "Hotel", "Winery",
    "Community Center", "DJ", "Photographer", "Videographer",
    "Florist", "Baker", "Decorator", "Limo Service",
    "Photo Booth", "Tent Rental",
}

# Fake phone patterns
FAKE_PHONE_PATTERNS = [
    re.compile(r"555"),                         # 555 prefix
    re.compile(r"(\d)\1{3,}"),                  # Repeated digits (1111, 2222)
    re.compile(r"1234|2345|3456|4567|5678|6789|7890"),  # Sequential
    re.compile(r"4321|5432|6543|7654|8765|9876"),       # Reverse sequential
]

_running = True


def signal_handler(sig, frame):
    global _running
    _running = False
    log.info("Shutting down vendor monitor")


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def get_vendor_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]


def get_recent_vendors(conn: sqlite3.Connection, since_id: int, limit: int = 50) -> List[Dict]:
    """Get vendors inserted after a given ID."""
    rows = conn.execute(
        "SELECT id, name, phone, email, city, category, source, created_at "
        "FROM vendors WHERE id > ? ORDER BY id ASC LIMIT ?",
        (since_id, limit),
    ).fetchall()
    return [
        {"id": r[0], "name": r[1], "phone": r[2], "email": r[3],
         "city": r[4], "category": r[5], "source": r[6], "created_at": r[7]}
        for r in rows
    ]


def get_max_vendor_id(conn: sqlite3.Connection) -> int:
    result = conn.execute("SELECT MAX(id) FROM vendors").fetchone()[0]
    return result or 0


def detect_fake_phone(phone: str) -> List[str]:
    """Check if a phone number matches known fake patterns."""
    if not phone:
        return []
    digits = re.sub(r"\D", "", phone)
    issues = []
    for pattern in FAKE_PHONE_PATTERNS:
        if pattern.search(digits):
            issues.append(f"fake_phone:{pattern.pattern}")
    return issues


def detect_unapproved_category(category: str) -> bool:
    """Check if vendor category is not in approved list."""
    if not category:
        return True
    # Fuzzy match: check if category matches any approved one (case insensitive)
    cat_lower = category.lower().replace("_", " ")
    for approved in APPROVED_CATEGORIES:
        if approved.lower() in cat_lower or cat_lower in approved.lower():
            return False
    return True


def analyze_new_vendors(vendors: List[Dict]) -> Dict[str, Any]:
    """Analyze a batch of new vendors for quality issues."""
    issues = []
    fake_phones = 0
    unapproved_cats = 0
    no_contact = 0

    for v in vendors:
        vendor_issues = []

        # Check phone
        phone_issues = detect_fake_phone(v.get("phone", ""))
        if phone_issues:
            fake_phones += 1
            vendor_issues.extend(phone_issues)

        # Check category
        if detect_unapproved_category(v.get("category", "")):
            unapproved_cats += 1
            vendor_issues.append(f"unapproved_category:{v.get('category', 'none')}")

        # Check contact info
        if not v.get("phone") and not v.get("email"):
            no_contact += 1
            vendor_issues.append("no_contact_info")

        if vendor_issues:
            issues.append({
                "vendor_id": v["id"],
                "name": v["name"],
                "issues": vendor_issues,
            })

    return {
        "total_new": len(vendors),
        "fake_phones": fake_phones,
        "unapproved_categories": unapproved_cats,
        "no_contact_info": no_contact,
        "issues": issues,
    }


def log_to_file(message: str):
    """Append message to monitor log file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(MONITOR_LOG, "a") as f:
        f.write(f"[{timestamp}] {message}\n")


def send_telegram_alert(message: str):
    """Send Telegram alert."""
    try:
        config = json.loads(CONFIG_PATH.read_text())
        token = config.get("telegram_bot_token", "")
        chat_id = config.get("telegram_chat_id", "8540603351")
        if not token:
            log.warning("No Telegram bot token — cannot send alert")
            return

        import urllib.request
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({"chat_id": chat_id, "text": message}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        log.info("Telegram alert sent")
    except Exception as e:
        log.error("Failed to send Telegram alert: %s", e)


def run_check(conn: sqlite3.Connection, last_count: int, last_max_id: int) -> Dict[str, Any]:
    """Run one monitoring cycle."""
    current_count = get_vendor_count(conn)
    current_max_id = get_max_vendor_id(conn)
    increase = current_count - last_count if last_count > 0 else 0

    result = {
        "timestamp": datetime.now().isoformat(),
        "vendor_count": current_count,
        "previous_count": last_count,
        "increase": increase,
        "status": "ok",
        "analysis": None,
    }

    if increase > 0:
        # New vendors detected — analyze them
        new_vendors = get_recent_vendors(conn, last_max_id)
        analysis = analyze_new_vendors(new_vendors)
        result["analysis"] = analysis

        if increase >= COUNT_ALERT_THRESHOLD:
            result["status"] = "alert"
            msg = (
                f"VENDOR MONITOR ALERT\n"
                f"New vendors detected: +{increase} (now {current_count})\n"
                f"Fake phones: {analysis['fake_phones']}\n"
                f"Unapproved categories: {analysis['unapproved_categories']}\n"
                f"No contact info: {analysis['no_contact_info']}"
            )
            log_to_file(f"ALERT: +{increase} vendors | {analysis['fake_phones']} fake phones | {analysis['unapproved_categories']} bad cats")
            send_telegram_alert(msg)
        else:
            result["status"] = "warning"
            log_to_file(f"WARNING: +{increase} vendors (below alert threshold)")
    else:
        log_to_file(f"OK: count stable at {current_count}")

    return result


def main():
    parser = argparse.ArgumentParser(description="Vendor quality monitor")
    parser.add_argument("--daemon", action="store_true", help="Run continuously")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if not DB_PATH.exists():
        log.error("Database not found: %s", DB_PATH)
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA query_only = ON")

    # Startup banner — shows which DB file we're monitoring
    last_count = get_vendor_count(conn)
    last_max_id = get_max_vendor_id(conn)
    last_insert = conn.execute("SELECT MAX(created_at) FROM vendors").fetchone()[0]
    log.info("Monitoring database: %s", DB_PATH)
    log.info("Database exists: %s (%.1f MB)", DB_PATH.exists(), DB_PATH.stat().st_size / (1024*1024))
    log.info("Current vendor count: %d", last_count)
    log.info("Last insert: %s", last_insert)
    log_to_file(f"INIT: db={DB_PATH}, count={last_count}, last_insert={last_insert}")

    # Load saved count if available
    if COUNT_FILE.exists():
        try:
            saved = int(COUNT_FILE.read_text().strip())
            if saved > 0:
                last_count = saved
        except ValueError:
            pass

    if args.daemon:
        log.info("Vendor quality monitor started (daemon mode, %ds interval)", CHECK_INTERVAL_SECONDS)
        log_to_file(f"STARTED: daemon mode, count={last_count}")

        while _running:
            result = run_check(conn, last_count, last_max_id)
            last_count = result["vendor_count"]
            last_max_id = get_max_vendor_id(conn)
            COUNT_FILE.write_text(str(last_count))

            if not _running:
                break
            time.sleep(CHECK_INTERVAL_SECONDS)

        log_to_file("STOPPED: daemon shutdown")
    else:
        # Single check
        result = run_check(conn, last_count, last_max_id)
        COUNT_FILE.write_text(str(result["vendor_count"]))

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"\nVendor Quality Monitor — {result['timestamp']}")
            print(f"  Count: {result['vendor_count']} (was {result['previous_count']}, +{result['increase']})")
            print(f"  Status: {result['status']}")
            if result["analysis"]:
                a = result["analysis"]
                print(f"  New vendors: {a['total_new']}")
                print(f"  Fake phones: {a['fake_phones']}")
                print(f"  Unapproved categories: {a['unapproved_categories']}")
                print(f"  No contact info: {a['no_contact_info']}")
                if a["issues"]:
                    print(f"  Issues found ({len(a['issues'])}):")
                    for issue in a["issues"][:5]:
                        print(f"    [{issue['vendor_id']}] {issue['name']}: {', '.join(issue['issues'])}")

    conn.close()


if __name__ == "__main__":
    main()
