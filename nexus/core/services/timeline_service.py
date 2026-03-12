"""
Timeline Service — structured activity logging for leads.
Every action in the system logs here. No silent updates.
"""
import sqlite3, json
from datetime import datetime
from pathlib import Path

DB_PATH = Path.home() / ".nexus" / "memory.db"


class TimelineService:
    def __init__(self, db_path=None):
        self.db_path = str(db_path or DB_PATH)

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def log_event(self, lead_id: int, event_type: str, details: str = "",
                  actor: str = "system", metadata: dict = None):
        conn = self._conn()
        conn.execute(
            "INSERT INTO lead_events (lead_id, event_type, details, actor, metadata) VALUES (?,?,?,?,?)",
            (lead_id, event_type, details, actor, json.dumps(metadata or {}))
        )
        conn.commit()
        conn.close()

    def get_timeline(self, lead_id: int, limit: int = 50) -> list:
        conn = self._conn()
        rows = conn.execute(
            "SELECT id, ts, event_type, details, actor, metadata FROM lead_events "
            "WHERE lead_id = ? ORDER BY ts DESC LIMIT ?",
            (lead_id, limit)
        ).fetchall()
        conn.close()
        return [
            {"id": r[0], "ts": r[1], "event_type": r[2], "details": r[3],
             "actor": r[4] or "system", "metadata": _parse_json(r[5])}
            for r in rows
        ]

    def get_recent_activity(self, limit: int = 20) -> list:
        conn = self._conn()
        rows = conn.execute(
            "SELECT e.id, e.ts, e.lead_id, e.event_type, e.details, e.actor, "
            "l.first_name, l.last_name, l.phone "
            "FROM lead_events e LEFT JOIN leads l ON e.lead_id = l.id "
            "ORDER BY e.ts DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        return [
            {"id": r[0], "ts": r[1], "lead_id": r[2], "event_type": r[3],
             "details": r[4], "actor": r[5] or "system",
             "lead_name": f"{r[6] or ''} {r[7] or ''}".strip(),
             "lead_phone": r[8] or ""}
            for r in rows
        ]


def _parse_json(s):
    try:
        return json.loads(s) if s else {}
    except:
        return {}
