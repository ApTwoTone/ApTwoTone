"""Google Places API vendor discovery — replaces AI-hallucinated research with real businesses.

Requires `google_places_api_key` in ~/.nexus/config.json.
If key is missing, all functions return empty results without crashing.
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger("google_places")

# ── Config ────────────────────────────────────────────────────────────────────

_CONFIG_PATH = Path.home() / ".nexus" / "config.json"

PLACES_TEXT_SEARCH = "https://maps.googleapis.com/maps/api/place/textsearch/json"
PLACES_DETAILS = "https://maps.googleapis.com/maps/api/place/details/json"

# Maps our vendor categories to Google Places search queries
CATEGORY_QUERIES = {
    "Wedding Venue": "wedding venue",
    "Event Venue": "event venue",
    "Banquet Hall": "banquet hall",
    "Catering": "catering company",
    "Wedding Planner": "wedding planner",
    "Event Planner": "event planner",
    "Party Rental": "party rental company",
    "Country Club": "country club",
    "Restaurant": "restaurant with event space",
    "Hotel": "hotel with event space",
    "Winery": "winery wedding venue",
    "Community Center": "community center event rental",
}

# Rate limit: 1 request per 100ms
_last_request_time = 0.0


def _load_api_key() -> str:
    """Load Google Places API key from config. Returns empty string if not found."""
    try:
        config = json.loads(_CONFIG_PATH.read_text())
        return config.get("google_places_api_key", "")
    except Exception:
        return ""


def _google_places_request(endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Make a Google Places API request with rate limiting and key injection."""
    global _last_request_time

    api_key = _load_api_key()
    if not api_key:
        log.warning("Google Places API key not configured — skipping request")
        return {"results": [], "status": "NO_API_KEY"}

    params["key"] = api_key

    # Rate limit
    elapsed = time.time() - _last_request_time
    if elapsed < 0.1:
        time.sleep(0.1 - elapsed)
    _last_request_time = time.time()

    try:
        resp = httpx.get(endpoint, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error("Google Places request failed: %s", e)
        return {"results": [], "status": "REQUEST_ERROR", "error": str(e)}


# ── Search ────────────────────────────────────────────────────────────────────


def search_vendors_nearby(
    category: str,
    location: str = "San Fernando, CA",
    radius_miles: int = 15,
) -> List[Dict[str, Any]]:
    """Search Google Places for vendors in a category near a location.

    Returns list of dicts with: name, address, phone, website, rating, place_id, types.
    Paginates with next_page_token (max 60 results per query).
    """
    query = CATEGORY_QUERIES.get(category, category)
    radius_meters = int(radius_miles * 1609.34)

    all_results = []
    params = {
        "query": f"{query} near {location}",
        "radius": radius_meters,
        "type": "establishment",
    }

    for page in range(3):  # Max 3 pages (60 results)
        data = _google_places_request(PLACES_TEXT_SEARCH, params)

        if data.get("status") in ("NO_API_KEY", "REQUEST_ERROR"):
            return all_results

        for place in data.get("results", []):
            all_results.append({
                "name": place.get("name", ""),
                "address": place.get("formatted_address", ""),
                "rating": place.get("rating", 0),
                "user_ratings_total": place.get("user_ratings_total", 0),
                "place_id": place.get("place_id", ""),
                "types": place.get("types", []),
                "business_status": place.get("business_status", ""),
            })

        next_token = data.get("next_page_token")
        if not next_token:
            break

        # Google requires a short delay before using next_page_token
        time.sleep(2)
        params = {"pagetoken": next_token}

    log.info("Found %d places for '%s' near %s", len(all_results), category, location)
    return all_results


def get_vendor_details(place_id: str) -> Dict[str, Any]:
    """Get detailed info for a single place (phone, website, hours, reviews)."""
    params = {
        "place_id": place_id,
        "fields": "name,formatted_phone_number,international_phone_number,"
                  "website,opening_hours,business_status,user_ratings_total,"
                  "formatted_address,types",
    }
    data = _google_places_request(PLACES_DETAILS, params)
    result = data.get("result", {})
    return {
        "name": result.get("name", ""),
        "phone": result.get("formatted_phone_number", ""),
        "phone_intl": result.get("international_phone_number", ""),
        "website": result.get("website", ""),
        "address": result.get("formatted_address", ""),
        "business_status": result.get("business_status", ""),
        "reviews_count": result.get("user_ratings_total", 0),
        "types": result.get("types", []),
        "has_hours": bool(result.get("opening_hours")),
    }


# ── Discover and Save ─────────────────────────────────────────────────────────


def _extract_city(address: str) -> str:
    """Extract city from a Google Places formatted address like '123 Main St, San Fernando, CA 91340'."""
    parts = [p.strip() for p in address.split(",")]
    if len(parts) >= 3:
        return parts[-3] if len(parts) >= 4 else parts[-2]
    return parts[0] if parts else ""


def discover_and_save(
    categories: Optional[List[str]] = None,
    location: str = "San Fernando, CA",
    radius_miles: int = 15,
) -> Dict[str, Any]:
    """Main entry: search Google Places for vendors, get details, save to DB.

    Returns stats: {found, saved, duplicates, rejected, skipped_no_key}.
    """
    from core.vendor_db import save_vendor

    api_key = _load_api_key()
    if not api_key:
        log.warning("Google Places API key not configured — cannot discover vendors")
        return {"found": 0, "saved": 0, "duplicates": 0, "rejected": 0, "skipped_no_key": True}

    if categories is None:
        categories = list(CATEGORY_QUERIES.keys())

    stats = {"found": 0, "saved": 0, "duplicates": 0, "rejected": 0, "skipped_no_key": False}

    for category in categories:
        places = search_vendors_nearby(category, location, radius_miles)
        stats["found"] += len(places)

        for place in places:
            # Skip non-operational businesses
            if place.get("business_status") == "CLOSED_PERMANENTLY":
                stats["rejected"] += 1
                continue

            # Get details (phone, website)
            details = get_vendor_details(place["place_id"])
            if not details.get("phone") and not details.get("website"):
                stats["rejected"] += 1
                continue

            city = _extract_city(details.get("address", place.get("address", "")))

            vendor = {
                "name": details.get("name") or place.get("name", ""),
                "phone": details.get("phone", ""),
                "email": "",  # Google Places doesn't provide emails
                "website": details.get("website", ""),
                "city": city,
                "state": "CA",
                "category": category,
                "source": "google_places",
                "google_place_id": place.get("place_id", ""),
                "google_rating": place.get("rating", 0),
                "google_reviews": details.get("reviews_count", 0),
            }

            result = save_vendor(vendor)
            action = result.get("action", "")
            if action == "created":
                stats["saved"] += 1
            elif action in ("updated", "skipped"):
                stats["duplicates"] += 1
            else:
                stats["rejected"] += 1

    log.info("Google Places discovery complete: %s", stats)
    return stats


# ── Async wrapper for coordinator integration ─────────────────────────────────


async def async_discover_and_save(
    categories: Optional[List[str]] = None,
    location: str = "San Fernando, CA",
    radius_miles: int = 15,
) -> Dict[str, Any]:
    """Async wrapper for discover_and_save (runs sync code in executor)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, discover_and_save, categories, location, radius_miles
    )
