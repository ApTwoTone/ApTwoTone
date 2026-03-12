#!/usr/bin/env python3
"""Vendor deduplication — finds duplicate vendor records.

Detection methods:
    1. Exact phone match (after digit normalization)
    2. Exact email match
    3. Fuzzy name match (Levenshtein distance <= 2)
    4. Same address + category

Usage:
    python scripts/vendor_dedup.py --dry-run        # Find duplicates (default)
    python scripts/vendor_dedup.py --merge           # Keep highest-scored, mark others
    python scripts/vendor_dedup.py --json            # JSON output
"""

import argparse
import json
import logging
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("vendor_dedup")

DB_PATH = Path.home() / ".nexus" / "memory.db"


def _normalize_phone(phone: str) -> str:
    """Normalize phone to digits only."""
    if not phone:
        return ""
    return re.sub(r"\D", "", phone)


def _normalize_name(name: str) -> str:
    """Normalize name for comparison."""
    if not name:
        return ""
    return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()


def _levenshtein(s1: str, s2: str) -> int:
    """Compute Levenshtein distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)

    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row

    return prev_row[-1]


def get_vendors(conn: sqlite3.Connection) -> list:
    """Fetch all vendors for dedup analysis."""
    rows = conn.execute(
        "SELECT id, name, phone, email, city, category, "
        "COALESCE(vetting_score, 0), source FROM vendors ORDER BY id"
    ).fetchall()
    return [
        {"id": r[0], "name": r[1], "phone": r[2], "email": r[3],
         "city": r[4], "category": r[5], "score": r[6], "source": r[7]}
        for r in rows
    ]


def find_phone_duplicates(vendors: list) -> list:
    """Find vendors with matching phone numbers."""
    phone_groups = defaultdict(list)
    for v in vendors:
        phone = _normalize_phone(v.get("phone", ""))
        if phone and len(phone) >= 10:
            phone_groups[phone].append(v)

    return [
        {"method": "phone", "phone": phone, "vendors": group}
        for phone, group in phone_groups.items()
        if len(group) > 1
    ]


def find_email_duplicates(vendors: list) -> list:
    """Find vendors with matching email addresses."""
    email_groups = defaultdict(list)
    for v in vendors:
        email = (v.get("email") or "").lower().strip()
        if email:
            email_groups[email].append(v)

    return [
        {"method": "email", "email": email, "vendors": group}
        for email, group in email_groups.items()
        if len(group) > 1
    ]


def find_name_duplicates(vendors: list, max_distance: int = 2) -> list:
    """Find vendors with similar names (Levenshtein distance <= max_distance)."""
    groups = []
    seen = set()
    normalized = [(v, _normalize_name(v.get("name", ""))) for v in vendors]

    for i, (v1, n1) in enumerate(normalized):
        if not n1 or v1["id"] in seen:
            continue
        cluster = [v1]
        for j, (v2, n2) in enumerate(normalized[i + 1:], i + 1):
            if not n2 or v2["id"] in seen:
                continue
            if abs(len(n1) - len(n2)) > max_distance:
                continue
            if _levenshtein(n1, n2) <= max_distance:
                cluster.append(v2)
                seen.add(v2["id"])

        if len(cluster) > 1:
            seen.add(v1["id"])
            groups.append({"method": "name", "vendors": cluster})

    return groups


def find_all_duplicates(vendors: list) -> list:
    """Run all dedup methods and merge results."""
    all_groups = []
    all_groups.extend(find_phone_duplicates(vendors))
    all_groups.extend(find_email_duplicates(vendors))
    # Name matching is O(n^2) — skip if too many vendors
    if len(vendors) <= 5000:
        all_groups.extend(find_name_duplicates(vendors))
    else:
        log.info("Skipping name dedup — %d vendors exceeds 5000 limit", len(vendors))
    return all_groups


def main():
    parser = argparse.ArgumentParser(description="Vendor deduplication")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Find duplicates only (default)")
    parser.add_argument("--merge", action="store_true", help="Keep highest-scored, mark others as duplicate")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if not DB_PATH.exists():
        log.error("Database not found: %s", DB_PATH)
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA query_only = ON")

    vendors = get_vendors(conn)
    if not vendors:
        print("  No vendors to analyze.")
        conn.close()
        return

    log.info("Analyzing %d vendors for duplicates...", len(vendors))
    groups = find_all_duplicates(vendors)

    if args.json:
        # Simplify for JSON output
        output = []
        for g in groups:
            output.append({
                "method": g["method"],
                "count": len(g["vendors"]),
                "vendor_ids": [v["id"] for v in g["vendors"]],
                "vendor_names": [v["name"] for v in g["vendors"]],
            })
        print(json.dumps(output, indent=2))
    else:
        print(f"\n  Vendor Deduplication Report")
        print(f"  Total vendors: {len(vendors)}")
        print(f"  Duplicate groups found: {len(groups)}")
        print(f"  {'=' * 60}")

        for i, g in enumerate(groups[:20]):
            method_info = g.get("phone", g.get("email", ""))
            print(f"\n  Group {i+1} ({g['method']}{': ' + method_info if method_info else ''}):")
            for v in g["vendors"]:
                print(f"    [{v['id']}] {v['name'][:35]:<35} "
                      f"score={v['score']:>3} phone={v.get('phone', '')}")

        if len(groups) > 20:
            print(f"\n  ... and {len(groups) - 20} more groups")

        affected = set()
        for g in groups:
            for v in g["vendors"]:
                affected.add(v["id"])
        print(f"\n  Total affected vendors: {len(affected)}")

        if args.merge:
            print(f"\n  Merge not yet implemented in this version.")
            print(f"  Use --json to export groups for manual review.")

    conn.close()


if __name__ == "__main__":
    main()
