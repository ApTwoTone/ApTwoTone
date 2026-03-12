#!/usr/bin/env python3
"""Reply Monitor — detects vendor replies to outreach emails via IMAP.

A vendor replying = warm lead. Kai needs to know within 5 minutes.

Checks Gmail IMAP every 15 minutes for UNSEEN messages.
Matches sender against vendors with outreach_status='sent'.
Filters out bounces and unsubscribe replies.
Sends Telegram alert on real replies.

Usage:
    python scripts/reply_monitor.py              # One check
    python scripts/reply_monitor.py --daemon      # Run every 15 min
    python scripts/reply_monitor.py --json        # JSON output
"""

import argparse
import email
import email.header
import imaplib
import json
import logging
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("reply_monitor")

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"

# Patterns to filter out (not real replies)
BOUNCE_SENDERS = {"mailer-daemon", "postmaster", "noreply", "no-reply", "do-not-reply"}
BOUNCE_SUBJECTS = [
    re.compile(r"delivery.*fail", re.IGNORECASE),
    re.compile(r"undeliver", re.IGNORECASE),
    re.compile(r"returned.*mail", re.IGNORECASE),
    re.compile(r"mail.*delivery.*error", re.IGNORECASE),
    re.compile(r"failure.*notice", re.IGNORECASE),
]
UNSUBSCRIBE_PATTERNS = [
    re.compile(r"\bunsubscribe\b", re.IGNORECASE),
    re.compile(r"\bopt[- ]?out\b", re.IGNORECASE),
    re.compile(r"\bremove\s+me\b", re.IGNORECASE),
    re.compile(r"\bstop\s+(sending|emailing)\b", re.IGNORECASE),
]
OUTREACH_SUBJECTS = [
    "restroom solutions", "restroom rentals", "partner for restroom",
    "luxury restroom", "restroom trailer", "outdoor events",
]


def _load_config():
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def _send_telegram(msg):
    try:
        subprocess.run(
            [sys.executable, str(Path(__file__).parent / "notify_telegram.py"), msg],
            timeout=15, capture_output=True,
        )
    except Exception as e:
        log.error("Telegram failed: %s", e)


def _decode_header(header_value):
    """Decode email header (handles encoded words)."""
    if not header_value:
        return ""
    decoded_parts = email.header.decode_header(header_value)
    result = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(part)
    return " ".join(result)


def _extract_sender_email(from_header):
    """Extract email address from From header."""
    match = re.search(r"<([^>]+)>", from_header)
    if match:
        return match.group(1).lower()
    # Bare email
    match = re.search(r"[\w.+-]+@[\w.-]+", from_header)
    if match:
        return match.group(0).lower()
    return from_header.lower().strip()


def _is_bounce(from_addr, subject):
    """Check if message is a bounce notification."""
    local_part = from_addr.split("@")[0].lower() if "@" in from_addr else from_addr.lower()
    if local_part in BOUNCE_SENDERS:
        return True
    for pattern in BOUNCE_SUBJECTS:
        if pattern.search(subject):
            return True
    return False


def _is_unsubscribe(subject, body):
    """Check if message is an unsubscribe request."""
    for pattern in UNSUBSCRIBE_PATTERNS:
        if pattern.search(subject) or pattern.search(body[:500]):
            return True
    return False


def _get_sent_vendors():
    """Get vendor emails we've already contacted by email.

    Uses both vendor status and outbound_log so reply matching still works
    even if a sender path wrote to one source but not the other.
    """
    if not DB_PATH.exists():
        return {}
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA query_only = ON")
    vendors = {}
    rows = conn.execute(
        """
        SELECT DISTINCT v.email, v.name, v.category, v.city
        FROM vendors v
        WHERE v.email IS NOT NULL
          AND v.email != ''
          AND (
                v.outreach_status IN ('sent', 'emailed', 'replied')
                OR EXISTS (
                    SELECT 1
                    FROM outbound_log o
                    WHERE lower(o.recipient) = lower(v.email)
                      AND o.channel = 'email'
                      AND o.result IN ('allowed', 'sent')
                )
          )
        """
    ).fetchall()
    for row in rows:
        vendors[row[0].lower()] = {
            "name": row[1],
            "category": row[2],
            "city": row[3],
        }
    conn.close()
    return vendors


def check_replies():
    """Check Gmail for replies to outreach emails."""
    config = _load_config()
    gmail_addr = config.get("gmail_address", "")
    gmail_pwd = config.get("gmail_app_password", "")

    if not gmail_addr or not gmail_pwd:
        return {"status": "error", "message": "Missing Gmail credentials in config"}

    sent_vendors = _get_sent_vendors()
    if not sent_vendors:
        return {"status": "ok", "replies": [], "message": "No vendors with outreach_status='sent'"}

    result = {"status": "ok", "replies": [], "bounces": 0, "unsubscribes": 0}

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", timeout=30)
        mail.login(gmail_addr, gmail_pwd)
        mail.select("INBOX")

        # Search for UNSEEN messages
        _, msg_ids = mail.search(None, "UNSEEN")
        if not msg_ids[0]:
            mail.close()
            mail.logout()
            return result

        for msg_id in msg_ids[0].split():
            try:
                _, data = mail.fetch(msg_id, "(BODY[HEADER.FIELDS (FROM SUBJECT DATE)])")
                if not data or not data[0]:
                    continue

                header_data = data[0][1].decode("utf-8", errors="replace")
                msg = email.message_from_string(header_data)

                from_header = _decode_header(msg.get("From", ""))
                subject = _decode_header(msg.get("Subject", ""))
                date_str = msg.get("Date", "")
                sender_email = _extract_sender_email(from_header)

                # Skip bounces
                if _is_bounce(sender_email, subject):
                    result["bounces"] += 1
                    continue

                # Check if sender is a vendor we emailed
                if sender_email in sent_vendors:
                    vendor = sent_vendors[sender_email]

                    # Fetch body snippet to check for unsubscribe
                    _, body_data = mail.fetch(msg_id, "(BODY[TEXT])")
                    body = ""
                    if body_data and body_data[0] and len(body_data[0]) > 1:
                        body = body_data[0][1].decode("utf-8", errors="replace")

                    if _is_unsubscribe(subject, body):
                        result["unsubscribes"] += 1
                        log.info("Unsubscribe from %s <%s>", vendor["name"], sender_email)
                        continue

                    # Real reply from a vendor
                    reply = {
                        "vendor_name": vendor["name"],
                        "vendor_email": sender_email,
                        "category": vendor["category"],
                        "city": vendor["city"],
                        "subject": subject,
                        "date": date_str,
                        "body_preview": body[:200].strip() if body else "",
                    }
                    result["replies"].append(reply)

                    try:
                        conn = sqlite3.connect(str(DB_PATH))
                        conn.execute(
                            "UPDATE vendors SET outreach_status='replied' WHERE LOWER(email)=LOWER(?)",
                            (sender_email,),
                        )
                        conn.commit()
                        conn.close()
                    except Exception:
                        pass

                    try:
                        from core.nexus_coordination import log_event
                        log_event(
                            "VENDOR_REPLIED",
                            payload={
                                "vendor_name": vendor["name"],
                                "vendor_email": sender_email,
                                "subject": subject,
                                "city": vendor["city"],
                                "category": vendor["category"],
                            },
                            source="reply_monitor",
                        )
                    except Exception:
                        pass

                    log.info("REPLY from %s <%s>: %s", vendor["name"], sender_email, subject[:60])

            except Exception as e:
                log.warning("Error processing message %s: %s", msg_id, e)
                continue

        mail.close()
        mail.logout()

    except Exception as e:
        result["status"] = "error"
        result["message"] = str(e)
        log.error("IMAP error: %s", e)

    # Send alerts for replies
    for reply in result["replies"]:
        msg = (
            "VENDOR REPLIED — CALL WITHIN 5 MINUTES\n\n"
            "From: %s (%s, %s)\n"
            "Email: %s\n"
            "Subject: %s\n"
            "Preview: %s"
        ) % (
            reply["vendor_name"], reply["category"], reply["city"],
            reply["vendor_email"], reply["subject"],
            reply["body_preview"][:100],
        )
        _send_telegram(msg)

    return result


def main():
    parser = argparse.ArgumentParser(description="Reply monitor")
    parser.add_argument("--daemon", action="store_true", help="Run every 15 min")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.daemon:
        log.info("Reply monitor started in daemon mode (15 min interval)")
        try:
            from core.agent_runtime import AgentRuntime
            _agent = AgentRuntime('reply_monitor', 'daemon', 'operations')
            _agent.start()
        except Exception:
            _agent = None
        while True:
            try:
                result = check_replies()
                replies = len(result.get("replies", []))
                if _agent:
                    _agent.heartbeat(f"Scanning inbox — {replies} new replies")
                if replies:
                    log.info("Found %d vendor replies", replies)
                else:
                    log.info("No new replies (bounces: %d, unsubs: %d)",
                             result.get("bounces", 0), result.get("unsubscribes", 0))
            except Exception as e:
                log.error("Check failed: %s", e)
                if _agent:
                    _agent.report_error(str(e), will_retry=True)
            time.sleep(900)  # 15 minutes
    else:
        result = check_replies()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("\n  Reply Monitor")
            print("  " + "=" * 45)
            print("  Status: %s" % result["status"])
            replies = result.get("replies", [])
            if replies:
                print("  REPLIES FOUND: %d" % len(replies))
                for r in replies:
                    print("    %s <%s>" % (r["vendor_name"], r["vendor_email"]))
                    print("    Subject: %s" % r["subject"])
            else:
                print("  No new vendor replies")
            print("  Bounces skipped: %d" % result.get("bounces", 0))
            print("  Unsubscribes: %d" % result.get("unsubscribes", 0))
            print("  " + "=" * 45)


if __name__ == "__main__":
    main()
