import json


def _write_config(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_sprint_plan_includes_caps_and_estimates(tmp_path, monkeypatch):
    import core.runpod_sprint as rs

    monkeypatch.setattr(rs, "DB_PATH", tmp_path / "beta_research.db")
    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runpod_sprint")
    monkeypatch.setattr(rs, "CONFIG_PATH", tmp_path / "config.json")
    _write_config(rs.CONFIG_PATH, {"runpod": {"max_credits_usd": 17, "phase_caps": [5, 10, 15, 17]}})

    engine = rs.RunpodSprintEngine()
    plan = engine.plan()

    assert plan["ok"] is True
    assert plan["hard_stop_cap_usd"] == 17
    assert plan["phases"][0]["checkpoint_cap_usd"] == 5
    assert plan["phases"][-1]["checkpoint_cap_usd"] == 17
    assert plan["estimate"]["creative_statics_usable"] >= 120


def test_sprint_start_rejects_failed_preflight(tmp_path, monkeypatch):
    import asyncio
    import core.runpod_sprint as rs

    monkeypatch.setattr(rs, "DB_PATH", tmp_path / "beta_research.db")
    monkeypatch.setattr(rs, "RUNS_ROOT", tmp_path / "runpod_sprint")
    monkeypatch.setattr(rs, "CONFIG_PATH", tmp_path / "config.json")
    _write_config(rs.CONFIG_PATH, {"runpod": {"max_credits_usd": 17}})

    engine = rs.RunpodSprintEngine()

    # Force preflight failure to test start guard.
    monkeypatch.setattr(engine, "_preflight", lambda runtime: {"ok": False, "missing_dependencies": ["x"]})

    result = asyncio.run(engine.start({}))
    assert result["ok"] is False
    assert "Preflight failed" in result["error"]
