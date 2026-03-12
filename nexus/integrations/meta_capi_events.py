"""
Meta Conversions API (CAPI) — Higher-level business event tracking.

Builds on top of meta_capi.py to fire structured business events:
 - Purchase: booking confirmed with payment
 - Schedule: booking delivery date set
 - Lead: new lead created in CRM
 - InitiateCheckout: quote generated for prospect

All PII is SHA256-hashed per Meta's spec before sending.
Events are logged to SQLite `capi_events` table for dashboard visibility.

Pixel: 1579580876639654
API: v21.0
"""
from __future__ import annotations
import asyncio, hashlib, json, sqlite3, time, uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

# ── Optional facebook-business SDK ───────────────────────────────────────────
# The SDK gives typed objects (UserData, CustomData, Event, EventRequest) but
# everything works via raw httpx if the SDK is not installed.
_HAS_FB_SDK = False
try:
    from facebook_business.adobjects.serverside.user_data import UserData
    from facebook_business.adobjects.serverside.custom_data import CustomData
    from facebook_business.adobjects.serverside.event import Event
    from facebook_business.adobjects.serverside.event_request import EventRequest
    from facebook_business.api import FacebookAdsApi
    _HAS_FB_SDK = True
except ImportError:
    # SDK not installed — we fall back to raw httpx POST
    UserData = None
    CustomData = None
    Event = None
    EventRequest = None
    FacebookAdsApi = None

# ── Config ────────────────────────────────────────────────────────────────────

PT = ZoneInfo("America/Los_Angeles")
DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"

PIXEL_ID = "1579580876639654"
# APP_TOKEN loaded from config at runtime — NEVER hardcode secrets
APP_TOKEN = ""  # Set by _load_config()
GRAPH_API_VERSION = "v21.0"
EVENT_SOURCE_URL = "https://zoarbathroomrental.com"
ACTION_SOURCE = "system_generated"

_access_token: str = ""


def _load_config() -> dict:
    """Load config from ~/.nexus/config.json."""
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def init_capi_events() -> None:
    """Initialize the CAPI events module. Call once at startup."""
    global _access_token, APP_TOKEN
    config = _load_config()
    _access_token = config.get("fb_page_token", "")
    # Load app token from config instead of hardcoding
    APP_TOKEN = config.get("fb_app_token", f"{config.get('fb_app_id', '')}|{config.get('fb_app_secret', '')}")
    _init_db()
    if _access_token:
        print(f"[CAPI] Events module initialized (pixel={PIXEL_ID})")
    else:
        print("[CAPI] WARNING: No fb_page_token in config — events will be logged but not sent")


def _init_db() -> None:
    """Create the capi_events table if it doesn't exist."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS capi_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_name TEXT NOT NULL,
            lead_id INTEGER DEFAULT 0,
            data_json TEXT DEFAULT '{}',
            response TEXT DEFAULT '',
            sent_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_capi_events_name ON capi_events(event_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_capi_events_lead ON capi_events(lead_id)")
    # Lead lifecycle feedback loop log — detailed event tracking for EMQ/dedup
    conn.execute("""
        CREATE TABLE IF NOT EXISTS capi_events_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            event_name TEXT,
            event_id TEXT,
            event_time TEXT,
            user_data_fields TEXT,
            custom_data TEXT,
            response TEXT,
            success INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_capi_log_event ON capi_events_log(event_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_capi_log_lead ON capi_events_log(lead_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_capi_log_eid ON capi_events_log(event_id)")
    conn.commit()
    conn.close()


# ── PII Hashing (SHA256 per Meta spec) ────────────────────────────────────────

def _hash_value(value: str) -> str:
    """SHA256 hash a string after lowercasing and stripping whitespace."""
    if not value:
        return ""
    return hashlib.sha256(value.lower().strip().encode("utf-8")).hexdigest()


def _hash_phone(phone: str) -> str:
    """Normalize phone to digits-only (US +1 prefix) then hash."""
    if not phone:
        return ""
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 10:
        digits = "1" + digits
    elif len(digits) == 11 and digits[0] == "1":
        pass  # already has country code
    return _hash_value(digits)


def _hash_user_data(data: dict) -> dict:
    """
    Build hashed user_data dict from lead data.

    Expects keys: email, phone, first_name, last_name, city
    All values are lowercased, stripped, and SHA256 hashed.
    State (CA) and country (US) are always set.
    """
    ud: dict = {}

    if data.get("email"):
        ud["em"] = [_hash_value(data["email"])]
    if data.get("phone"):
        ud["ph"] = [_hash_phone(data["phone"])]
    if data.get("first_name"):
        ud["fn"] = [_hash_value(data["first_name"])]
    if data.get("last_name"):
        ud["ln"] = [_hash_value(data["last_name"])]
    if data.get("city"):
        ud["ct"] = [_hash_value(data["city"])]

    # Always set state=CA, country=US for Zoar (SoCal business)
    ud["st"] = [_hash_value("ca")]
    ud["country"] = [_hash_value("us")]

    return ud


# ── Payload Builder ───────────────────────────────────────────────────────────

def _build_event_payload(
    event_name: str,
    user_data: dict,
    custom_data: dict | None = None,
) -> dict:
    """
    Build the full CAPI event payload ready for the Graph API.

    Returns the complete request body with data array, access_token, etc.
    """
    event = {
        "event_name": event_name,
        "event_time": int(time.time()),
        "event_id": uuid.uuid4().hex,
        "event_source_url": EVENT_SOURCE_URL,
        "action_source": ACTION_SOURCE,
        "user_data": user_data,
    }
    if custom_data:
        event["custom_data"] = custom_data

    return {
        "data": [event],
        "access_token": _access_token,
    }


# ── Event Sender ──────────────────────────────────────────────────────────────

async def _send_event(
    event_name: str,
    lead_data: dict,
    custom_data: dict | None = None,
    lead_id: int = 0,
) -> dict:
    """
    Send a single event to the Conversions API.

    Always logs to SQLite. Never raises — returns result dict.
    """
    user_data = _hash_user_data(lead_data)
    payload = _build_event_payload(event_name, user_data, custom_data)
    event_id = payload["data"][0]["event_id"]

    result = {"ok": False, "event_name": event_name, "event_id": event_id}

    if not _access_token:
        result["error"] = "No access token configured"
        _log_to_db(event_name, lead_id, payload, result)
        print(f"[CAPI] {event_name} logged (no token — not sent)")
        return result

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{PIXEL_ID}/events"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
            body = resp.json()

            result["ok"] = resp.status_code == 200
            result["status"] = resp.status_code
            result["response"] = body

            if resp.status_code == 200:
                print(f"[CAPI] {event_name} sent (id={event_id[:12]}...)")
            else:
                print(f"[CAPI] {event_name} failed: {resp.status_code} — {body}")

    except Exception as e:
        result["error"] = str(e)
        print(f"[CAPI] {event_name} exception: {e}")

    _log_to_db(event_name, lead_id, payload, result)
    return result


def _log_to_db(
    event_name: str,
    lead_id: int,
    payload: dict,
    result: dict,
) -> None:
    """Log event to SQLite capi_events table (best-effort)."""
    try:
        # Strip access_token from logged payload for security
        safe_payload = {**payload}
        safe_payload.pop("access_token", None)

        now_pt = datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "INSERT INTO capi_events (event_name, lead_id, data_json, response, sent_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                event_name,
                lead_id,
                json.dumps(safe_payload, default=str),
                json.dumps(result, default=str),
                now_pt,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[CAPI] DB log error: {e}")


# ── Public Business Events ────────────────────────────────────────────────────

async def send_purchase_event(lead_data: dict, amount: float) -> dict:
    """
    Fire a Purchase event when a booking is confirmed with payment.

    lead_data: dict with email, phone, first_name, last_name, city, event_type
    amount: booking dollar amount
    """
    custom_data = {
        "value": round(amount, 2),
        "currency": "USD",
        "content_name": "Luxury Restroom Trailer Rental",
        "content_category": lead_data.get("event_type", "event"),
    }
    lead_id = lead_data.get("id", 0)
    print(f"[CAPI] Sending Purchase event: ${amount:.2f} for lead {lead_id}")
    return await _send_event("Purchase", lead_data, custom_data, lead_id)


async def send_schedule_event(lead_data: dict, event_date: str) -> dict:
    """
    Fire a Schedule event when a booking delivery date is set.

    lead_data: dict with email, phone, first_name, last_name, city
    event_date: date string (e.g. "2026-06-15")
    """
    custom_data = {
        "content_name": "Restroom Trailer Delivery",
        "content_category": lead_data.get("event_type", "event"),
        "delivery_date": event_date,
    }
    lead_id = lead_data.get("id", 0)
    print(f"[CAPI] Sending Schedule event: {event_date} for lead {lead_id}")
    return await _send_event("Schedule", lead_data, custom_data, lead_id)


async def send_lead_event(lead_data: dict, source: str) -> dict:
    """
    Fire a Lead event when a new lead is created in the CRM.

    lead_data: dict with email, phone, first_name, last_name, city
    source: lead source (e.g. "facebook_ad", "craigslist", "website")
    """
    custom_data = {
        "content_name": f"Lead from {source}",
        "content_category": "lead_generation",
        "lead_source": source,
    }
    lead_id = lead_data.get("id", 0)
    print(f"[CAPI] Sending Lead event: {source} for lead {lead_id}")
    return await _send_event("Lead", lead_data, custom_data, lead_id)


async def send_initiate_checkout_event(lead_data: dict, amount: float) -> dict:
    """
    Fire an InitiateCheckout event when a quote is generated.

    lead_data: dict with email, phone, first_name, last_name, city
    amount: quoted dollar amount
    """
    custom_data = {
        "value": round(amount, 2),
        "currency": "USD",
        "content_name": "Luxury Restroom Trailer Quote",
        "content_category": lead_data.get("event_type", "event"),
    }
    lead_id = lead_data.get("id", 0)
    print(f"[CAPI] Sending InitiateCheckout event: ${amount:.2f} for lead {lead_id}")
    return await _send_event("InitiateCheckout", lead_data, custom_data, lead_id)


# ── Fire-and-forget wrappers ──────────────────────────────────────────────────
# These create asyncio tasks so callers don't have to await.

def fire_purchase(lead_data: dict, amount: float) -> None:
    """Non-blocking Purchase event. Safe to call from sync code."""
    try:
        asyncio.create_task(send_purchase_event(lead_data, amount))
    except RuntimeError:
        print("[CAPI] No event loop — Purchase event skipped")


def fire_schedule(lead_data: dict, event_date: str) -> None:
    """Non-blocking Schedule event. Safe to call from sync code."""
    try:
        asyncio.create_task(send_schedule_event(lead_data, event_date))
    except RuntimeError:
        print("[CAPI] No event loop — Schedule event skipped")


def fire_lead(lead_data: dict, source: str) -> None:
    """Non-blocking Lead event. Safe to call from sync code."""
    try:
        asyncio.create_task(send_lead_event(lead_data, source))
    except RuntimeError:
        print("[CAPI] No event loop — Lead event skipped")


def fire_initiate_checkout(lead_data: dict, amount: float) -> None:
    """Non-blocking InitiateCheckout event. Safe to call from sync code."""
    try:
        asyncio.create_task(send_initiate_checkout_event(lead_data, amount))
    except RuntimeError:
        print("[CAPI] No event loop — InitiateCheckout event skipped")


# ── Stats / Debug ─────────────────────────────────────────────────────────────

def get_event_stats(days: int = 30) -> dict:
    """Get CAPI event stats for the last N days."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cutoff = (datetime.now(PT) - __import__("datetime").timedelta(days=days)).strftime("%Y-%m-%d")

        rows = conn.execute(
            "SELECT event_name, COUNT(*) as cnt FROM capi_events "
            "WHERE sent_at >= ? GROUP BY event_name ORDER BY cnt DESC",
            (cutoff,),
        ).fetchall()

        total = conn.execute(
            "SELECT COUNT(*) FROM capi_events WHERE sent_at >= ?", (cutoff,)
        ).fetchone()[0]

        conn.close()

        breakdown = {row["event_name"]: row["cnt"] for row in rows}
        return {"total": total, "breakdown": breakdown, "days": days}
    except Exception as e:
        print(f"[CAPI] Stats error: {e}")
        return {"total": 0, "breakdown": {}, "days": days}


def get_recent_events(limit: int = 20) -> list[dict]:
    """Get the most recent CAPI events."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM capi_events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[CAPI] Recent events error: {e}")
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# LEAD LIFECYCLE FEEDBACK LOOP
# ═══════════════════════════════════════════════════════════════════════════════
#
# Send Lead → QualifiedLead → Purchase events back to Meta so their algorithm
# learns which leads actually become $1,100 bookings.
#
# Flow:
#   1. Lead comes in (send_lead_event — already exists above)
#   2. Lead qualifies after phone/text (send_qualified_lead_event)
#   3. Deposit collected (send_deposit_event → InitiateCheckout)
#   4. Booking confirmed (send_lifecycle_purchase_event → Purchase)
#
# All PII is SHA-256 hashed. Events are deduplicated via event_id (UUID).
# Every event is logged to capi_events_log for EMQ tracking.
# ═══════════════════════════════════════════════════════════════════════════════


# ── SHA-256 Hashing Helper ───────────────────────────────────────────────────

def _hash(value: str) -> str | None:
    """SHA-256 hash for PII fields (Meta CAPI requirement).

    Lowercases, strips whitespace, then hashes. Returns None if value is empty.
    """
    if not value:
        return None
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def _hash_phone_e164(phone: str) -> str | None:
    """Normalize phone to E.164 digits (country code 1) then SHA-256 hash."""
    if not phone:
        return None
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 10:
        digits = "1" + digits
    elif len(digits) == 11 and digits[0] == "1":
        pass  # already has US country code
    return _hash(digits)


# ── Build UserData for Maximum EMQ (7.0+) ────────────────────────────────────

def build_user_data(lead: dict) -> dict:
    """Build comprehensive UserData dict for maximum Event Match Quality (EMQ 7.0+).

    Extracts and SHA-256 hashes all available PII fields from the lead dict.
    The more fields provided, the higher the EMQ score and the better Meta's
    algorithm can match this event to the right user profile.

    Target fields for EMQ 7.0+:
      em, ph, fn, ln, ct, st, zp, country, fbc, fbp,
      client_ip_address, client_user_agent, external_id

    Args:
        lead: dict with keys like email, phone, first_name, last_name,
              city, state, zip, fbc, fbp, ip_address, user_agent, id, etc.

    Returns:
        dict ready to be used as user_data in the CAPI event payload.
    """
    ud: dict = {}
    fields_sent: list[str] = []

    # Email (em) — most important match key
    if lead.get("email"):
        ud["em"] = [_hash(lead["email"])]
        fields_sent.append("em")

    # Phone (ph) — second most important, E.164 with country code 1
    if lead.get("phone"):
        hashed_ph = _hash_phone_e164(lead["phone"])
        if hashed_ph:
            ud["ph"] = [hashed_ph]
            fields_sent.append("ph")

    # First name (fn)
    if lead.get("first_name"):
        ud["fn"] = [_hash(lead["first_name"])]
        fields_sent.append("fn")

    # Last name (ln)
    if lead.get("last_name"):
        ud["ln"] = [_hash(lead["last_name"])]
        fields_sent.append("ln")

    # City (ct)
    if lead.get("city"):
        ud["ct"] = [_hash(lead["city"])]
        fields_sent.append("ct")

    # State (st) — 2-letter lowercase (e.g. "ca")
    state = lead.get("state", "ca")  # default to CA for SoCal business
    if state:
        ud["st"] = [_hash(state[:2])]
        fields_sent.append("st")

    # Zip code (zp)
    if lead.get("zip"):
        ud["zp"] = [_hash(str(lead["zip"]))]
        fields_sent.append("zp")

    # Country — always US for Zoar
    ud["country"] = [_hash("us")]
    fields_sent.append("country")

    # Click ID cookie (fbc) — from Meta ad click, NOT hashed
    if lead.get("fbc"):
        ud["fbc"] = lead["fbc"]
        fields_sent.append("fbc")

    # Browser ID cookie (fbp) — Meta browser tracking, NOT hashed
    if lead.get("fbp"):
        ud["fbp"] = lead["fbp"]
        fields_sent.append("fbp")

    # Client IP address — NOT hashed (Meta needs raw IP for matching)
    if lead.get("ip_address"):
        ud["client_ip_address"] = lead["ip_address"]
        fields_sent.append("client_ip_address")
    elif lead.get("client_ip_address"):
        ud["client_ip_address"] = lead["client_ip_address"]
        fields_sent.append("client_ip_address")

    # Client User Agent — NOT hashed
    if lead.get("user_agent"):
        ud["client_user_agent"] = lead["user_agent"]
        fields_sent.append("client_user_agent")
    elif lead.get("client_user_agent"):
        ud["client_user_agent"] = lead["client_user_agent"]
        fields_sent.append("client_user_agent")

    # External ID — CRM lead ID, SHA-256 hashed
    lead_id = lead.get("id") or lead.get("lead_id")
    if lead_id:
        ud["external_id"] = [_hash(str(lead_id))]
        fields_sent.append("external_id")

    # Store the field list for EMQ tracking (not sent to Meta)
    ud["_fields_sent"] = fields_sent

    return ud


# ── Common Event Sender ──────────────────────────────────────────────────────

async def send_event(event: dict) -> dict:
    """Common sender that handles the API call, deduplication, logging, and errors.

    Takes a pre-built event dict with keys:
      event_name, user_data, custom_data, lead_id (optional)

    Adds event_id (UUID), event_time, event_source_url, action_source.
    Sends to Meta Graph API and logs to capi_events_log.

    Returns dict with ok, event_id, event_name, response, etc.
    """
    event_id = uuid.uuid4().hex
    event_time = int(time.time())
    event_name = event.get("event_name", "Unknown")
    lead_id = event.get("lead_id", 0)
    user_data = event.get("user_data", {})
    custom_data = event.get("custom_data", {})
    action_source = event.get("action_source", ACTION_SOURCE)

    # Extract EMQ field list before sending (not part of API payload)
    fields_sent = user_data.pop("_fields_sent", [])

    # Build the Graph API payload
    event_payload = {
        "event_name": event_name,
        "event_time": event_time,
        "event_id": event_id,
        "event_source_url": EVENT_SOURCE_URL,
        "action_source": action_source,
        "user_data": user_data,
    }
    if custom_data:
        event_payload["custom_data"] = custom_data

    api_payload = {
        "data": [event_payload],
        "access_token": _access_token,
    }

    result = {
        "ok": False,
        "event_name": event_name,
        "event_id": event_id,
        "event_time": event_time,
        "fields_sent": fields_sent,
    }

    if not _access_token:
        result["error"] = "No access token configured"
        _log_lifecycle_event(lead_id, event_name, event_id, event_time,
                            fields_sent, custom_data, result, success=False)
        print(f"[CAPI] {event_name} logged (no token — not sent) id={event_id[:12]}...")
        return result

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{PIXEL_ID}/events"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=api_payload)
            body = resp.json()

            result["ok"] = resp.status_code == 200
            result["status"] = resp.status_code
            result["response"] = body

            if resp.status_code == 200:
                print(f"[CAPI] {event_name} sent OK (id={event_id[:12]}... "
                      f"fields={len(fields_sent)} lead={lead_id})")
            else:
                print(f"[CAPI] {event_name} FAILED: {resp.status_code} — {body}")

    except Exception as e:
        result["error"] = str(e)
        print(f"[CAPI] {event_name} exception: {e}")

    _log_lifecycle_event(lead_id, event_name, event_id, event_time,
                        fields_sent, custom_data, result,
                        success=result.get("ok", False))
    return result


def _log_lifecycle_event(
    lead_id: int,
    event_name: str,
    event_id: str,
    event_time: int,
    fields_sent: list[str],
    custom_data: dict,
    result: dict,
    success: bool,
) -> None:
    """Log lifecycle event to capi_events_log table (best-effort)."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "INSERT INTO capi_events_log "
            "(lead_id, event_name, event_id, event_time, user_data_fields, "
            " custom_data, response, success) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                lead_id,
                event_name,
                event_id,
                datetime.fromtimestamp(event_time, tz=PT).strftime("%Y-%m-%d %H:%M:%S"),
                ",".join(fields_sent),
                json.dumps(custom_data, default=str),
                json.dumps(result, default=str),
                1 if success else 0,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[CAPI] Lifecycle log error: {e}")


# ── Lifecycle Event: QualifiedLead ───────────────────────────────────────────

async def send_qualified_lead_event(lead: dict) -> dict:
    """Send QualifiedLead event when lead is qualified after phone/text conversation.

    This custom event tells Meta's algorithm that this lead was a real,
    qualified prospect — not just a form fill. This is critical for the
    feedback loop: Meta learns which audiences produce qualified leads and
    optimizes ad delivery accordingly.

    Args:
        lead: dict with PII fields (email, phone, first_name, etc.) and
              event_type, id/lead_id.

    Returns:
        dict with ok, event_id, event_name, response, etc.
    """
    user_data = build_user_data(lead)
    lead_id = lead.get("id") or lead.get("lead_id") or 0

    custom_data = {
        "value": 1100.00,
        "currency": "USD",
        "content_name": "Qualified Lead",
        "content_category": lead.get("event_type", "wedding"),
        "status": "qualified",
    }

    print(f"[CAPI] Sending QualifiedLead for lead {lead_id} "
          f"({len(user_data.get('_fields_sent', []))} fields)")

    return await send_event({
        "event_name": "QualifiedLead",
        "user_data": user_data,
        "custom_data": custom_data,
        "lead_id": lead_id,
        "action_source": "system_generated",
    })


# ── Lifecycle Event: Purchase (Booking Confirmed) ────────────────────────────

async def send_lifecycle_purchase_event(lead: dict, amount: float = 1100.00) -> dict:
    """Send Purchase event when booking is confirmed.

    This is the ultimate conversion event — it tells Meta "this lead from
    your ad actually paid $1,100 for a booking." Meta's algorithm uses this
    to find more people like this customer.

    Named send_lifecycle_purchase_event to avoid collision with the existing
    send_purchase_event. Both work; this one uses the enhanced build_user_data
    for higher EMQ scores.

    Args:
        lead: dict with PII fields and event_type, booking_id, id/lead_id.
        amount: booking amount (default $1,100).

    Returns:
        dict with ok, event_id, event_name, response, etc.
    """
    user_data = build_user_data(lead)
    lead_id = lead.get("id") or lead.get("lead_id") or 0

    custom_data = {
        "value": round(amount, 2),
        "currency": "USD",
        "content_name": "Luxury Restroom Booking",
        "content_category": lead.get("event_type", "wedding"),
        "order_id": lead.get("booking_id"),
        "status": "completed",
    }

    print(f"[CAPI] Sending Purchase for lead {lead_id}: ${amount:.2f} "
          f"({len(user_data.get('_fields_sent', []))} fields)")

    return await send_event({
        "event_name": "Purchase",
        "user_data": user_data,
        "custom_data": custom_data,
        "lead_id": lead_id,
        "action_source": "system_generated",
    })


# ── Lifecycle Event: Deposit (InitiateCheckout) ─────────────────────────────

async def send_deposit_event(lead: dict, amount: float = 250.00) -> dict:
    """Send InitiateCheckout event when deposit is collected.

    Signals to Meta that this lead moved from "interested" to "money down."
    Using the standard InitiateCheckout event because Meta's algorithm
    already understands it as a mid-funnel signal.

    Args:
        lead: dict with PII fields and id/lead_id.
        amount: deposit amount (default $250).

    Returns:
        dict with ok, event_id, event_name, response, etc.
    """
    user_data = build_user_data(lead)
    lead_id = lead.get("id") or lead.get("lead_id") or 0

    custom_data = {
        "value": round(amount, 2),
        "currency": "USD",
        "content_name": "Booking Deposit",
        "status": "deposit_paid",
    }

    print(f"[CAPI] Sending InitiateCheckout (deposit) for lead {lead_id}: ${amount:.2f} "
          f"({len(user_data.get('_fields_sent', []))} fields)")

    return await send_event({
        "event_name": "InitiateCheckout",
        "user_data": user_data,
        "custom_data": custom_data,
        "lead_id": lead_id,
        "action_source": "system_generated",
    })


# ── Lifecycle Fire-and-Forget Wrappers ───────────────────────────────────────

def fire_qualified_lead(lead: dict) -> None:
    """Non-blocking QualifiedLead event. Safe to call from sync code."""
    try:
        asyncio.create_task(send_qualified_lead_event(lead))
    except RuntimeError:
        print("[CAPI] No event loop — QualifiedLead event skipped")


def fire_lifecycle_purchase(lead: dict, amount: float = 1100.00) -> None:
    """Non-blocking Purchase event (lifecycle version). Safe to call from sync code."""
    try:
        asyncio.create_task(send_lifecycle_purchase_event(lead, amount))
    except RuntimeError:
        print("[CAPI] No event loop — Purchase (lifecycle) event skipped")


def fire_deposit(lead: dict, amount: float = 250.00) -> None:
    """Non-blocking deposit (InitiateCheckout) event. Safe to call from sync code."""
    try:
        asyncio.create_task(send_deposit_event(lead, amount))
    except RuntimeError:
        print("[CAPI] No event loop — Deposit event skipped")


# ── CAPI Status / Dashboard ─────────────────────────────────────────────────

def get_capi_status() -> dict:
    """Return summary of lifecycle events sent — counts by type, last sent, EMQ info.

    Reads from capi_events_log (the detailed lifecycle log) and capi_events
    (the legacy log) to give a combined picture.

    Returns:
        dict with total_events, by_type (counts), last_event, emq_summary, etc.
    """
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

        # ── Counts by event type from lifecycle log ──
        type_rows = conn.execute(
            "SELECT event_name, COUNT(*) as cnt, SUM(success) as ok_cnt "
            "FROM capi_events_log GROUP BY event_name ORDER BY cnt DESC"
        ).fetchall()

        by_type = {}
        total = 0
        total_ok = 0
        for row in type_rows:
            by_type[row["event_name"]] = {
                "total": row["cnt"],
                "success": row["ok_cnt"] or 0,
            }
            total += row["cnt"]
            total_ok += (row["ok_cnt"] or 0)

        # ── Last event sent ──
        last_row = conn.execute(
            "SELECT event_name, event_id, event_time, lead_id, success "
            "FROM capi_events_log ORDER BY id DESC LIMIT 1"
        ).fetchone()

        last_event = None
        if last_row:
            last_event = {
                "event_name": last_row["event_name"],
                "event_id": last_row["event_id"],
                "event_time": last_row["event_time"],
                "lead_id": last_row["lead_id"],
                "success": bool(last_row["success"]),
            }

        # ── EMQ summary: avg fields sent per event ──
        emq_rows = conn.execute(
            "SELECT event_name, user_data_fields FROM capi_events_log "
            "WHERE user_data_fields IS NOT NULL AND user_data_fields != ''"
        ).fetchall()

        emq_by_type: dict[str, list[int]] = {}
        for row in emq_rows:
            name = row["event_name"]
            field_count = len(row["user_data_fields"].split(",")) if row["user_data_fields"] else 0
            emq_by_type.setdefault(name, []).append(field_count)

        emq_summary = {}
        for name, counts in emq_by_type.items():
            avg = sum(counts) / len(counts) if counts else 0
            emq_summary[name] = {
                "avg_fields": round(avg, 1),
                "min_fields": min(counts) if counts else 0,
                "max_fields": max(counts) if counts else 0,
                "events": len(counts),
            }

        # ── Also include legacy capi_events count ──
        legacy_total = 0
        try:
            legacy_row = conn.execute("SELECT COUNT(*) FROM capi_events").fetchone()
            legacy_total = legacy_row[0] if legacy_row else 0
        except Exception:
            pass

        conn.close()

        return {
            "total_lifecycle_events": total,
            "total_successful": total_ok,
            "success_rate": round(total_ok / total * 100, 1) if total > 0 else 0,
            "by_type": by_type,
            "last_event": last_event,
            "emq_summary": emq_summary,
            "legacy_events_total": legacy_total,
            "pixel_id": PIXEL_ID,
            "has_access_token": bool(_access_token),
            "has_fb_sdk": _HAS_FB_SDK,
        }
    except Exception as e:
        print(f"[CAPI] Status error: {e}")
        return {
            "total_lifecycle_events": 0,
            "error": str(e),
            "pixel_id": PIXEL_ID,
            "has_access_token": bool(_access_token),
            "has_fb_sdk": _HAS_FB_SDK,
        }
