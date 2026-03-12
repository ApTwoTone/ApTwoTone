"""
Calendar Sync — orchestration layer wrapping integrations/google_calendar.py.

Provides:
  - Sync events from Google Calendar → calendar_events table
  - Check availability across bookings + calendar
  - Create Google Calendar events for confirmed bookings

Gracefully handles missing credentials.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any

log = logging.getLogger("calendar_sync")
DB_PATH = Path.home() / ".nexus" / "memory.db"
CREDS_PATH = Path.home() / ".nexus" / "google_service_account.json"


def _conn():
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _has_credentials() -> bool:
    """Check if Google Calendar credentials are configured."""
    return CREDS_PATH.exists()


def _get_gcal_module():
    """Import google_calendar integration, return None if unavailable."""
    try:
        from integrations import google_calendar
        return google_calendar
    except ImportError:
        log.warning("integrations.google_calendar not importable")
        return None


def sync_from_google() -> Dict[str, Any]:
    """Pull events from both Google Calendars into calendar_events table."""
    if not _has_credentials():
        return {"status": "skipped", "reason": "credentials_not_configured",
                "help": "Place Google service account JSON at ~/.nexus/google_service_account.json"}

    gcal = _get_gcal_module()
    if not gcal:
        return {"status": "error", "reason": "google_calendar module not available"}

    try:
        result = gcal.sync_from_calendar()
        return {"status": "ok", "result": result}
    except Exception as e:
        log.error("Calendar sync failed: %s", e)
        return {"status": "error", "reason": str(e)}


def get_calendar_events(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    calendar_account: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get calendar events from local cache."""
    conn = _conn()
    where, params = [], []

    if date_from:
        where.append("start_time >= ?")
        params.append(date_from)
    if date_to:
        where.append("start_time <= ?")
        params.append(date_to + "T23:59:59")
    if calendar_account:
        where.append("calendar_account = ?")
        params.append(calendar_account)

    clause = ("WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        f"SELECT * FROM calendar_events {clause} ORDER BY start_time ASC",
        params
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def check_availability(date_str: str) -> Dict[str, Any]:
    """Check date availability using both bookings and calendar_events tables."""
    from core.booking_db import check_date_available
    return check_date_available(date_str)


def create_booking_event(booking_id: int) -> Dict[str, Any]:
    """Create a Google Calendar event for a confirmed booking."""
    if not _has_credentials():
        return {"status": "skipped", "reason": "credentials_not_configured"}

    conn = _conn()
    booking = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    if not booking:
        conn.close()
        return {"status": "error", "reason": "booking_not_found"}

    gcal = _get_gcal_module()
    if not gcal:
        conn.close()
        return {"status": "error", "reason": "google_calendar module not available"}

    try:
        result = gcal.create_booking(
            lead_id=booking["lead_id"],
            event_date=booking["event_date"],
            location=booking["location"] or booking["event_address"] or "",
            price=booking["price"] or booking["quoted_price"] or 0,
            event_name=booking["event_name"] or f"{booking['event_type']} - {booking['client_name']}",
        )

        # Cache in calendar_events
        if result.get("calendar_event_id"):
            conn.execute(
                """INSERT OR IGNORE INTO calendar_events
                   (booking_id, google_event_id, calendar_account, title,
                    start_time, end_time, location, is_booking, operator)
                   VALUES (?, ?, 'zoarbathrooms@gmail.com', ?, ?, ?, ?, 1, 'both')""",
                (booking_id, result["calendar_event_id"],
                 booking["event_name"] or booking["event_type"],
                 booking["event_date"], booking["event_end_date"] or booking["event_date"],
                 booking["location"] or booking["event_address"] or "")
            )
            conn.commit()

        conn.close()
        return {"status": "ok", "result": result}
    except Exception as e:
        conn.close()
        log.error("Failed to create calendar event for booking %d: %s", booking_id, e)
        return {"status": "error", "reason": str(e)}


def get_availability_range(
    start_date: str,
    end_date: str,
) -> List[Dict[str, Any]]:
    """Get availability for a date range."""
    from datetime import datetime, timedelta

    result = []
    current = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        avail = check_availability(date_str)
        result.append(avail)
        current += timedelta(days=1)

    return result
