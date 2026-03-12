"""Booking-focused outreach targeting helpers."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re
from typing import Any, Dict, Tuple

PREFERRED_BOOKING_CITIES = (
    "moorpark",
    "simi valley",
    "thousand oaks",
    "westlake village",
    "calabasas",
    "woodland hills",
    "tarzana",
    "encino",
    "sherman oaks",
    "studio city",
    "north hollywood",
    "burbank",
    "glendale",
    "northridge",
    "granada hills",
    "mission hills",
    "van nuys",
    "reseda",
    "san fernando",
    "los angeles",
)

PREFERRED_BOOKING_CITY_RANK = {
    city: idx for idx, city in enumerate(PREFERRED_BOOKING_CITIES)
}

FAST_BOOKING_CATEGORY_RANK = {
    "wedding_venue": 0,
    "event_venue": 1,
    "banquet_hall": 2,
    "quinceanera_venue": 3,
    "party_rental": 4,
    "tent_rental": 4,
    "catering": 5,
    "event_planner": 6,
    "wedding_planner": 7,
    "community_center": 7,
    "country_club": 7,
    "church_hall": 7,
    "church": 7,
    "hotel_venue": 7,
    "winery": 7,
    "winery_venue": 7,
    "venue": 1,
}

GENERIC_CONTACT_TOKENS = {
    "account",
    "accounts",
    "blog",
    "info",
    "sales",
    "contact",
    "hello",
    "admin",
    "support",
    "webmaster",
    "office",
    "team",
    "events",
    "event",
    "weddings",
    "wedding",
    "booking",
    "bookings",
    "inquiry",
    "inquiries",
    "reservations",
    "frontdesk",
    "mail",
    "manager",
    "rentals",
    "venue",
    "support",
}

CONTACT_NAME_BLOCKLIST = {
    "about",
    "added",
    "angeles",
    "are",
    "admin",
    "agent",
    "and",
    "best",
    "business",
    "can",
    "boutique",
    "because",
    "beautiful",
    "be",
    "blog",
    "by",
    "ceo",
    "call",
    "claim",
    "click",
    "code",
    "collected",
    "contact",
    "company",
    "cookies",
    "cookie",
    "copyright",
    "conversion",
    "creating",
    "deck",
    "date",
    "details",
    "development",
    "download",
    "event",
    "events",
    "expert",
    "fairfield",
    "form",
    "founded",
    "founder",
    "frees",
    "give",
    "get",
    "google",
    "hello",
    "help",
    "home",
    "how",
    "iframe",
    "ignite",
    "illinois",
    "immediately",
    "in",
    "information",
    "invitation",
    "lockbox",
    "location",
    "locations",
    "login",
    "manager",
    "memories",
    "message",
    "more",
    "not",
    "of",
    "offer",
    "onsite",
    "operations",
    "owner",
    "our",
    "page",
    "package",
    "packages",
    "paying",
    "phone",
    "private",
    "number",
    "pixel",
    "pixels",
    "powerful",
    "quickstructure",
    "resources",
    "sales",
    "service",
    "services",
    "sign",
    "shop",
    "snippet",
    "span",
    "staff",
    "start",
    "superplatform",
    "support",
    "tag",
    "team",
    "technology",
    "the",
    "touch",
    "through",
    "to",
    "tracking",
    "trailer",
    "trailers",
    "up",
    "uploading",
    "us",
    "via",
    "venue",
    "web",
    "webmaster",
    "wedding",
    "weddings",
    "who",
    "widget",
    "with",
    "website",
    "we",
    "have",
    "legend",
    "friends",
    "lets",
    "let",
    "message",
    "my",
    "rentals",
    "send",
    "you",
    "your",
    "los",
}

CONTACT_NAME_FRAGMENT_BLOCKLIST = {
    "book",
    "collect",
    "contact",
    "cookie",
    "equestrian",
    "event",
    "googl",
    "invitat",
    "iframe",
    "lockbox",
    "login",
    "manag",
    "microserv",
    "operat",
    "pixel",
    "phone",
    "planit",
    "powerful",
    "quickstructure",
    "resour",
    "superplatform",
    "technolog",
    "track",
    "trail",
    "trimicro",
    "vendor",
    "venue",
    "wedd",
    "widget",
    "website",
}

CONTACT_ROLE_SUFFIX_TOKENS = {
    "assistant",
    "coordinator",
    "director",
    "executive",
    "manager",
    "marketing",
    "owner",
    "planner",
    "president",
    "procurement",
    "professional",
    "specialist",
}

EXTRA_HUMAN_NAME_TOKENS = {
    "ano",
    "ayla",
    "haroun",
    "irene",
    "kaelya",
    "monique",
    "nono",
    "renee",
}

PROPER_NAMES_PATH = Path("/usr/share/dict/propernames")
COMMON_WORDS_PATH = Path("/usr/share/dict/words")


@lru_cache(maxsize=1)
def _load_wordlist(path: str) -> frozenset[str]:
    try:
        return frozenset(
            line.strip().lower()
            for line in Path(path).read_text(errors="ignore").splitlines()
            if line.strip()
        )
    except Exception:
        return frozenset()


def _norm(text: Any) -> str:
    return str(text or "").strip().lower()


def normalize_phone_digits(raw: Any) -> str:
    digits = re.sub(r"[^\d]", "", str(raw or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def contact_first_name(vendor: Dict[str, Any]) -> str:
    clean_name = normalized_contact_name(vendor.get("contact_name") or "")
    if not clean_name or not looks_human_contact_name(clean_name):
        return ""
    token = re.sub(r"[^A-Za-z'\-]", "", clean_name.split()[0] or "")
    token_lower = token.lower()
    if len(token) < 2 or token_lower in GENERIC_CONTACT_TOKENS or token_lower in CONTACT_NAME_BLOCKLIST:
        return ""
    return token.capitalize()


def normalized_contact_name(raw_name: Any) -> str:
    text = re.sub(r"\s+", " ", str(raw_name or "").strip())
    if not text:
        return ""

    raw_tokens = []
    for raw in text.split():
        token = re.sub(r"[^A-Za-z'\-]", "", raw)
        if not token:
            continue
        raw_tokens.append(token)

    while raw_tokens and raw_tokens[-1].lower() in CONTACT_ROLE_SUFFIX_TOKENS:
        raw_tokens.pop()

    tokens = []
    for token in raw_tokens:
        lower = token.lower()
        if lower in CONTACT_NAME_BLOCKLIST:
            return ""
        if any(fragment in lower for fragment in CONTACT_NAME_FRAGMENT_BLOCKLIST):
            return ""
        if len(token) < 2:
            return ""
        tokens.append(token)

    if not tokens or len(tokens) > 3:
        return ""
    if len(tokens) == 1 and tokens[0].lower() in GENERIC_CONTACT_TOKENS:
        return ""
    if len(tokens) == 1 and len(tokens[0]) < 3:
        return ""
    if len(tokens) == 1 and len(tokens[0]) > 10:
        return ""

    return " ".join(token[0].upper() + token[1:].lower() for token in tokens)


def looks_human_contact_name(raw_name: Any, confidence: Any = 0, verified: bool = False) -> bool:
    clean = normalized_contact_name(raw_name)
    if not clean:
        return False

    tokens = clean.split()
    if any(len(token) > 14 for token in tokens):
        return False
    if len(tokens) >= 2 and sum(len(token) for token in tokens) > 24:
        return False

    first = tokens[0].lower()
    if verified or int(confidence or 0) >= 95:
        return True

    if first in EXTRA_HUMAN_NAME_TOKENS:
        return True

    proper_names = _load_wordlist(str(PROPER_NAMES_PATH))
    if first in proper_names:
        return True

    common_words = _load_wordlist(str(COMMON_WORDS_PATH))
    if first in common_words or first in GENERIC_CONTACT_TOKENS or first in CONTACT_NAME_BLOCKLIST:
        return False

    if len(tokens) == 1:
        return False
    if len(tokens[0]) <= 3:
        return False

    for token in tokens[1:]:
        lower = token.lower()
        if lower in GENERIC_CONTACT_TOKENS or lower in CONTACT_NAME_BLOCKLIST or lower in common_words:
            return False

    return len(tokens) >= 2


def has_named_contact(vendor: Dict[str, Any]) -> bool:
    return bool(contact_first_name(vendor))


def has_phone(vendor: Dict[str, Any]) -> bool:
    if int(vendor.get("phone_valid") or 0) == 1:
        return True
    return len(normalize_phone_digits(vendor.get("phone") or "")) >= 10


def fast_booking_category_rank(category: Any) -> int:
    cat = _norm(category).replace(" ", "_")
    if cat in FAST_BOOKING_CATEGORY_RANK:
        return FAST_BOOKING_CATEGORY_RANK[cat]
    if "venue" in cat:
        return 1
    if "rental" in cat:
        return 4
    if "cater" in cat:
        return 5
    if "planner" in cat:
        return 7
    return 9


def preferred_city_rank(city: Any) -> int:
    return PREFERRED_BOOKING_CITY_RANK.get(_norm(city), len(PREFERRED_BOOKING_CITY_RANK) + 4)


def is_fast_booking_category(category: Any) -> bool:
    return fast_booking_category_rank(category) <= 7


def booking_priority_score(vendor: Dict[str, Any], decision: Dict[str, Any]) -> int:
    category_rank = fast_booking_category_rank(vendor.get("category"))
    city_rank = preferred_city_rank(vendor.get("city"))
    quality = int(decision.get("quality_score", 0) or 0)
    referral = int(vendor.get("referral_score") or 0)

    score = quality
    score += max(0, 24 - (category_rank * 4))
    score += max(0, 12 - city_rank)
    score += 8 if has_phone(vendor) else -12
    score += 6 if has_named_contact(vendor) else 0
    score += 5 if not decision.get("generic_inbox", False) else -8
    score += min(referral, 100) // 8
    return max(0, min(100, int(score)))


def booking_priority_sort_key(vendor: Dict[str, Any], decision: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        -booking_priority_score(vendor, decision),
        fast_booking_category_rank(vendor.get("category")),
        preferred_city_rank(vendor.get("city")),
        0 if has_phone(vendor) else 1,
        0 if has_named_contact(vendor) else 1,
        1 if decision.get("generic_inbox", False) else 0,
        -(int(vendor.get("referral_score") or 0)),
        _norm(vendor.get("name")),
        int(vendor.get("id") or 0),
    )


def should_skip_fast_booking_email_candidate(
    vendor: Dict[str, Any],
    decision: Dict[str, Any],
    preflight_reason: str = "",
) -> str:
    category_rank = fast_booking_category_rank(vendor.get("category"))
    if preflight_reason:
        return "email_preflight:%s" % preflight_reason
    if not decision.get("eligible"):
        return "quality_gate"
    if category_rank > 7:
        return "category_not_fast_booking_focus"
    if decision.get("generic_inbox") and category_rank >= 5:
        return "generic_inbox_low_leverage"
    if decision.get("generic_inbox") and not has_phone(vendor):
        return "generic_inbox_no_phone"
    if category_rank >= 6 and not (has_phone(vendor) or has_named_contact(vendor)):
        return "planner_without_direct_contact"
    return ""


def recommended_outreach_angle(vendor: Dict[str, Any]) -> str:
    category_rank = fast_booking_category_rank(vendor.get("category"))
    if category_rank <= 3:
        return (
            "Ask if they already have a primary restroom trailer vendor. "
            "If yes, position Zoar as the backup/secondary option. If no, "
            "ask to be added as their preferred restroom vendor."
        )
    if category_rank <= 5:
        return (
            "Ask if outdoor clients already request restroom trailers. "
            "Position Zoar as the go-to restroom partner they can refer or "
            "bundle into event packages."
        )
    return (
        "Ask if they already use a restroom trailer vendor for outdoor or "
        "private-property events. Offer Zoar as the backup vendor for dates "
        "where the main option is booked."
    )


def call_opening_line(vendor: Dict[str, Any]) -> str:
    first_name = contact_first_name(vendor)
    category_rank = fast_booking_category_rank(vendor.get("category"))
    intro = 'Hi, I am calling on behalf of Zoar Bathroom Rentals.'

    if first_name:
        if category_rank <= 3:
            return (
                f"Hi, did I reach {first_name}? {intro} Quick question: do you "
                "already have a primary restroom trailer vendor for outdoor "
                "events or overflow bathroom capacity?"
            )
        if category_rank <= 5:
            return (
                f"Hi, did I reach {first_name}? {intro} Do your outdoor-event "
                "clients ever ask your team about restroom trailers?"
            )
        return (
            f"Hi, did I reach {first_name}? {intro} Quick question: do you "
            "already have a restroom trailer vendor you trust for outdoor "
            "or private-property events?"
        )

    if category_rank <= 3:
        return (
            "Hi, is this the events team? I am calling on behalf of Zoar "
            "Bathroom Rentals. Do you already have a primary restroom "
            "trailer vendor for outdoor dates or overflow capacity?"
        )
    if category_rank <= 5:
        return (
            "Hi, is this the right person for vendor partnerships? I am "
            "calling on behalf of Zoar Bathroom Rentals. Do your outdoor "
            "clients ever ask you about restroom trailers?"
        )
    return (
        "Hi, is this the right person for event partnerships? I am calling "
        "on behalf of Zoar Bathroom Rentals. Do you already have a restroom "
        "trailer vendor you use for outdoor events?"
    )


def next_step_hint(vendor: Dict[str, Any]) -> str:
    status = _norm(vendor.get("outreach_status"))
    if status == "sent":
        return "Call and reference the earlier email. Ask for the correct vendor-list contact if needed."
    if status == "none":
        return "Call first. If no answer, leave a short voicemail and send the backup-vendor email the same day."
    return "Call first, then send the short backup-vendor email if they ask for info."
