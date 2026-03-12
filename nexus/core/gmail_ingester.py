"""
Gmail Ingester — pulls emails from Gmail IMAP into lead_conversations.

Uses the same IMAP connection pattern as integrations/gmail_imap.py
but writes to lead_conversations / lead_conversation_messages
for the unified conversation timeline.

Can be run as a standalone script (via launchd) or imported.

Schedule via launchd: every 15 minutes
"""
from __future__ import annotations

import email as email_lib
import imaplib
import json
import logging
import sqlite3
import traceback
from datetime import datetime, timezone
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("gmail_ingester")
DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"

# Skip messages from these senders
SKIP_SENDERS = {"mailer-daemon", "postmaster", "noreply", "no-reply",
                "notifications", "donotreply", "feedback"}


def _load_config() -> Dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def _decode_header_value(value: Optional[str]) -> str:
    """Decode an email header value (handles encoded-word syntax)."""
    if not value:
        return ""
    parts = decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(str(part))
    return " ".join(decoded).strip()


def _extract_body(msg) -> str:
    """Extract plain text body from email message."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if ct == "text/plain" and "attachment" not in cd:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        # Fallback: try text/html
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return ""


def _parse_timestamp(msg) -> str:
    """Extract and normalize the message timestamp."""
    date_str = msg.get("Date", "")
    if date_str:
        try:
            dt = parsedate_to_datetime(date_str)
            return dt.astimezone(timezone.utc).isoformat()
        except Exception:
            pass
    return datetime.utcnow().isoformat()


def ingest_recent_emails(
    gmail_address: Optional[str] = None,
    app_password: Optional[str] = None,
    minutes: int = 30,
) -> Dict:
    """Connect to IMAP, fetch recent messages, feed into conversation manager.

    Returns: {ingested: int, skipped: int, errors: int, account: str}
    """
    config = _load_config()
    gmail = gmail_address or config.get("gmail_address", "zoarbathrooms@gmail.com")
    password = app_password or config.get("gmail_app_password", "")

    if not password:
        return {"status": "error", "reason": "gmail_app_password not configured"}

    from core.conversation_manager import get_or_create_conversation, add_message

    ingested = 0
    skipped = 0
    errors = 0

    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(gmail, password)
        imap.select("INBOX", readonly=True)

        # Search for recent unseen messages
        status, data = imap.search(None, "UNSEEN")
        if status != "OK" or not data[0]:
            imap.logout()
            return {"status": "ok", "ingested": 0, "skipped": 0, "account": gmail}

        msg_nums = data[0].split()
        # Limit to most recent 100 to avoid overwhelming
        msg_nums = msg_nums[-100:]

        for num in msg_nums:
            try:
                status, msg_data = imap.fetch(num, "(RFC822)")
                if status != "OK":
                    continue

                raw = msg_data[0][1]
                msg = email_lib.message_from_bytes(raw)

                # Parse sender
                from_header = _decode_header_value(msg.get("From", ""))
                sender_name, sender_email = parseaddr(from_header)
                sender_email = sender_email.lower().strip()

                # Skip system/noreply senders
                local_part = sender_email.split("@")[0] if "@" in sender_email else ""
                if local_part in SKIP_SENDERS:
                    skipped += 1
                    continue

                # Skip our own sent emails (they'll be in Sent folder)
                if sender_email == gmail.lower():
                    skipped += 1
                    continue

                # Parse message
                message_id = msg.get("Message-ID", "")
                subject = _decode_header_value(msg.get("Subject", ""))
                body = _extract_body(msg)
                timestamp = _parse_timestamp(msg)

                if not body.strip():
                    skipped += 1
                    continue

                # Find or create conversation
                conv = get_or_create_conversation(
                    contact_email=sender_email,
                    contact_name=sender_name or sender_email.split("@")[0],
                    channel="email",
                )

                # Add the message (deduplicates by source_message_id)
                result = add_message(
                    conversation_id=conv["id"],
                    direction="inbound",
                    channel="email",
                    body_plain=body[:10000],  # Truncate very long emails
                    message_timestamp=timestamp,
                    source_message_id=message_id,
                    source_account=gmail,
                    sender=sender_email,
                    recipient=gmail,
                    subject=subject,
                )

                if result:
                    ingested += 1
                else:
                    skipped += 1  # Duplicate

            except Exception as e:
                log.error("Error processing message %s: %s", num, e)
                errors += 1

        imap.logout()

    except imaplib.IMAP4.error as e:
        log.error("IMAP connection error: %s", e)
        return {"status": "error", "reason": f"IMAP error: {e}", "account": gmail}
    except Exception as e:
        log.error("Gmail ingester error: %s\n%s", e, traceback.format_exc())
        return {"status": "error", "reason": str(e), "account": gmail}

    log.info("Gmail ingest complete: ingested=%d skipped=%d errors=%d account=%s",
             ingested, skipped, errors, gmail)
    return {
        "status": "ok",
        "ingested": ingested,
        "skipped": skipped,
        "errors": errors,
        "account": gmail,
    }


def ingest_sent_emails(
    gmail_address: Optional[str] = None,
    app_password: Optional[str] = None,
    limit: int = 50,
) -> Dict:
    """Ingest sent emails to capture outbound messages in conversations."""
    config = _load_config()
    gmail = gmail_address or config.get("gmail_address", "zoarbathrooms@gmail.com")
    password = app_password or config.get("gmail_app_password", "")

    if not password:
        return {"status": "error", "reason": "gmail_app_password not configured"}

    from core.conversation_manager import get_or_create_conversation, add_message

    ingested = 0
    skipped = 0

    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(gmail, password)
        imap.select('"[Gmail]/Sent Mail"', readonly=True)

        # Get recent sent messages
        status, data = imap.search(None, "ALL")
        if status != "OK" or not data[0]:
            imap.logout()
            return {"status": "ok", "ingested": 0, "account": gmail}

        msg_nums = data[0].split()[-limit:]

        for num in msg_nums:
            try:
                status, msg_data = imap.fetch(num, "(RFC822)")
                if status != "OK":
                    continue

                raw = msg_data[0][1]
                msg = email_lib.message_from_bytes(raw)

                to_header = _decode_header_value(msg.get("To", ""))
                _, to_email = parseaddr(to_header)
                to_email = to_email.lower().strip()

                if not to_email:
                    skipped += 1
                    continue

                message_id = msg.get("Message-ID", "")
                subject = _decode_header_value(msg.get("Subject", ""))
                body = _extract_body(msg)
                timestamp = _parse_timestamp(msg)

                conv = get_or_create_conversation(
                    contact_email=to_email,
                    contact_name=to_email.split("@")[0],
                    channel="email",
                )

                result = add_message(
                    conversation_id=conv["id"],
                    direction="outbound",
                    channel="email",
                    body_plain=body[:10000],
                    message_timestamp=timestamp,
                    source_message_id=message_id,
                    source_account=gmail,
                    sender=gmail,
                    recipient=to_email,
                    subject=subject,
                )

                if result:
                    ingested += 1
                else:
                    skipped += 1

            except Exception:
                pass

        imap.logout()
    except Exception as e:
        return {"status": "error", "reason": str(e)}

    return {"status": "ok", "ingested": ingested, "skipped": skipped, "account": gmail}


# ── Daemon mode ───────────────────────────────────────────────────────────────

def run_daemon(interval_minutes: int = 15):
    """Run ingestion on a loop with heartbeat reporting."""
    import signal
    import time

    _running = True

    def _stop(sig, frame):
        nonlocal _running
        _running = False
        log.info("Gmail ingester shutting down (signal %d)", sig)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    try:
        from core.agent_runtime import AgentRuntime
        _agent = AgentRuntime("conversation-ingester", "d1_vendor",
                              launchd_label="com.zoar.conversation-ingester")
    except Exception:
        _agent = None

    log.info("Gmail ingester daemon started (%d-min interval)", interval_minutes)

    while _running:
        try:
            result = ingest_recent_emails()
            ingested = result.get("ingested", 0)
            sent_result = ingest_sent_emails(limit=30)
            sent_ingested = sent_result.get("ingested", 0)
            log.info("Cycle: inbox=%d sent=%d", ingested, sent_ingested)
            if _agent:
                _agent.heartbeat("Ingested %d inbox, %d sent" % (ingested, sent_ingested))
        except Exception as e:
            log.error("Ingestion cycle error: %s", e)
            if _agent:
                _agent.report_error(str(e), will_retry=True)

        wait_until = time.time() + interval_minutes * 60
        while _running and time.time() < wait_until:
            time.sleep(1)

    if _agent:
        _agent.stop()
    log.info("Gmail ingester daemon stopped")


# ── Standalone execution (for launchd) ──────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    parser = argparse.ArgumentParser(description="Gmail Ingester")
    parser.add_argument("--daemon", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=15, help="Minutes between cycles (default: 15)")
    args = parser.parse_args()

    if args.daemon:
        run_daemon(interval_minutes=args.interval)
    else:
        result = ingest_recent_emails()
        print(f"Inbox: {result}")

        sent_result = ingest_sent_emails(limit=30)
        print(f"Sent: {sent_result}")

        sys.exit(0 if result.get("status") == "ok" else 1)
