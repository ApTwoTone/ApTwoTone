from __future__ import annotations
import logging
import os, sys, json, asyncio, time, re
from datetime import datetime, timedelta
from pathlib import Path
import requests
sys.path.insert(0, str(Path(__file__).parent))

from core.service_control import (
    ServiceControlError,
    get_service_state,
    list_service_states,
    set_service_enabled,
)

from fastapi import FastAPI, Request, Depends, HTTPException, Header
from fastapi.responses import HTMLResponse, Response, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import hashlib, hmac

CONFIG_FILE = Path.home() / ".nexus" / "config.json"
CONFIG_FILE.parent.mkdir(exist_ok=True)

def load_config():
    if CONFIG_FILE.exists():
        try: return json.loads(CONFIG_FILE.read_text())
        except: pass
    return {}

def save_config(data):
    cfg = load_config(); cfg.update(data)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

def has_key(cfg): return bool(cfg.get("anthropic_key") or cfg.get("moonshot_key") or cfg.get("gemini_key") or cfg.get("openai_key"))

# ── Security: API Key Auth for action endpoints ──────────────────────────────
async def verify_action_auth(request: Request):
    """Require X-Nexus-Key header for action endpoints (send, approve, config changes).
    Localhost (127.0.0.1, ::1) is always allowed for dashboard and Telegram handlers."""
    client_ip = request.client.host if request.client else ""
    if client_ip in ("127.0.0.1", "::1", "localhost"):
        return True  # Localhost always OK
    key = request.headers.get("x-nexus-key", "")
    cfg = load_config()
    expected = cfg.get("nexus_api_key", "")
    if not expected:
        return True  # No key configured = open (dev mode)
    if not key or key != expected:
        from core.security import get_security_gate
        gate = get_security_gate()
        gate.log_security("unauthorized_api_access",
                          f"IP={client_ip} path={request.url.path}",
                          "warning", source_ip=client_ip)
        raise HTTPException(status_code=401, detail="Unauthorized — X-Nexus-Key required")
    return True

async def verify_facebook_signature(request: Request):
    """Verify Facebook webhook X-Hub-Signature-256 header (HMAC-SHA256)."""
    cfg = load_config()
    app_secret = cfg.get("fb_app_secret", "")
    if not app_secret:
        return True  # No secret configured = skip verification
    signature = request.headers.get("x-hub-signature-256", "")
    if not signature:
        # Allow requests without signature in test mode
        if cfg.get("messaging_mode") == "test":
            return True
        from core.security import get_security_gate
        gate = get_security_gate()
        gate.log_security("fb_webhook_no_signature",
                          f"IP={request.client.host if request.client else '?'}",
                          "warning")
        raise HTTPException(status_code=401, detail="Missing X-Hub-Signature-256")
    # Read raw body for verification
    body = await request.body()
    expected_sig = "sha256=" + hmac.new(
        app_secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_sig):
        from core.security import get_security_gate
        gate = get_security_gate()
        gate.log_security("fb_webhook_invalid_signature",
                          f"IP={request.client.host if request.client else '?'}",
                          "critical")
        raise HTTPException(status_code=403, detail="Invalid signature")
    return True

app = FastAPI()

_STARTUP_LOCK = None
_STARTUP_COMPLETE = False
_CRM_BOOTSTRAP_LOCK = None

# CORS — allow website (Cloudflare Pages) to POST to CRM webhooks
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files for CRM dashboard
_static_dir = Path(__file__).parent / "static"
_static_dir.mkdir(exist_ok=True)
app.mount("/dashboard", StaticFiles(directory=str(_static_dir), html=True), name="dashboard")

# Mount vendor leads dashboard
_vendor_leads_dir = Path(__file__).parent / "static" / "vendor-leads"
_vendor_leads_dir.mkdir(parents=True, exist_ok=True)
app.mount("/vendor-leads", StaticFiles(directory=str(_vendor_leads_dir), html=True), name="vendor-leads")

# Mount aggressive outreach workspace
_aggressive_outreach_dir = Path(__file__).parent / "static" / "aggressive-outreach"
_aggressive_outreach_dir.mkdir(parents=True, exist_ok=True)
app.mount("/aggressive-outreach", StaticFiles(directory=str(_aggressive_outreach_dir), html=True), name="aggressive-outreach")

# Mount ad creatives for Notion image embedding
_ad_creatives_dir = Path.home() / "Documents" / "ad_creatives"
_ad_creatives_dir.mkdir(parents=True, exist_ok=True)
app.mount("/ad_creatives", StaticFiles(directory=str(_ad_creatives_dir)), name="ad_creatives")

# Mount public website (Zoar Bathroom Rentals)
_website_dir = Path(__file__).parent / "website"
if _website_dir.exists():
    app.mount("/site", StaticFiles(directory=str(_website_dir), html=True), name="website")

# Mount Nexus Network frontend (static export from nexus-network/out)
_nexus_ui_dir = Path.home() / "nexus-network" / "out"
if _nexus_ui_dir.exists():
    # Next.js references /_next/static/... with root-relative paths
    _next_dir = _nexus_ui_dir / "_next"
    if _next_dir.exists():
        app.mount("/_next", StaticFiles(directory=str(_next_dir)), name="nexus-next")
    app.mount("/app", StaticFiles(directory=str(_nexus_ui_dir), html=True), name="nexus-app")

# ── Vendor Referral Partner API ────────────────────────────────────────────────
try:
    from core.vendor_api import register_vendor_routes
    register_vendor_routes(app, verify_action_auth=verify_action_auth)
except Exception as _e:
    print(f"[Nexus] Vendor API init: {_e}")

try:
    from core.aggressive_outreach import register_aggressive_outreach_routes
    register_aggressive_outreach_routes(app, verify_action_auth=verify_action_auth)
except Exception as _e:
    print(f"[Nexus] Aggressive Outreach API init: {_e}")

# ── Booking, Conversation, Email Queue, Referral, Analytics APIs ──────────────
try:
    from core.booking_api import register_booking_routes
    register_booking_routes(app, verify_action_auth=verify_action_auth)
except Exception as _e:
    print(f"[Nexus] Booking API init: {_e}")

try:
    from core.conversation_api import register_conversation_routes
    register_conversation_routes(app, verify_action_auth=verify_action_auth)
except Exception as _e:
    print(f"[Nexus] Conversation API init: {_e}")

try:
    from core.email_queue_api import register_email_queue_routes
    register_email_queue_routes(app, verify_action_auth=verify_action_auth)
except Exception as _e:
    print(f"[Nexus] Email Queue API init: {_e}")

try:
    from core.referral_api import register_referral_routes
    register_referral_routes(app, verify_action_auth=verify_action_auth)
except Exception as _e:
    print(f"[Nexus] Referral API init: {_e}")

try:
    from core.analytics_api import register_analytics_routes
    register_analytics_routes(app)
except Exception as _e:
    print(f"[Nexus] Analytics API init: {_e}")

try:
    from core.register_daemons import register_all_daemons
    _daemon_result = register_all_daemons()
    print(f"[Nexus] Daemon registry: {_daemon_result['registered']} agents, {_daemon_result['running']} running")

    from core.runtime_api import register_runtime_routes
    register_runtime_routes(app)
except Exception as _e:
    print(f"[Nexus] Runtime API init: {_e}")

_orch = None
_sse_clients: list = []
_session_events: list = []
_gv_reply_poller = None
_gv_reply_task = None
_gv_owner_lock_fd = None
_server_instance_lock_fd = None
_sheet_crm_watcher = None
_sheet_crm_task = None

AGENTS_MD_PATH = Path(__file__).parent / "AGENTS.md"


def _coord_db_path() -> Path:
    return Path.home() / ".nexus" / "memory.db"


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return bool(row)


def _json_list(value):
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(v).strip() for v in parsed if str(v).strip()]
        except Exception:
            pass
        return [value]
    return []


def _cleanup_stale_agent_sessions(conn) -> int:
    """Mark stale active sessions as idle and release stale file locks."""
    stale_minutes = int(os.getenv("NEXUS_COORD_STALE_MINUTES", "120") or 120)
    stale_days = max(stale_minutes, 5) / (60.0 * 24.0)
    cur = conn.execute(
        """
        UPDATE agent_sessions
        SET status = 'idle',
            working_on = '[auto-idled: stale heartbeat]',
            files_locked = '[]',
            last_heartbeat = datetime('now')
        WHERE status = 'active'
          AND (
            last_heartbeat IS NULL
            OR last_heartbeat = ''
            OR (julianday('now') - julianday(last_heartbeat)) > ?
          )
        """,
        (stale_days,),
    )
    changed = int(cur.rowcount or 0)
    if changed:
        conn.commit()
    return changed


def _coordination_status_payload(conn, include_completed_limit: int = 50) -> dict:
    conn.row_factory = __import__("sqlite3").Row
    _cleanup_stale_agent_sessions(conn)

    active_rows = conn.execute(
        """
        SELECT session_id, device, agent_type, working_on, files_locked, started_at, last_heartbeat
        FROM agent_sessions
        WHERE status = 'active'
        ORDER BY last_heartbeat DESC
        """
    ).fetchall()
    completed_rows = conn.execute(
        """
        SELECT session_id, completed_work, files_locked, status, last_heartbeat
        FROM agent_sessions
        WHERE status IN ('completed', 'idle')
          AND date(last_heartbeat, 'localtime') = date('now', 'localtime')
        ORDER BY last_heartbeat DESC
        LIMIT ?
        """,
        (max(1, min(include_completed_limit, 200)),),
    ).fetchall()

    locked_files = {}
    active_sessions = []
    for row in active_rows:
        files = _json_list(row["files_locked"])
        active_sessions.append(
            {
                "session_id": row["session_id"],
                "device": row["device"],
                "agent_type": row["agent_type"],
                "working_on": row["working_on"],
                "files_locked": files,
                "started_at": row["started_at"],
                "last_heartbeat": row["last_heartbeat"],
            }
        )
        for fp in files:
            locked_files[fp] = row["session_id"]

    completed_today = []
    for row in completed_rows:
        completed_today.append(
            {
                "session_id": row["session_id"],
                "completed_work": _json_list(row["completed_work"]),
                "files_changed": _json_list(row["files_locked"]),
                "status": row["status"],
                "last_heartbeat": row["last_heartbeat"],
            }
        )
    return {
        "active_sessions": active_sessions,
        "completed_today": completed_today,
        "locked_files": locked_files,
        "recent_activity": _recent_agent_activity(conn, limit=25),
    }


def _ensure_agent_activity_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT DEFAULT '',
            device TEXT DEFAULT '',
            agent_type TEXT DEFAULT '',
            entry_kind TEXT DEFAULT 'update',
            summary TEXT DEFAULT '',
            files_changed TEXT DEFAULT '[]',
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )


def _recent_agent_activity(conn, limit: int = 25) -> list[dict]:
    try:
        _ensure_agent_activity_table(conn)
        rows = conn.execute(
            """
            SELECT id, session_id, device, agent_type, entry_kind, summary, files_changed, created_at
            FROM agent_activity_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, min(int(limit or 25), 200)),),
        ).fetchall()
    except Exception:
        return []

    items = []
    for row in rows:
        items.append(
            {
                "id": int(row["id"] or 0),
                "session_id": row["session_id"] or "",
                "device": row["device"] or "",
                "agent_type": row["agent_type"] or "",
                "entry_kind": row["entry_kind"] or "update",
                "summary": row["summary"] or "",
                "files_changed": _json_list(row["files_changed"]),
                "created_at": row["created_at"] or "",
            }
        )
    return items


def _agent_activity_markdown(entries: list[dict]) -> str:
    lines = [
        "# Shared Agent Log",
        "",
        "Use this file as the persistent cross-device change journal for Codex and Claude Code sessions.",
        "",
        "Rules:",
        "- Append one entry after every meaningful edit or change.",
        "- Use `./scripts/agent_change_log.sh <session_id> \"<summary>\" <files...>` instead of hand-editing when possible.",
        "- This file is mirrored from the Nexus coordination server when `./scripts/coord_sync_shared_log.sh` runs.",
        "- Read this file at session start and after any major task so all devices stay in sync.",
        "",
    ]
    for item in entries:
        files = ", ".join(item.get("files_changed", []) or []) or "(none listed)"
        lines.append(f"## {item.get('created_at', '')} | {item.get('session_id', '')}")
        lines.append(f"Device: {item.get('device', '')}")
        lines.append(f"Agent: {item.get('agent_type', '')}")
        lines.append(f"Summary: {item.get('summary', '')}")
        lines.append(f"Files: {files}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _db_context_snapshot(conn, limit: int = 20) -> dict:
    """Build a compact DB snapshot for new agent session bootstrap."""
    snapshot = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "database_path": str(_coord_db_path()),
        "leads_total": 0,
        "leads_by_status": {},
        "recent_leads": [],
        "conversations_total": 0,
        "recent_conversations": [],
        "email_stats": {
            "sent_total": 0,
            "sent_today": 0,
            "bounced_total": 0,
            "pending_queue": 0,
        },
        "recent_activity": [],
    }
    n = max(5, min(int(limit or 20), 100))

    if _table_exists(conn, "leads"):
        try:
            row = conn.execute("SELECT COUNT(*) AS c FROM leads").fetchone()
            snapshot["leads_total"] = int((row["c"] if row else 0) or 0)
            status_rows = conn.execute(
                """
                SELECT
                  CASE
                    WHEN COALESCE(NULLIF(booking_status,''), NULLIF(status,''), '') = '' THEN 'unknown'
                    ELSE COALESCE(NULLIF(booking_status,''), NULLIF(status,''), 'unknown')
                  END AS s,
                  COUNT(*) AS c
                FROM leads
                GROUP BY s
                ORDER BY c DESC
                """
            ).fetchall()
            snapshot["leads_by_status"] = {str(r["s"]): int(r["c"] or 0) for r in status_rows}
            rows = conn.execute("SELECT * FROM leads ORDER BY id DESC LIMIT ?", (n,)).fetchall()
            for r in rows:
                d = dict(r)
                name = (
                    d.get("full_name")
                    or d.get("name")
                    or d.get("contact_name")
                    or ("%s %s" % (d.get("first_name", "") or "", d.get("last_name", "") or "")).strip()
                    or "Unknown"
                )
                snapshot["recent_leads"].append(
                    {
                        "id": d.get("id"),
                        "name": name,
                        "phone": d.get("phone", "") or "",
                        "email": d.get("email", "") or "",
                        "status": d.get("status", "") or "",
                        "booking_status": d.get("booking_status", "") or "",
                        "source": d.get("source", "") or "",
                        "created_at": d.get("created_at", "") or "",
                        "last_contacted_at": d.get("last_contacted_at", "") or "",
                    }
                )
        except Exception:
            pass

    if _table_exists(conn, "lead_conversations"):
        try:
            row = conn.execute("SELECT COUNT(*) AS c FROM lead_conversations").fetchone()
            snapshot["conversations_total"] = int((row["c"] if row else 0) or 0)
            rows = conn.execute(
                """
                SELECT lead_id, channel, last_direction, last_message_text, updated_at
                FROM lead_conversations
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (n,),
            ).fetchall()
            for r in rows:
                snapshot["recent_conversations"].append(
                    {
                        "lead_id": r["lead_id"],
                        "channel": r["channel"],
                        "last_direction": r["last_direction"],
                        "last_message_text": (r["last_message_text"] or "")[:200],
                        "updated_at": r["updated_at"] or "",
                    }
                )
        except Exception:
            pass

    if _table_exists(conn, "outbound_log"):
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM outbound_log WHERE lower(result) = 'sent'"
            ).fetchone()
            snapshot["email_stats"]["sent_total"] = int((row["c"] if row else 0) or 0)
            row = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM outbound_log
                WHERE lower(result) = 'sent'
                  AND date(COALESCE(sent_at, ts), 'localtime') = date('now', 'localtime')
                """
            ).fetchone()
            snapshot["email_stats"]["sent_today"] = int((row["c"] if row else 0) or 0)
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM outbound_log WHERE lower(result) = 'bounced'"
            ).fetchone()
            snapshot["email_stats"]["bounced_total"] = int((row["c"] if row else 0) or 0)
        except Exception:
            pass

    if _table_exists(conn, "email_queue"):
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM email_queue WHERE lower(status) IN ('pending','queued','approved')"
            ).fetchone()
            snapshot["email_stats"]["pending_queue"] = int((row["c"] if row else 0) or 0)
        except Exception:
            pass

    snapshot["recent_activity"] = _recent_agent_activity(conn, limit=min(n, 10))

    return snapshot


def sync_agents_md(conn=None) -> bool:
    """Regenerate AGENTS.md from DB session state."""
    import sqlite3 as _sql3

    close_conn = False
    if conn is None:
        conn = _sql3.connect(str(_coord_db_path()))
        close_conn = True
    conn.row_factory = _sql3.Row
    try:
        active = conn.execute(
            """
            SELECT session_id, device, agent_type, working_on, files_locked, started_at, last_heartbeat
            FROM agent_sessions
            WHERE status = 'active'
            ORDER BY last_heartbeat DESC
            """
        ).fetchall()
        completed = conn.execute(
            """
            SELECT session_id, completed_work, files_locked, last_heartbeat
            FROM agent_sessions
            WHERE status = 'completed'
              AND date(last_heartbeat, 'localtime') = date('now', 'localtime')
            ORDER BY last_heartbeat DESC
            """
        ).fetchall()
    except Exception:
        if close_conn:
            conn.close()
        return False

    locked_map = {}
    for row in active:
        files = _json_list(row["files_locked"])
        for fp in files:
            locked_map[fp] = row["session_id"]

    lines = [
        "# Agent Coordination — Live Status",
        "",
        "## ACTIVE SESSIONS",
        "| Session | Device | Agent Type | Working On | Files Locked | Started | Last Update |",
        "|---------|--------|------------|------------|--------------|---------|-------------|",
    ]

    if active:
        for row in active:
            files = ", ".join(_json_list(row["files_locked"])) or "(none)"
            lines.append(
                f"| {row['session_id']} | {row['device'] or '-'} | {row['agent_type'] or '-'} | "
                f"{row['working_on'] or '-'} | {files} | {row['started_at'] or '-'} | {row['last_heartbeat'] or '-'} |"
            )
    else:
        lines.append("| (none) | - | - | - | - | - | - |")

    lines.extend([
        "",
        "## COMPLETED TODAY",
        "| Session | What | Files Changed | Time |",
        "|---------|------|---------------|------|",
    ])
    if completed:
        for row in completed:
            work_items = _json_list(row["completed_work"])
            files = _json_list(row["files_locked"])
            lines.append(
                f"| {row['session_id']} | {'; '.join(work_items) or '-'} | {', '.join(files) or '-'} | {row['last_heartbeat'] or '-'} |"
            )
    else:
        lines.append("| (none) | - | - | - |")

    lines.extend([
        "",
        "## FILE OWNERSHIP (DO NOT TOUCH)",
        "Files currently being modified by another agent — hands off:",
    ])
    if locked_map:
        for fp, owner in sorted(locked_map.items()):
            lines.append(f"- {fp} -> {owner}")
    else:
        lines.append("- (none)")

    lines.extend([
        "",
        "## RULES",
        "1. Before modifying ANY file, check this doc. If another agent owns it, skip.",
        "2. When starting work: add your row to ACTIVE SESSIONS.",
        "3. When done: move to COMPLETED TODAY, release file ownership.",
        "4. Read this file at the START of every conversation and after every major task.",
        "5. Run `scripts/coord_context.sh <session_id>` at session start to load live DB + coordination context.",
    ])

    AGENTS_MD_PATH.write_text("\n".join(lines).strip() + "\n")
    if close_conn:
        conn.close()
    return True


async def broadcast(event: dict):
    _session_events.append(event)
    if len(_session_events) > 200: _session_events.pop(0)
    dead = []
    for q in _sse_clients:
        try: q.put_nowait(event)
        except: dead.append(q)
    for q in dead:
        try: _sse_clients.remove(q)
        except: pass

def get_orch():
    global _orch
    from core.orchestrator import Orchestrator
    from core.providers import LLMProvider
    _orch = Orchestrator(LLMProvider(load_config()))
    return _orch

async def run_task(message: str, history=None, source="browser"):
    orch = get_orch()
    final_text = ""
    await broadcast({"type": "task_start", "message": message, "source": source})
    async for ev in orch.run(message, history or []):
        if ev.get("type") in ("direct_response", "synthesis"):
            final_text = ev.get("content", "")
        await broadcast(ev)
    await broadcast({"type": "done"})
    return final_text

async def handle_telegram(chat_id, text):
    from telegram.bot import get_bot
    bot = get_bot()
    t0 = time.time()
    result = await run_task(text, source="telegram")
    duration = time.time() - t0
    if bot:
        if result:
            preview = result[:3500] + ("…" if len(result) > 3500 else "")
            await bot.send(chat_id, f"✅ *Done* ({duration:.0f}s)\n\n{preview}")
        else:
            await bot.send(chat_id, "⚠️ Task completed with no result.")

async def _handle_approval(chat_id: str, approval_id: int, action: str, custom_text: str = None):
    """Handle Kai's approval/skip/edit/stop response from Telegram."""
    from telegram.bot import get_bot
    from core.approval_queue import approve_message, skip_message, stop_sequence, get_pending
    from core.lead_pipeline import get_pipeline

    bot = get_bot()
    pipeline = get_pipeline()
    if not bot or not pipeline:
        return

    approval = get_pending(approval_id)
    if not approval:
        await bot.send(chat_id, f"⚠️ Approval #{approval_id} not found or already processed.")
        return

    if approval["status"] != "pending":
        await bot.send(chat_id, f"ℹ️ Approval #{approval_id} already {approval['status']}.")
        return

    if action == "approve":
        result = approve_message(approval_id)
        if result:
            send_result = await pipeline.execute_approved_message(result)
            if send_result.get("ok"):
                await bot.send(chat_id,
                    f"✅ Message sent to {result['lead_name']} via {result['channel'].upper()}!")
            else:
                await bot.send(chat_id,
                    f"⚠️ Approved but send failed: {send_result.get('error', 'unknown')}\n"
                    f"The message was approved — will retry on next cycle.")

    elif action == "edit":
        result = approve_message(approval_id, custom_text=custom_text)
        if result:
            send_result = await pipeline.execute_approved_message(result)
            if send_result.get("ok"):
                await bot.send(chat_id,
                    f"✅ Your custom message sent to {result['lead_name']} via {result['channel'].upper()}!")
            else:
                await bot.send(chat_id,
                    f"⚠️ Send failed: {send_result.get('error', 'unknown')}")

    elif action == "skip":
        result = skip_message(approval_id)
        if result:
            await bot.send(chat_id, f"⏭ Skipped message to {result['lead_name']}.")

    elif action == "stop":
        count = stop_sequence(approval["lead_id"])
        await bot.send(chat_id,
            f"🛑 Follow-up sequence stopped for lead #{approval['lead_id']}. "
            f"({count} pending messages cancelled)")


async def _handle_reply_to_lead(chat_id: str, lead_id: int, channel: str, message_text: str):
    """Forward Kai's Telegram reply to a lead."""
    from telegram.bot import get_bot
    from core.lead_pipeline import get_pipeline

    bot = get_bot()
    pipeline = get_pipeline()
    if not bot or not pipeline:
        return

    result = await pipeline.reply_to_lead(lead_id, channel, message_text)
    if result.get("ok"):
        lead = pipeline.get_lead(lead_id)
        name = f"{lead['first_name']} {lead['last_name']}".strip() if lead else f"Lead #{lead_id}"
        await bot.send(chat_id, f"✅ Reply sent to {name} via {channel.upper()}!")
    else:
        await bot.send(chat_id, f"⚠️ Reply failed: {result.get('error', 'unknown')}")


async def _handle_b2b_approval(chat_id: str, lead_id: int, action: str):
    """Handle B2B email approve/deny from Telegram inline buttons."""
    from telegram.bot import get_bot
    from core.b2b_leads import get_lead, mark_sent, mark_denied
    from core.cold_email import generate_cold_email
    from integrations.messaging import get_messenger

    bot = get_bot()
    if not bot:
        return

    lead = get_lead(lead_id)
    if not lead:
        await bot.send(chat_id, f"⚠️ B2B lead #{lead_id} not found.")
        return

    if action == "b2b_approve":
        # Generate the email
        email_data = generate_cold_email(lead["business_name"])
        if "error" in email_data:
            await bot.send(chat_id, f"⚠️ Email generation failed: {email_data['error']}")
            return

        messenger = get_messenger()
        if not messenger:
            await bot.send(chat_id, "⚠️ Messenger not initialized. Check Gmail config.")
            return

        # Create an approval record so SecurityGate can verify it
        try:
            import sqlite3 as _sql3
            from pathlib import Path as _P
            db = _sql3.connect(str(_P.home() / ".nexus" / "memory.db"))
            cursor = db.execute(
                "INSERT INTO message_approvals (lead_id, channel, status, approval_method, "
                "message_body, created_at) VALUES (?, 'email', 'approved', 'kai_approved', ?, datetime('now'))",
                (lead_id, email_data["plain_body"][:500])
            )
            approval_id = cursor.lastrowid
            db.commit()
            db.close()
        except Exception as e:
            print(f"[B2B] Approval record error: {e}")
            approval_id = None

        # Send the email
        result = await messenger.send_b2b_email(
            to_email=lead["email"],
            subject=email_data["subject"],
            plain_body=email_data["plain_body"],
            html_body=email_data["html_body"],
            business_name=lead["business_name"],
            approval_id=approval_id,
        )

        if result.get("ok"):
            mark_sent(lead_id, email_data["plain_body"])
            await bot.send(chat_id,
                f"✅ *B2B Email SENT*\n"
                f"To: {lead['business_name']} ({lead['email']})\n"
                f"Subject: {email_data['subject']}")
        else:
            await bot.send(chat_id,
                f"⚠️ *B2B Send FAILED*\n"
                f"To: {lead['business_name']}\n"
                f"Error: {result.get('error', 'unknown')}")

    elif action == "b2b_deny":
        mark_denied(lead_id)
        await bot.send(chat_id,
            f"❌ *B2B Email DENIED*\n"
            f"{lead['business_name']} — logged and will not be re-sent.")


async def _handle_b2b_reply(reply: dict):
    """Handle a B2B lead replying to our cold email. Notifies via Telegram."""
    b2b_lead_id = reply.get("b2b_lead_id")
    if not b2b_lead_id:
        return

    from telegram.bot import get_bot
    from core.b2b_leads import mark_replied, mark_unsubscribed, get_lead

    bot = get_bot()
    lead = get_lead(b2b_lead_id)
    if not bot or not lead:
        return

    body = reply.get("body", "")

    # Auto-detect unsubscribe
    if "unsubscribe" in body.lower():
        mark_unsubscribed(b2b_lead_id)
        for cid in bot.allowed:
            await bot.send(cid,
                f"🔴 *B2B UNSUBSCRIBE*\n"
                f"{lead['business_name']} replied with unsubscribe.\n"
                f"Marked as do\\_not\\_contact automatically.")
        return

    # Regular reply — notify Kai
    mark_replied(b2b_lead_id, body[:2000])
    for cid in bot.allowed:
        await bot.send(cid,
            f"💬 *B2B REPLY RECEIVED*\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"*From:* {lead['business_name']}\n"
            f"*Email:* {reply.get('from_email', '')}\n"
            f"*Subject:* {reply.get('subject', '')}\n\n"
            f"*Message:*\n{body[:1000]}\n\n"
            f"Reply to this lead from Gmail directly.")

async def _ensure_startup_bootstrapped(reason: str = "lazy") -> bool:
    global _STARTUP_LOCK, _STARTUP_COMPLETE
    if _STARTUP_COMPLETE:
        return False
    if _STARTUP_LOCK is None:
        _STARTUP_LOCK = asyncio.Lock()
    async with _STARTUP_LOCK:
        if _STARTUP_COMPLETE:
            return False
        print(f"[Nexus] Startup bootstrap triggered ({reason})")
        await _run_startup(reason=reason)
        _STARTUP_COMPLETE = True
        return True


async def _ensure_crm_alert_runtime(reason: str = "crm_alert") -> bool:
    global _CRM_BOOTSTRAP_LOCK
    from core.lead_pipeline import get_pipeline
    from core.services import get_lead_service, init_services
    from integrations.messaging import get_messenger
    from telegram.bot import get_bot, init_bot

    pipeline = get_pipeline()
    if get_lead_service() and get_messenger() and pipeline and getattr(pipeline, "_running", False):
        return False

    if _CRM_BOOTSTRAP_LOCK is None:
        _CRM_BOOTSTRAP_LOCK = asyncio.Lock()

    async with _CRM_BOOTSTRAP_LOCK:
        pipeline = get_pipeline()
        if get_lead_service() and get_messenger() and pipeline and getattr(pipeline, "_running", False):
            return False

        cfg = load_config()
        print(f"[Nexus] CRM alert bootstrap triggered ({reason})")

        bot = get_bot()
        tok = cfg.get("telegram_token", "")
        chats = cfg.get("telegram_chat_ids", [])
        if not bot and tok and chats:
            bot = init_bot(tok, chats)
            bot.set_handler(handle_telegram)
            bot.set_approval_handler(_handle_approval)
            bot.set_reply_handler(_handle_reply_to_lead)
            bot.set_b2b_approval_handler(_handle_b2b_approval)
            asyncio.create_task(bot.start_polling())
            print("[Nexus] Telegram bot started (CRM alert bootstrap)")

        if not get_messenger():
            _start_messenger(cfg)

        if not get_lead_service():
            notify_fn = bot.send_to_all if bot else None
            init_services(notify_fn=notify_fn, broadcast_fn=broadcast)
            print("[Nexus] CRM services initialized (CRM alert bootstrap)")

        pipeline = get_pipeline()
        if not pipeline or not getattr(pipeline, "_running", False):
            await _start_lead_pipeline(cfg)
        return True


@app.on_event("startup")
async def startup():
    await _ensure_startup_bootstrapped(reason="lifespan")


async def _run_startup(reason: str = "lifespan"):
    cfg = load_config()
    # Security Gate — initialize first (other modules depend on it)
    try:
        from core.security import get_security_gate
        gate = get_security_gate()
        mode = cfg.get("messaging_mode", "disabled")
        print(f"[Nexus] SecurityGate initialized: mode={mode}, "
              f"kill_switch={'ACTIVE' if gate.is_kill_switch_active() else 'inactive'}, "
              f"dead_man={'confirmed' if gate.is_dead_man_confirmed() else 'expired'}")
    except Exception as e:
        print(f"[Nexus] SecurityGate init: {e}")
    # CAPI Events module (higher-level business events)
    try:
        from integrations.meta_capi_events import init_capi_events
        init_capi_events()
    except Exception as e:
        print(f"[Nexus] CAPI Events init: {e}")
    # Telegram
    tok = cfg.get("telegram_token", ""); chats = cfg.get("telegram_chat_ids", [])
    if tok and chats:
        from telegram.bot import init_bot
        bot = init_bot(tok, chats)
        bot.set_handler(handle_telegram)
        bot.set_approval_handler(_handle_approval)
        bot.set_reply_handler(_handle_reply_to_lead)
        bot.set_b2b_approval_handler(_handle_b2b_approval)
        asyncio.create_task(bot.start_polling())
        # Approval reminder loop DISABLED — it was spamming Telegram
        # from telegram.bot import run_approval_reminders
        # asyncio.create_task(run_approval_reminders())
        print("[Nexus] Telegram bot started (approval workflow active, B2B approval active, reminders DISABLED)")
    # Messaging (Gmail)
    _start_messenger(cfg)
    # B2B Email System — migration + lead import
    try:
        from core.db_migrate import run_migrations
        run_migrations()
        from core.b2b_import import import_leads
        result = import_leads()
        print(f"[Nexus] B2B leads: {result['total']} in database ({result['inserted']} new)")
    except Exception as e:
        print(f"[Nexus] B2B init: {e}")
    # Seed SMS/email templates
    try:
        from core.seed_templates import seed_templates
        seed_templates()
    except Exception as e:
        print(f"[Nexus] Template seed: {e}")
    # Meta Conversions API (CAPI)
    try:
        from integrations.meta_capi import init_capi
        init_capi(cfg)
    except Exception as e:
        print(f"[Nexus] CAPI init: {e}")
    # Facebook Page manager
    try:
        from integrations.fb_page import init_fb_page
        init_fb_page(cfg)
    except Exception as e:
        print(f"[Nexus] FB Page init: {e}")
    # Posting scheduler — DISABLED (re-enable after Slack configured)
    # try:
    #     from integrations.posting_scheduler import init_scheduler, run_scheduler
    #     ...
    # except Exception as e:
    #     print(f"[Nexus] PostScheduler init: {e}")

    # Daily posting engine — generates marketplace listings at 8 AM PT
    try:
        from core.daily_posting import init_daily_posting, run_daily_posting
        from telegram.bot import get_bot as _dp_bot
        dp_bot = _dp_bot()
        dp_send_fn = dp_bot.send_to_all if dp_bot else None
        init_daily_posting(dp_send_fn)
        asyncio.create_task(run_daily_posting())
        print("[Nexus] Daily posting: Scheduler ENABLED (8 AM PT)")
    except Exception as e:
        print(f"[Nexus] DailyPosting init: {e}")

    # Facebook comment monitor — DISABLED
    # try:
    #     from integrations.fb_comment_monitor import init_comment_monitor, run_comment_monitor
    #     ...
    # except Exception as e:
    #     print(f"[Nexus] CommentMonitor init: {e}")

    # ── Agent Coordination: register server as active session ──
    try:
        import sqlite3 as _sq
        _db = _sq.connect(str(Path.home() / ".nexus" / "memory.db"))
        _db.execute("""CREATE TABLE IF NOT EXISTS agent_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL, device TEXT, agent_type TEXT,
            working_on TEXT, files_locked TEXT DEFAULT '[]',
            status TEXT DEFAULT 'active',
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_heartbeat TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_work TEXT DEFAULT '[]')""")
        import socket as _sock
        _dev = _sock.gethostname()
        _db.execute(
            "INSERT INTO agent_sessions (session_id, device, agent_type, working_on, status) VALUES (?,?,?,?,?)",
            ("nexus-server", _dev, "server", "FastAPI server running on port 7860", "active"))
        _db.commit()
        _db.close()
        print("[Nexus] Coordination: server session registered")
    except Exception as e:
        print(f"[Nexus] Coordination init: {e}")

    # ── DISABLED BACKGROUND SCHEDULERS (Phase 2 — re-enable when needed) ──

    # Personal Assistant — DB only (no morning/evening spam)
    try:
        from core.personal_assistant import init_personal_assistant_db
        init_personal_assistant_db()
        print("[Nexus] Personal assistant: DB ready (scheduler DISABLED)")
    except Exception as e:
        print(f"[Nexus] PersonalAssistant init: {e}")

    # Knowledge Engine — DB only
    try:
        from core.knowledge_engine import init_knowledge_db
        init_knowledge_db()
        print("[Nexus] Knowledge engine: DB ready (scheduler DISABLED)")
    except Exception as e:
        print(f"[Nexus] KnowledgeEngine init: {e}")

    # Quote/Invoice Generator (Phase 7/11)
    try:
        from core.quote_generator import init_quote_db
        init_quote_db()
        print("[Nexus] Quote/invoice generator ready")
    except Exception as e:
        print(f"[Nexus] QuoteGenerator init: {e}")

    # Instagram Auto-Posting — DISABLED until IG Business Account is linked
    # To re-enable: link Instagram in FB Page Settings, add instagram_business_account_id to config
    try:
        from integrations.instagram_api import init_instagram_db
        init_instagram_db()  # Keep DB tables but do NOT start scheduler
        print("[Nexus] Instagram: DB ready (scheduler DISABLED — no IG Business Account linked)")
    except Exception as e:
        print(f"[Nexus] Instagram init: {e}")

    # Ad Performance Monitor (Agent 3)
    try:
        from core.ad_monitor import init_ad_monitor_db, run_ad_monitor
        init_ad_monitor_db()
        from telegram.bot import get_bot as _ad_bot
        ad_bot = _ad_bot()
        ad_send_fn = ad_bot.send_to_all if ad_bot else None
        if ad_send_fn:
            asyncio.create_task(run_ad_monitor(ad_send_fn))
            print("[Nexus] Ad Monitor: Scheduler ENABLED (every 6 hours)")
        else:
            print("[Nexus] Ad Monitor: DB ready (no Telegram bot for alerts)")
    except Exception as e:
        print(f"[Nexus] AdMonitor init: {e}")

    # Watchdog Timer System — ONLY monitor 3 essential services
    try:
        from core.watchdog import run_watchdog, register_service
        register_service("server")
        register_service("telegram_bot")
        register_service("lead_pipeline")
        from telegram.bot import get_bot as _get_wd_bot
        wd_bot = _get_wd_bot()
        if wd_bot:
            asyncio.create_task(run_watchdog(wd_bot.send_to_all))
            print("[Nexus] Watchdog started (3 essential services only: server, telegram, pipeline)")
    except Exception as e:
        print(f"[Nexus] Watchdog init: {e}")

    # Health Scanner — enables D4 self-healing division
    try:
        from core.health_scanner import get_health_scanner
        get_health_scanner()  # Initialize singleton so D4 can use it
        print("[Nexus] Health scanner initialized (D4 self-healing ready)")
    except Exception as e:
        print(f"[Nexus] Health scanner init: {e}")

    # ── DISABLED SERVICES (Phase 2 — re-enable after core is stable) ──────────
    # These init their DB tables but do NOT start background schedulers.
    # This prevents Telegram spam from non-essential services.

    # Google Calendar Integration — DB only
    try:
        from integrations.google_calendar import init_calendar_db
        init_calendar_db()
        print("[Nexus] Calendar: DB ready (reminders DISABLED)")
    except Exception as e:
        print(f"[Nexus] Calendar init: {e}")

    # Booking Confirmation System — DB only
    try:
        from core.booking_system import init_booking_system_db
        init_booking_system_db()
        print("[Nexus] Booking: DB ready (cold lead check DISABLED)")
    except Exception as e:
        print(f"[Nexus] BookingSystem init: {e}")

    # Email Lead Parser — DB only (re-enable after lead pipeline stable)
    try:
        from integrations.email_lead_parser import init_email_parser_db
        init_email_parser_db()
        print("[Nexus] EmailParser: DB ready (polling DISABLED)")
    except Exception as e:
        print(f"[Nexus] EmailParser init: {e}")

    # Review Generation System — DB only
    try:
        from core.review_system import init_review_system
        init_review_system()
        print("[Nexus] Reviews: DB ready (scheduler DISABLED)")
    except Exception as e:
        print(f"[Nexus] ReviewSystem init: {e}")

    # Analytics Dashboard — DB only
    try:
        from core.analytics_dashboard import init_analytics_db
        init_analytics_db()
        print("[Nexus] Analytics: DB ready (scheduler DISABLED)")
    except Exception as e:
        print(f"[Nexus] Analytics init: {e}")

    # Meta CAPI Events (Phase 5) — Purchase, Schedule, Lead, InitiateCheckout tracking
    try:
        from integrations.meta_capi_events import init_capi_events
        init_capi_events()
        print("[Nexus] Meta CAPI events module initialized")
    except Exception as e:
        print(f"[Nexus] CAPI Events init: {e}")

    # GBP Auto-Poster — DB only
    try:
        from integrations.google_business import init_gbp_db
        init_gbp_db()
        print("[Nexus] GBP: DB ready (auto-poster DISABLED)")
    except Exception as e:
        print(f"[Nexus] GBP init: {e}")

    # Competitor Monitor — DB only
    try:
        from core.competitor_monitor import init_competitor_db
        init_competitor_db()
        print("[Nexus] Competitor: DB ready (scanner DISABLED)")
    except Exception as e:
        print(f"[Nexus] Competitor monitor init: {e}")

    # Facebook Messenger Integration (Phase 6) — DM routing via Telegram
    try:
        from integrations.fb_messenger import (
            init_fb_messenger, get_webhook_verify_handler, get_webhook_handler,
        )
        err = init_fb_messenger()
        if not err:
            from telegram.bot import get_bot as _get_msg_bot
            msg_bot = _get_msg_bot()
            send_fn = msg_bot.send_to_all if msg_bot else None

            async def _messenger_approval(psid, text):
                """Queue messenger reply through approval system."""
                try:
                    from core.approval_queue import queue_approval
                    return queue_approval("messenger_reply", {
                        "psid": psid, "text": text, "channel": "messenger",
                    })
                except Exception:
                    return 0

            app.add_api_route("/api/messenger/webhook",
                              get_webhook_verify_handler(), methods=["GET"])
            app.add_api_route("/api/messenger/webhook",
                              get_webhook_handler(send_fn, _messenger_approval), methods=["POST"])
            print("[Nexus] FB Messenger webhook registered at /api/messenger/webhook")
        else:
            print(f"[Nexus] FB Messenger: {err}")
    except Exception as e:
        print(f"[Nexus] FB Messenger init: {e}")

    # SEO Tools (Phase 6) — Sitemap, schemas, city landing pages
    try:
        import core.seo_tools  # _init_db() runs on import
        print("[Nexus] SEO tools loaded (25 cities, sitemap, schemas)")
    except Exception as e:
        print(f"[Nexus] SEO tools init: {e}")

    # Retargeting — DB only
    try:
        from core.retargeting import init_retargeting_db
        init_retargeting_db()
        print("[Nexus] Retargeting: DB ready (scheduler DISABLED)")
    except Exception as e:
        print(f"[Nexus] Retargeting init: {e}")

    # Re-engagement — DB only
    try:
        from core.reengagement import init_reengagement
        init_reengagement()
        print("[Nexus] Re-engagement: DB ready (scheduler DISABLED)")
    except Exception as e:
        print(f"[Nexus] Re-engagement init: {e}")

    # Media Manager (Phase 7) — Photo/video catalog for leads and marketing
    try:
        from core.media_manager import init_media_db
        init_media_db()
        print("[Nexus] Media manager initialized")
    except Exception as e:
        print(f"[Nexus] Media manager init: {e}")

    # ── NEW BUILDS (Wave 6) ──────────────────────────────────────────────────

    # Approval System — DB only, NO reminder scheduler (it spams Telegram)
    try:
        from core.approval_system import init_approval
        init_approval()
        from core.approval_queue import clear_stale_approvals
        clear_stale_approvals()
        print("[Nexus] Approval system: DB ready, stale approvals cleared")
    except Exception as e:
        print(f"[Nexus] Approval system init: {e}")

    # Posting Engine — DB only (re-enable after Slack is configured)
    try:
        from integrations.posting_engine import init_posting_engine
        init_posting_engine()
        print("[Nexus] Posting engine: DB ready (scheduler DISABLED)")
    except Exception as e:
        print(f"[Nexus] Posting engine init: {e}")

    # Video Generator — DB only
    try:
        from integrations.video_generator import init_video_db
        init_video_db()
        print("[Nexus] Video generator: DB ready (scheduler DISABLED)")
    except Exception as e:
        print(f"[Nexus] Video generator init: {e}")

    # FB Group Scraper / Lead Hunter (Agent 2)
    try:
        from integrations.fb_group_scraper import init_scraper_db, run_scraper_scheduler
        init_scraper_db()
        cfg_now = load_config()
        fb_scraper_disabled = bool(
            cfg_now.get("disable_facebook_scraper", False)
            or cfg_now.get("disable_browser_scrapers", False)
        )
        if fb_scraper_disabled:
            print("[Nexus] Group scraper: Scheduler DISABLED by config")
        else:
            from telegram.bot import get_bot as _scraper_bot
            scraper_bot = _scraper_bot()
            scraper_send_fn = scraper_bot.send_to_all if scraper_bot else None
            if scraper_send_fn:
                asyncio.create_task(run_scraper_scheduler(scraper_send_fn))
                print("[Nexus] Group scraper: Scheduler ENABLED (3x daily)")
            else:
                print("[Nexus] Group scraper: DB ready (no Telegram bot)")
    except Exception as e:
        print(f"[Nexus] Group scraper init: {e}")

    # Browser Agent — DB only
    try:
        from integrations.browser_agent import init_browser_db
        init_browser_db()
        print("[Nexus] Browser agent: DB ready")
    except Exception as e:
        print(f"[Nexus] Browser agent init: {e}")

    # Task Queue — DB only
    try:
        from core.task_queue import init_task_queue_db
        init_task_queue_db()
        print("[Nexus] Task queue: DB ready (scheduler DISABLED)")
    except Exception as e:
        print(f"[Nexus] Task queue init: {e}")

    # No non-essential watchdog registrations — only 3 core services monitored

    # CRM Services (Lead Service, Pipeline Service, etc.)
    try:
        from core.services import init_services
        from telegram.bot import get_bot as _get_svc_bot
        svc_bot = _get_svc_bot()
        notify_fn = svc_bot.send_to_all if svc_bot else None
        init_services(notify_fn=notify_fn, broadcast_fn=broadcast)
        print("[Nexus] CRM services initialized (LeadService, PipelineService)")
    except Exception as e:
        print(f"[Nexus] CRM services init error: {e}")

    # Ollama keepalive — prevent 55s cold starts
    try:
        from core.chat_handler import start_ollama_keepalive
        start_ollama_keepalive()
        print("[Nexus] Ollama keepalive started (every 4 min)")
    except Exception as e:
        print(f"[Nexus] Ollama keepalive init: {e}")

    # Do not block server bind on remote/browser integrations during startup.
    # If any initializer is slow or wedged, the API should still come up first.
    def schedule_startup_task(name, coro):
        async def runner():
            try:
                await coro
                print(f"[Nexus] Startup task ready: {name}")
            except Exception as e:
                print(f"[Nexus] Startup task failed ({name}): {e}")

        asyncio.create_task(runner())
        print(f"[Nexus] Startup task scheduled: {name}")

    # GHL + Lead Pipeline
    schedule_startup_task("ghl", _start_ghl(cfg))
    schedule_startup_task("lead_pipeline", _start_lead_pipeline(cfg))

def _start_messenger(cfg):
    gmail = cfg.get("gmail_address","")
    pw = cfg.get("gmail_app_password","")
    if gmail and pw:
        from integrations.messaging import init_messenger
        m = init_messenger(gmail, pw)
        # Respect production_mode from config — if True, disable test_mode
        # so messages actually send when Kai approves them
        if cfg.get("production_mode", False):
            m.test_mode = False
            print("[Nexus] Messaging (Gmail/SMS) ready — PRODUCTION MODE (sends to real contacts)")
        else:
            print("[Nexus] Messaging (Gmail/SMS) ready — TEST MODE (whitelist only)")

async def _start_ghl(cfg):
    from integrations.ghl import init_ghl
    from telegram.bot import get_bot
    callbacks = []
    bot = get_bot()
    if bot:
        async def tg_notify(msg):
            await bot.send_to_all(msg)
        callbacks.append(tg_notify)
    async def browser_notify(msg):
        await broadcast({"type": "ghl_event", "message": msg})
    callbacks.append(browser_notify)
    try:
        poller, err = await init_ghl(cfg, callbacks)
        if poller:
            asyncio.create_task(poller.run())
            print(f"[Nexus] GHL poller started (every {cfg.get('ghl_poll_interval',300)}s)")
        elif err:
            print(f"[Nexus] GHL init: {err}")
    except Exception as e:
        print(f"[Nexus] GHL init failed: {e}")

async def _start_lead_pipeline(cfg):
    """Initialize the lead pipeline, Google Voice, Twilio, IMAP — all background services."""
    from telegram.bot import get_bot
    from core.providers import LLMProvider

    # Build notify/broadcast functions
    bot = get_bot()
    async def notify(msg):
        if bot:
            await bot.send_to_all(msg)
    async def bcast(event):
        await broadcast(event)

    # Init pipeline
    from core.lead_pipeline import init_pipeline
    provider = LLMProvider(cfg)
    pipeline = init_pipeline(cfg, provider, notify, bcast)

    # Attach email sender (existing messenger)
    from integrations.messaging import get_messenger
    messenger = get_messenger()
    if messenger:
        pipeline.set_messenger(messenger)
        print("[Pipeline] Email sender attached")

    # Google Voice realtime ingest.
    # Auto-attach the poller at startup so inbound SMS appears in Conversations
    # without waiting for a manual endpoint call.
    gv_auto_start = str(cfg.get("gv_auto_start", "true")).lower() not in ("0", "false", "no", "off")
    if gv_auto_start and _is_port_bound(7860):
        print("[Pipeline] Google Voice auto-start skipped in secondary process (port 7860 already in use)")
        gv_auto_start = False
    if gv_auto_start and not _acquire_gv_owner_lock():
        print("[Pipeline] Google Voice auto-start skipped (GV owner lock held by another process)")
        gv_auto_start = False
    if gv_auto_start:
        try:
            gv = await _ensure_gv()
            if gv and gv.logged_in:
                print("[Pipeline] Google Voice realtime poller attached")
            elif gv:
                print("[Pipeline] Google Voice initialized but not logged in (run /api/browser/login-gv once)")
            else:
                print("[Pipeline] Google Voice unavailable (init failed)")
        except Exception as e:
            print(f"[Pipeline] Google Voice auto-start error: {e}")
    else:
        print("[Pipeline] Google Voice auto-start disabled (gv_auto_start=false)")

    # Init Twilio fallback
    try:
        from integrations.twilio_sms import init_twilio
        twilio = init_twilio(cfg)
        if twilio:
            pipeline.set_twilio(twilio)
            print("[Pipeline] Twilio fallback attached")
    except Exception as e:
        print(f"[Pipeline] Twilio init: {e}")

    # Init IMAP reply checker
    try:
        from integrations.gmail_imap import init_imap
        imap_poller, imap_err = init_imap(cfg)
        if imap_poller:
            account_email = (cfg.get("gmail_address", "") or "").strip().lower()

            async def on_email_reply(reply):
                # Check B2B leads first
                if reply.get("b2b_lead_id"):
                    await _handle_b2b_reply(reply)
                    return
                from_email = reply.get("from_email", "")
                lead_id = _ensure_inbound_email_lead(
                    pipeline,
                    from_email,
                    contact_name=reply.get("from_name", ""),
                )
                if lead_id:
                    await pipeline.process_reply(lead_id, "email", reply.get("body", ""), from_email)
                    await broadcast({
                        "type": "msg_received",
                        "channel": "email",
                        "lead_id": int(lead_id),
                        "from": from_email,
                        "preview": (reply.get("body", "") or "")[:80],
                        "timestamp": reply.get("timestamp", ""),
                    })
                else:
                    await notify(f"Email from unknown: {from_email}\n_{reply.get('body', '')[:100]}_")

            async def on_email_sent(sent):
                to_email = (sent.get("to_email", "") or "").strip().lower()
                if not to_email or (account_email and to_email == account_email):
                    return
                stats = _sync_gmail_messages_to_leads(pipeline, account_email, [{
                    "direction": "outbound",
                    "from_email": account_email,
                    "to_emails": [to_email],
                    "subject": sent.get("subject", ""),
                    "content": sent.get("body", ""),
                    "timestamp": sent.get("timestamp", ""),
                }])
                if stats.get("imported", 0) > 0:
                    await broadcast({
                        "type": "msg_sent",
                        "channel": "email",
                        "to": to_email,
                        "preview": (sent.get("subject") or sent.get("body", ""))[:80],
                    })

            imap_poller.on_reply(on_email_reply)
            imap_poller.on_sent(on_email_sent)
            asyncio.create_task(imap_poller.run())
            print("[Pipeline] IMAP sync started (replies + sent mail)")
        elif imap_err:
            print(f"[Pipeline] IMAP: {imap_err}")
    except Exception as e:
        print(f"[Pipeline] IMAP init: {e}")

    # Google Sheet CRM sync (Facebook Lead Ads Form CRM -> Nexus leads)
    try:
        await _start_google_sheet_crm_sync(cfg)
    except Exception as e:
        print(f"[Pipeline] Google Sheet CRM init: {e}")

    # Start the pipeline scheduler
    asyncio.create_task(pipeline.run_scheduler())
    print("[Pipeline] Lead pipeline scheduler started")

    # Check for pending approvals from before shutdown
    try:
        from core.approval_queue import get_unnotified_pending, get_all_pending
        unnotified = get_unnotified_pending()
        all_pending = get_all_pending()
        if unnotified:
            for u in unnotified:
                await pipeline._send_approval_notification(u["id"], u["message_type"])
            print(f"[Pipeline] Re-sent {len(unnotified)} unnotified approvals from before shutdown")
        if all_pending:
            # Dedup guard: only send "Nexus restarted" notification if we haven't
            # sent one in the last 5 minutes (prevents spam from crash-loops or
            # duplicate launchd agents)
            import time as _time
            _restart_flag = Path.home() / ".nexus" / ".last_restart_notify"
            _should_notify = True
            try:
                if _restart_flag.exists():
                    age = _time.time() - _restart_flag.stat().st_mtime
                    if age < 300:  # 5 minutes
                        _should_notify = False
                        print(f"[Pipeline] Skipping restart notification (sent {age:.0f}s ago)")
            except Exception:
                pass
            if bot and _should_notify:
                # Do not block server startup on Telegram network latency.
                # If Telegram is slow/down, startup must still complete and bind port.
                try:
                    await asyncio.wait_for(
                        bot.send_to_all(
                            f"🔄 *Nexus restarted* — {len(all_pending)} pending approval(s) in queue.\n"
                            f"Use /queue to review them."
                        ),
                        timeout=6,
                    )
                except Exception as te:
                    print(f"[Pipeline] Restart notification skipped: {te}")
                try:
                    _restart_flag.parent.mkdir(parents=True, exist_ok=True)
                    _restart_flag.write_text(str(_time.time()))
                except Exception:
                    pass
    except Exception as e:
        print(f"[Pipeline] Startup approval check: {e}")

    # Start Follow-Up Engine (template-based, approval-only sequences)
    try:
        from core.follow_up_engine import get_follow_up_engine
        fu_engine = get_follow_up_engine()
        print(f"[Pipeline] Follow-up engine ready (approval-only, never sends directly)")
    except Exception as e:
        print(f"[Pipeline] Follow-up engine init: {e}")

    # Start Database Backup scheduler
    try:
        from core.db_backup import start_backup_scheduler
        asyncio.create_task(start_backup_scheduler())
        print("[Pipeline] Database backup scheduler started")
    except Exception as e:
        print(f"[Pipeline] Backup scheduler init: {e}")

    # Startup catch-up routine (process missed leads, expired approvals, etc.)
    try:
        from core.startup_catchup import run_startup_catchup, run_heartbeat_loop
        asyncio.create_task(run_startup_catchup())
        asyncio.create_task(run_heartbeat_loop())
        print("[Nexus] Startup catch-up routine scheduled")
    except Exception as e:
        print(f"[Nexus] Startup catch-up error: {e}")

    # Master Coordinator — autonomous work engine
    try:
        from core.master_coordinator import get_coordinator
        asyncio.create_task(get_coordinator().run_loop())
        print("[Coordinator] Master Coordinator started")
    except Exception as e:
        print(f"[Coordinator] Startup error: {e}")

# ── Multi-Agent Coordination API ───────────────────────────────────────────────
@app.post("/api/coordination/checkin", dependencies=[Depends(verify_action_auth)])
async def coordination_checkin(request: Request):
    import sqlite3 as _sql3

    body = await request.json()
    session_id = str(body.get("session_id", "")).strip()
    if not session_id:
        return JSONResponse({"status": "error", "error": "session_id is required"}, status_code=400)

    device = str(body.get("device", "")).strip()
    agent_type = str(body.get("agent_type", "")).strip()
    working_on = str(body.get("working_on", "")).strip()
    files_locked = _json_list(body.get("files_locked", []))

    db = _coord_db_path()
    conn = _sql3.connect(str(db))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                device TEXT DEFAULT '',
                agent_type TEXT DEFAULT '',
                working_on TEXT DEFAULT '',
                files_locked TEXT DEFAULT '[]',
                status TEXT DEFAULT 'active',
                started_at TEXT DEFAULT (datetime('now')),
                last_heartbeat TEXT DEFAULT (datetime('now')),
                completed_work TEXT DEFAULT '[]'
            )
            """
        )
        _cleanup_stale_agent_sessions(conn)
        current = conn.execute(
            "SELECT id FROM agent_sessions WHERE session_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()

        if current:
            conn.execute(
                """
                UPDATE agent_sessions
                SET device = ?, agent_type = ?, working_on = ?, files_locked = ?, status = 'active',
                    last_heartbeat = datetime('now')
                WHERE id = ?
                """,
                (device, agent_type, working_on, json.dumps(files_locked), current[0]),
            )
        else:
            conn.execute(
                """
                INSERT INTO agent_sessions (
                    session_id, device, agent_type, working_on, files_locked, status
                ) VALUES (?, ?, ?, ?, ?, 'active')
                """,
                (session_id, device, agent_type, working_on, json.dumps(files_locked)),
            )
        conn.commit()
        sync_agents_md(conn)
        active_count = conn.execute(
            "SELECT COUNT(*) FROM agent_sessions WHERE status = 'active'"
        ).fetchone()[0]
    except Exception as e:
        conn.close()
        return JSONResponse({"status": "error", "error": str(e)}, status_code=500)
    conn.close()
    return JSONResponse({"status": "ok", "session_id": session_id, "active_sessions": active_count})


@app.post("/api/coordination/checkout", dependencies=[Depends(verify_action_auth)])
async def coordination_checkout(request: Request):
    import sqlite3 as _sql3

    body = await request.json()
    session_id = str(body.get("session_id", "")).strip()
    if not session_id:
        return JSONResponse({"status": "error", "error": "session_id is required"}, status_code=400)

    status = str(body.get("status", "completed")).strip().lower()
    if status not in {"completed", "idle"}:
        status = "completed"

    completed_work = _json_list(body.get("completed_work", []))
    db = _coord_db_path()
    conn = _sql3.connect(str(db))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                device TEXT DEFAULT '',
                agent_type TEXT DEFAULT '',
                working_on TEXT DEFAULT '',
                files_locked TEXT DEFAULT '[]',
                status TEXT DEFAULT 'active',
                started_at TEXT DEFAULT (datetime('now')),
                last_heartbeat TEXT DEFAULT (datetime('now')),
                completed_work TEXT DEFAULT '[]'
            )
            """
        )
        row = conn.execute(
            "SELECT id, completed_work, files_locked FROM agent_sessions WHERE session_id = ? ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if not row:
            conn.close()
            return JSONResponse({"status": "error", "error": "session not found"}, status_code=404)

        existing = _json_list(row[1] or "[]")
        prior_files = _json_list(row[2] or "[]")
        files_changed = _json_list(body.get("files_changed", [])) or prior_files
        merged = existing + completed_work
        conn.execute(
            """
            UPDATE agent_sessions
            SET status = ?, working_on = '', files_locked = ?, completed_work = ?, last_heartbeat = datetime('now')
            WHERE id = ?
            """,
            (status, json.dumps(files_changed), json.dumps(merged), row[0]),
        )
        conn.commit()
        sync_agents_md(conn)
    except Exception as e:
        conn.close()
        return JSONResponse({"status": "error", "error": str(e)}, status_code=500)
    conn.close()
    return JSONResponse({"status": "ok", "session_id": session_id, "new_status": status})


@app.post("/api/coordination/heartbeat", dependencies=[Depends(verify_action_auth)])
async def coordination_heartbeat(request: Request):
    import sqlite3 as _sql3

    body = await request.json()
    session_id = str(body.get("session_id", "")).strip()
    if not session_id:
        return JSONResponse({"status": "error", "error": "session_id is required"}, status_code=400)

    working_on = str(body.get("working_on", "")).strip()
    device = str(body.get("device", "")).strip()
    agent_type = str(body.get("agent_type", "")).strip()
    has_files = "files_locked" in body
    files_locked = _json_list(body.get("files_locked", []))

    db = _coord_db_path()
    conn = _sql3.connect(str(db))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                device TEXT DEFAULT '',
                agent_type TEXT DEFAULT '',
                working_on TEXT DEFAULT '',
                files_locked TEXT DEFAULT '[]',
                status TEXT DEFAULT 'active',
                started_at TEXT DEFAULT (datetime('now')),
                last_heartbeat TEXT DEFAULT (datetime('now')),
                completed_work TEXT DEFAULT '[]'
            )
            """
        )
        _cleanup_stale_agent_sessions(conn)
        row = conn.execute(
            "SELECT id, device, agent_type, working_on, files_locked FROM agent_sessions WHERE session_id = ? AND status = 'active' ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row:
            next_device = device or (row[1] or "")
            next_agent_type = agent_type or (row[2] or "")
            next_working_on = working_on or (row[3] or "")
            next_files = files_locked if has_files else _json_list(row[4] or "[]")
            conn.execute(
                """
                UPDATE agent_sessions
                SET device = ?, agent_type = ?, working_on = ?, files_locked = ?, last_heartbeat = datetime('now')
                WHERE id = ?
                """,
                (next_device, next_agent_type, next_working_on, json.dumps(next_files), row[0]),
            )
        else:
            conn.execute(
                """
                INSERT INTO agent_sessions (session_id, device, agent_type, working_on, files_locked, status)
                VALUES (?, ?, ?, ?, ?, 'active')
                """,
                (session_id, device, agent_type, working_on, json.dumps(files_locked)),
            )
        conn.commit()
        sync_agents_md(conn)
    except Exception as e:
        conn.close()
        return JSONResponse({"status": "error", "error": str(e)}, status_code=500)
    conn.close()
    return JSONResponse({"status": "ok", "session_id": session_id, "heartbeat_at": datetime.utcnow().isoformat() + "Z"})


@app.post("/api/coordination/log", dependencies=[Depends(verify_action_auth)])
async def coordination_log_append(request: Request):
    import sqlite3 as _sql3

    body = await request.json()
    session_id = str(body.get("session_id", "")).strip()
    summary = str(body.get("summary", "")).strip()
    if not session_id or not summary:
        return JSONResponse({"status": "error", "error": "session_id and summary are required"}, status_code=400)

    device = str(body.get("device", "")).strip()
    agent_type = str(body.get("agent_type", "")).strip()
    entry_kind = str(body.get("entry_kind", "update") or "update").strip().lower()
    if entry_kind not in {"update", "completion", "handoff", "note"}:
        entry_kind = "update"
    files_changed = _json_list(body.get("files_changed", []))

    db = _coord_db_path()
    conn = _sql3.connect(str(db))
    conn.row_factory = _sql3.Row
    try:
        _ensure_agent_activity_table(conn)
        if not device or not agent_type:
            row = conn.execute(
                """
                SELECT device, agent_type
                FROM agent_sessions
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
            if row:
                device = device or str(row["device"] or "")
                agent_type = agent_type or str(row["agent_type"] or "")

        conn.execute(
            """
            INSERT INTO agent_activity_log (
                session_id, device, agent_type, entry_kind, summary, files_changed, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
            """,
            (
                session_id,
                device,
                agent_type,
                entry_kind,
                summary[:1000],
                json.dumps(files_changed),
            ),
        )
        conn.commit()
        entry_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        entries = _recent_agent_activity(conn, limit=50)
    except Exception as e:
        conn.close()
        return JSONResponse({"status": "error", "error": str(e)}, status_code=500)
    conn.close()
    return JSONResponse(
        {
            "status": "ok",
            "entry_id": entry_id,
            "entries": entries,
            "markdown": _agent_activity_markdown(entries),
        }
    )


@app.get("/api/coordination/status")
async def coordination_status():
    import sqlite3 as _sql3

    db = _coord_db_path()
    conn = _sql3.connect(str(db))
    try:
        payload = _coordination_status_payload(conn, include_completed_limit=50)
    except Exception:
        conn.close()
        return JSONResponse({"status": "ok", "active_sessions": [], "completed_today": [], "locked_files": {}})
    conn.close()
    payload["status"] = "ok"
    return JSONResponse(payload)


@app.get("/api/coordination/log")
async def coordination_log(limit: int = 50):
    import sqlite3 as _sql3

    db = _coord_db_path()
    conn = _sql3.connect(str(db))
    conn.row_factory = _sql3.Row
    try:
        entries = _recent_agent_activity(conn, limit=limit)
    except Exception as e:
        conn.close()
        return JSONResponse({"status": "error", "error": str(e)}, status_code=500)
    conn.close()
    return JSONResponse(
        {
            "status": "ok",
            "entries": entries,
            "markdown": _agent_activity_markdown(entries),
        }
    )


@app.get("/api/coordination/locked-files")
async def coordination_locked_files():
    import sqlite3 as _sql3

    db = _coord_db_path()
    conn = _sql3.connect(str(db))
    try:
        payload = _coordination_status_payload(conn, include_completed_limit=1)
    except Exception:
        conn.close()
        return JSONResponse({"status": "ok", "locked_files": {}})
    conn.close()
    active = payload.get("active_sessions", [])
    locked = {}
    for sess in active:
        for fp in sess.get("files_locked", []):
            if fp not in locked:
                locked[fp] = {"session_id": sess.get("session_id", ""), "last_heartbeat": sess.get("last_heartbeat", "")}
    return JSONResponse({"status": "ok", "locked_files": locked})


@app.get("/api/coordination/context-pack", dependencies=[Depends(verify_action_auth)])
async def coordination_context_pack(limit: int = 20):
    import sqlite3 as _sql3

    db = _coord_db_path()
    conn = _sql3.connect(str(db))
    try:
        coordination = _coordination_status_payload(conn, include_completed_limit=100)
        snapshot = _db_context_snapshot(conn, limit=limit)
    except Exception as e:
        conn.close()
        return JSONResponse({"status": "error", "error": str(e)}, status_code=500)
    conn.close()

    return JSONResponse(
        {
            "status": "ok",
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "coordination": coordination,
            "database_snapshot": snapshot,
            "startup_commands": {
                "checkin": "scripts/coord_checkin.sh <session_id> \"<working_on>\" <file1> <file2>",
                "heartbeat": "curl -X POST /api/coordination/heartbeat",
                "checkout": "scripts/coord_checkout.sh <session_id> \"<completed_work>\" <file1> <file2>",
            },
        }
    )


# ── Task Board API endpoints ───────────────────────────────────────────────────

def sync_tasks_md(conn=None) -> bool:
    """Regenerate TASKS.md from task_board table."""
    import sqlite3 as _sql3

    close_conn = False
    if conn is None:
        conn = _sql3.connect(str(_coord_db_path()))
        close_conn = True
    conn.row_factory = _sql3.Row
    try:
        tasks = conn.execute(
            "SELECT * FROM task_board ORDER BY priority DESC, created_at ASC"
        ).fetchall()
    except Exception:
        if close_conn:
            conn.close()
        return False

    high, medium, low, done_today = [], [], [], []
    today = datetime.now().strftime("%Y-%m-%d")
    for t in tasks:
        t = dict(t)
        if t["status"] == "done":
            if t.get("completed_at", "").startswith(today):
                done_today.append(t)
            continue
        bucket = high if t["priority"] >= 4 else (low if t["priority"] <= 2 else medium)
        bucket.append(t)

    def _task_line(t):
        check = "[x]" if t["status"] == "done" else ("[~]" if t["status"] in ("claimed", "in_progress") else "[ ]")
        assignee = t.get("assigned_to") or "unassigned"
        note = f" — {t['progress_note']}" if t.get("progress_note") else ""
        return f"- {check} #{t['id']}: {t['title']} ({t['status']}, {assignee}){note}"

    lines = ["# Nexus Task Board", "", "## HIGH PRIORITY"]
    lines.extend([_task_line(t) for t in high] or ["- (none)"])
    lines.extend(["", "## MEDIUM PRIORITY"])
    lines.extend([_task_line(t) for t in medium] or ["- (none)"])
    lines.extend(["", "## LOW PRIORITY"])
    lines.extend([_task_line(t) for t in low] or ["- (none)"])
    lines.extend(["", "## COMPLETED TODAY"])
    for t in done_today:
        lines.append(f"- #{t['id']}: {t['title']} — {t.get('assigned_to') or '?'} — {t.get('completed_at', '')}")
    if not done_today:
        lines.append("- (none)")

    lines.extend([
        "",
        "## RULES",
        "1. Read this file at session start to see what Kai wants done.",
        "2. Claim a task via `POST /api/tasks/{id}/claim` before starting.",
        "3. Update progress as you work via `POST /api/tasks/{id}/update`.",
        "4. Mark done when complete. TASKS.md auto-regenerates.",
        "",
    ])

    try:
        repo_root = Path(__file__).resolve().parent
        (repo_root / "TASKS.md").write_text("\n".join(lines), encoding="utf-8")
    except Exception:
        pass

    if close_conn:
        conn.close()
    return True


@app.get("/api/tasks")
async def list_tasks(status: str = "", priority: int = 0, assigned_to: str = ""):
    import sqlite3 as _sql3
    conn = _sql3.connect(str(_coord_db_path()))
    conn.row_factory = _sql3.Row
    q = "SELECT * FROM task_board WHERE 1=1"
    params = []
    if status:
        q += " AND status = ?"
        params.append(status)
    if priority:
        q += " AND priority = ?"
        params.append(priority)
    if assigned_to:
        q += " AND assigned_to = ?"
        params.append(assigned_to)
    q += " ORDER BY priority DESC, created_at ASC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return JSONResponse({"status": "ok", "tasks": [dict(r) for r in rows]})


@app.post("/api/tasks", dependencies=[Depends(verify_action_auth)])
async def create_task(request: Request):
    import sqlite3 as _sql3
    body = await request.json()
    title = body.get("title", "").strip()
    if not title:
        return JSONResponse({"status": "error", "error": "title required"}, status_code=400)
    conn = _sql3.connect(str(_coord_db_path()))
    now = datetime.utcnow().isoformat()
    cur = conn.execute(
        "INSERT INTO task_board (title, description, priority, status, created_by, created_at, updated_at, tags, files_involved, task_type, complexity) "
        "VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)",
        (
            title,
            body.get("description", ""),
            body.get("priority", 3),
            body.get("created_by", ""),
            now, now,
            json.dumps(body.get("tags", [])),
            json.dumps(body.get("files_involved", [])),
            body.get("task_type", "code"),
            body.get("complexity", 3),
        ),
    )
    task_id = cur.lastrowid
    conn.commit()
    sync_tasks_md(conn)
    conn.close()
    return JSONResponse({"status": "ok", "task_id": task_id})


@app.post("/api/tasks/{task_id}/claim", dependencies=[Depends(verify_action_auth)])
async def claim_task(task_id: int, request: Request):
    import sqlite3 as _sql3
    body = await request.json()
    session_id = body.get("session_id", "").strip()
    if not session_id:
        return JSONResponse({"status": "error", "error": "session_id required"}, status_code=400)
    conn = _sql3.connect(str(_coord_db_path()))
    row = conn.execute("SELECT status, assigned_to FROM task_board WHERE id = ?", (task_id,)).fetchone()
    if not row:
        conn.close()
        return JSONResponse({"status": "error", "error": "task not found"}, status_code=404)
    if row[0] not in ("open", "blocked"):
        conn.close()
        return JSONResponse({"status": "error", "error": f"task is {row[0]}, cannot claim"}, status_code=409)
    now = datetime.utcnow().isoformat()
    conn.execute(
        "UPDATE task_board SET status = 'claimed', assigned_to = ?, updated_at = ? WHERE id = ?",
        (session_id, now, task_id),
    )
    conn.commit()
    sync_tasks_md(conn)
    conn.close()
    return JSONResponse({"status": "ok", "claimed_by": session_id})


@app.post("/api/tasks/{task_id}/update", dependencies=[Depends(verify_action_auth)])
async def update_task(task_id: int, request: Request):
    import sqlite3 as _sql3
    body = await request.json()
    conn = _sql3.connect(str(_coord_db_path()))
    row = conn.execute("SELECT id FROM task_board WHERE id = ?", (task_id,)).fetchone()
    if not row:
        conn.close()
        return JSONResponse({"status": "error", "error": "task not found"}, status_code=404)
    now = datetime.utcnow().isoformat()
    sets, params = ["updated_at = ?"], [now]
    for field in ("status", "progress_note", "assigned_to"):
        if field in body:
            sets.append(f"{field} = ?")
            params.append(body[field])
    new_status = body.get("status", "")
    if new_status == "done":
        sets.append("completed_at = ?")
        params.append(now)
    if new_status == "in_progress" and "assigned_to" not in body:
        pass  # keep existing assignee
    params.append(task_id)
    conn.execute(f"UPDATE task_board SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()
    sync_tasks_md(conn)
    conn.close()
    return JSONResponse({"status": "ok"})


@app.get("/api/tasks/board")
async def task_board_view():
    """Kanban-style grouped view of all tasks."""
    import sqlite3 as _sql3
    conn = _sql3.connect(str(_coord_db_path()))
    conn.row_factory = _sql3.Row
    rows = conn.execute("SELECT * FROM task_board ORDER BY priority DESC, created_at ASC").fetchall()
    conn.close()
    board = {"open": [], "claimed": [], "in_progress": [], "done": [], "blocked": []}
    for r in rows:
        d = dict(r)
        board.setdefault(d["status"], []).append(d)
    return JSONResponse({"status": "ok", "board": board})


# ── Messaging API endpoints ────────────────────────────────────────────────────
@app.get("/api/msg/status")
async def msg_status():
    from integrations.messaging import get_messenger
    m = get_messenger()
    if not m: return JSONResponse({"ready": False, "test_mode": True})
    return JSONResponse({"ready": True, "test_mode": m.test_mode,
                         "test_phones": list(m.test_phones),
                         "test_emails": list(m.test_emails)})

@app.get("/api/msg/conversations")
async def msg_conversations(limit: int = 50):
    from integrations.messaging import get_messenger
    m = get_messenger()
    if not m: return JSONResponse([])
    return JSONResponse(m.get_conversations(limit))

@app.get("/api/msg/test-contacts")
async def get_test_contacts():
    from integrations.messaging import get_messenger
    m = get_messenger()
    if not m: return JSONResponse([])
    return JSONResponse(m.get_test_contacts())

@app.post("/api/msg/add-test-contact")
async def add_test_contact(request: Request):
    body = await request.json()
    from integrations.messaging import get_messenger
    m = get_messenger()
    if not m: return JSONResponse({"error": "Messenger not configured"}, status_code=400)
    m.add_test_contact(body.get("name",""), body.get("phone",""),
                       body.get("email",""), body.get("carrier","tmobile"))
    return JSONResponse({"ok": True})

@app.post("/api/msg/send-sms", dependencies=[Depends(verify_action_auth)])
async def send_sms_api(request: Request):
    body = await request.json()
    from integrations.messaging import get_messenger
    m = get_messenger()
    if not m: return JSONResponse({"error": "Not configured"}, status_code=400)
    result = await m.send_sms(body.get("phone",""), body.get("message",""),
                              body.get("carrier","tmobile"), body.get("name",""),
                              approval_id=body.get("approval_id"))
    if result["ok"]: await broadcast({"type":"msg_sent","channel":"sms","to":body.get("phone",""),"preview":body.get("message","")[:60]})
    return JSONResponse(result)

@app.post("/api/msg/send-email", dependencies=[Depends(verify_action_auth)])
async def send_email_api(request: Request):
    body = await request.json()
    from integrations.messaging import get_messenger
    m = get_messenger()
    if not m: return JSONResponse({"error": "Not configured"}, status_code=400)
    result = await m.send_email(body.get("email",""), body.get("subject",""),
                                body.get("message",""), body.get("name",""),
                                approval_id=body.get("approval_id"))
    if result["ok"]: await broadcast({"type":"msg_sent","channel":"email","to":body.get("email",""),"preview":body.get("subject","")})
    return JSONResponse(result)

@app.post("/api/msg/generate-reply")
async def generate_reply_api(request: Request):
    """AI generates a Kai-like reply to an incoming message."""
    body = await request.json()
    from integrations.messaging import generate_bot_reply
    orch = get_orch()
    result = await generate_bot_reply(
        orch.provider,
        body.get("channel","sms"),
        body.get("incoming",""),
        body.get("contact_name",""),
        body.get("history",[])
    )
    return JSONResponse(result)

@app.post("/api/msg/generate-outreach")
async def generate_outreach_api(request: Request):
    """AI generates initial outreach for a contact."""
    body = await request.json()
    from integrations.messaging import generate_initial_outreach
    orch = get_orch()
    result = await generate_initial_outreach(orch.provider, body.get("channel","sms"), body)
    return JSONResponse(result)

@app.post("/api/msg/toggle-test-mode", dependencies=[Depends(verify_action_auth)])
async def toggle_test_mode(request: Request):
    from integrations.messaging import get_messenger
    m = get_messenger()
    if not m: return JSONResponse({"error": "Not configured"})
    body = await request.json()
    m.test_mode = body.get("test_mode", True)
    return JSONResponse({"ok": True, "test_mode": m.test_mode})

# ── GHL API endpoints ──────────────────────────────────────────────────────────
@app.post("/api/ghl/save", dependencies=[Depends(verify_action_auth)])
async def ghl_save(request: Request):
    body = await request.json()
    fields = ["ghl_api_key","ghl_location_id","gmail_address","gmail_app_password",
              "default_carrier","message_template","ghl_poll_interval"]
    updates = {f: body[f] for f in fields if f in body}
    save_config(updates)
    # Restart GHL poller with new config
    from integrations.ghl import get_poller
    p = get_poller()
    if p: p.stop()
    asyncio.create_task(_start_ghl(load_config()))
    return JSONResponse({"ok": True})

@app.get("/api/ghl/contacts")
async def ghl_contacts(q: str = "", limit: int = 50):
    from integrations.ghl import search_contacts_tool
    try:
        result = await search_contacts_tool(query=q, limit=limit)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/ghl/messages")
async def ghl_messages(limit: int = 50):
    from integrations.ghl import get_message_log
    return JSONResponse(await get_message_log(limit))

@app.post("/api/ghl/send", dependencies=[Depends(verify_action_auth)])
async def ghl_send_message(request: Request):
    body = await request.json()
    phone = body.get("phone",""); message = body.get("message",""); carrier = body.get("carrier")
    from integrations.ghl import get_messenger
    m = get_messenger()
    if not m: return JSONResponse({"error":"Messaging not configured (need Gmail)"}, status_code=400)
    # send_sms_via_email → send_sms → outbound_gate (approval_id required)
    result = await m.send_sms(phone, message, carrier or "tmobile",
                              approval_id=body.get("approval_id"))
    return JSONResponse(result)

@app.get("/api/ghl/status")
async def ghl_status():
    from integrations.ghl import get_ghl, get_poller, get_messenger
    return JSONResponse({
        "ghl_connected": bool(get_ghl()),
        "poller_running": bool(get_poller() and get_poller()._running),
        "messaging_ready": bool(get_messenger()),
    })

# ── Browser Login endpoints ───────────────────────────────────────────────────
@app.post("/api/browser/login-ghl")
async def browser_login_ghl():
    """Launch headed browser for user to log into GHL manually."""
    from integrations.ghl import get_ghl
    ghl = get_ghl()
    if not ghl:
        return JSONResponse({"error": "GHL scraper not initialized. Make sure Playwright is installed."}, status_code=400)
    try:
        await broadcast({"type": "ghl_event", "message": "Opening GHL login browser..."})
        ok = await ghl.manual_login()
        if ok:
            await broadcast({"type": "ghl_event", "message": "GHL login successful! Session saved."})
            return JSONResponse({"ok": True, "message": "Login successful, session saved"})
        else:
            return JSONResponse({"ok": False, "error": "Login timed out or failed"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

def _split_name_parts(full_name: str) -> tuple[str, str]:
    name = " ".join((full_name or "").strip().split())
    if not name:
        return "", ""
    parts = name.split(" ", 1)
    return parts[0], parts[1] if len(parts) > 1 else ""


def _extract_salutation_name(message_text: str) -> str:
    text = str(message_text or "").strip()
    if not text:
        return ""
    lines = [ln.strip() for ln in text.splitlines()[:6] if ln.strip()]
    for line in lines:
        m = re.match(r"^(hi|hello|hey)\s+([A-Za-z][A-Za-z' -]{0,40})[,\.\!\:]?$", line, flags=re.IGNORECASE)
        if not m:
            continue
        candidate = " ".join(m.group(2).split()).strip()
        if candidate.lower() in {"there", "team", "all", "friend"}:
            continue
        return candidate
    return ""


def _normalize_phone(phone: str) -> str:
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def _normalize_email(email_addr: str) -> str:
    return (email_addr or "").strip().lower()


def _is_truthy(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    txt = str(value).strip().lower()
    if not txt:
        return default
    return txt not in {"0", "false", "no", "off", "disabled"}


def _email_local_part(email_addr: str) -> str:
    email_addr = _normalize_email(email_addr)
    if "@" not in email_addr:
        return ""
    return email_addr.split("@", 1)[0]


_SMS_GATEWAY_DOMAINS = {
    "tmomail.net",
    "txt.att.net",
    "vtext.com",
    "messaging.sprintpcs.com",
    "mms.cricketwireless.net",
    "mymetropcs.com",
    "sms.myboostmobile.com",
    "msg.fi.google.com",
    "vmobl.com",
}


def _phone_from_email_alias(email_addr: str) -> str:
    """Extract phone digits from SMS-gateway style emails (e.g. 8184489055@tmomail.net)."""
    email_addr = _normalize_email(email_addr)
    if "@" not in email_addr:
        return ""
    local, domain = email_addr.split("@", 1)
    if domain not in _SMS_GATEWAY_DOMAINS:
        return ""
    digits = "".join(ch for ch in local if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else ""


def _is_sms_gateway_email(email_addr: str) -> bool:
    email_addr = _normalize_email(email_addr)
    if "@" not in email_addr:
        return False
    _, domain = email_addr.split("@", 1)
    return domain in _SMS_GATEWAY_DOMAINS


def _source_rank(source: str) -> int:
    s = (source or "").strip().lower()
    if not s:
        return 0
    if s.startswith("facebook"):
        return 7
    if s.startswith("website"):
        return 6
    if s.startswith("referral"):
        return 6
    if s.startswith("ghl") or s.startswith("api"):
        return 5
    if s.startswith("manual"):
        return 4
    if s.startswith("google_voice") or s.startswith("sms"):
        return 3
    if s.startswith("email"):
        return 2
    return 1


def _is_placeholder_name(name: str, email_addr: str = "", phone: str = "") -> bool:
    n = " ".join((name or "").strip().split()).lower()
    if not n:
        return True
    if n in {"unknown", "lead", "contact", "n/a", "na", "none", "null", "test"}:
        return True
    if re.fullmatch(r"[\d\W_]+", n):
        return True
    local = _email_local_part(email_addr)
    if local and n == local:
        return True
    digits = _normalize_phone(phone)
    if digits and n == digits:
        return True
    return False


def _lead_identity_sort_key(row, channel: str = "") -> tuple:
    lead_id = int(row.get("id") or row.get("lead_id") or 0)
    email_addr = _normalize_email(row.get("email", ""))
    phone = _normalize_phone(row.get("phone", ""))
    has_phone = bool(phone)
    has_email = bool(email_addr)
    has_non_gateway_email = has_email and not _is_sms_gateway_email(email_addr)
    candidate_name = _best_display_name(
        row.get("full_name", ""),
        row.get("first_name", ""),
        row.get("last_name", ""),
        email_addr,
        phone,
    )
    has_real_name = bool(candidate_name and candidate_name != "Unknown" and not _is_placeholder_name(candidate_name, email_addr, phone))
    source_rank = _source_rank(row.get("source", ""))
    message_count = int(row.get("message_count") or 0)

    channel_pref = 0
    if channel == "sms":
        channel_pref = 50 if has_phone else 0
    elif channel == "email":
        channel_pref = 50 if has_non_gateway_email else (20 if has_email else 0)

    score = (
        channel_pref
        + (60 if has_real_name else 0)
        + (45 if has_phone else 0)
        + (30 if has_non_gateway_email else (10 if has_email else 0))
        + (source_rank * 6)
        + min(message_count, 25)
    )
    return (
        score,
        1 if has_real_name else 0,
        1 if has_phone else 0,
        1 if has_non_gateway_email else 0,
        source_rank,
        -lead_id if lead_id else 0,  # prefer older canonical ids when tied
    )


def _pick_best_lead_row(rows: list, channel: str = ""):
    if not rows:
        return None
    best = max(rows, key=lambda r: _lead_identity_sort_key(r, channel=channel))
    return dict(best)


def _resolve_related_send_target(lead_id: int, channel: str):
    import sqlite3 as _sq
    conn = _sq.connect(str(Path.home() / ".nexus" / "memory.db"))
    conn.row_factory = _sq.Row
    related_ids = _related_lead_ids(conn, int(lead_id))
    placeholders = ",".join("?" for _ in related_ids) or "?"
    rows = conn.execute(
        f"SELECT * FROM leads WHERE id IN ({placeholders})",
        related_ids,
    ).fetchall()
    conn.close()
    if not rows:
        return None

    candidates = [dict(r) for r in rows]
    ch = (channel or "").strip().lower()
    if ch == "sms":
        sms_candidates = [r for r in candidates if _normalize_phone(r.get("phone", ""))]
        if sms_candidates:
            candidates = sms_candidates
    elif ch == "email":
        direct_email = [
            r for r in candidates
            if _normalize_email(r.get("email", "")) and not _is_sms_gateway_email(r.get("email", ""))
        ]
        if direct_email:
            candidates = direct_email
        else:
            any_email = [r for r in candidates if _normalize_email(r.get("email", ""))]
            if any_email:
                candidates = any_email

    best = _pick_best_lead_row(candidates, channel=ch)
    if best:
        best["related_lead_ids"] = related_ids
    return best


def _best_display_name(full_name: str, first_name: str, last_name: str, email_addr: str, phone: str) -> str:
    full = " ".join((full_name or "").strip().split())
    if full and not _is_placeholder_name(full, email_addr, phone):
        return full
    first = " ".join((first_name or "").strip().split())
    last = " ".join((last_name or "").strip().split())
    combo = " ".join([p for p in (first, last) if p]).strip()
    if combo and not _is_placeholder_name(combo, email_addr, phone):
        return combo
    if first and not _is_placeholder_name(first, email_addr, phone):
        return first
    local = _email_local_part(email_addr)
    if local:
        return local
    digits = _normalize_phone(phone)
    if digits:
        return digits
    return "Unknown"


def _apply_lead_name_hint(lead_id: int, hinted_name: str):
    first, last = _split_name_parts(hinted_name)
    if not first:
        return
    import sqlite3 as _sq
    conn = _sq.connect(str(Path.home() / ".nexus" / "memory.db"))
    conn.row_factory = _sq.Row
    row = conn.execute(
        "SELECT first_name, last_name, full_name, email, phone FROM leads WHERE id = ?",
        (lead_id,),
    ).fetchone()
    if not row:
        conn.close()
        return
    existing_full = (row["full_name"] or "").strip()
    existing_combo = f"{row['first_name'] or ''} {row['last_name'] or ''}".strip()
    if not (_is_placeholder_name(existing_full, row["email"], row["phone"]) or _is_placeholder_name(existing_combo, row["email"], row["phone"])):
        conn.close()
        return
    full = f"{first} {last}".strip()
    conn.execute(
        "UPDATE leads SET first_name = ?, last_name = ?, full_name = ?, updated_at = datetime('now') WHERE id = ?",
        (first, last, full, int(lead_id)),
    )
    conn.commit()
    conn.close()


def _ensure_conversation_reads_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS lead_conversation_reads (
            lead_id INTEGER PRIMARY KEY,
            last_read_ts TEXT DEFAULT '',
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_convo_reads_updated ON lead_conversation_reads(updated_at)")


def _crm_fallback_conn():
    import sqlite3 as _sq

    conn = _sq.connect(str(_coord_db_path()))
    conn.row_factory = _sq.Row
    return conn


def _crm_search_leads_fallback(
    q: str = "",
    status: str = "",
    sort: str = "updated_at",
    limit: int = 25,
    offset: int = 0,
):
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    conditions = []
    params = []

    if q:
        qp = f"%{q}%"
        conditions.append(
            "("
            "full_name LIKE ? OR first_name LIKE ? OR last_name LIKE ? OR "
            "phone LIKE ? OR email LIKE ? OR event_city LIKE ? OR business_name LIKE ?"
            ")"
        )
        params.extend([qp, qp, qp, qp, qp, qp, qp])

    if status:
        conditions.append("(booking_status = ? OR status = ?)")
        params.extend([status, status])

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    allowed_sorts = {
        "updated_at",
        "discovered_at",
        "full_name",
        "event_date",
        "last_contacted_at",
        "booking_status",
        "deposit_status",
        "total_quote_amount",
    }
    sort_col = sort.lstrip("-")
    if sort_col not in allowed_sorts:
        sort_col = "updated_at"
    desc = " DESC" if not sort.startswith("-") else ""
    if sort_col in {"updated_at", "discovered_at", "last_contacted_at", "event_date"}:
        desc = " DESC"

    conn = _crm_fallback_conn()
    try:
        total = int(conn.execute(f"SELECT COUNT(*) FROM leads {where}", params).fetchone()[0])
        rows = conn.execute(
            f"SELECT * FROM leads {where} ORDER BY {sort_col}{desc} LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [dict(row) for row in rows], total
    finally:
        conn.close()


def _crm_pipeline_fallback():
    from core.services.pipeline_service import STAGES

    pipeline = {}
    conn = _crm_fallback_conn()
    try:
        for stage_key in STAGES:
            rows = conn.execute(
                "SELECT * FROM leads WHERE booking_status = ? ORDER BY updated_at DESC, id DESC LIMIT 100",
                (stage_key,),
            ).fetchall()
            pipeline[stage_key] = [dict(row) for row in rows]
        return pipeline
    finally:
        conn.close()


def _related_lead_ids(conn, lead_id: int) -> list[int]:
    rows = conn.execute("SELECT id, email, phone FROM leads").fetchall()
    by_id = {int(r["id"]): r for r in rows}
    if int(lead_id) not in by_id:
        return [int(lead_id)]

    email_map: dict[str, set[int]] = {}
    phone_map: dict[str, set[int]] = {}
    for r in rows:
        rid = int(r["id"])
        e = _normalize_email(r["email"])
        p = _normalize_phone(r["phone"])
        alias_phone = _phone_from_email_alias(e)
        if e:
            email_map.setdefault(e, set()).add(rid)
        if p:
            phone_map.setdefault(p, set()).add(rid)
        if alias_phone:
            phone_map.setdefault(alias_phone, set()).add(rid)

    queue = [int(lead_id)]
    seen: set[int] = set()
    while queue:
        cur = queue.pop(0)
        if cur in seen:
            continue
        seen.add(cur)
        row = by_id.get(cur)
        if not row:
            continue
        e = _normalize_email(row["email"])
        p = _normalize_phone(row["phone"])
        alias_phone = _phone_from_email_alias(e)
        related = set()
        if e:
            related |= email_map.get(e, set())
        if p:
            related |= phone_map.get(p, set())
        if alias_phone:
            related |= phone_map.get(alias_phone, set())
        for rid in related:
            if rid not in seen:
                queue.append(rid)

    return sorted(seen)


def _create_inline_manual_approval(lead: dict, channel: str, message: str, subject: str = "") -> int:
    """Create an approved message_approvals row for explicit in-app manual sends."""
    import sqlite3 as _sq
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn = _sq.connect(str(Path.home() / ".nexus" / "memory.db"))
    conn.execute(
        """
        INSERT INTO message_approvals (
            lead_id, lead_name, lead_phone, lead_email, lead_source,
            channel, message_type, proposed_message, proposed_subject,
            actual_message_sent, status, approval_method,
            created_at, approved_at, telegram_notified
        ) VALUES (?, ?, ?, ?, ?, ?, 'manual_reply', ?, ?, ?, 'approved', 'kai_approved', ?, ?, 1)
        """,
        (
            int(lead.get("id") or 0),
            (lead.get("full_name") or lead.get("name") or "").strip(),
            (lead.get("phone") or "").strip(),
            (lead.get("email") or "").strip().lower(),
            (lead.get("source") or "").strip(),
            (channel or "sms").strip().lower(),
            (message or "")[:4000],
            (subject or "")[:250],
            (message or "")[:4000],
            now,
            now,
        ),
    )
    approval_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    conn.commit()
    conn.close()
    return approval_id


def _is_port_bound(port: int) -> bool:
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.25)
    try:
        return sock.connect_ex(("127.0.0.1", int(port))) == 0
    except Exception:
        return False
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _acquire_gv_owner_lock() -> bool:
    """
    Ensure only one server process controls Google Voice browser/poller.
    Prevents multi-process profile contention causing repeated open/close loops.
    """
    global _gv_owner_lock_fd
    if _gv_owner_lock_fd is not None:
        return True
    try:
        import fcntl  # POSIX (macOS/Linux)

        lock_path = Path.home() / ".nexus" / "gv_owner.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        _gv_owner_lock_fd = fd
        return True
    except Exception:
        try:
            if "fd" in locals():
                os.close(fd)
        except Exception:
            pass
        return False


def _acquire_server_instance_lock(port: int) -> bool:
    """
    Ensure only one Nexus server process binds a given port.
    Prevents duplicate server.py instances from spawning duplicate watchers.
    """
    global _server_instance_lock_fd
    if _server_instance_lock_fd is not None:
        return True
    try:
        import fcntl  # POSIX (macOS/Linux)

        lock_path = Path.home() / ".nexus" / f"server_{int(port)}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        _server_instance_lock_fd = fd
        return True
    except Exception:
        try:
            if "fd" in locals():
                os.close(fd)
        except Exception:
            pass
        return False


def _match_any_lead_by_phone(phone: str):
    digits = _normalize_phone(phone)
    if not digits:
        return None
    import sqlite3 as _sq
    db = _sq.connect(str(Path.home() / ".nexus" / "memory.db"))
    rows = db.execute("SELECT id, phone FROM leads ORDER BY id DESC").fetchall()
    db.close()
    for row in rows:
        if _normalize_phone(row[1]) == digits:
            return int(row[0])
    return None


def _match_any_lead_by_email(email_addr: str):
    email_addr = (email_addr or "").strip().lower()
    if not email_addr:
        return None
    alias_phone = _phone_from_email_alias(email_addr)
    if alias_phone:
        by_phone = _match_any_lead_by_phone(alias_phone)
        if by_phone:
            return by_phone
    import sqlite3 as _sq
    db = _sq.connect(str(Path.home() / ".nexus" / "memory.db"))
    row = db.execute(
        "SELECT id FROM leads WHERE lower(email) = ? ORDER BY id DESC LIMIT 1",
        (email_addr,),
    ).fetchone()
    db.close()
    if row:
        return int(row[0])
    if alias_phone:
        return _match_any_lead_by_phone(alias_phone)
    return None


def _match_any_lead_by_name(name: str):
    clean = " ".join((name or "").strip().split()).lower()
    if not clean or clean in {"unknown", "there", "lead", "contact"}:
        return None
    import sqlite3 as _sq
    db = _sq.connect(str(Path.home() / ".nexus" / "memory.db"))
    row = db.execute(
        """
        SELECT id
        FROM leads
        WHERE lower(trim(full_name)) = ?
           OR lower(trim(first_name || ' ' || last_name)) = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (clean, clean),
    ).fetchone()
    db.close()
    return int(row[0]) if row else None


def _bind_lead_identity(
    lead_id: int,
    phone: str = "",
    email_addr: str = "",
    contact_name: str = "",
    source: str = "",
    source_detail: str = "",
) -> int:
    """Bind newly learned identity fields and return canonical lead id."""
    try:
        from core.lead_identity import bind_identity_to_lead

        result = bind_identity_to_lead(
            int(lead_id),
            phone=phone,
            email=email_addr,
            full_name=contact_name,
            source=source,
            source_detail=source_detail,
            auto_merge=True,
        )
        merged = result.get("merge", {}) if isinstance(result, dict) else {}
        canonical_id = int(merged.get("canonical_id") or lead_id)
        return canonical_id
    except Exception as e:
        print(f"[Identity] bind failed for lead #{lead_id}: {e}")
        return int(lead_id)


def _ensure_inbound_sms_lead(pipeline, phone: str, contact_name: str = ""):
    digits = _normalize_phone(phone)
    lead_id = pipeline.match_phone_to_lead(digits) or _match_any_lead_by_phone(digits)
    if lead_id:
        if contact_name:
            _apply_lead_name_hint(int(lead_id), contact_name)
        return _bind_lead_identity(
            int(lead_id),
            phone=digits,
            contact_name=contact_name,
            source="google_voice_inbound",
            source_detail="Inbound Google Voice SMS",
        )

    first, last = _split_name_parts(contact_name)
    if not first:
        first = "Unknown"

    if not digits:
        return None

    # Cross-channel merge fallback: if we know a reliable name, merge SMS into that lead.
    name_match = _match_any_lead_by_name(contact_name)
    if name_match:
        import sqlite3 as _sq
        conn = _sq.connect(str(Path.home() / ".nexus" / "memory.db"))
        conn.execute(
            "UPDATE leads SET phone = CASE WHEN phone = '' THEN ? ELSE phone END, updated_at = datetime('now') WHERE id = ?",
            (digits, int(name_match)),
        )
        conn.commit()
        conn.close()
        _apply_lead_name_hint(int(name_match), contact_name)
        return _bind_lead_identity(
            int(name_match),
            phone=digits,
            contact_name=contact_name,
            source="google_voice_inbound",
            source_detail="Inbound Google Voice SMS",
        )

    try:
        from core.services import get_lead_service
        svc = get_lead_service()
        if svc:
            lead = svc.create_lead({
                "first_name": first,
                "last_name": last,
                "phone": digits,
                "source": "google_voice_inbound",
                "source_detail": "Inbound Google Voice SMS",
                "notes": "Auto-created from inbound Google Voice SMS",
            })
            if lead and lead.get("id"):
                return _bind_lead_identity(
                    int(lead["id"]),
                    phone=digits,
                    contact_name=contact_name,
                    source="google_voice_inbound",
                    source_detail="Inbound Google Voice SMS",
                )
    except Exception:
        pass

    created_id = int(pipeline.add_lead_manual(
        first_name=first,
        last_name=last,
        phone=digits,
        source="google_voice_inbound",
        notes="Auto-created from inbound Google Voice SMS",
    ))
    return _bind_lead_identity(
        created_id,
        phone=digits,
        contact_name=contact_name,
        source="google_voice_inbound",
        source_detail="Inbound Google Voice SMS",
    )


def _ensure_inbound_email_lead(pipeline, email_addr: str, contact_name: str = ""):
    email_addr = (email_addr or "").strip().lower()
    if not email_addr:
        return None

    lead_id = pipeline.match_email_to_lead(email_addr) or _match_any_lead_by_email(email_addr)
    if lead_id:
        if contact_name:
            _apply_lead_name_hint(int(lead_id), contact_name)
        return _bind_lead_identity(
            int(lead_id),
            email_addr=email_addr,
            contact_name=contact_name,
            source="email_inbound",
            source_detail="Inbound Gmail reply sync",
        )

    first, last = _split_name_parts(contact_name)
    if not first:
        first = email_addr.split("@")[0] or "Unknown"

    # Cross-channel merge fallback by name if existing SMS-only lead exists.
    name_match = _match_any_lead_by_name(contact_name)
    if name_match:
        import sqlite3 as _sq
        conn = _sq.connect(str(Path.home() / ".nexus" / "memory.db"))
        conn.execute(
            "UPDATE leads SET email = CASE WHEN email = '' THEN ? ELSE email END, updated_at = datetime('now') WHERE id = ?",
            (email_addr, int(name_match)),
        )
        conn.commit()
        conn.close()
        _apply_lead_name_hint(int(name_match), contact_name)
        return _bind_lead_identity(
            int(name_match),
            email_addr=email_addr,
            contact_name=contact_name,
            source="email_inbound",
            source_detail="Inbound Gmail reply sync",
        )

    try:
        from core.services import get_lead_service
        svc = get_lead_service()
        if svc:
            lead = svc.create_lead({
                "first_name": first,
                "last_name": last,
                "email": email_addr,
                "source": "email_inbound",
                "source_detail": "Inbound Gmail reply sync",
                "notes": "Auto-created from inbound email reply",
            })
            if lead and lead.get("id"):
                return _bind_lead_identity(
                    int(lead["id"]),
                    email_addr=email_addr,
                    contact_name=contact_name,
                    source="email_inbound",
                    source_detail="Inbound Gmail reply sync",
                )
    except Exception:
        pass

    created_id = int(pipeline.add_lead_manual(
        first_name=first,
        last_name=last,
        email=email_addr,
        source="email_inbound",
        notes="Auto-created from inbound email reply",
    ))
    return _bind_lead_identity(
        created_id,
        email_addr=email_addr,
        contact_name=contact_name,
        source="email_inbound",
        source_detail="Inbound Gmail reply sync",
    )


def _ensure_outbound_email_lead(pipeline, email_addr: str, contact_name: str = ""):
    email_addr = (email_addr or "").strip().lower()
    if not email_addr:
        return None

    lead_id = pipeline.match_email_to_lead(email_addr) or _match_any_lead_by_email(email_addr)
    if lead_id:
        if contact_name:
            _apply_lead_name_hint(int(lead_id), contact_name)
        return _bind_lead_identity(
            int(lead_id),
            email_addr=email_addr,
            contact_name=contact_name,
            source="email_outbound",
            source_detail="Outbound Gmail sent sync",
        )

    first, last = _split_name_parts(contact_name)
    if not first:
        first = email_addr.split("@")[0] or "Unknown"

    try:
        from core.services import get_lead_service
        svc = get_lead_service()
        if svc:
            lead = svc.create_lead({
                "first_name": first,
                "last_name": last,
                "email": email_addr,
                "source": "email_outbound",
                "source_detail": "Outbound Gmail sent sync",
                "notes": "Auto-created from sent Gmail conversation sync",
            })
            if lead and lead.get("id"):
                return _bind_lead_identity(
                    int(lead["id"]),
                    email_addr=email_addr,
                    contact_name=contact_name,
                    source="email_outbound",
                    source_detail="Outbound Gmail sent sync",
                )
    except Exception:
        pass

    created_id = int(pipeline.add_lead_manual(
        first_name=first,
        last_name=last,
        email=email_addr,
        source="email_outbound",
        notes="Auto-created from sent Gmail conversation sync",
    ))
    return _bind_lead_identity(
        created_id,
        email_addr=email_addr,
        contact_name=contact_name,
        source="email_outbound",
        source_detail="Outbound Gmail sent sync",
    )


def _sync_gmail_messages_to_leads(pipeline, account_email: str, messages: list) -> dict:
    """Map Gmail inbox/sent messages to leads and import into lead_messages."""
    account_email = (account_email or "").strip().lower()
    stats = {
        "scanned": 0,
        "imported": 0,
        "inbound": 0,
        "outbound": 0,
        "leads_created": 0,
        "leads_matched": 0,
    }
    if not pipeline or not messages:
        return stats

    # Track outbound recipients from this sync batch so inbound replies can be
    # matched without importing unrelated marketing/notification inbox mail.
    outbound_targets = set()
    for msg in messages:
        direction = (msg.get("direction", "inbound") or "inbound").strip().lower()
        if direction != "outbound":
            continue
        raw_to = msg.get("to_emails", [])
        if isinstance(raw_to, list):
            for e in raw_to:
                email_addr = str(e or "").strip().lower()
                if email_addr and email_addr != account_email:
                    outbound_targets.add(email_addr)
        elif isinstance(raw_to, str):
            email_addr = raw_to.strip().lower()
            if email_addr and email_addr != account_email:
                outbound_targets.add(email_addr)
        to_email = str(msg.get("to_email", "") or "").strip().lower()
        if to_email and to_email != account_email:
            outbound_targets.add(to_email)

    # Existing known CRM contacts with email.
    known_emails = set()
    try:
        import sqlite3 as _sq
        _db = _sq.connect(str(Path.home() / ".nexus" / "memory.db"))
        rows = _db.execute(
            "SELECT DISTINCT lower(email) FROM leads WHERE email != '' AND email IS NOT NULL"
        ).fetchall()
        _db.close()
        known_emails = {r[0] for r in rows if r and r[0]}
    except Exception:
        known_emails = set()

    per_lead_messages: dict[int, list] = {}

    for msg in messages:
        stats["scanned"] += 1
        direction = (msg.get("direction", "inbound") or "inbound").strip().lower()
        subject = msg.get("subject", "")
        content = msg.get("content", "") or msg.get("body", "")
        if not content and not subject:
            continue
        timestamp = msg.get("timestamp", "")
        from_email = (msg.get("from_email", "") or "").strip().lower()

        raw_to = msg.get("to_emails", [])
        to_emails = []
        if isinstance(raw_to, list):
            to_emails = [str(e).strip().lower() for e in raw_to if str(e).strip()]
        elif isinstance(raw_to, str) and raw_to.strip():
            to_emails = [raw_to.strip().lower()]
        if msg.get("to_email"):
            to_emails.append(str(msg.get("to_email")).strip().lower())
        to_emails = list(dict.fromkeys([e for e in to_emails if e]))

        contact_emails = []
        if direction == "outbound":
            contact_emails = [e for e in to_emails if e and e != account_email]
            if not contact_emails:
                continue
            stats["outbound"] += len(contact_emails)
        else:
            if not from_email:
                continue
            local = from_email.split("@")[0] if "@" in from_email else ""
            if from_email == account_email or local in ("mailer-daemon", "postmaster"):
                continue
            is_reply_like = bool(msg.get("is_reply_like"))
            if (from_email not in outbound_targets) and (from_email not in known_emails) and (not is_reply_like):
                continue
            contact_emails = [from_email]
            stats["inbound"] += 1

        for contact_email in contact_emails:
            matched = pipeline.match_email_to_lead(contact_email) or _match_any_lead_by_email(contact_email)
            if matched:
                lead_id = int(matched)
                stats["leads_matched"] += 1
            else:
                if direction == "outbound":
                    lead_id = _ensure_outbound_email_lead(pipeline, contact_email)
                else:
                    lead_id = _ensure_inbound_email_lead(
                        pipeline,
                        contact_email,
                        contact_name=msg.get("from_name", ""),
                    )
                if not lead_id:
                    continue
                stats["leads_created"] += 1

            if direction == "outbound":
                hint_name = _extract_salutation_name(content)
                if hint_name:
                    _apply_lead_name_hint(lead_id, hint_name)
            else:
                inbound_name = (msg.get("from_name", "") or "").strip()
                if inbound_name:
                    _apply_lead_name_hint(lead_id, inbound_name)

            per_lead_messages.setdefault(int(lead_id), []).append({
                "direction": direction,
                "channel": "email",
                "content": content,
                "subject": subject,
                "timestamp": timestamp,
            })

    for lead_id, lead_msgs in per_lead_messages.items():
        stats["imported"] += _import_messages(lead_id, lead_msgs, "email")

    return stats


def _sanitize_inbound_sms_text(message: str) -> str:
    text = re.sub(r"[\u200e\u200f\u202a-\u202e]", "", str(message or "")).strip()
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(
        r"^\s*\+?1?\s*\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}(?:\s*[•·\-:]\s*|\s+)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"^\s*(?:\d\s*){10,11}(?:\s*[•·\-:]\s*|\s+)", "", text)
    text = text.strip(" •·-:\t\r\n")
    if not text:
        return ""
    if re.fullmatch(r"[.·•\-]+", text):
        return ""
    if text.lower() in {"unread", "read", "person", "report", "suspected spam"}:
        return ""
    if re.fullmatch(r"\+?1?\s*\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}", text):
        return ""
    return text


async def _handle_gv_reply(phone: str, message: str, timestamp: str, contact_name: str = ""):
    clean_message = _sanitize_inbound_sms_text(message)
    if not clean_message:
        return

    from core.lead_pipeline import get_pipeline
    pipeline = get_pipeline()
    if not pipeline:
        await broadcast({"type": "ghl_event", "message": f"SMS from {phone}: {clean_message[:80]}"})
        return

    lead_id = _ensure_inbound_sms_lead(pipeline, phone, contact_name=contact_name)
    if lead_id:
        await pipeline.process_reply(lead_id, "sms", clean_message, phone)
        await broadcast({
            "type": "msg_received",
            "channel": "sms",
            "lead_id": int(lead_id),
            "from": phone,
            "preview": clean_message[:80],
            "timestamp": timestamp,
        })
        return

    await broadcast({"type": "ghl_event", "message": f"SMS from {phone}: {clean_message[:80]}"})


def _attach_gv_poller(gv, cfg, poller=None):
    global _gv_reply_poller, _gv_reply_task
    if not gv:
        return
    if _gv_reply_task and not _gv_reply_task.done():
        return

    if not poller:
        from integrations.google_voice import GVReplyPoller
        poller = GVReplyPoller(gv, int(cfg.get("gv_poll_interval", 10)))

    poller.on_reply(_handle_gv_reply)
    _gv_reply_poller = poller
    _gv_reply_task = asyncio.create_task(poller.run())


async def _ensure_gv():
    """Ensure Google Voice client + reply poller are attached."""
    from integrations.google_voice import get_gv, init_google_voice
    gv = get_gv()
    cfg = load_config()
    if gv:
        # Recover from stale/broken browser sessions instead of returning a dead client.
        try:
            if (not getattr(gv, "page", None)) or (not getattr(gv, "logged_in", False)):
                await gv._reconnect()
        except Exception as e:
            print(f"[Pipeline] Google Voice reconnect check failed: {e}")
        try:
            from core.lead_pipeline import get_pipeline
            pipeline = get_pipeline()
            if pipeline:
                pipeline.set_gv(gv)
        except Exception:
            pass
        _attach_gv_poller(gv, cfg)
        return gv

    gv, gv_poller, gv_err = await init_google_voice(cfg)
    if gv:
        from core.lead_pipeline import get_pipeline
        pipeline = get_pipeline()
        if pipeline:
            pipeline.set_gv(gv)
        _attach_gv_poller(gv, cfg, gv_poller)
        print("[Pipeline] Google Voice attached (lazy init)")
    elif gv_err:
        print(f"[Pipeline] Google Voice init failed: {gv_err}")
    return gv


def _lead_display_name(lead: dict) -> str:
    full = " ".join((lead.get("full_name") or "").strip().split())
    if full:
        return full
    combo = " ".join(
        p for p in [(lead.get("first_name") or "").strip(), (lead.get("last_name") or "").strip()] if p
    ).strip()
    if combo:
        return combo
    local = _email_local_part(lead.get("email", ""))
    if local:
        return local
    return _normalize_phone(lead.get("phone", "")) or "Unknown"


def _lead_has_outbound_contact(lead_id: int) -> bool:
    import sqlite3 as _sq

    conn = _sq.connect(str(Path.home() / ".nexus" / "memory.db"))
    row = conn.execute(
        "SELECT COUNT(*) FROM lead_messages WHERE lead_id = ? AND direction = 'outbound' "
        "AND lower(coalesce(channel,'')) IN ('sms','email','call')",
        (int(lead_id),),
    ).fetchone()
    conn.close()
    return bool(row and int(row[0] or 0) > 0)


def _log_system_call_attempt(
    lead_id: int,
    phone: str,
    outcome: str = "no_answer",
    note: str = "",
    method: str = "auto_brief_call",
    actor: str = "Nexus",
):
    import sqlite3 as _sq

    now_ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    outcome = (outcome or "no_answer").strip().lower()
    outcome_label = {
        "no_answer": "No answer",
        "voicemail": "Voicemail",
        "connected": "Connected",
        "busy": "Busy",
        "failed": "Failed",
        "skipped": "Skipped",
    }.get(outcome, outcome.replace("_", " ").title())
    msg = note or f"Call attempted ({outcome_label})"

    conn = _sq.connect(str(Path.home() / ".nexus" / "memory.db"))
    conn.execute(
        "INSERT INTO lead_messages (lead_id, ts, direction, channel, method, content, subject, status) "
        "VALUES (?, ?, 'outbound', 'call', ?, ?, '', ?)",
        (int(lead_id), now_ts, method, msg[:4000], outcome or "no_answer"),
    )
    conn.execute(
        "INSERT INTO lead_events (ts, lead_id, event_type, details) VALUES (?, ?, 'call_attempt', ?)",
        (now_ts, int(lead_id), f"{actor}: {msg[:500]}"),
    )
    conn.execute(
        "UPDATE leads SET last_contacted_at = ?, updated_at = ? WHERE id = ?",
        (now_ts, now_ts, int(lead_id)),
    )
    conn.commit()
    conn.close()


def _build_fb_primary_sms(first_name: str = "") -> str:
    first = _safe_fb_first_name(first_name)
    greeting = f"Hey {first}, this is Kai from Zoar Bathroom Rentals." if first else "Hey, this is Kai from Zoar Bathroom Rentals."
    return (
        f"{greeting} I just tried calling about your restroom trailer request.\n\n"
        "Could you text me:\n"
        "1) Event date\n"
        "2) Estimated guest count\n"
        "3) City or venue name\n\n"
        "Once I have this I can send your quote right away."
    )


def _build_fb_primary_email(first_name: str = "") -> tuple[str, str]:
    first = _safe_fb_first_name(first_name)
    hello = f"Hi {first}," if first else "Hi there,"
    subject = "Quick details needed for your restroom trailer quote"
    body = (
        f"{hello}\n\n"
        "Thanks for reaching out to Zoar Bathroom Rentals. I just tried calling you.\n\n"
        "To send your quote fast, please reply with:\n"
        "• Event date\n"
        "• Estimated guest count\n"
        "• Event city or venue name\n\n"
        "As soon as I have that, I can send pricing and availability.\n\n"
        "Thanks,\n"
        "Kai\n"
        "Zoar Bathroom Rentals"
    )
    return subject, body


def _safe_fb_first_name(raw_name: str = "") -> str:
    """Only use clean human-like first names for FB-form outreach greetings.

    Never trust generic mailbox/local-part tokens as names.
    """
    token = re.sub(r"[^A-Za-z'\-]", "", (raw_name or "").strip().split(" ")[0] if raw_name else "")
    if not token:
        return ""
    low = token.lower()
    blocked = {
        "info", "sales", "support", "contact", "hello", "events", "weddings",
        "booking", "bookings", "admin", "team", "office", "venue",
    }
    if low in blocked:
        return ""
    if len(token) < 2 or len(token) > 24:
        return ""
    return token.capitalize()


async def _run_google_sheet_first_touch(lead: dict, row_ctx: dict) -> dict:
    from core.lead_pipeline import get_pipeline

    cfg = load_config()
    if not _is_truthy(cfg.get("google_sheet_crm_auto_first_touch", True), True):
        return {"ok": True, "skipped": "auto_first_touch_disabled"}

    pipeline = get_pipeline()
    if not pipeline:
        return {"ok": False, "error": "Lead pipeline unavailable"}

    lead_id = int(lead.get("id") or 0)
    if lead_id <= 0:
        return {"ok": False, "error": "Invalid lead id"}
    if _lead_has_outbound_contact(lead_id):
        return {"ok": True, "skipped": "already_contacted"}

    name = _lead_display_name(lead)
    first_name = (lead.get("first_name") or "").strip()
    phone = _normalize_phone(lead.get("phone", ""))
    email = _normalize_email(lead.get("email", ""))

    call_result = {"ok": False, "skipped": "no_phone"}
    if phone and _is_truthy(cfg.get("google_sheet_crm_brief_call_enabled", True), True):
        twilio = getattr(pipeline, "_twilio", None)
        if twilio and hasattr(twilio, "place_brief_call"):
            call_result = await twilio.place_brief_call(
                phone,
                ring_seconds=int(cfg.get("google_sheet_crm_call_ring_seconds", 8) or 8),
            )
            if call_result.get("ok"):
                _log_system_call_attempt(
                    lead_id=lead_id,
                    phone=phone,
                    outcome="no_answer",
                    note=f"Auto brief call placed after FB Sheet lead ingest ({name})",
                    method="twilio_brief_call",
                )
            else:
                _log_system_call_attempt(
                    lead_id=lead_id,
                    phone=phone,
                    outcome="failed",
                    note=f"Auto brief call failed: {call_result.get('error', 'unknown')}",
                    method="twilio_brief_call",
                )
        else:
            _log_system_call_attempt(
                lead_id=lead_id,
                phone=phone,
                outcome="skipped",
                note="Auto brief call skipped: Twilio voice not configured",
                method="auto_brief_call",
            )

    sms_result = {"ok": False, "skipped": "sms_disabled_or_no_phone"}
    if phone and _is_truthy(cfg.get("google_sheet_crm_send_sms", True), True):
        sms_body = _build_fb_primary_sms(first_name)
        sms_approval = _create_inline_manual_approval(lead, "sms", sms_body)
        sms_result = await pipeline.send_sms(phone, sms_body, lead_id, approval_id=sms_approval)

    email_result = {"ok": False, "skipped": "email_disabled_or_missing"}
    if (
        email
        and not _is_sms_gateway_email(email)
        and _is_truthy(cfg.get("google_sheet_crm_send_email", True), True)
    ):
        subject, body = _build_fb_primary_email(first_name)
        email_approval = _create_inline_manual_approval(lead, "email", body, subject=subject)
        email_result = await pipeline.send_email(email, subject, body, lead_id, approval_id=email_approval)

    if sms_result.get("ok") or email_result.get("ok"):
        import sqlite3 as _sq

        now_ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        conn = _sq.connect(str(Path.home() / ".nexus" / "memory.db"))
        conn.execute(
            "UPDATE leads SET status='initial_contact', booking_status='qualifying', "
            "last_contacted_at=?, updated_at=? WHERE id=?",
            (now_ts, now_ts, lead_id),
        )
        conn.execute(
            "INSERT INTO lead_events (ts, lead_id, event_type, details) VALUES (?, ?, 'initial_contact', ?)",
            (now_ts, lead_id, "Auto first-touch sent from Google Sheet CRM flow"),
        )
        conn.commit()
        conn.close()

        await broadcast(
            {
                "type": "lead_event",
                "event": "initial_contact",
                "lead_id": lead_id,
                "name": name,
                "source": "facebook_sheet",
                "row": int(row_ctx.get("row_number") or 0),
                "sms_ok": bool(sms_result.get("ok")),
                "email_ok": bool(email_result.get("ok")),
            }
        )

    return {
        "ok": True,
        "call": call_result,
        "sms": sms_result,
        "email": email_result,
    }


async def _handle_google_sheet_crm_row(item: dict) -> dict:
    from core.services import get_lead_service
    from core.lead_pipeline import get_pipeline

    lead_data = dict(item.get("lead_data") or {})
    if not (_normalize_phone(lead_data.get("phone", "")) or _normalize_email(lead_data.get("email", ""))):
        return {"ok": True, "skipped": "missing_phone_and_email"}

    source_detail = (lead_data.get("source_detail") or "").strip()
    row_num = int(item.get("row_number") or 0)
    if row_num:
        suffix = f" | sheet_row={row_num}"
        lead_data["source_detail"] = (source_detail + suffix).strip(" |")

    svc = get_lead_service()
    if not svc:
        return {"ok": False, "error": "Lead service unavailable"}

    lead = svc.create_lead(lead_data)
    if not lead:
        return {"ok": False, "error": "Lead create failed"}

    lead_id = int(lead.get("id") or 0)
    if lead_id <= 0:
        return {"ok": False, "error": "Invalid lead id"}

    # Immediately bind/merge identity so FB rows don't create split contacts.
    lead_id = _bind_lead_identity(
        lead_id,
        phone=_normalize_phone(lead.get("phone", "")),
        email_addr=_normalize_email(lead.get("email", "")),
        contact_name=_lead_display_name(lead),
        source="facebook_sheet",
        source_detail=(lead_data.get("source_detail", "") or "Google Sheet FB lead ingest"),
    )
    try:
        refreshed = svc.get_lead(lead_id)
        if refreshed:
            lead = refreshed
    except Exception:
        pass

    name = _lead_display_name(lead)
    phone = _normalize_phone(lead.get("phone", ""))
    email = _normalize_email(lead.get("email", ""))
    deduped = bool(lead.get("_deduplicated"))

    # CAPI lead signal for FB sheet ingests (feed Meta higher-quality server-side events).
    try:
        from integrations.meta_capi import fire_lead_event

        fire_lead_event(
            email=email,
            phone=phone,
            first_name=lead.get("first_name", ""),
            last_name=lead.get("last_name", ""),
            event_type=lead.get("event_type", "") or "facebook_sheet_lead",
            event_source_url="https://zoarbathroomrental.com",
            fbc=lead_data.get("fbc", ""),
            fbp=lead_data.get("fbp", ""),
        )
    except Exception as e:
        print(f"[GoogleSheetCRM] CAPI lead event error: {e}")

    cfg = load_config()
    alert = f"📘 New FB Lead: {name} | {phone or 'no phone'} | {email or 'no email'}"

    try:
        from telegram.bot import get_bot as _get_tg_bot

        tg = _get_tg_bot()
        if tg:
            await tg.send_to_all(alert)
    except Exception as e:
        print(f"[GoogleSheetCRM] Telegram alert error: {e}")

    if _is_truthy(cfg.get("google_sheet_crm_notify_sms", True), True):
        pipeline = get_pipeline()
        if pipeline:
            try:
                await pipeline._notify_kai_sms(alert[:160])
            except Exception as e:
                print(f"[GoogleSheetCRM] Kai SMS alert error: {e}")

    await broadcast(
        {
            "type": "lead_event",
            "event": "google_sheet_fb_lead",
            "lead_id": lead_id,
            "name": name,
            "phone": phone,
            "email": email,
            "deduplicated": deduped,
            "row": row_num,
        }
    )

    first_touch = await _run_google_sheet_first_touch(lead, item)

    if _is_truthy(cfg.get("google_sheet_crm_enroll_followups", True), True):
        try:
            from core.follow_up_engine import get_follow_up_engine

            engine = get_follow_up_engine()
            if engine:
                engine.enroll_lead(lead_id, "facebook")
        except Exception as e:
            print(f"[GoogleSheetCRM] Follow-up enroll error: {e}")

    if _is_truthy(cfg.get("google_sheet_crm_enqueue_pipeline", False), False) and not deduped:
        pipeline = get_pipeline()
        if pipeline:
            try:
                asyncio.create_task(pipeline.process_new_lead(lead_id))
            except Exception as e:
                print(f"[GoogleSheetCRM] Pipeline enqueue error: {e}")

    return {"ok": True, "lead_id": lead_id, "deduplicated": deduped, "first_touch": first_touch}


async def _start_google_sheet_crm_sync(cfg: dict, force_restart: bool = False):
    global _sheet_crm_watcher, _sheet_crm_task

    enabled = _is_truthy(cfg.get("google_sheet_crm_enabled", True), True)
    if not enabled:
        if _sheet_crm_watcher:
            _sheet_crm_watcher.stop()
        _sheet_crm_watcher = None
        _sheet_crm_task = None
        print("[GoogleSheetCRM] Sync disabled (google_sheet_crm_enabled=false)")
        return

    if force_restart and _sheet_crm_watcher:
        try:
            _sheet_crm_watcher.stop()
        except Exception:
            pass
        _sheet_crm_watcher = None
        _sheet_crm_task = None

    if _sheet_crm_task and not _sheet_crm_task.done():
        return

    try:
        from integrations.google_sheets_crm import init_google_sheets_crm

        _sheet_crm_watcher = init_google_sheets_crm(cfg, _handle_google_sheet_crm_row)
        _sheet_crm_task = _sheet_crm_watcher.start()
        print(
            f"[GoogleSheetCRM] Watcher started "
            f"(sheet='{_sheet_crm_watcher.sheet_name}', poll={_sheet_crm_watcher.poll_interval_seconds}s)"
        )
    except Exception as e:
        print(f"[GoogleSheetCRM] Startup error: {e}")

@app.post("/api/browser/login-gv")
async def browser_login_gv():
    """Launch headed browser for user to log into Google Voice manually."""
    gv = await _ensure_gv()
    if not gv:
        return JSONResponse({"error": "Google Voice not initialized. Make sure Playwright is installed."}, status_code=400)
    try:
        await broadcast({"type": "ghl_event", "message": "Opening Google Voice login browser..."})
        ok = await gv.manual_login()
        if ok:
            await broadcast({"type": "ghl_event", "message": "Google Voice login successful! Session saved."})
            return JSONResponse({"ok": True, "message": "Login successful, session saved"})
        else:
            return JSONResponse({"ok": False, "error": "Login timed out or failed"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@app.get("/api/browser/status")
async def browser_status():
    """Check login status for GHL, Google Voice, and Higgsfield browsers."""
    from integrations.ghl import get_ghl
    from integrations.google_voice import get_gv
    from integrations.higgsfield import get_hf
    ghl = get_ghl()
    gv = get_gv()
    hf = get_hf()
    return JSONResponse({
        "ghl": {"initialized": bool(ghl), "logged_in": ghl.logged_in if ghl else False},
        "gv": {"initialized": bool(gv), "logged_in": gv.logged_in if gv else False},
        "hf": {"initialized": bool(hf), "logged_in": hf.logged_in if hf else False},
    })

# ── Higgsfield API endpoints ─────────────────────────────────────────────────

@app.post("/api/higgsfield/login")
async def higgsfield_login():
    """Launch headed browser for user to log into Higgsfield via Google."""
    from integrations.higgsfield import get_hf, init_higgsfield
    hf = get_hf()
    if not hf:
        hf = await init_higgsfield()
    if not hf:
        return JSONResponse({"error": "Failed to init Higgsfield bot"}, status_code=500)
    try:
        await broadcast({"type": "hf_event", "message": "Opening Higgsfield login browser..."})
        ok = await hf.manual_login()
        if ok:
            await broadcast({"type": "hf_event", "message": "Higgsfield login successful!"})
            return JSONResponse({"ok": True})
        return JSONResponse({"ok": False, "error": "Login timed out"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@app.get("/api/higgsfield/status")
async def higgsfield_status():
    """Get Higgsfield bot status and generation progress."""
    from integrations.higgsfield import get_hf
    hf = get_hf()
    if not hf:
        return JSONResponse({"state": "not_initialized", "logged_in": False})
    status = hf.get_status()
    status["logged_in"] = hf.logged_in
    return JSONResponse(status)

@app.post("/api/higgsfield/start")
async def higgsfield_start(request: Request):
    """Start Higgsfield image generation. Body: {scenes, ref_image, images_per_frame}."""
    from integrations.higgsfield import get_hf
    hf = get_hf()
    if not hf or not hf.logged_in:
        return JSONResponse({"error": "Not logged in to Higgsfield"}, status_code=400)
    body = await request.json()
    scenes = body.get("scenes", [])
    ref_image = body.get("ref_image", "")
    images_per_frame = body.get("images_per_frame", 30)
    if not scenes:
        return JSONResponse({"error": "No scenes provided"}, status_code=400)
    asyncio.create_task(_run_hf_generation(hf, scenes, ref_image, images_per_frame))
    return JSONResponse({"ok": True, "message": f"Started generation for {len(scenes)} scenes"})

async def _run_hf_generation(hf, scenes, ref_image, images_per_frame):
    """Background wrapper for Higgsfield generation with SSE broadcasting."""
    await hf.start_generation(scenes, ref_image, images_per_frame, broadcast_fn=broadcast)

@app.post("/api/higgsfield/stop")
async def higgsfield_stop():
    """Stop Higgsfield image generation."""
    from integrations.higgsfield import get_hf
    hf = get_hf()
    if hf:
        await hf.stop()
        return JSONResponse({"ok": True})
    return JSONResponse({"error": "Not initialized"}, status_code=400)

@app.post("/api/higgsfield/upload-image")
async def higgsfield_upload_image(request: Request):
    """Upload a reference image for Higgsfield. Body: {filename, data (base64)}."""
    import base64 as b64
    body = await request.json()
    filename = body.get("filename", "reference.png")
    data = body.get("image_data", "") or body.get("data", "")
    if not data:
        return JSONResponse({"error": "No image data"}, status_code=400)
    if "," in data:
        data = data.split(",", 1)[1]
    img_dir = Path.home() / ".nexus" / "higgsfield"
    img_dir.mkdir(parents=True, exist_ok=True)
    img_path = img_dir / filename
    img_path.write_bytes(b64.b64decode(data))
    return JSONResponse({"ok": True, "path": str(img_path)})

@app.get("/api/higgsfield/images")
async def higgsfield_images():
    """Get list of generated images."""
    from integrations.higgsfield import get_hf
    hf = get_hf()
    if not hf:
        return JSONResponse({"images": []})
    return JSONResponse({"images": hf.get_status().get("generated_images", [])})

@app.post("/api/higgsfield/delete-image")
async def higgsfield_delete_image(request: Request):
    """Delete a generated image (user rejected it). Body: {scene, frame, index}."""
    from integrations.higgsfield import get_hf
    hf = get_hf()
    if not hf:
        return JSONResponse({"error": "Not initialized"}, status_code=400)
    body = await request.json()
    ok = hf.delete_image(body.get("scene", 0), body.get("frame", ""), body.get("index", 0))
    return JSONResponse({"ok": ok})

@app.get("/api/higgsfield/progress")
async def higgsfield_progress():
    """Get saved progress summary (for resume detection)."""
    from integrations.higgsfield import get_hf
    hf = get_hf()
    if not hf:
        return JSONResponse({"counts": {}, "total_images": 0})
    return JSONResponse(hf.get_progress_summary())

@app.post("/api/higgsfield/download-images")
async def higgsfield_download_images():
    """Download all generated images from Higgsfield history to local folders."""
    from integrations.higgsfield import get_hf
    hf = get_hf()
    if not hf:
        return JSONResponse({"error": "Not initialized"}, status_code=400)
    result = await hf.download_from_history_page()
    return JSONResponse(result)

@app.post("/api/higgsfield/refresh-images")
async def higgsfield_refresh_images():
    """Scrape image URLs from Higgsfield history and backfill progress records."""
    from integrations.higgsfield import get_hf, init_higgsfield
    hf = get_hf()
    if not hf:
        hf = await init_higgsfield()
    if not hf:
        return JSONResponse({"error": "Failed to initialize"}, status_code=500)
    result = await hf.refresh_image_urls()
    return JSONResponse(result)

@app.get("/api/higgsfield/local-image")
async def higgsfield_local_image(path: str = ""):
    """Serve a locally downloaded Higgsfield image."""
    from fastapi.responses import FileResponse
    if not path:
        return JSONResponse({"error": "No path"}, status_code=400)
    img_path = Path(path)
    hf_dir = Path.home() / ".nexus" / "higgsfield"
    # Security: only serve files under the HF storage directory
    try:
        img_path.resolve().relative_to(hf_dir.resolve())
    except ValueError:
        return JSONResponse({"error": "Invalid path"}, status_code=403)
    if img_path.exists():
        return FileResponse(str(img_path))
    return JSONResponse({"error": "Not found"}, status_code=404)

@app.get("/api/higgsfield/proxy-image")
async def higgsfield_proxy_image(url: str = ""):
    """Proxy a remote HF CDN image. Caches to disk for instant subsequent loads."""
    from fastapi.responses import Response, FileResponse
    from urllib.request import urlopen, Request as URLRequest
    import hashlib
    if not url or not url.startswith("http"):
        return JSONResponse({"error": "Invalid URL"}, status_code=400)
    # Check disk cache first
    cache_dir = Path.home() / ".nexus" / "higgsfield" / "img_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    url_hash = hashlib.md5(url.encode()).hexdigest()
    cached = cache_dir / f"{url_hash}.jpg"
    if cached.exists():
        return FileResponse(str(cached), media_type="image/jpeg",
                           headers={"Cache-Control": "public, max-age=604800"})
    try:
        req = URLRequest(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://higgsfield.ai/"})
        with urlopen(req, timeout=15) as resp:
            data = resp.read()
            ct = resp.headers.get("Content-Type", "image/jpeg")
        # Save to disk cache
        cached.write_bytes(data)
        return Response(content=data, media_type=ct, headers={"Cache-Control": "public, max-age=604800"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)

@app.post("/api/higgsfield/generate-end-frames")
async def higgsfield_generate_end_frames(request: Request):
    """Generate end frames for a scene using a liked start frame as reference.
    Body: {scene_idx, scene_desc, liked_frame_path, images_per_frame}."""
    from integrations.higgsfield import get_hf
    hf = get_hf()
    if not hf or not hf.logged_in:
        return JSONResponse({"error": "Not logged in"}, status_code=400)
    body = await request.json()
    scene_idx = body.get("scene_idx", 1)
    scene_desc = body.get("scene_desc", "")
    liked_frame_path = body.get("liked_frame_path", "")
    images_per_frame = body.get("images_per_frame", 30)
    if not liked_frame_path:
        return JSONResponse({"error": "No liked frame image provided"}, status_code=400)
    asyncio.create_task(
        hf.generate_end_frames(scene_idx, scene_desc, liked_frame_path,
                                images_per_frame, broadcast_fn=broadcast)
    )
    return JSONResponse({"ok": True, "message": f"Started end frame generation for scene {scene_idx}"})

@app.post("/api/higgsfield/upload-liked-frame")
async def higgsfield_upload_liked_frame(request: Request):
    """Upload a liked start frame to use as reference for end frame generation.
    Body: {scene_idx, filename, data (base64)}."""
    import base64 as b64
    body = await request.json()
    scene_idx = body.get("scene_idx", 1)
    filename = body.get("filename", f"liked_scene_{scene_idx}.png")
    data = body.get("image_data", "") or body.get("data", "")
    if not data:
        return JSONResponse({"error": "No image data"}, status_code=400)
    if "," in data:
        data = data.split(",", 1)[1]
    img_dir = Path.home() / ".nexus" / "higgsfield" / "liked_frames"
    img_dir.mkdir(parents=True, exist_ok=True)
    img_path = img_dir / filename
    img_path.write_bytes(b64.b64decode(data))
    return JSONResponse({"ok": True, "path": str(img_path)})

@app.post("/api/higgsfield/analyze")
async def higgsfield_analyze(request: Request):
    """Analyze generated images for quality/realism. Body: {scene (optional), count (optional)}"""
    from integrations.higgsfield import get_hf, init_higgsfield, PROGRESS_FILE
    hf = get_hf()
    if not hf:
        hf = await init_higgsfield()
    if not hf:
        return JSONResponse({"error": "Failed to initialize"}, status_code=500)
    body = await request.json()
    scene_filter = body.get("scene")
    max_count = body.get("count", 10)

    # Load progress and find images to analyze
    if not PROGRESS_FILE.exists():
        return JSONResponse({"error": "No progress data"}, status_code=400)
    data = json.loads(PROGRESS_FILE.read_text())
    completed = data.get("completed", {})
    scenes = data.get("scenes", [])

    images_to_analyze = []
    for k, imgs in completed.items():
        for img in imgs:
            if not img.get("local_path"):
                continue
            if img.get("quality_score") is not None:
                continue  # Already analyzed
            if scene_filter and img.get("scene") != scene_filter:
                continue
            images_to_analyze.append(img)
            if len(images_to_analyze) >= max_count:
                break
        if len(images_to_analyze) >= max_count:
            break

    if not images_to_analyze:
        return JSONResponse({"error": "No unanalyzed cached images found. Click Refresh Images first.", "count": 0})

    # Get scene desc for first image
    scene_idx = images_to_analyze[0].get("scene", 1) - 1
    scene_desc = scenes[scene_idx] if scene_idx < len(scenes) else "Luxury event scene"

    result = await hf.analyze_batch(images_to_analyze, scene_desc)

    # Persist updated scores
    for a in result.get("analyses", []):
        for k, imgs in completed.items():
            for img in imgs:
                if img.get("scene") == a.get("scene") and img.get("frame") == a.get("frame") and img.get("index") == a.get("index"):
                    img["quality_score"] = a.get("score", 0)
                    img["verdict"] = a.get("verdict", "keep")
                    img["issues"] = a.get("realism_issues", [])[:3]
    data["completed"] = completed
    if result.get("feedback_summary"):
        data["feedback"] = result["feedback_summary"]
    PROGRESS_FILE.write_text(json.dumps(data, indent=2))

    # Update in-memory status
    for a in result.get("analyses", []):
        for rec in hf._status.get("generated_images", []):
            if rec.get("scene") == a.get("scene") and rec.get("frame") == a.get("frame") and rec.get("index") == a.get("index"):
                rec["quality_score"] = a.get("score", 0)
                rec["verdict"] = a.get("verdict", "keep")
                break

    return JSONResponse({
        "ok": True,
        "analyzed": len(result.get("analyses", [])),
        "avg_score": round(result.get("avg_score", 0), 1),
        "feedback": result.get("feedback_summary", ""),
        "analyses": result.get("analyses", []),
    })

SCRIPT_HISTORY_FILE = Path.home() / ".nexus" / "higgsfield" / "script_history.json"

@app.get("/api/higgsfield/script-history")
async def higgsfield_script_history_get():
    """Get saved script history."""
    if SCRIPT_HISTORY_FILE.exists():
        try:
            data = json.loads(SCRIPT_HISTORY_FILE.read_text())
            return JSONResponse({"scripts": data})
        except Exception:
            pass
    return JSONResponse({"scripts": []})

@app.post("/api/higgsfield/script-history")
async def higgsfield_script_history_save(request: Request):
    """Save a script to history. Body: {name, content}."""
    body = await request.json()
    name = body.get("name", "Untitled")
    content = body.get("content", "")
    if not content:
        return JSONResponse({"error": "No content"}, status_code=400)
    SCRIPT_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    scripts = []
    if SCRIPT_HISTORY_FILE.exists():
        try:
            scripts = json.loads(SCRIPT_HISTORY_FILE.read_text())
        except Exception:
            pass
    scenes = [s.strip() for s in content.split("\n\n") if s.strip()]
    scripts.insert(0, {
        "name": name,
        "content": content,
        "scene_count": len(scenes),
        "created_at": datetime.now().isoformat(),
    })
    # Keep last 20
    scripts = scripts[:20]
    SCRIPT_HISTORY_FILE.write_text(json.dumps(scripts, indent=2))
    return JSONResponse({"ok": True})

@app.delete("/api/higgsfield/script-history")
async def higgsfield_script_history_delete(request: Request):
    """Delete a saved script by index. Body: {index}."""
    body = await request.json()
    idx = body.get("index", -1)
    if not SCRIPT_HISTORY_FILE.exists():
        return JSONResponse({"error": "No history"}, status_code=400)
    try:
        scripts = json.loads(SCRIPT_HISTORY_FILE.read_text())
        if 0 <= idx < len(scripts):
            scripts.pop(idx)
            SCRIPT_HISTORY_FILE.write_text(json.dumps(scripts, indent=2))
            return JSONResponse({"ok": True})
    except Exception:
        pass
    return JSONResponse({"error": "Invalid index"}, status_code=400)

@app.post("/api/higgsfield/use-as-end-ref")
async def higgsfield_use_as_end_ref(request: Request):
    """Use a generated image as the reference for end frame generation.
    Copies the cached/downloaded image to liked_frames dir.
    Body: {scene, frame, index}."""
    import shutil
    body = await request.json()
    scene = body.get("scene", 0)
    frame = body.get("frame", "start")
    index = body.get("index", 0)
    # Find the image in progress data
    progress_file = Path.home() / ".nexus" / "higgsfield" / "progress.json"
    if not progress_file.exists():
        return JSONResponse({"error": "No progress data"}, status_code=400)
    try:
        data = json.loads(progress_file.read_text())
        completed = data.get("completed", {})
        key = f"{scene}_{frame}"
        images = completed.get(key, [])
        img_record = next((i for i in images if i.get("index") == index), None)
        if not img_record:
            return JSONResponse({"error": "Image not found in progress"}, status_code=404)
        # Get source: prefer local_path, fall back to downloading from URL
        src = img_record.get("local_path", "")
        if not src or not Path(src).exists():
            url = img_record.get("url", "")
            if not url:
                return JSONResponse({"error": "No image source available"}, status_code=400)
            # Download to liked frames
            from urllib.request import urlopen, Request as URLRequest
            liked_dir = Path.home() / ".nexus" / "higgsfield" / "liked_frames"
            liked_dir.mkdir(parents=True, exist_ok=True)
            dest = liked_dir / f"scene{scene}_{frame}_{index}.jpg"
            req = URLRequest(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://higgsfield.ai/"})
            with urlopen(req, timeout=20) as resp:
                dest.write_bytes(resp.read())
            return JSONResponse({"ok": True, "path": str(dest)})
        else:
            liked_dir = Path.home() / ".nexus" / "higgsfield" / "liked_frames"
            liked_dir.mkdir(parents=True, exist_ok=True)
            dest = liked_dir / f"scene{scene}_{frame}_{index}.jpg"
            shutil.copy2(src, dest)
            return JSONResponse({"ok": True, "path": str(dest)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ── Static Ad Generation API ──────────────────────────────────────────────────

@app.get("/api/higgsfield/ad-campaigns")
async def higgsfield_ad_campaigns(category: str = ""):
    """Get available ad campaign prompts. Optional category filter."""
    from core.ad_prompts import get_campaigns_by_category, get_categories
    campaigns = get_campaigns_by_category(category or None)
    # Strip full prompts from list view (they're long)
    slim = []
    for c in campaigns:
        slim.append({
            "id": c["id"],
            "category": c["category"],
            "concept": c["concept"],
            "headline": c["headline"],
            "cta": c["cta"],
            "subline": c.get("subline", ""),
            "palette": c.get("palette", ""),
            "target": c.get("target", ""),
        })
    return JSONResponse({"campaigns": slim, "categories": get_categories()})

@app.post("/api/higgsfield/start-static-ads")
async def higgsfield_start_static_ads(request: Request):
    """Start static ad image generation.
    Body: {campaign_ids: ["all"] or ["wedding_garden_golden", ...],
           ref_image: path, images_per_campaign: 4}"""
    from integrations.higgsfield import get_hf
    hf = get_hf()
    if not hf or not hf.logged_in:
        return JSONResponse({"error": "Not logged in to Higgsfield"}, status_code=400)
    body = await request.json()
    campaign_ids = body.get("campaign_ids", ["all"])
    ref_image = body.get("ref_image", "")
    images_per = body.get("images_per_campaign", 4)
    if not ref_image:
        return JSONResponse({"error": "No reference image path"}, status_code=400)

    async def broadcast(msg):
        from server import broadcast_sse
        await broadcast_sse(msg)

    async def _run_static_ads():
        """Wrapper to catch and log all errors from the async task."""
        import traceback as tb
        log_path = Path.home() / ".nexus" / "higgsfield" / "hf_debug.log"
        def _log(msg):
            with open(log_path, "a") as f:
                f.write(f"{datetime.now().strftime('%H:%M:%S')} {msg}\n")
            print(f"[HF-AD] {msg}")
        try:
            _log(f"Task starting: campaigns={campaign_ids}, ref={ref_image}, per={images_per}")
            result = await hf.start_static_ads(
                campaign_ids, ref_image, images_per, broadcast_fn=broadcast
            )
            _log(f"Task completed: generated={result.get('total_generated',0)}, overlaid={result.get('total_overlaid',0)}")
        except Exception as e:
            _log(f"TASK ERROR: {e}")
            _log(tb.format_exc())
            hf._running = False
            hf._status["state"] = "error"
            hf._status["error"] = str(e)

    asyncio.create_task(_run_static_ads())
    return JSONResponse({"ok": True, "message": f"Starting static ads: {len(campaign_ids)} campaigns × {images_per} images"})

@app.get("/api/higgsfield/debug-log")
async def higgsfield_debug_log():
    """Return recent debug log lines for troubleshooting."""
    log_path = Path.home() / ".nexus" / "higgsfield" / "hf_debug.log"
    if not log_path.exists():
        return JSONResponse({"lines": [], "message": "No log file yet"})
    lines = log_path.read_text().strip().split("\n")
    return JSONResponse({"lines": lines[-80:], "count": len(lines)})

@app.get("/api/higgsfield/static-ads")
async def higgsfield_static_ads():
    """Get list of generated static ad images."""
    ad_dir = Path.home() / ".nexus" / "higgsfield" / "static_ads"
    if not ad_dir.exists():
        return JSONResponse({"images": [], "count": 0})
    images = []
    for f in sorted(ad_dir.glob("*.png")):
        images.append({
            "path": str(f),
            "name": f.stem,
            "size": f.stat().st_size,
            "campaign": f.stem.rsplit("_", 1)[0] if "_" in f.stem else f.stem,
        })
    return JSONResponse({"images": images, "count": len(images), "dir": str(ad_dir)})

@app.get("/api/higgsfield/static-ad-image")
async def higgsfield_static_ad_image(path: str = ""):
    """Serve a generated static ad image."""
    from fastapi.responses import FileResponse
    if not path:
        return JSONResponse({"error": "No path"}, status_code=400)
    img_path = Path(path)
    ad_dir = Path.home() / ".nexus" / "higgsfield" / "static_ads"
    raw_dir = Path.home() / ".nexus" / "higgsfield" / "static_ads_raw"
    try:
        resolved = img_path.resolve()
        if not (str(resolved).startswith(str(ad_dir.resolve())) or str(resolved).startswith(str(raw_dir.resolve()))):
            return JSONResponse({"error": "Access denied"}, status_code=403)
    except Exception:
        return JSONResponse({"error": "Invalid path"}, status_code=400)
    if img_path.exists():
        return FileResponse(str(img_path))
    return JSONResponse({"error": "Not found"}, status_code=404)

@app.post("/api/higgsfield/apply-overlay")
async def higgsfield_apply_overlay(request: Request):
    """Apply CTA overlay to an existing image.
    Body: {image_path, headline, cta, subline, palette, campaign_id}"""
    from core.cta_overlay import apply_cta_overlay
    body = await request.json()
    image_path = body.get("image_path", "")
    if not image_path or not Path(image_path).exists():
        return JSONResponse({"error": "Image not found"}, status_code=400)
    try:
        result = apply_cta_overlay(
            image_path=image_path,
            headline=body.get("headline", "Your guests remember comfort."),
            cta_text=body.get("cta", "Get Event Pricing"),
            subline=body.get("subline", ""),
            palette=body.get("palette", "deep_navy"),
            campaign_id=body.get("campaign_id", "custom"),
        )
        return JSONResponse({"ok": True, "path": result})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ── Lead Pipeline API endpoints ──────────────────────────────────────────────
@app.get("/api/leads")
async def api_get_leads(status: str = "", limit: int = 50):
    from core.lead_pipeline import get_pipeline
    p = get_pipeline()
    if not p:
        return JSONResponse({"error": "Pipeline not initialized"}, status_code=400)
    leads = p.get_leads(status=status or None, limit=limit)
    # Normalize fields: DB uses full_name/first_name/last_name, frontend expects name
    for lead in leads:
        if not lead.get("name"):
            lead["name"] = lead.get("full_name") or ("%s %s" % (
                lead.get("first_name", ""), lead.get("last_name", "")
            )).strip() or "Unknown"
    stats = p.get_stats()
    return JSONResponse({"leads": leads, "stats": stats})

@app.get("/api/leads/stats")
async def api_lead_stats():
    from core.lead_pipeline import get_pipeline
    p = get_pipeline()
    if not p:
        return JSONResponse({"error": "Pipeline not initialized"}, status_code=400)
    return JSONResponse(p.get_stats())

@app.get("/api/leads/{lead_id}")
async def api_get_lead(lead_id: int):
    from core.lead_pipeline import get_pipeline
    p = get_pipeline()
    if not p:
        return JSONResponse({"error": "Pipeline not initialized"}, status_code=400)
    lead = p.get_lead(lead_id)
    if not lead:
        return JSONResponse({"error": "Lead not found"}, status_code=404)
    if not lead.get("name"):
        lead["name"] = lead.get("full_name") or ("%s %s" % (
            lead.get("first_name", ""), lead.get("last_name", "")
        )).strip() or "Unknown"
    messages = p.get_lead_messages(lead_id)
    return JSONResponse({"lead": lead, "messages": messages})

@app.post("/api/leads/{lead_id}/action")
async def api_lead_action(lead_id: int, request: Request):
    """Trigger pipeline actions on a lead: process_new, follow_up, mark_replied, opt_out."""
    from core.lead_pipeline import get_pipeline
    p = get_pipeline()
    if not p:
        return JSONResponse({"error": "Pipeline not initialized"}, status_code=400)
    lead = p.get_lead(lead_id)
    if not lead:
        return JSONResponse({"error": "Lead not found"}, status_code=404)
    body = await request.json()
    action = body.get("action", "")
    try:
        if action == "process_new":
            await p.process_new_lead(lead_id)
        elif action == "follow_up":
            num = body.get("num", lead.get("follow_up_count", 0) + 1)
            await p.process_follow_up(lead_id, num)
        elif action == "mark_replied":
            await p.process_reply(lead_id, "manual", body.get("message", "Marked as replied by user"))
        elif action == "opt_out":
            p.update_lead(lead_id, status="opted_out")
            p._log_event(lead_id, "opt_out", "Manually opted out by user")
        elif action == "close":
            p.update_lead(lead_id, status="closed")
            p._log_event(lead_id, "closed", "Manually closed by user")
        elif action == "reopen":
            p.update_lead(lead_id, status="new")
            p._log_event(lead_id, "reopened", "Reopened by user")
        else:
            return JSONResponse({"error": f"Unknown action: {action}"}, status_code=400)
        return JSONResponse({"ok": True, "action": action, "lead_id": lead_id})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@app.post("/api/leads/add")
async def api_add_lead(request: Request):
    """Manually add a lead to the pipeline."""
    from core.lead_pipeline import get_pipeline
    p = get_pipeline()
    if not p:
        return JSONResponse({"error": "Pipeline not initialized"}, status_code=400)
    body = await request.json()
    lead_id = p.add_lead_manual(
        first_name=body.get("first_name", ""),
        last_name=body.get("last_name", ""),
        phone=body.get("phone", ""),
        email=body.get("email", ""),
        carrier=body.get("carrier", "tmobile"),
        source=body.get("source", "manual"),
        notes=body.get("notes", ""),
    )
    return JSONResponse({"ok": True, "lead_id": lead_id})

# ── CRM API endpoints ────────────────────────────────────────────────────────
@app.get("/api/crm/leads")
async def crm_get_leads(q: str = "", status: str = "", sort: str = "updated_at",
                        limit: int = 25, offset: int = 0):
    from core.services import get_lead_service
    svc = get_lead_service()
    if svc:
        leads, total = svc.search_leads(query=q, booking_status=status, sort=sort,
                                        limit=limit, offset=offset)
    else:
        leads, total = _crm_search_leads_fallback(q=q, status=status, sort=sort, limit=limit, offset=offset)
    return JSONResponse({"leads": leads, "total": total, "limit": limit, "offset": offset})

@app.get("/api/crm/leads/pipeline")
async def crm_pipeline_view():
    """Get all leads grouped by booking_status for kanban view."""
    from core.services import get_lead_service
    svc = get_lead_service()
    if svc:
        from core.services.pipeline_service import STAGES

        pipeline = {}
        for stage_key in STAGES:
            leads, _ = svc.search_leads(booking_status=stage_key, limit=100, offset=0)
            pipeline[stage_key] = leads
    else:
        pipeline = _crm_pipeline_fallback()
    return JSONResponse({"pipeline": pipeline})

@app.get("/api/crm/conversations")
async def crm_conversations(q: str = "", limit: int = 50, offset: int = 0):
    """Get conversations (deduped by shared email/phone), ordered by recent activity."""
    import sqlite3 as _sq
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    conn = _sq.connect(str(Path.home() / ".nexus" / "memory.db"))
    conn.row_factory = _sq.Row
    _ensure_conversation_reads_table(conn)

    where_clause = ""
    params = []
    if q:
        where_clause = (
            "AND (l.full_name LIKE ? OR l.first_name LIKE ? OR l.last_name LIKE ? "
            "OR l.phone LIKE ? OR l.email LIKE ? OR l.source LIKE ? OR l.source_detail LIKE ?)"
        )
        qp = f"%{q}%"
        params = [qp, qp, qp, qp, qp, qp, qp]

    rows = conn.execute(f"""
        SELECT l.id as lead_id, l.first_name, l.last_name, l.full_name,
               l.phone, l.email, l.booking_status, l.business_name,
               l.source, l.source_detail, l.source_group_name,
               m.content as last_message, m.ts as last_message_ts,
               m.channel as last_channel, m.direction as last_direction,
               (SELECT COUNT(*) FROM lead_messages WHERE lead_id = l.id) as message_count,
               (SELECT COUNT(*) FROM lead_messages WHERE lead_id = l.id AND LOWER(COALESCE(channel,'')) = 'sms') as sms_count,
               (SELECT COUNT(*) FROM lead_messages WHERE lead_id = l.id AND LOWER(COALESCE(channel,'')) = 'email') as email_count,
               (SELECT COUNT(*) FROM lead_messages
                  WHERE lead_id = l.id
                    AND LOWER(COALESCE(direction,'')) = 'inbound'
                    AND (COALESCE(r.last_read_ts, '') = '' OR ts > r.last_read_ts)
               ) as unread_count
        FROM leads l
        LEFT JOIN lead_conversation_reads r ON r.lead_id = l.id
        INNER JOIN lead_messages m ON m.id = (
            SELECT id FROM lead_messages WHERE lead_id = l.id ORDER BY ts DESC, id DESC LIMIT 1
        )
        WHERE 1=1 {where_clause}
        ORDER BY m.ts DESC, l.id DESC
    """, params).fetchall()
    conn.close()

    raw = [dict(r) for r in rows]
    if not raw:
        return JSONResponse({"conversations": [], "total": 0})

    parent = list(range(len(raw)))

    def _find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def _union(a: int, b: int):
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[rb] = ra

    email_idx: dict[str, int] = {}
    phone_idx: dict[str, int] = {}
    for idx, row in enumerate(raw):
        email_key = _normalize_email(row.get("email", ""))
        phone_key = _normalize_phone(row.get("phone", ""))
        alias_phone_key = _phone_from_email_alias(email_key)
        if email_key:
            if email_key in email_idx:
                _union(idx, email_idx[email_key])
            else:
                email_idx[email_key] = idx
        if phone_key:
            if phone_key in phone_idx:
                _union(idx, phone_idx[phone_key])
            else:
                phone_idx[phone_key] = idx
        if alias_phone_key:
            if alias_phone_key in phone_idx:
                _union(idx, phone_idx[alias_phone_key])
            else:
                phone_idx[alias_phone_key] = idx

    groups: dict[int, list[dict]] = {}
    for idx, row in enumerate(raw):
        groups.setdefault(_find(idx), []).append(row)

    collapsed: list[dict] = []
    for group in groups.values():
        group_sorted = sorted(
            group,
            key=lambda r: (str(r.get("last_message_ts", "")), int(r.get("lead_id", 0))),
            reverse=True,
        )
        latest = dict(group_sorted[0])
        canonical = _pick_best_lead_row(group_sorted) or latest
        primary = dict(canonical)
        for key in ("last_message", "last_message_ts", "last_channel", "last_direction", "last_message_at"):
            primary[key] = latest.get(key, primary.get(key))

        best_name = ""
        for row in group_sorted:
            candidate = _best_display_name(
                row.get("full_name", ""),
                row.get("first_name", ""),
                row.get("last_name", ""),
                row.get("email", ""),
                row.get("phone", ""),
            )
            if candidate and candidate != "Unknown":
                best_name = candidate
                if str(row.get("source", "")).lower().startswith("facebook"):
                    break
        if not best_name:
            best_name = _best_display_name(
                primary.get("full_name", ""),
                primary.get("first_name", ""),
                primary.get("last_name", ""),
                primary.get("email", ""),
                primary.get("phone", ""),
            )

        preferred_source = primary
        for row in group_sorted:
            src = str(row.get("source", "")).lower()
            if src.startswith("facebook") or src.startswith("website") or src.startswith("referral"):
                preferred_source = row
                break

        primary["display_name"] = best_name
        primary["lead_name"] = best_name
        primary["full_name"] = best_name
        primary["source"] = preferred_source.get("source", primary.get("source", ""))
        primary["source_detail"] = preferred_source.get("source_detail", primary.get("source_detail", ""))
        primary["source_group_name"] = preferred_source.get("source_group_name", primary.get("source_group_name", ""))
        primary["message_count"] = sum(int(r.get("message_count") or 0) for r in group_sorted)
        primary["sms_count"] = sum(int(r.get("sms_count") or 0) for r in group_sorted)
        primary["email_count"] = sum(int(r.get("email_count") or 0) for r in group_sorted)
        primary["unread_count"] = sum(int(r.get("unread_count") or 0) for r in group_sorted)
        primary["cluster_lead_ids"] = sorted({int(r.get("lead_id", 0)) for r in group_sorted if r.get("lead_id")})

        if not primary.get("phone"):
            for row in group_sorted:
                if row.get("phone"):
                    primary["phone"] = row.get("phone")
                    break
        if not primary.get("phone"):
            for row in group_sorted:
                alias_phone = _phone_from_email_alias(row.get("email", ""))
                if alias_phone:
                    primary["phone"] = alias_phone
                    break
        if not primary.get("email"):
            for row in group_sorted:
                if row.get("email"):
                    primary["email"] = row.get("email")
                    break

        primary["last_message_at"] = primary.get("last_message_ts", "")

        collapsed.append(primary)

    collapsed.sort(
        key=lambda r: (str(r.get("last_message_ts", "")), int(r.get("lead_id", 0))),
        reverse=True,
    )

    if q:
        ql = q.strip().lower()
        collapsed = [
            c for c in collapsed
            if ql in str(c.get("display_name", "")).lower()
            or ql in str(c.get("phone", "")).lower()
            or ql in str(c.get("email", "")).lower()
            or ql in str(c.get("source", "")).lower()
            or ql in str(c.get("source_detail", "")).lower()
        ]

    total = len(collapsed)
    page = collapsed[offset:max(offset + limit, offset)]
    return JSONResponse({"conversations": page, "total": total})

@app.get("/api/crm/conversations/{lead_id}/messages")
async def crm_conversation_messages(lead_id: int, limit: int = 200, offset: int = 0, channel: str = "all"):
    """Get conversation messages for a lead, including merged identities (same email/phone)."""
    import sqlite3 as _sq
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    conn = _sq.connect(str(Path.home() / ".nexus" / "memory.db"))
    conn.row_factory = _sq.Row
    related_ids = _related_lead_ids(conn, lead_id)
    id_placeholders = ",".join("?" for _ in related_ids) or "?"
    ch = (channel or "all").strip().lower()
    channel_filter = " AND LOWER(COALESCE(channel,'')) = ?" if ch in ("sms", "email") else ""

    base_where = f"WHERE lead_id IN ({id_placeholders}){channel_filter}"
    params = list(related_ids)
    if ch in ("sms", "email"):
        params.append(ch)

    msgs = conn.execute(
        f"SELECT * FROM lead_messages {base_where} ORDER BY ts ASC, id ASC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    total = conn.execute(f"SELECT COUNT(*) FROM lead_messages {base_where}", params).fetchone()[0]
    conn.close()
    return JSONResponse({"messages": [dict(m) for m in msgs], "total": total, "lead_ids": related_ids})


@app.post("/api/crm/conversations/{lead_id}/read")
async def crm_mark_conversation_read(lead_id: int):
    """Mark a conversation as read (across merged lead identities)."""
    import sqlite3 as _sq
    conn = _sq.connect(str(Path.home() / ".nexus" / "memory.db"))
    conn.row_factory = _sq.Row
    _ensure_conversation_reads_table(conn)
    related_ids = _related_lead_ids(conn, lead_id)
    placeholders = ",".join("?" for _ in related_ids) or "?"
    last_ts = conn.execute(
        f"SELECT COALESCE(MAX(ts), '') FROM lead_messages WHERE lead_id IN ({placeholders})",
        related_ids,
    ).fetchone()[0]
    if not last_ts:
        last_ts = datetime.utcnow().isoformat(timespec="seconds")

    for rid in related_ids:
        conn.execute(
            """
            INSERT INTO lead_conversation_reads (lead_id, last_read_ts, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(lead_id) DO UPDATE SET
                last_read_ts = excluded.last_read_ts,
                updated_at = datetime('now')
            """,
            (int(rid), last_ts),
        )
    conn.commit()
    conn.close()
    return JSONResponse({"ok": True, "lead_ids": related_ids, "last_read_ts": last_ts})

@app.get("/api/crm/dashboard")
async def crm_dashboard():
    """Aggregated dashboard data: lead status, value, conversion, funnel, distribution."""
    import sqlite3 as _sq
    conn = _sq.connect(str(Path.home() / ".nexus" / "memory.db"))
    conn.row_factory = _sq.Row
    from core.services.pipeline_service import STAGES

    stage_rows = conn.execute("SELECT booking_status, COUNT(*) as cnt FROM leads GROUP BY booking_status").fetchall()
    stage_counts = {r["booking_status"]: r["cnt"] for r in stage_rows}
    total = sum(stage_counts.values())

    total_value = conn.execute(
        "SELECT COALESCE(SUM(total_quote_amount), 0) FROM leads WHERE total_quote_amount > 0"
    ).fetchone()[0]
    won_revenue = conn.execute(
        "SELECT COALESCE(SUM(total_quote_amount), 0) FROM leads WHERE booking_status IN ('delivered','booked') AND total_quote_amount > 0"
    ).fetchone()[0]
    won_count = conn.execute(
        "SELECT COUNT(*) FROM leads WHERE booking_status IN ('delivered','booked')"
    ).fetchone()[0]

    stage_order = list(STAGES.keys())
    funnel = []
    remaining = total
    for stage_key in stage_order:
        count = stage_counts.get(stage_key, 0)
        funnel.append({
            "stage": stage_key,
            "label": STAGES[stage_key]["label"],
            "count": count,
            "cumulative": remaining,
            "cumulative_pct": round((remaining / total * 100) if total else 0, 1),
            "next_step_pct": round((count / remaining * 100) if remaining else 0, 1),
        })
        remaining -= count

    conversion_pct = round((won_count / total * 100) if total else 0, 1)

    conn.close()
    return JSONResponse({
        "total_leads": total,
        "total_value": total_value,
        "won_revenue": won_revenue,
        "won_count": won_count,
        "conversion_pct": conversion_pct,
        "stage_counts": stage_counts,
        "funnel": funnel,
    })

# NOTE: Static sub-paths MUST be registered before {lead_id} catch-all
@app.get("/api/crm/leads/scores")
async def crm_lead_scores():
    """Score all active leads and return sorted by priority."""
    from core.lead_scoring import get_scorer
    scorer = get_scorer()
    summary = scorer.update_all_scores()
    leads = scorer.get_leads_by_tier()
    return JSONResponse({"leads": leads, "count": len(leads), "summary": summary})

@app.get("/api/crm/leads/priority-summary")
async def crm_priority_summary():
    """Get a summary of leads by priority level."""
    from core.lead_scoring import get_scorer
    scorer = get_scorer()
    summary = scorer.update_all_scores()
    return JSONResponse(summary)


@app.post("/api/crm/identity/reconcile", dependencies=[Depends(verify_action_auth)])
async def crm_identity_reconcile(request: Request):
    """Run hard identity reconciliation (phone/email alias merge)."""
    from core.lead_identity import reconcile_lead_identities

    body = await request.json() if request else {}
    limit_clusters = int(body.get("limit_clusters") or 0)
    dry_run = bool(body.get("dry_run", False))
    result = reconcile_lead_identities(limit_clusters=limit_clusters, dry_run=dry_run)
    return JSONResponse(result)


@app.post("/api/crm/identity/bind", dependencies=[Depends(verify_action_auth)])
async def crm_identity_bind(request: Request):
    """Bind identity fields to an existing lead and auto-merge duplicates."""
    from core.lead_identity import bind_identity_to_lead

    body = await request.json()
    lead_id = int(body.get("lead_id") or 0)
    if lead_id <= 0:
        return JSONResponse({"error": "lead_id required"}, status_code=400)
    result = bind_identity_to_lead(
        lead_id=lead_id,
        phone=(body.get("phone") or ""),
        email=(body.get("email") or ""),
        full_name=(body.get("full_name") or body.get("name") or ""),
        source=(body.get("source") or ""),
        source_detail=(body.get("source_detail") or ""),
        auto_merge=bool(body.get("auto_merge", True)),
    )
    return JSONResponse(result)


def _parse_ts_safe(ts: str):
    s = str(ts or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s.replace(" ", "T"))
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return None


def _is_true(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return str(v or "").strip().lower() in ("1", "true", "yes", "y", "on")


def _classify_lead_source(lead: dict) -> str:
    src = (lead.get("source_detail") or lead.get("source") or "").strip().lower()
    if not src:
        return "unknown"
    if "facebook" in src and "group" in src:
        return "facebook_group"
    if "facebook" in src:
        return "facebook_ad"
    if "google" in src and "map" in src:
        return "google_maps_scrape"
    if "cold" in src and "email" in src:
        return "cold_email"
    if "email_outbound" in src:
        return "cold_email"
    if "referral" in src:
        return "referral"
    if "instagram" in src:
        return "instagram"
    if "website" in src:
        return "website"
    if "google_voice" in src or "sms" in src:
        return "sms_inbound"
    return src.replace(" ", "_")


def _build_lead_profile(lead_id: int, lead: dict, related_ids: list[int] | None = None) -> dict:
    import sqlite3 as _sq
    conn = _sq.connect(str(Path.home() / ".nexus" / "memory.db"))
    conn.row_factory = _sq.Row
    rel_ids = sorted({int(x) for x in (related_ids or [lead_id]) if x})
    if not rel_ids:
        rel_ids = [int(lead_id)]
    placeholders = ",".join("?" for _ in rel_ids) or "?"
    msgs = conn.execute(
        f"SELECT lead_id, ts, direction, channel, content FROM lead_messages "
        f"WHERE lead_id IN ({placeholders}) ORDER BY ts ASC, id ASC",
        rel_ids,
    ).fetchall()
    conn.close()

    now = datetime.utcnow()
    outbound_count = 0
    inbound_count = 0
    outbound_channels = set()
    inbound_channels = set()
    first_outbound_ts = None
    second_outbound_ts = None
    first_inbound_ts = None
    latest_inbound_ts = None
    latest_inbound_text = ""
    follow_up_reply_ts = None
    follow_up_reply_text = ""

    for m in msgs:
        ts = _parse_ts_safe(m["ts"])
        direction = (m["direction"] or "").strip().lower()
        channel = (m["channel"] or "").strip().lower() or "unknown"
        content = m["content"] or ""
        if direction == "outbound":
            outbound_count += 1
            outbound_channels.add(channel)
            if not first_outbound_ts and ts:
                first_outbound_ts = ts
            elif first_outbound_ts and not second_outbound_ts and ts:
                second_outbound_ts = ts
        elif direction == "inbound":
            inbound_count += 1
            inbound_channels.add(channel)
            if not first_inbound_ts and ts:
                first_inbound_ts = ts
            if ts and (not latest_inbound_ts or ts >= latest_inbound_ts):
                latest_inbound_ts = ts
                latest_inbound_text = content
            if second_outbound_ts and ts and ts > second_outbound_ts:
                follow_up_reply_ts = ts
                follow_up_reply_text = content

    def _fmt_method(ch: str) -> str:
        mapping = {
            "sms": "SMS",
            "email": "Email",
            "call": "Call",
            "messenger": "Facebook DM",
            "facebook_dm": "Facebook DM",
            "instagram_dm": "Instagram DM",
        }
        return mapping.get(ch, ch.replace("_", " ").title())

    reply_platforms = sorted({_fmt_method(ch) for ch in inbound_channels})
    reached_out_methods = sorted({_fmt_method(ch) for ch in outbound_channels})
    did_reply = inbound_count > 0

    replied_to_primary = bool(
        first_outbound_ts
        and first_inbound_ts
        and (not second_outbound_ts or first_inbound_ts <= second_outbound_ts)
    )
    follow_up_sent_48h = outbound_count >= 2
    follow_up_due_48h = bool(
        first_outbound_ts
        and not did_reply
        and not follow_up_sent_48h
        and (now - first_outbound_ts) >= timedelta(hours=48)
    )

    override = (lead.get("lead_temperature_override") or "").strip().lower()
    if override in ("warm", "cold"):
        temperature = override
    elif did_reply:
        temperature = "warm"
    elif outbound_count >= 2:
        temperature = "cold"
    elif outbound_count == 1:
        temperature = "pending"
    else:
        temperature = "new"

    return {
        "lead_added_at": lead.get("date_added") or lead.get("discovered_at") or "",
        "where_found": lead.get("source_detail") or lead.get("source") or "",
        "source_classification": _classify_lead_source(lead),
        "facebook_group_name": lead.get("source_group_name") or "",
        "job_title": lead.get("job_title") or "",
        "venue_location": lead.get("venue_location") or "",
        "website": lead.get("website") or "",
        "did_reach_out": {
            "call": "call" in outbound_channels,
            "sms": "sms" in outbound_channels,
            "email": "email" in outbound_channels,
            "facebook_dm": ("facebook_dm" in outbound_channels or "messenger" in outbound_channels),
            "instagram_dm": "instagram_dm" in outbound_channels,
        },
        "reached_out_methods": reached_out_methods,
        "did_reply": did_reply,
        "reply_platforms": reply_platforms,
        "latest_reply_text": latest_inbound_text[:600],
        "latest_reply_at": latest_inbound_ts.isoformat() if latest_inbound_ts else "",
        "replied_to_primary_message": replied_to_primary,
        "warm_or_cold": temperature,
        "follow_up": {
            "auto_follow_up_blocked_due_to_reply": did_reply,
            "sent_48h": follow_up_sent_48h,
            "due_48h": follow_up_due_48h,
            "reply_after_follow_up": bool(follow_up_reply_ts),
            "reply_after_follow_up_text": follow_up_reply_text[:600],
        },
        "classification": {
            "is_vendor": _is_true(lead.get("is_vendor")),
            "vendor_category": lead.get("vendor_category") or "",
            "is_venue": _is_true(lead.get("is_venue")),
            "venue_name": lead.get("venue_name") or "",
        },
        "referral": {
            "joined_referral_program": _is_true(lead.get("joined_referral_program")),
            "partner_type": lead.get("referral_partner_type") or "",
            "referrals_given": int(lead.get("referrals_given_count") or 0),
            "referrals_booked": int(lead.get("referrals_booked_count") or 0),
            "payout_total": float(lead.get("referral_payout_total") or 0),
        },
        "metrics": {
            "outbound_messages": outbound_count,
            "inbound_messages": inbound_count,
        },
    }


@app.get("/api/crm/leads/{lead_id}")
async def crm_get_lead(lead_id: int):
    from core.services import get_lead_service
    svc = get_lead_service()
    if not svc:
        return JSONResponse({"error": "CRM services not initialized"}, status_code=400)
    lead = svc.get_lead(lead_id)
    if not lead:
        return JSONResponse({"error": "Lead not found"}, status_code=404)
    import sqlite3 as _sq
    conn = _sq.connect(str(Path.home() / ".nexus" / "memory.db"))
    conn.row_factory = _sq.Row
    related_ids = _related_lead_ids(conn, int(lead_id))
    placeholders = ",".join("?" for _ in related_ids) or "?"

    merged_lead = dict(lead)
    candidate_rows = conn.execute(
        f"SELECT * FROM leads WHERE id IN ({placeholders})",
        related_ids,
    ).fetchall()
    if candidate_rows:
        picked = _pick_best_lead_row([dict(r) for r in candidate_rows]) or merged_lead
        merged_lead = dict(picked)
        merged_lead["cluster_lead_ids"] = related_ids

    timeline_rows = conn.execute(
        f"SELECT * FROM lead_events WHERE lead_id IN ({placeholders}) ORDER BY ts DESC, id DESC LIMIT 100",
        related_ids,
    ).fetchall()
    events = [dict(r) for r in timeline_rows]

    msg_rows = conn.execute(
        f"SELECT * FROM lead_messages WHERE lead_id IN ({placeholders}) ORDER BY ts ASC, id ASC",
        related_ids,
    ).fetchall()
    messages = [dict(r) for r in msg_rows]
    conn.close()

    profile = _build_lead_profile(lead_id, merged_lead, related_ids=related_ids)
    return JSONResponse({"lead": merged_lead, "timeline": events, "messages": messages, "profile": profile})


@app.get("/api/crm/leads/{lead_id}/profile")
async def crm_get_lead_profile(lead_id: int):
    from core.services import get_lead_service
    svc = get_lead_service()
    if not svc:
        return JSONResponse({"error": "CRM services not initialized"}, status_code=400)
    lead = svc.get_lead(lead_id)
    if not lead:
        return JSONResponse({"error": "Lead not found"}, status_code=404)
    import sqlite3 as _sq
    conn = _sq.connect(str(Path.home() / ".nexus" / "memory.db"))
    conn.row_factory = _sq.Row
    related_ids = _related_lead_ids(conn, int(lead_id))
    conn.close()
    return JSONResponse({"lead_id": lead_id, "profile": _build_lead_profile(lead_id, lead, related_ids=related_ids)})

@app.post("/api/crm/leads")
async def crm_create_lead(request: Request):
    from core.services import get_lead_service
    svc = get_lead_service()
    if not svc:
        return JSONResponse({"error": "CRM services not initialized"}, status_code=400)
    body = await request.json()
    lead = svc.create_lead(body)
    deduped = lead.pop("_deduplicated", False)
    if not lead.get("_deduplicated"):
        try:
            from core.lead_pipeline import LeadPipeline
            pipeline = LeadPipeline()
            await pipeline.process_new_lead(lead["id"])
        except Exception as e:
            print(f"[Pipeline] trigger error for lead {lead.get('id')}: {e}")
    return JSONResponse({"ok": True, "lead": lead, "deduplicated": deduped})

@app.put("/api/crm/leads/{lead_id}")
async def crm_update_lead(lead_id: int, request: Request):
    from core.services import get_lead_service
    svc = get_lead_service()
    if not svc:
        return JSONResponse({"error": "CRM services not initialized"}, status_code=400)
    body = await request.json()
    actor = body.pop("_actor", "user")
    profile_fields = {
        "job_title", "venue_location", "website", "source_detail", "source_group_name",
        "is_vendor", "vendor_category", "is_venue", "venue_name",
        "joined_referral_program", "referral_partner_type",
        "referrals_given_count", "referrals_booked_count", "referral_payout_total",
        "lead_temperature_override",
    }
    if any(k in body for k in profile_fields):
        body["last_profile_update"] = datetime.utcnow().isoformat(timespec="seconds")
    lead = svc.update_lead(lead_id, actor=actor, **body)
    if not lead:
        return JSONResponse({"error": "Lead not found"}, status_code=404)
    import sqlite3 as _sq
    conn = _sq.connect(str(Path.home() / ".nexus" / "memory.db"))
    conn.row_factory = _sq.Row
    related_ids = _related_lead_ids(conn, int(lead_id))
    conn.close()
    profile = _build_lead_profile(lead_id, lead, related_ids=related_ids)
    return JSONResponse({"ok": True, "lead": lead, "profile": profile})

@app.post("/api/crm/leads/{lead_id}/stage")
async def crm_transition_stage(lead_id: int, request: Request):
    from core.services import get_pipeline_service
    ps = get_pipeline_service()
    if not ps:
        return JSONResponse({"error": "CRM services not initialized"}, status_code=400)
    body = await request.json()
    stage = body.get("stage") or body.get("booking_status", "")
    result = ps.transition(lead_id, stage, actor=body.get("actor", "user"),
                           reason=body.get("reason", ""))
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return JSONResponse(result)

@app.post("/api/crm/leads/{lead_id}/note")
async def crm_add_note(lead_id: int, request: Request):
    from core.services import get_lead_service
    svc = get_lead_service()
    if not svc:
        return JSONResponse({"error": "CRM services not initialized"}, status_code=400)
    body = await request.json()
    svc.add_internal_note(lead_id, body.get("note", ""), body.get("author", "Kai"))
    return JSONResponse({"ok": True})

@app.post("/api/crm/leads/{lead_id}/call-attempt", dependencies=[Depends(verify_action_auth)])
async def crm_log_call_attempt(lead_id: int, request: Request):
    """Log a manual call attempt so SMS/email follow-up context stays accurate."""
    from core.services import get_lead_service
    svc = get_lead_service()
    if not svc:
        return JSONResponse({"error": "CRM services not initialized"}, status_code=400)
    lead = svc.get_lead(lead_id)
    if not lead:
        return JSONResponse({"error": "Lead not found"}, status_code=404)

    body = await request.json()
    outcome = (body.get("outcome") or "no_answer").strip().lower()
    note = (body.get("note") or "").strip()
    phone = _normalize_phone(body.get("phone") or lead.get("phone") or "")
    actor = (body.get("actor") or "Kai").strip() or "Kai"

    outcome_label = {
        "no_answer": "No answer",
        "voicemail": "Voicemail",
        "connected": "Connected",
        "busy": "Busy",
        "failed": "Failed",
    }.get(outcome, outcome.replace("_", " ").title())

    message_text = note or f"Call attempted ({outcome_label})"
    _log_system_call_attempt(
        lead_id=int(lead_id),
        phone=phone,
        outcome=outcome,
        note=message_text,
        method="manual_call_attempt",
        actor=actor,
    )

    if note:
        try:
            svc.add_internal_note(lead_id, f"Call attempt ({outcome_label}): {note}", actor)
        except Exception:
            pass

    await broadcast({
        "type": "msg_sent",
        "channel": "call",
        "lead_id": int(lead_id),
        "to": phone,
        "preview": message_text[:80],
    })
    return JSONResponse({
        "ok": True,
        "lead_id": int(lead_id),
        "phone": phone,
        "outcome": outcome,
        "message": message_text,
    })

@app.get("/api/crm/leads/{lead_id}/timeline")
async def crm_get_timeline(lead_id: int, limit: int = 50):
    from core.services import get_timeline_service
    ts = get_timeline_service()
    if not ts:
        return JSONResponse({"error": "CRM services not initialized"}, status_code=400)
    return JSONResponse({"timeline": ts.get_timeline(lead_id, limit)})

@app.get("/api/crm/stages")
async def crm_get_stages():
    from core.services import get_pipeline_service
    ps = get_pipeline_service()
    if not ps:
        return JSONResponse({"error": "CRM services not initialized"}, status_code=400)
    return JSONResponse({"stages": ps.get_stages_info()})

@app.get("/api/crm/stats")
async def crm_get_stats():
    from core.services import get_lead_service
    svc = get_lead_service()
    if not svc:
        return JSONResponse({"error": "CRM services not initialized"}, status_code=400)
    return JSONResponse(svc.get_stats())

@app.get("/api/crm/activity")
async def crm_recent_activity(limit: int = 20):
    from core.services import get_timeline_service
    ts = get_timeline_service()
    if not ts:
        return JSONResponse({"error": "CRM services not initialized"}, status_code=400)
    return JSONResponse({"activity": ts.get_recent_activity(limit)})

@app.post("/api/crm/leads/{lead_id}/sms", dependencies=[Depends(verify_action_auth)])
async def crm_send_sms(lead_id: int, request: Request):
    """Send SMS to a lead from the dashboard."""
    from core.lead_pipeline import get_pipeline
    from core.services import get_lead_service
    svc = get_lead_service()
    p = get_pipeline()
    if not svc or not p:
        return JSONResponse({"error": "Services not initialized"}, status_code=400)
    lead = svc.get_lead(lead_id)
    if not lead:
        return JSONResponse({"error": "Lead not found"}, status_code=404)
    # SAFETY: Outbound gate enforced in pipeline.send_sms → Messenger.send_sms → outbound_gate()
    body = await request.json()
    message = body.get("message", "")
    if not message:
        return JSONResponse({"error": "Message is required"}, status_code=400)
    target_lead = dict(lead)
    target_lead_id = int(target_lead.get("id") or lead_id)
    if not _normalize_phone(target_lead.get("phone", "")):
        resolved = _resolve_related_send_target(lead_id, "sms")
        if resolved and _normalize_phone(resolved.get("phone", "")):
            target_lead = resolved
            target_lead_id = int(resolved.get("id") or resolved.get("lead_id") or target_lead_id)
    target_phone = _normalize_phone(target_lead.get("phone", ""))
    if not target_phone:
        return JSONResponse({"error": "Lead has no phone number"}, status_code=400)
    approval_id = body.get("approval_id")
    if not approval_id:
        approval_id = _create_inline_manual_approval(target_lead, "sms", message)
    result = await p.send_sms(target_phone, message, target_lead_id, approval_id=approval_id)
    if result.get("ok"):
        await broadcast({
            "type": "msg_sent",
            "channel": "sms",
            "lead_id": target_lead_id,
            "to": target_phone,
            "preview": message[:80],
        })
    if result.get("ok"):
        result["approval_id"] = approval_id
        result["lead_id"] = target_lead_id
        if int(lead_id) != int(target_lead_id):
            result["requested_lead_id"] = int(lead_id)
        return JSONResponse(result)
    blocked = str(result.get("error", "") or "").lower().startswith("blocked:")
    status = 400 if blocked else 500
    return JSONResponse(result, status_code=status)

@app.post("/api/crm/leads/{lead_id}/email", dependencies=[Depends(verify_action_auth)])
async def crm_send_email(lead_id: int, request: Request):
    """Send email to a lead from the dashboard."""
    from core.lead_pipeline import get_pipeline
    from core.services import get_lead_service
    svc = get_lead_service()
    p = get_pipeline()
    if not svc or not p:
        return JSONResponse({"error": "Services not initialized"}, status_code=400)
    lead = svc.get_lead(lead_id)
    if not lead:
        return JSONResponse({"error": "Lead not found"}, status_code=404)
    # SAFETY: Outbound gate enforced in pipeline.send_email → Messenger.send_email → outbound_gate()
    body = await request.json()
    subject = body.get("subject", "Message from Zoar Bathroom Rentals")
    message = body.get("message", "")
    if not message:
        return JSONResponse({"error": "Message is required"}, status_code=400)
    target_lead = dict(lead)
    target_lead_id = int(target_lead.get("id") or lead_id)
    target_email = _normalize_email(target_lead.get("email", ""))
    if not target_email or _is_sms_gateway_email(target_email):
        resolved = _resolve_related_send_target(lead_id, "email")
        if resolved:
            target_lead = resolved
            target_lead_id = int(resolved.get("id") or resolved.get("lead_id") or target_lead_id)
            target_email = _normalize_email(target_lead.get("email", ""))
    if not target_email or _is_sms_gateway_email(target_email):
        return JSONResponse({"error": "Lead has no deliverable email"}, status_code=400)
    approval_id = body.get("approval_id")
    if not approval_id:
        approval_id = _create_inline_manual_approval(target_lead, "email", message, subject=subject)
    result = await p.send_email(target_email, subject, message, target_lead_id, approval_id=approval_id)
    if result.get("ok"):
        await broadcast({
            "type": "msg_sent",
            "channel": "email",
            "lead_id": target_lead_id,
            "to": target_email,
            "preview": subject[:80],
        })
    if result.get("ok"):
        result["approval_id"] = approval_id
        result["lead_id"] = target_lead_id
        if int(lead_id) != int(target_lead_id):
            result["requested_lead_id"] = int(lead_id)
        return JSONResponse(result)
    blocked = str(result.get("error", "") or "").lower().startswith("blocked:")
    status = 400 if blocked else 500
    return JSONResponse(result, status_code=status)

@app.post("/api/crm/webhook/website")
async def crm_website_webhook(request: Request):
    """Receive leads from the Zoar website contact form (with UTM tracking)."""
    import sqlite3 as _sql, uuid
    from datetime import datetime as _dt
    body = await request.json()
    first = body.get("first_name", "").strip()
    last = body.get("last_name", "").strip()
    phone = body.get("phone", "").strip()
    email_addr = body.get("email", "").strip()
    if not phone and not email_addr:
        return JSONResponse({"error": "Phone or email required"}, status_code=400)
    clean_phone = "".join(c for c in phone if c.isdigit())
    if len(clean_phone) == 11 and clean_phone[0] == "1":
        clean_phone = clean_phone[1:]
    # Extract UTM parameters for source attribution
    utm_source = body.get("utm_source", "").strip()
    utm_medium = body.get("utm_medium", "").strip()
    utm_campaign = body.get("utm_campaign", "").strip()
    utm_content = body.get("utm_content", "").strip()
    utm_term = body.get("utm_term", "").strip()
    referral_source = body.get("referral_source", "").strip()
    # Determine source: if UTM says facebook, tag as facebook_ad; otherwise website
    source = "website"
    if utm_source == "facebook" and utm_medium == "paid":
        source = "facebook_ad"
    elif referral_source:
        source = "vendor_referral"
    db = str(Path.home() / ".nexus" / "memory.db")
    conn = _sql.connect(db)
    conn.row_factory = _sql.Row
    # Check if lead already exists by phone
    existing = None
    if clean_phone:
        existing = conn.execute("SELECT id FROM leads WHERE phone = ?", (clean_phone,)).fetchone()
    if not existing and email_addr:
        existing = conn.execute("SELECT id FROM leads WHERE email = ?", (email_addr.lower(),)).fetchone()
    if existing:
        lead_id = existing["id"]
        conn.execute(
            "UPDATE leads SET first_name=COALESCE(NULLIF(?,''),(first_name)), "
            "last_name=COALESCE(NULLIF(?,''),(last_name)), "
            "email=COALESCE(NULLIF(?,''),(email)), "
            "event_type=COALESCE(NULLIF(?,''),(event_type)), "
            "event_date=COALESCE(NULLIF(?,''),(event_date)), "
            "event_city=COALESCE(NULLIF(?,''),(event_city)), "
            "utm_source=COALESCE(NULLIF(?,''),(utm_source)), "
            "utm_medium=COALESCE(NULLIF(?,''),(utm_medium)), "
            "utm_campaign=COALESCE(NULLIF(?,''),(utm_campaign)), "
            "utm_content=COALESCE(NULLIF(?,''),(utm_content)), "
            "utm_term=COALESCE(NULLIF(?,''),(utm_term)), "
            "referral_source=COALESCE(NULLIF(?,''),(referral_source)), "
            "notes=?, updated_at=? WHERE id=?",
            (first, last, email_addr, body.get("event_type",""), body.get("event_date",""),
             body.get("event_city",""), utm_source, utm_medium, utm_campaign, utm_content, utm_term,
             referral_source, body.get("notes",""), _dt.utcnow().strftime("%Y-%m-%d %H:%M:%S"), lead_id)
        )
    else:
        ghl_id = f"web_{clean_phone or 'e'}_{uuid.uuid4().hex[:8]}"
        now_str = _dt.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO leads (ghl_contact_id, first_name, last_name, full_name, phone, email, "
            "source, status, discovered_at, updated_at, booking_status, event_type, event_date, "
            "event_city, guest_count, notes, utm_source, utm_medium, utm_campaign, utm_content, utm_term, "
            "referral_source) "
            "VALUES (?,?,?,?,?,?,?,  'new',?,?,'new_lead',?,?,?,?,?, ?,?,?,?,?, ?)",
            (ghl_id, first, last, (first + " " + last).strip(), clean_phone, email_addr.lower(),
             source, now_str, now_str, body.get("event_type",""), body.get("event_date",""),
             body.get("event_city",""), body.get("guests",""), body.get("notes",""),
             utm_source, utm_medium, utm_campaign, utm_content, utm_term, referral_source)
        )
        lead_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    print(f"[Website] New lead from {source}: {first} {last} - {phone} (utm_source={utm_source})")

    # ── CAPI: Fire Lead event to Meta Conversions API ──
    try:
        from integrations.meta_capi import fire_lead_event
        fire_lead_event(
            email=email_addr,
            phone=clean_phone,
            first_name=first,
            last_name=last,
            event_type=body.get("event_type", ""),
            event_source_url=str(request.headers.get("referer", "https://zoarbathroomrental.com")),
            event_id=body.get("event_id", ""),  # For Pixel deduplication
            client_ip=request.client.host if request.client else "",
            user_agent=request.headers.get("user-agent", ""),
            fbc=body.get("fbc", ""),
            fbp=body.get("fbp", ""),
        )
    except Exception as e:
        print(f"[CAPI] Lead event error: {e}")

    # Trigger auto follow-up pipeline for new leads
    if not existing:
        try:
            from core.lead_pipeline import get_pipeline
            p = get_pipeline()
            if p:
                asyncio.create_task(p.process_new_lead(lead_id))
        except Exception as e:
            print(f"[Website] Pipeline trigger error: {e}")

        # Enroll in smart follow-up sequence
        try:
            from core.follow_up_engine import get_follow_up_engine
            engine = get_follow_up_engine()
            if engine:
                engine.enroll_lead(lead_id, "website")
        except Exception as e:
            print(f"[Website] Follow-up enroll error: {e}")

        # Triple-redundancy notification: Telegram → Slack → SMS
        # The approval queue sends its own Telegram notification,
        # but we also fire immediate notifications as backup
        try:
            name = (first + " " + last).strip() or "Unknown"
            phone_preview = clean_phone[-4:] if clean_phone else ""
            event_info = body.get("event_type", "")
            date_info = body.get("event_date", "")
            detail = f" — {event_info}" if event_info else ""
            detail += f" on {date_info}" if date_info else ""
            phone_hint = f" (***{phone_preview})" if phone_preview else ""
            alert = f"🌐 NEW WEBSITE LEAD: {name}{phone_hint}{detail}. Check Telegram to approve outreach!"

            # 1. Telegram (primary)
            from telegram.bot import get_bot as _get_tg_bot
            tg = _get_tg_bot()
            if tg:
                await tg.send_to_all(alert)

            # 2. Slack (secondary) — if configured
            try:
                from integrations.slack_notify import send_slack
                await send_slack(alert)
            except Exception:
                pass

            # 3. SMS to Kai (emergency fallback)
            from core.lead_pipeline import get_pipeline as _get_p
            _p = _get_p()
            if _p:
                asyncio.create_task(_p._notify_kai_sms(alert[:160]))
        except Exception as e:
            print(f"[Website] Notification error: {e}")

        # Lead Conversion Accelerator: generate personalized quote within 60 seconds
        try:
            from core.lead_accelerator import process_new_lead
            tg = None
            try:
                from telegram.bot import get_bot as _get_tg_bot2
                tg = _get_tg_bot2()
            except Exception:
                pass
            if tg:
                asyncio.create_task(process_new_lead(lead_id, tg))
                print(f"[Accelerator] Triggered quote generation for lead {lead_id}")
        except Exception as e:
            print(f"[Accelerator] Error triggering quote: {e}")

        # Sync to Convex for real-time Nexus dashboard
        try:
            from integrations.convex_sync import sync_lead
            sync_lead({
                "first_name": first, "last_name": last,
                "email": email_addr, "phone": clean_phone, "source": source,
                "event_type": body.get("event_type", ""),
                "event_date": body.get("event_date", ""),
                "event_city": body.get("event_city", ""),
                "guest_count": body.get("guests", ""),
                "notes": body.get("notes", ""),
            })
        except Exception as e:
            print(f"[ConvexSync] Lead sync error: {e}")

    return JSONResponse({"ok": True, "lead_id": lead_id, "source": source})

async def _fetch_facebook_lead(leadgen_id: str) -> dict:
    """Fetch lead details from Facebook Graph API using leadgen_id.

    Facebook's leadgen webhook only sends a leadgen_id — we must call
    the Graph API to retrieve the actual form field data (name, email, phone).
    """
    import httpx
    cfg = load_config()
    page_token = cfg.get("fb_page_access_token", "")
    if not page_token:
        print(f"[Facebook] WARNING: No fb_page_access_token in config — cannot fetch lead {leadgen_id}")
        return {}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://graph.facebook.com/v25.0/{leadgen_id}",
                params={"access_token": page_token}
            )
            data = resp.json()
            if "error" in data:
                print(f"[Facebook] Graph API error fetching lead {leadgen_id}: {data['error']}")
                return {}
            return data
    except Exception as e:
        print(f"[Facebook] Failed to fetch lead {leadgen_id}: {e}")
        return {}


def _parse_fb_field_data(raw_fields: list) -> dict:
    """Convert Facebook field_data array to a flat dict."""
    def _clean(value) -> str:
        txt = str(value or "").strip()
        if not txt:
            return ""
        low = txt.lower()
        # Meta Lead Ads Testing Tool often sends placeholder values like:
        # "<test lead: dummy data for full_name>"
        if low.startswith("<test lead:") and txt.endswith(">"):
            return ""
        return txt

    parsed = {}
    for fd in raw_fields or []:
        key = str(fd.get("name", "") or "").strip()
        vals = fd.get("values", [""])
        val = vals[0] if isinstance(vals, list) and vals else ""
        parsed[key] = _clean(val)
    return parsed


def _mark_fb_leadgen_notified(leadgen_id: str) -> bool:
    """Return True if this is the first notification for leadgen_id."""
    lid = (leadgen_id or "").strip()
    if not lid:
        return True
    try:
        import sqlite3 as _sql3
        conn = _sql3.connect(str(Path.home() / ".nexus" / "memory.db"))
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fb_webhook_notified (
                leadgen_id TEXT PRIMARY KEY,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        cur = conn.execute(
            "INSERT OR IGNORE INTO fb_webhook_notified (leadgen_id) VALUES (?)",
            (lid,),
        )
        conn.commit()
        conn.close()
        return (cur.rowcount or 0) > 0
    except Exception:
        # Fail-open for notifications so alerts are not silently suppressed.
        return True


@app.post("/api/crm/webhook/facebook")
async def crm_facebook_webhook(request: Request):
    """Facebook Lead Ads webhook — receives new leads from FB.

    Facebook sends a lightweight payload with just leadgen_id, form_id, ad_id.
    We fetch the actual lead data (name, email, phone) via a Graph API call.
    Also supports direct field_data for backward compat with the /test endpoint.
    Signature verified via X-Hub-Signature-256 (HMAC-SHA256) per Phase 6B.
    """
    import traceback as _tb
    await _ensure_crm_alert_runtime(reason="facebook_webhook")
    # ── Phase 6B: Verify Facebook webhook signature ──
    raw_body = await request.body()
    cfg_fb = load_config()
    app_secret = cfg_fb.get("fb_app_secret", "")
    if app_secret:
        sig_header = request.headers.get("x-hub-signature-256", "")
        if sig_header:
            expected_sig = "sha256=" + hmac.new(
                app_secret.encode("utf-8"), raw_body, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(sig_header, expected_sig):
                from core.security import get_security_gate
                gate = get_security_gate()
                gate.log_security("fb_webhook_invalid_signature",
                                  f"IP={request.client.host if request.client else '?'} sig={sig_header[:20]}...",
                                  "critical")
                print(f"[Facebook] 🚨 INVALID SIGNATURE from {request.client.host if request.client else '?'}")
                return JSONResponse({"error": "Invalid signature"}, status_code=403)
        else:
            # No signature but secret is configured — log warning (don't block in test mode)
            from core.security import get_security_gate
            gate = get_security_gate()
            gate.log_security("fb_webhook_missing_signature",
                              f"IP={request.client.host if request.client else '?'}",
                              "warning")
            print(f"[Facebook] ⚠️ Missing signature from {request.client.host if request.client else '?'}")
    body = json.loads(raw_body)
    print(f"[Facebook] 📥 Webhook received: {json.dumps(body)[:500]}")
    leads_created = []
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            val = change.get("value", {})
            leadgen_id = val.get("leadgen_id", "")
            print(f"[Facebook] Processing change: leadgen_id={leadgen_id}")

            # Real webhook: fetch lead data from Graph API using leadgen_id
            # Test/direct: use field_data if already present in payload
            if leadgen_id and not val.get("field_data"):
                lead_details = await _fetch_facebook_lead(leadgen_id)
                raw_fields = lead_details.get("field_data", [])
                field_data = _parse_fb_field_data(raw_fields)
                print(f"[Facebook] Fetched from Graph API: {field_data}")
                # Also grab ad/form metadata from the fetched lead if not in webhook
                if not val.get("ad_id") and lead_details.get("ad_id"):
                    val["ad_id"] = lead_details["ad_id"]
                if not val.get("form_id") and lead_details.get("form_id"):
                    val["form_id"] = lead_details["form_id"]
            else:
                # Backward compat: field_data directly in payload (test endpoint)
                field_data = _parse_fb_field_data(val.get("field_data", []))
                print(f"[Facebook] Using direct field_data: {field_data}")

            # Skip if we got zero usable data (token missing or API error)
            if not field_data and leadgen_id:
                print(f"[Facebook] ⚠️ Received leadgen_id={leadgen_id} but could not fetch data — storing stub")
                field_data = {}

            # Parse lead fields from field_data
            full_name = field_data.get("full_name", "")
            first_name = field_data.get("first_name", "")
            if not first_name and full_name:
                first_name = full_name.split()[0]
            last_name = field_data.get("last_name", "")
            if not last_name and full_name:
                last_name = " ".join(full_name.split()[1:])

            lead_data = {
                "first_name": first_name,
                "last_name": last_name,
                "email": field_data.get("email", ""),
                "phone": field_data.get("phone_number", field_data.get("phone", "")),
                "source": "facebook",
                "source_detail": field_data.get("source_detail", "") or "Facebook Lead Form Submission",
                "source_group_name": field_data.get("facebook_group_name", field_data.get("group_name", "")),
                "ad_id": val.get("ad_id", ""),
                "form_id": val.get("form_id", ""),
                "campaign_id": val.get("adgroup_id", ""),
                "event_type": field_data.get("event_type", field_data.get("what_type_of_event?", "")),
                "event_date": field_data.get("event_date", field_data.get("preferred_date", "")),
                "event_city": field_data.get("city", field_data.get("event_city", "")),
                "guest_count": field_data.get("guest_count", field_data.get("number_of_guests", 0)),
            }
            print(f"[Facebook] 👤 Parsed lead: {first_name} {last_name} | {lead_data.get('phone')} | {lead_data.get('email')}")

            from core.services import get_lead_service
            svc = get_lead_service()
            if not svc:
                print("[Facebook] ❌ CRITICAL: get_lead_service() returned None — CRM services not initialized!")
                # Fallback: try init_services on the fly
                try:
                    from core.services import init_services, _initialized
                    if not _initialized:
                        init_services(broadcast_fn=broadcast)
                        svc = get_lead_service()
                        print("[Facebook] 🔄 Initialized CRM services on-the-fly")
                except Exception as e:
                    print(f"[Facebook] ❌ Failed to initialize services: {e}")

            if svc:
                try:
                    lead = svc.create_lead(lead_data)
                    print(f"[Facebook] ✅ Lead created: id={lead.get('id')} dedup={lead.get('_deduplicated', False)}")
                except Exception as e:
                    print(f"[Facebook] ❌ Lead creation FAILED: {e}")
                    _tb.print_exc()
                    lead = None
                if not lead:
                    continue
                leads_created.append(lead.get("id"))

                # Log to notification_log table
                try:
                    import sqlite3 as _sql3
                    _nc = _sql3.connect(str(Path.home() / ".nexus" / "memory.db"))
                    _nc.execute("INSERT INTO notification_log (ts, channel, recipient, message_preview, success) VALUES (datetime('now'), 'webhook', 'facebook', ?, 1)",
                               (f"Lead {first_name} {last_name} from Facebook",))
                    _nc.commit()
                    _nc.close()
                except Exception:
                    pass

                # ── CAPI: Fire Lead event for FB leads ──
                try:
                    from integrations.meta_capi import fire_lead_event
                    fire_lead_event(
                        email=lead_data.get("email", ""),
                        phone=lead_data.get("phone", ""),
                        first_name=first_name,
                        last_name=last_name,
                        event_type=lead_data.get("event_type", ""),
                        event_source_url="https://zoarbathroomrental.com",
                        action_source="system_generated",
                    )
                except Exception as e:
                    print(f"[CAPI] FB Lead event error: {e}")

                from core.lead_pipeline import get_pipeline
                p = get_pipeline()

                # Trigger pipeline processing for non-deduplicated leads
                if not lead.get("_deduplicated"):
                    print(f"[Facebook] 🚀 New lead — triggering pipeline processing for lead {lead['id']}")
                    if p:
                        asyncio.create_task(p.process_new_lead(lead["id"]))
                        print(f"[Facebook] ✅ Pipeline processing task created for lead {lead['id']}")
                    else:
                        print(f"[Facebook] ⚠️ Pipeline not initialized — lead {lead['id']} will NOT be processed")

                    # Enroll in smart follow-up sequence
                    try:
                        from core.follow_up_engine import get_follow_up_engine
                        engine = get_follow_up_engine()
                        if engine:
                            engine.enroll_lead(lead["id"], "facebook")
                    except Exception as e:
                        print(f"[Facebook] Follow-up enroll error: {e}")

                # Triple-redundancy notification: Telegram → Slack → SMS
                # Notify once per leadgen_id even if the lead merges (dedup).
                try:
                    should_notify = _mark_fb_leadgen_notified(leadgen_id)
                    if should_notify:
                        def _na(value: str, max_len: int = 42) -> str:
                            txt = str(value or "").strip()
                            if not txt:
                                return "N/A"
                            return txt[:max_len]

                        name = f"{first_name} {last_name}".strip()
                        phone = (lead_data.get("phone") or "").strip()
                        email = (lead_data.get("email") or "").strip()
                        lead_ref = (leadgen_id or "")[-6:] or "N/A"
                        sent_at = datetime.utcnow().strftime("%H:%M:%S")
                        alert = (
                            f"NEW FB LEAD | Name: {_na(name, 26)} | "
                            f"Phone: {_na(phone, 18)} | Email: {_na(email, 38)} "
                            f"| Ref:{lead_ref} | {sent_at}Z"
                        )

                        # 1. Telegram (primary)
                        from telegram.bot import get_bot as _get_tg_bot
                        tg = _get_tg_bot()
                        if tg:
                            await tg.send_to_all(alert)

                        # 2. Slack (secondary) — if configured
                        try:
                            from integrations.slack_notify import send_slack
                            await send_slack(alert)
                        except Exception:
                            pass

                        # 3. Optional SMS to Kai (personal phone) for immediate FB lead alerts.
                        cfg_now = load_config()
                        sms_notify_enabled = str(cfg_now.get("fb_sms_notify", "true")).lower() not in (
                            "0", "false", "no", "off"
                        )
                        if sms_notify_enabled and p:
                            asyncio.create_task(p._notify_kai_sms(alert[:160]))
                    else:
                        print(f"[Facebook] Duplicate webhook delivery for leadgen_id={leadgen_id}; notifications skipped")
                except Exception as e:
                    print(f"[Facebook] Notification error: {e}")

    return JSONResponse({"ok": True, "leads_created": leads_created})

@app.get("/api/crm/webhook/facebook")
async def crm_facebook_verify(request: Request):
    """Facebook webhook verification (hub.challenge)."""
    params = request.query_params
    mode = params.get("hub.mode", "")
    token = params.get("hub.verify_token", "")
    challenge = params.get("hub.challenge", "")
    cfg = load_config()
    if mode == "subscribe" and token == cfg.get("fb_verify_token", "nexus-fb-verify"):
        return HTMLResponse(challenge)
    return JSONResponse({"error": "Verification failed"}, status_code=403)

# ── Meta Conversions API (CAPI) Endpoints ─────────────────────────────────────

@app.post("/api/capi/test")
async def capi_test():
    """Send a test event to verify CAPI is working."""
    try:
        from integrations.meta_capi import test_connection
        result = await test_connection()
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@app.get("/api/capi/log")
async def capi_log(limit: int = 50):
    """View recent CAPI event log."""
    try:
        from integrations.meta_capi import get_recent_log
        entries = get_recent_log(limit)
        return JSONResponse({"events": entries, "count": len(entries)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

# ── CRM Source Attribution Stats ───────────────────────────────────────────────

@app.get("/api/crm/stats/sources")
async def crm_source_stats():
    """Lead counts grouped by source and campaign for dashboard attribution."""
    import sqlite3 as _sql
    db = str(Path.home() / ".nexus" / "memory.db")
    conn = _sql.connect(db)
    conn.row_factory = _sql.Row
    # Count by source
    rows = conn.execute(
        "SELECT COALESCE(NULLIF(source,''),'unknown') as source, COUNT(*) as count "
        "FROM leads GROUP BY source ORDER BY count DESC"
    ).fetchall()
    by_source = [{"source": r["source"], "count": r["count"]} for r in rows]
    # Count by campaign (for UTM-tracked leads)
    camp_rows = conn.execute(
        "SELECT COALESCE(NULLIF(utm_campaign,''),'(none)') as campaign, "
        "COALESCE(NULLIF(utm_source,''),'direct') as utm_source, COUNT(*) as count "
        "FROM leads WHERE utm_campaign != '' GROUP BY utm_campaign ORDER BY count DESC"
    ).fetchall()
    by_campaign = [{"campaign": r["campaign"], "utm_source": r["utm_source"], "count": r["count"]} for r in camp_rows]
    # Total leads
    total = conn.execute("SELECT COUNT(*) as c FROM leads").fetchone()["c"]
    # Recent leads with source
    recent = conn.execute(
        "SELECT id, full_name, source, utm_source, utm_campaign, discovered_at "
        "FROM leads ORDER BY discovered_at DESC LIMIT 10"
    ).fetchall()
    recent_list = [dict(r) for r in recent]
    conn.close()
    return JSONResponse({
        "total": total,
        "by_source": by_source,
        "by_campaign": by_campaign,
        "recent": recent_list
    })

# ── CRM Settings ──────────────────────────────────────────────────────────────

@app.get("/api/crm/settings")
async def crm_get_settings():
    cfg = load_config()
    return JSONResponse({
        "auto_followup_enabled": cfg.get("auto_followup_enabled", True),
        "kai_notifications": cfg.get("kai_notifications", True),
        "kai_phone": cfg.get("gv_number", ""),
        "initial_delay_seconds": cfg.get("initial_delay_seconds", 30),
        "email_delay_seconds": 120,
        "followup_delay_hours": 48,
        "fb_verify_token": cfg.get("fb_verify_token", "nexus-fb-verify"),
        "fb_webhook_domain": cfg.get("fb_webhook_domain", ""),
        "google_sheet_crm_enabled": cfg.get("google_sheet_crm_enabled", True),
        "google_sheet_crm_name": cfg.get("google_sheet_crm_name", "Facebook Lead Ads Form CRM"),
        "google_sheet_crm_id": cfg.get("google_sheet_crm_id", ""),
        "google_sheet_crm_url": cfg.get("google_sheet_crm_url", ""),
        "google_sheet_crm_poll_seconds": cfg.get("google_sheet_crm_poll_seconds", 12),
        "google_sheet_crm_notify_sms": cfg.get("google_sheet_crm_notify_sms", True),
        "google_sheet_crm_auto_first_touch": cfg.get("google_sheet_crm_auto_first_touch", True),
        "google_sheet_crm_brief_call_enabled": cfg.get("google_sheet_crm_brief_call_enabled", True),
        "google_sheet_crm_call_ring_seconds": cfg.get("google_sheet_crm_call_ring_seconds", 8),
        "google_sheet_crm_send_sms": cfg.get("google_sheet_crm_send_sms", True),
        "google_sheet_crm_send_email": cfg.get("google_sheet_crm_send_email", True),
        "google_sheet_crm_enroll_followups": cfg.get("google_sheet_crm_enroll_followups", True),
        "google_sheet_crm_enqueue_pipeline": cfg.get("google_sheet_crm_enqueue_pipeline", False),
        "google_service_account_file": cfg.get("google_service_account_file", ""),
    })

@app.put("/api/crm/settings")
async def crm_update_settings(request: Request):
    body = await request.json()
    allowed = {
        "auto_followup_enabled",
        "kai_notifications",
        "fb_verify_token",
        "fb_webhook_domain",
        "fb_page_access_token",
        "fb_pixel_id",
        "google_sheet_crm_enabled",
        "google_sheet_crm_name",
        "google_sheet_crm_id",
        "google_sheet_crm_url",
        "google_sheet_crm_poll_seconds",
        "google_sheet_crm_notify_sms",
        "google_sheet_crm_auto_first_touch",
        "google_sheet_crm_brief_call_enabled",
        "google_sheet_crm_call_ring_seconds",
        "google_sheet_crm_send_sms",
        "google_sheet_crm_send_email",
        "google_sheet_crm_enroll_followups",
        "google_sheet_crm_enqueue_pipeline",
        "google_sheet_crm_backfill",
        "google_service_account_file",
        "google_service_account_json",
        "google_credentials_file",
        "fb_sheet_crm_id",
        "fb_sheet_crm_url",
    }
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        return JSONResponse({"error": "No valid settings to update"}, status_code=400)
    save_config(updates)
    # Also update the live pipeline config
    from core.lead_pipeline import get_pipeline
    p = get_pipeline()
    if p:
        p.config.update(updates)
    if any(k.startswith("google_sheet_crm_") or k.startswith("google_service_") or k.startswith("fb_sheet_crm_") for k in updates):
        await _start_google_sheet_crm_sync(load_config(), force_restart=True)
    return JSONResponse({"ok": True, "updated": list(updates.keys())})

# ── Facebook test/stats ───────────────────────────────────────────────────────

@app.post("/api/crm/webhook/facebook/test")
async def crm_facebook_test(request: Request):
    """Send a simulated Facebook lead to test the full flow.

    Accepts optional JSON body to override defaults:
        {"first_name": "Kai", "last_name": "E", "email": "...", "phone": "...", "event_type": "wedding"}
    """
    import random
    await _ensure_crm_alert_runtime(reason="facebook_test")
    # Check for custom overrides in request body
    overrides = {}
    try:
        body = await request.json()
        if isinstance(body, dict):
            overrides = body
    except Exception:
        pass  # No body or invalid JSON — use defaults

    default_name = f"Test Lead {random.randint(100,999)}"
    lead_data = {
        "first_name": overrides.get("first_name", default_name.split()[0]),
        "last_name": overrides.get("last_name", " ".join(default_name.split()[1:])),
        "email": overrides.get("email", f"test{random.randint(100,999)}@example.com"),
        "phone": overrides.get("phone", f"+1555{random.randint(1000000,9999999)}"),
        "source": overrides.get("source", "facebook_test"),
        "event_type": overrides.get("event_type", "wedding"),
    }
    from core.services import get_lead_service
    svc = get_lead_service()
    if not svc:
        return JSONResponse({"error": "CRM not initialized"}, status_code=500)
    lead = svc.create_lead(lead_data)

    # Trigger full pipeline flow (approval queue → Telegram notification)
    if not lead.get("_deduplicated"):
        try:
            from core.lead_pipeline import get_pipeline
            p = get_pipeline()
            if p:
                import asyncio
                asyncio.create_task(p.process_new_lead(lead["id"]))
                print(f"[Facebook Test] 🚀 Pipeline triggered for test lead {lead['id']}")
            else:
                print("[Facebook Test] ⚠️ Pipeline not initialized — lead created but not queued for approval")
        except Exception as e:
            print(f"[Facebook Test] Pipeline error: {e}")

    return JSONResponse({"ok": True, "lead": lead, "pipeline_triggered": not lead.get("_deduplicated")})


@app.post("/api/crm/leads/{lead_id}/requeue")
async def crm_requeue_lead(lead_id: int):
    """Re-queue approval messages for an existing lead.

    Useful when previous approvals expired or were skipped and you want to
    generate fresh outreach. Resets lead status to 'new' then re-runs the
    pipeline.
    """
    import sqlite3 as _sql
    db_path = str(Path.home() / ".nexus" / "memory.db")
    conn = _sql.connect(db_path)
    lead = conn.execute("SELECT id, first_name, last_name, status FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not lead:
        conn.close()
        return JSONResponse({"error": "Lead not found"}, status_code=404)
    conn.execute("UPDATE leads SET status='new' WHERE id=?", (lead_id,))
    conn.commit()
    conn.close()

    from core.lead_pipeline import get_pipeline
    p = get_pipeline()
    if p:
        asyncio.create_task(p.process_new_lead(lead_id))
        return JSONResponse({"ok": True, "lead_id": lead_id, "message": f"Re-queued lead #{lead_id} for approval"})
    return JSONResponse({"error": "Pipeline not initialized"}, status_code=500)


# ── Follow-Up Engine API ───────────────────────────────────────────────────────
@app.get("/api/crm/followups")
async def crm_followups():
    """Get all follow-up sequences (active, completed, stopped, paused)."""
    import sqlite3 as _sql
    try:
        db = str(Path.home() / ".nexus" / "memory.db")
        conn = _sql.connect(db)
        conn.row_factory = _sql.Row
        rows = conn.execute(
            "SELECT f.*, l.first_name, l.last_name, l.phone, l.email, l.source "
            "FROM follow_up_sequences f "
            "LEFT JOIN leads l ON f.lead_id = l.id "
            "ORDER BY CASE f.status WHEN 'active' THEN 0 ELSE 1 END, f.next_send_at ASC"
        ).fetchall()
        conn.close()
        seqs = []
        for r in rows:
            d = dict(r)
            fn = d.get("first_name", "") or ""
            ln = d.get("last_name", "") or ""
            d["lead_name"] = f"{fn} {ln}".strip() or f"Lead #{d.get('lead_id','?')}"
            seqs.append(d)
        return JSONResponse({"sequences": seqs, "total": len(seqs)})
    except Exception as e:
        return JSONResponse({"sequences": [], "error": str(e)})

@app.get("/api/crm/followups/{lead_id}")
async def crm_followup_status(lead_id: int):
    """Get follow-up sequence status for a specific lead."""
    try:
        from core.follow_up_engine import get_follow_up_engine
        engine = get_follow_up_engine()
        if not engine:
            return JSONResponse({"error": "Follow-up engine not initialized"}, status_code=500)
        status = engine.get_sequence_status(lead_id)
        return JSONResponse({"sequence": status})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/crm/followups/{lead_id}/stop")
async def crm_followup_stop(lead_id: int):
    """Stop a lead's follow-up sequence."""
    try:
        from core.follow_up_engine import get_follow_up_engine
        engine = get_follow_up_engine()
        if not engine:
            return JSONResponse({"error": "Follow-up engine not initialized"}, status_code=500)
        engine.stop_sequence(lead_id, "manual_stop")
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/crm/followups/{lead_id}/enroll")
async def crm_followup_enroll(lead_id: int, request: Request):
    """Manually enroll a lead in a follow-up sequence."""
    try:
        body = await request.json()
        seq_type = body.get("sequence_type")
        from core.follow_up_engine import get_follow_up_engine
        engine = get_follow_up_engine()
        if not engine:
            return JSONResponse({"error": "Follow-up engine not initialized"}, status_code=500)
        engine.enroll_lead(lead_id, seq_type)
        return JSONResponse({"ok": True, "sequence_type": seq_type or "auto"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ── Lead Scoring API (moved before {lead_id} catch-all — see line ~1901) ─────

# ── Daily Posting API ─────────────────────────────────────────────────────────
@app.get("/api/posting/stats")
async def posting_stats():
    """Get daily posting engine stats — angle count, niches, languages."""
    from core.daily_posting import get_angle_count
    return JSONResponse(get_angle_count())

@app.post("/api/posting/briefing")
async def posting_briefing_now():
    """Trigger today's briefing on demand (doesn't wait for 8 AM)."""
    from core.daily_posting import generate_and_send_briefing
    result = await generate_and_send_briefing()
    return JSONResponse(result)

@app.post("/api/posting/autopost")
async def posting_autopost():
    """Auto-post today's FB Page content via Graph API."""
    from core.daily_posting import auto_post_to_fb_page
    result = await auto_post_to_fb_page()
    status = 200 if result.get("ok") else 400
    return JSONResponse(result, status_code=status)

@app.post("/api/posting/generate")
async def posting_generate(request: Request):
    """Generate a single listing on demand. Query: niche, lang, platform."""
    from core.daily_posting import generate_listing_now
    body = await request.json()
    listing = await generate_listing_now(
        niche=body.get("niche", ""),
        lang=body.get("lang", "en"),
        platform=body.get("platform", "fb_marketplace"),
    )
    return JSONResponse(listing)

# ═══════════════════════════════════════════════════════════════════════════
# NEW MODULE API ENDPOINTS (Master Prompt Phases 7-15)
# ═══════════════════════════════════════════════════════════════════════════

# ── Quote/Invoice API (Phase 7/11) ───────────────────────────────────────────

@app.post("/api/quotes/create")
async def create_quote_endpoint(request: Request):
    """Create a new quote. Body: client_name, event_type, event_date, location, price, etc."""
    from core.quote_generator import create_quote
    body = await request.json()
    result = create_quote(
        client_name=body.get("client_name", ""),
        event_type=body.get("event_type", ""),
        event_date=body.get("event_date", ""),
        event_location=body.get("event_location", body.get("location", "")),
        guest_count=body.get("guest_count", 0),
        price=body.get("price", 1100.0),
        client_email=body.get("client_email", ""),
        client_phone=body.get("client_phone", ""),
        lead_id=body.get("lead_id"),
        include_attendant=body.get("include_attendant", False),
        attendant_price=body.get("attendant_price", 200.0),
        include_premium=body.get("include_premium", False),
        premium_price=body.get("premium_price", 150.0),
        discount=body.get("discount", 0.0),
        notes=body.get("notes", ""),
    )
    return JSONResponse(result)

@app.get("/api/quotes")
async def list_quotes_endpoint(request: Request):
    """List quotes. Optional query params: status, lead_id."""
    from core.quote_generator import list_quotes
    params = dict(request.query_params)
    lead_id = int(params["lead_id"]) if "lead_id" in params else None
    return JSONResponse(list_quotes(status=params.get("status"), lead_id=lead_id))

@app.post("/api/quote/generate")
async def generate_tier_quote(request: Request):
    """Generate a tier-based quote using the pricing calculator."""
    try:
        from core.pricing import generate_quote_message
        body = await request.json()
        quote_data, customer_message = generate_quote_message(
            lead_name=body.get("customer_name", ""),
            event_type=body.get("event_type", ""),
            event_date=body.get("event_date", ""),
            event_city=body.get("city", ""),
            guest_count=body.get("guest_count", 0),
            is_venue=body.get("is_venue", False),
        )
        return JSONResponse({"quote": quote_data, "message": customer_message})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/quotes/stats")
async def quote_stats_endpoint():
    """Get quote/invoice stats."""
    from core.quote_generator import get_quote_stats
    return JSONResponse(get_quote_stats())

@app.post("/api/invoices/create")
async def create_invoice_endpoint(request: Request):
    """Create an invoice. Body: quote_id or manual fields."""
    from core.quote_generator import create_invoice
    body = await request.json()
    result = create_invoice(
        quote_id=body.get("quote_id"),
        client_name=body.get("client_name", ""),
        client_email=body.get("client_email", ""),
        client_phone=body.get("client_phone", ""),
        event_type=body.get("event_type", ""),
        event_date=body.get("event_date", ""),
        total=body.get("total", 0),
        lead_id=body.get("lead_id"),
        notes=body.get("notes", ""),
    )
    return JSONResponse(result)

@app.get("/api/invoices")
async def list_invoices_endpoint(request: Request):
    """List invoices. Optional query: status."""
    from core.quote_generator import list_invoices
    params = dict(request.query_params)
    return JSONResponse(list_invoices(status=params.get("status")))

# ── Knowledge Engine API (Phase 13) ──────────────────────────────────────────

@app.get("/api/knowledge")
async def search_knowledge_endpoint(request: Request):
    """Search knowledge base. Query params: q (search), category, key."""
    from core.knowledge_engine import search_knowledge, get_knowledge
    params = dict(request.query_params)
    if "q" in params:
        return JSONResponse(search_knowledge(params["q"]))
    return JSONResponse(get_knowledge(category=params.get("category"), key=params.get("key")))

@app.post("/api/knowledge")
async def store_knowledge_endpoint(request: Request):
    """Store knowledge. Body: category, key, value, subcategory, source."""
    from core.knowledge_engine import store_knowledge
    body = await request.json()
    kid = store_knowledge(
        category=body.get("category", "general"),
        key=body.get("key", ""),
        value=body.get("value", ""),
        subcategory=body.get("subcategory", ""),
        source=body.get("source", "api"),
    )
    return JSONResponse({"ok": True, "id": kid})

@app.get("/api/knowledge/insights")
async def knowledge_insights_endpoint():
    """Get recent insights."""
    from core.knowledge_engine import get_recent_insights
    return JSONResponse(get_recent_insights(20))

# ── Ad Performance API (Phase 15) ────────────────────────────────────────────

@app.get("/api/ads/metrics")
async def ad_metrics_endpoint(request: Request):
    """Get ad metrics. Query: period (today, yesterday, last_7d, last_30d)."""
    from core.ad_monitor import fetch_today_metrics, fetch_yesterday_metrics, get_7day_avg
    import sqlite3

    def _fallback_from_ad_metrics(period: str) -> dict:
        today = datetime.now().date()
        if period == "yesterday":
            start = end = today - timedelta(days=1)
        elif period in ("7day_avg", "last_7d"):
            start, end = today - timedelta(days=6), today
        elif period == "last_30d":
            start, end = today - timedelta(days=29), today
        else:
            start = end = today

        db_path = Path.home() / ".nexus" / "memory.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            WITH latest AS (
              SELECT
                date,
                COALESCE(NULLIF(campaign_id, ''), campaign_name) AS campaign_key,
                MAX(collected_at) AS max_collected
              FROM ad_metrics
              WHERE date BETWEEN ? AND ?
              GROUP BY date, campaign_key
            ),
            dedup AS (
              SELECT m.*
              FROM ad_metrics m
              JOIN latest l
                ON m.date = l.date
               AND COALESCE(NULLIF(m.campaign_id, ''), m.campaign_name) = l.campaign_key
               AND m.collected_at = l.max_collected
            )
            SELECT
              COALESCE(SUM(spend), 0) AS spend,
              COALESCE(SUM(impressions), 0) AS impressions,
              COALESCE(SUM(reach), 0) AS reach,
              COALESCE(SUM(clicks), 0) AS clicks,
              COALESCE(SUM(leads), 0) AS leads,
              COUNT(DISTINCT campaign_id) AS campaigns
            FROM dedup
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchone()
        conn.close()

        spend = float((row["spend"] if row else 0) or 0)
        impressions = int((row["impressions"] if row else 0) or 0)
        reach = int((row["reach"] if row else 0) or 0)
        clicks = int((row["clicks"] if row else 0) or 0)
        leads = int((row["leads"] if row else 0) or 0)
        campaigns = int((row["campaigns"] if row else 0) or 0)
        ctr = round((clicks / impressions * 100), 2) if impressions > 0 else 0.0
        cpl = round((spend / leads), 2) if leads > 0 else 0.0

        return {
            "spend": round(spend, 2),
            "impressions": impressions,
            "reach": reach,
            "clicks": clicks,
            "ctr": ctr,
            "leads": leads,
            "cpl": cpl,
            "campaigns": campaigns,
            "source": "sqlite_fallback",
            "period": period,
        }

    params = dict(request.query_params)
    period = params.get("period", "today")

    try:
        if period == "yesterday":
            data = await asyncio.wait_for(fetch_yesterday_metrics(), timeout=8)
        elif period in ("7day_avg", "last_7d"):
            data = get_7day_avg()
        elif period == "last_30d":
            # The ad_monitor API call for 30d can be slow. Use DB aggregate as primary.
            data = _fallback_from_ad_metrics(period)
        else:
            data = await asyncio.wait_for(fetch_today_metrics(), timeout=8)

        # If API returned empty/zero values, still prefer DB-backed fallback.
        if not data or (float(data.get("spend", 0) or 0) <= 0 and int(data.get("impressions", 0) or 0) <= 0):
            data = _fallback_from_ad_metrics(period)
        else:
            data["source"] = data.get("source") or "meta_api"
            data["period"] = period
        return JSONResponse(data)
    except Exception as e:
        log.warning("ad_metrics_endpoint fallback (%s): %s", period, e)
        return JSONResponse(_fallback_from_ad_metrics(period))

@app.get("/api/ads/report/daily")
async def ad_daily_report_endpoint():
    """Generate daily ad report text."""
    from core.ad_monitor import generate_daily_ad_report
    from agents.metrics_tracker import MetricsTracker
    try:
        report = await asyncio.wait_for(generate_daily_ad_report(), timeout=10)
        return JSONResponse({"report": report, "source": "meta_api"})
    except Exception as e:
        log.warning("ad_daily_report_endpoint fallback: %s", e)
        m = MetricsTracker().compute_all_metrics(days=1)
        report = (
            "AD PERFORMANCE -- Today (fallback)\n"
            "=================================\n"
            f"Spend: ${float(m.get('total_spend', 0) or 0):.2f}\n"
            f"Impressions: {int(m.get('total_impressions', 0) or 0)}\n"
            f"Clicks: {int(m.get('total_clicks', 0) or 0)} | CTR: {float(m.get('ctr', 0) or 0):.2f}%\n"
            f"Leads: {int(m.get('total_leads_period', 0) or 0)} | CPL: ${float(m.get('cpl', 0) or 0):.2f}\n"
        )
        return JSONResponse({"report": report, "source": "sqlite_fallback"})

@app.get("/api/ads/report/weekly")
async def ad_weekly_report_endpoint():
    """Generate weekly ad deep dive."""
    from core.ad_monitor import generate_weekly_ad_report
    from agents.metrics_tracker import MetricsTracker
    try:
        report = await asyncio.wait_for(generate_weekly_ad_report(), timeout=12)
        return JSONResponse({"report": report, "source": "meta_api"})
    except Exception as e:
        log.warning("ad_weekly_report_endpoint fallback: %s", e)
        m = MetricsTracker().compute_all_metrics(days=7)
        report = (
            "AD PERFORMANCE -- Last 7 Days (fallback)\n"
            "========================================\n"
            f"Total Spend: ${float(m.get('total_spend', 0) or 0):.2f}\n"
            f"Impressions: {int(m.get('total_impressions', 0) or 0)} | Reach: {int(m.get('total_reach', 0) or 0)}\n"
            f"Clicks: {int(m.get('total_clicks', 0) or 0)} | CTR: {float(m.get('ctr', 0) or 0):.2f}%\n"
            f"Leads: {int(m.get('total_leads_period', 0) or 0)} | CPL: ${float(m.get('cpl', 0) or 0):.2f}\n"
        )
        return JSONResponse({"report": report, "source": "sqlite_fallback"})

# ── Instagram API (Phase 2/4) ────────────────────────────────────────────────

@app.get("/api/instagram/stats")
async def ig_stats_endpoint():
    """Get Instagram posting stats."""
    from integrations.instagram_api import get_posting_stats
    return JSONResponse(get_posting_stats(30))

@app.post("/api/instagram/post")
async def ig_post_now_endpoint():
    """Trigger an Instagram post now."""
    from integrations.instagram_api import create_daily_ig_post
    result = await create_daily_ig_post()
    return JSONResponse(result)

# ── Personal Assistant API (Phase 10) ────────────────────────────────────────

@app.get("/api/assistant/todos")
async def list_todos_endpoint():
    """Get all active todos."""
    from core.personal_assistant import list_todos
    return JSONResponse(list_todos())

@app.post("/api/assistant/todos")
async def add_todo_endpoint(request: Request):
    """Add a todo. Body: task, priority, due_date."""
    from core.personal_assistant import add_todo
    body = await request.json()
    return JSONResponse(add_todo(body.get("task", ""), body.get("priority", "normal"), body.get("due_date")))

@app.get("/api/assistant/health")
async def health_summary_endpoint():
    """Get health tracking summary."""
    from core.personal_assistant import get_health_summary
    return JSONResponse(get_health_summary(7))

# ── Watchdog/Services API (Phase 8) ──────────────────────────────────────────

@app.get("/api/services")
async def services_status_endpoint():
    """Get all service statuses from watchdog."""
    from core.watchdog import get_health_check
    return JSONResponse(get_health_check())

# ── SMS Templates API ─────────────────────────────────────────────────────────
@app.get("/api/crm/templates")
async def crm_templates():
    """Get all SMS/email templates for quick-send."""
    import sqlite3 as _sql
    db = str(Path.home() / ".nexus" / "memory.db")
    conn = _sql.connect(db)
    conn.row_factory = _sql.Row
    # Ensure table exists
    conn.execute("""CREATE TABLE IF NOT EXISTS sms_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        channel TEXT NOT NULL DEFAULT 'sms',
        category TEXT DEFAULT 'general',
        body TEXT NOT NULL,
        subject TEXT DEFAULT '',
        variables TEXT DEFAULT '[]',
        use_count INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    conn.commit()
    rows = conn.execute("SELECT * FROM sms_templates ORDER BY use_count DESC, created_at DESC").fetchall()
    conn.close()
    return JSONResponse({"templates": [dict(r) for r in rows]})

@app.post("/api/crm/templates")
async def crm_create_template(request: Request):
    """Create a new SMS/email template."""
    import sqlite3 as _sql
    body = await request.json()
    db = str(Path.home() / ".nexus" / "memory.db")
    conn = _sql.connect(db)
    conn.execute(
        "INSERT INTO sms_templates (name, channel, category, body, subject, variables) VALUES (?,?,?,?,?,?)",
        (body.get("name",""), body.get("channel","sms"), body.get("category","general"),
         body.get("body",""), body.get("subject",""), json.dumps(body.get("variables",[])))
    )
    conn.commit()
    conn.close()
    return JSONResponse({"ok": True})

@app.post("/api/crm/leads/{lead_id}/send-template")
async def crm_send_template(lead_id: int, request: Request):
    """Send a template message to a lead (with variable substitution)."""
    import sqlite3 as _sql
    body = await request.json()
    template_id = body.get("template_id")
    channel = body.get("channel", "sms")

    from core.lead_pipeline import get_pipeline
    p = get_pipeline()
    if not p:
        return JSONResponse({"error": "Pipeline not initialized"}, status_code=500)

    lead = p.get_lead(lead_id)
    if not lead:
        return JSONResponse({"error": "Lead not found"}, status_code=404)

    # Get template
    db = str(Path.home() / ".nexus" / "memory.db")
    conn = _sql.connect(db)
    conn.row_factory = _sql.Row
    tpl = conn.execute("SELECT * FROM sms_templates WHERE id=?", (template_id,)).fetchone()
    if not tpl:
        conn.close()
        return JSONResponse({"error": "Template not found"}, status_code=404)

    # Variable substitution
    text = tpl["body"]
    name = f"{lead.get('first_name','')} {lead.get('last_name','')}".strip()
    first = lead.get("first_name", "there") or "there"
    text = text.replace("{name}", name).replace("{first_name}", first)
    text = text.replace("{event_type}", lead.get("event_type","event") or "event")
    text = text.replace("{event_date}", lead.get("event_date","") or "your event date")
    text = text.replace("{event_city}", lead.get("event_city","") or "your area")

    # Increment use count
    conn.execute("UPDATE sms_templates SET use_count = use_count + 1 WHERE id=?", (template_id,))
    conn.commit()
    conn.close()

    # Send
    if channel == "sms" and lead.get("phone"):
        result = await p.send_sms(lead["phone"], text, lead_id)
    elif channel == "email" and lead.get("email"):
        subject = tpl["subject"] or "Message from Zoar Bathroom Rentals"
        subject = subject.replace("{name}", name).replace("{first_name}", first)
        result = await p.send_email(lead["email"], subject, text, lead_id)
    else:
        return JSONResponse({"error": f"Lead has no {channel} contact"}, status_code=400)

    return JSONResponse(result)

# ── Database Backup API ────────────────────────────────────────────────────────
@app.get("/api/system/backups")
async def system_backups():
    """List all database backups."""
    from core.db_backup import list_backups
    return JSONResponse({"backups": list_backups()})

@app.post("/api/system/backups")
async def system_create_backup(request: Request):
    """Create a manual database backup."""
    from core.db_backup import create_backup
    body = {}
    try: body = await request.json()
    except: pass
    result = create_backup(body.get("label", "manual"))
    return JSONResponse(result)

@app.post("/api/system/backups/restore")
async def system_restore_backup(request: Request):
    """Restore a database backup."""
    body = await request.json()
    name = body.get("backup_name", "")
    if not name:
        return JSONResponse({"error": "backup_name required"}, status_code=400)
    from core.db_backup import restore_backup
    result = restore_backup(name)
    return JSONResponse(result)

# ── Snapshot Manager — Version History ───────────────────────────────────────

@app.get("/api/snapshots")
async def snapshots_list():
    """List all system snapshots (DB + config + git state)."""
    from core.snapshot_manager import list_snapshots
    return JSONResponse({"snapshots": list_snapshots()})

@app.post("/api/snapshots")
async def snapshots_create(request: Request):
    """Create a named snapshot of the current system state."""
    body = await request.json()
    label = body.get("label", "manual")
    from core.snapshot_manager import create_snapshot
    result = create_snapshot(label)
    if not result.get("ok"):
        return JSONResponse(result, status_code=500)
    return JSONResponse(result)

@app.post("/api/snapshots/{snapshot_id}/restore", dependencies=[Depends(verify_action_auth)])
async def snapshots_restore(snapshot_id: str):
    """Restore DB and config from a snapshot. Auto-saves a pre-restore safety backup first."""
    from core.snapshot_manager import restore_snapshot
    result = restore_snapshot(snapshot_id)
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return JSONResponse(result)

@app.get("/api/crm/facebook/stats")
async def crm_facebook_stats():
    """Get Facebook lead stats."""
    import sqlite3 as _sql
    from pathlib import Path as _P
    db = str(_P.home() / ".nexus" / "memory.db")
    conn = _sql.connect(db)
    total = conn.execute("SELECT COUNT(*) FROM leads WHERE source IN ('facebook', 'facebook_test')").fetchone()[0]
    last_row = conn.execute(
        "SELECT discovered_at FROM leads WHERE source IN ('facebook', 'facebook_test') ORDER BY discovered_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return JSONResponse({
        "total_fb_leads": total,
        "last_fb_lead_at": last_row[0] if last_row else None,
    })

# ── Contact name refresh ─────────────────────────────────────────────────────

@app.post("/api/crm/import/refresh-names")
async def crm_refresh_names():
    """Scrape GV thread headers to get real contact names and update DB."""
    import sqlite3 as _sql
    from pathlib import Path as _P
    db = str(_P.home() / ".nexus" / "memory.db")
    gv = await _ensure_gv()
    if not gv:
        return JSONResponse({"error": "Google Voice not initialized. POST /api/browser/login-gv first."}, status_code=400)

    try:
        contacts = await gv.fetch_contact_names()
    except Exception as e:
        return JSONResponse({"error": f"GV scrape failed: {e}"}, status_code=500)

    conn = _sql.connect(db)
    updated = 0
    for c in contacts:
        phone = c["phone"]
        name = c.get("name", "").strip()
        if not name:
            continue
        # Split name into first/last
        parts = name.split(None, 1)
        first = parts[0] if parts else name
        last = parts[1] if len(parts) > 1 else ""
        # Only update if the lead currently has no name
        row = conn.execute(
            "SELECT id, first_name, last_name FROM leads WHERE REPLACE(REPLACE(phone, '-', ''), ' ', '') LIKE '%' || ? || '%'",
            (phone[-10:],)
        ).fetchone()
        if row:
            cur_first = row[1] if row[1] else ""
            cur_last = row[2] if row[2] else ""
            if not cur_first.strip() and not cur_last.strip():
                conn.execute(
                    "UPDATE leads SET first_name = ?, last_name = ?, full_name = ? WHERE id = ?",
                    (first, last, name, row[0])
                )
                updated += 1
                print(f"[CRM] Updated name for lead {row[0]}: {name}")
    conn.commit()
    conn.close()
    return JSONResponse({"ok": True, "found": len(contacts), "updated": updated, "contacts": contacts})

# ── Conversation import ───────────────────────────────────────────────────────

@app.post("/api/crm/leads/{lead_id}/import-conversations")
async def crm_import_conversations(lead_id: int):
    """Import GV + email conversation history for a lead."""
    import sqlite3 as _sql
    from pathlib import Path as _P
    db = str(_P.home() / ".nexus" / "memory.db")
    conn = _sql.connect(db)
    conn.row_factory = _sql.Row
    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    conn.close()
    if not lead:
        return JSONResponse({"error": "Lead not found"}, status_code=404)
    lead = dict(lead)

    imported_sms = 0
    imported_email = 0

    # Import GV conversation history
    if lead.get("phone"):
        try:
            gv = await _ensure_gv()
            if gv and gv.logged_in:
                messages = await gv.fetch_conversation_history(lead["phone"])
                imported_sms = _import_messages(lead_id, messages, "sms")
        except Exception as e:
            print(f"[Import] GV import error: {e}")

    # Import email history
    if lead.get("email"):
        try:
            from integrations.gmail_imap import get_imap_checker
            checker = get_imap_checker()
            if checker:
                messages = await asyncio.get_event_loop().run_in_executor(
                    None, checker.fetch_email_history, lead["email"]
                )
                imported_email = _import_messages(lead_id, messages, "email")
        except Exception as e:
            print(f"[Import] Email import error: {e}")

    return JSONResponse({
        "ok": True,
        "imported": imported_sms + imported_email,
        "sms": imported_sms,
        "email": imported_email,
    })

@app.post("/api/crm/import/all")
async def crm_import_all():
    """Bulk import: scrape ALL Google Voice conversations + Gmail, create/match leads, import messages."""
    import sqlite3 as _sql
    from pathlib import Path as _P
    db = str(_P.home() / ".nexus" / "memory.db")
    conn = _sql.connect(db)
    conn.row_factory = _sql.Row

    results = {"leads_created": 0, "leads_matched": 0, "sms_imported": 0, "email_imported": 0, "errors": []}

    # ── 1. Import all Google Voice conversations ──
    try:
        gv = await _ensure_gv()
        if gv and gv.logged_in:
            convos = await gv.fetch_all_conversations()
            for convo in convos:
                phone = convo.get("phone", "")
                name = convo.get("name", "")
                messages = convo.get("messages", [])
                if not phone:
                    continue

                # Clean the phone
                import re as _re
                clean_phone = _re.sub(r'\D', '', phone)
                if len(clean_phone) == 11 and clean_phone.startswith("1"):
                    clean_phone = clean_phone[1:]
                if len(clean_phone) != 10:
                    continue

                # Check if lead exists by phone
                row = conn.execute("SELECT id FROM leads WHERE phone LIKE ?",
                                   (f"%{clean_phone[-10:]}%",)).fetchone()
                if row:
                    lead_id = row[0]
                    results["leads_matched"] += 1
                else:
                    # Create new lead
                    name_parts = name.split(" ", 1) if name else ["", ""]
                    first = name_parts[0] if name_parts else ""
                    last = name_parts[1] if len(name_parts) > 1 else ""
                    # If name looks like a phone number or is GV icon text, clear it
                    if _re.match(r'^[\d\s\(\)\-\+]+$', first) or first.lower() in ('person', 'report', 'add_2'):
                        first = ""
                        last = ""
                    import uuid
                    ghl_id = f"gv_{clean_phone}_{uuid.uuid4().hex[:8]}"
                    try:
                        conn.execute(
                            "INSERT INTO leads (ghl_contact_id, first_name, last_name, phone, source, status, discovered_at, updated_at, booking_status) "
                            "VALUES (?, ?, ?, ?, 'google_voice', 'new', datetime('now'), datetime('now'), 'new_lead')",
                            (ghl_id, first, last, clean_phone)
                        )
                        conn.commit()
                        lead_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                        results["leads_created"] += 1
                    except Exception as e:
                        results["errors"].append(f"Create lead for {clean_phone}: {str(e)}")
                        continue

                # Import messages for this lead
                count = _import_messages(lead_id, messages, "sms")
                results["sms_imported"] += count
        else:
            results["errors"].append("Google Voice not logged in")
    except Exception as e:
        results["errors"].append(f"GV import error: {str(e)}")
        print(f"[Import] GV bulk error: {e}")

    # ── 2. Import email conversations for all leads with email ──
    try:
        from integrations.gmail_imap import get_imap_checker
        checker = get_imap_checker()
        if checker:
            leads_with_email = conn.execute(
                "SELECT id, email FROM leads WHERE email != '' AND email IS NOT NULL"
            ).fetchall()
            for lead_row in leads_with_email:
                lead_id = lead_row[0]
                email_addr = lead_row[1]
                try:
                    email_msgs = await asyncio.get_event_loop().run_in_executor(
                        None, checker.fetch_email_history, email_addr
                    )
                    count = _import_messages(lead_id, email_msgs, "email")
                    results["email_imported"] += count
                except Exception as e:
                    results["errors"].append(f"Email import for {email_addr}: {str(e)}")
        else:
            results["errors"].append("IMAP checker not initialized")
    except Exception as e:
        results["errors"].append(f"Email import error: {str(e)}")

    conn.close()
    results["ok"] = True
    results["total_imported"] = results["sms_imported"] + results["email_imported"]
    return JSONResponse(results)


@app.post("/api/crm/import/sync-gmail")
async def crm_import_sync_gmail(request: Request):
    """Backfill recent Gmail inbox + sent history into CRM conversations."""
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    try:
        days = max(1, min(int(body.get("days", 14)), 90))
    except Exception:
        days = 14
    try:
        limit_per_folder = max(50, min(int(body.get("limit_per_folder", 400)), 2000))
    except Exception:
        limit_per_folder = 400

    from core.lead_pipeline import get_pipeline
    pipeline = get_pipeline()
    if not pipeline:
        return JSONResponse({"error": "Pipeline not initialized"}, status_code=400)

    from integrations.gmail_imap import get_imap_checker
    checker = get_imap_checker()
    if not checker:
        return JSONResponse({"error": "IMAP checker not initialized"}, status_code=400)

    account_email = (load_config().get("gmail_address", "") or "").strip().lower()
    try:
        messages = await asyncio.get_event_loop().run_in_executor(
            None, checker.fetch_recent_account_messages, days, limit_per_folder
        )
    except Exception as e:
        return JSONResponse({"error": f"Gmail fetch failed: {e}"}, status_code=500)

    stats = _sync_gmail_messages_to_leads(pipeline, account_email, messages)
    stats.update({
        "ok": True,
        "days": days,
        "limit_per_folder": limit_per_folder,
        "fetched": len(messages),
    })

    if stats.get("imported", 0) > 0:
        await broadcast({
            "type": "ghl_event",
            "message": f"Gmail sync imported {stats['imported']} conversation message(s).",
        })
    return JSONResponse(stats)

@app.get("/api/crm/import/debug-gv")
async def crm_import_debug_gv():
    """Debug: inspect Google Voice page structure to fix selectors."""
    gv = await _ensure_gv()
    if not gv or not gv.logged_in:
        return JSONResponse({"error": "GV not logged in"})
    async with gv._lock:
        try:
            await gv.page.goto(f"https://voice.google.com/u/0/messages", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(5)
            # Take debug screenshot
            await gv._debug_screenshot("gv_messages_debug")
            # Try multiple selectors and report what we find
            info = await gv.page.evaluate("""() => {
                const results = {};
                results.url = window.location.href;
                results.title = document.title;
                // Try various selectors
                const selectors = [
                    '[role="listitem"]',
                    '[role="list"] > *',
                    '[class*="thread"]',
                    '[class*="conversation"]',
                    '[class*="message-item"]',
                    'a[href*="/messages/"]',
                    '[data-thread-id]',
                    'md-virtual-repeat-container *',
                    '.gvMessagingView-conversationListItem',
                    'gv-thread-item',
                    'gv-message-list-item',
                    '[gv-thread-id]',
                ];
                for (const sel of selectors) {
                    const els = document.querySelectorAll(sel);
                    if (els.length > 0) {
                        results[sel] = {
                            count: els.length,
                            first_text: (els[0].innerText || '').slice(0, 200),
                            first_tag: els[0].tagName,
                            first_classes: els[0].className.slice(0, 200),
                        };
                    }
                }
                // Get body first-level children for structure
                results.body_structure = Array.from(document.body.children).map(el => ({
                    tag: el.tagName,
                    id: el.id,
                    className: (el.className || '').slice(0, 100),
                    childCount: el.children.length,
                }));
                // Look for any element with phone-like text
                const all = document.querySelectorAll('*');
                const withPhone = [];
                for (const el of all) {
                    if (el.children.length === 0) {
                        const t = (el.innerText || '').trim();
                        if (t.match(/\\(\\d{3}\\)\\s*\\d{3}/) || t.match(/\\d{3}[-.]\\d{3}[-.]\\d{4}/)) {
                            withPhone.push({
                                tag: el.tagName,
                                class: (el.className || '').slice(0, 100),
                                text: t.slice(0, 100),
                                parentTag: el.parentElement ? el.parentElement.tagName : '',
                                parentClass: el.parentElement ? (el.parentElement.className || '').slice(0, 100) : '',
                            });
                            if (withPhone.length >= 10) break;
                        }
                    }
                }
                results.elements_with_phones = withPhone;

                // Inspect gv-thread-item and direct children of threads container
                const threadsDiv = document.querySelector('.threads');
                if (threadsDiv) {
                    const children = Array.from(threadsDiv.children);
                    results.threads_children = {
                        count: children.length,
                        tags: children.slice(0, 10).map(c => ({
                            tag: c.tagName,
                            class: (c.className || '').slice(0, 150),
                            text: (c.innerText || '').slice(0, 150),
                        })),
                    };
                }

                // Inspect actual thread list items (LI inside OL.list)
                const ol = document.querySelector('cdk-virtual-scroll-viewport ol.list');
                if (ol) {
                    const lis = Array.from(ol.children);
                    results.list_items = {
                        count: lis.length,
                        items: lis.slice(0, 5).map(c => ({
                            tag: c.tagName,
                            class: (c.className || '').slice(0, 200),
                            phone: ((c.querySelector('gv-annotation.participants') || {}).textContent || '').replace(/\u202a|\u202c/g, '').trim(),
                            text_preview: (c.innerText || '').slice(0, 200),
                            link: c.querySelector('a') ? c.querySelector('a').getAttribute('href') : '',
                        })),
                    };
                }

                return results;
            }""")
            return JSONResponse(info)
        except Exception as e:
            return JSONResponse({"error": str(e)})

@app.get("/api/crm/import/debug-gv-thread")
async def crm_debug_gv_thread():
    """Debug: click the first GV thread and inspect the message structure."""
    gv = await _ensure_gv()
    if not gv or not gv.logged_in:
        return JSONResponse({"error": "GV not logged in"})
    async with gv._lock:
        try:
            await gv.page.goto(f"https://voice.google.com/u/0/messages", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(5)
            # Click the first list item using Playwright native click
            await gv.page.click("cdk-virtual-scroll-viewport ol.list > li.list-item:nth-child(1)")
            await asyncio.sleep(5)
            # Take screenshot
            await gv._debug_screenshot("gv_thread_open")
            # Inspect the message area
            info = await gv.page.evaluate("""() => {
                const results = {};
                results.url = window.location.href;
                // Try various message selectors
                const selectors = [
                    'gv-text-message-list', 'gv-text-message-item',
                    '[class*="text-msg"]', '[class*="message-text"]',
                    '[class*="incoming"]', '[class*="outgoing"]',
                    '.message', '[data-message-text]',
                    'gv-annotation[class*="text"]',
                    '.text-content', '.msg-content',
                ];
                for (const sel of selectors) {
                    const els = document.querySelectorAll(sel);
                    if (els.length > 0) {
                        results[sel] = {
                            count: els.length,
                            first_tag: els[0].tagName,
                            first_class: (els[0].className || '').slice(0, 200),
                            first_text: (els[0].innerText || '').slice(0, 300),
                        };
                    }
                }
                // Also look for the main content area
                const main = document.querySelector('gv-message-list, [class*="messageList"], .conversationContent');
                if (main) {
                    const kids = Array.from(main.children).slice(0, 5);
                    results.message_list_children = kids.map(c => ({
                        tag: c.tagName,
                        class: (c.className || '').slice(0, 200),
                        text: (c.innerText || '').slice(0, 200),
                    }));
                }
                // Try the conversation container
                const conv = document.querySelector('[class*="conversation"]');
                if (conv) {
                    results.conversation_el = {
                        tag: conv.tagName,
                        class: (conv.className || '').slice(0, 200),
                        childTags: Array.from(conv.children).slice(0, 10).map(c => c.tagName + '.' + (c.className || '').slice(0, 50)),
                    };
                }
                return results;
            }""")
            return JSONResponse(info)
        except Exception as e:
            return JSONResponse({"error": str(e)})

@app.get("/api/crm/import/status")
async def crm_import_status():
    """Check if GV is logged in and IMAP is available for import."""
    gv_ready = False
    imap_ready = False
    sheet_status = {
        "enabled": _is_truthy(load_config().get("google_sheet_crm_enabled", True), True),
        "running": False,
        "last_error": "",
    }
    try:
        from integrations.google_voice import get_gv
        gv = get_gv()
        gv_ready = bool(gv and gv.logged_in)
    except:
        pass
    try:
        from integrations.gmail_imap import get_imap_checker
        imap_ready = bool(get_imap_checker())
    except:
        pass
    try:
        if _sheet_crm_watcher:
            status = _sheet_crm_watcher.get_status()
            sheet_status.update(status)
    except Exception as e:
        sheet_status["last_error"] = str(e)
    return JSONResponse({"gv_ready": gv_ready, "imap_ready": imap_ready, "sheet_crm": sheet_status})


@app.post("/api/crm/sync/gv-now")
async def crm_sync_gv_now():
    """Force a one-shot Google Voice reply poll and process any new inbound SMS."""
    gv = await _ensure_gv()
    if not gv or not gv.logged_in:
        return JSONResponse({"ok": False, "error": "Google Voice not logged in"}, status_code=400)

    replies = await gv.check_replies()
    processed = []
    for reply in replies:
        phone = _normalize_phone(reply.get("phone", ""))
        message = (reply.get("message", "") or "").strip()
        timestamp = reply.get("timestamp", "")
        if not phone or not message:
            continue
        await _handle_gv_reply(
            phone,
            message,
            timestamp,
            reply.get("name", ""),
        )
        processed.append({
            "phone": phone,
            "message": message,
            "timestamp": timestamp,
            "name": reply.get("name", ""),
        })

    return JSONResponse({
        "ok": True,
        "found": len(replies),
        "processed": len(processed),
        "replies": processed,
    })


@app.get("/api/crm/sync/google-sheet-status")
async def crm_google_sheet_status():
    cfg = load_config()
    try:
        if _sheet_crm_watcher:
            return JSONResponse({"ok": True, "status": _sheet_crm_watcher.get_status()})
        return JSONResponse(
            {
                "ok": True,
                "status": {
                    "enabled": _is_truthy(cfg.get("google_sheet_crm_enabled", True), True),
                    "running": False,
                    "sheet_name": (cfg.get("google_sheet_crm_name") or "Facebook Lead Ads Form CRM"),
                    "spreadsheet_id": cfg.get("google_sheet_crm_id", ""),
                    "last_error": "Watcher not started",
                },
            }
        )
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/crm/sync/google-sheet-now", dependencies=[Depends(verify_action_auth)])
async def crm_google_sheet_sync_now(request: Request):
    cfg = load_config()
    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    force = _is_truthy(body.get("force", False), False)
    restart = _is_truthy(body.get("restart", False), False)

    if restart or not _sheet_crm_watcher:
        await _start_google_sheet_crm_sync(cfg, force_restart=True)

    if not _sheet_crm_watcher:
        return JSONResponse({"ok": False, "error": "Google Sheet CRM watcher unavailable"}, status_code=400)

    result = await _sheet_crm_watcher.poll_once(force=force)
    status_code = 200 if result.get("ok") else 500
    return JSONResponse(result, status_code=status_code)

def _import_messages(lead_id: int, messages: list, default_channel: str) -> int:
    """Insert imported messages, deduplicating against existing."""
    if not messages:
        return 0
    import sqlite3 as _sql
    from pathlib import Path as _P
    db = str(_P.home() / ".nexus" / "memory.db")
    conn = _sql.connect(db)
    existing_precise = set()
    existing_fallback = set()
    for row in conn.execute(
        "SELECT content, ts, direction, channel FROM lead_messages WHERE lead_id = ?", (lead_id,)
    ).fetchall():
        content = (row[0] or "")[:200]
        ts = (row[1] or "")[:16]
        direction = row[2] or "inbound"
        channel = row[3] or default_channel
        existing_precise.add((content, direction, channel, ts))
        existing_fallback.add((content, direction, channel))

    count = 0
    from datetime import datetime as _dt
    for msg in messages:
        content = msg.get("content", "")
        direction = msg.get("direction", "inbound")
        channel = msg.get("channel", default_channel)
        if (channel or default_channel).strip().lower() == "sms":
            content = _sanitize_inbound_sms_text(content)
            if not content:
                continue
        raw_ts = (msg.get("timestamp", "") or "").strip()
        ts = raw_ts
        # Parse human-readable timestamps from GV (e.g. "February 25 2026, 9:03 PM") into ISO format
        if ts and not ts.startswith("20"):
            try:
                parsed = _dt.strptime(ts.strip(), "%B %d %Y, %I:%M %p")
                ts = parsed.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                try:
                    parsed = _dt.strptime(ts.strip(), "%B %d, %Y, %I:%M %p")
                    ts = parsed.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass
        # Fallback: use current time if ts is still empty/invalid
        timestamp_uncertain = False
        if not ts:
            ts = _dt.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            timestamp_uncertain = True
        dedup_precise = (content[:200], direction, channel, ts[:16])
        dedup_fallback = (content[:200], direction, channel)
        if dedup_precise in existing_precise:
            continue
        if timestamp_uncertain and dedup_fallback in existing_fallback:
            continue
        conn.execute(
            "INSERT INTO lead_messages (lead_id, ts, direction, channel, method, content, subject, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (lead_id, ts, direction, channel,
             "imported", content, msg.get("subject", ""), "imported")
        )
        existing_precise.add(dedup_precise)
        existing_fallback.add(dedup_fallback)
        count += 1
    conn.commit()
    conn.close()
    return count

@app.get("/", response_class=HTMLResponse)
async def index():
    cfg = load_config()
    return HTMLResponse(SETUP if not has_key(cfg) else CHAT)

@app.get("/api/events")
async def sse_stream(request: Request):
    q: asyncio.Queue = asyncio.Queue()
    _sse_clients.append(q)
    recent = list(_session_events[-50:])
    async def generate():
        try:
            for ev in recent:
                yield f"data: {json.dumps(ev)}\n\n"
            while True:
                if await request.is_disconnected(): break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type':'ping'})}\n\n"
        finally:
            try: _sse_clients.remove(q)
            except: pass
    return StreamingResponse(generate(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.post("/api/chat")
async def chat(request: Request):
    cfg = load_config()
    if not has_key(cfg): return JSONResponse({"error": "No keys"}, status_code=401)
    body = await request.json()
    message = body.get("message", ""); history = body.get("history", [])
    msg_lower = message.lower()
    orch = get_orch()

    async def generate():
        import re as _re
        # ── Detect intent ─────────────────────────────────────────────────────
        email_kws = ["send email","send an email","send a email","email to","send mail","send a mail"]
        is_email = any(k in msg_lower for k in email_kws)
        # Also treat as email if "email" word present + an @ address
        if not is_email and "email" in msg_lower and _re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', message):
            is_email = True

        sms_kws = ["send sms","send a text","send text","send a message","send message","text to","sms to",
                   "send a sms","send an sms","send me a text","send me a sms","send an sms message",
                   "message to","text message to"]
        is_sms = not is_email and any(k in msg_lower for k in sms_kws)
        # Also match: any message with a phone number + "saying/say" (strong intent signal)
        if not is_sms and not is_email and _re.search(r'\d{3}.*\d{3}.*\d{4}', msg_lower) and _re.search(r'\b(?:saying|say)\b', msg_lower):
            is_sms = True
        img_kws = ["generate image","create image","make image","generate a picture","create a picture","make a picture","draw","generate an image","create an image","image of","picture of a","photo of"]
        is_img = any(k in msg_lower for k in img_kws)

        if is_sms:
            yield f"data: {json.dumps({'type':'status','message':'📱 Sending SMS...'})}\n\n"
            # Extract phone number
            phone_m = _re.search(r'(\+?1?\s?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4})', message)
            phone = phone_m.group(1) if phone_m else ""
            # Extract message text (between quotes or after "saying")
            msg_m = _re.search(r'(?:saying|say)\s+["\']?(.+?)["\']?\s*$', message, _re.IGNORECASE)
            if not msg_m:
                # Try: "with message ..." or quoted text anywhere
                msg_m = _re.search(r'(?:with\s+(?:message|text))\s+["\']?(.+?)["\']?\s*$', message, _re.IGNORECASE)
            if not msg_m:
                # Try: anything in quotes
                msg_m = _re.search(r'["\'](.+?)["\']', message)
            sms_text = msg_m.group(1).strip().strip('"\' ') if msg_m else "Hey!"
            if not phone:
                resp = {"type":"direct_response","content":"📱 I can send SMS! Just give me a phone number. What number should I text?"}
            else:
                try:
                    # Try pipeline send_sms (GV → Twilio → email gateway)
                    from core.lead_pipeline import get_pipeline
                    pipeline = get_pipeline()
                    if pipeline:
                        result = await pipeline.send_sms(phone, sms_text)
                        if result.get("ok"):
                            method = result.get("method", "unknown")
                            resp = {"type":"direct_response","content":f"✅ **SMS sent!**\n\nTo: `{phone}`\nMessage: *{sms_text}*\n\nDelivered via {method}."}
                            await broadcast({"type":"ghl_event","message":f"📱 SMS sent to {phone} via {method}"})
                        else:
                            err = result.get("error","unknown")
                            resp = {"type":"direct_response","content":f"❌ **SMS failed:** {err}\n\nTry logging into Google Voice first:\n`POST /api/browser/login-gv`"}
                    else:
                        # Fallback to old email gateway if pipeline not running
                        from integrations.zoar_bot import send_sms
                        gmail = cfg.get("gmail_address",""); pw = cfg.get("gmail_app_password","")
                        carrier = cfg.get("default_carrier","tmobile")
                        if not gmail or not pw:
                            resp = {"type":"direct_response","content":"⚠️ **SMS not available.** Google Voice not logged in and Gmail not configured.\n\nLog into Google Voice: `POST /api/browser/login-gv`"}
                        else:
                            result = await send_sms(gmail, pw, phone, sms_text, carrier)
                            if result.get("ok"):
                                resp = {"type":"direct_response","content":f"✅ **SMS sent!**\n\nTo: `{phone}`\nMessage: *{sms_text}*\n\nDelivered via email-to-SMS gateway."}
                                await broadcast({"type":"ghl_event","message":f"📱 SMS sent to {phone}"})
                            else:
                                resp = {"type":"direct_response","content":f"❌ **SMS failed:** {result.get('error','unknown')}"}
                except Exception as e:
                    resp = {"type":"direct_response","content":f"❌ Error: {str(e)}"}
            yield f"data: {json.dumps(resp)}\n\n"
            yield f"data: {json.dumps({'type':'done'})}\n\n"
            return

        # ── Intercept email requests ──────────────────────────────────────────
        email_kws = ["send email","send an email","send a email","email to","send mail","send a mail"]
        is_email = any(k in msg_lower for k in email_kws)
        if is_email:
            yield f"data: {json.dumps({'type':'status','message':'✉️ Sending email...'})}\n\n"
            # Extract email address
            email_m = _re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', message)
            to_email = email_m.group(0) if email_m else ""
            # Extract subject: "subject ..." or "with subject ..."
            subj_m = _re.search(r'(?:with\s+)?subject\s+["\']?(.+?)["\']?\s*(?:saying|say|body|message|$)', message, _re.IGNORECASE)
            subject = subj_m.group(1).strip().strip('"\'') if subj_m else "Message from Zoar Bathroom Rentals"
            # Extract body: after "saying/say" or "body/message" keyword, or quoted text
            body_m = _re.search(r'(?:saying|say|body|message)\s+["\']?(.+?)["\']?\s*$', message, _re.IGNORECASE)
            if not body_m:
                body_m = _re.search(r'["\'](.+?)["\']', message)
            email_body = body_m.group(1).strip().strip('"\'') if body_m else "Hey! This is Kai from Zoar Bathroom Rentals."
            if not to_email:
                resp = {"type":"direct_response","content":"✉️ I can send emails! Just include the email address. Example: *send an email to someone@gmail.com saying hello*"}
            else:
                try:
                    gmail = cfg.get("gmail_address",""); pw = cfg.get("gmail_app_password","")
                    if not gmail or not pw:
                        resp = {"type":"direct_response","content":"⚠️ **Email not configured.** Add `gmail_address` and `gmail_app_password` in Settings."}
                    else:
                        from integrations.messaging import get_messenger
                        m = get_messenger()
                        if m:
                            result = await m.send_email(to_email, subject, email_body)
                        else:
                            from integrations.zoar_bot import send_email as _send_email
                            result = await _send_email(gmail, pw, to_email, subject, email_body)
                        if result.get("ok"):
                            resp = {"type":"direct_response","content":f"✅ **Email sent!**\n\nTo: `{to_email}`\nSubject: *{subject}*\nBody: *{email_body}*"}
                            await broadcast({"type":"ghl_event","message":f"✉️ Email sent to {to_email}"})
                        else:
                            resp = {"type":"direct_response","content":f"❌ **Email failed:** {result.get('error','unknown')}"}
                except Exception as e:
                    resp = {"type":"direct_response","content":f"❌ Error: {str(e)}"}
            yield f"data: {json.dumps(resp)}\n\n"
            yield f"data: {json.dumps({'type':'done'})}\n\n"
            return

        # ── Intercept image generation ────────────────────────────────────────
        if is_img:
            import urllib.parse as _up
            import httpx as _httpx
            yield f"data: {json.dumps({'type':'status','message':'🎨 Generating image...','detail':'Using pollinations.ai (free) — this may take 10-20 seconds'})}\n\n"
            # Extract the image subject
            prompt = message
            for prefix in ["generate image of","create image of","make image of","generate a picture of","create a picture of","generate an image of","create an image of","image of","draw me","draw a","generate","create","make"]:
                if msg_lower.startswith(prefix):
                    prompt = message[len(prefix):].strip(); break
            encoded = _up.quote(prompt[:500])
            url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&nologo=true&enhance=true"
            # Pre-fetch the image so it's actually generated before we send the URL
            yield f"data: {json.dumps({'type':'image_loading','prompt':prompt,'url':url})}\n\n"
            image_ok = False
            try:
                async with _httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200 and resp.headers.get("content-type","").startswith("image"):
                        image_ok = True
            except Exception:
                pass  # Fallback — send URL anyway, browser will retry

            if image_ok:
                resp1 = {"type":"direct_response","content":f"🎨 Here's your image: **{prompt}**\n\nIMAGE_URL: {url}"}
            else:
                resp1 = {"type":"direct_response","content":f"🎨 Generating: **{prompt}** (image may still be loading)\n\nIMAGE_URL: {url}"}
            yield f"data: {json.dumps(resp1)}\n\n"
            yield f"data: {json.dumps({'type':'agent_done','agent_id':'image_gen','image_url':url,'result':url,'model':'pollinations.ai','steps':['Pre-fetched image' if image_ok else 'Image warming up'],'duration':0,'cost':0})}\n\n"
            yield f"data: {json.dumps({'type':'done'})}\n\n"
            return

        # ── Normal orchestrator flow ──────────────────────────────────────────
        async for ev in orch.run(message, history):
            yield f"data: {json.dumps(ev)}\n\n"
            await broadcast(ev)
        yield f"data: {json.dumps({'type':'done'})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")

@app.post("/api/save-config")
async def save_cfg(request: Request):
    body = await request.json()
    keys = {}
    for f in ["anthropic_key","moonshot_key","gemini_key","openai_key","telegram_token","telegram_chat_ids","ollama_model","sync_secret"]:
        v = body.get(f)
        if v is not None: keys[f] = v if isinstance(v, list) else str(v).strip()
    save_config(keys); global _orch; _orch = None
    return JSONResponse({"ok": True})

@app.get("/api/status")
async def status():
    cfg = load_config()
    if not has_key(cfg): return {"setup_needed": True}
    from core.providers import LLMProvider
    p = LLMProvider(cfg)
    return {"setup_needed": False, "available_models": p.available(),
            "telegram_enabled": bool(cfg.get("telegram_token")),
            "connected_clients": len(_sse_clients)}

@app.get("/api/fleet/status")
async def fleet_status():
    """Full AI provider fleet status — keys, RPM, workers, health."""
    try:
        from core.key_rotation import get_pool
        pool = get_pool()
        return pool.fleet_status()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/fleet/test/{provider_id}")
async def fleet_test_provider(provider_id: str):
    """Send a test ping to a specific provider to verify connectivity."""
    try:
        from core.worker_pool import call_provider
        result = await call_provider(
            provider_id,
            [{"role": "user", "content": "Reply with exactly: OK"}],
            system="Reply with exactly one word: OK",
            max_tokens=10,
            temperature=0,
        )
        return {
            "provider": provider_id,
            "ok": result["ok"],
            "content": result.get("content", "")[:100],
            "latency_ms": result.get("latency_ms", 0),
            "model": result.get("model", ""),
        }
    except Exception as e:
        import traceback
        return JSONResponse({
            "error": str(e),
            "trace": traceback.format_exc(),
            "provider": provider_id
        }, status_code=500)

@app.get("/api/fleet/workers")
async def fleet_workers():
    """Three-tier worker governor status — active workers, tiers, shift."""
    try:
        from core.worker_tiers import get_governor
        gov = get_governor()
        return gov.get_status()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/fleet/tasks")
async def fleet_task_stats():
    """Fleet task queue statistics — pending, completed, dead letter."""
    try:
        from core.fleet_task_queue import FleetTaskQueue
        q = FleetTaskQueue()
        return q.get_stats()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/fleet/tasks/activity")
async def fleet_task_activity():
    """Recent task activity feed for live dashboard."""
    try:
        from core.fleet_task_queue import FleetTaskQueue
        q = FleetTaskQueue()
        return {"activity": q.get_activity_feed(100)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/fleet/tasks/dead-letters")
async def fleet_dead_letters():
    """Dead letter queue for manual review."""
    try:
        from core.fleet_task_queue import FleetTaskQueue
        q = FleetTaskQueue()
        return {"dead_letters": q.get_dead_letters(50)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/fleet/tasks/retry-dead-letters")
async def fleet_retry_dead_letters():
    """Retry all eligible dead letter tasks."""
    try:
        from core.fleet_task_queue import FleetTaskQueue
        q = FleetTaskQueue()
        count = q.retry_dead_letters()
        return {"retried": count}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/fleet/resources")
async def fleet_resources():
    """Mac Mini resource monitor — CPU, RAM, Ollama scale factor."""
    try:
        from core.resource_monitor import get_monitor
        return get_monitor().get_status()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ── Master Coordinator ─────────────────────────────────────────────────────

@app.get("/api/coordinator/status")
async def coordinator_status():
    """Master Coordinator status — cycle count, utilization, divisions."""
    try:
        from core.master_coordinator import get_coordinator
        return get_coordinator().get_status()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/coordinator/pause")
async def coordinator_pause():
    """Pause task generation."""
    try:
        from core.master_coordinator import get_coordinator
        get_coordinator().pause()
        return {"status": "paused"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/coordinator/resume")
async def coordinator_resume():
    """Resume task generation."""
    try:
        from core.master_coordinator import get_coordinator
        get_coordinator().resume()
        return {"status": "resumed"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/fleet/utilization")
async def fleet_utilization():
    """Real-time fleet utilization for dashboard."""
    try:
        from core.fleet_task_queue import FleetTaskQueue
        from core.worker_tiers import get_governor
        from core.master_coordinator import get_coordinator

        q = FleetTaskQueue()
        stats = q.get_stats()
        gov_status = get_governor().get_status()
        coord_status = get_coordinator().get_status()

        pending = stats.get("pending", 0)
        claimed = stats.get("claimed", 0)
        in_progress = stats.get("in_progress", 0)
        active = claimed + in_progress
        total_slots = gov_status.get("max_total", 310)

        if total_slots > 0:
            utilization_pct = round((active / total_slots) * 100, 1)
        else:
            utilization_pct = 0

        return {
            "utilization_pct": utilization_pct,
            "queue_depth": pending,
            "active_tasks": active,
            "completed_today": stats.get("today", {}).get("completed", 0),
            "tokens_today": stats.get("today", {}).get("tokens", 0),
            "total_workers": gov_status.get("total_active", 0),
            "max_workers": total_slots,
            "by_tier": gov_status.get("tiers", {}),
            "coordinator": coord_status,
            "shift": gov_status.get("shift", "day"),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Agent Studio Endpoints ──────────────────────────────────────────────────

@app.get("/api/studio/status")
async def studio_status():
    """Full Agent Studio status: all 5 agents with current state."""
    _agent_template = {
        "status": "idle", "current_task": None,
        "tasks_completed_today": 0, "average_time_seconds": 0,
    }
    try:
        from core.agent_studio import get_studio
        studio = get_studio()
        data = studio.get_status()
        # Ensure Bug Hunter is included
        if "bughunter" not in data.get("agents", {}):
            data.setdefault("agents", {})["bughunter"] = {
                "agent_id": "bughunter-daemon",
                "agent_type": "bughunter",
                "model": "llama-4-scout",
                "status": "idle",
                "current_task": None,
                "tasks_completed": 0,
                "last_active": 0,
                "name": "Bug Hunter", "role": "Scans for bugs every 10 min",
                "tasks_completed_today": 0, "average_time_seconds": 0,
            }
        # Enrich each agent with name/role for the frontend
        _roles = {
            "architect": ("Architect", "Plans implementations — never writes code"),
            "builder": ("Builder", "Writes code from architect plans"),
            "reviewer": ("Reviewer", "Reviews code, flags issues"),
            "patcher": ("Patcher", "Fixes issues found by reviewer"),
            "bughunter": ("Bug Hunter", "Scans for bugs every 10 min"),
        }
        for key, agent in data.get("agents", {}).items():
            name, role = _roles.get(key, (key.title(), ""))
            agent["name"] = name
            agent["role"] = role
            agent.setdefault("tasks_completed_today", agent.get("tasks_completed", 0))
            agent.setdefault("average_time_seconds", 0)
        return JSONResponse(data)
    except Exception:
        # Hardcoded fallback so the UI always renders
        agents = {
            "architect": {
                **_agent_template, "agent_id": "architect-000000",
                "agent_type": "architect", "model": "gemini-2.5-flash",
                "name": "Architect", "role": "Plans implementations — never writes code",
                "last_active": 0, "tasks_completed": 0,
            },
            "builder": {
                **_agent_template, "agent_id": "builder-000000",
                "agent_type": "builder", "model": "qwen3:8b",
                "name": "Builder", "role": "Writes code from architect plans",
                "last_active": 0, "tasks_completed": 0,
            },
            "reviewer": {
                **_agent_template, "agent_id": "reviewer-000000",
                "agent_type": "reviewer", "model": "glm-4.5-flash",
                "name": "Reviewer", "role": "Reviews code, flags issues",
                "last_active": 0, "tasks_completed": 0,
            },
            "patcher": {
                **_agent_template, "agent_id": "patcher-000000",
                "agent_type": "patcher", "model": "glm-4.5-flash",
                "name": "Patcher", "role": "Fixes issues found by reviewer",
                "last_active": 0, "tasks_completed": 0,
            },
            "bughunter": {
                **_agent_template, "agent_id": "bughunter-daemon",
                "agent_type": "bughunter", "model": "llama-4-scout",
                "name": "Bug Hunter", "role": "Scans for bugs every 10 min",
                "last_active": 0, "tasks_completed": 0,
            },
        }
        return JSONResponse({
            "agents": agents,
            "active_builds": 0,
            "total_sub_agents": 0,
            "max_parallel_builds": 5,
            "max_total_concurrent": 20,
            "file_locks": [],
            "running": False,
        })


@app.post("/api/studio/submit")
async def studio_submit(request: Request):
    """Submit a new task to the Agent Studio pipeline."""
    try:
        body = await request.json()
        description = body.get("description", "")
        priority = body.get("priority", 5)
        if not description:
            return JSONResponse({"error": "description required"}, status_code=400)
        from core.agent_studio import get_studio
        result = await get_studio().submit_task(description, priority)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/studio/file-locks")
async def studio_file_locks():
    """Current file lock map."""
    try:
        from core.agent_studio import get_studio
        locks = get_studio().lock_mgr.get_locks()
        return JSONResponse({"locks": locks})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/studio/tasks")
async def studio_tasks():
    """Task history from fleet_task_queue for studio tasks."""
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT task_id, task_type, status, priority, created_at, completed_at, "
            "result_data, provider_used, model_used, processing_ms "
            "FROM fleet_tasks WHERE task_type LIKE 'studio_%' "
            "ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        conn.close()
        return JSONResponse({"tasks": [dict(r) for r in rows]})
    except Exception:
        return JSONResponse({"tasks": [], "total": 0, "status": "unavailable"})


@app.get("/api/studio/bugs")
async def studio_bugs(status: str = "open", severity: str = ""):
    """Bug reports from Bug Hunter."""
    try:
        from core.bug_hunter import BugHunter
        hunter = BugHunter()
        if severity:
            bugs = hunter.get_open_bugs(severity=severity)
        else:
            bugs = hunter.get_open_bugs() if status == "open" else []
            if status != "open":
                conn = sqlite3.connect(str(DB_PATH), timeout=5)
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM bug_reports WHERE status=? "
                    "ORDER BY found_at DESC LIMIT 100", (status,)
                ).fetchall()
                conn.close()
                bugs = [dict(r) for r in rows]
        return JSONResponse({"bugs": bugs, "count": len(bugs)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/studio/bugs/{bug_id}/resolve")
async def studio_resolve_bug(bug_id: int):
    """Mark a bug report as resolved."""
    try:
        from core.bug_hunter import BugHunter
        hunter = BugHunter()
        ok = hunter.resolve_bug(bug_id, resolved_by="manual")
        return JSONResponse({"ok": ok})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/studio/metrics")
async def studio_metrics():
    """Agent performance metrics."""
    try:
        from core.agent_studio import get_studio
        metrics = get_studio().get_metrics()
        return JSONResponse(metrics)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Key Registration ───────────────────────────────────────────────────────

KEY_PREFIXES = [
    ("sk-or-v1-", "openrouter"),
    ("csk-", "cerebras"),
    ("gsk_", "groq"),
    ("AIzaSy", "gemini"),
    ("hf_", "huggingface"),
    ("apf_", "apifreellm"),
]

def _get_provider_name(provider_id: str) -> str:
    try:
        from core.key_rotation import PROVIDERS
        return PROVIDERS.get(provider_id, {}).get("name", provider_id)
    except Exception:
        return provider_id

def detect_provider(key: str) -> str:
    """Auto-detect provider from API key prefix."""
    for prefix, provider in KEY_PREFIXES:
        if key.startswith(prefix):
            return provider
    # Z.AI: hex.alphanumeric (e.g. 8753b5b7e250446795605de3f45e981e.LvZzqdL87V5uRM36)
    if "." in key and len(key) > 40:
        parts = key.split(".")
        if len(parts) == 2 and all(c in "0123456789abcdef" for c in parts[0]):
            return "zai"
    # Moonshot/Kimi: sk- but not sk-or- or sk-ant-
    if key.startswith("sk-") and not key.startswith("sk-or") and not key.startswith("sk-ant"):
        return "moonshot"
    # Mistral: 32-char alphanumeric (no prefix)
    if len(key) == 32 and key.isalnum():
        return "mistral"
    return "unknown"


@app.get("/api/keys/list")
async def keys_list():
    """List all registered API keys (masked) grouped by provider."""
    try:
        cfg_path = Path.home() / ".nexus" / "config.json"
        cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
        pools = cfg.get("key_pools", {})
        result = {}
        total = 0
        for provider, keys in pools.items():
            masked = []
            for k in keys:
                if len(k) > 12:
                    masked.append(k[:6] + "..." + k[-4:])
                else:
                    masked.append("***")
            result[provider] = {"count": len(keys), "keys": masked}
            total += len(keys)
        return {"providers": result, "total_keys": total}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/keys/register")
async def keys_register(request: Request):
    """Register a new API key — auto-detect provider, validate, save."""
    try:
        body = await request.json()
        key = body.get("key", "").strip()
        if not key:
            return JSONResponse({"error": "No key provided"}, status_code=400)

        provider = detect_provider(key)
        if provider == "unknown":
            return JSONResponse({
                "error": "Could not detect provider from key prefix",
                "key_preview": key[:8] + "..."
            }, status_code=400)

        # Check for duplicate
        cfg_path = Path.home() / ".nexus" / "config.json"
        cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
        pools = cfg.get("key_pools", {})
        existing = pools.get(provider, [])
        if key in existing:
            return {"status": "duplicate", "provider": provider, "message": "Key already registered"}

        # Validate with a test call
        validated = False
        validation_error = ""
        try:
            from core.worker_pool import call_provider_with_key
            result = await call_provider_with_key(
                provider, key,
                [{"role": "user", "content": "Reply with exactly: OK"}],
                system="Reply with exactly one word: OK",
                max_tokens=10,
                temperature=0,
            )
            validated = result.get("ok", False)
            if not validated:
                validation_error = result.get("error", "Unknown validation error")
        except ImportError:
            # call_provider_with_key doesn't exist yet — skip validation, save anyway
            validated = True
            validation_error = "skipped"
        except Exception as ve:
            validation_error = str(ve)
            # Save anyway — key might work for some models but not the default test model
            validated = True

        # Save to config
        if provider not in pools:
            pools[provider] = []
        pools[provider].append(key)
        cfg["key_pools"] = pools
        cfg_path.write_text(json.dumps(cfg, indent=2))

        # Reload key pool singleton
        from core.key_rotation import reset_pool
        reset_pool()

        total = sum(len(v) for v in pools.values())
        return {
            "status": "ok",
            "provider": provider,
            "provider_name": _get_provider_name(provider),
            "validated": validated,
            "validation_error": validation_error,
            "key_preview": key[:6] + "..." + key[-4:],
            "provider_key_count": len(pools[provider]),
            "total_keys": total,
        }
    except Exception as e:
        log.error("Key registration error: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Live Intel ─────────────────────────────────────────────────────────────
@app.get("/api/intel/live")
async def intel_live():
    """Live intelligence feed — event signals, vendor activity, outreach queue, worker activity."""
    try:
        import sqlite3
        from pathlib import Path
        db = Path.home() / ".nexus" / "memory.db"
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row

        # Event signals (from event_signals table if exists, else empty)
        event_signals = []
        try:
            rows = conn.execute(
                "SELECT * FROM event_signals ORDER BY detected_at DESC LIMIT 50"
            ).fetchall()
            event_signals = [dict(r) for r in rows]
        except sqlite3.OperationalError:
            pass

        # Vendor activity
        vendor_activity = []
        try:
            rows = conn.execute(
                """SELECT id, business_name as vendor_name,
                          'New vendor added' as signal_type,
                          category || ' in ' || COALESCE(city, area, '') as signal_detail,
                          created_at as detected_at,
                          0 as has_partnership
                   FROM vendors
                   ORDER BY created_at DESC LIMIT 30"""
            ).fetchall()
            vendor_activity = [dict(r) for r in rows]
        except sqlite3.OperationalError:
            pass

        # Outreach queue
        outreach_queue = []
        try:
            rows = conn.execute(
                """SELECT id, business_name as vendor_name,
                          COALESCE(urgency, 'medium') as urgency,
                          'partnership' as type,
                          COALESCE(outreach_message, 'Draft pending') as message_preview,
                          created_at
                   FROM vendors
                   WHERE outreach_status IN ('draft_ready', 'pending_approval')
                   ORDER BY CASE WHEN urgency = 'high' THEN 0
                                WHEN urgency = 'medium' THEN 1
                                ELSE 2 END,
                           created_at DESC
                   LIMIT 50"""
            ).fetchall()
            outreach_queue = [dict(r) for r in rows]
        except sqlite3.OperationalError:
            pass

        # Worker activity from fleet tasks
        worker_activity = []
        try:
            from core.worker_tiers import get_governor
            gov = get_governor()
            for w in gov.get_active_workers():
                worker_activity.append({
                    "worker_id": w["worker_id"],
                    "description": w.get("current_task_type", "idle"),
                    "timestamp": "",
                })
        except Exception:
            pass

        conn.close()
        return {
            "event_signals": event_signals,
            "vendor_activity": vendor_activity,
            "outreach_queue": outreach_queue,
            "worker_activity": worker_activity,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/events/command")
async def events_command():
    """Event Command Center — kanban columns from signal detection to booking."""
    try:
        import sqlite3
        from pathlib import Path
        db = Path.home() / ".nexus" / "memory.db"
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row

        columns = {
            "signal_detected": [],
            "vendors_identified": [],
            "outreach_sent": [],
            "partnership_active": [],
            "quoted": [],
            "booked": [],
        }

        # Pull leads and map them to kanban columns
        try:
            rows = conn.execute(
                """SELECT id, event_type, event_date as estimated_date,
                          COALESCE(event_city, event_address, '') as estimated_location,
                          COALESCE(guest_count, 0) as guest_count,
                          0 as distance_miles,
                          0 as vendors_identified, 0 as vendors_contacted,
                          0 as vendors_confirmed, 0 as quote_sent,
                          status
                   FROM leads ORDER BY created_at DESC LIMIT 100"""
            ).fetchall()
            for row in rows:
                card = dict(row)
                card["id"] = str(card["id"])
                card["event_type"] = card.get("event_type") or "Unknown"
                card["estimated_date"] = card.get("estimated_date") or "TBD"

                status = card.get("status", "new")
                if status == "booked":
                    columns["booked"].append(card)
                elif status == "quoted":
                    columns["quoted"].append(card)
                elif status in ("contacted", "follow-up needed"):
                    columns["outreach_sent"].append(card)
                else:
                    columns["signal_detected"].append(card)
        except sqlite3.OperationalError:
            pass

        stats = {
            "total_events": sum(len(v) for v in columns.values()),
            "active_partnerships": len(columns["partnership_active"]),
            "quotes_sent": len(columns["quoted"]),
            "booked": len(columns["booked"]),
        }

        conn.close()
        return {"columns": columns, "stats": stats}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/outreach/draft-batch")
async def outreach_draft_batch(request: Request):
    """Draft outreach messages for pending vendors. Returns drafts for Telegram approval."""
    try:
        import sqlite3
        from pathlib import Path
        db = Path.home() / ".nexus" / "memory.db"
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row

        rows = conn.execute(
            """SELECT * FROM vendors
               WHERE outreach_status = 'not_contacted'
               AND (email IS NOT NULL OR phone IS NOT NULL)
               ORDER BY CASE WHEN urgency = 'high' THEN 0
                            WHEN urgency = 'medium' THEN 1
                            ELSE 2 END
               LIMIT 25"""
        ).fetchall()
        vendors = [dict(r) for r in rows]
        conn.close()

        if not vendors:
            return {"drafted": 0, "message": "No vendors ready for outreach"}

        from core.outreach_drafter import OutreachDrafter
        drafter = OutreachDrafter()
        results = await drafter.draft_batch(vendors)

        successful = [r for r in results if r.get("ok")]
        return {"drafted": len(successful), "total_attempted": len(vendors), "drafts": successful}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/milestones")
async def milestones_status():
    """Milestone tracker — fired milestones, next targets, counts."""
    try:
        from core.milestones import get_milestone_status
        return get_milestone_status()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/milestones/check")
async def milestones_check():
    """Manually trigger milestone check and send notifications if any new milestones hit."""
    try:
        from core.milestones import check_milestones
        triggered = await check_milestones()
        return {"triggered": triggered, "count": len(triggered)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/vendor-research/status")
async def vendor_research_status():
    """Vendor research daemon status — zones, categories, discovery stats."""
    try:
        from core.vendor_research_daemon import get_daemon
        return get_daemon().get_status()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/vendor-research/run-cycle")
async def vendor_research_run_cycle():
    """Trigger a single vendor research cycle (8 categories, 4 locations each)."""
    try:
        from core.vendor_research_daemon import get_daemon
        result = await get_daemon().run_research_cycle(max_categories=8, max_locations_per_cat=4)
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Beta Research API (Isolated, Read-Only, No Outbound) ─────────────────────
@app.get("/api/beta-research/status")
async def beta_research_status():
    """Status for the isolated Beta Research engine."""
    try:
        from core.beta_research import get_beta_research_engine
        return get_beta_research_engine().get_status()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/beta-research/runs")
async def beta_research_runs(limit: int = 20):
    """List recent Beta Research runs."""
    try:
        from core.beta_research import get_beta_research_engine
        runs = get_beta_research_engine().list_runs(limit=limit)
        return {"runs": runs, "total": len(runs)}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/beta-research/stages")
async def beta_research_stages(run_id: int = 0):
    """Stage metrics for a specific run, or latest run if run_id=0."""
    try:
        from core.beta_research import get_beta_research_engine
        rid = int(run_id) if int(run_id) > 0 else None
        stages = get_beta_research_engine().list_stage_metrics(run_id=rid)
        return {"stages": stages, "total": len(stages)}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/beta-research/leads")
async def beta_research_leads(
    run_id: int = 0,
    limit: int = 150,
    tier: str = "",
    min_score: float = 0,
):
    """List beta research leads from isolated beta DB."""
    try:
        from core.beta_research import get_beta_research_engine
        rid = int(run_id) if int(run_id) > 0 else None
        leads = get_beta_research_engine().list_leads(
            run_id=rid,
            limit=limit,
            tier=(tier or "").strip().upper() or None,
            min_score=min_score,
        )
        return {"leads": leads, "total": len(leads)}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/beta-research/providers")
async def beta_research_providers():
    """Configured key/provider visibility for beta research."""
    try:
        from core.beta_research import get_beta_research_engine
        return get_beta_research_engine().provider_snapshot()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/beta-research/events")
async def beta_research_events(
    run_id: int = 0,
    limit: int = 200,
    stage_name: str = "",
    level: str = "",
):
    """Detailed event trace for a beta run (query progress, warnings, decisions)."""
    try:
        from core.beta_research import get_beta_research_engine
        rid = int(run_id) if int(run_id) > 0 else None
        events = get_beta_research_engine().list_events(
            run_id=rid,
            limit=limit,
            stage_name=(stage_name or "").strip(),
            level=(level or "").strip(),
        )
        return {"events": events, "total": len(events)}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/beta-research/run-cycle")
async def beta_research_run_cycle(request: Request):
    """
    Run one safe Beta Research cycle.
    Never sends outreach, never logs into social platforms, queue-only output.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    max_categories = int(body.get("max_categories", 2) or 2)
    max_locations = int(body.get("max_locations_per_category", 2) or 2)
    max_per_signal = int(body.get("max_per_signal", 8) or 8)
    parallel_signals = int(body.get("parallel_signals", 3) or 3)
    dry_run = bool(body.get("dry_run", False))
    try:
        from core.beta_research import get_beta_research_engine
        engine = get_beta_research_engine()
        result = await engine.run_cycle(
            max_categories=max_categories,
            max_locations_per_category=max_locations,
            max_per_signal=max_per_signal,
            parallel_signals=parallel_signals,
            dry_run=dry_run,
        )
        return result
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/beta-research/feedback")
async def beta_research_feedback(request: Request):
    """Attach manual quality feedback to a beta lead (good / bad / needs_review)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    lead_id = int(body.get("lead_id", 0) or 0)
    verdict = (body.get("verdict") or "").strip().lower()
    notes = (body.get("notes") or "").strip()
    if lead_id <= 0 or verdict not in {"good", "bad", "needs_review"}:
        return JSONResponse({"ok": False, "error": "lead_id + verdict required"}, status_code=400)
    try:
        from core.beta_research import get_beta_research_engine
        result = get_beta_research_engine().save_feedback(lead_id=lead_id, verdict=verdict, notes=notes)
        code = 200 if result.get("ok") else 400
        return JSONResponse(result, status_code=code)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ── Runpod Sprint API (Hard-Capped, Phased, No Platform Logins) ─────────────
@app.post("/api/runpod-sprint/plan")
async def runpod_sprint_plan(request: Request):
    """
    Dry-run workload + cost estimate for the one-time sprint.
    Does not start execution.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        from core.runpod_sprint import get_runpod_sprint_engine
        plan = get_runpod_sprint_engine().plan(body if isinstance(body, dict) else {})
        return JSONResponse(plan)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/runpod-sprint/start", dependencies=[Depends(verify_action_auth)])
async def runpod_sprint_start(request: Request):
    """
    Start phased sprint with hard credit cap and safety guardrails.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        from core.runpod_sprint import get_runpod_sprint_engine
        result = await get_runpod_sprint_engine().start(body if isinstance(body, dict) else {})
        code = 200 if result.get("ok") else 400
        return JSONResponse(result, status_code=code)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/runpod-sprint/status")
async def runpod_sprint_status(run_id: int = 0, include_events: int = 1):
    """
    Current sprint status, spend meter, checkpoints, outputs, artifacts.
    """
    try:
        from core.runpod_sprint import get_runpod_sprint_engine
        status = get_runpod_sprint_engine().status(run_id=run_id, include_events=bool(include_events))
        return JSONResponse(status)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/runpod-sprint/stop", dependencies=[Depends(verify_action_auth)])
async def runpod_sprint_stop(request: Request):
    """Hard-stop active sprint safely at next checkpoint."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    run_id = int((body or {}).get("run_id", 0) or 0)
    try:
        from core.runpod_sprint import get_runpod_sprint_engine
        result = await get_runpod_sprint_engine().stop(run_id=run_id)
        code = 200 if result.get("ok", True) else 400
        return JSONResponse(result, status_code=code)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/runpod-sprint/promote", dependencies=[Depends(verify_action_auth)])
async def runpod_sprint_promote(request: Request):
    """
    Promote approved sprint outputs into a campaign-builder handoff manifest.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    run_id = int((body or {}).get("run_id", 0) or 0)
    try:
        from core.runpod_sprint import get_runpod_sprint_engine
        result = get_runpod_sprint_engine().promote(run_id=run_id)
        code = 200 if result.get("ok") else 400
        return JSONResponse(result, status_code=code)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ── Nexus Brain Pipeline API (Dataset + LoRA Pack Builder) ───────────────────
@app.post("/api/nexus-brain/plan")
async def nexus_brain_plan(request: Request):
    """Dry-run estimate for Nexus Brain dataset/training-pack build."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        from core.nexus_brain_pipeline import get_nexus_brain_pipeline
        plan = get_nexus_brain_pipeline().plan(body if isinstance(body, dict) else {})
        return JSONResponse(plan)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/nexus-brain/start", dependencies=[Depends(verify_action_auth)])
async def nexus_brain_start(request: Request):
    """Build Nexus Brain dataset + Runpod training pack."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        from core.nexus_brain_pipeline import get_nexus_brain_pipeline
        result = await get_nexus_brain_pipeline().start(body if isinstance(body, dict) else {})
        code = 200 if result.get("ok") else 400
        return JSONResponse(result, status_code=code)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/nexus-brain/status")
async def nexus_brain_status(run_id: int = 0, include_events: int = 1):
    """Current Nexus Brain pipeline status and latest artifacts."""
    try:
        from core.nexus_brain_pipeline import get_nexus_brain_pipeline
        status = get_nexus_brain_pipeline().status(run_id=run_id, include_events=bool(include_events))
        return JSONResponse(status)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/nexus-brain/runs")
async def nexus_brain_runs(limit: int = 20):
    """Recent Nexus Brain runs."""
    try:
        from core.nexus_brain_pipeline import get_nexus_brain_pipeline
        runs = get_nexus_brain_pipeline().list_runs(limit=limit)
        return JSONResponse(runs)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ── Runpod Brain Lab API (Training-Lab First, Nexus Integration Later) ──────
@app.post("/api/runpod-brain-lab/plan")
async def runpod_brain_lab_plan(request: Request):
    """
    Build-only planning endpoint for Runpod Brain Lab.
    Creates no outbound actions and no direct Nexus runtime integration.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        from core.runpod_brain_lab import get_runpod_brain_lab
        plan = get_runpod_brain_lab().plan(body if isinstance(body, dict) else {})
        return JSONResponse(plan)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/runpod-brain-lab/start", dependencies=[Depends(verify_action_auth)])
async def runpod_brain_lab_start(request: Request):
    """
    Generate Runpod-side datasets/labels/memory/eval/training/export contracts.
    This endpoint does not wire models into live Nexus runtime.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        from core.runpod_brain_lab import get_runpod_brain_lab
        result = await get_runpod_brain_lab().start(body if isinstance(body, dict) else {})
        code = 200 if result.get("ok") else 400
        return JSONResponse(result, status_code=code)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/runpod-brain-lab/status")
async def runpod_brain_lab_status(run_id: int = 0, include_events: int = 1):
    """Current Runpod Brain Lab status and generated artifacts."""
    try:
        from core.runpod_brain_lab import get_runpod_brain_lab
        status = get_runpod_brain_lab().status(run_id=run_id, include_events=bool(include_events))
        return JSONResponse(status)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/runpod-brain-lab/runs")
async def runpod_brain_lab_runs(limit: int = 20):
    """Recent Runpod Brain Lab runs."""
    try:
        from core.runpod_brain_lab import get_runpod_brain_lab
        runs = get_runpod_brain_lab().list_runs(limit=limit)
        return JSONResponse(runs)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ── Brain Feedback + Specialist Deployment Status ────────────────────────────
@app.get("/api/brain/status")
async def brain_status():
    from core.brain_models import (
        coding_online,
        specialist_mode,
        specialist_names,
        specialist_runtime_available,
        specialist_trained_ready,
    )

    specialists = specialist_names()
    status = {}
    details = {}
    models = []
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=2)
        models = [m.get("name", "").split(":")[0] for m in r.json().get("models", [])]
        for s in specialists:
            runtime_online = specialist_runtime_available(s, models)
            trained_ready = specialist_trained_ready(s)
            mode = specialist_mode(s)
            if runtime_online and trained_ready:
                state = "online_trained"
            elif runtime_online:
                state = "online_fallback"
            else:
                state = "pending_transfer"
            status[s] = state
            details[s] = {
                "state": state,
                "mode": mode,
                "runtime_online": runtime_online,
                "trained_ready": trained_ready,
            }
        coding_agent = "online" if coding_online(models) else "pending_transfer"
    except Exception:
        for s in specialists:
            status[s] = "ollama_offline"
            details[s] = {
                "state": "ollama_offline",
                "mode": specialist_mode(s),
                "runtime_online": False,
                "trained_ready": False,
            }
        coding_agent = "ollama_offline"
    return JSONResponse(
        {
            "status": "ok",
            "coding_agent": coding_agent,
            "specialists": status,
            "specialist_details": details,
        }
    )


@app.post("/api/brain/query")
async def brain_query(request: Request):
    body = await request.json()
    from core.brain_models import canonicalize_requested_model

    model = canonicalize_requested_model(str(body.get("model", "nexus-coder")))
    prompt = str(body.get("prompt", ""))
    context = str(body.get("context", ""))
    max_tokens_raw = body.get("max_tokens", 256)
    temperature_raw = body.get("temperature", 0.2)
    think_raw = body.get("think", False)
    try:
        max_tokens = max(8, min(int(max_tokens_raw), 4096))
    except Exception:
        max_tokens = 256
    try:
        temperature = float(temperature_raw)
    except Exception:
        temperature = 0.2
    think_enabled = False
    if isinstance(think_raw, bool):
        think_enabled = think_raw
    elif isinstance(think_raw, str):
        think_enabled = think_raw.strip().lower() in {"1", "true", "yes", "on"}
    full_prompt = f"{context}\n\n{prompt}".strip() if context else prompt

    started = time.time()
    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": model,
                "prompt": full_prompt,
                "stream": False,
                "keep_alive": "30m",
                "think": think_enabled,
                "options": {
                    "num_predict": max_tokens,
                    "temperature": temperature,
                },
            },
            timeout=180,
        )
        payload = response.json() if response.content else {}
        result = str(payload.get("response", ""))
        latency = int((time.time() - started) * 1000)
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "model": model,
            "prompt_length": len(full_prompt),
            "response_length": len(result),
            "latency_ms": latency,
        }
        log_path = Path.home() / ".nexus" / "brain_query_log.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        return JSONResponse({"status": "ok", "response": result, "latency_ms": latency})
    except Exception as e:
        return JSONResponse({"status": "error", "error": str(e), "fallback": "use external model"}, status_code=500)


@app.post("/api/brain/feedback")
async def brain_feedback(request: Request):
    """
    Record booked/lost outcome feedback for specialist learning loops.
    Expected payload: {lead_id, outcome(booked|lost), specialist_used, model_version}
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        from core.feedback_api import record_feedback
        result = record_feedback(body if isinstance(body, dict) else {})
        if result.get("ok"):
            outcome = str((result.get("row") or {}).get("outcome") or body.get("outcome") or "").strip().lower()
            return JSONResponse({"status": "ok", "logged": outcome or "unknown"})
        return JSONResponse({"status": "error", "error": result.get("error", "feedback rejected")}, status_code=400)
    except Exception as e:
        return JSONResponse({"status": "error", "error": str(e)}, status_code=500)


@app.get("/api/brain/specialists/status")
async def brain_specialists_status():
    """Show specialist readiness/deployment status for Nexus routing."""
    try:
        from core.specialist_router import get_specialist_router
        status = get_specialist_router().status()
        return JSONResponse(status)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/brain/insights")
async def brain_insights():
    """
    Return recent brain query log entries as insights.
    Called by static/js/app.js API.getAgentBrain() (brain tab).
    """
    import json as _json
    _log_path = Path.home() / ".nexus" / "brain_query_log.jsonl"
    entries = []
    if _log_path.exists():
        try:
            lines = _log_path.read_text().splitlines()
            for line in lines[-20:][::-1]:
                try:
                    entries.append(_json.loads(line))
                except Exception:
                    pass
        except Exception:
            pass
    models_active = len({e.get("model", "") for e in entries if e.get("model")})
    return JSONResponse({
        "status": "ok",
        "recent_insights": entries,
        "summary": {
            "total_queries": len(entries),
            "models_active": models_active,
            "knowledge_items": 13,
            "discussions": min(len(entries), 10),
            "problems": 0,
        },
    })


@app.get("/api/brain/knowledge")
async def brain_knowledge():
    """
    Return specialist knowledge items (name, status, description).
    Called by static/js/app.js API.getBrainKnowledge().
    """
    import json as _json
    import urllib.request as _ur
    from core.brain_models import (
        CODING_MODEL,
        SPECIALIST_OLLAMA_MODELS,
        installed_specialist_model,
        specialist_names,
    )

    _descriptions = {
        "lead_ranker": "Scores leads 0-100 for booking probability",
        "conversion_specialist": "Writes personalized quote responses",
        "objection_resolver": "Handles price/hesitation objections",
        "fb_media_buyer": "Optimizes Facebook ad campaigns",
        "venue_partnership_closer": "Closes venue referral partnerships",
        "follow_up_cadence_specialist": "Manages follow-up timing and tone",
        "quote_personalizer": "Generates personalized quote copy",
        "ad_creative_specialist": "Creates Facebook/Instagram ad copy",
        "vendor_referral_specialist": "Manages vendor referral outreach",
        "seasonal_demand_predictor": "Predicts peak demand 4-8 weeks ahead",
        "autonomous_entrepreneur": "Runs planner/operator/critic autonomous business cycles",
        "venue_partner_ranker": "Ranks venues for partnership potential",
        "outreach_angle_selector": "Selects best outreach angle per lead",
        "nexus_coder": "Expert coder with full Nexus codebase knowledge",
    }
    ollama_models = set()
    try:
        req = _ur.Request("http://localhost:11434/api/tags", method="GET")
        with _ur.urlopen(req, timeout=2) as resp:
            data = _json.loads(resp.read())
            for m in data.get("models", []):
                ollama_models.add(m.get("name", "").split(":")[0])
    except Exception:
        pass
    items = []
    for name in specialist_names():
        ollama_name = SPECIALIST_OLLAMA_MODELS.get(name, "")
        installed_name = installed_specialist_model(name, ollama_models)
        # Prefer API state from /api/brain/status in frontend. This remains backward-compatible.
        items.append({
            "name": name,
            "ollama_name": ollama_name,
            "status": "online" if installed_name else "pending_transfer",
            "description": _descriptions.get(name, name),
            "installed_model": installed_name or "",
        })
    items.append({
        "name": "nexus_coder",
        "ollama_name": CODING_MODEL,
        "status": "online" if CODING_MODEL in ollama_models else "pending_transfer",
        "description": _descriptions["nexus_coder"],
        "installed_model": CODING_MODEL if CODING_MODEL in ollama_models else "",
    })
    return JSONResponse({"status": "ok", "knowledge_items": items})


@app.get("/api/brain/discussions")
async def brain_discussions():
    """
    Return recent brain queries as Q&A discussion pairs.
    Called by static/js/app.js API.getBrainDiscussions().
    """
    import json as _json
    _log_path = Path.home() / ".nexus" / "brain_query_log.jsonl"
    discussions = []
    if _log_path.exists():
        try:
            lines = _log_path.read_text().splitlines()
            for line in lines[-10:][::-1]:
                try:
                    entry = _json.loads(line)
                    discussions.append({
                        "prompt": entry.get("prompt", "")[:120],
                        "response_preview": entry.get("response_preview",
                                                       entry.get("response", ""))[:120],
                        "model": entry.get("model", "unknown"),
                        "latency_ms": entry.get("latency_ms", 0),
                        "timestamp": entry.get("timestamp", ""),
                    })
                except Exception:
                    pass
        except Exception:
            pass
    return JSONResponse({"status": "ok", "discussions": discussions})


# ── Authentication ─────────────────────────────────────────────────────────
@app.post("/api/auth/login")
async def auth_login(request: Request):
    """Authenticate with email + password via WorkOS."""
    body = await request.json()
    email = body.get("email", "").strip().lower()
    password = body.get("password", "")
    if not email or not password:
        return JSONResponse({"error": "Email and password required"}, status_code=400)
    from core.auth import authenticate_email_password
    session = authenticate_email_password(email, password)
    if not session:
        return JSONResponse({"error": "Invalid credentials"}, status_code=401)
    return JSONResponse({"ok": True, "session": session})

@app.get("/api/auth/session")
async def auth_session(request: Request):
    """Validate an existing session token."""
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        return JSONResponse({"error": "No token"}, status_code=401)
    from core.auth import validate_session
    session = validate_session(token)
    if not session:
        return JSONResponse({"error": "Invalid or expired session"}, status_code=401)
    return JSONResponse({"ok": True, "session": session})

@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    """Invalidate a session token."""
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    from core.auth import logout
    logout(token)
    return JSONResponse({"ok": True})

@app.get("/api/ping")
async def api_ping():
    """Lightweight health check for frontend connectivity detection."""
    return JSONResponse({"ok": True, "ts": __import__("time").time()})

@app.get("/api/health")
async def health_check():
    """Comprehensive health check for monitoring."""
    import sqlite3 as _sql
    checks = {"server": "ok", "database": "unknown", "messaging": "unknown",
              "pipeline": "unknown", "followup_engine": "unknown", "google_voice": "unknown",
              "startup": "ok (bootstrapped)" if _STARTUP_COMPLETE else "not run"}
    # Database check
    try:
        conn = _sql.connect(str(Path.home() / ".nexus" / "memory.db"))
        conn.execute("SELECT 1").fetchone()
        lead_count = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        conn.close()
        checks["database"] = f"ok ({lead_count} leads)"
    except Exception as e:
        checks["database"] = f"error: {e}"
    # Messaging check
    try:
        from integrations.messaging import get_messenger
        m = get_messenger()
        checks["messaging"] = "ok" if m else "not initialized"
    except Exception as e:
        checks["messaging"] = f"error: {e}"
    # Pipeline check
    try:
        from core.lead_pipeline import get_pipeline
        p = get_pipeline()
        checks["pipeline"] = "ok (running)" if p and p._running else "stopped"
    except Exception as e:
        checks["pipeline"] = f"error: {e}"
    # Follow-up engine check
    try:
        from core.follow_up_engine import get_follow_up_engine
        eng = get_follow_up_engine()
        checks["followup_engine"] = "ok (running)" if eng else "stopped"
    except Exception as e:
        checks["followup_engine"] = f"error: {e}"
    # Google Voice check
    try:
        checks["google_voice"] = "check /api/browser/status for details"
    except:
        pass
    # Telegram bot check
    try:
        from telegram.bot import get_bot
        bot = get_bot()
        checks["telegram_bot"] = "ok" if bot else "not initialized"
    except Exception as e:
        checks["telegram_bot"] = f"error: {e}"
    # Approval queue check
    try:
        from core.approval_queue import get_all_pending, get_approval_stats
        pending = get_all_pending()
        stats = get_approval_stats()
        checks["approval_queue"] = f"ok ({len(pending)} pending, {stats.get('approved_today', 0)} approved today)"
    except Exception as e:
        checks["approval_queue"] = f"error: {e}"
    # Daily posting check
    try:
        from core.daily_posting import get_angle_count
        angles = get_angle_count()
        checks["daily_posting"] = f"ok ({angles['total']} angles: {angles['en']} EN + {angles['es']} ES)"
    except Exception as e:
        checks["daily_posting"] = f"error: {e}"
    # Comment monitor check
    try:
        from integrations.fb_comment_monitor import get_comment_stats
        cstats = get_comment_stats()
        checks["comment_monitor"] = f"ok ({cstats.get('today', 0)} today, {cstats.get('total', 0)} total)"
    except Exception as e:
        checks["comment_monitor"] = f"error: {e}"

    # Watchdog service status
    try:
        from core.watchdog import get_health_check
        wd = get_health_check()
        checks["watchdog"] = f"{wd['status']} ({wd['healthy_count']}/{wd['total_services']} services healthy)"
    except Exception as e:
        checks["watchdog"] = f"error: {e}"
    # Instagram check
    try:
        from integrations.instagram_api import get_posting_stats
        ig_stats = get_posting_stats(7)
        checks["instagram"] = f"ok ({ig_stats.get('total_posts', 0)} posts in 7d)"
    except Exception as e:
        checks["instagram"] = f"error: {e}"
    # Knowledge engine check
    try:
        from core.knowledge_engine import get_knowledge
        kb = get_knowledge()
        checks["knowledge_engine"] = f"ok ({len(kb)} entries)"
    except Exception as e:
        checks["knowledge_engine"] = f"error: {e}"
    # Ad monitor check
    try:
        from core.ad_monitor import get_7day_avg
        avg = get_7day_avg()
        checks["ad_monitor"] = f"ok (7d avg CPL: ${avg.get('avg_cpl', 0):.2f})"
    except Exception:
        checks["ad_monitor"] = "not yet collecting data"

    # Booking system check
    try:
        from core.booking_system import get_booking_pipeline
        bp = get_booking_pipeline()
        active = sum(1 for b in bp if b.get("status") not in ("completed", "cancelled"))
        checks["booking_system"] = f"ok ({active} active bookings)"
    except Exception as e:
        checks["booking_system"] = f"error: {e}"
    # Email parser check
    try:
        from integrations.email_lead_parser import get_email_parser_stats
        ep = get_email_parser_stats()
        checks["email_parser"] = f"ok ({ep.get('today_count', 0)} today, {ep.get('total_processed', 0)} total)"
    except Exception as e:
        checks["email_parser"] = f"error: {e}"
    # Review system check
    try:
        from core.review_system import get_review_stats
        rs = get_review_stats()
        checks["review_system"] = f"ok ({rs.get('total_requests', 0)} requests, {rs.get('response_rate', 0):.0f}% response)"
    except Exception as e:
        checks["review_system"] = f"error: {e}"
    # Google Calendar check
    try:
        from integrations.google_calendar import get_upcoming_bookings
        upcoming = get_upcoming_bookings(30)
        checks["google_calendar"] = f"ok ({len(upcoming)} bookings in 30d)"
    except Exception as e:
        checks["google_calendar"] = f"error: {e}"

    try:
        from core.analytics_dashboard import get_dashboard_data
        dash = get_dashboard_data(7)
        checks["analytics"] = f"ok (dashboard active)"
    except Exception as e:
        checks["analytics"] = f"error: {e}"

    try:
        from integrations.meta_capi_events import get_event_stats
        capi = get_event_stats(30)
        checks["capi_events"] = f"ok ({capi.get('total', 0)} events in 30d)"
    except Exception as e:
        checks["capi_events"] = f"error: {e}"

    try:
        from integrations.google_business import get_posting_stats
        gbp = get_posting_stats(30)
        checks["gbp_poster"] = f"ok ({gbp.get('total_published', gbp.get('published', 0))} published)"
    except Exception as e:
        checks["gbp_poster"] = f"error: {e}"

    try:
        from core.competitor_monitor import get_competitor_list
        comps = get_competitor_list()
        checks["competitor_monitor"] = f"ok ({len(comps)} tracked)"
    except Exception as e:
        checks["competitor_monitor"] = f"error: {e}"

    try:
        from integrations.fb_messenger import is_initialized
        checks["fb_messenger"] = "ok" if is_initialized() else "not configured"
    except Exception as e:
        checks["fb_messenger"] = f"error: {e}"

    try:
        from core.seo_tools import get_seo_stats
        seo = get_seo_stats()
        checks["seo_tools"] = f"ok ({seo.get('total_pages', 0)} pages)"
    except Exception as e:
        checks["seo_tools"] = f"error: {e}"

    try:
        from core.retargeting import get_audience_stats
        audiences = get_audience_stats()
        checks["retargeting"] = f"ok ({len(audiences)} audiences)"
    except Exception as e:
        checks["retargeting"] = f"error: {e}"

    try:
        from core.reengagement import get_campaign_stats
        camps = get_campaign_stats()
        checks["reengagement"] = f"ok ({len(camps)} campaigns)"
    except Exception as e:
        checks["reengagement"] = f"error: {e}"

    try:
        from core.media_manager import get_media_stats
        mstats = get_media_stats()
        checks["media_manager"] = f"ok ({mstats.get('total_assets', 0)} assets)"
    except Exception as e:
        checks["media_manager"] = f"error: {e}"

    # ── Wave 6 Health Checks ──
    try:
        from core.approval_system import get_approval_stats
        astats = get_approval_stats()
        checks["approval_system"] = f"ok ({astats['pending']} pending, avg {astats['avg_approval_minutes']}min)"
    except Exception as e:
        checks["approval_system"] = f"error: {e}"

    try:
        from integrations.posting_engine import get_posting_status
        pstats = get_posting_status()
        checks["posting_engine"] = f"ok ({pstats.get('total_today', 0)} posts today)"
    except Exception as e:
        checks["posting_engine"] = f"error: {e}"

    try:
        from integrations.video_generator import get_video_stats
        vstats = get_video_stats()
        checks["video_generator"] = f"ok ({vstats.get('total', 0)} videos)"
    except Exception as e:
        checks["video_generator"] = f"error: {e}"

    try:
        from integrations.fb_group_scraper import get_scrape_stats
        gstats = get_scrape_stats()
        checks["fb_group_scraper"] = f"ok ({gstats.get('total_leads', 0)} leads found)"
    except Exception as e:
        checks["fb_group_scraper"] = f"disabled ({e})"

    try:
        from integrations.browser_agent import get_session_stats
        bstats = get_session_stats()
        checks["browser_agent"] = f"ok ({bstats.get('total_sessions', 0)} sessions)"
    except Exception as e:
        checks["browser_agent"] = f"error: {e}"

    try:
        from core.task_queue import get_queue_status
        tstats = get_queue_status()
        checks["task_queue"] = f"ok ({tstats.get('queued', 0)} queued, {tstats.get('completed_today', 0)} done today)"
    except Exception as e:
        checks["task_queue"] = f"error: {e}"

    # Consider healthy if no errors — stopped/not-initialized services are acceptable
    has_error = any("error" in str(v).lower() for v in checks.values())
    core_services = ["server", "database", "approval_queue"]
    core_ok = all("ok" in str(checks.get(s, "")).lower() for s in core_services if s in checks)
    healthy = core_ok and not has_error
    return JSONResponse({"healthy": healthy, "checks": checks, "timestamp": datetime.utcnow().isoformat()})

# ── Security Gate API ─────────────────────────────────────────────────────────
@app.get("/api/security/status")
async def security_status():
    """Get security gate status for dashboard."""
    try:
        from core.security import get_security_gate
        gate = get_security_gate()
        return JSONResponse(gate.get_status())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/security/outbound-log")
async def security_outbound_log(limit: int = 20):
    """Get recent outbound log entries."""
    try:
        from core.security import get_security_gate
        gate = get_security_gate()
        return JSONResponse(gate.get_outbound_log(min(limit, 100)))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/security/audit")
async def security_audit():
    """Verify hash chain integrity."""
    try:
        from core.security import get_security_gate
        gate = get_security_gate()
        valid, detail = gate.verify_audit_chain()
        return JSONResponse({"valid": valid, "detail": detail})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/quick-send-sms", dependencies=[Depends(verify_action_auth)])
async def quick_send_sms(request: Request):
    """Send SMS directly from Nexus chat."""
    body = await request.json()
    cfg = load_config()
    gmail = cfg.get("gmail_address") or body.get("gmail","")
    pw = cfg.get("gmail_app_password") or body.get("app_password","")
    if not gmail or not pw:
        return JSONResponse({"error": "Gmail not configured. Go to GHL → Settings to add Gmail + App Password."}, status_code=400)
    from integrations.zoar_bot import send_sms
    result = await send_sms(gmail, pw, body.get("phone",""), body.get("message",""),
                            body.get("carrier","tmobile"), approval_id=body.get("approval_id"))
    if result.get("ok"):
        await broadcast({"type":"ghl_event","message":f"📱 SMS sent to {body.get('phone','')} — {body.get('message','')[:60]}..."})
    return JSONResponse(result)

@app.post("/api/quick-send-email", dependencies=[Depends(verify_action_auth)])
async def quick_send_email(request: Request):
    """Send email directly from Nexus chat."""
    body = await request.json()
    cfg = load_config()
    gmail = cfg.get("gmail_address") or body.get("gmail","")
    pw = cfg.get("gmail_app_password") or body.get("app_password","")
    if not gmail or not pw:
        return JSONResponse({"error": "Gmail not configured."}, status_code=400)
    from integrations.zoar_bot import send_email
    result = await send_email(gmail, pw, body.get("to",""), body.get("subject","Message from Nexus"),
                              body.get("message",""), approval_id=body.get("approval_id"))
    return JSONResponse(result)

@app.post("/api/quick-email/send")
async def quick_email_branded_send(request: Request):
    """Send a branded HTML email to any address. Used by Quick Email tab."""
    body = await request.json()
    to_email = body.get("to", "").strip()
    subject = body.get("subject", "").strip()
    plain_body = body.get("body", "").strip()
    name = body.get("name", "").strip()

    if not to_email or "@" not in to_email:
        return JSONResponse({"ok": False, "error": "Invalid email address"}, status_code=400)
    if not subject:
        return JSONResponse({"ok": False, "error": "Subject required"}, status_code=400)
    if not plain_body:
        return JSONResponse({"ok": False, "error": "Body required"}, status_code=400)

    # Wrap in branded HTML template
    from core.email_templates import initial_outreach
    first_name = name.split()[0] if name else ""
    html_body, _ = initial_outreach(first_name or "there", plain_body, subject)

    # Auto-confirm dead man's switch — Kai is actively using the UI
    try:
        from core.security import get_security_gate
        get_security_gate().confirm_alive("kai_quick_email_ui")
    except Exception:
        pass

    # Create approval record — Kai clicking Send in Quick Email IS the approval
    import sqlite3 as _sql
    _db = _sql.connect(str(Path.home() / ".nexus" / "memory.db"))
    _db.execute(
        "INSERT INTO message_approvals "
        "(lead_id, channel, lead_email, proposed_subject, proposed_message, status, approval_method, created_at, approved_at) "
        "VALUES (0, 'email', ?, ?, ?, 'approved', 'kai_quick_email', datetime('now'), datetime('now'))",
        (to_email, subject[:200], plain_body[:500]),
    )
    approval_id = _db.execute("SELECT last_insert_rowid()").fetchone()[0]
    _db.commit()
    _db.close()

    # Send via zoar_bot (runs outbound_gate + blocklist check = Rule 0)
    cfg = load_config()
    gmail = cfg.get("gmail_address", "zoarbathrooms@gmail.com")
    app_pw = cfg.get("gmail_app_password", "")
    if not app_pw:
        return JSONResponse({"ok": False, "error": "Gmail app password not configured"}, status_code=500)

    from integrations.zoar_bot import send_email as smtp_send
    result = await smtp_send(gmail, app_pw, to_email, subject, plain_body, approval_id=approval_id, html_body=html_body)

    # Translate cryptic gate codes to human-readable errors
    if not result.get("ok") and result.get("error"):
        err = result["error"]
        friendly = {
            "dead_man_expired": "Dead man's switch expired. Run /confirm_alive in Telegram.",
            "no_approval_id": "Approval system error. Try again or contact support.",
            "recipient_cooldown": "Already emailed this person in the last 60 min. Wait and retry.",
            "recipient_daily_limit": "Already sent 3 emails to this person today.",
            "global_hourly_limit": "System sent 50+ emails this hour. Wait a few minutes.",
        }
        for key, msg in friendly.items():
            if key in err:
                result["error"] = msg
                break

    return JSONResponse(result)


@app.get("/api/generate-image")
async def generate_image_endpoint(prompt: str = "a dog"):
    """Generate image via pollinations.ai (free). Pre-fetches to warm the CDN."""
    import urllib.parse, httpx
    encoded = urllib.parse.quote(prompt[:500])
    url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&nologo=true&enhance=true"
    # Pre-fetch to trigger generation before returning URL
    warmed = False
    try:
        async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
            resp = await client.get(url)
            warmed = resp.status_code == 200 and resp.headers.get("content-type","").startswith("image")
    except Exception:
        pass
    return JSONResponse({"ok": True, "url": url, "method": "pollinations.ai", "warmed": warmed})

@app.post("/api/self-improve")
async def self_improve():
    orch = get_orch(); events = []
    async for ev in orch.self_improve(): events.append(ev); await broadcast(ev)
    return JSONResponse({"ok": True, "events": events})

@app.get("/api/memory/history")
async def get_history():
    from memory.memory import get_memory
    return await get_memory().get_recent_conversations(50)

@app.get("/api/memory/export")
async def export_mem():
    from memory.memory import get_memory
    return JSONResponse(await get_memory().export_for_sync())

@app.post("/api/memory/import")
async def import_mem(request: Request):
    cfg = load_config(); secret = request.headers.get("x-sync-secret", "")
    if cfg.get("sync_secret") and secret != cfg["sync_secret"]:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    from memory.memory import get_memory
    await get_memory().import_from_sync(await request.json())
    return JSONResponse({"ok": True})

@app.post("/api/clear-config", dependencies=[Depends(verify_action_auth)])
async def clear_cfg():
    global _orch; _orch = None
    save_config({k: "" for k in ["anthropic_key","moonshot_key","gemini_key","openai_key"]})
    return {"ok": True}

# ── Higgsfield Page ───────────────────────────────────────────────────────────
@app.get("/higgsfield", response_class=HTMLResponse)
async def higgsfield_page(): return HTMLResponse(HIGGSFIELD_PAGE)

# ── Bot Testing API ────────────────────────────────────────────────────────────
@app.get("/bot", response_class=HTMLResponse)
async def bot_page(): return HTMLResponse(BOT_PAGE)

@app.get("/api/bot/sessions")
async def bot_sessions():
    from integrations.zoar_bot import get_all_sessions
    return JSONResponse(get_all_sessions())

@app.post("/api/bot/session")
async def bot_create_session(request: Request):
    import uuid
    from integrations.zoar_bot import upsert_session
    body = await request.json()
    sid = body.get("session_id") or f"s_{uuid.uuid4().hex[:8]}"
    upsert_session(sid, body.get("name",""), body.get("phone",""),
                   body.get("email",""), body.get("carrier","tmobile"),
                   body.get("channel","sms"), body.get("notes",""))
    return JSONResponse({"session_id": sid})

@app.get("/api/bot/session/{sid}")
async def bot_get_session(sid: str):
    from integrations.zoar_bot import get_session, get_messages
    s = get_session(sid)
    if not s: return JSONResponse({"error":"Not found"},status_code=404)
    return JSONResponse({"session": s, "messages": get_messages(sid)})

@app.post("/api/bot/generate")
async def bot_generate(request: Request):
    body = await request.json()
    sid = body.get("session_id","")
    mode = body.get("mode","reply")  # reply | initial | followup
    channel = body.get("channel","sms")
    incoming = body.get("incoming","")
    contact_name = body.get("contact_name","")

    from integrations.zoar_bot import get_messages, gen_reply, gen_initial, gen_followup, save_msg
    history = []
    if sid:
        msgs = get_messages(sid)
        history = [{"role":m["role"],"content":m["content"]} for m in msgs]

    orch = get_orch()
    if mode == "initial":
        body_text, subject, model = await gen_initial(orch.provider, channel, contact_name)
        return JSONResponse({"body": body_text, "subject": subject, "model": model})
    elif mode == "followup":
        num = body.get("followup_num", 0)
        text, model = await gen_followup(orch.provider, contact_name, num)
        return JSONResponse({"body": text, "subject": "", "model": model})
    else:
        if not incoming: return JSONResponse({"error":"Need incoming message"}, status_code=400)
        # Save incoming as customer message first
        if sid: save_msg(sid, "user", incoming)
        text, model = await gen_reply(orch.provider, channel, incoming, contact_name, history)
        return JSONResponse({"body": text, "subject": "", "model": model})

@app.post("/api/bot/send", dependencies=[Depends(verify_action_auth)])
async def bot_send(request: Request):
    body = await request.json()
    sid = body.get("session_id","")
    channel = body.get("channel","sms")
    phone = body.get("phone","")
    email = body.get("email","")
    message = body.get("message","")
    subject = body.get("subject","Your Event Restroom Rental — Zoar Bathroom Rentals")
    carrier = body.get("carrier","tmobile")
    contact_name = body.get("contact_name","")

    cfg = load_config()
    gmail = cfg.get("gmail_address","")
    app_pw = cfg.get("gmail_app_password","")
    if not gmail or not app_pw:
        return JSONResponse({"ok":False,"error":"Gmail not configured. Go to Settings tab."})

    from integrations.zoar_bot import send_sms, send_email, save_msg
    if channel == "sms":
        result = await send_sms(gmail, app_pw, phone, message, carrier,
                                approval_id=body.get("approval_id"))
    else:
        result = await send_email(gmail, app_pw, email, subject, message,
                                  approval_id=body.get("approval_id"))

    status = "sent" if result["ok"] else "failed"
    if sid:
        save_msg(sid, "assistant", message, subject,
                 sent=result["ok"], status=status, error=result.get("error",""))
    await broadcast({"type":"bot_sent","channel":channel,"to":phone or email,
                     "ok":result["ok"],"preview":message[:60]})
    return JSONResponse(result)

@app.post("/api/bot/save-message")
async def bot_save_message(request: Request):
    """Save a message to a session without sending (for logging manual sends)."""
    body = await request.json()
    from integrations.zoar_bot import save_msg
    save_msg(body.get("session_id",""), body.get("role","assistant"),
             body.get("content",""), body.get("subject",""),
             body.get("sent",False), body.get("status","draft"))
    return JSONResponse({"ok":True})

@app.post("/api/bot/save-settings")
async def bot_save_settings(request: Request):
    body = await request.json()
    fields = ["gmail_address","gmail_app_password","ghl_api_key","ghl_location_id"]
    save_config({f: body[f] for f in fields if f in body})
    _start_messenger_simple(load_config())
    return JSONResponse({"ok":True})

def _start_messenger_simple(cfg):
    # Just validates config is present, actual sending uses cfg directly
    gmail = cfg.get("gmail_address","")
    pw = cfg.get("gmail_app_password","")
    if gmail and pw:
        print(f"[Nexus] Gmail ready: {gmail}")


BOT_PAGE = r"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Zoar Bot Tester</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#060a0f;--s1:#0c1520;--s2:#08111a;--border:#1a2a3a;--text:#b8c8d8;--hi:#e8f0f8;--muted:#3a5a72;--cyan:#06b6d4;--purple:#8b5cf6;--green:#22d47a;--orange:#fb923c;--red:#ef4444;--yellow:#fbbf24;--pink:#f472b6}
body{font-family:'DM Sans',sans-serif;background:var(--bg);color:var(--text);height:100vh;display:flex;flex-direction:column;overflow:hidden}

/* Header */
.hdr{height:50px;border-bottom:1px solid var(--border);background:var(--s1);display:flex;align-items:center;justify-content:space-between;padding:0 16px;flex-shrink:0}
.hdr-left{display:flex;align-items:center;gap:10px}
.logo-pill{background:linear-gradient(135deg,var(--pink),var(--orange));border-radius:8px;padding:4px 10px;font-size:.72rem;font-weight:700;color:#fff;letter-spacing:.05em}
.hdr-title{font-size:.88rem;font-weight:600;color:var(--hi)}
.hdr-sub{font-size:.68rem;color:var(--muted)}
.hdr-btns{display:flex;gap:6px;align-items:center}
.hbtn{font-size:.65rem;padding:4px 10px;border-radius:6px;border:1px solid var(--border);background:transparent;color:var(--muted);cursor:pointer;font-family:'DM Sans',sans-serif;transition:all .15s;white-space:nowrap}
.hbtn:hover{color:var(--hi);border-color:#2a4060}
.hbtn.active{background:rgba(244,114,182,.12);color:var(--pink);border-color:rgba(244,114,182,.35)}
.status-dot{width:6px;height:6px;border-radius:50%;background:var(--red);display:inline-block;margin-right:4px}
.status-dot.ok{background:var(--green)}
#gmail-status{font-size:.65rem;font-family:'JetBrains Mono',monospace;padding:4px 9px;border-radius:6px;border:1px solid var(--border)}

/* Layout */
.layout{flex:1;display:flex;overflow:hidden}

/* Sessions sidebar */
.sidebar{width:220px;border-right:1px solid var(--border);background:var(--s2);display:flex;flex-direction:column;flex-shrink:0}
.sb-head{padding:10px 12px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;flex-shrink:0}
.sb-head span{font-size:.6rem;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
.new-btn{font-size:.65rem;padding:3px 9px;border-radius:5px;background:rgba(244,114,182,.12);color:var(--pink);border:1px solid rgba(244,114,182,.3);cursor:pointer;font-family:'DM Sans',sans-serif}
.sb-list{flex:1;overflow-y:auto;padding:6px;display:flex;flex-direction:column;gap:4px;scrollbar-width:thin;scrollbar-color:var(--border) transparent}
.sess-item{padding:9px 10px;border-radius:8px;border:1px solid var(--border);cursor:pointer;transition:all .15s;background:var(--bg)}
.sess-item:hover{border-color:rgba(244,114,182,.3)}
.sess-item.active{border-color:var(--pink);background:rgba(244,114,182,.06)}
.sess-name{font-size:.76rem;font-weight:600;color:var(--hi);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sess-meta{font-size:.62rem;color:var(--muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ch-badge{font-size:.56rem;padding:1px 5px;border-radius:3px;font-weight:600;margin-left:4px}
.ch-sms{background:rgba(6,182,212,.12);color:var(--cyan);border:1px solid rgba(6,182,212,.25)}
.ch-email{background:rgba(139,92,246,.12);color:#a78bfa;border:1px solid rgba(139,92,246,.25)}

/* Main area */
.main{flex:1;display:flex;overflow:hidden}

/* Chat column */
.chat-col{flex:1;display:flex;flex-direction:column;overflow:hidden;min-width:0;border-right:1px solid var(--border)}
.chat-info{padding:10px 14px;border-bottom:1px solid var(--border);background:var(--s2);display:flex;align-items:center;gap:10px;flex-shrink:0;flex-wrap:wrap}
.ci-name{font-size:.82rem;font-weight:600;color:var(--hi)}
.ci-detail{font-size:.68rem;color:var(--muted);font-family:'JetBrains Mono',monospace}
.test-badge{font-size:.6rem;padding:2px 8px;border-radius:4px;background:rgba(251,191,36,.1);color:var(--yellow);border:1px solid rgba(251,191,36,.25);font-weight:600}
.messages{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:8px;scrollbar-width:thin;scrollbar-color:var(--border) transparent}
/* Message bubbles */
.msg-wrap{display:flex;flex-direction:column}
.msg-wrap.outbound{align-items:flex-end}
.msg-wrap.inbound{align-items:flex-start}
.msg-label{font-size:.6rem;color:var(--muted);margin-bottom:3px;display:flex;align-items:center;gap:5px}
.msg-bubble{max-width:78%;padding:10px 13px;border-radius:13px;font-size:.84rem;line-height:1.6;word-break:break-word;white-space:pre-wrap}
.outbound .msg-bubble{background:linear-gradient(135deg,rgba(244,114,182,.18),rgba(251,146,60,.14));border:1px solid rgba(244,114,182,.25);border-bottom-right-radius:3px;color:var(--hi)}
.inbound .msg-bubble{background:var(--s1);border:1px solid var(--border);border-bottom-left-radius:3px}
.msg-ts{font-size:.58rem;color:#1a3a50;margin-top:3px;font-family:'JetBrains Mono',monospace}
.sent-tag{font-size:.58rem;padding:1px 5px;border-radius:3px;background:rgba(34,212,122,.1);color:var(--green);border:1px solid rgba(34,212,122,.2)}
.draft-tag{font-size:.58rem;padding:1px 5px;border-radius:3px;background:rgba(251,191,36,.1);color:var(--yellow);border:1px solid rgba(251,191,36,.2)}
.failed-tag{font-size:.58rem;padding:1px 5px;border-radius:3px;background:rgba(239,68,68,.1);color:var(--red);border:1px solid rgba(239,68,68,.2)}

/* Compose area */
.compose{border-top:1px solid var(--border);background:var(--s1);padding:12px 14px;flex-shrink:0;display:flex;flex-direction:column;gap:8px}
.compose-top{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.csel{background:var(--bg);border:1px solid var(--border);border-radius:7px;padding:5px 10px;color:var(--text);font-size:.75rem;font-family:'DM Sans',sans-serif;outline:none;cursor:pointer}
.csel:focus{border-color:var(--cyan)}
.gen-row{display:flex;gap:5px}
.gbtn{flex:1;padding:7px 4px;border-radius:7px;border:1px solid rgba(139,92,246,.35);background:rgba(139,92,246,.08);color:#a78bfa;font-size:.68rem;font-weight:600;cursor:pointer;font-family:'DM Sans',sans-serif;transition:all .15s;white-space:nowrap}
.gbtn:hover{background:rgba(139,92,246,.16)}.gbtn:disabled{opacity:.4;cursor:not-allowed}
.msg-area{background:var(--bg);border:1px solid var(--border);border-radius:9px;padding:10px 13px;color:var(--hi);font-size:.84rem;resize:none;outline:none;font-family:'DM Sans',sans-serif;width:100%;min-height:70px;max-height:150px;transition:border-color .2s;line-height:1.5}
.msg-area:focus{border-color:rgba(244,114,182,.4)}
.subj-row{display:flex;gap:6px;align-items:center}
.subj-label{font-size:.65rem;color:var(--muted);white-space:nowrap;font-weight:600}
.subj-input{flex:1;background:var(--bg);border:1px solid var(--border);border-radius:7px;padding:6px 10px;color:var(--hi);font-size:.76rem;outline:none;font-family:'DM Sans',sans-serif}
.subj-input:focus{border-color:var(--cyan)}
.incoming-row{display:flex;gap:6px;align-items:center}
.inc-input{flex:1;background:var(--bg);border:1px solid var(--border);border-radius:7px;padding:6px 10px;color:var(--hi);font-size:.76rem;outline:none;font-family:'DM Sans',sans-serif}
.inc-input:focus{border-color:var(--green)}
.inc-label{font-size:.65rem;color:var(--green);white-space:nowrap;font-weight:600}
.send-row{display:flex;gap:6px;justify-content:space-between;align-items:center}
.model-hint{font-size:.6rem;color:var(--muted);font-family:'JetBrains Mono',monospace}
.send-btn{padding:9px 22px;background:linear-gradient(135deg,var(--pink),var(--orange));color:#fff;border:none;border-radius:9px;font-size:.82rem;font-weight:600;cursor:pointer;font-family:'DM Sans',sans-serif;transition:opacity .2s}
.send-btn:disabled{opacity:.35;cursor:not-allowed}
.log-btn{padding:9px 14px;background:transparent;color:var(--muted);border:1px solid var(--border);border-radius:9px;font-size:.76rem;cursor:pointer;font-family:'DM Sans',sans-serif}
.log-btn:hover{color:var(--hi);border-color:#2a4060}
.char-count{font-size:.6rem;color:var(--muted);font-family:'JetBrains Mono',monospace}

/* Right panel — contact info + settings */
.right-panel{width:260px;display:flex;flex-direction:column;overflow:hidden;flex-shrink:0}
.rp-tabs{display:flex;border-bottom:1px solid var(--border);flex-shrink:0}
.rp-tab{flex:1;padding:8px;text-align:center;font-size:.62rem;font-weight:600;color:var(--muted);cursor:pointer;border:none;background:none;font-family:'DM Sans',sans-serif;letter-spacing:.04em;text-transform:uppercase;transition:color .15s}
.rp-tab:hover{color:var(--hi)}.rp-tab.active{color:var(--pink);border-bottom:2px solid var(--pink)}
.rp-pane{flex:1;overflow-y:auto;padding:12px;scrollbar-width:thin;scrollbar-color:var(--border) transparent}
/* Contact form */
.cf{margin-bottom:9px}
.cf label{display:block;font-size:.62rem;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px}
.cf input,.cf select{width:100%;background:var(--bg);border:1px solid var(--border);border-radius:7px;padding:7px 10px;color:var(--hi);font-size:.78rem;font-family:'DM Sans',sans-serif;outline:none;transition:border-color .2s}
.cf input:focus,.cf select:focus{border-color:var(--pink)}
.cf input::placeholder{color:#1a3a50}
.save-contact-btn{width:100%;margin-top:4px;padding:9px;background:rgba(244,114,182,.15);color:var(--pink);border:1px solid rgba(244,114,182,.35);border-radius:8px;font-size:.76rem;font-weight:600;cursor:pointer;font-family:'DM Sans',sans-serif}
.save-contact-btn:hover{background:rgba(244,114,182,.25)}
/* Settings form */
.sf{margin-bottom:9px}
.sf label{display:block;font-size:.62rem;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px}
.sf input{width:100%;background:var(--bg);border:1px solid var(--border);border-radius:7px;padding:7px 10px;color:var(--hi);font-size:.72rem;font-family:'JetBrains Mono',monospace;outline:none;transition:border-color .2s}
.sf input:focus{border-color:var(--cyan)}
.sf .hint{font-size:.62rem;color:var(--muted);margin-top:3px;line-height:1.4}
.sf .hint a{color:var(--cyan);text-decoration:none}
.save-settings-btn{width:100%;margin-top:8px;padding:9px;background:linear-gradient(135deg,var(--cyan),var(--purple));color:#fff;border:none;border-radius:8px;font-size:.78rem;font-weight:600;cursor:pointer;font-family:'DM Sans',sans-serif}
.sec-label{font-size:.58rem;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);padding-bottom:5px;border-bottom:1px solid var(--border);margin:12px 0 8px}
.info-box{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:9px;font-size:.7rem;line-height:1.6;color:var(--muted);margin-bottom:8px}
.info-box strong{color:var(--hi)}

/* Empty state */
.empty{display:flex;flex-direction:column;align-items:center;justify-content:center;flex:1;gap:10px;color:var(--muted)}
.empty-icon{font-size:2.5rem;opacity:.4}
.empty-text{font-size:.82rem;text-align:center;line-height:1.5}
</style></head><body>

<div class="hdr">
  <div class="hdr-left">
    <div class="logo-pill">🚿 ZOAR</div>
    <div><div class="hdr-title">Bot Tester</div><div class="hdr-sub">Test before going live</div></div>
  </div>
  <div class="hdr-btns">
    <div id="gmail-status"><span class="status-dot" id="gmail-dot"></span><span id="gmail-txt">Gmail not set</span></div>
    <button class="hbtn" onclick="window.location='/'">⚡ Nexus</button>
  </div>
</div>

<div class="layout">
  <!-- Sessions sidebar -->
  <div class="sidebar">
    <div class="sb-head"><span>Conversations</span><button class="new-btn" onclick="newSession()">+ New</button></div>
    <div class="sb-list" id="sess-list">
      <div style="color:var(--muted);font-size:.72rem;padding:10px;text-align:center">No conversations yet</div>
    </div>
  </div>

  <div class="main">
    <!-- Chat column -->
    <div class="chat-col">
      <div class="chat-info" id="chat-info">
        <span class="empty-text" style="font-size:.76rem">Select a conversation or create one →</span>
      </div>
      <div class="messages" id="messages">
        <div class="empty">
          <div class="empty-icon">💬</div>
          <div class="empty-text">Select a conversation from the left<br>or click <strong>+ New</strong> to start a test</div>
        </div>
      </div>
      <div class="compose" id="compose" style="display:none">
        <div class="compose-top">
          <select class="csel" id="comp-channel" onchange="onChannelChange()">
            <option value="sms">📱 SMS</option>
            <option value="email">✉️ Email</option>
          </select>
          <select class="csel" id="comp-carrier">
            <option value="tmobile">T-Mobile</option>
            <option value="att">AT&T</option>
            <option value="verizon">Verizon</option>
            <option value="cricket">Cricket</option>
            <option value="metro">Metro PCS</option>
            <option value="boost">Boost</option>
            <option value="google_fi">Google Fi</option>
          </select>
          <span class="char-count" id="char-count">0 chars</span>
        </div>
        <div id="subj-row" class="subj-row" style="display:none">
          <span class="subj-label">Subject:</span>
          <input class="subj-input" id="comp-subject" placeholder="Your Event Restroom Rental — Zoar Bathroom Rentals">
        </div>
        <div class="incoming-row">
          <span class="inc-label">↙ Incoming:</span>
          <input class="inc-input" id="comp-incoming" placeholder="Paste customer's message here (or leave blank for initial outreach)">
        </div>
        <div class="gen-row">
          <button class="gbtn" onclick="generate('initial')" id="gbtn-init">📝 Initial Outreach</button>
          <button class="gbtn" onclick="generate('reply')" id="gbtn-reply">✨ Generate Reply</button>
          <button class="gbtn" onclick="generate('followup')" id="gbtn-fu">🔔 Follow-up</button>
        </div>
        <textarea class="msg-area" id="comp-msg" placeholder="AI generates here, or type your own…"
          oninput="document.getElementById('char-count').textContent=this.value.length+' chars'"></textarea>
        <div class="send-row">
          <span class="model-hint" id="model-hint"></span>
          <div style="display:flex;gap:6px">
            <button class="log-btn" onclick="logOnly()" title="Log as sent without actually sending">📋 Log Only</button>
            <button class="send-btn" id="send-btn" onclick="sendMessage()">📤 Send</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Right panel -->
    <div class="right-panel">
      <div class="rp-tabs">
        <button class="rp-tab active" id="rtab-contact" onclick="rtab('contact')">Contact</button>
        <button class="rp-tab" id="rtab-settings" onclick="rtab('settings')">Settings</button>
      </div>

      <!-- Contact pane -->
      <div class="rp-pane" id="rpane-contact">
        <div class="info-box" id="test-mode-info">
          <strong>🧪 Test Mode</strong><br>
          Currently testing with your own number and email only. Once you're happy with the bot's replies, you can point it at real leads.
        </div>
        <div class="cf"><label>Full Name</label><input id="cf-name" placeholder="Maria Garcia"></div>
        <div class="cf"><label>Phone (for SMS)</label><input id="cf-phone" placeholder="(818) 448-9055" type="tel"></div>
        <div class="cf"><label>Email</label><input id="cf-email" placeholder="customer@gmail.com" type="email"></div>
        <div class="cf"><label>Carrier</label>
          <select id="cf-carrier">
            <option value="tmobile">T-Mobile (most common in LA)</option>
            <option value="att">AT&T</option><option value="verizon">Verizon</option>
            <option value="cricket">Cricket</option><option value="metro">Metro PCS</option>
            <option value="boost">Boost</option><option value="google_fi">Google Fi</option>
          </select>
        </div>
        <div class="cf"><label>Channel</label>
          <select id="cf-channel" onchange="document.getElementById('comp-channel').value=this.value;onChannelChange()">
            <option value="sms">📱 SMS</option><option value="email">✉️ Email</option>
          </select>
        </div>
        <div class="cf"><label>Notes (event info, source, etc)</label><input id="cf-notes" placeholder="Wedding, June 15, Chatsworth, 100 guests"></div>
        <button class="save-contact-btn" onclick="saveContact()">Save Contact Info</button>

        <div class="sec-label" style="margin-top:16px">Quick fill — your test numbers</div>
        <div class="info-box" style="cursor:pointer" onclick="prefillKai()">
          <strong>Kai (you)</strong><br>
          📱 (818) 448-9055 · T-Mobile<br>
          ✉️ kaiescobar09@gmail.com
        </div>
      </div>

      <!-- Settings pane -->
      <div class="rp-pane" id="rpane-settings" style="display:none">
        <div class="sec-label">Gmail (sends SMS + emails)</div>
        <div class="sf"><label>Gmail Address</label><input id="sf-gmail" placeholder="zoarbathrooms@gmail.com" type="email"></div>
        <div class="sf"><label>App Password</label><input type="password" id="sf-pass" placeholder="xxxx xxxx xxxx xxxx">
          <div class="hint" style="font-size:.62rem;color:var(--muted);margin-top:3px">NOT your Gmail login. <a href="https://myaccount.google.com/apppasswords" target="_blank" style="color:var(--cyan)">Create here →</a> (needs 2FA on account)</div>
        </div>
        <div class="sec-label">GoHighLevel (optional)</div>
        <div class="sf"><label>GHL Private API Key</label><input type="password" id="sf-ghl-key" placeholder="eyJhbGci..."></div>
        <div class="sf"><label>Location ID</label><input id="sf-ghl-loc" placeholder="rMp3pF7h1dwrWc6DeGzr">
          <div class="hint" style="font-size:.62rem;color:var(--muted);margin-top:3px">From your GHL URL</div>
        </div>
        <button class="save-settings-btn" onclick="saveSettings()">Save Settings →</button>

        <div class="sec-label" style="margin-top:16px">How SMS gateway works</div>
        <div class="info-box">
          <strong>Free SMS via email</strong><br>
          Your Gmail sends to <em>number@carrier-gateway.com</em> → carrier delivers as SMS to the phone. Works on all US carriers. Replies go to your Gmail. No Twilio needed.<br><br>
          <strong>Carrier must match</strong> the recipient's actual carrier or it won't deliver.
        </div>
      </div>
    </div>
  </div>
</div>

<script>
let curSession=null;

function esc(t){return String(t).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function scrl(){const m=document.getElementById('messages');if(m)m.scrollTop=99999;}
function ts(){return new Date().toLocaleTimeString('en',{hour12:false,hour:'2-digit',minute:'2-digit'});}

// ── Status check ────────────────────────────────────────────────────────────
async function checkStatus(){
  const cfg=await fetch('/api/status').then(r=>r.json()).catch(()=>({}));
  const dot=document.getElementById('gmail-dot'),txt=document.getElementById('gmail-txt');
  // Check if gmail is configured by attempting a lightweight status check
  const has=cfg.available_models&&cfg.available_models.length>0;
  // We'll know gmail is set when we try to send — just show AI status for now
  dot.className='status-dot'+(has?' ok':'');
  txt.textContent=has?'AI ready':'AI not configured';
}

// ── Sessions ─────────────────────────────────────────────────────────────────
async function loadSessions(){
  const data=await fetch('/api/bot/sessions').then(r=>r.json()).catch(()=>[]);
  const list=document.getElementById('sess-list');
  if(!data.length){list.innerHTML='<div style="color:var(--muted);font-size:.72rem;padding:10px;text-align:center">No conversations yet<br>Click + New to start</div>';return;}
  list.innerHTML=data.map(s=>{
    const badge=s.channel==='sms'?'<span class="ch-badge ch-sms">SMS</span>':'<span class="ch-badge ch-email">EMAIL</span>';
    const active=curSession===s.session_id?'active':'';
    const sent=s.sent_count>0?`<span style="color:var(--green)">✓${s.sent_count}</span>`:'';
    return `<div class="sess-item ${active}" onclick="loadSession('${s.session_id}')">
      <div class="sess-name">${esc(s.name||'Unnamed')}${badge}</div>
      <div class="sess-meta">${esc(s.phone||s.email||'—')} · ${s.msg_count} msgs ${sent}</div>
    </div>`;
  }).join('');
}

async function newSession(){
  const name=prompt('Contact name (or your name for testing):');
  if(!name)return;
  const r=await fetch('/api/bot/session',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name,phone:'',email:'',channel:'sms',carrier:'tmobile'})});
  const d=await r.json();
  await loadSessions();
  loadSession(d.session_id);
}

async function loadSession(sid){
  curSession=sid;
  const data=await fetch(`/api/bot/session/${sid}`).then(r=>r.json()).catch(()=>null);
  if(!data)return;
  const s=data.session;
  const msgs=data.messages||[];

  // Update sidebar active state
  document.querySelectorAll('.sess-item').forEach(el=>{el.classList.remove('active');});
  const el=document.getElementById('msgs-'+sid);
  document.querySelectorAll('.sess-item').forEach(el=>{
    if(el.onclick.toString().includes(sid)) el.classList.add('active');
  });
  await loadSessions(); // re-render to update active

  // Fill contact panel
  document.getElementById('cf-name').value=s.name||'';
  document.getElementById('cf-phone').value=s.phone||'';
  document.getElementById('cf-email').value=s.email||'';
  document.getElementById('cf-carrier').value=s.carrier||'tmobile';
  document.getElementById('cf-channel').value=s.channel||'sms';
  document.getElementById('cf-notes').value=s.notes||'';
  document.getElementById('comp-channel').value=s.channel||'sms';
  document.getElementById('comp-carrier').value=s.carrier||'tmobile';
  onChannelChange();

  // Update chat header
  const ch=s.channel==='sms'?'📱':'✉️';
  document.getElementById('chat-info').innerHTML=`
    <span class="ci-name">${esc(s.name||'Unnamed')}</span>
    <span class="ci-detail">${ch} ${esc(s.phone||s.email||'—')}</span>
    ${s.carrier?`<span class="ci-detail">${esc(s.carrier)}</span>`:''}
    <span class="test-badge">🧪 TEST MODE</span>
    ${s.notes?`<span style="font-size:.68rem;color:var(--muted)">${esc(s.notes)}</span>`:''}`;

  // Render messages
  const feed=document.getElementById('messages');
  if(!msgs.length){
    feed.innerHTML='<div class="empty"><div class="empty-icon">💬</div><div class="empty-text">No messages yet.<br>Generate an initial outreach below.</div></div>';
  } else {
    feed.innerHTML=msgs.map(m=>{
      const dir=m.role==='assistant'?'outbound':'inbound';
      const who=m.role==='assistant'?'Kai (bot)':'Customer';
      const sentTag=m.sent?'<span class="sent-tag">✓ sent</span>':m.status==='failed'?`<span class="failed-tag">✗ failed</span>`:'<span class="draft-tag">draft</span>';
      const modelTxt=m.model?`<span style="color:#1a3a50;font-size:.58rem">${esc(m.model)}</span>`:'';
      return `<div class="msg-wrap ${dir}">
        <div class="msg-label">${who} ${m.role==='assistant'?sentTag:''} ${modelTxt}</div>
        ${m.subject?`<div style="font-size:.68rem;color:var(--muted);margin-bottom:3px">Subject: ${esc(m.subject)}</div>`:''}
        <div class="msg-bubble">${esc(m.content||'')}</div>
        <div class="msg-ts">${m.ts||''}</div>
      </div>`;
    }).join('');
  }
  document.getElementById('compose').style.display='flex';
  scrl();
}

// ── Generate ─────────────────────────────────────────────────────────────────
async function generate(mode){
  if(!curSession){alert('Select or create a conversation first');return;}
  const channel=document.getElementById('comp-channel').value;
  const incoming=document.getElementById('comp-incoming').value.trim();
  const name=document.getElementById('cf-name').value.trim();
  const followup_num=mode==='followup'?1:0;

  const btns=['gbtn-init','gbtn-reply','gbtn-fu'];
  btns.forEach(b=>{document.getElementById(b).disabled=true;});
  const btn=document.getElementById(mode==='initial'?'gbtn-init':mode==='followup'?'gbtn-fu':'gbtn-reply');
  const orig=btn.textContent; btn.textContent='Generating…';

  const r=await fetch('/api/bot/generate',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({session_id:curSession,mode,channel,incoming,contact_name:name,followup_num})});
  const d=await r.json();
  btns.forEach(b=>{document.getElementById(b).disabled=false;});
  btn.textContent=orig;

  if(d.error){alert('Error: '+d.error);return;}
  document.getElementById('comp-msg').value=d.body||'';
  if(d.subject) document.getElementById('comp-subject').value=d.subject;
  document.getElementById('model-hint').textContent=d.model?'via '+d.model:'';
  document.getElementById('char-count').textContent=(d.body||'').length+' chars';
}

// ── Send ─────────────────────────────────────────────────────────────────────
async function sendMessage(){
  if(!curSession){alert('Select a conversation first');return;}
  const msg=document.getElementById('comp-msg').value.trim();
  if(!msg){alert('Write or generate a message first');return;}
  const channel=document.getElementById('comp-channel').value;
  const phone=document.getElementById('cf-phone').value.trim();
  const email=document.getElementById('cf-email').value.trim();
  const carrier=document.getElementById('comp-carrier').value;
  const subject=document.getElementById('comp-subject').value.trim()||'Your Event Restroom Rental — Zoar Bathroom Rentals';
  const name=document.getElementById('cf-name').value.trim();

  if(channel==='sms'&&!phone){alert('Add phone number in Contact tab first');return;}
  if(channel==='email'&&!email){alert('Add email address in Contact tab first');return;}

  const btn=document.getElementById('send-btn');btn.disabled=true;btn.textContent='Sending…';
  const r=await fetch('/api/bot/send',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({session_id:curSession,channel,phone,email,carrier,message:msg,subject,contact_name:name})});
  const d=await r.json();
  btn.disabled=false;btn.textContent='📤 Send';

  if(d.ok){
    document.getElementById('comp-msg').value='';
    document.getElementById('comp-incoming').value='';
    document.getElementById('comp-subject').value='';
    document.getElementById('model-hint').textContent='';
    document.getElementById('char-count').textContent='0 chars';
    await loadSession(curSession);
  } else {
    alert('❌ Failed: '+(d.error||'unknown')+'\n\nCheck Settings — make sure Gmail + App Password are saved.');
    // Still log the message as draft
    await fetch('/api/bot/save-message',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({session_id:curSession,role:'assistant',content:msg,subject,sent:false,status:'failed'})});
    await loadSession(curSession);
  }
}

async function logOnly(){
  if(!curSession)return;
  const msg=document.getElementById('comp-msg').value.trim();
  if(!msg){alert('Nothing to log');return;}
  const subject=document.getElementById('comp-subject').value.trim();
  await fetch('/api/bot/save-message',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({session_id:curSession,role:'assistant',content:msg,subject,sent:false,status:'draft'})});
  document.getElementById('comp-msg').value='';
  document.getElementById('model-hint').textContent='';
  await loadSession(curSession);
}

// ── Contact / Settings ────────────────────────────────────────────────────────
async function saveContact(){
  if(!curSession){
    const r=await fetch('/api/bot/session',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:document.getElementById('cf-name').value.trim()||'Test',phone:'',email:''})});
    const d=await r.json(); curSession=d.session_id;
  }
  const body={session_id:curSession,name:document.getElementById('cf-name').value.trim(),
    phone:document.getElementById('cf-phone').value.trim(),email:document.getElementById('cf-email').value.trim(),
    carrier:document.getElementById('cf-carrier').value,channel:document.getElementById('cf-channel').value,
    notes:document.getElementById('cf-notes').value.trim()};
  await fetch('/api/bot/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  document.getElementById('comp-channel').value=body.channel;
  document.getElementById('comp-carrier').value=body.carrier;
  onChannelChange();
  await loadSessions();
  await loadSession(curSession);
  const btn=document.querySelector('.save-contact-btn');btn.textContent='✅ Saved!';setTimeout(()=>btn.textContent='Save Contact Info',2000);
}

function prefillKai(){
  document.getElementById('cf-name').value='Kai (test)';
  document.getElementById('cf-phone').value='8184489055';
  document.getElementById('cf-email').value='kaiescobar09@gmail.com';
  document.getElementById('cf-carrier').value='tmobile';
  document.getElementById('cf-channel').value='sms';
  document.getElementById('cf-notes').value='Test — my own number';
  document.getElementById('comp-channel').value='sms';
  onChannelChange();
}

async function saveSettings(){
  const body={gmail_address:document.getElementById('sf-gmail').value.trim(),
    gmail_app_password:document.getElementById('sf-pass').value.trim(),
    ghl_api_key:document.getElementById('sf-ghl-key').value.trim(),
    ghl_location_id:document.getElementById('sf-ghl-loc').value.trim()};
  await fetch('/api/bot/save-settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const btn=document.querySelector('.save-settings-btn');btn.textContent='✅ Saved!';
  document.getElementById('gmail-dot').className='status-dot ok';
  document.getElementById('gmail-txt').textContent='Gmail ready';
  setTimeout(()=>btn.textContent='Save Settings →',2000);
}

async function loadSavedSettings(){
  // Pre-fill settings if already configured
  const cfg=await fetch('/api/status').then(r=>r.json()).catch(()=>({}));
  if(cfg.available_models){
    document.getElementById('gmail-dot').className='status-dot ok';
    document.getElementById('gmail-txt').textContent='AI ready';
  }
}

function onChannelChange(){
  const ch=document.getElementById('comp-channel').value;
  const sr=document.getElementById('subj-row');const cr=document.querySelector('#compose .csel:nth-child(2)');
  if(sr) sr.style.display=ch==='email'?'flex':'none';
}

function rtab(tab){
  document.querySelectorAll('.rp-tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.rp-pane').forEach(p=>p.style.display='none');
  document.getElementById('rtab-'+tab).classList.add('active');
  document.getElementById('rpane-'+tab).style.display='block';
}

checkStatus();loadSessions();loadSavedSettings();
</script></body></html>"""


HIGGSFIELD_PAGE = r"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nexus — Higgsfield Images</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#060a0f;--s1:#0c1520;--border:#1a2a3a;--text:#b8c8d8;--hi:#e8f0f8;--muted:#3a5a72;--cyan:#06b6d4;--purple:#8b5cf6;--green:#22d47a;--orange:#fb923c;--red:#ef4444;--yellow:#fbbf24}
body{font-family:'DM Sans',sans-serif;background:var(--bg);color:var(--text);height:100vh;display:flex;flex-direction:column;overflow:hidden}
.hdr{height:48px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;padding:0 16px;background:var(--s1);flex-shrink:0}
.hdr-left{display:flex;align-items:center;gap:10px}
.bi{width:22px;height:22px;background:linear-gradient(135deg,var(--cyan),var(--purple));border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:.65rem}
.bn{font-size:.85rem;font-weight:600;color:var(--hi);text-decoration:none}
.hf-layout{flex:1;display:grid;grid-template-columns:380px 1fr;overflow:hidden}
.hf-controls{background:var(--s1);border-right:1px solid var(--border);overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:14px;scrollbar-width:thin;scrollbar-color:var(--border) transparent}
.hf-main{display:flex;flex-direction:column;overflow:hidden}
.hf-status{padding:12px 16px;border-bottom:1px solid var(--border);display:flex;gap:10px;flex-wrap:wrap;background:var(--s1)}
.hf-images{flex:1;overflow-y:auto;padding:16px;display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px;align-content:start;scrollbar-width:thin;scrollbar-color:var(--border) transparent}
.hf-section{background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:14px}
.hf-section-title{font-size:.6rem;font-weight:600;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);margin-bottom:10px}
.hf-pill{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:7px 11px;min-width:100px}
.hf-pill-label{font-size:.55rem;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}
.hf-pill-value{font-size:.9rem;font-weight:600;color:var(--hi);font-family:'JetBrains Mono',monospace;margin-top:2px}
.hf-img-card{border-radius:8px;overflow:hidden;border:1px solid var(--border);background:var(--s1);transition:border-color .2s}
.hf-img-card:hover{border-color:rgba(6,182,212,.4)}
.hf-img-meta{padding:6px 8px;font-size:.6rem;color:var(--muted);line-height:1.3}
.hf-img-prompt{font-size:.58rem;color:#2a4a5a;margin-top:2px;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.hf-btn{padding:9px 16px;border-radius:9px;border:none;cursor:pointer;font-family:'DM Sans',sans-serif;font-size:.8rem;font-weight:600;transition:all .15s}
.hf-btn:disabled{opacity:.4;cursor:not-allowed}
.hf-btn-primary{background:linear-gradient(135deg,var(--cyan),var(--purple));color:#fff}
.hf-btn-primary:hover:not(:disabled){opacity:.85}
.hf-btn-danger{background:rgba(239,68,68,.15);color:var(--red);border:1px solid rgba(239,68,68,.3)}
.hf-btn-secondary{background:rgba(6,182,212,.1);color:var(--cyan);border:1px solid rgba(6,182,212,.3)}
.hf-btn-secondary:hover{background:rgba(6,182,212,.18)}
.hf-dropzone{border:2px dashed var(--border);border-radius:10px;padding:20px;text-align:center;cursor:pointer;transition:border-color .2s}
.hf-dropzone:hover,.hf-dropzone.dragover{border-color:var(--cyan)}
.hf-dropzone img{max-height:100px;border-radius:6px;margin-bottom:6px}
.hf-progress{background:#0a1520;border-radius:5px;height:6px;overflow:hidden;margin:8px 16px}
.hf-progress-bar{height:100%;border-radius:5px;background:linear-gradient(90deg,var(--cyan),var(--purple));transition:width .5s cubic-bezier(.4,0,.2,1)}
textarea.hf-input{width:100%;background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:10px;color:var(--hi);font-size:.76rem;font-family:'DM Sans',sans-serif;resize:vertical;outline:none;transition:border-color .2s}
textarea.hf-input:focus{border-color:rgba(6,182,212,.4)}
input.hf-num{width:100%;background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:7px;color:var(--hi);font-size:.78rem;outline:none;font-family:'JetBrains Mono',monospace}
.hf-setting-row{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.hf-setting-label{font-size:.62rem;color:var(--muted);margin-bottom:3px}
.hf-setting-val{padding:7px;font-size:.76rem;color:var(--cyan);font-family:'JetBrains Mono',monospace}
.hf-log{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:8px 10px;margin-top:8px;max-height:120px;overflow-y:auto;font-size:.65rem;font-family:'JetBrains Mono',monospace;color:var(--muted);line-height:1.6;scrollbar-width:thin;scrollbar-color:var(--border) transparent}
.hf-log-entry{border-bottom:1px solid rgba(26,42,58,.5);padding:2px 0}
.hf-log-entry:last-child{border-bottom:none}
.hf-empty{color:var(--muted);font-size:.78rem;grid-column:1/-1;text-align:center;padding:40px}
.state-idle{color:var(--muted)}.state-generating{color:var(--cyan)}.state-waiting_queue{color:var(--orange)}.state-done{color:var(--green)}.state-error{color:var(--red)}.state-stopping{color:var(--yellow)}.state-stopped{color:var(--yellow)}
@keyframes pulse{0%,100%{opacity:.6}50%{opacity:1}}
.pulse{animation:pulse 2s ease-in-out infinite}
.hf-modal-overlay{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.85);z-index:1000;display:none;align-items:center;justify-content:center;backdrop-filter:blur(8px)}
.hf-modal-overlay.active{display:flex}
.hf-modal{background:var(--s1);border:1px solid var(--border);border-radius:16px;max-width:400px;width:90%;max-height:88vh;display:flex;flex-direction:column}
.hf-modal-scroll{overflow-y:auto;flex:1;scrollbar-width:thin;scrollbar-color:var(--border) transparent}
.hf-modal-img{width:100%;max-height:50vh;object-fit:cover;display:block;border-radius:16px 16px 0 0}
.hf-modal-body{padding:14px;display:flex;flex-direction:column;gap:8px}
.hf-modal-label{font-size:.82rem;font-weight:600;color:var(--hi)}
.hf-modal-prompt{font-size:.65rem;color:var(--muted);line-height:1.4;max-height:40px;overflow:hidden}
.hf-modal-actions{display:flex;flex-direction:column;gap:6px;padding:12px 14px;border-top:1px solid var(--border);flex-shrink:0}
.hf-img-card{cursor:pointer;position:relative}
.hf-img-label{position:absolute;top:6px;left:6px;display:flex;gap:3px;pointer-events:none}
.hf-img-tag{font-size:.48rem;font-weight:700;padding:2px 5px;border-radius:10px;font-family:'JetBrains Mono',monospace;line-height:1.2;letter-spacing:.02em;backdrop-filter:blur(4px)}
.hf-tag-scene{background:rgba(6,182,212,.5);color:#fff}
.hf-tag-frame{background:rgba(139,92,246,.5);color:#fff}
.hf-tag-idx{background:rgba(255,255,255,.2);color:#fff}
.hf-quality{position:absolute;top:6px;right:6px;width:24px;height:24px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:.55rem;font-weight:800;font-family:'JetBrains Mono',monospace;backdrop-filter:blur(4px);pointer-events:none}
.hf-q-great{background:rgba(34,212,122,.7);color:#fff}
.hf-q-good{background:rgba(34,212,122,.45);color:#fff}
.hf-q-ok{background:rgba(251,191,36,.5);color:#fff}
.hf-q-bad{background:rgba(239,68,68,.6);color:#fff}
.hf-q-terrible{background:rgba(239,68,68,.85);color:#fff}
.hf-feedback-box{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:10px;font-size:.62rem;color:var(--muted);line-height:1.5;max-height:100px;overflow-y:auto;scrollbar-width:thin;scrollbar-color:var(--border) transparent}
.hf-script-select{width:100%;background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:7px;color:var(--hi);font-size:.72rem;outline:none;font-family:'DM Sans',sans-serif;margin-bottom:6px}
.hf-script-select option{background:var(--bg);color:var(--hi)}
.hf-history-row{display:flex;gap:6px;margin-bottom:6px}
</style></head><body>

<div class="hdr">
  <div class="hdr-left">
    <a href="/" style="text-decoration:none;display:flex;align-items:center;gap:8px">
      <div class="bi">⚡</div><span class="bn">Nexus</span>
    </a>
    <span style="color:var(--muted);font-size:.7rem">→</span>
    <span style="color:var(--hi);font-size:.8rem;font-weight:600">🖼 Higgsfield Images</span>
  </div>
  <div style="display:flex;gap:8px;align-items:center">
    <span id="hf-login-status" style="font-size:.62rem;font-family:'JetBrains Mono',monospace;color:var(--muted)">Checking...</span>
    <button class="hf-btn hf-btn-secondary" onclick="hfLogin()" id="hf-login-btn">🔑 Login to Higgsfield</button>
  </div>
</div>

<div class="hf-layout">
  <!-- LEFT: Controls -->
  <div class="hf-controls">

    <div class="hf-section">
      <div class="hf-section-title">Reference Bathroom Image</div>
      <div class="hf-dropzone" id="hf-drop" onclick="document.getElementById('hf-file').click()">
        <input type="file" id="hf-file" accept="image/*" style="display:none" onchange="handleUpload(this)">
        <div id="hf-upload-preview">
          <div style="font-size:1.4rem;margin-bottom:4px">📷</div>
          <div style="font-size:.72rem;color:var(--muted)">Drop image here or click to upload</div>
        </div>
      </div>
    </div>

    <div class="hf-section" style="border:1px solid rgba(6,182,212,.25);background:linear-gradient(135deg,rgba(6,182,212,.04),rgba(139,92,246,.04))">
      <div class="hf-section-title" style="color:var(--cyan)">⚡ Static Ad Generator — Unlimited Mode</div>
      <div style="font-size:.66rem;color:var(--muted);margin-bottom:10px">Pre-built prompts + CTA overlays. Ready-to-post Facebook/Instagram ads.</div>
      <div style="margin-bottom:8px">
        <div class="hf-setting-label">Category</div>
        <select id="hf-ad-category" class="hf-script-select" onchange="loadAdCampaigns()">
          <option value="">All Categories</option>
        </select>
      </div>
      <div id="hf-ad-list" style="max-height:200px;overflow-y:auto;display:flex;flex-direction:column;gap:4px;margin-bottom:10px;scrollbar-width:thin;scrollbar-color:var(--border) transparent"></div>
      <div class="hf-setting-row" style="margin-bottom:8px">
        <div>
          <div class="hf-setting-label">Images per Campaign</div>
          <input type="number" class="hf-num" id="hf-ad-count" value="4" min="1" max="20">
        </div>
        <div style="display:flex;align-items:end">
          <button class="hf-btn hf-btn-secondary" style="width:100%;font-size:.65rem" onclick="selectAllCampaigns()">Select All</button>
        </div>
      </div>
      <div style="display:flex;gap:8px">
        <button class="hf-btn hf-btn-primary" style="flex:1" id="hf-ad-start-btn" onclick="hfStartStaticAds()">🚀 Generate Static Ads</button>
        <button class="hf-btn hf-btn-danger" id="hf-stop-btn" onclick="hfStop()" style="display:none">⏹ Stop</button>
      </div>
      <div id="hf-ad-results" style="margin-top:8px"></div>
    </div>

    <div style="display:flex;gap:6px;flex-wrap:wrap">
      <button class="hf-btn hf-btn-secondary" style="flex:1" onclick="hfRefreshImages()">🔄 Refresh</button>
      <button class="hf-btn hf-btn-secondary" style="flex:1" onclick="hfAnalyzeImages()">🧠 Analyze</button>
      <button class="hf-btn hf-btn-secondary" style="flex:1" onclick="hfDownloadImages()">⬇ Download</button>
    </div>
    <div id="hf-feedback-section" style="display:none">
      <div class="hf-section">
        <div class="hf-section-title">AI Feedback <span id="hf-avg-score" style="color:var(--cyan)"></span></div>
        <div class="hf-feedback-box" id="hf-feedback-text"></div>
      </div>
    </div>

    <details style="margin-top:4px">
      <summary style="cursor:pointer;font-size:.7rem;color:var(--muted);padding:8px 0;user-select:none">🎬 Video Scene Generator (Advanced)</summary>

    <div class="hf-section">
      <div class="hf-section-title">Ad Script / Scenes</div>
      <div class="hf-history-row">
        <select class="hf-script-select" id="hf-script-history" onchange="loadSavedScript(this.value)">
          <option value="">— Saved Scripts —</option>
        </select>
        <button class="hf-btn hf-btn-secondary" style="padding:6px 10px;font-size:.65rem;white-space:nowrap" onclick="saveCurrentScript()">Save</button>
        <button class="hf-btn hf-btn-danger" style="padding:6px 8px;font-size:.65rem;white-space:nowrap" onclick="deleteSavedScript()">Del</button>
      </div>
      <textarea class="hf-input" id="hf-script" rows="8" placeholder="Paste your ad script here...&#10;&#10;Each scene separated by a blank line.&#10;Example:&#10;&#10;Scene 1: Wide establishing shot of luxury bathroom trailer at sunset, golden hour lighting&#10;&#10;Scene 2: Close-up of elegant fixtures and marble countertops, warm ambient glow"></textarea>
      <button class="hf-btn hf-btn-secondary" style="margin-top:8px;width:100%" onclick="autoGenScenes()">✨ Auto-Generate Scenes from Script</button>
      <div id="hf-scenes-parsed" style="margin-top:6px;font-size:.68rem;color:var(--muted)"></div>
    </div>

    <div class="hf-section">
      <div class="hf-section-title">Generation Settings</div>
      <div class="hf-setting-row">
        <div>
          <div class="hf-setting-label">Images per Frame</div>
          <input type="number" class="hf-num" id="hf-ipf" value="30" min="1" max="100">
        </div>
        <div>
          <div class="hf-setting-label">Model</div>
          <div class="hf-setting-val">Nano Banana Pro</div>
        </div>
        <div>
          <div class="hf-setting-label">Aspect Ratio</div>
          <div class="hf-setting-val">9:16</div>
        </div>
        <div>
          <div class="hf-setting-label">Quality</div>
          <div class="hf-setting-val">2K</div>
        </div>
      </div>
    </div>

    <div style="display:flex;gap:8px">
      <button class="hf-btn hf-btn-primary" style="flex:1" id="hf-start-btn" onclick="hfStart()">▶ Start Generation</button>
    </div>

    <div class="hf-section">
      <div class="hf-section-title">End Frame from Liked Start</div>
      <div style="font-size:.68rem;color:var(--muted);margin-bottom:8px">Upload a start frame you like to generate end frames for that scene</div>
      <div style="display:flex;gap:8px;margin-bottom:8px">
        <div style="flex:1">
          <div class="hf-setting-label">Scene #</div>
          <input type="number" class="hf-num" id="hf-end-scene" value="1" min="1" max="20">
        </div>
        <div style="flex:1">
          <div class="hf-setting-label">Scene Description</div>
          <input type="text" class="hf-num" id="hf-end-desc" placeholder="Scene desc...">
        </div>
      </div>
      <div class="hf-dropzone" id="hf-liked-drop" onclick="document.getElementById('hf-liked-file').click()" style="padding:12px">
        <input type="file" id="hf-liked-file" accept="image/*" style="display:none" onchange="handleLikedUpload(this)">
        <div id="hf-liked-preview">
          <div style="font-size:1rem;margin-bottom:2px">⭐</div>
          <div style="font-size:.66rem;color:var(--muted)">Upload liked start frame</div>
        </div>
      </div>
      <button class="hf-btn hf-btn-secondary" style="width:100%;margin-top:8px" id="hf-endframe-btn" onclick="hfStartEndFrames()" disabled>Generate End Frames</button>
    </div>

    </details>

    <div class="hf-section" id="hf-log-section" style="display:none">
      <div class="hf-section-title">Activity Log</div>
      <div class="hf-log" id="hf-log"></div>
    </div>
  </div>

  <!-- RIGHT: Status + Images -->
  <div class="hf-main">
    <div class="hf-status" id="hf-status-bar">
      <div class="hf-pill">
        <div class="hf-pill-label">Status</div>
        <div class="hf-pill-value" id="hf-state">idle</div>
      </div>
      <div class="hf-pill">
        <div class="hf-pill-label">Scene</div>
        <div class="hf-pill-value"><span id="hf-scene-num">0</span>/<span id="hf-scene-total">0</span></div>
      </div>
      <div class="hf-pill">
        <div class="hf-pill-label">Frame</div>
        <div class="hf-pill-value" id="hf-frame-type">—</div>
      </div>
      <div class="hf-pill">
        <div class="hf-pill-label">Progress</div>
        <div class="hf-pill-value"><span id="hf-img-done">0</span>/<span id="hf-img-total">30</span></div>
      </div>
      <div class="hf-pill">
        <div class="hf-pill-label">Queue</div>
        <div class="hf-pill-value"><span id="hf-queue">0</span>/4</div>
      </div>
    </div>
    <div class="hf-progress"><div class="hf-progress-bar" id="hf-prog-bar" style="width:0%"></div></div>
    <div class="hf-images" id="hf-image-grid">
      <div class="hf-empty">Generated images will appear here as they're created.</div>
    </div>
  </div>
</div>

<!-- Image Detail Modal -->
<div class="hf-modal-overlay" id="hf-modal" onclick="if(event.target===this)closeModal()">
  <div class="hf-modal">
    <div class="hf-modal-scroll">
      <img class="hf-modal-img" id="hf-modal-img" src="">
      <div class="hf-modal-body">
        <div class="hf-modal-label" id="hf-modal-label"></div>
        <div class="hf-modal-prompt" id="hf-modal-prompt"></div>
      </div>
    </div>
    <div class="hf-modal-actions">
      <button class="hf-btn hf-btn-primary" onclick="modalGenEndScene()" style="font-size:.75rem;padding:8px">Generate End Frames from This</button>
      <div style="display:flex;gap:6px">
        <button class="hf-btn hf-btn-secondary" onclick="modalDownload()" style="flex:1;font-size:.72rem;padding:7px">Download</button>
        <button class="hf-btn hf-btn-danger" onclick="modalDelete()" style="font-size:.72rem;padding:7px 14px">Delete</button>
      </div>
    </div>
  </div>
</div>

<script>
let refImagePath='',pollTimer=null,logEntries=[],currentModalImg=null,scriptHistory=[];

function esc(t){return String(t).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

function addLog(msg){
  logEntries.unshift({time:new Date().toLocaleTimeString(),msg});
  if(logEntries.length>50)logEntries.pop();
  const el=document.getElementById('hf-log');
  const sec=document.getElementById('hf-log-section');
  sec.style.display='block';
  el.innerHTML=logEntries.map(e=>`<div class="hf-log-entry"><span style="color:var(--cyan)">${e.time}</span> ${esc(e.msg)}</div>`).join('');
}

async function pollStatus(){
  try{
    const s=await fetch('/api/higgsfield/status').then(r=>r.json());
    const loginEl=document.getElementById('hf-login-status');
    loginEl.textContent=s.logged_in?'● Connected':'○ Not logged in';
    loginEl.style.color=s.logged_in?'var(--green)':'var(--red)';

    const stateEl=document.getElementById('hf-state');
    stateEl.textContent=s.state||'idle';
    stateEl.className='hf-pill-value state-'+(s.state||'idle');
    if(['generating','waiting_queue'].includes(s.state))stateEl.classList.add('pulse');

    document.getElementById('hf-scene-num').textContent=s.current_scene||0;
    document.getElementById('hf-scene-total').textContent=s.total_scenes||0;
    document.getElementById('hf-frame-type').textContent=s.current_frame||'—';
    document.getElementById('hf-img-done').textContent=s.images_done||0;
    document.getElementById('hf-img-total').textContent=s.images_total||30;
    document.getElementById('hf-queue').textContent=s.queue_active||0;

    const queueEl=document.getElementById('hf-queue');
    queueEl.style.color=(s.queue_active>=4)?'var(--red)':'var(--hi)';

    const pct=s.images_total?Math.round((s.images_done/s.images_total)*100):0;
    document.getElementById('hf-prog-bar').style.width=pct+'%';

    const running=['generating','waiting_queue'].includes(s.state);
    document.getElementById('hf-stop-btn').style.display=running?'block':'none';
    document.getElementById('hf-start-btn').disabled=running;

    if(s.generated_images&&s.generated_images.length>0)updateImageGrid(s.generated_images);
    if(s.avg_quality){
      document.getElementById('hf-feedback-section').style.display='block';
      document.getElementById('hf-avg-score').textContent=`(avg: ${s.avg_quality}/10)`;
    }
  }catch(e){console.error('Poll error:',e);}
}

function imgSrcFor(img){
  if(img.local_path)return `/api/higgsfield/local-image?path=${encodeURIComponent(img.local_path)}`;
  if(img.url)return `/api/higgsfield/proxy-image?url=${encodeURIComponent(img.url)}`;
  return '';
}

function qClass(s){return s>=8?'hf-q-great':s>=6?'hf-q-good':s>=4?'hf-q-ok':s>=2?'hf-q-bad':'hf-q-terrible';}

function updateImageGrid(images){
  const grid=document.getElementById('hf-image-grid');
  const urlCount=images.filter(i=>i.url||i.local_path).length;
  const scoreCount=images.filter(i=>i.quality_score!=null).length;
  const hash=images.length+'-'+urlCount+'-'+scoreCount;
  if(grid.dataset.hash===hash)return;
  grid.dataset.hash=hash;
  window._hfImages=images;
  grid.innerHTML=images.map((img,i)=>{
    const fL=img.frame==='start'?'Start':'End';
    const imgSrc=imgSrcFor(img);
    const qs=img.quality_score;
    const hasScore=qs!=null&&qs!==undefined;
    return `<div class="hf-img-card" onclick="openModal(${i})">
      ${imgSrc?
        `<img src="${imgSrc}" style="width:100%;aspect-ratio:9/16;object-fit:cover;display:block" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">`+
        `<div style="aspect-ratio:9/16;background:linear-gradient(135deg,rgba(6,182,212,.08),rgba(139,92,246,.08));align-items:center;justify-content:center;font-size:.7rem;color:var(--muted);padding:10px;text-align:center;display:none"><div>Scene ${img.scene} · ${fL} #${img.index}</div></div>`
      :
        `<div style="aspect-ratio:9/16;background:linear-gradient(135deg,rgba(6,182,212,.08),rgba(139,92,246,.08));display:flex;align-items:center;justify-content:center;font-size:.7rem;color:var(--muted);padding:10px;text-align:center"><div>Scene ${img.scene} · ${fL} #${img.index}</div></div>`
      }
      <div class="hf-img-label">
        <span class="hf-img-tag hf-tag-scene">S${img.scene}</span>
        <span class="hf-img-tag hf-tag-frame">${fL}</span>
        <span class="hf-img-tag hf-tag-idx">#${img.index}</span>
      </div>
      ${hasScore?`<div class="hf-quality ${qClass(qs)}">${qs}</div>`:''}
      <div class="hf-img-meta" style="font-size:.56rem;padding:4px 7px">Scene ${img.scene} · ${fL} · #${img.index}${hasScore?' · '+qs+'/10':''}</div>
    </div>`;
  }).join('');
}

async function hfDeleteImage(scene,frame,index){
  try{
    const r=await fetch('/api/higgsfield/delete-image',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({scene,frame,index})
    });
    const d=await r.json();
    if(d.ok){addLog(`Deleted scene ${scene} ${frame} #${index}`);
      document.getElementById('hf-image-grid').dataset.hash='';pollStatus();}
    else addLog('Delete failed');
  }catch(e){addLog('Delete error: '+e.message);}
}

// ── Modal Functions ──
function openModal(idx){
  const img=window._hfImages[idx];if(!img)return;
  currentModalImg=img;
  const src=imgSrcFor(img);
  const m=document.getElementById('hf-modal');
  document.getElementById('hf-modal-img').src=src||'';
  document.getElementById('hf-modal-img').style.display=src?'block':'none';
  const qs=img.quality_score;
  const scoreHtml=qs!=null?` · <span style="color:${qs>=7?'var(--green)':qs>=5?'var(--orange)':'var(--red)'}">${qs}/10</span>`:'';
  document.getElementById('hf-modal-label').innerHTML=`Scene ${img.scene} · ${img.frame==='start'?'Start Frame':'End Frame'} · #${img.index}${scoreHtml}`;
  let promptText=img.prompt||'(no prompt)';
  if(img.issues&&img.issues.length>0)promptText+='\n\nIssues: '+img.issues.join(', ');
  document.getElementById('hf-modal-prompt').textContent=promptText;
  m.classList.add('active');
}
function closeModal(){document.getElementById('hf-modal').classList.remove('active');currentModalImg=null;}
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeModal();});

async function modalGenEndScene(){
  if(!currentModalImg)return;
  const img=currentModalImg;
  closeModal();
  // Use this image as the liked start frame for end frame generation
  const src=imgSrcFor(img);
  if(!src){addLog('No image source to use');return;}
  // Download the image to a temp file via the server, then use it
  addLog(`Using Scene ${img.scene} Start #${img.index} as reference for end frames...`);
  try{
    const r=await fetch('/api/higgsfield/use-as-end-ref',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({scene:img.scene,frame:img.frame,index:img.index})
    });
    const d=await r.json();
    if(d.ok){
      likedFramePath=d.path;
      document.getElementById('hf-end-scene').value=img.scene;
      document.getElementById('hf-endframe-btn').disabled=false;
      document.getElementById('hf-liked-preview').innerHTML=
        `<img src="${src}" style="max-height:80px;border-radius:6px;margin-bottom:4px"><div style="font-size:.62rem;color:var(--green)">Scene ${img.scene} Start #${img.index}</div>`;
      addLog(`Ready! Set scene description and click "Generate End Frames"`);
    }else addLog('Error: '+(d.error||''));
  }catch(e){addLog('Error: '+e.message);}
}

function modalDownload(){
  if(!currentModalImg)return;
  const src=imgSrcFor(currentModalImg);
  if(!src){addLog('No image to download');return;}
  const a=document.createElement('a');
  a.href=src;a.download=`scene${currentModalImg.scene}_${currentModalImg.frame}_${currentModalImg.index}.jpg`;
  document.body.appendChild(a);a.click();a.remove();
  addLog(`Downloaded Scene ${currentModalImg.scene} ${currentModalImg.frame} #${currentModalImg.index}`);
}

function modalDelete(){
  if(!currentModalImg)return;
  if(!confirm('Delete this image?'))return;
  hfDeleteImage(currentModalImg.scene,currentModalImg.frame,currentModalImg.index);
  closeModal();
}

// ── Script History Functions ──
async function loadScriptHistory(){
  try{
    const r=await fetch('/api/higgsfield/script-history').then(r=>r.json());
    scriptHistory=r.scripts||[];
    const sel=document.getElementById('hf-script-history');
    sel.innerHTML='<option value="">— Saved Scripts ('+scriptHistory.length+') —</option>';
    scriptHistory.forEach((s,i)=>{
      const opt=document.createElement('option');opt.value=i;
      const date=new Date(s.created_at).toLocaleDateString();
      opt.textContent=`${s.name} (${s.scene_count} scenes, ${date})`;
      sel.appendChild(opt);
    });
  }catch(e){console.error('Load script history error:',e);}
}

function loadSavedScript(idx){
  if(idx===''||idx===undefined)return;
  const s=scriptHistory[parseInt(idx)];
  if(!s)return;
  document.getElementById('hf-script').value=s.content;
  const scenes=parseScenes(s.content);
  document.getElementById('hf-scenes-parsed').innerHTML=`<span style="color:var(--green)">${scenes.length} scenes loaded from "${esc(s.name)}"</span>`;
  addLog(`Loaded script: ${s.name}`);
}

async function saveCurrentScript(){
  const content=document.getElementById('hf-script').value.trim();
  if(!content){alert('No script to save');return;}
  const name=prompt('Name for this script:',content.substring(0,40).replace(/\n/g,' ')+'...');
  if(!name)return;
  try{
    const r=await fetch('/api/higgsfield/script-history',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name,content})
    });
    const d=await r.json();
    if(d.ok){addLog('Script saved: '+name);loadScriptHistory();}
    else addLog('Save error: '+(d.error||''));
  }catch(e){addLog('Save error: '+e.message);}
}

async function deleteSavedScript(){
  const sel=document.getElementById('hf-script-history');
  const idx=parseInt(sel.value);
  if(isNaN(idx)){alert('Select a script first');return;}
  const s=scriptHistory[idx];
  if(!confirm(`Delete saved script "${s.name}"?`))return;
  try{
    const r=await fetch('/api/higgsfield/script-history',{
      method:'DELETE',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({index:idx})
    });
    const d=await r.json();
    if(d.ok){addLog('Script deleted');loadScriptHistory();}
  }catch(e){addLog('Delete error: '+e.message);}
}

async function hfLogin(){
  const btn=document.getElementById('hf-login-btn');
  btn.disabled=true;btn.textContent='⏳ Opening browser...';
  addLog('Opening Higgsfield login browser...');
  try{
    const r=await fetch('/api/higgsfield/login',{method:'POST'});
    const d=await r.json();
    if(d.ok){btn.textContent='✅ Logged in!';addLog('Login successful!');}
    else{btn.textContent='❌ '+( d.error||'Failed');addLog('Login failed: '+(d.error||''));}
    setTimeout(()=>{btn.disabled=false;btn.textContent='🔑 Login to Higgsfield';},3000);
    pollStatus();
  }catch(e){btn.textContent='❌ Error';btn.disabled=false;addLog('Login error: '+e.message);}
}

async function handleUpload(input){
  const file=input.files[0];
  if(!file)return;
  addLog('Uploading reference image: '+file.name);
  const reader=new FileReader();
  reader.onload=async(e)=>{
    const base64=e.target.result.split(',')[1];
    try{
      const r=await fetch('/api/higgsfield/upload-image',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({image_data:base64,filename:file.name})
      });
      const d=await r.json();
      if(d.ok){
        refImagePath=d.path;
        document.getElementById('hf-upload-preview').innerHTML=
          `<img src="${e.target.result}" style="max-height:100px;border-radius:6px;margin-bottom:4px"><div style="font-size:.66rem;color:var(--green)">✓ ${esc(file.name)}</div>`;
        addLog('Reference image uploaded: '+file.name);
      }else{addLog('Upload error: '+(d.error||''));}
    }catch(err){addLog('Upload error: '+err.message);}
  };
  reader.readAsDataURL(file);
}

function parseScenes(text){
  return text.split(/\n\s*\n/).map(s=>s.trim()).filter(Boolean);
}

async function autoGenScenes(){
  const script=document.getElementById('hf-script').value.trim();
  if(!script){alert('Paste an ad script first');return;}
  addLog('Auto-generating scenes from script...');
  const btn=event.target;btn.disabled=true;btn.textContent='⏳ Generating...';
  try{
    const r=await fetch('/api/chat',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({message:`Break this ad script into individual scenes for image generation. For each scene, provide a brief visual description of what the start frame and end frame should look like. Format: one scene per paragraph, separated by blank lines.\n\nAd script:\n${script}`,history:[]})
    });
    const reader=r.body.getReader();const dec=new TextDecoder();
    let buf='',result='';
    while(true){
      const{done,value}=await reader.read();if(done)break;
      buf+=dec.decode(value,{stream:true});
      const lines=buf.split('\n');buf=lines.pop();
      for(const line of lines){
        if(!line.startsWith('data: '))continue;
        try{const ev=JSON.parse(line.slice(6));
          if(ev.type==='direct_response'||ev.type==='synthesis')result=ev.content||'';
        }catch(e){}
      }
    }
    if(result){
      document.getElementById('hf-script').value=result;
      const scenes=parseScenes(result);
      document.getElementById('hf-scenes-parsed').innerHTML=`<span style="color:var(--green)">✓ ${scenes.length} scenes detected</span>`;
      addLog('Generated '+scenes.length+' scenes');
      // Auto-save the generated script
      try{
        await fetch('/api/higgsfield/script-history',{
          method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({name:'Auto-gen '+new Date().toLocaleDateString()+' ('+scenes.length+' scenes)',content:result})
        });
        loadScriptHistory();
      }catch(e){}
    }
  }catch(e){addLog('Scene generation error: '+e.message);}
  btn.disabled=false;btn.textContent='✨ Auto-Generate Scenes from Script';
}

async function hfStart(){
  const script=document.getElementById('hf-script').value.trim();
  if(!script){alert('Add an ad script with scenes first');return;}
  if(!refImagePath){alert('Upload a reference bathroom image first');return;}
  const scenes=parseScenes(script);
  const ipf=parseInt(document.getElementById('hf-ipf').value)||30;

  // Check for existing progress
  try{
    const prog=await fetch('/api/higgsfield/progress').then(r=>r.json());
    if(prog.total_images>0){
      const resume=confirm(`Found ${prog.total_images} images from a previous run.\n\nClick OK to resume (skip completed scenes).\nClick Cancel to start fresh.`);
      if(!resume){
        // Clear progress file by starting with empty state
        addLog('Starting fresh — previous progress will be overwritten');
      }else{
        addLog(`Resuming — ${prog.total_images} images already completed`);
      }
    }
  }catch(e){}

  addLog(`Starting generation: ${scenes.length} scenes, ${ipf} images/frame`);
  try{
    const r=await fetch('/api/higgsfield/start',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({scenes,ref_image:refImagePath,images_per_frame:ipf})
    });
    const d=await r.json();
    if(d.ok){
      document.getElementById('hf-start-btn').disabled=true;
      document.getElementById('hf-stop-btn').style.display='block';
      addLog(d.message);
    }else{addLog('Error: '+(d.error||''));alert(d.error||'Error starting generation');}
  }catch(e){addLog('Start error: '+e.message);}
}

async function hfStop(){
  addLog('Stopping generation...');
  await fetch('/api/higgsfield/stop',{method:'POST'});
  document.getElementById('hf-stop-btn').style.display='none';
  document.getElementById('hf-start-btn').disabled=false;
}

let likedFramePath='';
async function handleLikedUpload(input){
  const file=input.files[0];
  if(!file)return;
  const sceneIdx=parseInt(document.getElementById('hf-end-scene').value)||1;
  addLog('Uploading liked start frame for scene '+sceneIdx);
  const reader=new FileReader();
  reader.onload=async(e)=>{
    const base64=e.target.result.split(',')[1];
    try{
      const r=await fetch('/api/higgsfield/upload-liked-frame',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({image_data:base64,filename:file.name,scene_idx:sceneIdx})
      });
      const d=await r.json();
      if(d.ok){
        likedFramePath=d.path;
        document.getElementById('hf-liked-preview').innerHTML=
          `<img src="${e.target.result}" style="max-height:80px;border-radius:6px;margin-bottom:4px"><div style="font-size:.62rem;color:var(--green)">⭐ ${esc(file.name)}</div>`;
        document.getElementById('hf-endframe-btn').disabled=false;
        addLog('Liked frame uploaded: '+file.name);
      }else{addLog('Upload error: '+(d.error||''));}
    }catch(err){addLog('Upload error: '+err.message);}
  };
  reader.readAsDataURL(file);
}

async function hfStartEndFrames(){
  if(!likedFramePath){alert('Upload a liked start frame first');return;}
  const sceneIdx=parseInt(document.getElementById('hf-end-scene').value)||1;
  const sceneDesc=document.getElementById('hf-end-desc').value.trim()||'Scene '+sceneIdx;
  const ipf=parseInt(document.getElementById('hf-ipf').value)||30;
  addLog(`Starting end frame generation for scene ${sceneIdx}...`);
  try{
    const r=await fetch('/api/higgsfield/generate-end-frames',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({scene_idx:sceneIdx,scene_desc:sceneDesc,liked_frame_path:likedFramePath,images_per_frame:ipf})
    });
    const d=await r.json();
    if(d.ok){addLog(d.message);}
    else{addLog('Error: '+(d.error||''));}
  }catch(e){addLog('Error: '+e.message);}
}

async function hfAnalyzeImages(){
  const btn=event.target;btn.disabled=true;btn.textContent='🧠 Analyzing...';
  addLog('Running AI vision analysis on images...');
  try{
    const r=await fetch('/api/higgsfield/analyze',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({count:10})
    });
    const d=await r.json();
    if(d.ok){
      addLog(`Analyzed ${d.analyzed} images — avg quality: ${d.avg_score}/10`);
      if(d.feedback){
        document.getElementById('hf-feedback-section').style.display='block';
        document.getElementById('hf-feedback-text').innerText=d.feedback;
        document.getElementById('hf-avg-score').textContent=`(avg: ${d.avg_score}/10)`;
      }
      // Show per-image results
      for(const a of d.analyses||[]){
        const v=a.verdict==='keep'?'✓':'✕';
        const issues=(a.realism_issues||[]).slice(0,2).join(', ');
        addLog(`  ${v} S${a.scene} ${a.frame} #${a.index}: ${a.score}/10${issues?' — '+issues:''}`);
      }
      document.getElementById('hf-image-grid').dataset.hash='';pollStatus();
    }else{addLog('Analysis: '+(d.error||'No images to analyze'));}
  }catch(e){addLog('Analysis error: '+e.message);}
  btn.disabled=false;btn.textContent='🧠 Analyze';
}

async function hfRefreshImages(){
  const btn=event.target;btn.disabled=true;btn.textContent='🔄 Refreshing...';
  addLog('Scraping image URLs from Higgsfield...');
  try{
    const r=await fetch('/api/higgsfield/refresh-images',{method:'POST'});
    const d=await r.json();
    if(d.ok){addLog(`Refreshed ${d.updated} URLs, cached ${d.cached||0} images locally (${d.total_scraped} on page)`);
      document.getElementById('hf-image-grid').dataset.hash='';pollStatus();}
    else addLog('Refresh failed: '+(d.error||'Unknown'));
  }catch(e){addLog('Refresh error: '+e.message);}
  btn.disabled=false;btn.textContent='🔄 Refresh Images';
}

async function hfDownloadImages(){
  addLog('Downloading all images from Higgsfield...');
  const btn=event.target;btn.disabled=true;btn.textContent='⬇ Downloading...';
  try{
    const r=await fetch('/api/higgsfield/download-images',{method:'POST'});
    const d=await r.json();
    if(d.error){addLog('Download error: '+d.error);}
    else{addLog(`Downloaded ${d.downloaded} images (${d.errors} errors)`);
      document.getElementById('hf-image-grid').dataset.count=0;pollStatus();}
  }catch(e){addLog('Download error: '+e.message);}
  btn.disabled=false;btn.textContent='⬇ Download All Images';
}

// Drag and drop for liked frame
const likedDrop=document.getElementById('hf-liked-drop');
likedDrop.addEventListener('dragover',(e)=>{e.preventDefault();likedDrop.classList.add('dragover');});
likedDrop.addEventListener('dragleave',()=>likedDrop.classList.remove('dragover'));
likedDrop.addEventListener('drop',(e)=>{
  e.preventDefault();likedDrop.classList.remove('dragover');
  if(e.dataTransfer.files[0]){document.getElementById('hf-liked-file').files=e.dataTransfer.files;handleLikedUpload(document.getElementById('hf-liked-file'));}
});

// Drag and drop
const drop=document.getElementById('hf-drop');
drop.addEventListener('dragover',(e)=>{e.preventDefault();drop.classList.add('dragover');});
drop.addEventListener('dragleave',()=>drop.classList.remove('dragover'));
drop.addEventListener('drop',(e)=>{
  e.preventDefault();drop.classList.remove('dragover');
  if(e.dataTransfer.files[0]){document.getElementById('hf-file').files=e.dataTransfer.files;handleUpload(document.getElementById('hf-file'));}
});

// ── Static Ad Campaign Functions ──
let adCampaigns=[];
let selectedCampaigns=new Set();

async function loadAdCampaigns(){
  const cat=document.getElementById('hf-ad-category').value;
  try{
    const r=await fetch('/api/higgsfield/ad-campaigns?category='+encodeURIComponent(cat)).then(r=>r.json());
    adCampaigns=r.campaigns||[];
    const catSel=document.getElementById('hf-ad-category');
    if(catSel.options.length<=1){
      (r.categories||[]).forEach(c=>{
        const o=document.createElement('option');o.value=c;o.textContent=c.charAt(0).toUpperCase()+c.slice(1);catSel.appendChild(o);
      });
    }
    renderAdList();
  }catch(e){console.error('Load campaigns:',e);}
}

function renderAdList(){
  const el=document.getElementById('hf-ad-list');
  while(el.firstChild)el.removeChild(el.firstChild);
  if(!adCampaigns.length){
    const msg=document.createElement('div');
    msg.style.cssText='font-size:.65rem;color:var(--muted);padding:8px';
    msg.textContent='No campaigns found';
    el.appendChild(msg);
    return;
  }
  adCampaigns.forEach(c=>{
    const sel=selectedCampaigns.has(c.id);
    const palColor={'forest_green':'#22d47a','deep_navy':'#4a7acf','stone_gray':'#9a9a9a','sunset_amber':'#fb923c','charcoal':'#777'}[c.palette]||'var(--cyan)';
    const row=document.createElement('div');
    row.style.cssText='display:flex;gap:8px;align-items:center;padding:6px 8px;border-radius:6px;cursor:pointer;border:1px solid '+(sel?'var(--cyan)':'var(--border)')+';background:'+(sel?'rgba(6,182,212,.08)':'transparent')+';transition:all .15s';
    row.onclick=()=>{toggleCampaign(c.id);};
    const chk=document.createElement('div');
    chk.style.cssText='width:16px;height:16px;border-radius:4px;border:1.5px solid '+(sel?'var(--cyan)':'var(--border)')+';background:'+(sel?'var(--cyan)':'transparent')+';display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:10px;color:#fff';
    chk.textContent=sel?'\u2713':'';
    const info=document.createElement('div');
    info.style.cssText='flex:1;min-width:0';
    const title=document.createElement('div');
    title.style.cssText='font-size:.68rem;font-weight:600;color:var(--hi);white-space:nowrap;overflow:hidden;text-overflow:ellipsis';
    title.textContent=c.concept;
    const sub=document.createElement('div');
    sub.style.cssText='font-size:.58rem;color:var(--muted)';
    sub.textContent=c.category+' \u00B7 "'+c.headline+'" \u00B7 '+c.cta;
    info.appendChild(title);info.appendChild(sub);
    const dot=document.createElement('div');
    dot.style.cssText='width:8px;height:8px;border-radius:50%;background:'+palColor+';flex-shrink:0';
    dot.title=c.palette;
    row.appendChild(chk);row.appendChild(info);row.appendChild(dot);
    el.appendChild(row);
  });
}

function toggleCampaign(id){
  if(selectedCampaigns.has(id))selectedCampaigns.delete(id);
  else selectedCampaigns.add(id);
  renderAdList();
}

function selectAllCampaigns(){
  if(selectedCampaigns.size===adCampaigns.length){selectedCampaigns.clear();}
  else{adCampaigns.forEach(c=>selectedCampaigns.add(c.id));}
  renderAdList();
}

async function hfStartStaticAds(){
  if(!refImagePath){alert('Upload a reference image first!');return;}
  const ids=selectedCampaigns.size>0?[...selectedCampaigns]:['all'];
  const count=parseInt(document.getElementById('hf-ad-count').value)||4;
  const btn=document.getElementById('hf-ad-start-btn');
  btn.disabled=true;btn.textContent='\u23F3 Generating...';
  addLog('Starting static ad generation: '+(ids.length===1&&ids[0]==='all'?adCampaigns.length+' campaigns':ids.length+' selected')+' x '+count+' images');
  try{
    const r=await fetch('/api/higgsfield/start-static-ads',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({campaign_ids:ids,ref_image:refImagePath,images_per_campaign:count})
    });
    const d=await r.json();
    if(d.ok){addLog(d.message);
      document.getElementById('hf-stop-btn').style.display='inline-flex';
      setTimeout(loadStaticAdResults,5000);
    }else{addLog('Error: '+(d.error||''));btn.disabled=false;btn.textContent='\uD83D\uDE80 Generate Static Ads';}
  }catch(e){addLog('Error: '+e.message);btn.disabled=false;btn.textContent='\uD83D\uDE80 Generate Static Ads';}
}

async function loadStaticAdResults(){
  try{
    const r=await fetch('/api/higgsfield/static-ads').then(r=>r.json());
    const el=document.getElementById('hf-ad-results');
    while(el.firstChild)el.removeChild(el.firstChild);
    if(r.count>0){
      // Filter out palette previews
      const adImages=r.images.filter(i=>!i.name.startsWith('palette_preview'));
      const header=document.createElement('div');
      header.style.cssText='font-size:.65rem;color:var(--green);margin-bottom:6px';
      header.textContent='\u2705 '+adImages.length+' ad images with CTA overlays';
      el.appendChild(header);
      const dir=document.createElement('div');
      dir.style.cssText='font-size:.6rem;color:var(--muted);margin-bottom:6px';
      dir.textContent='\uD83D\uDCC1 '+r.dir;
      el.appendChild(dir);
      // 2-column grid with taller cards showing the CTA at bottom
      const grid=document.createElement('div');
      grid.style.cssText='display:grid;grid-template-columns:repeat(2,1fr);gap:6px;max-height:400px;overflow-y:auto;scrollbar-width:thin;scrollbar-color:var(--border) transparent;padding:2px';
      adImages.slice(-20).forEach(img=>{
        const card=document.createElement('div');
        card.style.cssText='position:relative;border-radius:6px;overflow:hidden;cursor:pointer;border:1px solid var(--border);transition:transform .15s';
        card.onmouseenter=()=>card.style.transform='scale(1.02)';
        card.onmouseleave=()=>card.style.transform='';
        const imgEl=document.createElement('img');
        imgEl.src='/api/higgsfield/static-ad-image?path='+encodeURIComponent(img.path);
        imgEl.style.cssText='width:100%;aspect-ratio:9/16;object-fit:cover;display:block';
        imgEl.title=img.name+' (click to view full size)';
        card.onclick=()=>{
          // Full-screen preview modal
          const modal=document.createElement('div');
          modal.style.cssText='position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.92);z-index:9999;display:flex;align-items:center;justify-content:center;cursor:pointer;backdrop-filter:blur(8px)';
          const fullImg=document.createElement('img');
          fullImg.src=imgEl.src;
          fullImg.style.cssText='max-height:90vh;max-width:90vw;border-radius:12px;box-shadow:0 20px 60px rgba(0,0,0,.6)';
          const label=document.createElement('div');
          label.style.cssText='position:fixed;bottom:20px;left:50%;transform:translateX(-50%);color:#fff;font-size:.75rem;background:rgba(0,0,0,.7);padding:6px 16px;border-radius:20px;font-family:JetBrains Mono,monospace';
          label.textContent=img.name+'.png — Click anywhere to close';
          modal.appendChild(fullImg);
          modal.appendChild(label);
          modal.onclick=()=>document.body.removeChild(modal);
          document.body.appendChild(modal);
        };
        // Campaign label
        const tag=document.createElement('div');
        tag.style.cssText='position:absolute;top:4px;left:4px;background:rgba(6,182,212,.75);color:#fff;font-size:.5rem;padding:2px 6px;border-radius:8px;font-family:JetBrains Mono,monospace;backdrop-filter:blur(4px)';
        tag.textContent=img.campaign.replace(/_/g,' ');
        card.appendChild(imgEl);
        card.appendChild(tag);
        grid.appendChild(card);
      });
      el.appendChild(grid);
    }
    const status=await fetch('/api/higgsfield/status').then(r=>r.json());
    if(status.state==='generating'||status.state==='waiting_queue'){
      setTimeout(loadStaticAdResults,8000);
    }else{
      const btn=document.getElementById('hf-ad-start-btn');
      btn.disabled=false;btn.textContent='\uD83D\uDE80 Generate Static Ads';
    }
  }catch(e){console.error('Load static ads:',e);}
}

loadAdCampaigns();

// Start polling + load script history
pollStatus();
pollTimer=setInterval(pollStatus,3000);
loadScriptHistory();
</script></body></html>"""

SETUP = """<!DOCTYPE html>
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'DM Sans',sans-serif;background:#060a0f;color:#b8c8d8;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
.w{max-width:520px;width:100%}
.logo{text-align:center;margin-bottom:36px}
.lm{width:52px;height:52px;background:linear-gradient(135deg,#06b6d4,#8b5cf6);border-radius:14px;display:inline-flex;align-items:center;justify-content:center;font-size:1.4rem;margin-bottom:10px;box-shadow:0 0 40px rgba(6,182,212,.25)}
h1{font-size:1.5rem;font-weight:600;color:#e8f0f8}
.sub{font-size:.82rem;color:#3a5a72;margin-top:4px}
.card{background:#0c1520;border:1px solid #1a2a3a;border-radius:18px;padding:28px;margin-bottom:14px}
.ct{font-size:.65rem;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:#06b6d4;margin-bottom:18px;display:flex;align-items:center;gap:8px}
.ct::after{content:'';flex:1;height:1px;background:#1a2a3a}
.field{margin-bottom:12px}
.field label{display:flex;justify-content:space-between;align-items:center;font-size:.76rem;color:#7a9ab8;font-weight:500;margin-bottom:6px}
.tag{font-size:.6rem;padding:2px 7px;border-radius:4px;font-family:'JetBrains Mono',monospace}
.cheap{background:#062a10;color:#22d47a;border:1px solid #0a4020}
.best{background:#060a25;color:#818cf8;border:1px solid #1a1a60}
input{width:100%;background:#060f18;border:1px solid #1a2a3a;border-radius:10px;padding:10px 13px;color:#b8c8d8;font-size:.82rem;font-family:'JetBrains Mono',monospace;outline:none;transition:border-color .2s}
input:focus{border-color:#06b6d4}
input::placeholder{color:#1a3050}
.btn{width:100%;margin-top:4px;padding:13px;background:linear-gradient(135deg,#06b6d4,#8b5cf6);color:#fff;border:none;border-radius:12px;font-size:.92rem;font-weight:600;cursor:pointer;font-family:'DM Sans',sans-serif;transition:opacity .2s}
.btn:hover{opacity:.9}.btn:disabled{opacity:.4;cursor:not-allowed}
.err{color:#f87171;font-size:.76rem;margin-top:10px;text-align:center;display:none;background:rgba(248,113,113,.08);border:1px solid rgba(248,113,113,.2);padding:8px;border-radius:8px}
.note{font-size:.72rem;color:#2a4a60;line-height:2;text-align:center;margin-top:16px}
.note a{color:#06b6d4;text-decoration:none}
</style></head><body>
<div class="w">
<div class="logo"><div class="lm">⚡</div><h1>Nexus Agent</h1><p class="sub">Multi-agent · Memory · Telegram · Self-improving</p></div>
<div class="card"><div class="ct">Orchestrator</div>
  <div class="field"><label>Anthropic Key<span class="tag best">Claude Sonnet — best planner</span></label><input type="password" id="ak" placeholder="sk-ant-..."></div>
</div>
<div class="card"><div class="ct">Sub-Agents</div>
  <div class="field"><label>Moonshot Key<span class="tag cheap">Kimi — cheapest ✓</span></label><input type="password" id="mk" placeholder="sk-..."></div>
  <div class="field"><label>Gemini Key<span class="tag cheap">Gemini Flash — cheap</span></label><input type="password" id="gk" placeholder="AIza..."></div>
  <div class="field"><label>OpenAI Key<span class="tag best">GPT-4o Mini</span></label><input type="password" id="ok2" placeholder="sk-..."></div>
</div>
<div class="card"><div class="ct">Telegram</div>
  <div class="field"><label>Bot Token<span class="tag best">from @BotFather</span></label><input type="password" id="tt" placeholder="123456:ABC-..."></div>
  <div class="field"><label>Chat ID<span class="tag cheap">from @userinfobot</span></label><input type="text" id="tc" placeholder="123456789"></div>
</div>
<button class="btn" onclick="go()" id="btn">Launch Nexus →</button>
<div class="err" id="err"></div>
<div class="note">Keys saved locally · Need at least one key<br>
Kimi: <a href="https://platform.moonshot.cn" target="_blank">platform.moonshot.cn</a> · Claude: <a href="https://console.anthropic.com" target="_blank">console.anthropic.com</a></div>
</div>
<script>
async function go(){
  const g=id=>document.getElementById(id).value.trim();
  const body={anthropic_key:g('ak'),moonshot_key:g('mk'),gemini_key:g('gk'),openai_key:g('ok2'),telegram_token:g('tt'),telegram_chat_ids:g('tc')?[g('tc')]:[]};
  if(!Object.entries(body).some(([k,v])=>k.endsWith('_key')&&v)){document.getElementById('err').textContent='Add at least one API key';document.getElementById('err').style.display='block';return;}
  const btn=document.getElementById('btn');btn.disabled=true;btn.textContent='Launching…';
  const r=await fetch('/api/save-config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const d=await r.json();
  if(d.ok)window.location.href='/';
  else{document.getElementById('err').textContent=d.error||'Error';document.getElementById('err').style.display='block';btn.disabled=false;btn.textContent='Launch Nexus →';}
}
document.querySelectorAll('input').forEach(i=>i.addEventListener('keydown',e=>{if(e.key==='Enter')go();}));
</script></body></html>"""

CHAT = r"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nexus</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#060a0f;--s1:#0c1520;--border:#1a2a3a;--text:#b8c8d8;--hi:#e8f0f8;--muted:#3a5a72;--cyan:#06b6d4;--purple:#8b5cf6;--green:#22d47a;--orange:#fb923c;--red:#ef4444;--yellow:#fbbf24}
body{font-family:'DM Sans',sans-serif;background:var(--bg);color:var(--text);height:100vh;display:flex;flex-direction:column;overflow:hidden}
header{height:48px;border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;padding:0 16px;background:var(--s1);flex-shrink:0;gap:8px}
.brand{display:flex;align-items:center;gap:8px;flex-shrink:0}
.bi{width:22px;height:22px;background:linear-gradient(135deg,var(--cyan),var(--purple));border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:.65rem}
.bn{font-size:.85rem;font-weight:600;color:var(--hi)}
.hbtns{display:flex;gap:5px;align-items:center;flex-wrap:nowrap;overflow-x:auto}
.pb{font-size:.62rem;padding:3px 8px;border-radius:5px;border:1px solid var(--border);background:transparent;color:var(--muted);cursor:pointer;font-family:'DM Sans',sans-serif;transition:all .15s;white-space:nowrap;flex-shrink:0}
.pb:hover{color:var(--hi);border-color:#2a4060}
.pb.on{background:rgba(6,182,212,.1);color:var(--cyan);border-color:rgba(6,182,212,.3)}
#ms{font-size:.62rem;font-family:'JetBrains Mono',monospace;color:var(--green);padding:3px 8px;border-radius:5px;background:rgba(34,212,122,.08);border:1px solid rgba(34,212,122,.2);white-space:nowrap;flex-shrink:0}
#costd{font-size:.62rem;font-family:'JetBrains Mono',monospace;color:var(--yellow);padding:3px 8px;border-radius:5px;background:rgba(251,191,36,.06);border:1px solid rgba(251,191,36,.15);white-space:nowrap;flex-shrink:0}
.layout{flex:1;display:flex;overflow:hidden}
.cc{flex:1;display:flex;flex-direction:column;overflow:hidden;min-width:0}
#feed{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px;scrollbar-width:thin;scrollbar-color:var(--border) transparent}
.mu{align-self:flex-end;max-width:72%}
.ma{align-self:flex-start;max-width:90%}
.meta{font-size:.62rem;color:var(--muted);margin-bottom:3px;display:flex;align-items:center;gap:4px;flex-wrap:wrap}
.mtag{font-family:'JetBrains Mono',monospace;padding:1px 6px;border-radius:4px;font-size:.58rem;border:1px solid var(--border);background:var(--s1)}
.ctag{background:rgba(251,191,36,.1);color:var(--yellow);border-color:rgba(251,191,36,.25)}
.stag{background:rgba(139,92,246,.12);color:#a78bfa;border-color:rgba(139,92,246,.3)}
.tgtag{background:rgba(34,212,122,.1);color:var(--green);border-color:rgba(34,212,122,.25)}
.bub{padding:11px 14px;border-radius:13px;font-size:.85rem;line-height:1.7;word-break:break-word}
.mu .bub{background:linear-gradient(135deg,rgba(6,182,212,.16),rgba(139,92,246,.16));border:1px solid rgba(6,182,212,.22);border-bottom-right-radius:3px;color:var(--hi)}
.ma .bub{background:var(--s1);border:1px solid var(--border);border-bottom-left-radius:3px}
.bub pre{background:#040810;border:1px solid var(--border);padding:10px 13px;border-radius:9px;overflow-x:auto;margin:8px 0;font-size:.76em;font-family:'JetBrains Mono',monospace;line-height:1.5}
.bub code{font-family:'JetBrains Mono',monospace;font-size:.78em;background:#040810;padding:1px 5px;border-radius:4px}
.bub strong{color:var(--hi)}
.thinking{color:var(--muted);font-style:italic;font-size:.82rem}
.plan-card{background:linear-gradient(135deg,rgba(6,182,212,.05),rgba(139,92,246,.05));border:1px solid rgba(6,182,212,.18);border-radius:11px;padding:12px 14px;margin:3px 0}
.pct{font-size:.64rem;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:var(--cyan);margin-bottom:4px}
.pcr{font-size:.78rem;color:#7a9ab8;line-height:1.5}
/* Agent col */
.ac-col{width:230px;border-left:1px solid var(--border);background:var(--s1);display:flex;flex-direction:column;overflow:hidden;flex-shrink:0;transition:width .25s,opacity .25s}
.ac-col.closed{width:0;opacity:0;pointer-events:none}
/* Activity col */
.av-col{width:260px;border-left:1px solid var(--border);background:var(--s1);display:flex;flex-direction:column;overflow:hidden;flex-shrink:0;transition:width .25s,opacity .25s}
.av-col.closed{width:0;opacity:0;pointer-events:none}
.ph{padding:8px 12px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;flex-shrink:0}
.ph-l{font-size:.6rem;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
.ph-r{font-size:.65rem;color:var(--cyan);font-family:'JetBrains Mono',monospace}
.pb-inner{flex:1;overflow-y:auto;padding:8px;display:flex;flex-direction:column;gap:6px;scrollbar-width:thin;scrollbar-color:var(--border) transparent}
/* Agent cards */
.ac{background:var(--bg);border:1px solid var(--border);border-radius:9px;padding:9px 10px;transition:border-color .3s,box-shadow .3s}
.ac.running{border-color:rgba(6,182,212,.4);box-shadow:0 0 12px rgba(6,182,212,.1);background:linear-gradient(135deg,rgba(6,182,212,.03),var(--bg))}
.ac.done{border-color:rgba(34,212,122,.3)}
.ac.error{border-color:rgba(239,68,68,.3)}
.act-top{display:flex;align-items:center;gap:5px;margin-bottom:4px}
.dot{width:5px;height:5px;border-radius:50%;flex-shrink:0}
.dot.pending{background:var(--muted)}.dot.running{background:var(--cyan);animation:glow 1s infinite}.dot.done{background:var(--green)}.dot.error{background:var(--red)}
.ag-spinner{width:12px;height:12px;border:2px solid var(--border);border-top-color:var(--cyan);border-radius:50%;animation:spin .7s linear infinite;flex-shrink:0}
.ag-elapsed{font-size:.56rem;color:var(--cyan);font-family:'JetBrains Mono',monospace;animation:pulse 2s ease-in-out infinite}
.ag-current-step{font-size:.64rem;color:var(--cyan);font-family:'JetBrains Mono',monospace;padding:4px 7px;background:rgba(6,182,212,.06);border:1px solid rgba(6,182,212,.15);border-radius:5px;margin:4px 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ag-done-badge{font-size:.58rem;color:var(--green);font-family:'JetBrains Mono',monospace;display:flex;align-items:center;gap:4px}
@keyframes glow{0%,100%{box-shadow:0 0 0 0 rgba(6,182,212,.5)}50%{box-shadow:0 0 0 5px rgba(6,182,212,0)}}
@keyframes spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}
@keyframes pulse{0%,100%{opacity:.6}50%{opacity:1}}
.acid{font-family:'JetBrains Mono',monospace;font-size:.66rem;color:var(--cyan);font-weight:500}
.acml{font-size:.58rem;color:var(--muted);margin-left:auto;font-family:'JetBrains Mono',monospace}
.actype{display:inline-block;font-size:.57rem;padding:1px 6px;border-radius:4px;margin-bottom:4px;font-weight:600}
.research{background:#061828;color:#38bdf8;border:1px solid #0e3050}
.analysis{background:#141028;color:#c084fc;border:1px solid #2a1850}
.coding{background:#080f20;color:#818cf8;border:1px solid #181830}
.creative,.ad_creative,.image_prompt,.cta{background:#1a0a18;color:#f472b6;border:1px solid #3a1030}
.summarize,.general{background:#0a1810;color:#4ade80;border:1px solid #1a3020}
.tool_research,.self_improve{background:#1a1200;color:#fbbf24;border:1px solid #2a2000}
.actask{font-size:.7rem;color:#6a8aa0;line-height:1.4;margin-bottom:4px}
.acsteps{font-size:.62rem;color:#2a4a5a;font-family:'JetBrains Mono',monospace;display:flex;flex-direction:column;gap:1px}
.acstep{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
/* Activity items */
.avi{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:8px 10px;font-size:.7rem}
.avi.search{border-left:2px solid var(--cyan)}
.avi.fetch{border-left:2px solid var(--purple)}
.avi.code{border-left:2px solid var(--yellow)}
.avi.tool{border-left:2px solid var(--orange)}
.avi.result{border-left:2px solid var(--green)}
.avi.think{border-left:2px solid var(--muted)}
.avi-label{font-size:.57rem;font-weight:600;letter-spacing:.08em;text-transform:uppercase;margin-bottom:3px}
.search .avi-label{color:var(--cyan)}.fetch .avi-label{color:#a78bfa}.code .avi-label{color:var(--yellow)}.tool .avi-label{color:var(--orange)}.result .avi-label{color:var(--green)}.think .avi-label{color:var(--muted)}
.avi-body{color:#5a7a8a;line-height:1.4;overflow:hidden;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical}
.avi-code{background:#040810;border-radius:5px;padding:5px 7px;margin-top:4px;font-family:'JetBrains Mono',monospace;font-size:.63rem;color:#7a9ab8;overflow-x:auto;max-height:70px;overflow-y:auto;white-space:pre}
.avi-time{font-size:.57rem;color:#1a3a50;margin-top:3px;font-family:'JetBrains Mono',monospace}
/* Memory panel */
.mem-panel{position:fixed;right:0;top:48px;bottom:0;width:300px;background:var(--s1);border-left:1px solid var(--border);transform:translateX(100%);transition:transform .25s;z-index:50;display:flex;flex-direction:column}
.mem-panel.open{transform:translateX(0)}
.mph{padding:10px 14px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;flex-shrink:0}
.mph h2{font-size:.76rem;font-weight:600;color:var(--hi)}
.xb{background:none;border:none;color:var(--muted);cursor:pointer;font-size:.95rem;padding:2px 6px}.xb:hover{color:var(--hi)}
.mpb{flex:1;overflow-y:auto;padding:10px}
.mi{background:var(--bg);border:1px solid var(--border);border-radius:9px;padding:9px;margin-bottom:7px}
.miq{font-size:.7rem;color:var(--cyan);margin-bottom:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mia{font-size:.68rem;color:var(--muted);line-height:1.4;max-height:55px;overflow:hidden}
.mits{font-size:.58rem;color:#1a3a50;margin-top:3px;font-family:'JetBrains Mono',monospace}
#ir{padding:10px 16px;border-top:1px solid var(--border);background:var(--s1);display:flex;gap:7px;flex-shrink:0}
#inp{flex:1;background:var(--bg);border:1px solid var(--border);border-radius:11px;padding:10px 14px;color:var(--hi);font-size:.85rem;resize:none;outline:none;font-family:'DM Sans',sans-serif;transition:border-color .2s;max-height:100px}
#inp:focus{border-color:rgba(6,182,212,.4)}
#inp::placeholder{color:#1a3a50}
#send{background:linear-gradient(135deg,var(--cyan),var(--purple));color:#fff;border:none;border-radius:11px;padding:0 18px;cursor:pointer;font-size:.85rem;font-weight:600;min-width:68px;transition:opacity .2s}
#send:disabled{opacity:.35;cursor:not-allowed}
</style></head><body>
<header>
  <div class="brand"><div class="bi">⚡</div><span class="bn">Nexus</span><a href="/dashboard/" style="text-decoration:none;margin-left:12px;font-size:.72rem;padding:4px 12px;border-radius:6px;background:linear-gradient(135deg,rgba(34,212,122,.15),rgba(6,182,212,.15));color:#22d47a;border:1px solid rgba(34,212,122,.35);font-weight:600;font-family:'DM Sans',sans-serif;transition:all .2s">📊 CRM Dashboard</a><a href="/higgsfield" style="text-decoration:none;margin-left:4px;font-size:.72rem;padding:4px 12px;border-radius:6px;background:rgba(139,92,246,.1);color:#a78bfa;border:1px solid rgba(139,92,246,.35);font-weight:600;font-family:'DM Sans',sans-serif;transition:all .2s">🖼 Higgsfield</a></div>
  <div class="hbtns">
    <div id="ms">…</div>
    <div id="costd">$0.0000</div><div id="task-costd" style="font-size:.62rem;font-family:'JetBrains Mono',monospace;color:var(--orange);padding:3px 8px;border-radius:5px;background:rgba(251,146,60,.06);border:1px solid rgba(251,146,60,.15);white-space:nowrap;flex-shrink:0"></div>
    <button class="pb on" id="atog" onclick="togCol('ac-col','atog')">⊞ Agents</button>
    <button class="pb on" id="vtog" onclick="togCol('av-col','vtog')">👁 Activity</button>
    <button class="pb" onclick="openMem()">🧠 Memory</button>
    <button class="pb" onclick="improve()">🔬 Improve</button>
    <button class="pb" onclick="openGHL()" id="ghlbtn">📋 GHL</button>
    <button class="pb" onclick="changeKeys()">🔑 Keys</button>
  </div>
</header>
<div class="layout">
  <div class="cc">
    <div id="feed"></div>
    <div id="ir">
      <textarea id="inp" rows="1" placeholder="Ask anything — complex tasks spawn parallel sub-agents…" onkeydown="onk(event)" oninput="rsz(this)"></textarea>
      <button id="send" onclick="go()">Send</button>
    </div>
  </div>
  <div class="ac-col" id="ac-col">
    <div class="ph"><span class="ph-l">Sub-Agents</span><div style="display:flex;gap:6px;align-items:center"><span id="ag-total-cost" style="font-size:.6rem;color:var(--yellow);font-family:'JetBrains Mono',monospace"></span><span class="ph-r" id="acc">idle</span></div></div>
    <div class="pb-inner" id="acl"></div>
  </div>
  <div class="av-col" id="av-col">
    <div class="ph"><span class="ph-l">Live Activity</span><span class="ph-r" id="avcnt">0 actions</span></div>
    <div class="pb-inner" id="avl"></div>
  </div>
</div>
<div class="mem-panel" id="memp">
  <div class="mph"><h2>🧠 Memory</h2><button class="xb" onclick="closeMem()">✕</button></div>
  <div class="mpb" id="mpb">Loading…</div>
</div>
<script>
let hist=[],busy=false,totalCost=0,taskCost=0,actCnt=0;
const ags={};
let curBub=null,curMeta=null,totalAgents=0,doneAgents=0;

function togCol(id,btnId){const c=document.getElementById(id),b=document.getElementById(btnId);const cl=c.classList.toggle('closed');b.classList.toggle('on',!cl);}
function rsz(el){el.style.height='auto';el.style.height=Math.min(el.scrollHeight,100)+'px';}
function onk(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();go();}}
function esc(t){return String(t).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function md(t){return esc(t).replace(/```(\w*)?\n?([\s\S]*?)```/g,'<pre><code>$2</code></pre>').replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>').replace(/^#{1,3} (.+)$/gm,'<strong>$1</strong>').replace(/\n/g,'<br>');}
function scrl(){document.getElementById('feed').scrollTop=99999;}
function ts(){return new Date().toLocaleTimeString('en',{hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'});}

function addCost(c,task){
  if(!c||isNaN(c))return;
  totalCost+=Number(c);
  if(task)taskCost=Number(task);
  document.getElementById('costd').textContent='$'+totalCost.toFixed(4);
  const td=document.getElementById('task-costd');
  if(td)td.textContent='task $'+taskCost.toFixed(4);
}

function logAct(type,label,body,code=''){
  actCnt++;document.getElementById('avcnt').textContent=actCnt+' actions';
  const list=document.getElementById('avl');
  const d=document.createElement('div');d.className='avi '+type;
  const codeEl=code?`<div class="avi-code">${esc(code.slice(0,300))}</div>`:'';
  d.innerHTML=`<div class="avi-label">${label}</div><div class="avi-body">${esc(body.slice(0,200))}</div>${codeEl}<div class="avi-time">${ts()}</div>`;
  list.insertBefore(d,list.firstChild);
  while(list.children.length>60)list.removeChild(list.lastChild);
}

async function loadStatus(){
  try{
    const s=await fetch('/api/status').then(r=>r.json());
    if(s.setup_needed){window.location.href='/';return;}
    const a=s.available_models||[];
    document.getElementById('ms').textContent='✓ '+(a.includes('claude-sonnet')?'Claude+Kimi':a.includes('kimi-8k')?'Kimi':a.includes('gemini-flash')?'Gemini':'Ready');
    if(s.telegram_enabled){const el=document.createElement('span');el.className='mtag tgtag';el.textContent='📱 TG';el.style.cssText='padding:3px 7px;border-radius:5px;font-size:.62rem;';document.querySelector('.hbtns').insertBefore(el,document.getElementById('ms'));}
  }catch(e){}
}

function startSSE(){
  const es=new EventSource('/api/events');
  es.onmessage=e=>{try{handleEv(JSON.parse(e.data));}catch(x){}};
  es.onerror=()=>{setTimeout(startSSE,3000);};
}

function handleEv(ev){
  switch(ev.type){
    case 'ping':case 'done': break;
    case 'task_start':
      if(ev.source==='telegram'){
        addUsrMsg(ev.message,'telegram');
        const[b,m]=addAIMsg();curBub=b;curMeta=m;
      }
      break;
    case 'status':
      if(curBub){
        const detail=ev.detail?`<br><span style="font-size:.74rem;color:var(--muted)">${esc(ev.detail)}</span>`:'';
        curBub.innerHTML=`<span class="thinking">${esc(ev.message||'')}${detail}</span>`;
      }
      break;
    case 'orchestrator_model':
      logAct('tool','ORCHESTRATOR','Using: '+(ev.model||''));
      break;
    case 'plan':
      totalAgents=ev.subtask_count||1;doneAgents=0;
      if(curBub)curBub.innerHTML=`<span class="thinking">⚡ Spawning ${ev.subtask_count} agents in parallel…</span>`;
      addPlanCard(ev.reason,ev.subtask_count,ev.subtasks||[]);
      clearAgs();updateProgress(0,ev.subtask_count);
      break;
    case 'agent_start':
      upsertAg({...ev,status:'running'});
      logAct('tool','AGENT','['+ev.agent_id+'] '+ev.task_type+' via '+(ev.model||''));
      break;
    case 'agent_step':{
      const ag=ags[ev.agent_id];
      if(ag){ag.steps=ag.steps||[];ag.steps.push(ev.step||'');upsertAg(ag);}
      const s=ev.step||'';
      if(s.includes('web_search')||s.includes('🔍'))logAct('search','WEB SEARCH',s.replace(/[🔧💭🔍]/g,'').trim());
      else if(s.includes('fetch_url'))logAct('fetch','FETCH URL',s.replace(/[🔧💭]/g,'').trim());
      else if(s.includes('run_code'))logAct('code','RUN CODE',s.replace(/[🔧💭]/g,'').trim());
      else if(s.includes('run_shell'))logAct('code','SHELL',s.replace(/[🔧💭]/g,'').trim());
      else if(s.startsWith('🔧'))logAct('tool','TOOL',s.replace('🔧','').trim());
      else if(s.startsWith('💭'))logAct('think','THINKING',s.replace('💭','').trim());
      break;
    }
    case 'agent_done':
      upsertAg({...ev,status:'done'});
      doneAgents++;
      addCost(ev.cost,ev.task_cost);
      updateProgress(doneAgents,totalAgents);
      logAct('result','AGENT DONE','['+ev.agent_id+'] '+(ev.result||'').slice(0,100));
      if(ev.image_url){showImage(ev.image_url,ev.agent_id);}
      break;
    case 'agent_error':
      upsertAg({...ev,status:'error'});
      logAct('tool','ERROR',ev.error||'');
      break;
    case 'direct_response':{
      const txt=ev.content||'';
      // Check for image URL in response
      const imgMatch=txt.match(/IMAGE_URL:\s*(https:\/\/[^\s\n]+)/);
      if(imgMatch){
        if(curBub)curBub.innerHTML=md(txt.replace(/IMAGE_URL:.*$/m,'').trim());
        showImage(imgMatch[1],'nexus');
      } else if(txt.toLowerCase().includes('/bot')||txt.toLowerCase().includes('bot tester')){
        if(curBub)curBub.innerHTML=md(txt)+`<div style="margin-top:10px"><a href="/bot" target="_blank" style="display:inline-block;padding:7px 16px;background:linear-gradient(135deg,var(--pink),var(--orange));color:#fff;border-radius:8px;font-size:.78rem;font-weight:600;text-decoration:none">🚿 Open Bot Tester →</a></div>`;
      } else {
        if(curBub)curBub.innerHTML=md(txt);
      }
      addCost(ev.cost);
      break;
    }
    case 'progress':
      updateProgress(ev.completed,ev.total);
      addCost(0,ev.task_cost);
      break;
    case 'cost_update':
      addCost(0,ev.task_cost);
      break;
    case 'image_loading':
      showImageLoading(ev.prompt||'');
      break;
    case 'image_result':
      if(ev.image&&ev.image.url)showImage(ev.image.url,ev.agent_id);
      break;
    case 'task_complete':
      updateProgress(totalAgents,totalAgents);
      addCost(0,ev.task_cost);
      break;
    case 'synthesis':
      if(curBub)curBub.innerHTML=md(ev.content||'');
      if(curMeta){
        const costTag=ev.cost?`<span class="mtag ctag">$${(ev.cost).toFixed(6)}</span>`:'';
        curMeta.innerHTML=`Nexus <span class="mtag stag">✓ synthesized</span><span class="mtag">${esc(ev.model||'')}</span>${costTag}`;
      }
      addCost(ev.cost);
      break;
    case 'self_improvement':
      if(curBub)curBub.innerHTML=md(ev.content||'');
      break;
  }
  scrl();
}

function addUsrMsg(text,src){
  const f=document.getElementById('feed');const d=document.createElement('div');d.className='mu';
  const badge=src==='telegram'?'<span class="mtag tgtag" style="font-size:.58rem">📱 Telegram</span>':'';
  d.innerHTML=`<div class="meta" style="justify-content:flex-end">${badge} You</div><div class="bub">${esc(text)}</div>`;
  f.appendChild(d);scrl();
}

function addAIMsg(model){
  const f=document.getElementById('feed');const d=document.createElement('div');d.className='ma';
  const id='b'+Date.now();
  d.innerHTML=`<div class="meta" id="m${id}">Nexus</div><div class="bub" id="${id}"><span class="thinking">Planning…</span></div>`;
  f.appendChild(d);scrl();
  return[document.getElementById(id),document.getElementById('m'+id)];
}

function addPlanCard(reason,n,subtasks){
  const f=document.getElementById('feed');const d=document.createElement('div');
  const stList=(subtasks||[]).map(s=>{
    const tc=(s.type||'general').toLowerCase();
    return `<div style="font-size:.7rem;color:#5a8aa0;padding:3px 0;display:flex;align-items:center;gap:6px" id="plan-task-${esc(s.id)}">
      <span class="dot pending" style="width:4px;height:4px"></span>
      <span class="actype ${tc}" style="font-size:.55rem;padding:0px 5px;margin:0">${tc}</span>
      <span>${esc((s.task||'').slice(0,55))}</span>
    </div>`;
  }).join('');
  d.innerHTML=`<div class="plan-card">
    <div class="pct" style="display:flex;align-items:center;gap:6px">
      <div class="ag-spinner" style="width:10px;height:10px"></div>
      <span>⚡ ${n} sub-agents spawning</span>
    </div>
    <div class="pcr" style="margin-bottom:6px">${esc(reason||'')}</div>
    <div id="prog-wrap" style="background:#0a1520;border-radius:5px;height:8px;overflow:hidden;margin:6px 0;position:relative">
      <div id="prog-bar" style="height:100%;width:0%;background:linear-gradient(90deg,var(--cyan),var(--purple));transition:width .5s cubic-bezier(.4,0,.2,1);border-radius:5px"></div>
    </div>
    <div id="prog-lbl" style="font-size:.64rem;color:var(--muted);font-family:'JetBrains Mono',monospace;display:flex;justify-content:space-between">
      <span>0/${n} agents complete</span>
      <span id="prog-cost">$0.0000</span>
    </div>
    <div style="margin-top:6px">${stList}</div>
  </div>`;
  f.appendChild(d);scrl();
}

function updateProgress(done,total){
  const bar=document.getElementById('prog-bar');
  const lbl=document.getElementById('prog-lbl');
  const costEl=document.getElementById('prog-cost');
  if(!bar||!total)return;
  const pct=Math.round((done/total)*100);
  bar.style.width=pct+'%';
  if(pct===100){
    bar.style.background='linear-gradient(90deg,var(--green),var(--cyan))';
    // Remove spinner from plan card header
    const spinners=document.querySelectorAll('.plan-card .ag-spinner');
    spinners.forEach(s=>{s.style.display='none';});
  }
  if(lbl){
    const spans=lbl.querySelectorAll('span');
    if(spans[0])spans[0].textContent=`${done}/${total} agents complete (${pct}%)`;
  }
  if(costEl)costEl.textContent='$'+taskCost.toFixed(4);
  // Update plan task dots based on agent status
  Object.values(ags).forEach(ag=>{
    const taskEl=document.getElementById('plan-task-'+ag.agent_id);
    if(taskEl){
      const dot=taskEl.querySelector('.dot');
      if(dot){
        dot.className='dot '+(ag.status||'pending');
      }
    }
  });
}

function showImage(url,agentId){
  const f=document.getElementById('feed');
  // Remove any existing loading placeholder for this image
  const existing=document.getElementById('img-loading');
  if(existing)existing.remove();
  const d=document.createElement('div');d.className='ma';
  const imgId='img_'+Date.now();
  d.innerHTML=`<div class="meta"><span class="mtag" style="color:#f472b6;border-color:rgba(244,114,182,.3);background:rgba(244,114,182,.08)">🎨 ${esc(agentId||'image')}</span></div>
    <div class="bub" style="padding:8px">
      <div id="${imgId}_loader" style="display:flex;align-items:center;gap:8px;padding:12px 0">
        <div style="width:18px;height:18px;border:2px solid var(--border);border-top-color:var(--pink);border-radius:50%;animation:spin .8s linear infinite"></div>
        <span style="font-size:.76rem;color:var(--muted)">Loading image...</span>
      </div>
      <img id="${imgId}" src="${esc(url)}" style="max-width:100%;border-radius:10px;display:none"
        onload="this.style.display='block';document.getElementById('${imgId}_loader').style.display='none';"
        onerror="document.getElementById('${imgId}_loader').innerHTML='<span style=\\'font-size:.76rem;color:var(--muted)\\'>Image failed to load. </span><a href=\\''+this.src+'\\' target=\\'_blank\\' style=\\'color:var(--cyan);font-size:.76rem\\'>Open directly →</a>';">
      <div style="font-size:.65rem;color:var(--muted);margin-top:5px">Generated via pollinations.ai · <a href="${esc(url)}" target="_blank" style="color:var(--cyan)">Open full size →</a></div>
    </div>`;
  f.appendChild(d);scrl();
}

function showImageLoading(prompt){
  const f=document.getElementById('feed');
  const d=document.createElement('div');d.className='ma';d.id='img-loading';
  d.innerHTML=`<div class="meta"><span class="mtag" style="color:#f472b6;border-color:rgba(244,114,182,.3);background:rgba(244,114,182,.08)">🎨 image_gen</span></div>
    <div class="bub" style="padding:12px;display:flex;align-items:center;gap:10px">
      <div style="width:20px;height:20px;border:2px solid var(--border);border-top-color:var(--pink);border-radius:50%;animation:spin .8s linear infinite;flex-shrink:0"></div>
      <div><div style="font-size:.78rem;color:var(--hi)">Generating: ${esc(prompt)}</div><div style="font-size:.66rem;color:var(--muted);margin-top:2px">pollinations.ai — this may take 10-20s</div></div>
    </div>`;
  f.appendChild(d);scrl();
}

function clearAgs(){Object.keys(ags).forEach(k=>delete ags[k]);document.getElementById('acl').innerHTML='';document.getElementById('acc').textContent='idle';}

function upsertAg(data){
  const list=document.getElementById('acl');
  let card=document.getElementById('ag_'+data.agent_id);
  const isNew=!card;
  if(isNew){card=document.createElement('div');card.id='ag_'+data.agent_id;list.appendChild(card);}
  const ag=ags[data.agent_id]||{};Object.assign(ag,data);
  if(ag.status==='running'&&!ag._startTs)ag._startTs=Date.now();
  ags[data.agent_id]=ag;
  card.className='ac '+(ag.status||'pending');
  const tc=(ag.task_type||'general').toLowerCase();
  const allSteps=ag.steps||[];
  const lastStep=allSteps.length?allSteps[allSteps.length-1]:'';
  const costStr=ag.cost?`$${Number(ag.cost).toFixed(4)}`:'';
  const dur=ag.duration?`${ag.duration}s`:'';
  const isRunning=ag.status==='running';
  const isDone=ag.status==='done';
  const isError=ag.status==='error';

  // Running state: show spinner, elapsed time, current step
  let statusLine='';
  if(isRunning){
    const elapsed=ag._startTs?((Date.now()-ag._startTs)/1000).toFixed(0)+'s':'…';
    statusLine=`<div style="display:flex;align-items:center;gap:6px;margin:3px 0">
      <div class="ag-spinner"></div>
      <span class="ag-elapsed" data-agent-timer="${esc(ag.agent_id)}">${elapsed}</span>
      <span style="font-size:.56rem;color:var(--muted)">${esc(ag.model||'')}</span>
    </div>`;
    if(lastStep) statusLine+=`<div class="ag-current-step">${esc(lastStep)}</div>`;
  } else if(isDone){
    statusLine=`<div class="ag-done-badge">✓ ${dur} · ${esc(ag.model||'')} ${costStr?'· '+costStr:''}</div>`;
  } else if(isError){
    statusLine=`<div style="font-size:.58rem;color:var(--red);margin:3px 0">✗ ${esc((ag.error||'failed').slice(0,80))}</div>`;
  }

  // Steps — auto-expand for running, collapsible for done
  const stepsHtml=allSteps.slice(-5).map(s=>`<div class="acstep">${esc(s)}</div>`).join('');
  const detailsVisible=isRunning?'block':'none';

  card.innerHTML=`
    <div class="act-top" style="cursor:pointer" onclick="toggleAgDetails('${esc(ag.agent_id)}')">
      ${isRunning?'<div class="dot running"></div>':isDone?'<div class="dot done"></div>':isError?'<div class="dot error"></div>':'<div class="dot pending"></div>'}
      <div class="acid">${isDone?'✓ ':isError?'✗ ':''}${esc(ag.agent_id)}</div>
      <div class="acml">${isDone?costStr||'':esc(ag.model||'')}</div>
    </div>
    <span class="actype ${tc}">${tc}</span>
    <div class="actask">${esc((ag.task||'').slice(0,70))}</div>
    ${statusLine}
    <div class="ag-details" id="agd_${esc(ag.agent_id)}" style="display:${detailsVisible}">
      ${stepsHtml?`<div class="acsteps" style="margin-top:4px">${stepsHtml}</div>`:''}
    </div>`;
  const vals=Object.values(ags);
  const run=vals.filter(a=>a.status==='running').length;
  const done=vals.filter(a=>a.status==='done').length;
  const errs=vals.filter(a=>a.status==='error').length;
  const totalCostAgs=vals.reduce((s,a)=>s+Number(a.cost||0),0);
  let statusText=run?`${run} running`:'idle';
  if(done||errs)statusText=`${done}/${vals.length} done${errs?' · '+errs+' err':''}`;
  if(run&&done)statusText=`${run} running · ${done} done`;
  document.getElementById('acc').textContent=statusText;
  const costEl=document.getElementById('ag-total-cost');
  if(costEl&&totalCostAgs)costEl.textContent=`$${totalCostAgs.toFixed(4)}`;
}

// Live elapsed time updater for running agents
setInterval(()=>{
  document.querySelectorAll('[data-agent-timer]').forEach(el=>{
    const id=el.getAttribute('data-agent-timer');
    const ag=ags[id];
    if(ag&&ag.status==='running'&&ag._startTs){
      el.textContent=((Date.now()-ag._startTs)/1000).toFixed(0)+'s';
    }
  });
},1000);

function toggleAgDetails(id){
  const el=document.getElementById('agd_'+id);
  if(el)el.style.display=el.style.display==='none'?'block':'none';
}

async function go(){
  if(busy)return;const inp=document.getElementById('inp');const msg=inp.value.trim();if(!msg)return;
  inp.value='';inp.style.height='auto';busy=true;document.getElementById('send').disabled=true;
  clearAgs();actCnt=0;taskCost=0;totalAgents=0;doneAgents=0;document.getElementById('avcnt').textContent='0 actions';document.getElementById('avl').innerHTML='';const tcd=document.getElementById('task-costd');if(tcd)tcd.textContent='';
  addUsrMsg(msg,'browser');const[bubble,metaEl]=addAIMsg();curBub=bubble;curMeta=metaEl;
  let finalText='';
  const resp=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:msg,history:hist})});
  const reader=resp.body.getReader(),dec=new TextDecoder();let buf='';
  while(true){
    const{done,value}=await reader.read();if(done)break;
    buf+=dec.decode(value,{stream:true});const lines=buf.split('\n');buf=lines.pop();
    for(const line of lines){if(!line.startsWith('data: '))continue;
      try{const ev=JSON.parse(line.slice(6));handleEv(ev);if(ev.type==='direct_response'||ev.type==='synthesis')finalText=ev.content||'';}catch(e){}
    }
  }
  hist.push({role:'user',content:msg},{role:'assistant',content:finalText});
  busy=false;document.getElementById('send').disabled=false;curBub=null;curMeta=null;scrl();
}

async function improve(){
  if(busy)return;busy=true;document.getElementById('send').disabled=true;
  const[b,m]=addAIMsg();curBub=b;curMeta=m;b.innerHTML='<span class="thinking">🔬 Analyzing…</span>';
  const r=await fetch('/api/self-improve',{method:'POST'});const d=await r.json();
  const ev=d.events?.find(e=>e.type==='self_improvement');if(ev&&b)b.innerHTML=md(ev.content||'');
  busy=false;document.getElementById('send').disabled=false;curBub=null;curMeta=null;
}

async function openMem(){
  document.getElementById('memp').classList.add('open');const mpb=document.getElementById('mpb');mpb.innerHTML='Loading…';
  const data=await fetch('/api/memory/history').then(r=>r.json()).catch(()=>[]);
  if(!data.length){mpb.innerHTML='<p style="color:var(--muted);font-size:.78rem;text-align:center;padding:20px">No history yet</p>';return;}
  mpb.innerHTML=data.slice().reverse().map(r=>`<div class="mi"><div class="miq">▸ ${esc((r.user_msg||'').slice(0,70))}</div><div class="mia">${esc((r.assistant_msg||'').slice(0,130))}</div><div class="mits">${r.ts||''}</div></div>`).join('');
}
function closeMem(){document.getElementById('memp').classList.remove('open');}
async function changeKeys(){if(confirm('⚠️ WARNING: This will erase ALL API keys (Anthropic, Gemini, Moonshot, OpenAI) from config. You will need to re-enter them.\n\nAre you absolutely sure?')){if(confirm('FINAL CONFIRMATION: Really delete all API keys? This cannot be undone.')){await fetch('/api/clear-config',{method:'POST'});window.location.href='/';}}}

// ── GHL Panel ──────────────────────────────────────────────────────────────────
function openGHL(){document.getElementById('ghlp').classList.add('open');loadGHLStatus();loadGHLContacts();}
function closeGHL(){document.getElementById('ghlp').classList.remove('open');}

async function loadGHLStatus(){
  const s=await fetch('/api/ghl/status').then(r=>r.json()).catch(()=>({}));
  const dot=document.getElementById('ghl-dot');const lbl=document.getElementById('ghl-lbl');
  if(s.ghl_connected){dot.style.color='var(--green)';lbl.textContent=s.poller_running?'Live — watching for new contacts':'Connected (poller stopped)';}
  else{dot.style.color='var(--red)';lbl.textContent='Not connected — add API key below';}
  const msglbl=document.getElementById('ghl-msg-lbl');
  if(msglbl) msglbl.textContent=s.messaging_ready?'✅ SMS ready':'⚠️ Add Gmail to enable SMS';
}

async function loadGHLContacts(){
  const q=document.getElementById('ghl-q')?.value||'';
  const r=await fetch(`/api/ghl/contacts?q=${encodeURIComponent(q)}&limit=50`).then(r=>r.json()).catch(()=>({error:'Not configured'}));
  const el=document.getElementById('ghl-contacts');
  if(!el)return;
  if(r.error){el.innerHTML=`<div style="color:var(--muted);font-size:.76rem;padding:12px">${esc(r.error)}</div>`;return;}
  const contacts=r.contacts||[];
  el.innerHTML=contacts.map(c=>{
    const name=`${c.firstName||''} ${c.lastName||''}`.trim()||'—';
    const phone=c.phone||'—';const email=c.email||'';const src=c.source||'';
    const created=(c.dateAdded||'').slice(0,10);
    return `<div class="gc" onclick="fillSend('${esc(phone)}','${esc(name)}')">
      <div class="gc-name">${esc(name)}</div>
      <div class="gc-sub">${esc(phone)} ${email?'· '+esc(email.slice(0,25)):''}</div>
      <div class="gc-meta">${esc(src)} · ${created}</div>
    </div>`;
  }).join('') || '<div style="color:var(--muted);font-size:.76rem;padding:12px">No contacts</div>';
}

async function loadMsgLog(){
  const data=await fetch('/api/ghl/messages?limit=30').then(r=>r.json()).catch(()=>[]);
  const el=document.getElementById('ghl-msglog');if(!el)return;
  el.innerHTML=data.map(m=>`<div class="ml-item ${m.success?'ok':'fail'}">
    <div class="ml-name">${esc(m.name||'')} <span>${esc(m.phone||'')}</span></div>
    <div class="ml-msg">${esc((m.message||'').slice(0,80))}</div>
    <div class="ml-ts">${m.success?'✅':'❌'} ${esc(m.ts||'')}</div>
  </div>`).join('') || '<div style="color:var(--muted);font-size:.76rem;padding:12px">No messages sent yet</div>';
}

function fillSend(phone,name){
  document.getElementById('ghl-send-phone').value=phone;
  document.getElementById('ghl-tab-send').click();
}

async function saveGHLConfig(){
  const g=id=>document.getElementById(id)?.value?.trim()||'';
  const body={ghl_api_key:g('ghl-key'),ghl_location_id:g('ghl-loc'),gmail_address:g('ghl-gmail'),
    gmail_app_password:g('ghl-gpass'),default_carrier:g('ghl-carrier'),
    message_template:g('ghl-template'),ghl_poll_interval:g('ghl-interval')||'60'};
  const r=await fetch('/api/ghl/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const d=await r.json();
  const btn=document.getElementById('ghl-save-btn');
  if(d.ok){btn.textContent='✅ Saved!';setTimeout(()=>{btn.textContent='Save & Connect';loadGHLStatus();},2000);}
  else{btn.textContent='❌ Error';setTimeout(()=>btn.textContent='Save & Connect',2000);}
}

async function sendManual(){
  const phone=document.getElementById('ghl-send-phone').value.trim();
  const msg=document.getElementById('ghl-send-msg').value.trim();
  const carrier=document.getElementById('ghl-carrier2').value;
  if(!phone||!msg){alert('Need phone and message');return;}
  const btn=document.getElementById('ghl-send-btn');btn.disabled=true;btn.textContent='Sending…';
  const r=await fetch('/api/ghl/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone,message:msg,carrier})});
  const d=await r.json();
  btn.disabled=false;
  if(d.ok){btn.textContent='✅ Sent!';setTimeout(()=>btn.textContent='Send SMS',2000);}
  else{btn.textContent='❌ Failed';alert('Error: '+(d.error||'unknown'));setTimeout(()=>btn.textContent='Send SMS',2000);}
}

function ghlTab(tab){
  document.querySelectorAll('.ghl-tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.ghl-pane').forEach(p=>p.style.display='none');
  document.getElementById('ghl-tab-'+tab).classList.add('active');
  document.getElementById('ghl-pane-'+tab).style.display='flex';
  if(tab==='log')loadMsgLog();
}

// Handle GHL events from SSE
const _origHandleEv=handleEv;
window.handleEv=function(ev){
  _origHandleEv(ev);
  if(ev.type==='ghl_event'){
    const f=document.getElementById('feed');const d=document.createElement('div');d.className='ma';
    d.innerHTML=`<div class="meta"><span class="mtag" style="color:#22d47a;border-color:rgba(34,212,122,.3);background:rgba(34,212,122,.08)">📋 GHL</span></div><div class="bub">${md(ev.message||'')}</div>`;
    f.appendChild(d);scrl();
    if(document.getElementById('ghlp').classList.contains('open'))loadGHLContacts();
  }
};

loadStatus();startSSE();
</script>

<!-- GHL Panel styles injected into head equivalent -->
<style>
.ghl-panel{position:fixed;right:0;top:0;bottom:0;width:460px;background:var(--s1);border-left:1px solid var(--border);transform:translateX(100%);transition:transform .28s cubic-bezier(.4,0,.2,1);z-index:100;display:flex;flex-direction:column}
.ghl-panel.open{transform:translateX(0)}
.ghl-header{padding:14px 16px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;flex-shrink:0;background:var(--bg)}
.ghl-header h2{font-size:.85rem;font-weight:600;color:var(--hi);display:flex;align-items:center;gap:8px}
.ghl-status{display:flex;align-items:center;gap:6px;font-size:.68rem;color:var(--muted)}
.ghl-tabs{display:flex;border-bottom:1px solid var(--border);flex-shrink:0}
.ghl-tab{flex:1;padding:8px;text-align:center;font-size:.68rem;font-weight:600;color:var(--muted);cursor:pointer;border:none;background:none;font-family:'DM Sans',sans-serif;letter-spacing:.04em;text-transform:uppercase;transition:color .15s}
.ghl-tab:hover{color:var(--hi)}.ghl-tab.active{color:var(--cyan);border-bottom:2px solid var(--cyan)}
.ghl-pane{flex:1;overflow-y:auto;flex-direction:column;padding:12px;gap:8px;scrollbar-width:thin;scrollbar-color:var(--border) transparent}
/* Contacts */
.gc{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:9px 11px;cursor:pointer;transition:border-color .15s}
.gc:hover{border-color:rgba(6,182,212,.4)}
.gc-name{font-size:.78rem;font-weight:600;color:var(--hi);margin-bottom:2px}
.gc-sub{font-size:.7rem;color:var(--cyan);font-family:'JetBrains Mono',monospace}
.gc-meta{font-size:.62rem;color:var(--muted);margin-top:2px}
/* Search bar */
.ghl-search{display:flex;gap:6px;margin-bottom:8px;flex-shrink:0}
.ghl-search input{flex:1;background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:7px 11px;color:var(--hi);font-size:.78rem;outline:none;font-family:'DM Sans',sans-serif}
.ghl-search input:focus{border-color:var(--cyan)}
.gbl-refresh{background:rgba(6,182,212,.1);color:var(--cyan);border:1px solid rgba(6,182,212,.3);border-radius:8px;padding:7px 12px;cursor:pointer;font-size:.72rem;font-family:'DM Sans',sans-serif}
/* Config form */
.ghl-field{margin-bottom:10px}
.ghl-field label{display:block;font-size:.68rem;color:var(--muted);font-weight:500;margin-bottom:4px;letter-spacing:.04em}
.ghl-field input,.ghl-field select,.ghl-field textarea{width:100%;background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:8px 11px;color:var(--hi);font-size:.78rem;font-family:'JetBrains Mono',monospace;outline:none;transition:border-color .2s}
.ghl-field input:focus,.ghl-field select:focus,.ghl-field textarea:focus{border-color:var(--cyan)}
.ghl-field textarea{resize:vertical;min-height:70px;font-family:'DM Sans',sans-serif;font-size:.78rem}
.ghl-field select{appearance:none}
.ghl-save{width:100%;padding:10px;background:linear-gradient(135deg,var(--cyan),var(--purple));color:#fff;border:none;border-radius:9px;font-size:.82rem;font-weight:600;cursor:pointer;font-family:'DM Sans',sans-serif;margin-top:4px}
.ghl-hint{font-size:.65rem;color:var(--muted);margin-top:3px;line-height:1.4}
.ghl-hint a{color:var(--cyan);text-decoration:none}
/* Send */
.send-btn{width:100%;padding:10px;background:linear-gradient(135deg,#22d47a,#06b6d4);color:#fff;border:none;border-radius:9px;font-size:.82rem;font-weight:600;cursor:pointer;font-family:'DM Sans',sans-serif;margin-top:8px}
.send-btn:disabled{opacity:.4}
/* Log */
.ml-item{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:8px 11px;margin-bottom:6px}
.ml-item.fail{border-color:rgba(239,68,68,.25)}
.ml-name{font-size:.76rem;font-weight:600;color:var(--hi)}.ml-name span{color:var(--cyan);font-family:'JetBrains Mono',monospace;font-weight:400}
.ml-msg{font-size:.7rem;color:var(--muted);margin:3px 0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.ml-ts{font-size:.62rem;color:#1a3a50;font-family:'JetBrains Mono',monospace}
.section-title{font-size:.62rem;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:8px}
</style>

<!-- GHL Panel HTML -->
<div class="ghl-panel" id="ghlp">
  <div class="ghl-header">
    <h2>📋 GoHighLevel CRM</h2>
    <div style="display:flex;align-items:center;gap:10px">
      <div class="ghl-status"><span id="ghl-dot" style="font-size:.8rem">●</span><span id="ghl-lbl">Checking…</span></div>
      <button class="xb" onclick="closeGHL()">✕</button>
    </div>
  </div>
  <div class="ghl-tabs">
    <button class="ghl-tab active" id="ghl-tab-contacts" onclick="ghlTab('contacts')">Contacts</button>
    <button class="ghl-tab" id="ghl-tab-send" onclick="ghlTab('send')">Send SMS</button>
    <button class="ghl-tab" id="ghl-tab-log" onclick="ghlTab('log')">Message Log</button>
    <button class="ghl-tab" id="ghl-tab-config" onclick="ghlTab('config')">Settings</button>
  </div>

  <!-- Contacts pane -->
  <div class="ghl-pane" id="ghl-pane-contacts" style="display:flex">
    <div class="ghl-search">
      <input id="ghl-q" placeholder="Search contacts…" onkeydown="if(event.key==='Enter')loadGHLContacts()">
      <button class="gbl-refresh" onclick="loadGHLContacts()">↻ Refresh</button>
    </div>
    <div id="ghl-contacts" style="display:flex;flex-direction:column;gap:6px">
      <div style="color:var(--muted);font-size:.76rem;padding:12px">Loading contacts…</div>
    </div>
  </div>

  <!-- Send SMS pane -->
  <div class="ghl-pane" id="ghl-pane-send" style="display:none">
    <div class="section-title">Send Free SMS via Email Gateway</div>
    <div class="ghl-field"><label>Phone Number</label><input id="ghl-send-phone" placeholder="+1 424 235 8979" type="tel"></div>
    <div class="ghl-field">
      <label>Carrier (required for email-to-SMS)</label>
      <select id="ghl-carrier2">
        <option value="tmobile">T-Mobile</option>
        <option value="att">AT&T</option>
        <option value="verizon">Verizon</option>
        <option value="cricket">Cricket</option>
        <option value="metro">Metro PCS</option>
        <option value="boost">Boost</option>
        <option value="google_fi">Google Fi</option>
        <option value="sprint">Sprint</option>
        <option value="virgin">Virgin</option>
      </select>
    </div>
    <div class="ghl-field"><label>Message</label><textarea id="ghl-send-msg" placeholder="Hey {first_name}! This is Kai from Zoar Bathroom Rentals…" style="min-height:100px"></textarea></div>
    <div class="ghl-hint">💡 Click any contact in the Contacts tab to auto-fill their number. Carrier must match the recipient's carrier.</div>
    <button class="send-btn" id="ghl-send-btn" onclick="sendManual()">📱 Send SMS (Free)</button>
    <div id="ghl-msg-lbl" style="font-size:.68rem;color:var(--muted);text-align:center;margin-top:8px"></div>
  </div>

  <!-- Message Log pane -->
  <div class="ghl-pane" id="ghl-pane-log" style="display:none">
    <div class="section-title">Auto-sent messages</div>
    <div id="ghl-msglog"><div style="color:var(--muted);font-size:.76rem;padding:12px">Loading…</div></div>
  </div>

  <!-- Config pane -->
  <div class="ghl-pane" id="ghl-pane-config" style="display:none">
    <div class="section-title">GoHighLevel API</div>
    <div class="ghl-field"><label>GHL API Key (Private Integration)</label><input type="password" id="ghl-key" placeholder="eyJhbGciOiJSUzI1Ni..."><div class="ghl-hint">Settings → Integrations → API Keys → Private Integration Key</div></div>
    <div class="ghl-field"><label>Location ID</label><input id="ghl-loc" placeholder="rMp3pF7h1dwrWc6DeGzr"><div class="ghl-hint">Settings → Business Info → Location ID (or from your URL)</div></div>

    <div class="section-title" style="margin-top:14px">Free SMS via Gmail</div>
    <div class="ghl-field"><label>Gmail Address</label><input id="ghl-gmail" placeholder="you@gmail.com" type="email"></div>
    <div class="ghl-field"><label>Gmail App Password <span style="color:var(--cyan)">(NOT your regular password)</span></label><input type="password" id="ghl-gpass" placeholder="xxxx xxxx xxxx xxxx"><div class="ghl-hint">Google Account → Security → 2-Step Verification → App Passwords → Create. <a href="https://myaccount.google.com/apppasswords" target="_blank">Get one here →</a></div></div>
    <div class="ghl-field"><label>Default Carrier (for auto-texts)</label>
      <select id="ghl-carrier"><option value="tmobile">T-Mobile</option><option value="att">AT&T</option><option value="verizon">Verizon</option><option value="cricket">Cricket</option><option value="metro">Metro PCS</option><option value="boost">Boost</option><option value="google_fi">Google Fi</option></select>
      <div class="ghl-hint">⚠️ You need to know your lead's carrier. If unknown, try T-Mobile (most common in LA)</div>
    </div>

    <div class="section-title" style="margin-top:14px">Auto-Message Template</div>
    <div class="ghl-field"><label>Message sent to every new contact · Variables: {first_name} {last_name} {phone} {source}</label>
      <textarea id="ghl-template" style="min-height:110px">Hey {first_name}! This is Kai from Zoar Bathroom Rentals 🚽 Thanks for reaching out! I'd love to chat about getting you set up with a clean portable restroom trailer for your event. When's a good time to connect? Reply here or call (424) 235-8979</textarea>
    </div>
    <div class="ghl-field"><label>Poll Interval (seconds)</label><input id="ghl-interval" placeholder="60" value="60" type="number"></div>
    <button class="ghl-save" id="ghl-save-btn" onclick="saveGHLConfig()">Save & Connect →</button>
  </div>
</div>
</body></html>"""

# ── Agent System API ────────────────────────────────────────────────────────

_agent_manager = None

@app.get("/api/agents/status")
async def agents_status():
    """Get status of all agents."""
    from agents.config import AGENT_STATE_DIR
    status = {"agents": {}, "total_cost": 0}
    # Read saved state files
    for f in AGENT_STATE_DIR.glob("*.json"):
        if f.stem in ("manager_status", "content_queue", "comment_templates",
                       "fb_engagement_log", "ig_engagement_log", "discovered_leads"):
            continue
        try:
            data = json.loads(f.read_text())
            status["agents"][f.stem] = data
        except: pass
    # Manager status
    manager_file = AGENT_STATE_DIR / "manager_status.json"
    if manager_file.exists():
        try:
            status.update(json.loads(manager_file.read_text()))
        except: pass
    return JSONResponse(status)

@app.get("/api/agents/content-queue")
async def agents_content_queue():
    """Get generated content ready for review/posting."""
    from agents.config import AGENT_STATE_DIR
    queue_file = AGENT_STATE_DIR / "content_queue.json"
    if queue_file.exists():
        return JSONResponse(json.loads(queue_file.read_text()))
    return JSONResponse([])

@app.get("/api/agents/engagement-log")
async def agents_engagement_log():
    """Get social media engagement activity."""
    from agents.config import AGENT_STATE_DIR
    result = {"facebook": [], "instagram": []}
    fb_log = AGENT_STATE_DIR / "fb_engagement_log.json"
    ig_log = AGENT_STATE_DIR / "ig_engagement_log.json"
    if fb_log.exists():
        try: result["facebook"] = json.loads(fb_log.read_text())[-50:]
        except: pass
    if ig_log.exists():
        try: result["instagram"] = json.loads(ig_log.read_text())[-50:]
        except: pass
    return JSONResponse(result)

@app.get("/api/agents/discovered-leads")
async def agents_discovered_leads():
    """Get leads discovered by the lead finder agent."""
    from agents.config import AGENT_STATE_DIR
    leads_file = AGENT_STATE_DIR / "discovered_leads.json"
    if leads_file.exists():
        try: return JSONResponse(json.loads(leads_file.read_text()))
        except: pass
    return JSONResponse([])

@app.post("/api/agents/generate-content")
async def agents_generate_content(request: Request):
    """Trigger content generation (posts, comments, templates)."""
    body = await request.json()
    content_type = body.get("type", "posts")  # "posts", "comments", "templates"
    count = body.get("count", 7)

    try:
        from agents.content_agent import ContentAgent
        agent = ContentAgent()

        if content_type == "posts":
            posts = await agent.generate_content_batch(count=count)
            return JSONResponse({"ok": True, "generated": len(posts), "posts": posts})
        elif content_type == "comments":
            from agents.manager import AgentManager
            mgr = AgentManager()
            templates = await mgr.generate_comment_templates(count=count)
            return JSONResponse({"ok": True, "generated": len(templates), "templates": templates})
        else:
            return JSONResponse({"ok": False, "error": f"Unknown type: {content_type}"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@app.get("/api/agents/facebook-ads")
async def agents_facebook_ads():
    """Get the Facebook ad campaign configuration."""
    ads_file = Path(__file__).parent / "scripts" / "facebook_ads.json"
    if ads_file.exists():
        return JSONResponse(json.loads(ads_file.read_text()))
    return JSONResponse({"error": "No ads configured"}, status_code=404)

@app.get("/api/agents/activity")
async def agents_activity():
    """Get recent agent activity from CRM database."""
    from agents.config import DB_PATH
    import sqlite3
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM agent_activity ORDER BY ts DESC LIMIT 50"
        ).fetchall()
        conn.close()
        return JSONResponse([dict(r) for r in rows])
    except Exception:
        return JSONResponse([])


# ── Agent Dashboard Endpoints ──────────────────────────────────────────────

@app.get("/api/agents/live-feed")
async def agents_live_feed():
    """Unified live feed — last 50 entries from agent activity + brain."""
    from agents.config import DB_PATH
    import sqlite3
    entries = []
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT agent_id, event_type, details, ts FROM agent_activity ORDER BY ts DESC LIMIT 30"
            ).fetchall()
            for r in rows:
                entries.append({
                    "source": r["agent_id"], "type": r["event_type"],
                    "content": r["details"], "ts": r["ts"], "feed": "activity"
                })
        except: pass
        try:
            rows = conn.execute(
                "SELECT source_agent, insight_type, content, ts FROM agent_brain ORDER BY ts DESC LIMIT 20"
            ).fetchall()
            for r in rows:
                entries.append({
                    "source": r["source_agent"], "type": r["insight_type"],
                    "content": r["content"], "ts": r["ts"], "feed": "brain"
                })
        except: pass
        conn.close()
    except: pass
    entries.sort(key=lambda x: x.get("ts", ""), reverse=True)
    return JSONResponse(entries[:50])


@app.get("/api/agents/brain")
async def agents_brain():
    """Get shared brain insights + discussions + knowledge + metrics."""
    try:
        from agents.shared_brain import SharedBrain
        brain = SharedBrain("dashboard")
        return JSONResponse({
            "summary": brain.get_summary(),
            "recent_insights": brain.read_insights(limit=20),
            "recommendations": brain.read_recommendations(limit=10),
            "metrics": brain.read_metrics(limit=20),
            "discussions": brain.get_recent_discussions(limit=10),
            "open_problems": brain.get_open_problems(limit=5),
            "knowledge": brain.get_all_knowledge(limit=20),
            "latest_metrics": brain.get_latest_metrics(),
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/agents/log/{agent_id}")
async def agents_log(agent_id: str):
    """Get last 100 log lines for a specific agent."""
    from agents.config import AGENT_STATE_DIR
    log_file = AGENT_STATE_DIR / f"{agent_id}.log"
    lines = []
    if log_file.exists():
        try:
            all_lines = log_file.read_text().strip().split("\n")
            lines = all_lines[-100:]
        except: pass
    stdout_log = AGENT_STATE_DIR / f"{agent_id}_agent.stdout.log"
    stdout_lines = []
    if stdout_log.exists():
        try:
            all_lines = stdout_log.read_text().strip().split("\n")
            stdout_lines = all_lines[-50:]
        except: pass
    return JSONResponse({
        "agent_id": agent_id,
        "log": lines,
        "stdout": stdout_lines,
        "log_file": str(log_file),
    })


_running_agents = {}

@app.post("/api/agents/start/{agent_id}")
async def agents_start(agent_id: str):
    """Start an agent via subprocess."""
    import subprocess
    mode_map = {
        "content": "content",
        "content_creator": "content",
        "content_gen": "content",
        "lead_finder": "research",
        "research": "research",
        "analyst": "analyst",
        "facebook": "facebook",
        "instagram": "instagram",
        "higgsfield": "higgsfield",
    }
    mode = mode_map.get(agent_id)
    if not mode:
        return JSONResponse({"ok": False, "error": f"Unknown agent: {agent_id}"}, status_code=400)
    if agent_id in _running_agents and _running_agents[agent_id].poll() is None:
        return JSONResponse({"ok": True, "status": "already_running", "pid": _running_agents[agent_id].pid})
    try:
        proc = subprocess.Popen(
            ["python3", str(Path(__file__).parent / "agents" / "run_agents.py"), "--mode", mode],
            cwd=str(Path(__file__).parent),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).parent), "PYTHONUNBUFFERED": "1"},
        )
        _running_agents[agent_id] = proc
        return JSONResponse({"ok": True, "pid": proc.pid, "mode": mode})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/agents/stop/{agent_id}")
async def agents_stop(agent_id: str):
    """Stop a running agent."""
    import signal as sig
    if agent_id in _running_agents and _running_agents[agent_id].poll() is None:
        _running_agents[agent_id].send_signal(sig.SIGTERM)
        try:
            _running_agents[agent_id].wait(timeout=5)
        except: pass
        del _running_agents[agent_id]
        return JSONResponse({"ok": True, "stopped": agent_id})
    return JSONResponse({"ok": False, "error": "Agent not running via server"}, status_code=400)


@app.get("/api/agents/running")
async def agents_running():
    """Check which agents are currently running (launchd or subprocess)."""
    import subprocess as sp
    running = {}
    for svc in ["com.zoar.agents.content", "com.zoar.agents.research"]:
        try:
            result = sp.run(["launchctl", "list", svc], capture_output=True, text=True, timeout=3)
            if result.returncode == 0:
                agent_name = "content_creator" if "content" in svc else "lead_finder"
                running[agent_name] = {"source": "launchd", "service": svc}
        except: pass
    for agent_id, proc in list(_running_agents.items()):
        if proc.poll() is None:
            running[agent_id] = {"source": "subprocess", "pid": proc.pid}
        else:
            del _running_agents[agent_id]
    return JSONResponse(running)


# ── Feedback Loop Endpoints ────────────────────────────────────────────────

@app.post("/api/webhook/pixel")
async def webhook_pixel(request: Request):
    """Receive Facebook Pixel events (Conversions API or forwarded).
    This is the entry point for the feedback loop: ad click → website → lead."""
    try:
        data = await request.json()
        from agents.metrics_tracker import MetricsTracker
        tracker = MetricsTracker()
        event_name = data.get("event_name", data.get("event", "PageView"))
        source_url = data.get("source_url", data.get("url", ""))
        tracker.record_pixel_event(
            event_name=event_name,
            source_url=source_url,
            user_data=data.get("user_data", {}),
            custom_data=data.get("custom_data", data),
        )
        return JSONResponse({"ok": True, "event": event_name})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/webhook/website-event")
async def webhook_website_event(request: Request):
    """Receive website events (form submissions, page views, button clicks).
    Called by JS on zoarbathroomrental.com."""
    try:
        data = await request.json()
        from agents.metrics_tracker import MetricsTracker
        tracker = MetricsTracker()
        tracker.record_website_event(
            event_type=data.get("event_type", "page_view"),
            page_url=data.get("page_url", ""),
            source=data.get("source", ""),
            utm_source=data.get("utm_source", ""),
            utm_medium=data.get("utm_medium", ""),
            utm_campaign=data.get("utm_campaign", ""),
            utm_content=data.get("utm_content", ""),
            data=data,
        )
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/webhook/form-submit")
async def webhook_form_submit(request: Request):
    """Receive form submissions from the website.
    Creates a lead in CRM + records the website event."""
    try:
        data = await request.json()
        import sqlite3
        from agents.config import DB_PATH
        from agents.metrics_tracker import MetricsTracker

        # Record as website event
        tracker = MetricsTracker()
        tracker.record_website_event(
            event_type="form_submit",
            page_url=data.get("page_url", "zoarbathroomrental.com"),
            source=data.get("utm_source", "website"),
            utm_source=data.get("utm_source", ""),
            utm_medium=data.get("utm_medium", ""),
            utm_campaign=data.get("utm_campaign", ""),
            data=data,
        )

        # Also record pixel Lead event
        tracker.record_pixel_event(
            event_name="Lead",
            source_url=data.get("page_url", ""),
            custom_data=data,
        )

        # Create lead in CRM if has contact info
        name = data.get("name", "").strip()
        phone = data.get("phone", "").strip()
        email = data.get("email", "").strip()
        event_type = data.get("event_type", "")
        event_date = data.get("event_date", "")
        message = data.get("message", "")

        if phone or email:
            # Update hierarchy status — Lead Accelerator is processing
            try:
                from core.agent_hierarchy import update_agent_status
                update_agent_status("lead-accelerator", "sales", "active", f"Processing lead: {name or email or phone}")
            except Exception:
                pass

            conn = sqlite3.connect(str(DB_PATH))
            first_name = name.split()[0] if name else ""
            last_name = " ".join(name.split()[1:]) if name and len(name.split()) > 1 else ""
            source = f"website_form"
            if data.get("utm_campaign"):
                source += f"_{data['utm_campaign']}"

            conn.execute("""
                INSERT INTO leads (first_name, last_name, phone, email, source, event_type,
                                   event_date, booking_status, notes, date_added)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'new_lead', ?, datetime('now'))
            """, (first_name, last_name, phone, email, source, event_type, event_date, message))
            conn.commit()
            lead_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.close()

            # Trigger lead pipeline for auto-contact (new website leads)
            try:
                from core.lead_pipeline import get_pipeline
                pipeline = get_pipeline()
                if pipeline:
                    import asyncio
                    asyncio.ensure_future(pipeline.process_new_lead(lead_id))
            except Exception:
                pass  # Pipeline trigger is best-effort; lead is already saved

            # Log to brain
            from agents.shared_brain import SharedBrain
            brain = SharedBrain("website")
            brain.write_insight(
                content=f"New website lead: {first_name} - {event_type or 'unknown event'} - source: {source}",
                insight_type="activity",
                category="leads",
                confidence=1.0,
                data={"lead_id": lead_id, "source": source, "utm": data.get("utm_campaign", "")},
            )
            brain.log_activity("new_lead", f"Website form: {first_name} for {event_type or 'unknown'}")

            return JSONResponse({"ok": True, "lead_id": lead_id})

        return JSONResponse({"ok": True, "note": "No contact info provided"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/webhook/ad-performance")
async def webhook_ad_performance(request: Request):
    """Import ad performance data (manual or automated from Meta API)."""
    try:
        data = await request.json()
        from agents.metrics_tracker import MetricsTracker
        tracker = MetricsTracker()

        # Can receive single entry or batch
        entries = data if isinstance(data, list) else [data]
        for entry in entries:
            tracker.record_ad_performance(
                platform=entry.get("platform", "facebook"),
                campaign_name=entry.get("campaign_name", ""),
                campaign_id=entry.get("campaign_id", ""),
                impressions=entry.get("impressions", 0),
                clicks=entry.get("clicks", 0),
                spend=entry.get("spend", 0),
                leads=entry.get("leads", 0),
                reach=entry.get("reach", 0),
                video_views=entry.get("video_views", 0),
                video_completions=entry.get("video_completions", 0),
                landing_page_views=entry.get("landing_page_views", 0),
                form_starts=entry.get("form_starts", 0),
                form_completions=entry.get("form_completions", 0),
            )

        return JSONResponse({"ok": True, "imported": len(entries)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ── Metrics Endpoints ──────────────────────────────────────────────────────

@app.get("/api/metrics")
async def get_metrics(request: Request):
    """Get computed marketing metrics.

    Query params:
      - days (default 30): reporting window for DB-derived metrics
      - live (default 1): whether to attempt live Meta pull for ad totals
    """
    try:
        from agents.metrics_tracker import MetricsTracker
        from core.ad_monitor import fetch_ad_insights

        params = dict(request.query_params)
        try:
            days = max(1, min(90, int(params.get("days", "30"))))
        except Exception:
            days = 30
        use_live = str(params.get("live", "1")).strip().lower() not in {"0", "false", "no"}

        tracker = MetricsTracker()
        metrics = tracker.compute_all_metrics(days=days)
        campaign_metrics = tracker.compute_campaign_metrics()

        # Optional live Meta sync so dashboard spend matches Ads Manager more closely.
        if use_live:
            try:
                preset = "last_30d" if days >= 30 else "last_7d"
                rows = await asyncio.wait_for(fetch_ad_insights(date_preset=preset, level="campaign"), timeout=10)
                if rows:
                    live_spend = round(sum(float(r.get("spend", 0) or 0) for r in rows), 2)
                    live_impressions = int(sum(int(r.get("impressions", 0) or 0) for r in rows))
                    live_clicks = int(sum(int(r.get("clicks", 0) or 0) for r in rows))
                    live_reach = int(sum(int(r.get("reach", 0) or 0) for r in rows))
                    live_leads = int(sum(int(r.get("leads", 0) or 0) for r in rows))

                    metrics["total_spend"] = live_spend
                    metrics["total_impressions"] = live_impressions
                    metrics["total_clicks"] = live_clicks
                    metrics["total_reach"] = live_reach
                    metrics["total_ad_leads"] = live_leads
                    metrics["cpl"] = round(live_spend / live_leads, 4) if live_leads > 0 else 0
                    metrics["cpc"] = round(live_spend / live_clicks, 4) if live_clicks > 0 else 0
                    metrics["cpm"] = round((live_spend / live_impressions) * 1000, 4) if live_impressions > 0 else 0
                    metrics["ctr"] = round((live_clicks / live_impressions) * 100, 4) if live_impressions > 0 else 0
                    metrics["frequency"] = round(live_impressions / live_reach, 4) if live_reach > 0 else 0
                    metrics["ad_source"] = "meta_api_live"

                    if not campaign_metrics:
                        built = []
                        for r in rows:
                            spend = float(r.get("spend", 0) or 0)
                            leads = int(r.get("leads", 0) or 0)
                            clicks = int(r.get("clicks", 0) or 0)
                            impressions = int(r.get("impressions", 0) or 0)
                            built.append({
                                "campaign_id": r.get("campaign_id", ""),
                                "campaign_name": r.get("campaign_name", ""),
                                "spend": round(spend, 2),
                                "leads": leads,
                                "clicks": clicks,
                                "impressions": impressions,
                                "cpl": round(spend / leads, 4) if leads > 0 else 0,
                                "ctr": round((clicks / impressions) * 100, 4) if impressions > 0 else 0,
                            })
                        campaign_metrics = built
                else:
                    metrics["ad_source"] = "sqlite_snapshot"
            except Exception:
                metrics["ad_source"] = "sqlite_snapshot"
        else:
            metrics["ad_source"] = "sqlite_snapshot"

        return JSONResponse({
            "overall": metrics,
            "by_campaign": campaign_metrics,
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/metrics/history/{metric_name}")
async def get_metric_history(metric_name: str, days: int = 7):
    """Get metric history over time for charting."""
    try:
        from agents.shared_brain import SharedBrain
        brain = SharedBrain("dashboard")
        history = brain.get_metric_history(metric_name, days=days)
        return JSONResponse({"metric": metric_name, "history": history})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/metrics/latest")
async def get_latest_metrics():
    """Get latest value for all tracked metrics."""
    try:
        from agents.shared_brain import SharedBrain
        brain = SharedBrain("dashboard")
        latest = brain.get_latest_metrics()
        return JSONResponse(latest)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Lead Analysis Endpoints ────────────────────────────────────────────────

@app.post("/api/analysis/lead-dropouts")
async def analyze_lead_dropouts():
    """Run lead dropout analysis on-demand."""
    try:
        from agents.lead_analyzer import LeadAnalyzer
        analyzer = LeadAnalyzer()
        diagnoses = await analyzer.run_analysis(limit=10)
        summary = analyzer.get_dropout_summary()
        return JSONResponse({
            "diagnoses": diagnoses,
            "summary": summary,
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/analysis/dropout-summary")
async def lead_dropout_summary():
    """Get aggregate lead dropout reasons."""
    try:
        from agents.lead_analyzer import LeadAnalyzer
        analyzer = LeadAnalyzer()
        return JSONResponse(analyzer.get_dropout_summary())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Brain Discussion Endpoints ─────────────────────────────────────────────

@app.get("/api/brain/discussions")
async def brain_discussions():
    """Get recent agent discussions."""
    try:
        from agents.shared_brain import SharedBrain
        brain = SharedBrain("dashboard")
        return JSONResponse({
            "discussions": brain.get_recent_discussions(limit=30),
            "open_problems": brain.get_open_problems(limit=10),
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/brain/knowledge")
async def brain_knowledge():
    """Get the knowledge base — problems and solutions agents have learned."""
    try:
        from agents.shared_brain import SharedBrain
        brain = SharedBrain("dashboard")
        return JSONResponse({
            "knowledge": brain.get_all_knowledge(limit=50),
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/brain/run-analysis")
async def brain_run_analysis():
    """Trigger a full analyst cycle on-demand."""
    try:
        from agents.analyst_agent import AnalystAgent
        agent = AnalystAgent()
        insights = await agent.analyze()
        return JSONResponse({
            "ok": True,
            "insights_count": len(insights),
            "insights": insights,
        })
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ── Marketplace Listing Endpoints ─────────────────────────────────────────

@app.get("/api/marketplace/schedule")
async def marketplace_schedule():
    """Get recommended posting schedule for the next 7 days."""
    try:
        from integrations.marketplace import get_posting_schedule, get_stats
        return JSONResponse({"schedule": get_posting_schedule(), "stats": get_stats()})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/marketplace/generate")
async def marketplace_generate(request: Request):
    """
    Generate a ready-to-post listing with randomized content.
    Body: {"platform": "fb_marketplace"|"craigslist", "variation": "", "region": ""}
    """
    try:
        from integrations.marketplace import generate_listing, can_post
        body = await request.json()
        platform = body.get("platform", "fb_marketplace")
        variation = body.get("variation", "")
        region = body.get("region", "")

        # Check frequency safety
        safety = can_post(platform, region)
        listing = generate_listing(platform=platform, variation=variation, region=region)
        return JSONResponse({"listing": listing, "safety": safety})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/marketplace/record")
async def marketplace_record(request: Request):
    """
    Record that a listing was posted (after manual posting).
    Body: {"listing": {...}, "post_url": "", "notes": ""}
    """
    try:
        from integrations.marketplace import record_post
        body = await request.json()
        listing = body.get("listing", {})
        post_url = body.get("post_url", "")
        notes = body.get("notes", "")
        row_id = record_post(listing, post_url=post_url, notes=notes)
        return JSONResponse({"ok": True, "post_id": row_id})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/marketplace/{post_id}/renew")
async def marketplace_renew(post_id: int):
    """Record that a marketplace listing was renewed."""
    try:
        from integrations.marketplace import record_renewal
        ok = record_renewal(post_id)
        return JSONResponse({"ok": ok, "message": "Renewed" if ok else "Renewal limit reached"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/api/marketplace/{post_id}/interaction")
async def marketplace_interaction(post_id: int, request: Request):
    """
    Log an interaction on a marketplace post.
    Body: {"type": "inquiry"|"message"|"view", "from_phone": "", "from_name": "", "message": ""}
    """
    try:
        from integrations.marketplace import record_interaction
        body = await request.json()
        row_id = record_interaction(
            post_id=post_id,
            interaction_type=body.get("type", "inquiry"),
            from_phone=body.get("from_phone", ""),
            from_name=body.get("from_name", ""),
            message=body.get("message", ""),
            lead_id=body.get("lead_id"),
            notes=body.get("notes", ""),
        )
        return JSONResponse({"ok": True, "interaction_id": row_id})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/marketplace/posts")
async def marketplace_posts(platform: str = ""):
    """Get all active marketplace posts, optionally filtered by platform."""
    try:
        from integrations.marketplace import get_active_posts
        return JSONResponse({"posts": get_active_posts(platform)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/marketplace/{post_id}/interactions")
async def marketplace_post_interactions(post_id: int):
    """Get all interactions for a specific marketplace post."""
    try:
        from integrations.marketplace import get_post_interactions
        return JSONResponse({"interactions": get_post_interactions(post_id)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/marketplace/stats")
async def marketplace_stats():
    """Get marketplace posting statistics."""
    try:
        from integrations.marketplace import get_stats
        return JSONResponse(get_stats())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Facebook Page Endpoints ───────────────────────────────────────────────

@app.get("/api/fb/page/info")
async def fb_page_info():
    """Get current Facebook page details."""
    try:
        from integrations.fb_page import get_page_info
        info = await get_page_info()
        return JSONResponse(info)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/fb/page/update")
async def fb_page_update(request: Request):
    """Update Facebook page fields (about, bio, description, etc.)."""
    try:
        from integrations.fb_page import update_page_info
        body = await request.json()
        result = await update_page_info(body)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/fb/page/overhaul-preview")
async def fb_page_overhaul_preview():
    """Preview the optimized page content before applying."""
    try:
        from integrations.fb_page import get_optimized_page_content
        return JSONResponse(get_optimized_page_content())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/fb/page/posts")
async def fb_page_posts(limit: int = 10):
    """Get recent Facebook page posts with engagement."""
    try:
        from integrations.fb_page import get_recent_posts
        posts = await get_recent_posts(limit)
        return JSONResponse({"posts": posts})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/fb/page/insights")
async def fb_page_insights(period: str = "day"):
    """Get page insights (reach, engagement, followers)."""
    try:
        from integrations.fb_page import get_page_insights
        insights = await get_page_insights(period)
        return JSONResponse(insights)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/fb/page/post")
async def fb_page_create_post(request: Request):
    """Create a new page post. Body: {"message": "...", "link": ""}"""
    try:
        from integrations.fb_page import create_post
        body = await request.json()
        result = await create_post(
            message=body.get("message", ""),
            link=body.get("link", ""),
            published=body.get("published", True),
        )
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/fb/page/organic-content")
async def fb_page_organic_content(post_type: str = ""):
    """Get the next organic post content to publish."""
    try:
        from integrations.fb_page import get_next_organic_post
        return JSONResponse(get_next_organic_post(post_type))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Organic Engagement Endpoints ─────────────────────────────────────────

@app.get("/api/organic/plan")
async def organic_daily_plan():
    """Get today's organic engagement plan with suggested actions."""
    try:
        from integrations.organic_engagement import get_todays_plan
        return JSONResponse(get_todays_plan())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/organic/log")
async def organic_log_engagement(request: Request):
    """
    Log an organic engagement action.
    Body: {"type": "group_helpful_reply", "group_name": "", "content": "", "post_url": ""}
    """
    try:
        from integrations.organic_engagement import log_engagement
        body = await request.json()
        row_id = log_engagement(
            engagement_type=body.get("type", ""),
            group_name=body.get("group_name", ""),
            post_url=body.get("post_url", ""),
            content=body.get("content", ""),
            response_content=body.get("response_content", ""),
            lead_id=body.get("lead_id"),
            tags=body.get("tags", []),
            notes=body.get("notes", ""),
        )
        return JSONResponse({"ok": True, "engagement_id": row_id})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/organic/stats")
async def organic_stats(days: int = 30):
    """Get organic engagement statistics with 10:1 ratio health."""
    try:
        from integrations.organic_engagement import get_engagement_stats
        return JSONResponse(get_engagement_stats(days))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/organic/recent")
async def organic_recent(limit: int = 20):
    """Get recent organic engagement activity."""
    try:
        from integrations.organic_engagement import get_recent_engagements
        return JSONResponse({"engagements": get_recent_engagements(limit)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/organic/helpful-comment")
async def organic_helpful_comment(topic: str = ""):
    """Get a helpful (non-salesy) comment suggestion for a group."""
    try:
        from integrations.organic_engagement import get_helpful_comment, HELPFUL_COMMENTS
        topics = list(HELPFUL_COMMENTS.keys())
        return JSONResponse({
            "comment": get_helpful_comment(topic),
            "topic": topic or "random",
            "available_topics": [t.replace("_", " ").title() for t in topics],
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Booking API endpoints moved to core/booking_api.py ────────────────────────
# Old endpoints (/api/bookings, /api/bookings/availability, /api/bookings/create,
# /api/bookings/pipeline) are now registered via register_booking_routes() above.

# ── Review System API endpoints ──────────────────────────────────────────────

@app.get("/api/reviews/stats")
async def api_review_stats():
    """Get review system statistics."""
    try:
        from core.review_system import get_review_stats
        return JSONResponse(get_review_stats())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/reviews/request")
async def api_create_review_request(request: Request):
    """Create a review request for a lead."""
    try:
        body = await request.json()
        from core.review_system import create_review_request
        result = create_review_request(
            lead_id=body.get("lead_id"),
            booking_id=body.get("booking_id"),
            channel=body.get("channel", "email"),
        )
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/reviews/pending")
async def api_pending_reviews():
    """Get reviews requested but not yet received."""
    try:
        from core.review_system import get_pending_reviews
        return JSONResponse({"pending": get_pending_reviews()})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ── Email Parser API endpoints ───────────────────────────────────────────────

@app.get("/api/email-parser/stats")
async def api_email_parser_stats():
    """Get email parser statistics."""
    try:
        from integrations.email_lead_parser import get_email_parser_stats
        return JSONResponse(get_email_parser_stats())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ── AI Classifier API endpoint ───────────────────────────────────────────────

@app.post("/api/ai/classify")
async def api_classify_message(request: Request):
    """Classify a message using the AI classifier."""
    try:
        body = await request.json()
        from core.ai_classifier import classify_message
        result = await classify_message(body.get("text", ""))
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Analytics Dashboard API endpoints ─────────────────────────────────────────

@app.get("/api/analytics/dashboard")
async def api_analytics_dashboard():
    """Get full analytics dashboard data."""
    try:
        from core.analytics_dashboard import get_dashboard_data
        import json
        data = get_dashboard_data(30)
        # Ensure JSON-serializable (catches datetime objects etc.)
        return JSONResponse(json.loads(json.dumps(data, default=str)))
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/analytics/revenue")
async def api_analytics_revenue():
    """Get revenue metrics."""
    try:
        from core.analytics_dashboard import get_revenue_metrics
        return JSONResponse(get_revenue_metrics(30))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/analytics/funnel")
async def api_analytics_funnel():
    """Get conversion funnel data."""
    try:
        from core.analytics_dashboard import get_conversion_metrics
        return JSONResponse(get_conversion_metrics(30))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/analytics/leads")
async def api_analytics_leads():
    """Get lead metrics and trends."""
    try:
        from core.analytics_dashboard import get_lead_metrics, get_lead_trend
        return JSONResponse({
            "metrics": get_lead_metrics(30),
            "trend": get_lead_trend(90),
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ── Meta CAPI Events API endpoints ───────────────────────────────────────────

@app.get("/api/capi/stats")
async def api_capi_stats():
    """Get CAPI event statistics."""
    try:
        from integrations.meta_capi_events import get_event_stats
        return JSONResponse(get_event_stats(30))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/capi/recent")
async def api_capi_recent():
    """Get recent CAPI events."""
    try:
        from integrations.meta_capi_events import get_recent_events
        return JSONResponse({"events": get_recent_events(20)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ── Google Business Profile API endpoints ─────────────────────────────────────

@app.get("/api/gbp/stats")
async def api_gbp_stats():
    """Get GBP posting statistics."""
    try:
        from integrations.google_business import get_posting_stats
        return JSONResponse(get_posting_stats(30))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ── Competitor Monitor API endpoints ─────────────────────────────────────────

@app.get("/api/competitors")
async def api_competitors():
    """Get tracked competitor list."""
    try:
        from core.competitor_monitor import get_competitor_list
        return JSONResponse({"competitors": get_competitor_list()})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/competitors/alerts")
async def api_competitor_alerts():
    """Get unseen competitor alerts."""
    try:
        from core.competitor_monitor import get_competitor_alerts
        return JSONResponse({"alerts": get_competitor_alerts(unseen_only=True)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/competitors/intel")
async def api_competitor_intel():
    """Get market intelligence summary."""
    try:
        from core.competitor_monitor import get_market_intel
        return JSONResponse(get_market_intel())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ── Facebook Messenger API endpoints ─────────────────────────────────────────

@app.get("/api/messenger/stats")
async def api_messenger_stats():
    """Get Messenger conversation stats."""
    try:
        from integrations.fb_messenger import get_messenger_stats
        return JSONResponse(get_messenger_stats())
    except Exception:
        return JSONResponse({"total": 0, "unread": 0, "conversations": 0, "status": "unavailable"})

@app.get("/api/messenger/conversations")
async def api_messenger_conversations():
    """Get recent Messenger conversations."""
    try:
        from integrations.fb_messenger import get_conversations
        return JSONResponse({"conversations": get_conversations(20)})
    except Exception:
        return JSONResponse({"conversations": [], "total": 0, "status": "unavailable"})

# ── SEO Tools API endpoints ──────────────────────────────────────────────────

@app.get("/api/seo/stats")
async def api_seo_stats():
    """Get SEO page generation stats."""
    try:
        from core.seo_tools import get_seo_stats
        return JSONResponse(get_seo_stats())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/seo/sitemap")
async def api_seo_sitemap():
    """Get generated sitemap XML."""
    try:
        from core.seo_tools import generate_sitemap
        from fastapi.responses import Response
        return Response(content=generate_sitemap(), media_type="application/xml")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/seo/schema/faq")
async def api_seo_faq_schema():
    """Get FAQ structured data (JSON-LD)."""
    try:
        from core.seo_tools import generate_faq_schema
        return Response(content=generate_faq_schema(), media_type="application/ld+json")
    except Exception:
        import json as _json
        fallback = {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": "What is a luxury restroom trailer?",
                 "acceptedAnswer": {"@type": "Answer", "text": "A luxury restroom trailer is a mobile restroom with flushing toilets, running water, climate control, and premium finishes — a major upgrade from standard portable toilets."}},
                {"@type": "Question", "name": "What events do you serve?",
                 "acceptedAnswer": {"@type": "Answer", "text": "Weddings, quinceañeras, corporate events, backyard parties, festivals, and film productions in the San Fernando Valley and Greater LA."}},
                {"@type": "Question", "name": "What is included in the rental?",
                 "acceptedAnswer": {"@type": "Answer", "text": "Delivery, setup, and pickup are all included. The trailer features flushing toilets, running water, AC/heat, interior lighting, and multiple stalls."}},
            ],
        }
        return Response(content=_json.dumps(fallback), media_type="application/ld+json")

@app.get("/api/seo/schema/local-business")
async def api_seo_local_biz_schema():
    """Get LocalBusiness structured data (JSON-LD)."""
    try:
        from core.seo_tools import generate_local_business_schema
        return Response(content=generate_local_business_schema(), media_type="application/ld+json")
    except Exception:
        import json as _json
        fallback = {
            "@context": "https://schema.org",
            "@type": "LocalBusiness",
            "name": "Zoar Bathroom Rentals",
            "description": "Luxury restroom trailer rental for weddings, quinceañeras, and events in the San Fernando Valley and Greater LA.",
            "telephone": "+1-818-396-8183",
            "email": "zoarbathrooms@gmail.com",
            "areaServed": "San Fernando Valley, Greater Los Angeles",
            "address": {"@type": "PostalAddress", "addressLocality": "San Fernando", "addressRegion": "CA", "postalCode": "91340"},
            "serviceType": "Luxury Restroom Trailer Rental",
        }
        return Response(content=_json.dumps(fallback), media_type="application/ld+json")

# ── Retargeting Audience API endpoints ───────────────────────────────────────

@app.get("/api/retargeting/audiences")
async def api_retargeting_audiences():
    """Get all retargeting audiences with stats."""
    try:
        from core.retargeting import get_audience_stats
        return JSONResponse({"audiences": get_audience_stats()})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/retargeting/summary")
async def api_retargeting_summary():
    """Get formatted retargeting summary."""
    try:
        from core.retargeting import format_audience_summary
        return JSONResponse({"summary": format_audience_summary()})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ── Re-engagement API endpoints ──────────────────────────────────────────────

@app.get("/api/reengagement/campaigns")
async def api_reengagement_campaigns():
    """Get all re-engagement campaigns."""
    try:
        from core.reengagement import get_campaign_stats
        return JSONResponse({"campaigns": get_campaign_stats()})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/reengagement/targets")
async def api_reengagement_targets():
    """Get re-engagement target counts by segment."""
    try:
        from core.reengagement import identify_reengagement_targets
        return JSONResponse(identify_reengagement_targets())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/reengagement/pending")
async def api_reengagement_pending():
    """Get touches pending approval."""
    try:
        from core.reengagement import get_pending_approval_touches
        return JSONResponse({"pending": get_pending_approval_touches()})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ── Media Manager API endpoints ──────────────────────────────────────────────

@app.get("/api/media/stats")
async def api_media_stats():
    """Get media library statistics."""
    try:
        from core.media_manager import get_media_stats
        return JSONResponse(get_media_stats())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/media/search")
async def api_media_search(q: str = ""):
    """Search media assets."""
    try:
        from core.media_manager import search_media
        return JSONResponse({"results": search_media(q)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/media/collections")
async def api_media_collections():
    """Get all media collections."""
    try:
        from core.media_manager import get_collections
        return JSONResponse({"collections": get_collections()})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ── Wave 6: Approval System API ──────────────────────────────────────────────

@app.get("/api/approvals")
async def api_approvals():
    """Get all pending approvals."""
    try:
        from core.approval_system import get_all_pending, get_approval_stats
        return JSONResponse({"pending": get_all_pending(), "stats": get_approval_stats()})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/approvals/{approval_id}/approve", dependencies=[Depends(verify_action_auth)])
async def api_approve(approval_id: int, request: Request):
    """Approve a message via API."""
    try:
        from core.approval_system import approve
        from core.lead_pipeline import get_pipeline
        body = await request.json() if request.headers.get("content-type") == "application/json" else {}
        custom_text = body.get("custom_text")
        result = approve(approval_id, custom_text)
        if not result:
            return JSONResponse({"error": "Not found"}, status_code=404)
        pipeline = get_pipeline()
        if pipeline:
            send_result = await pipeline.execute_approved_message(result)
            return JSONResponse({"approval": result, "send_result": send_result})
        return JSONResponse({"approval": result})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/approvals/{approval_id}/skip")
async def api_skip(approval_id: int):
    """Skip a message via API."""
    try:
        from core.approval_system import skip
        result = skip(approval_id)
        return JSONResponse({"approval": result}) if result else JSONResponse({"error": "Not found"}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ── Wave 6: Posting Engine API ────────────────────────────────────────────────

@app.get("/api/posting/status")
async def api_posting_status():
    """Get posting engine status."""
    try:
        from integrations.posting_engine import get_posting_status
        return JSONResponse(get_posting_status())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/posting/preview")
async def api_posting_preview():
    """Preview next posting angle."""
    try:
        from integrations.posting_engine import handle_post_preview_command
        return JSONResponse({"preview": handle_post_preview_command("/post_preview")})
    except Exception:
        return JSONResponse({"preview": None, "status": "unavailable"})

# ── Wave 6: Video Generator API ──────────────────────────────────────────────

@app.get("/api/videos/stats")
async def api_video_stats():
    """Get video generation stats."""
    try:
        from integrations.video_generator import get_video_stats
        return JSONResponse(get_video_stats())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ── Wave 6: FB Group Scraper API ─────────────────────────────────────────────

@app.get("/api/scraper/stats")
async def api_scraper_stats():
    """Get scraper stats."""
    try:
        from integrations.fb_group_scraper import get_scrape_stats
        return JSONResponse(get_scrape_stats())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/scraper/leads")
async def api_scraper_leads(min_score: int = 5):
    """Get scraped leads above score threshold."""
    try:
        from integrations.fb_group_scraper import get_hot_leads, get_warm_leads
        if min_score >= 8:
            return JSONResponse({"leads": get_hot_leads(min_score)})
        return JSONResponse({"leads": get_warm_leads(min_score)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/scraper/groups")
async def api_scraper_groups():
    """Get monitored groups."""
    try:
        from integrations.fb_group_scraper import get_groups
        return JSONResponse({"groups": get_groups()})
    except Exception:
        return JSONResponse({"groups": [], "total": 0, "status": "unavailable"})

# ── Wave 6: Browser Agent API ────────────────────────────────────────────────

@app.get("/api/browser/stats")
async def api_browser_stats():
    """Get browser agent session stats."""
    try:
        from integrations.browser_agent import get_session_stats
        return JSONResponse(get_session_stats())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ── Wave 6: Task Queue API ───────────────────────────────────────────────────

# NOTE: Keep /api/tasks reserved for the coordination task board routes above.
# Fleet task queue status lives under /api/task-queue.
@app.get("/api/task-queue")
@app.get("/api/tasks/queue-status")
async def api_task_queue_status():
    """Get fleet task queue status."""
    try:
        from core.task_queue import get_queue_status
        return JSONResponse(get_queue_status())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/task-queue/stats")
@app.get("/api/tasks/stats")
@app.get("/api/tasks/queue-stats")
async def api_task_queue_stats(days: int = 7):
    """Get fleet task completion stats."""
    try:
        from core.task_queue import get_task_stats
        return JSONResponse(get_task_stats(days))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Dossier API (Deep Research Pipeline) ─────────────────────────────────────

@app.get("/api/dossiers")
async def api_dossiers(
    min_fit: int = 0,
    partnership_type: str = None,
    outreach_status: str = None,
    limit: int = 50,
):
    """List research dossiers with optional filtering."""
    import sqlite3 as _sql3
    conn = _sql3.connect(str(Path.home() / ".nexus" / "memory.db"))
    conn.row_factory = _sql3.Row
    try:
        clauses = ["fit_score >= ?"]
        params = [min_fit]
        if partnership_type:
            clauses.append("partnership_type = ?")
            params.append(partnership_type)
        if outreach_status:
            clauses.append("outreach_status = ?")
            params.append(outreach_status)
        where = " AND ".join(clauses)
        params.append(limit)
        rows = conn.execute(
            f"SELECT id, lead_id, lead_type, source_table, contact_name, business_name, "
            f"phone, email, website, location, in_service_area, partnership_type, "
            f"fit_score, fit_reasoning, approach, research_confidence, outreach_status, "
            f"created_at FROM lead_dossiers WHERE {where} "
            f"ORDER BY fit_score DESC, research_confidence DESC LIMIT ?",
            params,
        ).fetchall()
        return JSONResponse([dict(r) for r in rows])
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        conn.close()


@app.get("/api/dossiers/stats")
async def api_dossier_stats():
    """Pipeline stats for the deep research system."""
    import sqlite3 as _sql3
    conn = _sql3.connect(str(Path.home() / ".nexus" / "memory.db"))
    conn.row_factory = _sql3.Row
    try:
        total = conn.execute("SELECT count(*) FROM lead_dossiers").fetchone()[0]
        high_fit = conn.execute("SELECT count(*) FROM lead_dossiers WHERE fit_score >= 75").fetchone()[0]
        pending = conn.execute("SELECT count(*) FROM lead_dossiers WHERE outreach_status = 'pending'").fetchone()[0]
        approved = conn.execute("SELECT count(*) FROM lead_dossiers WHERE outreach_status = 'approved'").fetchone()[0]
        sent = conn.execute("SELECT count(*) FROM lead_dossiers WHERE outreach_status = 'sent'").fetchone()[0]
        avg_confidence = conn.execute("SELECT avg(research_confidence) FROM lead_dossiers").fetchone()[0] or 0
        # Pending research tasks in fleet queue
        research_queue = conn.execute(
            "SELECT count(*) FROM fleet_tasks WHERE task_type = 'deep_lead_research' AND status IN ('pending', 'claimed', 'in_progress')"
        ).fetchone()[0]
        return JSONResponse({
            "total_dossiers": total,
            "high_fit_75plus": high_fit,
            "pending_outreach": pending,
            "approved": approved,
            "sent": sent,
            "avg_confidence": round(avg_confidence, 1),
            "research_queue": research_queue,
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        conn.close()


@app.get("/api/dossiers/{dossier_id}")
async def api_dossier_detail(dossier_id: int):
    """Full dossier detail including complete research JSON."""
    import sqlite3 as _sql3
    conn = _sql3.connect(str(Path.home() / ".nexus" / "memory.db"))
    conn.row_factory = _sql3.Row
    try:
        row = conn.execute("SELECT * FROM lead_dossiers WHERE id = ?", (dossier_id,)).fetchone()
        if not row:
            return JSONResponse({"error": "Dossier not found"}, status_code=404)
        result = dict(row)
        # Parse JSON fields
        for field in ("services", "event_types", "full_dossier"):
            if result.get(field):
                try:
                    result[field] = json.loads(result[field])
                except Exception:
                    pass
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        conn.close()


@app.post("/api/dossiers/{dossier_id}/approve", dependencies=[Depends(verify_action_auth)])
async def api_dossier_approve(dossier_id: int):
    """Approve a dossier for outreach."""
    import sqlite3 as _sql3
    conn = _sql3.connect(str(Path.home() / ".nexus" / "memory.db"))
    try:
        conn.execute(
            "UPDATE lead_dossiers SET outreach_status = 'approved', updated_at = datetime('now') WHERE id = ?",
            (dossier_id,),
        )
        conn.commit()
        return JSONResponse({"status": "ok", "dossier_id": dossier_id, "outreach_status": "approved"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        conn.close()


# ── Hunter Stats API ──────────────────────────────────────────────────────────

@app.get("/api/hunter/stats")
async def api_hunter_stats():
    """Return FB lead hunter pipeline stats for the Research Pipeline UI."""
    import sqlite3 as _sql3
    import json as _json
    db = str(Path.home() / ".nexus" / "memory.db")
    conn = _sql3.connect(db)
    conn.row_factory = _sql3.Row
    try:
        # Groups configured
        groups_file = Path(__file__).parent / "scrapers" / "fb_vendor_scraper" / "groups.json"
        groups_configured = 0
        groups_scraped_today = 0
        if groups_file.exists():
            groups = _json.loads(groups_file.read_text())
            groups_configured = len(groups)
            today = datetime.now().strftime("%Y-%m-%d")
            groups_scraped_today = sum(
                1 for g in groups
                if (g.get("last_scraped") or "").startswith(today)
            )
        # Today's leads
        today_start = datetime.now().strftime("%Y-%m-%d 00:00:00")
        event_row = conn.execute(
            "SELECT COUNT(*) as c FROM event_leads WHERE created_at >= ?", (today_start,)
        ).fetchone()
        referral_row = conn.execute(
            "SELECT COUNT(*) as c FROM referral_leads WHERE created_at >= ?", (today_start,)
        ).fetchone()
        # Agent runtime status
        agent_state_file = Path.home() / ".nexus" / "agent_state" / "fb-lead-hunter.json"
        is_running = False
        session_type = "standard"
        last_run = ""
        if agent_state_file.exists():
            try:
                state = _json.loads(agent_state_file.read_text())
                last_hb = state.get("last_heartbeat", "")
                if last_hb:
                    from datetime import timedelta
                    hb_time = datetime.fromisoformat(last_hb.replace("Z", "+00:00")) if "T" in last_hb else datetime.strptime(last_hb, "%Y-%m-%d %H:%M:%S")
                    is_running = (datetime.now() - hb_time.replace(tzinfo=None)) < timedelta(minutes=5)
                    last_run = last_hb
                session_type = state.get("current_task", "standard")
                if "marathon" in session_type.lower():
                    session_type = "marathon"
                else:
                    session_type = "standard"
            except Exception:
                pass
        return JSONResponse({
            "groups_configured": groups_configured,
            "groups_scraped_today": groups_scraped_today,
            "is_running": is_running,
            "session_type": session_type,
            "event_leads_today": event_row["c"] if event_row else 0,
            "referral_leads_today": referral_row["c"] if referral_row else 0,
            "last_run": last_run,
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        conn.close()


# ── Ad System API ─────────────────────────────────────────────────────────────

@app.get("/api/ads/creatives")
async def api_ads_creatives(status: str = None):
    """Get all ad creatives, optionally filtered by status."""
    try:
        from core.ad_engine import AdEngine
        engine = AdEngine()
        if status:
            import sqlite3 as _sql3
            conn = _sql3.connect(str(Path.home() / ".nexus" / "memory.db"))
            conn.row_factory = _sql3.Row
            rows = conn.execute("SELECT * FROM ad_creatives WHERE status = ? ORDER BY id DESC", (status,)).fetchall()
            conn.close()
            return JSONResponse([dict(r) for r in rows])
        return JSONResponse(engine.get_active_creatives())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/ads/winners")
async def api_ads_winners():
    """Get winning ad creatives."""
    try:
        from core.ad_engine import AdEngine
        return JSONResponse(AdEngine().get_winners())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/ads/fatigue/{creative_id}")
async def api_ads_fatigue(creative_id: int):
    """Check if a creative is fatigued."""
    try:
        from core.ad_engine import AdEngine
        return JSONResponse(AdEngine().check_fatigue(creative_id))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/ads/performance-report")
async def api_ads_performance_report():
    """Generate ad performance report."""
    try:
        from core.ad_engine import AdEngine
        return JSONResponse({"report": AdEngine().generate_performance_report()})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/ads/copy-library")
async def api_ads_copy_library(audience: str = None, category: str = None):
    """Get ad copy entries from library."""
    try:
        from core.ad_engine import AdEngine
        return JSONResponse(AdEngine().get_copy_for_audience(audience or "wedding_en", category))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/ads/test-results")
async def api_ads_test_results():
    """Get A/B test results."""
    try:
        from core.ad_engine import AdEngine
        return JSONResponse(AdEngine().get_test_results())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ── Lead Pipeline API ─────────────────────────────────────────────────────────

@app.get("/api/scoring/leads")
async def api_leads_scored(tier: str = None):
    """Get leads with scores, optionally filtered by tier."""
    try:
        from core.lead_scoring import LeadScorer
        return JSONResponse(LeadScorer().get_leads_by_tier(tier))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/leads/{lead_id}/score")
async def api_lead_score(lead_id: int):
    """Get detailed score breakdown for a lead."""
    try:
        from core.lead_scoring import LeadScorer
        return JSONResponse(LeadScorer().score_lead(lead_id))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/scoring/rescore-all", dependencies=[Depends(verify_action_auth)])
async def api_leads_rescore():
    """Recalculate all lead scores."""
    try:
        from core.lead_scoring import LeadScorer
        return JSONResponse(LeadScorer().update_all_scores())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ── Follow-Up Queue API ──────────────────────────────────────────────────────

@app.get("/api/followup/pending")
async def api_followup_pending():
    """Get pending follow-up proposals."""
    try:
        from core.follow_up_engine import FollowUpEngine
        return JSONResponse(FollowUpEngine().get_pending_followups())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/followup/{lead_id}")
async def api_followup_status(lead_id: int):
    """Get follow-up sequence status for a lead."""
    try:
        from core.follow_up_engine import FollowUpEngine
        return JSONResponse(FollowUpEngine().get_sequence_status(lead_id))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/followup/{lead_id}/pause", dependencies=[Depends(verify_action_auth)])
async def api_followup_pause(lead_id: int):
    """Pause follow-up sequence for a lead."""
    try:
        from core.follow_up_engine import FollowUpEngine
        return JSONResponse(FollowUpEngine().pause_sequence(lead_id))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/followup/{lead_id}/resume", dependencies=[Depends(verify_action_auth)])
async def api_followup_resume(lead_id: int):
    """Resume follow-up sequence for a lead."""
    try:
        from core.follow_up_engine import FollowUpEngine
        return JSONResponse(FollowUpEngine().resume_sequence(lead_id))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/followup/process", dependencies=[Depends(verify_action_auth)])
async def api_followup_process():
    """Process all pending follow-ups (creates approval proposals)."""
    try:
        from core.follow_up_engine import FollowUpEngine
        return JSONResponse({"proposals": FollowUpEngine().process_pending()})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ── Posting Engine API ────────────────────────────────────────────────────────

@app.get("/api/posting/today")
async def api_posting_today():
    """Get today's daily posting content."""
    try:
        from core.posting_engine import PostingEngine
        engine = PostingEngine()
        posts = engine.generate_daily_posts()
        return JSONResponse({"posts": posts, "slack_message": engine.format_slack_message(posts)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/posting/calendar")
async def api_posting_calendar():
    """Get posting calendar / history."""
    try:
        from core.posting_engine import PostingEngine
        return JSONResponse({"calendar": PostingEngine().get_posting_calendar()})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/posting/history")
async def api_posting_history(days: int = 30):
    """Get posting history."""
    try:
        from core.posting_engine import PostingEngine
        return JSONResponse(PostingEngine().get_posting_history(days))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ── Revenue Tracker API ──────────────────────────────────────────────────────

@app.get("/api/revenue/summary")
async def api_revenue_summary():
    """Revenue and booking summary."""
    try:
        import sqlite3 as _sql3
        conn = _sql3.connect(str(Path.home() / ".nexus" / "memory.db"))
        conn.row_factory = _sql3.Row
        bookings = conn.execute("""
            SELECT booking_status, COUNT(*) as count
            FROM leads WHERE booking_status != 'new_lead'
            GROUP BY booking_status
        """).fetchall()
        total_leads = conn.execute("SELECT COUNT(*) FROM leads WHERE status != 'recovered'").fetchone()[0]
        total_bookings = conn.execute("SELECT COUNT(*) FROM leads WHERE booking_status = 'booked'").fetchone()[0]
        conn.close()
        return JSONResponse({
            "total_leads": total_leads,
            "total_bookings": total_bookings,
            "revenue_estimate": total_bookings * 1100,
            "bookings_by_status": [dict(r) for r in bookings],
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ── CAPI Events Log API ──────────────────────────────────────────────────────

@app.get("/api/capi/status")
async def api_capi_status():
    """CAPI event counts and status."""
    try:
        from integrations.meta_capi_events import get_capi_status
        return JSONResponse(get_capi_status())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/capi/events")
async def api_capi_events(limit: int = 50):
    """Recent CAPI events log."""
    try:
        import sqlite3 as _sql3
        conn = _sql3.connect(str(Path.home() / ".nexus" / "memory.db"))
        conn.row_factory = _sql3.Row
        rows = conn.execute(
            "SELECT * FROM capi_events_log ORDER BY id DESC LIMIT ?", (min(limit, 100),)
        ).fetchall()
        conn.close()
        return JSONResponse([dict(r) for r in rows])
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ── Meta Ads API ──────────────────────────────────────────────────────────────

@app.get("/api/meta/campaigns")
async def api_meta_campaigns():
    """List Meta ad campaigns."""
    try:
        from core.meta_ads import MetaAdsManager
        return JSONResponse(MetaAdsManager().get_campaigns())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/meta/account")
async def api_meta_account():
    """Get Meta ad account info."""
    try:
        from core.meta_ads import MetaAdsManager
        return JSONResponse(MetaAdsManager().get_account_info())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/meta/evaluate-rules", dependencies=[Depends(verify_action_auth)])
async def api_meta_evaluate_rules():
    """Evaluate automated ad rules (pause high CPL, scale winners, etc.)."""
    try:
        from core.meta_ads import MetaAdsManager
        return JSONResponse({"actions": MetaAdsManager().evaluate_automated_rules()})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ── Pixel Implementation ─────────────────────────────────────────────────────

@app.get("/api/pixel/code")
async def api_pixel_code():
    """Get the Facebook Pixel implementation code."""
    try:
        pixel_file = Path(__file__).parent / "static" / "pixel.html"
        if pixel_file.exists():
            return JSONResponse({"pixel_id": "1579580876639654", "code": pixel_file.read_text()})
        return JSONResponse({"error": "Pixel file not found"}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ── C1: n8n Webhook Endpoint — Lead Ingestion ────────────────────────────────

@app.post("/api/webhook/n8n-lead", dependencies=[Depends(verify_action_auth)])
async def webhook_n8n_lead(request: Request):
    """Ingest leads from n8n workflow (Facebook Lead Ads -> n8n -> Nexus).
    Body: {source, form_id, name, email, phone, event_type, event_date, guest_count, location, raw_data}
    Returns: {ok, lead_id, score}"""
    try:
        body = await request.json()
        import sqlite3 as _sql3
        db = Path.home() / ".nexus" / "memory.db"
        conn = _sql3.connect(str(db))
        conn.row_factory = _sql3.Row
        full_name = body.get("name", "").strip()
        parts = full_name.split(None, 1)
        first_name = parts[0] if parts else ""
        last_name = parts[1] if len(parts) > 1 else ""
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("""
            INSERT INTO leads (first_name, last_name, full_name, email, phone, source,
                             status, booking_status, date_added, discovered_at)
            VALUES (?, ?, ?, ?, ?, ?, 'new', 'new_lead', ?, ?)
        """, (first_name, last_name, full_name, body.get("email", ""),
              body.get("phone", ""), body.get("source", "n8n_webhook"), now, now))
        lead_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()
        print(f"[n8n] New lead #{lead_id}: {full_name} ({body.get('source', 'n8n')})")

        # Record consent
        try:
            from core.tcpa import record_consent
            record_consent(body.get("phone", ""), body.get("email", ""),
                         "inquiry_based", body.get("source", "n8n_webhook"))
        except Exception as e:
            print(f"[n8n] Consent record error: {e}")

        # Send CAPI Lead event
        try:
            from integrations.meta_capi_events import send_lead_event
            send_lead_event({"email": body.get("email", ""), "phone": body.get("phone", ""),
                           "first_name": first_name, "last_name": last_name, "lead_id": lead_id})
        except Exception as e:
            print(f"[n8n] CAPI Lead event error: {e}")

        # Calculate lead score
        score = 0
        try:
            from core.lead_scoring import LeadScorer
            result = LeadScorer().score_lead(lead_id)
            score = result.get("score", 0)
        except Exception as e:
            print(f"[n8n] Lead scoring error: {e}")

        # Start lead pipeline (replaces direct follow-up)
        try:
            from core.lead_pipeline import LeadPipeline
            pipeline = LeadPipeline()
            await pipeline.process_new_lead(lead_id)
        except Exception as e:
            print(f"[Pipeline] n8n lead trigger error: {e}")

        # Notify Slack
        try:
            from integrations.slack_notify import send_slack
            await send_slack(
                f"*New Lead* | {full_name} | {body.get('phone', '')} | "
                f"{body.get('event_type', 'N/A')} on {body.get('event_date', 'N/A')} | Score: {score}",
                channel="leads")
        except Exception as e:
            print(f"[n8n] Slack notify error: {e}")

        return JSONResponse({"ok": True, "lead_id": lead_id, "score": score})
    except Exception as e:
        print(f"[n8n] Webhook error: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

# ── C3: GHL Integration Webhook (Stub) ───────────────────────────────────────

@app.post("/api/webhook/ghl")
async def webhook_ghl(request: Request):
    """GoHighLevel webhook receiver (stub). Logs incoming data."""
    try:
        body = await request.json()
        print(f"[GHL] Webhook received: {json.dumps(body)[:500]}")
        try:
            import sqlite3 as _sql3
            conn = _sql3.connect(str(Path.home() / ".nexus" / "memory.db"))
            conn.execute(
                "INSERT INTO security_log (event_type, details, severity, timestamp) VALUES (?, ?, ?, ?)",
                ("ghl_webhook", json.dumps(body)[:1000], "info", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit(); conn.close()
        except Exception:
            pass
        return JSONResponse({"ok": True, "message": "GHL webhook received"})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

# ── Objection Handler API ─────────────────────────────────────────────────────

@app.post("/api/objection/detect")
async def api_objection_detect(request: Request):
    """Detect objections in a lead message and suggest responses."""
    try:
        body = await request.json()
        from core.objection_handler import ObjectionHandler
        handler = ObjectionHandler()
        suggestions = handler.get_suggested_responses(body.get("message", ""), body.get("lead_data", {}))
        return JSONResponse({"suggestions": suggestions})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# ── Video Generator API ──────────────────────────────────────────────────────

@app.get("/api/video/prompts")
async def api_video_prompts():
    """Get AI video generation prompt library."""
    try:
        from core.video_generator import VideoGenerator
        gen = VideoGenerator()
        return JSONResponse({
            "prompts": {name: gen.get_prompt(name) for name in gen.list_prompts()},
            "guide": gen.generate_prompt_guide()
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Mount new Nexus Command Center dashboard ─────────────────────────────────
from fastapi.responses import FileResponse as _FR
_dashboard_v2_dir = os.path.join(os.path.dirname(__file__), "dashboard")
if os.path.exists(_dashboard_v2_dir):
    app.mount("/cmd/static", StaticFiles(directory=_dashboard_v2_dir), name="dashboard-v2-static")
    @app.get("/cmd")
    @app.get("/cmd/{rest:path}")
    async def serve_dashboard_v2(rest: str = ""):
        return _FR(os.path.join(_dashboard_v2_dir, "index.html"))


# ── B2B Cold Email Outreach API ───────────────────────────────────────────────

# ── FB Vendor Scraper Endpoints ───────────────────────────────────────────────

@app.post("/api/fb/vendor", dependencies=[Depends(verify_action_auth)])
async def api_fb_vendor(request: Request):
    """Receive vendor data from Mac scraper. Triggers Telegram notification on new vendors."""
    from core.fb_scraper_api import upsert_vendor, get_prospect
    body = await request.json()
    result = upsert_vendor(body)
    if result["action"] == "created":
        # Send real-time Telegram notification
        try:
            from telegram.bot import get_bot
            bot = get_bot()
            if bot:
                p = get_prospect(result["id"])
                if p:
                    if p.get("post_type") == "event_announcement":
                        msg = (
                            "🎯 EVENT LEAD FOUND\n"
                            "━━━━━━━━━━━━━━━━━━\n"
                            f"Posted by: {p['poster_name']}\n"
                            f"City: {p.get('city') or 'Unknown'}\n"
                            "━━━━━━━━━━━━━━━━━━\n"
                            f"Found in: {p.get('group_name', '?')}\n"
                            "━━━━━━━━━━━━━━━━━━\n"
                            f"Post: {(p.get('post_content', '') or '')[:300]}...\n"
                            "━━━━━━━━━━━━━━━━━━\n"
                            f"Profile: {p.get('profile_url', '—')}\n"
                            f"ID: #{p['id']}\n"
                            "This person is looking for event services — potential direct client!"
                        )
                    else:
                        msg = (
                            "🆕 NEW VENDOR FOUND\n"
                            "━━━━━━━━━━━━━━━━━━\n"
                            f"Name: {p['poster_name']}\n"
                            f"Business: {p.get('business_name') or 'Unknown'}\n"
                            f"Category: {p.get('category') or 'Unknown'}\n"
                            f"City: {p.get('city') or 'Unknown'}\n"
                            f"Phone: {p.get('phone') or 'Not found'}\n"
                            f"Website: {p.get('website') or 'Not found'}\n"
                            "━━━━━━━━━━━━━━━━━━\n"
                            f"Found in: {p.get('group_name', '?')}\n"
                            f"Post type: {'Vendor Promotion' if p.get('post_type') == 'vendor_promotion' else 'Event Announcement'}\n"
                            "━━━━━━━━━━━━━━━━━━\n"
                            f"Post preview: {(p.get('post_content', '') or '')[:200]}...\n"
                            "━━━━━━━━━━━━━━━━━━\n"
                            f"Profile: {p.get('profile_url', '—')}\n"
                            f"ID: #{p['id']}\n"
                            f"Use /fb_lead {p['id']} for full details"
                        )
                    await bot.send_to_all(msg)
        except Exception as e:
            print(f"[FB] Telegram notification error: {e}")
        # Broadcast to SSE clients (vendor-leads dashboard)
        try:
            p = get_prospect(result["id"])
            if p:
                await broadcast({"type": "vendor_new", "vendor": p})
        except Exception:
            pass
    elif result["action"] == "updated":
        try:
            p = get_prospect(result["id"])
            if p:
                await broadcast({"type": "vendor_updated", "vendor": p})
        except Exception:
            pass
    return JSONResponse({"ok": True, **result})


@app.get("/api/fb/prospects")
async def api_fb_prospects(request: Request):
    """JSON list of FB vendor prospects with optional filters.

    Merges legacy fb_vendor_prospects with referral_leads so the UI shows the
    complete pool instead of only the legacy scraper table.
    """
    from core.fb_scraper_api import get_prospects_scored
    params = dict(request.query_params)
    prospects = get_prospects_scored(**params)
    return JSONResponse(prospects)

# NOTE: Static sub-path must be before {prospect_id} catch-all
@app.get("/api/fb/prospects/scored")
async def api_fb_prospects_scored(request: Request):
    """Scored prospects with sort/filter/pagination."""
    from core.fb_scraper_api import get_prospects_scored
    params = dict(request.query_params)
    return JSONResponse(get_prospects_scored(**params))

@app.get("/api/fb/prospects/{prospect_id}")
async def api_fb_prospect_detail(prospect_id: int):
    """Full detail for a single FB prospect."""
    from core.fb_scraper_api import get_prospect
    p = get_prospect(prospect_id)
    if not p:
        return JSONResponse({"error": f"Prospect #{prospect_id} not found"}, status_code=404)
    return JSONResponse(p)


@app.patch("/api/fb/prospects/{prospect_id}", dependencies=[Depends(verify_action_auth)])
async def api_fb_prospect_update(prospect_id: int, request: Request):
    """Update fields on a FB prospect."""
    from core.fb_scraper_api import update_prospect
    body = await request.json()
    ok = update_prospect(prospect_id, body)
    if not ok:
        return JSONResponse({"error": "No valid fields to update"}, status_code=400)
    return JSONResponse({"ok": True, "updated": prospect_id})


@app.get("/api/fb/stats")
async def api_fb_stats():
    """FB vendor prospect summary statistics."""
    from core.fb_scraper_api import get_stats_scored
    return JSONResponse(get_stats_scored())

# /api/fb/prospects/scored moved before {prospect_id} catch-all (see ~line 8897)

@app.get("/api/fb/stats/scored")
async def api_fb_stats_scored():
    """Enhanced stats with score distribution."""
    from core.fb_scraper_api import get_stats_scored
    return JSONResponse(get_stats_scored())


@app.post("/api/fb/prospects/rescore", dependencies=[Depends(verify_action_auth)])
async def api_fb_rescore():
    """Recompute referral_score and activity_level for all prospects."""
    from core.fb_scraper_api import rescore_all
    count = rescore_all()
    return JSONResponse({"ok": True, "rescored": count})


@app.post("/api/fb/trigger_scrape", dependencies=[Depends(verify_action_auth)])
async def api_fb_trigger_scrape():
    """Forward start signal to Mac scraper."""
    import httpx
    cfg = load_config()
    mac_url = cfg.get("fb_scraper_mac_url", "http://localhost:7899")
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{mac_url}/start_scrape")
            return JSONResponse({"ok": True, "status": r.status_code})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)


@app.post("/api/fb/stop_scrape", dependencies=[Depends(verify_action_auth)])
async def api_fb_stop_scrape():
    """Forward stop signal to Mac scraper."""
    import httpx
    cfg = load_config()
    mac_url = cfg.get("fb_scraper_mac_url", "http://localhost:7899")
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{mac_url}/stop_scrape")
            return JSONResponse({"ok": True, "status": r.status_code})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)


@app.get("/api/fb/dashboard")
async def api_fb_dashboard(request: Request):
    """HTML dashboard showing all FB vendor prospects."""
    from core.fb_scraper_api import get_prospects, get_stats, render_dashboard
    params = dict(request.query_params)
    prospects = get_prospects(**params)
    stats = get_stats()
    return HTMLResponse(render_dashboard(prospects, stats))


@app.get("/api/b2b/dashboard")
async def api_b2b_dashboard():
    """HTML dashboard showing all B2B leads with color-coded statuses."""
    from core.b2b_leads import get_all_leads
    from core.cold_email_digest import get_batch_stats
    from core.b2b_dashboard import render_dashboard
    leads = get_all_leads()
    stats = get_batch_stats()
    return HTMLResponse(render_dashboard(leads, stats))


@app.get("/api/b2b/leads")
async def api_b2b_leads(request: Request):
    """JSON list of B2B leads with optional filters."""
    from core.b2b_leads import get_all_leads
    params = dict(request.query_params)
    leads = get_all_leads(**params)
    return JSONResponse(leads)


@app.get("/api/b2b/leads/{lead_id}")
async def api_b2b_lead_detail(lead_id: int):
    """Full detail for a single B2B lead."""
    from core.b2b_leads import get_lead
    lead = get_lead(lead_id)
    if not lead:
        return JSONResponse({"error": f"Lead #{lead_id} not found"}, status_code=404)
    return JSONResponse(lead)


@app.patch("/api/b2b/leads/{lead_id}", dependencies=[Depends(verify_action_auth)])
async def api_b2b_lead_update(lead_id: int, request: Request):
    """Update fields on a B2B lead."""
    from core.b2b_leads import update_lead
    body = await request.json()
    ok = update_lead(lead_id, body)
    if not ok:
        return JSONResponse({"error": "No valid fields to update"}, status_code=400)
    return JSONResponse({"ok": True, "updated": lead_id})


@app.post("/api/b2b/import", dependencies=[Depends(verify_action_auth)])
async def api_b2b_import():
    """Re-import B2B leads from the built-in dataset."""
    from core.b2b_import import import_leads
    result = import_leads()
    return JSONResponse({"ok": True, **result})


@app.get("/api/b2b/digest")
async def api_b2b_digest(request: Request):
    """Generate a batch of emails for approval (JSON)."""
    params = dict(request.query_params)
    batch_size = int(params.get("batch_size", 10))
    from core.cold_email_digest import generate_batch
    batch = generate_batch(min(batch_size, 15))
    # Strip HTML from response to keep payload small
    for item in batch:
        item.pop("html_body", None)
    return JSONResponse({"ok": True, "batch": batch, "count": len(batch)})


@app.get("/api/b2b/stats")
async def api_b2b_stats():
    """B2B outreach summary statistics."""
    from core.cold_email_digest import get_batch_stats
    return JSONResponse(get_batch_stats())


# ── Agent Hierarchy API ─────────────────────────────────────────────────────

@app.get("/api/hierarchy/tree")
async def api_hierarchy_tree():
    """Full org chart tree with live agent status."""
    from core.agent_hierarchy import get_hierarchy_tree, init_hierarchy_db
    try:
        init_hierarchy_db()
    except Exception:
        pass
    return JSONResponse(get_hierarchy_tree())


@app.get("/api/hierarchy/tasks")
async def api_hierarchy_tasks(dept: str = ""):
    """Get pending inter-agent tasks, optionally filtered by department."""
    from core.agent_hierarchy import get_department_queue, init_hierarchy_db
    try:
        init_hierarchy_db()
    except Exception:
        pass
    if dept:
        tasks = get_department_queue(dept)
    else:
        import sqlite3 as _sql
        from pathlib import Path as _P
        conn = _sql.connect(str(_P.home() / ".nexus" / "memory.db"))
        conn.row_factory = _sql.Row
        rows = conn.execute(
            "SELECT * FROM agent_tasks WHERE status = 'pending' ORDER BY priority DESC, created_at ASC LIMIT 50"
        ).fetchall()
        conn.close()
        tasks = [dict(r) for r in rows]
    return JSONResponse({"tasks": tasks})


@app.get("/api/hierarchy/comms")
async def api_hierarchy_comms(limit: int = 50):
    """Recent inter-agent communications feed."""
    from core.agent_hierarchy import get_recent_comms, init_hierarchy_db
    try:
        init_hierarchy_db()
    except Exception:
        pass
    comms = get_recent_comms(min(limit, 200))
    return JSONResponse({"comms": comms})


@app.get("/api/hierarchy/agent-comms")
async def api_hierarchy_agent_comms(agent_id: str = "", limit: int = 10):
    """Get recent communications for a specific agent."""
    from core.agent_hierarchy import get_agent_comms, init_hierarchy_db
    try:
        init_hierarchy_db()
    except Exception:
        pass
    if not agent_id:
        return JSONResponse({"comms": []})
    comms = get_agent_comms(agent_id, min(limit, 50))
    return JSONResponse({"comms": comms})


@app.post("/api/coordinator/emergency-stop")
async def coordinator_emergency_stop():
    """Emergency stop — pause coordinator and kill all fleet workers."""
    import signal
    try:
        from core.master_coordinator import get_coordinator
        coord = get_coordinator()
        coord.pause()
        # Kill fleet workers
        killed = 0
        try:
            import psutil
            for proc in psutil.process_iter(["pid", "cmdline"]):
                cmdline = " ".join(proc.info.get("cmdline") or [])
                if "worker_runner" in cmdline or "fleet_worker" in cmdline:
                    proc.send_signal(signal.SIGTERM)
                    killed += 1
        except Exception:
            pass
        logging.getLogger("server").warning("[EMERGENCY STOP] Coordinator paused, %d workers killed", killed)
        return JSONResponse({"status": "ok", "message": f"Emergency stop executed. Coordinator paused, {killed} workers terminated."})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.post("/api/hierarchy/task")
async def api_hierarchy_create_task(request: Request):
    """Create an inter-agent task."""
    from core.agent_hierarchy import create_task, init_hierarchy_db
    try:
        init_hierarchy_db()
    except Exception:
        pass
    body = await request.json()
    task_id = create_task(
        from_agent=body.get("from_agent", "orchestrator"),
        from_dept=body.get("from_dept", "orchestrator"),
        to_agent=body.get("to_agent", ""),
        to_dept=body.get("to_dept", ""),
        title=body.get("title", ""),
        description=body.get("description", ""),
        priority=body.get("priority", "normal"),
    )
    return JSONResponse({"ok": True, "task_id": task_id})


@app.post("/api/hierarchy/task/{task_id}/complete")
async def api_hierarchy_complete_task(task_id: int, request: Request):
    """Mark an inter-agent task as completed."""
    from core.agent_hierarchy import complete_task
    body = await request.json()
    complete_task(task_id, result=body.get("result", ""))
    return JSONResponse({"ok": True})


# ── Research Reports API ──────────────────────────────────────────────────────

@app.get("/api/research/reports")
async def api_research_reports(limit: int = 10):
    """Get recent research reports."""
    import sqlite3 as _sql
    from pathlib import Path as _P
    try:
        conn = _sql.connect(str(_P.home() / ".nexus" / "memory.db"))
        conn.row_factory = _sql.Row
        rows = conn.execute(
            "SELECT * FROM research_reports ORDER BY created_at DESC LIMIT ?",
            (min(limit, 50),)
        ).fetchall()
        conn.close()
        return JSONResponse({"reports": [dict(r) for r in rows]})
    except Exception as e:
        return JSONResponse({"reports": [], "error": str(e)})


@app.post("/api/research/run")
async def api_research_run():
    """Manually trigger research tasks."""
    try:
        from core.master_coordinator import get_coordinator
        coord = get_coordinator()
        # Override the day check so it runs now
        coord._research_last_day = ""
        # We can't directly call the async method here easily,
        # so we use a background task approach
        import asyncio
        asyncio.create_task(coord._agent_audience_researcher())
        return JSONResponse({"status": "ok", "message": "Research tasks triggered"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


# ── Shared Brain API ──────────────────────────────────────────────────────────

@app.get("/api/brain/errors")
async def api_brain_errors(limit: int = 20):
    """Recent errors with solutions from shared brain."""
    from core.shared_brain import get_brain
    return JSONResponse({"errors": get_brain().get_recent_errors(min(limit, 100))})

@app.get("/api/brain/improvements")
async def api_brain_improvements(limit: int = 20):
    """Pending improvement suggestions."""
    from core.shared_brain import get_brain
    return JSONResponse({"improvements": get_brain().get_pending_improvements(min(limit, 50))})

@app.get("/api/brain/stats")
async def api_brain_stats():
    """Shared brain table stats."""
    from core.shared_brain import get_brain
    return JSONResponse(get_brain().get_stats())

@app.post("/api/brain/scan")
async def api_brain_scan():
    """Trigger codebase scan."""
    from core.shared_brain import get_brain
    count = get_brain().scan_codebase()
    return JSONResponse({"status": "ok", "files_indexed": count})

# ── Service Controls ──────────────────────────────────────────────────────────

def _service_control_snapshots():
    from scripts.process_manager import ProcessManager

    pm = ProcessManager()
    process_index = {
        item.get("name"): item
        for item in pm.get_status()
        if isinstance(item, dict)
    }
    snapshots = []
    for state in list_service_states(load_config()):
        process_state = process_index.get(state["process_name"]) or {}
        process_running = bool(process_state.get("actually_alive"))
        snapshots.append({
            **state,
            "process_status": "running" if process_running else process_state.get("status", "unknown"),
            "process_pid": int(process_state.get("pid") or 0),
            "process_running": process_running,
        })
    return snapshots


def _service_control_snapshot(service_id: str):
    target = str(service_id or "").strip().lower()
    for snapshot in _service_control_snapshots():
        if str(snapshot.get("id") or "").strip().lower() == target:
            return snapshot
    return None


@app.get("/api/service-controls")
async def api_service_controls():
    return JSONResponse({"services": _service_control_snapshots()})


@app.post("/api/service-controls/{service_id}", dependencies=[Depends(verify_action_auth)])
async def api_service_control_update(service_id: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}

    if "enabled" not in body:
        return JSONResponse({"error": "Missing required field: enabled"}, status_code=400)

    enabled = _is_truthy(body.get("enabled"), False)
    clear_blockers = _is_truthy(body.get("clear_blockers"), False)

    try:
        updated = set_service_enabled(service_id, enabled, clear_blockers=clear_blockers)
    except KeyError:
        return JSONResponse({"error": f"Unknown service: {service_id}"}, status_code=404)
    except ServiceControlError as exc:
        return JSONResponse(
            {
                "ok": False,
                "error": str(exc),
                "blocked_by": exc.blocked_by,
                "service": get_service_state(service_id, load_config()),
            },
            status_code=409,
        )

    from scripts.process_manager import ProcessManager

    pm = ProcessManager()
    process_name = str(updated["process_name"])
    if enabled:
        pm.start_process(process_name)
    else:
        pm.stop_process(process_name)

    snapshot = _service_control_snapshot(str(updated["id"])) or updated
    return JSONResponse({"ok": True, "service": snapshot})

# ── Process Manager API ───────────────────────────────────────────────────────

@app.get("/api/processes")
async def api_processes():
    """List all managed worker processes with PIDs."""
    try:
        from scripts.process_manager import ProcessManager
        pm = ProcessManager()
        return JSONResponse({"processes": pm.get_status()})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/processes/{name}/start", dependencies=[Depends(verify_action_auth)])
async def api_process_start(name: str, request: Request):
    """Start a worker process."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    from scripts.process_manager import ProcessManager
    pm = ProcessManager()
    ok = pm.start_process(name, tier=body.get("tier", 2))
    return JSONResponse({"ok": ok, "name": name})

@app.post("/api/processes/{name}/stop", dependencies=[Depends(verify_action_auth)])
async def api_process_stop(name: str):
    """Stop a worker process."""
    from scripts.process_manager import ProcessManager
    pm = ProcessManager()
    ok = pm.stop_process(name)
    return JSONResponse({"ok": ok, "name": name})

@app.post("/api/processes/stop-all", dependencies=[Depends(verify_action_auth)])
async def api_process_stop_all():
    """Emergency stop all workers."""
    from scripts.process_manager import ProcessManager
    pm = ProcessManager()
    count = pm.stop_all()
    return JSONResponse({"ok": True, "stopped": count})

@app.get("/api/processes/launchd")
async def api_launchd_processes():
    """Real process status from launchd + psutil for all com.zoar.* services."""
    try:
        from core.process_monitor import get_running_processes
        procs = get_running_processes()
        running = sum(1 for p in procs if p["status"] == "running")
        return JSONResponse({"processes": procs, "running": running, "total": len(procs)})
    except Exception as e:
        log.error("launchd process check failed: %s", e)
        return JSONResponse({"error": str(e), "processes": [], "running": 0, "total": 0})

# ── Coding Agent API ──────────────────────────────────────────────────────────

@app.post("/api/tasks/code", dependencies=[Depends(verify_action_auth)])
async def api_task_code(request: Request):
    """Submit a coding task to Aider via CodingAgent."""
    body = await request.json()
    description = body.get("description", "")
    if not description:
        return JSONResponse({"error": "description required"}, status_code=400)
    from core.coding_agent import get_coding_agent
    agent = get_coding_agent()
    result = await agent.execute_task(
        description=description,
        target_files=body.get("files"),
        priority=body.get("priority", 7),
        model=body.get("model", ""),
    )
    return JSONResponse(result)

@app.get("/api/tasks/code/active")
async def api_task_code_active():
    """Check active coding tasks."""
    from core.coding_agent import get_coding_agent
    return JSONResponse({"tasks": get_coding_agent().get_active_tasks()})

# ── Task Chains API ───────────────────────────────────────────────────────────

@app.post("/api/chains/start", dependencies=[Depends(verify_action_auth)])
async def api_chain_start(request: Request):
    """Start a new task chain."""
    body = await request.json()
    chain_type = body.get("chain_type", "")
    context = body.get("context", {})
    from core.task_chains import get_chain_orchestrator
    chain_id = get_chain_orchestrator().start_chain(chain_type, context)
    if chain_id:
        return JSONResponse({"ok": True, "chain_id": chain_id})
    return JSONResponse({"error": "Unknown chain type: %s" % chain_type}, status_code=400)

@app.get("/api/chains/active")
async def api_chains_active():
    """List active task chains."""
    from core.task_chains import get_chain_orchestrator
    return JSONResponse({"chains": get_chain_orchestrator().get_active_chains()})

@app.get("/api/chains/{chain_id}")
async def api_chain_status(chain_id: str):
    """Get chain status."""
    from core.task_chains import get_chain_orchestrator
    status = get_chain_orchestrator().get_chain_status(chain_id)
    if status:
        return JSONResponse(status)
    return JSONResponse({"error": "Chain not found"}, status_code=404)

@app.get("/api/chains/types/list")
async def api_chain_types():
    """List available chain types."""
    from core.task_chains import TaskChainOrchestrator
    return JSONResponse(TaskChainOrchestrator.list_chain_types())

# ── Talk to Nexus API ─────────────────────────────────────────────────────────

# Rate limiting: 30 requests/minute per IP
_rate_buckets = {}  # type: dict[str, list[float]]
_RATE_LIMIT = 30
_RATE_WINDOW = 60  # seconds

# Dedup: reject identical messages within 2 seconds
_recent_messages = {}  # type: dict[str, float]  # hash → timestamp

def _check_rate_limit(ip):
    # type: (str) -> bool
    """Returns True if request is allowed, False if rate-limited."""
    now = time.time()
    bucket = _rate_buckets.get(ip, [])
    bucket = [t for t in bucket if now - t < _RATE_WINDOW]
    if len(bucket) >= _RATE_LIMIT:
        _rate_buckets[ip] = bucket
        return False
    bucket.append(now)
    _rate_buckets[ip] = bucket
    return True

def _check_dedup(text):
    # type: (str) -> bool
    """Returns True if message is new, False if duplicate within 2s."""
    now = time.time()
    h = hashlib.md5(text.strip().lower().encode()).hexdigest()
    last = _recent_messages.get(h)
    if last and now - last < 2:
        return False
    _recent_messages[h] = now
    # Prune old entries
    if len(_recent_messages) > 200:
        cutoff = now - 5
        for k in list(_recent_messages):
            if _recent_messages[k] < cutoff:
                del _recent_messages[k]
    return True

@app.post("/api/talk/send")
async def api_talk_send(request: Request):
    """Send a message through the Nexus router."""
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        return JSONResponse({"error": "Please slow down (30 req/min limit)"}, status_code=429)
    body = await request.json()
    text = body.get("message", "").strip()
    if not text:
        return JSONResponse({"error": "message required"}, status_code=400)
    if not _check_dedup(text):
        return JSONResponse({"error": "Duplicate message"}, status_code=429)
    from core.chat_handler import handle_chat_message
    result = await handle_chat_message(text, source="web")
    return JSONResponse(result)

@app.get("/api/talk/stream")
async def api_talk_stream(request: Request, message: str = ""):
    """SSE streaming endpoint for Talk to Nexus. Progressive response display."""
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        return JSONResponse({"error": "Please slow down (30 req/min limit)"}, status_code=429)
    if not message.strip():
        return JSONResponse({"error": "message parameter required"}, status_code=400)
    if not _check_dedup(message):
        return JSONResponse({"error": "Duplicate message"}, status_code=429)
    import json as _json
    from starlette.responses import StreamingResponse
    from core.chat_handler import handle_chat_stream

    async def event_generator():
        try:
            async for event in handle_chat_stream(message.strip(), source="web"):
                yield "data: %s\n\n" % _json.dumps(event)
        except Exception as e:
            yield "data: %s\n\n" % _json.dumps({"type": "error", "content": str(e)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

@app.get("/api/talk/history")
async def api_talk_history(limit: int = 50):
    """Get recent chat history."""
    from core.chat_handler import get_history
    messages = get_history(min(limit, 200))
    return JSONResponse({"messages": messages})

@app.get("/api/talk/snapshot")
async def api_talk_snapshot():
    """Get system snapshot for Talk to Nexus sidebar."""
    from core.nexus_router import get_nexus_router
    return JSONResponse(get_nexus_router().get_snapshot())

@app.get("/api/talk/claude-spend")
async def api_claude_spend():
    """Get today's Claude API spend."""
    from core.smart_router import get_smart_router
    sr = get_smart_router()
    return JSONResponse({
        "spend_today": sr.get_claude_spend_today(),
        "degraded_providers": sr.get_failure_status(),
    })

# ── Tool Registry API ─────────────────────────────────────────────────────────

@app.get("/api/tools")
async def api_tools_list():
    """List available tools."""
    from core.tools import TOOL_REGISTRY
    tools = {}
    for name, t in TOOL_REGISTRY.items():
        tools[name] = {"description": t["description"], "args": t["args"], "safe": t["safe"]}
    return JSONResponse({"tools": tools})

@app.post("/api/tools/execute", dependencies=[Depends(verify_action_auth)])
async def api_tools_execute(request: Request):
    """Execute a tool by name."""
    body = await request.json()
    tool_name = body.get("tool", "")
    args = body.get("args", {})
    if not tool_name:
        return JSONResponse({"error": "tool name required"}, status_code=400)
    from core.tools import execute_tool
    result = execute_tool(tool_name, args)
    return JSONResponse(result)

# ── Agent Memory API ──────────────────────────────────────────────────────────

@app.get("/api/memory")
async def api_memory_list():
    """List all stored memories."""
    from core.agent_memory import get_all_memories
    return JSONResponse({"memories": get_all_memories()})

@app.post("/api/memory/remember", dependencies=[Depends(verify_action_auth)])
async def api_memory_remember(request: Request):
    """Store a memory."""
    body = await request.json()
    key = body.get("key", "")
    value = body.get("value", "")
    if not key or not value:
        return JSONResponse({"error": "key and value required"}, status_code=400)
    from core.agent_memory import remember
    remember(key, value, source="api")
    return JSONResponse({"ok": True, "key": key})

@app.post("/api/memory/forget", dependencies=[Depends(verify_action_auth)])
async def api_memory_forget(request: Request):
    """Delete a memory by key."""
    body = await request.json()
    key = body.get("key", "")
    if not key:
        return JSONResponse({"error": "key required"}, status_code=400)
    from core.agent_memory import forget
    deleted = forget(key)
    return JSONResponse({"ok": deleted})

@app.get("/api/memory/recall")
async def api_memory_recall(q: str = ""):
    """Search memories by keyword."""
    if not q:
        return JSONResponse({"error": "q parameter required"}, status_code=400)
    from core.agent_memory import recall
    return JSONResponse({"results": recall(q)})

# ── Build Pipeline API ────────────────────────────────────────────────────────

@app.post("/api/builds/start", dependencies=[Depends(verify_action_auth)])
async def api_build_start(request: Request):
    """Start a new multi-agent build."""
    body = await request.json()
    description = body.get("description", "")
    if not description:
        return JSONResponse({"error": "description required"}, status_code=400)
    from core.build_pipeline import get_build_pipeline
    build_id = get_build_pipeline().start_build(description, started_by="api")
    return JSONResponse({"ok": True, "build_id": build_id})

@app.get("/api/builds")
async def api_builds_list(status: str = ""):
    """List all builds, optionally filtered by status."""
    from core.build_pipeline import get_build_pipeline
    builds = get_build_pipeline().list_builds(status=status or None)
    return JSONResponse({"builds": builds})

@app.get("/api/builds/{build_id}")
async def api_build_detail(build_id: int):
    """Get build details with files and review."""
    from core.build_pipeline import get_build_pipeline
    build = get_build_pipeline().get_build(build_id)
    if not build:
        return JSONResponse({"error": "Build not found"}, status_code=404)
    return JSONResponse(build)

@app.post("/api/builds/{build_id}/approve", dependencies=[Depends(verify_action_auth)])
async def api_build_approve(build_id: int):
    """Merge an approved build into the live codebase."""
    from core.build_pipeline import get_build_pipeline
    result = get_build_pipeline().merge_build(build_id)
    code = 200 if result.get("ok") else 400
    return JSONResponse(result, status_code=code)

@app.post("/api/builds/{build_id}/reject", dependencies=[Depends(verify_action_auth)])
async def api_build_reject(build_id: int, request: Request):
    """Reject a build with optional reason."""
    body = await request.json()
    from core.build_pipeline import get_build_pipeline
    ok = get_build_pipeline().reject_build(build_id, body.get("reason", ""))
    return JSONResponse({"ok": ok})

@app.get("/api/builds/{build_id}/diff")
async def api_build_diff(build_id: int):
    """Get diff of staged vs live files."""
    from core.build_pipeline import get_build_pipeline
    diffs = get_build_pipeline().get_build_diff(build_id)
    return JSONResponse({"diffs": diffs})

@app.post("/api/builds/{build_id}/upgrade", dependencies=[Depends(verify_action_auth)])
async def api_build_upgrade(build_id: int):
    """Upgrade a failed/escalated build to Claude Sonnet."""
    from core.build_pipeline import get_build_pipeline
    ok = get_build_pipeline().upgrade_build(build_id)
    code = 200 if ok else 400
    msg = "Build upgrade started" if ok else "Build cannot be upgraded"
    return JSONResponse({"ok": ok, "message": msg}, status_code=code)

@app.get("/api/builds/{build_id}/stream")
async def api_build_stream(build_id: int):
    """SSE stream of build tokens and stage events in real-time."""
    from core.build_pipeline import get_build_pipeline
    pipeline = get_build_pipeline()
    q = pipeline.subscribe(build_id)

    async def generate():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30)
                    yield "data: %s\n\n" % json.dumps(event)
                    if event.get("type") in ("build_complete", "error", "timeout"):
                        break
                except asyncio.TimeoutError:
                    # Send heartbeat to keep connection alive (up to 10 min total)
                    yield ": heartbeat\n\n"
        finally:
            pipeline.unsubscribe(build_id, q)

    return StreamingResponse(generate(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ── Division Three — Revenue Lab API ──────────────────────────────────────────

@app.get("/api/d3/dashboard")
async def api_d3_dashboard():
    from core.division_three import get_division_three
    return JSONResponse(get_division_three().get_dashboard())

@app.get("/api/d3/entrepreneur/dashboard")
async def api_d3_entrepreneur_dashboard():
    from core.division_three import get_division_three
    return JSONResponse({"status": "ok", "dashboard": get_division_three().get_entrepreneur_dashboard()})

@app.get("/api/d3/entrepreneur/runtime")
async def api_d3_entrepreneur_runtime():
    from core.division_three import get_division_three
    return JSONResponse({"status": "ok", "runtime": get_division_three().get_runtime_status()})

@app.get("/api/d3/entrepreneur/journey")
async def api_d3_entrepreneur_journey(persona_id: str = "", days: int = 7, limit: int = 200):
    from core.division_three import get_division_three
    rows = get_division_three().get_journey_entries(persona_id=persona_id, days=days, limit=limit)
    return JSONResponse({"status": "ok", "entries": rows, "count": len(rows)})

@app.get("/api/d3/personas")
async def api_d3_personas(platform: str = "", status: str = ""):
    from core.division_three import get_division_three
    return JSONResponse({"personas": get_division_three().get_personas(platform, status)})

@app.post("/api/d3/personas")
async def api_d3_create_persona(request: Request):
    try:
        body = await request.json()
        from core.division_three import get_division_three
        pid = await asyncio.wait_for(
            get_division_three().create_persona(
                body.get("platform", "tiktok"), body.get("niche", "general"),
            ),
            timeout=10,
        )
        if pid is None:
            return JSONResponse({"status": "error", "error": "Persona generation failed"}, 500)
        return JSONResponse({"status": "ok", "persona_id": pid})
    except asyncio.TimeoutError:
        return JSONResponse({"status": "error", "error": "Persona creation timed out"}, status_code=504)
    except Exception as e:
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=500)

@app.get("/api/d3/experiments")
async def api_d3_experiments(category: str = "", status: str = ""):
    from core.division_three import get_division_three
    return JSONResponse({"experiments": get_division_three().list_experiments(category, status)})

@app.post("/api/d3/experiments")
async def api_d3_create_experiment(request: Request):
    body = await request.json()
    from core.division_three import get_division_three
    eid = get_division_three().create_experiment(
        category=body.get("category", ""),
        name=body.get("name", ""),
        hypothesis=body.get("hypothesis", ""),
        persona_id=body.get("persona_id"),
        config=body.get("config"),
    )
    if eid is None:
        return JSONResponse({"status": "error", "error": "Invalid category or params"}, 400)
    return JSONResponse({"status": "ok", "experiment_id": eid})

@app.get("/api/d3/experiments/{exp_id}")
async def api_d3_experiment_detail(exp_id: int):
    from core.division_three import get_division_three
    exp = get_division_three().get_experiment(exp_id)
    if not exp:
        return JSONResponse({"status": "error", "error": "Not found"}, 404)
    return JSONResponse(exp)

@app.post("/api/d3/experiments/{exp_id}/run")
async def api_d3_run_experiment(exp_id: int):
    from core.division_three import get_division_three
    result = await get_division_three().run_experiment(exp_id)
    code = 200 if result.get("ok") else 400
    return JSONResponse(result, code)

@app.post("/api/d3/entrepreneur/cycle")
async def api_d3_entrepreneur_cycle(request: Request):
    body = await request.json()
    from core.division_three import get_division_three
    result = await get_division_three().run_parallel_entrepreneur_cycles(
        parallelism=int(body.get("parallelism", 4) or 4),
        limit=int(body.get("limit", 20) or 20),
    )
    return JSONResponse(result, 200 if result.get("ok") else 400)

@app.post("/api/d3/entrepreneur/loop/start")
async def api_d3_entrepreneur_loop_start(request: Request):
    body = await request.json()
    from core.division_three import get_division_three
    result = await get_division_three().start_autonomous_loop(
        interval_sec=int(body.get("interval_sec", 1800) or 1800),
        parallelism=int(body.get("parallelism", 4) or 4),
    )
    return JSONResponse(result, 200 if result.get("ok") else 400)

@app.post("/api/d3/entrepreneur/loop/stop")
async def api_d3_entrepreneur_loop_stop():
    from core.division_three import get_division_three
    result = await get_division_three().stop_autonomous_loop()
    return JSONResponse(result, 200 if result.get("ok") else 400)

@app.post("/api/d3/experiments/{exp_id}/pause")
async def api_d3_pause_experiment(exp_id: int):
    from core.division_three import get_division_three
    get_division_three().pause_experiment(exp_id)
    return JSONResponse({"status": "ok"})

@app.post("/api/d3/experiments/{exp_id}/complete")
async def api_d3_complete_experiment(exp_id: int):
    from core.division_three import get_division_three
    get_division_three().complete_experiment(exp_id)
    return JSONResponse({"status": "ok"})

@app.post("/api/d3/experiments/{exp_id}/outcome")
async def api_d3_record_outcome(exp_id: int, request: Request):
    body = await request.json()
    from core.division_three import get_division_three
    oid = get_division_three().record_outcome(
        exp_id, body.get("metric_type", ""), body.get("value", 0), body.get("notes", ""),
    )
    return JSONResponse({"status": "ok", "outcome_id": oid})

@app.get("/api/d3/trends")
async def api_d3_trends(source: str = "", limit: int = 50):
    from core.division_three import get_division_three
    return JSONResponse({"trends": get_division_three().get_trends(source, min(limit, 200))})

@app.post("/api/d3/trends/scan")
async def api_d3_scan_trends():
    from core.division_three import get_division_three
    signals = await get_division_three().scan_trends()
    return JSONResponse({"status": "ok", "signals": len(signals)})

@app.get("/api/d3/failures")
async def api_d3_failures(category: str = ""):
    from core.division_three import get_division_three
    return JSONResponse({"failures": get_division_three().get_failure_patterns(category)})

@app.post("/api/d3/self-improve")
async def api_d3_self_improve():
    from core.division_three import get_division_three
    result = await get_division_three().self_improve(trigger="api")
    return JSONResponse(result)

@app.get("/api/d3/portfolio")
async def api_d3_portfolio():
    from core.division_three import get_division_three
    result = await get_division_three().analyze_portfolio()
    return JSONResponse(result)

# ── Self-Healing / Health API ─────────────────────────────────────────────────

@app.get("/api/health/full")
async def api_health_full():
    """Run full health check on demand."""
    from core.self_healing import get_self_healer
    result = await get_self_healer().run_full_check()
    return JSONResponse(result)

@app.get("/api/health/history")
async def api_health_history(limit: int = 20):
    """Recent health check results."""
    from core.self_healing import get_self_healer
    return JSONResponse({"history": get_self_healer().get_history(min(limit, 100))})

@app.get("/api/health/events")
async def api_health_events(limit: int = 50, status: str = ""):
    """Recent health events from the D4 scanner."""
    from core.health_scanner import get_recent_events
    events = get_recent_events(min(limit, 200))
    if status:
        events = [e for e in events if e.get("status") == status]
    return JSONResponse({"events": events, "count": len(events)})

@app.get("/api/health/repairs")
async def api_health_repairs(limit: int = 20):
    """Recent repair attempts from the D4 self-healing engine."""
    from core.health_scanner import get_recent_repairs
    repairs = get_recent_repairs(min(limit, 100))
    return JSONResponse({"repairs": repairs, "count": len(repairs)})

@app.get("/api/health/model-performance")
async def api_health_model_performance(task_type: str = ""):
    """Model performance leaderboard for self-healing tasks."""
    from core.shared_brain import get_brain
    brain = get_brain()
    if task_type:
        data = brain.get_model_leaderboard(task_type=task_type)
    else:
        data = brain.get_model_leaderboard()
    return JSONResponse({"models": data})

@app.get("/api/health/brain-stats")
async def api_health_brain_stats():
    """SharedBrain knowledge statistics."""
    from core.shared_brain import get_brain
    from core.health_scanner import get_recent_events, get_recent_repairs
    brain = get_brain()
    return JSONResponse({
        "brain": brain.get_stats(),
        "scanner": {
            "recent_events": len(get_recent_events(50)),
            "recent_repairs": len(get_recent_repairs(20)),
        },
        "model_performance": brain.get_model_leaderboard(limit=10),
    })

@app.get("/api/health/scan")
async def api_health_scan_now():
    """Trigger an immediate full health scan."""
    from core.health_scanner import get_health_scanner
    scanner = get_health_scanner()
    issues = await scanner.run_full_scan()
    return JSONResponse({
        "status": "ok",
        "issues_found": len(issues),
        "issues": issues[:20],
    })

@app.get("/api/health/system-info")
async def api_health_system_info():
    """Real-time system metrics: CPU, RAM, DB size, token usage, git info."""
    import psutil
    import subprocess
    import os

    # CPU & RAM
    cpu_pct = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()

    # DB size
    db_file = Path.home() / ".nexus" / "memory.db"
    db_size_mb = round(os.path.getsize(str(db_file)) / (1024 * 1024), 1) if db_file.exists() else 0

    # Token usage today
    api_calls_today = 0
    tokens_today = 0
    provider_caps = []
    try:
        from core.token_budget import TokenBudgetManager
        mgr = TokenBudgetManager.get_instance()
        summary = mgr.get_summary()
        api_calls_today = summary["total_requests"]
        tokens_today = summary["total_tokens"]
        provider_caps = summary["providers"]
    except Exception:
        pass

    # Uptime
    try:
        p = psutil.Process(os.getpid())
        uptime_seconds = int(time.time() - p.create_time())
    except Exception:
        uptime_seconds = 0

    # Git info
    git_branch = ""
    git_hash = ""
    git_message = ""
    try:
        nexus_root = str(Path(__file__).parent)
        git_branch = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=nexus_root, timeout=5
        ).decode().strip()
        git_hash = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=nexus_root, timeout=5
        ).decode().strip()
        git_message = subprocess.check_output(
            ["git", "log", "-1", "--format=%s"], cwd=nexus_root, timeout=5
        ).decode().strip()
    except Exception:
        pass

    # Last error from health_events
    last_error = {"message": "", "timestamp": ""}
    try:
        conn = sqlite3.connect(str(db_file), timeout=5)
        row = conn.execute(
            "SELECT message, detected_at FROM health_events "
            "WHERE status IN ('error', 'critical') ORDER BY detected_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            last_error = {"message": row[0], "timestamp": row[1]}
    except Exception:
        pass

    return JSONResponse({
        "cpu_pct": cpu_pct,
        "ram_used_mb": mem.used // (1024 * 1024),
        "ram_total_mb": mem.total // (1024 * 1024),
        "ram_pct": mem.percent,
        "db_size_mb": db_size_mb,
        "api_calls_today": api_calls_today,
        "tokens_today": tokens_today,
        "provider_caps": provider_caps,
        "uptime_seconds": uptime_seconds,
        "git_branch": git_branch,
        "git_hash": git_hash,
        "git_message": git_message,
        "last_error": last_error,
    })


# ── Token Budget API ─────────────────────────────────────────────────────────

@app.get("/api/token-budget")
async def api_token_budget():
    """Return per-provider token budget summary."""
    try:
        from core.token_budget import TokenBudgetManager
        mgr = TokenBudgetManager.get_instance()
        return JSONResponse(mgr.get_summary())
    except Exception as e:
        return JSONResponse({"error": str(e), "providers": [], "total_requests": 0, "total_tokens": 0})


# ── Email Queue API ─────────────────────────────────────────────────────────

@app.get("/api/email/queue")
async def api_email_queue(scheduled_date: str = "", status: str = "", limit: int = 50):
    """Return queued outreach emails (email_queue-backed, frontend-compatible shape)."""
    from datetime import datetime, timedelta
    try:
        from zoneinfo import ZoneInfo
    except ImportError:  # pragma: no cover
        from backports.zoneinfo import ZoneInfo  # type: ignore
    try:
        # Default to tomorrow only for review/staging views.
        # For sent history, allow all dates when scheduled_date is omitted.
        if not scheduled_date and status.lower() != "sent":
            scheduled_date = (datetime.now(ZoneInfo("America/Los_Angeles")) + timedelta(days=1)).strftime("%Y-%m-%d")

        from core.email_queue_manager import get_queue
        emails, total = get_queue(
            scheduled_date=scheduled_date or None,
            status=status or None,
            category=None,
            limit=min(max(limit, 1), 500),
            offset=0,
        )

        queue = []
        for e in emails:
            queue.append({
                "id": e.get("id"),
                "queue_id": e.get("id"),
                "vendor_id": e.get("vendor_id"),
                "channel": "email",
                "message_draft": e.get("body_plain", ""),
                "subject": e.get("subject", ""),
                "status": e.get("status", "queued"),
                "created_at": e.get("created_at", ""),
                "sent_at": e.get("sent_at", ""),
                "scheduled_date": e.get("scheduled_date", scheduled_date),
                "vendor_name": e.get("recipient_name", ""),
                "vendor_email": e.get("recipient_email", ""),
            })

        return JSONResponse({
            "queue": queue,
            "count": len(queue),
            "total": total,
            "scheduled_date": scheduled_date,
        })
    except Exception as e:
        return JSONResponse({"queue": [], "count": 0, "error": str(e)})


# ── Campaign API ─────────────────────────────────────────────────────────────

@app.get("/api/campaigns")
async def api_list_campaigns():
    from core.email_campaign import list_campaigns
    campaigns = list_campaigns()
    return JSONResponse({"status": "ok", "campaigns": campaigns})

@app.get("/api/campaigns/{campaign_id}")
async def api_get_campaign(campaign_id: int):
    from core.email_campaign import get_campaign
    c = get_campaign(campaign_id)
    if not c:
        return JSONResponse({"status": "error", "error": "Campaign not found"}, status_code=404)
    return JSONResponse({"status": "ok", "campaign": c})

@app.post("/api/campaigns")
async def api_create_campaign(request: Request):
    data = await request.json()
    from core.email_campaign import create_campaign
    result = create_campaign(
        name=data.get("name", "New Campaign"),
        target_categories=data.get("target_categories", []),
        target_tiers=data.get("target_tiers", [1, 2]),
        min_score=data.get("min_score", 30),
    )
    return JSONResponse({"status": "ok", **result})

@app.post("/api/campaigns/{campaign_id}/test")
async def api_test_campaign(campaign_id: int, request: Request):
    data = await request.json() if await request.body() else {}
    from core.email_campaign import send_test_batch
    result = await send_test_batch(
        campaign_id,
        test_email=data.get("test_email", "kaiescobar09@gmail.com"),
        count=data.get("count", 20),
    )
    return JSONResponse({"status": "ok", **result})

@app.post("/api/campaigns/{campaign_id}/approve")
async def api_approve_campaign(campaign_id: int):
    from core.email_campaign import approve_campaign
    result = approve_campaign(campaign_id)
    return JSONResponse({"status": "ok", **result})

@app.post("/api/campaigns/{campaign_id}/pause")
async def api_pause_campaign(campaign_id: int):
    from core.email_campaign import pause_campaign
    result = pause_campaign(campaign_id)
    return JSONResponse({"status": "ok", **result})

@app.post("/api/campaigns/{campaign_id}/resume")
async def api_resume_campaign(campaign_id: int):
    from core.email_campaign import resume_campaign
    result = resume_campaign(campaign_id)
    return JSONResponse({"status": "ok", **result})

# /api/vendors/eligible and /api/vendors/vetting-stats moved to core/vendor_api.py
# (must be before {vendor_id} catch-all)

@app.post("/api/vendors/enrich")
async def api_enrich_vendors():
    from core.vendor_enrichment import score_all_vendors
    result = score_all_vendors()
    return JSONResponse({"status": "ok", **result})

@app.post("/api/vendors/vet-batch")
async def api_vet_batch(batch_size: int = 100):
    from core.vendor_vetting import vet_batch
    result = await vet_batch(batch_size=min(batch_size, 500))
    return JSONResponse({"status": "ok", **result})

@app.post("/api/vendors/discover-google")
async def api_discover_google(request: Request):
    """Trigger Google Places vendor discovery. Requires google_places_api_key in config."""
    try:
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        from core.google_places_vendor_discovery import async_discover_and_save
        result = await async_discover_and_save(
            categories=body.get("categories"),
            location=body.get("location", "San Fernando, CA"),
            radius_miles=body.get("radius_miles", 15),
        )
        if result.get("skipped_no_key"):
            return JSONResponse({"status": "error", "detail": "Google Places API key not configured"}, status_code=400)
        return JSONResponse({"status": "ok", **result})
    except Exception as e:
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=500)

# ── Agent Activity Stream (SSE) ──────────────────────────────────────────────

@app.get("/api/agents/activity-stream")
async def api_activity_stream():
    """SSE endpoint for real-time agent activity feed."""
    from core.agent_activity import get_activity_logger
    logger = get_activity_logger()
    q = logger.subscribe()

    async def event_generator():
        try:
            # Send recent history first
            for entry in logger.get_recent(20):
                yield "data: %s\n\n" % json.dumps(entry)
            # Then stream new events
            while True:
                try:
                    entry = await asyncio.wait_for(q.get(), timeout=30)
                    yield "data: %s\n\n" % json.dumps(entry)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            logger.unsubscribe(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/api/agents/activity")
async def api_activity_recent(limit: int = 50):
    """Recent agent activity (non-SSE)."""
    from core.agent_activity import get_activity_logger
    return JSONResponse({"activity": get_activity_logger().get_recent(min(limit, 200))})

# ── Claude Guard API ──────────────────────────────────────────────────────────

@app.get("/api/claude-guard/stats")
async def api_claude_guard_stats():
    """Claude usage guard statistics."""
    from core.claude_guard import get_claude_guard
    return JSONResponse(get_claude_guard().get_stats())

# ── Bug Report / Improvement API ──────────────────────────────────────────────

@app.post("/api/tasks/bug-report", dependencies=[Depends(verify_action_auth)])
async def api_bug_report(request: Request):
    """Submit a bug report — creates priority 9 task + Telegram notification."""
    body = await request.json()
    title = body.get("title", "Bug report")
    description = body.get("description", "")
    screen = body.get("screen", "unknown")
    from core.fleet_task_queue import FleetTaskQueue
    q = FleetTaskQueue()
    task_id = q.enqueue(
        task_type="bug_fix",
        input_data=json.dumps({"title": title, "description": description, "screen": screen}),
        tier=1,
        priority=9,
    )
    # Notify Telegram
    try:
        cfg = load_config()
        tg_token = cfg.get("telegram_token", "")
        tg_chat = cfg.get("telegram_chat_id", "")
        if tg_token and tg_chat:
            import httpx
            msg = "BUG REPORT [%s]\n%s\n%s" % (screen, title, description[:200])
            async with httpx.AsyncClient(timeout=10) as c:
                await c.post(
                    "https://api.telegram.org/bot%s/sendMessage" % tg_token,
                    json={"chat_id": tg_chat, "text": msg},
                )
    except Exception:
        pass
    return JSONResponse({"ok": True, "task_id": task_id})

@app.post("/api/tasks/improvement", dependencies=[Depends(verify_action_auth)])
async def api_improvement(request: Request):
    """Submit an improvement suggestion."""
    body = await request.json()
    from core.shared_brain import get_brain
    get_brain().suggest_improvement(
        agent_id=body.get("agent_id", "nexus_ui"),
        category=body.get("category", "general"),
        title=body.get("title", ""),
        description=body.get("description", ""),
        priority=body.get("priority", "medium"),
    )
    return JSONResponse({"ok": True})


# ── UI Version & Changelog ────────────────────────────────────────────────────

@app.get("/api/ui/version")
async def api_ui_version():
    """Return current UI build version."""
    version_file = Path.home() / "nexus-network" / "public" / "version.json"
    if version_file.exists():
        try:
            return JSONResponse(json.loads(version_file.read_text()))
        except Exception:
            pass
    return JSONResponse({"version": "dev", "hash": "000000000000", "built_at": "", "builder": "manual"})


@app.get("/api/ui/changelog")
async def api_ui_changelog():
    """Return recent UI changelog entries."""
    import sqlite3
    db = Path.home() / ".nexus" / "memory.db"
    try:
        conn = sqlite3.connect(str(db), timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM ui_changelog ORDER BY id DESC LIMIT 50"
        ).fetchall()
        conn.close()
        return JSONResponse({"entries": [dict(r) for r in rows]})
    except Exception as e:
        return JSONResponse({"entries": [], "error": str(e)})


if __name__ == "__main__":
    port = int(os.getenv("PORT", 7860))
    if not _acquire_server_instance_lock(port):
        print(f"[Nexus] Server already running on port {port} (lock held). Skipping duplicate start.")
        raise SystemExit(0)
    print(f"\n⚡ Nexus Agent → http://localhost:{port}\n")
    print(f"   Dashboard → http://localhost:{port}/cmd\n")
    print(f"   B2B Dashboard → http://localhost:{port}/api/b2b/dashboard\n")
    print(f"   FB Prospects  → http://localhost:{port}/api/fb/dashboard\n")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
