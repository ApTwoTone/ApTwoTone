"""
Venue Intelligence Agent — Deep-crawls venue websites to determine
restroom availability and fit for Zoar Bathroom Rentals.

Read-only research only (CLAUDE.md Rule 24). Never interacts with venue content.
Uses free AI models only (Rule 6).

Usage:
    from core.venue_intel import VenueIntelAgent
    agent = VenueIntelAgent()
    result = await agent.assess_venue(vendor_id=42)
    results = await agent.assess_venues([42, 43, 44, 45, 46])
    await agent.daemon_cycle()
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

log = logging.getLogger("venue_intel")
DB_PATH = Path.home() / ".nexus" / "memory.db"

# Keywords to discover relevant subpages from homepage links
SUBPAGE_KEYWORDS = [
    "pricing", "price", "rates", "packages", "cost",
    "faq", "frequently-asked", "questions",
    "amenities", "amenity", "facilities", "facility",
    "features", "details", "included", "what-s-included",
    "whats-included", "about", "venue-details", "the-space",
    "our-space", "rental", "rentals", "event-space",
]

FALLBACK_PATHS = ["/pricing", "/faq", "/amenities", "/about", "/packages"]

# Max chars per page to include in AI prompt
MAX_PAGE_CHARS = 8000

SYSTEM_PROMPT = """You are a venue restroom analyst for a luxury portable restroom trailer rental company in the San Fernando Valley, CA. You analyze venue website content to determine whether the venue needs portable restroom services.

Your job: read the venue's website text and determine:
1. Does this venue have permanent restroom facilities on-site?
2. If yes, how many stalls? Are they adequate for large events (100-300 guests)?
3. Are restrooms offered as a paid add-on? If so, at what price?
4. Is the venue primarily outdoor (where portable restrooms are most needed)?
5. What is the venue's capacity?
6. What types of events do they host?

IMPORTANT: Your ENTIRE response must be a single valid JSON object. Do NOT include any explanation, summary, or text outside the JSON. No markdown. No comments. Just the JSON.

Return ONLY a valid JSON object with these exact keys:
{
  "has_restrooms": "yes" | "no" | "limited" | "addon" | "unknown",
  "restroom_type": "permanent_full" | "permanent_limited" | "portable_addon" | "none" | "unknown",
  "restroom_addon_price": "$XXX or empty string if not applicable",
  "restroom_details": "Brief description of restroom situation",
  "outdoor_capacity": 0,
  "indoor_capacity": 0,
  "venue_type": "ranch | garden | estate | barn | park | rooftop | banquet_hall | hotel | restaurant | winery | beach | other",
  "event_types": ["wedding", "corporate", "quinceanera"],
  "contact_email": "email or empty",
  "contact_phone": "phone or empty",
  "fit_score": 5,
  "fit_reasoning": "One sentence explaining the score"
}

Scoring guide:
- 9-10: Outdoor venue, explicitly NO restrooms or restrooms not included, 100+ guest capacity
- 7-8: Outdoor venue, no restroom mention anywhere on site, 50+ guests
- 5-6: Limited restrooms or restrooms as expensive add-on ($500+), large capacity
- 3-4: Has some restrooms but unclear adequacy, or mixed indoor/outdoor
- 1-2: Full permanent restrooms included, or cheap restroom add-on under $400

CRITICAL: If the venue website explicitly mentions offering restroom trailers, portable restrooms, or restroom add-ons under $400, score them LOW (1-3). They already provide this service and are a BAD target."""


def _conn():
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


class VenueIntelAgent:
    """Deep-research agent that crawls venue websites to assess restroom fit."""

    def __init__(self, max_concurrent: int = 3):
        self._sem = asyncio.Semaphore(max_concurrent)

    # ── Public API ────────────────────────────────────────────

    async def assess_venue(self, vendor_id: int) -> Dict[str, Any]:
        """Full assessment pipeline for one venue."""
        conn = _conn()
        row = conn.execute(
            "SELECT id, name, website, city, category FROM vendors WHERE id = ?",
            (vendor_id,),
        ).fetchone()
        conn.close()

        if not row:
            return {"error": f"Vendor {vendor_id} not found", "vendor_id": vendor_id}

        vendor = dict(row)
        log.info("Assessing venue: %s (id=%d)", vendor["name"], vendor_id)

        website = (vendor.get("website") or "").strip()
        if website and not website.startswith("http"):
            website = f"https://{website}"

        try:
            if website:
                assessment = await self._assess_via_website(vendor, website)
            else:
                assessment = await self._ai_knowledge_fallback(
                    vendor["name"], vendor.get("city", "")
                )

            self._save_assessment(vendor_id, vendor["name"], website, assessment)
            return {"ok": True, "vendor_id": vendor_id, **assessment}

        except Exception as e:
            log.error("Assessment failed for vendor %d: %s", vendor_id, e)
            failed = {
                "has_restrooms": "unknown",
                "fit_score": 0,
                "fit_reasoning": f"Assessment failed: {e}",
                "confidence": "failed",
            }
            self._save_assessment(vendor_id, vendor["name"], website, failed)
            return {"ok": False, "vendor_id": vendor_id, "error": str(e)}

    async def assess_venues(self, vendor_ids: List[int]) -> List[Dict[str, Any]]:
        """Assess multiple venues in parallel (bounded by semaphore)."""
        async def _bounded(vid):
            async with self._sem:
                return await self.assess_venue(vid)

        return await asyncio.gather(*[_bounded(vid) for vid in vendor_ids])

    async def daemon_cycle(self, limit: int = 10) -> Dict[str, Any]:
        """Pick unassessed venue-category vendors and assess them."""
        conn = _conn()
        rows = conn.execute(
            """
            SELECT id FROM vendors
            WHERE venue_assessed = 0
              AND category IN (
                  'wedding_venue', 'venue', 'event_venue', 'banquet_hall',
                  'country_club', 'hotel_venue', 'winery', 'ranch',
                  'garden_venue', 'estate', 'barn_venue', 'park_venue'
              )
              AND (website IS NOT NULL AND website != '' AND website != 'N/A')
            ORDER BY referral_score DESC, campaign_quality DESC, id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        conn.close()

        if not rows:
            log.info("No unassessed venues found")
            return {"assessed": 0, "targets": []}

        vendor_ids = [r["id"] for r in rows]
        results = await self.assess_venues(vendor_ids)

        targets = [
            r for r in results
            if r.get("ok") and r.get("fit_score", 0) >= 7
        ]

        summary = {
            "assessed": len(results),
            "succeeded": sum(1 for r in results if r.get("ok")),
            "failed": sum(1 for r in results if not r.get("ok")),
            "high_value": len(targets),
            "targets": targets,
        }

        log.info(
            "Venue intel cycle: %d assessed, %d high-value targets",
            summary["assessed"], summary["high_value"],
        )
        return summary

    def get_top_targets(self, min_score: int = 7, limit: int = 20) -> List[Dict[str, Any]]:
        """Get highest-scoring venue targets."""
        conn = _conn()
        rows = conn.execute(
            """
            SELECT va.*, v.name as vendor_name, v.phone, v.email, v.city, v.category
            FROM venue_assessments va
            JOIN vendors v ON v.id = va.vendor_id
            WHERE va.fit_score >= ?
            ORDER BY va.fit_score DESC, va.outdoor_capacity DESC
            LIMIT ?
            """,
            (min_score, limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_stats(self) -> Dict[str, Any]:
        """Assessment statistics."""
        conn = _conn()
        total = conn.execute("SELECT COUNT(*) FROM venue_assessments").fetchone()[0]
        by_score = conn.execute(
            """
            SELECT
                SUM(CASE WHEN fit_score >= 7 THEN 1 ELSE 0 END) as high,
                SUM(CASE WHEN fit_score BETWEEN 4 AND 6 THEN 1 ELSE 0 END) as medium,
                SUM(CASE WHEN fit_score BETWEEN 1 AND 3 THEN 1 ELSE 0 END) as low,
                SUM(CASE WHEN fit_score = 0 THEN 1 ELSE 0 END) as failed
            FROM venue_assessments
            """
        ).fetchone()
        by_restroom = conn.execute(
            "SELECT has_restrooms, COUNT(*) as cnt FROM venue_assessments "
            "GROUP BY has_restrooms"
        ).fetchall()
        unassessed = conn.execute(
            """
            SELECT COUNT(*) FROM vendors
            WHERE venue_assessed = 0
              AND category IN (
                  'wedding_venue', 'venue', 'event_venue', 'banquet_hall',
                  'country_club', 'hotel_venue', 'winery', 'ranch',
                  'garden_venue', 'estate', 'barn_venue', 'park_venue'
              )
              AND (website IS NOT NULL AND website != '' AND website != 'N/A')
            """
        ).fetchone()[0]
        conn.close()
        return {
            "total_assessed": total,
            "unassessed_remaining": unassessed,
            "by_score": {
                "high_value": by_score["high"] or 0,
                "medium": by_score["medium"] or 0,
                "low": by_score["low"] or 0,
                "failed": by_score["failed"] or 0,
            },
            "by_restroom": {r["has_restrooms"]: r["cnt"] for r in by_restroom},
        }

    # ── Website crawling ──────────────────────────────────────

    async def _assess_via_website(
        self, vendor: Dict, website: str
    ) -> Dict[str, Any]:
        """Crawl venue website and analyze with AI."""
        from integrations.browser_agent import browse_url

        # Crawl homepage
        log.info("Crawling homepage: %s", website)
        homepage = await browse_url(website, extract_text=True)

        if not homepage.get("success"):
            log.warning("Failed to load %s: %s", website, homepage.get("error"))
            return await self._ai_knowledge_fallback(
                vendor["name"], vendor.get("city", "")
            )

        pages = {"homepage": (homepage.get("text") or "")[:MAX_PAGE_CHARS]}
        links = homepage.get("links") or []

        # Discover and crawl subpages
        subpage_urls = self._discover_subpages(website, links)
        for url in subpage_urls:
            label = self._label_from_url(url)
            log.info("Crawling subpage [%s]: %s", label, url)
            try:
                result = await browse_url(url, extract_text=True)
                if result.get("success"):
                    text = (result.get("text") or "")[:MAX_PAGE_CHARS]
                    if len(text) > 200:
                        pages[label] = text
            except Exception as e:
                log.warning("Subpage crawl failed for %s: %s", url, e)

        # If no subpages found via links, try fallback paths
        if len(pages) == 1:
            base = website.rstrip("/")
            for path in FALLBACK_PATHS:
                try:
                    result = await browse_url(f"{base}{path}", extract_text=True)
                    if result.get("success"):
                        text = (result.get("text") or "")[:MAX_PAGE_CHARS]
                        if len(text) > 200:
                            pages[path.strip("/")] = text
                            if len(pages) >= 3:
                                break
                except Exception:
                    pass

        # Analyze with AI
        combined_text = self._build_combined_text(vendor["name"], pages)
        assessment = await self._analyze_with_ai(
            vendor["name"],
            vendor.get("city", ""),
            combined_text,
        )
        assessment["pages_crawled"] = len(pages)
        assessment["confidence"] = "high" if len(pages) >= 2 else "medium"
        assessment["source"] = "website_crawl"
        return assessment

    def _discover_subpages(self, base_url: str, links: List[Dict]) -> List[str]:
        """Find up to 3 most relevant subpages from homepage links."""
        base_domain = urlparse(base_url).netloc.lower()
        scored = []

        for link in links:
            href = (link.get("href") or "").strip()
            text = (link.get("text") or "").lower()

            if not href:
                continue

            # Parse and filter
            parsed = urlparse(href)
            link_domain = parsed.netloc.lower()

            # Only same-domain links
            if link_domain and base_domain not in link_domain:
                continue

            # Build absolute URL
            if not parsed.scheme:
                href = f"{base_url.rstrip('/')}/{href.lstrip('/')}"

            # Skip anchors, images, social
            if any(x in href.lower() for x in [
                "#", ".jpg", ".png", ".gif", ".pdf",
                "facebook.com", "instagram.com", "twitter.com",
                "mailto:", "tel:",
            ]):
                continue

            # Score by keyword matches
            path = parsed.path.lower()
            score = sum(1 for kw in SUBPAGE_KEYWORDS if kw in path or kw in text)
            if score > 0:
                scored.append((score, href))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Deduplicate and return top 3
        seen = set()
        result = []
        for _, url in scored:
            normalized = url.rstrip("/").lower()
            if normalized not in seen and normalized != base_url.rstrip("/").lower():
                seen.add(normalized)
                result.append(url)
            if len(result) >= 3:
                break
        return result

    @staticmethod
    def _label_from_url(url: str) -> str:
        """Extract a human-readable label from URL path."""
        path = urlparse(url).path.strip("/")
        return path.replace("/", "_") if path else "page"

    @staticmethod
    def _build_combined_text(venue_name: str, pages: Dict[str, str]) -> str:
        """Combine page texts into a single prompt-ready string."""
        parts = []
        for label, text in pages.items():
            parts.append(f"--- {label.upper()} ---\n{text}")
        return "\n\n".join(parts)

    # ── AI analysis ───────────────────────────────────────────

    async def _analyze_with_ai(
        self, venue_name: str, city: str, combined_text: str
    ) -> Dict[str, Any]:
        """Send combined text to Groq for structured assessment."""
        from core.worker_pool import call_for_task

        user_msg = (
            f"Venue: {venue_name}\n"
            f"Location: {city}\n\n"
            f"Website content:\n\n{combined_text}"
        )

        result = await call_for_task(
            task_type="structured_data",
            messages=[{"role": "user", "content": user_msg}],
            system=SYSTEM_PROMPT,
            max_tokens=1024,
            temperature=0.2,
        )

        if not result.get("ok"):
            log.warning("AI analysis failed: %s", result.get("error"))
            return {
                "has_restrooms": "unknown",
                "fit_score": 0,
                "fit_reasoning": "AI analysis failed",
                "raw_ai_response": str(result),
            }

        assessment = self._parse_ai_response(result.get("content", ""))

        # If parse failed, retry with a simpler extraction prompt
        if assessment.get("fit_score", 0) == 0 and assessment.get("has_restrooms") == "unknown":
            raw_text = result.get("content", "")
            if raw_text and len(raw_text) > 50:
                log.info("First parse failed, retrying with extraction prompt")
                retry_msg = (
                    "The following text is an AI analysis of a venue. "
                    "Extract the key facts and return ONLY a JSON object, nothing else.\n\n"
                    f"Text to extract from:\n{raw_text[:3000]}\n\n"
                    "Return JSON with keys: has_restrooms, restroom_type, restroom_details, "
                    "outdoor_capacity, indoor_capacity, venue_type, fit_score, fit_reasoning"
                )
                retry_result = await call_for_task(
                    task_type="structured_data",
                    messages=[{"role": "user", "content": retry_msg}],
                    system="You are a JSON extractor. Output ONLY valid JSON. No text, no markdown, no explanation.",
                    max_tokens=512,
                    temperature=0.1,
                )
                if retry_result.get("ok"):
                    retry_assessment = self._parse_ai_response(retry_result.get("content", ""))
                    if retry_assessment.get("fit_score", 0) > 0 or retry_assessment.get("has_restrooms") != "unknown":
                        retry_assessment["raw_ai_response"] = raw_text[:2000]
                        return retry_assessment

        return assessment

    async def _ai_knowledge_fallback(
        self, venue_name: str, city: str
    ) -> Dict[str, Any]:
        """When no website found, ask AI for general knowledge."""
        from core.worker_pool import call_for_task

        user_msg = (
            f"I need information about this venue but have no website to crawl:\n\n"
            f"Venue: {venue_name}\n"
            f"Location: {city}\n\n"
            f"Based on your knowledge, analyze this venue's restroom situation "
            f"and return the JSON assessment. If you don't know, use 'unknown' "
            f"for restroom fields and score 5 (neutral)."
        )

        result = await call_for_task(
            task_type="structured_data",
            messages=[{"role": "user", "content": user_msg}],
            system=SYSTEM_PROMPT,
            max_tokens=1024,
            temperature=0.3,
        )

        if not result.get("ok"):
            return {
                "has_restrooms": "unknown",
                "fit_score": 0,
                "fit_reasoning": "No website and AI fallback failed",
                "confidence": "failed",
                "source": "ai_knowledge",
            }

        assessment = self._parse_ai_response(result.get("content", ""))
        assessment["confidence"] = "low"
        assessment["source"] = "ai_knowledge"
        return assessment

    @staticmethod
    def _parse_ai_response(content: str) -> Dict[str, Any]:
        """Parse JSON from AI response, handling markdown fences."""
        # Strip markdown code fences
        cleaned = content.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # Remove first and last fence lines
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            # Try to find JSON object in the text (supports nested braces)
            data = None
            for i, ch in enumerate(cleaned):
                if ch == '{':
                    depth = 0
                    for j in range(i, len(cleaned)):
                        if cleaned[j] == '{':
                            depth += 1
                        elif cleaned[j] == '}':
                            depth -= 1
                        if depth == 0:
                            try:
                                data = json.loads(cleaned[i:j+1])
                            except json.JSONDecodeError:
                                pass
                            break
                    if data is not None:
                        break
            if data is None:
                return {
                    "has_restrooms": "unknown",
                    "fit_score": 0,
                    "fit_reasoning": "No JSON found in AI response",
                    "raw_ai_response": content[:2000],
                }

        # Normalize and validate (safe int parsing for AI-generated values)
        def _safe_int(val, default=0):
            if isinstance(val, (int, float)):
                return int(val)
            if isinstance(val, str):
                # Handle "8/10", "~200", "100+", etc.
                digits = re.sub(r'[^\d]', '', str(val).split('/')[0].split('-')[0])
                return int(digits) if digits else default
            return default

        data["fit_score"] = max(0, min(10, _safe_int(data.get("fit_score", 0))))
        data["outdoor_capacity"] = _safe_int(data.get("outdoor_capacity", 0))
        data["indoor_capacity"] = _safe_int(data.get("indoor_capacity", 0))
        data.setdefault("has_restrooms", "unknown")
        data.setdefault("restroom_type", "unknown")
        data.setdefault("restroom_addon_price", "")
        data.setdefault("restroom_details", "")
        data.setdefault("venue_type", "other")
        data.setdefault("fit_reasoning", "")
        data.setdefault("raw_ai_response", content[:2000])

        # Ensure event_types is a JSON string
        et = data.get("event_types", [])
        if isinstance(et, list):
            data["event_types"] = json.dumps(et)
        elif isinstance(et, str):
            data["event_types"] = et

        # Ensure contact_info is a JSON string
        ci = {}
        if data.get("contact_email"):
            ci["email"] = data.pop("contact_email")
        if data.get("contact_phone"):
            ci["phone"] = data.pop("contact_phone")
        data["contact_info"] = json.dumps(ci)

        return data

    # ── Persistence ───────────────────────────────────────────

    def _save_assessment(
        self, vendor_id: int, venue_name: str, website: str,
        assessment: Dict[str, Any],
    ) -> None:
        """Write assessment to venue_assessments table."""
        conn = _conn()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO venue_assessments (
                    vendor_id, venue_name, website, has_restrooms, restroom_type,
                    restroom_addon_price, restroom_details, outdoor_capacity,
                    indoor_capacity, venue_type, event_types, contact_info,
                    fit_score, fit_reasoning, confidence, pages_crawled,
                    raw_ai_response, source, assessed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    vendor_id,
                    venue_name,
                    website or "",
                    assessment.get("has_restrooms", "unknown"),
                    assessment.get("restroom_type", "unknown"),
                    assessment.get("restroom_addon_price", ""),
                    assessment.get("restroom_details", ""),
                    assessment.get("outdoor_capacity", 0),
                    assessment.get("indoor_capacity", 0),
                    assessment.get("venue_type", ""),
                    assessment.get("event_types", "[]"),
                    assessment.get("contact_info", "{}"),
                    assessment.get("fit_score", 0),
                    assessment.get("fit_reasoning", ""),
                    assessment.get("confidence", "low"),
                    assessment.get("pages_crawled", 0),
                    assessment.get("raw_ai_response", ""),
                    assessment.get("source", "website_crawl"),
                ),
            )
            conn.execute(
                "UPDATE vendors SET venue_assessed = 1, venue_fit_score = ? WHERE id = ?",
                (assessment.get("fit_score", 0), vendor_id),
            )
            conn.commit()
        except Exception as e:
            log.error("Failed to save assessment for vendor %d: %s", vendor_id, e)
        finally:
            conn.close()
