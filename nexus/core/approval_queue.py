"""
Approval Queue — Every outbound message must be approved by Kai via Telegram.

Flow:
1. Pipeline generates a message for a lead
2. Message is queued here with status='pending'
3. Telegram notification sent to Kai with approve/edit/skip buttons
4. Kai responds → status changes to approved/edited/skipped
5. If approved/edited → message is sent to lead
6. If skipped → nothing sent, lead stays in current state

No batch approvals. No auto-send. Ever.
"""
from __future__ import annotations
import sqlite3, json
from pathlib import Path
from datetime import datetime, timedelta

DB_PATH = Path.home() / ".nexus" / "memory.db"


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def init_approval_db():
    """Create the approval queue table."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS message_approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lead_id INTEGER NOT NULL,
        lead_name TEXT DEFAULT '',
        lead_phone TEXT DEFAULT '',
        lead_email TEXT DEFAULT '',
        lead_source TEXT DEFAULT '',
        channel TEXT DEFAULT 'sms',
        message_type TEXT DEFAULT 'initial',
        proposed_message TEXT DEFAULT '',
        proposed_subject TEXT DEFAULT '',
        actual_message_sent TEXT DEFAULT '',
        status TEXT DEFAULT 'pending',
        approval_method TEXT DEFAULT '',
        followup_step INTEGER DEFAULT 0,
        last_outbound TEXT DEFAULT '',
        last_inbound TEXT DEFAULT '',
        reminder_count INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        approved_at TEXT DEFAULT '',
        telegram_notified INTEGER DEFAULT 0,
        FOREIGN KEY (lead_id) REFERENCES leads(id)
    );

    CREATE INDEX IF NOT EXISTS idx_approvals_status ON message_approvals(status);
    CREATE INDEX IF NOT EXISTS idx_approvals_lead ON message_approvals(lead_id);
    """)
    conn.commit()
    conn.close()
    print("[ApprovalQueue] Database table ready")


# ── Queue operations ─────────────────────────────────────────────────────────

def queue_message(
    lead_id: int,
    lead_name: str,
    lead_phone: str,
    lead_email: str,
    lead_source: str,
    channel: str,
    message_type: str,
    proposed_message: str,
    proposed_subject: str = "",
    followup_step: int = 0,
    last_outbound: str = "",
    last_inbound: str = "",
) -> int:
    """Add a message to the approval queue. Returns the approval ID.
    Dedup: if a pending approval already exists for this (lead_id, channel, message_type),
    return the existing ID instead of creating a duplicate.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(str(DB_PATH))
    # Dedup guard — prevent phantom duplicates
    existing = conn.execute(
        "SELECT id FROM message_approvals "
        "WHERE lead_id = ? AND channel = ? AND message_type = ? AND status = 'pending' "
        "LIMIT 1",
        (lead_id, channel, message_type),
    ).fetchone()
    if existing:
        conn.close()
        return existing[0]
    cursor = conn.execute(
        """INSERT INTO message_approvals
        (lead_id, lead_name, lead_phone, lead_email, lead_source,
         channel, message_type, proposed_message, proposed_subject,
         followup_step, last_outbound, last_inbound, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (lead_id, lead_name, lead_phone, lead_email, lead_source,
         channel, message_type, proposed_message, proposed_subject,
         followup_step, last_outbound, last_inbound, now)
    )
    approval_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return approval_id


def clear_stale_approvals(max_age_hours=24):
    """Expire pending approvals older than max_age_hours. Run at startup."""
    conn = sqlite3.connect(str(DB_PATH))
    updated = conn.execute(
        "UPDATE message_approvals SET status = 'expired' "
        "WHERE status = 'pending' AND created_at < datetime('now', ?)",
        ("-%d hours" % max_age_hours,),
    ).rowcount
    conn.commit()
    conn.close()
    if updated:
        print("[ApprovalQueue] Expired %d stale pending approvals" % updated)


def approve_message(approval_id: int, custom_text: str = None) -> dict | None:
    """Approve a message. If custom_text provided, send that instead."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM message_approvals WHERE id = ?", (approval_id,)).fetchone()
    if not row:
        conn.close()
        return None

    actual_msg = custom_text if custom_text else row["proposed_message"]
    method = "kai_edited" if custom_text else "kai_approved"

    conn.execute(
        "UPDATE message_approvals SET status='approved', approval_method=?, "
        "actual_message_sent=?, approved_at=? WHERE id=?",
        (method, actual_msg, _now(), approval_id)
    )
    conn.commit()
    result = dict(conn.execute("SELECT * FROM message_approvals WHERE id = ?", (approval_id,)).fetchone())
    conn.close()
    return result


def skip_message(approval_id: int) -> dict | None:
    """Skip a message — don't send anything."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM message_approvals WHERE id = ?", (approval_id,)).fetchone()
    if not row:
        conn.close()
        return None
    conn.execute(
        "UPDATE message_approvals SET status='skipped', approval_method='skipped', approved_at=? WHERE id=?",
        (_now(), approval_id)
    )
    conn.commit()
    result = dict(conn.execute("SELECT * FROM message_approvals WHERE id = ?", (approval_id,)).fetchone())
    conn.close()
    return result


def stop_sequence(lead_id: int) -> int:
    """Stop the entire follow-up sequence for a lead. Returns count of cancelled approvals."""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.execute(
        "UPDATE message_approvals SET status='skipped', approval_method='sequence_stopped', approved_at=? "
        "WHERE lead_id=? AND status='pending'",
        (_now(), lead_id)
    )
    count = cursor.rowcount
    conn.commit()
    conn.close()
    return count


def get_pending(approval_id: int = None) -> dict | None:
    """Get a single pending approval by ID."""
    if approval_id is None:
        return None
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM message_approvals WHERE id = ?", (approval_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_pending() -> list[dict]:
    """Get all pending approvals, newest first."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM message_approvals WHERE status='pending' ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_latest_pending_for_lead(lead_id: int) -> dict | None:
    """Get the most recent pending approval for a lead."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM message_approvals WHERE lead_id=? AND status='pending' "
        "ORDER BY created_at DESC LIMIT 1", (lead_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def mark_notified(approval_id: int):
    """Mark that Telegram notification was sent for this approval."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "UPDATE message_approvals SET telegram_notified=1 WHERE id=?", (approval_id,)
    )
    conn.commit()
    conn.close()


def get_unnotified_pending() -> list[dict]:
    """Get pending approvals that haven't been sent to Telegram yet (for startup recovery)."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM message_approvals WHERE status='pending' AND telegram_notified=0 "
        "ORDER BY created_at ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_needs_reminder() -> list[dict]:
    """Get pending approvals older than 2 hours that haven't been reminded."""
    cutoff_2h = (datetime.utcnow() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    cutoff_24h = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM message_approvals WHERE status='pending' "
        "AND telegram_notified=1 AND created_at <= ? AND reminder_count < 2 "
        "ORDER BY created_at ASC",
        (cutoff_2h,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def increment_reminder(approval_id: int):
    """Increment the reminder count."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "UPDATE message_approvals SET reminder_count = reminder_count + 1 WHERE id=?",
        (approval_id,)
    )
    conn.commit()
    conn.close()


def get_approval_stats() -> dict:
    """Get approval queue stats for the daily briefing."""
    conn = sqlite3.connect(str(DB_PATH))
    pending = conn.execute("SELECT COUNT(*) FROM message_approvals WHERE status='pending'").fetchone()[0]
    approved_today = conn.execute(
        "SELECT COUNT(*) FROM message_approvals WHERE status='approved' AND approved_at >= date('now')"
    ).fetchone()[0]
    skipped_today = conn.execute(
        "SELECT COUNT(*) FROM message_approvals WHERE status='skipped' AND approved_at >= date('now')"
    ).fetchone()[0]
    conn.close()
    return {"pending": pending, "approved_today": approved_today, "skipped_today": skipped_today}
