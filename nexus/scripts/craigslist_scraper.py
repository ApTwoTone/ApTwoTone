"""
Craigslist Vendor Scraper — Finds actively advertising event vendors.

Scrapes LA Craigslist event services, creative services, and event gigs
for vendors posting in the last 21 days. Uses Groq AI to classify listings.

All results go through save_vendor() for geo-filtering, vetting, and dedup.

Usage:
    python scripts/craigslist_scraper.py
"""
import json
import logging
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.vendor_db import save_vendor

log = logging.getLogger("craigslist_scraper")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

# ── Craigslist URLs ──────────────────────────────────────────────────────────

CRAIGSLIST_URLS = [
    # SFV Event Services — primary market
    "https://losangeles.craigslist.org/search/sfv/evs",
    # All LA Event Services
    "https://losangeles.craigslist.org/search/evs",
    # Westside Event Services
    "https://losangeles.craigslist.org/search/wst/evs",
    # SFV Creative Services (photographers, videographers)
    "https://losangeles.craigslist.org/search/sfv/crs",
    # SFV Event Gigs
    "https://losangeles.craigslist.org/search/sfv/evg",
    # All LA Event Gigs
    "https://losangeles.craigslist.org/search/evg",
    # Keyword-filtered searches
    "https://losangeles.craigslist.org/search/sfv/evs?query=wedding",
    "https://losangeles.craigslist.org/search/sfv/evs?query=party",
    "https://losangeles.craigslist.org/search/sfv/evs?query=tent",
    "https://losangeles.craigslist.org/search/sfv/evs?query=catering",
    "https://losangeles.craigslist.org/search/sfv/evs?query=DJ",
    "https://losangeles.craigslist.org/search/sfv/evs?query=event+rental",
    "https://losangeles.craigslist.org/search/sfv/evs?query=venue",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

PHONE_RE = re.compile(r'[\(]?\d{3}[\)]?[-.\s]?\d{3}[-.\s]?\d{4}')
EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
URL_RE = re.compile(r'https?://[^\s<>"\']+|www\.[^\s<>"\']+')

# 21 days ago cutoff
CUTOFF_DATE = datetime.now(timezone.utc) - timedelta(days=21)

# ── Category inference from listing text ─────────────────────────────────────

KEYWORD_TO_CATEGORY = {
    "wedding planner": "wedding_planner",
    "event planner": "event_planner",
    "event coordinator": "event_planner",
    "quinceañera": "quinceanera_planner",
    "quinceanera": "quinceanera_planner",
    "tent rental": "tent_rental",
    "party rental": "party_rental",
    "table rental": "party_rental",
    "chair rental": "party_rental",
    "bounce house": "party_rental",
    "photo booth": "party_rental",
    "catering": "catering",
    "caterer": "catering",
    "photographer": "photographer",
    "photography": "photographer",
    "videograph": "videography",
    "florist": "florist",
    "flower": "florist",
    "dj service": "dj",
    "disc jockey": "dj",
    "mobile dj": "dj",
    "wedding dj": "dj",
    "event dj": "dj",
    "venue": "event_venue",
    "banquet": "banquet_hall",
    "wedding venue": "wedding_venue",
    "lighting": "lighting",
    "decorator": "event_decorator",
    "balloon": "balloon_artist",
    "bartend": "bartending",
    "mobile bar": "bartending",
    "officiant": "officiant",
}


def _classify_listing(title, body):
    """Classify a Craigslist listing by keyword matching. Returns category or None."""
    text = ("%s %s" % (title or "", body or "")).lower()
    for keyword, category in KEYWORD_TO_CATEGORY.items():
        if keyword in text:
            return category
    return None


def _extract_business_name(title, body):
    """Try to extract a business name from listing text."""
    # Common patterns: "Company Name - Services", "Company Name | ...", business name in first line
    if not title:
        return None
    # Remove common suffixes
    name = re.sub(r'\s*[-|/].*$', '', title).strip()
    # Remove pricing
    name = re.sub(r'\$[\d,]+.*$', '', name).strip()
    # If remaining is just generic like "DJ for hire", use as-is
    if len(name) > 3 and len(name) < 80:
        return name
    return None


def _extract_city_from_location(location_text):
    """Parse Craigslist location text to extract city."""
    if not location_text:
        return ""
    # Craigslist locations look like "(Van Nuys)" or "(North Hollywood / Burbank)"
    loc = location_text.strip("() \t\n")
    # Take first city if multiple
    loc = loc.split("/")[0].strip()
    return loc


def scrape_craigslist():
    """Scrape all Craigslist URLs and save vendors."""
    total_saved = 0
    total_skipped = 0
    total_rejected = 0
    seen_urls = set()

    for url_i, base_url in enumerate(CRAIGSLIST_URLS):
        log.info("Source %d/%d: %s", url_i + 1, len(CRAIGSLIST_URLS), base_url)

        listings = _fetch_listing_page(base_url)
        log.info("  Found %d listings", len(listings))

        for listing in listings:
            if listing["url"] in seen_urls:
                continue
            seen_urls.add(listing["url"])

            # Check date cutoff
            if listing.get("post_date"):
                try:
                    post_dt = datetime.fromisoformat(listing["post_date"].replace("Z", "+00:00"))
                    if post_dt < CUTOFF_DATE:
                        continue
                except (ValueError, TypeError):
                    pass

            # Fetch full listing details
            time.sleep(random.uniform(3, 5))
            detail = _fetch_listing_detail(listing["url"])
            if not detail:
                continue

            # Classify
            category = _classify_listing(listing.get("title"), detail.get("body"))
            if not category:
                continue  # Not an event vendor

            business_name = _extract_business_name(listing.get("title"), detail.get("body"))
            if not business_name:
                continue

            city = _extract_city_from_location(listing.get("location"))

            # Build vendor record
            vendor = {
                "name": business_name,
                "category": category,
                "city": city,
                "source": "craigslist",
                "vetting_score": 50,
                "notes": "craigslist_url: %s | post_date: %s" % (
                    listing["url"], listing.get("post_date", "unknown")
                ),
            }

            # Add contact info from detail page
            if detail.get("phones"):
                vendor["phone"] = detail["phones"][0]
            if detail.get("emails"):
                vendor["email"] = detail["emails"][0]
            if detail.get("websites"):
                vendor["website"] = detail["websites"][0]

            result = save_vendor(vendor)
            action = result.get("action", "error")
            if action == "created":
                total_saved += 1
            elif action in ("updated", "skipped"):
                total_skipped += 1
            else:
                total_rejected += 1

        time.sleep(random.uniform(3, 5))

    log.info("DONE: %d saved, %d skipped, %d rejected from %d URLs",
             total_saved, total_skipped, total_rejected, len(CRAIGSLIST_URLS))
    return total_saved


def _fetch_listing_page(url):
    """Fetch a Craigslist search results page and extract listing cards."""
    listings = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            log.warning("  HTTP %d for %s", resp.status_code, url)
            return listings

        soup = BeautifulSoup(resp.text, "html.parser")

        # Craigslist 2026 uses <li class="cl-static-search-result">
        results = soup.find_all("li", class_="cl-static-search-result")
        if not results:
            results = soup.find_all("li", class_="cl-search-result")
        if not results:
            results = soup.find_all("div", class_="result-info")

        for result in results:
            listing = {}

            # Title and URL — structure: li > a[href] > div.title
            link = result.find("a", href=True)
            if link:
                title_div = link.find("div", class_="title")
                listing["title"] = title_div.get_text(strip=True) if title_div else link.get_text(strip=True)
                href = link.get("href", "")
                if href.startswith("/"):
                    listing["url"] = "https://losangeles.craigslist.org" + href
                else:
                    listing["url"] = href
            else:
                continue

            # Post date — may be in a time element or from the title attribute
            time_el = result.find("time")
            if time_el:
                listing["post_date"] = time_el.get("datetime", "")

            # Location — div.location inside div.details
            loc_el = result.find("div", class_="location")
            if not loc_el:
                loc_el = result.find("span", class_="nearby") or result.find("div", class_="meta")
            if loc_el:
                listing["location"] = loc_el.get_text(strip=True)

            if listing.get("url"):
                listings.append(listing)

    except Exception as e:
        log.error("  Failed to fetch %s: %s", url, e)

    return listings


def _fetch_listing_detail(url):
    """Fetch a full Craigslist listing page and extract contact info."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        # Post body
        body_el = soup.find("section", id="postingbody")
        body_text = ""
        if body_el:
            # Remove the "QR Code Link" notice that CL adds
            for notice in body_el.find_all("div", class_="print-information"):
                notice.decompose()
            body_text = body_el.get_text(strip=True)

        # Extract contact info from body
        phones = PHONE_RE.findall(body_text)
        emails = EMAIL_RE.findall(body_text)
        websites = URL_RE.findall(body_text)

        # Filter out Craigslist URLs from websites
        websites = [w for w in websites if "craigslist.org" not in w and "google.com" not in w]

        return {
            "body": body_text[:500],
            "phones": phones[:1],
            "emails": emails[:1],
            "websites": websites[:1],
        }

    except Exception as e:
        log.debug("  Detail fetch failed for %s: %s", url, e)
        return None


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    count = scrape_craigslist()
    print("Craigslist scraper complete: %d vendors saved" % count)
