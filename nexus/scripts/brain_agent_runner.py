"""
Nexus Brain Agent Runner — Executor daemon for the 5 brain control plane agents.

The NexusBrainControlPlane defines agents, a task queue, and memory — but nothing
executes tasks. This script is the missing executor: it starts one thread per agent,
each thread polls the queue every 10 seconds, claims tasks, routes them to the correct
Ollama specialist model, and records results back to the control plane.

Graceful degradation: if Ollama is offline or a model hasn't been transferred yet,
the task falls through to the free model chain (ZAI→Cerebras→Groq→Ollama). Zero
blocking, zero crashes. Works immediately before training is complete.

Usage:
    python scripts/brain_agent_runner.py           # runs forever (daemon mode)
    python scripts/brain_agent_runner.py --once    # process one round and exit (debug)

Launchd: com.zoar.brain-agent-runner.plist (KeepAlive=true)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

# Make sure /Users/kai/nexus is on the path when launched from launchd
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

log = logging.getLogger("brain_agent_runner")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

NEXUS_BASE = "http://localhost:7860"
POLL_INTERVAL = 10  # seconds between task-claim attempts per agent

# ── Task type → Ollama specialist model ──────────────────────────────────────
# When a brain task arrives, we look up task["type"] here.
# If model is offline/missing, _call_specialist() falls through to free models.
TASK_MODEL_MAP = {
    # lead_intelligence_agent tasks
    "lead_qualification":       "nexus-lead-ranker",
    "lead_scoring":             "nexus-lead-ranker",
    "venue_fit_scoring":        "nexus-venue-ranker",
    "outreach_angle_selection": "nexus-outreach-angle",
    # ads_intelligence_agent tasks
    "ad_diagnostics":           "nexus-fb-buyer",
    "ad_creative":              "nexus-ad-creative",
    "creative_scoring":         "nexus-ad-creative",
    "hook_analysis":            "nexus-ad-creative",
    # quote / conversion tasks (brain_supervisor)
    "quote_generation":         "nexus-quote",
    "objection_handling":       "nexus-objections",
    "venue_outreach":           "nexus-venue-closer",
    "vendor_outreach":          "nexus-vendor-referral",
    # experiment_critic_agent tasks
    "experiment_design":        "nexus-seasonal",
    "result_scoring":           "nexus-seasonal",
    "lesson_extraction":        "nexus-seasonal",
    # coding_repair_agent tasks
    "code_review":              "nexus-coder",
    "bug_fix":                  "nexus-coder",
    "diagnostics":              "nexus-coder",
    "patch_proposal":           "nexus-coder",
    "test_generation":          "nexus-coder",
    "safe_refactor":            "nexus-coder",
}


def _ollama_online(model: str) -> bool:
    """Return True if Ollama is running and the model is loaded."""
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read())
            names = {m.get("name", "").split(":")[0] for m in data.get("models", [])}
            return model in names
    except Exception:
        return False


def _call_specialist(model: str, prompt: str, context: str = "") -> Optional[str]:
    """
    POST to /api/brain/query (which routes to Ollama).
    Returns response text or None on failure.
    """
    payload = json.dumps({"model": model, "prompt": prompt, "context": context}).encode()
    try:
        req = urllib.request.Request(
            f"{NEXUS_BASE}/api/brain/query",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=65) as resp:
            data = json.loads(resp.read())
            if data.get("status") == "ok":
                return data.get("response", "")
    except Exception as e:
        log.debug("specialist call failed (model=%s): %s", model, e)
    return None


def _call_free_model(prompt: str) -> str:
    """
    Fallback to the free model chain when Ollama offline or model not yet transferred.
    Uses chat_handler.call_with_fallback() (ZAI→Cerebras→Groq→Ollama→hardcoded).
    """
    try:
        from core.chat_handler import call_with_fallback
        messages = [{"role": "user", "content": prompt}]
        result = asyncio.run(call_with_fallback(messages))
        if isinstance(result, dict):
            return result.get("content", "") or "No response from free model chain."
        return str(result or "No response from free model chain.")
    except Exception as e:
        log.debug("free model fallback failed: %s", e)
        return "Fallback unavailable."


def _execute_task(task: dict, agent_id: str) -> str:
    """
    Route a task to the appropriate specialist (or free model fallback).
    Returns the response string.
    """
    task_type = task.get("task_type") or task.get("type", "default")
    objective = task.get("objective", "")
    context = task.get("payload") or task.get("context_json") or ""
    if isinstance(context, dict):
        context = json.dumps(context)

    model = TASK_MODEL_MAP.get(task_type)
    if model and _ollama_online(model):
        log.info("[%s] Routing task #%s (%s) → %s", agent_id, task.get("id"), task_type, model)
        result = _call_specialist(model, objective, context or "")
        if result:
            return result
        log.debug("[%s] Specialist returned empty, falling through to free models", agent_id)
    else:
        if model:
            log.debug("[%s] Model %s not yet online, using free models for task #%s", agent_id, model, task.get("id"))
        else:
            log.debug("[%s] No specialist mapped for type=%s, using free models", agent_id, task_type)

    return _call_free_model(objective)


def _agent_loop(agent_id: str, lane: str, cp, stop_event: threading.Event, once: bool) -> None:
    """
    Main loop for one brain agent.
    Runs until stop_event is set (or once=True → single iteration).
    """
    log.info("[%s] Agent thread started (lane=%s)", agent_id, lane)

    while not stop_event.is_set():
        try:
            cp.heartbeat(agent_id=agent_id, status="idle", detail="polling task queue")
        except Exception as e:
            log.debug("[%s] heartbeat error: %s", agent_id, e)

        try:
            claim = cp.claim_next_task(agent_id=agent_id, lane=lane)
            task = claim.get("task")
        except Exception as e:
            log.warning("[%s] claim_next_task error: %s", agent_id, e)
            task = None

        if task:
            task_id = task.get("id")
            task_type = task.get("task_type") or task.get("type", "unknown")
            log.info("[%s] Claimed task #%s type=%s", agent_id, task_id, task_type)

            try:
                cp.heartbeat(agent_id=agent_id, status="running", current_task_id=task_id,
                             detail=f"executing {task_type}")
                result_text = _execute_task(task, agent_id)

                cp.complete_task(
                    task_id=task_id,
                    agent_id=agent_id,
                    result={"response": result_text[:2000], "agent_id": agent_id},
                )

                # Store outcome in episodic memory
                cp.add_memory(
                    memory_type="episodic",
                    title=f"Completed {task_type} #{task_id}",
                    body=result_text[:500],
                    scope="zoar_core",
                    source=agent_id,
                    confidence=0.7,
                    importance=0.5,
                    tags=[task_type, agent_id],
                )
                log.info("[%s] Task #%s completed", agent_id, task_id)

            except Exception as e:
                log.error("[%s] Task #%s failed: %s", agent_id, task_id, e)
                try:
                    cp.fail_task(task_id=task_id, error=str(e)[:500], agent_id=agent_id)
                except Exception:
                    pass

        if once:
            break

        stop_event.wait(POLL_INTERVAL)

    log.info("[%s] Agent thread exiting", agent_id)


def main(once: bool = False) -> None:
    from core.nexus_brain_control_plane import NexusBrainControlPlane

    cp = NexusBrainControlPlane()
    cp.bootstrap_default_agents()

    # Agent ID → lane mapping (matches DEFAULT_BRAIN_AGENTS)
    agents = [
        ("brain_supervisor",         "core_ops"),
        ("ads_intelligence_agent",   "ads_intel"),
        ("lead_intelligence_agent",  "lead_ops"),
        ("experiment_critic_agent",  "experiments"),
        ("coding_repair_agent",      "systems"),
    ]

    stop_event = threading.Event()
    threads = []

    for agent_id, lane in agents:
        t = threading.Thread(
            target=_agent_loop,
            args=(agent_id, lane, cp, stop_event, once),
            name=f"agent-{agent_id}",
            daemon=True,
        )
        t.start()
        threads.append(t)

    log.info("Brain Agent Runner started — %d agents polling every %ds", len(agents), POLL_INTERVAL)
    log.info("Ollama will be checked per task. Free model fallback active until models transfer.")

    if once:
        for t in threads:
            t.join(timeout=120)
        log.info("--once mode complete.")
        return

    # Main thread: keep alive and log health every 60s
    try:
        while True:
            time.sleep(60)
            alive = sum(1 for t in threads if t.is_alive())
            log.debug("Health check: %d/%d agent threads alive", alive, len(threads))
    except KeyboardInterrupt:
        log.info("Shutdown signal received — stopping agents.")
        stop_event.set()
        for t in threads:
            t.join(timeout=15)
        log.info("Brain Agent Runner stopped.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nexus Brain Agent Runner")
    parser.add_argument("--once", action="store_true",
                        help="Process one round of tasks per agent and exit (debug mode)")
    args = parser.parse_args()
    main(once=args.once)
