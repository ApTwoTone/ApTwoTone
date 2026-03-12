from __future__ import annotations
"""
Google Calendar Integration for Zoar Bathroom Rentals
- Manages booking calendar via Google Calendar API (service account auth)
- Local SQLite bookings table synced with Google Calendar events
- Double-booking prevention: checks both local DB + Google Calendar
- Telegram command handlers for /avail, /book, /bookings, /cancel
- Daily reminder scheduler for upcoming bookings (3-day lookahead)
- Multi-day booking support (construction sites, film shoots)
- All dates/times in America/Los_Angeles (Pacific Time)
"""
import asyncio, json, re, sqlite3, traceback
from pathlib import Path
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"
SERVICE_ACCOUNT_PATH = Path.home() / ".nexus" / "google_service_account.json"
PT = ZoneInfo("America/Los_Angeles")

# Google Calendar event color IDs (per Google Calendar API)
COLOR_GREEN = "10"      # Confirmed — Basil (green)
COLOR_YELLOW = "5"      # Tentative — Banana (yellow)

_calendar = None


def _load_config() -> dict:
    """Load config from ~/.nexus/config.json."""
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def _now_pt() -> datetime:
    return datetime.now(PT)


def _today_pt() -> date:
    return _now_pt().date()


def _parse_date(date_str: str) -> date | None:
    """Parse flexible date formats: 2026-03-15, 3/15, 3/15/2026, March 15, etc."""
    date_str = date_str.strip()
    today = _today_pt()

    # ISO format: 2026-03-15
    try:
        return date.fromisoformat(date_str)
    except ValueError:
        pass

    # M/D or M/D/YYYY
    m = re.match(r'^(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?$', date_str)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else today.year
        if year < 100:
            year += 2000
        try:
            d = date(year, month, day)
            # If no year specified and date is in the past, assume next year
            if not m.group(3) and d < today:
                d = date(today.year + 1, month, day)
            return d
        except ValueError:
            return None

    # Month Day (e.g., "March 15", "Mar 15", "march 15th")
    month_names = {
        "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
        "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6,
        "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9,
        "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
    }
    m = re.match(r'^([a-zA-Z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:[,\s]+(\d{4}))?$', date_str)
    if m:
        month_str = m.group(1).lower()
        day = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else today.year
        month = month_names.get(month_str)
        if month:
            try:
                d = date(year, month, day)
                if not m.group(3) and d < today:
                    d = date(today.year + 1, month, day)
                return d
            except ValueError:
                return None

    return None


# ── Database ──────────────────────────────────────────────────────────────────

def init_calendar_db() -> None:
    """Create the bookings table if it doesn't exist."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lead_id INTEGER DEFAULT NULL,
        event_name TEXT DEFAULT '',
        event_date TEXT NOT NULL,
        event_end_date TEXT DEFAULT '',
        location TEXT DEFAULT '',
        status TEXT DEFAULT 'confirmed',
        calendar_event_id TEXT DEFAULT '',
        price REAL DEFAULT 0.0,
        notes TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (lead_id) REFERENCES leads(id)
    );
    CREATE INDEX IF NOT EXISTS idx_bookings_date ON bookings(event_date);
    CREATE INDEX IF NOT EXISTS idx_bookings_status ON bookings(status);
    CREATE INDEX IF NOT EXISTS idx_bookings_lead ON bookings(lead_id);
    """)
    conn.commit()
    conn.close()
    print("[Calendar] Bookings table ready")


# ── Google Calendar API helpers ───────────────────────────────────────────────

def _get_calendar_service():
    """Build and return a Google Calendar API service using service account credentials.

    Returns None with a warning if credentials are not configured.
    """
    config = _load_config()
    creds_path = config.get("google_credentials_path", str(SERVICE_ACCOUNT_PATH))
    creds_file = Path(creds_path)

    if not creds_file.exists():
        print("[Calendar] Warning: Google service account credentials not found at "
              f"{creds_file} — calendar sync disabled")
        return None

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        scopes = ["https://www.googleapis.com/auth/calendar"]
        credentials = service_account.Credentials.from_service_account_file(
            str(creds_file), scopes=scopes
        )
        service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        return service
    except ImportError:
        print("[Calendar] Warning: google-api-python-client or google-auth not installed — "
              "run: pip install google-api-python-client google-auth")
        return None
    except Exception as e:
        print(f"[Calendar] Error building calendar service: {e}")
        return None


def _get_calendar_id() -> str:
    """Get the Google Calendar ID from config, defaulting to 'primary'."""
    config = _load_config()
    return config.get("google_calendar_id", "primary")


def _get_lead_info(lead_id: int | None) -> dict:
    """Fetch lead contact info for calendar event description."""
    if not lead_id:
        return {}
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT first_name, last_name, phone, email FROM leads WHERE id = ?",
            (lead_id,)
        ).fetchone()
        conn.close()
        if row:
            return dict(row)
    except Exception:
        pass
    return {}


def _build_event_body(booking: dict, lead_info: dict) -> dict:
    """Build a Google Calendar event body from booking + lead info."""
    event_name = booking.get("event_name", "Booking")
    location = booking.get("location", "")
    title = f"\U0001f6bf ZOAR \u2014 {event_name}"
    if location:
        title += f" ({location})"

    # Build description with client info
    desc_lines = ["Zoar Bathroom Rentals \u2014 Luxury Restroom Trailer", ""]
    name = f"{lead_info.get('first_name', '')} {lead_info.get('last_name', '')}".strip()
    if name:
        desc_lines.append(f"Client: {name}")
    if lead_info.get("phone"):
        desc_lines.append(f"Phone: {lead_info['phone']}")
    if lead_info.get("email"):
        desc_lines.append(f"Email: {lead_info['email']}")
    price = booking.get("price", 0)
    if price:
        desc_lines.append(f"Price: ${price:,.2f}")
    notes = booking.get("notes", "")
    if notes:
        desc_lines.append(f"\nNotes: {notes}")

    description = "\n".join(desc_lines)

    status = booking.get("status", "confirmed")
    color_id = COLOR_GREEN if status == "confirmed" else COLOR_YELLOW

    event_date = booking["event_date"]
    end_date_str = booking.get("event_end_date", "") or event_date
    # End date for all-day events is exclusive in Google Calendar API
    try:
        end_date_obj = date.fromisoformat(end_date_str) + timedelta(days=1)
        end_date_exclusive = end_date_obj.isoformat()
    except ValueError:
        end_date_exclusive = (date.fromisoformat(event_date) + timedelta(days=1)).isoformat()

    event = {
        "summary": title,
        "description": description,
        "location": location,
        "start": {"date": event_date},
        "end": {"date": end_date_exclusive},
        "colorId": color_id,
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "email", "minutes": 2 * 24 * 60},   # 2 days before
                {"method": "popup", "minutes": 1 * 24 * 60},   # 1 day before
            ],
        },
    }
    return event


def _create_gcal_event(booking: dict, lead_info: dict) -> str | None:
    """Create a Google Calendar event. Returns the event ID or None on failure."""
    service = _get_calendar_service()
    if not service:
        return None
    try:
        cal_id = _get_calendar_id()
        body = _build_event_body(booking, lead_info)
        event = service.events().insert(calendarId=cal_id, body=body).execute()
        event_id = event.get("id", "")
        print(f"[Calendar] Created Google Calendar event: {event_id}")
        return event_id
    except Exception as e:
        print(f"[Calendar] Error creating Google Calendar event: {e}")
        traceback.print_exc()
        return None


def _update_gcal_event(calendar_event_id: str, booking: dict, lead_info: dict) -> bool:
    """Update an existing Google Calendar event. Returns True on success."""
    if not calendar_event_id:
        return False
    service = _get_calendar_service()
    if not service:
        return False
    try:
        cal_id = _get_calendar_id()
        body = _build_event_body(booking, lead_info)
        service.events().update(calendarId=cal_id, eventId=calendar_event_id, body=body).execute()
        print(f"[Calendar] Updated Google Calendar event: {calendar_event_id}")
        return True
    except Exception as e:
        print(f"[Calendar] Error updating Google Calendar event: {e}")
        traceback.print_exc()
        return False


def _delete_gcal_event(calendar_event_id: str) -> bool:
    """Delete a Google Calendar event. Returns True on success."""
    if not calendar_event_id:
        return False
    service = _get_calendar_service()
    if not service:
        return False
    try:
        cal_id = _get_calendar_id()
        service.events().delete(calendarId=cal_id, eventId=calendar_event_id).execute()
        print(f"[Calendar] Deleted Google Calendar event: {calendar_event_id}")
        return True
    except Exception as e:
        print(f"[Calendar] Error deleting Google Calendar event: {e}")
        traceback.print_exc()
        return False


def _fetch_gcal_events(start_date: date, end_date: date) -> list[dict]:
    """Fetch events from Google Calendar for a date range."""
    service = _get_calendar_service()
    if not service:
        return []
    try:
        cal_id = _get_calendar_id()
        time_min = datetime(start_date.year, start_date.month, start_date.day, tzinfo=PT).isoformat()
        # End is exclusive — add one day
        end_next = end_date + timedelta(days=1)
        time_max = datetime(end_next.year, end_next.month, end_next.day, tzinfo=PT).isoformat()

        events_result = service.events().list(
            calendarId=cal_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        events = events_result.get("items", [])
        return events
    except Exception as e:
        print(f"[Calendar] Error fetching Google Calendar events: {e}")
        traceback.print_exc()
        return []


# ── Core booking functions ────────────────────────────────────────────────────

def check_availability(date_str: str) -> dict:
    """Check if a single date is available for booking.

    Returns: {available: bool, date: str, conflicts: list[dict], warnings: list[str]}
    """
    d = _parse_date(date_str)
    if not d:
        return {"available": False, "date": date_str, "conflicts": [],
                "warnings": [], "error": f"Could not parse date: {date_str}"}

    return _check_date(d)


def _check_date(d: date) -> dict:
    """Internal: check a single date against local DB and Google Calendar."""
    date_iso = d.isoformat()
    conflicts = []
    warnings = []

    # Check local DB
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    # A booking covers target date if:
    #   - Single-day (no end date): event_date = target
    #   - Multi-day: event_date <= target AND event_end_date >= target
    rows = conn.execute(
        """SELECT id, event_name, event_date, event_end_date, location, status, price, lead_id
           FROM bookings
           WHERE status != 'cancelled'
             AND (
               (event_end_date != '' AND event_end_date IS NOT NULL
                AND event_date <= ? AND event_end_date >= ?)
               OR
               ((event_end_date = '' OR event_end_date IS NULL) AND event_date = ?)
             )
        """,
        (date_iso, date_iso, date_iso)
    ).fetchall()
    conn.close()

    for row in rows:
        b = dict(row)
        if b["status"] == "confirmed":
            conflicts.append(b)
        elif b["status"] == "tentative":
            warnings.append(f"Tentative booking: {b['event_name']} at {b['location']}")

    # Also check Google Calendar for events not in local DB
    gcal_events = _fetch_gcal_events(d, d)
    for ev in gcal_events:
        summary = ev.get("summary", "")
        # Skip if it's one of ours (already in local DB)
        if "ZOAR" in summary.upper():
            continue
        # External event on the calendar
        warnings.append(f"Calendar event: {summary}")

    available = len(conflicts) == 0
    return {
        "available": available,
        "date": date_iso,
        "conflicts": conflicts,
        "warnings": warnings,
    }


def check_date_range(start_date_str: str, end_date_str: str) -> dict:
    """Check availability for a multi-day range.

    Returns: {available: bool, start: str, end: str, unavailable_dates: list, warnings: list}
    """
    start = _parse_date(start_date_str)
    end = _parse_date(end_date_str)
    if not start:
        return {"available": False, "error": f"Could not parse start date: {start_date_str}"}
    if not end:
        return {"available": False, "error": f"Could not parse end date: {end_date_str}"}
    if end < start:
        return {"available": False, "error": "End date is before start date"}

    unavailable = []
    all_warnings = []
    current = start
    while current <= end:
        result = _check_date(current)
        if not result["available"]:
            unavailable.append({
                "date": current.isoformat(),
                "conflicts": result["conflicts"],
            })
        if result.get("warnings"):
            all_warnings.extend(result["warnings"])
        current += timedelta(days=1)

    return {
        "available": len(unavailable) == 0,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "unavailable_dates": unavailable,
        "warnings": all_warnings,
    }


def create_booking(
    lead_id: int | None,
    event_name: str,
    event_date: str,
    location: str = "",
    price: float = 0.0,
    event_end_date: str = "",
    status: str = "confirmed",
    notes: str = "",
) -> dict:
    """Create a new booking in local DB and Google Calendar.

    Returns: {ok: bool, booking_id: int, calendar_event_id: str, ...} or {ok: False, error: str}
    """
    start_d = _parse_date(event_date)
    if not start_d:
        return {"ok": False, "error": f"Could not parse event date: {event_date}"}

    end_d = None
    if event_end_date:
        end_d = _parse_date(event_end_date)
        if not end_d:
            return {"ok": False, "error": f"Could not parse end date: {event_end_date}"}
        if end_d < start_d:
            return {"ok": False, "error": "End date is before start date"}

    # Double-booking check
    if status == "confirmed":
        if end_d:
            range_check = check_date_range(start_d.isoformat(), end_d.isoformat())
            if not range_check["available"]:
                conflict_dates = [u["date"] for u in range_check.get("unavailable_dates", [])]
                return {
                    "ok": False,
                    "error": "Date conflict — already booked",
                    "conflict_dates": conflict_dates,
                    "unavailable_dates": range_check["unavailable_dates"],
                }
        else:
            avail = _check_date(start_d)
            if not avail["available"]:
                return {
                    "ok": False,
                    "error": "Date conflict — already booked",
                    "conflicts": avail["conflicts"],
                }

    # Insert into local DB
    now = _now_pt().strftime("%Y-%m-%d %H:%M:%S")
    end_date_iso = end_d.isoformat() if end_d else ""
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.execute(
        """INSERT INTO bookings
           (lead_id, event_name, event_date, event_end_date, location, status, price, notes,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (lead_id, event_name, start_d.isoformat(), end_date_iso, location, status,
         price, notes, now, now)
    )
    booking_id = cur.lastrowid
    conn.commit()
    conn.close()

    # Create Google Calendar event
    booking_data = {
        "event_name": event_name,
        "event_date": start_d.isoformat(),
        "event_end_date": end_date_iso,
        "location": location,
        "status": status,
        "price": price,
        "notes": notes,
    }
    lead_info = _get_lead_info(lead_id)
    cal_event_id = _create_gcal_event(booking_data, lead_info)

    # Update booking with calendar event ID
    if cal_event_id:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "UPDATE bookings SET calendar_event_id = ? WHERE id = ?",
            (cal_event_id, booking_id)
        )
        conn.commit()
        conn.close()

    print(f"[Calendar] Booking #{booking_id} created: {event_name} on {start_d.isoformat()}")
    return {
        "ok": True,
        "booking_id": booking_id,
        "calendar_event_id": cal_event_id or "",
        "event_name": event_name,
        "event_date": start_d.isoformat(),
        "event_end_date": end_date_iso,
        "location": location,
        "status": status,
        "price": price,
    }


def update_booking(booking_id: int, **kwargs) -> dict:
    """Update an existing booking's fields and sync to Google Calendar.

    Supported kwargs: event_name, event_date, event_end_date, location, status, price, notes, lead_id
    Returns: {ok: bool, ...}
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    if not row:
        conn.close()
        return {"ok": False, "error": f"Booking #{booking_id} not found"}

    booking = dict(row)
    conn.close()

    # Parse and validate date changes
    if "event_date" in kwargs:
        d = _parse_date(kwargs["event_date"])
        if not d:
            return {"ok": False, "error": f"Could not parse date: {kwargs['event_date']}"}
        kwargs["event_date"] = d.isoformat()

    if "event_end_date" in kwargs and kwargs["event_end_date"]:
        d = _parse_date(kwargs["event_end_date"])
        if not d:
            return {"ok": False, "error": f"Could not parse end date: {kwargs['event_end_date']}"}
        kwargs["event_end_date"] = d.isoformat()

    # If changing to confirmed, check for conflicts (exclude this booking)
    new_status = kwargs.get("status", booking["status"])
    new_date = kwargs.get("event_date", booking["event_date"])
    new_end = kwargs.get("event_end_date", booking["event_end_date"])
    if new_status == "confirmed" and (
        "event_date" in kwargs or "event_end_date" in kwargs or
        (kwargs.get("status") == "confirmed" and booking["status"] != "confirmed")
    ):
        # Temporarily "remove" this booking to check availability
        conn = sqlite3.connect(str(DB_PATH))
        old_status = booking["status"]
        conn.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (booking_id,))
        conn.commit()
        conn.close()

        if new_end:
            range_result = check_date_range(new_date, new_end)
            conflict = not range_result["available"]
        else:
            avail_result = _check_date(date.fromisoformat(new_date))
            conflict = not avail_result["available"]

        # Restore original status
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("UPDATE bookings SET status = ? WHERE id = ?", (old_status, booking_id))
        conn.commit()
        conn.close()

        if conflict:
            return {"ok": False, "error": "Date conflict with another confirmed booking"}

    # Apply updates
    allowed_fields = {"event_name", "event_date", "event_end_date", "location",
                      "status", "price", "notes", "lead_id"}
    updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
    if not updates:
        return {"ok": False, "error": "No valid fields to update"}

    now = _now_pt().strftime("%Y-%m-%d %H:%M:%S")
    updates["updated_at"] = now
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [booking_id]

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(f"UPDATE bookings SET {set_clause} WHERE id = ?", values)
    conn.commit()

    # Re-fetch updated booking
    conn.row_factory = sqlite3.Row
    updated_row = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    conn.close()
    updated = dict(updated_row) if updated_row else booking
    updated.update(updates)

    # Sync to Google Calendar
    cal_event_id = updated.get("calendar_event_id", "")
    lead_info = _get_lead_info(updated.get("lead_id"))
    if cal_event_id:
        _update_gcal_event(cal_event_id, updated, lead_info)
    else:
        # No calendar event yet — create one
        new_cal_id = _create_gcal_event(updated, lead_info)
        if new_cal_id:
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute(
                "UPDATE bookings SET calendar_event_id = ? WHERE id = ?",
                (new_cal_id, booking_id)
            )
            conn.commit()
            conn.close()

    print(f"[Calendar] Booking #{booking_id} updated: {list(updates.keys())}")
    return {"ok": True, "booking_id": booking_id, "updated_fields": list(updates.keys())}


def cancel_booking(booking_id: int) -> dict:
    """Cancel a booking: mark cancelled in DB and remove from Google Calendar.

    Returns: {ok: bool, ...}
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    if not row:
        conn.close()
        return {"ok": False, "error": f"Booking #{booking_id} not found"}

    booking = dict(row)
    if booking["status"] == "cancelled":
        conn.close()
        return {"ok": False, "error": f"Booking #{booking_id} is already cancelled"}

    now = _now_pt().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "UPDATE bookings SET status = 'cancelled', updated_at = ? WHERE id = ?",
        (now, booking_id)
    )
    conn.commit()
    conn.close()

    # Remove from Google Calendar
    cal_event_id = booking.get("calendar_event_id", "")
    if cal_event_id:
        _delete_gcal_event(cal_event_id)

    print(f"[Calendar] Booking #{booking_id} cancelled: {booking['event_name']}")
    return {
        "ok": True,
        "booking_id": booking_id,
        "event_name": booking["event_name"],
        "event_date": booking["event_date"],
    }


def get_upcoming_bookings(days: int = 30) -> list[dict]:
    """Get bookings for the next N days (non-cancelled)."""
    today = _today_pt().isoformat()
    future = (_today_pt() + timedelta(days=days)).isoformat()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Check if leads table exists (it may not if lead_pipeline hasn't run yet)
    has_leads = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='leads'"
    ).fetchone() is not None

    # A booking is "upcoming" if it overlaps [today, future]:
    #   - Its start <= future (starts before window ends)
    #   - Its effective end >= today (ends after window starts)
    #   For single-day bookings (no end date), effective end = start date
    upcoming_where = """
               WHERE b.status != 'cancelled'
                 AND b.event_date <= ?
                 AND (
                   (b.event_end_date != '' AND b.event_end_date IS NOT NULL AND b.event_end_date >= ?)
                   OR
                   ((b.event_end_date = '' OR b.event_end_date IS NULL) AND b.event_date >= ?)
                 )
               ORDER BY b.event_date ASC"""

    if has_leads:
        rows = conn.execute(
            """SELECT b.*, l.first_name, l.last_name, l.phone as lead_phone, l.email as lead_email
               FROM bookings b
               LEFT JOIN leads l ON b.lead_id = l.id"""
            + upcoming_where,
            (future, today, today)
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT b.*, '' as first_name, '' as last_name,
                      '' as lead_phone, '' as lead_email
               FROM bookings b"""
            + upcoming_where,
            (future, today, today)
        ).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def get_booking_by_lead(lead_id: int) -> list[dict]:
    """Find all bookings for a specific lead."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM bookings WHERE lead_id = ? ORDER BY event_date DESC",
        (lead_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_booking(booking_id: int) -> dict | None:
    """Fetch a single booking by ID."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def sync_from_calendar() -> dict:
    """Pull events from Google Calendar into local DB (for manually-added events).

    Scans the next 90 days for ZOAR events not already tracked locally.
    Returns: {synced: int, errors: int}
    """
    today = _today_pt()
    end = today + timedelta(days=90)
    gcal_events = _fetch_gcal_events(today, end)
    if not gcal_events:
        return {"synced": 0, "errors": 0}

    # Get known calendar event IDs
    conn = sqlite3.connect(str(DB_PATH))
    known_ids = {
        r[0] for r in conn.execute(
            "SELECT calendar_event_id FROM bookings WHERE calendar_event_id != ''"
        ).fetchall()
    }
    conn.close()

    synced = 0
    errors = 0
    for ev in gcal_events:
        event_id = ev.get("id", "")
        if event_id in known_ids:
            continue

        summary = ev.get("summary", "")
        # Only sync ZOAR events
        if "ZOAR" not in summary.upper():
            continue

        try:
            # Parse date
            start_info = ev.get("start", {})
            event_date = start_info.get("date", "")
            if not event_date:
                # dateTime format — extract date portion
                dt_str = start_info.get("dateTime", "")
                if dt_str:
                    event_date = dt_str[:10]
            if not event_date:
                continue

            end_info = ev.get("end", {})
            end_date = end_info.get("date", "")
            if end_date:
                # Google's all-day end date is exclusive — subtract a day
                try:
                    end_d = date.fromisoformat(end_date) - timedelta(days=1)
                    end_date = end_d.isoformat()
                except ValueError:
                    end_date = ""
            # If end == start, treat as single-day
            if end_date == event_date:
                end_date = ""

            location = ev.get("location", "")
            description = ev.get("description", "")

            # Try to extract price from description
            price = 0.0
            price_match = re.search(r'\$([0-9,]+(?:\.\d{2})?)', description)
            if price_match:
                price = float(price_match.group(1).replace(",", ""))

            # Clean event name from the ZOAR prefix
            event_name = summary
            zoar_match = re.match(r'.*?ZOAR\s*[\u2014\-]\s*(.+)', summary, re.IGNORECASE)
            if zoar_match:
                event_name = zoar_match.group(1).strip()
                # Remove location suffix if present
                loc_match = re.match(r'^(.+?)\s*\(([^)]+)\)\s*$', event_name)
                if loc_match:
                    event_name = loc_match.group(1).strip()
                    if not location:
                        location = loc_match.group(2).strip()

            # Determine status from color
            color_id = ev.get("colorId", "")
            status = "confirmed" if color_id == COLOR_GREEN or not color_id else "tentative"

            now = _now_pt().strftime("%Y-%m-%d %H:%M:%S")
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute(
                """INSERT INTO bookings
                   (event_name, event_date, event_end_date, location, status,
                    calendar_event_id, price, notes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (event_name, event_date, end_date, location, status,
                 event_id, price, f"Synced from Google Calendar", now, now)
            )
            conn.commit()
            conn.close()
            synced += 1
            print(f"[Calendar] Synced event from Google Calendar: {event_name} on {event_date}")
        except Exception as e:
            print(f"[Calendar] Error syncing event: {e}")
            traceback.print_exc()
            errors += 1

    print(f"[Calendar] Sync complete: {synced} new events, {errors} errors")
    return {"synced": synced, "errors": errors}


# ── Telegram command handlers ─────────────────────────────────────────────────

def handle_availability_command(text: str) -> str:
    """Handle '/avail March 15' or '/avail 3/15-3/17' commands.

    Returns a formatted response string.
    """
    # Strip the command prefix
    parts = text.strip()
    for prefix in ["/avail", "/availability", "/available"]:
        if parts.lower().startswith(prefix):
            parts = parts[len(prefix):].strip()
            break

    if not parts:
        return "Usage: /avail <date> or /avail <start>-<end>\nExamples: /avail March 15, /avail 3/15-3/17"

    # Check for range: "3/15-3/17", "March 15 - March 17", "3/15 - 3/17"
    # Must not split ISO dates like 2026-03-15 on their internal hyphens.
    # Match: a dash/en-dash with spaces around it, OR a dash between date-like tokens
    # that aren't part of an ISO date (i.e., not digit-digit).
    range_match = re.match(
        r'^(.+?)\s+[-\u2013]\s+(.+)$',  # space-dash-space (safe, never splits ISO dates)
        parts
    )
    if not range_match:
        # Also try slash-date ranges: "3/15-3/17" (digits/digits-digits/digits)
        range_match = re.match(
            r'^(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s*-\s*(\d{1,2}/\d{1,2}(?:/\d{2,4})?)$',
            parts
        )
    if range_match:
        start_str = range_match.group(1).strip()
        end_str = range_match.group(2).strip()
        result = check_date_range(start_str, end_str)
        if "error" in result:
            return f"Error: {result['error']}"
        return format_availability_response(
            f"{result['start']} to {result['end']}",
            result["available"],
            result.get("unavailable_dates", []),
            result.get("warnings", []),
        )
    else:
        result = check_availability(parts)
        if "error" in result:
            return f"Error: {result['error']}"
        return format_availability_response(
            result["date"],
            result["available"],
            result.get("conflicts", []),
            result.get("warnings", []),
        )


def handle_book_command(text: str) -> str:
    """Handle '/book [lead_id] [date] [location]' command.

    Returns a formatted response string.
    """
    parts = text.strip()
    for prefix in ["/book"]:
        if parts.lower().startswith(prefix):
            parts = parts[len(prefix):].strip()
            break

    if not parts:
        return ("Usage: /book <lead_id> <date> <location>\n"
                "Example: /book 42 3/15 Malibu Wedding\n"
                "Example: /book 42 3/15-3/17 Construction Site Downtown")

    tokens = parts.split(None, 2)
    if len(tokens) < 2:
        return "Need at least lead ID and date. Usage: /book <lead_id> <date> [location]"

    # Parse lead_id
    try:
        lead_id = int(tokens[0])
    except ValueError:
        return f"Invalid lead ID: {tokens[0]} (must be a number)"

    # Parse date (may include range)
    date_part = tokens[1]
    location = tokens[2] if len(tokens) > 2 else ""

    # Check for date range
    range_match = re.match(r'^(.+?)-(.+)$', date_part)
    event_date = date_part
    event_end_date = ""
    if range_match:
        event_date = range_match.group(1).strip()
        event_end_date = range_match.group(2).strip()

    # Get lead name for event title
    lead_info = _get_lead_info(lead_id)
    if not lead_info:
        return f"Lead #{lead_id} not found"

    lead_name = f"{lead_info.get('first_name', '')} {lead_info.get('last_name', '')}".strip()
    event_name = f"{lead_name} Event"
    if location:
        event_name = f"{lead_name} \u2014 {location}"

    result = create_booking(
        lead_id=lead_id,
        event_name=event_name,
        event_date=event_date,
        event_end_date=event_end_date,
        location=location,
        status="confirmed",
    )

    if result.get("ok"):
        booking = get_booking(result["booking_id"])
        return format_booking_for_telegram(booking) if booking else "Booking created successfully."
    else:
        error = result.get("error", "Unknown error")
        conflicts = result.get("conflicts", [])
        msg = f"Could not create booking: {error}"
        if conflicts:
            for c in conflicts:
                msg += f"\n  Conflict: {c['event_name']} on {c['event_date']}"
        return msg


def handle_bookings_command() -> str:
    """Handle '/bookings' command — show next 30 days of bookings."""
    bookings = get_upcoming_bookings(30)
    if not bookings:
        return "No upcoming bookings in the next 30 days."
    return format_upcoming_bookings(bookings)


def handle_cancel_command(text: str) -> str:
    """Handle '/cancel [booking_id]' command."""
    parts = text.strip()
    for prefix in ["/cancel"]:
        if parts.lower().startswith(prefix):
            parts = parts[len(prefix):].strip()
            break

    if not parts:
        return "Usage: /cancel <booking_id>\nUse /bookings to see booking IDs."

    try:
        booking_id = int(parts)
    except ValueError:
        return f"Invalid booking ID: {parts} (must be a number)"

    # Show the booking before cancelling
    booking = get_booking(booking_id)
    if not booking:
        return f"Booking #{booking_id} not found"

    result = cancel_booking(booking_id)
    if result.get("ok"):
        return (f"Cancelled booking #{booking_id}:\n"
                f"  {result['event_name']}\n"
                f"  Date: {result['event_date']}")
    else:
        return f"Error: {result.get('error', 'Unknown error')}"


# ── Formatting functions ──────────────────────────────────────────────────────

def format_booking_for_telegram(booking: dict) -> str:
    """Format a booking as a nice Telegram card."""
    if not booking:
        return "No booking data."

    status_icon = {
        "confirmed": "\u2705",
        "tentative": "\U0001f7e1",
        "cancelled": "\u274c",
    }
    icon = status_icon.get(booking.get("status", ""), "\u2753")
    status = booking.get("status", "unknown").upper()

    event_date = booking.get("event_date", "")
    end_date = booking.get("event_end_date", "")
    try:
        d = date.fromisoformat(event_date)
        date_display = d.strftime("%A, %B %-d, %Y")
    except (ValueError, TypeError):
        date_display = event_date

    date_line = date_display
    if end_date and end_date != event_date:
        try:
            ed = date.fromisoformat(end_date)
            date_line = f"{date_display} \u2192 {ed.strftime('%A, %B %-d, %Y')}"
        except (ValueError, TypeError):
            date_line = f"{date_display} \u2192 {end_date}"

    lines = [
        f"{icon} *Booking #{booking.get('id', '?')}* [{status}]",
        f"\U0001f4c5 {date_line}",
        f"\U0001f3af {booking.get('event_name', 'N/A')}",
    ]
    if booking.get("location"):
        lines.append(f"\U0001f4cd {booking['location']}")
    if booking.get("price"):
        lines.append(f"\U0001f4b0 ${booking['price']:,.2f}")

    # Lead info (from joined query)
    lead_name = ""
    if booking.get("first_name") or booking.get("last_name"):
        lead_name = f"{booking.get('first_name', '')} {booking.get('last_name', '')}".strip()
    if lead_name:
        lines.append(f"\U0001f464 {lead_name}")
    if booking.get("lead_phone"):
        lines.append(f"\U0001f4de {booking['lead_phone']}")

    if booking.get("notes"):
        lines.append(f"\U0001f4dd {booking['notes']}")

    return "\n".join(lines)


def format_availability_response(
    date_str: str,
    available: bool,
    conflicts: list,
    warnings: list | None = None,
) -> str:
    """Format an availability check result for Telegram."""
    if available:
        msg = f"\u2705 *{date_str}* is AVAILABLE"
        if warnings:
            msg += "\n\n\u26a0\ufe0f Heads up:"
            for w in warnings:
                msg += f"\n  \u2022 {w}"
    else:
        msg = f"\u274c *{date_str}* is NOT AVAILABLE"
        if isinstance(conflicts, list):
            for c in conflicts:
                if isinstance(c, dict):
                    msg += f"\n  \u2022 {c.get('event_name', 'Booking')} at {c.get('location', '?')}"
                else:
                    msg += f"\n  \u2022 {c}"
        if warnings:
            msg += "\n\n\u26a0\ufe0f Also note:"
            for w in warnings:
                msg += f"\n  \u2022 {w}"
    return msg


def format_upcoming_bookings(bookings: list[dict]) -> str:
    """Format a list of upcoming bookings for Telegram."""
    if not bookings:
        return "No upcoming bookings."

    lines = [f"\U0001f4c5 *Upcoming Bookings* ({len(bookings)})\n"]
    for b in bookings:
        status_icon = {
            "confirmed": "\u2705",
            "tentative": "\U0001f7e1",
        }.get(b.get("status", ""), "\u2753")

        try:
            d = date.fromisoformat(b.get("event_date", ""))
            date_display = d.strftime("%b %-d")
        except (ValueError, TypeError):
            date_display = b.get("event_date", "?")

        end_str = ""
        end_date = b.get("event_end_date", "")
        if end_date and end_date != b.get("event_date"):
            try:
                ed = date.fromisoformat(end_date)
                end_str = f"-{ed.strftime('%b %-d')}"
            except (ValueError, TypeError):
                pass

        lead_name = ""
        if b.get("first_name") or b.get("last_name"):
            lead_name = f" \u2014 {b.get('first_name', '')} {b.get('last_name', '')}".strip()

        price_str = f" (${b['price']:,.0f})" if b.get("price") else ""
        location_str = f" @ {b['location']}" if b.get("location") else ""

        lines.append(
            f"{status_icon} *{date_display}{end_str}* #{b['id']}: "
            f"{b.get('event_name', '?')}{location_str}{price_str}{lead_name}"
        )

    return "\n".join(lines)


# ── Scheduler: booking reminders ──────────────────────────────────────────────

async def run_booking_reminders(send_fn) -> None:
    """Async loop: check daily at 8 AM PT for bookings in the next 3 days.

    send_fn: async def(message: str) -> None  (sends to Telegram)
    """
    print("[Calendar] Booking reminder scheduler started")
    while True:
        try:
            now = _now_pt()
            # Calculate seconds until next 8 AM PT
            target = now.replace(hour=8, minute=0, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            wait_seconds = (target - now).total_seconds()
            print(f"[Calendar] Next reminder check at {target.strftime('%Y-%m-%d %H:%M %Z')} "
                  f"({wait_seconds / 3600:.1f}h)")
            await asyncio.sleep(wait_seconds)

            # Check for bookings in the next 3 days
            bookings = get_upcoming_bookings(3)
            if bookings:
                msg_lines = ["\U0001f514 *Booking Reminders*\n"]
                for b in bookings:
                    try:
                        d = date.fromisoformat(b.get("event_date", ""))
                        days_until = (d - _today_pt()).days
                        if days_until == 0:
                            time_label = "TODAY"
                        elif days_until == 1:
                            time_label = "TOMORROW"
                        else:
                            time_label = f"in {days_until} days"
                    except (ValueError, TypeError):
                        time_label = "upcoming"

                    lines = [format_booking_for_telegram(b), f"\u23f0 {time_label}", ""]
                    msg_lines.extend(lines)

                reminder_msg = "\n".join(msg_lines)
                try:
                    await send_fn(reminder_msg)
                    print(f"[Calendar] Sent reminder for {len(bookings)} upcoming bookings")
                except Exception as e:
                    print(f"[Calendar] Error sending reminder: {e}")

        except asyncio.CancelledError:
            print("[Calendar] Reminder scheduler cancelled")
            break
        except Exception as e:
            print(f"[Calendar] Reminder scheduler error: {e}")
            traceback.print_exc()
            # Wait a bit before retrying to avoid tight error loops
            await asyncio.sleep(300)


# ── Module-level init ─────────────────────────────────────────────────────────

def init_calendar(config: dict | None = None) -> str:
    """Initialize the calendar module. Returns error string (empty on success)."""
    global _calendar
    try:
        init_calendar_db()
        # Test Google Calendar connection
        service = _get_calendar_service()
        if service:
            cal_id = _get_calendar_id()
            try:
                cal = service.calendars().get(calendarId=cal_id).execute()
                cal_name = cal.get("summary", cal_id)
                print(f"[Calendar] Connected to Google Calendar: {cal_name}")
            except Exception as e:
                print(f"[Calendar] Could not verify calendar access: {e}")
                return f"Calendar DB ready, but Google Calendar connection failed: {e}"
        else:
            print("[Calendar] Running in local-only mode (no Google Calendar sync)")

        _calendar = True
        return ""
    except Exception as e:
        print(f"[Calendar] Init error: {e}")
        traceback.print_exc()
        return str(e)


def is_calendar_ready() -> bool:
    """Check if the calendar module has been initialized."""
    return _calendar is not None
