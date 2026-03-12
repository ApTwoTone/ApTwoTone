from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

# Canonical Ollama model names used by Nexus specialists.
SPECIALIST_OLLAMA_MODELS: Dict[str, str] = {
    "lead_ranker": "nexus-lead-ranker",
    "venue_partner_ranker": "nexus-venue-ranker",
    "outreach_angle_selector": "nexus-outreach-angle",
    "conversion_specialist": "nexus-conversion",
    "objection_resolver": "nexus-objections",
    "fb_media_buyer": "nexus-fb-buyer",
    "venue_partnership_closer": "nexus-venue-closer",
    "follow_up_cadence_specialist": "nexus-followup",
    "quote_personalizer": "nexus-quote",
    "ad_creative_specialist": "nexus-ad-creative",
    "vendor_referral_specialist": "nexus-vendor-referral",
    "seasonal_demand_predictor": "nexus-seasonal",
    "autonomous_entrepreneur": "nexus-autonomous",
}

CODING_MODEL = "nexus-coder"
DEPLOYMENT_STATE_PATH = Path.home() / ".nexus" / "brain_lab" / "deployment_state.json"
MIN_VALID_GGUF_BYTES = 5 * 1024 * 1024

# Backward compatibility aliases that previously appeared in code.
_MODEL_ALIASES: Dict[str, str] = {
    "nexus-lead_ranker": "nexus-lead-ranker",
    "nexus-lead-ranker": "nexus-lead-ranker",
}


def normalize_ollama_model_name(name: str) -> str:
    raw = str(name or "").strip()
    if not raw:
        return ""
    base = raw.split(":")[0]
    return _MODEL_ALIASES.get(base, base)


def canonical_model_for_specialist(specialist: str) -> str:
    return SPECIALIST_OLLAMA_MODELS.get(str(specialist or "").strip(), "")


def model_aliases_for_specialist(specialist: str) -> Set[str]:
    s = str(specialist or "").strip()
    aliases: Set[str] = set()
    canonical = canonical_model_for_specialist(s)
    if canonical:
        aliases.add(canonical)
    if s:
        aliases.add("nexus-%s" % s)
        aliases.add("nexus-%s" % s.replace("_", "-"))
    # Include explicit alias keys that map to this specialist's canonical model.
    if canonical:
        for alias, target in _MODEL_ALIASES.items():
            if target == canonical:
                aliases.add(alias)
    return aliases


def canonicalize_requested_model(name: str) -> str:
    raw = normalize_ollama_model_name(name)
    if raw:
        return raw
    return CODING_MODEL


def specialist_online(specialist: str, installed_models: Iterable[str]) -> bool:
    installed = {normalize_ollama_model_name(m) for m in installed_models if m}
    aliases = {normalize_ollama_model_name(m) for m in model_aliases_for_specialist(specialist)}
    return bool(installed.intersection(aliases))


def coding_online(installed_models: Iterable[str]) -> bool:
    installed = {normalize_ollama_model_name(m) for m in installed_models if m}
    return normalize_ollama_model_name(CODING_MODEL) in installed


def installed_specialist_model(specialist: str, installed_models: Iterable[str]) -> Optional[str]:
    installed = {normalize_ollama_model_name(m) for m in installed_models if m}
    aliases = [normalize_ollama_model_name(a) for a in model_aliases_for_specialist(specialist)]
    for alias in aliases:
        if alias in installed:
            return alias
    return None


def specialist_names() -> List[str]:
    return list(SPECIALIST_OLLAMA_MODELS.keys())


def load_deployment_state() -> Dict[str, Any]:
    if not DEPLOYMENT_STATE_PATH.exists():
        return {}
    try:
        data = json.loads(DEPLOYMENT_STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def specialist_deployment_record(specialist: str) -> Dict[str, Any]:
    state = load_deployment_state()
    models = state.get("models")
    if isinstance(models, dict):
        rec = models.get(str(specialist or "").strip())
        if isinstance(rec, dict):
            return rec
    return {}


def _is_truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    txt = str(v if v is not None else "").strip().lower()
    return txt in {"1", "true", "yes", "on", "ok"}


def specialist_mode(specialist: str) -> str:
    rec = specialist_deployment_record(specialist)
    mode = str(rec.get("mode", "")).strip().lower()
    return mode or "unknown"


def specialist_has_valid_gguf(specialist: str) -> bool:
    state = load_deployment_state()
    normalized = state.get("normalized_gguf")
    if not isinstance(normalized, dict):
        return False
    path_raw = normalized.get(str(specialist or "").strip())
    if not path_raw:
        return False
    try:
        p = Path(str(path_raw)).expanduser().resolve()
        return p.exists() and p.is_file() and p.stat().st_size >= MIN_VALID_GGUF_BYTES
    except Exception:
        return False


def specialist_trained_ready(specialist: str) -> bool:
    rec = specialist_deployment_record(specialist)
    if not _is_truthy(rec.get("ok", False)):
        return False
    mode = specialist_mode(specialist)
    if mode != "gguf":
        return False
    return specialist_has_valid_gguf(specialist)


def specialist_runtime_available(specialist: str, installed_models: Iterable[str]) -> bool:
    return specialist_online(specialist, installed_models)
