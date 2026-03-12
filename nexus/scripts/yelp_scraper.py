"""
Yelp Vendor Scraper — Playwright-based.

Searches Yelp for event vendors in the San Fernando Valley and Greater LA.
Extracts business name, rating, review count, phone, category, and address.

All results go through save_vendor() for geo-filtering, vetting, and dedup.

Usage:
    python scripts/yelp_scraper.py [--no-headless] [--max-queries N]
"""
import logging
import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.vendor_db import save_vendor

log = logging.getLogger("yelp_scraper")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

# ── Search queries ───────────────────────────────────────────────────────────

YELP_SEARCHES = [
    {"cflt": "eventplanning", "find_loc": "San Fernando Valley, CA"},
    {"cflt": "eventplanning", "find_loc": "Encino, CA"},
    {"cflt": "eventplanning", "find_loc": "Woodland Hills, CA"},
    {"cflt": "eventplanning", "find_loc": "Burbank, CA"},
    {"cflt": "venues", "find_loc": "San Fernando Valley, CA"},
    {"cflt": "venues", "find_loc": "Van Nuys, CA"},
    {"cflt": "caterers", "find_loc": "San Fernando Valley, CA"},
    {"cflt": "caterers", "find_loc": "Burbank, CA"},
    {"cflt": "partysupplies", "find_loc": "San Fernando Valley, CA"},
    {"cflt": "djs", "find_loc": "San Fernando Valley, CA"},
    {"cflt": "photographers", "find_loc": "San Fernando Valley, CA"},
    {"find_desc": "quinceañera", "find_loc": "San Fernando Valley, CA"},
    {"find_desc": "quinceañera", "find_loc": "Van Nuys, CA"},
    {"find_desc": "wedding venue", "find_loc": "Calabasas, CA"},
    {"find_desc": "tent rental", "find_loc": "San Fernando Valley, CA"},
]

YELP_CATEGORY_MAP = {
    "event planning & services": "event_planner",
    "event planning": "event_planner",
    "wedding planning": "wedding_planner",
    "venues & event spaces": "event_venue",
    "venues": "event_venue",
    "caterers": "catering",
    "catering": "catering",
    "party supplies": "party_rental",
    "party & event planning": "event_planner",
    "party equipment rentals": "party_rental",
    "djs": "dj",
    "disc jockey": "dj",
    "photographers": "photographer",
    "photography": "photographer",
    "videographers": "videography",
    "florists": "florist",
    "tent rentals": "tent_rental",
    "wedding venues": "wedding_venue",
    "banquet halls": "banquet_hall",
}


def _map_yelp_category(raw_cats, query_params):
    """Map Yelp categories to our category system."""
    if raw_cats:
        for cat in raw_cats:
            cl = cat.lower().strip()
            for key, val in YELP_CATEGORY_MAP.items():
                if key in cl:
                    return val

    # Fallback: infer from query
    cflt = query_params.get("cflt", "")
    desc = query_params.get("find_desc", "")
    combined = (cflt + " " + desc).lower()
    for key, val in YELP_CATEGORY_MAP.items():
        if key in combined:
            return val
    return "event_planner"


def _parse_city_from_address(address):
    """Extract city from Yelp address like 'Van Nuys, CA 91405'."""
    if not address:
        return ""
    parts = [p.strip() for p in address.split(",")]
    if len(parts) >= 2:
        # Last part before state is usually the city
        for part in parts:
            part = part.strip()
            # Skip parts that look like state + zip
            if re.match(r'^[A-Z]{2}\s*\d{5}', part):
                continue
            if re.match(r'^\d', part):
                continue
            return part
    return parts[0] if parts else ""


def scrape_yelp(headless=True, max_queries=None):
    """Run all Yelp search queries and save vendors."""
    from playwright.sync_api import sync_playwright
    from playwright_stealth import Stealth

    queries = YELP_SEARCHES[:max_queries] if max_queries else YELP_SEARCHES
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

        for qi, qparams in enumerate(queries):
            log.info("Query %d/%d: %s", qi + 1, len(queries), qparams)
            try:
                results = _scrape_yelp_results(page, qparams)
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

            time.sleep(random.uniform(5, 8))

        browser.close()

    log.info("DONE: %d saved, %d skipped, %d rejected", total_saved, total_skipped, total_rejected)
    return total_saved


def _scrape_yelp_results(page, qparams):
    """Scrape a single Yelp search results page."""
    vendors = []

    # Build URL
    params = []
    if "cflt" in qparams:
        params.append("cflt=%s" % qparams["cflt"])
    if "find_desc" in qparams:
        params.append("find_desc=%s" % qparams["find_desc"].replace(" ", "+"))
    params.append("find_loc=%s" % qparams["find_loc"].replace(" ", "+").replace(",", "%2C"))
    url = "https://www.yelp.com/search?%s" % "&".join(params)

    page.goto(url, wait_until="domcontentloaded")
    time.sleep(random.uniform(3, 5))

    # Check for CAPTCHA or block
    if "unusual traffic" in page.content().lower():
        log.warning("  Yelp CAPTCHA detected, skipping query")
        return vendors

    # Scrape up to 2 pages of results
    for page_num in range(2):
        if page_num > 0:
            # Try to click "Next" button
            try:
                next_btn = page.locator('a[aria-label="Next"]').first
                if next_btn.count() > 0:
                    next_btn.click()
                    time.sleep(random.uniform(3, 5))
                else:
                    break
            except Exception:
                break

        # Extract business cards from the search results
        # Yelp uses various container structures — try multiple selectors
        cards = page.locator('[data-testid="serp-ia-card"]').all()
        if not cards:
            cards = page.locator('div[class*="container"] h3 a').all()

        # Alternative: extract from the structured data
        page_vendors = _extract_from_page(page, qparams)
        vendors.extend(page_vendors)

    return vendors


def _extract_from_page(page, qparams):
    """Extract vendor data from a Yelp search results page using multiple strategies."""
    vendors = []

    try:
        # Strategy 1: Use structured data (JSON-LD) if available
        ld_scripts = page.locator('script[type="application/ld+json"]').all()
        for script in ld_scripts:
            try:
                import json
                data = json.loads(script.text_content())
                if isinstance(data, dict) and data.get("@type") == "ItemList":
                    for item in data.get("itemListElement", []):
                        biz = item.get("item", {})
                        if biz.get("name"):
                            vendor = {
                                "name": biz["name"],
                                "source": "yelp",
                                "vetting_score": 60,
                                "category": _map_yelp_category(
                                    [biz.get("@type", "")], qparams
                                ),
                            }
                            addr = biz.get("address", {})
                            if addr:
                                vendor["address"] = addr.get("streetAddress", "")
                                vendor["city"] = addr.get("addressLocality", "")
                            if biz.get("telephone"):
                                vendor["phone"] = biz["telephone"]
                            if biz.get("url"):
                                vendor["notes"] = "yelp_url: %s" % biz["url"]
                            agg = biz.get("aggregateRating", {})
                            if agg:
                                vendor["rating"] = float(agg.get("ratingValue", 0))
                                vendor["review_count"] = int(agg.get("reviewCount", 0))
                            vendors.append(vendor)
            except Exception:
                continue

        if vendors:
            return vendors

        # Strategy 2: Parse the DOM directly
        # Look for business name links within search results
        biz_links = page.locator('h3 a[href*="/biz/"]').all()
        for link in biz_links:
            try:
                name = link.text_content(timeout=2000).strip()
                href = link.get_attribute("href", timeout=1000)
                if not name or not href:
                    continue

                # Clean name — remove numbering like "1. Business Name"
                name = re.sub(r'^\d+\.\s*', '', name).strip()

                vendor = {
                    "name": name,
                    "source": "yelp",
                    "vetting_score": 60,
                    "category": _map_yelp_category([], qparams),
                }

                # Try to find the parent card and extract more info
                card = link.locator("xpath=ancestor::div[contains(@class, 'container')]").first
                if card.count() > 0:
                    card_text = card.text_content(timeout=2000)
                    # Extract rating from text
                    rating_match = re.search(r'([\d.]+)\s*star', card_text.lower())
                    if rating_match:
                        vendor["rating"] = float(rating_match.group(1))
                    # Extract review count
                    review_match = re.search(r'(\d+)\s*review', card_text.lower())
                    if review_match:
                        vendor["review_count"] = int(review_match.group(1))
                    # Extract categories
                    cat_matches = re.findall(r'(?:Event Planning|Venues|Caterers|DJs|Photographers|Party)', card_text)
                    if cat_matches:
                        vendor["category"] = _map_yelp_category(cat_matches, qparams)

                if href.startswith("/"):
                    vendor["notes"] = "yelp_url: https://www.yelp.com%s" % href
                else:
                    vendor["notes"] = "yelp_url: %s" % href

                vendors.append(vendor)
            except Exception:
                continue

    except Exception as e:
        log.debug("  Page extraction failed: %s", e)

    return vendors


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Scrape Yelp for SFV event vendors")
    parser.add_argument("--no-headless", action="store_true")
    parser.add_argument("--max-queries", type=int, default=None)
    args = parser.parse_args()

    count = scrape_yelp(headless=not args.no_headless, max_queries=args.max_queries)
    print("Yelp scraper complete: %d vendors saved" % count)
