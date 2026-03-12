"""
Follow-Up Engine -- Automated follow-up sequence manager for Zoar Bathroom Rentals.

CRITICAL: This engine NEVER sends messages directly. It ONLY creates approval
proposals that Kai approves via Telegram before anything is sent.

Flow:
  1.  Lead enters a sequence (new_lead, post_quote, post_booking, review_request)
  2.  Engine checks ``next_send_at`` every 15 minutes via ``process_pending()``
  3.  When a step is due, the template is filled with lead data
  4.  An entry is created in ``message_approvals`` (status='pending')
  5.  Kai sees it in Telegram, approves / edits / skips
  6.  On approval the send happens (handled by approval_queue + pipeline)
  7.  Engine advances to the next step only after approval-based send

Database: ~/.nexus/memory.db
Tables:  follow_up_sequences, follow_up_templates, message_approvals (existing)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

DB_PATH = Path.home() / ".nexus" / "memory.db"


# -- Helpers -------------------------------------------------------------------

def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _future_hours(hours: int) -> str:
    return (datetime.utcnow() + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


def _get_conn(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _parse_datetime(dt_str: str):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(dt_str.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


# ==============================================================================
# FollowUpEngine
# ==============================================================================

class FollowUpEngine:
    """
    Manages multi-step follow-up sequences for leads.

    Every outbound message goes through the approval queue -- the engine
    **never** sends anything autonomously.

    Usage::

        engine = FollowUpEngine()
        engine.start_sequence(lead_id=42, sequence_type="new_lead")
        proposals = engine.process_pending()   # called by scheduler every 15 min
    """

    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        self._ensure_tables()
        self._seed_templates()

    # -- Schema ----------------------------------------------------------------

    def _ensure_tables(self):
        """Create follow_up_sequences and follow_up_templates tables."""
        conn = sqlite3.connect(str(self.db_path))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS follow_up_sequences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id INTEGER NOT NULL,
                sequence_type TEXT DEFAULT 'new_lead',
                step INTEGER DEFAULT 1,
                status TEXT DEFAULT 'active',
                next_send_at TEXT,
                last_sent_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS follow_up_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sequence_type TEXT NOT NULL,
                step INTEGER NOT NULL,
                delay_hours INTEGER NOT NULL,
                template TEXT NOT NULL,
                language TEXT DEFAULT 'en',
                channel TEXT DEFAULT 'sms'
            );

            CREATE INDEX IF NOT EXISTS idx_fseq_lead ON follow_up_sequences(lead_id);
            CREATE INDEX IF NOT EXISTS idx_fseq_status ON follow_up_sequences(status);
            CREATE INDEX IF NOT EXISTS idx_fseq_next ON follow_up_sequences(next_send_at);
            CREATE INDEX IF NOT EXISTS idx_ftpl_type ON follow_up_templates(sequence_type, step);
        """)
        conn.commit()
        conn.close()
        print("[FollowUp] Database tables ready")

    # -- Template seeding ------------------------------------------------------

    def _seed_templates(self):
        """Insert all template rows if the table is empty. Idempotent."""
        conn = sqlite3.connect(str(self.db_path))
        count = conn.execute("SELECT COUNT(*) FROM follow_up_templates").fetchone()[0]
        if count > 0:
            conn.close()
            return  # Already seeded

        templates = []

        # ===== new_lead (English, 7 touches) ==================================
        templates.extend([
            ("new_lead", 1, 0, "sms", "en",
             "Hi {name}! This is Kai from Zoar Bathroom Rentals \U0001f6bf\u2728 "
             "Thanks for reaching out about luxury restroom trailers for your "
             "{event_type} on {date}! When's a good time for a quick chat?"),

            ("new_lead", 2, 1, "sms", "en",
             "Hey {name}, just wanted to make sure you got my message! Our luxury "
             "trailers include AC, running water, premium finishes & music \u2014 "
             "basically a 5-star bathroom for your guests. Happy to answer any "
             "questions! \U0001f4f1"),

            ("new_lead", 3, 24, "sms", "en",
             "Hi {name}! Our luxury restroom trailers are really popular for "
             "{month} events in the San Fernando Valley area. Want me to send you "
             "some photos and pricing?"),

            ("new_lead", 4, 72, "sms", "en",
             "Hi {name}! Quick reminder about what's included with our luxury "
             "trailer: \u2705 4 private stalls with flushing toilets \u2705 AC/heat "
             "\u2705 Running hot & cold water \u2705 LED lighting & mirrors "
             "\u2705 Bluetooth speaker \u2705 Delivery, setup & pickup included. "
             "All for $1,100. Want me to check availability for {date}?"),

            ("new_lead", 5, 168, "sms", "en",
             "{month} dates book up fast. We still have availability for {date}. "
             "Would you like me to pencil you in? No obligation \u2014 just holds "
             "your spot \U0001f60a"),

            ("new_lead", 6, 336, "sms", "en",
             "Our clients always say our luxury trailers are the surprise hit of "
             "their event \U0001f602\U0001f389 Want to see some photos from a "
             "recent {event_type} we serviced?"),

            ("new_lead", 7, 720, "sms", "en",
             "This is my last check-in \u2014 don't want to bug you! If anything "
             "changes, reply anytime. Wishing you an amazing {event_type}! "
             "\U0001f38a -Kai, Zoar Bathroom Rentals. Reply STOP to opt out."),
        ])

        # ===== new_lead_es (Spanish, 7 touches) ==============================
        templates.extend([
            ("new_lead_es", 1, 0, "sms", "es",
             "\u00a1Hola {name}! Soy Kai de Zoar Bathroom Rentals \U0001f6bf\u2728 "
             "Gracias por escribirnos sobre nuestros trailers de ba\u00f1os de lujo "
             "para tu {event_type} el {date}. \u00bfCu\u00e1ndo te conviene una "
             "llamada r\u00e1pida?"),

            ("new_lead_es", 2, 1, "sms", "es",
             "\u00a1Hola {name}! Solo quer\u00eda asegurarme de que recibiste mi "
             "mensaje. Nuestros trailers de lujo incluyen aire acondicionado, agua "
             "corriente, acabados premium y m\u00fasica \u2014 b\u00e1sicamente un "
             "ba\u00f1o 5 estrellas para tus invitados. \u00a1Con gusto respondo "
             "cualquier pregunta! \U0001f4f1"),

            ("new_lead_es", 3, 24, "sms", "es",
             "\u00a1Hola {name}! Nuestros trailers de ba\u00f1os de lujo son muy "
             "populares para eventos de {month} en el \u00e1rea del Valle de San "
             "Fernando. \u00bfQuieres que te mande fotos y precios?"),

            ("new_lead_es", 4, 72, "sms", "es",
             "\u00a1Hola {name}! Te recuerdo lo que incluye nuestro trailer de lujo: "
             "\u2705 4 ba\u00f1os privados con inodoros \u2705 Aire acondicionado/calefacci\u00f3n "
             "\u2705 Agua caliente y fr\u00eda \u2705 Iluminaci\u00f3n LED y espejos "
             "\u2705 Bocina Bluetooth \u2705 Entrega, instalaci\u00f3n y recogida incluidas. "
             "Todo por $1,100. \u00bfQuieres que verifique disponibilidad para el {date}?"),

            ("new_lead_es", 5, 168, "sms", "es",
             "Las fechas de {month} se llenan r\u00e1pido. Todav\u00eda tenemos "
             "disponibilidad para el {date}. \u00bfQuieres que te aparte la fecha? "
             "Sin compromiso \u2014 solo reserva tu lugar \U0001f60a"),

            ("new_lead_es", 6, 336, "sms", "es",
             "Nuestros clientes siempre dicen que nuestros trailers de lujo son la "
             "sorpresa favorita de su evento \U0001f602\U0001f389 \u00bfQuieres ver "
             "fotos de un(a) {event_type} reciente que atendimos?"),

            ("new_lead_es", 7, 720, "sms", "es",
             "Este es mi \u00faltimo mensaje \u2014 \u00a1no quiero molestarte! Si "
             "algo cambia, escr\u00edbeme cuando quieras. \u00a1Te deseo un(a) "
             "{event_type} incre\u00edble! \U0001f38a -Kai, Zoar Bathroom Rentals. "
             "Responde STOP para no recibir m\u00e1s mensajes."),
        ])

        # ===== post_quote (3 touches) =========================================
        templates.extend([
            ("post_quote", 1, 24, "sms", "en",
             "Hi {name}! Just following up on the quote I sent over for your "
             "{event_type}. Any questions about pricing or what's included? "
             "Happy to jump on a quick call \U0001f4de"),

            ("post_quote", 2, 72, "sms", "en",
             "Hey {name}! Wanted to check if you had a chance to review the quote "
             "for your {event_type} on {date}. We'd love to be part of your event! "
             "Let me know if you'd like to lock in the date \U0001f60a"),

            ("post_quote", 3, 168, "sms", "en",
             "Hi {name}, just a gentle nudge about the quote for your {event_type}. "
             "Our {month} calendar is filling up quickly. If you're still interested, "
             "I can hold your date with a small deposit. No pressure at all! "
             "\U0001f64f"),
        ])

        # ===== post_booking (3 touches) =======================================
        templates.extend([
            ("post_booking", 1, 0, "sms", "en",
             "\U0001f389 Awesome, {name}! You're officially booked for {date}! "
             "We'll take care of everything \u2014 delivery, setup, and pickup. "
             "I'll send you a confirmation with all the details. Reach out anytime "
             "if you have questions!"),

            ("post_booking", 2, 720, "sms", "en",
             "Hi {name}! Your {event_type} is coming up in about a month on {date}! "
             "\U0001f389 Just checking in \u2014 do you have any questions about "
             "setup, placement, or logistics? We want everything to be perfect!"),

            ("post_booking", 3, 1344, "sms", "en",
             "Hi {name}! Your {event_type} is just a week away! \U0001f31f Here's "
             "what to expect: we'll deliver and set up the trailer the morning of "
             "{date}. Please make sure there's a flat, accessible spot for the "
             "trailer. We'll handle the rest! Can't wait \U0001f60a"),
        ])

        # ===== review_request (2 touches) =====================================
        templates.extend([
            ("review_request", 1, 24, "sms", "en",
             "Hi {name}! Hope your {event_type} was amazing! \U0001f389 We'd love "
             "to hear how everything went. If you have a moment, a quick Google "
             "review would mean the world to us: {review_link} Thank you so much! "
             "\U0001f64f\u2728"),

            ("review_request", 2, 72, "sms", "en",
             "Hey {name}! Just a quick follow-up \u2014 if you enjoyed our luxury "
             "restroom trailer at your {event_type}, we'd really appreciate a "
             "review! It helps other event planners find us. Here's the link: "
             "{review_link} Thank you! \U0001f60a"),
        ])

        # Insert all templates
        for tpl in templates:
            conn.execute(
                "INSERT INTO follow_up_templates "
                "(sequence_type, step, delay_hours, channel, language, template) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                tpl,
            )
        conn.commit()
        conn.close()
        print(f"[FollowUp] Seeded {len(templates)} follow-up templates")

    # -- Sequence lifecycle ----------------------------------------------------

    def start_sequence(self, lead_id: int, sequence_type: str = "new_lead") -> dict:
        """Start a follow-up sequence for a lead.

        Returns dict with sequence id and status, or error if lead already
        has an active sequence.
        """
        conn = _get_conn(self.db_path)
        try:
            # Check for existing active sequence
            existing = conn.execute(
                "SELECT id, sequence_type, step FROM follow_up_sequences "
                "WHERE lead_id = ? AND status = 'active'",
                (lead_id,),
            ).fetchone()
            if existing:
                return {
                    "ok": False,
                    "error": f"Lead #{lead_id} already has active sequence "
                             f"'{existing['sequence_type']}' (step {existing['step']})",
                    "existing_id": existing["id"],
                }

            # Get first template to compute next_send_at
            first_tpl = conn.execute(
                "SELECT delay_hours FROM follow_up_templates "
                "WHERE sequence_type = ? AND step = 1",
                (sequence_type,),
            ).fetchone()

            if not first_tpl:
                return {"ok": False,
                        "error": f"No templates found for sequence '{sequence_type}'"}

            delay = first_tpl["delay_hours"]
            next_send = _future_hours(delay)

            cursor = conn.execute(
                "INSERT INTO follow_up_sequences "
                "(lead_id, sequence_type, step, status, next_send_at, created_at) "
                "VALUES (?, ?, 1, 'active', ?, ?)",
                (lead_id, sequence_type, next_send, _now()),
            )
            seq_id = cursor.lastrowid
            conn.commit()

            print(f"[FollowUp] Started '{sequence_type}' sequence for lead #{lead_id} "
                  f"(seq #{seq_id}, first send at {next_send})")
            return {"ok": True, "sequence_id": seq_id, "lead_id": lead_id,
                    "sequence_type": sequence_type, "next_send_at": next_send}

        except Exception as e:
            print(f"[FollowUp] Error starting sequence: {e}")
            return {"ok": False, "error": str(e)}
        finally:
            conn.close()

    def get_pending_followups(self) -> list[dict]:
        """Find sequences where next_send_at <= now and status='active'."""
        conn = _get_conn(self.db_path)
        try:
            now = _now()
            rows = conn.execute(
                "SELECT s.*, l.first_name, l.last_name, l.phone, l.email, "
                "l.source, l.status AS lead_status, l.event_type, l.event_date, "
                "l.guest_count, l.booking_status "
                "FROM follow_up_sequences s "
                "JOIN leads l ON s.lead_id = l.id "
                "WHERE s.status = 'active' AND s.next_send_at IS NOT NULL "
                "AND s.next_send_at <= ?",
                (now,),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            print(f"[FollowUp] Error fetching pending: {e}")
            return []
        finally:
            conn.close()

    def generate_message(self, sequence_id: int) -> dict:
        """Fill the current step's template with lead data.

        Returns::

            {
                "lead_id": int,
                "channel": str,
                "message": str,          # filled template
                "template_step": int,
                "sequence_type": str,
            }
        """
        conn = _get_conn(self.db_path)
        try:
            seq = conn.execute(
                "SELECT * FROM follow_up_sequences WHERE id = ?", (sequence_id,)
            ).fetchone()
            if not seq:
                return {"error": f"Sequence #{sequence_id} not found"}

            lead = conn.execute(
                "SELECT * FROM leads WHERE id = ?", (seq["lead_id"],)
            ).fetchone()
            if not lead:
                return {"error": f"Lead #{seq['lead_id']} not found"}

            tpl = conn.execute(
                "SELECT * FROM follow_up_templates "
                "WHERE sequence_type = ? AND step = ?",
                (seq["sequence_type"], seq["step"]),
            ).fetchone()
            if not tpl:
                return {"error": f"No template for {seq['sequence_type']} step {seq['step']}"}

            ld = dict(lead)
            message = self._fill_template(tpl["template"], ld)

            return {
                "lead_id": seq["lead_id"],
                "channel": tpl["channel"],
                "message": message,
                "template_step": seq["step"],
                "sequence_type": seq["sequence_type"],
                "sequence_id": sequence_id,
            }

        except Exception as e:
            print(f"[FollowUp] Error generating message: {e}")
            return {"error": str(e)}
        finally:
            conn.close()

    def create_approval_proposal(self, sequence_id: int) -> dict:
        """Create a ``message_approvals`` entry for Kai to approve via Telegram.

        **DOES NOT SEND.** Only creates the approval row.

        Returns::

            {
                "approval_id": int,
                "lead_name": str,
                "message_preview": str,
                "step": int,
            }
        """
        msg_data = self.generate_message(sequence_id)
        if "error" in msg_data:
            return msg_data

        conn = _get_conn(self.db_path)
        try:
            lead = conn.execute(
                "SELECT * FROM leads WHERE id = ?", (msg_data["lead_id"],)
            ).fetchone()
            if not lead:
                return {"error": f"Lead #{msg_data['lead_id']} not found"}

            ld = dict(lead)
            lead_name = (
                ld.get("first_name") or ld.get("full_name") or f"Lead #{ld['id']}"
            )

            # Get last outbound/inbound for context
            last_out, last_in = "", ""
            try:
                msgs = conn.execute(
                    "SELECT direction, content FROM lead_messages "
                    "WHERE lead_id = ? ORDER BY ts DESC LIMIT 20",
                    (msg_data["lead_id"],),
                ).fetchall()
                for m in msgs:
                    if m["direction"] == "outbound" and not last_out:
                        last_out = (m["content"] or "")[:200]
                    if m["direction"] == "inbound" and not last_in:
                        last_in = (m["content"] or "")[:200]
            except Exception:
                pass

            # Respect lead's preferred channel
            preferred = lead.get("preferred_channel", "").strip() if lead else ""
            channel = msg_data["channel"]
            if preferred in ("sms", "email", "facebook_dm", "instagram_dm"):
                channel = preferred

            # Insert into message_approvals
            from core.approval_queue import queue_message

            approval_id = queue_message(
                lead_id=msg_data["lead_id"],
                lead_name=lead_name,
                lead_phone=ld.get("phone", ""),
                lead_email=ld.get("email", ""),
                lead_source=ld.get("source", ""),
                channel=channel,
                message_type="followup",
                proposed_message=msg_data["message"],
                proposed_subject="",
                followup_step=msg_data["template_step"],
                last_outbound=last_out,
                last_inbound=last_in,
            )

            print(f"[FollowUp] Created approval #{approval_id} for lead "
                  f"#{msg_data['lead_id']} ({lead_name}), "
                  f"step {msg_data['template_step']}")

            return {
                "ok": True,
                "approval_id": approval_id,
                "lead_id": msg_data["lead_id"],
                "lead_name": lead_name,
                "message_preview": msg_data["message"][:120],
                "step": msg_data["template_step"],
                "sequence_type": msg_data["sequence_type"],
                "channel": channel,
            }

        except Exception as e:
            print(f"[FollowUp] Error creating approval proposal: {e}")
            return {"ok": False, "error": str(e)}
        finally:
            conn.close()

    def advance_step(self, sequence_id: int) -> dict:
        """After an approved send, advance to the next step and calculate
        ``next_send_at``.  If no more steps, mark the sequence completed."""
        conn = _get_conn(self.db_path)
        try:
            seq = conn.execute(
                "SELECT * FROM follow_up_sequences WHERE id = ?", (sequence_id,)
            ).fetchone()
            if not seq:
                return {"error": f"Sequence #{sequence_id} not found"}

            current_step = seq["step"]
            seq_type = seq["sequence_type"]

            # Check if there's a next step
            next_tpl = conn.execute(
                "SELECT step, delay_hours FROM follow_up_templates "
                "WHERE sequence_type = ? AND step = ?",
                (seq_type, current_step + 1),
            ).fetchone()

            now = _now()

            if next_tpl:
                next_send = _future_hours(next_tpl["delay_hours"])
                conn.execute(
                    "UPDATE follow_up_sequences SET step = ?, next_send_at = ?, "
                    "last_sent_at = ? WHERE id = ?",
                    (current_step + 1, next_send, now, sequence_id),
                )
                conn.commit()
                print(f"[FollowUp] Sequence #{sequence_id}: advanced to step "
                      f"{current_step + 1}, next send at {next_send}")
                return {
                    "ok": True,
                    "sequence_id": sequence_id,
                    "new_step": current_step + 1,
                    "next_send_at": next_send,
                }
            else:
                conn.execute(
                    "UPDATE follow_up_sequences SET status = 'completed', "
                    "last_sent_at = ?, completed_at = ?, next_send_at = NULL "
                    "WHERE id = ?",
                    (now, now, sequence_id),
                )
                conn.commit()
                print(f"[FollowUp] Sequence #{sequence_id}: completed "
                      f"(all {current_step} steps done)")
                return {
                    "ok": True,
                    "sequence_id": sequence_id,
                    "completed": True,
                    "total_steps": current_step,
                }

        except Exception as e:
            print(f"[FollowUp] Error advancing step: {e}")
            return {"ok": False, "error": str(e)}
        finally:
            conn.close()

    def pause_sequence(self, lead_id: int, reason: str = "manual") -> dict:
        """Pause the active sequence for a lead."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            cursor = conn.execute(
                "UPDATE follow_up_sequences SET status = 'paused' "
                "WHERE lead_id = ? AND status = 'active'",
                (lead_id,),
            )
            conn.commit()
            affected = cursor.rowcount
            if affected:
                print(f"[FollowUp] Paused sequence for lead #{lead_id}: {reason}")
                return {"ok": True, "lead_id": lead_id, "reason": reason}
            return {"ok": False, "error": f"No active sequence for lead #{lead_id}"}
        except Exception as e:
            print(f"[FollowUp] Error pausing sequence: {e}")
            return {"ok": False, "error": str(e)}
        finally:
            conn.close()

    def resume_sequence(self, lead_id: int) -> dict:
        """Resume a paused sequence for a lead."""
        conn = _get_conn(self.db_path)
        try:
            seq = conn.execute(
                "SELECT * FROM follow_up_sequences "
                "WHERE lead_id = ? AND status = 'paused'",
                (lead_id,),
            ).fetchone()
            if not seq:
                return {"ok": False,
                        "error": f"No paused sequence for lead #{lead_id}"}

            # Recalculate next_send_at based on current step delay
            tpl = conn.execute(
                "SELECT delay_hours FROM follow_up_templates "
                "WHERE sequence_type = ? AND step = ?",
                (seq["sequence_type"], seq["step"]),
            ).fetchone()

            next_send = _future_hours(tpl["delay_hours"] if tpl else 24)

            conn.execute(
                "UPDATE follow_up_sequences SET status = 'active', "
                "next_send_at = ? WHERE id = ?",
                (next_send, seq["id"]),
            )
            conn.commit()
            print(f"[FollowUp] Resumed sequence for lead #{lead_id}, "
                  f"next send at {next_send}")
            return {"ok": True, "lead_id": lead_id, "next_send_at": next_send}

        except Exception as e:
            print(f"[FollowUp] Error resuming sequence: {e}")
            return {"ok": False, "error": str(e)}
        finally:
            conn.close()

    def cancel_sequence(self, lead_id: int, reason: str = "manual") -> dict:
        """Cancel the active/paused sequence for a lead. Also cancels any
        pending approvals for that lead."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            now = _now()
            cursor = conn.execute(
                "UPDATE follow_up_sequences SET status = 'cancelled', "
                "completed_at = ?, next_send_at = NULL "
                "WHERE lead_id = ? AND status IN ('active', 'paused')",
                (now, lead_id),
            )
            seq_count = cursor.rowcount

            # Also cancel any pending approvals for this lead
            from core.approval_queue import stop_sequence as stop_approvals
            approval_count = stop_approvals(lead_id)

            conn.commit()
            print(f"[FollowUp] Cancelled sequence for lead #{lead_id}: {reason} "
                  f"({seq_count} sequences, {approval_count} approvals cancelled)")
            return {
                "ok": True,
                "lead_id": lead_id,
                "reason": reason,
                "sequences_cancelled": seq_count,
                "approvals_cancelled": approval_count,
            }
        except Exception as e:
            print(f"[FollowUp] Error cancelling sequence: {e}")
            return {"ok": False, "error": str(e)}
        finally:
            conn.close()

    def on_approval_completed(self, approval_id: int) -> dict:
        """Called when Kai approves a follow-up message. Advances the sequence."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT lead_id, sequence_id FROM message_approvals WHERE id=?",
                (approval_id,)
            ).fetchone()
            conn.close()
            if row and row["sequence_id"]:
                return self.advance_step(row["sequence_id"])
            return {"ok": False, "error": "No sequence found for approval"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_sequence_status(self, lead_id: int) -> dict:
        """Get the current follow-up sequence status for a lead.

        Returns the most recent sequence (active > paused > completed > cancelled).
        """
        conn = _get_conn(self.db_path)
        try:
            row = conn.execute(
                "SELECT s.*, "
                "(SELECT COUNT(*) FROM follow_up_templates "
                " WHERE sequence_type = s.sequence_type) AS total_steps "
                "FROM follow_up_sequences s "
                "WHERE s.lead_id = ? "
                "ORDER BY "
                "  CASE s.status "
                "    WHEN 'active' THEN 1 "
                "    WHEN 'paused' THEN 2 "
                "    WHEN 'completed' THEN 3 "
                "    ELSE 4 "
                "  END, "
                "  s.created_at DESC "
                "LIMIT 1",
                (lead_id,),
            ).fetchone()
            if not row:
                return {"lead_id": lead_id, "status": "none",
                        "message": "No sequence found"}
            return dict(row)
        except Exception as e:
            print(f"[FollowUp] Error getting status: {e}")
            return {"lead_id": lead_id, "status": "error", "error": str(e)}
        finally:
            conn.close()

    # -- Scheduler: main processing loop ---------------------------------------

    def process_pending(self) -> list[dict]:
        """Main scheduler method: find all pending follow-ups, generate messages,
        and create approval proposals.

        Called every 15 minutes by the scheduler.
        Returns list of created proposals.

        IMPORTANT: This only creates approvals. Nothing is sent.
        """
        pending = self.get_pending_followups()
        if not pending:
            return []

        proposals = []
        for seq_data in pending:
            lead_id = seq_data["lead_id"]
            seq_id = seq_data["id"]
            lead_status = seq_data.get("lead_status", "")
            booking_status = seq_data.get("booking_status", "")

            # Check stop conditions
            stop_statuses = {"opted_out", "closed"}
            stop_booking = {"booked", "completed", "lost"}

            if lead_status in stop_statuses or booking_status in stop_booking:
                self.cancel_sequence(
                    lead_id,
                    f"auto: lead_status={lead_status}, booking={booking_status}",
                )
                continue

            # Check if lead has replied since sequence started
            last_reply = seq_data.get("last_reply_at") or ""
            created_at = seq_data.get("created_at") or ""
            if last_reply and created_at:
                reply_dt = _parse_datetime(last_reply)
                created_dt = _parse_datetime(created_at)
                if reply_dt and created_dt and reply_dt > created_dt:
                    self.handle_lead_reply(lead_id)
                    continue

            # Create approval proposal
            result = self.create_approval_proposal(seq_id)
            if result.get("ok"):
                proposals.append(result)
            else:
                print(f"[FollowUp] Failed to create proposal for seq #{seq_id}: "
                      f"{result.get('error')}")

        if proposals:
            print(f"[FollowUp] process_pending: created {len(proposals)} proposals")
        return proposals

    def handle_lead_reply(self, lead_id: int) -> dict:
        """When a lead replies, pause their sequence and alert for human takeover.

        This prevents automated follow-ups from stomping on a live conversation.
        """
        result = self.pause_sequence(lead_id, reason="lead_replied")
        if result.get("ok"):
            print(f"[FollowUp] Lead #{lead_id} replied -- sequence paused for "
                  "human takeover")
        return {
            "ok": True,
            "lead_id": lead_id,
            "action": "paused_for_reply",
            "message": f"Lead #{lead_id} replied. Sequence paused for human takeover.",
        }

    # -- Template filling ------------------------------------------------------

    def _fill_template(self, template: str, lead: dict) -> str:
        """Fill a template string with lead data.

        Supported placeholders:
            {name}         - first name or "there"
            {event_type}   - event type or "event"
            {date}         - event date or "your upcoming event"
            {month}        - current month name (e.g. "March")
            {guest_count}  - number of guests or ""
            {review_link}  - Google review link placeholder
        """
        name = (lead.get("first_name") or "").strip()
        if not name:
            name = (lead.get("full_name") or "").strip().split()[0] if lead.get("full_name") else "there"

        event_type = (lead.get("event_type") or "event").strip()
        if not event_type:
            event_type = "event"

        event_date = (lead.get("event_date") or "").strip()
        if not event_date:
            date_display = "your upcoming event"
        else:
            date_display = event_date

        month = datetime.utcnow().strftime("%B")

        guest_count = str(lead.get("guest_count") or "")

        # Google review link (placeholder -- Kai should set this in config)
        review_link = "https://g.page/r/zoarbathroomrentals/review"

        try:
            return template.format(
                name=name,
                event_type=event_type,
                date=date_display,
                month=month,
                guest_count=guest_count,
                review_link=review_link,
            )
        except (KeyError, IndexError) as e:
            print(f"[FollowUp] Template fill error: {e}")
            return template  # Return raw template if fill fails


# -- Module-level singleton ----------------------------------------------------

_engine = None


def get_follow_up_engine(db_path=None) -> FollowUpEngine:
    """Get or create singleton FollowUpEngine instance."""
    global _engine
    if _engine is None:
        _engine = FollowUpEngine(db_path)
    return _engine
