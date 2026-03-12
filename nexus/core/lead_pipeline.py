from __future__ import annotations
"""
Lead Pipeline Manager
- State machine for lead lifecycle (new → contact → follow-up → closed)
- Scheduler runs as asyncio background task every 60s
- Coordinates SMS (Google Voice → Twilio → email gateway) and email
- Triggers AI message generation via zoar_bot
- Sends Telegram notifications + SSE events to browser
"""
import asyncio, sqlite3, json, traceback
from pathlib import Path
from datetime import datetime, timedelta
import requests
from typing import Optional
from core.brain_models import canonical_model_for_specialist

DB_PATH = Path.home() / ".nexus" / "memory.db"

# ── Lead states ───────────────────────────────────────────────────────────────
STATES = {
    "new":               "Newly discovered, not yet contacted",
    "awaiting_approval": "Message queued, waiting for Kai's approval",
    "sms_sent":          "SMS sent, email pending (2 min delay)",
    "initial_contact":   "Initial SMS + email sent, waiting for reply",
    "waiting_reply":     "Contacted, awaiting response",
    "replied":           "Lead replied, conversation active",
    "follow_up_1":       "First follow-up sent (48h after initial)",
    "follow_up_2":       "Second follow-up sent (48h after first)",
    "closed":            "Conversation complete or unresponsive",
    "opted_out":         "Lead asked to stop",
}

FOLLOW_UP_DELAY_HOURS = 48
INITIAL_DELAY_SECONDS = 30    # 30 sec before first SMS — speed wins deals
EMAIL_DELAY_SECONDS = 120     # 2 min after SMS before email
KAI_PHONE = "8184489055"
# Safety: only auto-contact these numbers when production_mode=False (testing)
SAFE_PHONES = {"8184489055", "18184489055"}
SCHEDULER_INTERVAL = 60  # seconds
OPT_OUT_KEYWORDS = {"stop", "unsubscribe", "remove me", "don't text", "dont text", "opt out", "leave me alone"}

_pipeline = None


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _future(hours):
    return (datetime.utcnow() + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


def _future_seconds(seconds):
    return (datetime.utcnow() + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")


def _clean_phone(phone: str) -> str:
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def score_lead_with_specialist(lead: dict) -> Optional[float]:
    """Use trained lead_ranker specialist if available, else return None."""
    try:
        model_name = canonical_model_for_specialist("lead_ranker") or "nexus-lead-ranker"
        r = requests.post(
            "http://localhost:7860/api/brain/query",
            json={
                "model": model_name,
                "prompt": (
                    "Score this lead 1-10 for booking probability. "
                    f"Lead data: {json.dumps(lead, ensure_ascii=False)}. "
                    "Return only a number 1-10."
                ),
                "context": "You are the Nexus lead scoring specialist.",
                "max_tokens": 12,
                "temperature": 0.0,
            },
            timeout=10,
        )
        if r.status_code != 200:
            return None
        payload = r.json() if r.content else {}
        result = str(payload.get("response", "")).strip()
        if not result:
            return None
        score = float(result.split()[0])
        return min(max(score, 1.0), 10.0)
    except Exception:
        return None


class LeadPipeline:
    def __init__(self, config: dict, provider, notify_fn, broadcast_fn):
        """
        config: from ~/.nexus/config.json
        provider: LLMProvider instance for AI message generation
        notify_fn: async def(msg) → send to Telegram
        broadcast_fn: async def(event_dict) → SSE broadcast to browser UI
        """
        self.config = config
        self.provider = provider
        self.notify = notify_fn
        self.broadcast = broadcast_fn
        self._gv = None       # GoogleVoiceSMS instance
        self._twilio = None   # TwilioSMS instance
        self._messenger = None  # Messenger (email + email-to-SMS gateway)
        self._running = False
        self._init_db()

        # Initialize CRM services
        from core.services import init_services
        init_services(notify_fn, broadcast_fn)

        # Initialize approval queue
        from core.approval_queue import init_approval_db
        init_approval_db()

    def _init_db(self):
        conn = sqlite3.connect(str(DB_PATH))
        # Enable WAL mode for crash resilience and concurrent reads
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ghl_contact_id TEXT UNIQUE DEFAULT '',
            first_name TEXT DEFAULT '',
            last_name TEXT DEFAULT '',
            email TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            carrier TEXT DEFAULT 'tmobile',
            source TEXT DEFAULT '',
            date_added TEXT DEFAULT '',
            discovered_at TEXT DEFAULT (datetime('now')),
            status TEXT DEFAULT 'new',
            last_contacted_at TEXT DEFAULT '',
            last_reply_at TEXT DEFAULT '',
            follow_up_count INTEGER DEFAULT 0,
            next_action_at TEXT DEFAULT '',
            sms_method TEXT DEFAULT 'google_voice',
            initial_sms_sent INTEGER DEFAULT 0,
            initial_email_sent INTEGER DEFAULT 0,
            notes TEXT DEFAULT '',
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS lead_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            ts TEXT DEFAULT (datetime('now')),
            direction TEXT DEFAULT 'outbound',
            channel TEXT DEFAULT 'sms',
            method TEXT DEFAULT '',
            content TEXT DEFAULT '',
            subject TEXT DEFAULT '',
            status TEXT DEFAULT 'sent',
            error TEXT DEFAULT '',
            model_used TEXT DEFAULT '',
            FOREIGN KEY (lead_id) REFERENCES leads(id)
        );

        CREATE TABLE IF NOT EXISTS lead_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT DEFAULT (datetime('now')),
            lead_id INTEGER,
            event_type TEXT,
            details TEXT DEFAULT '',
            FOREIGN KEY (lead_id) REFERENCES leads(id)
        );
        -- Performance indexes for CRM queries
        CREATE INDEX IF NOT EXISTS idx_leads_phone ON leads(phone);
        CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email);
        CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
        CREATE INDEX IF NOT EXISTS idx_leads_source ON leads(source);
        CREATE INDEX IF NOT EXISTS idx_leads_created ON leads(discovered_at);
        CREATE INDEX IF NOT EXISTS idx_leads_next_action ON leads(next_action_at);
        CREATE INDEX IF NOT EXISTS idx_messages_lead ON lead_messages(lead_id);
        CREATE INDEX IF NOT EXISTS idx_events_lead ON lead_events(lead_id);
        """)
        # Historical schemas used an empty-string default for lead_messages.ts.
        # Normalize blank timestamps so CRM ordering remains reliable.
        conn.execute(
            "UPDATE lead_messages SET ts = datetime('now') WHERE ts IS NULL OR trim(ts) = ''"
        )
        conn.commit()
        conn.close()

        # Run CRM migrations (adds new columns, backfills data)
        from core.db_migrate import run_migrations
        run_migrations()
        print("[Pipeline] Database tables ready (with indexes)")

    # ── Sender registration ───────────────────────────────────────────────────

    def set_gv(self, gv):
        """Set Google Voice sender."""
        self._gv = gv

    def set_twilio(self, twilio):
        """Set Twilio fallback sender."""
        self._twilio = twilio

    def set_messenger(self, messenger):
        """Set email/SMS-gateway messenger."""
        self._messenger = messenger

    # ── SMS with fallback chain ───────────────────────────────────────────────

    async def send_sms(self, phone: str, message: str, lead_id: int = None,
                       approval_id: int = None) -> dict:
        """
        Try SMS via fallback chain:
        1. Google Voice (Playwright)
        2. Twilio (API)
        3. Email-to-SMS gateway (existing Messenger)

        REQUIRES: approval_id with a verified kai_approved/kai_edited status.
        The outbound_gate in each sender will block if missing.
        """
        errors = []
        target_phone = _clean_phone(phone) or str(phone or "")

        # 1. Google Voice (primary)
        if self._gv:
            try:
                if getattr(self._gv, "logged_in", False):
                    result = await self._gv.send_sms(
                        target_phone, message, approval_id=approval_id
                    )
                    if result.get("ok"):
                        if lead_id:
                            self._log_message(
                                lead_id, "outbound", "sms",
                                result.get("method", "google_voice"),
                                message,
                            )
                        return {
                            "ok": True,
                            "method": result.get("method", "google_voice"),
                        }
                    if result.get("method") == "blocked":
                        return result
                    errors.append(f"Google Voice: {result.get('error', 'unknown')}")
                else:
                    errors.append("Google Voice: not logged in")
            except Exception as e:
                errors.append(f"Google Voice: {e}")

        # 2. Twilio (fallback)
        if self._twilio:
            try:
                result = await self._twilio.send_sms(
                    target_phone, message, approval_id=approval_id
                )
                if result.get("ok"):
                    if lead_id:
                        self._log_message(
                            lead_id, "outbound", "sms",
                            result.get("method", "twilio"),
                            message,
                        )
                    return {"ok": True, "method": result.get("method", "twilio")}
                if result.get("method") == "blocked":
                    return result
                errors.append(f"Twilio: {result.get('error', 'unknown')}")
            except Exception as e:
                errors.append(f"Twilio: {e}")

        # 3. Email-to-SMS gateway (gate check inside Messenger.send_sms)
        if self._messenger:
            try:
                carrier = self._get_lead_carrier(lead_id) if lead_id else "tmobile"
                result = await self._messenger.send_sms(
                    target_phone, message, carrier, approval_id=approval_id)
                if result.get("ok"):
                    if lead_id:
                        self._log_message(lead_id, "outbound", "sms", "email_gateway", message)
                    return {"ok": True, "method": "email_gateway"}
                # If blocked by gate, propagate the block reason
                if result.get("method") == "blocked":
                    return result
                errors.append(f"Gateway: {result.get('error', 'unknown')}")
            except Exception as e:
                errors.append(f"Gateway: {e}")

        error = " | ".join(errors) or "No SMS sender configured"
        if lead_id:
            self._log_message(lead_id, "outbound", "sms", "failed", message, status="failed", error=error)
        return {"ok": False, "error": error}

    async def send_email(self, to_email: str, subject: str, body: str,
                         lead_id: int, approval_id: int = None) -> dict:
        """Send email via existing Messenger.

        REQUIRES: approval_id with a verified kai_approved/kai_edited status.
        The outbound_gate in Messenger.send_email will block if missing.
        """
        if not self._messenger:
            return {"ok": False, "error": "Email not configured"}
        try:
            result = await self._messenger.send_email(
                to_email, subject, body, approval_id=approval_id)
            # If blocked by gate, propagate
            if result.get("method") == "blocked":
                return result
            status = "sent" if result.get("ok") else "failed"
            self._log_message(lead_id, "outbound", "email", "gmail_smtp", body, subject, status,
                              result.get("error", ""))
            return result
        except Exception as e:
            self._log_message(lead_id, "outbound", "email", "gmail_smtp", body, subject, "failed", str(e))
            return {"ok": False, "error": str(e)}

    # ── State machine transitions ─────────────────────────────────────────────

    async def process_new_lead(self, lead_id: int):
        """Process a new lead. New leads (requires_manual_approval=0) get auto-sent.
        Existing contacts (requires_manual_approval=1) queue for Kai's Telegram approval."""
        print(f"[Pipeline] 📥 process_new_lead called for lead {lead_id}")
        # Guard: skip if already processed (prevents double-fire from endpoint + scheduler)
        lead = self.get_lead(lead_id)
        if lead and lead.get("status") in ("awaiting_approval", "contacted", "replied", "booked"):
            print(f"[Pipeline] ⏭️ Lead {lead_id} already in status={lead['status']} — skipping")
            return

        # Coordination event log (SQLite-backed).
        try:
            from core.nexus_coordination import log_event
            if lead:
                log_event(
                    "NEW_LEAD",
                    payload={
                        "lead_id": lead_id,
                        "name": f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip(),
                        "phone": lead.get("phone", ""),
                        "email": lead.get("email", ""),
                        "source": lead.get("source", ""),
                    },
                    source="lead_pipeline",
                    lead_id=lead_id,
                )
        except Exception:
            pass

        try:
            # New leads (requires_manual_approval=0): auto-send immediately
            # Existing contacts (requires_manual_approval=1): queue for Kai's Telegram approval
            if not lead.get("requires_manual_approval", 1):
                await self._auto_send_initial(lead_id)
            else:
                await self._queue_initial_for_approval(lead_id)
        except Exception as e:
            print(f"[Pipeline] ❌ CRITICAL: initial outreach FAILED for lead {lead_id}: {e}")
            import traceback
            traceback.print_exc()
            # Still mark as awaiting_approval so lead isn't lost — Kai can see it manually
            try:
                conn = sqlite3.connect(str(DB_PATH))
                conn.execute(
                    "UPDATE leads SET status='awaiting_approval', notes=notes||' [AI_FAILED]', updated_at=? WHERE id=?",
                    (_now(), lead_id)
                )
                conn.commit()
                conn.close()
                # Send raw Telegram notification even without the AI-generated message
                lead = self.get_lead(lead_id)
                if lead:
                    name = f"{lead['first_name']} {lead['last_name']}".strip()
                    await self._notify(
                        f"🚨 NEW LEAD (AI failed):\n"
                        f"**{name}**\n"
                        f"📱 {lead.get('phone', 'no phone')}\n"
                        f"📧 {lead.get('email', 'no email')}\n"
                        f"Source: {lead.get('source', '?')}\n\n"
                        f"⚠️ Auto-message generation failed. Check Telegram to respond manually."
                    )
            except Exception as e2:
                print(f"[Pipeline] ❌ Even fallback notification failed: {e2}")

    async def _queue_initial_for_approval(self, lead_id: int):
        """Queue initial SMS + email for Kai's approval via Telegram. NEVER auto-send."""
        lead = self.get_lead(lead_id)
        if not lead:
            return
        name = f"{lead['first_name']} {lead['last_name']}".strip() or "there"
        first_name = (lead.get("first_name") or "there").strip()

        from core.approval_queue import queue_message, mark_notified
        lead_source = lead.get("source", "unknown")

        # Template fallbacks if AI generation fails
        sms_template = (
            f"Hi {first_name}! Thanks for reaching out about our luxury restroom trailer. "
            f"We have a 4-stall trailer with AC, running water, LED lighting, and hardwood floors — "
            f"perfect for any event. Want me to check availability for your date? 🚿"
        )
        email_template = (
            f"Hi {first_name},\n\n"
            f"Thanks for your interest in Zoar Bathroom Rentals! We offer a luxury 4-stall restroom "
            f"trailer with climate control, running water, LED lighting, full-length mirrors, and "
            f"hardwood-style flooring — your guests will love it.\n\n"
            f"I'd love to check availability for your event. Could you share your event date and "
            f"location? I'll get you a quick quote.\n\n"
            f"Best,\nKai\nZoar Bathroom Rentals\n(424) 235-8979"
        )
        email_subject_template = "Your Luxury Restroom Trailer Quote — Zoar Bathroom Rentals"
        # Optional specialist personalization (conversion_specialist)
        try:
            from core.specialist_router import get_specialist_router
            specialist_payload = {
                "lead_id": lead_id,
                "event_type": lead.get("event_type") or "",
                "guest_count": lead.get("guest_count"),
                "event_city": lead.get("event_city") or "",
                "quote_price": 999,
            }
            spec = get_specialist_router().route("conversion_specialist", specialist_payload)
            spec_msg = (spec or {}).get("message")
            if isinstance(spec_msg, str) and spec_msg.strip():
                sms_template = spec_msg.strip()
                email_template = (
                    f"Hi {first_name},\n\n"
                    f"{spec_msg.strip()}\n\n"
                    "If you want, I can send a quick quote summary and lock your date.\n\n"
                    "Best,\nKai\nZoar Bathroom Rentals\n(424) 235-8979"
                )
        except Exception:
            pass

        queued_any = False

        # Generate proposed SMS
        if lead.get("phone"):
            sms_body = sms_template
            try:
                from integrations.zoar_bot import gen_initial
                if self.provider:
                    sms_body, _, _ = await gen_initial(self.provider, "sms", name, source=lead_source)
                    print(f"[Pipeline] ✅ AI-generated SMS for {name}")
                else:
                    print(f"[Pipeline] ⚠️ No AI provider — using SMS template for {name}")
            except Exception as e:
                print(f"[Pipeline] ⚠️ AI SMS generation failed for {name}: {e} — using template")

            sms_aid = queue_message(
                lead_id=lead_id, lead_name=name,
                lead_phone=lead.get("phone", ""), lead_email=lead.get("email", ""),
                lead_source=lead_source, channel="sms", message_type="initial",
                proposed_message=sms_body,
            )
            if sms_aid:
                await self._send_approval_notification(sms_aid, "initial")
                queued_any = True
                print(f"[Pipeline] 📋 SMS approval #{sms_aid} queued for {name}")

        # Generate proposed email
        if lead.get("email"):
            email_body = email_template
            email_subject = email_subject_template
            try:
                from integrations.zoar_bot import gen_initial
                if self.provider:
                    email_body, email_subject, _ = await gen_initial(self.provider, "email", name, source=lead_source)
                    print(f"[Pipeline] ✅ AI-generated email for {name}")
                else:
                    print(f"[Pipeline] ⚠️ No AI provider — using email template for {name}")
            except Exception as e:
                print(f"[Pipeline] ⚠️ AI email generation failed for {name}: {e} — using template")

            email_aid = queue_message(
                lead_id=lead_id, lead_name=name,
                lead_phone=lead.get("phone", ""), lead_email=lead.get("email", ""),
                lead_source=lead_source, channel="email", message_type="initial",
                proposed_message=email_body, proposed_subject=email_subject,
            )
            if email_aid:
                await self._send_approval_notification(email_aid, "initial")
                queued_any = True
                print(f"[Pipeline] 📋 Email approval #{email_aid} queued for {name}")

        if not queued_any:
            print(f"[Pipeline] ❌ No messages queued for lead {lead_id} — no phone or email")
            return

        # Mark lead as awaiting approval (don't change to sms_sent until approved)
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "UPDATE leads SET status='awaiting_approval', updated_at=? WHERE id=?",
            (_now(), lead_id)
        )
        conn.commit()
        conn.close()

        self._log_event(lead_id, "queued_for_approval", f"Initial outreach queued for {name}")
        print(f"[Pipeline] ✅ Lead {lead_id} ({name}) → awaiting_approval")
        await self._broadcast({"type": "lead_event", "event": "awaiting_approval",
                               "lead_id": lead_id, "name": name})

    async def _auto_send_initial(self, lead_id: int):
        """Auto-send initial SMS + email to NEW leads (requires_manual_approval=0).

        Creates a pre-approved record in message_approvals for audit trail,
        then sends immediately. All 10 security gates still run (blocklist,
        DLP, rate limits, etc.) — only the manual Telegram approval wait is skipped.
        Kai gets an informational Telegram notification (no buttons needed).

        Respects send window (8am-5pm PT). If outside window, defers to next morning.
        """
        from integrations.messaging import _check_blocklist
        from core.send_window import now_in_window

        # Quiet hours gate: defer if outside send window
        in_window, now_local, win = now_in_window()
        if not in_window:
            from datetime import timedelta
            next_morning = now_local.replace(hour=win.start_hour, minute=0, second=0, microsecond=0)
            if next_morning <= now_local:
                next_morning += timedelta(days=1)
            next_str = next_morning.strftime("%Y-%m-%d %H:%M:%S")
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute(
                "UPDATE leads SET next_action_at=?, status='new', updated_at=? WHERE id=?",
                (next_str, _now(), lead_id)
            )
            conn.commit()
            conn.close()
            self._log_event(lead_id, "deferred_quiet_hours",
                            f"Outside send window ({win.start_hour}:00-{win.end_hour}:00 {win.timezone}). "
                            f"Deferred to {next_str}")
            print(f"[Pipeline] Deferred lead {lead_id} to {next_str} (outside send window)")
            return

        lead = self.get_lead(lead_id)
        if not lead:
            return
        name = f"{lead['first_name']} {lead['last_name']}".strip() or "there"
        first_name = (lead.get("first_name") or "there").strip()
        lead_source = lead.get("source", "unknown")

        # Safety: double-check this is actually a new lead
        if lead.get("requires_manual_approval", 1):
            print(f"[Pipeline] ⚠️ _auto_send_initial called for manual-approval lead {lead_id} — falling back")
            await self._queue_initial_for_approval(lead_id)
            return

        # Generate message content (same logic as _queue_initial_for_approval)
        sms_body = (
            f"Hi {first_name}! Thanks for reaching out about our luxury restroom trailer. "
            f"We have a 4-stall trailer with AC, running water, LED lighting, and hardwood floors — "
            f"perfect for any event. Want me to check availability for your date?"
        )
        email_body = (
            f"Hi {first_name},\n\n"
            f"Thanks for your interest in Zoar Bathroom Rentals! We offer a luxury 4-stall restroom "
            f"trailer with climate control, running water, LED lighting, full-length mirrors, and "
            f"hardwood-style flooring — your guests will love it.\n\n"
            f"I'd love to check availability for your event. Could you share your event date and "
            f"location? I'll get you a quick quote.\n\n"
            f"Best,\nKai\nZoar Bathroom Rentals\n(424) 235-8979"
        )
        email_subject = "Your Luxury Restroom Trailer Quote — Zoar Bathroom Rentals"

        # Try AI-generated messages (specialist router)
        try:
            from core.specialist_router import get_specialist_router
            spec_payload = {
                "lead_id": lead_id,
                "event_type": lead.get("event_type") or "",
                "guest_count": lead.get("guest_count"),
                "event_city": lead.get("event_city") or "",
                "quote_price": 999,
            }
            spec = get_specialist_router().route("conversion_specialist", spec_payload)
            spec_msg = (spec or {}).get("message")
            if isinstance(spec_msg, str) and spec_msg.strip():
                sms_body = spec_msg.strip()
                email_body = (
                    f"Hi {first_name},\n\n"
                    f"{spec_msg.strip()}\n\n"
                    "If you want, I can send a quick quote summary and lock your date.\n\n"
                    "Best,\nKai\nZoar Bathroom Rentals\n(424) 235-8979"
                )
        except Exception:
            pass

        # Try AI generation via zoar_bot
        try:
            from integrations.zoar_bot import gen_initial
            if self.provider:
                ai_sms, ai_subject, _ = await gen_initial(self.provider, "sms", name, source=lead_source)
                if ai_sms:
                    sms_body = ai_sms
                ai_email, ai_email_subj, _ = await gen_initial(self.provider, "email", name, source=lead_source)
                if ai_email:
                    email_body = ai_email
                if ai_email_subj:
                    email_subject = ai_email_subj
        except Exception as e:
            print(f"[Pipeline] ⚠️ AI generation failed for auto-send to {name}: {e} — using templates")

        sent_any = False

        # Auto-send SMS
        if lead.get("phone"):
            phone = lead["phone"]
            if _check_blocklist(phone):
                print(f"[Pipeline] 🚫 BLOCKLIST: {phone} blocked — skipping SMS auto-send")
            else:
                approval_id = self._create_system_approval("sms", phone, sms_body)
                result = await self.send_sms(phone, sms_body, lead_id=lead_id, approval_id=approval_id)
                if result.get("ok"):
                    sent_any = True
                    print(f"[Pipeline] ✅ Auto-sent SMS to {name} ({phone})")
                else:
                    print(f"[Pipeline] ⚠️ SMS auto-send failed for {name}: {result.get('error', 'unknown')}")

        # Auto-send email
        if lead.get("email"):
            email_addr = lead["email"]
            if _check_blocklist(email_addr):
                print(f"[Pipeline] 🚫 BLOCKLIST: {email_addr} blocked — skipping email auto-send")
            else:
                approval_id = self._create_system_approval("email", email_addr, email_body)
                result = await self.send_email(email_addr, email_subject, email_body,
                                               lead_id=lead_id, approval_id=approval_id)
                if result.get("ok"):
                    sent_any = True
                    print(f"[Pipeline] ✅ Auto-sent email to {name} ({email_addr})")
                else:
                    print(f"[Pipeline] ⚠️ Email auto-send failed for {name}: {result.get('error', 'unknown')}")

        # Update lead status
        conn = sqlite3.connect(str(DB_PATH))
        if sent_any:
            conn.execute(
                "UPDATE leads SET status='contacted', booking_status='auto_contacted', "
                "last_contacted_at=?, updated_at=? WHERE id=?",
                (_now(), _now(), lead_id)
            )
        else:
            # Nothing sent (no phone/email or all blocked) — still mark as processed
            conn.execute(
                "UPDATE leads SET status='awaiting_approval', updated_at=? WHERE id=?",
                (_now(), lead_id)
            )
        conn.commit()
        conn.close()

        self._log_event(lead_id, "auto_sent_initial", f"Auto-sent initial outreach to {name}")

        # Advance CRM pipeline stage
        try:
            from core.services.pipeline_service import PipelineService
            ps = PipelineService()
            if sent_any:
                ps.auto_transition_on_action(lead_id, "initial_contact")
        except Exception as e:
            print(f"[Pipeline] ⚠️ Pipeline transition error: {e}")

        # Auto-generate quote if lead has event details
        quote_info = ""
        if sent_any and (lead.get("event_type") or lead.get("event_city") or lead.get("event_date")):
            try:
                from core.quote_generator import create_quote
                from core.pricing import calculate_quote as _calc_quote
                event_city = lead.get("event_city") or ""
                price = 999.0  # Default starting price
                if event_city:
                    try:
                        price_data = _calc_quote(event_city)
                        if price_data.get("total"):
                            price = float(price_data["total"])
                    except Exception:
                        pass  # Use default price if geocoding fails

                qr = create_quote(
                    client_name=name,
                    event_type=lead.get("event_type", ""),
                    event_date=lead.get("event_date", ""),
                    event_location=event_city,
                    guest_count=int(lead.get("guest_count") or 0),
                    price=price,
                    client_email=lead.get("email", ""),
                    client_phone=lead.get("phone", ""),
                    lead_id=lead_id,
                )
                if qr.get("ok"):
                    quote_info = f"\nQuote: {qr['quote_number']} — ${qr.get('total', price):,.0f}"
                    # Update lead with quote amount
                    conn2 = sqlite3.connect(str(DB_PATH))
                    conn2.execute(
                        "UPDATE leads SET total_quote_amount=?, updated_at=? WHERE id=?",
                        (qr.get("total", price), _now(), lead_id)
                    )
                    conn2.commit()
                    conn2.close()
                    # Advance pipeline to quote_sent
                    ps.auto_transition_on_action(lead_id, "quote_sent")
                    self._log_event(lead_id, "auto_quote_generated",
                                    f"Quote {qr['quote_number']} auto-generated: ${qr.get('total', price):,.0f}")
                    print(f"[Pipeline] ✅ Auto-generated quote {qr['quote_number']} for lead {lead_id}")
            except Exception as e:
                print(f"[Pipeline] ⚠️ Auto-quote generation failed for lead {lead_id}: {e}")

        # Informational Telegram notification (no approval buttons needed)
        await self._notify(
            f"NEW LEAD — Auto-contacted\n"
            f"{name}\n"
            f"Phone: {lead.get('phone', 'none')}\n"
            f"Email: {lead.get('email', 'none')}\n"
            f"Source: {lead_source}\n"
            f"{'SMS + Email sent' if sent_any else 'No contact info — check manually'}"
            f"{quote_info}"
        )
        await self._broadcast({"type": "lead_event", "event": "auto_contacted",
                               "lead_id": lead_id, "name": name})

    async def _send_approval_notification(self, approval_id: int, msg_type: str):
        """Send a Telegram notification for a pending approval."""
        from core.approval_queue import get_pending, mark_notified
        from telegram.bot import get_bot

        approval = get_pending(approval_id)
        if not approval:
            return

        bot = get_bot()
        if not bot:
            print("[Pipeline] No Telegram bot — cannot send approval notification")
            return

        if msg_type in ("initial",):
            text, buttons = bot.format_new_lead_approval(approval)
        else:
            text, buttons = bot.format_followup_approval(approval)

        for cid in bot.allowed:
            await bot.send(cid, text, reply_markup=buttons)
            # Set context so text replies route to this approval
            bot.set_context(cid, approval_id=approval_id)

        mark_notified(approval_id)

        # Slack (secondary) — if configured
        name = approval.get("lead_name", "Unknown")
        source = approval.get("lead_source", "?")
        try:
            from integrations.slack_notify import send_slack
            await send_slack(f"📋 Approval needed: {name} from {source} — check Telegram")
        except Exception:
            pass

        # SMS to Kai DISABLED — Telegram notifications are the primary channel.
        # Re-enable only if Telegram becomes unreliable.
        # await self._notify_kai_sms(f"NEW LEAD: {name} from {source} — check Telegram to approve")

    async def execute_approved_message(self, approval: dict) -> dict:
        """Send a message that Kai has approved. Returns send result.

        The approval_id is passed through to the outbound_gate, which verifies
        this approval exists and was approved by Kai before allowing the send.
        """
        lead_id = approval["lead_id"]
        approval_id = approval.get("id")
        lead = self.get_lead(lead_id)
        if not lead:
            return {"ok": False, "error": "Lead not found"}

        channel = approval["channel"]
        message = approval["actual_message_sent"]
        subject = approval.get("proposed_subject", "")
        name = f"{lead['first_name']} {lead['last_name']}".strip()

        result = {"ok": False}
        method = "none"

        if channel == "sms" and lead.get("phone"):
            result = await self.send_sms(lead["phone"], message, lead_id,
                                          approval_id=approval_id)
            method = result.get("method", "none")
        elif channel == "email" and lead.get("email"):
            result = await self.send_email(lead["email"], subject, message, lead_id,
                                            approval_id=approval_id)
            method = "email"

        if result.get("ok"):
            # Update lead status based on message type
            msg_type = approval.get("message_type", "initial")
            conn = sqlite3.connect(str(DB_PATH))

            if msg_type == "initial" and channel == "sms":
                conn.execute(
                    "UPDATE leads SET status='sms_sent', booking_status='auto_contacted', "
                    "last_contacted_at=?, next_action_at=?, initial_sms_sent=1, sms_method=?, "
                    "updated_at=? WHERE id=?",
                    (_now(), _future_seconds(EMAIL_DELAY_SECONDS), method, _now(), lead_id)
                )
            elif msg_type == "initial" and channel == "email":
                conn.execute(
                    "UPDATE leads SET status='initial_contact', booking_status='auto_contacted', "
                    "initial_email_sent=1, next_action_at=?, updated_at=? WHERE id=?",
                    (_future(FOLLOW_UP_DELAY_HOURS), _now(), lead_id)
                )
            elif msg_type == "followup":
                step = approval.get("followup_step", 0)
                new_status = f"follow_up_{step + 1}"
                conn.execute(
                    "UPDATE leads SET status=?, booking_status='qualifying', "
                    "follow_up_count=follow_up_count+1, last_contacted_at=?, "
                    "next_action_at=?, updated_at=? WHERE id=?",
                    (new_status, _now(), _future(FOLLOW_UP_DELAY_HOURS), _now(), lead_id)
                )
            elif msg_type == "reply":
                conn.execute(
                    "UPDATE leads SET last_contacted_at=?, updated_at=? WHERE id=?",
                    (_now(), _now(), lead_id)
                )

            conn.commit()
            conn.close()

            self._log_event(lead_id, f"{channel}_sent",
                           f"{'Approved' if approval.get('approval_method') == 'kai_approved' else 'Edited'} by Kai, sent via {method}")

            # CAPI tracking
            try:
                from integrations.meta_capi import fire_contact_event
                fire_contact_event(
                    email=lead.get("email", ""),
                    phone=lead.get("phone", ""),
                    first_name=lead.get("first_name", ""),
                    last_name=lead.get("last_name", ""),
                    method=channel,
                )
            except Exception:
                pass

        await self._broadcast({"type": "lead_event", "event": "message_sent",
                               "lead_id": lead_id, "name": name, "channel": channel,
                               "ok": result.get("ok", False)})
        return result

    async def _queue_followup_for_approval(self, lead_id: int, followup_step: int):
        """Queue a follow-up message for Kai's approval."""
        # Guard: don't queue if this lead already has a pending approval
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        existing = conn.execute(
            "SELECT id FROM message_approvals WHERE lead_id = ? AND status = 'pending' LIMIT 1",
            (lead_id,)
        ).fetchone()
        conn.close()
        if existing:
            return  # Already has a pending approval — skip

        lead = self.get_lead(lead_id)
        if not lead:
            return
        name = f"{lead['first_name']} {lead['last_name']}".strip()

        from integrations.zoar_bot import gen_followup
        from core.approval_queue import queue_message

        text, _ = await gen_followup(self.provider, name, followup_step)

        # Get last outbound and inbound messages for context
        last_out, last_in = "", ""
        msgs = self.get_lead_messages(lead_id)
        for m in reversed(msgs):
            if m.get("direction") == "outbound" and not last_out:
                last_out = m.get("content", "")[:200]
            if m.get("direction") == "inbound" and not last_in:
                last_in = m.get("content", "")[:200]

        channel = "sms" if lead.get("phone") else "email"
        aid = queue_message(
            lead_id=lead_id, lead_name=name,
            lead_phone=lead.get("phone", ""), lead_email=lead.get("email", ""),
            lead_source=lead.get("source", "unknown"),
            channel=channel, message_type="followup",
            proposed_message=text, followup_step=followup_step,
            last_outbound=last_out, last_inbound=last_in,
        )
        await self._send_approval_notification(aid, "followup")
        self._log_event(lead_id, "followup_queued", f"Follow-up {followup_step+1} queued for approval")

    # ── Legacy _send_initial_email kept as internal helper ─────────────────
    # (now only called by execute_approved_message, not directly)

    async def _send_initial_email_direct(self, lead_id: int):
        """Direct email send — only called after Kai approves."""
        lead = self.get_lead(lead_id)
        if not lead:
            return
        name = f"{lead['first_name']} {lead['last_name']}".strip()

        from integrations.zoar_bot import gen_initial
        lead_source = lead.get("source", "unknown")
        email_body, email_subject, email_model = await gen_initial(self.provider, "email", name, source=lead_source)

        email_ok = False
        no_email = not lead["email"]
        if lead["email"]:
            result = await self.send_email(lead["email"], email_subject, email_body, lead_id)
            email_ok = result.get("ok", False)

        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "UPDATE leads SET status='initial_contact', initial_email_sent=?, "
            "next_action_at=?, updated_at=? WHERE id=?",
            (int(email_ok), _future(FOLLOW_UP_DELAY_HOURS), _now(), lead_id)
        )
        conn.commit()
        conn.close()

        if no_email:
            email_status = "skipped (no email on file)"
        elif email_ok:
            email_status = "sent"
        else:
            email_status = "failed"

        self._log_event(lead_id, "initial_contact", f"Email: {email_status}")

        if email_ok:
            try:
                from integrations.meta_capi import fire_contact_event
                fire_contact_event(
                    email=lead.get("email", ""),
                    phone=lead.get("phone", ""),
                    first_name=lead.get("first_name", ""),
                    last_name=lead.get("last_name", ""),
                    method="email",
                )
            except Exception:
                pass

        from core.services import get_pipeline_service
        ps = get_pipeline_service()
        if ps:
            ps.auto_transition_on_action(lead_id, "initial_contact")

        msg = f"Follow-up email for **{name}**: {email_status}"
        await self._notify(msg)
        await self._broadcast({"type": "lead_event", "event": "initial_contact", "lead_id": lead_id,
                               "name": name, "status": "email_" + ("ok" if email_ok else ("skip" if no_email else "failed"))})

    async def process_reply(self, lead_id: int, channel: str, message: str, from_addr: str = ""):
        """Reply detected → update status, notify Telegram + Kai SMS."""
        lead = self.get_lead(lead_id)
        if not lead:
            return
        name = f"{lead['first_name']} {lead['last_name']}".strip()

        # Check for opt-out
        if any(kw in message.lower() for kw in OPT_OUT_KEYWORDS):
            await self.opt_out_lead(lead_id)
            return

        # Cross-channel duplicate check: skip if we got a reply on the other channel within 5 min
        if self._is_duplicate_reply(lead_id, channel):
            print(f"[Pipeline] Skipping duplicate cross-channel reply for lead {lead_id} on {channel}")
            self._log_message(lead_id, "inbound", channel, "received", message)
            return

        # Save inbound message
        self._log_message(lead_id, "inbound", channel, "received", message)

        # Update lead
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "UPDATE leads SET status='replied', booking_status='qualifying', last_reply_at=?, next_action_at='', updated_at=? WHERE id=?",
            (_now(), _now(), lead_id)
        )
        conn.commit()
        conn.close()

        # Stop follow-up sequence (lead replied — no more auto-messages)
        try:
            from core.follow_up_engine import get_follow_up_engine
            engine = get_follow_up_engine()
            if engine:
                engine.stop_sequence(lead_id, "lead_replied")
        except Exception:
            pass

        self._log_event(lead_id, "reply_received", f"{channel}: {message[:100]}")
        self._maybe_fire_qualified_lead_event(lead_id, lead)

        # Auto-advance CRM pipeline stage
        from core.services import get_pipeline_service
        ps = get_pipeline_service()
        if ps:
            ps.auto_transition_on_action(lead_id, "reply_received")

        preview = message[:200] if len(message) <= 200 else message[:200] + "..."

        # Get our last outbound message for context
        our_last = ""
        msgs = self.get_lead_messages(lead_id)
        for m in reversed(msgs):
            if m.get("direction") == "outbound":
                our_last = m.get("content", "")[:300]
                break

        # Send rich Telegram notification with reply context
        from telegram.bot import get_bot
        bot = get_bot()
        if bot:
            text, buttons = bot.format_lead_reply(
                lead_name=name,
                lead_phone=lead.get("phone", ""),
                lead_email=lead.get("email", ""),
                reply_text=message,
                our_last_message=our_last,
                channel=channel,
                lead_id=lead_id,
            )
            for cid in bot.allowed:
                await bot.send(cid, text, reply_markup=buttons)
                # Set context so Kai's next text reply gets forwarded to this lead
                bot.set_context(cid, lead_id=lead_id, channel=channel)

        # Slack (secondary)
        try:
            from integrations.slack_notify import send_slack
            await send_slack(f"💬 Lead reply from {name} ({channel}): {message[:80]}")
        except Exception:
            pass

        # SMS to Kai (emergency fallback)
        await self._notify_kai_sms(f"REPLY from {name} ({channel}): {message[:80]}")

        await self._broadcast({"type": "lead_event", "event": "reply_received", "lead_id": lead_id,
                               "name": name, "channel": channel, "preview": preview})

    async def process_follow_up(self, lead_id: int, follow_up_num: int):
        """Queue follow-up for Kai's approval (never auto-send)."""
        await self._queue_followup_for_approval(lead_id, follow_up_num)

    async def close_lead(self, lead_id: int, reason: str = "no_response"):
        """Mark lead as closed."""
        lead = self.get_lead(lead_id)
        name = f"{lead['first_name']} {lead['last_name']}".strip() if lead else "Unknown"
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("UPDATE leads SET status='closed', next_action_at='', updated_at=? WHERE id=?",
                     (_now(), lead_id))
        conn.commit()
        conn.close()
        self._log_event(lead_id, "closed", reason)
        from core.services import get_pipeline_service
        ps = get_pipeline_service()
        if ps:
            ps.auto_transition_on_action(lead_id, "closed")
        await self._notify(f"Lead closed ({reason}): **{name}**")

    async def opt_out_lead(self, lead_id: int):
        """Mark lead as opted out."""
        lead = self.get_lead(lead_id)
        name = f"{lead['first_name']} {lead['last_name']}".strip() if lead else "Unknown"
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("UPDATE leads SET status='opted_out', next_action_at='', updated_at=? WHERE id=?",
                     (_now(), lead_id))
        conn.commit()
        conn.close()
        # Stop follow-up sequence
        try:
            from core.follow_up_engine import get_follow_up_engine
            engine = get_follow_up_engine()
            if engine:
                engine.stop_sequence(lead_id, "opted_out")
        except Exception:
            pass
        self._log_event(lead_id, "opted_out", "")
        from core.services import get_pipeline_service
        ps = get_pipeline_service()
        if ps:
            ps.auto_transition_on_action(lead_id, "opted_out")
        await self._notify(f"Lead opted out: **{name}** (will not be contacted again)")

    # ── Scheduler ─────────────────────────────────────────────────────────────

    async def run_scheduler(self):
        """Main scheduler loop. Runs every 60s, processes new leads + due follow-ups."""
        self._running = True
        print("[Pipeline] Scheduler started")
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                print(f"[Pipeline] Scheduler error: {e}")
                traceback.print_exc()
                await self._notify(f"Pipeline error: {e}")
            await asyncio.sleep(SCHEDULER_INTERVAL)

    async def _tick(self):
        """Single scheduler tick — queue messages for Kai's approval, never auto-send."""
        now = _now()
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

        # 1. Process new leads → queue for Kai's approval (NEVER auto-send)
        cutoff = (datetime.utcnow() - timedelta(seconds=INITIAL_DELAY_SECONDS)).strftime("%Y-%m-%d %H:%M:%S")
        new_leads = conn.execute(
            "SELECT id, phone FROM leads WHERE status = 'new' AND discovered_at <= ?", (cutoff,)
        ).fetchall()
        for row in new_leads:
            await self._queue_initial_for_approval(row["id"])

        # 2. Process due follow-ups → queue for approval (not auto-send)
        due = conn.execute(
            "SELECT id, status, follow_up_count, phone FROM leads "
            "WHERE next_action_at != '' AND next_action_at <= ? "
            "AND status NOT IN ('closed', 'opted_out', 'replied', 'new', 'awaiting_approval')", (now,)
        ).fetchall()

        # Get leads that already have a pending approval (avoid duplicate follow-up spam)
        pending_lead_ids = set()
        if due:
            pending_rows = conn.execute(
                "SELECT DISTINCT lead_id FROM message_approvals WHERE status = 'pending'"
            ).fetchall()
            pending_lead_ids = {r["lead_id"] for r in pending_rows}

        for row in due:
            if row["id"] in pending_lead_ids:
                continue  # Already has a pending approval — don't queue another
            if row["status"] in ("initial_contact", "waiting_reply", "sms_sent"):
                await self._queue_followup_for_approval(row["id"], 0)
            elif row["status"] == "follow_up_1":
                await self._queue_followup_for_approval(row["id"], 1)
            elif row["status"] == "follow_up_2":
                await self.close_lead(row["id"])

        # 3. Check for approval reminders (2h and 24h)
        try:
            from core.approval_queue import get_needs_reminder, increment_reminder
            from telegram.bot import get_bot
            bot = get_bot()
            if bot:
                reminders = get_needs_reminder()
                for r in reminders:
                    is_final = r["reminder_count"] >= 1
                    text = bot.format_reminder(r, is_final=is_final)
                    for cid in bot.allowed:
                        await bot.send(cid, text)
                    increment_reminder(r["id"])
                    if is_final:
                        # Mark as needs_attention after final reminder
                        conn2 = sqlite3.connect(str(DB_PATH))
                        conn2.execute(
                            "UPDATE leads SET notes = notes || ' [needs_attention]' WHERE id=?",
                            (r["lead_id"],)
                        )
                        conn2.commit()
                        conn2.close()
        except Exception as e:
            print(f"[Pipeline] Reminder check error: {e}")

        conn.close()

    def stop(self):
        self._running = False

    # ── Reply matching ────────────────────────────────────────────────────────

    def match_phone_to_lead(self, phone: str) -> int | None:
        """Find lead ID by phone number."""
        from integrations.zoar_bot import _clean_phone
        digits = _clean_phone(phone)
        if not digits:
            return None
        conn = sqlite3.connect(str(DB_PATH))
        # Match against cleaned phone numbers
        rows = conn.execute("SELECT id, phone FROM leads WHERE status NOT IN ('closed', 'opted_out')").fetchall()
        conn.close()
        for row in rows:
            if _clean_phone(row[1]) == digits:
                return row[0]
        return None

    def match_email_to_lead(self, email: str) -> int | None:
        """Find lead ID by email address."""
        email = email.lower().strip()
        conn = sqlite3.connect(str(DB_PATH))
        row = conn.execute(
            "SELECT id FROM leads WHERE lower(email) = ? AND status NOT IN ('closed', 'opted_out')", (email,)
        ).fetchone()
        conn.close()
        return row[0] if row else None

    # ── Query helpers ─────────────────────────────────────────────────────────

    def get_lead(self, lead_id: int) -> dict | None:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_leads(self, status: str = None, limit: int = 50) -> list:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        if status:
            rows = conn.execute("SELECT * FROM leads WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
                                (status, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM leads ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_lead_messages(self, lead_id: int) -> list:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM lead_messages WHERE lead_id = ? ORDER BY ts ASC",
                            (lead_id,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        conn = sqlite3.connect(str(DB_PATH))
        counts = {}
        for status in STATES:
            row = conn.execute("SELECT COUNT(*) FROM leads WHERE status = ?", (status,)).fetchone()
            counts[status] = row[0]
        total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        msgs_today = conn.execute(
            "SELECT COUNT(*) FROM lead_messages WHERE ts >= date('now') AND direction = 'outbound'"
        ).fetchone()[0]
        conn.close()
        return {"counts": counts, "total": total, "messages_today": msgs_today}

    def add_lead_manual(self, first_name: str, last_name: str = "", phone: str = "",
                        email: str = "", carrier: str = "tmobile", source: str = "manual",
                        notes: str = "") -> int:
        """Manually add a lead (for testing or non-GHL leads)."""
        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.execute(
            "INSERT INTO leads (first_name, last_name, phone, email, carrier, source, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (first_name, last_name, phone, email, carrier, source, notes)
        )
        lead_id = cur.lastrowid
        conn.commit()
        conn.close()
        self._log_event(lead_id, "new_lead", f"Manual add: {first_name} {last_name}")
        return lead_id

    def update_lead(self, lead_id: int, **kwargs):
        """Update lead fields."""
        allowed = {"first_name", "last_name", "email", "phone", "carrier", "source",
                    "notes", "status", "next_action_at"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        updates["updated_at"] = _now()
        set_clause = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [lead_id]
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(f"UPDATE leads SET {set_clause} WHERE id=?", values)
        conn.commit()
        conn.close()

    async def reply_to_lead(self, lead_id: int, channel: str, message: str) -> dict:
        """Send Kai's reply to a lead (via Telegram conversation forwarding)."""
        lead = self.get_lead(lead_id)
        if not lead:
            return {"ok": False, "error": "Lead not found"}

        result = {"ok": False}
        subject = "Re: Zoar Bathroom Rentals"
        approval_id = self._create_manual_reply_approval(
            lead=lead,
            channel=channel,
            message=message,
            subject=subject,
        )
        if channel == "sms" and lead.get("phone"):
            result = await self.send_sms(lead["phone"], message, lead_id, approval_id=approval_id)
        elif channel == "email" and lead.get("email"):
            result = await self.send_email(lead["email"], subject, message, lead_id, approval_id=approval_id)
        else:
            return {"ok": False, "error": f"No {channel} for lead"}

        if result.get("ok"):
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute(
                "UPDATE leads SET last_contacted_at=?, updated_at=? WHERE id=?",
                (_now(), _now(), lead_id)
            )
            conn.commit()
            conn.close()
            self._log_event(lead_id, "kai_reply", f"Kai replied via {channel}")

        return result

    # ── Kai notifications ─────────────────────────────────────────────────────

    def _create_system_approval(self, channel: str, recipient: str, message: str) -> int:
        """Create an approved message_approvals row for internal notifications."""
        now = _now()
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            """
            INSERT INTO message_approvals (
                lead_id, lead_name, lead_phone, lead_email, lead_source,
                channel, message_type, proposed_message, proposed_subject,
                actual_message_sent, status, approval_method,
                created_at, approved_at, telegram_notified
            ) VALUES (?, ?, ?, ?, ?, ?, 'manual_reply', ?, '', ?, 'approved', 'kai_approved', ?, ?, 1)
            """,
            (
                0,
                "System Notification",
                recipient if channel == "sms" else "",
                recipient if channel == "email" else "",
                "system",
                (channel or "sms").strip().lower(),
                (message or "")[:4000],
                (message or "")[:4000],
                now,
                now,
            ),
        )
        approval_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.commit()
        conn.close()
        return approval_id

    def _create_manual_reply_approval(
        self,
        lead: dict,
        channel: str,
        message: str,
        subject: str = "",
    ) -> int:
        """Create an approved message_approvals row for explicit manual replies."""
        now = _now()
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            """
            INSERT INTO message_approvals (
                lead_id, lead_name, lead_phone, lead_email, lead_source,
                channel, message_type, proposed_message, proposed_subject,
                actual_message_sent, status, approval_method,
                created_at, approved_at, telegram_notified
            ) VALUES (?, ?, ?, ?, ?, ?, 'manual_reply', ?, ?, ?, 'approved', 'kai_approved', ?, ?, 1)
            """,
            (
                int(lead.get("id") or 0),
                f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip()
                or lead.get("full_name", "")
                or "Lead",
                (lead.get("phone") or "").strip(),
                (lead.get("email") or "").strip().lower(),
                (lead.get("source") or "").strip(),
                (channel or "sms").strip().lower(),
                (message or "")[:4000],
                (subject or "")[:250],
                (message or "")[:4000],
                now,
                now,
            ),
        )
        approval_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.commit()
        conn.close()
        return approval_id

    async def _notify_kai_sms(self, message: str):
        """Send a short notification SMS to Kai's personal phone.

        Uses the standard send_sms() fallback chain first (GV → Twilio → Messenger).
        If all fail, falls back to email-to-SMS via Gmail SMTP.
        """
        if not self.config.get("kai_notifications", True):
            return
        approval_id = None
        try:
            approval_id = self._create_system_approval("sms", KAI_PHONE, message)
            result = await self.send_sms(KAI_PHONE, message, approval_id=approval_id)
            if result.get("ok"):
                return
        except Exception as e:
            print(f"[Pipeline] Kai SMS via send_sms failed: {e}")

        # Fallback: email-to-SMS via Gmail SMTP → carrier gateway
        try:
            if approval_id is None:
                approval_id = self._create_system_approval("sms", KAI_PHONE, message)
            await self._email_to_sms(KAI_PHONE, message, approval_id=approval_id)
        except Exception as e:
            print(f"[Pipeline] Kai email-to-SMS fallback also failed: {e}")

    async def _email_to_sms(self, phone: str, message: str, approval_id: int = None):
        """Send SMS via email-to-SMS carrier gateway using Gmail SMTP.

        Carrier gateways: T-Mobile → @tmomail.net, AT&T → @txt.att.net,
        Verizon → @vtext.com, Sprint → @messaging.sprintpcs.com

        REQUIRES: outbound_gate check. Even internal fallback path is gated.
        """
        from integrations.messaging import outbound_gate
        gate = outbound_gate("sms", phone, "", message,
                             approval_id=approval_id, code_path="LeadPipeline._email_to_sms")
        if not gate["ok"]:
            print(f"[Pipeline] _email_to_sms BLOCKED: {gate['reason']}")
            return

        import smtplib
        from email.mime.text import MIMEText
        import re as _re

        gateways = {
            "tmobile": "tmomail.net",
            "att": "txt.att.net",
            "verizon": "vtext.com",
            "sprint": "messaging.sprintpcs.com",
        }
        carrier = self.config.get("default_carrier", "tmobile")
        domain = gateways.get(carrier, "tmomail.net")
        clean_phone = _re.sub(r'\D', '', phone)
        if clean_phone.startswith("1") and len(clean_phone) == 11:
            clean_phone = clean_phone[1:]  # Strip country code
        to_addr = f"{clean_phone}@{domain}"

        gmail_addr = self.config.get("gmail_address", "")
        gmail_pass = self.config.get("gmail_app_password", "")
        if not gmail_addr or not gmail_pass:
            print("[Pipeline] Email-to-SMS: No Gmail credentials configured")
            return

        # Truncate to SMS length (160 chars)
        sms_text = message[:160]
        msg = MIMEText(sms_text)
        msg["From"] = gmail_addr
        msg["To"] = to_addr
        msg["Subject"] = ""  # No subject for SMS gateway

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._smtp_send, gmail_addr, gmail_pass, to_addr, msg)
        print(f"[Pipeline] Email-to-SMS sent to {to_addr}")

    @staticmethod
    def _smtp_send(gmail_addr, gmail_pass, to_addr, msg):
        """Blocking SMTP send — run in executor."""
        import smtplib
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(gmail_addr, gmail_pass)
            smtp.send_message(msg)

    def _is_duplicate_reply(self, lead_id: int, channel: str) -> bool:
        """Check if we already processed a reply from this lead on another channel within 5 min."""
        other_channel = "email" if channel == "sms" else "sms"
        cutoff = (datetime.utcnow() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(str(DB_PATH))
        row = conn.execute(
            "SELECT COUNT(*) FROM lead_messages WHERE lead_id = ? AND direction = 'inbound' "
            "AND channel = ? AND ts >= ?",
            (lead_id, other_channel, cutoff)
        ).fetchone()
        conn.close()
        return row[0] > 0 if row else False

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_lead_carrier(self, lead_id: int) -> str:
        conn = sqlite3.connect(str(DB_PATH))
        row = conn.execute("SELECT carrier FROM leads WHERE id = ?", (lead_id,)).fetchone()
        conn.close()
        return row[0] if row else "tmobile"

    def _maybe_fire_qualified_lead_event(self, lead_id: int, lead: dict):
        """Send Meta QualifiedLead feedback once for Facebook-origin leads."""
        source = str(lead.get("source", "") or "").lower()
        source_detail = str(lead.get("source_detail", "") or "").lower()
        is_facebook = ("facebook" in source) or ("facebook" in source_detail) or source.startswith("fb_")
        if not is_facebook:
            return

        try:
            conn = sqlite3.connect(str(DB_PATH))
            sent = conn.execute(
                "SELECT 1 FROM lead_events WHERE lead_id = ? AND event_type = 'meta_qualified_lead_sent' LIMIT 1",
                (lead_id,),
            ).fetchone()
            conn.close()
            if sent:
                return
        except Exception:
            pass

        try:
            from integrations.meta_capi_events import fire_qualified_lead

            fire_qualified_lead({
                "id": lead_id,
                "lead_id": lead_id,
                "first_name": lead.get("first_name", ""),
                "last_name": lead.get("last_name", ""),
                "email": lead.get("email", ""),
                "phone": lead.get("phone", ""),
                "city": lead.get("event_city", "") or lead.get("city", ""),
                "event_type": lead.get("event_type", "") or "event",
                "fbc": lead.get("fbc", ""),
                "fbp": lead.get("fbp", ""),
            })
            self._log_event(lead_id, "meta_qualified_lead_sent", "QualifiedLead fired on inbound reply")
        except Exception as e:
            print(f"[Pipeline] QualifiedLead event error for lead {lead_id}: {e}")

    def _log_message(self, lead_id, direction, channel, method, content,
                     subject="", status="sent", error="", model=""):
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "INSERT INTO lead_messages (lead_id, ts, direction, channel, method, content, subject, status, error, model_used) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                lead_id,
                _now(),
                direction,
                channel,
                method,
                content,
                subject,
                status,
                error,
                model,
            )
        )
        conn.commit()
        conn.close()

    def _log_event(self, lead_id, event_type, details=""):
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute("INSERT INTO lead_events (lead_id, event_type, details) VALUES (?, ?, ?)",
                         (lead_id, event_type, details))
            conn.commit()
            conn.close()
        except Exception:
            pass

    async def _notify(self, msg):
        if self.notify:
            try:
                await self.notify(msg)
            except Exception as e:
                print(f"[Pipeline] Telegram notify error: {e}")

    async def _broadcast(self, event):
        if self.broadcast:
            try:
                await self.broadcast(event)
            except Exception:
                pass


# ── Module-level init ─────────────────────────────────────────────────────────

def init_pipeline(config, provider, notify_fn, broadcast_fn) -> LeadPipeline:
    global _pipeline
    _pipeline = LeadPipeline(config, provider, notify_fn, broadcast_fn)
    return _pipeline


def get_pipeline() -> LeadPipeline | None:
    return _pipeline
