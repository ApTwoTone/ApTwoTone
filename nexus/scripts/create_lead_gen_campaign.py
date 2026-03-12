#!/usr/bin/env python3
"""
Zoar Lead Gen Campaign — $10/day Instant Forms

Creates a Facebook Lead Generation campaign with in-platform Instant Forms,
3 visually distinct ad creatives, and proper SFV targeting.

Everything is created PAUSED. Kai reviews in Ads Manager, then activates.

Usage:
    python scripts/create_lead_gen_campaign.py              # Create for real
    python scripts/create_lead_gen_campaign.py --dry-run    # Preview without API calls
"""
import argparse
import base64
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests

# ── Configuration ────────────────────────────────────────────────────────────

API_VERSION = "v25.0"
BASE_URL = f"https://graph.facebook.com/{API_VERSION}"

AD_ACCOUNT_ID = "act_1713830169593455"
PAGE_ID = "946769878528865"
PIXEL_ID = "1579580876639654"

WEBSITE_URL = "https://zoarbathroomrental.com"
PRIVACY_URL = "https://zoarbathroomrental.com/privacy"
WEBHOOK_URL = "https://crm.zoarbathroomrental.com/api/crm/webhook/facebook"

DAILY_BUDGET_CENTS = 1000  # $10.00/day

# Image paths — 3 visually distinct Higgsfield images
IMAGE_DIR = Path.home() / "Documents" / "ad_creatives"
IMAGES = {
    "wedding": "hf_20260222_035739_bd8624a2-488c-44b1-beab-2ecb5772a96e.jpeg",
    "branded": "hf_20260222_002105_07c09475-7f2e-44ca-850c-bd3f5e8be0c6.jpeg",
    "comfort": "hf_20260222_043919_fe3bd329-c57a-42fa-b380-82f046712775.png",
}

# Output
CONFIG_DIR = Path.home() / ".nexus" / "fb_ads"
OUTPUT_FILE = CONFIG_DIR / "campaign_config.json"

# Telegram
TELEGRAM_CHAT_ID = "8540603351"


# ── API Helper ───────────────────────────────────────────────────────────────

def api_call(method, endpoint, token, params=None, files=None):
    """Make a Facebook Graph API call with error handling."""
    url = f"{BASE_URL}/{endpoint}" if not endpoint.startswith("http") else endpoint
    if params is None:
        params = {}
    params["access_token"] = token

    if files:
        resp = getattr(requests, method)(url, data=params, files=files)
    else:
        resp = getattr(requests, method)(url, params=params)

    data = resp.json()
    if "error" in data:
        err = data["error"]
        print(f"\n  API Error: {err.get('message', 'Unknown error')}")
        print(f"  Code: {err.get('code')}, Type: {err.get('type')}")
        if err.get("error_subcode"):
            print(f"  Subcode: {err['error_subcode']}")
        return {"error": err}
    return data


# ── Step Functions ───────────────────────────────────────────────────────────

def load_token():
    """Load FB access token from ~/.nexus/config.json."""
    config_path = Path.home() / ".nexus" / "config.json"
    if not config_path.exists():
        print("ERROR: ~/.nexus/config.json not found")
        sys.exit(1)

    config = json.loads(config_path.read_text())
    token = config.get("fb_page_access_token")
    if not token:
        print("ERROR: fb_page_access_token not found in config")
        sys.exit(1)

    print(f"  Token loaded ({len(token)} chars)")
    return token


def verify_permissions(token):
    """Verify token has required permissions."""
    print("\n[1/10] Verifying access token...")

    data = api_call("get", "me", token, params={"fields": "id,name"})
    if "error" in data:
        print("  Token verification failed!")
        sys.exit(1)
    print(f"  Authenticated as: {data.get('name')} (ID: {data.get('id')})")

    perms_data = api_call("get", "me/permissions", token)
    if "error" in perms_data:
        print("  Could not check permissions")
        sys.exit(1)

    granted = [p["permission"] for p in perms_data.get("data", []) if p.get("status") == "granted"]
    required = ["ads_management", "pages_manage_ads"]
    missing = [p for p in required if p not in granted]

    if missing:
        print(f"  MISSING REQUIRED: {', '.join(missing)}")
        sys.exit(1)

    print(f"  Permissions OK: {', '.join(granted)}")

    if "leads_retrieval" not in granted:
        print("  WARNING: leads_retrieval not granted — lead form creation may fail")

    return granted


def pause_existing_campaigns(token):
    """Pause all existing campaigns in the ad account."""
    print("\n[2/10] Pausing existing campaigns...")

    data = api_call("get", f"{AD_ACCOUNT_ID}/campaigns", token, params={
        "fields": "id,name,status,effective_status",
        "limit": 100,
    })
    if "error" in data:
        print("  Could not fetch campaigns — continuing anyway")
        return []

    campaigns = data.get("data", [])
    paused = []

    for camp in campaigns:
        status = camp.get("effective_status", "")
        if status in ("ACTIVE", "PAUSED"):
            result = api_call("post", camp["id"], token, params={"status": "PAUSED"})
            if "error" not in result:
                paused.append(camp["id"])
                print(f"  PAUSED: {camp['name']} ({camp['id']})")
            else:
                print(f"  Failed to pause: {camp['name']}")

    if not paused:
        print("  No active campaigns to pause")

    return paused


def create_campaign(token):
    """Create Lead Gen campaign at $10/day."""
    print("\n[3/10] Creating Lead Gen campaign...")

    data = api_call("post", f"{AD_ACCOUNT_ID}/campaigns", token, params={
        "name": "Zoar Lead Gen — SFV Weddings Mar 2026",
        "objective": "OUTCOME_LEADS",
        "status": "PAUSED",
        "special_ad_categories": "[]",
    })
    if "error" in data:
        print("  Campaign creation failed!")
        sys.exit(1)

    campaign_id = data["id"]
    print(f"  Campaign created: {campaign_id}")
    return campaign_id


def create_lead_form(token):
    """Create Instant Form with 4 fields (name, phone, email, event type)."""
    print("\n[4/10] Creating Instant Lead Form...")

    questions = [
        {"type": "FULL_NAME"},
        {"type": "PHONE"},
        {"type": "EMAIL"},
        {
            "type": "CUSTOM",
            "key": "event_type",
            "label": "What type of event?",
            "options": [
                {"key": "wedding", "value": "Wedding"},
                {"key": "quinceanera", "value": "Quincea\u00f1era"},
                {"key": "corporate", "value": "Corporate Event"},
                {"key": "backyard", "value": "Backyard Party"},
                {"key": "festival", "value": "Festival"},
                {"key": "other", "value": "Other Outdoor Event"},
            ],
        },
    ]

    context_card = {
        "title": "Get a Free Quote in Minutes",
        "content": [
            "Luxury 4-stall restroom trailer with AC, flushing toilets, running water, and premium finishes.",
            "Delivery, setup, and pickup included in every rental.",
            "Serving the San Fernando Valley and Greater LA.",
        ],
        "style": "PARAGRAPH_STYLE",
    }

    thank_you_page = {
        "title": "Thanks! We'll reach out shortly.",
        "body": "We'll call or text you within 2 hours with a personalized quote. Can't wait? Call us now at (424) 235-8979.",
        "button_text": "Call Now",
        "button_type": "CALL_BUSINESS",
    }

    privacy_policy = {"url": PRIVACY_URL, "link_text": "Privacy Policy"}

    data = api_call("post", f"{PAGE_ID}/leadgen_forms", token, params={
        "name": "Zoar — Free Quote (Instant Form)",
        "questions": json.dumps(questions),
        "context_card": json.dumps(context_card),
        "thank_you_page": json.dumps(thank_you_page),
        "privacy_policy": json.dumps(privacy_policy),
        "follow_up_action_url": WEBSITE_URL,
        "locale": "EN_US",
    })

    if "error" in data:
        print("  Lead form creation failed!")
        print("  This may require leads_retrieval permission.")
        sys.exit(1)

    form_id = data["id"]
    print(f"  Lead form created: {form_id}")
    return form_id


def create_ad_set(token, campaign_id):
    """Create ad set with SFV targeting, women 24-45, event interests."""
    print("\n[5/10] Creating ad set...")

    targeting = {
        "age_min": 24,
        "age_max": 45,
        "genders": [2],  # Women (primary event planners)
        "geo_locations": {
            "custom_locations": [{
                "latitude": 34.1867,
                "longitude": -118.4490,
                "radius": 25,
                "distance_unit": "mile",
            }],
            "location_types": ["home", "recent"],
        },
        "locales": [6, 24],  # English, Spanish
        "flexible_spec": [{
            "interests": [
                {"id": "6003020834894", "name": "Wedding planning"},
                {"id": "6003384248674", "name": "Outdoor wedding"},
                {"id": "6003327847662", "name": "Event planning"},
                {"id": "6003590648498", "name": "Party planning"},
                {"id": "6003012166411", "name": "Bridal shower"},
                {"id": "6003107902433", "name": "Wedding venues"},
            ],
        }],
        "publisher_platforms": ["facebook", "instagram"],
        "facebook_positions": ["feed", "story"],
        "instagram_positions": ["stream", "story"],
    }

    data = api_call("post", f"{AD_ACCOUNT_ID}/adsets", token, params={
        "name": "SFV — Events + Engaged Women 24-45",
        "campaign_id": campaign_id,
        "daily_budget": str(DAILY_BUDGET_CENTS),
        "billing_event": "IMPRESSIONS",
        "optimization_goal": "LEAD_GENERATION",
        "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
        "targeting": json.dumps(targeting),
        "status": "PAUSED",
        "promoted_object": json.dumps({"page_id": PAGE_ID}),
    })

    if "error" in data:
        print("  Ad set creation failed!")
        sys.exit(1)

    ad_set_id = data["id"]
    print(f"  Ad set created: {ad_set_id}")
    return ad_set_id


def upload_images(token):
    """Upload 3 ad images from ~/Documents/ad_creatives/."""
    print("\n[6/10] Uploading ad images...")

    image_hashes = {}
    for key, filename in IMAGES.items():
        filepath = IMAGE_DIR / filename
        if not filepath.exists():
            print(f"  WARNING: {filename} not found, skipping")
            continue

        with open(filepath, "rb") as f:
            data = api_call("post", f"{AD_ACCOUNT_ID}/adimages", token,
                            params={},
                            files={"filename": (filename, f)})

        if "error" in data:
            print(f"  Failed to upload {key}: {filename}")
            continue

        images = data.get("images", {})
        for img_name, img_data in images.items():
            image_hashes[key] = img_data.get("hash")
            print(f"  Uploaded {key}: {img_data.get('hash', 'no hash')}")
            break

    if len(image_hashes) < 3:
        print(f"  WARNING: Only {len(image_hashes)}/3 images uploaded")

    return image_hashes


def create_ad_creatives(token, form_id, image_hashes):
    """Create 3 fundamentally different ad creatives."""
    print("\n[7/10] Creating 3 ad creatives...")

    ads_config = [
        {
            "name": "Ad A — Wedding Contrast",
            "image_key": "wedding",
            "message": (
                "You planned every detail. The dress. The flowers. The playlist. "
                "Don't let porta-potties ruin the magic.\n\n"
                "Zoar Bathroom Rentals delivers a luxury restroom trailer with "
                "flushing toilets, running water, climate control, and premium finishes.\n\n"
                "Delivery and setup included. Starting at $999.\n\n"
                "Get your free quote below."
            ),
            "headline": "Luxury Restrooms for Your Wedding",
            "description": "Delivery & Setup Included",
        },
        {
            "name": "Ad B — Social Proof",
            "image_key": "branded",
            "message": (
                "Your guests will think the restroom trailer is part of the venue.\n\n"
                "Flushing toilets. Running water. Climate control. Multiple stalls. "
                "Premium interior finishes.\n\n"
                "Serving the San Fernando Valley and Greater LA.\n\n"
                "Pricing varies by location. Get your personalized quote."
            ),
            "headline": "Skip the Porta-Potties",
            "description": "Starting at $999 — Free Quote",
        },
        {
            "name": "Ad C — Urgency / Seasonal",
            "image_key": "comfort",
            "message": (
                "Spring and summer dates are filling up fast.\n\n"
                "Zoar Bathroom Rentals provides a luxury restroom trailer for "
                "weddings, quincea\u00f1eras, and outdoor events across SoCal.\n\n"
                "Delivery, setup, and pickup included in every rental.\n\n"
                "Your date might still be available. Check now."
            ),
            "headline": "Spring Dates Booking Fast",
            "description": "Luxury Restroom Trailer Rental",
        },
    ]

    creative_ids = {}
    for ad in ads_config:
        img_hash = image_hashes.get(ad["image_key"])
        if not img_hash:
            print(f"  Skipping {ad['name']} — no image hash for {ad['image_key']}")
            continue

        object_story_spec = {
            "page_id": PAGE_ID,
            "link_data": {
                "call_to_action": {
                    "type": "SIGN_UP",
                    "value": {"lead_gen_form_id": form_id},
                },
                "image_hash": img_hash,
                "link": WEBSITE_URL,
                "message": ad["message"],
                "name": ad["headline"],
                "description": ad["description"],
            },
        }

        data = api_call("post", f"{AD_ACCOUNT_ID}/adcreatives", token, params={
            "name": ad["name"],
            "object_story_spec": json.dumps(object_story_spec),
        })

        if "error" in data:
            print(f"  Failed: {ad['name']}")
            continue

        creative_ids[ad["name"]] = data["id"]
        print(f"  Created: {ad['name']} ({data['id']})")

    return creative_ids


def create_ads(token, ad_set_id, creative_ids):
    """Create one ad per creative, all PAUSED."""
    print("\n[8/10] Creating ads...")

    ad_ids = {}
    for name, creative_id in creative_ids.items():
        data = api_call("post", f"{AD_ACCOUNT_ID}/ads", token, params={
            "name": name,
            "adset_id": ad_set_id,
            "creative": json.dumps({"creative_id": creative_id}),
            "status": "PAUSED",
            "tracking_specs": json.dumps([
                {"action.type": ["offsite_conversion"], "fb_pixel": [PIXEL_ID]}
            ]),
        })

        if "error" in data:
            print(f"  Failed: {name}")
            continue

        ad_ids[name] = data["id"]
        print(f"  Created: {name} ({data['id']})")

    return ad_ids


def subscribe_webhook(token):
    """Subscribe page to leadgen webhook events."""
    print("\n[9/10] Subscribing to leadgen webhooks...")
    data = api_call("post", f"{PAGE_ID}/subscribed_apps", token, params={
        "subscribed_fields": "leadgen",
    })
    if "error" not in data and data.get("success"):
        print(f"  Webhook subscribed! Leads POST to: {WEBHOOK_URL}")
    else:
        print("  Webhook subscription failed — set up manually in Meta Developer Console")
        print(f"  Callback URL: {WEBHOOK_URL}")
        print(f"  Verify Token: nexus-fb-verify")


def save_results(results):
    """Save all campaign IDs to ~/.nexus/fb_ads/campaign_config.json."""
    print("\n[10/10] Saving campaign config...")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    results["created_at"] = datetime.utcnow().isoformat()
    results["daily_budget_cents"] = DAILY_BUDGET_CENTS
    results["page_id"] = PAGE_ID
    results["pixel_id"] = PIXEL_ID
    results["webhook_url"] = WEBHOOK_URL
    OUTPUT_FILE.write_text(json.dumps(results, indent=2))
    print(f"  Saved to: {OUTPUT_FILE}")


def send_telegram(message):
    """Send notification to Kai via Telegram."""
    config_path = Path.home() / ".nexus" / "config.json"
    try:
        config = json.loads(config_path.read_text())
        bot_token = config.get("telegram_bot_token")
        if not bot_token:
            return
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message},
            timeout=10,
        )
    except Exception:
        pass  # Non-critical


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Create Zoar Lead Gen Campaign")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview all actions without making API calls")
    args = parser.parse_args()

    print("=" * 60)
    print("  Zoar Lead Gen Campaign — $10/day Instant Forms")
    print("=" * 60)

    if args.dry_run:
        print("\n[DRY RUN] Would execute:\n")
        print("  1. Load token from ~/.nexus/config.json")
        print("  2. Verify permissions (ads_management, pages_manage_ads)")
        print("  3. Pause ALL existing campaigns in ad account")
        print("  4. Create campaign: 'Zoar Lead Gen - SFV Weddings Mar 2026'")
        print(f"     Objective: OUTCOME_LEADS, Budget: ${DAILY_BUDGET_CENTS/100:.0f}/day, Status: PAUSED")
        print("  5. Create Instant Form: name, phone, email, event type dropdown")
        print("     Event types: Wedding, Quincea\u00f1era, Corporate, Backyard, Festival, Other")
        print("     Thank-you: 'We'll reach out shortly' + call (424) 235-8979")
        print("  6. Create ad set: SFV 25mi radius, women 24-45, wedding/event interests")
        print("     Placements: FB Feed + IG Feed + Stories")
        print("  7. Upload 3 images from ~/Documents/ad_creatives/:")
        for key, filename in IMAGES.items():
            path = IMAGE_DIR / filename
            exists = "EXISTS" if path.exists() else "MISSING"
            print(f"     - {key}: {filename} [{exists}]")
        print("  8. Create 3 ad creatives:")
        print("     - Ad A 'Wedding Contrast' — emotional, porta-potty comparison")
        print("     - Ad B 'Social Proof' — feature-focused, guests will love it")
        print("     - Ad C 'Urgency/Seasonal' — spring dates filling up")
        print("  9. Create 3 ads (one per creative, all PAUSED)")
        print(" 10. Subscribe page to leadgen webhooks")
        print(f" 11. Save IDs to {OUTPUT_FILE}")
        print(" 12. Send Telegram notification to Kai")
        print(f"\n  Ad copy uses approved language only:")
        print(f"    'Starting at $999', 'Delivery and setup included'")
        print(f"    'Pricing varies by location'")
        print(f"    NO 'all-inclusive', NO 'no hidden fees', NO film/production")
        return

    # Load token
    print("\n  Loading token from ~/.nexus/config.json...")
    token = load_token()

    # Step 1: Verify
    verify_permissions(token)

    # Step 2: Pause old campaigns
    paused_campaigns = pause_existing_campaigns(token)

    # Step 3: Create campaign
    campaign_id = create_campaign(token)

    # Step 4: Create lead form
    form_id = create_lead_form(token)

    # Step 5: Create ad set
    ad_set_id = create_ad_set(token, campaign_id)

    # Step 6: Upload images
    image_hashes = upload_images(token)

    # Step 7: Create creatives
    creative_ids = create_ad_creatives(token, form_id, image_hashes)

    # Step 8: Create ads
    ad_ids = create_ads(token, ad_set_id, creative_ids)

    # Step 9: Subscribe webhook
    subscribe_webhook(token)

    # Step 10: Save results
    results = {
        "campaign_id": campaign_id,
        "ad_set_id": ad_set_id,
        "form_id": form_id,
        "creative_ids": creative_ids,
        "ad_ids": ad_ids,
        "paused_campaigns": paused_campaigns,
    }
    save_results(results)

    # Telegram notification
    n_creatives = len(creative_ids)
    n_ads = len(ad_ids)
    send_telegram(
        f"FB ADS: New Lead Gen campaign created (PAUSED)\n\n"
        f"Campaign: Zoar Lead Gen - SFV Weddings\n"
        f"Budget: $10/day\n"
        f"Objective: Lead Generation (Instant Forms)\n"
        f"Ads: {n_ads} creatives uploaded\n"
        f"Old campaigns paused: {len(paused_campaigns)}\n\n"
        f"Go to Ads Manager to review and activate."
    )

    # Summary
    print("\n" + "=" * 60)
    print("  DONE! Campaign created in PAUSED state.")
    print("=" * 60)
    print(f"""
Next steps:
  1. Go to Ads Manager: https://adsmanager.facebook.com
  2. Find campaign: "Zoar Lead Gen - SFV Weddings Mar 2026"
  3. Review all 3 ads — check images look correct
  4. Toggle campaign from PAUSED -> ACTIVE
  5. DO NOT TOUCH for 7 days (let Meta's algorithm calibrate)

Campaign: {campaign_id}
Ad Set:   {ad_set_id}
Form:     {form_id}
Ads:      {n_ads} created
Images:   {len(image_hashes)} uploaded

Lead form submissions flow to:
  {WEBHOOK_URL} -> Nexus CRM -> Telegram alert -> 60-second quote

Config saved: {OUTPUT_FILE}
""")


if __name__ == "__main__":
    main()
