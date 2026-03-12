#!/usr/bin/env python3
"""
Model Watcher Agent — Weekly scan for new free AI models.

Runs every Monday at 6AM via launchd. Scans known AI benchmark leaderboards
and model registries for new free-tier models that could join the Nexus roster.

Usage:
    python3 scripts/model_watcher.py              # Run scan and report
    python3 scripts/model_watcher.py --check      # Preview without Telegram
"""
import os
import sys
import json
import argparse
import logging
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).parent.parent))

NEXUS_DIR = Path.home() / ".nexus"
LOG_DIR = NEXUS_DIR / "orchestrator_logs"
ROSTER_FILE = NEXUS_DIR / "model_roster.json"

LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "model_watcher.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("model_watcher")

# Current Nexus roster — models we already know about
CURRENT_ROSTER = {
    "gemini-3-flash-preview": {"provider": "Google AI Studio", "free": True, "status": "active"},
    "llama-4-scout": {"provider": "Groq", "free": True, "status": "active"},
    "llama-3.3-70b": {"provider": "OpenRouter", "free": True, "status": "active"},
    "qwen3-8b": {"provider": "Ollama (local)", "free": True, "status": "active"},
    "glm-4.7-flash": {"provider": "Z.AI", "free": True, "status": "pending_key"},
    # Evaluated and rejected
    "glm-5": {"provider": "Z.AI", "free": False, "status": "rejected", "reason": "$1/M input"},
    "minimax-m2.5": {"provider": "MiniMax", "free": False, "status": "rejected", "reason": "No permanent free tier"},
    "deepseek-v3.2": {"provider": "DeepSeek", "free": False, "status": "rejected", "reason": "No free tier"},
}


def load_roster():
    """Load the persistent model roster."""
    if ROSTER_FILE.exists():
        try:
            return json.loads(ROSTER_FILE.read_text())
        except Exception:
            pass
    return {
        "last_scan": None,
        "models": CURRENT_ROSTER,
        "scan_history": [],
    }


def save_roster(roster):
    ROSTER_FILE.write_text(json.dumps(roster, indent=2))


def scan_for_models():
    """Use Gemini to scan for new free AI models.

    The three-check rule from CLAUDE.md Rule 21:
    1. Free tier confirmed (permanently free, not trial)
    2. API accessible (not platform-only)
    3. Benchmarks meet minimum bar
    """
    # Use Gemini for the research (free, good at research tasks)
    from scripts.orchestrator import call_model, MODELS

    prompt = """You are a Model Watcher agent. Your job is to identify NEW free AI models that could be useful for a code/content generation pipeline.

CURRENT ROSTER (already known):
- Gemini 2.5 Flash (Google AI Studio) — FREE, active
- Llama 4 Scout 17B (Groq) — FREE, active
- Llama 3.3 70B (OpenRouter free) — FREE, active
- Qwen 3 8B (Ollama local) — FREE, active
- GLM-4.7-Flash (Z.AI) — FREE, pending API key

ALREADY EVALUATED AND REJECTED:
- GLM-5 (Z.AI) — $1/M input, NOT free
- MiniMax M2.5 — No permanent free tier
- DeepSeek V3.2 — No free tier
- Kimi K2.5 — $0.45/M on OpenRouter, NOT free

REQUIREMENTS FOR NEW MODELS (all three must be true):
1. PERMANENTLY FREE API access (not a trial, not sign-up bonus credits)
2. API-accessible (not platform-only like kimi.com Agent Swarm)
3. Competitive benchmarks (at least as good as Llama 3.3 70B)

Scan these sources:
- OpenRouter: any new ":free" models added recently
- Groq: any new free models on their API
- Together AI: any free tier models
- Fireworks AI: any free tier models
- Cerebras: any free inference API
- Sambanova: any free inference
- Any other provider offering permanently free LLM API access

For EACH new model found, report:
- Model name and version
- Provider and API endpoint format
- Free tier details (daily limits, rate limits)
- Key benchmarks (coding, reasoning, general)
- Whether it's OpenAI-compatible
- Confidence that it meets all 3 requirements (high/medium/low)

If no new models qualify, say "NO NEW MODELS FOUND" — that's a valid result.

Respond with ONLY valid JSON:
{
  "scan_date": "YYYY-MM-DD",
  "new_models": [
    {
      "name": "...",
      "provider": "...",
      "free_confirmed": true/false,
      "api_accessible": true/false,
      "benchmark_pass": true/false,
      "openai_compatible": true/false,
      "daily_limit": "...",
      "confidence": "high/medium/low",
      "notes": "..."
    }
  ],
  "market_notes": "Brief summary of AI model market trends relevant to free API access"
}"""

    try:
        result = call_model("gemini", prompt)
        # Extract JSON
        result = result.strip()
        if result.startswith("```"):
            result = result.split("\n", 1)[1]
            result = result.rsplit("```", 1)[0]
        return json.loads(result)
    except Exception as e:
        log.error(f"Scan failed: {e}")
        return {"scan_date": datetime.now().strftime("%Y-%m-%d"), "new_models": [], "error": str(e)}


def format_telegram_report(scan_result, roster):
    """Format scan results for Telegram notification."""
    new_models = scan_result.get("new_models", [])
    qualifying = [m for m in new_models if m.get("free_confirmed") and m.get("api_accessible") and m.get("benchmark_pass")]

    report = f"MODEL WATCHER SCAN — {scan_result.get('scan_date', 'today')}\n\n"
    report += f"Current roster: {sum(1 for m in roster['models'].values() if m.get('status') == 'active')} active models\n"
    report += f"Models scanned: {len(new_models)}\n"
    report += f"Qualifying: {len(qualifying)}\n\n"

    if qualifying:
        report += "NEW MODELS FOUND:\n"
        for m in qualifying:
            report += f"  {m['name']} ({m['provider']})\n"
            report += f"  Free: {m.get('daily_limit', 'unknown')}\n"
            report += f"  Confidence: {m.get('confidence', 'unknown')}\n\n"
        report += "Reply ADD <model> or SKIP <model> for each.\n"
    else:
        report += "No new qualifying models found.\n"

    market = scan_result.get("market_notes", "")
    if market:
        report += f"\nMarket: {market[:200]}\n"

    return report


def run_scan():
    """Run the full model watcher scan."""
    log.info("Starting Model Watcher scan...")
    roster = load_roster()

    # Run the scan
    scan_result = scan_for_models()

    # Update roster
    roster["last_scan"] = datetime.now().isoformat()
    roster["scan_history"].append({
        "date": datetime.now().isoformat(),
        "new_models_found": len(scan_result.get("new_models", [])),
        "qualifying": sum(
            1 for m in scan_result.get("new_models", [])
            if m.get("free_confirmed") and m.get("api_accessible") and m.get("benchmark_pass")
        ),
    })

    # Add new models to roster as "pending_review"
    for m in scan_result.get("new_models", []):
        model_key = m["name"].lower().replace(" ", "-").replace(".", "")
        if model_key not in roster["models"]:
            roster["models"][model_key] = {
                "provider": m.get("provider", "unknown"),
                "free": m.get("free_confirmed", False),
                "api_accessible": m.get("api_accessible", False),
                "benchmark_pass": m.get("benchmark_pass", False),
                "status": "pending_review" if m.get("free_confirmed") else "rejected",
                "discovered": datetime.now().isoformat(),
                "notes": m.get("notes", ""),
            }

    save_roster(roster)
    log.info(f"Roster updated: {len(roster['models'])} models tracked")

    return scan_result, roster


def send_telegram_report(report_text):
    """Send report via Telegram."""
    try:
        cfg_file = NEXUS_DIR / "config.json"
        if not cfg_file.exists():
            log.warning("No config.json found")
            return
        cfg = json.loads(cfg_file.read_text())
        token = cfg.get("telegram_token", "")
        chat_ids = cfg.get("telegram_chat_ids", [])

        if not token or not chat_ids:
            log.warning("No Telegram config")
            return

        if len(report_text) > 4000:
            report_text = report_text[:4000] + "\n\n(truncated)"

        for cid in chat_ids:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = json.dumps({"chat_id": str(cid), "text": report_text}).encode()
            req = Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urlopen(req, timeout=10) as resp:
                resp.read()
            log.info(f"Report sent to {cid}")
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Preview without sending")
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

    scan_result, roster = run_scan()
    report = format_telegram_report(scan_result, roster)

    print(report)
    if not args.check:
        send_telegram_report(report)
