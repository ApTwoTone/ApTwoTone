#!/usr/bin/env python3
"""
Phase 2: Create new Dynamic Creative ad sets + ads.
Creatives already exist. Old ads already deleted. Old ad sets need replacing.
"""

import json
import requests

API_VERSION = "v21.0"
BASE = f"https://graph.facebook.com/{API_VERSION}"
AD_ACCOUNT = "act_1713830169593455"
PIXEL_ID = "1579580876639654"

with open("/Users/kai/Downloads/fb_token_ads.txt") as f:
    TOKEN = f.read().strip().split("\n")[0]

def api_post(endpoint, data):
    d = {"access_token": TOKEN}
    d.update(data)
    r = requests.post(f"{BASE}/{endpoint}", data=d)
    return r.json()

def api_delete(endpoint):
    r = requests.delete(f"{BASE}/{endpoint}", params={"access_token": TOKEN})
    return r.json()

# === Ad set configs (from existing ones, with is_dynamic_creative added) ===
AD_SETS = [
    {
        "label": "WEDDINGS",
        "campaign_id": "120240274369540446",
        "old_adset_id": "120240274369680446",
        "creative_id": "1419897139834628",
        "name": "Zoar_Weddings_DynamicCreative_AdSet",
        "daily_budget": "500",
        "optimization_goal": "OFFSITE_CONVERSIONS",
        "billing_event": "IMPRESSIONS",
        "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
        "targeting": {
            "age_max": 55,
            "age_min": 25,
            "flexible_spec": [{"interests": [
                {"id": "6003029412207", "name": "Wedding reception"},
                {"id": "6003349071605", "name": "Wedding photography"},
                {"id": "6003360603580", "name": "Engagement"},
                {"id": "6003402319418", "name": "Wedding dress"},
                {"id": "6003409392877", "name": "Weddings"},
                {"id": "6836096590166", "name": "Bridal shop"},
            ]}],
            "geo_locations": {
                "custom_locations": [{
                    "distance_unit": "mile",
                    "latitude": 34.2011,
                    "longitude": -118.5976,
                    "radius": 30,
                }],
            },
            "targeting_automation": {"advantage_audience": 0},
            "publisher_platforms": ["facebook", "instagram"],
            "facebook_positions": ["feed"],
            "instagram_positions": ["stream", "story", "reels"],
        },
    },
    {
        "label": "CONSTRUCTION",
        "campaign_id": "120240274384370446",
        "old_adset_id": "120240274384740446",
        "creative_id": "1960559044817182",
        "name": "Zoar_Construction_DynamicCreative_AdSet",
        "daily_budget": "500",
        "optimization_goal": "OFFSITE_CONVERSIONS",
        "billing_event": "IMPRESSIONS",
        "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
        "targeting": {
            "age_max": 65,
            "age_min": 28,
            "flexible_spec": [{"interests": [
                {"id": "6003142479061", "name": "Construction management"},
                {"id": "6003331677371", "name": "Production company"},
                {"id": "6003395414271", "name": "Construction"},
                {"id": "6003714246353", "name": "Filmmaking"},
                {"id": "6741786978515", "name": "Movie and Television Industry"},
            ]}],
            "geo_locations": {
                "custom_locations": [{
                    "distance_unit": "mile",
                    "latitude": 34.2011,
                    "longitude": -118.5976,
                    "radius": 30,
                }],
            },
            "targeting_automation": {"advantage_audience": 0},
            "publisher_platforms": ["facebook", "instagram"],
            "facebook_positions": ["feed"],
            "instagram_positions": ["stream", "story", "reels"],
        },
    },
    {
        "label": "EVENTS",
        "campaign_id": "120240274393510446",
        "old_adset_id": "120240274393600446",
        "creative_id": "3434589723382911",
        "name": "Zoar_Events_DynamicCreative_AdSet",
        "daily_budget": "500",
        "optimization_goal": "OFFSITE_CONVERSIONS",
        "billing_event": "IMPRESSIONS",
        "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
        "targeting": {
            "age_max": 60,
            "age_min": 25,
            "flexible_spec": [{"interests": [
                {"id": "6003092932417", "name": "Event management"},
                {"id": "6003152263430", "name": "Birthday"},
                {"id": "6003321277514", "name": "Baby shower"},
                {"id": "6899296281873", "name": "Special occasions and events"},
            ]}],
            "geo_locations": {
                "custom_locations": [{
                    "distance_unit": "mile",
                    "latitude": 34.2011,
                    "longitude": -118.5976,
                    "radius": 30,
                }],
            },
            "targeting_automation": {"advantage_audience": 0},
            "publisher_platforms": ["facebook", "instagram"],
            "facebook_positions": ["feed"],
            "instagram_positions": ["stream", "story", "reels"],
        },
    },
]

results = {}

for cfg in AD_SETS:
    label = cfg["label"]
    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")

    # Step 1: Delete old empty ad set
    print(f"\n  [1/3] Deleting old ad set {cfg['old_adset_id']}...")
    del_resp = api_delete(cfg["old_adset_id"])
    print(f"    Result: {del_resp}")

    # Step 2: Create new ad set with is_dynamic_creative=true
    print(f"\n  [2/3] Creating new Dynamic Creative ad set...")
    adset_params = {
        "campaign_id": cfg["campaign_id"],
        "name": cfg["name"],
        "daily_budget": cfg["daily_budget"],
        "optimization_goal": cfg["optimization_goal"],
        "billing_event": cfg["billing_event"],
        "bid_strategy": cfg["bid_strategy"],
        "targeting": json.dumps(cfg["targeting"]),
        "promoted_object": json.dumps({
            "pixel_id": PIXEL_ID,
            "custom_event_type": "LEAD",
        }),
        "is_dynamic_creative": "true",
        "status": "ACTIVE",
    }
    adset_resp = api_post(f"{AD_ACCOUNT}/adsets", adset_params)
    print(f"    Result: {json.dumps(adset_resp, indent=4)}")

    if "error" in adset_resp:
        print(f"    ERROR: {adset_resp['error'].get('error_user_msg', adset_resp['error']['message'])}")
        results[label] = {"error": adset_resp["error"]}
        continue

    new_adset_id = adset_resp["id"]
    print(f"    New Ad Set ID: {new_adset_id}")

    # Step 3: Create ad in new ad set
    print(f"\n  [3/3] Creating ad with creative {cfg['creative_id']}...")
    ad_params = {
        "name": f"Zoar_{label}_AllImages",
        "adset_id": new_adset_id,
        "creative": json.dumps({"creative_id": cfg["creative_id"]}),
        "status": "ACTIVE",
        "tracking_specs": json.dumps([{
            "action.type": ["offsite_conversion"],
            "fb_pixel": [PIXEL_ID],
        }]),
    }
    ad_resp = api_post(f"{AD_ACCOUNT}/ads", ad_params)
    print(f"    Result: {json.dumps(ad_resp, indent=4)}")

    if "error" in ad_resp:
        print(f"    ERROR: {ad_resp['error'].get('error_user_msg', ad_resp['error']['message'])}")
        results[label] = {"error": ad_resp["error"], "new_adset_id": new_adset_id}
        continue

    new_ad_id = ad_resp["id"]
    print(f"    New Ad ID: {new_ad_id}")

    results[label] = {
        "new_adset_id": new_adset_id,
        "new_ad_id": new_ad_id,
        "creative_id": cfg["creative_id"],
        "old_adset_deleted": cfg["old_adset_id"],
    }

# === SUMMARY ===
print(f"\n\n{'='*60}")
print("  SUMMARY")
print(f"{'='*60}")
for label, data in results.items():
    if "error" in data:
        print(f"\n  {label}: FAILED")
        print(f"    Error: {data['error'].get('message', str(data['error']))}")
    else:
        print(f"\n  {label}: SUCCESS")
        print(f"    New Ad Set: {data['new_adset_id']}")
        print(f"    New Ad:     {data['new_ad_id']}")
        print(f"    Creative:   {data['creative_id']}")

# Save results
with open("/Users/kai/nexus/scripts/fb_fix_results.json", "w") as f:
    json.dump(results, f, indent=2)
print(f"\nResults saved to fb_fix_results.json")
