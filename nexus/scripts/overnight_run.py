#!/usr/bin/env python3
"""
NEXUS Overnight Agent Run — Launch all 19 agents with specific assignments.

Each agent gets a task routed to the appropriate free model based on complexity.
Results are saved to ~/.nexus/session_logs/ and a summary sent via Telegram at 8AM.

Usage:
    python3 scripts/overnight_run.py
"""
import os
import sys
import json
import time
import logging
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.orchestrator import (
    call_model, get_available_model, increment_quota, load_config,
    save_session, send_telegram_batch, MODELS, LOG_DIR, SESSION_DIR
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "overnight.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("overnight")

# ── Agent Task Definitions ───────────────────────────────────────────────────

OVERNIGHT_TASKS = [
    # Sales & Conversion
    {
        "agent_id": "lead-accelerator",
        "department": "sales",
        "model_preference": "groq",
        "complexity": "low",
        "title": "Lead Accelerator — Standby",
        "description": "You are the Lead Accelerator agent for Zoar Bathroom Rentals. Your job is to monitor for new website form submissions and generate instant quotes. For now, report: 'Ready and monitoring for new leads.' This agent runs reactively via webhook, not on a schedule.",
    },
    {
        "agent_id": "instant-quoter",
        "department": "sales",
        "model_preference": "groq",
        "complexity": "low",
        "title": "Instant Quote Generator — Standby",
        "description": "You are the Instant Quote Generator for Zoar Bathroom Rentals. You calculate prices based on event location distance from San Fernando, CA 91340. Tier 1 (0-10mi): $1,000. Tier 2 (10-20mi): $1,200 + mileage. Tier 3 (20+mi): $1,500 + mileage. Report: 'Quote engine ready. Awaiting Telegram forwarded inquiries.'",
    },
    {
        "agent_id": "follow-up-agent",
        "department": "sales",
        "model_preference": "groq",
        "complexity": "medium",
        "title": "Follow-up Agent — Draft messages for stale leads",
        "description": "You are the Follow-up Agent for Zoar Bathroom Rentals. Review these lead statuses and draft follow-up messages for any leads that were quoted more than 48 hours ago but haven't responded. Use warm, non-pushy language. Mention date availability and the $160 deposit to secure. Draft 3 different follow-up message templates: one for wedding leads, one for quinceañera leads, and one generic. Do NOT send any messages — drafts only.",
    },
    # Marketing & Lead Gen
    {
        "agent_id": "fb-lead-hunter",
        "department": "marketing",
        "model_preference": "groq",
        "complexity": "low",
        "title": "FB Lead Hunter — Continue monitoring",
        "description": "You are the Facebook Group Lead Hunter. Report current monitoring status. This agent runs as a background process in server.py, monitoring Facebook groups for keywords like 'outdoor wedding', 'restroom trailer', 'bathroom rental'. Report: 'Active and monitoring Facebook groups in SFV/Greater LA.'",
    },
    {
        "agent_id": "ad-copywriter",
        "department": "marketing",
        "model_preference": "openrouter",
        "complexity": "medium",
        "title": "Ad Copywriter — Generate 5 new ad copy variations",
        "description": "You are the Ad Copywriter for Zoar Bathroom Rentals. Create 5 new Facebook ad copy variations for luxury restroom trailer rentals. Rules: Use 'Starting at $999' or 'Starting at $1,000'. Say 'Delivery and setup included'. Never say 'all-inclusive' or 'no hidden fees'. Use contrast format: luxury trailer vs porta potty. Target: weddings and quinceañeras in San Fernando Valley. Each ad should have: headline (under 40 chars), primary text (under 125 chars), description (under 30 chars), and CTA button text. Make them emotionally compelling — focus on the guest experience, not the product specs.",
    },
    {
        "agent_id": "content-creator",
        "department": "marketing",
        "model_preference": "openrouter",
        "complexity": "medium",
        "title": "Content Creator — Draft 10 Facebook post ideas",
        "description": "You are the Content Creator for Zoar Bathroom Rentals. Create 10 Facebook post drafts for the business page. Mix of: 3 educational (why luxury restrooms matter for events), 3 social proof (review-style, testimonial format), 2 behind-the-scenes (trailer features showcase), 2 seasonal/timely (spring wedding season, quinceañera planning). Each post should be 2-4 sentences, include a suggested image description, and end with a soft CTA. Do NOT use hashtags excessively — max 3 per post. Write in a warm, professional tone from 'Zoar Bathroom Rentals'.",
    },
    # Data & Analytics
    {
        "agent_id": "ad-optimizer",
        "department": "data",
        "model_preference": "groq",
        "complexity": "low",
        "title": "Ad Optimizer — Continue monitoring",
        "description": "You are the Ad Performance Optimizer. This agent runs as a background process checking Facebook ad metrics every 6 hours. Report: 'Active. Monitoring ad performance with $8 CPL threshold. Best historical: $2.34-$4.93 CPL.'",
    },
    {
        "agent_id": "crm-expert",
        "department": "data",
        "model_preference": "ollama",
        "complexity": "low",
        "title": "CRM Expert — Audit lead data quality",
        "description": "You are the CRM Expert for Zoar Bathroom Rentals. Perform a data quality audit. Check for: leads missing event dates, leads missing locations, leads with no follow-up scheduled, leads quoted but never followed up, duplicate entries, leads older than 30 days with no status update. Provide a summary report with counts and specific lead IDs that need attention. Also suggest 3 CRM improvements for better lead tracking.",
    },
    # Research
    {
        "agent_id": "doc-researcher",
        "department": "research",
        "model_preference": "gemini",
        "complexity": "high",
        "title": "Doc Researcher — Research 4 topics",
        "description": "You are the Documentation Researcher. Research and summarize these 4 topics:\n1. Best practices for luxury restroom trailer marketing in 2026\n2. Facebook Ads optimization strategies for local service businesses under $500/month budget\n3. Wedding vendor partnership strategies — how to get venues to recommend your service\n4. Seasonal pricing strategies for event rental businesses\nFor each topic, provide: 3-5 key insights, 2-3 actionable recommendations specific to Zoar Bathroom Rentals, and any competitive advantages to highlight.",
    },
    {
        "agent_id": "competitor-analyst",
        "department": "research",
        "model_preference": "gemini",
        "complexity": "high",
        "title": "Competitor Analyst — SFV/LA competitor intel",
        "description": "You are the Competitor Analyst for Zoar Bathroom Rentals in the San Fernando Valley and Greater Los Angeles area. Research and compile intelligence on luxury restroom trailer rental competitors. For each competitor found, note: business name, service area, price range if available, unique selling points, online presence (website quality, social media activity, Google reviews count and rating). Also identify gaps in the market that Zoar can exploit. Focus on the SFV, Burbank, Glendale, Pasadena, and central LA areas.",
    },
    # Social Media
    {
        "agent_id": "social-poster",
        "department": "social",
        "model_preference": "groq",
        "complexity": "low",
        "title": "Social Poster — Format posts for Facebook",
        "description": "You are the Social Media Poster. Take the content drafts from the Content Creator agent and format them for Facebook posting. Add appropriate spacing, emoji placement (subtle, not excessive), and ensure each post has: an attention-grabbing first line, the main content, and a clear CTA. Output 5 ready-to-post Facebook posts. Do NOT actually post anything — output formatted text only.",
    },
    {
        "agent_id": "media-buyer",
        "department": "social",
        "model_preference": "openrouter",
        "complexity": "medium",
        "title": "Media Buyer — Budget allocation plan",
        "description": "You are the Media Buyer for Zoar Bathroom Rentals. With a monthly ad budget of $200-$500, create an optimized budget allocation plan. Consider: Facebook Ads (main channel), Google Ads (search intent), Instagram (visual showcase). Recommend: daily budget per platform, audience targeting (demographics, interests, locations within 20mi of San Fernando CA 91340), campaign structure (how many campaigns, ad sets, ads). Historical best: Wedding Leads Zoar SFV campaign at $2.34-$4.93 CPL. Goal: maximize leads while keeping CPL under $8.",
    },
    # Frontend
    {
        "agent_id": "website-designer",
        "department": "frontend",
        "model_preference": "gemini",
        "complexity": "high",
        "title": "Website Designer — Audit zoarbathroomrentals.com",
        "description": "You are the Website Designer. Audit the current zoarbathroomrentals.com website (it's a new Next.js site). Evaluate: page load speed expectations, mobile responsiveness requirements, SEO meta tags needed, conversion optimization (CTA placement, form design, trust signals), content hierarchy, and visual design recommendations. Provide a prioritized list of improvements with estimated impact on lead conversion. Focus on: hero section, quote request form, pricing section, gallery, and testimonials page.",
    },
    {
        "agent_id": "ui-builder",
        "department": "frontend",
        "model_preference": "groq",
        "complexity": "low",
        "title": "UI Builder — Audit Nexus Network components",
        "description": "You are the UI Builder for the Nexus Network desktop app (Next.js + Tauri). Review the component list and report which components need fixes: agent-control.tsx (org chart), ad-performance.tsx (ad metrics), dashboard.tsx (main dashboard), leads-pipeline.tsx (CRM view), quote-generator.tsx (pricing tool). For each, note: is it functional, does it have proper error states, does it handle loading states, does it match the dark theme. Report findings only — do not write code.",
    },
    # Backend
    {
        "agent_id": "api-builder",
        "department": "backend",
        "model_preference": "groq",
        "complexity": "low",
        "title": "API Builder — Audit server.py endpoints",
        "description": "You are the API Builder. Audit the Nexus server API endpoints. Count: total endpoints, endpoints with authentication, endpoints without authentication, endpoints returning errors. Check for: missing error handling, endpoints that could leak sensitive data, duplicate functionality. Provide a summary report. Server is FastAPI on port 7860 with 100+ endpoints across ads, leads, calendar, messaging, analytics, and agent management.",
    },
    {
        "agent_id": "db-manager",
        "department": "backend",
        "model_preference": "groq",
        "complexity": "low",
        "title": "DB Manager — Database health check",
        "description": "You are the Database Manager. Perform a health check on the SQLite database at ~/.nexus/memory.db. Report: total tables, total rows across key tables (leads, messages, approvals, ad_metrics, agent_status), database file size, any tables without indexes that should have them, tables with potential data integrity issues. Also check WAL mode is enabled and suggest any optimization opportunities.",
    },
    # Security
    {
        "agent_id": "security-auditor",
        "department": "security",
        "model_preference": "gemini",
        "complexity": "high",
        "title": "Security Auditor — Audit webhooks, blocklist, and auth",
        "description": "You are the Security Auditor for Nexus. Perform a security audit covering: 1) Webhook endpoints — are they properly authenticated? Can anyone post to /webhook/form? 2) Contact blocklist — is the outbound_gate() function properly checking the blocklist before every message? 3) Authentication — are API endpoints properly secured? 4) API key storage — are keys in config.json and not in code? 5) CORS policy — is it too permissive? (currently allow_origins=['*']). Provide severity ratings (critical/high/medium/low) for each finding and recommended fixes.",
    },
    # Project Manager
    {
        "agent_id": "task-coordinator",
        "department": "pm",
        "model_preference": "gemini",
        "complexity": "high",
        "title": "Project Manager — Compile overnight summary",
        "description": "You are the Project Manager coordinating the overnight agent run for Zoar Bathroom Rentals. Compile a morning report covering: 1) Agent status summary (which ran successfully, which had issues), 2) Key findings from each department, 3) Top 5 action items for Kai to review today, 4) Any blockers that need immediate attention, 5) Progress toward the 90-day booking goal (1 booking every 5 days). Format as a clean, scannable morning briefing that Kai can read on his phone in under 2 minutes.",
    },
]


def update_agent_status(agent_id, department, status, task):
    """Update agent status in hierarchy DB."""
    try:
        from core.agent_hierarchy import update_agent_status as _update
        _update(agent_id, department, status, task)
    except Exception as e:
        log.warning(f"Could not update hierarchy for {agent_id}: {e}")


def run_overnight():
    session_id = f"overnight_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    log.info(f"Starting overnight run: {session_id}")
    log.info(f"Total agents: {len(OVERNIGHT_TASKS)}")

    results = []
    completed = 0
    errors = 0

    for i, task in enumerate(OVERNIGHT_TASKS, 1):
        agent_id = task["agent_id"]
        dept = task["department"]
        model_pref = task["model_preference"]
        complexity = task["complexity"]

        log.info(f"[{i}/{len(OVERNIGHT_TASKS)}] {agent_id}: {task['title']}")

        # Update hierarchy status
        update_agent_status(agent_id, dept, "active", task["title"])

        # Get available model (prefer the assigned one)
        model_key = model_pref if model_pref in MODELS else None
        if model_key:
            # Check if preferred model has quota and key
            model = MODELS[model_key]
            has_key = not model.get("env_key") or os.environ.get(model["env_key"], "")
            if not has_key:
                model_key = get_available_model(complexity)
        else:
            model_key = get_available_model(complexity)

        if not model_key:
            log.error(f"No model available for {agent_id}")
            update_agent_status(agent_id, dept, "error", "No model available")
            results.append({
                "agent_id": agent_id,
                "status": "error",
                "model": "none",
                "result": "No model available",
            })
            errors += 1
            continue

        # Try preferred model, then fallback chain
        fallback_chain = [model_key]
        for fb in ["gemini", "groq", "ollama"]:
            if fb not in fallback_chain:
                fallback_chain.append(fb)

        succeeded = False
        for try_model in fallback_chain:
            try:
                result = call_model(try_model, task["description"])
                model_name = MODELS[try_model]["name"]
                if try_model != model_key:
                    model_name += " (fallback)"
                update_agent_status(agent_id, dept, "idle", f"Completed: {task['title']}")
                results.append({
                    "agent_id": agent_id,
                    "status": "completed",
                    "model": model_name,
                    "title": task["title"],
                    "result": result[:2000],
                })
                completed += 1
                log.info(f"  -> {agent_id} completed via {model_name}")
                succeeded = True
                break
            except Exception as e:
                log.warning(f"  -> {agent_id} failed on {MODELS[try_model]['name']}: {e}")
                continue

        if not succeeded:
            log.error(f"  -> {agent_id} FAILED on all models")
            update_agent_status(agent_id, dept, "error", "All models failed")
            results.append({
                "agent_id": agent_id,
                "status": "error",
                "model": "all failed",
                "title": task["title"],
                "result": "All models exhausted",
            })
            errors += 1

        # Small delay between API calls to avoid rate limits
        time.sleep(2)

    # Save results
    results_file = SESSION_DIR / f"{session_id}.json"
    results_file.write_text(json.dumps({
        "session_id": session_id,
        "started_at": datetime.now().isoformat(),
        "total_agents": len(OVERNIGHT_TASKS),
        "completed": completed,
        "errors": errors,
        "results": results,
    }, indent=2))
    log.info(f"Results saved: {results_file}")

    # Send Telegram summary
    try:
        cfg = load_config()
        token = cfg.get("telegram_token", "")
        chat_ids = cfg.get("telegram_chat_ids", [])
        if token and chat_ids:
            from urllib.request import Request, urlopen
            summary = (
                f"OVERNIGHT RUN COMPLETE\n\n"
                f"Agents: {completed}/{len(OVERNIGHT_TASKS)} succeeded\n"
                f"Errors: {errors}\n"
                f"Results: {results_file}\n\n"
                f"Full morning report compiling..."
            )
            for cid in chat_ids:
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                payload = json.dumps({
                    "chat_id": str(cid),
                    "text": summary,
                }).encode()
                req = Request(url, data=payload, headers={"Content-Type": "application/json"})
                with urlopen(req, timeout=10) as resp:
                    resp.read()
            log.info("Telegram summary sent")
    except Exception as e:
        log.warning(f"Telegram notification failed: {e}")

    # Print summary
    print("\n" + "=" * 60)
    print(f"OVERNIGHT RUN: {session_id}")
    print(f"Completed: {completed}/{len(OVERNIGHT_TASKS)}")
    print(f"Errors: {errors}")
    print("=" * 60)
    for r in results:
        icon = "OK" if r["status"] == "completed" else "ERR"
        print(f"  [{icon}] {r['agent_id']} -> {r.get('model', 'N/A')}")
    print("=" * 60)
    print(f"Results: {results_file}")


if __name__ == "__main__":
    # Load env vars
    zshrc = Path.home() / ".zshrc"
    if zshrc.exists():
        for line in zshrc.read_text().splitlines():
            if line.startswith("export ") and "=" in line:
                parts = line.replace("export ", "").split("=", 1)
                key = parts[0].strip()
                val = parts[1].strip().strip('"').strip("'")
                os.environ.setdefault(key, val)

    run_overnight()
