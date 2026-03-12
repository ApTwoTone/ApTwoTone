#!/usr/bin/env python3
"""
Zoar Outreach Batch Sender v2 — Bulletproof Edition
====================================================
10 batches of 3. 15 seconds between emails. 5 minutes between batches.
Pre-validates every email (MX check). Category-specific templates.
Contact name personalization. Referral incentive in every email.
Runs automatically at 8 AM via launchd. Zero manual intervention.

Usage:
    python scripts/send_outreach_batch.py              # Send 30 emails
    python scripts/send_outreach_batch.py --count 15   # Send 15 emails
    python scripts/send_outreach_batch.py --dry-run    # Validate + preview only
    python scripts/send_outreach_batch.py --status     # Show outreach stats
"""

import json
import os
import random
import re
import smtplib
import sqlite3
import sys
import time
import uuid
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

import dns.resolver

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.cold_email import generate_cold_email
from core.email_queue_manager import evaluate_vendor_candidate, _email_preflight_reason
from core.outreach_targeting import (
    booking_priority_score,
    booking_priority_sort_key,
    recommended_outreach_angle,
    should_skip_fast_booking_email_candidate,
)
from core.send_window import now_in_window
from integrations.messaging import _build_mime_with_logo
from core.nexus_coordination import (
    get_unconsumed_instructions,
    mark_instruction_consumed,
    log_event,
)

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"
LOG_DIR = Path.home() / ".nexus" / "logs"
LOG_FILE = LOG_DIR / "outreach_batch.log"

BATCH_SIZE = 3
INTRA_BATCH_DELAY = 15   # seconds between emails within a batch
INTER_BATCH_DELAY = 300  # seconds (5 min) between batches
DAILY_LIMIT = 30


# ═══════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════

def log(msg):
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = "[%s] %s" % (timestamp, msg)
    print(line, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def load_config():
    return json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}


def get_db():
    db = sqlite3.connect(str(DB_PATH), timeout=30)
    db.row_factory = sqlite3.Row
    return db


def send_telegram(msg):
    try:
        cfg = load_config()
        token = cfg.get("telegram_token", "")
        if not token:
            return
        url = "https://api.telegram.org/bot%s/sendMessage" % token
        data = json.dumps({"chat_id": "8540603351", "text": msg}).encode()
        req = urllib.request.Request(url, data=data)
        req.add_header("Content-Type", "application/json")
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════
# STEP 1: SAFETY CHECKS — ALL 5 MUST PASS OR ABORT
# ═══════════════════════════════════════════════════════════

def run_safety_checks(config):
    failures = []

    # Check 1: Outbound enabled
    if not config.get("outbound_messages_enabled", False):
        failures.append("outbound_messages_enabled is False")

    # Check 2: Kill switch
    if (Path.home() / ".nexus" / ".kill_switch").exists():
        failures.append("Kill switch is active")

    # Check 3: Daily limit not reached
    db = get_db()
    sent_today = db.execute(
        "SELECT COUNT(*) FROM outbound_log "
        "WHERE DATE(timestamp) = DATE('now', 'localtime') "
        "AND result = 'allowed' AND channel = 'email'"
    ).fetchone()[0]
    db.close()
    if sent_today >= DAILY_LIMIT:
        failures.append("Daily limit reached: %d/%d" % (sent_today, DAILY_LIMIT))

    # Check 4: SMTP works
    gmail = config.get("gmail_address", "zoarbathrooms@gmail.com")
    app_pw = config.get("gmail_app_password", "")
    try:
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10)
        server.login(gmail, app_pw)
        server.quit()
    except Exception as e:
        failures.append("SMTP auth failed: %s" % e)

    # Check 5: Confirm dead man's switch (launchd schedule = operator intent)
    try:
        from core.security import get_security_gate
        gate = get_security_gate()
        gate.confirm_alive("launchd_email_outreach")
    except Exception as e:
        failures.append("Dead man switch confirm failed: %s" % e)

    # Check 6: Send window (config-driven; default 08:00-17:00 Pacific)
    try:
        inside, now_local, win = now_in_window()
        if not inside:
            failures.append(
                "Outside send window: %02d:00 (allowed: %02d-%02d %s)"
                % (now_local.hour, win.start_hour, win.end_hour, win.timezone)
            )
    except Exception:
        pass

    return failures


def apply_kai_instructions_before_batch():
    """Read Kai instructions and apply hard-stop / prioritization directives."""
    actions = {
        "pause_batch": False,
        "pause_reason": "",
        "prioritize_wedding": False,
    }
    try:
        instructions = get_unconsumed_instructions(limit=25)
    except Exception:
        return actions

    # Process oldest-first to preserve instruction chronology.
    instructions = list(reversed(instructions))
    for inst in instructions:
        text = (inst.get("instruction") or "").strip()
        text_l = text.lower()
        inst_id = int(inst.get("id", 0) or 0)

        # Hard stop / pause directives.
        if any(
            phrase in text_l
            for phrase in (
                "stop email",
                "pause email",
                "halt email",
                "don't send",
                "dont send",
                "no emails",
            )
        ):
            actions["pause_batch"] = True
            actions["pause_reason"] = text

        # Prioritization hints.
        if "wedding" in text_l and any(k in text_l for k in ("priority", "prioritize", "focus")):
            actions["prioritize_wedding"] = True

        if inst_id:
            try:
                mark_instruction_consumed(inst_id, "batch_sender")
            except Exception:
                pass

    return actions


# ═══════════════════════════════════════════════════════════
# STEP 2: GET AND VALIDATE VENDORS
# ═══════════════════════════════════════════════════════════

def validate_email_address(email):
    if not email or "@" not in email:
        return False, "invalid_format"

    if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
        return False, "invalid_format"

    domain = email.split("@")[1]

    bad_domains = [
        "example.com", "test.com", "fake.com", "temp-mail.org",
        "guerrillamail.com", "mailinator.com", "throwaway.email",
    ]
    if domain in bad_domains:
        return False, "known_bad_domain"

    try:
        mx_records = dns.resolver.resolve(domain, "MX")
        if not mx_records:
            return False, "no_mx_records"
    except dns.resolver.NXDOMAIN:
        return False, "domain_does_not_exist: %s" % domain
    except dns.resolver.NoAnswer:
        return False, "no_mx_answer: %s" % domain
    except dns.resolver.NoNameservers:
        return False, "no_nameservers: %s" % domain
    except Exception as e:
        log("    DNS check inconclusive for %s: %s" % (domain, e))
        return True, "dns_inconclusive_allowing"

    return True, "valid"


def get_vendors_to_email(limit=40):
    from core.vendor_blacklist import get_blacklist_sql_clauses

    db = get_db()
    blacklist_clauses = get_blacklist_sql_clauses()
    raw_limit = max(int(limit) * 15, 300)
    raw_vendors = db.execute(
        "SELECT id, name, email, category, city, contact_name, referral_score, "
        "phone, website, website_status, phone_valid, email_valid, "
        "campaign_quality, source, notes, outreach_status, "
        "contact_name_source, contact_name_confidence, contact_name_verified "
        "FROM vendors "
        "WHERE campaign_eligible = 1 "
        "AND email IS NOT NULL AND length(email) > 0 "
        "AND (outreach_status IS NULL OR outreach_status NOT IN ('sent', 'replied', 'bounced', 'skipped', 'blacklisted')) "
        "AND email NOT IN (SELECT email FROM contact_blocklist WHERE active = 1) "
        "AND email NOT IN ("
        "  SELECT recipient FROM outbound_log "
        "  WHERE channel = 'email' AND result IN ('sent', 'allowed')"
        "  AND recipient IS NOT NULL AND recipient != '' AND recipient != 'None' "
        ") "
        "AND ("
        "  lower(category) LIKE '%venue%' OR "
        "  lower(category) LIKE '%planner%' OR "
        "  lower(category) LIKE '%rental%' OR "
        "  lower(category) LIKE '%cater%' OR "
        "  lower(category) IN ('banquet_hall', 'party_rental', 'tent_rental', 'event_planner', 'wedding_planner', 'quinceanera_venue', 'country_club', 'community_center', 'church', 'church_hall', 'hotel_venue', 'winery', 'winery_venue')"
        ") "
        + blacklist_clauses + " "
        "ORDER BY "
        "campaign_quality DESC, "
        "referral_score DESC, "
        "CASE WHEN contact_name IS NOT NULL AND contact_name != '' THEN 0 ELSE 1 END, "
        "id ASC "
        "LIMIT ?",
        (raw_limit,),
    ).fetchall()
    rejection_counts = {}
    selected = []

    for row in raw_vendors:
        vendor = dict(row)
        decision = evaluate_vendor_candidate(vendor)
        preflight_reason = _email_preflight_reason(db, vendor.get("email", ""))
        skip_reason = should_skip_fast_booking_email_candidate(
            vendor,
            decision,
            preflight_reason=preflight_reason,
        )
        if skip_reason:
            rejection_counts[skip_reason] = rejection_counts.get(skip_reason, 0) + 1
            continue
        vendor["email"] = decision.get("normalized_email") or vendor.get("email", "")
        vendor["_selection_decision"] = decision
        vendor["_selection_priority_score"] = booking_priority_score(vendor, decision)
        vendor["_selection_angle"] = recommended_outreach_angle(vendor)
        selected.append(vendor)

    selected.sort(key=lambda vendor: booking_priority_sort_key(vendor, vendor["_selection_decision"]))
    get_vendors_to_email.last_rejections = rejection_counts
    get_vendors_to_email.last_pool_count = len(raw_vendors)
    db.close()
    return selected[:limit]


def pre_validate_all(vendors, target_count):
    valid = []
    invalid = []

    for v in vendors:
        is_valid, reason = validate_email_address(v["email"])
        if is_valid:
            valid.append(v)
        else:
            invalid.append((v, reason))
            log("  INVALID: %s -- %s -- %s" % (v["name"], v["email"], reason))
            db = get_db()
            db.execute(
                "UPDATE vendors SET campaign_eligible = 0, email_valid = 0 WHERE id = ?",
                (v["id"],),
            )
            db.commit()
            db.close()

    return valid[:target_count], invalid


# ═══════════════════════════════════════════════════════════
# STEP 3: SEND A SINGLE EMAIL (DIRECT SMTP)
# ═══════════════════════════════════════════════════════════

def send_single_email(config, vendor, subject, plain_body, html_body):
    gmail = config.get("gmail_address", "zoarbathrooms@gmail.com")
    app_pw = config.get("gmail_app_password", "")
    recipient = vendor["email"]
    from_addr = "Zoar Bathroom Rentals <%s>" % gmail

    try:
        extra_headers = {
            "Reply-To": gmail,
            "Message-ID": "<%s@zoarbathroomrental.com>" % uuid.uuid4().hex[:12],
            "List-Unsubscribe": "<mailto:%s?subject=unsubscribe>" % gmail,
        }

        msg = _build_mime_with_logo(
            subject, from_addr, recipient, plain_body, html_body, extra_headers
        )

        server = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15)
        server.login(gmail, app_pw)
        server.sendmail(gmail, recipient, msg.as_string())
        server.quit()

        return True, "sent"
    except Exception as e:
        return False, str(e)


def log_send_to_db(vendor, success, subject_line):
    db = get_db()
    try:
        db.execute(
            "INSERT INTO outbound_log (channel, recipient, recipient_name, "
            "message_preview, result, reason, code_path) "
            "VALUES ('email', ?, ?, ?, ?, ?, 'send_outreach_batch_v2')",
            (
                vendor["email"],
                vendor["name"],
                subject_line[:100] if subject_line else "",
                "allowed" if success else "failed",
                "batch_sender_v2" if success else "smtp_error",
            ),
        )
    except Exception as e:
        log("  DB ERROR (outbound_log): %s" % e)

    if success:
        try:
            db.execute(
                "UPDATE vendors SET outreach_status = 'sent', status = 'contacted' "
                "WHERE id = ?",
                (vendor["id"],),
            )
        except Exception as e:
            log("  DB ERROR (vendor update): %s" % e)

    try:
        db.commit()
    except Exception as e:
        log("  DB COMMIT ERROR: %s" % e)
    db.close()


def was_already_emailed(email):
    """Hard dedup: check outbound_log for ANY prior successful send to this address.
    This is the LAST line of defense — no email goes out twice. Ever."""
    if not email or email == 'None':
        return True  # treat garbage as already-sent to block it
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM outbound_log "
        "WHERE recipient = ? AND channel = 'email' AND result IN ('sent', 'allowed') "
        "LIMIT 1",
        (email,),
    ).fetchone()
    db.close()
    return row is not None


def is_blocklisted(email):
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM contact_blocklist WHERE email = ? AND active = 1",
        (email,),
    ).fetchone()
    db.close()
    return row is not None


# ═══════════════════════════════════════════════════════════
# STATUS DISPLAY
# ═══════════════════════════════════════════════════════════

def show_status():
    db = get_db()
    total = db.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
    eligible = db.execute(
        "SELECT COUNT(*) FROM vendors WHERE campaign_eligible=1 "
        "AND email IS NOT NULL AND length(email)>0"
    ).fetchone()[0]
    sent = db.execute(
        "SELECT COUNT(*) FROM vendors WHERE outreach_status='sent'"
    ).fetchone()[0]
    sent_today = db.execute(
        "SELECT COUNT(*) FROM outbound_log "
        "WHERE DATE(timestamp) = DATE('now', 'localtime') "
        "AND result = 'allowed' AND channel = 'email'"
    ).fetchone()[0]
    db.close()

    print("\n  OUTREACH STATUS")
    print("  " + "=" * 50)
    print("  Total vendors: %d" % total)
    print("  Eligible (email + valid): %d" % eligible)
    print("  Already contacted: %d" % sent)
    print("  Remaining: %d" % (eligible - sent))
    print("  Sent today: %d / %d" % (sent_today, DAILY_LIMIT))
    print("  " + "=" * 50)


# ═══════════════════════════════════════════════════════════
# MAIN EXECUTION
# ═══════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Zoar Outreach Batch Sender v2")
    parser.add_argument("--count", type=int, default=30, help="Emails to send (default: 30)")
    parser.add_argument("--dry-run", action="store_true", help="Validate + preview only")
    parser.add_argument("--status", action="store_true", help="Show outreach stats")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    total_target = args.count
    batch_count = (total_target + BATCH_SIZE - 1) // BATCH_SIZE

    try:
        from core.agent_runtime import AgentRuntime
        _agent = AgentRuntime("outreach-batch", "d1_vendor",
                              launchd_label="com.zoar.email-outreach")
        _agent.heartbeat("Starting outreach batch")
    except Exception:
        _agent = None

    log("=" * 60)
    log("ZOAR OUTREACH v2 -- BULLETPROOF BATCH SENDER")
    log("=" * 60)

    config = load_config()

    # ── SAFETY CHECKS ──
    log("Running 5 safety checks...")
    failures = run_safety_checks(config)
    if failures and not args.dry_run:
        if any("Daily limit reached" in f for f in failures):
            try:
                log_event(
                    "DAILY_LIMIT_REACHED",
                    payload={"failures": failures, "daily_limit": DAILY_LIMIT},
                    source="send_outreach_batch",
                )
            except Exception:
                pass
        if any("SMTP auth failed" in f for f in failures):
            try:
                log_event(
                    "SMTP_AUTH_FAILED",
                    payload={"failures": failures},
                    source="send_outreach_batch",
                )
            except Exception:
                pass
        msg = "ABORTED -- Safety check failures:\n" + "\n".join(
            "  - %s" % f for f in failures
        )
        log(msg)
        # Only notify Telegram for unexpected failures (SMTP), not routine blocks
        is_routine = all(
            any(k in f for k in ("send window", "Daily limit"))
            for f in failures
        )
        if not is_routine:
            send_telegram("EMAIL OUTREACH ABORTED\n\n%s" % msg)
        # Exit 0 for expected blocks (Sunday, window, daily limit)
        # Exit 1 only for real errors (SMTP fail) so launchd doesn't retry
        sys.exit(0 if is_routine else 1)
    log("All 5 safety checks passed")

    # ── KAI INSTRUCTIONS ──
    inst_actions = apply_kai_instructions_before_batch()
    if inst_actions["pause_batch"] and not args.dry_run:
        reason = inst_actions["pause_reason"] or "manual pause instruction"
        log("PAUSED -- Kai instruction: %s" % reason)
        send_telegram("⏸ Email batch paused by Kai instruction:\n\n%s" % reason)
        try:
            log_event(
                "KAI_INSTRUCTION",
                payload={"action": "pause_batch", "reason": reason},
                source="send_outreach_batch",
                push_alert=False,
            )
        except Exception:
            pass
        sys.exit(0)

    # ── GET VENDORS ──
    log("Pulling booking-first outreach candidates...")
    raw_vendors = get_vendors_to_email(limit=total_target)
    pool_count = getattr(get_vendors_to_email, "last_pool_count", len(raw_vendors))
    log("  Evaluated %d candidates, kept %d booking-first targets" % (pool_count, len(raw_vendors)))
    rejection_counts = getattr(get_vendors_to_email, "last_rejections", {})
    if rejection_counts:
        top_rejections = sorted(rejection_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
        log(
            "  Top rejects: %s"
            % ", ".join("%s=%d" % (reason, count) for reason, count in top_rejections)
        )

    if len(raw_vendors) == 0:
        msg = "ABORTED -- No vendors to email!"
        log(msg)
        send_telegram(msg)
        sys.exit(1)

    if len(raw_vendors) < total_target:
        log("  Only %d available (wanted %d)" % (len(raw_vendors), total_target))

    if inst_actions.get("prioritize_wedding"):
        log("Applying Kai priority instruction: wedding/quince leads first")
        raw_vendors.sort(
            key=lambda v: (
                0
                if ("wedding" in (v.get("category") or "").lower() or "quince" in (v.get("category") or "").lower())
                else 1,
                -float(v.get("referral_score") or 0),
            )
        )

    # ── PRE-VALIDATE EVERY EMAIL ──
    log("Pre-validating all email addresses (MX record check)...")
    valid_vendors, invalid_vendors = pre_validate_all(raw_vendors, total_target)
    log("  Valid: %d" % len(valid_vendors))
    log("  Invalid: %d" % len(invalid_vendors))

    if not valid_vendors:
        msg = "ABORTED -- No valid vendors after MX validation!"
        log(msg)
        send_telegram(msg)
        sys.exit(1)

    actual_count = len(valid_vendors)
    actual_batches = (actual_count + BATCH_SIZE - 1) // BATCH_SIZE

    if args.dry_run:
        log("\nDRY RUN -- would send %d emails in %d batches" % (actual_count, actual_batches))
        for i, v in enumerate(valid_vendors):
            email_data = generate_cold_email(
                v["name"], v.get("category", ""),
                v.get("contact_name", ""), v.get("city", ""),
            )
            tmpl = email_data.get("template_type", "?")
            contact = v.get("contact_name", "")
            greeting = "Hi %s" % contact if contact else "Hi there"
            log("  [%d] %s <%s> -- %s, %s -- template: %s -- %s -- score=%s" % (
                i + 1, v["name"], v["email"],
                v.get("category", "?"), v.get("city", "?"),
                tmpl, greeting, v.get("_selection_priority_score", "?"),
            ))
            log("      angle: %s" % v.get("_selection_angle", ""))
        return

    # ── START SENDING ──
    est_minutes = actual_batches * 5
    send_telegram(
        "EMAIL OUTREACH STARTING\n\n"
        "%d emails in %d batches of %d\n"
        "15s between emails, 5 min between batches\n"
        "Estimated completion: ~%d minutes"
        % (actual_count, actual_batches, BATCH_SIZE, est_minutes)
    )

    log("\nSENDING %d emails in %d batches\n" % (actual_count, actual_batches))

    total_sent = 0
    total_failed = 0

    for batch_num in range(actual_batches):
        start_idx = batch_num * BATCH_SIZE
        end_idx = min(start_idx + BATCH_SIZE, actual_count)
        batch = valid_vendors[start_idx:end_idx]

        log("--- BATCH %d/%d ---" % (batch_num + 1, actual_batches))
        batch_names = []

        for i, vendor in enumerate(batch):
            name = vendor["name"]
            email = vendor["email"]
            contact = vendor.get("contact_name", "") or ""
            category = vendor.get("category", "") or ""
            city = vendor.get("city", "") or ""

            # BLACKLIST CHECK: defense in depth — catches anything SQL missed
            from core.vendor_blacklist import is_vendor_blacklisted
            is_blocked, block_reason = is_vendor_blacklisted(name, category, email)
            if is_blocked:
                log("  BLACKLISTED: %s -- %s (%s)" % (name, email, block_reason))
                try:
                    db = get_db()
                    db.execute(
                        "UPDATE vendors SET campaign_eligible = 0, outreach_status = 'blacklisted' WHERE email = ?",
                        (email,),
                    )
                    db.commit()
                    db.close()
                except Exception:
                    pass
                continue

            # HARD DEDUP: never send to same address twice (checks outbound_log)
            if was_already_emailed(email):
                log("  SKIPPED (already emailed): %s -- %s" % (name, email))
                # Fix the vendor record so they don't appear in future queries
                try:
                    db = get_db()
                    db.execute(
                        "UPDATE vendors SET outreach_status = 'sent' WHERE email = ?",
                        (email,),
                    )
                    db.commit()
                    db.close()
                except Exception:
                    pass
                continue

            # Re-check blocklist (new entries could appear mid-batch)
            if is_blocklisted(email):
                log("  BLOCKED (blocklist): %s -- %s" % (name, email))
                total_failed += 1
                continue

            # Generate category-specific email
            email_data = generate_cold_email(name, category, contact, city)
            if "error" in email_data:
                log("  CONTENT ERROR: %s -- %s" % (name, email_data["error"]))
                total_failed += 1
                continue

            subject = email_data["subject"]
            plain_body = email_data["plain_body"]
            html_body = email_data["html_body"]
            tmpl_type = email_data.get("template_type", "?")

            # SEND
            success, result = send_single_email(config, vendor, subject, plain_body, html_body)

            if success:
                total_sent += 1
                greeting = contact if contact else "Hi there"
                log("  [%d/%d] SENT %s (%s) -> %s -- %s" % (
                    total_sent, actual_count, name, tmpl_type, email, greeting))
                batch_names.append("%s (%s, %s)" % (contact or name, category, city))
                log_send_to_db(vendor, True, subject)
                try:
                    log_event(
                        "EMAIL_SENT",
                        payload={
                            "vendor_id": vendor.get("id"),
                            "vendor_name": name,
                            "recipient": email,
                            "category": category,
                            "city": city,
                            "template": tmpl_type,
                        },
                        source="send_outreach_batch",
                        vendor_id=vendor.get("id"),
                        push_alert=False,
                    )
                except Exception:
                    pass
            else:
                total_failed += 1
                log("  FAILED: %s -> %s -- %s" % (name, email, result))
                log_send_to_db(vendor, False, subject)

                # SMTP auth failure = abort everything
                if "auth" in result.lower():
                    log("SMTP AUTH FAILURE -- ABORTING")
                    send_telegram(
                        "SMTP AUTH FAILED -- outreach aborted after %d sends" % total_sent
                    )
                    try:
                        log_event(
                            "SMTP_AUTH_FAILED",
                            payload={
                                "error": result,
                                "sent_before_abort": total_sent,
                            },
                            source="send_outreach_batch",
                        )
                    except Exception:
                        pass
                    sys.exit(1)

            # Intra-batch delay (except after last email in batch)
            if i < len(batch) - 1:
                delay = INTRA_BATCH_DELAY + random.uniform(-3, 3)
                time.sleep(delay)

        log("Batch %d done: %s" % (batch_num + 1, ", ".join(batch_names) or "no sends"))
        if _agent:
            _agent.heartbeat("Batch %d/%d — %d sent, %d failed" % (batch_num + 1, actual_batches, total_sent, total_failed))

        # Telegram per batch
        if batch_names:
            send_telegram(
                "Batch %d/%d sent: %s\n(%d total, %d failed)"
                % (batch_num + 1, actual_batches, ", ".join(batch_names),
                   total_sent, total_failed)
            )

        # Inter-batch delay (except after last batch)
        if batch_num < actual_batches - 1:
            jitter = random.uniform(-10, 10)
            delay = INTER_BATCH_DELAY + jitter
            log("Pausing %ds before batch %d..." % (int(delay), batch_num + 2))
            time.sleep(delay)

    # ── FINAL REPORT ──
    log("=" * 60)
    log("COMPLETE: %d sent, %d failed" % (total_sent, total_failed))
    log("=" * 60)

    send_telegram(
        "EMAIL OUTREACH COMPLETE\n\n"
        "Sent: %d\n"
        "Failed: %d\n"
        "Invalid emails caught pre-send: %d\n\n"
        "Done at %s\n\n"
        "Watch for replies. Follow-ups in 5 days for non-responders."
        % (total_sent, total_failed, len(invalid_vendors),
           datetime.now().strftime("%I:%M %p"))
    )

    if _agent:
        _agent.complete_task("Sent: %d, Failed: %d" % (total_sent, total_failed))


if __name__ == "__main__":
    main()
