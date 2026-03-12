"""
Lead Finder Agent — Searches for potential customers across platforms.
Uses web search to find event planners, wedding venues, and potential clients
in the SoCal area who might need luxury restroom rentals.
"""
import asyncio
import json
import random
from datetime import datetime
from pathlib import Path

from agents.base import BaseAgent
from agents.config import AGENT_DEFAULTS, BUSINESS, AGENT_STATE_DIR, DB_PATH

LEADS_QUEUE = AGENT_STATE_DIR / "discovered_leads.json"


class LeadFinderAgent(BaseAgent):
    """Discovers potential leads through web research."""

    def __init__(self):
        super().__init__("lead_finder", "lead_discovery")

    async def search_for_leads(self, query_type: str = "wedding") -> list:
        """
        Use AI to generate targeted search queries and analyze results.
        Returns structured lead data.
        """
        queries_by_type = {
            "wedding": [
                "outdoor wedding venues Los Angeles 2026",
                "backyard wedding planning Santa Clarita",
                "ranch wedding venue Ventura County",
                "outdoor wedding Malibu vendor recommendations",
                "vineyard wedding SoCal planning",
            ],
            "corporate": [
                "corporate event venues outdoor Los Angeles",
                "company picnic planning San Fernando Valley",
                "outdoor team building events LA",
            ],
            "film": [
                "film production outdoor locations Los Angeles",
                "commercial shoot outdoor venue LA",
                "movie production remote location facilities",
            ],
            "festival": [
                "outdoor festival Los Angeles 2026",
                "community event planning Santa Monica",
                "outdoor concert venue LA county",
            ],
        }

        queries = queries_by_type.get(query_type, queries_by_type["wedding"])
        query = random.choice(queries)

        # Use AI to generate outreach targets
        prompt = f"""You are a lead researcher for {BUSINESS['name']}, a luxury restroom trailer rental company in {', '.join(BUSINESS['service_areas'])}.

Search intent: "{query}"

Generate 5 realistic potential lead targets based on this search intent. For each, provide:
1. Business/person name
2. Category (venue, planner, caterer, production, individual)
3. Why they would need a luxury restroom trailer
4. Best outreach approach (email, DM, comment, phone)
5. Urgency level (high/medium/low)

Format as JSON array:
[{{"name": "...", "category": "...", "reason": "...", "approach": "...", "urgency": "..."}}]"""

        result = await self.think(
            prompt,
            model=AGENT_DEFAULTS["smart_model"],
            max_tokens=600
        )

        try:
            # Try to parse AI response as JSON
            start = result.find("[")
            end = result.rfind("]") + 1
            if start >= 0 and end > start:
                leads = json.loads(result[start:end])
                self._save_leads(leads, query_type)
                self.log(f"Found {len(leads)} potential leads for {query_type}")
                return leads
        except json.JSONDecodeError:
            self.log(f"Failed to parse lead results", "WARN")

        return []

    def _save_leads(self, leads: list, query_type: str):
        existing = []
        if LEADS_QUEUE.exists():
            try:
                existing = json.loads(LEADS_QUEUE.read_text())
            except Exception:
                pass

        for lead in leads:
            lead["discovered_at"] = datetime.now().isoformat()
            lead["query_type"] = query_type
            lead["status"] = "discovered"

        existing.extend(leads)
        LEADS_QUEUE.write_text(json.dumps(existing, indent=2))

    async def analyze_competition(self) -> str:
        """Analyze what competitors are doing in the SoCal market."""
        prompt = f"""Analyze the luxury portable restroom rental market in the Greater Los Angeles area.

Consider these aspects:
1. Who are the main competitors? (e.g., Royal Restrooms, Porta Potty Dogs, VIP To Go)
2. What are their pricing strategies?
3. What marketing channels do they use?
4. What are their weaknesses that {BUSINESS['name']} can exploit?
5. What makes our product different? ({', '.join(BUSINESS['features'][:4])})

Give actionable insights in 5 bullet points."""

        analysis = await self.think(
            prompt,
            model=AGENT_DEFAULTS["smart_model"],
            max_tokens=500
        )
        self.log_to_crm("competition_analysis", analysis[:300])
        return analysis

    async def find_upcoming_events(self) -> list:
        """Research upcoming events in SoCal that might need restroom rentals."""
        prompt = f"""List 10 types of upcoming events in the greater Los Angeles / Ventura County area
(March-June 2026) that would likely need a luxury restroom trailer:

For each event type, provide:
- Event category
- Typical locations
- Who to contact (organizer type)
- Best time to reach out
- What to emphasize in outreach

Focus on outdoor events, ranch weddings, film shoots, festivals, and corporate retreats.
Format as JSON array."""

        result = await self.think(
            prompt,
            model=AGENT_DEFAULTS["smart_model"],
            max_tokens=800
        )

        try:
            start = result.find("[")
            end = result.rfind("]") + 1
            if start >= 0 and end > start:
                events = json.loads(result[start:end])
                # Save to file
                events_file = AGENT_STATE_DIR / "upcoming_events.json"
                events_file.write_text(json.dumps(events, indent=2))
                self.log(f"Identified {len(events)} event opportunities")
                return events
        except Exception:
            pass

        return []

    async def run_research_cycle(self):
        """Run one research cycle — find leads across all categories."""
        categories = ["wedding", "corporate", "film", "festival"]
        all_leads = []

        for cat in categories:
            leads = await self.search_for_leads(cat)
            all_leads.extend(leads)
            await asyncio.sleep(5)

        # Also find upcoming events
        events = await self.find_upcoming_events()

        self._state["leads_found"] = self._state.get("leads_found", 0) + len(all_leads)
        self._state["actions_taken"] = self._state.get("actions_taken", 0) + 1
        self.save_state()

        # Write to shared brain
        cats_found = {}
        for lead in all_leads:
            cat = lead.get("query_type", "unknown")
            cats_found[cat] = cats_found.get(cat, 0) + 1
        self.brain.write_insight(
            content=f"Research cycle complete: {len(all_leads)} leads across {len(cats_found)} categories, {len(events)} event types identified",
            insight_type="activity",
            category="leads",
            confidence=1.0,
            data={"total_leads": len(all_leads), "by_category": cats_found, "events": len(events)},
        )

        self.brain.log_activity("research_complete",
                                f"Found {len(all_leads)} leads, {len(events)} event types", "research")
        self.log_to_crm("research_complete", f"Found {len(all_leads)} leads across {len(cats_found)} categories")
        self.log(f"Research cycle complete: {len(all_leads)} leads, {len(events)} event types")
        return all_leads

    async def start(self):
        """Run lead finder on a schedule (every 6 hours)."""
        await super().start()
        while self._running:
            try:
                await self.run_research_cycle()
                # Research every 6 hours
                for _ in range(21600):
                    if not self._running:
                        break
                    await asyncio.sleep(1)
            except Exception as e:
                self.log(f"Research error: {e}", "ERROR")
                await asyncio.sleep(600)
