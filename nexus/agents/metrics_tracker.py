"""
Metrics Tracker — Tracks every marketing metric Kai cares about.

Collects and computes:
- CPL (Cost Per Lead)
- CPC (Cost Per Click)
- CTR (Click-Through Rate)
- CPM (Cost Per Mille / 1000 impressions)
- Hook Rate (first 3 seconds of video)
- Thumbstop Rate
- Video Completion Rate
- Frequency (avg times a user sees the ad)
- Landing Page View Rate
- Lead Form Completion Rate
- Booking Conversion Rate
- Revenue Efficiency (revenue per ad dollar)

Data sources:
- Facebook Pixel events (via Conversions API or webhook)
- CRM database (leads, bookings, revenue)
- Agent activity (content performance, engagement)
- Website analytics (form submissions, page views)

Runs as part of the analyst cycle or on-demand.
"""
import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path

from agents.shared_brain import SharedBrain

DB_PATH = Path.home() / ".nexus" / "memory.db"


class MetricsTracker:
    """Collects and computes all marketing metrics."""

    def __init__(self):
        self.brain = SharedBrain("metrics_tracker")
        self._init_tables()

    def _init_tables(self):
        conn = sqlite3.connect(str(DB_PATH))
        conn.executescript("""
            -- Ad spend and performance data (manual or API import)
            CREATE TABLE IF NOT EXISTS ad_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT DEFAULT (datetime('now')),
                platform TEXT,        -- 'facebook', 'instagram', 'google'
                campaign_id TEXT,
                campaign_name TEXT,
                ad_set TEXT,
                impressions INTEGER DEFAULT 0,
                clicks INTEGER DEFAULT 0,
                spend REAL DEFAULT 0,
                leads INTEGER DEFAULT 0,
                reach INTEGER DEFAULT 0,
                video_views INTEGER DEFAULT 0,
                video_completions INTEGER DEFAULT 0,
                landing_page_views INTEGER DEFAULT 0,
                form_starts INTEGER DEFAULT 0,
                form_completions INTEGER DEFAULT 0,
                data_json TEXT DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_ad_perf_ts ON ad_performance(ts DESC);
            CREATE INDEX IF NOT EXISTS idx_ad_perf_campaign ON ad_performance(campaign_id);

            -- Website events (form submissions, page views, etc.)
            CREATE TABLE IF NOT EXISTS website_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT DEFAULT (datetime('now')),
                event_type TEXT,     -- 'page_view', 'form_start', 'form_submit', 'button_click', 'scroll_depth'
                page_url TEXT,
                source TEXT,         -- 'facebook', 'instagram', 'google', 'direct', 'organic'
                utm_source TEXT,
                utm_medium TEXT,
                utm_campaign TEXT,
                utm_content TEXT,
                data_json TEXT DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_web_events_ts ON website_events(ts DESC);
            CREATE INDEX IF NOT EXISTS idx_web_events_type ON website_events(event_type);

            -- Pixel events (Facebook Conversions API or webhook)
            CREATE TABLE IF NOT EXISTS pixel_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT DEFAULT (datetime('now')),
                pixel_id TEXT,
                event_name TEXT,     -- 'PageView', 'Lead', 'ViewContent', 'Contact', 'Schedule'
                source_url TEXT,
                user_data_json TEXT DEFAULT '{}',
                custom_data_json TEXT DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_pixel_ts ON pixel_events(ts DESC);
            CREATE INDEX IF NOT EXISTS idx_pixel_event ON pixel_events(event_name);
        """)
        conn.commit()
        conn.close()

    # ── Data Collection ────────────────────────────────────────────────────

    def record_ad_performance(self, platform: str, campaign_name: str,
                              impressions: int = 0, clicks: int = 0,
                              spend: float = 0, leads: int = 0,
                              campaign_id: str = "", **kwargs):
        """Record ad performance data (can be called by API or manual import)."""
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("""
            INSERT INTO ad_performance
            (platform, campaign_id, campaign_name, impressions, clicks, spend,
             leads, reach, video_views, video_completions, landing_page_views,
             form_starts, form_completions, data_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            platform, campaign_id, campaign_name,
            impressions, clicks, spend, leads,
            kwargs.get("reach", 0),
            kwargs.get("video_views", 0),
            kwargs.get("video_completions", 0),
            kwargs.get("landing_page_views", 0),
            kwargs.get("form_starts", 0),
            kwargs.get("form_completions", 0),
            json.dumps(kwargs),
        ))
        conn.commit()
        conn.close()

    def record_website_event(self, event_type: str, page_url: str = "",
                             source: str = "", utm_source: str = "",
                             utm_medium: str = "", utm_campaign: str = "",
                             utm_content: str = "", data: dict = None):
        """Record a website event (form submission, page view, etc.)."""
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("""
            INSERT INTO website_events
            (event_type, page_url, source, utm_source, utm_medium, utm_campaign, utm_content, data_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (event_type, page_url, source, utm_source, utm_medium,
              utm_campaign, utm_content, json.dumps(data or {})))
        conn.commit()
        conn.close()

    def record_pixel_event(self, event_name: str, source_url: str = "",
                           pixel_id: str = "1087553350137807",
                           user_data: dict = None, custom_data: dict = None):
        """Record a Facebook pixel event."""
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("""
            INSERT INTO pixel_events (pixel_id, event_name, source_url, user_data_json, custom_data_json)
            VALUES (?, ?, ?, ?, ?)
        """, (pixel_id, event_name, source_url,
              json.dumps(user_data or {}), json.dumps(custom_data or {})))
        conn.commit()
        conn.close()

    # ── Metrics Computation ────────────────────────────────────────────────

    def compute_all_metrics(self, days: int = 7) -> dict:
        """Compute all marketing metrics for the given period."""
        since = (datetime.now() - timedelta(days=days)).isoformat()
        since_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

        # Aggregate ad performance
        ad_data = conn.execute("""
            SELECT
                SUM(impressions) as total_impressions,
                SUM(clicks) as total_clicks,
                SUM(spend) as total_spend,
                SUM(leads) as total_ad_leads,
                SUM(reach) as total_reach,
                SUM(video_views) as total_video_views,
                SUM(video_completions) as total_video_completions,
                SUM(landing_page_views) as total_lpv,
                SUM(form_starts) as total_form_starts,
                SUM(form_completions) as total_form_completions
            FROM ad_performance WHERE ts >= ?
        """, (since,)).fetchone()

        # CRM data
        total_leads = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE date_added >= ?", (since,)
        ).fetchone()[0]
        booked = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE booking_status = 'booked' AND date_added >= ?", (since,)
        ).fetchone()[0]
        total_all_leads = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        total_booked = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE booking_status = 'booked'"
        ).fetchone()[0]

        # Website events
        page_views = conn.execute(
            "SELECT COUNT(*) FROM website_events WHERE event_type = 'page_view' AND ts >= ?", (since,)
        ).fetchone()[0]
        form_submits = conn.execute(
            "SELECT COUNT(*) FROM website_events WHERE event_type = 'form_submit' AND ts >= ?", (since,)
        ).fetchone()[0]

        # Pixel events
        pixel_leads = conn.execute(
            "SELECT COUNT(*) FROM pixel_events WHERE event_name = 'Lead' AND ts >= ?", (since,)
        ).fetchone()[0]
        pixel_page_views = conn.execute(
            "SELECT COUNT(*) FROM pixel_events WHERE event_name = 'PageView' AND ts >= ?", (since,)
        ).fetchone()[0]

        # Safe division helper
        def safe_div(a, b, mult=1):
            return round((a / b) * mult, 4) if b and b > 0 else 0

        # Extract ad aggregates from ad_performance first, then fall back to
        # ad_metrics (populated by core/ad_monitor.py) when ad_performance is sparse.
        impressions = (ad_data["total_impressions"] or 0) if ad_data else 0
        clicks = (ad_data["total_clicks"] or 0) if ad_data else 0
        spend = (ad_data["total_spend"] or 0) if ad_data else 0
        ad_leads = (ad_data["total_ad_leads"] or 0) if ad_data else 0
        reach = (ad_data["total_reach"] or 0) if ad_data else 0
        video_views = (ad_data["total_video_views"] or 0) if ad_data else 0
        video_completions = (ad_data["total_video_completions"] or 0) if ad_data else 0
        lpv = (ad_data["total_lpv"] or 0) if ad_data else 0
        form_starts = (ad_data["total_form_starts"] or 0) if ad_data else 0
        form_completions = (ad_data["total_form_completions"] or 0) if ad_data else 0

        if spend <= 0 and impressions <= 0:
            try:
                fallback = conn.execute(
                    """
                    WITH latest AS (
                        SELECT
                            date,
                            COALESCE(NULLIF(campaign_id, ''), campaign_name) AS campaign_key,
                            MAX(collected_at) AS max_collected
                        FROM ad_metrics
                        WHERE date >= ?
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
                    SELECT
                        COALESCE(SUM(spend), 0) AS spend,
                        COALESCE(SUM(impressions), 0) AS impressions,
                        COALESCE(SUM(clicks), 0) AS clicks,
                        COALESCE(SUM(reach), 0) AS reach,
                        COALESCE(SUM(leads), 0) AS leads
                    FROM dedup
                    """,
                    (since_date,),
                ).fetchone()
                if fallback:
                    spend = float(fallback["spend"] or 0)
                    impressions = int(fallback["impressions"] or 0)
                    clicks = int(fallback["clicks"] or 0)
                    reach = int(fallback["reach"] or 0)
                    ad_leads = int(fallback["leads"] or 0)
            except Exception:
                pass

        conn.close()

        # Compute metrics
        metrics = {
            "period_days": days,
            "period_since": since,

            # Ad metrics
            "total_spend": round(spend, 2),
            "total_impressions": impressions,
            "total_clicks": clicks,
            "total_reach": reach,

            # Cost metrics
            "cpl": safe_div(spend, max(ad_leads, total_leads, 1)),  # Cost Per Lead
            "cpc": safe_div(spend, clicks),  # Cost Per Click
            "cpm": safe_div(spend, impressions, 1000),  # Cost Per Mille

            # Rate metrics
            "ctr": safe_div(clicks, impressions, 100),  # Click-Through Rate %
            "landing_page_view_rate": safe_div(lpv, clicks, 100),  # LP View Rate %
            "form_start_rate": safe_div(form_starts, lpv, 100),  # Form Start Rate %
            "lead_form_completion_rate": safe_div(form_completions, form_starts, 100),  # Form Completion %

            # Video metrics
            "video_views": video_views,
            "video_completions": video_completions,
            "video_completion_rate": safe_div(video_completions, video_views, 100),
            "hook_rate": safe_div(video_views, impressions, 100),  # ~thumbstop rate
            "thumbstop_rate": safe_div(video_views, impressions, 100),

            # Frequency
            "frequency": safe_div(impressions, reach),

            # Conversion metrics
            "total_leads_period": total_leads,
            "total_leads_all_time": total_all_leads,
            "booked_period": booked,
            "booked_all_time": total_booked,
            "booking_conversion_rate": safe_div(total_booked, total_all_leads, 100),

            # Revenue metrics (at $1,100 standard rate)
            "estimated_revenue": total_booked * 1100,
            "revenue_efficiency": safe_div(total_booked * 1100, max(spend, 1)),  # Revenue per ad dollar

            # Website metrics
            "website_page_views": page_views + pixel_page_views,
            "website_form_submits": form_submits,
            "pixel_leads": pixel_leads,

            # Lead quality
            "lead_efficiency": safe_div(booked, total_leads, 100) if total_leads else 0,
        }

        return metrics

    def compute_campaign_metrics(self, days: int = 7) -> list:
        """Compute metrics broken down by campaign."""
        since = (datetime.now() - timedelta(days=days)).isoformat()
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

        campaigns = conn.execute("""
            SELECT campaign_name,
                SUM(impressions) as impressions,
                SUM(clicks) as clicks,
                SUM(spend) as spend,
                SUM(leads) as leads,
                SUM(reach) as reach,
                SUM(video_views) as video_views,
                SUM(video_completions) as video_completions,
                SUM(landing_page_views) as lpv
            FROM ad_performance
            WHERE ts >= ?
            GROUP BY campaign_name
            ORDER BY SUM(spend) DESC
        """, (since,)).fetchall()

        conn.close()

        def safe_div(a, b, mult=1):
            return round((a / b) * mult, 4) if b and b > 0 else 0

        results = []
        for c in campaigns:
            results.append({
                "campaign": c["campaign_name"],
                "spend": round(c["spend"] or 0, 2),
                "impressions": c["impressions"] or 0,
                "clicks": c["clicks"] or 0,
                "leads": c["leads"] or 0,
                "ctr": safe_div(c["clicks"] or 0, c["impressions"] or 0, 100),
                "cpc": safe_div(c["spend"] or 0, c["clicks"] or 0),
                "cpl": safe_div(c["spend"] or 0, c["leads"] or 0),
                "cpm": safe_div(c["spend"] or 0, c["impressions"] or 0, 1000),
                "video_completion_rate": safe_div(
                    c["video_completions"] or 0, c["video_views"] or 0, 100
                ),
            })

        return results

    def write_metrics_to_brain(self, metrics: dict = None):
        """Write computed metrics to the shared brain for all agents to see."""
        if metrics is None:
            metrics = self.compute_all_metrics()

        # Write key metrics
        key_metrics = [
            ("CPL", metrics.get("cpl", 0), "ads"),
            ("CPC", metrics.get("cpc", 0), "ads"),
            ("CTR", metrics.get("ctr", 0), "ads"),
            ("CPM", metrics.get("cpm", 0), "ads"),
            ("booking_conversion_rate", metrics.get("booking_conversion_rate", 0), "conversion"),
            ("total_leads", metrics.get("total_leads_all_time", 0), "pipeline"),
            ("total_booked", metrics.get("booked_all_time", 0), "pipeline"),
            ("revenue_efficiency", metrics.get("revenue_efficiency", 0), "revenue"),
            ("lead_form_completion_rate", metrics.get("lead_form_completion_rate", 0), "conversion"),
            ("frequency", metrics.get("frequency", 0), "ads"),
        ]

        for name, value, category in key_metrics:
            if value > 0:
                self.brain.record_metric(name, value, category)

        # Write summary insight
        self.brain.write_insight(
            content=(
                f"Metrics snapshot: CPL=${metrics.get('cpl', 0):.2f}, "
                f"CTR={metrics.get('ctr', 0):.2f}%, "
                f"Conversion={metrics.get('booking_conversion_rate', 0):.1f}%, "
                f"Leads={metrics.get('total_leads_all_time', 0)}, "
                f"Booked={metrics.get('booked_all_time', 0)}"
            ),
            insight_type="metric",
            category="performance",
            confidence=1.0,
            data=metrics,
        )

        self.brain.log_activity("metrics_computed", f"Computed {len(key_metrics)} metrics")
