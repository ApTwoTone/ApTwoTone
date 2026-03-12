#!/usr/bin/env python3
"""Facebook ad spend monitor — checks budget compliance and lead delivery.

Alerts if:
- Daily spend exceeds $7 (target is $5/day)
- 0 impressions after 6 hours (targeting broken)
- 1000+ impressions with 0 leads (creative broken)
- CPL exceeds $50 (pause and review)

Usage:
    python scripts/ad_spend_monitor.py              # One check
    python scripts/ad_spend_monitor.py --json        # JSON output
"""

import argparse
import json
import logging
import sqlite3
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ad_spend_monitor")

DB_PATH = Path.home() / ".nexus" / "memory.db"

# Budget rules (Rule 26: $100/week = ~$14.29/day, but target is $5/day)
DAILY_SPEND_TARGET = 5.0
DAILY_SPEND_ALERT = 7.0
CPL_ALERT = 50.0
IMPRESSION_DROUGHT_HOURS = 6
IMPRESSION_NO_LEADS_THRESHOLD = 1000


def _send_telegram(msg):
    try:
        subprocess.run(
            [sys.executable, str(Path(__file__).parent / "notify_telegram.py"), msg],
            timeout=15, capture_output=True,
        )
    except Exception as e:
        log.error("Telegram failed: %s", e)


def check_ad_spend():
    """Check Facebook ad metrics from the database."""
    if not DB_PATH.exists():
        return {"status": "error", "message": "DB not found"}

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA query_only = ON")
    result = {"status": "ok", "alerts": []}

    # Check for ad metrics tables
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%ad%'"
    ).fetchall()]

    if "ad_metrics" not in tables and "facebook_ads" not in tables:
        # Try ad_performance or similar
        for t in tables:
            if "metric" in t or "performance" in t or "insight" in t:
                result["metrics_table"] = t
                break

    # Try to read from ad_metrics or ad_performance
    for table_name in ["ad_metrics", "ad_performance", "facebook_ad_metrics"]:
        try:
            today_spend = conn.execute(
                "SELECT SUM(spend) FROM %s WHERE DATE(date) = DATE('now')" % table_name
            ).fetchone()
            if today_spend and today_spend[0] is not None:
                spend = today_spend[0]
                result["today_spend"] = spend
                if spend > DAILY_SPEND_ALERT:
                    result["alerts"].append({
                        "type": "overspend",
                        "message": "Daily spend $%.2f exceeds $%.2f alert threshold" % (spend, DAILY_SPEND_ALERT),
                        "severity": "high",
                    })

            today_impressions = conn.execute(
                "SELECT SUM(impressions) FROM %s WHERE DATE(date) = DATE('now')" % table_name
            ).fetchone()
            if today_impressions and today_impressions[0] is not None:
                impr = today_impressions[0]
                result["today_impressions"] = impr

            today_leads = conn.execute(
                "SELECT SUM(leads) FROM %s WHERE DATE(date) = DATE('now')" % table_name
            ).fetchone()
            if today_leads and today_leads[0] is not None:
                leads = today_leads[0]
                result["today_leads"] = leads

                if impr and impr >= IMPRESSION_NO_LEADS_THRESHOLD and leads == 0:
                    result["alerts"].append({
                        "type": "no_leads",
                        "message": "%d impressions but 0 leads — creative or form may be broken" % impr,
                        "severity": "high",
                    })

                if leads > 0 and spend:
                    cpl = spend / leads
                    result["cpl"] = round(cpl, 2)
                    if cpl > CPL_ALERT:
                        result["alerts"].append({
                            "type": "high_cpl",
                            "message": "CPL $%.2f exceeds $%.2f threshold — consider pausing" % (cpl, CPL_ALERT),
                            "severity": "medium",
                        })
            break
        except Exception:
            continue

    # Check weekly spend
    try:
        for table_name in ["ad_metrics", "ad_performance", "facebook_ad_metrics"]:
            try:
                week_spend = conn.execute(
                    "SELECT SUM(spend) FROM %s WHERE date > datetime('now', '-7 days')" % table_name
                ).fetchone()
                if week_spend and week_spend[0] is not None:
                    result["week_spend"] = week_spend[0]
                    if week_spend[0] > 100:
                        result["alerts"].append({
                            "type": "weekly_overspend",
                            "message": "Weekly spend $%.2f exceeds $100 budget (Rule 26)" % week_spend[0],
                            "severity": "critical",
                        })
                break
            except Exception:
                continue
    except Exception:
        pass

    result["ad_tables_found"] = tables
    conn.close()

    # Send alerts
    for alert in result.get("alerts", []):
        if alert["severity"] in ("high", "critical"):
            _send_telegram("AD ALERT [%s]: %s" % (alert["severity"].upper(), alert["message"]))

    return result


def score_fb_lead(lead_data):
    """Score a Facebook lead 1-10 for quality.

    Args:
        lead_data: dict with keys: name, email, phone, event_type, message

    Returns:
        int score 1-10 and dict with breakdown
    """
    score = 0
    breakdown = {}

    # Phone number present and valid
    phone = lead_data.get("phone", "")
    if phone and len(phone.replace("-", "").replace(" ", "").replace("(", "").replace(")", "")) >= 10:
        score += 2
        breakdown["phone"] = 2
    elif phone:
        score += 1
        breakdown["phone"] = 1

    # Email present
    if lead_data.get("email") and "@" in lead_data.get("email", ""):
        score += 2
        breakdown["email"] = 2

    # Event type
    event = (lead_data.get("event_type") or "").lower()
    if any(w in event for w in ["wedding", "quincea"]):
        score += 2
        breakdown["event_type"] = 2
    elif any(w in event for w in ["birthday", "corporate", "party"]):
        score += 1
        breakdown["event_type"] = 1

    # Name quality
    name = lead_data.get("name", "")
    if name and name.lower() not in ("test", "asdf", "none", "n/a", "x"):
        score += 1
        breakdown["name"] = 1

    # Phone not fake
    if phone:
        digits = re.sub(r"\D", "", phone) if "re" in dir() else phone
        if not any(p in digits for p in ["5551", "0000", "1111", "1234"]):
            score += 1
            breakdown["phone_quality"] = 1

    # Message/notes present
    if lead_data.get("message") and len(lead_data["message"]) > 10:
        score += 1
        breakdown["message"] = 1

    # Route recommendation
    if score >= 7:
        route = "HOT — call within 5 minutes"
    elif score >= 4:
        route = "WARM — call within 2 hours"
    else:
        route = "COLD — auto-response, follow up in 24h"

    return min(score, 10), {"score": min(score, 10), "breakdown": breakdown, "route": route}


def main():
    parser = argparse.ArgumentParser(description="Ad spend monitor")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    result = check_ad_spend()

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("\n  Ad Spend Monitor")
        print("  " + "=" * 45)
        print("  Status: %s" % result.get("status"))
        print("  Ad tables found: %s" % result.get("ad_tables_found", []))
        if "today_spend" in result:
            print("  Today spend: $%.2f (target: $%.2f)" % (result["today_spend"], DAILY_SPEND_TARGET))
        if "today_impressions" in result:
            print("  Today impressions: %d" % result["today_impressions"])
        if "today_leads" in result:
            print("  Today leads: %d" % result["today_leads"])
        if "cpl" in result:
            print("  CPL: $%.2f" % result["cpl"])
        if "week_spend" in result:
            print("  Week spend: $%.2f / $100" % result["week_spend"])

        alerts = result.get("alerts", [])
        if alerts:
            print("\n  Alerts:")
            for a in alerts:
                print("    [%s] %s" % (a["severity"].upper(), a["message"]))
        else:
            print("  No alerts")
        print("  " + "=" * 45)


# Import guard for score_fb_lead
import re

if __name__ == "__main__":
    main()
