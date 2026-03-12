#!/usr/bin/env python3
"""
Run venue intelligence assessments.

Usage:
    python3 scripts/run_venue_intel.py                        # Assess 10 unassessed venues
    python3 scripts/run_venue_intel.py --vendor-id 42         # Assess specific vendor
    python3 scripts/run_venue_intel.py --category wedding_venue --limit 20
    python3 scripts/run_venue_intel.py --report               # Print ranked report
    python3 scripts/run_venue_intel.py --daemon               # Run continuous loop (5min)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.venue_intel import VenueIntelAgent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
log = logging.getLogger("run_venue_intel")
DB_PATH = Path.home() / ".nexus" / "memory.db"


def _conn():
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    return c


async def run_single(vendor_id: int):
    agent = VenueIntelAgent()
    result = await agent.assess_venue(vendor_id)
    print(json.dumps(result, indent=2, default=str))


async def run_batch(category: str = "", limit: int = 10):
    conn = _conn()
    if category:
        rows = conn.execute(
            """
            SELECT id FROM vendors
            WHERE venue_assessed = 0
              AND category = ?
              AND (website IS NOT NULL AND website != '' AND website != 'N/A')
            ORDER BY referral_score DESC, id ASC
            LIMIT ?
            """,
            (category, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id FROM vendors
            WHERE venue_assessed = 0
              AND category IN (
                  'wedding_venue', 'venue', 'event_venue', 'banquet_hall',
                  'country_club', 'hotel_venue', 'winery', 'ranch',
                  'garden_venue', 'estate', 'barn_venue', 'park_venue'
              )
              AND (website IS NOT NULL AND website != '' AND website != 'N/A')
            ORDER BY referral_score DESC, campaign_quality DESC, id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    conn.close()

    if not rows:
        print("No unassessed venues found matching criteria.")
        return

    vendor_ids = [r["id"] for r in rows]
    print(f"Assessing {len(vendor_ids)} venues...")

    agent = VenueIntelAgent()
    results = await agent.assess_venues(vendor_ids)

    high = [r for r in results if r.get("ok") and r.get("fit_score", 0) >= 7]
    failed = [r for r in results if not r.get("ok")]

    print(f"\nDone: {len(results)} assessed")
    print(f"  High-value (7+): {len(high)}")
    print(f"  Failed: {len(failed)}")

    if high:
        print("\n--- HIGH-VALUE TARGETS ---")
        for r in sorted(high, key=lambda x: x.get("fit_score", 0), reverse=True):
            print(f"  {r.get('fit_score', 0)}/10 | vendor_id={r['vendor_id']} | "
                  f"{r.get('has_restrooms', '?')} restrooms | "
                  f"{r.get('fit_reasoning', '')}")


def print_report(min_score: int = 5):
    agent = VenueIntelAgent()
    targets = agent.get_top_targets(min_score=min_score, limit=50)
    stats = agent.get_stats()

    print(f"=== VENUE INTELLIGENCE REPORT ===")
    print(f"Total assessed: {stats['total_assessed']}")
    print(f"Remaining: {stats['unassessed_remaining']}")
    print(f"High-value (7+): {stats['by_score']['high_value']}")
    print(f"Medium (4-6): {stats['by_score']['medium']}")
    print(f"Low (1-3): {stats['by_score']['low']}")
    print(f"Failed: {stats['by_score']['failed']}")
    print()

    if not targets:
        print("No targets found above minimum score.")
        return

    print(f"--- TOP TARGETS (score >= {min_score}) ---")
    for t in targets:
        phone = t.get("phone") or "no phone"
        city = t.get("city") or "?"
        print(
            f"  {t['fit_score']}/10 | {t['vendor_name']} ({city}) | "
            f"{phone} | {t.get('has_restrooms', '?')} restrooms | "
            f"outdoor:{t.get('outdoor_capacity', 0)} | "
            f"{t.get('fit_reasoning', '')}"
        )


async def run_daemon(interval: int = 300):
    agent = VenueIntelAgent()
    log.info("Starting venue intel daemon (cycle every %ds)", interval)

    while True:
        try:
            summary = await agent.daemon_cycle(limit=5)
            log.info(
                "Cycle done: %d assessed, %d high-value",
                summary["assessed"], summary["high_value"],
            )
        except Exception as e:
            log.error("Daemon cycle failed: %s", e)

        await asyncio.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Venue Intelligence Agent")
    parser.add_argument("--vendor-id", type=int, help="Assess a specific vendor")
    parser.add_argument("--category", type=str, default="", help="Filter by category")
    parser.add_argument("--limit", type=int, default=10, help="Max venues to assess")
    parser.add_argument("--report", action="store_true", help="Print ranked report")
    parser.add_argument("--daemon", action="store_true", help="Run continuous loop")
    parser.add_argument("--min-score", type=int, default=5, help="Min score for report")
    args = parser.parse_args()

    if args.report:
        print_report(min_score=args.min_score)
    elif args.daemon:
        asyncio.run(run_daemon())
    elif args.vendor_id:
        asyncio.run(run_single(args.vendor_id))
    else:
        asyncio.run(run_batch(category=args.category, limit=args.limit))


if __name__ == "__main__":
    main()
