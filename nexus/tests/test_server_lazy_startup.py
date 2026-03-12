import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import server
from core import lead_pipeline as lp_mod
from core import services as services_mod
from integrations import messaging as msg_mod
from telegram import bot as tg_bot_mod


def _reset_startup_state():
    server._STARTUP_LOCK = None
    server._STARTUP_COMPLETE = False
    server._CRM_BOOTSTRAP_LOCK = None


def test_ensure_startup_bootstrapped_runs_once(monkeypatch):
    _reset_startup_state()
    calls = []

    async def fake_run_startup(reason="lifespan"):
        calls.append(reason)

    monkeypatch.setattr(server, "_run_startup", fake_run_startup)

    first = asyncio.run(server._ensure_startup_bootstrapped(reason="facebook_webhook"))
    second = asyncio.run(server._ensure_startup_bootstrapped(reason="facebook_test"))

    assert first is True
    assert second is False
    assert calls == ["facebook_webhook"]


def test_ensure_startup_bootstrapped_serializes_concurrent_calls(monkeypatch):
    _reset_startup_state()
    calls = []

    async def fake_run_startup(reason="lifespan"):
        calls.append(reason)
        await asyncio.sleep(0.01)

    monkeypatch.setattr(server, "_run_startup", fake_run_startup)

    async def scenario():
        return await asyncio.gather(
            server._ensure_startup_bootstrapped(reason="first"),
            server._ensure_startup_bootstrapped(reason="second"),
        )

    results = asyncio.run(scenario())

    assert sorted(results) == [False, True]
    assert calls == ["first"]


def test_ensure_crm_alert_runtime_initializes_minimal_stack(monkeypatch):
    _reset_startup_state()
    state = {}

    class FakeBot:
        def set_handler(self, handler):
            state["handler"] = handler

        def set_approval_handler(self, handler):
            state["approval_handler"] = handler

        def set_reply_handler(self, handler):
            state["reply_handler"] = handler

        def set_b2b_approval_handler(self, handler):
            state["b2b_handler"] = handler

        async def start_polling(self):
            state["polling_started"] = True

        async def send_to_all(self, message):
            state["last_notify"] = message

    fake_bot = FakeBot()

    monkeypatch.setattr(
        server,
        "load_config",
        lambda: {
            "telegram_token": "tok",
            "telegram_chat_ids": [123],
            "gmail_address": "zoar@example.com",
            "gmail_app_password": "app-password",
        },
    )
    monkeypatch.setattr(tg_bot_mod, "get_bot", lambda: state.get("bot"))
    monkeypatch.setattr(
        tg_bot_mod,
        "init_bot",
        lambda token, chats: state.setdefault("bot", fake_bot),
    )
    monkeypatch.setattr(msg_mod, "get_messenger", lambda: state.get("messenger"))
    monkeypatch.setattr(
        server,
        "_start_messenger",
        lambda cfg: state.setdefault("messenger", object()),
    )
    monkeypatch.setattr(services_mod, "get_lead_service", lambda: state.get("lead_service"))
    monkeypatch.setattr(
        services_mod,
        "init_services",
        lambda **kwargs: state.setdefault("lead_service", object()),
    )
    monkeypatch.setattr(lp_mod, "get_pipeline", lambda: state.get("pipeline"))

    async def fake_start_lead_pipeline(cfg):
        state["pipeline"] = type("Pipeline", (), {"_running": True})()

    monkeypatch.setattr(server, "_start_lead_pipeline", fake_start_lead_pipeline)

    started = asyncio.run(server._ensure_crm_alert_runtime(reason="test"))

    assert started is True
    assert state["bot"] is fake_bot
    assert state["messenger"] is not None
    assert state["lead_service"] is not None
    assert state["pipeline"]._running is True
    assert state["polling_started"] is True
