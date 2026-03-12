from __future__ import annotations
"""
Analytics & Reporting Engine — Zoar Bathroom Rentals CRM

Comprehensive analytics covering:
  - Lead metrics: counts by source, status, event_type, and trends
  - Revenue tracking: log payments, breakdowns, projections
  - Conversion funnel: new → contacted → quoted → booked → completed
  - Ad spend & CPL: cost per lead by source (from ad_metrics)
  - Response metrics: time to first contact, reply rates
  - Channel performance: SMS vs email approximated engagement
  - Daily snapshots: midnight scheduler saves metrics to analytics_snapshots
  - Telegram commands: /analytics, /revenue, /funnel
  - Weekly & monthly reports: formatted for Telegram

Database: ~/.nexus/memory.db (WAL mode)
Timezone: America/Los_Angeles
"""
import asyncio
import sqlite3
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

# ── Constants ────────────────────────────────────────────────────────────────

DB_PATH = Path.home() / ".nexus" / "memory.db"
LA_TZ = ZoneInfo("America/Los_Angeles")

# All known lead sources for consistent reporting
KNOWN_SOURCES = [
    "fb_marketplace_en", "fb_marketplace_es",
    "craigslist_en", "craigslist_es",
    "facebook", "facebook_test",
    "the_knot", "weddingwire",
    "email_inquiry", "website",
    "instagram", "google", "referral",
]

# CRM pipeline stages (from pipeline_service.py)
PIPELINE_STAGES = [
    "new_lead", "auto_contacted", "qualifying",
    "quote_sent", "deposit_pending", "booked", "completed", "lost",
]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _now_la() -> datetime:
    """Current time in Pacific."""
    return datetime.now(LA_TZ)


def _now_utc() -> str:
    """UTC timestamp string for DB storage."""
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _days_ago(days: int) -> str:
    """UTC timestamp string N days in the past."""
    return (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _conn() -> sqlite3.Connection:
    """Get a DB connection with WAL mode and row factory."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """Check if a table exists in the database."""
    row = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
        (table,)
    ).fetchone()
    return row[0] > 0 if row else False


def _safe_div(numerator: float, denominator: float, decimals: int = 2) -> float:
    """Safe division, returns 0.0 when denominator is zero."""
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, decimals)


def _fmt_money(v: float) -> str:
    return f"${v:,.2f}"


def _fmt_pct(v: float) -> str:
    return f"{v:.1f}%"


def _fmt_num(v: int | float) -> str:
    if isinstance(v, float):
        return f"{v:,.1f}"
    return f"{v:,}"


# ── Database Init ────────────────────────────────────────────────────────────

def init_analytics_db() -> None:
    """Create revenue_entries and analytics_snapshots tables."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS revenue_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        booking_id TEXT DEFAULT '',
        lead_id INTEGER DEFAULT 0,
        amount REAL NOT NULL DEFAULT 0,
        type TEXT NOT NULL DEFAULT 'booking',
        description TEXT DEFAULT '',
        payment_method TEXT DEFAULT '',
        date TEXT NOT NULL DEFAULT (date('now')),
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS analytics_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL UNIQUE,
        total_leads INTEGER DEFAULT 0,
        new_leads INTEGER DEFAULT 0,
        converted_leads INTEGER DEFAULT 0,
        revenue REAL DEFAULT 0,
        ad_spend REAL DEFAULT 0,
        cpl REAL DEFAULT 0,
        conversion_rate REAL DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_revenue_date ON revenue_entries(date);
    CREATE INDEX IF NOT EXISTS idx_revenue_lead ON revenue_entries(lead_id);
    CREATE INDEX IF NOT EXISTS idx_revenue_booking ON revenue_entries(booking_id);
    CREATE INDEX IF NOT EXISTS idx_snapshots_date ON analytics_snapshots(date);
    """)
    conn.commit()
    conn.close()
    print("[Analytics] Database tables ready")


# ═════════════════════════════════════════════════════════════════════════════
# REVENUE LOGGING
# ═════════════════════════════════════════════════════════════════════════════

def log_revenue(
    booking_id: str,
    lead_id: int,
    amount: float,
    type: str = "booking",
    description: str = "",
    payment_method: str = "",
) -> int:
    """Record a payment/revenue entry. Returns the new entry ID."""
    conn = _conn()
    cur = conn.execute(
        "INSERT INTO revenue_entries (booking_id, lead_id, amount, type, description, payment_method, date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (booking_id, lead_id, amount, type, description, payment_method,
         _now_la().strftime("%Y-%m-%d")),
    )
    entry_id = cur.lastrowid
    conn.commit()
    conn.close()
    print(f"[Analytics] Revenue logged: {_fmt_money(amount)} ({type}) for booking {booking_id}")
    return entry_id


def get_revenue_total(start_date: str, end_date: str) -> float:
    """Sum revenue for a date range (YYYY-MM-DD strings)."""
    conn = _conn()
    if not _table_exists(conn, "revenue_entries"):
        conn.close()
        return 0.0
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM revenue_entries WHERE date >= ? AND date <= ?",
        (start_date, end_date),
    ).fetchone()
    conn.close()
    return float(row[0]) if row else 0.0


def get_revenue_breakdown(days: int = 30) -> dict[str, Any]:
    """Revenue broken down by source, event_type, and month."""
    conn = _conn()
    result: dict[str, Any] = {"by_source": {}, "by_event_type": {}, "by_month": []}

    if not _table_exists(conn, "revenue_entries"):
        conn.close()
        return result

    cutoff = _days_ago(days)
    has_leads = _table_exists(conn, "leads")

    # By source (join with leads)
    if has_leads:
        rows = conn.execute(
            "SELECT l.source, COALESCE(SUM(r.amount), 0) AS total "
            "FROM revenue_entries r "
            "LEFT JOIN leads l ON r.lead_id = l.id "
            "WHERE r.created_at >= ? "
            "GROUP BY l.source ORDER BY total DESC",
            (cutoff,),
        ).fetchall()
        result["by_source"] = {(row["source"] or "unknown"): float(row["total"]) for row in rows}

    # By event_type (join with leads, event_type may not exist)
    if has_leads:
        try:
            rows = conn.execute(
                "SELECT l.event_type, COALESCE(SUM(r.amount), 0) AS total "
                "FROM revenue_entries r "
                "LEFT JOIN leads l ON r.lead_id = l.id "
                "WHERE r.created_at >= ? AND l.event_type != '' "
                "GROUP BY l.event_type ORDER BY total DESC",
                (cutoff,),
            ).fetchall()
            result["by_event_type"] = {(row["event_type"] or "unknown"): float(row["total"]) for row in rows}
        except sqlite3.OperationalError:
            # event_type column may not exist yet
            pass

    # By month (last 6 months)
    rows = conn.execute(
        "SELECT strftime('%Y-%m', date) AS month, COALESCE(SUM(amount), 0) AS total "
        "FROM revenue_entries "
        "WHERE date >= date('now', '-6 months') "
        "GROUP BY month ORDER BY month ASC",
    ).fetchall()
    result["by_month"] = [{"month": row["month"], "total": float(row["total"])} for row in rows]

    conn.close()
    return result


# ═════════════════════════════════════════════════════════════════════════════
# CORE METRICS FUNCTIONS
# ═════════════════════════════════════════════════════════════════════════════

def get_lead_metrics(days: int = 30) -> dict[str, Any]:
    """Lead counts: total, new, by source, by status, by event_type."""
    conn = _conn()
    if not _table_exists(conn, "leads"):
        conn.close()
        return {"total": 0, "new_leads": 0, "by_source": {}, "by_status": {},
                "by_event_type": {}, "period_days": days}
    cutoff = _days_ago(days)

    total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    new_leads = conn.execute(
        "SELECT COUNT(*) FROM leads WHERE discovered_at >= ?", (cutoff,)
    ).fetchone()[0]

    # By source
    rows = conn.execute(
        "SELECT source, COUNT(*) AS cnt FROM leads WHERE discovered_at >= ? "
        "GROUP BY source ORDER BY cnt DESC",
        (cutoff,),
    ).fetchall()
    by_source = {(row["source"] or "unknown"): row["cnt"] for row in rows}

    # By status
    rows = conn.execute(
        "SELECT status, COUNT(*) AS cnt FROM leads "
        "GROUP BY status ORDER BY cnt DESC",
    ).fetchall()
    by_status = {row["status"]: row["cnt"] for row in rows}

    # By event_type (may not exist in schema yet)
    by_event_type: dict[str, int] = {}
    try:
        rows = conn.execute(
            "SELECT event_type, COUNT(*) AS cnt FROM leads "
            "WHERE discovered_at >= ? AND event_type != '' "
            "GROUP BY event_type ORDER BY cnt DESC",
            (cutoff,),
        ).fetchall()
        by_event_type = {(row["event_type"] or "unknown"): row["cnt"] for row in rows}
    except sqlite3.OperationalError:
        pass

    conn.close()
    return {
        "total": total,
        "new_leads": new_leads,
        "by_source": by_source,
        "by_status": by_status,
        "by_event_type": by_event_type,
        "period_days": days,
    }


def get_conversion_metrics(days: int = 30) -> dict[str, Any]:
    """Conversion funnel: leads → contacted → quoted → booked → completed."""
    conn = _conn()
    if not _table_exists(conn, "leads"):
        conn.close()
        return {"total_leads": 0, "contacted": 0, "quoted": 0, "booked": 0,
                "completed": 0, "lost": 0, "conversion_rate": 0.0,
                "funnel_detail": {}, "period_days": days}
    cutoff = _days_ago(days)

    total = conn.execute(
        "SELECT COUNT(*) FROM leads WHERE discovered_at >= ?", (cutoff,)
    ).fetchone()[0]

    # Use booking_status for CRM pipeline stages (added by migration)
    funnel: dict[str, int] = {}
    try:
        for stage in PIPELINE_STAGES:
            row = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE booking_status = ? AND discovered_at >= ?",
                (stage, cutoff),
            ).fetchone()
            funnel[stage] = row[0] if row else 0
    except sqlite3.OperationalError:
        # booking_status column doesn't exist — fallback to old status field
        status_map = {
            "new_lead": ("new", "awaiting_approval"),
            "auto_contacted": ("sms_sent", "initial_contact"),
            "qualifying": ("waiting_reply", "replied"),
            "quote_sent": ("follow_up_1",),
            "booked": ("follow_up_2",),
            "completed": ("closed",),
            "lost": ("opted_out",),
        }
        for stage, statuses in status_map.items():
            placeholders = ", ".join("?" for _ in statuses)
            row = conn.execute(
                f"SELECT COUNT(*) FROM leads WHERE status IN ({placeholders}) AND discovered_at >= ?",
                (*statuses, cutoff),
            ).fetchone()
            funnel[stage] = row[0] if row else 0

    # Simplified funnel for display
    contacted = funnel.get("auto_contacted", 0) + funnel.get("qualifying", 0)
    quoted = funnel.get("quote_sent", 0) + funnel.get("deposit_pending", 0)
    booked = funnel.get("booked", 0)
    completed = funnel.get("completed", 0)

    conversion_rate = _safe_div((booked + completed) * 100, total) if total > 0 else 0.0

    conn.close()
    return {
        "total_leads": total,
        "contacted": contacted,
        "quoted": quoted,
        "booked": booked,
        "completed": completed,
        "lost": funnel.get("lost", 0),
        "conversion_rate": conversion_rate,
        "funnel_detail": funnel,
        "period_days": days,
    }


def get_revenue_metrics(days: int = 30) -> dict[str, Any]:
    """Revenue: total, avg booking, by source, by event_type, MoM growth."""
    conn = _conn()
    result: dict[str, Any] = {
        "total": 0.0,
        "avg_booking": 0.0,
        "by_source": {},
        "by_event_type": {},
        "mom_growth": 0.0,
        "period_days": days,
    }

    if not _table_exists(conn, "revenue_entries"):
        conn.close()
        return result

    cutoff = _days_ago(days)

    # Total revenue and booking count
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0), COUNT(*) FROM revenue_entries WHERE created_at >= ?",
        (cutoff,),
    ).fetchone()
    total_rev = float(row[0])
    booking_count = row[1]
    result["total"] = total_rev
    result["avg_booking"] = _safe_div(total_rev, booking_count)

    # Also count from booking_confirmations total_price if revenue_entries is sparse
    if total_rev == 0 and _table_exists(conn, "booking_confirmations"):
        row = conn.execute(
            "SELECT COALESCE(SUM(total_price), 0), COUNT(*) "
            "FROM booking_confirmations WHERE created_at >= ? AND status != 'cancelled'",
            (cutoff,),
        ).fetchone()
        total_rev = float(row[0])
        booking_count = row[1]
        result["total"] = total_rev
        result["avg_booking"] = _safe_div(total_rev, booking_count)

    # By source
    breakdown = get_revenue_breakdown(days)
    result["by_source"] = breakdown["by_source"]
    result["by_event_type"] = breakdown["by_event_type"]

    # Month-over-month growth
    now = _now_la()
    this_month_start = now.replace(day=1).strftime("%Y-%m-%d")
    last_month_end = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m-%d")
    last_month_start = (now.replace(day=1) - timedelta(days=1)).replace(day=1).strftime("%Y-%m-%d")

    this_month_rev = get_revenue_total(this_month_start, now.strftime("%Y-%m-%d"))
    last_month_rev = get_revenue_total(last_month_start, last_month_end)

    if last_month_rev > 0:
        result["mom_growth"] = round((this_month_rev - last_month_rev) / last_month_rev * 100, 1)

    conn.close()
    return result


def get_cpl_metrics(days: int = 30) -> dict[str, Any]:
    """Cost per lead by source — ad spend from ad_metrics / leads from that source."""
    conn = _conn()
    result: dict[str, Any] = {"overall": 0.0, "by_source": {}, "total_spend": 0.0, "total_leads": 0}
    cutoff = _days_ago(days)

    # Total ad spend from ad_metrics
    total_spend = 0.0
    if _table_exists(conn, "ad_metrics"):
        row = conn.execute(
            "SELECT COALESCE(SUM(spend), 0) FROM ad_metrics WHERE collected_at >= ?",
            (cutoff,),
        ).fetchone()
        total_spend = float(row[0]) if row else 0.0

    # Total leads in period
    if not _table_exists(conn, "leads"):
        conn.close()
        result["total_spend"] = total_spend
        return result

    row = conn.execute(
        "SELECT COUNT(*) FROM leads WHERE discovered_at >= ?", (cutoff,)
    ).fetchone()
    total_leads = row[0] if row else 0

    result["total_spend"] = total_spend
    result["total_leads"] = total_leads
    result["overall"] = _safe_div(total_spend, total_leads)

    # Per-source CPL — paid sources only (facebook, instagram, google)
    paid_sources = {"facebook", "facebook_test", "instagram", "google"}
    for source in paid_sources:
        src_leads = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE source = ? AND discovered_at >= ?",
            (source, cutoff),
        ).fetchone()[0]
        if src_leads > 0:
            # Estimate spend per source (proportional to leads from that source)
            src_fraction = src_leads / total_leads if total_leads > 0 else 0
            src_spend = total_spend * src_fraction
            result["by_source"][source] = {
                "leads": src_leads,
                "estimated_spend": round(src_spend, 2),
                "cpl": _safe_div(src_spend, src_leads),
            }

    # Direct CPL from ad_metrics (if Facebook-specific data available)
    if _table_exists(conn, "ad_metrics"):
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(spend), 0), COALESCE(SUM(leads), 0) "
                "FROM ad_metrics WHERE collected_at >= ?",
                (cutoff,),
            ).fetchone()
            fb_spend = float(row[0])
            fb_leads = int(row[1])
            if fb_leads > 0:
                result["by_source"]["facebook_ads"] = {
                    "leads": fb_leads,
                    "estimated_spend": fb_spend,
                    "cpl": _safe_div(fb_spend, fb_leads),
                }
        except sqlite3.OperationalError:
            pass

    conn.close()
    return result


def get_response_metrics(days: int = 30) -> dict[str, Any]:
    """Average time to first response, approval rate, follow-up effectiveness."""
    conn = _conn()
    cutoff = _days_ago(days)
    has_leads = _table_exists(conn, "leads")
    result: dict[str, Any] = {
        "avg_response_time_hours": 0.0,
        "median_response_time_hours": 0.0,
        "approval_rate": 0.0,
        "reply_rate": 0.0,
        "followup_effectiveness": 0.0,
    }

    # Average time from lead discovery to first outbound message
    response_times: list[float] = []
    if has_leads and _table_exists(conn, "lead_messages"):
        rows = conn.execute(
            "SELECT l.id, l.discovered_at, MIN(m.ts) AS first_msg "
            "FROM leads l "
            "JOIN lead_messages m ON m.lead_id = l.id AND m.direction = 'outbound' "
            "WHERE l.discovered_at >= ? "
            "GROUP BY l.id",
            (cutoff,),
        ).fetchall()
        for row in rows:
            try:
                discovered = datetime.strptime(row["discovered_at"], "%Y-%m-%d %H:%M:%S")
                first_msg = datetime.strptime(row["first_msg"], "%Y-%m-%d %H:%M:%S")
                diff_hours = (first_msg - discovered).total_seconds() / 3600
                if 0 <= diff_hours < 720:  # ignore outliers beyond 30 days
                    response_times.append(diff_hours)
            except (ValueError, TypeError):
                continue

    if response_times:
        result["avg_response_time_hours"] = round(sum(response_times) / len(response_times), 1)
        sorted_times = sorted(response_times)
        mid = len(sorted_times) // 2
        result["median_response_time_hours"] = round(sorted_times[mid], 1)

    # Approval rate (from message_approvals if exists)
    if _table_exists(conn, "message_approvals"):
        total_approvals = conn.execute(
            "SELECT COUNT(*) FROM message_approvals WHERE created_at >= ?", (cutoff,)
        ).fetchone()[0]
        approved = conn.execute(
            "SELECT COUNT(*) FROM message_approvals WHERE status = 'approved' AND created_at >= ?",
            (cutoff,),
        ).fetchone()[0]
        result["approval_rate"] = _safe_div(approved * 100, total_approvals) if total_approvals > 0 else 0.0

    # Reply rate — leads that replied out of total contacted
    if has_leads:
        total_contacted = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status NOT IN ('new', 'awaiting_approval') AND discovered_at >= ?",
            (cutoff,),
        ).fetchone()[0]
        total_replied = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status = 'replied' AND discovered_at >= ?",
            (cutoff,),
        ).fetchone()[0]
        result["reply_rate"] = _safe_div(total_replied * 100, total_contacted)

    # Follow-up effectiveness — leads that replied after follow-up
    if has_leads and _table_exists(conn, "lead_events"):
        followup_sent = conn.execute(
            "SELECT COUNT(DISTINCT lead_id) FROM lead_events "
            "WHERE event_type LIKE 'followup%' AND ts >= ?",
            (cutoff,),
        ).fetchone()[0]
        followup_replied = conn.execute(
            "SELECT COUNT(DISTINCT le.lead_id) FROM lead_events le "
            "JOIN leads l ON l.id = le.lead_id "
            "WHERE le.event_type LIKE 'followup%' AND le.ts >= ? "
            "AND l.status = 'replied'",
            (cutoff,),
        ).fetchone()[0]
        result["followup_effectiveness"] = _safe_div(followup_replied * 100, followup_sent)

    conn.close()
    return result


def get_channel_performance(days: int = 30) -> dict[str, Any]:
    """SMS vs email performance (approximated from reply rates)."""
    conn = _conn()
    cutoff = _days_ago(days)
    result: dict[str, Any] = {"sms": {}, "email": {}}

    if not _table_exists(conn, "lead_messages"):
        conn.close()
        return result

    for channel in ("sms", "email"):
        sent = conn.execute(
            "SELECT COUNT(*) FROM lead_messages "
            "WHERE channel = ? AND direction = 'outbound' AND status = 'sent' AND ts >= ?",
            (channel, cutoff),
        ).fetchone()[0]

        # Count replies that came back on this channel
        replies = conn.execute(
            "SELECT COUNT(*) FROM lead_messages "
            "WHERE channel = ? AND direction = 'inbound' AND ts >= ?",
            (channel, cutoff),
        ).fetchone()[0]

        failed = conn.execute(
            "SELECT COUNT(*) FROM lead_messages "
            "WHERE channel = ? AND direction = 'outbound' AND status = 'failed' AND ts >= ?",
            (channel, cutoff),
        ).fetchone()[0]

        result[channel] = {
            "sent": sent,
            "replies": replies,
            "failed": failed,
            "reply_rate": _safe_div(replies * 100, sent),
            "delivery_rate": _safe_div((sent - failed) * 100, sent) if sent > 0 else 0.0,
        }

    conn.close()
    return result


# ═════════════════════════════════════════════════════════════════════════════
# TREND ANALYSIS
# ═════════════════════════════════════════════════════════════════════════════

def get_lead_trend(days: int = 90) -> list[dict[str, Any]]:
    """Daily lead counts for charting."""
    conn = _conn()
    if not _table_exists(conn, "leads"):
        conn.close()
        return []
    rows = conn.execute(
        "SELECT date(discovered_at) AS day, COUNT(*) AS cnt "
        "FROM leads WHERE discovered_at >= ? "
        "GROUP BY day ORDER BY day ASC",
        (_days_ago(days),),
    ).fetchall()
    conn.close()
    return [{"date": row["day"], "count": row["cnt"]} for row in rows]


def get_revenue_trend(months: int = 6) -> list[dict[str, Any]]:
    """Monthly revenue for charting."""
    conn = _conn()
    result: list[dict[str, Any]] = []

    if not _table_exists(conn, "revenue_entries"):
        # Fall back to booking_confirmations
        if _table_exists(conn, "booking_confirmations"):
            rows = conn.execute(
                "SELECT strftime('%Y-%m', created_at) AS month, COALESCE(SUM(total_price), 0) AS total "
                "FROM booking_confirmations "
                "WHERE created_at >= date('now', ? || ' months') AND status != 'cancelled' "
                "GROUP BY month ORDER BY month ASC",
                (f"-{months}",),
            ).fetchall()
            result = [{"month": row["month"], "revenue": float(row["total"])} for row in rows]
        conn.close()
        return result

    rows = conn.execute(
        "SELECT strftime('%Y-%m', date) AS month, COALESCE(SUM(amount), 0) AS total "
        "FROM revenue_entries "
        "WHERE date >= date('now', ? || ' months') "
        "GROUP BY month ORDER BY month ASC",
        (f"-{months}",),
    ).fetchall()
    conn.close()
    return [{"month": row["month"], "revenue": float(row["total"])} for row in rows]


def get_source_trend(days: int = 30) -> dict[str, Any]:
    """Which sources are growing or declining vs previous period."""
    conn = _conn()
    if not _table_exists(conn, "leads"):
        conn.close()
        return {"trends": {}, "period_days": days}
    cutoff_current = _days_ago(days)
    cutoff_previous = _days_ago(days * 2)

    # Current period
    current_rows = conn.execute(
        "SELECT source, COUNT(*) AS cnt FROM leads "
        "WHERE discovered_at >= ? GROUP BY source",
        (cutoff_current,),
    ).fetchall()
    current = {(row["source"] or "unknown"): row["cnt"] for row in current_rows}

    # Previous period
    prev_rows = conn.execute(
        "SELECT source, COUNT(*) AS cnt FROM leads "
        "WHERE discovered_at >= ? AND discovered_at < ? GROUP BY source",
        (cutoff_previous, cutoff_current),
    ).fetchall()
    previous = {(row["source"] or "unknown"): row["cnt"] for row in prev_rows}

    conn.close()

    trends: dict[str, Any] = {}
    all_sources = set(list(current.keys()) + list(previous.keys()))
    for source in all_sources:
        cur = current.get(source, 0)
        prev = previous.get(source, 0)
        change = cur - prev
        pct_change = _safe_div(change * 100, prev) if prev > 0 else (100.0 if cur > 0 else 0.0)
        direction = "up" if change > 0 else ("down" if change < 0 else "flat")
        trends[source] = {
            "current": cur,
            "previous": prev,
            "change": change,
            "pct_change": pct_change,
            "direction": direction,
        }

    return {"trends": trends, "period_days": days}


def calculate_projected_revenue(days: int = 30) -> dict[str, Any]:
    """Project revenue based on current pipeline and historical conversion rate."""
    conn = _conn()
    result: dict[str, Any] = {
        "pipeline_value": 0.0,
        "projected_revenue": 0.0,
        "conversion_rate_used": 0.0,
        "leads_in_pipeline": 0,
    }

    # Active pipeline value from booking_confirmations
    if _table_exists(conn, "booking_confirmations"):
        row = conn.execute(
            "SELECT COALESCE(SUM(total_price), 0), COUNT(*) "
            "FROM booking_confirmations WHERE status = 'active'",
        ).fetchone()
        result["pipeline_value"] = float(row[0])
        result["leads_in_pipeline"] = row[1]

    # Historical conversion rate (booked / total leads)
    cutoff = _days_ago(days)
    total = 0
    booked = 0
    if _table_exists(conn, "leads"):
        total = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE discovered_at >= ?", (cutoff,)
        ).fetchone()[0]

        try:
            booked = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE booking_status IN ('booked', 'completed') "
                "AND discovered_at >= ?",
                (cutoff,),
            ).fetchone()[0]
        except sqlite3.OperationalError:
            # booking_status column may not exist
            booked = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE status = 'closed' AND discovered_at >= ?",
                (cutoff,),
            ).fetchone()[0]

    conv_rate = _safe_div(booked, total) if total > 0 else 0.0
    result["conversion_rate_used"] = round(conv_rate * 100, 1)

    # Active quotes — leads in quote_sent or deposit_pending
    active_quotes = 0
    avg_booking_value = 0.0
    if _table_exists(conn, "leads"):
        try:
            active_quotes = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE booking_status IN ('quote_sent', 'deposit_pending')",
            ).fetchone()[0]
        except sqlite3.OperationalError:
            pass

    # Average booking value
    if _table_exists(conn, "revenue_entries"):
        row = conn.execute(
            "SELECT AVG(amount) FROM revenue_entries WHERE type = 'booking' AND amount > 0",
        ).fetchone()
        avg_booking_value = float(row[0]) if row and row[0] else 0.0
    elif _table_exists(conn, "booking_confirmations"):
        row = conn.execute(
            "SELECT AVG(total_price) FROM booking_confirmations WHERE total_price > 0",
        ).fetchone()
        avg_booking_value = float(row[0]) if row and row[0] else 0.0

    # Projected = pipeline_value + (active_quotes * conv_rate * avg_booking)
    projected = result["pipeline_value"] + (active_quotes * conv_rate * avg_booking_value)
    result["projected_revenue"] = round(projected, 2)

    conn.close()
    return result


# ═════════════════════════════════════════════════════════════════════════════
# DASHBOARD DATA ENDPOINT
# ═════════════════════════════════════════════════════════════════════════════

def get_dashboard_data(days: int = 30) -> dict[str, Any]:
    """Returns comprehensive metrics dict for the web dashboard."""
    lead_m = get_lead_metrics(days)
    conv_m = get_conversion_metrics(days)
    rev_m = get_revenue_metrics(days)
    cpl_m = get_cpl_metrics(days)
    resp_m = get_response_metrics(days)
    channel_m = get_channel_performance(days)

    # Daily lead trend for sparkline
    lead_daily = get_lead_trend(days)

    # Monthly revenue trend
    rev_monthly = get_revenue_trend(6)

    # Top sources by lead count
    top_sources = sorted(
        lead_m["by_source"].items(),
        key=lambda x: x[1],
        reverse=True,
    )[:10]

    # Projection
    projection = calculate_projected_revenue(days)

    return {
        "summary": {
            "total_leads": lead_m["total"],
            "new_this_month": lead_m["new_leads"],
            "revenue_this_month": rev_m["total"],
            "conversion_rate": conv_m["conversion_rate"],
            "avg_response_hours": resp_m["avg_response_time_hours"],
            "pipeline_value": projection["pipeline_value"],
        },
        "leads_by_source": lead_m["by_source"],
        "leads_by_status": lead_m["by_status"],
        "leads_by_event_type": lead_m["by_event_type"],
        "revenue": {
            "total": rev_m["total"],
            "avg_booking": rev_m["avg_booking"],
            "by_source": rev_m["by_source"],
            "by_event_type": rev_m["by_event_type"],
            "by_month": rev_monthly,
            "mom_growth": rev_m["mom_growth"],
        },
        "funnel": {
            "leads": conv_m["total_leads"],
            "contacted": conv_m["contacted"],
            "quoted": conv_m["quoted"],
            "booked": conv_m["booked"],
            "completed": conv_m["completed"],
            "lost": conv_m["lost"],
        },
        "cpl": {
            "overall": cpl_m["overall"],
            "by_source": cpl_m["by_source"],
            "total_spend": cpl_m["total_spend"],
        },
        "channel_performance": channel_m,
        "trends": {
            "leads_daily": lead_daily,
            "revenue_monthly": rev_monthly,
        },
        "top_sources": [{"source": s, "count": c} for s, c in top_sources],
        "response_time_avg": resp_m["avg_response_time_hours"],
        "projection": projection,
        "period_days": days,
        "generated_at": _now_la().isoformat(),
    }


# ═════════════════════════════════════════════════════════════════════════════
# TELEGRAM REPORTS
# ═════════════════════════════════════════════════════════════════════════════

def format_dashboard_summary() -> str:
    """Quick overview for Telegram — concise one-screen summary."""
    try:
        data = get_dashboard_data(30)
    except Exception as e:
        return f"[Analytics] Error generating summary: {e}"

    s = data["summary"]
    f = data["funnel"]
    cpl = data["cpl"]

    lines = [
        "📊 *Zoar CRM Dashboard*",
        f"_Last 30 days_\n",
        f"👥 Total leads: *{_fmt_num(s['total_leads'])}*",
        f"🆕 New this month: *{_fmt_num(s['new_this_month'])}*",
        f"💰 Revenue: *{_fmt_money(s['revenue_this_month'])}*",
        f"📈 Conversion: *{_fmt_pct(s['conversion_rate'])}*",
        f"⏱ Avg response: *{s['avg_response_hours']:.1f}h*\n",
        "🔄 *Funnel*",
        f"  Leads → {f['leads']}",
        f"  Contacted → {f['contacted']}",
        f"  Quoted → {f['quoted']}",
        f"  Booked → {f['booked']}",
        f"  Completed → {f['completed']}\n",
    ]

    if cpl["overall"] > 0:
        lines.append(f"💵 CPL (overall): *{_fmt_money(cpl['overall'])}*")
        lines.append(f"📣 Ad spend: *{_fmt_money(cpl['total_spend'])}*\n")

    # Top 5 sources
    top = data["top_sources"][:5]
    if top:
        lines.append("🏆 *Top Sources*")
        for i, src in enumerate(top, 1):
            lines.append(f"  {i}. {src['source']}: {src['count']}")

    return "\n".join(lines)


def generate_weekly_report() -> str:
    """Comprehensive weekly summary for /stats — sent every Sunday 10 AM."""
    try:
        data = get_dashboard_data(7)
        data_30 = get_dashboard_data(30)
    except Exception as e:
        return f"[Analytics] Error generating weekly report: {e}"

    now = _now_la()
    s = data["summary"]
    f = data["funnel"]
    cpl = data["cpl"]
    ch = data.get("channel_performance", {})
    resp = data.get("response_time_avg", 0)

    lines = [
        f"📊 *Weekly Report — {now.strftime('%b %d, %Y')}*",
        f"_Week ending {now.strftime('%A, %B %-d')}_\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        "📋 *LEADS*",
        f"  New this week: *{_fmt_num(s['new_this_month'])}*",
        f"  Total in system: *{_fmt_num(s['total_leads'])}*",
    ]

    # Source breakdown
    src = data.get("leads_by_source", {})
    if src:
        lines.append("\n  _By source:_")
        for source, count in sorted(src.items(), key=lambda x: x[1], reverse=True)[:8]:
            lines.append(f"    {source}: {count}")

    # Event type breakdown
    evt = data.get("leads_by_event_type", {})
    if evt:
        lines.append("\n  _By event type:_")
        for etype, count in sorted(evt.items(), key=lambda x: x[1], reverse=True)[:6]:
            lines.append(f"    {etype}: {count}")

    lines.extend([
        "\n━━━━━━━━━━━━━━━━━━━━━━━━",
        "🔄 *FUNNEL (this week)*",
        f"  Leads:     {f['leads']}",
        f"  Contacted: {f['contacted']}",
        f"  Quoted:    {f['quoted']}",
        f"  Booked:    {f['booked']}",
        f"  Completed: {f['completed']}",
        f"  Lost:      {f['lost']}",
        f"  Conv rate: *{_fmt_pct(s['conversion_rate'])}*",
    ])

    lines.extend([
        "\n━━━━━━━━━━━━━━━━━━━━━━━━",
        "💰 *REVENUE*",
        f"  This week: *{_fmt_money(s['revenue_this_month'])}*",
        f"  30-day total: *{_fmt_money(data_30['summary']['revenue_this_month'])}*",
    ])

    rev_data = data.get("revenue", {})
    if rev_data.get("avg_booking", 0) > 0:
        lines.append(f"  Avg booking: *{_fmt_money(rev_data['avg_booking'])}*")
    if rev_data.get("mom_growth", 0) != 0:
        direction = "📈" if rev_data["mom_growth"] > 0 else "📉"
        lines.append(f"  MoM growth: {direction} *{rev_data['mom_growth']:+.1f}%*")

    # Ad performance
    if cpl["total_spend"] > 0:
        lines.extend([
            "\n━━━━━━━━━━━━━━━━━━━━━━━━",
            "📣 *AD PERFORMANCE*",
            f"  Spend: *{_fmt_money(cpl['total_spend'])}*",
            f"  CPL: *{_fmt_money(cpl['overall'])}*",
        ])
        for src_name, src_data in cpl.get("by_source", {}).items():
            lines.append(f"    {src_name}: {_fmt_money(src_data['cpl'])} ({src_data['leads']} leads)")

    # Channel performance
    if ch:
        lines.extend([
            "\n━━━━━━━━━━━━━━━━━━━━━━━━",
            "📱 *CHANNEL PERFORMANCE*",
        ])
        for channel_name in ("sms", "email"):
            cd = ch.get(channel_name, {})
            if cd.get("sent", 0) > 0:
                lines.append(
                    f"  {channel_name.upper()}: {cd['sent']} sent, "
                    f"{cd['replies']} replies ({_fmt_pct(cd['reply_rate'])} reply rate)"
                )

    # Response time
    if resp > 0:
        lines.extend([
            "\n━━━━━━━━━━━━━━━━━━━━━━━━",
            "⏱ *RESPONSE TIME*",
            f"  Avg: *{resp:.1f} hours*",
        ])

    # Source trends
    source_trends = get_source_trend(7)
    growing = []
    declining = []
    for src_name, trend in source_trends.get("trends", {}).items():
        if trend["direction"] == "up" and trend["change"] >= 2:
            growing.append((src_name, trend["change"]))
        elif trend["direction"] == "down" and abs(trend["change"]) >= 2:
            declining.append((src_name, trend["change"]))

    if growing or declining:
        lines.append("\n━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("📊 *SOURCE TRENDS*")
        for src_name, change in sorted(growing, key=lambda x: x[1], reverse=True)[:3]:
            lines.append(f"  📈 {src_name}: +{change}")
        for src_name, change in sorted(declining, key=lambda x: x[1])[:3]:
            lines.append(f"  📉 {src_name}: {change}")

    # Projection
    proj = data.get("projection", {})
    if proj.get("pipeline_value", 0) > 0:
        lines.extend([
            "\n━━━━━━━━━━━━━━━━━━━━━━━━",
            "🔮 *PROJECTION*",
            f"  Pipeline value: *{_fmt_money(proj['pipeline_value'])}*",
            f"  Projected revenue: *{_fmt_money(proj['projected_revenue'])}*",
        ])

    return "\n".join(lines)


def generate_monthly_report() -> str:
    """Monthly business review — comprehensive 30-day analysis."""
    try:
        data = get_dashboard_data(30)
        prev_data = get_dashboard_data(60)
    except Exception as e:
        return f"[Analytics] Error generating monthly report: {e}"

    now = _now_la()
    s = data["summary"]
    f = data["funnel"]

    lines = [
        f"📊 *Monthly Business Review — {now.strftime('%B %Y')}*\n",
        "═══════════════════════════════",
        "",
        "📋 *LEAD SUMMARY*",
        f"  Total leads (all time): *{_fmt_num(s['total_leads'])}*",
        f"  New this month: *{_fmt_num(s['new_this_month'])}*",
        f"  Conversion rate: *{_fmt_pct(s['conversion_rate'])}*\n",
        "🔄 *CONVERSION FUNNEL*",
        f"  {f['leads']} leads",
        f"    → {f['contacted']} contacted ({_fmt_pct(_safe_div(f['contacted'] * 100, f['leads']))})",
        f"    → {f['quoted']} quoted ({_fmt_pct(_safe_div(f['quoted'] * 100, f['leads']))})",
        f"    → {f['booked']} booked ({_fmt_pct(_safe_div(f['booked'] * 100, f['leads']))})",
        f"    → {f['completed']} completed",
        f"    ✗ {f['lost']} lost\n",
    ]

    # Revenue section
    rev = data.get("revenue", {})
    lines.extend([
        "💰 *REVENUE*",
        f"  Total: *{_fmt_money(rev.get('total', 0))}*",
        f"  Avg booking: *{_fmt_money(rev.get('avg_booking', 0))}*",
    ])
    if rev.get("mom_growth", 0) != 0:
        lines.append(f"  MoM growth: *{rev['mom_growth']:+.1f}%*")

    # Revenue by source
    rev_src = rev.get("by_source", {})
    if rev_src:
        lines.append("\n  _Revenue by source:_")
        for src, amt in sorted(rev_src.items(), key=lambda x: x[1], reverse=True)[:6]:
            lines.append(f"    {src}: {_fmt_money(amt)}")

    # Revenue by event type
    rev_evt = rev.get("by_event_type", {})
    if rev_evt:
        lines.append("\n  _Revenue by event type:_")
        for evt, amt in sorted(rev_evt.items(), key=lambda x: x[1], reverse=True)[:6]:
            lines.append(f"    {evt}: {_fmt_money(amt)}")

    # Lead sources detailed
    src = data.get("leads_by_source", {})
    if src:
        lines.extend(["\n═══════════════════════════════", "", "🏆 *LEAD SOURCES*"])
        total_period_leads = sum(src.values())
        for source, count in sorted(src.items(), key=lambda x: x[1], reverse=True):
            pct = _safe_div(count * 100, total_period_leads)
            bar = "█" * max(1, int(pct / 5))
            lines.append(f"  {bar} {source}: {count} ({_fmt_pct(pct)})")

    # CPL analysis
    cpl = data.get("cpl", {})
    if cpl.get("total_spend", 0) > 0:
        lines.extend([
            "\n═══════════════════════════════",
            "",
            "📣 *AD SPEND ANALYSIS*",
            f"  Total ad spend: *{_fmt_money(cpl['total_spend'])}*",
            f"  Overall CPL: *{_fmt_money(cpl['overall'])}*",
            f"  ROI: *{_fmt_pct(_safe_div(rev.get('total', 0) * 100, cpl['total_spend']))}*",
        ])

    # Recommendations
    lines.extend([
        "\n═══════════════════════════════",
        "",
        "💡 *KEY INSIGHTS*",
    ])

    # Auto-generate insights
    if s["conversion_rate"] < 10:
        lines.append("  ⚠️ Conversion rate below 10% — review follow-up process")
    elif s["conversion_rate"] > 25:
        lines.append("  ✅ Strong conversion rate — keep current process")

    if data.get("response_time_avg", 0) > 4:
        lines.append(f"  ⚠️ Avg response time {data['response_time_avg']:.1f}h — aim for under 2h")
    elif data.get("response_time_avg", 0) > 0:
        lines.append(f"  ✅ Response time {data['response_time_avg']:.1f}h — good speed")

    top = data.get("top_sources", [])
    if top:
        lines.append(f"  📊 Top source: {top[0]['source']} ({top[0]['count']} leads)")

    if cpl.get("overall", 0) > 8:
        lines.append(f"  ⚠️ CPL at {_fmt_money(cpl['overall'])} — consider optimizing ads")
    elif cpl.get("overall", 0) > 0:
        lines.append(f"  ✅ CPL at {_fmt_money(cpl['overall'])} — healthy cost per lead")

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# TELEGRAM COMMAND HANDLERS
# ═════════════════════════════════════════════════════════════════════════════

def handle_analytics_command(text: str) -> str:
    """Handle /analytics command — shows dashboard summary."""
    parts = text.strip().split()
    days = 30
    if len(parts) > 1:
        try:
            days = int(parts[1])
            days = max(1, min(days, 365))
        except ValueError:
            pass
    try:
        data = get_dashboard_data(days)
    except Exception as e:
        return f"❌ Analytics error: {e}"

    return format_dashboard_summary()


def handle_revenue_command(text: str) -> str:
    """Handle /revenue command — shows revenue metrics."""
    parts = text.strip().split()
    days = 30
    if len(parts) > 1:
        try:
            days = int(parts[1])
            days = max(1, min(days, 365))
        except ValueError:
            pass

    try:
        rev = get_revenue_metrics(days)
        breakdown = get_revenue_breakdown(days)
        proj = calculate_projected_revenue(days)
    except Exception as e:
        return f"❌ Revenue error: {e}"

    lines = [
        f"💰 *Revenue Report* ({days} days)\n",
        f"Total revenue: *{_fmt_money(rev['total'])}*",
        f"Avg booking value: *{_fmt_money(rev['avg_booking'])}*",
    ]

    if rev["mom_growth"] != 0:
        direction = "📈" if rev["mom_growth"] > 0 else "📉"
        lines.append(f"MoM growth: {direction} *{rev['mom_growth']:+.1f}%*")

    # By source
    src = rev.get("by_source", {})
    if src:
        lines.append("\n_By source:_")
        for source, amt in sorted(src.items(), key=lambda x: x[1], reverse=True)[:6]:
            lines.append(f"  {source}: {_fmt_money(amt)}")

    # By event type
    evt = rev.get("by_event_type", {})
    if evt:
        lines.append("\n_By event type:_")
        for etype, amt in sorted(evt.items(), key=lambda x: x[1], reverse=True)[:6]:
            lines.append(f"  {etype}: {_fmt_money(amt)}")

    # Monthly trend
    monthly = breakdown.get("by_month", [])
    if monthly:
        lines.append("\n_Monthly trend:_")
        for m in monthly[-4:]:
            lines.append(f"  {m['month']}: {_fmt_money(m['total'])}")

    # Projection
    if proj["pipeline_value"] > 0:
        lines.extend([
            "\n🔮 _Projection:_",
            f"  Pipeline value: {_fmt_money(proj['pipeline_value'])}",
            f"  Projected: {_fmt_money(proj['projected_revenue'])}",
        ])

    return "\n".join(lines)


def handle_funnel_command() -> str:
    """Handle /funnel command — shows conversion funnel."""
    try:
        conv = get_conversion_metrics(30)
        conv_7 = get_conversion_metrics(7)
    except Exception as e:
        return f"❌ Funnel error: {e}"

    f = conv
    f7 = conv_7

    # Build ASCII funnel
    max_width = 28
    stages = [
        ("Leads", f["total_leads"]),
        ("Contacted", f["contacted"]),
        ("Quoted", f["quoted"]),
        ("Booked", f["booked"]),
        ("Completed", f["completed"]),
    ]

    lines = ["🔄 *Conversion Funnel*\n"]
    lines.append("_Last 30 days:_\n")

    max_val = max(s[1] for s in stages) if stages else 1
    for label, count in stages:
        bar_width = max(1, int(count / max(max_val, 1) * max_width))
        bar = "█" * bar_width
        drop = ""
        if label != "Leads" and f["total_leads"] > 0:
            pct = _safe_div(count * 100, f["total_leads"])
            drop = f" ({_fmt_pct(pct)})"
        lines.append(f"  {bar} {label}: {count}{drop}")

    lines.append(f"\n  Conversion rate: *{_fmt_pct(f['conversion_rate'])}*")
    lines.append(f"  Lost: {f['lost']}")

    # 7-day comparison
    lines.extend([
        "\n_Last 7 days:_",
        f"  Leads: {f7['total_leads']} | Contacted: {f7['contacted']}",
        f"  Quoted: {f7['quoted']} | Booked: {f7['booked']}",
        f"  Conversion: {_fmt_pct(f7['conversion_rate'])}",
    ])

    # Stage drop-off analysis
    if f["total_leads"] > 0:
        lines.extend(["\n_Drop-off analysis:_"])
        prev_count = f["total_leads"]
        for label, count in stages[1:]:
            if prev_count > 0:
                drop_rate = _safe_div((prev_count - count) * 100, prev_count)
                if drop_rate > 50:
                    lines.append(f"  ⚠️ {label}: {_fmt_pct(drop_rate)} drop-off")
                else:
                    lines.append(f"  ✅ {label}: {_fmt_pct(drop_rate)} drop-off")
            prev_count = count

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# DAILY SNAPSHOT SCHEDULER
# ═════════════════════════════════════════════════════════════════════════════

def take_daily_snapshot() -> None:
    """Save current metrics to analytics_snapshots. Run at midnight."""
    try:
        today = _now_la().strftime("%Y-%m-%d")
        lead_m = get_lead_metrics(1)
        conv_m = get_conversion_metrics(30)
        rev_m = get_revenue_metrics(30)
        cpl_m = get_cpl_metrics(30)

        conn = _conn()
        # Upsert — replace if snapshot for today already exists
        conn.execute(
            "INSERT OR REPLACE INTO analytics_snapshots "
            "(date, total_leads, new_leads, converted_leads, revenue, ad_spend, cpl, conversion_rate) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                today,
                lead_m["total"],
                lead_m["new_leads"],
                conv_m["booked"] + conv_m["completed"],
                rev_m["total"],
                cpl_m["total_spend"],
                cpl_m["overall"],
                conv_m["conversion_rate"],
            ),
        )
        conn.commit()
        conn.close()
        print(f"[Analytics] Daily snapshot saved for {today}")
    except Exception as e:
        print(f"[Analytics] Snapshot error: {e}")
        traceback.print_exc()


async def run_analytics_scheduler(send_fn: Optional[Any] = None) -> None:
    """
    Async background loop:
      - Daily snapshot at midnight PT
      - Weekly report at Sunday 10 AM PT (sent via send_fn)

    send_fn: async def(message: str) → send to Telegram
    """
    print("[Analytics] Scheduler started")
    last_snapshot_date = ""
    last_weekly_date = ""

    while True:
        try:
            now = _now_la()
            today = now.strftime("%Y-%m-%d")
            weekday = now.weekday()  # 0=Mon, 6=Sun
            hour = now.hour

            # Daily snapshot at midnight (hour 0)
            if hour == 0 and today != last_snapshot_date:
                take_daily_snapshot()
                last_snapshot_date = today

            # Weekly report on Sundays at 10 AM PT
            if weekday == 6 and hour == 10 and today != last_weekly_date:
                report = generate_weekly_report()
                if send_fn:
                    try:
                        await send_fn(report)
                    except Exception as e:
                        print(f"[Analytics] Failed to send weekly report: {e}")
                last_weekly_date = today

        except Exception as e:
            print(f"[Analytics] Scheduler error: {e}")
            traceback.print_exc()

        # Check every 5 minutes
        await asyncio.sleep(300)
