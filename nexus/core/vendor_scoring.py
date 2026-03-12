"""
Vendor Lead Scoring — computes referral likelihood and activity level
for FB vendor prospects. Used by fb_scraper_api on upsert and by the
vendor-leads dashboard for sorting/filtering.

Referral score (0-100): How likely this vendor is to refer bathroom rentals.
Activity level (1-5):   How established/active the vendor's business appears.

Scoring philosophy: We're looking for REFERRAL PARTNERS — vendors who serve
the same clients (outdoor events, weddings) and would naturally refer Zoar
for portable bathrooms. Highest-value partners are event planners, tent
companies, and caterers who do outdoor/remote events.
"""
import re

# Category → base referral score.
# Tier 1: Event logistics coordinators who directly book portable bathrooms.
# Tier 2: Equipment rental companies — natural cross-sell partners.
# Tier 3: Service providers present at events, can refer.
# Tier 4: Entertainment/media — less central to logistics.
# Tier 5: Venues — typically have their own facilities.
CATEGORY_SCORES = {
    "event_planner": 90,
    "wedding_planner": 90,
    "setup_crew": 85,
    "tent_rental": 85,
    "party_rental": 80,
    "bathroom_rental": 75,
    "generator_rental": 75,
    "dance_floor": 70,
    "caterer": 65,
    "decorator": 60,
    "valet": 55,
    "bounce_house": 55,
    "dj": 40,
    "lighting": 40,
    "photographer": 35,
    "florist": 35,
    "photo_booth": 35,
    "venue": 15,
    "other": 30,
}

# Cities near San Fernando / SFV for the local bonus
LOCAL_CITIES = {
    "san fernando", "sylmar", "pacoima", "arleta", "sun valley",
    "north hollywood", "van nuys", "reseda", "northridge", "granada hills",
    "chatsworth", "canoga park", "woodland hills", "tarzana", "encino",
    "sherman oaks", "studio city", "burbank", "glendale", "pasadena",
    "los angeles", "la", "sfv", "calabasas", "malibu", "santa clarita",
    "simi valley", "thousand oaks", "ventura", "oxnard",
}

# Signals in post text that indicate a strong referral partner
REFERRAL_PARTNER_SIGNALS = {
    # Collaborative language — they already refer others
    "collaborative": {
        "patterns": [
            r"\b(vendor team|preferred vendor|vendor list|vendor referral)\b",
            r"\b(love working with|worked with|teamed up with|partnered with)\b",
            r"\b(recommend|highly recommend|go-to vendor|our (preferred|favorite))\b",
        ],
        "score": 12,
    },
    # Outdoor/remote event language — these events NEED bathrooms
    "outdoor_events": {
        "patterns": [
            r"\b(outdoor (event|wedding|reception|party|setup))\b",
            r"\b(backyard (wedding|event|party))\b",
            r"\b(ranch|farm|vineyard|winery|estate|garden)\s*(wedding|event|venue)?\b",
            r"\b(tent(ed)?\s*(wedding|event|reception|party))\b",
            r"\b(open air|al fresco|under the stars)\b",
        ],
        "score": 15,
    },
    # Active booking language — busy vendor = more events = more referrals
    "active_booking": {
        "patterns": [
            r"\b(now booking|booking for|accepting bookings|dates available)\b",
            r"\b(limited (dates?|availability|spots?))\b",
            r"\b(book(ing)? (your|now|today|for 202\d))\b",
            r"\b(busy season|fully booked|waitlist)\b",
        ],
        "score": 8,
    },
    # Professional language — legitimate business
    "professional": {
        "patterns": [
            r"\b(licensed|insured|bonded|certified|permitted)\b",
            r"\b(years? (of )?experience|established|since \d{4})\b",
            r"\b(portfolio|packages?|pricing|free (quote|consultation|estimate))\b",
        ],
        "score": 5,
    },
}

# Negative signals
NEGATIVE_SIGNALS = {
    "hobbyist": {
        "patterns": [r"\b(hobby|just starting|beginner|first time|new to this)\b"],
        "score": -10,
    },
    "spam": {
        "patterns": [r"\b(mlm|join my team|opportunity|passive income|work from home)\b"],
        "score": -30,
    },
    "indoor_only": {
        "patterns": [r"\b(indoor only|ballroom|hotel (event|wedding))\b"],
        "score": -5,
    },
}


def compute_referral_score(prospect: dict) -> int:
    """Score 0-100 for how likely this vendor is to refer bathroom rentals."""
    category = (prospect.get("category") or "other").strip().lower()
    score = CATEGORY_SCORES.get(category, 30)

    # Contact info bonuses — can we actually reach them?
    if prospect.get("phone"):
        score += 3
    if prospect.get("email"):
        score += 3
    if prospect.get("website"):
        score += 2

    # Local area bonus
    city = (prospect.get("city") or "").strip().lower()
    if city and any(loc in city for loc in LOCAL_CITIES):
        score += 2

    # Event announcements are potential clients, not referral partners
    if prospect.get("post_type") == "event_announcement":
        score = max(score - 30, 5)

    # Analyze post text for referral partner signals
    post_text = prospect.get("post_content", "")
    if post_text:
        lower_text = post_text.lower()

        for signal_name, config in REFERRAL_PARTNER_SIGNALS.items():
            for pattern in config["patterns"]:
                if re.search(pattern, lower_text, re.IGNORECASE):
                    score += config["score"]
                    break  # Only count each signal category once

        for signal_name, config in NEGATIVE_SIGNALS.items():
            for pattern in config["patterns"]:
                if re.search(pattern, lower_text, re.IGNORECASE):
                    score += config["score"]  # negative values
                    break

    # Image count bonus — portfolio = professional vendor
    img = prospect.get("image_count") or 0
    if img >= 3:
        score += 3
    if img >= 6:
        score += 2

    # Business name detected = professional operation
    if prospect.get("business_name"):
        score += 3

    return max(0, min(score, 100))


def compute_activity_level(prospect: dict) -> int:
    """Score 1-5 for how established/active the vendor appears."""
    points = 0.0

    if prospect.get("business_name"):
        points += 1.0
    if prospect.get("phone"):
        points += 1.0
    if prospect.get("website"):
        points += 1.0
    if prospect.get("email"):
        points += 0.5
    if prospect.get("about"):
        points += 0.5

    img = prospect.get("image_count") or 0
    if img > 0:
        points += 0.5
    if img > 3:
        points += 0.5

    # Post text signals
    post_text = (prospect.get("post_content") or "").lower()
    if any(w in post_text for w in ["now booking", "dates available", "accepting bookings"]):
        points += 0.5
    if any(w in post_text for w in ["portfolio", "packages", "pricing"]):
        points += 0.5

    return max(1, min(round(points), 5))


def score_prospect(prospect: dict) -> dict:
    """Compute both scores. Returns dict with referral_score and activity_level."""
    return {
        "referral_score": compute_referral_score(prospect),
        "activity_level": compute_activity_level(prospect),
    }


# ── Campaign Quality Scoring ─────────────────────────────────────────────────

# Categories most likely to generate referrals for bathroom rentals
HIGH_PRIORITY_CATEGORIES = {
    "event_planner", "wedding_planner", "quinceanera_planner",
    "tent_rental", "setup_crew", "party_rental",
    "wedding_venue", "quinceanera_venue", "venue",
    "caterer", "catering",
}

MEDIUM_PRIORITY_CATEGORIES = {
    "decorator", "dj_entertainment", "dj", "photography", "photographer",
    "florist", "bartending_mobile_bar", "dance_floor", "generator_rental",
    "valet", "bounce_house", "photo_booth",
}


def compute_campaign_quality(vendor: dict) -> int:
    """Score 0-100 for how ready a vendor is for email outreach.

    Measures data completeness and segment fit — NOT referral likelihood
    (that's compute_referral_score). This determines which leads are
    worth spending an email send on.
    """
    score = 0

    # 1. Email is mandatory
    email = (vendor.get("email") or "").strip().lower()
    if email and "@" in email:
        score += 25
        # Professional domain bonus (non-generic)
        generic_domains = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com", "aol.com", "msn.com"}
        domain = email.split("@")[-1]
        if domain not in generic_domains:
            score += 5
    else:
        return 0  # No email = score 0, not eligible

    # 2. Contact info & Name completeness
    name = (vendor.get("name") or "").strip()
    if vendor.get("website") and "example" not in (vendor.get("website") or ""):
        score += 8
    if vendor.get("phone"):
        score += 8
    if vendor.get("city"):
        score += 5
    if name:
        score += 5

    # 3. High-Value Keywords in Business Name (+10)
    high_value_keywords = ["events", "weddings", "planner", "rentals", "design", "catering", "party", "venue", "production"]
    if any(kw in name.lower() for kw in high_value_keywords):
        score += 10

    # 4. Category priority
    category = (vendor.get("category") or "other").strip().lower()
    if category in HIGH_PRIORITY_CATEGORIES:
        score += 15
    elif category in MEDIUM_PRIORITY_CATEGORIES:
        score += 8
    else:
        score += 0

    # 5. Local Service Area (SFV / LA)
    city = (vendor.get("city") or "").strip().lower()
    address = (vendor.get("address") or "").strip()

    # SFV Zip Code Match (913xx, 914xx, 915xx, 916xx, 910xx, 911xx, 912xx)
    if re.search(r"9[10][0-6]\d{2}", address):
        score += 15
    elif city and any(loc in city for loc in LOCAL_CITIES):
        score += 10
    elif city:
        score += 2

    # 7. Entity Type Filtering (Corporate vs. Boutique)
    lower_name = name.lower()

    # Corporate/Chain Penalty (-25)
    corporate_keywords = [
        "hotel", "resort", "marriott", "hilton", "hyatt", "sheraton",
        "westin", "intercontinental", "ritz-carlton", "ritz carlton",
        "renaissance hotel", "doubletree", "embassy suites", "courtyard",
        "holiday inn", "fairmont", "omni", "loews", "biltmore"
    ]
    if any(ckw in lower_name for ckw in corporate_keywords):
        score -= 25

    # Boutique/Rustic Boost (+15)
    boutique_keywords = [
        "ranch", "farm", "estate", "vineyard", "winery", "barn",
        "garden", "boutique", "rustic", "manor", "oak", "hidden",
        "secret", "meadow", "grove", "canyon", "hills", "valley"
    ]
    # Check category and name for boutique signals
    if any(bkw in lower_name for bkw in boutique_keywords):
        score += 15
    elif category in ["wedding_venue", "venue"] and any(bkw in lower_name for bkw in boutique_keywords):
        score += 5  # double check for venue category

    return max(0, min(score, 100))


def get_campaign_eligible(min_quality=40, segment=None, limit=100, offset=0, db_path=None):
    """Get vendors eligible for email campaigns, filtered by quality and segment.

    Returns (vendors_list, total_count).
    """
    import sqlite3
    from pathlib import Path

    path = db_path or str(Path.home() / ".nexus" / "memory.db")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row

    # Start with vendors that have emails and haven't been contacted
    where = [
        "email != ''",
        "email IS NOT NULL",
        "website != ''",
        "website IS NOT NULL",
        "website NOT LIKE '%example%'",
        "COALESCE(website_status, -1) != 0",
        "campaign_quality >= ?",
        "(outreach_status = 'none' OR outreach_status IS NULL OR outreach_status = '')",
        "lower(name) NOT LIKE '%golf club%'",
        "lower(name) NOT LIKE '%country club%'",
        "lower(name) NOT LIKE '%resort%'",
    ]
    params = [min_quality]

    # Segment filter by category
    if segment:
        from core.email_sequences import CATEGORY_TO_SEGMENT
        cats = [cat for cat, seg in CATEGORY_TO_SEGMENT.items() if seg == segment]
        if cats:
            placeholders = ",".join("?" * len(cats))
            where.append("category IN (%s)" % placeholders)
            params.extend(cats)

    where_clause = " AND ".join(where)

    # Get total count first
    total = conn.execute(
        "SELECT COUNT(*) FROM vendors WHERE %s" % where_clause, params
    ).fetchone()[0]

    # Get the actual rows
    rows = conn.execute(
        "SELECT * FROM vendors WHERE %s ORDER BY campaign_quality DESC, rating DESC, review_count DESC "
        "LIMIT ? OFFSET ?" % where_clause,
        params + [limit, offset],
    ).fetchall()

    conn.close()

    # Return rows
    eligible = []
    for row in rows:
        vendor = dict(row)
        vendor["segment"] = segment or _infer_segment(vendor)
        eligible.append(vendor)

    return eligible, total


def _infer_segment(vendor: dict) -> str:
    """Infer the email segment for a vendor based on category."""
    from core.email_sequences import map_category_to_segment
    return map_category_to_segment(vendor.get("category", "other"))
