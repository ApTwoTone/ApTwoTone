"""
Vendor Research Daemon — Runs 24/7 to continuously discover vendor partners.

ALL AGENTS READ-ONLY: Zero interaction with platform content. (CLAUDE.md Rule 24)

Targets:
  - 15,000 vendor records within 30 days (CLAUDE.md Rule 27)
  - 30,000 vendor records within 60 days
  - Minimum 500 new vendors per night

Geographic Zones (8 total):
  1. San Fernando Valley (home base)
  2. Santa Clarita
  3. Conejo Valley
  4. San Gabriel Valley
  5. LA Proper
  6. West LA / Beach Cities
  7. South Bay
  8. Antelope Valley

Category Rotation:
  50+ vendor categories covering events, construction, film, and hospitality.

Usage:
    python -m core.vendor_research_daemon      # Run directly
    # Or via launchd for persistent 24/7 operation
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("vendor_research_daemon")

DB_PATH = Path.home() / ".nexus" / "memory.db"
STATE_PATH = Path.home() / ".nexus" / "research_daemon_state.json"

# ── Geographic Zones ──────────────────────────────────────────────────────────

# Geographic zones restricted to approved SFV + nearby service area only.
# Zones outside the whitelist (West LA, South Bay, Antelope Valley, San Gabriel)
# have been removed to prevent garbage data accumulation.
GEOGRAPHIC_ZONES = {
    "sfv_core": {
        "name": "SFV Core",
        "priority": 1,
        "locations": [
            "San Fernando, CA", "Pacoima, CA", "Sylmar, CA",
            "Van Nuys, CA", "North Hollywood, CA", "Panorama City, CA",
            "Sun Valley, CA", "Arleta, CA", "Granada Hills, CA",
            "Northridge, CA", "Reseda, CA",
        ],
    },
    "sfv_west": {
        "name": "SFV West",
        "priority": 1,
        "locations": [
            "Chatsworth, CA", "Canoga Park, CA", "Woodland Hills, CA",
            "West Hills, CA", "Winnetka, CA", "Tarzana, CA",
            "Encino, CA", "Porter Ranch, CA",
        ],
    },
    "sfv_south": {
        "name": "SFV South",
        "priority": 2,
        "locations": [
            "Sherman Oaks, CA", "Studio City, CA", "Sunland, CA",
            "Tujunga, CA",
        ],
    },
    "santa_clarita": {
        "name": "Santa Clarita",
        "priority": 2,
        "locations": [
            "Santa Clarita, CA", "Valencia, CA", "Newhall, CA",
            "Canyon Country, CA",
        ],
    },
    "nearby_cities": {
        "name": "Nearby Cities",
        "priority": 3,
        "locations": [
            "Burbank, CA", "Glendale, CA", "Pasadena, CA",
            "Calabasas, CA", "Hidden Hills, CA",
        ],
    },
    "conejo_simi": {
        "name": "Conejo / Simi",
        "priority": 3,
        "locations": [
            "Thousand Oaks, CA", "Agoura Hills, CA",
            "Simi Valley, CA",
        ],
    },
}

# ── Extended Category List (50+) ──────────────────────────────────────────────

RESEARCH_CATEGORIES = {
    # Wedding (highest priority — primary event type)
    "wedding_venue": {"priority": 1, "queries": ["outdoor wedding venues", "garden wedding venues", "estate wedding venues", "ranch wedding venues", "vineyard wedding venues"]},
    "wedding_planner": {"priority": 1, "queries": ["wedding planners", "wedding coordinators", "day-of wedding coordinators", "bridal consultants"]},
    "bridal_shop": {"priority": 2, "queries": ["bridal shops", "wedding dress boutiques", "bridesmaid dress shops"]},
    "wedding_cake": {"priority": 2, "queries": ["wedding cake bakeries", "custom wedding cakes", "specialty cake designers"]},
    "wedding_invitation": {"priority": 3, "queries": ["wedding invitation designers", "custom invitation printers", "stationery designers"]},

    # Quinceañera (second priority)
    "quinceanera_venue": {"priority": 1, "queries": ["quinceanera venues", "quinceanera halls", "banquet halls quinceanera", "reception halls"]},
    "quinceanera_planner": {"priority": 1, "queries": ["quinceanera planners", "quinceanera coordinators", "fiesta planners"]},
    "quinceanera_dress": {"priority": 2, "queries": ["quinceanera dress shops", "vestidos de quinceanera", "formal dress boutiques"]},

    # Corporate
    "corporate_event_venue": {"priority": 2, "queries": ["corporate event venues", "conference venues", "business meeting spaces"]},
    "corporate_event_planner": {"priority": 2, "queries": ["corporate event planners", "business event coordinators", "conference planners"]},
    "team_building": {"priority": 3, "queries": ["team building event companies", "corporate retreat planners"]},

    # General Event Services
    "event_planner": {"priority": 1, "queries": ["event planners", "event coordinators", "party planners", "celebration planners"]},
    "catering": {"priority": 1, "queries": ["catering companies", "wedding catering", "event catering", "taco catering", "BBQ catering"]},
    "party_rental": {"priority": 1, "queries": ["party rental companies", "event equipment rental", "table chair rental", "linen rental"]},
    "tent_rental": {"priority": 1, "queries": ["tent rental companies", "event tent rental", "canopy rental", "outdoor event tents"]},
    "bounce_house": {"priority": 2, "queries": ["bounce house rental", "inflatable rental", "kids party rental"]},
    "dj_entertainment": {"priority": 1, "queries": ["DJ services events", "wedding DJ", "party DJ", "mobile DJ services"]},
    "live_band": {"priority": 2, "queries": ["live bands for events", "wedding bands", "cover bands for hire"]},
    "photography": {"priority": 1, "queries": ["wedding photographers", "event photographers", "portrait photographers"]},
    "videography": {"priority": 2, "queries": ["wedding videographers", "event videography", "cinematic wedding films"]},
    "florist": {"priority": 1, "queries": ["wedding florists", "event florists", "floral designers", "flower shops events"]},
    "bartending_mobile_bar": {"priority": 1, "queries": ["mobile bar services", "bartending services events", "cocktail catering"]},
    "photo_booth": {"priority": 2, "queries": ["photo booth rental", "event photo booth", "360 photo booth"]},

    # Decorating & Design
    "event_decorator": {"priority": 2, "queries": ["event decorators", "party decorators", "balloon decorators"]},
    "balloon_artist": {"priority": 3, "queries": ["balloon artists", "balloon arches", "balloon garlands events"]},
    "lighting_design": {"priority": 2, "queries": ["event lighting companies", "uplighting rental", "string light rental"]},
    "furniture_rental": {"priority": 2, "queries": ["event furniture rental", "lounge furniture rental", "specialty chair rental"]},
    "stage_rental": {"priority": 3, "queries": ["stage rental events", "dance floor rental", "riser rental"]},

    # Food & Beverage
    "food_truck": {"priority": 2, "queries": ["food trucks for events", "catering food trucks", "gourmet food trucks hire"]},
    "dessert_catering": {"priority": 3, "queries": ["dessert catering", "dessert table catering", "churro cart rental"]},
    "coffee_cart": {"priority": 3, "queries": ["coffee cart rental events", "espresso bar catering", "mobile coffee service"]},

    # Kids & Family
    "kids_party": {"priority": 2, "queries": ["kids party entertainment", "children party planners", "birthday party planners"]},
    "face_painter": {"priority": 3, "queries": ["face painters for events", "face painting entertainment"]},
    "magician": {"priority": 3, "queries": ["magicians for events", "event entertainment magician"]},
    "character_company": {"priority": 3, "queries": ["character appearances events", "princess party entertainment"]},

    # Outdoor & Venue
    "backyard_party": {"priority": 1, "queries": ["backyard party services", "outdoor party setup", "backyard event rental"]},
    "farm_ranch_venue": {"priority": 2, "queries": ["farm wedding venues", "ranch event venues", "barn wedding venues"]},
    "winery_venue": {"priority": 2, "queries": ["winery wedding venues", "vineyard event spaces"]},
    "church_hall": {"priority": 2, "queries": ["church event halls", "church reception halls", "parish halls rental"]},
    "community_center": {"priority": 3, "queries": ["community center events", "recreation center rental"]},

    # Construction (secondary market)
    "construction": {"priority": 3, "queries": ["construction companies", "general contractors", "commercial construction"]},
    "festival_organizer": {"priority": 2, "queries": ["festival organizers", "outdoor festival production", "street fair organizers"]},

    # Hospitality & Accommodation
    "hotel_venue": {"priority": 2, "queries": ["hotels with event space", "boutique hotel events", "hotel wedding venues"]},
    "airbnb_property": {"priority": 3, "queries": ["event-friendly Airbnb", "large vacation rental events"]},

    # Transportation
    "limo_service": {"priority": 2, "queries": ["limousine service events", "party bus rental", "wedding transportation"]},
    "valet_service": {"priority": 3, "queries": ["valet parking events", "event parking services"]},

    # Miscellaneous
    "officiant": {"priority": 2, "queries": ["wedding officiants", "ceremony officiants"]},
    "beauty_services": {"priority": 2, "queries": ["bridal makeup artists", "wedding hair stylists", "mobile beauty services"]},
    "porta_potty_competitor": {"priority": 3, "queries": ["portable restroom rental", "porta potty rental", "portable toilet rental"]},
    "security_service": {"priority": 3, "queries": ["event security services", "private security events"]},
}

# ── State Management ──────────────────────────────────────────────────────────

def _load_state() -> Dict[str, Any]:
    """Load daemon state from disk."""
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {
        "last_run": None,
        "zone_index": 0,
        "category_index": 0,
        "total_discovered": 0,
        "runs_completed": 0,
        "errors": [],
    }


def _save_state(state: Dict[str, Any]):
    """Persist daemon state to disk."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str))


def _get_vendor_count() -> int:
    """Get current vendor count from database."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        row = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception:
        return 0


# ── Research Runner ───────────────────────────────────────────────────────────

class VendorResearchDaemon:
    """Continuous vendor research daemon.

    Rotates through geographic zones and categories, using the existing
    VendorResearcher for discovery and vendor_db for storage.
    """

    def __init__(self):
        self.state = _load_state()
        self._session_discovered = 0
        self._session_errors = 0

        # Build ordered lists for rotation
        self._zones = sorted(
            GEOGRAPHIC_ZONES.items(),
            key=lambda x: x[1]["priority"],
        )
        self._categories = sorted(
            RESEARCH_CATEGORIES.items(),
            key=lambda x: x[1]["priority"],
        )

    async def run_research_cycle(self, max_categories: int = 10, max_locations_per_cat: int = 5) -> Dict[str, Any]:
        """Run one research cycle across zones and categories.

        Each cycle picks the next set of zone+category combinations
        and researches them. Rotates state so no zone/category is repeated
        before all others are covered.

        Returns summary dict with stats.
        """
        from core.vendor_research import VendorResearcher
        from core.vendor_db import bulk_save_vendors
        from core.worker_pool import call_provider

        researcher = VendorResearcher()
        cycle_start = time.time()
        cycle_discovered = 0
        cycle_errors = 0
        cycle_details = []

        # Pick starting indices from saved state
        zone_idx = self.state.get("zone_index", 0) % len(self._zones)
        cat_idx = self.state.get("category_index", 0) % len(self._categories)

        categories_processed = 0
        while categories_processed < max_categories:
            cat_key, cat_info = self._categories[cat_idx % len(self._categories)]
            zone_key, zone_info = self._zones[zone_idx % len(self._zones)]

            # Pick a subset of locations from this zone
            locations = zone_info["locations"]
            if len(locations) > max_locations_per_cat:
                locations = random.sample(locations, max_locations_per_cat)

            queries = cat_info["queries"]
            query = random.choice(queries)

            log.info(
                "Researching: %s in %s (%s)",
                cat_key, zone_info["name"], query,
            )

            try:
                # Use AI-powered research (Tier 2 provider)
                results = await self._ai_research(
                    call_provider, cat_key, query, locations,
                )

                if results:
                    saved = bulk_save_vendors(results)
                    cycle_discovered += saved
                    self._session_discovered += saved
                    log.info("  → Found %d new vendors for %s in %s", saved, cat_key, zone_info["name"])
                else:
                    log.info("  → No new vendors for %s in %s", cat_key, zone_info["name"])

                cycle_details.append({
                    "zone": zone_info["name"],
                    "category": cat_key,
                    "query": query,
                    "found": len(results) if results else 0,
                    "saved": saved if results else 0,
                })

            except Exception as e:
                log.warning("Research error for %s/%s: %s", cat_key, zone_key, e)
                cycle_errors += 1
                self._session_errors += 1
                cycle_details.append({
                    "zone": zone_info["name"],
                    "category": cat_key,
                    "error": str(e),
                })

            # Rotate: advance category, advance zone every 3 categories
            cat_idx += 1
            if categories_processed % 3 == 2:
                zone_idx += 1

            categories_processed += 1

            # Brief delay between categories
            await asyncio.sleep(2)

        # Update state
        self.state["zone_index"] = zone_idx % len(self._zones)
        self.state["category_index"] = cat_idx % len(self._categories)
        self.state["last_run"] = datetime.now(timezone.utc).isoformat()
        self.state["total_discovered"] = self.state.get("total_discovered", 0) + cycle_discovered
        self.state["runs_completed"] = self.state.get("runs_completed", 0) + 1
        _save_state(self.state)

        elapsed = int(time.time() - cycle_start)
        total_vendors = _get_vendor_count()

        return {
            "cycle_discovered": cycle_discovered,
            "cycle_errors": cycle_errors,
            "cycle_duration_secs": elapsed,
            "categories_processed": categories_processed,
            "total_vendors_in_db": total_vendors,
            "session_discovered": self._session_discovered,
            "runs_completed": self.state["runs_completed"],
            "details": cycle_details,
        }

    async def _ai_research(
        self,
        call_provider,
        category: str,
        query: str,
        locations: List[str],
    ) -> List[Dict[str, Any]]:
        """Use AI model to discover vendors in given locations.

        Routes through Tier 2 providers (Gemini, OpenRouter, Moonshot)
        with fallback to Tier 3 (HF, Z.AI, Ollama).
        """
        from core.worker_pool import call_for_task

        location_str = ", ".join(locations)
        prompt = f"""List 20 real {query} businesses in or near these locations: {location_str}

Return ONLY a JSON array. Each entry must have:
- "name": business name (must be a REAL business, not made up)
- "phone": phone number if known (format: xxx-xxx-xxxx), or ""
- "email": email if known, or ""
- "website": website URL if known, or ""
- "address": street address if known, or ""
- "city": city name
- "category": "{category}"

Important: Only include REAL businesses that actually exist. Do not fabricate businesses.
Return the JSON array and nothing else. No markdown, no explanation."""

        # Try task-based routing first (uses tier system)
        result = await call_for_task(
            "vendor_research",
            [{"role": "user", "content": prompt}],
            system="You are a business directory assistant. Return ONLY valid JSON arrays of real businesses. Never fabricate business names.",
            max_tokens=2000,
            temperature=0.3,
        )

        if not result.get("ok"):
            log.warning("AI research failed: %s", result.get("content", "unknown"))
            return []

        # Parse JSON from response
        content = result.get("content", "")
        return self._parse_vendor_json(content, category)

    def _parse_vendor_json(self, content: str, category: str) -> List[Dict[str, Any]]:
        """Parse vendor JSON from AI response, handling common format issues."""
        # Strip markdown code blocks if present
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            # Remove first and last lines (``` markers)
            lines = [l for l in lines if not l.strip().startswith("```")]
            content = "\n".join(lines)

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # Try to find JSON array in the content
            import re
            match = re.search(r'\[.*\]', content, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    log.warning("Could not parse vendor JSON from AI response")
                    return []
            else:
                return []

        if not isinstance(data, list):
            return []

        vendors = []
        for item in data:
            if not isinstance(item, dict):
                continue
            name = item.get("name", "").strip()
            if not name or len(name) < 3:
                continue

            vendors.append({
                "name": name,
                "phone": str(item.get("phone", "")).strip(),
                "email": str(item.get("email", "")).strip(),
                "website": str(item.get("website", "")).strip(),
                "address": str(item.get("address", "")).strip(),
                "city": str(item.get("city", "")).strip(),
                "category": category,
                "source": "ai_research",
            })

        return vendors

    async def run_nightly(self, target_new: int = 500) -> Dict[str, Any]:
        """Run continuous research cycles until target is met or 6 hours pass.

        Default target: 500 new vendors per night.
        Max runtime: 6 hours (to leave headroom).
        """
        log.info("Starting nightly research run (target: %d new vendors)", target_new)
        start = time.time()
        max_runtime = 6 * 3600  # 6 hours max
        total_discovered = 0
        cycles = 0
        all_details = []

        while total_discovered < target_new and (time.time() - start) < max_runtime:
            result = await self.run_research_cycle(
                max_categories=8,
                max_locations_per_cat=4,
            )
            total_discovered += result["cycle_discovered"]
            cycles += 1
            all_details.extend(result.get("details", []))

            log.info(
                "Cycle %d complete: +%d vendors (total session: %d, target: %d)",
                cycles, result["cycle_discovered"], total_discovered, target_new,
            )

            # If a cycle found nothing, increase delay to avoid hammering
            if result["cycle_discovered"] == 0:
                await asyncio.sleep(30)
            else:
                await asyncio.sleep(5)

        elapsed = int(time.time() - start)
        total_in_db = _get_vendor_count()

        summary = {
            "total_discovered": total_discovered,
            "cycles_completed": cycles,
            "runtime_secs": elapsed,
            "total_vendors_in_db": total_in_db,
            "target_met": total_discovered >= target_new,
        }

        # Send Telegram summary
        await self._notify_completion(summary)

        return summary

    async def _notify_completion(self, summary: Dict[str, Any]):
        """Send Telegram notification with nightly research results."""
        try:
            from telegram.bot import get_bot
            bot = get_bot()
            if not bot:
                return

            hours = summary["runtime_secs"] // 3600
            mins = (summary["runtime_secs"] % 3600) // 60

            icon = "✅" if summary["target_met"] else "⚠️"
            msg = (
                f"{icon} *Nightly Vendor Research Complete*\n\n"
                f"New vendors found: *{summary['total_discovered']}*\n"
                f"Research cycles: {summary['cycles_completed']}\n"
                f"Runtime: {hours}h {mins}m\n"
                f"Total vendors in DB: *{summary['total_vendors_in_db']:,}*\n"
                f"Target met: {'Yes' if summary['target_met'] else 'No'}"
            )

            for chat_id in bot.allowed_chat_ids:
                await bot.send(chat_id, msg)
        except Exception as e:
            log.warning("Failed to send Telegram notification: %s", e)

    def get_status(self) -> Dict[str, Any]:
        """Get current daemon status for API endpoint."""
        return {
            "last_run": self.state.get("last_run"),
            "total_discovered_all_time": self.state.get("total_discovered", 0),
            "runs_completed": self.state.get("runs_completed", 0),
            "current_zone_index": self.state.get("zone_index", 0),
            "current_category_index": self.state.get("category_index", 0),
            "total_zones": len(self._zones),
            "total_categories": len(self._categories),
            "total_vendors_in_db": _get_vendor_count(),
            "session_discovered": self._session_discovered,
            "session_errors": self._session_errors,
            "zones": {k: v["name"] for k, v in self._zones},
            "category_count": len(self._categories),
        }


# Global daemon instance
_daemon = None

def get_daemon() -> VendorResearchDaemon:
    global _daemon
    if _daemon is None:
        _daemon = VendorResearchDaemon()
    return _daemon


# ── CLI Entry Point ───────────────────────────────────────────────────────────

async def _main():
    """Run the daemon as a standalone process."""
    logging.basicConfig(
        level=logging.INFO,
        format="[%(name)s] %(message)s",
    )
    daemon = VendorResearchDaemon()
    log.info("Vendor research daemon starting...")
    log.info("Zones: %d, Categories: %d", len(daemon._zones), len(daemon._categories))
    log.info("Current vendor count: %d", _get_vendor_count())

    cycle = 0
    while True:
        cycle += 1
        try:
            result = await daemon.run_nightly(target_new=500)
            discovered = result.get("total_discovered", 0)
            total = result.get("total_vendors_in_db", 0)
            log.info(
                "Cycle %d complete: %d new vendors, %d total in DB",
                cycle, discovered, total,
            )
            # Short sleep if still finding vendors, longer if dry
            delay = 5 if discovered > 0 else 30
        except Exception as e:
            log.error("Cycle %d failed: %s", cycle, e)
            delay = 30

        log.info("Next cycle in %ds...", delay)
        await asyncio.sleep(delay)


if __name__ == "__main__":
    asyncio.run(_main())
