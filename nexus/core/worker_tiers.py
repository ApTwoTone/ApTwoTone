"""
Nexus Seven-Tier Worker Architecture

ALL AGENTS READ-ONLY: Vendor research agents are permanently read-only.
Zero interaction with any platform content ever. (CLAUDE.md Rule 24)

Tier 1 — Lead Response (30):  Groq → Cerebras → Mistral          5s
Tier 2 — Vendor Outreach (40): Mistral → ZAI → OpenRouter        60s
Tier 3 — Vendor Research (80): Groq → Cerebras → HF → Mistral   120s
Tier 4 — Creative Prod (40):  ZAI → Mistral → Moonshot          120s
Tier 5 — Build Pipeline (20): Gemini → Groq → Cerebras          180s
Tier 6 — D3 Experiments (60): ZAI → Gemini → Groq               180s
Tier 7 — Background/SEO (40): ApiFreeLLM → HF → Ollama → ZAI   300s

Total governed maximum: 310 simultaneous workers.

Usage:
    from core.worker_tiers import WorkerGovernor, get_governor
    gov = get_governor()
    worker = await gov.acquire_worker(tier=3)
    result = await worker.execute(task)
    gov.release_worker(worker)
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("worker_tiers")


# ── Tier Configuration ───────────────────────────────────────────────────────

TIER_CONFIG = {
    1: {
        "name": "Lead Response",
        "color": "red",
        "max_workers": 30,
        "providers": ["groq", "cerebras", "mistral"],
        "timeout_secs": 5,
        "task_types": [
            "lead_response", "quote_generation", "urgent_outreach",
            "speed_critical", "structured_data",
        ],
    },
    2: {
        "name": "Vendor Outreach",
        "color": "orange",
        "max_workers": 40,
        "providers": ["mistral", "zai", "openrouter"],
        "timeout_secs": 60,
        "task_types": [
            "outreach_drafting", "cold_email", "follow_up",
            "partnership_outreach", "venue_contact",
        ],
    },
    3: {
        "name": "Vendor Research",
        "color": "blue",
        "max_workers": 80,
        "providers": ["groq", "cerebras", "huggingface", "mistral"],
        "timeout_secs": 120,
        "task_types": [
            "vendor_research", "event_signal", "deep_research",
            "multi_source_synthesis", "research", "analysis",
            "long_context", "complex_research", "vendor_data",
            "contact_parsing", "fb_page_collection", "ig_bio_extraction",
            "bulk_extraction", "db_enrichment",
        ],
    },
    4: {
        "name": "Creative Prod",
        "color": "purple",
        "max_workers": 40,
        "providers": ["zai", "mistral", "moonshot"],
        "timeout_secs": 120,
        "task_types": [
            "ad_copy", "content_generation", "email_sequence",
            "content_calendar", "creative_brief", "social_post",
        ],
    },
    5: {
        "name": "Build Pipeline",
        "color": "green",
        "max_workers": 20,
        "providers": ["gemini", "groq", "cerebras"],
        "timeout_secs": 180,
        "task_types": [
            "planning", "code_review", "documentation",
            "pipeline_task", "system_improvement",
            "studio_architect", "studio_build", "studio_review",
            "studio_patch", "studio_bug_scan",
        ],
    },
    6: {
        "name": "D3 Experiments",
        "color": "cyan",
        "max_workers": 60,
        "providers": ["zai", "gemini", "groq"],
        "timeout_secs": 180,
        "task_types": [
            "d3_trend_scan", "d3_experiment", "d3_evaluate",
            "d3_self_improve", "d3_portfolio", "d3_task",
        ],
    },
    7: {
        "name": "Background/SEO",
        "color": "grey",
        "max_workers": 40,
        "providers": ["apifreellm", "huggingface", "ollama", "zai"],
        "timeout_secs": 300,
        "task_types": [
            "background", "low_priority", "data_processing",
            "seo_content", "sitemap_gen", "keyword_research",
        ],
    },
}

MAX_TOTAL_WORKERS = 310


# ── Shift Schedule ───────────────────────────────────────────────────────────

SHIFT_DAY = {  # 6am - 10pm
    1: 30,
    2: 40,
    3: 80,
    4: 40,
    5: 20,
    6: 60,
    7: 40,
}

SHIFT_NIGHT = {  # 10pm - 6am
    1: 5,    # Emergency lead responses only
    2: 10,   # Minimal outreach
    3: 80,   # Full vendor research
    4: 20,   # Half creative
    5: 5,    # Minimal pipeline
    6: 60,   # Full D3 experiments
    7: 40,   # Full background
}


def get_current_shift() -> Dict[int, int]:
    """Return worker limits based on current time of day."""
    hour = time.localtime().tm_hour
    if 6 <= hour < 22:
        return SHIFT_DAY
    return SHIFT_NIGHT


def get_shift_name() -> str:
    hour = time.localtime().tm_hour
    if 6 <= hour < 22:
        return "day"
    return "night"


# ── Token Budgets ────────────────────────────────────────────────────────────

TOKEN_BUDGETS = {
    # Tier 1 — Lead Response
    "lead_response": 1000,
    "quote_generation": 800,
    "urgent_outreach": 500,
    # Tier 2 — Vendor Outreach
    "outreach_drafting": 800,
    "cold_email": 800,
    "follow_up": 500,
    "partnership_outreach": 1000,
    # Tier 3 — Vendor Research
    "vendor_research": 2000,
    "deep_research": 2000,
    "vendor_contact_extraction": 500,
    "bulk_extraction": 500,
    "event_signal": 1500,
    "complex_research": 3000,
    # Tier 4 — Creative
    "ad_copy": 1000,
    "content_generation": 1500,
    "email_sequence": 2000,
    "content_calendar": 2000,
    "creative_brief": 1500,
    # Tier 5 — Build Pipeline
    "planning": 2000,
    "pipeline_task": 2000,
    # Tier 6 — D3 Experiments
    "d3_trend_scan": 2000,
    "d3_experiment": 3000,
    "d3_evaluate": 1500,
    "d3_self_improve": 2000,
    # Tier 7 — Background
    "seo_content": 1500,
    "keyword_research": 1000,
    "complex_synthesis": 4000,
}

# Running averages for auto-tightening
_token_averages: Dict[str, List[int]] = {}


def get_token_budget(task_type: str) -> int:
    """Get max tokens for a task type, auto-tightened based on averages."""
    base = TOKEN_BUDGETS.get(task_type, 2000)
    avg_list = _token_averages.get(task_type, [])
    if len(avg_list) >= 10:
        avg = sum(avg_list) / len(avg_list)
        # If average is >80% of budget, keep budget. If <50%, tighten.
        if avg < base * 0.5:
            return max(int(avg * 1.5), 200)
    return base


def record_token_usage(task_type: str, tokens: int):
    """Record actual token usage for auto-tightening."""
    if task_type not in _token_averages:
        _token_averages[task_type] = []
    _token_averages[task_type].append(tokens)
    # Keep last 100 samples
    if len(_token_averages[task_type]) > 100:
        _token_averages[task_type] = _token_averages[task_type][-100:]


# ── Worker ───────────────────────────────────────────────────────────────────

class Worker:
    """A single worker that executes tasks from the queue."""

    def __init__(self, worker_id: str, tier: int):
        self.worker_id = worker_id
        self.tier = tier
        self.status = "idle"  # idle, active, waiting_key, failed
        self.current_task = None  # type: Optional[Dict]
        self.current_provider = ""
        self.current_model = ""
        self.task_start_time = 0.0
        self.tokens_used = 0
        self.tasks_completed = 0
        self.tasks_failed = 0
        self.last_active = time.time()

    def to_dict(self) -> Dict[str, Any]:
        elapsed = 0
        if self.task_start_time > 0:
            elapsed = int((time.time() - self.task_start_time) * 1000)
        return {
            "worker_id": self.worker_id,
            "tier": self.tier,
            "tier_name": TIER_CONFIG[self.tier]["name"],
            "tier_color": TIER_CONFIG[self.tier]["color"],
            "status": self.status,
            "current_task_type": self.current_task.get("task_type", "") if self.current_task else "",
            "current_provider": self.current_provider,
            "current_model": self.current_model,
            "elapsed_ms": elapsed,
            "tokens_used": self.tokens_used,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
        }


# ── Worker Governor ──────────────────────────────────────────────────────────

class WorkerGovernor:
    """Governs worker allocation across tiers with shift-aware limits."""

    def __init__(self):
        self._workers: Dict[str, Worker] = {}  # worker_id -> Worker
        self._tier_counts: Dict[int, int] = {t: 0 for t in TIER_CONFIG}
        self._lock = asyncio.Lock()
        self._next_id = 0
        self._total_tasks_completed = 0
        self._total_tokens = 0
        self._start_time = time.time()

    async def acquire_worker(self, tier: int) -> Optional[Worker]:
        """Try to acquire a worker slot for a tier. Returns Worker or None."""
        async with self._lock:
            shift = get_current_shift()
            max_for_tier = shift.get(tier, 0)
            total_active = sum(self._tier_counts.values())

            if self._tier_counts.get(tier, 0) >= max_for_tier:
                return None
            if total_active >= MAX_TOTAL_WORKERS:
                return None

            self._next_id += 1
            wid = "w-%d-%d" % (tier, self._next_id)
            worker = Worker(wid, tier)
            self._workers[wid] = worker
            if tier not in self._tier_counts:
                self._tier_counts[tier] = 0
            self._tier_counts[tier] += 1
            return worker

    async def release_worker(self, worker: Worker):
        """Release a worker slot back to the pool."""
        async with self._lock:
            if worker.worker_id in self._workers:
                self._total_tasks_completed += worker.tasks_completed
                self._total_tokens += worker.tokens_used
                del self._workers[worker.worker_id]
                self._tier_counts[worker.tier] = max(0, self._tier_counts[worker.tier] - 1)

    def get_active_workers(self) -> List[Dict[str, Any]]:
        """Get all active worker details."""
        return [w.to_dict() for w in self._workers.values()]

    def get_status(self) -> Dict[str, Any]:
        """Full governor status for dashboard."""
        shift = get_current_shift()
        shift_name = get_shift_name()

        tier_status = {}
        for tier in TIER_CONFIG:
            cfg = TIER_CONFIG[tier]
            active = self._tier_counts[tier]
            tier_status[tier] = {
                "name": cfg["name"],
                "color": cfg["color"],
                "active_workers": active,
                "max_workers": shift[tier],
                "shift_max": shift[tier],
                "providers": cfg["providers"],
                "task_types": cfg["task_types"],
            }

        # In-memory governor count (processes that registered)
        governor_active = sum(self._tier_counts.values())

        # Also check fleet_tasks DB for actually claimed/in_progress tasks
        # (standalone workers don't register with the governor)
        db_active = 0
        try:
            db_path = Path.home() / ".nexus" / "memory.db"
            conn = sqlite3.connect(str(db_path), timeout=5)
            row = conn.execute(
                "SELECT COUNT(*) FROM fleet_tasks WHERE status IN ('claimed', 'in_progress')"
            ).fetchone()
            db_active = row[0] if row else 0
            conn.close()
        except Exception:
            pass

        total_active = max(governor_active, db_active)

        return {
            "shift": shift_name,
            "total_active": total_active,
            "max_total": MAX_TOTAL_WORKERS,
            "tiers": tier_status,
            "total_tasks_completed": self._total_tasks_completed,
            "total_tokens": self._total_tokens,
            "uptime_hours": round((time.time() - self._start_time) / 3600, 1),
            "workers": self.get_active_workers(),
        }

    async def enforce_limits(self):
        """Check worker counts and terminate idle workers above ceiling.

        Called by the governor process every 5 seconds.
        """
        async with self._lock:
            shift = get_current_shift()
            now = time.time()

            # Check resource pressure for Ollama workers
            try:
                from core.resource_monitor import get_monitor
                scale = get_monitor().check()
            except Exception:
                scale = 1.0

            terminated = []
            for wid, worker in list(self._workers.items()):
                # Terminate idle workers above tier limit
                tier_limit = shift.get(worker.tier, 0)
                if self._tier_counts[worker.tier] > tier_limit:
                    if worker.status == "idle" and now - worker.last_active > 10:
                        terminated.append(wid)
                        continue

                # Apply Ollama resource scaling
                if worker.current_provider == "ollama" and scale < 1.0:
                    if worker.status == "idle":
                        terminated.append(wid)

            for wid in terminated:
                worker = self._workers.pop(wid)
                self._tier_counts[worker.tier] = max(0, self._tier_counts[worker.tier] - 1)
                log.info("Terminated worker %s (shift enforcement)", wid)

    def tier_for_task_type(self, task_type: str) -> int:
        """Determine which tier handles a task type."""
        for tier, cfg in TIER_CONFIG.items():
            if task_type in cfg["task_types"]:
                return tier
        return 7  # Default to background


# ── Global Singleton ─────────────────────────────────────────────────────────

_governor: Optional[WorkerGovernor] = None


def get_governor() -> WorkerGovernor:
    global _governor
    if _governor is None:
        _governor = WorkerGovernor()
    return _governor
