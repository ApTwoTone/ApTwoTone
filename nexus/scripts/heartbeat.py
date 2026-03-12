#!/usr/bin/env python3
"""
Mac Mini Heartbeat — Writes periodic heartbeat to signal the system is online.

Runs every 5 minutes via launchd. The cloud failover (GitHub Actions) checks
this heartbeat to determine if the Mac Mini is operational.

The heartbeat is written to:
1. ~/.nexus/heartbeat.json (local)
2. A Cloudflare Worker endpoint (if configured) for remote checking
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

NEXUS_DIR = Path.home() / ".nexus"
HEARTBEAT_FILE = NEXUS_DIR / "heartbeat.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("heartbeat")


def write_heartbeat():
    """Write heartbeat with system status."""
    import os

    # Check key services
    server_running = False
    ollama_running = False
    try:
        from urllib.request import urlopen as _urlopen
        with _urlopen("http://localhost:7860/api/health", timeout=3) as r:
            server_running = r.status == 200
    except Exception:
        pass

    try:
        from urllib.request import urlopen as _urlopen
        with _urlopen("http://localhost:11434/api/tags", timeout=3) as r:
            ollama_running = r.status == 200
    except Exception:
        pass

    heartbeat = {
        "timestamp": datetime.now().isoformat(),
        "epoch": int(datetime.now().timestamp()),
        "status": "online",
        "server_running": server_running,
        "ollama_running": ollama_running,
        "hostname": os.uname().nodename,
    }

    HEARTBEAT_FILE.write_text(json.dumps(heartbeat, indent=2))
    log.info(f"Heartbeat written: server={server_running}, ollama={ollama_running}")

    return heartbeat


if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    try:
        from core.agent_runtime import AgentRuntime
        _rt = AgentRuntime("heartbeat-monitor", "d0_core",
                           launchd_label="com.zoar.heartbeat")
        _rt.heartbeat("Writing system heartbeat")
    except Exception:
        _rt = None

    hb = write_heartbeat()

    if _rt:
        _rt.complete_task(f"server={hb.get('server_running')}, ollama={hb.get('ollama_running')}")
