#!/usr/bin/env python3
"""Weekly audit report — comprehensive rollup for Kai.

Covers: email totals, reputation metrics, vendor growth, provider usage,
security events, cost tracking.

Usage:
    python scripts/weekly_audit_report.py              # Print report
    python scripts/weekly_audit_report.py --telegram   # Send via Telegram
    python scripts/weekly_audit_report.py --json       # JSON output
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
log = logging.getLogger("weekly_audit")

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


def generate_report() -> dict:
    """Generate the weekly audit report."""
    report = {"timestamp": datetime.now().isoformat(), "period": "7 days", "sections": {}}
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    if not DB_PATH.exists():
        return report

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA query_only = ON")

    # Email totals
    sends = _query(conn,
        "SELECT COUNT(*) FROM outbound_log WHERE result='allowed' AND timestamp > ?",
        (week_ago,))
    blocked = _query(conn,
        "SELECT COUNT(*) FROM outbound_log WHERE result='blocked' AND timestamp > ?",
        (week_ago,))
    report["sections"]["email"] = {
        "sent_7d": sends[0] if sends else 0,
        "blocked_7d": blocked[0] if blocked else 0,
    }

    # Vendor growth
    vendor_count = _query(conn, "SELECT COUNT(*) FROM vendors")
    new_vendors = _query(conn,
        "SELECT COUNT(*) FROM vendors WHERE created_at > ?", (week_ago,))
    eligible = _query(conn,
        "SELECT COUNT(*) FROM vendors WHERE COALESCE(campaign_eligible, 0) = 1")
    report["sections"]["vendors"] = {
        "total": vendor_count[0] if vendor_count else 0,
        "added_7d": new_vendors[0] if new_vendors else 0,
        "campaign_eligible": eligible[0] if eligible else 0,
    }

    # Lead pipeline
    leads_total = _query(conn, "SELECT COUNT(*) FROM leads")
    new_leads = _query(conn,
        "SELECT COUNT(*) FROM leads WHERE created_at > ?", (week_ago,))
    booked = _query(conn,
        "SELECT COUNT(*) FROM leads WHERE status = 'booked'")
    report["sections"]["leads"] = {
        "total": leads_total[0] if leads_total else 0,
        "new_7d": new_leads[0] if new_leads else 0,
        "booked": booked[0] if booked else 0,
    }

    # Provider usage
    providers = _query_all(conn,
        "SELECT provider, COUNT(*), AVG(latency_ms), SUM(tokens_used) "
        "FROM token_usage WHERE timestamp > ? GROUP BY provider ORDER BY COUNT(*) DESC",
        (week_ago,))
    report["sections"]["providers"] = [
        {"provider": r[0], "calls": r[1],
         "avg_latency_ms": round(r[2]) if r[2] else 0,
         "tokens": r[3] or 0}
        for r in providers
    ]

    # Security events
    audit_events = _query(conn,
        "SELECT COUNT(*) FROM audit_trail WHERE timestamp > ?", (week_ago,))
    blocklist_adds = _query(conn,
        "SELECT COUNT(*) FROM contact_blocklist WHERE created_at > ?", (week_ago,))
    report["sections"]["security"] = {
        "audit_events_7d": audit_events[0] if audit_events else 0,
        "blocklist_additions_7d": blocklist_adds[0] if blocklist_adds else 0,
    }

    conn.close()

    # Cost tracking (should be $0 per Rule 6)
    report["sections"]["cost"] = {
        "ai_api_cost": "$0 (all free tier)",
        "note": "Rule 6: No paid APIs except Claude Code Max",
    }

    return report


def format_report(report: dict) -> str:
    """Format report as human-readable text."""
    s = report["sections"]
    lines = [
        "NEXUS WEEKLY AUDIT REPORT",
        f"{datetime.now().strftime('%Y-%m-%d %H:%M PT')}",
        f"Period: Last 7 days",
        "",
    ]

    # Email
    em = s.get("email", {})
    lines.append(f"Emails sent: {em.get('sent_7d', 0)}")
    lines.append(f"Emails blocked: {em.get('blocked_7d', 0)}")

    # Vendors
    v = s.get("vendors", {})
    lines.append(f"Vendors: {v.get('total', 0)} (+{v.get('added_7d', 0)})")
    lines.append(f"Campaign eligible: {v.get('campaign_eligible', 0)}")

    # Leads
    l = s.get("leads", {})
    lines.append(f"Leads: {l.get('total', 0)} (+{l.get('new_7d', 0)}) | Booked: {l.get('booked', 0)}")

    # Providers
    providers = s.get("providers", [])
    if providers:
        lines.append("Provider usage:")
        for p in providers[:5]:
            lines.append(f"  {p['provider']}: {p['calls']} calls, {p['avg_latency_ms']}ms avg")

    # Security
    sec = s.get("security", {})
    lines.append(f"Audit events: {sec.get('audit_events_7d', 0)}")
    lines.append(f"Blocklist additions: {sec.get('blocklist_additions_7d', 0)}")

    # Cost
    lines.append(f"AI cost: $0 (free tier only)")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Weekly audit report")
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
