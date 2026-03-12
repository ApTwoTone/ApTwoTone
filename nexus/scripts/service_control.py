#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.service_control import list_service_states, resolve_service_id, set_service_enabled
from scripts.process_manager import ProcessManager


def _snapshot_map():
    pm = ProcessManager()
    process_index = {
        item.get("name"): item
        for item in pm.get_status()
        if isinstance(item, dict)
    }
    snapshots = []
    for state in list_service_states():
        process_state = process_index.get(state["process_name"]) or {}
        process_running = bool(process_state.get("actually_alive"))
        snapshots.append({
            **state,
            "process_status": "running" if process_running else process_state.get("status", "unknown"),
            "process_pid": int(process_state.get("pid") or 0),
            "process_running": process_running,
        })
    return pm, snapshots


def _print_status(snapshots):
    for item in snapshots:
        status = "enabled" if item.get("enabled") else "disabled"
        running = "running" if item.get("process_running") else item.get("process_status", "stopped")
        print(f"{item['id']}: {status} | {running} | pid={item['process_pid']}")
        print(f"  {item['summary']}")
        if item.get("blocked_by"):
            print(f"  blocked_by={','.join(item['blocked_by'])}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Pause or resume managed Nexus services.")
    parser.add_argument("action", choices=["status", "enable", "disable", "restart"])
    parser.add_argument("service", nargs="?", help="Service id or alias")
    parser.add_argument("--json", action="store_true", help="Print machine-readable output")
    parser.add_argument(
        "--clear-blockers",
        action="store_true",
        help="Clear broader blocking flags when enabling a service.",
    )
    args = parser.parse_args()

    pm, snapshots = _snapshot_map()

    if args.action == "status":
        if args.json:
            print(json.dumps({"services": snapshots}, indent=2))
        else:
            _print_status(snapshots)
        return 0

    if not args.service:
        parser.error("service is required for enable, disable, and restart")

    service_id = resolve_service_id(args.service)

    if args.action == "restart":
        updated = set_service_enabled(service_id, True, clear_blockers=args.clear_blockers)
        pm.stop_process(updated["process_name"])
        pm.start_process(updated["process_name"])
    elif args.action == "enable":
        updated = set_service_enabled(service_id, True, clear_blockers=args.clear_blockers)
        pm.start_process(updated["process_name"])
    else:
        updated = set_service_enabled(service_id, False, clear_blockers=args.clear_blockers)
        pm.stop_process(updated["process_name"])

    _, snapshots = _snapshot_map()
    current = next(item for item in snapshots if item["id"] == service_id)
    if args.json:
        print(json.dumps(current, indent=2))
    else:
        _print_status([current])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
