"""
Re-engagement System -- Zoar Bathroom Rentals

Multi-channel re-engagement for reviving cold/stale leads:
  - Segments leads by inactivity window (30d, 60d, 90d, quoted-no-book, past customers)
  - 3-touch escalating sequences per segment with template rotation
  - ALL outbound messages routed through approval queue (Kai approves via Telegram)
  - Campaign tracking: targeted vs responded, per-segment conversion
  - Telegram commands: /reengage, /reengage start, /reengage queue
  - Daily 10 AM PT scheduler: identify targets, queue touches, notify Kai

Config: ~/.nexus/config.json
Database: ~/.nexus/memory.db
"""
from __future__ import annotations
import asyncio
import json
import random
import sqlite3
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# -- Constants ----------------------------------------------------------------

DB_PATH = Path.home() / ".nexus" / "memory.db"
PT = ZoneInfo("America/Los_Angeles")

SCHEDULER_HOUR = 10          # 10 AM PT daily
SCHEDULER_INTERVAL = 300     # check every 5 minutes

ZOAR_PHONE = "(424) 235-8979"
ZOAR_EMAIL = "zoarbathrooms@gmail.com"
ZOAR_WEBSITE = "zoarbathroomrental.com"

# -- Helpers ------------------------------------------------------------------

def _now_pt() -> datetime:
    return datetime.now(PT)

def _now_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def _today_str() -> str:
    return _now_pt().strftime("%Y-%m-%d")

def _log(msg: str):
    print(f"[ReEngage] {msg}")

def _load_config() -> dict:
    p = Path.home() / ".nexus" / "config.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn

def _season_label() -> str:
    """Return current season label for templates."""
    month = _now_pt().month
    if month in (3, 4, 5):
        return "spring"
    elif month in (6, 7, 8):
        return "summer"
    elif month in (9, 10, 11):
        return "fall"
    return "winter"

def _season_hook() -> str:
    """Return a seasonal marketing hook."""
    season = _season_label()
    hooks = {
        "spring": "Spring wedding season is filling up fast",
        "summer": "Summer event season is in full swing",
        "fall":   "Fall booking season is here",
        "winter": "The new year is a great time to lock in your event date",
    }
    return hooks.get(season, "Event season is approaching")


# =============================================================================
# TEMPLATE SEQUENCES
# =============================================================================

# Each segment has 3 touches. Each touch has 3 template variants (randomly
# selected for variety). Placeholders: {name}, {event_type}, {season_hook}

SEGMENTS = {
    "cold_30d": {
        "label": "Cold 30-60 days",
        "description": "No activity for 30-60 days -- warm check-in",
        "min_inactive_days": 30,
        "max_inactive_days": 60,
    },
    "cold_60d": {
        "label": "Cold 60-90 days",
        "description": "No activity for 60-90 days -- value reminder + offer",
        "min_inactive_days": 60,
        "max_inactive_days": 90,
    },
    "cold_90d": {
        "label": "Cold 90+ days",
        "description": "No activity for 90+ days -- last-chance urgency",
        "min_inactive_days": 90,
        "max_inactive_days": 9999,
    },
    "quoted_no_book": {
        "label": "Quoted but never booked",
        "description": "Received a quote but never completed booking",
    },
    "past_customers": {
        "label": "Past customers",
        "description": "Completed bookings -- referrals + repeat business",
    },
}

TOUCH_DELAYS = {
    1: 0,      # Touch 1: immediate once campaign starts
    2: 6,      # Touch 2: 5-7 days later (use 6 as midpoint)
    3: 14,     # Touch 3: 7-10 days after touch 2 (cumulative 14)
}

TEMPLATE_SEQUENCES: dict[str, dict[int, list[str]]] = {
    # ── cold_30d ─────────────────────────────────────────────────────────
    "cold_30d": {
        1: [
            "Hi {name}, it's Kai from Zoar Bathroom Rentals! I wanted to check in -- are you still planning your {event_type}? We'd love to help make it perfect with our luxury 4-stall restroom trailer. Let me know!",
            "Hey {name}! Just circling back about your {event_type}. {season_hook}, and we'd love to be part of your plans. Our luxury trailer with AC, running water, and Bluetooth speaker is a guest favorite. Still interested?",
            "Hi {name}, hope you're doing well! We chatted about your {event_type} a while back. Still thinking it over? Happy to answer any questions about our luxury restroom trailer. -- Kai, Zoar",
        ],
        2: [
            "Hi {name}, quick follow-up from Zoar! {season_hook} and dates are filling up. Our 4-stall luxury trailer (AC, running water, LED lighting) is the #1 choice for {event_type}s in SoCal. Want me to hold a date for you?",
            "{name}, just a friendly nudge! {season_hook}. Our luxury restroom trailer includes delivery, setup, and pickup -- no hidden fees. Perfect for your {event_type}. Can I send you updated availability?",
            "Hi {name}! Wanted to share that {season_hook}. We're booking {event_type}s and would love to save a spot for you. Our guests rave about the mirrors, AC, and flushable toilets. Shall I check dates?",
        ],
        3: [
            "Hi {name}, last check-in from Zoar! I don't want you to miss out on having luxury restrooms for your {event_type}. {season_hook} and our calendar is almost full. Let me know if I can help!",
            "{name}, final follow-up from Kai at Zoar. If your {event_type} plans have changed, no worries at all! But if you're still interested, I'd love to lock in a date before we're fully booked. {season_hook}.",
            "Hi {name}, I'll keep this short -- just wanted to make sure luxury restrooms are checked off your {event_type} to-do list. {season_hook} and spots are limited. Happy to chat anytime! -- Kai",
        ],
    },
    # ── cold_60d ─────────────────────────────────────────────────────────
    "cold_60d": {
        1: [
            "Hi {name}! It's been a little while since we connected about your {event_type}. {season_hook}, and I wanted to remind you why Zoar is SoCal's top choice: luxury 4-stall trailer, AC, running water, and full setup included. Still planning?",
            "Hey {name}, Kai from Zoar here! I know it's been a bit -- just wanted to share that {season_hook}. Our luxury restroom trailer is still available for {event_type}s in your area. Would love to help!",
            "{name}, it's Kai at Zoar Bathroom Rentals. {season_hook} and I wanted to reach out one more time. Our luxury trailer with flushable toilets, mirrors, and AC could be the perfect addition to your {event_type}. Interested?",
        ],
        2: [
            "Hi {name}! Since we last spoke, we've added even more features to our luxury trailer experience. {season_hook} and we're offering priority booking for returning inquiries. Your {event_type} deserves the best!",
            "{name}, {season_hook} and we'd love to make your {event_type} stress-free! Our 4-stall luxury trailer is all-inclusive: delivery, setup, pickup, AC, Bluetooth speaker, and LED lighting. Can I get you a fresh quote?",
            "Hi {name}! Just a reminder that Zoar is here when you're ready. {season_hook} and our calendar for {event_type}s is filling up. I can send you updated pricing -- just say the word!",
        ],
        3: [
            "{name}, this is my final reach-out about your {event_type}. No pressure at all -- but {season_hook} and I'd hate for you to miss out on luxury restrooms. We're nearly booked. Let me know! -- Kai, Zoar",
            "Hi {name}, last note from Zoar! If the timing wasn't right before, I totally understand. But {season_hook} and dates are going fast. Our luxury 4-stall trailer would be perfect for your {event_type}. Here whenever you're ready!",
            "{name}, closing the loop on your {event_type} inquiry. {season_hook} and our availability is limited. If you'd like to reserve a date, I'm happy to help. Otherwise, wishing you a wonderful event! -- Kai",
        ],
    },
    # ── cold_90d ─────────────────────────────────────────────────────────
    "cold_90d": {
        1: [
            "Hi {name}! It's Kai from Zoar Bathroom Rentals. It's been a while since your {event_type} inquiry. Are you still looking for luxury restroom rentals? {season_hook} and we'd love to reconnect.",
            "{name}, long time! Kai here from Zoar. Just wanted to see if luxury restrooms are still on your radar for your {event_type}. {season_hook} -- happy to chat if you're still planning!",
            "Hi {name}, hope all is well! Reaching out from Zoar one more time. If your {event_type} is still in the works, {season_hook} and our luxury trailer (4-stall, AC, running water) might be just what you need.",
        ],
        2: [
            "Hi {name}, Kai from Zoar again. I know it's been a while! {season_hook} and our luxury restroom trailer is still SoCal's best option for {event_type}s. Full setup included. Want me to check availability for you?",
            "{name}, {season_hook} and I'm reaching out one last time about your {event_type}. Our 4-stall luxury trailer is booking up. Would love to help if you're still interested. No obligation -- just checking in!",
            "Hi {name}! {season_hook} and Zoar has limited availability left. If your {event_type} still needs luxury restrooms, now's the time to book. Our trailer features AC, mirrors, LED lighting, and flushable toilets. -- Kai",
        ],
        3: [
            "{name}, this is my last message about your {event_type} inquiry from a while back. Totally understand if plans changed! But if you're still looking, {season_hook} and we have very few dates left. Wishing you all the best either way! -- Kai, Zoar",
            "Hi {name}, final note from Zoar Bathroom Rentals. If your {event_type} is still happening, I'd love to help -- {season_hook} and availability is extremely limited. If not, no worries and best of luck with everything!",
            "{name}, wrapping up your inquiry from Zoar. {season_hook} and our luxury 4-stall trailer is nearly fully booked. This is my last reach-out, but I'm always here if you need us. Take care! -- Kai",
        ],
    },
    # ── quoted_no_book ───────────────────────────────────────────────────
    "quoted_no_book": {
        1: [
            "Hi {name}! I noticed you received a quote from Zoar for your {event_type} but haven't booked yet. Any questions I can answer? {season_hook} and I'd love to help finalize your plans!",
            "{name}, just following up on the quote we sent for your {event_type}. Everything still look good? {season_hook} and I want to make sure we can hold your preferred date. -- Kai, Zoar",
            "Hi {name}! Wanted to check in about the quote for your {event_type}. Our luxury 4-stall trailer with AC, running water, and full setup is all-inclusive. {season_hook} -- shall I update the quote?",
        ],
        2: [
            "Hi {name}, {season_hook} and I wanted to make sure your {event_type} quote is still valid. Pricing may adjust as the season fills up. Want me to confirm your rate and hold a date?",
            "{name}, quick update from Zoar! {season_hook} and we're filling up fast. Your {event_type} quote is still available but dates are going. Should I reserve your spot?",
            "Hi {name}! Just a reminder that your Zoar quote for your {event_type} includes everything: luxury 4-stall trailer, delivery, setup, AC, Bluetooth speaker, and pickup. {season_hook} -- ready to lock it in?",
        ],
        3: [
            "{name}, last heads up -- your {event_type} quote from Zoar is about to expire. {season_hook} and we can't guarantee pricing much longer. Let me know if you'd like to proceed! -- Kai",
            "Hi {name}, final follow-up on your Zoar quote. I'd hate for you to miss out on luxury restrooms for your {event_type}. {season_hook} and availability is very limited. Ready when you are!",
            "{name}, closing the loop on your {event_type} quote. {season_hook} and this is my last reminder before the quote expires. Totally understand if plans changed -- wishing you a great event either way! -- Kai, Zoar",
        ],
    },
    # ── past_customers ───────────────────────────────────────────────────
    "past_customers": {
        1: [
            "Hi {name}! It's Kai from Zoar. Thank you again for choosing us for your {event_type} -- we loved being part of it! Do you have any upcoming events or know someone who does? We'd love to help again!",
            "{name}, hope you're doing great! Kai from Zoar here. We had a blast at your {event_type}. If you or anyone you know has an upcoming event, we'd love the referral. {season_hook}!",
            "Hi {name}! Just wanted to say thanks again for renting with Zoar for your {event_type}. If you know anyone planning a wedding, party, or event, we'd really appreciate the referral. {season_hook}!",
        ],
        2: [
            "Hi {name}! {season_hook} and we're getting lots of bookings. If you have another event coming up, we'd love to offer you our returning-customer priority. Our luxury 4-stall trailer is better than ever!",
            "{name}, {season_hook} and it's a great time for events! As a past Zoar customer, you get priority scheduling. Planning another {event_type} or any event? Let me know! -- Kai",
            "Hi {name}! Friendly reminder that {season_hook}. If you're planning anything, Zoar's luxury restroom trailer is ready to roll. As a returning customer, you're at the top of our list!",
        ],
        3: [
            "{name}, {season_hook} and Zoar is nearly fully booked! If there's any chance you have an event this season, please let me know soon so I can hold a date. Or if you know someone who needs luxury restrooms, referrals mean the world to us! -- Kai",
            "Hi {name}, last note of the season from Zoar! {season_hook} and we have just a few dates left. Would love to work with you again or anyone you might refer. Thank you for being a great customer!",
            "{name}, wrapping up our seasonal outreach. As a valued past customer, I wanted to give you first chance at our remaining dates. {season_hook}! Let me know if I can help with anything. -- Kai, Zoar",
        ],
    },
}


# =============================================================================
# DATABASE INIT
# =============================================================================

def init_reengagement_db() -> None:
    """Create reengagement tables if they don't exist."""
    conn = _get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS reengagement_campaigns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        target_segment TEXT NOT NULL,
        channel TEXT DEFAULT 'email',
        template_sequence TEXT DEFAULT '{}',
        status TEXT DEFAULT 'active',
        leads_targeted INTEGER DEFAULT 0,
        leads_responded INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS reengagement_touches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        campaign_id INTEGER NOT NULL,
        lead_id INTEGER NOT NULL,
        touch_number INTEGER DEFAULT 1,
        channel TEXT DEFAULT 'email',
        template_used TEXT DEFAULT '',
        message_text TEXT DEFAULT '',
        status TEXT DEFAULT 'queued',
        scheduled_at TEXT DEFAULT '',
        sent_at TEXT DEFAULT '',
        response_at TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (campaign_id) REFERENCES reengagement_campaigns(id),
        FOREIGN KEY (lead_id) REFERENCES leads(id)
    );

    CREATE INDEX IF NOT EXISTS idx_rc_segment ON reengagement_campaigns(target_segment);
    CREATE INDEX IF NOT EXISTS idx_rc_status ON reengagement_campaigns(status);
    CREATE INDEX IF NOT EXISTS idx_rt_campaign ON reengagement_touches(campaign_id);
    CREATE INDEX IF NOT EXISTS idx_rt_lead ON reengagement_touches(lead_id);
    CREATE INDEX IF NOT EXISTS idx_rt_status ON reengagement_touches(status);
    CREATE INDEX IF NOT EXISTS idx_rt_scheduled ON reengagement_touches(scheduled_at);
    """)
    conn.commit()
    conn.close()
    _log("Database tables ready")


# =============================================================================
# SEGMENT IDENTIFICATION
# =============================================================================

def identify_reengagement_targets() -> dict:
    """Scan the leads table and count how many leads fall into each segment.

    Returns dict like:
        {"cold_30d": {"count": 12, "lead_ids": [1,2,3,...]}, ...}
    """
    conn = _get_conn()
    now_utc = datetime.utcnow()
    results: dict[str, dict] = {}

    # ── Cold leads by inactivity window ──────────────────────────────────
    for seg_key in ("cold_30d", "cold_60d", "cold_90d"):
        seg = SEGMENTS[seg_key]
        min_days = seg["min_inactive_days"]
        max_days = seg["max_inactive_days"]
        cutoff_start = (now_utc - timedelta(days=max_days)).strftime("%Y-%m-%d %H:%M:%S")
        cutoff_end = (now_utc - timedelta(days=min_days)).strftime("%Y-%m-%d %H:%M:%S")

        rows = conn.execute("""
            SELECT id FROM leads
            WHERE status NOT IN ('opted_out', 'closed')
            AND booking_status IN ('new_lead', '')
            AND last_reply_at = ''
            AND last_contacted_at != ''
            AND last_contacted_at BETWEEN ? AND ?
            AND id NOT IN (
                SELECT DISTINCT lead_id FROM reengagement_touches
                WHERE status NOT IN ('failed')
            )
        """, (cutoff_start, cutoff_end)).fetchall()

        results[seg_key] = {
            "count": len(rows),
            "lead_ids": [r["id"] for r in rows],
        }

    # ── Quoted but never booked ──────────────────────────────────────────
    rows = conn.execute("""
        SELECT id FROM leads
        WHERE status NOT IN ('opted_out', 'closed')
        AND booking_status IN ('quoted', 'new_lead', '')
        AND total_quote_amount > 0
        AND booking_status NOT IN ('deposit_pending', 'deposit_received',
            'confirmed', 'booked', 'completed')
        AND id NOT IN (
            SELECT DISTINCT lead_id FROM reengagement_touches
            WHERE status NOT IN ('failed')
        )
    """).fetchall()
    results["quoted_no_book"] = {
        "count": len(rows),
        "lead_ids": [r["id"] for r in rows],
    }

    # ── Past customers (completed bookings) ──────────────────────────────
    rows = conn.execute("""
        SELECT id FROM leads
        WHERE booking_status = 'completed'
        AND status NOT IN ('opted_out')
        AND id NOT IN (
            SELECT DISTINCT lead_id FROM reengagement_touches
            WHERE status NOT IN ('failed')
        )
    """).fetchall()
    results["past_customers"] = {
        "count": len(rows),
        "lead_ids": [r["id"] for r in rows],
    }

    conn.close()
    return results


# =============================================================================
# CAMPAIGN MANAGEMENT
# =============================================================================

def create_campaign(segment: str, channel: str = "email") -> dict:
    """Create a re-engagement campaign for a given segment.

    Identifies matching leads, creates campaign row, and queues touch-1
    for each lead (status='queued').

    Returns campaign info dict or error.
    """
    if segment not in SEGMENTS:
        return {"ok": False, "error": f"Unknown segment: {segment}. Valid: {', '.join(SEGMENTS.keys())}"}

    if segment not in TEMPLATE_SEQUENCES:
        return {"ok": False, "error": f"No templates defined for segment: {segment}"}

    targets = identify_reengagement_targets()
    seg_data = targets.get(segment, {})
    lead_ids = seg_data.get("lead_ids", [])

    if not lead_ids:
        return {"ok": False, "error": f"No eligible leads found for segment '{segment}'"}

    seg_info = SEGMENTS[segment]
    campaign_name = f"{seg_info['label']} -- {_today_str()}"
    templates_json = json.dumps(TEMPLATE_SEQUENCES[segment], default=str)

    conn = _get_conn()

    # Check for duplicate active campaign on same segment
    existing = conn.execute(
        "SELECT id FROM reengagement_campaigns WHERE target_segment = ? AND status = 'active'",
        (segment,),
    ).fetchone()
    if existing:
        conn.close()
        return {
            "ok": False,
            "error": f"Active campaign already exists for '{segment}' (ID #{existing['id']}). Pause it first.",
        }

    conn.execute(
        "INSERT INTO reengagement_campaigns "
        "(name, target_segment, channel, template_sequence, status, leads_targeted) "
        "VALUES (?, ?, ?, ?, 'active', ?)",
        (campaign_name, segment, channel, templates_json, len(lead_ids)),
    )
    campaign_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Queue touch 1 for each lead
    now = datetime.utcnow()
    scheduled = now.strftime("%Y-%m-%d %H:%M:%S")

    for lead_id in lead_ids:
        # Pick a random template variant for touch 1
        variants = TEMPLATE_SEQUENCES[segment][1]
        template_text = random.choice(variants)

        conn.execute(
            "INSERT INTO reengagement_touches "
            "(campaign_id, lead_id, touch_number, channel, template_used, status, scheduled_at) "
            "VALUES (?, ?, 1, ?, ?, 'queued', ?)",
            (campaign_id, lead_id, channel, template_text, scheduled),
        )

    conn.commit()
    conn.close()

    _log(f"Campaign #{campaign_id} created: '{campaign_name}' targeting {len(lead_ids)} leads")
    return {
        "ok": True,
        "campaign_id": campaign_id,
        "name": campaign_name,
        "segment": segment,
        "channel": channel,
        "leads_targeted": len(lead_ids),
    }


def pause_campaign(campaign_id: int) -> bool:
    """Pause an active campaign."""
    conn = _get_conn()
    conn.execute(
        "UPDATE reengagement_campaigns SET status = 'paused', updated_at = ? WHERE id = ? AND status = 'active'",
        (_now_str(), campaign_id),
    )
    changed = conn.total_changes
    conn.commit()
    conn.close()
    return changed > 0


def resume_campaign(campaign_id: int) -> bool:
    """Resume a paused campaign."""
    conn = _get_conn()
    conn.execute(
        "UPDATE reengagement_campaigns SET status = 'active', updated_at = ? WHERE id = ? AND status = 'paused'",
        (_now_str(), campaign_id),
    )
    changed = conn.total_changes
    conn.commit()
    conn.close()
    return changed > 0


# =============================================================================
# TOUCH MANAGEMENT
# =============================================================================

def get_next_touches(limit: int = 10) -> list[dict]:
    """Get queued touches that are ready to send (scheduled_at <= now).

    Only returns touches from active campaigns.
    """
    conn = _get_conn()
    now = _now_str()
    rows = conn.execute("""
        SELECT t.*, c.target_segment, c.status as campaign_status,
               l.first_name, l.last_name, l.email, l.phone, l.event_type
        FROM reengagement_touches t
        JOIN reengagement_campaigns c ON t.campaign_id = c.id
        JOIN leads l ON t.lead_id = l.id
        WHERE t.status = 'queued'
        AND t.scheduled_at <= ?
        AND c.status = 'active'
        ORDER BY t.scheduled_at ASC
        LIMIT ?
    """, (now, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def format_touch_message(touch: dict) -> str:
    """Fill in template placeholders for a touch message."""
    name = (touch.get("first_name") or "there").strip()
    event_type = (touch.get("event_type") or "event").strip()
    template = touch.get("template_used", "")

    message = template.replace("{name}", name)
    message = message.replace("{event_type}", event_type)
    message = message.replace("{season_hook}", _season_hook())

    return message


def format_touch_for_approval(touch: dict) -> str:
    """Format a touch as a Telegram approval card for Kai."""
    name = f"{touch.get('first_name', '')} {touch.get('last_name', '')}".strip() or "Unknown"
    segment = touch.get("target_segment", "unknown")
    seg_label = SEGMENTS.get(segment, {}).get("label", segment)
    touch_num = touch.get("touch_number", 1)
    channel = (touch.get("channel") or "email").upper()
    lead_id = touch.get("lead_id", 0)
    touch_id = touch.get("id", 0)

    message = format_touch_message(touch)

    return (
        f"RE-ENGAGE APPROVAL\n"
        f"{'=' * 22}\n"
        f"Lead: {name} (#{lead_id})\n"
        f"Segment: {seg_label}\n"
        f"Touch: {touch_num} of 3\n"
        f"Channel: {channel}\n"
        f"Phone: {touch.get('phone') or 'N/A'}\n"
        f"Email: {touch.get('email') or 'N/A'}\n\n"
        f"PROPOSED MESSAGE:\n"
        f"{'=' * 22}\n"
        f"\"{message}\"\n\n"
        f"{'=' * 22}\n"
        f"Tap Approve to queue for send, or Skip.\n"
        f"Touch #{touch_id}"
    )


def mark_touch_pending_approval(touch_id: int) -> None:
    """Move touch from queued to pending_approval."""
    conn = _get_conn()
    conn.execute(
        "UPDATE reengagement_touches SET status = 'pending_approval' WHERE id = ?",
        (touch_id,),
    )
    conn.commit()
    conn.close()


def mark_touch_approved(touch_id: int) -> None:
    """Mark touch as approved (ready to send)."""
    conn = _get_conn()
    conn.execute(
        "UPDATE reengagement_touches SET status = 'approved' WHERE id = ?",
        (touch_id,),
    )
    conn.commit()
    conn.close()
    _log(f"Touch #{touch_id} approved")


def mark_touch_sent(touch_id: int) -> None:
    """Mark touch as sent and record timestamp."""
    conn = _get_conn()
    conn.execute(
        "UPDATE reengagement_touches SET status = 'sent', sent_at = ? WHERE id = ?",
        (_now_str(), touch_id),
    )
    conn.commit()
    conn.close()
    _log(f"Touch #{touch_id} marked sent")


def mark_touch_responded(touch_id: int) -> None:
    """Mark touch as responded and update campaign responded count."""
    conn = _get_conn()
    touch = conn.execute(
        "SELECT campaign_id FROM reengagement_touches WHERE id = ?",
        (touch_id,),
    ).fetchone()
    if touch:
        conn.execute(
            "UPDATE reengagement_touches SET status = 'responded', response_at = ? WHERE id = ?",
            (_now_str(), touch_id),
        )
        conn.execute(
            "UPDATE reengagement_campaigns SET leads_responded = leads_responded + 1, "
            "updated_at = ? WHERE id = ?",
            (_now_str(), touch["campaign_id"]),
        )
        conn.commit()
    conn.close()
    _log(f"Touch #{touch_id} marked responded")


def mark_touch_failed(touch_id: int) -> None:
    """Mark touch as failed."""
    conn = _get_conn()
    conn.execute(
        "UPDATE reengagement_touches SET status = 'failed' WHERE id = ?",
        (touch_id,),
    )
    conn.commit()
    conn.close()


def skip_touch(touch_id: int) -> None:
    """Skip a touch (Kai declined to send)."""
    conn = _get_conn()
    conn.execute(
        "UPDATE reengagement_touches SET status = 'failed' WHERE id = ?",
        (touch_id,),
    )
    conn.commit()
    conn.close()
    _log(f"Touch #{touch_id} skipped")


def _queue_next_touch(touch: dict) -> None:
    """After a touch is sent, queue the next touch in the sequence if applicable."""
    current_num = touch.get("touch_number", 1)
    if current_num >= 3:
        return  # sequence complete

    next_num = current_num + 1
    campaign_id = touch["campaign_id"]
    lead_id = touch["lead_id"]
    channel = touch.get("channel", "email")

    conn = _get_conn()

    # Check if next touch already exists
    existing = conn.execute(
        "SELECT id FROM reengagement_touches "
        "WHERE campaign_id = ? AND lead_id = ? AND touch_number = ?",
        (campaign_id, lead_id, next_num),
    ).fetchone()
    if existing:
        conn.close()
        return

    # Get segment from campaign
    campaign = conn.execute(
        "SELECT target_segment FROM reengagement_campaigns WHERE id = ?",
        (campaign_id,),
    ).fetchone()
    if not campaign:
        conn.close()
        return

    segment = campaign["target_segment"]
    templates = TEMPLATE_SEQUENCES.get(segment, {}).get(next_num, [])
    if not templates:
        conn.close()
        return

    template_text = random.choice(templates)
    delay_days = TOUCH_DELAYS.get(next_num, 7)
    scheduled = (datetime.utcnow() + timedelta(days=delay_days)).strftime("%Y-%m-%d %H:%M:%S")

    conn.execute(
        "INSERT INTO reengagement_touches "
        "(campaign_id, lead_id, touch_number, channel, template_used, status, scheduled_at) "
        "VALUES (?, ?, ?, ?, ?, 'queued', ?)",
        (campaign_id, lead_id, next_num, channel, template_text, scheduled),
    )
    conn.commit()
    conn.close()
    _log(f"Queued touch {next_num} for lead #{lead_id} in campaign #{campaign_id}")


# =============================================================================
# STATS & REPORTING
# =============================================================================

def get_campaign_stats() -> list[dict]:
    """Get overview stats for all campaigns."""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT c.*,
            (SELECT COUNT(*) FROM reengagement_touches t
             WHERE t.campaign_id = c.id AND t.status = 'sent') as touches_sent,
            (SELECT COUNT(*) FROM reengagement_touches t
             WHERE t.campaign_id = c.id AND t.status = 'queued') as touches_queued,
            (SELECT COUNT(*) FROM reengagement_touches t
             WHERE t.campaign_id = c.id AND t.status = 'pending_approval') as touches_pending,
            (SELECT COUNT(*) FROM reengagement_touches t
             WHERE t.campaign_id = c.id AND t.status = 'responded') as touches_responded
        FROM reengagement_campaigns c
        ORDER BY c.created_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_pending_approval_touches() -> list[dict]:
    """Get all touches currently pending Kai's approval."""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT t.*, c.target_segment,
               l.first_name, l.last_name, l.email, l.phone, l.event_type
        FROM reengagement_touches t
        JOIN reengagement_campaigns c ON t.campaign_id = c.id
        JOIN leads l ON t.lead_id = l.id
        WHERE t.status = 'pending_approval'
        ORDER BY t.created_at ASC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def format_reengagement_summary() -> str:
    """Telegram-formatted dashboard for re-engagement campaigns."""
    campaigns = get_campaign_stats()
    targets = identify_reengagement_targets()

    msg = "RE-ENGAGEMENT DASHBOARD\n"
    msg += "=" * 24 + "\n\n"

    # Available segments
    msg += "AVAILABLE SEGMENTS:\n"
    for seg_key, seg_info in SEGMENTS.items():
        count = targets.get(seg_key, {}).get("count", 0)
        msg += f"  {seg_info['label']}: {count} leads\n"
    total_available = sum(t.get("count", 0) for t in targets.values())
    msg += f"  Total: {total_available} re-engageable leads\n\n"

    # Active campaigns
    active = [c for c in campaigns if c["status"] == "active"]
    if active:
        msg += f"ACTIVE CAMPAIGNS ({len(active)}):\n"
        for c in active:
            seg_label = SEGMENTS.get(c["target_segment"], {}).get("label", c["target_segment"])
            resp_rate = round(c["leads_responded"] / c["leads_targeted"] * 100, 1) if c["leads_targeted"] > 0 else 0
            msg += (
                f"  #{c['id']} {seg_label}\n"
                f"    Targeted: {c['leads_targeted']} | Sent: {c.get('touches_sent', 0)}"
                f" | Responded: {c['leads_responded']} ({resp_rate}%)\n"
                f"    Queued: {c.get('touches_queued', 0)}"
                f" | Pending: {c.get('touches_pending', 0)}\n"
            )
    else:
        msg += "No active campaigns.\n"

    # Completed campaigns
    completed = [c for c in campaigns if c["status"] == "completed"]
    if completed:
        msg += f"\nCOMPLETED ({len(completed)}):\n"
        for c in completed[:5]:
            seg_label = SEGMENTS.get(c["target_segment"], {}).get("label", c["target_segment"])
            resp_rate = round(c["leads_responded"] / c["leads_targeted"] * 100, 1) if c["leads_targeted"] > 0 else 0
            msg += f"  #{c['id']} {seg_label}: {c['leads_responded']}/{c['leads_targeted']} responded ({resp_rate}%)\n"

    msg += "\nCommands:\n"
    msg += "  /reengage start [segment]\n"
    msg += "  /reengage queue\n"
    msg += "  /reengage pause [id]\n"

    return msg


# =============================================================================
# TELEGRAM COMMAND HANDLERS
# =============================================================================

def handle_reengage_command(text: str) -> str:
    """/reengage -- show summary + available segments."""
    return format_reengagement_summary()


def handle_reengage_start_command(text: str) -> str:
    """/reengage start [segment] [channel] -- start a campaign."""
    parts = text.strip().split()
    # Expected: /reengage start cold_30d [email]
    # Or: /reengage_start cold_30d [email]

    segment = None
    channel = "email"

    # Parse segment from various formats
    for p in parts:
        if p in SEGMENTS:
            segment = p
        elif p in ("email", "sms", "messenger"):
            channel = p

    if not segment:
        lines = ["Usage: /reengage start [segment] [channel]\n"]
        lines.append("Segments:")
        targets = identify_reengagement_targets()
        for seg_key, seg_info in SEGMENTS.items():
            count = targets.get(seg_key, {}).get("count", 0)
            lines.append(f"  {seg_key} -- {seg_info['label']} ({count} leads)")
        lines.append("\nChannels: email (default), sms")
        lines.append("\nExample: /reengage start cold_30d email")
        return "\n".join(lines)

    result = create_campaign(segment, channel)
    if not result.get("ok"):
        return f"Error: {result.get('error', 'Unknown error')}"

    return (
        f"Campaign started!\n"
        f"{'=' * 22}\n"
        f"ID: #{result['campaign_id']}\n"
        f"Segment: {SEGMENTS[segment]['label']}\n"
        f"Channel: {channel.upper()}\n"
        f"Leads targeted: {result['leads_targeted']}\n\n"
        f"Touch 1 queued for all leads.\n"
        f"Use /reengage queue to review and approve messages."
    )


def handle_reengage_queue_command(text: str) -> str:
    """/reengage queue -- show pending approval touches."""
    pending = get_pending_approval_touches()
    if not pending:
        # Check if there are queued touches ready to move to pending
        ready = get_next_touches(limit=20)
        if ready:
            return (
                f"No touches pending approval right now, but {len(ready)} "
                f"touch(es) are queued and ready.\n"
                f"The scheduler will move them to the approval queue at {SCHEDULER_HOUR} AM PT."
            )
        return "No re-engagement touches pending approval or queued."

    msg = f"RE-ENGAGE APPROVAL QUEUE ({len(pending)})\n"
    msg += "=" * 26 + "\n\n"

    for t in pending[:10]:
        name = f"{t.get('first_name', '')} {t.get('last_name', '')}".strip() or "Unknown"
        seg_label = SEGMENTS.get(t.get("target_segment", ""), {}).get("label", "?")
        msg += (
            f"#{t['id']} | {name} | Touch {t['touch_number']}/3\n"
            f"  Segment: {seg_label} | {(t.get('channel') or 'email').upper()}\n"
        )

    if len(pending) > 10:
        msg += f"\n... and {len(pending) - 10} more\n"

    msg += "\nEach touch will be sent to you for individual approval."
    return msg


def handle_reengage_pause_command(text: str) -> str:
    """/reengage pause [campaign_id] -- pause a campaign."""
    parts = text.strip().split()
    campaign_id = None
    for p in parts:
        try:
            campaign_id = int(p)
            break
        except ValueError:
            continue

    if campaign_id is None:
        return "Usage: /reengage pause [campaign_id]\nExample: /reengage pause 3"

    if pause_campaign(campaign_id):
        return f"Campaign #{campaign_id} paused. Use '/reengage resume {campaign_id}' to restart."
    return f"Campaign #{campaign_id} not found or not active."


def handle_reengage_resume_command(text: str) -> str:
    """/reengage resume [campaign_id] -- resume a paused campaign."""
    parts = text.strip().split()
    campaign_id = None
    for p in parts:
        try:
            campaign_id = int(p)
            break
        except ValueError:
            continue

    if campaign_id is None:
        return "Usage: /reengage resume [campaign_id]\nExample: /reengage resume 3"

    if resume_campaign(campaign_id):
        return f"Campaign #{campaign_id} resumed."
    return f"Campaign #{campaign_id} not found or not paused."


# =============================================================================
# CALLBACK HANDLER (Telegram approval buttons)
# =============================================================================

async def handle_reengage_callback(action: str, touch_id: int, send_fn) -> str:
    """Handle Telegram callback for re-engagement touch approval.

    Actions: re_approve, re_skip
    Returns status message to send back.
    """
    conn = _get_conn()
    touch = conn.execute(
        "SELECT t.*, l.first_name, l.last_name, l.email, l.phone, l.event_type, "
        "c.target_segment "
        "FROM reengagement_touches t "
        "JOIN leads l ON t.lead_id = l.id "
        "JOIN reengagement_campaigns c ON t.campaign_id = c.id "
        "WHERE t.id = ?",
        (touch_id,),
    ).fetchone()
    conn.close()

    if not touch:
        return f"Touch #{touch_id} not found."

    touch = dict(touch)

    if action == "re_approve":
        mark_touch_approved(touch_id)

        # Format the actual message that will be sent
        message = format_touch_message(touch)
        name = f"{touch.get('first_name', '')} {touch.get('last_name', '')}".strip()
        channel = (touch.get("channel") or "email").upper()

        # Queue the next touch in the sequence
        _queue_next_touch(touch)

        return (
            f"Touch #{touch_id} APPROVED\n"
            f"Lead: {name}\n"
            f"Channel: {channel}\n"
            f"Message ready to send via {channel}.\n\n"
            f"Note: Approved touches are sent through the lead pipeline."
        )

    elif action == "re_skip":
        skip_touch(touch_id)
        name = f"{touch.get('first_name', '')} {touch.get('last_name', '')}".strip()
        return f"Touch #{touch_id} skipped for {name}."

    return f"Unknown action: {action}"


# =============================================================================
# SCHEDULER
# =============================================================================

async def run_reengagement_scheduler(send_fn) -> None:
    """Daily 10 AM PT scheduler.

    1. Identify new targets across all segments
    2. Queue touches from active campaigns whose scheduled_at has passed
    3. Move queued touches to pending_approval
    4. Notify Kai of pending approvals via Telegram
    5. Heartbeat every 60s
    """
    try:
        from core.watchdog import heartbeat
    except ImportError:
        def heartbeat(_): pass

    _log("Re-engagement scheduler started")
    last_run_date = ""

    while True:
        try:
            heartbeat("reengagement")
            now = _now_pt()
            today = now.strftime("%Y-%m-%d")

            # Daily run at SCHEDULER_HOUR AM PT
            if now.hour == SCHEDULER_HOUR and today != last_run_date:
                last_run_date = today
                _log(f"Daily re-engagement check ({today})")

                # Step 1: Report available targets
                targets = identify_reengagement_targets()
                total = sum(t.get("count", 0) for t in targets.values())
                _log(f"Found {total} re-engageable leads across all segments")

                # Step 2: Get queued touches ready to send from active campaigns
                ready_touches = get_next_touches(limit=50)
                _log(f"{len(ready_touches)} touches ready for approval")

                # Step 3: Move to pending_approval and notify Kai
                approval_count = 0
                for touch in ready_touches:
                    touch_id = touch["id"]
                    mark_touch_pending_approval(touch_id)
                    approval_card = format_touch_for_approval(touch)

                    buttons = {
                        "inline_keyboard": [
                            [
                                {"text": "Approve", "callback_data": f"re_approve:{touch_id}"},
                                {"text": "Skip", "callback_data": f"re_skip:{touch_id}"},
                            ]
                        ]
                    }

                    try:
                        from telegram.bot import get_bot
                        bot = get_bot()
                        if bot:
                            for cid in bot.allowed:
                                await bot.send(cid, approval_card, reply_markup=buttons)
                        approval_count += 1
                    except Exception as e:
                        _log(f"Failed to send approval for touch #{touch_id}: {e}")

                # Step 4: Send daily summary if there's activity
                if total > 0 or approval_count > 0:
                    summary_parts = [
                        f"RE-ENGAGE DAILY REPORT ({today})",
                        "=" * 24,
                        f"Available leads: {total}",
                    ]
                    for seg_key, seg_info in SEGMENTS.items():
                        count = targets.get(seg_key, {}).get("count", 0)
                        if count > 0:
                            summary_parts.append(f"  {seg_info['label']}: {count}")
                    summary_parts.append(f"\nTouches sent for approval: {approval_count}")

                    # Active campaign stats
                    campaigns = get_campaign_stats()
                    active = [c for c in campaigns if c["status"] == "active"]
                    if active:
                        summary_parts.append(f"Active campaigns: {len(active)}")

                    try:
                        await send_fn("\n".join(summary_parts))
                    except Exception as e:
                        _log(f"Failed to send daily summary: {e}")

                _log("Daily re-engagement check complete")

        except Exception as e:
            _log(f"Scheduler error: {e}")
            traceback.print_exc()

        await asyncio.sleep(60)


# =============================================================================
# MODULE INIT
# =============================================================================

_initialized = False


def init_reengagement() -> None:
    """Initialize the re-engagement system. Safe to call on every startup."""
    global _initialized
    if not _initialized:
        init_reengagement_db()
        _initialized = True
