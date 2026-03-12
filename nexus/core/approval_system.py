"""
Human Approval System — Centralized outbound message gatekeeper.

Every outbound message (SMS, email, Messenger) to a lead MUST go through here.
Kai approves via Telegram. No batch approvals. No auto-send. EVER.

Triple Redundancy: Telegram → Slack → SMS to Kai
If all 3 fail, the message stays pending and the reminder scheduler will retry.

Flow:
  1. Any module calls queue_outbound() instead of sending directly
  2. Triple notification fires: Telegram (primary) + Slack (secondary) + SMS (tertiary)
  3. Kai responds via Telegram: /approve, /skip, /stop, or text reply (edit)
  4. If approved → execute_send() delivers the message to the lead
  5. If 2h passes with no response → reminder sent
  6. If 24h passes → auto-expire (NEVER auto-send, just mark expired)
  7. Reminder scheduler runs every 5 minutes checking for stale approvals

Telegram Commands:
  /queue              — Show all pending approvals
  /approve <id>       — Approve message (send as-is)
  /approve <id> <msg> — Approve with custom text (replaces proposed message)
  /skip <id>          — Skip this message (don't send)
  /stop <lead_id>     — Stop entire sequence for a lead
  /test_notification  — Fire a test notification to verify all 3 channels
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"
LA_TZ = ZoneInfo("America/Los_Angeles")

# Reminder schedule: first at 2h, second at 6h, then expire at 24h
REMINDER_1_HOURS = 2
REMINDER_2_HOURS = 6
EXPIRE_HOURS = 24
SCHEDULER_INTERVAL = 300  # 5 minutes

_system = None  # Module-level singleton


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _la_now():
    return datetime.now(LA_TZ).strftime("%I:%M %p")


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE SETUP
# ═══════════════════════════════════════════════════════════════════════════════

def init_approval_system():
    """Create/upgrade approval tables. Safe to call multiple times."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")

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
        expired_at TEXT DEFAULT '',
        telegram_notified INTEGER DEFAULT 0,
        slack_notified INTEGER DEFAULT 0,
        sms_notified INTEGER DEFAULT 0,
        notification_errors TEXT DEFAULT '',
        FOREIGN KEY (lead_id) REFERENCES leads(id)
    );

    CREATE INDEX IF NOT EXISTS idx_approvals_status ON message_approvals(status);
    CREATE INDEX IF NOT EXISTS idx_approvals_lead ON message_approvals(lead_id);
    CREATE INDEX IF NOT EXISTS idx_approvals_created ON message_approvals(created_at);

    -- Notification log: tracks every notification attempt per channel
    CREATE TABLE IF NOT EXISTS notification_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        approval_id INTEGER,
        channel TEXT DEFAULT '',
        success INTEGER DEFAULT 0,
        error_message TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    );
    """)

    # Add new columns if upgrading from old schema
    _safe_add_column(conn, "message_approvals", "expired_at", "TEXT DEFAULT ''")
    _safe_add_column(conn, "message_approvals", "slack_notified", "INTEGER DEFAULT 0")
    _safe_add_column(conn, "message_approvals", "sms_notified", "INTEGER DEFAULT 0")
    _safe_add_column(conn, "message_approvals", "notification_errors", "TEXT DEFAULT ''")

    conn.commit()
    conn.close()
    print("[ApprovalSystem] Database ready — triple redundancy enabled")


def _safe_add_column(conn, table, column, definition):
    """Add a column if it doesn't exist (SQLite ALTER TABLE safe)."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except sqlite3.OperationalError:
        pass  # Column already exists


# ═══════════════════════════════════════════════════════════════════════════════
# QUEUE OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def queue_outbound(
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
    """Queue an outbound message for Kai's approval. Returns approval ID.

    This is the ONLY way a message should enter the send pipeline.
    Called by: lead_pipeline, follow_up_engine, reengagement, fb_messenger.
    """
    conn = sqlite3.connect(str(DB_PATH))

    # Rate guard: max 50 of same message_type per hour
    recent = conn.execute(
        "SELECT COUNT(*) as c FROM message_approvals "
        "WHERE message_type = ? AND created_at > datetime('now', '-1 hour')",
        (message_type,),
    ).fetchone()
    if recent and recent[0] >= 50:
        conn.close()
        print("[ApprovalSystem] RATE LIMIT: %d %s approvals in last hour, skipping" % (recent[0], message_type))
        return -1

    cursor = conn.execute(
        """INSERT INTO message_approvals
        (lead_id, lead_name, lead_phone, lead_email, lead_source,
         channel, message_type, proposed_message, proposed_subject,
         followup_step, last_outbound, last_inbound)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (lead_id, lead_name, lead_phone, lead_email, lead_source,
         channel, message_type, proposed_message, proposed_subject,
         followup_step, last_outbound, last_inbound)
    )
    approval_id = cursor.lastrowid
    conn.commit()
    conn.close()
    print("[ApprovalSystem] Queued #%d: %s to %s via %s" % (approval_id, message_type, lead_name, channel))
    return approval_id


def approve(approval_id: int, custom_text: str = None) -> dict | None:
    """Approve a message. If custom_text given, send that instead."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM message_approvals WHERE id = ?", (approval_id,)).fetchone()
    if not row:
        conn.close()
        return None

    if row["status"] != "pending":
        conn.close()
        return {"error": f"Already {row['status']}", **dict(row)}

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

    print(f"[ApprovalSystem] #{approval_id} APPROVED by Kai ({method})")
    return result


def skip(approval_id: int) -> dict | None:
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
    print(f"[ApprovalSystem] #{approval_id} SKIPPED")
    return result


def stop_sequence(lead_id: int) -> int:
    """Stop the entire follow-up sequence for a lead."""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.execute(
        "UPDATE message_approvals SET status='skipped', approval_method='sequence_stopped', approved_at=? "
        "WHERE lead_id=? AND status='pending'",
        (_now(), lead_id)
    )
    count = cursor.rowcount
    conn.commit()
    conn.close()
    print(f"[ApprovalSystem] Stopped sequence for lead #{lead_id}: {count} approvals cancelled")
    return count


def expire(approval_id: int) -> dict | None:
    """Expire a message after 24h — NEVER auto-sends."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM message_approvals WHERE id = ?", (approval_id,)).fetchone()
    if not row or row["status"] != "pending":
        conn.close()
        return None
    conn.execute(
        "UPDATE message_approvals SET status='expired', expired_at=? WHERE id=?",
        (_now(), approval_id)
    )
    conn.commit()
    result = dict(conn.execute("SELECT * FROM message_approvals WHERE id = ?", (approval_id,)).fetchone())
    conn.close()
    print(f"[ApprovalSystem] #{approval_id} EXPIRED (never sent)")
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# QUERY HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def get_pending(approval_id: int = None) -> dict | None:
    if approval_id is None:
        return None
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM message_approvals WHERE id = ?", (approval_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_pending() -> list[dict]:
    """All pending approvals, newest first."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM message_approvals WHERE status='pending' ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_latest_pending_for_lead(lead_id: int) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM message_approvals WHERE lead_id=? AND status='pending' "
        "ORDER BY created_at DESC LIMIT 1", (lead_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_approval_stats() -> dict:
    """Stats for the daily briefing."""
    conn = sqlite3.connect(str(DB_PATH))
    pending = conn.execute("SELECT COUNT(*) FROM message_approvals WHERE status='pending'").fetchone()[0]
    approved_today = conn.execute(
        "SELECT COUNT(*) FROM message_approvals WHERE status='approved' AND approved_at >= date('now')"
    ).fetchone()[0]
    skipped_today = conn.execute(
        "SELECT COUNT(*) FROM message_approvals WHERE status='skipped' AND approved_at >= date('now')"
    ).fetchone()[0]
    expired = conn.execute("SELECT COUNT(*) FROM message_approvals WHERE status='expired'").fetchone()[0]
    avg_time = conn.execute(
        "SELECT AVG(CAST((julianday(approved_at) - julianday(created_at)) * 24 * 60 AS INTEGER)) "
        "FROM message_approvals WHERE status='approved' AND approved_at > ''"
    ).fetchone()[0]
    conn.close()
    return {
        "pending": pending,
        "approved_today": approved_today,
        "skipped_today": skipped_today,
        "expired": expired,
        "avg_approval_minutes": round(avg_time or 0, 1),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TRIPLE-REDUNDANCY NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════════════════════

async def notify_all_channels(
    approval_id: int,
    msg_type: str,
    telegram_text: str = None,
    telegram_buttons: object = None,
    lead_name: str = "",
    lead_source: str = "",
    context_text: str = "",
):
    """Fire notifications to ALL 3 channels. Never skip a channel.

    Channel 1: Telegram (primary) — rich formatted with inline buttons
    Channel 2: Slack (secondary) — plain text via webhook
    Channel 3: SMS to Kai (tertiary) — short alert via GV/Twilio/email gateway

    Each channel is independent — one failing doesn't block others.
    """
    errors = []

    # ── Channel 1: Telegram ──────────────────────────────────────────────────
    tg_ok = False
    try:
        tg_ok = await _notify_telegram(approval_id, msg_type, telegram_text, telegram_buttons)
    except Exception as e:
        errors.append(f"telegram:{e}")
        print(f"[ApprovalSystem] Telegram notification failed for #{approval_id}: {e}")

    # ── Channel 2: Slack ─────────────────────────────────────────────────────
    slack_ok = False
    try:
        slack_ok = await _notify_slack(approval_id, msg_type, lead_name, lead_source, context_text)
    except Exception as e:
        errors.append(f"slack:{e}")
        print(f"[ApprovalSystem] Slack notification failed for #{approval_id}: {e}")

    # ── Channel 3: SMS to Kai ────────────────────────────────────────────────
    sms_ok = False
    try:
        sms_ok = await _notify_kai_sms(approval_id, msg_type, lead_name, lead_source)
    except Exception as e:
        errors.append(f"sms:{e}")
        print(f"[ApprovalSystem] SMS notification failed for #{approval_id}: {e}")

    # ── Update notification status ───────────────────────────────────────────
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "UPDATE message_approvals SET telegram_notified=?, slack_notified=?, sms_notified=?, "
        "notification_errors=? WHERE id=?",
        (1 if tg_ok else 0, 1 if slack_ok else 0, 1 if sms_ok else 0,
         "; ".join(errors), approval_id)
    )
    conn.commit()
    conn.close()

    # Log to notification_log table
    for ch, ok, err in [("telegram", tg_ok, ""), ("slack", slack_ok, ""), ("sms", sms_ok, "")]:
        if not ok and errors:
            err = next((e for e in errors if e.startswith(ch)), "")
        _log_notification(approval_id, ch, ok, err)

    channels_ok = sum([tg_ok, slack_ok, sms_ok])
    if channels_ok == 0:
        print(f"[ApprovalSystem] ⚠️ ALL 3 CHANNELS FAILED for #{approval_id}!")
    elif channels_ok < 3:
        print(f"[ApprovalSystem] #{approval_id}: {channels_ok}/3 channels succeeded")
    else:
        print(f"[ApprovalSystem] #{approval_id}: All 3 channels notified ✓")

    return {"telegram": tg_ok, "slack": slack_ok, "sms": sms_ok, "errors": errors}


async def _notify_telegram(approval_id, msg_type, text, buttons) -> bool:
    """Send formatted approval notification to Telegram."""
    try:
        from telegram.bot import get_bot
        bot = get_bot()
        if not bot:
            print("[ApprovalSystem] No Telegram bot instance available")
            return False

        # If no pre-formatted text, generate from approval data
        if not text:
            approval = get_pending(approval_id)
            if not approval:
                return False
            if msg_type in ("initial", "new_lead"):
                text, buttons = bot.format_new_lead_approval(approval)
            elif msg_type == "followup":
                text, buttons = bot.format_followup_approval(approval)
            elif msg_type == "reply":
                text, buttons = _format_reply_notification(approval)
            elif msg_type == "reminder":
                text, buttons = _format_reminder(approval)
            elif msg_type == "expiry":
                text = _format_expiry(approval)
                buttons = None
            else:
                text, buttons = bot.format_new_lead_approval(approval)

        for cid in bot.allowed:
            await bot.send(cid, text, reply_markup=buttons)
            # Set context so text replies route to this approval
            if msg_type not in ("expiry", "reminder"):
                bot.set_context(cid, approval_id=approval_id)

        return True
    except Exception as e:
        print(f"[ApprovalSystem] Telegram error: {e}")
        traceback.print_exc()
        return False


async def _notify_slack(approval_id, msg_type, lead_name, lead_source, context_text="") -> bool:
    """Send notification to Slack via webhook."""
    try:
        from integrations.slack_notify import send_slack

        emoji_map = {
            "initial": "🆕", "new_lead": "🆕", "followup": "📋",
            "reply": "💬", "reminder": "⏰", "expiry": "⏳",
            "test": "🧪",
        }
        emoji = emoji_map.get(msg_type, "📋")

        if msg_type == "reply":
            msg = f"{emoji} Lead reply from {lead_name}: {context_text[:100]}"
        elif msg_type == "reminder":
            msg = f"{emoji} REMINDER: Pending approval #{approval_id} for {lead_name} — respond in Telegram"
        elif msg_type == "expiry":
            msg = f"{emoji} Approval #{approval_id} for {lead_name} expired (not sent)"
        else:
            msg = f"{emoji} Approval needed #{approval_id}: {lead_name} from {lead_source} — check Telegram"

        return await send_slack(msg)
    except Exception as e:
        print(f"[ApprovalSystem] Slack error: {e}")
        return False


async def _notify_kai_sms(approval_id, msg_type, lead_name, lead_source) -> bool:
    """Send short SMS to Kai's personal phone."""
    try:
        from core.lead_pipeline import get_pipeline
        pipeline = get_pipeline()
        if not pipeline:
            print("[ApprovalSystem] No pipeline instance for SMS")
            return False

        sms_map = {
            "initial": f"NEW LEAD: {lead_name} from {lead_source} — check Telegram to approve",
            "new_lead": f"NEW LEAD: {lead_name} from {lead_source} — check Telegram to approve",
            "followup": f"FOLLOW-UP: {lead_name} — check Telegram to approve #{approval_id}",
            "reply": f"REPLY from {lead_name} — check Telegram NOW",
            "reminder": f"REMINDER: #{approval_id} for {lead_name} still pending — check Telegram",
            "expiry": f"EXPIRED: #{approval_id} for {lead_name} (not sent)",
            "test": "TEST: Nexus notification system working ✓",
        }
        message = sms_map.get(msg_type, f"APPROVAL #{approval_id}: {lead_name} — check Telegram")

        await pipeline._notify_kai_sms(message)
        return True
    except Exception as e:
        print(f"[ApprovalSystem] SMS error: {e}")
        return False


def _log_notification(approval_id, channel, success, error_msg=""):
    """Log notification attempt to the database."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "INSERT INTO notification_log (approval_id, channel, success, error_message) VALUES (?, ?, ?, ?)",
            (approval_id, channel, 1 if success else 0, str(error_msg)[:500])
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# NOTIFICATION FORMATTERS
# ═══════════════════════════════════════════════════════════════════════════════

def _format_reply_notification(approval: dict) -> tuple:
    """Format a lead reply notification for Telegram."""
    name = approval.get("lead_name", "Unknown")
    channel = approval.get("channel", "?")
    last_in = approval.get("last_inbound", "")[:200]
    last_out = approval.get("last_outbound", "")[:200]

    text = (
        f"💬 <b>Lead Reply</b>\n\n"
        f"👤 <b>{name}</b>\n"
        f"📱 Channel: {channel}\n\n"
    )
    if last_in:
        text += f"📩 <b>Their message:</b>\n<i>{last_in}</i>\n\n"
    if last_out:
        text += f"📤 <b>Our last message:</b>\n<i>{last_out}</i>\n\n"
    text += "Reply to this message to send a response, or use /skip"

    return text, None


def _format_reminder(approval: dict) -> tuple:
    """Format a reminder notification for Telegram."""
    aid = approval["id"]
    name = approval.get("lead_name", "Unknown")
    source = approval.get("lead_source", "?")
    msg_type = approval.get("message_type", "initial")
    channel = approval.get("channel", "sms")
    proposed = approval.get("proposed_message", "")[:150]
    created = approval.get("created_at", "")
    reminder_count = approval.get("reminder_count", 0)

    # Calculate age
    age_text = ""
    try:
        created_dt = datetime.strptime(created, "%Y-%m-%d %H:%M:%S")
        age_minutes = (datetime.utcnow() - created_dt).total_seconds() / 60
        if age_minutes < 60:
            age_text = f"{int(age_minutes)}m ago"
        else:
            age_text = f"{age_minutes / 60:.1f}h ago"
    except Exception:
        age_text = created

    text = (
        f"⏰ <b>REMINDER #{reminder_count + 1}</b>\n\n"
        f"Pending approval #{aid} for <b>{name}</b>\n"
        f"Source: {source} | Type: {msg_type} | Via: {channel}\n"
        f"Queued: {age_text}\n\n"
        f"📝 <i>{proposed}{'...' if len(proposed) >= 150 else ''}</i>\n\n"
        f"<b>/approve {aid}</b> to send\n"
        f"<b>/skip {aid}</b> to skip\n"
        f"<b>/stop {approval.get('lead_id', '')}</b> to stop all for this lead"
    )

    return text, None


def _format_expiry(approval: dict) -> str:
    """Format an expiry notification for Telegram."""
    aid = approval["id"]
    name = approval.get("lead_name", "Unknown")
    msg_type = approval.get("message_type", "initial")

    return (
        f"⏳ <b>EXPIRED</b>\n\n"
        f"Approval #{aid} for <b>{name}</b> ({msg_type}) expired after 24h.\n"
        f"Message was NOT sent. Lead stays in current state.\n\n"
        f"If you still want to contact them, use /approve {aid} to override."
    )


def format_queue_summary(pending: list[dict]) -> str:
    """Format the /queue command output."""
    if not pending:
        return "✅ No pending approvals — queue is empty!"

    lines = [f"📋 <b>Pending Approvals ({len(pending)})</b>\n"]

    for a in pending[:15]:  # Show max 15
        aid = a["id"]
        name = a.get("lead_name", "Unknown")
        source = a.get("lead_source", "?")
        channel = a.get("channel", "?")
        msg_type = a.get("message_type", "?")
        proposed = a.get("proposed_message", "")[:60]
        reminder_count = a.get("reminder_count", 0)

        # Channel emoji
        ch_emoji = {"sms": "📱", "email": "📧", "messenger": "💬"}.get(channel, "📤")
        type_emoji = {"initial": "🆕", "followup": "📋", "reply": "💬"}.get(msg_type, "📋")

        reminder_tag = f" ⏰x{reminder_count}" if reminder_count > 0 else ""

        lines.append(
            f"{type_emoji} #{aid} — <b>{name}</b> ({source}){reminder_tag}\n"
            f"   {ch_emoji} {channel} | {msg_type}\n"
            f"   📝 <i>{proposed}{'...' if len(proposed) >= 60 else ''}</i>\n"
        )

    if len(pending) > 15:
        lines.append(f"\n... and {len(pending) - 15} more")

    lines.append(f"\n/approve <id> | /skip <id> | /stop <lead_id>")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# HIGH-LEVEL QUEUE + NOTIFY (single call for modules)
# ═══════════════════════════════════════════════════════════════════════════════

async def queue_and_notify(
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
    """Queue a message AND fire triple notifications. Returns approval ID.

    This is the main entry point that other modules should call.
    Replaces the pattern of: queue_message() + _send_approval_notification()
    """
    # Queue it
    approval_id = queue_outbound(
        lead_id=lead_id,
        lead_name=lead_name,
        lead_phone=lead_phone,
        lead_email=lead_email,
        lead_source=lead_source,
        channel=channel,
        message_type=message_type,
        proposed_message=proposed_message,
        proposed_subject=proposed_subject,
        followup_step=followup_step,
        last_outbound=last_outbound,
        last_inbound=last_inbound,
    )

    # Fire triple notifications
    await notify_all_channels(
        approval_id=approval_id,
        msg_type=message_type,
        lead_name=lead_name,
        lead_source=lead_source,
    )

    return approval_id


async def notify_lead_reply(
    lead_id: int,
    lead_name: str,
    reply_text: str,
    channel: str,
    our_last_message: str = "",
) -> None:
    """Notify Kai that a lead replied. No approval needed — just notification.

    Fires on ALL 3 channels to guarantee Kai sees it immediately.
    """
    # Build Telegram notification with reply context
    try:
        from telegram.bot import get_bot
        bot = get_bot()
        if bot:
            # Find lead data
            lead = None
            try:
                from core.lead_pipeline import get_pipeline
                p = get_pipeline()
                if p:
                    lead = p.get_lead(lead_id)
            except Exception:
                pass

            text, buttons = bot.format_lead_reply(
                lead_name=lead_name,
                lead_phone=lead.get("phone", "") if lead else "",
                lead_email=lead.get("email", "") if lead else "",
                reply_text=reply_text,
                our_last_message=our_last_message,
                channel=channel,
                lead_id=lead_id,
            )
            for cid in bot.allowed:
                await bot.send(cid, text, reply_markup=buttons)
                bot.set_context(cid, lead_id=lead_id, channel=channel)
    except Exception as e:
        print(f"[ApprovalSystem] Reply Telegram notification error: {e}")

    # Slack
    try:
        from integrations.slack_notify import send_slack
        await send_slack(f"💬 Lead reply from {lead_name} ({channel}): {reply_text[:80]}")
    except Exception:
        pass

    # SMS to Kai
    try:
        from core.lead_pipeline import get_pipeline
        p = get_pipeline()
        if p:
            await p._notify_kai_sms(f"REPLY from {lead_name} ({channel}): {reply_text[:80]}")
    except Exception:
        pass

    print(f"[ApprovalSystem] Reply notification sent for lead #{lead_id} ({lead_name})")


# ═══════════════════════════════════════════════════════════════════════════════
# COMMAND HANDLERS (for Telegram bot routing)
# ═══════════════════════════════════════════════════════════════════════════════

def handle_queue_command() -> str:
    """Handle /queue — show all pending approvals."""
    pending = get_all_pending()
    return format_queue_summary(pending)


async def handle_approve_command(text: str) -> str:
    """Handle /approve <id> [custom message].

    /approve 42         → approve as-is
    /approve 42 Hey ... → approve with custom text
    """
    parts = text.strip().split(None, 2)  # /approve <id> [rest]

    if len(parts) < 2:
        # No ID provided — show the queue
        pending = get_all_pending()
        if not pending:
            return "✅ No pending approvals!"
        # Auto-approve the most recent one
        return f"Usage: /approve <id>\n\n{format_queue_summary(pending)}"

    try:
        approval_id = int(parts[1])
    except ValueError:
        return "❌ Invalid ID. Usage: /approve <number>"

    custom_text = parts[2] if len(parts) > 2 else None

    result = approve(approval_id, custom_text)
    if not result:
        return f"❌ Approval #{approval_id} not found"
    if "error" in result:
        return f"⚠️ #{approval_id}: {result['error']}"

    # Execute the send
    try:
        from core.lead_pipeline import get_pipeline
        pipeline = get_pipeline()
        if pipeline:
            send_result = await pipeline.execute_approved_message(result)
            if send_result.get("ok"):
                method = send_result.get("method", result.get("channel", "?"))
                return (
                    f"✅ #{approval_id} approved & sent!\n"
                    f"To: {result.get('lead_name', '?')} via {method}\n"
                    f"{'📝 Custom message used' if custom_text else '📋 Original message sent'}"
                )
            else:
                return f"⚠️ #{approval_id} approved but send failed: {send_result.get('error', 'unknown')}"
    except Exception as e:
        return f"⚠️ #{approval_id} approved but execution error: {e}"

    return f"✅ #{approval_id} approved"


async def handle_skip_command(text: str) -> str:
    """Handle /skip <id>."""
    parts = text.strip().split()
    if len(parts) < 2:
        return "Usage: /skip <id>"

    try:
        approval_id = int(parts[1])
    except ValueError:
        return "❌ Invalid ID. Usage: /skip <number>"

    result = skip(approval_id)
    if not result:
        return f"❌ Approval #{approval_id} not found"

    return f"⏭️ #{approval_id} skipped — message NOT sent to {result.get('lead_name', '?')}"


def handle_stop_command(text: str) -> str:
    """Handle /stop <lead_id>."""
    parts = text.strip().split()
    if len(parts) < 2:
        return "Usage: /stop <lead_id>"

    try:
        lead_id = int(parts[1])
    except ValueError:
        return "❌ Invalid lead ID. Usage: /stop <number>"

    count = stop_sequence(lead_id)
    return f"🛑 Stopped sequence for lead #{lead_id}: {count} pending messages cancelled"


async def handle_test_notification() -> str:
    """Handle /test_notification — verify all 3 channels work."""
    print("[ApprovalSystem] Running test notification on all 3 channels...")

    results = {}

    # Telegram
    try:
        from telegram.bot import get_bot
        bot = get_bot()
        if bot:
            for cid in bot.allowed:
                await bot.send(cid, "🧪 <b>TEST: Approval System</b>\n\nAll notification channels being tested...")
            results["telegram"] = "✅"
        else:
            results["telegram"] = "❌ No bot instance"
    except Exception as e:
        results["telegram"] = f"❌ {e}"

    # Slack
    try:
        from integrations.slack_notify import send_slack
        ok = await send_slack("🧪 TEST: Nexus approval system notification test")
        results["slack"] = "✅" if ok else "❌ Webhook returned error"
    except Exception as e:
        results["slack"] = f"❌ {e}"

    # SMS
    try:
        from core.lead_pipeline import get_pipeline
        p = get_pipeline()
        if p:
            await p._notify_kai_sms("TEST: Nexus notification system working ✓")
            results["sms"] = "✅ (sent)"
        else:
            results["sms"] = "❌ No pipeline instance"
    except Exception as e:
        results["sms"] = f"❌ {e}"

    summary = (
        f"🧪 <b>Notification Test Results</b>\n\n"
        f"📱 Telegram: {results.get('telegram', '?')}\n"
        f"💬 Slack: {results.get('slack', '?')}\n"
        f"📲 SMS: {results.get('sms', '?')}\n\n"
        f"Timestamp: {_la_now()} PT"
    )

    return summary


# ═══════════════════════════════════════════════════════════════════════════════
# ASYNC REMINDER & EXPIRY SCHEDULER
# ═══════════════════════════════════════════════════════════════════════════════

async def run_approval_scheduler(send_fn=None):
    """Background scheduler that:
    1. Sends reminders for pending approvals older than 2h
    2. Expires approvals older than 24h (NEVER auto-sends)
    3. Retries notifications that failed on all channels

    Runs every 5 minutes. Registered with watchdog.
    """
    print("[ApprovalSystem] Reminder scheduler started (every 5 min)")

    while True:
        try:
            # Heartbeat
            try:
                from core.watchdog import heartbeat
                heartbeat("approval_scheduler")
            except Exception:
                pass

            await _check_reminders()
            await _check_expiry()
            await _retry_failed_notifications()

        except Exception as e:
            print(f"[ApprovalSystem] Scheduler error: {e}")
            traceback.print_exc()

        await asyncio.sleep(SCHEDULER_INTERVAL)


async def _check_reminders():
    """Send reminders for pending approvals that are overdue."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Reminder 1: 2h old, not yet reminded
    cutoff_2h = (datetime.utcnow() - timedelta(hours=REMINDER_1_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    cutoff_6h = (datetime.utcnow() - timedelta(hours=REMINDER_2_HOURS)).strftime("%Y-%m-%d %H:%M:%S")

    rows = conn.execute(
        "SELECT * FROM message_approvals WHERE status='pending' "
        "AND (telegram_notified=1 OR slack_notified=1 OR sms_notified=1) "
        "AND created_at <= ? AND reminder_count < 2 "
        "ORDER BY created_at ASC",
        (cutoff_2h,)
    ).fetchall()
    conn.close()

    for row in rows:
        approval = dict(row)
        aid = approval["id"]
        reminder_count = approval["reminder_count"]

        # Only send 2nd reminder if 6h+ old
        if reminder_count >= 1:
            try:
                created = datetime.strptime(approval["created_at"], "%Y-%m-%d %H:%M:%S")
                if datetime.utcnow() - created < timedelta(hours=REMINDER_2_HOURS):
                    continue
            except Exception:
                pass

        print(f"[ApprovalSystem] Sending reminder #{reminder_count + 1} for approval #{aid}")

        await notify_all_channels(
            approval_id=aid,
            msg_type="reminder",
            lead_name=approval.get("lead_name", "Unknown"),
            lead_source=approval.get("lead_source", "?"),
        )

        # Increment reminder count
        conn2 = sqlite3.connect(str(DB_PATH))
        conn2.execute(
            "UPDATE message_approvals SET reminder_count = reminder_count + 1 WHERE id=?",
            (aid,)
        )
        conn2.commit()
        conn2.close()


async def _check_expiry():
    """Expire approvals older than 24h. NEVER auto-sends."""
    cutoff_24h = (datetime.utcnow() - timedelta(hours=EXPIRE_HOURS)).strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM message_approvals WHERE status='pending' AND created_at <= ? "
        "ORDER BY created_at ASC",
        (cutoff_24h,)
    ).fetchall()
    conn.close()

    for row in rows:
        approval = dict(row)
        aid = approval["id"]

        result = expire(aid)
        if result:
            await notify_all_channels(
                approval_id=aid,
                msg_type="expiry",
                lead_name=approval.get("lead_name", "Unknown"),
                lead_source=approval.get("lead_source", "?"),
            )


async def _retry_failed_notifications():
    """Retry notifications where ALL channels failed (0/3).

    Only retries once — if all 3 fail again, gives up.
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM message_approvals WHERE status='pending' "
        "AND telegram_notified=0 AND slack_notified=0 AND sms_notified=0 "
        "AND notification_errors != '' "
        "ORDER BY created_at ASC LIMIT 5"
    ).fetchall()
    conn.close()

    for row in rows:
        approval = dict(row)
        aid = approval["id"]
        print(f"[ApprovalSystem] Retrying failed notification for #{aid}")

        await notify_all_channels(
            approval_id=aid,
            msg_type=approval.get("message_type", "initial"),
            lead_name=approval.get("lead_name", "Unknown"),
            lead_source=approval.get("lead_source", "?"),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# STARTUP RECOVERY
# ═══════════════════════════════════════════════════════════════════════════════

async def recover_unnotified():
    """On startup, find pending approvals that were never notified and send them.

    This handles the case where the server crashed between queue and notify.
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM message_approvals WHERE status='pending' "
        "AND telegram_notified=0 AND slack_notified=0 AND sms_notified=0 "
        "AND notification_errors='' "
        "ORDER BY created_at ASC"
    ).fetchall()
    conn.close()

    if not rows:
        return

    print(f"[ApprovalSystem] Recovering {len(rows)} unnotified approvals from before shutdown")

    for row in rows:
        approval = dict(row)
        await notify_all_channels(
            approval_id=approval["id"],
            msg_type=approval.get("message_type", "initial"),
            lead_name=approval.get("lead_name", "Unknown"),
            lead_source=approval.get("lead_source", "?"),
        )
        await asyncio.sleep(1)  # Don't flood


# ═══════════════════════════════════════════════════════════════════════════════
# MODULE SINGLETON
# ═══════════════════════════════════════════════════════════════════════════════

def get_approval_system():
    """Get the singleton (for backwards compat — module functions are stateless)."""
    return _system


def init_approval(send_fn=None):
    """Initialize the approval system. Call on startup."""
    global _system
    init_approval_system()  # DB tables
    _system = True
    print("[ApprovalSystem] Initialized — all outbound messages require approval")
    return _system
