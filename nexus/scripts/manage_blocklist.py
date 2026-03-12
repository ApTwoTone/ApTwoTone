#!/usr/bin/env python3
"""Blocklist management CLI — add, remove, search, export contact blocklist entries.

Uses the existing contact_blocklist table (migration 028).

Usage:
    python scripts/manage_blocklist.py --list                          # Show all active entries
    python scripts/manage_blocklist.py --add-phone 8185551234 --reason "spam"
    python scripts/manage_blocklist.py --add-email bad@example.com --reason "bounced"
    python scripts/manage_blocklist.py --search 818                    # Search by phone/email
    python scripts/manage_blocklist.py --remove 5                      # Soft-delete entry ID 5
    python scripts/manage_blocklist.py --export                        # CSV export
    python scripts/manage_blocklist.py --stats                         # Summary stats
"""

import argparse
import csv
import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("blocklist")

DB_PATH = Path.home() / ".nexus" / "memory.db"


def _connect():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def list_entries(active_only: bool = True) -> list:
    """List blocklist entries."""
    conn = _connect()
    where = "WHERE active = 1" if active_only else ""
    rows = conn.execute(
        f"SELECT id, phone, email, reason, blocked_by, created_at, active "
        f"FROM contact_blocklist {where} ORDER BY id ASC"
    ).fetchall()
    conn.close()
    return [
        {"id": r[0], "phone": r[1], "email": r[2], "reason": r[3],
         "blocked_by": r[4], "created_at": r[5], "active": r[6]}
        for r in rows
    ]


def add_phone(phone: str, reason: str, blocked_by: str = "auditor"):
    """Add a phone number to the blocklist."""
    conn = _connect()
    # Check for existing
    existing = conn.execute(
        "SELECT id, active FROM contact_blocklist WHERE phone = ?", (phone,)
    ).fetchone()
    if existing:
        if existing[1] == 1:
            print(f"  Phone {phone} already blocklisted (id={existing[0]})")
            conn.close()
            return existing[0]
        else:
            # Reactivate
            conn.execute("UPDATE contact_blocklist SET active = 1, reason = ? WHERE id = ?",
                         (reason, existing[0]))
            conn.commit()
            conn.close()
            _log_audit("blocklist_reactivate", blocked_by, f"phone:{phone}", reason)
            print(f"  Phone {phone} reactivated (id={existing[0]})")
            return existing[0]

    conn.execute(
        "INSERT INTO contact_blocklist (phone, email, reason, blocked_by) VALUES (?, '', ?, ?)",
        (phone, reason, blocked_by),
    )
    conn.commit()
    entry_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    _log_audit("blocklist_add_phone", blocked_by, f"phone:{phone}", reason)
    print(f"  Phone {phone} added to blocklist (id={entry_id})")
    return entry_id


def add_email(email: str, reason: str, blocked_by: str = "auditor"):
    """Add an email to the blocklist."""
    conn = _connect()
    existing = conn.execute(
        "SELECT id, active FROM contact_blocklist WHERE email = ?", (email,)
    ).fetchone()
    if existing:
        if existing[1] == 1:
            print(f"  Email {email} already blocklisted (id={existing[0]})")
            conn.close()
            return existing[0]
        else:
            conn.execute("UPDATE contact_blocklist SET active = 1, reason = ? WHERE id = ?",
                         (reason, existing[0]))
            conn.commit()
            conn.close()
            _log_audit("blocklist_reactivate", blocked_by, f"email:{email}", reason)
            print(f"  Email {email} reactivated (id={existing[0]})")
            return existing[0]

    conn.execute(
        "INSERT INTO contact_blocklist (phone, email, reason, blocked_by) VALUES ('', ?, ?, ?)",
        (email, reason, blocked_by),
    )
    conn.commit()
    entry_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    _log_audit("blocklist_add_email", blocked_by, f"email:{email}", reason)
    print(f"  Email {email} added to blocklist (id={entry_id})")
    return entry_id


def remove_entry(entry_id: int):
    """Soft-delete a blocklist entry (sets active=0)."""
    conn = _connect()
    row = conn.execute(
        "SELECT phone, email, active FROM contact_blocklist WHERE id = ?", (entry_id,)
    ).fetchone()
    if not row:
        print(f"  Entry {entry_id} not found.")
        conn.close()
        return
    if row[2] == 0:
        print(f"  Entry {entry_id} already inactive.")
        conn.close()
        return

    conn.execute("UPDATE contact_blocklist SET active = 0 WHERE id = ?", (entry_id,))
    conn.commit()
    conn.close()

    target = f"phone:{row[0]}" if row[0] else f"email:{row[1]}"
    _log_audit("blocklist_remove", "auditor", target, f"Soft-deleted entry {entry_id}")
    print(f"  Entry {entry_id} deactivated (soft delete). Contact: {target}")


def search_entries(term: str) -> list:
    """Search blocklist by phone or email."""
    conn = _connect()
    like = f"%{term}%"
    rows = conn.execute(
        "SELECT id, phone, email, reason, blocked_by, created_at, active "
        "FROM contact_blocklist WHERE phone LIKE ? OR email LIKE ? ORDER BY id ASC",
        (like, like),
    ).fetchall()
    conn.close()
    return [
        {"id": r[0], "phone": r[1], "email": r[2], "reason": r[3],
         "blocked_by": r[4], "created_at": r[5], "active": r[6]}
        for r in rows
    ]


def export_csv(filepath: str = None):
    """Export blocklist to CSV."""
    entries = list_entries(active_only=False)
    if not filepath:
        filepath = str(Path.home() / ".nexus" / "blocklist_export.csv")

    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "phone", "email", "reason",
                                                "blocked_by", "created_at", "active"])
        writer.writeheader()
        writer.writerows(entries)

    print(f"  Exported {len(entries)} entries to {filepath}")


def get_stats() -> dict:
    """Get blocklist summary stats."""
    conn = _connect()
    total = conn.execute("SELECT COUNT(*) FROM contact_blocklist").fetchone()[0]
    active = conn.execute("SELECT COUNT(*) FROM contact_blocklist WHERE active = 1").fetchone()[0]
    phones = conn.execute("SELECT COUNT(*) FROM contact_blocklist WHERE active = 1 AND phone != ''").fetchone()[0]
    emails = conn.execute("SELECT COUNT(*) FROM contact_blocklist WHERE active = 1 AND email != ''").fetchone()[0]
    conn.close()
    return {"total": total, "active": active, "inactive": total - active,
            "phones": phones, "emails": emails}


def _log_audit(action: str, actor: str, target: str, detail: str):
    """Log to audit trail if available."""
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from audit_helpers import log_audit
        log_audit(action, actor, target, detail)
    except ImportError:
        pass


def _print_entries(entries: list):
    if not entries:
        print("  (no entries)")
        return
    for e in entries:
        contact = e["phone"] if e["phone"] else e["email"]
        status = "ACTIVE" if e["active"] else "inactive"
        print(f"  [{e['id']}] {contact} — {e['reason']} (by {e['blocked_by']}, {status})")


def main():
    parser = argparse.ArgumentParser(description="Blocklist management")
    parser.add_argument("--list", action="store_true", help="List active entries")
    parser.add_argument("--list-all", action="store_true", help="List all entries including inactive")
    parser.add_argument("--add-phone", type=str, metavar="PHONE", help="Add phone to blocklist")
    parser.add_argument("--add-email", type=str, metavar="EMAIL", help="Add email to blocklist")
    parser.add_argument("--reason", type=str, default="manual", help="Reason for blocking")
    parser.add_argument("--remove", type=int, metavar="ID", help="Soft-delete entry by ID")
    parser.add_argument("--search", type=str, metavar="TERM", help="Search by phone/email")
    parser.add_argument("--export", action="store_true", help="Export to CSV")
    parser.add_argument("--stats", action="store_true", help="Show summary stats")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if not DB_PATH.exists():
        log.error("Database not found: %s", DB_PATH)
        sys.exit(1)

    if args.add_phone:
        add_phone(args.add_phone, args.reason)
    elif args.add_email:
        add_email(args.add_email, args.reason)
    elif args.remove is not None:
        remove_entry(args.remove)
    elif args.search:
        entries = search_entries(args.search)
        if args.json:
            print(json.dumps(entries, indent=2))
        else:
            print(f"\n  Blocklist — search: '{args.search}' ({len(entries)} results)")
            _print_entries(entries)
    elif args.export:
        export_csv()
    elif args.stats:
        stats = get_stats()
        if args.json:
            print(json.dumps(stats, indent=2))
        else:
            print(f"\n  Blocklist Stats")
            for k, v in stats.items():
                print(f"    {k}: {v}")
    elif args.list_all:
        entries = list_entries(active_only=False)
        if args.json:
            print(json.dumps(entries, indent=2))
        else:
            print(f"\n  Blocklist — all entries ({len(entries)})")
            _print_entries(entries)
    else:
        # Default: list active
        entries = list_entries(active_only=True)
        if args.json:
            print(json.dumps(entries, indent=2))
        else:
            print(f"\n  Blocklist — active entries ({len(entries)})")
            _print_entries(entries)


if __name__ == "__main__":
    main()
