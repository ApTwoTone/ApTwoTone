"""
Agent system configuration.
Reads API keys from nexus config, defines agent behavior parameters.
"""
import json
from pathlib import Path

NEXUS_CONFIG_PATH = Path.home() / ".nexus" / "config.json"
DB_PATH = Path.home() / ".nexus" / "memory.db"
AGENT_STATE_DIR = Path.home() / ".nexus" / "agents"
BROWSER_PROFILES_DIR = Path.home() / ".nexus" / "browser_state"

# Ensure dirs exist
AGENT_STATE_DIR.mkdir(parents=True, exist_ok=True)

def load_nexus_config() -> dict:
    if NEXUS_CONFIG_PATH.exists():
        return json.loads(NEXUS_CONFIG_PATH.read_text())
    return {}

# ── Business Context ───────────────────────────────────────────────────────
BUSINESS = {
    "name": "Zoar Bathroom Rentals",
    "product": "4-stall luxury restroom trailer",
    "features": [
        "Porcelain flushing toilets",
        "Running water with hot/cold",
        "LED lighting",
        "Full-length mirrors",
        "Climate control (AC and heat)",
        "Bluetooth speaker",
        "Hardwood-style flooring",
    ],
    "service_areas": [
        "Los Angeles", "San Fernando Valley", "Santa Clarita",
        "Ventura County", "Oxnard", "Santa Monica", "San Fernando",
    ],
    "pricing": {
        "standard": 1100,
        "discount": 1000,
        "floor": 900,
        "strategy": "Standard rate $1,100. Offer 10% discount ($1,000) if pushback. Never below $900.",
    },
    "contact": {
        "phone": "(424) 235-8979",
        "email": "zoarbathrooms@gmail.com",
        "website": "zoarbathroomrental.com",
    },
    "differentiators": [
        "NOT a porta-potty — luxury trailer",
        "Delivery, setup, and pickup included",
        "Professional, clean, modern",
        "Perfect for weddings, corporate, film, festivals",
    ],
}

# ── Agent Behavior ─────────────────────────────────────────────────────────
AGENT_DEFAULTS = {
    # Rate limiting (actions per hour)
    "max_comments_per_hour": 4,
    "max_dms_per_hour": 2,
    "max_posts_per_day": 3,
    # Delays (seconds)
    "min_action_delay": 45,
    "max_action_delay": 180,
    "scroll_delay_min": 3,
    "scroll_delay_max": 8,
    # Models (cost-optimized)
    "fast_model": "claude-3-haiku-20240307",     # $0.25/$1.25 per 1M — comments, short responses
    "smart_model": "claude-sonnet-4-20250514",  # $3/$15 per 1M — strategy, longer content
    "vision_model": "claude-sonnet-4-20250514", # For analyzing images/screenshots
    # Content tone
    "tone": "helpful, knowledgeable, casual but professional. NEVER salesy or pushy. Build trust first.",
    "persona": "Someone who works in the event industry and has experience with outdoor event logistics.",
}

# ── Facebook Groups to Monitor ─────────────────────────────────────────────
FACEBOOK_GROUPS = [
    # Wedding groups
    {"name": "LA Brides & Weddings", "url": "https://www.facebook.com/groups/labrides", "category": "wedding"},
    {"name": "SoCal Wedding Planning", "url": "https://www.facebook.com/groups/socalweddingplanning", "category": "wedding"},
    {"name": "Los Angeles Wedding Vendors", "url": "https://www.facebook.com/groups/laweddingvendors", "category": "wedding"},
    {"name": "California Wedding Vendors Network", "url": "https://www.facebook.com/groups/caweddingvendors", "category": "wedding"},
    # Event planning groups
    {"name": "Los Angeles Event Planners", "url": "https://www.facebook.com/groups/laeventplanners", "category": "events"},
    {"name": "SoCal Events & Entertainment", "url": "https://www.facebook.com/groups/socalevents", "category": "events"},
    {"name": "Party Planning LA", "url": "https://www.facebook.com/groups/partyplanningla", "category": "events"},
    # Vendor/Industry groups
    {"name": "Event Vendor Network SoCal", "url": "https://www.facebook.com/groups/eventvendorsocal", "category": "vendor"},
    {"name": "Construction & Contractors LA", "url": "https://www.facebook.com/groups/constructionla", "category": "construction"},
]

# ── Instagram Hashtags to Monitor ──────────────────────────────────────────
INSTAGRAM_HASHTAGS = [
    "#LAwedding", "#SoCalWedding", "#LAeventplanner", "#outdoorweddingLA",
    "#LAparty", "#VenturaWedding", "#SantaClaritaEvents", "#MalibuWedding",
    "#SanFernandoValleyEvents", "#CaliforniaWedding", "#BackyardWedding",
    "#OutdoorEventLA", "#LAcorporateevents", "#WeddingPlannerLA",
    "#LAfilmproduction", "#EventRentalsLA", "#PartyRentalsLA",
]

# ── Engagement Keywords (triggers for helpful responses) ───────────────────
ENGAGEMENT_TRIGGERS = {
    "high_intent": [
        "portable restroom", "restroom trailer", "bathroom trailer",
        "porta potty wedding", "outdoor wedding bathroom", "luxury restroom",
        "restroom rental", "portable toilet wedding",
    ],
    "medium_intent": [
        "outdoor wedding tips", "outdoor event checklist", "wedding vendor recs",
        "backyard wedding advice", "outdoor venue", "festival planning",
        "construction site facilities", "film set amenities",
    ],
    "general": [
        "wedding planning help", "outdoor event", "party planning",
        "event vendor recommendation", "corporate event outdoor",
    ],
}
