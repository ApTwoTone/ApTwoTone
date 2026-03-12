"""
Runtime API — Unified endpoints for the Nexus runtime dashboard.

Aggregates data from agent_status, key_rotation, fleet_task_queue,
and agent_activity into single frontend-ready responses.

Usage in server.py:
    from core.runtime_api import register_runtime_routes
    register_runtime_routes(app)
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

log = logging.getLogger("runtime_api")
CONFIG_PATH = Path.home() / ".nexus" / "config.json"


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def register_runtime_routes(app: FastAPI):
    """Register all /api/runtime/* endpoints."""

    # ── GET /api/runtime/dashboard — single call for entire view ─────────

    @app.get("/api/runtime/dashboard")
    async def api_runtime_dashboard():
        agents_data = _get_agents_section()
        providers_data = _get_providers_section()
        tasks_data = _get_tasks_section()

        return JSONResponse({
            "agents": agents_data,
            "providers": providers_data,
            "tasks": tasks_data,
            "timestamp": time.time(),
        })

    # ── GET /api/runtime/agents — all agents with heartbeat age ──────────

    @app.get("/api/runtime/agents")
    async def api_runtime_agents():
        return JSONResponse(_get_agents_section())

    # ── GET /api/runtime/providers — all providers with health ───────────

    @app.get("/api/runtime/providers")
    async def api_runtime_providers():
        return JSONResponse(_get_providers_section())

    # ── GET /api/runtime/logs — recent activity entries ──────────────────

    @app.get("/api/runtime/logs")
    async def api_runtime_logs(limit: int = 50):
        try:
            from core.agent_activity import AgentActivityLogger
            logger = AgentActivityLogger()
            entries = list(logger._buffer)[-limit:]
            return JSONResponse({
                "logs": entries,
                "total": len(logger._buffer),
            })
        except Exception as e:
            log.warning("Failed to read activity logs: %s", e)
            return JSONResponse({"logs": [], "total": 0})

    # ── GET /api/runtime/health — system-wide health summary ─────────────

    @app.get("/api/runtime/health")
    async def api_runtime_health():
        agents = _get_agents_section()
        providers = _get_providers_section()

        # Health check uses only launchd daemons (not ad-hoc agents)
        daemons = [a for a in agents["agents"] if a.get("daemon_type") == "launchd"]
        daemons_running = sum(1 for d in daemons if d["status"] == "running")
        daemons_error = sum(1 for d in daemons if d["status"] in ("error", "retrying"))

        issues = []
        if daemons_error > 0:
            issues.append(f"{daemons_error} daemon(s) in error state")
        if daemons_running == 0:
            issues.append("No daemons running")
        if providers.get("down", 0) > 0:
            issues.append(f"{providers['down']} provider(s) down")

        if not issues:
            health = "healthy"
        elif daemons_running > len(daemons) // 3:
            health = "degraded"
        else:
            health = "critical"

        return JSONResponse({
            "status": health,
            "daemons_running": daemons_running,
            "daemons_total": len(daemons),
            "agents_running": agents["running"],
            "agents_total": agents["total"],
            "providers_healthy": providers.get("healthy", 0),
            "providers_total": providers.get("total", 0),
            "issues": issues,
            "timestamp": time.time(),
        })


# ── Data aggregation helpers ─────────────────────────────────────────────────

def _get_agents_section() -> dict:
    """Aggregate agent data from agent_status table."""
    from core.agent_runtime import get_all_agent_statuses

    agents = get_all_agent_statuses()
    running = sum(1 for a in agents if a["status"] == "running")
    idle = sum(1 for a in agents if a["status"] in ("idle", "completed"))
    error = sum(1 for a in agents if a["status"] in ("error", "retrying"))
    stopped = sum(1 for a in agents if a["status"] == "stopped")

    return {
        "total": len(agents),
        "running": running,
        "idle": idle,
        "error": error,
        "stopped": stopped,
        "agents": agents,
    }


def _get_providers_section() -> dict:
    """Aggregate provider data from KeyPool.fleet_status()."""
    try:
        from core.key_rotation import KeyPool
        config = _load_config()
        pool = KeyPool.from_config(config)
        fleet = pool.fleet_status()

        providers = fleet.get("providers", [])
        healthy = sum(1 for p in providers if p.get("keys_active", 0) > 0)
        rate_limited = sum(1 for p in providers if p.get("keys_active", 0) == 0 and p.get("keys_total", 0) > 0)
        down = sum(1 for p in providers if p.get("keys_total", 0) == 0)

        return {
            "total": len(providers),
            "healthy": healthy,
            "rate_limited": rate_limited,
            "down": down,
            "total_rpm": fleet.get("total_rpm", 0),
            "total_keys": fleet.get("total_keys", 0),
            "providers": providers,
        }
    except Exception as e:
        log.warning("Failed to get provider status: %s", e)
        return {"total": 0, "healthy": 0, "rate_limited": 0, "down": 0, "providers": []}


def _get_tasks_section() -> dict:
    """Aggregate task data from fleet_task_queue."""
    try:
        from core.fleet_task_queue import FleetTaskQueue
        q = FleetTaskQueue()
        stats = q.get_stats()

        return {
            "pending": stats.get("pending", 0),
            "in_progress": stats.get("in_progress", 0) + stats.get("claimed", 0),
            "completed_today": stats.get("completed", 0),
            "failed_today": stats.get("failed", 0),
            "dead_letters": stats.get("dead_letter", 0),
        }
    except Exception as e:
        log.warning("Failed to get task stats: %s", e)
        return {"pending": 0, "in_progress": 0, "completed_today": 0, "failed_today": 0}
