"""
Meta Conversions API (CAPI) — Server-side event sending to Facebook.

Supplements the client-side Pixel to:
 • Bypass ad blockers & iOS tracking restrictions
 • Send richer PII (hashed) for better matching
 • Dedup with Pixel via shared event_id

Free to use — events sent to:
  POST https://graph.facebook.com/v25.0/{pixel_id}/events
"""
from __future__ import annotations
import asyncio, hashlib, json, time, uuid
from datetime import datetime
from pathlib import Path

import httpx

# ── Config ────────────────────────────────────────────────────────────────────

_config: dict = {}
_LOG_FILE = Path.home() / ".nexus" / "capi_log.jsonl"

GRAPH_API_VERSION = "v25.0"


def init_capi(config: dict):
    """Call once at startup with the full nexus config dict."""
    global _config
    _config = config
    _LOG_FILE.parent.mkdir(exist_ok=True)
    pixel = config.get("fb_pixel_id", "")
    print(f"[CAPI] Initialized (pixel={pixel})")


# ── PII Hashing ──────────────────────────────────────────────────────────────

def _sha256(value: str) -> str:
    """SHA-256 hash a string (lowered + stripped) per Meta's spec."""
    return hashlib.sha256(value.lower().strip().encode()).hexdigest()


def _hash_phone(phone: str) -> str:
    """Normalize to E.164 (US +1) then hash."""
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 10:
        digits = "1" + digits
    elif len(digits) == 11 and digits[0] == "1":
        pass
    return _sha256(digits)


def _hash_email(email: str) -> str:
    return _sha256(email.lower().strip())


def _hash_name(name: str) -> str:
    return _sha256(name.lower().strip())


# ── Event Building ───────────────────────────────────────────────────────────

def _build_user_data(
    email: str = "",
    phone: str = "",
    first_name: str = "",
    last_name: str = "",
    client_ip: str = "",
    user_agent: str = "",
    fbc: str = "",
    fbp: str = "",
) -> dict:
    """Build the user_data dict with hashed PII."""
    ud: dict = {}
    if email:
        ud["em"] = [_hash_email(email)]
    if phone:
        ud["ph"] = [_hash_phone(phone)]
    if first_name:
        ud["fn"] = [_hash_name(first_name)]
    if last_name:
        ud["ln"] = [_hash_name(last_name)]
    if client_ip:
        ud["client_ip_address"] = client_ip
    if user_agent:
        ud["client_user_agent"] = user_agent
    if fbc:
        ud["fbc"] = fbc
    if fbp:
        ud["fbp"] = fbp
    return ud


def _build_event(
    event_name: str,
    user_data: dict,
    event_id: str = "",
    event_source_url: str = "",
    custom_data: dict | None = None,
    action_source: str = "website",
) -> dict:
    """Build a single event payload."""
    return {
        "event_name": event_name,
        "event_time": int(time.time()),
        "event_id": event_id or uuid.uuid4().hex,
        "event_source_url": event_source_url,
        "action_source": action_source,
        "user_data": user_data,
        **({"custom_data": custom_data} if custom_data else {}),
    }


# ── Sending ──────────────────────────────────────────────────────────────────

async def _send_event(event: dict) -> dict:
    """
    POST a single event to the Conversions API.
    Returns {"ok": True/False, "response": ..., "error": ...}
    """
    pixel_id = _config.get("fb_pixel_id", "")
    token = _config.get("fb_page_access_token", "")

    if not pixel_id or not token:
        return {"ok": False, "error": "Missing fb_pixel_id or fb_page_access_token"}

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{pixel_id}/events"
    payload = {
        "data": [event],
        "access_token": token,
    }

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.post(url, json=payload)
            body = resp.json()

            result = {
                "ok": resp.status_code == 200,
                "status": resp.status_code,
                "response": body,
                "event_name": event["event_name"],
                "event_id": event["event_id"],
            }

            # Log to JSONL
            _log_event(event, result)

            if resp.status_code == 200:
                print(f"[CAPI] ✓ {event['event_name']} sent (id={event['event_id'][:12]}…)")
            else:
                print(f"[CAPI] ✗ {event['event_name']} failed: {resp.status_code} — {body}")

            return result

        except Exception as e:
            error_result = {
                "ok": False,
                "error": str(e),
                "event_name": event["event_name"],
                "event_id": event["event_id"],
            }
            _log_event(event, error_result)
            print(f"[CAPI] ✗ {event['event_name']} exception: {e}")
            return error_result


def _log_event(event: dict, result: dict):
    """Append to JSONL log file for debugging."""
    try:
        entry = {
            "ts": datetime.utcnow().isoformat(),
            "event_name": event.get("event_name"),
            "event_id": event.get("event_id"),
            "ok": result.get("ok"),
            "status": result.get("status"),
            "error": result.get("error"),
        }
        with open(_LOG_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


# Also log to SQLite (best-effort)
def _log_to_db(event: dict, result: dict):
    """Log CAPI event to SQLite for dashboard visibility."""
    try:
        import sqlite3
        db_path = Path.home() / ".nexus" / "memory.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT OR IGNORE INTO capi_events "
            "(event_id, event_name, event_time, action_source, ok, status_code, error, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.get("event_id", ""),
                event.get("event_name", ""),
                event.get("event_time", 0),
                event.get("action_source", "website"),
                int(result.get("ok", False)),
                result.get("status", 0),
                result.get("error", ""),
                datetime.utcnow().isoformat(),
            )
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # DB logging is best-effort


# ── Public API (fire-and-forget) ─────────────────────────────────────────────

def fire_lead_event(
    email: str = "",
    phone: str = "",
    first_name: str = "",
    last_name: str = "",
    event_type: str = "",
    event_source_url: str = "",
    event_id: str = "",
    client_ip: str = "",
    user_agent: str = "",
    fbc: str = "",
    fbp: str = "",
    action_source: str = "website",
):
    """
    Fire a Lead event to CAPI (non-blocking).
    Call after a lead is created in the CRM.
    """
    if not _config.get("fb_pixel_id"):
        return

    user_data = _build_user_data(
        email=email, phone=phone,
        first_name=first_name, last_name=last_name,
        client_ip=client_ip, user_agent=user_agent,
        fbc=fbc, fbp=fbp,
    )
    custom_data = {}
    if event_type:
        custom_data["content_name"] = event_type
        custom_data["content_category"] = "quote_request"

    event = _build_event(
        event_name="Lead",
        user_data=user_data,
        event_id=event_id,
        event_source_url=event_source_url or "https://zoarbathroomrental.com",
        custom_data=custom_data or None,
        action_source=action_source or "website",
    )

    asyncio.create_task(_send_and_log(event))


def fire_contact_event(
    email: str = "",
    phone: str = "",
    first_name: str = "",
    last_name: str = "",
    method: str = "sms",
    client_ip: str = "",
):
    """
    Fire a Contact event after SMS/email outreach.
    This tells Meta's algorithm we made contact → higher intent signal.
    """
    if not _config.get("fb_pixel_id"):
        return

    user_data = _build_user_data(
        email=email, phone=phone,
        first_name=first_name, last_name=last_name,
        client_ip=client_ip,
    )
    custom_data = {"contact_method": method}

    event = _build_event(
        event_name="Contact",
        user_data=user_data,
        event_source_url="https://zoarbathroomrental.com",
        custom_data=custom_data,
        action_source="system_generated",
    )

    asyncio.create_task(_send_and_log(event))


def fire_view_content_event(
    event_source_url: str = "",
    client_ip: str = "",
    user_agent: str = "",
    fbc: str = "",
    fbp: str = "",
    event_id: str = "",
):
    """Fire a ViewContent event from server side (supplements Pixel PageView)."""
    if not _config.get("fb_pixel_id"):
        return

    user_data = _build_user_data(
        client_ip=client_ip, user_agent=user_agent,
        fbc=fbc, fbp=fbp,
    )
    event = _build_event(
        event_name="ViewContent",
        user_data=user_data,
        event_id=event_id,
        event_source_url=event_source_url,
    )
    asyncio.create_task(_send_and_log(event))


async def _send_and_log(event: dict):
    """Send event and log to both JSONL and DB."""
    result = await _send_event(event)
    _log_to_db(event, result)


# ── Test / Debug ─────────────────────────────────────────────────────────────

async def test_connection() -> dict:
    """
    Send a test event to verify CAPI is working.
    Returns the full API response.
    """
    user_data = _build_user_data(email="test@example.com", phone="0000000000")
    event = _build_event(
        event_name="ViewContent",
        user_data=user_data,
        event_source_url="https://zoarbathroomrental.com",
        custom_data={"content_name": "CAPI Test Event"},
    )
    return await _send_event(event)


def get_recent_log(limit: int = 50) -> list[dict]:
    """Read last N entries from the JSONL log."""
    if not _LOG_FILE.exists():
        return []
    lines = _LOG_FILE.read_text().strip().split("\n")
    entries = []
    for line in lines[-limit:]:
        try:
            entries.append(json.loads(line))
        except Exception:
            pass
    return entries
