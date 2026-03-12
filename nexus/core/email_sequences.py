"""
Email Sequence System — Multi-step cold email sequences for vendor outreach.

4 sequence types based on segment:
  VENUE        — 5 emails over 14 days targeting venues for vendor list placement
  VENDOR       — 5 emails over 14 days targeting event vendors (DJ/photo/caterer/etc)
  PLANNER      — 6 emails over 18 days targeting wedding/event planners
  REACTIVATION — 4 emails over 135 days for re-engaging cold partners

Each step has: day_offset, subject_options[], body_template (with placeholders),
and a CTA description. Placeholders: {first_name}, {business_name}, {city},
{category_phrase}, {your_name}.

Usage:
    from core.email_sequences import get_sequence, render_email, map_category_to_segment
    segment = map_category_to_segment("wedding_venue")
    seq = get_sequence(segment)
    email = render_email(seq[0], vendor_data)
"""
from __future__ import annotations

import random
import re
from typing import Any, Dict, List, Optional

# ── CAN-SPAM Footer ──────────────────────────────────────────────────────────

CAN_SPAM_FOOTER = (
    "\n\n"
    "Zoar Bathroom Rentals | San Fernando Valley, CA\n"
    'If you\'d rather not get follow-ups, reply "stop" '
    "and we will remove you immediately."
)

CAN_SPAM_HTML = """<tr>
<td style="padding:16px 32px;border-top:1px solid #e0e0e0;font-size:11px;
color:#999999;text-align:center;line-height:1.5;">
Zoar Bathroom Rentals | San Fernando Valley, CA<br>
If you'd rather not get follow-ups, reply &ldquo;stop&rdquo;
and we will remove you immediately.
</td>
</tr>"""

TRUSTED_NAME_SOURCES = {
    "facebook_lead_form",
    "facebook",
    "facebook_sheet",
    "facebook_ad",
    "facebook_test",
}

GENERIC_NAME_TOKENS = {
    "info", "sales", "contact", "hello", "admin", "support", "office", "team",
    "events", "event", "weddings", "wedding", "booking", "bookings", "book",
    "inquiry", "inquiries", "reservations", "frontdesk", "mail", "help",
    "service", "services", "staff", "manager", "owner", "venue",
}


# ── Sequence Definitions ─────────────────────────────────────────────────────

VENUE_SEQUENCE = [
    {
        "step": 1,
        "day_offset": 0,
        "subject_options": [
            "Backup restroom vendor for outdoor events",
            "Quick vendor list question",
            "Overflow restroom coverage",
            "Secondary restroom vendor option",
            "Outdoor event restroom partner",
        ],
        "body_template": (
            "Hi {first_name}!\n\n"
            "Quick question: do you already have a primary restroom trailer vendor for outdoor events or overflow restroom capacity?\n\n"
            "Zoar Bathroom Rentals supports venues across LA and SFV with fast availability checks plus full delivery, setup, and pickup.\n\n"
            "If you already have a primary, we would like to be the backup option for peak dates. If you do not, I can send a short one-page intro.\n\n"
            "Open to that?\n\n"
            "Best,\n"
            "{your_name}\n"
            "Zoar Bathroom Rentals\n"
            "(424) 235-8979\n"
            "zoarbathroomrental.com\n"
        ),
        "cta": "Open to adding us as a backup option?",
    },
    {
        "step": 2,
        "day_offset": 3,
        "subject_options": [
            "Who handles vendor lists?",
            "Right contact for event partners?",
            "Quick follow-up",
            "Vendor list contact?",
            "Backup vendor option",
        ],
        "body_template": (
            "Hi {first_name} — quick follow-up.\n\n"
            "Is vendor list maintenance handled by you, or by someone else "
            "on the events or operations side?\n\n"
            "If you point me to the right person, I will keep it brief and "
            "send a single-page overview they can review quickly.\n\n"
            "— {your_name}\n"
        ),
        "cta": "Point me to the right contact",
    },
    {
        "step": 3,
        "day_offset": 7,
        "subject_options": [
            "When restrooms are the bottleneck",
            "Backup coverage for peak dates",
            "Outdoor event safeguard",
            "Fast availability checks",
            "Last-minute coverage",
        ],
        "body_template": (
            "Hi {first_name} —\n\n"
            "The reason venues keep us as a backup is simple: when the "
            "main option is booked (or the guest count grows), restrooms "
            "become a guest-experience risk fast.\n\n"
            "Our workflow is quick: you/your client texts a date + address "
            "+ headcount → we confirm availability (we have one unit, so "
            "it's a clean yes/no) → quote.\n\n"
            "Want the 1-page overview to review internally?\n\n"
            "— {your_name}\n"
        ),
        "cta": "Want the 1-page overview?",
    },
    {
        "step": 4,
        "day_offset": 11,
        "subject_options": [
            "Simple referral terms",
            "Referral question",
            "Easy way to mention us",
            "Vendor list + referral",
            "Quick payout info",
        ],
        "body_template": (
            "Hi {first_name} —\n\n"
            "If it helps, we offer a simple referral thank-you for "
            "venue/vendor partners when a booking comes through "
            "(typically $200 per event). No pressure if you don't do "
            "referral fees — being listed as a backup option is still "
            "useful.\n\n"
            "If you're open, I can send a one-page overview and the "
            "referral terms in one short message.\n\n"
            "— {your_name}\n"
        ),
        "cta": "Want the overview + referral terms?",
    },
    {
        "step": 5,
        "day_offset": 15,
        "subject_options": [
            "Close the loop?",
            "Should I drop this?",
            "Last note",
            "Worth sending info?",
            "Vendor list or no?",
        ],
        "body_template": (
            "Hi {first_name} — last note from me.\n\n"
            "Should I (a) send the 1-pager to review for your vendor list, "
            "or (b) close this out?\n\n"
            "Either way is helpful — just reply with \"a\" or \"b.\"\n\n"
            "— {your_name}\n"
        ),
        "cta": "Reply 'a' or 'b'",
    },
]

VENDOR_SEQUENCE = [
    {
        "step": 1,
        "day_offset": 0,
        "subject_options": [
            "Restroom trailer partner for outdoor clients",
            "Quick partner question",
            "Backup restroom option for outdoor events",
            "Outdoor event restroom add-on",
            "Preferred restroom partner?",
        ],
        "body_template": (
            "Hi {first_name}!\n\n"
            "Quick question: when your outdoor-event clients ask about restroom trailers, do you already have a go-to partner?\n\n"
            "Zoar Bathroom Rentals covers LA and SFV with fast quoting plus full delivery, setup, and pickup.\n\n"
            "If you do not already have a preferred restroom partner, we would love to be the one your team calls first.\n\n"
            "Open to that?\n\n"
            "Best,\n"
            "{your_name}\n"
            "Zoar Bathroom Rentals\n"
            "(424) 235-8979\n"
            "zoarbathroomrental.com\n"
        ),
        "cta": "Open to a restroom trailer partner?",
    },
    {
        "step": 2,
        "day_offset": 3,
        "subject_options": [
            "Quick follow-up",
            "Simple partner follow-up",
            "How we handle outdoor jobs",
            "One-minute partner note",
            "Partner check-in",
        ],
        "body_template": (
            "Hi {first_name} - quick follow-up.\n\n"
            "When one of your clients needs restroom coverage, send us the event date, location, "
            "and guest count and we will confirm availability fast.\n\n"
            "If helpful, I can send a one-page overview your team can keep on hand for outdoor jobs.\n\n"
            "— {your_name}\n"
        ),
        "cta": "Want the one-page overview?",
    },
    {
        "step": 3,
        "day_offset": 7,
        "subject_options": [
            "One unit = fast yes/no",
            "Availability is simple",
            "Backup option",
            "For peak Saturdays",
            "Quick quote workflow",
        ],
        "body_template": (
            "Hi {first_name} —\n\n"
            "One thing vendors like: we only have one unit, so "
            "availability is clean — quick yes/no. That makes you look "
            "organized if the client asks late in planning.\n\n"
            "If you ever want to intro us on a thread, you can just "
            "CC/text us. We'll handle quoting directly.\n\n"
            "Should I send the \"how to refer\" one-pager?\n\n"
            "— {your_name}\n"
        ),
        "cta": "Send the one-pager—yes/no?",
    },
    {
        "step": 4,
        "day_offset": 11,
        "subject_options": [
            "If they already have restrooms",
            "When it's covered… until it isn't",
            "Outdoor guest count changes",
            "Quick backup note",
            "Last-minute upgrades",
        ],
        "body_template": (
            "Hi {first_name} -\n\n"
            "Totally normal if clients say \"restrooms are covered.\" "
            "The moments we get called: guest count grew, bathrooms are "
            "far from the reception area, or the venue's facilities "
            "aren't enough for the outdoor portion.\n\n"
            "If helpful, I can send a short referral checklist for that scenario.\n\n"
            "— {your_name}\n"
        ),
        "cta": "Want the backup scenario checklist?",
    },
    {
        "step": 5,
        "day_offset": 15,
        "subject_options": [
            "Close this out?",
            "Should I stop here?",
            "Last check",
            "Partner or pass",
            "Worth sending info?",
        ],
        "body_template": (
            "Hi {first_name} — should I keep you in the loop with a "
            "single quarterly note (availability + any updates), or "
            "close this out?\n\n"
            "Reply \"loop\" or \"close.\"\n\n"
            "— {your_name}\n"
        ),
        "cta": "Reply 'loop' or 'close'",
    },
]

PLANNER_SEQUENCE = [
    {
        "step": 1,
        "day_offset": 0,
        "subject_options": [
            "Backup restroom option for outdoor events",
            "Quick planner partner question",
            "Restroom vendor backup for your events",
            "Fast restroom backup for outdoor events",
            "Secondary restroom vendor option",
        ],
        "body_template": (
            "Hi {first_name}!\n\n"
            "Quick question: do you already have a restroom trailer vendor you trust for outdoor or private-property events?\n\n"
            "Zoar Bathroom Rentals helps planners across LA and SFV with fast availability checks and full onsite setup.\n\n"
            "If you already have a primary vendor, we would like to be the backup option for overflow dates. If you do not, I can send a short intro.\n\n"
            "Open to that?\n\n"
            "Best,\n"
            "{your_name}\n"
            "Zoar Bathroom Rentals\n"
            "(424) 235-8979\n"
            "zoarbathroomrental.com\n"
        ),
        "cta": "Open to a backup restroom vendor option?",
    },
    {
        "step": 2,
        "day_offset": 3,
        "subject_options": [
            "Client-safe wording",
            "The simple question",
            "Planning workflow",
            "Client wording option",
            "Restroom backup option",
        ],
        "body_template": (
            "Hi {first_name} — here's the client wording partners use:\n\n"
            '"Have we confirmed restrooms for guest comfort (especially '
            "for the outdoor portion)? If not, I can share a premium "
            "local restroom trailer option so we're covered.\"\n\n"
            "Workflow is simple: date + address + guest count → quick "
            "availability yes/no → quote.\n\n"
            "Want the one-paragraph vendor-list blurb too?\n\n"
            "— {your_name}\n"
        ),
        "cta": "Send the vendor-list blurb—yes/no?",
    },
    {
        "step": 3,
        "day_offset": 7,
        "subject_options": [
            "Preferred vs backup",
            "How planners position this",
            "Planning doc insert",
            "Vendor list blurb",
            "Add to your toolkit",
        ],
        "body_template": (
            "Hi {first_name} —\n\n"
            "In practice, planners position us one of two ways:\n\n"
            "1. Preferred when the venue's facilities are clearly "
            "insufficient, or\n"
            "2. Backup for peace of mind (especially peak Saturdays).\n\n"
            "I can send a 2-3 sentence \"vendor list blurb\" you can "
            "paste into your planning doc. Want that?\n\n"
            "— {your_name}\n"
        ),
        "cta": "Want the vendor-list blurb?",
    },
    {
        "step": 4,
        "day_offset": 11,
        "subject_options": [
            "Venue requirements",
            "Planner logistics",
            "Compliance + setup",
            "Making approvals easy",
            "Setup requirements",
        ],
        "body_template": (
            "Hi {first_name} —\n\n"
            "Quick note since planners often get stuck here: many venues "
            "require vendors to provide proof of insurance and list the "
            "venue as additional insured.\n\n"
            "If that's part of your workflow, we keep booking logistics "
            "simple (and can respond fast on dates/quotes).\n\n"
            "Want me to send a one-page \"setup requirements\" checklist "
            "you can keep on file?\n\n"
            "— {your_name}\n"
        ),
        "cta": "Send setup checklist—yes/no?",
    },
    {
        "step": 5,
        "day_offset": 15,
        "subject_options": [
            "Referral thank-you",
            "Optional referral terms",
            "If you ever refer",
            "Simple crediting",
            "Planner partner note",
        ],
        "body_template": (
            "Hi {first_name} —\n\n"
            "Optional but sharing in case helpful: we do a simple "
            "referral thank-you (typically $200 per booked event).\n\n"
            "If you don't do referrals, no issue — being on your "
            "preferred/backup list is the main goal.\n\n"
            "Should I send the one-paragraph terms?\n\n"
            "— {your_name}\n"
        ),
        "cta": "Send terms—yes/no?",
    },
    {
        "step": 6,
        "day_offset": 18,
        "subject_options": [
            "Loop or close?",
            "Should I archive this?",
            "Last check-in",
            "Quick yes/no",
            "Close the loop",
        ],
        "body_template": (
            "Hi {first_name} — last note.\n\n"
            "Should I (1) keep you on a low-frequency planner-only "
            "check-in (every 30-45 days), or (2) close this out?\n\n"
            "Reply \"1\" or \"2.\"\n\n"
            "— {your_name}\n"
        ),
        "cta": "Reply '1' or '2'",
    },
]

REACTIVATION_SEQUENCE = [
    {
        "step": 1,
        "day_offset": 0,
        "subject_options": [
            "Quick availability snapshot",
            "Dates booking up",
            "Outdoor season check-in",
            "One-unit availability",
            "Restroom backup reminder",
        ],
        "body_template": (
            "Hi {first_name} — quick monthly snapshot (keeping it brief).\n\n"
            "We're booking upcoming dates now; since we only have one "
            "unit, availability is either open or not. If you have any "
            "clients with outdoor/private venues coming up, feel free to "
            "text a date + area and we'll confirm quickly.\n\n"
            "Want me to send updated photos you can keep in your vendor kit?\n\n"
            "— {your_name}\n"
        ),
        "cta": "Want updated photos—yes/no?",
    },
    {
        "step": 2,
        "day_offset": 40,
        "subject_options": [
            "Updated client wording",
            "Client wording update",
            "Quick referral refresh",
            "Vendor kit snippet",
            "Simple wording",
        ],
        "body_template": (
            "Hi {first_name} — sharing cleaner client wording "
            "partners have been using:\n\n"
            '"Have you confirmed restrooms for guest comfort — especially '
            "outdoors? If not, I can share a premium local restroom "
            "trailer option so you're covered.\"\n\n"
            "— {your_name}\n"
        ),
        "cta": "Want a 2-3 sentence vendor-list blurb too?",
    },
    {
        "step": 3,
        "day_offset": 80,
        "subject_options": [
            "Seasonal planning note",
            "When to book restrooms",
            "Peak-date reminder",
            "Outdoor setups check",
            "Quick tip",
        ],
        "body_template": (
            "Hi {first_name} — quick planning tip (common miss): "
            "restrooms usually get booked late, but availability "
            "disappears fast on peak Saturdays.\n\n"
            "If you want, I can hold a 24-hour soft hold when you have "
            "a serious client date (no pressure — just helps reduce "
            "risk).\n\n"
            "Would that be useful for your workflow?\n\n"
            "— {your_name}\n"
        ),
        "cta": "Useful—yes/no?",
    },
    {
        "step": 4,
        "day_offset": 120,
        "subject_options": [
            "Still relevant?",
            "Keep or pause?",
            "Partner check-in",
            "Should I remove you?",
            "Quick yes/no",
        ],
        "body_template": (
            "Hi {first_name} — checking in so I don't become noise.\n\n"
            "Should I keep you on this low-frequency partner list, "
            "or remove you?\n\n"
            "Reply \"keep\" or \"remove.\"\n\n"
            "— {your_name}\n"
        ),
        "cta": "Reply 'keep' or 'remove'",
    },
]


# ── Sequence Registry ─────────────────────────────────────────────────────────

SEQUENCES = {
    "venue": VENUE_SEQUENCE,
    "vendor": VENDOR_SEQUENCE,
    "planner": PLANNER_SEQUENCE,
    "reactivation": REACTIVATION_SEQUENCE,
}

# ── Category → Segment Mapping ───────────────────────────────────────────────

CATEGORY_TO_SEGMENT = {
    # VENUE segment
    "wedding_venue": "venue",
    "quinceanera_venue": "venue",
    "venue": "venue",
    "winery_venue": "venue",
    "winery": "venue",
    "farm_ranch_venue": "venue",
    "church": "venue",
    "church_hall": "venue",
    "community_center": "venue",
    "event_venue": "venue",
    "banquet_hall": "venue",
    "country_club": "venue",
    "hotel": "venue",
    "hotel_venue": "venue",
    "restaurant": "venue",
    "corporate_event_venue": "venue",
    # PLANNER segment
    "event_planner": "planner",
    "wedding_planner": "planner",
    "quinceanera_planner": "planner",
    "corporate_event_planner": "planner",
    # VENDOR segment (everything else)
    "catering": "vendor",
    "caterer": "vendor",
    "event_rental": "vendor",
    "furniture_rental": "vendor",
    "party_rental": "vendor",
    "tent_rental": "vendor",
    "dj_entertainment": "vendor",
    "dj": "vendor",
    "photography": "vendor",
    "photographer": "vendor",
    "florist": "vendor",
    "bartending_mobile_bar": "vendor",
    "construction": "vendor",
    "festival_organizer": "vendor",
    "setup_crew": "vendor",
    "generator_rental": "vendor",
    "dance_floor": "vendor",
    "decorator": "vendor",
    "valet": "vendor",
    "bounce_house": "vendor",
    "photo_booth": "vendor",
    "lighting": "vendor",
    "food_truck": "vendor",
    "balloon_artist": "vendor",
    "officiant": "vendor",
    "hair_makeup": "vendor",
    "wedding_cake": "vendor",
    "security_service": "vendor",
    "videography": "vendor",
    "other": "vendor",
}

# Category → friendly phrase for email body
CATEGORY_PHRASES = {
    "event_planner": "event planning",
    "wedding_planner": "wedding planning",
    "quinceanera_planner": "quinceañera planning",
    "catering": "catering",
    "caterer": "catering",
    "event_rental": "event rentals",
    "event_services": "event rentals",
    "event_service": "event rentals",
    "furniture_rental": "furniture rentals",
    "party_rental": "party rentals",
    "tent_rental": "tent rentals",
    "dj_entertainment": "DJ services",
    "dj": "DJ services",
    "photography": "photography",
    "photographer": "photography",
    "florist": "floral design",
    "bartending_mobile_bar": "bartending",
    "decorator": "event decorating",
    "valet": "valet services",
    "bounce_house": "bounce house rentals",
    "photo_booth": "photo booth services",
    "lighting": "event lighting",
    "food_truck": "food truck services",
    "balloon_artist": "balloon artistry",
    "officiant": "officiating",
    "hair_makeup": "hair and makeup",
    "wedding_cake": "cake design",
    "setup_crew": "event setup",
    "generator_rental": "generator rentals",
    "dance_floor": "dance floor rentals",
    "construction": "construction services",
    "festival_organizer": "festival organizing",
    "security_service": "security services",
    "videography": "videography",
    "event_venue": "event venues",
    "banquet_hall": "banquet venues",
    "country_club": "private clubs",
    "hotel": "hotel events",
    "hotel_venue": "hotel events",
    "restaurant": "restaurant events",
    "wedding_venue": "hosting events",
    "quinceanera_venue": "hosting celebrations",
    "venue": "event hosting",
    "other": "event rentals",
}


# ── Public API ─────────────────────────────────────────────────────────────────

def map_category_to_segment(category):
    # type: (str) -> str
    """Map a vendor category to a sequence segment type."""
    cat = (category or "other").strip().lower().replace(" ", "_")
    return CATEGORY_TO_SEGMENT.get(cat, "vendor")


def get_sequence(segment):
    # type: (str) -> List[Dict[str, Any]]
    """Get the email sequence steps for a segment type."""
    return SEQUENCES.get(segment, VENDOR_SEQUENCE)


def get_step(segment, step_number):
    # type: (str, int) -> Optional[Dict[str, Any]]
    """Get a specific step from a sequence (1-indexed)."""
    seq = get_sequence(segment)
    for s in seq:
        if s["step"] == step_number:
            return s
    return None


def render_email(step, vendor, sender_name="Kai"):
    # type: (Dict[str, Any], Dict[str, Any], str) -> Dict[str, str]
    """
    Render a specific sequence step for a vendor.

    Returns: {subject, plain_body, html_body, segment, step_number}
    """
    # Extract vendor fields with fallbacks
    name = vendor.get("name", vendor.get("business_name", ""))
    identity = _resolve_contact_identity(vendor)
    first_name = identity.get("first_name", "there")

    category = (vendor.get("category") or "other").strip().lower().replace(" ", "_")
    category_phrase = CATEGORY_PHRASES.get(category, "event rentals")
    city = vendor.get("city", "Los Angeles")
    if not city:
        city = "Los Angeles"

    # Pick a random subject from options, excluding script-like phrasing.
    subject_options = [s for s in step["subject_options"] if "script" not in s.lower()]
    if not subject_options:
        subject_options = list(step["subject_options"])
    subject = random.choice(subject_options)

    # Render body template
    plain_body = step["body_template"].format(
        first_name=first_name,
        business_name=name,
        city=city,
        category_phrase=category_phrase,
        your_name=sender_name,
    )
    plain_body = _apply_personalization_guards(plain_body, vendor, category_phrase=category_phrase, city=city)
    plain_body = _normalize_plain_copy(plain_body)

    # Add CAN-SPAM footer
    plain_body += CAN_SPAM_FOOTER

    # Generate simple HTML version
    html_body = _plain_to_html(plain_body, name)

    return {
        "subject": subject,
        "plain_body": plain_body,
        "html_body": html_body,
        "segment": map_category_to_segment(category),
        "step_number": step["step"],
        "name_source": identity.get("name_source", "none"),
        "name_confidence": float(identity.get("name_confidence", 0.0)),
        "name_personalized": bool(identity.get("name_personalized", False)),
        "greeting_name": first_name,
    }


def _extract_contact_first_name(raw_name):
    # type: (str) -> str
    text = (raw_name or "").strip()
    if not text:
        return ""
    token = re.sub(r"[^A-Za-z'\-]", "", text.split()[0] or "")
    if not token:
        return ""
    low = token.lower()
    if low in GENERIC_NAME_TOKENS:
        return ""
    if len(token) < 2 or len(token) > 24:
        return ""
    return token.capitalize()


def _coerce_float(v, default=0.0):
    # type: (Any, float) -> float
    try:
        return float(v)
    except Exception:
        return float(default)


def _coerce_bool(v):
    # type: (Any) -> bool
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    text = str(v or "").strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def _resolve_contact_identity(vendor):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """Return a safe greeting identity.

    Rule:
    - We only personalize with a first name when source is trusted + confidence high.
    - Otherwise force fallback to "there" (which renders "Hi there!").
    """
    source = (vendor.get("contact_name_source") or vendor.get("name_source") or vendor.get("source") or "").strip().lower()
    confidence = _coerce_float(vendor.get("contact_name_confidence", vendor.get("name_confidence", 0.0)), 0.0)
    verified = _coerce_bool(vendor.get("contact_name_verified", vendor.get("name_verified", False)))

    candidates = [
        vendor.get("trusted_first_name", ""),
        vendor.get("first_name", ""),
        vendor.get("contact_name", ""),
    ]
    first = ""
    for cand in candidates:
        parsed = _extract_contact_first_name(str(cand or ""))
        if parsed:
            first = parsed
            break

    trusted_source = source in TRUSTED_NAME_SOURCES
    name_personalized = bool(first and trusted_source and (verified or confidence >= 0.90))
    if not name_personalized:
        first = "there"
        confidence = 0.0 if not trusted_source else confidence
        source = source or "none"

    return {
        "first_name": first,
        "name_source": source or "none",
        "name_confidence": round(confidence, 2),
        "name_personalized": name_personalized,
    }


def _build_proof_line(vendor, category_phrase, city):
    # type: (Dict[str, Any], str, str) -> str
    """Build a factual line from known fields only (no fabricated claims)."""
    for key in ("proof_snippet", "proof_snippet_1", "proof_line"):
        val = (vendor.get(key) or "").strip()
        if val:
            return val
    business = (vendor.get("name") or vendor.get("business_name") or "your business").strip()
    cat = (category_phrase or "events").strip()
    loc = (city or "your area").strip()
    if business and loc:
        return f"I came across {business} in {loc}."
    if business:
        return f"I came across {business}."
    return f"I came across your {cat} business."


def _apply_personalization_guards(plain_text, vendor, category_phrase, city):
    # type: (str, Dict[str, Any], str, str) -> str
    text = (plain_text or "").replace("\r\n", "\n")
    lines = text.split("\n")
    cleaned = []

    # Drop non-evidenced claim lines.
    for line in lines:
        low = line.strip().lower()
        if low.startswith("saw you "):
            continue
        if low.startswith("saw that "):
            continue
        cleaned.append(line)

    proof_line = _build_proof_line(vendor, category_phrase=category_phrase, city=city)
    if not proof_line:
        return "\n".join(cleaned)

    out = []
    inserted = False
    for line in cleaned:
        out.append(line)
        if not inserted and re.match(r"^\s*hi\b", line, flags=re.IGNORECASE):
            out.extend(["", proof_line, ""])
            inserted = True

    if not inserted:
        out = [proof_line, ""] + out
    return "\n".join(out)


def _normalize_plain_copy(plain_text):
    # type: (str) -> str
    """Normalize punctuation/signoff to avoid AI-like dash signatures."""
    text = (plain_text or "").replace("\r\n", "\n").strip()
    text = text.replace("—", "-").replace("–", "-")
    filtered_lines = []
    for line in text.split("\n"):
        low = line.strip().lower()
        if low in {"--", "---"}:
            continue
        if "script" in low or "copy/paste" in low:
            continue
        filtered_lines.append(line)
    text = "\n".join(filtered_lines)
    text = re.sub(r"^Hi\s+([^\n]+?)\s*[-]+\s*$", r"Hi \1!", text, flags=re.MULTILINE)
    text = text.replace("Hi there -", "Hi there!")
    text = re.sub(r"^\s*[-]+\s*([A-Za-z][A-Za-z '.-]{0,40})\s*$", r"Best,\n\1", text, flags=re.MULTILINE)
    text = text.replace("\n---\n", "\n\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text + "\n"


def _plain_to_html(plain_text, vendor_name=""):
    # type: (str, str) -> str
    """Convert plain text email to simple, clean HTML (no heavy design)."""
    # Replace newlines with <br> for HTML
    body_html = plain_text.replace("\n", "<br>\n")

    return """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.6;
color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
%s
</body>
</html>""" % body_html


def get_sequence_stats():
    # type: () -> Dict[str, Any]
    """Get summary of available sequences."""
    return {
        segment: {
            "steps": len(seq),
            "total_days": seq[-1]["day_offset"] if seq else 0,
        }
        for segment, seq in SEQUENCES.items()
    }

async def process_email_sequences(db_path=None):
    # type: (Optional[str]) -> Dict[str, Any]
    """Process due follow-ups in active email sequences."""
    import sqlite3
    import asyncio
    import json
    from datetime import datetime, timedelta
    from pathlib import Path
    from core.vendor_db import save_outreach_draft, mark_outreach_sent
    from integrations.zoar_bot import send_email as smtp_send
    import logging

    log = logging.getLogger("email_sequences")
    db_path = db_path or (Path.home() / ".nexus" / "memory.db")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    cfg_path = Path.home() / ".nexus" / "config.json"
    cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    gmail = cfg.get("gmail_address", cfg.get("zoar_gmail", "zoarbathrooms@gmail.com"))
    app_pw = cfg.get("gmail_app_password", cfg.get("zoar_gmail_app_password", ""))

    if not app_pw:
        conn.close()
        return {"ok": False, "error": "No gmail_app_password", "sent": 0}

    # Fetch sequences where next_send_at is past
    rows = conn.execute("""
        SELECT es.*, v.email, v.name, v.category, v.city
        FROM email_sequences es
        JOIN vendors v ON v.id = es.vendor_id
        WHERE es.completed = 0 AND es.next_send_at <= datetime('now')
        LIMIT 20
    """).fetchall()

    if not rows:
        conn.close()
        return {"ok": True, "sent": 0}

    sent = 0
    errors = 0

    for row in rows:
        vid = row["vendor_id"]
        seg = row["sequence_type"]
        current_step = row["current_step"]
        to_email = row["email"]

        # Check unsubscribes
        unsub = conn.execute("SELECT 1 FROM email_unsubscribes WHERE email = ?", (to_email,)).fetchone()
        if unsub:
            conn.execute("UPDATE email_sequences SET completed = 1 WHERE id = ?", (row["id"],))
            conn.commit()
            continue

        # Check if already replied
        replied = conn.execute("SELECT 1 FROM vendors WHERE id = ? AND outreach_status = 'replied'", (vid,)).fetchone()
        if replied:
            conn.execute("UPDATE email_sequences SET completed = 1 WHERE id = ?", (row["id"],))
            conn.commit()
            continue

        seq = get_sequence(seg)
        if not seq or current_step >= len(seq):
            conn.execute("UPDATE email_sequences SET completed = 1 WHERE id = ?", (row["id"],))
            conn.commit()
            continue

        step_data = seq[current_step]
        vendor_data = {
            "id": vid,
            "name": row["name"],
            "category": row["category"],
            "city": row["city"],
            "email": to_email
        }

        try:
            email_data = render_email(step_data, vendor_data)
            outreach_id = save_outreach_draft(vid, "email", email_data["plain_body"])

            res = await smtp_send(gmail, app_pw, to_email, email_data["subject"], email_data["plain_body"], html_body=email_data["html_body"])

            if res.get("ok"):
                mark_outreach_sent(outreach_id)
                new_step = current_step + 1

                if new_step >= len(seq):
                    conn.execute("UPDATE email_sequences SET completed = 1, last_sent_at = datetime('now') WHERE id = ?", (row["id"],))
                else:
                    delay_days = seq[new_step].get("day_offset", 3)
                    next_send = (datetime.utcnow() + timedelta(days=delay_days)).isoformat()
                    conn.execute(
                        "UPDATE email_sequences SET current_step = ?, last_sent_at = datetime('now'), next_send_at = ? WHERE id = ?",
                        (new_step, next_send, row["id"])
                    )
                conn.commit()
                sent += 1
            else:
                errors += 1

        except Exception as e:
            log.warning("Sequence follow-up error: %s", e)
            errors += 1
            pass

        await asyncio.sleep(4)

    conn.close()
    return {"ok": True, "sent": sent, "errors": errors}
