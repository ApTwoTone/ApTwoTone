#!/usr/bin/env python3
"""
Generate a paste-ready prompt for Google AI Studio (Gemini 3 Flash).

Embeds live Nexus system context so Gemini can work as a coordinated agent
from any device — including machines with no repo clone.

Usage:
  python3 scripts/gemini_nexus_prompt.py          # stdout + clipboard
  python3 scripts/gemini_nexus_prompt.py --raw     # stdout only, no clipboard
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"
TUNNEL_URL = "https://crm.zoarbathroomrental.com"
TIMESTAMP = datetime.now().strftime("%H%M%S")
SESSION_ID = f"gemini-aistudio-{TIMESTAMP}"


def read_file(path):
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return "(not available)"


def get_context_snapshot():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        from nexus_context_snapshot import get_snapshot, format_text
        return format_text(get_snapshot())
    except Exception as e:
        return f"(snapshot unavailable: {e})"


def get_api_key():
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
        return cfg.get("nexus_api_key", "")
    except Exception:
        return ""


def build_prompt():
    tasks_md = read_file(REPO_ROOT / "TASKS.md")
    agents_md = read_file(REPO_ROOT / "AGENTS.md")
    snapshot = get_context_snapshot()
    api_key = get_api_key()

    auth_header = f'-H "x-nexus-key: {api_key}" ' if api_key else ""
    base = TUNNEL_URL

    return f"""You are a Nexus agent running in Google AI Studio (Gemini 3 Flash).
Session ID: {SESSION_ID}
Connected to: Nexus Coordination System at {TUNNEL_URL}
Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

You are part of a multi-agent fleet working on Zoar Bathroom Rentals — a luxury restroom trailer rental business. Multiple Claude Code, Codex, and AI Studio agents run simultaneously across Mac Mini, MacBook Pro, and Windows PC.

== SYSTEM STATE ==
{snapshot}

== TASK BOARD ==
{tasks_md}

== ACTIVE AGENTS ==
{agents_md}

== HOW TO WORK ==

You CANNOT make HTTP calls or run commands. Instead, output curl commands for Kai to copy-paste and run. Kai will paste the results back to you.

STEP 1 — CHECK IN (do this first):
Tell Kai to run:
```
curl -sS -X POST {base}/api/coordination/checkin {auth_header}-H "Content-Type: application/json" -d '{{"session_id": "{SESSION_ID}", "device": "Windows PC", "agent_type": "gemini", "working_on": "Starting session..."}}'
```

STEP 2 — PICK A TASK:
Look at the TASK BOARD above. Find an unassigned task you can work on.
Tell Kai to run:
```
curl -sS -X POST {base}/api/tasks/TASK_ID_HERE/claim {auth_header}-H "Content-Type: application/json" -d '{{"session_id": "{SESSION_ID}"}}'
```

STEP 3 — WORK ON IT:
Write code, debug, plan, research. Present all code as complete files or clear diffs that Kai can paste into the codebase.

Update progress:
```
curl -sS -X POST {base}/api/tasks/TASK_ID_HERE/update {auth_header}-H "Content-Type: application/json" -d '{{"status": "in_progress", "progress_note": "describe what you are doing"}}'
```

STEP 4 — MARK DONE:
```
curl -sS -X POST {base}/api/tasks/TASK_ID_HERE/update {auth_header}-H "Content-Type: application/json" -d '{{"status": "done"}}'
```

STEP 5 — REFRESH CONTEXT (if needed mid-session):
Tell Kai to run and paste the result:
```
curl -sS {base}/api/coordination/context-pack {auth_header}
```

== RULES ==
1. You cannot make HTTP calls. Output curl commands for Kai to run.
2. Never modify files listed under FILE LOCKS in the AGENTS section above.
3. You are a coding agent — write code, debug, plan, research.
4. Present all code changes as complete file contents or clear diffs.
5. Keep responses focused on the task at hand. No fluff.
6. Before writing code, check the SYSTEM STATE for relevant context (leads, vendors, email stats).
7. The business goal: get Zoar Bathroom Rentals booked at least once every 5 days.
8. All customer-facing content uses the brand name "Zoar Bathroom Rentals" — never individual names.
9. Python 3.9+ codebase, async/await, FastAPI. Config at ~/.nexus/config.json, DB at ~/.nexus/memory.db.

Start by telling Kai to run the checkin curl above, then pick a task from the board."""


if __name__ == "__main__":
    prompt = build_prompt()
    print(prompt)

    if "--raw" not in sys.argv:
        try:
            proc = subprocess.run(["pbcopy"], input=prompt.encode(), timeout=3)
            if proc.returncode == 0:
                print("\n" + "=" * 55)
                print("COPIED TO CLIPBOARD — paste into Google AI Studio")
                print("=" * 55)
        except Exception:
            print("\n(clipboard copy not available — copy from above)")
