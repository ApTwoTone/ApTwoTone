"""
Lead Follow-Up Scheduler — Automated 24/48h follow-up sequence.

After a lead is quoted and doesn't respond, this daemon sends:
  - +24h: gentle check-in SMS
  - +48h: urgency SMS ("Your date is still available")
  - +72h: flags in Telegram digest only (no 3rd auto-SMS)

Safety rules (enforced):
  - Only runs for leads with requires_manual_approval=0 (new leads from website)
  - All sends go through outbound_gate() in integrations/messaging.py (Rule 0)
  - Existing contacts with requires_manual_approval=1 are NEVER auto-followed-up
  - After 3 attempts with no reply → status = 'follow_up_needed', stop auto-outreach

Usage:
    python -m core.lead_followup_scheduler          # run one pass
    python -m core.lead_followup_scheduler --dry-run # preview, don't send
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("lead_followup_scheduler")
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

DB_PATH = Path.home() / ".nexus" / "memory.db"
NEXUS_DIR = Path.home() / ".nexus"
TELEGRAM_CHAT_ID = "8540603351"

# Hours after last contact before sending each follow-up
FOLLOWUP_HOURS = {1: 24, 2: 48}  # attempt_number → hours_since_last_contact
FOLLOWUP_STOP_AFTER = 3  # Never auto-send more than this many follow-ups


def _db():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hours_since(ts_str: Optional[str]) -> float:
    """Return hours since the given ISO timestamp. 9999 if None/invalid."""
    if not ts_str:
        return 9999.0
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return delta.total_seconds() / 3600.0
    except Exception:
        return 9999.0


def get_leads_needing_followup() -> list:
    """
    Find quoted leads that haven't responded and are due for a follow-up.
    Excludes: requires_manual_approval=1, already lost/booked, blocked.
    """
    conn = _db()
    try:
        rows = conn.execute(
            """
            SELECT l.id, l.full_name, l.phone, l.email, l.event_type, l.event_date,
                   l.event_city, l.guest_count, l.booking_status, l.status,
                   l.requires_manual_approval, l.last_contacted_at, l.total_quote_amount,
                   l.followup_count,
                   COALESCE(l.followup_count, 0) as fc
            FROM leads l
            WHERE l.booking_status IN ('qualifying', 'auto_contacted')
              AND (l.requires_manual_approval = 0 OR l.requires_manual_approval IS NULL)
              AND l.status NOT IN ('booked', 'lost', 'cancelled', 'follow_up_needed')
              AND COALESCE(l.followup_count, 0) < ?
            ORDER BY l.last_contacted_at ASC
            LIMIT 20
            """,
            (FOLLOWUP_STOP_AFTER,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        log.debug("get_leads_needing_followup error: %s", e)
        return []
    finally:
        conn.close()


def _build_followup_message(lead: dict, attempt: int) -> str:
    """Build the appropriate follow-up SMS based on attempt number."""
    name = (lead.get("full_name") or "").split()[0] or "there"
    event_type = lead.get("event_type") or "event"
    event_date = lead.get("event_date") or ""
    amount = lead.get("total_quote_amount")
    price_note = f" (starting at ${int(amount):,})" if amount else ""

    if attempt == 1:
        # 24h — gentle, warm check-in
        msg = (
            f"Hi {name}! Just following up on your {event_type} rental inquiry"
        )
        if event_date:
            msg += f" for {event_date}"
        msg += (
            f". Your date is still available and I wanted to make sure you received "
            f"your quote{price_note}. Happy to answer any questions! "
            f"Reply or call (424) 235-8979. — Zoar Bathroom Rentals"
        )
    else:
        # 48h — urgency, date availability
        msg = (
            f"Hi {name}, one last check-in about your {event_type} inquiry"
        )
        if event_date:
            msg += f" for {event_date}"
        msg += (
            f". Your date is still showing available but we're booking up quickly for "
            f"spring/summer events. A $160 deposit holds your date. "
            f"Happy to help — call or text (424) 235-8979. — Zoar Bathroom Rentals"
        )
    return msg


def _increment_followup_count(lead_id: int) -> int:
    """Increment followup_count and return the new value."""
    conn = _db()
    try:
        # Add column if it doesn't exist yet (safe migration)
        try:
            conn.execute("ALTER TABLE leads ADD COLUMN followup_count INTEGER DEFAULT 0")
            conn.commit()
        except Exception:
            pass  # Column already exists

        conn.execute(
            "UPDATE leads SET followup_count = COALESCE(followup_count, 0) + 1, "
            "last_contacted_at = ?, updated_at = ? WHERE id = ?",
            (_now_iso(), _now_iso(), lead_id),
        )
        conn.commit()
        row = conn.execute("SELECT COALESCE(followup_count, 0) as fc FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return row["fc"] if row else 1
    finally:
        conn.close()


def _mark_followup_needed(lead_id: int) -> None:
    """After max attempts, mark lead for manual review."""
    conn = _db()
    try:
        conn.execute(
            "UPDATE leads SET status = 'follow_up_needed', updated_at = ? WHERE id = ?",
            (_now_iso(), lead_id),
        )
        conn.commit()
    finally:
        conn.close()


async def _send_followup_sms(lead: dict, message: str) -> bool:
    """
    Send follow-up SMS via Google Voice, gated by outbound_gate().
    Returns True on success.
    """
    phone = lead.get("phone", "")
    if not phone:
        log.info("Lead %s has no phone — skipping SMS", lead.get("id"))
        return False

    try:
        from integrations.messaging import outbound_gate
        gate_result = outbound_gate("sms", phone, lead_id=lead.get("id"))
        if not gate_result.get("allowed"):
            reason = gate_result.get("reason", "blocked")
            log.info("Lead %s blocked by outbound_gate: %s", lead.get("id"), reason)
            return False
    except Exception as e:
        log.warning("outbound_gate check failed for lead %s: %s — skipping", lead.get("id"), e)
        return False

    try:
        from integrations.google_voice import GoogleVoiceMessenger
        messenger = GoogleVoiceMessenger()
        result = await messenger.send_sms(phone, message)
        return bool(result)
    except Exception as e:
        log.warning("SMS send failed for lead %s: %s", lead.get("id"), e)
        return False


def _send_telegram_digest(leads_at_72h: list) -> None:
    """
    Send Telegram digest for leads at 72h+ with no response.
    These need Kai's manual decision — no auto-SMS.
    """
    if not leads_at_72h:
        return

    cfg_path = NEXUS_DIR / "config.json"
    try:
        cfg = json.loads(cfg_path.read_text())
        token = cfg.get("telegram_bot_token", "") or cfg.get("telegram_token", "")
    except Exception:
        return

    if not token:
        return

    lines = [f"{len(leads_at_72h)} lead(s) unresponsive 72+ hours after quote — manual action needed:"]
    for lead in leads_at_72h:
        name = lead.get("full_name", "Unknown")
        city = lead.get("event_city", "")
        date = lead.get("event_date", "")
        amount = lead.get("total_quote_amount")
        line = f"  - {name}"
        if city:
            line += f", {city}"
        if date:
            line += f", {date}"
        if amount:
            line += f" (${int(amount):,})"
        lines.append(line)
    lines.append("Use /pipeline to take action.")

    message = "\n".join(lines)
    import urllib.request, urllib.parse
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": message}).encode()
    try:
        urllib.request.urlopen(url, data=data, timeout=10)
    except Exception as e:
        log.warning("Telegram digest failed: %s", e)


async def run_pass(dry_run: bool = False) -> dict:
    """
    Main scheduler pass. Finds leads needing follow-up and sends them.
    Returns a summary dict.
    """
    leads = get_leads_needing_followup()
    sent = 0
    skipped = 0
    flagged_72h = []

    for lead in leads:
        lead_id = lead["id"]
        fc = lead.get("fc", 0) or 0
        hours = _hours_since(lead.get("last_contacted_at"))

        # Determine which follow-up attempt is due
        if fc == 0 and hours >= FOLLOWUP_HOURS[1]:
            attempt = 1
        elif fc == 1 and hours >= FOLLOWUP_HOURS[2]:
            attempt = 2
        elif fc >= 2:
            # 72h+ — flag for manual Telegram review, don't auto-SMS
            if hours >= 72:
                flagged_72h.append(lead)
            skipped += 1
            continue
        else:
            skipped += 1
            continue

        message = _build_followup_message(lead, attempt)
        name = lead.get("full_name", f"Lead #{lead_id}")
        log.info("Follow-up #%d for %s (attempt %d, %.1fh since last contact)",
                 lead_id, name, attempt, hours)

        if dry_run:
            log.info("  [DRY RUN] Would send: %s", message[:80])
            sent += 1
        else:
            success = await _send_followup_sms(lead, message)
            if success:
                new_count = _increment_followup_count(lead_id)
                if new_count >= FOLLOWUP_STOP_AFTER:
                    _mark_followup_needed(lead_id)
                    log.info("Lead %s marked follow_up_needed after %d attempts", lead_id, new_count)
                sent += 1
            else:
                skipped += 1

    # Send 72h digest to Kai
    if flagged_72h and not dry_run:
        _send_telegram_digest(flagged_72h)

    log.info("Follow-up pass: %d sent, %d skipped, %d flagged for manual review",
             sent, skipped, len(flagged_72h))
    return {"sent": sent, "skipped": skipped, "needs_manual": len(flagged_72h)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lead follow-up scheduler")
    parser.add_argument("--dry-run", action="store_true", help="Preview without sending")
    args = parser.parse_args()
    result = asyncio.run(run_pass(dry_run=args.dry_run))
    print(json.dumps(result, indent=2))
