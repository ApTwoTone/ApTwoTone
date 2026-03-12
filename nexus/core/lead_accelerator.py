"""
Agent 1 — Lead Conversion Accelerator

Monitors website form submissions. Within 60 seconds:
1. Calculates exact quote using tier-based pricing
2. Generates personalized professional quote
3. Sends to Kai on Telegram with SEND / EDIT buttons
4. If no response in 10 min → URGENT ping
5. Logs everything to CRM
6. Leads not booking within 48 hours → daily digest
"""

import sqlite3
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timedelta

from core.pricing import calculate_quote, generate_quote_message, format_price_breakdown

log = logging.getLogger("lead_accelerator")
DB_PATH = Path.home() / ".nexus" / "memory.db"


def _db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _init_tables():
    """Ensure the lead_quotes tracking table exists."""
    conn = _db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lead_quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL,
            quote_amount REAL,
            tier INTEGER,
            distance_miles REAL,
            event_type TEXT DEFAULT '',
            event_date TEXT DEFAULT '',
            event_city TEXT DEFAULT '',
            guest_count INTEGER DEFAULT 0,
            customer_message TEXT DEFAULT '',
            telegram_message_id TEXT DEFAULT '',
            status TEXT DEFAULT 'pending_approval',
            sent_at TEXT DEFAULT '',
            urgent_ping_sent INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (lead_id) REFERENCES leads(id)
        )
    """)
    conn.commit()
    conn.close()


_init_tables()


async def process_new_lead(lead_id: int, bot=None):
    """Process a new website lead: calculate quote + send to Kai for approval.

    This should be called immediately when a lead submits the contact form.
    Target: complete within 60 seconds.
    """
    conn = _db()
    lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
        log.error(f"Lead {lead_id} not found")
        conn.close()
        return None

    lead = dict(lead)
    conn.close()

    first = lead.get("first_name", "")
    last = lead.get("last_name", "")
    name = f"{first} {last}".strip() or "there"
    event_type = lead.get("event_type", "")
    event_date = lead.get("event_date", "")
    event_city = lead.get("event_city", "")
    guest_count = lead.get("guest_count", 0)
    phone = lead.get("phone", "")
    email = lead.get("email", "")

    try:
        guest_count = int(guest_count) if guest_count else 0
    except (ValueError, TypeError):
        guest_count = 0

    # Calculate quote using tier-based pricing
    quote_data, customer_message = generate_quote_message(
        lead_name=name,
        event_type=event_type,
        event_date=event_date,
        event_city=event_city,
        guest_count=guest_count,
    )

    # Store quote in tracking table
    conn = _db()
    conn.execute(
        """INSERT INTO lead_quotes
           (lead_id, quote_amount, tier, distance_miles, event_type, event_date,
            event_city, guest_count, customer_message, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_approval')""",
        (lead_id, quote_data["total"], quote_data["tier"],
         quote_data["distance_miles"], event_type, event_date,
         event_city, guest_count, customer_message),
    )
    quote_row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Update lead record with quote info
    conn.execute(
        """UPDATE leads SET
           total_quote_amount = ?,
           booking_status = 'auto_contacted',
           updated_at = datetime('now')
           WHERE id = ?""",
        (quote_data["total"], lead_id),
    )
    conn.commit()
    conn.close()

    log.info(f"Lead {lead_id}: {name} — ${quote_data['total']:,.0f} "
             f"(Tier {quote_data['tier']}, {quote_data['distance_miles']}mi)")

    # Send Telegram notification to Kai with SEND / EDIT buttons
    if bot:
        await _send_quote_approval(bot, lead, quote_data, customer_message, quote_row_id)

        # Schedule urgent follow-up if no response in 10 minutes
        asyncio.create_task(_schedule_urgent_ping(bot, lead, quote_row_id))

    return {
        "lead_id": lead_id,
        "quote_id": quote_row_id,
        "total": quote_data["total"],
        "tier": quote_data["tier"],
    }


async def _send_quote_approval(bot, lead: dict, quote: dict, customer_msg: str, quote_row_id: int):
    """Send the quote to Kai on Telegram with SEND/EDIT buttons."""
    first = lead.get("first_name", "")
    last = lead.get("last_name", "")
    name = f"{first} {last}".strip() or "Unknown"
    phone = lead.get("phone", "")
    email = lead.get("email", "")
    event_type = lead.get("event_type", "")
    event_date = lead.get("event_date", "")
    source = lead.get("source", "website")

    text = (
        f"🚨 NEW LEAD — QUOTE READY\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 {name}\n"
        f"📱 {phone or 'No phone'}\n"
        f"📧 {email or 'No email'}\n"
        f"📍 {lead.get('event_city', 'Unknown location')}\n"
        f"🎉 {event_type or 'Event'}"
    )
    if event_date:
        text += f" — {event_date}"
    text += (
        f"\n📊 Source: {source}\n"
        f"\n💰 QUOTE: ${quote['total']:,.0f} (Tier {quote['tier']}, {quote['distance_miles']}mi)\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"\n📝 READY-TO-SEND MESSAGE:\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{customer_msg[:2000]}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"\n🆔 Quote #{quote_row_id} | Lead #{lead.get('id', '?')}\n"
        f"⏱ Auto-sends in 20 min if no action"
    )

    buttons = {
        "inline_keyboard": [
            [
                {"text": "✅ SEND Quote", "callback_data": f"send_quote:{quote_row_id}"},
                {"text": "✏️ EDIT First", "callback_data": f"edit_quote:{quote_row_id}"},
            ],
            [
                {"text": "❌ Skip", "callback_data": f"skip_quote:{quote_row_id}"},
            ],
        ]
    }

    msg_result = await bot.send_to_all(text, reply_markup=buttons)

    # Store telegram message ID for urgent ping reference
    if msg_result:
        conn = _db()
        conn.execute(
            "UPDATE lead_quotes SET telegram_message_id = ? WHERE id = ?",
            (str(msg_result), quote_row_id),
        )
        conn.commit()
        conn.close()


async def _schedule_urgent_ping(bot, lead: dict, quote_row_id: int):
    """
    Wait 10 min → URGENT ping to Kai.
    Wait another 10 min → auto-send quote if still pending (new website leads only).
    Total window: 20 minutes before auto-send.
    """
    await asyncio.sleep(600)  # 10 minutes

    conn = _db()
    row = conn.execute(
        "SELECT status FROM lead_quotes WHERE id = ?", (quote_row_id,)
    ).fetchone()
    conn.close()

    if row and row["status"] == "pending_approval":
        # Still pending — send urgent ping
        name = f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip()
        phone = lead.get("phone", "")

        urgent_text = (
            f"🔴 URGENT — LEAD GOING COLD\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 {name} ({phone})\n"
            f"Quote #{quote_row_id} has been waiting 10+ minutes\n\n"
            f"⚡ Respond NOW or auto-sends in 10 min\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        buttons = {
            "inline_keyboard": [
                [
                    {"text": "✅ SEND NOW", "callback_data": f"send_quote:{quote_row_id}"},
                    {"text": "❌ Skip", "callback_data": f"skip_quote:{quote_row_id}"},
                ]
            ]
        }
        await bot.send_to_all(urgent_text, reply_markup=buttons)

        conn = _db()
        conn.execute(
            "UPDATE lead_quotes SET urgent_ping_sent = 1 WHERE id = ?",
            (quote_row_id,),
        )
        conn.commit()
        conn.close()
        log.warning(f"Urgent ping sent for quote #{quote_row_id} (lead: {name})")

        # Schedule auto-send: if Kai still hasn't acted after another 10 min, auto-send
        # Only for new leads (requires_manual_approval=0)
        if not lead.get("requires_manual_approval", 1):
            asyncio.create_task(_auto_send_after_timeout(bot, lead, quote_row_id))


async def _auto_send_after_timeout(bot, lead: dict, quote_row_id: int):
    """
    Auto-send the quote 2 minutes after the urgent ping if Kai hasn't acted.
    Only fires for new website leads where requires_manual_approval=0.
    This is a BACKUP path — lead_pipeline._auto_send_initial() fires first for new leads.
    """
    await asyncio.sleep(120)  # 2 minutes after urgent ping

    conn = _db()
    row = conn.execute(
        "SELECT * FROM lead_quotes WHERE id = ?", (quote_row_id,)
    ).fetchone()
    conn.close()

    if not row or row["status"] != "pending_approval":
        # Kai already acted — nothing to do
        return

    name = f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip()
    log.info("Auto-sending quote #%s to %s (backup 2-min timeout, no Kai response)", quote_row_id, name)

    # Deliver quote to lead
    message_to_send = row["customer_message"]
    sent_via = await _deliver_quote_to_lead(
        lead_id=row["lead_id"],
        lead=lead,
        message=message_to_send,
    )

    # Update quote status
    conn = _db()
    conn.execute(
        "UPDATE lead_quotes SET status = 'sent', sent_at = datetime('now') WHERE id = ?",
        (quote_row_id,),
    )
    conn.execute(
        "UPDATE leads SET booking_status = 'qualifying', last_contacted_at = datetime('now'), "
        "updated_at = datetime('now') WHERE id = ?",
        (row["lead_id"],),
    )
    conn.commit()
    conn.close()

    # Notify Kai that auto-send happened
    fyi_text = (
        f"FYI: Auto-sent quote #{quote_row_id} to {name} (no Kai response in 20 min)\n"
        f"Sent via: {sent_via}\n"
        f"Lead status -> qualifying. Follow-up in 48h if no response."
    )
    if bot:
        await bot.send_to_all(fyi_text)
    log.info("Quote #%s auto-sent to lead #%s via %s", quote_row_id, row["lead_id"], sent_via)


async def handle_quote_callback(bot, chat_id: str, action: str, quote_row_id: int, custom_text: str = None):
    """Handle Kai's response to a quote notification (SEND, EDIT, SKIP)."""
    conn = _db()
    row = conn.execute(
        "SELECT * FROM lead_quotes WHERE id = ?", (quote_row_id,)
    ).fetchone()

    if not row:
        await bot.send(chat_id, "Quote not found.")
        conn.close()
        return

    row = dict(row)
    lead = conn.execute(
        "SELECT * FROM leads WHERE id = ?", (row["lead_id"],)
    ).fetchone()

    if action == "send_quote":
        # Mark as sent
        conn.execute(
            """UPDATE lead_quotes SET status = 'sent', sent_at = datetime('now')
               WHERE id = ?""", (quote_row_id,)
        )
        # Update lead status
        conn.execute(
            """UPDATE leads SET booking_status = 'qualifying',
               last_contacted_at = datetime('now'), updated_at = datetime('now')
               WHERE id = ?""", (row["lead_id"],)
        )
        conn.commit()
        conn.close()

        message_to_send = custom_text or row["customer_message"]

        # Send the quote to the lead via available channels
        sent_via = await _deliver_quote_to_lead(
            lead_id=row["lead_id"],
            lead=dict(lead) if lead else {},
            message=message_to_send,
        )

        await bot.send(
            chat_id,
            f"✅ Quote #{quote_row_id} SENT to {dict(lead).get('first_name', 'lead')} "
            f"via {sent_via}.\n"
            f"Lead status → qualifying. Follow-up in 48hrs if no response."
        )
        log.info(f"Quote #{quote_row_id} sent to lead #{row['lead_id']} via {sent_via}")

    elif action == "edit_quote":
        # Mark as awaiting edit — Kai will reply with custom text
        conn.execute(
            "UPDATE lead_quotes SET status = 'awaiting_edit' WHERE id = ?",
            (quote_row_id,),
        )
        conn.commit()
        conn.close()

        await bot.send(
            chat_id,
            f"✏️ Quote #{quote_row_id} — Reply with your edited message.\n"
            f"I'll send it to the lead exactly as you write it."
        )

    elif action == "skip_quote":
        conn.execute(
            "UPDATE lead_quotes SET status = 'skipped' WHERE id = ?",
            (quote_row_id,),
        )
        conn.execute(
            """UPDATE leads SET booking_status = 'new_lead',
               updated_at = datetime('now') WHERE id = ?""",
            (row["lead_id"],),
        )
        conn.commit()
        conn.close()
        await bot.send(chat_id, f"❌ Quote #{quote_row_id} skipped.")


async def _deliver_quote_to_lead(lead_id: int, lead: dict, message: str) -> str:
    """Send the quote message to the lead via SMS or email.

    Returns the channel used (e.g. 'sms', 'email').
    """
    phone = lead.get("phone", "")
    email = lead.get("email", "")

    # Try SMS first (higher response rate)
    if phone:
        try:
            from core.lead_pipeline import get_pipeline
            p = get_pipeline()
            if p:
                await p._send_sms(lead_id, phone, message)
                return "SMS"
        except Exception as e:
            log.warning(f"SMS delivery failed for lead {lead_id}: {e}")

    # Fall back to email
    if email:
        try:
            from integrations.gmail_imap import send_email
            await send_email(
                to=email,
                subject="Your Zoar Bathroom Rentals Quote",
                body=message,
            )
            return "email"
        except Exception as e:
            log.warning(f"Email delivery failed for lead {lead_id}: {e}")

    log.error(f"No delivery channel available for lead {lead_id}")
    return "none (no phone or email)"


async def get_stale_leads_digest() -> str:
    """Generate a digest of leads with quotes older than 48 hours that haven't booked."""
    conn = _db()
    cutoff = (datetime.utcnow() - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        """SELECT lq.*, l.first_name, l.last_name, l.phone, l.booking_status
           FROM lead_quotes lq
           JOIN leads l ON lq.lead_id = l.id
           WHERE lq.status = 'sent'
           AND lq.sent_at < ?
           AND l.booking_status NOT IN ('completed', 'deposit_pending', 'contract_pending')
           ORDER BY lq.sent_at ASC""",
        (cutoff,),
    ).fetchall()
    conn.close()

    if not rows:
        return ""

    lines = [
        f"⏰ LEADS NEEDING FOLLOW-UP ({len(rows)} quotes sent 48+ hrs ago)",
        "━━━━━━━━━━━━━━━━━━",
    ]
    for r in rows:
        r = dict(r)
        name = f"{r.get('first_name', '')} {r.get('last_name', '')}".strip()
        lines.append(
            f"• {name} ({r.get('phone', 'no phone')}) — "
            f"${r.get('quote_amount', 0):,.0f} — sent {r.get('sent_at', '?')}"
        )

    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append("Recommended: Follow up with a brief text mentioning date availability.")
    return "\n".join(lines)
