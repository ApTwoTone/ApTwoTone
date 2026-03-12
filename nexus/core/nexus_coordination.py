from __future__ import annotations

"""Nexus coordination storage backed by SQLite.

Provides:
- `nexus_events` table for cross-system event logging
- `kai_instructions` table for persistent operator instructions
"""

import json
import os
import sqlite3
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"

_SCHEMA_READY = False
_ALERT_EVENTS = {
    "NEW_LEAD": "🔥 NEW LEAD",
    "EMAIL_BOUNCED": "⚠️ EMAIL BOUNCED",
    "VENDOR_REPLIED": "📩 VENDOR REPLIED",
    "EMERGENCY_STOP": "🚨 EMERGENCY STOP",
    "AGENT_CRASHED": "💀 AGENT CRASHED",
    "SMTP_AUTH_FAILED": "🚨 SMTP AUTH FAILED",
    "DAILY_LIMIT_REACHED": "📧 DAILY LIMIT REACHED",
}


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    cols = conn.execute("PRAGMA table_info(%s)" % table).fetchall()
    names = {row[1] for row in cols}
    if column not in names:
        conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, ddl))


def _load_config() -> Dict[str, Any]:
    try:
        if CONFIG_PATH.exists():
            return json.loads(CONFIG_PATH.read_text())
    except Exception:
        pass
    return {}


def _send_telegram_alert(text: str) -> None:
    """Best-effort Telegram push for critical events."""
    cfg = _load_config()
    token = (
        cfg.get("telegram_token")
        or cfg.get("telegram_bot_token")
        or os.environ.get("TELEGRAM_TOKEN", "")
    )
    chat_ids = cfg.get("telegram_chat_ids") or []
    single_chat = cfg.get("telegram_chat_id")
    if single_chat:
        chat_ids = [single_chat] + [c for c in chat_ids if str(c) != str(single_chat)]
    if not token or not chat_ids:
        return

    for chat_id in chat_ids[:5]:
        try:
            payload = json.dumps(
                {"chat_id": str(chat_id), "text": text[:3900]}
            ).encode()
            req = urllib.request.Request(
                "https://api.telegram.org/bot%s/sendMessage" % token,
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=8)
        except Exception:
            continue


def init_coordination_tables() -> None:
    """Create coordination tables if they do not already exist."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return

    conn = _conn()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS nexus_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                source TEXT DEFAULT '',
                lead_id INTEGER,
                vendor_id INTEGER,
                payload TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_nexus_events_type_time
            ON nexus_events(event_type, created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_nexus_events_lead
            ON nexus_events(lead_id, created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_nexus_events_vendor
            ON nexus_events(vendor_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS kai_instructions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instruction TEXT NOT NULL,
                priority TEXT DEFAULT 'normal',
                source TEXT DEFAULT 'telegram',
                chat_id TEXT DEFAULT '',
                created_by TEXT DEFAULT 'kai',
                consumed INTEGER DEFAULT 0,
                consumed_by TEXT DEFAULT '',
                consumed_at TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_kai_instructions_time
            ON kai_instructions(created_at DESC);
            """
        )

        # Backward-compatible schema upgrades for already-existing tables.
        _ensure_column(conn, "kai_instructions", "priority", "TEXT DEFAULT 'normal'")
        _ensure_column(conn, "kai_instructions", "consumed", "INTEGER DEFAULT 0")
        _ensure_column(conn, "kai_instructions", "consumed_by", "TEXT DEFAULT ''")
        _ensure_column(conn, "kai_instructions", "consumed_at", "TEXT DEFAULT ''")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kai_instructions_consumed "
            "ON kai_instructions(consumed, id DESC)"
        )

        conn.commit()
        _SCHEMA_READY = True
    finally:
        conn.close()


def log_event(
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
    source: str = "",
    lead_id: Optional[int] = None,
    vendor_id: Optional[int] = None,
    push_alert: bool = True,
) -> int:
    """Append an event to `nexus_events`. Returns event row id."""
    init_coordination_tables()
    conn = _conn()
    try:
        cur = conn.execute(
            """
            INSERT INTO nexus_events (event_type, source, lead_id, vendor_id, payload)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                event_type,
                source,
                lead_id,
                vendor_id,
                json.dumps(payload or {}, default=str),
            ),
        )
        conn.commit()
        row_id = int(cur.lastrowid or 0)
    finally:
        conn.close()

    if push_alert and event_type in _ALERT_EVENTS:
        try:
            prefix = _ALERT_EVENTS[event_type]
            details = payload or {}
            if isinstance(details, dict):
                detail_lines = []
                for k, v in details.items():
                    detail_lines.append("%s: %s" % (k, v))
                detail_text = "\n".join(detail_lines[:12]) if detail_lines else ""
            else:
                detail_text = str(details)
            msg = "%s\n\n%sSource: %s" % (
                prefix,
                (detail_text + "\n\n") if detail_text else "",
                source or "unknown",
            )
            _send_telegram_alert(msg)
        except Exception:
            pass

    return row_id


def add_kai_instruction(
    instruction: str,
    priority: str = "normal",
    source: str = "telegram",
    chat_id: str = "",
    created_by: str = "kai",
) -> int:
    """Persist a plain-text instruction from Kai and log KAI_INSTRUCTION event."""
    init_coordination_tables()
    conn = _conn()
    try:
        cur = conn.execute(
            """
            INSERT INTO kai_instructions (instruction, priority, source, chat_id, created_by)
            VALUES (?, ?, ?, ?, ?)
            """,
            (instruction.strip(), priority, source, str(chat_id or ""), created_by),
        )
        conn.commit()
        instruction_id = int(cur.lastrowid or 0)
    finally:
        conn.close()

    log_event(
        "KAI_INSTRUCTION",
        payload={
            "instruction_id": instruction_id,
            "instruction": instruction.strip(),
            "priority": priority,
        },
        source=source,
        push_alert=False,
    )
    return instruction_id


def save_kai_instruction(
    instruction: str,
    priority: str = "normal",
    source: str = "telegram",
    chat_id: str = "",
    created_by: str = "kai",
) -> int:
    """Compatibility wrapper."""
    return add_kai_instruction(
        instruction=instruction,
        priority=priority,
        source=source,
        chat_id=chat_id,
        created_by=created_by,
    )


def get_latest_instruction(after_id: int = 0) -> Optional[Dict[str, Any]]:
    """Return the latest instruction newer than `after_id`, if any."""
    init_coordination_tables()
    conn = _conn()
    try:
        row = conn.execute(
            """
            SELECT id, instruction, priority, source, chat_id, created_by,
                   consumed, consumed_by, consumed_at, created_at
            FROM kai_instructions
            WHERE id > ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (after_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_unconsumed_instructions(limit: int = 20) -> List[Dict[str, Any]]:
    """Read unconsumed Kai instructions, newest first."""
    init_coordination_tables()
    conn = _conn()
    try:
        rows = conn.execute(
            """
            SELECT id, instruction, priority, source, chat_id, created_by, created_at
            FROM kai_instructions
            WHERE COALESCE(consumed, 0) = 0
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, min(limit, 500)),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_instruction_consumed(instruction_id: int, consumed_by: str) -> None:
    """Mark an instruction as consumed by an agent/process."""
    init_coordination_tables()
    conn = _conn()
    try:
        conn.execute(
            """
            UPDATE kai_instructions
            SET consumed = 1,
                consumed_by = ?,
                consumed_at = datetime('now')
            WHERE id = ?
            """,
            (consumed_by, int(instruction_id)),
        )
        conn.commit()
    finally:
        conn.close()


def get_recent_events(limit: int = 50, event_type: str = "") -> List[Dict[str, Any]]:
    """Read recent events from `nexus_events`."""
    init_coordination_tables()
    conn = _conn()
    try:
        if event_type:
            rows = conn.execute(
                """
                SELECT id, event_type, source, lead_id, vendor_id, payload, created_at
                FROM nexus_events
                WHERE event_type = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (event_type, max(1, min(limit, 500))),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, event_type, source, lead_id, vendor_id, payload, created_at
                FROM nexus_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(limit, 500)),),
            ).fetchall()

        out: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item.get("payload") or "{}")
            except Exception:
                item["payload"] = {"raw": item.get("payload", "")}
            out.append(item)
        return out
    finally:
        conn.close()
