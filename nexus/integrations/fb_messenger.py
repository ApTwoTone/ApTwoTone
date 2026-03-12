from __future__ import annotations
"""
Facebook Messenger Webhook Integration
- Receives Facebook Page DMs via webhook
- Routes messages to Telegram for Kai's review
- All replies go through the approval queue — NEVER auto-sends
- Links conversations to CRM leads
- Stores full message audit trail in SQLite
"""
import hashlib, hmac, json, re, sqlite3, traceback
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"
PT = ZoneInfo("America/Los_Angeles")
GRAPH_API = "https://graph.facebook.com/v21.0"

# Keywords that suggest a lead inquiry
LEAD_KEYWORDS = re.compile(
    r"(wedding|event|rental|rent|quote|price|pric|cost|book|reserv|party|"
    r"festival|corporate|porta.?pott|restroom|bathroom|trailer|outdoor|"
    r"how much|available|avail)", re.IGNORECASE,
)

def _now():
    return datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S")

def _load_config() -> dict:
    try: return json.loads(CONFIG_PATH.read_text())
    except Exception: return {}

def _db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


# ── Database ──────────────────────────────────────────────────────────────────

def init_messenger_db():
    """Create Messenger tables if they don't exist."""
    conn = _db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS messenger_conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        psid TEXT UNIQUE,
        name TEXT DEFAULT '',
        profile_pic TEXT DEFAULT '',
        last_message TEXT DEFAULT '',
        last_message_at TEXT DEFAULT '',
        lead_id INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS messenger_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        psid TEXT,
        direction TEXT DEFAULT 'inbound',
        message_text TEXT DEFAULT '',
        mid TEXT DEFAULT '',
        status TEXT DEFAULT 'received',
        approval_id INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_mm_psid ON messenger_messages(psid);
    CREATE INDEX IF NOT EXISTS idx_mc_psid ON messenger_conversations(psid);
    CREATE INDEX IF NOT EXISTS idx_mc_lead ON messenger_conversations(lead_id);
    """)
    conn.commit(); conn.close()
    print("[Messenger] Database tables ready")


# ── Webhook verification ─────────────────────────────────────────────────────

def verify_webhook(mode: str, token: str, challenge: str) -> str | None:
    """Handle Facebook GET verification. Returns challenge if valid, None otherwise."""
    config = _load_config()
    verify_token = config.get("fb_verify_token", "nexus-fb-verify")
    if mode == "subscribe" and token == verify_token:
        print("[Messenger] Webhook verified")
        return challenge
    print(f"[Messenger] Webhook verification failed (mode={mode})")
    return None


def verify_signature(payload_bytes: bytes, signature_header: str) -> bool:
    """Verify the X-Hub-Signature-256 from Facebook."""
    config = _load_config()
    app_secret = config.get("fb_app_secret", "")
    if not app_secret:
        print("[Messenger] No fb_app_secret configured — skipping verification")
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header[7:])


# ── Incoming message processing ──────────────────────────────────────────────

async def process_webhook(payload: dict, send_fn, approval_fn) -> dict:
    """Process incoming Messenger webhook POST.
    send_fn:     async (text) -> sends to Kai's Telegram
    approval_fn: async (psid, text, approval_id) -> queues for approval
    Returns {"ok": True, "messages_processed": count}
    """
    if payload.get("object") != "page":
        return {"ok": False, "error": "Not a page event"}
    count = 0
    for entry in payload.get("entry", []):
        for event in entry.get("messaging", []):
            try:
                await _handle_messaging_event(event, send_fn, approval_fn)
                count += 1
            except Exception as e:
                print(f"[Messenger] Error processing event: {e}")
                traceback.print_exc()
    return {"ok": True, "messages_processed": count}


async def _handle_messaging_event(event: dict, send_fn, approval_fn):
    """Handle a single messaging event from Facebook."""
    sender_psid = event.get("sender", {}).get("id", "")
    message = event.get("message", {})
    text, mid = message.get("text", ""), message.get("mid", "")
    if not sender_psid or not text:
        return

    config = _load_config()
    # Ignore echo messages from the page itself
    if sender_psid == config.get("fb_page_id", ""):
        return

    print(f"[Messenger] Message from {sender_psid}: {text[:80]}")

    # Get or create conversation, fetch profile if needed
    convo = _get_or_create_conversation(sender_psid)
    name = convo.get("name", "")
    if not name:
        profile = await _fetch_profile(sender_psid)
        name = profile.get("name", "")
        if name:
            _update_conversation(sender_psid, name=name, profile_pic=profile.get("profile_pic", ""))

    # Store inbound message and update conversation
    _store_message(sender_psid, "inbound", text, mid, "received")
    _update_conversation(sender_psid, last_message=text, last_message_at=_now())

    # CRM lead linking
    lead_id = convo.get("lead_id", 0)
    lead_info = ""
    if lead_id:
        lead_info = f"Lead: #{lead_id}"
    else:
        matched_id = _match_to_lead(name, text)
        if matched_id:
            link_to_lead(sender_psid, matched_id)
            lead_id = matched_id
            lead_info = f"Lead: #{lead_id} (auto-linked)"
        else:
            lead_info = f"New \u2014 /msglead {sender_psid}"

    # Auto-create lead if it looks like a lead inquiry
    if not lead_id and LEAD_KEYWORDS.search(text):
        lead_id = _auto_create_lead(sender_psid, name, text)
        if lead_id:
            link_to_lead(sender_psid, lead_id)
            lead_info = f"Lead: #{lead_id} (auto-created)"
            print(f"[Messenger] Auto-created lead #{lead_id} for {name}")

    # Telegram notification
    display_name = name or sender_psid
    preview = text if len(text) <= 200 else text[:200] + "..."
    tg_text = (
        f"\U0001f4ac FB MESSENGER\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"From: {display_name}\n"
        f"Message: {preview}\n\n"
        f"Reply: /msg {sender_psid} [your reply]\n"
        f"{lead_info}"
    )
    if send_fn:
        try: await send_fn(tg_text)
        except Exception as e: print(f"[Messenger] Telegram notify error: {e}")


# ── Profile fetch ────────────────────────────────────────────────────────────

async def _fetch_profile(psid: str) -> dict:
    """Fetch user profile from Facebook Graph API."""
    config = _load_config()
    token = config.get("fb_page_token", "")
    if not token:
        return {}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{GRAPH_API}/{psid}",
                params={"fields": "first_name,last_name,profile_pic", "access_token": token},
            )
            if resp.status_code != 200:
                print(f"[Messenger] Profile fetch failed ({resp.status_code}): {resp.text[:200]}")
                return {}
            data = resp.json()
            first, last = data.get("first_name", ""), data.get("last_name", "")
            return {"name": f"{first} {last}".strip(), "first_name": first,
                    "last_name": last, "profile_pic": data.get("profile_pic", "")}
    except Exception as e:
        print(f"[Messenger] Profile fetch error: {e}")
        return {}


# ── Send message via Send API ────────────────────────────────────────────────

async def send_message(psid: str, text: str, approval_id: int = None) -> dict:
    """Send a message via the Facebook Send API. Returns {"ok": bool, ...}"""
    from integrations.messaging import outbound_gate
    gate = outbound_gate("facebook_dm", psid, "", text,
                         approval_id=approval_id, code_path="fb_messenger.send_message")
    if not gate["ok"]:
        return {"ok": False, "error": f"BLOCKED: {gate['reason']}", "method": "blocked"}
    config = _load_config()
    token = config.get("fb_page_token", "")
    if not token:
        return {"ok": False, "error": "No fb_page_token configured"}
    body = {
        "recipient": {"id": psid},
        "messaging_type": "RESPONSE",
        "message": {"text": text},
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{GRAPH_API}/me/messages", params={"access_token": token}, json=body,
            )
            data = resp.json()
            if resp.status_code == 200 and "message_id" in data:
                mid = data.get("message_id", "")
                _store_message(psid, "outbound", text, mid, "sent")
                _update_conversation(psid, last_message=f"[You] {text[:100]}", last_message_at=_now())
                print(f"[Messenger] Sent to {psid}: {text[:60]}")
                return {"ok": True, "message_id": mid}
            error = data.get("error", {}).get("message", resp.text[:200])
            print(f"[Messenger] Send failed: {error}")
            return {"ok": False, "error": error}
    except Exception as e:
        print(f"[Messenger] Send error: {e}")
        return {"ok": False, "error": str(e)}


# ── Approval-gated reply ─────────────────────────────────────────────────────

async def queue_reply(psid: str, text: str, approval_fn) -> dict:
    """Queue a reply through the approval system. NEVER sends directly.
    approval_fn: async (psid, text) -> int (approval_id)
    """
    conn = _db()
    cursor = conn.execute(
        "INSERT INTO messenger_messages (psid, direction, message_text, status) "
        "VALUES (?, 'outbound', ?, 'pending_approval')", (psid, text),
    )
    msg_id = cursor.lastrowid
    conn.commit(); conn.close()

    approval_id = 0
    if approval_fn:
        try: approval_id = await approval_fn(psid, text)
        except Exception as e: print(f"[Messenger] Approval queue error: {e}")

    if approval_id:
        conn = _db()
        conn.execute("UPDATE messenger_messages SET approval_id=? WHERE id=?", (approval_id, msg_id))
        conn.commit(); conn.close()

    print(f"[Messenger] Reply queued for approval (msg={msg_id}, approval={approval_id})")
    return {"ok": True, "message_id": msg_id, "approval_id": approval_id, "status": "pending_approval"}


async def handle_approved_reply(message_id: int) -> dict:
    """Called when Kai approves a queued reply. Sends and updates status."""
    conn = _db()
    row = conn.execute(
        "SELECT id, psid, message_text, status FROM messenger_messages WHERE id=?", (message_id,)
    ).fetchone()
    conn.close()

    if not row:
        return {"ok": False, "error": "Message not found"}
    if row["status"] != "pending_approval":
        return {"ok": False, "error": f"Status is '{row['status']}', expected pending_approval"}

    result = await send_message(row["psid"], row["message_text"])
    new_status = "sent" if result.get("ok") else "failed"

    conn = _db()
    conn.execute("UPDATE messenger_messages SET status=? WHERE id=?", (new_status, message_id))
    conn.commit(); conn.close()

    print(f"[Messenger] Approved reply {message_id} -> {new_status}")
    return {"ok": result.get("ok", False), "status": new_status, "send_result": result}


# ── Conversation helpers ─────────────────────────────────────────────────────

def _get_or_create_conversation(psid: str) -> dict:
    conn = _db()
    row = conn.execute("SELECT * FROM messenger_conversations WHERE psid=?", (psid,)).fetchone()
    if row:
        conn.close(); return dict(row)
    conn.execute("INSERT INTO messenger_conversations (psid, created_at) VALUES (?, ?)", (psid, _now()))
    conn.commit()
    row = conn.execute("SELECT * FROM messenger_conversations WHERE psid=?", (psid,)).fetchone()
    conn.close()
    return dict(row) if row else {"psid": psid}


def _update_conversation(psid: str, **kwargs):
    if not kwargs: return
    set_parts = ", ".join(f"{k}=?" for k in kwargs)
    values = list(kwargs.values()) + [psid]
    conn = _db()
    conn.execute(f"UPDATE messenger_conversations SET {set_parts} WHERE psid=?", values)
    conn.commit(); conn.close()


def _store_message(psid: str, direction: str, text: str, mid: str, status: str):
    conn = _db()
    conn.execute(
        "INSERT INTO messenger_messages (psid, direction, message_text, mid, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)", (psid, direction, text, mid, status, _now()),
    )
    conn.commit(); conn.close()


def _match_to_lead(name: str, message_text: str) -> int | None:
    """Try to match a Messenger conversation to an existing CRM lead by name."""
    if not name: return None
    parts = name.lower().split()
    if not parts: return None
    conn = _db()
    try:
        if len(parts) >= 2:
            row = conn.execute(
                "SELECT id FROM leads WHERE lower(first_name)=? AND lower(last_name)=? "
                "AND status NOT IN ('closed','opted_out') ORDER BY id DESC LIMIT 1",
                (parts[0], parts[-1]),
            ).fetchone()
            if row: return row["id"]
        row = conn.execute(
            "SELECT id FROM leads WHERE lower(first_name)=? "
            "AND status NOT IN ('closed','opted_out') ORDER BY id DESC LIMIT 1",
            (parts[0],),
        ).fetchone()
        return row["id"] if row else None
    except Exception as e:
        print(f"[Messenger] Lead match error: {e}")
        return None
    finally:
        conn.close()


def _auto_create_lead(psid: str, name: str, message: str) -> int | None:
    """Auto-create a CRM lead from a Messenger inquiry."""
    try:
        parts = name.split() if name else []
        first = parts[0] if parts else ""
        last = parts[-1] if len(parts) > 1 else ""
        conn = _db()
        cursor = conn.execute(
            "INSERT INTO leads (first_name, last_name, source, notes, status) "
            "VALUES (?, ?, 'facebook_messenger', ?, 'new')",
            (first, last, f"FB Messenger inquiry: {message[:200]}"),
        )
        lead_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO lead_events (lead_id, event_type, details) VALUES (?, 'new_lead', ?)",
            (lead_id, f"Auto-created from Messenger DM by {name or psid}"),
        )
        conn.commit(); conn.close()
        return lead_id
    except Exception as e:
        print(f"[Messenger] Auto-create lead error: {e}")
        return None


# ── Public query functions ───────────────────────────────────────────────────

def get_conversations(limit: int = 20) -> list[dict]:
    """Get recent conversations with last message preview."""
    conn = _db()
    rows = conn.execute(
        "SELECT * FROM messenger_conversations ORDER BY last_message_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_conversation_history(psid: str, limit: int = 50) -> list[dict]:
    """Get full message history for a conversation."""
    conn = _db()
    rows = conn.execute(
        "SELECT * FROM messenger_messages WHERE psid=? ORDER BY created_at ASC LIMIT ?", (psid, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def link_to_lead(psid: str, lead_id: int) -> bool:
    """Link a Messenger conversation to a CRM lead."""
    try:
        conn = _db()
        conn.execute("UPDATE messenger_conversations SET lead_id=? WHERE psid=?", (lead_id, psid))
        conn.commit(); conn.close()
        print(f"[Messenger] Linked PSID {psid} to lead #{lead_id}")
        return True
    except Exception as e:
        print(f"[Messenger] Link error: {e}")
        return False


def get_messenger_stats() -> dict:
    """Stats: total conversations, messages today, unlinked, avg response time."""
    conn = _db()
    total_convos = conn.execute("SELECT COUNT(*) FROM messenger_conversations").fetchone()[0]
    today = datetime.now(PT).strftime("%Y-%m-%d")
    msgs_today = conn.execute(
        "SELECT COUNT(*) FROM messenger_messages WHERE created_at >= ?", (today,)
    ).fetchone()[0]
    unlinked = conn.execute(
        "SELECT COUNT(*) FROM messenger_conversations WHERE lead_id = 0"
    ).fetchone()[0]
    # Avg response time: minutes between inbound and next outbound for same PSID
    avg_resp = conn.execute("""
        SELECT AVG(CAST((julianday(o.created_at) - julianday(i.created_at)) * 1440 AS REAL))
        FROM messenger_messages i
        JOIN messenger_messages o ON i.psid = o.psid
            AND o.direction = 'outbound' AND o.created_at > i.created_at
            AND o.id = (SELECT MIN(o2.id) FROM messenger_messages o2
                        WHERE o2.psid = i.psid AND o2.direction = 'outbound'
                        AND o2.created_at > i.created_at)
        WHERE i.direction = 'inbound'
    """).fetchone()
    avg_min = round(avg_resp[0], 1) if avg_resp and avg_resp[0] else 0
    conn.close()
    return {
        "total_conversations": total_convos, "messages_today": msgs_today,
        "unlinked_conversations": unlinked, "avg_response_minutes": avg_min,
    }


def format_conversations() -> str:
    """Telegram-formatted recent conversations list."""
    convos = get_conversations(15)
    if not convos:
        return "\U0001f4ac No Messenger conversations yet."
    lines = ["\U0001f4ac FB Messenger Conversations", "\u2501" * 24]
    for c in convos:
        name = c.get("name") or c.get("psid", "?")[:12]
        preview = (c.get("last_message") or "")[:50]
        lead = f" \u2192 Lead #{c['lead_id']}" if c.get("lead_id") else ""
        ts = c.get("last_message_at", "")[:16]
        lines.append(f"\u2022 {name}: {preview}{lead}")
        if ts: lines.append(f"  {ts}")
    return "\n".join(lines)


# ── Telegram command handlers ────────────────────────────────────────────────

def handle_msg_command(text: str) -> tuple[str, str | None]:
    """Parse '/msg [psid] [text]'. Returns (response_text, psid_or_None).
    Actual sending goes through approval — this just parses.
    """
    parts = text.strip().split(None, 2)
    if len(parts) < 3:
        return "Usage: /msg [psid] [your reply text]", None
    psid, reply_text = parts[1], parts[2]
    if not psid.isdigit():
        return f"Invalid PSID: {psid} (should be numeric)", None
    conn = _db()
    row = conn.execute("SELECT name FROM messenger_conversations WHERE psid=?", (psid,)).fetchone()
    conn.close()
    name = row["name"] if row and row["name"] else psid
    return (
        f"Queuing reply to {name}:\n\"{reply_text[:100]}\"\n\n"
        f"This will go through approval before sending.", psid
    )


def handle_msg_lead_command(text: str) -> str:
    """Parse '/msglead [psid]' to create a CRM lead from a Messenger conversation."""
    parts = text.strip().split()
    if len(parts) < 2: return "Usage: /msglead [psid]"
    psid = parts[1]
    if not psid.isdigit(): return f"Invalid PSID: {psid}"

    conn = _db()
    row = conn.execute("SELECT * FROM messenger_conversations WHERE psid=?", (psid,)).fetchone()
    conn.close()
    if not row: return f"No conversation found for PSID {psid}"

    convo = dict(row)
    if convo.get("lead_id"): return f"Already linked to lead #{convo['lead_id']}"

    messages = get_conversation_history(psid, 5)
    context = " | ".join(m.get("message_text", "")[:80] for m in messages if m.get("direction") == "inbound")
    lead_id = _auto_create_lead(psid, convo.get("name", ""), context or "Messenger DM")
    if lead_id:
        link_to_lead(psid, lead_id)
        return f"Created lead #{lead_id} for {convo.get('name') or psid} and linked to conversation."
    return "Failed to create lead. Check logs."


def handle_conversations_command(text: str) -> str:
    """Handle /conversations command — list recent FB DMs."""
    return format_conversations()


# ── FastAPI route helpers ────────────────────────────────────────────────────

def get_webhook_verify_handler():
    """Returns a handler function for the GET webhook verification route."""
    async def handler(request):
        from fastapi.responses import PlainTextResponse
        params = request.query_params
        result = verify_webhook(
            params.get("hub.mode", ""), params.get("hub.verify_token", ""),
            params.get("hub.challenge", ""),
        )
        if result is not None:
            return PlainTextResponse(result)
        return PlainTextResponse("Verification failed", status_code=403)
    return handler


def get_webhook_handler(send_fn=None, approval_fn=None):
    """Returns a handler function for the POST webhook route.
    send_fn:     async (text) -> send to Kai's Telegram
    approval_fn: async (psid, text) -> int (approval_id)
    """
    async def handler(request):
        from fastapi.responses import JSONResponse
        body = await request.body()
        sig = request.headers.get("X-Hub-Signature-256", "")
        if not verify_signature(body, sig):
            print("[Messenger] Invalid signature — rejecting webhook")
            return JSONResponse({"error": "Invalid signature"}, status_code=403)
        try: payload = json.loads(body)
        except Exception: return JSONResponse({"error": "Invalid JSON"}, status_code=400)
        result = await process_webhook(payload, send_fn, approval_fn)
        return JSONResponse(result)
    return handler


# ── Module-level init ────────────────────────────────────────────────────────

_initialized = False

def init_fb_messenger(config: dict = None) -> str:
    """Initialize Facebook Messenger integration.
    Returns empty string on success, error message on failure.
    """
    global _initialized
    if config is None: config = _load_config()
    if not config.get("fb_page_token"):
        return "No fb_page_token in config"
    try:
        init_messenger_db()
        _initialized = True
        print(f"[Messenger] Initialized for page {config.get('fb_page_id', '?')}")
        return ""
    except Exception as e:
        return f"Messenger init error: {e}"

def is_initialized() -> bool:
    return _initialized
