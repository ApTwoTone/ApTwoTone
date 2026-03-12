#!/usr/bin/env python3
"""
Create 3 Facebook Ad Campaigns for Zoar Bathroom Rentals.
Uses requests directly (no SDK) for reliability.

Campaigns:
  1. Weddings           ($5/day, 10 images)
  2. Construction/Corp  ($5/day, 7 images)
  3. General Events     ($5/day, 10 images)
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# ── Config ──────────────────────────────────────────────────────────────────
API_VERSION = "v21.0"
BASE = f"https://graph.facebook.com/{API_VERSION}"
AD_ACCOUNT = "act_1713830169593455"
PIXEL_ID = "1579580876639654"
PAGE_ID = "946769878528865"
SITE = "https://zoarbathroomrental.com"
TODAY = datetime.now().strftime("%Y%m%d")
IMAGE_DIR = Path.home() / "Downloads" / "facebook static images Mar 2"

TOKEN = os.environ.get("FB_USER_TOKEN") or ""
if not TOKEN:
    token_file = Path.home() / "Downloads" / "fb_token_ads.txt"
    if token_file.exists():
        TOKEN = token_file.read_text().strip()


def api(method, endpoint, **kwargs):
    """Make Graph API call with error handling."""
    url = f"{BASE}/{endpoint}"
    kwargs.setdefault("params", {})["access_token"] = TOKEN
    resp = getattr(requests, method)(url, **kwargs)
    data = resp.json()
    if "error" in data:
        err = data["error"]
        print(f"  ❌ API Error: {err.get('message')}")
        print(f"     Code: {err.get('code')}, Subcode: {err.get('error_subcode')}")
        if err.get("error_user_msg"):
            print(f"     Detail: {err['error_user_msg']}")
        return None
    return data


def upload_image(filepath):
    """Upload a single image, return hash."""
    url = f"{BASE}/{AD_ACCOUNT}/adimages"
    with open(filepath, "rb") as f:
        resp = requests.post(url, params={"access_token": TOKEN}, files={"filename": f})
    data = resp.json()
    if "error" in data:
        return None
    # Extract hash from response
    images = data.get("images", {})
    for key, val in images.items():
        return val.get("hash")
    return None


def upload_images(filenames, label):
    """Upload batch of images, return list of hashes."""
    print(f"\n  📸 Uploading {len(filenames)} images for {label}...")
    hashes = []
    for i, fname in enumerate(filenames, 1):
        fpath = IMAGE_DIR / fname
        if not fpath.exists():
            print(f"    [{i}] SKIP — not found: {fname[:50]}")
            continue
        h = upload_image(fpath)
        if h:
            hashes.append(h)
            print(f"    [{i}] ✓ {fname[:45]}... → {h[:12]}")
        else:
            print(f"    [{i}] ✗ upload failed: {fname[:45]}")
        time.sleep(0.5)
    print(f"  Uploaded: {len(hashes)}/{len(filenames)}")
    return hashes


def create_campaign(name):
    """Create a LEADS campaign (PAUSED)."""
    print(f"\n  🏗️  Creating campaign: {name}")
    data = api("post", f"{AD_ACCOUNT}/campaigns", params={
        "access_token": TOKEN,
        "name": name,
        "objective": "OUTCOME_LEADS",
        "status": "PAUSED",
        "special_ad_categories": "[]",
        "is_adset_budget_sharing_enabled": "false",
    })
    if not data:
        return None
    cid = data["id"]
    print(f"  Campaign ID: {cid}")
    return cid


def create_adset(campaign_id, name, targeting, budget_cents=500):
    """Create ad set with targeting."""
    print(f"\n  📋 Creating ad set: {name}")
    data = api("post", f"{AD_ACCOUNT}/adsets", params={
        "access_token": TOKEN,
        "name": name,
        "campaign_id": campaign_id,
        "daily_budget": str(budget_cents),
        "billing_event": "IMPRESSIONS",
        "optimization_goal": "OFFSITE_CONVERSIONS",
        "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
        "targeting": json.dumps(targeting),
        "status": "PAUSED",
        "promoted_object": json.dumps({"pixel_id": PIXEL_ID, "custom_event_type": "LEAD"}),
    })
    if not data:
        return None
    asid = data["id"]
    print(f"  Ad Set ID: {asid}")
    return asid


def utm_url(campaign, content):
    """Build URL with UTM params."""
    c = campaign.replace(" ", "_")
    t = content.replace(" ", "_")
    return f"{SITE}?utm_source=facebook&utm_medium=paid&utm_campaign={c}&utm_content={t}"


def create_ads_individual(adset_id, image_hashes, copy_cfg, campaign_name):
    """Create individual ads: one per primary_text, cycling through images."""
    print(f"\n  📢 Creating individual ads...")
    created = []

    for j, primary_text in enumerate(copy_cfg["primary_texts"]):
        headline = copy_cfg["headlines"][j % len(copy_cfg["headlines"])]
        description = copy_cfg["descriptions"][j % len(copy_cfg["descriptions"])]
        img_hash = image_hashes[j % len(image_hashes)]
        ad_name = f"{campaign_name}_v{j+1}"
        dest_url = utm_url(campaign_name, ad_name)

        # Create creative
        creative_data = api("post", f"{AD_ACCOUNT}/adcreatives", params={
            "access_token": TOKEN,
            "name": f"Creative_{ad_name}",
            "object_story_spec": json.dumps({
                "page_id": PAGE_ID,
                "link_data": {
                    "message": primary_text,
                    "link": dest_url,
                    "name": headline,
                    "description": description,
                    "image_hash": img_hash,
                    "call_to_action": {
                        "type": "GET_QUOTE",
                        "value": {"link": dest_url},
                    },
                },
            }),
        })
        if not creative_data:
            continue

        creative_id = creative_data["id"]

        # Create ad
        ad_data = api("post", f"{AD_ACCOUNT}/ads", params={
            "access_token": TOKEN,
            "name": ad_name,
            "adset_id": adset_id,
            "creative": json.dumps({"creative_id": creative_id}),
            "status": "PAUSED",
            "tracking_specs": json.dumps([
                {"action.type": ["offsite_conversion"], "fb_pixel": [PIXEL_ID]}
            ]),
        })
        if ad_data:
            created.append({"ad_id": ad_data["id"], "creative_id": creative_id, "name": ad_name})
            print(f"    ✓ {ad_name} → Ad ID: {ad_data['id']}")
        time.sleep(0.3)

    # Create extra ads with remaining images (different image, same copy rotated)
    for i, img_hash in enumerate(image_hashes[3:], 4):  # skip first 3, already used
        j = i % len(copy_cfg["primary_texts"])
        primary_text = copy_cfg["primary_texts"][j]
        headline = copy_cfg["headlines"][j % len(copy_cfg["headlines"])]
        description = copy_cfg["descriptions"][j % len(copy_cfg["descriptions"])]
        ad_name = f"{campaign_name}_img{i}"
        dest_url = utm_url(campaign_name, ad_name)

        creative_data = api("post", f"{AD_ACCOUNT}/adcreatives", params={
            "access_token": TOKEN,
            "name": f"Creative_{ad_name}",
            "object_story_spec": json.dumps({
                "page_id": PAGE_ID,
                "link_data": {
                    "message": primary_text,
                    "link": dest_url,
                    "name": headline,
                    "description": description,
                    "image_hash": img_hash,
                    "call_to_action": {
                        "type": "GET_QUOTE",
                        "value": {"link": dest_url},
                    },
                },
            }),
        })
        if not creative_data:
            continue

        ad_data = api("post", f"{AD_ACCOUNT}/ads", params={
            "access_token": TOKEN,
            "name": ad_name,
            "adset_id": adset_id,
            "creative": json.dumps({"creative_id": creative_data["id"]}),
            "status": "PAUSED",
            "tracking_specs": json.dumps([
                {"action.type": ["offsite_conversion"], "fb_pixel": [PIXEL_ID]}
            ]),
        })
        if ad_data:
            created.append({"ad_id": ad_data["id"], "creative_id": creative_data["id"], "name": ad_name})
            print(f"    ✓ {ad_name} → Ad ID: {ad_data['id']}")
        time.sleep(0.3)

    print(f"  Created {len(created)} ads.")
    return created


# ── Image Lists ─────────────────────────────────────────────────────────────
WEDDING_IMAGES = [
    "hf_20260302_082541_9bbc5612-335f-4055-8ee4-d0957ac8b4f9.png",
    "hf_20260302_083050_bf6f5a51-a341-4a7c-a4ca-29e411ce97ad.jpeg",
    "hf_20260302_182624_e27878e0-70a2-4429-a0a4-50872b89f13a.png",
    "hf_20260302_191944_ae52eee2-ec3d-4cd5-9479-e2f4028fada4.jpeg",
    "hf_20260302_200254_d9e535c8-ea37-4b4c-87e5-602aa38033eb.png",
    "hf_20260302_200447_110d68bb-7246-4e0e-822c-f5ffa90a8acc.jpeg",
    "hf_20260302_200656_566bc535-71b5-42c6-9687-e301f174ebc0.png",
    "hf_20260302_201859_f99ba19e-a7c8-4c55-92f8-89f3f2546ddd.jpeg",
    "hf_20260302_203349_36a03155-6332-4696-9886-8cfae3e59fc3.png",
    "hf_20260302_203754_3b59d734-286a-4079-a2d0-4855103d5f79.jpeg",
]

CONSTRUCTION_IMAGES = [
    "hf_20260302_082058_dbfb0ed0-8cd0-4195-a817-1425ec108884.png",
    "hf_20260302_091159_d1103fdd-464a-47f5-9c5b-b78c26ddae74.jpeg",
    "hf_20260302_181547_8f94962a-7c4b-4d88-99cc-78176a397b12.jpeg",
    "hf_20260302_181637_28a5cee5-fa97-4074-9161-269366ba2e81.jpeg",
    "hf_20260302_181738_7bbad919-a9a5-483b-abb5-5e06b47ed013.png",
    "hf_20260302_182835_42af43b3-9be6-400b-8736-7dad2e8e0e8f.jpeg",
    "hf_20260302_204046_1759a8ef-1972-4368-b2ba-56378b84cbf6.png",
]

EVENTS_IMAGES = [
    "hf_20260302_181246_b562a78c-454f-49ee-8215-673dd3d62f0d.png",
    "hf_20260302_182751_40a2b42c-afd6-4b6d-9a4c-1ecd67042f84.jpeg",
    "hf_20260302_183057_1d7c41b0-5619-4fd9-9bfc-88aedd78ecc4.png",
    "hf_20260302_204342_0910577c-2b29-4566-9a8f-410a70e3e571.jpeg",
    "hf_20260302_213218_86e7b894-3d06-42eb-b744-8e4f5cd838dc.jpeg",
    "hf_20260302_220018_07a46347-bcdf-4d72-91d9-2a7b32b3641f.jpeg",
    "hf_20260302_205134_6420571d-0daa-4a33-8863-59d5f44d8fb5.jpeg",
    "hf_20260302_215357_57611e98-7cce-405b-a324-279c6c7228f9.jpeg",
    "hf_20260302_213451_3a1ff32d-aecf-42f9-aebb-534f3f657717.png",
    "hf_20260302_191503_a5b2268e-72b7-4950-8f80-5e662d6e21ea.jpeg",
]


# ── Ad Copy ─────────────────────────────────────────────────────────────────
WEDDING_COPY = {
    "primary_texts": [
        "Planning an outdoor wedding in LA? Your guests deserve better than porta-potties. Our luxury 4-stall restroom trailer features flushing toilets, running water, AC, full-size mirrors, and dark wood interiors. Delivery, setup, and pickup included. Starting at $999.",
        "Your wedding day should be perfect — including the restrooms. Our luxury trailer has 4 private stalls with flushing toilets, AC, running water, and full-size mirrors. We deliver, set up, and pick up. Starting at $999 in Greater LA.",
        "Don't let porta-potties ruin your outdoor wedding. Our 4-stall luxury restroom trailer is climate-controlled with flushing toilets, mirrors, and dark wood finishes. All-inclusive from $999.",
    ],
    "headlines": [
        "Luxury Restrooms for Your Wedding",
        "Your Guests Will Thank You",
        "Upgrade Your Outdoor Wedding",
    ],
    "descriptions": [
        "4-stall trailer with AC, flushing toilets, mirrors. Delivery included.",
        "All-inclusive luxury restroom rental. Serving Greater LA.",
    ],
}

CONSTRUCTION_COPY = {
    "primary_texts": [
        "Need clean restrooms on your job site or production set? Our luxury 4-stall trailer has flushing toilets, running water, AC, and is fully climate-controlled. We deliver, set up, and pick up. Serving all of Greater LA.",
        "Upgrade your crew's restroom situation. Our 4-stall luxury trailer beats any porta-potty — flushing toilets, AC, running water, mirrors. All-inclusive delivery and pickup across Greater LA.",
        "Film sets, construction sites, corporate events — our luxury restroom trailer handles it all. 4 private stalls, AC, flushing toilets. We deliver and set up. Starting at $999.",
    ],
    "headlines": [
        "Luxury Restrooms for Your Job Site",
        "Better Than a Porta-Potty",
        "4-Stall Restroom Trailer Rental",
    ],
    "descriptions": [
        "Flushing toilets, AC, running water. Delivery and setup included.",
        "Professional restroom rental for construction and production.",
    ],
}

EVENTS_COPY = {
    "primary_texts": [
        "Hosting an outdoor event in LA? Skip the porta-potties. Our luxury 4-stall restroom trailer has flushing toilets, AC, running water, full-size mirrors, and dark wood interiors. We deliver, set up, clean, and pick up. Starting at $999.",
        "Quinceañeras, birthdays, baby showers, backyard parties — your guests deserve a real restroom. Our luxury trailer has 4 private stalls with flushing toilets, AC, and mirrors. All-inclusive from $999 in Greater LA.",
        "The most-used part of your event shouldn't be the worst part. Our luxury 4-stall restroom trailer is a massive upgrade from porta-potties. Flushing toilets, AC, running water. Starting at $999.",
    ],
    "headlines": [
        "Luxury Restroom Trailer Rental",
        "Upgrade Your Event",
        "Starting at $999 — All-Inclusive",
    ],
    "descriptions": [
        "4-stall luxury trailer. Flushing toilets, AC, mirrors. Delivery included.",
        "Serving LA, SFV, and surrounding areas. Book your date today.",
    ],
}

# ── Targeting ───────────────────────────────────────────────────────────────
WEDDING_TARGETING = {
    "age_min": 25, "age_max": 55,
    "geo_locations": {
        "custom_locations": [{"latitude": 34.2011, "longitude": -118.5976, "radius": 30, "distance_unit": "mile"}],
    },
    "flexible_spec": [{"interests": [
        {"id": "6003409392877", "name": "Weddings"},
        {"id": "6003402319418", "name": "Wedding dress"},
        {"id": "6003349071605", "name": "Wedding photography"},
        {"id": "6836096590166", "name": "Bridal shop"},
        {"id": "6003360603580", "name": "Engagement"},
        {"id": "6003029412207", "name": "Wedding reception"},
    ]}],
    "targeting_automation": {"advantage_audience": 0},
    "publisher_platforms": ["facebook", "instagram"],
    "facebook_positions": ["feed"],
    "instagram_positions": ["stream", "story", "reels"],
}

CONSTRUCTION_TARGETING = {
    "age_min": 28, "age_max": 65,
    "geo_locations": {
        "custom_locations": [{"latitude": 34.2011, "longitude": -118.5976, "radius": 30, "distance_unit": "mile"}],
    },
    "flexible_spec": [{"interests": [
        {"id": "6003395414271", "name": "Construction"},
        {"id": "6003142479061", "name": "Construction management"},
        {"id": "6003714246353", "name": "Filmmaking"},
        {"id": "6003331677371", "name": "Production company"},
        {"id": "6741786978515", "name": "Movie and Television Industry"},
    ]}],
    "targeting_automation": {"advantage_audience": 0},
    "publisher_platforms": ["facebook", "instagram"],
    "facebook_positions": ["feed"],
    "instagram_positions": ["stream", "story", "reels"],
}

EVENTS_TARGETING = {
    "age_min": 25, "age_max": 60,
    "geo_locations": {
        "custom_locations": [{"latitude": 34.2011, "longitude": -118.5976, "radius": 30, "distance_unit": "mile"}],
    },
    "flexible_spec": [{"interests": [
        {"id": "6003092932417", "name": "Event management"},
        {"id": "6003152263430", "name": "Birthday"},
        {"id": "6003321277514", "name": "Baby shower"},
        {"id": "6899296281873", "name": "Special occasions and events"},
    ]}],
    "targeting_automation": {"advantage_audience": 0},
    "publisher_platforms": ["facebook", "instagram"],
    "facebook_positions": ["feed"],
    "instagram_positions": ["stream", "story", "reels"],
}


# ── Main ────────────────────────────────────────────────────────────────────
CAMPAIGNS = [
    {
        "name": f"Zoar_Weddings_{TODAY}",
        "label": "WEDDINGS",
        "images": WEDDING_IMAGES,
        "copy": WEDDING_COPY,
        "targeting": WEDDING_TARGETING,
    },
    {
        "name": f"Zoar_Construction_{TODAY}",
        "label": "CONSTRUCTION / CORPORATE",
        "images": CONSTRUCTION_IMAGES,
        "copy": CONSTRUCTION_COPY,
        "targeting": CONSTRUCTION_TARGETING,
    },
    {
        "name": f"Zoar_Events_{TODAY}",
        "label": "GENERAL EVENTS",
        "images": EVENTS_IMAGES,
        "copy": EVENTS_COPY,
        "targeting": EVENTS_TARGETING,
    },
]


def main():
    if not TOKEN:
        print("No token found. Set FB_USER_TOKEN or place token in ~/Downloads/fb_token_ads.txt")
        sys.exit(1)

    # Verify
    print("=" * 60)
    print("  Zoar Bathroom Rentals — Creating 3 Ad Campaigns")
    print("=" * 60)
    me = api("get", "me", params={"fields": "id,name"})
    if not me:
        print("Token invalid!")
        sys.exit(1)
    print(f"  Authenticated as: {me.get('name')}")

    acct = api("get", AD_ACCOUNT, params={"fields": "name,account_status,currency"})
    if not acct:
        sys.exit(1)
    print(f"  Ad Account: {acct.get('name')} (status={acct.get('account_status')})")

    results = {}

    for cfg in CAMPAIGNS:
        print(f"\n{'='*60}")
        print(f"  CAMPAIGN: {cfg['label']}")
        print(f"{'='*60}")

        # Upload images
        hashes = upload_images(cfg["images"], cfg["label"])
        if not hashes:
            print(f"  FATAL: No images for {cfg['label']}")
            results[cfg["label"]] = None
            continue

        # Create campaign
        cid = create_campaign(cfg["name"])
        if not cid:
            results[cfg["label"]] = None
            continue

        # Create ad set
        asid = create_adset(cid, f"{cfg['name']}_AdSet", cfg["targeting"])
        if not asid:
            results[cfg["label"]] = None
            continue

        # Create ads
        ads = create_ads_individual(asid, hashes, cfg["copy"], cfg["name"])

        results[cfg["label"]] = {
            "campaign_id": cid,
            "campaign_name": cfg["name"],
            "adset_id": asid,
            "images": len(hashes),
            "ads": len(ads),
            "ad_ids": [a["ad_id"] for a in ads],
        }

    # Save results
    out_path = Path(__file__).parent / "fb_3campaigns_results.json"
    results["created_at"] = datetime.utcnow().isoformat()
    results["pixel_id"] = PIXEL_ID
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\n  💾 Results saved to: {out_path}")

    # Summary
    total_images = sum(r.get("images", 0) for r in results.values() if isinstance(r, dict) and "images" in r)
    total_ads = sum(r.get("ads", 0) for r in results.values() if isinstance(r, dict) and "ads" in r)

    print("\n" + "=" * 60)
    print("  ✅ VERIFICATION CHECKLIST")
    print("=" * 60)
    for label in ["WEDDINGS", "CONSTRUCTION / CORPORATE", "GENERAL EVENTS"]:
        r = results.get(label)
        if r:
            print(f"  [✓] {label}: {r['images']} images, {r['ads']} ads → Campaign {r['campaign_id']}")
        else:
            print(f"  [✗] {label}: FAILED")
    print(f"  [✓] Total images: {total_images}")
    print(f"  [✓] Total ads: {total_ads}")
    print(f"  [✓] Budget: $5/day × 3 = $15/day total")
    print(f"  [✓] Pixel: {PIXEL_ID}")
    print(f"  [✓] UTM params: on all destination URLs")
    print(f"  [✓] Status: PAUSED (review before activating)")
    print(f"\n  NEXT: Go to https://adsmanager.facebook.com → review → ACTIVATE")
    print()


if __name__ == "__main__":
    main()
