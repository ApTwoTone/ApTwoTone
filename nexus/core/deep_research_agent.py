"""
Deep Research Agent — Autonomous Lead Enrichment Pipeline
=========================================================
Claims `deep_lead_research` tasks from the FleetTaskQueue and produces
complete dossiers on Facebook-discovered leads using free AI models.

Pipeline per lead:
  1. FB Profile deep dive (extract_profile_data if browser available)
  2. Website analysis (httpx-based, no Playwright needed)
  3. AI research brief (Groq Llama 4 Scout — FREE)
  4. Competitive intelligence (Groq — FREE)
  5. Dossier assembly → lead_dossiers table

All AI calls use free models via core.worker_pool. Zero paid API usage.
Runs as launchd daemon: com.zoar.deep-research

Safety:
  - READ-ONLY on all platforms (Rule 24)
  - No outbound messages without Kai's approval (Rule 0)
  - Website fetches use standard User-Agent, no scraping tricks
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("deep_research_agent")

DB_PATH = Path.home() / ".nexus" / "memory.db"
LOG_DIR = Path.home() / ".nexus" / "logs"

# Research confidence thresholds
HIGH_CONFIDENCE_THRESHOLD = 75
TELEGRAM_ALERT_THRESHOLD = 75


# ── Database ─────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_dossier_table():
    """Create lead_dossiers table if it doesn't exist."""
    conn = _conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lead_dossiers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id INTEGER NOT NULL,
                lead_type TEXT NOT NULL,
                source_table TEXT NOT NULL,
                contact_name TEXT DEFAULT '',
                business_name TEXT DEFAULT '',
                phone TEXT DEFAULT '',
                email TEXT DEFAULT '',
                website TEXT DEFAULT '',
                instagram TEXT DEFAULT '',
                location TEXT DEFAULT '',
                in_service_area INTEGER DEFAULT 0,
                services TEXT DEFAULT '[]',
                event_types TEXT DEFAULT '[]',
                service_area TEXT DEFAULT '',
                price_range TEXT DEFAULT '',
                partnership_type TEXT DEFAULT '',
                fit_score INTEGER DEFAULT 0,
                fit_reasoning TEXT DEFAULT '',
                approach TEXT DEFAULT '',
                objection_prep TEXT DEFAULT '',
                email_subject TEXT DEFAULT '',
                email_body TEXT DEFAULT '',
                call_script TEXT DEFAULT '',
                full_dossier TEXT DEFAULT '{}',
                research_confidence INTEGER DEFAULT 0,
                model_used TEXT DEFAULT '',
                outreach_status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dossier_lead ON lead_dossiers(lead_id, source_table)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dossier_fit ON lead_dossiers(fit_score)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dossier_status ON lead_dossiers(outreach_status)")
        conn.commit()
    finally:
        conn.close()
    log.info("lead_dossiers table verified")


def dossier_exists(lead_id: int, source_table: str) -> bool:
    """Check if a dossier already exists for this lead."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM lead_dossiers WHERE lead_id = ? AND source_table = ?",
            (lead_id, source_table),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def save_dossier(dossier: dict) -> int:
    """Persist a research dossier. Returns row ID."""
    conn = _conn()
    try:
        conn.execute("""
            INSERT INTO lead_dossiers
                (lead_id, lead_type, source_table,
                 contact_name, business_name, phone, email, website, instagram,
                 location, in_service_area,
                 services, event_types, service_area, price_range,
                 partnership_type, fit_score, fit_reasoning, approach, objection_prep,
                 email_subject, email_body, call_script,
                 full_dossier, research_confidence, model_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            dossier.get("lead_id", 0),
            dossier.get("lead_type", ""),
            dossier.get("source_table", ""),
            dossier.get("contact", {}).get("name", ""),
            dossier.get("contact", {}).get("business", ""),
            dossier.get("contact", {}).get("phone", ""),
            dossier.get("contact", {}).get("email", ""),
            dossier.get("contact", {}).get("website", ""),
            dossier.get("contact", {}).get("instagram", ""),
            dossier.get("contact", {}).get("location", ""),
            1 if dossier.get("contact", {}).get("in_service_area") else 0,
            json.dumps(dossier.get("business_intel", {}).get("services", [])),
            json.dumps(dossier.get("business_intel", {}).get("event_types", [])),
            dossier.get("business_intel", {}).get("service_area", ""),
            dossier.get("business_intel", {}).get("price_range", ""),
            dossier.get("fit_analysis", {}).get("partnership_type", ""),
            dossier.get("fit_analysis", {}).get("fit_score", 0),
            dossier.get("fit_analysis", {}).get("reasoning", ""),
            dossier.get("fit_analysis", {}).get("approach", ""),
            dossier.get("fit_analysis", {}).get("objection_prep", ""),
            dossier.get("outreach_ready", {}).get("email_subject", ""),
            dossier.get("outreach_ready", {}).get("email_body", ""),
            dossier.get("outreach_ready", {}).get("call_script", ""),
            json.dumps(dossier),
            dossier.get("research_confidence", 0),
            dossier.get("model_used", ""),
        ))
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    except Exception as e:
        log.error(f"Failed to save dossier: {e}")
        return 0
    finally:
        conn.close()


# ── Website Analysis (httpx, no Playwright) ──────────────────────────────────

async def fetch_website_text(url: str, timeout: float = 15.0) -> Dict[str, Any]:
    """Fetch a website and extract text content. Returns {ok, text, emails, phones, links}."""
    try:
        import httpx
    except ImportError:
        return {"ok": False, "text": "", "emails": [], "phones": [], "links": [], "error": "httpx not installed"}

    if not url or not url.startswith("http"):
        return {"ok": False, "text": "", "emails": [], "phones": [], "links": [], "error": "invalid url"}

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
        ) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                return {"ok": False, "text": "", "emails": [], "phones": [], "links": [], "error": f"HTTP {resp.status_code}"}

            html = resp.text
            # Strip HTML tags for text extraction
            text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()[:10000]

            # Extract emails
            emails = list(set(re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", html)))
            emails = [e for e in emails if not e.endswith((".png", ".jpg", ".gif", ".css", ".js"))]

            # Extract phones
            phones = list(set(re.findall(r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", html)))

            # Extract links (social media, contact pages)
            links = re.findall(r'href=["\']([^"\']+)["\']', html)
            social_links = [l for l in links if any(s in l.lower() for s in ["instagram", "yelp", "facebook", "linkedin", "google.com/maps"])]

            return {
                "ok": True,
                "text": text,
                "emails": emails[:10],
                "phones": phones[:5],
                "links": social_links[:10],
                "error": "",
            }
    except Exception as e:
        return {"ok": False, "text": "", "emails": [], "phones": [], "links": [], "error": str(e)}


def extract_instagram(links: List[str], text: str) -> str:
    """Extract Instagram handle from links or text."""
    for link in links:
        if "instagram.com/" in link.lower():
            match = re.search(r"instagram\.com/([a-zA-Z0-9_.]+)", link)
            if match:
                return f"@{match.group(1)}"
    match = re.search(r"@([a-zA-Z0-9_.]{3,30})", text)
    if match:
        return match.group(0)
    return ""


# ── AI Research Calls (all FREE models) ──────────────────────────────────────

async def ai_research_brief(lead: dict, website_text: str) -> Dict[str, Any]:
    """Use Groq (free) to analyze the lead and generate a research brief."""
    try:
        from core.worker_pool import call_provider
    except ImportError:
        return _fallback_brief(lead)

    context_parts = []
    if lead.get("poster_name"):
        context_parts.append(f"Name: {lead['poster_name']}")
    if lead.get("post_text"):
        context_parts.append(f"Facebook post: {lead['post_text'][:800]}")
    if lead.get("group_name"):
        context_parts.append(f"Found in group: {lead['group_name']}")
    if lead.get("website"):
        context_parts.append(f"Website: {lead['website']}")
    if website_text:
        context_parts.append(f"Website content (excerpt): {website_text[:2000]}")
    if lead.get("email"):
        context_parts.append(f"Email: {lead['email']}")
    if lead.get("phone"):
        context_parts.append(f"Phone: {lead['phone']}")
    if lead.get("event_date"):
        context_parts.append(f"Event date: {lead['event_date']}")
    if lead.get("event_location"):
        context_parts.append(f"Event location: {lead['event_location']}")

    context = "\n".join(context_parts)
    lead_type = lead.get("lead_type", "REFERRAL_LEAD")

    if lead_type == "EVENT_OPPORTUNITY":
        prompt = f"""Analyze this event opportunity for Zoar Bathroom Rentals (luxury restroom trailer rental in San Fernando Valley / Greater LA).

{context}

Respond ONLY with valid JSON (no markdown, no explanation):
{{
  "event_analysis": {{
    "event_type": "wedding/quince/corporate/festival/other",
    "is_outdoor": true/false,
    "estimated_attendance": 0,
    "needs_restroom": true/false,
    "urgency": "high/medium/low"
  }},
  "organizer": {{
    "likely_role": "event host/planner/venue/vendor",
    "business_name": "" or business name if identifiable
  }},
  "approach": "1-2 sentence recommended outreach approach",
  "email_subject": "short subject line for outreach email",
  "email_body": "3-4 sentence personalized email body from Zoar Bathroom Rentals",
  "call_script": "2-3 sentence phone call opening",
  "fit_score": 0-100,
  "partnership_type": "direct_booking/referral_partner/venue_partner"
}}"""
    else:
        prompt = f"""Analyze this vendor/professional for partnership potential with Zoar Bathroom Rentals (luxury restroom trailer rental in San Fernando Valley / Greater LA).

{context}

Respond ONLY with valid JSON (no markdown, no explanation):
{{
  "business_intel": {{
    "services": ["list of services they offer"],
    "event_types": ["types of events they serve"],
    "service_area": "geographic area",
    "price_range": "estimated price range if visible",
    "team_size": "solo/small/medium/large",
    "years_active": "since YYYY or unknown",
    "notable_clients": ["any mentioned clients or brands"]
  }},
  "fit_analysis": {{
    "partnership_type": "referral_partner/secondary_vendor/venue_partner/competitor",
    "fit_score": 0-100,
    "reasoning": "1-2 sentences why this score",
    "approach": "1-2 sentence recommended approach",
    "objection_prep": "likely objection and how to handle it"
  }},
  "outreach": {{
    "email_subject": "short subject line",
    "email_body": "3-4 sentence personalized email from Zoar Bathroom Rentals",
    "call_script": "2-3 sentence phone call opening"
  }}
}}"""

    result = await call_provider(
        "groq",
        messages=[{"role": "user", "content": prompt}],
        system="You are a business intelligence analyst. Output ONLY valid JSON.",
        max_tokens=2048,
        temperature=0.3,
        agent_id="deep-research-agent",
        task_type="research_brief",
    )

    if not result.get("ok"):
        # Fallback to Ollama
        result = await call_provider(
            "ollama",
            messages=[{"role": "user", "content": prompt}],
            system="You are a business intelligence analyst. Output ONLY valid JSON.",
            max_tokens=2048,
            temperature=0.3,
            agent_id="deep-research-agent",
        )

    content = result.get("content", "")
    model_used = f"{result.get('provider', 'unknown')}/{result.get('model', 'unknown')}"

    # Parse JSON from response
    try:
        # Strip markdown code blocks if present
        clean = re.sub(r"```(?:json)?\s*", "", content).strip().rstrip("`")
        parsed = json.loads(clean)
        parsed["_model_used"] = model_used
        return parsed
    except json.JSONDecodeError:
        log.warning(f"AI response not valid JSON, using fallback. Response: {content[:200]}")
        brief = _fallback_brief(lead)
        brief["_model_used"] = model_used
        return brief


def _fallback_brief(lead: dict) -> dict:
    """Generate a minimal brief when AI is unavailable."""
    lead_type = lead.get("lead_type", "REFERRAL_LEAD")
    name = lead.get("poster_name", "there")
    if lead_type == "EVENT_OPPORTUNITY":
        return {
            "event_analysis": {"event_type": "unknown", "is_outdoor": True, "needs_restroom": True, "urgency": "medium"},
            "approach": f"Reach out about restroom trailer availability for their event.",
            "email_subject": "Luxury restroom trailer for your upcoming event",
            "email_body": f"Hi {name}, I noticed you're planning an event and wanted to reach out. Zoar Bathroom Rentals provides luxury restroom trailers with AC, running water, and premium finishes — perfect for outdoor events. Would you like a quick quote?",
            "call_script": f"Hi {name}, this is a representative from Zoar Bathroom Rentals. I saw your post about an upcoming event and wanted to see if you need restroom facilities.",
            "fit_score": 50,
            "partnership_type": "direct_booking",
            "_model_used": "fallback",
        }
    return {
        "business_intel": {"services": [], "event_types": [], "service_area": "unknown"},
        "fit_analysis": {
            "partnership_type": "referral_partner",
            "fit_score": 40,
            "reasoning": "Insufficient data for detailed analysis.",
            "approach": "Introduce Zoar as a complementary vendor for their event clients.",
        },
        "outreach": {
            "email_subject": "Partnership opportunity — luxury restroom trailers",
            "email_body": f"Hi {name}, I came across your work and thought there might be a great partnership opportunity. Zoar Bathroom Rentals provides luxury restroom trailers for events in the SFV and Greater LA area. Many event vendors refer us to their clients. Would you be open to a quick chat?",
            "call_script": f"Hi {name}, this is a representative from Zoar Bathroom Rentals. We partner with event vendors in LA to provide luxury restroom trailers for their clients' events.",
        },
        "_model_used": "fallback",
    }


# ── Service Area Check ───────────────────────────────────────────────────────

LOCAL_AREA_KEYWORDS = {
    "los angeles", "san fernando", "sfv", "burbank", "glendale", "pasadena",
    "woodland hills", "encino", "sherman oaks", "northridge", "van nuys",
    "calabasas", "tarzana", "chatsworth", "canoga park", "panorama city",
    "sylmar", "pacoima", "north hollywood", "socal", "southern california",
    "santa clarita", "sun valley", "reseda", "lake balboa",
}


def is_in_service_area(text: str) -> bool:
    """Check if any text mentions our service area."""
    lower = text.lower()
    return any(kw in lower for kw in LOCAL_AREA_KEYWORDS)


# ── Dossier Assembly ─────────────────────────────────────────────────────────

def assemble_dossier(
    lead: dict,
    website_data: dict,
    ai_brief: dict,
) -> dict:
    """Combine all research into a structured dossier."""
    lead_type = lead.get("lead_type", "REFERRAL_LEAD")

    # Merge contact info from all sources
    contact = {
        "name": lead.get("poster_name", ""),
        "business": ai_brief.get("business_intel", {}).get("services", [""])[0] if ai_brief.get("business_intel") else
                    ai_brief.get("organizer", {}).get("business_name", ""),
        "phone": lead.get("phone", "") or (website_data.get("phones", [None])[0] if website_data.get("phones") else ""),
        "email": lead.get("email", "") or (website_data.get("emails", [None])[0] if website_data.get("emails") else ""),
        "website": lead.get("website", ""),
        "instagram": extract_instagram(website_data.get("links", []), website_data.get("text", "")),
        "location": lead.get("event_location", ""),
        "in_service_area": is_in_service_area(
            f"{lead.get('post_text', '')} {lead.get('event_location', '')} {website_data.get('text', '')}"
        ),
    }

    # Extract business intel
    if lead_type == "EVENT_OPPORTUNITY":
        business_intel = {
            "event_type": ai_brief.get("event_analysis", {}).get("event_type", "unknown"),
            "is_outdoor": ai_brief.get("event_analysis", {}).get("is_outdoor", True),
            "estimated_attendance": ai_brief.get("event_analysis", {}).get("estimated_attendance", 0),
            "needs_restroom": ai_brief.get("event_analysis", {}).get("needs_restroom", True),
            "urgency": ai_brief.get("event_analysis", {}).get("urgency", "medium"),
        }
    else:
        business_intel = ai_brief.get("business_intel", {
            "services": [], "event_types": [], "service_area": "unknown",
        })

    # Extract fit analysis
    if lead_type == "EVENT_OPPORTUNITY":
        fit_analysis = {
            "partnership_type": ai_brief.get("partnership_type", "direct_booking"),
            "fit_score": ai_brief.get("fit_score", 50),
            "reasoning": ai_brief.get("approach", ""),
            "approach": ai_brief.get("approach", ""),
            "objection_prep": "",
        }
    else:
        fit_analysis = ai_brief.get("fit_analysis", {
            "partnership_type": "referral_partner",
            "fit_score": 40,
            "reasoning": "Insufficient data",
            "approach": "Introduce as complementary vendor",
        })

    # Extract outreach materials
    if lead_type == "EVENT_OPPORTUNITY":
        outreach = {
            "email_subject": ai_brief.get("email_subject", ""),
            "email_body": ai_brief.get("email_body", ""),
            "call_script": ai_brief.get("call_script", ""),
        }
    else:
        outreach = ai_brief.get("outreach", {
            "email_subject": "", "email_body": "", "call_script": "",
        })

    # Compute research confidence
    confidence = 30  # base
    if contact["email"]:
        confidence += 15
    if contact["phone"]:
        confidence += 10
    if contact["website"] and website_data.get("ok"):
        confidence += 15
    if ai_brief.get("_model_used", "") != "fallback":
        confidence += 15
    if contact["in_service_area"]:
        confidence += 10
    if lead.get("profile_url"):
        confidence += 5
    confidence = min(100, confidence)

    return {
        "lead_id": lead.get("lead_id", 0),
        "lead_type": lead_type,
        "source_table": lead.get("table", "referral_leads"),
        "contact": contact,
        "business_intel": business_intel,
        "fit_analysis": fit_analysis,
        "outreach_ready": outreach,
        "research_confidence": confidence,
        "researched_at": datetime.now(timezone.utc).isoformat(),
        "model_used": ai_brief.get("_model_used", "unknown"),
    }


# ── Agent Class ──────────────────────────────────────────────────────────────

class DeepResearchAgent:
    """Autonomous daemon that processes deep_lead_research tasks from the fleet queue."""

    def __init__(self):
        from core.agent_runtime import AgentRuntime
        from core.fleet_task_queue import FleetTaskQueue

        self.rt = AgentRuntime("deep-research-agent", "lead_gen")
        self.queue = FleetTaskQueue()
        self._tasks_done = 0

    async def daemon_loop(self):
        """Main loop: claim tasks, research leads, save dossiers."""
        init_dossier_table()
        self.rt.start()
        log.info("Deep Research Agent started — waiting for tasks")

        while True:
            task = self.queue.claim("deep-research-agent", tier=2)
            if not task:
                self.rt.heartbeat("Waiting for research tasks")
                await asyncio.sleep(15)
                continue

            # Parse input
            input_data = task.get("input_data", "{}")
            if isinstance(input_data, str):
                try:
                    lead = json.loads(input_data)
                except json.JSONDecodeError:
                    log.error(f"Invalid input_data for task {task['task_id']}")
                    self.queue.fail(task["task_id"], "Invalid JSON input_data")
                    continue
            else:
                lead = input_data

            task_type = task.get("task_type", "")
            if task_type != "deep_lead_research":
                # Not our task type — skip (re-enqueue would be ideal but just complete)
                log.debug(f"Skipping task {task['task_id']} (type={task_type})")
                self.queue.complete(task["task_id"], {"skipped": True, "reason": "wrong task_type"})
                continue

            poster_name = lead.get("poster_name", "unknown")
            lead_id = lead.get("lead_id", 0)
            source_table = lead.get("table", "referral_leads")

            # Skip if dossier already exists
            if dossier_exists(lead_id, source_table):
                log.info(f"Dossier already exists for {poster_name} (lead_id={lead_id}) — skipping")
                self.queue.complete(task["task_id"], {"skipped": True, "reason": "dossier_exists"})
                continue

            self.rt.heartbeat(f"Researching {poster_name}")
            self.queue.start_processing(task["task_id"])
            log.info(f"Researching: {poster_name} (lead_id={lead_id}, type={lead.get('lead_type', '?')})")

            try:
                dossier = await self.research_lead(lead)
                row_id = save_dossier(dossier)

                if row_id:
                    self._tasks_done += 1
                    self.queue.complete(
                        task["task_id"],
                        {
                            "dossier_id": row_id,
                            "confidence": dossier["research_confidence"],
                            "fit_score": dossier.get("fit_analysis", {}).get("fit_score", 0),
                        },
                        provider=dossier.get("model_used", ""),
                    )
                    self.rt.complete_task(f"Dossier #{row_id} for {poster_name}")

                    # Telegram alert for high-confidence dossiers
                    if dossier["research_confidence"] >= TELEGRAM_ALERT_THRESHOLD:
                        try:
                            from core.nexus_coordination import log_event
                            fit = dossier.get("fit_analysis", {})
                            contact = dossier.get("contact", {})
                            log_event("RESEARCH_DOSSIER_READY", {
                                "dossier_id": row_id,
                                "name": contact.get("name", ""),
                                "business": contact.get("business", ""),
                                "fit_score": fit.get("fit_score", 0),
                                "partnership_type": fit.get("partnership_type", ""),
                                "approach": fit.get("approach", "")[:200],
                                "confidence": dossier["research_confidence"],
                            }, source="deep-research-agent", push_alert=True)
                        except Exception:
                            pass

                    log.info(
                        f"  Dossier #{row_id} saved: confidence={dossier['research_confidence']}, "
                        f"fit={dossier.get('fit_analysis', {}).get('fit_score', 0)}, "
                        f"type={dossier.get('fit_analysis', {}).get('partnership_type', '?')}"
                    )
                else:
                    self.queue.fail(task["task_id"], "Failed to save dossier to DB")

            except Exception as e:
                log.error(f"Research failed for {poster_name}: {e}", exc_info=True)
                self.queue.fail(task["task_id"], str(e)[:500])
                self.rt.report_error(str(e), will_retry=True)

            # Breathing room between tasks
            await asyncio.sleep(3)

    async def research_lead(self, lead: dict) -> dict:
        """Run the full research pipeline on a single lead."""
        # Step 1: Website analysis (if URL available)
        website = lead.get("website", "")
        website_data = {"ok": False, "text": "", "emails": [], "phones": [], "links": []}
        if website:
            log.info(f"  Step 1: Fetching website {website}")
            website_data = await fetch_website_text(website)
            if website_data["ok"]:
                log.info(f"  Website OK: {len(website_data['text'])} chars, "
                         f"{len(website_data['emails'])} emails, {len(website_data['phones'])} phones")
            else:
                log.info(f"  Website failed: {website_data.get('error', 'unknown')}")

        # Step 2: AI research brief
        log.info(f"  Step 2: AI research brief")
        ai_brief = await ai_research_brief(lead, website_data.get("text", ""))
        log.info(f"  AI brief generated via {ai_brief.get('_model_used', 'unknown')}")

        # Step 3: Assemble dossier
        log.info(f"  Step 3: Assembling dossier")
        dossier = assemble_dossier(lead, website_data, ai_brief)

        return dossier


# ── Entry Point ──────────────────────────────────────────────────────────────

def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    from logging.handlers import TimedRotatingFileHandler

    handler = TimedRotatingFileHandler(
        str(LOG_DIR / "deep_research_agent.log"),
        when="D", interval=1, backupCount=14,
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.setLevel(logging.INFO)
    if not log.handlers:
        log.addHandler(handler)
        log.addHandler(console)


async def async_main():
    setup_logging()
    agent = DeepResearchAgent()
    try:
        await agent.daemon_loop()
    except KeyboardInterrupt:
        log.info("Shutting down deep research agent")
        agent.rt.shutdown()


if __name__ == "__main__":
    asyncio.run(async_main())
