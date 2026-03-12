from __future__ import annotations

import json

import pytest

from core import service_control as sc


def test_google_voice_state_reflects_disable_flag():
    state = sc.get_service_state("google_voice", {"disable_sms_control_daemon": True})

    assert state["enabled"] is False
    assert state["blocked_by"] == ["disable_sms_control_daemon"]


def test_facebook_enable_rejects_global_browser_blocker():
    with pytest.raises(sc.ServiceControlError) as exc:
        sc.set_service_enabled(
            "facebook_lead_hunter",
            True,
            cfg={
                "disable_facebook_scraper": True,
                "disable_browser_scrapers": True,
            },
        )

    assert exc.value.blocked_by == ["disable_browser_scrapers"]


def test_set_service_enabled_persists_disable_flag(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"disable_sms_control_daemon": False}), encoding="utf-8")
    monkeypatch.setattr(sc, "CONFIG_PATH", cfg_path)

    updated = sc.set_service_enabled("google_voice", False)
    saved = json.loads(cfg_path.read_text(encoding="utf-8"))

    assert updated["enabled"] is False
    assert saved["disable_sms_control_daemon"] is True


def test_explicit_empty_config_does_not_fall_back_to_disk(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"disable_facebook_scraper": True}), encoding="utf-8")
    monkeypatch.setattr(sc, "CONFIG_PATH", cfg_path)

    state = sc.get_service_state("facebook_lead_hunter", cfg={})

    assert state["enabled"] is True
    assert state["blocked_by"] == []
