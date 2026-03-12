"""
Nexus Token Budget Manager — Per-provider daily usage thresholds.

Prevents agents from exhausting provider limits by enforcing percentage-based
daily budgets. Reserves capacity for manual user requests (Talk to Nexus).

Integration: Called by worker_pool.call_provider() as a pre-flight check.
Usage is recorded after each successful call via a background flush queue.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("token_budget")

DB_PATH = Path.home() / ".nexus" / "memory.db"

# ── Provider Budget Configuration ────────────────────────────────────────────
# threshold_pct: max % of daily limit to use for automated tasks
# daily_requests: approximate daily request cap (RPD)
# daily_tokens: approximate daily token cap (0 = unlimited)
# reset_hour_utc: hour when daily limits reset (most providers = 0 UTC)

PROVIDER_BUDGETS = {
    "groq":       {"threshold_pct": 80, "daily_requests": 1000, "daily_tokens": 0, "reset_hour_utc": 0},
    "cerebras":   {"threshold_pct": 80, "daily_requests": 14400, "daily_tokens": 0, "reset_hour_utc": 0},
    "zai":        {"threshold_pct": 70, "daily_requests": 999999, "daily_tokens": 0, "reset_hour_utc": 0},
    "gemini":     {"threshold_pct": 90, "daily_requests": 500, "daily_tokens": 0, "reset_hour_utc": 0},
    "openrouter": {"threshold_pct": 60, "daily_requests": 200, "daily_tokens": 0, "reset_hour_utc": 0},
    "mistral":    {"threshold_pct": 80, "daily_requests": 1000, "daily_tokens": 0, "reset_hour_utc": 0},
    "huggingface": {"threshold_pct": 80, "daily_requests": 1000, "daily_tokens": 0, "reset_hour_utc": 0},
    "moonshot":   {"threshold_pct": 80, "daily_requests": 100, "daily_tokens": 0, "reset_hour_utc": 0},
    "apifreellm": {"threshold_pct": 80, "daily_requests": 50, "daily_tokens": 0, "reset_hour_utc": 0},
    "ollama":     {"threshold_pct": 100, "daily_requests": 999999, "daily_tokens": 0, "reset_hour_utc": 0},
}


# ── Token Budget Manager ────────────────────────────────────────────────────

class TokenBudgetManager:
    """Tracks and enforces per-provider daily token budgets."""

    _instance = None

    def __init__(self):
        self._write_queue = deque(maxlen=5000)
        self._lock = threading.Lock()
        self._last_flush = time.time()
        self._flush_interval = 30  # seconds
        # In-memory counters: {provider: {"requests": N, "tokens": N}}
        self._today_str = ""
        self._counters = {}  # type: Dict[str, Dict[str, int]]
        self._load_today()

    @classmethod
    def get_instance(cls):
        # type: () -> TokenBudgetManager
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _today_key(self):
        # type: () -> str
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _load_today(self):
        """Load today's usage from DB into memory."""
        today = self._today_key()
        if self._today_str == today:
            return
        self._today_str = today
        self._counters = {}
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=5)
            rows = conn.execute(
                "SELECT provider, COUNT(*), COALESCE(SUM(tokens_total), 0) "
                "FROM token_usage WHERE DATE(created_at) = ? GROUP BY provider",
                (today,)
            ).fetchall()
            conn.close()
            for provider, req_count, tok_count in rows:
                self._counters[provider] = {"requests": req_count, "tokens": tok_count}
        except Exception as e:
            log.warning("Failed to load token usage: %s", e)

    def check_budget(self, provider_id):
        # type: (str) -> bool
        """Return True if provider is under its daily threshold."""
        budget = PROVIDER_BUDGETS.get(provider_id)
        if not budget:
            return True  # Unknown provider, allow

        if budget["threshold_pct"] >= 100:
            return True  # Unlimited (e.g. ollama)

        self._load_today()
        counter = self._counters.get(provider_id, {"requests": 0, "tokens": 0})
        max_requests = int(budget["daily_requests"] * budget["threshold_pct"] / 100)
        return counter["requests"] < max_requests

    def get_usage_pct(self, provider_id):
        # type: (str) -> float
        """Return current usage as percentage of daily limit."""
        budget = PROVIDER_BUDGETS.get(provider_id)
        if not budget or budget["daily_requests"] <= 0:
            return 0.0

        self._load_today()
        counter = self._counters.get(provider_id, {"requests": 0, "tokens": 0})
        return min(100.0, counter["requests"] / budget["daily_requests"] * 100)

    def hours_until_cap(self, provider_id):
        # type: (str) -> float
        """Estimate hours until this provider hits its threshold at current rate."""
        budget = PROVIDER_BUDGETS.get(provider_id)
        if not budget or budget["threshold_pct"] >= 100:
            return 99.0

        self._load_today()
        counter = self._counters.get(provider_id, {"requests": 0, "tokens": 0})
        max_requests = int(budget["daily_requests"] * budget["threshold_pct"] / 100)
        remaining = max_requests - counter["requests"]
        if remaining <= 0:
            return 0.0

        # Calculate rate from today's usage
        now = datetime.now(timezone.utc)
        hours_elapsed = now.hour + now.minute / 60.0
        if hours_elapsed < 0.1:
            return 24.0  # Start of day, can't estimate

        rate_per_hour = counter["requests"] / hours_elapsed
        if rate_per_hour < 0.1:
            return 99.0  # Very low rate

        return min(99.0, remaining / rate_per_hour)

    def should_defer(self, provider_id, priority="normal"):
        # type: (str, str) -> bool
        """Return True if non-urgent tasks should wait for provider reset."""
        if priority == "user":
            return False  # Never defer user requests

        budget = PROVIDER_BUDGETS.get(provider_id)
        if not budget:
            return False

        # If within 2 hours of reset and over 90% of threshold
        hours_left = self.hours_until_cap(provider_id)
        usage_pct = self.get_usage_pct(provider_id)
        threshold = budget["threshold_pct"]

        if usage_pct >= threshold * 0.9 and hours_left < 2:
            return True

        return False

    def record_usage(self, provider_id, tokens_total, agent_id="", task_type="",
                     tokens_in=0, tokens_out=0, latency_ms=0, model=""):
        # type: (str, int, str, str, int, int, int, str) -> None
        """Queue a usage record for background flush to DB."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Update in-memory counters immediately
        if provider_id not in self._counters:
            self._counters[provider_id] = {"requests": 0, "tokens": 0}
        self._counters[provider_id]["requests"] += 1
        self._counters[provider_id]["tokens"] += tokens_total

        # Queue for DB write
        self._write_queue.append((
            provider_id, model, agent_id, task_type,
            tokens_in, tokens_out, tokens_total, latency_ms, ts
        ))

        # Flush if interval elapsed
        if time.time() - self._last_flush >= self._flush_interval:
            self.flush()

    def flush(self):
        """Write queued records to SQLite."""
        if not self._write_queue:
            return

        with self._lock:
            batch = []
            while self._write_queue:
                batch.append(self._write_queue.popleft())
            self._last_flush = time.time()

        if not batch:
            return

        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=10)
            conn.executemany(
                "INSERT INTO token_usage "
                "(provider, model, agent_id, task_type, tokens_in, tokens_out, "
                "tokens_total, latency_ms, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                batch
            )
            conn.commit()
            conn.close()
            log.debug("Flushed %d token usage records", len(batch))
        except Exception as e:
            log.warning("Failed to flush token usage: %s", e)

    def get_summary(self):
        # type: () -> Dict[str, Any]
        """Return summary for Telegram digest / API response."""
        self._load_today()
        providers = []
        total_requests = 0
        total_tokens = 0

        for pid, budget in PROVIDER_BUDGETS.items():
            counter = self._counters.get(pid, {"requests": 0, "tokens": 0})
            used_pct = self.get_usage_pct(pid)
            hrs = self.hours_until_cap(pid)
            providers.append({
                "provider": pid,
                "requests_today": counter["requests"],
                "tokens_today": counter["tokens"],
                "daily_limit": budget["daily_requests"],
                "threshold_pct": budget["threshold_pct"],
                "used_pct": round(used_pct, 1),
                "hours_until_cap": round(hrs, 1),
                "status": "ok" if used_pct < budget["threshold_pct"] else "capped",
            })
            total_requests += counter["requests"]
            total_tokens += counter["tokens"]

        return {
            "date": self._today_str,
            "total_requests": total_requests,
            "total_tokens": total_tokens,
            "providers": providers,
        }

    def get_telegram_summary(self):
        # type: () -> str
        """Format a compact Telegram-ready summary."""
        s = self.get_summary()
        lines = ["TOKEN BUDGET %s" % s["date"]]
        lines.append("Total: %d calls, %s tokens" % (
            s["total_requests"],
            "{:,}".format(s["total_tokens"]) if s["total_tokens"] else "0"
        ))
        near_cap = []
        for p in s["providers"]:
            if p["used_pct"] > 50 and p["provider"] != "ollama":
                near_cap.append("%s: %s%% (%sh left)" % (
                    p["provider"], p["used_pct"], p["hours_until_cap"]
                ))
        if near_cap:
            lines.append("Near limit: " + ", ".join(near_cap))
        else:
            lines.append("All providers healthy")
        return "\n".join(lines)
