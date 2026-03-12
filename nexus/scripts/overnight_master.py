#!/usr/bin/env python3
"""
Overnight Master Runner — Runs ALL overnight tasks as a single launchd service.

This script replaces the need for VS Code / Claude Code to be open overnight.
launchd fires this at 11PM, it runs everything, and the morning report fires
separately at 8AM.

Execution order:
1. Load env vars from ~/.zshrc
2. Run parallel swarm (75 tasks)
3. Run overnight agents (18 tasks)
4. Run identity + queue quality sweep
5. Run Model Watcher if it's Monday
6. Send Telegram summary
7. Write heartbeat

Usage:
    python3 scripts/overnight_master.py           # Run everything
    python3 scripts/overnight_master.py --dry-run  # Preview what would run
"""
import os
import sys
import json
import time
import logging
from datetime import datetime
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).parent.parent))

NEXUS_DIR = Path.home() / ".nexus"
LOG_DIR = NEXUS_DIR / "orchestrator_logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "overnight_master.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("overnight_master")


def load_env():
    """Load environment variables from ~/.zshrc."""
    zshrc = Path.home() / ".zshrc"
    if zshrc.exists():
        for line in zshrc.read_text().splitlines():
            if line.startswith("export ") and "=" in line:
                parts = line.replace("export ", "").split("=", 1)
                key = parts[0].strip()
                val = parts[1].strip().strip('"').strip("'")
                os.environ.setdefault(key, val)
        log.info("Loaded env vars from ~/.zshrc")


def send_telegram(msg):
    """Send a message via Telegram."""
    try:
        cfg_file = NEXUS_DIR / "config.json"
        if not cfg_file.exists():
            return
        cfg = json.loads(cfg_file.read_text())
        token = cfg.get("telegram_token", "")
        chat_ids = cfg.get("telegram_chat_ids", [])
        if not token or not chat_ids:
            return

        if len(msg) > 4000:
            msg = msg[:4000] + "\n\n(truncated)"

        from urllib.request import Request, urlopen
        for cid in chat_ids:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = json.dumps({"chat_id": str(cid), "text": msg}).encode()
            req = Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urlopen(req, timeout=10) as resp:
                resp.read()
    except Exception as e:
        log.warning(f"Telegram send failed: {e}")


def run_swarm():
    """Run parallel swarm (75 tasks)."""
    log.info("=" * 60)
    log.info("PHASE 1: PARALLEL SWARM")
    log.info("=" * 60)
    try:
        from scripts.parallel_swarm import run_all_swarms
        result = run_all_swarms()
        log.info(f"Swarm complete: {result['total_ok']}/{result['total_tasks']} tasks")
        return result
    except Exception as e:
        log.error(f"Swarm failed: {e}")
        return {"total_tasks": 0, "total_ok": 0, "error": str(e)}


def run_agents():
    """Run overnight agents (18 tasks)."""
    log.info("=" * 60)
    log.info("PHASE 2: OVERNIGHT AGENTS")
    log.info("=" * 60)
    try:
        from scripts.overnight_run import run_overnight
        run_overnight()
        # Read the latest session log for stats
        session_dir = NEXUS_DIR / "session_logs"
        latest = max(session_dir.glob("overnight_*.json"), key=lambda f: f.stat().st_mtime)
        return json.loads(latest.read_text())
    except Exception as e:
        log.error(f"Overnight agents failed: {e}")
        return {"total_agents": 0, "completed": 0, "errors": 1, "error": str(e)}


def run_model_watcher():
    """Run model watcher if it's Monday."""
    if datetime.now().weekday() != 0:  # 0 = Monday
        log.info("Skipping Model Watcher (not Monday)")
        return None

    log.info("=" * 60)
    log.info("PHASE 3: MODEL WATCHER (Monday scan)")
    log.info("=" * 60)
    try:
        from scripts.model_watcher import run_scan, format_telegram_report
        scan_result, roster = run_scan()
        report = format_telegram_report(scan_result, roster)
        log.info(f"Model Watcher complete: {len(scan_result.get('new_models', []))} models scanned")
        return scan_result
    except Exception as e:
        log.error(f"Model Watcher failed: {e}")
        return {"error": str(e)}


def run_identity_quality_sweep():
    """Run overnight identity merge + queue quality prep."""
    log.info("=" * 60)
    log.info("PHASE 3: IDENTITY + QUEUE QUALITY SWEEP")
    log.info("=" * 60)
    try:
        from scripts.overnight_identity_quality import run_overnight_identity_quality
        result = run_overnight_identity_quality()
        ident = result.get("identity", {})
        presend = result.get("presend", {})
        log.info(
            "Identity sweep: merged %s leads (%s clusters), queue autofix=%s",
            ident.get("merged_leads", 0),
            ident.get("clusters_merged", 0),
            presend.get("autofixed_rows", 0),
        )
        return result
    except Exception as e:
        log.error(f"Identity quality sweep failed: {e}")
        return {"error": str(e)}


def write_heartbeat():
    """Write heartbeat to confirm system is alive."""
    try:
        from scripts.heartbeat import write_heartbeat as _write
        _write()
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")


def main(dry_run=False):
    started = datetime.now()
    log.info(f"OVERNIGHT MASTER STARTED at {started.isoformat()}")

    load_env()

    if dry_run:
        log.info("DRY RUN — would execute:")
        log.info("  1. Parallel swarm (75 tasks)")
        log.info("  2. Overnight agents (18 tasks)")
        log.info("  3. Identity + queue quality sweep")
        if datetime.now().weekday() == 0:
            log.info("  4. Model Watcher (Monday)")
        log.info("  5. Telegram summary")
        log.info("  6. Heartbeat")
        return

    send_telegram("OVERNIGHT RUN STARTING\n\nPhase 1: Parallel swarm (75 tasks)\nPhase 2: Agent run (18 tasks)\nEstimated duration: 10-15 minutes")

    # Phase 1: Swarm
    swarm_result = run_swarm()
    time.sleep(10)  # Cool down between phases

    # Phase 2: Agents
    agent_result = run_agents()
    time.sleep(5)

    # Phase 3: Identity + queue quality sweep
    identity_result = run_identity_quality_sweep()
    time.sleep(3)

    # Phase 4: Model Watcher (Mondays only)
    watcher_result = run_model_watcher()

    # Phase 5: Heartbeat
    write_heartbeat()

    # Phase 6: Summary
    elapsed = (datetime.now() - started).total_seconds()
    swarm_ok = swarm_result.get("total_ok", 0)
    swarm_total = swarm_result.get("total_tasks", 0)
    agent_ok = agent_result.get("completed", 0)
    agent_total = agent_result.get("total_agents", 0)

    summary = (
        f"OVERNIGHT RUN COMPLETE\n\n"
        f"Duration: {elapsed/60:.1f} minutes\n"
        f"Swarm: {swarm_ok}/{swarm_total} tasks\n"
        f"Agents: {agent_ok}/{agent_total} agents\n"
        f"Total: {swarm_ok + agent_ok}/{swarm_total + agent_total}\n"
        f"Errors: {agent_result.get('errors', 0)}\n\n"
        f"Morning report will send at 8AM."
    )

    if not identity_result.get("error"):
        ident = identity_result.get("identity", {})
        pre = identity_result.get("presend", {})
        summary += (
            f"\nIdentity sweep: {ident.get('merged_leads', 0)} merged"
            f" ({ident.get('clusters_merged', 0)} clusters)"
            f"\nQueue autofix rows: {pre.get('autofixed_rows', 0)}"
        )

    if watcher_result and not watcher_result.get("error"):
        new_models = len(watcher_result.get("new_models", []))
        summary += f"\nModel Watcher: {new_models} models scanned"

    send_telegram(summary)
    log.info(summary)
    log.info("OVERNIGHT MASTER FINISHED")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
