from __future__ import annotations
from typing import Optional
"""
Gmail IMAP Reply Checker
- Connects to imap.gmail.com using existing Gmail app password
- Fetches UNSEEN messages, matches sender against known lead emails
- Polls every N minutes and feeds replies into the lead pipeline
"""
import asyncio, imaplib, email as email_lib, sqlite3, logging, re, traceback
from email.header import decode_header
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path
from datetime import datetime, timedelta, timezone

from core.telegram_policy import should_send_notification

log = logging.getLogger("gmail_imap")
DB_PATH = Path.home() / ".nexus" / "memory.db"

# Bounce sender patterns
BOUNCE_SENDERS = {"mailer-daemon", "postmaster"}
BOUNCE_SUBJECT_PATTERNS = re.compile(
    r"(delivery.*(fail|status|notification)|undeliverable|returned mail|"
    r"mail delivery|failure notice|non.?deliver)",
    re.IGNORECASE,
)
UNSUBSCRIBE_PATTERNS = re.compile(
    r"\b(unsubscribe|opt.?out|stop.?email|remove me|no longer)\b", re.IGNORECASE
)

_imap_checker = None


class GmailIMAPChecker:
    def __init__(self, gmail: str, app_password: str):
        self.gmail = gmail
        self.app_password = app_password
        # Keep a short rolling in-memory cache to prevent duplicate sent imports
        # across overlapping IMAP searches.
        self._seen_sent_ids: set[str] = set()
        # Start with a small lookback so we capture recent sends after restart.
        self._last_sent_scan_utc = datetime.now(timezone.utc) - timedelta(minutes=30)

    def _parse_datetime_utc(self, date_str: str) -> datetime:
        if not date_str:
            return datetime.now(timezone.utc)
        try:
            dt = parsedate_to_datetime(date_str)
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return datetime.now(timezone.utc)

    def _format_ts(self, dt: datetime) -> str:
        try:
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    def _open_sent_folder(self, mail) -> bool:
        for folder in ('"[Gmail]/Sent Mail"', '"[Gmail]/Sent"', "Sent"):
            try:
                status, _ = mail.select(folder)
                if status == "OK":
                    return True
            except Exception:
                continue
        return False

    def _message_identity(self, msg, recipient_hint: str = "") -> str:
        message_id = (msg.get("Message-ID", "") or "").strip()
        recipient_hint = (recipient_hint or "").strip().lower()
        if message_id:
            return f"{message_id}|{recipient_hint}"
        subject = self._decode_header(msg.get("Subject", ""))[:120]
        date_str = msg.get("Date", "")[:80]
        return f"{recipient_hint}|{subject}|{date_str}"

    def _parse_message_rich(self, mail, num, direction: str):
        try:
            status, msg_data = mail.fetch(num, "(RFC822)")
            if status != "OK":
                return None
            msg = email_lib.message_from_bytes(msg_data[0][1])
            body = self._extract_body(msg)
            subject = self._decode_header(msg.get("Subject", ""))
            ts_utc = self._parse_datetime_utc(msg.get("Date", ""))
            from_name, from_email = email_lib.utils.parseaddr(msg.get("From", ""))
            to_pairs = getaddresses([msg.get("To", "")])
            to_emails = [addr.lower().strip() for _, addr in to_pairs if addr]
            in_reply_to = (msg.get("In-Reply-To", "") or "").strip()
            references = (msg.get("References", "") or "").strip()
            is_reply_like = bool(in_reply_to or references or subject.lower().startswith("re:"))

            return {
                "direction": direction,
                "channel": "email",
                "content": body[:2000],
                "subject": subject,
                "timestamp": self._format_ts(ts_utc),
                "timestamp_utc": ts_utc,
                "from_name": from_name or "",
                "from_email": (from_email or "").lower().strip(),
                "to_emails": to_emails,
                "message_id": (msg.get("Message-ID", "") or "").strip(),
                "raw_date": msg.get("Date", ""),
                "is_reply_like": is_reply_like,
            }
        except Exception:
            return None

    def _get_lead_emails(self) -> set:
        """Query leads + b2b_leads + campaign sends for all known email addresses."""
        emails = set()
        try:
            conn = sqlite3.connect(str(DB_PATH))
            rows = conn.execute(
                "SELECT DISTINCT lower(email) FROM leads WHERE email != '' AND status NOT IN ('closed', 'opted_out')"
            ).fetchall()
            emails.update(r[0] for r in rows)
            # Also check B2B leads
            try:
                b2b_rows = conn.execute(
                    "SELECT DISTINCT lower(email) FROM b2b_leads WHERE email != '' AND do_not_contact = 0"
                ).fetchall()
                emails.update(r[0] for r in b2b_rows)
            except Exception:
                pass
            # Also check campaign send emails
            try:
                camp_rows = conn.execute(
                    "SELECT DISTINCT lower(email) FROM email_campaign_sends WHERE status = 'sent'"
                ).fetchall()
                emails.update(r[0] for r in camp_rows)
            except Exception:
                pass
            # Also check vendor outreach emails (cold email recipients)
            try:
                vendor_rows = conn.execute(
                    "SELECT DISTINCT lower(v.email) FROM vendors v "
                    "JOIN vendor_outreach vo ON v.id = vo.vendor_id "
                    "WHERE v.email != '' AND vo.status = 'sent'"
                ).fetchall()
                emails.update(r[0] for r in vendor_rows)
            except Exception:
                pass
            conn.close()
        except Exception:
            pass
        return emails

    def _get_campaign_emails(self) -> set:
        """Get all emails that received campaign sends (for bounce/reply matching)."""
        emails = set()
        try:
            conn = sqlite3.connect(str(DB_PATH))
            rows = conn.execute(
                "SELECT DISTINCT lower(email) FROM email_campaign_sends WHERE status = 'sent'"
            ).fetchall()
            emails.update(r[0] for r in rows)
            conn.close()
        except Exception:
            pass
        return emails

    async def check_replies(self) -> list[dict]:
        """
        Connect to IMAP, fetch unseen messages, filter to known lead emails.
        Returns list of {from_email, from_name, subject, body, timestamp}.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._check_sync)

    def _check_sync(self) -> list:
        """Synchronous IMAP check — runs in thread executor."""
        replies = []
        lead_emails = self._get_lead_emails()

        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        try:
            mail.login(self.gmail, self.app_password)
            mail.select("INBOX")

            # Search for unseen messages
            status, data = mail.search(None, "UNSEEN")
            if status != "OK" or not data[0]:
                return []

            message_nums = data[0].split()
            # Process most recent 50 messages max
            for num in message_nums[-50:]:
                try:
                    status, msg_data = mail.fetch(num, "(RFC822)")
                    if status != "OK":
                        continue

                    msg = email_lib.message_from_bytes(msg_data[0][1])
                    from_name, from_addr = email_lib.utils.parseaddr(msg["From"])
                    from_addr = from_addr.lower().strip()
                    if not from_addr:
                        continue

                    from_local = from_addr.split("@")[0] if "@" in from_addr else ""
                    if from_addr == self.gmail.lower() or from_local in BOUNCE_SENDERS:
                        continue

                    subject = self._decode_header(msg.get("Subject", ""))
                    in_reply_to = (msg.get("In-Reply-To", "") or "").strip()
                    references = (msg.get("References", "") or "").strip()
                    is_known_contact = from_addr in lead_emails
                    is_reply_like = bool(in_reply_to or references or subject.lower().startswith("re:"))
                    ts_utc = self._parse_datetime_utc(msg.get("Date", ""))

                    # Keep the inbox signal high: ingest known contacts always,
                    # and unknown senders only when the message looks like a reply thread.
                    if not is_known_contact and not is_reply_like:
                        continue

                    body = self._extract_body(msg)
                    reply = {
                        "from_email": from_addr,
                        "from_name": from_name or from_addr.split("@")[0],
                        "subject": subject,
                        "body": body[:2000],  # Limit body size
                        "timestamp": self._format_ts(ts_utc),
                        "is_known_contact": is_known_contact,
                    }

                    # Check if this is a B2B lead reply
                    try:
                        from core.b2b_leads import find_lead_by_email
                        b2b_lead = find_lead_by_email(from_addr)
                        if b2b_lead:
                            reply["b2b_lead_id"] = b2b_lead["id"]
                            reply["b2b_business"] = b2b_lead["business_name"]
                    except Exception:
                        pass

                    # Check if this is a vendor outreach reply
                    try:
                        from core.vendor_db import get_vendor_by_email
                        vendor = get_vendor_by_email(from_addr)
                        if vendor:
                            reply["vendor_id"] = vendor["id"]
                            reply["vendor_name"] = vendor.get("business_name", "")
                            self._handle_vendor_reply(vendor, subject, body)
                    except Exception:
                        pass

                    replies.append(reply)
                    # Mark as read so we don't re-process on next poll
                    try:
                        mail.store(num, '+FLAGS', '\\Seen')
                    except Exception:
                        pass
                    known_label = "known" if is_known_contact else "unknown-reply"
                    print(f"[IMAP] Found inbound email ({known_label}): {from_addr}")
                except Exception as e:
                    print(f"[IMAP] Error processing message: {e}")
                    continue

        except imaplib.IMAP4.error as e:
            print(f"[IMAP] IMAP error: {e}")
        except Exception as e:
            print(f"[IMAP] Error: {e}")
        finally:
            try:
                mail.logout()
            except:
                pass

        return replies

    async def check_sent_messages(self) -> list[dict]:
        """
        Poll Sent Mail for new outbound emails.
        Returns list of {to_email, subject, body, timestamp, message_id}.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._check_sent_sync)

    def _check_sent_sync(self, max_scan: int = 200) -> list[dict]:
        outbox = []
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        latest_seen = self._last_sent_scan_utc
        try:
            mail.login(self.gmail, self.app_password)
            if not self._open_sent_folder(mail):
                return []

            since = self._last_sent_scan_utc.astimezone(timezone.utc).strftime("%d-%b-%Y")
            status, data = mail.search(None, f'(SINCE "{since}")')
            if status != "OK" or not data[0]:
                return []

            nums = data[0].split()[-max_scan:]
            for num in nums:
                parsed = self._parse_message_rich(mail, num, "outbound")
                if not parsed:
                    continue

                msg_dt = parsed.get("timestamp_utc") or datetime.now(timezone.utc)
                if msg_dt <= self._last_sent_scan_utc:
                    continue

                subject = parsed.get("subject", "")
                body = parsed.get("content", "")
                to_emails = parsed.get("to_emails", []) or []
                if not to_emails:
                    continue

                for to_email in to_emails:
                    to_email = (to_email or "").lower().strip()
                    if not to_email or to_email == self.gmail.lower():
                        continue
                    identity = self._message_identity(
                        {"Message-ID": parsed.get("message_id", ""), "Subject": subject, "Date": parsed.get("raw_date", "")},
                        recipient_hint=to_email,
                    )
                    if identity in self._seen_sent_ids:
                        continue
                    self._seen_sent_ids.add(identity)
                    outbox.append({
                        "to_email": to_email,
                        "subject": subject,
                        "body": body[:2000],
                        "timestamp": parsed.get("timestamp", ""),
                        "message_id": parsed.get("message_id", ""),
                    })
                    if len(self._seen_sent_ids) > 5000:
                        self._seen_sent_ids = set(list(self._seen_sent_ids)[-2500:])

                if msg_dt > latest_seen:
                    latest_seen = msg_dt

        except Exception as e:
            print(f"[IMAP] Sent sync error: {e}")
        finally:
            self._last_sent_scan_utc = latest_seen
            try:
                mail.logout()
            except Exception:
                pass

        return outbox

    def _extract_body(self, msg) -> str:
        """Extract plain text body from email message."""
        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                cd = str(part.get("Content-Disposition", ""))
                # Skip attachments
                if "attachment" in cd:
                    continue
                if ct == "text/plain":
                    try:
                        return part.get_payload(decode=True).decode("utf-8", errors="replace")
                    except:
                        return ""
            # Fallback to HTML if no plain text
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    try:
                        html = part.get_payload(decode=True).decode("utf-8", errors="replace")
                        # Strip HTML tags (basic)
                        import re
                        return re.sub(r'<[^>]+>', '', html).strip()
                    except:
                        return ""
        else:
            try:
                return msg.get_payload(decode=True).decode("utf-8", errors="replace")
            except:
                return ""
        return ""

    def _handle_vendor_reply(self, vendor: dict, subject: str, body: str):
        """Update vendor outreach status and send Telegram notification on reply."""
        try:
            conn = sqlite3.connect(str(DB_PATH))
            vid = vendor["id"]
            # Update vendor outreach_status
            conn.execute(
                "UPDATE vendors SET outreach_status='responded' WHERE id=?", (vid,)
            )
            # Update the most recent sent outreach record
            conn.execute(
                "UPDATE vendor_outreach SET status='replied', response_date=datetime('now') "
                "WHERE vendor_id=? AND status='sent' ORDER BY sent_date DESC LIMIT 1",
                (vid,)
            )
            conn.commit()
            conn.close()
            log.info("[IMAP] Vendor reply: %s (id=%s)", vendor.get("business_name", ""), vid)
            # Telegram notification
            try:
                import json as _json
                from urllib.request import Request as _Req, urlopen as _urlopen
                cfg_file = Path.home() / ".nexus" / "config.json"
                cfg = _json.loads(cfg_file.read_text()) if cfg_file.exists() else {}
                token = cfg.get("telegram_token", "")
                chat_ids = cfg.get("telegram_chat_ids", [])
                if token and chat_ids:
                    name = vendor.get("business_name", "Unknown")
                    cat = vendor.get("category", "")
                    text = (
                        "VENDOR REPLIED\n"
                        "Name: %s\n"
                        "Category: %s\n"
                        "Subject: %s\n"
                        "Preview: %s"
                    ) % (name, cat, subject[:80], body[:150])
                    for cid in chat_ids:
                        decision = should_send_notification(
                            text,
                            source="integrations.gmail_imap.vendor_reply",
                            recipient=str(cid),
                        )
                        if not decision.allowed:
                            log.info("Vendor reply Telegram suppressed: %s", decision.reason)
                            continue
                        url = "https://api.telegram.org/bot%s/sendMessage" % token
                        payload = _json.dumps({"chat_id": str(cid), "text": text}).encode()
                        req = _Req(url, data=payload, headers={"Content-Type": "application/json"})
                        _urlopen(req, timeout=10)
            except Exception as e:
                log.warning("[IMAP] Could not send vendor reply Telegram: %s", e)
        except Exception as e:
            log.error("[IMAP] Error handling vendor reply: %s", e)

    def _decode_header(self, header_val) -> str:
        """Decode a possibly-encoded email header."""
        if not header_val:
            return ""
        parts = decode_header(header_val)
        result = []
        for data, charset in parts:
            if isinstance(data, bytes):
                result.append(data.decode(charset or "utf-8", errors="replace"))
            else:
                result.append(str(data))
        return " ".join(result)


    def process_bounces_and_unsubscribes(self) -> dict:
        """Scan inbox for bounce notices and unsubscribe replies from campaign emails."""
        stats = {"bounces": 0, "unsubscribes": 0, "replies": 0}
        campaign_emails = self._get_campaign_emails()
        if not campaign_emails:
            return stats

        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        try:
            mail.login(self.gmail, self.app_password)
            mail.select("INBOX")
            status, data = mail.search(None, "UNSEEN")
            if status != "OK" or not data[0]:
                return stats

            conn = sqlite3.connect(str(DB_PATH))
            for num in data[0].split()[-100:]:
                try:
                    status, msg_data = mail.fetch(num, "(RFC822)")
                    if status != "OK":
                        continue
                    msg = email_lib.message_from_bytes(msg_data[0][1])
                    _, from_addr = email_lib.utils.parseaddr(msg["From"])
                    from_addr = from_addr.lower().strip()
                    subject = self._decode_header(msg.get("Subject", ""))
                    from_local = from_addr.split("@")[0] if "@" in from_addr else ""

                    # Check for bounce notifications
                    if from_local in BOUNCE_SENDERS or BOUNCE_SUBJECT_PATTERNS.search(subject):
                        body = self._extract_body(msg)
                        bounced_email = self._extract_bounced_email(body, subject)
                        if bounced_email and bounced_email in campaign_emails:
                            self._record_bounce(conn, bounced_email)
                            stats["bounces"] += 1
                            log.info("[IMAP] Bounce detected for %s", bounced_email)
                            try:
                                mail.store(num, '+FLAGS', '\\Seen')
                            except Exception:
                                pass
                        continue

                    # Check for replies from campaign recipients
                    if from_addr in campaign_emails:
                        body = self._extract_body(msg)
                        if UNSUBSCRIBE_PATTERNS.search(body) or UNSUBSCRIBE_PATTERNS.search(subject):
                            self._record_unsubscribe(conn, from_addr)
                            stats["unsubscribes"] += 1
                            log.info("[IMAP] Unsubscribe detected from %s", from_addr)
                        else:
                            self._record_campaign_reply(conn, from_addr)
                            stats["replies"] += 1
                            log.info("[IMAP] Campaign reply from %s", from_addr)
                        try:
                            mail.store(num, '+FLAGS', '\\Seen')
                        except Exception:
                            pass

                except Exception as e:
                    log.warning("[IMAP] Error processing bounce/unsub message: %s", e)
                    continue

            conn.commit()
            conn.close()

        except Exception as e:
            log.error("[IMAP] Bounce/unsub scan error: %s", e)
        finally:
            try:
                mail.logout()
            except Exception:
                pass

        return stats

    def _extract_bounced_email(self, body: str, subject: str) -> Optional[str]:
        """Extract the original recipient email from a bounce notification."""
        # Common patterns in bounce messages
        patterns = [
            r"<([^>]+@[^>]+)>",                          # <user@domain.com>
            r"(?:to|recipient|address)[:\s]+(\S+@\S+)",   # to: user@domain.com
            r"(\S+@\S+\.\S+)",                            # bare email
        ]
        text = body + " " + subject
        for pat in patterns:
            match = re.search(pat, text, re.IGNORECASE)
            if match:
                addr = match.group(1).lower().strip().rstrip(".")
                # Skip our own email and common bounce addresses
                if addr != self.gmail.lower() and addr.split("@")[0] not in BOUNCE_SENDERS:
                    return addr
        return None

    def _record_bounce(self, conn: sqlite3.Connection, email_addr: str):
        """Mark a bounced email in campaign sends and vendor tables."""
        conn.execute(
            "UPDATE email_campaign_sends SET status='bounced' WHERE lower(email)=? AND status='sent'",
            (email_addr,)
        )
        conn.execute(
            "UPDATE vendors SET email_valid=0 WHERE lower(email)=?",
            (email_addr,)
        )
        # Increment campaign bounce count
        conn.execute("""
            UPDATE email_campaigns SET bounce_count = bounce_count + 1
            WHERE id IN (
                SELECT DISTINCT campaign_id FROM email_campaign_sends
                WHERE lower(email)=? AND status='bounced'
            )
        """, (email_addr,))

    def _record_unsubscribe(self, conn: sqlite3.Connection, email_addr: str):
        """Record an unsubscribe from campaign emails (CAN-SPAM compliance)."""
        conn.execute(
            "INSERT OR IGNORE INTO email_unsubscribes (email, source) VALUES (?, 'reply')",
            (email_addr,)
        )
        conn.execute(
            "UPDATE email_campaign_sends SET status='unsubscribed' WHERE lower(email)=? AND status='sent'",
            (email_addr,)
        )

    def _record_campaign_reply(self, conn: sqlite3.Connection, email_addr: str):
        """Record a positive reply from a campaign recipient."""
        conn.execute(
            "UPDATE email_campaign_sends SET status='replied' WHERE lower(email)=? AND status='sent'",
            (email_addr,)
        )
        conn.execute("""
            UPDATE email_campaigns SET reply_count = reply_count + 1
            WHERE id IN (
                SELECT DISTINCT campaign_id FROM email_campaign_sends
                WHERE lower(email)=? AND status='replied'
            )
        """, (email_addr,))

    def fetch_email_history(self, email_address: str, limit: int = 50) -> list:
        """Fetch email conversation history with a specific address (both sent and received)."""
        messages = []
        email_address = email_address.lower().strip()

        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        try:
            mail.login(self.gmail, self.app_password)

            # Search INBOX for messages from this email
            mail.select("INBOX")
            status, data = mail.search(None, f'(FROM "{email_address}")')
            if status == "OK" and data[0]:
                for num in data[0].split()[-limit:]:
                    msg = self._parse_message(mail, num, "inbound")
                    if msg:
                        messages.append(msg)

            # Search Sent folder for messages to this email
            for folder in ['"[Gmail]/Sent Mail"', "Sent", '"[Gmail]/Sent"']:
                try:
                    status, _ = mail.select(folder)
                    if status != "OK":
                        continue
                    status, data = mail.search(None, f'(TO "{email_address}")')
                    if status == "OK" and data[0]:
                        for num in data[0].split()[-limit:]:
                            msg = self._parse_message(mail, num, "outbound")
                            if msg:
                                messages.append(msg)
                    break  # Found the sent folder
                except:
                    continue

        except Exception as e:
            print(f"[IMAP] History fetch error: {e}")
        finally:
            try:
                mail.logout()
            except:
                pass

        # Sort by timestamp
        messages.sort(key=lambda m: m.get("timestamp", ""))
        print(f"[IMAP] Fetched {len(messages)} emails for {email_address}")
        return messages

    def fetch_recent_account_messages(self, days: int = 14, limit_per_folder: int = 400) -> list:
        """
        Fetch recent inbound + outbound Gmail messages across the account.
        Returns normalized rows with from/to metadata for lead sync backfill.
        """
        messages = []
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        try:
            mail.login(self.gmail, self.app_password)
            since = (datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))).strftime("%d-%b-%Y")

            # Inbound from inbox
            try:
                mail.select("INBOX")
                status, data = mail.search(None, f'(SINCE "{since}")')
                if status == "OK" and data[0]:
                    for num in data[0].split()[-limit_per_folder:]:
                        parsed = self._parse_message_rich(mail, num, "inbound")
                        if parsed:
                            messages.append(parsed)
            except Exception:
                pass

            # Outbound from Sent Mail
            if self._open_sent_folder(mail):
                status, data = mail.search(None, f'(SINCE "{since}")')
                if status == "OK" and data[0]:
                    for num in data[0].split()[-limit_per_folder:]:
                        parsed = self._parse_message_rich(mail, num, "outbound")
                        if parsed:
                            messages.append(parsed)
        except Exception as e:
            print(f"[IMAP] Recent account fetch error: {e}")
        finally:
            try:
                mail.logout()
            except Exception:
                pass

        messages.sort(key=lambda m: m.get("timestamp", ""))
        return messages

    def _parse_message(self, mail, num, direction: str):
        """Parse a single IMAP message into a dict."""
        parsed = self._parse_message_rich(mail, num, direction)
        if not parsed:
            return None
        return {
            "direction": parsed.get("direction", direction),
            "channel": "email",
            "content": parsed.get("content", "")[:2000],
            "subject": parsed.get("subject", ""),
            "timestamp": parsed.get("timestamp", ""),
        }


class IMAPPoller:
    """Background task to poll Gmail for lead replies."""

    def __init__(self, checker: GmailIMAPChecker, interval: int = 30):
        self.checker = checker
        self.interval = interval
        self._running = False
        self._reply_callback = None
        self._sent_callback = None

    def on_reply(self, callback):
        """Register callback: async def callback(reply_dict)"""
        self._reply_callback = callback

    def on_sent(self, callback):
        """Register callback: async def callback(sent_dict)"""
        self._sent_callback = callback

    async def run(self):
        """Poll inbox replies + sent-folder outbounds every interval seconds."""
        self._running = True
        print(f"[IMAP] Poller started (every {self.interval}s)")
        while self._running:
            try:
                replies = await self.checker.check_replies()
                for r in replies:
                    if self._reply_callback:
                        await self._reply_callback(r)
                sent_messages = await self.checker.check_sent_messages()
                for sent in sent_messages:
                    if self._sent_callback:
                        await self._sent_callback(sent)
            except Exception as e:
                print(f"[IMAP] Poller error: {e}")
                traceback.print_exc()
            await asyncio.sleep(self.interval)

    def stop(self):
        self._running = False


# ── Module-level init ─────────────────────────────────────────────────────────

def init_imap(config: dict):
    """Initialize IMAP checker. Returns (IMAPPoller | None, error_string)."""
    global _imap_checker
    gmail = config.get("gmail_address", "")
    pw = config.get("gmail_app_password", "")
    if not gmail or not pw:
        return None, "Gmail not configured for IMAP"
    _imap_checker = GmailIMAPChecker(gmail, pw)
    poller = IMAPPoller(_imap_checker, int(config.get("imap_poll_interval", 30)))
    return poller, ""


def get_imap_checker():
    return _imap_checker
