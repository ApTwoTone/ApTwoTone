import json
import sqlite3
from pathlib import Path

from core import sms_control, voice_control


def _init_task_board(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE task_board (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            priority INTEGER DEFAULT 3,
            status TEXT DEFAULT 'open',
            assigned_to TEXT DEFAULT '',
            created_by TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT DEFAULT '',
            tags TEXT DEFAULT '[]',
            files_involved TEXT DEFAULT '[]',
            progress_note TEXT DEFAULT '',
            task_type TEXT DEFAULT 'code',
            complexity INTEGER DEFAULT 3
        )
        """
    )
    conn.commit()
    conn.close()


class FakeBrainControlPlane:
    def __init__(self):
        self.tasks = []
        self.approvals = []
        self.agents = [{"agent_id": "brain_supervisor", "status": "idle", "current_task_id": 0}]

    def enqueue_task(
        self,
        *,
        task_type,
        objective,
        lane="core_ops",
        priority=50,
        payload=None,
        constraints=None,
        parent_task_id=0,
        assigned_agent_id="",
        requires_approval=False,
        risk_level="low",
        scheduled_for="",
        approval_reason="",
        requested_by="system",
    ):
        task_id = len(self.tasks) + 1
        self.tasks.append(
            {
                "id": task_id,
                "task_type": task_type,
                "objective": objective,
                "lane": lane,
                "priority": priority,
                "status": "queued",
                "payload": payload or {},
                "constraints": constraints or {},
                "result": {},
                "error_text": "",
                "created_at": "2026-03-11T07:00:00",
                "started_at": "",
                "finished_at": "",
            }
        )
        if requires_approval:
            approval_id = len(self.approvals) + 1
            self.tasks[-1]["approval_status"] = "pending"
            self.approvals.append(
                {
                    "approval_id": approval_id,
                    "task_id": task_id,
                    "status": "pending",
                    "reason": approval_reason or "Approval required",
                }
            )
            return {"ok": True, "task_id": task_id, "approval_id": approval_id}
        return {"ok": True, "task_id": task_id}

    def get_task(self, task_id):
        for task in self.tasks:
            if task["id"] == int(task_id):
                return task
        return None

    def list_tasks(self, *, status="", lane="", limit=200):
        rows = list(self.tasks)
        if status:
            rows = [task for task in rows if task["status"] == status]
        if lane:
            rows = [task for task in rows if task["lane"] == lane]
        return list(reversed(rows))[:limit]

    def list_agents(self, lane="", status="", limit=200):
        rows = list(self.agents)
        if lane:
            rows = [agent for agent in rows if agent.get("lane", "") == lane]
        if status:
            rows = [agent for agent in rows if agent["status"] == status]
        return rows[:limit]

    def list_approvals(self, status="", limit=200):
        rows = list(self.approvals)
        if status:
            rows = [approval for approval in rows if approval["status"] == status]
        return rows[:limit]

    def approve_task(self, *, task_id, decision, decision_by="", decision_note=""):
        for approval in self.approvals:
            if approval["task_id"] == int(task_id) and approval["status"] == "pending":
                approval["status"] = decision
                task = self.get_task(task_id)
                if task:
                    task["approval_status"] = decision
                    task["status"] = "queued" if decision == "approved" else "cancelled"
                return {"ok": True}
        return {"ok": False, "error": "approval not found"}


def test_voice_welcome_requires_owner_number(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({}))

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(voice_control, "CONFIG_PATH", cfg_path)

    status, content_type, body = voice_control.dispatch_http_request(
        "POST",
        "/voice/welcome",
        {},
        {"From": "+13235551111"},
    )

    assert status == 403
    assert content_type.startswith("text/xml")
    assert b"owner control number" in body


def test_voice_welcome_builds_gather_prompt(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"fb_webhook_domain": "https://crm.zoarbathroomrental.com"}))

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(voice_control, "CONFIG_PATH", cfg_path)

    status, content_type, body = voice_control.dispatch_http_request(
        "POST",
        "/voice/welcome",
        {"phone": ["8184489055"], "message": ["Booking audit is done."]},
        {},
    )

    assert status == 200
    assert content_type.startswith("text/xml")
    assert b"<Gather" in body
    assert b"Booking audit is done" in body
    assert b"Zoar website" in body
    assert b"/voice/instructions?phone=8184489055" in body


def test_voice_instructions_route_plain_speech_to_brain_queue(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"fb_webhook_domain": "https://crm.zoarbathroomrental.com"}))

    fake_cp = FakeBrainControlPlane()

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(sms_control, "_BRAIN_CONTROL_PLANE", fake_cp)
    monkeypatch.setattr(sms_control, "_get_brain_control_plane", lambda: fake_cp)
    monkeypatch.setattr(voice_control, "CONFIG_PATH", cfg_path)

    status, content_type, body = voice_control.dispatch_http_request(
        "POST",
        "/voice/instructions",
        {"phone": ["8184489055"]},
        {"SpeechResult": "debug the booking flow"},
    )

    assert status == 200
    assert content_type.startswith("text/xml")
    assert fake_cp.tasks[0]["objective"] == "debug the booking flow"
    assert fake_cp.tasks[0]["lane"] == "systems"
    assert b"Queued brain task" in body


def test_voice_digit_shortcut_opens_zoar_website(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"fb_webhook_domain": "https://crm.zoarbathroomrental.com"}))

    opened = []

    def fake_run(cmd, check, timeout):
        opened.append((cmd, check, timeout))

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(voice_control, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(sms_control.subprocess, "run", fake_run)

    response = voice_control.route_voice_instruction("8184489055", digits="3")

    assert "Opened zoarbathroomrental.com on this Mac mini" in response
    assert opened == [(["open", "https://zoarbathroomrental.com"], True, sms_control.LOCAL_OPEN_TIMEOUT_SECONDS)]


def test_voice_digit_shortcut_opens_dashboard(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"fb_webhook_domain": "https://crm.zoarbathroomrental.com"}))

    opened = []

    def fake_run(cmd, check, timeout):
        opened.append((cmd, check, timeout))

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(voice_control, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(sms_control.subprocess, "run", fake_run)

    response = voice_control.route_voice_instruction("8184489055", digits="4")

    assert "Opened crm.zoarbathroomrental.com/dashboard/ on this Mac mini" in response
    assert opened == [(["open", "https://crm.zoarbathroomrental.com/dashboard/"], True, sms_control.LOCAL_OPEN_TIMEOUT_SECONDS)]


def test_voice_session_repeat_and_slow_down(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"fb_webhook_domain": "https://crm.zoarbathroomrental.com"}))

    fake_cp = FakeBrainControlPlane()

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(sms_control, "_BRAIN_CONTROL_PLANE", fake_cp)
    monkeypatch.setattr(sms_control, "_get_brain_control_plane", lambda: fake_cp)
    monkeypatch.setattr(voice_control, "CONFIG_PATH", cfg_path)

    session = voice_control.VoiceSessionState(call_sid="call-1", phone="8184489055")
    brief = voice_control.route_voice_turn("8184489055", speech_result="brief me", session=session)
    repeated = voice_control.route_voice_turn("8184489055", speech_result="repeat that", session=session)
    slowed = voice_control.route_voice_turn("8184489055", speech_result="slow down", session=session)

    assert "Best next actions" in brief.spoken_text
    assert repeated.spoken_text == brief.spoken_text
    assert session.pace == "slow"
    assert "I will slow down." in slowed.spoken_text


def test_voice_health_payload_reports_stateful_transport(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"fb_webhook_domain": "https://crm.zoarbathroomrental.com"}))

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(voice_control, "CONFIG_PATH", cfg_path)

    status, content_type, body = voice_control.dispatch_http_request("GET", "/voice/healthz", {}, {})
    payload = json.loads(body.decode("utf-8"))

    assert status == 200
    assert content_type.startswith("application/json")
    assert payload["transport"] == "twiml_gather_with_stateful_session"
    assert payload["real_time_ready"] is True
    assert payload["stream_same_port"] is True
    assert payload["stream_public_url"].endswith("/voice/stream")


def test_stream_twiml_uses_public_voice_base_without_explicit_stream_url(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"fb_webhook_domain": "https://crm.zoarbathroomrental.com"}))

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(voice_control, "CONFIG_PATH", cfg_path)

    status, content_type, body = voice_control.dispatch_http_request(
        "POST",
        "/voice/stream-twiml",
        {"phone": ["8184489055"]},
        {"CallSid": "CA123"},
    )

    assert status == 200
    assert content_type.startswith("text/xml")
    assert b"<Connect><Stream" in body
    assert b"wss://crm.zoarbathroomrental.com/voice/stream?call_sid=CA123&amp;phone=8184489055" in body


def test_stream_message_flow_handles_start_and_utterance(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"fb_webhook_domain": "https://crm.zoarbathroomrental.com"}))
    fake_cp = FakeBrainControlPlane()

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(sms_control, "_BRAIN_CONTROL_PLANE", fake_cp)
    monkeypatch.setattr(sms_control, "_get_brain_control_plane", lambda: fake_cp)
    monkeypatch.setattr(voice_control, "CONFIG_PATH", cfg_path)

    session = voice_control.stream_session_state("CA555", "8184489055")
    start_frames = voice_control.handle_stream_message(
        session,
        json.dumps({"type": "start", "phone": "8184489055"}),
    )
    utterance_frames = voice_control.handle_stream_message(
        session,
        json.dumps({"type": "utterance", "text": "find me 10 wedding venue leads in Ventura"}),
    )

    assert start_frames[0]["type"] == "session"
    assert start_frames[1]["type"] == "assistant"
    assert "Best next actions" in start_frames[1]["text"]
    assert utterance_frames[0]["type"] == "assistant"
    assert "Queued brain task 1" in utterance_frames[0]["text"]
    assert fake_cp.tasks[0]["lane"] == "lead_ops"


def test_stream_message_rejects_unauthorized_owner(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({}))

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(voice_control, "CONFIG_PATH", cfg_path)

    session = voice_control.stream_session_state("CA777", "3235551111")
    frames = voice_control.handle_stream_message(
        session,
        json.dumps({"type": "start", "phone": "3235551111"}),
    )

    assert frames[0]["type"] == "error"
    assert frames[0]["code"] == "unauthorized"
    assert frames[1]["type"] == "hangup"


def test_conversationrelay_twiml_uses_public_websocket_url(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(
        json.dumps(
            {
                "fb_webhook_domain": "https://crm.zoarbathroomrental.com",
                "voice_control_transport": "conversation_relay",
                "voice_control_tts_provider": "Google",
                "voice_control_tts_voice": "en-US-Journey-O",
            }
        )
    )

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(voice_control, "CONFIG_PATH", cfg_path)

    status, content_type, body = voice_control.dispatch_http_request(
        "POST",
        "/voice/conversationrelay-twiml",
        {"phone": ["8184489055"]},
        {"CallSid": "CArelay123"},
    )

    assert status == 200
    assert content_type.startswith("text/xml")
    assert b"<ConversationRelay " in body
    assert b'wss://crm.zoarbathroomrental.com/voice/conversationrelay?phone=8184489055&amp;call_sid=CArelay123' in body
    assert b'ttsProvider="Google"' in body
    assert b'voice="en-US-Journey-O"' in body


def test_voice_outbound_prefers_conversationrelay_when_configured(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(
        json.dumps(
            {
                "fb_webhook_domain": "https://crm.zoarbathroomrental.com",
                "voice_control_transport": "conversation_relay",
            }
        )
    )

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(voice_control, "CONFIG_PATH", cfg_path)

    status, content_type, body = voice_control.dispatch_http_request(
        "POST",
        "/voice/outbound",
        {"phone": ["8184489055"], "message": ["Testing the low latency path"]},
        {"CallSid": "CArelayout"},
    )

    assert status == 200
    assert content_type.startswith("text/xml")
    assert b"<ConversationRelay " in body


def test_conversationrelay_message_flow_handles_setup_and_prompt(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"fb_webhook_domain": "https://crm.zoarbathroomrental.com"}))
    fake_cp = FakeBrainControlPlane()

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(sms_control, "_BRAIN_CONTROL_PLANE", fake_cp)
    monkeypatch.setattr(sms_control, "_get_brain_control_plane", lambda: fake_cp)
    monkeypatch.setattr(voice_control, "CONFIG_PATH", cfg_path)

    session = voice_control.stream_session_state("CArelay", "8184489055")
    setup_frames = voice_control.handle_conversation_relay_message(
        session,
        json.dumps({"type": "setup", "callSid": "CArelay", "from": "8184489055"}),
    )
    prompt_frames = voice_control.handle_conversation_relay_message(
        session,
        json.dumps({"type": "prompt", "voicePrompt": "find me 5 venues in Moorpark", "last": True}),
    )

    assert setup_frames[0]["type"] == "text"
    assert setup_frames[-1]["last"] is True
    assert "Best next actions" in "".join(frame["token"] for frame in setup_frames)
    assert prompt_frames[0]["type"] == "text"
    assert prompt_frames[-1]["last"] is True
    assert "Queued brain task 1" in "".join(frame["token"] for frame in prompt_frames)
    assert fake_cp.tasks[0]["lane"] == "lead_ops"


def test_voice_status_callback_records_call_event(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"fb_webhook_domain": "https://crm.zoarbathroomrental.com"}))

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(voice_control, "CONFIG_PATH", cfg_path)

    status, content_type, body = voice_control.dispatch_http_request(
        "POST",
        "/voice/status",
        {},
        {
            "CallSid": "CAstatus123",
            "CallStatus": "ringing",
            "From": "+14242358979",
            "To": "+18184489055",
            "Direction": "outbound-api",
        },
    )

    assert status == 200
    assert content_type.startswith("application/json")
    assert json.loads(body.decode("utf-8")) == {"ok": True}

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        f"SELECT call_sid, call_status, from_number, to_number, direction FROM {voice_control.VOICE_CALL_EVENT_TABLE}"
    ).fetchone()
    conn.close()

    assert dict(row) == {
        "call_sid": "CAstatus123",
        "call_status": "ringing",
        "from_number": "+14242358979",
        "to_number": "+18184489055",
        "direction": "outbound-api",
    }
