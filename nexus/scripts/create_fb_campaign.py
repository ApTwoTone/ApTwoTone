#!/usr/bin/env python3
"""
Create a Facebook Lead Form Video Ad Campaign via the Marketing API.

Usage:
    python scripts/create_fb_campaign.py --token YOUR_ACCESS_TOKEN

    # Or set as env var:
    export FB_ACCESS_TOKEN=YOUR_TOKEN
    python scripts/create_fb_campaign.py

The campaign is created in PAUSED state (draft).
Upload your video in Ads Manager, then activate.

Prerequisites:
    1. Facebook Developer App with ads_management + leads_retrieval permissions
    2. User Access Token (get from developers.facebook.com/tools/explorer)
    3. pip install requests (already installed)
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests

# ── Configuration ────────────────────────────────────────────────────────────

API_VERSION = "v21.0"
BASE_URL = f"https://graph.facebook.com/{API_VERSION}"

# From Ads Manager URL: act=1713830169593455
AD_ACCOUNT_ID = "act_1713830169593455"

# Pixel ID from config
PIXEL_ID = "1579580876639654"

# Website
WEBSITE_URL = "https://zoarbathroomrental.com"
PRIVACY_URL = "https://zoarbathroomrental.com"

# Webhook for lead form data
WEBHOOK_URL = "https://crm.zoarbathroomrental.com/api/crm/webhook/facebook"


def api_call(method, endpoint, params=None, **kwargs):
    """Make a Facebook Graph API call with error handling."""
    url = f"{BASE_URL}/{endpoint}" if not endpoint.startswith("http") else endpoint
    resp = getattr(requests, method)(url, params=params, **kwargs)
    data = resp.json()
    if "error" in data:
        err = data["error"]
        print(f"\n  API Error: {err.get('message', 'Unknown error')}")
        print(f"  Code: {err.get('code')}, Type: {err.get('type')}")
        if err.get("error_subcode"):
            print(f"  Subcode: {err['error_subcode']}")
        sys.exit(1)
    return data


def get_page_id(token):
    """Find the Zoar Bathroom Rental page ID."""
    print("  Finding Zoar page...")
    data = api_call("get", "me/accounts", params={
        "access_token": token,
        "fields": "id,name,access_token",
    })
    pages = data.get("data", [])
    for page in pages:
        name = page.get("name", "").lower()
        if "zoar" in name or "bathroom" in name:
            print(f"  Found: {page['name']} (ID: {page['id']})")
            return page["id"], page.get("access_token", token)

    # If no Zoar page found, list all pages
    if pages:
        print("\n  Available pages:")
        for p in pages:
            print(f"    - {p['name']} (ID: {p['id']})")
        print("\n  No 'Zoar' page found. Set PAGE_ID manually in the script.")
    else:
        print("  No pages found. Make sure token has pages_manage_ads permission.")
    sys.exit(1)


def verify_token(token):
    """Check token permissions and validity."""
    print("\n1. Verifying access token...")
    data = api_call("get", "me", params={
        "access_token": token,
        "fields": "id,name",
    })
    print(f"  Authenticated as: {data.get('name')} (ID: {data.get('id')})")

    # Check permissions
    perms_data = api_call("get", "me/permissions", params={"access_token": token})
    granted = [p["permission"] for p in perms_data.get("data", []) if p.get("status") == "granted"]

    required = ["ads_management", "pages_manage_ads"]
    recommended = ["leads_retrieval", "pages_read_engagement"]

    missing_required = [p for p in required if p not in granted]
    missing_recommended = [p for p in recommended if p not in granted]

    if missing_required:
        print(f"\n  MISSING REQUIRED permissions: {', '.join(missing_required)}")
        print("  Go to developers.facebook.com/tools/explorer and add these permissions.")
        sys.exit(1)

    if missing_recommended:
        print(f"  Note: Missing recommended permissions: {', '.join(missing_recommended)}")
        print("  (Campaign creation will still work, but leads_retrieval is needed for webhook)")

    print(f"  Permissions OK: {', '.join(granted)}")
    return data


def create_campaign(token):
    """Create the lead generation campaign (PAUSED)."""
    print("\n2. Creating campaign...")
    data = api_call("post", f"{AD_ACCOUNT_ID}/campaigns", params={
        "access_token": token,
        "name": "Video Lead Form - SoCal Weddings",
        "objective": "OUTCOME_LEADS",
        "status": "PAUSED",
        "special_ad_categories": "[]",
    })
    campaign_id = data["id"]
    print(f"  Campaign created: {campaign_id}")
    return campaign_id


def create_ad_set(token, campaign_id):
    """Create the ad set with broader SoCal targeting."""
    print("\n3. Creating ad set...")

    targeting = {
        "age_min": 24,
        "age_max": 50,
        "geo_locations": {
            "cities": [
                {"key": "2420379", "radius": 40, "distance_unit": "mile"},  # Los Angeles
            ],
            "regions": [
                {"key": "3847"},   # California
            ],
            "location_types": ["home", "recent"],
        },
        "flexible_spec": [
            {
                "interests": [
                    {"id": "6003020834894", "name": "Wedding planning"},
                    {"id": "6003384248674", "name": "Outdoor wedding"},
                    {"id": "6003327847662", "name": "Event planning"},
                    {"id": "6003012166411", "name": "Bridal shower"},
                    {"id": "6003590648498", "name": "Party planning"},
                    {"id": "6003107902433", "name": "Wedding venues"},
                ],
            },
        ],
        "publisher_platforms": ["facebook", "instagram"],
        "facebook_positions": ["feed", "reels"],
        "instagram_positions": ["stream", "story", "reels"],
    }

    data = api_call("post", f"{AD_ACCOUNT_ID}/adsets", params={
        "access_token": token,
        "name": "Broader SoCal - Wedding & Event Interest",
        "campaign_id": campaign_id,
        "daily_budget": "1500",  # $15.00 in cents
        "billing_event": "IMPRESSIONS",
        "optimization_goal": "LEAD_GENERATION",
        "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
        "targeting": json.dumps(targeting),
        "status": "PAUSED",
        "promoted_object": json.dumps({"pixel_id": PIXEL_ID}),
    })
    ad_set_id = data["id"]
    print(f"  Ad set created: {ad_set_id}")
    return ad_set_id


def create_lead_form(token, page_id, page_token):
    """Create the instant lead form on the page."""
    print("\n4. Creating lead form...")

    form_config = {
        "name": "Zoar Quote Request - Video",
        "follow_up_action_url": WEBSITE_URL,
        "questions": [
            {"type": "FULL_NAME"},
            {"type": "PHONE"},
            {"type": "EMAIL"},
            {
                "type": "CUSTOM",
                "key": "event_type",
                "label": "What type of event is this for?",
                "options": [
                    {"key": "wedding", "value": "Wedding"},
                    {"key": "corporate", "value": "Corporate Event"},
                    {"key": "birthday_party", "value": "Birthday / Party"},
                    {"key": "festival", "value": "Festival / Concert"},
                    {"key": "construction", "value": "Construction Site"},
                    {"key": "other", "value": "Other"},
                ],
            },
        ],
        "context_card": {
            "title": "Get a Free Quote in 2 Minutes",
            "content": [
                "Luxury 4-stall restroom trailer with AC, running water, hardwood floors & full-length mirrors.",
                "Serving all of SoCal \u2014 LA, San Fernando Valley, Santa Clarita, Ventura, Oxnard & Santa Monica.",
                "Spring & summer dates booking fast!",
            ],
            "style": "PARAGRAPH_STYLE",
        },
        "thank_you_page": {
            "title": "Thanks! We'll Reach Out Within an Hour",
            "body": "A member of our team will contact you shortly to discuss your event and provide a custom quote.",
            "button_text": "Visit Our Website",
            "button_type": "VIEW_WEBSITE",
            "website_url": WEBSITE_URL,
        },
        "privacy_policy": {"url": PRIVACY_URL, "link_text": "Privacy Policy"},
        "locale": "EN_US",
    }

    # Use page access token for lead form creation
    use_token = page_token or token

    data = api_call("post", f"{page_id}/leadgen_forms", params={
        "access_token": use_token,
        "name": form_config["name"],
        "questions": json.dumps(form_config["questions"]),
        "context_card": json.dumps(form_config["context_card"]),
        "thank_you_page": json.dumps(form_config["thank_you_page"]),
        "privacy_policy": json.dumps(form_config["privacy_policy"]),
        "follow_up_action_url": form_config["follow_up_action_url"],
        "locale": form_config["locale"],
    })
    form_id = data["id"]
    print(f"  Lead form created: {form_id}")
    return form_id


def create_ad_creative(token, page_id, form_id):
    """Create ad creative (without video — placeholder)."""
    print("\n5. Creating ad creative (no video yet — placeholder)...")

    creative_data = {
        "access_token": token,
        "name": "Video - Luxury Restroom Trailer",
        "object_story_spec": json.dumps({
            "page_id": page_id,
            "link_data": {
                "call_to_action": {
                    "type": "SIGN_UP",
                    "value": {"lead_gen_form_id": form_id},
                },
                "description": "Serving SoCal \u2014 Free Quotes",
                "link": WEBSITE_URL,
                "message": (
                    "Stop worrying about your guests' comfort. Our luxury 4-stall "
                    "restroom trailer has AC, running water, hardwood floors & "
                    "full-length mirrors. Your guests will think it's part of the venue.\n\n"
                    "Serving all of SoCal \u2014 LA, Valley, Ventura, Santa Clarita & beyond.\n\n"
                    "Get your free quote in 2 minutes \U0001f447"
                ),
                "name": "Luxury Restroom Trailer Rental",
            },
        }),
    }

    data = api_call("post", f"{AD_ACCOUNT_ID}/adcreatives", params=creative_data)
    creative_id = data["id"]
    print(f"  Ad creative created: {creative_id}")
    print("  NOTE: No video attached yet. Upload video in Ads Manager.")
    return creative_id


def create_ad(token, ad_set_id, creative_id):
    """Create the ad (PAUSED — waiting for video upload)."""
    print("\n6. Creating ad (PAUSED)...")
    data = api_call("post", f"{AD_ACCOUNT_ID}/ads", params={
        "access_token": token,
        "name": "Video Ad - Luxury Trailer (DRAFT - needs video)",
        "adset_id": ad_set_id,
        "creative": json.dumps({"creative_id": creative_id}),
        "status": "PAUSED",
        "tracking_specs": json.dumps([
            {"action.type": ["offsite_conversion"], "fb_pixel": [PIXEL_ID]}
        ]),
    })
    ad_id = data["id"]
    print(f"  Ad created: {ad_id}")
    return ad_id


def subscribe_webhook(page_id, page_token):
    """Subscribe the page to leadgen webhook events."""
    print("\n7. Subscribing page to leadgen webhooks...")
    try:
        data = api_call("post", f"{page_id}/subscribed_apps", params={
            "access_token": page_token,
            "subscribed_fields": "leadgen",
        })
        if data.get("success"):
            print("  Webhook subscription successful!")
            print(f"  Leads will POST to: {WEBHOOK_URL}")
        else:
            print("  Webhook subscription response:", data)
    except SystemExit:
        print("  Could not auto-subscribe. Do it manually:")
        print(f"  1. Go to developers.facebook.com → Your App → Webhooks")
        print(f"  2. Subscribe to Page → leadgen")
        print(f"  3. Callback URL: {WEBHOOK_URL}")
        print(f"  4. Verify Token: nexus-fb-verify")


def save_results(results):
    """Save all created IDs to a JSON file."""
    out_path = Path(__file__).parent / "fb_campaign_video.json"
    results["created_at"] = datetime.utcnow().isoformat()
    results["webhook_url"] = WEBHOOK_URL
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\n  Results saved to: {out_path}")


def main():
    parser = argparse.ArgumentParser(description="Create Facebook Video Lead Form Campaign")
    parser.add_argument("--token", help="Facebook User Access Token")
    parser.add_argument("--page-id", help="Override Facebook Page ID (auto-detected if omitted)")
    parser.add_argument("--dry-run", action="store_true", help="Show config without creating anything")
    args = parser.parse_args()

    token = args.token or os.environ.get("FB_ACCESS_TOKEN")
    if not token:
        print("=" * 60)
        print("  Facebook Video Lead Form Campaign Creator")
        print("=" * 60)
        print("\nNo access token provided. Here's how to get one:\n")
        print("  1. Go to: https://developers.facebook.com/tools/explorer/")
        print("  2. Select your Zoar Bathroom Rental app (or create one)")
        print("  3. Click 'Generate Access Token'")
        print("  4. Grant these permissions:")
        print("     - ads_management")
        print("     - pages_manage_ads")
        print("     - leads_retrieval")
        print("     - pages_read_engagement")
        print("  5. Copy the token and run:")
        print(f"\n     python {__file__} --token YOUR_TOKEN_HERE\n")
        print("  Or set as env var:")
        print("     export FB_ACCESS_TOKEN=YOUR_TOKEN_HERE")
        print(f"     python {__file__}")
        print()

        # Check if they have a developer app
        print("Don't have a Facebook Developer App yet?")
        print("  1. Go to: https://developers.facebook.com/apps/")
        print("  2. Click 'Create App' → 'Business' type")
        print("  3. Name it 'Zoar CRM' or similar")
        print("  4. Add 'Marketing API' product")
        print("  5. Then go to Graph API Explorer and generate a token")
        sys.exit(0)

    if args.dry_run:
        print("\n[DRY RUN] Would create:")
        print(f"  Campaign: 'Video Lead Form - SoCal Weddings' (PAUSED)")
        print(f"  Ad Set: 'Broader SoCal - Wedding & Event Interest' ($15/day)")
        print(f"  Lead Form: Name, Phone, Email, Event Type dropdown")
        print(f"  Ad: Video placeholder (PAUSED)")
        print(f"  Webhook: {WEBHOOK_URL}")
        return

    print("=" * 60)
    print("  Creating Facebook Video Lead Form Campaign")
    print("=" * 60)

    # Step 1: Verify token
    verify_token(token)

    # Step 2: Find page
    page_id = args.page_id
    page_token = token
    if not page_id:
        page_id, page_token = get_page_id(token)

    # Step 3: Create campaign
    campaign_id = create_campaign(token)

    # Step 4: Create ad set
    ad_set_id = create_ad_set(token, campaign_id)

    # Step 5: Create lead form
    form_id = create_lead_form(token, page_id, page_token)

    # Step 6: Create ad creative (no video yet)
    creative_id = create_ad_creative(token, page_id, form_id)

    # Step 7: Create ad
    ad_id = create_ad(token, ad_set_id, creative_id)

    # Step 8: Subscribe webhook
    subscribe_webhook(page_id, page_token)

    # Save results
    results = {
        "campaign_id": campaign_id,
        "ad_set_id": ad_set_id,
        "form_id": form_id,
        "creative_id": creative_id,
        "ad_id": ad_id,
        "page_id": page_id,
        "ad_account_id": AD_ACCOUNT_ID,
    }
    save_results(results)

    print("\n" + "=" * 60)
    print("  DONE! Campaign created in PAUSED state.")
    print("=" * 60)
    print(f"""
Next steps:
  1. Go to Ads Manager: https://adsmanager.facebook.com
  2. Find campaign: "Video Lead Form - SoCal Weddings"
  3. Click into the ad → Edit → Upload your video
  4. Review everything looks good
  5. Toggle campaign from PAUSED → ACTIVE

Lead form submissions will automatically flow to:
  {WEBHOOK_URL} → Nexus CRM

Campaign IDs saved to: scripts/fb_campaign_video.json
""")


if __name__ == "__main__":
    main()
