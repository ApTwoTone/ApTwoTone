"""
Nexus Task Chains — Multi-agent workflow orchestration.

Defines chain templates (vendor→outreach, activity→score→draft→approve)
and orchestrates step-by-step execution through the fleet task queue.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("task_chains")

DB_PATH = Path.home() / ".nexus" / "memory.db"

# ── Chain Definitions ─────────────────────────────────────────────────────────

CHAIN_DEFINITIONS = {
    "vendor_to_outreach": {
        "description": "Research vendors → Draft content → Queue batch → Telegram approval",
        "steps": [
            {"name": "research", "task_type": "vendor_research", "tier": 3, "priority": 5},
            {"name": "draft", "task_type": "content_generation", "tier": 2, "priority": 6},
            {"name": "queue", "task_type": "batch_queue", "tier": 2, "priority": 7},
            {"name": "approve", "task_type": "telegram_approval", "tier": 1, "priority": 8},
        ],
    },
    "vendor_activity_to_outreach": {
        "description": "Monitor activity → Score urgency → Draft message → Approval",
        "steps": [
            {"name": "monitor", "task_type": "vendor_research", "tier": 3, "priority": 4},
            {"name": "score", "task_type": "analysis", "tier": 2, "priority": 5},
            {"name": "draft", "task_type": "outreach_drafting", "tier": 2, "priority": 6},
            {"name": "approve", "task_type": "telegram_approval", "tier": 1, "priority": 8},
        ],
    },
    "lead_to_quote": {
        "description": "New lead → Calculate quote → Draft message → Send approval",
        "steps": [
            {"name": "calculate", "task_type": "data_processing", "tier": 2, "priority": 9},
            {"name": "draft", "task_type": "content_generation", "tier": 2, "priority": 9},
            {"name": "approve", "task_type": "telegram_approval", "tier": 1, "priority": 10},
        ],
    },
    "bug_to_fix": {
        "description": "Bug report → Analyze → CodingAgent fix → Verify",
        "steps": [
            {"name": "analyze", "task_type": "analysis", "tier": 2, "priority": 9},
            {"name": "fix", "task_type": "coding_task", "tier": 1, "priority": 9},
            {"name": "verify", "task_type": "health_check", "tier": 2, "priority": 8},
        ],
    },
    "build_pipeline": {
        "description": "Architect → Build → Review → Patch → Claude Gate",
        "steps": [
            {"name": "architect", "task_type": "build_architect", "tier": 5, "priority": 8},
            {"name": "build", "task_type": "build_implement", "tier": 5, "priority": 7},
            {"name": "review", "task_type": "build_review", "tier": 5, "priority": 7},
            {"name": "patch", "task_type": "build_patch", "tier": 5, "priority": 7},
        ],
    },
    "d3_experiment_cycle": {
        "description": "Trend scan → Plan → Generate content → Analyze → Learn",
        "steps": [
            {"name": "scan", "task_type": "d3_trend_scan", "tier": 6, "priority": 4},
            {"name": "plan", "task_type": "d3_experiment_plan", "tier": 6, "priority": 5},
            {"name": "create", "task_type": "d3_content_gen", "tier": 6, "priority": 5},
            {"name": "analyze", "task_type": "d3_analysis", "tier": 6, "priority": 4},
        ],
    },
    "self_heal": {
        "description": "Diagnose issue → Repair → Verify fix → Learn pattern",
        "steps": [
            {"name": "diagnose", "task_type": "d4_diagnose", "tier": 3, "priority": 9},
            {"name": "repair", "task_type": "d4_repair", "tier": 5, "priority": 9},
            {"name": "verify", "task_type": "d4_verify", "tier": 7, "priority": 8},
            {"name": "learn", "task_type": "d4_learn", "tier": 7, "priority": 7},
        ],
    },
}


class TaskChainOrchestrator:
    """Manages multi-step task chains through the fleet queue."""

    def __init__(self):
        pass

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def start_chain(self, chain_type: str, context: Dict,
                    started_by: str = "system") -> Optional[str]:
        """Start a new task chain. Returns chain_id or None."""
        definition = CHAIN_DEFINITIONS.get(chain_type)
        if not definition:
            log.error("Unknown chain type: %s", chain_type)
            return None

        chain_id = "chain-%s-%s" % (chain_type, uuid.uuid4().hex[:8])
        steps = definition["steps"]

        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO task_chains
                   (chain_id, chain_type, status, current_step, total_steps, context)
                   VALUES (?, ?, 'active', 0, ?, ?)""",
                (chain_id, chain_type, len(steps), json.dumps(context)),
            )
            conn.commit()
        finally:
            conn.close()

        log.info("Started chain %s (%s, %d steps)", chain_id, chain_type, len(steps))

        # Enqueue first step
        self._enqueue_step(chain_id, chain_type, 0, context)
        return chain_id

    def advance_chain(self, chain_id: str, step_result: str = "") -> bool:
        """Advance chain to next step after current step completes."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM task_chains WHERE chain_id = ?", (chain_id,)
            ).fetchone()
            if not row:
                log.error("Chain %s not found", chain_id)
                return False

            current = row["current_step"]
            total = row["total_steps"]
            chain_type = row["chain_type"]

            # Update context with step result
            context = json.loads(row["context"] or "{}")
            if step_result:
                context["step_%d_result" % current] = step_result

            next_step = current + 1
            if next_step >= total:
                # Chain complete
                conn.execute(
                    """UPDATE task_chains SET status = 'completed',
                       current_step = ?, context = ? WHERE chain_id = ?""",
                    (next_step, json.dumps(context), chain_id),
                )
                conn.commit()
                log.info("Chain %s completed (%d steps)", chain_id, total)
                return True

            # Advance
            conn.execute(
                """UPDATE task_chains SET current_step = ?, context = ?
                   WHERE chain_id = ?""",
                (next_step, json.dumps(context), chain_id),
            )
            conn.commit()

            # Enqueue next step
            self._enqueue_step(chain_id, chain_type, next_step, context)
            log.info("Chain %s advanced to step %d/%d", chain_id, next_step + 1, total)
            return True
        finally:
            conn.close()

    def get_chain_status(self, chain_id: str) -> Optional[Dict]:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM task_chains WHERE chain_id = ?", (chain_id,)
            ).fetchone()
            if row:
                d = dict(row)
                d["context"] = json.loads(d.get("context", "{}") or "{}")
                definition = CHAIN_DEFINITIONS.get(d["chain_type"], {})
                d["steps"] = definition.get("steps", [])
                return d
            return None
        finally:
            conn.close()

    def get_active_chains(self) -> List[Dict]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM task_chains WHERE status = 'active' ORDER BY started_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def cancel_chain(self, chain_id: str) -> bool:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE task_chains SET status = 'cancelled' WHERE chain_id = ?",
                (chain_id,),
            )
            conn.commit()
            log.info("Chain %s cancelled", chain_id)
            return True
        finally:
            conn.close()

    def _enqueue_step(self, chain_id: str, chain_type: str,
                      step_index: int, context: Dict):
        """Enqueue a chain step into the fleet task queue."""
        definition = CHAIN_DEFINITIONS.get(chain_type)
        if not definition or step_index >= len(definition["steps"]):
            return

        step = definition["steps"][step_index]
        try:
            from core.fleet_task_queue import FleetTaskQueue
            q = FleetTaskQueue()
            input_data = {
                "chain_id": chain_id,
                "chain_type": chain_type,
                "step_index": step_index,
                "step_name": step["name"],
                "context": context,
            }
            q.enqueue(
                task_type=step["task_type"],
                input_data=input_data,
                tier=step.get("tier", 2),
                priority=step.get("priority", 5),
            )
            log.info("Enqueued step %d (%s) for chain %s",
                     step_index, step["name"], chain_id)
        except Exception as e:
            log.error("Failed to enqueue step %d for chain %s: %s",
                      step_index, chain_id, e)

    @staticmethod
    def list_chain_types() -> Dict:
        """Return all available chain definitions."""
        return {
            k: {"description": v["description"], "steps": len(v["steps"])}
            for k, v in CHAIN_DEFINITIONS.items()
        }


# Singleton
_orchestrator = None  # type: Optional[TaskChainOrchestrator]


def get_chain_orchestrator() -> TaskChainOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = TaskChainOrchestrator()
    return _orchestrator
