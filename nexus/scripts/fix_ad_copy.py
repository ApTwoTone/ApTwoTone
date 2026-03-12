#!/usr/bin/env python3
"""
Fix Facebook Ad Campaigns — Option B Implementation
=====================================================
Consolidates each campaign to 1 ad with all images + 5/5 copy variations.
Uses asset_feed_spec (dynamic creative) to let Meta's ML optimize combos.
"""

import json
import requests
import sys
import time

# === CONFIG ===
API_VERSION = "v21.0"
BASE = f"https://graph.facebook.com/{API_VERSION}"
AD_ACCOUNT = "act_1713830169593455"
PAGE_ID = "946769878528865"
PIXEL_ID = "1579580876639654"
SITE_URL = "https://zoarbathroomrental.com"

with open("/Users/kai/Downloads/fb_token_ads.txt") as f:
    TOKEN = f.read().strip().split("\n")[0]

# === API HELPERS ===
def api_get(endpoint, params=None):
    p = {"access_token": TOKEN}
    if params:
        p.update(params)
    r = requests.get(f"{BASE}/{endpoint}", params=p)
    return r.json()

def api_post(endpoint, data=None):
    d = {"access_token": TOKEN}
    if data:
        d.update(data)
    r = requests.post(f"{BASE}/{endpoint}", data=d)
    return r.json()

def api_delete(endpoint):
    r = requests.delete(f"{BASE}/{endpoint}", params={"access_token": TOKEN})
    return r.json()

# === CAMPAIGN DATA ===
CAMPAIGNS = {
    "WEDDINGS": {
        "campaign_id": "120240274369540446",
        "adset_id": "120240274369680446",
        "ad_ids": [
            "120240274370500446", "120240274370970446", "120240274371960446",
            "120240274377640446", "120240274378280446", "120240274379060446",
            "120240274380680446", "120240274381340446", "120240274382020446",
            "120240274382740446"
        ],
    },
    "CONSTRUCTION": {
        "campaign_id": "120240274384370446",
        "adset_id": "120240274384740446",
        "ad_ids": [
            "120240274385270446", "120240274385980446", "120240274386640446",
            "120240274387430446", "120240274388610446", "120240274389740446",
            "120240274390440446"
        ],
    },
    "EVENTS": {
        "campaign_id": "120240274393510446",
        "adset_id": "120240274393600446",
        "ad_ids": [
            "120240274394430446", "120240274395100446", "120240274396290446",
            "120240274396970446", "120240274397700446", "120240274398110446",
            "120240274398580446", "120240274399230446", "120240274400230446",
            "120240274401010446"
        ],
    },
}

# === AD COPY FROM THE DOC (EXACT) ===
COPY = {
    "WEDDINGS": {
        "primary_texts": [
            "Planning an outdoor wedding? Your guests deserve better than porta-potties. Our luxury 4-stall restroom trailer has flushing toilets, running water, AC, full-size mirrors, and dark wood interiors. Delivery and setup included. Starting at $999.",
            "Your wedding day should be perfect \u2014 including the restrooms. Our luxury trailer features 4 private stalls with flushing toilets, AC, running water, and full-size mirrors. We deliver, set up, and pick up. Starting at $999.",
            "Don\u2019t let porta-potties ruin your outdoor wedding. Our 4-stall luxury restroom trailer is climate-controlled with flushing toilets, mirrors, and dark wood finishes. Starting at $999 \u2014 delivery and setup included.",
            "The most-used part of your wedding shouldn\u2019t be the worst part. Our luxury restroom trailer has 4 private stalls, flushing toilets, AC, running water, and full-size mirrors. Serving the San Fernando Valley and Greater LA. Starting at $999.",
            "Outdoor wedding? Skip the porta-potties. Our luxury 4-stall trailer features flushing toilets, AC, dark wood interiors, and full-size mirrors in every stall. We handle delivery, setup, and pickup. Starting at $999.",
        ],
        "headlines": [
            "Luxury Restrooms for Your Wedding",
            "Your Guests Will Thank You",
            "Skip the Porta-Potties",
            "4-Stall Luxury Restroom Trailer",
            "Starting at $999 \u2014 Book Your Date",
        ],
        "descriptions": [
            "4 private stalls. Flushing toilets. AC. Mirrors. Delivery and setup included.",
            "Luxury restroom trailer rental. Serving SFV and Greater LA.",
            "Flushing toilets, running water, AC, and dark wood interiors. Starting at $999.",
            "2 Gentlemen stalls. 2 Ladies stalls. Separate entrances. We deliver and set up.",
            "Book your wedding date today. Delivery, setup, and pickup included.",
        ],
    },
    "CONSTRUCTION": {
        "primary_texts": [
            "Need clean restrooms on your job site or production set? Our luxury 4-stall trailer has flushing toilets, running water, AC, and full-size mirrors. We deliver, set up, and pick up. Serving Greater LA. Starting at $999.",
            "Upgrade your crew\u2019s restroom situation. Our 4-stall luxury trailer beats any porta-potty \u2014 flushing toilets, AC, running water, mirrors. Delivery and setup included across Greater LA. Starting at $999.",
            "Film sets, construction sites, corporate events \u2014 our luxury restroom trailer handles it all. 4 private stalls, AC, flushing toilets, dark wood interiors. We deliver and set up. Starting at $999.",
            "Your crew deserves better than a blue box. Our luxury restroom trailer has 4 private stalls with flushing toilets, AC, running water, and mirrors. Delivery and setup included. Starting at $999.",
            "Long-term job site? Production shoot? Corporate event? Our 4-stall luxury restroom trailer is climate-controlled with flushing toilets, mirrors, and running water. We handle everything. Starting at $999.",
        ],
        "headlines": [
            "Luxury Restrooms for Your Job Site",
            "Better Than a Porta-Potty",
            "4-Stall Restroom Trailer Rental",
            "Clean Restrooms for Your Crew",
            "Starting at $999 \u2014 We Deliver",
        ],
        "descriptions": [
            "Flushing toilets, AC, running water. Delivery and setup included.",
            "Professional restroom rental for construction, film, and corporate events.",
            "4 private stalls. Climate-controlled. We deliver, set up, and pick up.",
            "Serving job sites and production sets across Greater LA. Starting at $999.",
            "Upgrade from porta-potties. Book your dates today.",
        ],
    },
    "EVENTS": {
        "primary_texts": [
            "Hosting an outdoor event? Skip the porta-potties. Our luxury 4-stall restroom trailer has flushing toilets, AC, running water, full-size mirrors, and dark wood interiors. We deliver, set up, and pick up. Starting at $999.",
            "Quincea\u00f1eras, birthdays, baby showers, backyard parties \u2014 your guests deserve a real restroom. Our luxury trailer has 4 private stalls with flushing toilets, AC, and mirrors. Delivery and setup included. Starting at $999.",
            "The most-used part of your event shouldn\u2019t be the worst part. Our luxury 4-stall restroom trailer is a massive upgrade from porta-potties. Flushing toilets, AC, running water, mirrors. Starting at $999.",
            "Throwing a party? Family reunion? Church event? Our luxury restroom trailer has 4 private stalls with flushing toilets, running water, AC, and full-size mirrors. We handle delivery, setup, and pickup. Starting at $999.",
            "Your outdoor event deserves luxury restrooms. 4 private stalls, flushing toilets, AC, dark wood interiors, and full-size mirrors. Serving the San Fernando Valley and Greater LA. Starting at $999.",
        ],
        "headlines": [
            "Luxury Restroom Trailer Rental",
            "Upgrade Your Event",
            "Skip the Porta-Potties",
            "4-Stall Luxury Trailer \u2014 Book Now",
            "Starting at $999 \u2014 We Deliver",
        ],
        "descriptions": [
            "4 private stalls. Flushing toilets. AC. Mirrors. Delivery and setup included.",
            "Serving LA, SFV, and surrounding areas. Book your date today.",
            "Flushing toilets, running water, AC, dark wood interiors. Starting at $999.",
            "Quincea\u00f1eras, birthdays, weddings, corporate events. We handle everything.",
            "Luxury restroom rental with delivery, setup, and pickup included.",
        ],
    },
}

# ============================================================
# PHASE 1: Fetch image hashes from all existing ads
# ============================================================
def fetch_image_hashes(campaign_name, ad_ids):
    """Get image_hash from each ad's creative."""
    hashes = []
    for ad_id in ad_ids:
        resp = api_get(ad_id, {"fields": "creative.fields(id,image_hash)"})
        if "creative" in resp and "image_hash" in resp["creative"]:
            h = resp["creative"]["image_hash"]
            if h not in hashes:  # deduplicate
                hashes.append(h)
            print(f"  Ad {ad_id}: hash={h}")
        else:
            print(f"  Ad {ad_id}: WARNING - no image_hash found! Response: {resp}")
    return hashes


# ============================================================
# PHASE 2: Enable dynamic creative on ad sets
# ============================================================
def enable_dynamic_creative(adset_id):
    """Try to enable is_dynamic_creative on the ad set."""
    resp = api_post(adset_id, {"is_dynamic_creative": "true"})
    return resp


# ============================================================
# PHASE 3: Create new dynamic creative
# ============================================================
def create_dynamic_creative(campaign_name, image_hashes, copy_data):
    """Create an ad creative with asset_feed_spec."""
    asset_feed = {
        "images": [{"hash": h} for h in image_hashes],
        "bodies": [{"text": t} for t in copy_data["primary_texts"]],
        "titles": [{"text": t} for t in copy_data["headlines"]],
        "descriptions": [{"text": t} for t in copy_data["descriptions"]],
        "ad_formats": ["SINGLE_IMAGE"],
        "call_to_action_types": ["CONTACT_US"],
        "link_urls": [{"website_url": SITE_URL}],
    }

    creative_params = {
        "name": f"Zoar_{campaign_name}_DynamicCreative",
        "asset_feed_spec": json.dumps(asset_feed),
        "object_story_spec": json.dumps({
            "page_id": PAGE_ID,
        }),
        "url_tags": f"utm_source=facebook&utm_medium=cpc&utm_campaign=zoar_{campaign_name.lower()}",
    }

    resp = api_post(f"{AD_ACCOUNT}/adcreatives", creative_params)
    return resp


# ============================================================
# PHASE 4: Create new ad with dynamic creative
# ============================================================
def create_ad(campaign_name, adset_id, creative_id):
    """Create a single ad using the dynamic creative."""
    ad_params = {
        "name": f"Zoar_{campaign_name}_AllImages",
        "adset_id": adset_id,
        "creative": json.dumps({"creative_id": creative_id}),
        "status": "ACTIVE",
        "tracking_specs": json.dumps([{
            "action.type": ["offsite_conversion"],
            "fb_pixel": [PIXEL_ID],
        }]),
    }
    resp = api_post(f"{AD_ACCOUNT}/ads", ad_params)
    return resp


# ============================================================
# PHASE 5: Delete old ads
# ============================================================
def delete_old_ads(ad_ids):
    """Delete all old individual ads."""
    results = []
    for ad_id in ad_ids:
        resp = api_delete(ad_id)
        results.append((ad_id, resp))
        print(f"  Deleted ad {ad_id}: {resp}")
    return results


# ============================================================
# PHASE 6: Verify
# ============================================================
def verify_new_ad(ad_id):
    """Read back the new ad to verify copy."""
    resp = api_get(ad_id, {
        "fields": "name,status,creative.fields(id,asset_feed_spec,url_tags)"
    })
    return resp


# ============================================================
# MAIN EXECUTION
# ============================================================
def main():
    results = {}

    for campaign_name, campaign_data in CAMPAIGNS.items():
        print("\n" + "=" * 70)
        print(f"  PROCESSING: {campaign_name}")
        print("=" * 70)

        adset_id = campaign_data["adset_id"]
        ad_ids = campaign_data["ad_ids"]
        copy_data = COPY[campaign_name]

        # --- Phase 1: Get image hashes ---
        print(f"\n[1/5] Fetching image hashes from {len(ad_ids)} ads...")
        image_hashes = fetch_image_hashes(campaign_name, ad_ids)
        print(f"  Found {len(image_hashes)} unique image hashes")

        if not image_hashes:
            print(f"  ERROR: No image hashes found for {campaign_name}! Skipping.")
            continue

        # --- Phase 2: Enable dynamic creative on ad set ---
        print(f"\n[2/5] Enabling dynamic creative on ad set {adset_id}...")
        dc_resp = enable_dynamic_creative(adset_id)
        print(f"  Response: {dc_resp}")

        if "error" in dc_resp:
            print(f"  WARNING: Could not enable dynamic creative: {dc_resp['error'].get('message', '')}")
            print(f"  Will try creating the creative anyway...")

        # --- Phase 3: Create dynamic creative ---
        print(f"\n[3/5] Creating dynamic creative with {len(image_hashes)} images, 5/5/5 copy...")
        creative_resp = create_dynamic_creative(campaign_name, image_hashes, copy_data)
        print(f"  Response: {json.dumps(creative_resp, indent=2)}")

        if "error" in creative_resp:
            print(f"  ERROR creating creative: {creative_resp['error'].get('message', '')}")
            print(f"  Error details: {json.dumps(creative_resp['error'], indent=2)}")
            results[campaign_name] = {"error": creative_resp["error"]}
            continue

        creative_id = creative_resp["id"]
        print(f"  Creative ID: {creative_id}")

        # --- Phase 4: Create new ad ---
        print(f"\n[4/5] Creating new consolidated ad...")
        ad_resp = create_ad(campaign_name, adset_id, creative_id)
        print(f"  Response: {json.dumps(ad_resp, indent=2)}")

        if "error" in ad_resp:
            print(f"  ERROR creating ad: {ad_resp['error'].get('message', '')}")
            results[campaign_name] = {"error": ad_resp["error"], "creative_id": creative_id}
            continue

        new_ad_id = ad_resp["id"]
        print(f"  New Ad ID: {new_ad_id}")

        # --- Phase 5: Delete old ads ---
        print(f"\n[5/5] Deleting {len(ad_ids)} old ads...")
        delete_results = delete_old_ads(ad_ids)

        # --- Verify ---
        print(f"\n[VERIFY] Reading back new ad...")
        verify = verify_new_ad(new_ad_id)
        print(f"  Status: {verify.get('status', 'UNKNOWN')}")
        print(f"  Name: {verify.get('name', 'UNKNOWN')}")

        results[campaign_name] = {
            "new_ad_id": new_ad_id,
            "creative_id": creative_id,
            "image_count": len(image_hashes),
            "image_hashes": image_hashes,
            "old_ads_deleted": len(ad_ids),
            "status": verify.get("status", "UNKNOWN"),
        }

    # === SUMMARY ===
    print("\n\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    for name, data in results.items():
        if "error" in data:
            print(f"\n  {name}: FAILED - {data['error'].get('message', str(data['error']))}")
        else:
            print(f"\n  {name}:")
            print(f"    New Ad ID:     {data['new_ad_id']}")
            print(f"    Creative ID:   {data['creative_id']}")
            print(f"    Images:        {data['image_count']}")
            print(f"    Old Ads Deleted: {data['old_ads_deleted']}")
            print(f"    Status:        {data['status']}")

    # Save results
    with open("/Users/kai/nexus/scripts/fb_fix_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to /Users/kai/nexus/scripts/fb_fix_results.json")

    return results


if __name__ == "__main__":
    main()
