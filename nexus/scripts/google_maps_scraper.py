"""
Google Maps Vendor Scraper — Playwright + stealth mode.

Searches Google Maps for event vendors in the San Fernando Valley and
Greater LA area. Extracts business name, phone, website, address, category,
rating, and review count from each result.

All results go through save_vendor() for geo-filtering, vetting, and dedup.

Usage:
    python scripts/google_maps_scraper.py [--headless] [--max-queries N]
"""
import json
import logging
import random
import re
import sys
import time
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.vendor_db import save_vendor

log = logging.getLogger("google_maps_scraper")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

# ── Search queries ───────────────────────────────────────────────────────────

QUERIES = [
    # Tier 1: Direct referral partners — wedding/event planners
    "wedding planner San Fernando Valley",
    "event planner San Fernando Valley",
    "wedding planner Encino",
    "wedding planner Woodland Hills",
    "wedding planner Calabasas",
    "wedding planner Burbank",
    "wedding planner Glendale",
    "event coordinator Los Angeles",
    "quinceañera planner San Fernando Valley",
    "quinceañera planner Van Nuys",
    "quinceañera planner North Hollywood",
    # Tier 1: Venues
    "outdoor wedding venue San Fernando Valley",
    "outdoor event venue Los Angeles",
    "wedding venue Calabasas",
    "ranch wedding venue Los Angeles",
    "garden wedding venue Los Angeles",
    "banquet hall Van Nuys",
    "banquet hall Glendale",
    "banquet hall North Hollywood",
    "event space Woodland Hills",
    "event venue Northridge",
    # Tier 2: Cross-referral partners
    "party rental San Fernando Valley",
    "tent rental Los Angeles",
    "table chair rental San Fernando Valley",
    "party rental Van Nuys",
    "party rental Burbank",
    "catering company San Fernando Valley",
    "wedding catering Los Angeles",
    "event catering Burbank",
    "DJ wedding Los Angeles",
    "DJ events San Fernando Valley",
    # Tier 3: Direct clients
    "film production company Burbank",
    "production company North Hollywood",
    "general contractor San Fernando Valley",
    "construction company Van Nuys",
]

# ── Google category → our category mapping ───────────────────────────────────

GOOGLE_TO_CATEGORY = {
    "wedding planner": "wedding_planner",
    "event planner": "event_planner",
    "event management company": "event_planner",
    "wedding venue": "wedding_venue",
    "event venue": "event_venue",
    "banquet hall": "banquet_hall",
    "reception hall": "banquet_hall",
    "party equipment rental service": "party_rental",
    "tent rental service": "tent_rental",
    "party store": "party_rental",
    "caterer": "catering",
    "catering service": "catering",
    "dj service": "dj",
    "disc jockey service": "dj",
    "photographer": "photographer",
    "wedding photographer": "photographer",
    "videographer": "videography",
    "florist": "florist",
    "general contractor": "construction",
    "winery": "winery",
    "country club": "country_club",
    "restaurant": "restaurant",
    "hotel": "hotel",
    "community center": "community_center",
    "church": "church",
}

# Fallback: infer category from search query
QUERY_TO_CATEGORY = {
    "wedding planner": "wedding_planner",
    "event planner": "event_planner",
    "event coordinator": "event_planner",
    "quinceañera planner": "quinceanera_planner",
    "wedding venue": "wedding_venue",
    "event venue": "event_venue",
    "outdoor wedding venue": "wedding_venue",
    "outdoor event venue": "event_venue",
    "banquet hall": "banquet_hall",
    "event space": "event_venue",
    "party rental": "party_rental",
    "tent rental": "tent_rental",
    "table chair rental": "party_rental",
    "catering": "catering",
    "wedding catering": "catering",
    "event catering": "catering",
    "DJ": "dj",
    "general contractor": "construction",
    "construction company": "construction",
    "ranch wedding": "wedding_venue",
    "garden wedding": "wedding_venue",
}


def _infer_category_from_query(query):
    """Infer vendor category from the search query used."""
    q = query.lower()
    for key, cat in QUERY_TO_CATEGORY.items():
        if key.lower() in q:
            return cat
    return "event_planner"


def _map_google_category(google_cat, query):
    """Map Google's category label to our category, falling back to query inference."""
    if not google_cat:
        return _infer_category_from_query(query)
    gl = google_cat.lower().strip()
    for key, cat in GOOGLE_TO_CATEGORY.items():
        if key in gl:
            return cat
    return _infer_category_from_query(query)


def _parse_city_from_address(address):
    """Extract city from a Google Maps address string like '12345 Main St, Van Nuys, CA 91401'."""
    if not address:
        return ""
    parts = [p.strip() for p in address.split(",")]
    if len(parts) >= 3:
        return parts[-2].split()[0] if parts[-2] else parts[1]
    if len(parts) == 2:
        return parts[0]
    return ""


def _jitter(base_seconds):
    """Add ±30% random jitter to a delay."""
    return base_seconds * (0.7 + random.random() * 0.6)


def scrape_google_maps(headless=True, max_queries=None):
    """Run all Google Maps search queries and return total vendors saved."""
    from playwright.sync_api import sync_playwright
    from playwright_stealth import Stealth

    queries = QUERIES[:max_queries] if max_queries else QUERIES
    total_saved = 0
    total_skipped = 0
    total_rejected = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="en-US",
        )
        page = context.new_page()
        Stealth().apply_stealth_sync(page)

        for qi, query in enumerate(queries):
            log.info("Query %d/%d: %s", qi + 1, len(queries), query)
            try:
                results = _scrape_single_query(page, query)
                for vendor_data in results:
                    result = save_vendor(vendor_data)
                    action = result.get("action", "error")
                    if action == "created":
                        total_saved += 1
                    elif action in ("updated", "skipped"):
                        total_skipped += 1
                    else:
                        total_rejected += 1
                log.info("  → %d results, running total: %d saved", len(results), total_saved)
            except Exception as e:
                log.error("  → Query failed: %s", e)

            # Rate limit between queries
            time.sleep(_jitter(10))

        browser.close()

    log.info("DONE: %d saved, %d skipped/dupes, %d rejected", total_saved, total_skipped, total_rejected)
    return total_saved


def _scrape_single_query(page, query):
    """Search Google Maps for a query and extract all business results."""
    vendors = []

    # Navigate directly to Google Maps search URL
    import urllib.parse
    encoded_query = urllib.parse.quote(query)
    page.goto("https://www.google.com/maps/search/%s/" % encoded_query, wait_until="domcontentloaded")
    time.sleep(_jitter(5))

    # Handle consent dialog if it appears
    try:
        accept_btn = page.locator('button:has-text("Accept all")').first
        if accept_btn.count() > 0:
            accept_btn.click()
            time.sleep(2)
    except Exception:
        pass

    # Wait for results feed to appear
    feed = page.locator('div[role="feed"]')
    if feed.count() == 0:
        log.warning("  No results feed found for query: %s", query)
        return vendors

    # Scroll the results feed to load all results
    for scroll_i in range(15):
        try:
            page.evaluate('(el) => el.scrollTop = el.scrollHeight',
                          feed.element_handle())
            time.sleep(_jitter(2))
            # Check for end of results
            if page.locator('text="You\'ve reached the end of the list"').count() > 0:
                break
        except Exception:
            break

    # Find all result links — they have an href starting with /maps/place/
    # Use a more stable selector: links within the feed
    result_links = feed.locator('a[href*="/maps/place/"]').all()
    log.info("  Found %d result links", len(result_links))

    seen_names = set()
    for i, link in enumerate(result_links):
        try:
            # Click the result to open details panel
            link.click()
            time.sleep(_jitter(1.5))

            vendor = _extract_details(page, query)
            if vendor and vendor["name"] not in seen_names:
                seen_names.add(vendor["name"])
                vendors.append(vendor)
        except Exception as e:
            log.debug("  Result %d extraction failed: %s", i, e)
            continue

    return vendors


def _extract_details(page, query):
    """Extract vendor details from the Google Maps details panel."""
    vendor = {
        "source": "google_maps",
        "vetting_score": 70,
    }

    # Name — try multiple selectors (Google changes these)
    for sel in ['h1.DUwDvf', 'h1[class*="header"]', 'div[role="main"] h1']:
        el = page.locator(sel).first
        if el.count() > 0:
            vendor["name"] = el.text_content(timeout=2000).strip()
            break

    if not vendor.get("name"):
        return None

    # Category — from the subtitle/type element
    for sel in ['button[jsaction*="category"]', 'span.DkEaL', '[class*="category"]']:
        el = page.locator(sel).first
        if el.count() > 0:
            raw_cat = el.text_content(timeout=1000).strip()
            vendor["category"] = _map_google_category(raw_cat, query)
            break
    if "category" not in vendor:
        vendor["category"] = _infer_category_from_query(query)

    # Rating and review count from aria-label
    try:
        rating_el = page.locator('[role="img"][aria-label*="stars"]').first
        if rating_el.count() > 0:
            aria = rating_el.get_attribute("aria-label", timeout=1000)
            if aria:
                m = re.search(r"([\d.]+)\s*stars?", aria)
                if m:
                    vendor["rating"] = float(m.group(1))
                m2 = re.search(r"([\d,]+)\s*reviews?", aria)
                if m2:
                    vendor["review_count"] = int(m2.group(1).replace(",", ""))
    except Exception:
        pass

    # Address, phone, website — from the info buttons/links
    try:
        # Address — button with data-item-id containing "address" or aria-label with address
        addr_el = page.locator('button[data-item-id="address"]').first
        if addr_el.count() == 0:
            addr_el = page.locator('button[aria-label*="Address"]').first
        if addr_el.count() > 0:
            vendor["address"] = addr_el.text_content(timeout=1000).strip()
            vendor["city"] = _parse_city_from_address(vendor["address"])
    except Exception:
        pass

    try:
        # Phone
        phone_el = page.locator('button[data-item-id*="phone"]').first
        if phone_el.count() == 0:
            phone_el = page.locator('button[aria-label*="Phone"]').first
        if phone_el.count() > 0:
            raw_phone = phone_el.text_content(timeout=1000).strip()
            # Clean phone: keep only digits, parens, dashes, spaces
            vendor["phone"] = re.sub(r'[^\d\(\)\-\s\+]', '', raw_phone).strip()
    except Exception:
        pass

    try:
        # Website
        web_el = page.locator('a[data-item-id="authority"]').first
        if web_el.count() == 0:
            web_el = page.locator('a[aria-label*="Website"]').first
        if web_el.count() > 0:
            href = web_el.get_attribute("href", timeout=1000)
            if href and "google.com" not in href:
                vendor["website"] = href
    except Exception:
        pass

    # Google Maps URL
    try:
        vendor["notes"] = "google_maps_url: %s" % page.url
    except Exception:
        pass

    return vendor


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Scrape Google Maps for SFV event vendors")
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--no-headless", action="store_true", help="Run with visible browser")
    parser.add_argument("--max-queries", type=int, default=None, help="Limit number of queries")
    args = parser.parse_args()

    headless = not args.no_headless
    count = scrape_google_maps(headless=headless, max_queries=args.max_queries)
    print("Google Maps scraper complete: %d vendors saved" % count)
