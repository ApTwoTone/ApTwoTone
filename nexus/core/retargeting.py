"""
Facebook Custom Audience Builder — Zoar Bathroom Rentals

Syncs CRM leads to Facebook retargeting audiences via the Marketing API:
  - Hot leads: status "hot" or "qualified" in last 90 days
  - Warm leads: created in last 30 days without a booking
  - Cold revival: leads > 60 days old with no recent activity
  - All customers: confirmed bookings (lookalike seed audience)
  - Website visitors: pixel-based placeholder (config only)

PII is SHA256-hashed per Meta spec before any upload.
All outbound actions require Kai's approval via Telegram callbacks.

Telegram commands: /retarget, /retarget sync, /retarget create [type]
Scheduler: Weekly Wednesday 2 AM PT auto-sync

Pixel:     1579580876639654
Ad Account: act_1713830169593455
Graph API:  v21.0

Config:   ~/.nexus/config.json
Database: ~/.nexus/memory.db
"""
from __future__ import annotations
import asyncio, hashlib, json, sqlite3, traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import httpx

# ── Constants ────────────────────────────────────────────────────────────────

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"
PT = ZoneInfo("America/Los_Angeles")

PIXEL_ID = "1579580876639654"
AD_ACCOUNT_ID = "act_1713830169593455"
GRAPH_API_VERSION = "v21.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

AUDIENCE_TYPES = {
    "hot_leads":        "Leads with status hot/qualified in last 90 days",
    "warm_leads":       "Leads created in last 30 days without a booking",
    "cold_revival":     "Leads > 60 days old with no recent activity",
    "all_customers":    "Leads with confirmed bookings (lookalike seed)",
    "website_visitors": "Pixel-based audience (config placeholder)",
}

# How many users per batch upload (Meta recommends <= 10 000)
UPLOAD_BATCH_SIZE = 5000

# ── Helpers ──────────────────────────────────────────────────────────────────

def _now_pt() -> datetime:
    return datetime.now(PT)

def _now_str() -> str:
    return _now_pt().strftime("%Y-%m-%d %H:%M:%S")

def _log(msg: str):
    print(f"[Retarget] {msg}")

def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}

def _get_access_token() -> str:
    """Load FB access token from config."""
    config = _load_config()
    return config.get("fb_page_token", "")

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


# ═════════════════════════════════════════════════════════════════════════════
# DATABASE INIT
# ═════════════════════════════════════════════════════════════════════════════

def init_retargeting_db():
    """Create retargeting_audiences table if it doesn't exist."""
    conn = _get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS retargeting_audiences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        audience_name TEXT NOT NULL,
        fb_audience_id TEXT DEFAULT '',
        audience_type TEXT NOT NULL,
        description TEXT DEFAULT '',
        member_count INTEGER DEFAULT 0,
        last_synced TEXT DEFAULT '',
        sync_status TEXT DEFAULT 'pending',
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_retarget_type ON retargeting_audiences(audience_type);
    CREATE INDEX IF NOT EXISTS idx_retarget_fbid ON retargeting_audiences(fb_audience_id);
    """)
    conn.commit()
    conn.close()
    _log("Database table ready")


# ═════════════════════════════════════════════════════════════════════════════
# PII HASHING — SHA256 per Meta Custom Audience spec
# ═════════════════════════════════════════════════════════════════════════════

def _hash_pii(value: str) -> str:
    """
    SHA256-hash a single PII value per Meta spec.

    Normalization: lowercase, strip whitespace, UTF-8 encode, then SHA256.
    Returns empty string for falsy input.
    """
    if not value:
        return ""
    normalized = value.lower().strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _hash_phone(phone: str) -> str:
    """
    Normalize phone to E.164 digits (US +1 prefix) then hash.

    Meta spec: phone must include country code, digits only.
    """
    if not phone:
        return ""
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 10:
        digits = "1" + digits
    elif len(digits) == 11 and digits[0] == "1":
        pass  # already has country code
    return _hash_pii(digits)


def _build_user_record(lead: dict) -> dict:
    """
    Build a hashed user record for a single lead, per Meta Custom Audience schema.

    Fields: email (em), phone (ph), first name (fn), last name (ln),
            city (ct), state (st), country.
    Each value is an array (Meta supports multi-value matching).
    """
    record: dict = {}

    if lead.get("email"):
        record["em"] = [_hash_pii(lead["email"])]
    if lead.get("phone"):
        record["ph"] = [_hash_phone(lead["phone"])]
    if lead.get("first_name"):
        record["fn"] = [_hash_pii(lead["first_name"])]
    if lead.get("last_name"):
        record["ln"] = [_hash_pii(lead["last_name"])]

    # Zoar is SoCal — always set state=CA, country=US
    record["st"] = [_hash_pii("ca")]
    record["country"] = [_hash_pii("us")]

    return record


# ═════════════════════════════════════════════════════════════════════════════
# CRM LEAD QUERIES — audience segmentation
# ═════════════════════════════════════════════════════════════════════════════

def _get_audience_leads(audience_type: str) -> list[dict]:
    """
    Query CRM leads matching the audience type criteria.

    Returns list of lead dicts with PII fields needed for hashing.
    """
    conn = _get_conn()
    now = _now_pt()
    leads: list[dict] = []

    if audience_type == "hot_leads":
        # Leads with status containing "hot" or "qualified" from last 90 days
        cutoff = (now - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
        rows = conn.execute(
            "SELECT id, first_name, last_name, email, phone FROM leads "
            "WHERE (lower(status) LIKE '%hot%' OR lower(status) LIKE '%qualified%') "
            "AND discovered_at >= ? "
            "AND status NOT IN ('opted_out', 'closed')",
            (cutoff,),
        ).fetchall()
        leads = [dict(r) for r in rows]

    elif audience_type == "warm_leads":
        # Leads created in last 30 days without a confirmed booking
        cutoff = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        rows = conn.execute(
            "SELECT id, first_name, last_name, email, phone FROM leads "
            "WHERE discovered_at >= ? "
            "AND lower(status) NOT IN ('booked', 'purchased', 'opted_out') "
            "AND status NOT IN ('opted_out')",
            (cutoff,),
        ).fetchall()
        leads = [dict(r) for r in rows]

    elif audience_type == "cold_revival":
        # Leads older than 60 days with no recent activity (no reply in last 30 days)
        old_cutoff = (now - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S")
        recent_cutoff = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        rows = conn.execute(
            "SELECT id, first_name, last_name, email, phone FROM leads "
            "WHERE discovered_at < ? "
            "AND (last_reply_at IS NULL OR last_reply_at = '' OR last_reply_at < ?) "
            "AND status NOT IN ('opted_out', 'booked', 'purchased')",
            (old_cutoff, recent_cutoff),
        ).fetchall()
        leads = [dict(r) for r in rows]

    elif audience_type == "all_customers":
        # Leads with confirmed bookings (for lookalike seed)
        rows = conn.execute(
            "SELECT id, first_name, last_name, email, phone FROM leads "
            "WHERE lower(status) IN ('booked', 'purchased', 'confirmed', 'completed') "
            "AND status != 'opted_out'",
        ).fetchall()
        leads = [dict(r) for r in rows]

    elif audience_type == "website_visitors":
        # Pixel-based — no CRM query; this is managed on Meta's side
        _log("website_visitors audience is pixel-based; no CRM leads to query")
        leads = []

    else:
        _log(f"Unknown audience type: {audience_type}")

    conn.close()

    # Filter: must have at least email or phone to be useful
    valid = [l for l in leads if l.get("email") or l.get("phone")]
    _log(f"Audience '{audience_type}': {len(valid)} leads (of {len(leads)} total)")
    return valid


# ═════════════════════════════════════════════════════════════════════════════
# FACEBOOK MARKETING API — Custom Audiences
# ═════════════════════════════════════════════════════════════════════════════

async def create_custom_audience(
    name: str,
    description: str,
    audience_type: str,
    approval_fn=None,
) -> dict:
    """
    Create a new Custom Audience on Facebook via the Marketing API.

    Requires Kai's approval before making the API call.
    Returns {"ok": bool, "audience_id": str, "db_id": int} or error dict.
    """
    if audience_type not in AUDIENCE_TYPES:
        return {"ok": False, "error": f"Invalid audience type: {audience_type}"}

    token = _get_access_token()
    if not token:
        return {"ok": False, "error": "No fb_page_token in config"}

    # ── Kai approval gate ────────────────────────────────────────────────
    if approval_fn:
        approved = await approval_fn(
            f"Create FB Custom Audience?\n"
            f"  Name: {name}\n"
            f"  Type: {audience_type}\n"
            f"  Desc: {description}"
        )
        if not approved:
            _log(f"Audience creation rejected by Kai: {name}")
            return {"ok": False, "error": "Rejected by Kai"}

    # ── Pixel-based website_visitors — just store config, no API call ────
    if audience_type == "website_visitors":
        conn = _get_conn()
        conn.execute(
            "INSERT INTO retargeting_audiences "
            "(audience_name, fb_audience_id, audience_type, description, sync_status) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, f"pixel_{PIXEL_ID}", audience_type, description, "pixel_managed"),
        )
        conn.commit()
        db_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.close()
        _log(f"Website visitors audience registered (pixel-managed): {name}")
        return {"ok": True, "audience_id": f"pixel_{PIXEL_ID}", "db_id": db_id}

    # ── Create audience via Marketing API ────────────────────────────────
    url = f"{GRAPH_BASE}/{AD_ACCOUNT_ID}/customaudiences"
    payload = {
        "name": name,
        "subtype": "CUSTOM",
        "description": description,
        "customer_file_source": "USER_PROVIDED_ONLY",
        "access_token": token,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, data=payload)
            body = resp.json()

            if resp.status_code == 200 and body.get("id"):
                fb_audience_id = body["id"]
                # Store in local DB
                conn = _get_conn()
                conn.execute(
                    "INSERT INTO retargeting_audiences "
                    "(audience_name, fb_audience_id, audience_type, description, sync_status) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (name, fb_audience_id, audience_type, description, "created"),
                )
                conn.commit()
                db_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.close()
                _log(f"Created audience '{name}' → FB ID {fb_audience_id}")
                return {"ok": True, "audience_id": fb_audience_id, "db_id": db_id}
            else:
                error_msg = body.get("error", {}).get("message", str(body))
                _log(f"Failed to create audience '{name}': {resp.status_code} — {error_msg}")
                return {"ok": False, "error": error_msg, "status": resp.status_code}

    except httpx.TimeoutException:
        _log(f"Timeout creating audience '{name}'")
        return {"ok": False, "error": "Request timed out"}
    except Exception as e:
        _log(f"Exception creating audience '{name}': {e}")
        return {"ok": False, "error": str(e)}


async def _upload_users_to_audience(fb_audience_id: str, leads: list[dict]) -> dict:
    """
    Upload hashed user data to a Facebook Custom Audience.

    Batches uploads to UPLOAD_BATCH_SIZE users per request.
    Returns {"ok": bool, "total_uploaded": int, "batches": int}.
    """
    token = _get_access_token()
    if not token:
        return {"ok": False, "error": "No fb_page_token in config"}

    if not leads:
        return {"ok": True, "total_uploaded": 0, "batches": 0}

    url = f"{GRAPH_BASE}/{fb_audience_id}/users"
    total_uploaded = 0
    batch_count = 0
    errors = []

    # Process in batches
    for i in range(0, len(leads), UPLOAD_BATCH_SIZE):
        batch = leads[i : i + UPLOAD_BATCH_SIZE]
        batch_count += 1

        # Build the schema + data payload per Meta spec
        # Schema defines the field order; data contains hashed values in same order
        schema = ["EMAIL", "PHONE", "FN", "LN", "ST", "COUNTRY"]
        data_rows = []

        for lead in batch:
            record = _build_user_record(lead)
            row = [
                record.get("em", [""])[0],
                record.get("ph", [""])[0],
                record.get("fn", [""])[0],
                record.get("ln", [""])[0],
                record.get("st", [""])[0],
                record.get("country", [""])[0],
            ]
            data_rows.append(row)

        payload = {
            "payload": json.dumps({
                "schema": schema,
                "data": data_rows,
            }),
            "access_token": token,
        }

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, data=payload)
                body = resp.json()

                if resp.status_code == 200:
                    num_received = body.get("audience_id", "")
                    total_uploaded += len(batch)
                    _log(f"Batch {batch_count}: uploaded {len(batch)} users to {fb_audience_id}")
                else:
                    error_msg = body.get("error", {}).get("message", str(body))
                    errors.append(f"Batch {batch_count}: {error_msg}")
                    _log(f"Batch {batch_count} failed: {resp.status_code} — {error_msg}")

        except httpx.TimeoutException:
            errors.append(f"Batch {batch_count}: timeout")
            _log(f"Batch {batch_count} timed out")
        except Exception as e:
            errors.append(f"Batch {batch_count}: {e}")
            _log(f"Batch {batch_count} exception: {e}")

        # Polite delay between batches
        if i + UPLOAD_BATCH_SIZE < len(leads):
            await asyncio.sleep(2)

    result = {
        "ok": len(errors) == 0,
        "total_uploaded": total_uploaded,
        "batches": batch_count,
    }
    if errors:
        result["errors"] = errors
    return result


async def sync_audience(audience_id: int, approval_fn=None) -> dict:
    """
    Sync a single audience: fetch matching CRM leads, hash PII, upload to FB.

    audience_id: local DB id from retargeting_audiences table.
    Requires Kai's approval before uploading data.
    Returns sync result dict.
    """
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM retargeting_audiences WHERE id = ?", (audience_id,)
    ).fetchone()
    conn.close()

    if not row:
        return {"ok": False, "error": f"Audience ID {audience_id} not found"}

    audience_type = row["audience_type"]
    fb_audience_id = row["fb_audience_id"]
    audience_name = row["audience_name"]

    if audience_type == "website_visitors":
        _log(f"Skipping sync for pixel-managed audience: {audience_name}")
        return {"ok": True, "skipped": True, "reason": "pixel_managed"}

    if not fb_audience_id:
        return {"ok": False, "error": f"No FB audience ID for '{audience_name}'"}

    # Fetch matching leads
    leads = _get_audience_leads(audience_type)
    member_count = len(leads)

    if member_count == 0:
        # Update DB with zero count
        conn = _get_conn()
        conn.execute(
            "UPDATE retargeting_audiences SET member_count=0, last_synced=?, "
            "sync_status='synced_empty' WHERE id=?",
            (_now_str(), audience_id),
        )
        conn.commit()
        conn.close()
        _log(f"No leads for audience '{audience_name}' — marked empty")
        return {"ok": True, "audience": audience_name, "members": 0}

    # ── Kai approval gate ────────────────────────────────────────────────
    if approval_fn:
        approved = await approval_fn(
            f"Sync audience to Facebook?\n"
            f"  Audience: {audience_name}\n"
            f"  Type: {audience_type}\n"
            f"  Members: {member_count}\n"
            f"  Target: {fb_audience_id}"
        )
        if not approved:
            _log(f"Audience sync rejected by Kai: {audience_name}")
            return {"ok": False, "error": "Rejected by Kai"}

    # Upload hashed data
    _log(f"Syncing {member_count} leads to audience '{audience_name}'...")
    result = await _upload_users_to_audience(fb_audience_id, leads)

    # Update local DB
    sync_status = "synced" if result["ok"] else "sync_error"
    conn = _get_conn()
    conn.execute(
        "UPDATE retargeting_audiences SET member_count=?, last_synced=?, "
        "sync_status=? WHERE id=?",
        (result["total_uploaded"], _now_str(), sync_status, audience_id),
    )
    conn.commit()
    conn.close()

    _log(
        f"Sync complete for '{audience_name}': "
        f"{result['total_uploaded']}/{member_count} uploaded in {result['batches']} batch(es)"
    )
    result["audience"] = audience_name
    result["members"] = member_count
    return result


async def sync_all_audiences(approval_fn=None, send_fn=None) -> list[dict]:
    """
    Sync all active audiences.

    Iterates every audience in retargeting_audiences, syncs each one.
    Sends a Telegram summary when complete.
    """
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, audience_name, audience_type FROM retargeting_audiences "
        "WHERE sync_status != 'pixel_managed' ORDER BY id ASC"
    ).fetchall()
    conn.close()

    if not rows:
        _log("No audiences to sync")
        return []

    results = []
    for row in rows:
        try:
            r = await sync_audience(row["id"], approval_fn=approval_fn)
            results.append(r)
        except Exception as e:
            _log(f"Error syncing audience '{row['audience_name']}': {e}")
            traceback.print_exc()
            results.append({
                "ok": False, "audience": row["audience_name"], "error": str(e),
            })

    # Send summary via Telegram
    if send_fn:
        synced = sum(1 for r in results if r.get("ok"))
        failed = len(results) - synced
        total_members = sum(r.get("total_uploaded", 0) for r in results)

        msg = (
            "\U0001f3af RETARGETING SYNC COMPLETE\n"
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            f"Audiences synced: {synced}/{len(results)}\n"
            f"Total members uploaded: {total_members}\n"
        )
        if failed:
            msg += f"\n\u26a0\ufe0f {failed} audience(s) had errors:\n"
            for r in results:
                if not r.get("ok"):
                    msg += f"  \u2022 {r.get('audience', '?')}: {r.get('error', 'unknown')}\n"

        for r in results:
            if r.get("ok") and not r.get("skipped"):
                msg += (
                    f"\n\u2705 {r.get('audience', '?')}: "
                    f"{r.get('total_uploaded', 0)} members"
                )

        try:
            await send_fn(msg)
        except Exception as e:
            _log(f"Failed to send sync summary: {e}")

    _log(f"Full sync complete: {len(results)} audiences processed")
    return results


# ═════════════════════════════════════════════════════════════════════════════
# LOOKALIKE AUDIENCE
# ═════════════════════════════════════════════════════════════════════════════

async def create_lookalike_audience(
    source_audience_id: int,
    country: str = "US",
    ratio: float = 0.01,
    approval_fn=None,
) -> dict:
    """
    Create a Lookalike Audience from an existing Custom Audience.

    source_audience_id: local DB id of the source audience.
    country: target country code (default US).
    ratio: audience size as fraction of country population (0.01 = 1%).
    Requires Kai's approval.
    """
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM retargeting_audiences WHERE id = ?", (source_audience_id,)
    ).fetchone()
    conn.close()

    if not row:
        return {"ok": False, "error": f"Source audience ID {source_audience_id} not found"}

    fb_source_id = row["fb_audience_id"]
    source_name = row["audience_name"]

    if not fb_source_id or fb_source_id.startswith("pixel_"):
        return {"ok": False, "error": f"Source audience '{source_name}' has no valid FB ID"}

    token = _get_access_token()
    if not token:
        return {"ok": False, "error": "No fb_page_token in config"}

    pct = int(ratio * 100)
    lal_name = f"LAL {pct}% — {source_name} ({country})"

    # ── Kai approval gate ────────────────────────────────────────────────
    if approval_fn:
        approved = await approval_fn(
            f"Create Lookalike Audience?\n"
            f"  Source: {source_name} (FB ID: {fb_source_id})\n"
            f"  Country: {country}\n"
            f"  Size: {pct}% ({ratio})\n"
            f"  Name: {lal_name}"
        )
        if not approved:
            _log(f"Lookalike creation rejected by Kai: {lal_name}")
            return {"ok": False, "error": "Rejected by Kai"}

    url = f"{GRAPH_BASE}/{AD_ACCOUNT_ID}/customaudiences"
    payload = {
        "name": lal_name,
        "subtype": "LOOKALIKE",
        "origin_audience_id": fb_source_id,
        "lookalike_spec": json.dumps({
            "type": "similarity",
            "country": country,
            "ratio": ratio,
        }),
        "access_token": token,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, data=payload)
            body = resp.json()

            if resp.status_code == 200 and body.get("id"):
                lal_fb_id = body["id"]
                # Store in local DB
                conn = _get_conn()
                conn.execute(
                    "INSERT INTO retargeting_audiences "
                    "(audience_name, fb_audience_id, audience_type, description, sync_status) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (lal_name, lal_fb_id, "lookalike", f"Lookalike from {source_name}", "fb_managed"),
                )
                conn.commit()
                db_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.close()
                _log(f"Created lookalike audience '{lal_name}' → FB ID {lal_fb_id}")
                return {"ok": True, "audience_id": lal_fb_id, "db_id": db_id, "name": lal_name}
            else:
                error_msg = body.get("error", {}).get("message", str(body))
                _log(f"Failed to create lookalike '{lal_name}': {resp.status_code} — {error_msg}")
                return {"ok": False, "error": error_msg, "status": resp.status_code}

    except httpx.TimeoutException:
        _log(f"Timeout creating lookalike '{lal_name}'")
        return {"ok": False, "error": "Request timed out"}
    except Exception as e:
        _log(f"Exception creating lookalike '{lal_name}': {e}")
        return {"ok": False, "error": str(e)}


# ═════════════════════════════════════════════════════════════════════════════
# AUDIENCE STATS & FORMATTING
# ═════════════════════════════════════════════════════════════════════════════

def get_audience_stats() -> list[dict]:
    """
    Get overview of all audiences with member counts and sync status.

    Returns list of audience dicts sorted by last synced (most recent first).
    """
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM retargeting_audiences ORDER BY last_synced DESC, created_at DESC"
    ).fetchall()
    conn.close()

    stats = []
    for r in rows:
        last_synced = r["last_synced"] or "never"
        age = ""
        if r["last_synced"]:
            try:
                synced_dt = datetime.strptime(r["last_synced"], "%Y-%m-%d %H:%M:%S")
                synced_dt = synced_dt.replace(tzinfo=PT)
                delta = _now_pt() - synced_dt
                if delta.days > 0:
                    age = f"{delta.days}d ago"
                else:
                    hours = delta.seconds // 3600
                    age = f"{hours}h ago" if hours > 0 else "just now"
            except (ValueError, TypeError):
                age = ""

        stats.append({
            "id": r["id"],
            "name": r["audience_name"],
            "type": r["audience_type"],
            "fb_id": r["fb_audience_id"],
            "members": r["member_count"],
            "last_synced": last_synced,
            "sync_age": age,
            "sync_status": r["sync_status"],
            "description": r["description"],
            "created_at": r["created_at"],
        })
    return stats


def format_audience_summary() -> str:
    """Telegram-formatted audience summary."""
    stats = get_audience_stats()

    if not stats:
        return (
            "\U0001f3af RETARGETING AUDIENCES\n"
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            "No audiences configured yet.\n\n"
            "Create one with:\n"
            "  /retarget create hot_leads\n"
            "  /retarget create warm_leads\n"
            "  /retarget create cold_revival\n"
            "  /retarget create all_customers\n"
            "  /retarget create website_visitors"
        )

    total_members = sum(s["members"] for s in stats)
    msg = (
        "\U0001f3af RETARGETING AUDIENCES\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\U0001f4ca {len(stats)} audience(s) | {total_members:,} total members\n\n"
    )

    status_icons = {
        "synced": "\u2705",
        "created": "\U0001f7e1",
        "pending": "\u23f3",
        "sync_error": "\u274c",
        "synced_empty": "\U0001f4ed",
        "pixel_managed": "\U0001f310",
        "fb_managed": "\U0001f517",
    }

    for s in stats:
        icon = status_icons.get(s["sync_status"], "\u2753")
        sync_info = s["sync_age"] if s["sync_age"] else s["sync_status"]
        msg += (
            f"{icon} {s['name']}\n"
            f"   Type: {s['type']} | Members: {s['members']:,}\n"
            f"   Synced: {sync_info}\n"
        )
        if s["fb_id"]:
            msg += f"   FB ID: {s['fb_id']}\n"
        msg += "\n"

    # Show available lead counts per type
    msg += "\U0001f50d Available leads by segment:\n"
    for atype, desc in AUDIENCE_TYPES.items():
        leads = _get_audience_leads(atype)
        msg += f"   {atype}: {len(leads)} leads\n"

    return msg


# ═════════════════════════════════════════════════════════════════════════════
# TELEGRAM COMMAND HANDLERS
# ═════════════════════════════════════════════════════════════════════════════

def handle_retarget_command(text: str) -> str:
    """/retarget — show audience summary."""
    return format_audience_summary()


async def handle_retarget_sync_command(text: str, approval_fn=None, send_fn=None) -> str:
    """
    /retarget sync — trigger sync of all audiences.

    This is async because it makes HTTP calls to Facebook.
    """
    stats = get_audience_stats()
    if not stats:
        return (
            "\u26a0\ufe0f No audiences to sync.\n"
            "Create one first with /retarget create [type]"
        )

    syncable = [s for s in stats if s["sync_status"] not in ("pixel_managed", "fb_managed")]
    if not syncable:
        return "\u26a0\ufe0f No syncable audiences (all are pixel/FB managed)."

    _log(f"Manual sync triggered for {len(syncable)} audience(s)")
    results = await sync_all_audiences(approval_fn=approval_fn, send_fn=send_fn)

    synced = sum(1 for r in results if r.get("ok"))
    total_uploaded = sum(r.get("total_uploaded", 0) for r in results)

    msg = (
        f"\u2705 Sync complete!\n"
        f"Audiences synced: {synced}/{len(results)}\n"
        f"Total members uploaded: {total_uploaded:,}"
    )
    for r in results:
        if not r.get("ok") and not r.get("skipped"):
            msg += f"\n\u274c {r.get('audience', '?')}: {r.get('error', 'unknown')}"

    return msg


async def handle_retarget_create_command(text: str, approval_fn=None) -> str:
    """
    /retarget create [type] — create a new audience.

    Valid types: hot_leads, warm_leads, cold_revival, all_customers, website_visitors
    """
    usage = (
        "Usage: /retarget create [type]\n\n"
        "Available types:\n"
    )
    for atype, desc in AUDIENCE_TYPES.items():
        leads = _get_audience_leads(atype)
        usage += f"  {atype} — {desc} ({len(leads)} leads)\n"

    # Parse the type from the command text
    stripped = text.strip()
    parts = stripped.split()

    # Find the audience type in the command
    audience_type = None
    for part in parts:
        if part in AUDIENCE_TYPES:
            audience_type = part
            break

    if not audience_type:
        return usage

    # Check if audience of this type already exists
    conn = _get_conn()
    existing = conn.execute(
        "SELECT id, audience_name FROM retargeting_audiences WHERE audience_type = ?",
        (audience_type,),
    ).fetchone()
    conn.close()

    if existing:
        return (
            f"\u26a0\ufe0f An audience of type '{audience_type}' already exists:\n"
            f"  {existing['audience_name']} (ID: {existing['id']})\n\n"
            f"Use /retarget sync to update it."
        )

    # Build audience name and description
    name_map = {
        "hot_leads": "Zoar Hot Leads",
        "warm_leads": "Zoar Warm Leads 30d",
        "cold_revival": "Zoar Cold Revival 60d+",
        "all_customers": "Zoar Customers (Booked)",
        "website_visitors": "Zoar Website Visitors",
    }
    audience_name = name_map.get(audience_type, f"Zoar {audience_type}")
    description = AUDIENCE_TYPES[audience_type]

    result = await create_custom_audience(
        name=audience_name,
        description=description,
        audience_type=audience_type,
        approval_fn=approval_fn,
    )

    if result.get("ok"):
        leads_count = len(_get_audience_leads(audience_type))
        msg = (
            f"\u2705 Created audience: {audience_name}\n"
            f"   Type: {audience_type}\n"
            f"   FB ID: {result.get('audience_id', 'N/A')}\n"
            f"   Available leads: {leads_count}\n\n"
            f"Run /retarget sync to populate it."
        )
        return msg
    else:
        return f"\u274c Failed to create audience: {result.get('error', 'unknown')}"


# ═════════════════════════════════════════════════════════════════════════════
# SCHEDULER — Weekly Wednesday 2 AM PT auto-sync
# ═════════════════════════════════════════════════════════════════════════════

async def run_retargeting_scheduler(send_fn, approval_fn=None) -> None:
    """
    Async scheduler — runs every Wednesday at 2 AM PT.

    Auto-syncs all audiences and sends a summary via Telegram.
    Heartbeats every 60s for watchdog monitoring.
    """
    try:
        from core.watchdog import heartbeat
    except ImportError:
        def heartbeat(_): pass

    _log("Retargeting scheduler started")
    sent_week = ""

    while True:
        try:
            heartbeat("retargeting_scheduler")
            now = _now_pt()
            wk = now.strftime("%Y-W%W")

            # Wednesday = weekday 2, at 2 AM PT
            if now.weekday() == 2 and now.hour == 2 and wk != sent_week:
                _log("Weekly retargeting sync triggered")
                sent_week = wk

                try:
                    results = await sync_all_audiences(
                        approval_fn=approval_fn,
                        send_fn=send_fn,
                    )

                    # Also send the audience overview
                    summary = format_audience_summary()
                    try:
                        await send_fn(summary)
                    except Exception as e:
                        _log(f"Failed to send summary: {e}")

                    _log("Weekly sync complete, summary sent")
                except Exception as e:
                    _log(f"Weekly sync error: {e}")
                    traceback.print_exc()
                    try:
                        await send_fn(
                            f"\u274c Retargeting sync failed:\n{e}"
                        )
                    except Exception:
                        pass

        except Exception as e:
            _log(f"Scheduler error: {e}")
            traceback.print_exc()

        await asyncio.sleep(60)
