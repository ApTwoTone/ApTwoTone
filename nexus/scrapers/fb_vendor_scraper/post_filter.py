"""
FB Vendor Scraper — Post and group filtering logic.
Uses keyword matching to identify relevant vendor/event posts and filter out irrelevant groups.
Includes geo-filtering to keep only LA / SFV area groups.
"""

# ── Geo-filter keywords ─────────────────────────────────────────────────────

LOCAL_AREA_KEYWORDS = {
    # Cities / neighborhoods within ~20 mi of San Fernando
    "los angeles", "l.a.", " la ", "sfv", "san fernando",
    "calabasas", "malibu", "ventura", "burbank", "glendale",
    "pasadena", "hollywood", "sherman oaks", "encino", "woodland hills",
    "santa clarita", "tarzana", "northridge", "reseda", "van nuys",
    "panorama city", "sun valley", "sylmar", "pacoima", "arleta",
    "canoga park", "chatsworth", "granada hills", "porter ranch",
    "north hills", "lake balboa", "west hills", "winnetka",
    "mission hills", "san fernando valley",
    # Broader SoCal terms
    "socal", "southern california", "south california",
    "oc", "orange county", "inland empire",
    "long beach", "beverly hills", "santa monica", "west hollywood",
    "culver city", "inglewood", "torrance", "downey", "whittier",
    "pomona", "ontario", "rancho cucamonga", "fontana",
    # Area codes
    "818", "213", "310", "323", "626", "747", "661", "805",
}

EXCLUDE_AREA_KEYWORDS = {
    # Northern California
    "sacramento", "bay area", "san francisco", "san jose", "oakland",
    "norcal", "northern california", "fresno", "central valley",
    "stockton", "modesto", "bakersfield",
    # Other CA regions far from LA
    "san diego", "sd county",
    # Out of state
    "texas", "houston", "dallas", "austin", "louisiana", "florida",
    "miami", "orlando", "tampa", "nevada", "las vegas", "phoenix",
    "arizona", "new york", "chicago", "atlanta", "seattle",
    "portland", "denver", "philadelphia",
    # International
    "philippines", "uk", "london", "canada", "australia", "india",
    "nigeria", "ghana", "kenya",
    # Area codes for excluded regions
    "559", "916", "530", "209", "510", "408", "415",
    # Misc
    "dmv", "tri-state", "midwest",
}

# Keywords that indicate a relevant vendor or event post
CAPTURE_KEYWORDS = {
    # Core event/vendor terms
    "rental", "rent", "book", "booking", "available", "event", "wedding",
    "party", "tent", "table", "chair", "catering", "planner", "coordinator",
    "setup", "outdoor", "venue", "hire", "service", "dj", "photo booth",
    "bounce house", "generator", "lighting", "dance floor", "floral",
    "flowers", "decorator", "entertainment", "reception", "banquet",
    "canopy", "linens", "tableware", "bartender", "bar service",
    "dessert table", "balloon", "arch", "backdrop", "stage",
    "sound system", "speaker", "marquee", "gazebo", "pavilion",
    "valet", "parking service", "setup crew", "teardown",
    "event labor", "event staff",
    # Vendor self-promotion (how vendors describe themselves in posts)
    "photographer", "photography", "videographer", "videography",
    "pricing", "price", "rates", "packages", "quote",
    "portfolio", "my work", "check out my",
    "dm me", "message me", "inbox me", "contact me", "reach out",
    "now booking", "accepting bookings", "dates available",
    "special offer", "discount", "promo", "offering",
    "bridal", "bride", "groom", "ceremony", "quinceañera", "quince",
    "birthday", "baby shower", "sweet 16", "graduation",
    "corporate event", "nonprofit", "fundraiser", "gala",
    # Service-specific terms vendors use
    "mobile bar", "food truck", "taco cart", "popcorn", "cotton candy",
    "face painting", "henna", "mariachi", "band", "live music",
    "drone", "360 booth", "mirror booth", "led wall", "projector",
    "porta potty", "restroom trailer", "bathroom trailer",
    "stanchion", "red carpet", "aisle runner",
}

# Keywords that indicate a post to skip (only strong signals)
SKIP_KEYWORDS = {
    "selling my used", "for sale used", "wts",
    "job opening", "hiring now", "we're hiring", "help wanted",
    "selling used", "garage sale", "yard sale", "moving sale",
}

# Keywords that make a group a "vendor group" — every poster is a potential lead.
# In these groups, every post is likely from a vendor promoting their services,
# so we skip keyword filtering and accept any post with substance.
VENDOR_GROUP_KEYWORDS = {
    "vendor", "vendors", "vender", "venders",
    "wedding pro", "event pro",
    "wedding professional", "event professional",
    "wedding industry", "event industry",
    "wedding network", "event network",
    "wedding planner", "event planner",
    "party planner", "party planning",
    "wedding photographer",
    "rental", "rentals", "party rental",
    "catering", "caterer",
    "party central", "party supplies",
    "event services", "party vendors", "event vendors",
    "styled shoots", "event production",
    "wedding coordinator",
    "quinceañera", "quinceanera",
    "bar mitzvah", "bat mitzvah",
    # Broader group patterns — groups with these names are typically vendor-heavy
    "events in los angeles", "events in la",
    "los angeles event", "la event",
    "socal wedding", "socal event",
    "wedding and event", "weddings and events",
    "event and wedding",
    "party and vendor",
    "all vendors",
    "vendors wanted",
}

# Keywords for relevant groups (name/description must contain at least one)
GROUP_RELEVANT_KEYWORDS = {
    "event", "wedding", "party", "rental", "catering", "planning",
    "vendor", "tent", "entertainment", "dj", "photography", "venue",
    "coordination", "coordinator", "planner", "rentals", "florist",
    "floral", "photo booth", "bounce house", "decorator", "reception",
    "banquet", "bridal", "celebration",
}

# Keywords that disqualify a group
GROUP_IRRELEVANT_KEYWORDS = {
    "cars", "surfing", "fitness", "gym", "politics", "housing",
    "apartment", "real estate", "jobs board", "buy sell trade",
    "garage sale", "yard sale", "pets", "dating", "singles",
    "crypto", "bitcoin", "stocks", "forex", "weightlifting",
    "running club", "hiking", "fishing", "hunting",
}

# Category detection keywords → category value
CATEGORY_KEYWORDS = {
    "tent_rental": ["tent rental", "tent", "canopy", "marquee", "gazebo", "pavilion"],
    "party_rental": ["party rental", "table rental", "chair rental", "linen", "tableware", "rental company"],
    "event_planner": ["event planner", "event planning", "event coordinator", "event coordination"],
    "caterer": ["catering", "caterer", "food service", "chef", "meal prep", "bartender", "bar service"],
    "dj": ["dj", "disc jockey", "music", "sound system", "speaker rental"],
    "photographer": ["photographer", "photography", "videographer", "videography", "photo"],
    "bounce_house": ["bounce house", "jumper", "inflatable", "water slide"],
    "lighting": ["lighting", "string lights", "uplighting", "led", "light rental"],
    "florist": ["florist", "floral", "flowers", "flower arrangement", "bouquet"],
    "photo_booth": ["photo booth", "photobooth", "selfie station", "360 booth", "mirror booth"],
    "generator_rental": ["generator", "power rental"],
    "dance_floor": ["dance floor", "portable floor", "flooring"],
    "wedding_planner": ["wedding planner", "wedding planning", "wedding coordinator", "bridal"],
    "venue": ["venue", "event space", "banquet hall", "reception hall"],
    "decorator": ["decorator", "decoration", "decor", "backdrop", "balloon", "arch", "centerpiece"],
    "valet": ["valet", "valet parking", "parking service", "event parking"],
    "setup_crew": ["setup crew", "event setup", "event labor", "setup and teardown", "event staff", "event staffing"],
    "bathroom_rental": ["porta potty", "restroom trailer", "bathroom trailer", "portable restroom", "luxury restroom"],
}


def is_vendor_group(group_name: str) -> bool:
    """Check if the group name signals it's specifically for vendors.
    In vendor groups, every poster is a potential lead — skip keyword filtering."""
    lower = group_name.lower()
    for kw in VENDOR_GROUP_KEYWORDS:
        if kw in lower:
            return True
    return False


def is_relevant_post(text: str, group_name: str = "") -> bool:
    """Check if a post contains vendor/event-related keywords.
    In vendor groups, any post with substance is relevant."""
    if not text:
        return False
    lower = text.lower()

    # Hard skip — only for clearly irrelevant content
    for kw in SKIP_KEYWORDS:
        if kw in lower:
            return False

    # In vendor groups, accept any post with enough content
    # (the group membership itself is the signal)
    if group_name and is_vendor_group(group_name):
        return len(text) > 50

    # In non-vendor groups, require keyword match
    for kw in CAPTURE_KEYWORDS:
        if kw in lower:
            return True
    return False


def classify_post_type(text: str) -> str:
    """Determine if a post is vendor_promotion or event_announcement."""
    if not text:
        return "vendor_promotion"
    lower = text.lower()
    # Event announcement indicators: someone looking for services
    seeking_keywords = [
        "looking for", "need a", "need an", "recommend", "recommendation",
        "who knows", "anyone know", "searching for", "planning a",
        "planning an", "help me find", "suggestions for", "iso ",
        "in search of", "can anyone recommend", "does anyone know",
    ]
    for kw in seeking_keywords:
        if kw in lower:
            return "event_announcement"
    return "vendor_promotion"


def detect_category(text: str) -> str:
    """Detect the vendor category from post/profile text."""
    if not text:
        return "other"
    lower = text.lower()
    # Check each category's keywords; return first match
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return category
    return "other"


def is_relevant_group(name: str, description: str = "") -> bool:
    """Check if a Facebook group is relevant to event vendors."""
    combined = f"{name} {description}".lower()
    # Disqualify if irrelevant keywords appear
    for kw in GROUP_IRRELEVANT_KEYWORDS:
        if kw in combined:
            return False
    # Must have at least one relevant keyword
    for kw in GROUP_RELEVANT_KEYWORDS:
        if kw in combined:
            return True
    return False


def is_local_group(name: str, description: str = "") -> bool:
    """Check if a group is in the LA / SFV area (~20 mi of San Fernando).

    Logic: exclude first (hard no), then include if local keyword found,
    default to True for ambiguous groups (better to join extra than miss).
    """
    combined = f" {name} {description} ".lower()

    for kw in EXCLUDE_AREA_KEYWORDS:
        if kw in combined:
            return False

    for kw in LOCAL_AREA_KEYWORDS:
        if kw in combined:
            return True

    # No geo signal either way — default to keeping it
    return True
