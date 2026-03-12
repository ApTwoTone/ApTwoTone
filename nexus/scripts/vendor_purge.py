#!/usr/bin/env python3
"""Vendor purge script — removes AI-hallucinated vendor data.

STAGED ONLY — requires --execute flag and manual confirmation to actually delete.
Default mode is dry-run which shows what WOULD be deleted without touching the DB.

Safety features:
- Pre-purge backup (DB copy + CSV export)
- KEEP set preserves any valuable vendors
- Dry run by default
- --execute requires typing "CONFIRM PURGE"
- Batch deletes (500 at a time) to avoid locking
- VACUUM after purge
- Full audit trail to AUDITOR_LOG.md
"""

import argparse
import csv
import json
import logging
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("vendor_purge")

DB_PATH = Path.home() / ".nexus" / "memory.db"
BACKUP_DIR = Path.home() / ".nexus" / "backups"
AUDITOR_LOG = Path(__file__).resolve().parent.parent / "AUDITOR_LOG.md"

# ── KEEP criteria ─────────────────────────────────────────────────────────────
# Vendors matching ANY of these conditions are preserved (never deleted).

KEEP_SQL = """
SELECT DISTINCT id FROM vendors WHERE
    -- Vetted with high score
    (vetting_status = 'vetted' AND COALESCE(vetting_score, 0) >= 80)
    -- Campaign eligible
    OR COALESCE(campaign_eligible, 0) = 1
    -- Has outreach sent or replied
    OR id IN (SELECT vendor_id FROM vendor_outreach WHERE status IN ('sent', 'replied', 'responded'))
    -- Created before the AI research flood (March 6 2026)
    OR created_at < '2026-03-06'
    -- Manually marked as partner/contacted/responded
    OR outreach_status IN ('contacted', 'responded', 'partner')
    -- From Google Places (real verified data)
    OR source = 'google_places'
"""


def backup_database(conn: sqlite3.Connection) -> dict:
    """Create pre-purge backup: DB file copy + CSV export of all vendors."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    # Copy the DB file
    db_backup = BACKUP_DIR / f"memory_pre_purge_{timestamp}.db"
    shutil.copy2(DB_PATH, db_backup)
    db_size = db_backup.stat().st_size / (1024 * 1024)
    log.info("DB backup: %s (%.1f MB)", db_backup, db_size)

    # CSV export of all vendors
    csv_path = BACKUP_DIR / f"vendors_pre_purge_{timestamp}.csv"
    cur = conn.execute("SELECT * FROM vendors")
    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows(rows)

    log.info("CSV backup: %s (%d rows)", csv_path, len(rows))

    return {
        "db_backup": str(db_backup),
        "csv_backup": str(csv_path),
        "db_size_mb": round(db_size, 1),
        "vendor_count": len(rows),
        "timestamp": timestamp,
    }


def get_keep_set(conn: sqlite3.Connection) -> set:
    """Get IDs of vendors that should be preserved."""
    rows = conn.execute(KEEP_SQL).fetchall()
    return {r[0] for r in rows}


def get_delete_set(conn: sqlite3.Connection, keep_ids: set) -> list:
    """Get IDs of vendors that will be deleted (everything NOT in keep set)."""
    all_ids = conn.execute("SELECT id FROM vendors").fetchall()
    return [r[0] for r in all_ids if r[0] not in keep_ids]


def sample_vendors(conn: sqlite3.Connection, ids: list, n: int = 10) -> list:
    """Get sample vendor records for preview."""
    sample_ids = ids[:n] if len(ids) >= n else ids
    if not sample_ids:
        return []
    placeholders = ",".join("?" * len(sample_ids))
    rows = conn.execute(
        f"SELECT id, name, city, category, phone, source, vetting_status FROM vendors WHERE id IN ({placeholders})",
        sample_ids,
    ).fetchall()
    return [
        {"id": r[0], "name": r[1], "city": r[2], "category": r[3],
         "phone": r[4], "source": r[5], "vetting_status": r[6]}
        for r in rows
    ]


def preserved_vendors(conn: sqlite3.Connection, keep_ids: set) -> list:
    """Get details of all preserved vendors."""
    if not keep_ids:
        return []
    placeholders = ",".join("?" * len(keep_ids))
    rows = conn.execute(
        f"SELECT id, name, city, category, phone, email, source, vetting_status, "
        f"COALESCE(vetting_score, 0), outreach_status "
        f"FROM vendors WHERE id IN ({placeholders})",
        list(keep_ids),
    ).fetchall()
    return [
        {"id": r[0], "name": r[1], "city": r[2], "category": r[3],
         "phone": r[4], "email": r[5], "source": r[6], "vetting_status": r[7],
         "vetting_score": r[8], "outreach_status": r[9]}
        for r in rows
    ]


def dry_run(conn: sqlite3.Connection) -> dict:
    """Show what WOULD happen without touching the database."""
    total = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
    keep_ids = get_keep_set(conn)
    delete_ids = get_delete_set(conn, keep_ids)

    result = {
        "mode": "DRY RUN",
        "total_vendors": total,
        "keep_count": len(keep_ids),
        "delete_count": len(delete_ids),
        "sample_deletes": sample_vendors(conn, delete_ids, 10),
        "preserved": preserved_vendors(conn, keep_ids),
    }

    # Category breakdown of deletions
    if delete_ids:
        placeholders = ",".join("?" * len(delete_ids))
        cats = conn.execute(
            f"SELECT category, COUNT(*) FROM vendors WHERE id IN ({placeholders}) GROUP BY category ORDER BY COUNT(*) DESC LIMIT 15",
            delete_ids,
        ).fetchall()
        result["delete_by_category"] = {r[0]: r[1] for r in cats}

    # Source breakdown
    if delete_ids:
        sources = conn.execute(
            f"SELECT source, COUNT(*) FROM vendors WHERE id IN ({placeholders}) GROUP BY source ORDER BY COUNT(*) DESC",
            delete_ids,
        ).fetchall()
        result["delete_by_source"] = {r[0]: r[1] for r in sources}

    return result


def execute_purge(conn: sqlite3.Connection) -> dict:
    """Actually delete vendors NOT in the keep set. Requires manual confirmation."""
    print("\n" + "=" * 60)
    print("  VENDOR PURGE — EXECUTE MODE")
    print("=" * 60)

    keep_ids = get_keep_set(conn)
    delete_ids = get_delete_set(conn, keep_ids)

    print(f"\n  Vendors to KEEP:   {len(keep_ids)}")
    print(f"  Vendors to DELETE: {len(delete_ids)}")
    print(f"  Total:             {len(keep_ids) + len(delete_ids)}")

    if not delete_ids:
        print("\n  Nothing to delete.")
        return {"status": "nothing_to_delete"}

    print(f"\n  Type 'CONFIRM PURGE' to proceed (anything else cancels):")
    confirmation = input("  > ").strip()

    if confirmation != "CONFIRM PURGE":
        print("  Purge cancelled.")
        return {"status": "cancelled", "input": confirmation}

    # Batch delete
    batch_size = 500
    deleted = 0
    start = time.time()

    for i in range(0, len(delete_ids), batch_size):
        batch = delete_ids[i:i + batch_size]
        placeholders = ",".join("?" * len(batch))

        # Delete related outreach records first
        conn.execute(
            f"DELETE FROM vendor_outreach WHERE vendor_id IN ({placeholders})",
            batch,
        )

        # Delete vendors
        conn.execute(
            f"DELETE FROM vendors WHERE id IN ({placeholders})",
            batch,
        )
        conn.commit()
        deleted += len(batch)
        log.info("Deleted %d / %d vendors", deleted, len(delete_ids))

    # VACUUM to reclaim space
    pre_vacuum = DB_PATH.stat().st_size / (1024 * 1024)
    log.info("Running VACUUM...")
    conn.execute("VACUUM")
    post_vacuum = DB_PATH.stat().st_size / (1024 * 1024)

    elapsed = time.time() - start
    remaining = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]

    result = {
        "status": "completed",
        "deleted": deleted,
        "remaining": remaining,
        "kept": len(keep_ids),
        "db_before_mb": round(pre_vacuum, 1),
        "db_after_mb": round(post_vacuum, 1),
        "saved_mb": round(pre_vacuum - post_vacuum, 1),
        "elapsed_seconds": round(elapsed, 1),
    }

    log.info("Purge complete: %s", json.dumps(result, indent=2))
    return result


def write_to_auditor_log(result: dict):
    """Append purge results to AUDITOR_LOG.md."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M PT")
    mode = result.get("mode", "EXECUTE")

    lines = [
        f"\n\n## TASK 3 — VENDOR PURGE SCRIPT — {'DRY RUN' if mode == 'DRY RUN' else 'EXECUTED'}",
        f"\nTIMESTAMP: {timestamp}",
        f"MODE: {mode}",
    ]

    if mode == "DRY RUN":
        lines.extend([
            f"Total vendors: {result['total_vendors']}",
            f"Would keep: {result['keep_count']}",
            f"Would delete: {result['delete_count']}",
            f"\nPreserved vendors ({result['keep_count']}):",
        ])
        for v in result.get("preserved", []):
            lines.append(f"  - [{v['id']}] {v['name']} ({v['city']}) — {v['vetting_status']}, score={v['vetting_score']}, source={v['source']}")
        lines.append(f"\nSample deletions (10 of {result['delete_count']}):")
        for v in result.get("sample_deletes", []):
            lines.append(f"  - [{v['id']}] {v['name']} ({v['city']}) — {v['category']}, phone={v['phone']}")
        if result.get("delete_by_source"):
            lines.append("\nDeletions by source:")
            for src, cnt in result["delete_by_source"].items():
                lines.append(f"  - {src}: {cnt}")
    else:
        status = result.get("status", "unknown")
        if status == "completed":
            lines.extend([
                f"Deleted: {result['deleted']}",
                f"Remaining: {result['remaining']}",
                f"Kept: {result['kept']}",
                f"DB before: {result['db_before_mb']} MB",
                f"DB after: {result['db_after_mb']} MB",
                f"Saved: {result['saved_mb']} MB",
                f"Elapsed: {result['elapsed_seconds']}s",
            ])
        else:
            lines.append(f"Status: {status}")

    lines.append("\n---")

    with open(AUDITOR_LOG, "a") as f:
        f.write("\n".join(lines))

    log.info("Results written to %s", AUDITOR_LOG)


def main():
    parser = argparse.ArgumentParser(description="Vendor purge — remove AI-hallucinated vendors")
    parser.add_argument("--execute", action="store_true", help="Actually delete (default is dry run)")
    parser.add_argument("--purge-all", action="store_true", help="Delete ALL vendors (skip KEEP set)")
    parser.add_argument("--no-backup", action="store_true", help="Skip backup (not recommended)")
    parser.add_argument("--report", action="store_true", help="Write results to AUDITOR_LOG.md")
    args = parser.parse_args()

    if not DB_PATH.exists():
        log.error("Database not found: %s", DB_PATH)
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")

    # Override keep set if --purge-all
    if args.purge_all:
        global get_keep_set
        _original_get_keep_set = get_keep_set
        get_keep_set = lambda conn: set()  # Empty keep set = delete everything
        log.info("--purge-all: KEEP set disabled, ALL vendors will be deleted")

    try:
        if args.execute:
            # Backup first (unless skipped)
            if not args.no_backup:
                backup = backup_database(conn)
                print(f"\nBackup created:")
                print(f"  DB:  {backup['db_backup']} ({backup['db_size_mb']} MB)")
                print(f"  CSV: {backup['csv_backup']} ({backup['vendor_count']} rows)")

            result = execute_purge(conn)
        else:
            result = dry_run(conn)

            # Print human-readable summary
            print("\n" + "=" * 60)
            print("  VENDOR PURGE — DRY RUN")
            print("=" * 60)
            print(f"\n  Total vendors:     {result['total_vendors']}")
            print(f"  Would KEEP:        {result['keep_count']}")
            print(f"  Would DELETE:      {result['delete_count']}")

            if result.get("preserved"):
                print(f"\n  Preserved vendors ({result['keep_count']}):")
                for v in result["preserved"]:
                    print(f"    [{v['id']}] {v['name']} ({v['city']}) — {v['vetting_status']}, score={v['vetting_score']}")
            else:
                print("\n  No vendors match KEEP criteria.")

            print(f"\n  Sample deletions (10 of {result['delete_count']}):")
            for v in result.get("sample_deletes", []):
                print(f"    [{v['id']}] {v['name']} ({v['city']}) — {v['category']}")

            if result.get("delete_by_source"):
                print("\n  By source:")
                for src, cnt in result["delete_by_source"].items():
                    print(f"    {src}: {cnt}")

            if result.get("delete_by_category"):
                print("\n  Top categories being deleted:")
                for cat, cnt in list(result["delete_by_category"].items())[:10]:
                    print(f"    {cat}: {cnt}")

            print(f"\n  To execute: python {__file__} --execute")
            print("=" * 60)

        if args.report:
            write_to_auditor_log(result)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
