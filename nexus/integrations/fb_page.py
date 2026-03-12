"""
Facebook Page Management — Zoar Bathroom Rental

Uses the Facebook Graph API (Page token) to:
 • Update page info (about, bio, description, hours, CTA)
 • Create posts (text, photo, link)
 • Reply to comments on posts
 • Get page insights (reach, engagement)

All public-facing content uses ONLY:
  Phone: (424) 235-8979
  Email: zoarbathrooms@gmail.com
  Website: https://zoarbathroomrental.com
"""
from __future__ import annotations
import json, httpx, sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

DB_PATH = Path.home() / ".nexus" / "memory.db"

_config: dict = {}

GRAPH_API = "https://graph.facebook.com/v25.0"

# ── Public business info (NEVER personal) ────────────────────────────────────
BIZ = {
    "name": "Zoar Bathroom Rental",
    "phone": "(424) 235-8979",
    "email": "zoarbathrooms@gmail.com",
    "website": "https://zoarbathroomrental.com",
    "address": "Los Angeles, CA",
    "service_area": "Los Angeles, San Fernando Valley, Ventura, Santa Clarita, Santa Barbara, and all of SoCal",
}

# ── Init ─────────────────────────────────────────────────────────────────────

def init_fb_page(config: dict):
    """Initialize with config containing fb_page_id and fb_page_access_token."""
    global _config
    _config = config
    page_id = config.get("fb_page_id", "")
    print(f"[FB Page] Initialized (page_id={page_id})")


def _token() -> str:
    return _config.get("fb_page_access_token", "")


def _page_id() -> str:
    return _config.get("fb_page_id", "")


# ── Page Info ────────────────────────────────────────────────────────────────

async def get_page_info() -> dict:
    """Fetch current page details."""
    url = f"{GRAPH_API}/{_page_id()}"
    params = {
        "fields": "name,about,bio,description,category,phone,emails,website,"
                  "single_line_address,hours,fan_count,followers_count,"
                  "call_to_actions,cover",
        "access_token": _token(),
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params)
        return resp.json()


async def update_page_info(updates: dict) -> dict:
    """
    Update page fields. Valid fields:
    about, bio, description, phone, website, emails, single_line_address, hours
    """
    url = f"{GRAPH_API}/{_page_id()}"
    data = {**updates, "access_token": _token()}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, data=data)
        result = resp.json()
        if result.get("success"):
            print(f"[FB Page] Updated: {list(updates.keys())}")
        else:
            print(f"[FB Page] Update failed: {result}")
        return result


async def set_page_cta(cta_type: str = "CALL_NOW", cta_value: str = "") -> dict:
    """
    Set the page's call-to-action button.
    Types: CALL_NOW, CONTACT_US, SEND_MESSAGE, BOOK_NOW, LEARN_MORE, GET_QUOTE
    """
    url = f"{GRAPH_API}/{_page_id()}/call_to_actions"
    data = {
        "type": cta_type,
        "access_token": _token(),
    }
    if cta_value:
        if cta_type in ("CALL_NOW",):
            data["value"] = json.dumps({"phone_number": cta_value})
        elif cta_type in ("LEARN_MORE", "GET_QUOTE", "BOOK_NOW"):
            data["value"] = json.dumps({"website": cta_value})

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, data=data)
        result = resp.json()
        print(f"[FB Page] CTA set: {cta_type} → {result}")
        return result


# ── Posting ──────────────────────────────────────────────────────────────────

async def create_post(
    message: str,
    link: str = "",
    published: bool = True,
) -> dict:
    """Create a text or link post on the page."""
    url = f"{GRAPH_API}/{_page_id()}/feed"
    data = {
        "message": message,
        "published": str(published).lower(),
        "access_token": _token(),
    }
    if link:
        data["link"] = link

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, data=data)
        result = resp.json()
        post_id = result.get("id", "")
        if post_id:
            print(f"[FB Page] Post created: {post_id}")
            _log_action("post_created", {"post_id": post_id, "message": message[:100]})
        else:
            print(f"[FB Page] Post failed: {result}")
        return result


async def create_photo_post(
    message: str,
    photo_url: str = "",
    photo_path: str = "",
) -> dict:
    """Create a photo post. Provide either photo_url or photo_path."""
    url = f"{GRAPH_API}/{_page_id()}/photos"

    async with httpx.AsyncClient(timeout=30) as client:
        if photo_path:
            with open(photo_path, "rb") as f:
                files = {"source": (Path(photo_path).name, f, "image/jpeg")}
                data = {"message": message, "access_token": _token()}
                resp = await client.post(url, data=data, files=files)
        else:
            data = {"message": message, "url": photo_url, "access_token": _token()}
            resp = await client.post(url, data=data)

        result = resp.json()
        post_id = result.get("post_id", result.get("id", ""))
        if post_id:
            print(f"[FB Page] Photo post created: {post_id}")
            _log_action("photo_post_created", {"post_id": post_id})
        return result


# ── Comments ─────────────────────────────────────────────────────────────────

async def get_post_comments(post_id: str, limit: int = 25) -> list:
    """Get comments on a page post."""
    url = f"{GRAPH_API}/{post_id}/comments"
    params = {
        "fields": "id,message,from,created_time,comment_count",
        "limit": limit,
        "access_token": _token(),
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params)
        data = resp.json()
        return data.get("data", [])


async def reply_to_comment(comment_id: str, message: str) -> dict:
    """Reply to a comment on a page post."""
    url = f"{GRAPH_API}/{comment_id}/comments"
    data = {"message": message, "access_token": _token()}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, data=data)
        result = resp.json()
        if result.get("id"):
            print(f"[FB Page] Replied to comment {comment_id}")
            _log_action("comment_reply", {"comment_id": comment_id, "reply": message[:100]})
        return result


# ── Insights ─────────────────────────────────────────────────────────────────

async def get_page_insights(period: str = "day") -> dict:
    """
    Get page insights. Period: day, week, days_28.
    Returns reach, engagement, impressions, followers.
    """
    url = f"{GRAPH_API}/{_page_id()}/insights"
    metrics = [
        "page_impressions", "page_engaged_users",
        "page_fans", "page_views_total",
        "page_post_engagements", "page_fan_adds",
    ]
    params = {
        "metric": ",".join(metrics),
        "period": period,
        "access_token": _token(),
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params)
        data = resp.json()
        results = {}
        for item in data.get("data", []):
            name = item.get("name", "")
            values = item.get("values", [])
            if values:
                results[name] = values[-1].get("value", 0)
        return results


async def get_recent_posts(limit: int = 10) -> list:
    """Get recent page posts with engagement metrics."""
    url = f"{GRAPH_API}/{_page_id()}/posts"
    params = {
        "fields": "id,message,created_time,shares,likes.summary(true),comments.summary(true)",
        "limit": limit,
        "access_token": _token(),
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params)
        data = resp.json()
        posts = []
        for p in data.get("data", []):
            posts.append({
                "id": p.get("id"),
                "message": p.get("message", "")[:200],
                "created_time": p.get("created_time"),
                "likes": p.get("likes", {}).get("summary", {}).get("total_count", 0),
                "comments": p.get("comments", {}).get("summary", {}).get("total_count", 0),
                "shares": p.get("shares", {}).get("count", 0) if p.get("shares") else 0,
            })
        return posts


# ── Page Overhaul Content ────────────────────────────────────────────────────

def get_optimized_page_content() -> dict:
    """
    Returns the optimized page content for the Zoar Bathroom Rental Facebook page.
    This is the content that should be applied during the page overhaul.
    """
    return {
        "about": (
            "Luxury portable restroom trailer rental for weddings, quinceañeras, "
            "corporate events, and outdoor parties across the San Fernando Valley "
            "and Los Angeles. Our 4-stall trailer features AC, running water, "
            "hardwood floors, full-length mirrors, and a Bluetooth speaker. "
            f"Serving {BIZ['service_area']}. "
            f"Call {BIZ['phone']} for a free quote."
        ),
        "bio": (
            "Luxury restroom trailer rental for SoCal events. "
            "AC • Running Water • Hardwood Floors • Full Mirrors. "
            f"Free quotes: {BIZ['phone']}"
        ),
        "description": (
            "Zoar Bathroom Rental provides luxury portable restroom trailers "
            "for events throughout Southern California.\n\n"
            "Our trailer features:\n"
            "• 4 private stalls (2 men's, 2 women's)\n"
            "• Air conditioning\n"
            "• Running water & flushing toilets\n"
            "• Hardwood-style floors\n"
            "• Full-length mirrors\n"
            "• Built-in Bluetooth speaker\n"
            "• Ambient lighting\n\n"
            "Perfect for:\n"
            "→ Weddings & receptions\n"
            "→ Corporate events & galas\n"
            "→ Quinceañeras & sweet sixteens\n"
            "→ Backyard parties & family reunions\n"
            "→ Festivals & outdoor celebrations\n"
            "→ Graduation parties & quinceañeras\n\n"
            "We handle delivery, setup, and pickup. "
            "Day, weekend, weekly, and monthly rates available.\n\n"
            f"Serving: {BIZ['service_area']}\n\n"
            f"📞 {BIZ['phone']}\n"
            f"📧 {BIZ['email']}\n"
            f"🌐 {BIZ['website']}"
        ),
        "phone": BIZ["phone"],
        "website": BIZ["website"],
        "single_line_address": BIZ["address"],
        "cta": {"type": "CALL_NOW", "value": "+14242358979"},
    }


# ── Organic Content Templates ────────────────────────────────────────────────

ORGANIC_POSTS = {
    "tips": [
        {
            "message": (
                "Planning an outdoor wedding? Here are 3 things most couples forget:\n\n"
                "1️⃣ Guest restroom comfort — plastic porta potties can ruin the vibe\n"
                "2️⃣ Handwash stations — especially important for food-forward receptions\n"
                "3️⃣ Mirror access — your guests will want to freshen up between cocktails and dancing\n\n"
                "Our luxury restroom trailer handles all three with AC, running water, "
                f"hardwood floors, and full-length mirrors.\n\n{BIZ['website']}"
            ),
            "tags": ["wedding", "tips", "planning"],
        },
        {
            "message": (
                "Hosting a quinceañera or sweet sixteen? Here's what sets luxury restrooms apart:\n\n"
                "• Climate controlled — your guests stay comfortable rain or shine\n"
                "• Real flushing toilets and running water\n"
                "• Full-length mirrors for outfit checks\n"
                "• Hardwood floors and ambient lighting\n\n"
                f"We handle delivery, setup, and pickup. {BIZ['phone']}"
            ),
            "tags": ["quinceanera", "party", "tips"],
        },
        {
            "message": (
                "Hosting a backyard party with 50+ guests? Don't let your home bathroom "
                "become the bottleneck.\n\n"
                "Our luxury restroom trailer has 4 private stalls with AC, running water, "
                "and mirrors — your guests won't believe it's not a real building.\n\n"
                f"Get a free quote: {BIZ['phone']}"
            ),
            "tags": ["backyard", "party", "tips"],
        },
    ],
    "behind_scenes": [
        {
            "message": (
                "Delivery day! Getting our luxury restroom trailer set up for a gorgeous "
                "vineyard wedding in Ventura County. 🍇✨\n\n"
                "Fun fact: most of our wedding clients say their guests were genuinely impressed "
                "by the restroom situation. That's the reaction we aim for.\n\n"
                f"Book your date: {BIZ['phone']}"
            ),
            "tags": ["bts", "wedding", "delivery"],
        },
        {
            "message": (
                "Another beautiful setup in the Valley! Our trailer tucked perfectly between "
                "the ceremony space and the reception tent.\n\n"
                "Pro tip: let us know your venue layout in advance and we'll find the perfect "
                "placement for maximum convenience and minimum visibility.\n\n"
                f"{BIZ['website']}"
            ),
            "tags": ["bts", "setup", "valley"],
        },
    ],
    "seasonal": [
        {
            "message": (
                "Spring wedding season is HERE 🌸\n\n"
                "Our calendar is filling up fast — if you're planning an outdoor event "
                "between March and June, now is the time to lock in your date.\n\n"
                "We're serving weddings, quinceañeras, graduations, and corporate events "
                f"all across SoCal.\n\n"
                f"Get your free quote: {BIZ['phone']}\n{BIZ['website']}"
            ),
            "tags": ["seasonal", "spring", "booking"],
        },
    ],
}


def get_next_organic_post(post_type: str = "") -> dict:
    """Get the next organic post to publish. Rotates through types."""
    import random
    if post_type and post_type in ORGANIC_POSTS:
        return random.choice(ORGANIC_POSTS[post_type])

    # Pick from all types
    all_posts = []
    for ptype, posts in ORGANIC_POSTS.items():
        for p in posts:
            all_posts.append({**p, "type": ptype})
    return random.choice(all_posts)


# ── Action Logging ───────────────────────────────────────────────────────────

def _log_action(action_type: str, data: dict):
    """Log FB page actions to DB for tracking."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "INSERT INTO marketplace_interactions "
            "(post_id, interaction_type, from_name, message, notes) "
            "VALUES (0, ?, 'fb_page', ?, ?)",
            (f"fb_page_{action_type}", json.dumps(data)[:500], datetime.utcnow().isoformat())
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
