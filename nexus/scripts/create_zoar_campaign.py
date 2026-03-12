#!/usr/bin/env python3
"""
Create Zoar Bathroom Rentals — SFV Weddings Lead Gen Campaign

Creates a PAUSED campaign with:
  - Campaign: $5/day budget, OUTCOME_LEADS objective
  - Ad Set: SFV zip codes, ages 25-55, wedding/event interests
  - Lead Form: name, email, phone, event date, event type, guest count, city
  - Ad: Single image, approved copy, GET_QUOTE CTA

All created PAUSED — Kai activates manually after review.
Saves all IDs to facebook_campaign_ids.json.
"""
import json
import sys
import traceback
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.meta_ads import MetaAdsManager, TARGETING_PRESETS


def main():
    print("=" * 60)
    print("ZOAR CAMPAIGN CREATOR — SFV Wedding Leads")
    print("=" * 60)

    mgr = MetaAdsManager()
    results = {}
    errors = []

    # ── 1. Create Campaign ($5/day = 500 cents) ──────────────────────
    print("\n[1/4] Creating campaign...")
    campaign = mgr.create_campaign(
        name="Zoar Bathroom Rentals — SFV Weddings",
        objective="OUTCOME_LEADS",
        budget_cents=500,  # $5/day
        status="PAUSED",
    )
    if "error" in campaign:
        errors.append(f"Campaign: {campaign['error']}")
        print(f"  ERROR: {campaign['error']}")
        # Can't continue without campaign
        _save_results(results, errors)
        return
    results["campaign"] = campaign
    print(f"  OK — Campaign ID: {campaign['id']}")

    # ── 2. Create Ad Set with SFV targeting ──────────────────────────
    print("\n[2/4] Creating ad set with SFV wedding targeting...")

    # Custom targeting: SFV zip codes, ages 25-55, wedding interests
    sfv_targeting = {
        "geo_locations": {
            "location_types": ["home"],
            "zips": [
                {"key": z} for z in [
                    "91301", "91302", "91303", "91304", "91306", "91307",
                    "91311", "91316", "91324", "91325", "91326", "91330",
                    "91331", "91335", "91340", "91342", "91343", "91344",
                    "91345", "91352", "91356", "91364", "91367", "91371",
                    "91381", "91382", "91384", "91387", "91390", "91401",
                    "91402", "91403", "91405", "91406", "91411", "91423",
                    "91436", "91501", "91502", "91504", "91505", "91506",
                    "91601", "91602", "91604", "91605", "91606", "91607",
                ]
            ],
        },
        "age_min": 25,
        "age_max": 55,
        "flexible_spec": [
            {
                "interests": [
                    {"id": "6003384285439", "name": "Wedding planning"},
                    {"id": "6003020834693", "name": "The Knot"},
                    {"id": "6003397425735", "name": "WeddingWire"},
                    {"id": "6003348604839", "name": "Event planning"},
                    {"id": "6003012949676", "name": "Outdoor recreation"},
                ]
            }
        ],
    }

    ad_set = mgr.create_ad_set(
        campaign_id=campaign["id"],
        name="SFV Wedding Leads — $5 Daily",
        targeting=sfv_targeting,
        budget_cents=500,
        optimization_goal="LEAD_GENERATION",
        bid_strategy="LOWEST_COST_WITHOUT_CAP",
    )
    if "error" in ad_set:
        errors.append(f"Ad Set: {ad_set['error']}")
        print(f"  ERROR: {ad_set['error']}")
    else:
        results["ad_set"] = ad_set
        print(f"  OK — Ad Set ID: {ad_set['id']}")

    # ── 3. Create Lead Form ──────────────────────────────────────────
    print("\n[3/4] Creating lead form...")
    try:
        lead_form = mgr.create_lead_form(
            name="Zoar Restroom Rental Inquiry",
            questions=[
                {"type": "FULL_NAME", "label": "Full Name"},
                {"type": "EMAIL", "label": "Email Address"},
                {"type": "PHONE", "label": "Phone Number"},
                {"type": "CUSTOM", "key": "event_date", "label": "Event Date (MM/DD/YYYY)"},
                {"type": "CUSTOM", "key": "event_type", "label": "Event Type",
                 "options": [
                     {"value": "Wedding", "key": "wedding"},
                     {"value": "Quinceañera", "key": "quinceanera"},
                     {"value": "Corporate Event", "key": "corporate"},
                     {"value": "Backyard Party", "key": "backyard"},
                     {"value": "Festival", "key": "festival"},
                     {"value": "Film Production", "key": "film"},
                     {"value": "Other", "key": "other"},
                 ]},
                {"type": "CUSTOM", "key": "guest_count", "label": "Estimated Guest Count"},
                {"type": "CUSTOM", "key": "event_city", "label": "Event City"},
            ],
            thank_you_page={
                "title": "Thank You!",
                "body": "We'll send you a personalized quote within minutes. "
                        "Our luxury restroom trailers feature flushing toilets, "
                        "running water, climate control, and premium finishes. "
                        "We look forward to making your event special!",
            },
            privacy_policy_url="https://zoarbathroomrental.com/privacy",
        )
        if isinstance(lead_form, dict) and "error" in lead_form:
            errors.append(f"Lead Form: {lead_form['error']}")
            print(f"  ERROR: {lead_form['error']}")
        else:
            results["lead_form"] = lead_form if isinstance(lead_form, dict) else {"id": str(lead_form)}
            print(f"  OK — Lead Form ID: {results['lead_form'].get('id', '?')}")
    except Exception as e:
        errors.append(f"Lead Form: {e}")
        print(f"  ERROR: {e}")

    # ── 4. Create Ad Creative + Ad ──────────────────────────────────
    print("\n[4/4] Creating ad creative + ad...")
    if "ad_set" in results:
        # Try to upload an image first (look for trailer photos)
        image_hash = None
        image_candidates = [
            Path(__file__).parent.parent / "static" / "trailer.jpg",
            Path(__file__).parent.parent / "static" / "zoar-trailer.jpg",
            Path(__file__).parent.parent / "static" / "images" / "trailer.jpg",
        ]
        for img_path in image_candidates:
            if img_path.exists():
                try:
                    upload_result = mgr.upload_image(str(img_path))
                    if isinstance(upload_result, dict) and upload_result.get("hash"):
                        image_hash = upload_result["hash"]
                        print(f"  Image uploaded: {img_path.name} -> {image_hash}")
                        break
                except Exception:
                    pass

        if not image_hash:
            print("  NOTE: No trailer image found. Upload an image to Ads Manager")
            print("        and create the ad creative manually.")
            print("        Approved ad copy saved below for reference:")
            print()
            print("  Headline: Luxury Restroom Trailers — Starting at $999")
            print("  Body: Skip the porta potties. Your guests deserve flushing")
            print("        toilets, running water, and climate control.")
            print("  Description: Get a free quote in minutes")
            print("  CTA: GET_QUOTE")
            print("  URL: https://zoarbathroomrental.com")
            results["ad_copy"] = {
                "headline": "Luxury Restroom Trailers — Starting at $999",
                "body": (
                    "Skip the porta potties. Your guests deserve flushing toilets, "
                    "running water, and climate control.\n\n"
                    "Zoar Bathroom Rentals delivers luxury restroom trailers "
                    "for weddings and events across the San Fernando Valley.\n\n"
                    "Delivery and setup included. Pricing varies by location."
                ),
                "description": "Get a free quote in minutes",
                "cta": "GET_QUOTE",
                "url": "https://zoarbathroomrental.com",
            }
        else:
            try:
                creative = mgr.create_creative(
                    name="Zoar SFV Wedding — Primary",
                    image_hash_or_video_id=image_hash,
                    primary_text=(
                        "Skip the porta potties. Your guests deserve flushing toilets, "
                        "running water, and climate control.\n\n"
                        "Zoar Bathroom Rentals delivers luxury restroom trailers "
                        "for weddings and events across the San Fernando Valley.\n\n"
                        "Delivery and setup included. Pricing varies by location."
                    ),
                    headline="Luxury Restroom Trailers — Starting at $999",
                    description="Get a free quote in minutes",
                    cta="GET_QUOTE",
                    link="https://zoarbathroomrental.com",
                )
                if isinstance(creative, dict) and "error" in creative:
                    errors.append(f"Creative: {creative['error']}")
                    print(f"  Creative ERROR: {creative['error']}")
                else:
                    creative_id = creative.get("id", "") if isinstance(creative, dict) else str(creative)
                    results["creative"] = creative if isinstance(creative, dict) else {"id": creative_id}

                    ad = mgr.create_ad(
                        ad_set_id=results["ad_set"]["id"],
                        creative_id=creative_id,
                        name="Zoar SFV Wedding — Primary Ad",
                        status="PAUSED",
                    )
                    if "error" in ad:
                        errors.append(f"Ad: {ad['error']}")
                        print(f"  Ad ERROR: {ad['error']}")
                    else:
                        results["ad"] = ad
                        print(f"  OK — Ad ID: {ad['id']}")
            except Exception as e:
                errors.append(f"Ad creation: {e}")
                print(f"  ERROR: {e}")
    else:
        print("  SKIPPED — no ad set created")

    # ── Save Results ─────────────────────────────────────────────────
    _save_results(results, errors)


def _save_results(results, errors):
    output = {
        "created_at": __import__("datetime").datetime.utcnow().isoformat(),
        "status": "partial" if errors else "success",
        "results": results,
        "errors": errors,
    }

    out_path = Path(__file__).parent / "facebook_campaign_ids.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nResults saved to {out_path}")

    if errors:
        print(f"\n{len(errors)} error(s):")
        for e in errors:
            print(f"  - {e}")

        # Also log to error file
        err_path = Path(__file__).parent / "facebook_ads_errors.log"
        with open(err_path, "a") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"{output['created_at']}\n")
            for e in errors:
                f.write(f"  {e}\n")

        # Telegram alert
        try:
            from scripts.notify_telegram import send_telegram, load_config
            cfg = load_config()
            token = cfg.get("telegram_token", "")
            for cid in cfg.get("telegram_chat_ids", []):
                send_telegram(token, str(cid),
                              f"FB ADS SETUP: {len(errors)} errors\n" + "\n".join(errors[:5]))
        except Exception:
            pass
    else:
        print("\nAll components created successfully (PAUSED).")
        print("Activate campaigns manually in Facebook Ads Manager.")


if __name__ == "__main__":
    main()
