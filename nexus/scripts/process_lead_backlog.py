#!/usr/bin/env python3
"""
One-time backlog processor: fix requires_manual_approval for FB/website leads
stuck in awaiting_approval. Run with --dry-run first, then --execute.
"""

import argparse
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("process_lead_backlog")

DB_PATH = Path.home() / ".nexus" / "memory.db"

# Only these sources are considered trusted / auto-approvable
TRUSTED_SOURCES = frozenset({
    "facebook_ad",
    "facebook_lead_ad",
    "website",
    "website_form",
})

# Leads in these statuses must NOT be touched
TERMINAL_STATUSES = frozenset({
    "contacted",
    "booked",
    "lost",
    "opted_out",
})


def _connect():
    """Open a WAL-mode connection to the Nexus SQLite database."""
    if not DB_PATH.exists():
        log.error("Database not found at %s", DB_PATH)
        sys.exit(1)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _is_quiet_hours() -> bool:
    """Return True if current Pacific time is before 8 AM or at/after 7 PM."""
    now_pt = datetime.now(ZoneInfo("America/Los_Angeles"))
    return now_pt.hour < 8 or now_pt.hour >= 19


def _is_blocklisted(conn, phone: str, email: str) -> bool:
    """Check whether a phone or email appears on the active blocklist."""
    if phone:
        row = conn.execute(
            "SELECT 1 FROM contact_blocklist WHERE phone = ? AND active = 1 LIMIT 1",
            (phone,),
        ).fetchone()
        if row:
            return True
    if email:
        row = conn.execute(
            "SELECT 1 FROM contact_blocklist WHERE email = ? AND active = 1 LIMIT 1",
            (email,),
        ).fetchone()
        if row:
            return True
    return False


def fetch_qualifying_leads(conn):
    """Return leads stuck in awaiting_approval from trusted sources."""
    placeholders = ",".join("?" for _ in TRUSTED_SOURCES)
    terminal_ph = ",".join("?" for _ in TERMINAL_STATUSES)

    query = f"""
        SELECT id, COALESCE(full_name, first_name || ' ' || last_name), phone, email, source, status, requires_manual_approval
        FROM leads
        WHERE status = 'awaiting_approval'
          AND LOWER(source) IN ({placeholders})
          AND status NOT IN ({terminal_ph})
          AND requires_manual_approval = 1
        ORDER BY id ASC
    """
    params = list(TRUSTED_SOURCES) + list(TERMINAL_STATUSES)
    return conn.execute(query, params).fetchall()


def process_backlog(execute: bool = False):
    """Main logic: find qualifying leads and optionally clear the approval flag."""
    conn = _connect()

    leads = fetch_qualifying_leads(conn)
    log.info("Found %d qualifying leads in awaiting_approval from trusted sources", len(leads))

    if not leads:
        log.info("Nothing to do.")
        conn.close()
        return

    updated = 0
    skipped_blocklist = 0
    skipped_other = 0

    for lead_id, name, phone, email, source, status, approval_flag in leads:
        # Safety: check blocklist before touching each lead
        if _is_blocklisted(conn, phone or "", email or ""):
            log.warning(
                "  SKIP (blocklisted): id=%d name=%s phone=%s source=%s",
                lead_id, name, phone, source,
            )
            skipped_blocklist += 1
            continue

        label = "WOULD UPDATE" if not execute else "UPDATING"
        log.info(
            "  %s: id=%d name=%s phone=%s source=%s",
            label, lead_id, name, phone, source,
        )

        if execute:
            conn.execute(
                "UPDATE leads SET requires_manual_approval = 0 WHERE id = ?",
                (lead_id,),
            )
            updated += 1
        else:
            updated += 1  # count what would be updated in dry-run

    if execute:
        conn.commit()
        log.info("Committed changes to database.")
    else:
        log.info("DRY RUN — no changes written. Re-run with --execute to apply.")

    conn.close()

    # Summary
    log.info("=" * 60)
    log.info("SUMMARY")
    log.info("  Total qualifying leads found : %d", len(leads))
    log.info("  Leads %s            : %d", "updated" if execute else "would update", updated)
    log.info("  Skipped (blocklisted)        : %d", skipped_blocklist)
    log.info("  Skipped (other)              : %d", skipped_other)
    log.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "One-time backlog processor: clear requires_manual_approval for "
            "trusted-source leads stuck in awaiting_approval."
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Print what would be changed without modifying the database (default)",
    )
    group.add_argument(
        "--execute",
        action="store_true",
        help="Actually update the database",
    )
    args = parser.parse_args()

    execute = args.execute

    log.info("Mode: %s", "EXECUTE" if execute else "DRY RUN")
    log.info("Database: %s", DB_PATH)

    # Quiet-hours guard (Pacific time)
    if execute and _is_quiet_hours():
        log.warning(
            "REFUSED: Current Pacific time is within quiet hours (before 8 AM or "
            "after 7 PM). Bulk updates are not allowed during quiet hours. "
            "Please run again during business hours."
        )
        sys.exit(1)

    process_backlog(execute=execute)


if __name__ == "__main__":
    main()
