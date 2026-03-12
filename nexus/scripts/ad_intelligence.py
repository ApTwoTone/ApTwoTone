#!/usr/bin/env python3
"""
Ad Intelligence — AI-powered Facebook Ads analysis for Zoar Bathroom Rentals.

Pulls metrics from the ad_metrics table (populated by core/ad_monitor.py),
computes CPL/CTR trends per campaign and day, sends data to Groq for strategic
analysis, stores results in ad_analysis table, and sends a Telegram brief.

Usage:
    python scripts/ad_intelligence.py                # Full run: analyze + store + Telegram
    python scripts/ad_intelligence.py --dry-run      # Analyze only, no store or Telegram
    python scripts/ad_intelligence.py --detail        # Print full AI analysis text
    python scripts/ad_intelligence.py --days 7        # Analysis window (default 14)
    python scripts/ad_intelligence.py --json           # JSON output
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ad_intelligence")

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"

# Budget context for the AI analyst
WEEKLY_BUDGET = 100.0
DAILY_BUDGET = WEEKLY_BUDGET / 7
AD_ACCOUNT_ID = "act_1713830169593455"


# ── Database ────────────────────────────────────────────────────────────────

def init_ad_analysis_db():
    """Create the ad_analysis table if it doesn't exist."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS ad_analysis (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        analysis_type TEXT NOT NULL,
        campaigns_analyzed INTEGER DEFAULT 0,
        total_spend REAL DEFAULT 0,
        total_leads INTEGER DEFAULT 0,
        avg_cpl REAL DEFAULT 0,
        trend_direction TEXT DEFAULT '',
        recommendations TEXT DEFAULT '[]',
        raw_metrics TEXT DEFAULT '{}',
        ai_model TEXT DEFAULT '',
        ai_response TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_ad_analysis_date ON ad_analysis(date);
    """)
    conn.commit()
    conn.close()


# ── Metrics Pull ────────────────────────────────────────────────────────────

def pull_metrics(days: int = 14) -> List[Dict[str, Any]]:
    """Read ad_metrics rows for the last N days."""
    if not DB_PATH.exists():
        log.warning("Database not found at %s", DB_PATH)
        return []

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")

    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT * FROM ad_metrics WHERE date >= ? ORDER BY date DESC",
        (cutoff,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Trend Computation ───────────────────────────────────────────────────────

def compute_trends(metrics: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute CPL, spend, and lead trends per campaign and per day."""
    if not metrics:
        return {"error": "No metrics available", "campaigns": {}, "daily": {}}

    # Per-campaign aggregation
    campaigns = defaultdict(lambda: {
        "spend": 0.0, "leads": 0, "impressions": 0, "clicks": 0,
        "days_active": set(), "name": "",
    })
    # Per-day aggregation
    daily = defaultdict(lambda: {"spend": 0.0, "leads": 0, "impressions": 0})

    for row in metrics:
        cid = row.get("campaign_id", "unknown")
        cname = row.get("campaign_name", "")
        date = row.get("date", "")
        spend = float(row.get("spend", 0) or 0)
        leads = int(row.get("leads", 0) or 0)
        impressions = int(row.get("impressions", 0) or 0)
        clicks = int(row.get("clicks", 0) or 0)

        c = campaigns[cid]
        c["spend"] += spend
        c["leads"] += leads
        c["impressions"] += impressions
        c["clicks"] += clicks
        c["days_active"].add(date)
        if cname:
            c["name"] = cname

        d = daily[date]
        d["spend"] += spend
        d["leads"] += leads
        d["impressions"] += impressions

    # Compute CPL and CTR per campaign
    campaign_summary = {}
    for cid, c in campaigns.items():
        cpl = round(c["spend"] / c["leads"], 2) if c["leads"] > 0 else None
        ctr = round((c["clicks"] / c["impressions"]) * 100, 2) if c["impressions"] > 0 else 0.0
        campaign_summary[cid] = {
            "name": c["name"],
            "spend": round(c["spend"], 2),
            "leads": c["leads"],
            "impressions": c["impressions"],
            "clicks": c["clicks"],
            "cpl": cpl,
            "ctr": ctr,
            "days_active": len(c["days_active"]),
        }

    # Daily CPL trend (sorted chronologically)
    daily_summary = {}
    for date in sorted(daily.keys()):
        d = daily[date]
        cpl = round(d["spend"] / d["leads"], 2) if d["leads"] > 0 else None
        daily_summary[date] = {
            "spend": round(d["spend"], 2),
            "leads": d["leads"],
            "impressions": d["impressions"],
            "cpl": cpl,
        }

    # Overall trend direction
    total_spend = sum(c["spend"] for c in campaign_summary.values())
    total_leads = sum(c["leads"] for c in campaign_summary.values())
    avg_cpl = round(total_spend / total_leads, 2) if total_leads > 0 else None

    # CPL trend: compare first half vs second half
    dates = sorted(daily_summary.keys())
    mid = len(dates) // 2
    first_half_leads = sum(daily_summary[d]["leads"] for d in dates[:mid])
    second_half_leads = sum(daily_summary[d]["leads"] for d in dates[mid:])
    first_half_spend = sum(daily_summary[d]["spend"] for d in dates[:mid])
    second_half_spend = sum(daily_summary[d]["spend"] for d in dates[mid:])

    if first_half_leads > 0 and second_half_leads > 0:
        first_cpl = first_half_spend / first_half_leads
        second_cpl = second_half_spend / second_half_leads
        if second_cpl > first_cpl * 1.2:
            trend = "declining"
        elif second_cpl < first_cpl * 0.8:
            trend = "improving"
        else:
            trend = "stable"
    elif second_half_leads == 0 and second_half_spend > 0:
        trend = "declining"
    elif first_half_leads == 0 and second_half_leads > 0:
        trend = "improving"
    else:
        trend = "insufficient_data"

    return {
        "campaigns": campaign_summary,
        "daily": daily_summary,
        "total_spend": round(total_spend, 2),
        "total_leads": total_leads,
        "avg_cpl": avg_cpl,
        "trend_direction": trend,
        "days_analyzed": len(dates),
    }


# ── AI Analysis via Groq ───────────────────────────────────────────────────

async def ai_analyze(trends: Dict[str, Any]) -> Dict[str, Any]:
    """Send trends to Groq for strategic analysis and recommendations."""
    # Add project root to path for core imports
    project_root = str(Path(__file__).resolve().parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    try:
        from core.worker_pool import call_provider
    except ImportError:
        log.error("Cannot import core.worker_pool — falling back to basic analysis")
        return _fallback_analysis(trends)

    system_prompt = (
        "You are a Facebook Ads performance analyst for Zoar Bathroom Rentals, "
        "a luxury restroom trailer rental company in San Fernando Valley, CA. "
        "Target market: weddings, quinceañeras, corporate events, backyard parties. "
        "Budget: $100/week ($14.29/day). Ad account: act_1713830169593455. "
        "The business has 1 trailer and needs at least 1 booking every 5 days. "
        "All ads must use approved language: 'Starting at $999', 'Delivery and setup included'. "
        "Never recommend 'All-inclusive' or 'No hidden fees'."
    )

    metrics_text = json.dumps(trends, indent=2, default=str)
    user_prompt = (
        "Here are the Facebook Ads performance metrics for the past %d days:\n\n"
        "%s\n\n"
        "Provide exactly 3-5 specific, actionable recommendations. "
        "For each recommendation, use this exact format:\n"
        "ACTION: [PAUSE|SCALE|TEST|ADJUST]\n"
        "TARGET: [specific campaign name, ad, or audience]\n"
        "RATIONALE: [why, based on the data]\n"
        "EXPECTED IMPACT: [what improvement to expect]\n\n"
        "Also provide:\n"
        "- OVERALL ASSESSMENT: 1 sentence summary of ad health\n"
        "- BIGGEST RISK: the #1 thing that could waste budget this week\n"
        "- QUICK WIN: the single easiest change to improve results"
    ) % (trends.get("days_analyzed", 14), metrics_text)

    result = await call_provider(
        "groq",
        messages=[{"role": "user", "content": user_prompt}],
        system=system_prompt,
        max_tokens=2048,
        temperature=0.3,
        agent_id="ad-intelligence",
        task_type="structured_data",
    )

    if not result.get("ok"):
        log.warning("Groq failed: %s — trying Gemini fallback", result.get("content", ""))
        result = await call_provider(
            "gemini",
            messages=[{"role": "user", "content": user_prompt}],
            system=system_prompt,
            max_tokens=2048,
            temperature=0.3,
            agent_id="ad-intelligence",
            task_type="structured_data",
        )

    if not result.get("ok"):
        log.warning("All AI providers failed — using fallback analysis")
        return _fallback_analysis(trends)

    return {
        "ai_response": result["content"],
        "ai_model": "%s/%s" % (result.get("provider", ""), result.get("model", "")),
        "latency_ms": result.get("latency_ms", 0),
    }


def _fallback_analysis(trends: Dict[str, Any]) -> Dict[str, Any]:
    """Rule-based fallback when AI providers are unavailable."""
    recs = []
    for cid, c in trends.get("campaigns", {}).items():
        if c["spend"] > 5 and c["leads"] == 0:
            recs.append("PAUSE '%s' — $%.2f spent with 0 leads" % (c["name"], c["spend"]))
        elif c["cpl"] and c["cpl"] < 6:
            recs.append("SCALE '%s' — good CPL at $%.2f" % (c["name"], c["cpl"]))
        elif c["cpl"] and c["cpl"] > 10:
            recs.append("REVIEW '%s' — CPL $%.2f is above $8 threshold" % (c["name"], c["cpl"]))

    if not recs:
        recs.append("No clear action — insufficient data. Ensure ad_monitor is running.")

    return {
        "ai_response": "Fallback analysis (AI unavailable):\n" + "\n".join("- " + r for r in recs),
        "ai_model": "rule-based/fallback",
        "latency_ms": 0,
    }


# ── Storage ─────────────────────────────────────────────────────────────────

def store_analysis(trends: Dict[str, Any], ai_result: Dict[str, Any]):
    """Persist analysis to the ad_analysis table."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """INSERT INTO ad_analysis
           (date, analysis_type, campaigns_analyzed, total_spend, total_leads,
            avg_cpl, trend_direction, recommendations, raw_metrics, ai_model, ai_response)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            datetime.utcnow().strftime("%Y-%m-%d"),
            "daily",
            len(trends.get("campaigns", {})),
            trends.get("total_spend", 0),
            trends.get("total_leads", 0),
            trends.get("avg_cpl", 0) or 0,
            trends.get("trend_direction", ""),
            json.dumps(ai_result.get("ai_response", ""), default=str),
            json.dumps(trends, default=str),
            ai_result.get("ai_model", ""),
            ai_result.get("ai_response", ""),
        ),
    )
    conn.commit()
    conn.close()
    log.info("Analysis stored in ad_analysis table")


# ── Telegram Report ─────────────────────────────────────────────────────────

def format_telegram_report(trends: Dict[str, Any], ai_result: Dict[str, Any]) -> str:
    """Format a concise Telegram intelligence brief."""
    today = datetime.utcnow().strftime("%b %d")
    daily = trends.get("daily", {})
    dates = sorted(daily.keys())

    # Today's metrics (latest date)
    today_data = daily.get(dates[-1], {}) if dates else {}
    today_spend = today_data.get("spend", 0)
    today_leads = today_data.get("leads", 0)

    # Week metrics (last 7 days)
    week_dates = dates[-7:] if len(dates) >= 7 else dates
    week_spend = sum(daily[d]["spend"] for d in week_dates)
    week_leads = sum(daily[d]["leads"] for d in week_dates)

    # CPL display
    avg_cpl = trends.get("avg_cpl")
    cpl_str = "$%.2f" % avg_cpl if avg_cpl else "N/A (0 leads)"
    trend = trends.get("trend_direction", "unknown")
    trend_emoji = {"improving": "DOWN", "declining": "UP", "stable": "FLAT"}.get(trend, "??")

    # Extract recommendations: pair ACTION lines with their TARGET lines
    ai_text = ai_result.get("ai_response", "")
    lines = ai_text.split("\n")
    action_lines = []
    i = 0
    while i < len(lines) and len(action_lines) < 3:
        stripped = lines[i].strip()
        if stripped.startswith("ACTION:"):
            action = stripped.replace("ACTION:", "").strip()
            # Look for TARGET on the next line
            target = ""
            if i + 1 < len(lines) and lines[i + 1].strip().startswith("TARGET:"):
                target = lines[i + 1].strip().replace("TARGET:", "").strip()
            if target:
                action_lines.append("%s %s" % (action, target))
            else:
                action_lines.append(action)
        i += 1

    # If no structured actions found, grab first 3 non-empty substantive lines
    if not action_lines:
        for line in lines:
            stripped = line.strip()
            if stripped and len(stripped) > 15 and not stripped.startswith("{") and not stripped.startswith("**"):
                action_lines.append(stripped[:120])
            if len(action_lines) >= 3:
                break

    report = (
        "AD INTELLIGENCE — %s\n\n"
        "Spend: $%.2f today / $%.2f this week\n"
        "Leads: %d today / %d this week\n"
        "CPL: %s (trend: %s)\n"
        "Campaigns: %d analyzed\n\n"
        "TOP RECOMMENDATIONS:\n%s\n\n"
        "Model: %s"
    ) % (
        today,
        today_spend, week_spend,
        today_leads, week_leads,
        cpl_str, trend_emoji,
        len(trends.get("campaigns", {})),
        "\n".join("  %d. %s" % (i + 1, a) for i, a in enumerate(action_lines)) or "  (none — check ad_monitor data)",
        ai_result.get("ai_model", "unknown"),
    )
    return report


def send_telegram(message: str):
    """Send a plain-text Telegram message."""
    if not CONFIG_PATH.exists():
        log.error("Config not found at %s", CONFIG_PATH)
        return
    cfg = json.loads(CONFIG_PATH.read_text())
    token = cfg.get("telegram_token", "")
    chat_ids = cfg.get("telegram_chat_ids", [])

    if not token or not chat_ids:
        log.error("telegram_token or telegram_chat_ids not configured")
        return

    for cid in chat_ids:
        try:
            url = "https://api.telegram.org/bot%s/sendMessage" % token
            payload = json.dumps({"chat_id": str(cid), "text": message}).encode()
            req = Request(url, data=payload, headers={"Content-Type": "application/json"})
            urlopen(req, timeout=10)
            log.info("Telegram sent to chat %s", cid)
        except Exception as e:
            log.error("Telegram send failed for %s: %s", cid, e)


# ── Main ────────────────────────────────────────────────────────────────────

async def run_analysis(days: int = 14, dry_run: bool = False) -> Dict[str, Any]:
    """Run the full ad intelligence pipeline."""
    init_ad_analysis_db()

    # Step 1: Pull metrics
    metrics = pull_metrics(days)
    if not metrics:
        msg = "No ad metrics found for the last %d days. Is ad_monitor running?" % days
        log.warning(msg)
        return {"status": "error", "message": msg}

    log.info("Pulled %d metric rows for %d-day window", len(metrics), days)

    # Step 2: Compute trends
    trends = compute_trends(metrics)
    log.info(
        "Trends: $%.2f spend, %d leads, CPL %s, direction: %s",
        trends["total_spend"], trends["total_leads"],
        trends.get("avg_cpl") or "N/A", trends["trend_direction"],
    )

    # Step 3: AI analysis
    ai_result = await ai_analyze(trends)
    log.info("AI analysis complete via %s (%dms)",
             ai_result.get("ai_model", "?"), ai_result.get("latency_ms", 0))

    # Step 4: Store
    if not dry_run:
        store_analysis(trends, ai_result)

    # Step 5: Telegram report
    report = format_telegram_report(trends, ai_result)
    if not dry_run:
        send_telegram(report)
    else:
        log.info("Dry run — skipping Telegram and DB store")

    return {
        "status": "ok",
        "trends": trends,
        "ai_result": ai_result,
        "report": report,
    }


def main():
    parser = argparse.ArgumentParser(description="Ad Intelligence — AI-powered ad analysis")
    parser.add_argument("--days", type=int, default=14, help="Analysis window in days (default: 14)")
    parser.add_argument("--dry-run", action="store_true", help="Analyze without storing or sending Telegram")
    parser.add_argument("--detail", action="store_true", help="Print full AI analysis text")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    result = asyncio.run(run_analysis(days=args.days, dry_run=args.dry_run))

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    elif args.detail:
        ai_text = result.get("ai_result", {}).get("ai_response", "")
        print("\n  Ad Intelligence — Full Analysis")
        print("  " + "=" * 50)
        print(ai_text)
        print("  " + "=" * 50)
    else:
        report = result.get("report", "")
        if report:
            print("\n" + report)
        else:
            print(result.get("message", "No results"))


if __name__ == "__main__":
    main()
