#!/usr/bin/env python3
"""Notion Posting Scheduler — creates daily task boards for manual posting.

Since Playwright automation for FB Marketplace/Groups/Craigslist was skipped
(account ban risk), this creates a Notion board each morning with copy-paste
content for every platform. Kai checks off each task as he posts.

Usage:
    python scripts/notion_posting_scheduler.py              # Create today's board
    python scripts/notion_posting_scheduler.py --date 2026-03-08  # Specific date
    python scripts/notion_posting_scheduler.py --status      # Show today's board status
    python scripts/notion_posting_scheduler.py --setup        # Create the Notion database

Requires in ~/.nexus/config.json:
    "notion_token": "ntn_..."
    "notion_database_id": "..."  (auto-created via --setup if missing)

Importable:
    from scripts.notion_posting_scheduler import create_daily_board
"""

import argparse
import json
import logging
import sys
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("notion_scheduler")

CONFIG_PATH = Path.home() / ".nexus" / "config.json"

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

PLATFORM_CONFIG = {
    "fb_marketplace": {
        "label": "FB Marketplace",
        "color": "blue",
        "emoji": "🛒",
        "instructions": "facebook.com/marketplace → Sell → Item for Sale → Event Services. Paste the title and body below. Upload photos from the section below.",
    },
    "craigslist": {
        "label": "Craigslist",
        "color": "orange",
        "emoji": "📌",
        "instructions": "craigslist.org → Post → Services → Event Services. Paste the title and body below. Add photos.",
    },
    "instagram": {
        "label": "Instagram",
        "color": "purple",
        "emoji": "📸",
        "instructions": "Instagram app → New Post → select best photo below → paste caption as the post caption.",
    },
    "fb_page": {
        "label": "FB Page",
        "color": "green",
        "emoji": "📘",
        "instructions": "Facebook Page → Create Post → paste body below. Add a photo from the set below.",
    },
    "fb_groups": {
        "label": "FB Groups",
        "color": "yellow",
        "emoji": "👥",
        "instructions": "Post to each target group. Keep it natural and helpful, not salesy. Paste body below.",
    },
}


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    return json.loads(CONFIG_PATH.read_text())


def _notion_request(method: str, endpoint: str, token: str, body: Optional[dict] = None) -> dict:
    """Make a Notion API request."""
    url = f"{NOTION_API}/{endpoint}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Notion-Version", NOTION_VERSION)
    req.add_header("Content-Type", "application/json")

    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else str(e)
        log.error("Notion API %s %s failed (%d): %s", method, endpoint, e.code, error_body)
        return {"error": True, "status": e.code, "message": error_body}


def setup_database(token: str, parent_page_id: Optional[str] = None) -> Optional[str]:
    """Create the Notion database for daily posting tasks.

    If parent_page_id is not provided, creates in the workspace root.
    Returns the database ID.
    """
    parent = {"type": "page_id", "page_id": parent_page_id} if parent_page_id else {"type": "workspace", "workspace": True}

    db_schema = {
        "parent": parent,
        "title": [{"type": "text", "text": {"content": "Zoar Daily Posting Schedule"}}],
        "properties": {
            "Task": {"title": {}},
            "Platform": {
                "select": {
                    "options": [
                        {"name": cfg["label"], "color": cfg["color"]}
                        for cfg in PLATFORM_CONFIG.values()
                    ]
                }
            },
            "Date": {"date": {}},
            "Language": {
                "select": {
                    "options": [
                        {"name": "English", "color": "blue"},
                        {"name": "Spanish", "color": "red"},
                    ]
                }
            },
            "Posted": {"checkbox": {}},
            "Angle": {"rich_text": {}},
        },
    }

    result = _notion_request("POST", "databases", token, db_schema)
    if result.get("error"):
        log.error("Failed to create database: %s", result.get("message"))
        return None

    db_id = result.get("id")
    log.info("Created Notion database: %s", db_id)

    # Save to config
    cfg = _load_config()
    cfg["notion_database_id"] = db_id
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    log.info("Saved database ID to config")

    return db_id


def _build_content_blocks(post: dict) -> list:
    """Build Notion block children for a posting task."""
    platform = post.get("platform", "unknown")
    pcfg = PLATFORM_CONFIG.get(platform, {})
    instructions = pcfg.get("instructions", "")
    photo_set = post.get("photo_set", "")

    # Load image URLs from config
    cfg = _load_config()
    image_sets = cfg.get("notion_image_sets", {})
    image_urls = image_sets.get(photo_set, []) if photo_set else []

    blocks = []

    # Instructions callout
    if instructions:
        blocks.append({
            "object": "block",
            "type": "callout",
            "callout": {
                "rich_text": [{"type": "text", "text": {"content": instructions}}],
                "icon": {"type": "emoji", "emoji": "📋"},
            },
        })

    blocks.append({"object": "block", "type": "divider", "divider": {}})

    # ✍️ Copy to Paste section
    blocks.append({
        "object": "block",
        "type": "heading_2",
        "heading_2": {
            "rich_text": [{"type": "text", "text": {"content": "✍️  Copy to Paste"}}],
        },
    })

    # Title
    title = post.get("title", "")
    if title:
        blocks.append({
            "object": "block",
            "type": "heading_3",
            "heading_3": {
                "rich_text": [{"type": "text", "text": {"content": "Title"}}],
            },
        })
        blocks.append({
            "object": "block",
            "type": "code",
            "code": {
                "rich_text": [{"type": "text", "text": {"content": title}}],
                "language": "plain text",
            },
        })

    # Body
    body = post.get("body", "")
    if body:
        blocks.append({
            "object": "block",
            "type": "heading_3",
            "heading_3": {
                "rich_text": [{"type": "text", "text": {"content": "Body"}}],
            },
        })
        chunks = [body[i:i+1900] for i in range(0, len(body), 1900)]
        for chunk in chunks:
            blocks.append({
                "object": "block",
                "type": "code",
                "code": {
                    "rich_text": [{"type": "text", "text": {"content": chunk}}],
                    "language": "plain text",
                },
            })

    blocks.append({"object": "block", "type": "divider", "divider": {}})

    # 🖼️ Photos section
    blocks.append({
        "object": "block",
        "type": "heading_2",
        "heading_2": {
            "rich_text": [{"type": "text", "text": {"content": f"🖼️  Photos — Set {photo_set}"}}],
        },
    })

    if image_urls:
        for url in image_urls:
            blocks.append({
                "object": "block",
                "type": "image",
                "image": {
                    "type": "external",
                    "external": {"url": url},
                },
            })
    else:
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": f"Photo set {photo_set} — run upload_notion_images.py to embed images."}}],
            },
        })

    return blocks


def create_task_page(token: str, database_id: str, post: dict, date_str: str) -> Optional[str]:
    """Create a single Notion page (task) for one platform post."""
    platform = post.get("platform", "unknown")
    language = post.get("language", "en")
    pcfg = PLATFORM_CONFIG.get(platform, {})
    label = pcfg.get("label", platform)
    emoji = pcfg.get("emoji", "📄")
    lang_label = "Spanish" if language == "es" else "English"
    angle_raw = post.get("angle_name", post.get("angle_id", ""))
    angle_display = str(angle_raw).replace("_", " ").title()

    task_title = f"{label} ({lang_label}) — {angle_display}"

    page_body = {
        "parent": {"database_id": database_id},
        "icon": {"type": "emoji", "emoji": emoji},
        "properties": {
            "Task": {
                "title": [{"type": "text", "text": {"content": task_title}}],
            },
            "Platform": {"select": {"name": label}},
            "Date": {"date": {"start": date_str}},
            "Language": {"select": {"name": lang_label}},
            "Posted": {"checkbox": False},
            "Angle": {
                "rich_text": [{"type": "text", "text": {"content": angle_display[:100]}}],
            },
        },
        "children": _build_content_blocks(post),
    }

    result = _notion_request("POST", "pages", token, page_body)
    if result.get("error"):
        log.error("Failed to create task for %s: %s", platform, result.get("message"))
        return None

    page_id = result.get("id")
    log.info("Created task: %s (page %s)", task_title, page_id)
    return page_id


def create_daily_board(date: Optional[datetime] = None) -> dict:
    """Create the full daily posting board in Notion.

    Generates posts via PostingEngine and creates one Notion task per platform.

    Returns:
        {"ok": bool, "tasks_created": int, "date": str, "error": str|None}
    """
    cfg = _load_config()
    token = cfg.get("notion_token")
    if not token:
        msg = "No notion_token in config. Run --setup or add it to ~/.nexus/config.json"
        log.warning(msg)
        return {"ok": False, "tasks_created": 0, "date": "", "error": msg}

    database_id = cfg.get("notion_database_id")
    if not database_id:
        msg = "No notion_database_id in config. Run --setup first."
        log.warning(msg)
        return {"ok": False, "tasks_created": 0, "date": "", "error": msg}

    if date is None:
        from zoneinfo import ZoneInfo
        date = datetime.now(ZoneInfo("America/Los_Angeles"))

    date_str = date.strftime("%Y-%m-%d")
    date_display = date.strftime("%B %d, %Y")

    # Check if today's board already exists (avoid duplicates)
    query_result = _notion_request("POST", f"databases/{database_id}/query", token, {
        "filter": {
            "property": "Date",
            "date": {"equals": date_str},
        },
    })
    if not query_result.get("error") and query_result.get("results"):
        existing = len(query_result["results"])
        log.info("Board for %s already exists (%d tasks). Skipping.", date_str, existing)
        return {"ok": True, "tasks_created": 0, "date": date_str,
                "error": f"Already exists ({existing} tasks)"}

    # Generate posts
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from core.posting_engine import PostingEngine
    engine = PostingEngine()
    posts = engine.generate_all_daily_posts()

    if not posts:
        return {"ok": False, "tasks_created": 0, "date": date_str,
                "error": "PostingEngine returned no posts"}

    # Add FB Groups task (manual posting reminder)
    fb_groups = cfg.get("fb_target_groups", [])
    if fb_groups:
        # Reuse the FB marketplace EN post content for groups
        fb_mp_post = next((p for p in posts if p["platform"] == "fb_marketplace" and p["language"] == "en"), None)
        if fb_mp_post:
            group_body = fb_mp_post["body"]
            group_body += "\n\n---\nTarget groups:\n" + "\n".join(f"- {g}" for g in fb_groups)
            posts.append({
                "platform": "fb_groups",
                "language": "en",
                "title": fb_mp_post["title"],
                "body": group_body,
                "angle_id": fb_mp_post.get("angle_id", ""),
                "angle_name": fb_mp_post.get("angle_name", ""),
                "photo_set": fb_mp_post.get("photo_set", []),
            })

    # Create one Notion task per post
    created = 0
    for post in posts:
        page_id = create_task_page(token, database_id, post, date_str)
        if page_id:
            created += 1

    log.info("Created %d/%d tasks for %s", created, len(posts), date_display)

    # Send Telegram confirmation
    try:
        tg_token = cfg.get("telegram_token", "")
        if tg_token:
            tg_msg = f"Notion board created for {date_display}\n{created} posting tasks ready\nOpen Notion to start posting"
            tg_url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
            tg_data = json.dumps({"chat_id": "8540603351", "text": tg_msg}).encode()
            tg_req = urllib.request.Request(tg_url, data=tg_data)
            tg_req.add_header("Content-Type", "application/json")
            urllib.request.urlopen(tg_req)
    except Exception as e:
        log.warning("Telegram notification failed: %s", e)

    return {"ok": True, "tasks_created": created, "date": date_str, "error": None}


def show_status(date: Optional[datetime] = None):
    """Show today's posting board status."""
    cfg = _load_config()
    token = cfg.get("notion_token")
    if not token:
        print("  No Notion token configured.")
        return

    database_id = cfg.get("notion_database_id")
    if not database_id:
        print("  No Notion database configured. Run --setup first.")
        return

    if date is None:
        from zoneinfo import ZoneInfo
        date = datetime.now(ZoneInfo("America/Los_Angeles"))

    date_str = date.strftime("%Y-%m-%d")

    result = _notion_request("POST", f"databases/{database_id}/query", token, {
        "filter": {
            "property": "Date",
            "date": {"equals": date_str},
        },
    })

    if result.get("error"):
        print(f"  Error querying Notion: {result.get('message')}")
        return

    tasks = result.get("results", [])
    if not tasks:
        print(f"  No posting board for {date_str}. Run without --status to create one.")
        return

    print(f"\n  Posting Board — {date_str}")
    print(f"  {'=' * 50}")
    done = 0
    for task in tasks:
        props = task.get("properties", {})
        title = ""
        title_prop = props.get("Task", {}).get("title", [])
        if title_prop:
            title = title_prop[0].get("plain_text", "")
        posted = props.get("Posted", {}).get("checkbox", False)
        icon = "DONE" if posted else "TODO"
        if posted:
            done += 1
        print(f"  [{icon}] {title}")

    print(f"  {'=' * 50}")
    print(f"  Progress: {done}/{len(tasks)} posted")


def main():
    parser = argparse.ArgumentParser(description="Notion Posting Scheduler")
    parser.add_argument("--date", help="Date to create board for (YYYY-MM-DD)")
    parser.add_argument("--status", action="store_true", help="Show today's board status")
    parser.add_argument("--setup", action="store_true", help="Create the Notion database")
    parser.add_argument("--parent-page", help="Notion page ID to create database under (for --setup)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    cfg = _load_config()
    token = cfg.get("notion_token")

    if args.setup:
        if not token:
            print("  Add 'notion_token' to ~/.nexus/config.json first.")
            print("  Get one at: https://www.notion.so/my-integrations")
            sys.exit(1)
        db_id = setup_database(token, args.parent_page)
        if db_id:
            print(f"  Database created: {db_id}")
            print(f"  Saved to config. You can now run without --setup.")
        else:
            print("  Failed to create database.")
            sys.exit(1)
        return

    if args.status:
        target_date = None
        if args.date:
            target_date = datetime.strptime(args.date, "%Y-%m-%d")
        show_status(target_date)
        return

    # Default: create today's board
    target_date = None
    if args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d")

    result = create_daily_board(target_date)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result["ok"]:
            if result["tasks_created"] > 0:
                print(f"\n  Notion board created for {result['date']}")
                print(f"  {result['tasks_created']} posting tasks ready")
                print(f"  Open Notion to start posting!")
            else:
                print(f"\n  {result.get('error', 'Board already exists')}")
        else:
            print(f"\n  Error: {result['error']}")
            sys.exit(1)


if __name__ == "__main__":
    main()
