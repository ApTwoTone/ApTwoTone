"""
Booking database operations for Zoar Bathroom Rentals.
Handles CRUD for the bookings table + availability checks.

Pattern follows core/vendor_db.py.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

log = logging.getLogger("booking_db")
DB_PATH = Path.home() / ".nexus" / "memory.db"


def _conn():
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _generate_ref() -> str:
    """Generate booking reference like ZBR-2026-0001."""
    conn = _conn()
    year = datetime.now().strftime("%Y")
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM bookings WHERE booking_ref LIKE ?",
        (f"ZBR-{year}-%",)
    ).fetchone()
    seq = (row["cnt"] if row else 0) + 1
    conn.close()
    return f"ZBR-{year}-{seq:04d}"


def get_bookings(
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    """List bookings with optional filters."""
    conn = _conn()
    where, params = [], []

    if status:
        where.append("status = ?")
        params.append(status)
    if date_from:
        where.append("event_date >= ?")
        params.append(date_from)
    if date_to:
        where.append("event_date <= ?")
        params.append(date_to)
    if source:
        where.append("source = ?")
        params.append(source)

    clause = ("WHERE " + " AND ".join(where)) if where else ""

    total = conn.execute(f"SELECT COUNT(*) as cnt FROM bookings {clause}", params).fetchone()["cnt"]
    rows = conn.execute(
        f"SELECT * FROM bookings {clause} ORDER BY event_date ASC LIMIT ? OFFSET ?",
        params + [limit, offset]
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows], total


def get_booking(booking_id: int) -> Optional[Dict[str, Any]]:
    """Get a single booking by ID."""
    conn = _conn()
    row = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_booking(data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new booking and return it."""
    ref = _generate_ref()
    conn = _conn()

    cols = [
        "booking_ref", "lead_id", "vendor_id", "client_name", "client_phone",
        "client_email", "event_name", "event_type", "event_date", "event_end_date",
        "event_start_time", "event_end_time", "event_city", "event_address",
        "location", "guest_count", "stalls_needed", "status", "quoted_price",
        "price", "deposit_amount", "total_paid", "delivery_operator",
        "pickup_operator", "delivery_time", "pickup_time", "source",
        "referral_vendor_id", "referral_fee_paid", "notes",
    ]

    insert_cols = ["booking_ref"]
    insert_vals = [ref]

    for col in cols:
        if col == "booking_ref":
            continue
        if col in data:
            insert_cols.append(col)
            insert_vals.append(data[col])

    placeholders = ", ".join(["?"] * len(insert_cols))
    col_str = ", ".join(insert_cols)

    cursor = conn.execute(
        f"INSERT INTO bookings ({col_str}) VALUES ({placeholders})",
        insert_vals,
    )
    conn.commit()
    booking_id = cursor.lastrowid

    row = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    conn.close()
    log.info("Created booking %s (id=%d)", ref, booking_id)
    return dict(row)


def update_booking(booking_id: int, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Update a booking. Returns updated booking or None if not found."""
    conn = _conn()
    existing = conn.execute("SELECT id FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    if not existing:
        conn.close()
        return None

    allowed = {
        "client_name", "client_phone", "client_email", "event_name",
        "event_type", "event_date", "event_end_date", "event_start_time",
        "event_end_time", "event_city", "event_address", "location",
        "guest_count", "stalls_needed", "status", "quoted_price", "price",
        "deposit_amount", "total_paid", "delivery_operator", "pickup_operator",
        "delivery_time", "pickup_time", "source", "referral_vendor_id",
        "referral_fee_paid", "notes", "calendar_event_id",
    }

    sets, vals = [], []
    for k, v in data.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            vals.append(v)

    if not sets:
        conn.close()
        return get_booking(booking_id)

    sets.append("updated_at = datetime('now')")
    vals.append(booking_id)

    conn.execute(f"UPDATE bookings SET {', '.join(sets)} WHERE id = ?", vals)
    conn.commit()

    row = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    conn.close()
    log.info("Updated booking id=%d", booking_id)
    return dict(row)


def get_booking_stats() -> Dict[str, Any]:
    """Aggregate booking statistics."""
    conn = _conn()
    total = conn.execute("SELECT COUNT(*) as cnt FROM bookings").fetchone()["cnt"]

    status_rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM bookings GROUP BY status"
    ).fetchall()
    by_status = {r["status"]: r["cnt"] for r in status_rows}

    source_rows = conn.execute(
        "SELECT source, COUNT(*) as cnt FROM bookings WHERE source != '' GROUP BY source"
    ).fetchall()
    by_source = {r["source"]: r["cnt"] for r in source_rows}

    revenue = conn.execute("SELECT COALESCE(SUM(total_paid), 0) as rev FROM bookings").fetchone()["rev"]
    pipeline = conn.execute(
        "SELECT COALESCE(SUM(quoted_price), 0) as val FROM bookings WHERE status NOT IN ('cancelled', 'completed')"
    ).fetchone()["val"]

    upcoming = conn.execute(
        "SELECT COUNT(*) as cnt FROM bookings WHERE event_date >= date('now') AND status != 'cancelled'"
    ).fetchone()["cnt"]

    conn.close()
    return {
        "total": total,
        "upcoming": upcoming,
        "by_status": by_status,
        "by_source": by_source,
        "revenue": revenue,
        "pipeline_value": pipeline,
    }


def check_date_available(date_str: str) -> Dict[str, Any]:
    """Check if a date is available for booking."""
    conn = _conn()
    # Check bookings
    booking_conflicts = conn.execute(
        "SELECT id, client_name, event_type, status FROM bookings "
        "WHERE event_date = ? AND status NOT IN ('cancelled')",
        (date_str,)
    ).fetchall()

    # Check calendar events that block availability
    cal_conflicts = conn.execute(
        "SELECT id, title, operator FROM calendar_events "
        "WHERE start_time LIKE ? AND blocks_availability = 1",
        (f"{date_str}%",)
    ).fetchall()

    conflicts = []
    for b in booking_conflicts:
        conflicts.append({"type": "booking", "id": b["id"], "name": b["client_name"],
                          "event_type": b["event_type"], "status": b["status"]})
    for c in cal_conflicts:
        conflicts.append({"type": "calendar", "id": c["id"], "title": c["title"],
                          "operator": c["operator"]})

    conn.close()
    return {
        "date": date_str,
        "available": len(conflicts) == 0,
        "conflicts": conflicts,
    }
