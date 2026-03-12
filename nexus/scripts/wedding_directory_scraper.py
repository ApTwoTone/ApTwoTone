"""
Wedding Directory Scraper — The Knot, HereComesTheGuide, Eventective.

Purpose-built wedding/event directories where every listing is a real vendor.
Uses Playwright since all sites require JS rendering.

All results go through save_vendor() for geo-filtering, vetting, and dedup.

Usage:
    python scripts/wedding_directory_scraper.py [--no-headless] [--max-queries N]
"""
import logging
import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.vendor_db import save_vendor

log = logging.getLogger("wedding_directory_scraper")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

# ── Directory URLs ───────────────────────────────────────────────────────────

DIRECTORY_PAGES = [
    # The Knot — LA market
    {
        "url": "https://www.theknot.com/marketplace/wedding-planners-los-angeles-ca",
        "source": "the_knot",
        "category": "wedding_planner",
    },
    {
        "url": "https://www.theknot.com/marketplace/wedding-venues-los-angeles-ca",
        "source": "the_knot",
        "category": "wedding_venue",
    },
    {
        "url": "https://www.theknot.com/marketplace/caterers-los-angeles-ca",
        "source": "the_knot",
        "category": "catering",
    },
    {
        "url": "https://www.theknot.com/marketplace/party-rentals-los-angeles-ca",
        "source": "the_knot",
        "category": "party_rental",
    },
    {
        "url": "https://www.theknot.com/marketplace/djs-los-angeles-ca",
        "source": "the_knot",
        "category": "dj",
    },
    {
        "url": "https://www.theknot.com/marketplace/florists-los-angeles-ca",
        "source": "the_knot",
        "category": "florist",
    },
    {
        "url": "https://www.theknot.com/marketplace/photographers-los-angeles-ca",
        "source": "the_knot",
        "category": "photographer",
    },
    # HereComesTheGuide — SFV-specific
    {
        "url": "https://www.herecomestheguide.com/wedding-venues/san-fernando-valley",
        "source": "herecomestheguide",
        "category": "wedding_venue",
    },
    # Eventective — SFV venues
    {
        "url": "https://www.eventective.com/san-fernando-ca/party-event-venues/",
        "source": "eventective",
        "category": "event_venue",
    },
    {
        "url": "https://www.eventective.com/san-fernando-ca/wedding-venues/",
        "source": "eventective",
        "category": "wedding_venue",
    },
    {
        "url": "https://www.eventective.com/granada-hills-ca/party-event-venues/",
        "source": "eventective",
        "category": "event_venue",
    },
    {
        "url": "https://www.eventective.com/van-nuys-ca/party-event-venues/",
        "source": "eventective",
        "category": "event_venue",
    },
    {
        "url": "https://www.eventective.com/north-hollywood-ca/party-event-venues/",
        "source": "eventective",
        "category": "event_venue",
    },
]


def scrape_wedding_directories(headless=True, max_queries=None):
    """Scrape all wedding directory pages and save vendors."""
    from playwright.sync_api import sync_playwright
    from playwright_stealth import Stealth

    pages_to_scrape = DIRECTORY_PAGES[:max_queries] if max_queries else DIRECTORY_PAGES
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

        for qi, dir_page in enumerate(pages_to_scrape):
            log.info("Page %d/%d: %s (%s)", qi + 1, len(pages_to_scrape),
                     dir_page["source"], dir_page["url"][:60])
            try:
                source = dir_page["source"]
                if source == "the_knot":
                    results = _scrape_the_knot(page, dir_page)
                elif source == "herecomestheguide":
                    results = _scrape_herecomestheguide(page, dir_page)
                elif source == "eventective":
                    results = _scrape_eventective(page, dir_page)
                else:
                    results = []

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
                log.error("  → Failed: %s", e)

            time.sleep(random.uniform(5, 8))

        browser.close()

    log.info("DONE: %d saved, %d skipped, %d rejected", total_saved, total_skipped, total_rejected)
    return total_saved


def _scrape_the_knot(page, dir_page):
    """Scrape vendor listings from The Knot marketplace page."""
    vendors = []
    page.goto(dir_page["url"], wait_until="domcontentloaded")
    time.sleep(random.uniform(3, 5))

    # Scroll to load more results (The Knot lazy-loads)
    for _ in range(5):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(random.uniform(1.5, 2.5))

    # The Knot uses vendor cards with business names as links
    # Try JSON-LD structured data first
    try:
        import json
        ld_scripts = page.locator('script[type="application/ld+json"]').all()
        for script in ld_scripts:
            try:
                data = json.loads(script.text_content())
                if isinstance(data, list):
                    for item in data:
                        vendor = _parse_ld_json_vendor(item, dir_page)
                        if vendor:
                            vendors.append(vendor)
                elif isinstance(data, dict):
                    if data.get("@type") == "ItemList":
                        for item in data.get("itemListElement", []):
                            vendor = _parse_ld_json_vendor(item.get("item", item), dir_page)
                            if vendor:
                                vendors.append(vendor)
                    else:
                        vendor = _parse_ld_json_vendor(data, dir_page)
                        if vendor:
                            vendors.append(vendor)
            except Exception:
                continue
    except Exception:
        pass

    if vendors:
        return vendors

    # Fallback: parse DOM for vendor cards
    vendor_links = page.locator('a[href*="/marketplace/"][class*="vendor"], a[href*="/marketplace/"][data-testid]').all()
    if not vendor_links:
        vendor_links = page.locator('a[href*="/marketplace/"]').all()

    seen_names = set()
    for link in vendor_links:
        try:
            name = link.text_content(timeout=2000).strip()
            href = link.get_attribute("href", timeout=1000) or ""
            # Filter out category/navigation links
            if not name or len(name) < 3 or len(name) > 100:
                continue
            if name.lower() in ("view more", "see all", "load more"):
                continue
            if name in seen_names:
                continue
            seen_names.add(name)

            vendor = {
                "name": name,
                "category": dir_page["category"],
                "source": "the_knot",
                "vetting_score": 60,
            }
            if href:
                full_url = href if href.startswith("http") else "https://www.theknot.com%s" % href
                vendor["notes"] = "directory_url: %s" % full_url
            vendors.append(vendor)
        except Exception:
            continue

    return vendors


def _parse_ld_json_vendor(data, dir_page):
    """Parse a JSON-LD vendor object into our format."""
    if not isinstance(data, dict):
        return None
    name = data.get("name")
    if not name:
        return None

    vendor = {
        "name": name,
        "category": dir_page["category"],
        "source": dir_page["source"],
        "vetting_score": 60,
    }

    addr = data.get("address", {})
    if isinstance(addr, dict):
        vendor["address"] = addr.get("streetAddress", "")
        vendor["city"] = addr.get("addressLocality", "")

    if data.get("telephone"):
        vendor["phone"] = data["telephone"]
    if data.get("url"):
        vendor["website"] = data["url"]

    agg = data.get("aggregateRating", {})
    if isinstance(agg, dict):
        vendor["rating"] = float(agg.get("ratingValue", 0) or 0)
        vendor["review_count"] = int(agg.get("reviewCount", 0) or 0)

    return vendor


def _scrape_herecomestheguide(page, dir_page):
    """Scrape venue listings from HereComesTheGuide."""
    vendors = []
    page.goto(dir_page["url"], wait_until="domcontentloaded")
    time.sleep(random.uniform(3, 5))

    # Scroll to load
    for _ in range(3):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1.5)

    # Venue cards typically have venue names as headings
    venue_links = page.locator('a[href*="/wedding-party-venues/"]').all()
    seen = set()

    for link in venue_links:
        try:
            name = link.text_content(timeout=2000).strip()
            href = link.get_attribute("href", timeout=1000)
            if not name or len(name) < 3 or name in seen:
                continue
            if name.lower() in ("view more", "see details", "learn more"):
                continue
            seen.add(name)

            vendor = {
                "name": name,
                "category": "wedding_venue",
                "source": "herecomestheguide",
                "vetting_score": 60,
            }
            if href:
                full_url = href if href.startswith("http") else "https://www.herecomestheguide.com%s" % href
                vendor["notes"] = "directory_url: %s" % full_url
            vendors.append(vendor)
        except Exception:
            continue

    return vendors


def _scrape_eventective(page, dir_page):
    """Scrape venue listings from Eventective."""
    vendors = []
    page.goto(dir_page["url"], wait_until="domcontentloaded")
    time.sleep(random.uniform(3, 5))

    # Eventective has venue cards with business name links
    venue_links = page.locator('a[href*="/listing/"]').all()
    if not venue_links:
        venue_links = page.locator('h3 a, h2 a').all()

    seen = set()
    for link in venue_links:
        try:
            name = link.text_content(timeout=2000).strip()
            href = link.get_attribute("href", timeout=1000)
            if not name or len(name) < 3 or name in seen:
                continue
            seen.add(name)

            vendor = {
                "name": name,
                "category": dir_page["category"],
                "source": "eventective",
                "vetting_score": 60,
                "city": dir_page["url"].split("/")[-2].replace("-", " ").title().split(" Ca")[0],
            }
            if href:
                full_url = href if href.startswith("http") else "https://www.eventective.com%s" % href
                vendor["notes"] = "directory_url: %s" % full_url
            vendors.append(vendor)
        except Exception:
            continue

    return vendors


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Scrape wedding directories for SFV vendors")
    parser.add_argument("--no-headless", action="store_true")
    parser.add_argument("--max-queries", type=int, default=None)
    args = parser.parse_args()

    count = scrape_wedding_directories(headless=not args.no_headless, max_queries=args.max_queries)
    print("Wedding directory scraper complete: %d vendors saved" % count)
