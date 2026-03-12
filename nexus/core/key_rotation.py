"""
Nexus Multi-Provider Key Rotation Engine

Central hub for all AI provider API keys. Provides:
- Round-robin key rotation per provider (3 keys = 3x throughput)
- Per-key rate limit tracking with automatic cooldown
- Provider health monitoring
- Task-type routing to optimal providers
- Fleet status reporting

Usage:
    pool = KeyPool.from_config(config_dict)
    key = pool.next_key("groq")           # Round-robin next key
    pool.report_rate_limit("groq", key)   # Mark key as rate-limited
    status = pool.fleet_status()          # Full fleet report
"""
from __future__ import annotations

import json
import logging
import math
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("key_rotation")

# ── Provider Definitions ─────────────────────────────────────────────────────

PROVIDERS: Dict[str, Dict[str, Any]] = {
    "groq": {
        "name": "Groq",
        "endpoint": "https://api.groq.com/openai/v1/chat/completions",
        "model": "meta-llama/llama-4-scout-17b-16e-instruct",
        "rpm_per_key": 30,
        "rpd_per_key": 1000,
        "openai_compat": True,
        "extra_headers": {"User-Agent": "Nexus/1.0"},
        "task_types": ["speed_critical", "structured_data", "vendor_research"],
        "strength": "Fastest inference, structured JSON output",
    },
    "cerebras": {
        "name": "Cerebras",
        "endpoint": "https://api.cerebras.ai/v1/chat/completions",
        "model": "llama3.1-8b",
        "rpm_per_key": 30,
        "rpd_per_key": 14400,
        "openai_compat": True,
        "extra_headers": {},
        "task_types": ["speed_critical", "boilerplate", "simple_coding"],
        "strength": "Ultra-fast inference, good for quick tasks",
    },
    "gemini": {
        "name": "Gemini 3 Flash",
        "endpoint": "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent",
        "model": "gemini-3-flash-preview",
        "rpm_per_key": 15,
        "rpd_per_key": 500,
        "openai_compat": False,
        "extra_headers": {},
        "task_types": ["research", "analysis", "long_context", "planning"],
        "strength": "1M context window, strong reasoning and analysis",
    },
    "openrouter": {
        "name": "OpenRouter",
        "endpoint": "https://openrouter.ai/api/v1/chat/completions",
        "model": "meta-llama/llama-3.1-8b-instruct",
        "rpm_per_key": 10,
        "rpd_per_key": 200,
        "openai_compat": True,
        "extra_headers": {"HTTP-Referer": "https://nexus.zoarbathroomrental.com"},
        "task_types": ["research", "analysis", "medium_coding"],
        "strength": "Access to many free models, good reasoning",
    },
    "mistral": {
        "name": "Mistral",
        "endpoint": "https://api.mistral.ai/v1/chat/completions",
        "model": "open-mistral-nemo",
        "rpm_per_key": 60,
        "rpd_per_key": 10000,
        "openai_compat": True,
        "extra_headers": {},
        "task_types": ["multilingual", "structured_output", "content_generation"],
        "strength": "Multilingual excellence, structured JSON output",
    },
    "huggingface": {
        "name": "HuggingFace",
        "endpoint": "https://router.huggingface.co/v1/chat/completions",
        "model": "meta-llama/Llama-3.1-8B-Instruct",
        "rpm_per_key": 30,
        "rpd_per_key": 5000,
        "openai_compat": True,
        "extra_headers": {},
        "task_types": ["bulk_extraction", "data_processing", "background"],
        "strength": "200+ models, bulk data extraction",
    },
    "moonshot": {
        "name": "Moonshot/Kimi",
        "endpoint": "https://api.moonshot.ai/v1/chat/completions",
        "model": "moonshot-v1-8k",
        "rpm_per_key": 3,
        "rpd_per_key": 500,
        "openai_compat": True,
        "extra_headers": {},
        "task_types": ["complex_research", "long_context", "multi_step"],
        "strength": "Complex multi-step research, 128K context",
    },
    "zai": {
        "name": "Z.AI GLM",
        "endpoint": "https://api.z.ai/api/paas/v4/chat/completions",
        "model": "glm-4.5-flash",
        "rpm_per_key": 60,
        "rpd_per_key": 999999,
        "openai_compat": False,
        "extra_headers": {},
        "task_types": ["content_generation", "ad_copy", "creative_writing"],
        "strength": "Unlimited free tier, content generation and ad copy",
    },
    "apifreellm": {
        "name": "ApiFreeLLM",
        "endpoint": "https://apifreellm.com/api/v1/chat",
        "model": "apifreellm",
        "rpm_per_key": 2,
        "rpd_per_key": 5760,
        "openai_compat": False,
        "extra_headers": {},
        "task_types": ["background", "low_priority", "vendor_data"],
        "strength": "Free background processing, no token limits",
    },
    "ollama": {
        "name": "Ollama Local",
        "endpoint": "http://localhost:11434/api/chat",
        "model": "llama3.2:3b",
        "rpm_per_key": 999,
        "rpd_per_key": 999999,
        "openai_compat": False,
        "extra_headers": {},
        "task_types": ["background", "drafts", "exploration", "fallback"],
        "strength": "Unlimited local, zero cost, always available",
    },
}

# ── Task-Type to Provider Routing ────────────────────────────────────────────

TASK_ROUTING: Dict[str, List[str]] = {
    # Speed-critical: lead response, instant quotes
    "speed_critical": ["groq", "mistral", "ollama"],
    # Structured data: vendor research, JSON generation
    "structured_data": ["groq", "mistral", "huggingface", "ollama"],
    "creative_writing": ["zai", "mistral", "moonshot", "ollama"],
    # Coding tasks
    "simple_coding": ["groq", "mistral", "ollama"],
    "medium_coding": ["openrouter", "mistral", "groq", "ollama"],
    "boilerplate": ["groq", "ollama"],
    # Multilingual
    "multilingual": ["mistral", "openrouter", "zai", "ollama"],
    "structured_output": ["mistral", "groq", "ollama"],
    # Bulk & background
    "bulk_extraction": ["huggingface", "groq", "ollama"],
    "data_processing": ["huggingface", "groq", "ollama"],
    "background": ["apifreellm", "ollama", "huggingface", "zai"],
    "low_priority": ["apifreellm", "ollama", "zai", "huggingface"],
    "vendor_data": ["apifreellm", "huggingface", "ollama", "zai"],
    "vendor_research": ["groq", "mistral", "ollama"],
    # Complex multi-step
    "complex_research": ["moonshot", "gemini", "openrouter", "ollama"],
    "multi_step": ["moonshot", "gemini", "openrouter", "ollama"],
    # Fallback
    "drafts": ["ollama", "apifreellm", "zai"],
    "exploration": ["ollama", "apifreellm", "zai"],
    "fallback": ["ollama", "zai", "apifreellm"],
    # Nexus Brain — primary chat intelligence (ZAI default, 3M RPD)
    "nexus_brain": ["zai", "groq", "mistral", "ollama"],
    # Default
    "general": ["groq", "mistral", "openrouter", "ollama"],
    # Build Pipeline — Multi-Agent feature builds
    "build_architect": ["gemini", "moonshot", "openrouter", "ollama"],
    "build_implement": ["groq", "mistral", "ollama"],
    "build_review": ["mistral", "openrouter", "zai", "ollama"],
    "build_patch": ["groq", "mistral", "ollama"],
    # Division Three — Revenue Experimentation
    "d3_persona_gen": ["zai", "mistral", "ollama"],
    "d3_trend_scan": ["gemini", "groq", "ollama"],
    "d3_content_gen": ["zai", "ollama", "mistral"],
    "d3_experiment_plan": ["gemini", "openrouter", "ollama"],
    "d3_seo_gen": ["zai", "ollama", "mistral"],
    "d3_product_gen": ["zai", "ollama", "mistral"],
    "d3_analysis": ["gemini", "groq", "ollama"],
    # Division Four — Self-Healing
    "d4_diagnose": ["groq", "mistral", "ollama"],
    "d4_repair": ["groq", "gemini", "ollama"],
    "d4_verify": ["ollama", "apifreellm", "huggingface"],
    "d4_learn": ["ollama", "apifreellm", "groq"],
    "d3_self_improve": ["gemini", "openrouter", "ollama"],
    # Agent Studio — Parallel build agents
    "studio_architect": ["gemini", "groq", "openrouter", "ollama"],
    "studio_build": ["ollama", "openrouter", "groq"],
    "studio_review": ["zai", "groq", "ollama"],
    "studio_patch": ["zai", "groq", "ollama"],
    "studio_bug_scan": ["groq", "zai", "ollama"],
}


# ── Key Pool ─────────────────────────────────────────────────────────────────

class KeyPool:
    """Manages API key rotation across all providers."""

    def __init__(self):
        self._keys: Dict[str, List[str]] = {}       # provider -> [keys]
        self._index: Dict[str, int] = {}             # provider -> current index
        self._cooldowns: Dict[str, float] = {}       # "provider:key" -> cooldown_until
        self._call_counts: Dict[str, int] = {}       # "provider:key" -> calls today
        self._error_counts: Dict[str, int] = {}      # "provider:key" -> consecutive errors
        self._backoff_counts: Dict[str, int] = {}    # "provider:key" -> backoff level for exp backoff
        self._last_reset: str = ""                    # date string for daily reset
        self._total_calls: int = 0

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "KeyPool":
        """Build pool from config.json key_pools section."""
        pool = cls()
        key_pools = config.get("key_pools", {})

        for provider, keys in key_pools.items():
            if isinstance(keys, list) and keys:
                pool._keys[provider] = keys
                pool._index[provider] = 0
                log.info("Loaded %d keys for %s", len(keys), provider)

        # Also add single-key fallbacks from legacy config fields
        legacy_map = {
            "groq": "groq_api_key",
            "gemini": "gemini_key",
            "zai": "zai_api_key",
            "moonshot": "moonshot_key",
            "openrouter": "openrouter_key",
        }
        for provider, config_key in legacy_map.items():
            val = config.get(config_key, "")
            if val and provider not in pool._keys:
                pool._keys[provider] = [val]
                pool._index[provider] = 0

        # Ollama always available (no key needed)
        if "ollama" not in pool._keys:
            pool._keys["ollama"] = ["local"]
            pool._index["ollama"] = 0

        return pool

    def _check_daily_reset(self):
        """Reset daily counters if it's a new day."""
        today = time.strftime("%Y-%m-%d")
        if today != self._last_reset:
            self._call_counts.clear()
            self._error_counts.clear()
            self._last_reset = today
            log.info("Daily counters reset for %s", today)

    def has_keys(self, provider: str) -> bool:
        """Check if a provider has any keys loaded."""
        return provider in self._keys and len(self._keys[provider]) > 0

    def key_count(self, provider: str) -> int:
        """Number of keys for a provider."""
        return len(self._keys.get(provider, []))

    def next_key(self, provider: str) -> Optional[str]:
        """Get next available key via round-robin, skipping rate-limited keys.

        Moonshot uses sequential consumption (exhaust key 0 before moving to key 1)
        to prevent all keys hitting their rate limit simultaneously.
        """
        self._check_daily_reset()

        keys = self._keys.get(provider, [])
        if not keys:
            return None

        now = time.time()
        n = len(keys)

        # Moonshot: sequential consumption (stick with one key until exhausted)
        if provider == "moonshot":
            return self._next_key_sequential(provider, keys, now)

        # OpenRouter safeguard: skip entirely if >150 total daily requests
        if provider == "openrouter":
            total_or = sum(
                self._call_counts.get("openrouter:%d" % i, 0)
                for i in range(n)
            )
            if total_or >= 150:
                log.info("OpenRouter daily safeguard: %d/150 used, skipping", total_or)
                return None

        start_idx = self._index.get(provider, 0)

        for i in range(n):
            idx = (start_idx + i) % n
            key = keys[idx]
            ck = "%s:%d" % (provider, idx)

            # Skip if in cooldown
            if self._cooldowns.get(ck, 0) > now:
                continue

            # Skip if too many consecutive errors
            if self._error_counts.get(ck, 0) >= 5:
                # But allow retry after 5 min
                if self._cooldowns.get(ck, 0) == 0:
                    self._cooldowns[ck] = now + 300
                continue

            # Check daily limit
            prov = PROVIDERS.get(provider, {})
            rpd = prov.get("rpd_per_key", 999999)
            if self._call_counts.get(ck, 0) >= rpd:
                continue

            # Use this key, advance index
            self._index[provider] = (idx + 1) % n
            self._call_counts[ck] = self._call_counts.get(ck, 0) + 1
            self._total_calls += 1
            return key

        log.warning("All keys exhausted for %s", provider)
        return None

    def _next_key_sequential(self, provider: str, keys: List[str], now: float) -> Optional[str]:
        """Sequential key consumption for rate-limited providers like Moonshot.

        Uses one key at a time until it's rate-limited, then moves to the next.
        Only advances when the current key is in cooldown.
        """
        n = len(keys)
        start_idx = self._index.get(provider, 0)
        prov = PROVIDERS.get(provider, {})
        rpd = prov.get("rpd_per_key", 999999)

        # First try the current preferred key
        ck = "%s:%d" % (provider, start_idx)
        if (self._cooldowns.get(ck, 0) <= now
                and self._error_counts.get(ck, 0) < 5
                and self._call_counts.get(ck, 0) < rpd):
            self._call_counts[ck] = self._call_counts.get(ck, 0) + 1
            self._total_calls += 1
            return keys[start_idx]

        # Current key unavailable — find next available
        for i in range(1, n):
            idx = (start_idx + i) % n
            ck = "%s:%d" % (provider, idx)
            if (self._cooldowns.get(ck, 0) <= now
                    and self._error_counts.get(ck, 0) < 5
                    and self._call_counts.get(ck, 0) < rpd):
                self._index[provider] = idx
                self._call_counts[ck] = self._call_counts.get(ck, 0) + 1
                self._total_calls += 1
                return keys[idx]

        log.warning("All Moonshot keys exhausted or rate-limited")
        return None

    def report_rate_limit(self, provider: str, key: str, cooldown_secs: float = 60):
        """Mark a key as rate-limited with exponential backoff + jitter.

        Moonshot keys share synchronized reset windows, so we use staggered
        exponential backoff with random jitter to avoid thundering herd.
        """
        keys = self._keys.get(provider, [])
        try:
            idx = keys.index(key)
        except ValueError:
            return
        ck = "%s:%d" % (provider, idx)

        # Exponential backoff: base * 2^level + random jitter
        level = self._backoff_counts.get(ck, 0)
        if provider == "moonshot":
            # Moonshot: aggressive backoff since only 3 RPM per key
            base = cooldown_secs  # 60s default
            backoff = min(base * math.pow(2, level), 600)  # Cap at 10 min
            jitter = random.uniform(0, backoff * 0.3)  # 30% jitter
            # Stagger keys: offset by key index to prevent synchronized retries
            stagger = idx * 15  # 15s gap between keys
            actual_cooldown = backoff + jitter + stagger
        else:
            # Other providers: simpler backoff
            backoff = min(cooldown_secs * math.pow(1.5, level), 300)
            jitter = random.uniform(0, backoff * 0.2)
            actual_cooldown = backoff + jitter

        self._cooldowns[ck] = time.time() + actual_cooldown
        self._backoff_counts[ck] = min(level + 1, 8)  # Cap backoff level
        log.info("Key %s:%d rate-limited for %.0fs (level=%d, jitter=%.0f)",
                 provider, idx, actual_cooldown, level, jitter)

    def report_error(self, provider: str, key: str):
        """Track consecutive errors on a key."""
        keys = self._keys.get(provider, [])
        try:
            idx = keys.index(key)
        except ValueError:
            return
        ck = "%s:%d" % (provider, idx)
        self._error_counts[ck] = self._error_counts.get(ck, 0) + 1

    def report_success(self, provider: str, key: str):
        """Clear error count and reset backoff on success."""
        keys = self._keys.get(provider, [])
        try:
            idx = keys.index(key)
        except ValueError:
            return
        ck = "%s:%d" % (provider, idx)
        self._error_counts[ck] = 0
        self._backoff_counts[ck] = 0

    def get_provider_for_task(self, task_type: str) -> Optional[Tuple[str, str]]:
        """Get the best (provider, key) pair for a task type.

        Returns the first provider with an available key in the routing order.
        """
        route = TASK_ROUTING.get(task_type, TASK_ROUTING["general"])
        for provider in route:
            key = self.next_key(provider)
            if key:
                return (provider, key)
        return None

    def fleet_status(self) -> Dict[str, Any]:
        """Full fleet status report for all providers."""
        self._check_daily_reset()
        now = time.time()
        providers_status = []
        total_rpm = 0
        total_keys = 0
        total_daily_capacity = 0

        for prov_id, prov_info in PROVIDERS.items():
            keys = self._keys.get(prov_id, [])
            n_keys = len(keys)
            if n_keys == 0:
                continue

            rpm_per_key = prov_info.get("rpm_per_key", 0)
            rpd_per_key = prov_info.get("rpd_per_key", 0)
            provider_rpm = rpm_per_key * n_keys
            provider_rpd = rpd_per_key * n_keys

            # Count active (non-cooldown) keys
            active_keys = 0
            for idx in range(n_keys):
                ck = "%s:%d" % (prov_id, idx)
                if self._cooldowns.get(ck, 0) <= now and self._error_counts.get(ck, 0) < 5:
                    active_keys += 1

            # Count today's calls
            day_calls = sum(
                self._call_counts.get("%s:%d" % (prov_id, i), 0)
                for i in range(n_keys)
            )

            total_rpm += provider_rpm
            total_keys += n_keys
            total_daily_capacity += provider_rpd

            providers_status.append({
                "id": prov_id,
                "name": prov_info["name"],
                "model": prov_info["model"],
                "keys_total": n_keys,
                "keys_active": active_keys,
                "rpm_per_key": rpm_per_key,
                "rpm_total": provider_rpm,
                "rpd_per_key": rpd_per_key,
                "rpd_total": provider_rpd,
                "calls_today": day_calls,
                "strength": prov_info["strength"],
                "task_types": prov_info["task_types"],
                "openai_compat": prov_info["openai_compat"],
            })

        # Parallel worker count = total RPM / avg ~2 sec per call = ~RPM/2
        parallel_workers = max(1, total_rpm // 2)

        return {
            "providers": providers_status,
            "total_providers": len(providers_status),
            "total_keys": total_keys,
            "total_rpm": total_rpm,
            "total_daily_capacity": total_daily_capacity,
            "parallel_workers": parallel_workers,
            "total_calls_today": self._total_calls,
        }


# ── Global Singleton ─────────────────────────────────────────────────────────

_pool: Optional[KeyPool] = None


def get_pool(config: Optional[Dict[str, Any]] = None) -> KeyPool:
    """Get or create the global KeyPool singleton."""
    global _pool
    if _pool is None:
        if config is None:
            cfg_path = Path.home() / ".nexus" / "config.json"
            if cfg_path.exists():
                config = json.loads(cfg_path.read_text())
            else:
                config = {}
        _pool = KeyPool.from_config(config)
    return _pool


def reset_pool():
    """Force re-creation of the pool (e.g., after config change)."""
    global _pool
    _pool = None
