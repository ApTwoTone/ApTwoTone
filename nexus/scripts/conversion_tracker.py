#!/usr/bin/env python3
"""Conversion tracker — logs funnel stage transitions and generates reports.

Importable by other modules:
    from scripts.conversion_tracker import log_stage_transition, get_funnel_report

Usage:
    python scripts/conversion_tracker.py --report          # 7-day funnel report
    python scripts/conversion_tracker.py --report --days 30 # 30-day report
    python scripts/conversion_tracker.py --json             # JSON output
"""

import argparse
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("conversion_tracker")

DB_PATH = Path.home() / ".nexus" / "memory.db"

STAGE_ORDER = [
    "impression", "click", "inquiry", "reply", "call",
    "quote_sent", "quote_viewed", "negotiating", "booked", "completed", "lost"
]


def log_stage_transition(source, stage, lead_id=None, vendor_id=None, notes=None, revenue=0):
    """Log a conversion funnel event."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "INSERT INTO conversion_funnel (lead_id, vendor_id, source, stage, notes, revenue) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (lead_id, vendor_id, source, stage, notes, revenue)
    )
    conn.commit()
    conn.close()
    log.info("Funnel: %s -> %s (lead=%s, vendor=%s)", source, stage, lead_id, vendor_id)


def get_funnel_report(days=7):
    """Generate conversion funnel report for the last N days."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA query_only = ON")

    # Stage counts by source
    stages = conn.execute(
        "SELECT source, stage, COUNT(*) as cnt "
        "FROM conversion_funnel "
        "WHERE stage_entered_at > datetime('now', ?) "
        "GROUP BY source, stage "
        "ORDER BY source, "
        "CASE stage "
        "  WHEN 'impression' THEN 1 WHEN 'click' THEN 2 "
        "  WHEN 'inquiry' THEN 3 WHEN 'reply' THEN 4 "
        "  WHEN 'call' THEN 5 WHEN 'quote_sent' THEN 6 "
        "  WHEN 'quote_viewed' THEN 7 WHEN 'negotiating' THEN 8 "
        "  WHEN 'booked' THEN 9 WHEN 'completed' THEN 10 "
        "  WHEN 'lost' THEN 11 END",
        ("-%d days" % days,)
    ).fetchall()

    # Revenue by source
    revenue = conn.execute(
        "SELECT source, SUM(revenue) FROM conversion_funnel "
        "WHERE stage = 'booked' AND stage_entered_at > datetime('now', ?) "
        "GROUP BY source",
        ("-%d days" % days,)
    ).fetchall()

    # Total bookings
    bookings = conn.execute(
        "SELECT COUNT(*) FROM conversion_funnel "
        "WHERE stage = 'booked' AND stage_entered_at > datetime('now', ?)",
        ("-%d days" % days,)
    ).fetchone()[0]

    conn.close()

    # Build report
    report = {
        "period_days": days,
        "generated_at": datetime.now().isoformat(),
        "stages_by_source": {},
        "revenue_by_source": {r[0]: r[1] or 0 for r in revenue},
        "total_bookings": bookings,
    }

    for source, stage, count in stages:
        if source not in report["stages_by_source"]:
            report["stages_by_source"][source] = {}
        report["stages_by_source"][source][stage] = count

    return report


def format_report(report):
    """Format funnel report as human-readable text."""
    lines = [
        "CONVERSION FUNNEL REPORT",
        "Period: Last %d days" % report["period_days"],
        "Generated: %s" % report["generated_at"][:16],
        "",
    ]

    for source, stages in report["stages_by_source"].items():
        lines.append("  %s:" % source.upper())
        for stage in STAGE_ORDER:
            if stage in stages:
                lines.append("    %s: %d" % (stage, stages[stage]))
        rev = report["revenue_by_source"].get(source, 0)
        if rev:
            lines.append("    revenue: $%.0f" % rev)
        lines.append("")

    lines.append("Total bookings: %d" % report["total_bookings"])
    total_rev = sum(report["revenue_by_source"].values())
    if total_rev:
        lines.append("Total revenue: $%.0f" % total_rev)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Conversion tracker")
    parser.add_argument("--report", action="store_true", help="Generate funnel report")
    parser.add_argument("--days", type=int, default=7, help="Report period (days)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--log", nargs=4, metavar=("SOURCE", "STAGE", "LEAD_ID", "VENDOR_ID"),
                        help="Log a stage transition")
    args = parser.parse_args()

    if args.log:
        source, stage, lead_id, vendor_id = args.log
        lead_id = int(lead_id) if lead_id != "0" else None
        vendor_id = int(vendor_id) if vendor_id != "0" else None
        log_stage_transition(source, stage, lead_id, vendor_id)
        print("Logged: %s -> %s" % (source, stage))
    elif args.report or args.json:
        report = get_funnel_report(args.days)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print("\n" + format_report(report))
    else:
        report = get_funnel_report(args.days)
        print("\n" + format_report(report))


if __name__ == "__main__":
    main()
