"""
Personal Assistant — Phase 10 of Nexus Master Prompt

Manages Kai's daily life via Telegram + scheduled background tasks:
  1. Morning Briefing (7:30 AM PT daily)
  2. Evening Summary (9:00 PM PT daily)
  3. Daily Reminders (fixed schedule, customizable)
  4. To-Do List (add, complete, list, auto-carry-over at midnight)
  5. Custom Reminders (/remind)
  6. Health Tracking (/gym, supplements, meals, sleep, teeth)

Persistence: SQLite at ~/.nexus/memory.db
Timezone: America/Los_Angeles (all schedule checks)
Integration: importable by server.py and telegram/bot.py
"""
from __future__ import annotations

import asyncio
import sqlite3
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DB_PATH = Path.home() / ".nexus" / "memory.db"
LA_TZ = ZoneInfo("America/Los_Angeles")

# ── Default daily reminders to seed on first run ─────────────────────────────
DEFAULT_REMINDERS = [
    ("08:00", "\U0001f373 Breakfast time! Fuel up for the day.", "both"),
    ("08:30", "\U0001f48a Morning supplements", "telegram"),
    ("12:00", "\U0001f37d\ufe0f Lunch reminder \u2014 take a break and eat!", "both"),
    ("15:00", "\u2600\ufe0f Afternoon check-in \u2014 how's the day going? Any tasks to add?", "telegram"),
    ("17:00", "\U0001f3c1 End of work day \u2014 wrap up business tasks", "telegram"),
    ("18:00", "\U0001f3cb\ufe0f Gym/workout time (if scheduled)", "telegram"),
    ("21:00", "\U0001f319 Evening routine \u2014 brush teeth, supplements, wind down", "both"),
    ("22:00", "\U0001f4dd Tomorrow prep \u2014 anything to add to tomorrow's to-do?", "telegram"),
]

# ── In-memory state: track what was sent today (reset at midnight PT) ────────
_sent_today: set[str] = set()
_last_reset_date: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE SETUP
# ═══════════════════════════════════════════════════════════════════════════════

def _conn() -> sqlite3.Connection:
    """Get a database connection with row_factory and WAL mode."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _now_pt() -> datetime:
    """Current time in Pacific timezone."""
    return datetime.now(LA_TZ)


def _now_pt_str() -> str:
    """Current time as ISO string in Pacific timezone."""
    return _now_pt().strftime("%Y-%m-%d %H:%M:%S")


def _today_pt() -> str:
    """Today's date string in Pacific timezone."""
    return _now_pt().strftime("%Y-%m-%d")


def init_personal_assistant_db():
    """Create tables and seed default daily reminders if empty."""
    conn = _conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS todos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        priority TEXT DEFAULT 'normal',
        created_at TEXT DEFAULT (datetime('now')),
        completed_at TEXT,
        due_date TEXT,
        carry_count INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message TEXT NOT NULL,
        remind_at TEXT NOT NULL,
        repeat_type TEXT DEFAULT 'once',
        status TEXT DEFAULT 'pending',
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS health_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        log_type TEXT NOT NULL,
        details TEXT DEFAULT '',
        logged_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS daily_reminders_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        time_pt TEXT NOT NULL,
        message TEXT NOT NULL,
        enabled INTEGER DEFAULT 1,
        channel TEXT DEFAULT 'telegram'
    );

    CREATE INDEX IF NOT EXISTS idx_todos_status ON todos(status);
    CREATE INDEX IF NOT EXISTS idx_todos_due ON todos(due_date);
    CREATE INDEX IF NOT EXISTS idx_reminders_status ON reminders(status);
    CREATE INDEX IF NOT EXISTS idx_reminders_at ON reminders(remind_at);
    CREATE INDEX IF NOT EXISTS idx_health_type ON health_log(log_type);
    CREATE INDEX IF NOT EXISTS idx_health_logged ON health_log(logged_at);
    """)
    conn.commit()

    # Seed default daily reminders if the table is empty
    count = conn.execute("SELECT COUNT(*) FROM daily_reminders_config").fetchone()[0]
    if count == 0:
        for time_pt, message, channel in DEFAULT_REMINDERS:
            conn.execute(
                "INSERT INTO daily_reminders_config (time_pt, message, channel) VALUES (?, ?, ?)",
                (time_pt, message, channel),
            )
        conn.commit()
        print("[PersonalAssistant] Seeded default daily reminders")

    conn.close()
    print("[PersonalAssistant] Database tables ready")


# ═══════════════════════════════════════════════════════════════════════════════
# TO-DO SYSTEM
# ═══════════════════════════════════════════════════════════════════════════════

def add_todo(task: str, priority: str = "normal", due_date: str = None) -> dict:
    """Add a new to-do item. Returns the created todo dict."""
    if priority not in ("low", "normal", "high", "urgent"):
        priority = "normal"
    conn = _conn()
    cur = conn.execute(
        "INSERT INTO todos (task, priority, due_date, created_at) VALUES (?, ?, ?, ?)",
        (task, priority, due_date, _now_pt_str()),
    )
    todo_id = cur.lastrowid
    conn.commit()
    row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
    conn.close()
    return dict(row) if row else {"id": todo_id, "task": task, "status": "pending"}


def complete_todo(todo_id: int) -> dict:
    """Mark a to-do as completed. Returns the updated todo dict."""
    conn = _conn()
    conn.execute(
        "UPDATE todos SET status = 'done', completed_at = ? WHERE id = ?",
        (_now_pt_str(), todo_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
    conn.close()
    if row:
        return dict(row)
    return {"id": todo_id, "status": "done", "error": "not_found"}


def list_todos(include_done: bool = False) -> list[dict]:
    """List todos. By default only pending/carried items."""
    conn = _conn()
    if include_done:
        rows = conn.execute(
            "SELECT * FROM todos ORDER BY "
            "CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 "
            "WHEN 'normal' THEN 2 WHEN 'low' THEN 3 END, id"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM todos WHERE status IN ('pending', 'carried') ORDER BY "
            "CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 "
            "WHEN 'normal' THEN 2 WHEN 'low' THEN 3 END, id"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def carry_over_incomplete():
    """Move yesterday's incomplete todos to today, increment carry_count."""
    conn = _conn()
    conn.execute(
        "UPDATE todos SET status = 'carried', carry_count = carry_count + 1 "
        "WHERE status IN ('pending', 'carried')"
    )
    conn.commit()
    carried = conn.execute(
        "SELECT COUNT(*) FROM todos WHERE status = 'carried'"
    ).fetchone()[0]
    conn.close()
    return carried


def get_stale_todos() -> list[dict]:
    """Return todos that have been pending for 3+ days."""
    conn = _conn()
    cutoff = (_now_pt() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        "SELECT * FROM todos WHERE status IN ('pending', 'carried') AND created_at <= ? "
        "ORDER BY created_at ASC",
        (cutoff,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_todo(todo_id: int) -> bool:
    """Delete a todo by ID."""
    conn = _conn()
    conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
    conn.commit()
    conn.close()
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# REMINDER SYSTEM
# ═══════════════════════════════════════════════════════════════════════════════

def add_reminder(message: str, remind_at: str, repeat_type: str = "once") -> dict:
    """
    Add a custom reminder.
    remind_at: ISO datetime string in PT, e.g. "2026-03-02 14:00:00"
    repeat_type: "once", "daily", or "weekly"
    """
    if repeat_type not in ("once", "daily", "weekly"):
        repeat_type = "once"
    conn = _conn()
    cur = conn.execute(
        "INSERT INTO reminders (message, remind_at, repeat_type, created_at) VALUES (?, ?, ?, ?)",
        (message, remind_at, repeat_type, _now_pt_str()),
    )
    reminder_id = cur.lastrowid
    conn.commit()
    row = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
    conn.close()
    return dict(row) if row else {"id": reminder_id, "message": message}


def get_due_reminders() -> list[dict]:
    """Get reminders that are due now (within a 1-minute window)."""
    now = _now_pt()
    window_start = (now - timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S")
    window_end = (now + timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S")
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM reminders WHERE status = 'pending' AND remind_at BETWEEN ? AND ?",
        (window_start, window_end),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_reminder_sent(reminder_id: int):
    """Mark a reminder as sent, or reschedule if repeating."""
    conn = _conn()
    row = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
    if not row:
        conn.close()
        return

    reminder = dict(row)
    if reminder["repeat_type"] == "once":
        conn.execute("UPDATE reminders SET status = 'sent' WHERE id = ?", (reminder_id,))
    elif reminder["repeat_type"] == "daily":
        # Reschedule to same time tomorrow
        try:
            old_dt = datetime.strptime(reminder["remind_at"], "%Y-%m-%d %H:%M:%S")
            new_dt = old_dt + timedelta(days=1)
            conn.execute(
                "UPDATE reminders SET remind_at = ? WHERE id = ?",
                (new_dt.strftime("%Y-%m-%d %H:%M:%S"), reminder_id),
            )
        except ValueError:
            conn.execute("UPDATE reminders SET status = 'sent' WHERE id = ?", (reminder_id,))
    elif reminder["repeat_type"] == "weekly":
        try:
            old_dt = datetime.strptime(reminder["remind_at"], "%Y-%m-%d %H:%M:%S")
            new_dt = old_dt + timedelta(weeks=1)
            conn.execute(
                "UPDATE reminders SET remind_at = ? WHERE id = ?",
                (new_dt.strftime("%Y-%m-%d %H:%M:%S"), reminder_id),
            )
        except ValueError:
            conn.execute("UPDATE reminders SET status = 'sent' WHERE id = ?", (reminder_id,))
    conn.commit()
    conn.close()


def cancel_reminder(reminder_id: int) -> dict:
    """Cancel a pending reminder."""
    conn = _conn()
    conn.execute("UPDATE reminders SET status = 'cancelled' WHERE id = ?", (reminder_id,))
    conn.commit()
    row = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
    conn.close()
    return dict(row) if row else {"id": reminder_id, "status": "cancelled"}


def list_reminders(include_sent: bool = False) -> list[dict]:
    """List active reminders."""
    conn = _conn()
    if include_sent:
        rows = conn.execute("SELECT * FROM reminders ORDER BY remind_at").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE status = 'pending' ORDER BY remind_at"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_todays_reminders() -> list[dict]:
    """Get all custom reminders due today."""
    today = _today_pt()
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM reminders WHERE status = 'pending' AND remind_at LIKE ? ORDER BY remind_at",
        (f"{today}%",),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════════
# HEALTH TRACKING
# ═══════════════════════════════════════════════════════════════════════════════

def log_health(log_type: str, details: str = "") -> dict:
    """
    Log a health event.
    log_type: gym, supplement, meal, sleep, teeth
    """
    valid_types = ("gym", "supplement", "meal", "sleep", "teeth")
    if log_type not in valid_types:
        return {"error": f"Invalid type. Use: {', '.join(valid_types)}"}
    conn = _conn()
    cur = conn.execute(
        "INSERT INTO health_log (log_type, details, logged_at) VALUES (?, ?, ?)",
        (log_type, details, _now_pt_str()),
    )
    log_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {"id": log_id, "log_type": log_type, "details": details, "logged_at": _now_pt_str()}


def get_health_summary(days: int = 7) -> dict:
    """
    Summarize health logs for the last N days.
    Returns counts by type and recent entries.
    """
    cutoff = (_now_pt() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = _conn()

    # Counts by type
    rows = conn.execute(
        "SELECT log_type, COUNT(*) as cnt FROM health_log "
        "WHERE logged_at >= ? GROUP BY log_type",
        (cutoff,),
    ).fetchall()
    counts = {r["log_type"]: r["cnt"] for r in rows}

    # Gym sessions (distinct days)
    gym_days = conn.execute(
        "SELECT COUNT(DISTINCT date(logged_at)) as cnt FROM health_log "
        "WHERE log_type = 'gym' AND logged_at >= ?",
        (cutoff,),
    ).fetchone()["cnt"]

    # Last gym session
    last_gym = conn.execute(
        "SELECT logged_at, details FROM health_log "
        "WHERE log_type = 'gym' ORDER BY logged_at DESC LIMIT 1"
    ).fetchone()

    # Today's logs
    today = _today_pt()
    today_rows = conn.execute(
        "SELECT log_type, details, logged_at FROM health_log "
        "WHERE logged_at LIKE ? ORDER BY logged_at",
        (f"{today}%",),
    ).fetchall()

    conn.close()

    return {
        "period_days": days,
        "counts": counts,
        "gym_days": gym_days,
        "last_gym": dict(last_gym) if last_gym else None,
        "today": [dict(r) for r in today_rows],
    }


def get_today_health() -> list[dict]:
    """Get all health logs for today."""
    today = _today_pt()
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM health_log WHERE logged_at LIKE ? ORDER BY logged_at",
        (f"{today}%",),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════════
# DAILY REMINDER CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

def get_daily_reminders() -> list[dict]:
    """Get all configured daily reminders."""
    conn = _conn()
    rows = conn.execute("SELECT * FROM daily_reminders_config ORDER BY time_pt").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def toggle_daily_reminder(reminder_id: int) -> dict:
    """Toggle a daily reminder on/off."""
    conn = _conn()
    row = conn.execute("SELECT * FROM daily_reminders_config WHERE id = ?", (reminder_id,)).fetchone()
    if not row:
        conn.close()
        return {"error": "Reminder not found"}
    new_enabled = 0 if row["enabled"] else 1
    conn.execute(
        "UPDATE daily_reminders_config SET enabled = ? WHERE id = ?",
        (new_enabled, reminder_id),
    )
    conn.commit()
    updated = conn.execute("SELECT * FROM daily_reminders_config WHERE id = ?", (reminder_id,)).fetchone()
    conn.close()
    return dict(updated)


def add_daily_reminder(time_pt: str, message: str, channel: str = "telegram") -> dict:
    """Add a new daily reminder."""
    conn = _conn()
    cur = conn.execute(
        "INSERT INTO daily_reminders_config (time_pt, message, channel) VALUES (?, ?, ?)",
        (time_pt, message, channel),
    )
    rid = cur.lastrowid
    conn.commit()
    row = conn.execute("SELECT * FROM daily_reminders_config WHERE id = ?", (rid,)).fetchone()
    conn.close()
    return dict(row) if row else {"id": rid}


def remove_daily_reminder(reminder_id: int) -> bool:
    """Remove a daily reminder."""
    conn = _conn()
    conn.execute("DELETE FROM daily_reminders_config WHERE id = ?", (reminder_id,))
    conn.commit()
    conn.close()
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# BUSINESS DATA HELPERS (fail-safe imports)
# ═══════════════════════════════════════════════════════════════════════════════

def _get_lead_stats() -> dict:
    """Pull lead pipeline stats. Returns empty dict if module unavailable."""
    try:
        conn = _conn()
        total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        by_status = {}
        for row in conn.execute("SELECT status, COUNT(*) as cnt FROM leads GROUP BY status").fetchall():
            by_status[row["status"]] = row["cnt"]

        # Messages sent today
        msgs_today = conn.execute(
            "SELECT COUNT(*) FROM lead_messages WHERE ts >= date('now') AND direction = 'outbound'"
        ).fetchone()[0]

        # Replies received today
        replies_today = conn.execute(
            "SELECT COUNT(*) FROM lead_messages WHERE ts >= date('now') AND direction = 'inbound'"
        ).fetchone()[0]

        # New leads today
        new_today = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE discovered_at >= date('now')"
        ).fetchone()[0]

        conn.close()
        return {
            "total": total,
            "by_status": by_status,
            "new_today": new_today,
            "msgs_sent_today": msgs_today,
            "replies_today": replies_today,
        }
    except Exception:
        return {}


def _get_approval_stats() -> dict:
    """Pull approval queue stats. Returns empty dict if unavailable."""
    try:
        from core.approval_queue import get_approval_stats
        return get_approval_stats()
    except Exception:
        return {}


def _get_next_booked_event() -> str:
    """Placeholder for calendar integration. Returns formatted string."""
    # Future: integrate with Google Calendar or booking system
    return "No events synced yet"


# ═══════════════════════════════════════════════════════════════════════════════
# BRIEFINGS
# ═══════════════════════════════════════════════════════════════════════════════

def generate_morning_briefing() -> str:
    """
    Generate the 7:30 AM morning briefing text.
    Includes: priorities, to-do list, health reminders, business snapshot,
    posting preview, and reminders for today.
    """
    now = _now_pt()
    day_str = now.strftime("%A, %B %-d")

    # ── To-dos ──
    todos = list_todos()
    stale = get_stale_todos()
    stale_ids = {t["id"] for t in stale}

    # Identify priorities (urgent/high, or stale)
    priorities = [t for t in todos if t.get("priority") in ("urgent", "high")]
    if not priorities:
        priorities = todos[:3]  # Top 3 if no explicit priorities

    # ── Health ──
    health_today = get_today_health()
    health_summary = get_health_summary(7)

    # ── Business data ──
    lead_stats = _get_lead_stats()
    approval_stats = _get_approval_stats()

    # ── Custom reminders for today ──
    today_reminders = get_todays_reminders()

    # ── Build message ──
    lines = []
    lines.append(f"\u2600\ufe0f GOOD MORNING KAI \u2014 {day_str}")
    lines.append("\u2501" * 34)
    lines.append("")

    # Priorities
    lines.append("\U0001f9e0 TODAY'S PRIORITY:")
    if priorities:
        for t in priorities[:3]:
            marker = "\u203c\ufe0f" if t.get("priority") == "urgent" else "\u2022"
            lines.append(f"{marker} {t['task']}")
    else:
        lines.append("\u2022 No priorities set \u2014 add some!")
    lines.append("")

    # To-do list
    lines.append("\U0001f4cb TO-DO LIST:")
    if todos:
        for t in todos:
            if t["id"] in stale_ids:
                marker = "[!]"
                suffix = f" (stale \u2014 {t.get('carry_count', 0)} days carried)"
            elif t.get("status") == "carried":
                marker = "[\u27a1\ufe0f]"
                suffix = ""
            else:
                marker = "[ ]"
                suffix = ""
            priority_tag = ""
            if t.get("priority") in ("urgent", "high"):
                priority_tag = f" [{t['priority'].upper()}]"
            lines.append(f"\u2022 {marker} #{t['id']}: {t['task']}{priority_tag}{suffix}")
    else:
        lines.append("\u2022 No tasks yet! Reply 'add [task]' to add one.")
    lines.append("(Reply with task # to mark done, or 'add [task]' to add)")
    lines.append("")

    # Health & Wellness
    lines.append("\U0001f3cb\ufe0f HEALTH & WELLNESS:")
    lines.append("\u2022 Breakfast reminder: 8:00 AM")
    lines.append("\u2022 Supplements: Morning + Evening")
    gym_days = health_summary.get("gym_days", 0)
    last_gym = health_summary.get("last_gym")
    if last_gym:
        last_gym_str = last_gym.get("logged_at", "?")[:10]
        lines.append(f"\u2022 Gym: {gym_days} sessions this week (last: {last_gym_str})")
    else:
        lines.append("\u2022 Gym: No sessions logged this week \u2014 time to go!")
    # Teeth tracking
    teeth_today = [h for h in health_today if h.get("log_type") == "teeth"]
    if teeth_today:
        lines.append("\u2022 Brush teeth AM \u2705 (remind at 9 PM)")
    else:
        lines.append("\u2022 Brush teeth AM \u2014 don't forget!")
    lines.append("")

    # Business Snapshot
    lines.append("\U0001f4ca BUSINESS SNAPSHOT:")
    if lead_stats:
        by_status = lead_stats.get("by_status", {})
        active = lead_stats.get("total", 0)
        pending_approvals = approval_stats.get("pending", 0)
        new_count = by_status.get("new", 0)
        qualifying = by_status.get("awaiting_approval", 0) + by_status.get("waiting_reply", 0)
        quote_sent = by_status.get("initial_contact", 0) + by_status.get("sms_sent", 0)
        lines.append(f"\u2022 Active leads: {active} | Pending approvals: {pending_approvals}")
        lines.append(f"\u2022 Pipeline: {new_count} new \u2192 {qualifying} qualifying \u2192 {quote_sent} quote sent")
        lines.append(f"\u2022 Next booked event: {_get_next_booked_event()}")
    else:
        lines.append("\u2022 Lead pipeline not initialized yet")
    lines.append("")

    # Posting preview
    lines.append("\U0001f4e2 POSTING PACKAGE:")
    try:
        from core.daily_posting import get_todays_fb_post
        fb_post = get_todays_fb_post()
        if fb_post:
            lines.append(f"\u2022 FB Post ready: {fb_post.get('title', 'Untitled')[:60]}")
        else:
            lines.append("\u2022 Daily posting briefing at 8 AM")
    except Exception:
        lines.append("\u2022 Daily posting module not loaded")
    lines.append("")

    # Reminders for today
    lines.append("\U0001f514 REMINDERS TODAY:")
    if today_reminders:
        for r in today_reminders:
            time_str = r.get("remind_at", "")
            try:
                t = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
                time_str = t.strftime("%-I:%M %p")
            except ValueError:
                pass
            lines.append(f"\u2022 {time_str} \u2014 {r['message']}")
    else:
        lines.append("\u2022 No custom reminders for today")
    lines.append("\u2501" * 34)

    return "\n".join(lines)


def generate_evening_summary() -> str:
    """
    Generate the 9:00 PM evening summary.
    Includes: todos completed vs total, today's numbers, what Nexus learned,
    tomorrow preview, teeth reminder.
    """
    now = _now_pt()

    # ── To-dos ──
    all_todos = list_todos(include_done=True)
    today_str = _today_pt()

    # Todos completed today
    done_today = [
        t for t in all_todos
        if t.get("status") == "done"
        and t.get("completed_at", "").startswith(today_str)
    ]
    pending = [t for t in all_todos if t.get("status") in ("pending", "carried")]
    total_active = len(done_today) + len(pending)

    # ── Business data ──
    lead_stats = _get_lead_stats()
    approval_stats = _get_approval_stats()

    # ── Build message ──
    lines = []
    lines.append("\U0001f319 END OF DAY SUMMARY")
    lines.append("\u2501" * 34)

    # To-do report
    lines.append(f"\U0001f4cb TO-DO COMPLETED: {len(done_today)}/{total_active}")
    if done_today:
        for t in done_today:
            lines.append(f"\u2022 \u2705 {t['task']}")
    if pending:
        for t in pending:
            lines.append(f"\u2022 \u274c {t['task']} \u2014 carry to tomorrow?")
    lines.append("")

    # Today's numbers
    lines.append("\U0001f4ca TODAY'S NUMBERS:")
    if lead_stats:
        lines.append(f"\u2022 New leads: {lead_stats.get('new_today', 0)}")
        lines.append(f"\u2022 Messages sent (approved): {approval_stats.get('approved_today', 0)}")
        lines.append(f"\u2022 Replies received: {lead_stats.get('replies_today', 0)}")
    else:
        lines.append("\u2022 No business data available")
    lines.append("")

    # Nexus learned
    lines.append("\U0001f9e0 NEXUS LEARNED TODAY:")
    lines.append("\u2022 [Knowledge engine integration coming soon]")
    lines.append("")

    # Tomorrow preview
    lines.append("\U0001f514 TOMORROW PREVIEW:")
    stale = get_stale_todos()
    if stale:
        lines.append(f"\u2022 {len(stale)} stale task(s) need attention!")
        for t in stale[:3]:
            lines.append(f"  \u2192 #{t['id']}: {t['task']}")
    if pending:
        lines.append(f"\u2022 {len(pending)} task(s) carry over to tomorrow")
    # Tomorrow's custom reminders
    tomorrow = (_now_pt() + timedelta(days=1)).strftime("%Y-%m-%d")
    conn = _conn()
    tomorrow_reminders = conn.execute(
        "SELECT * FROM reminders WHERE status = 'pending' AND remind_at LIKE ?",
        (f"{tomorrow}%",),
    ).fetchall()
    conn.close()
    if tomorrow_reminders:
        for r in tomorrow_reminders:
            lines.append(f"\u2022 Reminder: {r['message']}")
    if not stale and not pending and not tomorrow_reminders:
        lines.append("\u2022 All clear! Fresh start tomorrow.")
    lines.append("")

    # Teeth reminder
    lines.append("\U0001fab4 Don't forget to brush your teeth!")
    lines.append("\u2501" * 34)

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# BACKGROUND SCHEDULER
# ═══════════════════════════════════════════════════════════════════════════════

async def run_personal_assistant(send_fn):
    """
    Main background loop. Runs every 60 seconds.
    send_fn(text) is an async function to send Telegram messages to Kai.

    Checks each minute:
    1. Is it time for morning briefing? (7:30 AM PT, sent once per day)
    2. Is it time for evening summary? (9:00 PM PT, sent once per day)
    3. Are any daily reminders due? (check each minute)
    4. Are any custom reminders due?
    5. Any stale todos to warn about? (3 PM warning)
    6. Carry over incomplete todos at midnight
    """
    global _sent_today, _last_reset_date

    # Initialize database tables on startup
    init_personal_assistant_db()

    print("[PersonalAssistant] Background scheduler started")

    while True:
        try:
            now = _now_pt()
            current_time = now.strftime("%H:%M")
            current_date = now.strftime("%Y-%m-%d")

            # ── Midnight reset ──
            if current_date != _last_reset_date:
                _sent_today.clear()
                _last_reset_date = current_date

                # Carry over at midnight
                if current_time == "00:00":
                    carried = carry_over_incomplete()
                    if carried > 0:
                        await _safe_send(send_fn,
                            f"\U0001f504 Midnight carry-over: {carried} task(s) moved to today."
                        )

            # ── 1. Morning Briefing (7:30 AM PT) ──
            if current_time == "07:30" and "morning_briefing" not in _sent_today:
                _sent_today.add("morning_briefing")
                try:
                    briefing = generate_morning_briefing()
                    await _safe_send(send_fn, briefing)
                except Exception as e:
                    print(f"[PersonalAssistant] Morning briefing error: {e}")
                    traceback.print_exc()

            # ── 2. Evening Summary (9:00 PM PT) ──
            if current_time == "21:00" and "evening_summary" not in _sent_today:
                _sent_today.add("evening_summary")
                try:
                    summary = generate_evening_summary()
                    await _safe_send(send_fn, summary)
                except Exception as e:
                    print(f"[PersonalAssistant] Evening summary error: {e}")
                    traceback.print_exc()

            # ── 3. Daily Reminders ──
            daily = get_daily_reminders()
            for dr in daily:
                if not dr.get("enabled"):
                    continue
                dr_time = dr.get("time_pt", "")
                dr_key = f"daily_{dr['id']}_{dr_time}"
                if current_time == dr_time and dr_key not in _sent_today:
                    _sent_today.add(dr_key)
                    channel = dr.get("channel", "telegram")
                    if channel in ("telegram", "both"):
                        await _safe_send(send_fn, dr["message"])
                    # "sms" and "both" channels: future SMS integration point
                    # For now, "both" sends via Telegram (SMS can be added later)

            # ── 4. Custom Reminders ──
            due_reminders = get_due_reminders()
            for r in due_reminders:
                r_key = f"reminder_{r['id']}"
                if r_key not in _sent_today:
                    _sent_today.add(r_key)
                    repeat_label = ""
                    if r.get("repeat_type") in ("daily", "weekly"):
                        repeat_label = f" [{r['repeat_type']}]"
                    await _safe_send(send_fn, f"\U0001f514 Reminder{repeat_label}: {r['message']}")
                    mark_reminder_sent(r["id"])

            # ── 5. Stale Todo Warning (3:00 PM PT) ──
            if current_time == "15:00" and "stale_warning" not in _sent_today:
                stale = get_stale_todos()
                if stale:
                    _sent_today.add("stale_warning")
                    msg_lines = [f"\u26a0\ufe0f STALE TASKS ({len(stale)} pending 3+ days):"]
                    for t in stale:
                        days_old = (_now_pt() - datetime.strptime(
                            t["created_at"], "%Y-%m-%d %H:%M:%S"
                        ).replace(tzinfo=LA_TZ)).days
                        msg_lines.append(f"\u2022 #{t['id']}: {t['task']} ({days_old}d old)")
                    msg_lines.append("\nReply with task # to complete, or 'delete #' to remove.")
                    await _safe_send(send_fn, "\n".join(msg_lines))

        except Exception as e:
            print(f"[PersonalAssistant] Scheduler error: {e}")
            traceback.print_exc()

        await asyncio.sleep(60)


async def _safe_send(send_fn, text: str):
    """Safely call the send function, catching any errors."""
    try:
        await send_fn(text)
    except Exception as e:
        print(f"[PersonalAssistant] Send error: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND PARSER (for Telegram integration)
# ═══════════════════════════════════════════════════════════════════════════════

async def handle_pa_command(text: str) -> str | None:
    """
    Parse personal assistant commands from Telegram messages.
    Returns response text, or None if the message is not a PA command.

    Supported commands:
      /todo or /todos           - List current todos
      /add <task>               - Add a todo
      /done <id>                - Complete a todo
      /delete <id>              - Delete a todo
      /remind <time> <message>  - Set a reminder
      /reminders                - List active reminders
      /cancelreminder <id>      - Cancel a reminder
      /gym [details]            - Log gym session
      /supplement [details]     - Log supplement
      /meal [details]           - Log meal
      /teeth                    - Log teeth brushing
      /health                   - Health summary
      /briefing                 - Get morning briefing now
      /summary                  - Get evening summary now
      /dailyreminders           - List daily reminder config
    """
    if not text:
        return None

    text = text.strip()
    lower = text.lower()

    # ── To-Do Commands ──
    if lower in ("/todo", "/todos", "/list"):
        todos = list_todos()
        if not todos:
            return "\U0001f4cb No pending tasks! Use /add <task> to add one."
        lines = ["\U0001f4cb YOUR TO-DO LIST:"]
        for t in todos:
            status_icon = "\u27a1\ufe0f" if t.get("status") == "carried" else "\u2b1c"
            stale_flag = ""
            if t.get("carry_count", 0) >= 3:
                stale_flag = " \u26a0\ufe0f STALE"
            priority_tag = ""
            if t.get("priority") in ("urgent", "high"):
                priority_tag = f" [{t['priority'].upper()}]"
            lines.append(f"{status_icon} #{t['id']}: {t['task']}{priority_tag}{stale_flag}")
        lines.append("\n/done <#> to complete | /add <task> to add")
        return "\n".join(lines)

    if lower.startswith("/add "):
        task_text = text[5:].strip()
        if not task_text:
            return "Usage: /add <task description>"
        # Check for priority flag
        priority = "normal"
        for p in ("urgent", "high", "low"):
            if task_text.lower().startswith(f"!{p} "):
                priority = p
                task_text = task_text[len(p) + 2:]
                break
        todo = add_todo(task_text, priority=priority)
        return f"\u2705 Added todo #{todo['id']}: {task_text}" + (
            f" [{priority.upper()}]" if priority != "normal" else ""
        )

    if lower.startswith("/done "):
        try:
            todo_id = int(text[6:].strip().lstrip("#"))
            result = complete_todo(todo_id)
            if result.get("error") == "not_found":
                return f"\u274c Todo #{todo_id} not found."
            return f"\u2705 Completed: {result.get('task', f'#{todo_id}')}"
        except ValueError:
            return "Usage: /done <task number>"

    if lower.startswith("/delete "):
        try:
            todo_id = int(text[8:].strip().lstrip("#"))
            delete_todo(todo_id)
            return f"\U0001f5d1\ufe0f Deleted todo #{todo_id}"
        except ValueError:
            return "Usage: /delete <task number>"

    # ── Reminder Commands ──
    if lower.startswith("/remind "):
        parts = text[8:].strip()
        # Parse: /remind <datetime> <message>
        # Try common formats: "tomorrow 3pm Take out trash"
        #                     "2026-03-02 14:00 Call dentist"
        return _parse_remind_command(parts)

    if lower in ("/reminders",):
        reminders = list_reminders()
        if not reminders:
            return "\U0001f514 No active reminders. Use /remind to add one."
        lines = ["\U0001f514 ACTIVE REMINDERS:"]
        for r in reminders:
            repeat_label = f" [{r['repeat_type']}]" if r.get("repeat_type") != "once" else ""
            lines.append(f"\u2022 #{r['id']}: {r['remind_at']} \u2014 {r['message']}{repeat_label}")
        lines.append("\n/cancelreminder <#> to cancel")
        return "\n".join(lines)

    if lower.startswith("/cancelreminder "):
        try:
            rid = int(text[16:].strip().lstrip("#"))
            result = cancel_reminder(rid)
            return f"\u274c Cancelled reminder #{rid}"
        except ValueError:
            return "Usage: /cancelreminder <id>"

    # ── Health Commands ──
    if lower.startswith("/gym"):
        details = text[4:].strip() if len(text) > 4 else ""
        log_health("gym", details)
        return f"\U0001f3cb\ufe0f Gym session logged!" + (f" ({details})" if details else "")

    if lower.startswith("/supplement"):
        details = text[11:].strip() if len(text) > 11 else ""
        log_health("supplement", details)
        return f"\U0001f48a Supplement logged!" + (f" ({details})" if details else "")

    if lower.startswith("/meal"):
        details = text[5:].strip() if len(text) > 5 else ""
        log_health("meal", details)
        return f"\U0001f37d\ufe0f Meal logged!" + (f" ({details})" if details else "")

    if lower in ("/teeth", "/brush"):
        log_health("teeth", "brushed")
        return "\U0001fab7 Teeth brushing logged! Great job!"

    if lower.startswith("/sleep"):
        details = text[6:].strip() if len(text) > 6 else ""
        log_health("sleep", details)
        return f"\U0001f634 Sleep logged!" + (f" ({details})" if details else "")

    if lower in ("/health", "/healthsummary"):
        summary = get_health_summary(7)
        lines = ["\U0001f3cb\ufe0f HEALTH SUMMARY (7 days):"]
        counts = summary.get("counts", {})
        lines.append(f"\u2022 Gym sessions: {counts.get('gym', 0)} ({summary.get('gym_days', 0)} unique days)")
        lines.append(f"\u2022 Supplements logged: {counts.get('supplement', 0)}")
        lines.append(f"\u2022 Meals logged: {counts.get('meal', 0)}")
        lines.append(f"\u2022 Teeth: {counts.get('teeth', 0)} times")
        lines.append(f"\u2022 Sleep: {counts.get('sleep', 0)} entries")
        last_gym = summary.get("last_gym")
        if last_gym:
            lines.append(f"\nLast gym: {last_gym.get('logged_at', '?')[:10]}"
                         + (f" \u2014 {last_gym['details']}" if last_gym.get("details") else ""))
        today_logs = summary.get("today", [])
        if today_logs:
            lines.append("\nToday:")
            for h in today_logs:
                lines.append(f"  \u2022 {h['log_type']}: {h.get('details', '') or 'logged'}")
        return "\n".join(lines)

    # ── Briefing on demand ──
    if lower in ("/briefing", "/morning"):
        return generate_morning_briefing()

    if lower in ("/summary", "/evening"):
        return generate_evening_summary()

    # ── Daily Reminder Config ──
    if lower in ("/dailyreminders",):
        reminders = get_daily_reminders()
        lines = ["\u23f0 DAILY REMINDERS:"]
        for dr in reminders:
            status = "\u2705" if dr.get("enabled") else "\u274c"
            lines.append(f"{status} #{dr['id']} {dr['time_pt']} \u2014 {dr['message']} [{dr['channel']}]")
        lines.append("\nUse /togglereminder <#> to enable/disable")
        return "\n".join(lines)

    if lower.startswith("/togglereminder "):
        try:
            rid = int(text[16:].strip().lstrip("#"))
            result = toggle_daily_reminder(rid)
            if result.get("error"):
                return f"\u274c {result['error']}"
            status = "enabled" if result.get("enabled") else "disabled"
            return f"\u2705 Daily reminder #{rid} is now {status}"
        except ValueError:
            return "Usage: /togglereminder <id>"

    # Not a PA command
    return None


def _parse_remind_command(text: str) -> str:
    """
    Parse a remind command string and create the reminder.
    Formats supported:
      /remind tomorrow 3pm Take out trash
      /remind 2026-03-02 14:00 Call dentist
      /remind 30m Check oven
      /remind 2h Review document
      /remind daily 08:00 Morning standup
    """
    if not text:
        return ("Usage:\n"
                "/remind tomorrow 3pm <message>\n"
                "/remind 2026-03-02 14:00 <message>\n"
                "/remind 30m <message>\n"
                "/remind 2h <message>\n"
                "/remind daily 08:00 <message>")

    parts = text.split(None, 2)
    now = _now_pt()

    # Check for repeat type prefix
    repeat_type = "once"
    if parts[0].lower() in ("daily", "weekly"):
        repeat_type = parts[0].lower()
        text = text[len(parts[0]):].strip()
        parts = text.split(None, 1)

    # Relative time: "30m", "2h", "1d"
    first = parts[0].lower()
    if first.endswith("m") and first[:-1].isdigit():
        minutes = int(first[:-1])
        remind_at = now + timedelta(minutes=minutes)
        message = text[len(parts[0]):].strip()
        if not message:
            return "Usage: /remind 30m <message>"
        r = add_reminder(message, remind_at.strftime("%Y-%m-%d %H:%M:%S"), repeat_type)
        return f"\U0001f514 Reminder set for {remind_at.strftime('%-I:%M %p')}: {message}"

    if first.endswith("h") and first[:-1].isdigit():
        hours = int(first[:-1])
        remind_at = now + timedelta(hours=hours)
        message = text[len(parts[0]):].strip()
        if not message:
            return "Usage: /remind 2h <message>"
        r = add_reminder(message, remind_at.strftime("%Y-%m-%d %H:%M:%S"), repeat_type)
        return f"\U0001f514 Reminder set for {remind_at.strftime('%-I:%M %p')}: {message}"

    if first.endswith("d") and first[:-1].isdigit():
        days = int(first[:-1])
        remind_at = now + timedelta(days=days)
        message = text[len(parts[0]):].strip()
        if not message:
            return "Usage: /remind 1d <message>"
        r = add_reminder(message, remind_at.strftime("%Y-%m-%d %H:%M:%S"), repeat_type)
        return f"\U0001f514 Reminder set for {remind_at.strftime('%b %-d, %-I:%M %p')}: {message}"

    # "tomorrow" keyword
    if first == "tomorrow":
        rest = text[len("tomorrow"):].strip()
        time_and_msg = rest.split(None, 1)
        if len(time_and_msg) < 2:
            return "Usage: /remind tomorrow 3pm <message>"
        time_str = time_and_msg[0]
        message = time_and_msg[1]
        hour, minute = _parse_time_str(time_str)
        if hour is None:
            return f"Could not parse time: {time_str}. Use formats like 3pm, 15:00, 2:30pm"
        tomorrow = now + timedelta(days=1)
        remind_at = tomorrow.replace(hour=hour, minute=minute, second=0, microsecond=0)
        r = add_reminder(message, remind_at.strftime("%Y-%m-%d %H:%M:%S"), repeat_type)
        return f"\U0001f514 Reminder set for tomorrow {remind_at.strftime('%-I:%M %p')}: {message}"

    # Try ISO date: "2026-03-02 14:00 message"
    if len(parts) >= 2:
        try:
            # Try "YYYY-MM-DD HH:MM message"
            date_str = parts[0]
            rest = text[len(date_str):].strip()
            rest_parts = rest.split(None, 1)
            if len(rest_parts) >= 2:
                time_str = rest_parts[0]
                message = rest_parts[1]
                remind_at = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
                remind_at = remind_at.replace(tzinfo=LA_TZ)
                r = add_reminder(message, remind_at.strftime("%Y-%m-%d %H:%M:%S"), repeat_type)
                return f"\U0001f514 Reminder set for {remind_at.strftime('%b %-d, %-I:%M %p')}: {message}"
        except ValueError:
            pass

        # Try "HH:MM message" (today)
        try:
            hour, minute = _parse_time_str(parts[0])
            if hour is not None:
                message = text[len(parts[0]):].strip()
                if not message:
                    return "Usage: /remind <time> <message>"
                remind_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                # If time already passed today, set for tomorrow
                if remind_at <= now:
                    remind_at += timedelta(days=1)
                r = add_reminder(message, remind_at.strftime("%Y-%m-%d %H:%M:%S"), repeat_type)
                day_label = "today" if remind_at.date() == now.date() else "tomorrow"
                return f"\U0001f514 Reminder set for {day_label} {remind_at.strftime('%-I:%M %p')}: {message}"
        except Exception:
            pass

    return ("Could not parse reminder. Examples:\n"
            "/remind 30m Check oven\n"
            "/remind tomorrow 3pm Call dentist\n"
            "/remind 2026-03-02 14:00 Meeting prep\n"
            "/remind daily 08:00 Morning standup")


def _parse_time_str(s: str) -> tuple[int | None, int | None]:
    """
    Parse a time string like "3pm", "15:00", "2:30pm", "08:00".
    Returns (hour, minute) or (None, None) on failure.
    """
    s = s.strip().lower()

    # Handle am/pm formats
    is_pm = s.endswith("pm")
    is_am = s.endswith("am")
    if is_pm or is_am:
        s = s[:-2]

    parts = s.split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return None, None

    if is_pm and hour < 12:
        hour += 12
    if is_am and hour == 12:
        hour = 0

    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute
    return None, None
