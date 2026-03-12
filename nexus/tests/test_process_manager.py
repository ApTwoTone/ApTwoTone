from __future__ import annotations

from scripts import process_manager as pm_mod


def _new_manager(monkeypatch, tmp_path):
    monkeypatch.setattr(pm_mod, "PID_DIR", tmp_path / "pids")
    monkeypatch.setattr(pm_mod, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(pm_mod.ProcessManager, "_register_external_services", lambda self: None)
    return pm_mod.ProcessManager()


def test_service_disabled_respects_sms_control_flag(monkeypatch, tmp_path):
    manager = _new_manager(monkeypatch, tmp_path)
    monkeypatch.setattr(manager, "_load_runtime_config", lambda: {"disable_sms_control_daemon": True})

    assert manager._service_disabled("sms_control_daemon") is True


def test_start_process_uses_external_service_launcher(monkeypatch, tmp_path):
    manager = _new_manager(monkeypatch, tmp_path)
    monkeypatch.setattr(manager, "_load_runtime_config", lambda: {})
    monkeypatch.delenv("NEXUS_DISABLE_FACEBOOK_SCRAPER", raising=False)
    monkeypatch.setattr(manager, "_find_service_pid", lambda svc: 0)

    started = {}

    def fake_start_external_service(svc):
        started["name"] = svc["name"]
        return True

    monkeypatch.setattr(manager, "_start_external_service", fake_start_external_service)

    assert manager.start_process("facebook_scraper") is True
    assert started["name"] == "facebook_scraper"


def test_start_process_refuses_disabled_service(monkeypatch, tmp_path):
    manager = _new_manager(monkeypatch, tmp_path)
    monkeypatch.setattr(manager, "_load_runtime_config", lambda: {"disable_facebook_scraper": True})

    updates = []
    monkeypatch.setattr(manager, "_update_db", lambda *args, **kwargs: updates.append((args, kwargs)))

    assert manager.start_process("facebook_scraper") is False
    assert updates
    assert updates[0][0][1] == "paused"
