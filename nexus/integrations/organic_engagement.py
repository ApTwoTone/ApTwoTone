"""
Organic Engagement Tracker — Zoar Bathroom Rental

Tracks all non-paid marketing activity:
 • Facebook group interactions (comments, replies, helpful posts)
 • Facebook page organic posts and engagement
 • Craigslist / FB Marketplace conversations
 • Platform-specific metrics

Rules:
 • 10:1 ratio — 10 helpful comments per 1 brand mention
 • Never salesy in groups — be genuinely helpful
 • ALL public content uses ONLY business contact info
 • Phone: (424) 235-8979 | Email: zoarbathrooms@gmail.com
"""
from __future__ import annotations
import json, sqlite3, uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

DB_PATH = Path.home() / ".nexus" / "memory.db"

# ── Engagement Types ─────────────────────────────────────────────────────────

ENGAGEMENT_TYPES = {
    # Facebook Groups
    "group_comment":      {"platform": "facebook", "category": "group", "brand_mention": False},
    "group_helpful_reply": {"platform": "facebook", "category": "group", "brand_mention": False},
    "group_brand_post":   {"platform": "facebook", "category": "group", "brand_mention": True},
    "group_question":     {"platform": "facebook", "category": "group", "brand_mention": False},
    # Facebook Page
    "page_post":          {"platform": "facebook", "category": "page", "brand_mention": True},
    "page_photo_post":    {"platform": "facebook", "category": "page", "brand_mention": True},
    "page_comment_reply": {"platform": "facebook", "category": "page", "brand_mention": True},
    "page_review_reply":  {"platform": "facebook", "category": "page", "brand_mention": True},
    # Marketplace
    "marketplace_reply":  {"platform": "facebook", "category": "marketplace", "brand_mention": True},
    "cl_reply":           {"platform": "craigslist", "category": "marketplace", "brand_mention": True},
    # Instagram
    "ig_post":            {"platform": "instagram", "category": "page", "brand_mention": True},
    "ig_story":           {"platform": "instagram", "category": "page", "brand_mention": True},
    "ig_comment":         {"platform": "instagram", "category": "engagement", "brand_mention": False},
    "ig_reel":            {"platform": "instagram", "category": "page", "brand_mention": True},
}

# ── Target Facebook Groups (SoCal wedding/event planning) ───────────────────

TARGET_GROUPS = [
    {"name": "SoCal Wedding Planning", "category": "wedding", "priority": "high"},
    {"name": "LA Event Planners", "category": "events", "priority": "high"},
    {"name": "San Fernando Valley Community", "category": "local", "priority": "medium"},
    {"name": "Ventura County Events", "category": "events", "priority": "medium"},
    {"name": "SoCal DIY Weddings", "category": "wedding", "priority": "high"},
    {"name": "LA Film & Production Crew", "category": "film", "priority": "medium"},
    {"name": "Santa Clarita Moms", "category": "local", "priority": "medium"},
    {"name": "SoCal Construction & Contractors", "category": "construction", "priority": "medium"},
    {"name": "LA Brides & Grooms", "category": "wedding", "priority": "high"},
    {"name": "Southern California Event Vendors", "category": "vendors", "priority": "high"},
]

# ── Helpful Comment Templates (NON-salesy, genuinely useful) ─────────────────

HELPFUL_COMMENTS = {
    "wedding_venue_question": [
        "Great question! For outdoor venues, make sure to ask about restroom access early. "
        "Some ranches and vineyards don't have enough facilities for 100+ guests, "
        "so you'll want to plan for that before signing the contract.",

        "One thing a lot of couples miss — check if the venue has enough restrooms "
        "for your guest count. The general rule is 1 restroom per 50 guests for "
        "events under 4 hours. If your venue is remote, you might need to bring in extras.",

        "Love that venue choice! Quick tip: if it's outdoors, confirm what restroom "
        "situation they provide. I've seen couples scramble last minute when they realize "
        "the venue only has 2 bathrooms for 150 people.",
    ],
    "outdoor_event_tips": [
        "For outdoor events in SoCal, keep these in mind:\n"
        "1. Shade — May–September can hit 90°+\n"
        "2. Power — generators for lighting/music if the venue is remote\n"
        "3. Guest comfort — restrooms, handwash stations, water stations\n"
        "These three make or break an outdoor event.",

        "The key to a great outdoor event is thinking about guest comfort first. "
        "Sound, lighting, shade, and restrooms. If those four are covered, "
        "everything else falls into place.",
    ],
    "budget_advice": [
        "From what I've seen, the areas where you can save without guests noticing: "
        "invitations (digital is fine now), centerpieces (greenery is gorgeous and affordable), "
        "and day-of-week (Friday events can save 30-40%). Don't skimp on food, music, or comfort.",

        "Budget tip: vendors are often more flexible on pricing for weekday events "
        "or January–March bookings. If your schedule allows, you can save a lot by "
        "choosing a less popular date.",
    ],
    "construction_question": [
        "For job site restrooms, the big difference is between standard porta potties "
        "($100-200/month) and restroom trailers ($350-500/month). The trailer has flushing "
        "toilets, running water, and AC — crew satisfaction goes way up, which helps retention.",

        "Good question. For longer projects, definitely factor in restroom quality. "
        "I've seen contractors lose good crew members over basic comfort issues on site. "
        "Worth the investment if the project runs more than a couple weeks.",
    ],
}

# ── Content Calendar ─────────────────────────────────────────────────────────

WEEKLY_CONTENT_PLAN = {
    "monday": {
        "actions": [
            {"type": "page_post", "description": "Share a tip or insight post"},
            {"type": "group_helpful_reply", "description": "Reply to 3-5 group questions (helpful, no pitch)"},
        ],
    },
    "tuesday": {
        "actions": [
            {"type": "group_helpful_reply", "description": "Reply to 3-5 group questions (helpful, no pitch)"},
            {"type": "ig_post", "description": "Post a photo with local hashtags (optional)"},
        ],
    },
    "wednesday": {
        "actions": [
            {"type": "page_photo_post", "description": "Behind-the-scenes or setup photo"},
            {"type": "group_helpful_reply", "description": "Reply to 2-3 group questions"},
        ],
    },
    "thursday": {
        "actions": [
            {"type": "group_helpful_reply", "description": "Reply to 3-5 group questions"},
            {"type": "page_comment_reply", "description": "Reply to any page comments/messages"},
        ],
    },
    "friday": {
        "actions": [
            {"type": "page_post", "description": "Seasonal or availability post"},
            {"type": "group_brand_post", "description": "1 thoughtful brand mention in a relevant group thread (only if naturally fits)"},
        ],
    },
    "saturday": {
        "actions": [
            {"type": "marketplace_reply", "description": "Respond to all marketplace inquiries"},
        ],
    },
    "sunday": {
        "actions": [
            {"type": "group_helpful_reply", "description": "Light engagement — 1-2 group replies"},
        ],
    },
}


# ── DB Operations ────────────────────────────────────────────────────────────

def _ensure_table():
    """Create the organic_engagements table if it doesn't exist."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS organic_engagements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            engagement_type TEXT NOT NULL,
            platform TEXT DEFAULT '',
            category TEXT DEFAULT '',
            is_brand_mention INTEGER DEFAULT 0,
            group_name TEXT DEFAULT '',
            post_url TEXT DEFAULT '',
            content TEXT DEFAULT '',
            response_content TEXT DEFAULT '',
            lead_id INTEGER DEFAULT NULL,
            tags TEXT DEFAULT '[]',
            notes TEXT DEFAULT '',
            ts TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_oe_type ON organic_engagements(engagement_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_oe_platform ON organic_engagements(platform)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_oe_ts ON organic_engagements(ts)")
    conn.commit()
    conn.close()


def log_engagement(
    engagement_type: str,
    group_name: str = "",
    post_url: str = "",
    content: str = "",
    response_content: str = "",
    lead_id: Optional[int] = None,
    tags: list = None,
    notes: str = "",
) -> int:
    """
    Log an organic engagement action.
    Returns the row ID.
    """
    _ensure_table()

    type_info = ENGAGEMENT_TYPES.get(engagement_type, {})
    platform = type_info.get("platform", "")
    category = type_info.get("category", "")
    is_brand = 1 if type_info.get("brand_mention", False) else 0

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.execute(
        "INSERT INTO organic_engagements "
        "(engagement_type, platform, category, is_brand_mention, "
        " group_name, post_url, content, response_content, lead_id, tags, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            engagement_type, platform, category, is_brand,
            group_name, post_url, content[:1000], response_content[:1000],
            lead_id, json.dumps(tags or []), notes,
        )
    )
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    print(f"[Organic] Logged {engagement_type} #{row_id}")
    return row_id


def get_engagement_stats(days: int = 30) -> dict:
    """Get engagement statistics for the last N days."""
    _ensure_table()
    conn = sqlite3.connect(str(DB_PATH))
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()

    total = conn.execute(
        "SELECT COUNT(*) FROM organic_engagements WHERE ts > ?", (since,)
    ).fetchone()[0]

    brand_mentions = conn.execute(
        "SELECT COUNT(*) FROM organic_engagements WHERE ts > ? AND is_brand_mention = 1", (since,)
    ).fetchone()[0]

    helpful_comments = conn.execute(
        "SELECT COUNT(*) FROM organic_engagements WHERE ts > ? AND is_brand_mention = 0", (since,)
    ).fetchone()[0]

    by_platform = conn.execute(
        "SELECT platform, COUNT(*) FROM organic_engagements WHERE ts > ? GROUP BY platform", (since,)
    ).fetchall()

    by_type = conn.execute(
        "SELECT engagement_type, COUNT(*) FROM organic_engagements WHERE ts > ? "
        "GROUP BY engagement_type ORDER BY COUNT(*) DESC", (since,)
    ).fetchall()

    by_group = conn.execute(
        "SELECT group_name, COUNT(*) FROM organic_engagements "
        "WHERE ts > ? AND group_name != '' GROUP BY group_name ORDER BY COUNT(*) DESC", (since,)
    ).fetchall()

    conn.close()

    # Calculate the 10:1 ratio health
    ratio = (helpful_comments / brand_mentions) if brand_mentions > 0 else helpful_comments
    ratio_healthy = ratio >= 10 if brand_mentions > 0 else True

    return {
        "period_days": days,
        "total_engagements": total,
        "brand_mentions": brand_mentions,
        "helpful_comments": helpful_comments,
        "helpful_to_brand_ratio": round(ratio, 1),
        "ratio_healthy": ratio_healthy,
        "ratio_target": "10:1 (helpful:brand)",
        "by_platform": {r[0]: r[1] for r in by_platform},
        "by_type": {r[0]: r[1] for r in by_type},
        "by_group": {r[0]: r[1] for r in by_group},
    }


def get_recent_engagements(limit: int = 20) -> list:
    """Get recent engagement activity."""
    _ensure_table()
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT id, engagement_type, platform, category, is_brand_mention, "
        "group_name, post_url, content, lead_id, tags, ts "
        "FROM organic_engagements ORDER BY ts DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [
        {
            "id": r[0], "type": r[1], "platform": r[2], "category": r[3],
            "is_brand_mention": bool(r[4]), "group_name": r[5], "post_url": r[6],
            "content": r[7][:200], "lead_id": r[8],
            "tags": json.loads(r[9]) if r[9] else [], "ts": r[10],
        }
        for r in rows
    ]


def get_todays_plan() -> dict:
    """Get today's content plan based on the weekly calendar."""
    day = datetime.utcnow().strftime("%A").lower()
    plan = WEEKLY_CONTENT_PLAN.get(day, {"actions": []})

    # Get a helpful comment suggestion
    import random
    comment_topics = list(HELPFUL_COMMENTS.keys())
    suggested_topic = random.choice(comment_topics)
    suggested_comment = random.choice(HELPFUL_COMMENTS[suggested_topic])

    return {
        "day": day.title(),
        "actions": plan["actions"],
        "suggested_helpful_comment": {
            "topic": suggested_topic.replace("_", " ").title(),
            "text": suggested_comment,
        },
        "target_groups": [g["name"] for g in TARGET_GROUPS if g["priority"] == "high"][:3],
    }


def get_helpful_comment(topic: str = "") -> str:
    """Get a random helpful comment for a specific topic."""
    import random
    if topic and topic in HELPFUL_COMMENTS:
        return random.choice(HELPFUL_COMMENTS[topic])

    all_comments = []
    for comments in HELPFUL_COMMENTS.values():
        all_comments.extend(comments)
    return random.choice(all_comments)
