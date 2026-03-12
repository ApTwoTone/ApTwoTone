"""
Convex Sync — Push data from FastAPI/SQLite to Convex cloud database.

The Convex HTTP endpoints receive data from this module and store it
in the reactive database. The Nexus Network Tauri app subscribes to
Convex queries for real-time updates (no polling needed).

Setup:
  1. Run `npx convex dev` in ~/nexus-network to get your deployment URL
  2. Add CONVEX_SITE_URL to ~/.nexus/config.json
"""

import json
import urllib.request
from pathlib import Path

CONFIG_FILE = Path.home() / ".nexus" / "config.json"


def _get_convex_url() -> str:
    """Get Convex HTTP site URL from config."""
    try:
        cfg = json.loads(CONFIG_FILE.read_text())
        return cfg.get("convex_site_url")
    except Exception:
        return None


def _post(path: str, data: dict) -> dict:
    """POST JSON to a Convex HTTP endpoint."""
    base = _get_convex_url()
    if not base:
        return None
    try:
        url = f"{base}{path}"
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}
        )
        resp = urllib.request.urlopen(req, timeout=5)
        return json.loads(resp.read())
    except Exception as e:
        print(f"[ConvexSync] Error posting to {path}: {e}")
        return None


def sync_lead(lead_data: dict) -> dict:
    """Sync a new or updated lead to Convex."""
    return _post("/sync/lead", lead_data)


def sync_agent_activity(agent_id: str, action: str, details: str = "", result: str = "") -> dict:
    """Log agent activity to Convex for real-time dashboard updates."""
    return _post("/sync/agent-activity", {
        "agent_id": agent_id,
        "action": action,
        "details": details,
        "result": result,
    })
