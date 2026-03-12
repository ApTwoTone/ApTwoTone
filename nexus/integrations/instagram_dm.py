from __future__ import annotations
"""
Instagram DM Webhook Integration
- Receives Instagram Direct Messages via webhook
- Routes messages to Telegram for Kai's review
- All replies go through the approval queue — NEVER auto-sends (Rule 0)
- Links conversations to CRM leads
- Stores full message audit trail in SQLite

Uses Instagram Graph API (same token as Facebook — fb_page_access_token).
Instagram Business Account discovered via FB Page ID.
"""
import hashlib, hmac, json, re, sqlite3, traceback
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"
PT = ZoneInfo("America/Los_Angeles")
GRAPH_API = "https://graph.facebook.com/v25.0"
FB_PAGE_ID = "946769878528865"

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


# -- Database ------------------------------------------------------------------

def init_instagram_dm_db():
    """Create Instagram DM tables if they don't exist."""
    conn = _db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS instagram_dm_conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ig_user_id TEXT UNIQUE,
        username TEXT DEFAULT '',
        name TEXT DEFAULT '',
        profile_pic TEXT DEFAULT '',
        last_message TEXT DEFAULT '',
        last_message_at TEXT DEFAULT '',
        lead_id INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS instagram_dm_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ig_user_id TEXT,
        direction TEXT DEFAULT 'inbound',
        message_text TEXT DEFAULT '',
        mid TEXT DEFAULT '',
        status TEXT DEFAULT 'received',
        approval_id INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_igdm_uid ON instagram_dm_messages(ig_user_id);
    CREATE INDEX IF NOT EXISTS idx_igdc_uid ON instagram_dm_conversations(ig_user_id);
    CREATE INDEX IF NOT EXISTS idx_igdc_lead ON instagram_dm_conversations(lead_id);
    """)
    conn.commit(); conn.close()
    print("[IG DM] Database tables ready")


# -- Webhook verification -----------------------------------------------------

def verify_webhook(mode: str, token: str, challenge: str) -> str | None:
    """Handle Instagram GET verification. Returns challenge if valid, None otherwise."""
    config = _load_config()
    verify_token = config.get("fb_verify_token", "nexus-fb-verify")
    if mode == "subscribe" and token == verify_token:
        print("[IG DM] Webhook verified")
        return challenge
    print(f"[IG DM] Webhook verification failed (mode={mode})")
    return None


def verify_signature(payload_bytes: bytes, signature_header: str) -> bool:
    """Verify the X-Hub-Signature-256 from Instagram (same as Facebook)."""
    config = _load_config()
    app_secret = config.get("fb_app_secret", "")
    if not app_secret:
        print("[IG DM] No fb_app_secret configured -- skipping verification")
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header[7:])


# -- Incoming message processing -----------------------------------------------

async def process_webhook(payload: dict, send_fn, approval_fn) -> dict:
    """Process incoming Instagram DM webhook POST.
    send_fn:     async (text) -> sends to Kai's Telegram
    approval_fn: async (ig_user_id, text, approval_id) -> queues for approval
    Returns {"ok": True, "messages_processed": count}
    """
    if payload.get("object") != "instagram":
        return {"ok": False, "error": "Not an instagram event"}
    count = 0
    for entry in payload.get("entry", []):
        for event in entry.get("messaging", []):
            try:
                await _handle_messaging_event(event, send_fn, approval_fn)
                count += 1
            except Exception as e:
                print(f"[IG DM] Error processing event: {e}")
                traceback.print_exc()
    return {"ok": True, "messages_processed": count}


async def _handle_messaging_event(event: dict, send_fn, approval_fn):
    """Handle a single messaging event from Instagram."""
    sender_id = event.get("sender", {}).get("id", "")
    message = event.get("message", {})
    text, mid = message.get("text", ""), message.get("mid", "")
    if not sender_id or not text:
        return

    config = _load_config()
    # Ignore echo messages from our own IG account
    ig_account_id = config.get("instagram_business_account_id", "")
    if sender_id == ig_account_id:
        return

    print(f"[IG DM] Message from {sender_id}: {text[:80]}")

    # Get or create conversation, fetch profile if needed
    convo = _get_or_create_conversation(sender_id)
    name = convo.get("name", "")
    username = convo.get("username", "")
    if not name:
        profile = await _fetch_profile(sender_id)
        name = profile.get("name", "")
        username = profile.get("username", "")
        if name or username:
            _update_conversation(
                sender_id,
                name=name,
                username=username,
                profile_pic=profile.get("profile_pic", ""),
            )

    # Store inbound message and update conversation
    _store_message(sender_id, "inbound", text, mid, "received")
    _update_conversation(sender_id, last_message=text, last_message_at=_now())

    # CRM lead linking
    lead_id = convo.get("lead_id", 0)
    lead_info = ""
    if lead_id:
        lead_info = f"Lead: #{lead_id}"
    else:
        matched_id = _match_to_lead(name, text)
        if matched_id:
            link_to_lead(sender_id, matched_id)
            lead_id = matched_id
            lead_info = f"Lead: #{lead_id} (auto-linked)"
        else:
            lead_info = f"New -- /igdmlead {sender_id}"

    # Auto-create lead if it looks like a lead inquiry
    if not lead_id and LEAD_KEYWORDS.search(text):
        lead_id = _auto_create_lead(sender_id, name, text)
        if lead_id:
            link_to_lead(sender_id, lead_id)
            lead_info = f"Lead: #{lead_id} (auto-created)"
            print(f"[IG DM] Auto-created lead #{lead_id} for {name}")

    # Telegram notification
    display_name = name or username or sender_id
    preview = text if len(text) <= 200 else text[:200] + "..."
    tg_text = (
        f"\U0001f4f8 INSTAGRAM DM\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"From: {display_name}"
        + (f" (@{username})" if username and username != display_name else "")
        + f"\nMessage: {preview}\n\n"
        f"Reply: /igdm {sender_id} [your reply]\n"
        f"{lead_info}"
    )
    if send_fn:
        try: await send_fn(tg_text)
        except Exception as e: print(f"[IG DM] Telegram notify error: {e}")


# -- Profile fetch -------------------------------------------------------------

async def _fetch_profile(ig_user_id: str) -> dict:
    """Fetch user profile from Instagram Graph API."""
    config = _load_config()
    token = config.get("fb_page_access_token", "")
    if not token:
        return {}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{GRAPH_API}/{ig_user_id}",
                params={"fields": "name,username,profile_pic", "access_token": token},
            )
            if resp.status_code != 200:
                print(f"[IG DM] Profile fetch failed ({resp.status_code}): {resp.text[:200]}")
                return {}
            data = resp.json()
            return {
                "name": data.get("name", ""),
                "username": data.get("username", ""),
                "profile_pic": data.get("profile_pic", ""),
            }
    except Exception as e:
        print(f"[IG DM] Profile fetch error: {e}")
        return {}


# -- Send message via Instagram Graph API --------------------------------------

async def send_message(ig_user_id: str, text: str, approval_id: int = None) -> dict:
    """Send a message via the Instagram Graph API. Returns {"ok": bool, ...}
    Uses POST /{ig-user-id}/messages endpoint.
    """
    from integrations.messaging import outbound_gate
    gate = outbound_gate("instagram_dm", ig_user_id, "", text,
                         approval_id=approval_id, code_path="instagram_dm.send_message")
    if not gate["ok"]:
        return {"ok": False, "error": f"BLOCKED: {gate['reason']}", "method": "blocked"}
    config = _load_config()
    token = config.get("fb_page_access_token", "")
    if not token:
        return {"ok": False, "error": "No fb_page_access_token configured"}

    # Resolve the IG business account ID for sending
    ig_account_id = config.get("instagram_business_account_id", "")
    if not ig_account_id:
        ig_account_id = await _get_ig_account_id()
    if not ig_account_id:
        return {"ok": False, "error": "Cannot resolve Instagram Business Account ID"}

    body = {
        "recipient": {"id": ig_user_id},
        "message": {"text": text},
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{GRAPH_API}/{ig_account_id}/messages",
                params={"access_token": token},
                json=body,
            )
            data = resp.json()
            if resp.status_code == 200 and "message_id" in data:
                mid = data.get("message_id", "")
                _store_message(ig_user_id, "outbound", text, mid, "sent")
                _update_conversation(ig_user_id, last_message=f"[You] {text[:100]}", last_message_at=_now())
                print(f"[IG DM] Sent to {ig_user_id}: {text[:60]}")
                return {"ok": True, "message_id": mid}
            error = data.get("error", {}).get("message", resp.text[:200])
            print(f"[IG DM] Send failed: {error}")
            return {"ok": False, "error": error}
    except Exception as e:
        print(f"[IG DM] Send error: {e}")
        return {"ok": False, "error": str(e)}


async def _get_ig_account_id() -> str:
    """Discover the Instagram Business Account ID from the linked FB Page."""
    config = _load_config()
    token = config.get("fb_page_access_token", "")
    page_id = config.get("fb_page_id", FB_PAGE_ID)
    if not token or not page_id:
        return ""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{GRAPH_API}/{page_id}",
                params={"fields": "instagram_business_account", "access_token": token},
            )
            if resp.status_code == 200:
                ig = resp.json().get("instagram_business_account", {})
                return ig.get("id", "")
    except Exception as e:
        print(f"[IG DM] IG account discovery error: {e}")
    return ""


# -- Approval-gated reply ------------------------------------------------------

async def queue_reply(ig_user_id: str, text: str, approval_fn) -> dict:
    """Queue a reply through the approval system. NEVER sends directly.
    approval_fn: async (ig_user_id, text) -> int (approval_id)
    """
    conn = _db()
    cursor = conn.execute(
        "INSERT INTO instagram_dm_messages (ig_user_id, direction, message_text, status) "
        "VALUES (?, 'outbound', ?, 'pending_approval')", (ig_user_id, text),
    )
    msg_id = cursor.lastrowid
    conn.commit(); conn.close()

    approval_id = 0
    if approval_fn:
        try: approval_id = await approval_fn(ig_user_id, text)
        except Exception as e: print(f"[IG DM] Approval queue error: {e}")

    if approval_id:
        conn = _db()
        conn.execute("UPDATE instagram_dm_messages SET approval_id=? WHERE id=?", (approval_id, msg_id))
        conn.commit(); conn.close()

    print(f"[IG DM] Reply queued for approval (msg={msg_id}, approval={approval_id})")
    return {"ok": True, "message_id": msg_id, "approval_id": approval_id, "status": "pending_approval"}


async def handle_approved_reply(message_id: int) -> dict:
    """Called when Kai approves a queued reply. Sends and updates status."""
    conn = _db()
    row = conn.execute(
        "SELECT id, ig_user_id, message_text, status FROM instagram_dm_messages WHERE id=?", (message_id,)
    ).fetchone()
    conn.close()

    if not row:
        return {"ok": False, "error": "Message not found"}
    if row["status"] != "pending_approval":
        return {"ok": False, "error": f"Status is '{row['status']}', expected pending_approval"}

    result = await send_message(row["ig_user_id"], row["message_text"])
    new_status = "sent" if result.get("ok") else "failed"

    conn = _db()
    conn.execute("UPDATE instagram_dm_messages SET status=? WHERE id=?", (new_status, message_id))
    conn.commit(); conn.close()

    print(f"[IG DM] Approved reply {message_id} -> {new_status}")
    return {"ok": result.get("ok", False), "status": new_status, "send_result": result}


# -- Conversation helpers ------------------------------------------------------

def _get_or_create_conversation(ig_user_id: str) -> dict:
    conn = _db()
    row = conn.execute("SELECT * FROM instagram_dm_conversations WHERE ig_user_id=?", (ig_user_id,)).fetchone()
    if row:
        conn.close(); return dict(row)
    conn.execute("INSERT INTO instagram_dm_conversations (ig_user_id, created_at) VALUES (?, ?)", (ig_user_id, _now()))
    conn.commit()
    row = conn.execute("SELECT * FROM instagram_dm_conversations WHERE ig_user_id=?", (ig_user_id,)).fetchone()
    conn.close()
    return dict(row) if row else {"ig_user_id": ig_user_id}


def _update_conversation(ig_user_id: str, **kwargs):
    if not kwargs: return
    set_parts = ", ".join(f"{k}=?" for k in kwargs)
    values = list(kwargs.values()) + [ig_user_id]
    conn = _db()
    conn.execute(f"UPDATE instagram_dm_conversations SET {set_parts} WHERE ig_user_id=?", values)
    conn.commit(); conn.close()


def _store_message(ig_user_id: str, direction: str, text: str, mid: str, status: str):
    conn = _db()
    conn.execute(
        "INSERT INTO instagram_dm_messages (ig_user_id, direction, message_text, mid, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)", (ig_user_id, direction, text, mid, status, _now()),
    )
    conn.commit(); conn.close()


def _match_to_lead(name: str, message_text: str) -> int | None:
    """Try to match an Instagram DM conversation to an existing CRM lead by name."""
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
        print(f"[IG DM] Lead match error: {e}")
        return None
    finally:
        conn.close()


def _auto_create_lead(ig_user_id: str, name: str, message: str) -> int | None:
    """Auto-create a CRM lead from an Instagram DM inquiry."""
    try:
        parts = name.split() if name else []
        first = parts[0] if parts else ""
        last = parts[-1] if len(parts) > 1 else ""
        conn = _db()
        cursor = conn.execute(
            "INSERT INTO leads (first_name, last_name, source, notes, status) "
            "VALUES (?, ?, 'instagram_dm', ?, 'new')",
            (first, last, f"Instagram DM inquiry: {message[:200]}"),
        )
        lead_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO lead_events (lead_id, event_type, details) VALUES (?, 'new_lead', ?)",
            (lead_id, f"Auto-created from Instagram DM by {name or ig_user_id}"),
        )
        conn.commit(); conn.close()
        return lead_id
    except Exception as e:
        print(f"[IG DM] Auto-create lead error: {e}")
        return None


# -- Public query functions ----------------------------------------------------

def get_conversations(limit: int = 20) -> list[dict]:
    """Get recent Instagram DM conversations with last message preview."""
    conn = _db()
    rows = conn.execute(
        "SELECT * FROM instagram_dm_conversations ORDER BY last_message_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_conversation_history(ig_user_id: str, limit: int = 50) -> list[dict]:
    """Get full message history for an Instagram DM conversation."""
    conn = _db()
    rows = conn.execute(
        "SELECT * FROM instagram_dm_messages WHERE ig_user_id=? ORDER BY created_at ASC LIMIT ?", (ig_user_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def link_to_lead(ig_user_id: str, lead_id: int) -> bool:
    """Link an Instagram DM conversation to a CRM lead."""
    try:
        conn = _db()
        conn.execute("UPDATE instagram_dm_conversations SET lead_id=? WHERE ig_user_id=?", (lead_id, ig_user_id))
        conn.commit(); conn.close()
        print(f"[IG DM] Linked IG user {ig_user_id} to lead #{lead_id}")
        return True
    except Exception as e:
        print(f"[IG DM] Link error: {e}")
        return False


def get_instagram_dm_stats() -> dict:
    """Stats: total conversations, messages today, unlinked, avg response time."""
    conn = _db()
    total_convos = conn.execute("SELECT COUNT(*) FROM instagram_dm_conversations").fetchone()[0]
    today = datetime.now(PT).strftime("%Y-%m-%d")
    msgs_today = conn.execute(
        "SELECT COUNT(*) FROM instagram_dm_messages WHERE created_at >= ?", (today,)
    ).fetchone()[0]
    unlinked = conn.execute(
        "SELECT COUNT(*) FROM instagram_dm_conversations WHERE lead_id = 0"
    ).fetchone()[0]
    avg_resp = conn.execute("""
        SELECT AVG(CAST((julianday(o.created_at) - julianday(i.created_at)) * 1440 AS REAL))
        FROM instagram_dm_messages i
        JOIN instagram_dm_messages o ON i.ig_user_id = o.ig_user_id
            AND o.direction = 'outbound' AND o.created_at > i.created_at
            AND o.id = (SELECT MIN(o2.id) FROM instagram_dm_messages o2
                        WHERE o2.ig_user_id = i.ig_user_id AND o2.direction = 'outbound'
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
    """Telegram-formatted recent Instagram DM conversations list."""
    convos = get_conversations(15)
    if not convos:
        return "\U0001f4f8 No Instagram DM conversations yet."
    lines = ["\U0001f4f8 Instagram DM Conversations", "\u2501" * 24]
    for c in convos:
        name = c.get("name") or c.get("username") or c.get("ig_user_id", "?")[:12]
        preview = (c.get("last_message") or "")[:50]
        lead = f" \u2192 Lead #{c['lead_id']}" if c.get("lead_id") else ""
        ts = c.get("last_message_at", "")[:16]
        lines.append(f"\u2022 {name}: {preview}{lead}")
        if ts: lines.append(f"  {ts}")
    return "\n".join(lines)


# -- Telegram command handlers -------------------------------------------------

def handle_igdm_command(text: str) -> tuple[str, str | None]:
    """Parse '/igdm [ig_user_id] [text]'. Returns (response_text, ig_user_id_or_None).
    Actual sending goes through approval -- this just parses.
    """
    parts = text.strip().split(None, 2)
    if len(parts) < 3:
        return "Usage: /igdm [ig_user_id] [your reply text]", None
    ig_user_id, reply_text = parts[1], parts[2]
    if not ig_user_id.isdigit():
        return f"Invalid IG user ID: {ig_user_id} (should be numeric)", None
    conn = _db()
    row = conn.execute("SELECT name, username FROM instagram_dm_conversations WHERE ig_user_id=?", (ig_user_id,)).fetchone()
    conn.close()
    name = (row["name"] if row and row["name"] else
            f"@{row['username']}" if row and row["username"] else ig_user_id)
    return (
        f"Queuing IG DM reply to {name}:\n\"{reply_text[:100]}\"\n\n"
        f"This will go through approval before sending.", ig_user_id
    )


def handle_igdm_lead_command(text: str) -> str:
    """Parse '/igdmlead [ig_user_id]' to create a CRM lead from an Instagram DM conversation."""
    parts = text.strip().split()
    if len(parts) < 2: return "Usage: /igdmlead [ig_user_id]"
    ig_user_id = parts[1]
    if not ig_user_id.isdigit(): return f"Invalid IG user ID: {ig_user_id}"

    conn = _db()
    row = conn.execute("SELECT * FROM instagram_dm_conversations WHERE ig_user_id=?", (ig_user_id,)).fetchone()
    conn.close()
    if not row: return f"No conversation found for IG user {ig_user_id}"

    convo = dict(row)
    if convo.get("lead_id"): return f"Already linked to lead #{convo['lead_id']}"

    messages = get_conversation_history(ig_user_id, 5)
    context = " | ".join(m.get("message_text", "")[:80] for m in messages if m.get("direction") == "inbound")
    lead_id = _auto_create_lead(ig_user_id, convo.get("name", ""), context or "Instagram DM")
    if lead_id:
        link_to_lead(ig_user_id, lead_id)
        return f"Created lead #{lead_id} for {convo.get('name') or convo.get('username') or ig_user_id} and linked to conversation."
    return "Failed to create lead. Check logs."


def handle_igdm_conversations_command(text: str) -> str:
    """Handle /igdmconvos command -- list recent Instagram DMs."""
    return format_conversations()


# -- FastAPI route helpers -----------------------------------------------------

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
    approval_fn: async (ig_user_id, text) -> int (approval_id)
    """
    async def handler(request):
        from fastapi.responses import JSONResponse
        body = await request.body()
        sig = request.headers.get("X-Hub-Signature-256", "")
        if not verify_signature(body, sig):
            print("[IG DM] Invalid signature -- rejecting webhook")
            return JSONResponse({"error": "Invalid signature"}, status_code=403)
        try: payload = json.loads(body)
        except Exception: return JSONResponse({"error": "Invalid JSON"}, status_code=400)
        result = await process_webhook(payload, send_fn, approval_fn)
        return JSONResponse(result)
    return handler


# -- Module-level init ---------------------------------------------------------

_initialized = False

def init_instagram_dm(config: dict = None) -> str:
    """Initialize Instagram DM integration.
    Returns empty string on success, error message on failure.
    """
    global _initialized
    if config is None: config = _load_config()
    if not config.get("fb_page_access_token"):
        return "No fb_page_access_token in config"
    try:
        init_instagram_dm_db()
        _initialized = True
        print(f"[IG DM] Initialized for page {config.get('fb_page_id', FB_PAGE_ID)}")
        return ""
    except Exception as e:
        return f"Instagram DM init error: {e}"

def is_initialized() -> bool:
    return _initialized
