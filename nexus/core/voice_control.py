from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from html import escape as xml_escape
from pathlib import Path
from typing import Dict, Mapping, Optional
from urllib.parse import parse_qs, urlencode

from core import sms_control

log = logging.getLogger("voice_control")

CONFIG_PATH = Path.home() / ".nexus" / "config.json"
DEFAULT_VOICE_PORT = 8790
VOICE_DEFAULT_PACE = "normal"
VOICE_SESSION_TABLE = "voice_control_sessions"
VOICE_CALL_EVENT_TABLE = "voice_control_call_events"
VOICE_GATHER_TIMEOUT_SECONDS = 4
VOICE_HINTS = (
    "brief me,what just finished,what are the agents doing,email me the summary,"
    "open the zoar website,open the dashboard,repeat that,slow down,approve,reject,pending approvals,hang up"
)


@dataclass
class VoiceSessionState:
    call_sid: str
    phone: str
    pace: str = VOICE_DEFAULT_PACE
    turns: int = 0
    last_prompt: str = ""
    last_briefing: str = ""
    last_result: str = ""
    repeatable_text: str = ""
    last_intent: str = ""
    stream_notice_sent: bool = False
    partial_utterance: str = ""


@dataclass
class VoiceTurnResult:
    spoken_text: str
    repeatable_text: str = ""
    hangup: bool = False
    menu_style: str = "short"


def _load_config() -> Dict[str, object]:
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def load_voice_port() -> int:
    cfg = _load_config()
    raw = cfg.get("voice_control_port") or os.environ.get("VOICE_CONTROL_PORT") or DEFAULT_VOICE_PORT
    try:
        port = int(raw)
    except Exception:
        port = DEFAULT_VOICE_PORT
    return max(1024, min(port, 65535))


def load_voice_stream_port() -> int:
    cfg = _load_config()
    raw = (
        cfg.get("voice_control_stream_port")
        or os.environ.get("VOICE_CONTROL_STREAM_PORT")
        or load_voice_port()
    )
    try:
        port = int(raw)
    except Exception:
        port = load_voice_port()
    return max(1024, min(port, 65535))


def load_voice_stream_public_url() -> str:
    cfg = _load_config()
    explicit = str(
        cfg.get("voice_control_stream_public_url")
        or cfg.get("voice_stream_public_url")
        or ""
    ).strip()
    if explicit:
        return explicit

    base = sms_control.load_shared_public_base_url()
    if base.startswith("https://"):
        return "wss://" + base[len("https://"):] + "/voice/stream"
    if base.startswith("http://"):
        return "ws://" + base[len("http://"):] + "/voice/stream"
    return ""


def load_voice_transport() -> str:
    cfg = _load_config()
    raw = str(cfg.get("voice_control_transport") or cfg.get("voice_transport") or "").strip().lower()
    normalized = raw.replace("-", "_").replace(" ", "_")
    if normalized in {"conversationrelay", "conversation_relay", "relay"}:
        return "conversation_relay"
    return "gather"


def load_voice_conversationrelay_public_url() -> str:
    cfg = _load_config()
    explicit = str(
        cfg.get("voice_control_conversationrelay_public_url")
        or cfg.get("voice_conversationrelay_public_url")
        or ""
    ).strip()
    if explicit:
        return explicit

    base = sms_control.load_shared_public_base_url()
    if base.startswith("https://"):
        return "wss://" + base[len("https://"):] + "/voice/conversationrelay"
    if base.startswith("http://"):
        return "ws://" + base[len("http://"):] + "/voice/conversationrelay"
    return ""


def load_voice_conversationrelay_settings() -> Dict[str, str]:
    cfg = _load_config()
    return {
        "language": str(cfg.get("voice_control_language") or cfg.get("voice_language") or "en-US").strip() or "en-US",
        "tts_language": str(cfg.get("voice_control_tts_language") or cfg.get("voice_tts_language") or "").strip(),
        "tts_provider": str(cfg.get("voice_control_tts_provider") or cfg.get("voice_tts_provider") or "").strip(),
        "voice": str(cfg.get("voice_control_tts_voice") or cfg.get("voice_tts_voice") or "").strip(),
        "transcription_language": str(
            cfg.get("voice_control_transcription_language") or cfg.get("voice_transcription_language") or ""
        ).strip(),
        "transcription_provider": str(
            cfg.get("voice_control_transcription_provider") or cfg.get("voice_transcription_provider") or ""
        ).strip(),
        "speech_model": str(cfg.get("voice_control_speech_model") or cfg.get("voice_speech_model") or "").strip(),
        "interruptible": str(cfg.get("voice_control_interruptible") or "any").strip() or "any",
        "interrupt_sensitivity": str(cfg.get("voice_control_interrupt_sensitivity") or "high").strip() or "high",
        "report_input_during_agent_speech": str(
            cfg.get("voice_control_report_input_during_agent_speech") or "speech"
        ).strip()
        or "speech",
        "dtmf_detection": "true" if bool(cfg.get("voice_control_dtmf_detection", True)) else "false",
        "hints": str(cfg.get("voice_control_hints") or VOICE_HINTS).strip() or VOICE_HINTS,
        "welcome_greeting": str(
            cfg.get("voice_control_welcome_greeting") or "Connecting you to Nexus voice control."
        ).strip()
        or "Connecting you to Nexus voice control.",
    }


def parse_form_body(body: bytes) -> Dict[str, str]:
    if not body:
        return {}
    parsed = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
    result: Dict[str, str] = {}
    for key, values in parsed.items():
        result[key] = str(values[0] or "") if values else ""
    return result


def _query_value(query: Mapping[str, object], key: str) -> str:
    value = query.get(key, "")
    if isinstance(value, list):
        return str(value[0] or "") if value else ""
    return str(value or "")


def _voice_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(sms_control.DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {VOICE_SESSION_TABLE} (
            call_sid TEXT PRIMARY KEY,
            phone TEXT DEFAULT '',
            pace TEXT DEFAULT 'normal',
            turns INTEGER DEFAULT 0,
            last_prompt TEXT DEFAULT '',
            last_briefing TEXT DEFAULT '',
            last_result TEXT DEFAULT '',
            repeatable_text TEXT DEFAULT '',
            last_intent TEXT DEFAULT '',
            stream_notice_sent INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {VOICE_CALL_EVENT_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            call_sid TEXT DEFAULT '',
            parent_call_sid TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            from_number TEXT DEFAULT '',
            to_number TEXT DEFAULT '',
            call_status TEXT DEFAULT '',
            answered_by TEXT DEFAULT '',
            direction TEXT DEFAULT '',
            error_code TEXT DEFAULT '',
            error_message TEXT DEFAULT '',
            raw_payload TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    try:
        columns = {
            str(row["name"] or "")
            for row in conn.execute(f"PRAGMA table_info({VOICE_SESSION_TABLE})").fetchall()
        }
        if "stream_notice_sent" not in columns:
            conn.execute(
                f"ALTER TABLE {VOICE_SESSION_TABLE} ADD COLUMN stream_notice_sent INTEGER DEFAULT 0"
            )
            conn.commit()
    except Exception:
        conn.rollback()
    return conn


def _session_id(phone: str, query: Mapping[str, object], form: Mapping[str, str]) -> str:
    call_sid = (
        _query_value(query, "call_sid")
        or str(form.get("CallSid") or "")
        or str(form.get("call_sid") or "")
        or f"phone:{sms_control.normalize_phone(phone) or 'unknown'}"
    )
    return call_sid[:120]


def _load_session(phone: str, query: Mapping[str, object], form: Mapping[str, str]) -> VoiceSessionState:
    normalized_phone = sms_control.normalize_phone(phone)
    call_sid = _session_id(normalized_phone, query, form)
    conn = _voice_conn()
    try:
        conn.execute(
            f"""
            INSERT OR IGNORE INTO {VOICE_SESSION_TABLE} (
                call_sid, phone, pace, turns, last_prompt, last_briefing,
                last_result, repeatable_text, last_intent, stream_notice_sent, created_at, updated_at
            ) VALUES (?, ?, ?, 0, '', '', '', '', '', 0, datetime('now'), datetime('now'))
            """,
            (call_sid, normalized_phone, VOICE_DEFAULT_PACE),
        )
        conn.commit()

        row = conn.execute(
            f"""
            SELECT call_sid, phone, pace, turns, last_prompt, last_briefing,
                   last_result, repeatable_text, last_intent, stream_notice_sent
            FROM {VOICE_SESSION_TABLE}
            WHERE call_sid = ?
            """,
            (call_sid,),
        ).fetchone()
        if row:
            stored_phone = sms_control.normalize_phone(str(row["phone"] or "")) or normalized_phone
            return VoiceSessionState(
                call_sid=str(row["call_sid"] or call_sid),
                phone=stored_phone,
                pace=str(row["pace"] or VOICE_DEFAULT_PACE),
                turns=int(row["turns"] or 0),
                last_prompt=str(row["last_prompt"] or ""),
                last_briefing=str(row["last_briefing"] or ""),
                last_result=str(row["last_result"] or ""),
                repeatable_text=str(row["repeatable_text"] or ""),
                last_intent=str(row["last_intent"] or ""),
                stream_notice_sent=bool(int(row["stream_notice_sent"] or 0)),
            )
    finally:
        conn.close()

    return VoiceSessionState(call_sid=call_sid, phone=normalized_phone)


def record_call_event(form: Mapping[str, str]) -> None:
    conn = _voice_conn()
    try:
        conn.execute(
            f"""
            INSERT INTO {VOICE_CALL_EVENT_TABLE} (
                call_sid, parent_call_sid, phone, from_number, to_number,
                call_status, answered_by, direction, error_code, error_message, raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(form.get("CallSid") or form.get("CallSid".lower()) or "")[:120],
                str(form.get("ParentCallSid") or "")[:120],
                sms_control.normalize_phone(str(form.get("To") or form.get("From") or "")),
                str(form.get("From") or "")[:40],
                str(form.get("To") or "")[:40],
                str(form.get("CallStatus") or "")[:80],
                str(form.get("AnsweredBy") or "")[:120],
                str(form.get("Direction") or "")[:80],
                str(form.get("ErrorCode") or "")[:80],
                str(form.get("ErrorMessage") or "")[:500],
                json.dumps(dict(form), ensure_ascii=False)[:4000],
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _save_session(session: VoiceSessionState) -> None:
    conn = _voice_conn()
    try:
        conn.execute(
            f"""
            INSERT INTO {VOICE_SESSION_TABLE} (
                call_sid, phone, pace, turns, last_prompt, last_briefing,
                last_result, repeatable_text, last_intent, stream_notice_sent, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(call_sid) DO UPDATE SET
                phone = excluded.phone,
                pace = excluded.pace,
                turns = excluded.turns,
                last_prompt = excluded.last_prompt,
                last_briefing = excluded.last_briefing,
                last_result = excluded.last_result,
                repeatable_text = excluded.repeatable_text,
                last_intent = excluded.last_intent,
                stream_notice_sent = excluded.stream_notice_sent,
                updated_at = datetime('now')
            """,
            (
                session.call_sid,
                sms_control.normalize_phone(session.phone),
                session.pace or VOICE_DEFAULT_PACE,
                int(session.turns or 0),
                session.last_prompt[:4000],
                session.last_briefing[:4000],
                session.last_result[:4000],
                session.repeatable_text[:4000],
                session.last_intent[:120],
                1 if session.stream_notice_sent else 0,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _session_count() -> int:
    conn = _voice_conn()
    try:
        row = conn.execute(f"SELECT COUNT(*) AS count FROM {VOICE_SESSION_TABLE}").fetchone()
        return int(row["count"] if row else 0)
    finally:
        conn.close()


def _caller_phone(form: Mapping[str, str], query: Mapping[str, object]) -> str:
    raw = (
        _query_value(query, "phone")
        or str(form.get("From") or "")
        or str(form.get("Caller") or "")
        or str(form.get("To") or "")
    )
    return sms_control.normalize_phone(raw)


def _public_url(endpoint: str, **params: str) -> str:
    base = sms_control.load_voice_public_base_url() or sms_control.VOICE_CONTROL_PATH_PREFIX
    url = f"{base.rstrip('/')}/{endpoint.lstrip('/')}"
    encoded = urlencode({k: v for k, v in params.items() if str(v or "").strip()})
    if encoded:
        url = f"{url}?{encoded}"
    return url


def _local_stream_url() -> str:
    return f"ws://127.0.0.1:{load_voice_stream_port()}/voice/stream"


def _clean_for_voice(text: str) -> str:
    spoken = str(text or "").strip()
    if not spoken:
        return ""

    for marker in ("What should I do next?", "Reply STATUS", "Examples:"):
        if marker in spoken:
            spoken = spoken.split(marker, 1)[0].strip()

    spoken = spoken.replace("\n", ". ")
    spoken = re.sub(r"\bQueued brain task B#(\d+)\b", r"Queued brain task \1", spoken, flags=re.IGNORECASE)
    spoken = re.sub(r"\bBrain task B#(\d+)\b", r"Brain task \1", spoken, flags=re.IGNORECASE)
    spoken = re.sub(r"\bTask #(\d+)\b", r"Task \1", spoken, flags=re.IGNORECASE)
    spoken = re.sub(r"\bB#(\d+)\b", r"brain task \1", spoken)
    spoken = re.sub(r"(?<![A-Za-z])#(\d+)\b", r"task \1", spoken)
    spoken = re.sub(r"https?://([^/\s]+)", r"\1", spoken)
    spoken = re.sub(r"\bRUN:\s*", "", spoken, flags=re.IGNORECASE)
    spoken = re.sub(r"\bORCH:\s*", "", spoken, flags=re.IGNORECASE)
    spoken = re.sub(r"^Task \d+ (done|failed)\.\s*", "", spoken, flags=re.IGNORECASE)
    spoken = re.sub(r"\s+", " ", spoken)
    return spoken[:900].strip(" .")


def _spoken_chunks(text: str) -> list[str]:
    cleaned = _clean_for_voice(text) or "Okay."
    chunks = [chunk.strip() for chunk in re.split(r"(?<=[.!?])\s+", cleaned) if chunk.strip()]
    return chunks or [cleaned]


def _say(text: str, *, pace: str = VOICE_DEFAULT_PACE) -> str:
    if str(pace or "").strip().lower() != "slow":
        return f'<Say voice="alice">{xml_escape(_clean_for_voice(text) or "Okay.")}</Say>'

    chunks = _spoken_chunks(text)
    parts = []
    for idx, chunk in enumerate(chunks):
        parts.append(f'<Say voice="alice">{xml_escape(chunk)}</Say>')
        if idx < len(chunks) - 1:
            parts.append("<Pause length=\"1\"/>")
    return "".join(parts)


def _conversation_relay_twiml(phone: str, *, call_sid: str = "") -> bytes:
    connect_url = load_voice_conversationrelay_public_url()
    settings = load_voice_conversationrelay_settings()
    if not connect_url:
        body = json.dumps({"ok": False, "error": "conversationrelay_public_url_missing"}).encode("utf-8")
        return body

    query = urlencode(
        {
            "phone": sms_control.normalize_phone(phone),
            "call_sid": call_sid[:120],
        }
    )
    attrs = {
        "url": f"{connect_url}?{query}",
        "welcomeGreeting": settings["welcome_greeting"],
        "interruptible": settings["interruptible"],
        "interruptSensitivity": settings["interrupt_sensitivity"],
        "reportInputDuringAgentSpeech": settings["report_input_during_agent_speech"],
        "dtmfDetection": settings["dtmf_detection"],
        "language": settings["language"],
        "hints": settings["hints"],
    }
    optional = {
        "ttsLanguage": settings["tts_language"],
        "ttsProvider": settings["tts_provider"],
        "voice": settings["voice"],
        "transcriptionLanguage": settings["transcription_language"],
        "transcriptionProvider": settings["transcription_provider"],
        "speechModel": settings["speech_model"],
    }
    for key, value in optional.items():
        if value:
            attrs[key] = value

    attr_text = " ".join(f'{name}="{xml_escape(value)}"' for name, value in attrs.items() if value)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Connect><ConversationRelay {attr_text} /></Connect>"
        "</Response>"
    ).encode("utf-8")


def _conversation_relay_text_frames(text: str, *, lang: str = "en-US", interruptible: bool = True) -> list[Dict[str, object]]:
    cleaned = _clean_for_voice(text) or "Okay."
    chunks = _spoken_chunks(cleaned)
    frames: list[Dict[str, object]] = []
    for idx, chunk in enumerate(chunks):
        frames.append(
            {
                "type": "text",
                "token": chunk + (" " if idx < len(chunks) - 1 else ""),
                "last": idx == len(chunks) - 1,
                "interruptible": bool(interruptible),
                "lang": lang,
            }
        )
    return frames


class VoicePromptBuilder:
    @staticmethod
    def short_follow_up() -> str:
        return (
            "What do you want next? "
            "You can also press 1 for a briefing, 2 to email the summary, 3 for the Zoar website, 4 for the dashboard, or 0 to hang up."
        )

    @staticmethod
    def help_menu() -> str:
        return (
            "You can say brief me, what just finished, what are the agents doing, email me the summary, "
            "open the dashboard, open the Zoar website, repeat that, slow down, approve, reject, or hang up. "
            "Keypad fallback: 1 briefing, 2 email summary, 3 Zoar website, 4 dashboard, 5 agent activity, "
            "6 repeat, 7 best next actions, 8 slow down, 9 this help, or 0 hang up."
        )

    @staticmethod
    def welcome_brief(phone: str, *, preface: str = "") -> str:
        brief = sms_control.format_voice_operator_brief(phone)
        if preface:
            return f"{preface}. {brief} {VoicePromptBuilder.short_follow_up()}"
        return f"{brief} {VoicePromptBuilder.short_follow_up()}"

    @staticmethod
    def compose_prompt(spoken_text: str, *, menu_style: str = "short") -> str:
        cleaned = _clean_for_voice(spoken_text)
        if menu_style == "help":
            return " ".join(part for part in (cleaned, VoicePromptBuilder.help_menu()) if part).strip()
        if menu_style == "welcome":
            return cleaned
        return " ".join(part for part in (cleaned, VoicePromptBuilder.short_follow_up()) if part).strip()


class VoiceIntentRouter:
    DIGIT_MAP = {
        "1": ("brief", ""),
        "2": ("email_summary", ""),
        "3": ("open_zoar", ""),
        "4": ("open_dashboard", ""),
        "5": ("agents", ""),
        "6": ("repeat", ""),
        "7": ("next_actions", ""),
        "8": ("slow_down", ""),
        "9": ("help", ""),
        "0": ("hangup", ""),
    }

    @staticmethod
    def parse(speech_result: str = "", digits: str = "") -> tuple[str, str]:
        digits = str(digits or "").strip()
        if digits:
            return VoiceIntentRouter.DIGIT_MAP.get(digits, ("unassigned_digit", digits))

        text = str(speech_result or "").strip()
        lowered = sms_control.normalize_body(text)
        if not lowered:
            return "empty", ""
        if lowered in {"repeat", "repeat that", "say that again", "again"}:
            return "repeat", text
        if lowered in {"menu", "help", "options"}:
            return "help", text
        if lowered in {"bye", "goodbye", "hang up", "hangup", "stop", "cancel"}:
            return "hangup", text
        if lowered in {
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
            return "brief", text
        if lowered in {"what just finished", "what finished", "latest completion", "what completed"}:
            return "latest_completion", text
        if lowered in {
            "what are the agents doing",
            "what are agents doing",
            "what is running",
            "what's running",
            "active work",
        }:
            return "agents", text
        if lowered in {"what should i do next", "next action", "next actions", "what is blocked", "what's blocked"}:
            return "next_actions", text
        if "slow down" in lowered or lowered in {"slower", "speak slower"}:
            return "slow_down", text
        if any(
            phrase in lowered
            for phrase in (
                "email me the summary",
                "email me summary",
                "email me a summary",
                "email me the brief",
                "email me the status",
                "email me that",
            )
        ):
            return "email_summary", text
        if re.search(r"\bopen\b", lowered):
            if "dashboard" in lowered or "crm" in lowered:
                return "open_dashboard", text
            if "zoar" in lowered or "website" in lowered:
                return "open_zoar", text
        task_match = re.fullmatch(r"(?:task|show)\s+(?:(b)\s*)?#?(\d+)", lowered)
        if task_match:
            task_kind = "brain" if task_match.group(1) else ""
            return "task_detail", f"{task_kind}:{task_match.group(2)}"
        return "natural_task", text


class VoiceTurnManager:
    def __init__(self, session: VoiceSessionState):
        self.session = session

    def welcome(self, *, preface: str = "") -> VoiceTurnResult:
        spoken = VoicePromptBuilder.welcome_brief(self.session.phone, preface=preface)
        self.session.last_briefing = spoken
        self.session.repeatable_text = spoken
        self.session.last_result = spoken
        self.session.last_intent = "welcome"
        self.session.turns += 1
        return VoiceTurnResult(spoken_text=spoken, repeatable_text=spoken, menu_style="welcome")

    def handle_turn(self, *, speech_result: str = "", digits: str = "") -> VoiceTurnResult:
        intent, payload = VoiceIntentRouter.parse(speech_result=speech_result, digits=digits)
        self.session.last_intent = intent

        if intent == "empty":
            spoken = "I did not catch that."
            self.session.last_result = spoken
            return VoiceTurnResult(spoken_text=spoken, repeatable_text=self.session.repeatable_text or spoken)
        if intent == "unassigned_digit":
            spoken = "That keypad option is not assigned."
            self.session.last_result = spoken
            return VoiceTurnResult(spoken_text=spoken, repeatable_text=spoken, menu_style="help")
        if intent == "repeat":
            repeated = self.session.repeatable_text or self.session.last_result or self.session.last_briefing
            spoken = repeated or "I do not have anything to repeat yet."
            self.session.last_result = spoken
            return VoiceTurnResult(spoken_text=spoken, repeatable_text=spoken)
        if intent == "slow_down":
            self.session.pace = "slow"
            spoken = "I will slow down. " + (self.session.last_briefing or sms_control.format_voice_operator_brief(self.session.phone))
            self.session.repeatable_text = spoken
            self.session.last_result = spoken
            return VoiceTurnResult(spoken_text=spoken, repeatable_text=spoken)
        if intent == "hangup":
            spoken = "Okay. Hanging up now."
            self.session.last_result = spoken
            self.session.repeatable_text = spoken
            return VoiceTurnResult(spoken_text=spoken, repeatable_text=spoken, hangup=True)
        if intent == "help":
            spoken = "Operator help."
            self.session.last_result = spoken
            self.session.repeatable_text = VoicePromptBuilder.help_menu()
            return VoiceTurnResult(spoken_text=spoken, repeatable_text=self.session.repeatable_text, menu_style="help")

        if intent == "brief":
            spoken = sms_control.format_voice_operator_brief(self.session.phone)
        elif intent == "latest_completion":
            spoken = sms_control.format_voice_latest_completion(self.session.phone)
        elif intent == "agents":
            spoken = sms_control.format_voice_active_work(self.session.phone)
        elif intent == "next_actions":
            spoken = sms_control.format_voice_decision_prompt(self.session.phone)
        elif intent == "email_summary":
            spoken = sms_control.send_voice_summary_email(
                self.session.phone,
                source_name="voice_call",
                raw_body=str(payload or "email me the summary"),
            )
        elif intent == "open_zoar":
            spoken = sms_control._handle_local_control_command(
                self.session.phone,
                "open zoar bathroom website",
                source_name="voice_call",
                raw_body="open zoar bathroom website",
            ) or "I could not open the Zoar website right now."
        elif intent == "open_dashboard":
            spoken = sms_control._handle_local_control_command(
                self.session.phone,
                "open the dashboard on my mac mini",
                source_name="voice_call",
                raw_body="open the dashboard on my mac mini",
            ) or "I could not open the dashboard right now."
        elif intent == "task_detail":
            task_kind, task_id = (payload.split(":", 1) + [""])[:2]
            spoken = sms_control.format_task_detail(
                self.session.phone,
                int(task_id or 0),
                task_kind=task_kind or "",
            )
        else:
            spoken = sms_control.handle_voice_operator_request(
                self.session.phone,
                str(payload or speech_result or ""),
                source_name="voice_call",
            )

        cleaned_spoken = _clean_for_voice(spoken)
        if intent in {"brief", "latest_completion", "agents", "next_actions"}:
            self.session.last_briefing = cleaned_spoken
        self.session.last_result = cleaned_spoken
        self.session.repeatable_text = cleaned_spoken
        self.session.turns += 1
        return VoiceTurnResult(spoken_text=cleaned_spoken, repeatable_text=cleaned_spoken)


def _render_gather_response(session: VoiceSessionState, result: VoiceTurnResult) -> bytes:
    if result.hangup:
        session.last_prompt = result.spoken_text
        _save_session(session)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            f"{_say(result.spoken_text, pace=session.pace)}"
            "<Hangup/>"
            "</Response>"
        ).encode("utf-8")

    action_url = _public_url("instructions", phone=session.phone, call_sid=session.call_sid)
    welcome_url = _public_url("welcome", phone=session.phone, call_sid=session.call_sid)
    prompt = VoicePromptBuilder.compose_prompt(result.spoken_text, menu_style=result.menu_style)
    session.last_prompt = prompt
    if result.repeatable_text:
        session.repeatable_text = result.repeatable_text
    _save_session(session)

    retry = "I did not catch that. I am still here."
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Gather input="speech dtmf" timeout="{VOICE_GATHER_TIMEOUT_SECONDS}" '
        f'speechTimeout="auto" numDigits="1" action="{xml_escape(action_url)}" method="POST" '
        f'language="en-US" hints="{xml_escape(VOICE_HINTS)}">'
        f"{_say(prompt, pace=session.pace)}"
        "</Gather>"
        f"{_say(retry, pace=session.pace)}"
        f'<Redirect method="POST">{xml_escape(welcome_url)}</Redirect>'
        "</Response>"
    ).encode("utf-8")


def _unauthorized_response() -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"{_say('This voice control line only accepts your owner control number.')}"
        "<Hangup/>"
        "</Response>"
    ).encode("utf-8")


def route_voice_turn(phone: str, speech_result: str = "", digits: str = "", *, session: Optional[VoiceSessionState] = None) -> VoiceTurnResult:
    session = session or VoiceSessionState(call_sid=f"phone:{sms_control.normalize_phone(phone)}", phone=sms_control.normalize_phone(phone))
    manager = VoiceTurnManager(session)
    return manager.handle_turn(speech_result=speech_result, digits=digits)


def route_voice_instruction(phone: str, speech_result: str = "", digits: str = "", *, session: Optional[VoiceSessionState] = None) -> str:
    return route_voice_turn(phone, speech_result=speech_result, digits=digits, session=session).spoken_text


def stream_session_state(call_sid: str, phone: str = "") -> VoiceSessionState:
    query = {"call_sid": [str(call_sid or "").strip()]}
    return _load_session(phone or "", query, {})


def handle_stream_message(session: VoiceSessionState, message: str) -> list[Dict[str, object]]:
    raw = str(message or "").strip()
    if not raw:
        return [{"type": "error", "message": "empty message"}]

    try:
        payload = json.loads(raw)
    except Exception:
        payload = {"type": "utterance", "text": raw}

    if not isinstance(payload, dict):
        return [{"type": "error", "message": "invalid payload"}]

    msg_type = str(payload.get("type") or payload.get("event") or "").strip().lower()
    if msg_type in {"ping", "health"}:
        return [{"type": "pong", "call_sid": session.call_sid, "phone": session.phone}]

    if msg_type in {"start", "connected"}:
        phone = sms_control.normalize_phone(str(payload.get("phone") or payload.get("from") or session.phone or ""))
        if phone:
            session.phone = phone
        if not sms_control.is_authorized_voice_phone(session.phone):
            return [{"type": "error", "code": "unauthorized", "message": "owner-only voice stream"}, {"type": "hangup"}]
        session.turns += 1
        session.last_intent = "stream_start"
        session.last_briefing = _clean_for_voice(sms_control.format_voice_operator_brief(session.phone))
        session.repeatable_text = session.last_briefing
        _save_session(session)
        return [
            {
                "type": "session",
                "call_sid": session.call_sid,
                "phone": session.phone,
                "pace": session.pace,
                "transport": "websocket_stream",
            },
            {
                "type": "assistant",
                "text": session.last_briefing,
                "pace": session.pace,
            },
        ]

    if msg_type in {"utterance", "user_text", "speech"}:
        turn = route_voice_turn(
            session.phone,
            speech_result=str(payload.get("text") or payload.get("utterance") or ""),
            digits=str(payload.get("digits") or ""),
            session=session,
        )
        _save_session(session)
        frames: list[Dict[str, object]] = [{"type": "assistant", "text": turn.spoken_text, "pace": session.pace}]
        if turn.hangup:
            frames.append({"type": "hangup"})
        return frames

    if msg_type == "dtmf":
        turn = route_voice_turn(
            session.phone,
            digits=str(payload.get("digits") or payload.get("digit") or ""),
            session=session,
        )
        _save_session(session)
        frames = [{"type": "assistant", "text": turn.spoken_text, "pace": session.pace}]
        if turn.hangup:
            frames.append({"type": "hangup"})
        return frames

    if msg_type == "media":
        if not session.stream_notice_sent:
            session.stream_notice_sent = True
            notice = (
                "The live audio stream transport is online, but speech transcription for raw audio is not configured yet. "
                "Use text utterances over the stream or keep the current TwiML gather fallback."
            )
            _save_session(session)
            return [{"type": "notice", "text": notice}]
        return [{"type": "ack", "event": "media"}]

    if msg_type in {"stop", "hangup"}:
        session.last_intent = "stream_stop"
        _save_session(session)
        return [{"type": "hangup"}]

    return [{"type": "error", "message": f"unsupported message type: {msg_type or 'unknown'}"}]


def handle_conversation_relay_message(session: VoiceSessionState, message: str) -> list[Dict[str, object]]:
    raw = str(message or "").strip()
    if not raw:
        return []

    try:
        payload = json.loads(raw)
    except Exception:
        return [{"type": "text", "token": "I could not read that request.", "last": True, "interruptible": True}]

    if not isinstance(payload, dict):
        return [{"type": "text", "token": "I received an invalid payload.", "last": True, "interruptible": True}]

    msg_type = str(payload.get("type") or "").strip().lower()
    lang = str(payload.get("lang") or load_voice_conversationrelay_settings()["language"]).strip() or "en-US"

    if msg_type in {"setup", "connected"}:
        phone = sms_control.normalize_phone(
            str(payload.get("from") or payload.get("caller") or payload.get("phone") or session.phone or "")
        )
        if phone:
            session.phone = phone
        call_sid = str(payload.get("callSid") or payload.get("call_sid") or payload.get("sessionId") or "").strip()
        if call_sid:
            session.call_sid = call_sid[:120]
        if not sms_control.is_authorized_voice_phone(session.phone):
            return [
                {"type": "text", "token": "This voice control line only accepts your owner control number.", "last": True},
                {"type": "end"},
            ]
        session.turns += 1
        session.last_intent = "conversation_relay_setup"
        session.last_briefing = _clean_for_voice(sms_control.format_voice_operator_brief(session.phone))
        session.repeatable_text = session.last_briefing
        session.last_result = session.last_briefing
        _save_session(session)
        return _conversation_relay_text_frames(session.last_briefing, lang=lang)

    if msg_type == "prompt":
        segment = str(payload.get("voicePrompt") or payload.get("text") or "").strip()
        if not segment and not str(payload.get("digits") or "").strip():
            return []
        is_last = bool(payload.get("last", True))
        if segment:
            session.partial_utterance = " ".join(part for part in (session.partial_utterance, segment) if part).strip()
        if not is_last:
            return []
        turn = route_voice_turn(
            session.phone,
            speech_result=session.partial_utterance,
            digits=str(payload.get("digits") or ""),
            session=session,
        )
        session.partial_utterance = ""
        _save_session(session)
        frames = _conversation_relay_text_frames(turn.spoken_text, lang=lang)
        if turn.hangup:
            frames.append({"type": "end"})
        return frames

    if msg_type == "dtmf":
        turn = route_voice_turn(
            session.phone,
            digits=str(payload.get("digit") or payload.get("digits") or ""),
            session=session,
        )
        _save_session(session)
        frames = _conversation_relay_text_frames(turn.spoken_text, lang=lang)
        if turn.hangup:
            frames.append({"type": "end"})
        return frames

    if msg_type == "interrupt":
        utterance_until_interrupt = str(payload.get("utteranceUntilInterrupt") or "").strip()
        if utterance_until_interrupt:
            session.last_result = _clean_for_voice(utterance_until_interrupt)
            _save_session(session)
        return []

    if msg_type in {"error", "end", "stop"}:
        session.last_intent = f"conversation_relay_{msg_type or 'stop'}"
        _save_session(session)
        return []

    return []


def _health_payload() -> Dict[str, object]:
    cfg = _load_config()
    stream_public_url = load_voice_stream_public_url()
    conversationrelay_public_url = load_voice_conversationrelay_public_url()
    explicit_stream = bool(
        str(cfg.get("voice_control_stream_public_url") or cfg.get("voice_stream_public_url") or "").strip()
    )
    stream_port = load_voice_stream_port()
    voice_port = load_voice_port()
    return {
        "ok": True,
        "port": voice_port,
        "public_base_url": sms_control.load_voice_public_base_url(),
        "allowed_numbers": sms_control.load_voice_allowed_numbers(),
        "call_capable": sms_control.is_voice_call_configured(),
        "session_count": _session_count(),
        "transport": "twiml_gather_with_stateful_session",
        "preferred_transport": load_voice_transport(),
        "real_time_ready": True,
        "stream_port": stream_port,
        "stream_local_url": _local_stream_url(),
        "stream_public_url": stream_public_url,
        "stream_public_configured": bool(stream_public_url),
        "stream_public_explicit": explicit_stream,
        "stream_same_port": stream_port == voice_port,
        "stream_transport": "websocket",
        "conversation_relay_public_url": conversationrelay_public_url,
        "conversation_relay_ready": bool(conversationrelay_public_url),
    }


def dispatch_http_request(
    method: str,
    path: str,
    query: Mapping[str, object],
    form: Mapping[str, str],
) -> tuple[int, str, bytes]:
    clean_path = str(path or "/").strip() or "/"
    phone = _caller_phone(form, query)

    if clean_path in {"/healthz", "/voice/healthz"}:
        body = json.dumps(_health_payload(), indent=2).encode("utf-8")
        return 200, "application/json; charset=utf-8", body

    if clean_path in {"/voice/stream-config", "/voice/stream/healthz", "/voice/conversationrelay/healthz"}:
        body = json.dumps(_health_payload(), indent=2).encode("utf-8")
        return 200, "application/json; charset=utf-8", body

    if clean_path == "/voice/status":
        record_call_event(form)
        return 200, "application/json; charset=utf-8", json.dumps({"ok": True}).encode("utf-8")

    if clean_path == "/voice/stream-twiml":
        if not sms_control.is_authorized_voice_phone(phone):
            return 403, "text/xml; charset=utf-8", _unauthorized_response()
        stream_url = load_voice_stream_public_url()
        if not stream_url:
            body = json.dumps({"ok": False, "error": "stream_public_url_missing"}).encode("utf-8")
            return 412, "application/json; charset=utf-8", body
        call_sid = _session_id(phone, query, form)
        connect_url = f"{stream_url}?call_sid={call_sid}&phone={phone}"
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            f'<Connect><Stream url="{xml_escape(connect_url)}" /></Connect>'
            "</Response>"
        ).encode("utf-8")
        return 200, "text/xml; charset=utf-8", twiml

    if clean_path == "/voice/conversationrelay-twiml":
        if not sms_control.is_authorized_voice_phone(phone):
            return 403, "text/xml; charset=utf-8", _unauthorized_response()
        relay_url = load_voice_conversationrelay_public_url()
        if not relay_url:
            body = json.dumps({"ok": False, "error": "conversationrelay_public_url_missing"}).encode("utf-8")
            return 412, "application/json; charset=utf-8", body
        call_sid = _session_id(phone, query, form)
        twiml = _conversation_relay_twiml(phone, call_sid=call_sid)
        return 200, "text/xml; charset=utf-8", twiml

    if clean_path not in {"/voice/welcome", "/voice/outbound", "/voice/instructions"}:
        body = json.dumps({"ok": False, "error": "not_found", "path": clean_path}).encode("utf-8")
        return 404, "application/json; charset=utf-8", body

    if not sms_control.is_authorized_voice_phone(phone):
        return 403, "text/xml; charset=utf-8", _unauthorized_response()

    session = _load_session(phone, query, form)
    manager = VoiceTurnManager(session)

    if clean_path in {"/voice/welcome", "/voice/outbound"}:
        preface = _query_value(query, "message")
        if clean_path == "/voice/outbound" and load_voice_transport() == "conversation_relay":
            twiml = _conversation_relay_twiml(phone, call_sid=session.call_sid)
            return 200, "text/xml; charset=utf-8", twiml
        twiml = _render_gather_response(session, manager.welcome(preface=preface))
        return 200, "text/xml; charset=utf-8", twiml

    result = manager.handle_turn(
        speech_result=str(form.get("SpeechResult") or ""),
        digits=str(form.get("Digits") or ""),
    )
    twiml = _render_gather_response(session, result)
    return 200, "text/xml; charset=utf-8", twiml
