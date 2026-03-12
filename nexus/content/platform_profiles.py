"""
Wedding Platform & Business Profile Content Generator

Pre-built optimized copy for:
  - The Knot vendor profile
  - WeddingWire vendor profile
  - Google Business Profile (GBP)
  - Yelp business listing

All content uses ONLY public business info:
  Phone: (424) 235-8979
  Email: zoarbathrooms@gmail.com
  Website: https://zoarbathroomrental.com
"""
from __future__ import annotations

# ── Public Business Info ──────────────────────────────────────────────────
BIZ = {
    "name": "Zoar Bathroom Rental",
    "phone": "(424) 235-8979",
    "email": "zoarbathrooms@gmail.com",
    "website": "https://zoarbathroomrental.com",
    "address": "Los Angeles, CA",
    "service_area": "Los Angeles, San Fernando Valley, Ventura County, Santa Clarita, Santa Barbara, Oxnard, and all of Southern California",
    "hours": "Monday-Sunday: 7:00 AM - 9:00 PM",
    "established": "2024",
    "category_primary": "Restroom Trailer Rental",
    "category_secondary": "Portable Restroom Rental",
}


# ═══════════════════════════════════════════════════════════════════════════
# THE KNOT — Vendor Profile
# ═══════════════════════════════════════════════════════════════════════════

THE_KNOT = {
    "vendor_category": "Rentals",
    "subcategory": "Restroom Trailers",

    "business_name": BIZ["name"],
    "tagline": "Luxury 4-Stall Restroom Trailer for Weddings & Events",

    "about": (
        "Zoar Bathroom Rental provides a luxury 4-stall restroom trailer for weddings and "
        "special events throughout Southern California. Our trailer features air conditioning, "
        "running water with flushing toilets, hardwood-style floors, full-length mirrors, "
        "ambient lighting, and a built-in Bluetooth speaker.\n\n"
        "We understand that every detail matters on your wedding day — including guest comfort. "
        "Our trailer is designed to blend seamlessly with upscale venues, from vineyard estates "
        "to beachfront properties to backyard celebrations.\n\n"
        "What's included:\n"
        "• 4 private stalls (2 women's, 2 men's)\n"
        "• Air conditioning\n"
        "• Running water & flushing toilets\n"
        "• Hardwood-style flooring\n"
        "• Full-length mirrors\n"
        "• Ambient lighting\n"
        "• Bluetooth speaker\n"
        "• Delivery, professional setup, and pickup\n\n"
        "We serve weddings of 50 to 300+ guests comfortably. Our team handles delivery, "
        "leveling, setup, and pickup so you can focus on enjoying your day.\n\n"
        f"Serving: {BIZ['service_area']}.\n"
        f"Contact us for a free quote: {BIZ['phone']}"
    ),

    "pricing_info": (
        "Starting at $1,000 for day rentals. Weekend and multi-day packages available. "
        "Price includes delivery, setup, and pickup within our service area. "
        "Contact us for a custom quote based on your event details."
    ),

    "faqs": [
        {
            "q": "How many guests can your trailer serve?",
            "a": "Our 4-stall trailer comfortably serves events of 50-300+ guests. "
                 "For very large events (400+), we recommend adding a second unit."
        },
        {
            "q": "What do you need for delivery?",
            "a": "We need a flat surface (driveway, parking area, or level grass), water hookup "
                 "within 100 feet (or we can bring our own water tank), and vehicle access for "
                 "our truck and trailer. We handle all setup and leveling."
        },
        {
            "q": "How far in advance should I book?",
            "a": "We recommend booking 2-4 weeks in advance, especially during peak season "
                 "(March-October). Popular dates fill up fast."
        },
        {
            "q": "Do you deliver to my area?",
            "a": f"We serve {BIZ['service_area']}. If you're unsure, just give us a call and "
                  "we'll let you know."
        },
        {
            "q": "Is there a minimum rental period?",
            "a": "Our minimum is a day rental. We also offer weekend, weekly, and monthly rates "
                 "for construction sites and longer events."
        },
    ],

    "highlights": [
        "Air Conditioned",
        "Running Water",
        "Flushing Toilets",
        "Hardwood Floors",
        "Full-Length Mirrors",
        "Bluetooth Speaker",
        "Delivery & Setup Included",
        "Serves 50-300+ Guests",
    ],

    "service_areas": [
        "Los Angeles", "San Fernando Valley", "Santa Clarita",
        "Ventura", "Oxnard", "Thousand Oaks", "Malibu",
        "Pacific Palisades", "Pasadena", "Santa Barbara",
        "Calabasas", "Westlake Village", "Simi Valley",
        "Burbank", "Glendale", "Beverly Hills",
    ],
}


# ═══════════════════════════════════════════════════════════════════════════
# WEDDINGWIRE — Vendor Profile
# ═══════════════════════════════════════════════════════════════════════════

WEDDINGWIRE = {
    "vendor_category": "Event Rentals",
    "subcategory": "Portable Restrooms",

    "business_name": BIZ["name"],
    "tagline": "Your Guests Will Think It's Part of the Venue",

    "description": (
        "Zoar Bathroom Rental offers a premium luxury restroom trailer that transforms "
        "outdoor event facilities. With 4 private stalls featuring AC, running water, "
        "hardwood-style floors, and full-length mirrors, our trailer provides a 5-star "
        "restroom experience for your wedding guests.\n\n"
        "Whether you're hosting a vineyard wedding in Ventura, a backyard reception in "
        "the Valley, or a beachfront ceremony in Malibu, our trailer delivers comfort "
        "that matches your venue.\n\n"
        "Services include:\n"
        "• Full delivery, setup, leveling, and pickup\n"
        "• Fresh water tank option for remote locations\n"
        "• Day, weekend, and multi-day rentals\n"
        "• Serving all of Southern California\n\n"
        "Your guests will genuinely be impressed. That's the reaction we aim for."
    ),

    "pricing_range": "$1,000 - $2,000",
    "pricing_details": (
        "Day rental starting at $1,000. Weekend packages starting at $1,400. "
        "Weekly and monthly rates available for extended events or construction. "
        "Delivery and setup included within our service area."
    ),

    "amenities": [
        "4 Private Stalls", "Air Conditioning", "Running Water",
        "Flushing Toilets", "Hardwood-Style Floors", "Full-Length Mirrors",
        "Ambient Lighting", "Bluetooth Speaker", "Handwash Stations",
        "Paper Towels", "Hand Soap", "Trash Receptacles",
    ],
}


# ═══════════════════════════════════════════════════════════════════════════
# GOOGLE BUSINESS PROFILE
# ═══════════════════════════════════════════════════════════════════════════

GOOGLE_BUSINESS = {
    "business_name": BIZ["name"],
    "primary_category": "Portable toilet supplier",
    "additional_categories": [
        "Event planner",
        "Party equipment rental service",
        "Portable building manufacturer",
    ],

    "short_description": (
        "Luxury 4-stall restroom trailer rental for weddings, corporate events, "
        "and construction sites across Southern California. AC, running water, "
        "hardwood floors, mirrors. Delivery & setup included."
    ),

    "long_description": (
        "Zoar Bathroom Rental provides a premium luxury restroom trailer for events "
        "and job sites throughout Southern California. Our 4-stall trailer features "
        "air conditioning, running water with flushing toilets, hardwood-style "
        "flooring, full-length mirrors, ambient lighting, and a built-in Bluetooth "
        "speaker.\n\n"
        "Perfect for:\n"
        "• Weddings & receptions\n"
        "• Corporate events & galas\n"
        "• Quinceañeras & graduation parties\n"
        "• Construction & renovation sites\n"
        "• Festivals & community events\n\n"
        "We handle delivery, professional setup, and pickup. Serving Los Angeles, "
        "San Fernando Valley, Ventura County, Santa Clarita, Santa Barbara, "
        "and all of SoCal.\n\n"
        f"Call for a free quote: {BIZ['phone']}\n"
        f"Visit: {BIZ['website']}"
    ),

    "services": [
        {"name": "Day Rental", "price": "Starting at $1,000", "description": "Single day luxury restroom trailer rental with delivery and pickup."},
        {"name": "Weekend Rental", "price": "Starting at $1,400", "description": "Friday-Sunday rental for weekend events and weddings."},
        {"name": "Weekly Rental", "price": "Contact for pricing", "description": "7-day rental for construction sites and extended events."},
        {"name": "Monthly Rental", "price": "Contact for pricing", "description": "30-day rental for long-term construction or renovation projects."},
    ],

    "attributes": {
        "appointment_required": False,
        "free_estimates": True,
        "delivery": True,
        "setup_included": True,
    },

    "posts_templates": [
        {
            "type": "whats_new",
            "text": (
                "Spring wedding season is here! Book your luxury restroom trailer now. "
                "4 stalls with AC, running water, hardwood floors, and mirrors. "
                f"Free quote: {BIZ['phone']}"
            ),
            "cta": "CALL",
        },
        {
            "type": "offer",
            "text": (
                "Book a weekend rental this month and get free extended hours (early "
                "delivery Friday, late pickup Sunday). Our luxury 4-stall trailer makes "
                f"any outdoor event feel upscale. Call: {BIZ['phone']}"
            ),
            "cta": "CALL",
        },
        {
            "type": "whats_new",
            "text": (
                "Planning an outdoor event? Our luxury restroom trailer has AC, running "
                "water, hardwood floors, and mirrors. Your guests will love it. "
                f"Serving all of SoCal. {BIZ['phone']}"
            ),
            "cta": "LEARN_MORE",
        },
    ],
}


# ═══════════════════════════════════════════════════════════════════════════
# YELP BUSINESS LISTING
# ═══════════════════════════════════════════════════════════════════════════

YELP = {
    "business_name": BIZ["name"],
    "category": "Party Equipment Rentals",
    "subcategories": ["Portable Toilet Services", "Event Planning & Services"],

    "about": (
        "Luxury portable restroom trailer rental for weddings, corporate events, "
        "parties, and construction sites in Southern California.\n\n"
        "Our 4-stall trailer features AC, running water, hardwood floors, "
        "full-length mirrors, and a Bluetooth speaker. We handle delivery, "
        "setup, and pickup.\n\n"
        f"Serving: {BIZ['service_area']}\n"
        f"Free quotes: {BIZ['phone']}"
    ),

    "specialties": (
        "Luxury 4-stall restroom trailer with AC, running water, hardwood floors, "
        "and full-length mirrors. Perfect for outdoor weddings, corporate events, "
        "quinceañeras, graduations, and construction sites. Delivery and setup included."
    ),

    "history": (
        "Founded in 2024, Zoar Bathroom Rental was created to solve a common problem: "
        "outdoor events with terrible restroom options. We provide a luxury alternative "
        "to standard porta potties, delivering a 5-star restroom experience to any "
        "venue across Southern California."
    ),
}


# ═══════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def get_all_profiles() -> dict:
    """Return all platform profiles as a dictionary."""
    return {
        "the_knot": THE_KNOT,
        "weddingwire": WEDDINGWIRE,
        "google_business": GOOGLE_BUSINESS,
        "yelp": YELP,
        "business_info": BIZ,
    }


def get_profile(platform: str) -> dict:
    """Get profile for a specific platform."""
    profiles = {
        "the_knot": THE_KNOT,
        "theknot": THE_KNOT,
        "weddingwire": WEDDINGWIRE,
        "google": GOOGLE_BUSINESS,
        "gbp": GOOGLE_BUSINESS,
        "yelp": YELP,
    }
    return profiles.get(platform.lower(), {})


def format_profile_for_telegram(platform: str) -> str:
    """Format a platform profile as a Telegram-friendly message."""
    profile = get_profile(platform)
    if not profile:
        return f"❌ Unknown platform: {platform}"

    name = profile.get("business_name", BIZ["name"])
    lines = [f"📋 *{platform.upper()} PROFILE — {name}*", "━" * 35, ""]

    if "about" in profile:
        lines.append("📝 *About/Description:*")
        lines.append(profile["about"][:1000])
        lines.append("")

    if "description" in profile:
        lines.append("📝 *Description:*")
        lines.append(profile["description"][:1000])
        lines.append("")

    if "tagline" in profile:
        lines.append(f"🎯 *Tagline:* {profile['tagline']}")
        lines.append("")

    if "pricing_info" in profile:
        lines.append(f"💰 *Pricing:* {profile['pricing_info']}")
        lines.append("")

    if "pricing_details" in profile:
        lines.append(f"💰 *Pricing:* {profile['pricing_details']}")
        lines.append("")

    if "highlights" in profile:
        lines.append("✨ *Highlights:*")
        for h in profile["highlights"]:
            lines.append(f"  • {h}")
        lines.append("")

    if "faqs" in profile:
        lines.append("❓ *FAQs:*")
        for faq in profile["faqs"][:3]:
            lines.append(f"  Q: {faq['q']}")
            lines.append(f"  A: {faq['a'][:200]}")
            lines.append("")

    return "\n".join(lines)
