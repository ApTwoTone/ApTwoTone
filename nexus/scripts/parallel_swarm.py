#!/usr/bin/env python3
"""
Parallel Swarm Runner — Simulate Agent Swarm with concurrent API calls.

Kimi Agent Swarm is a platform-only feature (kimi.com), not accessible via API.
This script simulates swarm behavior by spawning concurrent API calls to free models.

Usage:
    python3 scripts/parallel_swarm.py
"""
import os
import sys
import json
import time
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.orchestrator import (
    call_model, get_available_model, MODELS, LOG_DIR, SESSION_DIR,
    load_config
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "swarm.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("swarm")

# ── Swarm Task Definitions ───────────────────────────────────────────────────

# SFV Facebook Groups to scan (25 groups)
FB_GROUPS = [
    "SFV Wedding Planning", "LA Wedding Vendors", "San Fernando Valley Events",
    "Burbank Party Planning", "Glendale Events", "Pasadena Wedding Vendors",
    "NoHo Arts District Events", "Van Nuys Community", "Northridge Events",
    "Canoga Park Community", "Sylmar/San Fernando Events", "Granada Hills Neighbors",
    "Tarzana Community", "Encino Events", "Sherman Oaks Community",
    "Studio City Events", "Pacoima Community", "Arleta/Panorama City Events",
    "Sun Valley Community", "LA County Outdoor Events", "SoCal Wedding Planning",
    "Greater LA Event Vendors", "Inland Empire Events", "LA Quinceañera Planning",
    "SFV Backyard Party Ideas"
]

# Event types for ad copy (15 combinations)
AD_COPY_COMBOS = [
    ("Wedding", "Brides 25-40"), ("Wedding", "Parents of bride"),
    ("Quinceañera", "Parents 35-55"), ("Quinceañera", "Event planners"),
    ("Backyard Party", "Homeowners 30-50"), ("Corporate Event", "Office managers"),
    ("Festival", "Event organizers"), ("Film Production", "Production coordinators"),
    ("Wedding", "Engaged couples SFV"), ("Quinceañera", "Latina mothers SFV"),
    ("Outdoor Reception", "Couples planning outdoor"), ("Birthday Party", "Parents 35-55"),
    ("Graduation Party", "Families"), ("Holiday Party", "Corporate + residential"),
    ("Baby Shower", "Expectant parents")
]

# Competitor companies (15)
COMPETITORS = [
    "The Lavatory LA", "Luxury Loo Rentals", "Royal Restrooms LA",
    "Porta Palace", "VIP Restrooms", "The Throne Room LA",
    "Elegant Portable Solutions", "A Royal Flush LA", "Privy Affairs",
    "SFV Restroom Rentals", "LA Mobile Restrooms", "Premium Portable LA",
    "Executive Restrooms SFV", "Crown Portable Restrooms", "LA Luxury Loos"
]

# Website audit aspects (10)
WEBSITE_ASPECTS = [
    "Page load speed and performance", "Mobile responsiveness on iPhone/Android",
    "Quote request form visibility and UX", "Call-to-action button placement and copy",
    "Trust signals (reviews, certifications, BBB)", "Pricing page clarity",
    "Contact page and phone number prominence", "Image quality and gallery",
    "SEO meta tags and structured data", "Social proof and testimonials"
]

# Content angles (10)
CONTENT_ANGLES = [
    "Why luxury restrooms matter for weddings",
    "Quinceañera planning checklist with restroom tips",
    "Porta potty vs luxury restroom comparison",
    "Guest comfort makes or breaks outdoor events",
    "Behind the scenes: what's inside our luxury trailer",
    "Wedding vendor partnerships that save money",
    "Summer event planning in the San Fernando Valley",
    "How to plan a backyard party with 100+ guests",
    "Why film productions need quality restroom facilities",
    "The hidden cost of NOT having luxury restrooms"
]


def run_swarm_task(task_id, prompt, model_preference="gemini"):
    """Execute a single swarm subtask."""
    try:
        model_key = model_preference
        model = MODELS.get(model_key)
        if not model:
            model_key = get_available_model("medium")
        elif model.get("env_key") and not os.environ.get(model["env_key"], ""):
            model_key = get_available_model("medium")
        if not model_key:
            model_key = "ollama"  # Ultimate fallback

        result = call_model(model_key, prompt)
        return {"id": task_id, "status": "ok", "model": MODELS[model_key]["name"], "result": result[:1500]}
    except Exception as e:
        # Try fallback
        for fb in ["gemini", "groq", "ollama"]:
            if fb == model_preference:
                continue
            try:
                result = call_model(fb, prompt)
                return {"id": task_id, "status": "ok", "model": MODELS[fb]["name"] + " (fallback)", "result": result[:1500]}
            except Exception:
                continue
        return {"id": task_id, "status": "error", "model": "none", "result": str(e)[:200]}


def run_swarm(category, tasks, max_workers=5):
    """Run a batch of tasks in parallel using ThreadPoolExecutor."""
    log.info(f"SWARM [{category}]: Spawning {len(tasks)} parallel tasks (max {max_workers} concurrent)")
    results = []
    start = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for task in tasks:
            future = executor.submit(
                run_swarm_task,
                task["id"],
                task["prompt"],
                task.get("model", "gemini")
            )
            futures[future] = task["id"]

        for future in as_completed(futures):
            task_id = futures[future]
            try:
                result = future.result()
                results.append(result)
                status = "OK" if result["status"] == "ok" else "ERR"
                log.info(f"  [{status}] {task_id} via {result['model']}")
            except Exception as e:
                results.append({"id": task_id, "status": "error", "result": str(e)})
                log.error(f"  [ERR] {task_id}: {e}")

    elapsed = time.time() - start
    ok_count = sum(1 for r in results if r["status"] == "ok")
    log.info(f"SWARM [{category}]: {ok_count}/{len(tasks)} completed in {elapsed:.1f}s")
    return results


def build_fb_group_tasks():
    tasks = []
    for i, group in enumerate(FB_GROUPS):
        tasks.append({
            "id": f"fb-scan-{i+1}",
            "prompt": f"You are scanning the Facebook group '{group}' for potential leads for Zoar Bathroom Rentals in San Fernando Valley. Look for posts about: outdoor weddings, backyard parties, quinceañeras, events needing restroom facilities, venue recommendations without bathrooms. For each potential lead found, draft a helpful (not salesy) response mentioning Zoar. Report: group name, number of relevant posts found (estimate), best opportunity, and a drafted response.",
            "model": "groq",
        })
    return tasks


def build_ad_copy_tasks():
    tasks = []
    for i, (event, audience) in enumerate(AD_COPY_COMBOS):
        tasks.append({
            "id": f"ad-copy-{i+1}",
            "prompt": f"Write 2 Facebook ad copy variations for Zoar Bathroom Rentals targeting {event} events for audience: {audience}. Rules: Use 'Starting at $999'. Say 'Delivery and setup included'. Never say 'all-inclusive' or 'no hidden fees'. Use contrast: luxury trailer vs porta potty. Each ad: headline (<40 chars), primary text (<125 chars), description (<30 chars), CTA. Make it emotionally compelling focused on guest experience.",
            "model": "gemini",
        })
    return tasks


def build_competitor_tasks():
    tasks = []
    for i, competitor in enumerate(COMPETITORS):
        tasks.append({
            "id": f"competitor-{i+1}",
            "prompt": f"Research the luxury bathroom rental company '{competitor}' in the Los Angeles / San Fernando Valley area. Report: estimated price range, service area, website quality (1-10), Google review count and rating, unique selling points, social media presence, any Facebook ads running. If this company doesn't exist or you can't find info, say so. This is market research for Zoar Bathroom Rentals.",
            "model": "gemini",
        })
    return tasks


def build_website_audit_tasks():
    tasks = []
    for i, aspect in enumerate(WEBSITE_ASPECTS):
        tasks.append({
            "id": f"website-audit-{i+1}",
            "prompt": f"Audit zoarbathroomrental.com for: {aspect}. Provide: current status assessment, specific issues found, recommended fixes prioritized by impact on lead conversion, and an estimated effort level (quick fix / moderate / major). Focus on what will get more people to submit the quote request form.",
            "model": "gemini",
        })
    return tasks


def build_content_tasks():
    tasks = []
    for i, angle in enumerate(CONTENT_ANGLES):
        tasks.append({
            "id": f"content-{i+1}",
            "prompt": f"Write an organic Facebook post for Zoar Bathroom Rentals about: '{angle}'. Requirements: 2-4 sentences, warm professional tone, include a suggested image description, end with a soft CTA. Max 3 hashtags. Sign from 'Zoar Bathroom Rentals'. Make it shareable and engaging.",
            "model": "groq",
        })
    return tasks


def run_all_swarms():
    session_id = f"swarm_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    log.info(f"Starting swarm session: {session_id}")
    all_results = {}
    peak_concurrent = 0

    # Run swarms sequentially by category to avoid overwhelming rate limits
    # but tasks within each category run in parallel

    categories = [
        ("fb_groups", build_fb_group_tasks(), 5),          # 25 tasks, 5 concurrent
        ("ad_copy", build_ad_copy_tasks(), 4),              # 15 tasks, 4 concurrent
        ("competitors", build_competitor_tasks(), 4),        # 15 tasks, 4 concurrent
        ("website_audit", build_website_audit_tasks(), 3),   # 10 tasks, 3 concurrent
        ("content", build_content_tasks(), 4),               # 10 tasks, 4 concurrent
    ]

    for cat_name, tasks, max_workers in categories:
        peak_concurrent = max(peak_concurrent, max_workers)
        results = run_swarm(cat_name, tasks, max_workers)
        all_results[cat_name] = results
        time.sleep(5)  # Pause between categories to avoid rate limits

    # Save results
    results_file = SESSION_DIR / f"{session_id}.json"
    summary = {
        "session_id": session_id,
        "timestamp": datetime.now().isoformat(),
        "peak_concurrent": peak_concurrent,
        "total_tasks": sum(len(r) for r in all_results.values()),
        "total_ok": sum(1 for cat in all_results.values() for r in cat if r["status"] == "ok"),
        "categories": {k: {"total": len(v), "ok": sum(1 for r in v if r["status"] == "ok")} for k, v in all_results.items()},
        "results": all_results,
    }
    results_file.write_text(json.dumps(summary, indent=2))
    log.info(f"Swarm results saved: {results_file}")

    # Send Telegram summary
    try:
        cfg = load_config()
        token = cfg.get("telegram_token", "")
        chat_ids = cfg.get("telegram_chat_ids", [])
        if token and chat_ids:
            from urllib.request import Request, urlopen as _urlopen
            msg = (
                f"SWARM RUN COMPLETE\n\n"
                f"Total tasks: {summary['total_tasks']}\n"
                f"Succeeded: {summary['total_ok']}\n"
                f"Peak concurrent: {peak_concurrent}\n\n"
            )
            for cat, stats in summary["categories"].items():
                msg += f"{cat}: {stats['ok']}/{stats['total']}\n"

            for cid in chat_ids:
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                payload = json.dumps({"chat_id": str(cid), "text": msg}).encode()
                req_tg = __import__('urllib.request', fromlist=['Request']).Request(
                    url, data=payload, headers={"Content-Type": "application/json"}
                )
                with _urlopen(req_tg, timeout=10) as resp:
                    resp.read()
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")

    return summary


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

    run_all_swarms()
