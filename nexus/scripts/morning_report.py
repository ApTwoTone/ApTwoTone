#!/usr/bin/env python3
"""
8AM Morning Report — Compiles overnight results and sends to Telegram.

Reads all overnight session logs, swarm results, and agent statuses,
then compiles into a single morning briefing.

Usage:
    python3 scripts/morning_report.py          # Send immediately
    python3 scripts/morning_report.py --check  # Check what would be sent
"""
import os
import sys
import json
import glob
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parent.parent))

NEXUS_DIR = Path.home() / ".nexus"
SESSION_DIR = NEXUS_DIR / "session_logs"
LOG_DIR = NEXUS_DIR / "orchestrator_logs"


def load_config():
    cfg_file = NEXUS_DIR / "config.json"
    if cfg_file.exists():
        return json.loads(cfg_file.read_text())
    return {}


def get_overnight_sessions():
    """Find all session logs from the last 12 hours."""
    cutoff = datetime.now().timestamp() - (12 * 3600)
    sessions = []
    for f in sorted(SESSION_DIR.glob("*.json"), reverse=True):
        if f.stat().st_mtime > cutoff:
            try:
                sessions.append(json.loads(f.read_text()))
            except Exception:
                pass
    return sessions


def get_quota_usage():
    """Get today's model quota usage."""
    quota_file = LOG_DIR / "quota_tracker.json"
    if quota_file.exists():
        try:
            return json.loads(quota_file.read_text())
        except Exception:
            pass
    return {"date": "unknown", "usage": {}}


def get_agent_status():
    """Get current agent status from hierarchy DB."""
    try:
        from core.agent_hierarchy import get_hierarchy_tree
        return get_hierarchy_tree()
    except Exception:
        return {"departments": []}


def get_booking_goal_section() -> str:
    """Get booking goal status for morning report header."""
    try:
        from core.booking_goal_monitor import get_booking_goal_status
        data = get_booking_goal_status()
        days = data["days_since_last_booking"]
        goal = data["goal_days"]
        top_leads = data["top_leads"]

        if days >= 9999:
            status_line = "Booking goal: No bookings yet (goal: every 5 days)"
        elif days >= goal:
            status_line = f"Booking goal: OVERDUE — {days} days since last booking (goal: every {goal} days)"
        elif days == goal - 1:
            status_line = f"Booking goal: WARNING — {days} days since last booking (act today)"
        else:
            status_line = f"Booking goal: {days}/{goal} days since last booking — on track"

        lead_lines = []
        for lead in top_leads[:3]:
            name = lead.get("full_name", "Unknown")
            city = lead.get("event_city", "")
            date = lead.get("event_date", "")
            status = lead.get("booking_status", "")
            line = f"  - {name} ({status})"
            if city:
                line += f", {city}"
            if date:
                line += f", {date}"
            lead_lines.append(line)

        section = status_line
        if lead_lines:
            section += "\nTop leads needing action:\n" + "\n".join(lead_lines)
        return section
    except Exception:
        return "Booking goal: (data unavailable)"


def compile_report():
    """Compile the full morning report."""
    today = datetime.now().strftime("%Y-%m-%d")
    sessions = get_overnight_sessions()
    quota = get_quota_usage()
    hierarchy = get_agent_status()
    booking_goal = get_booking_goal_section()

    # Count totals
    total_tasks = 0
    total_completed = 0
    total_errors = 0
    tasks_by_model = {}
    peak_swarm = 0

    for session in sessions:
        total_tasks += session.get("total_agents", session.get("total_tasks", 0))
        total_completed += session.get("completed", session.get("total_ok", 0))
        total_errors += session.get("errors", 0)

        # Track swarm peak
        if session.get("peak_concurrent", 0) > peak_swarm:
            peak_swarm = session["peak_concurrent"]

        # Count by model
        for r in session.get("results", []):
            if isinstance(r, dict):
                model = r.get("model", "unknown")
                tasks_by_model[model] = tasks_by_model.get(model, 0) + 1
            elif isinstance(r, list):
                for item in r:
                    model = item.get("model", "unknown")
                    tasks_by_model[model] = tasks_by_model.get(model, 0) + 1

    # Flatten swarm results
    for session in sessions:
        if "categories" in session:
            for cat_name, cat_results in session.get("results", {}).items():
                if isinstance(cat_results, list):
                    for r in cat_results:
                        model = r.get("model", "unknown")
                        tasks_by_model[model] = tasks_by_model.get(model, 0) + 1

    # Count active models
    active_models = set()
    for model_name in tasks_by_model:
        if model_name and model_name != "unknown" and model_name != "none":
            active_models.add(model_name.split(" (")[0])  # Strip "(fallback)"

    # Agent status summary
    active_agents = 0
    idle_agents = 0
    error_agents = 0
    for dept in hierarchy.get("departments", []):
        for agent in dept.get("agents", []):
            if agent["status"] == "active":
                active_agents += 1
            elif agent["status"] == "error":
                error_agents += 1
            else:
                idle_agents += 1

    # Build tasks by model string
    model_lines = []
    for model, count in sorted(tasks_by_model.items(), key=lambda x: -x[1]):
        if model and model != "unknown":
            model_lines.append(f"  {model}: {count}")

    # Build report
    report = f"""ZOAR OVERNIGHT REPORT — {today}

{booking_goal}

Runner layer overnight: PRIMARY (Mac Mini online)
Orchestrator: Claude Code (this session)
Claude usage: Active for security audit + orchestration only
Peak parallel workers: {peak_swarm}
Total models active overnight: {len(active_models)}
Total tasks completed: {total_completed}/{total_tasks}
Errors: {total_errors}

Tasks by model:
{chr(10).join(model_lines) if model_lines else "  No model data available"}

Agents status: {active_agents} active, {idle_agents} idle, {error_agents} errors

Quota usage today:
  Gemini: {quota.get('usage', {}).get('gemini', 0)}/500
  Groq: {quota.get('usage', {}).get('groq', 0)}/1000
  GLM (Z.AI): {quota.get('usage', {}).get('glm', 0)}/unlimited
  Ollama: {quota.get('usage', {}).get('ollama', 0)}/unlimited

YOUR 3 ACTIONS TODAY (ordered by booking impact):

1. REVIEW Facebook Ads audit and approve the recommended $100/week campaign plan.

2. REVIEW overnight ad copy and content drafts in ~/.nexus/session_logs/.

3. REVIEW vendor outreach drafts — approve batches to start building referral partnerships."""

    return report


def send_report(report_text):
    """Send report via Telegram."""
    cfg = load_config()
    token = cfg.get("telegram_token", "")
    chat_ids = cfg.get("telegram_chat_ids", [])

    if not token or not chat_ids:
        print("No Telegram config found")
        return False

    # Telegram max message is 4096 chars
    if len(report_text) > 4000:
        report_text = report_text[:4000] + "\n\n(truncated)"

    for cid in chat_ids:
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = json.dumps({
                "chat_id": str(cid),
                "text": report_text,
            }).encode()
            req = Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urlopen(req, timeout=10) as resp:
                resp.read()
            print(f"Report sent to chat {cid}")
        except Exception as e:
            print(f"Failed to send to {cid}: {e}")

    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Preview without sending")
    args = parser.parse_args()

    report = compile_report()

    if args.check:
        print(report)
    else:
        print(report)
        print("\n" + "=" * 40)
        send_report(report)
