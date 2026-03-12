#!/usr/bin/env python3
"""Audit trail infrastructure — hash-chained tamper-evident logging.

Creates the audit_trail table and provides log_audit() for all safety scripts.

Usage:
    python scripts/audit_helpers.py --recent 20     # Show last 20 entries
    python scripts/audit_helpers.py --search term   # Search audit trail
    python scripts/audit_helpers.py --verify         # Verify hash chain integrity
    python scripts/audit_helpers.py --init           # Create table only (idempotent)

Importable:
    from scripts.audit_helpers import log_audit, verify_chain
"""

import argparse
import hashlib
import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("audit")

DB_PATH = Path.home() / ".nexus" / "memory.db"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS audit_trail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT DEFAULT (datetime('now')),
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    target TEXT DEFAULT '',
    detail TEXT DEFAULT '',
    prev_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL
)
"""


def ensure_table(conn: sqlite3.Connection):
    """Create audit_trail table if it doesn't exist."""
    conn.execute(CREATE_TABLE_SQL)
    conn.commit()


def _get_prev_hash(conn: sqlite3.Connection) -> str:
    """Get the hash of the most recent audit entry, or zeros if empty."""
    row = conn.execute(
        "SELECT entry_hash FROM audit_trail ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else "0" * 64


def _compute_hash(timestamp: str, action: str, actor: str, target: str,
                  detail: str, prev_hash: str) -> str:
    """Compute SHA-256 hash for an audit entry."""
    data = f"{timestamp}|{action}|{actor}|{target}|{detail}|{prev_hash}"
    return hashlib.sha256(data.encode()).hexdigest()


def log_audit(action: str, actor: str, target: str = "", detail: str = "",
              db_path: Path = None) -> int:
    """Log an action to the audit trail with hash chaining.

    Args:
        action: What happened (e.g. "kill_switch_activated", "blocklist_add")
        actor: Who did it (e.g. "auditor", "system", "kai")
        target: What was affected (e.g. "vendor:123", "email:foo@bar.com")
        detail: Additional context
        db_path: Override database path (defaults to ~/.nexus/memory.db)

    Returns:
        The ID of the new audit entry.
    """
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    ensure_table(conn)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prev_hash = _get_prev_hash(conn)
    entry_hash = _compute_hash(timestamp, action, actor, target, detail, prev_hash)

    conn.execute(
        "INSERT INTO audit_trail (timestamp, action, actor, target, detail, prev_hash, entry_hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (timestamp, action, actor, target, detail, prev_hash, entry_hash),
    )
    conn.commit()
    entry_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    log.info("Audit: %s by %s → %s", action, actor, target or "(none)")
    return entry_id


def verify_chain(db_path: Path = None) -> tuple:
    """Verify the audit trail hash chain for tamper detection.

    Returns:
        (valid: bool, message: str)
    """
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA query_only = ON")

    # Check if table exists
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_trail'"
    ).fetchone()
    if not tables:
        conn.close()
        return True, "No audit_trail table — nothing to verify"

    rows = conn.execute(
        "SELECT id, timestamp, action, actor, target, detail, prev_hash, entry_hash "
        "FROM audit_trail ORDER BY id ASC"
    ).fetchall()
    conn.close()

    if not rows:
        return True, "Chain empty — no entries"

    expected_prev = "0" * 64
    for row in rows:
        rid, ts, action, actor, target, detail, prev_hash, entry_hash = row
        if prev_hash != expected_prev:
            return False, f"Chain broken at id={rid}: expected prev={expected_prev[:16]}..."
        computed = _compute_hash(ts, action, actor, target, detail, prev_hash)
        if computed != entry_hash:
            return False, f"Tampered entry at id={rid}"
        expected_prev = entry_hash

    return True, f"Chain valid: {len(rows)} entries verified"


def get_recent(n: int = 20, db_path: Path = None) -> list:
    """Get the N most recent audit entries."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA query_only = ON")

    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_trail'"
    ).fetchone()
    if not tables:
        conn.close()
        return []

    rows = conn.execute(
        "SELECT id, timestamp, action, actor, target, detail FROM audit_trail "
        "ORDER BY id DESC LIMIT ?", (n,)
    ).fetchall()
    conn.close()

    return [
        {"id": r[0], "timestamp": r[1], "action": r[2], "actor": r[3],
         "target": r[4], "detail": r[5]}
        for r in rows
    ]


def search_trail(term: str, db_path: Path = None) -> list:
    """Search audit trail by action, actor, target, or detail."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA query_only = ON")

    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_trail'"
    ).fetchone()
    if not tables:
        conn.close()
        return []

    like = f"%{term}%"
    rows = conn.execute(
        "SELECT id, timestamp, action, actor, target, detail FROM audit_trail "
        "WHERE action LIKE ? OR actor LIKE ? OR target LIKE ? OR detail LIKE ? "
        "ORDER BY id DESC LIMIT 50",
        (like, like, like, like),
    ).fetchall()
    conn.close()

    return [
        {"id": r[0], "timestamp": r[1], "action": r[2], "actor": r[3],
         "target": r[4], "detail": r[5]}
        for r in rows
    ]


def _print_entries(entries: list):
    """Print audit entries in a readable format."""
    if not entries:
        print("  (no entries)")
        return
    for e in entries:
        target = f" → {e['target']}" if e['target'] else ""
        detail = f" | {e['detail']}" if e['detail'] else ""
        print(f"  [{e['id']}] {e['timestamp']} | {e['action']} by {e['actor']}{target}{detail}")


def main():
    parser = argparse.ArgumentParser(description="Audit trail management")
    parser.add_argument("--init", action="store_true", help="Create audit_trail table (idempotent)")
    parser.add_argument("--recent", type=int, metavar="N", help="Show last N entries")
    parser.add_argument("--search", type=str, metavar="TERM", help="Search audit trail")
    parser.add_argument("--verify", action="store_true", help="Verify hash chain integrity")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if not DB_PATH.exists():
        log.error("Database not found: %s", DB_PATH)
        sys.exit(1)

    if args.init:
        conn = sqlite3.connect(str(DB_PATH))
        ensure_table(conn)
        conn.close()
        print("audit_trail table ready.")
        return

    if args.verify:
        valid, msg = verify_chain()
        if args.json:
            print(json.dumps({"valid": valid, "message": msg}))
        else:
            print(f"\n  Hash Chain Verification: {'VALID' if valid else 'FAILED'}")
            print(f"  {msg}")
        sys.exit(0 if valid else 1)

    if args.search:
        entries = search_trail(args.search)
        if args.json:
            print(json.dumps(entries, indent=2))
        else:
            print(f"\n  Audit Trail — search: '{args.search}' ({len(entries)} results)")
            _print_entries(entries)
        return

    # Default: show recent
    n = args.recent or 20
    entries = get_recent(n)
    if args.json:
        print(json.dumps(entries, indent=2))
    else:
        print(f"\n  Audit Trail — last {n} entries ({len(entries)} found)")
        _print_entries(entries)


if __name__ == "__main__":
    main()
