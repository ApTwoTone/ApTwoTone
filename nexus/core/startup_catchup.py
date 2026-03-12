"""
Startup Catch-Up Routine — Part 4B of Nexus Activation.

Runs once at server startup to process anything missed during downtime:
1. Detect how long the server was offline (compare last heartbeat to now)
2. Scan for missed Facebook leads still in status='new'
3. Expire stale pending approvals (older than 24h)
4. Reschedule missed follow-up sequences
5. Send a Telegram startup summary to Kai

Called from server.py via:
    asyncio.create_task(run_startup_catchup())
"""
from __future__ import annotations

import asyncio
import sqlite3
import traceback
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DB_PATH = Path.home() / ".nexus" / "memory.db"
LA_TZ = ZoneInfo("America/Los_Angeles")

# ── Heartbeat key used by the watchdog / startup system ──────────────────────
HEARTBEAT_KEY = "nexus_server"


def _now_utc() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _now_la() -> str:
    return datetime.now(LA_TZ).strftime("%Y-%m-%d %I:%M %p PT")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_tables(conn: sqlite3.Connection):
    """Create the nexus_meta table if it doesn't exist (stores heartbeat, etc.)."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS nexus_meta (
        key TEXT PRIMARY KEY,
        value TEXT DEFAULT '',
        updated_at TEXT DEFAULT (datetime('now'))
    );
    """)
    conn.commit()


def _log_to_db(conn: sqlite3.Connection, channel: str, recipient: str, message: str, status: str):
    """Log a startup catch-up event to notification_log.

    Uses the existing notification_log table, adapting columns:
    - channel: 'startup_catchup'
    - success: 1 for 'sent', 0 for 'error'
    - error_message: stores the message text
    """
    try:
        conn.execute(
            "INSERT INTO notification_log (approval_id, channel, success, error_message, created_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (0, f"startup_{channel}", 1 if status == "sent" else 0, f"[{recipient}] {message}"[:500])
        )
        conn.commit()
    except Exception:
        pass  # notification_log may not exist yet if approval_system hasn't init'd


# ── Step 1: Detect downtime ─────────────────────────────────────────────────

def _detect_downtime(conn: sqlite3.Connection) -> tuple[float, str]:
    """Check how long the server was offline.

    Returns (downtime_hours, last_heartbeat_str).
    If no previous heartbeat, returns (0, "first_run").
    """
    _ensure_tables(conn)
    row = conn.execute(
        "SELECT value FROM nexus_meta WHERE key = ?", (HEARTBEAT_KEY,)
    ).fetchone()

    if not row or not row["value"]:
        return 0.0, "first_run"

    try:
        last_hb = datetime.strptime(row["value"], "%Y-%m-%d %H:%M:%S")
        now = datetime.utcnow()
        delta = now - last_hb
        return delta.total_seconds() / 3600.0, row["value"]
    except (ValueError, TypeError):
        return 0.0, "parse_error"


def _update_heartbeat(conn: sqlite3.Connection):
    """Write current time as the server heartbeat."""
    _ensure_tables(conn)
    now = _now_utc()
    conn.execute(
        "INSERT OR REPLACE INTO nexus_meta (key, value, updated_at) VALUES (?, ?, ?)",
        (HEARTBEAT_KEY, now, now)
    )
    conn.commit()


# ── Step 2: Scan for missed Facebook leads ───────────────────────────────────

def _scan_missed_leads(conn: sqlite3.Connection, downtime_hours: float) -> list[dict]:
    """Find leads still in status='new' that arrived during downtime.

    These are leads that came in while the server was offline and haven't been
    picked up by the pipeline yet.
    """
    if downtime_hours <= 0:
        # Even on fresh start, check for any stale 'new' leads older than 5 min
        cutoff = (datetime.utcnow() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    else:
        cutoff = (datetime.utcnow() - timedelta(hours=downtime_hours)).strftime("%Y-%m-%d %H:%M:%S")

    rows = conn.execute(
        "SELECT id, first_name, last_name, phone, email, source, discovered_at "
        "FROM leads WHERE status = 'new' AND discovered_at >= ? "
        "ORDER BY discovered_at ASC",
        (cutoff,)
    ).fetchall()

    return [dict(r) for r in rows]


# ── Step 3: Expire stale pending approvals ───────────────────────────────────

def _expire_stale_approvals(conn: sqlite3.Connection) -> int:
    """Mark pending approvals older than 24h as expired.

    Returns the count of expired approvals.
    """
    cutoff = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    now = _now_utc()

    # Only expire approvals with a valid created_at that's older than 24h.
    # Skip empty created_at (legacy data) — those are handled by explicit action.
    cursor = conn.execute(
        "UPDATE message_approvals SET status = 'expired', expired_at = ? "
        "WHERE status = 'pending' AND created_at != '' AND created_at < ?",
        (now, cutoff)
    )
    count = cursor.rowcount
    conn.commit()
    return count


# ── Step 4: Reschedule missed follow-ups ─────────────────────────────────────

def _reschedule_missed_followups(conn: sqlite3.Connection) -> int:
    """Find active follow-up sequences that were due during downtime and reschedule.

    Sets their next_followup_at to 5 minutes from now so the follow-up engine
    picks them up on its next tick (during business hours).

    Returns count of rescheduled sequences.
    """
    now = _now_utc()
    reschedule_to = (datetime.utcnow() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")

    # Find sequences that were supposed to fire but didn't (next_send_at is in the past)
    overdue = conn.execute(
        "SELECT f.id, f.lead_id, f.sequence_type, f.step, f.next_send_at, "
        "l.first_name, l.last_name, l.status as lead_status "
        "FROM follow_up_sequences f "
        "JOIN leads l ON f.lead_id = l.id "
        "WHERE f.status = 'active' AND f.next_send_at IS NOT NULL AND f.next_send_at < ?",
        (now,)
    ).fetchall()

    rescheduled = 0
    for row in overdue:
        lead_status = row["lead_status"] or ""
        # Don't reschedule if the lead is in a terminal state
        if lead_status in ("replied", "closed", "opted_out", "booked", "completed", "lost"):
            conn.execute(
                "UPDATE follow_up_sequences SET status = 'stopped', next_send_at = NULL "
                "WHERE id = ?", (row["id"],)
            )
            continue

        # Reschedule to 5 minutes from now
        conn.execute(
            "UPDATE follow_up_sequences SET next_send_at = ? WHERE id = ?",
            (reschedule_to, row["id"])
        )
        rescheduled += 1

    conn.commit()
    return rescheduled


# ── Step 5: Build and send Telegram summary ──────────────────────────────────

def _build_summary(
    downtime_hours: float,
    last_heartbeat: str,
    missed_leads: list[dict],
    expired_approvals: int,
    rescheduled_followups: int,
) -> str:
    """Build a Markdown-formatted startup summary for Telegram."""
    now_la = _now_la()

    lines = [
        "🔄 *NEXUS STARTUP CATCH-UP*",
        f"_{now_la}_",
        "",
    ]

    # Downtime info
    if last_heartbeat == "first_run":
        lines.append("▸ First run detected — no prior heartbeat")
    elif downtime_hours < 0.1:
        lines.append("▸ Downtime: less than 6 minutes")
    elif downtime_hours < 1:
        lines.append(f"▸ Downtime: {int(downtime_hours * 60)} minutes")
    else:
        h = int(downtime_hours)
        m = int((downtime_hours - h) * 60)
        lines.append(f"▸ Downtime: {h}h {m}m")
        if last_heartbeat not in ("first_run", "parse_error"):
            lines.append(f"▸ Last seen: {last_heartbeat} UTC")

    lines.append("")

    # Missed leads
    if missed_leads:
        lines.append(f"📥 *{len(missed_leads)} missed lead(s)* (status=new):")
        for lead in missed_leads[:10]:  # Cap at 10 to avoid huge messages
            name = f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip() or "Unknown"
            source = lead.get("source", "unknown")
            phone = lead.get("phone", "")
            lines.append(f"  • {name} ({source}) {phone}")
        if len(missed_leads) > 10:
            lines.append(f"  ... and {len(missed_leads) - 10} more")
        lines.append("  → Pipeline will process these automatically")
    else:
        lines.append("📥 No missed leads")

    lines.append("")

    # Expired approvals
    if expired_approvals > 0:
        lines.append(f"⏰ *{expired_approvals} approval(s) expired* (>24h pending)")
    else:
        lines.append("⏰ No expired approvals")

    # Rescheduled follow-ups
    if rescheduled_followups > 0:
        lines.append(f"🔁 *{rescheduled_followups} follow-up(s) rescheduled*")
    else:
        lines.append("🔁 No missed follow-ups")

    lines.append("")
    lines.append("✅ Nexus is back online and processing.")

    return "\n".join(lines)


# ── Main routine ─────────────────────────────────────────────────────────────

async def run_startup_catchup():
    """Run immediately after server startup to catch up on missed work.

    Called from server.py as:
        asyncio.create_task(run_startup_catchup())

    Steps:
        1. Detect downtime duration from last heartbeat
        2. Scan for missed leads (status='new')
        3. Expire stale pending approvals (>24h)
        4. Reschedule missed follow-up sequences
        5. Send Telegram summary to Kai
        6. Update heartbeat to current time
    """
    # Brief delay to let other startup tasks finish first
    await asyncio.sleep(3)
    print("[Catchup] Starting startup catch-up routine...")

    conn = _get_conn()

    try:
        # Step 1: Detect downtime
        downtime_hours, last_heartbeat = _detect_downtime(conn)
        if last_heartbeat == "first_run":
            print("[Catchup] First run — no previous heartbeat found")
        else:
            print(f"[Catchup] Server was offline for {downtime_hours:.1f} hours (last heartbeat: {last_heartbeat})")

        # Step 2: Scan for missed leads
        missed_leads = _scan_missed_leads(conn, downtime_hours)
        if missed_leads:
            print(f"[Catchup] Found {len(missed_leads)} missed lead(s) in status='new'")
            # Trigger pipeline processing for each missed lead
            try:
                from core.lead_pipeline import get_pipeline
                pipeline = get_pipeline()
                if pipeline:
                    for lead in missed_leads:
                        try:
                            asyncio.create_task(pipeline.process_new_lead(lead["id"]))
                            print(f"[Catchup]   → Queued lead #{lead['id']} for processing")
                        except Exception as e:
                            print(f"[Catchup]   → Failed to queue lead #{lead['id']}: {e}")
            except Exception as e:
                print(f"[Catchup] Pipeline not available for lead processing: {e}")
        else:
            print("[Catchup] No missed leads found")

        # Step 3: Expire stale approvals
        try:
            expired_approvals = _expire_stale_approvals(conn)
            if expired_approvals:
                print(f"[Catchup] Expired {expired_approvals} stale approval(s) (>24h pending)")
            else:
                print("[Catchup] No stale approvals to expire")
        except Exception as e:
            expired_approvals = 0
            print(f"[Catchup] Approval expiry check: {e}")

        # Step 4: Reschedule missed follow-ups
        try:
            rescheduled_followups = _reschedule_missed_followups(conn)
            if rescheduled_followups:
                print(f"[Catchup] Rescheduled {rescheduled_followups} missed follow-up(s)")
            else:
                print("[Catchup] No missed follow-ups to reschedule")
        except Exception as e:
            rescheduled_followups = 0
            print(f"[Catchup] Follow-up reschedule check: {e}")

        # Step 5: Send Telegram summary
        summary = _build_summary(
            downtime_hours, last_heartbeat,
            missed_leads, expired_approvals, rescheduled_followups,
        )

        try:
            from telegram.bot import get_bot
            bot = get_bot()
            if bot:
                await bot.send_to_all(summary)
                print("[Catchup] Startup summary sent to Telegram")
            else:
                print("[Catchup] Telegram bot not available — summary printed to console only")
                print(summary)
        except Exception as e:
            print(f"[Catchup] Telegram send failed: {e}")
            print(summary)

        # Log to notification_log
        _log_to_db(
            conn, "catchup_summary", "kai",
            f"Downtime={downtime_hours:.1f}h | Leads={len(missed_leads)} | "
            f"Expired={expired_approvals} | Rescheduled={rescheduled_followups}",
            "sent"
        )

        # Step 6: Update heartbeat
        _update_heartbeat(conn)
        print("[Catchup] Heartbeat updated — startup catch-up complete")

    except Exception as e:
        print(f"[Catchup] ERROR in startup catch-up: {e}")
        traceback.print_exc()
        _log_to_db(conn, "catchup_error", "system", str(e)[:500], "error")
    finally:
        conn.close()


# ── Heartbeat writer (called periodically from server or watchdog) ───────────

async def write_heartbeat():
    """Write a heartbeat timestamp. Call this periodically so the next startup
    knows how long the server was offline."""
    conn = _get_conn()
    try:
        _update_heartbeat(conn)
    finally:
        conn.close()


async def run_heartbeat_loop(interval: int = 300):
    """Background loop that writes a heartbeat every `interval` seconds (default 5 min).
    This ensures the startup catch-up routine can measure downtime accurately."""
    print(f"[Catchup] Heartbeat loop started (every {interval}s)")
    while True:
        try:
            await write_heartbeat()
        except Exception as e:
            print(f"[Catchup] Heartbeat write error: {e}")
        # Check milestones every heartbeat cycle
        try:
            from core.milestones import check_milestones
            triggered = await check_milestones()
            if triggered:
                print(f"[Milestones] Triggered: {triggered}")
        except Exception as e:
            print(f"[Milestones] Check error: {e}")
        await asyncio.sleep(interval)
