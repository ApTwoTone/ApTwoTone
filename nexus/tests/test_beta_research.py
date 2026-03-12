import pytest


@pytest.fixture
def engine(tmp_path, monkeypatch):
    import core.beta_research as br

    monkeypatch.setattr(br, "DB_PATH", tmp_path / "beta_research.db")
    return br.BetaResearchEngine()


def test_beta_research_safety_profile(engine):
    safety = engine.safety_profile()
    assert safety["mode"] == "research_only"
    assert safety["outbound_disabled"] is True
    assert safety["platform_logins_disabled"] is True
    assert safety["writes_to_production_db"] is False
    assert "facebook.com" in safety["blocked_platform_logins"]


@pytest.mark.asyncio
async def test_run_cycle_dry_run_uses_pipeline_without_storing(engine, monkeypatch):
    async def fake_collect_signals(**kwargs):
        return [
            {
                "name": "Test Estate Venue",
                "phone": "(818) 555-1212",
                "email": "events@testestate.com",
                "website": "https://testestate.com",
                "city": "Glendale, CA",
                "category": "wedding_venue",
                "signal_source": "google_maps",
            }
        ]

    monkeypatch.setattr(engine, "_build_signals", lambda **kwargs: [{"category": "wedding_venue"}])
    monkeypatch.setattr(engine, "_collect_signals", fake_collect_signals)

    result = await engine.run_cycle(dry_run=True)
    assert result["ok"] is True
    assert result["signals"] == 1
    assert result["decisioned"] >= 1

    leads = engine.list_leads(limit=50)
    assert leads == []
