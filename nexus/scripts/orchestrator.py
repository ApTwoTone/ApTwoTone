#!/usr/bin/env python3
"""
NEXUS Master Orchestrator — Routes tasks to free AI models.

Accepts a high-level goal, breaks it into subtasks using Gemini 2.5 Pro (free),
scores each subtask for complexity, routes to the cheapest capable model,
tracks completion, and sends batched Telegram notifications.

Usage:
    python3 scripts/orchestrator.py "Build the landing page for zoar website"
    python3 scripts/orchestrator.py --resume  # Resume from last session log

Models (all FREE):
    - Gemini 3 Flash (AI Studio): orchestration, planning, long context
    - Groq (Llama/Qwen): boilerplate, CRUD, tests, simple refactors
    - Kimi K2.5 (OpenRouter): mid-complexity, tool-use tasks
    - Qwen 3 (Ollama local): drafts, exploration, zero-cost fallback
    - Claude Code: ONLY architecture, security, complex bugs, final review
"""
import os
import sys
import json
import time
import argparse
import logging
from pathlib import Path
from datetime import datetime
from urllib.request import Request, urlopen
from typing import Dict, List, Optional
from urllib.error import URLError

# ── Config ────────────────────────────────────────────────────────────────────
NEXUS_DIR = Path.home() / ".nexus"
CONFIG_FILE = NEXUS_DIR / "config.json"
LOG_DIR = NEXUS_DIR / "orchestrator_logs"
SESSION_DIR = NEXUS_DIR / "session_logs"
QUOTA_FILE = LOG_DIR / "quota_tracker.json"

LOG_DIR.mkdir(parents=True, exist_ok=True)
SESSION_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "orchestrator.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("orchestrator")

# ── Model Definitions ────────────────────────────────────────────────────────

MODELS = {
    "gemini": {
        "name": "Gemini 3 Flash",
        "endpoint": "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash:generateContent",
        "env_key": "GEMINI_API_KEY",
        "daily_limit": 500,  # Flash gets 500 free RPD (vs 25 for Pro)
        "complexity": ["high", "medium"],
        "best_for": "orchestration, planning, long context, documentation, research",
    },
    "groq": {
        "name": "Groq (Llama 4 Scout)",
        "endpoint": "https://api.groq.com/openai/v1/chat/completions",
        "model_id": "meta-llama/llama-4-scout-17b-16e-instruct",
        "env_key": "GROQ_API_KEY",
        "daily_limit": 1000,
        "complexity": ["low", "trivial"],
        "best_for": "boilerplate, CRUD, tests, simple refactors, repetitive code",
    },
    "openrouter": {
        "name": "Llama 3.3 70B (OpenRouter)",
        "endpoint": "https://openrouter.ai/api/v1/chat/completions",
        "model_id": "meta-llama/llama-3.3-70b-instruct:free",
        "env_key": "OPENROUTER_API_KEY",
        "daily_limit": 200,
        "complexity": ["medium"],
        "best_for": "mid-complexity coding tasks, reasoning, anything Claude declines",
    },
    "ollama": {
        "name": "Qwen 3 8B (Local Ollama)",
        "endpoint": "http://localhost:11434/api/generate",
        "model_id": "qwen3:8b",
        "env_key": None,  # No API key needed
        "daily_limit": 999999,
        "complexity": ["trivial", "low"],
        "best_for": "drafts, exploration, tasks Claude won't do, zero-cost fallback",
    },
    "glm": {
        "name": "GLM-4.7-Flash (Z.AI)",
        "endpoint": "https://api.z.ai/api/paas/v4/chat/completions",
        "model_id": "glm-4.7-flash",
        "env_key": "ZAI_API_KEY",
        "daily_limit": 999999,  # Free tier, no documented daily limit
        "complexity": ["medium", "low"],
        "best_for": "customer-facing content, ad copy, quotes, Facebook posts, general coding",
    },
    "glm45": {
        "name": "GLM-4.5-Flash (Z.AI)",
        "endpoint": "https://api.z.ai/api/paas/v4/chat/completions",
        "model_id": "glm-4.5-flash",
        "env_key": "ZAI_API_KEY",
        "daily_limit": 999999,  # Free tier, second Z.AI worker
        "complexity": ["medium", "low"],
        "best_for": "parallel worker, ad copy, vendor research, content drafting",
    },
}

# Complexity routing order: try cheapest first
ROUTING_ORDER = {
    "critical": ["gemini"],  # Only Claude should handle, but Gemini can plan
    "high": ["gemini", "glm", "glm45", "openrouter"],
    "medium": ["glm", "glm45", "openrouter", "gemini", "ollama"],
    "low": ["groq", "glm45", "glm", "ollama", "openrouter"],
    "trivial": ["ollama", "groq", "glm45"],
}


# ── Quota Tracking (Cost Guard) ──────────────────────────────────────────────

def load_quota() -> dict:
    if QUOTA_FILE.exists():
        data = json.loads(QUOTA_FILE.read_text())
        # Reset if it's a new day
        if data.get("date") != datetime.now().strftime("%Y-%m-%d"):
            return {"date": datetime.now().strftime("%Y-%m-%d"), "usage": {}}
        return data
    return {"date": datetime.now().strftime("%Y-%m-%d"), "usage": {}}


def save_quota(quota: dict):
    QUOTA_FILE.write_text(json.dumps(quota, indent=2))


def check_quota(model_key: str) -> bool:
    """Returns True if the model has free quota remaining."""
    quota = load_quota()
    used = quota.get("usage", {}).get(model_key, 0)
    limit = MODELS[model_key]["daily_limit"]
    return used < limit


def increment_quota(model_key: str):
    quota = load_quota()
    quota.setdefault("usage", {})
    quota["usage"][model_key] = quota["usage"].get(model_key, 0) + 1
    save_quota(quota)


def get_available_model(complexity: str) -> Optional[str]:
    """Find the cheapest available model for a given complexity level."""
    order = ROUTING_ORDER.get(complexity, ROUTING_ORDER["medium"])
    for model_key in order:
        model = MODELS[model_key]
        # Check API key exists (except Ollama which is local)
        if model["env_key"] and not os.environ.get(model["env_key"]):
            continue
        # Check quota
        if not check_quota(model_key):
            log.warning(f"Quota exhausted for {model['name']}, trying next...")
            continue
        return model_key
    log.error(f"No available model for complexity={complexity}!")
    return None


# ── Model API Calls ──────────────────────────────────────────────────────────

def call_gemini(prompt: str) -> str:
    """Call Gemini 2.5 Pro via Google AI Studio (free tier)."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")
    url = f"{MODELS['gemini']['endpoint']}?key={api_key}"
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 8192},
    }).encode()
    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    increment_quota("gemini")
    return data["candidates"][0]["content"]["parts"][0]["text"]


def call_openai_compatible(model_key: str, prompt: str) -> str:
    """Call OpenAI-compatible APIs (Groq, OpenRouter/Kimi)."""
    model = MODELS[model_key]
    api_key = os.environ.get(model["env_key"], "")
    if not api_key:
        raise RuntimeError(f"{model['env_key']} not set")
    payload = json.dumps({
        "model": model["model_id"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 4096,
    }).encode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "nexus-orchestrator/1.0",
    }
    req = Request(model["endpoint"], data=payload, headers=headers)
    with urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    increment_quota(model_key)
    return data["choices"][0]["message"]["content"]


def call_ollama(prompt: str) -> str:
    """Call local Ollama instance (Qwen 3, zero cost)."""
    payload = json.dumps({
        "model": MODELS["ollama"]["model_id"],
        "prompt": prompt,
        "stream": False,
    }).encode()
    req = Request(MODELS["ollama"]["endpoint"], data=payload,
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
        increment_quota("ollama")
        return data["response"]
    except URLError:
        raise RuntimeError("Ollama not running. Start with: brew services start ollama")


def call_glm(prompt: str) -> str:
    """Call GLM-4.7-Flash via Z.AI (free tier, OpenAI-compatible)."""
    api_key = os.environ.get("ZAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("ZAI_API_KEY not set")
    model = MODELS["glm"]
    payload = json.dumps({
        "model": model["model_id"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 4096,
    }).encode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    req = Request(model["endpoint"], data=payload, headers=headers)
    with urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    increment_quota("glm")
    return data["choices"][0]["message"]["content"]


def call_model(model_key: str, prompt: str) -> str:
    """Route to the correct API for a given model."""
    log.info(f"Calling {MODELS[model_key]['name']}...")
    if model_key == "gemini":
        return call_gemini(prompt)
    elif model_key == "ollama":
        return call_ollama(prompt)
    elif model_key == "glm":
        return call_glm(prompt)
    else:
        return call_openai_compatible(model_key, prompt)


# ── Task Planning ─────────────────────────────────────────────────────────────

PLANNING_PROMPT_TEMPLATE = (
    "You are a task orchestrator. Break down the following goal into 3-8 concrete subtasks.\n\n"
    "For each subtask, provide:\n"
    '1. A short title (5-10 words)\n'
    '2. A detailed description of what to do\n'
    '3. Complexity rating: "critical", "high", "medium", "low", or "trivial"\n'
    '   - critical: architecture decisions, security, complex multi-system bugs\n'
    '   - high: large features, system design, research requiring 100K+ context\n'
    '   - medium: moderate features, refactoring, mid-level debugging\n'
    '   - low: boilerplate, CRUD operations, simple functions, test writing\n'
    '   - trivial: formatting, comments, simple config changes, file moves\n'
    '4. Whether it requires human approval: true/false\n\n'
    "IMPORTANT: Bias toward lower complexity. Most coding tasks are low or medium.\n"
    "Only use critical for genuine architecture/security decisions.\n\n"
    "Respond with ONLY valid JSON array. No markdown, no explanation.\n\n"
    "Example response:\n"
    '[{"title": "Create database schema", "description": "...", "complexity": "medium", "needs_approval": false},'
    ' {"title": "Review security model", "description": "...", "complexity": "critical", "needs_approval": true}]\n\n'
)


def plan_tasks(goal: str) -> List[Dict]:
    """Use Gemini to break a goal into scored subtasks."""
    prompt = PLANNING_PROMPT_TEMPLATE + f"GOAL: {goal}"
    model_key = get_available_model("high")
    if not model_key:
        log.error("No model available for planning!")
        return []

    result = call_model(model_key, prompt)

    # Extract JSON from response (handle markdown code blocks)
    result = result.strip()
    if result.startswith("```"):
        result = result.split("\n", 1)[1]
        result = result.rsplit("```", 1)[0]

    try:
        tasks = json.loads(result)
        if not isinstance(tasks, list):
            raise ValueError("Expected JSON array")
        for i, task in enumerate(tasks):
            task["id"] = i + 1
            task["status"] = "pending"
            task["assigned_model"] = None
            task["result"] = None
        log.info(f"Planned {len(tasks)} subtasks from goal")
        return tasks
    except (json.JSONDecodeError, ValueError) as e:
        log.error(f"Failed to parse plan: {e}\nRaw: {result[:500]}")
        return []


# ── Task Execution ────────────────────────────────────────────────────────────

EXECUTION_PROMPT_TEMPLATE = (
    "You are a coding assistant. Complete the following task precisely.\n\n"
    "Task: {title}\n"
    "Details: {description}\n\n"
    "Respond with:\n"
    "1. The code or content you produced\n"
    "2. A brief summary of what you did\n"
    "3. Any issues or blockers encountered\n\n"
    "Be concise and direct. Write production-quality code."
)


def execute_task(task: dict) -> dict:
    """Execute a single subtask using the appropriate model, with fallback on failure."""
    complexity = task.get("complexity", "medium")
    order = ROUTING_ORDER.get(complexity, ROUTING_ORDER["medium"])
    # Add all models as fallbacks beyond the primary routing order
    all_fallbacks = order + [k for k in MODELS if k not in order]

    prompt = EXECUTION_PROMPT_TEMPLATE.format(title=task["title"], description=task["description"])

    for model_key in all_fallbacks:
        model = MODELS[model_key]
        # Check API key exists (except Ollama which is local)
        if model["env_key"] and not os.environ.get(model["env_key"]):
            continue
        if not check_quota(model_key):
            continue

        task["assigned_model"] = model["name"]
        log.info(f"Task {task['id']}: '{task['title']}' → {model['name']} (complexity={complexity})")

        # Log routing decision
        routing_log = {
            "timestamp": datetime.now().isoformat(),
            "task_id": task["id"],
            "task_title": task["title"],
            "complexity": complexity,
            "routed_to": model_key,
            "model_name": model["name"],
        }
        log_path = LOG_DIR / f"routing_{datetime.now().strftime('%Y%m%d')}.jsonl"
        with open(log_path, "a") as f:
            f.write(json.dumps(routing_log) + "\n")

        try:
            result = call_model(model_key, prompt)
            task["status"] = "completed"
            task["result"] = result
            return task
        except Exception as e:
            log.warning(f"Task {task['id']} failed on {model['name']}: {e} — trying next model...")
            continue

    task["status"] = "blocked"
    task["result"] = "All models failed or exhausted"
    log.error(f"Task {task['id']}: all models failed")
    return task


# ── Session Persistence ───────────────────────────────────────────────────────

def get_session_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def save_session(session_id: str, goal: str, tasks: List[Dict]):
    """Write session state to a persistent markdown log."""
    log_path = SESSION_DIR / f"{session_id}.md"
    lines = [
        f"# Session: {session_id}",
        f"**Goal**: {goal}",
        f"**Started**: {datetime.now().isoformat()}",
        f"**Status**: {'complete' if all(t['status'] == 'completed' for t in tasks) else 'in_progress'}",
        "",
        "## Tasks",
        "",
    ]
    for task in tasks:
        status_icon = {"pending": "⬜", "completed": "✅", "error": "❌", "blocked": "🚫"}.get(task["status"], "⬜")
        lines.append(f"### {status_icon} Task {task['id']}: {task['title']}")
        lines.append(f"- **Complexity**: {task['complexity']}")
        lines.append(f"- **Model**: {task.get('assigned_model', 'not assigned')}")
        lines.append(f"- **Status**: {task['status']}")
        if task.get("result"):
            # Truncate long results for the log
            result_preview = task["result"][:500]
            if len(task["result"]) > 500:
                result_preview += "... (truncated)"
            lines.append(f"- **Result**: {result_preview}")
        lines.append("")

    log_path.write_text("\n".join(lines))
    log.info(f"Session saved: {log_path}")
    return log_path


# ── Telegram Notifications (Batched) ─────────────────────────────────────────

LAST_PING_FILE = LOG_DIR / "last_telegram_ping.txt"
MIN_PING_INTERVAL = 1200  # 20 minutes between pings


def should_send_telegram() -> bool:
    """Rate-limit Telegram pings to max every 20 minutes."""
    if LAST_PING_FILE.exists():
        last = float(LAST_PING_FILE.read_text().strip())
        if time.time() - last < MIN_PING_INTERVAL:
            return False
    return True


def send_telegram_batch(goal: str, tasks: List[Dict], is_final: bool = False):
    """Send a batched summary to Telegram."""
    if not should_send_telegram() and not is_final:
        log.info("Skipping Telegram ping (rate limited)")
        return

    cfg = load_config()
    token = cfg.get("telegram_token", "")
    chat_ids = cfg.get("telegram_chat_ids", [])
    if not token or not chat_ids:
        return

    completed = sum(1 for t in tasks if t["status"] == "completed")
    errors = sum(1 for t in tasks if t["status"] == "error")
    pending = sum(1 for t in tasks if t["status"] == "pending")
    needs_approval = [t for t in tasks if t.get("needs_approval") and t["status"] == "pending"]

    if is_final:
        header = "✅ *Batch Complete*"
    elif errors:
        header = "⚠️ *Orchestrator Update*"
    elif needs_approval:
        header = "🔔 *Approval Needed*"
    else:
        header = "📊 *Progress Update*"

    text = (
        f"{header}\n\n"
        f"*Goal*: {goal}\n"
        f"Progress: {completed}/{len(tasks)} done"
    )
    if errors:
        text += f", {errors} errors"
    if pending:
        text += f", {pending} pending"

    # Show model usage
    models_used = set(t.get("assigned_model", "?") for t in tasks if t.get("assigned_model"))
    if models_used:
        text += f"\n*Models*: {', '.join(models_used)}"

    if needs_approval:
        text += "\n\n*Needs your approval:*"
        for t in needs_approval:
            text += f"\n• {t['title']}"

    if errors:
        text += "\n\n*Errors:*"
        for t in tasks:
            if t["status"] == "error":
                text += f"\n• {t['title']}: {str(t.get('result', ''))[:100]}"

    for cid in chat_ids:
        try:
            _send_tg(token, str(cid), text)
        except Exception as e:
            log.warning(f"Telegram send failed: {e}")

    LAST_PING_FILE.write_text(str(time.time()))


def _send_tg(token: str, chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id, "text": text, "parse_mode": "Markdown"
    }).encode()
    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=10) as resp:
        resp.read()


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    return {}


# ── Main ──────────────────────────────────────────────────────────────────────

def run(goal: str):
    session_id = get_session_id()
    log.info(f"Session {session_id}: {goal}")

    # Step 1: Plan
    log.info("Planning subtasks...")
    tasks = plan_tasks(goal)
    if not tasks:
        log.error("Planning failed — no tasks generated")
        return

    save_session(session_id, goal, tasks)

    # Step 2: Execute non-approval tasks
    for task in tasks:
        if task.get("needs_approval"):
            log.info(f"Task {task['id']} needs approval — skipping for now")
            continue

        execute_task(task)
        save_session(session_id, goal, tasks)

    # Step 3: Send final summary
    send_telegram_batch(goal, tasks, is_final=True)

    # Step 4: Print summary
    print("\n" + "=" * 60)
    print(f"SESSION: {session_id}")
    print(f"GOAL: {goal}")
    print("=" * 60)
    for task in tasks:
        icon = {"pending": "⬜", "completed": "✅", "error": "❌", "blocked": "🚫"}.get(task["status"], "?")
        print(f"  {icon} [{task['complexity']}] {task['title']} → {task.get('assigned_model', 'N/A')}")
    print("=" * 60)

    # Show quota usage
    quota = load_quota()
    print("\nQuota usage today:")
    for model_key, model in MODELS.items():
        used = quota.get("usage", {}).get(model_key, 0)
        limit = model["daily_limit"]
        print(f"  {model['name']}: {used}/{limit}")

    log_path = save_session(session_id, goal, tasks)
    print(f"\nSession log: {log_path}")


def main():
    parser = argparse.ArgumentParser(description="NEXUS Master Orchestrator")
    parser.add_argument("goal", nargs="?", help="High-level goal to accomplish")
    parser.add_argument("--status", action="store_true", help="Show quota status")
    parser.add_argument("--resume", action="store_true", help="Resume last session")
    args = parser.parse_args()

    if args.status:
        quota = load_quota()
        print(f"Date: {quota.get('date', 'N/A')}")
        for model_key, model in MODELS.items():
            used = quota.get("usage", {}).get(model_key, 0)
            limit = model["daily_limit"]
            has_key = "✅" if (not model["env_key"] or os.environ.get(model["env_key"])) else "❌ (no key)"
            print(f"  {model['name']}: {used}/{limit} {has_key}")
        return

    if args.resume:
        # Find most recent session log
        logs = sorted(SESSION_DIR.glob("*.md"), reverse=True)
        if logs:
            print(f"Last session: {logs[0]}")
            print(logs[0].read_text())
        else:
            print("No previous sessions found")
        return

    if not args.goal:
        parser.print_help()
        return

    run(args.goal)


if __name__ == "__main__":
    main()
