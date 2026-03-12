"""
Vendor Referral Partner Research Engine — Discovers event vendors via Google Maps,
Google Search, and Yelp for the San Fernando Valley and Greater LA area.

Read-only research: no posting, no messaging, no account creation.
All HTTP requests are rate-limited (1-2 seconds between calls).
Results are deduplicated by name+phone before returning.

Usage:
    researcher = VendorResearcher()
    results = await researcher.search_google_maps("wedding venues", "San Fernando Valley, CA")
    results = await researcher.search_google("event planners near San Fernando Valley")
    results = await researcher.search_yelp("caterers", "San Fernando, CA")
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("vendor_research")

# Try httpx first, fall back to urllib
try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError

CONFIG_PATH = Path.home() / ".nexus" / "config.json"

# Default search locations — SFV + approved service area only
DEFAULT_LOCATIONS = [
    # San Fernando Valley (home base)
    "San Fernando, CA",
    "Van Nuys, CA",
    "Northridge, CA",
    "Encino, CA",
    "Woodland Hills, CA",
    "Sherman Oaks, CA",
    "Tarzana, CA",
    "Reseda, CA",
    "Panorama City, CA",
    "North Hollywood, CA",
    "Chatsworth, CA",
    "Granada Hills, CA",
    "Sylmar, CA",
    "Pacoima, CA",
    "Sun Valley, CA",
    "Canoga Park, CA",
    "Porter Ranch, CA",
    "Studio City, CA",
    # Nearby approved cities
    "Burbank, CA",
    "Glendale, CA",
    "Pasadena, CA",
    "Calabasas, CA",
    "Thousand Oaks, CA",
    "Simi Valley, CA",
    "Agoura Hills, CA",
    # Santa Clarita
    "Santa Clarita, CA",
    "Valencia, CA",
    "Newhall, CA",
    "Canyon Country, CA",
]

# Category to search query mappings
CATEGORY_QUERIES = {
    "wedding_venue": [
        "outdoor wedding venues",
        "garden wedding venues",
        "estate wedding venues",
        "ranch wedding venues",
    ],
    "wedding_planner": [
        "wedding planners",
        "wedding coordinators",
        "bridal event planners",
    ],
    "event_planner": [
        "event planners",
        "corporate event planners",
        "party planners",
    ],
    "quinceanera_venue": [
        "quinceanera venues",
        "quinceanera halls",
        "banquet halls quinceanera",
    ],
    "quinceanera_planner": [
        "quinceanera planners",
        "quinceanera event coordinators",
    ],
    "catering": [
        "catering companies events",
        "wedding catering",
        "event catering services",
    ],
    "party_rental": [
        "party rental companies",
        "tent rental events",
        "table chair rental events",
        "event equipment rental",
    ],
    "dj_entertainment": [
        "DJ services events",
        "wedding DJ",
        "entertainment companies events",
    ],
    "photography": [
        "wedding photographers",
        "event photography studios",
        "videography wedding events",
    ],
    "florist": [
        "event florists",
        "wedding florists",
        "floral design events",
    ],
    "bartending_mobile_bar": [
        "mobile bar services",
        "bartending services events",
        "mobile bartending weddings",
    ],
    "construction": [
        "construction companies site facilities",
        "general contractors portable restrooms",
        "commercial construction companies",
    ],
    "festival_organizer": [
        "festival organizers",
        "fair event organizers",
        "outdoor festival production",
    ],
}

# Rate limit tracker
_last_request_time = 0.0
_MIN_DELAY = 1.5  # seconds between requests


def _load_config() -> Dict[str, Any]:
    """Load config from ~/.nexus/config.json."""
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass
    return {}


def _rate_limit():
    """Enforce minimum delay between requests."""
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < _MIN_DELAY:
        time.sleep(_MIN_DELAY - elapsed)
    _last_request_time = time.time()


async def _async_rate_limit():
    """Async version of rate limiter."""
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < _MIN_DELAY:
        await asyncio.sleep(_MIN_DELAY - elapsed)
    _last_request_time = time.time()


def _normalize_phone(phone: str) -> str:
    """Normalize phone to digits only for dedup."""
    if not phone:
        return ""
    digits = re.sub(r"[^\d]", "", phone)
    # US numbers: strip leading 1 if 11 digits
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def _extract_email_from_text(text: str) -> str:
    """Extract an email address from text."""
    if not text:
        return ""
    match = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)
    return match.group(0) if match else ""


def _extract_phone_from_text(text: str) -> str:
    """Extract a phone number from text."""
    if not text:
        return ""
    match = re.search(
        r"(?:\+?1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}", text
    )
    return match.group(0) if match else ""


def _dedup_results(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate results by name+phone, keeping the record with most data."""
    seen = {}  # type: Dict[str, Dict[str, Any]]
    for r in results:
        name = (r.get("name") or "").strip().lower()
        phone = _normalize_phone(r.get("phone", ""))
        key = name + "|" + phone

        if key in seen:
            # Merge: keep whichever has more fields filled
            existing = seen[key]
            for field in ("phone", "email", "website", "address", "rating", "review_count"):
                new_val = r.get(field)
                old_val = existing.get(field)
                if new_val and not old_val:
                    existing[field] = new_val
                elif field in ("rating", "review_count") and new_val and old_val:
                    if float(new_val) > float(old_val):
                        existing[field] = new_val
        else:
            seen[key] = r.copy()

    return list(seen.values())


class VendorResearcher:
    """Research engine for discovering event vendor referral partners."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or _load_config()
        self._google_api_key = self.config.get("google_places_api_key", "")
        self._results_cache = {}  # type: Dict[str, List[Dict]]
        self._total_found = 0

    @property
    def total_found(self) -> int:
        return self._total_found

    # ── Google Maps Places API ──────────────────────────────────────────────

    async def search_google_maps(
        self,
        query: str,
        location: str = "San Fernando Valley, CA",
        radius_meters: int = 40000,
    ) -> List[Dict[str, Any]]:
        """Search Google Maps Places API (Text Search) for vendors.

        Uses the Places API text search endpoint. Falls back to
        Places nearbysearch if text search isn't available.

        Returns list of vendor dicts with standardized fields.
        """
        results = []

        if self._google_api_key:
            results = await self._search_places_api(query, location, radius_meters)
        else:
            # Fallback: scrape Google Maps search results page
            results = await self._search_google_maps_scrape(query, location)

        self._total_found += len(results)
        return results

    async def _search_places_api(
        self, query: str, location: str, radius_meters: int
    ) -> List[Dict[str, Any]]:
        """Use Google Places API text search."""
        full_query = "%s near %s" % (query, location)
        encoded = urllib.parse.urlencode({
            "query": full_query,
            "key": self._google_api_key,
            "type": "establishment",
        })
        url = "https://maps.googleapis.com/maps/api/place/textsearch/json?%s" % encoded

        await _async_rate_limit()
        data = await self._http_get_json(url)
        if not data or data.get("status") != "OK":
            log.warning("Google Places API error for '%s': %s", query, data.get("status", "unknown"))
            return []

        results = []
        for place in data.get("results", []):
            vendor = {
                "name": place.get("name", ""),
                "address": place.get("formatted_address", ""),
                "city": self._extract_city(place.get("formatted_address", "")),
                "rating": place.get("rating", 0),
                "review_count": place.get("user_ratings_total", 0),
                "phone": "",
                "email": "",
                "website": "",
                "category": "",
                "source": "google_maps",
            }

            # Get detailed info (phone, website) via Place Details
            place_id = place.get("place_id")
            if place_id:
                details = await self._get_place_details(place_id)
                if details:
                    vendor["phone"] = details.get("formatted_phone_number", "")
                    vendor["website"] = details.get("website", "")

            results.append(vendor)

        log.info("Google Maps API: found %d results for '%s'", len(results), query)
        return results

    async def _get_place_details(self, place_id: str) -> Optional[Dict[str, Any]]:
        """Fetch place details (phone, website) from Google Places API."""
        if not self._google_api_key:
            return None

        encoded = urllib.parse.urlencode({
            "place_id": place_id,
            "fields": "formatted_phone_number,website,name",
            "key": self._google_api_key,
        })
        url = "https://maps.googleapis.com/maps/api/place/details/json?%s" % encoded

        await _async_rate_limit()
        data = await self._http_get_json(url)
        if data and data.get("status") == "OK":
            return data.get("result", {})
        return None

    async def _search_google_maps_scrape(
        self, query: str, location: str
    ) -> List[Dict[str, Any]]:
        """Fallback: Search Google for Maps listings when no API key.

        Parses Google search results for local business information.
        """
        full_query = "%s near %s" % (query, location)
        encoded = urllib.parse.urlencode({"q": full_query})
        url = "https://www.google.com/search?%s" % encoded

        await _async_rate_limit()
        html = await self._http_get_text(url)
        if not html:
            return []

        results = self._parse_google_local_results(html, "google_maps")
        log.info("Google Maps scrape: found %d results for '%s'", len(results), query)
        return results

    # ── Google Search ───────────────────────────────────────────────────────

    async def search_google(self, query: str) -> List[Dict[str, Any]]:
        """Search Google for vendor listings.

        Parses organic results for business information.
        Returns list of vendor dicts.
        """
        encoded = urllib.parse.urlencode({"q": query})
        url = "https://www.google.com/search?%s" % encoded

        await _async_rate_limit()
        html = await self._http_get_text(url)
        if not html:
            return []

        results = self._parse_google_results(html)
        self._total_found += len(results)
        log.info("Google search: found %d results for '%s'", len(results), query)
        return results

    # ── Yelp Search ─────────────────────────────────────────────────────────

    async def search_yelp(
        self,
        category: str,
        location: str = "San Fernando, CA",
    ) -> List[Dict[str, Any]]:
        """Search Yelp for vendors.

        Uses Yelp Fusion API if key available, otherwise scrapes search page.
        """
        yelp_key = self.config.get("yelp_api_key", "")

        if yelp_key:
            results = await self._search_yelp_api(category, location, yelp_key)
        else:
            results = await self._search_yelp_scrape(category, location)

        self._total_found += len(results)
        return results

    async def _search_yelp_api(
        self, query: str, location: str, api_key: str
    ) -> List[Dict[str, Any]]:
        """Use Yelp Fusion API for business search."""
        encoded = urllib.parse.urlencode({
            "term": query,
            "location": location,
            "limit": 50,
            "sort_by": "rating",
        })
        url = "https://api.yelp.com/v3/businesses/search?%s" % encoded
        headers = {"Authorization": "Bearer %s" % api_key}

        await _async_rate_limit()
        data = await self._http_get_json(url, headers=headers)
        if not data or "businesses" not in data:
            log.warning("Yelp API error for '%s': %s", query, data)
            return []

        results = []
        for biz in data.get("businesses", []):
            loc = biz.get("location", {})
            results.append({
                "name": biz.get("name", ""),
                "phone": biz.get("display_phone", ""),
                "email": "",
                "website": biz.get("url", ""),
                "address": ", ".join(filter(None, loc.get("display_address", []))),
                "city": loc.get("city", ""),
                "category": "",
                "source": "yelp",
                "rating": biz.get("rating", 0),
                "review_count": biz.get("review_count", 0),
            })

        log.info("Yelp API: found %d results for '%s'", len(results), query)
        return results

    async def _search_yelp_scrape(
        self, category: str, location: str
    ) -> List[Dict[str, Any]]:
        """Fallback: scrape Yelp search results page."""
        encoded = urllib.parse.urlencode({
            "find_desc": category,
            "find_loc": location,
        })
        url = "https://www.yelp.com/search?%s" % encoded

        await _async_rate_limit()
        html = await self._http_get_text(url)
        if not html:
            return []

        results = self._parse_yelp_results(html)
        log.info("Yelp scrape: found %d results for '%s'", len(results), category)
        return results

    # ── Combined Search ─────────────────────────────────────────────────────

    async def search_category(
        self,
        category: str,
        locations: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Run all search methods for a category across locations.

        This is the main entry point for comprehensive vendor research.
        Uses AI research as primary source, supplements with web scraping.
        Deduplicates results across all sources.
        """
        if locations is None:
            locations = DEFAULT_LOCATIONS[:5]  # Top 5 locations

        all_results = []

        # PRIMARY: AI-powered research (most reliable, no anti-bot issues)
        # Restricted to approved SFV + nearby service area only
        ai_locations = [
            "San Fernando Valley", "Santa Clarita", "Burbank",
            "Pasadena", "Thousand Oaks", "Glendale",
            "Woodland Hills", "Simi Valley", "Calabasas",
        ]
        for loc in ai_locations:
            try:
                ai_results = await self.search_ai(category, loc)
                all_results.extend(ai_results)
            except Exception as e:
                log.error("AI research error for '%s' in '%s': %s", category, loc, e)

        # SECONDARY: Web scraping (supplement AI results)
        queries = CATEGORY_QUERIES.get(category, [category.replace("_", " ")])
        for query in queries[:2]:  # Limit queries to avoid rate limits
            for location in locations[:3]:  # Limit locations
                # Google Maps
                try:
                    maps_results = await self.search_google_maps(query, location)
                    for r in maps_results:
                        r["category"] = category
                    all_results.extend(maps_results)
                except Exception as e:
                    log.error("Google Maps error for '%s' in '%s': %s", query, location, e)

            # Google Search (top 2 locations only)
            try:
                for location in locations[:2]:
                    google_query = "%s near %s" % (query, location)
                    google_results = await self.search_google(google_query)
                    for r in google_results:
                        r["category"] = category
                    all_results.extend(google_results)
            except Exception as e:
                log.error("Google Search error for '%s': %s", query, e)

        # Deduplicate
        deduped = _dedup_results(all_results)
        log.info(
            "Category '%s': %d raw results -> %d after dedup",
            category, len(all_results), len(deduped),
        )
        return deduped

    # ── HTTP Helpers ────────────────────────────────────────────────────────

    async def _http_get_json(
        self, url: str, headers: Optional[Dict[str, str]] = None
    ) -> Optional[Dict[str, Any]]:
        """Make HTTP GET request and return parsed JSON."""
        try:
            if HAS_HTTPX:
                async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                    h = {
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                                      "Chrome/120.0.0.0 Safari/537.36",
                    }
                    if headers:
                        h.update(headers)
                    resp = await client.get(url, headers=h)
                    resp.raise_for_status()
                    return resp.json()
            else:
                req = Request(url)
                req.add_header(
                    "User-Agent",
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36",
                )
                if headers:
                    for k, v in headers.items():
                        req.add_header(k, v)
                with urlopen(req, timeout=15) as resp:
                    return json.loads(resp.read())
        except Exception as e:
            log.error("HTTP GET JSON failed for %s: %s", url[:100], e)
            return None

    async def _http_get_text(
        self, url: str, headers: Optional[Dict[str, str]] = None
    ) -> str:
        """Make HTTP GET request and return response text."""
        try:
            if HAS_HTTPX:
                async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                    h = {
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                                      "Chrome/120.0.0.0 Safari/537.36",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                    }
                    if headers:
                        h.update(headers)
                    resp = await client.get(url, headers=h)
                    resp.raise_for_status()
                    return resp.text
            else:
                req = Request(url)
                req.add_header(
                    "User-Agent",
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36",
                )
                req.add_header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
                if headers:
                    for k, v in headers.items():
                        req.add_header(k, v)
                with urlopen(req, timeout=15) as resp:
                    return resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            log.error("HTTP GET text failed for %s: %s", url[:100], e)
            return ""

    # ── HTML Parsing Helpers ────────────────────────────────────────────────

    def _parse_google_local_results(self, html: str, source: str) -> List[Dict[str, Any]]:
        """Parse Google local pack / map results from search HTML."""
        results = []

        # Look for structured local business data in the HTML
        # Google embeds JSON-LD and data attributes for local results
        # Pattern: business names in local pack divs
        name_pattern = re.compile(
            r'class="[^"]*(?:dbg0pd|OSrXXb|rllt__details)[^"]*"[^>]*>.*?'
            r'<span[^>]*>([^<]+)</span>',
            re.DOTALL,
        )
        names = name_pattern.findall(html)

        # Extract addresses
        addr_pattern = re.compile(
            r'class="[^"]*(?:rllt__wrapped|lqhpac)[^"]*"[^>]*>([^<]+)',
        )
        addresses = addr_pattern.findall(html)

        # Extract ratings
        rating_pattern = re.compile(r'(\d\.\d)\s*\(\d+\)')
        ratings = rating_pattern.findall(html)

        # Extract review counts
        review_pattern = re.compile(r'\d\.\d\s*\((\d[\d,]*)\)')
        reviews = review_pattern.findall(html)

        # Extract phone numbers from the page
        phone_pattern = re.compile(
            r'(?:\+?1[\s.-]?)?\(?(\d{3})\)?[\s.-]?(\d{3})[\s.-]?(\d{4})'
        )
        phones = phone_pattern.findall(html)

        for i, name in enumerate(names[:20]):  # Cap at 20 results
            name = name.strip()
            if not name or len(name) < 3:
                continue

            vendor = {
                "name": name,
                "phone": "",
                "email": "",
                "website": "",
                "address": addresses[i].strip() if i < len(addresses) else "",
                "city": "",
                "category": "",
                "source": source,
                "rating": float(ratings[i]) if i < len(ratings) else 0,
                "review_count": int(reviews[i].replace(",", "")) if i < len(reviews) else 0,
            }

            if i < len(phones):
                vendor["phone"] = "(%s) %s-%s" % phones[i]

            # Extract city from address
            vendor["city"] = self._extract_city(vendor["address"])

            results.append(vendor)

        return results

    def _parse_google_results(self, html: str) -> List[Dict[str, Any]]:
        """Parse organic Google search results for vendor info."""
        results = []

        # Extract result titles and URLs
        title_pattern = re.compile(
            r'<h3[^>]*>([^<]+)</h3>',
        )
        titles = title_pattern.findall(html)

        # Extract URLs from results
        url_pattern = re.compile(
            r'<a[^>]+href="(/url\?q=|)(https?://[^"&]+)',
        )
        urls = url_pattern.findall(html)

        # Extract snippets
        snippet_pattern = re.compile(
            r'class="[^"]*(?:VwiC3b|yXK7lf|MUxGbd)[^"]*"[^>]*>([^<]{20,})',
        )
        snippets = snippet_pattern.findall(html)

        for i, title in enumerate(titles[:15]):
            title = title.strip()
            if not title or len(title) < 5:
                continue

            # Skip non-business results
            skip_terms = ["map", "directions", "images", "news", "videos", "wikipedia"]
            if any(t in title.lower() for t in skip_terms):
                continue

            url = urls[i][1] if i < len(urls) else ""
            snippet = snippets[i] if i < len(snippets) else ""

            # Try to extract contact info from snippet
            phone = _extract_phone_from_text(snippet)
            email = _extract_email_from_text(snippet)

            results.append({
                "name": title,
                "phone": phone,
                "email": email,
                "website": url,
                "address": "",
                "city": "",
                "category": "",
                "source": "google_search",
                "rating": 0,
                "review_count": 0,
            })

        return results

    def _parse_yelp_results(self, html: str) -> List[Dict[str, Any]]:
        """Parse Yelp search results page for business listings."""
        results = []

        # Yelp uses JSON data in script tags for search results
        # Try to find the JSON data first
        json_pattern = re.compile(
            r'<!--\s*(\{"searchPageProps.*?\})\s*-->',
            re.DOTALL,
        )
        json_match = json_pattern.search(html)

        if json_match:
            try:
                data = json.loads(json_match.group(1))
                businesses = (
                    data.get("searchPageProps", {})
                    .get("mainContentComponentsListProps", [])
                )
                for item in businesses:
                    biz = item.get("bizId") or item.get("searchResultBusiness", {})
                    if isinstance(biz, dict) and biz.get("name"):
                        results.append({
                            "name": biz.get("name", ""),
                            "phone": biz.get("phone", ""),
                            "email": "",
                            "website": "",
                            "address": biz.get("formattedAddress", ""),
                            "city": biz.get("neighborhoods", [""])[0] if biz.get("neighborhoods") else "",
                            "category": "",
                            "source": "yelp",
                            "rating": biz.get("rating", 0),
                            "review_count": biz.get("reviewCount", 0),
                        })
            except (json.JSONDecodeError, KeyError):
                pass

        # Fallback: regex-based extraction from HTML
        if not results:
            # Business names in Yelp results
            name_pattern = re.compile(
                r'class="[^"]*css-[^"]*"[^>]*>(\d+\.\s*)?([A-Z][^<]{2,50})</a>',
            )
            for match in name_pattern.finditer(html):
                name = match.group(2).strip()
                if name and len(name) > 3:
                    results.append({
                        "name": name,
                        "phone": "",
                        "email": "",
                        "website": "",
                        "address": "",
                        "city": "",
                        "category": "",
                        "source": "yelp",
                        "rating": 0,
                        "review_count": 0,
                    })

        return results[:20]  # Cap at 20

    def _extract_city(self, address: str) -> str:
        """Extract city name from a formatted address string."""
        if not address:
            return ""
        # Pattern: "..., City, State ZIP" or "..., City, CA"
        match = re.search(r",\s*([A-Za-z\s]+),\s*CA", address)
        if match:
            return match.group(1).strip()
        # Try: "City, CA ZIP"
        match = re.search(r"([A-Za-z\s]+),\s*CA\s*\d{5}", address)
        if match:
            return match.group(1).strip()
        return ""

    # ── AI-Powered Research (Groq — Llama 4 Scout) ──────────────────────

    async def search_ai(
        self,
        category: str,
        location: str,
        model: str = "meta-llama/llama-4-scout-17b-16e-instruct",
    ) -> List[Dict[str, Any]]:
        """Use Groq AI to generate vendor lists from training knowledge.

        More reliable than scraping — bypasses anti-bot measures entirely.
        Returns structured vendor data for the given category and location.
        """
        import os
        groq_key = self.config.get("groq_api_key", "")
        if not groq_key:
            groq_key = os.environ.get("GROQ_API_KEY", "")
        if not groq_key:
            log.warning("No Groq API key — skipping AI research")
            return []

        category_label = category.replace("_", " ").title()
        prompt = (
            "List 15 real %s businesses in or near %s, California. "
            "For each business provide ONLY a JSON array with objects containing: "
            "name, phone, website, address, city. "
            "Only include businesses you are confident actually exist. "
            "Return ONLY the JSON array, no other text."
        ) % (category_label, location)

        await _async_rate_limit()

        try:
            payload = json.dumps({
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a local business directory. Return ONLY valid JSON arrays. No markdown, no explanation."},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 2000,
                "temperature": 0.3,
            }).encode()

            if HAS_HTTPX:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        content=payload,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": "Bearer %s" % groq_key,
                            "User-Agent": "Nexus/1.0",
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
            else:
                req = Request(
                    "https://api.groq.com/openai/v1/chat/completions",
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer %s" % groq_key,
                        "User-Agent": "Nexus/1.0",
                    },
                )
                with urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read())

            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not content:
                log.warning("AI research returned empty content for %s in %s", category, location)
                return []

            # Parse JSON from response (handle markdown code blocks)
            content = content.strip()
            if content.startswith("```"):
                content = re.sub(r"^```(?:json)?\s*", "", content)
                content = re.sub(r"\s*```$", "", content)

            vendors_raw = json.loads(content)
            if not isinstance(vendors_raw, list):
                log.warning("AI research returned non-list for %s", category)
                return []

            results = []
            for v in vendors_raw:
                if not isinstance(v, dict) or not v.get("name"):
                    continue
                results.append({
                    "name": v.get("name", ""),
                    "phone": v.get("phone", ""),
                    "email": v.get("email", ""),
                    "website": v.get("website", ""),
                    "address": v.get("address", ""),
                    "city": v.get("city", location.split(",")[0].strip()),
                    "category": category,
                    "source": "ai_research",
                    "rating": 0,
                    "review_count": 0,
                })

            self._total_found += len(results)
            log.info("AI research (%s): found %d vendors for '%s' in '%s'",
                     model, len(results), category, location)
            return results

        except json.JSONDecodeError as e:
            log.error("AI research JSON parse error for %s in %s: %s", category, location, e)
            return []
        except Exception as e:
            log.error("AI research failed for %s in %s: %s", category, location, e)
            return []

    # ── Utility ─────────────────────────────────────────────────────────────

    def get_all_categories(self) -> List[str]:
        """Return all supported vendor categories."""
        return list(CATEGORY_QUERIES.keys())

    def get_queries_for_category(self, category: str) -> List[str]:
        """Return search queries for a given category."""
        return CATEGORY_QUERIES.get(category, [category.replace("_", " ")])
