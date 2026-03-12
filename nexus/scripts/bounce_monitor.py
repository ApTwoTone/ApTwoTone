#!/usr/bin/env python3
"""Bounce monitor — watches Gmail for bounce notifications and spam complaints.

Detects:
- Hard bounces (550-554): email doesn't exist → mark invalid, remove from queue
- Soft bounces (450-452): temporary failure → log, 3x = hard bounce
- Spam complaints: → blocklist + Telegram alert, 2+ in a day = emergency stop
- Unsubscribe replies: → mark vendor unsubscribed, remove from queue

Uses IMAP SSL to check Gmail inbox every 10 minutes.

Usage:
    python scripts/bounce_monitor.py              # One check
    python scripts/bounce_monitor.py --daemon      # Continuous (10 min interval)
    python scripts/bounce_monitor.py --json        # JSON output
"""

import argparse
import email as email_lib
import imaplib
import json
import logging
import re
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from email.header import decode_header
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bounce_monitor")

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"
CHECK_INTERVAL = 600  # 10 minutes
SOFT_BOUNCE_FILE = Path.home() / ".nexus" / "soft_bounces.json"
PROCESSED_BOUNCE_FILE = Path.home() / ".nexus" / "processed_bounce_fingerprints.json"

# Patterns reused from integrations/gmail_imap.py
BOUNCE_SENDERS = {"mailer-daemon", "postmaster"}
BOUNCE_SUBJECT_RE = re.compile(
    r"(delivery.*(fail|status|notification)|undeliverable|returned mail|"
    r"mail delivery|failure notice|non.?deliver)",
    re.IGNORECASE,
)
UNSUBSCRIBE_RE = re.compile(
    r"\b(unsubscribe|opt.?out|stop.?email|remove me|no longer)\b", re.IGNORECASE
)
HARD_BOUNCE_CODES = {"550", "551", "552", "553", "554"}
SOFT_BOUNCE_CODES = {"450", "451", "452"}
ERROR_CODE_RE = re.compile(r"\b(4[0-5]\d|5[0-5]\d)\b")
FAILED_RECIPIENT_RE = re.compile(r"(?:Final-Recipient|Original-Recipient).*?;\s*rfc822;\s*(\S+@\S+)", re.IGNORECASE)
FAILED_RECIPIENT_RE2 = re.compile(r"<(\S+@\S+)>.*(?:failed|rejected|bounced|undeliverable)", re.IGNORECASE)
FAILED_RECIPIENT_RE3 = re.compile(r"(?:The email account|message was not delivered to)\s+(\S+@\S+)", re.IGNORECASE)
EMAIL_ADDR_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")

_running = True


def _signal_handler(sig, frame):
    global _running
    _running = False
    log.info("Shutting down bounce monitor")


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


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


def _extract_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            cd = str(part.get("Content-Disposition", ""))
            if "attachment" in cd:
                continue
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_payload(decode=True).decode("utf-8", errors="replace")
                except Exception:
                    return ""
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    html = part.get_payload(decode=True).decode("utf-8", errors="replace")
                    return re.sub(r"<[^>]+>", "", html).strip()
                except Exception:
                    return ""
    else:
        try:
            return msg.get_payload(decode=True).decode("utf-8", errors="replace")
        except Exception:
            return ""
    return ""


def _decode_subject(msg):
    raw = msg.get("Subject", "")
    try:
        parts = decode_header(raw)
        decoded = []
        for data, charset in parts:
            if isinstance(data, bytes):
                decoded.append(data.decode(charset or "utf-8", errors="replace"))
            else:
                decoded.append(data)
        return " ".join(decoded)
    except Exception:
        return str(raw)


def _extract_failed_recipient(body):
    def _normalize(raw):
        text = (raw or "").strip().lower()
        m = EMAIL_ADDR_RE.search(text)
        if not m:
            return None
        return m.group(0).strip().rstrip(">").rstrip(".")

    for pattern in [FAILED_RECIPIENT_RE, FAILED_RECIPIENT_RE2, FAILED_RECIPIENT_RE3]:
        match = pattern.search(body)
        if match:
            normalized = _normalize(match.group(1))
            if normalized:
                return normalized
    # Try to find any email address in the body that matches a vendor
    emails = re.findall(r"[\w.+-]+@[\w.-]+\.\w+", body)
    if emails:
        conn = sqlite3.connect(str(DB_PATH))
        for em in emails:
            normalized = _normalize(em)
            if not normalized:
                continue
            row = conn.execute("SELECT id FROM vendors WHERE LOWER(email) = ?", (normalized,)).fetchone()
            if row:
                conn.close()
                return normalized
        conn.close()
    return None


def _classify_bounce(body):
    codes = ERROR_CODE_RE.findall(body)
    for code in codes:
        if code in HARD_BOUNCE_CODES:
            return "hard", code
        if code in SOFT_BOUNCE_CODES:
            return "soft", code
    if re.search(r"does not exist|no such user|unknown user|mailbox not found|invalid address", body, re.IGNORECASE):
        return "hard", "pattern_match"
    if re.search(r"mailbox full|quota exceeded|try again|temporarily", body, re.IGNORECASE):
        return "soft", "pattern_match"
    return "hard", "unknown"


def _load_soft_bounce_counts():
    if SOFT_BOUNCE_FILE.exists():
        try:
            return json.loads(SOFT_BOUNCE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_soft_bounce_counts(counts):
    SOFT_BOUNCE_FILE.write_text(json.dumps(counts))


def _load_processed_bounce_fingerprints():
    if PROCESSED_BOUNCE_FILE.exists():
        try:
            data = json.loads(PROCESSED_BOUNCE_FILE.read_text())
            if isinstance(data, list):
                return set(str(x) for x in data if x)
        except Exception:
            pass
    return set()


def _save_processed_bounce_fingerprints(items):
    # Keep recent finite history to avoid unbounded growth.
    trimmed = list(sorted(set(items)))[-8000:]
    PROCESSED_BOUNCE_FILE.write_text(json.dumps(trimmed))


def _log_audit(action, detail):
    try:
        subprocess.run(
            [sys.executable, str(Path(__file__).parent / "audit_helpers.py"),
             "--log", action, "bounce_monitor", "email", detail],
            timeout=10, capture_output=True,
        )
    except Exception:
        pass


def check_bounces():
    config = _load_config()
    gmail = config.get("gmail_address", "")
    password = config.get("gmail_app_password", "")

    if not gmail or not password:
        return {"status": "error", "message": "Gmail credentials not configured"}

    bounces = []
    unsubscribes = []

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(gmail, password)
        mail.select("INBOX")

        processed_ids = set()
        processed_fingerprints = _load_processed_bounce_fingerprints()
        discovered_fingerprints = set()
        seen_bounce_keys = set()
        since_str = (datetime.utcnow() - timedelta(days=3)).strftime("%d-%b-%Y")

        # Search for bounce notifications
        for search_term in [
            'FROM "mailer-daemon"',
            'FROM "postmaster"',
            'FROM "Mail Delivery Subsystem"',
            'SUBJECT "Delivery Status Notification"',
            'SUBJECT "Undeliverable"',
            'SUBJECT "Failure Notice"',
        ]:
            try:
                for scope in ("UNSEEN", f'SINCE "{since_str}"'):
                    status, data = mail.search(None, f"({search_term} {scope})")
                    if status != "OK" or not data[0]:
                        continue

                    for num in data[0].split():
                        if num in processed_ids:
                            continue
                        processed_ids.add(num)
                        try:
                            status, msg_data = mail.fetch(num, "(RFC822)")
                            if status != "OK":
                                continue

                            msg = email_lib.message_from_bytes(msg_data[0][1])
                            body = _extract_body(msg)
                            subject = _decode_subject(msg)
                            recipient = _extract_failed_recipient(body)
                            bounce_type, code = _classify_bounce(body)
                            message_id = (msg.get("Message-ID") or "").strip().lower()
                            key_parts = [
                                message_id,
                                (recipient or "").strip().lower(),
                                bounce_type,
                                str(code or "").strip().lower(),
                            ]
                            fingerprint = "|".join(key_parts)

                            # Prevent duplicate processing when Gmail surfaces
                            # previously-seen DSN notifications again.
                            if fingerprint and fingerprint in processed_fingerprints:
                                continue
                            dedupe_key = ((recipient or "").strip().lower(), str(code or "").strip().lower())
                            if dedupe_key in seen_bounce_keys:
                                continue

                            if recipient:
                                bounces.append({
                                    "recipient": recipient,
                                    "bounce_type": bounce_type,
                                    "error_code": code,
                                    "subject": subject[:100],
                                    "body_preview": body[:300],
                                })
                                seen_bounce_keys.add(dedupe_key)
                                if fingerprint:
                                    discovered_fingerprints.add(fingerprint)

                            # Mark as seen (safe even if already seen)
                            try:
                                mail.store(num, "+FLAGS", "\\Seen")
                            except Exception:
                                pass

                        except Exception as e:
                            log.warning("Error processing bounce message: %s", e)
            except Exception as e:
                log.warning("IMAP search error for %s: %s", search_term, e)

        # Check for unsubscribe replies from vendors
        try:
            conn = sqlite3.connect(str(DB_PATH))
            vendor_emails = [r[0] for r in conn.execute(
                "SELECT DISTINCT LOWER(email) FROM vendors WHERE email IS NOT NULL AND email != ''"
            ).fetchall()]
            conn.close()

            status, data = mail.search(None, "UNSEEN")
            if status == "OK" and data[0]:
                for num in data[0].split()[-50:]:
                    try:
                        status, msg_data = mail.fetch(num, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
                        if status != "OK":
                            continue
                        header_text = msg_data[0][1].decode("utf-8", errors="replace")
                        from_match = re.search(r"From:.*?(\S+@\S+)", header_text)
                        subj_match = re.search(r"Subject:\s*(.*)", header_text)

                        if from_match:
                            from_addr = from_match.group(1).strip("<>").lower()
                            subject_text = subj_match.group(1).strip() if subj_match else ""

                            if from_addr in vendor_emails and UNSUBSCRIBE_RE.search(subject_text):
                                unsubscribes.append({"email": from_addr, "subject": subject_text})
                                mail.store(num, "+FLAGS", "\\Seen")
                    except Exception:
                        continue
        except Exception as e:
            log.warning("Unsubscribe check error: %s", e)

        mail.logout()
        if discovered_fingerprints:
            processed_fingerprints.update(discovered_fingerprints)
            _save_processed_bounce_fingerprints(processed_fingerprints)
    except imaplib.IMAP4.error as e:
        return {"status": "error", "message": "IMAP error: %s" % str(e)}
    except Exception as e:
        return {"status": "error", "message": "Connection error: %s" % str(e)}

    return {"status": "ok", "bounces": bounces, "unsubscribes": unsubscribes}


def handle_bounces(result):
    if result.get("status") != "ok":
        log.warning("Bounce check failed: %s", result.get("message"))
        return

    bounces = result.get("bounces", [])
    unsubscribes = result.get("unsubscribes", [])
    soft_counts = _load_soft_bounce_counts()

    conn = sqlite3.connect(str(DB_PATH))

    for bounce in bounces:
        recipient = (bounce.get("recipient") or "").strip().lower()
        m = EMAIL_ADDR_RE.search(recipient)
        recipient = m.group(0).strip().rstrip(">").rstrip(".") if m else ""
        if not recipient:
            continue
        bounce_type = bounce["bounce_type"]

        if bounce_type == "soft":
            soft_counts[recipient] = soft_counts.get(recipient, 0) + 1
            if soft_counts[recipient] >= 3:
                log.warning("3 soft bounces for %s — treating as hard bounce", recipient)
                bounce_type = "hard"
            else:
                log.info("Soft bounce #%d for %s — will retry", soft_counts[recipient], recipient)
                _log_audit("soft_bounce", "%s (code=%s, count=%d)" % (recipient, bounce["error_code"], soft_counts[recipient]))
                continue

        if bounce_type == "hard":
            # Prevent duplicate hard-bounce bookkeeping when Gmail re-surfaces
            # old delivery notifications (or the same notification arrives again).
            recent_bounce_log = conn.execute(
                "SELECT 1 FROM outbound_log "
                "WHERE channel = 'email' AND result = 'bounced' AND lower(trim(recipient)) = ? "
                "AND timestamp >= datetime('now', '-7 days') "
                "LIMIT 1",
                (recipient,),
            ).fetchone()
            if recent_bounce_log:
                continue

            vendor_rows = conn.execute(
                "SELECT id, name FROM vendors WHERE LOWER(email) = ?",
                (recipient,),
            ).fetchall()
            vendor_ids = [int(r[0]) for r in vendor_rows]

            conn.execute(
                "UPDATE vendors SET email_valid = 0, campaign_eligible = 0, outreach_status = 'bounced' "
                "WHERE LOWER(email) = ?",
                (recipient,),
            )
            conn.execute("DELETE FROM outreach_queue WHERE vendor_id IN (SELECT id FROM vendors WHERE LOWER(email) = ?)", (recipient,))
            conn.execute(
                "UPDATE email_queue "
                "SET send_result = CASE "
                "  WHEN send_result IS NULL OR trim(send_result) = '' THEN ? "
                "  ELSE send_result || ' | ' || ? "
                "END "
                "WHERE LOWER(recipient_email) = ?",
                (f"bounced:{bounce.get('error_code', '')}", f"bounced:{bounce.get('error_code', '')}", recipient),
            )
            for vid in vendor_ids:
                recent_vendor_bounce = conn.execute(
                    "SELECT 1 FROM vendor_outreach "
                    "WHERE vendor_id = ? AND channel = 'email' AND status = 'bounced' "
                    "AND COALESCE(sent_at, created_at) >= datetime('now', '-7 days') "
                    "LIMIT 1",
                    (vid,),
                ).fetchone()
                if recent_vendor_bounce:
                    continue
                conn.execute(
                    "INSERT INTO vendor_outreach (vendor_id, channel, status, response, sent_at) "
                    "VALUES (?, 'email', 'bounced', ?, datetime('now'))",
                    (vid, bounce.get("error_code", "")),
                )
            conn.execute(
                "INSERT INTO outbound_log (channel, recipient, recipient_name, message_preview, result, reason, code_path) "
                "VALUES ('email', ?, '', '', 'bounced', ?, 'bounce_monitor')",
                (recipient, bounce.get("error_code", "")),
            )
            conn.commit()

            try:
                from core.nexus_coordination import log_event
                log_event(
                    "EMAIL_BOUNCED",
                    payload={
                        "recipient": recipient,
                        "error_code": bounce.get("error_code", ""),
                        "bounce_type": "hard",
                    },
                    source="bounce_monitor",
                )
            except Exception:
                pass

            log.warning("HARD BOUNCE: %s — marked invalid, removed from queue", recipient)
            _log_audit("hard_bounce", "%s (code=%s)" % (recipient, bounce["error_code"]))
            _send_telegram("HARD BOUNCE: %s\nError: %s\nVendor marked invalid, removed from queue." % (recipient, bounce["error_code"]))

    for unsub in unsubscribes:
        email_addr = unsub["email"]
        conn.execute("UPDATE vendors SET unsubscribed = 1, campaign_eligible = 0 WHERE LOWER(email) = ?", (email_addr,))
        conn.execute("DELETE FROM outreach_queue WHERE vendor_id IN (SELECT id FROM vendors WHERE LOWER(email) = ?)", (email_addr,))
        conn.commit()
        log.info("UNSUBSCRIBE: %s", email_addr)
        _log_audit("unsubscribe_received", email_addr)
        _send_telegram("UNSUBSCRIBE: %s requested removal. Vendor marked, removed from queue." % email_addr)

    # Check for spam complaints (counted in audit_trail)
    try:
        spam_today = conn.execute(
            "SELECT COUNT(*) FROM audit_trail WHERE action = 'spam_complaint' AND DATE(timestamp) = DATE('now')"
        ).fetchone()[0]
        if spam_today >= 2:
            log.critical("2+ SPAM COMPLAINTS TODAY — triggering emergency stop")
            subprocess.run(
                [sys.executable, str(Path(__file__).parent / "emergency_stop.py"),
                 "--reason", "2+ spam complaints in one day"],
                timeout=15, capture_output=True,
            )
            _send_telegram("EMERGENCY STOP: 2+ spam complaints today. All outbound halted. Review immediately.")
    except Exception:
        pass

    conn.close()
    _save_soft_bounce_counts(soft_counts)


def run_once():
    log.info("Checking for bounces...")
    result = check_bounces()
    handle_bounces(result)
    return result


def main():
    parser = argparse.ArgumentParser(description="Bounce monitor")
    parser.add_argument("--daemon", action="store_true", help="Run continuously")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.daemon:
        log.info("Bounce monitor daemon started (interval=%ds)", CHECK_INTERVAL)
        try:
            from core.agent_runtime import AgentRuntime
            _agent = AgentRuntime('bounce_monitor', 'daemon', 'operations')
            _agent.start()
        except Exception:
            _agent = None
        while _running:
            try:
                run_once()
                if _agent:
                    _agent.heartbeat("Checking Gmail for bounces")
            except Exception as e:
                log.error("Bounce check error: %s", e)
                if _agent:
                    _agent.report_error(str(e), will_retry=True)
            time.sleep(CHECK_INTERVAL)
        if _agent:
            _agent.stop()
        log.info("Bounce monitor daemon stopped")
    else:
        result = check_bounces()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            bounces = result.get("bounces", [])
            unsubs = result.get("unsubscribes", [])
            print("\n  Bounce Monitor Check")
            print("  " + "=" * 40)
            print("  Status: %s" % result.get("status", "unknown"))
            print("  Bounces found: %d" % len(bounces))
            print("  Unsubscribes found: %d" % len(unsubs))
            for b in bounces:
                print("    [%s] %s — code=%s" % (b["bounce_type"].upper(), b["recipient"], b["error_code"]))
            for u in unsubs:
                print("    [UNSUB] %s" % u["email"])
            print("  " + "=" * 40)

        if not args.json:
            handle_bounces(result)


if __name__ == "__main__":
    main()
