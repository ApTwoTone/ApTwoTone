"""
Vendor Referral Partner Database — CRUD operations, dedup, outreach management.

Tables:
  vendors          — Discovered vendor/referral partner businesses
  vendor_outreach  — Outreach drafts and tracking per vendor

All SQL is parameterized. Uses the same DB path and patterns as b2b_leads.py.
"""
from __future__ import annotations

import logging
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

log = logging.getLogger("vendor_db")

DB_PATH = Path.home() / ".nexus" / "memory.db"
REJECTED_LOG = Path.home() / ".nexus" / "rejected_vendors.log"

# Allowed cities — only vendors in these locations are accepted
ALLOWED_CITIES = {
    # San Fernando Valley
    "chatsworth", "woodland hills", "canoga park", "west hills", "winnetka",
    "reseda", "tarzana", "encino", "sherman oaks", "studio city",
    "north hollywood", "van nuys", "panorama city", "arleta", "pacoima",
    "sun valley", "sunland", "tujunga", "sylmar", "granada hills",
    "northridge", "porter ranch", "san fernando",
    # Nearby cities
    "calabasas", "hidden hills", "agoura hills", "thousand oaks", "simi valley",
    "burbank", "glendale", "pasadena",
    # Santa Clarita
    "santa clarita", "valencia", "newhall", "canyon country",
}


def validate_vendor_location(city: str) -> bool:
    """Check if a vendor's city is in the allowed service area.

    Handles formats like "Van Nuys", "Van Nuys, CA", "Van Nuys, CA 91401".
    Returns True if city is allowed, False if rejected.
    """
    if not city or city.strip().lower() == "none":
        return False
    # Strip state and zip: "Studio City, CA 91604" → "Studio City"
    cleaned = city.split(",")[0].strip().lower()
    return cleaned in ALLOWED_CITIES


def _log_rejection(name: str, city: str, source: str):
    """Log a rejected vendor to both the rejection log file and the vendor_rejections table."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    # File log
    try:
        REJECTED_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(REJECTED_LOG, "a") as f:
            f.write("%s | REJECTED | %s | city=%s | source=%s\n" % (ts, name, city, source))
    except Exception:
        pass
    # Database table
    try:
        _insert_vendor_rejection(
            vendor={"name": name, "city": city, "source": source},
            reason="city not in service area",
            rejection_source=source or "pre_insert_gate",
            rejected_at=ts,
        )
    except Exception:
        pass


def _normalize_domain(website: str) -> str:
    """Normalize website/domain to a comparable hostname."""
    raw = (website or "").strip().lower()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "http://" + raw
    try:
        host = (urlparse(raw).hostname or "").lower()
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def _insert_vendor_rejection(
    vendor: Dict[str, Any],
    reason: str,
    rejection_source: str = "pre_insert_gate",
    rejected_at: Optional[str] = None,
    db_path: Optional[str] = None,
) -> None:
    """Write a rich rejection record to vendor_rejections."""
    conn = _conn(db_path)
    try:
        conn.execute(
            """
            INSERT INTO vendor_rejections (
                business_name, category, city, state, phone, email, website,
                rejection_reason, rejection_source, raw_data, rejected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (vendor.get("name") or vendor.get("business_name") or "").strip(),
                (vendor.get("category") or "").strip(),
                (vendor.get("city") or "").strip(),
                (vendor.get("state") or "").strip(),
                (vendor.get("phone") or "").strip(),
                (vendor.get("email") or "").strip().lower(),
                (vendor.get("website") or "").strip(),
                (reason or "rejected")[:500],
                rejection_source or "pre_insert_gate",
                json.dumps(vendor, default=str)[:4000],
                rejected_at or _now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _find_manual_rejection_reason(
    conn: sqlite3.Connection,
    *,
    name: str,
    email: str,
    website: str,
) -> str:
    """Return prior manual-review rejection reason for matching name/email/domain."""
    normalized_name = (name or "").strip().lower()
    normalized_email = (email or "").strip().lower()
    normalized_domain = _normalize_domain(website)

    if normalized_email:
        row = conn.execute(
            """
            SELECT rejection_reason
            FROM vendor_rejections
            WHERE rejection_source LIKE 'manual_review%' AND LOWER(email) = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (normalized_email,),
        ).fetchone()
        if row:
            return row["rejection_reason"] or "manual review rejection"

    if normalized_domain:
        row = conn.execute(
            """
            SELECT rejection_reason
            FROM vendor_rejections
            WHERE rejection_source LIKE 'manual_review%'
              AND website IS NOT NULL
              AND website != ''
              AND LOWER(website) LIKE ?
            ORDER BY id DESC
            LIMIT 1
            """,
            ("%" + normalized_domain + "%",),
        ).fetchone()
        if row:
            return row["rejection_reason"] or "manual review rejection"

    if normalized_name:
        row = conn.execute(
            """
            SELECT rejection_reason
            FROM vendor_rejections
            WHERE rejection_source LIKE 'manual_review%' AND LOWER(business_name) = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (normalized_name,),
        ).fetchone()
        if row:
            return row["rejection_reason"] or "manual review rejection"

    return ""


# Valid statuses for vendor lifecycle
VENDOR_STATUSES = {
    "new", "researched", "contacted", "responded",
    "partner", "not_interested", "do_not_contact",
}

# Valid outreach statuses
OUTREACH_STATUSES = {
    "draft", "pending_approval", "approved", "sent",
    "replied", "bounced", "cancelled",
}

# Valid outreach channels
OUTREACH_CHANNELS = {"email", "phone", "instagram_dm", "facebook_dm", "in_person"}

# All 13+ vendor categories
VENDOR_CATEGORIES = {
    "wedding_venue", "wedding_planner", "event_planner",
    "quinceanera_venue", "quinceanera_planner",
    "catering", "party_rental", "dj_entertainment",
    "photography", "florist", "bartending_mobile_bar",
    "construction", "festival_organizer",
    "other",
}


def _conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path or DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ── Table Creation ─────────────────────────────────────────────────────────────

def init_vendor_tables(db_path: Optional[str] = None):
    """Create vendors and vendor_outreach tables if they don't exist."""
    conn = _conn(db_path)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS vendors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT DEFAULT '',
            email TEXT DEFAULT '',
            website TEXT DEFAULT '',
            address TEXT DEFAULT '',
            city TEXT DEFAULT '',
            category TEXT DEFAULT 'other',
            source TEXT DEFAULT '',
            rating REAL DEFAULT 0,
            review_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'new',
            outreach_status TEXT DEFAULT 'none',
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(name, phone)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS vendor_outreach (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_id INTEGER NOT NULL,
            channel TEXT DEFAULT 'email',
            message_draft TEXT DEFAULT '',
            status TEXT DEFAULT 'draft',
            approved_at TEXT,
            sent_at TEXT,
            response TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (vendor_id) REFERENCES vendors(id)
        )
    """)

    # Indexes for common queries
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendors_category ON vendors(category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendors_status ON vendors(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendors_city ON vendors(city)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendors_source ON vendors(source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendors_outreach_status ON vendors(outreach_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendor_outreach_vendor ON vendor_outreach(vendor_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendor_outreach_status ON vendor_outreach(status)")

    conn.commit()
    conn.close()
    log.info("Vendor tables initialized")


# ── Vendor CRUD ────────────────────────────────────────────────────────────────

def save_vendor(vendor: Dict[str, Any], db_path: Optional[str] = None) -> Dict[str, Any]:
    """Insert a new vendor or update existing (dedup by name+phone).

    Returns {"action": "created"|"updated"|"skipped", "id": int}
    """
    name = (vendor.get("name") or vendor.get("business_name") or "").strip()
    phone = (vendor.get("phone") or "").strip()
    email = (vendor.get("email") or "").strip().lower()
    website = (vendor.get("website") or "").strip()
    city = (vendor.get("city") or "").strip()

    if not name:
        return {"action": "error", "id": 0, "error": "name is required"}

    # Geographic validation — reject vendors outside allowed service area
    if not validate_vendor_location(city):
        _log_rejection(name, city, vendor.get("source", ""))
        log.debug("Rejected vendor '%s' — city '%s' not in allowed list", name, city)
        return {"action": "rejected", "id": 0, "error": "city not in service area"}

    conn = _conn(db_path)

    # Respect manual review deletions so previously rejected leads stay blocked.
    prior_manual_reason = _find_manual_rejection_reason(
        conn,
        name=name,
        email=email,
        website=website,
    )
    if prior_manual_reason:
        conn.close()
        _insert_vendor_rejection(
            vendor,
            reason="blocked by prior manual rejection: " + prior_manual_reason,
            rejection_source="pre_insert_gate",
            db_path=db_path,
        )
        return {"action": "rejected", "id": 0, "error": "blocked by manual-review history"}

    # Quick vetting gate — category + email syntax (zero-network, <1ms)
    try:
        from core.vendor_vetting import quick_vet
        vet_ok, vet_reason = quick_vet(vendor)
        if not vet_ok:
            conn.close()
            log.info("GATE BLOCKED: '%s' [%s] — %s", name, vendor.get("category", ""), vet_reason)
            return {"action": "rejected", "id": 0, "error": "vetting: " + vet_reason}
    except Exception as e:
        log.warning("Vetting gate import failed: %s — allowing vendor through", e)

    # ── 5-layer dedup ────────────────────────────────────────────────────
    existing = None

    # Layer 1: Normalized phone match (strips formatting differences)
    if phone:
        phone_digits = re.sub(r'\D', '', phone)
        if len(phone_digits) == 11 and phone_digits.startswith('1'):
            phone_digits = phone_digits[1:]
        if len(phone_digits) == 10:
            existing = conn.execute(
                "SELECT * FROM vendors WHERE REPLACE(REPLACE(REPLACE(REPLACE("
                "phone, '-', ''), '(', ''), ')', ''), ' ', '') LIKE ?",
                (f'%{phone_digits}%',)
            ).fetchone()

    # Layer 2: Name + city match (catches vendors with missing/different phones)
    if not existing:
        if city:
            existing = conn.execute(
                "SELECT * FROM vendors WHERE name = ? AND city = ?",
                (name, city)
            ).fetchone()

    # Layer 3: Email match (same email = same business)
    if not existing and email:
        existing = conn.execute(
            "SELECT * FROM vendors WHERE LOWER(email) = ? AND email != ''",
            (email,)
        ).fetchone()

    # Layer 4: Website domain match (same domain = same business)
    if not existing and website:
        domain = re.sub(r'^https?://(www\.)?', '', website.lower()).rstrip('/')
        if domain and len(domain) > 3:
            existing = conn.execute(
                "SELECT * FROM vendors WHERE website != '' AND "
                "LOWER(REPLACE(REPLACE(REPLACE(website, 'https://', ''), "
                "'http://', ''), 'www.', '')) LIKE ?",
                (f'%{domain}%',)
            ).fetchone()

    now = _now()

    if existing:
        existing = dict(existing)
        # Update fields that were previously empty
        updates = {}
        for field in ("phone", "email", "website", "address", "city", "category",
                       "source", "rating", "review_count"):
            new_val = vendor.get(field)
            if new_val is None:
                continue
            if isinstance(new_val, str):
                new_val = new_val.strip()
            old_val = existing.get(field)
            if isinstance(old_val, str):
                old_val = old_val.strip()
            # Update if new value is non-empty and old is empty, or rating/review_count improved
            if field in ("rating", "review_count"):
                if new_val and (not old_val or float(new_val) > float(old_val)):
                    updates[field] = new_val
            elif new_val and not old_val:
                updates[field] = new_val

        if updates:
            updates["updated_at"] = now
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(
                "UPDATE vendors SET %s WHERE id = ?" % set_clause,
                list(updates.values()) + [existing["id"]],
            )
            conn.commit()
            conn.close()
            log.debug("Updated vendor #%d: %s (%d fields)", existing["id"], name, len(updates))
            return {"action": "updated", "id": existing["id"]}

        conn.close()
        return {"action": "skipped", "id": existing["id"]}

    # Insert new vendor
    try:
        cur = conn.execute(
            """INSERT INTO vendors
               (name, phone, email, website, address, city, category,
                source, rating, review_count, status, outreach_status,
                notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', 'none', '', ?, ?)""",
            (
                name,
                phone,
                (vendor.get("email") or "").strip(),
                (vendor.get("website") or "").strip(),
                (vendor.get("address") or "").strip(),
                (vendor.get("city") or "").strip(),
                (vendor.get("category") or "other").strip(),
                (vendor.get("source") or "").strip(),
                float(vendor.get("rating") or 0),
                int(vendor.get("review_count") or 0),
                now,
                now,
            ),
        )
        conn.commit()
        vendor_id = cur.lastrowid
        conn.close()
        log.info("Created vendor #%d: %s [%s]", vendor_id, name, vendor.get("category", "other"))
        return {"action": "created", "id": vendor_id}
    except sqlite3.IntegrityError:
        # Race condition or constraint violation — treat as duplicate
        conn.close()
        log.debug("Vendor already exists (constraint): %s", name)
        return {"action": "skipped", "id": 0}


def get_vendor(vendor_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get a single vendor by ID."""
    conn = _conn(db_path)
    row = conn.execute("SELECT * FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_vendors(
    category: Optional[str] = None,
    status: Optional[str] = None,
    source: Optional[str] = None,
    city: Optional[str] = None,
    search: Optional[str] = None,
    outreach_status: Optional[str] = None,
    send_ready: bool = False,
    limit: int = 100,
    offset: int = 0,
    db_path: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """Get vendors with optional filters. Returns (vendors, total_count)."""
    conn = _conn(db_path)
    where_parts = ["1=1"]
    params = []  # type: List[Any]

    if category:
        where_parts.append("category = ?")
        params.append(category)
    if status:
        where_parts.append("status = ?")
        params.append(status)
    if source:
        where_parts.append("source = ?")
        params.append(source)
    if city:
        where_parts.append("city LIKE ?")
        params.append("%" + city + "%")
    if search:
        where_parts.append("(name LIKE ? OR address LIKE ? OR email LIKE ? OR website LIKE ?)")
        like = "%" + search + "%"
        params.extend([like, like, like, like])
    if outreach_status:
        if outreach_status == "uncontacted":
            where_parts.append("(outreach_status = 'none' OR outreach_status IS NULL OR outreach_status = '')")
        else:
            where_parts.append("outreach_status = ?")
            params.append(outreach_status)
    if send_ready:
        where_parts.append(
            "email != '' AND email IS NOT NULL "
            "AND website != '' AND website IS NOT NULL "
            "AND website NOT LIKE '%example%' "
            "AND COALESCE(website_status, -1) != 0 "
            "AND COALESCE(campaign_quality, 0) >= 40 "
            "AND phone != '' AND phone IS NOT NULL"
        )

    where_clause = " AND ".join(where_parts)

    # Count total
    count_row = conn.execute(
        "SELECT COUNT(*) FROM vendors WHERE %s" % where_clause, params
    ).fetchone()
    total = count_row[0] if count_row else 0

    # Fetch page
    rows = conn.execute(
        "SELECT * FROM vendors WHERE %s ORDER BY created_at DESC LIMIT ? OFFSET ?" % where_clause,
        params + [limit, offset],
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows], total


def update_vendor_status(
    vendor_id: int,
    status: Optional[str] = None,
    outreach_status: Optional[str] = None,
    notes: Optional[str] = None,
    db_path: Optional[str] = None,
) -> bool:
    """Update a vendor's status fields."""
    updates = {}
    if status and status in VENDOR_STATUSES:
        updates["status"] = status
    if outreach_status:
        updates["outreach_status"] = outreach_status
    if notes is not None:
        updates["notes"] = notes

    if not updates:
        return False

    updates["updated_at"] = _now()
    conn = _conn(db_path)
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        "UPDATE vendors SET %s WHERE id = ?" % set_clause,
        list(updates.values()) + [vendor_id],
    )
    conn.commit()
    conn.close()
    log.info("Updated vendor #%d: %s", vendor_id, updates)
    return True


def get_vendor_stats(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Get aggregate statistics across all vendors."""
    conn = _conn(db_path)

    total = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]

    # By category
    cat_rows = conn.execute(
        "SELECT category, COUNT(*) as cnt FROM vendors GROUP BY category ORDER BY cnt DESC"
    ).fetchall()
    by_category = {r["category"]: r["cnt"] for r in cat_rows}

    # By source
    src_rows = conn.execute(
        "SELECT source, COUNT(*) as cnt FROM vendors GROUP BY source ORDER BY cnt DESC"
    ).fetchall()
    by_source = {r["source"]: r["cnt"] for r in src_rows}

    # By status
    status_rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM vendors GROUP BY status ORDER BY cnt DESC"
    ).fetchall()
    by_status = {r["status"]: r["cnt"] for r in status_rows}

    # By city (top 20)
    city_rows = conn.execute(
        "SELECT city, COUNT(*) as cnt FROM vendors WHERE city != '' GROUP BY city ORDER BY cnt DESC LIMIT 20"
    ).fetchall()
    by_city = {r["city"]: r["cnt"] for r in city_rows}

    # Outreach stats
    outreach_total = conn.execute("SELECT COUNT(*) FROM vendor_outreach").fetchone()[0]
    outreach_pending = conn.execute(
        "SELECT COUNT(*) FROM vendor_outreach WHERE status IN ('draft', 'pending_approval')"
    ).fetchone()[0]
    outreach_sent = conn.execute(
        "SELECT COUNT(*) FROM vendor_outreach WHERE status = 'sent'"
    ).fetchone()[0]
    outreach_replied = conn.execute(
        "SELECT COUNT(*) FROM vendor_outreach WHERE status = 'replied'"
    ).fetchone()[0]

    conn.close()
    return {
        "total": total,
        "by_category": by_category,
        "by_source": by_source,
        "by_status": by_status,
        "by_city": by_city,
        "outreach_total": outreach_total,
        "outreach_pending": outreach_pending,
        "outreach_sent": outreach_sent,
        "outreach_replied": outreach_replied,
    }


# ── Outreach CRUD ──────────────────────────────────────────────────────────────

def save_outreach_draft(
    vendor_id: int,
    channel: str,
    message_draft: str,
    db_path: Optional[str] = None,
) -> int:
    """Create an outreach draft for a vendor. Returns outreach ID."""
    if channel not in OUTREACH_CHANNELS:
        channel = "email"

    conn = _conn(db_path)
    cur = conn.execute(
        """INSERT INTO vendor_outreach (vendor_id, channel, message_draft, status, created_at)
           VALUES (?, ?, ?, 'draft', ?)""",
        (vendor_id, channel, message_draft, _now()),
    )
    conn.commit()
    outreach_id = cur.lastrowid

    # Update vendor outreach_status
    conn.execute(
        "UPDATE vendors SET outreach_status = 'draft_ready', updated_at = ? WHERE id = ?",
        (_now(), vendor_id),
    )
    conn.commit()
    conn.close()
    log.info("Created outreach draft #%d for vendor #%d via %s", outreach_id, vendor_id, channel)
    return outreach_id


def approve_outreach(outreach_id: int, db_path: Optional[str] = None) -> bool:
    """Mark an outreach draft as approved."""
    conn = _conn(db_path)
    now = _now()
    conn.execute(
        "UPDATE vendor_outreach SET status = 'approved', approved_at = ? WHERE id = ?",
        (now, outreach_id),
    )
    # Get vendor_id to update their status
    row = conn.execute(
        "SELECT vendor_id FROM vendor_outreach WHERE id = ?", (outreach_id,)
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE vendors SET outreach_status = 'approved', updated_at = ? WHERE id = ?",
            (now, row["vendor_id"]),
        )
    conn.commit()
    conn.close()
    log.info("Approved outreach #%d", outreach_id)
    return True


def mark_outreach_sent(outreach_id: int, db_path: Optional[str] = None) -> bool:
    """Mark an outreach as sent."""
    conn = _conn(db_path)
    now = _now()
    conn.execute(
        "UPDATE vendor_outreach SET status = 'sent', sent_at = ? WHERE id = ?",
        (now, outreach_id),
    )
    row = conn.execute(
        "SELECT vendor_id FROM vendor_outreach WHERE id = ?", (outreach_id,)
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE vendors SET outreach_status = 'sent', status = 'contacted', updated_at = ? WHERE id = ?",
            (now, row["vendor_id"]),
        )
    conn.commit()
    conn.close()
    return True


def record_outreach_response(outreach_id: int, response: str, db_path: Optional[str] = None) -> bool:
    """Record a response to an outreach."""
    conn = _conn(db_path)
    now = _now()
    conn.execute(
        "UPDATE vendor_outreach SET status = 'replied', response = ? WHERE id = ?",
        (response, outreach_id),
    )
    row = conn.execute(
        "SELECT vendor_id FROM vendor_outreach WHERE id = ?", (outreach_id,)
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE vendors SET outreach_status = 'replied', status = 'responded', updated_at = ? WHERE id = ?",
            (now, row["vendor_id"]),
        )
    conn.commit()
    conn.close()
    return True


def get_outreach_queue(
    status: Optional[str] = None,
    limit: int = 50,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get outreach records, optionally filtered by status. Includes vendor name."""
    conn = _conn(db_path)
    query = """
        SELECT vo.*, v.name as vendor_name, v.category as vendor_category,
               v.city as vendor_city, v.email as vendor_email, v.phone as vendor_phone
        FROM vendor_outreach vo
        JOIN vendors v ON vo.vendor_id = v.id
    """
    params = []  # type: List[Any]
    if status:
        query += " WHERE vo.status = ?"
        params.append(status)

    query += " ORDER BY vo.created_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_vendor_outreach(vendor_id: int, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Get all outreach records for a specific vendor."""
    conn = _conn(db_path)
    rows = conn.execute(
        "SELECT * FROM vendor_outreach WHERE vendor_id = ? ORDER BY created_at DESC",
        (vendor_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_vendor_by_email(email: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Find a vendor by email address (case-insensitive)."""
    if not email:
        return None
    conn = _conn(db_path)
    row = conn.execute(
        "SELECT * FROM vendors WHERE lower(email) = ? LIMIT 1",
        (email.lower(),),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_latest_sent_outreach(vendor_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get the most recent sent outreach for a vendor."""
    conn = _conn(db_path)
    row = conn.execute(
        "SELECT * FROM vendor_outreach WHERE vendor_id = ? AND status = 'sent' "
        "ORDER BY sent_at DESC LIMIT 1",
        (vendor_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def reject_and_delete_vendor(
    vendor_id: int,
    reason: str,
    rejected_by: str = "kai",
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Remove a vendor from pipeline + DB and store rejection reason for future blocking."""
    conn = _conn(db_path)
    try:
        row = conn.execute("SELECT * FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
        if not row:
            return {"ok": False, "error": "Vendor not found", "vendor_id": vendor_id}

        vendor = dict(row)
        reason_text = (reason or "").strip() or "Removed during morning batch review"
        source_label = "manual_review" if not rejected_by else ("manual_review:" + rejected_by.strip().lower())

        conn.execute(
            """
            INSERT INTO vendor_rejections (
                business_name, category, city, state, phone, email, website,
                rejection_reason, rejection_source, raw_data, rejected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (vendor.get("name") or "").strip(),
                (vendor.get("category") or "").strip(),
                (vendor.get("city") or "").strip(),
                (vendor.get("state") or "").strip(),
                (vendor.get("phone") or "").strip(),
                (vendor.get("email") or "").strip().lower(),
                (vendor.get("website") or "").strip(),
                reason_text[:500],
                source_label,
                json.dumps(vendor, default=str)[:4000],
                _now(),
            ),
        )

        removed_queue = conn.execute(
            "DELETE FROM email_queue WHERE vendor_id = ?",
            (vendor_id,),
        ).rowcount
        removed_outreach = conn.execute(
            "DELETE FROM vendor_outreach WHERE vendor_id = ?",
            (vendor_id,),
        ).rowcount
        removed_vendor = conn.execute(
            "DELETE FROM vendors WHERE id = ?",
            (vendor_id,),
        ).rowcount
        conn.commit()

        return {
            "ok": True,
            "vendor_id": vendor_id,
            "vendor_name": vendor.get("name", ""),
            "removed_vendor": removed_vendor,
            "removed_queue": removed_queue,
            "removed_outreach": removed_outreach,
            "reason": reason_text,
        }
    finally:
        conn.close()


def run_data_audit(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Run data quality checks on the vendor database."""
    conn = _conn(db_path)

    total = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]

    # Duplicate phones (excluding empty)
    dup_phones = conn.execute(
        "SELECT phone, COUNT(*) as cnt FROM vendors "
        "WHERE phone != '' AND phone IS NOT NULL "
        "GROUP BY phone HAVING cnt > 1 ORDER BY cnt DESC"
    ).fetchall()

    # Placeholder or broken websites
    placeholder_sites = conn.execute(
        "SELECT COUNT(*) FROM vendors WHERE "
        "website LIKE '%example%' OR website LIKE '%test%' "
        "OR website = 'http://' OR website = 'https://' "
        "OR website = '' OR website IS NULL"
    ).fetchone()[0]

    # Missing emails
    missing_email = conn.execute(
        "SELECT COUNT(*) FROM vendors WHERE email = '' OR email IS NULL"
    ).fetchone()[0]

    # Has email
    has_email = total - missing_email

    # Send-ready: has real email + has real website + has phone
    send_ready = conn.execute(
        "SELECT COUNT(*) FROM vendors WHERE "
        "email != '' AND email IS NOT NULL "
        "AND website != '' AND website IS NOT NULL "
        "AND website NOT LIKE '%example%' "
        "AND phone != '' AND phone IS NOT NULL"
    ).fetchone()[0]

    # Vendors with duplicate phone 818-882-1111 specifically
    common_placeholder = conn.execute(
        "SELECT phone, COUNT(*) as cnt FROM vendors "
        "WHERE phone != '' GROUP BY phone ORDER BY cnt DESC LIMIT 5"
    ).fetchall()

    conn.close()

    return {
        "total": total,
        "duplicate_phones": len(dup_phones),
        "duplicate_phone_vendors": sum(r["cnt"] for r in dup_phones),
        "top_duplicate_phones": [
            {"phone": r["phone"], "count": r["cnt"]} for r in dup_phones[:10]
        ],
        "placeholder_or_missing_websites": placeholder_sites,
        "missing_emails": missing_email,
        "has_email": has_email,
        "send_ready": send_ready,
        "send_ready_pct": round(send_ready / max(total, 1) * 100, 1),
        "most_common_phones": [
            {"phone": r["phone"], "count": r["cnt"]} for r in common_placeholder
        ],
    }


def bulk_save_vendors(vendors: List[Dict[str, Any]], db_path: Optional[str] = None) -> Dict[str, int]:
    """Save multiple vendors at once. Returns counts of created/updated/skipped."""
    counts = {"created": 0, "updated": 0, "skipped": 0, "rejected": 0, "errors": 0}
    for v in vendors:
        result = save_vendor(v, db_path=db_path)
        action = result.get("action", "error")
        if action in counts:
            counts[action] += 1
        else:
            counts["errors"] += 1
    log.info(
        "Bulk save: %d created, %d updated, %d skipped, %d rejected, %d errors",
        counts["created"], counts["updated"], counts["skipped"],
        counts["rejected"], counts["errors"],
    )
    return counts
