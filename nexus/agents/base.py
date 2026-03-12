"""
Base agent class — wraps Anthropic API with rate limiting, logging, and cost tracking.
All agents inherit from this. Uses Haiku for cheap tasks, Sonnet for complex ones.

Every agent has access to the SharedBrain and:
1. Reads cross-agent context before major decisions
2. Writes activity to both logs and the brain
3. Can post/respond to problems
4. Checks knowledge base before trying things
"""
import asyncio
import json
import time
import sqlite3
import random
from datetime import datetime
from pathlib import Path
from typing import Optional

import anthropic

from agents.config import (
    load_nexus_config, DB_PATH, AGENT_STATE_DIR,
    AGENT_DEFAULTS, BUSINESS,
)
from agents.shared_brain import SharedBrain


class BaseAgent:
    """Base class for all Zoar agents."""

    def __init__(self, agent_id: str, agent_type: str):
        self.agent_id = agent_id
        self.agent_type = agent_type
        self.config = load_nexus_config()
        self.brain = SharedBrain(agent_id)
        self._client = None
        self._running = False
        self._action_count = 0
        self._hour_start = time.time()
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._state_file = AGENT_STATE_DIR / f"{agent_id}.json"
        self._log_file = AGENT_STATE_DIR / f"{agent_id}.log"
        self._state = self._load_state()

    @property
    def client(self) -> anthropic.Anthropic:
        if not self._client:
            api_key = self.config.get("anthropic_key", "")
            if not api_key:
                raise ValueError("No Anthropic API key in ~/.nexus/config.json")
            self._client = anthropic.Anthropic(api_key=api_key)
        return self._client

    # ── AI Generation ──────────────────────────────────────────────────────

    async def think(self, prompt: str, system: str = "", model: str = None,
                    max_tokens: int = 500) -> str:
        """
        Call Claude for a response. Uses Haiku by default (cheap + fast).
        For complex tasks, pass model=AGENT_DEFAULTS["smart_model"].
        """
        model = model or AGENT_DEFAULTS["fast_model"]
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, lambda: self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system or self._default_system(),
                messages=[{"role": "user", "content": prompt}],
            ))
            text = response.content[0].text if response.content else ""
            self._total_input_tokens += response.usage.input_tokens
            self._total_output_tokens += response.usage.output_tokens
            return text.strip()
        except Exception as e:
            self.log(f"AI error: {e}")
            # Record errors as problems in the brain
            self.brain.post_problem(
                f"AI call failed in {self.agent_id}: {str(e)[:200]}",
                category="technical",
            )
            return ""

    async def think_with_history(self, messages: list, system: str = "",
                                 model: str = None, max_tokens: int = 500) -> str:
        """Call Claude with conversation history."""
        model = model or AGENT_DEFAULTS["fast_model"]
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, lambda: self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system or self._default_system(),
                messages=messages,
            ))
            text = response.content[0].text if response.content else ""
            self._total_input_tokens += response.usage.input_tokens
            self._total_output_tokens += response.usage.output_tokens
            return text.strip()
        except Exception as e:
            self.log(f"AI error: {e}")
            return ""

    async def think_with_brain(self, prompt: str, system: str = "",
                                model: str = None, max_tokens: int = 500) -> str:
        """Think with full brain context injected. Use for major decisions."""
        brain_context = self.brain.get_context_for_agent(self.agent_id)
        enhanced_prompt = f"""{prompt}

{brain_context}

Use the shared brain context above to inform your response. Reference other agents' insights if relevant."""
        return await self.think(enhanced_prompt, system=system, model=model,
                                max_tokens=max_tokens)

    def _default_system(self) -> str:
        return f"""You are a helpful assistant for {BUSINESS['name']}.
Business: {BUSINESS['product']} — {', '.join(BUSINESS['features'][:4])}.
Service areas: {', '.join(BUSINESS['service_areas'])}.
Contact: {BUSINESS['contact']['phone']} | {BUSINESS['contact']['website']}
Tone: {AGENT_DEFAULTS['tone']}
Persona: {AGENT_DEFAULTS['persona']}
RULES:
- NEVER be salesy or pushy
- NEVER mention you are AI or a bot
- Be genuinely helpful first, promote naturally only when directly relevant
- Keep responses concise and natural
- Use casual, friendly language"""

    # ── Brain Helpers ──────────────────────────────────────────────────────

    def check_knowledge(self, problem_keywords: str) -> list:
        """Check if the brain has solutions for a similar problem."""
        return self.brain.find_solution(problem_keywords)

    def report_problem(self, problem: str, category: str = "general",
                       data: dict = None) -> str:
        """Report a problem for other agents to help solve."""
        thread_id = self.brain.post_problem(problem, category, data)
        self.log(f"Posted problem to brain: {problem[:100]}")
        return thread_id

    def report_solution(self, thread_id: str, solution: str):
        """Report a solution to an open problem."""
        self.brain.post_solution(thread_id, solution)
        self.log(f"Posted solution to thread {thread_id}")

    def share_learning(self, problem: str, solution: str,
                       category: str = "general"):
        """Share a problem/solution pair directly to the knowledge base."""
        self.brain.save_knowledge(problem, solution, category)
        self.log(f"Shared learning: {problem[:60]} -> {solution[:60]}")

    # ── Rate Limiting ──────────────────────────────────────────────────────

    async def rate_limit(self, action_type: str = "comment"):
        """Enforce rate limits. Blocks until safe to proceed."""
        now = time.time()
        # Reset hourly counter
        if now - self._hour_start > 3600:
            self._action_count = 0
            self._hour_start = now

        limits = {
            "comment": AGENT_DEFAULTS["max_comments_per_hour"],
            "dm": AGENT_DEFAULTS["max_dms_per_hour"],
            "post": AGENT_DEFAULTS["max_posts_per_day"],
        }
        limit = limits.get(action_type, 5)

        if self._action_count >= limit:
            wait = 3600 - (now - self._hour_start) + random.randint(30, 120)
            self.log(f"Rate limit hit ({action_type}). Waiting {wait:.0f}s")
            await asyncio.sleep(wait)
            self._action_count = 0
            self._hour_start = time.time()

        # Random delay between actions (human-like)
        delay = random.uniform(
            AGENT_DEFAULTS["min_action_delay"],
            AGENT_DEFAULTS["max_action_delay"]
        )
        self.log(f"Waiting {delay:.0f}s before next action")
        await asyncio.sleep(delay)
        self._action_count += 1

    # ── State Management ───────────────────────────────────────────────────

    def _load_state(self) -> dict:
        if self._state_file.exists():
            try:
                return json.loads(self._state_file.read_text())
            except Exception:
                pass
        return {
            "created_at": datetime.now().isoformat(),
            "actions_taken": 0,
            "leads_found": 0,
            "comments_posted": 0,
            "dms_sent": 0,
            "posts_made": 0,
            "groups_joined": [],
            "last_active": None,
        }

    def save_state(self):
        self._state["last_active"] = datetime.now().isoformat()
        self._state_file.write_text(json.dumps(self._state, indent=2))

    # ── Logging ────────────────────────────────────────────────────────────

    def log(self, message: str, level: str = "INFO"):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{self.agent_id}] [{level}] {message}"
        print(line)
        try:
            with open(self._log_file, "a") as f:
                f.write(line + "\n")
        except Exception:
            pass

    # ── CRM Integration ────────────────────────────────────────────────────

    def log_to_crm(self, event_type: str, details: str, lead_id: int = None):
        """Log agent activity to the CRM database + brain activity log."""
        try:
            conn = sqlite3.connect(str(DB_PATH))
            if lead_id:
                conn.execute(
                    "INSERT INTO lead_events (lead_id, event_type, details) VALUES (?, ?, ?)",
                    (lead_id, f"agent_{event_type}", f"[{self.agent_id}] {details}")
                )
            conn.execute("""
                INSERT INTO agent_activity
                (agent_id, agent_type, event_type, details, ts)
                VALUES (?, ?, ?, ?, datetime('now'))
            """, (self.agent_id, self.agent_type, event_type, details))
            conn.commit()
            conn.close()
        except Exception as e:
            self.log(f"CRM log error: {e}", "WARN")

    # ── Cost Tracking ──────────────────────────────────────────────────────

    def get_cost_estimate(self) -> dict:
        """Estimate API costs based on token usage."""
        # Haiku pricing: $0.25 input / $1.25 output per 1M tokens
        # Sonnet pricing: $3 input / $15 output per 1M tokens
        haiku_cost = (
            (self._total_input_tokens * 0.25 / 1_000_000) +
            (self._total_output_tokens * 1.25 / 1_000_000)
        )
        return {
            "input_tokens": self._total_input_tokens,
            "output_tokens": self._total_output_tokens,
            "estimated_cost_usd": round(haiku_cost, 4),
        }

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self):
        """Override in subclass."""
        self._running = True
        self.log(f"Agent started: {self.agent_type}")
        self.brain.log_activity(
            "agent_started", f"{self.agent_id} ({self.agent_type}) started",
            self.agent_type,
        )

    def stop(self):
        self._running = False
        self.save_state()
        cost = self.get_cost_estimate()
        self.log(f"Agent stopped. Cost: ${cost['estimated_cost_usd']:.4f} "
                 f"({cost['input_tokens']} in / {cost['output_tokens']} out)")
        self.brain.log_activity(
            "agent_stopped",
            f"{self.agent_id} stopped. Cost: ${cost['estimated_cost_usd']:.4f}",
            self.agent_type,
        )
