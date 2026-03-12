"""
Database Backup System — Automated daily backups of ~/.nexus/memory.db

Features:
- Daily automated backups (runs as asyncio background task)
- Keeps last 7 daily backups (auto-prunes older ones)
- Backup on startup
- Manual backup API endpoint
- All backups stored locally in ~/.nexus/backups/
"""
import asyncio
import shutil
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

DB_PATH = Path.home() / ".nexus" / "memory.db"
BACKUP_DIR = Path.home() / ".nexus" / "backups"
MAX_BACKUPS = 7  # Keep last 7 backups (~800MB vs 3.4GB at 30)


def _ensure_backup_dir():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def create_backup(label: str = "") -> dict:
    """
    Create a backup of the SQLite database.
    Uses SQLite's built-in backup API for consistency (no partial reads).
    Returns: {"ok": True, "path": str, "size_mb": float} or {"ok": False, "error": str}
    """
    _ensure_backup_dir()

    if not DB_PATH.exists():
        return {"ok": False, "error": "Database not found"}

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{label}" if label else ""
    backup_name = f"memory_{timestamp}{suffix}.db"
    backup_path = BACKUP_DIR / backup_name

    try:
        # Use SQLite backup API for safe, consistent backups
        source = sqlite3.connect(str(DB_PATH))
        dest = sqlite3.connect(str(backup_path))
        source.backup(dest)
        dest.close()
        source.close()

        size_mb = backup_path.stat().st_size / (1024 * 1024)
        print(f"[Backup] Created: {backup_name} ({size_mb:.1f} MB)")
        return {"ok": True, "path": str(backup_path), "name": backup_name, "size_mb": round(size_mb, 2)}
    except Exception as e:
        print(f"[Backup] Error: {e}")
        return {"ok": False, "error": str(e)}


def prune_old_backups():
    """Remove backups older than MAX_BACKUPS days."""
    _ensure_backup_dir()
    backups = sorted(BACKUP_DIR.glob("memory_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for backup in backups[MAX_BACKUPS:]:
        try:
            backup.unlink()
            removed += 1
        except Exception:
            pass
    if removed:
        print(f"[Backup] Pruned {removed} old backups (keeping last {MAX_BACKUPS})")


def list_backups() -> list:
    """List all available backups."""
    _ensure_backup_dir()
    backups = sorted(BACKUP_DIR.glob("memory_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    result = []
    for b in backups:
        stat = b.stat()
        result.append({
            "name": b.name,
            "path": str(b),
            "size_mb": round(stat.st_size / (1024 * 1024), 2),
            "created": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
    return result


def restore_backup(backup_name: str) -> dict:
    """
    Restore a backup (copies backup over current DB).
    IMPORTANT: Should only be called when server is stopped.
    """
    backup_path = BACKUP_DIR / backup_name
    if not backup_path.exists():
        return {"ok": False, "error": f"Backup not found: {backup_name}"}

    try:
        # First, backup the current DB as a safety net
        create_backup("pre_restore")
        # Copy backup over current DB
        shutil.copy2(str(backup_path), str(DB_PATH))
        print(f"[Backup] Restored from: {backup_name}")
        return {"ok": True, "restored_from": backup_name}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def start_backup_scheduler():
    """Background task — creates a backup on start, then every 24 hours."""
    # Immediate backup on startup
    create_backup("startup")
    prune_old_backups()

    while True:
        # Wait 24 hours
        await asyncio.sleep(86400)
        try:
            create_backup("daily")
            prune_old_backups()
        except Exception as e:
            print(f"[Backup] Scheduler error: {e}")
