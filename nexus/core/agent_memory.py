"""
Agent Memory — Persistent context across chat sessions.

Simple keyword-based memory stored in SQLite. No embeddings, no AI calls.
- remember(key, value) — upsert a fact
- recall(query) — keyword search, returns top matches
- forget(key) — delete a memory
- get_relevant_context(message) — extract keywords, return matching memories
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("agent_memory")

DB_PATH = Path.home() / ".nexus" / "memory.db"


def _get_conn():
    # type: () -> sqlite3.Connection
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def init_memory_table():
    """Create agent_memory table if it doesn't exist."""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            source TEXT DEFAULT 'chat',
            created_at TEXT DEFAULT (datetime('now')),
            last_accessed TEXT DEFAULT (datetime('now')),
            access_count INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_key ON agent_memory(key)
    """)
    # FTS5 index for fast full-text recall
    try:
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS agent_memory_fts
            USING fts5(key, value, content=agent_memory, content_rowid=id)
        """)
        # Triggers to keep FTS in sync
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON agent_memory BEGIN
                INSERT INTO agent_memory_fts(rowid, key, value)
                VALUES (new.id, new.key, new.value);
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON agent_memory BEGIN
                INSERT INTO agent_memory_fts(agent_memory_fts, rowid, key, value)
                VALUES ('delete', old.id, old.key, old.value);
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE ON agent_memory BEGIN
                INSERT INTO agent_memory_fts(agent_memory_fts, rowid, key, value)
                VALUES ('delete', old.id, old.key, old.value);
                INSERT INTO agent_memory_fts(rowid, key, value)
                VALUES (new.id, new.key, new.value);
            END
        """)
    except Exception as e:
        log.warning("FTS5 init skipped: %s", e)
    conn.commit()
    conn.close()


def remember(key, value, source="chat"):
    # type: (str, str, str) -> None
    """Store or update a memory. Upserts on key."""
    conn = _get_conn()
    existing = conn.execute(
        "SELECT id FROM agent_memory WHERE key = ?", (key,)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE agent_memory SET value = ?, source = ?, "
            "last_accessed = datetime('now') WHERE key = ?",
            (value, source, key),
        )
    else:
        conn.execute(
            "INSERT INTO agent_memory (key, value, source) VALUES (?, ?, ?)",
            (key, value, source),
        )
    conn.commit()
    conn.close()
    log.info("Remembered: %s = %s", key, value[:100])


def recall(query):
    # type: (str) -> List[Dict]
    """Search memories by keyword. Uses FTS5 if available, falls back to LIKE."""
    conn = _get_conn()
    words = [w.strip() for w in query.lower().split() if len(w.strip()) > 2]
    if not words:
        conn.close()
        return []

    rows = None
    # Try FTS5 first
    try:
        fts_query = " OR ".join(words[:5])
        rows = conn.execute(
            "SELECT m.id, m.key, m.value, m.source, m.created_at, m.access_count "
            "FROM agent_memory m JOIN agent_memory_fts f ON m.id = f.rowid "
            "WHERE agent_memory_fts MATCH ? "
            "ORDER BY m.access_count DESC, m.last_accessed DESC LIMIT 5",
            (fts_query,),
        ).fetchall()
    except Exception:
        rows = None

    # Fallback to LIKE
    if rows is None:
        conditions = []
        params = []
        for word in words[:5]:
            conditions.append("(LOWER(key) LIKE ? OR LOWER(value) LIKE ?)")
            params.extend(["%" + word + "%", "%" + word + "%"])
        sql = (
            "SELECT id, key, value, source, created_at, access_count "
            "FROM agent_memory WHERE " + " OR ".join(conditions) +
            " ORDER BY access_count DESC, last_accessed DESC LIMIT 5"
        )
        rows = conn.execute(sql, params).fetchall()

    results = []
    ids = []
    for row in rows:
        results.append({
            "key": row["key"],
            "value": row["value"],
            "source": row["source"],
        })
        ids.append(row["id"])

    # Update access stats
    if ids:
        placeholders = ",".join("?" * len(ids))
        conn.execute(
            "UPDATE agent_memory SET access_count = access_count + 1, "
            "last_accessed = datetime('now') WHERE id IN (%s)" % placeholders,
            ids,
        )
        conn.commit()

    conn.close()
    return results


def forget(key):
    # type: (str) -> bool
    """Delete a memory by exact key. Returns True if deleted."""
    conn = _get_conn()
    cursor = conn.execute("DELETE FROM agent_memory WHERE key = ?", (key,))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    if deleted:
        log.info("Forgot: %s", key)
    return deleted


def get_all_memories(limit=50):
    # type: (int) -> List[Dict]
    """Get all memories, most accessed first."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT key, value, source, access_count, created_at "
        "FROM agent_memory ORDER BY access_count DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_relevant_context(message):
    # type: (str) -> str
    """Extract keywords from message, search memories, return context string."""
    # Skip very short messages
    if len(message) < 4:
        return ""

    stopwords = {
        "the", "is", "at", "which", "on", "a", "an", "and", "or", "but",
        "in", "with", "to", "for", "of", "not", "no", "can", "you", "my",
        "me", "do", "does", "what", "how", "why", "who", "where", "when",
        "this", "that", "it", "its", "from", "was", "were", "been", "has",
        "have", "had", "will", "would", "could", "should", "may", "might",
        "shall", "about", "are", "just", "please", "tell", "know",
    }

    words = re.findall(r'[a-zA-Z]+', message.lower())
    keywords = [w for w in words if w not in stopwords and len(w) > 2]

    if not keywords:
        return ""

    results = recall(" ".join(keywords[:5]))
    if not results:
        return ""

    parts = []
    for r in results[:3]:
        parts.append("%s: %s" % (r["key"], r["value"]))

    return "REMEMBERED CONTEXT:\n" + "\n".join(parts)


# ── Auto-detect "remember" signals in user messages ──────────────────────

_REMEMBER_PATTERNS = [
    (r"my name is (\w+)", "user_name"),
    (r"i am (\w+)", "user_identity"),
    (r"i prefer (.+)", "preference"),
    (r"remember (?:that )?(.+)", "user_note"),
    (r"i live in (.+)", "user_location"),
    (r"i work (?:at|for) (.+)", "user_workplace"),
    (r"my (?:phone|number) is (.+)", "user_phone"),
    (r"my email is (.+)", "user_email"),
    (r"call me (\w+)", "user_name"),
]


def extract_and_store(message):
    # type: (str) -> Optional[str]
    """Check if message contains a 'remember' signal. Store if found.
    Returns what was remembered, or None."""
    text = message.strip().lower()

    for pattern, key_prefix in _REMEMBER_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1).strip().rstrip(".")
            if not value:
                continue
            # Use the captured value as both key and value for searchability
            if key_prefix == "user_note":
                # For generic "remember X", use the note itself as key
                key = "note:%s" % value[:30]
            else:
                key = key_prefix
            remember(key, value, source="chat_auto")
            return "%s = %s" % (key, value)

    return None


# Init table on import
try:
    init_memory_table()
except Exception:
    pass
