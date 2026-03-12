#!/usr/bin/env python3
"""
Standalone worker process — claims tasks from fleet_task_queue,
executes via worker_pool, reports results. Runs independently of FastAPI.

Managed by ProcessManager. Each instance is a separate OS process with its own PID.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Dict, Optional

# Ensure nexus root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

PID_DIR = Path.home() / ".nexus" / "pids"
LOG_DIR = Path.home() / ".nexus" / "process_logs"
COORD_STATE_DIR = Path.home() / ".nexus" / "coordination"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)

_running = True


def _handle_term(signum, frame):
    global _running
    _running = False


signal.signal(signal.SIGTERM, _handle_term)
signal.signal(signal.SIGINT, _handle_term)


async def worker_loop(worker_name: str, tier: int):
    """Continuously claim and execute tasks from the queue."""
    from core.fleet_task_queue import FleetTaskQueue
    from core.worker_pool import call_for_task

    log = logging.getLogger(worker_name)
    q = FleetTaskQueue()
    tasks_completed = 0
    latest_instruction: Optional[Dict[str, str]] = None
    last_instruction_id = _load_last_instruction_id(worker_name)
    log.info("Worker %s starting (tier %d, PID %d)", worker_name, tier, os.getpid())

    while _running:
        # Write heartbeat
        _write_heartbeat(worker_name)

        # Refresh latest operator instruction from Kai (if any).
        try:
            from core.nexus_coordination import get_latest_instruction, mark_instruction_consumed
            instruction = get_latest_instruction(after_id=last_instruction_id)
            if instruction:
                latest_instruction = instruction
                last_instruction_id = int(instruction.get("id", last_instruction_id))
                _save_last_instruction_id(worker_name, last_instruction_id)
                try:
                    mark_instruction_consumed(last_instruction_id, worker_name)
                except Exception:
                    pass
                snippet = str(instruction.get("instruction", "")).replace("\n", " ").strip()
                if snippet:
                    log.info("Applied Kai instruction #%d: %s", last_instruction_id, snippet[:180])
        except Exception:
            pass

        # Try to claim a task
        try:
            task = q.claim(worker_name, tier)
        except Exception as e:
            log.error("Claim error: %s", e)
            await asyncio.sleep(5)
            continue

        if not task:
            await asyncio.sleep(2)  # No work available
            continue

        task_id = task["task_id"]
        task_type = task.get("task_type", "unknown")
        log.info("Claimed task %s (type: %s)", task_id, task_type)

        input_data = {}
        try:
            input_data = json.loads(task.get("input_data", "{}"))
        except (json.JSONDecodeError, TypeError):
            input_data = {"raw": task.get("input_data", "")}

        start = time.time()

        try:
            # Route D3 tasks directly to DivisionThree
            if task_type.startswith("d3_"):
                if latest_instruction and latest_instruction.get("instruction"):
                    input_data.setdefault("kai_instruction", latest_instruction["instruction"])
                    input_data.setdefault("kai_instruction_id", latest_instruction.get("id"))
                result = await _handle_d3_task(task_type, input_data, log)
                elapsed_ms = int((time.time() - start) * 1000)
                if result.get("ok"):
                    q.complete(task_id, json.dumps(result),
                               result.get("provider", "d3"),
                               result.get("model", ""),
                               result.get("tokens_used", 0))
                    tasks_completed += 1
                    log.info("D3 task %s completed in %dms", task_id, elapsed_ms)
                else:
                    q.fail(task_id, result.get("error", "D3 task failed"))
                    log.warning("D3 task %s failed: %s", task_id,
                                result.get("error", "")[:200])
                continue

            # Build prompt from input data
            prompt = input_data.get("prompt", "")
            if not prompt:
                prompt = input_data.get("description", str(input_data))

            system_prompt = input_data.get(
                "system", "You are a Nexus AI worker. Complete the task efficiently."
            )
            if latest_instruction and latest_instruction.get("instruction"):
                inst_text = str(latest_instruction["instruction"]).strip()
                if inst_text:
                    system_prompt = (
                        f"{system_prompt}\n\n"
                        f"Current operator instruction from Kai (#{latest_instruction.get('id')}):\n"
                        f"{inst_text}\n"
                        "Prioritize this instruction unless the task payload explicitly overrides it."
                    )

            result = await call_for_task(
                task_type,
                [{"role": "user", "content": prompt}],
                system=system_prompt,
                max_tokens=input_data.get("max_tokens", 2000),
            )
            elapsed_ms = int((time.time() - start) * 1000)

            if result.get("ok"):
                q.complete(
                    task_id,
                    result.get("content", ""),
                    result.get("provider", ""),
                    result.get("model", ""),
                    result.get("tokens_used", 0),
                )
                tasks_completed += 1
                log.info("Completed task %s in %dms (provider: %s)",
                         task_id, elapsed_ms, result.get("provider", "?"))

                # Post-process vendor research results
                post = input_data.get("post_process", "")
                if post == "vendor_research":
                    _post_process_vendor_research(
                        result.get("content", ""), input_data, log
                    )

                # Log to shared brain
                try:
                    from core.shared_brain import get_brain
                    get_brain().log_task(
                        task_type, prompt[:200], str(result.get("content", ""))[:500],
                        True, result.get("provider", ""), result.get("model", ""),
                        result.get("tokens_used", 0), elapsed_ms, worker_name,
                    )
                except Exception:
                    pass

                # Broadcast to activity stream (SSE → UI)
                try:
                    from core.agent_activity import log_activity
                    log_activity(
                        worker_name, "fleet", "task_completed",
                        "%s in %dms via %s" % (task_type, elapsed_ms, result.get("provider", "?")),
                        target=task_id, status="success",
                    )
                except Exception:
                    pass
            else:
                error_msg = result.get("content", "Provider returned error")
                q.fail(task_id, error_msg)
                log.warning("Task %s failed: %s", task_id, error_msg[:200])
                try:
                    from core.agent_activity import log_activity
                    log_activity(worker_name, "fleet", "task_failed",
                                 "%s: %s" % (task_type, error_msg[:100]),
                                 target=task_id, status="error")
                except Exception:
                    pass
        except Exception as e:
            q.fail(task_id, str(e))
            log.error("Task %s exception: %s", task_id, e)

    log.info("Worker %s shutting down after %d tasks", worker_name, tasks_completed)


def _write_heartbeat(worker_name: str):
    """Write heartbeat timestamp to PID dir for ProcessManager."""
    try:
        hb_file = PID_DIR / ("%s.heartbeat" % worker_name)
        hb_file.write_text(json.dumps({
            "pid": os.getpid(),
            "ts": time.time(),
            "worker": worker_name,
        }))
    except Exception:
        pass


def _instruction_state_file(worker_name: str) -> Path:
    return COORD_STATE_DIR / ("%s.last_instruction" % worker_name)


def _load_last_instruction_id(worker_name: str) -> int:
    """Load last applied Kai instruction id for this worker."""
    try:
        state_file = _instruction_state_file(worker_name)
        if not state_file.exists():
            return 0
        return int((state_file.read_text() or "0").strip())
    except Exception:
        return 0


def _save_last_instruction_id(worker_name: str, instruction_id: int) -> None:
    """Persist last applied instruction id to survive worker restarts."""
    try:
        COORD_STATE_DIR.mkdir(parents=True, exist_ok=True)
        _instruction_state_file(worker_name).write_text(str(int(instruction_id)))
    except Exception:
        pass


async def _handle_d3_task(task_type: str, input_data: dict, log) -> dict:
    """Route D3 tasks to DivisionThree engine."""
    try:
        from core.division_three import get_division_three
        d3 = get_division_three()
        return await d3.handle_task(task_type, input_data)
    except Exception as e:
        log.error("D3 handler error: %s", e)
        return {"ok": False, "error": str(e)}


def _post_process_vendor_research(content: str, input_data: dict, log):
    """Parse AI vendor research output and save to vendor DB."""
    try:
        from core.vendor_research_daemon import VendorResearchDaemon
        from core.vendor_db import bulk_save_vendors
    except ImportError as e:
        log.warning("Vendor post-process import failed: %s", e)
        return

    try:
        daemon = VendorResearchDaemon.__new__(VendorResearchDaemon)
        category = input_data.get("category", "unknown")
        vendors = daemon._parse_vendor_json(content, category)
        if vendors:
            counts = bulk_save_vendors(vendors)
            log.info("Vendors: %d created, %d updated (%s in %s)",
                     counts.get("created", 0), counts.get("updated", 0),
                     category, input_data.get("location", "?"))
    except Exception as e:
        log.warning("Vendor post-process failed: %s", e)


def main():
    parser = argparse.ArgumentParser(description="Nexus Worker Runner")
    parser.add_argument("--name", required=True, help="Worker name")
    parser.add_argument("--tier", type=int, default=3, help="Worker tier (1-7)")
    args = parser.parse_args()

    PID_DIR.mkdir(parents=True, exist_ok=True)
    COORD_STATE_DIR.mkdir(parents=True, exist_ok=True)

    # Write initial PID file
    (PID_DIR / ("%s.pid" % args.name)).write_text(str(os.getpid()))

    asyncio.run(worker_loop(args.name, args.tier))


if __name__ == "__main__":
    main()
