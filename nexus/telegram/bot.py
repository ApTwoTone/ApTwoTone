"""
Nexus Telegram Bot — Enhanced with approval workflow.

Every outbound message to leads requires Kai's explicit approval via Telegram.
Supports: inline buttons, /queue, reply forwarding, reminder system.
"""
import httpx, asyncio, json, os, shutil, sqlite3, subprocess, traceback
from typing import Optional, Callable, Any
from datetime import datetime
from pathlib import Path

from core.telegram_policy import should_send_notification


class TelegramBot:
    def __init__(self, token, allowed_chat_ids=None):
        self.token = token
        self.base = f"https://api.telegram.org/bot{token}"
        self.allowed = [str(x) for x in (allowed_chat_ids or [])]
        self._offset = 0
        self._running = False
        self._handler = None               # General task handler
        self._approval_handler = None       # Approval response handler
        self._reply_handler = None          # Lead reply forwarding handler
        self._b2b_approval_handler = None   # B2B email approval handler
        self._pending_context = {}          # chat_id → {approval_id, lead_id, action} for context
        self._pending_email = {}            # chat_id → {to, subject} waiting for body text

        try:
            from core.nexus_coordination import init_coordination_tables
            init_coordination_tables()
        except Exception:
            # Coordination tables are optional at boot and can be lazily created.
            pass

    def set_handler(self, fn):
        self._handler = fn

    def set_approval_handler(self, fn):
        """fn(chat_id, approval_id, action, custom_text) — handles approve/skip/edit/stop."""
        self._approval_handler = fn

    def set_reply_handler(self, fn):
        """fn(chat_id, lead_id, channel, message_text) — forwards Kai's reply to a lead."""
        self._reply_handler = fn

    def set_b2b_approval_handler(self, fn):
        """fn(chat_id, lead_id, action) — handles B2B email approve/deny."""
        self._b2b_approval_handler = fn

    # ── Sending ──────────────────────────────────────────────────────────────

    async def send(self, chat_id, text, parse_mode="Markdown", reply_markup=None):
        """Send a text message, optionally with inline keyboard."""
        if not self.token:
            return None
        result = None
        for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
            payload = {
                "chat_id": chat_id,
                "text": chunk,
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode
            if reply_markup and chunk == text[:4000]:  # Only add buttons to first chunk
                payload["reply_markup"] = json.dumps(reply_markup)
            async with httpx.AsyncClient(timeout=15) as c:
                try:
                    r = await c.post(f"{self.base}/sendMessage", json=payload)
                    data = r.json()
                    if data.get("ok"):
                        result = data.get("result", {}).get("message_id")
                except Exception as e:
                    print(f"[TG] Send error: {e}")
        return result

    async def send_to_all(self, text, reply_markup=None, category=None, force=False,
                          source="telegram.bot.send_to_all"):
        """Send a broadcast notification to all allowed chat IDs."""
        result = None
        for cid in self.allowed:
            decision = should_send_notification(
                text,
                source=source,
                category=category,
                force=force,
                recipient=str(cid),
            )
            if not decision.allowed:
                print(
                    "[TG] Suppressed broadcast to %s (%s: %s)"
                    % (cid, decision.category, decision.reason)
                )
                continue
            result = await self.send(cid, text, reply_markup=reply_markup)
        return result

    async def send_photo(self, chat_id, photo_path: str, caption: str = ""):
        """Send a photo file."""
        if not self.token:
            return
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                with open(photo_path, "rb") as f:
                    files = {"photo": f}
                    data = {"chat_id": chat_id, "caption": caption[:1024]}
                    await c.post(f"{self.base}/sendPhoto", data=data, files=files)
        except Exception as e:
            print(f"[TG] Photo send error: {e}")

    async def send_document(self, chat_id, file_path: str, caption: str = ""):
        """Send a document/file."""
        if not self.token:
            return
        try:
            async with httpx.AsyncClient(timeout=60) as c:
                with open(file_path, "rb") as f:
                    files = {"document": f}
                    data = {"chat_id": chat_id, "caption": caption[:1024]}
                    await c.post(f"{self.base}/sendDocument", data=data, files=files)
        except Exception as e:
            print(f"[TG] Document send error: {e}")

    async def edit_message(self, chat_id, message_id, text, parse_mode="Markdown"):
        """Edit an existing message."""
        if not self.token:
            return
        async with httpx.AsyncClient(timeout=15) as c:
            try:
                await c.post(f"{self.base}/editMessageText", json={
                    "chat_id": chat_id, "message_id": message_id,
                    "text": text, "parse_mode": parse_mode
                })
            except Exception:
                pass

    # ── Legacy helpers ────────────────────────────────────────────────────────

    async def notify_done(self, task, result, duration=0):
        preview = result[:400] + "..." if len(result) > 400 else result
        await self.send_to_all(f"✅ *Done* ({duration:.0f}s)\n\n*Task:* {task[:80]}\n\n{preview}")

    async def notify_agents(self, count, reason):
        await self.send_to_all(f"⚡ *Spawning {count} agents*\n\n{reason}")

    # ── Approval notification formatting ──────────────────────────────────────

    def _get_lead_score_line(self, lead_id: int) -> str:
        """Get formatted lead score line for notifications."""
        try:
            from core.lead_scoring import score_lead
            import sqlite3
            from pathlib import Path
            db = Path.home() / ".nexus" / "memory.db"
            conn = sqlite3.connect(str(db))
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
            conn.close()
            if row:
                info = score_lead(dict(row))
                score = info.get("score", 0)
                label = info.get("label", "unknown")
                priority = info.get("priority", "")
                emoji = "🔥" if score >= 8 else "⚡" if score >= 5 else "📋"
                return f"{emoji} Lead Score: {score}/10 — {label.upper()} ({priority})"
        except Exception:
            pass
        return ""

    def format_new_lead_approval(self, approval: dict) -> tuple[str, dict]:
        """Format a new lead approval notification. Returns (text, reply_markup)."""
        aid = approval["id"]
        channel_emoji = "📱" if approval["channel"] == "sms" else "📧"
        channel_name = "SMS" if approval["channel"] == "sms" else "Email"

        # Get lead score
        score_line = self._get_lead_score_line(approval.get("lead_id", 0))
        score_section = f"\n{score_line}\n" if score_line else "\n"

        text = (
            f"🔔 NEW LEAD RECEIVED\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 Name: {approval['lead_name']}\n"
            f"📱 Phone: {approval['lead_phone'] or 'N/A'}\n"
            f"📧 Email: {approval['lead_email'] or 'N/A'}\n"
            f"📍 Source: {approval['lead_source']}\n"
            f"📅 Received: {approval['created_at']}\n"
            f"{score_section}\n"
            f"📨 PROPOSED INITIAL MESSAGE:\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"\"{approval['proposed_message']}\"\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Via: {channel_name}\n"
            f"To: {approval['lead_phone'] if approval['channel'] == 'sms' else approval['lead_email']}\n\n"
            f"✅ Reply \"yes\" to send as-is\n"
            f"✏️ Reply with custom text to send instead\n"
            f"❌ Reply \"skip\" to skip\n"
            f"🆔 Approval #{aid}"
        )

        buttons = {
            "inline_keyboard": [
                [
                    {"text": "✅ Approve", "callback_data": f"approve:{aid}"},
                    {"text": "❌ Skip", "callback_data": f"skip:{aid}"},
                ]
            ]
        }
        return text, buttons

    def format_followup_approval(self, approval: dict) -> tuple[str, dict]:
        """Format a follow-up approval notification."""
        aid = approval["id"]
        step = approval.get("followup_step", 0) + 1
        channel_name = "SMS" if approval["channel"] == "sms" else "Email"

        last_out = approval.get("last_outbound", "")
        last_in = approval.get("last_inbound", "")

        # Get lead score
        score_line = self._get_lead_score_line(approval.get("lead_id", 0))

        text = (
            f"🔄 FOLLOW-UP READY — Step {step}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 Name: {approval['lead_name']}\n"
            f"📱 Phone: {approval['lead_phone'] or 'N/A'}\n"
            f"📧 Email: {approval['lead_email'] or 'N/A'}\n"
            f"📍 Source: {approval['lead_source']}\n"
        )
        if score_line:
            text += f"{score_line}\n"
        if last_out:
            text += f"💬 Last Message Sent: \"{last_out[:200]}\"\n"
        if last_in:
            text += f"💬 Their Last Reply: \"{last_in[:200]}\"\n"

        text += (
            f"\n📨 PROPOSED FOLLOW-UP:\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"\"{approval['proposed_message']}\"\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Via: {channel_name}\n"
            f"To: {approval['lead_phone'] if approval['channel'] == 'sms' else approval['lead_email']}\n\n"
            f"✅ Reply \"yes\" to send\n"
            f"✏️ Reply with custom message\n"
            f"❌ Reply \"skip\" to skip this step\n"
            f"🛑 Reply \"stop\" to end sequence\n"
            f"🆔 Approval #{aid}"
        )

        buttons = {
            "inline_keyboard": [
                [
                    {"text": "✅ Approve", "callback_data": f"approve:{aid}"},
                    {"text": "❌ Skip", "callback_data": f"skip:{aid}"},
                ],
                [
                    {"text": "🛑 Stop Sequence", "callback_data": f"stop:{aid}"},
                ]
            ]
        }
        return text, buttons

    def format_lead_reply(self, lead_name: str, lead_phone: str, lead_email: str,
                          reply_text: str, our_last_message: str, channel: str,
                          lead_id: int) -> tuple[str, dict]:
        """Format a lead reply notification."""
        # Get lead score
        score_line = self._get_lead_score_line(lead_id)
        score_section = f"{score_line}\n\n" if score_line else ""

        text = (
            f"💬 LEAD REPLIED\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 Name: {lead_name}\n"
            f"📱 Phone: {lead_phone or 'N/A'}\n"
            f"📧 Email: {lead_email or 'N/A'}\n"
            f"{score_section}\n"
            f"📩 THEIR MESSAGE:\n"
            f"\"{reply_text[:500]}\"\n\n"
            f"📨 IN RESPONSE TO OUR MESSAGE:\n"
            f"\"{our_last_message[:300]}\"\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Reply here with your response and I'll send it via {channel.upper()}\n"
            f"Or reply \"call\" to get a reminder to call them\n"
            f"🆔 Lead #{lead_id}"
        )
        buttons = {
            "inline_keyboard": [
                [
                    {"text": "📞 Remind to Call", "callback_data": f"call:{lead_id}"},
                ]
            ]
        }
        return text, buttons

    def format_reminder(self, approval: dict, is_final: bool = False) -> str:
        """Format a reminder for an unapproved message."""
        prefix = "⏰ FINAL REMINDER" if is_final else "⏰ REMINDER"
        return (
            f"{prefix} — Pending Approval #{approval['id']}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 {approval['lead_name']} — {approval['lead_source']}\n"
            f"📨 {approval['channel'].upper()} waiting to send\n"
            f"⏱️ Queued: {approval['created_at']}\n\n"
            f"Reply \"yes\" to approve or \"skip\" to skip\n"
            f"🆔 Approval #{approval['id']}"
        )

    # ── Polling ───────────────────────────────────────────────────────────────

    async def start_polling(self):
        """Poll for both messages and callback queries."""
        self._running = True
        print("[TG] Bot polling started")
        try:
            from core.agent_runtime import AgentRuntime
            self._agent_rt = AgentRuntime("telegram-bot", "d0_core",
                                          launchd_label="com.zoar.telegram-bot")
        except Exception:
            self._agent_rt = None
        _poll_count = 0
        while self._running:
            _poll_count += 1
            if getattr(self, "_agent_rt", None) and _poll_count % 10 == 0:
                self._agent_rt.heartbeat(f"Polling (cycle {_poll_count})")
            try:
                async with httpx.AsyncClient(timeout=35) as c:
                    r = await c.get(
                        f"{self.base}/getUpdates",
                        params={"offset": self._offset, "timeout": 30, "limit": 10}
                    )
                    for upd in r.json().get("result", []):
                        self._offset = upd["update_id"] + 1
                        try:
                            # Handle callback queries (inline button presses)
                            if "callback_query" in upd:
                                await self._handle_callback(upd["callback_query"])
                                continue

                            # Handle text messages
                            msg = upd.get("message", {})
                            chat_id = str(msg.get("chat", {}).get("id", ""))
                            text = msg.get("text", "").strip()

                            if not text or (self.allowed and chat_id not in self.allowed):
                                continue

                            await self._handle_message(chat_id, text)
                        except Exception as e:
                            print(f"[TG] Update handler error: {e}")
                            traceback.print_exc()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[TG] Polling error: {e}")
                if self._agent_rt:
                    self._agent_rt.report_error(str(e), will_retry=True)
            _poll_count += 1
            if self._agent_rt and _poll_count % 10 == 0:
                self._agent_rt.heartbeat("Polling — %d cycles" % _poll_count)
            await asyncio.sleep(1)
        if self._agent_rt:
            self._agent_rt.stop()

    async def _handle_callback(self, callback):
        """Handle inline button presses."""
        query_id = callback.get("id")
        data = callback.get("data", "")
        chat_id = str(callback.get("message", {}).get("chat", {}).get("id", ""))

        if self.allowed and chat_id not in self.allowed:
            return

        # Answer the callback to remove the loading spinner
        async with httpx.AsyncClient(timeout=10) as c:
            try:
                await c.post(f"{self.base}/answerCallbackQuery",
                             json={"callback_query_id": query_id})
            except Exception:
                pass

        # Parse callback data: "action:id"
        parts = data.split(":", 1)
        if len(parts) != 2:
            return

        action, target_id = parts[0], parts[1]

        if action in ("approve", "skip", "stop"):
            try:
                aid = int(target_id)
            except ValueError:
                return
            if self._approval_handler:
                await self._approval_handler(chat_id, aid, action, None)

        elif action in ("b2b_approve", "b2b_deny"):
            try:
                lead_id = int(target_id)
            except ValueError:
                return
            if self._b2b_approval_handler:
                await self._b2b_approval_handler(chat_id, lead_id, action)

        elif action == "call":
            try:
                lead_id = int(target_id)
            except ValueError:
                return
            await self.send(chat_id, f"📞 Reminder set: Call lead #{lead_id} now!")

        elif action == "campaign_approve":
            try:
                cid = int(target_id)
                from core.email_campaign import approve_campaign
                result = approve_campaign(cid)
                if result.get("activated"):
                    await self.send(chat_id,
                        f"Campaign #{cid} ACTIVATED\n"
                        f"Target: {result.get('total_target', 0)} vendors\n"
                        f"Drip sending begins now (30/day warm-up)")
                else:
                    await self.send(chat_id, f"Campaign #{cid}: {result.get('error', 'unknown error')}")
            except Exception as e:
                await self.send(chat_id, f"Campaign approve error: {e}")

        elif action == "campaign_pause":
            try:
                cid = int(target_id)
                from core.email_campaign import pause_campaign
                result = pause_campaign(cid)
                await self.send(chat_id, f"Campaign #{cid} PAUSED. {result.get('sent_count', 0)} sent so far.")
            except Exception as e:
                await self.send(chat_id, f"Campaign pause error: {e}")

    async def _handle_message(self, chat_id: str, text: str):
        """Handle incoming text messages."""
        text_lower = text.lower().strip()
        # Quick objection helper: "objection: too expensive for wedding"
        if text_lower.startswith("objection:") or text_lower.startswith("objection "):
            objection_text = text.split(":", 1)[1].strip() if ":" in text else text[len("objection "):].strip()
            try:
                from core.specialist_router import get_specialist_router
                res = get_specialist_router().route(
                    "objection_resolver",
                    {"objection": objection_text, "raw_text": text},
                )
                responses = (res or {}).get("responses") or []
                if isinstance(responses, list) and responses:
                    preview = "\n".join([f"{i+1}. {r}" for i, r in enumerate(responses[:5])])
                    await self.send(chat_id, f"🛠️ *Objection Responses*\n\n{preview}")
                else:
                    await self.send(chat_id, "I couldn't generate objection responses right now.")
            except Exception as e:
                await self.send(chat_id, f"Objection helper error: {e}")
            return

        # Strip @bot_username suffix from commands (Telegram appends it)
        if text_lower.startswith("/") and "@" in text_lower.split()[0]:
            first_word = text_lower.split()[0]
            text_lower = first_word.split("@")[0] + text_lower[len(first_word):]
            text = text.split()[0].split("@")[0] + text[len(text.split()[0]):]

        # ── Commands ──
        if text_lower in ("/start", "/help"):
            await self.send(chat_id,
                "🤖 *Nexus* online!\n\n"
                "*Core:*\n"
                "/today — Morning briefing\n"
                "/queue — Pending approvals\n"
                "/broadcast [msg] — Push to all agents\n"
                "/assign [task] to [agent] — Manual assignment\n"
                "/leads — Active leads\n"
                "/lead [id] — Lead detail\n"
                "/stats — Weekly stats\n"
                "/services — Service health\n"
                "/pipeline — Booking pipeline\n\n"
                "*Bookings:*\n"
                "/avail [date] — Check availability\n"
                "/book [lead] [date] [loc] — Create booking\n"
                "/bookings — Upcoming bookings\n"
                "/confirm [lead] [date] [price] — Confirm\n"
                "/deposit [id] [amt] — Mark deposit\n"
                "/complete [id] — Mark done\n\n"
                "*Analytics:*\n"
                "/analytics — Full dashboard\n"
                "/revenue — Revenue report\n"
                "/funnel — Conversion funnel\n"
                "/capi — CAPI event stats\n\n"
                "*Tools:*\n"
                "/quote [lead] [date] [loc] [price]\n"
                "/ads — Ad performance\n"
                "/review [lead] — Request review\n"
                "/reviewstats — Review metrics\n"
                "/gbp — Google Business posts\n"
                "/competitors — Competitor intel\n"
                "/alerts — Competitor alerts\n"
                "/seo — SEO status & generate\n"
                "/msg — Messenger conversations\n"
                "/retarget — Audience sync\n"
                "/reengage — Cold lead revival\n"
                "/media — Photo/video library\n"
                "/posting — Listing status\n"
                "/post_preview — Preview next post\n"
                "/hold [platform] — Pause posting\n"
                "/video — Video pipeline\n"
                "/scrape — Group scraper stats\n"
                "/groups — Monitored groups\n"
                "/digest — Intelligence digest\n"
                "/browse [url] — Web research\n"
                "/tasks — Task queue\n"
                "/do [request] — Queue a task\n"
                "/test_notification — Test all channels\n"
                "/note [text] — Save a note\n"
                "/know [topic] — Search brain\n"
                "/todo — View to-do list\n"
                "/remind [text] — Set reminder\n"
                "/emailstats — Email parser stats\n"
                "/watchdog — System health\n\n"
                "*Build Pipeline:*\n"
                "/build [desc] — Start build (free models)\n"
                "/claude\\_build [id] — Upgrade build to Claude ($)\n"
                "/builds — List recent builds\n\n"
                "*B2B Outreach:*\n"
                "/b2b\\_status — Outreach stats\n"
                "/generate\\_batch — Generate approval batch\n"
                "/b2b\\_lead [id] — Lead detail\n"
                "/b2b\\_search [term] — Search leads\n\n"
                "*FB Group Scraper:*\n"
                "/fb\\_scrape — Trigger manual scrape\n"
                "/fb\\_stop — Stop current scrape\n"
                "/fb\\_prospects — Prospect summary\n"
                "/fb\\_today — Today's finds\n"
                "/fb\\_lead [id] — Prospect detail\n"
                "/fb\\_status [id] [status] — Update status\n"
                "/fb\\_note [id] [text] — Add note\n"
                "/fb\\_groups — Active groups\n"
                "/fb\\_add\\_group [url] — Add group\n"
                "/fb\\_remove\\_group [url] — Remove group\n\n"
                "*Security:*\n"
                "/security — Security gate status\n"
                "/set\\_mode [mode] — Change messaging mode\n"
                "/kill — Emergency stop all sends\n"
                "/unkill — Remove kill switch\n"
                "/confirm\\_alive — Dead man's switch\n"
                "/outbound\\_status — Recent sends\n"
                "/outbound\\_log [n] — Outbound log detail\n"
                "/audit — Verify hash chain\n"
                "/dlp\\_test [text] — Test DLP scanner\n\n"
                "*Approval:*\n"
                "• \"yes\" or ✅ — approve\n"
                "• \"skip\" — skip • \"stop\" — cancel\n"
                "• Type text — send custom message\n\n"
                "💡 Or just type naturally — AI routes it!"
            )
            return

        # ── Telegram Command Center (additive) ──
        if text_lower == "/status":
            await self._cc_status(chat_id)
            return

        if text_lower == "/leads":
            await self._cc_leads(chat_id)
            return

        if text_lower == "/emails":
            await self._cc_emails(chat_id)
            return

        if text_lower == "/health":
            await self._cc_health(chat_id)
            return

        if text_lower.startswith("/find"):
            await self._cc_find(chat_id, text)
            return

        if text_lower.startswith("/send"):
            await self._cc_send(chat_id, text)
            return

        if text_lower.startswith("/ads"):
            await self._cc_ads(chat_id, text)
            return

        if text_lower.startswith("/broadcast"):
            await self._cc_broadcast(chat_id, text)
            return

        if text_lower.startswith("/assign"):
            await self._cc_assign(chat_id, text)
            return

        # Route all /scrape commands into fleet_task_queue (additive replacement
        # of raw subprocess-style scrape triggers).
        if text_lower.startswith("/scrape"):
            await self._cc_scrape(chat_id, text)
            return

        # /stop exact command is global emergency stop.
        # Keep /stop <lead_id> approval-sequence behavior lower in the file.
        if text_lower == "/stop":
            await self._cc_stop(chat_id)
            return

        if text_lower == "/resume":
            await self._cc_resume(chat_id)
            return

        if text_lower == "/queue":
            await self._show_queue(chat_id)
            return

        if text_lower.startswith("/lead "):
            await self._show_lead_detail(chat_id, text_lower)
            return

        if text_lower == "/today":
            await self._show_today_briefing(chat_id)
            return

        if text_lower == "/stats":
            await self._show_stats(chat_id)
            return

        if text_lower == "/services":
            await self._show_services(chat_id)
            return

        if text_lower == "/mode":
            await self._toggle_mode(chat_id)
            return

        if text_lower == "/briefing":
            await self.send(chat_id, "📋 Generating today's posting briefing...")
            try:
                from core.daily_posting import generate_and_send_briefing
                await generate_and_send_briefing()
            except Exception as e:
                await self.send(chat_id, f"❌ Briefing error: {e}")
            return

        if text_lower == "auto":
            # Auto-post today's FB Page organic post
            await self.send(chat_id, "📘 Auto-posting to FB Page...")
            try:
                from core.daily_posting import auto_post_to_fb_page
                result = await auto_post_to_fb_page()
                if result.get("ok"):
                    await self.send(chat_id, f"✅ Posted! ID: {result['post_id']}")
                else:
                    await self.send(chat_id, f"❌ {result.get('error', 'Unknown error')}")
            except Exception as e:
                await self.send(chat_id, f"❌ Auto-post error: {e}")
            return

        # ── Security Commands (Phase 1H) ──

        if text_lower == "/security":
            await self._show_security_status(chat_id)
            return

        if text_lower.startswith("/set_mode "):
            mode = text_lower.split(" ", 1)[1].strip()
            if mode not in ("disabled", "test", "production"):
                await self.send(chat_id, "❌ Mode must be: disabled, test, or production")
                return
            try:
                from core.security import get_security_gate
                gate = get_security_gate()
                gate.set_mode(mode, "kai_telegram")
                await self.send(chat_id, f"✅ Messaging mode set to: *{mode}*")
            except Exception as e:
                await self.send(chat_id, f"❌ Set mode error: {e}")
            return

        if text_lower == "/kill":
            try:
                from core.security import get_security_gate
                gate = get_security_gate()
                gate.activate_kill_switch("kai_telegram")
                await self.send(chat_id, "🛑 KILL SWITCH ACTIVATED\nAll outbound messages are now blocked.")
            except Exception as e:
                await self.send(chat_id, f"❌ Kill switch error: {e}")
            return

        if text_lower == "/unkill":
            try:
                from core.security import get_security_gate
                gate = get_security_gate()
                gate.deactivate_kill_switch()
                await self.send(chat_id, "✅ Kill switch deactivated")
            except Exception as e:
                await self.send(chat_id, f"❌ Unkill error: {e}")
            return

        if text_lower == "/confirm_alive":
            try:
                from core.security import get_security_gate
                gate = get_security_gate()
                gate.confirm_alive("kai_telegram")
                await self.send(chat_id, "✅ Dead man's switch confirmed — valid for 4 hours")
            except Exception as e:
                await self.send(chat_id, f"❌ Confirm alive error: {e}")
            return

        if text_lower == "/outbound_status":
            await self._show_outbound_status(chat_id)
            return

        if text_lower.startswith("/outbound_log"):
            parts = text_lower.split()
            n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 10
            await self._show_outbound_log(chat_id, n)
            return

        if text_lower == "/audit":
            await self._show_audit(chat_id)
            return

        if text_lower.startswith("/dlp_test "):
            test_text = text[len("/dlp_test "):]
            try:
                from core.security import get_security_gate
                gate = get_security_gate()
                violations = gate.scan_for_secrets(test_text)
                if violations:
                    await self.send(chat_id, f"🚨 DLP VIOLATIONS:\n" + "\n".join(f"• {v}" for v in violations))
                else:
                    await self.send(chat_id, "✅ No DLP violations detected")
            except Exception as e:
                await self.send(chat_id, f"❌ DLP test error: {e}")
            return

        if text_lower == "/campaign_status" or text_lower == "/campaigns":
            try:
                from core.email_campaign import list_campaigns
                campaigns = list_campaigns()
                if not campaigns:
                    await self.send(chat_id, "No email campaigns found.")
                    return
                lines = ["EMAIL CAMPAIGNS\n"]
                for c in campaigns[:5]:
                    lines.append(
                        f"#{c['id']} {c['name']}\n"
                        f"  Status: {c['status']} | Sent: {c['sent_count']}/{c['total_target']}\n"
                        f"  Bounced: {c['bounce_count']} | Replies: {c['reply_count']}"
                    )
                await self.send(chat_id, "\n".join(lines))
            except Exception as e:
                await self.send(chat_id, f"Campaign status error: {e}")
            return

        # ── Ad System & Lead Management Commands (Session 6) ──

        if text_lower == "/leads":
            try:
                from core.lead_scoring import LeadScorer
                scorer = LeadScorer()
                leads = scorer.get_leads_by_tier()
                if not leads:
                    await self.send(chat_id, "No active leads found.")
                    return
                lines = ["📊 *Active Leads by Score*\n"]
                for l in leads[:15]:
                    tier = "🔥" if l.get("lead_score",0) >= 80 else "🟡" if l.get("lead_score",0) >= 60 else "🟠" if l.get("lead_score",0) >= 40 else "❄️"
                    name = l.get("full_name") or l.get("first_name") or "Unknown"
                    lines.append(f"{tier} #{l['id']} {name} — Score: {l.get('lead_score',0)} | {l.get('booking_status','new')}")
                await self.send(chat_id, "\n".join(lines))
            except Exception as e:
                await self.send(chat_id, f"❌ Leads error: {e}")
            return

        if text_lower.startswith("/lead "):
            try:
                lid = int(text_lower.split()[1])
                from core.lead_scoring import LeadScorer
                result = LeadScorer().score_lead(lid)
                import sqlite3
                conn = sqlite3.connect(str(Path.home() / ".nexus" / "memory.db"))
                conn.row_factory = sqlite3.Row
                lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lid,)).fetchone()
                conn.close()
                if not lead:
                    await self.send(chat_id, f"Lead #{lid} not found.")
                    return
                name = lead["full_name"] or lead["first_name"] or "Unknown"
                msg = (f"👤 *Lead #{lid}: {name}*\n"
                       f"Phone: {lead.get('phone','N/A')}\n"
                       f"Email: {lead.get('email','N/A')}\n"
                       f"Source: {lead.get('source','N/A')}\n"
                       f"Status: {lead.get('status','N/A')} | {lead.get('booking_status','N/A')}\n"
                       f"Score: {result.get('score',0)} ({result.get('tier','?')})\n"
                       f"Added: {lead.get('date_added','N/A')}\n"
                       f"Last Contact: {lead.get('last_contacted_at','never')}\n"
                       f"Follow-ups: {lead.get('follow_up_count',0)}")
                await self.send(chat_id, msg)
            except Exception as e:
                await self.send(chat_id, f"❌ Lead detail error: {e}")
            return

        if text_lower.startswith("/qualify "):
            try:
                lid = int(text_lower.split()[1])
                import sqlite3
                conn = sqlite3.connect(str(Path.home() / ".nexus" / "memory.db"))
                conn.row_factory = sqlite3.Row
                lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lid,)).fetchone()
                if not lead:
                    await self.send(chat_id, f"Lead #{lid} not found."); conn.close(); return
                conn.execute("UPDATE leads SET booking_status = 'qualified' WHERE id = ?", (lid,))
                conn.commit(); conn.close()
                # Send CAPI QualifiedLead event
                try:
                    from integrations.meta_capi_events import send_qualified_lead_event
                    import asyncio
                    await send_qualified_lead_event(dict(lead))
                except Exception as ce:
                    print(f"[Bot] CAPI qualify error: {ce}")
                name = lead["full_name"] or lead["first_name"] or "Unknown"
                await self.send(chat_id, f"✅ Lead #{lid} ({name}) marked as *qualified*\nCAPI QualifiedLead event sent to Meta")
            except Exception as e:
                await self.send(chat_id, f"❌ Qualify error: {e}")
            return

        if text_lower.startswith("/book "):
            try:
                parts = text_lower.split()
                lid = int(parts[1])
                amount = float(parts[2]) if len(parts) > 2 else 1100.00
                import sqlite3
                conn = sqlite3.connect(str(Path.home() / ".nexus" / "memory.db"))
                conn.row_factory = sqlite3.Row
                lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lid,)).fetchone()
                if not lead:
                    await self.send(chat_id, f"Lead #{lid} not found."); conn.close(); return
                conn.execute("UPDATE leads SET booking_status = 'booked' WHERE id = ?", (lid,))
                conn.commit(); conn.close()
                try:
                    from integrations.meta_capi_events import send_lifecycle_purchase_event
                    await send_lifecycle_purchase_event(dict(lead), amount)
                except Exception as ce:
                    print(f"[Bot] CAPI purchase error: {ce}")
                name = lead["full_name"] or lead["first_name"] or "Unknown"
                await self.send(chat_id, f"🎉 Lead #{lid} ({name}) marked as *BOOKED* (${amount:,.0f})\nCAPI Purchase event sent to Meta")
            except Exception as e:
                await self.send(chat_id, f"❌ Book error: {e}")
            return

        if text_lower.startswith("/deposit "):
            try:
                parts = text_lower.split()
                lid = int(parts[1])
                amount = float(parts[2]) if len(parts) > 2 else 250.00
                import sqlite3
                conn = sqlite3.connect(str(Path.home() / ".nexus" / "memory.db"))
                conn.row_factory = sqlite3.Row
                lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lid,)).fetchone()
                if not lead:
                    await self.send(chat_id, f"Lead #{lid} not found."); conn.close(); return
                conn.execute("UPDATE leads SET booking_status = 'deposit_paid' WHERE id = ?", (lid,))
                conn.commit(); conn.close()
                try:
                    from integrations.meta_capi_events import send_deposit_event
                    await send_deposit_event(dict(lead), amount)
                except Exception as ce:
                    print(f"[Bot] CAPI deposit error: {ce}")
                name = lead["full_name"] or lead["first_name"] or "Unknown"
                await self.send(chat_id, f"💰 Lead #{lid} ({name}) — deposit *${amount:,.0f}* recorded\nCAPI InitiateCheckout event sent to Meta")
            except Exception as e:
                await self.send(chat_id, f"❌ Deposit error: {e}")
            return

        if text_lower.startswith("/score "):
            try:
                lid = int(text_lower.split()[1])
                from core.lead_scoring import LeadScorer
                result = LeadScorer().score_lead(lid)
                breakdown = result.get("breakdown", [])
                lines = [f"📊 *Score for Lead #{lid}: {result.get('score',0)}* ({result.get('tier','?')})\n"]
                for item in breakdown:
                    sign = "+" if item.get("points", 0) > 0 else ""
                    lines.append(f"  {sign}{item['points']} — {item['rule']}")
                await self.send(chat_id, "\n".join(lines))
            except Exception as e:
                await self.send(chat_id, f"❌ Score error: {e}")
            return

        if text_lower.startswith("/followup "):
            try:
                lid = int(text_lower.split()[1])
                from core.follow_up_engine import FollowUpEngine
                result = FollowUpEngine().get_sequence_status(lid)
                if not result or result.get("error"):
                    await self.send(chat_id, f"No follow-up sequence for lead #{lid}")
                    return
                msg = (f"📋 *Follow-up for Lead #{lid}*\n"
                       f"Sequence: {result.get('sequence_type','N/A')}\n"
                       f"Step: {result.get('step','?')}/{result.get('total_steps','?')}\n"
                       f"Status: {result.get('status','N/A')}\n"
                       f"Next send: {result.get('next_send_at','N/A')}\n"
                       f"Last sent: {result.get('last_sent_at','never')}")
                await self.send(chat_id, msg)
            except Exception as e:
                await self.send(chat_id, f"❌ Follow-up error: {e}")
            return

        if text_lower.startswith("/pause_followup "):
            try:
                lid = int(text_lower.split()[1])
                from core.follow_up_engine import FollowUpEngine
                result = FollowUpEngine().pause_sequence(lid, "kai_telegram")
                await self.send(chat_id, f"⏸️ Follow-up paused for lead #{lid}")
            except Exception as e:
                await self.send(chat_id, f"❌ Pause error: {e}")
            return

        if text_lower.startswith("/resume_followup "):
            try:
                lid = int(text_lower.split()[1])
                from core.follow_up_engine import FollowUpEngine
                result = FollowUpEngine().resume_sequence(lid)
                await self.send(chat_id, f"▶️ Follow-up resumed for lead #{lid}")
            except Exception as e:
                await self.send(chat_id, f"❌ Resume error: {e}")
            return

        if text_lower == "/ads":
            try:
                from core.ad_engine import AdEngine
                engine = AdEngine()
                creatives = engine.get_active_creatives()
                if not creatives:
                    await self.send(chat_id, "No active ad creatives.")
                    return
                lines = ["📊 *Active Ad Creatives*\n"]
                for c in creatives[:10]:
                    status_icon = {"testing": "🧪", "active": "▶️", "winner": "🏆"}.get(c.get("status",""), "📋")
                    cpl = f"${c['cpl']:.2f}" if c.get('cpl') else "N/A"
                    lines.append(f"{status_icon} #{c['id']} {c['name']} — CPL: {cpl} | Leads: {c.get('leads',0)}")
                await self.send(chat_id, "\n".join(lines))
            except Exception as e:
                await self.send(chat_id, f"❌ Ads error: {e}")
            return

        if text_lower == "/ad_report":
            try:
                from core.ad_engine import AdEngine
                report = AdEngine().generate_performance_report()
                await self.send(chat_id, f"```\n{report[:3800]}\n```")
            except Exception as e:
                await self.send(chat_id, f"❌ Ad report error: {e}")
            return

        if text_lower == "/fatigue_check":
            try:
                from core.ad_engine import AdEngine
                engine = AdEngine()
                creatives = engine.get_active_creatives()
                fatigued = []
                for c in creatives:
                    check = engine.check_fatigue(c["id"])
                    if check.get("fatigued"):
                        fatigued.append((c, check))
                if not fatigued:
                    await self.send(chat_id, "✅ No fatigued creatives detected!")
                    return
                lines = ["⚠️ *Fatigued Creatives*\n"]
                for c, check in fatigued:
                    lines.append(f"• #{c['id']} {c['name']}: {', '.join(check.get('reasons',[]))}")
                await self.send(chat_id, "\n".join(lines))
            except Exception as e:
                await self.send(chat_id, f"❌ Fatigue check error: {e}")
            return

        if text_lower == "/capi_status":
            try:
                from integrations.meta_capi_events import get_capi_status
                status = get_capi_status()
                lines = ["📡 *CAPI Status*\n"]
                lines.append(f"Total events: {status.get('total_lifecycle_events',0)}")
                lines.append(f"Success rate: {status.get('success_rate','N/A')}")
                by_type = status.get("by_type", {})
                for evt, data in by_type.items():
                    lines.append(f"  • {evt}: {data.get('count',0)} sent")
                await self.send(chat_id, "\n".join(lines))
            except Exception as e:
                await self.send(chat_id, f"❌ CAPI status error: {e}")
            return

        if text_lower == "/post_now":
            try:
                await self.send(chat_id, "Generating daily posts and sending to email...")
                from core.daily_posting import send_posting_emails
                result = await send_posting_emails()
                if result["ok"]:
                    await self.send(chat_id,
                        f"✅ {result['sent']}/{result['total']} posting emails sent to zoarbathrooms@gmail.com\n"
                        f"Check inbox for copy-paste content")
                else:
                    await self.send(chat_id, f"❌ Email delivery failed: {result.get('error', 'unknown')}")
            except Exception as e:
                await self.send(chat_id, f"❌ Post now error: {e}")
            return

        if text_lower.startswith("/copy "):
            try:
                audience = text_lower.split(None, 1)[1].strip()
                from core.ad_engine import AdEngine
                engine = AdEngine()
                copies = engine.get_copy_for_audience(audience, "primary_text")
                if not copies:
                    await self.send(chat_id, f"No copy found for audience: {audience}")
                    return
                import random
                selected = random.sample(copies, min(3, len(copies)))
                lines = [f"✍️ *Ad Copy for {audience}*\n"]
                for i, c in enumerate(selected, 1):
                    lines.append(f"*{i}.* {c['content'][:300]}\n")
                await self.send(chat_id, "\n".join(lines))
            except Exception as e:
                await self.send(chat_id, f"❌ Copy error: {e}")
            return

        # ── Personal Assistant Commands (Phase 10) ──
        if text_lower.startswith(("/todo", "/add", "/done", "/delete", "/remind",
                                   "/gym", "/supplement", "/meal", "/teeth", "/sleep",
                                   "/health", "/summary", "/dailyreminders",
                                   "/togglereminder", "/cancelreminder")):
            try:
                from core.personal_assistant import handle_pa_command
                response = handle_pa_command(text)
                if response:
                    await self.send(chat_id, response)
                    return
            except Exception as e:
                await self.send(chat_id, f"❌ PA error: {e}")
                return

        # ── Knowledge Engine Commands (Phase 13) ──
        if text_lower.startswith("/note "):
            try:
                from core.knowledge_engine import handle_note_command
                response = handle_note_command(text[6:].strip())
                await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Note error: {e}")
            return

        if text_lower == "/learn":
            try:
                from core.knowledge_engine import handle_learn_command
                await self.send(chat_id, handle_learn_command())
            except Exception as e:
                await self.send(chat_id, f"❌ Learn error: {e}")
            return

        if text_lower == "/insights":
            try:
                from core.knowledge_engine import handle_insights_command
                await self.send(chat_id, handle_insights_command())
            except Exception as e:
                await self.send(chat_id, f"❌ Insights error: {e}")
            return

        if text_lower.startswith("/know "):
            try:
                from core.knowledge_engine import handle_know_command
                await self.send(chat_id, handle_know_command(text[6:].strip()))
            except Exception as e:
                await self.send(chat_id, f"❌ Know error: {e}")
            return

        if text_lower.startswith("/forget "):
            try:
                from core.knowledge_engine import handle_forget_command
                await self.send(chat_id, handle_forget_command(text[8:].strip()))
            except Exception as e:
                await self.send(chat_id, f"❌ Forget error: {e}")
            return

        # ── Quote/Invoice Commands (Phase 7/11) ──
        if text_lower.startswith("/quote"):
            try:
                from core.quote_generator import parse_quote_command, create_quote, format_quote_for_telegram
                if text_lower.strip() == "/quote":
                    await self.send(chat_id,
                        "📋 *Quote Generator*\n\n"
                        "Usage:\n"
                        "`/quote [lead_id] [date] [location] [price]`\n"
                        "`/quote lead_id=15 price=1200`\n\n"
                        "Example:\n"
                        "`/quote 15 june15 thousand-oaks 1100`"
                    )
                    return
                params = parse_quote_command(text)
                # If lead_id provided, fetch lead data
                lead_data = None
                if params.get("lead_id"):
                    try:
                        import sqlite3
                        conn = sqlite3.connect(str(Path.home() / ".nexus" / "memory.db"))
                        conn.row_factory = sqlite3.Row
                        row = conn.execute("SELECT * FROM leads WHERE id = ?", (params["lead_id"],)).fetchone()
                        conn.close()
                        if row:
                            lead_data = dict(row)
                    except Exception:
                        pass
                result = create_quote(
                    client_name=params.get("client_name", lead_data.get("first_name", "") + " " + lead_data.get("last_name", "") if lead_data else ""),
                    event_type=params.get("event_type", lead_data.get("event_type", "") if lead_data else ""),
                    event_date=params.get("event_date", lead_data.get("event_date", "") if lead_data else ""),
                    event_location=params.get("location", lead_data.get("city", "") if lead_data else ""),
                    guest_count=params.get("guest_count", 0),
                    price=params.get("price", 1100.0),
                    client_email=lead_data.get("email", "") if lead_data else "",
                    client_phone=lead_data.get("phone", "") if lead_data else "",
                    lead_id=params.get("lead_id"),
                )
                if result.get("ok"):
                    # Send quote text preview
                    tg_text = format_quote_for_telegram(result)
                    await self.send(chat_id, tg_text)
                    # Send PDF if available
                    pdf_path = result.get("pdf_path", "")
                    if pdf_path and Path(pdf_path).exists():
                        await self.send_document(chat_id, pdf_path, f"Quote {result['quote_number']}")
                else:
                    await self.send(chat_id, f"❌ Quote error: {result.get('error', 'Unknown')}")
            except Exception as e:
                await self.send(chat_id, f"❌ Quote error: {e}")
            return

        # ── Ad Performance Commands (Phase 15) ──
        if text_lower.startswith("/ads"):
            try:
                from core.ad_monitor import handle_ads_query
                query = text[4:].strip() if len(text) > 4 else "how are the ads doing"
                response = await handle_ads_query(query)
                await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Ads error: {e}")
            return

        # ── Watchdog Services (Phase 8) ──
        if text_lower == "/watchdog":
            try:
                from core.watchdog import format_services_status
                await self.send(chat_id, format_services_status())
            except Exception as e:
                await self.send(chat_id, f"❌ Watchdog error: {e}")
            return

        # ── Google Calendar / Availability Commands ──
        if text_lower.startswith(("/avail", "/book ", "/bookings", "/cancel ")):
            try:
                from integrations.google_calendar import (
                    handle_availability_command, handle_book_command,
                    handle_bookings_command, handle_cancel_command,
                )
                if text_lower.startswith("/avail"):
                    response = await handle_availability_command(text[6:].strip())
                elif text_lower.startswith("/book "):
                    response = await handle_book_command(text[6:].strip())
                elif text_lower == "/bookings":
                    response = await handle_bookings_command()
                elif text_lower.startswith("/cancel "):
                    response = await handle_cancel_command(text[8:].strip())
                else:
                    response = "Usage: /avail [date], /book [lead_id] [date] [location], /bookings, /cancel [id]"
                await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Calendar error: {e}")
            return

        # ── Booking Confirmation System ──
        if text_lower.startswith(("/confirm ", "/deposit ", "/pipeline", "/complete ")):
            try:
                from core.booking_system import (
                    handle_confirm_command, handle_deposit_command,
                    handle_pipeline_command, handle_complete_command,
                )
                if text_lower.startswith("/confirm "):
                    response = handle_confirm_command(text[9:].strip())
                elif text_lower.startswith("/deposit "):
                    response = handle_deposit_command(text[9:].strip())
                elif text_lower.startswith("/pipeline"):
                    response = handle_pipeline_command()
                elif text_lower.startswith("/complete "):
                    response = handle_complete_command(text[10:].strip())
                else:
                    response = "Usage: /confirm [lead] [date] [price], /deposit [id] [amt], /pipeline, /complete [id]"
                await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Booking error: {e}")
            return

        # ── Review System Commands ──
        if text_lower.startswith(("/review ", "/reviewed ", "/reviewstats", "/reviewtemplates")):
            try:
                from core.review_system import (
                    handle_review_command, handle_reviewed_command,
                    handle_review_stats_command, handle_review_templates_command,
                )
                if text_lower.startswith("/reviewed "):
                    response = handle_reviewed_command(text[10:].strip())
                elif text_lower.startswith("/review "):
                    response = handle_review_command(text[8:].strip())
                elif text_lower.startswith("/reviewstats"):
                    response = handle_review_stats_command()
                elif text_lower.startswith("/reviewtemplates"):
                    response = handle_review_templates_command()
                else:
                    response = "Usage: /review [lead_id], /reviewed [lead_id] [rating] [platform], /reviewstats"
                await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Review error: {e}")
            return

        # ── Email Parser Stats ──
        if text_lower == "/emailstats":
            try:
                from integrations.email_lead_parser import format_parser_stats
                await self.send(chat_id, format_parser_stats())
            except Exception as e:
                await self.send(chat_id, f"❌ Email stats error: {e}")
            return

        # ── Analytics Dashboard Commands ──
        if text_lower.startswith(("/analytics", "/revenue", "/funnel")):
            try:
                from core.analytics_dashboard import (
                    handle_analytics_command, handle_revenue_command, handle_funnel_command,
                )
                if text_lower.startswith("/analytics"):
                    response = handle_analytics_command(text)
                elif text_lower.startswith("/revenue"):
                    response = handle_revenue_command(text)
                elif text_lower.startswith("/funnel"):
                    response = handle_funnel_command()
                else:
                    response = "Usage: /analytics, /revenue [days], /funnel"
                await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Analytics error: {e}")
            return

        # ── CAPI Event Stats ──
        if text_lower == "/capi":
            try:
                from integrations.meta_capi_events import get_event_stats
                stats = get_event_stats(30)
                lines = ["📡 CAPI EVENT STATS (30d)", "━" * 28]
                lines.append(f"Total events: {stats.get('total', 0)}")
                breakdown = stats.get("breakdown", {})
                if breakdown:
                    for evt, cnt in sorted(breakdown.items(), key=lambda x: x[1], reverse=True):
                        lines.append(f"  {evt}: {cnt}")
                else:
                    lines.append("  No events recorded yet")
                await self.send(chat_id, "\n".join(lines))
            except Exception as e:
                await self.send(chat_id, f"❌ CAPI stats error: {e}")
            return

        # ── Google Business Profile Commands ──
        if text_lower.startswith("/gbp"):
            try:
                from integrations.google_business import handle_gbp_command
                handled = await handle_gbp_command(text, lambda msg: self.send(chat_id, msg))
                if not handled:
                    await self.send(chat_id, "Usage: /gbp stats, /gbp preview, /gbp post, /gbp drafts")
            except Exception as e:
                await self.send(chat_id, f"❌ GBP error: {e}")
            return

        # ── Competitor Monitor Commands ──
        if text_lower.startswith(("/competitors", "/competitor")):
            try:
                from core.competitor_monitor import (
                    handle_competitors_command, handle_competitor_add_command,
                    format_competitor_summary,
                )
                if "add " in text_lower:
                    response = handle_competitor_add_command(text)
                else:
                    response = handle_competitors_command(text)
                await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Competitor error: {e}")
            return

        if text_lower.startswith("/alerts"):
            try:
                from core.competitor_monitor import handle_competitor_alerts_command
                response = handle_competitor_alerts_command(text)
                await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Alerts error: {e}")
            return

        # ── Facebook Messenger Commands ──
        if text_lower.startswith("/msg"):
            try:
                from integrations.fb_messenger import (
                    handle_msg_command, handle_msg_lead_command,
                    handle_conversations_command, format_conversations,
                )
                if text_lower.startswith("/msglead"):
                    response = handle_msg_lead_command(text)
                    await self.send(chat_id, response)
                elif text_lower == "/msg" or text_lower == "/messages":
                    await self.send(chat_id, format_conversations())
                else:
                    response, psid = handle_msg_command(text)
                    await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Messenger error: {e}")
            return

        if text_lower.startswith("/conversations"):
            try:
                from integrations.fb_messenger import format_conversations
                await self.send(chat_id, format_conversations())
            except Exception as e:
                await self.send(chat_id, f"❌ Conversations error: {e}")
            return

        # ── SEO Tools Commands ──
        if text_lower.startswith("/seo"):
            try:
                from core.seo_tools import handle_seo_command, handle_seo_generate_command
                if any(x in text_lower for x in ("generate", "sitemap", "stats")):
                    response = handle_seo_generate_command(text)
                else:
                    response = handle_seo_command(text)
                await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ SEO error: {e}")
            return

        # ── Retargeting Audience Commands ──
        if text_lower.startswith("/retarget"):
            try:
                from core.retargeting import (
                    handle_retarget_command, handle_retarget_sync_command,
                    handle_retarget_create_command,
                )
                if "sync" in text_lower:
                    response = await handle_retarget_sync_command(
                        text, send_fn=lambda msg: self.send(chat_id, msg))
                    await self.send(chat_id, response)
                elif "create" in text_lower:
                    response = await handle_retarget_create_command(text)
                    await self.send(chat_id, response)
                else:
                    response = handle_retarget_command(text)
                    await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Retargeting error: {e}")
            return

        # ── Re-engagement Commands ──
        if text_lower.startswith("/reengage"):
            try:
                from core.reengagement import (
                    handle_reengage_command, handle_reengage_start_command,
                    handle_reengage_queue_command, handle_reengage_pause_command,
                    handle_reengage_resume_command,
                )
                if "start" in text_lower:
                    response = handle_reengage_start_command(text)
                elif "queue" in text_lower:
                    response = handle_reengage_queue_command(text)
                elif "pause" in text_lower:
                    response = handle_reengage_pause_command(text)
                elif "resume" in text_lower:
                    response = handle_reengage_resume_command(text)
                else:
                    response = handle_reengage_command(text)
                await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Re-engagement error: {e}")
            return

        # ── Media Manager Commands ──
        if text_lower.startswith("/media"):
            try:
                from core.media_manager import (
                    handle_media_command, handle_media_search_command,
                    handle_media_tag_command,
                )
                if "search" in text_lower:
                    response = handle_media_search_command(text)
                elif "tag" in text_lower:
                    response = handle_media_tag_command(text)
                else:
                    response = handle_media_command(text)
                await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Media error: {e}")
            return

        # ── Posting Engine Commands (Build 3) ──
        if text_lower.startswith("/posting"):
            try:
                from integrations.posting_engine import handle_posting_command
                response = handle_posting_command(text)
                await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Posting error: {e}")
            return

        if text_lower.startswith("/post_preview"):
            try:
                from integrations.posting_engine import handle_post_preview_command
                response = handle_post_preview_command(text)
                await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Preview error: {e}")
            return

        if text_lower.startswith("/hold"):
            try:
                from integrations.posting_engine import handle_post_hold_command
                response = handle_post_hold_command(text)
                await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Hold error: {e}")
            return

        # ── Video Generator Commands (Build 4) ──
        if text_lower.startswith("/video"):
            try:
                if text_lower.startswith("/video_status"):
                    from integrations.video_generator import handle_video_status_command
                    response = handle_video_status_command(text)
                else:
                    from integrations.video_generator import handle_video_command
                    response = handle_video_command(text)
                await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Video error: {e}")
            return

        # ── FB Group Scraper Commands (Build 7) ──
        if text_lower.startswith("/scrape"):
            try:
                from integrations.fb_group_scraper import handle_scrape_command
                response = handle_scrape_command(text)
                await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Scraper error: {e}")
            return

        if text_lower.startswith("/groups"):
            try:
                from integrations.fb_group_scraper import handle_groups_command
                response = handle_groups_command(text)
                await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Groups error: {e}")
            return

        if text_lower.startswith("/add_group"):
            try:
                from integrations.fb_group_scraper import handle_add_group_command
                response = handle_add_group_command(text)
                await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Add group error: {e}")
            return

        if text_lower.startswith("/digest"):
            try:
                from integrations.fb_group_scraper import handle_digest_command
                response = handle_digest_command(text)
                await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Digest error: {e}")
            return

        if text_lower.startswith("/draft"):
            try:
                from integrations.fb_group_scraper import handle_draft_command
                response = await handle_draft_command(text)
                await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Draft error: {e}")
            return

        # ── Browser Agent Commands (Build 10) ──
        if text_lower.startswith("/browse"):
            try:
                from integrations.browser_agent import handle_browse_command
                response = await handle_browse_command(text)
                await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Browse error: {e}")
            return

        # ── Task Queue Commands (Build 10) ──
        if text_lower.startswith("/tasks"):
            try:
                from core.task_queue import handle_tasks_command
                response = handle_tasks_command(text)
                await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Tasks error: {e}")
            return

        if text_lower.startswith("/do "):
            try:
                from core.task_queue import handle_do_command
                response = handle_do_command(text)
                await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Do error: {e}")
            return

        # ── Build Pipeline Commands ──
        if text_lower.startswith("/build "):
            desc = text[7:].strip()
            if not desc:
                await self.send(chat_id, "Usage: /build [description]")
                return
            try:
                from core.build_pipeline import get_build_pipeline
                pipeline = get_build_pipeline()
                build_id = pipeline.start_build(desc, started_by="telegram")
                if build_id:
                    await self.send(chat_id, f"🔨 *Build #{build_id} started* (free models)\n_{desc}_")
                else:
                    await self.send(chat_id, "❌ Failed to start build")
            except Exception as e:
                await self.send(chat_id, f"❌ Build error: {e}")
            return

        if text_lower.startswith("/claude_build "):
            try:
                bid = int(text_lower.split()[1])
            except (IndexError, ValueError):
                await self.send(chat_id, "Usage: /claude\\_build [build\\_id]")
                return
            try:
                from core.build_pipeline import get_build_pipeline
                pipeline = get_build_pipeline()
                ok = pipeline.upgrade_build(bid)
                if ok:
                    await self.send(chat_id, f"⚡ *Build #{bid} upgrading to Claude Sonnet*\nStreaming will resume shortly.")
                else:
                    build = pipeline.get_build(bid)
                    status = build["status"] if build else "not found"
                    await self.send(chat_id, f"❌ Cannot upgrade build #{bid} (status: {status})")
            except Exception as e:
                await self.send(chat_id, f"❌ Upgrade error: {e}")
            return

        if text_lower == "/builds":
            try:
                from core.build_pipeline import get_build_pipeline
                builds = get_build_pipeline().list_builds(limit=5)
                if not builds:
                    await self.send(chat_id, "No builds yet.")
                    return
                lines = ["*Recent Builds:*"]
                for b in builds:
                    cost = f" | ${b.get('cost_usd', 0):.2f}" if b.get('cost_usd') else ""
                    lines.append(f"#{b['id']} — {b['status']}{cost}\n  _{b.get('description', '')[:60]}_")
                await self.send(chat_id, "\n".join(lines))
            except Exception as e:
                await self.send(chat_id, f"❌ Builds error: {e}")
            return

        # ── Approval System Commands (Build 1) ──
        if text_lower == "/test_notification":
            try:
                from core.approval_system import handle_test_notification
                response = await handle_test_notification()
                await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Test notification error: {e}")
            return

        # ── FB Group Scraper Commands ──
        if text_lower.startswith("/fb_"):
            await self._handle_fb_command(chat_id, text, text_lower)
            return

        # ── B2B Cold Email Commands ──
        if text_lower == "/b2b_status":
            await self._show_b2b_status(chat_id)
            return

        if text_lower == "/generate_batch" or text_lower.startswith("/generate_batch "):
            parts = text.split()
            batch_size = 10
            if len(parts) > 1:
                try:
                    batch_size = min(int(parts[1]), 15)
                except ValueError:
                    pass
            await self._generate_b2b_batch(chat_id, batch_size)
            return

        if text_lower.startswith("/b2b_lead "):
            try:
                lead_id = int(text.split()[1])
                await self._show_b2b_lead(chat_id, lead_id)
            except (ValueError, IndexError):
                await self.send(chat_id, "Usage: /b2b\\_lead {id}")
            return

        if text_lower.startswith("/b2b_search "):
            term = text[len("/b2b_search "):].strip()
            if term:
                await self._search_b2b(chat_id, term)
            else:
                await self.send(chat_id, "Usage: /b2b\\_search {term}")
            return

        if text_lower.startswith("/approve"):
            try:
                from core.approval_system import handle_approve_command
                response = await handle_approve_command(text)
                await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Approve error: {e}")
            return

        if text_lower.startswith("/skip") and not text_lower.startswith("/skipall"):
            try:
                from core.approval_system import handle_skip_command
                response = await handle_skip_command(text)
                await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Skip error: {e}")
            return

        if text_lower.startswith("/stop"):
            try:
                from core.approval_system import handle_stop_command
                response = handle_stop_command(text)
                await self.send(chat_id, response)
            except Exception as e:
                await self.send(chat_id, f"❌ Stop error: {e}")
            return

        # ── Approval responses ──
        # Check if there's a pending context (most recent approval shown)
        ctx = self._pending_context.get(chat_id)

        if text_lower in ("yes", "y") and ctx:
            if self._approval_handler:
                await self._approval_handler(chat_id, ctx["approval_id"], "approve", None)
            self._pending_context.pop(chat_id, None)
            return

        if text_lower == "skip" and ctx:
            if self._approval_handler:
                await self._approval_handler(chat_id, ctx["approval_id"], "skip", None)
            self._pending_context.pop(chat_id, None)
            return

        if text_lower == "stop" and ctx:
            if self._approval_handler:
                await self._approval_handler(chat_id, ctx["approval_id"], "stop", None)
            self._pending_context.pop(chat_id, None)
            return

        # ── Direct approval by ID: "yes 42" or "skip 42" ──
        if text_lower.startswith(("yes ", "y ")) and self._approval_handler:
            try:
                aid = int(text_lower.split()[-1])
                await self._approval_handler(chat_id, aid, "approve", None)
                return
            except (ValueError, IndexError):
                pass

        if text_lower.startswith("skip ") and self._approval_handler:
            try:
                aid = int(text_lower.split()[-1])
                await self._approval_handler(chat_id, aid, "skip", None)
                return
            except (ValueError, IndexError):
                pass

        if text_lower.startswith("stop ") and self._approval_handler:
            try:
                aid = int(text_lower.split()[-1])
                await self._approval_handler(chat_id, aid, "stop", None)
                return
            except (ValueError, IndexError):
                pass

        # ── Custom text reply (edit the message) ──
        if ctx and ctx.get("approval_id") and self._approval_handler:
            # Treat any non-command text as custom message to send
            await self._approval_handler(chat_id, ctx["approval_id"], "edit", text)
            self._pending_context.pop(chat_id, None)
            return

        # ── Lead reply forwarding ──
        if ctx and ctx.get("lead_id") and ctx.get("channel") and self._reply_handler:
            await self._reply_handler(chat_id, ctx["lead_id"], ctx["channel"], text)
            self._pending_context.pop(chat_id, None)
            return

        # ── Pending /send body capture ──
        if not text.startswith("/") and chat_id in self._pending_email:
            pending = self._pending_email.pop(chat_id, {})
            to_email = (pending.get("to") or "").strip()
            subject = (pending.get("subject") or "").strip()
            if to_email and subject:
                await self._deliver_zoar_email(chat_id, to_email, subject, text)
                return

        # ── Persist plain-text operator instructions ──
        if not text.startswith("/"):
            try:
                from core.nexus_coordination import add_kai_instruction
                instruction_id = add_kai_instruction(
                    text,
                    priority="high",
                    source="telegram",
                    chat_id=chat_id,
                    created_by="kai",
                )
                await self.send(
                    chat_id,
                    f"📝 Instruction saved for agents (#{instruction_id}).",
                    parse_mode=None,
                )
                return
            except Exception:
                pass

        # ── AI Classifier — natural language routing ──
        if not text.startswith("/"):
            try:
                from core.ai_classifier import handle_natural_language
                result = await handle_natural_language(text)
                if result:
                    response_text, command = result
                    if command:
                        # Re-route through _handle_message as a command
                        await self._handle_message(chat_id, command)
                        return
                    elif response_text:
                        await self.send(chat_id, response_text)
                        return
            except ImportError:
                pass  # AI classifier not installed yet
            except Exception as e:
                print(f"[TG] AI classifier error: {e}")

        # ── General task handler (original behavior) ──
        if self._handler:
            await self.send(chat_id, f"⚙️ _{text[:80]}_")
            try:
                await self._handler(chat_id, text)
            except Exception as e:
                await self.send(chat_id, f"❌ {e}")

    # ── Command Center helpers (additive, SQLite-backed) ─────────────────────

    def _db_conn(self) -> sqlite3.Connection:
        db_path = Path.home() / ".nexus" / "memory.db"
        conn = sqlite3.connect(str(db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _table_exists(self, conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        return bool(row)

    def _column_exists(self, conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
        try:
            rows = conn.execute("PRAGMA table_info(%s)" % table_name).fetchall()
            return any(str(r["name"] if isinstance(r, sqlite3.Row) else r[1]) == column_name for r in rows)
        except Exception:
            return False

    async def _cc_status(self, chat_id: str):
        """High-level status from existing SQLite + process manager state."""
        try:
            conn = self._db_conn()
            try:
                vendors_total = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0] if self._table_exists(conn, "vendors") else 0
                eligible = conn.execute("SELECT COUNT(*) FROM vendors WHERE campaign_eligible = 1").fetchone()[0] if self._table_exists(conn, "vendors") else 0
                outbound_ts_col = "sent_at" if self._column_exists(conn, "outbound_log", "sent_at") else "timestamp"
                sent_today = conn.execute(
                    "SELECT COUNT(*) FROM outbound_log WHERE DATE(%s)=DATE('now','localtime') AND result IN ('sent','allowed')" % outbound_ts_col
                ).fetchone()[0] if self._table_exists(conn, "outbound_log") else 0
                bounced = conn.execute(
                    "SELECT COUNT(*) FROM vendors WHERE outreach_status='bounced'"
                ).fetchone()[0] if self._table_exists(conn, "vendors") else 0
                lead_ts_col = ""
                if self._table_exists(conn, "leads"):
                    for c in ("created_at", "discovered_at", "date_added"):
                        if self._column_exists(conn, "leads", c):
                            lead_ts_col = c
                            break
                leads_today = conn.execute(
                    "SELECT COUNT(*) FROM leads WHERE DATE(%s)=DATE('now','localtime')" % lead_ts_col
                ).fetchone()[0] if (self._table_exists(conn, "leads") and lead_ts_col) else 0
                leads_total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0] if self._table_exists(conn, "leads") else 0
                pending_approvals = conn.execute(
                    "SELECT COUNT(*) FROM message_approvals WHERE status='pending'"
                ).fetchone()[0] if self._table_exists(conn, "message_approvals") else 0

                pm = conn.execute(
                    "SELECT pid, status, last_heartbeat FROM managed_processes WHERE name='process_manager' LIMIT 1"
                ).fetchone() if self._table_exists(conn, "managed_processes") else None
            finally:
                conn.close()

            pm_line = "process manager: not tracked"
            if pm:
                pm_line = f"process manager: {pm['status']} (pid {pm['pid']})"

            msg = (
                f"📊 NEXUS STATUS — {datetime.now().strftime('%I:%M %p')}\n\n"
                f"📧 Emails sent today: {sent_today}\n"
                f"⚠️ Bounced: {bounced}\n"
                f"👥 Vendors: {vendors_total} total / {eligible} eligible\n"
                f"🔥 Leads: {leads_today} today / {leads_total} total\n"
                f"📋 Pending approvals: {pending_approvals}\n"
                f"🤖 {pm_line}"
            )
            await self.send(chat_id, msg, parse_mode=None)
        except Exception as e:
            await self.send(chat_id, f"❌ /status error: {e}", parse_mode=None)

    async def _cc_leads(self, chat_id: str):
        """Show latest leads (today first), direct from SQLite."""
        try:
            conn = self._db_conn()
            try:
                if not self._table_exists(conn, "leads"):
                    await self.send(chat_id, "No leads table found.", parse_mode=None)
                    return
                lead_ts_col = ""
                for c in ("created_at", "discovered_at", "date_added"):
                    if self._column_exists(conn, "leads", c):
                        lead_ts_col = c
                        break
                if not lead_ts_col:
                    lead_ts_col = "id"
                rows = conn.execute(
                    """
                    SELECT id,
                           COALESCE(full_name, TRIM(COALESCE(first_name,'') || ' ' || COALESCE(last_name,''))) AS name,
                           phone,
                           email,
                           source,
                           %s AS created_ts
                    FROM leads
                    ORDER BY datetime(%s) DESC
                    LIMIT 10
                    """
                    % (lead_ts_col, lead_ts_col)
                ).fetchall()
            finally:
                conn.close()

            if not rows:
                await self.send(chat_id, "No leads found.", parse_mode=None)
                return

            lines = [f"🔥 Latest Leads ({len(rows)})", ""]
            for i, row in enumerate(rows, 1):
                name = row["name"] or "Unknown"
                lines.append(f"{i}. #{row['id']} {name}")
                lines.append(f"   📱 {row['phone'] or 'N/A'}")
                lines.append(f"   📧 {row['email'] or 'N/A'}")
                lines.append(f"   📍 {row['source'] or 'N/A'} — {row['created_ts'] or 'N/A'}")
                lines.append("")
            await self.send(chat_id, "\n".join(lines), parse_mode=None)
        except Exception as e:
            await self.send(chat_id, f"❌ /leads error: {e}", parse_mode=None)

    async def _cc_emails(self, chat_id: str):
        """Email outreach status from existing outreach/outbound tables."""
        try:
            conn = self._db_conn()
            try:
                sent_today = conn.execute(
                    "SELECT COUNT(*) FROM outbound_log WHERE DATE(%s)=DATE('now','localtime') AND result IN ('sent','allowed')" %
                    ("sent_at" if self._column_exists(conn, "outbound_log", "sent_at") else "timestamp")
                ).fetchone()[0] if self._table_exists(conn, "outbound_log") else 0

                bounced = conn.execute(
                    "SELECT COUNT(*) FROM vendors WHERE outreach_status='bounced'"
                ).fetchone()[0] if self._table_exists(conn, "vendors") else 0

                replied = conn.execute(
                    "SELECT COUNT(*) FROM vendors WHERE outreach_status='replied'"
                ).fetchone()[0] if self._table_exists(conn, "vendors") else 0

                pending = conn.execute(
                    "SELECT COUNT(*) FROM message_approvals WHERE status='pending'"
                ).fetchone()[0] if self._table_exists(conn, "message_approvals") else 0

                outbound_ts_col = "sent_at" if self._column_exists(conn, "outbound_log", "sent_at") else "timestamp"
                recent_rows = conn.execute(
                    """
                    SELECT recipient, %s AS sent_time
                    FROM outbound_log
                    WHERE result IN ('sent','allowed')
                    ORDER BY datetime(%s) DESC
                    LIMIT 5
                    """
                    % (outbound_ts_col, outbound_ts_col)
                ).fetchall() if self._table_exists(conn, "outbound_log") else []
            finally:
                conn.close()

            lines = [
                "📧 EMAIL STATUS",
                "",
                f"Sent today: {sent_today}",
                f"Bounced: {bounced}",
                f"Vendor replies: {replied}",
                f"Pending approvals: {pending}",
            ]
            if recent_rows:
                lines.append("")
                lines.append("Recent sends:")
                for row in recent_rows:
                    lines.append(f"  ✅ {row['recipient']} — {row['sent_time']}")
            await self.send(chat_id, "\n".join(lines), parse_mode=None)
        except Exception as e:
            await self.send(chat_id, f"❌ /emails error: {e}", parse_mode=None)

    async def _cc_health(self, chat_id: str):
        """Health + supervision status using existing process_manager tracking."""
        try:
            db_path = Path.home() / ".nexus" / "memory.db"
            db_size_mb = int(db_path.stat().st_size / (1024 * 1024)) if db_path.exists() else 0
            total, used, free = shutil.disk_usage("/")
            disk_free_gb = int(free / (1024 ** 3))

            server_status = "down"
            try:
                async with httpx.AsyncClient(timeout=5) as c:
                    for url in ("http://127.0.0.1:8080/api/health", "http://127.0.0.1:7860/api/health"):
                        try:
                            r = await c.get(url)
                            if r.status_code == 200:
                                server_status = "running"
                                break
                        except Exception:
                            continue
            except Exception:
                pass

            conn = self._db_conn()
            try:
                rows = conn.execute(
                    """
                    SELECT name, pid, status, last_heartbeat, restart_count
                    FROM managed_processes
                    ORDER BY CASE WHEN name='process_manager' THEN 0 ELSE 1 END, name ASC
                    """
                ).fetchall() if self._table_exists(conn, "managed_processes") else []
            finally:
                conn.close()

            running = sum(1 for r in rows if str(r["status"]).lower() == "running")
            kill_switch = "unknown"
            try:
                from core.security import get_security_gate
                kill_switch = "active" if get_security_gate().is_kill_switch_active() else "off"
            except Exception:
                kill_file = Path.home() / ".nexus" / ".kill_switch"
                kill_switch = "active" if kill_file.exists() else "off"

            lines = [
                "🏥 SYSTEM HEALTH",
                "",
                f"Server: {'✅' if server_status == 'running' else '❌'} {server_status}",
                f"Disk free: {disk_free_gb} GB",
                f"DB size: {db_size_mb} MB",
                f"Managed processes: {running}/{len(rows)} running",
                f"Kill switch: {'🚨 active' if kill_switch == 'active' else '✅ off'}",
            ]

            if rows:
                lines.append("")
                lines.append("Process manager view:")
                for row in rows[:10]:
                    icon = "✅" if str(row["status"]).lower() == "running" else "❌"
                    lines.append(
                        f"  {icon} {row['name']} (pid {row['pid']}) "
                        f"restarts={row['restart_count']} hb={row['last_heartbeat']}"
                    )

            await self.send(chat_id, "\n".join(lines), parse_mode=None)
        except Exception as e:
            await self.send(chat_id, f"❌ /health error: {e}", parse_mode=None)

    async def _cc_find(self, chat_id: str, text: str):
        query = text[5:].strip() if len(text) >= 5 else ""
        if not query:
            await self.send(chat_id, "Usage: /find wedding planner", parse_mode=None)
            return
        try:
            conn = self._db_conn()
            try:
                vendor_rows = conn.execute(
                    """
                    SELECT id, name, email, phone, category, city, outreach_status
                    FROM vendors
                    WHERE name LIKE ? OR category LIKE ? OR city LIKE ? OR email LIKE ?
                    ORDER BY COALESCE(referral_score, 0) DESC, id DESC
                    LIMIT 8
                    """,
                    (f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%"),
                ).fetchall() if self._table_exists(conn, "vendors") else []

                lead_rows = conn.execute(
                    """
                    SELECT id,
                           COALESCE(full_name, TRIM(COALESCE(first_name,'') || ' ' || COALESCE(last_name,''))) AS name,
                           phone, email, source, status
                    FROM leads
                    WHERE COALESCE(full_name,'') LIKE ?
                       OR COALESCE(first_name,'') LIKE ?
                       OR COALESCE(last_name,'') LIKE ?
                       OR COALESCE(email,'') LIKE ?
                       OR COALESCE(phone,'') LIKE ?
                    ORDER BY id DESC
                    LIMIT 8
                    """,
                    (f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%"),
                ).fetchall() if self._table_exists(conn, "leads") else []
            finally:
                conn.close()

            if not vendor_rows and not lead_rows:
                await self.send(chat_id, f"No matches for '{query}'.", parse_mode=None)
                return

            lines = [f"🔍 Results for '{query}'", ""]
            if vendor_rows:
                lines.append("Vendors:")
                for row in vendor_rows:
                    lines.append(
                        f"  • #{row['id']} {row['name']} | {row['category'] or 'N/A'} | "
                        f"{row['city'] or 'N/A'} | {row['email'] or 'N/A'} | {row['outreach_status'] or 'not_contacted'}"
                    )
                lines.append("")
            if lead_rows:
                lines.append("Leads:")
                for row in lead_rows:
                    lines.append(
                        f"  • #{row['id']} {row['name'] or 'Unknown'} | {row['phone'] or 'N/A'} | "
                        f"{row['email'] or 'N/A'} | {row['status'] or 'N/A'}"
                    )

            await self.send(chat_id, "\n".join(lines), parse_mode=None)
        except Exception as e:
            await self.send(chat_id, f"❌ /find error: {e}", parse_mode=None)

    async def _cc_send(self, chat_id: str, text: str):
        """Kai direct-send command for branded HTML emails."""
        raw = text[5:].strip() if len(text) >= 5 else ""
        if not raw:
            await self.send(chat_id, "Usage: /send email@addr Subject | Body text", parse_mode=None)
            return

        # Safety: honor global kill switch.
        try:
            from core.security import get_security_gate
            if get_security_gate().is_kill_switch_active():
                await self.send(chat_id, "🚫 Kill switch is active. Use /resume first.", parse_mode=None)
                return
        except Exception:
            pass

        if " " not in raw:
            await self.send(chat_id, "Usage: /send email@addr Subject | Body text", parse_mode=None)
            return

        to_email, rest = raw.split(" ", 1)
        to_email = to_email.strip()
        if "@" not in to_email:
            await self.send(chat_id, "Invalid recipient email.", parse_mode=None)
            return

        if "|" in rest:
            subject, body = rest.split("|", 1)
            subject = subject.strip()
            body = body.strip()
            if not subject or not body:
                await self.send(chat_id, "Usage: /send email@addr Subject | Body text", parse_mode=None)
                return
            await self._deliver_zoar_email(chat_id, to_email, subject, body)
            return

        # Two-step mode: user sends body in the next plain-text message.
        subject = rest.strip()
        if not subject:
            await self.send(chat_id, "Subject required. Usage: /send email@addr Subject | Body text", parse_mode=None)
            return
        self._pending_email[chat_id] = {"to": to_email, "subject": subject}
        await self.send(
            chat_id,
            (
                f"To: {to_email}\n"
                f"Subject: {subject}\n\n"
                "Now send the body text as your next message."
            ),
            parse_mode=None,
        )

    def _fetch_fb_ads_today(self, ad_account: str, token: str) -> dict:
        import urllib.request
        from urllib.parse import quote_plus

        account = ad_account.strip()
        if account and not account.startswith("act_"):
            account = "act_%s" % account

        fields = "spend,impressions,clicks,actions,cost_per_action_type"
        url = (
            "https://graph.facebook.com/v19.0/%s/insights"
            "?fields=%s&date_preset=today&access_token=%s"
            % (account, quote_plus(fields), quote_plus(token))
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Nexus/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("error"):
            raise RuntimeError(str(data.get("error")))
        return data

    async def _cc_ads(self, chat_id: str, text: str):
        """Facebook ad performance using config token/account, with local fallback."""
        query = text[4:].strip() if len(text) > 4 else ""
        cfg_path = Path.home() / ".nexus" / "config.json"
        cfg = {}
        try:
            if cfg_path.exists():
                cfg = json.loads(cfg_path.read_text())
        except Exception:
            cfg = {}

        token = (
            cfg.get("fb_page_access_token")
            or cfg.get("fb_access_token")
            or cfg.get("facebook_access_token")
            or ""
        )
        ad_account = cfg.get("fb_ad_account_id") or cfg.get("facebook_ad_account_id") or ""

        if token and ad_account:
            try:
                data = await asyncio.to_thread(self._fetch_fb_ads_today, ad_account, token)
                insights = data.get("data") or []
                if not insights:
                    await self.send(
                        chat_id,
                        "📊 FACEBOOK ADS (Today)\n\nNo ad data yet (possibly still in learning phase).",
                        parse_mode=None,
                    )
                    return

                i = insights[0]
                spend = float(i.get("spend") or 0)
                impressions = int(float(i.get("impressions") or 0))
                clicks = int(float(i.get("clicks") or 0))
                leads = 0
                for a in i.get("actions") or []:
                    if a.get("action_type") in ("lead", "onsite_conversion.lead_grouped"):
                        try:
                            leads += int(float(a.get("value") or 0))
                        except Exception:
                            continue

                ctr = (clicks / impressions * 100) if impressions > 0 else 0.0
                cpl = (spend / leads) if leads > 0 else 0.0
                lines = [
                    "📊 FACEBOOK ADS — Today",
                    "",
                    f"💰 Spend: ${spend:.2f}",
                    f"👁 Impressions: {impressions}",
                    f"👆 Clicks: {clicks}",
                    f"📈 CTR: {ctr:.2f}%",
                    f"🔥 Leads: {leads}",
                    f"💵 Cost per lead: ${cpl:.2f}" if leads > 0 else "💵 Cost per lead: N/A",
                ]
                await self.send(chat_id, "\n".join(lines), parse_mode=None)
                return
            except Exception as e:
                await self.send(chat_id, f"⚠️ FB API fetch failed: {e}", parse_mode=None)

        # Fallback to existing local ad monitor pipeline.
        try:
            from core.ad_monitor import handle_ads_query
            response = await handle_ads_query(query or "how are the ads doing today")
            await self.send(chat_id, response, parse_mode=None)
        except Exception as e:
            await self.send(
                chat_id,
                (
                    "Facebook ads not configured. Need fb_page_access_token and "
                    "fb_ad_account_id in ~/.nexus/config.json.\n"
                    f"Fallback error: {e}"
                ),
                parse_mode=None,
            )

    async def _cc_broadcast(self, chat_id: str, text: str):
        raw = text[len("/broadcast"):].strip() if text.startswith("/broadcast") else ""
        if not raw:
            await self.send(chat_id, "Usage: /broadcast Your message to all agents", parse_mode=None)
            return
        try:
            from core.nexus_coordination import add_kai_instruction, log_event
            instruction_id = add_kai_instruction(
                raw,
                priority="high",
                source="telegram_broadcast",
                chat_id=chat_id,
                created_by="kai",
            )
            log_event(
                "BROADCAST",
                payload={"instruction_id": instruction_id, "message": raw},
                source="telegram",
            )
            await self.send(
                chat_id,
                f"📢 Broadcast sent to all agents (instruction #{instruction_id}).",
                parse_mode=None,
            )
        except Exception as e:
            await self.send(chat_id, f"❌ Broadcast failed: {e}", parse_mode=None)

    async def _cc_assign(self, chat_id: str, text: str):
        raw = text[len("/assign"):].strip() if text.startswith("/assign") else ""
        if not raw or " to " not in raw.lower():
            await self.send(
                chat_id,
                "Usage: /assign Scrape 50 new quinceañera vendors to vendor-1-t3",
                parse_mode=None,
            )
            return

        split_at = raw.lower().rfind(" to ")
        task_desc = raw[:split_at].strip()
        agent_name = raw[split_at + 4:].strip()
        if not task_desc or not agent_name:
            await self.send(
                chat_id,
                "Usage: /assign Scrape 50 new quinceañera vendors to vendor-1-t3",
                parse_mode=None,
            )
            return

        tier = 3
        try:
            import re
            m = re.search(r"t(\d+)", agent_name.lower())
            if m:
                parsed = int(m.group(1))
                if 1 <= parsed <= 7:
                    tier = parsed
        except Exception:
            pass

        try:
            from core.fleet_task_queue import FleetTaskQueue
            from core.nexus_coordination import log_event
            q = FleetTaskQueue()
            task_id = q.enqueue(
                task_type="manual_assignment",
                input_data={
                    "prompt": task_desc,
                    "assigned_to": agent_name,
                    "source": "kai_telegram",
                },
                tier=tier,
                priority=10,
            )
            log_event(
                "TASK_ASSIGNED",
                payload={
                    "task": task_desc,
                    "agent": agent_name,
                    "task_id": task_id,
                },
                source="telegram",
            )
            await self.send(
                chat_id,
                f"✅ Task assigned to {agent_name}\nTask ID: {task_id}\n{task_desc}",
                parse_mode=None,
            )
        except Exception as e:
            await self.send(chat_id, f"❌ Assign failed: {e}", parse_mode=None)

    async def _deliver_zoar_email(self, chat_id: str, to_email: str, subject: str, body: str):
        script = Path("/Users/kai/nexus/scripts/zoar_email.py")
        if not script.exists():
            await self.send(chat_id, f"❌ Missing sender script: {script}", parse_mode=None)
            return
        python_bin = Path("/Users/kai/nexus/venv/bin/python3")
        if not python_bin.exists():
            python_bin = Path(os.environ.get("PYTHON", "")) if os.environ.get("PYTHON") else Path("python3")

        try:
            result = subprocess.run(
                [str(python_bin), str(script), "--to", to_email, "--subject", subject, "--body", body],
                cwd="/Users/kai/nexus",
                capture_output=True,
                text=True,
                timeout=60,
            )
            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()
            sent = ("Sent to" in stdout) or ("Sent! Email delivered" in stdout)

            if sent:
                try:
                    from core.nexus_coordination import log_event
                    log_event(
                        "EMAIL_SENT",
                        payload={"to": to_email, "subject": subject, "via": "telegram_send"},
                        source="telegram",
                    )
                except Exception:
                    pass
                await self.send(chat_id, f"✅ Branded email sent to {to_email}", parse_mode=None)
                return

            error_msg = stderr or stdout or f"exit={result.returncode}"
            await self.send(chat_id, f"❌ Send failed: {error_msg[:500]}", parse_mode=None)
        except Exception as e:
            await self.send(chat_id, f"❌ Send error: {e}", parse_mode=None)

    async def _cc_scrape(self, chat_id: str, text: str = ""):
        """Enqueue vendor discovery via fleet_task_queue (no raw subprocess)."""
        try:
            scope = ""
            if text and len(text) > 7:
                scope = text[7:].strip()
            if not scope:
                scope = "event vendors in Los Angeles / San Fernando Valley"

            from core.fleet_task_queue import FleetTaskQueue
            q = FleetTaskQueue()
            task_id = q.enqueue(
                task_type="vendor_research",
                input_data={
                    "prompt": (
                        f"List 15 {scope}. "
                        "Return JSON array with name, phone, email, website, address, city."
                    ),
                    "zone": "sfv",
                    "category": scope[:80],
                    "location": "Los Angeles, CA",
                    "max_tokens": 2000,
                    "post_process": "vendor_research",
                    "triggered_by": "kai_telegram",
                },
                tier=3,
                priority=9,
            )
            await self.send(chat_id, f"🔍 Discovery task queued: {task_id}", parse_mode=None)
        except Exception as e:
            await self.send(chat_id, f"❌ /scrape queue error: {e}", parse_mode=None)

    async def _cc_stop(self, chat_id: str):
        """Emergency stop all outbound sends using existing security gate."""
        try:
            from core.security import get_security_gate
            gate = get_security_gate()
            gate.activate_kill_switch("kai_telegram_command_center")
            # Preserve legacy file-based checks for older scripts.
            (Path.home() / ".nexus" / ".kill_switch").touch()
            await self.send(chat_id, "🚨 EMERGENCY STOP ACTIVATED. Outbound sends are blocked.", parse_mode=None)
        except Exception as e:
            await self.send(chat_id, f"❌ /stop error: {e}", parse_mode=None)

    async def _cc_resume(self, chat_id: str):
        """Resume outbound sends by clearing existing kill switch."""
        try:
            from core.security import get_security_gate
            gate = get_security_gate()
            gate.deactivate_kill_switch()
            kill_file = Path.home() / ".nexus" / ".kill_switch"
            if kill_file.exists():
                kill_file.unlink()
            await self.send(chat_id, "✅ Operations resumed. Kill switch deactivated.", parse_mode=None)
        except Exception as e:
            await self.send(chat_id, f"❌ /resume error: {e}", parse_mode=None)

    async def _show_queue(self, chat_id: str):
        """Show fleet task queue + approval queue status."""
        fleet_stats = {}
        fleet_active = []
        fleet_pending = []
        try:
            from core.fleet_task_queue import FleetTaskQueue
            q = FleetTaskQueue()
            fleet_stats = q.get_stats()
            activity = q.get_activity_feed(limit=30)
            for row in activity:
                status = str(row.get("status") or "")
                if status in ("claimed", "in_progress") and len(fleet_active) < 6:
                    fleet_active.append(row)
                elif status == "pending" and len(fleet_pending) < 8:
                    fleet_pending.append(row)
        except Exception:
            fleet_stats = {}

        pending = []
        stats = {"approved_today": 0, "skipped_today": 0}
        try:
            from core.approval_system import get_all_pending, get_approval_stats
            pending = get_all_pending()
            stats = get_approval_stats()
        except Exception:
            try:
                from core.approval_queue import get_all_pending, get_approval_stats
                pending = get_all_pending()
                stats = get_approval_stats()
            except Exception:
                pending = []

        lines = ["📋 NEXUS QUEUES", ""]
        if fleet_stats:
            lines.append(
                "Fleet tasks: active %s | pending %s | completed %s | dead-letter %s"
                % (
                    fleet_stats.get("claimed", 0) + fleet_stats.get("in_progress", 0),
                    fleet_stats.get("pending", 0),
                    fleet_stats.get("completed", 0),
                    fleet_stats.get("dead_letter", 0),
                )
            )
        else:
            lines.append("Fleet tasks: unavailable")

        lines.append(
            "Approvals: pending %d | approved today %d | skipped today %d"
            % (len(pending), stats.get("approved_today", 0), stats.get("skipped_today", 0))
        )

        if fleet_active:
            lines.append("")
            lines.append("ACTIVE TASKS:")
            for t in fleet_active:
                lines.append(
                    "  🔄 %s (%s) by %s"
                    % (
                        t.get("task_type", "unknown"),
                        t.get("status", "?"),
                        t.get("claimed_by", "?"),
                    )
                )

        if fleet_pending:
            lines.append("")
            lines.append("PENDING TASKS:")
            for t in fleet_pending:
                lines.append(
                    "  ⏳ %s (tier %s, pri %s)"
                    % (
                        t.get("task_type", "unknown"),
                        t.get("tier", "?"),
                        t.get("priority", "?"),
                    )
                )

        if pending:
            lines.append("")
            lines.append("PENDING APPROVALS:")
            for p in pending[:5]:
                lines.append(
                    "  • #%s %s (%s)"
                    % (p.get("id"), p.get("lead_name", "Unknown"), p.get("channel", "?"))
                )

        await self.send(chat_id, "\n".join(lines), parse_mode=None)

    async def _show_leads_summary(self, chat_id: str):
        """Show active leads summary."""
        try:
            from core.lead_pipeline import get_pipeline
            p = get_pipeline()
            if not p:
                await self.send(chat_id, "⚠️ Pipeline not initialized")
                return

            leads = p.get_leads(limit=20)
            active = [l for l in leads if l.get("status") not in ("closed", "opted_out")]

            if not active:
                await self.send(chat_id, "📊 No active leads in pipeline")
                return

            text = f"📊 *Active Leads* — {len(active)}\n━━━━━━━━━━━━━━━━━━\n\n"
            for l in active[:15]:
                name = f"{l.get('first_name', '')} {l.get('last_name', '')}".strip()
                text += f"• *{name}* — {l.get('status', '?')} ({l.get('source', '?')})\n"

            await self.send(chat_id, text)
        except Exception as e:
            await self.send(chat_id, f"❌ Error: {e}")

    # ── /today — Morning briefing ────────────────────────────────────────────

    async def _show_today_briefing(self, chat_id: str):
        """Combined morning briefing: pipeline status + posting package."""
        try:
            import sqlite3 as _sql
            from pathlib import Path as _P
            db = _sql.connect(str(_P.home() / ".nexus" / "memory.db"))

            active = db.execute(
                "SELECT COUNT(*) FROM leads WHERE status NOT IN ('closed','opted_out')"
            ).fetchone()[0]
            pending_approvals = db.execute(
                "SELECT COUNT(*) FROM message_approvals WHERE status='pending'"
            ).fetchone()[0]
            today_leads = db.execute(
                "SELECT COUNT(*) FROM leads WHERE created_at >= date('now')"
            ).fetchone()[0]
            db.close()

            from core.approval_queue import get_approval_stats
            stats = get_approval_stats()

            text = (
                f"☀️ GOOD MORNING KAI\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📊 *PIPELINE STATUS:*\n"
                f"• Active leads: {active}\n"
                f"• New today: {today_leads}\n"
                f"• Pending approvals: {pending_approvals}\n"
                f"• Approved today: {stats['approved_today']}\n"
                f"• Skipped today: {stats['skipped_today']}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📋 Generating posting package..."
            )
            await self.send(chat_id, text)

            # Also trigger the posting briefing
            try:
                from core.daily_posting import generate_and_send_briefing
                await generate_and_send_briefing()
            except Exception as e:
                await self.send(chat_id, f"⚠️ Posting briefing error: {e}")

        except Exception as e:
            await self.send(chat_id, f"❌ Briefing error: {e}")

    # ── /lead [id] — Full lead detail ─────────────────────────────────────────

    async def _show_lead_detail(self, chat_id: str, text_lower: str):
        """Show full detail for a specific lead."""
        try:
            parts = text_lower.split()
            if len(parts) < 2:
                await self.send(chat_id, "Usage: /lead [id]")
                return
            lead_id = int(parts[1])

            from core.lead_pipeline import get_pipeline
            p = get_pipeline()
            if not p:
                await self.send(chat_id, "⚠️ Pipeline not initialized")
                return

            lead = p.get_lead(lead_id)
            if not lead:
                await self.send(chat_id, f"❌ Lead #{lead_id} not found")
                return

            name = f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip()
            phone = lead.get("phone", "N/A")
            email_addr = lead.get("email", "N/A")
            source = lead.get("source", "?")
            status = lead.get("status", "?")
            created = lead.get("created_at", "?")
            notes = lead.get("notes", "")

            # Get lead score
            score_text = ""
            try:
                from core.lead_scoring import score_lead
                score_info = score_lead(lead)
                score_text = f"🎯 Score: {score_info['score']}/10 — {score_info['label'].upper()}\n"
            except Exception:
                pass

            text = (
                f"👤 *LEAD #{lead_id}*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"Name: {name}\n"
                f"📱 Phone: {phone}\n"
                f"📧 Email: {email_addr}\n"
                f"📍 Source: {source}\n"
                f"📋 Status: {status}\n"
                f"📅 Created: {created}\n"
                f"{score_text}"
            )
            if notes:
                text += f"📝 Notes: {notes[:300]}\n"

            await self.send(chat_id, text)
        except ValueError:
            await self.send(chat_id, "Usage: /lead [id] — id must be a number")
        except Exception as e:
            await self.send(chat_id, f"❌ Error: {e}")

    # ── /stats — Weekly stats ─────────────────────────────────────────────────

    async def _show_stats(self, chat_id: str):
        """Show weekly stats: leads by source, approvals, conversions."""
        try:
            import sqlite3 as _sql
            from pathlib import Path as _P
            db = _sql.connect(str(_P.home() / ".nexus" / "memory.db"))

            # Leads by source (last 7 days)
            rows = db.execute(
                "SELECT source, COUNT(*) as cnt FROM leads "
                "WHERE created_at >= date('now', '-7 days') "
                "GROUP BY source ORDER BY cnt DESC"
            ).fetchall()

            total_7d = sum(r[1] for r in rows)
            source_lines = "\n".join(f"  • {r[0] or 'unknown'}: {r[1]}" for r in rows) if rows else "  No leads this week"

            # Approval stats (last 7 days)
            approved = db.execute(
                "SELECT COUNT(*) FROM message_approvals WHERE status='approved' "
                "AND approved_at >= date('now', '-7 days')"
            ).fetchone()[0]
            skipped = db.execute(
                "SELECT COUNT(*) FROM message_approvals WHERE status='skipped' "
                "AND approved_at >= date('now', '-7 days')"
            ).fetchone()[0]
            pending = db.execute(
                "SELECT COUNT(*) FROM message_approvals WHERE status='pending'"
            ).fetchone()[0]

            # Lead status breakdown
            status_rows = db.execute(
                "SELECT status, COUNT(*) FROM leads WHERE status NOT IN ('closed','opted_out') "
                "GROUP BY status ORDER BY COUNT(*) DESC"
            ).fetchall()
            status_lines = "\n".join(f"  • {r[0]}: {r[1]}" for r in status_rows) if status_rows else "  No active leads"

            db.close()

            text = (
                f"📊 *WEEKLY STATS* (last 7 days)\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"*New Leads:* {total_7d}\n"
                f"{source_lines}\n\n"
                f"*Messages:*\n"
                f"  • Approved: {approved}\n"
                f"  • Skipped: {skipped}\n"
                f"  • Pending: {pending}\n\n"
                f"*Pipeline:*\n"
                f"{status_lines}"
            )
            await self.send(chat_id, text)
        except Exception as e:
            await self.send(chat_id, f"❌ Stats error: {e}")

    # ── /services — Service status ────────────────────────────────────────────

    async def _show_services(self, chat_id: str):
        """Show status of all running services."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get("http://127.0.0.1:8080/api/health")
                health = r.json()

            services = health.get("services", {})
            text = (
                f"🔧 *SERVICE STATUS*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"Server: {'✅' if health.get('status') == 'ok' else '❌'} {health.get('status', '?')}\n\n"
            )
            for name, status in services.items():
                emoji = "✅" if status in ("running", "ok", True) else "❌"
                text += f"  {emoji} {name}: {status}\n"

            await self.send(chat_id, text)
        except Exception as e:
            await self.send(chat_id, f"❌ Can't reach health endpoint: {e}")

    # ── /mode — Toggle production/test mode ───────────────────────────────────

    async def _toggle_mode(self, chat_id: str):
        """Toggle between production and test mode."""
        try:
            import json as _json
            from pathlib import Path as _P
            config_path = _P.home() / ".nexus" / "config.json"
            config = {}
            if config_path.exists():
                config = _json.loads(config_path.read_text())

            current = config.get("production_mode", False)
            config["production_mode"] = not current
            config_path.write_text(_json.dumps(config, indent=2))

            new_mode = config["production_mode"]
            emoji = "🟢" if new_mode else "🟡"
            label = "PRODUCTION" if new_mode else "TEST"

            text = (
                f"{emoji} Mode switched to *{label}*\n\n"
            )
            if new_mode:
                text += "All approved messages will be sent to REAL leads."
            else:
                text += "Messages only sent to safe test numbers."

            await self.send(chat_id, text)
        except Exception as e:
            await self.send(chat_id, f"❌ Mode toggle error: {e}")

    # ── /status — Enhanced system status ──────────────────────────────────────

    async def _show_system_status(self, chat_id: str):
        """Show enhanced system status with uptime and mode."""
        try:
            import json as _json
            from pathlib import Path as _P
            config_path = _P.home() / ".nexus" / "config.json"
            config = {}
            if config_path.exists():
                config = _json.loads(config_path.read_text())

            mode = "🟢 PRODUCTION" if config.get("production_mode") else "🟡 TEST"

            import sqlite3 as _sql
            db = _sql.connect(str(_P.home() / ".nexus" / "memory.db"))
            total_leads = db.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
            active_leads = db.execute(
                "SELECT COUNT(*) FROM leads WHERE status NOT IN ('closed','opted_out')"
            ).fetchone()[0]
            pending = db.execute(
                "SELECT COUNT(*) FROM message_approvals WHERE status='pending'"
            ).fetchone()[0]
            db.close()

            text = (
                f"✅ *Nexus Online*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"Mode: {mode}\n"
                f"Leads: {active_leads} active / {total_leads} total\n"
                f"Pending approvals: {pending}\n"
                f"Telegram: ✅ Connected"
            )
            await self.send(chat_id, text)
        except Exception as e:
            await self.send(chat_id, f"✅ Nexus online\n⚠️ Status detail error: {e}")

    # ── Security helper methods (Phase 1H) ────────────────────────────────────

    async def _show_security_status(self, chat_id):
        """Show security gate status."""
        try:
            from core.security import get_security_gate
            gate = get_security_gate()
            import json as _json
            from pathlib import Path
            cfg = _json.loads((Path.home() / ".nexus" / "config.json").read_text())
            mode = cfg.get("messaging_mode", "disabled")
            kill = gate.is_kill_switch_active()
            dms = gate.is_dead_man_confirmed()

            # Circuit breaker status
            cb_status = []
            for ch in ["sms", "email", "facebook_dm"]:
                ok = gate.check_circuit_breaker(ch)
                cb_status.append(f"  {ch}: {'🟢 closed' if ok else '🔴 OPEN'}")

            text = (
                f"🔒 *SECURITY GATE STATUS*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"Mode: *{mode.upper()}*\n"
                f"Kill Switch: {'🔴 ACTIVE' if kill else '🟢 inactive'}\n"
                f"Dead Man's Switch: {'🟢 confirmed' if dms else '🟡 not confirmed'}\n"
                f"Outbound Enabled: {'🟢 yes' if cfg.get('outbound_messages_enabled') else '🔴 no'}\n\n"
                f"*Circuit Breakers:*\n"
                + "\n".join(cb_status) +
                f"\n\n*Commands:*\n"
                f"/set\\_mode [disabled|test|production]\n"
                f"/kill — Emergency stop all sends\n"
                f"/unkill — Remove kill switch\n"
                f"/confirm\\_alive — Dead man's switch\n"
                f"/outbound\\_status — Recent sends\n"
                f"/audit — Verify hash chain"
            )
            await self.send(chat_id, text)
        except Exception as e:
            await self.send(chat_id, f"❌ Security status error: {e}")

    async def _show_outbound_status(self, chat_id):
        """Show recent outbound log entries."""
        try:
            from core.security import get_security_gate
            gate = get_security_gate()
            rows = gate.get_outbound_log(10)
            if not rows:
                await self.send(chat_id, "📋 No outbound log entries")
                return
            lines = ["📋 *RECENT OUTBOUND LOG*\n━━━━━━━━━━━━━━━━━━"]
            for r in rows:
                emoji = "✅" if r["result"] == "allowed" else "🚫"
                recip = r.get("recipient_preview", "***")
                reason = r.get("reason", "")[:40]
                lines.append(f"{emoji} {r['channel']} → {recip} | {r['result']} | {reason}")
            await self.send(chat_id, "\n".join(lines))
        except Exception as e:
            await self.send(chat_id, f"❌ Error: {e}")

    async def _show_outbound_log(self, chat_id, n=10):
        """Show last N outbound log entries with more detail."""
        try:
            from core.security import get_security_gate
            gate = get_security_gate()
            rows = gate.get_outbound_log(min(n, 50))
            if not rows:
                await self.send(chat_id, "📋 No outbound log entries")
                return
            lines = [f"📋 *OUTBOUND LOG* (last {len(rows)})\n━━━━━━━━━━━━━━━━━━"]
            for r in rows:
                emoji = "✅" if r["result"] == "allowed" else "🚫"
                ts = r.get("timestamp", "?")[:16]
                recip = r.get("recipient_preview", "***")
                reason = r.get("reason", "")[:60]
                code = r.get("code_path", "?")
                lines.append(
                    f"{emoji} `{ts}` {r['channel']} → {recip}\n"
                    f"   {r['result']} — {reason}\n"
                    f"   via: {code}"
                )
            await self.send(chat_id, "\n".join(lines))
        except Exception as e:
            await self.send(chat_id, f"❌ Error: {e}")

    async def _show_audit(self, chat_id):
        """Verify hash chain integrity."""
        try:
            from core.security import get_security_gate
            gate = get_security_gate()
            valid, detail = gate.verify_audit_chain()
            if valid:
                await self.send(chat_id, f"✅ *AUDIT PASSED*\n{detail}")
            else:
                await self.send(chat_id, f"🚨 *AUDIT FAILED*\n{detail}")
        except Exception as e:
            await self.send(chat_id, f"❌ Audit error: {e}")

    def set_context(self, chat_id: str, approval_id: int = None, lead_id: int = None,
                    channel: str = None, action: str = None):
        """Set the conversation context for a chat so text replies are routed correctly."""
        self._pending_context[chat_id] = {
            "approval_id": approval_id,
            "lead_id": lead_id,
            "channel": channel,
            "action": action,
        }

    async def stop(self):
        self._running = False


    # ── B2B Cold Email Methods ──────────────────────────────────────────────────

    async def _show_b2b_status(self, chat_id: str):
        """Show B2B outreach summary stats."""
        try:
            from core.cold_email_digest import get_batch_stats
            stats = get_batch_stats()
            text = (
                "📊 *B2B Outreach Status*\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                f"📋 Total leads: *{stats['total']}*\n"
                f"📨 Emails sent: *{stats['sent']}*\n"
                f"💬 Replies: *{stats['replied']}*\n"
                f"⏳ Pending approval: *{stats['pending_approval']}*\n"
                f"🚫 Denied: *{stats['denied']}*\n"
                f"📭 Not yet sent: *{stats['not_sent']}*\n"
                f"📵 No email on file: *{stats['no_email']}*\n"
                f"🔴 Do not contact: *{stats['do_not_contact']}*\n"
                f"✅ Eligible for next batch: *{stats['eligible_next_batch']}*\n\n"
                "*By Category:*\n"
            )
            for cat, data in stats.get("by_category", {}).items():
                text += f"  {cat}: {data['sent']}/{data['total']} sent\n"
            await self.send(chat_id, text)
        except Exception as e:
            await self.send(chat_id, f"❌ B2B status error: {e}")

    async def _generate_b2b_batch(self, chat_id: str, batch_size: int = 10):
        """Generate batch of B2B emails and send for approval."""
        try:
            from core.cold_email_digest import generate_batch
            batch = generate_batch(batch_size)

            if not batch:
                await self.send(chat_id,
                    "📭 *No eligible leads for this batch.*\n\n"
                    "All leads either have no email, were already sent, or are do-not-contact.\n"
                    "Use /b2b\\_status to see the breakdown."
                )
                return

            await self.send(chat_id,
                f"📬 *B2B Batch Generated* — {len(batch)} emails ready for review\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"Sending previews now. Tap APPROVE or DENY for each."
            )

            for lead in batch:
                await self._send_b2b_approval(chat_id, lead)
                await asyncio.sleep(0.5)  # Rate limit

        except Exception as e:
            await self.send(chat_id, f"❌ Batch generation error: {e}")
            import traceback
            traceback.print_exc()

    async def _send_b2b_approval(self, chat_id: str, lead: dict):
        """Send lead verification + email preview with approval buttons."""
        # Step 1: Lead verification message (no buttons)
        phone = lead.get("phone") or "Not on file"
        website = lead.get("website") or "Not on file"
        verification = (
            "🔍 LEAD VERIFICATION\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"Business: {lead['business_name']}\n"
            f"Phone: {phone}\n"
            f"Website: {website}\n"
            f"Category: {lead['category']}\n"
            f"City: {lead['city']} ({lead['distance_miles']} mi — Tier {lead['pricing_tier']})\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Review this lead before the email preview below."
        )
        await self.send(chat_id, verification)
        await asyncio.sleep(0.5)

        # Step 2: Email preview with APPROVE/DENY buttons
        text = (
            "📧 *NEW B2B EMAIL FOR APPROVAL*\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"*To:* {lead['business_name']}\n"
            f"*Email:* {lead['email']}\n"
            f"*Category:* {lead['category']}\n"
            f"*Rating:* {lead['rating']}\n\n"
            f"--- EMAIL PREVIEW ---\n"
            f"*Subject:* {lead['subject']}\n\n"
            f"{lead['plain_body']}\n"
            f"--- END PREVIEW ---"
        )

        buttons = {
            "inline_keyboard": [
                [
                    {"text": "✅ APPROVE", "callback_data": f"b2b_approve:{lead['id']}"},
                    {"text": "❌ DENY", "callback_data": f"b2b_deny:{lead['id']}"},
                ]
            ]
        }

        msg_id = await self.send(chat_id, text, reply_markup=buttons)
        if msg_id:
            try:
                from core.b2b_leads import update_lead
                update_lead(lead["id"], {"telegram_message_id": msg_id})
            except Exception:
                pass

    async def _show_b2b_lead(self, chat_id: str, lead_id: int):
        """Show full details for a single B2B lead."""
        try:
            from core.b2b_leads import get_lead
            lead = get_lead(lead_id)
            if not lead:
                await self.send(chat_id, f"❌ B2B lead #{lead_id} not found.")
                return

            text = (
                f"🏢 *B2B Lead #{lead['id']}*\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"*Business:* {lead['business_name']}\n"
                f"*Category:* {lead['category']}\n"
                f"*City:* {lead['city']}, {lead['state']}\n"
                f"*Distance:* {lead['distance_miles']} mi (Tier {lead['pricing_tier']})\n"
                f"*Rating:* {lead['rating']}\n"
                f"*Section:* {lead['section']}\n\n"
                f"*Contact:* {lead['contact_name'] or 'N/A'}\n"
                f"*Email:* {lead['email'] or 'N/A'}\n"
                f"*Phone:* {lead['phone'] or 'N/A'}\n"
                f"*Website:* {lead['website'] or 'N/A'}\n\n"
                f"*Email Status:* {lead['email_status']}\n"
                f"*Outcome:* {lead['outcome']}\n"
                f"*Approval:* {lead['telegram_approval_status'] or 'N/A'}\n"
            )
            if lead["email_sent_at"]:
                text += f"*Sent at:* {lead['email_sent_at']}\n"
            if lead["reply_content"]:
                text += f"\n*Reply:* {lead['reply_content'][:300]}\n"
            if lead["notes"]:
                text += f"\n*Notes:* {lead['notes']}\n"

            await self.send(chat_id, text)
        except Exception as e:
            await self.send(chat_id, f"❌ Error: {e}")

    async def _search_b2b(self, chat_id: str, term: str):
        """Search B2B leads by business name or city."""
        try:
            from core.b2b_leads import search_leads
            results = search_leads(term)
            if not results:
                await self.send(chat_id, f"🔍 No B2B leads found for \"{term}\"")
                return

            text = f"🔍 *B2B Search: \"{term}\"* — {len(results)} results\n━━━━━━━━━━━━━━━━━━\n\n"
            for r in results[:15]:
                status_icon = {"not_sent": "⬜", "pending_approval": "⏳", "sent": "📨", "replied": "💬"}.get(r["email_status"], "❓")
                text += (
                    f"{status_icon} *#{r['id']}* {r['business_name']}\n"
                    f"   {r['category']} | {r['city']} | Tier {r['pricing_tier']} | {r['rating']}\n"
                    f"   Email: {r['email'] or 'N/A'}\n\n"
                )
            if len(results) > 15:
                text += f"... and {len(results) - 15} more\n"
            await self.send(chat_id, text)
        except Exception as e:
            await self.send(chat_id, f"❌ Search error: {e}")


    # ── FB Group Scraper Methods ────────────────────────────────────────────────

    async def _handle_fb_command(self, chat_id: str, text: str, text_lower: str):
        """Dispatch /fb_ commands."""
        try:
            if text_lower == "/fb_scrape":
                await self._fb_trigger_scrape(chat_id)
            elif text_lower == "/fb_stop":
                await self._fb_stop_scrape(chat_id)
            elif text_lower == "/fb_prospects" or text_lower.startswith("/fb_prospects "):
                parts = text.split(maxsplit=1)
                category = parts[1].strip() if len(parts) > 1 else None
                await self._fb_show_prospects(chat_id, category)
            elif text_lower == "/fb_today":
                await self._fb_show_today(chat_id)
            elif text_lower.startswith("/fb_lead "):
                try:
                    lead_id = int(text.split()[1])
                    await self._fb_show_lead(chat_id, lead_id)
                except (ValueError, IndexError):
                    await self.send(chat_id, "Usage: /fb\\_lead {id}")
            elif text_lower.startswith("/fb_status "):
                parts = text.split(maxsplit=2)
                if len(parts) >= 3:
                    try:
                        pid = int(parts[1])
                        new_status = parts[2].strip().lower()
                        await self._fb_update_status(chat_id, pid, new_status)
                    except ValueError:
                        await self.send(chat_id, "Usage: /fb\\_status {id} {new|contacted|responded|on\\_referral\\_list|not\\_interested|do\\_not\\_contact}")
                else:
                    await self.send(chat_id, "Usage: /fb\\_status {id} {status}")
            elif text_lower.startswith("/fb_note "):
                parts = text.split(maxsplit=2)
                if len(parts) >= 3:
                    try:
                        pid = int(parts[1])
                        note = parts[2].strip()
                        await self._fb_add_note(chat_id, pid, note)
                    except ValueError:
                        await self.send(chat_id, "Usage: /fb\\_note {id} {text}")
                else:
                    await self.send(chat_id, "Usage: /fb\\_note {id} {text}")
            elif text_lower == "/fb_groups":
                await self._fb_list_groups(chat_id)
            elif text_lower.startswith("/fb_add_group "):
                url = text[len("/fb_add_group "):].strip()
                await self._fb_add_group(chat_id, url)
            elif text_lower.startswith("/fb_remove_group "):
                url = text[len("/fb_remove_group "):].strip()
                await self._fb_remove_group(chat_id, url)
            else:
                await self.send(chat_id, "Unknown /fb\\_ command. Use /start to see available commands.")
        except Exception as e:
            await self.send(chat_id, f"❌ FB command error: {e}")

    async def _fb_trigger_scrape(self, chat_id: str):
        """Signal Mac scraper to start via HTTP."""
        import json as _json
        from pathlib import Path as _P
        cfg_path = _P.home() / ".nexus" / "config.json"
        cfg = _json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
        mac_url = cfg.get("fb_scraper_mac_url", "http://localhost:7899")
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(f"{mac_url}/start_scrape")
                if r.status_code == 200:
                    await self.send(chat_id,
                        "🚀 Starting Facebook group scrape on your Mac.\n"
                        "I'll notify you as vendors are found."
                    )
                else:
                    await self.send(chat_id, f"⚠️ Mac scraper responded with status {r.status_code}")
        except Exception:
            await self.send(chat_id,
                "⚠️ Could not reach Mac scraper.\n"
                "Make sure the scraper listener is running on your Mac."
            )

    async def _fb_stop_scrape(self, chat_id: str):
        """Signal Mac scraper to stop gracefully."""
        import json as _json
        from pathlib import Path as _P
        cfg_path = _P.home() / ".nexus" / "config.json"
        cfg = _json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
        mac_url = cfg.get("fb_scraper_mac_url", "http://localhost:7899")
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(f"{mac_url}/stop_scrape")
                if r.status_code == 200:
                    await self.send(chat_id, "🛑 Stopping Facebook scraper after current action completes.")
                else:
                    await self.send(chat_id, f"⚠️ Mac scraper responded with status {r.status_code}")
        except Exception:
            await self.send(chat_id, "⚠️ Could not reach Mac scraper.")

    async def _fb_show_prospects(self, chat_id: str, category: str = None):
        """Show FB vendor prospect summary."""
        from core.fb_scraper_api import get_stats, get_prospects
        stats = get_stats()
        by_status = stats.get("by_status", {})
        text = (
            "📊 *FB Vendor Prospects*\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"📋 Total: *{stats['total']}*\n"
            f"🆕 New: *{by_status.get('new', 0)}*\n"
            f"📞 Contacted: *{by_status.get('contacted', 0)}*\n"
            f"💬 Responded: *{by_status.get('responded', 0)}*\n"
            f"⭐ On referral list: *{by_status.get('on_referral_list', 0)}*\n"
            f"🔍 Today: *{stats['today']}*\n\n"
        )
        if category:
            prospects = get_prospects(category=category)
            text += f"*Filtered by: {category}* — {len(prospects)} results\n\n"
            for p in prospects[:15]:
                text += (
                    f"#{p['id']} {p['poster_name']}"
                    f"{' — ' + p['business_name'] if p.get('business_name') else ''}\n"
                    f"   {p.get('city', '?')} | {p.get('status', 'new')}\n\n"
                )
            if len(prospects) > 15:
                text += f"... and {len(prospects) - 15} more\n"
        else:
            by_cat = stats.get("by_category", {})
            if by_cat:
                text += "*By Category:*\n"
                for cat, cnt in by_cat.items():
                    text += f"  {cat}: {cnt}\n"
        await self.send(chat_id, text)

    async def _fb_show_today(self, chat_id: str):
        """Show vendors found today."""
        from core.fb_scraper_api import get_today_prospects
        prospects = get_today_prospects()
        if not prospects:
            await self.send(chat_id, "📭 No vendors found today yet.")
            return
        text = f"📋 *Today's FB Vendors* — {len(prospects)} found\n━━━━━━━━━━━━━━━━━━\n\n"
        for p in prospects[:20]:
            post_icon = "🏪" if p.get("post_type") == "vendor_promotion" else "🎯"
            text += (
                f"{post_icon} *#{p['id']}* {p['poster_name']}"
                f"{' — ' + p['business_name'] if p.get('business_name') else ''}\n"
                f"   {p.get('category', '?')} | {p.get('city', '?')}\n"
                f"   {(p.get('post_content', '') or '')[:80]}...\n\n"
            )
        if len(prospects) > 20:
            text += f"... and {len(prospects) - 20} more\n"
        await self.send(chat_id, text)

    async def _fb_show_lead(self, chat_id: str, lead_id: int):
        """Show full detail for one FB prospect."""
        from core.fb_scraper_api import get_prospect
        p = get_prospect(lead_id)
        if not p:
            await self.send(chat_id, f"❌ Prospect #{lead_id} not found.")
            return
        post_icon = "🏪" if p.get("post_type") == "vendor_promotion" else "🎯"
        text = (
            f"{post_icon} *FB Prospect #{p['id']}*\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"*Name:* {p['poster_name']}\n"
            f"*Business:* {p.get('business_name') or 'Unknown'}\n"
            f"*Category:* {p.get('category') or 'Unknown'}\n"
            f"*City:* {p.get('city') or 'Unknown'}\n"
            f"*Phone:* {p.get('phone') or 'Not found'}\n"
            f"*Email:* {p.get('email') or 'Not found'}\n"
            f"*Website:* {p.get('website') or 'Not found'}\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"*Status:* {p.get('status', 'new')}\n"
            f"*Contact Method:* {p.get('contact_method', 'none')}\n"
            f"*Notes:* {p.get('notes') or '—'}\n"
            f"*Follow-up:* {p.get('follow_up_date') or '—'}\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"*Group:* {p.get('group_name', '?')}\n"
            f"*Post Type:* {'Vendor Promotion' if p.get('post_type') == 'vendor_promotion' else 'Event Lead'}\n"
            f"*Profile:* {p.get('profile_url', '—')}\n"
            f"*Scraped:* {p.get('scraped_at', '?')}\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"*Post:*\n{(p.get('post_content', '') or '')[:500]}\n"
        )
        if p.get("about"):
            text += f"\n*About:*\n{p['about'][:300]}\n"
        await self.send(chat_id, text)

    async def _fb_update_status(self, chat_id: str, prospect_id: int, new_status: str):
        """Update a prospect's status."""
        from core.fb_scraper_api import VALID_STATUSES, update_prospect, get_prospect
        if new_status not in VALID_STATUSES:
            await self.send(chat_id, f"❌ Invalid status. Use: {', '.join(sorted(VALID_STATUSES))}")
            return
        ok = update_prospect(prospect_id, {"status": new_status})
        if ok:
            p = get_prospect(prospect_id)
            name = p['poster_name'] if p else f"#{prospect_id}"
            await self.send(chat_id, f"✅ Updated #{prospect_id} ({name}) → *{new_status}*")
        else:
            await self.send(chat_id, f"❌ Could not update #{prospect_id}")

    async def _fb_add_note(self, chat_id: str, prospect_id: int, note: str):
        """Append a note to a prospect."""
        from core.fb_scraper_api import get_prospect, update_prospect
        p = get_prospect(prospect_id)
        if not p:
            await self.send(chat_id, f"❌ Prospect #{prospect_id} not found.")
            return
        from datetime import datetime, timezone
        existing = p.get("notes", "") or ""
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        new_notes = f"{existing}\n[{timestamp}] {note}".strip()
        update_prospect(prospect_id, {"notes": new_notes})
        await self.send(chat_id, f"📝 Note added to #{prospect_id} ({p['poster_name']})")

    async def _fb_list_groups(self, chat_id: str):
        """List groups currently being scraped."""
        import json as _json
        from pathlib import Path as _P
        cfg_path = _P.home() / ".nexus" / "config.json"
        cfg = _json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
        mac_url = cfg.get("fb_scraper_mac_url", "http://localhost:7899")
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"{mac_url}/groups")
                if r.status_code == 200:
                    groups = r.json().get("groups", [])
                    if not groups:
                        await self.send(chat_id, "📭 No groups configured. Use /fb\\_add\\_group {url} to add one.")
                        return
                    text = f"📋 *FB Scrape Groups* — {len(groups)} active\n━━━━━━━━━━━━━━━━━━\n\n"
                    for i, g in enumerate(groups, 1):
                        text += (
                            f"{i}. *{g.get('name', 'Unknown')}*\n"
                            f"   {g.get('url', '')}\n"
                            f"   Members: {g.get('member_count', '?')} | Posts scraped: {g.get('posts_scraped', 0)}\n\n"
                        )
                    await self.send(chat_id, text)
                else:
                    await self.send(chat_id, f"⚠️ Mac scraper responded with status {r.status_code}")
        except Exception:
            await self.send(chat_id, "⚠️ Could not reach Mac scraper. Make sure the listener is running.")

    async def _fb_add_group(self, chat_id: str, url: str):
        """Add a group URL to the scraper."""
        if "facebook.com/groups/" not in url:
            await self.send(chat_id, "❌ Invalid URL. Must be a Facebook group URL (facebook.com/groups/...)")
            return
        import json as _json
        from pathlib import Path as _P
        cfg_path = _P.home() / ".nexus" / "config.json"
        cfg = _json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
        mac_url = cfg.get("fb_scraper_mac_url", "http://localhost:7899")
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(f"{mac_url}/add_group", json={"url": url})
                if r.status_code == 200:
                    data = r.json()
                    name = data.get("name", "group")
                    await self.send(chat_id, f"✅ Added *{name}* to scrape list.")
                else:
                    await self.send(chat_id, f"⚠️ Mac scraper responded with status {r.status_code}")
        except Exception:
            await self.send(chat_id, "⚠️ Could not reach Mac scraper. Make sure the listener is running.")

    async def _fb_remove_group(self, chat_id: str, url: str):
        """Remove a group URL from the scraper."""
        import json as _json
        from pathlib import Path as _P
        cfg_path = _P.home() / ".nexus" / "config.json"
        cfg = _json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
        mac_url = cfg.get("fb_scraper_mac_url", "http://localhost:7899")
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(f"{mac_url}/remove_group", json={"url": url})
                if r.status_code == 200:
                    await self.send(chat_id, "✅ Group removed from scrape list.")
                else:
                    await self.send(chat_id, f"⚠️ Mac scraper responded with status {r.status_code}")
        except Exception:
            await self.send(chat_id, "⚠️ Could not reach Mac scraper.")


# ── Module-level singleton ────────────────────────────────────────────────────

_bot: Optional[TelegramBot] = None


def get_bot() -> Optional[TelegramBot]:
    return _bot


def init_bot(token, chat_ids) -> TelegramBot:
    global _bot
    _bot = TelegramBot(token, chat_ids)
    return _bot


# ── Approval Reminder Background Loop ────────────────────────────────────────

async def run_approval_reminders():
    """Background loop: sends reminders for unapproved messages.
    - After 2 hours: first reminder
    - After 24 hours: final reminder + mark 'needs_attention'
    Runs every 15 minutes.
    """
    import traceback
    while True:
        try:
            if not _bot:
                await asyncio.sleep(60)
                continue

            from core.approval_queue import get_needs_reminder, increment_reminder
            reminders = get_needs_reminder()

            for approval in reminders:
                is_final = approval.get("reminder_count", 0) >= 1
                text = _bot.format_reminder(approval, is_final=is_final)

                for cid in _bot.allowed:
                    await _bot.send(cid, text)
                    # Set context so "yes"/"skip" replies work
                    _bot.set_context(cid, approval_id=approval["id"])

                increment_reminder(approval["id"])

                if is_final:
                    # Mark as needs_attention in the DB
                    import sqlite3 as _sql
                    from pathlib import Path as _P
                    db = _sql.connect(str(_P.home() / ".nexus" / "memory.db"))
                    db.execute(
                        "UPDATE leads SET status='needs_attention', updated_at=datetime('now') "
                        "WHERE id=? AND status='awaiting_approval'",
                        (approval["lead_id"],)
                    )
                    db.commit()
                    db.close()

                # Small delay between reminders to avoid Telegram rate limits
                await asyncio.sleep(1)

        except Exception as e:
            print(f"[TG] Reminder loop error: {e}")
            traceback.print_exc()

        await asyncio.sleep(900)  # Every 15 minutes


# ── Standalone entry point (for launchd / manual run) ────────────────────────

if __name__ == "__main__":
    import sys
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
    )

    cfg_path = Path.home() / ".nexus" / "config.json"
    if not cfg_path.exists():
        print("[TG] FATAL: ~/.nexus/config.json not found")
        sys.exit(1)

    cfg = json.loads(cfg_path.read_text())
    token = cfg.get("telegram_token", "")
    chat_ids = cfg.get("telegram_chat_ids", [])

    if not token:
        print("[TG] FATAL: telegram_token not set in config.json")
        sys.exit(1)
    if not chat_ids:
        print("[TG] WARNING: telegram_chat_ids is empty — bot won't respond to anyone")

    print(f"[TG] Starting bot (chats={chat_ids})")
    bot = init_bot(token, chat_ids)

    try:
        from core.agent_runtime import AgentRuntime
        _rt = AgentRuntime("telegram-bot", "d0_core",
                           launchd_label="com.zoar.telegram-bot")
    except Exception:
        _rt = None

    asyncio.run(bot.start_polling())
