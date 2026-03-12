from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

CONFIG_PATH = Path.home() / ".nexus" / "config.json"

SERVICE_DEFINITIONS: Dict[str, Dict[str, object]] = {
    "google_voice": {
        "label": "Google Voice automation",
        "process_name": "sms_control_daemon",
        "managed_disable_key": "disable_sms_control_daemon",
        "blocking_disable_keys": [],
        "summary": "Controls the dedicated Google Voice browser daemon used for SMS control and alert calls.",
    },
    "facebook_lead_hunter": {
        "label": "Facebook lead hunter",
        "process_name": "facebook_scraper",
        "managed_disable_key": "disable_facebook_scraper",
        "blocking_disable_keys": ["disable_browser_scrapers"],
        "summary": "Controls the long-running Facebook group scraper for referral leads and event opportunities.",
    },
}

PROCESS_TO_SERVICE = {
    str(meta["process_name"]): service_id
    for service_id, meta in SERVICE_DEFINITIONS.items()
}

SERVICE_ALIASES = {
    "google_voice": "google_voice",
    "google-voice": "google_voice",
    "googlevoice": "google_voice",
    "gv": "google_voice",
    "sms_control_daemon": "google_voice",
    "facebook_lead_hunter": "facebook_lead_hunter",
    "facebook-lead-hunter": "facebook_lead_hunter",
    "facebook": "facebook_lead_hunter",
    "fb": "facebook_lead_hunter",
    "facebook_scraper": "facebook_lead_hunter",
}


class ServiceControlError(RuntimeError):
    def __init__(self, message: str, *, blocked_by: List[str] | None = None):
        super().__init__(message)
        self.blocked_by = list(blocked_by or [])


def _is_truthy(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return default
    return text not in {"0", "false", "no", "off", "disabled"}


def load_service_config() -> Dict[str, object]:
    try:
        if CONFIG_PATH.exists():
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
    except Exception:
        pass
    return {}


def save_service_config(cfg: Dict[str, object]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def resolve_service_id(service_id: str) -> str:
    key = str(service_id or "").strip().lower()
    resolved = SERVICE_ALIASES.get(key, key)
    if resolved not in SERVICE_DEFINITIONS:
        raise KeyError(service_id)
    return resolved


def get_service_definition(service_id: str) -> Dict[str, object]:
    resolved = resolve_service_id(service_id)
    return dict(SERVICE_DEFINITIONS[resolved], id=resolved)


def get_service_for_process(process_name: str) -> str | None:
    return PROCESS_TO_SERVICE.get(str(process_name or "").strip())


def get_service_state(service_id: str, cfg: Dict[str, object] | None = None) -> Dict[str, object]:
    resolved = resolve_service_id(service_id)
    meta = SERVICE_DEFINITIONS[resolved]
    cfg = load_service_config() if cfg is None else dict(cfg)
    managed_key = str(meta["managed_disable_key"])
    blocking_keys = [str(key) for key in meta.get("blocking_disable_keys", [])]
    blocked_by = []
    if _is_truthy(cfg.get(managed_key), False):
        blocked_by.append(managed_key)
    for key in blocking_keys:
        if _is_truthy(cfg.get(key), False):
            blocked_by.append(key)
    return {
        "id": resolved,
        "label": str(meta["label"]),
        "summary": str(meta.get("summary", "")),
        "process_name": str(meta["process_name"]),
        "managed_disable_key": managed_key,
        "blocking_disable_keys": blocking_keys,
        "blocked_by": blocked_by,
        "enabled": not blocked_by,
    }


def list_service_states(cfg: Dict[str, object] | None = None) -> List[Dict[str, object]]:
    loaded_cfg = load_service_config() if cfg is None else dict(cfg)
    return [
        get_service_state(service_id, loaded_cfg)
        for service_id in SERVICE_DEFINITIONS
    ]


def is_process_disabled(process_name: str, cfg: Dict[str, object] | None = None) -> bool:
    service_id = get_service_for_process(process_name)
    if not service_id:
        return False
    return not bool(get_service_state(service_id, cfg).get("enabled"))


def set_service_enabled(
    service_id: str,
    enabled: bool,
    *,
    clear_blockers: bool = False,
    cfg: Dict[str, object] | None = None,
) -> Dict[str, object]:
    resolved = resolve_service_id(service_id)
    current_cfg = load_service_config() if cfg is None else dict(cfg)
    current_state = get_service_state(resolved, current_cfg)
    managed_key = str(current_state["managed_disable_key"])
    blockers = [
        key for key in current_state["blocked_by"]
        if key != managed_key
    ]

    if enabled:
        if blockers and not clear_blockers:
            raise ServiceControlError(
                f"{current_state['label']} is blocked by {', '.join(blockers)}",
                blocked_by=blockers,
            )
        current_cfg[managed_key] = False
        for key in blockers:
            current_cfg[key] = False
    else:
        current_cfg[managed_key] = True

    save_service_config(current_cfg)
    return get_service_state(resolved, current_cfg)
