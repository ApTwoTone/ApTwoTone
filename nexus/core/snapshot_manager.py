"""
Snapshot Manager — Atomic version history for Nexus.

A snapshot captures the full system state at a point in time:
  - SQLite database  (~/.nexus/memory.db)
  - Config file      (~/.nexus/config.json)
  - Git commit hash  (informational — tells you what code was running)

Snapshots live in ~/.nexus/snapshots/{timestamp}_{label}/
Each directory is self-contained and independent of the backup system.

Usage:
    from core.snapshot_manager import create_snapshot, list_snapshots, restore_snapshot

    create_snapshot("before_unified_events")
    snapshots = list_snapshots()
    restore_snapshot("20260309_141423_before_unified_events")
"""
import json
import logging
import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("snapshot_manager")

NEXUS_DIR = Path.home() / ".nexus"
DB_PATH = NEXUS_DIR / "memory.db"
CONFIG_PATH = NEXUS_DIR / "config.json"
SNAPSHOT_DIR = NEXUS_DIR / "snapshots"
MAX_SNAPSHOTS = 20
NEXUS_REPO = Path(__file__).parent.parent  # ~/nexus/


def _ensure_dir():
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def _get_git_info() -> tuple:
    """Return (hash, message) for the current HEAD commit."""
    try:
        git_hash = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(NEXUS_REPO),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        git_msg = subprocess.check_output(
            ["git", "log", "-1", "--pretty=%s"],
            cwd=str(NEXUS_REPO),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        return git_hash, git_msg
    except Exception:
        return "unknown", ""


def _snapshot_id(label: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_label = label.replace(" ", "_").replace("/", "-")[:40]
    return f"{timestamp}_{safe_label}"


def create_snapshot(label: str = "manual") -> dict:
    """
    Create an atomic snapshot of DB + config + git state.
    Returns the manifest dict on success, or {"ok": False, "error": ...} on failure.
    """
    _ensure_dir()

    snapshot_id = _snapshot_id(label)
    snap_dir = SNAPSHOT_DIR / snapshot_id
    snap_dir.mkdir(parents=True, exist_ok=True)

    try:
        # 1. SQLite backup (atomic — uses native backup API, safe for WAL mode)
        if DB_PATH.exists():
            src = sqlite3.connect(str(DB_PATH))
            dst = sqlite3.connect(str(snap_dir / "memory.db"))
            src.backup(dst)
            dst.close()
            src.close()
            db_size_mb = round((snap_dir / "memory.db").stat().st_size / (1024 * 1024), 2)
        else:
            db_size_mb = 0.0

        # 2. Config copy
        if CONFIG_PATH.exists():
            shutil.copy2(str(CONFIG_PATH), str(snap_dir / "config.json"))

        # 3. Git state (informational)
        git_hash, git_msg = _get_git_info()

        # 4. Manifest
        manifest = {
            "id": snapshot_id,
            "label": label,
            "created_at": datetime.now().isoformat(),
            "db_size_mb": db_size_mb,
            "git_hash": git_hash,
            "git_message": git_msg,
            "ok": True,
        }
        (snap_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

        log.info("Snapshot created: %s (%.1f MB)", snapshot_id, db_size_mb)
        prune_old_snapshots()
        return manifest

    except Exception as e:
        log.error("Snapshot failed: %s", e)
        # Clean up partial snapshot
        shutil.rmtree(str(snap_dir), ignore_errors=True)
        return {"ok": False, "error": str(e)}


def list_snapshots() -> list:
    """Return all snapshots sorted newest first."""
    _ensure_dir()
    snapshots = []
    for snap_dir in sorted(SNAPSHOT_DIR.iterdir(), reverse=True):
        if not snap_dir.is_dir():
            continue
        manifest_path = snap_dir / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            snapshots.append(json.loads(manifest_path.read_text()))
        except Exception:
            pass
    return snapshots


def get_snapshot(snapshot_id: str) -> Optional[dict]:
    """Return manifest for a single snapshot, or None if not found."""
    snap_dir = SNAPSHOT_DIR / snapshot_id
    manifest_path = snap_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text())
    except Exception:
        return None


def restore_snapshot(snapshot_id: str) -> dict:
    """
    Restore DB and config from a snapshot.

    Safety: takes a pre-restore backup of the current DB first.
    Git code is NOT rolled back (intentional — avoids disrupting active development).
    The git_hash in the manifest tells you what commit the snapshot corresponds to.

    Returns: {"ok": True, "restored_from": id, "git_hash": hash, "note": str}
    """
    snap_dir = SNAPSHOT_DIR / snapshot_id
    if not snap_dir.exists():
        return {"ok": False, "error": f"Snapshot not found: {snapshot_id}"}

    manifest = get_snapshot(snapshot_id)
    if not manifest:
        return {"ok": False, "error": "Snapshot manifest unreadable"}

    try:
        # 1. Safety backup of current state before overwriting
        if DB_PATH.exists():
            safety_id = _snapshot_id("pre_restore")
            safety_dir = SNAPSHOT_DIR / safety_id
            safety_dir.mkdir(parents=True, exist_ok=True)
            src = sqlite3.connect(str(DB_PATH))
            dst = sqlite3.connect(str(safety_dir / "memory.db"))
            src.backup(dst)
            dst.close()
            src.close()
            if CONFIG_PATH.exists():
                shutil.copy2(str(CONFIG_PATH), str(safety_dir / "config.json"))
            git_hash, git_msg = _get_git_info()
            safety_manifest = {
                "id": safety_id,
                "label": "pre_restore",
                "created_at": datetime.now().isoformat(),
                "db_size_mb": round((safety_dir / "memory.db").stat().st_size / (1024 * 1024), 2),
                "git_hash": git_hash,
                "git_message": git_msg,
                "ok": True,
            }
            (safety_dir / "manifest.json").write_text(json.dumps(safety_manifest, indent=2))
            log.info("Safety backup created: %s", safety_id)

        # 2. Restore DB
        snap_db = snap_dir / "memory.db"
        if snap_db.exists():
            src = sqlite3.connect(str(snap_db))
            dst = sqlite3.connect(str(DB_PATH))
            src.backup(dst)
            dst.close()
            src.close()
            log.info("DB restored from snapshot: %s", snapshot_id)

        # 3. Restore config
        snap_cfg = snap_dir / "config.json"
        if snap_cfg.exists():
            shutil.copy2(str(snap_cfg), str(CONFIG_PATH))
            log.info("Config restored from snapshot: %s", snapshot_id)

        return {
            "ok": True,
            "restored_from": snapshot_id,
            "label": manifest.get("label", ""),
            "git_hash": manifest.get("git_hash", "unknown"),
            "note": (
                f"DB and config restored. Code is still at current git state "
                f"(snapshot was taken at git:{manifest.get('git_hash', '?')} — "
                f"\"{manifest.get('git_message', '')}\"). "
                f"Restart the Nexus server to reload config."
            ),
        }

    except Exception as e:
        log.error("Restore failed: %s", e)
        return {"ok": False, "error": str(e)}


def prune_old_snapshots():
    """Keep only the newest MAX_SNAPSHOTS snapshots."""
    _ensure_dir()
    snap_dirs = sorted(
        [d for d in SNAPSHOT_DIR.iterdir() if d.is_dir() and (d / "manifest.json").exists()],
        key=lambda d: d.name,
        reverse=True,
    )
    removed = 0
    for old_dir in snap_dirs[MAX_SNAPSHOTS:]:
        try:
            shutil.rmtree(str(old_dir))
            removed += 1
        except Exception:
            pass
    if removed:
        log.info("Pruned %d old snapshots (keeping last %d)", removed, MAX_SNAPSHOTS)
