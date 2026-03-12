"""
Analytics Dashboard API — aggregates data across all tables for the main dashboard.

Usage in server.py:
    from core.analytics_api import register_analytics_routes
    register_analytics_routes(app)
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

log = logging.getLogger("analytics_api")
DB_PATH = Path.home() / ".nexus" / "memory.db"


def _conn():
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def register_analytics_routes(app: FastAPI):
    """Register analytics dashboard endpoint."""

    @app.get("/api/analytics/dashboard-v1")
    async def api_analytics_dashboard_v1():
        conn = _conn()
        today = datetime.now().strftime("%Y-%m-%d")
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        # ── Leads ──
        leads_total = conn.execute("SELECT COUNT(*) as cnt FROM leads").fetchone()["cnt"]
        leads_today = conn.execute(
            "SELECT COUNT(*) as cnt FROM leads WHERE created_at >= ?", (today,)
        ).fetchone()["cnt"]
        leads_week = conn.execute(
            "SELECT COUNT(*) as cnt FROM leads WHERE created_at >= ?", (week_ago,)
        ).fetchone()["cnt"]

        lead_source_rows = conn.execute(
            "SELECT COALESCE(utm_source, 'unknown') as src, COUNT(*) as cnt FROM leads GROUP BY src"
        ).fetchall()
        leads_by_source = {r["src"]: r["cnt"] for r in lead_source_rows}

        # ── Emails ──
        emails_sent_today = 0
        emails_bounced = 0
        emails_replied = 0
        try:
            emails_sent_today = conn.execute(
                "SELECT COUNT(*) as cnt FROM email_queue WHERE sent_at >= ? AND status = 'sent'", (today,)
            ).fetchone()["cnt"]
            emails_bounced = conn.execute(
                "SELECT COUNT(*) as cnt FROM email_queue WHERE status = 'failed'"
            ).fetchone()["cnt"]
        except Exception:
            pass

        try:
            emails_replied = conn.execute(
                "SELECT COUNT(*) as cnt FROM lead_conversation_messages "
                "WHERE direction = 'inbound' AND channel = 'email' AND message_timestamp >= ?",
                (week_ago,)
            ).fetchone()["cnt"]
        except Exception:
            pass

        # ── Bookings ──
        bookings_total = conn.execute("SELECT COUNT(*) as cnt FROM bookings").fetchone()["cnt"]
        bookings_month = conn.execute(
            "SELECT COUNT(*) as cnt FROM bookings WHERE event_date LIKE ?",
            (datetime.now().strftime("%Y-%m") + "%",)
        ).fetchone()["cnt"]
        revenue = conn.execute(
            "SELECT COALESCE(SUM(total_paid), 0) as rev FROM bookings"
        ).fetchone()["rev"]
        pipeline_value = conn.execute(
            "SELECT COALESCE(SUM(quoted_price), 0) as val FROM bookings "
            "WHERE status NOT IN ('cancelled', 'completed')"
        ).fetchone()["val"]

        # ── Referral partners ──
        referrals_total = conn.execute("SELECT COUNT(*) as cnt FROM referral_leads").fetchone()["cnt"]
        active_partners = conn.execute(
            "SELECT COUNT(*) as cnt FROM referral_leads WHERE outreach_status = 'active_partner'"
        ).fetchone()["cnt"]

        # ── Vendors ──
        vendors_total = conn.execute("SELECT COUNT(*) as cnt FROM vendors").fetchone()["cnt"]
        vendors_emailed = conn.execute(
            "SELECT COUNT(*) as cnt FROM vendors WHERE outreach_status = 'emailed'"
        ).fetchone()["cnt"]

        # ── Conversations ──
        convs_active = conn.execute(
            "SELECT COUNT(*) as cnt FROM lead_conversations WHERE status = 'active'"
        ).fetchone()["cnt"]
        convs_follow_up = conn.execute(
            "SELECT COUNT(*) as cnt FROM lead_conversations WHERE needs_follow_up = 1"
        ).fetchone()["cnt"]

        # ── Event leads ──
        events_total = conn.execute("SELECT COUNT(*) as cnt FROM event_leads").fetchone()["cnt"]
        events_upcoming = conn.execute(
            "SELECT COUNT(*) as cnt FROM event_leads WHERE event_date >= date('now')"
        ).fetchone()["cnt"]

        # ── Ad spend (from pixel_events or ad tables if available) ──
        ad_spend = 0
        ad_leads = 0
        try:
            ad_rows = conn.execute(
                "SELECT COALESCE(SUM(spend), 0) as spend, COUNT(*) as leads "
                "FROM leads WHERE utm_source = 'facebook' AND created_at >= ?",
                (today,)
            ).fetchone()
            ad_leads = ad_rows["leads"] if ad_rows else 0
        except Exception:
            pass

        conn.close()

        return JSONResponse({
            "leads": {
                "today": leads_today,
                "this_week": leads_week,
                "total": leads_total,
                "by_source": leads_by_source,
            },
            "emails": {
                "sent_today": emails_sent_today,
                "bounced": emails_bounced,
                "replied_this_week": emails_replied,
            },
            "bookings": {
                "total": bookings_total,
                "this_month": bookings_month,
                "revenue": revenue,
                "pipeline_value": pipeline_value,
            },
            "referrals": {
                "total_partners": referrals_total,
                "active_partners": active_partners,
            },
            "vendors": {
                "total": vendors_total,
                "emailed": vendors_emailed,
            },
            "conversations": {
                "active": convs_active,
                "needs_follow_up": convs_follow_up,
            },
            "events": {
                "total": events_total,
                "upcoming": events_upcoming,
            },
            "ads": {
                "spend_today": ad_spend,
                "leads_today": ad_leads,
                "cpl": round(ad_spend / ad_leads, 2) if ad_leads > 0 else 0,
            },
        })
