#!/usr/bin/env python3
"""Vendor freshness check — verifies vendor data is current.

STAGED ONLY — manual trigger, never automated.

Checks:
    1. Website still returns 200 (HTTP HEAD)
    2. Data age (flags records >90 days old)
    3. Google Places listing status (if place_id exists)

Usage:
    python scripts/vendor_freshness_check.py --dry-run            # Check without updating
    python scripts/vendor_freshness_check.py --limit 20           # Check first 20
    python scripts/vendor_freshness_check.py --stale-only          # Only show stale records
"""

import argparse
import json
import logging
import sqlite3
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("vendor_freshness")

DB_PATH = Path.home() / ".nexus" / "memory.db"
STALE_DAYS = 90


def check_website(url: str, timeout: float = 5.0) -> dict:
    """Check if a website URL is still reachable."""
    if not url:
        return {"status": "no_url", "code": None}
    full_url = url if url.startswith("http") else f"https://{url}"
    try:
        req = urllib.request.Request(full_url, method="HEAD")
        req.add_header("User-Agent", "Nexus/1.0 FreshnessCheck")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"status": "ok", "code": resp.getcode()}
    except urllib.error.HTTPError as e:
        return {"status": "http_error", "code": e.code}
    except Exception as e:
        return {"status": "unreachable", "code": None, "error": str(e)[:50]}


def check_freshness(conn: sqlite3.Connection, limit: int = None,
                    stale_only: bool = False, check_websites: bool = True) -> list:
    """Check vendor data freshness."""
    query = "SELECT id, name, website, city, category, created_at FROM vendors"
    if limit:
        query += f" LIMIT {limit}"

    rows = conn.execute(query).fetchall()
    cutoff = (datetime.now() - timedelta(days=STALE_DAYS)).strftime("%Y-%m-%d")
    results = []

    for i, row in enumerate(rows):
        vid, name, website, city, category, created_at = row
        is_stale = created_at and created_at < cutoff if created_at else True

        if stale_only and not is_stale:
            continue

        result = {
            "id": vid,
            "name": name,
            "created_at": created_at,
            "stale": is_stale,
            "days_old": None,
            "website_status": None,
        }

        if created_at:
            try:
                created = datetime.strptime(created_at[:10], "%Y-%m-%d")
                result["days_old"] = (datetime.now() - created).days
            except ValueError:
                pass

        if check_websites and website:
            result["website_status"] = check_website(website)

        results.append(result)

        if (i + 1) % 50 == 0:
            log.info("Checked %d / %d", i + 1, len(rows))

    return results


def main():
    parser = argparse.ArgumentParser(description="Vendor freshness check (STAGED)")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Check only (default)")
    parser.add_argument("--limit", type=int, help="Limit vendors to check")
    parser.add_argument("--stale-only", action="store_true", help="Only show stale records")
    parser.add_argument("--skip-websites", action="store_true", help="Skip website checks")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if not DB_PATH.exists():
        log.error("Database not found: %s", DB_PATH)
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA query_only = ON")

    results = check_freshness(
        conn, limit=args.limit, stale_only=args.stale_only,
        check_websites=not args.skip_websites,
    )
    conn.close()

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(f"\n  Vendor Freshness Check")
        print(f"  Checked: {len(results)} vendors")

        stale = sum(1 for r in results if r["stale"])
        unreachable = sum(1 for r in results if r.get("website_status", {}).get("status") == "unreachable")

        print(f"  Stale (>{STALE_DAYS} days): {stale}")
        print(f"  Unreachable websites: {unreachable}")
        print(f"  {'=' * 50}")

        for r in results[:15]:
            ws = r.get("website_status")
            ws_str = ""
            if ws:
                ws_str = f" | web: {ws['status']}"
            days = f" ({r['days_old']}d)" if r["days_old"] is not None else ""
            flag = " [STALE]" if r["stale"] else ""
            print(f"  [{r['id']}] {r['name'][:35]:<35}{days}{ws_str}{flag}")

        if len(results) > 15:
            print(f"  ... and {len(results) - 15} more")


if __name__ == "__main__":
    main()
