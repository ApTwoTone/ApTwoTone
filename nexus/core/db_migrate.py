"""
Nexus CRM — Database Migration System
Tracks applied migrations in schema_migrations table.
Each ALTER TABLE ADD COLUMN is idempotent (duplicate column errors are caught and skipped).
"""
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".nexus" / "memory.db"


def _add_column(conn, table, col, col_type, default=None):
    """Add a column if it doesn't exist. Silently skips if already present."""
    defstr = f" DEFAULT {default!r}" if default is not None else ""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}{defstr}")
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise


def _migrate_001_crm_columns(conn):
    """Add CRM-specific columns to leads table."""
    adds = [
        ("leads", "lead_uuid",          "TEXT", ""),
        ("leads", "full_name",          "TEXT", ""),
        # Facebook ad attribution
        ("leads", "ad_id",              "TEXT", ""),
        ("leads", "ad_set_id",          "TEXT", ""),
        ("leads", "campaign_id",        "TEXT", ""),
        ("leads", "utm_source",         "TEXT", ""),
        ("leads", "utm_medium",         "TEXT", ""),
        ("leads", "utm_campaign",       "TEXT", ""),
        ("leads", "utm_content",        "TEXT", ""),
        ("leads", "utm_term",           "TEXT", ""),
        ("leads", "form_id",            "TEXT", ""),
        ("leads", "form_name",          "TEXT", ""),
        # Event details
        ("leads", "event_date",         "TEXT", ""),
        ("leads", "event_start_time",   "TEXT", ""),
        ("leads", "event_end_time",     "TEXT", ""),
        ("leads", "event_city",         "TEXT", ""),
        ("leads", "event_address",      "TEXT", ""),
        ("leads", "guest_count",        "INTEGER", 0),
        ("leads", "event_type",         "TEXT", ""),
        ("leads", "terrain_notes",      "TEXT", ""),
        ("leads", "power_water_notes",  "TEXT", ""),
        # Financial
        ("leads", "deposit_status",     "TEXT", "none"),
        ("leads", "deposit_amount",     "REAL", 0),
        ("leads", "total_quote_amount", "REAL", 0),
        # CRM booking status
        ("leads", "booking_status",     "TEXT", "new_lead"),
        # Internal notes (JSON array, append-only)
        ("leads", "internal_notes",     "TEXT", "[]"),
    ]
    for table, col, ctype, default in adds:
        _add_column(conn, table, col, ctype, default)


def _migrate_002_event_metadata(conn):
    """Add actor and metadata columns to lead_events."""
    _add_column(conn, "lead_events", "actor", "TEXT", "system")
    _add_column(conn, "lead_events", "metadata", "TEXT", "{}")


def _migrate_003_backfill_booking_status(conn):
    """Map old status values to new booking_status for existing leads."""
    mapping = {
        "new":             "new_lead",
        "initial_contact": "auto_contacted",
        "waiting_reply":   "auto_contacted",
        "replied":         "qualifying",
        "follow_up_1":     "auto_contacted",
        "follow_up_2":     "auto_contacted",
        "closed":          "lost",
        "opted_out":       "lost",
    }
    for old, new in mapping.items():
        conn.execute(
            "UPDATE leads SET booking_status = ? WHERE status = ? AND (booking_status = '' OR booking_status = 'new_lead')",
            (new, old)
        )
    # Backfill full_name where empty
    conn.execute(
        "UPDATE leads SET full_name = TRIM(first_name || ' ' || last_name) WHERE full_name = '' OR full_name IS NULL"
    )
    # Backfill lead_uuid where empty
    import uuid
    rows = conn.execute("SELECT id FROM leads WHERE lead_uuid = '' OR lead_uuid IS NULL").fetchall()
    for (lid,) in rows:
        conn.execute("UPDATE leads SET lead_uuid = ? WHERE id = ?", (uuid.uuid4().hex, lid))


def _migrate_004_ghl_stage_rename(conn):
    """Add business_name column to leads. (Stage renames removed — using auto_contacted stages.)"""
    _add_column(conn, "leads", "business_name", "TEXT", "")


def _migrate_005_capi_events(conn):
    """Create capi_events table for Meta Conversions API logging.
    Handles upgrade from older schema that had different columns."""
    # Check if table already exists with old schema (missing created_at)
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(capi_events)").fetchall()}
    if existing_cols and 'created_at' not in existing_cols:
        # Old table exists — add missing columns
        _add_column(conn, "capi_events", "event_id", "TEXT", "")
        _add_column(conn, "capi_events", "event_time", "INTEGER", 0)
        _add_column(conn, "capi_events", "action_source", "TEXT", "website")
        _add_column(conn, "capi_events", "ok", "INTEGER", 0)
        _add_column(conn, "capi_events", "status_code", "INTEGER", 0)
        _add_column(conn, "capi_events", "error", "TEXT", "")
        _add_column(conn, "capi_events", "created_at", "TEXT", "")
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS capi_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE,
                event_name TEXT NOT NULL,
                event_time INTEGER,
                action_source TEXT DEFAULT 'website',
                ok INTEGER DEFAULT 0,
                status_code INTEGER DEFAULT 0,
                error TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_capi_event_name ON capi_events(event_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_capi_created ON capi_events(created_at)")


def _migrate_006_marketplace_tables(conn):
    """Create marketplace_posts and marketplace_interactions tables."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS marketplace_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id TEXT DEFAULT '',
            platform TEXT NOT NULL DEFAULT '',
            variation TEXT DEFAULT '',
            region TEXT DEFAULT '',
            title TEXT DEFAULT '',
            description TEXT DEFAULT '',
            price REAL DEFAULT 0,
            images TEXT DEFAULT '[]',
            category TEXT DEFAULT '',
            status TEXT DEFAULT 'draft',
            post_url TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            posted_at TEXT DEFAULT '',
            renewed_at TEXT DEFAULT '',
            renewal_count INTEGER DEFAULT 0,
            expires_at TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS marketplace_interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER,
            interaction_type TEXT DEFAULT '',
            from_phone TEXT DEFAULT '',
            from_name TEXT DEFAULT '',
            message TEXT DEFAULT '',
            lead_id INTEGER DEFAULT NULL,
            notes TEXT DEFAULT '',
            ts TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (post_id) REFERENCES marketplace_posts(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mp_platform ON marketplace_posts(platform)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mp_status ON marketplace_posts(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mp_posted ON marketplace_posts(posted_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mi_post ON marketplace_interactions(post_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mi_type ON marketplace_interactions(interaction_type)")


def _migrate_007_b2b_leads(conn):
    """Create b2b_leads table for B2B cold email outreach system.
    Uses partial unique index on email (only when non-empty) so leads
    without email addresses don't collide on the empty string."""
    # Drop and recreate if the table has a broken UNIQUE(email) constraint
    existing = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='b2b_leads'"
    ).fetchone()
    if existing:
        count = conn.execute("SELECT COUNT(*) FROM b2b_leads").fetchone()[0]
        if count < 73:
            # Table exists but incomplete (likely hit UNIQUE constraint bug) — rebuild
            conn.execute("DROP TABLE b2b_leads")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS b2b_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_name TEXT NOT NULL,
            category TEXT DEFAULT '',
            city TEXT DEFAULT '',
            state TEXT DEFAULT 'CA',
            distance_miles REAL DEFAULT 0,
            pricing_tier INTEGER DEFAULT 1,
            contact_name TEXT DEFAULT '',
            email TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            website TEXT DEFAULT '',
            rating TEXT DEFAULT 'HOT',
            section TEXT DEFAULT '',
            lead_number INTEGER DEFAULT 0,
            email_status TEXT DEFAULT 'not_sent',
            email_sent_at TEXT,
            email_content TEXT DEFAULT '',
            reply_status TEXT DEFAULT 'none',
            reply_content TEXT DEFAULT '',
            reply_received_at TEXT,
            outcome TEXT DEFAULT 'not_contacted',
            telegram_approval_status TEXT DEFAULT '',
            telegram_message_id INTEGER,
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            is_duplicate INTEGER DEFAULT 0,
            do_not_contact INTEGER DEFAULT 0,
            UNIQUE(business_name, city)
        )
    """)
    # Partial unique index: only enforce email uniqueness when email is non-empty
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_b2b_email_unique ON b2b_leads(email) WHERE email != ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_b2b_email_status ON b2b_leads(email_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_b2b_category ON b2b_leads(category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_b2b_tier ON b2b_leads(pricing_tier)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_b2b_rating ON b2b_leads(rating)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_b2b_outcome ON b2b_leads(outcome)")


def _migrate_008_fb_vendor_prospects(conn):
    """Create fb_vendor_prospects table for Facebook group vendor scraping."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fb_vendor_prospects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            poster_name TEXT NOT NULL,
            profile_url TEXT NOT NULL UNIQUE,
            business_name TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            email TEXT DEFAULT '',
            website TEXT DEFAULT '',
            city TEXT DEFAULT '',
            category TEXT DEFAULT '',
            about TEXT DEFAULT '',
            post_content TEXT DEFAULT '',
            post_date TEXT DEFAULT '',
            post_url TEXT DEFAULT '',
            group_name TEXT DEFAULT '',
            group_url TEXT DEFAULT '',
            post_type TEXT DEFAULT 'vendor_promotion',
            image_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'new',
            contact_method TEXT DEFAULT 'none',
            notes TEXT DEFAULT '',
            follow_up_date TEXT DEFAULT '',
            scraped_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            is_duplicate INTEGER DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fbv_status ON fb_vendor_prospects(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fbv_category ON fb_vendor_prospects(category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fbv_city ON fb_vendor_prospects(city)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fbv_post_type ON fb_vendor_prospects(post_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fbv_scraped ON fb_vendor_prospects(scraped_at)")


def _migrate_009_vendor_scoring(conn):
    """Add referral_score and activity_level columns for lead quality scoring."""
    for col, default in [("referral_score", 0), ("activity_level", 0)]:
        try:
            conn.execute(f"ALTER TABLE fb_vendor_prospects ADD COLUMN {col} INTEGER DEFAULT {default}")
        except Exception:
            pass  # Column already exists
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fbv_score ON fb_vendor_prospects(referral_score)")


def _migrate_010_vendor_research_tables(conn):
    """Create vendors and vendor_outreach tables for referral partner research."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vendors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT DEFAULT '',
            email TEXT DEFAULT '',
            website TEXT DEFAULT '',
            address TEXT DEFAULT '',
            city TEXT DEFAULT '',
            category TEXT DEFAULT 'other',
            source TEXT DEFAULT '',
            rating REAL DEFAULT 0,
            review_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'new',
            outreach_status TEXT DEFAULT 'none',
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(name, phone)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vendor_outreach (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_id INTEGER NOT NULL,
            channel TEXT DEFAULT 'email',
            message_draft TEXT DEFAULT '',
            status TEXT DEFAULT 'draft',
            approved_at TEXT,
            sent_at TEXT,
            response TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (vendor_id) REFERENCES vendors(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendors_category ON vendors(category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendors_status ON vendors(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendors_city ON vendors(city)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendors_source ON vendors(source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendors_outreach_status ON vendors(outreach_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendor_outreach_vendor ON vendor_outreach(vendor_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendor_outreach_status ON vendor_outreach(status)")


def _migrate_011_shared_brain(conn):
    """Create shared brain tables for inter-agent knowledge sharing."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS brain_system_knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            source TEXT DEFAULT 'claude_md',
            confidence REAL DEFAULT 1.0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(category, key)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS brain_task_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_type TEXT NOT NULL,
            description TEXT NOT NULL,
            result_summary TEXT DEFAULT '',
            success INTEGER DEFAULT 1,
            provider_used TEXT DEFAULT '',
            model_used TEXT DEFAULT '',
            tokens_used INTEGER DEFAULT 0,
            duration_ms INTEGER DEFAULT 0,
            agent_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bth_type ON brain_task_history(task_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bth_agent ON brain_task_history(agent_id)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS brain_error_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            error_type TEXT NOT NULL,
            error_message TEXT NOT NULL,
            error_context TEXT DEFAULT '',
            file_path TEXT DEFAULT '',
            solution TEXT DEFAULT '',
            solution_verified INTEGER DEFAULT 0,
            auto_fixable INTEGER DEFAULT 0,
            fix_code TEXT DEFAULT '',
            occurrences INTEGER DEFAULT 1,
            first_seen TEXT DEFAULT (datetime('now')),
            last_seen TEXT DEFAULT (datetime('now')),
            UNIQUE(error_type, error_message)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bel_type ON brain_error_log(error_type)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS brain_vendor_intel (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_id INTEGER,
            vendor_name TEXT NOT NULL,
            intel_type TEXT NOT NULL,
            intel_data TEXT DEFAULT '{}',
            relevance_score REAL DEFAULT 0.5,
            source_agent TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            expires_at TEXT DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bvi_vendor ON brain_vendor_intel(vendor_id)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS brain_improvements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suggested_by TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            priority TEXT DEFAULT 'medium',
            status TEXT DEFAULT 'proposed',
            reviewed_at TEXT DEFAULT '',
            implemented_at TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bi_status ON brain_improvements(status)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS brain_code_knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL UNIQUE,
            module_name TEXT DEFAULT '',
            purpose TEXT DEFAULT '',
            key_functions TEXT DEFAULT '[]',
            dependencies TEXT DEFAULT '[]',
            line_count INTEGER DEFAULT 0,
            last_modified TEXT DEFAULT '',
            last_scanned TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bck_module ON brain_code_knowledge(module_name)")


def _migrate_012_process_manager(conn):
    """Create managed_processes table for OS-level worker process tracking."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS managed_processes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            pid INTEGER DEFAULT 0,
            process_type TEXT DEFAULT 'worker',
            tier INTEGER DEFAULT 3,
            status TEXT DEFAULT 'stopped',
            started_at TEXT DEFAULT '',
            last_heartbeat TEXT DEFAULT '',
            restart_count INTEGER DEFAULT 0,
            max_restarts INTEGER DEFAULT 10,
            config TEXT DEFAULT '{}',
            error_message TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mp_status ON managed_processes(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mp_pid ON managed_processes(pid)")


def _migrate_013_task_chains(conn):
    """Add chain_id for multi-agent task lineage tracking."""
    _add_column(conn, "fleet_tasks", "chain_id", "TEXT", "")
    # agent_tasks may not exist yet (created by agent_hierarchy.py at runtime)
    try:
        _add_column(conn, "agent_tasks", "chain_id", "TEXT", "")
        _add_column(conn, "agent_tasks", "fleet_task_id", "TEXT", "")
    except Exception:
        pass
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ft_chain ON fleet_tasks(chain_id)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_chains (
            chain_id TEXT PRIMARY KEY,
            chain_type TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            current_step INTEGER DEFAULT 0,
            total_steps INTEGER DEFAULT 0,
            context TEXT DEFAULT '{}',
            started_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT DEFAULT '',
            error TEXT DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tc_status ON task_chains(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tc_type ON task_chains(chain_type)")


def _migrate_014_build_pipeline(conn):
    """Create tables for Multi-Agent Build Pipeline."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS build_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            description TEXT NOT NULL,
            status TEXT DEFAULT 'architect',
            stage_model TEXT DEFAULT '',
            plan_json TEXT DEFAULT '{}',
            files_written TEXT DEFAULT '[]',
            review_report TEXT DEFAULT '',
            patch_log TEXT DEFAULT '[]',
            claude_verdict TEXT DEFAULT '',
            staging_path TEXT DEFAULT '',
            started_at TEXT DEFAULT '',
            completed_at TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS build_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            build_id INTEGER REFERENCES build_plans(id),
            file_path TEXT NOT NULL,
            action TEXT DEFAULT 'create',
            original_content TEXT DEFAULT '',
            staged_content TEXT DEFAULT '',
            patched_content TEXT DEFAULT '',
            review_notes TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bf_build ON build_files(build_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bp_status ON build_plans(status)")


def _migrate_015_talk_history(conn):
    """Create nexus_chat table for Talk to Nexus command history."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS nexus_chat (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL DEFAULT 'user',
            content TEXT NOT NULL,
            agent TEXT DEFAULT '',
            model TEXT DEFAULT '',
            category TEXT DEFAULT 'general',
            latency_ms INTEGER DEFAULT 0,
            build_id INTEGER,
            build_status TEXT DEFAULT '',
            source TEXT DEFAULT 'web',
            created_at TEXT DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nc_created ON nexus_chat(created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nc_category ON nexus_chat(category)")


def _migrate_016_division_three(conn):
    """Create Division Three tables for autonomous revenue experimentation."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS d3_personas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            platform TEXT DEFAULT '',
            niche TEXT DEFAULT '',
            personality TEXT DEFAULT '{}',
            metrics TEXT DEFAULT '{}',
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS d3_experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            name TEXT NOT NULL,
            hypothesis TEXT DEFAULT '',
            persona_id INTEGER REFERENCES d3_personas(id),
            config TEXT DEFAULT '{}',
            status TEXT DEFAULT 'queued',
            started_at TEXT DEFAULT '',
            completed_at TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS d3_experiment_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            experiment_id INTEGER REFERENCES d3_experiments(id),
            metric_type TEXT NOT NULL,
            value REAL DEFAULT 0,
            notes TEXT DEFAULT '',
            recorded_at TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS d3_trend_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            keyword TEXT NOT NULL,
            score REAL DEFAULT 0,
            context TEXT DEFAULT '',
            captured_at TEXT DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_d3_exp_cat ON d3_experiments(category, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_d3_trends_src ON d3_trend_signals(source, captured_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_d3_personas_plat ON d3_personas(platform, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_d3_outcomes_exp ON d3_experiment_outcomes(experiment_id)")


def _migrate_017_build_cost_tracking(conn):
    """Add cost tracking columns to build_plans for Claude-powered pipeline."""
    _add_column(conn, "build_plans", "input_tokens", "INTEGER", 0)
    _add_column(conn, "build_plans", "output_tokens", "INTEGER", 0)
    _add_column(conn, "build_plans", "cost_usd", "REAL", 0.0)
    _add_column(conn, "build_plans", "stage_costs", "TEXT", "{}")


def _migrate_018_posting_log_columns(conn):
    """Add missing columns to posting_log table."""
    _add_column(conn, "posting_log", "title", "TEXT", "")
    _add_column(conn, "posting_log", "body", "TEXT", "")
    _add_column(conn, "posting_log", "photos_used", "TEXT", "[]")
    _add_column(conn, "posting_log", "hashtags", "TEXT", "")
    _add_column(conn, "posting_log", "region", "TEXT", "")
    _add_column(conn, "posting_log", "auto_posted", "BOOLEAN", False)
    _add_column(conn, "posting_log", "engagement_data", "TEXT", "")


def _migrate_019_self_healing_tables(conn):
    """Create tables for self-healing flywheel: health events, model performance, repair history."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS health_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            check_type TEXT NOT NULL,
            target TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT DEFAULT '',
            resolved_by TEXT DEFAULT '',
            repair_task_id TEXT DEFAULT '',
            detected_at TEXT NOT NULL,
            resolved_at TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_health_events_status
        ON health_events(status, detected_at)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model TEXT NOT NULL,
            provider TEXT NOT NULL,
            task_type TEXT NOT NULL,
            success INTEGER NOT NULL,
            tokens_used INTEGER DEFAULT 0,
            latency_ms INTEGER DEFAULT 0,
            error_message TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_model_perf_task
        ON model_performance(task_type, success)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS repair_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            health_event_id INTEGER,
            chain_id TEXT DEFAULT '',
            diagnosis TEXT DEFAULT '',
            fix_approach TEXT DEFAULT '',
            files_changed TEXT DEFAULT '[]',
            diff TEXT DEFAULT '',
            status TEXT NOT NULL,
            model_used TEXT DEFAULT '',
            tokens_used INTEGER DEFAULT 0,
            duration_ms INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (health_event_id) REFERENCES health_events(id)
        )
    """)


def _migrate_020_vendor_enrichment(conn):
    """Add scoring/filtering columns to vendors + campaign tables for cold email pipeline."""
    # Enrichment columns on vendors
    _add_column(conn, "vendors", "referral_score", "INTEGER", 0)
    _add_column(conn, "vendors", "activity_level", "INTEGER", 0)
    _add_column(conn, "vendors", "distance_tier", "INTEGER", 0)
    _add_column(conn, "vendors", "email_valid", "INTEGER", -1)
    _add_column(conn, "vendors", "campaign_eligible", "INTEGER", 0)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendors_ref_score ON vendors(referral_score)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendors_eligible ON vendors(campaign_eligible)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendors_email_valid ON vendors(email_valid)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendors_dist_tier ON vendors(distance_tier)")

    # Email campaigns
    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            status TEXT DEFAULT 'draft',
            target_categories TEXT DEFAULT '[]',
            target_tiers TEXT DEFAULT '[1,2]',
            min_score INTEGER DEFAULT 30,
            daily_limit INTEGER DEFAULT 10,
            total_target INTEGER DEFAULT 0,
            sent_count INTEGER DEFAULT 0,
            bounce_count INTEGER DEFAULT 0,
            reply_count INTEGER DEFAULT 0,
            approved_by TEXT DEFAULT '',
            approved_at TEXT DEFAULT '',
            started_at TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Individual sends
    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_campaign_sends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            vendor_id INTEGER NOT NULL,
            email TEXT NOT NULL,
            subject TEXT DEFAULT '',
            plain_body TEXT DEFAULT '',
            status TEXT DEFAULT 'queued',
            sent_at TEXT DEFAULT '',
            error TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (campaign_id) REFERENCES email_campaigns(id),
            FOREIGN KEY (vendor_id) REFERENCES vendors(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ecs_campaign ON email_campaign_sends(campaign_id, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ecs_vendor ON email_campaign_sends(vendor_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ecs_email ON email_campaign_sends(email)")

    # Unsubscribe tracking
    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_unsubscribes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            source TEXT DEFAULT 'reply',
            detected_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_unsub_email ON email_unsubscribes(email)")


def _migrate_021_agent_studio(conn):
    """Create tables for Parallel Agent Studio: file locks, bug reports, metrics."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS file_locks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            task_id TEXT DEFAULT '',
            locked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ttl_seconds INTEGER DEFAULT 300,
            UNIQUE(file_path)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fl_agent ON file_locks(agent_id)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS bug_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL,
            line_number INTEGER DEFAULT 0,
            severity TEXT DEFAULT 'warning',
            category TEXT DEFAULT 'syntax',
            description TEXT NOT NULL,
            suggested_fix TEXT DEFAULT '',
            status TEXT DEFAULT 'open',
            found_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TEXT DEFAULT '',
            resolved_by TEXT DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_br_status ON bug_reports(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_br_severity ON bug_reports(severity)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_br_file ON bug_reports(file_path)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS studio_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_type TEXT NOT NULL,
            task_id TEXT DEFAULT '',
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT DEFAULT '',
            duration_seconds REAL DEFAULT 0,
            tokens_used INTEGER DEFAULT 0,
            provider TEXT DEFAULT '',
            success INTEGER DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sm_agent ON studio_metrics(agent_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sm_started ON studio_metrics(started_at)")


def _migrate_022_email_sequences(conn):
    """Create email_sequences table to track multi-step sequences."""
    _add_column(conn, "email_campaign_sends", "sequence_step", "INTEGER", 1)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_sequences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_id INTEGER NOT NULL UNIQUE,
            sequence_type TEXT NOT NULL,
            current_step INTEGER DEFAULT 1,
            last_sent_at TEXT DEFAULT '',
            next_send_at TEXT DEFAULT '',
            completed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (vendor_id) REFERENCES vendors(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_es_next_send ON email_sequences(next_send_at) WHERE completed = 0")


def _migrate_023_campaign_quality(conn):
    """Add campaign_quality column to vendors table for persistent scoring."""
    _add_column(conn, "vendors", "campaign_quality", "INTEGER", 0)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendors_campaign_quality ON vendors(campaign_quality)")


def _migrate_024_monitored_groups_fix(conn):
    """Align monitored_groups table with GroupScraper code (name -> group_name, url -> group_url)."""
    # Check if we need to rename or just add missing columns
    cols = {row[1] for row in conn.execute("PRAGMA table_info(monitored_groups)").fetchall()}
    if 'name' in cols and 'group_name' not in cols:
        conn.execute("ALTER TABLE monitored_groups RENAME COLUMN name TO group_name")
    if 'url' in cols and 'group_url' not in cols:
        conn.execute("ALTER TABLE monitored_groups RENAME COLUMN url TO group_url")

    # Ensure all required columns for fb_group_scraper.py exist
    _add_column(conn, "monitored_groups", "group_name", "TEXT", "")
    _add_column(conn, "monitored_groups", "group_url",  "TEXT", "")
    _add_column(conn, "monitored_groups", "category",   "TEXT", "")
    _add_column(conn, "monitored_groups", "language",   "TEXT", "en")
    _add_column(conn, "monitored_groups", "status",     "TEXT", "active")
    _add_column(conn, "monitored_groups", "last_scraped", "TEXT", "")
    _add_column(conn, "monitored_groups", "posts_found", "INTEGER", 0)
    _add_column(conn, "monitored_groups", "leads_found", "INTEGER", 0)
    _add_column(conn, "monitored_groups", "active",      "INTEGER", 1)


def _migrate_025_marketplace_niche(conn):
    """Add missing niche column to marketplace_posts for Posting Engine."""
    _add_column(conn, "marketplace_posts", "niche", "TEXT", "")

def _migrate_026_token_usage(conn):
    """Create token_usage table for per-provider daily budget tracking."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            model TEXT DEFAULT '',
            agent_id TEXT DEFAULT '',
            task_type TEXT DEFAULT '',
            tokens_in INTEGER DEFAULT 0,
            tokens_out INTEGER DEFAULT 0,
            tokens_total INTEGER DEFAULT 0,
            latency_ms INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tu_provider ON token_usage(provider, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tu_agent ON token_usage(agent_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tu_date ON token_usage(created_at)")


def _migrate_027_research_reports(conn):
    """Create research_reports table for daily AI market research."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS research_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            report_type TEXT DEFAULT 'audience',
            key_findings TEXT DEFAULT '',
            recommended_action TEXT DEFAULT '',
            content TEXT NOT NULL,
            model_used TEXT DEFAULT '',
            tokens_used INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rr_type ON research_reports(report_type, created_at)")


def _migrate_028_vendor_vetting(conn):
    """Add vendor vetting columns and ensure contact_blocklist exists."""
    vetting_cols = [
        ("vetting_status", "TEXT DEFAULT 'unvetted'"),
        ("vetting_score", "INTEGER DEFAULT 0"),
        ("vetted_at", "TEXT DEFAULT ''"),
        ("mx_valid", "INTEGER DEFAULT -1"),
        ("website_status", "INTEGER DEFAULT -1"),
        ("website_http_code", "INTEGER DEFAULT 0"),
        ("phone_valid", "INTEGER DEFAULT -1"),
        ("phone_formatted", "TEXT DEFAULT ''"),
        ("vetting_notes", "TEXT DEFAULT ''"),
    ]
    existing = {row[1] for row in conn.execute("PRAGMA table_info(vendors)").fetchall()}
    for col_name, col_def in vetting_cols:
        if col_name not in existing:
            conn.execute(f"ALTER TABLE vendors ADD COLUMN {col_name} {col_def}")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendors_vetting_status ON vendors(vetting_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendors_vetting_score ON vendors(vetting_score)")

    # Ensure contact_blocklist exists (was missing from migrations)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contact_blocklist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT DEFAULT '',
            email TEXT DEFAULT '',
            reason TEXT DEFAULT '',
            blocked_by TEXT DEFAULT 'system',
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_blocklist_email ON contact_blocklist(email)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_blocklist_phone ON contact_blocklist(phone)")


def _migrate_029_vendor_rejections(conn):
    """Create vendor_rejections table for tracking pre-insert gate rejections."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vendor_rejections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_name TEXT,
            category TEXT,
            city TEXT,
            state TEXT,
            phone TEXT,
            email TEXT,
            website TEXT,
            rejection_reason TEXT NOT NULL,
            rejection_source TEXT DEFAULT 'pre_insert_gate',
            raw_data TEXT,
            rejected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendor_rejections_reason ON vendor_rejections(rejection_reason)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vendor_rejections_date ON vendor_rejections(rejected_at)")


def _migrate_030_booking_calendar_referral(conn):
    """Extend bookings table, create calendar_events, referral_leads, event_leads."""
    # ── Extend bookings table with missing columns ──
    booking_adds = [
        ("bookings", "client_name",       "TEXT", ""),
        ("bookings", "client_phone",      "TEXT", ""),
        ("bookings", "client_email",      "TEXT", ""),
        ("bookings", "event_type",        "TEXT", ""),
        ("bookings", "event_start_time",  "TEXT", ""),
        ("bookings", "event_end_time",    "TEXT", ""),
        ("bookings", "event_city",        "TEXT", ""),
        ("bookings", "event_address",     "TEXT", ""),
        ("bookings", "guest_count",       "INTEGER", 0),
        ("bookings", "stalls_needed",     "INTEGER", 1),
        ("bookings", "quoted_price",      "REAL", 0),
        ("bookings", "deposit_amount",    "REAL", 160),
        ("bookings", "total_paid",        "REAL", 0),
        ("bookings", "delivery_operator", "TEXT", ""),
        ("bookings", "pickup_operator",   "TEXT", ""),
        ("bookings", "delivery_time",     "TEXT", ""),
        ("bookings", "pickup_time",       "TEXT", ""),
        ("bookings", "source",            "TEXT", ""),
        ("bookings", "referral_vendor_id","INTEGER", None),
        ("bookings", "referral_fee_paid", "REAL", 0),
        ("bookings", "vendor_id",         "INTEGER", None),
        ("bookings", "booking_ref",       "TEXT", ""),
    ]
    for table, col, ctype, default in booking_adds:
        _add_column(conn, table, col, ctype, default)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_bookings_date ON bookings(event_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bookings_status ON bookings(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bookings_source ON bookings(source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bookings_ref ON bookings(booking_ref)")

    # ── Calendar events — local cache of Google Calendar events ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS calendar_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            booking_id INTEGER,
            google_event_id TEXT DEFAULT '',
            calendar_account TEXT DEFAULT '',
            title TEXT DEFAULT '',
            start_time TEXT NOT NULL,
            end_time TEXT DEFAULT '',
            location TEXT DEFAULT '',
            all_day INTEGER DEFAULT 0,
            is_booking INTEGER DEFAULT 0,
            blocks_availability INTEGER DEFAULT 1,
            operator TEXT DEFAULT '',
            synced_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (booking_id) REFERENCES bookings(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cal_start ON calendar_events(start_time)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cal_google_id ON calendar_events(google_event_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cal_booking ON calendar_events(booking_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cal_account ON calendar_events(calendar_account)")

    # ── Referral leads — independent contractors from FB groups (Coder_2 populates) ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS referral_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            first_name TEXT DEFAULT '',
            username TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            email TEXT DEFAULT '',
            website TEXT DEFAULT '',
            website_works INTEGER DEFAULT -1,
            profile_url TEXT DEFAULT '',
            job_title TEXT DEFAULT '',
            business_name TEXT DEFAULT '',
            location TEXT DEFAULT '',
            city TEXT DEFAULT '',
            state TEXT DEFAULT 'CA',
            in_sfv INTEGER DEFAULT 0,
            in_la INTEGER DEFAULT 0,
            in_service_area INTEGER DEFAULT 0,
            facebook_group TEXT DEFAULT '',
            facebook_group_url TEXT DEFAULT '',
            post_text TEXT DEFAULT '',
            post_date TEXT DEFAULT '',
            post_url TEXT DEFAULT '',
            is_actively_promoting INTEGER DEFAULT 0,
            last_post_date TEXT DEFAULT '',
            has_dm_access INTEGER DEFAULT -1,
            qualification_score INTEGER DEFAULT 0,
            qualification_reason TEXT DEFAULT '',
            outreach_status TEXT DEFAULT 'new',
            outreach_channel TEXT DEFAULT '',
            referral_fee_offered REAL DEFAULT 0,
            bookings_referred INTEGER DEFAULT 0,
            revenue_generated REAL DEFAULT 0,
            notes TEXT DEFAULT '',
            source TEXT DEFAULT 'facebook_group_scraper',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rl_job ON referral_leads(job_title)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rl_city ON referral_leads(city)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rl_status ON referral_leads(outreach_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rl_score ON referral_leads(qualification_score)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rl_email ON referral_leads(email)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rl_phone ON referral_leads(phone)")

    # ── Event leads — vendor markets, fairs, festivals from FB (Coder_2 populates) ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS event_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_name TEXT NOT NULL,
            organizer_name TEXT DEFAULT '',
            organizer_phone TEXT DEFAULT '',
            organizer_email TEXT DEFAULT '',
            organizer_website TEXT DEFAULT '',
            event_date TEXT DEFAULT '',
            event_time TEXT DEFAULT '',
            event_location TEXT DEFAULT '',
            event_city TEXT DEFAULT '',
            event_address TEXT DEFAULT '',
            event_type TEXT DEFAULT '',
            expected_attendance INTEGER DEFAULT 0,
            vendor_spots_available INTEGER DEFAULT 0,
            vendor_fee REAL DEFAULT 0,
            is_outdoor INTEGER DEFAULT 1,
            restroom_need_score INTEGER DEFAULT 0,
            restroom_need_reason TEXT DEFAULT '',
            facebook_group TEXT DEFAULT '',
            facebook_group_url TEXT DEFAULT '',
            post_text TEXT DEFAULT '',
            post_url TEXT DEFAULT '',
            post_date TEXT DEFAULT '',
            poster_name TEXT DEFAULT '',
            poster_profile_url TEXT DEFAULT '',
            event_page_url TEXT DEFAULT '',
            signup_url TEXT DEFAULT '',
            outreach_status TEXT DEFAULT 'new',
            notes TEXT DEFAULT '',
            source TEXT DEFAULT 'facebook_event_scraper',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_el_date ON event_leads(event_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_el_type ON event_leads(event_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_el_status ON event_leads(outreach_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_el_city ON event_leads(event_city)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_el_score ON event_leads(restroom_need_score)")


def _migrate_031_conversations_email_queue(conn):
    """Create lead_conversations, lead_conversation_messages, and email_queue tables."""
    # ── Lead conversations — unified timeline per contact ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lead_conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            vendor_id INTEGER,
            contact_name TEXT DEFAULT '',
            contact_phone TEXT DEFAULT '',
            contact_email TEXT DEFAULT '',
            channel_types TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            last_message_at TEXT DEFAULT '',
            last_inbound_at TEXT DEFAULT '',
            last_outbound_at TEXT DEFAULT '',
            first_response_time_seconds INTEGER DEFAULT 0,
            message_count INTEGER DEFAULT 0,
            inbound_count INTEGER DEFAULT 0,
            outbound_count INTEGER DEFAULT 0,
            unread_count INTEGER DEFAULT 0,
            needs_follow_up INTEGER DEFAULT 0,
            follow_up_at TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            lead_type TEXT DEFAULT '',
            lead_stage TEXT DEFAULT 'new',
            priority_score INTEGER DEFAULT 0,
            priority_reason TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (lead_id) REFERENCES leads(id),
            FOREIGN KEY (vendor_id) REFERENCES vendors(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lc_lead ON lead_conversations(lead_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lc_vendor ON lead_conversations(vendor_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lc_status ON lead_conversations(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lc_stage ON lead_conversations(lead_stage)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lc_last_msg ON lead_conversations(last_message_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lc_follow_up ON lead_conversations(needs_follow_up)")

    # ── Conversation messages — individual messages across all channels ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lead_conversation_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            direction TEXT NOT NULL,
            channel TEXT NOT NULL DEFAULT 'email',
            sender TEXT DEFAULT '',
            recipient TEXT DEFAULT '',
            subject TEXT DEFAULT '',
            body_plain TEXT DEFAULT '',
            body_html TEXT DEFAULT '',
            source_account TEXT DEFAULT '',
            source_message_id TEXT DEFAULT '',
            read_at TEXT DEFAULT '',
            delivered_at TEXT DEFAULT '',
            bounced INTEGER DEFAULT 0,
            bounce_reason TEXT DEFAULT '',
            message_timestamp TEXT NOT NULL,
            scraped_at TEXT DEFAULT (datetime('now')),
            metadata TEXT DEFAULT '{}',
            FOREIGN KEY (conversation_id) REFERENCES lead_conversations(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lcm_conv ON lead_conversation_messages(conversation_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lcm_dir ON lead_conversation_messages(direction)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lcm_channel ON lead_conversation_messages(channel)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lcm_ts ON lead_conversation_messages(message_timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lcm_src_id ON lead_conversation_messages(source_message_id)")

    # ── Email queue — staged emails for morning review ──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS email_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_id INTEGER,
            lead_id INTEGER,
            recipient_email TEXT NOT NULL,
            recipient_name TEXT DEFAULT '',
            category TEXT DEFAULT '',
            subject TEXT NOT NULL,
            body_plain TEXT NOT NULL,
            body_html TEXT DEFAULT '',
            template_type TEXT DEFAULT 'initial_outreach',
            status TEXT DEFAULT 'queued',
            scheduled_date TEXT DEFAULT '',
            batch_number INTEGER DEFAULT 0,
            priority_score INTEGER DEFAULT 0,
            tags TEXT DEFAULT '',
            edited_by TEXT DEFAULT '',
            edited_at TEXT DEFAULT '',
            original_body TEXT DEFAULT '',
            sent_at TEXT DEFAULT '',
            send_result TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (vendor_id) REFERENCES vendors(id),
            FOREIGN KEY (lead_id) REFERENCES leads(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_eq_status ON email_queue(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_eq_date ON email_queue(scheduled_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_eq_vendor ON email_queue(vendor_id)")


def _migrate_032_agent_runtime_extensions(conn):
    """Extend agent_status table with runtime tracking columns."""
    adds = [
        ("agent_status", "last_heartbeat", "REAL", 0),
        ("agent_status", "daemon_type", "TEXT", ""),
        ("agent_status", "launchd_label", "TEXT", ""),
        ("agent_status", "pid", "INTEGER", 0),
        ("agent_status", "started_at", "TEXT", ""),
        ("agent_status", "uptime_seconds", "INTEGER", 0),
    ]
    for table, col, ctype, default in adds:
        _add_column(conn, table, col, ctype, default)


def _migrate_033_lead_profile_fields(conn):
    """Add richer lead profile and referral-tracking fields to leads."""
    adds = [
        ("leads", "job_title", "TEXT", ""),
        ("leads", "venue_location", "TEXT", ""),
        ("leads", "website", "TEXT", ""),
        ("leads", "source_detail", "TEXT", ""),
        ("leads", "source_group_name", "TEXT", ""),
        ("leads", "is_vendor", "INTEGER", 0),
        ("leads", "vendor_category", "TEXT", ""),
        ("leads", "is_venue", "INTEGER", 0),
        ("leads", "venue_name", "TEXT", ""),
        ("leads", "joined_referral_program", "INTEGER", 0),
        ("leads", "referral_partner_type", "TEXT", ""),
        ("leads", "referrals_given_count", "INTEGER", 0),
        ("leads", "referrals_booked_count", "INTEGER", 0),
        ("leads", "referral_payout_total", "REAL", 0),
        ("leads", "referral_source", "TEXT", ""),
        ("leads", "lead_temperature_override", "TEXT", ""),
        ("leads", "last_profile_update", "TEXT", ""),
    ]
    for table, col, ctype, default in adds:
        _add_column(conn, table, col, ctype, default)


def _migrate_034_venue_assessments(conn):
    """Create venue_assessments table for restroom intelligence."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS venue_assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_id INTEGER NOT NULL,
            venue_name TEXT NOT NULL,
            website TEXT DEFAULT '',
            has_restrooms TEXT DEFAULT 'unknown',
            restroom_type TEXT DEFAULT 'unknown',
            restroom_addon_price TEXT DEFAULT '',
            restroom_details TEXT DEFAULT '',
            outdoor_capacity INTEGER DEFAULT 0,
            indoor_capacity INTEGER DEFAULT 0,
            venue_type TEXT DEFAULT '',
            event_types TEXT DEFAULT '[]',
            contact_info TEXT DEFAULT '{}',
            fit_score INTEGER DEFAULT 0,
            fit_reasoning TEXT DEFAULT '',
            confidence TEXT DEFAULT 'low',
            pages_crawled INTEGER DEFAULT 0,
            raw_ai_response TEXT DEFAULT '',
            source TEXT DEFAULT 'website_crawl',
            assessed_at TEXT DEFAULT (datetime('now')),
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_va_vendor ON venue_assessments(vendor_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_va_fit ON venue_assessments(fit_score DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_va_restrooms ON venue_assessments(has_restrooms)")
    _add_column(conn, "vendors", "venue_assessed", "INTEGER", 0)
    _add_column(conn, "vendors", "venue_fit_score", "INTEGER", 0)


def _migrate_035_agent_sessions(conn):
    """Create agent_sessions table for multi-agent coordination."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            device TEXT DEFAULT '',
            agent_type TEXT DEFAULT '',
            working_on TEXT DEFAULT '',
            files_locked TEXT DEFAULT '[]',
            status TEXT DEFAULT 'active',
            started_at TEXT DEFAULT (datetime('now')),
            last_heartbeat TEXT DEFAULT (datetime('now')),
            completed_work TEXT DEFAULT '[]'
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_sessions_session ON agent_sessions(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_sessions_status ON agent_sessions(status, last_heartbeat DESC)")


def _migrate_036_task_board(conn):
    """Create task_board table for shared goals across all agents/devices."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_board (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            priority INTEGER DEFAULT 3,
            status TEXT DEFAULT 'open',
            assigned_to TEXT DEFAULT '',
            created_by TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT DEFAULT '',
            tags TEXT DEFAULT '[]',
            files_involved TEXT DEFAULT '[]',
            progress_note TEXT DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_task_board_status ON task_board(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_task_board_priority ON task_board(priority DESC, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_task_board_assigned ON task_board(assigned_to)")


def _migrate_037_task_board_extensions(conn):
    """Add task_type and complexity columns to task_board."""
    _add_column(conn, "task_board", "task_type", "TEXT", "code")
    _add_column(conn, "task_board", "complexity", "INTEGER", 3)


def _migrate_038_requires_manual_approval_column(conn):
    """Ensure requires_manual_approval column exists with DEFAULT 1.
    Backfill: set to 0 for leads from trusted ad/form sources that were
    incorrectly defaulting to 1."""
    _add_column(conn, "leads", "requires_manual_approval", "INTEGER", 1)
    # Backfill: Facebook ad and website form leads should be auto-contactable
    conn.execute(
        "UPDATE leads SET requires_manual_approval = 0 "
        "WHERE requires_manual_approval = 1 "
        "AND LOWER(source) IN ('facebook_ad', 'facebook_lead_ad', 'website', 'website_form')"
    )


def _migrate_039_lead_booking_link(conn):
    """Add booking_id column to leads table for lead → booking linkage."""
    _add_column(conn, "leads", "booking_id", "INTEGER", None)


MIGRATIONS = [
    (1, "Add CRM columns to leads", _migrate_001_crm_columns),
    (2, "Add actor/metadata to lead_events", _migrate_002_event_metadata),
    (3, "Backfill booking_status from old status", _migrate_003_backfill_booking_status),
    (4, "Rename stages to GHL naming + add business_name", _migrate_004_ghl_stage_rename),
    (5, "Create capi_events table for Meta CAPI logging", _migrate_005_capi_events),
    (6, "Create marketplace_posts and interactions tables", _migrate_006_marketplace_tables),
    (7, "Create b2b_leads table for cold email outreach", _migrate_007_b2b_leads),
    (8, "Create fb_vendor_prospects table for group scraping", _migrate_008_fb_vendor_prospects),
    (9, "Add vendor scoring columns", _migrate_009_vendor_scoring),
    (10, "Create vendors and vendor_outreach tables for referral research", _migrate_010_vendor_research_tables),
    (11, "Create shared brain tables for inter-agent knowledge", _migrate_011_shared_brain),
    (12, "Create managed_processes table for OS-level workers", _migrate_012_process_manager),
    (13, "Add task chains for multi-agent collaboration", _migrate_013_task_chains),
    (14, "Create build pipeline tables", _migrate_014_build_pipeline),
    (15, "Create nexus_chat table for Talk to Nexus", _migrate_015_talk_history),
    (16, "Create Division Three revenue experimentation tables", _migrate_016_division_three),
    (17, "Add cost tracking to build pipeline", _migrate_017_build_cost_tracking),
    (18, "Add missing columns to posting_log", _migrate_018_posting_log_columns),
    (19, "Create self-healing flywheel tables", _migrate_019_self_healing_tables),
    (20, "Vendor enrichment columns + email campaign tables", _migrate_020_vendor_enrichment),
    (21, "Agent Studio: file locks, bug reports, metrics", _migrate_021_agent_studio),
    (22, "Email sequences tracking", _migrate_022_email_sequences),
    (23, "Vendor campaign quality column", _migrate_023_campaign_quality),
    (24, "Fix monitored_groups column naming", _migrate_024_monitored_groups_fix),
    (25, "Add niche column to marketplace_posts", _migrate_025_marketplace_niche),
    (26, "Create token_usage table for budget tracking", _migrate_026_token_usage),
    (27, "Create research_reports table for AI market research", _migrate_027_research_reports),
    (28, "Vendor vetting columns + contact_blocklist", _migrate_028_vendor_vetting),
    (29, "Vendor rejections table", _migrate_029_vendor_rejections),
    (30, "Bookings enhancement + calendar_events + referral_leads + event_leads", _migrate_030_booking_calendar_referral),
    (31, "Lead conversations + conversation messages + email queue", _migrate_031_conversations_email_queue),
    (32, "Agent runtime extensions (heartbeat, daemon_type, pid)", _migrate_032_agent_runtime_extensions),
    (33, "Lead profile fields + referral tracking on leads", _migrate_033_lead_profile_fields),
    (34, "Venue assessments table for restroom intelligence", _migrate_034_venue_assessments),
    (35, "Agent coordination sessions table", _migrate_035_agent_sessions),
    (36, "Shared task board for cross-agent goals", _migrate_036_task_board),
    (37, "Add task_type and complexity to task_board", _migrate_037_task_board_extensions),
    (38, "Add requires_manual_approval column + backfill for ad/form leads", _migrate_038_requires_manual_approval_column),
    (39, "Add booking_id column to leads for lead-to-booking linkage", _migrate_039_lead_booking_link),
]


def run_migrations(db_path=None):
    """Run all pending migrations. Safe to call on every startup."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT DEFAULT (datetime('now')),
            description TEXT DEFAULT ''
        )
    """)
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()}
    for version, desc, fn in MIGRATIONS:
        if version not in applied:
            print(f"[DB] Running migration {version}: {desc}")
            fn(conn)
            conn.execute("INSERT INTO schema_migrations (version, description) VALUES (?, ?)", (version, desc))
            conn.commit()
    conn.close()
