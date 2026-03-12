#!/usr/bin/env python3
"""Daily safety report — morning safety briefing for Kai.

Covers: email sends vs warm-up limit, kill switch status, blocklist changes,
vendor count, provider health, system resources.

Usage:
    python scripts/daily_safety_report.py              # Print report
    python scripts/daily_safety_report.py --telegram   # Send via Telegram
    python scripts/daily_safety_report.py --json       # JSON output
"""

import argparse
import json
import logging
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("daily_safety")

DB_PATH = Path.home() / ".nexus" / "memory.db"
KILL_SWITCH = Path.home() / ".nexus" / ".kill_switch"
WARMUP_FILE = Path.home() / ".nexus" / "warmup_start.txt"


def _query(conn, sql, params=()):
    try:
        return conn.execute(sql, params).fetchone()
    except Exception:
        return None


def generate_report() -> dict:
    """Generate the daily safety report."""
    report = {"timestamp": datetime.now().isoformat(), "sections": {}}

    # Kill switch
    report["sections"]["kill_switch"] = {
        "active": KILL_SWITCH.exists(),
        "detail": KILL_SWITCH.read_text().strip() if KILL_SWITCH.exists() else "Not active",
    }

    # Warm-up status
    warmup_day = 0
    if WARMUP_FILE.exists():
        try:
            start = datetime.strptime(WARMUP_FILE.read_text().strip(), "%Y-%m-%d")
            warmup_day = max(1, (datetime.now() - start).days + 1)
        except (ValueError, OSError):
            pass
    report["sections"]["warmup"] = {"day": warmup_day, "started": WARMUP_FILE.exists()}

    if not DB_PATH.exists():
        report["sections"]["db"] = {"status": "missing"}
        return report

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA query_only = ON")

    # Email sends last 24h
    yesterday = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.now().strftime("%Y-%m-%d")

    sends_24h = _query(conn,
        "SELECT COUNT(*) FROM outbound_log WHERE result='allowed' AND timestamp > ?",
        (yesterday,))
    sends_today = _query(conn,
        "SELECT COUNT(*) FROM outbound_log WHERE result='allowed' AND timestamp LIKE ?",
        (f"{today}%",))
    blocked_24h = _query(conn,
        "SELECT COUNT(*) FROM outbound_log WHERE result='blocked' AND timestamp > ?",
        (yesterday,))

    report["sections"]["email"] = {
        "sends_24h": sends_24h[0] if sends_24h else 0,
        "sends_today": sends_today[0] if sends_today else 0,
        "blocked_24h": blocked_24h[0] if blocked_24h else 0,
    }

    # Blocklist
    blocklist_count = _query(conn, "SELECT COUNT(*) FROM contact_blocklist WHERE active = 1")
    blocklist_new = _query(conn,
        "SELECT COUNT(*) FROM contact_blocklist WHERE active = 1 AND created_at > ?",
        (yesterday,))
    report["sections"]["blocklist"] = {
        "active": blocklist_count[0] if blocklist_count else 0,
        "added_24h": blocklist_new[0] if blocklist_new else 0,
    }

    # Vendors
    vendor_count = _query(conn, "SELECT COUNT(*) FROM vendors")
    report["sections"]["vendors"] = {
        "count": vendor_count[0] if vendor_count else 0,
    }

    # Leads
    lead_count = _query(conn, "SELECT COUNT(*) FROM leads")
    new_leads = _query(conn,
        "SELECT COUNT(*) FROM leads WHERE created_at > ?", (yesterday,))
    report["sections"]["leads"] = {
        "total": lead_count[0] if lead_count else 0,
        "new_24h": new_leads[0] if new_leads else 0,
    }

    # DB health
    size_mb = DB_PATH.stat().st_size / (1024 * 1024)
    integrity = _query(conn, "PRAGMA integrity_check")
    report["sections"]["db"] = {
        "size_mb": round(size_mb, 1),
        "integrity": integrity[0] if integrity else "unknown",
    }

    # Audit trail
    audit_count = _query(conn, "SELECT COUNT(*) FROM audit_trail")
    recent_audit = _query(conn,
        "SELECT COUNT(*) FROM audit_trail WHERE timestamp > ?", (yesterday,))
    report["sections"]["audit"] = {
        "total": audit_count[0] if audit_count else 0,
        "last_24h": recent_audit[0] if recent_audit else 0,
    }

    # Outreach queue status
    queue_pending = _query(conn, "SELECT COUNT(*) FROM outreach_queue WHERE status = 'pending'")
    queue_sent = _query(conn, "SELECT COUNT(*) FROM outreach_queue WHERE status = 'sent'")
    report["sections"]["outreach"] = {
        "queue_pending": queue_pending[0] if queue_pending else 0,
        "queue_sent": queue_sent[0] if queue_sent else 0,
    }

    # Channel activity (last 24h)
    try:
        channels = conn.execute(
            "SELECT channel, COUNT(*) FROM outbound_channels "
            "WHERE posted_at > ? GROUP BY channel", (yesterday,)
        ).fetchall()
        report["sections"]["channels"] = {ch: cnt for ch, cnt in channels}
    except Exception:
        report["sections"]["channels"] = {}

    # Conversion funnel snapshot
    try:
        funnel = conn.execute(
            "SELECT stage, COUNT(*) FROM conversion_funnel "
            "WHERE stage_entered_at > datetime('now', '-7 days') "
            "GROUP BY stage"
        ).fetchall()
        report["sections"]["funnel_7d"] = {stage: cnt for stage, cnt in funnel}
    except Exception:
        report["sections"]["funnel_7d"] = {}

    conn.close()
    return report


def format_report(report: dict) -> str:
    """Format report as human-readable text."""
    s = report["sections"]
    lines = [
        "NEXUS DAILY SAFETY REPORT",
        f"{datetime.now().strftime('%Y-%m-%d %H:%M PT')}",
        "",
    ]

    # Kill switch
    ks = s.get("kill_switch", {})
    lines.append(f"Kill switch: {'ACTIVE — ' + ks.get('detail', '') if ks.get('active') else 'OFF (normal)'}")

    # Warm-up
    wu = s.get("warmup", {})
    if wu.get("started"):
        lines.append(f"Warm-up: Day {wu['day']}")
    else:
        lines.append("Warm-up: NOT STARTED")

    # Email
    em = s.get("email", {})
    lines.append(f"Emails sent (24h): {em.get('sends_24h', 0)}")
    lines.append(f"Emails today: {em.get('sends_today', 0)}")
    lines.append(f"Blocked (24h): {em.get('blocked_24h', 0)}")

    # Blocklist
    bl = s.get("blocklist", {})
    lines.append(f"Blocklist: {bl.get('active', 0)} active (+{bl.get('added_24h', 0)} new)")

    # Vendors & Leads
    v = s.get("vendors", {})
    l = s.get("leads", {})
    lines.append(f"Vendors: {v.get('count', 0)}")
    lines.append(f"Leads: {l.get('total', 0)} (+{l.get('new_24h', 0)} new)")

    # DB
    db = s.get("db", {})
    lines.append(f"DB: {db.get('size_mb', '?')}MB, integrity: {db.get('integrity', '?')}")

    # Audit
    au = s.get("audit", {})
    lines.append(f"Audit events (24h): {au.get('last_24h', 0)}")

    # Outreach queue
    oq = s.get("outreach", {})
    if oq:
        lines.append(f"Outreach queue: {oq.get('queue_pending', 0)} pending, {oq.get('queue_sent', 0)} sent")

    # Channel activity
    ch = s.get("channels", {})
    if ch:
        lines.append("Channel activity (24h): " + ", ".join(f"{k}={v}" for k, v in ch.items()))

    # Conversion funnel
    fn = s.get("funnel_7d", {})
    if fn:
        lines.append("Funnel (7d): " + ", ".join(f"{k}={v}" for k, v in fn.items()))

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Daily safety report")
    parser.add_argument("--telegram", action="store_true", help="Send via Telegram")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    report = generate_report()

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        text = format_report(report)
        print(f"\n{text}")

    if args.telegram:
        text = format_report(report)
        try:
            subprocess.run(
                [sys.executable, str(Path(__file__).parent / "notify_telegram.py"), text],
                timeout=15, capture_output=True,
            )
            log.info("Report sent via Telegram")
        except Exception as e:
            log.error("Telegram send failed: %s", e)


if __name__ == "__main__":
    main()
