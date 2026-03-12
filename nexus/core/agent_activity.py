"""
Nexus Agent Activity — Human-readable activity logger for inter-agent communication.

Provides in-memory ring buffer + SQLite persistence + SSE broadcast.
Frontend subscribes via /api/agents/activity-stream for real-time updates.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("agent_activity")

DB_PATH = Path.home() / ".nexus" / "memory.db"
RING_BUFFER_SIZE = 200


class AgentActivityLogger:
    """Logs agent activities and broadcasts them via SSE."""

    def __init__(self):
        self._buffer = collections.deque(maxlen=RING_BUFFER_SIZE)
        self._subscribers = []  # type: List[asyncio.Queue]
        self._counter = 0

    def log_activity(
        self,
        agent_id: str,
        dept_id: str,
        action: str,
        detail: str = "",
        target: str = "",
        status: str = "success",
    ):
        """Log an agent activity. Stored in ring buffer, DB, and broadcast via SSE."""
        self._counter += 1
        entry = {
            "id": self._counter,
            "ts": time.time(),
            "agent_id": agent_id,
            "dept_id": dept_id,
            "action": action,
            "detail": detail[:500],
            "target": target,
            "status": status,
        }

        # Ring buffer
        self._buffer.append(entry)

        # Persist to DB (fire and forget)
        try:
            self._persist(entry)
        except Exception:
            pass

        # Broadcast to SSE subscribers
        self._broadcast(entry)

        log.debug("Activity: [%s] %s → %s", agent_id, action, detail[:80])

    def get_recent(self, limit: int = 50) -> List[Dict]:
        """Get recent activities from ring buffer."""
        items = list(self._buffer)
        return items[-limit:]

    def subscribe(self) -> asyncio.Queue:
        """Subscribe to real-time activity feed. Returns an asyncio.Queue."""
        q = asyncio.Queue(maxsize=100)  # type: asyncio.Queue
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        """Remove a subscriber."""
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    def _broadcast(self, entry: Dict):
        """Push entry to all SSE subscribers."""
        dead = []
        for q in self._subscribers:
            try:
                q.put_nowait(entry)
            except asyncio.QueueFull:
                dead.append(q)
        # Clean up full queues
        for q in dead:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def _persist(self, entry: Dict):
        """Write activity to SQLite."""
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=5)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute(
                """INSERT OR IGNORE INTO agent_comms
                   (from_agent, from_dept, to_agent, message_type, content, priority)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    entry["agent_id"],
                    entry["dept_id"],
                    entry.get("target", "broadcast"),
                    entry["action"],
                    entry["detail"],
                    "normal",
                ),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass  # Non-critical

    def get_stats(self) -> Dict:
        """Activity statistics."""
        return {
            "buffer_size": len(self._buffer),
            "total_logged": self._counter,
            "active_subscribers": len(self._subscribers),
        }


# Singleton
_logger = None  # type: Optional[AgentActivityLogger]


def get_activity_logger() -> AgentActivityLogger:
    global _logger
    if _logger is None:
        _logger = AgentActivityLogger()
    return _logger


def log_activity(agent_id: str, dept_id: str, action: str,
                 detail: str = "", target: str = "", status: str = "success"):
    """Convenience function for quick logging."""
    get_activity_logger().log_activity(agent_id, dept_id, action, detail, target, status)
