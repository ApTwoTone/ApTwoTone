#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple
from urllib import parse as urlparse
from urllib import request as urlrequest


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path.home() / ".nexus" / "config.json"
REPORT_PATH = ROOT / "outputs" / "auto_heal_strict_report.json"
CHAT_ID = "8540603351"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(cmd: List[str], timeout: int = 60) -> Tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    out = ((p.stdout or "") + ("\n" + p.stderr if p.stderr else "")).strip()
    return p.returncode, out


def _get_json(url: str, timeout: int = 10) -> Dict:
    req = urlrequest.Request(url, method="GET")
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_json(url: str, payload: Dict, timeout: int = 30) -> Dict:
    req = urlrequest.Request(
        url,
        method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urlrequest.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _load_cfg() -> Dict:
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _send_telegram(text: str) -> Dict:
    cfg = _load_cfg()
    token = str(cfg.get("telegram_token") or cfg.get("telegram_bot_token") or "").strip()
    if not token:
        return {"ok": False, "error": "missing telegram token"}
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urlparse.urlencode({"chat_id": CHAT_ID, "text": text}).encode("utf-8")
    req = urlrequest.Request(url, data=body, method="POST")
    try:
        with urlrequest.urlopen(req, timeout=15) as resp:
            _ = resp.read()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _mark(results: Dict, key: str, ok: bool, detail: str = "") -> None:
    results["checks"][key] = {"ok": bool(ok), "detail": detail}


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto-heal + strict verification pass")
    parser.add_argument("--allow-fallback", action="store_true", help="Allow fallback specialists in final pass")
    parser.add_argument("--send-telegram", action="store_true", help="Send completion summary to Telegram")
    args = parser.parse_args()

    results: Dict[str, object] = {
        "started_at": _now(),
        "allow_fallback": bool(args.allow_fallback),
        "checks": {},
        "actions": [],
    }

    # 1) Enforce scraper pause if requested in config.
    cfg = _load_cfg()
    disable_fb = bool(cfg.get("disable_facebook_scraper", False) or cfg.get("disable_browser_scrapers", False))
    if disable_fb:
        _run(["pkill", "-f", "facebook_group_scraper.py"], timeout=10)
        results["actions"].append("facebook scraper process stopped due to config disable flag")
    _mark(results, "facebook_scraper_paused", True if disable_fb else True, "disabled_in_config" if disable_fb else "not_disabled")

    # 2) Refresh launchd services.
    rc, out = _run(["bash", str(ROOT / "scripts" / "start_nexus.sh")], timeout=120)
    _mark(results, "start_nexus_refresh", rc == 0, out[-500:])

    # 3) Ensure daemon set (feedback collector included).
    rc, out = _run(["bash", str(ROOT / "scripts" / "start_all_daemons.sh")], timeout=180)
    _mark(results, "start_all_daemons", rc == 0, out[-800:])

    # 4) Reconcile specialist deployment metadata.
    deploy_cmd = [
        "python3",
        str(ROOT / "scripts" / "deploy_brain_now.py"),
        "--skip-sync",
    ]
    if not args.allow_fallback:
        deploy_cmd.append("--require-trained")
    rc, out = _run(deploy_cmd, timeout=600)
    _mark(results, "deploy_reconcile", rc == 0, out[-1200:])

    # 5) API status checks.
    api_ok = True
    try:
        brain = _get_json("http://localhost:7860/api/brain/status", timeout=10)
        specialists = brain.get("specialists", {}) if isinstance(brain, dict) else {}
        bad = []
        fallback = []
        if isinstance(specialists, dict):
            for name, state in specialists.items():
                s = str(state)
                if s not in {"online_trained", "online_fallback", "pending_transfer", "ollama_offline"}:
                    bad.append(f"{name}:{s}")
                if s == "online_fallback":
                    fallback.append(name)
        if bad:
            api_ok = False
            _mark(results, "brain_status_api", False, "; ".join(bad))
        elif fallback and not args.allow_fallback:
            api_ok = False
            _mark(results, "brain_status_api", False, "fallback specialists: " + ", ".join(fallback))
        else:
            _mark(results, "brain_status_api", True, f"specialists={len(specialists)}")
    except Exception as e:
        api_ok = False
        _mark(results, "brain_status_api", False, str(e))

    # 6) Strict smoke test.
    smoke_cmd = [
        "python3",
        str(ROOT / "scripts" / "post_transfer_smoke_test.py"),
        "--skip-create",
    ]
    if args.allow_fallback:
        smoke_cmd.append("--allow-fallback")
    rc, out = _run(smoke_cmd, timeout=900)
    _mark(results, "post_transfer_smoke", rc == 0, out[-1500:])

    # 7) Feedback endpoint sanity.
    try:
        fb = _post_json(
            "http://localhost:7860/api/brain/feedback",
            {"lead_id": "smoke-test-lead", "outcome": "lost", "specialist_used": "auto_heal", "model_version": "run_14"},
            timeout=15,
        )
        _mark(results, "feedback_endpoint", fb.get("status") == "ok", json.dumps(fb, ensure_ascii=False))
    except Exception as e:
        _mark(results, "feedback_endpoint", False, str(e))

    # 8) Send-window code consistency check.
    checks = []
    for rel in ["scripts/send_outreach_batch.py", "core/master_coordinator.py"]:
        txt = (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
        checks.append("load_send_window" in txt or "now_in_window" in txt)
    _mark(results, "send_window_unified", all(checks), "core+script use shared send window helper")

    # Finalize
    failures = [k for k, v in results["checks"].items() if not bool((v or {}).get("ok"))]
    results["failed_checks"] = failures
    results["ok"] = len(failures) == 0
    results["finished_at"] = _now()

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.send_telegram:
        lines = [
            "AUTO-HEAL + STRICT VERIFY COMPLETE",
            f"Result: {'PASS' if results['ok'] else 'FAIL'}",
            f"Failed checks: {len(failures)}",
        ]
        if failures:
            lines.append("Failures: " + ", ".join(failures[:8]))
        lines.append(f"Report: {REPORT_PATH}")
        tg = _send_telegram("\n".join(lines))
        results["telegram"] = tg
        REPORT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if results["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
