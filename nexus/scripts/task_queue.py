#!/usr/bin/env python3
"""
Persistent Task Queue — SQLite-backed task queue with checkpoint/resume.

All tasks are written to the queue BEFORE execution. If the system crashes,
tasks can be resumed from their last checkpoint on restart.

Usage:
    from scripts.task_queue import TaskQueue
    q = TaskQueue()
    task_id = q.enqueue("ad-copywriter", "marketing", "Write 5 ad variations", priority=5)
    q.start(task_id)
    q.checkpoint(task_id, {"progress": "3/5 done"})
    q.complete(task_id, {"result": "5 ads written"})

    # On crash recovery:
    interrupted = q.get_interrupted()
    for task in interrupted:
        q.resume(task["id"])
"""
import json
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

NEXUS_DIR = Path.home() / ".nexus"
DB_PATH = NEXUS_DIR / "task_queue.db"

log = logging.getLogger("task_queue")


class TaskQueue:
    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                department TEXT NOT NULL,
                description TEXT NOT NULL,
                priority INTEGER DEFAULT 5,
                status TEXT DEFAULT 'queued',
                model_assigned TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                checkpoint_data TEXT,
                result_data TEXT,
                error TEXT,
                retry_count INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 3
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_agent ON tasks(agent_id)
        """)
        conn.commit()
        conn.close()

    def _conn(self):
        return sqlite3.connect(str(self.db_path))

    def enqueue(self, agent_id, department, description, priority=5, max_retries=3):
        """Add a task to the queue. Returns task ID."""
        conn = self._conn()
        cur = conn.execute(
            "INSERT INTO tasks (agent_id, department, description, priority, status, created_at, max_retries) "
            "VALUES (?, ?, ?, ?, 'queued', ?, ?)",
            (agent_id, department, description, priority, datetime.now().isoformat(), max_retries)
        )
        task_id = cur.lastrowid
        conn.commit()
        conn.close()
        log.info(f"Enqueued task {task_id}: {agent_id} ({department})")
        return task_id

    def start(self, task_id, model=None):
        """Mark a task as started."""
        conn = self._conn()
        conn.execute(
            "UPDATE tasks SET status='running', started_at=?, model_assigned=? WHERE id=?",
            (datetime.now().isoformat(), model, task_id)
        )
        conn.commit()
        conn.close()

    def checkpoint(self, task_id, data):
        """Save checkpoint data for a running task."""
        conn = self._conn()
        conn.execute(
            "UPDATE tasks SET checkpoint_data=? WHERE id=?",
            (json.dumps(data), task_id)
        )
        conn.commit()
        conn.close()

    def complete(self, task_id, result_data=None):
        """Mark a task as completed."""
        conn = self._conn()
        conn.execute(
            "UPDATE tasks SET status='completed', completed_at=?, result_data=? WHERE id=?",
            (datetime.now().isoformat(), json.dumps(result_data) if result_data else None, task_id)
        )
        conn.commit()
        conn.close()

    def fail(self, task_id, error_msg):
        """Mark a task as failed. Auto-requeue if retries remain."""
        conn = self._conn()
        row = conn.execute(
            "SELECT retry_count, max_retries FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        if row and row[0] < row[1]:
            conn.execute(
                "UPDATE tasks SET status='queued', retry_count=retry_count+1, error=? WHERE id=?",
                (error_msg, task_id)
            )
            log.info(f"Task {task_id} requeued (retry {row[0]+1}/{row[1]})")
        else:
            conn.execute(
                "UPDATE tasks SET status='failed', error=?, completed_at=? WHERE id=?",
                (error_msg, datetime.now().isoformat(), task_id)
            )
            log.warning(f"Task {task_id} permanently failed: {error_msg}")
        conn.commit()
        conn.close()

    def get_next(self):
        """Get the next queued task (highest priority first)."""
        conn = self._conn()
        row = conn.execute(
            "SELECT id, agent_id, department, description, priority, checkpoint_data "
            "FROM tasks WHERE status='queued' ORDER BY priority DESC, created_at ASC LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            return {
                "id": row[0], "agent_id": row[1], "department": row[2],
                "description": row[3], "priority": row[4],
                "checkpoint": json.loads(row[5]) if row[5] else None,
            }
        return None

    def get_interrupted(self):
        """Get all tasks that were running when system crashed."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT id, agent_id, department, description, priority, checkpoint_data, model_assigned "
            "FROM tasks WHERE status='running' ORDER BY priority DESC"
        ).fetchall()
        conn.close()
        return [
            {
                "id": r[0], "agent_id": r[1], "department": r[2],
                "description": r[3], "priority": r[4],
                "checkpoint": json.loads(r[5]) if r[5] else None,
                "model": r[6],
            }
            for r in rows
        ]

    def resume(self, task_id):
        """Reset a running task back to queued for re-execution."""
        conn = self._conn()
        conn.execute(
            "UPDATE tasks SET status='queued' WHERE id=? AND status='running'",
            (task_id,)
        )
        conn.commit()
        conn.close()

    def get_stats(self):
        """Get queue statistics."""
        conn = self._conn()
        stats = {}
        for status in ["queued", "running", "completed", "failed"]:
            count = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status=?", (status,)
            ).fetchone()[0]
            stats[status] = count
        stats["total"] = sum(stats.values())
        conn.close()
        return stats

    def get_recent(self, limit=20):
        """Get recent tasks for display."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT id, agent_id, department, status, model_assigned, created_at, completed_at "
            "FROM tasks ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        return [
            {
                "id": r[0], "agent_id": r[1], "department": r[2],
                "status": r[3], "model": r[4],
                "created": r[5], "completed": r[6],
            }
            for r in rows
        ]

    def cleanup_old(self, days=7):
        """Remove completed tasks older than N days."""
        conn = self._conn()
        conn.execute(
            "DELETE FROM tasks WHERE status='completed' AND completed_at < datetime('now', ?)",
            (f"-{days} days",)
        )
        conn.commit()
        conn.close()
