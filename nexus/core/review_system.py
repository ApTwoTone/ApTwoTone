"""
Review Generation & Management System — Zoar Bathroom Rentals

Automates the review request lifecycle:
1. Detects completed bookings eligible for review requests
2. Generates personalized review request messages from rotating templates
3. Queues ALL outbound messages for Kai's approval via Telegram
4. Tracks review requests, responses, and conversion rates
5. Supports manual /review, /reviewed, /reviewstats commands

NO auto-sending. Every review request goes through Kai's Telegram approval.
"""
from __future__ import annotations
import asyncio
import sqlite3
import json
import traceback
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DB_PATH = Path.home() / ".nexus" / "memory.db"
LA_TZ = ZoneInfo("America/Los_Angeles")

# ── Configuration ────────────────────────────────────────────────────────────
REVIEW_URL = "https://g.page/r/zoarbathroomrental/review"
BUSINESS_NAME = "Zoar Bathroom Rentals"
BUSINESS_PHONE = "(424) 235-8979"
BUSINESS_EMAIL = "zoarbathrooms@gmail.com"

# Timing
REVIEW_CHECK_HOUR = 10          # 10 AM LA time
FIRST_REQUEST_DELAY_DAYS = 1    # 1-2 days after event completion
SECOND_REQUEST_DELAY_DAYS = 7   # 7 days after first request
MAX_REQUESTS_PER_LEAD = 2       # Never ask more than twice

# Statuses for review requests
REQUEST_STATUSES = {
    "pending_approval": "Queued for Kai's approval",
    "approved":         "Approved, ready to send",
    "sent":             "Sent to customer",
    "completed":        "Review received",
    "skipped":          "Skipped by Kai",
}


def _now_la() -> datetime:
    """Current time in LA timezone."""
    return datetime.now(LA_TZ)


def _now_utc() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _is_review_check_time() -> bool:
    """Check if it's 10 AM (within the hour) in LA timezone."""
    now = _now_la()
    return now.hour == REVIEW_CHECK_HOUR


def _is_monday() -> bool:
    """Check if today is Monday (for weekly follow-up reminders)."""
    return _now_la().weekday() == 0


# ── Database Setup ───────────────────────────────────────────────────────────

def init_review_system_db() -> None:
    """Create review tables and seed default templates."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS review_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lead_id INTEGER NOT NULL,
        booking_id INTEGER DEFAULT NULL,
        request_type TEXT NOT NULL DEFAULT 'email',
        message_text TEXT DEFAULT '',
        subject_line TEXT DEFAULT '',
        status TEXT DEFAULT 'pending_approval',
        sent_at TEXT DEFAULT '',
        review_received INTEGER DEFAULT 0,
        review_rating INTEGER DEFAULT 0,
        review_platform TEXT DEFAULT '',
        review_text TEXT DEFAULT '',
        request_number INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (lead_id) REFERENCES leads(id)
    );

    CREATE TABLE IF NOT EXISTS review_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        template_text TEXT NOT NULL,
        subject_line TEXT DEFAULT '',
        channel TEXT NOT NULL DEFAULT 'email',
        use_count INTEGER DEFAULT 0,
        last_used TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_review_requests_lead ON review_requests(lead_id);
    CREATE INDEX IF NOT EXISTS idx_review_requests_status ON review_requests(status);
    CREATE INDEX IF NOT EXISTS idx_review_requests_sent ON review_requests(sent_at);
    CREATE INDEX IF NOT EXISTS idx_review_templates_channel ON review_templates(channel);
    """)
    conn.commit()

    # Seed templates if table is empty
    existing = conn.execute("SELECT COUNT(*) FROM review_templates").fetchone()[0]
    if existing == 0:
        _seed_templates(conn)

    conn.close()
    print("[Reviews] Database tables ready")


def _seed_templates(conn: sqlite3.Connection) -> None:
    """Seed default review request templates."""
    templates = [
        # ── Email templates ──
        {
            "name": "Warm & Personal",
            "channel": "email",
            "subject_line": "Thank you for choosing Zoar!",
            "template_text": (
                "Hi {first_name},\n\n"
                "Thank you so much for choosing Zoar Bathroom Rentals for your {event_type}! "
                "We hope your guests loved our luxury restroom trailer.\n\n"
                "If you have a moment, we'd really appreciate a Google review — "
                "it helps other event planners find us.\n\n"
                "{review_link}\n\n"
                "Thank you!\n"
                "— Kai, Zoar Bathroom Rentals\n"
                f"{BUSINESS_PHONE}"
            ),
        },
        {
            "name": "Quick & Direct",
            "channel": "email",
            "subject_line": "Quick favor from Zoar Bathroom Rentals",
            "template_text": (
                "Hi {first_name},\n\n"
                "Thanks for renting with Zoar! Quick favor — would you mind leaving us "
                "a Google review? It only takes 30 seconds.\n\n"
                "{review_link}\n\n"
                "Thanks so much!\n"
                "— Kai, Zoar Bathroom Rentals\n"
                f"{BUSINESS_PHONE}"
            ),
        },
        {
            "name": "Story-Driven",
            "channel": "email",
            "subject_line": "We loved being part of your {event_type}!",
            "template_text": (
                "Hi {first_name},\n\n"
                "We loved being part of your {event_type}! Your feedback means "
                "the world to us.\n\n"
                "If you'd share your experience on Google, we'd be incredibly grateful.\n\n"
                "{review_link}\n\n"
                "Warm regards,\n"
                "— Kai, Zoar Bathroom Rentals\n"
                f"{BUSINESS_PHONE}"
            ),
        },
        # ── SMS templates ──
        {
            "name": "SMS — Friendly",
            "channel": "sms",
            "subject_line": "",
            "template_text": (
                "Hi {first_name}! Thanks for choosing Zoar Bathroom Rentals. "
                "We'd love a Google review if you have a sec: {review_link} — Kai"
            ),
        },
        {
            "name": "SMS — Event Mention",
            "channel": "sms",
            "subject_line": "",
            "template_text": (
                "Hey {first_name}, hope your {event_type} was amazing! "
                "A quick Google review would mean a lot to us: {review_link}"
            ),
        },
    ]

    for t in templates:
        conn.execute(
            "INSERT INTO review_templates (name, template_text, subject_line, channel) "
            "VALUES (?, ?, ?, ?)",
            (t["name"], t["template_text"], t["subject_line"], t["channel"]),
        )
    conn.commit()
    print(f"[Reviews] Seeded {len(templates)} default templates")


# ── Template Selection ───────────────────────────────────────────────────────

def select_template(channel: str, lead_info: dict | None = None) -> dict | None:
    """Pick the best template: least recently used, matching channel."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    # Prefer the template with the lowest use_count, then oldest last_used
    row = conn.execute(
        "SELECT * FROM review_templates WHERE channel = ? "
        "ORDER BY use_count ASC, last_used ASC LIMIT 1",
        (channel,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def format_review_message(
    template: dict,
    lead_info: dict,
    booking_info: dict | None = None,
) -> tuple[str, str]:
    """Fill in template variables. Returns (message_text, subject_line)."""
    first_name = (lead_info.get("first_name") or "there").strip()
    event_type = (
        (booking_info or {}).get("event_type")
        or lead_info.get("event_type")
        or "event"
    )

    replacements = {
        "{first_name}": first_name,
        "{event_type}": event_type,
        "{review_link}": REVIEW_URL,
        "{business_name}": BUSINESS_NAME,
        "{business_phone}": BUSINESS_PHONE,
    }

    text = template["template_text"]
    subject = template.get("subject_line", "") or ""
    for key, val in replacements.items():
        text = text.replace(key, val)
        subject = subject.replace(key, val)

    return text, subject


def _increment_template_usage(template_id: int) -> None:
    """Track that a template was used."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "UPDATE review_templates SET use_count = use_count + 1, last_used = ? WHERE id = ?",
        (_now_utc(), template_id),
    )
    conn.commit()
    conn.close()


# ── Review Request Creation ──────────────────────────────────────────────────

def create_review_request(
    lead_id: int,
    booking_id: int | None = None,
    channel: str = "email",
) -> dict:
    """Generate a review request from template and queue for Kai's approval.

    Returns dict with request info or error.
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Get lead info
    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
        conn.close()
        return {"ok": False, "error": f"Lead #{lead_id} not found"}
    lead = dict(lead)

    # Check how many times we've already requested a review
    existing_count = conn.execute(
        "SELECT COUNT(*) FROM review_requests WHERE lead_id = ? AND status != 'skipped'",
        (lead_id,),
    ).fetchone()[0]

    if existing_count >= MAX_REQUESTS_PER_LEAD:
        conn.close()
        return {"ok": False, "error": f"Lead #{lead_id} already has {existing_count} review requests (max {MAX_REQUESTS_PER_LEAD})"}

    conn.close()

    # Select and format template
    template = select_template(channel, lead)
    if not template:
        return {"ok": False, "error": f"No {channel} templates available"}

    message_text, subject_line = format_review_message(template, lead)
    _increment_template_usage(template["id"])

    # Insert review request
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.execute(
        "INSERT INTO review_requests (lead_id, booking_id, request_type, message_text, "
        "subject_line, status, request_number) VALUES (?, ?, ?, ?, ?, 'pending_approval', ?)",
        (lead_id, booking_id, channel, message_text, subject_line, existing_count + 1),
    )
    request_id = cursor.lastrowid
    conn.commit()
    conn.close()

    print(f"[Reviews] Created review request #{request_id} for lead #{lead_id} via {channel}")

    return {
        "ok": True,
        "request_id": request_id,
        "lead_id": lead_id,
        "channel": channel,
        "message_text": message_text,
        "subject_line": subject_line,
        "request_number": existing_count + 1,
        "lead_name": f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip(),
        "lead_phone": lead.get("phone", ""),
        "lead_email": lead.get("email", ""),
    }


# ── Review Request Approval & Sending ────────────────────────────────────────

def approve_review_request(request_id: int, custom_text: str | None = None) -> dict | None:
    """Approve a review request. Optionally override the message text."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM review_requests WHERE id = ?", (request_id,)).fetchone()
    if not row:
        conn.close()
        return None

    actual_msg = custom_text if custom_text else row["message_text"]
    conn.execute(
        "UPDATE review_requests SET status = 'approved', message_text = ?, updated_at = ? WHERE id = ?",
        (actual_msg, _now_utc(), request_id),
    )
    conn.commit()
    result = dict(conn.execute("SELECT * FROM review_requests WHERE id = ?", (request_id,)).fetchone())
    conn.close()
    return result


def mark_review_request_sent(request_id: int) -> None:
    """Mark a review request as sent."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "UPDATE review_requests SET status = 'sent', sent_at = ?, updated_at = ? WHERE id = ?",
        (_now_utc(), _now_utc(), request_id),
    )
    conn.commit()
    conn.close()


def skip_review_request(request_id: int) -> None:
    """Skip a review request."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "UPDATE review_requests SET status = 'skipped', updated_at = ? WHERE id = ?",
        (_now_utc(), request_id),
    )
    conn.commit()
    conn.close()


# ── Review Tracking ──────────────────────────────────────────────────────────

def mark_review_received(
    request_id: int | None = None,
    lead_id: int | None = None,
    rating: int = 5,
    platform: str = "google",
    review_text: str = "",
) -> dict:
    """Mark that a review was received. Can look up by request_id or lead_id."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    if request_id:
        row = conn.execute("SELECT * FROM review_requests WHERE id = ?", (request_id,)).fetchone()
    elif lead_id:
        # Find the most recent sent request for this lead
        row = conn.execute(
            "SELECT * FROM review_requests WHERE lead_id = ? AND status = 'sent' "
            "ORDER BY sent_at DESC LIMIT 1",
            (lead_id,),
        ).fetchone()
        if not row:
            # Fallback: find any request for this lead
            row = conn.execute(
                "SELECT * FROM review_requests WHERE lead_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (lead_id,),
            ).fetchone()
    else:
        conn.close()
        return {"ok": False, "error": "Provide request_id or lead_id"}

    if not row:
        conn.close()
        return {"ok": False, "error": "No review request found"}

    rid = row["id"]
    conn.execute(
        "UPDATE review_requests SET review_received = 1, review_rating = ?, "
        "review_platform = ?, review_text = ?, status = 'completed', updated_at = ? WHERE id = ?",
        (rating, platform, review_text, _now_utc(), rid),
    )
    conn.commit()
    conn.close()

    print(f"[Reviews] Review received for request #{rid}: {rating}/5 on {platform}")
    return {"ok": True, "request_id": rid, "rating": rating, "platform": platform}


def get_review_stats() -> dict:
    """Calculate review request statistics."""
    conn = sqlite3.connect(str(DB_PATH))

    total_requests = conn.execute(
        "SELECT COUNT(*) FROM review_requests WHERE status IN ('sent', 'completed')"
    ).fetchone()[0]

    reviews_received = conn.execute(
        "SELECT COUNT(*) FROM review_requests WHERE review_received = 1"
    ).fetchone()[0]

    avg_rating_row = conn.execute(
        "SELECT AVG(review_rating) FROM review_requests WHERE review_received = 1 AND review_rating > 0"
    ).fetchone()
    avg_rating = round(avg_rating_row[0], 1) if avg_rating_row[0] else 0.0

    # By platform
    platform_rows = conn.execute(
        "SELECT review_platform, COUNT(*) FROM review_requests "
        "WHERE review_received = 1 GROUP BY review_platform"
    ).fetchall()
    by_platform = {row[0]: row[1] for row in platform_rows if row[0]}

    # Pending (sent but no review yet)
    pending = conn.execute(
        "SELECT COUNT(*) FROM review_requests WHERE status = 'sent' AND review_received = 0"
    ).fetchone()[0]

    # Pending approval
    pending_approval = conn.execute(
        "SELECT COUNT(*) FROM review_requests WHERE status = 'pending_approval'"
    ).fetchone()[0]

    # This month
    month_start = _now_la().replace(day=1, hour=0, minute=0, second=0).strftime("%Y-%m-%d")
    this_month_sent = conn.execute(
        "SELECT COUNT(*) FROM review_requests WHERE sent_at >= ? AND status IN ('sent', 'completed')",
        (month_start,),
    ).fetchone()[0]
    this_month_received = conn.execute(
        "SELECT COUNT(*) FROM review_requests WHERE review_received = 1 AND updated_at >= ?",
        (month_start,),
    ).fetchone()[0]

    conn.close()

    response_rate = round((reviews_received / total_requests * 100), 1) if total_requests > 0 else 0.0

    return {
        "total_requests_sent": total_requests,
        "reviews_received": reviews_received,
        "response_rate": response_rate,
        "average_rating": avg_rating,
        "by_platform": by_platform,
        "pending_review": pending,
        "pending_approval": pending_approval,
        "this_month_sent": this_month_sent,
        "this_month_received": this_month_received,
    }


def get_pending_reviews() -> list[dict]:
    """Get review requests that were sent but no review received yet."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT rr.*, l.first_name, l.last_name, l.phone, l.email, l.event_type "
        "FROM review_requests rr "
        "JOIN leads l ON rr.lead_id = l.id "
        "WHERE rr.status = 'sent' AND rr.review_received = 0 "
        "ORDER BY rr.sent_at ASC",
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_review_request(request_id: int) -> dict | None:
    """Get a single review request by ID."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM review_requests WHERE id = ?", (request_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ── Opportunity Detection ────────────────────────────────────────────────────

def _find_review_opportunities() -> list[dict]:
    """Find leads with completed bookings eligible for a first review request.

    Criteria:
    - booking_status = 'completed'
    - event_date was 1-2 days ago (or booking was marked completed 1-2 days ago)
    - No existing review request for this lead
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    cutoff_start = (_now_la() - timedelta(days=2)).strftime("%Y-%m-%d")
    cutoff_end = (_now_la() - timedelta(days=1)).strftime("%Y-%m-%d")

    # Find completed leads with event dates 1-2 days ago, no review request yet
    rows = conn.execute(
        """
        SELECT l.* FROM leads l
        WHERE l.booking_status = 'completed'
        AND (
            (l.event_date != '' AND l.event_date BETWEEN ? AND ?)
            OR (l.event_date = '' AND l.updated_at BETWEEN ? AND ?)
        )
        AND l.id NOT IN (
            SELECT lead_id FROM review_requests WHERE status != 'skipped'
        )
        AND l.status != 'opted_out'
        """,
        (cutoff_start, cutoff_end, cutoff_start, cutoff_end + " 23:59:59"),
    ).fetchall()
    conn.close()

    return [dict(r) for r in rows]


def _find_followup_opportunities() -> list[dict]:
    """Find leads who received a first review request 7+ days ago with no review.

    Criteria:
    - Has exactly 1 review request with status 'sent'
    - That request was sent 7+ days ago
    - No review received
    - Total requests < MAX_REQUESTS_PER_LEAD
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    cutoff = (_now_la() - timedelta(days=SECOND_REQUEST_DELAY_DAYS)).strftime("%Y-%m-%d %H:%M:%S")

    rows = conn.execute(
        """
        SELECT rr.lead_id, rr.id as first_request_id, rr.request_type,
               l.first_name, l.last_name, l.phone, l.email, l.event_type
        FROM review_requests rr
        JOIN leads l ON rr.lead_id = l.id
        WHERE rr.status = 'sent'
        AND rr.review_received = 0
        AND rr.sent_at <= ?
        AND rr.request_number = 1
        AND l.status != 'opted_out'
        AND rr.lead_id NOT IN (
            SELECT lead_id FROM review_requests
            WHERE request_number >= 2 AND status != 'skipped'
        )
        """,
        (cutoff,),
    ).fetchall()
    conn.close()

    return [dict(r) for r in rows]


# ── Review Request Scheduling (Approval Queue Integration) ───────────────────

async def _queue_review_for_approval(
    request_info: dict,
    send_fn,
) -> None:
    """Queue a review request for Kai's Telegram approval."""
    from telegram.bot import get_bot

    bot = get_bot()
    if not bot:
        print("[Reviews] No Telegram bot — cannot send approval notification")
        return

    text = format_review_request_for_telegram(request_info)
    request_id = request_info["request_id"]

    buttons = {
        "inline_keyboard": [
            [
                {"text": "Approve & Send", "callback_data": f"rev_approve:{request_id}"},
                {"text": "Skip", "callback_data": f"rev_skip:{request_id}"},
            ]
        ]
    }

    for cid in bot.allowed:
        await bot.send(cid, text, reply_markup=buttons)

    print(f"[Reviews] Review request #{request_id} queued for Kai's approval")


async def check_for_review_opportunities(send_fn) -> None:
    """Find completed bookings eligible for review requests and queue them.

    Called daily at 10 AM LA time.
    """
    # First review requests (1-2 days after event)
    opportunities = _find_review_opportunities()
    for lead in opportunities:
        lead_id = lead["id"]
        # Prefer email, fall back to SMS
        channel = "email" if lead.get("email") else "sms"
        if not lead.get("email") and not lead.get("phone"):
            print(f"[Reviews] Lead #{lead_id} has no email or phone — skipping")
            continue

        result = create_review_request(lead_id, channel=channel)
        if result.get("ok"):
            await _queue_review_for_approval(result, send_fn)
        else:
            print(f"[Reviews] Could not create review request for lead #{lead_id}: {result.get('error')}")

    # Follow-up review requests (7 days after first)
    followups = _find_followup_opportunities()
    for f in followups:
        lead_id = f["lead_id"]
        # Use same channel as first request, or email
        channel = f.get("request_type", "email")
        if channel == "sms" and not f.get("phone"):
            channel = "email"
        if channel == "email" and not f.get("email"):
            channel = "sms"
        if not f.get("email") and not f.get("phone"):
            continue

        result = create_review_request(lead_id, channel=channel)
        if result.get("ok"):
            await _queue_review_for_approval(result, send_fn)
        else:
            print(f"[Reviews] Follow-up request failed for lead #{lead_id}: {result.get('error')}")

    total = len(opportunities) + len(followups)
    if total > 0:
        print(f"[Reviews] Queued {len(opportunities)} new + {len(followups)} follow-up review requests")
    else:
        print("[Reviews] No review opportunities found today")


# ── Telegram Command Handlers ────────────────────────────────────────────────

def handle_review_command(text: str) -> str:
    """/review [lead_id] — Manually trigger a review request for a lead."""
    parts = text.strip().split()
    if len(parts) < 2:
        return (
            "Usage: /review [lead_id] [email|sms]\n\n"
            "Example:\n"
            "  /review 42\n"
            "  /review 42 sms"
        )

    try:
        lead_id = int(parts[1])
    except ValueError:
        return "Invalid lead ID. Usage: /review [lead_id]"

    channel = parts[2].lower() if len(parts) > 2 and parts[2].lower() in ("email", "sms") else "email"

    result = create_review_request(lead_id, channel=channel)
    if not result.get("ok"):
        return f"Could not create review request: {result.get('error', 'Unknown error')}"

    # Return the approval card — caller will handle queueing
    return (
        f"Review request created for {result['lead_name']} (#{lead_id})\n"
        f"Channel: {channel.upper()}\n"
        f"Request #{result['request_number']}\n\n"
        f"Queued for your approval."
    )


async def handle_review_command_async(text: str, send_fn) -> str:
    """/review [lead_id] — create and queue for approval."""
    parts = text.strip().split()
    if len(parts) < 2:
        return (
            "Usage: /review [lead_id] [email|sms]\n\n"
            "Example:\n"
            "  /review 42\n"
            "  /review 42 sms"
        )

    try:
        lead_id = int(parts[1])
    except ValueError:
        return "Invalid lead ID. Usage: /review [lead_id]"

    channel = parts[2].lower() if len(parts) > 2 and parts[2].lower() in ("email", "sms") else "email"

    result = create_review_request(lead_id, channel=channel)
    if not result.get("ok"):
        return f"Could not create review request: {result.get('error', 'Unknown error')}"

    await _queue_review_for_approval(result, send_fn)

    return (
        f"Review request #{result['request_id']} created for "
        f"{result['lead_name']} (#{lead_id}) via {channel.upper()}\n"
        f"Request #{result['request_number']} of {MAX_REQUESTS_PER_LEAD} max\n\n"
        f"Sent to your approval queue."
    )


def handle_reviewed_command(text: str) -> str:
    """/reviewed [lead_id] [rating] [platform] — Mark that a review was received.

    Examples:
        /reviewed 42 5 google
        /reviewed 42 4 yelp
        /reviewed 42 5
    """
    parts = text.strip().split()
    if len(parts) < 3:
        return (
            "Usage: /reviewed [lead_id] [rating] [platform]\n\n"
            "Examples:\n"
            "  /reviewed 42 5 google\n"
            "  /reviewed 42 4 yelp\n"
            "  /reviewed 42 5        (defaults to google)"
        )

    try:
        lead_id = int(parts[1])
    except ValueError:
        return "Invalid lead ID."

    try:
        rating = int(parts[2])
        if rating < 1 or rating > 5:
            return "Rating must be 1-5."
    except ValueError:
        return "Invalid rating. Must be 1-5."

    platform = parts[3].lower() if len(parts) > 3 else "google"
    if platform not in ("google", "yelp", "facebook", "thumbtack", "other"):
        platform = "other"

    result = mark_review_received(lead_id=lead_id, rating=rating, platform=platform)
    if not result.get("ok"):
        return f"Error: {result.get('error', 'Unknown')}"

    stars = "★" * rating + "☆" * (5 - rating)
    return (
        f"Review recorded!\n\n"
        f"Lead #{lead_id}\n"
        f"Rating: {stars} ({rating}/5)\n"
        f"Platform: {platform.title()}\n"
        f"Request #{result['request_id']}"
    )


def handle_review_stats_command() -> str:
    """/reviewstats — Show review request statistics."""
    return format_review_stats()


def handle_review_templates_command() -> str:
    """/reviewtemplates — List available review templates."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM review_templates ORDER BY channel, use_count ASC"
    ).fetchall()
    conn.close()

    if not rows:
        return "No review templates found. Run init to seed defaults."

    lines = ["REVIEW TEMPLATES\n"]
    for r in rows:
        channel_tag = "EMAIL" if r["channel"] == "email" else "SMS"
        lines.append(
            f"#{r['id']} [{channel_tag}] {r['name']}\n"
            f"   Used: {r['use_count']} times\n"
            f"   Preview: {r['template_text'][:80]}...\n"
        )

    return "\n".join(lines)


# ── Callback Handler for Review Approvals ────────────────────────────────────

async def handle_review_callback(action: str, request_id: int, send_fn) -> str:
    """Handle Telegram callback for review request approval.

    Returns a status message to send back.
    """
    if action == "rev_approve":
        req = approve_review_request(request_id)
        if not req:
            return f"Review request #{request_id} not found."

        # Send the review request via the pipeline
        lead_id = req["lead_id"]
        channel = req["request_type"]
        message = req["message_text"]
        subject = req.get("subject_line", "")

        try:
            from core.lead_pipeline import get_pipeline
            pipeline = get_pipeline()
            if not pipeline:
                return "Pipeline not initialized — cannot send."

            if channel == "email":
                conn = sqlite3.connect(str(DB_PATH))
                conn.row_factory = sqlite3.Row
                lead = conn.execute("SELECT email FROM leads WHERE id = ?", (lead_id,)).fetchone()
                conn.close()
                if lead and lead["email"]:
                    result = await pipeline.send_email(lead["email"], subject, message, lead_id)
                else:
                    return f"Lead #{lead_id} has no email address."
            elif channel == "sms":
                conn = sqlite3.connect(str(DB_PATH))
                conn.row_factory = sqlite3.Row
                lead = conn.execute("SELECT phone FROM leads WHERE id = ?", (lead_id,)).fetchone()
                conn.close()
                if lead and lead["phone"]:
                    result = await pipeline.send_sms(lead["phone"], message, lead_id)
                else:
                    return f"Lead #{lead_id} has no phone number."
            else:
                return f"Unknown channel: {channel}"

            if result.get("ok"):
                mark_review_request_sent(request_id)
                return f"Review request #{request_id} sent via {channel.upper()}!"
            else:
                return f"Failed to send: {result.get('error', 'Unknown error')}"

        except Exception as e:
            return f"Send error: {e}"

    elif action == "rev_skip":
        skip_review_request(request_id)
        return f"Review request #{request_id} skipped."

    return f"Unknown action: {action}"


# ── Telegram Formatting ──────────────────────────────────────────────────────

def format_review_request_for_telegram(request_info: dict) -> str:
    """Format a review request approval card for Telegram."""
    lead_name = request_info.get("lead_name", "Unknown")
    lead_phone = request_info.get("lead_phone", "N/A")
    lead_email = request_info.get("lead_email", "N/A")
    channel = request_info.get("channel", "email").upper()
    request_num = request_info.get("request_number", 1)
    message = request_info.get("message_text", "")
    request_id = request_info.get("request_id", 0)

    suffix = "st" if request_num == 1 else "nd"

    return (
        f"REVIEW REQUEST\n"
        f"{'=' * 20}\n"
        f"Lead: {lead_name}\n"
        f"Phone: {lead_phone or 'N/A'}\n"
        f"Email: {lead_email or 'N/A'}\n"
        f"Channel: {channel}\n"
        f"Request: {request_num}{suffix} of {MAX_REQUESTS_PER_LEAD}\n\n"
        f"PROPOSED MESSAGE:\n"
        f"{'=' * 20}\n"
        f"\"{message}\"\n\n"
        f"{'=' * 20}\n"
        f"Tap 'Approve & Send' to send or 'Skip' to cancel.\n"
        f"Review Request #{request_id}"
    )


def format_review_stats() -> str:
    """Format review stats for Telegram display."""
    stats = get_review_stats()

    stars_avg = ""
    if stats["average_rating"] > 0:
        full = int(stats["average_rating"])
        stars_avg = "★" * full + ("½" if stats["average_rating"] - full >= 0.5 else "") + f" ({stats['average_rating']})"

    platform_lines = []
    for platform, count in stats.get("by_platform", {}).items():
        platform_lines.append(f"  {platform.title()}: {count}")

    lines = [
        "REVIEW STATS",
        "=" * 20,
        f"Requests Sent: {stats['total_requests_sent']}",
        f"Reviews Received: {stats['reviews_received']}",
        f"Response Rate: {stats['response_rate']}%",
        f"Average Rating: {stars_avg or 'N/A'}",
        "",
        "By Platform:",
    ]
    if platform_lines:
        lines.extend(platform_lines)
    else:
        lines.append("  No reviews yet")

    lines.extend([
        "",
        f"Awaiting Review: {stats['pending_review']}",
        f"Pending Approval: {stats['pending_approval']}",
        "",
        "This Month:",
        f"  Sent: {stats['this_month_sent']}",
        f"  Received: {stats['this_month_received']}",
    ])

    return "\n".join(lines)


def format_review_pipeline() -> str:
    """Show the review funnel: Events Completed -> Requests Sent -> Reviews Received."""
    conn = sqlite3.connect(str(DB_PATH))

    completed_events = conn.execute(
        "SELECT COUNT(*) FROM leads WHERE booking_status = 'completed'"
    ).fetchone()[0]

    requests_sent = conn.execute(
        "SELECT COUNT(DISTINCT lead_id) FROM review_requests WHERE status IN ('sent', 'completed')"
    ).fetchone()[0]

    reviews_received = conn.execute(
        "SELECT COUNT(*) FROM review_requests WHERE review_received = 1"
    ).fetchone()[0]

    conn.close()

    # Calculate conversion rates
    request_rate = round(requests_sent / completed_events * 100, 1) if completed_events > 0 else 0
    review_rate = round(reviews_received / requests_sent * 100, 1) if requests_sent > 0 else 0
    overall_rate = round(reviews_received / completed_events * 100, 1) if completed_events > 0 else 0

    # Build funnel visualization
    lines = [
        "REVIEW FUNNEL",
        "=" * 24,
        "",
        f"  Events Completed: {completed_events}",
        f"         |",
        f"         v  ({request_rate}%)",
        f"  Requests Sent:    {requests_sent}",
        f"         |",
        f"         v  ({review_rate}%)",
        f"  Reviews Received: {reviews_received}",
        "",
        f"  Overall Conversion: {overall_rate}%",
    ]

    return "\n".join(lines)


# ── Main Scheduler Loop ─────────────────────────────────────────────────────

async def run_review_system(send_fn) -> None:
    """Main async loop for the review system.

    - Daily check at 10 AM LA time for new review opportunities
    - Weekly check on Mondays for follow-up reminders
    - All outbound messages go through Kai's Telegram approval
    """
    print("[Reviews] Review system scheduler started")
    last_daily_check: str = ""
    last_weekly_check: str = ""

    while True:
        try:
            now = _now_la()
            today = now.strftime("%Y-%m-%d")

            # Daily check at 10 AM
            if _is_review_check_time() and last_daily_check != today:
                print(f"[Reviews] Running daily review opportunity check ({today})")
                await check_for_review_opportunities(send_fn)
                last_daily_check = today

            # Weekly Monday check for follow-up reminders
            if _is_monday() and _is_review_check_time() and last_weekly_check != today:
                print(f"[Reviews] Running weekly follow-up check ({today})")
                # Follow-ups are already included in check_for_review_opportunities
                last_weekly_check = today

        except Exception as e:
            print(f"[Reviews] Scheduler error: {e}")
            traceback.print_exc()

        # Check every 5 minutes
        await asyncio.sleep(300)


# ── Module-level singleton ───────────────────────────────────────────────────
_review_system_initialized = False


def init_review_system() -> None:
    """Initialize the review system. Safe to call on every startup."""
    global _review_system_initialized
    if not _review_system_initialized:
        init_review_system_db()
        _review_system_initialized = True
