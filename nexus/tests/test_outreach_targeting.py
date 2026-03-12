from core.outreach_targeting import (
    booking_priority_score,
    call_opening_line,
    contact_first_name,
    fast_booking_category_rank,
    looks_human_contact_name,
    normalized_contact_name,
    preferred_city_rank,
    should_skip_fast_booking_email_candidate,
)


def test_category_and_city_priority_rank_high_intent_targets_first():
    assert fast_booking_category_rank("wedding_venue") < fast_booking_category_rank("event_planner")
    assert preferred_city_rank("Simi Valley") < preferred_city_rank("Irvine")


def test_booking_priority_prefers_direct_phone_over_generic_planner():
    venue = {
        "id": 1,
        "name": "Verdi Garden",
        "category": "event_venue",
        "city": "Encino",
        "phone": "(323) 687-2727",
        "phone_valid": 1,
        "contact_name": "Verdi",
        "referral_score": 82,
    }
    planner = {
        "id": 2,
        "name": "Planner Co",
        "category": "event_planner",
        "city": "Irvine",
        "phone": "",
        "phone_valid": 0,
        "contact_name": "",
        "referral_score": 82,
    }

    venue_score = booking_priority_score(venue, {"quality_score": 74, "generic_inbox": False})
    planner_score = booking_priority_score(planner, {"quality_score": 74, "generic_inbox": True})

    assert venue_score > planner_score


def test_skip_generic_inbox_low_leverage_vendor_without_phone():
    vendor = {
        "category": "event_planner",
        "phone": "",
        "phone_valid": 0,
        "contact_name": "",
    }
    decision = {"eligible": True, "generic_inbox": True}

    reason = should_skip_fast_booking_email_candidate(vendor, decision)

    assert reason == "generic_inbox_low_leverage"


def test_call_opening_uses_contact_name_when_available():
    vendor = {
        "category": "party_rental",
        "contact_name": "Irene",
    }

    opening = call_opening_line(vendor)

    assert "did I reach Irene" in opening


def test_normalized_contact_name_rejects_noisy_scraped_names():
    assert normalized_contact_name("OF THE BLUSHING") == ""
    assert normalized_contact_name("and CEO of") == ""
    assert normalized_contact_name("Eventsbyayla") == ""
    assert normalized_contact_name("Sign Up To") == ""
    assert normalized_contact_name("Los Angeles") == ""
    assert normalized_contact_name("End Google Tag") == ""
    assert normalized_contact_name("Business Development") == ""
    assert normalized_contact_name("Form Conversion Page") == ""
    assert normalized_contact_name("Form") == ""
    assert normalized_contact_name("Renee Symans") == "Renee Symans"
    assert normalized_contact_name("Get In Touch") == ""
    assert normalized_contact_name("Iframe") == ""
    assert normalized_contact_name("Rentals") == ""


def test_normalized_contact_name_trims_role_suffixes():
    assert normalized_contact_name("Kaelya Sommer Executive") == "Kaelya Sommer"


def test_contact_first_name_returns_empty_for_noise_and_name_for_real_contact():
    assert contact_first_name({"contact_name": "More"}) == ""
    assert contact_first_name({"contact_name": "Get In Touch"}) == ""
    assert contact_first_name({"contact_name": "Rentals"}) == ""
    assert contact_first_name({"contact_name": "Kim Diaz"}) == "Kim"


def test_looks_human_contact_name_rejects_cta_phrases_and_keeps_real_names():
    assert not looks_human_contact_name("Date Message Send")
    assert not looks_human_contact_name("Creating Beautiful Memories")
    assert not looks_human_contact_name("How We Can")
    assert not looks_human_contact_name("Tct Writer's Lab")
    assert not looks_human_contact_name("Doordash Directly For")
    assert looks_human_contact_name("Kaelya Sommer")
    assert looks_human_contact_name("Niall Johnston")
    assert looks_human_contact_name("Irene", verified=True)
