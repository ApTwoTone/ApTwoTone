#!/usr/bin/env python3
"""
Deploy Nexus Brain specialists locally on Mac now.

What this script does:
1. Optionally sync GGUF exports from an active RunPod pod.
2. Normalize GGUF filenames into ~/.nexus/brain_lab/exports/.
3. Ensure Modelfiles exist.
4. Register specialist models in Ollama.
   - Uses GGUF Modelfiles when available.
   - Falls back to local base model overlays (default qwen3:8b) when GGUF is missing.
5. Writes deployment state to ~/.nexus/brain_lab/deployment_state.json.

This allows Nexus to be operational immediately while waiting for full RunPod artifact transfer.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.brain_models import CODING_MODEL, SPECIALIST_OLLAMA_MODELS

log = logging.getLogger("deploy_brain_now")
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

NEXUS_HOME = Path.home() / ".nexus"
BRAIN_ROOT = NEXUS_HOME / "brain_lab"
EXPORTS_DIR = BRAIN_ROOT / "exports"
MODELFILES_DIR = BRAIN_ROOT / "modelfiles"
FALLBACK_MODELFILES_DIR = BRAIN_ROOT / "modelfiles_fallback"
REMOTE_SYNC_DIR = BRAIN_ROOT / "remote_sync"
DEPLOY_STATE_PATH = BRAIN_ROOT / "deployment_state.json"
RUNPOD_CURRENT = NEXUS_HOME / "runpod_current.json"
SPECIALIST_REGISTRY_PATH = NEXUS_HOME / "brain_specialists.json"
MIN_VALID_GGUF_BYTES = 5 * 1024 * 1024

MODELS: Dict[str, str] = dict(SPECIALIST_OLLAMA_MODELS)
MODELS["nexus_coder"] = CODING_MODEL

DEFAULT_FALLBACK_SYSTEMS = {
    "autonomous_entrepreneur": (
        "You are Nexus autonomous entrepreneur operator for Zoar Bathroom Rentals. "
        "Prioritize legal, compliant, low-risk actions that increase qualified leads and bookings. "
        "Always reason through planner -> operator -> critic structure."
    ),
    "nexus_coder": (
        "You are Nexus Coder. Write safe, production-quality code for the Nexus stack. "
        "Prefer parameterized SQL, robust error handling, and minimal diffs."
    ),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(cmd: List[str], *, check: bool = True, timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check, timeout=timeout)


def _load_runpod_current() -> Tuple[str, str]:
    if not RUNPOD_CURRENT.exists():
        return "", ""
    try:
        data = json.loads(RUNPOD_CURRENT.read_text(encoding="utf-8"))
        ip = str(data.get("public_ip", "")).strip()
        port = str(data.get("ssh_port", "")).strip()
        return ip, port
    except Exception:
        return "", ""


def _ensure_dirs() -> None:
    for p in (BRAIN_ROOT, EXPORTS_DIR, MODELFILES_DIR, FALLBACK_MODELFILES_DIR, REMOTE_SYNC_DIR):
        p.mkdir(parents=True, exist_ok=True)


def _sync_from_runpod(pod_ip: str, pod_port: str, run_dir: str) -> int:
    if not pod_ip or not pod_port:
        log.warning("RunPod IP/port missing; skipping sync.")
        return 0
    ssh_rsh = "ssh -p %s -o StrictHostKeyChecking=no -o ConnectTimeout=8" % pod_port
    remote_root = "root@%s:%s/" % (pod_ip, run_dir.rstrip("/"))
    REMOTE_SYNC_DIR.mkdir(parents=True, exist_ok=True)
    log.info("Syncing GGUF files from %s", remote_root)
    cmd = [
        "rsync",
        "-avz",
        "--prune-empty-dirs",
        "--include=*/",
        "--include=*.gguf",
        "--include=*.GGUF",
        "--exclude=*",
        "-e",
        ssh_rsh,
        remote_root,
        str(REMOTE_SYNC_DIR),
    ]
    try:
        _run(cmd, check=True, timeout=600)
    except subprocess.CalledProcessError as e:
        log.warning("RunPod sync failed: %s", (e.stderr or e.stdout or str(e)).strip())
        return 0
    except Exception as e:
        log.warning("RunPod sync failed: %s", e)
        return 0

    files = list(REMOTE_SYNC_DIR.rglob("*.gguf")) + list(REMOTE_SYNC_DIR.rglob("*.GGUF"))
    log.info("Synced %d GGUF artifact(s) to %s", len(files), REMOTE_SYNC_DIR)
    return len(files)


def _infer_specialist_from_path(path: Path) -> Optional[str]:
    p = path.as_posix().lower()
    for spec in MODELS.keys():
        token = spec.lower()
        if token in p:
            return spec
    # Handle hyphen variants.
    for spec in MODELS.keys():
        token = spec.lower().replace("_", "-")
        if token in p:
            return spec
    return None


def _normalize_exports() -> Dict[str, str]:
    """
    Collect any GGUFs from exports + remote_sync and normalize to:
    ~/.nexus/brain_lab/exports/<specialist>.Q4_K_M.gguf
    """
    sources = list(EXPORTS_DIR.rglob("*.gguf")) + list(EXPORTS_DIR.rglob("*.GGUF"))
    sources += list(REMOTE_SYNC_DIR.rglob("*.gguf")) + list(REMOTE_SYNC_DIR.rglob("*.GGUF"))

    best: Dict[str, Path] = {}
    for src in sources:
        try:
            if src.stat().st_size < MIN_VALID_GGUF_BYTES:
                continue
        except Exception:
            continue
        spec = _infer_specialist_from_path(src)
        if not spec:
            continue
        prev = best.get(spec)
        if prev is None or src.stat().st_size > prev.stat().st_size:
            best[spec] = src

    normalized: Dict[str, str] = {}
    for spec, src in best.items():
        target = EXPORTS_DIR / ("%s.Q4_K_M.gguf" % spec)
        if src.resolve() != target.resolve():
            shutil.copy2(src, target)
        normalized[spec] = str(target)
    return normalized


def _ensure_modelfiles() -> None:
    create_script = Path(__file__).resolve().parent / "create_ollama_modelfiles.py"
    _run([sys.executable, str(create_script)], check=True, timeout=120)


def _fallback_modelfile_for(spec: str, base_model: str) -> Path:
    # Use existing modelfile body when available, but replace FROM with base model.
    src_name = "%s.Modelfile" % spec
    src = MODELFILES_DIR / src_name
    dst = FALLBACK_MODELFILES_DIR / src_name
    if src.exists():
        text = src.read_text(encoding="utf-8")
        text = re.sub(r"(?m)^FROM\s+.+$", "FROM %s" % base_model, text, count=1)
    else:
        system = DEFAULT_FALLBACK_SYSTEMS.get(
            spec,
            "You are %s specialist for Zoar Bathroom Rentals. Be concise, practical, and safe." % spec,
        )
        text = (
            "FROM %s\n\n" % base_model
            + 'SYSTEM """%s"""\n\n' % system
            + "PARAMETER temperature 0.2\n"
            + "PARAMETER top_p 0.9\n"
            + "PARAMETER num_ctx 4096\n"
        )
    dst.write_text(text, encoding="utf-8")
    return dst


def _create_model(ollama_name: str, modelfile: Path) -> Tuple[bool, str]:
    try:
        cp = _run(
            ["ollama", "create", ollama_name, "-f", str(modelfile)],
            check=False,
            timeout=600,
        )
        if cp.returncode == 0:
            return True, ""
        err = (cp.stderr or cp.stdout or "").strip() or "ollama create failed"
        return False, err
    except Exception as e:
        return False, str(e)


def _register_models(base_model: str) -> Dict[str, Dict[str, str]]:
    state: Dict[str, Dict[str, str]] = {}
    for spec, ollama_name in MODELS.items():
        gguf = EXPORTS_DIR / ("%s.Q4_K_M.gguf" % spec)
        source_modelfile = MODELFILES_DIR / ("%s.Modelfile" % spec)
        gguf_valid = False
        try:
            gguf_valid = gguf.exists() and gguf.stat().st_size >= MIN_VALID_GGUF_BYTES
        except Exception:
            gguf_valid = False
        if gguf_valid and source_modelfile.exists():
            chosen = source_modelfile
            mode = "gguf"
        else:
            chosen = _fallback_modelfile_for(spec, base_model)
            mode = "fallback"

        ok, err = _create_model(ollama_name, chosen)
        recovered = False
        # If GGUF registration fails (corrupt/incomplete artifact), auto-fallback to local base model.
        if not ok and mode == "gguf":
            fb = _fallback_modelfile_for(spec, base_model)
            ok2, err2 = _create_model(ollama_name, fb)
            if ok2:
                ok, err = True, ""
                chosen = fb
                mode = "fallback"
                recovered = True
            else:
                err = ("%s | fallback failed: %s" % (err, err2)).strip(" |")
        state[spec] = {
            "ollama_name": ollama_name,
            "mode": mode,
            "ok": "true" if ok else "false",
            "error": err,
            "modelfile": str(chosen),
            "recovered_from_gguf_error": "true" if recovered else "false",
            "updated_at": _now(),
        }
        if ok:
            log.info("Registered %-30s -> %s (%s)", spec, ollama_name, mode)
        else:
            log.error("Failed %-34s %s", spec, err)
    return state


def _write_state(state: Dict[str, Dict[str, str]], synced_count: int, normalized: Dict[str, str], base_model: str) -> None:
    payload = {
        "updated_at": _now(),
        "synced_gguf_count": synced_count,
        "normalized_gguf": normalized,
        "fallback_base_model": base_model,
        "models": state,
    }
    DEPLOY_STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _reconcile_specialist_registry(state: Dict[str, Dict[str, str]], base_model: str) -> None:
    """
    Keep ~/.nexus/brain_specialists.json aligned with actual local Ollama deployment.
    This fixes stale deployed=false placeholders shown by /api/brain/specialists/status.
    """
    SPECIALIST_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        if SPECIALIST_REGISTRY_PATH.exists():
            registry = json.loads(SPECIALIST_REGISTRY_PATH.read_text(encoding="utf-8"))
        else:
            registry = {"updated_at": _now(), "specialists": {}}
    except Exception:
        registry = {"updated_at": _now(), "specialists": {}}

    specialists = registry.get("specialists")
    if not isinstance(specialists, dict):
        specialists = {}
        registry["specialists"] = specialists

    for spec, ollama_name in SPECIALIST_OLLAMA_MODELS.items():
        model_info = state.get(spec, {})
        ok = str(model_info.get("ok", "")).lower() == "true"
        mode = str(model_info.get("mode", "")).strip() or ("gguf" if ok else "fallback")
        is_trained = ok and mode == "gguf"
        version = "run_14_gguf" if is_trained else ("fallback_%s" % base_model.replace(":", "_"))
        specialists[spec] = {
            "name": spec,
            "endpoint": "ollama://%s" % ollama_name,
            "version": version,
            "deployed": bool(is_trained),
            "runtime_available": bool(ok),
            "updated_at": _now(),
        }

    registry["updated_at"] = _now()
    SPECIALIST_REGISTRY_PATH.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("Reconciled specialist registry: %s", SPECIALIST_REGISTRY_PATH)


def _fetch_brain_status() -> Dict[str, object]:
    import urllib.request

    req = urllib.request.Request("http://localhost:7860/api/brain/status", method="GET")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy Nexus brain specialists locally right now.")
    parser.add_argument("--pod-ip", default="", help="RunPod public IP (optional).")
    parser.add_argument("--pod-port", default="", help="RunPod SSH port (optional).")
    parser.add_argument("--run-dir", default="/workspace/nexus_brain_lab/run_14", help="Remote run dir on pod.")
    parser.add_argument("--skip-sync", action="store_true", help="Skip remote rsync.")
    parser.add_argument("--base-model", default="qwen3:8b", help="Fallback local base model for overlays.")
    parser.add_argument(
        "--require-trained",
        action="store_true",
        help="Fail if any specialist is not in trained GGUF mode.",
    )
    args = parser.parse_args()

    _ensure_dirs()
    _ensure_modelfiles()

    pod_ip = args.pod_ip.strip()
    pod_port = args.pod_port.strip()
    if (not pod_ip or not pod_port) and not args.skip_sync:
        auto_ip, auto_port = _load_runpod_current()
        pod_ip = pod_ip or auto_ip
        pod_port = pod_port or auto_port

    synced_count = 0
    if not args.skip_sync:
        synced_count = _sync_from_runpod(pod_ip, pod_port, args.run_dir)

    normalized = _normalize_exports()
    log.info("Normalized GGUF files: %d", len(normalized))

    state = _register_models(args.base_model)
    _write_state(state, synced_count, normalized, args.base_model)
    _reconcile_specialist_registry(state, args.base_model)
    log.info("Wrote deployment state: %s", DEPLOY_STATE_PATH)

    try:
        status = _fetch_brain_status()
        print(json.dumps(status, ensure_ascii=False, indent=2))
    except Exception as e:
        log.warning("Could not fetch /api/brain/status: %s", e)

    if args.require_trained:
        failures = [k for k, v in state.items() if v.get("ok") != "true" or v.get("mode") != "gguf"]
    else:
        failures = [k for k, v in state.items() if v.get("ok") != "true"]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
