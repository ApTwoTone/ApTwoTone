"""
Lead Intake Pipeline Tests — verify auto-approval routing and migration 038 backfill.

Tests the fix ensuring Facebook ad and website form leads get
requires_manual_approval=0, while manual/unknown sources get 1.
Also validates that migration 038 correctly backfills existing leads.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.services.lead_service import LeadService
from core.db_migrate import run_migrations


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

LEADS_DDL = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_uuid TEXT DEFAULT '',
    ghl_contact_id TEXT UNIQUE DEFAULT '',
    first_name TEXT DEFAULT '',
    last_name TEXT DEFAULT '',
    full_name TEXT DEFAULT '',
    email TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    carrier TEXT DEFAULT 'tmobile',
    source TEXT DEFAULT '',
    date_added TEXT DEFAULT '',
    discovered_at TEXT DEFAULT (datetime('now')),
    booking_status TEXT DEFAULT 'new_lead',
    status TEXT DEFAULT 'new',
    requires_manual_approval INTEGER DEFAULT 1,
    last_contacted_at TEXT DEFAULT '',
    last_reply_at TEXT DEFAULT '',
    follow_up_count INTEGER DEFAULT 0,
    next_action_at TEXT DEFAULT '',
    sms_method TEXT DEFAULT 'google_voice',
    initial_sms_sent INTEGER DEFAULT 0,
    initial_email_sent INTEGER DEFAULT 0,
    notes TEXT DEFAULT '',
    internal_notes TEXT DEFAULT '[]',
    updated_at TEXT DEFAULT (datetime('now')),
    ad_id TEXT DEFAULT '',
    ad_set_id TEXT DEFAULT '',
    campaign_id TEXT DEFAULT '',
    utm_source TEXT DEFAULT '',
    utm_medium TEXT DEFAULT '',
    utm_campaign TEXT DEFAULT '',
    utm_content TEXT DEFAULT '',
    utm_term TEXT DEFAULT '',
    form_id TEXT DEFAULT '',
    form_name TEXT DEFAULT '',
    event_date TEXT DEFAULT '',
    event_start_time TEXT DEFAULT '',
    event_end_time TEXT DEFAULT '',
    event_city TEXT DEFAULT '',
    event_address TEXT DEFAULT '',
    guest_count INTEGER DEFAULT 0,
    event_type TEXT DEFAULT '',
    terrain_notes TEXT DEFAULT '',
    power_water_notes TEXT DEFAULT '',
    deposit_status TEXT DEFAULT '',
    deposit_amount REAL DEFAULT 0,
    total_quote_amount REAL DEFAULT 0,
    business_name TEXT DEFAULT '',
    job_title TEXT DEFAULT '',
    venue_location TEXT DEFAULT '',
    website TEXT DEFAULT '',
    source_detail TEXT DEFAULT '',
    source_group_name TEXT DEFAULT '',
    is_vendor INTEGER DEFAULT 0,
    vendor_category TEXT DEFAULT '',
    is_venue INTEGER DEFAULT 0,
    venue_name TEXT DEFAULT '',
    joined_referral_program INTEGER DEFAULT 0,
    referral_partner_type TEXT DEFAULT '',
    referrals_given_count INTEGER DEFAULT 0,
    referrals_booked_count INTEGER DEFAULT 0,
    referral_payout_total REAL DEFAULT 0,
    lead_temperature_override TEXT DEFAULT '',
    last_profile_update TEXT DEFAULT ''
)
"""


def _make_db(tmp_path: Path) -> Path:
    """Create a temporary SQLite database with the leads table."""
    db_path = tmp_path / "test_leads.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(LEADS_DDL)
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Auto-approval routing tests
# ---------------------------------------------------------------------------

def test_facebook_lead_gets_auto_approval(tmp_path):
    """Facebook ad leads must be auto-approved (requires_manual_approval=0)."""
    db_path = _make_db(tmp_path)
    svc = LeadService(db_path=db_path)

    lead = svc.create_lead({
        "first_name": "Maria",
        "last_name": "Garcia",
        "phone": "8185551234",
        "source": "facebook_ad",
    })

    assert lead["requires_manual_approval"] == 0


def test_website_lead_gets_auto_approval(tmp_path):
    """Website form leads must be auto-approved (requires_manual_approval=0)."""
    db_path = _make_db(tmp_path)
    svc = LeadService(db_path=db_path)

    lead = svc.create_lead({
        "first_name": "James",
        "last_name": "Wilson",
        "email": "james@example.com",
        "source": "website_form",
    })

    assert lead["requires_manual_approval"] == 0


def test_manual_lead_requires_approval(tmp_path):
    """Manually entered leads must require Kai's approval (requires_manual_approval=1)."""
    db_path = _make_db(tmp_path)
    svc = LeadService(db_path=db_path)

    lead = svc.create_lead({
        "first_name": "Carlos",
        "last_name": "Mendez",
        "phone": "8185559999",
        "source": "manual",
    })

    assert lead["requires_manual_approval"] == 1


def test_unknown_source_requires_approval(tmp_path):
    """Unknown/untrusted sources must require manual approval."""
    db_path = _make_db(tmp_path)
    svc = LeadService(db_path=db_path)

    lead = svc.create_lead({
        "first_name": "Test",
        "last_name": "Scraper",
        "phone": "8185550000",
        "source": "scraper",
    })

    assert lead["requires_manual_approval"] == 1


# ---------------------------------------------------------------------------
# Migration 038 backfill tests
# ---------------------------------------------------------------------------

def _make_pre_migration_db(tmp_path: Path) -> Path:
    """Create a DB with leads but WITHOUT the requires_manual_approval column,
    plus a schema_migrations table marking migrations 1-37 as applied so that
    run_migrations() only executes migration 038."""
    db_path = tmp_path / "migrate_test.db"
    conn = sqlite3.connect(str(db_path))
    # Minimal leads table without requires_manual_approval
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_uuid TEXT DEFAULT '',
            ghl_contact_id TEXT UNIQUE DEFAULT '',
            first_name TEXT DEFAULT '',
            last_name TEXT DEFAULT '',
            full_name TEXT DEFAULT '',
            email TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            carrier TEXT DEFAULT 'tmobile',
            source TEXT DEFAULT '',
            booking_status TEXT DEFAULT 'new_lead',
            status TEXT DEFAULT 'new',
            notes TEXT DEFAULT '',
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT DEFAULT (datetime('now')),
            description TEXT DEFAULT ''
        );
    """)
    # Mark migrations 1-37 as already applied
    for v in range(1, 38):
        conn.execute(
            "INSERT INTO schema_migrations (version, description) VALUES (?, ?)",
            (v, f"pre-applied migration {v}"),
        )
    conn.commit()
    conn.close()
    return db_path


def test_migration_038_backfills_fb_leads(tmp_path):
    """Migration 038 must set requires_manual_approval=0 for existing facebook_ad leads
    that were incorrectly defaulting to 1."""
    db_path = _make_pre_migration_db(tmp_path)

    # Insert leads that simulate the old bug (no requires_manual_approval column yet)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO leads (ghl_contact_id, first_name, source) VALUES (?, ?, ?)",
        ("fb-lead-1", "Ana", "facebook_ad"),
    )
    conn.execute(
        "INSERT INTO leads (ghl_contact_id, first_name, source) VALUES (?, ?, ?)",
        ("fb-lead-2", "Luis", "website_form"),
    )
    conn.commit()
    conn.close()

    # Run migrations — this should add the column (DEFAULT 1) then backfill
    run_migrations(db_path=db_path)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    fb_lead = conn.execute(
        "SELECT requires_manual_approval FROM leads WHERE ghl_contact_id = ?",
        ("fb-lead-1",),
    ).fetchone()
    web_lead = conn.execute(
        "SELECT requires_manual_approval FROM leads WHERE ghl_contact_id = ?",
        ("fb-lead-2",),
    ).fetchone()
    conn.close()

    assert fb_lead["requires_manual_approval"] == 0, "facebook_ad lead should be backfilled to 0"
    assert web_lead["requires_manual_approval"] == 0, "website_form lead should be backfilled to 0"


def test_migration_038_preserves_manual_leads(tmp_path):
    """Migration 038 must NOT change requires_manual_approval for manual-source leads."""
    db_path = _make_pre_migration_db(tmp_path)

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO leads (ghl_contact_id, first_name, source) VALUES (?, ?, ?)",
        ("manual-lead-1", "Sofia", "manual"),
    )
    conn.execute(
        "INSERT INTO leads (ghl_contact_id, first_name, source) VALUES (?, ?, ?)",
        ("scraper-lead-1", "Diego", "scraper"),
    )
    conn.commit()
    conn.close()

    run_migrations(db_path=db_path)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    manual_lead = conn.execute(
        "SELECT requires_manual_approval FROM leads WHERE ghl_contact_id = ?",
        ("manual-lead-1",),
    ).fetchone()
    scraper_lead = conn.execute(
        "SELECT requires_manual_approval FROM leads WHERE ghl_contact_id = ?",
        ("scraper-lead-1",),
    ).fetchone()
    conn.close()

    assert manual_lead["requires_manual_approval"] == 1, "manual leads must stay at 1"
    assert scraper_lead["requires_manual_approval"] == 1, "scraper leads must stay at 1"
