"""
Analyst Agent — The brain that ties everything together.

Reads data from ALL agents + CRM + metrics + conversations, then:
1. Computes marketing metrics (CPL, CPC, CTR, CPM, etc.)
2. Analyzes lead dropout patterns
3. Produces cross-agent insights and recommendations
4. Facilitates brainstorming by posting findings for other agents
5. Diagnoses what's working and what's not

Runs every 2 hours. Writes everything to the shared brain.
"""
import asyncio
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from agents.base import BaseAgent
from agents.config import AGENT_DEFAULTS, BUSINESS, DB_PATH, AGENT_STATE_DIR
from agents.shared_brain import SharedBrain
from agents.metrics_tracker import MetricsTracker
from agents.lead_analyzer import LeadAnalyzer


class AnalystAgent(BaseAgent):
    """Reads everything, produces actionable insights."""

    def __init__(self):
        super().__init__("analyst", "analysis")
        self.brain = SharedBrain("analyst")
        self.metrics = MetricsTracker()
        self.lead_analyzer = LeadAnalyzer()

    # ── Data Collection ────────────────────────────────────────────────────

    def _get_crm_snapshot(self) -> dict:
        """Pull key metrics from CRM."""
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row

            total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
            by_stage = {}
            for r in conn.execute("SELECT booking_status, COUNT(*) as cnt FROM leads GROUP BY booking_status"):
                by_stage[r["booking_status"]] = r["cnt"]

            by_source = {}
            for r in conn.execute("SELECT source, COUNT(*) as cnt FROM leads GROUP BY source"):
                by_source[r["source"]] = r["cnt"]

            recent_leads = conn.execute(
                "SELECT id, first_name, last_name, phone, booking_status, source, event_type, date_added "
                "FROM leads ORDER BY date_added DESC LIMIT 10"
            ).fetchall()

            # Conversion funnel
            booked = conn.execute("SELECT COUNT(*) FROM leads WHERE booking_status = 'booked'").fetchone()[0]
            quote_sent = conn.execute("SELECT COUNT(*) FROM leads WHERE booking_status = 'quote_sent'").fetchone()[0]
            lost = conn.execute("SELECT COUNT(*) FROM leads WHERE booking_status = 'lost'").fetchone()[0]

            # Response time analysis
            avg_response = None
            try:
                rows = conn.execute("""
                    SELECT l.date_added, MIN(m.sent_at) as first_contact
                    FROM leads l
                    LEFT JOIN lead_messages m ON l.id = m.lead_id AND m.direction = 'outbound'
                    WHERE m.sent_at IS NOT NULL
                    GROUP BY l.id
                """).fetchall()
                if rows:
                    times = []
                    for r in rows:
                        try:
                            t1 = datetime.fromisoformat(r[0])
                            t2 = datetime.fromisoformat(r[1])
                            times.append((t2 - t1).total_seconds() / 3600)
                        except Exception:
                            pass
                    if times:
                        avg_response = round(sum(times) / len(times), 1)
            except Exception:
                pass

            conn.close()
            return {
                "total_leads": total,
                "by_stage": by_stage,
                "by_source": by_source,
                "booked": booked,
                "quote_sent": quote_sent,
                "lost": lost,
                "conversion_rate": round(booked / max(total, 1) * 100, 1),
                "avg_response_time_hours": avg_response,
                "recent": [dict(r) for r in recent_leads],
            }
        except Exception as e:
            self.log(f"CRM snapshot error: {e}", "WARN")
            return {}

    def _get_content_stats(self) -> dict:
        """Get content agent performance."""
        queue_file = AGENT_STATE_DIR / "content_queue.json"
        templates_file = AGENT_STATE_DIR / "comment_templates.json"
        content_state = AGENT_STATE_DIR / "content_gen.json"

        stats = {"posts_queued": 0, "templates": 0, "cost": 0}
        if queue_file.exists():
            try:
                data = json.loads(queue_file.read_text())
                stats["posts_queued"] = len(data) if isinstance(data, list) else 0
            except: pass
        if templates_file.exists():
            try:
                data = json.loads(templates_file.read_text())
                stats["templates"] = len(data) if isinstance(data, list) else 0
            except: pass
        if content_state.exists():
            try:
                data = json.loads(content_state.read_text())
                stats["cost"] = data.get("cost", 0)
                stats["actions"] = data.get("actions_taken", 0)
            except: pass
        return stats

    def _get_lead_research_stats(self) -> dict:
        """Get lead finder performance."""
        leads_file = AGENT_STATE_DIR / "discovered_leads.json"
        research_state = AGENT_STATE_DIR / "lead_finder.json"

        stats = {"discovered": 0, "by_category": {}, "cost": 0}
        if leads_file.exists():
            try:
                data = json.loads(leads_file.read_text())
                stats["discovered"] = len(data)
                for lead in data:
                    cat = lead.get("query_type", "unknown")
                    stats["by_category"][cat] = stats["by_category"].get(cat, 0) + 1
            except: pass
        if research_state.exists():
            try:
                data = json.loads(research_state.read_text())
                stats["cost"] = data.get("cost", 0)
                stats["cycles"] = data.get("actions_taken", 0)
            except: pass
        return stats

    def _get_conversation_stats(self) -> dict:
        """Analyze SMS/email conversation patterns."""
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row

            stats = {"total_conversations": 0, "avg_messages_per_lead": 0, "response_rate": 0}

            # Total messages
            try:
                total_msgs = conn.execute("SELECT COUNT(*) FROM lead_messages").fetchone()[0]
                outbound = conn.execute(
                    "SELECT COUNT(*) FROM lead_messages WHERE direction = 'outbound'"
                ).fetchone()[0]
                inbound = conn.execute(
                    "SELECT COUNT(*) FROM lead_messages WHERE direction = 'inbound'"
                ).fetchone()[0]
                leads_with_msgs = conn.execute(
                    "SELECT COUNT(DISTINCT lead_id) FROM lead_messages"
                ).fetchone()[0]
                stats["total_messages"] = total_msgs
                stats["outbound"] = outbound
                stats["inbound"] = inbound
                stats["response_rate"] = round(inbound / max(outbound, 1) * 100, 1)
                stats["avg_messages_per_lead"] = round(total_msgs / max(leads_with_msgs, 1), 1)
                stats["leads_with_conversations"] = leads_with_msgs
            except Exception:
                pass

            conn.close()
            return stats
        except Exception as e:
            self.log(f"Conversation stats error: {e}", "WARN")
            return {}

    def _get_ad_config(self) -> dict:
        """Read Facebook ad campaign config."""
        ads_file = Path(__file__).parent.parent / "scripts" / "facebook_ads.json"
        if ads_file.exists():
            try:
                return json.loads(ads_file.read_text())
            except: pass
        return {}

    # ── Analysis ───────────────────────────────────────────────────────

    async def analyze(self) -> list:
        """Run full analysis cycle. Returns list of insights produced."""
        self.log("Starting analysis cycle...")
        insights = []

        # Collect all data
        crm = self._get_crm_snapshot()
        content = self._get_content_stats()
        research = self._get_lead_research_stats()
        conversations = self._get_conversation_stats()
        ads = self._get_ad_config()

        # Compute marketing metrics
        marketing_metrics = self.metrics.compute_all_metrics()
        self.metrics.write_metrics_to_brain(marketing_metrics)

        # Run lead dropout analysis
        try:
            diagnoses = await self.lead_analyzer.run_analysis(limit=5)
            dropout_summary = self.lead_analyzer.get_dropout_summary()
        except Exception as e:
            self.log(f"Lead analysis error: {e}", "WARN")
            diagnoses = []
            dropout_summary = {}

        # Read brain context + open problems
        brain_summary = self.brain.get_summary()
        open_problems = self.brain.get_open_problems()
        knowledge = self.brain.get_all_knowledge(limit=10)

        # Build comprehensive analysis prompt
        prompt = f"""You are the chief marketing analyst for {BUSINESS['name']}, a luxury restroom trailer rental company in SoCal.

CURRENT DATA:
- CRM: {json.dumps(crm, default=str)}
- Content Agent: {json.dumps(content)}
- Lead Research: {json.dumps(research)}
- Conversations: {json.dumps(conversations)}
- Marketing Metrics: {json.dumps(marketing_metrics)}
- Ad Campaigns: {len(ads.get('ads', []))} variations configured
- Brain: {json.dumps(brain_summary)}
- Lead Dropout Analysis: {json.dumps(dropout_summary)}
- Open Problems: {json.dumps([p['content'] for p in open_problems])}
- Knowledge Base: {len(knowledge)} entries

Analyze ALL this data and produce 5-7 actionable insights. Include:

1. **Pipeline health** — Are leads flowing? Where's the bottleneck?
2. **Content effectiveness** — What content types work? What should change?
3. **Ad performance** — CTR, CPL, what's working?
4. **Lead quality** — Where do best leads come from? Why do they drop off?
5. **Response/engagement** — Are we responding fast enough? Too pushy?
6. **Cross-agent synergy** — How can agents help each other?
7. **Pricing** — Any patterns in price sensitivity?

For each insight:
- Reference ACTUAL numbers from the data
- Give a specific, actionable fix (not generic advice)
- Rate confidence 0.0-1.0

Format as JSON array:
[{{"insight": "...", "action": "...", "confidence": 0.8, "category": "ads|content|leads|engagement|conversion|pricing|strategy"}}]"""

        result = await self.think(
            prompt,
            model=AGENT_DEFAULTS["smart_model"],
            max_tokens=1200,
        )

        try:
            start = result.find("[")
            end = result.rfind("]") + 1
            if start >= 0 and end > start:
                parsed = json.loads(result[start:end])
                for item in parsed:
                    self.brain.write_insight(
                        content=item["insight"],
                        insight_type="observation",
                        category=item.get("category", "general"),
                        confidence=item.get("confidence", 0.5),
                        data={"action": item.get("action", "")},
                    )
                    if item.get("action"):
                        self.brain.write_recommendation(
                            content=item["action"],
                            category=item.get("category", "general"),
                            confidence=item.get("confidence", 0.7),
                        )
                    insights.append(item)
                self.log(f"Produced {len(insights)} insights")
        except json.JSONDecodeError:
            self.log("Failed to parse analysis results", "WARN")

        # Check for open problems and try to solve them
        if open_problems:
            for problem in open_problems[:3]:
                solutions = self.brain.find_solution(problem["content"])
                if solutions:
                    self.brain.post_solution(
                        problem["thread_id"],
                        f"Based on knowledge base: {solutions[0]['solution']}",
                    )

        # Write summary metrics
        if crm:
            self.brain.record_metric("total_leads", crm.get("total_leads", 0), "pipeline")
            self.brain.record_metric("conversion_rate", crm.get("conversion_rate", 0), "pipeline")
            self.brain.record_metric("booked", crm.get("booked", 0), "pipeline")
            self.brain.record_metric("lost", crm.get("lost", 0), "pipeline")
            if crm.get("avg_response_time_hours") is not None:
                self.brain.record_metric("avg_response_time_hours",
                                         crm["avg_response_time_hours"], "engagement")
        if research:
            self.brain.record_metric("discovered_leads", research.get("discovered", 0), "leads")
        if content:
            self.brain.record_metric("posts_queued", content.get("posts_queued", 0), "content")
        if conversations:
            self.brain.record_metric("response_rate",
                                     conversations.get("response_rate", 0), "engagement")

        self._state["insights_produced"] = self._state.get("insights_produced", 0) + len(insights)
        self._state["actions_taken"] = self._state.get("actions_taken", 0) + 1
        self._state["leads_analyzed"] = self._state.get("leads_analyzed", 0) + len(diagnoses)
        self.save_state()

        self.brain.log_activity("analysis_complete",
                                f"Produced {len(insights)} insights, analyzed {len(diagnoses)} leads")

        return insights

    async def run_analysis_cycle(self):
        """Single analysis cycle."""
        return await self.analyze()

    async def start(self):
        """Run analyst on a schedule (every 2 hours)."""
        await super().start()
        while self._running:
            try:
                await self.run_analysis_cycle()
                # Every 2 hours
                for _ in range(7200):
                    if not self._running:
                        break
                    await asyncio.sleep(1)
            except Exception as e:
                self.log(f"Analysis error: {e}", "ERROR")
                await asyncio.sleep(600)
