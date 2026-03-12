"""
Marketplace Posting Scheduler — Sends Telegram reminders
when it's time to post a new listing.

Runs as a background task checking once per hour.
When a posting window opens, generates the listing content
and sends it to Kai via Telegram.
"""
from __future__ import annotations
import asyncio, json
from datetime import datetime, timedelta

from integrations.marketplace import (
    generate_listing, can_post, get_posting_schedule,
    CL_REGIONS, VARIATIONS,
)

_running = False
_tg_notify = None
CHECK_INTERVAL = 3600  # Check every hour


def init_scheduler(notify_fn):
    """Pass in the Telegram notify function."""
    global _tg_notify
    _tg_notify = notify_fn
    print("[PostScheduler] Initialized")


async def run_scheduler():
    """Background loop — checks schedule and sends reminders."""
    global _running
    _running = True
    print("[PostScheduler] Scheduler started")

    while _running:
        try:
            await _check_and_notify()
        except Exception as e:
            print(f"[PostScheduler] Error: {e}")
        await asyncio.sleep(CHECK_INTERVAL)


async def _check_and_notify():
    """Check if it's time to post and send a Telegram notification."""
    from zoneinfo import ZoneInfo
    now_la = datetime.now(ZoneInfo("America/Los_Angeles"))
    hour = now_la.hour  # Correct LA time — auto-adjusts for DST
    now = datetime.utcnow()  # Keep UTC for date comparisons

    # Only send reminders between 8 AM and 10 AM PST
    if hour < 8 or hour > 10:
        return

    schedule = get_posting_schedule()
    today = now.strftime("%Y-%m-%d")

    for item in schedule:
        if item["date"] != today:
            continue

        platform = item["platform"]
        region = item.get("region", "")

        # Check if it's safe to post
        safety = can_post(platform, region)
        if not safety["ok"]:
            continue

        # Generate the listing
        listing = generate_listing(
            platform=platform,
            variation=item.get("variation", ""),
            region=region,
        )

        # Format Telegram message
        msg = _format_listing_message(listing, item)
        if _tg_notify:
            await _tg_notify(msg)


def _format_listing_message(listing: dict, schedule_item: dict) -> str:
    """Format a listing into a Telegram-friendly message."""
    platform_emoji = "📘" if listing["platform"] == "fb_marketplace" else "📋"
    region_info = ""
    if listing.get("region"):
        rname = CL_REGIONS.get(listing["region"], {}).get("name", listing["region"])
        region_info = f"\n📍 Region: {rname}"

    return (
        f"{platform_emoji} TIME TO POST — {listing['platform'].replace('_', ' ').title()}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📌 {schedule_item.get('action', 'Post listing')}\n"
        f"🎯 Angle: {listing['variation'].title()}{region_info}\n"
        f"💰 Price: ${listing['price']}\n\n"
        f"📝 TITLE:\n{listing['title']}\n\n"
        f"📄 DESCRIPTION:\n{listing['description']}\n\n"
        f"🖼 IMAGES (in order):\n"
        + "\n".join(f"  {i+1}. {img}" for i, img in enumerate(listing["images"]))
        + f"\n\n💡 After posting, tell me the URL and I'll track it in Nexus."
    )


async def generate_and_send_now(platform: str = "fb_marketplace", variation: str = "", region: str = ""):
    """Generate a listing right now and send via Telegram. For on-demand use."""
    listing = generate_listing(platform=platform, variation=variation, region=region)
    safety = can_post(platform, region)

    msg = (
        f"📋 GENERATED LISTING\n"
        f"━━━━━━━━━━━━━━━━━━\n"
    )

    if not safety["ok"]:
        msg += f"⚠️ Safety: {safety['reason']}\n\n"

    msg += (
        f"🎯 Platform: {platform.replace('_', ' ').title()}\n"
        f"🎨 Angle: {listing['variation'].title()}\n"
        f"💰 Price: ${listing['price']}\n\n"
        f"📝 TITLE:\n{listing['title']}\n\n"
        f"📄 DESCRIPTION:\n{listing['description']}\n\n"
        f"🖼 IMAGES:\n"
        + "\n".join(f"  {i+1}. {img}" for i, img in enumerate(listing["images"]))
    )

    if _tg_notify:
        await _tg_notify(msg)

    return listing


def stop_scheduler():
    global _running
    _running = False
    print("[PostScheduler] Stopped")
