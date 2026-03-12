"""
Agent Runtime — Unified heartbeat reporting for Nexus daemons.

Each daemon creates an AgentRuntime instance at startup, calls heartbeat()
inside its main loop, and shutdown() on exit. Data goes to BOTH:
  - agent_status table (read by /api/agents/* and /api/runtime/*)
  - agent_registry table (backward compat for older code)

Usage:
    from core.agent_runtime import AgentRuntime

    rt = AgentRuntime("master-coordinator", "d0_core",
                      launchd_label="com.zoar.master-coordinator")
    while running:
        rt.heartbeat("Feeding D1 vendor tasks")
    rt.shutdown()
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("agent_runtime")
DB_PATH = Path.home() / ".nexus" / "memory.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _ensure_tables():
    """Create agent_registry if it doesn't exist (backward compat)."""
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            agent_type TEXT DEFAULT '',
            category TEXT DEFAULT '',
            pid INTEGER DEFAULT 0,
            status TEXT DEFAULT 'stopped',
            current_task TEXT DEFAULT '',
            last_heartbeat TEXT DEFAULT '',
            started_at TEXT DEFAULT '',
            error_message TEXT DEFAULT '',
            error_count INTEGER DEFAULT 0,
            cycles_completed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


try:
    _ensure_tables()
except Exception:
    pass


class AgentRuntime:
    """Tracks a daemon's lifecycle in agent_status + agent_registry."""

    def __init__(
        self,
        agent_id: str,
        dept_id: str = "d0_core",
        daemon_type: str = "launchd",
        launchd_label: str = "",
        # Backward-compat kwargs
        agent_type: str = "",
        category: str = "",
    ):
        self.agent_id = agent_id
        self.dept_id = dept_id
        self.daemon_type = daemon_type
        self.launchd_label = launchd_label
        self._agent_type = agent_type or daemon_type
        self._category = category or dept_id
        self._pid = os.getpid()
        self._started_at = datetime.now().isoformat()
        self._start_time = time.time()
        self._cycles = 0

        self._register()

    # Backward-compat alias
    @property
    def name(self):
        return self.agent_id

    def _register(self):
        """Insert/update both tables."""
        now_ts = time.time()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = _conn()
        try:
            # Primary: agent_status
            conn.execute("""
                INSERT INTO agent_status (
                    agent_id, dept_id, status, current_task, pid,
                    daemon_type, launchd_label, started_at, last_heartbeat
                ) VALUES (?, ?, 'starting', '', ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    status = 'starting',
                    dept_id = excluded.dept_id,
                    pid = excluded.pid,
                    daemon_type = excluded.daemon_type,
                    launchd_label = excluded.launchd_label,
                    started_at = excluded.started_at,
                    last_heartbeat = excluded.last_heartbeat,
                    error_message = ''
            """, (
                self.agent_id, self.dept_id, self._pid,
                self.daemon_type, self.launchd_label, self._started_at, now_ts,
            ))

            # Backward compat: agent_registry
            conn.execute("""
                INSERT INTO agent_registry (name, agent_type, category, pid, status, started_at, last_heartbeat, updated_at)
                VALUES (?, ?, ?, ?, 'starting', ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    pid = excluded.pid,
                    status = 'starting',
                    started_at = excluded.started_at,
                    last_heartbeat = excluded.last_heartbeat,
                    error_message = '',
                    updated_at = excluded.updated_at
            """, (self.agent_id, self._agent_type, self._category, self._pid, now_str, now_str, now_str))

            conn.commit()
        except Exception as e:
            log.warning("AgentRuntime register failed for %s: %s", self.agent_id, e)
        finally:
            conn.close()

        # Register with watchdog
        try:
            from core.watchdog import register_service
            register_service(self.agent_id)
        except Exception:
            pass

        log.info("[Runtime] %s registered (pid=%d)", self.agent_id, self._pid)

    def start(self):
        """Backward-compat alias — registration happens in __init__."""
        self.heartbeat("Started")

    def heartbeat(self, current_task: str = "", progress_pct: int = 0):
        """Update status to running with current task. Call periodically."""
        self._cycles += 1
        now_ts = time.time()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        uptime = int(now_ts - self._start_time)

        conn = _conn()
        try:
            # Primary: agent_status
            conn.execute("""
                UPDATE agent_status SET
                    status = 'running',
                    current_task = ?,
                    progress_pct = ?,
                    last_heartbeat = ?,
                    uptime_seconds = ?,
                    pid = ?,
                    last_active = datetime('now')
                WHERE agent_id = ?
            """, (current_task, progress_pct, now_ts, uptime, self._pid, self.agent_id))

            # Backward compat: agent_registry
            conn.execute("""
                UPDATE agent_registry SET
                    last_heartbeat = ?, current_task = ?, pid = ?,
                    status = 'running', cycles_completed = ?, updated_at = ?
                WHERE name = ?
            """, (now_str, current_task, self._pid, self._cycles, now_str, self.agent_id))

            conn.commit()
        except Exception as e:
            log.warning("AgentRuntime.heartbeat failed for %s: %s", self.agent_id, e)
        finally:
            conn.close()

        # Watchdog heartbeat
        try:
            from core.watchdog import heartbeat as wd_heartbeat
            wd_heartbeat(self.agent_id)
        except Exception:
            pass

    def complete_task(self, task_description: str = ""):
        """Mark current task as complete. Increments tasks_today."""
        conn = _conn()
        try:
            conn.execute("""
                UPDATE agent_status SET
                    status = 'idle',
                    current_task = '',
                    progress_pct = 0,
                    tasks_today = tasks_today + 1,
                    last_heartbeat = ?,
                    last_active = datetime('now')
                WHERE agent_id = ?
            """, (time.time(), self.agent_id))
            conn.commit()
        except Exception as e:
            log.warning("AgentRuntime.complete_task failed for %s: %s", self.agent_id, e)
        finally:
            conn.close()

        # Log via activity logger for significant events
        try:
            from core.agent_activity import AgentActivityLogger
            logger = AgentActivityLogger()
            logger.log_activity(
                self.agent_id, self.dept_id,
                "task_complete", detail=task_description[:200],
            )
        except Exception:
            pass

    def report_error(self, error_msg: str, will_retry: bool = True):
        """Set status to error/retrying."""
        status = "retrying" if will_retry else "error"
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = _conn()
        try:
            conn.execute("""
                UPDATE agent_status SET
                    status = ?,
                    error_message = ?,
                    last_heartbeat = ?,
                    last_active = datetime('now')
                WHERE agent_id = ?
            """, (status, error_msg[:500], time.time(), self.agent_id))

            conn.execute("""
                UPDATE agent_registry SET
                    status = ?, error_message = ?,
                    error_count = error_count + 1, updated_at = ?
                WHERE name = ?
            """, (status, error_msg[:500], now_str, self.agent_id))

            conn.commit()
        except Exception as e:
            log.warning("AgentRuntime.report_error failed for %s: %s", self.agent_id, e)
        finally:
            conn.close()

        try:
            from core.agent_activity import AgentActivityLogger
            logger = AgentActivityLogger()
            logger.log_activity(
                self.agent_id, self.dept_id,
                "error", detail=error_msg[:200], status="error",
            )
        except Exception:
            pass

    def shutdown(self):
        """Mark agent as stopped. Call on graceful exit."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = _conn()
        try:
            conn.execute("""
                UPDATE agent_status SET
                    status = 'stopped',
                    current_task = '',
                    progress_pct = 0,
                    last_heartbeat = ?,
                    last_active = datetime('now')
                WHERE agent_id = ?
            """, (time.time(), self.agent_id))

            conn.execute("""
                UPDATE agent_registry SET
                    status = 'stopped', current_task = '', updated_at = ?
                WHERE name = ?
            """, (now_str, self.agent_id))

            conn.commit()
        except Exception as e:
            log.warning("AgentRuntime.shutdown failed for %s: %s", self.agent_id, e)
        finally:
            conn.close()

        try:
            from core.watchdog import unregister_service
            unregister_service(self.agent_id)
        except Exception:
            pass

        log.info("[Runtime] %s stopped (uptime=%ds)", self.agent_id, int(time.time() - self._start_time))

    # Backward-compat alias
    stop = shutdown


def get_all_agent_statuses() -> list:
    """Read all agent statuses from agent_status table. Used by runtime_api."""
    conn = _conn()
    rows = conn.execute("""
        SELECT agent_id, dept_id, status, current_task, progress_pct,
               tasks_today, runtime_seconds, last_active, error_message,
               last_heartbeat, daemon_type, launchd_label, pid,
               started_at, uptime_seconds
        FROM agent_status
        ORDER BY dept_id, agent_id
    """).fetchall()
    conn.close()

    now = time.time()
    agents = []
    for r in rows:
        d = dict(r)
        hb = d.get("last_heartbeat", 0) or 0
        d["heartbeat_age_seconds"] = round(now - hb, 1) if hb > 0 else -1
        agents.append(d)
    return agents
