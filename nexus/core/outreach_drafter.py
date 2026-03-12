"""
Nexus Outreach Drafter — Generates personalized vendor partnership messages.

ALL AGENTS READ-ONLY: Vendor research agents are permanently read-only.
Zero interaction with any platform content ever. (CLAUDE.md Rule 24)

Uses Z.AI GLM-4.5-Flash for content generation. Nothing is ever sent
without explicit Kai approval via Telegram.

Referral fee structure (from CLAUDE.md):
  Tier 1 (0-10 mi): $200 referral at $1,200 booking → vendor nets $200
  Tier 2 (10-20 mi): $300 referral at $1,500 booking → vendor nets $300

Usage:
    from core.outreach_drafter import OutreachDrafter
    drafter = OutreachDrafter()
    messages = await drafter.draft_batch(vendors[:25])
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger("outreach_drafter")


OUTREACH_TEMPLATE = """You are writing a vendor-to-vendor partnership email on behalf of Zoar Bathroom Rentals.

Target vendor: {vendor_name}
Category: {category}
Location: {location}
{research_detail}

Write a short, professional partnership outreach message. Rules:
1. Reference something specific about their business (from the research detail above)
2. Introduce Zoar Bathroom Rentals as a luxury restroom trailer rental company
3. Explain the referral partnership: they refer clients who need restroom facilities, we handle everything
4. State the referral fee: $200 for Tier 1 bookings (within 10 miles), $300 for Tier 2 (10-20 miles)
5. Include a clear, low-pressure call to action
6. Keep it under 150 words
7. Professional but warm tone — vendor to vendor, not salesy
8. Do NOT include specific Zoar pricing for the end customer
9. Sign off as "Zoar Bathroom Rentals Team"

Write ONLY the message body. No subject line. No greeting salutation."""


class OutreachDrafter:
    """Drafts personalized vendor partnership messages using Z.AI GLM."""

    def __init__(self):
        self._drafted_count = 0

    async def draft_single(self, vendor: Dict[str, Any]) -> Dict[str, Any]:
        """Draft a single outreach message for a vendor.

        Returns: {"vendor_id": ..., "vendor_name": ..., "message": ..., "ok": bool}
        """
        from core.worker_pool import call_provider

        vendor_name = vendor.get("business_name", vendor.get("name", "Unknown"))
        category = vendor.get("category", "Event Services")
        location = vendor.get("city", vendor.get("area", "Los Angeles"))

        # Build research detail from available data
        details = []
        if vendor.get("website"):
            details.append("Website: " + vendor["website"])
        if vendor.get("google_rating"):
            details.append("Google rating: " + str(vendor["google_rating"]) + " stars")
        if vendor.get("review_count"):
            details.append(str(vendor["review_count"]) + " reviews")
        if vendor.get("phone"):
            details.append("Phone available")
        if vendor.get("instagram"):
            details.append("Instagram: @" + vendor["instagram"])

        research_detail = "Research notes: " + ", ".join(details) if details else ""

        prompt = OUTREACH_TEMPLATE.format(
            vendor_name=vendor_name,
            category=category,
            location=location,
            research_detail=research_detail,
        )

        start = time.time()
        result = await call_provider(
            "zai",
            [{"role": "user", "content": prompt}],
            system="You are a professional business development writer for Zoar Bathroom Rentals.",
            max_tokens=800,
            temperature=0.7,
        )

        latency = int((time.time() - start) * 1000)

        if result["ok"]:
            self._drafted_count += 1
            return {
                "vendor_id": vendor.get("id", ""),
                "vendor_name": vendor_name,
                "category": category,
                "location": location,
                "message": result["content"],
                "ok": True,
                "provider": result.get("provider", "zai"),
                "tokens": result.get("tokens_used", 0),
                "latency_ms": latency,
            }

        # Fallback to Mistral if Z.AI fails
        result = await call_provider(
            "mistral",
            [{"role": "user", "content": prompt}],
            system="You are a professional business development writer for Zoar Bathroom Rentals.",
            max_tokens=800,
            temperature=0.7,
        )

        if result["ok"]:
            self._drafted_count += 1
            return {
                "vendor_id": vendor.get("id", ""),
                "vendor_name": vendor_name,
                "category": category,
                "location": location,
                "message": result["content"],
                "ok": True,
                "provider": result.get("provider", "mistral"),
                "tokens": result.get("tokens_used", 0),
                "latency_ms": latency,
            }

        return {
            "vendor_id": vendor.get("id", ""),
            "vendor_name": vendor_name,
            "message": "",
            "ok": False,
            "error": result.get("content", "Draft failed"),
        }

    async def draft_batch(self, vendors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Draft outreach messages for up to 25 vendors.

        Returns list of draft results. Nothing is sent — all drafts
        go to Telegram for Kai's approval.
        """
        import asyncio

        if len(vendors) > 25:
            vendors = vendors[:25]

        tasks = [self.draft_single(v) for v in vendors]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        drafts = []
        for r in results:
            if isinstance(r, Exception):
                drafts.append({"ok": False, "error": str(r)})
            else:
                drafts.append(r)

        successful = sum(1 for d in drafts if d.get("ok"))
        log.info("Drafted %d/%d outreach messages", successful, len(vendors))
        return drafts

    @property
    def drafted_count(self) -> int:
        return self._drafted_count
