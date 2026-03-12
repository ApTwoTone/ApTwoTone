#!/usr/bin/env python3
"""Pre-Launch Verification System — 12 checks before every email batch.

Runs at 7:45 AM daily (15 min before 8 AM batch).
ALL checks must pass for GO. Any failure = Telegram alert with fix instructions.

Usage:
    python scripts/prelaunch_verification.py           # Run all 12 checks
    python scripts/prelaunch_verification.py --json     # JSON output
"""

import argparse
import json
import logging
import os
import shutil
import smtplib
import sqlite3
import ssl
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("prelaunch_verify")

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"
KILL_SWITCH = Path.home() / ".nexus" / ".kill_switch"


def _load_config():
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def _send_telegram(msg):
    try:
        subprocess.run(
            [sys.executable, str(Path(__file__).parent / "notify_telegram.py"), msg],
            timeout=15, capture_output=True,
        )
    except Exception as e:
        log.error("Telegram failed: %s", e)


def check(name, condition, fix_instruction):
    """Run a single check. Returns (name, passed, fix, detail)."""
    try:
        result = condition()
        if isinstance(result, tuple):
            passed, detail = result
        else:
            passed = bool(result)
            detail = ""
        return name, passed, fix_instruction if not passed else "", detail
    except Exception as e:
        return name, False, fix_instruction, "Error: %s" % e


def run_all_checks():
    config = _load_config()
    checks = []

    # 1. Outbound enabled
    checks.append(check(
        "Outbound enabled",
        lambda: config.get("outbound_messages_enabled", False) is True,
        "Set outbound_messages_enabled: true in ~/.nexus/config.json"
    ))

    # 2. Kill switch OFF
    checks.append(check(
        "Kill switch OFF",
        lambda: not KILL_SWITCH.exists(),
        "Remove: rm ~/.nexus/.kill_switch"
    ))

    # 3. Warm-up limit >= 30
    def warmup_check():
        from scripts.sender_reputation_guard import get_daily_limit, get_warmup_status
        status = get_warmup_status()
        day = status["warmup_day"]
        limit = get_daily_limit(max(day, 1))
        return limit >= 30, "Day %d, limit %d" % (day, limit)
    checks.append(check(
        "Warm-up limit >= 30",
        warmup_check,
        "Check WARMUP_SCHEDULE in scripts/sender_reputation_guard.py"
    ))

    # 4. SMTP authenticates (port 465 SSL)
    def smtp_check():
        addr = config.get("gmail_address", "")
        pwd = config.get("gmail_app_password", "")
        if not addr or not pwd:
            return False, "Missing gmail_address or gmail_app_password"
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=10) as server:
            server.login(addr, pwd)
        return True, addr
    checks.append(check(
        "SMTP authenticated",
        smtp_check,
        "Check gmail_address and gmail_app_password in ~/.nexus/config.json"
    ))

    # 5. 10+ vendors in queue
    def vendor_check():
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("PRAGMA query_only = ON")
        count = conn.execute("""
            SELECT COUNT(*) FROM vendors
            WHERE campaign_eligible = 1
            AND email IS NOT NULL AND length(email) > 0
            AND email_valid = 1
            AND (outreach_status IS NULL OR outreach_status = '' OR outreach_status = 'none' OR outreach_status = 'pending')
        """).fetchone()[0]
        conn.close()
        return count >= 10, "%d vendors ready" % count
    checks.append(check(
        "10+ vendors in queue",
        vendor_check,
        "Run vendor discovery or email enrichment to find more eligible vendors"
    ))

    # 6. No sends yet today
    def no_sends_check():
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("PRAGMA query_only = ON")
        count = conn.execute("""
            SELECT COUNT(*) FROM outbound_log
            WHERE DATE(timestamp) = DATE('now', 'localtime') AND result = 'sent'
        """).fetchone()[0]
        conn.close()
        return count == 0, "%d sent today" % count
    checks.append(check(
        "No sends yet today",
        no_sends_check,
        "Batch may have already run today. Check outbound_log."
    ))

    # 7. Batch sender script exists
    batch_script = Path(__file__).parent / "send_outreach_batch.py"
    checks.append(check(
        "Batch sender exists",
        lambda: (batch_script.exists(), str(batch_script)),
        "Batch sender script missing: scripts/send_outreach_batch.py"
    ))

    # 8. Outbound monitor running
    def monitor_check():
        r = subprocess.run(["pgrep", "-f", "outbound_monitor.py"], capture_output=True, timeout=5)
        return r.returncode == 0, "PID: %s" % r.stdout.decode().strip() if r.returncode == 0 else ""
    checks.append(check(
        "Outbound monitor running",
        monitor_check,
        "Start: nohup python scripts/outbound_monitor.py --daemon &"
    ))

    # 9. Bounce monitor running
    def bounce_check():
        r = subprocess.run(["pgrep", "-f", "bounce_monitor.py"], capture_output=True, timeout=5)
        return r.returncode == 0, "PID: %s" % r.stdout.decode().strip() if r.returncode == 0 else ""
    checks.append(check(
        "Bounce monitor running",
        bounce_check,
        "Start: nohup python scripts/bounce_monitor.py --daemon &"
    ))

    # 10. Server alive
    def server_check():
        import urllib.request
        try:
            r = urllib.request.urlopen("http://localhost:7860/api/health", timeout=5)
            return r.status == 200, "HTTP %d" % r.status
        except Exception:
            # Try just connecting
            try:
                r = urllib.request.urlopen("http://localhost:7860/", timeout=5)
                return True, "HTTP %d" % r.status
            except Exception as e2:
                return False, str(e2)
    checks.append(check(
        "Server alive (port 7860)",
        server_check,
        "Start: cd ~/nexus && python server.py &"
    ))

    # 11. Disk space > 5GB
    def disk_check():
        total, used, free = shutil.disk_usage("/")
        free_gb = free / (1024 ** 3)
        return free_gb > 5, "%.1f GB free" % free_gb
    checks.append(check(
        "Disk space > 5GB",
        disk_check,
        "Clear space: rm old backups from ~/.nexus/backups/"
    ))

    # 12. Yesterday's bounce rate < 5%
    def bounce_rate_check():
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("PRAGMA query_only = ON")
        # Count emails sent yesterday
        sent_yesterday = conn.execute("""
            SELECT COUNT(*) FROM outbound_log
            WHERE DATE(timestamp) = DATE('now', 'localtime', '-1 day') AND result = 'sent'
        """).fetchone()[0]
        if sent_yesterday == 0:
            conn.close()
            return True, "No sends yesterday"
        # Count vendors that bounced (email_valid changed to 0 with outreach_status='bounced')
        bounced = conn.execute("""
            SELECT COUNT(*) FROM vendors
            WHERE outreach_status = 'bounced'
            AND email_valid = 0
        """).fetchone()[0]
        conn.close()
        rate = (bounced / max(sent_yesterday, 1)) * 100
        return rate < 5.0, "%.1f%% (%d bounced / %d sent)" % (rate, bounced, sent_yesterday)
    checks.append(check(
        "Bounce rate < 5%",
        bounce_rate_check,
        "High bounce rate. Review email validation. Consider pausing outreach."
    ))

    return checks


def main():
    parser = argparse.ArgumentParser(description="Pre-launch verification (12 checks)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    now = datetime.now().strftime("%Y-%m-%d %H:%M PT")

    checks = run_all_checks()

    passed = sum(1 for _, ok, _, _ in checks if ok)
    failed = sum(1 for _, ok, _, _ in checks if not ok)
    failures = [(n, f, d) for n, ok, f, d in checks if not ok]

    if args.json:
        print(json.dumps({
            "timestamp": now,
            "passed": passed,
            "failed": failed,
            "total": len(checks),
            "checks": [{"name": n, "ok": ok, "fix": f, "detail": d} for n, ok, f, d in checks],
        }, indent=2))
        return

    print("\n" + "=" * 60)
    print("  PRE-LAUNCH VERIFICATION — %s" % now)
    print("=" * 60 + "\n")

    for name, ok, fix, detail in checks:
        status = "PASS" if ok else "FAIL"
        extra = " (%s)" % detail if detail else ""
        print("  [%s]  %s%s" % (status, name, extra))
        if not ok and fix:
            print("         FIX: %s" % fix)

    print("\n  Result: %d/%d passed" % (passed, len(checks)))

    if failed == 0:
        print("\n  GO — All checks passed. Email batch fires at 8:00 AM.")
    else:
        print("\n  NO-GO — %d check(s) failed. Fix before 8 AM!" % failed)

    print("=" * 60)

    # Send Telegram
    msg = "PRE-LAUNCH CHECK %s\n\n%d/%d passed\n" % (now, passed, len(checks))
    if failures:
        msg += "\nFAILURES:\n"
        for name, fix, detail in failures:
            msg += "  %s" % name
            if detail:
                msg += " (%s)" % detail
            msg += "\n  Fix: %s\n" % fix
    else:
        msg += "\nAll systems GO for 8:00 AM batch."
    _send_telegram(msg)

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
