#!/usr/bin/env python3
"""Evening operations report — end-of-day summary for Kai.

Covers: leads touched, quotes sent, outreach emails, outstanding quotes,
system health (RAM, disk, DB).

Usage:
    python scripts/evening_ops_report.py              # Print report
    python scripts/evening_ops_report.py --telegram   # Send via Telegram
    python scripts/evening_ops_report.py --json       # JSON output
"""

import argparse
import json
import logging
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("evening_ops")

DB_PATH = Path.home() / ".nexus" / "memory.db"


def _query(conn, sql, params=()):
    try:
        return conn.execute(sql, params).fetchone()
    except Exception:
        return None


def _query_all(conn, sql, params=()):
    try:
        return conn.execute(sql, params).fetchall()
    except Exception:
        return []


def get_disk_free() -> float:
    """Get free disk space in GB."""
    st = os.statvfs("/")
    return st.f_bavail * st.f_frsize / (1024**3)


def generate_report() -> dict:
    """Generate the evening operations report."""
    report = {"timestamp": datetime.now().isoformat(), "sections": {}}
    today = datetime.now().strftime("%Y-%m-%d")

    if not DB_PATH.exists():
        return report

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA query_only = ON")

    # Leads
    leads_today = _query(conn,
        "SELECT COUNT(*) FROM leads WHERE created_at LIKE ?", (f"{today}%",))
    leads_quoted = _query(conn,
        "SELECT COUNT(*) FROM leads WHERE status = 'quoted'")
    leads_outstanding = _query(conn,
        "SELECT COUNT(*) FROM leads WHERE status = 'quoted' AND "
        "datetime(updated_at) < datetime('now', '-48 hours')")

    report["sections"]["leads"] = {
        "new_today": leads_today[0] if leads_today else 0,
        "quoted_total": leads_quoted[0] if leads_quoted else 0,
        "outstanding_48h": leads_outstanding[0] if leads_outstanding else 0,
    }

    # Outreach
    emails_today = _query(conn,
        "SELECT COUNT(*) FROM outbound_log WHERE result='allowed' AND "
        "channel='email' AND timestamp LIKE ?", (f"{today}%",))
    outreach_sent = _query(conn,
        "SELECT COUNT(*) FROM vendor_outreach WHERE status = 'sent' AND "
        "sent_at LIKE ?", (f"{today}%",))

    report["sections"]["outreach"] = {
        "emails_today": emails_today[0] if emails_today else 0,
        "vendor_outreach_today": outreach_sent[0] if outreach_sent else 0,
    }

    # Vendors
    vendor_count = _query(conn, "SELECT COUNT(*) FROM vendors")
    eligible = _query(conn,
        "SELECT COUNT(*) FROM vendors WHERE COALESCE(campaign_eligible, 0) = 1")
    report["sections"]["vendors"] = {
        "total": vendor_count[0] if vendor_count else 0,
        "campaign_eligible": eligible[0] if eligible else 0,
    }

    conn.close()

    # System health
    disk_free = get_disk_free()
    db_size = DB_PATH.stat().st_size / (1024 * 1024)
    report["sections"]["system"] = {
        "disk_free_gb": round(disk_free, 1),
        "db_size_mb": round(db_size, 1),
    }

    return report


def format_report(report: dict) -> str:
    """Format report as human-readable text."""
    s = report["sections"]
    lines = [
        "NEXUS EVENING OPS REPORT",
        f"{datetime.now().strftime('%Y-%m-%d %H:%M PT')}",
        "",
    ]

    l = s.get("leads", {})
    lines.append(f"New leads today: {l.get('new_today', 0)}")
    lines.append(f"Quoted (total): {l.get('quoted_total', 0)}")
    lines.append(f"Outstanding >48h: {l.get('outstanding_48h', 0)}")

    o = s.get("outreach", {})
    lines.append(f"Emails sent today: {o.get('emails_today', 0)}")
    lines.append(f"Vendor outreach today: {o.get('vendor_outreach_today', 0)}")

    v = s.get("vendors", {})
    lines.append(f"Vendors: {v.get('total', 0)} ({v.get('campaign_eligible', 0)} eligible)")

    sys_info = s.get("system", {})
    lines.append(f"Disk: {sys_info.get('disk_free_gb', '?')}GB free")
    lines.append(f"DB: {sys_info.get('db_size_mb', '?')}MB")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Evening operations report")
    parser.add_argument("--telegram", action="store_true", help="Send via Telegram")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    report = generate_report()

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"\n{format_report(report)}")

    if args.telegram:
        try:
            subprocess.run(
                [sys.executable, str(Path(__file__).parent / "notify_telegram.py"),
                 format_report(report)],
                timeout=15, capture_output=True,
            )
        except Exception as e:
            log.error("Telegram failed: %s", e)


if __name__ == "__main__":
    main()
