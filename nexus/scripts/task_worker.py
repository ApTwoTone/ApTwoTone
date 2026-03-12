"""
Nexus Task Worker — Autonomous daemon that picks up tasks from task_board
and executes them using free AI models via worker_pool.

Usage:
    python scripts/task_worker.py              # Single iteration, dry-run
    python scripts/task_worker.py --loop       # Continuous polling, dry-run
    python scripts/task_worker.py --live       # Write files (must be explicit)
    python scripts/task_worker.py --once       # Alias for single iteration
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("task_worker")

DB_PATH = Path.home() / ".nexus" / "memory.db"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

BANNED_FILES = frozenset([
    "core/security.py",
    "core/auth.py",
    "core/approval_system.py",
    "integrations/messaging.py",
    "core/key_rotation.py",
    "telegram/bot.py",
])

MAX_COMPLEXITY = 7
POLL_INTERVAL = 60
WORKER_SESSION = "task-worker-daemon"

TASK_TYPE_ROUTING = {
    "code": "build_implement",
    "bugfix": "build_implement",
    "refactor": "build_implement",
    "test": "build_implement",
    "research": "structured_data",
    "content": "nexus_brain",
}

SYSTEM_PROMPT = (
    "You are a senior Python developer working on a FastAPI monolith (Python 3.9+). "
    "Output ONLY the code changes needed. For each file, output:\n"
    "--- FILE: relative/path.py ---\n"
    "<complete file content>\n"
    "--- END FILE ---\n\n"
    "Rules:\n"
    "- Use Optional[str] not str | None (Python 3.9)\n"
    "- Use logging, never print()\n"
    "- Parameterized SQL only\n"
    "- Never touch security/auth files\n"
    "- If the task is research or content, output plain text instead of code blocks"
)


# ── Database helpers ─────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def get_next_task() -> Optional[Dict[str, Any]]:
    """Find highest-priority open, unassigned task with complexity < MAX_COMPLEXITY."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM task_board "
            "WHERE status = 'open' AND (assigned_to = '' OR assigned_to IS NULL) "
            "AND complexity < ? "
            "ORDER BY priority DESC, created_at ASC LIMIT 1",
            (MAX_COMPLEXITY,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def claim_task(task_id: int) -> bool:
    """Atomically claim a task. Returns False if already claimed."""
    conn = _conn()
    try:
        cur = conn.execute(
            "UPDATE task_board SET status = 'in_progress', assigned_to = ?, "
            "updated_at = datetime('now') "
            "WHERE id = ? AND status = 'open'",
            (WORKER_SESSION, task_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def release_task(task_id: int, status: str, note: str):
    """Release a task with final status and progress note."""
    conn = _conn()
    try:
        conn.execute(
            "UPDATE task_board SET status = ?, progress_note = ?, "
            "assigned_to = '', updated_at = datetime('now'), "
            "completed_at = CASE WHEN ? = 'done' THEN datetime('now') ELSE '' END "
            "WHERE id = ?",
            (status, note[:5000], status, task_id),
        )
        conn.commit()
    finally:
        conn.close()


# ── File lock checking ───────────────────────────────────────────────────────

def check_file_locks(files: List[str]) -> List[str]:
    """Return files from the list that are locked by other agents."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT files_locked FROM agent_sessions WHERE status = 'active'"
        ).fetchall()
        locked = set()
        for r in rows:
            try:
                locked.update(json.loads(r["files_locked"] or "[]"))
            except (json.JSONDecodeError, TypeError):
                pass
        return [f for f in files if f in locked]
    finally:
        conn.close()


# ── Context building ─────────────────────────────────────────────────────────

def build_context(task: Dict[str, Any]) -> str:
    """Read task description + referenced files to build AI context."""
    parts = ["## Task\n", task.get("description", "") or task.get("title", ""), "\n"]

    files_involved = []  # type: List[str]
    try:
        files_involved = json.loads(task.get("files_involved", "[]"))
    except (json.JSONDecodeError, TypeError):
        pass

    for rel_path in files_involved[:5]:
        full = PROJECT_ROOT / rel_path
        if full.exists() and full.stat().st_size < 50_000:
            try:
                content = full.read_text(encoding="utf-8", errors="replace")
                parts.append("\n## File: %s\n```python\n%s\n```\n" % (rel_path, content))
            except Exception:
                pass

    return "\n".join(parts)


# ── AI execution ─────────────────────────────────────────────────────────────

async def execute_task(task: Dict[str, Any], context: str) -> Dict[str, Any]:
    """Route task to free AI via worker_pool and get response."""
    from core.worker_pool import call_for_task

    task_type = task.get("task_type", "code") or "code"
    routing_key = TASK_TYPE_ROUTING.get(task_type, "build_implement")

    messages = [{"role": "user", "content": context}]

    result = await call_for_task(
        routing_key, messages, system=SYSTEM_PROMPT,
        max_tokens=4096, temperature=0.2,
        agent_id="task-worker",
    )
    return result


# ── Change application ───────────────────────────────────────────────────────

def apply_changes(task: Dict[str, Any], ai_content: str, dry_run: bool = True) -> str:
    """Parse AI response for file blocks and optionally write them."""
    if dry_run:
        return "DRY RUN -- output saved to progress_note only:\n\n" + ai_content[:3000]

    file_blocks = re.findall(
        r'--- FILE: (.+?) ---\n(.*?)--- END FILE ---',
        ai_content, re.DOTALL,
    )

    if not file_blocks:
        return "No file blocks found in AI response. Raw output saved."

    written = []   # type: List[str]
    skipped = []   # type: List[str]

    paths = [fb[0].strip() for fb in file_blocks]
    locked = check_file_locks(paths)

    for rel_path, content in file_blocks:
        rel_path = rel_path.strip()

        if rel_path in BANNED_FILES:
            skipped.append("%s (BANNED)" % rel_path)
            continue

        if rel_path in locked:
            skipped.append("%s (LOCKED)" % rel_path)
            continue

        full = (PROJECT_ROOT / rel_path).resolve()
        if not str(full).startswith(str(PROJECT_ROOT)):
            skipped.append("%s (path traversal blocked)" % rel_path)
            continue

        try:
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content.strip() + "\n", encoding="utf-8")
            written.append(rel_path)
        except Exception as e:
            skipped.append("%s (write error: %s)" % (rel_path, e))

    parts = []
    if written:
        parts.append("Written: %s" % ", ".join(written))
    if skipped:
        parts.append("Skipped: %s" % ", ".join(skipped))
    return "\n".join(parts) or "No changes applied."


# ── Main loop ────────────────────────────────────────────────────────────────

async def run_one(dry_run: bool = True) -> bool:
    """Pick up and execute one task. Returns True if a task was processed."""
    task = get_next_task()
    if not task:
        log.info("No tasks available")
        return False

    task_id = task["id"]
    log.info("Claiming task %d: %s", task_id, (task.get("title") or "")[:80])

    if not claim_task(task_id):
        log.warning("Task %d already claimed", task_id)
        return False

    try:
        context = build_context(task)
        result = await execute_task(task, context)

        if not result.get("ok"):
            release_task(task_id, "open",
                         "AI call failed: %s" % (result.get("content", ""))[:500])
            log.warning("Task %d: AI call failed, returning to pool", task_id)
            return True

        summary = apply_changes(task, result.get("content", ""), dry_run=dry_run)
        final_note = "Provider: %s | Model: %s\n\n%s" % (
            result.get("provider", "?"), result.get("model", "?"), summary,
        )

        status = "done" if not dry_run else "review_needed"
        release_task(task_id, status, final_note)
        log.info("Task %d completed (dry_run=%s)", task_id, dry_run)
        return True

    except Exception as e:
        log.error("Task %d error: %s", task_id, e, exc_info=True)
        release_task(task_id, "open", "Worker error: %s" % str(e)[:500])
        return False


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    try:
        from core.db_migrate import run_migrations
        run_migrations()
    except Exception as e:
        log.warning("Migration check failed: %s", e)

    dry_run = "--live" not in sys.argv
    loop = "--loop" in sys.argv

    if dry_run:
        log.info("DRY RUN mode (pass --live to write files)")

    if not loop:
        asyncio.run(run_one(dry_run))
    else:
        log.info("Task worker daemon started (poll every %ds)", POLL_INTERVAL)
        while True:
            try:
                asyncio.run(run_one(dry_run))
            except Exception as e:
                log.error("Worker loop error: %s", e)
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
