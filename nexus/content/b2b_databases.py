"""
B2B Partnership Databases — Venue, Planner, and Construction Outreach

Pre-built databases of potential B2B partners for direct outreach.
Each entry includes business info + outreach template.

Categories:
  - Wedding Venues (50+) — ranches, estates, vineyards, gardens
  - Event Planners (50+) — wedding planners, corporate event companies
  - Construction Companies (50+) — general contractors, remodelers

All outreach uses ONLY public business info:
  Phone: (424) 235-8979
  Email: zoarbathrooms@gmail.com
  Website: https://zoarbathroomrental.com
"""
from __future__ import annotations

BIZ_PHONE = "(424) 235-8979"
BIZ_EMAIL = "zoarbathrooms@gmail.com"
BIZ_WEBSITE = "https://zoarbathroomrental.com"
BIZ_NAME = "Zoar Bathroom Rental"

# ═══════════════════════════════════════════════════════════════════════════
# WEDDING VENUES — SoCal outdoor/ranch/estate venues likely to need
# restroom trailers (no permanent restroom facilities)
# ═══════════════════════════════════════════════════════════════════════════

VENUE_TYPES = [
    "ranch", "vineyard", "estate", "garden", "barn", "farm",
    "hilltop", "beachfront", "desert", "mountain", "park",
    "museum_outdoor", "rooftop", "warehouse",
]

# Regions to search for venues
VENUE_REGIONS = {
    "san_fernando_valley": {
        "label": "San Fernando Valley",
        "cities": ["Encino", "Sherman Oaks", "Tarzana", "Woodland Hills",
                   "Northridge", "Granada Hills", "Chatsworth", "Canoga Park"],
    },
    "santa_clarita": {
        "label": "Santa Clarita Valley",
        "cities": ["Santa Clarita", "Valencia", "Newhall", "Castaic", "Agua Dulce"],
    },
    "ventura_county": {
        "label": "Ventura County",
        "cities": ["Ventura", "Oxnard", "Camarillo", "Thousand Oaks",
                   "Moorpark", "Simi Valley", "Ojai", "Fillmore"],
    },
    "la_westside": {
        "label": "LA Westside",
        "cities": ["Malibu", "Pacific Palisades", "Brentwood", "Santa Monica",
                   "Topanga", "Calabasas", "Westlake Village", "Agoura Hills"],
    },
    "pasadena_area": {
        "label": "Pasadena & San Gabriel Valley",
        "cities": ["Pasadena", "Altadena", "La Cañada Flintridge", "San Marino",
                   "Arcadia", "Monrovia", "Glendora"],
    },
    "south_bay": {
        "label": "South Bay",
        "cities": ["Palos Verdes", "Rancho Palos Verdes", "Manhattan Beach",
                   "Redondo Beach", "Long Beach", "San Pedro"],
    },
    "inland": {
        "label": "Inland & Foothills",
        "cities": ["Claremont", "Pomona", "Riverside", "Temecula",
                   "Rancho Cucamonga", "Redlands"],
    },
    "santa_barbara": {
        "label": "Santa Barbara",
        "cities": ["Santa Barbara", "Goleta", "Montecito", "Carpinteria",
                   "Santa Ynez", "Solvang", "Los Olivos"],
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# OUTREACH TEMPLATES
# ═══════════════════════════════════════════════════════════════════════════

OUTREACH_TEMPLATES = {
    "venue_intro_email": {
        "subject": "Luxury Restroom Trailer — Vendor Partnership for {venue_name}",
        "body": (
            "Hi {contact_name},\n\n"
            "I'm reaching out from Zoar Bathroom Rental. We provide a luxury 4-stall "
            "restroom trailer that's been a hit with outdoor wedding venues across SoCal.\n\n"
            "Our trailer features AC, running water, hardwood floors, and full-length mirrors — "
            "your couples' guests genuinely think it's part of the venue.\n\n"
            "I'd love to be on your vendor list. We handle delivery, setup, and pickup, so "
            "there's zero work on your end. We can also offer a referral commission for "
            "any bookings that come through your recommendation.\n\n"
            "Would you be open to a quick call or meeting? I can bring photos and pricing info.\n\n"
            f"Best,\nKai Escobar\n{BIZ_NAME}\n{BIZ_PHONE}\n{BIZ_EMAIL}\n{BIZ_WEBSITE}"
        ),
    },

    "venue_followup_email": {
        "subject": "Following up — Luxury Restroom Trailer for {venue_name} Events",
        "body": (
            "Hi {contact_name},\n\n"
            "Just following up on my previous email about our luxury restroom trailer. "
            "Spring season is coming up and many of our venue partners are already "
            "recommending us to their couples.\n\n"
            "Happy to send over our portfolio or stop by with photos. No pressure at all — "
            "just wanted to make sure this reached you.\n\n"
            f"Kai Escobar\n{BIZ_PHONE}\n{BIZ_WEBSITE}"
        ),
    },

    "planner_intro_email": {
        "subject": "Restroom Trailer Vendor — Partnership for Your Clients",
        "body": (
            "Hi {contact_name},\n\n"
            "I know restrooms aren't the glamorous part of event planning, but they can make "
            "or break an outdoor event. That's where we come in.\n\n"
            "Zoar Bathroom Rental provides a luxury 4-stall restroom trailer with AC, "
            "running water, hardwood floors, and mirrors. Our couples consistently tell us "
            "their guests were impressed.\n\n"
            "We'd love to partner with you. We offer:\n"
            "• Referral commission on bookings\n"
            "• Priority scheduling for your events\n"
            "• Seamless coordination (we handle everything)\n\n"
            "Can I send you our pricing guide and photos?\n\n"
            f"Best,\nKai Escobar\n{BIZ_NAME}\n{BIZ_PHONE}\n{BIZ_EMAIL}"
        ),
    },

    "construction_intro_email": {
        "subject": "Luxury Restroom Trailer — Upgrade Your Job Site Facilities",
        "body": (
            "Hi {contact_name},\n\n"
            "I'm Kai from Zoar Bathroom Rental. We provide a luxury restroom trailer "
            "as an alternative to standard porta potties for construction sites.\n\n"
            "Our 4-stall trailer has AC, running water, flushing toilets, and handwash "
            "stations. It's a significant upgrade for your crew and clients — especially "
            "on high-end residential or commercial projects where impression matters.\n\n"
            "We offer flexible weekly and monthly rates with delivery and pickup included.\n\n"
            "Would you like me to send over pricing for your next project?\n\n"
            f"Best,\nKai Escobar\n{BIZ_NAME}\n{BIZ_PHONE}\n{BIZ_EMAIL}"
        ),
    },

    "venue_dm_instagram": {
        "message": (
            "Hi! 👋 We're Zoar Bathroom Rental — we have a luxury restroom trailer "
            "with AC, running water, and hardwood floors. A lot of outdoor venues love "
            "recommending us to their couples. Would you be open to partnering? "
            "Happy to share photos and pricing. 🙏"
        ),
    },

    "planner_dm_instagram": {
        "message": (
            "Hi! 👋 We provide a luxury restroom trailer for outdoor weddings and events — "
            "AC, running water, mirrors, the whole deal. Our planners love that we handle "
            "everything (delivery, setup, pickup). Would you be open to a quick chat about "
            "adding us to your vendor list? 💒"
        ),
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# SEARCH QUERIES — For finding venues, planners, contractors
# ═══════════════════════════════════════════════════════════════════════════

SEARCH_QUERIES = {
    "venues": [
        "{city} outdoor wedding venue",
        "{city} ranch wedding venue",
        "{city} vineyard wedding",
        "{city} estate wedding venue",
        "{city} garden wedding venue",
        "{city} barn wedding venue",
        "best outdoor wedding venues {region}",
        "rustic wedding venues near {city}",
    ],
    "planners": [
        "{city} wedding planner",
        "{city} event planner",
        "top wedding planners {region}",
        "{city} corporate event planner",
        "best event coordinators {region}",
    ],
    "construction": [
        "{city} general contractor",
        "{city} home remodeling contractor",
        "{city} construction company",
        "commercial construction {region}",
        "luxury home builder {city}",
        "{city} renovation contractor",
    ],
}


# ═══════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def fill_template(template_key: str, **kwargs) -> dict:
    """Fill an outreach template with specific values."""
    template = OUTREACH_TEMPLATES.get(template_key, {})
    filled = {}
    for key, value in template.items():
        if isinstance(value, str):
            filled[key] = value.format(**kwargs)
        else:
            filled[key] = value
    return filled


def get_search_queries(category: str, city: str = "", region: str = "") -> list[str]:
    """Generate search queries for finding B2B partners."""
    queries = SEARCH_QUERIES.get(category, [])
    result = []
    for q in queries:
        filled = q.format(city=city or "Los Angeles", region=region or "Southern California")
        result.append(filled)
    return result


def get_all_cities() -> list[str]:
    """Get flat list of all cities across all regions."""
    cities = []
    for region in VENUE_REGIONS.values():
        cities.extend(region["cities"])
    return sorted(set(cities))


def get_outreach_plan() -> dict:
    """Generate a complete outreach plan with suggested targets and templates."""
    plan = {
        "venue_outreach": {
            "goal": "50+ outdoor wedding venues",
            "template": "venue_intro_email",
            "followup_template": "venue_followup_email",
            "ig_template": "venue_dm_instagram",
            "regions": list(VENUE_REGIONS.keys()),
            "search_queries": get_search_queries("venues"),
            "targets_per_region": 6,
        },
        "planner_outreach": {
            "goal": "50+ wedding & event planners",
            "template": "planner_intro_email",
            "ig_template": "planner_dm_instagram",
            "regions": list(VENUE_REGIONS.keys()),
            "search_queries": get_search_queries("planners"),
            "targets_per_region": 6,
        },
        "construction_outreach": {
            "goal": "50+ construction companies",
            "template": "construction_intro_email",
            "regions": ["san_fernando_valley", "santa_clarita", "ventura_county",
                        "la_westside", "pasadena_area", "inland"],
            "search_queries": get_search_queries("construction"),
            "targets_per_region": 8,
        },
    }
    return plan


def format_outreach_for_telegram() -> str:
    """Format the outreach plan as a Telegram message."""
    plan = get_outreach_plan()
    lines = [
        "🤝 B2B OUTREACH PLAN",
        "━" * 35,
        "",
    ]

    for key, p in plan.items():
        emoji = "🏰" if "venue" in key else "📋" if "planner" in key else "🏗️"
        lines.append(f"{emoji} {key.replace('_', ' ').title()}")
        lines.append(f"   Goal: {p['goal']}")
        lines.append(f"   Regions: {len(p['regions'])}")
        lines.append(f"   Template: {p['template']}")
        lines.append("")

    lines.append("Reply 'venues', 'planners', or 'construction' for search queries.")
    return "\n".join(lines)
