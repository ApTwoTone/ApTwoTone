import sqlite3
from datetime import datetime, timezone

from scripts import facebook_group_scraper as fb


def test_extract_event_date_time_location_and_attendance():
    text = (
        "Calling all vendors for our outdoor spring market on April 19, 2026 "
        "from 10am to 4pm at Griffith Park in Los Angeles. Expecting 250 guests."
    )

    assert fb.extract_event_date(text, reference_dt=datetime(2026, 3, 11, tzinfo=timezone.utc)) == "2026-04-19"
    assert fb.extract_event_time(text) == "10am - 4pm"
    assert fb.extract_event_location(text) == "Griffith Park in Los Angeles"
    assert fb.extract_expected_attendance(text) == 250


def test_extract_event_date_keeps_recent_past_same_year_for_historical_post_context():
    text = "Join us Thurs Mar 5 for live band karaoke in Calabasas."
    reference = datetime(2026, 3, 10, tzinfo=timezone.utc)

    assert fb.extract_event_date(text, reference_dt=reference) == "2026-03-05"


def test_score_bathroom_relevance_prioritizes_explicit_restroom_need():
    text = (
        "Need portable restrooms for an outdoor event in Burbank with 200 guests "
        "all day. Looking for vendors."
    )

    assert fb.score_bathroom_relevance(text) >= 9
    assert fb.classify_lead_type(text, "other") == "EVENT_OPPORTUNITY"


def test_should_alert_event_lead_for_hot_local_post():
    post = {
        "group_name": "Events In Los Angeles",
        "post_content": "Looking for portable restrooms for an outdoor event in Glendale with 150 guests.",
    }

    assert fb.should_alert_event_lead(post, 8) is True


def test_should_not_alert_low_signal_event_outside_service_area():
    post = {
        "group_name": "Bay Area Wedding Vendors",
        "post_content": "Vendors wanted for a small indoor mixer in Oakland.",
    }

    assert fb.should_alert_event_lead(post, 5) is False


def test_clean_poster_name_strips_relative_time_and_anonymous_noise():
    assert fb.clean_poster_name("Myra Lopez a week ago") == "Myra Lopez"
    assert fb.clean_poster_name("Anonymous participant") == ""


def test_classify_lead_type_prioritizes_event_hosts_over_vendor_category():
    text = (
        "Looking for a backyard wedding venue in Burbank for 150 guests on April 19. "
        "Need recommendations."
    )

    assert fb.classify_lead_type(text, "venue") == "EVENT_OPPORTUNITY"


def test_save_event_persists_actionable_metadata(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    monkeypatch.setattr(fb, "DB_PATH", db_path)
    fb.init_db()

    row_id = fb.save_event(
        {
            "poster_name": "Kai Tester",
            "post_content": (
                "Looking for portable restrooms for an outdoor event in Glendale on April 19, 2026 "
                "from 10am to 4pm at Griffith Park with 250 guests."
            ),
            "group_name": "Events In Los Angeles",
            "group_url": "https://www.facebook.com/groups/eventsinLA/",
            "post_url": "https://www.facebook.com/groups/eventsinLA/posts/1234567890/",
            "post_date": "2026-03-11T22:00:00+00:00",
            "phone": "(818) 555-1212",
            "email": "lead@example.com",
            "website": "https://example.com/event",
        },
        "content-hash-1",
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM event_leads WHERE id = ?", (row_id,)).fetchone()
    conn.close()

    assert row_id > 0
    assert row["post_url"] == "https://www.facebook.com/groups/eventsinLA/posts/1234567890/"
    assert row["event_date"] == "2026-04-19"
    assert row["event_time"] == "10am - 4pm"
    assert row["expected_attendance"] == 250
    assert row["organizer_phone"] == "(818) 555-1212"
    assert row["organizer_email"] == "lead@example.com"


def test_repair_existing_facebook_leads_backfills_blank_fields(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    monkeypatch.setattr(fb, "DB_PATH", db_path)
    fb.init_db()

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO referral_leads
            (name, first_name, facebook_group_url, post_url, qualification_score, qualification_reason, source)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "Myra Lopez a week ago",
            "Myra",
            "https://www.facebook.com/groups/example/",
            "",
            1,
            "test",
            "fb_group_scraper",
        ),
    )
    conn.execute(
        """
        INSERT INTO event_leads
            (event_name, post_text, post_url, facebook_group_url, restroom_need_score, restroom_need_reason, source)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "Unnamed Event",
            "Looking for portable restrooms for an outdoor event in Burbank on April 19 with 120 guests.",
            "",
            "https://www.facebook.com/groups/example/",
            0,
            "",
            "fb_group_scraper",
        ),
    )
    conn.commit()
    conn.close()

    repaired = fb.repair_existing_facebook_leads()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    referral = conn.execute("SELECT name, post_url FROM referral_leads").fetchone()
    event = conn.execute(
        "SELECT event_date, post_url, restroom_need_score, restroom_need_reason FROM event_leads"
    ).fetchone()
    conn.close()

    assert repaired["referral_updates"] >= 1
    assert repaired["event_updates"] >= 1
    assert referral["name"] == "Myra Lopez"
    assert referral["post_url"] == "https://www.facebook.com/groups/example/"
    assert event["event_date"] == "2026-04-19"
    assert event["post_url"] == "https://www.facebook.com/groups/example/"
    assert event["restroom_need_score"] > 0
    assert "restroom" in event["restroom_need_reason"]
