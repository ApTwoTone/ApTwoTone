#!/usr/bin/env python3
"""
Nexus Morning Briefing — Daily business metrics sent to Telegram at 7:30am.

Queries real data from the Nexus database and sends a comprehensive
morning briefing to Kai via Telegram.

Usage:
    python3 scripts/morning_briefing.py          # Send briefing
    python3 scripts/morning_briefing.py --check  # Preview without sending
"""
import json
import sqlite3
import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).parent.parent))

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"


def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def query_db(sql, params=()):
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute(sql, params)
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        return []


def query_one(sql, params=()):
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()
        c.execute(sql, params)
        row = c.fetchone()
        conn.close()
        return row
    except:
        return None


def get_leads_section():
    """Section 1: Leads overview."""
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    cutoff_48h = (datetime.now() - timedelta(hours=48)).isoformat()

    new_leads = query_db(
        "SELECT full_name FROM leads WHERE date_added >= ? AND date_added != ''",
        (yesterday,)
    )
    total = query_one("SELECT COUNT(*) FROM leads")[0]
    hot = query_db(
        "SELECT full_name FROM leads WHERE lead_score >= 7 AND status != 'lost'"
    )
    stale = query_db(
        "SELECT full_name, date_added FROM leads WHERE "
        "(last_contacted_at IS NULL OR last_contacted_at = '' OR last_contacted_at < ?) "
        "AND status NOT IN ('booked', 'lost') AND date_added != ''",
        (cutoff_48h,)
    )

    lines = []
    lines.append("LEADS")
    new_names = [l["full_name"] for l in new_leads if l["full_name"]]
    lines.append(f"New since yesterday: {len(new_leads)}" + (f" ({', '.join(new_names[:3])})" if new_names else ""))
    lines.append(f"Total in pipeline: {total}")

    hot_names = [l["full_name"] for l in hot if l["full_name"]]
    lines.append(f"Hot leads (score 7+): {len(hot)}" + (f" ({', '.join(hot_names[:3])})" if hot_names else ""))

    stale_names = [l["full_name"] for l in stale if l["full_name"]]
    lines.append(f"Need follow-up (48h+): {len(stale)}" + (f" ({', '.join(stale_names[:3])})" if stale_names else ""))

    return "\n".join(lines)


def get_outreach_section():
    """Section 2: Vendor outreach."""
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    week_start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    sent_yesterday = query_one(
        "SELECT COUNT(*) FROM vendor_outreach WHERE created_at >= ?",
        (yesterday,)
    )[0]
    sent_week = query_one(
        "SELECT COUNT(*) FROM vendor_outreach WHERE created_at >= ?",
        (week_start,)
    )[0]

    # Check for replies
    replies = query_db(
        "SELECT v.name FROM vendor_outreach vo JOIN vendors v ON vo.vendor_id = v.id "
        "WHERE vo.status = 'replied' AND vo.created_at >= ?",
        (week_start,)
    )
    queued = query_one(
        "SELECT COUNT(*) FROM vendors WHERE outreach_status = 'new' AND email != '' AND email IS NOT NULL"
    )[0]

    lines = []
    lines.append("VENDOR OUTREACH")
    lines.append(f"Emails sent yesterday: {sent_yesterday}")
    lines.append(f"Emails sent this week: {sent_week}")
    reply_names = [r["name"] for r in replies]
    lines.append(f"Replies received: {len(replies)}" + (f" ({', '.join(reply_names[:3])})" if reply_names else ""))
    lines.append(f"Vendors queued for outreach: {queued}")
    lines.append("Next scheduled send: 8:00 AM")

    return "\n".join(lines)


def get_ads_section():
    """Section 3: Facebook ads performance."""
    try:
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        week_start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        spend_row = query_one(
            "SELECT SUM(spend) FROM ad_performance WHERE date >= ?",
            (yesterday,)
        )
        spend_yesterday = spend_row[0] if spend_row and spend_row[0] else 0

        leads_row = query_one(
            "SELECT SUM(leads) FROM ad_performance WHERE date >= ?",
            (yesterday,)
        )
        leads_yesterday = leads_row[0] if leads_row and leads_row[0] else 0

        cpl_yesterday = spend_yesterday / leads_yesterday if leads_yesterday > 0 else 0

        week_spend = query_one(
            "SELECT SUM(spend) FROM ad_performance WHERE date >= ?",
            (week_start,)
        )
        week_leads = query_one(
            "SELECT SUM(leads) FROM ad_performance WHERE date >= ?",
            (week_start,)
        )
        w_spend = week_spend[0] if week_spend and week_spend[0] else 0
        w_leads = week_leads[0] if week_leads and week_leads[0] else 0
        avg_cpl = w_spend / w_leads if w_leads > 0 else 0

        budget_remaining = 100 - w_spend  # $100/week budget

        lines = []
        lines.append("FACEBOOK ADS")
        lines.append(f"Spend yesterday: ${spend_yesterday:.2f}")
        lines.append(f"Leads yesterday: {int(leads_yesterday)}")
        lines.append(f"CPL yesterday: ${cpl_yesterday:.2f}")
        lines.append(f"7-day avg CPL: ${avg_cpl:.2f}")
        lines.append(f"Budget remaining this week: ${budget_remaining:.2f}")
        return "\n".join(lines)
    except:
        return "FACEBOOK ADS\nAd data not available yet. Check Ad Performance page."


def get_agents_section():
    """Section 4: Agent status."""
    try:
        agents = query_db("SELECT name, status FROM agent_status")
        running = [a for a in agents if a["status"] in ("running", "active")]
        stopped = [a for a in agents if a["status"] in ("stopped", "error", "idle")]

        lines = []
        lines.append("AGENT STATUS")
        lines.append(f"Running: {len(running)} of {len(agents)}")
        if stopped:
            stop_names = [a["name"] for a in stopped[:5]]
            lines.append(f"Stopped: {', '.join(stop_names)}")
        else:
            lines.append("All agents operational")
        return "\n".join(lines)
    except:
        return "AGENT STATUS\nAgent data not available."


def get_recommendation(briefing_data):
    """Generate recommendation using ZAI or fallback."""
    cfg = load_config()
    zai_key = ""

    # Try ZAI keys
    key_pools = cfg.get("key_pools", {})
    zai_keys = key_pools.get("zai", [])
    if zai_keys:
        zai_key = zai_keys[0] if isinstance(zai_keys[0], str) else zai_keys[0].get("key", "")

    if not zai_key:
        zai_key = cfg.get("zai_api_key", "")

    if not zai_key:
        return "Focus on following up with any leads that have been waiting more than 48 hours for a response."

    prompt = (
        "You are a business advisor for Zoar Bathroom Rentals, a luxury restroom trailer "
        "rental company in the San Fernando Valley. Based on the following daily data, generate "
        "one specific recommended action Kai should take today to get closer to a booking. "
        "Be concrete and specific. Under 50 words. Start with an action verb.\n\n"
        f"{briefing_data}"
    )

    try:
        payload = json.dumps({
            "model": "glm-4-flash",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 100,
        }).encode()

        req = Request(
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {zai_key}",
            }
        )
        with urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return "Focus on following up with any leads that have been waiting more than 48 hours for a response."


def send_telegram(text, check_only=False):
    """Send briefing to Telegram."""
    if check_only:
        print(text)
        return

    cfg = load_config()
    token = cfg.get("telegram_token", "")
    chat_ids = cfg.get("telegram_chat_ids", [])

    if not token or not chat_ids:
        print("ERROR: Telegram not configured in ~/.nexus/config.json")
        return

    for chat_id in chat_ids:
        try:
            payload = json.dumps({
                "chat_id": chat_id,
                "text": text,
            }).encode()
            req = Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=payload,
                headers={"Content-Type": "application/json"}
            )
            with urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                if result.get("ok"):
                    print(f"Briefing sent to Telegram chat {chat_id}")
                else:
                    print(f"Telegram error: {result}")
        except Exception as e:
            print(f"Failed to send to {chat_id}: {e}")


def build_briefing():
    """Build the complete morning briefing."""
    now = datetime.now()
    day_name = now.strftime("%A")
    full_date = now.strftime("%B %d, %Y")
    time_str = now.strftime("%I:%M %p")

    header = f"Good morning Kai -- {day_name}, {full_date}\nNexus Daily Briefing -- {time_str}\n"

    leads = get_leads_section()
    outreach = get_outreach_section()
    ads = get_ads_section()
    agents = get_agents_section()

    # Build data for recommendation
    data_summary = f"{leads}\n{outreach}\n{ads}\n{agents}"
    recommendation = get_recommendation(data_summary)

    briefing = (
        f"{header}\n"
        f"{leads}\n\n"
        f"{outreach}\n\n"
        f"{ads}\n\n"
        f"{agents}\n\n"
        f"TODAY'S FOCUS\n"
        f"{recommendation}"
    )

    # Truncate if over Telegram limit
    if len(briefing) > 4000:
        briefing = briefing[:3990] + "..."

    return briefing


def main():
    parser = argparse.ArgumentParser(description="Nexus Morning Briefing")
    parser.add_argument("--check", action="store_true", help="Preview without sending")
    args = parser.parse_args()

    briefing = build_briefing()

    if args.check:
        print("=" * 60)
        print("MORNING BRIEFING PREVIEW")
        print("=" * 60)
        print(briefing)
        print("=" * 60)
        print(f"Length: {len(briefing)} chars")
    else:
        send_telegram(briefing)


if __name__ == "__main__":
    main()
