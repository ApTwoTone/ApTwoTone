#!/usr/bin/env python3
"""
Parallel Swarm v2 — True async parallel workers using asyncio + aiohttp.

Runs 25-35 concurrent workers across free AI model APIs with automatic
fallback, quota tracking, and structured result logging.

Usage:
    # From a JSON file:
    python3 scripts/parallel_swarm_v2.py tasks.json

    # From stdin:
    cat tasks.json | python3 scripts/parallel_swarm_v2.py

    # Generate default Zoar tasks (no argument, no stdin):
    python3 scripts/parallel_swarm_v2.py

Task format (JSON array):
    [
        {"id": "task-1", "prompt": "...", "complexity": "low", "category": "ad_copy"},
        {"id": "task-2", "prompt": "...", "complexity": "medium", "category": "research"}
    ]
"""
import os
import sys
import json
import signal
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any

# ---------------------------------------------------------------------------
# Paths & config
# ---------------------------------------------------------------------------
NEXUS_DIR = Path.home() / ".nexus"
LOG_DIR = NEXUS_DIR / "orchestrator_logs"
CONFIG_FILE = NEXUS_DIR / "config.json"

LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "swarm_v2.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("swarm_v2")

# ---------------------------------------------------------------------------
# Model definitions (mirrors orchestrator.py)
# ---------------------------------------------------------------------------
MODELS: Dict[str, Dict[str, Any]] = {
    "gemini": {
        "name": "Gemini 3 Flash",
        "model_id": "gemini-3-flash-preview", # Added model_id
        "endpoint": "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent", # Updated endpoint
        "env_key": "GEMINI_API_KEY",
        "daily_limit": 500,
        "style": "gemini",
    },
    "groq": {
        "name": "Groq (Llama 4 Scout)",
        "endpoint": "https://api.groq.com/openai/v1/chat/completions",
        "model_id": "meta-llama/llama-4-scout-17b-16e-instruct",
        "env_key": "GROQ_API_KEY",
        "daily_limit": 1000,
        "style": "openai",
    },
    "glm": {
        "name": "GLM-4.7-Flash (Z.AI)",
        "endpoint": "https://api.z.ai/api/paas/v4/chat/completions",
        "model_id": "glm-4.7-flash",
        "env_key": "ZAI_API_KEY",
        "daily_limit": 999999,
        "style": "openai",
    },
    "glm45": {
        "name": "GLM-4.5-Flash (Z.AI)",
        "endpoint": "https://api.z.ai/api/paas/v4/chat/completions",
        "model_id": "glm-4.5-flash",
        "env_key": "ZAI_API_KEY",
        "daily_limit": 999999,
        "style": "openai",
    },
    "openrouter": {
        "name": "Llama 3.3 70B (OpenRouter)",
        "endpoint": "https://openrouter.ai/api/v1/chat/completions",
        "model_id": "meta-llama/llama-3.3-70b-instruct:free",
        "env_key": "OPENROUTER_API_KEY",
        "daily_limit": 200,
        "style": "openai",
    },
    "ollama": {
        "name": "Qwen 3 8B (Local Ollama)",
        "endpoint": "http://localhost:11434/api/generate",
        "model_id": "qwen3:8b",
        "env_key": None,
        "daily_limit": 999999,
        "style": "ollama",
    },
}

# Complexity -> ordered list of models to try (cheapest / best-fit first)
ROUTING_ORDER: Dict[str, List[str]] = {
    "high": ["gemini", "glm", "glm45", "openrouter"],
    "medium": ["glm", "glm45", "openrouter", "gemini", "groq", "ollama"],
    "low": ["groq", "glm45", "glm", "ollama", "openrouter"],
    "trivial": ["ollama", "groq", "glm45"],
}

# How many workers to run in parallel
MAX_CONCURRENCY = 30


# ---------------------------------------------------------------------------
# Thread-safe quota tracker (in-memory, flushed at end)
# ---------------------------------------------------------------------------
class QuotaTracker:
    """Async-safe per-model quota counters using asyncio.Lock."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._usage: Dict[str, int] = {k: 0 for k in MODELS}
        self._date = datetime.now().strftime("%Y-%m-%d")

    async def try_reserve(self, model_key: str) -> bool:
        """Atomically check and increment. Returns True if quota available."""
        async with self._lock:
            limit = MODELS[model_key]["daily_limit"]
            if self._usage[model_key] >= limit:
                return False
            self._usage[model_key] += 1
            return True

    async def release(self, model_key: str):
        """Give back a reservation on failure (so the slot isn't wasted)."""
        async with self._lock:
            if self._usage[model_key] > 0:
                self._usage[model_key] -= 1

    def snapshot(self) -> Dict[str, int]:
        """Return a copy of current usage (safe for read outside lock)."""
        return dict(self._usage)


# ---------------------------------------------------------------------------
# Stats collector
# ---------------------------------------------------------------------------
class SwarmStats:
    """Collects per-task results and aggregate stats."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self.completed = 0
        self.errors = 0
        self.fallbacks = 0
        self.results: List[Dict[str, Any]] = []

    async def record(self, result: Dict[str, Any]):
        async with self._lock:
            self.results.append(result)
            if result["status"] == "ok":
                self.completed += 1
            else:
                self.errors += 1
            if result.get("fallback", False):
                self.fallbacks += 1


# ---------------------------------------------------------------------------
# Async model callers
# ---------------------------------------------------------------------------

async def call_gemini(session, prompt: str) -> str:
    """Gemini 2.5 Flash via Google AI Studio."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")
    url = MODELS["gemini"]["endpoint"] + "?key=" + api_key
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 4096},
    }
    async with session.post(url, json=payload, timeout=_timeout(120)) as resp:
        if resp.status == 429:
            raise RateLimitError("gemini 429")
        resp.raise_for_status()
        data = await resp.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise RuntimeError("Gemini returned unexpected structure: " + json.dumps(data)[:300])


async def call_openai_compat(session, model_key: str, prompt: str) -> str:
    """Generic OpenAI-compatible caller (Groq, OpenRouter, Z.AI/GLM)."""
    model = MODELS[model_key]
    api_key = os.environ.get(model["env_key"], "")
    if not api_key:
        raise RuntimeError(model["env_key"] + " not set")
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + api_key,
        "User-Agent": "nexus-swarm-v2/1.0",
    }
    payload = {
        "model": model["model_id"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 4096,
    }
    async with session.post(model["endpoint"], json=payload, headers=headers, timeout=_timeout(120)) as resp:
        if resp.status == 429:
            raise RateLimitError(model_key + " 429")
        resp.raise_for_status()
        data = await resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise RuntimeError(model_key + " unexpected response: " + json.dumps(data)[:300])


async def call_ollama(session, prompt: str) -> str:
    """Local Ollama (Qwen 3 8B)."""
    payload = {
        "model": MODELS["ollama"]["model_id"],
        "prompt": prompt,
        "stream": False,
    }
    try:
        async with session.post(MODELS["ollama"]["endpoint"], json=payload, timeout=_timeout(300)) as resp:
            resp.raise_for_status()
            data = await resp.json()
        return data["response"]
    except Exception as e:
        if "Cannot connect" in str(e) or "ConnectionRefused" in str(e):
            raise RuntimeError("Ollama not running. Start with: brew services start ollama")
        raise


async def call_model_async(session, model_key: str, prompt: str) -> str:
    """Dispatch to the correct async caller."""
    style = MODELS[model_key]["style"]
    if style == "gemini":
        return await call_gemini(session, prompt)
    elif style == "ollama":
        return await call_ollama(session, prompt)
    else:
        return await call_openai_compat(session, model_key, prompt)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class RateLimitError(Exception):
    """Raised on HTTP 429 to trigger fallback."""
    pass


def _timeout(seconds: int):
    """Create an aiohttp ClientTimeout."""
    import aiohttp
    return aiohttp.ClientTimeout(total=seconds)


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    return {}


def _load_env_vars():
    """Load environment variables from ~/.zshrc exports."""
    zshrc = Path.home() / ".zshrc"
    if zshrc.exists():
        for line in zshrc.read_text().splitlines():
            if line.startswith("export ") and "=" in line:
                parts = line.replace("export ", "").split("=", 1)
                key = parts[0].strip()
                val = parts[1].strip().strip('"').strip("'")
                os.environ.setdefault(key, val)


def _model_available(model_key: str) -> bool:
    """Check if a model's API key is configured (Ollama needs no key)."""
    env_key = MODELS[model_key]["env_key"]
    if env_key is None:
        return True
    return bool(os.environ.get(env_key, ""))


def _get_fallback_chain(complexity: str) -> List[str]:
    """Build the ordered fallback chain for a complexity level.

    Primary routing order for the complexity, then remaining models as fallback.
    Only includes models whose API keys are configured.
    """
    primary = ROUTING_ORDER.get(complexity, ROUTING_ORDER["medium"])
    rest = [k for k in MODELS if k not in primary]
    chain = [k for k in (primary + rest) if _model_available(k)]
    return chain


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

async def process_task(
    task: Dict[str, Any],
    session,
    semaphore: asyncio.Semaphore,
    quota: QuotaTracker,
    stats: SwarmStats,
    shutdown_event: asyncio.Event,
):
    """Process a single task with fallback across models."""
    task_id = task["id"]
    prompt = task["prompt"]
    complexity = task.get("complexity", "medium")
    category = task.get("category", "unknown")
    chain = _get_fallback_chain(complexity)

    if not chain:
        result = {
            "id": task_id,
            "category": category,
            "complexity": complexity,
            "status": "error",
            "error": "No models available (missing API keys)",
            "model": None,
            "fallback": False,
            "result": None,
        }
        await stats.record(result)
        log.error("[%s] No models available", task_id)
        return result

    async with semaphore:
        if shutdown_event.is_set():
            result = {
                "id": task_id,
                "category": category,
                "complexity": complexity,
                "status": "cancelled",
                "error": "Shutdown requested",
                "model": None,
                "fallback": False,
                "result": None,
            }
            await stats.record(result)
            return result

        used_fallback = False
        attempted_models = []

        for i, model_key in enumerate(chain):
            if shutdown_event.is_set():
                break

            # Reserve quota
            if not await quota.try_reserve(model_key):
                log.debug("[%s] Quota exhausted for %s, skipping", task_id, model_key)
                continue

            attempted_models.append(model_key)
            model_name = MODELS[model_key]["name"]

            if i > 0:
                used_fallback = True

            try:
                log.info("[%s] Calling %s (complexity=%s)", task_id, model_name, complexity)
                text = await call_model_async(session, model_key, prompt)
                result = {
                    "id": task_id,
                    "category": category,
                    "complexity": complexity,
                    "status": "ok",
                    "model": model_name,
                    "model_key": model_key,
                    "fallback": used_fallback,
                    "attempted": attempted_models,
                    "result": text[:2000],
                    "error": None,
                }
                await stats.record(result)
                log.info("[OK]  %s via %s%s", task_id, model_name,
                         " (fallback)" if used_fallback else "")
                return result

            except RateLimitError:
                log.warning("[%s] Rate limited on %s, falling back", task_id, model_name)
                await quota.release(model_key)  # Give back the slot
                await asyncio.sleep(1)  # Brief pause before trying next
                continue

            except Exception as e:
                log.warning("[%s] Error on %s: %s — trying next", task_id, model_name, e)
                continue

        # All models exhausted
        result = {
            "id": task_id,
            "category": category,
            "complexity": complexity,
            "status": "error",
            "error": "All models failed",
            "model": None,
            "model_key": None,
            "fallback": used_fallback,
            "attempted": attempted_models,
            "result": None,
        }
        await stats.record(result)
        log.error("[ERR] %s — all %d models failed", task_id, len(attempted_models))
        return result


# ---------------------------------------------------------------------------
# Default task builders (for standalone mode)
# ---------------------------------------------------------------------------

FB_GROUPS = [
    "SFV Wedding Planning", "LA Wedding Vendors", "San Fernando Valley Events",
    "Burbank Party Planning", "Glendale Events", "Pasadena Wedding Vendors",
    "NoHo Arts District Events", "Van Nuys Community", "Northridge Events",
    "Canoga Park Community", "Sylmar/San Fernando Events", "Granada Hills Neighbors",
    "Tarzana Community", "Encino Events", "Sherman Oaks Community",
    "Studio City Events", "Pacoima Community", "Arleta/Panorama City Events",
    "Sun Valley Community", "LA County Outdoor Events", "SoCal Wedding Planning",
    "Greater LA Event Vendors", "Inland Empire Events", "LA Quinceañera Planning",
    "SFV Backyard Party Ideas",
]

AD_COPY_COMBOS = [
    ("Wedding", "Brides 25-40"), ("Wedding", "Parents of bride"),
    ("Quinceañera", "Parents 35-55"), ("Quinceañera", "Event planners"),
    ("Backyard Party", "Homeowners 30-50"), ("Corporate Event", "Office managers"),
    ("Festival", "Event organizers"), ("Film Production", "Production coordinators"),
    ("Wedding", "Engaged couples SFV"), ("Quinceañera", "Latina mothers SFV"),
    ("Outdoor Reception", "Couples planning outdoor"), ("Birthday Party", "Parents 35-55"),
    ("Graduation Party", "Families"), ("Holiday Party", "Corporate + residential"),
    ("Baby Shower", "Expectant parents"),
]

COMPETITORS = [
    "The Lavatory LA", "Luxury Loo Rentals", "Royal Restrooms LA",
    "Porta Palace", "VIP Restrooms", "The Throne Room LA",
    "Elegant Portable Solutions", "A Royal Flush LA", "Privy Affairs",
    "SFV Restroom Rentals", "LA Mobile Restrooms", "Premium Portable LA",
    "Executive Restrooms SFV", "Crown Portable Restrooms", "LA Luxury Loos",
]

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
    "The hidden cost of NOT having luxury restrooms",
]

WEBSITE_ASPECTS = [
    "Page load speed and performance", "Mobile responsiveness on iPhone/Android",
    "Quote request form visibility and UX", "Call-to-action button placement and copy",
    "Trust signals (reviews, certifications, BBB)", "Pricing page clarity",
    "Contact page and phone number prominence", "Image quality and gallery",
    "SEO meta tags and structured data", "Social proof and testimonials",
]


def build_default_tasks() -> List[Dict[str, Any]]:
    """Generate the full default Zoar task set (75 tasks)."""
    tasks = []

    # FB group scans (25 tasks, low complexity — pattern matching)
    for i, group in enumerate(FB_GROUPS):
        tasks.append({
            "id": "fb-scan-%d" % (i + 1),
            "prompt": (
                "You are scanning the Facebook group '%s' for potential leads for "
                "Zoar Bathroom Rentals in San Fernando Valley. Look for posts about: "
                "outdoor weddings, backyard parties, quinceañeras, events needing restroom "
                "facilities, venue recommendations without bathrooms. For each potential "
                "lead found, draft a helpful (not salesy) response mentioning Zoar. "
                "Report: group name, number of relevant posts found (estimate), best "
                "opportunity, and a drafted response."
            ) % group,
            "complexity": "low",
            "category": "fb_groups",
        })

    # Ad copy (15 tasks, medium complexity — creative writing)
    for i, (event, audience) in enumerate(AD_COPY_COMBOS):
        tasks.append({
            "id": "ad-copy-%d" % (i + 1),
            "prompt": (
                "Write 2 Facebook ad copy variations for Zoar Bathroom Rentals targeting "
                "%s events for audience: %s. Rules: Use 'Starting at $999'. Say 'Delivery "
                "and setup included'. Never say 'all-inclusive' or 'no hidden fees'. Use "
                "contrast: luxury trailer vs porta potty. Each ad: headline (<40 chars), "
                "primary text (<125 chars), description (<30 chars), CTA. Make it "
                "emotionally compelling focused on guest experience."
            ) % (event, audience),
            "complexity": "medium",
            "category": "ad_copy",
        })

    # Competitor research (15 tasks, medium complexity — research)
    for i, competitor in enumerate(COMPETITORS):
        tasks.append({
            "id": "competitor-%d" % (i + 1),
            "prompt": (
                "Research the luxury bathroom rental company '%s' in the Los Angeles / "
                "San Fernando Valley area. Report: estimated price range, service area, "
                "website quality (1-10), Google review count and rating, unique selling "
                "points, social media presence, any Facebook ads running. If this company "
                "doesn't exist or you can't find info, say so. This is market research "
                "for Zoar Bathroom Rentals."
            ) % competitor,
            "complexity": "medium",
            "category": "competitors",
        })

    # Website audit (10 tasks, medium complexity)
    for i, aspect in enumerate(WEBSITE_ASPECTS):
        tasks.append({
            "id": "website-audit-%d" % (i + 1),
            "prompt": (
                "Audit zoarbathroomrental.com for: %s. Provide: current status assessment, "
                "specific issues found, recommended fixes prioritized by impact on lead "
                "conversion, and an estimated effort level (quick fix / moderate / major). "
                "Focus on what will get more people to submit the quote request form."
            ) % aspect,
            "complexity": "medium",
            "category": "website_audit",
        })

    # Content drafts (10 tasks, low complexity — short posts)
    for i, angle in enumerate(CONTENT_ANGLES):
        tasks.append({
            "id": "content-%d" % (i + 1),
            "prompt": (
                "Write an organic Facebook post for Zoar Bathroom Rentals about: '%s'. "
                "Requirements: 2-4 sentences, warm professional tone, include a suggested "
                "image description, end with a soft CTA. Max 3 hashtags. Sign from "
                "'Zoar Bathroom Rentals'. Make it shareable and engaging."
            ) % angle,
            "complexity": "low",
            "category": "content",
        })

    return tasks


# ---------------------------------------------------------------------------
# Result logging
# ---------------------------------------------------------------------------

def save_results(stats: SwarmStats, quota: QuotaTracker, elapsed: float):
    """Write structured results to ~/.nexus/orchestrator_logs/swarm_{date}.json."""
    today = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = LOG_DIR / ("swarm_%s.json" % today)
    usage = quota.snapshot()

    # Per-model breakdown
    model_usage = {}
    for r in stats.results:
        mk = r.get("model_key")
        if mk:
            model_usage.setdefault(mk, {"name": MODELS[mk]["name"], "calls": 0, "ok": 0, "errors": 0})
            model_usage[mk]["calls"] += 1
            if r["status"] == "ok":
                model_usage[mk]["ok"] += 1
            else:
                model_usage[mk]["errors"] += 1

    # Per-category breakdown
    category_stats = {}
    for r in stats.results:
        cat = r.get("category", "unknown")
        category_stats.setdefault(cat, {"total": 0, "ok": 0, "errors": 0})
        category_stats[cat]["total"] += 1
        if r["status"] == "ok":
            category_stats[cat]["ok"] += 1
        else:
            category_stats[cat]["errors"] += 1

    report = {
        "session_id": "swarm_v2_%s" % today,
        "timestamp": datetime.now().isoformat(),
        "elapsed_seconds": round(elapsed, 2),
        "max_concurrency": MAX_CONCURRENCY,
        "summary": {
            "total_tasks": len(stats.results),
            "completed": stats.completed,
            "errors": stats.errors,
            "fallbacks": stats.fallbacks,
        },
        "per_model_usage": model_usage,
        "quota_consumed": usage,
        "per_category": category_stats,
        "results": stats.results,
    }

    out_path.write_text(json.dumps(report, indent=2, default=str))
    log.info("Results saved: %s", out_path)
    return out_path, report


# ---------------------------------------------------------------------------
# Telegram notification
# ---------------------------------------------------------------------------

async def send_telegram_summary(report: dict):
    """Send a one-shot summary to Telegram (non-blocking, best-effort)."""
    cfg = load_config()
    token = cfg.get("telegram_token", "")
    chat_ids = cfg.get("telegram_chat_ids", [])
    if not token or not chat_ids:
        return

    s = report["summary"]
    lines = [
        "SWARM v2 COMPLETE",
        "",
        "Tasks: %d | OK: %d | Errors: %d | Fallbacks: %d" % (
            s["total_tasks"], s["completed"], s["errors"], s["fallbacks"]),
        "Elapsed: %.1fs | Workers: %d" % (report["elapsed_seconds"], report["max_concurrency"]),
        "",
    ]
    for cat, cs in report.get("per_category", {}).items():
        lines.append("%s: %d/%d" % (cat, cs["ok"], cs["total"]))
    lines.append("")
    for mk, mu in report.get("per_model_usage", {}).items():
        lines.append("%s: %d calls (%d ok)" % (mu["name"], mu["calls"], mu["ok"]))

    msg = "\n".join(lines)

    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            for cid in chat_ids:
                url = "https://api.telegram.org/bot%s/sendMessage" % token
                payload = {"chat_id": str(cid), "text": msg}
                async with session.post(url, json=payload, timeout=_timeout(10)) as resp:
                    await resp.read()
    except Exception as e:
        log.warning("Telegram send failed: %s", e)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

async def run_swarm(tasks: List[Dict[str, Any]]):
    """Execute all tasks with async concurrency, fallback, and quota tracking."""
    import aiohttp

    log.info("Starting swarm v2: %d tasks, max %d concurrent workers", len(tasks), MAX_CONCURRENCY)

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    quota = QuotaTracker()
    stats = SwarmStats()
    shutdown_event = asyncio.Event()

    # Graceful shutdown handler
    loop = asyncio.get_running_loop()
    original_handlers = {}

    def _shutdown_handler(sig):
        log.warning("Received signal %s — shutting down gracefully...", sig.name)
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            original_handlers[sig] = loop.add_signal_handler(sig, _shutdown_handler, sig)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    start_time = asyncio.get_event_loop().time()

    # Use a single shared session with connection pooling
    connector = aiohttp.TCPConnector(
        limit=MAX_CONCURRENCY + 5,  # Slightly above semaphore to avoid starving
        limit_per_host=15,          # Don't hammer any single endpoint
        ttl_dns_cache=300,
        enable_cleanup_closed=True,
    )
    timeout = aiohttp.ClientTimeout(total=180, connect=15)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        # Launch all tasks — semaphore controls actual concurrency
        coros = [
            process_task(task, session, semaphore, quota, stats, shutdown_event)
            for task in tasks
        ]
        # gather with return_exceptions so one failure doesn't cancel all
        await asyncio.gather(*coros, return_exceptions=True)

    elapsed = asyncio.get_event_loop().time() - start_time

    # Save results
    out_path, report = save_results(stats, quota, elapsed)

    # Print summary to console
    print("\n" + "=" * 70)
    print("SWARM v2 COMPLETE")
    print("=" * 70)
    s = report["summary"]
    print("  Total tasks:  %d" % s["total_tasks"])
    print("  Completed:    %d" % s["completed"])
    print("  Errors:       %d" % s["errors"])
    print("  Fallbacks:    %d" % s["fallbacks"])
    print("  Elapsed:      %.1fs" % elapsed)
    print("  Concurrency:  %d workers" % MAX_CONCURRENCY)
    print("")
    print("Per-model usage:")
    for mk, mu in report.get("per_model_usage", {}).items():
        print("  %-30s %4d calls (%d ok, %d errors)" % (
            mu["name"], mu["calls"], mu["ok"], mu["errors"]))
    print("")
    print("Per-category:")
    for cat, cs in report.get("per_category", {}).items():
        print("  %-20s %3d/%3d ok" % (cat, cs["ok"], cs["total"]))
    print("")
    print("Quota consumed this session:")
    for mk, count in report.get("quota_consumed", {}).items():
        if count > 0:
            print("  %-30s %d / %d" % (MODELS[mk]["name"], count, MODELS[mk]["daily_limit"]))
    print("")
    print("Results: %s" % out_path)
    print("=" * 70)

    # Send Telegram notification (best-effort)
    await send_telegram_summary(report)

    return report


# ---------------------------------------------------------------------------
# Task loading
# ---------------------------------------------------------------------------

def load_tasks_from_file(filepath: str) -> List[Dict[str, Any]]:
    """Load tasks from a JSON file."""
    path = Path(filepath)
    if not path.exists():
        log.error("File not found: %s", filepath)
        sys.exit(1)
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, list):
            log.error("Expected JSON array in %s", filepath)
            sys.exit(1)
        return _validate_tasks(data)
    except json.JSONDecodeError as e:
        log.error("Invalid JSON in %s: %s", filepath, e)
        sys.exit(1)


def load_tasks_from_stdin() -> Optional[List[Dict[str, Any]]]:
    """Load tasks from stdin if data is available."""
    if sys.stdin.isatty():
        return None
    raw = sys.stdin.read().strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            log.error("Expected JSON array from stdin")
            sys.exit(1)
        return _validate_tasks(data)
    except json.JSONDecodeError as e:
        log.error("Invalid JSON from stdin: %s", e)
        sys.exit(1)


def _validate_tasks(tasks: List[Dict]) -> List[Dict[str, Any]]:
    """Validate and normalize task dicts."""
    valid = []
    for i, t in enumerate(tasks):
        if "prompt" not in t:
            log.warning("Task %d missing 'prompt', skipping", i)
            continue
        task = {
            "id": t.get("id", "task-%d" % (i + 1)),
            "prompt": t["prompt"],
            "complexity": t.get("complexity", "medium"),
            "category": t.get("category", "general"),
        }
        # Normalize complexity
        if task["complexity"] not in ROUTING_ORDER:
            task["complexity"] = "medium"
        valid.append(task)

    if not valid:
        log.error("No valid tasks found")
        sys.exit(1)

    log.info("Loaded %d valid tasks", len(valid))
    return valid


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    _load_env_vars()

    # Determine task source
    tasks = None

    if len(sys.argv) > 1 and sys.argv[1] not in ("--help", "-h"):
        # Argument provided: treat as file path
        tasks = load_tasks_from_file(sys.argv[1])
    else:
        # Try stdin
        tasks = load_tasks_from_stdin()

    if tasks is None:
        # No file and no stdin — generate default Zoar tasks
        log.info("No task file or stdin provided. Generating default Zoar task set.")
        tasks = build_default_tasks()

    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        sys.exit(0)

    # Check which models are available
    available = [k for k in MODELS if _model_available(k)]
    unavailable = [k for k in MODELS if not _model_available(k)]
    log.info("Available models: %s", ", ".join(MODELS[k]["name"] for k in available))
    if unavailable:
        log.warning("Unavailable (no API key): %s", ", ".join(MODELS[k]["name"] for k in unavailable))

    if not available:
        log.error("No models available. Set at least one API key.")
        sys.exit(1)

    # Run the swarm
    try:
        asyncio.run(run_swarm(tasks))
    except KeyboardInterrupt:
        log.info("Interrupted by user")
        sys.exit(130)


if __name__ == "__main__":
    main()
