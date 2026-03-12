"""
Lead Dropout Analyzer — Diagnoses why leads don't convert to bookings.

For every lead that went cold or was lost, this agent:
1. Examines the full conversation history (SMS/email)
2. Checks follow-up timing (too slow? too fast?)
3. Analyzes message tone (too pushy? too passive?)
4. Checks pricing interactions (sticker shock? negotiation?)
5. Looks at lead source and quality (organic vs paid, intent level)
6. Writes a diagnosis to the shared brain

Runs as part of the analyst cycle or on-demand from the dashboard.
"""
import asyncio
import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from agents.base import BaseAgent
from agents.config import AGENT_DEFAULTS, BUSINESS, DB_PATH
from agents.shared_brain import SharedBrain


class LeadAnalyzer(BaseAgent):
    """Analyzes why leads drop off and recommends fixes."""

    def __init__(self):
        super().__init__("lead_analyzer", "analysis")
        self.brain = SharedBrain("lead_analyzer")

    def _get_lost_leads(self, limit: int = 20) -> list:
        """Get leads that didn't convert — lost, stale, or no response."""
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        leads = conn.execute("""
            SELECT * FROM leads
            WHERE booking_status IN ('lost', 'new_lead', 'new', 'contacted', 'auto_contacted', 'no_response')
            ORDER BY date_added DESC LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in leads]

    def _get_lead_conversations(self, lead_id: int) -> list:
        """Get all messages for a lead (SMS + email)."""
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        messages = []

        # Try lead_messages table
        try:
            rows = conn.execute(
                "SELECT * FROM lead_messages WHERE lead_id = ? ORDER BY sent_at ASC",
                (lead_id,)
            ).fetchall()
            messages.extend([dict(r) for r in rows])
        except Exception:
            pass

        # Try msg_conversations table
        try:
            rows = conn.execute(
                "SELECT * FROM msg_conversations WHERE lead_id = ? ORDER BY ts ASC",
                (lead_id,)
            ).fetchall()
            messages.extend([dict(r) for r in rows])
        except Exception:
            pass

        conn.close()
        return messages

    def _get_lead_events(self, lead_id: int) -> list:
        """Get all events for a lead."""
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM lead_events WHERE lead_id = ? ORDER BY ts ASC",
                (lead_id,)
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            conn.close()
            return []

    def _compute_lead_timing(self, lead: dict, messages: list, events: list) -> dict:
        """Compute timing metrics for a lead."""
        created = lead.get("date_added", "")
        first_contact = None
        last_contact = None
        total_messages = len(messages)
        our_messages = [m for m in messages if m.get("direction") == "outbound" or m.get("sender") == "bot"]
        their_replies = [m for m in messages if m.get("direction") == "inbound" or m.get("sender") != "bot"]

        if messages:
            first_contact = messages[0].get("sent_at") or messages[0].get("ts", "")
            last_contact = messages[-1].get("sent_at") or messages[-1].get("ts", "")

        # Time from lead creation to first contact
        response_time_hours = None
        if created and first_contact:
            try:
                t1 = datetime.fromisoformat(created.replace("Z", "+00:00").replace("+00:00", ""))
                t2 = datetime.fromisoformat(first_contact.replace("Z", "+00:00").replace("+00:00", ""))
                response_time_hours = (t2 - t1).total_seconds() / 3600
            except Exception:
                pass

        return {
            "total_messages": total_messages,
            "our_messages": len(our_messages),
            "their_replies": len(their_replies),
            "response_time_hours": response_time_hours,
            "first_contact": first_contact,
            "last_contact": last_contact,
            "total_events": len(events),
        }

    async def analyze_lead(self, lead: dict) -> dict:
        """Analyze a single lead and produce a diagnosis."""
        lead_id = lead.get("id")
        messages = self._get_lead_conversations(lead_id)
        events = self._get_lead_events(lead_id)
        timing = self._compute_lead_timing(lead, messages, events)

        # Build analysis prompt
        msg_summary = ""
        if messages:
            for m in messages[:10]:  # Last 10 messages
                direction = m.get("direction", m.get("sender", "unknown"))
                content = m.get("content", m.get("body", m.get("message", "")))
                ts = m.get("sent_at", m.get("ts", ""))
                msg_summary += f"  [{direction}] ({ts}) {content[:200]}\n"
        else:
            msg_summary = "  No conversation history found.\n"

        prompt = f"""Analyze this lead for {BUSINESS['name']} and diagnose why they didn't book.

LEAD DATA:
- Name: {lead.get('first_name', '')} {lead.get('last_name', '')}
- Source: {lead.get('source', 'unknown')}
- Event Type: {lead.get('event_type', 'unknown')}
- Status: {lead.get('booking_status', 'unknown')}
- Created: {lead.get('date_added', 'unknown')}
- Phone: {lead.get('phone', 'N/A')}

TIMING:
- Response time: {timing['response_time_hours'] if timing['response_time_hours'] is not None else 'N/A'} hours (goal: under 1 hour)
- Our messages: {timing['our_messages']}
- Their replies: {timing['their_replies']}
- Total events: {timing['total_events']}

CONVERSATION:
{msg_summary}

BUSINESS CONTEXT:
- Price: $1,100 standard, $1,000 discount, $900 minimum
- Product: 4-stall luxury restroom trailer

Produce a diagnosis as JSON:
{{
    "dropout_reason": "primary reason (pricing/timing/engagement/quality/competition/ghost/unknown)",
    "diagnosis": "2-3 sentence specific analysis",
    "was_too_pushy": true/false,
    "was_too_slow": true/false,
    "pricing_issue": true/false,
    "appearance_issue": true/false,
    "fix": "specific actionable recommendation",
    "lead_quality_score": 0.0-1.0,
    "followup_needed": true/false
}}"""

        result = await self.think(
            prompt,
            model=AGENT_DEFAULTS["smart_model"],
            max_tokens=400,
        )

        try:
            start = result.find("{")
            end = result.rfind("}") + 1
            if start >= 0 and end > start:
                diagnosis = json.loads(result[start:end])
                diagnosis["lead_id"] = lead_id
                diagnosis["lead_name"] = f"{lead.get('first_name', '')} {lead.get('last_name', '')}"
                diagnosis["timing"] = timing
                return diagnosis
        except (json.JSONDecodeError, Exception):
            pass

        return {
            "lead_id": lead_id,
            "dropout_reason": "unknown",
            "diagnosis": "Could not analyze — insufficient data",
            "timing": timing,
        }

    async def run_analysis(self, limit: int = 10) -> list:
        """Analyze all non-converting leads and produce diagnoses."""
        self.log("Starting lead dropout analysis...")
        leads = self._get_lost_leads(limit)
        diagnoses = []

        for lead in leads:
            try:
                diagnosis = await self.analyze_lead(lead)
                diagnoses.append(diagnosis)

                # Write to brain
                reason = diagnosis.get("dropout_reason", "unknown")
                self.brain.write_insight(
                    content=f"Lead #{diagnosis.get('lead_id')} ({diagnosis.get('lead_name', 'unknown')}): "
                            f"Dropped off — {reason}. {diagnosis.get('diagnosis', '')}",
                    insight_type="observation",
                    category="conversion",
                    confidence=diagnosis.get("lead_quality_score", 0.5),
                    data=diagnosis,
                )

                # If there's a fix, write recommendation
                if diagnosis.get("fix"):
                    self.brain.write_recommendation(
                        content=diagnosis["fix"],
                        category="conversion",
                        confidence=0.8,
                    )

            except Exception as e:
                self.log(f"Error analyzing lead {lead.get('id')}: {e}", "WARN")

        # Aggregate analysis
        if diagnoses:
            reasons = {}
            for d in diagnoses:
                r = d.get("dropout_reason", "unknown")
                reasons[r] = reasons.get(r, 0) + 1

            pushy_count = sum(1 for d in diagnoses if d.get("was_too_pushy"))
            slow_count = sum(1 for d in diagnoses if d.get("was_too_slow"))
            pricing_count = sum(1 for d in diagnoses if d.get("pricing_issue"))

            summary = (
                f"Lead dropout analysis: {len(diagnoses)} leads analyzed. "
                f"Reasons: {json.dumps(reasons)}. "
                f"Too pushy: {pushy_count}/{len(diagnoses)}. "
                f"Too slow: {slow_count}/{len(diagnoses)}. "
                f"Pricing issues: {pricing_count}/{len(diagnoses)}."
            )

            self.brain.write_insight(
                content=summary,
                insight_type="observation",
                category="conversion",
                confidence=0.9,
                data={
                    "reasons": reasons,
                    "pushy_count": pushy_count,
                    "slow_count": slow_count,
                    "pricing_count": pricing_count,
                    "total_analyzed": len(diagnoses),
                },
            )

            # Save patterns as knowledge
            if pushy_count > len(diagnoses) * 0.3:
                self.brain.save_knowledge(
                    problem="Too many leads think our outreach is too pushy",
                    solution="Reduce follow-up frequency. Lead with value (tips, event checklists) before mentioning our service. Wait for them to ask.",
                    category="engagement",
                )
            if slow_count > len(diagnoses) * 0.3:
                self.brain.save_knowledge(
                    problem="Response time is too slow — leads go cold",
                    solution="Aim for under 1 hour response time. Set up auto-response for new leads with helpful info.",
                    category="engagement",
                )
            if pricing_count > len(diagnoses) * 0.3:
                self.brain.save_knowledge(
                    problem="Pricing is a barrier for many leads",
                    solution="Lead with value proposition (luxury, not porta-potty). Show testimonials/photos first. Only discuss price after building value.",
                    category="pricing",
                )

        self.log(f"Analyzed {len(diagnoses)} leads")
        self.brain.log_activity("lead_analysis", f"Analyzed {len(diagnoses)} leads — {len(diagnoses)} diagnoses")

        self._state["actions_taken"] = self._state.get("actions_taken", 0) + 1
        self._state["leads_analyzed"] = self._state.get("leads_analyzed", 0) + len(diagnoses)
        self.save_state()

        return diagnoses

    def get_dropout_summary(self) -> dict:
        """Get aggregate dropout reasons from the brain."""
        insights = self.brain.read_insights(category="conversion", limit=50)
        reasons = {}
        fixes = []
        for i in insights:
            data = json.loads(i.get("data_json", "{}")) if isinstance(i.get("data_json"), str) else i.get("data_json", {})
            if "reasons" in data:
                for k, v in data["reasons"].items():
                    reasons[k] = reasons.get(k, 0) + v
            if i.get("insight_type") == "recommendation":
                fixes.append(i["content"])

        return {
            "dropout_reasons": reasons,
            "recommended_fixes": fixes[:10],
            "total_analyzed": sum(reasons.values()),
        }
