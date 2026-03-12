#!/usr/bin/env python3
"""Weekly ROI report — calculates return on investment per channel.

Covers: spend, leads per channel, CPL, conversion rates, revenue, ROI.

Usage:
    python scripts/weekly_roi_report.py              # Print report
    python scripts/weekly_roi_report.py --telegram   # Send via Telegram
    python scripts/weekly_roi_report.py --json       # JSON output
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
log = logging.getLogger("weekly_roi")

DB_PATH = Path.home() / ".nexus" / "memory.db"


def generate_report():
    """Generate the weekly ROI report."""
    report = {"timestamp": datetime.now().isoformat(), "period": "7 days", "channels": {}}

    if not DB_PATH.exists():
        return report

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA query_only = ON")

    # Email outreach metrics
    try:
        email_sent = conn.execute(
            "SELECT COUNT(*) FROM outbound_log WHERE channel = 'email' "
            "AND result = 'allowed' AND timestamp > datetime('now', '-7 days')"
        ).fetchone()[0]
        email_replies = conn.execute(
            "SELECT COUNT(*) FROM conversion_funnel WHERE source = 'cold_email' "
            "AND stage = 'reply' AND stage_entered_at > datetime('now', '-7 days')"
        ).fetchone()[0]
        email_bookings = conn.execute(
            "SELECT COUNT(*) FROM conversion_funnel WHERE source = 'cold_email' "
            "AND stage = 'booked' AND stage_entered_at > datetime('now', '-7 days')"
        ).fetchone()[0]
        email_revenue = conn.execute(
            "SELECT COALESCE(SUM(revenue), 0) FROM conversion_funnel WHERE source = 'cold_email' "
            "AND stage = 'booked' AND stage_entered_at > datetime('now', '-7 days')"
        ).fetchone()[0]
        report["channels"]["email"] = {
            "sent": email_sent,
            "replies": email_replies,
            "reply_rate": round(email_replies / max(email_sent, 1) * 100, 1),
            "bookings": email_bookings,
            "revenue": email_revenue,
            "cost": 0,
            "roi": "infinite" if email_revenue > 0 else "n/a",
        }
    except Exception:
        report["channels"]["email"] = {"sent": 0, "note": "No data"}

    # Facebook ad metrics
    try:
        for table in ["ad_metrics", "ad_performance"]:
            try:
                ad_spend = conn.execute(
                    "SELECT COALESCE(SUM(spend), 0) FROM %s "
                    "WHERE date > datetime('now', '-7 days')" % table
                ).fetchone()[0]
                ad_leads = conn.execute(
                    "SELECT COALESCE(SUM(leads), 0) FROM %s "
                    "WHERE date > datetime('now', '-7 days')" % table
                ).fetchone()[0]
                ad_impressions = conn.execute(
                    "SELECT COALESCE(SUM(impressions), 0) FROM %s "
                    "WHERE date > datetime('now', '-7 days')" % table
                ).fetchone()[0]
                report["channels"]["facebook_ads"] = {
                    "spend": ad_spend,
                    "impressions": ad_impressions,
                    "leads": ad_leads,
                    "cpl": round(ad_spend / max(ad_leads, 1), 2),
                    "bookings": 0,
                    "revenue": 0,
                }
                break
            except Exception:
                continue
    except Exception:
        pass

    # Organic channels (from outbound_channels)
    try:
        channels = conn.execute(
            "SELECT channel, COUNT(*) FROM outbound_channels "
            "WHERE posted_at > datetime('now', '-7 days') "
            "GROUP BY channel"
        ).fetchall()
        for ch, count in channels:
            if ch not in report["channels"]:
                report["channels"][ch] = {"posts": count, "cost": 0}
    except Exception:
        pass

    # Overall conversion funnel
    try:
        funnel = conn.execute(
            "SELECT source, stage, COUNT(*) FROM conversion_funnel "
            "WHERE stage_entered_at > datetime('now', '-7 days') "
            "GROUP BY source, stage"
        ).fetchall()
        report["funnel"] = {}
        for source, stage, count in funnel:
            if source not in report["funnel"]:
                report["funnel"][source] = {}
            report["funnel"][source][stage] = count
    except Exception:
        report["funnel"] = {}

    # Total revenue
    try:
        total_rev = conn.execute(
            "SELECT COALESCE(SUM(revenue), 0) FROM conversion_funnel "
            "WHERE stage = 'booked' AND stage_entered_at > datetime('now', '-7 days')"
        ).fetchone()[0]
        total_spend = sum(
            ch.get("spend", ch.get("cost", 0))
            for ch in report["channels"].values()
            if isinstance(ch, dict)
        )
        report["totals"] = {
            "revenue": total_rev,
            "spend": total_spend,
            "profit": total_rev - total_spend,
            "roi": round((total_rev - total_spend) / max(total_spend, 1) * 100, 1) if total_spend else "n/a",
        }
    except Exception:
        report["totals"] = {"revenue": 0, "spend": 0}

    conn.close()
    return report


def format_report(report):
    """Format as human-readable text."""
    lines = [
        "NEXUS WEEKLY ROI REPORT",
        datetime.now().strftime("%Y-%m-%d %H:%M PT"),
        "Period: Last 7 days",
        "",
    ]

    for ch, data in report.get("channels", {}).items():
        lines.append("%s:" % ch.upper().replace("_", " "))
        for k, v in data.items():
            if isinstance(v, float):
                lines.append("  %s: $%.2f" % (k, v) if "spend" in k or "cost" in k or "revenue" in k or "cpl" in k else "  %s: %.1f%%" % (k, v) if "rate" in k else "  %s: %s" % (k, v))
            else:
                lines.append("  %s: %s" % (k, v))
        lines.append("")

    totals = report.get("totals", {})
    if totals:
        lines.append("TOTALS:")
        lines.append("  Revenue: $%.0f" % totals.get("revenue", 0))
        lines.append("  Spend: $%.0f" % totals.get("spend", 0))
        lines.append("  Profit: $%.0f" % totals.get("profit", 0))
        lines.append("  ROI: %s" % totals.get("roi", "n/a"))

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Weekly ROI report")
    parser.add_argument("--telegram", action="store_true", help="Send via Telegram")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    report = generate_report()

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("\n" + format_report(report))

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
