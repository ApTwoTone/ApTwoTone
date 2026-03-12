#!/usr/bin/env python3
"""
Claude Failover Orchestrator — Monitors Claude usage and chains to free models.

When Claude usage hits 80%, warns via Telegram and starts routing overflow
tasks to free models in this chain:
  Claude → Gemini 2.5 Flash → GLM-4.7-Flash → Groq → Ollama

Usage:
    python3 scripts/claude_failover.py --status       # Show current orchestrator status
    python3 scripts/claude_failover.py --set-limit N  # Set daily Claude task limit
"""
import os
import sys
import json
import argparse
import logging
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parent.parent))

NEXUS_DIR = Path.home() / ".nexus"
LOG_DIR = NEXUS_DIR / "orchestrator_logs"
FAILOVER_STATE = LOG_DIR / "claude_failover_state.json"

LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "claude_failover.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("claude_failover")

# Failover chain — order matters
FAILOVER_CHAIN = [
    {"key": "gemini", "name": "Gemini 3 Flash", "gemini": "gemini-3-flash-preview", "env": "GEMINI_API_KEY"},
    {"key": "glm", "name": "GLM-4.7-Flash", "env": "ZAI_API_KEY"},
    {"key": "groq", "name": "Groq (Llama 4 Scout)", "env": "GROQ_API_KEY"},
    {"key": "ollama", "name": "Qwen 3 8B (Local)", "env": None},
]

# Default daily Claude task limit (can be adjusted)
DEFAULT_CLAUDE_LIMIT = 50


def load_state():
    """Load current failover state."""
    if FAILOVER_STATE.exists():
        try:
            state = json.loads(FAILOVER_STATE.read_text())
            # Reset if new day
            if state.get("date") != datetime.now().strftime("%Y-%m-%d"):
                return new_state()
            return state
        except Exception:
            pass
    return new_state()


def new_state():
    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "claude_tasks_today": 0,
        "claude_limit": DEFAULT_CLAUDE_LIMIT,
        "current_orchestrator": "claude",
        "failover_active": False,
        "warning_sent": False,
        "failover_history": [],
        "tasks_by_model": {},
    }


def save_state(state):
    FAILOVER_STATE.write_text(json.dumps(state, indent=2))


def get_usage_percent(state):
    """Get Claude usage as a percentage of daily limit."""
    if state["claude_limit"] <= 0:
        return 100
    return (state["claude_tasks_today"] / state["claude_limit"]) * 100


def should_failover(state):
    """Check if we should failover away from Claude."""
    return get_usage_percent(state) >= 80


def get_current_orchestrator(state):
    """Get which model is currently acting as orchestrator."""
    return state.get("current_orchestrator", "claude")


def record_claude_task(state, task_description=""):
    """Record a Claude task and check if failover is needed."""
    state["claude_tasks_today"] += 1
    usage_pct = get_usage_percent(state)

    # 80% warning
    if usage_pct >= 80 and not state.get("warning_sent"):
        state["warning_sent"] = True
        log.warning(f"Claude usage at {usage_pct:.0f}% — failover imminent")
        send_warning(state, usage_pct)

    # Activate failover
    if usage_pct >= 80 and not state.get("failover_active"):
        state["failover_active"] = True
        # Find first available model in chain
        for model in FAILOVER_CHAIN:
            if model["env"] is None or os.environ.get(model["env"]):
                state["current_orchestrator"] = model["key"]
                state["failover_history"].append({
                    "timestamp": datetime.now().isoformat(),
                    "from": "claude",
                    "to": model["key"],
                    "reason": f"Usage at {usage_pct:.0f}%",
                })
                log.info(f"Failover activated: claude -> {model['name']}")
                send_failover_notice(state, model["name"])
                break

    save_state(state)
    return state


def route_task(task_description, complexity="medium"):
    """Route a task to the appropriate model, respecting failover state."""
    state = load_state()

    if not state["failover_active"]:
        # Claude is primary — but only for critical/high
        if complexity in ["critical", "high"]:
            record_claude_task(state, task_description)
            return "claude"
        else:
            # Non-critical tasks always go to free models
            from scripts.orchestrator import get_available_model
            return get_available_model(complexity)
    else:
        # Failover active — route through free model chain
        from scripts.orchestrator import get_available_model
        model = get_available_model(complexity)
        if model:
            state["tasks_by_model"][model] = state["tasks_by_model"].get(model, 0) + 1
            save_state(state)
            return model
        return state.get("current_orchestrator", "ollama")


def reinstate_claude(state):
    """Reinstate Claude as primary orchestrator (new day or manual reset)."""
    state["failover_active"] = False
    state["current_orchestrator"] = "claude"
    state["warning_sent"] = False
    state["failover_history"].append({
        "timestamp": datetime.now().isoformat(),
        "from": state.get("current_orchestrator", "unknown"),
        "to": "claude",
        "reason": "Manual reinstatement or new day",
    })
    save_state(state)
    log.info("Claude reinstated as primary orchestrator")


def send_warning(state, usage_pct):
    """Send 80% usage warning via Telegram."""
    msg = (
        f"CLAUDE USAGE WARNING\n\n"
        f"Usage: {state['claude_tasks_today']}/{state['claude_limit']} tasks ({usage_pct:.0f}%)\n"
        f"Failover will activate at 80%\n"
        f"Current chain: Gemini → GLM → Groq → Ollama\n\n"
        f"No action needed — automatic failover will handle routing."
    )
    _send_telegram(msg)


def send_failover_notice(state, target_model):
    """Send failover activation notice via Telegram."""
    msg = (
        f"CLAUDE FAILOVER ACTIVATED\n\n"
        f"Primary orchestrator: {target_model}\n"
        f"Claude tasks today: {state['claude_tasks_today']}/{state['claude_limit']}\n"
        f"All new tasks routing to free models.\n"
        f"Claude will be reinstated tomorrow automatically."
    )
    _send_telegram(msg)


def _send_telegram(msg):
    """Send message via Telegram."""
    try:
        cfg_file = NEXUS_DIR / "config.json"
        if not cfg_file.exists():
            return
        cfg = json.loads(cfg_file.read_text())
        token = cfg.get("telegram_token", "")
        chat_ids = cfg.get("telegram_chat_ids", [])
        if not token or not chat_ids:
            return
        for cid in chat_ids:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = json.dumps({"chat_id": str(cid), "text": msg}).encode()
            req = Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urlopen(req, timeout=10) as resp:
                resp.read()
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")


def print_status(state):
    """Print current failover status."""
    usage_pct = get_usage_percent(state)
    print(f"\nCLAUDE FAILOVER STATUS — {state['date']}")
    print(f"{'=' * 50}")
    print(f"Claude tasks today: {state['claude_tasks_today']}/{state['claude_limit']} ({usage_pct:.0f}%)")
    print(f"Current orchestrator: {state['current_orchestrator']}")
    print(f"Failover active: {state['failover_active']}")
    print(f"Warning sent: {state.get('warning_sent', False)}")

    if state.get("tasks_by_model"):
        print(f"\nTasks by model:")
        for model, count in state["tasks_by_model"].items():
            print(f"  {model}: {count}")

    if state.get("failover_history"):
        print(f"\nFailover history:")
        for entry in state["failover_history"][-5:]:
            print(f"  {entry['timestamp']}: {entry['from']} → {entry['to']} ({entry['reason']})")

    # Show chain availability
    print(f"\nFailover chain availability:")
    for model in FAILOVER_CHAIN:
        available = model["env"] is None or bool(os.environ.get(model["env"]))
        status = "READY" if available else "NO KEY"
        print(f"  {model['name']}: {status}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true", help="Show current status")
    parser.add_argument("--set-limit", type=int, help="Set daily Claude task limit")
    parser.add_argument("--reset", action="store_true", help="Reinstate Claude as primary")
    args = parser.parse_args()

    # Load env vars
    zshrc = Path.home() / ".zshrc"
    if zshrc.exists():
        for line in zshrc.read_text().splitlines():
            if line.startswith("export ") and "=" in line:
                parts = line.replace("export ", "").split("=", 1)
                key = parts[0].strip()
                val = parts[1].strip().strip('"').strip("'")
                os.environ.setdefault(key, val)

    state = load_state()

    if args.set_limit:
        state["claude_limit"] = args.set_limit
        save_state(state)
        print(f"Claude daily limit set to {args.set_limit}")

    if args.reset:
        reinstate_claude(state)
        state = load_state()

    print_status(state)
