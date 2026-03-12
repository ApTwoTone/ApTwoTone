from core import aggressive_outreach
from core.aggressive_outreach import (
    SMS_BLOCK_REASON,
    TEST_BUSINESS_RE,
    _body_for_vendor,
    _extract_contact_identity,
    _extract_evidence_snippet,
    _is_plausible_email,
    _normalize_phone,
    _preferred_phone,
    _preferred_email,
    _upsert_target,
)


def test_preferred_email_favors_same_domain_contact():
    email = _preferred_email(
        ["hello@gmail.com", "events@verdigarden.com", "info@verdigarden.com"],
        "https://verdigarden.com/contact",
        "",
    )

    assert email == "events@verdigarden.com"


def test_asset_file_names_are_not_treated_as_real_emails():
    assert not _is_plausible_email("client-1@2x-1-1.png")
    assert _preferred_email(
        ["client-1@2x-1-1.png", "events@verdigarden.com"],
        "https://verdigarden.com/contact",
        "",
    ) == "events@verdigarden.com"


def test_preferred_email_uses_fallback_when_source_is_noisy():
    noisy = [
        "hello@rfuenzalida.com",
        "impallari@gmail.com",
        "matt@pixelspread.com",
        "team@latofonts.com",
    ]
    assert _preferred_email(
        noisy,
        "https://weddingdancelessonla.com/contact",
        "kim@weddingdancelessonla.com",
    ) == "kim@weddingdancelessonla.com"


def test_placeholder_and_tracking_emails_are_not_treated_as_real_emails():
    assert not _is_plausible_email("user@domain.com")
    assert not _is_plausible_email("605a7baede844d27b8b9dc95ae0a9123@sentry-next.wixpress.com")


def test_curated_body_includes_evidence_and_backup_vendor_angle():
    vendor = {
        "name": "Verdi Garden",
        "category": "event_venue",
        "city": "Encino",
        "contact_name": "Verdi",
    }

    body = _body_for_vendor(vendor, "Private events and weddings hosted in the garden courtyard.")

    assert "Verdi Garden" in body
    assert "backup option" in body.lower()
    assert "Private events and weddings hosted in the garden courtyard." in body


def test_curated_body_falls_back_to_hi_there_when_name_is_noise():
    vendor = {
        "name": "The Blushing Details",
        "category": "wedding_planner",
        "city": "Pasadena",
        "contact_name": "OF THE BLUSHING",
    }

    body = _body_for_vendor(vendor, "Destination wedding services and banquet events.")

    assert body.startswith("Hi there,")
    assert "Hi Of," not in body


def test_sms_block_reason_is_explicit():
    assert "blocked" in SMS_BLOCK_REASON.lower()
    assert "consent" in SMS_BLOCK_REASON.lower()


def test_test_business_regex_catches_placeholder_names():
    assert TEST_BUSINESS_RE.search("ZZ Test Norm")
    assert not TEST_BUSINESS_RE.search("Sunset On The 5th")


def test_normalize_phone_rejects_invalid_nanp_numbers():
    assert _normalize_phone("174-976-3997") == ""
    assert _normalize_phone("(333) 333-3333") == ""
    assert _normalize_phone("(747) 976-3997") == "7479763997"


def test_preferred_phone_uses_fallback_when_site_also_contains_it():
    assert _preferred_phone(
        ["437-673-1301", "818-804-0320", "333-333-3333"],
        "(818) 804-0320",
    ) == "(818) 804-0320"


def test_preferred_phone_uses_fallback_when_source_is_noisy():
    noisy = [
        "207-781-0283",
        "209-451-3638",
        "219-915-6671",
        "219-994-5254",
        "220-752-0000",
        "233-508-7917",
        "252-629-4491",
        "323-297-3642",
        "410-831-9108",
    ]
    assert _preferred_phone(noisy, "(602) 291-0716") == "(602) 291-0716"


def test_evidence_snippet_skips_markup_noise():
    snippet = _extract_evidence_snippet(
        '<option value="Wedding Wire">Wedding Wire</option>\n'
        "Private events and weddings hosted in the garden courtyard."
    )

    assert snippet == "Private events and weddings hosted in the garden courtyard."


def test_upsert_target_handles_empty_source_pages(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    monkeypatch.setattr(aggressive_outreach, "DB_PATH", db_path)
    aggressive_outreach.ensure_tables()

    conn = aggressive_outreach._conn()
    result = {
        "vendor": {
            "id": 99123,
            "name": "Quiet Venue",
            "category": "event_venue",
            "city": "Moorpark",
            "website": "https://quietvenue.example",
        },
        "agent_name": "research-1",
        "source_pages": [],
        "discovered_emails": [],
        "discovered_phones": [],
        "official_email": "",
        "official_phone": "",
        "contact_name": "",
        "contact_role": "",
        "contact_confidence": 0,
        "evidence_snippet": "",
        "sales_brief": "Test brief",
        "offer_strategy": "Test strategy",
        "qualifying_questions": ["Question 1", "Question 2"],
        "fit_signals": ["weddings"],
        "research_stack": ["discovery_agent", "copy_agent"],
        "site_quality_flags": [],
        "message_subject": "Test subject",
        "message_body": "Test body",
        "call_script": "Test call script",
        "sms_draft": "",
        "sms_allowed": 0,
        "sms_reason": SMS_BLOCK_REASON,
        "email_ready": 0,
        "call_ready": 0,
        "status": "researched",
    }

    target_id = _upsert_target(conn, result)
    row = conn.execute(
        "SELECT source_url, source_title FROM aggressive_outreach_targets WHERE id = ?",
        (target_id,),
    ).fetchone()
    conn.close()

    assert row["source_url"] == ""
    assert row["source_title"] == ""


def test_extract_contact_identity_prefers_verified_name_then_email_name():
    verified_name, verified_confidence = _extract_contact_identity(
        "Contact our venue coordinator for weddings.",
        fallback="Renee Symans",
        fallback_email="renee@quietvenue.com",
        fallback_confidence=55,
        fallback_verified=1,
    )
    email_name, email_confidence = _extract_contact_identity(
        "Private events and wedding venue details.",
        fallback="",
        fallback_email="kim@quietvenue.com",
        fallback_confidence=0,
        fallback_verified=0,
    )

    assert verified_name == "Renee Symans"
    assert verified_confidence >= 95
    assert email_name == "Kim"
    assert email_confidence >= 70


def test_extract_contact_identity_rejects_long_marketing_email_locals():
    name, confidence = _extract_contact_identity(
        "Wedding and private events.",
        fallback="",
        fallback_email="eventsbyayla@example.com",
        fallback_confidence=0,
        fallback_verified=0,
    )

    assert name == ""
    assert confidence == 0


def test_extract_contact_identity_rejects_cta_phrases_and_placeholder_email():
    name, confidence = _extract_contact_identity(
        "Contact Get In Touch for pricing. Creating Beautiful Memories for every couple.",
        fallback="Date Message Send",
        fallback_email="your@email.com",
        fallback_confidence=78,
        fallback_verified=0,
    )

    assert name == ""
    assert confidence == 0
