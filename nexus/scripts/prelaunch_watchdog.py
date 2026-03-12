#!/usr/bin/env python3
"""Pre-launch overnight watchdog — timed checks before 8 AM email launch.

Schedule:
    Continuous:  Watch for new FB ads files, scan for content compliance
    7:30 AM:     Verify warm-up limit = 30
    7:45 AM:     Full pre-launch systems check (8 checks)
    8:00 AM:     Start real-time send monitoring (every 2 min)
    10:30 AM:    Post-batch audit, then exit

Usage:
    python scripts/prelaunch_watchdog.py           # Run all timed tasks
    python scripts/prelaunch_watchdog.py --test     # Run all checks once (no waiting)
"""

import argparse
import json
import logging
import os
import re
import smtplib
import sqlite3
import ssl
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(Path.home() / ".nexus" / "prelaunch_watchdog.log")),
    ],
)
log = logging.getLogger("prelaunch_watchdog")

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"
KILL_SWITCH = Path.home() / ".nexus" / ".kill_switch"
PROJECT_DIR = Path(__file__).resolve().parent.parent

# FB ads file targets
FB_ADS_DIR = Path.home() / ".nexus" / "fb_ads"
FB_ADS_PLAYBOOK = PROJECT_DIR / "FACEBOOK_ADS_PLAYBOOK.md"

# Extra compliance patterns beyond content_compliance.py defaults
EXTRA_PATTERNS = [
    ("film_reference", re.compile(r"\b(film|production|shooting|shoot)\b", re.IGNORECASE),
     "Film/production reference"),
    ("grip_lighting", re.compile(r"\b(grip|gaffer|lighting\s+crew|best\s+boy)\b", re.IGNORECASE),
     "Grip/lighting crew reference"),
]

# Track which files we already scanned
_scanned_files = set()


def _send_telegram(msg):
    try:
        subprocess.run(
            [sys.executable, str(Path(__file__).parent / "notify_telegram.py"), msg],
            timeout=15, capture_output=True,
        )
        log.info("Telegram sent: %s", msg[:80])
    except Exception as e:
        log.error("Telegram failed: %s", e)


def _load_config():
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────
# Task 1: Verify warm-up limit (7:30 AM)
# ─────────────────────────────────────────────────────────

def verify_warmup_limit():
    """Verify Builder changed warm-up limit to 30 for Day 1."""
    log.info("=== TASK 1: Warm-up limit verification ===")

    try:
        sys.path.insert(0, str(PROJECT_DIR))
        from scripts.sender_reputation_guard import get_daily_limit, get_warmup_status

        day1_limit = get_daily_limit(1)
        status = get_warmup_status()

        log.info("Day 1 limit: %d (expected 30)", day1_limit)
        log.info("Current status: day=%d, limit=%d, sent=%d",
                 status["warmup_day"], status["daily_limit"], status["sent_today"])

        if day1_limit != 30:
            msg = ("WARM-UP ALERT: Day 1 limit is %d, expected 30. "
                   "Builder fix NOT applied. Check sender_reputation_guard.py WARMUP_SCHEDULE." % day1_limit)
            _send_telegram(msg)
            log.error(msg)
            return False

        log.info("PASS: Warm-up limit verified at 30")
        return True

    except Exception as e:
        msg = "WARM-UP CHECK FAILED: %s" % e
        _send_telegram(msg)
        log.error(msg)
        return False


# ─────────────────────────────────────────────────────────
# Task 2: FB Ads content compliance (continuous)
# ─────────────────────────────────────────────────────────

def scan_fb_ads_files():
    """Watch for and scan new FB ads files for content compliance."""
    global _scanned_files

    try:
        sys.path.insert(0, str(PROJECT_DIR))
        from scripts.content_compliance import scan_file
    except ImportError:
        try:
            # Direct import if running from project dir
            spec_path = Path(__file__).parent / "content_compliance.py"
            if spec_path.exists():
                import importlib.util
                spec = importlib.util.spec_from_file_location("content_compliance", str(spec_path))
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                scan_file = mod.scan_file
            else:
                scan_file = None
        except Exception:
            log.warning("content_compliance.py not importable, using inline scanner")
            scan_file = None

    files_to_scan = []

    # Check ~/.nexus/fb_ads/ directory
    if FB_ADS_DIR.exists():
        for f in FB_ADS_DIR.rglob("*"):
            if f.is_file() and str(f) not in _scanned_files:
                files_to_scan.append(f)

    # Check FACEBOOK_ADS_PLAYBOOK.md
    if FB_ADS_PLAYBOOK.exists() and str(FB_ADS_PLAYBOOK) not in _scanned_files:
        files_to_scan.append(FB_ADS_PLAYBOOK)

    # Also check for any new ad-related files in project root
    for pattern in ["*ad_copy*", "*fb_ad*", "*facebook_ad*", "*campaign*"]:
        for f in PROJECT_DIR.glob(pattern):
            if f.is_file() and str(f) not in _scanned_files:
                files_to_scan.append(f)

    if not files_to_scan:
        return []

    log.info("=== TASK 2: Scanning %d new FB ads files ===", len(files_to_scan))
    all_violations = []

    for filepath in files_to_scan:
        _scanned_files.add(str(filepath))
        violations = []

        try:
            content = filepath.read_text()
            lines = content.split("\n")
        except Exception as e:
            log.warning("Cannot read %s: %s", filepath, e)
            continue

        # Use content_compliance.py's scan_file if available
        if scan_file is not None:
            violations.extend(scan_file(filepath))

        # Run extra patterns (film/production/grip/lighting)
        for line_num, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue
            for name, pattern, description in EXTRA_PATTERNS:
                matches = pattern.findall(line)
                if matches:
                    violations.append({
                        "file": str(filepath),
                        "line": line_num,
                        "pattern": name,
                        "description": description,
                        "match": matches[0] if len(matches) == 1 else str(matches),
                        "context": stripped[:80],
                    })

        if violations:
            log.warning("Found %d violations in %s", len(violations), filepath.name)
            all_violations.extend(violations)

    # Log violations to AUDITOR_LOG.md
    if all_violations:
        _log_violations_to_auditor(all_violations)
        summary = "FB ADS COMPLIANCE: %d violations in %d files" % (
            len(all_violations), len(files_to_scan))
        _send_telegram(summary + "\n" + "\n".join(
            "- [%s:%d] %s: %s" % (v["file"].split("/")[-1], v["line"], v["pattern"], v["match"])
            for v in all_violations[:5]
        ))

    return all_violations


def _log_violations_to_auditor(violations):
    """Append FB ads violations to AUDITOR_LOG.md."""
    auditor_log = PROJECT_DIR / "AUDITOR_LOG.md"
    if not auditor_log.exists():
        return

    entry = "\n\n## FB Ads Content Compliance Scan — %s\n\n" % datetime.now().strftime("%Y-%m-%d %H:%M PT")
    entry += "| File | Line | Pattern | Match |\n"
    entry += "|------|------|---------|-------|\n"
    for v in violations:
        fname = v.get("file", "").split("/")[-1]
        entry += "| %s | %d | %s | %s |\n" % (fname, v["line"], v["pattern"], v["match"][:40])
    entry += "\nTotal: %d violations\n" % len(violations)

    with open(str(auditor_log), "a") as f:
        f.write(entry)
    log.info("Violations logged to AUDITOR_LOG.md")


# ─────────────────────────────────────────────────────────
# Task 3: Pre-launch systems check (7:45 AM)
# ─────────────────────────────────────────────────────────

def _is_process_running(name):
    """Check if a Python process with given name is running."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", name], capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def _test_smtp():
    """Test SMTP authentication without sending."""
    config = _load_config()
    addr = config.get("gmail_address", "")
    pwd = config.get("gmail_app_password", "")
    if not addr or not pwd:
        return False
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=10) as server:
            server.login(addr, pwd)
        return True
    except Exception as e:
        log.error("SMTP auth failed: %s", e)
        return False


def _get_vendor_count():
    """Get current vendor count."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("PRAGMA query_only = ON")
        count = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
        conn.close()
        return count
    except Exception:
        return 0


def _get_queue_count():
    """Get outreach queue pending count."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("PRAGMA query_only = ON")
        count = conn.execute(
            "SELECT COUNT(*) FROM outreach_queue WHERE status = 'pending'"
        ).fetchone()[0]
        conn.close()
        return count
    except Exception:
        return 0


def prelaunch_check():
    """Full pre-launch systems check — 8 checks, all must pass."""
    log.info("=== TASK 3: Pre-launch systems check (7:45 AM) ===")

    checks = {}

    # 1. Outbound monitor running
    checks["outbound_monitor"] = _is_process_running("outbound_monitor.py")
    log.info("  Outbound monitor: %s", "RUNNING" if checks["outbound_monitor"] else "DOWN")

    # 2. Bounce monitor running
    checks["bounce_monitor"] = _is_process_running("bounce_monitor.py")
    log.info("  Bounce monitor: %s", "RUNNING" if checks["bounce_monitor"] else "DOWN")

    # 3. Kill switch OFF
    checks["kill_switch_off"] = not KILL_SWITCH.exists()
    log.info("  Kill switch: %s", "OFF" if checks["kill_switch_off"] else "ACTIVE")

    # 4. SMTP authenticates
    checks["smtp_auth"] = _test_smtp()
    log.info("  SMTP auth: %s", "OK" if checks["smtp_auth"] else "FAIL")

    # 5. Vendor count stable (should be ~415)
    vendor_count = _get_vendor_count()
    checks["vendor_count"] = vendor_count > 0
    log.info("  Vendors: %d", vendor_count)

    # 6. Outreach queue populated
    queue_count = _get_queue_count()
    checks["outreach_queue"] = queue_count > 0
    log.info("  Outreach queue: %d pending", queue_count)

    # 7. Config: outbound_messages_enabled = true
    config = _load_config()
    checks["outbound_enabled"] = config.get("outbound_messages_enabled") is True
    log.info("  Outbound enabled: %s", checks["outbound_enabled"])

    # 8. Warm-up limit = 30
    try:
        sys.path.insert(0, str(PROJECT_DIR))
        from scripts.sender_reputation_guard import get_daily_limit
        day1_limit = get_daily_limit(1)
        checks["warmup_limit_30"] = (day1_limit == 30)
        log.info("  Warm-up Day 1 limit: %d (expected 30)", day1_limit)
    except Exception as e:
        checks["warmup_limit_30"] = False
        log.error("  Warm-up check error: %s", e)

    # Evaluate
    failures = [k for k, v in checks.items() if not v]
    if failures:
        msg = "PRE-LAUNCH FAIL at 7:45 AM:\n" + "\n".join("- %s" % f for f in failures)
        msg += "\n\nPassing: %s" % ", ".join(k for k, v in checks.items() if v)
        _send_telegram(msg)
        log.error("PRE-LAUNCH: %d FAILURES: %s", len(failures), ", ".join(failures))
        return False
    else:
        log.info("PRE-LAUNCH: ALL 8 CHECKS PASS")
        _send_telegram("Pre-launch check 7:45 AM: ALL 8 CHECKS PASS. Ready for 8 AM send.")
        return True


# ─────────────────────────────────────────────────────────
# Task 4: Real-time send monitoring (8:00 AM)
# ─────────────────────────────────────────────────────────

def monitor_sends():
    """Monitor outbound_log every 2 minutes during the email batch."""
    log.info("=== TASK 4: Real-time send monitoring (8:00 AM) ===")

    last_count = 0
    no_change_count = 0
    alert_sent = set()

    while True:
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute("PRAGMA query_only = ON")

            # Total sent today
            sent_today = conn.execute(
                "SELECT COUNT(*) FROM outbound_log "
                "WHERE DATE(timestamp) = DATE('now') AND result = 'allowed'"
            ).fetchone()[0]

            # Blocked today
            blocked = conn.execute(
                "SELECT COUNT(*) FROM outbound_log "
                "WHERE DATE(timestamp) = DATE('now') AND result = 'blocked'"
            ).fetchone()[0]

            # Last 5 sends
            recent = conn.execute(
                "SELECT id, recipient, result, timestamp FROM outbound_log "
                "WHERE DATE(timestamp) = DATE('now') "
                "ORDER BY id DESC LIMIT 5"
            ).fetchall()

            # Non-vendor sends (CRITICAL)
            non_vendor = conn.execute(
                "SELECT recipient FROM outbound_log "
                "WHERE DATE(timestamp) = DATE('now') AND result = 'allowed' "
                "AND recipient NOT IN (SELECT email FROM vendors WHERE email IS NOT NULL) "
                "AND recipient NOT LIKE '%kaiescobar%' "
                "AND recipient NOT LIKE '%zoar%'"
            ).fetchall()

            # Pacing check — any sends < 3 min apart
            pacing_ok = True
            timestamps = conn.execute(
                "SELECT timestamp FROM outbound_log "
                "WHERE DATE(timestamp) = DATE('now') AND result = 'allowed' "
                "ORDER BY id DESC LIMIT 10"
            ).fetchall()
            if len(timestamps) >= 2:
                try:
                    t1 = datetime.strptime(timestamps[0][0][:19], "%Y-%m-%d %H:%M:%S")
                    t2 = datetime.strptime(timestamps[1][0][:19], "%Y-%m-%d %H:%M:%S")
                    gap = abs((t1 - t2).total_seconds())
                    if gap < 180:
                        pacing_ok = False
                except ValueError:
                    pass

            # Send pacing summary
            pacing = conn.execute(
                "SELECT MIN(timestamp), MAX(timestamp), COUNT(*) "
                "FROM outbound_log "
                "WHERE DATE(timestamp) = DATE('now') AND result = 'allowed'"
            ).fetchone()

            conn.close()

            # Log status
            log.info("Sends: %d (blocked: %d) | Last: %s",
                     sent_today, blocked,
                     recent[0][1][:30] if recent else "none")

            # ALERTS
            if non_vendor and "non_vendor" not in alert_sent:
                msg = "CRITICAL: Email sent to NON-VENDOR: %s" % non_vendor[0][0]
                _send_telegram(msg)
                log.error(msg)
                alert_sent.add("non_vendor")

            if sent_today > 30 and "over_limit" not in alert_sent:
                msg = "ALERT: Sent %d emails today — exceeds 30 limit!" % sent_today
                _send_telegram(msg)
                log.error(msg)
                alert_sent.add("over_limit")

            if not pacing_ok and "pacing" not in alert_sent:
                msg = "ALERT: Emails sent less than 3 minutes apart"
                _send_telegram(msg)
                log.warning(msg)
                alert_sent.add("pacing")

            if KILL_SWITCH.exists() and "kill_switch" not in alert_sent:
                msg = "ALERT: Kill switch ACTIVATED during batch"
                _send_telegram(msg)
                log.error(msg)
                alert_sent.add("kill_switch")

            # Check if batch is done (no new sends for 15 min)
            if sent_today == last_count and sent_today > 0:
                no_change_count += 1
                if no_change_count >= 8:  # 8 x 2min = 16 min
                    log.info("Batch appears complete: %d sends, no new for 16 min", sent_today)
                    break
            else:
                no_change_count = 0
            last_count = sent_today

            # Hard stop at 10:30 AM
            if datetime.now().hour >= 10 and datetime.now().minute >= 30:
                log.info("Reached 10:30 AM cutoff")
                break

        except Exception as e:
            log.error("Monitor error: %s", e)

        time.sleep(120)  # 2 minutes

    # Post-batch summary
    _post_batch_audit()


def _post_batch_audit():
    """Generate post-batch audit report."""
    log.info("=== POST-BATCH AUDIT ===")

    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("PRAGMA query_only = ON")

        sent = conn.execute(
            "SELECT COUNT(*) FROM outbound_log "
            "WHERE DATE(timestamp) = DATE('now') AND result = 'allowed'"
        ).fetchone()[0]

        blocked = conn.execute(
            "SELECT COUNT(*) FROM outbound_log "
            "WHERE DATE(timestamp) = DATE('now') AND result = 'blocked'"
        ).fetchone()[0]

        errors = conn.execute(
            "SELECT COUNT(*) FROM outbound_log "
            "WHERE DATE(timestamp) = DATE('now') AND result NOT IN ('allowed', 'blocked')"
        ).fetchone()[0]

        pacing = conn.execute(
            "SELECT MIN(timestamp), MAX(timestamp) FROM outbound_log "
            "WHERE DATE(timestamp) = DATE('now') AND result = 'allowed'"
        ).fetchone()

        recipients = conn.execute(
            "SELECT recipient FROM outbound_log "
            "WHERE DATE(timestamp) = DATE('now') AND result = 'allowed'"
        ).fetchall()

        non_vendor = conn.execute(
            "SELECT recipient FROM outbound_log "
            "WHERE DATE(timestamp) = DATE('now') AND result = 'allowed' "
            "AND recipient NOT IN (SELECT email FROM vendors WHERE email IS NOT NULL) "
            "AND recipient NOT LIKE '%kaiescobar%' AND recipient NOT LIKE '%zoar%'"
        ).fetchall()

        conn.close()

        # Build summary
        summary = "DAY 1 EMAIL BATCH REPORT\n"
        summary += "Sent: %d | Blocked: %d | Errors: %d\n" % (sent, blocked, errors)
        if pacing[0]:
            summary += "First: %s | Last: %s\n" % (pacing[0][:16], pacing[1][:16])
        if non_vendor:
            summary += "NON-VENDOR SENDS: %d (INVESTIGATE)\n" % len(non_vendor)
        else:
            summary += "All sends to verified vendors: YES\n"
        summary += "Anomalies: %s" % ("NONE" if not non_vendor and errors == 0 else "SEE LOG")

        _send_telegram(summary)
        log.info("Post-batch audit complete:\n%s", summary)

        # Write to AUDITOR_LOG.md
        auditor_log = PROJECT_DIR / "AUDITOR_LOG.md"
        if auditor_log.exists():
            entry = "\n\n## Day 1 Email Batch Audit — %s\n\n" % datetime.now().strftime("%Y-%m-%d %H:%M PT")
            entry += "| Metric | Value |\n|--------|-------|\n"
            entry += "| Sent | %d |\n" % sent
            entry += "| Blocked | %d |\n" % blocked
            entry += "| Errors | %d |\n" % errors
            entry += "| Non-vendor sends | %d |\n" % len(non_vendor)
            if pacing[0]:
                entry += "| First send | %s |\n" % pacing[0][:16]
                entry += "| Last send | %s |\n" % pacing[1][:16]
            entry += "\nVerdict: %s\n" % ("CLEAN" if not non_vendor and errors == 0 else "INVESTIGATE")

            with open(str(auditor_log), "a") as f:
                f.write(entry)

    except Exception as e:
        log.error("Post-batch audit failed: %s", e)


# ─────────────────────────────────────────────────────────
# Main loop — timed execution
# ─────────────────────────────────────────────────────────

def _wait_until(hour, minute):
    """Sleep until the specified time today."""
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        return  # Already past
    wait = (target - now).total_seconds()
    log.info("Waiting until %02d:%02d (%d seconds)", hour, minute, int(wait))
    time.sleep(wait)


def run_all_checks_once():
    """Run all checks immediately (test mode)."""
    log.info("=== TEST MODE: Running all checks ===")
    verify_warmup_limit()
    scan_fb_ads_files()
    prelaunch_check()
    log.info("=== TEST MODE COMPLETE ===")


def main():
    parser = argparse.ArgumentParser(description="Pre-launch overnight watchdog")
    parser.add_argument("--test", action="store_true", help="Run all checks once (no waiting)")
    args = parser.parse_args()

    if args.test:
        run_all_checks_once()
        return

    log.info("Overnight watchdog started. Schedule:")
    log.info("  Continuous: FB ads file watcher (every 5 min)")
    log.info("  7:30 AM: Warm-up limit verification")
    log.info("  7:45 AM: Pre-launch systems check")
    log.info("  8:00 AM: Real-time send monitoring")

    # Phase 1: Watch for FB ads files until 7:30 AM
    while datetime.now().hour < 7 or (datetime.now().hour == 7 and datetime.now().minute < 30):
        scan_fb_ads_files()
        time.sleep(300)  # 5 minutes

    # Phase 2: 7:30 AM — verify warm-up limit
    verify_warmup_limit()

    # Continue watching for FB ads files until 7:45
    while datetime.now().hour < 7 or (datetime.now().hour == 7 and datetime.now().minute < 45):
        scan_fb_ads_files()
        time.sleep(60)  # Check every minute now (closer to launch)

    # Phase 3: 7:45 AM — pre-launch systems check
    prelaunch_check()

    # Final FB ads scan
    scan_fb_ads_files()

    # Phase 4: Wait until 8:00 AM, then monitor
    _wait_until(8, 0)
    monitor_sends()

    log.info("Overnight watchdog complete.")


if __name__ == "__main__":
    main()
