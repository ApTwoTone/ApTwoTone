"""
Zoar Bathroom Rentals — Tier-Based Pricing Calculator

Base location: San Fernando, CA 91340
Tier 1 (0-10 mi): $1,000 individual / $1,200 venue
Tier 2 (10-20 mi): $1,200 base + mileage / $1,500 venue + mileage
Tier 3 (20+ mi): $1,500 base + mileage / individual only

Mileage formulas:
  Tier 2: base + (distance - 10) * $3/mi + $50 per 10-mile block beyond 10
  Tier 3: base + (distance - 20) * $5/mi + $50 per 10-mile block beyond 20

Deposit: $100 booking + $60 damage = $160 total
"""

import math
import logging
from urllib.request import Request, urlopen
from urllib.error import URLError
import json

log = logging.getLogger("pricing")

# ── Base coordinates: San Fernando, CA 91340 ──────────────────────────────────
BASE_LAT = 34.2819
BASE_LON = -118.4390

# ── Common SFV / Greater LA city distances (miles from San Fernando) ──────────
# Pre-calculated to avoid geocoding API calls for the most common cities
CITY_DISTANCES = {
    "san fernando": 0,
    "pacoima": 3,
    "sylmar": 4,
    "mission hills": 3,
    "arleta": 4,
    "panorama city": 5,
    "sun valley": 5,
    "north hills": 5,
    "north hollywood": 8,
    "van nuys": 7,
    "reseda": 8,
    "northridge": 6,
    "granada hills": 4,
    "chatsworth": 8,
    "canoga park": 10,
    "woodland hills": 11,
    "tarzana": 10,
    "encino": 10,
    "sherman oaks": 10,
    "studio city": 9,
    "burbank": 10,
    "glendale": 12,
    "pasadena": 15,
    "la canada": 13,
    "altadena": 14,
    "eagle rock": 13,
    "highland park": 14,
    "silverlake": 15,
    "los feliz": 14,
    "hollywood": 14,
    "west hollywood": 16,
    "beverly hills": 17,
    "santa monica": 22,
    "culver city": 20,
    "inglewood": 22,
    "torrance": 28,
    "long beach": 35,
    "downtown la": 18,
    "los angeles": 18,
    "la": 18,
    "dtla": 18,
    "malibu": 28,
    "calabasas": 14,
    "agoura hills": 18,
    "thousand oaks": 25,
    "simi valley": 15,
    "valencia": 12,
    "santa clarita": 14,
    "palmdale": 40,
    "lancaster": 45,
    "pomona": 35,
    "ontario": 40,
    "riverside": 55,
    "anaheim": 40,
    "irvine": 50,
    "ventura": 50,
    "oxnard": 45,
    "whittier": 25,
    "alhambra": 16,
    "arcadia": 18,
    "azusa": 25,
    "covina": 28,
    "west covina": 27,
    "monrovia": 20,
    "duarte": 20,
    "glendora": 25,
    "claremont": 32,
    "redondo beach": 25,
    "manhattan beach": 23,
    "hermosa beach": 24,
    "el segundo": 22,
    "marina del rey": 21,
    "venice": 22,
    "playa del rey": 21,
    "westchester": 20,
    "bel air": 15,
    "brentwood": 16,
    "westwood": 16,
    "century city": 17,
    "rancho palos verdes": 30,
    "san pedro": 30,
    "wilmington": 28,
    "carson": 26,
    "compton": 25,
    "downey": 23,
    "norwalk": 26,
    "cerritos": 28,
    "lakewood": 30,
    "signal hill": 33,
    "bell gardens": 22,
    "montebello": 20,
    "pico rivera": 22,
    "la mirada": 27,
    "fullerton": 32,
    "costa mesa": 45,
    "newport beach": 48,
    "huntington beach": 42,
    "fontana": 48,
    "san bernardino": 55,
    "rancho cucamonga": 40,
    "upland": 38,
    "la verne": 30,
    "san dimas": 28,
    "diamond bar": 32,
    "rowland heights": 30,
    "walnut": 30,
    "hacienda heights": 27,
    "west hills": 9,
    "porter ranch": 7,
    "lake balboa": 7,
    "winnetka": 8,
    "west van nuys": 7,
    "valley village": 9,
    "toluca lake": 10,
    "atwater village": 13,
    "echo park": 15,
    "boyle heights": 18,
    "east la": 19,
    "montecito heights": 15,
    "mount washington": 14,
    "glassell park": 13,
    "cypress park": 14,
    "lincoln heights": 16,
    "el sereno": 17,
    "south pasadena": 14,
    "san marino": 16,
    "temple city": 18,
    "rosemead": 18,
    "el monte": 20,
    "baldwin park": 23,
    "irwindale": 22,
    "south el monte": 21,
    "la puente": 25,
    "industry": 25,
    "city of industry": 25,
}


def get_distance_miles(city: str) -> float:
    """Get approximate distance from San Fernando to a city.

    First checks the pre-calculated lookup table, then falls back
    to Nominatim geocoding + Haversine formula.
    """
    if not city:
        return 0.0

    normalized = city.lower().strip().rstrip(",").strip()
    # Strip state suffixes
    for suffix in [", ca", ", california", " ca", " california"]:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].strip()

    # Check lookup table
    if normalized in CITY_DISTANCES:
        return float(CITY_DISTANCES[normalized])

    # Fallback: geocode with Nominatim (free, no API key)
    try:
        query = f"{city}, California, USA"
        url = f"https://nominatim.openstreetmap.org/search?q={query}&format=json&limit=1"
        req = Request(url, headers={"User-Agent": "zoar-pricing/1.0"})
        with urlopen(req, timeout=10) as resp:
            results = json.loads(resp.read())
        if results:
            lat = float(results[0]["lat"])
            lon = float(results[0]["lon"])
            return _haversine(BASE_LAT, BASE_LON, lat, lon)
    except (URLError, KeyError, IndexError, ValueError) as e:
        log.warning(f"Geocoding failed for '{city}': {e}")

    # Default: assume Tier 1 if we can't determine distance
    return 0.0


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in miles between two lat/lon points."""
    R = 3959  # Earth's radius in miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


def calculate_price(
    distance_miles: float,
    is_venue: bool = False,
) -> dict:
    """Calculate the rental price based on distance from San Fernando.

    Returns dict with: tier, base_price, mileage_surcharge, block_surcharge,
    venue_kickback, total, deposit, balance_due
    """
    if distance_miles <= 10:
        tier = 1
        if is_venue:
            base_price = 1200.0
            venue_kickback = 200.0
        else:
            base_price = 1000.0
            venue_kickback = 0.0
        mileage_surcharge = 0.0
        block_surcharge = 0.0

    elif distance_miles <= 20:
        tier = 2
        if is_venue:
            base_price = 1500.0
            venue_kickback = 300.0
        else:
            base_price = 1200.0
            venue_kickback = 0.0
        extra_miles = distance_miles - 10
        mileage_surcharge = extra_miles * 3.0
        blocks_beyond_10 = math.ceil(extra_miles / 10)
        block_surcharge = blocks_beyond_10 * 50.0

    else:
        tier = 3
        base_price = 1500.0
        venue_kickback = 0.0  # No venue partnerships at Tier 3
        is_venue = False
        extra_miles = distance_miles - 20
        mileage_surcharge = extra_miles * 5.0
        blocks_beyond_20 = math.ceil(extra_miles / 10)
        block_surcharge = blocks_beyond_20 * 50.0

    total = base_price + mileage_surcharge + block_surcharge
    deposit = 160.0  # $100 booking + $60 damage
    balance_due = total - deposit

    return {
        "tier": tier,
        "distance_miles": round(distance_miles, 1),
        "is_venue": is_venue,
        "base_price": base_price,
        "mileage_surcharge": round(mileage_surcharge, 2),
        "block_surcharge": block_surcharge,
        "venue_kickback": venue_kickback,
        "zoar_net": total - venue_kickback,
        "total": round(total, 2),
        "deposit": deposit,
        "balance_due": round(balance_due, 2),
    }


def calculate_quote(city: str, is_venue: bool = False) -> dict:
    """Full quote calculation: geocode city → get distance → calculate price."""
    distance = get_distance_miles(city)
    price = calculate_price(distance, is_venue)
    price["city"] = city
    return price


def format_price_breakdown(quote: dict) -> str:
    """Format a quote dict into a human-readable price breakdown."""
    lines = [
        f"Tier {quote['tier']} — {quote['distance_miles']} miles from San Fernando",
        f"Base rental: ${quote['base_price']:,.0f}",
    ]
    if quote["mileage_surcharge"] > 0:
        lines.append(f"Mileage surcharge: ${quote['mileage_surcharge']:,.2f}")
    if quote["block_surcharge"] > 0:
        lines.append(f"Distance block fee: ${quote['block_surcharge']:,.0f}")
    if quote["is_venue"] and quote["venue_kickback"] > 0:
        lines.append(f"(Venue partner rate — venue receives ${quote['venue_kickback']:,.0f} referral)")

    lines.append(f"─────────────")
    lines.append(f"Total: ${quote['total']:,.2f}")
    lines.append(f"Deposit to confirm: ${quote['deposit']:,.0f} ($100 booking + $60 damage)")
    lines.append(f"Balance due 14 days before event: ${quote['balance_due']:,.2f}")
    return "\n".join(lines)


# ── Event-specific feature highlights ─────────────────────────────────────────
EVENT_FEATURES = {
    "wedding": [
        "Premium interior finishes for a luxury experience",
        "Climate control (AC/heat) for guest comfort",
        "Multiple stalls — perfect for the bridal party",
        "Elegant design that matches your special day",
    ],
    "quinceañera": [
        "Climate control (AC/heat) for guest comfort",
        "Premium interior finishes",
        "Multiple stalls for large guest counts",
        "Delivery, setup, and pickup all included",
    ],
    "corporate": [
        "Professional, clean appearance",
        "Running water and premium soap",
        "Climate control for all-day comfort",
        "Multiple stalls for high attendance events",
    ],
    "backyard": [
        "No need to worry about bathroom facilities for your guests",
        "Your guests stay comfortable all event long",
        "Delivery, setup, and pickup completely handled",
        "Climate control keeps everyone happy",
    ],
    "festival": [
        "Multiple stalls for high-capacity events",
        "Climate control (AC/heat)",
        "Running water in every stall",
        "Built to handle large crowds comfortably",
    ],
    "film": [
        "Keep your crew comfortable on-site",
        "Climate control for long shoot days",
        "Multiple stalls for full production teams",
        "Available for the full duration of your production",
    ],
    "party": [
        "No need to worry about bathroom facilities",
        "Your guests stay comfortable all event long",
        "Delivery, setup, and pickup completely handled",
        "Flushing toilets, running water, and climate control",
    ],
}


def get_event_features(event_type: str) -> list:
    """Get relevant trailer features for a given event type."""
    if not event_type:
        return EVENT_FEATURES.get("party", [])
    et = event_type.lower().strip()
    for key in EVENT_FEATURES:
        if key in et:
            return EVENT_FEATURES[key]
    return EVENT_FEATURES.get("party", [])


def generate_quote_message(
    lead_name: str,
    event_type: str,
    event_date: str,
    event_city: str,
    guest_count: int = 0,
    is_venue: bool = False,
) -> tuple:
    """Generate a complete personalized quote message for a lead.

    Returns (quote_data, customer_message) where customer_message is the
    ready-to-send text to the lead.
    """
    quote = calculate_quote(event_city, is_venue)
    features = get_event_features(event_type)

    # Build personalized message signed from Zoar (not any individual)
    greeting = f"Hi {lead_name}," if lead_name else "Hi there,"
    event_label = event_type.title() if event_type else "event"
    date_line = f" on {event_date}" if event_date else ""
    guest_line = f" for {guest_count} guests" if guest_count else ""

    feature_bullets = "\n".join(f"  - {f}" for f in features[:4])

    msg = (
        f"{greeting}\n\n"
        f"Thank you for your interest in Zoar Bathroom Rentals for your "
        f"{event_label}{date_line}{guest_line}!\n\n"
        f"Our luxury restroom trailer includes:\n"
        f"{feature_bullets}\n\n"
        f"Delivery, setup, and pickup are all included.\n\n"
        f"Your quote:\n"
        f"{format_price_breakdown(quote)}\n\n"
        f"To secure your date, all we need is a ${int(quote['deposit'])} deposit "
        f"($100 booking + $60 damage deposit, both refundable per our policy).\n\n"
    )

    if event_date:
        msg += (
            f"Dates fill up quickly, especially for weekends in spring and summer. "
            f"Your date is currently available!\n\n"
        )

    msg += (
        f"Feel free to call or text us to confirm your booking or if you have any questions.\n\n"
        f"— Zoar Bathroom Rentals\n"
        f"(424) 235-8979\n"
        f"zoarbathroomrental.com"
    )

    return quote, msg
