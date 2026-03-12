import asyncio
import json
import sqlite3
from pathlib import Path


def _init_source_dbs(nexus_db: Path, beta_db: Path):
    nexus_db.parent.mkdir(parents=True, exist_ok=True)
    beta_db.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(nexus_db)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT,
            last_name TEXT,
            full_name TEXT,
            phone TEXT,
            email TEXT,
            source TEXT,
            booking_status TEXT,
            event_city TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS lead_timeline (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            event_type TEXT,
            details TEXT,
            ts TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS message_approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            status TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO leads
        (first_name, last_name, full_name, phone, email, source, booking_status, event_city, created_at)
        VALUES
        ('Kai', 'Escobar', 'Kai Escobar', '8184489055', 'kai@example.com', 'facebook_lead_form', 'qualified', 'Burbank', datetime('now')),
        ('Test', 'Lead', 'Test Lead', '', 'lead@example.com', 'cold_email', 'new', 'Glendale', datetime('now'))
        """
    )
    conn.execute(
        """
        INSERT INTO lead_timeline (lead_id, event_type, details, ts)
        VALUES (1, 'created', 'Lead created from form', datetime('now'))
        """
    )
    conn.execute("INSERT INTO message_approvals (lead_id, status) VALUES (1, 'pending')")
    conn.commit()
    conn.close()

    conn = sqlite3.connect(beta_db)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS beta_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue_name TEXT,
            website TEXT,
            city TEXT,
            region TEXT,
            venue_type TEXT,
            contact_name TEXT,
            contact_email TEXT,
            contact_phone TEXT,
            score REAL,
            tier TEXT,
            best_pitch_angle TEXT,
            validation_status TEXT,
            proof_snippet_1 TEXT,
            proof_snippet_2 TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO beta_leads
        (venue_name, website, city, region, venue_type, contact_name, contact_email, contact_phone, score, tier, best_pitch_angle, validation_status, proof_snippet_1, proof_snippet_2)
        VALUES
        ('Calabasas Estate Venue', 'https://estate.example.com', 'Calabasas', 'premium_outer', 'wedding_venue', 'Norberta', 'events@estate.example.com', '8185551212', 84, 'A', 'preferred_vendor', 'validated', 'Outdoor weddings', 'Vendor referrals allowed')
        """
    )
    conn.commit()
    conn.close()


def test_runpod_brain_lab_plan(tmp_path, monkeypatch):
    import core.runpod_brain_lab as rbl

    monkeypatch.setattr(rbl, "DB_PATH", tmp_path / "runpod_brain_lab.db")
    monkeypatch.setattr(rbl, "RUNS_ROOT", tmp_path / "runpod_brain_lab_runs")
    monkeypatch.setattr(rbl, "NEXUS_DB_PATH", tmp_path / "memory.db")
    monkeypatch.setattr(rbl, "BETA_DB_PATH", tmp_path / "beta_research.db")
    _init_source_dbs(rbl.NEXUS_DB_PATH, rbl.BETA_DB_PATH)

    lab = rbl.RunpodBrainLab()
    plan = lab.plan({"max_examples_per_dataset": 800})
    assert plan["ok"] is True
    assert plan["source_counts"]["leads"] >= 2
    assert "runpod_training_artifacts" in plan["separation"]
    assert plan["runtime"]["max_examples_per_dataset"] == 800
    assert plan["swarm_estimate"]["cpu_agent_workers"] >= 1
    assert "market_intel_agent" in plan["swarm_estimate"]["cpu_agents"]


def test_runpod_brain_lab_start_creates_separated_artifacts(tmp_path, monkeypatch):
    import core.runpod_brain_lab as rbl

    monkeypatch.setattr(rbl, "DB_PATH", tmp_path / "runpod_brain_lab.db")
    monkeypatch.setattr(rbl, "RUNS_ROOT", tmp_path / "runpod_brain_lab_runs")
    monkeypatch.setattr(rbl, "NEXUS_DB_PATH", tmp_path / "memory.db")
    monkeypatch.setattr(rbl, "BETA_DB_PATH", tmp_path / "beta_research.db")
    _init_source_dbs(rbl.NEXUS_DB_PATH, rbl.BETA_DB_PATH)

    lab = rbl.RunpodBrainLab()
    result = asyncio.run(lab.start({"max_examples_per_dataset": 300}))
    assert result["ok"] is True
    outputs = result["outputs"]

    lab_dir = Path(outputs["lab_dir"])
    assert (lab_dir / "training_artifacts").exists()
    assert (lab_dir / "exports").exists()
    assert (lab_dir / "integration_hooks").exists()

    datasets_manifest = Path(outputs["datasets_manifest_json"])
    assert datasets_manifest.exists()
    dm = json.loads(datasets_manifest.read_text())
    assert "datasets" in dm
    assert "lead_ranker" in dm["datasets"]

    export_manifest = Path(outputs["export_manifest_json"])
    assert export_manifest.exists()
    em = json.loads(export_manifest.read_text())
    assert "export_items" in em

    hooks_manifest = Path(outputs["integration_hooks_json"])
    assert hooks_manifest.exists()
    hm = json.loads(hooks_manifest.read_text())
    assert "hooks" in hm

    training_manifest = Path(outputs["training_manifest_json"])
    assert training_manifest.exists()
    tm = json.loads(training_manifest.read_text())
    swarm_settings = Path(tm["swarm_settings_json"])
    assert swarm_settings.exists()
    ss = json.loads(swarm_settings.read_text())
    assert ss["cpu_agent_workers"] >= 1
    assert ss["gpu_train_workers"] >= 1
