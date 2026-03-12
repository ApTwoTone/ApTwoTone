#!/usr/bin/env python3
"""Generate 5 unique Craigslist posts for Services > Event in LA area.

Each post has a different title, opening hook, and feature ordering to avoid
Craigslist duplicate detection. Outputs ready-to-paste text.

Usage:
    python scripts/generate_cl_posts.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CONTACT = {
    "phone": "(424) 235-8979",
    "website": "zoarbathroomrental.com",
    "email": "zoarbathrooms@gmail.com",
    "area": "San Fernando Valley & Greater Los Angeles",
}

# 5 distinct post variations — different titles, hooks, feature order, CTAs
POSTS = [
    {
        "title": "Luxury Restroom Trailer Rental — Weddings & Events (San Fernando Valley)",
        "hook": (
            "Planning an outdoor wedding, quinceañera, or event? "
            "Give your guests a restroom experience that matches the "
            "elegance of your celebration."
        ),
        "features": [
            "Flushing toilets with real plumbing",
            "Running hot and cold water with sinks",
            "Climate control — AC in summer, heat in winter",
            "Premium interior finishes and lighting",
            "Multiple private stalls",
        ],
        "body": (
            "Zoar Bathroom Rentals provides luxury restroom trailers "
            "that are a world apart from standard portable restrooms. "
            "We deliver, set up, and pick up — you don't have to "
            "worry about a thing.\n\n"
            "Perfect for:\n"
            "- Outdoor weddings\n"
            "- Quinceañeras\n"
            "- Corporate events\n"
            "- Backyard parties\n"
            "- Festivals and film productions\n\n"
            "Starting at $999. Delivery and setup included. "
            "Pricing varies by event location."
        ),
        "cta": "Call or text for a free quote — we respond within minutes.",
    },
    {
        "title": "Premium Portable Bathroom for Events — NOT a Porta-Potty (LA Area)",
        "hook": (
            "Your guests deserve better than a porta-potty. Our luxury "
            "restroom trailers bring the comfort of an indoor bathroom "
            "to your outdoor event."
        ),
        "features": [
            "Climate-controlled interior (AC and heat)",
            "Real flushing toilets — not chemical tanks",
            "Running water at every sink",
            "Interior lighting and mirrors",
            "Multiple stalls for large guest counts",
        ],
        "body": (
            "Zoar Bathroom Rentals serves the San Fernando Valley and "
            "all of Greater Los Angeles. Whether it's a wedding with "
            "200 guests or an intimate backyard party, our luxury "
            "trailer handles it with style.\n\n"
            "We take care of everything:\n"
            "- Delivery to your venue\n"
            "- Professional setup and leveling\n"
            "- Pickup after your event\n\n"
            "Starting at $999 with all logistics included. "
            "Get a personalized quote based on your event location."
        ),
        "cta": "Text or call us today — your date might still be available.",
    },
    {
        "title": "Event Restroom Trailer — Luxury Upgrade for Your Next Party (SFV/LA)",
        "hook": (
            "Hosting a big event and worried about restroom capacity? "
            "Our luxury restroom trailer is the upgrade your guests "
            "will actually thank you for."
        ),
        "features": [
            "Multiple private stalls for fast turnover",
            "Real plumbing — flushing toilets and running water",
            "Air conditioning and heating built in",
            "Elegant interior with lighting and finishes",
            "Self-contained — no hookups needed",
        ],
        "body": (
            "Zoar Bathroom Rentals is a local company based in the "
            "San Fernando Valley. We provide luxury restroom trailers "
            "for events of all sizes throughout LA.\n\n"
            "Our clients use us for:\n"
            "- Weddings and receptions\n"
            "- Quinceañeras and sweet sixteens\n"
            "- Corporate gatherings\n"
            "- Birthday parties and family reunions\n"
            "- Film and production sets\n\n"
            "Starting at $999. Delivery, setup, and pickup all included. "
            "Pricing depends on your event location."
        ),
        "cta": "Reach out for a quick quote — we usually respond same day.",
    },
    {
        "title": "Restroom Trailer Rental for Weddings — Climate Controlled (Los Angeles)",
        "hook": (
            "Getting married at an outdoor venue with limited restroom "
            "access? Our luxury restroom trailer keeps your guests "
            "comfortable all day and night."
        ),
        "features": [
            "Premium finishes that complement your wedding decor",
            "Climate control keeps guests comfortable year-round",
            "Flushing toilets and running water at every station",
            "Private stalls with interior lighting and mirrors",
            "Full delivery, setup, and pickup by our team",
        ],
        "body": (
            "Zoar Bathroom Rentals specializes in luxury restroom "
            "trailers for outdoor weddings in the San Fernando Valley "
            "and Greater LA area.\n\n"
            "Why our brides love us:\n"
            "- Guests are comfortable, not complaining\n"
            "- Bridal party has a private, elegant space\n"
            "- Multiple stalls handle 100-300+ guests easily\n"
            "- We handle all logistics so you don't have to\n\n"
            "Starting at $999. Your date may still be available — "
            "dates fill up quickly for spring and summer weekends."
        ),
        "cta": "Call or text now for availability and a free personalized quote.",
    },
    {
        "title": "Luxury Bathroom Rental — Outdoor Events, Parties & Productions (LA)",
        "hook": (
            "Need a real bathroom at your outdoor event? Our luxury "
            "restroom trailer is the solution event planners and "
            "hosts have been looking for."
        ),
        "features": [
            "Real bathrooms, not porta-potties",
            "Hot and cold running water",
            "Air conditioning and heating",
            "Professional-grade interior finishes",
            "Self-contained — works anywhere",
        ],
        "body": (
            "Zoar Bathroom Rentals provides premium restroom trailers "
            "for events across Los Angeles. Based in the San Fernando "
            "Valley, we serve Encino, Burbank, Glendale, Pasadena, "
            "Santa Clarita, Malibu, and everywhere in between.\n\n"
            "Great for:\n"
            "- Backyard parties and celebrations\n"
            "- Outdoor weddings and quinceañeras\n"
            "- Corporate events and retreats\n"
            "- Film sets and productions\n"
            "- Festivals and large gatherings\n\n"
            "Starting at $999. All delivery, setup, and pickup included. "
            "We bring the luxury — you enjoy your event."
        ),
        "cta": "Get in touch for a free quote. We respond fast.",
    },
]


def main():
    print("=" * 60)
    print("ZOAR BATHROOM RENTALS — Craigslist Posts")
    print("Post in: Los Angeles > Services > Event")
    print("=" * 60)
    print()

    for i, post in enumerate(POSTS, 1):
        print("=" * 60)
        print("=== POST %d of 5 ===" % i)
        print("=" * 60)
        print()
        print("TITLE: %s" % post["title"])
        print()
        print("--- BODY (copy everything below this line) ---")
        print()
        print(post["hook"])
        print()
        print("FEATURES:")
        for f in post["features"]:
            print("✓ %s" % f)
        print()
        print(post["body"])
        print()
        print(post["cta"])
        print()
        print("📞 %s" % CONTACT["phone"])
        print("🌐 %s" % CONTACT["website"])
        print()
        print("Zoar Bathroom Rentals")
        print("San Fernando Valley & Greater Los Angeles")
        print()
        print("--- END POST %d ---" % i)
        print()
        print()

    print("=" * 60)
    print("Done. Post each one separately in: Los Angeles > Services > Event")
    print("Space them out over a few days to avoid flagging.")
    print("=" * 60)


if __name__ == "__main__":
    main()
