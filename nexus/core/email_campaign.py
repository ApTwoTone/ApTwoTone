"""
Email Campaign Engine — Throttled cold email sending with warm-up schedule.

Flow: create_campaign → send_test_batch (to Kai) → approve → process_campaign_batch (drip)
All emails go through outbound_gate() (Rule 0 blocklist enforced).
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

log = logging.getLogger("email_campaign")

DB_PATH = Path.home() / ".nexus" / "memory.db"
TEST_EMAIL = "kaiescobar09@gmail.com"

# Warm-up schedule: day number → max emails/day
# Conservative ramp to protect Gmail sender reputation
WARMUP_SCHEDULE = {
    1: 30,  # 30 emails/day from Day 1
}
MAX_DAILY = 30
MAX_HOURLY = 15
SEND_DELAY_MIN = 10    # 10-20s between emails within a batch (inter-batch pause in CLI)
SEND_DELAY_MAX = 20

BOUNCE_PAUSE_THRESHOLD = 0.05  # 5% bounce rate → auto-pause


def _conn(db_path=None):
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# ── Campaign CRUD ─────────────────────────────────────────────────────────────

def create_campaign(name, target_categories=None, target_tiers=None,
                    min_score=30, daily_limit=None, db_path=None):
    # type: (str, Optional[List[str]], Optional[List[int]], int, Optional[int], Optional[str]) -> Dict
    """Create a campaign and populate queued sends from eligible vendors."""
    conn = _conn(db_path)

    if target_tiers is None:
        target_tiers = [1, 2, 3]
    if daily_limit is None:
        daily_limit = WARMUP_SCHEDULE.get(1, 30)

    # Insert campaign
    cur = conn.execute(
        """INSERT INTO email_campaigns (name, target_categories, target_tiers,
           min_score, daily_limit) VALUES (?, ?, ?, ?, ?)""",
        (name, json.dumps(target_categories or []),
         json.dumps(target_tiers), min_score, daily_limit),
    )
    campaign_id = cur.lastrowid
    conn.commit()

    # Query eligible vendors
    query = (
        "SELECT id, name, email, category FROM vendors "
        "WHERE campaign_eligible = 1 AND referral_score >= ? "
        "AND email_valid = 1"
    )
    params = [min_score]  # type: List

    tier_placeholders = ",".join("?" * len(target_tiers))
    query += " AND distance_tier IN (%s)" % tier_placeholders
    params.extend(target_tiers)

    if target_categories:
        cat_placeholders = ",".join("?" * len(target_categories))
        query += " AND category IN (%s)" % cat_placeholders
        params.extend(target_categories)

    # Exclude already-contacted vendors and unsubscribes
    query += (
        " AND outreach_status NOT IN ('sent', 'replied', 'approved') "
        " AND email NOT IN (SELECT email FROM email_unsubscribes) "
        " AND email NOT IN (SELECT email FROM contact_blocklist WHERE active = 1) "
    )

    query += " ORDER BY referral_score DESC, distance_tier ASC"
    vendors = conn.execute(query, params).fetchall()

    # Populate sends
    sends = []
    for v in vendors:
        sends.append((campaign_id, v["id"], v["email"]))

    if sends:
        conn.executemany(
            "INSERT INTO email_campaign_sends (campaign_id, vendor_id, email) "
            "VALUES (?, ?, ?)",
            sends,
        )

    conn.execute(
        "UPDATE email_campaigns SET total_target = ? WHERE id = ?",
        (len(sends), campaign_id),
    )
    conn.commit()
    conn.close()

    log.info("Campaign %d created: '%s' with %d vendors queued", campaign_id, name, len(sends))
    return {
        "campaign_id": campaign_id,
        "name": name,
        "total_target": len(sends),
        "tiers": target_tiers,
        "min_score": min_score,
    }


def get_campaign(campaign_id, db_path=None):
    # type: (int, Optional[str]) -> Optional[Dict]
    """Get campaign details with live stats."""
    conn = _conn(db_path)
    row = conn.execute(
        "SELECT * FROM email_campaigns WHERE id = ?", (campaign_id,)
    ).fetchone()
    if not row:
        conn.close()
        return None

    d = dict(row)

    # Live counts
    stats = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM email_campaign_sends "
        "WHERE campaign_id = ? GROUP BY status",
        (campaign_id,),
    ).fetchall()
    d["send_stats"] = {r["status"]: r["cnt"] for r in stats}

    # Today's sends
    today_sent = conn.execute(
        "SELECT COUNT(*) FROM email_campaign_sends "
        "WHERE campaign_id = ? AND status = 'sent' AND sent_at >= date('now')",
        (campaign_id,),
    ).fetchone()[0]
    d["sent_today"] = today_sent

    conn.close()
    return d


def list_campaigns(db_path=None):
    # type: (Optional[str],) -> List[Dict]
    conn = _conn(db_path)
    rows = conn.execute(
        "SELECT * FROM email_campaigns ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def approve_campaign(campaign_id, approved_by="kai_telegram", db_path=None):
    # type: (int, str, Optional[str]) -> bool
    conn = _conn(db_path)
    conn.execute(
        "UPDATE email_campaigns SET status = 'active', approved_by = ?, "
        "approved_at = datetime('now'), started_at = datetime('now') "
        "WHERE id = ? AND status IN ('draft', 'testing', 'approved')",
        (approved_by, campaign_id),
    )
    conn.commit()
    affected = conn.total_changes
    conn.close()
    log.info("Campaign %d approved by %s", campaign_id, approved_by)
    return affected > 0


def pause_campaign(campaign_id, db_path=None):
    # type: (int, Optional[str]) -> bool
    conn = _conn(db_path)
    conn.execute(
        "UPDATE email_campaigns SET status = 'paused' WHERE id = ? AND status = 'active'",
        (campaign_id,),
    )
    conn.commit()
    conn.close()
    log.info("Campaign %d paused", campaign_id)
    return True


def resume_campaign(campaign_id, db_path=None):
    # type: (int, Optional[str]) -> bool
    conn = _conn(db_path)
    conn.execute(
        "UPDATE email_campaigns SET status = 'active' WHERE id = ? AND status = 'paused'",
        (campaign_id,),
    )
    conn.commit()
    conn.close()
    log.info("Campaign %d resumed", campaign_id)
    return True


# ── Test Batch ────────────────────────────────────────────────────────────────

async def send_test_batch(campaign_id, test_email=None, count=20, db_path=None):
    # type: (int, Optional[str], int, Optional[str]) -> Dict
    """Send test emails to Kai's inbox. Does NOT mark sends as sent."""
    if test_email is None:
        test_email = TEST_EMAIL

    conn = _conn(db_path)

    # Get first N queued sends
    rows = conn.execute(
        "SELECT ecs.id, ecs.vendor_id, ecs.email, v.name, v.category "
        "FROM email_campaign_sends ecs "
        "JOIN vendors v ON v.id = ecs.vendor_id "
        "WHERE ecs.campaign_id = ? AND ecs.status = 'queued' "
        "ORDER BY v.referral_score DESC LIMIT ?",
        (campaign_id, count),
    ).fetchall()
    conn.close()

    if not rows:
        return {"ok": False, "error": "No queued sends found"}

    from core.cold_email import generate_cold_email
    from integrations.messaging import get_messenger

    messenger = get_messenger()
    sent = []
    errors = []

    for row in rows:
        email_data = generate_cold_email(
            business_name=row["name"],
            category=row["category"] or "",
        )

        if "error" in email_data:
            errors.append({"vendor": row["name"], "error": email_data["error"]})
            continue

        # Send to test email, not the actual vendor
        subject = "[TEST] %s" % email_data["subject"]
        try:
            result = await messenger.send_email(
                to_email=test_email,
                subject=subject,
                body=email_data["plain_body"],
                html_body=email_data.get("html_body"),
            )
            if result.get("ok"):
                sent.append({
                    "vendor": row["name"],
                    "category": row["category"],
                    "subject": email_data["subject"],
                })
            else:
                errors.append({"vendor": row["name"], "error": result.get("error", "unknown")})
        except Exception as e:
            errors.append({"vendor": row["name"], "error": str(e)})

        # Small delay between test sends
        await asyncio.sleep(2)

    # Update campaign status to testing
    conn2 = _conn(db_path)
    conn2.execute(
        "UPDATE email_campaigns SET status = 'testing' WHERE id = ? AND status = 'draft'",
        (campaign_id,),
    )
    conn2.commit()
    conn2.close()

    log.info("Test batch: %d sent, %d errors for campaign %d", len(sent), len(errors), campaign_id)
    return {
        "ok": True,
        "sent_count": len(sent),
        "error_count": len(errors),
        "sent": sent,
        "errors": errors,
        "test_email": test_email,
    }


# ── Campaign Batch Processing ────────────────────────────────────────────────

def _get_daily_limit(campaign):
    # type: (Dict,) -> int
    """Get today's send limit based on warm-up schedule."""
    started_at = campaign.get("started_at", "")
    if not started_at:
        return WARMUP_SCHEDULE.get(1, 30)

    try:
        start = datetime.fromisoformat(started_at)
        day_num = (datetime.utcnow() - start).days + 1
    except (ValueError, TypeError):
        day_num = 1

    # Find the applicable limit from warmup schedule
    for day in sorted(WARMUP_SCHEDULE.keys(), reverse=True):
        if day_num >= day:
            return WARMUP_SCHEDULE[day]
    return MAX_DAILY


async def process_campaign_batch(campaign_id, db_path=None, approval_id=None, messenger=None):
    # type: (int, Optional[str], Optional[int], Optional[object]) -> Dict
    """Process next batch of sends for an active campaign."""
    conn = _conn(db_path)

    # Get campaign
    campaign = conn.execute(
        "SELECT * FROM email_campaigns WHERE id = ?", (campaign_id,)
    ).fetchone()
    if not campaign:
        conn.close()
        return {"ok": False, "error": "Campaign not found"}

    campaign = dict(campaign)
    if campaign["status"] != "active":
        conn.close()
        return {"ok": False, "error": "Campaign not active (status: %s)" % campaign["status"]}

    # Send window enforcement (Pacific time)
    try:
        _cfg = json.loads((Path.home() / ".nexus" / "config.json").read_text())
        _win = _cfg.get("send_window", {})
        _tz = ZoneInfo(_win.get("timezone", "America/Los_Angeles"))
        _now = datetime.now(_tz)
        _start_h = int(_win.get("start", "09:00").split(":")[0])
        _end_h = int(_win.get("end", "17:00").split(":")[0])
        if _now.hour < _start_h or _now.hour >= _end_h:
            conn.close()
            return {"ok": True, "sent": 0, "reason": "Outside send window (%02d:00-%02d:00)" % (_start_h, _end_h)}
        if _now.weekday() == 6:  # Sunday
            conn.close()
            return {"ok": True, "sent": 0, "reason": "No sends on Sunday"}
    except Exception:
        pass  # If config missing, allow sends (existing behavior)

    # Check daily limit
    daily_limit = _get_daily_limit(campaign)
    sent_today = conn.execute(
        "SELECT COUNT(*) FROM email_campaign_sends "
        "WHERE campaign_id = ? AND status = 'sent' AND sent_at >= date('now')",
        (campaign_id,),
    ).fetchone()[0]

    daily_remaining = daily_limit - sent_today
    if daily_remaining <= 0:
        conn.close()
        return {"ok": True, "sent": 0, "reason": "Daily limit reached (%d)" % daily_limit}

    # Check hourly limit
    sent_this_hour = conn.execute(
        "SELECT COUNT(*) FROM email_campaign_sends "
        "WHERE campaign_id = ? AND status = 'sent' "
        "AND sent_at >= datetime('now', '-1 hour')",
        (campaign_id,),
    ).fetchone()[0]

    hourly_remaining = MAX_HOURLY - sent_this_hour
    if hourly_remaining <= 0:
        conn.close()
        return {"ok": True, "sent": 0, "reason": "Hourly limit reached (%d)" % MAX_HOURLY}

    # Get next batch
    batch_size = min(daily_remaining, hourly_remaining, 3)
    rows = conn.execute(
        "SELECT ecs.id, ecs.vendor_id, ecs.email, v.name, v.category, "
        "v.contact_name, v.city, v.referral_score "
        "FROM email_campaign_sends ecs "
        "JOIN vendors v ON v.id = ecs.vendor_id "
        "WHERE ecs.campaign_id = ? AND ecs.status = 'queued' "
        "ORDER BY v.referral_score DESC LIMIT ?",
        (campaign_id, batch_size),
    ).fetchall()
    conn.close()

    if not rows:
        # Mark campaign as completed
        conn2 = _conn(db_path)
        conn2.execute(
            "UPDATE email_campaigns SET status = 'completed' WHERE id = ?",
            (campaign_id,),
        )
        conn2.commit()
        conn2.close()
        return {"ok": True, "sent": 0, "reason": "All sends complete"}

    from core.cold_email import generate_cold_email

    if messenger is None:
        from integrations.messaging import get_messenger
        messenger = get_messenger()
    if messenger is None:
        return {"ok": False, "error": "Messenger not initialized"}
    sent_count = 0
    skip_count = 0
    vendors_processed = []

    for row in rows:
        email = row["email"]

        # Check unsubscribes
        conn3 = _conn(db_path)
        unsub = conn3.execute(
            "SELECT 1 FROM email_unsubscribes WHERE email = ?", (email,)
        ).fetchone()
        if unsub:
            conn3.execute(
                "UPDATE email_campaign_sends SET status = 'skipped', "
                "error = 'unsubscribed' WHERE id = ?",
                (row["id"],),
            )
            conn3.commit()
            conn3.close()
            skip_count += 1
            continue

        # Check blocklist
        blocked = conn3.execute(
            "SELECT 1 FROM contact_blocklist WHERE (email = ? OR phone = ?) AND active = 1",
            (email, ""),
        ).fetchone()
        if blocked:
            conn3.execute(
                "UPDATE email_campaign_sends SET status = 'skipped', "
                "error = 'blocklisted' WHERE id = ?",
                (row["id"],),
            )
            conn3.commit()
            conn3.close()
            skip_count += 1
            continue

        conn3.close()

        # Generate email
        email_data = generate_cold_email(
            business_name=row["name"],
            category=row["category"] or "",
            contact_name=row["contact_name"] or "",
            city=row["city"] or "",
        )
        if "error" in email_data:
            conn4 = _conn(db_path)
            conn4.execute(
                "UPDATE email_campaign_sends SET status = 'skipped', "
                "error = ? WHERE id = ?",
                (email_data["error"], row["id"]),
            )
            conn4.commit()
            conn4.close()
            skip_count += 1
            continue

        # Send via messenger (goes through outbound_gate)
        try:
            result = await messenger.send_b2b_email(
                to_email=email,
                subject=email_data["subject"],
                plain_body=email_data["plain_body"],
                html_body=email_data.get("html_body"),
                approval_id=approval_id,
            )

            conn5 = _conn(db_path)
            if result.get("ok"):
                conn5.execute(
                    "UPDATE email_campaign_sends SET status = 'sent', "
                    "subject = ?, plain_body = ?, sent_at = datetime('now') "
                    "WHERE id = ?",
                    (email_data["subject"], email_data["plain_body"][:500], row["id"]),
                )
                conn5.execute(
                    "UPDATE vendors SET outreach_status = 'sent', status = 'contacted' "
                    "WHERE id = ?",
                    (row["vendor_id"],),
                )
                conn5.execute(
                    "UPDATE email_campaigns SET sent_count = sent_count + 1 "
                    "WHERE id = ?",
                    (campaign_id,),
                )
                sent_count += 1
                vendors_processed.append({
                    "name": row["name"], "email": email,
                    "category": row["category"] or "",
                    "city": row["city"] if "city" in row.keys() else "",
                    "status": "sent",
                })
            else:
                error_msg = result.get("error", result.get("reason", "send failed"))
                conn5.execute(
                    "UPDATE email_campaign_sends SET status = 'skipped', "
                    "error = ? WHERE id = ?",
                    (str(error_msg)[:200], row["id"]),
                )
                skip_count += 1
                vendors_processed.append({
                    "name": row["name"], "email": email,
                    "category": row["category"] or "",
                    "city": row["city"] if "city" in row.keys() else "",
                    "status": "skipped", "error": str(error_msg)[:100],
                })

            conn5.commit()
            conn5.close()
        except Exception as e:
            log.error("Send failed for vendor %d: %s", row["vendor_id"], e)
            conn6 = _conn(db_path)
            conn6.execute(
                "UPDATE email_campaign_sends SET status = 'skipped', "
                "error = ? WHERE id = ?",
                (str(e)[:200], row["id"]),
            )
            conn6.commit()
            conn6.close()
            skip_count += 1
            vendors_processed.append({
                "name": row["name"], "email": email,
                "category": row["category"] or "",
                "city": row["city"] if "city" in row.keys() else "",
                "status": "error", "error": str(e)[:100],
            })

        # Random delay between sends
        delay = random.uniform(SEND_DELAY_MIN, SEND_DELAY_MAX)
        await asyncio.sleep(delay)

    # Check campaign health (bounce rate)
    await check_campaign_health(campaign_id, db_path)

    return {"ok": True, "sent": sent_count, "skipped": skip_count,
            "vendors_processed": vendors_processed}


async def check_campaign_health(campaign_id, db_path=None):
    # type: (int, Optional[str]) -> Dict
    """Check bounce rate and auto-pause if too high."""
    conn = _conn(db_path)
    stats = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM email_campaign_sends "
        "WHERE campaign_id = ? GROUP BY status",
        (campaign_id,),
    ).fetchall()
    conn.close()

    counts = {r["status"]: r["cnt"] for r in stats}
    sent = counts.get("sent", 0)
    bounced = counts.get("bounced", 0)

    if sent > 10 and bounced / sent > BOUNCE_PAUSE_THRESHOLD:
        pause_campaign(campaign_id, db_path)
        log.warning("Campaign %d auto-paused: %.1f%% bounce rate",
                     campaign_id, bounced / sent * 100)

        # Telegram alert
        try:
            msg = (
                "CAMPAIGN PAUSED\n"
                "Campaign %d auto-paused due to high bounce rate.\n"
                "Sent: %d | Bounced: %d (%.1f%%)\n"
                "Action: Check email list quality."
            ) % (campaign_id, sent, bounced, bounced / sent * 100)
            from scripts.notify_telegram import send_message
            await send_message(msg)
        except Exception:
            pass

        return {"healthy": False, "reason": "High bounce rate", "bounce_pct": bounced / sent * 100}

    return {"healthy": True, "sent": sent, "bounced": bounced}


async def process_active_campaigns(db_path=None):
    # type: (Optional[str],) -> int
    """Process all active campaigns. Called by coordinator."""
    conn = _conn(db_path)
    campaigns = conn.execute(
        "SELECT id FROM email_campaigns WHERE status = 'active'"
    ).fetchall()
    conn.close()

    total_sent = 0
    for row in campaigns:
        try:
            result = await process_campaign_batch(row["id"], db_path)
            total_sent += result.get("sent", 0)
        except Exception as e:
            log.error("Campaign %d batch error: %s", row["id"], e)

    return total_sent
