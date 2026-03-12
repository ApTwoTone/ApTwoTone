#!/usr/bin/env python3
"""Generate ready-to-paste wedding directory listing content using Groq free tier.

Outputs formatted listings for: The Knot, WeddingWire, Thumbtack, Yelp.
Uses Groq AI for variation, falls back to hardcoded templates if rate-limited.

Usage:
    python scripts/generate_listings.py
"""
import json
import sys
from pathlib import Path

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BUSINESS = {
    "name": "Zoar Bathroom Rentals",
    "tagline": "Luxury Restroom Trailers for Events",
    "phone": "(424) 235-8979",
    "email": "zoarbathrooms@gmail.com",
    "website": "zoarbathroomrental.com",
    "service_area": "San Fernando Valley and Greater Los Angeles",
    "cities": "San Fernando, Encino, Burbank, Glendale, Pasadena, Santa Clarita, Malibu",
    "pricing": "Starting at $999",
    "features": [
        "Flushing toilets with real plumbing",
        "Running hot and cold water",
        "Climate control (AC and heat)",
        "Interior lighting",
        "Premium interior finishes",
        "Multiple stalls",
        "Delivery, setup, and pickup included",
    ],
    "events": "Weddings, Quinceañeras, Corporate Events, Backyard Parties, Festivals, Film Productions",
}

DIRECTORIES = {
    "The Knot": {
        "max_desc": 500,
        "tone": "warm, wedding-focused, aspirational",
        "notes": "Emphasize wedding experience, bridal party comfort, guest experience",
    },
    "WeddingWire": {
        "max_desc": 400,
        "tone": "professional, detail-oriented, trust-building",
        "notes": "Focus on service reliability, delivery included, climate control",
    },
    "Thumbtack": {
        "max_desc": 300,
        "tone": "direct, value-focused, action-oriented",
        "notes": "Highlight pricing, service area, quick response time",
    },
    "Yelp": {
        "max_desc": 400,
        "tone": "friendly, local-business, community-focused",
        "notes": "Emphasize local SFV roots, personal service, quality difference vs porta-potties",
    },
}

# Hardcoded fallback listings if Groq is unavailable
FALLBACKS = {
    "The Knot": (
        "Zoar Bathroom Rentals provides luxury restroom trailers for outdoor "
        "weddings throughout the San Fernando Valley and Greater Los Angeles. "
        "Our climate-controlled trailers feature flushing toilets, running water, "
        "premium interior finishes, and multiple stalls — giving your guests a "
        "true indoor bathroom experience at your outdoor venue. The bridal party "
        "gets a private, elegant space with mirrors and running water. We handle "
        "everything: delivery, professional setup, and pickup. Starting at $999 "
        "with delivery and setup included. Pricing varies by location."
    ),
    "WeddingWire": (
        "Luxury restroom trailer rental serving the San Fernando Valley and "
        "Greater LA. Our trailers feature climate control, flushing toilets, "
        "running water, and premium finishes — a world apart from standard "
        "portable restrooms. We handle all logistics including delivery, leveling, "
        "setup, and pickup. Ideal for outdoor weddings, vineyard ceremonies, "
        "ranch receptions, and estate celebrations. Starting at $999 with full "
        "delivery and setup included."
    ),
    "Thumbtack": (
        "Luxury restroom trailer rental for events in the San Fernando Valley "
        "and Greater LA. Flushing toilets, running water, AC/heat, premium "
        "finishes, multiple stalls. We deliver, set up, and pick up — you "
        "don't lift a finger. Starting at $999. Weddings, quinceañeras, "
        "corporate events, backyard parties, and more."
    ),
    "Yelp": (
        "We're Zoar Bathroom Rentals, a local luxury restroom trailer rental "
        "company based right here in the San Fernando Valley. Our trailers are "
        "nothing like porta-potties — think flushing toilets, running water, "
        "climate control, and premium finishes that your guests will actually "
        "appreciate. We deliver, set up, and pick everything up. Serving the "
        "Valley, Burbank, Glendale, Pasadena, and all of Greater LA. Starting "
        "at $999 with delivery included."
    ),
}


def _try_groq(directory_name, directory_info):
    """Try to generate listing content via Groq. Returns None on failure."""
    try:
        from core.key_rotation import KeyPool

        config_path = Path.home() / ".nexus" / "config.json"
        if not config_path.exists():
            return None
        config = json.loads(config_path.read_text())
        pool = KeyPool.from_config(config)
        api_key = pool.next_key("groq")
        if not api_key:
            return None

        import urllib.request

        prompt = (
            "Write a business listing description for %s on %s.\n\n"
            "Business: %s\n"
            "Service: Luxury restroom trailer rental\n"
            "Service area: %s\n"
            "Features: %s\n"
            "Events served: %s\n"
            "Pricing language: %s (never use specific amounts above this)\n"
            "Tone: %s\n"
            "Notes: %s\n"
            "Max length: %d characters\n\n"
            "RULES:\n"
            "- Never say 'all-inclusive'\n"
            "- Never include dollar amounts above $999\n"
            "- Brand name is 'Zoar Bathroom Rentals' — no individual names\n"
            "- Include 'delivery and setup included'\n"
            "- Write as the business, first person plural (we)\n"
            "- Output ONLY the listing text, no headers or formatting"
        ) % (
            BUSINESS["name"],
            directory_name,
            BUSINESS["name"],
            BUSINESS["service_area"],
            ", ".join(BUSINESS["features"]),
            BUSINESS["events"],
            BUSINESS["pricing"],
            directory_info["tone"],
            directory_info["notes"],
            directory_info["max_desc"],
        )

        payload = json.dumps({
            "model": "meta-llama/llama-4-scout-17b-16e-instruct",
            "messages": [
                {"role": "system", "content": "You write concise, compelling business directory listings."},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 500,
            "temperature": 0.7,
        }).encode()

        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": "Bearer %s" % api_key,
                "Content-Type": "application/json",
                "User-Agent": "Nexus/1.0",
            },
        )
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        content = data["choices"][0]["message"]["content"].strip()
        pool.report_success("groq", api_key)
        return content

    except Exception as e:
        print("  [Groq failed for %s: %s — using fallback]" % (directory_name, e))
        return None


def main():
    print("=" * 60)
    print("ZOAR BATHROOM RENTALS — Directory Listing Content")
    print("=" * 60)
    print()

    for directory_name, info in DIRECTORIES.items():
        print("=" * 60)
        print("=== %s ===" % directory_name)
        print("=" * 60)
        print()

        # Try AI-generated content first
        content = _try_groq(directory_name, info)
        if not content:
            content = FALLBACKS[directory_name]

        print("BUSINESS NAME: %s" % BUSINESS["name"])
        print("TAGLINE: %s" % BUSINESS["tagline"])
        print()
        print("DESCRIPTION:")
        print(content)
        print()
        print("PHONE: %s" % BUSINESS["phone"])
        print("EMAIL: %s" % BUSINESS["email"])
        print("WEBSITE: %s" % BUSINESS["website"])
        print("SERVICE AREA: %s" % BUSINESS["service_area"])
        print("PRICING: %s" % BUSINESS["pricing"])
        print()
        print("FEATURES:")
        for f in BUSINESS["features"]:
            print("  - %s" % f)
        print()
        print("EVENTS SERVED: %s" % BUSINESS["events"])
        print()
        print()

    print("=" * 60)
    print("Done. Copy each section above and paste into the respective directory.")
    print("=" * 60)


if __name__ == "__main__":
    main()
