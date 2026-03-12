"""
Nexus Fleet Task Queue — Three-tier SQLite task queue with atomic claims.

ALL AGENTS READ-ONLY: Vendor research agents are permanently read-only.
Zero interaction with any platform content ever. (CLAUDE.md Rule 24)

Every piece of work flows through this queue. Workers never pass data
directly to each other. Everything goes through the database.

Usage:
    from core.fleet_task_queue import FleetTaskQueue
    q = FleetTaskQueue()
    task_id = q.enqueue("vendor_research", {"zone": "Van Nuys"}, tier=3, priority=5)
    task = q.claim("worker-42", tier=3)
    q.complete(task_id, {"vendors_found": 12, "data": [...]})
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("fleet_task_queue")

DB_PATH = Path.home() / ".nexus" / "memory.db"


class FleetTaskQueue:
    """SQLite-based fleet task queue with atomic claims and dead letter management."""

    PENDING = "pending"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"

    MAX_RETRIES = 3
    MAX_TOTAL_RETRIES = 6
    TASK_TIMEOUT_SECS = 300

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or DB_PATH
        self._ensure_schema()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self):
        conn = self._get_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS fleet_tasks (
                    task_id TEXT PRIMARY KEY,
                    task_type TEXT NOT NULL,
                    priority INTEGER DEFAULT 5,
                    tier INTEGER DEFAULT 3,
                    input_data TEXT DEFAULT '{}',
                    status TEXT DEFAULT 'pending',
                    created_at REAL NOT NULL,
                    deadline_at REAL,
                    claimed_by TEXT,
                    claimed_at REAL,
                    completed_at REAL,
                    result_data TEXT,
                    provider_used TEXT,
                    model_used TEXT,
                    key_id TEXT,
                    tokens_consumed INTEGER DEFAULT 0,
                    processing_ms INTEGER DEFAULT 0,
                    retry_count INTEGER DEFAULT 0,
                    error_log TEXT DEFAULT '[]',
                    batch_id TEXT,
                    parent_task_id TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_ft_status_tier_priority
                    ON fleet_tasks(status, tier, priority DESC);
                CREATE INDEX IF NOT EXISTS idx_ft_batch
                    ON fleet_tasks(batch_id) WHERE batch_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_ft_status_claimed
                    ON fleet_tasks(status, claimed_at);
                CREATE INDEX IF NOT EXISTS idx_ft_completed_at
                    ON fleet_tasks(completed_at) WHERE status = 'completed';
            """)
            conn.commit()
        finally:
            conn.close()

    def enqueue(
        self,
        task_type: str,
        input_data: Dict[str, Any],
        tier: int = 3,
        priority: int = 5,
        deadline_secs: Optional[float] = None,
        batch_id: Optional[str] = None,
        parent_task_id: Optional[str] = None,
    ) -> str:
        """Add a task to the queue. Returns task_id."""
        task_id = str(uuid.uuid4())[:12]
        now = time.time()
        deadline = now + deadline_secs if deadline_secs else None

        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO fleet_tasks
                   (task_id, task_type, priority, tier, input_data, status,
                    created_at, deadline_at, batch_id, parent_task_id)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
                (task_id, task_type, priority, tier,
                 json.dumps(input_data), now, deadline, batch_id, parent_task_id),
            )
            conn.commit()
        finally:
            conn.close()

        log.debug("Enqueued task %s type=%s tier=%d pri=%d", task_id, task_type, tier, priority)
        return task_id

    def enqueue_batch(
        self,
        tasks: List[Dict[str, Any]],
        batch_id: Optional[str] = None,
    ) -> List[str]:
        """Enqueue multiple tasks atomically. Returns list of task_ids."""
        if not batch_id:
            batch_id = "batch-" + str(uuid.uuid4())[:8]

        task_ids = []
        now = time.time()
        conn = self._get_conn()
        try:
            for t in tasks:
                tid = str(uuid.uuid4())[:12]
                task_ids.append(tid)
                conn.execute(
                    """INSERT INTO fleet_tasks
                       (task_id, task_type, priority, tier, input_data, status,
                        created_at, batch_id)
                       VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
                    (tid, t["task_type"], t.get("priority", 5), t.get("tier", 3),
                     json.dumps(t.get("input_data", {})), now, batch_id),
                )
            conn.commit()
        finally:
            conn.close()

        log.info("Enqueued batch %s with %d tasks", batch_id, len(tasks))
        return task_ids

    def claim(self, worker_id: str, tier: int) -> Optional[Dict[str, Any]]:
        """Atomically claim the highest-priority pending task for a tier.

        Uses BEGIN IMMEDIATE to prevent race conditions.
        """
        now = time.time()
        conn = self._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """SELECT task_id FROM fleet_tasks
                   WHERE status = 'pending' AND tier = ?
                   ORDER BY priority DESC, created_at ASC
                   LIMIT 1""",
                (tier,),
            )
            row = cursor.fetchone()
            if not row:
                conn.execute("COMMIT")
                return None

            task_id = row["task_id"]
            conn.execute(
                """UPDATE fleet_tasks
                   SET status = 'claimed', claimed_by = ?, claimed_at = ?
                   WHERE task_id = ? AND status = 'pending'""",
                (worker_id, now, task_id),
            )
            conn.commit()

            cursor = conn.execute(
                "SELECT * FROM fleet_tasks WHERE task_id = ?", (task_id,),
            )
            result = cursor.fetchone()
            return dict(result) if result else None
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def start_processing(self, task_id: str):
        """Mark task as in-progress."""
        conn = self._get_conn()
        try:
            conn.execute(
                "UPDATE fleet_tasks SET status = 'in_progress' WHERE task_id = ?",
                (task_id,),
            )
            conn.commit()
        finally:
            conn.close()

    def complete(
        self,
        task_id: str,
        result: Dict[str, Any],
        provider: str = "",
        model: str = "",
        tokens: int = 0,
        processing_ms: int = 0,
    ):
        """Mark task as completed with result data."""
        conn = self._get_conn()
        try:
            conn.execute(
                """UPDATE fleet_tasks
                   SET status = 'completed', completed_at = ?,
                       result_data = ?, provider_used = ?, model_used = ?,
                       tokens_consumed = ?, processing_ms = ?
                   WHERE task_id = ?""",
                (time.time(), json.dumps(result), provider, model,
                 tokens, processing_ms, task_id),
            )
            conn.commit()
        finally:
            conn.close()

    def fail(self, task_id: str, error: str):
        """Record a failure. Re-queue if under retry limit, else dead-letter."""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "SELECT retry_count, error_log FROM fleet_tasks WHERE task_id = ?",
                (task_id,),
            )
            row = cursor.fetchone()
            if not row:
                return

            retry_count = row["retry_count"] + 1
            errors = json.loads(row["error_log"] or "[]")
            errors.append({"time": time.time(), "error": error[:500]})

            if retry_count >= self.MAX_RETRIES:
                new_status = self.DEAD_LETTER
                log.warning("Task %s -> dead letter after %d retries", task_id, retry_count)
            else:
                new_status = self.PENDING
                log.info("Task %s re-queued (retry %d/%d)", task_id, retry_count, self.MAX_RETRIES)

            conn.execute(
                """UPDATE fleet_tasks
                   SET status = ?, retry_count = ?, error_log = ?,
                       claimed_by = NULL, claimed_at = NULL
                   WHERE task_id = ?""",
                (new_status, retry_count, json.dumps(errors), task_id),
            )
            conn.commit()
        finally:
            conn.close()

    def recover_stale(self) -> int:
        """Reset stale in-progress tasks (>5 min) back to pending."""
        cutoff = time.time() - self.TASK_TIMEOUT_SECS
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """UPDATE fleet_tasks
                   SET status = 'pending', claimed_by = NULL, claimed_at = NULL
                   WHERE status IN ('claimed', 'in_progress')
                   AND claimed_at < ?""",
                (cutoff,),
            )
            conn.commit()
            count = cursor.rowcount
            if count > 0:
                log.info("Recovered %d stale tasks", count)
            return count
        finally:
            conn.close()

    def retry_dead_letters(self) -> int:
        """Retry dead-letter tasks with retry_count < MAX_TOTAL_RETRIES."""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """UPDATE fleet_tasks
                   SET status = 'pending', claimed_by = NULL, claimed_at = NULL
                   WHERE status = 'dead_letter' AND retry_count < ?""",
                (self.MAX_TOTAL_RETRIES,),
            )
            conn.commit()
            count = cursor.rowcount
            if count > 0:
                log.info("Re-queued %d dead-letter tasks for retry", count)
            return count
        finally:
            conn.close()

    def cleanup_old(self, max_age_hours: int = 24) -> int:
        """Delete completed/failed tasks older than max_age_hours. Prevents unbounded growth."""
        cutoff = time.time() - (max_age_hours * 3600)
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "DELETE FROM fleet_tasks WHERE status IN ('completed', 'failed') AND completed_at < ?",
                (cutoff,),
            )
            conn.commit()
            count = cursor.rowcount
            if count > 0:
                log.info("Cleaned up %d old tasks (>%dh)", count, max_age_hours)
            return count
        finally:
            conn.close()

    def get_stats(self) -> Dict[str, Any]:
        """Queue statistics for dashboard."""
        conn = self._get_conn()
        try:
            stats = {}
            for status in [self.PENDING, self.CLAIMED, self.IN_PROGRESS,
                           self.COMPLETED, self.FAILED, self.DEAD_LETTER]:
                cursor = conn.execute(
                    "SELECT COUNT(*) as c FROM fleet_tasks WHERE status = ?", (status,),
                )
                stats[status] = cursor.fetchone()["c"]

            tier_stats = {}
            for tier in [1, 2, 3]:
                cursor = conn.execute(
                    """SELECT status, COUNT(*) as c FROM fleet_tasks
                       WHERE tier = ? GROUP BY status""",
                    (tier,),
                )
                tier_stats[tier] = {row["status"]: row["c"] for row in cursor.fetchall()}
            stats["by_tier"] = tier_stats

            today_start = time.time() - (time.time() % 86400)
            cursor = conn.execute(
                """SELECT COUNT(*) as c, COALESCE(SUM(tokens_consumed), 0) as tokens,
                          COALESCE(SUM(processing_ms), 0) as ms
                   FROM fleet_tasks
                   WHERE status = 'completed' AND completed_at >= ?""",
                (today_start,),
            )
            row = cursor.fetchone()
            stats["today"] = {
                "completed": row["c"],
                "tokens": int(row["tokens"] or 0),
                "processing_ms": int(row["ms"] or 0),
            }

            return stats
        finally:
            conn.close()

    def get_dead_letters(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get dead-letter tasks for manual review."""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """SELECT * FROM fleet_tasks
                   WHERE status = 'dead_letter'
                   ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_recent_completed(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Recent completed tasks."""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """SELECT * FROM fleet_tasks
                   WHERE status = 'completed'
                   ORDER BY completed_at DESC LIMIT ?""",
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_batch_status(self, batch_id: str) -> Dict[str, Any]:
        """Check if all tasks in a batch are complete."""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """SELECT status, COUNT(*) as c FROM fleet_tasks
                   WHERE batch_id = ? GROUP BY status""",
                (batch_id,),
            )
            status_counts = {row["status"]: row["c"] for row in cursor.fetchall()}
            total = sum(status_counts.values())
            completed = status_counts.get("completed", 0)
            return {
                "batch_id": batch_id,
                "total": total,
                "completed": completed,
                "all_done": completed == total and total > 0,
                "status_counts": status_counts,
            }
        finally:
            conn.close()

    def get_activity_feed(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Recent task activity for live feed display."""
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """SELECT task_id, task_type, tier, status, provider_used,
                          model_used, tokens_consumed, processing_ms,
                          created_at, claimed_at, completed_at, claimed_by
                   FROM fleet_tasks
                   ORDER BY COALESCE(completed_at, claimed_at, created_at) DESC
                   LIMIT ?""",
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def count_pending(self, tier: Optional[int] = None) -> int:
        """Count pending tasks, optionally filtered by tier."""
        conn = self._get_conn()
        try:
            if tier:
                cursor = conn.execute(
                    "SELECT COUNT(*) as c FROM fleet_tasks WHERE status = 'pending' AND tier = ?",
                    (tier,),
                )
            else:
                cursor = conn.execute(
                    "SELECT COUNT(*) as c FROM fleet_tasks WHERE status = 'pending'",
                )
            return cursor.fetchone()["c"]
        finally:
            conn.close()
