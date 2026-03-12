"""
Nexus Messaging Module
Wraps SMS (via email-to-SMS gateway) and email sending with:
- OUTBOUND KILL SWITCH (must be enabled in config)
- Approval verification (every send needs a valid approval_id)
- Outbound audit log (every attempt logged, sent or blocked)
- Test mode (only send to whitelisted numbers/emails)
- Conversation tracking
- Session management
"""
import asyncio, logging, smtplib, sqlite3, json, traceback
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from datetime import datetime

log = logging.getLogger("messaging")

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"


LOGO_PATH = Path(__file__).parent.parent / "static" / "images" / "zoar_logo.jpg"


def _build_mime_with_logo(subject, from_addr, to_email, plain_body, html_body="", extra_headers=None):
    """Build a MIME message with inline logo CID attachment.
    Falls back to simple alternative if logo file missing."""
    has_logo = LOGO_PATH.exists() and html_body and "cid:zoar_logo" in html_body

    if has_logo:
        # related wraps alternative + inline image
        msg_related = MIMEMultipart("related")
        msg_alt = MIMEMultipart("alternative")
        msg_alt.attach(MIMEText(plain_body, "plain"))
        msg_alt.attach(MIMEText(html_body, "html"))
        msg_related.attach(msg_alt)

        with open(LOGO_PATH, "rb") as f:
            logo = MIMEImage(f.read(), _subtype="jpeg")
        logo.add_header("Content-ID", "<zoar_logo>")
        logo.add_header("Content-Disposition", "inline", filename="zoar_logo.jpg")
        msg_related.attach(logo)
        msg = msg_related
    else:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(plain_body, "plain"))
        if html_body:
            msg.attach(MIMEText(html_body, "html"))

    msg["From"] = from_addr
    msg["To"] = to_email
    msg["Subject"] = subject
    if extra_headers:
        for k, v in extra_headers.items():
            msg[k] = v
    return msg


# ── Outbound Kill Switch + Audit Log ────────────────────────────────────────

def _load_config():
    """Load config.json — cached for the lifetime of the process."""
    try:
        return json.load(open(CONFIG_PATH))
    except Exception:
        return {}

def _is_outbound_enabled() -> bool:
    """Check the kill switch. Returns True ONLY if explicitly enabled."""
    cfg = _load_config()
    return cfg.get("outbound_messages_enabled", False) is True

def _ensure_outbound_log():
    """Create the outbound_log table if it doesn't exist."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS outbound_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT DEFAULT (datetime('now')),
            channel TEXT,
            recipient TEXT,
            recipient_name TEXT,
            message_preview TEXT,
            approval_id INTEGER,
            approved_by TEXT,
            result TEXT,
            reason TEXT,
            code_path TEXT
        )
    """)
    conn.commit()
    conn.close()

def _log_outbound(channel: str, recipient: str, recipient_name: str,
                   message_preview: str, approval_id: int, approved_by: str,
                   result: str, reason: str, code_path: str):
    """Log EVERY outbound attempt to the audit table."""
    try:
        _ensure_outbound_log()
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "INSERT INTO outbound_log (channel, recipient, recipient_name, "
            "message_preview, approval_id, approved_by, result, reason, code_path) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (channel, recipient, recipient_name, message_preview[:200],
             approval_id or 0, approved_by or "", result, reason, code_path)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[OutboundLog] Failed to log: {e}")

def _verify_approval(approval_id: int) -> tuple:
    """Verify an approval exists and was approved by Kai.
    Returns (ok: bool, method: str, reason: str)."""
    if not approval_id:
        return False, "", "no_approval_id"
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status, approval_method FROM message_approvals WHERE id=?",
            (approval_id,)
        ).fetchone()
        conn.close()
        if not row:
            return False, "", f"approval_{approval_id}_not_found"
        if row["status"] != "approved":
            return False, row["status"], f"approval_{approval_id}_status_{row['status']}"
        method = row["approval_method"] or ""
        if method not in ("kai_approved", "kai_edited"):
            return False, method, f"approval_{approval_id}_method_{method}"
        return True, method, "verified"
    except Exception as e:
        return False, "", f"verification_error: {e}"


def _check_blocklist(recipient: str) -> bool:
    """Check if recipient (phone or email) is on the contact blocklist.
    Returns True if BLOCKED (do not send). This is RULE 0 — cannot be overridden."""
    import sqlite3 as _sql
    from pathlib import Path as _P
    try:
        db = _sql.connect(str(_P.home() / ".nexus" / "memory.db"))
        # Strip non-digits for phone comparison
        digits = "".join(c for c in recipient if c.isdigit())
        rows = db.execute(
            "SELECT id FROM contact_blocklist WHERE active = 1 AND (phone = ? OR email = ? OR phone = ?)",
            (recipient, recipient, digits)
        ).fetchall()
        db.close()
        return len(rows) > 0
    except Exception as e:
        log.warning(f"[BLOCKLIST] Check failed for {recipient}: {e} — BLOCKING by default for safety")
        return True  # If blocklist check fails, block by default


def _check_requires_approval(recipient: str) -> bool:
    """Check if the recipient's lead record requires manual Telegram approval.
    Returns True if approval is required (existing contacts)."""
    import sqlite3 as _sql
    from pathlib import Path as _P
    try:
        db = _sql.connect(str(_P.home() / ".nexus" / "memory.db"))
        digits = "".join(c for c in recipient if c.isdigit())
        rows = db.execute(
            "SELECT requires_manual_approval FROM leads WHERE (phone = ? OR email = ? OR phone = ?) AND requires_manual_approval = 1",
            (recipient, recipient, digits)
        ).fetchall()
        db.close()
        return len(rows) > 0
    except Exception:
        return True  # Default to requiring approval if check fails


def outbound_gate(channel: str, recipient: str, recipient_name: str,
                   message: str, approval_id: int = None,
                   code_path: str = "unknown") -> dict:
    """Central gate for ALL outbound messages — delegates to SecurityGate (10 gates).

    RULE 0: Blocklist check runs FIRST. If blocked, message is rejected immediately.
    Existing contacts require explicit Telegram approval before every outreach.

    Returns {"ok": True, "approved_by": method} if sending is allowed.
    Returns {"ok": False, "reason": reason} if blocked.
    Every attempt is logged with hash-chained audit trail.
    """
    # RULE 0 — Blocklist check (CANNOT BE OVERRIDDEN)
    if _check_blocklist(recipient):
        log.warning(f"[BLOCKLIST] BLOCKED outbound to {recipient} via {channel} — contact is blocklisted")
        _log_outbound(channel, recipient, recipient_name, message[:200],
                      approval_id, "", "blocked", "BLOCKLISTED", code_path)
        return {"ok": False, "reason": "BLOCKLISTED — contact is on permanent blocklist"}

    # RULE 0b — Existing contacts require explicit approval
    if _check_requires_approval(recipient) and not approval_id:
        log.warning(f"[APPROVAL] BLOCKED outbound to {recipient} — requires manual Telegram approval")
        _log_outbound(channel, recipient, recipient_name, message[:200],
                      approval_id, "", "blocked", "requires_manual_approval", code_path)
        return {"ok": False, "reason": "Existing contact requires explicit Telegram approval before outreach"}

    from core.security import get_security_gate
    gate = get_security_gate()
    result = gate.gate_outbound(
        channel=channel,
        recipient=recipient,
        message=message,
        approval_id=approval_id,
        code_path=code_path,
    )
    if result["allowed"]:
        return {"ok": True, "approved_by": result.get("approved_by", "")}
    return {"ok": False, "reason": result["reason"]}

CARRIER_GATEWAYS = {
    "att": "@txt.att.net",
    "tmobile": "@tmomail.net",
    "verizon": "@vtext.com",
    "sprint": "@messaging.sprintpcs.com",
    "cricket": "@mms.cricketwireless.net",
    "metro": "@mymetropcs.com",
    "boost": "@sms.myboostmobile.com",
    "google_fi": "@msg.fi.google.com",
    "virgin": "@vmobl.com",
}

_messenger = None

def init_messenger(gmail: str, app_password: str):
    global _messenger
    _messenger = Messenger(gmail, app_password)
    return _messenger

def get_messenger():
    return _messenger


class Messenger:
    def __init__(self, gmail: str, app_password: str):
        self.gmail = gmail
        self.app_password = app_password
        # Load test_mode from config (default True for safety)
        try:
            import json as _json
            _cfg_path = Path.home() / ".nexus" / "config.json"
            _cfg = _json.loads(_cfg_path.read_text()) if _cfg_path.exists() else {}
            self.test_mode = _cfg.get("messaging_test_mode", True)
        except Exception:
            self.test_mode = True
        self.test_phones: set = set()
        self.test_emails: set = set()
        self._init_db()
        self._load_test_contacts()

    def _init_db(self):
        conn = sqlite3.connect(str(DB_PATH))
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS msg_conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT DEFAULT (datetime('now')),
            channel TEXT DEFAULT 'sms',
            direction TEXT DEFAULT 'outbound',
            contact_name TEXT DEFAULT '',
            contact_phone TEXT DEFAULT '',
            contact_email TEXT DEFAULT '',
            carrier TEXT DEFAULT 'tmobile',
            subject TEXT DEFAULT '',
            message TEXT DEFAULT '',
            status TEXT DEFAULT 'sent',
            error TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS msg_test_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            email TEXT DEFAULT '',
            carrier TEXT DEFAULT 'tmobile',
            added_at TEXT DEFAULT (datetime('now'))
        );
        """)
        conn.commit()
        conn.close()

    def _load_test_contacts(self):
        try:
            conn = sqlite3.connect(str(DB_PATH))
            rows = conn.execute("SELECT phone, email FROM msg_test_contacts").fetchall()
            conn.close()
            for phone, email in rows:
                if phone: self.test_phones.add(_clean_phone(phone))
                if email: self.test_emails.add(email.lower().strip())
        except:
            pass

    def add_test_contact(self, name: str, phone: str, email: str, carrier: str = "tmobile"):
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "INSERT INTO msg_test_contacts (name, phone, email, carrier) VALUES (?,?,?,?)",
            (name, _clean_phone(phone), email.lower().strip(), carrier)
        )
        conn.commit()
        conn.close()
        if phone: self.test_phones.add(_clean_phone(phone))
        if email: self.test_emails.add(email.lower().strip())

    def get_test_contacts(self):
        conn = sqlite3.connect(str(DB_PATH))
        rows = conn.execute("SELECT name, phone, email, carrier FROM msg_test_contacts ORDER BY added_at DESC").fetchall()
        conn.close()
        return [{"name": r[0], "phone": r[1], "email": r[2], "carrier": r[3]} for r in rows]

    def get_conversations(self, limit: int = 50):
        conn = sqlite3.connect(str(DB_PATH))
        rows = conn.execute(
            "SELECT ts, channel, direction, contact_name, contact_phone, contact_email, subject, message, status, error "
            "FROM msg_conversations ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [{"ts": r[0], "channel": r[1], "direction": r[2], "name": r[3], "phone": r[4],
                 "email": r[5], "subject": r[6], "message": r[7], "status": r[8], "error": r[9]} for r in rows]

    def _log_conversation(self, channel, direction, name, phone, email, carrier, subject, message, status, error=""):
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute(
                "INSERT INTO msg_conversations (channel, direction, contact_name, contact_phone, contact_email, carrier, subject, message, status, error) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (channel, direction, name, phone, email, carrier, subject, message, status, error)
            )
            conn.commit()
            conn.close()
        except:
            pass

    async def send_sms(self, phone: str, message: str, carrier: str = "tmobile",
                       name: str = "", approval_id: int = None):
        digits = _clean_phone(phone)
        if len(digits) != 10:
            return {"ok": False, "error": f"Invalid phone number: need 10 digits, got {len(digits)}"}

        # OUTBOUND GATE: Kill switch + approval verification
        gate = outbound_gate("sms", digits, name, message,
                             approval_id=approval_id,
                             code_path="Messenger.send_sms")
        if not gate["ok"]:
            return {"ok": False, "error": f"BLOCKED: {gate['reason']}", "method": "blocked"}

        if self.test_mode and digits not in self.test_phones:
            _log_outbound("sms", digits, name, message, approval_id, "",
                          "blocked", "test_mode", "Messenger.send_sms")
            return {"ok": False, "error": f"Test mode: {digits} not in test contacts."}

        gateway = CARRIER_GATEWAYS.get(carrier, "@tmomail.net")
        to_addr = f"{digits}{gateway}"

        msg = MIMEText(message)
        msg["From"] = self.gmail
        msg["To"] = to_addr
        msg["Subject"] = ""

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: _smtp_send(self.gmail, self.app_password, to_addr, msg.as_string()))
            self._log_conversation("sms", "outbound", name, phone, "", carrier, "", message, "sent")
            # Update outbound_log to "sent"
            _log_outbound("sms", digits, name, message, approval_id,
                          gate.get("approved_by", ""), "sent", "delivered", "Messenger.send_sms")
            return {"ok": True, "to": to_addr, "digits": digits, "method": "email_gateway"}
        except Exception as e:
            err = str(e)
            self._log_conversation("sms", "outbound", name, phone, "", carrier, "", message, "failed", err)
            _log_outbound("sms", digits, name, message, approval_id,
                          gate.get("approved_by", ""), "failed", err, "Messenger.send_sms")
            return {"ok": False, "error": err}

    async def send_email(self, to_email: str, subject: str, body: str,
                         name: str = "", html_body: str = "", approval_id: int = None):
        if not to_email or "@" not in to_email:
            return {"ok": False, "error": "Invalid email address"}

        # OUTBOUND GATE: Kill switch + approval verification
        gate = outbound_gate("email", to_email, name, body,
                             approval_id=approval_id,
                             code_path="Messenger.send_email")
        if not gate["ok"]:
            return {"ok": False, "error": f"BLOCKED: {gate['reason']}", "method": "blocked"}

        if self.test_mode and to_email.lower().strip() not in self.test_emails:
            _log_outbound("email", to_email, name, body, approval_id, "",
                          "blocked", "test_mode", "Messenger.send_email")
            return {"ok": False, "error": f"Test mode: {to_email} not in test contacts."}

        msg = _build_mime_with_logo(
            subject, f"Zoar Bathroom Rentals <{self.gmail}>",
            to_email, body, html_body,
        )

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: _smtp_send(self.gmail, self.app_password, to_email, msg.as_string()))
            self._log_conversation("email", "outbound", name, "", to_email, "", subject, body, "sent")
            _log_outbound("email", to_email, name, body, approval_id,
                          gate.get("approved_by", ""), "sent", "delivered", "Messenger.send_email")
            return {"ok": True, "to": to_email}
        except Exception as e:
            err = str(e)
            self._log_conversation("email", "outbound", name, "", to_email, "", subject, body, "failed", err)
            _log_outbound("email", to_email, name, body, approval_id,
                          gate.get("approved_by", ""), "failed", err, "Messenger.send_email")
            return {"ok": False, "error": err}

    async def send_b2b_email(self, to_email: str, subject: str, plain_body: str,
                             html_body: str = "", business_name: str = "",
                             approval_id: int = None):
        """Send a B2B cold email with proper headers (Reply-To, List-Unsubscribe, Message-ID).
        Flows through the full SecurityGate (10 gates).
        """
        if not to_email or "@" not in to_email:
            return {"ok": False, "error": "Invalid email address"}

        # OUTBOUND GATE
        gate = outbound_gate("email", to_email, business_name, plain_body,
                             approval_id=approval_id,
                             code_path="Messenger.send_b2b_email")
        if not gate["ok"]:
            return {"ok": False, "error": f"BLOCKED: {gate['reason']}", "method": "blocked"}

        if self.test_mode and to_email.lower().strip() not in self.test_emails:
            _log_outbound("email", to_email, business_name, plain_body, approval_id, "",
                          "blocked", "test_mode", "Messenger.send_b2b_email")
            return {"ok": False, "error": f"Test mode: {to_email} not in test contacts."}

        import uuid
        msg = _build_mime_with_logo(
            subject, f"Zoar Bathroom Rentals <{self.gmail}>",
            to_email, plain_body, html_body,
            extra_headers={
                "Reply-To": self.gmail,
                "Message-ID": f"<b2b-{uuid.uuid4().hex[:12]}@zoarbathroomrental.com>",
                "List-Unsubscribe": f"<mailto:{self.gmail}?subject=unsubscribe>",
            },
        )

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: _smtp_send(self.gmail, self.app_password, to_email, msg.as_string()))
            self._log_conversation("email", "outbound", business_name, "", to_email, "", subject, plain_body, "sent")
            _log_outbound("email", to_email, business_name, plain_body, approval_id,
                          gate.get("approved_by", ""), "sent", "delivered", "Messenger.send_b2b_email")
            print(f"[B2B] Email sent to {to_email} ({business_name})")
            return {"ok": True, "to": to_email}
        except Exception as e:
            err = str(e)
            self._log_conversation("email", "outbound", business_name, "", to_email, "", subject, plain_body, "failed", err)
            _log_outbound("email", to_email, business_name, plain_body, approval_id,
                          gate.get("approved_by", ""), "failed", err, "Messenger.send_b2b_email")
            return {"ok": False, "error": err}

    async def send_sms_via_email(self, phone: str, message: str, carrier: str = None):
        """Convenience alias used by GHL integration."""
        return await self.send_sms(phone, message, carrier or "tmobile")


async def generate_bot_reply(provider, channel, incoming, contact_name, history=None):
    """Generate an AI reply using the Zoar bot personality."""
    from integrations.zoar_bot import gen_reply
    text, model = await gen_reply(provider, channel, incoming, contact_name, history)
    return {"body": text, "model": model}


async def generate_initial_outreach(provider, channel, contact_info):
    """Generate initial outreach message."""
    from integrations.zoar_bot import gen_initial
    name = contact_info.get("name", contact_info.get("contact_name", ""))
    body, subject, model = await gen_initial(provider, channel, name)
    return {"body": body, "subject": subject, "model": model}


def _clean_phone(p: str) -> str:
    d = "".join(c for c in str(p) if c.isdigit())
    if len(d) == 11 and d[0] == "1":
        d = d[1:]
    return d


def _smtp_send(gmail: str, app_pw: str, to: str, msg_string: str):
    """Synchronous SMTP send — runs in executor."""
    server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
    try:
        server.login(gmail, app_pw)
        server.sendmail(gmail, to, msg_string)
    finally:
        server.quit()
