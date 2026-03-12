#!/usr/bin/env python3
"""
submit_fleet_task.py — Add agentic tasks to the Nexus AI Fleet.
"""
import sys
import json
import argparse
from pathlib import Path

# Add nexus root to path
NEXUS_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(NEXUS_ROOT))

from core.fleet_task_queue import FleetTaskQueue

def main():
    parser = argparse.ArgumentParser(description="Submit task to AI Fleet")
    parser.add_argument("type", help="Task type (e.g., vendor_research, analysis, background)")
    parser.add_argument("prompt", help="The actual task description/prompt")
    parser.add_argument("--tier", type=int, default=3, help="Worker tier (1-3)")
    parser.add_argument("--priority", type=int, default=5, help="Priority (1-10)")
    args = parser.parse_args()

    q = FleetTaskQueue()
    task_id = q.enqueue(
        args.type,
        {"prompt": args.prompt},
        tier=args.tier,
        priority=args.priority
    )

    print(f"Task enqueued. ID: \033[92m{task_id}\033[0m")
    print(f"Type: {args.type} | Tier: {args.tier} | Priority: {args.priority}")
    print("Monitor progress with: python3 scripts/fleet_status.py --watch")

if __name__ == "__main__":
    main()
