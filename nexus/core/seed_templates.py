"""
Seed SMS/Email templates for quick-send from CRM dashboard.
Run once or on startup to ensure templates exist.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".nexus" / "memory.db"

TEMPLATES = [
    # ── Initial Contact ─────────────────────────────────────────
    {
        "name": "Initial Intro (SMS)",
        "channel": "sms",
        "category": "initial",
        "body": "Hey {first_name}! This is Kai from Zoar Bathroom Rentals. Thanks for your interest in our luxury restroom trailer! What's the date and location of your {event_type}? I'll check availability for you right now 🙌",
        "subject": "",
        "variables": ["first_name", "event_type"],
    },
    {
        "name": "Initial Intro (Email)",
        "channel": "email",
        "category": "initial",
        "body": "Hey {first_name},\n\nThanks for reaching out about our luxury restroom trailer! I'm Kai from Zoar Bathroom Rentals.\n\nOur 4-stall trailer is perfect for {event_type}s — it comes with flushing toilets, running water, AC, LED lighting, mirrors, and even a Bluetooth speaker. Delivery, setup, and pickup are all included.\n\nWhat's the date and location of your event? I'd love to check availability and get you a quote!\n\nBest,\nKai\nZoar Bathroom Rentals\n(424) 235-8979",
        "subject": "Your Event Restroom Rental — Zoar Bathroom Rentals",
        "variables": ["first_name", "event_type"],
    },

    # ── Follow-Up ───────────────────────────────────────────────
    {
        "name": "Gentle Check-In (SMS)",
        "channel": "sms",
        "category": "follow_up",
        "body": "Hey {first_name}, just checking in! Still looking for a restroom trailer for your {event_type}? Happy to answer any questions or get you a quick quote 😊",
        "subject": "",
        "variables": ["first_name", "event_type"],
    },
    {
        "name": "Availability Urgency (SMS)",
        "channel": "sms",
        "category": "follow_up",
        "body": "Hey {first_name}! Quick heads up — weekend dates are filling up fast this month. Want me to hold your date? Just let me know and I'll lock it in for you!",
        "subject": "",
        "variables": ["first_name"],
    },
    {
        "name": "Final Check-In (SMS)",
        "channel": "sms",
        "category": "follow_up",
        "body": "Hey {first_name}, just wanted to check one last time — still need a luxury restroom trailer for your event? No worries either way! Here if you need us 🙏",
        "subject": "",
        "variables": ["first_name"],
    },

    # ── Pricing/Quote ───────────────────────────────────────────
    {
        "name": "Quick Price Quote (SMS)",
        "channel": "sms",
        "category": "pricing",
        "body": "Hey {first_name}! We'd love to help with your {event_type}. Our luxury restroom trailer includes delivery, setup, and pickup — we'll put together a custom quote based on your event details. What's the date? 😊",
        "subject": "",
        "variables": ["first_name", "event_type"],
    },
    {
        "name": "Discount Offer (SMS)",
        "channel": "sms",
        "category": "pricing",
        "body": "Hey {first_name}! Pricing depends on your event location and date — happy to put together a personalized quote for your {event_type}. Our trailer has AC, flushing toilets, LED lights, the works. Delivery, setup, and pickup all included. Want me to hold your date?",
        "subject": "",
        "variables": ["first_name", "event_type"],
    },

    # ── Booking/Confirmation ────────────────────────────────────
    {
        "name": "Booking Confirmation (SMS)",
        "channel": "sms",
        "category": "booking",
        "body": "Hey {first_name}! 🎉 You're all booked for {event_date}! I'll reach out a few days before to confirm delivery details. Thanks for choosing Zoar — can't wait to make your event awesome!",
        "subject": "",
        "variables": ["first_name", "event_date"],
    },
    {
        "name": "Pre-Event Reminder (SMS)",
        "channel": "sms",
        "category": "booking",
        "body": "Hey {first_name}! Just confirming everything for {event_date}. Our team will have the trailer delivered and set up before your guests arrive. Any last-minute questions? We're here for you!",
        "subject": "",
        "variables": ["first_name", "event_date"],
    },
    {
        "name": "Post-Event Thank You (SMS)",
        "channel": "sms",
        "category": "booking",
        "body": "Hey {first_name}! Hope your {event_type} was amazing! 🎊 Thanks for choosing Zoar. If you ever need us again or know someone who does, we'd love to help. Have a great rest of your week!",
        "subject": "",
        "variables": ["first_name", "event_type"],
    },

    # ── Info/FAQ ────────────────────────────────────────────────
    {
        "name": "What's Included (SMS)",
        "channel": "sms",
        "category": "info",
        "body": "Our luxury trailer has 4 private stalls with flushing toilets, running water, AC, LED lighting, full-length mirrors, and a Bluetooth speaker. Delivery, setup and pickup included! Want to see photos?",
        "subject": "",
        "variables": [],
    },
    {
        "name": "Service Areas (SMS)",
        "channel": "sms",
        "category": "info",
        "body": "We cover all of Greater LA! That includes the Valley, Santa Clarita, Ventura, Oxnard, Santa Monica, and everywhere in between. What area is your event in?",
        "subject": "",
        "variables": [],
    },
]


def seed_templates():
    """Insert templates if they don't already exist."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""CREATE TABLE IF NOT EXISTS sms_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        channel TEXT NOT NULL DEFAULT 'sms',
        category TEXT DEFAULT 'general',
        body TEXT NOT NULL,
        subject TEXT DEFAULT '',
        variables TEXT DEFAULT '[]',
        use_count INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    )""")

    existing = conn.execute("SELECT COUNT(*) FROM sms_templates").fetchone()[0]
    if existing > 0:
        print(f"[Templates] Already seeded ({existing} templates exist)")
        conn.close()
        return existing

    import json
    for t in TEMPLATES:
        conn.execute(
            "INSERT INTO sms_templates (name, channel, category, body, subject, variables) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (t["name"], t["channel"], t["category"], t["body"],
             t.get("subject", ""), json.dumps(t.get("variables", [])))
        )

    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM sms_templates").fetchone()[0]
    conn.close()
    print(f"[Templates] Seeded {count} message templates")
    return count


if __name__ == "__main__":
    seed_templates()
