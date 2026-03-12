"""
Centralized Vendor Blacklist — single source of truth for vendor filtering.

Used by:
  - scripts/send_outreach_batch.py (query filter + pre-send check)
  - core/vendor_enrichment.py (scoring gate)
  - scripts/lead_discovery_daemon.py (ingestion gate)

Any vendor matching these patterns should NEVER receive outreach email.
"""

import logging

log = logging.getLogger("vendor_blacklist")

# ── Name substring patterns (case-insensitive) ──────────────────────────────
# If ANY of these appear in the vendor name, the vendor is blacklisted.
BLACKLISTED_NAME_PATTERNS = [
    # Film / production / entertainment industry
    "film", "production", "movie", "cinema", "hollywood",
    "warner bros", "warner brother", "grip", "honey wagon",
    "craft service", "talent", "casting", "prop heaven",
    "set zero",
    # Stage / lighting (film equipment, not event decor)
    "lighting", "stage ", " stage", "staging",
    # Transportation (not event referral partners)
    "limousine", "limo ", " limo", "party bus",
    # Retail / non-event businesses
    "jewelry", "jeweler", "smoke shop", "liquor store",
    "gas station", "laundromat", "dry clean",
    # Trades / construction
    "plumbing", "hvac", "roofing", "u-haul",
    # Sports / recreation (not event venues)
    "shooting range", "firing", "skating", "ice rink",
    "la kings",
    # Real estate
    "real estate photo",
    # Chain hotels (too large to be referral partners)
    "marriott", "hilton", "hyatt",
]

# ── Blacklisted categories ──────────────────────────────────────────────────
# Entire categories that are NOT event referral partners.
BLACKLISTED_CATEGORIES = {
    "stage_rental", "coffee_cart", "community_center", "restaurant",
    "jewelry", "film_production", "grip", "lighting_design",
    "lighting_equipment", "construction", "bathroom_rental",
    "porta_potty_competitor", "security_service", "beauty_services",
    "character_company", "limo_service", "valet_service", "valet",
    "face_painter", "balloon_artist", "magician", "officiant",
    "live_band", "kids_party", "bridal_shop", "wedding_cake",
    "wedding_invitation", "quinceanera_dress", "airbnb_property",
    "team_building", "dessert_catering",
}

# ── Allowed categories (whitelist — safer than blacklist) ────────────────────
# Only vendors in these categories should be emailed.
ALLOWED_CATEGORIES = {
    "wedding_planner", "event_planner", "wedding_venue", "event_venue",
    "catering", "party_rental", "banquet_hall", "quinceanera_venue",
    "country_club", "photographer", "videographer", "florist",
    "dj_entertainment", "photo_booth", "tent_rental", "event_decorator",
    "event_coordinator", "wedding_coordinator",
}

# ── Blacklisted email domains ───────────────────────────────────────────────
# Government and corporate emails that won't generate referrals.
BLACKLISTED_EMAIL_DOMAINS = [
    "@burbankca.gov", "@lasvegas.gov", "@lacity.org",
    "fourthwallproduction",
]


def is_vendor_blacklisted(name, category=None, email=None):
    """Check if a vendor should be excluded from outreach.

    Returns:
        (bool, str): (is_blacklisted, reason)
    """
    name_lower = (name or "").lower()
    cat_lower = (category or "").lower()
    email_lower = (email or "").lower()

    # Check name patterns
    for pattern in BLACKLISTED_NAME_PATTERNS:
        if pattern in name_lower:
            return True, "name matches: %s" % pattern

    # Check category blacklist
    if cat_lower in BLACKLISTED_CATEGORIES:
        return True, "category blacklisted: %s" % cat_lower

    # Check category whitelist (if category exists and isn't in allowed list)
    if cat_lower and cat_lower not in ALLOWED_CATEGORIES:
        return True, "category not in allowed list: %s" % cat_lower

    # Check email domain
    for domain in BLACKLISTED_EMAIL_DOMAINS:
        if domain in email_lower:
            return True, "email domain blacklisted: %s" % domain

    return False, "ok"


def get_blacklist_sql_clauses():
    """Return SQL WHERE clauses to exclude blacklisted vendors.

    Usage in SQL:
        query = "SELECT ... FROM vendors WHERE campaign_eligible=1 " + get_blacklist_sql_clauses()
    """
    clauses = []
    for pattern in BLACKLISTED_NAME_PATTERNS:
        # Escape single quotes for SQL safety
        safe = pattern.replace("'", "''")
        clauses.append("AND LOWER(name) NOT LIKE '%%%s%%'" % safe)
    return "\n".join(clauses)


def get_category_priority_sql():
    """Return SQL CASE expression for category-based ordering.

    Lower number = higher priority = emailed first.
    """
    return """CASE LOWER(category)
        WHEN 'wedding_planner' THEN 1
        WHEN 'wedding_venue' THEN 2
        WHEN 'event_planner' THEN 3
        WHEN 'event_venue' THEN 4
        WHEN 'banquet_hall' THEN 5
        WHEN 'catering' THEN 6
        WHEN 'quinceanera_venue' THEN 7
        WHEN 'country_club' THEN 8
        WHEN 'party_rental' THEN 9
        WHEN 'photographer' THEN 10
        WHEN 'florist' THEN 11
        WHEN 'dj_entertainment' THEN 12
        ELSE 20
    END"""
