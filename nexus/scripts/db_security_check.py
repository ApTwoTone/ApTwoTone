#!/usr/bin/env python3
"""Database security check — verifies DB configuration and file security.

Checks:
    1. WAL mode enabled
    2. File permissions (not world-readable)
    3. Integrity check
    4. Backup age
    5. Sensitive files in ~/.nexus/

Usage:
    python scripts/db_security_check.py        # Run all checks
    python scripts/db_security_check.py --json  # JSON output
"""

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("db_security")

DB_PATH = Path.home() / ".nexus" / "memory.db"
NEXUS_DIR = Path.home() / ".nexus"
BACKUP_DIR = NEXUS_DIR / "backups"

SENSITIVE_PATTERNS = ["config.json", "*.key", "*.pem", "*.secret", "gmail_app_password"]
BACKUP_WARN_DAYS = 7


def check_wal_mode() -> dict:
    """Verify WAL mode is enabled."""
    conn = sqlite3.connect(str(DB_PATH))
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    return {
        "check": "wal_mode",
        "status": "ok" if mode == "wal" else "warning",
        "mode": mode,
    }


def check_file_permissions() -> dict:
    """Check file permissions on database and config."""
    results = []
    for path in [DB_PATH, NEXUS_DIR / "config.json"]:
        if not path.exists():
            results.append({"file": str(path), "status": "missing"})
            continue
        mode = oct(path.stat().st_mode)[-3:]
        world_readable = path.stat().st_mode & 0o004
        results.append({
            "file": str(path),
            "permissions": mode,
            "world_readable": bool(world_readable),
            "status": "warning" if world_readable else "ok",
        })

    overall = "warning" if any(r.get("world_readable") for r in results) else "ok"
    return {"check": "file_permissions", "status": overall, "files": results}


def check_integrity() -> dict:
    """Run SQLite integrity check."""
    conn = sqlite3.connect(str(DB_PATH))
    result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()
    return {
        "check": "integrity",
        "status": "ok" if result == "ok" else "critical",
        "result": result,
    }


def check_backup_age() -> dict:
    """Check when the last backup was taken."""
    if not BACKUP_DIR.exists():
        return {"check": "backup_age", "status": "warning",
                "message": "No backup directory found"}

    backups = sorted(BACKUP_DIR.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not backups:
        return {"check": "backup_age", "status": "warning",
                "message": "No database backups found"}

    latest = backups[0]
    age_days = (datetime.now() - datetime.fromtimestamp(latest.stat().st_mtime)).days

    return {
        "check": "backup_age",
        "status": "ok" if age_days <= BACKUP_WARN_DAYS else "warning",
        "latest_backup": latest.name,
        "age_days": age_days,
        "backup_count": len(backups),
    }


def check_sensitive_files() -> dict:
    """Check for sensitive files with wrong permissions."""
    issues = []
    for item in NEXUS_DIR.iterdir():
        if item.is_file():
            mode = item.stat().st_mode
            world_readable = mode & 0o004
            world_writable = mode & 0o002
            if world_readable or world_writable:
                # Only flag actually sensitive files
                if item.suffix in (".json", ".key", ".pem", ".secret", ".log") or \
                   "password" in item.name.lower() or "token" in item.name.lower():
                    issues.append({
                        "file": item.name,
                        "permissions": oct(mode)[-3:],
                        "world_readable": bool(world_readable),
                        "world_writable": bool(world_writable),
                    })

    return {
        "check": "sensitive_files",
        "status": "warning" if issues else "ok",
        "issues": issues,
    }


def check_db_size() -> dict:
    """Check database size and freelist."""
    size_mb = DB_PATH.stat().st_size / (1024 * 1024)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA query_only = ON")
    page_count = conn.execute("PRAGMA page_count").fetchone()[0]
    freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]
    conn.close()

    freelist_pct = (freelist / page_count * 100) if page_count > 0 else 0
    status = "ok"
    if freelist_pct > 40:
        status = "warning"  # Needs VACUUM

    return {
        "check": "db_size",
        "status": status,
        "size_mb": round(size_mb, 1),
        "freelist_pct": round(freelist_pct, 1),
        "needs_vacuum": freelist_pct > 40,
    }


def main():
    parser = argparse.ArgumentParser(description="Database security check")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if not DB_PATH.exists():
        log.error("Database not found: %s", DB_PATH)
        sys.exit(1)

    checks = [
        check_wal_mode(),
        check_file_permissions(),
        check_integrity(),
        check_backup_age(),
        check_sensitive_files(),
        check_db_size(),
    ]

    if args.json:
        print(json.dumps(checks, indent=2))
    else:
        print(f"\n  Database Security Check")
        print(f"  Database: {DB_PATH}")
        print(f"  {'=' * 50}")

        for c in checks:
            icon = {"ok": "OK", "warning": "WARN", "critical": "CRIT"}.get(c["status"], "?")
            print(f"\n  [{icon}] {c['check'].upper()}")
            for k, v in c.items():
                if k not in ("check", "status"):
                    if isinstance(v, list):
                        for item in v:
                            print(f"      {item}")
                    else:
                        print(f"      {k}: {v}")

        overall = "SECURE" if all(c["status"] == "ok" for c in checks) else "ISSUES FOUND"
        print(f"\n  Overall: {overall}")
        print(f"  {'=' * 50}")


if __name__ == "__main__":
    main()
