"""
Frontend Watcher — Monitors nexus-network source files and triggers rebuilds.

Runs as a launchd daemon (com.zoar.frontend-watcher).
Watches .tsx/.ts/.css files in nexus-network/src/ for changes.
On change: runs `npm run build` and logs a changelog entry to SQLite.

Next.js dev mode (turbopack) already handles HMR in development.
This watcher handles production static builds for the Tauri desktop app.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import subprocess
import time
from pathlib import Path

log = logging.getLogger("frontend_watcher")

NEXUS_NETWORK = Path.home() / "nexus-network"
SRC_DIR = NEXUS_NETWORK / "src"
DB_PATH = Path.home() / ".nexus" / "memory.db"
BUILD_INTERVAL = 5  # Check every 5 seconds
DEBOUNCE_SECONDS = 3  # Wait 3 seconds after last change before building

WATCH_EXTENSIONS = {".tsx", ".ts", ".css", ".json"}
IGNORE_DIRS = {"node_modules", ".next", "out", ".git"}


def _ensure_changelog_table():
    """Create changelog table if it doesn't exist."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ui_changelog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version TEXT NOT NULL,
            build_hash TEXT NOT NULL,
            changed_files TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
        )
    """)
    conn.commit()
    conn.close()


def _get_src_fingerprint():
    """Compute a hash of all watched source files' mtimes."""
    h = hashlib.md5()
    files = []
    for root, dirs, filenames in os.walk(str(SRC_DIR)):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for f in sorted(filenames):
            if Path(f).suffix in WATCH_EXTENSIONS:
                fp = os.path.join(root, f)
                try:
                    mtime = os.path.getmtime(fp)
                    h.update(("%s:%s" % (fp, mtime)).encode())
                    files.append(fp)
                except OSError:
                    pass
    return h.hexdigest(), files


def _get_changed_files(old_files_map, new_files_map):
    """Return list of files that changed between two snapshots."""
    changed = []
    for fp, mtime in new_files_map.items():
        if fp not in old_files_map or old_files_map[fp] != mtime:
            changed.append(os.path.relpath(fp, str(NEXUS_NETWORK)))
    return changed


def _snapshot_mtimes():
    """Return dict of filepath → mtime for watched files."""
    result = {}
    for root, dirs, filenames in os.walk(str(SRC_DIR)):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for f in sorted(filenames):
            if Path(f).suffix in WATCH_EXTENSIONS:
                fp = os.path.join(root, f)
                try:
                    result[fp] = os.path.getmtime(fp)
                except OSError:
                    pass
    return result


def _run_build():
    """Run npm run build and return (success, output)."""
    try:
        result = subprocess.run(
            ["npm", "run", "build"],
            cwd=str(NEXUS_NETWORK),
            capture_output=True,
            text=True,
            timeout=120,
        )
        ok = result.returncode == 0
        # Tauri app loads from localhost:3000 (HMR) so no rebuild needed.
        # Static export (out/) is for Vercel deploy only.
        return ok, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return False, "Build timed out after 120 seconds"
    except Exception as e:
        return False, str(e)


def _rebuild_tauri_app():
    """Rebuild the Tauri desktop app and reinstall to /Applications."""
    cargo_bin = Path.home() / ".cargo" / "bin"
    env = os.environ.copy()
    env["PATH"] = "%s:%s" % (cargo_bin, env.get("PATH", ""))

    try:
        result = subprocess.run(
            ["npx", "tauri", "build"],
            cwd=str(NEXUS_NETWORK),
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        if result.returncode == 0:
            import shutil
            src = NEXUS_NETWORK / "src-tauri" / "target" / "release" / "bundle" / "macos" / "Nexus Network.app"
            dst = Path("/Applications/Nexus Network.app")
            if src.exists():
                # Quit app, replace, reopen
                subprocess.run(["osascript", "-e", 'tell application "Nexus Network" to quit'], capture_output=True)
                time.sleep(1)
                if dst.exists():
                    shutil.rmtree(str(dst))
                shutil.copytree(str(src), str(dst))
                subprocess.Popen(["open", str(dst)])
                log.info("Tauri app rebuilt and reinstalled")
            else:
                log.warning("Tauri build output not found at %s", src)
        else:
            log.warning("Tauri build failed: %s", result.stderr[-300:])
    except Exception as e:
        log.warning("Tauri rebuild failed: %s", e)


def _log_changelog(build_hash, changed_files):
    """Write a changelog entry to SQLite."""
    version = time.strftime("v%Y%m%d.%H%M%S")
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "INSERT INTO ui_changelog (version, build_hash, changed_files, summary, created_at) VALUES (?, ?, ?, ?, datetime('now'))",
            (
                version,
                build_hash[:12],
                ", ".join(changed_files[:20]),
                "%d files updated" % len(changed_files),
            ),
        )
        conn.commit()
        conn.close()
        log.info("Changelog: %s (%s, %d files)", version, build_hash[:12], len(changed_files))
    except Exception as e:
        log.warning("Failed to log changelog: %s", e)


def _write_version_file(build_hash):
    """Write version info for the UI to read."""
    version_file = NEXUS_NETWORK / "public" / "version.json"
    version_file.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": time.strftime("v%Y%m%d.%H%M%S"),
        "hash": build_hash[:12],
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "builder": "frontend-watcher",
    }
    version_file.write_text(json.dumps(data))


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="[frontend_watcher] %(message)s",
    )
    log.info("Frontend watcher starting, watching %s", SRC_DIR)

    _ensure_changelog_table()

    last_hash, _ = _get_src_fingerprint()
    last_mtimes = _snapshot_mtimes()
    last_change_time = 0.0

    # Write initial version file
    _write_version_file(last_hash)
    log.info("Initial fingerprint: %s", last_hash[:12])

    while True:
        time.sleep(BUILD_INTERVAL)
        current_hash, _ = _get_src_fingerprint()

        if current_hash != last_hash:
            last_change_time = time.time()
            last_hash = current_hash

        # Debounce: only build if changes settled for DEBOUNCE_SECONDS
        if last_change_time > 0 and (time.time() - last_change_time) >= DEBOUNCE_SECONDS:
            current_mtimes = _snapshot_mtimes()
            changed = _get_changed_files(last_mtimes, current_mtimes)

            if changed:
                log.info("Detected %d changed files, building...", len(changed))
                for f in changed[:5]:
                    log.info("  changed: %s", f)

                success, output = _run_build()
                build_hash = current_hash

                if success:
                    log.info("Build succeeded (hash: %s)", build_hash[:12])
                    _log_changelog(build_hash, changed)
                    _write_version_file(build_hash)
                else:
                    log.error("Build FAILED: %s", output[-500:])

                last_mtimes = current_mtimes

            last_change_time = 0.0


if __name__ == "__main__":
    main()
