import json
import sqlite3
from pathlib import Path

from core import sms_control


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
        self.agents = [
            {
                "agent_id": "brain_supervisor",
                "status": "idle",
                "current_task_id": 0,
            },
            {
                "agent_id": "coding_repair_agent",
                "status": "idle",
                "current_task_id": 0,
            },
        ]

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
        task = {
            "id": task_id,
            "task_type": task_type,
            "objective": objective,
            "lane": lane,
            "priority": priority,
            "status": "queued",
            "payload": payload or {},
            "constraints": constraints or {},
            "parent_task_id": parent_task_id,
            "assigned_agent_id": assigned_agent_id,
            "requires_approval": requires_approval,
            "approval_status": "not_required",
            "risk_level": risk_level,
            "result": {},
            "error_text": "",
            "retry_count": 0,
            "scheduled_for": scheduled_for,
            "created_at": "2026-03-11T07:00:00",
            "started_at": "",
            "finished_at": "",
        }
        self.tasks.append(task)
        if requires_approval:
            approval_id = len(self.approvals) + 1
            task["approval_status"] = "pending"
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


def test_authorized_phone_uses_config(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"sms_control_allowed_numbers": ["+1 (323) 555-1111"]}))

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)

    assert sms_control.is_authorized_phone("3235551111") is True
    assert sms_control.is_authorized_phone("8184489055") is False


def test_voice_phone_defaults_to_owner_only(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"sms_control_allowed_numbers": ["+1 (323) 555-1111", "8184489055"]}))

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)

    assert sms_control.is_authorized_phone("3235551111") is True
    assert sms_control.is_authorized_voice_phone("3235551111") is False
    assert sms_control.is_authorized_voice_phone("8184489055") is True


def test_orch_command_queues_brain_task_and_status(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"sms_control_allowed_numbers": ["8184489055"]}))
    fake_cp = FakeBrainControlPlane()

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(sms_control, "_BRAIN_CONTROL_PLANE", fake_cp)
    monkeypatch.setattr(sms_control, "_get_brain_control_plane", lambda: fake_cp)

    response = sms_control.handle_inbound_sms("8184489055", "ORCH: fix the booking confirmation copy")
    assert "Queued brain task B#1" in response
    assert fake_cp.tasks[0]["lane"] == "systems"
    assert fake_cp.tasks[0]["objective"] == "fix the booking confirmation copy"

    status = sms_control.handle_inbound_sms("8184489055", "STATUS")
    assert "Recent SMS tasks:" in status
    assert "booking confirmation copy" in status


def test_unprefixed_freeform_text_is_rejected_without_queueing(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"sms_control_allowed_numbers": ["8184489055"]}))

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)

    response = sms_control.handle_inbound_sms("8184489055", "fix the booking confirmation copy")

    assert "strict mode" in response.lower()
    assert "RUN:" in response
    assert "ORCH:" in response

    conn = sqlite3.connect(str(db_path))
    task_count = conn.execute("SELECT COUNT(*) FROM task_board").fetchone()[0]
    inbox_row = conn.execute(
        "SELECT task_id FROM sms_control_inbox ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()

    assert task_count == 0
    assert inbox_row == (0,)


def test_duplicate_inbound_is_suppressed(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"sms_control_allowed_numbers": ["8184489055"]}))

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)

    first = sms_control.handle_inbound_sms("8184489055", "research competitor pricing")
    second = sms_control.handle_inbound_sms("8184489055", "research competitor pricing")

    assert "strict mode" in first.lower()
    assert second is None

    conn = sqlite3.connect(str(db_path))
    task_count = conn.execute("SELECT COUNT(*) FROM task_board").fetchone()[0]
    conn.close()
    assert task_count == 0


def test_completion_notification_only_once(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"sms_control_allowed_numbers": ["8184489055"]}))

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)

    task_id = sms_control.queue_sms_task("8184489055", "implement sms control bridge")

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE task_board SET status='done', progress_note='Bridge shipped cleanly.' WHERE id=?",
        (task_id,),
    )
    conn.commit()
    conn.close()

    messages = sms_control.get_pending_completion_notifications("8184489055")
    assert len(messages) == 1
    assert "What should I do next?" in messages[0]
    assert "RUN: email me" in messages[0]

    sms_control.mark_task_notification_sent("8184489055", task_id, "done")
    assert sms_control.get_pending_completion_notifications("8184489055") == []


def test_open_website_command_executes_immediately(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"sms_control_allowed_numbers": ["8184489055"]}))

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)

    opened = []

    def fake_run(cmd, check, timeout):
        opened.append((cmd, check, timeout))

    monkeypatch.setattr(sms_control.subprocess, "run", fake_run)

    response = sms_control.handle_inbound_sms(
        "8184489055",
        "open the zoar bathroom website on my mac mini",
    )

    assert opened == [(["open", "https://zoarbathroomrental.com"], True, sms_control.LOCAL_OPEN_TIMEOUT_SECONDS)]
    assert "Opened https://zoarbathroomrental.com on this Mac mini." in response

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    task = conn.execute(
        "SELECT status, progress_note FROM task_board WHERE id = 1"
    ).fetchone()
    notification = conn.execute(
        "SELECT task_status FROM sms_control_notifications WHERE task_id = 1"
    ).fetchone()
    conn.close()

    assert dict(task) == {
        "status": "done",
        "progress_note": "Opened https://zoarbathroomrental.com on this Mac mini.",
    }
    assert dict(notification) == {"task_status": "done"}


def test_run_prefix_executes_open_command_immediately(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"sms_control_allowed_numbers": ["8184489055"]}))

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)

    opened = []

    def fake_run(cmd, check, timeout):
        opened.append((cmd, check, timeout))

    monkeypatch.setattr(sms_control.subprocess, "run", fake_run)

    response = sms_control.handle_inbound_sms(
        "8184489055",
        "RUN: open the zoar bathroom website on my mac mini",
    )

    assert opened == [(["open", "https://zoarbathroomrental.com"], True, sms_control.LOCAL_OPEN_TIMEOUT_SECONDS)]
    assert "Opened https://zoarbathroomrental.com on this Mac mini." in response

    duplicate = sms_control.handle_inbound_sms(
        "8184489055",
        "RUN: open the zoar bathroom website on my mac mini",
    )
    assert duplicate is None


def test_email_me_command_executes_immediately(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"sms_control_allowed_numbers": ["8184489055"]}))

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)

    sent = {}

    def fake_send(subject, body):
        sent["subject"] = subject
        sent["body"] = body
        return True, "Emailed kaiescobar09@gmail.com from zoarbathrooms@gmail.com with subject \"Status\"."

    monkeypatch.setattr(sms_control, "_send_self_email", fake_send)

    response = sms_control.handle_inbound_sms(
        "8184489055",
        "RUN: email me Status || The booking audit is done.",
    )

    assert sent == {
        "subject": "Status",
        "body": "The booking audit is done.",
    }
    assert "Emailed kaiescobar09@gmail.com" in response

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    task = conn.execute(
        "SELECT status, progress_note FROM task_board WHERE id = 1"
    ).fetchone()
    conn.close()

    assert dict(task) == {
        "status": "done",
        "progress_note": "Emailed kaiescobar09@gmail.com from zoarbathrooms@gmail.com with subject \"Status\".",
    }


def test_call_me_command_fails_cleanly_when_voice_not_configured(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"sms_control_allowed_numbers": ["8184489055"]}))

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(sms_control, "is_google_voice_call_configured", lambda max_age_seconds=86400: False)

    response = sms_control.handle_inbound_sms(
        "8184489055",
        "RUN: call me say nexus finished the task",
    )

    assert "Voice call is not configured." in response
    assert "Google Voice SMS control daemon signed in for browser-based alert calls." in response

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    task = conn.execute(
        "SELECT status, progress_note FROM task_board WHERE id = 1"
    ).fetchone()
    conn.close()

    assert dict(task) == {
        "status": "failed",
        "progress_note": "Voice call is not configured. Add twilio_sid, twilio_token, and twilio_from to ~/.nexus/config.json, or keep the Google Voice SMS control daemon signed in for browser-based alert calls.",
    }


def test_place_voice_call_is_limited_to_owner_number(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(
        json.dumps(
            {
                "voice_control_allowed_numbers": ["8184489055"],
                "twilio_sid": "sid",
                "twilio_token": "token",
                "twilio_from": "+14242358979",
                "fb_webhook_domain": "https://crm.zoarbathroomrental.com",
            }
        )
    )

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)

    ok, note = sms_control._place_voice_call("3235551111", "Nexus finished the task.")
    assert ok is False
    assert note == "Voice calls are restricted to your owner control number only."


def test_place_voice_call_queues_google_voice_alert_when_runtime_ready(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"voice_control_allowed_numbers": ["8184489055"]}))

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(sms_control, "is_google_voice_call_configured", lambda max_age_seconds=86400: True)

    import integrations.messaging as messaging

    monkeypatch.setattr(
        messaging,
        "outbound_gate",
        lambda channel, recipient, recipient_name, message, approval_id=None, code_path="unknown": {"ok": True},
    )

    ok, note = sms_control._place_voice_call("8184489055", "Nexus finished the task.")

    assert ok is True
    assert "Queued a Google Voice alert call to 8184489055" in note
    assert "Spoken voice prompts still require Twilio" in note

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT phone, message, provider, status, approval_id FROM sms_control_voice_call_requests ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row["phone"] == "8184489055"
    assert row["message"] == "Nexus finished the task."
    assert row["provider"] == "google_voice"
    assert row["status"] == "pending"
    assert int(row["approval_id"]) > 0


def test_voice_call_configured_accepts_google_voice_runtime(monkeypatch):
    monkeypatch.setattr(sms_control, "_load_config", lambda: {})
    monkeypatch.setattr(sms_control, "is_google_voice_call_configured", lambda max_age_seconds=86400: True)

    assert sms_control.is_voice_call_configured() is True


def test_orchestrator_idle_prompt_and_brain_reply(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"sms_control_allowed_numbers": ["8184489055"]}))

    fake_cp = FakeBrainControlPlane()

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(sms_control, "_BRAIN_CONTROL_PLANE", fake_cp)
    monkeypatch.setattr(sms_control, "_get_brain_control_plane", lambda: fake_cp)
    monkeypatch.setattr(sms_control, "BRAIN_IDLE_STABLE_SECONDS", 0)
    monkeypatch.setattr(sms_control, "BRAIN_IDLE_PROMPT_COOLDOWN_MINUTES", 0)

    assert sms_control.get_orchestrator_idle_prompt("8184489055") is None
    prompt = sms_control.get_orchestrator_idle_prompt("8184489055")
    assert "orchestrator queue is empty" in prompt

    sms_control.mark_orchestrator_prompt_sent("8184489055")
    response = sms_control.handle_inbound_sms("8184489055", "debug the booking flow")

    assert "Queued brain task B#1" in response
    assert fake_cp.tasks[0]["lane"] == "systems"
    assert fake_cp.tasks[0]["task_type"] == "diagnostics"

    status = sms_control.format_status("8184489055")
    assert "B#1 queued: debug the booking flow" in status


def test_brain_task_completion_notification_only_once(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"sms_control_allowed_numbers": ["8184489055"]}))

    fake_cp = FakeBrainControlPlane()
    queued = fake_cp.enqueue_task(
        task_type="human_request",
        objective="review today lead quality",
        lane="core_ops",
        priority=10,
        payload={"source_phone": "8184489055", "created_by": "sms:8184489055"},
        constraints={"human_requested": True},
    )
    fake_cp.tasks[0]["status"] = "completed"
    fake_cp.tasks[0]["result"] = {"response": "Top leads ranked and summarized."}
    fake_cp.tasks[0]["finished_at"] = "2026-03-11T07:05:00"

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(sms_control, "_BRAIN_CONTROL_PLANE", fake_cp)
    monkeypatch.setattr(sms_control, "_get_brain_control_plane", lambda: fake_cp)

    messages = sms_control.get_pending_brain_completion_notifications("8184489055")
    assert len(messages) == 1
    assert "Brain task B#1 done." in messages[0]["message"]

    sms_control.mark_brain_task_notification_sent("8184489055", queued["task_id"], "completed")
    assert sms_control.get_pending_brain_completion_notifications("8184489055") == []


def test_voice_command_routes_plain_speech_into_brain_queue(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"sms_control_allowed_numbers": ["8184489055"]}))

    fake_cp = FakeBrainControlPlane()

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(sms_control, "_BRAIN_CONTROL_PLANE", fake_cp)
    monkeypatch.setattr(sms_control, "_get_brain_control_plane", lambda: fake_cp)

    response = sms_control.handle_inbound_voice_command("8184489055", "debug the booking flow")

    assert "Queued brain task B#1" in response
    assert fake_cp.tasks[0]["objective"] == "debug the booking flow"
    assert fake_cp.tasks[0]["lane"] == "systems"


def test_open_dashboard_command_executes_immediately(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"sms_control_allowed_numbers": ["8184489055"]}))

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)

    opened = []

    def fake_run(cmd, check, timeout):
        opened.append((cmd, check, timeout))

    monkeypatch.setattr(sms_control.subprocess, "run", fake_run)

    response = sms_control.handle_inbound_sms(
        "8184489055",
        "open the dashboard on my mac mini",
    )

    assert opened == [(["open", "https://crm.zoarbathroomrental.com/dashboard/"], True, sms_control.LOCAL_OPEN_TIMEOUT_SECONDS)]
    assert "Opened https://crm.zoarbathroomrental.com/dashboard/ on this Mac mini." in response


def test_voice_operator_brief_uses_completion_active_and_next_steps(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"sms_control_allowed_numbers": ["8184489055"]}))

    fake_cp = FakeBrainControlPlane()
    queued = fake_cp.enqueue_task(
        task_type="diagnostics",
        objective="debug the booking flow",
        lane="systems",
        priority=12,
        payload={"source_phone": "8184489055", "created_by": "sms:8184489055"},
        constraints={"human_requested": True},
    )
    fake_cp.tasks[0]["status"] = "running"
    fake_cp.tasks[0]["started_at"] = "2026-03-11T07:10:00"
    fake_cp.agents[0]["status"] = "running"
    fake_cp.agents[0]["current_task_id"] = queued["task_id"]

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(sms_control, "_BRAIN_CONTROL_PLANE", fake_cp)
    monkeypatch.setattr(sms_control, "_get_brain_control_plane", lambda: fake_cp)

    task_id = sms_control.queue_sms_task("8184489055", "open the dashboard on my mac mini")
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE task_board SET status='done', progress_note='Opened https://crm.zoarbathroomrental.com/dashboard/ on this Mac mini.' WHERE id=?",
        (task_id,),
    )
    conn.commit()
    conn.close()

    brief = sms_control.format_voice_operator_brief("8184489055")
    assert "Just finished:" in brief
    assert "verified done" in brief
    assert "Active now:" in brief
    assert "Best next actions:" in brief


def test_voice_operator_request_email_summary_executes_locally(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"sms_control_allowed_numbers": ["8184489055"]}))

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)

    sent = {}

    def fake_send(subject, body):
        sent["subject"] = subject
        sent["body"] = body
        return True, "Emailed kaiescobar09@gmail.com from zoarbathrooms@gmail.com with subject \"Nexus voice operator summary\"."

    monkeypatch.setattr(sms_control, "_send_self_email", fake_send)

    response = sms_control.handle_voice_operator_request("8184489055", "email me the summary")

    assert sent["subject"] == "Nexus voice operator summary"
    assert "Nexus voice operator summary" in sent["body"]
    assert "Emailed kaiescobar09@gmail.com" in response


def test_natural_owner_code_request_creates_pending_approval(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"sms_control_allowed_numbers": ["8184489055"]}))

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(sms_control, "_BRAIN_CONTROL_PLANE", None)

    response = sms_control.handle_inbound_sms(
        "8184489055",
        "change the Facebook ad headline to Luxury Restroom Trailer for Weddings",
    )

    assert "Prepared code task #1." in response
    assert "Approval needed. Reply APPROVE #1 or REJECT #1." in response
    assert "scripts/create_fb_campaign.py" in response

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    task = conn.execute(
        "SELECT status, title, files_involved FROM task_board WHERE id = 1"
    ).fetchone()
    approval = conn.execute(
        "SELECT status, target_ref, request_kind FROM sms_control_owner_approvals WHERE target_id = 1"
    ).fetchone()
    conn.close()

    assert dict(task) == {
        "status": "blocked",
        "title": "change the Facebook ad headline to Luxury Restroom Trailer for Weddings",
        "files_involved": json.dumps(
            [
                "scripts/create_fb_campaign.py",
                "scripts/fix_ad_copy.py",
                "scripts/fix_ad_copy_phase2.py",
                "scripts/create_lead_gen_campaign.py",
            ]
        ),
    }
    assert dict(approval) == {
        "status": "pending",
        "target_ref": "#1",
        "request_kind": "code edit",
    }

    notifications = sms_control.get_pending_operator_event_notifications("8184489055")
    assert any("Reply APPROVE #1 or REJECT #1." in item["message"] for item in notifications)


def test_approve_owner_code_task_opens_it_for_execution(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"sms_control_allowed_numbers": ["8184489055"]}))

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)

    sms_control.handle_inbound_sms(
        "8184489055",
        "change the Facebook ad headline to Luxury Restroom Trailer for Weddings",
    )
    response = sms_control.handle_inbound_sms("8184489055", "APPROVE #1")

    assert "#1 approved." in response
    assert "Task is now queued for execution." in response

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    task = conn.execute(
        "SELECT status, progress_note FROM task_board WHERE id = 1"
    ).fetchone()
    approval = conn.execute(
        "SELECT status FROM sms_control_owner_approvals WHERE target_id = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()

    assert dict(task) == {
        "status": "open",
        "progress_note": "Owner approved via SMS control.",
    }
    assert dict(approval) == {"status": "approved"}


def test_natural_leads_request_routes_to_brain_queue(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"sms_control_allowed_numbers": ["8184489055"]}))
    fake_cp = FakeBrainControlPlane()

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(sms_control, "_BRAIN_CONTROL_PLANE", fake_cp)
    monkeypatch.setattr(sms_control, "_get_brain_control_plane", lambda: fake_cp)

    response = sms_control.handle_inbound_sms(
        "8184489055",
        "find me 25 wedding venue leads in Ventura with phone and email",
    )

    assert "Queued brain task B#1" in response
    assert fake_cp.tasks[0]["lane"] == "lead_ops"
    assert fake_cp.tasks[0]["task_type"] == "venue_fit_scoring"
    assert fake_cp.tasks[0]["objective"] == "find me 25 wedding venue leads in Ventura with phone and email"


def test_loose_leads_request_expands_sfv_shorthand(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"sms_control_allowed_numbers": ["8184489055"]}))
    fake_cp = FakeBrainControlPlane()

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(sms_control, "_BRAIN_CONTROL_PLANE", fake_cp)
    monkeypatch.setattr(sms_control, "_get_brain_control_plane", lambda: fake_cp)

    response = sms_control.handle_inbound_sms(
        "8184489055",
        "find me 10 leads in the sfv",
    )

    assert "Queued brain task B#1" in response
    assert fake_cp.tasks[0]["lane"] == "lead_ops"
    assert fake_cp.tasks[0]["task_type"] == "lead_scoring"
    assert fake_cp.tasks[0]["objective"] == "find me 10 leads in the San Fernando Valley"


def test_business_cold_call_phrase_routes_to_lead_ops(tmp_path, monkeypatch):
    db_path = tmp_path / "memory.db"
    cfg_path = tmp_path / "config.json"
    _init_task_board(db_path)
    cfg_path.write_text(json.dumps({"sms_control_allowed_numbers": ["8184489055"]}))
    fake_cp = FakeBrainControlPlane()

    monkeypatch.setattr(sms_control, "DB_PATH", db_path)
    monkeypatch.setattr(sms_control, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(sms_control, "_BRAIN_CONTROL_PLANE", fake_cp)
    monkeypatch.setattr(sms_control, "_get_brain_control_plane", lambda: fake_cp)

    response = sms_control.handle_inbound_sms(
        "8184489055",
        "get me businesses in Encino to cold call",
    )

    assert "Queued brain task B#1" in response
    assert fake_cp.tasks[0]["lane"] == "lead_ops"
    assert fake_cp.tasks[0]["task_type"] == "lead_scoring"
    assert fake_cp.tasks[0]["objective"] == "get me businesses in Encino to cold call"
