#!/usr/bin/env python3
"""
Vendor Discovery Runner — Searches across Google Maps, Google, and Yelp
for event vendors who could refer bathroom rental clients to Zoar.

Usage:
    python3 scripts/vendor_discovery.py                         # All 13 categories
    python3 scripts/vendor_discovery.py wedding_venue catering  # Specific categories
    python3 scripts/vendor_discovery.py --list                  # List available categories
    python3 scripts/vendor_discovery.py --stats                 # Show current DB stats

Sends Telegram notifications at 50, 100, 250, 500 vendor milestones.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from urllib.request import Request, urlopen

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.vendor_research import VendorResearcher, CATEGORY_QUERIES
from core.vendor_db import (
    init_vendor_tables,
    bulk_save_vendors,
    get_vendor_stats,
)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vendor_discovery")

CONFIG_PATH = Path.home() / ".nexus" / "config.json"

# Milestone thresholds for Telegram notifications
MILESTONES = {50, 100, 250, 500, 1000}


def _load_config():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass
    return {}


def _send_telegram(message: str):
    """Send a Telegram notification. Non-blocking, ignores errors."""
    cfg = _load_config()
    token = cfg.get("telegram_token", "")
    chat_ids = cfg.get("telegram_chat_ids", [])
    if not token or not chat_ids:
        log.warning("Telegram not configured — skipping notification")
        return

    for cid in chat_ids:
        try:
            url = "https://api.telegram.org/bot%s/sendMessage" % token
            payload = json.dumps({
                "chat_id": str(cid),
                "text": message,
                "parse_mode": "Markdown",
            }).encode()
            req = Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urlopen(req, timeout=10) as resp:
                resp.read()
            log.info("Telegram notification sent to %s", cid)
        except Exception as e:
            log.error("Telegram send failed for %s: %s", cid, e)


def _print_stats():
    """Print current vendor database statistics."""
    stats = get_vendor_stats()
    print("\n" + "=" * 60)
    print("VENDOR DATABASE STATISTICS")
    print("=" * 60)
    print("Total vendors: %d" % stats["total"])
    print()

    if stats["by_category"]:
        print("By Category:")
        for cat, cnt in sorted(stats["by_category"].items(), key=lambda x: -x[1]):
            print("  %-30s %d" % (cat, cnt))
        print()

    if stats["by_source"]:
        print("By Source:")
        for src, cnt in sorted(stats["by_source"].items(), key=lambda x: -x[1]):
            print("  %-30s %d" % (src, cnt))
        print()

    if stats["by_status"]:
        print("By Status:")
        for st, cnt in sorted(stats["by_status"].items(), key=lambda x: -x[1]):
            print("  %-30s %d" % (st, cnt))
        print()

    if stats["by_city"]:
        print("Top Cities:")
        for city, cnt in list(stats["by_city"].items())[:15]:
            print("  %-30s %d" % (city, cnt))
        print()

    print("Outreach: %d total | %d pending | %d sent | %d replied" % (
        stats["outreach_total"],
        stats["outreach_pending"],
        stats["outreach_sent"],
        stats["outreach_replied"],
    ))
    print("=" * 60 + "\n")


async def run_discovery(categories: list, locations: list = None):
    """Run vendor discovery across specified categories.

    Args:
        categories: List of category keys from CATEGORY_QUERIES.
        locations: Optional list of locations to search. Uses defaults if None.
    """
    # Initialize database tables
    init_vendor_tables()

    researcher = VendorResearcher()
    total_created = 0
    total_updated = 0
    total_skipped = 0
    total_by_category = {}
    total_by_source = {}
    notified_milestones = set()

    log.info("Starting vendor discovery for %d categories", len(categories))

    _send_telegram(
        "*VENDOR DISCOVERY STARTED*\n"
        "Searching %d categories across Google Maps, Google, and Yelp\n"
        "Categories: %s" % (
            len(categories),
            ", ".join(categories[:5]) + ("..." if len(categories) > 5 else ""),
        )
    )

    for i, category in enumerate(categories, 1):
        log.info(
            "[%d/%d] Searching category: %s",
            i, len(categories), category,
        )

        try:
            results = await researcher.search_category(category, locations)

            if not results:
                log.warning("No results for category: %s", category)
                continue

            # Save to database
            counts = bulk_save_vendors(results)

            total_created += counts["created"]
            total_updated += counts["updated"]
            total_skipped += counts["skipped"]
            total_by_category[category] = len(results)

            # Track sources
            for r in results:
                src = r.get("source", "unknown")
                total_by_source[src] = total_by_source.get(src, 0) + 1

            log.info(
                "  Category '%s': %d found, %d new, %d updated, %d skipped",
                category, len(results), counts["created"], counts["updated"], counts["skipped"],
            )

            # Check milestones
            running_total = total_created + total_updated
            for milestone in MILESTONES:
                if running_total >= milestone and milestone not in notified_milestones:
                    notified_milestones.add(milestone)
                    _send_telegram(
                        "*VENDOR DISCOVERY MILESTONE: %d+ vendors*\n"
                        "Created: %d | Updated: %d\n"
                        "Progress: %d/%d categories complete" % (
                            milestone, total_created, total_updated,
                            i, len(categories),
                        )
                    )
                    log.info("Milestone reached: %d vendors", milestone)

            # Progress report every 50 new vendors
            if total_created > 0 and total_created % 50 < counts["created"]:
                log.info(
                    "Progress: %d vendors created so far (%d/%d categories)",
                    total_created, i, len(categories),
                )

        except Exception as e:
            log.error("Error processing category '%s': %s", category, e, exc_info=True)
            continue

    # Final summary
    summary_lines = [
        "*VENDOR DISCOVERY COMPLETE*",
        "=" * 30,
        "Total found: %d" % (total_created + total_updated + total_skipped),
        "New vendors: %d" % total_created,
        "Updated: %d" % total_updated,
        "Duplicates skipped: %d" % total_skipped,
        "",
        "*By Category:*",
    ]
    for cat, cnt in sorted(total_by_category.items(), key=lambda x: -x[1]):
        summary_lines.append("  %s: %d" % (cat, cnt))

    summary_lines.append("")
    summary_lines.append("*By Source:*")
    for src, cnt in sorted(total_by_source.items(), key=lambda x: -x[1]):
        summary_lines.append("  %s: %d" % (src, cnt))

    summary_text = "\n".join(summary_lines)

    log.info("\n%s", summary_text)
    _send_telegram(summary_text)

    # Print final stats from DB
    _print_stats()

    return {
        "total_created": total_created,
        "total_updated": total_updated,
        "total_skipped": total_skipped,
        "by_category": total_by_category,
        "by_source": total_by_source,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Discover event vendors for Zoar Bathroom Rentals referral partnerships"
    )
    parser.add_argument(
        "categories",
        nargs="*",
        help="Category names to search (default: all). Use --list to see available.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all available vendor categories and exit",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show current vendor database statistics and exit",
    )
    parser.add_argument(
        "--locations",
        nargs="*",
        help="Override default search locations",
    )
    args = parser.parse_args()

    if args.list:
        print("\nAvailable vendor categories:")
        print("=" * 50)
        for cat, queries in CATEGORY_QUERIES.items():
            print("  %-25s (%d search queries)" % (cat, len(queries)))
        print("\nUsage: python3 scripts/vendor_discovery.py wedding_venue catering dj_entertainment")
        return

    if args.stats:
        init_vendor_tables()
        _print_stats()
        return

    # Determine which categories to search
    all_categories = list(CATEGORY_QUERIES.keys())
    if args.categories:
        # Validate categories
        invalid = [c for c in args.categories if c not in CATEGORY_QUERIES]
        if invalid:
            log.error("Unknown categories: %s", ", ".join(invalid))
            log.info("Use --list to see available categories")
            sys.exit(1)
        categories = args.categories
    else:
        categories = all_categories

    # Run the discovery
    result = asyncio.run(run_discovery(categories, args.locations))
    log.info("Discovery complete: %d new vendors found", result["total_created"])


if __name__ == "__main__":
    main()
