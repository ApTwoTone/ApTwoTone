from __future__ import annotations
"""
Instagram Graph API integration for auto-posting.
- Posts to Instagram Business account via Facebook Graph API
- Hashtag rotation (A/B/C/D sets, never consecutive repeats)
- Caption template rotation (10 templates, avoid repeats within 10 days)
- Photo rotation (never repeat within 7 days)
- Local image upload via FB Page → public URL → IG container → publish
- Engagement tracking (likes, comments, reach, impressions)
- Background scheduler for daily posting at optimal times
- DB tracking for all posts, stats, and analytics

Requires: fb_page_access_token in ~/.nexus/config.json
FB Page ID: 946769878528865
"""
import httpx, json, sqlite3, random, asyncio, traceback
from pathlib import Path
from datetime import datetime, timedelta

DB_PATH = Path.home() / ".nexus" / "memory.db"
GRAPH_API = "https://graph.facebook.com/v25.0"
FB_PAGE_ID = "946769878528865"

# ── Hashtag sets — rotate daily, never same set two days in a row ─────────────

HASHTAG_SETS = {
    "A": (
        "#LuxuryWedding #WeddingPlanning #LAWedding #OutdoorWedding "
        "#WeddingRentals #SFVWedding #CaliforniaWedding #WeddingDay "
        "#BrideToBeLA #WeddingVenue #EventRentals #LuxuryEvents "
        "#WeddingDecor #DreamWedding #LABride"
    ),
    "B": (
        "#EventPlanning #PartyRentals #LuxuryEvents #LAEvents "
        "#EventRentals #RestroomTrailer #PortableBathroom #EventSetup "
        "#PartyPlanning #CelebrationTime #LuxuryRentals #EventDesign "
        "#PartyIdeas #LAParty #EventPro"
    ),
    "C": (
        "#QuinceaneraPlanning #Quinceanera2026 #FiestaDeQuince #MisQuince "
        "#QuinceaneraIdeas #LatinaWedding #BodaLatina #EventosLA "
        "#FiestaLA #BodaAlAireLibre #QuinceaneraDecor #PartyLatina "
        "#CelebracionLatina #FiestaPatronales #EventosMexicanos"
    ),
    "D": (
        "#SanFernandoValley #LosAngelesEvents #VenturaCounty #SantaClarita "
        "#MalibuWeddings #BurbankEvents #GlendaleEvents #PasadenaEvents "
        "#ThousandOaks #SimiValley #LALocal #SoCalEvents "
        "#CaliforniaEvents #SoCalWedding #LALife"
    ),
}

# ── 10 Instagram caption templates (rotate daily) ────────────────────────────

CAPTION_TEMPLATES = [
    # Template 1: Feature highlight
    (
        "\u2728 Your guests deserve better than a porta-potty.\n\n"
        "Our luxury 4-stall restroom trailer features:\n"
        "\U0001f6bf Running water & flushing toilets\n"
        "\u2744\ufe0f Climate control (AC + heat)\n"
        "\U0001f4a1 LED lighting & chrome fixtures\n"
        "\U0001f50a Bluetooth speakers\n"
        "\U0001fa9e Vanity mirrors & premium soap\n\n"
        "Fully self-contained \u2014 just needs flat ground. No hookups required!\n\n"
        "Serving all of SoCal \U0001f4cd\n\n"
        "\U0001f449 Link in bio for a free quote!\n\n"
        "{hashtags}"
    ),
    # Template 2: Wedding focus
    (
        "Your outdoor wedding deserves a luxury touch \u2014 even in the bathroom. \U0001f492\u2728\n\n"
        "Our restroom trailer has been the hidden MVP at weddings across LA, "
        "Ventura, and Santa Clarita.\n\n"
        "4 private stalls | AC | LED lighting | Running water | Bluetooth speakers\n\n"
        "Delivery, setup, and pickup included. Starting at $999.\n\n"
        "DM us for availability! \U0001f4e9\n\n"
        "{hashtags}"
    ),
    # Template 3: Problem/Solution
    (
        "Planning an outdoor event? \U0001f33f\n\n"
        "The #1 complaint at outdoor events: the bathrooms.\n\n"
        "Fix that with our luxury restroom trailer:\n"
        "\u2705 4 private stalls\n"
        "\u2705 Real flushing toilets\n"
        "\u2705 AC in summer, heat in winter\n"
        "\u2705 No hookups needed \u2014 fully self-contained\n"
        "\u2705 Delivery + setup + pickup included\n\n"
        "Your guests will think it's part of the venue. \U0001f60f\n\n"
        "{hashtags}"
    ),
    # Template 4: Social proof
    (
        "\"Our guests couldn't believe the bathrooms were a rental!\" \U0001f929\n\n"
        "That's the reaction we get every single time.\n\n"
        "Luxury restroom trailer rental serving all of SoCal.\n"
        "\U0001f6bf Running water | \u2744\ufe0f AC | \U0001f4a1 LED lighting | \U0001f50a Bluetooth\n\n"
        "Weddings \u2022 Quincea\u00f1eras \u2022 Corporate \u2022 Parties \u2022 Festivals\n\n"
        "Starting at $999 \u2014 delivery, setup & pickup included.\n\n"
        "{hashtags}"
    ),
    # Template 5: Quincea\u00f1era
    (
        "\u00a1Tu quincea\u00f1era merece lo mejor! \U0001f380\u2728\n\n"
        "Nuestro tr\u00e1iler de ba\u00f1o de lujo tiene:\n"
        "\u2022 4 ba\u00f1os privados\n"
        "\u2022 Agua corriente y WC\n"
        "\u2022 Aire acondicionado\n"
        "\u2022 Luces LED y espejos\n"
        "\u2022 Parlantes Bluetooth\n\n"
        "No necesita conexiones \u2014 solo terreno plano.\n\n"
        "Entrega, instalaci\u00f3n y recogida incluida. \U0001f69a\n\n"
        "DM para disponibilidad \U0001f4e9\n\n"
        "{hashtags}"
    ),
    # Template 6: Value proposition
    (
        "$999 for THIS?! \U0001f440\n\n"
        "4 luxury stalls with AC, running water, LED lighting, Bluetooth speakers, "
        "vanity mirrors, and premium soap.\n\n"
        "Delivery. Setup. Pickup. All included.\n\n"
        "No water hookup needed. No power outlet needed. Just flat ground.\n\n"
        "Serving LA, SFV, Ventura, Santa Clarita & beyond.\n\n"
        "Book your date before it's gone! \U0001f4c5\n\n"
        "{hashtags}"
    ),
    # Template 7: Behind the scenes
    (
        "Here's what goes into every delivery \U0001f69b\u2728\n\n"
        "1. Fresh deep clean\n"
        "2. Stock premium supplies\n"
        "3. Test all systems (water, AC, lights)\n"
        "4. Deliver & level on-site\n"
        "5. Final walkthrough\n"
        "6. Pick up after your event\n\n"
        "We handle everything so you can focus on your guests.\n\n"
        "Luxury restroom trailer rental | SoCal \U0001f4cd\n\n"
        "{hashtags}"
    ),
    # Template 8: Seasonal
    (
        "Wedding season is HERE! \U0001f490\U0001f338\n\n"
        "Don't let bathroom logistics stress you out. We've got you covered "
        "with our luxury 4-stall restroom trailer.\n\n"
        "\u2728 Perfect for outdoor & venue weddings\n"
        "\U0001f321\ufe0f Climate controlled for any weather\n"
        "\U0001f6bf Running water & flushing toilets\n"
        "\U0001f4cd Serving all of SoCal\n\n"
        "Dates are filling up fast \u2014 secure yours today!\n\n"
        "{hashtags}"
    ),
    # Template 9: Construction/B2B
    (
        "Upgrade your job site \U0001f3d7\ufe0f\n\n"
        "Tired of basic porta-potties for your crew? Our luxury restroom trailer "
        "is available for monthly rentals.\n\n"
        "\u2022 4 stalls with flushing toilets\n"
        "\u2022 Running water for proper handwashing\n"
        "\u2022 Climate controlled\n"
        "\u2022 Self-contained \u2014 no hookups needed\n\n"
        "Monthly rates available. DM for pricing.\n\n"
        "{hashtags}"
    ),
    # Template 10: Local spotlight
    (
        "Proudly serving {city} and all of SoCal! \U0001f4cd\n\n"
        "Whether it's a wedding in Malibu, a quincea\u00f1era in the Valley, "
        "or a corporate event in Ventura \u2014 we deliver luxury to your doorstep.\n\n"
        "4-stall luxury restroom trailer | AC | Running water | LED lighting\n\n"
        "Delivery, setup & pickup included.\n"
        "Starting at $999.\n\n"
        "{hashtags}"
    ),
]

SPOTLIGHT_CITIES = [
    "Los Angeles", "San Fernando Valley", "Ventura", "Santa Clarita",
    "Thousand Oaks", "Malibu", "Burbank", "Pasadena", "Glendale",
    "Simi Valley", "Santa Barbara", "Oxnard",
]

# Optimal posting hours (Pacific Time, 24h)
OPTIMAL_HOURS = [9, 12, 15, 18]

# Photo directory
PHOTO_DIR = Path(__file__).parent.parent / "website" / "images" / "bathroom"


# ── Database ──────────────────────────────────────────────────────────────────

def init_instagram_db():
    """Create instagram_posts table if it doesn't exist."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS instagram_posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ig_post_id TEXT DEFAULT '',
        caption TEXT NOT NULL,
        hashtag_set TEXT DEFAULT '',
        image_path TEXT DEFAULT '',
        post_type TEXT DEFAULT 'feed',
        niche_angle TEXT DEFAULT '',
        status TEXT DEFAULT 'pending',
        engagement TEXT DEFAULT '{}',
        created_at TEXT DEFAULT (datetime('now')),
        published_at TEXT,
        error TEXT DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_ig_posts_date ON instagram_posts(created_at);
    """)
    conn.commit()
    conn.close()
    print("[IG] Database table initialized")


# ── Config ────────────────────────────────────────────────────────────────────

def _load_config() -> dict:
    """Load config from ~/.nexus/config.json."""
    config_path = Path.home() / ".nexus" / "config.json"
    if config_path.exists():
        try:
            return json.loads(config_path.read_text())
        except Exception:
            pass
    return {}


def _get_token() -> str:
    """Get the FB Page access token from config."""
    return _load_config().get("fb_page_access_token", "")


def _get_page_id() -> str:
    """Get the FB Page ID from config, falling back to hardcoded default."""
    return _load_config().get("fb_page_id", FB_PAGE_ID)


# ── Instagram Account Discovery ──────────────────────────────────────────────

# Cache the IG account ID so we don't re-fetch every call
_ig_account_id_cache: str | None = None


async def get_ig_account_id() -> str | None:
    """
    Get Instagram Business Account ID linked to the FB Page.
    Caches the result after first successful fetch.
    """
    global _ig_account_id_cache
    if _ig_account_id_cache:
        return _ig_account_id_cache

    page_id = _get_page_id()
    token = _get_token()
    if not page_id or not token:
        print("[IG] Missing fb_page_id or fb_page_access_token in config")
        return None

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{GRAPH_API}/{page_id}",
                params={
                    "fields": "instagram_business_account",
                    "access_token": token,
                },
            )
            if r.status_code != 200:
                print(f"[IG] Failed to fetch IG account: HTTP {r.status_code} — {r.text[:200]}")
                return None
            data = r.json()
            ig_account = data.get("instagram_business_account", {})
            ig_id = ig_account.get("id")
            if ig_id:
                _ig_account_id_cache = ig_id
                print(f"[IG] Instagram Business Account ID: {ig_id}")
            else:
                print("[IG] No Instagram Business Account linked to this FB Page")
            return ig_id
    except Exception as e:
        print(f"[IG] Error fetching IG account ID: {e}")
        return None


# ── Hashtag Selection ─────────────────────────────────────────────────────────

def _get_last_hashtag_set() -> str:
    """Get the hashtag set used in the most recent published post."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        row = conn.execute(
            "SELECT hashtag_set FROM instagram_posts "
            "WHERE status='published' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        return row[0] if row else ""
    except Exception:
        return ""


def select_hashtag_set() -> tuple[str, str]:
    """
    Select next hashtag set (never same as yesterday).
    Returns (set_letter, hashtags_string).
    """
    last = _get_last_hashtag_set()
    available = [k for k in HASHTAG_SETS if k != last]
    chosen = random.choice(available)
    return chosen, HASHTAG_SETS[chosen]


# ── Caption Selection ─────────────────────────────────────────────────────────

def select_caption() -> str:
    """Select a caption template, avoiding repeats within the last 10 days."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        recent = conn.execute(
            "SELECT caption FROM instagram_posts "
            "WHERE published_at >= date('now', '-10 days')"
        ).fetchall()
        conn.close()
    except Exception:
        recent = []

    # Compare by first 50 chars to match templates
    recent_prefixes = {r[0][:50] for r in recent}

    # Shuffle and pick one not recently used
    templates = list(CAPTION_TEMPLATES)
    random.shuffle(templates)
    for t in templates:
        if t[:50] not in recent_prefixes:
            return t
    # All used recently — just pick random
    return random.choice(CAPTION_TEMPLATES)


# ── Photo Selection ───────────────────────────────────────────────────────────

def select_photo() -> str | None:
    """Select a photo, rotating through available ones (no repeat within 7 days)."""
    if not PHOTO_DIR.exists():
        print(f"[IG] Photo directory not found: {PHOTO_DIR}")
        return None

    photos = sorted(PHOTO_DIR.glob("*.jpg")) + sorted(PHOTO_DIR.glob("*.png"))
    if not photos:
        print("[IG] No photos found in photo directory")
        return None

    # Get recently used photos (last 7 days)
    try:
        conn = sqlite3.connect(str(DB_PATH))
        recent = conn.execute(
            "SELECT image_path FROM instagram_posts "
            "WHERE published_at >= date('now', '-7 days')"
        ).fetchall()
        conn.close()
    except Exception:
        recent = []

    recent_paths = {r[0] for r in recent}

    available = [p for p in photos if str(p) not in recent_paths]
    if not available:
        # All used recently — reset rotation
        available = photos

    chosen = random.choice(available)
    print(f"[IG] Selected photo: {chosen.name} (from {len(available)} available)")
    return str(chosen)


# ── Image Upload to Facebook ─────────────────────────────────────────────────

async def upload_local_image_to_fb(image_path: str) -> str | None:
    """
    Upload a local image to Facebook Page (unpublished) to get a public URL.
    The Instagram Graph API requires images at publicly accessible URLs.
    We upload to the FB Page as an unpublished photo, then extract the URL.

    Returns the public image URL or None on failure.
    """
    token = _get_token()
    page_id = _get_page_id()
    if not token or not page_id:
        print("[IG] Missing token or page_id for FB image upload")
        return None

    image_file = Path(image_path)
    if not image_file.exists():
        print(f"[IG] Image file not found: {image_path}")
        return None

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            # Upload as unpublished photo to the FB Page
            with open(image_path, "rb") as f:
                r = await client.post(
                    f"{GRAPH_API}/{page_id}/photos",
                    data={
                        "published": "false",
                        "access_token": token,
                    },
                    files={
                        "source": (image_file.name, f, "image/jpeg"),
                    },
                )

            if r.status_code != 200:
                print(f"[IG] FB photo upload failed: HTTP {r.status_code} — {r.text[:300]}")
                return None

            data = r.json()
            photo_id = data.get("id")
            if not photo_id:
                print(f"[IG] FB photo upload returned no ID: {data}")
                return None

            print(f"[IG] Uploaded photo to FB (unpublished), photo_id={photo_id}")

            # Now fetch the photo URL from the uploaded photo
            r2 = await client.get(
                f"{GRAPH_API}/{photo_id}",
                params={
                    "fields": "images",
                    "access_token": token,
                },
            )

            if r2.status_code != 200:
                print(f"[IG] Failed to fetch photo URL: HTTP {r2.status_code}")
                return None

            images = r2.json().get("images", [])
            if not images:
                print("[IG] No image URLs returned from FB photo")
                return None

            # Get the largest image (first one is usually the largest)
            image_url = images[0].get("source", "")
            if image_url:
                print(f"[IG] Got public image URL from FB: {image_url[:80]}...")
                return image_url

            print("[IG] No source URL in FB photo images")
            return None

    except Exception as e:
        print(f"[IG] Error uploading image to FB: {e}")
        traceback.print_exc()
        return None


# ── Instagram Posting ─────────────────────────────────────────────────────────

async def post_to_instagram(
    caption: str,
    image_path: str = None,
    image_url: str = None,
    hashtag_set: str = "",
) -> dict:
    """
    Post a photo to Instagram via Graph API.

    Flow:
    1. If image_path (local file) provided, upload to FB first to get a public URL.
    2. Create a media container on Instagram with the image URL + caption.
    3. Publish the container.

    Args:
        caption: Full caption text (with hashtags already filled in).
        image_path: Path to local image file (optional if image_url given).
        image_url: Public URL of the image (optional if image_path given).
        hashtag_set: Letter of the hashtag set used (A/B/C/D) for tracking.

    Returns:
        {"ok": True, "ig_post_id": "...", "permalink": "..."} on success
        {"ok": False, "error": "..."} on failure
    """
    token = _get_token()
    if not token:
        return {"ok": False, "error": "No fb_page_access_token configured"}

    ig_account_id = await get_ig_account_id()
    if not ig_account_id:
        return {"ok": False, "error": "No Instagram Business Account linked to FB Page"}

    # Step 1: Get a public image URL
    final_image_url = image_url
    if not final_image_url and image_path:
        final_image_url = await upload_local_image_to_fb(image_path)

    if not final_image_url:
        return {"ok": False, "error": "No image URL available (local upload failed or no image provided)"}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Step 2: Create media container
            r = await client.post(
                f"{GRAPH_API}/{ig_account_id}/media",
                data={
                    "image_url": final_image_url,
                    "caption": caption,
                    "access_token": token,
                },
            )

            if r.status_code != 200:
                error_msg = r.text[:300]
                print(f"[IG] Container creation failed: HTTP {r.status_code} — {error_msg}")
                return {"ok": False, "error": f"Container creation failed: {error_msg}"}

            container_data = r.json()
            creation_id = container_data.get("id")
            if not creation_id:
                return {"ok": False, "error": f"No creation_id returned: {container_data}"}

            print(f"[IG] Media container created: {creation_id}")

            # Step 2b: Wait for container to be ready (status check)
            # Instagram processes the image asynchronously
            for attempt in range(10):
                await asyncio.sleep(3)
                status_r = await client.get(
                    f"{GRAPH_API}/{creation_id}",
                    params={
                        "fields": "status_code",
                        "access_token": token,
                    },
                )
                if status_r.status_code == 200:
                    status_data = status_r.json()
                    status_code = status_data.get("status_code", "")
                    if status_code == "FINISHED":
                        print(f"[IG] Container ready after {(attempt + 1) * 3}s")
                        break
                    elif status_code == "ERROR":
                        return {"ok": False, "error": f"Container processing error: {status_data}"}
                    else:
                        print(f"[IG] Container status: {status_code}, waiting...")
            else:
                print("[IG] Container did not finish processing in 30s, attempting publish anyway")

            # Step 3: Publish the container
            r2 = await client.post(
                f"{GRAPH_API}/{ig_account_id}/media_publish",
                data={
                    "creation_id": creation_id,
                    "access_token": token,
                },
            )

            if r2.status_code != 200:
                error_msg = r2.text[:300]
                print(f"[IG] Publish failed: HTTP {r2.status_code} — {error_msg}")
                return {"ok": False, "error": f"Publish failed: {error_msg}"}

            publish_data = r2.json()
            ig_post_id = publish_data.get("id", "")
            print(f"[IG] Published! Post ID: {ig_post_id}")

            # Step 4: Get the permalink
            permalink = ""
            try:
                r3 = await client.get(
                    f"{GRAPH_API}/{ig_post_id}",
                    params={
                        "fields": "permalink",
                        "access_token": token,
                    },
                )
                if r3.status_code == 200:
                    permalink = r3.json().get("permalink", "")
            except Exception:
                pass

            return {
                "ok": True,
                "ig_post_id": ig_post_id,
                "permalink": permalink,
            }

    except httpx.TimeoutException:
        return {"ok": False, "error": "Request timed out"}
    except Exception as e:
        print(f"[IG] Post error: {e}")
        traceback.print_exc()
        return {"ok": False, "error": str(e)}


# ── Full Auto-Post Flow ──────────────────────────────────────────────────────

async def create_daily_ig_post() -> dict:
    """
    Full auto-post flow:
    1. Select photo (rotating, no repeat within 7 days)
    2. Select caption template (no repeat within 10 days)
    3. Select hashtag set (never repeat consecutive days)
    4. Fill in caption with hashtags and city (if template uses {city})
    5. Post to Instagram
    6. Record in DB

    Returns:
        {"ok": True, "ig_post_id": "...", "caption_preview": "...", ...} on success
        {"ok": False, "error": "..."} on failure
    """
    # Check if already posted today
    try:
        conn = sqlite3.connect(str(DB_PATH))
        today_post = conn.execute(
            "SELECT id FROM instagram_posts "
            "WHERE status='published' AND date(published_at) = date('now')"
        ).fetchone()
        conn.close()
        if today_post:
            return {"ok": False, "error": "Already posted today", "already_posted": True}
    except Exception:
        pass

    # 1. Select photo
    photo_path = select_photo()
    if not photo_path:
        return {"ok": False, "error": "No photos available"}

    # 2. Select caption template
    template = select_caption()

    # 3. Select hashtag set
    set_letter, hashtags = select_hashtag_set()

    # 4. Fill in placeholders
    city = random.choice(SPOTLIGHT_CITIES)
    caption = template.replace("{hashtags}", hashtags)
    if "{city}" in caption:
        caption = caption.replace("{city}", city)

    # Determine niche angle from caption content
    niche = "general"
    caption_lower = caption.lower()
    if "wedding" in caption_lower or "bride" in caption_lower:
        niche = "wedding"
    elif "quincea" in caption_lower or "quince" in caption_lower:
        niche = "quinceanera"
    elif "job site" in caption_lower or "construction" in caption_lower:
        niche = "construction"
    elif "proudly serving" in caption_lower:
        niche = "local_spotlight"

    # 5. Record pending post in DB
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.execute(
            "INSERT INTO instagram_posts "
            "(caption, hashtag_set, image_path, post_type, niche_angle, status) "
            "VALUES (?, ?, ?, 'feed', ?, 'pending')",
            (caption, set_letter, photo_path, niche),
        )
        post_db_id = cursor.lastrowid
        conn.commit()
        conn.close()
    except Exception as e:
        return {"ok": False, "error": f"DB error: {e}"}

    # 6. Post to Instagram
    result = await post_to_instagram(
        caption=caption,
        image_path=photo_path,
        hashtag_set=set_letter,
    )

    # 7. Update DB with result
    try:
        conn = sqlite3.connect(str(DB_PATH))
        if result["ok"]:
            conn.execute(
                "UPDATE instagram_posts SET "
                "ig_post_id=?, status='published', published_at=datetime('now') "
                "WHERE id=?",
                (result.get("ig_post_id", ""), post_db_id),
            )
        else:
            conn.execute(
                "UPDATE instagram_posts SET "
                "status='failed', error=? "
                "WHERE id=?",
                (result.get("error", "unknown"), post_db_id),
            )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[IG] DB update error: {e}")

    # Enrich result
    result["hashtag_set"] = set_letter
    result["niche_angle"] = niche
    result["photo"] = Path(photo_path).name if photo_path else ""
    result["caption_preview"] = caption[:120] + "..." if len(caption) > 120 else caption
    return result


# ── Engagement Insights ───────────────────────────────────────────────────────

async def get_ig_insights(ig_post_id: str) -> dict:
    """
    Get engagement metrics for an Instagram post.
    Returns: {"likes": N, "comments": N, "reach": N, "impressions": N, ...}
    """
    token = _get_token()
    if not token or not ig_post_id:
        return {}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Get basic engagement (likes, comments)
            r = await client.get(
                f"{GRAPH_API}/{ig_post_id}",
                params={
                    "fields": "like_count,comments_count,timestamp,permalink",
                    "access_token": token,
                },
            )

            engagement = {}
            if r.status_code == 200:
                data = r.json()
                engagement["likes"] = data.get("like_count", 0)
                engagement["comments"] = data.get("comments_count", 0)
                engagement["permalink"] = data.get("permalink", "")
                engagement["timestamp"] = data.get("timestamp", "")

            # Get insights (reach, impressions)
            r2 = await client.get(
                f"{GRAPH_API}/{ig_post_id}/insights",
                params={
                    "metric": "impressions,reach,saved",
                    "access_token": token,
                },
            )

            if r2.status_code == 200:
                insights_data = r2.json().get("data", [])
                for metric in insights_data:
                    name = metric.get("name", "")
                    values = metric.get("values", [{}])
                    if values:
                        engagement[name] = values[0].get("value", 0)

            return engagement

    except Exception as e:
        print(f"[IG] Insights error for {ig_post_id}: {e}")
        return {}


async def update_post_engagement(ig_post_id: str) -> dict:
    """Fetch and store engagement data for a post."""
    engagement = await get_ig_insights(ig_post_id)
    if not engagement:
        return engagement

    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "UPDATE instagram_posts SET engagement=? WHERE ig_post_id=?",
            (json.dumps(engagement), ig_post_id),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[IG] Engagement DB update error: {e}")

    return engagement


# ── Stats & Analytics ─────────────────────────────────────────────────────────

def get_posting_stats(days: int = 7) -> dict:
    """
    Get Instagram posting stats for the last N days.

    Returns: {
        "total_posts": N,
        "published": N,
        "failed": N,
        "total_likes": N,
        "total_comments": N,
        "avg_likes": float,
        "avg_comments": float,
        "hashtag_distribution": {"A": N, "B": N, ...},
        "niche_distribution": {"wedding": N, ...},
        "posts": [...]
    }
    """
    try:
        conn = sqlite3.connect(str(DB_PATH))
        rows = conn.execute(
            "SELECT id, ig_post_id, caption, hashtag_set, image_path, "
            "post_type, niche_angle, status, engagement, created_at, "
            "published_at, error "
            "FROM instagram_posts "
            "WHERE created_at >= date('now', ?)",
            (f"-{days} days",),
        ).fetchall()
        conn.close()
    except Exception:
        return {"total_posts": 0, "published": 0, "failed": 0, "posts": []}

    posts = []
    total_likes = 0
    total_comments = 0
    published_count = 0
    failed_count = 0
    hashtag_dist = {}
    niche_dist = {}

    for row in rows:
        eng = {}
        try:
            eng = json.loads(row[8]) if row[8] else {}
        except Exception:
            pass

        post = {
            "id": row[0],
            "ig_post_id": row[1],
            "caption_preview": (row[2] or "")[:80],
            "hashtag_set": row[3],
            "photo": Path(row[4]).name if row[4] else "",
            "post_type": row[5],
            "niche_angle": row[6],
            "status": row[7],
            "engagement": eng,
            "created_at": row[9],
            "published_at": row[10],
            "error": row[11],
        }
        posts.append(post)

        if row[7] == "published":
            published_count += 1
            total_likes += eng.get("likes", 0)
            total_comments += eng.get("comments", 0)
        elif row[7] == "failed":
            failed_count += 1

        hs = row[3]
        if hs:
            hashtag_dist[hs] = hashtag_dist.get(hs, 0) + 1

        na = row[6]
        if na:
            niche_dist[na] = niche_dist.get(na, 0) + 1

    return {
        "total_posts": len(posts),
        "published": published_count,
        "failed": failed_count,
        "total_likes": total_likes,
        "total_comments": total_comments,
        "avg_likes": round(total_likes / published_count, 1) if published_count else 0,
        "avg_comments": round(total_comments / published_count, 1) if published_count else 0,
        "hashtag_distribution": hashtag_dist,
        "niche_distribution": niche_dist,
        "posts": posts,
    }


# ── Post Preview for Morning Briefing ─────────────────────────────────────────

def generate_ig_post_preview() -> dict:
    """
    Generate a preview of today's IG post for the morning briefing.
    Does NOT actually post — just shows what would be posted.

    Returns: {
        "caption": "...",
        "hashtag_set": "A",
        "hashtag_set_name": "Wedding",
        "photo_path": "...",
        "photo_name": "...",
        "niche_angle": "...",
        "preview_text": "..."  (short summary for Telegram)
    }
    """
    set_names = {"A": "Wedding", "B": "Events/Party", "C": "Quinceanera/Latina", "D": "Local/SoCal"}

    photo_path = select_photo()
    template = select_caption()
    set_letter, hashtags = select_hashtag_set()
    city = random.choice(SPOTLIGHT_CITIES)

    caption = template.replace("{hashtags}", hashtags)
    if "{city}" in caption:
        caption = caption.replace("{city}", city)

    # Determine niche
    niche = "general"
    cl = caption.lower()
    if "wedding" in cl or "bride" in cl:
        niche = "wedding"
    elif "quincea" in cl or "quince" in cl:
        niche = "quinceanera"
    elif "job site" in cl or "construction" in cl:
        niche = "construction"
    elif "proudly serving" in cl:
        niche = "local_spotlight"

    # Build preview text for Telegram
    photo_name = Path(photo_path).name if photo_path else "none"
    first_line = caption.split("\n")[0][:60]
    preview_text = (
        f"IG Post Preview:\n"
        f"Photo: {photo_name}\n"
        f"Hashtags: Set {set_letter} ({set_names.get(set_letter, '')})\n"
        f"Niche: {niche}\n"
        f"Caption: {first_line}..."
    )

    return {
        "caption": caption,
        "hashtag_set": set_letter,
        "hashtag_set_name": set_names.get(set_letter, ""),
        "photo_path": photo_path or "",
        "photo_name": photo_name,
        "niche_angle": niche,
        "preview_text": preview_text,
    }


# ── Background Scheduler ─────────────────────────────────────────────────────

async def run_instagram_scheduler(send_fn):
    """
    Background loop for Instagram auto-posting.

    - Daily post at a rotating optimal time (9 AM, 12 PM, 3 PM, or 6 PM PT)
    - Check engagement on yesterday's post (24h later)
    - send_fn: async function to send Telegram messages (send_fn(text))

    Runs every 5 minutes, checks if it's time to post.
    """
    print("[IG] Scheduler started")
    init_instagram_db()

    # Track today's post status across iterations
    last_post_date = ""
    last_engagement_check_date = ""

    # Pick today's posting hour at startup
    today_post_hour = random.choice(OPTIMAL_HOURS)

    while True:
        try:
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")
            current_hour = now.hour

            # Reset posting hour at midnight
            if today != last_post_date and today != last_engagement_check_date:
                today_post_hour = random.choice(OPTIMAL_HOURS)

            # ── Daily Post ────────────────────────────────────────────
            if today != last_post_date and current_hour >= today_post_hour:
                print(f"[IG] Time to post! (target hour: {today_post_hour}, current: {current_hour})")

                result = await create_daily_ig_post()

                if result.get("ok"):
                    last_post_date = today
                    msg = (
                        f"Instagram post published!\n\n"
                        f"Photo: {result.get('photo', '')}\n"
                        f"Hashtags: Set {result.get('hashtag_set', '')}\n"
                        f"Niche: {result.get('niche_angle', '')}\n"
                        f"Post ID: {result.get('ig_post_id', '')}\n"
                    )
                    permalink = result.get("permalink", "")
                    if permalink:
                        msg += f"Link: {permalink}\n"
                    msg += f"\nCaption preview: {result.get('caption_preview', '')}"

                    if send_fn:
                        try:
                            await send_fn(msg)
                        except Exception as e:
                            print(f"[IG] Telegram notify error: {e}")
                    print(f"[IG] Daily post successful: {result.get('ig_post_id', '')}")

                elif result.get("already_posted"):
                    last_post_date = today
                    print("[IG] Already posted today, skipping")

                else:
                    error = result.get("error", "unknown")
                    print(f"[IG] Daily post failed: {error}")
                    if send_fn:
                        try:
                            await send_fn(f"Instagram post FAILED: {error}")
                        except Exception:
                            pass
                    # Don't set last_post_date — will retry next cycle

            # ── Engagement Check (yesterday's post, 24h later) ────────
            if today != last_engagement_check_date and current_hour >= 10:
                try:
                    conn = sqlite3.connect(str(DB_PATH))
                    yesterday_post = conn.execute(
                        "SELECT ig_post_id FROM instagram_posts "
                        "WHERE status='published' "
                        "AND date(published_at) = date('now', '-1 day') "
                        "AND ig_post_id != '' "
                        "LIMIT 1"
                    ).fetchone()
                    conn.close()

                    if yesterday_post and yesterday_post[0]:
                        ig_post_id = yesterday_post[0]
                        engagement = await update_post_engagement(ig_post_id)
                        last_engagement_check_date = today

                        if engagement and send_fn:
                            likes = engagement.get("likes", 0)
                            comments = engagement.get("comments", 0)
                            reach = engagement.get("reach", "N/A")
                            impressions = engagement.get("impressions", "N/A")
                            msg = (
                                f"Yesterday's IG post engagement (24h):\n"
                                f"Likes: {likes}\n"
                                f"Comments: {comments}\n"
                                f"Reach: {reach}\n"
                                f"Impressions: {impressions}"
                            )
                            try:
                                await send_fn(msg)
                            except Exception:
                                pass
                    else:
                        last_engagement_check_date = today
                except Exception as e:
                    print(f"[IG] Engagement check error: {e}")

        except Exception as e:
            print(f"[IG] Scheduler error: {e}")
            traceback.print_exc()

        # Sleep 5 minutes between checks
        await asyncio.sleep(300)


# ── Module Init ───────────────────────────────────────────────────────────────

_initialized = False


async def init_instagram():
    """
    Initialize the Instagram integration.
    - Creates DB table
    - Verifies IG account is linked
    Returns (success: bool, error: str)
    """
    global _initialized
    init_instagram_db()

    ig_id = await get_ig_account_id()
    if not ig_id:
        token = _get_token()
        if not token:
            return False, "No fb_page_access_token in config"
        return False, "No Instagram Business Account linked to FB Page (check FB Page settings)"

    _initialized = True
    return True, ""


def is_initialized() -> bool:
    return _initialized
