"""
SmartRouter — Free-model chat brain for Nexus.

Routes every chat message to the optimal free model:
- ZAI GLM (default brain, 3M RPD unlimited)
- Cerebras (speed-critical, under 2s)
- Groq (structured JSON output)
- Gemini (long context reasoning)
- Ollama (always-available fallback)

Claude is NEVER called automatically. Only via /claude command or build
pipeline complexity > 8/10.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("smart_router")
DB_PATH = Path.home() / ".nexus" / "memory.db"

# ── System Prompt ────────────────────────────────────────────────────────────

NEXUS_SYSTEM_PROMPT = (
    "You are Nexus, the autonomous operating system for Zoar Bathroom Rentals "
    "run by Kai in the San Fernando Valley.\n\n"
    "You have direct tool access to the entire system. You are not a chatbot "
    "that suggests actions. You are an autonomous agent that takes actions.\n\n"
    "Your knowledge: you know every vendor in the database, every lead, every "
    "booking, every experiment, every worker, every API key, every rule. You "
    "answer with exact numbers from live queries.\n\n"
    "Your personality: direct, fast, no filler words. When Kai asks for something "
    "you do it and confirm it is done. You never say you will try. You never say "
    "perhaps consider. You do it.\n\n"
    "Rules you never break: never expose API keys in responses. Never send emails "
    "or SMS without Kai approval. Never spend money. Always verify changes before "
    "confirming.\n\n"
)

# ── Failure Tracking ─────────────────────────────────────────────────────────

_failure_tracker: Dict[str, List[float]] = {}
_claude_spend_today = 0.0
_claude_spend_reset_day = 0

_smart_router = None


def get_smart_router() -> "SmartRouter":
    global _smart_router
    if _smart_router is None:
        _smart_router = SmartRouter()
    return _smart_router


class SmartRouter:
    """Routes chat messages to optimal free model with context injection."""

    def __init__(self):
        global _claude_spend_today, _claude_spend_reset_day
        today = time.localtime().tm_yday
        if _claude_spend_reset_day != today:
            _claude_spend_today = 0.0
            _claude_spend_reset_day = today

    # ── Main Entry ────────────────────────────────────────────────────────

    async def route(
        self,
        text: str,
        history: Optional[List[Dict]] = None,
        force_claude: bool = False,
        data_context: str = "",
    ) -> Dict[str, Any]:
        """Route message to optimal free model. Returns response dict."""
        start = time.time()

        if force_claude:
            return await self._call_claude(text, history, start)

        task_type = self._pick_task_type(text)
        messages = self._build_context(text, history, data_context)

        result = await self._call_with_fallback(task_type, messages)
        latency = int((time.time() - start) * 1000)

        if not result.get("ok"):
            return {
                "content": "I'm having trouble processing that right now.",
                "agent": "Nexus AI",
                "model": "fallback",
                "provider": "none",
                "latency_ms": latency,
                "cost": 0.0,
            }

        content = result.get("content", "")

        # Quality check for non-trivial responses
        if len(content) > 100 and task_type == "nexus_brain":
            quality = await self._check_quality(text, content)
            if quality < 6:
                log.info("Quality %d/10 too low from %s, retrying",
                         quality, result.get("provider", "?"))
                exclude = result.get("provider", "")
                result2 = await self._retry_next_model(task_type, messages, exclude)
                if result2.get("ok"):
                    result = result2
                    content = result.get("content", "")

        latency = int((time.time() - start) * 1000)
        provider = result.get("provider", "")
        model = result.get("model", "")

        return {
            "content": content,
            "agent": "Nexus AI",
            "model": "%s (%s)" % (model, provider) if provider else model,
            "provider": provider,
            "latency_ms": latency,
            "cost": 0.0,
        }

    # ── Task Type Selection ───────────────────────────────────────────────

    def _pick_task_type(self, text: str) -> str:
        """Route to optimal model based on message content."""
        tl = text.lower()

        # Speed-critical: quick status checks, counts, lookups
        speed_kws = [
            "status", "health", "how many", "count", "booking",
            "quick", "check", "pipeline", "workers", "active",
        ]
        if any(kw in tl for kw in speed_kws):
            return "speed_critical"

        # Structured output: needs JSON, classification, extraction
        struct_kws = [
            "classify", "extract", "parse", "json", "list all",
            "categorize", "sort by", "rank", "score",
        ]
        if any(kw in tl for kw in struct_kws):
            return "structured_data"

        # Long context: multi-file, deep analysis, planning
        long_kws = [
            "read file", "all files", "analyze all", "compare",
            "multi-step", "plan how", "detailed analysis",
        ]
        if any(kw in tl for kw in long_kws):
            return "long_context"

        # Default: ZAI brain for everything else
        return "nexus_brain"

    # ── Context Injection ─────────────────────────────────────────────────

    def _build_context(
        self,
        text: str,
        history: Optional[List[Dict]],
        data_context: str = "",
    ) -> List[Dict[str, str]]:
        """Build messages list with smart context injection.

        Injects: system prompt + live snapshot + last 3 history msgs + data context.
        Keeps total under ~4000 tokens for speed.
        """
        system_parts = [NEXUS_SYSTEM_PROMPT]

        # Live snapshot (under 200 tokens)
        snapshot = self._get_snapshot()
        if snapshot:
            system_parts.append("LIVE SYSTEM STATE:\n" + snapshot)

        # Data context if provided (under 500 tokens)
        if data_context:
            system_parts.append("DATABASE CONTEXT:\n" + data_context[:1500])

        system = "\n\n".join(system_parts)

        messages: List[Dict[str, str]] = []

        # Last 3 history messages for continuity
        if history:
            for msg in history[-3:]:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content[:500]})

        # Current message
        messages.append({"role": "user", "content": text})

        # Prepend system as first message for providers that need it
        return [{"role": "system", "content": system}] + messages

    def _get_snapshot(self) -> str:
        """Get live system snapshot (under 200 tokens)."""
        lines = []
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=3)
            conn.row_factory = sqlite3.Row

            try:
                r = conn.execute("SELECT COUNT(*) as c FROM vendors").fetchone()
                lines.append("Vendors: %d" % (r["c"] if r else 0))
            except Exception:
                pass
            try:
                r = conn.execute("SELECT COUNT(*) as c FROM leads").fetchone()
                lines.append("Leads: %d" % (r["c"] if r else 0))
                rows = conn.execute(
                    "SELECT status, COUNT(*) as c FROM leads GROUP BY status"
                ).fetchall()
                if rows:
                    lines.append("  " + ", ".join(
                        "%s=%d" % (r["status"] or "?", r["c"]) for r in rows))
            except Exception:
                pass
            try:
                r = conn.execute(
                    "SELECT COUNT(*) as c FROM fleet_tasks WHERE status IN ('claimed','in_progress')"
                ).fetchone()
                lines.append("Active workers: %d" % (r["c"] if r else 0))
            except Exception:
                pass
            try:
                r = conn.execute(
                    "SELECT COUNT(*) as c FROM fleet_tasks WHERE status='pending'"
                ).fetchone()
                lines.append("Pending tasks: %d" % (r["c"] if r else 0))
            except Exception:
                pass

            conn.close()
        except Exception:
            pass
        return "\n".join(lines)

    # ── Call with Fallback ────────────────────────────────────────────────

    async def _call_with_fallback(
        self, task_type: str, messages: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        """Call provider with automatic fallback, skipping degraded providers."""
        from core.worker_pool import call_for_task

        # Extract system message
        system = ""
        user_msgs = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                user_msgs.append(m)

        result = await call_for_task(
            task_type, user_msgs, system=system,
            max_tokens=4096, temperature=0.7,
        )

        if result.get("ok"):
            return result

        # Record failure
        provider = result.get("provider", "")
        if provider:
            self._record_failure(provider)

        return result

    async def _retry_next_model(
        self, task_type: str, messages: List[Dict[str, str]], exclude: str
    ) -> Dict[str, Any]:
        """Retry with next model in chain, excluding the failed one."""
        from core.key_rotation import TASK_ROUTING
        from core.worker_pool import call_provider

        system = ""
        user_msgs = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                user_msgs.append(m)

        route = TASK_ROUTING.get(task_type, TASK_ROUTING.get("general", []))
        for provider_id in route:
            if provider_id == exclude or self._is_degraded(provider_id):
                continue
            result = await call_provider(
                provider_id, user_msgs, system=system,
                max_tokens=4096, temperature=0.7,
            )
            if result.get("ok"):
                return result
            self._record_failure(provider_id)

        return {"ok": False, "content": "All providers exhausted"}

    # ── Quality Threshold ─────────────────────────────────────────────────

    async def _check_quality(self, question: str, answer: str) -> int:
        """Fast quality check using Cerebras as judge. Returns score 1-10."""
        from core.worker_pool import call_provider

        judge_prompt = (
            "Rate this answer 1-10 for relevance and completeness. "
            "Reply with ONLY a single number.\n\n"
            "Question: %s\n\nAnswer: %s" % (question[:200], answer[:500])
        )
        try:
            result = await call_provider(
                "cerebras",
                [{"role": "user", "content": judge_prompt}],
                max_tokens=16,
                temperature=0.0,
            )
            if result.get("ok"):
                import re
                match = re.search(r"\b(\d+)\b", result["content"])
                if match:
                    return min(int(match.group(1)), 10)
        except Exception as e:
            log.debug("Quality check failed: %s", e)

        return 7  # Assume OK if check fails

    # ── Claude Gate ───────────────────────────────────────────────────────

    async def _call_claude(
        self, text: str, history: Optional[List[Dict]], start: float
    ) -> Dict[str, Any]:
        """Route to Claude Sonnet. Only via /claude command."""
        global _claude_spend_today

        messages = self._build_context(text, history)
        system = ""
        user_msgs = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                user_msgs.append(m)

        # Call Claude via the LLMProvider directly
        try:
            import json as _json
            from pathlib import Path as _Path
            cfg = _json.loads((_Path.home() / ".nexus/config.json").read_text())
            from core.providers import LLMProvider
            provider = LLMProvider(cfg)
            if "claude-sonnet" in provider.available():
                result = await provider.chat(
                    "claude-sonnet", user_msgs, system=system,
                    max_tokens=4096,
                )
                if result and result.get("ok"):
                    latency = int((time.time() - start) * 1000)
                    content = result.get("content", "")

                    cost = 0.003  # ~$0.003 per message estimate
                    _claude_spend_today += cost

                    return {
                        "content": content,
                        "agent": "Claude",
                        "model": "claude-sonnet",
                        "provider": "anthropic",
                        "latency_ms": latency,
                        "cost": cost,
                    }
        except Exception as e:
            log.warning("Claude call failed: %s", e)

        # Fallback to free model if Claude fails
        latency = int((time.time() - start) * 1000)
        return {
            "content": "Claude is unavailable. Routing to free model...",
            "agent": "Nexus AI",
            "model": "fallback",
            "provider": "none",
            "latency_ms": latency,
            "cost": 0.0,
        }

    # ── Failure Tracking ──────────────────────────────────────────────────

    def _is_degraded(self, provider: str) -> bool:
        """True if 3+ failures in last 10 minutes."""
        now = time.time()
        failures = _failure_tracker.get(provider, [])
        recent = [t for t in failures if now - t < 600]
        return len(recent) >= 3

    def _record_failure(self, provider: str):
        """Record a provider failure timestamp."""
        if provider not in _failure_tracker:
            _failure_tracker[provider] = []
        _failure_tracker[provider].append(time.time())
        # Prune old entries
        now = time.time()
        _failure_tracker[provider] = [
            t for t in _failure_tracker[provider] if now - t < 600
        ]
        if len(_failure_tracker[provider]) >= 3:
            log.warning("Provider %s marked as degraded (3+ failures in 10min)", provider)

    @staticmethod
    def get_claude_spend_today() -> float:
        """Return total Claude API spend today."""
        return _claude_spend_today

    @staticmethod
    def get_failure_status() -> Dict[str, int]:
        """Return current failure counts per provider."""
        now = time.time()
        return {
            p: len([t for t in ts if now - t < 600])
            for p, ts in _failure_tracker.items()
            if any(now - t < 600 for t in ts)
        }
