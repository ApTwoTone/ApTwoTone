"""
Real process monitor — queries launchd + psutil for actual running services.
Returns only processes that are genuinely active with real OS-level data.
"""
import logging
import subprocess
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("process_monitor")
DB_PATH = Path.home() / ".nexus" / "memory.db"

# All known com.zoar.* launchd services → display metadata
LAUNCHD_SERVICES: Dict[str, Dict[str, str]] = {
    "com.zoar.nexus-server":       {"name": "Nexus API Server",    "model": "FastAPI"},
    "com.zoar.telegram-bot":       {"name": "Telegram Bot",        "model": "python-telegram-bot"},
    "com.zoar.master-coordinator": {"name": "Master Coordinator",  "model": "Orchestrator"},
    "com.zoar.heartbeat":          {"name": "Heartbeat",           "model": "System"},
    "com.zoar.nextjs-dev":         {"name": "Next.js Dev Server",  "model": "Turbopack"},
    "com.zoar.frontend-watcher":   {"name": "Frontend Watcher",    "model": "System"},
    "com.zoar.morning-report":     {"name": "Morning Report",      "model": "Gemini Flash"},
    "com.zoar.overnight-master":   {"name": "Overnight Master",    "model": "Fleet"},
    "com.zoar.model-watcher":      {"name": "Model Watcher",       "model": "Gemini Flash"},
    "com.zoar.nexus-deploy":       {"name": "Auto Deploy",         "model": "System"},
    "com.zoar.vendor-discovery":   {"name": "Vendor Discovery",    "model": "Groq / Llama"},
    "com.zoar.process-manager":    {"name": "Process Manager",     "model": "System"},
    "com.zoar.server-watchdog":    {"name": "Server Watchdog",     "model": "bash"},
    "com.zoar.agent-studio":       {"name": "Agent Studio",        "model": "Fleet"},
    "com.zoar.agents.content":     {"name": "Content Agent",       "model": "Groq / Llama"},
    "com.zoar.agents.research":    {"name": "Research Agent",      "model": "Gemini Flash"},
    "com.zoar.bug-hunter":         {"name": "Bug Hunter",          "model": "Llama 4 Scout"},
}


def _get_launchd_pid(label: str) -> Optional[int]:
    """Query launchctl for a service's PID. Returns None if not running."""
    try:
        result = subprocess.run(
            ["launchctl", "list", label],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return None
        # launchctl list <label> output format:
        # {
        #   "PID" = 12345;
        #   ...
        # }
        # Or tabular: PID\tStatus\tLabel
        out = result.stdout.strip()
        # Try JSON-ish format first
        for line in out.split("\n"):
            line = line.strip().strip(";")
            if '"PID"' in line or "'PID'" in line:
                parts = line.split("=")
                if len(parts) == 2:
                    pid_str = parts[1].strip().strip(";").strip()
                    if pid_str.isdigit():
                        return int(pid_str)
            # Tabular format: PID\tStatus\tLabel
            tab_parts = line.split("\t")
            if len(tab_parts) >= 3 and tab_parts[2].strip() == label:
                pid_str = tab_parts[0].strip()
                if pid_str != "-" and pid_str.isdigit():
                    return int(pid_str)
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def get_running_processes() -> List[Dict[str, Any]]:
    """Query all com.zoar.* launchd services and return real status."""
    import psutil

    results = []
    for label, meta in LAUNCHD_SERVICES.items():
        entry: Dict[str, Any] = {
            "id": label,
            "name": meta["name"],
            "model": meta["model"],
            "status": "stopped",
            "pid": None,
            "cpu_pct": 0.0,
            "mem_mb": 0.0,
            "uptime_seconds": 0,
            "tasks_today": 0,
            "last_active": "",
        }

        pid = _get_launchd_pid(label)
        if pid is not None:
            entry["pid"] = pid
            entry["status"] = "running"
            try:
                proc = psutil.Process(pid)
                entry["cpu_pct"] = round(proc.cpu_percent(interval=0.1), 1)
                entry["mem_mb"] = round(proc.memory_info().rss / 1024 / 1024, 1)
                entry["uptime_seconds"] = int(
                    datetime.now().timestamp() - proc.create_time()
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                entry["status"] = "error"

        results.append(entry)

    _enrich_with_db(results)
    return results


def _enrich_with_db(processes: List[Dict[str, Any]]) -> None:
    """Pull tasks_today and last_active from agent_status table."""
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=3)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT agent_id, tasks_today, last_active FROM agent_status"
        ).fetchall()
        conn.close()

        status_map = {r["agent_id"]: dict(r) for r in rows}
        for proc in processes:
            # Match service label to agent_id (best-effort fuzzy)
            short_key = proc["id"].replace("com.zoar.", "").replace("-", "_")
            for aid, data in status_map.items():
                if short_key in aid or aid in short_key:
                    proc["tasks_today"] = data.get("tasks_today", 0) or 0
                    proc["last_active"] = data.get("last_active", "") or ""
                    break
    except Exception as e:
        log.warning("Could not enrich processes from DB: %s", e)
