"""
Facebook Ads Performance Monitoring — Phase 15 of Nexus Master Prompt

Monitors Facebook Ads via the Marketing API and sends automated alerts
and reports via Telegram:
  - Fetches ad insights (campaign, adset, ad level)
  - Stores metrics in SQLite for historical trend analysis
  - Alert system: CPL spikes, no-lead droughts, overspend, reach drops
  - Daily report at 8 AM PT (part of morning package)
  - Weekly deep-dive at 10 AM PT on Sundays
  - Natural language ad query handler for Telegram

Config: ~/.nexus/config.json  (fb_page_access_token used as system user token)
Database: ~/.nexus/memory.db   (ad_metrics + ad_alerts tables)
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

# ── Constants ────────────────────────────────────────────────────────────────

DB_PATH = Path.home() / ".nexus" / "memory.db"
AD_ACCOUNT_ID = "act_1713830169593455"
GRAPH_API = "https://graph.facebook.com/v25.0"
PT = ZoneInfo("America/Los_Angeles")

# Alert thresholds
CPL_ALERT_THRESHOLD = 8.0       # Alert if CPL > $8
NO_LEAD_HOURS = 24              # Alert if no leads in 24h
OVERSPEND_THRESHOLD = 1.2       # Alert if spend > 120% of daily budget
REACH_DROP_THRESHOLD = 0.4      # Alert if reach drops 40%+ vs 7-day avg
DAILY_BUDGET = 10.0             # $10/day budget baseline

# Scheduler state (reset at midnight PT)
_sent_today: set[str] = set()
_last_date: str = ""
_last_hourly_store: str = ""

# Lead action types to look for in the actions array
LEAD_ACTION_TYPES = {
    "lead",
    "onsite_conversion.lead_grouped",
    "offsite_conversion.fb_pixel_lead",
}


# ── Database Init ────────────────────────────────────────────────────────────

def init_ad_monitor_db():
    """Create ad_metrics and ad_alerts tables if they don't exist."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS ad_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        campaign_id TEXT DEFAULT '',
        campaign_name TEXT DEFAULT '',
        adset_id TEXT DEFAULT '',
        adset_name TEXT DEFAULT '',
        ad_id TEXT DEFAULT '',
        ad_name TEXT DEFAULT '',
        spend REAL DEFAULT 0,
        impressions INTEGER DEFAULT 0,
        reach INTEGER DEFAULT 0,
        clicks INTEGER DEFAULT 0,
        ctr REAL DEFAULT 0,
        cpc REAL DEFAULT 0,
        leads INTEGER DEFAULT 0,
        cpl REAL DEFAULT 0,
        actions TEXT DEFAULT '{}',
        collected_at TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_ad_metrics_date ON ad_metrics(date);
    CREATE INDEX IF NOT EXISTS idx_ad_metrics_campaign ON ad_metrics(campaign_id);

    CREATE TABLE IF NOT EXISTS ad_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        alert_type TEXT NOT NULL,
        message TEXT NOT NULL,
        data TEXT DEFAULT '{}',
        acknowledged INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    );
    """)
    conn.commit()
    conn.close()
    print("[AdMonitor] Database tables ready")


# ── Config / Auth ────────────────────────────────────────────────────────────

def _load_config() -> dict:
    config_path = Path.home() / ".nexus" / "config.json"
    if config_path.exists():
        try:
            return json.loads(config_path.read_text())
        except Exception:
            pass
    return {}


def _token() -> str:
    return _load_config().get("fb_page_access_token", "")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _now_pt() -> datetime:
    return datetime.now(PT)


def _today_str() -> str:
    return _now_pt().strftime("%Y-%m-%d")


def _parse_leads_from_actions(actions: list | None) -> int:
    """
    Extract lead count from the Facebook actions array.
    Looks for action_type = 'lead', 'onsite_conversion.lead_grouped',
    or 'offsite_conversion.fb_pixel_lead'.
    """
    if not actions:
        return 0
    total = 0
    for action in actions:
        if action.get("action_type", "") in LEAD_ACTION_TYPES:
            try:
                total += int(action.get("value", 0))
            except (ValueError, TypeError):
                pass
    return total


def _safe_cpl(spend: float, leads: int) -> float:
    """Calculate CPL safely, returning 0 when no leads."""
    if leads <= 0:
        return 0.0
    return round(spend / leads, 2)


def _fmt_money(v: float) -> str:
    return f"${v:,.2f}"


def _fmt_num(v: int) -> str:
    return f"{v:,}"


def _fmt_pct(v: float) -> str:
    return f"{v:.2f}%"


# ═════════════════════════════════════════════════════════════════════════════
# DATA COLLECTION
# ═════════════════════════════════════════════════════════════════════════════

async def fetch_ad_insights(
    date_preset: str = "today",
    level: str = "campaign",
) -> list[dict]:
    """
    Fetch ad insights from Facebook Marketing API.

    date_preset: today, yesterday, last_7d, last_30d, this_month
    level: campaign, adset, ad

    Returns a list of insight rows, each enriched with parsed lead count and CPL.
    """
    token = _token()
    if not token:
        print("[AdMonitor] No fb_page_access_token configured")
        return []

    fields = (
        "campaign_name,campaign_id,adset_name,adset_id,ad_name,ad_id,"
        "spend,impressions,reach,clicks,ctr,cpc,actions,cost_per_action_type"
    )
    url = f"{GRAPH_API}/{AD_ACCOUNT_ID}/insights"
    params = {
        "fields": fields,
        "date_preset": date_preset,
        "level": level,
        "access_token": token,
        "limit": 500,
    }

    results: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params=params)
            data = resp.json()

            if "error" in data:
                err = data["error"]
                print(f"[AdMonitor] API error: {err.get('message', err)}")
                return []

            rows = data.get("data", [])

            # Handle pagination
            paging = data.get("paging", {})
            while paging.get("next"):
                resp = await client.get(paging["next"])
                page = resp.json()
                rows.extend(page.get("data", []))
                paging = page.get("paging", {})

            for row in rows:
                spend = float(row.get("spend", 0))
                impressions = int(row.get("impressions", 0))
                reach = int(row.get("reach", 0))
                clicks = int(row.get("clicks", 0))
                ctr = float(row.get("ctr", 0))
                cpc = float(row.get("cpc", 0))
                actions = row.get("actions", [])
                leads = _parse_leads_from_actions(actions)
                cpl = _safe_cpl(spend, leads)

                results.append({
                    "date_start": row.get("date_start", _today_str()),
                    "date_stop": row.get("date_stop", _today_str()),
                    "campaign_id": row.get("campaign_id", ""),
                    "campaign_name": row.get("campaign_name", ""),
                    "adset_id": row.get("adset_id", ""),
                    "adset_name": row.get("adset_name", ""),
                    "ad_id": row.get("ad_id", ""),
                    "ad_name": row.get("ad_name", ""),
                    "spend": spend,
                    "impressions": impressions,
                    "reach": reach,
                    "clicks": clicks,
                    "ctr": ctr,
                    "cpc": cpc,
                    "leads": leads,
                    "cpl": cpl,
                    "actions_raw": actions,
                })

    except httpx.HTTPError as e:
        print(f"[AdMonitor] HTTP error fetching insights: {e}")
    except Exception as e:
        print(f"[AdMonitor] Error fetching insights: {e}")
        traceback.print_exc()

    return results


async def fetch_today_metrics() -> dict:
    """
    Get today's aggregated metrics across all campaigns.
    Returns: {"spend", "impressions", "reach", "clicks", "ctr", "leads", "cpl"}
    """
    rows = await fetch_ad_insights(date_preset="today", level="campaign")
    return _aggregate_rows(rows)


async def fetch_yesterday_metrics() -> dict:
    """Same as today but for yesterday."""
    rows = await fetch_ad_insights(date_preset="yesterday", level="campaign")
    return _aggregate_rows(rows)


def _aggregate_rows(rows: list[dict]) -> dict:
    """Aggregate a list of insight rows into a single summary dict."""
    total_spend = sum(r["spend"] for r in rows)
    total_impressions = sum(r["impressions"] for r in rows)
    total_reach = sum(r["reach"] for r in rows)
    total_clicks = sum(r["clicks"] for r in rows)
    total_leads = sum(r["leads"] for r in rows)
    ctr = (total_clicks / total_impressions * 100) if total_impressions > 0 else 0.0
    cpl = _safe_cpl(total_spend, total_leads)

    return {
        "spend": round(total_spend, 2),
        "impressions": total_impressions,
        "reach": total_reach,
        "clicks": total_clicks,
        "ctr": round(ctr, 2),
        "leads": total_leads,
        "cpl": cpl,
        "campaigns": len(rows),
    }


async def store_daily_metrics(metrics: list[dict]):
    """Store fetched insight rows in the ad_metrics table."""
    if not metrics:
        return

    conn = sqlite3.connect(str(DB_PATH))
    for row in metrics:
        date = row.get("date_start", _today_str())
        actions_json = json.dumps(row.get("actions_raw", []))

        # Upsert: delete existing row for same date+campaign+adset+ad, then insert
        conn.execute(
            "DELETE FROM ad_metrics WHERE date=? AND campaign_id=? AND adset_id=? AND ad_id=?",
            (date, row.get("campaign_id", ""), row.get("adset_id", ""), row.get("ad_id", "")),
        )
        conn.execute(
            """INSERT INTO ad_metrics
               (date, campaign_id, campaign_name, adset_id, adset_name,
                ad_id, ad_name, spend, impressions, reach, clicks, ctr, cpc,
                leads, cpl, actions)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                date,
                row.get("campaign_id", ""),
                row.get("campaign_name", ""),
                row.get("adset_id", ""),
                row.get("adset_name", ""),
                row.get("ad_id", ""),
                row.get("ad_name", ""),
                row.get("spend", 0),
                row.get("impressions", 0),
                row.get("reach", 0),
                row.get("clicks", 0),
                row.get("ctr", 0),
                row.get("cpc", 0),
                row.get("leads", 0),
                row.get("cpl", 0),
                actions_json,
            ),
        )
    conn.commit()
    conn.close()
    print(f"[AdMonitor] Stored {len(metrics)} metric rows")


# ═════════════════════════════════════════════════════════════════════════════
# ALERT SYSTEM
# ═════════════════════════════════════════════════════════════════════════════

async def check_alerts() -> list[dict]:
    """
    Check all alert conditions and return new alerts to send.

    Conditions:
    1. CPL > $8 today
    2. No leads in 24 hours
    3. Spend > 120% of daily budget
    4. Reach dropped 40%+ vs 7-day average
    """
    alerts: list[dict] = []
    today = await fetch_today_metrics()

    # ── 1. CPL Alert ─────────────────────────────────────────────────────────
    if today["leads"] > 0 and today["cpl"] > CPL_ALERT_THRESHOLD:
        if not _was_alert_sent_today("cpl_high"):
            msg = (
                f"CPL alert: {_fmt_money(today['cpl'])} today "
                f"(threshold: {_fmt_money(CPL_ALERT_THRESHOLD)}). "
                f"Spend: {_fmt_money(today['spend'])} for {today['leads']} lead(s)."
            )
            _store_alert("cpl_high", msg, {"cpl": today["cpl"], "spend": today["spend"]})
            alerts.append({"type": "cpl_high", "message": msg})

    # ── 2. No Leads Alert ────────────────────────────────────────────────────
    if today["spend"] > 5.0 and today["leads"] == 0:
        # Check DB for any leads in the last 24h
        last_lead_hours = _hours_since_last_lead()
        if last_lead_hours >= NO_LEAD_HOURS and not _was_alert_sent_today("no_leads"):
            msg = (
                f"No leads in {int(last_lead_hours)}h! "
                f"Spent {_fmt_money(today['spend'])} today with 0 leads. "
                f"Check ad creative and targeting."
            )
            _store_alert("no_leads", msg, {"hours": last_lead_hours, "spend": today["spend"]})
            alerts.append({"type": "no_leads", "message": msg})

    # ── 3. Overspend Alert ───────────────────────────────────────────────────
    overspend_limit = DAILY_BUDGET * OVERSPEND_THRESHOLD
    if today["spend"] > overspend_limit:
        if not _was_alert_sent_today("overspend"):
            pct = round(today["spend"] / DAILY_BUDGET * 100)
            msg = (
                f"Overspend alert: {_fmt_money(today['spend'])} today "
                f"({pct}% of {_fmt_money(DAILY_BUDGET)} budget). "
                f"Check campaign budget caps."
            )
            _store_alert("overspend", msg, {"spend": today["spend"], "budget": DAILY_BUDGET})
            alerts.append({"type": "overspend", "message": msg})

    # ── 4. Reach Drop Alert ──────────────────────────────────────────────────
    avg_7d = get_7day_avg()
    avg_reach = avg_7d.get("avg_reach", 0)
    if avg_reach > 0 and today["reach"] > 0:
        drop_pct = 1.0 - (today["reach"] / avg_reach)
        if drop_pct >= REACH_DROP_THRESHOLD and not _was_alert_sent_today("reach_drop"):
            msg = (
                f"Reach drop alert: {_fmt_num(today['reach'])} today "
                f"vs {_fmt_num(int(avg_reach))} 7-day avg "
                f"({int(drop_pct * 100)}% decline). "
                f"Possible audience fatigue or budget issue."
            )
            _store_alert("reach_drop", msg, {
                "today_reach": today["reach"],
                "avg_reach": avg_reach,
                "drop_pct": round(drop_pct, 2),
            })
            alerts.append({"type": "reach_drop", "message": msg})

    return alerts


def _store_alert(alert_type: str, message: str, data: dict = None):
    """Store alert in ad_alerts table."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "INSERT INTO ad_alerts (alert_type, message, data) VALUES (?, ?, ?)",
        (alert_type, message, json.dumps(data or {})),
    )
    conn.commit()
    conn.close()


def _was_alert_sent_today(alert_type: str) -> bool:
    """Check if this alert type was already sent today (dedup)."""
    today = _today_str()
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        "SELECT COUNT(*) FROM ad_alerts WHERE alert_type=? AND date(created_at)=?",
        (alert_type, today),
    ).fetchone()
    conn.close()
    return (row[0] if row else 0) > 0


def _hours_since_last_lead() -> float:
    """How many hours since we last recorded a lead in ad_metrics."""
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        "SELECT MAX(collected_at) FROM ad_metrics WHERE leads > 0"
    ).fetchone()
    conn.close()
    if not row or not row[0]:
        return 999.0  # No leads ever recorded
    try:
        last = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
        delta = datetime.utcnow() - last
        return delta.total_seconds() / 3600
    except Exception:
        return 999.0


# ═════════════════════════════════════════════════════════════════════════════
# REPORTS
# ═════════════════════════════════════════════════════════════════════════════

async def generate_daily_ad_report() -> str:
    """
    Daily ad report for morning briefing.
    Shows yesterday's performance + 7-day rolling averages.
    """
    yesterday = await fetch_yesterday_metrics()
    avg = get_7day_avg()
    month_total = get_month_spend()

    # Days remaining in month
    now_pt = _now_pt()
    import calendar
    days_in_month = calendar.monthrange(now_pt.year, now_pt.month)[1]
    days_remaining = days_in_month - now_pt.day + 1
    budget_remaining = max(0, (DAILY_BUDGET * days_in_month) - month_total)

    # CPL trend arrow
    cpl_trend = ""
    if avg.get("avg_cpl", 0) > 0 and yesterday["cpl"] > 0:
        if yesterday["cpl"] > avg["avg_cpl"] * 1.1:
            cpl_trend = " (trending UP)"
        elif yesterday["cpl"] < avg["avg_cpl"] * 0.9:
            cpl_trend = " (trending DOWN)"
        else:
            cpl_trend = " (stable)"

    report = f"""AD PERFORMANCE -- Yesterday
{'=' * 34}
Spend: {_fmt_money(yesterday['spend'])}
Impressions: {_fmt_num(yesterday['impressions'])} | Reach: {_fmt_num(yesterday['reach'])}
Clicks: {_fmt_num(yesterday['clicks'])} | CTR: {_fmt_pct(yesterday['ctr'])}
Leads: {yesterday['leads']} | CPL: {_fmt_money(yesterday['cpl'])}

7-Day Rolling Average:
- Avg CPL: {_fmt_money(avg.get('avg_cpl', 0))}{cpl_trend}
- Avg Leads/Day: {avg.get('avg_leads', 0):.1f}
- Total Spend (7d): {_fmt_money(avg.get('total_spend', 0))}

Budget: {_fmt_money(budget_remaining)} remaining this month ({days_remaining} days left)
Month spend so far: {_fmt_money(month_total)}"""

    return report


async def generate_weekly_ad_report() -> str:
    """
    Weekly deep dive (sent Sundays at 10 AM PT).
    Day-by-day breakdown, top performers, and recommendations.
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Get last 7 days of data
    seven_days_ago = (_now_pt() - timedelta(days=7)).strftime("%Y-%m-%d")
    today = _today_str()

    rows = conn.execute(
        """
        WITH latest AS (
            SELECT
                date,
                COALESCE(NULLIF(campaign_id, ''), campaign_name) AS campaign_key,
                MAX(collected_at) AS max_collected
            FROM ad_metrics
            WHERE date >= ? AND date < ?
            GROUP BY date, campaign_key
        ),
        dedup AS (
            SELECT m.*
            FROM ad_metrics m
            JOIN latest l
              ON m.date = l.date
             AND COALESCE(NULLIF(m.campaign_id, ''), m.campaign_name) = l.campaign_key
             AND m.collected_at = l.max_collected
        )
        SELECT date,
               SUM(spend) as spend,
               SUM(impressions) as impressions,
               SUM(reach) as reach,
               SUM(clicks) as clicks,
               SUM(leads) as leads
        FROM dedup
        GROUP BY date
        ORDER BY date ASC
        """,
        (seven_days_ago, today),
    ).fetchall()

    # Campaign-level breakdown
    campaign_rows = conn.execute(
        """
        WITH latest AS (
            SELECT
                date,
                COALESCE(NULLIF(campaign_id, ''), campaign_name) AS campaign_key,
                MAX(collected_at) AS max_collected
            FROM ad_metrics
            WHERE date >= ? AND date < ?
            GROUP BY date, campaign_key
        ),
        dedup AS (
            SELECT m.*
            FROM ad_metrics m
            JOIN latest l
              ON m.date = l.date
             AND COALESCE(NULLIF(m.campaign_id, ''), m.campaign_name) = l.campaign_key
             AND m.collected_at = l.max_collected
        )
        SELECT campaign_name,
               SUM(spend) as spend,
               SUM(leads) as leads
        FROM dedup
        GROUP BY COALESCE(NULLIF(campaign_id, ''), campaign_name)
        ORDER BY leads DESC
        """,
        (seven_days_ago, today),
    ).fetchall()

    # Adset-level breakdown
    adset_rows = conn.execute(
        """
        WITH latest AS (
            SELECT
                date,
                COALESCE(NULLIF(adset_id, ''), adset_name) AS adset_key,
                MAX(collected_at) AS max_collected
            FROM ad_metrics
            WHERE date >= ? AND date < ?
            GROUP BY date, adset_key
        ),
        dedup AS (
            SELECT m.*
            FROM ad_metrics m
            JOIN latest l
              ON m.date = l.date
             AND COALESCE(NULLIF(m.adset_id, ''), m.adset_name) = l.adset_key
             AND m.collected_at = l.max_collected
        )
        SELECT adset_name,
               SUM(spend) as spend,
               SUM(leads) as leads
        FROM dedup
        GROUP BY COALESCE(NULLIF(adset_id, ''), adset_name)
        ORDER BY leads DESC
        """,
        (seven_days_ago, today),
    ).fetchall()

    conn.close()

    # Day names
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    # Build day-by-day section
    day_lines = []
    best_day = {"name": "N/A", "cpl": 999}
    worst_day = {"name": "N/A", "cpl": 0}
    total_spend = 0.0
    total_leads = 0

    for row in rows:
        d = row["date"]
        try:
            dt = datetime.strptime(d, "%Y-%m-%d")
            day_name = day_names[dt.weekday()]
        except Exception:
            day_name = d
        spend = float(row["spend"] or 0)
        leads = int(row["leads"] or 0)
        cpl = _safe_cpl(spend, leads)
        total_spend += spend
        total_leads += leads

        day_lines.append(
            f"  {day_name} ({d}): {_fmt_money(spend)} spend | {leads} leads | {_fmt_money(cpl)} CPL"
        )

        if leads > 0 and cpl < best_day["cpl"]:
            best_day = {"name": f"{day_name} ({d})", "cpl": cpl}
        if leads > 0 and cpl > worst_day["cpl"]:
            worst_day = {"name": f"{day_name} ({d})", "cpl": cpl}

    avg_cpl = _safe_cpl(total_spend, total_leads)

    # Top campaigns
    top_campaign_lines = []
    for cr in campaign_rows[:3]:
        name = cr["campaign_name"] or "Unnamed"
        sp = float(cr["spend"] or 0)
        ld = int(cr["leads"] or 0)
        c = _safe_cpl(sp, ld)
        top_campaign_lines.append(f"  - {name}: {ld} leads at {_fmt_money(c)} CPL")

    # Top adsets
    top_adset_lines = []
    for ar in adset_rows[:3]:
        name = ar["adset_name"] or "Unnamed"
        sp = float(ar["spend"] or 0)
        ld = int(ar["leads"] or 0)
        c = _safe_cpl(sp, ld)
        top_adset_lines.append(f"  - {name}: {ld} leads at {_fmt_money(c)} CPL")

    # Generate recommendations
    recommendations = _generate_recommendations(
        avg_cpl=avg_cpl,
        total_leads=total_leads,
        total_spend=total_spend,
        best_day=best_day,
        worst_day=worst_day,
        campaign_rows=campaign_rows,
    )

    day_section = "\n".join(day_lines) if day_lines else "  No data available"
    campaign_section = "\n".join(top_campaign_lines) if top_campaign_lines else "  No campaign data"
    adset_section = "\n".join(top_adset_lines) if top_adset_lines else "  No adset data"
    rec_section = "\n".join(f"  - {r}" for r in recommendations) if recommendations else "  - Insufficient data for recommendations"

    report = f"""WEEKLY AD DEEP DIVE
{'=' * 40}

DAY-BY-DAY:
{day_section}

WEEK TOTALS:
  - Total Spend: {_fmt_money(total_spend)}
  - Total Leads: {total_leads}
  - Avg CPL: {_fmt_money(avg_cpl)}
  - Best Day: {best_day['name']} ({_fmt_money(best_day['cpl'])} CPL)
  - Worst Day: {worst_day['name']} ({_fmt_money(worst_day['cpl'])} CPL)

TOP PERFORMING CAMPAIGNS:
{campaign_section}

TOP PERFORMING AD SETS:
{adset_section}

RECOMMENDATIONS:
{rec_section}"""

    return report


def _generate_recommendations(
    avg_cpl: float,
    total_leads: int,
    total_spend: float,
    best_day: dict,
    worst_day: dict,
    campaign_rows: list,
) -> list[str]:
    """Generate data-driven recommendations based on weekly trends."""
    recs: list[str] = []

    # CPL trending high
    if avg_cpl > CPL_ALERT_THRESHOLD:
        recs.append(
            f"CPL at {_fmt_money(avg_cpl)} is above {_fmt_money(CPL_ALERT_THRESHOLD)} target. "
            f"Consider refreshing ad creative or tightening audience targeting."
        )

    # Low lead volume
    if total_leads < 7:  # Less than 1/day average
        recs.append(
            "Lead volume is low (<1/day avg). Consider increasing budget or "
            "expanding lookalike audiences."
        )

    # Overspend
    weekly_budget = DAILY_BUDGET * 7
    if total_spend > weekly_budget * 1.1:
        recs.append(
            f"Spend ({_fmt_money(total_spend)}) exceeded weekly budget "
            f"({_fmt_money(weekly_budget)}). Review campaign spending limits."
        )

    # Campaign-level insights
    if len(campaign_rows) >= 2:
        top = campaign_rows[0]
        top_leads = int(top["leads"] or 0)
        top_spend = float(top["spend"] or 0)
        bottom = campaign_rows[-1]
        bottom_leads = int(bottom["leads"] or 0)
        bottom_spend = float(bottom["spend"] or 0)

        if bottom_spend > 10 and bottom_leads == 0:
            recs.append(
                f"Campaign '{bottom['campaign_name']}' spent {_fmt_money(bottom_spend)} "
                f"with 0 leads. Consider pausing or overhauling."
            )

        if top_leads > 0:
            top_cpl = _safe_cpl(top_spend, top_leads)
            recs.append(
                f"Top performer: '{top['campaign_name']}' at {_fmt_money(top_cpl)} CPL. "
                f"Consider scaling budget for this campaign."
            )

    # Best vs worst day
    if best_day["cpl"] < 999 and worst_day["cpl"] > 0:
        if worst_day["cpl"] > best_day["cpl"] * 2:
            recs.append(
                f"Big CPL spread: {best_day['name']} ({_fmt_money(best_day['cpl'])}) "
                f"vs {worst_day['name']} ({_fmt_money(worst_day['cpl'])}). "
                f"Investigate what drove the difference."
            )

    if not recs:
        recs.append("Performance looks stable. Keep monitoring and testing new creatives.")

    return recs


def get_7day_avg() -> dict:
    """Get 7-day rolling averages for spend, leads, CPL, and reach."""
    conn = sqlite3.connect(str(DB_PATH))
    seven_days_ago = (_now_pt() - timedelta(days=7)).strftime("%Y-%m-%d")
    today = _today_str()

    row = conn.execute(
        """
        WITH latest AS (
            SELECT
                date,
                COALESCE(NULLIF(campaign_id, ''), campaign_name) AS campaign_key,
                MAX(collected_at) AS max_collected
            FROM ad_metrics
            WHERE date >= ? AND date < ?
            GROUP BY date, campaign_key
        ),
        dedup AS (
            SELECT m.date, m.spend, m.leads, m.reach
            FROM ad_metrics m
            JOIN latest l
              ON m.date = l.date
             AND COALESCE(NULLIF(m.campaign_id, ''), m.campaign_name) = l.campaign_key
             AND m.collected_at = l.max_collected
        )
        SELECT
            SUM(spend) as total_spend,
            SUM(leads) as total_leads,
            AVG(reach) as avg_reach,
            COUNT(DISTINCT date) as days
        FROM (
            SELECT date,
                   SUM(spend) as spend,
                   SUM(leads) as leads,
                   SUM(reach) as reach
            FROM dedup
            GROUP BY date
        )
        """,
        (seven_days_ago, today),
    ).fetchone()
    conn.close()

    if not row or not row[0]:
        return {"total_spend": 0, "total_leads": 0, "avg_cpl": 0, "avg_reach": 0, "avg_leads": 0, "days": 0}

    total_spend = float(row[0] or 0)
    total_leads = int(row[1] or 0)
    avg_reach = float(row[2] or 0)
    days = int(row[3] or 1) or 1  # prevent div-by-zero

    return {
        "total_spend": round(total_spend, 2),
        "total_leads": total_leads,
        "avg_cpl": _safe_cpl(total_spend, total_leads),
        "avg_reach": round(avg_reach),
        "avg_leads": round(total_leads / days, 1),
        "days": days,
    }


def get_month_spend() -> float:
    """Total ad spend for the current calendar month."""
    now_pt = _now_pt()
    month_start = now_pt.strftime("%Y-%m-01")
    tomorrow = (now_pt + timedelta(days=1)).strftime("%Y-%m-%d")

    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        """
        WITH latest AS (
            SELECT
                date,
                COALESCE(NULLIF(campaign_id, ''), campaign_name) AS campaign_key,
                MAX(collected_at) AS max_collected
            FROM ad_metrics
            WHERE date >= ? AND date < ?
            GROUP BY date, campaign_key
        ),
        dedup AS (
            SELECT m.spend
            FROM ad_metrics m
            JOIN latest l
              ON m.date = l.date
             AND COALESCE(NULLIF(m.campaign_id, ''), m.campaign_name) = l.campaign_key
             AND m.collected_at = l.max_collected
        )
        SELECT SUM(spend) FROM dedup
        """,
        (month_start, tomorrow),
    ).fetchone()
    conn.close()

    return round(float(row[0] or 0), 2) if row else 0.0


# ═════════════════════════════════════════════════════════════════════════════
# TELEGRAM INTEGRATION — Natural Language Ad Queries
# ═════════════════════════════════════════════════════════════════════════════

async def handle_ads_query(query: str) -> str:
    """
    Handle natural language ad queries from Kai via Telegram.

    Examples:
      - "How are the ads doing?" -> Today's metrics
      - "How much have I spent this week?" -> Weekly spend
      - "What's my CPL?" -> Current CPL with trend
      - "Ad report" -> Full daily report
      - "Weekly ads" -> Weekly deep dive
    """
    q = query.lower().strip()

    # Weekly report
    if any(kw in q for kw in ("weekly", "week report", "deep dive", "this week ads")):
        return await generate_weekly_ad_report()

    # Full daily report
    if any(kw in q for kw in ("ad report", "ads report", "daily report", "full report")):
        return await generate_daily_ad_report()

    # Spend queries
    if any(kw in q for kw in ("spend", "spent", "cost", "budget")):
        if "week" in q or "7 day" in q or "7d" in q:
            avg = get_7day_avg()
            return (
                f"7-day ad spend: {_fmt_money(avg['total_spend'])}\n"
                f"Avg/day: {_fmt_money(avg['total_spend'] / max(avg['days'], 1))}\n"
                f"Daily budget: {_fmt_money(DAILY_BUDGET)}"
            )
        elif "month" in q:
            total = get_month_spend()
            now_pt = _now_pt()
            import calendar
            days_in_month = calendar.monthrange(now_pt.year, now_pt.month)[1]
            budget_total = DAILY_BUDGET * days_in_month
            return (
                f"Month ad spend: {_fmt_money(total)}\n"
                f"Monthly budget: {_fmt_money(budget_total)}\n"
                f"Remaining: {_fmt_money(max(0, budget_total - total))}"
            )
        else:
            today = await fetch_today_metrics()
            return (
                f"Today's spend: {_fmt_money(today['spend'])}\n"
                f"Daily budget: {_fmt_money(DAILY_BUDGET)}\n"
                f"({int(today['spend'] / DAILY_BUDGET * 100)}% used)"
            )

    # CPL queries
    if any(kw in q for kw in ("cpl", "cost per lead", "cost per")):
        today = await fetch_today_metrics()
        avg = get_7day_avg()
        trend = ""
        if avg["avg_cpl"] > 0 and today["cpl"] > 0:
            if today["cpl"] > avg["avg_cpl"] * 1.1:
                trend = " (above 7d avg)"
            elif today["cpl"] < avg["avg_cpl"] * 0.9:
                trend = " (below 7d avg)"
            else:
                trend = " (in line with 7d avg)"
        return (
            f"Today's CPL: {_fmt_money(today['cpl'])}{trend}\n"
            f"7-day avg CPL: {_fmt_money(avg['avg_cpl'])}\n"
            f"Today: {today['leads']} leads from {_fmt_money(today['spend'])} spend"
        )

    # Lead queries
    if any(kw in q for kw in ("lead", "leads", "conversion")):
        today = await fetch_today_metrics()
        avg = get_7day_avg()
        return (
            f"Today: {today['leads']} leads ({_fmt_money(today['cpl'])} CPL)\n"
            f"7-day avg: {avg['avg_leads']:.1f} leads/day\n"
            f"7-day total: {avg['total_leads']} leads"
        )

    # Default: today's snapshot
    today = await fetch_today_metrics()
    avg = get_7day_avg()

    return (
        f"Ad Performance Today:\n"
        f"  Spend: {_fmt_money(today['spend'])} | Leads: {today['leads']} | CPL: {_fmt_money(today['cpl'])}\n"
        f"  Impressions: {_fmt_num(today['impressions'])} | Clicks: {_fmt_num(today['clicks'])} | CTR: {_fmt_pct(today['ctr'])}\n"
        f"\n"
        f"7-Day Averages:\n"
        f"  CPL: {_fmt_money(avg['avg_cpl'])} | Leads/Day: {avg['avg_leads']:.1f} | Spend: {_fmt_money(avg['total_spend'] / max(avg['days'], 1))}/day"
    )


# ═════════════════════════════════════════════════════════════════════════════
# BACKGROUND SCHEDULER
# ═════════════════════════════════════════════════════════════════════════════

async def run_ad_monitor(send_fn):
    """
    Background loop. Runs every 15 minutes.
    send_fn is an async function to send Telegram messages.

    Every 15 min:
      - Fetch today's metrics
      - Check alert conditions
      - Send any new alerts

    At specific times:
      - 8:00 AM PT: Daily ad report (part of morning package)
      - 10:00 AM PT Sundays: Weekly deep dive

    Stores metrics hourly for trend analysis.
    """
    global _sent_today, _last_date, _last_hourly_store

    print("[AdMonitor] Background scheduler started")
    init_ad_monitor_db()

    # Register with hierarchy status tracker
    try:
        from core.agent_hierarchy import update_agent_status, init_hierarchy_db
        init_hierarchy_db()
        update_agent_status("ad-optimizer", "data", "active", "Initializing ad monitor")
    except Exception:
        pass

    # Small initial delay to let everything else boot up
    await asyncio.sleep(10)

    while True:
        try:
            # Update hierarchy status each cycle
            try:
                from core.agent_hierarchy import update_agent_status
                update_agent_status("ad-optimizer", "data", "active", "Checking ad metrics")
            except Exception:
                pass

            now_pt = _now_pt()
            today_date = now_pt.strftime("%Y-%m-%d")
            current_time = now_pt.strftime("%H:%M")
            current_hour = now_pt.strftime("%H:00")

            # Reset sent_today tracker at midnight PT
            if today_date != _last_date:
                _sent_today.clear()
                _last_date = today_date
                print(f"[AdMonitor] New day: {today_date} — reset daily trackers")

            # ── Fetch + store metrics (every cycle) ──────────────────────────
            try:
                insights = await fetch_ad_insights(date_preset="today", level="campaign")
                if insights:
                    await store_daily_metrics(insights)
            except Exception as e:
                print(f"[AdMonitor] Fetch error: {e}")

            # ── Store hourly snapshot (once per hour) ────────────────────────
            if current_hour != _last_hourly_store:
                _last_hourly_store = current_hour
                try:
                    # Also fetch adset-level for granular data
                    adset_insights = await fetch_ad_insights(date_preset="today", level="adset")
                    if adset_insights:
                        await store_daily_metrics(adset_insights)
                except Exception as e:
                    print(f"[AdMonitor] Hourly adset fetch error: {e}")

            # ── Check alerts ─────────────────────────────────────────────────
            try:
                new_alerts = await check_alerts()
                for alert in new_alerts:
                    await _safe_send(send_fn, alert["message"])
            except Exception as e:
                print(f"[AdMonitor] Alert check error: {e}")

            # ── Every 15 min: Poll for new FB ad leads (webhook fallback) ───
            poll_key = f"lead_poll_{current_hour}_{now_pt.minute // 15}"
            if poll_key not in _sent_today:
                _sent_today.add(poll_key)
                try:
                    from core.meta_ads import MetaAdsManager
                    from core.services import get_lead_service
                    from core.lead_pipeline import get_pipeline
                    mgr = MetaAdsManager()
                    lead_svc = get_lead_service()
                    pipeline = get_pipeline()
                    forms = mgr.get_lead_forms()
                    new_leads = 0
                    for form in forms:
                        if "error" in form or form.get("status") != "ACTIVE":
                            continue
                        # Get leads from last 2 days to catch any webhook misses
                        from datetime import timedelta
                        since = (now_pt - timedelta(days=2)).strftime("%Y-%m-%d")
                        leads = mgr.get_leads(form["id"], since=since)
                        for lead in leads:
                            if "error" in lead:
                                continue
                            # Dedup: check if leadgen_id already in leads table
                            leadgen_id = lead.get("id", "")
                            if not leadgen_id:
                                continue
                            fields = lead.get("fields", {}) or {}
                            full_name = (fields.get("full_name", "") or "").strip()
                            first_name = (fields.get("first_name", "") or "").strip()
                            last_name = (fields.get("last_name", "") or "").strip()
                            if full_name and not first_name:
                                parts = full_name.split()
                                first_name = parts[0] if parts else ""
                                last_name = " ".join(parts[1:]) if len(parts) > 1 else last_name

                            payload = {
                                "lead_uuid": leadgen_id,
                                "first_name": first_name,
                                "last_name": last_name,
                                "full_name": full_name,
                                "phone": fields.get("phone_number", fields.get("phone", "")),
                                "email": fields.get("email", ""),
                                "source": "facebook_ad",
                                "source_detail": "Facebook Lead Form Poll Fallback",
                                "form_id": form["id"],
                                "event_type": fields.get("event_type", fields.get("what_type_of_event?", "")),
                                "event_date": fields.get("event_date", fields.get("preferred_date", "")),
                                "event_city": fields.get("city", fields.get("event_city", "")),
                                "guest_count": fields.get("guest_count", fields.get("number_of_guests", 0)),
                                "date_added": lead.get("created_time", ""),
                            }

                            created = None
                            if lead_svc:
                                try:
                                    created = lead_svc.create_lead(payload)
                                except Exception as e:
                                    print(f"[AdMonitor] Lead create error for {leadgen_id}: {e}")
                            else:
                                try:
                                    conn = sqlite3.connect(str(Path.home() / ".nexus" / "memory.db"))
                                    exists = conn.execute(
                                        "SELECT id FROM leads WHERE lead_uuid = ?",
                                        (leadgen_id,),
                                    ).fetchone()
                                    if not exists:
                                        conn.execute(
                                            "INSERT OR IGNORE INTO leads "
                                            "(lead_uuid, first_name, last_name, full_name, phone, email, "
                                            "event_type, event_date, event_city, guest_count, source, source_detail, "
                                            "form_id, status, date_added) "
                                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'facebook_ad', 'Facebook Lead Form Poll Fallback', ?, 'new', ?)",
                                            (
                                                payload.get("lead_uuid", ""),
                                                payload.get("first_name", ""),
                                                payload.get("last_name", ""),
                                                payload.get("full_name", ""),
                                                payload.get("phone", ""),
                                                payload.get("email", ""),
                                                payload.get("event_type", ""),
                                                payload.get("event_date", ""),
                                                payload.get("event_city", ""),
                                                int(payload.get("guest_count", 0) or 0),
                                                payload.get("form_id", ""),
                                                payload.get("date_added", ""),
                                            ),
                                        )
                                        conn.commit()
                                        created = {"id": conn.execute("SELECT last_insert_rowid()").fetchone()[0]}
                                    conn.close()
                                except Exception as e:
                                    print(f"[AdMonitor] Fallback insert error for {leadgen_id}: {e}")

                            if created and not created.get("_deduplicated"):
                                new_leads += 1
                                if pipeline:
                                    try:
                                        asyncio.create_task(pipeline.process_new_lead(int(created["id"])))
                                    except Exception as e:
                                        print(f"[AdMonitor] Pipeline trigger failed for lead {created.get('id')}: {e}")
                    if new_leads > 0:
                        await _safe_send(send_fn,
                            f"NEW FB AD LEAD(S): {new_leads} lead(s) ingested from ad forms")
                        print(f"[AdMonitor] Polled {new_leads} new leads from FB ad forms")
                except Exception as e:
                    print(f"[AdMonitor] Lead poll error: {e}")

            # ── 8:00 AM PT: Daily ad report ──────────────────────────────────
            if current_time == "08:00" and "daily_ad_report" not in _sent_today:
                _sent_today.add("daily_ad_report")
                try:
                    # Fetch and store yesterday's full data first
                    yesterday_insights = await fetch_ad_insights(
                        date_preset="yesterday", level="ad"
                    )
                    if yesterday_insights:
                        await store_daily_metrics(yesterday_insights)

                    report = await generate_daily_ad_report()
                    await _safe_send(send_fn, report)
                    print("[AdMonitor] Daily ad report sent")
                except Exception as e:
                    print(f"[AdMonitor] Daily report error: {e}")
                    await _safe_send(send_fn, f"[AdMonitor] Daily report error: {e}")

            # ── 10:00 AM PT Sundays: Weekly deep dive ────────────────────────
            if (
                now_pt.weekday() == 6  # Sunday
                and current_time == "10:00"
                and "weekly_ad_report" not in _sent_today
            ):
                _sent_today.add("weekly_ad_report")
                try:
                    report = await generate_weekly_ad_report()
                    await _safe_send(send_fn, report)
                    print("[AdMonitor] Weekly ad report sent")
                except Exception as e:
                    print(f"[AdMonitor] Weekly report error: {e}")
                    await _safe_send(send_fn, f"[AdMonitor] Weekly report error: {e}")

            # ── Every 6 hours: evaluate automated optimization rules ────────
            if now_pt.hour in (8, 14, 20) and now_pt.minute < 15:
                eval_key = f"ad_rules_eval_{now_pt.hour}"
                if eval_key not in _sent_today:
                    _sent_today.add(eval_key)
                    try:
                        from core.meta_ads import MetaAdsManager
                        mgr = MetaAdsManager()
                        recs = mgr.evaluate_automated_rules()
                        if recs:
                            msg = "AD OPTIMIZATION RECOMMENDATIONS\n"
                            for i, r in enumerate(recs, 1):
                                msg += f"\n{i}. {r.get('action', 'unknown')}: {r.get('reason', '')}"
                                msg += f"\n   Ad: {r.get('ad_name', r.get('ad_id', 'N/A'))}"
                            msg += "\n\nThese are suggestions only. No changes made."
                            await _safe_send(send_fn, msg)
                            print(f"[AdMonitor] Sent {len(recs)} optimization recommendations")
                        else:
                            print("[AdMonitor] No optimization recommendations this cycle")
                    except Exception as e:
                        print(f"[AdMonitor] Rules eval error: {e}")

        except Exception as e:
            print(f"[AdMonitor] Scheduler loop error: {e}")
            traceback.print_exc()

        # Sleep 15 minutes (check every minute for time-sensitive sends)
        # We use a 60s loop internally so we don't miss the exact minute
        for _ in range(15):
            await asyncio.sleep(60)
            # Re-check time-sensitive triggers within the 15-min window
            now_check = _now_pt()
            check_time = now_check.strftime("%H:%M")
            check_date = now_check.strftime("%Y-%m-%d")

            if check_date != _last_date:
                _sent_today.clear()
                _last_date = check_date

            if check_time == "08:00" and "daily_ad_report" not in _sent_today:
                _sent_today.add("daily_ad_report")
                try:
                    yesterday_insights = await fetch_ad_insights(
                        date_preset="yesterday", level="ad"
                    )
                    if yesterday_insights:
                        await store_daily_metrics(yesterday_insights)
                    report = await generate_daily_ad_report()
                    await _safe_send(send_fn, report)
                    print("[AdMonitor] Daily ad report sent (inner loop)")
                except Exception as e:
                    print(f"[AdMonitor] Daily report error (inner): {e}")

            if (
                now_check.weekday() == 6
                and check_time == "10:00"
                and "weekly_ad_report" not in _sent_today
            ):
                _sent_today.add("weekly_ad_report")
                try:
                    report = await generate_weekly_ad_report()
                    await _safe_send(send_fn, report)
                    print("[AdMonitor] Weekly ad report sent (inner loop)")
                except Exception as e:
                    print(f"[AdMonitor] Weekly report error (inner): {e}")


async def _safe_send(send_fn, message: str):
    """Send a message via the provided send function, handling errors."""
    if not send_fn:
        print(f"[AdMonitor] No send_fn — message: {message[:100]}")
        return
    try:
        await send_fn(message)
    except Exception as e:
        print(f"[AdMonitor] Send error: {e}")
