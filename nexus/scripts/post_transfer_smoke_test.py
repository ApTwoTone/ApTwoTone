"""
Post-Transfer Smoke Test for Nexus Brain Specialists.

Run this AFTER GGUF files have been rsync'd from RunPod into:
    ~/.nexus/brain_lab/exports/

What this script does:
  1. Reads Modelfiles from ~/.nexus/brain_lab/modelfiles/
  2. Runs `ollama create` for each specialist whose GGUF exists
  3. Sends a test prompt to each created model via POST /api/brain/query
  4. Prints a pass/fail table
  5. Sends a Telegram summary to Kai

Usage:
    python scripts/post_transfer_smoke_test.py

Flags:
    --skip-create      Skip `ollama create` (models already registered)
    --force            Re-create models even if they already exist in Ollama
    --allow-fallback   Do not fail when specialist is only available in fallback mode
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("smoke_test")
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

EXPORTS_DIR = Path.home() / ".nexus" / "brain_lab" / "exports"
MODELFILES_DIR = Path.home() / ".nexus" / "brain_lab" / "modelfiles"
DEPLOYMENT_STATE = Path.home() / ".nexus" / "brain_lab" / "deployment_state.json"
NEXUS_BASE = "http://localhost:7860"
TELEGRAM_CHAT_ID = "8540603351"
MIN_VALID_GGUF_BYTES = 5 * 1024 * 1024

# Simple test prompts per specialist — expected response type
TEST_PROMPTS = {
    "lead_ranker":                 ("Rate this lead: outdoor wedding, 120 guests, Woodland Hills CA, 3 weeks out, has phone", "number"),
    "conversion_specialist":       ("Write a quote response for Maria, backyard quinceañera, June 14, Reseda CA, 150 guests", "text"),
    "objection_resolver":          ("Lead says: 'I think it might be too expensive for us'", "text"),
    "fb_media_buyer":              ("Analyze: CTR 1.2%, CPL $9.40, reach 4200, spend $47. Recommend action.", "text"),
    "venue_partnership_closer":    ("Draft cold outreach to Hacienda de la Flores event venue, Sylmar CA", "text"),
    "follow_up_cadence_specialist": ("Lead submitted form 52 hours ago, no response. What do we send?", "text"),
    "quote_personalizer":          ("Generate quote: James Rodriguez, corporate event, Aug 22, Burbank CA, 200 guests", "text"),
    "ad_creative_specialist":      ("Write primary text for a Facebook ad targeting quinceañera families in SFV", "text"),
    "vendor_referral_specialist":  ("Draft outreach to Carlos's Catering, San Fernando CA, serves 300-500 guest events", "text"),
    "seasonal_demand_predictor":   ("Predict demand for next 8 weeks. Today is March 9, 2026.", "text"),
    "venue_partner_ranker":        ("Rate venue: Sunflower Gardens, North Hollywood CA, outdoor, no restrooms, seats 200", "number"),
    "outreach_angle_selector":     ("Lead: wedding planner, event in 5 weeks, haven't replied to quote. Best angle?", "text"),
    "nexus_coder":                 ("In one sentence, what does the outbound_gate() function in integrations/messaging.py do?", "text"),
}

# Map specialist name → ollama model name (must match create_ollama_modelfiles.py)
OLLAMA_NAMES = {
    "lead_ranker":                  "nexus-lead-ranker",
    "conversion_specialist":        "nexus-conversion",
    "objection_resolver":           "nexus-objections",
    "fb_media_buyer":               "nexus-fb-buyer",
    "venue_partnership_closer":     "nexus-venue-closer",
    "follow_up_cadence_specialist": "nexus-followup",
    "quote_personalizer":           "nexus-quote",
    "ad_creative_specialist":       "nexus-ad-creative",
    "vendor_referral_specialist":   "nexus-vendor-referral",
    "seasonal_demand_predictor":    "nexus-seasonal",
    "venue_partner_ranker":         "nexus-venue-ranker",
    "outreach_angle_selector":      "nexus-outreach-angle",
    "nexus_coder":                  "nexus-coder",
}


def _ollama_list() -> list:
    """Return list of model names currently registered in Ollama."""
    try:
        result = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=10
        )
        names = []
        for line in result.stdout.splitlines()[1:]:  # skip header
            parts = line.split()
            if parts:
                names.append(parts[0].split(":")[0])  # strip :latest tag
        return names
    except Exception as e:
        log.warning("Could not list Ollama models: %s", e)
        return []


def _ollama_create(specialist: str, force: bool = False) -> Optional[str]:
    """
    Run `ollama create` for a specialist. Returns error string or None on success.
    Skips if model already registered (unless force=True).
    """
    gguf_path = EXPORTS_DIR / f"{specialist}.Q4_K_M.gguf"
    modelfile_path = MODELFILES_DIR / f"{specialist}.Modelfile"
    ollama_name = OLLAMA_NAMES[specialist]

    if not gguf_path.exists():
        return f"GGUF not found: {gguf_path.name}"
    if gguf_path.stat().st_size < MIN_VALID_GGUF_BYTES:
        return f"GGUF invalid (too small): {gguf_path.name} ({gguf_path.stat().st_size} bytes)"

    if not modelfile_path.exists():
        return f"Modelfile not found — run create_ollama_modelfiles.py first"

    existing = _ollama_list()
    if ollama_name in existing and not force:
        log.info("  skip create  %s  (already registered in Ollama)", ollama_name)
        return None

    log.info("  ollama create %s ...", ollama_name)
    result = subprocess.run(
        ["ollama", "create", ollama_name, "-f", str(modelfile_path)],
        capture_output=True, text=True, timeout=300
    )
    if result.returncode != 0:
        return result.stderr.strip() or "ollama create failed"
    return None


def _send_test_prompt(specialist: str) -> tuple:
    """
    POST a test prompt to /api/brain/query.
    Returns (passed: bool, latency_ms: int, response_preview: str).
    """
    import urllib.request
    import urllib.error

    ollama_name = OLLAMA_NAMES[specialist]
    prompt, _ = TEST_PROMPTS[specialist]

    payload = json.dumps({
        "model": ollama_name,
        "prompt": prompt,
    }).encode()

    start = time.time()
    try:
        req = urllib.request.Request(
            f"{NEXUS_BASE}/api/brain/query",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read())
            latency_ms = int((time.time() - start) * 1000)
            response = data.get("response", "").strip()
            passed = bool(response) and data.get("status") == "ok"
            preview = response[:80] + ("..." if len(response) > 80 else "")
            return passed, latency_ms, preview
    except Exception as e:
        latency_ms = int((time.time() - start) * 1000)
        return False, latency_ms, str(e)[:80]


def _send_telegram(message: str) -> None:
    """Send a plain-text Telegram message to Kai."""
    import urllib.request
    import urllib.parse

    config_path = Path.home() / ".nexus" / "config.json"
    try:
        config = json.loads(config_path.read_text())
        token = config.get("telegram_bot_token", "")
    except Exception:
        log.warning("Could not load Telegram token from config.json")
        return

    if not token:
        log.warning("telegram_bot_token not set in config — skipping Telegram notification")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
    }).encode()
    try:
        urllib.request.urlopen(url, data=data, timeout=10)
    except Exception as e:
        log.warning("Telegram send failed: %s", e)


def _deployment_modes() -> dict:
    if not DEPLOYMENT_STATE.exists():
        return {}
    try:
        payload = json.loads(DEPLOYMENT_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    models = payload.get("models")
    return models if isinstance(models, dict) else {}


def main(skip_create: bool = False, force: bool = False, allow_fallback: bool = False) -> None:
    log.info("Nexus Brain Post-Transfer Smoke Test")
    log.info("=" * 50)

    specialists = list(OLLAMA_NAMES.keys())
    results = {}
    create_errors = {}

    # ── Phase 1: Create models ─────────────────────────────────────────────
    if not skip_create:
        log.info("")
        log.info("Phase 1: Registering models with Ollama")
        for name in specialists:
            gguf = EXPORTS_DIR / f"{name}.Q4_K_M.gguf"
            if not gguf.exists():
                log.info("  skip  %-40s (GGUF not transferred yet)", name)
                create_errors[name] = "GGUF not transferred yet"
                continue
            err = _ollama_create(name, force=force)
            if err:
                create_errors[name] = err
                log.error("  FAIL  %-40s %s", name, err)

    # ── Phase 2: Test prompts ──────────────────────────────────────────────
    log.info("")
    log.info("Phase 2: Sending test prompts via /api/brain/query")

    passed_count = 0
    failed_count = 0
    skipped_count = 0
    rows = []

    for name in specialists:
        if name in create_errors:
            skipped_count += 1
            rows.append((name, "SKIP", 0, create_errors[name]))
            continue

        passed, latency_ms, preview = _send_test_prompt(name)
        if passed:
            passed_count += 1
            rows.append((name, "PASS", latency_ms, preview))
            log.info("  PASS  %-40s %dms  %s", name, latency_ms, preview[:40])
        else:
            failed_count += 1
            rows.append((name, "FAIL", latency_ms, preview))
            log.error("  FAIL  %-40s %dms  %s", name, latency_ms, preview[:40])

    # Strict mode: fail if runtime is only fallback overlays.
    deployment = _deployment_modes()
    fallback_only = []
    for name in specialists:
        rec = deployment.get(name, {}) if isinstance(deployment, dict) else {}
        mode = str(rec.get("mode", "")).strip().lower()
        ok = str(rec.get("ok", "")).strip().lower() == "true"
        if ok and mode and mode != "gguf":
            fallback_only.append(name)
    if fallback_only and not allow_fallback:
        for name in fallback_only:
            failed_count += 1
            rows.append((name, "FAIL", 0, "fallback_only_runtime"))
        log.error("  FAIL strict: fallback-only specialists detected: %s", ", ".join(fallback_only))
    elif fallback_only:
        log.warning("  WARN strict disabled: fallback-only specialists: %s", ", ".join(fallback_only))

    # ── Phase 3: Summary ───────────────────────────────────────────────────
    log.info("")
    log.info("=" * 50)
    log.info("RESULTS: %d passed  %d failed  %d skipped", passed_count, failed_count, skipped_count)
    log.info("=" * 50)

    # ── Phase 4: Telegram alert ────────────────────────────────────────────
    status_icon = "✓" if failed_count == 0 else "✗"
    summary_lines = [
        f"{status_icon} Nexus Brain Transfer Smoke Test",
        f"Passed: {passed_count} | Failed: {failed_count} | Skipped: {skipped_count}",
    ]
    if failed_count > 0:
        failed_names = [r[0] for r in rows if r[1] == "FAIL"]
        summary_lines.append(f"Failed: {', '.join(failed_names)}")
    if skipped_count > 0:
        skipped_names = [r[0] for r in rows if r[1] == "SKIP"]
        summary_lines.append(f"Skipped (GGUF not yet transferred): {', '.join(skipped_names)}")
    summary_lines.append("Action: " + ("All models online with trained GGUFs."
                          if failed_count == 0 and skipped_count == 0
                          else "Check logs at ~/.nexus/ for details."))

    message = "\n".join(summary_lines)
    log.info("")
    log.info("Sending Telegram summary to Kai...")
    _send_telegram(message)

    sys.exit(1 if failed_count > 0 else 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Post-transfer smoke test for Nexus Brain")
    parser.add_argument("--skip-create", action="store_true",
                        help="Skip `ollama create` (models already registered)")
    parser.add_argument("--force", action="store_true",
                        help="Re-create models even if already in Ollama")
    parser.add_argument("--allow-fallback", action="store_true",
                        help="Allow fallback-only specialist runtime without failing")
    args = parser.parse_args()
    main(skip_create=args.skip_create, force=args.force, allow_fallback=args.allow_fallback)
