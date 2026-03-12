from __future__ import annotations
"""
Booking Confirmation & Management System — Zoar Bathroom Rentals
- Manages the full booking lifecycle: initiate → confirm → deposit → contract → event → complete
- Generates professional confirmation emails/SMS, contracts, and reminder messages
- All outbound messages routed through approval queue (Kai approves via Telegram)
- Cold lead re-engagement with weekly Monday check
- Booking timeline tracking for full audit trail
- SQLite at ~/.nexus/memory.db with WAL mode
"""
import asyncio
import sqlite3
import json
import traceback
import uuid
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DB_PATH = Path.home() / ".nexus" / "memory.db"
LA_TZ = ZoneInfo("America/Los_Angeles")

# ── Constants ─────────────────────────────────────────────────────────────────

ZOAR_PHONE = "(424) 235-8979"
ZOAR_EMAIL = "zoarbathrooms@gmail.com"
ZOAR_WEBSITE = "zoarbathroomrental.com"

DEPOSIT_STATUSES = {"pending", "received", "waived"}
BOOKING_ACTIONS = {
    "created", "confirmed", "deposit_received", "contract_sent",
    "contract_signed", "reminder_sent", "completed", "cancelled",
}

REMINDER_SCHEDULE = [
    {"days_before": 7,  "label": "1 week",   "type": "week_before"},
    {"days_before": 2,  "label": "2 days",    "type": "two_days_before"},
    {"days_before": 0,  "label": "day of",    "type": "day_of"},
    {"days_before": -1, "label": "day after",  "type": "day_after"},
]

COLD_LEAD_DAYS = 30
COLD_CHECK_HOUR = 10  # 10 AM PT on Mondays

_booking_system = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_pt() -> datetime:
    """Current time in Pacific."""
    return datetime.now(LA_TZ)


def _now_str() -> str:
    """UTC timestamp string for DB storage."""
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _format_date(date_str: str) -> str:
    """Format a date string for display (e.g., 'Saturday, March 15, 2026')."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%A, %B %-d, %Y")
    except (ValueError, TypeError):
        return date_str or "TBD"


def _format_price(price: float) -> str:
    """Format price for display."""
    return f"${price:,.2f}"


def _conn() -> sqlite3.Connection:
    """Get a DB connection with WAL mode."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def _generate_booking_id() -> str:
    """Generate a short, human-readable booking ID like 'ZBR-2026-A3F8'."""
    year = _now_pt().year
    suffix = uuid.uuid4().hex[:4].upper()
    return f"ZBR-{year}-{suffix}"


# ── Database Init ─────────────────────────────────────────────────────────────

def init_booking_system_db() -> None:
    """Create booking_confirmations and booking_timeline tables."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS booking_confirmations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lead_id INTEGER NOT NULL,
        booking_id TEXT NOT NULL UNIQUE,
        event_name TEXT DEFAULT '',
        event_date TEXT DEFAULT '',
        event_location TEXT DEFAULT '',
        confirmation_type TEXT DEFAULT 'email',
        confirmation_sent INTEGER DEFAULT 0,
        confirmation_date TEXT DEFAULT '',
        deposit_status TEXT DEFAULT 'pending',
        deposit_amount REAL DEFAULT 0,
        total_price REAL DEFAULT 0,
        contract_sent INTEGER DEFAULT 0,
        contract_signed INTEGER DEFAULT 0,
        status TEXT DEFAULT 'active',
        notes TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (lead_id) REFERENCES leads(id)
    );

    CREATE TABLE IF NOT EXISTS booking_timeline (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        booking_id TEXT NOT NULL,
        action TEXT NOT NULL,
        details TEXT DEFAULT '',
        actor TEXT DEFAULT 'system',
        timestamp TEXT DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_bc_lead ON booking_confirmations(lead_id);
    CREATE INDEX IF NOT EXISTS idx_bc_booking_id ON booking_confirmations(booking_id);
    CREATE INDEX IF NOT EXISTS idx_bc_status ON booking_confirmations(status);
    CREATE INDEX IF NOT EXISTS idx_bc_event_date ON booking_confirmations(event_date);
    CREATE INDEX IF NOT EXISTS idx_bt_booking ON booking_timeline(booking_id);
    """)
    conn.commit()
    conn.close()
    print("[Booking] Database tables ready")


# ── Timeline Logging ──────────────────────────────────────────────────────────

def _log_timeline(booking_id: str, action: str, details: str = "", actor: str = "system") -> None:
    """Log an action to the booking timeline."""
    try:
        conn = _conn()
        conn.execute(
            "INSERT INTO booking_timeline (booking_id, action, details, actor) VALUES (?, ?, ?, ?)",
            (booking_id, action, details, actor)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Booking] Timeline log error: {e}")


# ── Core Booking Functions ────────────────────────────────────────────────────

def _get_lead(lead_id: int) -> dict | None:
    """Fetch a lead by ID."""
    conn = _conn()
    row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def _get_booking(booking_id: str) -> dict | None:
    """Fetch a booking by booking_id."""
    conn = _conn()
    row = conn.execute(
        "SELECT * FROM booking_confirmations WHERE booking_id = ?", (booking_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _get_booking_by_id(db_id: int) -> dict | None:
    """Fetch a booking by database ID."""
    conn = _conn()
    row = conn.execute(
        "SELECT * FROM booking_confirmations WHERE id = ?", (db_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _get_booking_with_lead(booking_id: str) -> dict | None:
    """Fetch booking joined with lead info."""
    conn = _conn()
    row = conn.execute("""
        SELECT b.*, l.first_name, l.last_name, l.email, l.phone, l.event_type,
               l.source, l.event_address, l.guest_count
        FROM booking_confirmations b
        JOIN leads l ON b.lead_id = l.id
        WHERE b.booking_id = ?
    """, (booking_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


async def initiate_booking(
    lead_id: int,
    event_date: str,
    location: str,
    price: float,
    event_name: str | None = None,
) -> dict:
    """
    Create a new booking from a lead.
    - Generates a booking ID
    - Creates booking_confirmations record
    - Attempts to create a Google Calendar hold (lazy import)
    - Queues confirmation message for Kai's approval
    Returns: {"ok": bool, "booking_id": str, ...}
    """
    lead = _get_lead(lead_id)
    if not lead:
        return {"ok": False, "error": f"Lead {lead_id} not found"}

    name = f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip() or "Customer"
    booking_id = _generate_booking_id()

    # Create the booking record
    conn = _conn()
    try:
        conn.execute("""
            INSERT INTO booking_confirmations
            (lead_id, booking_id, event_name, event_date, event_location,
             total_price, deposit_status, status)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', 'active')
        """, (lead_id, booking_id, event_name or "", event_date, location, price))
        conn.commit()
    except Exception as e:
        conn.close()
        return {"ok": False, "error": f"DB error: {e}"}
    conn.close()

    _log_timeline(booking_id, "created",
                  f"Booking initiated for {name} — {_format_date(event_date)} at {location}, {_format_price(price)}")

    # Update lead status to reflect booking
    try:
        conn = _conn()
        conn.execute(
            "UPDATE leads SET booking_status='deposit_pending', event_date=?, "
            "event_address=?, total_quote_amount=?, updated_at=? WHERE id=?",
            (event_date, location, price, _now_str(), lead_id)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Booking] Lead update error: {e}")

    # Try to create a Google Calendar hold (lazy import — being built concurrently)
    try:
        from integrations.google_calendar import create_calendar_hold
        await create_calendar_hold(
            title=f"Zoar Booking: {name} — {event_name or 'Event'}",
            date=event_date,
            location=location,
            notes=f"Booking {booking_id} | {name} | {_format_price(price)}",
        )
        _log_timeline(booking_id, "confirmed", "Google Calendar hold created")
        print(f"[Booking] Calendar hold created for {booking_id}")
    except ImportError:
        print("[Booking] Google Calendar integration not yet available — skipping hold")
    except Exception as e:
        print(f"[Booking] Calendar hold error (non-fatal): {e}")

    # Queue confirmation message for Kai's approval
    try:
        email_msg = generate_confirmation_message(booking_id, channel="email")
        sms_msg = generate_confirmation_message(booking_id, channel="sms")

        from core.approval_queue import queue_message
        if lead.get("email"):
            queue_message(
                lead_id=lead_id, lead_name=name,
                lead_phone=lead.get("phone", ""), lead_email=lead.get("email", ""),
                lead_source=lead.get("source", ""), channel="email",
                message_type="booking_confirmation",
                proposed_message=email_msg["body"],
                proposed_subject=email_msg["subject"],
            )
            print(f"[Booking] Email confirmation queued for approval — {booking_id}")

        if lead.get("phone"):
            queue_message(
                lead_id=lead_id, lead_name=name,
                lead_phone=lead.get("phone", ""), lead_email=lead.get("email", ""),
                lead_source=lead.get("source", ""), channel="sms",
                message_type="booking_confirmation",
                proposed_message=sms_msg["body"],
            )
            print(f"[Booking] SMS confirmation queued for approval — {booking_id}")

    except Exception as e:
        print(f"[Booking] Approval queue error: {e}")
        traceback.print_exc()

    # CRM pipeline transition
    try:
        from core.services import get_pipeline_service
        ps = get_pipeline_service()
        if ps:
            ps.auto_transition_on_action(lead_id, "deposit_sent")
    except Exception:
        pass

    print(f"[Booking] Booking {booking_id} created for lead {lead_id} ({name})")
    return {"ok": True, "booking_id": booking_id, "lead_id": lead_id, "name": name,
            "price": price, "event_date": event_date, "location": location}


# ── Confirmation Message Templates ───────────────────────────────────────────

def generate_confirmation_message(booking_id: str, channel: str = "email") -> dict:
    """
    Generate a professional confirmation message for email or SMS.
    Returns: {"subject": str (email only), "body": str}
    """
    info = _get_booking_with_lead(booking_id)
    if not info:
        return {"subject": "", "body": f"[Error: Booking {booking_id} not found]"}

    first_name = info.get("first_name", "").strip() or "there"
    event_date_fmt = _format_date(info.get("event_date", ""))
    location = info.get("event_location", "your event location")
    price = _format_price(info.get("total_price", 0))
    event_name = info.get("event_name", "").strip()
    deposit = info.get("deposit_amount", 0)

    if channel == "sms":
        body = (
            f"Hi {first_name}! Your luxury restroom trailer is confirmed for "
            f"{event_date_fmt} at {location}. Total: {price}. "
            f"Questions? Call {ZOAR_PHONE} \u2014 Zoar Bathroom Rentals"
        )
        return {"subject": "", "body": body}

    # Email confirmation
    subject = f"Your Zoar Bathroom Rental \u2014 Booking Confirmation #{booking_id}"

    event_line = f" for your {event_name}" if event_name else ""
    deposit_line = ""
    if deposit > 0:
        deposit_line = (
            f"\n\nDeposit: A deposit of {_format_price(deposit)} is required to secure your date. "
            f"We accept Zelle, Venmo, cash, or card."
        )
    elif info.get("deposit_status") == "pending":
        deposit_line = (
            "\n\nDeposit: To secure your date, we'll send deposit details shortly. "
            "We accept Zelle, Venmo, cash, or card."
        )

    body = (
        f"Hey {first_name},\n\n"
        f"Thank you for booking with Zoar Bathroom Rentals{event_line}! "
        f"We're excited to be part of your event.\n\n"
        f"Here are your booking details:\n\n"
        f"  Booking #: {booking_id}\n"
        f"  Date: {event_date_fmt}\n"
        f"  Location: {location}\n"
        f"  Total: {price}\n\n"
        f"What's included:\n"
        f"  \u2022 4-stall luxury restroom trailer\n"
        f"  \u2022 Climate-controlled AC\n"
        f"  \u2022 Running water with flushable toilets\n"
        f"  \u2022 LED interior lighting & mirrors\n"
        f"  \u2022 Bluetooth speaker\n"
        f"  \u2022 Full delivery, setup, and pickup\n"
        f"{deposit_line}\n\n"
        f"Next steps:\n"
        f"  1. We'll send over a simple rental agreement\n"
        f"  2. Confirm deposit to lock in your date\n"
        f"  3. We'll follow up with delivery details as your event approaches\n\n"
        f"Questions? Call or text us anytime at {ZOAR_PHONE}.\n\n"
        f"Looking forward to it!\n\n"
        f"Kai\n"
        f"Zoar Bathroom Rentals\n"
        f"{ZOAR_PHONE} | {ZOAR_EMAIL}"
    )
    return {"subject": subject, "body": body}


def generate_contract_message(booking_id: str) -> dict:
    """
    Generate a simple rental agreement text for a booking.
    Returns: {"subject": str, "body": str}
    """
    info = _get_booking_with_lead(booking_id)
    if not info:
        return {"subject": "", "body": f"[Error: Booking {booking_id} not found]"}

    first_name = info.get("first_name", "").strip() or "Customer"
    last_name = info.get("last_name", "").strip()
    full_name = f"{first_name} {last_name}".strip()
    event_date_fmt = _format_date(info.get("event_date", ""))
    location = info.get("event_location", "TBD")
    price = _format_price(info.get("total_price", 0))
    today = _now_pt().strftime("%B %-d, %Y")

    subject = f"Rental Agreement \u2014 Zoar Bathroom Rentals #{booking_id}"

    body = (
        f"ZOAR BATHROOM RENTALS \u2014 RENTAL AGREEMENT\n"
        f"{'=' * 50}\n\n"
        f"Agreement Date: {today}\n"
        f"Booking #: {booking_id}\n\n"
        f"CLIENT INFORMATION\n"
        f"  Name: {full_name}\n"
        f"  Email: {info.get('email', 'N/A')}\n"
        f"  Phone: {info.get('phone', 'N/A')}\n\n"
        f"EVENT DETAILS\n"
        f"  Date: {event_date_fmt}\n"
        f"  Location: {location}\n\n"
        f"RENTAL DETAILS\n"
        f"  Equipment: 4-Stall Luxury Restroom Trailer\n"
        f"  Includes: Delivery, setup, servicing, and pickup\n"
        f"  Features: AC, running water, flushable toilets, LED lighting, mirrors, Bluetooth speaker\n"
        f"  Total Price: {price}\n\n"
        f"TERMS\n"
        f"  1. Delivery & pickup times will be coordinated 48 hours before the event.\n"
        f"  2. Client is responsible for ensuring adequate access for trailer delivery (min 10ft wide path).\n"
        f"  3. A flat, level surface is required for trailer placement.\n"
        f"  4. Cancellation within 7 days of event forfeits the deposit.\n"
        f"  5. Zoar Bathroom Rentals is not liable for damage caused by guests or weather.\n\n"
        f"By replying 'I agree' to this email, you accept the terms above.\n\n"
        f"{'=' * 50}\n"
        f"Zoar Bathroom Rentals\n"
        f"{ZOAR_PHONE} | {ZOAR_EMAIL}\n"
        f"{ZOAR_WEBSITE}"
    )
    return {"subject": subject, "body": body}


# ── Booking State Updates ─────────────────────────────────────────────────────

def mark_deposit_received(booking_id: str, amount: float) -> dict:
    """Mark deposit as received for a booking."""
    booking = _get_booking(booking_id)
    if not booking:
        return {"ok": False, "error": f"Booking {booking_id} not found"}

    conn = _conn()
    conn.execute(
        "UPDATE booking_confirmations SET deposit_status='received', deposit_amount=?, "
        "updated_at=? WHERE booking_id=?",
        (amount, _now_str(), booking_id)
    )
    conn.commit()
    conn.close()

    _log_timeline(booking_id, "deposit_received",
                  f"Deposit of {_format_price(amount)} received")

    # Update lead deposit tracking
    try:
        lead_id = booking["lead_id"]
        conn = _conn()
        conn.execute(
            "UPDATE leads SET deposit_status='received', deposit_amount=?, updated_at=? WHERE id=?",
            (amount, _now_str(), lead_id)
        )
        conn.commit()
        conn.close()

        # Advance CRM pipeline
        from core.services import get_pipeline_service
        ps = get_pipeline_service()
        if ps:
            ps.auto_transition_on_action(lead_id, "deposit_paid")
    except Exception as e:
        print(f"[Booking] Lead deposit update error: {e}")

    print(f"[Booking] Deposit received for {booking_id}: {_format_price(amount)}")
    return {"ok": True, "booking_id": booking_id, "amount": amount}


def mark_contract_signed(booking_id: str) -> dict:
    """Mark contract as signed for a booking."""
    booking = _get_booking(booking_id)
    if not booking:
        return {"ok": False, "error": f"Booking {booking_id} not found"}

    conn = _conn()
    conn.execute(
        "UPDATE booking_confirmations SET contract_signed=1, updated_at=? WHERE booking_id=?",
        (_now_str(), booking_id)
    )
    conn.commit()
    conn.close()

    _log_timeline(booking_id, "contract_signed", "Rental agreement signed by client")
    print(f"[Booking] Contract signed for {booking_id}")
    return {"ok": True, "booking_id": booking_id}


def send_contract(booking_id: str) -> dict:
    """
    Queue contract message for Kai's approval.
    Does NOT auto-send. Everything goes through approval.
    """
    booking = _get_booking(booking_id)
    if not booking:
        return {"ok": False, "error": f"Booking {booking_id} not found"}

    lead = _get_lead(booking["lead_id"])
    if not lead:
        return {"ok": False, "error": "Lead not found for booking"}

    name = f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip() or "Customer"
    contract = generate_contract_message(booking_id)

    try:
        from core.approval_queue import queue_message
        if lead.get("email"):
            queue_message(
                lead_id=booking["lead_id"], lead_name=name,
                lead_phone=lead.get("phone", ""), lead_email=lead.get("email", ""),
                lead_source=lead.get("source", ""), channel="email",
                message_type="contract",
                proposed_message=contract["body"],
                proposed_subject=contract["subject"],
            )
            # Mark contract as queued (not sent until approved)
            conn = _conn()
            conn.execute(
                "UPDATE booking_confirmations SET contract_sent=1, updated_at=? WHERE booking_id=?",
                (_now_str(), booking_id)
            )
            conn.commit()
            conn.close()

            _log_timeline(booking_id, "contract_sent", "Rental agreement queued for approval")
            print(f"[Booking] Contract queued for approval — {booking_id}")
            return {"ok": True, "booking_id": booking_id}
    except Exception as e:
        print(f"[Booking] Contract queue error: {e}")
        return {"ok": False, "error": str(e)}

    return {"ok": False, "error": "No email on file for lead"}


def complete_booking(booking_id: str) -> dict:
    """Mark a booking as completed after the event."""
    booking = _get_booking(booking_id)
    if not booking:
        return {"ok": False, "error": f"Booking {booking_id} not found"}

    conn = _conn()
    conn.execute(
        "UPDATE booking_confirmations SET status='completed', updated_at=? WHERE booking_id=?",
        (_now_str(), booking_id)
    )
    conn.commit()
    conn.close()

    _log_timeline(booking_id, "completed", "Event completed successfully")

    # Advance CRM pipeline
    try:
        lead_id = booking["lead_id"]
        from core.services import get_pipeline_service
        ps = get_pipeline_service()
        if ps:
            ps.auto_transition_on_action(lead_id, "event_completed")
    except Exception as e:
        print(f"[Booking] Pipeline transition error: {e}")

    print(f"[Booking] Booking {booking_id} marked as completed")
    return {"ok": True, "booking_id": booking_id}


def cancel_booking(booking_id: str, reason: str = "") -> dict:
    """Cancel a booking."""
    booking = _get_booking(booking_id)
    if not booking:
        return {"ok": False, "error": f"Booking {booking_id} not found"}

    conn = _conn()
    conn.execute(
        "UPDATE booking_confirmations SET status='cancelled', notes=?, updated_at=? WHERE booking_id=?",
        (reason, _now_str(), booking_id)
    )
    conn.commit()
    conn.close()

    _log_timeline(booking_id, "cancelled", reason or "Booking cancelled")
    print(f"[Booking] Booking {booking_id} cancelled: {reason}")
    return {"ok": True, "booking_id": booking_id}


# ── Booking Pipeline Overview ─────────────────────────────────────────────────

def get_booking_pipeline() -> list[dict]:
    """Get an overview of all active bookings and their status."""
    conn = _conn()
    rows = conn.execute("""
        SELECT b.*, l.first_name, l.last_name, l.phone, l.email, l.event_type
        FROM booking_confirmations b
        JOIN leads l ON b.lead_id = l.id
        WHERE b.status IN ('active', 'completed')
        ORDER BY b.event_date ASC
    """).fetchall()
    conn.close()

    bookings = []
    for row in rows:
        r = dict(row)
        r["lead_name"] = f"{r.get('first_name', '')} {r.get('last_name', '')}".strip()
        # Determine pipeline stage
        if r["status"] == "completed":
            r["stage"] = "complete"
        elif r.get("contract_signed"):
            r["stage"] = "contract_signed"
        elif r.get("contract_sent"):
            r["stage"] = "contract_sent"
        elif r.get("deposit_status") == "received":
            r["stage"] = "deposit_received"
        elif r.get("confirmation_sent"):
            r["stage"] = "confirmed"
        else:
            r["stage"] = "pending_confirmation"
        bookings.append(r)

    return bookings


def get_booking_timeline_entries(booking_id: str) -> list[dict]:
    """Get chronological timeline of a booking."""
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM booking_timeline WHERE booking_id = ? ORDER BY timestamp ASC",
        (booking_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Booking Reminders ─────────────────────────────────────────────────────────

def _generate_reminder_message(booking_id: str, reminder_type: str) -> dict:
    """Generate a reminder message based on type."""
    info = _get_booking_with_lead(booking_id)
    if not info:
        return {"body": "", "subject": ""}

    first_name = info.get("first_name", "").strip() or "there"
    event_date_fmt = _format_date(info.get("event_date", ""))
    location = info.get("event_location", "your event location")

    if reminder_type == "week_before":
        body = (
            f"Hi {first_name}! Your event is in 1 week! Just a friendly reminder that "
            f"your luxury restroom trailer is confirmed for {event_date_fmt} at {location}. "
            f"We'll reach out soon with delivery details. Questions? {ZOAR_PHONE} \u2014 Zoar"
        )
    elif reminder_type == "two_days_before":
        body = (
            f"Hi {first_name}! Delivery for your luxury restroom trailer is scheduled for "
            f"{event_date_fmt} at {location}. We'll arrive by 10 AM for setup. "
            f"Please make sure we have clear access (10ft wide). See you soon! \u2014 Zoar"
        )
    elif reminder_type == "day_of":
        body = (
            f"Hi {first_name}! Your luxury restroom trailer is being delivered today! "
            f"Our team is heading to {location}. We'll have everything set up and ready to go. "
            f"Have an amazing event! \u2014 Zoar Bathroom Rentals"
        )
    elif reminder_type == "day_after":
        body = (
            f"Hi {first_name}! Thank you for choosing Zoar Bathroom Rentals for your event! "
            f"We hope everything was perfect. We'd love a quick review if you have a moment \u2014 "
            f"it really helps us out. Thanks again! \u2014 Kai, Zoar"
        )
    else:
        body = f"Hi {first_name}, just checking in about your upcoming event on {event_date_fmt}. \u2014 Zoar"

    subject = f"Reminder: Your Zoar Rental \u2014 {event_date_fmt}"
    return {"body": body, "subject": subject}


async def check_and_queue_reminders() -> int:
    """
    Check all active bookings for upcoming events and queue reminder messages.
    Returns number of reminders queued.
    All reminders go through approval — never auto-sent.
    """
    today = _now_pt().date()
    queued = 0

    conn = _conn()
    bookings = conn.execute("""
        SELECT b.*, l.first_name, l.last_name, l.phone, l.email, l.source
        FROM booking_confirmations b
        JOIN leads l ON b.lead_id = l.id
        WHERE b.status = 'active'
    """).fetchall()
    conn.close()

    for row in bookings:
        booking = dict(row)
        booking_id = booking["booking_id"]
        try:
            event_date = datetime.strptime(booking.get("event_date", ""), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue

        days_until = (event_date - today).days

        for schedule in REMINDER_SCHEDULE:
            if days_until != schedule["days_before"]:
                continue

            # Check if this reminder type was already sent
            conn = _conn()
            already_sent = conn.execute(
                "SELECT COUNT(*) FROM booking_timeline WHERE booking_id=? AND action='reminder_sent' "
                "AND details LIKE ?",
                (booking_id, f"%{schedule['type']}%")
            ).fetchone()[0]
            conn.close()

            if already_sent > 0:
                continue

            # Generate and queue the reminder
            reminder = _generate_reminder_message(booking_id, schedule["type"])
            name = f"{booking.get('first_name', '')} {booking.get('last_name', '')}".strip()

            try:
                from core.approval_queue import queue_message
                if booking.get("phone"):
                    queue_message(
                        lead_id=booking["lead_id"], lead_name=name,
                        lead_phone=booking.get("phone", ""),
                        lead_email=booking.get("email", ""),
                        lead_source=booking.get("source", ""),
                        channel="sms", message_type="booking_reminder",
                        proposed_message=reminder["body"],
                    )
                    _log_timeline(booking_id, "reminder_sent",
                                  f"Reminder queued ({schedule['type']}): {schedule['label']} before event")
                    queued += 1
                    print(f"[Booking] Reminder queued for {booking_id}: {schedule['label']}")
            except Exception as e:
                print(f"[Booking] Reminder queue error for {booking_id}: {e}")

    return queued


# ── Cold Lead Re-Engagement ───────────────────────────────────────────────────

def get_cold_leads(days: int = COLD_LEAD_DAYS) -> list[dict]:
    """
    Find leads with no activity in the last N days.
    Excludes closed, opted_out, booked, and completed leads.
    """
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = _conn()
    rows = conn.execute("""
        SELECT * FROM leads
        WHERE status NOT IN ('closed', 'opted_out')
        AND booking_status NOT IN ('booked', 'completed', 'lost')
        AND updated_at <= ?
        AND updated_at != ''
        ORDER BY updated_at ASC
        LIMIT 20
    """, (cutoff,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def generate_reengagement_message(lead_id: int) -> dict:
    """
    Generate a warm re-engagement message for a cold lead.
    Returns: {"body": str, "subject": str}
    """
    lead = _get_lead(lead_id)
    if not lead:
        return {"body": "", "subject": ""}

    first_name = lead.get("first_name", "").strip() or "there"
    event_type = lead.get("event_type", "").strip()
    month = _now_pt().strftime("%B")

    event_phrase = f"your {event_type}" if event_type else "your upcoming event"
    season = _get_season()

    body = (
        f"Hi {first_name}, we noticed you inquired about our luxury restroom trailer "
        f"for {event_phrase}. Still planning? {season} dates are filling up fast \u2014 "
        f"we'd love to help make your event unforgettable. "
        f"Call or text us anytime: {ZOAR_PHONE} \u2014 Kai, Zoar"
    )
    subject = f"Still planning {event_phrase}? \u2014 Zoar Bathroom Rentals"
    return {"body": body, "subject": subject}


def _get_season() -> str:
    """Get current season name for messaging."""
    month = _now_pt().month
    if month in (3, 4, 5):
        return "Spring"
    elif month in (6, 7, 8):
        return "Summer"
    elif month in (9, 10, 11):
        return "Fall"
    else:
        return "Winter"


async def run_cold_lead_check(send_fn=None) -> None:
    """
    Async loop: check for cold leads every Monday at 10 AM PT.
    Queues re-engagement messages for approval — never auto-sends.
    """
    print("[Booking] Cold lead check loop started")
    while True:
        try:
            now = _now_pt()
            # Only run on Mondays at the configured hour
            if now.weekday() == 0 and now.hour == COLD_CHECK_HOUR and now.minute < 5:
                cold = get_cold_leads()
                if cold:
                    print(f"[Booking] Found {len(cold)} cold leads for re-engagement")
                    for lead in cold:
                        lead_id = lead["id"]
                        name = f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip()
                        msg = generate_reengagement_message(lead_id)

                        try:
                            from core.approval_queue import queue_message
                            if lead.get("phone"):
                                queue_message(
                                    lead_id=lead_id, lead_name=name,
                                    lead_phone=lead.get("phone", ""),
                                    lead_email=lead.get("email", ""),
                                    lead_source=lead.get("source", ""),
                                    channel="sms", message_type="reengagement",
                                    proposed_message=msg["body"],
                                )
                                print(f"[Booking] Re-engagement queued for {name} (lead {lead_id})")
                            elif lead.get("email"):
                                queue_message(
                                    lead_id=lead_id, lead_name=name,
                                    lead_phone="", lead_email=lead.get("email", ""),
                                    lead_source=lead.get("source", ""),
                                    channel="email", message_type="reengagement",
                                    proposed_message=msg["body"],
                                    proposed_subject=msg["subject"],
                                )
                                print(f"[Booking] Re-engagement email queued for {name} (lead {lead_id})")
                        except Exception as e:
                            print(f"[Booking] Re-engagement queue error for lead {lead_id}: {e}")

                    # Notify Kai via Telegram
                    if send_fn:
                        try:
                            await send_fn(
                                f"[Booking] Found {len(cold)} cold leads (30+ days). "
                                f"Re-engagement messages queued for your approval."
                            )
                        except Exception:
                            pass
                else:
                    print("[Booking] No cold leads found this week")

                # Wait until next hour to avoid duplicate runs within the 5-minute window
                await asyncio.sleep(3600)
            else:
                # Check every 5 minutes
                await asyncio.sleep(300)
        except Exception as e:
            print(f"[Booking] Cold lead check error: {e}")
            traceback.print_exc()
            await asyncio.sleep(300)


# ── Telegram Command Handlers ────────────────────────────────────────────────

async def handle_confirm_command(text: str) -> str:
    """
    Handle /confirm command from Telegram.
    Usage: /confirm [lead_id] [date YYYY-MM-DD] [price]
    Example: /confirm 42 2026-04-15 1100
    """
    parts = text.strip().split()
    if len(parts) < 4:
        return (
            "Usage: /confirm [lead_id] [date] [price]\n"
            "Example: /confirm 42 2026-04-15 1100"
        )

    try:
        lead_id = int(parts[1])
    except ValueError:
        return "Invalid lead ID. Must be a number."

    date_str = parts[2]
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return "Invalid date format. Use YYYY-MM-DD."

    try:
        price = float(parts[3])
    except ValueError:
        return "Invalid price. Must be a number."

    lead = _get_lead(lead_id)
    if not lead:
        return f"Lead {lead_id} not found."

    name = f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip()
    location = lead.get("event_address", "") or lead.get("event_city", "") or "TBD"
    event_name = " ".join(parts[4:]) if len(parts) > 4 else None

    result = await initiate_booking(lead_id, date_str, location, price, event_name)
    if result.get("ok"):
        return (
            f"Booking created!\n\n"
            f"  ID: {result['booking_id']}\n"
            f"  Client: {name}\n"
            f"  Date: {_format_date(date_str)}\n"
            f"  Location: {location}\n"
            f"  Price: {_format_price(price)}\n\n"
            f"Confirmation message queued for your approval."
        )
    return f"Error: {result.get('error', 'Unknown error')}"


async def handle_deposit_command(text: str) -> str:
    """
    Handle /deposit command from Telegram.
    Usage: /deposit [booking_id] [amount]
    Example: /deposit ZBR-2026-A3F8 500
    """
    parts = text.strip().split()
    if len(parts) < 3:
        return (
            "Usage: /deposit [booking_id] [amount]\n"
            "Example: /deposit ZBR-2026-A3F8 500"
        )

    booking_id = parts[1]
    try:
        amount = float(parts[2])
    except ValueError:
        return "Invalid amount. Must be a number."

    result = mark_deposit_received(booking_id, amount)
    if result.get("ok"):
        return f"Deposit of {_format_price(amount)} recorded for {booking_id}."
    return f"Error: {result.get('error', 'Unknown error')}"


async def handle_pipeline_command() -> str:
    """Handle /pipeline command from Telegram — show booking pipeline status."""
    return format_pipeline_status()


async def handle_complete_command(text: str) -> str:
    """
    Handle /complete command from Telegram.
    Usage: /complete [booking_id]
    Example: /complete ZBR-2026-A3F8
    """
    parts = text.strip().split()
    if len(parts) < 2:
        return (
            "Usage: /complete [booking_id]\n"
            "Example: /complete ZBR-2026-A3F8"
        )

    booking_id = parts[1]
    result = complete_booking(booking_id)
    if result.get("ok"):
        return f"Booking {booking_id} marked as completed. Great event!"
    return f"Error: {result.get('error', 'Unknown error')}"


# ── Format Functions ──────────────────────────────────────────────────────────

def format_booking_confirmation_for_telegram(booking_info: dict) -> str:
    """
    Format a booking confirmation as a Telegram approval card for Kai.
    Shows the proposed confirmation message and booking details.
    """
    booking_id = booking_info.get("booking_id", "?")
    name = booking_info.get("lead_name", "")
    if not name:
        name = f"{booking_info.get('first_name', '')} {booking_info.get('last_name', '')}".strip()
    event_date = _format_date(booking_info.get("event_date", ""))
    location = booking_info.get("event_location", "TBD")
    price = _format_price(booking_info.get("total_price", 0))
    deposit = booking_info.get("deposit_status", "pending")

    return (
        f"NEW BOOKING CONFIRMATION\n"
        f"{'=' * 30}\n\n"
        f"  Booking: {booking_id}\n"
        f"  Client: {name}\n"
        f"  Date: {event_date}\n"
        f"  Location: {location}\n"
        f"  Total: {price}\n"
        f"  Deposit: {deposit}\n\n"
        f"Confirmation message is queued for your approval.\n"
        f"Check pending approvals to review and send."
    )


def format_pipeline_status() -> str:
    """
    Format a visual pipeline status for Telegram.
    Lead -> Quoted -> Confirmed -> Deposit -> Contract -> Event Day -> Complete
    """
    bookings = get_booking_pipeline()
    if not bookings:
        return "No active bookings in the pipeline."

    stage_icons = {
        "pending_confirmation": "\u23f3",
        "confirmed":           "\u2709\ufe0f",
        "deposit_received":    "\u2705",
        "contract_sent":       "\ud83d\udcdd",
        "contract_signed":     "\u2705",
        "complete":            "\ud83c\udf89",
    }

    # Count by stage
    stage_counts: dict[str, int] = {}
    for b in bookings:
        s = b.get("stage", "pending_confirmation")
        stage_counts[s] = stage_counts.get(s, 0) + 1

    pipeline_stages = [
        ("pending_confirmation", "Pending"),
        ("confirmed", "Confirmed"),
        ("deposit_received", "Deposit"),
        ("contract_sent", "Contract Sent"),
        ("contract_signed", "Contract Signed"),
        ("complete", "Complete"),
    ]

    lines = ["BOOKING PIPELINE", "=" * 30, ""]

    for key, label in pipeline_stages:
        count = stage_counts.get(key, 0)
        icon = stage_icons.get(key, "\u25cb")
        bar = "\u2588" * count if count > 0 else "\u2500"
        lines.append(f"  {icon} {label}: {count} {bar}")

    lines.append(f"\n  Total Active: {len([b for b in bookings if b['status'] == 'active'])}")
    lines.append(f"  Total Complete: {len([b for b in bookings if b['status'] == 'completed'])}")

    # List upcoming events
    active = [b for b in bookings if b["status"] == "active"]
    if active:
        lines.append("\nUPCOMING EVENTS:")
        lines.append("-" * 30)
        for b in active[:10]:
            name = b.get("lead_name", "?")
            date = _format_date(b.get("event_date", ""))
            bid = b.get("booking_id", "?")
            stage = b.get("stage", "?")
            icon = stage_icons.get(stage, "\u25cb")
            lines.append(f"  {icon} {bid} | {name} | {date}")

    return "\n".join(lines)


def format_booking_timeline(booking_id: str) -> str:
    """Format the chronological history of a booking for display."""
    entries = get_booking_timeline_entries(booking_id)
    booking = _get_booking(booking_id)

    if not booking:
        return f"Booking {booking_id} not found."

    if not entries:
        return f"No timeline entries for {booking_id}."

    action_icons = {
        "created":           "\ud83d\udfe2",
        "confirmed":         "\u2709\ufe0f",
        "deposit_received":  "\ud83d\udcb0",
        "contract_sent":     "\ud83d\udcdd",
        "contract_signed":   "\u2705",
        "reminder_sent":     "\ud83d\udd14",
        "completed":         "\ud83c\udf89",
        "cancelled":         "\u274c",
    }

    lines = [
        f"BOOKING TIMELINE: {booking_id}",
        "=" * 35,
        "",
    ]

    for entry in entries:
        ts = entry.get("timestamp", "?")
        action = entry.get("action", "?")
        details = entry.get("details", "")
        icon = action_icons.get(action, "\u25cf")
        try:
            dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            ts_fmt = dt.strftime("%b %-d, %Y %-I:%M %p")
        except (ValueError, TypeError):
            ts_fmt = ts
        lines.append(f"  {icon} {ts_fmt}")
        lines.append(f"     {action.replace('_', ' ').title()}: {details}")
        lines.append("")

    return "\n".join(lines)


# ── Reminder Loop ─────────────────────────────────────────────────────────────

async def run_reminder_loop(send_fn=None) -> None:
    """
    Async loop: check for booking reminders daily at 9 AM PT.
    All reminders go through approval — never auto-sent.
    """
    print("[Booking] Reminder loop started")
    while True:
        try:
            now = _now_pt()
            # Run daily at 9 AM PT
            if now.hour == 9 and now.minute < 5:
                queued = await check_and_queue_reminders()
                if queued > 0 and send_fn:
                    try:
                        await send_fn(f"[Booking] {queued} reminder(s) queued for your approval.")
                    except Exception:
                        pass
                # Wait until next hour
                await asyncio.sleep(3600)
            else:
                await asyncio.sleep(300)
        except Exception as e:
            print(f"[Booking] Reminder loop error: {e}")
            traceback.print_exc()
            await asyncio.sleep(300)


# ── Module-Level Init ─────────────────────────────────────────────────────────

def init_booking_system() -> None:
    """Initialize the booking system. Call on server startup."""
    global _booking_system
    init_booking_system_db()
    _booking_system = True
    print("[Booking] System initialized")


def get_booking_system_ready() -> bool:
    """Check if booking system is initialized."""
    return _booking_system is not None
