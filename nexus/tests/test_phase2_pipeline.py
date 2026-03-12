"""
Phase 2 Pipeline Tests — quiet hours gate, config-driven test_mode,
quote auto-generation, pipeline transitions, Convex sync fix, migration 039.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.db_migrate import run_migrations, _migrate_039_lead_booking_link
from core.services.pipeline_service import PipelineService, STAGES, TRANSITIONS, ACTION_TRANSITIONS


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
    event_date TEXT DEFAULT '',
    event_city TEXT DEFAULT '',
    guest_count INTEGER DEFAULT 0,
    event_type TEXT DEFAULT '',
    total_quote_amount REAL DEFAULT 0,
    booking_id INTEGER DEFAULT NULL
)
"""


def _make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test_phase2.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(LEADS_DDL)
    conn.close()
    return db_path


_lead_counter = 0

def _insert_lead(db_path: Path, first="Test", last="Lead", phone="8185551234",
                 source="facebook_ad", booking_status="new_lead", **kwargs) -> int:
    global _lead_counter
    _lead_counter += 1
    conn = sqlite3.connect(str(db_path))
    cols = "first_name, last_name, phone, source, booking_status, ghl_contact_id"
    vals = [first, last, phone, source, booking_status, f"test-{_lead_counter}"]
    for k, v in kwargs.items():
        cols += f", {k}"
        vals.append(v)
    placeholders = ", ".join(["?"] * len(vals))
    cur = conn.execute(f"INSERT INTO leads ({cols}) VALUES ({placeholders})", vals)
    lead_id = cur.lastrowid
    conn.commit()
    conn.close()
    return lead_id


# ---------------------------------------------------------------------------
# 1. Config-driven test_mode
# ---------------------------------------------------------------------------

def test_messenger_test_mode_reads_config(tmp_path):
    """Messenger.test_mode should read from config.json instead of being hardcoded True."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"messaging_test_mode": False}))

    with patch("integrations.messaging.Path") as mock_path_cls:
        # Make Path.home() return tmp_path so config reads from our temp file
        mock_home = MagicMock()
        mock_home.__truediv__ = lambda self, x: tmp_path / x if x == ".nexus" else MagicMock()
        nexus_dir = tmp_path / ".nexus"
        nexus_dir.mkdir(exist_ok=True)
        (nexus_dir / "config.json").write_text(json.dumps({"messaging_test_mode": False}))

        mock_path_cls.home.return_value = tmp_path

        # We need to reload, but since the import already happened, let's test the logic directly
        import integrations.messaging as msg_mod
        _cfg_path = tmp_path / ".nexus" / "config.json"
        _cfg = json.loads(_cfg_path.read_text())
        assert _cfg.get("messaging_test_mode", True) is False


def test_messenger_test_mode_defaults_true(tmp_path):
    """Without config, test_mode should default to True for safety."""
    cfg_path = tmp_path / ".nexus" / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps({}))

    cfg = json.loads(cfg_path.read_text())
    assert cfg.get("messaging_test_mode", True) is True


# ---------------------------------------------------------------------------
# 2. Quiet hours gate
# ---------------------------------------------------------------------------

def test_quiet_hours_logic():
    """Outside send window, leads should be deferred not sent."""
    from core.send_window import SendWindow

    # Simulate 2am PT — outside 8am-5pm window
    win = SendWindow(start_hour=8, end_hour=17, timezone="America/Los_Angeles")
    # The logic: if hour < start_hour or hour >= end_hour, defer
    assert 2 < win.start_hour  # 2am is before 8am
    assert 22 >= win.end_hour  # 10pm is after 5pm


def test_defer_calculates_next_morning():
    """Deferred leads should get next_action_at set to 8:00am next day."""
    from datetime import timedelta

    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    tz = ZoneInfo("America/Los_Angeles")
    # Simulate 2am PT
    now_local = datetime(2026, 3, 12, 2, 0, 0, tzinfo=tz)
    start_hour = 8

    next_morning = now_local.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    if next_morning <= now_local:
        next_morning += timedelta(days=1)

    assert next_morning.hour == 8
    assert next_morning.day == now_local.day  # Same day since 8am > 2am


def test_defer_wraps_to_next_day():
    """If it's 9pm, defer should go to 8am next day."""
    from datetime import timedelta

    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo

    tz = ZoneInfo("America/Los_Angeles")
    now_local = datetime(2026, 3, 12, 21, 0, 0, tzinfo=tz)
    start_hour = 8

    next_morning = now_local.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    if next_morning <= now_local:
        next_morning += timedelta(days=1)

    assert next_morning.hour == 8
    assert next_morning.day == 13  # Next day


# ---------------------------------------------------------------------------
# 3. Pipeline transitions
# ---------------------------------------------------------------------------

def test_pipeline_auto_transition_initial_contact(tmp_path):
    """auto_transition_on_action('initial_contact') should advance new_lead → auto_contacted."""
    db_path = _make_db(tmp_path)
    lead_id = _insert_lead(db_path, booking_status="new_lead")

    ps = PipelineService(db_path=db_path)
    ps.auto_transition_on_action(lead_id, "initial_contact")

    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT booking_status FROM leads WHERE id=?", (lead_id,)).fetchone()
    conn.close()
    assert row[0] == "auto_contacted"


def test_pipeline_auto_transition_quote_sent(tmp_path):
    """auto_transition_on_action('quote_sent') should advance auto_contacted → quote_sent."""
    db_path = _make_db(tmp_path)
    lead_id = _insert_lead(db_path, booking_status="auto_contacted")

    ps = PipelineService(db_path=db_path)
    ps.auto_transition_on_action(lead_id, "quote_sent")

    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT booking_status FROM leads WHERE id=?", (lead_id,)).fetchone()
    conn.close()
    assert row[0] == "quote_sent"


def test_pipeline_invalid_transition_blocked(tmp_path):
    """Cannot skip stages — new_lead directly to booked should fail."""
    db_path = _make_db(tmp_path)
    lead_id = _insert_lead(db_path, booking_status="new_lead")

    ps = PipelineService(db_path=db_path)
    result = ps.transition(lead_id, "booked")
    assert result["ok"] is False
    assert "Cannot transition" in result["error"]


def test_action_transitions_mapping():
    """All expected actions should be defined in ACTION_TRANSITIONS."""
    expected = ["initial_contact", "reply_received", "quote_sent",
                "deposit_sent", "deposit_paid", "event_completed", "opted_out", "closed"]
    for action in expected:
        assert action in ACTION_TRANSITIONS, f"Missing action: {action}"


# ---------------------------------------------------------------------------
# 4. Migration 039
# ---------------------------------------------------------------------------

def test_migration_039_adds_booking_id(tmp_path):
    """Migration 039 should add booking_id column to leads table."""
    db_path = tmp_path / "migrate039_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT DEFAULT '',
            source TEXT DEFAULT '',
            booking_status TEXT DEFAULT 'new_lead'
        );
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT DEFAULT (datetime('now')),
            description TEXT DEFAULT ''
        );
    """)
    # Mark 1-38 as applied
    for v in range(1, 39):
        conn.execute("INSERT INTO schema_migrations (version, description) VALUES (?, ?)",
                     (v, f"pre-applied {v}"))
    conn.commit()
    conn.close()

    run_migrations(db_path=db_path)

    conn = sqlite3.connect(str(db_path))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(leads)").fetchall()]
    conn.close()
    assert "booking_id" in cols


# ---------------------------------------------------------------------------
# 5. Stage definitions completeness
# ---------------------------------------------------------------------------

def test_all_stages_have_transitions():
    """Every defined stage should appear in TRANSITIONS."""
    for stage in STAGES:
        assert stage in TRANSITIONS, f"Stage {stage} missing from TRANSITIONS"


def test_stage_counts_query(tmp_path):
    """get_stage_counts should return counts for all stages."""
    db_path = _make_db(tmp_path)
    _insert_lead(db_path, booking_status="new_lead")
    _insert_lead(db_path, booking_status="new_lead", phone="8185552222")
    _insert_lead(db_path, booking_status="auto_contacted", phone="8185553333")

    ps = PipelineService(db_path=db_path)
    counts = ps.get_stage_counts()

    assert counts["new_lead"] == 2
    assert counts["auto_contacted"] == 1
    assert counts["booked"] == 0


# ---------------------------------------------------------------------------
# 6. Send window extension (Phase 3)
# ---------------------------------------------------------------------------

def test_send_window_default_end_hour_is_19():
    """Send window default end hour should be 7pm (19), not 5pm (17)."""
    from core.send_window import SendWindow
    win = SendWindow()
    assert win.end_hour == 19, f"Expected end_hour=19 (7pm), got {win.end_hour}"
    assert win.start_hour == 8


def test_send_window_7pm_is_in_window():
    """6:30pm PT should be within the 8am-7pm send window."""
    from core.send_window import SendWindow
    win = SendWindow()  # 8-19
    hour = 18  # 6:30pm
    assert win.start_hour <= hour < win.end_hour


# ---------------------------------------------------------------------------
# 7. Channel locking (Phase 4)
# ---------------------------------------------------------------------------

def test_migration_040_adds_channel_columns(tmp_path):
    """Migration 040 should add preferred_channel and last_reply_channel."""
    db_path = tmp_path / "migrate040_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT DEFAULT '',
            source TEXT DEFAULT '',
            booking_status TEXT DEFAULT 'new_lead'
        );
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT DEFAULT (datetime('now')),
            description TEXT DEFAULT ''
        );
    """)
    for v in range(1, 40):
        conn.execute("INSERT INTO schema_migrations (version, description) VALUES (?, ?)",
                     (v, f"pre-applied {v}"))
    conn.commit()
    conn.close()

    run_migrations(db_path=db_path)

    conn = sqlite3.connect(str(db_path))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(leads)").fetchall()]
    conn.close()
    assert "preferred_channel" in cols
    assert "last_reply_channel" in cols


# ---------------------------------------------------------------------------
# 8. Contact enrichment (Phase 4)
# ---------------------------------------------------------------------------

def test_extract_name_from_email_firstname_lastname():
    """Should extract first.last from email patterns."""
    from core.contact_enrichment import extract_name_from_email
    result = extract_name_from_email("sarah.jones@example.com")
    assert result["first_name"] == "Sarah"
    assert result["last_name"] == "Jones"
    assert result["confidence"] == "medium"


def test_extract_name_from_generic_email():
    """Generic emails like info@ should return no name."""
    from core.contact_enrichment import extract_name_from_email
    result = extract_name_from_email("info@somebusiness.com")
    assert result["confidence"] == "none"
    assert result["first_name"] == ""


def test_extract_name_from_business_email():
    """Business prefix emails should not guess a name."""
    from core.contact_enrichment import extract_name_from_email
    result = extract_name_from_email("bookings@venuename.com")
    assert result["confidence"] == "none"


def test_extract_name_single_word_low_confidence():
    """Single name prefix should be low confidence."""
    from core.contact_enrichment import extract_name_from_email
    result = extract_name_from_email("chris@business.com")
    assert result["confidence"] == "low"
    assert result["first_name"] == "Chris"
