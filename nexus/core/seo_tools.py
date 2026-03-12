from __future__ import annotations
"""
SEO Tools & City Landing Page Generator — Zoar Bathroom Rentals

Generates all SEO assets for zoarbathroomrental.com:
- XML sitemap with city + event landing pages
- FAQ structured data (JSON-LD)
- LocalBusiness schema markup
- Unique city landing pages with local landmarks/venues
- Event-type landing pages (weddings, corporate, etc.)
- OpenGraph + Twitter meta tags
- Batch generation + write-to-disk for Cloudflare Pages deployment

All content uses (424) 235-8979 and zoarbathrooms@gmail.com.
SQLite at ~/.nexus/memory.db with WAL mode.
"""
import sqlite3
import json
import os
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

DB_PATH = Path.home() / ".nexus" / "memory.db"
LA_TZ = ZoneInfo("America/Los_Angeles")

# ── Business Constants ────────────────────────────────────────────────────────

SITE_URL = "https://zoarbathroomrental.com"
BUSINESS_NAME = "Zoar Bathroom Rentals"
BUSINESS_PHONE = "(424) 235-8979"
BUSINESS_EMAIL = "zoarbathrooms@gmail.com"
BUSINESS_DESCRIPTION = (
    "Zoar Bathroom Rentals provides luxury 4-stall restroom trailer rentals "
    "across Southern California. Our trailer features flushable porcelain toilets, "
    "running water, LED lighting, full-length mirrors, climate control AC/heat, "
    "hardwood-style flooring, and a Bluetooth speaker system. Perfect for weddings, "
    "corporate events, festivals, and private parties."
)

EVENT_TYPES = [
    "weddings", "corporate events", "festivals", "birthday parties",
    "quinceañeras", "construction sites", "film/TV production",
    "concerts", "graduation parties", "family reunions",
]

# Slugified event types for URLs
EVENT_SLUGS = {
    "weddings":           "wedding-restroom-rental",
    "corporate events":   "corporate-event-restroom-rental",
    "festivals":          "festival-restroom-rental",
    "birthday parties":   "birthday-party-restroom-rental",
    "quinceañeras":       "quinceanera-restroom-rental",
    "construction sites": "construction-site-restroom-rental",
    "film/TV production": "film-tv-production-restroom-rental",
    "concerts":           "concert-restroom-rental",
    "graduation parties": "graduation-party-restroom-rental",
    "family reunions":    "family-reunion-restroom-rental",
}

# ── City Data ─────────────────────────────────────────────────────────────────

CITY_DATA = {
    "Los Angeles": {
        "slug": "los-angeles",
        "landmarks": ["Griffith Observatory", "The Getty Center", "LA Live"],
        "venues": ["rustic ranches", "rooftop venues", "beachfront locations"],
        "nearby": ["Beverly Hills", "Santa Monica", "Pasadena"],
        "population_note": "the heart of Southern California",
    },
    "San Fernando Valley": {
        "slug": "san-fernando-valley",
        "landmarks": ["Universal Studios", "The Great Wall of LA", "Lake Balboa Park"],
        "venues": ["backyard estates", "park pavilions", "ranch-style venues"],
        "nearby": ["Burbank", "Encino", "Sherman Oaks", "Woodland Hills"],
        "population_note": "the sprawling Valley community",
    },
    "Santa Clarita": {
        "slug": "santa-clarita",
        "landmarks": ["Six Flags Magic Mountain", "Vasquez Rocks", "The MAIN theater"],
        "venues": ["vineyard weddings", "mountain-view estates", "community parks"],
        "nearby": ["Palmdale", "Lancaster", "San Fernando Valley"],
        "population_note": "one of the fastest-growing cities in LA County",
    },
    "Ventura": {
        "slug": "ventura",
        "landmarks": ["Ventura Pier", "Serra Cross Park", "San Buenaventura Mission"],
        "venues": ["oceanfront celebrations", "historic downtown venues", "botanical gardens"],
        "nearby": ["Oxnard", "Camarillo", "Thousand Oaks"],
        "population_note": "the charming coastal city of Ventura County",
    },
    "Oxnard": {
        "slug": "oxnard",
        "landmarks": ["Channel Islands Harbor", "Heritage Square", "Oxnard Beach Park"],
        "venues": ["harbor-side receptions", "strawberry field celebrations", "beachfront events"],
        "nearby": ["Ventura", "Camarillo", "Malibu"],
        "population_note": "Ventura County's largest city",
    },
    "Malibu": {
        "slug": "malibu",
        "landmarks": ["Point Dume", "Malibu Pier", "Zuma Beach"],
        "venues": ["beachfront weddings", "vineyard events", "cliffside celebrations"],
        "nearby": ["Santa Monica", "Calabasas", "Thousand Oaks"],
        "population_note": "Malibu's stunning coastal setting",
    },
    "Santa Monica": {
        "slug": "santa-monica",
        "landmarks": ["Santa Monica Pier", "Third Street Promenade", "Palisades Park"],
        "venues": ["pier-side receptions", "boutique hotel events", "oceanview rooftops"],
        "nearby": ["Malibu", "Beverly Hills", "Los Angeles"],
        "population_note": "the iconic beachside city",
    },
    "Pasadena": {
        "slug": "pasadena",
        "landmarks": ["Rose Bowl", "The Huntington Library", "Old Town Pasadena"],
        "venues": ["garden estate weddings", "historic mansion events", "arboretum celebrations"],
        "nearby": ["Glendale", "Burbank", "Los Angeles"],
        "population_note": "the Crown City with rich cultural heritage",
    },
    "Burbank": {
        "slug": "burbank",
        "landmarks": ["Warner Bros. Studios", "Walt Disney Studios", "Magnolia Park"],
        "venues": ["studio lot events", "media district gatherings", "mid-century modern venues"],
        "nearby": ["Glendale", "Pasadena", "San Fernando Valley"],
        "population_note": "the Media Capital of the World",
    },
    "Calabasas": {
        "slug": "calabasas",
        "landmarks": ["The Commons at Calabasas", "Malibu Creek State Park", "King Gillette Ranch"],
        "venues": ["luxury estate weddings", "hilltop receptions", "country club events"],
        "nearby": ["Malibu", "Woodland Hills", "Agoura Hills"],
        "population_note": "the exclusive enclave in the Santa Monica Mountains",
    },
    "Thousand Oaks": {
        "slug": "thousand-oaks",
        "landmarks": ["Gardens of the World", "Wildwood Regional Park", "Civic Arts Plaza"],
        "venues": ["garden ceremonies", "oak-shaded receptions", "cultural center events"],
        "nearby": ["Westlake Village", "Agoura Hills", "Simi Valley"],
        "population_note": "the second-largest city in Ventura County",
    },
    "Simi Valley": {
        "slug": "simi-valley",
        "landmarks": ["Ronald Reagan Presidential Library", "Strathearn Historical Park", "Corriganville Park"],
        "venues": ["presidential library events", "hilltop celebrations", "ranch weddings"],
        "nearby": ["Thousand Oaks", "Moorpark", "San Fernando Valley"],
        "population_note": "a scenic valley community surrounded by hills",
    },
    "Camarillo": {
        "slug": "camarillo",
        "landmarks": ["Camarillo Premium Outlets", "CAF SoCal Air Museum", "Spanish Hills Country Club"],
        "venues": ["country club receptions", "ranch-style weddings", "outlet district events"],
        "nearby": ["Oxnard", "Thousand Oaks", "Ventura"],
        "population_note": "a family-friendly Ventura County gem",
    },
    "Agoura Hills": {
        "slug": "agoura-hills",
        "landmarks": ["Paramount Ranch", "Chesebro Canyon", "The Canyon at Agoura Hills"],
        "venues": ["canyon weddings", "western ranch events", "live music venue gatherings"],
        "nearby": ["Calabasas", "Westlake Village", "Thousand Oaks"],
        "population_note": "the gateway to the Santa Monica Mountains",
    },
    "Westlake Village": {
        "slug": "westlake-village",
        "landmarks": ["Westlake Lake", "The Landing at Westlake Village", "Triunfo Creek Vineyards"],
        "venues": ["lakeside weddings", "vineyard receptions", "waterfront celebrations"],
        "nearby": ["Thousand Oaks", "Agoura Hills", "Calabasas"],
        "population_note": "an upscale lakeside community",
    },
    "Encino": {
        "slug": "encino",
        "landmarks": ["Encino Park", "Balboa Sports Center", "Los Encinos State Historic Park"],
        "venues": ["estate garden parties", "country club events", "park celebrations"],
        "nearby": ["Sherman Oaks", "Woodland Hills", "Calabasas"],
        "population_note": "a prestigious Valley neighborhood",
    },
    "Sherman Oaks": {
        "slug": "sherman-oaks",
        "landmarks": ["Sherman Oaks Galleria", "Van Nuys/Sherman Oaks Park", "Ventura Boulevard"],
        "venues": ["upscale restaurant buyouts", "rooftop events", "boutique venue celebrations"],
        "nearby": ["Encino", "Studio City", "Van Nuys"],
        "population_note": "a vibrant heart of the San Fernando Valley",
    },
    "Woodland Hills": {
        "slug": "woodland-hills",
        "landmarks": ["Warner Center", "Upper Las Virgenes Canyon", "Orcutt Ranch Horticultural Center"],
        "venues": ["historic ranch weddings", "corporate park events", "canyon celebrations"],
        "nearby": ["Calabasas", "Encino", "West Hills"],
        "population_note": "a beautiful West Valley community",
    },
    "Northridge": {
        "slug": "northridge",
        "landmarks": ["CSUN Campus", "Northridge Fashion Center", "Dearborn Park"],
        "venues": ["university events", "community center celebrations", "backyard gatherings"],
        "nearby": ["Granada Hills", "Woodland Hills", "Chatsworth"],
        "population_note": "home to Cal State Northridge",
    },
    "Beverly Hills": {
        "slug": "beverly-hills",
        "landmarks": ["Rodeo Drive", "Beverly Gardens Park", "Greystone Mansion"],
        "venues": ["luxury hotel ballrooms", "mansion galas", "exclusive private estates"],
        "nearby": ["West Hollywood", "Los Angeles", "Santa Monica"],
        "population_note": "the world-renowned luxury destination",
    },
    "Glendale": {
        "slug": "glendale",
        "landmarks": ["The Americana at Brand", "Forest Lawn Memorial Park", "Brand Park"],
        "venues": ["Americana-adjacent events", "hilltop receptions", "art gallery celebrations"],
        "nearby": ["Burbank", "Pasadena", "Los Angeles"],
        "population_note": "the Jewel City of Los Angeles County",
    },
    "Long Beach": {
        "slug": "long-beach",
        "landmarks": ["Queen Mary", "Aquarium of the Pacific", "Shoreline Village"],
        "venues": ["waterfront galas", "nautical-themed events", "museum receptions"],
        "nearby": ["Torrance", "Los Angeles", "Huntington Beach"],
        "population_note": "Southern California's waterfront city",
    },
    "Torrance": {
        "slug": "torrance",
        "landmarks": ["Del Amo Fashion Center", "Madrona Marsh Preserve", "Torrance Beach"],
        "venues": ["South Bay receptions", "beachside celebrations", "corporate campus events"],
        "nearby": ["Long Beach", "Redondo Beach", "Los Angeles"],
        "population_note": "the heart of the South Bay",
    },
    "Palmdale": {
        "slug": "palmdale",
        "landmarks": ["Palmdale Amphitheater", "DryTown Water Park", "Antelope Valley California Poppy Reserve"],
        "venues": ["desert-view celebrations", "amphitheater events", "ranch weddings"],
        "nearby": ["Lancaster", "Santa Clarita", "Acton"],
        "population_note": "the gateway to the Antelope Valley",
    },
    "Lancaster": {
        "slug": "lancaster",
        "landmarks": ["Antelope Valley Fairgrounds", "Lancaster Performing Arts Center", "Apollo Community Park"],
        "venues": ["fairground events", "aerospace-themed galas", "desert estate celebrations"],
        "nearby": ["Palmdale", "Santa Clarita", "Rosamond"],
        "population_note": "the Antelope Valley's cultural hub",
    },
}

ALL_CITIES = list(CITY_DATA.keys())

# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_pt() -> datetime:
    return datetime.now(LA_TZ)


def _now_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db():
    conn = _db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS seo_pages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        page_type TEXT NOT NULL,
        slug TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        meta_description TEXT DEFAULT '',
        content_html TEXT DEFAULT '',
        schema_json TEXT DEFAULT '',
        generated_at TEXT DEFAULT (datetime('now')),
        deployed_at TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_seo_pages_type ON seo_pages(page_type);
    CREATE INDEX IF NOT EXISTS idx_seo_pages_slug ON seo_pages(slug);
    """)
    conn.commit()
    conn.close()
    print("[SEO] Database table ready")

_init_db()


def _save_page(page_type: str, slug: str, title: str, meta: str,
               html: str, schema: str):
    conn = _db()
    conn.execute(
        "INSERT INTO seo_pages (page_type, slug, title, meta_description, content_html, schema_json) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(slug) DO UPDATE SET title=excluded.title, meta_description=excluded.meta_description, "
        "content_html=excluded.content_html, schema_json=excluded.schema_json, generated_at=datetime('now')",
        (page_type, slug, title, meta, html, schema)
    )
    conn.commit()
    conn.close()


# ── 1. Sitemap ────────────────────────────────────────────────────────────────

def generate_sitemap() -> str:
    """Generate XML sitemap for zoarbathroomrental.com."""
    print("[SEO] Generating sitemap")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        "  <url>",
        f"    <loc>{SITE_URL}/</loc>",
        "    <changefreq>weekly</changefreq>",
        "    <priority>1.0</priority>",
        "  </url>",
    ]

    # City landing pages
    for city, data in CITY_DATA.items():
        slug = data["slug"]
        lines += [
            "  <url>",
            f"    <loc>{SITE_URL}/luxury-restroom-rental-{slug}</loc>",
            "    <changefreq>monthly</changefreq>",
            "    <priority>0.8</priority>",
            "  </url>",
        ]

    # Event type pages
    for event, slug in EVENT_SLUGS.items():
        lines += [
            "  <url>",
            f"    <loc>{SITE_URL}/{slug}</loc>",
            "    <changefreq>monthly</changefreq>",
            "    <priority>0.8</priority>",
            "  </url>",
        ]

    lines.append("</urlset>")
    sitemap = "\n".join(lines)
    print(f"[SEO] Sitemap generated: {len(CITY_DATA)} cities + {len(EVENT_SLUGS)} event types")
    return sitemap


# ── 2. FAQ Schema ─────────────────────────────────────────────────────────────

def generate_faq_schema() -> str:
    """Generate FAQ structured data as JSON-LD."""
    print("[SEO] Generating FAQ schema")
    faqs = [
        {
            "q": "How much does a luxury restroom trailer cost?",
            "a": (
                "Our luxury 4-stall restroom trailer starts at $1,100 for a full-day rental, "
                "which includes delivery, setup, servicing, and pickup. Pricing may vary based "
                "on distance, event duration, and guest count. Call us at (424) 235-8979 for a free quote."
            ),
        },
        {
            "q": "How many guests can a 4-stall restroom trailer serve?",
            "a": (
                "Our 4-stall luxury trailer comfortably serves events of 100 to 300+ guests. "
                "Each stall operates independently with its own flushable toilet, sink, and mirror, "
                "keeping lines short and guests happy throughout your event."
            ),
        },
        {
            "q": "Do you deliver and set up the restroom trailer?",
            "a": (
                "Yes! Full delivery, professional setup, and pickup are included in every rental. "
                "Our team arrives several hours before your event to ensure everything is perfectly "
                "positioned, leveled, and ready for guests."
            ),
        },
        {
            "q": "What areas do you serve?",
            "a": (
                "We serve all of Greater Los Angeles, the San Fernando Valley, Santa Clarita, "
                "Ventura County, Oxnard, Malibu, Santa Monica, Pasadena, Beverly Hills, Long Beach, "
                "and surrounding Southern California communities. Contact us to confirm availability "
                "for your location."
            ),
        },
        {
            "q": "How far in advance should I book?",
            "a": (
                "We recommend booking 2 to 4 weeks in advance, especially during peak wedding and "
                "event season (April through October). Last-minute bookings may be available depending "
                "on our schedule — call (424) 235-8979 to check availability."
            ),
        },
        {
            "q": "What's included in the rental?",
            "a": (
                "Every rental includes the luxury 4-stall trailer with flushable porcelain toilets, "
                "running hot and cold water, LED interior lighting, full-length mirrors, climate-controlled "
                "AC and heat, hardwood-style flooring, a Bluetooth speaker, hand soap, paper towels, "
                "toilet paper, and a trash receptacle. Delivery, setup, and pickup are included."
            ),
        },
        {
            "q": "Do you need water and electric hookups on site?",
            "a": (
                "Our trailer comes with a built-in freshwater tank and waste holding tank, so no "
                "external water hookup is needed. We do require a standard 20-amp electrical outlet "
                "within 100 feet, or we can arrange a generator rental for remote locations."
            ),
        },
        {
            "q": "What types of events do you service?",
            "a": (
                "We provide luxury restroom trailers for weddings, corporate events, festivals, "
                "birthday parties, quinceañeras, construction sites, film and TV productions, "
                "concerts, graduation parties, family reunions, and any outdoor or indoor event "
                "that needs premium restroom facilities."
            ),
        },
        {
            "q": "How long can I rent the trailer for?",
            "a": (
                "Our standard rental covers a single day (up to 12 hours). We also offer multi-day "
                "and weekly rentals for festivals, construction projects, and extended productions. "
                "Contact us for multi-day pricing at (424) 235-8979."
            ),
        },
        {
            "q": "How is the trailer cleaned between rentals?",
            "a": (
                "Our trailer is thoroughly sanitized and detailed between every rental. We use "
                "hospital-grade disinfectants on all surfaces, restock all supplies, deep-clean "
                "the flooring, and ensure every fixture is spotless. Your guests will experience "
                "a fresh, pristine restroom every time."
            ),
        },
        {
            "q": "Can the trailer fit in my backyard or driveway?",
            "a": (
                "The trailer requires a flat, level surface approximately 8.5 feet wide and 22 feet "
                "long. Most driveways and backyard areas work well. Our team will coordinate placement "
                "with you before delivery to ensure a smooth setup."
            ),
        },
        {
            "q": "What makes your trailer different from a porta-potty?",
            "a": (
                "Our luxury restroom trailer is a completely different experience. Instead of a plastic "
                "box, your guests get flushable porcelain toilets, running water sinks, air conditioning, "
                "hardwood-style floors, full-length mirrors, LED lighting, and a Bluetooth sound system. "
                "It feels like a high-end indoor restroom."
            ),
        },
    ]

    schema = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": f["q"],
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": f["a"],
                },
            }
            for f in faqs
        ],
    }
    result = json.dumps(schema, indent=2)
    print(f"[SEO] FAQ schema generated: {len(faqs)} questions")
    return result


# ── 3. LocalBusiness Schema ───────────────────────────────────────────────────

def generate_local_business_schema() -> str:
    """Generate LocalBusiness structured data as JSON-LD."""
    print("[SEO] Generating LocalBusiness schema")

    # Check for review data
    avg_rating, review_count = _get_review_stats()

    schema = {
        "@context": "https://schema.org",
        "@type": "LocalBusiness",
        "additionalType": "https://schema.org/LocalBusiness",
        "name": BUSINESS_NAME,
        "description": BUSINESS_DESCRIPTION,
        "url": SITE_URL,
        "telephone": BUSINESS_PHONE,
        "email": BUSINESS_EMAIL,
        "priceRange": "$$",
        "image": f"{SITE_URL}/images/zoar-luxury-restroom-trailer.jpg",
        "address": {
            "@type": "PostalAddress",
            "addressLocality": "Los Angeles",
            "addressRegion": "CA",
            "addressCountry": "US",
        },
        "geo": {
            "@type": "GeoCoordinates",
            "latitude": "34.0522",
            "longitude": "-118.2437",
        },
        "areaServed": [
            {"@type": "City", "name": city} for city in ALL_CITIES
        ],
        "openingHoursSpecification": [
            {
                "@type": "OpeningHoursSpecification",
                "dayOfWeek": day,
                "opens": "07:00",
                "closes": "21:00",
            }
            for day in ["Monday", "Tuesday", "Wednesday", "Thursday",
                        "Friday", "Saturday", "Sunday"]
        ],
        "sameAs": [
            "https://www.instagram.com/zoarbathroomrentals",
            "https://www.facebook.com/zoarbathroomrentals",
        ],
        "hasOfferCatalog": {
            "@type": "OfferCatalog",
            "name": "Luxury Restroom Trailer Rental Services",
            "itemListElement": [
                {
                    "@type": "Offer",
                    "itemOffered": {
                        "@type": "Service",
                        "name": "Luxury 4-Stall Restroom Trailer Rental",
                        "description": (
                            "Full-day rental of our luxury 4-stall restroom trailer "
                            "with delivery, setup, and pickup included."
                        ),
                    },
                },
            ],
        },
    }

    if avg_rating and review_count:
        schema["aggregateRating"] = {
            "@type": "AggregateRating",
            "ratingValue": str(round(avg_rating, 1)),
            "reviewCount": str(review_count),
            "bestRating": "5",
            "worstRating": "1",
        }
        print(f"[SEO] Including AggregateRating: {avg_rating:.1f} ({review_count} reviews)")

    result = json.dumps(schema, indent=2)
    print("[SEO] LocalBusiness schema generated")
    return result


def _get_review_stats() -> tuple:
    """Pull review stats from DB if available."""
    try:
        conn = _db()
        row = conn.execute(
            "SELECT AVG(rating), COUNT(*) FROM review_requests "
            "WHERE status='completed' AND rating IS NOT NULL AND rating > 0"
        ).fetchone()
        conn.close()
        if row and row[0] and row[1] > 0:
            return row[0], row[1]
    except Exception:
        pass
    return None, None


# ── 4. City Landing Page ──────────────────────────────────────────────────────

def generate_city_landing_page(city: str) -> dict:
    """Generate SEO-optimized HTML content for a city landing page."""
    data = CITY_DATA.get(city)
    if not data:
        print(f"[SEO] Unknown city: {city}")
        return {}

    slug = data["slug"]
    landmarks = data["landmarks"]
    venues = data["venues"]
    nearby = data["nearby"]
    note = data["population_note"]

    page_url = f"{SITE_URL}/luxury-restroom-rental-{slug}"
    title = f"Luxury Restroom Trailer Rental in {city} | {BUSINESS_NAME}"
    meta_desc = (
        f"{city} luxury portable restroom trailer rental for weddings, events & more. "
        f"4-stall trailer with AC, hardwood floors, mirrors. Free quotes! {BUSINESS_PHONE}"
    )
    h1 = f"Luxury Restroom Trailer Rental in {city}"

    # Build unique content sections
    intro = (
        f"<p>Planning an event in {city}? {BUSINESS_NAME} delivers a premium restroom "
        f"experience to {note}. Whether you're hosting near {landmarks[0]}, "
        f"celebrating at {landmarks[1]}, or organizing an event close to {landmarks[2]}, "
        f"our luxury 4-stall restroom trailer ensures your guests enjoy the comfort "
        f"they deserve — not a standard porta-potty.</p>"
    )

    services = (
        '<h2>Our Luxury Restroom Trailer — What\'s Included</h2>'
        "<ul>"
        "<li>4 private stalls with flushable porcelain toilets</li>"
        "<li>Running hot and cold water at each sink</li>"
        "<li>Climate-controlled AC and heat for year-round comfort</li>"
        "<li>Hardwood-style flooring and LED interior lighting</li>"
        "<li>Full-length mirrors in every stall</li>"
        "<li>Bluetooth speaker system for ambient music</li>"
        "<li>Hand soap, paper towels, toilet paper, and trash receptacles</li>"
        "<li>Professional delivery, leveling, setup, and pickup</li>"
        "</ul>"
        "<p>Everything is included in one flat rate — no hidden fees, no surprises. "
        f"Call {BUSINESS_PHONE} for a free quote.</p>"
    )

    venues_str = ", ".join(venues)
    events_section = (
        f'<h2>Popular Event Types in {city}</h2>'
        f"<p>{city} hosts incredible events year-round. Our luxury restroom trailer "
        f"is the top choice for {venues_str} throughout the area. "
        f"From elegant weddings to large corporate gatherings, birthday parties to "
        f"film productions, our trailer handles events of 100 to 300+ guests with ease.</p>"
        f"<p>Popular uses in {city} include:</p>"
        "<ul>"
        "<li><strong>Weddings & Receptions</strong> — Upscale restroom facilities that match your venue's elegance</li>"
        "<li><strong>Corporate Events</strong> — Professional amenities for company picnics, retreats, and galas</li>"
        "<li><strong>Private Parties</strong> — Birthdays, quinceañeras, graduations, and family reunions</li>"
        "<li><strong>Film & TV Production</strong> — Reliable, clean facilities for cast and crew on location</li>"
        "<li><strong>Festivals & Concerts</strong> — Premium restrooms that keep attendees comfortable all day</li>"
        "</ul>"
    )

    why_section = (
        f'<h2>Why Choose {BUSINESS_NAME} for Your {city} Event</h2>'
        "<ul>"
        f"<li><strong>Local Expertise</strong> — We know {city} venues and logistics inside and out</li>"
        "<li><strong>All-Inclusive Pricing</strong> — Delivery, setup, servicing, and pickup in one price</li>"
        "<li><strong>Spotless Guarantee</strong> — Hospital-grade sanitation between every rental</li>"
        "<li><strong>Reliable Service</strong> — We arrive hours early so everything is perfect</li>"
        "<li><strong>Flexible Scheduling</strong> — Single-day, multi-day, and weekly rentals available</li>"
        "</ul>"
    )

    nearby_links = ", ".join(
        f'<a href="{SITE_URL}/luxury-restroom-rental-{CITY_DATA[n]["slug"]}">{n}</a>'
        for n in nearby if n in CITY_DATA
    )
    service_area = (
        '<h2>Service Area</h2>'
        f"<p>In addition to {city}, we deliver our luxury restroom trailer to "
        f"nearby communities including {nearby_links} and throughout "
        f"Greater Los Angeles and Ventura County.</p>"
    )

    cta = (
        '<div class="cta-section">'
        f'<h2>Get Your Free Quote</h2>'
        f"<p>Ready to elevate your {city} event with luxury restroom facilities? "
        f"Call us today at <strong><a href=\"tel:+14242358979\">{BUSINESS_PHONE}</a></strong> "
        f"or email <a href=\"mailto:{BUSINESS_EMAIL}\">{BUSINESS_EMAIL}</a> "
        f"for a free, no-obligation quote.</p>"
        '</div>'
    )

    content_html = f"{intro}\n{services}\n{events_section}\n{why_section}\n{service_area}\n{cta}"

    # Breadcrumb schema
    breadcrumb = json.dumps({
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": 1,
                "name": "Home",
                "item": f"{SITE_URL}/",
            },
            {
                "@type": "ListItem",
                "position": 2,
                "name": f"Luxury Restroom Rental in {city}",
                "item": page_url,
            },
        ],
    }, indent=2)

    result = {
        "city": city,
        "slug": slug,
        "url": page_url,
        "title": title,
        "meta_description": meta_desc,
        "h1": h1,
        "content_html": content_html,
        "schema_json": breadcrumb,
    }

    _save_page("city", f"luxury-restroom-rental-{slug}", title, meta_desc, content_html, breadcrumb)
    print(f"[SEO] City page generated: {city}")
    return result


# ── 5. Event Landing Page ─────────────────────────────────────────────────────

EVENT_CONTENT = {
    "weddings": {
        "intro": (
            "Your wedding day deserves every detail to be perfect — including the restroom "
            "experience. Zoar Bathroom Rentals provides a luxury 4-stall restroom trailer "
            "that matches the elegance of your venue. Flushable toilets, running water, mirrors, "
            "AC, and hardwood floors make our trailer indistinguishable from a high-end indoor restroom."
        ),
        "why": [
            "Elegant design complements any wedding venue or theme",
            "Keeps bridal party and guests comfortable all day and night",
            "Eliminates long lines — 4 stalls serve 100-300+ guests",
            "Perfect for outdoor, barn, vineyard, and estate weddings",
        ],
    },
    "corporate events": {
        "intro": (
            "Make a professional impression at your next company event. Zoar Bathroom Rentals "
            "delivers luxury restroom facilities that reflect your organization's standards. "
            "Our 4-stall trailer with climate control, running water, and premium finishes "
            "keeps employees and clients comfortable at off-site retreats, picnics, and galas."
        ),
        "why": [
            "Professional-grade amenities for company events of any size",
            "Climate-controlled comfort for outdoor corporate retreats",
            "Quick, reliable setup that fits your event timeline",
            "Impresses clients and boosts employee satisfaction",
        ],
    },
    "festivals": {
        "intro": (
            "Festivals demand reliable, high-capacity restroom solutions. Zoar Bathroom Rentals "
            "offers a luxury 4-stall trailer that provides a premium experience for festival-goers, "
            "keeping them comfortable and happy throughout multi-day events. Our built-in tanks "
            "and sturdy construction handle high-traffic use with ease."
        ),
        "why": [
            "High-capacity 4-stall design handles large festival crowds",
            "Built-in freshwater and waste tanks for remote locations",
            "Multi-day rental options for weekend and week-long festivals",
            "Dramatically improves attendee satisfaction over porta-potties",
        ],
    },
    "birthday parties": {
        "intro": (
            "Throw the ultimate birthday bash with luxury restroom facilities that impress your "
            "guests. Whether it's a milestone celebration, a backyard BBQ, or a themed party, "
            "Zoar's restroom trailer adds a touch of class. No one wants to use a porta-potty "
            "at a party — give your guests the premium experience."
        ),
        "why": [
            "Elevates any backyard or outdoor birthday celebration",
            "Kid-friendly and family-safe facilities",
            "Perfect for milestone birthdays — Sweet 16, 30th, 50th, and more",
            "Your guests will be talking about the restrooms (in a good way)",
        ],
    },
    "quinceañeras": {
        "intro": (
            "A quinceañera is one of the most important celebrations in a young woman's life. "
            "Ensure every detail is elegant with Zoar Bathroom Rentals. Our luxury restroom trailer "
            "provides a beautiful, comfortable space for the quinceañera and all her guests, "
            "with mirrors for touch-ups and a Bluetooth speaker for ambient music."
        ),
        "why": [
            "Elegant facilities that match the importance of the celebration",
            "Full-length mirrors perfect for outfit touch-ups and photos",
            "Serves the large guest lists that quinceañeras are known for",
            "Bluetooth speaker adds ambiance with your party's music",
        ],
    },
    "construction sites": {
        "intro": (
            "Keep your crew comfortable and compliant with premium restroom facilities on the "
            "job site. Zoar Bathroom Rentals provides a luxury 4-stall trailer that goes far "
            "beyond a standard construction site porta-potty. Running water, climate control, "
            "and a sanitary environment improve morale and meet OSHA requirements."
        ),
        "why": [
            "Exceeds OSHA sanitation requirements for job sites",
            "Running water and soap promote better hygiene on site",
            "Climate-controlled for summer heat and winter cold",
            "Weekly rental options fit project timelines and budgets",
        ],
    },
    "film/TV production": {
        "intro": (
            "Production schedules are demanding, and your cast and crew deserve reliable, "
            "comfortable facilities. Zoar Bathroom Rentals is trusted by SoCal productions "
            "for on-location restroom solutions. Our luxury trailer provides a quiet, "
            "private space with AC, running water, and professional-grade cleanliness."
        ),
        "why": [
            "Trusted by film and TV productions throughout Southern California",
            "Quiet operation won't interfere with sound recording",
            "AC and heat keep talent comfortable during long shoot days",
            "Quick setup and teardown to match production schedules",
        ],
    },
    "concerts": {
        "intro": (
            "Concert-goers expect more than a basic portable toilet. Zoar Bathroom Rentals "
            "delivers a VIP restroom experience for concerts and live music events. Our luxury "
            "trailer can be designated as an artist/VIP restroom or offered as a premium "
            "upgrade for general admission attendees."
        ),
        "why": [
            "VIP restroom option for artists, performers, and backstage crew",
            "Handles high-volume traffic at large concert events",
            "Premium amenities create a memorable concert experience",
            "Built-in tanks work in any outdoor venue or field",
        ],
    },
    "graduation parties": {
        "intro": (
            "Celebrate your graduate's achievement with a party that has every detail covered. "
            "Zoar Bathroom Rentals ensures your backyard or outdoor graduation party has clean, "
            "comfortable restroom facilities. No cramped porta-potties — just a beautiful trailer "
            "with all the amenities your guests expect."
        ),
        "why": [
            "Perfect addition to backyard and outdoor graduation parties",
            "Handles the large guest lists graduation celebrations attract",
            "Clean, modern facilities that parents and grandparents appreciate",
            "Hassle-free setup lets you focus on celebrating",
        ],
    },
    "family reunions": {
        "intro": (
            "Family reunions bring everyone together — make sure the facilities are up to the "
            "occasion. Zoar Bathroom Rentals provides a luxury restroom trailer that keeps "
            "every generation comfortable, from grandparents to grandkids. Our trailer is "
            "easy to access, clean, and fully stocked for a full day of family fun."
        ),
        "why": [
            "Accessible and comfortable for guests of all ages",
            "Serves large family gatherings of 100-300+ people",
            "Clean facilities that everyone in the family will appreciate",
            "All-inclusive pricing makes budgeting for the reunion easy",
        ],
    },
}


def generate_event_landing_page(event_type: str) -> dict:
    """Generate SEO-optimized HTML content for an event-type landing page."""
    slug = EVENT_SLUGS.get(event_type)
    content_data = EVENT_CONTENT.get(event_type)
    if not slug or not content_data:
        print(f"[SEO] Unknown event type: {event_type}")
        return {}

    # Title case for display
    display_name = event_type.title() if event_type != "film/TV production" else "Film & TV Production"
    page_url = f"{SITE_URL}/{slug}"
    title = f"{display_name} Restroom Trailer Rental | {BUSINESS_NAME}"
    meta_desc = (
        f"Luxury restroom trailer rental for {event_type}. 4-stall trailer with flushable "
        f"toilets, AC, mirrors, running water. Serving all of SoCal. {BUSINESS_PHONE}"
    )
    h1 = f"{display_name} Restroom Trailer Rental"

    intro = f"<p>{content_data['intro']}</p>"

    features = (
        '<h2>What You Get with Every Rental</h2>'
        "<ul>"
        "<li>4 private stalls with flushable porcelain toilets</li>"
        "<li>Running hot and cold water at every sink</li>"
        "<li>Air conditioning and heat for all-season comfort</li>"
        "<li>Hardwood-style flooring and LED lighting</li>"
        "<li>Full-length mirrors, hand soap, paper towels, and all supplies</li>"
        "<li>Bluetooth speaker system</li>"
        "<li>Delivery, professional setup, and pickup included</li>"
        "</ul>"
    )

    why_items = "".join(f"<li>{item}</li>" for item in content_data["why"])
    why_section = (
        f'<h2>Why Our Trailer Is Perfect for {display_name}</h2>'
        f"<ul>{why_items}</ul>"
    )

    # Cities served section
    city_links = ", ".join(
        f'<a href="{SITE_URL}/luxury-restroom-rental-{CITY_DATA[c]["slug"]}">{c}</a>'
        for c in ALL_CITIES[:12]
    )
    areas = (
        '<h2>Areas We Serve</h2>'
        f"<p>We provide luxury restroom trailer rentals for {event_type} in "
        f"{city_links}, and throughout Southern California.</p>"
    )

    cta = (
        '<div class="cta-section">'
        f'<h2>Book Your {display_name} Restroom Trailer</h2>'
        f"<p>Don't settle for a porta-potty at your next event. Call "
        f"<strong><a href=\"tel:+14242358979\">{BUSINESS_PHONE}</a></strong> "
        f"or email <a href=\"mailto:{BUSINESS_EMAIL}\">{BUSINESS_EMAIL}</a> "
        f"for a free quote today.</p>"
        '</div>'
    )

    content_html = f"{intro}\n{features}\n{why_section}\n{areas}\n{cta}"

    breadcrumb = json.dumps({
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": 1,
                "name": "Home",
                "item": f"{SITE_URL}/",
            },
            {
                "@type": "ListItem",
                "position": 2,
                "name": f"{display_name} Restroom Rental",
                "item": page_url,
            },
        ],
    }, indent=2)

    result = {
        "event_type": event_type,
        "slug": slug,
        "url": page_url,
        "title": title,
        "meta_description": meta_desc,
        "h1": h1,
        "content_html": content_html,
        "schema_json": breadcrumb,
    }

    _save_page("event", slug, title, meta_desc, content_html, breadcrumb)
    print(f"[SEO] Event page generated: {event_type}")
    return result


# ── 6. Meta Tags ──────────────────────────────────────────────────────────────

def generate_meta_tags(page_type: str, value: str) -> dict:
    """Generate og:, twitter:, and canonical meta tags for a page."""
    if page_type == "city":
        data = CITY_DATA.get(value, {})
        slug = data.get("slug", value.lower().replace(" ", "-"))
        url = f"{SITE_URL}/luxury-restroom-rental-{slug}"
        title = f"Luxury Restroom Trailer Rental in {value} | {BUSINESS_NAME}"
        description = (
            f"{value} luxury portable restroom trailer rental for weddings, events & more. "
            f"4-stall trailer with AC, hardwood floors, mirrors. {BUSINESS_PHONE}"
        )
    elif page_type == "event":
        slug = EVENT_SLUGS.get(value, value.lower().replace(" ", "-").replace("/", "-"))
        url = f"{SITE_URL}/{slug}"
        display = value.title() if value != "film/TV production" else "Film & TV Production"
        title = f"{display} Restroom Trailer Rental | {BUSINESS_NAME}"
        description = (
            f"Luxury restroom trailer rental for {value}. 4-stall trailer with flushable "
            f"toilets, AC, mirrors, running water. Serving SoCal. {BUSINESS_PHONE}"
        )
    else:
        url = f"{SITE_URL}/"
        title = f"Luxury Restroom Trailer Rental | {BUSINESS_NAME}"
        description = (
            f"Premium 4-stall luxury restroom trailer rental in Southern California. "
            f"Flushable toilets, AC, mirrors, hardwood floors. {BUSINESS_PHONE}"
        )

    og_image = f"{SITE_URL}/images/zoar-luxury-restroom-trailer.jpg"

    return {
        "canonical": url,
        "og:title": title,
        "og:description": description,
        "og:image": og_image,
        "og:url": url,
        "og:type": "website",
        "twitter:card": "summary_large_image",
        "twitter:title": title,
        "twitter:description": description,
    }


# ── 8 & 9. Batch Generation ──────────────────────────────────────────────────

def generate_all_city_pages() -> list[dict]:
    """Batch generate all city landing pages."""
    print(f"[SEO] Generating all {len(CITY_DATA)} city pages")
    pages = []
    for city in CITY_DATA:
        page = generate_city_landing_page(city)
        if page:
            pages.append(page)
    print(f"[SEO] Generated {len(pages)} city pages")
    return pages


def generate_all_event_pages() -> list[dict]:
    """Batch generate all event type landing pages."""
    print(f"[SEO] Generating all {len(EVENT_TYPES)} event pages")
    pages = []
    for event in EVENT_TYPES:
        page = generate_event_landing_page(event)
        if page:
            pages.append(page)
    print(f"[SEO] Generated {len(pages)} event pages")
    return pages


# ── 10. Write to Disk ────────────────────────────────────────────────────────

def write_pages_to_disk(output_dir: str) -> int:
    """Write generated HTML pages to disk for deployment. Returns count written."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    count = 0

    faq_schema = generate_faq_schema()
    local_schema = generate_local_business_schema()

    # City pages
    city_pages = generate_all_city_pages()
    for page in city_pages:
        html = _wrap_html(page["title"], page["meta_description"], page["h1"],
                          page["content_html"], page["schema_json"], page["url"])
        file_path = out / f"{page['slug']}.html"
        file_path.write_text(html, encoding="utf-8")
        count += 1

    # Event pages
    event_pages = generate_all_event_pages()
    for page in event_pages:
        html = _wrap_html(page["title"], page["meta_description"], page["h1"],
                          page["content_html"], page["schema_json"], page["url"])
        file_path = out / f"{page['slug']}.html"
        file_path.write_text(html, encoding="utf-8")
        count += 1

    # Sitemap
    sitemap = generate_sitemap()
    (out / "sitemap.xml").write_text(sitemap, encoding="utf-8")
    count += 1

    # Schema files
    (out / "faq-schema.json").write_text(faq_schema, encoding="utf-8")
    (out / "local-business-schema.json").write_text(local_schema, encoding="utf-8")
    count += 2

    # Mark deployed
    conn = _db()
    conn.execute("UPDATE seo_pages SET deployed_at = ? WHERE deployed_at = ''", (_now_str(),))
    conn.commit()
    conn.close()

    print(f"[SEO] Wrote {count} files to {output_dir}")
    return count


def _wrap_html(title: str, meta_desc: str, h1: str, body: str,
               schema_json: str, canonical: str) -> str:
    """Wrap content in a full HTML page."""
    meta = generate_meta_tags("home", "")
    meta["canonical"] = canonical
    meta["og:title"] = title
    meta["og:description"] = meta_desc

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <meta name="description" content="{meta_desc}">
    <link rel="canonical" href="{canonical}">
    <meta property="og:title" content="{title}">
    <meta property="og:description" content="{meta_desc}">
    <meta property="og:url" content="{canonical}">
    <meta property="og:type" content="website">
    <meta property="og:image" content="{SITE_URL}/images/zoar-luxury-restroom-trailer.jpg">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="{title}">
    <meta name="twitter:description" content="{meta_desc}">
    <script type="application/ld+json">
{schema_json}
    </script>
</head>
<body>
    <header>
        <nav>
            <a href="{SITE_URL}/">{BUSINESS_NAME}</a>
            <a href="tel:+14242358979">{BUSINESS_PHONE}</a>
        </nav>
    </header>
    <main>
        <h1>{h1}</h1>
        {body}
    </main>
    <footer>
        <p>&copy; {_now_pt().year} {BUSINESS_NAME} | {BUSINESS_PHONE} | {BUSINESS_EMAIL}</p>
    </footer>
</body>
</html>"""


# ── 11. SEO Stats ─────────────────────────────────────────────────────────────

def get_seo_stats() -> dict:
    """Return SEO generation stats."""
    conn = _db()
    city_count = conn.execute(
        "SELECT COUNT(*) FROM seo_pages WHERE page_type='city'"
    ).fetchone()[0]
    event_count = conn.execute(
        "SELECT COUNT(*) FROM seo_pages WHERE page_type='event'"
    ).fetchone()[0]
    deployed = conn.execute(
        "SELECT COUNT(*) FROM seo_pages WHERE deployed_at != ''"
    ).fetchone()[0]
    last_gen = conn.execute(
        "SELECT MAX(generated_at) FROM seo_pages"
    ).fetchone()[0]
    conn.close()

    sitemap_entries = 1 + len(CITY_DATA) + len(EVENT_SLUGS)  # home + cities + events

    return {
        "city_pages": city_count,
        "event_pages": event_count,
        "total_pages": city_count + event_count,
        "sitemap_entries": sitemap_entries,
        "deployed": deployed,
        "schema_types": ["FAQPage", "LocalBusiness", "BreadcrumbList"],
        "cities_configured": len(CITY_DATA),
        "events_configured": len(EVENT_TYPES),
        "last_generated": last_gen or "never",
    }


# ── 12. Telegram Command Handlers ────────────────────────────────────────────

def handle_seo_command(text: str) -> str:
    """Handle /seo — show SEO status and stats."""
    stats = get_seo_stats()
    lines = [
        "SEO Status — Zoar Bathroom Rentals",
        "=" * 40,
        f"City pages:       {stats['city_pages']}/{stats['cities_configured']}",
        f"Event pages:      {stats['event_pages']}/{stats['events_configured']}",
        f"Sitemap entries:  {stats['sitemap_entries']}",
        f"Schema types:     {', '.join(stats['schema_types'])}",
        f"Deployed:         {stats['deployed']}/{stats['total_pages']}",
        f"Last generated:   {stats['last_generated']}",
        "",
        "Commands:",
        "  /seo generate  — Regenerate all pages",
        "  /seo sitemap   — Show sitemap XML",
        "  /seo stats     — Show detailed stats",
    ]
    return "\n".join(lines)


def handle_seo_generate_command(text: str) -> str:
    """Handle /seo generate — regenerate all pages."""
    parts = text.strip().lower().split()

    # /seo sitemap
    if len(parts) >= 2 and parts[1] == "sitemap":
        sitemap = generate_sitemap()
        return f"Sitemap generated ({len(CITY_DATA)} cities + {len(EVENT_SLUGS)} events):\n\n{sitemap[:2000]}"

    # /seo stats
    if len(parts) >= 2 and parts[1] == "stats":
        return handle_seo_command(text)

    # /seo generate [output_dir]
    output_dir = None
    if len(parts) >= 3 and parts[1] == "generate":
        output_dir = parts[2]

    print("[SEO] Regenerating all SEO pages")
    city_pages = generate_all_city_pages()
    event_pages = generate_all_event_pages()
    sitemap = generate_sitemap()
    faq = generate_faq_schema()
    local_biz = generate_local_business_schema()

    written = 0
    if output_dir:
        written = write_pages_to_disk(output_dir)

    lines = [
        "SEO Generation Complete",
        "=" * 40,
        f"City pages:    {len(city_pages)}",
        f"Event pages:   {len(event_pages)}",
        f"Sitemap:       {1 + len(CITY_DATA) + len(EVENT_SLUGS)} entries",
        f"FAQ schema:    {len(json.loads(faq)['mainEntity'])} questions",
        f"Local schema:  generated",
    ]
    if written:
        lines.append(f"Files written: {written} to {output_dir}")
    else:
        lines.append("Use '/seo generate <dir>' to write files to disk")

    return "\n".join(lines)
