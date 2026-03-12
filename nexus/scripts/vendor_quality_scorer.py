#!/usr/bin/env python3
"""Vendor quality scorer — multi-dimension scoring system (0-100).

Scoring dimensions:
    Phone validity:    25 pts (no 555, no repeated digits, proper format)
    Website reachable: 25 pts (HTTP HEAD returns 200/301/302)
    Email validity:    20 pts (MX record exists on domain)
    Category match:    15 pts (in approved categories list)
    Location match:    15 pts (SFV/Greater LA area)

Usage:
    python scripts/vendor_quality_scorer.py --dry-run           # Show scores without writing
    python scripts/vendor_quality_scorer.py --dry-run --limit 10
    python scripts/vendor_quality_scorer.py --update             # Write scores to DB
    python scripts/vendor_quality_scorer.py --summary            # Score distribution summary
"""

import argparse
import json
import logging
import re
import socket
import sqlite3
import sys
import urllib.request
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("vendor_scorer")

DB_PATH = Path.home() / ".nexus" / "memory.db"

APPROVED_CATEGORIES = {
    "Wedding Venue", "Event Venue", "Banquet Hall", "Catering",
    "Wedding Planner", "Event Planner", "Party Rental",
    "Country Club", "Restaurant", "Hotel", "Winery",
    "Community Center", "DJ", "Photographer", "Videographer",
    "Florist", "Baker", "Decorator", "Limo Service",
    "Photo Booth", "Tent Rental",
}

FAKE_PHONE_PATTERNS = [
    re.compile(r"555"),
    re.compile(r"(\d)\1{3,}"),
    re.compile(r"1234|2345|3456|4567|5678|6789|7890"),
    re.compile(r"4321|5432|6543|7654|8765|9876"),
]

SFV_LA_CITIES = {
    "san fernando", "sylmar", "pacoima", "arleta", "sun valley",
    "north hollywood", "van nuys", "panorama city", "mission hills",
    "northridge", "granada hills", "chatsworth", "porter ranch",
    "reseda", "tarzana", "encino", "sherman oaks", "studio city",
    "woodland hills", "canoga park", "winnetka", "west hills",
    "burbank", "glendale", "pasadena", "los angeles", "la",
    "santa clarita", "valencia", "newhall", "saugus",
    "calabasas", "agoura hills", "thousand oaks", "simi valley",
    "lancaster", "palmdale", "downey", "long beach", "torrance",
    "inglewood", "compton", "pomona", "alhambra", "monterey park",
    "west covina", "whittier", "azusa", "covina",
}


def score_phone(phone: Optional[str]) -> int:
    """Score phone validity (0-25)."""
    if not phone:
        return 0
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 10:
        return 5  # Partial phone
    for pattern in FAKE_PHONE_PATTERNS:
        if pattern.search(digits):
            return 0  # Fake pattern
    return 25


def score_website(website: Optional[str], timeout: float = 5.0) -> int:
    """Score website reachability (0-25). Uses HEAD request."""
    if not website:
        return 0
    url = website if website.startswith("http") else f"https://{website}"
    try:
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", "Nexus/1.0 VendorCheck")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.getcode() in (200, 301, 302, 303, 307, 308):
                return 25
            return 10  # Responded but unusual status
    except Exception:
        return 0


def score_email(email: Optional[str]) -> int:
    """Score email validity (0-20). Checks MX record on domain."""
    if not email or "@" not in email:
        return 0
    domain = email.split("@")[1]
    # Skip obviously fake domains
    if domain in ("example.com", "test.com", "fake.com"):
        return 0
    try:
        import subprocess
        result = subprocess.run(
            ["dig", "+short", "MX", domain],
            capture_output=True, text=True, timeout=5,
        )
        if result.stdout.strip():
            return 20  # MX record exists
        return 5  # Domain exists but no MX
    except Exception:
        return 5  # Can't check, give partial credit


def score_category(category: Optional[str]) -> int:
    """Score category match (0-15)."""
    if not category:
        return 0
    cat_lower = category.lower().replace("_", " ")
    for approved in APPROVED_CATEGORIES:
        if approved.lower() in cat_lower or cat_lower in approved.lower():
            return 15
    return 0


def score_location(city: Optional[str]) -> int:
    """Score location match (0-15). Checks if city is in SFV/Greater LA."""
    if not city:
        return 0
    city_lower = city.lower().strip()
    if city_lower in SFV_LA_CITIES:
        return 15
    # Partial match
    for known in SFV_LA_CITIES:
        if known in city_lower or city_lower in known:
            return 10
    return 0


def score_vendor(vendor: dict, check_website: bool = False) -> dict:
    """Score a single vendor across all dimensions."""
    phone_score = score_phone(vendor.get("phone"))
    web_score = score_website(vendor.get("website")) if check_website else 0
    email_score = score_email(vendor.get("email"))
    cat_score = score_category(vendor.get("category"))
    loc_score = score_location(vendor.get("city"))

    total = phone_score + web_score + email_score + cat_score + loc_score
    max_possible = 100 if check_website else 75  # Without website check

    return {
        "vendor_id": vendor.get("id"),
        "name": vendor.get("name"),
        "total": total,
        "max_possible": max_possible,
        "normalized": round(total / max_possible * 100) if max_possible > 0 else 0,
        "breakdown": {
            "phone": phone_score,
            "website": web_score,
            "email": email_score,
            "category": cat_score,
            "location": loc_score,
        },
    }


def get_vendors(conn: sqlite3.Connection, limit: int = None) -> list:
    """Fetch vendors for scoring."""
    query = "SELECT id, name, phone, email, website, city, category FROM vendors"
    if limit:
        query += f" LIMIT {limit}"
    rows = conn.execute(query).fetchall()
    return [
        {"id": r[0], "name": r[1], "phone": r[2], "email": r[3],
         "website": r[4], "city": r[5], "category": r[6]}
        for r in rows
    ]


def get_summary(conn: sqlite3.Connection) -> dict:
    """Get score distribution summary."""
    rows = conn.execute(
        "SELECT vetting_score, COUNT(*) FROM vendors GROUP BY vetting_score ORDER BY vetting_score"
    ).fetchall()

    buckets = {"0-19": 0, "20-39": 0, "40-59": 0, "60-79": 0, "80-100": 0, "null": 0}
    for score, count in rows:
        if score is None:
            buckets["null"] += count
        elif score < 20:
            buckets["0-19"] += count
        elif score < 40:
            buckets["20-39"] += count
        elif score < 60:
            buckets["40-59"] += count
        elif score < 80:
            buckets["60-79"] += count
        else:
            buckets["80-100"] += count

    total = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
    eligible = conn.execute(
        "SELECT COUNT(*) FROM vendors WHERE vetting_score >= 80"
    ).fetchone()[0]

    return {"total": total, "eligible": eligible, "distribution": buckets}


def main():
    parser = argparse.ArgumentParser(description="Vendor quality scorer")
    parser.add_argument("--dry-run", action="store_true", help="Show scores without writing")
    parser.add_argument("--update", action="store_true", help="Write scores to DB")
    parser.add_argument("--summary", action="store_true", help="Score distribution summary")
    parser.add_argument("--limit", type=int, help="Limit vendors to score")
    parser.add_argument("--check-websites", action="store_true", help="Include website checks (slow)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if not DB_PATH.exists():
        log.error("Database not found: %s", DB_PATH)
        sys.exit(1)

    if args.summary:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("PRAGMA query_only = ON")
        summary = get_summary(conn)
        conn.close()
        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            print(f"\n  Vendor Score Distribution")
            print(f"  Total: {summary['total']}, Eligible (>=80): {summary['eligible']}")
            for bucket, count in summary["distribution"].items():
                bar = "#" * min(count // 10, 50)
                print(f"    {bucket:>6}: {count:>5} {bar}")
        return

    conn = sqlite3.connect(str(DB_PATH))
    if args.dry_run:
        conn.execute("PRAGMA query_only = ON")

    vendors = get_vendors(conn, args.limit)
    if not vendors:
        print("  No vendors to score.")
        conn.close()
        return

    log.info("Scoring %d vendors (websites=%s)", len(vendors), args.check_websites)
    results = []
    for i, v in enumerate(vendors):
        result = score_vendor(v, check_website=args.check_websites)
        results.append(result)
        if (i + 1) % 100 == 0:
            log.info("Scored %d / %d", i + 1, len(vendors))

    if args.update and not args.dry_run:
        for r in results:
            conn.execute(
                "UPDATE vendors SET vetting_score = ? WHERE id = ?",
                (r["normalized"], r["vendor_id"]),
            )
        conn.commit()
        log.info("Updated %d vendor scores", len(results))

    conn.close()

    if args.json:
        print(json.dumps(results[:50] if not args.json else results, indent=2))
    else:
        print(f"\n  Vendor Quality Scores ({len(results)} vendors)")
        print(f"  {'=' * 60}")
        for r in results[:20]:
            bd = r["breakdown"]
            print(f"  [{r['vendor_id']}] {r['name'][:30]:<30} "
                  f"Score: {r['normalized']:>3}/100 "
                  f"(P:{bd['phone']} W:{bd['website']} E:{bd['email']} "
                  f"C:{bd['category']} L:{bd['location']})")
        if len(results) > 20:
            print(f"  ... and {len(results) - 20} more")

        avg = sum(r["normalized"] for r in results) / len(results) if results else 0
        high = sum(1 for r in results if r["normalized"] >= 80)
        print(f"\n  Average: {avg:.0f}/100 | Eligible (>=80): {high}/{len(results)}")
        if args.dry_run:
            print(f"  (dry run — no scores written)")


if __name__ == "__main__":
    main()
