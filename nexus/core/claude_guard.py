"""
Claude Usage Guard — Prevents unnecessary Claude API calls.

All task routing passes through should_use_claude(). Tasks that can be handled
by Aider+Groq/Gemini are NEVER sent to Claude. Only architecture decisions,
security-sensitive review, and complex debugging after free model failure
escalate to Claude.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("claude_guard")

GUARD_LOG = Path.home() / ".nexus" / "orchestrator_logs" / "claude_guard.jsonl"

# Tasks that NEVER need Claude
FREE_MODEL_TASKS = [
    "vendor_research", "outreach_drafting", "bulk_extraction",
    "contact_parsing", "fb_page_collection", "ig_bio_extraction",
    "content_generation", "ad_copy", "data_processing",
    "vendor_data", "low_priority", "background",
    "boilerplate", "crud", "test_writing",
    "simple_refactor", "comment_adding", "formatting",
    "bug_fix", "coding_task", "batch_queue",
    "telegram_approval", "health_check", "analysis",
]

# Tasks that MAY need Claude (only after free model failure)
ESCALATION_TASKS = [
    "architecture", "security_review", "complex_debug",
    "system_design", "final_review",
]


class ClaudeGuard:
    """Intercepts task routing to conserve Claude usage."""

    def __init__(self):
        self._daily_claude_calls = 0
        self._daily_blocked = 0
        self._last_reset_day = time.strftime("%Y-%m-%d")
        GUARD_LOG.parent.mkdir(parents=True, exist_ok=True)

    def should_use_claude(self, task_type: str, description: str = "",
                          free_model_failed: bool = False) -> bool:
        """Returns True ONLY if Claude is truly needed for this task."""
        self._reset_daily_if_needed()

        if task_type in FREE_MODEL_TASKS:
            self._log_decision(task_type, description, "blocked", "free_model_task")
            self._daily_blocked += 1
            return False

        if task_type in ESCALATION_TASKS:
            if free_model_failed:
                self._log_decision(task_type, description, "allowed", "escalation_after_failure")
                self._daily_claude_calls += 1
                return True
            self._log_decision(task_type, description, "blocked", "try_free_first")
            self._daily_blocked += 1
            return False

        # Check for security/architecture keywords
        desc_lower = description.lower()
        security_kw = ["vulnerability", "auth bypass", "injection", "xss", "credential leak"]
        arch_kw = ["architecture decision", "system design", "database schema redesign"]
        for kw in security_kw + arch_kw:
            if kw in desc_lower:
                self._log_decision(task_type, description, "allowed", "keyword: " + kw)
                self._daily_claude_calls += 1
                return True

        self._log_decision(task_type, description, "blocked", "default_block")
        self._daily_blocked += 1
        return False

    def get_stats(self) -> dict:
        self._reset_daily_if_needed()
        return {
            "daily_claude_calls": self._daily_claude_calls,
            "daily_blocked": self._daily_blocked,
            "savings_estimate": "$%.2f" % (self._daily_blocked * 0.003),
            "date": self._last_reset_day,
        }

    def _log_decision(self, task_type: str, description: str,
                      decision: str, reason: str):
        entry = {
            "ts": time.time(),
            "task_type": task_type,
            "desc": description[:100],
            "decision": decision,
            "reason": reason,
        }
        try:
            with open(GUARD_LOG, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    def _reset_daily_if_needed(self):
        today = time.strftime("%Y-%m-%d")
        if today != self._last_reset_day:
            log.info("Claude guard daily reset: %d calls, %d blocked",
                     self._daily_claude_calls, self._daily_blocked)
            self._daily_claude_calls = 0
            self._daily_blocked = 0
            self._last_reset_day = today


# Singleton
_guard = None  # type: Optional[ClaudeGuard]


def get_claude_guard() -> ClaudeGuard:
    global _guard
    if _guard is None:
        _guard = ClaudeGuard()
    return _guard
