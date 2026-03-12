"""
Register Daemons — Seeds agent_status with all known launchd daemons.

Called once at server startup. Probes launchctl to detect running PIDs.

Usage in server.py:
    from core.register_daemons import register_all_daemons
    register_all_daemons()
"""
from __future__ import annotations

import logging
import re
import sqlite3
import subprocess
from pathlib import Path

log = logging.getLogger("register_daemons")
DB_PATH = Path.home() / ".nexus" / "memory.db"

# ── Daemon Registry ──────────────────────────────────────────────────────────
# Every com.zoar.* launchd agent that should appear in the runtime dashboard.

DAEMON_REGISTRY = [
    # Core infrastructure
    {"agent_id": "nexus-server", "dept_id": "d0_core", "launchd_label": "com.zoar.nexus-server"},
    {"agent_id": "master-coordinator", "dept_id": "d0_core", "launchd_label": "com.zoar.master-coordinator"},
    {"agent_id": "process-manager", "dept_id": "d0_core", "launchd_label": "com.zoar.process-manager"},
    {"agent_id": "server-watchdog", "dept_id": "d0_core", "launchd_label": "com.zoar.server-watchdog"},
    {"agent_id": "heartbeat-monitor", "dept_id": "d0_core", "launchd_label": "com.zoar.heartbeat"},
    {"agent_id": "nexus-deploy", "dept_id": "d0_core", "launchd_label": "com.zoar.nexus-deploy"},
    {"agent_id": "nexus-master", "dept_id": "d0_core", "launchd_label": "com.zoar.nexus-master"},
    {"agent_id": "telegram-bot", "dept_id": "d0_core", "launchd_label": "com.zoar.telegram-bot"},
    {"agent_id": "task-worker", "dept_id": "d0_core", "launchd_label": "com.zoar.task-worker"},
    {"agent_id": "brain-agent-runner", "dept_id": "d0_core", "launchd_label": "com.zoar.brain-agent-runner"},
    {"agent_id": "brain-task-scheduler", "dept_id": "d0_core", "launchd_label": "com.zoar.brain-task-scheduler"},

    # Development
    {"agent_id": "frontend-watcher", "dept_id": "d0_core", "launchd_label": "com.zoar.frontend-watcher"},
    {"agent_id": "nextjs-dev", "dept_id": "d0_core", "launchd_label": "com.zoar.nextjs-dev"},

    # Vendor research (D1)
    {"agent_id": "vendor-research", "dept_id": "d1_vendor", "launchd_label": "com.zoar.agents.research"},
    {"agent_id": "email-outreach", "dept_id": "d1_vendor", "launchd_label": "com.zoar.email-outreach"},
    {"agent_id": "email-queue-gen", "dept_id": "d1_vendor", "launchd_label": "com.zoar.email-queue-generator"},
    {"agent_id": "conversation-ingester", "dept_id": "d1_vendor", "launchd_label": "com.zoar.conversation-ingester"},

    # Creative (D2)
    {"agent_id": "content-producer", "dept_id": "d2_creative", "launchd_label": "com.zoar.agents.content"},

    # Agent studio / QA
    {"agent_id": "agent-studio", "dept_id": "d0_core", "launchd_label": "com.zoar.agent-studio"},
    {"agent_id": "bug-hunter", "dept_id": "d0_core", "launchd_label": "com.zoar.bug-hunter"},

    # Scheduled reports
    {"agent_id": "morning-report", "dept_id": "d0_core", "launchd_label": "com.zoar.morning-report"},
    {"agent_id": "morning-briefing", "dept_id": "d0_core", "launchd_label": "com.zoar.morning_briefing"},
    {"agent_id": "overnight-master", "dept_id": "d0_core", "launchd_label": "com.zoar.overnight-master"},
    {"agent_id": "model-watcher", "dept_id": "d0_core", "launchd_label": "com.zoar.model-watcher"},
    {"agent_id": "prelaunch-check", "dept_id": "d0_core", "launchd_label": "com.zoar.prelaunch-check"},
]


def _probe_launchctl() -> dict:
    """Run launchctl list, return {label: pid} for com.zoar.* agents.

    PID is int > 0 if running, 0 if loaded but not running, -1 if not found.
    """
    result = {}
    try:
        out = subprocess.run(
            ["launchctl", "list"],
            capture_output=True, text=True, timeout=10,
        )
        for line in out.stdout.splitlines():
            if "com.zoar" not in line:
                continue
            # Format: PID\tStatus\tLabel  (PID is "-" if not running)
            parts = line.split("\t")
            if len(parts) >= 3:
                pid_str = parts[0].strip()
                label = parts[2].strip()
                pid = int(pid_str) if pid_str != "-" else 0
                result[label] = pid
    except Exception as e:
        log.warning("launchctl probe failed: %s", e)
    return result


def register_all_daemons():
    """Seed agent_status with all known daemons + detect running status."""
    launchctl_pids = _probe_launchctl()

    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")

    registered = 0
    running = 0

    for daemon in DAEMON_REGISTRY:
        agent_id = daemon["agent_id"]
        dept_id = daemon["dept_id"]
        label = daemon["launchd_label"]
        pid = launchctl_pids.get(label, 0)
        status = "running" if pid > 0 else "stopped"

        if pid > 0:
            running += 1

        conn.execute("""
            INSERT INTO agent_status (
                agent_id, dept_id, daemon_type, launchd_label, pid, status
            ) VALUES (?, ?, 'launchd', ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                daemon_type = 'launchd',
                launchd_label = excluded.launchd_label,
                pid = CASE WHEN excluded.pid > 0 THEN excluded.pid ELSE agent_status.pid END,
                status = CASE
                    WHEN excluded.pid > 0 THEN 'running'
                    WHEN agent_status.last_heartbeat > 0
                         AND (strftime('%%s','now') - agent_status.last_heartbeat) < 600
                    THEN agent_status.status
                    ELSE 'stopped'
                END
        """, (agent_id, dept_id, label, pid, status))
        registered += 1

    conn.commit()
    conn.close()

    log.info("[Daemons] Registered %d daemons (%d running via launchctl)", registered, running)
    return {"registered": registered, "running": running}
