"""
Facebook Comment Monitor — Watches page posts for new comments.

Periodically checks recent posts for new comments, notifies Kai via Telegram,
and optionally auto-replies with a CTA. Tracks which comments have been
processed to avoid duplicate notifications.

Also provides retargeting utilities:
  - Custom Audience creation guide (manual steps for Ads Manager)
  - Engagement-based audience documentation

Uses the existing FB Page token from config.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"
GRAPH_API = "https://graph.facebook.com/v25.0"

_running = False
_tg_notify = None
CHECK_INTERVAL = 300  # 5 minutes


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def _token() -> str:
    return _load_config().get("fb_page_access_token", "")


def _page_id() -> str:
    return _load_config().get("fb_page_id", "")


def init_comment_monitor(notify_fn):
    """Initialize with Telegram notify function."""
    global _tg_notify
    _tg_notify = notify_fn
    _init_tables()
    print("[CommentMonitor] Initialized")


def _init_tables():
    """Create comment tracking table."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fb_comment_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            comment_id TEXT UNIQUE NOT NULL,
            post_id TEXT NOT NULL,
            from_name TEXT,
            from_id TEXT,
            message TEXT,
            replied BOOLEAN DEFAULT 0,
            reply_text TEXT,
            notified BOOLEAN DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


async def run_comment_monitor():
    """Background loop — checks for new comments every 5 minutes."""
    global _running
    _running = True
    print("[CommentMonitor] Monitor started")

    while _running:
        try:
            await _check_for_new_comments()
        except Exception as e:
            print(f"[CommentMonitor] Error: {e}")
        await asyncio.sleep(CHECK_INTERVAL)


def stop_comment_monitor():
    global _running
    _running = False
    print("[CommentMonitor] Stopped")


async def _check_for_new_comments():
    """Check recent posts for new comments."""
    import httpx

    token = _token()
    page_id = _page_id()
    if not token or not page_id:
        return

    # Get recent posts (last 10)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{GRAPH_API}/{page_id}/posts",
                params={
                    "fields": "id,message,created_time",
                    "limit": 10,
                    "access_token": token,
                }
            )
            posts = resp.json().get("data", [])
    except Exception as e:
        print(f"[CommentMonitor] Error fetching posts: {e}")
        return

    new_comments = []

    for post in posts:
        post_id = post.get("id", "")
        if not post_id:
            continue

        # Get comments for this post
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{GRAPH_API}/{post_id}/comments",
                    params={
                        "fields": "id,message,from,created_time",
                        "limit": 25,
                        "access_token": token,
                    }
                )
                comments = resp.json().get("data", [])
        except Exception:
            continue

        # Check each comment against our log
        conn = sqlite3.connect(str(DB_PATH))
        for comment in comments:
            cid = comment.get("id", "")
            if not cid:
                continue

            # Skip if already processed
            existing = conn.execute(
                "SELECT id FROM fb_comment_log WHERE comment_id = ?", (cid,)
            ).fetchone()
            if existing:
                continue

            # New comment — record it
            from_data = comment.get("from", {})
            from_name = from_data.get("name", "Unknown")
            from_id = from_data.get("id", "")
            message = comment.get("message", "")

            conn.execute(
                "INSERT INTO fb_comment_log (comment_id, post_id, from_name, from_id, message) "
                "VALUES (?, ?, ?, ?, ?)",
                (cid, post_id, from_name, from_id, message)
            )
            new_comments.append({
                "comment_id": cid,
                "post_id": post_id,
                "from_name": from_name,
                "from_id": from_id,
                "message": message,
                "post_preview": post.get("message", "")[:80],
            })

        conn.commit()
        conn.close()

    # Notify about new comments
    if new_comments and _tg_notify:
        for c in new_comments[:5]:  # Max 5 notifications at once
            text = (
                f"💬 *New FB Comment*\n"
                f"From: {c['from_name']}\n"
                f"Post: {c['post_preview']}...\n\n"
                f"Comment: {c['message'][:200]}\n\n"
                f"Reply? Type: reply {c['comment_id'][:20]} [your response]"
            )
            await _tg_notify(text)

        if len(new_comments) > 5:
            await _tg_notify(f"... and {len(new_comments) - 5} more new comments. Check CRM dashboard.")


async def reply_to_comment(comment_id: str, reply_text: str) -> dict:
    """Reply to a Facebook comment."""
    import httpx

    token = _token()
    if not token:
        return {"ok": False, "error": "No page token configured"}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{GRAPH_API}/{comment_id}/comments",
                data={
                    "message": reply_text,
                    "access_token": token,
                }
            )
            result = resp.json()

            if result.get("id"):
                # Record reply
                conn = sqlite3.connect(str(DB_PATH))
                conn.execute(
                    "UPDATE fb_comment_log SET replied = 1, reply_text = ? WHERE comment_id = ?",
                    (reply_text, comment_id)
                )
                conn.commit()
                conn.close()
                return {"ok": True, "reply_id": result["id"]}
            else:
                return {"ok": False, "error": str(result)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_comment_stats() -> dict:
    """Get comment monitoring stats."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        total = conn.execute("SELECT COUNT(*) FROM fb_comment_log").fetchone()[0]
        replied = conn.execute("SELECT COUNT(*) FROM fb_comment_log WHERE replied = 1").fetchone()[0]
        today = conn.execute(
            "SELECT COUNT(*) FROM fb_comment_log WHERE date(created_at) = date('now')"
        ).fetchone()[0]
        conn.close()
        return {"total": total, "replied": replied, "unreplied": total - replied, "today": today}
    except Exception:
        return {"total": 0, "replied": 0, "unreplied": 0, "today": 0}


# ═══════════════════════════════════════════════════════════════════════════
# RETARGETING DOCUMENTATION
# ═══════════════════════════════════════════════════════════════════════════

RETARGETING_GUIDE = {
    "title": "Facebook Retargeting Setup Guide — Zoar Bathroom Rental",

    "custom_audiences": [
        {
            "name": "Website Visitors - Last 30 Days",
            "type": "website",
            "steps": [
                "Go to Ads Manager → Audiences → Create Audience → Custom Audience",
                "Select 'Website' as the source",
                "Set rule: 'All website visitors' in the last 30 days",
                "Name it 'WV - All - 30d'",
                "Click 'Create Audience'",
            ],
            "requires": "Facebook Pixel installed on zoarbathroomrental.com",
        },
        {
            "name": "Website Visitors - Quote Page",
            "type": "website",
            "steps": [
                "Create Custom Audience → Website",
                "Set rule: 'People who visited specific web pages'",
                "URL contains: '/quote' or '/contact'",
                "Retention: 60 days",
                "Name: 'WV - Quote Page - 60d'",
            ],
            "requires": "Pixel + quote/contact page exists",
        },
        {
            "name": "FB/IG Engagement - Last 90 Days",
            "type": "engagement",
            "steps": [
                "Create Custom Audience → Facebook Page",
                "Select 'Everyone who engaged with your Page'",
                "Retention: 90 days",
                "Name: 'Engaged - Page - 90d'",
            ],
            "requires": "Facebook Page with posts",
        },
        {
            "name": "Video Viewers - 50%+ Watch Time",
            "type": "engagement",
            "steps": [
                "Create Custom Audience → Video",
                "Select videos from your page",
                "Engagement type: 'People who watched at least 50%'",
                "Retention: 365 days",
                "Name: 'Video - 50% Watch - 365d'",
            ],
            "requires": "Video content posted to Page",
        },
        {
            "name": "Lead Form Openers (Didn't Submit)",
            "type": "lead_form",
            "steps": [
                "Create Custom Audience → Lead Form",
                "Select your lead form",
                "People who opened but didn't submit",
                "Retention: 90 days",
                "Name: 'Lead Form - Opened Not Submitted - 90d'",
            ],
            "requires": "Active lead form campaigns",
        },
        {
            "name": "Lookalike - Existing Customers",
            "type": "lookalike",
            "steps": [
                "Create Lookalike Audience",
                "Source: Customer list (upload CSV of converted leads)",
                "Location: United States",
                "Audience size: 1%",
                "Name: 'LAL - Customers - 1%'",
            ],
            "requires": "10+ converted customers in CSV",
        },
    ],

    "retargeting_campaigns": [
        {
            "name": "Warm Retarget — Website Visitors",
            "audience": "WV - All - 30d",
            "objective": "OUTCOME_LEADS",
            "budget": "$5/day",
            "creative": "Testimonial-style ad with social proof",
            "notes": "These people already visited the site. Show them proof and a clear CTA.",
        },
        {
            "name": "Hot Retarget — Quote Page Visitors",
            "audience": "WV - Quote Page - 60d",
            "objective": "OUTCOME_LEADS",
            "budget": "$5/day",
            "creative": "Urgency ad: 'Still planning your event? Get your free quote today'",
            "notes": "Highest intent. They were on the quote page but didn't convert.",
        },
        {
            "name": "Engagement Retarget — Page Engagers",
            "audience": "Engaged - Page - 90d",
            "objective": "OUTCOME_LEADS",
            "budget": "$3/day",
            "creative": "Behind-the-scenes or client testimonial video",
            "notes": "Social engagement indicates interest. Nurture with content.",
        },
    ],
}


def get_retargeting_guide() -> dict:
    """Get the full retargeting guide."""
    return RETARGETING_GUIDE


def format_retargeting_for_telegram() -> str:
    """Format retargeting guide as Telegram message."""
    guide = RETARGETING_GUIDE
    lines = [
        "🎯 *FACEBOOK RETARGETING GUIDE*",
        "━" * 35,
        "",
        "*Custom Audiences to Create:*",
        "",
    ]

    for i, audience in enumerate(guide["custom_audiences"], 1):
        lines.append(f"{i}. *{audience['name']}*")
        lines.append(f"   Type: {audience['type']}")
        lines.append(f"   Requires: {audience['requires']}")
        lines.append("")

    lines.append("*Retargeting Campaigns:*")
    lines.append("")

    for camp in guide["retargeting_campaigns"]:
        lines.append(f"📢 *{camp['name']}*")
        lines.append(f"   Audience: {camp['audience']}")
        lines.append(f"   Budget: {camp['budget']}")
        lines.append(f"   Note: {camp['notes']}")
        lines.append("")

    return "\n".join(lines)
