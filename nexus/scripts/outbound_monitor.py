#!/usr/bin/env python3
"""Outbound monitor — watches outbound_log for anomalies.

Detects: excessive send rate, duplicate recipients, hash chain breaks.

Usage:
    python scripts/outbound_monitor.py              # One check cycle
    python scripts/outbound_monitor.py --daemon      # Continuous (60s interval)
    python scripts/outbound_monitor.py --json        # JSON output
"""

import argparse
import json
import logging
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("outbound_monitor")

DB_PATH = Path.home() / ".nexus" / "memory.db"
CHECK_INTERVAL = 60

# Warm-up limits — matches sender_reputation_guard.py and email_campaign.py
WARMUP_LIMITS = {
    (1, 3): 30,
    (4, 7): 30,
    (8, 14): 40,
    (15, 999): 50,
}

# Alert thresholds
MAX_SENDS_PER_HOUR = 15
DUPLICATE_WINDOW_HOURS = 24

_running = True


def _signal_handler(sig, frame):
    global _running
    _running = False
    log.info("Shutting down outbound monitor")


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


def _send_telegram(message: str):
    """Send alert via existing notify script."""
    try:
        subprocess.run(
            [sys.executable, str(Path(__file__).parent / "notify_telegram.py"), message],
            timeout=15, capture_output=True,
        )
    except Exception as e:
        log.error("Telegram alert failed: %s", e)


def check_send_rate(conn: sqlite3.Connection) -> dict:
    """Check sends in the last hour."""
    one_hour_ago = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    row = conn.execute(
        "SELECT COUNT(*) FROM outbound_log WHERE result='allowed' AND timestamp > ?",
        (one_hour_ago,),
    ).fetchone()
    count = row[0] if row else 0

    status = "ok"
    if count > MAX_SENDS_PER_HOUR:
        status = "alert"

    return {"check": "send_rate_1h", "status": status, "count": count, "limit": MAX_SENDS_PER_HOUR}


def check_daily_rate(conn: sqlite3.Connection) -> dict:
    """Check total sends today vs warm-up limit."""
    today = datetime.now().strftime("%Y-%m-%d")
    row = conn.execute(
        "SELECT COUNT(*) FROM outbound_log WHERE result='allowed' AND timestamp LIKE ?",
        (f"{today}%",),
    ).fetchone()
    count = row[0] if row else 0

    # Determine warm-up day
    warmup_file = Path.home() / ".nexus" / "warmup_start.txt"
    if warmup_file.exists():
        try:
            start = datetime.strptime(warmup_file.read_text().strip(), "%Y-%m-%d")
            day = (datetime.now() - start).days + 1
        except (ValueError, OSError):
            day = 1
    else:
        day = 1

    limit = 5  # default
    for (lo, hi), lim in WARMUP_LIMITS.items():
        if lo <= day <= hi:
            limit = lim
            break

    status = "ok"
    if count >= limit:
        status = "alert"
    elif count >= limit * 0.8:
        status = "warning"

    return {"check": "daily_rate", "status": status, "count": count,
            "limit": limit, "warmup_day": day}


def check_duplicate_recipients(conn: sqlite3.Connection) -> dict:
    """Check if any recipient was contacted more than once in 24h."""
    cutoff = (datetime.now() - timedelta(hours=DUPLICATE_WINDOW_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        "SELECT recipient_hash, COUNT(*) as cnt FROM outbound_log "
        "WHERE result='allowed' AND timestamp > ? "
        "GROUP BY recipient_hash HAVING cnt > 1",
        (cutoff,),
    ).fetchall()

    duplicates = [{"hash": r[0][:16] + "...", "count": r[1]} for r in rows]
    status = "alert" if duplicates else "ok"

    return {"check": "duplicate_recipients", "status": status,
            "duplicates": len(duplicates), "details": duplicates[:5]}


def check_blocked_attempts(conn: sqlite3.Connection) -> dict:
    """Check recent blocked outbound attempts."""
    one_hour_ago = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    row = conn.execute(
        "SELECT COUNT(*) FROM outbound_log WHERE result='blocked' AND timestamp > ?",
        (one_hour_ago,),
    ).fetchone()
    count = row[0] if row else 0

    status = "ok"
    if count > 5:
        status = "warning"

    return {"check": "blocked_attempts_1h", "status": status, "count": count}


def check_hash_chain(conn: sqlite3.Connection) -> dict:
    """Verify outbound_log hash chain integrity."""
    # Check if hash columns exist
    try:
        rows = conn.execute(
            "SELECT id, timestamp, channel, recipient_hash, message_hash, "
            "result, prev_hash, entry_hash FROM outbound_log "
            "WHERE prev_hash IS NOT NULL AND entry_hash IS NOT NULL "
            "ORDER BY id ASC"
        ).fetchall()
    except sqlite3.OperationalError:
        return {"check": "hash_chain", "status": "ok", "message": "No hash columns — legacy entries only"}

    if not rows:
        return {"check": "hash_chain", "status": "ok", "message": "No hash-chained entries"}

    import hashlib
    expected_prev = "0" * 64
    for row in rows:
        rid, ts, ch, rh, mh, result, prev_h, entry_h = row
        if prev_h != expected_prev:
            return {"check": "hash_chain", "status": "alert",
                    "message": f"Chain broken at id={rid}"}
        data = f"{ts}|{ch}|{rh}|{mh}|{result}|{prev_h}"
        computed = hashlib.sha256(data.encode()).hexdigest()
        if computed != entry_h:
            return {"check": "hash_chain", "status": "alert",
                    "message": f"Tampered entry at id={rid}"}
        expected_prev = entry_h

    return {"check": "hash_chain", "status": "ok",
            "message": f"Chain valid: {len(rows)} entries"}


def run_check() -> dict:
    """Run all outbound monitoring checks."""
    if not DB_PATH.exists():
        return {"timestamp": datetime.now().isoformat(), "status": "error",
                "message": "Database not found"}

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA query_only = ON")

    # Check if outbound_log table exists
    table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='outbound_log'"
    ).fetchone()
    if not table:
        conn.close()
        return {"timestamp": datetime.now().isoformat(), "status": "ok",
                "message": "No outbound_log table — no sends yet", "checks": []}

    checks = [
        check_send_rate(conn),
        check_daily_rate(conn),
        check_duplicate_recipients(conn),
        check_blocked_attempts(conn),
        check_hash_chain(conn),
    ]
    conn.close()

    overall = "ok"
    alerts = [c for c in checks if c["status"] == "alert"]
    warnings = [c for c in checks if c["status"] == "warning"]
    if alerts:
        overall = "alert"
    elif warnings:
        overall = "warning"

    return {
        "timestamp": datetime.now().isoformat(),
        "status": overall,
        "checks": checks,
        "alert_count": len(alerts),
        "warning_count": len(warnings),
    }


def main():
    parser = argparse.ArgumentParser(description="Outbound message monitor")
    parser.add_argument("--daemon", action="store_true", help="Run continuously")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    log.info("Outbound monitor — database: %s", DB_PATH)

    if args.daemon:
        log.info("Daemon mode (%ds interval)", CHECK_INTERVAL)
        try:
            from core.agent_runtime import AgentRuntime
            _agent = AgentRuntime('outbound_monitor', 'daemon', 'operations')
            _agent.start()
        except Exception:
            _agent = None
        while _running:
            result = run_check()
            if _agent:
                sends = sum(c.get("count", 0) for c in result.get("checks", []) if "send" in c.get("check", "").lower())
                _agent.heartbeat(f"Monitoring — status: {result['status']}")
            if result["status"] == "alert":
                alert_checks = [c for c in result.get("checks", []) if c["status"] == "alert"]
                msg = "OUTBOUND MONITOR ALERT\n"
                for c in alert_checks:
                    msg += f"  {c['check']}: {json.dumps({k: v for k, v in c.items() if k != 'check'})}\n"
                _send_telegram(msg)
                log.warning("ALERT: %s", msg.strip())
            else:
                log.info("Status: %s", result["status"])

            if not _running:
                break
            time.sleep(CHECK_INTERVAL)
        if _agent:
            _agent.stop()
    else:
        result = run_check()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"\n  Outbound Monitor — {result['timestamp']}")
            print(f"  Overall: {result['status'].upper()}")
            for c in result.get("checks", []):
                icon = {"ok": "OK", "warning": "WARN", "alert": "ALERT"}.get(c["status"], "?")
                detail = {k: v for k, v in c.items() if k not in ("check", "status")}
                print(f"  [{icon}] {c['check']}: {detail}")
            if not result.get("checks"):
                print(f"  {result.get('message', 'No data')}")


if __name__ == "__main__":
    main()
