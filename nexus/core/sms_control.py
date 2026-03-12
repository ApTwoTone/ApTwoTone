from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import smtplib
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import base64
from email.message import EmailMessage
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("sms_control")

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"

DEFAULT_ALLOWED_NUMBER = os.environ.get("KAI_PERSONAL_PHONE", "8184489055")
DEFAULT_SELF_EMAIL = os.environ.get("KAI_PERSONAL_EMAIL", "kaiescobar09@gmail.com")
SMS_CREATED_BY_PREFIX = "sms:"
INBOUND_DEDUP_MINUTES = 10
MAX_STATUS_TASKS = 5
LOCAL_OPEN_TIMEOUT_SECONDS = 15
LOCAL_EMAIL_TIMEOUT_SECONDS = 20
LOCAL_CALL_TIMEOUT_SECONDS = 20
ZOAR_WEBSITE_URL = "https://zoarbathroomrental.com"
BRAIN_IDLE_STABLE_SECONDS = 120
BRAIN_IDLE_PROMPT_COOLDOWN_MINUTES = 30
MAX_BRAIN_TASK_SCAN = 200
DEFAULT_SELF_EMAIL_SUBJECT = "SMS control update"
DEFAULT_CALL_MESSAGE = "This is Nexus on your Mac mini. Your requested call test worked."
VOICE_CONTROL_PATH_PREFIX = "/voice"
VOICE_OPERATOR_SCAN_LIMIT = 8
VOICE_AGENT_SCAN_LIMIT = 100
ZOAR_DASHBOARD_PATH = "/dashboard/"
PID_DIR = Path.home() / ".nexus" / "pids"
LOG_DIR = Path.home() / ".nexus" / "process_logs"
MAX_OPERATOR_EVENTS = 20
MAX_PENDING_APPROVALS = 5
MAX_VOICE_ALERTS = 2
OWNER_CODE_TASK_COMPLEXITY = 4
ROOT_DIR = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT_DIR / "venv" / "bin" / "python3"

_BRAIN_CONTROL_PLANE = None


def _utc_now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _parse_timestamp(value: str) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        pass
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _timestamp_sort_key(value: str) -> float:
    parsed = _parse_timestamp(value)
    return parsed.timestamp() if parsed else 0.0


def _get_brain_control_plane():
    global _BRAIN_CONTROL_PLANE
    if _BRAIN_CONTROL_PLANE is None:
        from core.nexus_brain_control_plane import NexusBrainControlPlane

        _BRAIN_CONTROL_PLANE = NexusBrainControlPlane()
    return _BRAIN_CONTROL_PLANE


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sms_control_inbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT DEFAULT '',
            body TEXT DEFAULT '',
            body_norm TEXT DEFAULT '',
            source_name TEXT DEFAULT '',
            task_id INTEGER DEFAULT 0,
            received_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sms_control_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT DEFAULT '',
            task_id INTEGER DEFAULT 0,
            task_status TEXT DEFAULT '',
            sent_at TEXT DEFAULT (datetime('now')),
            UNIQUE(phone, task_id, task_status)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sms_control_brain_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT DEFAULT '',
            brain_task_id INTEGER DEFAULT 0,
            task_status TEXT DEFAULT '',
            sent_at TEXT DEFAULT (datetime('now')),
            UNIQUE(phone, brain_task_id, task_status)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sms_control_orchestrator_state (
            phone TEXT PRIMARY KEY,
            awaiting_reply INTEGER DEFAULT 0,
            active_prompt_kind TEXT DEFAULT '',
            last_prompt_sent_at TEXT DEFAULT '',
            brain_idle_since TEXT DEFAULT '',
            last_reply_at TEXT DEFAULT '',
            last_reply_body TEXT DEFAULT '',
            last_brain_task_id INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sms_control_operator_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT DEFAULT '',
            event_key TEXT DEFAULT '',
            source_kind TEXT DEFAULT '',
            source_id INTEGER DEFAULT 0,
            status TEXT DEFAULT '',
            severity TEXT DEFAULT 'info',
            title TEXT DEFAULT '',
            detail TEXT DEFAULT '',
            proof TEXT DEFAULT '',
            recommendations_json TEXT DEFAULT '[]',
            requires_response INTEGER DEFAULT 0,
            response_kind TEXT DEFAULT '',
            response_target TEXT DEFAULT '',
            notified_sms INTEGER DEFAULT 0,
            notified_voice INTEGER DEFAULT 0,
            resolved_at TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(phone, event_key)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sms_control_owner_approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT DEFAULT '',
            channel TEXT DEFAULT '',
            target_id INTEGER DEFAULT 0,
            target_ref TEXT DEFAULT '',
            request_kind TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            summary TEXT DEFAULT '',
            detail TEXT DEFAULT '',
            approval_reason TEXT DEFAULT '',
            requested_at TEXT DEFAULT (datetime('now')),
            decided_at TEXT DEFAULT '',
            decision_note TEXT DEFAULT '',
            UNIQUE(phone, channel, target_id, status)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sms_control_voice_call_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT DEFAULT '',
            message TEXT DEFAULT '',
            provider TEXT DEFAULT 'google_voice',
            approval_id INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            requested_at TEXT DEFAULT (datetime('now')),
            started_at TEXT DEFAULT '',
            completed_at TEXT DEFAULT '',
            result_note TEXT DEFAULT ''
        )
        """
    )
    return conn


def _load_config() -> Dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def normalize_phone(phone: str) -> str:
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def normalize_body(body: str) -> str:
    text = str(body or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def load_allowed_numbers() -> List[str]:
    cfg = _load_config()
    raw = cfg.get("sms_control_allowed_numbers") or cfg.get("kai_control_numbers") or []
    if isinstance(raw, str):
        raw = [raw]
    allowed = [normalize_phone(v) for v in raw if normalize_phone(v)]
    if allowed:
        return sorted(set(allowed))
    fallback = normalize_phone(DEFAULT_ALLOWED_NUMBER)
    return [fallback] if fallback else []


def load_self_email() -> str:
    cfg = _load_config()
    for key in ("kai_email", "owner_email", "personal_email", "notification_email"):
        value = str(cfg.get(key) or "").strip()
        if value and "@" in value:
            return value
    return DEFAULT_SELF_EMAIL


def load_voice_allowed_numbers() -> List[str]:
    cfg = _load_config()
    raw = (
        cfg.get("voice_control_allowed_numbers")
        or cfg.get("voice_call_allowed_numbers")
        or []
    )
    if isinstance(raw, str):
        raw = [raw]
    allowed = [normalize_phone(v) for v in raw if normalize_phone(v)]
    if allowed:
        return sorted(set(allowed))
    fallback = normalize_phone(DEFAULT_ALLOWED_NUMBER)
    return [fallback] if fallback else []


def is_authorized_voice_phone(phone: str) -> bool:
    normalized = normalize_phone(phone)
    return bool(normalized) and normalized in set(load_voice_allowed_numbers())


def _is_twilio_voice_configured(cfg: Optional[Dict[str, Any]] = None) -> bool:
    cfg = cfg or _load_config()
    return bool(
        str(cfg.get("twilio_sid") or "").strip()
        and str(cfg.get("twilio_token") or "").strip()
        and str(cfg.get("twilio_from") or "").strip()
    )


def is_google_voice_call_configured(max_age_seconds: int = 86400) -> bool:
    try:
        from integrations.google_voice import is_google_voice_runtime_ready

        return bool(is_google_voice_runtime_ready(max_age_seconds=max_age_seconds))
    except Exception:
        return False


def is_voice_call_configured() -> bool:
    cfg = _load_config()
    return _is_twilio_voice_configured(cfg) or is_google_voice_call_configured()


def load_voice_public_base_url() -> str:
    cfg = _load_config()
    explicit = str(cfg.get("voice_control_public_base_url") or cfg.get("voice_public_base_url") or "").strip()
    if explicit:
        return explicit.rstrip("/")

    shared = load_shared_public_base_url()
    if shared:
        return shared.rstrip("/") + VOICE_CONTROL_PATH_PREFIX
    return ""


def load_shared_public_base_url() -> str:
    cfg = _load_config()
    shared = str(cfg.get("fb_webhook_domain") or cfg.get("public_base_url") or "").strip()
    if shared:
        return shared.rstrip("/")
    return "https://crm.zoarbathroomrental.com"


def load_local_voice_port() -> int:
    cfg = _load_config()
    raw = cfg.get("voice_control_port") or os.environ.get("VOICE_CONTROL_PORT") or 8790
    try:
        return max(1024, min(int(raw), 65535))
    except Exception:
        return 8790


def is_authorized_phone(phone: str) -> bool:
    normalized = normalize_phone(phone)
    return bool(normalized) and normalized in set(load_allowed_numbers())


def control_identity(phone: str) -> str:
    return f"{SMS_CREATED_BY_PREFIX}{normalize_phone(phone)}"


def classify_task(text: str) -> Tuple[str, int]:
    lower = normalize_body(text)
    if any(token in lower for token in ("test", "verify", "audit", "check", "debug")):
        return "test", 3
    if any(token in lower for token in ("research", "investigate", "analyze", "compare", "look up")):
        return "research", 3
    if any(token in lower for token in ("write", "draft", "copy", "post", "content")):
        return "content", 3
    if any(token in lower for token in ("refactor", "cleanup")):
        return "refactor", 3
    if any(token in lower for token in ("bug", "fix", "patch", "broken", "error")):
        return "bugfix", 4
    return "code", 4


def classify_brain_task(text: str) -> Dict[str, Any]:
    lower = normalize_body(_expand_owner_shorthand(text))
    if any(token in lower for token in ("codex", "code edit", "edit code", "patch the repo", "change the code")):
        return {"task_type": "diagnostics", "lane": "systems", "priority": 9}
    if any(token in lower for token in ("fix", "bug", "error", "debug", "broken", "code", "script", "automation", "integration", "website", "refactor")):
        return {"task_type": "diagnostics", "lane": "systems", "priority": 12}
    if any(token in lower for token in ("ad ", " ads", "campaign", "creative", "hook", "cpl", "ctr", "meta ad", "facebook ad", "instagram ad")):
        if any(token in lower for token in ("creative", "hook", "script", "angle", "copy")):
            return {"task_type": "creative_scoring", "lane": "ads_intel", "priority": 16}
        return {"task_type": "ad_diagnostics", "lane": "ads_intel", "priority": 14}
    if any(token in lower for token in ("venue", "vendor", "partner", "referral")):
        return {"task_type": "venue_fit_scoring", "lane": "lead_ops", "priority": 15}
    if any(token in lower for token in ("prospect", "prospects", "business", "businesses", "company", "companies", "account", "accounts")):
        if any(token in lower for token in LEAD_ACTION_TOKENS + LEAD_REQUEST_VERBS):
            return {"task_type": "lead_scoring", "lane": "lead_ops", "priority": 14}
    if any(token in lower for token in ("lead", "reply", "follow up", "follow-up", "quote", "booking", "outreach")):
        if any(token in lower for token in ("reply", "follow up", "follow-up", "outreach", "quote")):
            return {"task_type": "outreach_angle_selection", "lane": "lead_ops", "priority": 13}
        return {"task_type": "lead_scoring", "lane": "lead_ops", "priority": 14}
    if any(token in lower for token in ("experiment", "hypothesis", "ab test", "a/b test", "test variation")):
        return {"task_type": "experiment_design", "lane": "experiments", "priority": 18}
    return {"task_type": "human_request", "lane": "core_ops", "priority": 10}


FILE_HINT_MAP: List[Tuple[Tuple[str, ...], List[str]]] = [
    (
        ("facebook ad", "facebook ads", "ad copy", "ad headline", "campaign", "meta ad", "instagram ad"),
        [
            "scripts/create_fb_campaign.py",
            "scripts/fix_ad_copy.py",
            "scripts/fix_ad_copy_phase2.py",
            "scripts/create_lead_gen_campaign.py",
        ],
    ),
    (
        ("voice", "operator", "call flow"),
        [
            "core/voice_control.py",
            "scripts/voice_control_server.py",
            "tests/test_voice_control.py",
        ],
    ),
    (
        ("sms", "text control", "owner command"),
        [
            "core/sms_control.py",
            "scripts/sms_control_daemon.py",
            "tests/test_sms_control.py",
        ],
    ),
    (
        ("lead", "leads", "vendor", "vendors", "venue", "venues"),
        [
            "scripts/lead_discovery_daemon.py",
            "core/lead_pipeline.py",
            "core/vendor_research.py",
        ],
    ),
    (
        ("website", "landing page", "homepage", "hero section"),
        [
            "website/index.html",
            "website/js/main.js",
            "static/js/app.js",
        ],
    ),
]

OWNER_GEO_ALIASES: Dict[str, str] = {
    "sfv": "San Fernando Valley",
    "the sfv": "the San Fernando Valley",
    "s f v": "San Fernando Valley",
    "ie": "Inland Empire",
    "the ie": "the Inland Empire",
}

LEAD_REQUEST_VERBS: Tuple[str, ...] = (
    "find",
    "research",
    "list",
    "get",
    "prospect",
    "show me",
    "pull",
    "source",
    "look for",
    "build",
)

LEAD_ENTITY_TOKENS: Tuple[str, ...] = (
    "lead",
    "leads",
    "prospect",
    "prospects",
    "account",
    "accounts",
    "business",
    "businesses",
    "company",
    "companies",
    "venue",
    "venues",
    "vendor",
    "vendors",
    "planner",
    "planners",
    "church",
    "churches",
    "restaurant",
    "restaurants",
    "event space",
    "event spaces",
    "event venue",
    "event venues",
    "wedding venue",
    "wedding venues",
)

LEAD_ACTION_TOKENS: Tuple[str, ...] = (
    "cold call",
    "call",
    "pitch",
    "outreach",
    "follow up",
    "follow-up",
    "quote",
    "bookings",
)

LEAD_LOCATION_TOKENS: Tuple[str, ...] = (
    "sfv",
    "san fernando valley",
    "encino",
    "ventura",
    "burbank",
    "pasadena",
    "glendale",
    "sherman oaks",
    "studio city",
    "tarzana",
    "woodland hills",
    "calabasas",
    "malibu",
    "westlake village",
    "los angeles",
    "la",
    "ventura county",
)


def _lane_label(lane: str) -> str:
    mapping = {
        "core_ops": "orchestrator",
        "ads_intel": "ads",
        "lead_ops": "lead ops",
        "experiments": "experiments",
        "systems": "systems",
    }
    return mapping.get(str(lane or "").strip().lower(), lane or "orchestrator")


def _expand_owner_shorthand(text: str) -> str:
    expanded = str(text or "").strip()
    if not expanded:
        return ""
    for alias, replacement in OWNER_GEO_ALIASES.items():
        expanded = re.sub(
            rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])",
            replacement,
            expanded,
            flags=re.IGNORECASE,
        )
    return re.sub(r"\s+", " ", expanded).strip()


def _task_title(text: str) -> str:
    title = str(text or "").strip().replace("\n", " ")
    title = re.sub(r"\s+", " ", title)
    return title[:120] or "SMS task"


def _status_label(status: str) -> str:
    mapping = {
        "open": "queued",
        "queued": "queued",
        "in_progress": "working",
        "claimed": "working",
        "running": "working",
        "done": "done",
        "completed": "done",
        "verified_done": "verified done",
        "failed": "failed",
        "review_needed": "needs review",
        "blocked": "blocked",
        "blocked_approval": "waiting on approval",
        "waiting_on_kai": "waiting on Kai",
        "approval_pending": "waiting on approval",
        "approved": "approved",
        "rejected": "rejected",
        "cancelled": "cancelled",
    }
    return mapping.get((status or "").strip().lower(), status or "unknown")


def _status_bucket(status: str) -> str:
    raw = str(status or "").strip().lower()
    if raw in {"done", "completed", "verified_done"}:
        return "done"
    if raw in {"failed", "cancelled"}:
        return "failed"
    if raw in {"blocked", "review_needed"}:
        return "blocked"
    if raw in {"blocked_approval", "waiting_on_kai", "approval_pending"}:
        return "waiting"
    if raw in {"claimed", "in_progress", "running"}:
        return "running"
    if raw in {"open", "queued"}:
        return "queued"
    return "unknown"


def _clean_progress_note(note: str, limit: int = 220) -> str:
    cleaned = str(note or "").strip()
    if not cleaned:
        return ""
    for marker in ("What should I do next?", "Reply STATUS", "Examples:"):
        if marker in cleaned:
            cleaned = cleaned.split(marker, 1)[0].strip()
    cleaned = re.sub(r"```.*?```", " ", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
    cleaned = re.sub(r"(^|\n)\s*#{1,6}\s*", " ", cleaned)
    cleaned = re.sub(r"\s#{1,6}\s*", " ", cleaned)
    cleaned = re.sub(r"(^|\n)\s*[-*]\s+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:limit].strip(" .")


def _is_verified_completion(task: Dict[str, Any]) -> bool:
    bucket = _status_bucket(str(task.get("status") or ""))
    if bucket != "done":
        return False

    note = _clean_progress_note(str(task.get("progress_note") or ""), limit=300).lower()
    if not note:
        return False

    strong_markers = (
        "verified",
        "confirmed",
        "passed",
        "validated",
        "opened ",
        "emailed ",
        "placed a live voice call",
        "placed a voice call",
        "sent ",
    )
    if any(marker in note for marker in strong_markers):
        return True
    if str(task.get("task_kind") or "") == "task_board" and note:
        return True
    return False


def _voice_status_label(task: Dict[str, Any]) -> str:
    bucket = _status_bucket(str(task.get("status") or ""))
    if bucket == "done" and _is_verified_completion(task):
        return "verified done"
    if bucket == "done":
        return "done"
    if bucket == "waiting":
        return "waiting on Kai"
    if bucket == "running":
        return "running"
    if bucket == "queued":
        return "queued"
    if bucket == "blocked":
        return "blocked"
    if bucket == "failed":
        return "failed"
    return _status_label(str(task.get("status") or ""))


def _voice_task_title(task: Dict[str, Any], limit: int = 96) -> str:
    title = str(task.get("title") or "").strip()
    title = re.sub(r"\s+", " ", title)
    return title[:limit] or "untitled task"


def _voice_task_sentence(task: Dict[str, Any], *, include_note: bool = True) -> str:
    title = _voice_task_title(task)
    ref = str(task.get("ref") or "").strip()
    label = _voice_status_label(task)
    note = _clean_progress_note(str(task.get("progress_note") or ""), limit=160)
    if note:
        note = re.split(r"(?<=[.!?])\s+", note, maxsplit=1)[0].strip()
        if re.search(r":\s*\d+[.)]", note):
            note = note.split(":", 1)[0].strip()
        note = note.rstrip(" .!?")
    prefix = f"{ref} is {label}: {title}" if ref else f"{title} is {label}"
    if include_note and note:
        return f"{prefix}. {note}."
    return f"{prefix}."


def _voice_task_brief_label(task: Dict[str, Any]) -> str:
    title = _voice_task_title(task, limit=72)
    label = _voice_status_label(task)
    return f"{str(task.get('ref') or '').strip()} {label}: {title}".strip()


def _operator_recommendations(
    latest_completion: Optional[Dict[str, Any]],
    active_tasks: List[Dict[str, Any]],
    blocked_tasks: List[Dict[str, Any]],
    awaiting_reply: bool,
) -> List[str]:
    recommendations: List[str] = []
    if blocked_tasks:
        recommendations.extend(
            [
                "email you the summary",
                "start another debug pass",
                "open the dashboard on the Mac mini",
            ]
        )
    elif latest_completion:
        title_norm = normalize_body(str(latest_completion.get("title") or ""))
        if any(token in title_norm for token in ("audit", "debug", "booking", "conversion")):
            recommendations.extend(
                [
                    "email you the summary",
                    "open the dashboard on the Mac mini",
                    "start another debug pass",
                ]
            )
        elif any(token in title_norm for token in ("website", "zoar", "dashboard", "launch")):
            recommendations.extend(
                [
                    "open the Zoar website",
                    "open the dashboard on the Mac mini",
                    "email you the summary",
                ]
            )
        else:
            recommendations.extend(
                [
                    "email you the summary",
                    "brief you again",
                    "take a new instruction",
                ]
            )
    elif active_tasks:
        recommendations.extend(
            [
                "brief you on active work",
                "email you the summary",
                "take a new instruction",
            ]
        )
    elif awaiting_reply:
        recommendations.extend(
            [
                "take your next instruction",
                "brief you again",
                "email you the summary",
            ]
        )
    else:
        recommendations.extend(
            [
                "take a new instruction",
                "open the dashboard on the Mac mini",
                "open the Zoar website",
            ]
        )

    deduped: List[str] = []
    seen = set()
    for item in recommendations:
        normalized = normalize_body(item)
        if normalized and normalized not in seen:
            deduped.append(item)
            seen.add(normalized)
    return deduped[:4]


def _join_voice_options(options: List[str]) -> str:
    cleaned = [str(item or "").strip() for item in options if str(item or "").strip()]
    if not cleaned:
        return "take a new instruction"
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} or {cleaned[1]}"
    return f"{', '.join(cleaned[:-1])}, or {cleaned[-1]}"


def _json_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raw = str(value or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except Exception:
        pass
    return [raw]


def _operator_event_key(source_kind: str, source_id: int, status: str) -> str:
    return f"{str(source_kind or '').strip().lower()}:{int(source_id or 0)}:{normalize_body(status)}"


def _critical_event_from_text(title: str, detail: str, status: str) -> bool:
    text = normalize_body("%s %s" % (title, detail))
    if str(status or "").strip().lower() in {"failed", "blocked", "blocked_approval", "waiting_on_kai"}:
        if any(token in text for token in ("booking", "campaign", "overnight", "queue", "voice", "website", "ad")):
            return True
    if str(status or "").strip().lower() in {"verified_done", "done"}:
        if any(token in text for token in ("booking flow", "overnight", "campaign", "deploy")):
            return True
    return False


def log_operator_event(
    phone: str,
    *,
    source_kind: str,
    source_id: int,
    status: str,
    title: str,
    detail: str = "",
    proof: str = "",
    recommendations: Optional[List[str]] = None,
    requires_response: bool = False,
    response_kind: str = "",
    response_target: str = "",
    severity: str = "",
) -> int:
    normalized_phone = normalize_phone(phone)
    if not normalized_phone:
        return 0

    event_status = str(status or "").strip().lower() or "info"
    event_key = _operator_event_key(source_kind, source_id, event_status)
    event_severity = str(severity or "").strip().lower()
    if not event_severity:
        event_severity = "critical" if _critical_event_from_text(title, detail, event_status) else "info"

    payload = json.dumps(_json_list(recommendations or []))
    conn = _conn()
    try:
        conn.execute(
            """
            INSERT INTO sms_control_operator_events (
                phone, event_key, source_kind, source_id, status, severity,
                title, detail, proof, recommendations_json, requires_response,
                response_kind, response_target, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(phone, event_key) DO UPDATE SET
                severity=excluded.severity,
                title=excluded.title,
                detail=excluded.detail,
                proof=excluded.proof,
                recommendations_json=excluded.recommendations_json,
                requires_response=excluded.requires_response,
                response_kind=excluded.response_kind,
                response_target=excluded.response_target,
                updated_at=excluded.updated_at,
                resolved_at=CASE WHEN excluded.requires_response = 1 THEN '' ELSE resolved_at END
            """,
            (
                normalized_phone,
                event_key,
                str(source_kind or "")[:60],
                int(source_id or 0),
                event_status,
                event_severity,
                str(title or "")[:240],
                str(detail or "")[:2000],
                str(proof or "")[:2000],
                payload,
                1 if requires_response else 0,
                str(response_kind or "")[:80],
                str(response_target or "")[:120],
                _utc_now(),
                _utc_now(),
            ),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT id
            FROM sms_control_operator_events
            WHERE phone = ? AND event_key = ?
            LIMIT 1
            """,
            (normalized_phone, event_key),
        ).fetchone()
        return int(row["id"] if row else 0)
    finally:
        conn.close()


def resolve_operator_event(phone: str, source_kind: str, source_id: int, status: str = "") -> None:
    normalized_phone = normalize_phone(phone)
    if not normalized_phone:
        return
    conn = _conn()
    try:
        if status:
            conn.execute(
                """
                UPDATE sms_control_operator_events
                SET resolved_at = ?, requires_response = 0, updated_at = ?
                WHERE phone = ? AND event_key = ?
                """,
                (_utc_now(), _utc_now(), normalized_phone, _operator_event_key(source_kind, source_id, status)),
            )
        else:
            conn.execute(
                """
                UPDATE sms_control_operator_events
                SET resolved_at = ?, requires_response = 0, updated_at = ?
                WHERE phone = ? AND source_kind = ? AND source_id = ? AND resolved_at = ''
                """,
                (_utc_now(), _utc_now(), normalized_phone, str(source_kind or "")[:60], int(source_id or 0)),
            )
        conn.commit()
    finally:
        conn.close()


def _operator_event_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": int(row["id"]),
        "phone": row["phone"] or "",
        "event_key": row["event_key"] or "",
        "source_kind": row["source_kind"] or "",
        "source_id": int(row["source_id"] or 0),
        "status": row["status"] or "",
        "severity": row["severity"] or "info",
        "title": row["title"] or "",
        "detail": row["detail"] or "",
        "proof": row["proof"] or "",
        "recommendations": _json_list(row["recommendations_json"] or "[]"),
        "requires_response": bool(int(row["requires_response"] or 0)),
        "response_kind": row["response_kind"] or "",
        "response_target": row["response_target"] or "",
        "notified_sms": bool(int(row["notified_sms"] or 0)),
        "notified_voice": bool(int(row["notified_voice"] or 0)),
        "resolved_at": row["resolved_at"] or "",
        "created_at": row["created_at"] or "",
        "updated_at": row["updated_at"] or "",
    }


def get_recent_operator_events(phone: str, limit: int = MAX_OPERATOR_EVENTS) -> List[Dict[str, Any]]:
    conn = _conn()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM sms_control_operator_events
            WHERE phone = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (normalize_phone(phone), int(max(limit or MAX_OPERATOR_EVENTS, 1))),
        ).fetchall()
        return [_operator_event_row_to_dict(row) for row in rows]
    finally:
        conn.close()


def mark_operator_event_sms_notified(event_id: int) -> None:
    conn = _conn()
    try:
        conn.execute(
            "UPDATE sms_control_operator_events SET notified_sms = 1, updated_at = ? WHERE id = ?",
            (_utc_now(), int(event_id)),
        )
        conn.commit()
    finally:
        conn.close()


def mark_operator_event_voice_notified(event_id: int) -> None:
    conn = _conn()
    try:
        conn.execute(
            "UPDATE sms_control_operator_events SET notified_voice = 1, updated_at = ? WHERE id = ?",
            (_utc_now(), int(event_id)),
        )
        conn.commit()
    finally:
        conn.close()


def create_owner_approval(
    phone: str,
    *,
    channel: str,
    target_id: int,
    target_ref: str,
    request_kind: str,
    summary: str,
    detail: str = "",
    approval_reason: str = "",
) -> int:
    normalized_phone = normalize_phone(phone)
    conn = _conn()
    try:
        conn.execute(
            """
            INSERT INTO sms_control_owner_approvals (
                phone, channel, target_id, target_ref, request_kind, status,
                summary, detail, approval_reason, requested_at
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
            ON CONFLICT(phone, channel, target_id, status) DO UPDATE SET
                summary=excluded.summary,
                detail=excluded.detail,
                approval_reason=excluded.approval_reason,
                requested_at=excluded.requested_at
            """,
            (
                normalized_phone,
                str(channel or "")[:40],
                int(target_id or 0),
                str(target_ref or "")[:40],
                str(request_kind or "")[:80],
                str(summary or "")[:240],
                str(detail or "")[:2000],
                str(approval_reason or "")[:500],
                _utc_now(),
            ),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT id
            FROM sms_control_owner_approvals
            WHERE phone = ? AND channel = ? AND target_id = ? AND status = 'pending'
            ORDER BY id DESC
            LIMIT 1
            """,
            (normalized_phone, str(channel or "")[:40], int(target_id or 0)),
        ).fetchone()
        return int(row["id"] if row else 0)
    finally:
        conn.close()


def _approval_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": int(row["id"]),
        "phone": row["phone"] or "",
        "channel": row["channel"] or "",
        "target_id": int(row["target_id"] or 0),
        "target_ref": row["target_ref"] or "",
        "request_kind": row["request_kind"] or "",
        "status": row["status"] or "",
        "summary": row["summary"] or "",
        "detail": row["detail"] or "",
        "approval_reason": row["approval_reason"] or "",
        "requested_at": row["requested_at"] or "",
        "decided_at": row["decided_at"] or "",
        "decision_note": row["decision_note"] or "",
    }


def get_pending_owner_approvals(phone: str, limit: int = MAX_PENDING_APPROVALS) -> List[Dict[str, Any]]:
    conn = _conn()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM sms_control_owner_approvals
            WHERE phone = ? AND status = 'pending'
            ORDER BY id DESC
            LIMIT ?
            """,
            (normalize_phone(phone), int(max(limit or 1, 1))),
        ).fetchall()
        return [_approval_row_to_dict(row) for row in rows]
    finally:
        conn.close()


def format_pending_approvals(phone: str) -> str:
    approvals = get_pending_owner_approvals(phone)
    approvals.extend(_brain_pending_approvals(phone))
    if not approvals:
        return "There are no pending owner approvals."
    lines = ["Pending owner approvals:"]
    for approval in approvals[:MAX_PENDING_APPROVALS]:
        lines.append(
            f"{approval['target_ref']} {approval['request_kind']}: {approval['summary'][:100]}"
        )
    lines.append("Reply APPROVE <ref> or REJECT <ref>.")
    return "\n".join(lines)


def format_next_step_prompt() -> str:
    return (
        "What should I do next?\n"
        "Examples: RUN: email me status || body | RUN: open zoar bathroom website | "
        "CODEX: change the Facebook ad headline | "
        "LEADS: find 25 wedding venue leads in Ventura"
    )


def _ensure_orchestrator_state_row(conn: sqlite3.Connection, phone: str) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO sms_control_orchestrator_state (phone, updated_at)
        VALUES (?, ?)
        """,
        (normalize_phone(phone), _utc_now()),
    )


def _get_orchestrator_state(phone: str) -> Dict[str, Any]:
    normalized = normalize_phone(phone)
    conn = _conn()
    try:
        _ensure_orchestrator_state_row(conn, normalized)
        conn.commit()
        row = conn.execute(
            """
            SELECT phone, awaiting_reply, active_prompt_kind, last_prompt_sent_at,
                   brain_idle_since, last_reply_at, last_reply_body, last_brain_task_id, updated_at
            FROM sms_control_orchestrator_state
            WHERE phone = ?
            """,
            (normalized,),
        ).fetchone()
        return dict(row) if row else {
            "phone": normalized,
            "awaiting_reply": 0,
            "active_prompt_kind": "",
            "last_prompt_sent_at": "",
            "brain_idle_since": "",
            "last_reply_at": "",
            "last_reply_body": "",
            "last_brain_task_id": 0,
            "updated_at": "",
        }
    finally:
        conn.close()


def _update_orchestrator_state(phone: str, **fields: Any) -> None:
    if not fields:
        return
    normalized = normalize_phone(phone)
    conn = _conn()
    try:
        _ensure_orchestrator_state_row(conn, normalized)
        payload = dict(fields)
        payload["updated_at"] = _utc_now()
        assignments = ", ".join(f"{key} = ?" for key in payload)
        values = list(payload.values()) + [normalized]
        conn.execute(
            f"""
            UPDATE sms_control_orchestrator_state
            SET {assignments}
            WHERE phone = ?
            """,
            values,
        )
        conn.commit()
    finally:
        conn.close()


def _is_waiting_for_orchestrator_reply(phone: str) -> bool:
    state = _get_orchestrator_state(phone)
    return bool(int(state.get("awaiting_reply") or 0))


def _clear_orchestrator_wait(phone: str, reply_text: str = "", brain_task_id: int = 0) -> None:
    updates: Dict[str, Any] = {
        "awaiting_reply": 0,
        "active_prompt_kind": "",
        "brain_idle_since": "",
    }
    if reply_text:
        updates["last_reply_at"] = _utc_now()
        updates["last_reply_body"] = str(reply_text or "")[:500]
        updates["last_brain_task_id"] = int(brain_task_id or 0)
    _update_orchestrator_state(phone, **updates)


def _brain_task_matches_phone(task: Dict[str, Any], phone: str) -> bool:
    payload = task.get("payload", {}) or {}
    if normalize_phone(payload.get("source_phone", "")) == normalize_phone(phone):
        return True
    return payload.get("created_by", "") == control_identity(phone)


def _brain_task_ref(task_id: int) -> str:
    return f"B#{int(task_id)}"


def _brain_task_note(task: Dict[str, Any]) -> str:
    if str(task.get("status", "")).lower() == "failed":
        note = str(task.get("error_text") or "").strip()
        return note[:240] or "Task failed without an error summary."

    result = task.get("result", {}) or {}
    note = ""
    if isinstance(result, dict):
        note = str(result.get("response") or result.get("summary") or "").strip()
        if not note and result:
            note = json.dumps(result, ensure_ascii=True)
    else:
        note = str(result or "").strip()
    note = re.sub(r"\s+", " ", note)
    return note[:240] or "No progress note yet."


def _recent_brain_tasks(phone: str, limit: int = MAX_STATUS_TASKS) -> List[Dict[str, Any]]:
    try:
        cp = _get_brain_control_plane()
        tasks = cp.list_tasks(limit=max(limit * 10, MAX_BRAIN_TASK_SCAN))
    except Exception as exc:
        log.warning("Brain task lookup failed: %s", exc)
        return []

    rows: List[Dict[str, Any]] = []
    for task in tasks:
        if not _brain_task_matches_phone(task, phone):
            continue
        updated_at = task.get("finished_at") or task.get("started_at") or task.get("created_at") or ""
        rows.append(
            {
                "id": int(task["id"]),
                "ref": _brain_task_ref(task["id"]),
                "title": str(task.get("objective") or "")[:120],
                "status": str(task.get("status") or ""),
                "progress_note": _brain_task_note(task),
                "updated_at": updated_at,
                "task_kind": "brain",
            }
        )
        if len(rows) >= limit * 2:
            break
    return rows


def _get_brain_task_detail(phone: str, task_id: int) -> Optional[Dict[str, Any]]:
    try:
        cp = _get_brain_control_plane()
        task = cp.get_task(int(task_id))
    except Exception as exc:
        log.warning("Brain task detail lookup failed: %s", exc)
        return None

    if not task or not _brain_task_matches_phone(task, phone):
        return None

    updated_at = task.get("finished_at") or task.get("started_at") or task.get("created_at") or ""
    return {
        "id": int(task["id"]),
        "ref": _brain_task_ref(task["id"]),
        "title": str(task.get("objective") or "")[:120],
        "status": str(task.get("status") or ""),
        "progress_note": _brain_task_note(task),
        "updated_at": updated_at,
        "task_kind": "brain",
    }


def _finalize_task(task_id: int, status: str, note: str) -> None:
    conn = _conn()
    try:
        completed_at = _utc_now() if status == "done" else ""
        conn.execute(
            """
            UPDATE task_board
            SET status = ?, progress_note = ?, assigned_to = '',
                updated_at = ?, completed_at = ?
            WHERE id = ?
            """,
            (str(status or "open"), str(note or "")[:5000], _utc_now(), completed_at, int(task_id)),
        )
        conn.commit()
    finally:
        conn.close()


def _extract_open_target(text: str) -> Optional[str]:
    normalized = normalize_body(text)
    match = re.match(r"^(?:open(?: up)?|launch|go to)\s+(.+)$", normalized)
    if not match:
        return None

    target = match.group(1).strip(" .")
    target = re.sub(r"\s+(?:on|in)\s+(?:my|this)\s+mac(?:\s+mini)?$", "", target)
    target = re.sub(r"\s+here$", "", target)
    target = target.strip(" .")

    direct_url = re.search(r"https?://[^\s]+", target)
    if direct_url:
        return direct_url.group(0).rstrip(".,)")

    if "zoar bathroom" in target or "zoar bathrooms" in target:
        return ZOAR_WEBSITE_URL
    if "dashboard" in target or "crm" in target:
        return load_shared_public_base_url().rstrip("/") + ZOAR_DASHBOARD_PATH

    domain_match = re.fullmatch(r"(?:www\.)?[a-z0-9.-]+\.[a-z]{2,}(?:/[^\s]*)?", target)
    if domain_match:
        domain = domain_match.group(0).rstrip(".,)")
        if not domain.startswith(("http://", "https://")):
            domain = "https://" + domain
        return domain

    return None


def _open_url_on_mac(url: str) -> Tuple[bool, str]:
    try:
        subprocess.run(
            ["open", url],
            check=True,
            timeout=LOCAL_OPEN_TIMEOUT_SECONDS,
        )
        return True, f"Opened {url} on this Mac mini."
    except subprocess.TimeoutExpired:
        return False, f"Timed out trying to open {url} on this Mac mini."
    except subprocess.CalledProcessError as exc:
        return False, f"macOS open failed for {url}: exit {exc.returncode}."
    except Exception as exc:
        return False, f"Failed to open {url} on this Mac mini: {exc}"


def _complete_local_action(
    phone: str,
    text: str,
    *,
    source_name: str = "",
    raw_body: str = "",
    executor: Any,
) -> str:
    task_id = queue_sms_task(phone, text)
    inbound_body = raw_body or text
    record_inbound(phone, inbound_body, source_name=source_name, task_id=task_id)

    ok, note = executor()
    status = "done" if ok else "failed"
    _finalize_task(task_id, status, note)
    mark_task_notification_sent(phone, task_id, status)
    log_operator_event(
        phone,
        source_kind="task_board",
        source_id=task_id,
        status="verified_done" if ok else "failed",
        title=_task_title(text),
        detail=_clean_progress_note(note, limit=300),
        proof=str(note or "")[:300],
        recommendations=_operator_recommendations(
            {"id": task_id, "title": _task_title(text), "status": "verified_done" if ok else "failed", "progress_note": note},
            [],
            [],
            False,
        ),
    )
    resolve_operator_event(phone, "task_board", task_id)
    if _is_waiting_for_orchestrator_reply(phone):
        _clear_orchestrator_wait(phone, reply_text=inbound_body, brain_task_id=0)

    return (
        f"Task #{task_id} {_status_label(status)}.\n"
        f"{note}\n"
        f"{format_next_step_prompt()}"
    )


def _parse_self_email_command(text: str) -> Optional[Tuple[str, str]]:
    match = re.match(r"^\s*email\s+(?:me|myself)\b[:\s-]*(.*)$", str(text or "").strip(), re.IGNORECASE | re.DOTALL)
    if not match:
        return None

    remainder = str(match.group(1) or "").strip()
    if not remainder:
        return (
            DEFAULT_SELF_EMAIL_SUBJECT,
            "This is a self-email from SMS control on your Mac mini.",
        )

    subject = DEFAULT_SELF_EMAIL_SUBJECT
    body = remainder

    for separator in ("||", "::"):
        if separator in remainder:
            left, right = remainder.split(separator, 1)
            left = left.strip()
            right = right.strip()
            subject = left or DEFAULT_SELF_EMAIL_SUBJECT
            body = right or "This is a self-email from SMS control on your Mac mini."
            break
    else:
        structured = re.match(
            r"^\s*subject\s*=\s*(.*?)\s+body\s*=\s*(.+)$",
            remainder,
            re.IGNORECASE | re.DOTALL,
        )
        if structured:
            subject = structured.group(1).strip() or DEFAULT_SELF_EMAIL_SUBJECT
            body = structured.group(2).strip() or "This is a self-email from SMS control on your Mac mini."

    return subject[:200], body[:5000]


def _send_self_email(subject: str, body: str) -> Tuple[bool, str]:
    cfg = _load_config()
    from_email = str(cfg.get("gmail_address") or "zoarbathrooms@gmail.com").strip()
    app_password = str(cfg.get("gmail_app_password") or "").strip()
    to_email = load_self_email()
    if not from_email or not app_password:
        return False, "Email is not configured. Add gmail_address and gmail_app_password to ~/.nexus/config.json."

    approval_id = create_system_outbound_approval("email", to_email, body, subject=subject)
    try:
        from integrations.messaging import outbound_gate

        gate = outbound_gate("email", to_email, "Kai", body, approval_id=approval_id, code_path="sms_control.send_self_email")
        if not gate.get("ok"):
            return False, f"Email blocked: {gate.get('reason', 'unknown policy block')}."
    except Exception as exc:
        return False, f"Email gate failed: {exc}"

    msg = EmailMessage()
    msg["From"] = f"Zoar Bathroom Rentals <{from_email}>"
    msg["To"] = to_email
    msg["Reply-To"] = from_email
    msg["Subject"] = subject or DEFAULT_SELF_EMAIL_SUBJECT
    msg.set_content(body or "This is a self-email from SMS control on your Mac mini.")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=LOCAL_EMAIL_TIMEOUT_SECONDS) as smtp:
            smtp.login(from_email, app_password)
            smtp.send_message(msg)
        return True, f"Emailed {to_email} from {from_email} with subject \"{msg['Subject']}\"."
    except Exception as exc:
        return False, f"Email send failed: {exc}"


def _handle_local_email_command(phone: str, text: str, source_name: str = "", raw_body: str = "") -> Optional[str]:
    parsed = _parse_self_email_command(text)
    if not parsed:
        return None
    subject, body = parsed
    return _complete_local_action(
        phone,
        text,
        source_name=source_name,
        raw_body=raw_body,
        executor=lambda: _send_self_email(subject, body),
    )


def _parse_call_me_command(text: str) -> Optional[str]:
    match = re.match(
        r"^\s*(?:voice\s+)?call\s+me(?:\s+(?:and\s+)?say)?\b[:\s-]*(.*)$",
        str(text or "").strip(),
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    message = str(match.group(1) or "").strip()
    return message[:500] or DEFAULT_CALL_MESSAGE


def queue_voice_call_request(
    phone: str,
    message: str,
    *,
    provider: str = "google_voice",
    approval_id: int = 0,
) -> int:
    conn = _conn()
    try:
        conn.execute(
            """
            INSERT INTO sms_control_voice_call_requests (
                phone, message, provider, approval_id, status, requested_at
            ) VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            (
                normalize_phone(phone),
                str(message or "")[:1000],
                str(provider or "google_voice")[:40],
                int(approval_id or 0),
                _utc_now(),
            ),
        )
        conn.commit()
        return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    finally:
        conn.close()


def claim_pending_voice_call_request(provider: str = "google_voice") -> Optional[Dict[str, Any]]:
    conn = _conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT *
            FROM sms_control_voice_call_requests
            WHERE status = 'pending'
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            conn.commit()
            return None

        conn.execute(
            """
            UPDATE sms_control_voice_call_requests
            SET status = 'dialing', provider = ?, started_at = ?
            WHERE id = ?
            """,
            (str(provider or "google_voice")[:40], _utc_now(), int(row["id"])),
        )
        conn.commit()
        item = dict(row)
        item["status"] = "dialing"
        item["provider"] = str(provider or "google_voice")[:40]
        return item
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def complete_voice_call_request(request_id: int, ok: bool, note: str, provider: str = "google_voice") -> None:
    conn = _conn()
    try:
        conn.execute(
            """
            UPDATE sms_control_voice_call_requests
            SET status = ?, provider = ?, completed_at = ?, result_note = ?
            WHERE id = ?
            """,
            (
                "placed" if ok else "failed",
                str(provider or "google_voice")[:40],
                _utc_now(),
                str(note or "")[:1000],
                int(request_id),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _check_voice_outbound_gate(phone: str, message: str, *, code_path: str) -> Tuple[int, Optional[str]]:
    approval_id = create_system_outbound_approval("sms", phone, message)
    try:
        from integrations.messaging import outbound_gate

        gate = outbound_gate("sms", phone, "Kai", message, approval_id=approval_id, code_path=code_path)
        if not gate.get("ok"):
            return 0, f"Voice call blocked: {gate.get('reason', 'unknown policy block')}."
    except Exception as exc:
        return 0, f"Voice call gate failed: {exc}"
    return approval_id, None


def _place_voice_call_via_twilio(phone: str, message: str, cfg: Dict[str, Any]) -> Tuple[bool, str]:
    digits = normalize_phone(phone)
    sid = str(cfg.get("twilio_sid") or "").strip()
    token = str(cfg.get("twilio_token") or "").strip()
    from_number = str(cfg.get("twilio_from") or "").strip()

    _approval_id, gate_error = _check_voice_outbound_gate(
        phone,
        message,
        code_path="sms_control.place_voice_call",
    )
    if gate_error:
        return False, gate_error

    if len(digits) != 10:
        return False, f"Invalid phone for voice call: {phone}"

    public_base = load_voice_public_base_url()
    if not public_base:
        return (
            False,
            "Voice call is missing a public webhook base URL. Add voice_control_public_base_url or fb_webhook_domain to ~/.nexus/config.json.",
        )

    webhook_url = (
        f"{public_base}/outbound"
        f"?phone={urllib.parse.quote(digits, safe='')}"
        f"&message={urllib.parse.quote(message or DEFAULT_CALL_MESSAGE, safe='')}"
    )
    status_callback_url = f"{public_base}/status"
    transport = "gather"
    try:
        from core import voice_control as _voice_control

        transport = _voice_control.load_voice_transport()
    except Exception:
        transport = "gather"
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json"
    payload = urllib.parse.urlencode(
        {
            "To": f"+1{digits}",
            "From": from_number,
            "Url": webhook_url,
            "Method": "POST",
            "Timeout": "20",
            "StatusCallback": status_callback_url,
            "StatusCallbackMethod": "POST",
            "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"],
        },
        doseq=True,
    ).encode()
    request = urllib.request.Request(url, data=payload, method="POST")
    request.add_header("Authorization", "Basic " + base64.b64encode(f"{sid}:{token}".encode("utf-8")).decode("ascii"))

    try:
        with urllib.request.urlopen(request, timeout=LOCAL_CALL_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8", errors="replace")
        data = json.loads(raw or "{}")
        call_sid = str(data.get("sid") or "").strip()
        if call_sid:
            return True, (
                f"Placed a live voice call to {phone} from {from_number} via Twilio "
                f"using the {transport} voice transport. Message: \"{message}\"."
            )
        return True, f"Placed a voice call to {phone} from {from_number} via Twilio using the {transport} transport."
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        return False, f"Voice call failed: Twilio HTTP {exc.code}: {detail}"
    except Exception as exc:
        return False, f"Voice call failed: {exc}"


def _place_voice_call(phone: str, message: str) -> Tuple[bool, str]:
    digits = normalize_phone(phone)
    if not is_authorized_voice_phone(digits):
        return False, "Voice calls are restricted to your owner control number only."

    cfg = _load_config()
    if _is_twilio_voice_configured(cfg):
        return _place_voice_call_via_twilio(phone, message, cfg)

    if not is_google_voice_call_configured():
        return (
            False,
            "Voice call is not configured. Add twilio_sid, twilio_token, and twilio_from to ~/.nexus/config.json, "
            "or keep the Google Voice SMS control daemon signed in for browser-based alert calls.",
        )

    approval_id, gate_error = _check_voice_outbound_gate(
        phone,
        message,
        code_path="sms_control.queue_google_voice_call",
    )
    if gate_error:
        return False, gate_error

    request_id = queue_voice_call_request(
        digits,
        message or DEFAULT_CALL_MESSAGE,
        provider="google_voice",
        approval_id=approval_id,
    )
    if request_id <= 0:
        return False, "Failed to queue the Google Voice alert call request."

    return (
        True,
        f"Queued a Google Voice alert call to {phone} from the active browser session. Request #{request_id}. "
        "The phone should ring shortly. Spoken voice prompts still require Twilio; use the SMS response for details.",
    )


def _handle_local_call_command(phone: str, text: str, source_name: str = "", raw_body: str = "") -> Optional[str]:
    message = _parse_call_me_command(text)
    if message is None:
        return None
    return _complete_local_action(
        phone,
        text,
        source_name=source_name,
        raw_body=raw_body,
        executor=lambda: _place_voice_call(phone, message),
    )


def _handle_local_open_command(phone: str, text: str, source_name: str = "", raw_body: str = "") -> Optional[str]:
    url = _extract_open_target(text)
    if not url:
        return None

    return _complete_local_action(
        phone,
        text,
        source_name=source_name,
        raw_body=raw_body,
        executor=lambda: _open_url_on_mac(url),
    )


def _service_aliases() -> Dict[str, str]:
    return {
        "voice": "voice_control_server",
        "voice daemon": "voice_control_server",
        "voice server": "voice_control_server",
        "sms": "sms_control_daemon",
        "sms daemon": "sms_control_daemon",
        "process manager": "process_manager",
        "manager": "process_manager",
        "task worker": "task_worker",
        "worker": "task_worker",
        "brain runner": "brain_agent_runner",
        "brain agent runner": "brain_agent_runner",
        "brain scheduler": "brain_task_scheduler",
        "scheduler": "brain_task_scheduler",
    }


def _service_target(text: str) -> str:
    lowered = normalize_body(text)
    aliases = _service_aliases()
    for alias, target in aliases.items():
        if alias in lowered:
            return target
    return ""


def _managed_process_row(name: str) -> Optional[Dict[str, Any]]:
    conn = _conn()
    try:
        row = conn.execute(
            """
            SELECT name, pid, status, process_type, last_heartbeat, error_message
            FROM managed_processes
            WHERE name = ?
            LIMIT 1
            """,
            (str(name or ""),),
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def _pid_file_for_service(name: str) -> Path:
    return PID_DIR / f"{str(name or '').strip()}.pid"


def _read_service_pid(name: str) -> int:
    pid_file = _pid_file_for_service(name)
    if not pid_file.exists():
        return 0
    try:
        raw = pid_file.read_text(encoding="utf-8").strip()
        return int(raw) if raw.isdigit() else 0
    except Exception:
        return 0


def _wait_for_service_health(service_name: str, timeout_seconds: int = 20) -> bool:
    deadline = time.time() + max(1, int(timeout_seconds or 1))
    while time.time() < deadline:
        if service_name == "voice_control_server":
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{load_local_voice_port()}/voice/healthz",
                    timeout=4,
                ) as response:
                    if response.status == 200:
                        return True
            except Exception:
                pass
        else:
            row = _managed_process_row(service_name)
            pid = int((row or {}).get("pid") or 0)
            if pid > 0:
                try:
                    os.kill(pid, 0)
                    return True
                except Exception:
                    pass
        time.sleep(1)
    return False


def _start_named_service(service_name: str) -> Tuple[bool, str]:
    service = str(service_name or "").strip()
    if service == "process_manager":
        try:
            subprocess.Popen(
                [str(VENV_PYTHON), "scripts/process_manager.py"],
                cwd=str(ROOT_DIR),
                stdout=open(str(LOG_DIR / "manager.manual_restart.log"), "a"),
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except Exception as exc:
            return False, f"Failed to start process manager: {exc}"
        ok = _wait_for_service_health("process_manager", timeout_seconds=10)
        return ok, "Process manager restarted." if ok else "Process manager restart requested but health is still pending."

    try:
        subprocess.run(
            ["bash", str(ROOT_DIR / "scripts" / "start_all_daemons.sh")],
            cwd=str(ROOT_DIR),
            timeout=40,
            check=True,
        )
    except Exception as exc:
        return False, f"Failed to restart {service}: {exc}"
    ok = _wait_for_service_health(service, timeout_seconds=20)
    if ok:
        return True, f"{service} restarted and verified."
    return False, f"{service} restart requested but verification is still pending."


def _restart_named_service(service_name: str) -> Tuple[bool, str]:
    service = str(service_name or "").strip()
    if not service:
        return False, "No service specified."

    pid = _read_service_pid(service)
    if pid <= 0:
        row = _managed_process_row(service) or {}
        pid = int(row.get("pid") or 0)
    if pid > 0:
        try:
            os.kill(pid, 15)
        except Exception:
            pass
        time.sleep(2)
    return _start_named_service(service)


def _service_status_text(service_name: str) -> str:
    row = _managed_process_row(service_name) or {}
    pid = int(row.get("pid") or 0)
    status = str(row.get("status") or "unknown")
    heartbeat = str(row.get("last_heartbeat") or "").strip()
    error = str(row.get("error_message") or "").strip()
    details = [f"{service_name} is {_status_label(status)}"]
    if pid > 0:
        details.append(f"PID {pid}")
    if heartbeat:
        details.append(f"heartbeat {heartbeat}")
    if error:
        details.append(f"error {error[:120]}")
    return ". ".join(details) + "."


def _handle_local_service_command(phone: str, text: str, source_name: str = "", raw_body: str = "") -> Optional[str]:
    lowered = normalize_body(text)
    service_name = _service_target(lowered)
    if not service_name:
        return None

    if any(token in lowered for token in ("restart", "reboot", "bounce")):
        return _complete_local_action(
            phone,
            f"restart {service_name}",
            source_name=source_name,
            raw_body=raw_body or text,
            executor=lambda: _restart_named_service(service_name),
        )

    if any(token in lowered for token in ("status", "health", "heartbeat", "is the")):
        return _complete_local_action(
            phone,
            f"status {service_name}",
            source_name=source_name,
            raw_body=raw_body or text,
            executor=lambda: (True, _service_status_text(service_name)),
        )

    return None


def _handle_local_control_command(phone: str, text: str, source_name: str = "", raw_body: str = "") -> Optional[str]:
    for handler in (
        _handle_local_email_command,
        _handle_local_call_command,
        _handle_local_open_command,
        _handle_local_service_command,
    ):
        response = handler(phone, text, source_name=source_name, raw_body=raw_body)
        if response:
            return response
    return None


def _extract_control_mode(text: str) -> Tuple[str, str]:
    match = re.match(r"^(run|local|orch|orchestrator|brain)\s*:\s*(.*)$", str(text or "").strip(), re.IGNORECASE)
    if not match:
        return "", ""
    raw_mode = str(match.group(1) or "").strip().lower()
    payload = str(match.group(2) or "").strip()
    mode = "run" if raw_mode in {"run", "local"} else "orch"
    return mode, payload


def should_accept_inbound(phone: str, body: str) -> bool:
    conn = _conn()
    try:
        cutoff = (datetime.utcnow() - timedelta(minutes=INBOUND_DEDUP_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
        row = conn.execute(
            """
            SELECT 1 FROM sms_control_inbox
            WHERE phone = ? AND body_norm = ? AND received_at >= ?
            LIMIT 1
            """,
            (normalize_phone(phone), normalize_body(body), cutoff),
        ).fetchone()
        return row is None
    finally:
        conn.close()


def record_inbound(phone: str, body: str, source_name: str = "", task_id: int = 0) -> None:
    conn = _conn()
    try:
        conn.execute(
            """
            INSERT INTO sms_control_inbox (phone, body, body_norm, source_name, task_id, received_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                normalize_phone(phone),
                str(body or "").strip(),
                normalize_body(body),
                str(source_name or "")[:120],
                int(task_id or 0),
                _utc_now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def queue_sms_task(phone: str, body: str) -> int:
    task_type, priority = classify_task(body)
    tags = json.dumps(["sms_control", task_type])
    conn = _conn()
    try:
        conn.execute(
            """
            INSERT INTO task_board (
                title, description, priority, status, assigned_to, created_by,
                created_at, updated_at, completed_at, tags, files_involved,
                progress_note, task_type, complexity
            ) VALUES (?, ?, ?, 'open', '', ?, datetime('now'), datetime('now'), '', ?, '[]', '', ?, 3)
            """,
            (_task_title(body), str(body or "").strip(), priority, control_identity(phone), tags, task_type),
        )
        conn.commit()
        task_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        return task_id
    finally:
        conn.close()


def _suggest_files_for_request(text: str) -> List[str]:
    lowered = normalize_body(text)
    selected: List[str] = []
    for tokens, files in FILE_HINT_MAP:
        if any(token in lowered for token in tokens):
            selected.extend(files)
    deduped: List[str] = []
    seen = set()
    for path in selected:
        clean = str(path or "").strip()
        if clean and clean not in seen and clean not in {
            "server.py",
            "core/vendor_api.py",
            "core/email_queue_manager.py",
            "scripts/bounce_monitor.py",
        }:
            deduped.append(clean)
            seen.add(clean)
    return deduped[:6]


def queue_owner_code_task(
    phone: str,
    body: str,
    *,
    source_name: str = "",
    requires_approval: bool = True,
) -> Dict[str, Any]:
    task_type, priority = classify_task(body)
    files_involved = _suggest_files_for_request(body)
    tags = json.dumps(["sms_control", "owner_copilot", "codex_request", task_type])
    status = "blocked" if requires_approval else "open"
    progress_note = "Waiting for owner approval." if requires_approval else "Queued from owner SMS command."

    conn = _conn()
    try:
        conn.execute(
            """
            INSERT INTO task_board (
                title, description, priority, status, assigned_to, created_by,
                created_at, updated_at, completed_at, tags, files_involved,
                progress_note, task_type, complexity
            ) VALUES (?, ?, ?, ?, '', ?, datetime('now'), datetime('now'), '', ?, ?, ?, ?, ?)
            """,
            (
                _task_title(body),
                str(body or "").strip(),
                max(priority, 3),
                status,
                control_identity(phone),
                tags,
                json.dumps(files_involved),
                progress_note,
                task_type,
                OWNER_CODE_TASK_COMPLEXITY,
            ),
        )
        conn.commit()
        task_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    finally:
        conn.close()

    task_ref = f"#{task_id}"
    recommendations = ["approve the code edit", "review the requested files", "cancel the request"]
    if requires_approval:
        create_owner_approval(
            phone,
            channel="task_board",
            target_id=task_id,
            target_ref=task_ref,
            request_kind="code edit",
            summary=_task_title(body),
            detail=str(body or "").strip(),
            approval_reason="Owner-requested code or ad change",
        )
        log_operator_event(
            phone,
            source_kind="task_board",
            source_id=task_id,
            status="waiting_on_kai",
            title=_task_title(body),
            detail=f"Owner approval needed before executing {task_ref}. Reply APPROVE {task_ref} to run it.",
            proof="Files: " + ", ".join(files_involved) if files_involved else "",
            recommendations=recommendations,
            requires_response=True,
            response_kind="approval",
            response_target=task_ref,
        )
    else:
        log_operator_event(
            phone,
            source_kind="task_board",
            source_id=task_id,
            status="queued",
            title=_task_title(body),
            detail=f"Queued owner code task {task_ref}.",
            proof="Files: " + ", ".join(files_involved) if files_involved else "",
            recommendations=["check status", "wait for completion"],
        )
    return {
        "task_id": task_id,
        "task_ref": task_ref,
        "status": status,
        "files_involved": files_involved,
        "requires_approval": bool(requires_approval),
        "source_name": source_name,
    }


def queue_brain_sms_task(
    phone: str,
    body: str,
    source_name: str = "",
    *,
    requires_approval: bool = False,
    risk_level: str = "low",
    approval_reason: str = "",
) -> Dict[str, Any]:
    route = classify_brain_task(body)
    payload = {
        "source": "sms_control",
        "source_phone": normalize_phone(phone),
        "source_name": str(source_name or "")[:120],
        "created_by": control_identity(phone),
        "reply_channel": "sms",
        "requested_at": _utc_now(),
    }
    constraints = {
        "human_requested": True,
        "requested_via": "sms",
    }
    cp = _get_brain_control_plane()
    result = cp.enqueue_task(
        task_type=route["task_type"],
        objective=str(body or "").strip(),
        lane=route["lane"],
        priority=int(route["priority"]),
        payload=payload,
        constraints=constraints,
        requires_approval=bool(requires_approval),
        risk_level=str(risk_level or "low"),
        approval_reason=str(approval_reason or "")[:240],
        requested_by=control_identity(phone),
    )
    if not result.get("ok"):
        raise RuntimeError(result.get("error", "unknown brain enqueue failure"))
    return {
        "task_id": int(result["task_id"]),
        "lane": route["lane"],
        "task_type": route["task_type"],
        "approval_id": int(result.get("approval_id") or 0),
        "requires_approval": bool(requires_approval),
    }


def _queue_brain_task_from_sms(
    phone: str,
    body: str,
    *,
    source_name: str = "",
    raw_body: str = "",
    clear_orchestrator_wait: bool = False,
    requires_approval: bool = False,
    risk_level: str = "low",
    approval_reason: str = "",
) -> str:
    try:
        task = queue_brain_sms_task(
            phone,
            body,
            source_name=source_name,
            requires_approval=requires_approval,
            risk_level=risk_level,
            approval_reason=approval_reason,
        )
    except Exception as exc:
        log.warning("Failed to queue brain task from SMS: %s", exc)
        return "The orchestrator could not queue that task right now. Try again in a minute."

    inbound_body = raw_body or body
    record_inbound(phone, inbound_body, source_name=source_name, task_id=int(task["task_id"]))
    if clear_orchestrator_wait:
        _clear_orchestrator_wait(phone, reply_text=inbound_body, brain_task_id=int(task["task_id"]))
    if task.get("requires_approval"):
        task_ref = _brain_task_ref(int(task["task_id"]))
        log_operator_event(
            phone,
            source_kind="brain",
            source_id=int(task["task_id"]),
            status="waiting_on_kai",
            title=_task_title(body),
            detail=f"Owner approval needed before executing {task_ref}. Reply APPROVE {task_ref} to run it.",
            recommendations=["approve the task", "reject the task", "show pending approvals"],
            requires_response=True,
            response_kind="approval",
            response_target=task_ref,
        )
        return (
            f"Prepared brain task {task_ref} ({_lane_label(str(task.get('lane') or 'core_ops'))}).\n"
            f"{_task_title(body)}\n"
            f"Approval needed. Reply APPROVE {task_ref} or REJECT {task_ref}."
        )

    log_operator_event(
        phone,
        source_kind="brain",
        source_id=int(task["task_id"]),
        status="queued",
        title=_task_title(body),
        detail=f"Queued brain task {_brain_task_ref(int(task['task_id']))} for {_lane_label(str(task.get('lane') or 'core_ops'))}.",
        recommendations=["check status", "wait for completion"],
    )
    return format_queued_brain_task(int(task["task_id"]), body, str(task.get("lane") or "core_ops"))


def create_system_sms_approval(phone: str, message: str) -> int:
    return create_system_outbound_approval("sms", phone, message)


def create_system_outbound_approval(channel: str, recipient: str, message: str, subject: str = "") -> int:
    conn = _conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS message_approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id INTEGER NOT NULL,
                lead_name TEXT DEFAULT '',
                lead_phone TEXT DEFAULT '',
                lead_email TEXT DEFAULT '',
                lead_source TEXT DEFAULT '',
                channel TEXT DEFAULT 'sms',
                message_type TEXT DEFAULT 'initial',
                proposed_message TEXT DEFAULT '',
                proposed_subject TEXT DEFAULT '',
                actual_message_sent TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                approval_method TEXT DEFAULT '',
                followup_step INTEGER DEFAULT 0,
                last_outbound TEXT DEFAULT '',
                last_inbound TEXT DEFAULT '',
                reminder_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                approved_at TEXT DEFAULT '',
                expired_at TEXT DEFAULT '',
                telegram_notified INTEGER DEFAULT 0,
                slack_notified INTEGER DEFAULT 0,
                sms_notified INTEGER DEFAULT 0,
                notification_errors TEXT DEFAULT ''
            )
            """
        )
        now = _utc_now()
        conn.execute(
            """
            INSERT INTO message_approvals (
                lead_id, lead_name, lead_phone, lead_email, lead_source,
                channel, message_type, proposed_message, proposed_subject,
                actual_message_sent, status, approval_method,
                created_at, approved_at, telegram_notified
            ) VALUES (?, ?, ?, ?, ?, ?, 'manual_reply', ?, ?, ?, 'approved', 'kai_approved', ?, ?, 1)
            """,
            (
                0,
                "SMS Control",
                normalize_phone(recipient) if str(channel or "").strip().lower() == "sms" else "",
                str(recipient or "").strip().lower() if str(channel or "").strip().lower() == "email" else "",
                "system",
                str(channel or "sms").strip().lower(),
                str(message or "")[:4000],
                str(subject or "")[:250],
                str(message or "")[:4000],
                now,
                now,
            ),
        )
        conn.commit()
        return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    finally:
        conn.close()


def get_recent_tasks(phone: str, limit: int = MAX_STATUS_TASKS) -> List[Dict]:
    conn = _conn()
    try:
        rows = conn.execute(
            """
            SELECT id, title, status, assigned_to, progress_note, updated_at, completed_at
            FROM task_board
            WHERE created_by = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (control_identity(phone), int(max(limit * 2, MAX_STATUS_TASKS))),
        ).fetchall()
        tasks = [
            {
                "id": int(r["id"]),
                "ref": f"#{int(r['id'])}",
                "title": r["title"] or "",
                "status": r["status"] or "",
                "progress_note": r["progress_note"] or "",
                "updated_at": r["updated_at"] or "",
                "task_kind": "task_board",
            }
            for r in rows
        ]
    finally:
        conn.close()

    tasks.extend(_recent_brain_tasks(phone, limit=max(limit * 2, MAX_STATUS_TASKS)))
    tasks.sort(
        key=lambda item: (
            _timestamp_sort_key(item.get("updated_at", "")),
            int(item.get("id", 0)),
        ),
        reverse=True,
    )
    return tasks[:limit]


def get_task_detail(phone: str, task_id: int, task_kind: str = "") -> Optional[Dict]:
    kind = str(task_kind or "").strip().lower()
    if kind != "brain":
        conn = _conn()
        try:
            row = conn.execute(
                """
                SELECT id, title, description, status, assigned_to, progress_note, updated_at, completed_at
                FROM task_board
                WHERE id = ? AND created_by = ?
                """,
                (int(task_id), control_identity(phone)),
            ).fetchone()
            if row:
                return {
                    "id": int(row["id"]),
                    "ref": f"#{int(row['id'])}",
                    "title": row["title"] or "",
                    "description": row["description"] or "",
                    "status": row["status"] or "",
                    "progress_note": row["progress_note"] or "",
                    "updated_at": row["updated_at"] or "",
                    "task_kind": "task_board",
                }
        finally:
            conn.close()

    return _get_brain_task_detail(phone, task_id)


def _task_event_status(task: Dict[str, Any]) -> str:
    bucket = _status_bucket(str(task.get("status") or ""))
    if bucket == "done":
        return "verified_done" if _is_verified_completion(task) else "done"
    if bucket == "waiting":
        return "waiting_on_kai"
    if bucket == "running":
        return "running"
    if bucket == "queued":
        return "queued"
    if bucket == "blocked":
        return "blocked"
    if bucket == "failed":
        return "failed"
    return str(task.get("status") or "unknown")


def sync_operator_events(phone: str) -> None:
    normalized_phone = normalize_phone(phone)
    if not normalized_phone:
        return

    tasks = get_recent_tasks(normalized_phone, limit=max(MAX_STATUS_TASKS * 2, 10))
    for task in tasks:
        event_status = _task_event_status(task)
        recommendations = []
        if event_status in {"verified_done", "done"}:
            recommendations = _operator_recommendations(dict(task), [], [], False)
        elif event_status in {"failed", "blocked", "waiting_on_kai"}:
            recommendations = ["review the issue", "approve the next step", "ask for a brief"]
        elif event_status in {"queued", "running"}:
            recommendations = ["check status", "wait for completion"]

        log_operator_event(
            normalized_phone,
            source_kind=str(task.get("task_kind") or "task_board"),
            source_id=int(task.get("id") or 0),
            status=event_status,
            title=_voice_task_title(task),
            detail=_clean_progress_note(str(task.get("progress_note") or ""), limit=300),
            proof=str(task.get("progress_note") or "")[:300],
            recommendations=recommendations,
            requires_response=event_status == "waiting_on_kai",
            response_kind="approval" if event_status == "waiting_on_kai" else "",
            response_target=str(task.get("ref") or ""),
        )

    for approval in get_pending_owner_approvals(normalized_phone):
        log_operator_event(
            normalized_phone,
            source_kind="approval",
            source_id=int(approval["id"]),
            status="waiting_on_kai",
            title=approval["summary"],
            detail=f"Pending owner approval for {approval['target_ref']}. Reply APPROVE {approval['target_ref']} or REJECT {approval['target_ref']}.",
            proof=approval.get("detail", ""),
            recommendations=["approve the task", "reject the task", "show task detail"],
            requires_response=True,
            response_kind="approval",
            response_target=str(approval["target_ref"] or ""),
        )


def _brain_pending_approvals(phone: str, limit: int = MAX_PENDING_APPROVALS) -> List[Dict[str, Any]]:
    try:
        cp = _get_brain_control_plane()
        approvals = cp.list_approvals(status="pending", limit=max(limit * 3, limit))
    except Exception as exc:
        log.warning("Brain approval lookup failed: %s", exc)
        return []

    rows: List[Dict[str, Any]] = []
    for approval in approvals:
        task_id = int(approval.get("task_id") or 0)
        if task_id <= 0:
            continue
        task = _get_brain_task_detail(phone, task_id)
        if not task:
            continue
        rows.append(
            {
                "target_ref": task["ref"],
                "summary": task["title"],
                "detail": approval.get("reason") or "",
                "channel": "brain",
                "target_id": task_id,
                "status": "pending",
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _parse_decision_command(text: str) -> Optional[Dict[str, str]]:
    match = re.match(
        r"^\s*(approve|reject|deny|cancel)\b(?:\s+(?:task\s+)?)?(b\s*)?#?(\d+)?(?:\s+(.*))?$",
        str(text or "").strip(),
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    decision_word = str(match.group(1) or "").strip().lower()
    decision = "approved" if decision_word == "approve" else "rejected"
    target_kind = "brain" if match.group(2) else "task_board"
    digits = str(match.group(3) or "").strip()
    ref = f"{'B#' if target_kind == 'brain' else '#'}{digits}" if digits else ""
    note = str(match.group(4) or "").strip()
    return {"decision": decision, "target_kind": target_kind, "ref": ref, "note": note}


def _pending_owner_approval_for_ref(phone: str, target_ref: str = "") -> Optional[Dict[str, Any]]:
    approvals = get_pending_owner_approvals(phone, limit=max(MAX_PENDING_APPROVALS * 2, 10))
    normalized_ref = str(target_ref or "").strip().upper()
    if normalized_ref:
        for approval in approvals:
            if str(approval.get("target_ref") or "").strip().upper() == normalized_ref:
                return approval
        return None
    return approvals[0] if approvals else None


def decide_owner_task_approval(phone: str, decision: str, target_ref: str = "", note: str = "") -> str:
    normalized_phone = normalize_phone(phone)
    dec = str(decision or "").strip().lower()
    if dec not in {"approved", "rejected"}:
        return "Decision must be APPROVE or REJECT."

    if str(target_ref or "").strip().upper().startswith("B#"):
        task_id = int(re.sub(r"\D+", "", str(target_ref or "")) or "0")
        if task_id <= 0:
            pending_brain = _brain_pending_approvals(normalized_phone, limit=1)
            if not pending_brain:
                return "There is no pending brain approval."
            task_id = int(pending_brain[0]["target_id"])
            target_ref = pending_brain[0]["target_ref"]
        try:
            cp = _get_brain_control_plane()
            result = cp.approve_task(
                task_id=task_id,
                decision=dec,
                decision_by=control_identity(normalized_phone),
                decision_note=note,
            )
        except Exception as exc:
            return f"Approval failed: {exc}"
        if not result.get("ok"):
            return "Approval failed: " + str(result.get("error") or "unknown error")

        log_operator_event(
            normalized_phone,
            source_kind="brain",
            source_id=task_id,
            status="approved" if dec == "approved" else "rejected",
            title=f"Brain task {target_ref}",
            detail=f"{target_ref} {dec}.",
            recommendations=["check status", "brief me"],
        )
        resolve_operator_event(normalized_phone, "brain", task_id)
        return f"{target_ref} {dec}. {'Queued for execution.' if dec == 'approved' else 'It will not run.'}"

    approval = _pending_owner_approval_for_ref(normalized_phone, target_ref=target_ref)
    if not approval:
        return "There is no pending owner approval for that task."

    conn = _conn()
    try:
        conn.execute(
            """
            UPDATE sms_control_owner_approvals
            SET status = ?, decided_at = ?, decision_note = ?
            WHERE id = ?
            """,
            (dec, _utc_now(), str(note or "")[:500], int(approval["id"])),
        )
        next_status = "open" if dec == "approved" else "cancelled"
        progress_note = (
            "Owner approved via SMS control."
            if dec == "approved"
            else "Owner rejected via SMS control."
        )
        conn.execute(
            """
            UPDATE task_board
            SET status = ?, progress_note = ?, updated_at = ?
            WHERE id = ? AND created_by = ?
            """,
            (next_status, progress_note, _utc_now(), int(approval["target_id"]), control_identity(normalized_phone)),
        )
        conn.commit()
    finally:
        conn.close()

    log_operator_event(
        normalized_phone,
        source_kind="task_board",
        source_id=int(approval["target_id"]),
        status="approved" if dec == "approved" else "rejected",
        title=approval["summary"],
        detail=f"{approval['target_ref']} {dec}.",
        recommendations=["check status", "brief me"],
    )
    resolve_operator_event(normalized_phone, "task_board", int(approval["target_id"]))
    resolve_operator_event(normalized_phone, "approval", int(approval["id"]))
    return (
        f"{approval['target_ref']} {dec}.\n"
        f"{'Task is now queued for execution.' if dec == 'approved' else 'Task has been cancelled.'}"
    )


def format_help() -> str:
    return (
        "SMS control ready.\n"
        "Use RUN: <local command> for deterministic Mac actions.\n"
        "Use ORCH: <task> for agent work, CODEX: <edit request> for repo changes, ADS: <ad request>, and LEADS: <lead request>.\n"
        "Direct open/launch/go-to website commands also work.\n"
        "Examples: RUN: email me status || body, CODEX: change the Facebook ad headline, LEADS: find 25 wedding venue leads in Ventura.\n"
        "Voice calls only target your owner control number. Twilio supports spoken prompts; the Google Voice daemon can place alert calls from the browser session.\n"
        "Commands: STATUS, TASK <id>, TASK B#<id>, APPROVALS, APPROVE <ref>, REJECT <ref>, HELP.\n"
        "If the orchestrator goes idle, I will text you for the next task."
    )


def format_strict_mode_hint() -> str:
    return (
        "SMS control is in strict mode to save usage.\n"
        "Use RUN:, ORCH:, CODEX:, ADS:, or LEADS:.\n"
        "Direct open/launch/go-to website, email me, call me, restart voice daemon, and natural owner commands like find me leads also work when the route is clear.\n"
        "Reply HELP for examples."
    )


def format_status(phone: str) -> str:
    sync_operator_events(phone)
    tasks = get_recent_tasks(phone)
    approvals = get_pending_owner_approvals(phone)
    brain_approvals = _brain_pending_approvals(phone)
    if not tasks:
        if approvals or brain_approvals:
            return format_pending_approvals(phone)
        return "No SMS tasks yet. Use RUN:, ORCH:, CODEX:, ADS:, or LEADS:."

    lines = ["Recent SMS tasks:"]
    for task in tasks[:MAX_STATUS_TASKS]:
        label = _status_label(task.get("status", ""))
        lines.append(f"{task['ref']} {label}: {task.get('title', '')[:80]}")
    if approvals or brain_approvals:
        lines.append(f"Pending approvals: {len(approvals) + len(brain_approvals)}")
    lines.append("Reply TASK <id> or TASK B#<id> for detail. Use RUN: or ORCH: for the next task.")
    return "\n".join(lines)


def build_operator_snapshot(phone: str, limit: int = VOICE_OPERATOR_SCAN_LIMIT) -> Dict[str, Any]:
    normalized_phone = normalize_phone(phone)
    sync_operator_events(normalized_phone)
    tasks = get_recent_tasks(normalized_phone, limit=max(int(limit or 1) * 2, MAX_STATUS_TASKS))
    state = _get_orchestrator_state(normalized_phone)
    events = get_recent_operator_events(normalized_phone, limit=max(MAX_OPERATOR_EVENTS, int(limit or 1) * 2))

    enriched_tasks: List[Dict[str, Any]] = []
    for task in tasks:
        row = dict(task)
        row["voice_status"] = _voice_status_label(row)
        row["voice_note"] = _clean_progress_note(str(row.get("progress_note") or ""))
        row["status_bucket"] = _status_bucket(str(row.get("status") or ""))
        row["verified"] = _is_verified_completion(row)
        enriched_tasks.append(row)

    latest_event = next(
        (
            event for event in events
            if _status_bucket(str(event.get("status") or "")) in {"done", "failed", "blocked", "waiting"}
        ),
        None,
    )
    latest_completion = _operator_event_to_task(latest_event) if latest_event else next(
        (task for task in enriched_tasks if task["status_bucket"] in {"done", "failed", "blocked", "waiting"}),
        None,
    )
    active_tasks = [task for task in enriched_tasks if task["status_bucket"] in {"queued", "running"}][:3]
    blocked_tasks = [task for task in enriched_tasks if task["status_bucket"] in {"blocked", "failed", "waiting"}][:2]
    waiting_event = next(
        (
            event for event in events
            if bool(event.get("requires_response")) or _status_bucket(str(event.get("status") or "")) == "waiting"
        ),
        None,
    )

    agents: List[Dict[str, Any]] = []
    active_agents: List[Dict[str, Any]] = []
    try:
        cp = _get_brain_control_plane()
        agents = cp.list_agents(limit=VOICE_AGENT_SCAN_LIMIT)
        active_agents = [
            dict(agent)
            for agent in agents
            if str(agent.get("status") or "").lower() in {"running", "working", "busy", "claimed"}
            or int(agent.get("current_task_id") or 0) > 0
        ]
    except Exception as exc:
        log.warning("Operator snapshot agent lookup failed: %s", exc)

    recommendations = list(waiting_event.get("recommendations", [])) if waiting_event else []
    if not recommendations:
        recommendations = _operator_recommendations(
            latest_completion,
            active_tasks,
            blocked_tasks,
            bool(int(state.get("awaiting_reply") or 0)),
        )

    waiting_text = ""
    if waiting_event:
        waiting_text = str(waiting_event.get("detail") or waiting_event.get("title") or "").strip()
    elif int(state.get("awaiting_reply") or 0):
        waiting_text = "I am waiting on your next instruction."
    elif blocked_tasks:
        waiting_text = "Decision needed: " + _voice_task_sentence(blocked_tasks[0], include_note=True)
    elif not active_tasks and not active_agents:
        waiting_text = "Nothing is blocked and I am ready for the next instruction."

    return {
        "phone": normalized_phone,
        "tasks": enriched_tasks,
        "latest_completion": latest_completion,
        "active_tasks": active_tasks,
        "blocked_tasks": blocked_tasks,
        "agents": agents,
        "active_agents": active_agents,
        "events": events,
        "waiting_event": waiting_event,
        "awaiting_reply": bool(int(state.get("awaiting_reply") or 0)),
        "waiting_text": waiting_text,
        "recommendations": recommendations,
    }


def format_voice_latest_completion(phone: str) -> str:
    snapshot = build_operator_snapshot(phone)
    latest = snapshot.get("latest_completion")
    if not latest:
        return "Nothing has finished recently."
    return "Just finished: " + _voice_task_sentence(dict(latest), include_note=True)


def format_voice_active_work(phone: str) -> str:
    snapshot = build_operator_snapshot(phone)
    active_tasks = [dict(task) for task in snapshot.get("active_tasks", [])]
    active_agents = [dict(agent) for agent in snapshot.get("active_agents", [])]

    if not active_tasks and not active_agents:
        return "Nothing is running right now. The agent fleet is idle."

    parts: List[str] = []
    if active_tasks:
        parts.append(
            "Active now: " + "; ".join(_voice_task_brief_label(task) for task in active_tasks) + "."
        )
    if active_agents:
        labels = []
        for agent in active_agents[:3]:
            agent_id = str(agent.get("agent_id") or "agent").replace("_", " ")
            task_id = int(agent.get("current_task_id") or 0)
            if task_id > 0:
                labels.append(f"{agent_id} on task {task_id}")
            else:
                labels.append(agent_id)
        parts.append("Agents working now: " + ", ".join(labels) + ".")
    return " ".join(parts)


def format_voice_decision_prompt(phone: str) -> str:
    snapshot = build_operator_snapshot(phone)
    waiting_text = str(snapshot.get("waiting_text") or "").strip()
    if waiting_text:
        recommendations = snapshot.get("recommendations", [])
        if recommendations:
            return f"{waiting_text} Best next actions: {_join_voice_options(list(recommendations))}."
        return waiting_text
    return "Nothing is blocked. You can give me the next instruction anytime."


def format_voice_operator_brief(phone: str) -> str:
    snapshot = build_operator_snapshot(phone)
    parts: List[str] = []

    latest = snapshot.get("latest_completion")
    if latest:
        parts.append(format_voice_latest_completion(phone))
    else:
        parts.append("Nothing just finished.")

    active_text = format_voice_active_work(phone)
    if active_text:
        parts.append(active_text)

    waiting_text = str(snapshot.get("waiting_text") or "").strip()
    if waiting_text:
        parts.append(waiting_text)
    elif snapshot.get("blocked_tasks"):
        blocked = dict(snapshot["blocked_tasks"][0])
        parts.append("Blocked: " + _voice_task_sentence(blocked, include_note=True))
    else:
        parts.append("Nothing is blocked.")

    recommendations = list(snapshot.get("recommendations", []))
    if recommendations:
        parts.append("Best next actions: " + _join_voice_options(recommendations) + ".")

    return " ".join(part for part in parts if part).strip()


def build_voice_summary_email(phone: str) -> Tuple[str, str]:
    snapshot = build_operator_snapshot(phone)
    subject = "Nexus voice operator summary"

    lines = ["Nexus voice operator summary", ""]
    lines.append(format_voice_latest_completion(phone))
    lines.append(format_voice_active_work(phone))
    lines.append(format_voice_decision_prompt(phone))

    recommendations = list(snapshot.get("recommendations", []))
    if recommendations:
        lines.append("Recommended next actions: " + ", ".join(recommendations) + ".")

    latest = snapshot.get("latest_completion")
    if latest:
        lines.append("Latest completion detail: " + _voice_task_sentence(dict(latest), include_note=True))

    active_tasks = [dict(task) for task in snapshot.get("active_tasks", [])]
    if active_tasks:
        lines.append("Active tasks:")
        for task in active_tasks:
            lines.append(f"- {_voice_task_sentence(task, include_note=True)}")

    blocked_tasks = [dict(task) for task in snapshot.get("blocked_tasks", [])]
    if blocked_tasks:
        lines.append("Blocked or failed:")
        for task in blocked_tasks:
            lines.append(f"- {_voice_task_sentence(task, include_note=True)}")

    return subject, "\n".join(line for line in lines if line.strip() or line == "")


def summarize_recent_activity_for_voice(phone: str, limit: int = 3) -> str:
    snapshot = build_operator_snapshot(phone, limit=max(1, int(limit or 1)))
    latest = snapshot.get("latest_completion")
    if latest:
        return format_voice_latest_completion(phone)

    active_tasks = [dict(task) for task in snapshot.get("active_tasks", [])]
    if active_tasks:
        return format_voice_active_work(phone)

    return "I am online on your Mac mini. There are no recent SMS or orchestrator tasks."


def send_voice_summary_email(phone: str, source_name: str = "voice_call", raw_body: str = "email me the summary") -> str:
    subject, body = build_voice_summary_email(phone)
    control_text = f"email me {subject} || {body}"
    return _complete_local_action(
        phone,
        control_text,
        source_name=source_name,
        raw_body=raw_body,
        executor=lambda: _send_self_email(subject, body),
    )


def handle_voice_operator_request(phone: str, body: str, source_name: str = "voice_call") -> str:
    normalized_phone = normalize_phone(phone)
    text = str(body or "").strip()
    text_norm = normalize_body(text)

    if not normalized_phone:
        return "I could not identify the caller number."
    if not is_authorized_voice_phone(normalized_phone):
        return "Voice control is only available to your owner control number."
    if not text:
        return (
            "I did not catch that. Say brief me, email me the summary, open the dashboard, "
            "or tell me the next task."
        )
    if text_norm in {"bye", "goodbye", "hang up", "hangup", "stop", "cancel"}:
        return "Okay. Hanging up now."
    if text_norm in {
        "brief",
        "brief me",
        "status",
        "what is happening",
        "what's happening",
        "what is happening now",
        "what's happening now",
        "what is going on",
        "what's going on",
    }:
        return format_voice_operator_brief(normalized_phone)
    if text_norm in {"what just finished", "what finished", "latest completion", "what completed"}:
        return format_voice_latest_completion(normalized_phone)
    if text_norm in {"what are the agents doing", "what are agents doing", "what is running", "what's running", "active work"}:
        return format_voice_active_work(normalized_phone)
    if text_norm in {"what should i do next", "next action", "next actions", "decision prompt", "what is blocked", "what's blocked"}:
        return format_voice_decision_prompt(normalized_phone)
    if any(
        phrase in text_norm
        for phrase in (
            "email me the summary",
            "email me summary",
            "email me a summary",
            "email me the brief",
            "email me the status",
            "email me that",
        )
    ):
        return send_voice_summary_email(normalized_phone, source_name=source_name, raw_body=text)
    return handle_inbound_voice_command(normalized_phone, text, source_name=source_name)


def format_task_detail(phone: str, task_id: int, task_kind: str = "") -> str:
    task = get_task_detail(phone, task_id, task_kind=task_kind)
    if not task:
        return f"Task {(_brain_task_ref(task_id) if task_kind == 'brain' else '#' + str(task_id))} not found."

    note = str(task.get("progress_note") or "").strip()
    if note:
        note = re.sub(r"\s+", " ", note)[:240]
    else:
        note = "No progress note yet."

    return (
        f"Task {task['ref']} - {_status_label(task.get('status', ''))}\n"
        f"{task.get('title', '')[:120]}\n"
        f"Progress: {note}\n"
        "Reply STATUS for the queue."
    )


def format_queued_task(phone: str, task_id: int, body: str) -> str:
    task_type, _ = classify_task(body)
    return (
        f"Queued task #{task_id} ({task_type}).\n"
        f"{_task_title(body)}\n"
        "I will text when it is done. Reply STATUS anytime."
    )


def format_queued_brain_task(task_id: int, body: str, lane: str) -> str:
    return (
        f"Queued brain task {_brain_task_ref(task_id)} ({_lane_label(lane)}).\n"
        f"{_task_title(body)}\n"
        "The orchestrator will route it. Reply STATUS anytime."
    )


def format_prepared_code_task(task_ref: str, body: str, files_involved: List[str]) -> str:
    file_line = ""
    if files_involved:
        file_line = "Files: " + ", ".join(files_involved[:4]) + "\n"
    return (
        f"Prepared code task {task_ref}.\n"
        f"{_task_title(body)}\n"
        f"{file_line}"
        f"Approval needed. Reply APPROVE {task_ref} or REJECT {task_ref}."
    ).strip()


def _operator_event_to_task(event: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": int(event.get("source_id") or event.get("id") or 0),
        "ref": str(event.get("response_target") or event.get("target_ref") or f"#{int(event.get('source_id') or 0)}"),
        "title": str(event.get("title") or "").strip(),
        "status": str(event.get("status") or "").strip(),
        "progress_note": str(event.get("detail") or event.get("proof") or "").strip(),
        "updated_at": str(event.get("updated_at") or event.get("created_at") or "").strip(),
        "task_kind": str(event.get("source_kind") or "operator_event"),
    }


def mark_task_notification_sent(phone: str, task_id: int, status: str) -> None:
    conn = _conn()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO sms_control_notifications (phone, task_id, task_status, sent_at)
            VALUES (?, ?, ?, ?)
            """,
            (normalize_phone(phone), int(task_id), str(status or ""), _utc_now()),
        )
        conn.commit()
    finally:
        conn.close()


def mark_brain_task_notification_sent(phone: str, task_id: int, status: str) -> None:
    conn = _conn()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO sms_control_brain_notifications (phone, brain_task_id, task_status, sent_at)
            VALUES (?, ?, ?, ?)
            """,
            (normalize_phone(phone), int(task_id), str(status or ""), _utc_now()),
        )
        conn.commit()
    finally:
        conn.close()


def _parse_owner_mode(text: str) -> Tuple[str, str]:
    match = re.match(
        r"^\s*(run|local|orch|orchestrator|brain|codex(?:\s+now)?|ads|leads)\s*:\s*(.*)$",
        str(text or "").strip(),
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return "", ""
    raw_mode = normalize_body(match.group(1))
    payload = str(match.group(2) or "").strip()
    if raw_mode in {"run", "local"}:
        return "run", payload
    if raw_mode in {"orch", "orchestrator", "brain"}:
        return "orch", payload
    if raw_mode == "codex now":
        return "codex_now", payload
    return raw_mode, payload


def _looks_like_code_edit(text: str) -> bool:
    lowered = normalize_body(text)
    edit_tokens = ("change", "update", "edit", "rewrite", "fix", "patch", "modify", "refactor")
    subject_tokens = (
        "facebook ad",
        "ad copy",
        "ad headline",
        "website",
        "homepage",
        "landing page",
        "voice",
        "sms",
        "daemon",
        "dashboard",
        "code",
        "repo",
        "file",
        "script",
    )
    return any(token in lowered for token in edit_tokens) and any(token in lowered for token in subject_tokens)


def _looks_like_ads_request(text: str) -> bool:
    lowered = normalize_body(text)
    return any(token in lowered for token in ("facebook ad", "facebook ads", "meta ad", "ad copy", "campaign", "creative", "hook"))


def _looks_like_leads_request(text: str) -> bool:
    lowered = normalize_body(_expand_owner_shorthand(text))
    has_request_verb = any(token in lowered for token in LEAD_REQUEST_VERBS)
    has_entity = any(token in lowered for token in LEAD_ENTITY_TOKENS)
    has_location = any(token in lowered for token in LEAD_LOCATION_TOKENS) or bool(
        re.search(r"\bin\s+[a-z][a-z\s]+$", lowered)
    )
    has_count = bool(re.search(r"\b\d{1,3}\b", lowered))
    has_lead_action = any(token in lowered for token in LEAD_ACTION_TOKENS)

    if has_entity and (has_request_verb or has_lead_action):
        return True
    if has_request_verb and (has_location or has_count) and (
        has_entity or "lead" in lowered or "prospect" in lowered or "business" in lowered
    ):
        return True
    if has_location and has_lead_action and (has_entity or has_request_verb):
        return True
    return False


def _wants_immediate_execution(text: str) -> bool:
    lowered = normalize_body(text)
    return any(token in lowered for token in ("do it now", "ship it now", "codex now:", "run it now", "execute now"))


def route_owner_sms_request(phone: str, text: str, source_name: str = "") -> Optional[str]:
    normalized_phone = normalize_phone(phone)
    payload = str(text or "").strip()
    expanded_payload = _expand_owner_shorthand(payload)
    text_norm = normalize_body(payload)
    mode, explicit_payload = _parse_owner_mode(payload)

    if text_norm in {"approvals", "pending approvals", "what needs approval", "what needs my approval"}:
        record_inbound(normalized_phone, payload, source_name=source_name, task_id=0)
        sync_operator_events(normalized_phone)
        return format_pending_approvals(normalized_phone)

    decision = _parse_decision_command(payload)
    if decision:
        record_inbound(normalized_phone, payload, source_name=source_name, task_id=0)
        return decide_owner_task_approval(
            normalized_phone,
            decision=decision["decision"],
            target_ref=decision["ref"],
            note=decision["note"],
        )

    if mode in {"codex", "codex_now"}:
        request_body = explicit_payload or payload
        if not request_body:
            record_inbound(normalized_phone, payload, source_name=source_name, task_id=0)
            return "CODEX needs an edit request.\nExample: CODEX: change the Facebook ad headline"
        queued = queue_owner_code_task(
            normalized_phone,
            request_body,
            source_name=source_name,
            requires_approval=(mode != "codex_now"),
        )
        record_inbound(normalized_phone, payload, source_name=source_name, task_id=int(queued["task_id"]))
        if queued["requires_approval"]:
            return format_prepared_code_task(queued["task_ref"], request_body, queued["files_involved"])
        return (
            f"Queued code task {queued['task_ref']}.\n"
            f"{_task_title(request_body)}\n"
            "The task worker can pick it up immediately. Reply STATUS anytime."
        )

    if mode == "ads":
        request_body = explicit_payload or payload
        if _looks_like_code_edit(request_body):
            queued = queue_owner_code_task(
                normalized_phone,
                request_body,
                source_name=source_name,
                requires_approval=not _wants_immediate_execution(payload),
            )
            record_inbound(normalized_phone, payload, source_name=source_name, task_id=int(queued["task_id"]))
            if queued["requires_approval"]:
                return format_prepared_code_task(queued["task_ref"], request_body, queued["files_involved"])
            return f"Queued code task {queued['task_ref']} for the ad change."
        return _queue_brain_task_from_sms(
            normalized_phone,
            request_body,
            source_name=source_name,
            raw_body=payload,
            clear_orchestrator_wait=_is_waiting_for_orchestrator_reply(normalized_phone),
        )

    if mode == "leads":
        request_body = _expand_owner_shorthand(explicit_payload or expanded_payload or payload)
        return _queue_brain_task_from_sms(
            normalized_phone,
            request_body,
            source_name=source_name,
            raw_body=payload,
            clear_orchestrator_wait=_is_waiting_for_orchestrator_reply(normalized_phone),
        )

    if _looks_like_leads_request(payload):
        return _queue_brain_task_from_sms(
            normalized_phone,
            expanded_payload or payload,
            source_name=source_name,
            raw_body=payload,
            clear_orchestrator_wait=_is_waiting_for_orchestrator_reply(normalized_phone),
        )

    if _looks_like_ads_request(payload):
        if _looks_like_code_edit(payload):
            queued = queue_owner_code_task(
                normalized_phone,
                payload,
                source_name=source_name,
                requires_approval=not _wants_immediate_execution(payload),
            )
            record_inbound(normalized_phone, payload, source_name=source_name, task_id=int(queued["task_id"]))
            if queued["requires_approval"]:
                return format_prepared_code_task(queued["task_ref"], payload, queued["files_involved"])
            return f"Queued code task {queued['task_ref']} for the ad change."
        return _queue_brain_task_from_sms(
            normalized_phone,
            payload,
            source_name=source_name,
            raw_body=payload,
            clear_orchestrator_wait=_is_waiting_for_orchestrator_reply(normalized_phone),
        )

    if _looks_like_code_edit(payload):
        queued = queue_owner_code_task(
            normalized_phone,
            payload,
            source_name=source_name,
            requires_approval=not _wants_immediate_execution(payload),
        )
        record_inbound(normalized_phone, payload, source_name=source_name, task_id=int(queued["task_id"]))
        if queued["requires_approval"]:
            return format_prepared_code_task(queued["task_ref"], payload, queued["files_involved"])
        return f"Queued code task {queued['task_ref']}."

    return None


def get_pending_completion_notifications(phone: str, limit: int = 3) -> List[str]:
    conn = _conn()
    try:
        rows = conn.execute(
            """
            SELECT t.id, t.title, t.status, t.progress_note
            FROM task_board t
            LEFT JOIN sms_control_notifications n
              ON n.task_id = t.id AND n.phone = ? AND n.task_status = t.status
            WHERE t.created_by = ?
              AND t.status IN ('done', 'failed', 'review_needed', 'blocked')
              AND n.id IS NULL
            ORDER BY t.updated_at ASC, t.id ASC
            LIMIT ?
            """,
            (normalize_phone(phone), control_identity(phone), int(limit)),
        ).fetchall()

        messages = []
        for row in rows:
            status = _status_label(row["status"])
            note = re.sub(r"\s+", " ", str(row["progress_note"] or "").strip())[:220]
            if not note:
                note = "No extra note."
            messages.append(
                f"Task #{row['id']} {status}.\n"
                f"{str(row['title'] or '')[:120]}\n"
                f"{note}\n"
                f"{format_next_step_prompt()}"
            )
        return messages
    finally:
        conn.close()


def get_pending_brain_completion_notifications(phone: str, limit: int = 3) -> List[Dict[str, Any]]:
    tasks = _recent_brain_tasks(phone, limit=max(limit * 4, MAX_STATUS_TASKS))
    conn = _conn()
    try:
        messages: List[Dict[str, Any]] = []
        for task in tasks:
            raw_status = str(task.get("status") or "").lower()
            if raw_status not in {"completed", "failed", "cancelled"}:
                continue
            sent = conn.execute(
                """
                SELECT 1
                FROM sms_control_brain_notifications
                WHERE phone = ? AND brain_task_id = ? AND task_status = ?
                LIMIT 1
                """,
                (normalize_phone(phone), int(task["id"]), raw_status),
            ).fetchone()
            if sent:
                continue

            note = re.sub(r"\s+", " ", str(task.get("progress_note") or "").strip())[:220]
            if not note:
                note = "No extra note."
            messages.append(
                {
                    "task_id": int(task["id"]),
                    "status": raw_status,
                    "message": (
                        f"Brain task {task['ref']} {_status_label(raw_status)}.\n"
                        f"{str(task.get('title') or '')[:120]}\n"
                        f"{note}\n"
                        f"{format_next_step_prompt()}"
                    ),
                }
            )
            if len(messages) >= limit:
                break
        return messages
    finally:
        conn.close()


def get_pending_operator_event_notifications(phone: str, limit: int = 3) -> List[Dict[str, Any]]:
    sync_operator_events(phone)
    events = get_recent_operator_events(phone, limit=max(limit * 4, MAX_OPERATOR_EVENTS))
    messages: List[Dict[str, Any]] = []
    seen_keys = set()
    for event in events:
        if event.get("notified_sms"):
            continue
        status = str(event.get("status") or "").lower()
        if not (
            bool(event.get("requires_response"))
            or str(event.get("severity") or "").lower() == "critical"
            or status in {"approved", "rejected"}
        ):
            continue
        ref = str(event.get("response_target") or event.get("target_ref") or "").strip()
        dedupe_key = (
            f"approval:{ref.upper()}"
            if ref and bool(event.get("requires_response"))
            else f"{str(event.get('source_kind') or '').lower()}:{int(event.get('source_id') or 0)}:{status}"
        )
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        message = f"{event.get('title') or 'Operator event'}\n{event.get('detail') or ''}".strip()
        if ref and bool(event.get("requires_response")):
            message += f"\nReply APPROVE {ref} or REJECT {ref}."
        messages.append({"event_id": int(event["id"]), "message": message})
        if len(messages) >= limit:
            break
    return messages


def get_pending_critical_voice_alerts(phone: str, limit: int = MAX_VOICE_ALERTS) -> List[Dict[str, Any]]:
    sync_operator_events(phone)
    events = get_recent_operator_events(phone, limit=max(limit * 4, MAX_OPERATOR_EVENTS))
    alerts: List[Dict[str, Any]] = []
    for event in events:
        if event.get("notified_voice"):
            continue
        if str(event.get("severity") or "").lower() != "critical":
            continue
        status = str(event.get("status") or "").lower()
        if status not in {"failed", "blocked", "waiting_on_kai", "verified_done", "done"}:
            continue
        title = str(event.get("title") or "operator event").strip()
        detail = _clean_progress_note(str(event.get("detail") or event.get("proof") or ""), limit=180)
        message = f"Nexus alert. {_status_label(status)}. {title}."
        if detail:
            message += f" {detail}."
        alerts.append({"event_id": int(event["id"]), "message": message})
        if len(alerts) >= limit:
            break
    return alerts


def get_orchestrator_idle_prompt(phone: str) -> Optional[str]:
    normalized = normalize_phone(phone)
    state = _get_orchestrator_state(normalized)

    try:
        cp = _get_brain_control_plane()
        tasks = cp.list_tasks(limit=MAX_BRAIN_TASK_SCAN)
        agents = cp.list_agents(limit=100)
    except Exception as exc:
        log.warning("Orchestrator idle check failed: %s", exc)
        return None

    active_tasks = [t for t in tasks if str(t.get("status") or "").lower() in {"queued", "claimed", "running"}]
    active_agents = [
        agent for agent in agents
        if str(agent.get("status") or "").lower() == "running" or int(agent.get("current_task_id") or 0) > 0
    ]

    if active_tasks or active_agents:
        updates: Dict[str, Any] = {}
        if state.get("brain_idle_since"):
            updates["brain_idle_since"] = ""
        if int(state.get("awaiting_reply") or 0):
            updates["awaiting_reply"] = 0
            updates["active_prompt_kind"] = ""
        if updates:
            _update_orchestrator_state(normalized, **updates)
        return None

    idle_since = _parse_timestamp(str(state.get("brain_idle_since") or ""))
    if not idle_since:
        _update_orchestrator_state(normalized, brain_idle_since=_utc_now())
        return None

    idle_seconds = (datetime.utcnow() - idle_since.replace(tzinfo=None)).total_seconds()
    if idle_seconds < BRAIN_IDLE_STABLE_SECONDS:
        return None

    if int(state.get("awaiting_reply") or 0):
        return None

    last_prompt = _parse_timestamp(str(state.get("last_prompt_sent_at") or ""))
    if last_prompt and (datetime.utcnow() - last_prompt.replace(tzinfo=None)) < timedelta(minutes=BRAIN_IDLE_PROMPT_COOLDOWN_MINUTES):
        return None

    agent_count = len(agents) or 1
    return (
        f"All {agent_count} brain agents are idle and the orchestrator queue is empty.\n"
        "Reply with the next task and I will route it."
    )


def mark_orchestrator_prompt_sent(phone: str, prompt_kind: str = "brain_idle") -> None:
    _update_orchestrator_state(
        phone,
        awaiting_reply=1,
        active_prompt_kind=str(prompt_kind or "brain_idle"),
        last_prompt_sent_at=_utc_now(),
    )


def _handle_brain_orchestrator_reply(phone: str, text: str, source_name: str = "") -> Optional[str]:
    if not _is_waiting_for_orchestrator_reply(phone):
        return None
    return _queue_brain_task_from_sms(
        phone,
        text,
        source_name=source_name,
        raw_body=text,
        clear_orchestrator_wait=True,
    )


def handle_inbound_sms(phone: str, body: str, source_name: str = "") -> Optional[str]:
    normalized_phone = normalize_phone(phone)
    text = str(body or "").strip()
    text_norm = normalize_body(text)

    if not normalized_phone or not text:
        return None

    if not is_authorized_phone(normalized_phone):
        log.info("Ignoring unauthorized SMS control attempt from %s", normalized_phone)
        return None

    if not should_accept_inbound(normalized_phone, text):
        log.info("Suppressing duplicate SMS command from %s: %s", normalized_phone, text_norm)
        return None

    sync_operator_events(normalized_phone)

    upper = text_norm.upper()
    if upper in {"HELP", "?", "MENU"}:
        record_inbound(normalized_phone, text, source_name=source_name, task_id=0)
        return format_help()

    if upper in {"STATUS", "TASKS", "QUEUE"}:
        record_inbound(normalized_phone, text, source_name=source_name, task_id=0)
        return format_status(normalized_phone)

    match = re.fullmatch(r"(task|show)\s+(?:(b)\s*)?#?(\d+)", text_norm)
    if match:
        task_kind = "brain" if match.group(2) else ""
        task_id = int(match.group(3))
        record_inbound(normalized_phone, text, source_name=source_name, task_id=task_id)
        return format_task_detail(normalized_phone, task_id, task_kind=task_kind)

    owner_response = route_owner_sms_request(normalized_phone, text, source_name=source_name)
    if owner_response:
        return owner_response

    local_open_response = _handle_local_control_command(
        normalized_phone,
        text,
        source_name=source_name,
        raw_body=text,
    )
    if local_open_response:
        return local_open_response

    mode, payload = _extract_control_mode(text)
    if mode == "run":
        if not payload:
            record_inbound(normalized_phone, text, source_name=source_name, task_id=0)
            return "RUN needs a command.\nExample: RUN: open zoar bathroom website"
        run_response = _handle_local_control_command(
            normalized_phone,
            payload,
            source_name=source_name,
            raw_body=text,
        )
        if run_response:
            return run_response
        record_inbound(normalized_phone, text, source_name=source_name, task_id=0)
        return (
            "RUN supports deterministic local commands like open, email me, and call me.\n"
            "Examples: RUN: open zoar bathroom website | RUN: email me status || body\n"
            "For agent work, use ORCH: <task>."
        )

    if mode == "orch":
        if not payload:
            record_inbound(normalized_phone, text, source_name=source_name, task_id=0)
            return "ORCH needs a task.\nExample: ORCH: debug the booking flow"
        return _queue_brain_task_from_sms(
            normalized_phone,
            payload,
            source_name=source_name,
            raw_body=text,
            clear_orchestrator_wait=_is_waiting_for_orchestrator_reply(normalized_phone),
        )

    brain_reply_response = _handle_brain_orchestrator_reply(normalized_phone, text, source_name=source_name)
    if brain_reply_response:
        return brain_reply_response

    record_inbound(normalized_phone, text, source_name=source_name, task_id=0)
    return format_strict_mode_hint()


def handle_inbound_voice_command(phone: str, body: str, source_name: str = "voice_call") -> str:
    normalized_phone = normalize_phone(phone)
    text = str(body or "").strip()
    text_norm = normalize_body(text)

    if not normalized_phone:
        return "I could not identify the caller number."
    if not is_authorized_voice_phone(normalized_phone):
        return "Voice control is only available to your owner control number."
    if not text:
        return (
            "I did not catch that. Say status, email me a summary, open the Zoar website, "
            "or tell me the next task."
        )
    if text_norm in {"bye", "goodbye", "hang up", "hangup", "stop", "cancel"}:
        return "Okay. Hanging up now."

    response = handle_inbound_sms(normalized_phone, text, source_name=source_name)
    if response is None:
        return "That sounded like a recent duplicate, so I ignored it. What would you like next?"
    if response == format_strict_mode_hint():
        return _queue_brain_task_from_sms(
            normalized_phone,
            text,
            source_name=source_name,
            raw_body=text,
            clear_orchestrator_wait=_is_waiting_for_orchestrator_reply(normalized_phone),
        )
    return response
