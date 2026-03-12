"""
Marketplace Listing Generator & Tracker — Zoar Bathroom Rentals

Generates varied listings for FB Marketplace + Craigslist that:
 • Rotate 4 angles (wedding, corporate/film, construction, seasonal)
 • Randomize titles, descriptions, and photo order per listing
 • Track what was posted where and when (in SQLite)
 • Enforce safe posting frequency (no bans)
 • Push "time to post" reminders via Telegram

Both platforms lack official posting APIs, so this is a
content-prep + tracking system. Kai posts manually; Nexus
tracks everything and generates the copy.
"""
from __future__ import annotations
import json, random, sqlite3, uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

DB_PATH = Path.home() / ".nexus" / "memory.db"

# ── Business Contact (PUBLIC ONLY — never personal info) ─────────────────────
BUSINESS_PHONE = "(424) 235-8979"
BUSINESS_EMAIL = "zoarbathrooms@gmail.com"
BUSINESS_WEBSITE = "https://zoarbathroomrental.com"
BUSINESS_NAME = "Zoar Bathroom Rental"
SERVICE_AREA = "Los Angeles, San Fernando Valley, Ventura, Santa Clarita, and all of SoCal"

# ── Image Sets (rotated per listing to avoid duplicate detection) ────────────
# These reference the web-optimized images from the website
IMAGE_SETS = [
    ["web-000.jpg", "web-004.jpg", "web-006.jpg", "web-010.jpg", "web-024.jpg"],
    ["web-002.jpg", "web-010.jpg", "web-000.jpg", "web-014.jpg", "web-006.jpg"],
    ["web-024.jpg", "web-002.jpg", "web-014.jpg", "web-004.jpg", "web-000.jpg"],
    ["web-006.jpg", "web-000.jpg", "web-024.jpg", "web-002.jpg", "web-010.jpg"],
]

# ── Craigslist Regions ───────────────────────────────────────────────────────
CL_REGIONS = {
    "la_central":  {"name": "LA Central",     "url": "losangeles.craigslist.org", "area": "lac"},
    "sfv":         {"name": "San Fernando Valley", "url": "losangeles.craigslist.org", "area": "sfv"},
    "westside":    {"name": "Westside / South Bay", "url": "losangeles.craigslist.org", "area": "wst"},
    "sgv":         {"name": "San Gabriel Valley", "url": "losangeles.craigslist.org", "area": "sgv"},
    "ventura":     {"name": "Ventura County",  "url": "ventura.craigslist.org", "area": ""},
    "orange":      {"name": "Orange County",   "url": "orangecounty.craigslist.org", "area": ""},
    "inland":      {"name": "Inland Empire",   "url": "inlandempire.craigslist.org", "area": ""},
    "sb":          {"name": "Santa Barbara",   "url": "santabarbara.craigslist.org", "area": ""},
}

# ── Listing Variations ───────────────────────────────────────────────────────
# Each variation has multiple title/description options for rotation

VARIATIONS = {
    "wedding": {
        "titles": [
            "Luxury Restroom Trailer for Weddings & Events — SoCal",
            "5-Star Portable Restroom Trailer — Perfect for Weddings",
            "Elegant 4-Stall Restroom Trailer Rental — LA Weddings",
            "Luxury Bathroom Trailer for Rent — Outdoor Weddings SoCal",
            "Premium Restroom Trailer — Your Guests Will Love It",
        ],
        "descriptions": [
            (
                "Elevate your wedding with our luxury 4-stall restroom trailer. "
                "Features AC, running water, hardwood-style floors, full-length mirrors, "
                "and built-in Bluetooth speaker. Your guests will think it's part of the venue.\n\n"
                "Perfect for outdoor weddings, vineyard receptions, ranch ceremonies, "
                "and backyard celebrations throughout SoCal.\n\n"
                f"Serving {SERVICE_AREA}.\n\n"
                "Includes delivery, setup, and pickup.\n\n"
                f"Call or text for a free quote: {BUSINESS_PHONE}\n"
                f"{BUSINESS_WEBSITE}"
            ),
            (
                "Planning an outdoor wedding? Don't let porta potties ruin the vibe.\n\n"
                "Our luxury restroom trailer has 4 private stalls with AC, hardwood floors, "
                "running water, full-length mirrors, and ambient lighting. It looks like it "
                "belongs at a 5-star venue.\n\n"
                "We handle delivery, setup, and pickup — all you do is enjoy your day.\n\n"
                f"Serving all of SoCal: {SERVICE_AREA}.\n\n"
                f"Free quotes: {BUSINESS_PHONE}\n"
                f"{BUSINESS_WEBSITE}"
            ),
            (
                "Your guests deserve better than a plastic box.\n\n"
                "Our 4-stall luxury restroom trailer features:\n"
                "• Air conditioning\n"
                "• Running water & flushing toilets\n"
                "• Hardwood-style floors\n"
                "• Full-length mirrors\n"
                "• Bluetooth speaker\n\n"
                "Ideal for weddings, engagement parties, and outdoor receptions.\n\n"
                f"We deliver anywhere in SoCal. Call for a free quote: {BUSINESS_PHONE}\n"
                f"{BUSINESS_WEBSITE}"
            ),
        ],
        "price_range": (1100, 1400),
        "category": "Home & Garden",
    },
    "corporate": {
        "titles": [
            "Premium Restroom Trailer — Film Sets & Corporate Events",
            "Luxury Portable Restroom — Production & Corporate Rental",
            "Executive Restroom Trailer for Rent — LA Film & Events",
            "AC Restroom Trailer — Corporate Events & Productions",
            "Professional Restroom Trailer Rental — Film & Corporate SoCal",
        ],
        "descriptions": [
            (
                "Professional-grade luxury restroom trailer for film sets, corporate events, "
                "and production shoots.\n\n"
                "4 private stalls with AC, running water, hardwood floors, and full-length mirrors. "
                "Keeps your talent and crew comfortable on location.\n\n"
                "Flexible rental periods — day, weekend, or long-term available.\n\n"
                f"Serving {SERVICE_AREA}.\n"
                f"Delivery, setup, and pickup included.\n\n"
                f"Get a quote: {BUSINESS_PHONE}\n"
                f"{BUSINESS_WEBSITE}"
            ),
            (
                "Need restrooms for a shoot or corporate gathering? Our luxury trailer "
                "is the industry choice for productions across LA.\n\n"
                "Features: 4 stalls, AC, running water, flushing toilets, hardwood floors, "
                "mirrors, and ambient lighting.\n\n"
                "Short-term or long-term rental available.\n\n"
                f"Covers all of SoCal: {SERVICE_AREA}.\n\n"
                f"Call or text: {BUSINESS_PHONE}\n"
                f"{BUSINESS_WEBSITE}"
            ),
        ],
        "price_range": (1100, 1500),
        "category": "Home & Garden",
    },
    "construction": {
        "titles": [
            "Executive Restroom Trailer — Construction & Renovation Sites",
            "Luxury Portable Restroom for Rent — Job Sites & Remodels",
            "Clean Restroom Trailer Rental — Construction & Contractors",
            "4-Stall Restroom Trailer — Job Site & Home Renovation Rental",
        ],
        "descriptions": [
            (
                "Upgrade your job site with a luxury restroom trailer. "
                "Way better than a standard porta potty — your crew and clients "
                "will appreciate it.\n\n"
                "4 stalls with AC, running water, flushing toilets, and handwash stations.\n\n"
                "Great for construction sites, home renovations, and long-term projects.\n\n"
                f"Day, weekend, weekly, or monthly rental.\n"
                f"Serving {SERVICE_AREA}.\n\n"
                f"Free quote: {BUSINESS_PHONE}\n"
                f"{BUSINESS_WEBSITE}"
            ),
            (
                "Running a construction project or home remodel? "
                "Keep your crew and clients happy with a real restroom — not a plastic box.\n\n"
                "Our luxury trailer has 4 private stalls with AC, running water, and "
                "handwash stations. Delivered to your site and picked up when you're done.\n\n"
                f"We serve all of SoCal. Weekly and monthly rates available.\n\n"
                f"Text or call for pricing: {BUSINESS_PHONE}\n"
                f"{BUSINESS_WEBSITE}"
            ),
        ],
        "price_range": (900, 1100),
        "category": "Home & Garden",
    },
    "seasonal": {
        "titles": [
            "5-Star Restroom Trailer Rental — Book Now for Spring Season",
            "Luxury Restroom Trailer — Spring & Summer Event Bookings Open",
            "Restroom Trailer for Rent — Festivals, Parties, Graduations",
            "Book a Luxury Bathroom Trailer — Outdoor Events SoCal",
            "Portable Luxury Restroom — Graduation Parties & Summer Events",
        ],
        "descriptions": [
            (
                "Spring and summer event season is here! Book your luxury restroom "
                "trailer now before dates fill up.\n\n"
                "Perfect for: graduations, birthday parties, family reunions, "
                "quinceañeras, festivals, and outdoor celebrations.\n\n"
                "4 private stalls with AC, running water, hardwood floors, mirrors, "
                "and Bluetooth speaker.\n\n"
                f"Serving {SERVICE_AREA}.\n"
                f"Delivery + setup + pickup included.\n\n"
                f"Book your date: {BUSINESS_PHONE}\n"
                f"{BUSINESS_WEBSITE}"
            ),
            (
                "Hosting an outdoor event this season? Our luxury restroom trailer "
                "makes any event feel upscale.\n\n"
                "Ideal for graduations, backyard parties, quinceañeras, family reunions, "
                "and community events.\n\n"
                "4 stalls • AC • Running Water • Hardwood Floors • Mirrors\n\n"
                "We deliver and set up for you — anywhere in SoCal.\n\n"
                f"Text or call: {BUSINESS_PHONE}\n"
                f"{BUSINESS_WEBSITE}"
            ),
        ],
        "price_range": (1100, 1300),
        "category": "Home & Garden",
    },
}

# ── Frequency Limits ─────────────────────────────────────────────────────────

LIMITS = {
    "fb_marketplace": {
        "min_hours_between_posts": 48,   # ~2-3 listings per week
        "max_per_week": 3,
        "renewal_after_days": 7,
        "max_renewals": 5,
    },
    "craigslist": {
        "min_hours_between_posts": 48,   # per region
        "max_per_week_per_region": 3,
    },
}


# ── Content Generation ───────────────────────────────────────────────────────

def generate_listing(
    platform: str = "fb_marketplace",
    variation: str = "",
    region: str = "",
) -> dict:
    """
    Generate a ready-to-post listing with randomized content.
    Returns dict with title, description, price, images, category, metadata.
    """
    # Pick variation (rotate if not specified)
    if not variation:
        variation = _pick_next_variation(platform)

    var_data = VARIATIONS[variation]

    title = random.choice(var_data["titles"])
    description = random.choice(var_data["descriptions"])
    price = random.randint(*var_data["price_range"])
    images = random.choice(IMAGE_SETS).copy()
    random.shuffle(images)  # Extra shuffle for uniqueness

    listing = {
        "id": uuid.uuid4().hex[:12],
        "platform": platform,
        "variation": variation,
        "region": region,
        "title": title,
        "description": description,
        "price": price,
        "images": images,
        "category": var_data["category"],
        "generated_at": datetime.utcnow().isoformat(),
    }

    if platform == "craigslist" and region:
        region_info = CL_REGIONS.get(region, {})
        listing["cl_url"] = region_info.get("url", "losangeles.craigslist.org")
        listing["cl_area"] = region_info.get("area", "")
        listing["cl_region_name"] = region_info.get("name", region)

    return listing


def _pick_next_variation(platform: str) -> str:
    """Pick the least-recently-used variation for this platform."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute(
            "SELECT variation, MAX(posted_at) as last_posted "
            "FROM marketplace_posts WHERE platform = ? "
            "GROUP BY variation ORDER BY last_posted ASC",
            (platform,)
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    conn.close()

    used = {r[0] for r in rows}
    variations = list(VARIATIONS.keys())

    # Prefer unused variations first
    unused = [v for v in variations if v not in used]
    if unused:
        return random.choice(unused)

    # Otherwise pick least-recently-used
    if rows:
        return rows[0][0]
    return random.choice(variations)


# ── Frequency Safety ─────────────────────────────────────────────────────────

def can_post(platform: str, region: str = "") -> dict:
    """
    Check if it's safe to post right now on this platform/region.
    Returns {"ok": True/False, "reason": "...", "next_ok_at": "..."}
    """
    conn = sqlite3.connect(str(DB_PATH))
    now = datetime.utcnow()

    try:
        if platform == "fb_marketplace":
            limits = LIMITS["fb_marketplace"]
            # Check time since last post
            row = conn.execute(
                "SELECT MAX(posted_at) FROM marketplace_posts "
                "WHERE platform = 'fb_marketplace' AND status != 'deleted'",
            ).fetchone()
            if row and row[0]:
                last = datetime.fromisoformat(row[0])
                hours_since = (now - last).total_seconds() / 3600
                if hours_since < limits["min_hours_between_posts"]:
                    next_ok = last + timedelta(hours=limits["min_hours_between_posts"])
                    conn.close()
                    return {
                        "ok": False,
                        "reason": f"Only {hours_since:.0f}h since last FB post (need {limits['min_hours_between_posts']}h)",
                        "next_ok_at": next_ok.isoformat(),
                    }

            # Check weekly count
            week_ago = (now - timedelta(days=7)).isoformat()
            count = conn.execute(
                "SELECT COUNT(*) FROM marketplace_posts "
                "WHERE platform = 'fb_marketplace' AND posted_at > ? AND status != 'deleted'",
                (week_ago,)
            ).fetchone()[0]
            if count >= limits["max_per_week"]:
                conn.close()
                return {"ok": False, "reason": f"Already posted {count}x this week (max {limits['max_per_week']})"}

        elif platform == "craigslist":
            limits = LIMITS["craigslist"]
            row = conn.execute(
                "SELECT MAX(posted_at) FROM marketplace_posts "
                "WHERE platform = 'craigslist' AND region = ? AND status != 'deleted'",
                (region,)
            ).fetchone()
            if row and row[0]:
                last = datetime.fromisoformat(row[0])
                hours_since = (now - last).total_seconds() / 3600
                if hours_since < limits["min_hours_between_posts"]:
                    next_ok = last + timedelta(hours=limits["min_hours_between_posts"])
                    conn.close()
                    return {
                        "ok": False,
                        "reason": f"Only {hours_since:.0f}h since last CL post in {region} (need 48h)",
                        "next_ok_at": next_ok.isoformat(),
                    }

        conn.close()
        return {"ok": True, "reason": "Good to post"}

    except sqlite3.OperationalError:
        conn.close()
        return {"ok": True, "reason": "Table not yet created — first post"}


# ── Post Tracking (DB) ──────────────────────────────────────────────────────

def record_post(listing: dict, post_url: str = "", notes: str = "") -> int:
    """Record that a listing was actually posted. Returns the DB row ID."""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.execute(
        "INSERT INTO marketplace_posts "
        "(listing_id, platform, variation, region, title, description, price, "
        " images, category, status, post_url, notes, posted_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'posted', ?, ?, datetime('now'))",
        (
            listing.get("id", uuid.uuid4().hex[:12]),
            listing.get("platform", ""),
            listing.get("variation", ""),
            listing.get("region", ""),
            listing.get("title", ""),
            listing.get("description", ""),
            listing.get("price", 0),
            json.dumps(listing.get("images", [])),
            listing.get("category", ""),
            post_url,
            notes,
        )
    )
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    print(f"[Marketplace] Recorded post #{row_id} on {listing.get('platform')} ({listing.get('variation')})")
    return row_id


def record_renewal(post_id: int) -> bool:
    """Record that a listing was renewed. Returns True if under renewal limit."""
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        "SELECT renewal_count FROM marketplace_posts WHERE id = ?", (post_id,)
    ).fetchone()
    if not row:
        conn.close()
        return False

    current = row[0]
    max_renewals = LIMITS.get("fb_marketplace", {}).get("max_renewals", 5)
    if current >= max_renewals:
        conn.close()
        return False

    conn.execute(
        "UPDATE marketplace_posts SET renewal_count = ?, renewed_at = datetime('now') WHERE id = ?",
        (current + 1, post_id)
    )
    conn.commit()
    conn.close()
    print(f"[Marketplace] Renewed post #{post_id} ({current + 1}/{max_renewals})")
    return True


def record_interaction(
    post_id: int,
    interaction_type: str,
    from_phone: str = "",
    from_name: str = "",
    message: str = "",
    lead_id: Optional[int] = None,
    notes: str = "",
) -> int:
    """
    Log an interaction (inquiry, message, view, etc.) on a marketplace post.
    Returns the interaction row ID.
    """
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.execute(
        "INSERT INTO marketplace_interactions "
        "(post_id, interaction_type, from_phone, from_name, message, lead_id, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (post_id, interaction_type, from_phone, from_name, message, lead_id, notes)
    )
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    print(f"[Marketplace] Interaction #{row_id} on post #{post_id}: {interaction_type}")
    return row_id


# ── Queries ──────────────────────────────────────────────────────────────────

def get_active_posts(platform: str = "") -> list[dict]:
    """Get all active (posted/renewed) marketplace listings."""
    conn = sqlite3.connect(str(DB_PATH))
    where = "WHERE status IN ('posted', 'renewed')"
    params = ()
    if platform:
        where += " AND platform = ?"
        params = (platform,)

    rows = conn.execute(
        f"SELECT id, listing_id, platform, variation, region, title, price, "
        f"status, post_url, posted_at, renewed_at, renewal_count, expires_at "
        f"FROM marketplace_posts {where} ORDER BY posted_at DESC",
        params
    ).fetchall()
    conn.close()

    return [
        {
            "id": r[0], "listing_id": r[1], "platform": r[2], "variation": r[3],
            "region": r[4], "title": r[5], "price": r[6], "status": r[7],
            "post_url": r[8], "posted_at": r[9], "renewed_at": r[10],
            "renewal_count": r[11], "expires_at": r[12],
        }
        for r in rows
    ]


def get_post_interactions(post_id: int) -> list[dict]:
    """Get all interactions for a specific post."""
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT id, interaction_type, from_phone, from_name, message, lead_id, notes, ts "
        "FROM marketplace_interactions WHERE post_id = ? ORDER BY ts DESC",
        (post_id,)
    ).fetchall()
    conn.close()
    return [
        {
            "id": r[0], "type": r[1], "from_phone": r[2], "from_name": r[3],
            "message": r[4], "lead_id": r[5], "notes": r[6], "ts": r[7],
        }
        for r in rows
    ]


def get_posting_schedule() -> list[dict]:
    """
    Generate the recommended posting schedule for the next 7 days.
    Returns a list of {day, platform, action, region, variation, time}.
    """
    schedule = []
    variations = list(VARIATIONS.keys())
    var_idx = 0
    cl_regions_list = list(CL_REGIONS.keys())
    cl_idx = 0
    now = datetime.utcnow()

    for day_offset in range(7):
        day = now + timedelta(days=day_offset)
        day_name = day.strftime("%A")

        if day.weekday() == 0:  # Monday
            schedule.append({
                "day": day_name, "date": day.strftime("%Y-%m-%d"),
                "platform": "fb_marketplace", "variation": variations[var_idx % 4],
                "region": "", "time": "9:00 AM", "action": "Post new listing",
            })
            var_idx += 1
            if cl_idx < len(cl_regions_list):
                schedule.append({
                    "day": day_name, "date": day.strftime("%Y-%m-%d"),
                    "platform": "craigslist", "variation": variations[(var_idx - 1) % 4],
                    "region": cl_regions_list[cl_idx], "time": "11:00 AM",
                    "action": f"Post in {CL_REGIONS[cl_regions_list[cl_idx]]['name']}",
                })
                cl_idx += 1

        elif day.weekday() == 2:  # Wednesday
            schedule.append({
                "day": day_name, "date": day.strftime("%Y-%m-%d"),
                "platform": "fb_marketplace", "variation": variations[var_idx % 4],
                "region": "", "time": "11:00 AM", "action": "Post or renew listing",
            })
            var_idx += 1
            for _ in range(2):
                if cl_idx < len(cl_regions_list):
                    schedule.append({
                        "day": day_name, "date": day.strftime("%Y-%m-%d"),
                        "platform": "craigslist", "variation": variations[(var_idx - 1) % 4],
                        "region": cl_regions_list[cl_idx], "time": "9:00 AM",
                        "action": f"Post in {CL_REGIONS[cl_regions_list[cl_idx]]['name']}",
                    })
                    cl_idx += 1

        elif day.weekday() == 3:  # Thursday
            schedule.append({
                "day": day_name, "date": day.strftime("%Y-%m-%d"),
                "platform": "fb_marketplace", "variation": variations[var_idx % 4],
                "region": "", "time": "9:00 AM", "action": "Post new listing (alt angle)",
            })
            var_idx += 1
            if cl_idx < len(cl_regions_list):
                schedule.append({
                    "day": day_name, "date": day.strftime("%Y-%m-%d"),
                    "platform": "craigslist", "variation": variations[(var_idx - 1) % 4],
                    "region": cl_regions_list[cl_idx], "time": "10:00 AM",
                    "action": f"Post in {CL_REGIONS[cl_regions_list[cl_idx]]['name']}",
                })
                cl_idx += 1

        elif day.weekday() == 4:  # Friday
            for _ in range(2):
                if cl_idx < len(cl_regions_list):
                    schedule.append({
                        "day": day_name, "date": day.strftime("%Y-%m-%d"),
                        "platform": "craigslist", "variation": variations[var_idx % 4],
                        "region": cl_regions_list[cl_idx], "time": "9:00 AM",
                        "action": f"Post in {CL_REGIONS[cl_regions_list[cl_idx]]['name']}",
                    })
                    cl_idx += 1

        # Reset CL region rotation weekly
        if cl_idx >= len(cl_regions_list):
            cl_idx = 0

    return schedule


def get_stats() -> dict:
    """Get marketplace posting statistics."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        total = conn.execute("SELECT COUNT(*) FROM marketplace_posts").fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM marketplace_posts WHERE status IN ('posted', 'renewed')"
        ).fetchone()[0]
        interactions = conn.execute(
            "SELECT COUNT(*) FROM marketplace_interactions"
        ).fetchone()[0]
        by_platform = conn.execute(
            "SELECT platform, COUNT(*) FROM marketplace_posts GROUP BY platform"
        ).fetchall()
        by_variation = conn.execute(
            "SELECT variation, COUNT(*) FROM marketplace_posts GROUP BY variation"
        ).fetchall()
        conn.close()
        return {
            "total_posts": total,
            "active_posts": active,
            "total_interactions": interactions,
            "by_platform": {r[0]: r[1] for r in by_platform},
            "by_variation": {r[0]: r[1] for r in by_variation},
        }
    except sqlite3.OperationalError:
        conn.close()
        return {"total_posts": 0, "active_posts": 0, "total_interactions": 0}
