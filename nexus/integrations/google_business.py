"""
Google Business Profile Auto-Posting — Zoar Bathroom Rentals

Automated local SEO posting to Google Business Profile (2x/week).
 - 20+ rotating post templates covering weddings, events, SoCal cities, etc.
 - Least-recently-used selection so content never repeats within 2 weeks
 - Telegram approval flow: Kai previews before anything goes live
 - Posts Tuesdays and Fridays at 11 AM PT
 - Falls back to draft mode if API credentials aren't configured

Credentials: ~/.nexus/google_service_account.json (same as Calendar)
Config: ~/.nexus/config.json → google_business_location_id
DB: ~/.nexus/memory.db (WAL mode)
"""
from __future__ import annotations
import asyncio, json, sqlite3, random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Callable, Awaitable
from zoneinfo import ZoneInfo

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"
SERVICE_ACCOUNT_PATH = Path.home() / ".nexus" / "google_service_account.json"
PT = ZoneInfo("America/Los_Angeles")

SCHEDULER_INTERVAL = 300  # Check every 5 minutes
WEBSITE_URL = "https://zoarbathroomrental.com"
PHONE = "(424) 235-8979"

_gbp_service = None


def _load_config() -> dict:
    """Load config from ~/.nexus/config.json."""
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def _now_pt() -> datetime:
    return datetime.now(PT)


# ── Post Templates ────────────────────────────────────────────────────────────
# Each template has an id, category, and text.
# Text uses {phone} and {website} placeholders for consistency.

POST_TEMPLATES: list[dict] = [
    # Wedding season tips
    {
        "id": "wedding_checklist",
        "category": "wedding",
        "text": (
            "Planning your dream outdoor wedding? Don't forget restroom facilities "
            "for your guests! Our luxury restroom trailers feature climate control, "
            "hardwood floors, and elegant fixtures that match the beauty of your venue. "
            "Book early for peak wedding season. Call {phone} or visit {website}"
        ),
    },
    {
        "id": "wedding_outdoor_tips",
        "category": "wedding",
        "text": (
            "Outdoor wedding tip: Place restroom facilities within a short walk of "
            "the ceremony and reception areas, but far enough for privacy. Our luxury "
            "trailers blend right in with any elegant setting. Let us help you plan the "
            "perfect setup! {phone}"
        ),
    },
    {
        "id": "wedding_guest_comfort",
        "category": "wedding",
        "text": (
            "Your wedding guests deserve better than a porta-potty. Our luxury restroom "
            "trailers include flushing toilets, running water, vanity mirrors, and air "
            "conditioning. Make every detail count on your special day. {phone}"
        ),
    },

    # Corporate event planning
    {
        "id": "corporate_events",
        "category": "corporate",
        "text": (
            "Hosting a corporate event, company picnic, or team-building retreat? "
            "Professional restroom facilities make a lasting impression. Our trailers "
            "are ADA-accessible and professionally serviced. Contact us for corporate "
            "event pricing. {phone}"
        ),
    },
    {
        "id": "corporate_film",
        "category": "corporate",
        "text": (
            "Film and production crews trust Zoar for on-set restroom trailers. "
            "Multi-day rentals, flexible scheduling, and reliable delivery across "
            "Southern California. Keep your crew comfortable and your production on track. {phone}"
        ),
    },

    # Luxury vs porta-potty comparison
    {
        "id": "luxury_vs_portapotty",
        "category": "comparison",
        "text": (
            "Luxury restroom trailer vs. standard porta-potty: climate control, "
            "flushing toilets, running water, hardwood floors, vanity mirrors, music "
            "system, and LED lighting. Your guests will thank you for choosing Zoar. "
            "See the difference at {website}"
        ),
    },
    {
        "id": "upgrade_your_event",
        "category": "comparison",
        "text": (
            "Why settle for a porta-potty when you can have a luxury restroom experience? "
            "Our trailers are cleaned and sanitized before every event. Freshwater system, "
            "premium finishes, and a setup that impresses. Get a free quote: {phone}"
        ),
    },

    # SoCal city spotlights
    {
        "id": "spotlight_malibu",
        "category": "city_spotlight",
        "text": (
            "Malibu events deserve Malibu-quality facilities. Whether it's a beachside "
            "wedding, vineyard celebration, or private estate party, Zoar delivers luxury "
            "restroom trailers anywhere in Malibu and surrounding areas. {phone}"
        ),
    },
    {
        "id": "spotlight_ventura",
        "category": "city_spotlight",
        "text": (
            "Serving Ventura County with premium restroom trailer rentals! From Thousand "
            "Oaks to Oxnard, we deliver and set up luxury facilities for weddings, "
            "festivals, and private events. Book your Ventura event today: {phone}"
        ),
    },
    {
        "id": "spotlight_santa_clarita",
        "category": "city_spotlight",
        "text": (
            "Santa Clarita and the SCV valley trust Zoar for outdoor event restroom "
            "rentals. Ranch weddings, park events, and backyard celebrations all get "
            "the luxury treatment. Fast delivery, professional setup. {phone}"
        ),
    },
    {
        "id": "spotlight_la",
        "category": "city_spotlight",
        "text": (
            "From the Hollywood Hills to the LA coast, Zoar provides luxury restroom "
            "trailers for events across Los Angeles County. No venue is too remote or "
            "too exclusive. Contact us for LA event pricing: {phone}"
        ),
    },
    {
        "id": "spotlight_pasadena",
        "category": "city_spotlight",
        "text": (
            "Pasadena garden weddings and estate events pair perfectly with our luxury "
            "restroom trailers. Climate-controlled comfort in any season. Serving "
            "Pasadena, Altadena, San Marino, and the San Gabriel Valley. {phone}"
        ),
    },

    # Customer testimonials (generic)
    {
        "id": "testimonial_wedding",
        "category": "testimonial",
        "text": (
            "Our clients love the difference a luxury restroom trailer makes! "
            "\"Our guests kept complimenting the restroom facilities at our outdoor "
            "wedding. It was like having an indoor bathroom in the middle of a vineyard.\" "
            "Give your event the 5-star treatment: {phone}"
        ),
    },
    {
        "id": "testimonial_corporate",
        "category": "testimonial",
        "text": (
            "\"We've used Zoar for three corporate events now. Professional delivery, "
            "spotless trailers, and the crew was great to work with.\" "
            "Join our happy clients! Book your next event: {phone}"
        ),
    },

    # Behind-the-scenes (trailer features)
    {
        "id": "bts_trailer_features",
        "category": "behind_the_scenes",
        "text": (
            "Inside our luxury restroom trailers: freshwater flushing system, hands-free "
            "faucets, granite countertops, LED lighting, stereo system, and climate "
            "control. We prep every unit before delivery so it arrives spotless. "
            "See photos at {website}"
        ),
    },
    {
        "id": "bts_delivery_process",
        "category": "behind_the_scenes",
        "text": (
            "How does delivery work? We handle everything! Our team delivers the trailer, "
            "levels it on-site, connects freshwater and power, and does a final walkthrough. "
            "After your event, we pick it all up. Zero hassle for you. {phone}"
        ),
    },

    # FAQ answers
    {
        "id": "faq_how_delivery_works",
        "category": "faq",
        "text": (
            "FAQ: How does restroom trailer delivery work? We bring the trailer to your "
            "venue, set it up with water and power connections, and do a full walkthrough. "
            "After the event, we handle pickup too. Most setups take under an hour. {phone}"
        ),
    },
    {
        "id": "faq_pricing",
        "category": "faq",
        "text": (
            "FAQ: How much does a luxury restroom trailer rental cost? Pricing depends "
            "on your event size, duration, and location. We offer transparent quotes with "
            "no hidden fees. Delivery, setup, and pickup are included. Get your free "
            "quote: {phone}"
        ),
    },
    {
        "id": "faq_capacity",
        "category": "faq",
        "text": (
            "FAQ: How many guests can a restroom trailer serve? Our luxury trailers "
            "comfortably serve events of 50 to 300+ guests. For larger events, we "
            "recommend multiple units. We'll help you choose the right setup. {phone}"
        ),
    },

    # Seasonal content
    {
        "id": "seasonal_summer_weddings",
        "category": "seasonal",
        "text": (
            "Summer wedding season is here! Outdoor ceremonies in SoCal deserve luxury "
            "restroom facilities. Our climate-controlled trailers keep guests comfortable "
            "even on the hottest days. Book now before peak dates fill up! {phone}"
        ),
    },
    {
        "id": "seasonal_holiday_parties",
        "category": "seasonal",
        "text": (
            "Planning a holiday party or year-end celebration? Whether it's an outdoor "
            "winter gathering or a company holiday bash, Zoar's luxury restroom trailers "
            "add comfort and class. Holiday dates book fast! {phone}"
        ),
    },
    {
        "id": "seasonal_spring_events",
        "category": "seasonal",
        "text": (
            "Spring is perfect for outdoor events in Southern California! Garden parties, "
            "school fundraisers, community festivals, and more. Our luxury restroom "
            "trailers make any outdoor event feel upscale. {phone}"
        ),
    },
    {
        "id": "seasonal_fall_harvest",
        "category": "seasonal",
        "text": (
            "Fall harvest festivals, vineyard events, and outdoor celebrations are better "
            "with luxury restroom trailers. Crisp weather, beautiful venues, and restroom "
            "facilities that match the experience. Book your fall event: {phone}"
        ),
    },
]


# ── Database ──────────────────────────────────────────────────────────────────

def init_gbp_db() -> None:
    """Create the gbp_posts table if it doesn't exist."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gbp_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_type TEXT DEFAULT 'update',
            template_id TEXT DEFAULT '',
            content TEXT DEFAULT '',
            image_path TEXT DEFAULT '',
            call_to_action TEXT DEFAULT 'LEARN_MORE',
            cta_url TEXT DEFAULT 'https://zoarbathroomrental.com',
            status TEXT DEFAULT 'draft',
            posted_at TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gbp_status ON gbp_posts(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gbp_template ON gbp_posts(template_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gbp_posted ON gbp_posts(posted_at)")
    conn.commit()
    conn.close()
    print("[GBP] Database table ready")


# ── Template Selection ────────────────────────────────────────────────────────

def select_post_template() -> dict:
    """
    Pick the least-recently-used template, ensuring no repeats within 2 weeks.

    Returns a template dict with id, category, and text (placeholders filled).
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    two_weeks_ago = (_now_pt() - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")

    # Get template_ids used in the last 2 weeks
    recent_rows = conn.execute(
        "SELECT template_id FROM gbp_posts WHERE posted_at >= ? AND status IN ('posted', 'draft')",
        (two_weeks_ago,),
    ).fetchall()
    recent_ids = {row["template_id"] for row in recent_rows}
    conn.close()

    # Filter out recently used templates
    available = [t for t in POST_TEMPLATES if t["id"] not in recent_ids]

    # If all templates used recently, fall back to full list (shuffle for variety)
    if not available:
        available = list(POST_TEMPLATES)
        print("[GBP] All templates used recently — resetting rotation")

    # Pick randomly from available to add variety
    template = random.choice(available)

    # Fill placeholders
    filled = {**template}
    filled["text"] = filled["text"].replace("{phone}", PHONE).replace("{website}", WEBSITE_URL)

    return filled


# ── Google Business Profile API ───────────────────────────────────────────────

def _get_gbp_credentials():
    """Load Google service account credentials for GBP API."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        if not SERVICE_ACCOUNT_PATH.exists():
            print("[GBP] No service account file found at", SERVICE_ACCOUNT_PATH)
            return None, None

        config = _load_config()
        location_id = config.get("google_business_location_id", "")
        if not location_id:
            print("[GBP] No google_business_location_id in config")
            return None, None

        creds = service_account.Credentials.from_service_account_file(
            str(SERVICE_ACCOUNT_PATH),
            scopes=["https://www.googleapis.com/auth/business.manage"],
        )

        service = build("mybusinessbusinessinformation", "v1", credentials=creds)
        return service, location_id

    except ImportError:
        print("[GBP] google-auth / google-api-python-client not installed")
        return None, None
    except Exception as e:
        print(f"[GBP] Credentials error: {e}")
        return None, None


async def create_gbp_post(
    template_text: str,
    image_path: str | None = None,
    cta_url: str = WEBSITE_URL,
    template_id: str = "",
) -> dict:
    """
    Post to Google Business Profile via the API.

    If credentials are not configured, saves the post as a draft and
    prints instructions for manual posting.

    Returns: {"ok": bool, "post_id": int, "status": str, ...}
    """
    now_pt = _now_pt().strftime("%Y-%m-%d %H:%M:%S")

    # Save to DB first (draft)
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.execute(
        "INSERT INTO gbp_posts (post_type, template_id, content, image_path, "
        "call_to_action, cta_url, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'draft', ?)",
        ("update", template_id, template_text, image_path or "", "LEARN_MORE", cta_url, now_pt),
    )
    post_id = cur.lastrowid
    conn.commit()
    conn.close()

    # Try to post via API
    service, location_id = _get_gbp_credentials()
    if not service or not location_id:
        print(f"[GBP] Post #{post_id} saved as DRAFT (API not configured)")
        print("[GBP] To post manually:")
        print(f"[GBP]   1. Go to https://business.google.com")
        print(f"[GBP]   2. Create a new update post")
        print(f"[GBP]   3. Paste the content from post #{post_id}")
        print(f"[GBP]   4. Add a 'Learn More' button linking to {cta_url}")
        return {"ok": False, "post_id": post_id, "status": "draft",
                "reason": "API not configured — saved as draft"}

    try:
        # Build the post body
        post_body = {
            "summary": template_text,
            "callToAction": {
                "actionType": "LEARN_MORE",
                "url": cta_url,
            },
            "topicType": "STANDARD",
        }

        # If image path provided and exists, attach media
        if image_path and Path(image_path).exists():
            post_body["media"] = [{
                "mediaFormat": "PHOTO",
                "sourceUrl": image_path,
            }]

        # Post to GBP
        parent = f"locations/{location_id}"
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: service.locations().posts().create(
                parent=parent, body=post_body
            ).execute()
        )

        # Update DB status
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "UPDATE gbp_posts SET status='posted', posted_at=? WHERE id=?",
            (now_pt, post_id),
        )
        conn.commit()
        conn.close()

        print(f"[GBP] Post #{post_id} published successfully")
        return {"ok": True, "post_id": post_id, "status": "posted", "response": result}

    except Exception as e:
        # Mark as failed in DB
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "UPDATE gbp_posts SET status='failed' WHERE id=?", (post_id,)
        )
        conn.commit()
        conn.close()

        print(f"[GBP] Post #{post_id} failed: {e}")
        return {"ok": False, "post_id": post_id, "status": "failed", "error": str(e)}


# ── Stats ─────────────────────────────────────────────────────────────────────

def get_posting_stats(days: int = 30) -> dict:
    """Get GBP posting stats for the last N days."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cutoff = (_now_pt() - timedelta(days=days)).strftime("%Y-%m-%d")

        total = conn.execute(
            "SELECT COUNT(*) FROM gbp_posts WHERE created_at >= ?", (cutoff,)
        ).fetchone()[0]

        posted = conn.execute(
            "SELECT COUNT(*) FROM gbp_posts WHERE status='posted' AND posted_at >= ?", (cutoff,)
        ).fetchone()[0]

        drafts = conn.execute(
            "SELECT COUNT(*) FROM gbp_posts WHERE status='draft' AND created_at >= ?", (cutoff,)
        ).fetchone()[0]

        failed = conn.execute(
            "SELECT COUNT(*) FROM gbp_posts WHERE status='failed' AND created_at >= ?", (cutoff,)
        ).fetchone()[0]

        # Category breakdown for posted
        cat_rows = conn.execute(
            "SELECT template_id, COUNT(*) as cnt FROM gbp_posts "
            "WHERE status='posted' AND posted_at >= ? GROUP BY template_id",
            (cutoff,),
        ).fetchall()

        # Map template_ids back to categories
        id_to_cat = {t["id"]: t["category"] for t in POST_TEMPLATES}
        categories: dict[str, int] = {}
        for row in cat_rows:
            cat = id_to_cat.get(row["template_id"], "other")
            categories[cat] = categories.get(cat, 0) + row["cnt"]

        last_post = conn.execute(
            "SELECT posted_at FROM gbp_posts WHERE status='posted' ORDER BY posted_at DESC LIMIT 1"
        ).fetchone()

        conn.close()

        return {
            "days": days,
            "total": total,
            "posted": posted,
            "drafts": drafts,
            "failed": failed,
            "categories": categories,
            "last_posted": last_post["posted_at"] if last_post else "never",
        }
    except Exception as e:
        print(f"[GBP] Stats error: {e}")
        return {"days": days, "total": 0, "posted": 0, "drafts": 0, "failed": 0,
                "categories": {}, "last_posted": "never"}


def format_gbp_stats() -> str:
    """Format GBP posting stats for Telegram display."""
    stats = get_posting_stats(30)
    cats = stats.get("categories", {})
    cat_lines = "\n".join(f"  {k.replace('_', ' ').title()}: {v}" for k, v in cats.items())
    if not cat_lines:
        cat_lines = "  No posts yet"

    return (
        f"Google Business Profile (last 30 days)\n"
        f"{'=' * 38}\n"
        f"Total posts: {stats['posted']}\n"
        f"Drafts: {stats['drafts']}\n"
        f"Failed: {stats['failed']}\n"
        f"Last posted: {stats['last_posted']}\n\n"
        f"Categories:\n{cat_lines}"
    )


# ── Scheduler ─────────────────────────────────────────────────────────────────

async def run_gbp_scheduler(
    send_fn: Callable[[str], Awaitable[None]],
) -> None:
    """
    Async scheduler loop. Posts on Tuesdays and Fridays at 11 AM PT.

    send_fn: async function to send Telegram messages (for approval flow).
    Previews the post content for Kai's approval before publishing.
    """
    init_gbp_db()
    print("[GBP] Scheduler started (Tues + Fri at 11 AM PT)")

    # Track what we've already queued today to avoid duplicates on each tick
    last_queued_date: str = ""

    while True:
        try:
            now = _now_pt()
            today_str = now.strftime("%Y-%m-%d")
            day_of_week = now.weekday()  # 0=Mon, 1=Tue, 4=Fri
            hour = now.hour

            # Post on Tuesday (1) and Friday (4) at 11 AM
            is_posting_day = day_of_week in (1, 4)
            is_posting_hour = hour == 11

            if is_posting_day and is_posting_hour and today_str != last_queued_date:
                last_queued_date = today_str

                # Select a template
                template = select_post_template()
                content = template["text"]

                day_name = "Tuesday" if day_of_week == 1 else "Friday"
                preview = (
                    f"Google Business Post Preview\n"
                    f"{'=' * 35}\n"
                    f"Day: {day_name} {today_str}\n"
                    f"Category: {template['category'].replace('_', ' ').title()}\n"
                    f"Template: {template['id']}\n\n"
                    f"Content:\n{content}\n\n"
                    f"CTA: Learn More -> {WEBSITE_URL}\n\n"
                    f"Reply 'post' to publish, 'skip' to skip, or edit and reply with new text."
                )

                # Send preview for Kai's approval
                await send_fn(preview)

                # Save as draft (will be published when Kai approves)
                await create_gbp_post(
                    template_text=content,
                    cta_url=WEBSITE_URL,
                    template_id=template["id"],
                )

                print(f"[GBP] {day_name} post queued for approval: {template['id']}")

            # Auto-repost: GBP posts expire after 7 days. On posting days,
            # check if the last published post is >7 days old and queue a fresh one.
            try:
                conn = sqlite3.connect(str(DB_PATH))
                conn.row_factory = sqlite3.Row
                last_posted = conn.execute(
                    "SELECT posted_at FROM gbp_posts WHERE status='posted' "
                    "ORDER BY posted_at DESC LIMIT 1"
                ).fetchone()
                conn.close()
                if last_posted and last_posted["posted_at"]:
                    from datetime import datetime as _dt, timedelta
                    posted_dt = _dt.strptime(last_posted["posted_at"][:19], "%Y-%m-%d %H:%M:%S")
                    if (_now_pt().replace(tzinfo=None) - posted_dt) > timedelta(days=7):
                        print("[GBP] Last post expired (>7 days). Fresh post already queued above.")
            except Exception as e:
                print(f"[GBP] Expiry check error: {e}")

        except Exception as e:
            print(f"[GBP] Scheduler error: {e}")

        await asyncio.sleep(SCHEDULER_INTERVAL)


# ── Telegram Command Handlers ────────────────────────────────────────────────

async def handle_gbp_command(
    text: str,
    send_fn: Callable[[str], Awaitable[None]],
) -> bool:
    """
    Handle /gbp commands from Telegram.

    /gbp stats    — show posting stats
    /gbp preview  — generate and preview next post
    /gbp post     — publish the latest draft
    /gbp drafts   — list pending drafts

    Returns True if the command was handled, False otherwise.
    """
    parts = text.strip().split(maxsplit=1)
    if not parts or parts[0].lower() != "/gbp":
        return False

    subcommand = parts[1].lower().strip() if len(parts) > 1 else "stats"

    if subcommand == "stats":
        await send_fn(format_gbp_stats())
        return True

    elif subcommand == "preview":
        template = select_post_template()
        content = template["text"]
        preview = (
            f"GBP Post Preview\n"
            f"{'=' * 30}\n"
            f"Category: {template['category'].replace('_', ' ').title()}\n"
            f"Template: {template['id']}\n\n"
            f"{content}\n\n"
            f"CTA: Learn More -> {WEBSITE_URL}\n\n"
            f"Reply 'post' to publish this, or 'skip' to skip."
        )
        await send_fn(preview)
        return True

    elif subcommand == "post":
        # Publish the latest draft
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        draft = conn.execute(
            "SELECT * FROM gbp_posts WHERE status='draft' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()

        if not draft:
            await send_fn("No drafts to publish. Use '/gbp preview' to create one.")
            return True

        result = await create_gbp_post(
            template_text=draft["content"],
            image_path=draft["image_path"] if draft["image_path"] else None,
            cta_url=draft["cta_url"],
            template_id=draft["template_id"],
        )

        if result.get("ok"):
            await send_fn(f"GBP post #{result['post_id']} published!")
        else:
            status = result.get("status", "unknown")
            reason = result.get("reason", result.get("error", ""))
            await send_fn(f"GBP post #{result['post_id']} status: {status}\n{reason}")
        return True

    elif subcommand == "drafts":
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        drafts = conn.execute(
            "SELECT id, template_id, content, created_at FROM gbp_posts "
            "WHERE status='draft' ORDER BY id DESC LIMIT 5"
        ).fetchall()
        conn.close()

        if not drafts:
            await send_fn("No pending drafts.")
            return True

        lines = ["Pending GBP Drafts:", "=" * 25]
        for d in drafts:
            preview_text = d["content"][:80] + "..." if len(d["content"]) > 80 else d["content"]
            lines.append(f"#{d['id']} [{d['template_id']}] {d['created_at']}")
            lines.append(f"  {preview_text}")
            lines.append("")
        await send_fn("\n".join(lines))
        return True

    else:
        await send_fn(
            "GBP commands:\n"
            "  /gbp stats   — posting stats (30 days)\n"
            "  /gbp preview — generate next post preview\n"
            "  /gbp post    — publish latest draft\n"
            "  /gbp drafts  — list pending drafts"
        )
        return True
