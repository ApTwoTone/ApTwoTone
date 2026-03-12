"""
Runpod Brain Lab
----------------

Runpod-first build pipeline for Nexus Brain training/adaptation artifacts.

This module intentionally separates:
  1) Runpod training artifacts
  2) Export-ready model/adapters metadata
  3) Nexus integration hooks (contract only, no direct runtime wiring)
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

log = logging.getLogger("runpod_brain_lab")

DB_PATH = Path.home() / ".nexus" / "runpod_brain_lab.db"
NEXUS_DB_PATH = Path.home() / ".nexus" / "memory.db"
BETA_DB_PATH = Path.home() / ".nexus" / "beta_research.db"
RUNS_ROOT = Path.home() / ".nexus" / "runpod_brain_lab"

DEFAULT_CFG: Dict[str, Any] = {
    "enabled": True,
    "max_examples_per_dataset": 5000,
    "feedback_data_path": "",
    "feedback_weight": 0.30,
    "target_specialists": [
        "lead_ranker",
        "venue_partner_ranker",
        "creative_critic",
        "outreach_angle_selector",
        "ops_diagnostic_critic",
        "code_repair_critic",
        "research_planner",
        "conversion_specialist",
        "objection_resolver",
        "fb_media_buyer",
        "venue_partnership_closer",
        "follow_up_cadence_specialist",
        "quote_personalizer",
        "ad_creative_specialist",
        "vendor_referral_specialist",
        "seasonal_demand_predictor",
        "autonomous_entrepreneur",
    ],
    "base_models": {
        "lead_ranker": "Qwen/Qwen3.5-35B-A3B-Instruct",
        "venue_partner_ranker": "Qwen/Qwen3.5-35B-A3B-Instruct",
        "creative_critic": "Qwen/Qwen3.5-35B-A3B-Instruct",
        "outreach_angle_selector": "Qwen/Qwen3.5-35B-A3B-Instruct",
        "ops_diagnostic_critic": "Qwen/Qwen3.5-35B-A3B-Instruct",
        "code_repair_critic": "Qwen/Qwen3.5-35B-A3B-Instruct",
        "research_planner": "Qwen/Qwen3.5-35B-A3B-Instruct",
        "conversion_specialist": "Qwen/Qwen3.5-35B-A3B-Instruct",
        "objection_resolver": "Qwen/Qwen3.5-35B-A3B-Instruct",
        "fb_media_buyer": "Qwen/Qwen3.5-35B-A3B-Instruct",
        "venue_partnership_closer": "Qwen/Qwen3.5-35B-A3B-Instruct",
        "follow_up_cadence_specialist": "Qwen/Qwen3.5-35B-A3B-Instruct",
        "quote_personalizer": "Qwen/Qwen3.5-35B-A3B-Instruct",
        "ad_creative_specialist": "Qwen/Qwen3.5-35B-A3B-Instruct",
        "vendor_referral_specialist": "Qwen/Qwen3.5-35B-A3B-Instruct",
        "seasonal_demand_predictor": "Qwen/Qwen3.5-35B-A3B-Instruct",
        "autonomous_entrepreneur": "Qwen/Qwen3.5-35B-A3B-Instruct",
        "main_reasoner": "Qwen/Qwen3.5-35B-A3B",
    },
    "lora": {
        "rank": 16,
        "alpha": 32,
        "dropout": 0.05,
        "epochs": 2,
        "learning_rate": 2e-4,
        "micro_batch_size": 1,
        "gradient_accumulation_steps": 64,
    },
    "h100_profile": {
        "gpu": "H100 NVL",
        "mixed_precision": "bf16",
        "max_seq_len": 2048,
        "gradient_checkpointing": True,
        "dataloader_num_workers": 4,
        "dataloader_pin_memory": True,
        "dataloader_prefetch_factor": 2,
    },
    "swarm": {
        "enable_sub_agents": True,
        "cpu_agent_workers": 4,
        "gpu_train_workers": 1,
        "max_cpu_agents": 50,
        "max_concurrent_training": 1,
        "stop_on_first_gpu_failure": True,
        "logical_micro_agents": 256,
        "max_logical_micro_agents": 4096,
        "cpu_agents": [
            "market_intel_agent",
            "code_index_agent",
            "dataset_health_agent",
            "eval_readiness_agent",
            "micro_agent_fanout",
        ],
    },
    "training": {
        "min_examples_to_train": 32,
        "curriculum_order": [
            "lead_ranker",
            "venue_partner_ranker",
            "outreach_angle_selector",
            "conversion_specialist",
            "objection_resolver",
            "fb_media_buyer",
            "venue_partnership_closer",
            "follow_up_cadence_specialist",
            "quote_personalizer",
            "ad_creative_specialist",
            "vendor_referral_specialist",
            "seasonal_demand_predictor",
            "autonomous_entrepreneur",
            "ops_diagnostic_critic",
            "code_repair_critic",
            "research_planner",
            "creative_critic",
        ],
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(v: Any, default: str = "") -> str:
    try:
        return str(v if v is not None else default).strip()
    except Exception:
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return int(default)


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def _to_json(v: Any) -> str:
    return json.dumps(v if v is not None else {}, ensure_ascii=False)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


class RunpodBrainLab:
    def __init__(self) -> None:
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _ensure_schema(self) -> None:
        conn = self._conn()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runpod_brain_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    status TEXT DEFAULT 'running',
                    phase TEXT DEFAULT 'plan',
                    runtime_json TEXT DEFAULT '{}',
                    outputs_json TEXT DEFAULT '{}',
                    error TEXT DEFAULT '',
                    started_at TEXT DEFAULT (datetime('now')),
                    finished_at TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS runpod_brain_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    level TEXT DEFAULT 'info',
                    phase TEXT DEFAULT '',
                    message TEXT DEFAULT '',
                    payload_json TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS runpod_brain_artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    artifact_type TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    meta_json TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT (datetime('now'))
                );
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rbl_runs_started ON runpod_brain_runs(started_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rbl_events_run ON runpod_brain_events(run_id, id DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rbl_artifacts_run ON runpod_brain_artifacts(run_id, id DESC)")
            conn.commit()
        finally:
            conn.close()

    # ── Public API ───────────────────────────────────────────────────────

    def plan(self, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        runtime = self._runtime(overrides or {})
        source_counts = self._source_counts()
        specialist_targets = runtime.get("target_specialists") or []
        estimate = {
            "lead_ranker_examples": min(runtime["max_examples_per_dataset"], source_counts["leads"] + source_counts["beta_leads"] + 200),
            "venue_partner_examples": min(runtime["max_examples_per_dataset"], source_counts["beta_leads"] + 150),
            "creative_critic_examples": min(runtime["max_examples_per_dataset"], source_counts["creative_feedback"] + 180),
            "outreach_angle_examples": min(runtime["max_examples_per_dataset"], source_counts["beta_leads"] + source_counts["leads"] + 180),
            "ops_diagnostic_examples": min(runtime["max_examples_per_dataset"], source_counts["timeline_events"] + source_counts["approvals"] + 200),
            "code_repair_examples": min(runtime["max_examples_per_dataset"], source_counts["code_samples"] + 120),
            "research_planner_examples": min(runtime["max_examples_per_dataset"], source_counts["market_records"] + source_counts["beta_leads"] + 200),
            "conversion_specialist_examples": min(runtime["max_examples_per_dataset"], max(2200, source_counts["leads"] + source_counts["timeline_events"] + 400)),
            "objection_resolver_examples": min(runtime["max_examples_per_dataset"], max(1200, source_counts["leads"] + 380)),
            "fb_media_buyer_examples": min(runtime["max_examples_per_dataset"], source_counts["timeline_events"] + source_counts["leads"] + 300),
            "venue_partnership_closer_examples": min(runtime["max_examples_per_dataset"], source_counts["beta_leads"] + 240),
            "follow_up_cadence_examples": min(runtime["max_examples_per_dataset"], source_counts["timeline_events"] + 280),
            "quote_personalizer_examples": min(runtime["max_examples_per_dataset"], source_counts["leads"] + source_counts["beta_leads"] + 280),
            "ad_creative_specialist_examples": min(runtime["max_examples_per_dataset"], source_counts["creative_feedback"] + 520),
            "vendor_referral_specialist_examples": min(runtime["max_examples_per_dataset"], source_counts["beta_leads"] + 260),
            "seasonal_demand_predictor_examples": min(runtime["max_examples_per_dataset"], max(300, source_counts["market_records"] + source_counts["timeline_events"] + 140)),
            "autonomous_entrepreneur_examples": min(runtime["max_examples_per_dataset"], max(2000, source_counts["timeline_events"] + source_counts["market_records"] + 500)),
        }
        return {
            "ok": True,
            "runtime": runtime,
            "source_counts": source_counts,
            "specialists": specialist_targets,
            "estimate": estimate,
            "swarm_estimate": {
                "cpu_agent_workers": _safe_int(((runtime.get("swarm") or {}).get("cpu_agent_workers")), 4),
                "gpu_train_workers": _safe_int(((runtime.get("swarm") or {}).get("gpu_train_workers")), 1),
                "max_cpu_agents": _safe_int(((runtime.get("swarm") or {}).get("max_cpu_agents")), 50),
                "max_concurrent_training": _safe_int(((runtime.get("swarm") or {}).get("max_concurrent_training")), 1),
                "logical_micro_agents": _safe_int(((runtime.get("swarm") or {}).get("logical_micro_agents")), 256),
                "cpu_agents": (runtime.get("swarm") or {}).get("cpu_agents", []),
                "training_order": list(specialist_targets),
            },
            "artifact_layout": self._artifact_layout_hint(),
            "separation": {
                "runpod_training_artifacts": "runpod_lab/training_artifacts/*",
                "exported_model_adapters": "runpod_lab/exports/*",
                "nexus_integration_hooks": "runpod_lab/integration_hooks/*",
            },
        }

    async def start(self, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        runtime = self._runtime(overrides or {})
        run_id = self._create_run(runtime)
        started = time.time()
        try:
            run_dir = RUNS_ROOT / f"run_{run_id}"
            lab_dir = run_dir / "runpod_lab"
            lab_dir.mkdir(parents=True, exist_ok=True)

            self._set_phase(run_id, "datasets")
            self._event(run_id, "info", "datasets", "Building specialist datasets")
            datasets = self._build_datasets(run_id, runtime, lab_dir)

            self._set_phase(run_id, "labels")
            self._event(run_id, "info", "labels", "Writing labeling schema and annotation guides")
            labels = self._build_labeling_artifacts(run_id, runtime, lab_dir)

            self._set_phase(run_id, "memory_eval")
            self._event(run_id, "info", "memory_eval", "Generating memory/eval/experiment artifacts")
            memory_eval = self._build_memory_eval_artifacts(run_id, runtime, lab_dir, datasets)

            self._set_phase(run_id, "market_intel")
            self._event(run_id, "info", "market_intel", "Generating LA/SFV market intelligence artifacts")
            market_intel = self._build_market_intel_artifacts(run_id, runtime, lab_dir)

            self._set_phase(run_id, "training_configs")
            self._event(run_id, "info", "training_configs", "Writing specialist training configs for H100")
            training = self._build_training_configs(run_id, runtime, lab_dir, datasets)

            self._set_phase(run_id, "export_contract")
            self._event(run_id, "info", "export_contract", "Writing export contract + Nexus integration hooks")
            export_contract = self._build_export_contract(run_id, runtime, lab_dir)

            self._set_phase(run_id, "finalize")
            report = self._write_report(run_id, runtime, lab_dir, datasets, labels, memory_eval, training, export_contract)

            outputs = {
                "run_dir": str(run_dir),
                "lab_dir": str(lab_dir),
                "report_md": str(report),
                "datasets_manifest_json": str(lab_dir / "training_artifacts" / "datasets_manifest.json"),
                "labeling_manifest_json": str(lab_dir / "training_artifacts" / "labels" / "labeling_manifest.json"),
                "training_manifest_json": str(lab_dir / "training_artifacts" / "training_manifest.json"),
                "market_intel_manifest_json": str(lab_dir / "training_artifacts" / "market_intel_manifest.json"),
                "export_manifest_json": str(lab_dir / "exports" / "export_manifest_template.json"),
                "integration_hooks_json": str(lab_dir / "integration_hooks" / "integration_hooks_manifest.json"),
                "duration_ms": int((time.time() - started) * 1000),
            }
            self._finish_run(run_id, "completed", outputs=outputs, error="")
            return {"ok": True, "run_id": run_id, "outputs": outputs}
        except Exception as e:
            self._event(run_id, "error", self._phase(run_id), "Runpod Brain Lab failed", {"error": str(e)})
            self._finish_run(run_id, "failed", outputs={}, error=str(e))
            return {"ok": False, "run_id": run_id, "error": str(e)}

    def status(self, run_id: int = 0, include_events: bool = True) -> Dict[str, Any]:
        conn = self._conn()
        try:
            if int(run_id or 0) > 0:
                run = conn.execute("SELECT * FROM runpod_brain_runs WHERE id=?", (int(run_id),)).fetchone()
            else:
                run = conn.execute("SELECT * FROM runpod_brain_runs ORDER BY id DESC LIMIT 1").fetchone()
            if not run:
                return {"ok": True, "status": "idle", "run": None, "events": [], "artifacts": []}
            rid = int(run["id"])
            events: List[Dict[str, Any]] = []
            if include_events:
                rows = conn.execute(
                    """
                    SELECT id, level, phase, message, payload_json, created_at
                    FROM runpod_brain_events
                    WHERE run_id=?
                    ORDER BY id DESC
                    LIMIT 300
                    """,
                    (rid,),
                ).fetchall()
                events = [
                    {
                        "id": int(r["id"]),
                        "level": r["level"],
                        "phase": r["phase"],
                        "message": r["message"],
                        "payload": json.loads(r["payload_json"] or "{}"),
                        "created_at": r["created_at"],
                    }
                    for r in rows
                ]

            art_rows = conn.execute(
                """
                SELECT artifact_type, file_path, meta_json, created_at
                FROM runpod_brain_artifacts
                WHERE run_id=?
                ORDER BY id DESC
                LIMIT 300
                """,
                (rid,),
            ).fetchall()
            artifacts = [
                {
                    "artifact_type": r["artifact_type"],
                    "file_path": r["file_path"],
                    "meta": json.loads(r["meta_json"] or "{}"),
                    "created_at": r["created_at"],
                }
                for r in art_rows
            ]
            return {
                "ok": True,
                "status": run["status"],
                "run": {
                    "id": rid,
                    "status": run["status"],
                    "phase": run["phase"],
                    "runtime": json.loads(run["runtime_json"] or "{}"),
                    "outputs": json.loads(run["outputs_json"] or "{}"),
                    "error": run["error"] or "",
                    "started_at": run["started_at"],
                    "finished_at": run["finished_at"],
                },
                "events": events,
                "artifacts": artifacts,
            }
        finally:
            conn.close()

    def list_runs(self, limit: int = 20) -> Dict[str, Any]:
        lim = max(1, min(int(limit or 20), 200))
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT id, status, phase, started_at, finished_at, error FROM runpod_brain_runs ORDER BY id DESC LIMIT ?",
                (lim,),
            ).fetchall()
            return {"ok": True, "runs": [dict(r) for r in rows]}
        finally:
            conn.close()

    # ── Build stages ─────────────────────────────────────────────────────

    def _build_datasets(self, run_id: int, runtime: Dict[str, Any], lab_dir: Path) -> Dict[str, Any]:
        root = lab_dir / "training_artifacts" / "datasets"
        root.mkdir(parents=True, exist_ok=True)

        builders = {
            "lead_ranker": self._build_lead_ranker_dataset,
            "venue_partner_ranker": self._build_venue_partner_dataset,
            "creative_critic": self._build_creative_critic_dataset,
            "outreach_angle_selector": self._build_outreach_dataset,
            "ops_diagnostic_critic": self._build_ops_dataset,
            "code_repair_critic": self._build_code_repair_dataset,
            "research_planner": self._build_research_planner_dataset,
            "conversion_specialist": self._build_conversion_dataset,
            "objection_resolver": self._build_objection_resolver_dataset,
            "fb_media_buyer": self._build_fb_media_buyer_dataset,
            "venue_partnership_closer": self._build_venue_partnership_closer_dataset,
            "follow_up_cadence_specialist": self._build_follow_up_cadence_dataset,
            "quote_personalizer": self._build_quote_personalizer_dataset,
            "ad_creative_specialist": self._build_ad_creative_specialist_dataset,
            "vendor_referral_specialist": self._build_vendor_referral_specialist_dataset,
            "seasonal_demand_predictor": self._build_seasonal_demand_predictor_dataset,
            "autonomous_entrepreneur": self._build_autonomous_entrepreneur_dataset,
        }
        target = runtime.get("target_specialists") or []
        ordered = [s for s in target if s in builders] + [s for s in builders.keys() if s not in set(target)]
        datasets: Dict[str, Any] = {}
        for specialist in ordered:
            if specialist not in set(target):
                continue
            datasets[specialist] = builders[specialist](root / f"{specialist}_train.jsonl", runtime)

        manifest = {
            "created_at": _now(),
            "datasets": datasets,
        }
        manifest_path = root.parent / "datasets_manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        self._artifact(run_id, "datasets_manifest_json", manifest_path, {"datasets": len(manifest["datasets"])})
        return {"manifest": manifest, "path": manifest_path}

    def _build_labeling_artifacts(self, run_id: int, runtime: Dict[str, Any], lab_dir: Path) -> Dict[str, Any]:
        root = lab_dir / "training_artifacts" / "labels"
        root.mkdir(parents=True, exist_ok=True)

        schemas = {
            "lead_quality_label_schema.json": {
                "label_name": "lead_quality_score",
                "scale": {"min": 0, "max": 100},
                "required_fields": ["booking_intent", "contactability", "fit_to_service_area", "budget_fit"],
                "explanations_required": True,
                "grade_map": {"A": "90-100", "B": "80-89", "C": "70-79", "D": "60-69", "F": "0-59"},
            },
            "creative_quality_label_schema.json": {
                "label_name": "creative_quality_score",
                "scale": {"min": 0, "max": 100},
                "required_fields": ["hook_strength", "clarity", "visual_relevance", "cta_strength", "local_relevance"],
                "reject_conditions": ["off-brand", "wrong product", "text unreadable", "audio quality poor"],
            },
            "outreach_angle_label_schema.json": {
                "label_name": "best_pitch_angle",
                "classes": ["preferred_vendor", "backup_vendor", "referral_partner", "overflow_peak_date"],
                "required_rationale_fields": ["venue_type", "vendor_list_signal", "contactability", "premium_signal"],
            },
            "experiment_outcome_label_schema.json": {
                "label_name": "experiment_outcome",
                "classes": ["win", "mixed", "loss"],
                "required_fields": ["hypothesis", "metric_delta", "confidence", "next_action"],
            },
            "code_patch_quality_label_schema.json": {
                "label_name": "code_patch_quality_score",
                "scale": {"min": 0, "max": 100},
                "required_fields": ["correctness", "safety", "regression_risk", "clarity"],
                "reject_conditions": ["introduces_security_risk", "breaks_existing_behavior", "missing_tests_for_critical_path"],
            },
            "research_plan_quality_label_schema.json": {
                "label_name": "research_plan_quality_score",
                "scale": {"min": 0, "max": 100},
                "required_fields": ["source_quality", "market_specificity", "actionability", "evidence"],
                "reject_conditions": ["hallucinated_sources", "generic_non_local_strategy"],
            },
            "meta_ads_diagnostic_label_schema.json": {
                "label_name": "meta_ads_diagnostic_score",
                "scale": {"min": 0, "max": 100},
                "required_fields": ["metric_interpretation", "hook_diagnosis", "test_design", "expected_impact"],
                "reject_conditions": ["metric_blind_recommendations", "untestable_advice"],
            },
            "website_cro_label_schema.json": {
                "label_name": "website_cro_score",
                "scale": {"min": 0, "max": 100},
                "required_fields": ["cta_quality", "trust_signal_quality", "mobile_clarity", "conversion_friction"],
                "reject_conditions": ["non_actionable_feedback", "no_local_context"],
            },
            "media_buyer_strategy_label_schema.json": {
                "label_name": "media_buyer_strategy_score",
                "scale": {"min": 0, "max": 100},
                "required_fields": ["budget_structure", "test_matrix", "risk_controls", "optimization_logic"],
                "reject_conditions": ["no_guardrails", "single-test_overfitting"],
            },
            "funnel_diagnostic_label_schema.json": {
                "label_name": "funnel_diagnostic_score",
                "scale": {"min": 0, "max": 100},
                "required_fields": ["dropoff_stage", "cause_hypotheses", "experiment_design", "measurement"],
                "reject_conditions": ["no_experiment_path", "non_measurable_actions"],
            },
            "la_market_mapper_label_schema.json": {
                "label_name": "la_market_mapper_score",
                "scale": {"min": 0, "max": 100},
                "required_fields": ["city_specificity", "segment_fit", "actionability", "evidence_alignment"],
                "reject_conditions": ["generic_geo_advice", "no_city_prioritization"],
            },
            "conversion_specialist_label_schema.json": {
                "label_name": "conversion_specialist_score",
                "scale": {"min": 0, "max": 100},
                "required_fields": ["deposit_anchor", "follow_up_sequence", "event_fit", "close_quality"],
                "reject_conditions": ["missing_160_deposit_anchor", "missing_48h_followup"],
            },
            "objection_resolver_label_schema.json": {
                "label_name": "objection_resolver_score",
                "scale": {"min": 0, "max": 100},
                "required_fields": ["objection_mapping", "variation_count", "tone_fit", "conversion_recovery"],
                "reject_conditions": ["less_than_5_variations", "non_contextual_response"],
            },
            "fb_media_buyer_label_schema.json": {
                "label_name": "fb_media_buyer_score",
                "scale": {"min": 0, "max": 100},
                "required_fields": ["budget_cap_logic", "cpl_strategy", "audience_segmentation", "test_design"],
                "reject_conditions": ["budget_violation", "no_test_matrix"],
            },
            "venue_partnership_closer_label_schema.json": {
                "label_name": "venue_partnership_closer_score",
                "scale": {"min": 0, "max": 100},
                "required_fields": ["tier_offer_fit", "close_strength", "persona_fit", "reliability_positioning"],
                "reject_conditions": ["unclear_tier_offer", "missing_close_prompt"],
            },
            "autonomous_entrepreneur_label_schema.json": {
                "label_name": "autonomous_entrepreneur_score",
                "scale": {"min": 0, "max": 100},
                "required_fields": ["kpi_reasoning", "experiment_design", "memory_update_quality", "safety_compliance"],
                "reject_conditions": ["policy_bypass_tactics", "no_measurement_plan", "unsafe_automation"],
            },
        }
        for name, body in schemas.items():
            (root / name).write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")

        guide = root / "annotation_guide.md"
        guide.write_text(
            (
                "# Runpod Brain Lab Annotation Guide\n\n"
                "## Goals\n"
                "- Maximize real booking impact for Zoar Bathroom Rentals.\n"
                "- Prefer labels that improve lead quality and conversion diagnostics.\n\n"
                "## Rules\n"
                "- Do not hallucinate facts not present in the sample.\n"
                "- If uncertain, mark low confidence and explain uncertainty.\n"
                "- For lead quality, penalize non-contactable records heavily.\n"
                "- For creative quality, penalize mismatch with restroom trailer product.\n"
                "- For outreach angles, prefer backup-vendor framing when primary-vendor confidence is weak.\n"
            ),
            encoding="utf-8",
        )
        manifest = {
            "created_at": _now(),
            "schemas": list(schemas.keys()),
            "guide": str(guide),
        }
        manifest_path = root / "labeling_manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        self._artifact(run_id, "labeling_manifest_json", manifest_path, {"schemas": len(schemas)})
        return {"manifest": manifest, "path": manifest_path}

    def _build_memory_eval_artifacts(
        self,
        run_id: int,
        runtime: Dict[str, Any],
        lab_dir: Path,
        datasets: Dict[str, Any],
    ) -> Dict[str, Any]:
        mem_root = lab_dir / "training_artifacts" / "memory"
        eval_root = lab_dir / "training_artifacts" / "eval"
        exp_root = lab_dir / "training_artifacts" / "experiments"
        mem_root.mkdir(parents=True, exist_ok=True)
        eval_root.mkdir(parents=True, exist_ok=True)
        exp_root.mkdir(parents=True, exist_ok=True)

        semantic = self._write_semantic_memory(mem_root / "semantic_memory.jsonl")
        episodic = self._write_episodic_memory(mem_root / "episodic_memory.jsonl")
        strategic = self._write_strategic_memory(mem_root / "strategic_memory.jsonl")
        procedural = self._write_procedural_memory(mem_root / "procedural_memory.jsonl")

        rubrics = self._write_eval_rubrics(eval_root)
        benchmark = self._write_eval_benchmark(eval_root / "benchmark_suite.jsonl")
        templates = self._write_experiment_templates(exp_root / "experiment_templates.json")

        manifest = {
            "created_at": _now(),
            "memory": {
                "semantic_count": semantic,
                "episodic_count": episodic,
                "strategic_count": strategic,
                "procedural_count": procedural,
            },
            "eval": {"rubrics": rubrics, "benchmark_examples": benchmark},
            "experiments": {"templates": templates},
        }
        path = lab_dir / "training_artifacts" / "memory_eval_manifest.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        self._artifact(run_id, "memory_eval_manifest_json", path, manifest)
        return {"manifest": manifest, "path": path}

    def _build_market_intel_artifacts(self, run_id: int, runtime: Dict[str, Any], lab_dir: Path) -> Dict[str, Any]:
        root = lab_dir / "training_artifacts" / "market_intel"
        root.mkdir(parents=True, exist_ok=True)

        la_sfv_cities = [
            "San Fernando", "Burbank", "Glendale", "Calabasas", "Hidden Hills", "North Hollywood",
            "Valley Village", "Studio City", "Sherman Oaks", "Van Nuys", "Panorama City", "Valley Glen",
            "Woodland Hills", "Canoga Park", "Winnetka", "Reseda", "Tarzana", "Encino", "West Hills",
            "Chatsworth", "Sylmar", "Granada Hills", "Mission Hills", "Arleta", "Pacoima", "Lake View Terrace",
            "Sun Valley", "Sunland", "Tujunga", "Shadow Hills", "Toluca Lake", "Universal City",
            "Malibu", "Thousand Oaks", "Westlake Village", "Agoura Hills", "Santa Clarita", "Pasadena",
        ]
        premium_pockets = ["Malibu", "Calabasas", "Hidden Hills", "Westlake Village", "Pasadena", "Agoura Hills"]
        categories = ["wedding_venue", "estate_venue", "ranch_venue", "vineyard_venue", "event_venue", "vendor_partner"]

        cities_path = root / "la_sfv_city_profile.json"
        cities_path.write_text(
            json.dumps(
                {
                    "created_at": _now(),
                    "cities": la_sfv_cities,
                    "premium_pockets": premium_pockets,
                    "priority_categories": categories,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        leads = self._fetch_beta_leads(limit=4000)
        graph_rows: List[Dict[str, Any]] = []
        for lead in leads:
            venue = _safe_text(lead.get("venue_name"))
            city = _safe_text(lead.get("city"))
            vt = _safe_text(lead.get("venue_type") or "event_venue")
            angle = _safe_text(lead.get("best_pitch_angle") or "referral_partner")
            if not venue:
                continue
            graph_rows.append(
                {
                    "node": {"type": "venue", "id": venue, "city": city, "venue_type": vt},
                    "edge": {"type": "recommended_angle", "target": angle, "weight": float(_safe_float(lead.get("score"), 0.0) / 100.0)},
                }
            )
            if city:
                graph_rows.append(
                    {
                        "node": {"type": "venue", "id": venue},
                        "edge": {"type": "located_in", "target": city, "weight": 1.0},
                    }
                )
            graph_rows.append(
                {
                    "node": {"type": "venue", "id": venue},
                    "edge": {"type": "category", "target": vt, "weight": 1.0},
                }
            )

        if not graph_rows:
            for city in la_sfv_cities[:12]:
                graph_rows.append(
                    {
                        "node": {"type": "city", "id": city},
                        "edge": {"type": "priority", "target": "lead_discovery", "weight": 0.6 if city in premium_pockets else 0.4},
                    }
                )
        graph_path = root / "market_knowledge_graph.jsonl"
        self._write_jsonl(graph_path, graph_rows)

        priors = {
            "created_at": _now(),
            "venue_type_priors": {
                "wedding_venue": {"conversion_prior": 0.72, "first_pitch": "backup_vendor"},
                "estate_venue": {"conversion_prior": 0.69, "first_pitch": "preferred_vendor"},
                "ranch_venue": {"conversion_prior": 0.64, "first_pitch": "backup_vendor"},
                "vineyard_venue": {"conversion_prior": 0.66, "first_pitch": "backup_vendor"},
                "event_venue": {"conversion_prior": 0.58, "first_pitch": "referral_partner"},
                "vendor_partner": {"conversion_prior": 0.62, "first_pitch": "referral_partner"},
            },
            "message_priors": {
                "backup_vendor": {"subject": "Backup restroom coverage for booked-out dates"},
                "preferred_vendor": {"subject": "Preferred restroom partner for your events?"},
                "referral_partner": {"subject": "Referral partner idea ($200 per booked event)"},
            },
        }
        priors_path = root / "market_priors.json"
        priors_path.write_text(json.dumps(priors, ensure_ascii=False, indent=2), encoding="utf-8")

        manifest = {
            "created_at": _now(),
            "city_profile_json": str(cities_path),
            "market_graph_jsonl": str(graph_path),
            "market_priors_json": str(priors_path),
            "graph_rows": len(graph_rows),
        }
        path = lab_dir / "training_artifacts" / "market_intel_manifest.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        self._artifact(run_id, "market_intel_manifest_json", path, manifest)
        return {"manifest": manifest, "path": path}

    def _build_training_configs(
        self,
        run_id: int,
        runtime: Dict[str, Any],
        lab_dir: Path,
        datasets: Dict[str, Any],
    ) -> Dict[str, Any]:
        root = lab_dir / "training_artifacts" / "training_configs"
        root.mkdir(parents=True, exist_ok=True)
        specs = runtime.get("target_specialists") or []
        lora = runtime.get("lora") or {}
        base_models = runtime.get("base_models") or {}
        h100 = runtime.get("h100_profile") or {}
        swarm = runtime.get("swarm") or {}
        cfg_paths: List[str] = []

        for specialist in specs:
            ds_name = f"{specialist}_train.jsonl"
            cfg = {
                "specialist": specialist,
                "base_model": base_models.get(
                    specialist,
                    base_models.get("main_reasoner", "Qwen/Qwen3.5-35B-A3B"),
                ),
                "dataset_path": f"../datasets/{ds_name}",
                "output_dir": f"../../exports/{specialist}",
                "feedback_data_path": _safe_text(runtime.get("feedback_data_path") or ""),
                "feedback_weight": _safe_float(runtime.get("feedback_weight"), 0.30),
                "lora": lora,
                "h100_profile": h100,
                "notes": [
                    "Run on H100 NVL pod.",
                    "Keep adapters specialist-specific; do not merge into single monolith during lab phase.",
                    "Evaluate with benchmark suite before export.",
                ],
            }
            p = root / f"{specialist}.json"
            p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            cfg_paths.append(str(p))

        train_py = root / "train_specialist.py"
        local_train_specialist = Path(__file__).with_name("train_specialist.py")
        if local_train_specialist.exists():
            train_py.write_text(local_train_specialist.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            raise RuntimeError(f"Missing local training template: {local_train_specialist}")

        export_py = root / "export_specialist_gguf.py"
        local_export_specialist = Path(__file__).with_name("export_specialist_gguf.py")
        if local_export_specialist.exists():
            export_py.write_text(local_export_specialist.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            raise RuntimeError(f"Missing local export template: {local_export_specialist}")

        req = root / "requirements.txt"
        req.write_text(
            "\n".join(
                [
                    "numpy<2.0",
                    "torch==2.6.0+cu124",
                    "transformers==5.3.0.dev0",
                    "peft==0.18.2.dev0",
                    "unsloth",
                    "accelerate",
                    "bitsandbytes",
                    "datasets",
                    "trl",
                    "sentencepiece>=0.2.0",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        run_all = root / "run_all_specialists.sh"
        run_all.write_text(
            (
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "ROOT_DIR=\"$(cd \"$(dirname \"$0\")\" && pwd)\"\n"
                "python3 -m pip install --upgrade pip\n"
                "python3 -m pip install -r \"$ROOT_DIR/requirements.txt\"\n"
                "for cfg in \"$ROOT_DIR\"/*.json; do\n"
                "  [[ \"$cfg\" == *\"training_manifest.json\" ]] && continue\n"
                "  [[ \"$cfg\" == *\"integration_hooks_manifest.json\" ]] && continue\n"
                "  echo \"[RunpodLab] training with $cfg\"\n"
                "  python3 \"$ROOT_DIR/train_specialist.py\" \"$cfg\"\n"
                "  echo \"[RunpodLab] exporting gguf for $cfg\"\n"
                "  python3 \"$ROOT_DIR/export_specialist_gguf.py\" --config \"$cfg\" || true\n"
                "done\n"
            ),
            encoding="utf-8",
        )
        try:
            run_all.chmod(0o755)
        except Exception:
            pass

        eval_py = root / "evaluate_specialists.py"
        eval_py.write_text(
            (
                "import json\n"
                "import sys\n"
                "from pathlib import Path\n"
                "\n"
                "def _load_json(path: Path):\n"
                "    try:\n"
                "        return json.loads(path.read_text(encoding='utf-8'))\n"
                "    except Exception:\n"
                "        return {}\n"
                "\n"
                "def main():\n"
                "    root = Path(__file__).resolve().parent\n"
                "    exports = (root / '../../exports').resolve()\n"
                "    report = {'created_at': None, 'specialists': [], 'passed': True}\n"
                "    min_examples = 20\n"
                "    max_train_loss = 4.5\n"
                "    for d in sorted([p for p in exports.iterdir() if p.is_dir()]):\n"
                "        mpath = d / 'training_metrics.json'\n"
                "        entry = {'specialist': d.name, 'status': 'fail', 'reasons': []}\n"
                "        if not mpath.exists():\n"
                "            entry['reasons'].append('missing_training_metrics')\n"
                "            report['passed'] = False\n"
                "            report['specialists'].append(entry)\n"
                "            continue\n"
                "        m = _load_json(mpath)\n"
                "        train_examples = int(m.get('train_examples', 0) or 0)\n"
                "        train_loss = float(m.get('train_loss', 999.0) or 999.0)\n"
                "        if train_examples < min_examples:\n"
                "            entry['reasons'].append('insufficient_train_examples')\n"
                "        if train_loss > max_train_loss:\n"
                "            entry['reasons'].append('train_loss_too_high')\n"
                "        if not entry['reasons']:\n"
                "            entry['status'] = 'pass'\n"
                "            entry['metrics'] = {'train_examples': train_examples, 'train_loss': train_loss}\n"
                "        else:\n"
                "            report['passed'] = False\n"
                "            entry['metrics'] = {'train_examples': train_examples, 'train_loss': train_loss}\n"
                "        report['specialists'].append(entry)\n"
                "    out = exports / 'eval_gate_report.json'\n"
                "    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')\n"
                "    print('[RunpodLab] Wrote', out)\n"
                "    if not report['passed']:\n"
                "        raise SystemExit(2)\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
            encoding="utf-8",
        )

        training_cfg = runtime.get("training") or {}
        curriculum = [s for s in (training_cfg.get("curriculum_order") or []) if s in set(specs)]
        fallback = [s for s in specs if s not in set(curriculum)]
        training_order = curriculum + fallback
        swarm_settings = {
            "created_at": _now(),
            "max_cpu_agents": max(1, min(_safe_int(swarm.get("max_cpu_agents"), 50), 500)),
            "cpu_agent_workers": max(1, min(_safe_int(swarm.get("cpu_agent_workers"), 4), 32)),
            "gpu_train_workers": max(1, min(_safe_int(swarm.get("gpu_train_workers"), 1), 8)),
            "max_concurrent_training": max(1, min(_safe_int(swarm.get("max_concurrent_training"), 1), 3)),
            "stop_on_first_gpu_failure": bool(swarm.get("stop_on_first_gpu_failure", True)),
            "enable_sub_agents": bool(swarm.get("enable_sub_agents", True)),
            "logical_micro_agents": max(1, min(_safe_int(swarm.get("logical_micro_agents"), 256), _safe_int(swarm.get("max_logical_micro_agents"), 4096))),
            "max_logical_micro_agents": max(64, min(_safe_int(swarm.get("max_logical_micro_agents"), 4096), 20000)),
            "stall_timeout_sec": max(60, min(_safe_int(swarm.get("stall_timeout_sec"), 300), 3600)),
            "max_restarts_per_agent": max(0, min(_safe_int(swarm.get("max_restarts_per_agent"), 1), 5)),
            "cpu_agents": [a for a in (swarm.get("cpu_agents") or []) if _safe_text(a)],
            "training_order": [s for s in training_order if _safe_text(s)],
            "min_examples_to_train": max(1, min(_safe_int(training_cfg.get("min_examples_to_train"), 32), 5000)),
            "notes": [
                "gpu_train_workers > 1 is only recommended for multi-GPU pods.",
                "On single H100, keep gpu_train_workers=1 to avoid OOM contention.",
            ],
        }
        swarm_settings_path = root / "swarm_settings.json"
        swarm_settings_path.write_text(json.dumps(swarm_settings, ensure_ascii=False, indent=2), encoding="utf-8")

        swarm_py = root / "agent_swarm.py"
        swarm_py.write_text(
            (
                "import json\n"
                "import concurrent.futures as cf\n"
                "import subprocess\n"
                "import time\n"
                "from pathlib import Path\n"
                "from datetime import datetime, timezone\n"
                "\n"
                "def _now():\n"
                "    return datetime.now(timezone.utc).isoformat()\n"
                "\n"
                "def _load_settings(root: Path):\n"
                "    p = root / 'swarm_settings.json'\n"
                "    if not p.exists():\n"
                "        return {}\n"
                "    try:\n"
                "        return json.loads(p.read_text(encoding='utf-8'))\n"
                "    except Exception:\n"
                "        return {}\n"
                "\n"
                "def _count_jsonl_rows(path: Path):\n"
                "    rows = 0\n"
                "    if not path.exists():\n"
                "        return rows\n"
                "    with path.open('r', encoding='utf-8') as f:\n"
                "        for _ in f:\n"
                "            rows += 1\n"
                "    return rows\n"
                "\n"
                "def _run_cpu_market_agent(root: Path):\n"
                "    market = (root / '../market_intel').resolve()\n"
                "    graph = market / 'market_knowledge_graph.jsonl'\n"
                "    rows = 0\n"
                "    if graph.exists():\n"
                "        with graph.open('r', encoding='utf-8') as f:\n"
                "            for _ in f:\n"
                "                rows += 1\n"
                "    out = (root / '../../exports/market_intel_agent_report.json').resolve()\n"
                "    out.write_text(json.dumps({'agent': 'market_intel_agent', 'rows': rows, 'created_at': _now()}, ensure_ascii=False, indent=2), encoding='utf-8')\n"
                "    return rows\n"
                "\n"
                "def _run_cpu_code_agent(root: Path):\n"
                "    ds = (root / '../datasets/code_repair_critic_train.jsonl').resolve()\n"
                "    rows = 0\n"
                "    if ds.exists():\n"
                "        with ds.open('r', encoding='utf-8') as f:\n"
                "            for _ in f:\n"
                "                rows += 1\n"
                "    out = (root / '../../exports/code_index_agent_report.json').resolve()\n"
                "    out.write_text(json.dumps({'agent': 'code_index_agent', 'rows': rows, 'created_at': _now()}, ensure_ascii=False, indent=2), encoding='utf-8')\n"
                "    return rows\n"
                "\n"
                "def _run_cpu_dataset_health_agent(root: Path):\n"
                "    ds_dir = (root / '../datasets').resolve()\n"
                "    stats = []\n"
                "    for p in sorted(ds_dir.glob('*.jsonl')):\n"
                "        rows = 0\n"
                "        with p.open('r', encoding='utf-8') as f:\n"
                "            for _ in f:\n"
                "                rows += 1\n"
                "        stats.append({'dataset': p.name, 'rows': rows})\n"
                "    out = (root / '../../exports/dataset_health_agent_report.json').resolve()\n"
                "    out.write_text(json.dumps({'agent': 'dataset_health_agent', 'datasets': stats, 'created_at': _now()}, ensure_ascii=False, indent=2), encoding='utf-8')\n"
                "    return len(stats)\n"
                "\n"
                "def _run_cpu_eval_readiness_agent(root: Path):\n"
                "    eval_dir = (root / '../eval').resolve()\n"
                "    required = [\n"
                "        'lead_quality_rubric.json',\n"
                "        'creative_quality_rubric.json',\n"
                "        'outreach_quality_rubric.json',\n"
                "        'code_patch_quality_rubric.json',\n"
                "        'research_plan_quality_rubric.json',\n"
                "        'benchmark_suite.jsonl',\n"
                "    ]\n"
                "    present = []\n"
                "    missing = []\n"
                "    for name in required:\n"
                "        p = eval_dir / name\n"
                "        if p.exists():\n"
                "            present.append(name)\n"
                "        else:\n"
                "            missing.append(name)\n"
                "    out = (root / '../../exports/eval_readiness_agent_report.json').resolve()\n"
                "    out.write_text(json.dumps({'agent': 'eval_readiness_agent', 'present': present, 'missing': missing, 'created_at': _now()}, ensure_ascii=False, indent=2), encoding='utf-8')\n"
                "    return len(present)\n"
                "\n"
                "def _run_cpu_micro_agent_fanout(root: Path, logical_agents: int, cpu_workers: int):\n"
                "    market = (root / '../market_intel/market_knowledge_graph.jsonl').resolve()\n"
                "    dataset = (root / '../datasets/outreach_angle_selector_train.jsonl').resolve()\n"
                "    market_rows = _count_jsonl_rows(market)\n"
                "    dataset_rows = _count_jsonl_rows(dataset)\n"
                "    logical_agents = max(1, int(logical_agents or 1))\n"
                "    work = []\n"
                "    for idx in range(logical_agents):\n"
                "        work.append({'agent_id': idx, 'market_probe': (idx * 7) % max(1, market_rows), 'dataset_probe': (idx * 13) % max(1, dataset_rows)})\n"
                "    def _worker(item):\n"
                "        score = (item['market_probe'] + item['dataset_probe']) % 100\n"
                "        return {'agent_id': item['agent_id'], 'score': score}\n"
                "    results = []\n"
                "    with cf.ThreadPoolExecutor(max_workers=max(1, cpu_workers)) as ex:\n"
                "        futs = [ex.submit(_worker, w) for w in work]\n"
                "        for fut in cf.as_completed(futs):\n"
                "            results.append(fut.result())\n"
                "    avg_score = round(sum(r['score'] for r in results) / max(1, len(results)), 2)\n"
                "    out = (root / '../../exports/micro_agent_fanout_report.json').resolve()\n"
                "    out.write_text(json.dumps({'agent': 'micro_agent_fanout', 'logical_agents': logical_agents, 'completed': len(results), 'avg_score': avg_score, 'market_rows': market_rows, 'dataset_rows': dataset_rows, 'created_at': _now()}, ensure_ascii=False, indent=2), encoding='utf-8')\n"
                "    return len(results)\n"
                "\n"
                "def _write_dashboard(root: Path, summary: dict, phase: str):\n"
                "    out = (root / '../../exports/swarm_dashboard.json').resolve()\n"
                "    payload = dict(summary)\n"
                "    payload['phase'] = phase\n"
                "    payload['updated_at'] = _now()\n"
                "    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')\n"
                "\n"
                "def _run_cpu_agent_with_timeout(root: Path, agent_name: str, fn, stall_timeout_sec: int, max_restarts: int):\n"
                "    attempt = 0\n"
                "    while attempt <= max_restarts:\n"
                "        start = time.time()\n"
                "        with cf.ThreadPoolExecutor(max_workers=1) as ex:\n"
                "            fut = ex.submit(fn, root)\n"
                "            try:\n"
                "                rows = fut.result(timeout=max(60, stall_timeout_sec))\n"
                "                return {'agent': agent_name, 'status': 'ok', 'rows': rows, 'attempt': attempt + 1, 'elapsed_sec': round(time.time() - start, 2)}\n"
                "            except Exception as e:\n"
                "                attempt += 1\n"
                "                if attempt > max_restarts:\n"
                "                    return {'agent': agent_name, 'status': 'fail', 'error': str(e), 'attempt': attempt, 'elapsed_sec': round(time.time() - start, 2)}\n"
                "\n"
                "def _train_one(root: Path, cfg_name: str, min_examples_to_train: int = 1):\n"
                "    cfg = root / cfg_name\n"
                "    if not cfg.exists():\n"
                "        return {'cfg': cfg_name, 'status': 'skipped', 'reason': 'missing_cfg'}\n"
                "    try:\n"
                "        body = json.loads(cfg.read_text(encoding='utf-8'))\n"
                "        ds_rel = body.get('dataset_path', '')\n"
                "        ds = (cfg.parent / ds_rel).resolve()\n"
                "        ds_rows = _count_jsonl_rows(ds)\n"
                "        if ds_rows < max(1, int(min_examples_to_train or 1)):\n"
                "            return {'cfg': cfg_name, 'status': 'skipped', 'reason': 'insufficient_examples', 'dataset_rows': ds_rows}\n"
                "    except Exception:\n"
                "        pass\n"
                "    p = subprocess.run(['python3', str(root / 'train_specialist.py'), str(cfg)], check=False)\n"
                "    if p.returncode != 0:\n"
                "        return {'cfg': cfg_name, 'status': 'fail', 'code': p.returncode}\n"
                "    export = subprocess.run(['python3', str(root / 'export_specialist_gguf.py'), '--config', str(cfg)], capture_output=True, text=True, check=False)\n"
                "    export_ok = export.returncode == 0\n"
                "    export_summary = (export.stdout or export.stderr or '').strip()[:1200]\n"
                "    return {'cfg': cfg_name, 'status': 'ok', 'code': p.returncode, 'gguf_export_ok': export_ok, 'gguf_export_summary': export_summary}\n"
                "\n"
                "def main():\n"
                "    root = Path(__file__).resolve().parent\n"
                "    settings = _load_settings(root)\n"
                "    max_cpu_agents = max(1, int(settings.get('max_cpu_agents', 50) or 50))\n"
                "    cpu_workers = max(1, min(int(settings.get('cpu_agent_workers', 2) or 2), max_cpu_agents))\n"
                "    max_concurrent_training = max(1, int(settings.get('max_concurrent_training', 1) or 1))\n"
                "    gpu_workers = max(1, min(int(settings.get('gpu_train_workers', 1) or 1), max_concurrent_training))\n"
                "    stop_on_first_gpu_failure = bool(settings.get('stop_on_first_gpu_failure', True))\n"
                "    logical_micro_agents = max(1, int(settings.get('logical_micro_agents', 128) or 128))\n"
                "    min_examples_to_train = max(1, int(settings.get('min_examples_to_train', 1) or 1))\n"
                "    stall_timeout_sec = max(60, int(settings.get('stall_timeout_sec', 300) or 300))\n"
                "    max_restarts_per_agent = max(0, int(settings.get('max_restarts_per_agent', 1) or 1))\n"
                "    found_cfgs = {p.name for p in root.glob('*.json')}\n"
                "    requested_order = settings.get('training_order') or []\n"
                "    preferred = [f\"{str(s)}.json\" for s in requested_order if str(s).strip()]\n"
                "    preferred_cfgs = [c for c in preferred if c in found_cfgs]\n"
                "    extra_cfgs = sorted([c for c in found_cfgs if c not in set(preferred) and c not in {'training_manifest.json', 'swarm_settings.json'}])\n"
                "    cfgs = preferred_cfgs + extra_cfgs\n"
                "    cpu_agent_map = {\n"
                "        'market_intel_agent': _run_cpu_market_agent,\n"
                "        'code_index_agent': _run_cpu_code_agent,\n"
                "        'dataset_health_agent': _run_cpu_dataset_health_agent,\n"
                "        'eval_readiness_agent': _run_cpu_eval_readiness_agent,\n"
                "        'micro_agent_fanout': lambda r: _run_cpu_micro_agent_fanout(r, logical_micro_agents=logical_micro_agents, cpu_workers=cpu_workers),\n"
                "    }\n"
                "    requested_agents = settings.get('cpu_agents') or list(cpu_agent_map.keys())\n"
                "    cpu_agents = [a for a in requested_agents if a in cpu_agent_map]\n"
                "    summary = {\n"
                "        'started_at': _now(),\n"
                "        'settings': {\n"
                "            'max_cpu_agents': max_cpu_agents,\n"
                "            'cpu_agent_workers': cpu_workers,\n"
                "            'max_concurrent_training': max_concurrent_training,\n"
                "            'gpu_train_workers': gpu_workers,\n"
                "            'stop_on_first_gpu_failure': stop_on_first_gpu_failure,\n"
                "            'logical_micro_agents': logical_micro_agents,\n"
                "            'min_examples_to_train': min_examples_to_train,\n"
                "            'stall_timeout_sec': stall_timeout_sec,\n"
                "            'max_restarts_per_agent': max_restarts_per_agent,\n"
                "            'cpu_agents': cpu_agents,\n"
                "        },\n"
                "        'cpu_agents': [],\n"
                "        'gpu_train': [],\n"
                "    }\n"
                "    _write_dashboard(root, summary, 'cpu_agents')\n"
                "    with cf.ThreadPoolExecutor(max_workers=cpu_workers) as ex:\n"
                "        fut_to_agent = {ex.submit(_run_cpu_agent_with_timeout, root, name, cpu_agent_map[name], stall_timeout_sec, max_restarts_per_agent): name for name in cpu_agents}\n"
                "        for fut in cf.as_completed(fut_to_agent):\n"
                "            name = fut_to_agent[fut]\n"
                "            try:\n"
                "                summary['cpu_agents'].append(fut.result())\n"
                "            except Exception as e:\n"
                "                summary['cpu_agents'].append({'agent': name, 'status': 'fail', 'error': str(e)})\n"
                "            _write_dashboard(root, summary, 'cpu_agents')\n"
                "\n"
                "    _write_dashboard(root, summary, 'gpu_training')\n"
                "    if gpu_workers <= 1:\n"
                "        for cfg in cfgs:\n"
                "            result = _train_one(root, cfg, min_examples_to_train=min_examples_to_train)\n"
                "            summary['gpu_train'].append(result)\n"
                "            _write_dashboard(root, summary, 'gpu_training')\n"
                "            if stop_on_first_gpu_failure and result.get('status') != 'ok':\n"
                "                break\n"
                "    else:\n"
                "        with cf.ThreadPoolExecutor(max_workers=gpu_workers) as ex:\n"
                "            fut_to_cfg = {ex.submit(_train_one, root, cfg, min_examples_to_train): cfg for cfg in cfgs}\n"
                "            for fut in cf.as_completed(fut_to_cfg):\n"
                "                cfg = fut_to_cfg[fut]\n"
                "                try:\n"
                "                    summary['gpu_train'].append(fut.result())\n"
                "                except Exception as e:\n"
                "                    summary['gpu_train'].append({'cfg': cfg, 'status': 'fail', 'error': str(e)})\n"
                "                _write_dashboard(root, summary, 'gpu_training')\n"
                "    eval_run = subprocess.run(['python3', str(root / 'evaluate_specialists.py')], check=False)\n"
                "    summary['eval_exit_code'] = eval_run.returncode\n"
                "    summary['finished_at'] = _now()\n"
                "    _write_dashboard(root, summary, 'completed')\n"
                "    out = (root / '../../exports/agent_swarm_summary.json').resolve()\n"
                "    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')\n"
                "    print('[RunpodLab] Wrote', out)\n"
                "    if eval_run.returncode != 0:\n"
                "        raise SystemExit(eval_run.returncode)\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
            encoding="utf-8",
        )

        run_swarm = root / "run_agent_swarm.sh"
        run_swarm.write_text(
            (
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "ROOT_DIR=\"$(cd \"$(dirname \"$0\")\" && pwd)\"\n"
                "export HF_HOME=\"${HF_HOME:-/root/.cache/huggingface}\"\n"
                "export HUGGINGFACE_HUB_CACHE=\"${HUGGINGFACE_HUB_CACHE:-$HF_HOME/hub}\"\n"
                "export TRANSFORMERS_CACHE=\"${TRANSFORMERS_CACHE:-$HF_HOME/transformers}\"\n"
                "export HF_DATASETS_CACHE=\"${HF_DATASETS_CACHE:-$HF_HOME/datasets}\"\n"
                "mkdir -p \"$HUGGINGFACE_HUB_CACHE\" \"$TRANSFORMERS_CACHE\" \"$HF_DATASETS_CACHE\"\n"
                "if [[ \"${RUNPOD_LAB_SKIP_PIP:-1}\" == \"1\" ]]; then\n"
                "  echo \"[RunpodLab] Skipping pip bootstrap (RUNPOD_LAB_SKIP_PIP=1)\"\n"
                "else\n"
                "  python3 -m pip install --upgrade pip\n"
                "  python3 -m pip install -r \"$ROOT_DIR/requirements.txt\"\n"
                "fi\n"
                "echo \"[RunpodLab] Swarm settings:\"\n"
                "cat \"$ROOT_DIR/swarm_settings.json\" || true\n"
                "python3 \"$ROOT_DIR/agent_swarm.py\"\n"
            ),
            encoding="utf-8",
        )
        try:
            run_swarm.chmod(0o755)
        except Exception:
            pass

        manifest = {
            "created_at": _now(),
            "specialists": specs,
            "configs": cfg_paths,
            "script_train_specialist_py": str(train_py),
            "script_export_specialist_gguf_py": str(export_py),
            "script_run_all_sh": str(run_all),
            "script_agent_swarm_py": str(swarm_py),
            "script_run_agent_swarm_sh": str(run_swarm),
            "script_eval_specialists_py": str(eval_py),
            "swarm_settings_json": str(swarm_settings_path),
            "requirements_txt": str(req),
            "separation_note": "Training configs generate artifacts into ./exports only. No Nexus runtime writes in this phase.",
        }
        path = root.parent / "training_manifest.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        self._artifact(run_id, "training_manifest_json", path, {"specialists": len(specs)})
        return {"manifest": manifest, "path": path}

    def _build_export_contract(self, run_id: int, runtime: Dict[str, Any], lab_dir: Path) -> Dict[str, Any]:
        exports = lab_dir / "exports"
        hooks = lab_dir / "integration_hooks"
        exports.mkdir(parents=True, exist_ok=True)
        hooks.mkdir(parents=True, exist_ok=True)

        export_manifest = {
            "version": "1.0",
            "created_at": _now(),
            "packaging_policy": {
                "strategy": "adapters_only",
                "max_total_export_gb": 12,
                "max_single_adapter_gb": 4,
                "notes": [
                    "Do not export full base models from Runpod lab.",
                    "Export only LoRA adapter weights + tokenizer/config + eval metrics.",
                    "Nexus local runtime resolves base model separately."
                ],
            },
            "export_items": [
                {
                    "name": "specialist_adapter",
                    "required_files": [
                        "adapter_model.safetensors",
                        "adapter_config.json",
                        "tokenizer_config.json",
                        "training_metrics.json",
                    ],
                    "optional_files": ["eval_report.json", "router_profile.json"],
                }
            ],
            "deployment_target": "Mac mini (Nexus local runtime)",
            "promotion_policy": [
                "No adapter can be promoted without benchmark pass.",
                "No adapter can control outbound actions directly.",
                "Adapters first run in recommendation-only mode.",
            ],
        }
        export_manifest_path = exports / "export_manifest_template.json"
        export_manifest_path.write_text(json.dumps(export_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        integration_manifest = {
            "version": "1.0",
            "created_at": _now(),
            "hooks": {
                "model_registry_hook": {
                    "expected_input": "exported adapter directory + metadata",
                    "output": "registered model profile in Nexus router",
                },
                "memory_loader_hook": {
                    "expected_input": "memory/*.jsonl artifacts",
                    "output": "local memory store import plan",
                },
                "eval_loader_hook": {
                    "expected_input": "eval/*.jsonl and rubric json",
                    "output": "evaluation baseline records",
                },
                "safety_gate_hook": {
                    "expected_input": "risk profile from model metadata",
                    "output": "approval requirements by task type",
                },
            },
            "note": "Contract only; no direct runtime integration is executed in Runpod lab phase.",
        }
        integration_path = hooks / "integration_hooks_manifest.json"
        integration_path.write_text(json.dumps(integration_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        readme = hooks / "README.md"
        readme.write_text(
            (
                "# Nexus Integration Hooks (Contract)\n\n"
                "This folder defines how exported Runpod artifacts will be integrated later.\n"
                "No hooks are executed in this phase.\n\n"
                "## Separation\n"
                "1. Runpod training artifacts: `training_artifacts/`\n"
                "2. Export-ready adapters: `exports/`\n"
                "3. Nexus hook contracts only: `integration_hooks/`\n"
            ),
            encoding="utf-8",
        )

        transfer_sh = hooks / "transfer_to_mac.sh"
        transfer_sh.write_text(
            (
                "#!/bin/bash\n"
                "set -euo pipefail\n"
                "POD_IP=${1:?Usage: bash transfer_to_mac.sh [ip] [port]}\n"
                "POD_PORT=${2:?Usage: bash transfer_to_mac.sh [ip] [port]}\n"
                "DEST=\"$HOME/.nexus/brain_lab/run_14\"\n"
                "mkdir -p \"$DEST/models\" \"$DEST/specialist_exports\" \"$DEST/training_data\" \"$DEST/logs\"\n"
                "rsync -avz --progress -e \"ssh -p $POD_PORT -o StrictHostKeyChecking=no\" root@$POD_IP:/workspace/nexus_brain_lab/run_14/exports/gguf/ \"$DEST/models/\"\n"
                "rsync -avz --progress -e \"ssh -p $POD_PORT -o StrictHostKeyChecking=no\" root@$POD_IP:/workspace/nexus_brain_lab/run_14/runpod_lab/exports/ \"$DEST/specialist_exports/\"\n"
                "rsync -avz --progress -e \"ssh -p $POD_PORT -o StrictHostKeyChecking=no\" root@$POD_IP:/workspace/nexus_brain_lab/run_14/coding_data/ \"$DEST/training_data/\"\n"
                "echo \"Transfer complete: $DEST\"\n"
            ),
            encoding="utf-8",
        )
        try:
            transfer_sh.chmod(0o755)
        except Exception:
            pass

        setup_sh = hooks / "setup_on_mac.sh"
        setup_sh.write_text(
            (
                "#!/bin/bash\n"
                "set -euo pipefail\n"
                "MODELS_DIR=\"$HOME/.nexus/brain_lab/run_14/models\"\n"
                "if ! ollama list >/dev/null 2>&1; then\n"
                "  open -a Ollama || true\n"
                "  sleep 5\n"
                "fi\n"
                "if [ -d \"$MODELS_DIR/coding_agent\" ] && [ -f \"$MODELS_DIR/coding_agent/Modelfile\" ]; then\n"
                "  cd \"$MODELS_DIR/coding_agent\"\n"
                "  ollama create nexus-coder -f Modelfile || true\n"
                "fi\n"
                "for specialist_dir in \"$MODELS_DIR\"/*/; do\n"
                "  [ -d \"$specialist_dir\" ] || continue\n"
                "  name=$(basename \"$specialist_dir\")\n"
                "  if [ \"$name\" = \"coding_agent\" ]; then continue; fi\n"
                "  if [ -f \"$specialist_dir/Modelfile\" ]; then\n"
                "    cd \"$specialist_dir\"\n"
                "    ollama create \"nexus-$name\" -f Modelfile || true\n"
                "  fi\n"
                "done\n"
                "ollama list | grep -i nexus || true\n"
            ),
            encoding="utf-8",
        )
        try:
            setup_sh.chmod(0o755)
        except Exception:
            pass

        self._artifact(run_id, "export_manifest_template_json", export_manifest_path, {})
        self._artifact(run_id, "integration_hooks_manifest_json", integration_path, {})
        self._artifact(run_id, "transfer_to_mac_sh", transfer_sh, {})
        self._artifact(run_id, "setup_on_mac_sh", setup_sh, {})
        return {
            "export_manifest": str(export_manifest_path),
            "integration_hooks_manifest": str(integration_path),
            "transfer_to_mac_sh": str(transfer_sh),
            "setup_on_mac_sh": str(setup_sh),
        }

    # ── Dataset builders ─────────────────────────────────────────────────

    def _build_lead_ranker_dataset(self, path: Path, runtime: Dict[str, Any]) -> Dict[str, Any]:
        limit = _safe_int(runtime.get("max_examples_per_dataset"), 5000)
        rows = self._fetch_lead_rows(limit=limit)
        records: List[Dict[str, Any]] = []
        for row in rows:
            score = self._heuristic_lead_score(row)
            records.append(
                {
                    "messages": [
                        {"role": "system", "content": "Score lead quality for Zoar Bathroom Rentals on 0-100 scale."},
                        {"role": "user", "content": json.dumps(row, ensure_ascii=False)},
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "lead_quality_score": score,
                                    "tier": "A" if score >= 80 else ("B" if score >= 65 else ("C" if score >= 50 else "D")),
                                    "reasoning": self._lead_reason(row, score),
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ]
                }
            )
        if not records:
            records = self._seed_lead_ranker_examples()
        self._write_jsonl(path, records)
        return {"path": str(path), "examples": len(records)}

    def _build_venue_partner_dataset(self, path: Path, runtime: Dict[str, Any]) -> Dict[str, Any]:
        limit = _safe_int(runtime.get("max_examples_per_dataset"), 5000)
        rows = self._fetch_beta_leads(limit=limit)
        records: List[Dict[str, Any]] = []
        for row in rows:
            total_score = _safe_float(row.get("score", 0), 0.0)
            angle = _safe_text(row.get("best_pitch_angle", "referral_partner"))
            records.append(
                {
                    "messages": [
                        {"role": "system", "content": "Rank venue partner fit for Zoar and choose first outreach angle."},
                        {"role": "user", "content": json.dumps(row, ensure_ascii=False)},
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "venue_partner_score": total_score,
                                    "tier": _safe_text(row.get("tier", "C")),
                                    "recommended_angle": angle or "referral_partner",
                                    "rationale": self._venue_reason(row),
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ]
                }
            )
        if not records:
            records = self._seed_venue_examples()
        self._write_jsonl(path, records)
        return {"path": str(path), "examples": len(records)}

    def _build_creative_critic_dataset(self, path: Path, runtime: Dict[str, Any]) -> Dict[str, Any]:
        limit = _safe_int(runtime.get("max_examples_per_dataset"), 5000)
        rows = self._fetch_creative_feedback(limit=limit)
        records: List[Dict[str, Any]] = []
        for row in rows:
            score = _safe_float(row.get("score", 0), 0.0)
            records.append(
                {
                    "messages": [
                        {"role": "system", "content": "Critique ad creative quality for Meta conversion performance."},
                        {"role": "user", "content": json.dumps(row, ensure_ascii=False)},
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "creative_quality_score": score,
                                    "grade": "pass" if score >= 70 else "reject",
                                    "improvements": self._creative_improvement_hints(row, score),
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ]
                }
            )
        if not records:
            records = self._seed_creative_examples()
        self._write_jsonl(path, records)
        return {"path": str(path), "examples": len(records)}

    def _build_outreach_dataset(self, path: Path, runtime: Dict[str, Any]) -> Dict[str, Any]:
        limit = _safe_int(runtime.get("max_examples_per_dataset"), 5000)
        leads = self._fetch_beta_leads(limit=limit // 2)
        core_leads = self._fetch_lead_rows(limit=limit // 2)
        records: List[Dict[str, Any]] = []

        for row in leads:
            angle = _safe_text(row.get("best_pitch_angle", "referral_partner"))
            records.append(
                {
                    "messages": [
                        {"role": "system", "content": "Select best outreach angle and first-line opener for Zoar."},
                        {"role": "user", "content": json.dumps(row, ensure_ascii=False)},
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "best_pitch_angle": angle,
                                    "subject_line": self._subject_for_angle(angle),
                                    "opening_line": self._opening_for_lead(row),
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ]
                }
            )

        for row in core_leads:
            angle = "referral_partner"
            records.append(
                {
                    "messages": [
                        {"role": "system", "content": "Pick outreach strategy for this lead."},
                        {"role": "user", "content": json.dumps(row, ensure_ascii=False)},
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "best_pitch_angle": angle,
                                    "subject_line": self._subject_for_angle(angle),
                                    "opening_line": self._opening_for_lead(row),
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ]
                }
            )

        if not records:
            records = self._seed_outreach_examples()
        self._write_jsonl(path, records[:limit])
        return {"path": str(path), "examples": min(len(records), limit)}

    def _build_ops_dataset(self, path: Path, runtime: Dict[str, Any]) -> Dict[str, Any]:
        limit = _safe_int(runtime.get("max_examples_per_dataset"), 5000)
        events = self._fetch_timeline_events(limit=limit)
        records: List[Dict[str, Any]] = []
        for row in events:
            records.append(
                {
                    "messages": [
                        {"role": "system", "content": "Diagnose ops issue and propose safe next action."},
                        {"role": "user", "content": json.dumps(row, ensure_ascii=False)},
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "diagnosis": "Potential funnel friction detected",
                                    "next_action": "create experiment and monitor 48h response delta",
                                    "risk": "low",
                                    "requires_approval": False,
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ]
                }
            )
        if not records:
            records = self._seed_ops_examples()
        self._write_jsonl(path, records[:limit])
        return {"path": str(path), "examples": min(len(records), limit)}

    def _build_code_repair_dataset(self, path: Path, runtime: Dict[str, Any]) -> Dict[str, Any]:
        limit = _safe_int(runtime.get("max_examples_per_dataset"), 5000)
        records: List[Dict[str, Any]] = []
        files = self._collect_repo_python_files(limit=max(30, min(500, limit)))
        for f in files:
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if not text.strip():
                continue
            snippet = text[:2200]
            records.append(
                {
                    "messages": [
                        {"role": "system", "content": "Review code for reliability bugs and propose safe, minimal patches with tests."},
                        {"role": "user", "content": json.dumps({"file": str(f), "snippet": snippet}, ensure_ascii=False)},
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "risk_level": "medium",
                                    "issues": ["Add guardrails around external IO", "Improve explicit error handling path"],
                                    "patch_plan": [
                                        "Preserve existing behavior and add narrow validation checks",
                                        "Add one regression-focused unit test for error path",
                                    ],
                                    "requires_approval": True,
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ]
                }
            )
            if len(records) >= limit:
                break
        if not records:
            records = self._seed_code_repair_examples()
        self._write_jsonl(path, records[:limit])
        return {"path": str(path), "examples": min(len(records), limit)}

    def _build_research_planner_dataset(self, path: Path, runtime: Dict[str, Any]) -> Dict[str, Any]:
        limit = _safe_int(runtime.get("max_examples_per_dataset"), 5000)
        leads = self._fetch_beta_leads(limit=max(80, min(1500, limit)))
        records: List[Dict[str, Any]] = []
        for row in leads:
            city = _safe_text(row.get("city") or "Los Angeles")
            venue = _safe_text(row.get("venue_name") or "Unknown Venue")
            vt = _safe_text(row.get("venue_type") or "event_venue")
            records.append(
                {
                    "messages": [
                        {"role": "system", "content": "Create a source-grounded deep research plan for LA/SFV events market opportunities."},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "city": city,
                                    "venue_name": venue,
                                    "venue_type": vt,
                                    "goal": "increase qualified bookings for Zoar Bathroom Rentals",
                                },
                                ensure_ascii=False,
                            ),
                        },
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "research_plan": [
                                        "Collect venue-level vendor policy and preferred-vendor signals",
                                        "Estimate booking fit by event type, city, and premium segment",
                                        "Generate two angle hypotheses and rank by expected conversion",
                                    ],
                                    "deliverables": ["evidence_log", "priority_targets", "first_message_variants"],
                                    "safety": {"no_outbound": True, "approval_required_for_actions": True},
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ]
                }
            )
            if len(records) >= limit:
                break
        if not records:
            records = self._seed_research_examples()
        self._write_jsonl(path, records[:limit])
        return {"path": str(path), "examples": min(len(records), limit)}

    def _build_meta_ads_dataset(self, path: Path, runtime: Dict[str, Any]) -> Dict[str, Any]:
        limit = _safe_int(runtime.get("max_examples_per_dataset"), 5000)
        leads = self._fetch_lead_rows(limit=max(150, min(2500, limit)))
        records: List[Dict[str, Any]] = []
        for idx, row in enumerate(leads):
            lead_score = self._heuristic_lead_score(row)
            cpl = max(4.5, min(38.0, 32.0 - (lead_score / 5.5)))
            ctr = max(0.4, min(4.2, 0.6 + (lead_score / 35.0)))
            hook = "guest_comfort" if (idx % 2 == 0) else "luxury_clarity"
            records.append(
                {
                    "messages": [
                        {"role": "system", "content": "Act as a senior Meta ads analyst. Diagnose performance and propose high-ROI next tests."},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "lead": row,
                                    "metrics": {"ctr": round(ctr, 2), "cpl": round(cpl, 2), "cpc": round(cpl / max(1.0, ctr), 2), "hook": hook},
                                },
                                ensure_ascii=False,
                            ),
                        },
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "diagnosis": "Hook/audience alignment can improve qualified lead quality.",
                                    "priority_changes": [
                                        "Test stronger first-3s hook with trailer exterior + event context",
                                        "Split ad sets by ENG/ESP with matching creative language",
                                        "Rotate CTA toward date-availability intent",
                                    ],
                                    "expected_impact": {"cpl_delta_pct": -12, "qualified_lead_delta_pct": 15},
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ]
                }
            )
            if len(records) >= limit:
                break
        if not records:
            records = self._seed_meta_ads_examples()
        self._write_jsonl(path, records[:limit])
        return {"path": str(path), "examples": min(len(records), limit)}

    def _build_website_cro_dataset(self, path: Path, runtime: Dict[str, Any]) -> Dict[str, Any]:
        limit = _safe_int(runtime.get("max_examples_per_dataset"), 5000)
        leads = self._fetch_beta_leads(limit=max(120, min(2200, limit)))
        records: List[Dict[str, Any]] = []
        for row in leads:
            website = _safe_text(row.get("website"))
            if not website:
                continue
            records.append(
                {
                    "messages": [
                        {"role": "system", "content": "Act as a senior CRO reviewer for event-service websites."},
                        {"role": "user", "content": json.dumps({"website": website, "city": row.get("city"), "venue_type": row.get("venue_type")}, ensure_ascii=False)},
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "cro_score": 72,
                                    "issues": ["Weak above-the-fold CTA", "Low local proof signal"],
                                    "fixes": [
                                        "Add immediate date-check CTA above fold",
                                        "Add trust proof: booking photos + city-specific social proof",
                                        "Add mobile-first inquiry form with fewer fields",
                                    ],
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ]
                }
            )
            if len(records) >= limit:
                break
        if not records:
            records = self._seed_website_cro_examples()
        self._write_jsonl(path, records[:limit])
        return {"path": str(path), "examples": min(len(records), limit)}

    def _build_media_buyer_dataset(self, path: Path, runtime: Dict[str, Any]) -> Dict[str, Any]:
        limit = _safe_int(runtime.get("max_examples_per_dataset"), 5000)
        beta = self._fetch_beta_leads(limit=max(120, min(2500, limit)))
        records: List[Dict[str, Any]] = []
        for row in beta:
            score = int(_safe_float(row.get("score"), 55))
            records.append(
                {
                    "messages": [
                        {"role": "system", "content": "Act as a senior media buyer. Build budget and test allocation for bookings growth."},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "city": row.get("city"),
                                    "venue_type": row.get("venue_type"),
                                    "partner_score": score,
                                    "angle": row.get("best_pitch_angle"),
                                },
                                ensure_ascii=False,
                            ),
                        },
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "budget_split": {"prospecting": 0.6, "retargeting": 0.25, "creative_testing": 0.15},
                                    "test_plan": [
                                        "Launch 3-hook set (comfort, premium, logistics)",
                                        "Hold one control creative for benchmark",
                                        "Cut bottom quartile creatives every 48h",
                                    ],
                                    "guardrails": ["No budget increase without quality-lead lift", "Pause ad sets with high CPL + low reply intent"],
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ]
                }
            )
            if len(records) >= limit:
                break
        if not records:
            records = self._seed_media_buyer_examples()
        self._write_jsonl(path, records[:limit])
        return {"path": str(path), "examples": min(len(records), limit)}

    def _build_funnel_diagnostic_dataset(self, path: Path, runtime: Dict[str, Any]) -> Dict[str, Any]:
        limit = _safe_int(runtime.get("max_examples_per_dataset"), 5000)
        timeline = self._fetch_timeline_events(limit=max(180, min(2500, limit)))
        records: List[Dict[str, Any]] = []
        for row in timeline:
            records.append(
                {
                    "messages": [
                        {"role": "system", "content": "Act as a senior funnel diagnostic expert. Find drop-off causes and remediation experiments."},
                        {"role": "user", "content": json.dumps(row, ensure_ascii=False)},
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "dropoff_stage": "post_first_contact",
                                    "root_cause_hypotheses": [
                                        "Messaging mismatch with event urgency",
                                        "Slow follow-up cadence",
                                        "Insufficient trust proof in first touch",
                                    ],
                                    "next_experiment": {
                                        "name": "response_time_vs_reply_rate",
                                        "metric": "positive_reply_rate",
                                        "window_hours": 48,
                                    },
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ]
                }
            )
            if len(records) >= limit:
                break
        if not records:
            records = self._seed_funnel_examples()
        self._write_jsonl(path, records[:limit])
        return {"path": str(path), "examples": min(len(records), limit)}

    def _build_la_market_mapper_dataset(self, path: Path, runtime: Dict[str, Any]) -> Dict[str, Any]:
        limit = _safe_int(runtime.get("max_examples_per_dataset"), 5000)
        beta = self._fetch_beta_leads(limit=max(120, min(2500, limit)))
        records: List[Dict[str, Any]] = []
        for row in beta:
            city = _safe_text(row.get("city") or "Los Angeles")
            vt = _safe_text(row.get("venue_type") or "event_venue")
            records.append(
                {
                    "messages": [
                        {"role": "system", "content": "Act as an LA/SFV market mapper. Rank neighborhood opportunity and give tactical recommendations."},
                        {"role": "user", "content": json.dumps({"city": city, "venue_type": vt, "score": row.get("score")}, ensure_ascii=False)},
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "market_opportunity_score": min(95, max(40, int(_safe_float(row.get("score"), 60)))),
                                    "segment": "premium" if city in {"Malibu", "Calabasas", "Hidden Hills", "Pasadena"} else "core",
                                    "recommendations": [
                                        "Prioritize outdoor/tented event operators",
                                        "Lead with backup-vendor reliability angle",
                                        "Use city-specific proof snippets in outreach",
                                    ],
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ]
                }
            )
            if len(records) >= limit:
                break
        if not records:
            records = self._seed_la_market_examples()
        self._write_jsonl(path, records[:limit])
        return {"path": str(path), "examples": min(len(records), limit)}

    def _build_conversion_dataset(self, path: Path, runtime: Dict[str, Any]) -> Dict[str, Any]:
        limit = max(2000, _safe_int(runtime.get("max_examples_per_dataset"), 5000))
        event_types = ["wedding", "quinceanera", "corporate", "backyard_party"]
        tiers = [1, 2, 3]
        guest_counts = [50, 80, 120, 180, 250, 350]
        rows: List[Dict[str, Any]] = []
        for i in range(limit):
            event_type = event_types[i % len(event_types)]
            tier = tiers[(i // 2) % len(tiers)]
            guests = guest_counts[(i // 3) % len(guest_counts)]
            quote = 999 if tier == 1 else (1299 if tier == 2 else 1599)
            rows.append(
                {
                    "messages": [
                        {"role": "system", "content": "You are conversion_specialist for Zoar Bathroom Rentals. Optimize quote->deposit->booking conversion."},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "event_type": event_type,
                                    "guest_count": guests,
                                    "distance_tier": tier,
                                    "quote_price": quote,
                                    "goal": "book in <=48h",
                                },
                                ensure_ascii=False,
                            ),
                        },
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "initial_message": f"For your {event_type}, we can start at ${quote}. A $160 deposit secures your date today.",
                                    "follow_up_24h": "Quick check-in: I can still hold that date if you want me to lock it now.",
                                    "follow_up_48h": "Final nudge before this weekend fills up — want me to reserve your date with the $160 deposit?",
                                    "conversion_notes": "Lead with clarity + date security, then reduce decision friction.",
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ]
                }
            )
        self._write_jsonl(path, rows[:limit])
        return {"path": str(path), "examples": min(len(rows), limit)}

    def _build_objection_resolver_dataset(self, path: Path, runtime: Dict[str, Any]) -> Dict[str, Any]:
        limit = max(1200, _safe_int(runtime.get("max_examples_per_dataset"), 5000))
        objections = ["too_expensive", "let_me_think", "check_with_partner", "found_cheaper", "not_ready"]
        event_types = ["wedding", "quinceanera", "corporate", "backyard_party"]
        lead_temps = ["hot", "warm", "cold"]
        rows: List[Dict[str, Any]] = []
        for i in range(limit):
            objection = objections[i % len(objections)]
            event_type = event_types[(i // 2) % len(event_types)]
            temp = lead_temps[(i // 3) % len(lead_temps)]
            guest_count = [70, 110, 180, 260][(i // 4) % 4]
            variations = [
                "I hear you — if budget is the concern, we can hold your date with a $160 deposit while you finalize details.",
                "Totally fair. We’re usually chosen for reliability and guest comfort, especially for outdoor events.",
                "If it helps, I can send a shorter quote option so you can compare clearly.",
                "I can reserve your date first, and we can confirm final logistics after.",
                "Want me to send two options so you can pick what fits best?",
            ]
            rows.append(
                {
                    "messages": [
                        {"role": "system", "content": "You are objection_resolver. Return objection-handling replies tailored by event type and lead temperature."},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "objection": objection,
                                    "event_type": event_type,
                                    "guest_count": guest_count,
                                    "lead_temperature": temp,
                                },
                                ensure_ascii=False,
                            ),
                        },
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "strategy": "value_reframe" if objection == "too_expensive" else "friction_reduction",
                                    "responses": variations,
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ]
                }
            )
        self._write_jsonl(path, rows[:limit])
        return {"path": str(path), "examples": min(len(rows), limit)}

    def _build_fb_media_buyer_dataset(self, path: Path, runtime: Dict[str, Any]) -> Dict[str, Any]:
        # Reuse the ads analyst builder and enforce historical CPL context.
        result = self._build_meta_ads_dataset(path, runtime)
        return result

    def _build_venue_partnership_closer_dataset(self, path: Path, runtime: Dict[str, Any]) -> Dict[str, Any]:
        limit = max(600, _safe_int(runtime.get("max_examples_per_dataset"), 5000))
        leads = self._fetch_beta_leads(limit=max(200, min(2500, limit)))
        rows: List[Dict[str, Any]] = []
        for i, row in enumerate(leads):
            tier = 1 if (i % 2 == 0) else 2
            payout = 200 if tier == 1 else 300
            rows.append(
                {
                    "messages": [
                        {"role": "system", "content": "You are venue_partnership_closer. Close venue coordinator partnerships with preferred-vendor or backup-vendor language."},
                        {"role": "user", "content": json.dumps(row, ensure_ascii=False)},
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "tier": tier,
                                    "offer": f"${payout} kickback per closed booking",
                                    "opening": "We can support your team as a dependable backup or preferred restroom trailer partner.",
                                    "close": "If you want, I can send a one-page partner setup with response SLA and booking process.",
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ]
                }
            )
            if len(rows) >= limit:
                break
        if not rows:
            rows = self._seed_venue_examples()
        self._write_jsonl(path, rows[:limit])
        return {"path": str(path), "examples": min(len(rows), limit)}

    def _build_follow_up_cadence_dataset(self, path: Path, runtime: Dict[str, Any]) -> Dict[str, Any]:
        limit = max(700, _safe_int(runtime.get("max_examples_per_dataset"), 5000))
        events = ["wedding", "quinceanera", "corporate", "backyard_party"]
        seasons = ["spring", "summer", "fall", "winter"]
        rows: List[Dict[str, Any]] = []
        for i in range(limit):
            event_type = events[i % len(events)]
            season = seasons[(i // 2) % len(seasons)]
            rows.append(
                {
                    "messages": [
                        {"role": "system", "content": "You are follow_up_cadence_specialist. Build non-pushy, high-conversion follow-up cadence."},
                        {
                            "role": "user",
                            "content": json.dumps({"event_type": event_type, "season": season, "timeline": "5m -> 24h -> 48h"}, ensure_ascii=False),
                        },
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "step_1_5min": "Quick response with quote range and date check CTA.",
                                    "step_2_24h": "Polite follow-up with value reminder and availability check.",
                                    "step_3_48h": "Final nudge with optional hold/deposit path; then back off.",
                                    "urgency_policy": "Apply urgency only when date pressure is credible.",
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ]
                }
            )
        self._write_jsonl(path, rows[:limit])
        return {"path": str(path), "examples": min(len(rows), limit)}

    def _build_quote_personalizer_dataset(self, path: Path, runtime: Dict[str, Any]) -> Dict[str, Any]:
        limit = max(1000, _safe_int(runtime.get("max_examples_per_dataset"), 5000))
        event_types = ["wedding", "quinceanera", "corporate", "backyard_party"]
        venue_types = ["estate_venue", "ranch_venue", "event_hall", "private_home"]
        rows: List[Dict[str, Any]] = []
        for i in range(limit):
            event_type = event_types[i % len(event_types)]
            venue_type = venue_types[(i // 2) % len(venue_types)]
            guests = [60, 100, 180, 260][(i // 3) % 4]
            distance_tier = 1 + ((i // 4) % 3)
            rows.append(
                {
                    "messages": [
                        {"role": "system", "content": "You are quote_personalizer. Personalize feature emphasis by event profile."},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "event_type": event_type,
                                    "guest_count": guests,
                                    "venue_type": venue_type,
                                    "distance_tier": distance_tier,
                                },
                                ensure_ascii=False,
                            ),
                        },
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "feature_emphasis": [
                                        "4 stalls for throughput",
                                        "climate control + running water",
                                        "self-contained setup for outdoor reliability",
                                    ],
                                    "quote_style": "direct and concise with clear next step",
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ]
                }
            )
        self._write_jsonl(path, rows[:limit])
        return {"path": str(path), "examples": min(len(rows), limit)}

    def _build_ad_creative_specialist_dataset(self, path: Path, runtime: Dict[str, Any]) -> Dict[str, Any]:
        limit = max(600, _safe_int(runtime.get("max_examples_per_dataset"), 5000))
        audiences = ["wedding", "quinceanera", "corporate", "backyard"]
        angles = ["luxury_vs_porta", "climate_control", "multi_stall", "convenience"]
        rows: List[Dict[str, Any]] = []
        for i in range(limit):
            audience = audiences[i % len(audiences)]
            angle = angles[(i // 2) % len(angles)]
            rows.append(
                {
                    "messages": [
                        {"role": "system", "content": "You are ad_creative_specialist. Generate high-CTR local service ad copy within policy constraints."},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "audience": audience,
                                    "angle": angle,
                                    "constraints": ["use 'starting at $999'", "never say 'all-inclusive'", "never say 'no hidden fees'"],
                                },
                                ensure_ascii=False,
                            ),
                        },
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "headline": "Luxury Restroom Trailer — Starting at $999",
                                    "primary_text": f"{audience.title()} event coming up? Give guests a clean, climate-controlled restroom experience.",
                                    "cta": "Check Date Availability",
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ]
                }
            )
        self._write_jsonl(path, rows[:limit])
        return {"path": str(path), "examples": min(len(rows), limit)}

    def _build_vendor_referral_specialist_dataset(self, path: Path, runtime: Dict[str, Any]) -> Dict[str, Any]:
        limit = max(700, _safe_int(runtime.get("max_examples_per_dataset"), 5000))
        vendor_types = ["wedding_photographer", "caterer", "event_planner", "dj"]
        rows: List[Dict[str, Any]] = []
        for i in range(limit):
            vendor_type = vendor_types[i % len(vendor_types)]
            rows.append(
                {
                    "messages": [
                        {"role": "system", "content": "You are vendor_referral_specialist. Craft lateral B2B outreach without dollar amounts."},
                        {"role": "user", "content": json.dumps({"vendor_type": vendor_type, "objective": "referral partnership"}, ensure_ascii=False)},
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "subject": "Preferred partner idea for your event clients",
                                    "opening": "We support outdoor events with a luxury 4-stall restroom trailer and can offer preferred rates for partners.",
                                    "notes": "No explicit payout amounts in first touch.",
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ]
                }
            )
        self._write_jsonl(path, rows[:limit])
        return {"path": str(path), "examples": min(len(rows), limit)}

    def _build_seasonal_demand_predictor_dataset(self, path: Path, runtime: Dict[str, Any]) -> Dict[str, Any]:
        limit = max(300, _safe_int(runtime.get("max_examples_per_dataset"), 5000))
        months = [
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
        ]
        event_mix = [
            "wedding",
            "quinceanera",
            "corporate",
            "backyard_party",
            "festival",
            "film_production",
        ]
        rows: List[Dict[str, Any]] = []
        for i in range(limit):
            month = months[i % len(months)]
            dominant = event_mix[(i // 2) % len(event_mix)]
            weekly_budget = 100
            recommendation = {
                "budget_distribution": "front_load" if month in {"march", "april", "may", "june", "september", "october"} else "even_spread",
                "priority_segments": ["wedding", "quinceanera"] if month in {"march", "april", "may", "june"} else ["corporate", "backyard_party"],
                "risk_flags": ["cpl_above_8_pause_and_refresh_creative", "keep_weekly_cap_100"],
                "advice": "Shift spend to highest-intent segment while preserving backup test budget for new hooks.",
            }
            rows.append(
                {
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are seasonal_demand_predictor for Zoar Bathroom Rentals. "
                                "Forecast SFV/LA demand and recommend budget allocation under a strict $100/week cap."
                            ),
                        },
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "market": "LA/SFV",
                                    "month": month,
                                    "dominant_event_type": dominant,
                                    "weekly_budget_cap": weekly_budget,
                                },
                                ensure_ascii=False,
                            ),
                        },
                        {"role": "assistant", "content": json.dumps(recommendation, ensure_ascii=False)},
                    ]
                }
            )
        self._write_jsonl(path, rows[:limit])
        return {"path": str(path), "examples": min(len(rows), limit)}

    def _build_autonomous_entrepreneur_dataset(self, path: Path, runtime: Dict[str, Any]) -> Dict[str, Any]:
        limit = max(2000, _safe_int(runtime.get("max_examples_per_dataset"), 5000))
        platforms = ["youtube", "etsy", "affiliate_blog", "newsletter", "marketplace", "reddit_content"]
        scenarios = [
            "low_ctr", "post_underperforming", "account_warmup", "policy_warning",
            "price_objection", "ranking_stuck", "traffic_up_low_conversion",
            "new_offer_launch", "retention_drop", "manual_approval_required",
        ]
        rows: List[Dict[str, Any]] = []
        for i in range(limit):
            platform = platforms[i % len(platforms)]
            scenario = scenarios[(i // 2) % len(scenarios)]
            revenue_goal = 500 if platform in {"youtube", "affiliate_blog"} else 300
            rows.append(
                {
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are autonomous_entrepreneur for Nexus. "
                                "Use safe, compliant, non-spam growth tactics with human-approval gates for risky actions."
                            ),
                        },
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "platform": platform,
                                    "scenario": scenario,
                                    "goal": "grow revenue with durable compounding",
                                    "weekly_target_usd": revenue_goal,
                                    "constraints": [
                                        "no stealth evasion",
                                        "no policy bypass",
                                        "no unsolicited outbound messaging",
                                        "approval required for publishing/spend/account changes",
                                    ],
                                },
                                ensure_ascii=False,
                            ),
                        },
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "reflection": "Diagnose KPI bottleneck before creating new output volume.",
                                    "decision": "Run one measurable experiment with stop-loss and success threshold.",
                                    "action": {
                                        "name": "optimize_existing_asset",
                                        "steps": [
                                            "Audit top 3 bottlenecks",
                                            "Apply one controlled change",
                                            "Measure 24h and 72h impact",
                                        ],
                                    },
                                    "memory_update": "Store outcome + avoid repeating failed tactic.",
                                    "safety": {"requires_approval": True, "risk_level": "low"},
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ]
                }
            )
        self._write_jsonl(path, rows[:limit])
        return {"path": str(path), "examples": min(len(rows), limit)}

    # ── Memory / eval artifacts ──────────────────────────────────────────

    def _write_semantic_memory(self, path: Path) -> int:
        facts = [
            {"type": "business_fact", "key": "service", "value": "Luxury 4-stall restroom trailer rental"},
            {"type": "business_fact", "key": "base_pricing", "value": "Starting around $999-$1000, minimum booking usually $1000+"},
            {"type": "business_fact", "key": "referral_payout", "value": "$200 per closed referral booking"},
            {"type": "service_area", "key": "priority_geo", "value": "San Fernando Valley, Burbank, Glendale, Calabasas, Hidden Hills, Malibu-adjacent"},
            {"type": "operational", "key": "vehicle_setup", "value": "Self-contained trailer with onboard tanks and generator support"},
        ]
        self._write_jsonl(path, facts)
        return len(facts)

    def _write_episodic_memory(self, path: Path) -> int:
        events = self._fetch_timeline_events(limit=500)
        rows = [{"type": "event", "payload": e} for e in events]
        if not rows:
            rows = [{"type": "event", "payload": {"note": "No historical events found in source DB yet"}}]
        self._write_jsonl(path, rows)
        return len(rows)

    def _write_strategic_memory(self, path: Path) -> int:
        lessons = [
            {
                "type": "strategic_lesson",
                "lesson": "When contact name confidence is low, generic greeting performs safer than guessed first-name greeting.",
                "confidence": 0.82,
            },
            {
                "type": "strategic_lesson",
                "lesson": "Backup-vendor framing is lower-friction than primary-vendor ask for many established venues.",
                "confidence": 0.78,
            },
            {
                "type": "strategic_lesson",
                "lesson": "Fast multi-channel follow-up is necessary but must stay approval-gated for safety.",
                "confidence": 0.86,
            },
        ]
        self._write_jsonl(path, lessons)
        return len(lessons)

    def _write_procedural_memory(self, path: Path) -> int:
        playbooks = [
            {
                "type": "playbook",
                "name": "facebook_form_lead_intake",
                "steps": [
                    "Ingest lead fields (name, phone, email, source timestamp).",
                    "Normalize phone/email and dedupe against existing contacts.",
                    "Create/merge lead profile and mark source as facebook_lead_form.",
                    "Queue call + SMS + email follow-up tasks (approval-gated where required).",
                    "Track outcome labels for feedback loop.",
                ],
            },
            {
                "type": "playbook",
                "name": "venue_partner_outreach",
                "steps": [
                    "Score venue fit with partner rubric.",
                    "Select first pitch angle: preferred_vendor / backup_vendor / referral_partner.",
                    "Generate outreach draft and subject variants.",
                    "Queue for morning batch review.",
                    "Log reply outcome and lesson.",
                ],
            },
        ]
        self._write_jsonl(path, playbooks)
        return len(playbooks)

    def _write_eval_rubrics(self, root: Path) -> List[str]:
        rubrics = {
            "lead_quality_rubric.json": {
                "metric": "lead_quality_score",
                "weights": {"contactability": 30, "intent": 30, "geo_fit": 20, "commercial_fit": 20},
                "fail_conditions": ["no_contact_path", "invalid_phone_and_email"],
            },
            "creative_quality_rubric.json": {
                "metric": "creative_quality_score",
                "weights": {"hook": 25, "clarity": 20, "product_relevance": 25, "cta": 20, "platform_fit": 10},
                "fail_conditions": ["wrong_product_visual", "illegible_text", "audio_distortion"],
            },
            "outreach_quality_rubric.json": {
                "metric": "outreach_angle_quality",
                "weights": {"angle_fit": 35, "personalization": 20, "clarity": 20, "trust_signal": 15, "tone": 10},
                "fail_conditions": ["hallucinated_name", "spammy_language"],
            },
            "code_patch_quality_rubric.json": {
                "metric": "code_patch_quality_score",
                "weights": {"correctness": 40, "safety": 25, "regression_risk": 20, "test_coverage": 15},
                "fail_conditions": ["security_regression", "breaking_change_without_migration"],
            },
            "research_plan_quality_rubric.json": {
                "metric": "research_plan_quality_score",
                "weights": {"market_specificity": 35, "source_quality": 25, "actionability": 25, "evidence_structure": 15},
                "fail_conditions": ["no_local_market_focus", "uncited_claims"],
            },
            "meta_ads_diagnostic_rubric.json": {
                "metric": "meta_ads_diagnostic_score",
                "weights": {"metric_reasoning": 30, "hook_diagnosis": 25, "test_design": 25, "expected_impact_logic": 20},
                "fail_conditions": ["ignores_core_metrics", "untestable_recommendations"],
            },
            "website_cro_rubric.json": {
                "metric": "website_cro_score",
                "weights": {"cta_strength": 30, "trust_proof": 25, "clarity": 25, "friction_reduction": 20},
                "fail_conditions": ["no_conversion_path", "non_actionable_feedback"],
            },
            "media_buyer_strategy_rubric.json": {
                "metric": "media_buyer_strategy_score",
                "weights": {"budget_logic": 30, "testing_rigor": 30, "risk_controls": 20, "scalability": 20},
                "fail_conditions": ["no_guardrails", "single-point_strategy"],
            },
            "funnel_diagnostic_rubric.json": {
                "metric": "funnel_diagnostic_score",
                "weights": {"dropoff_detection": 30, "root_cause_quality": 25, "experiment_quality": 25, "measurement": 20},
                "fail_conditions": ["no_dropoff_stage", "no_measurable_experiment"],
            },
            "la_market_mapper_rubric.json": {
                "metric": "la_market_mapper_score",
                "weights": {"geo_specificity": 35, "segment_fit": 25, "actionability": 25, "evidence_alignment": 15},
                "fail_conditions": ["generic_non_local_mapping", "missing_city_prioritization"],
            },
            "conversion_specialist_rubric.json": {
                "metric": "conversion_specialist_score",
                "weights": {"deposit_anchor": 25, "timing_logic": 25, "message_clarity": 25, "conversion_likelihood": 25},
                "fail_conditions": ["missing_deposit_anchor", "missing_48h_logic"],
            },
            "objection_resolver_rubric.json": {
                "metric": "objection_resolver_score",
                "weights": {"objection_fit": 30, "variation_depth": 25, "tone_control": 20, "conversion_recovery": 25},
                "fail_conditions": ["single_response_only", "generic_non_contextual_response"],
            },
            "fb_media_buyer_rubric.json": {
                "metric": "fb_media_buyer_score",
                "weights": {"budget_logic": 25, "segmentation": 25, "creative_testing": 25, "cpl_goal_alignment": 25},
                "fail_conditions": ["ignores_budget_cap", "no_cpl_strategy"],
            },
            "venue_partnership_closer_rubric.json": {
                "metric": "venue_partnership_closer_score",
                "weights": {"offer_clarity": 25, "venue_fit": 25, "close_strength": 25, "relationship_framing": 25},
                "fail_conditions": ["no_close", "unclear_offer_structure"],
            },
            "follow_up_cadence_specialist_rubric.json": {
                "metric": "follow_up_cadence_specialist_score",
                "weights": {"cadence_timing": 35, "non_pushy_tone": 20, "urgency_control": 25, "conversion_pull": 20},
                "fail_conditions": ["missing_24h_or_48h_step", "overly_pushy_language"],
            },
            "quote_personalizer_rubric.json": {
                "metric": "quote_personalizer_score",
                "weights": {"event_fit": 30, "feature_matching": 30, "contextualization": 20, "clarity": 20},
                "fail_conditions": ["generic_same_quote", "no_feature_adaptation"],
            },
            "ad_creative_specialist_rubric.json": {
                "metric": "ad_creative_specialist_score",
                "weights": {"hook_strength": 30, "policy_compliance": 25, "cta_strength": 25, "audience_fit": 20},
                "fail_conditions": ["policy_violations", "missing_cta"],
            },
            "vendor_referral_specialist_rubric.json": {
                "metric": "vendor_referral_specialist_score",
                "weights": {"partner_fit": 30, "tone_quality": 25, "clarity": 25, "compliance": 20},
                "fail_conditions": ["mentions_dollar_amount", "spammy_copy"],
            },
            "seasonal_demand_predictor_rubric.json": {
                "metric": "seasonal_demand_predictor_score",
                "weights": {"seasonality_fit": 30, "budget_guardrails": 30, "segment_prioritization": 20, "actionability": 20},
                "fail_conditions": ["ignores_weekly_budget_cap", "non_local_generic_forecast"],
            },
            "autonomous_entrepreneur_rubric.json": {
                "metric": "autonomous_entrepreneur_score",
                "weights": {"decision_quality": 30, "roi_logic": 25, "memory_learning": 20, "safety_controls": 25},
                "fail_conditions": ["unsafe_automation", "no_experiment_metric", "policy_bypass_guidance"],
            },
        }
        files: List[str] = []
        for name, body in rubrics.items():
            p = root / name
            p.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
            files.append(str(p))
        return files

    def _write_eval_benchmark(self, path: Path) -> int:
        suite = [
            {
                "task": "lead_quality_scoring",
                "input": {"name": "Norberta Salazar", "phone": "+13107809619", "email": "rosario3844@outlook.com", "source": "facebook_lead_form"},
                "expected": {"min_score": 70, "must_include": ["contactability", "intent"]},
            },
            {
                "task": "creative_critic",
                "input": {"hook": "Luxury restroom trailer for outdoor weddings", "visual": "trailer_exterior", "cta": "Check date availability"},
                "expected": {"min_score": 75, "must_include": ["hook_strength", "cta"]},
            },
            {
                "task": "outreach_angle_selector",
                "input": {"venue_type": "wedding_venue", "vendor_list_signal": True, "contact_path": "events@venue.com"},
                "expected": {"angle_in": ["preferred_vendor", "backup_vendor"]},
            },
            {
                "task": "code_repair_critic",
                "input": {"file": "core/runpod_sprint.py", "issue": "silent failure on webhook retry"},
                "expected": {"must_include": ["minimal patch plan", "regression guard", "approval gate"]},
            },
            {
                "task": "research_planner",
                "input": {"market": "LA/SFV wedding venues", "goal": "increase bookings"},
                "expected": {"must_include": ["source-backed plan", "city segmentation", "testable hypotheses"]},
            },
            {"task": "conversion_specialist", "input": {"event_type": "wedding", "distance_tier": 2, "quote_price": 1299}, "expected": {"must_include": ["$160 deposit", "24h follow-up", "48h final nudge"]}},
            {"task": "objection_resolver", "input": {"objection": "too_expensive", "event_type": "quinceanera", "lead_temperature": "warm"}, "expected": {"must_include": ["5 response variants", "event-tailored language"]}},
            {"task": "fb_media_buyer", "input": {"cpl_history": [2.34, 4.93], "weekly_budget": 100, "market": "LA/SFV"}, "expected": {"must_include": ["segmentation plan", "test matrix", "budget allocation"]}},
            {"task": "venue_partnership_closer", "input": {"venue_type": "outdoor_wedding_venue", "tier": 2}, "expected": {"must_include": ["preferred/backup partner angle", "$200/$300 structure"]}},
            {"task": "follow_up_cadence_specialist", "input": {"timeline": "5m-24h-48h", "season": "summer"}, "expected": {"must_include": ["timing", "urgency rules", "backoff rule"]}},
            {"task": "quote_personalizer", "input": {"event_type": "backyard_party", "guest_count": 80, "venue_type": "private_home", "distance_tier": 1}, "expected": {"must_include": ["feature emphasis", "personalized framing"]}},
            {"task": "ad_creative_specialist", "input": {"audience": "wedding", "angle": "luxury_vs_porta"}, "expected": {"must_include": ["starting at $999", "policy-safe copy", "clear CTA"]}},
            {"task": "vendor_referral_specialist", "input": {"vendor_type": "event_planner"}, "expected": {"must_include": ["preferred rates language", "no dollar amount"]}},
            {"task": "seasonal_demand_predictor", "input": {"month": "october", "market": "LA/SFV", "weekly_budget_cap": 100}, "expected": {"must_include": ["seasonality", "budget split", "segment priority"]}},
            {"task": "autonomous_entrepreneur", "input": {"platform": "youtube", "scenario": "low_ctr", "goal": "increase qualified revenue"}, "expected": {"must_include": ["experiment", "safety gate", "memory update"]}},
        ]
        self._write_jsonl(path, suite)
        return len(suite)

    def _write_experiment_templates(self, path: Path) -> int:
        templates = [
            {
                "name": "backup_vendor_vs_referral_angle",
                "goal": "Increase positive replies from venue partners",
                "hypothesis": "Backup-vendor framing yields higher initial reply rate than referral-only framing.",
                "metric": "positive_reply_rate",
                "success_threshold": 0.15,
                "constraints": {"no_auto_send": True, "approval_required": True},
            },
            {
                "name": "creative_hook_test",
                "goal": "Increase qualified lead form submission quality",
                "hypothesis": "Hook focusing on guest comfort outperforms generic luxury claim.",
                "metric": "qualified_lead_rate",
                "success_threshold": 0.2,
                "constraints": {"budget_guardrail": "manual"},
            },
        ]
        path.write_text(json.dumps(templates, ensure_ascii=False, indent=2), encoding="utf-8")
        return len(templates)

    # ── Source adapters ──────────────────────────────────────────────────

    def _source_counts(self) -> Dict[str, int]:
        return {
            "leads": self._count_table_rows(NEXUS_DB_PATH, "leads"),
            "timeline_events": self._count_table_rows(NEXUS_DB_PATH, "lead_timeline"),
            "approvals": self._count_table_rows(NEXUS_DB_PATH, "message_approvals"),
            "beta_leads": self._count_table_rows(BETA_DB_PATH, "beta_leads"),
            "creative_feedback": self._count_creative_feedback(),
            "code_samples": self._count_code_samples(),
            "market_records": self._count_market_records(),
        }

    def _count_table_rows(self, db_path: Path, table: str) -> int:
        if not db_path.exists():
            return 0
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            if not self._table_exists(conn, table):
                return 0
            row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
            return int(row["c"] or 0) if row else 0
        except Exception:
            return 0
        finally:
            conn.close()

    def _fetch_lead_rows(self, limit: int) -> List[Dict[str, Any]]:
        if not NEXUS_DB_PATH.exists():
            return []
        conn = sqlite3.connect(NEXUS_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            table = "leads"
            if not self._table_exists(conn, table):
                return []
            cols = self._table_columns(conn, table)
            wanted = [c for c in ["id", "first_name", "last_name", "full_name", "phone", "email", "source", "booking_status", "status", "event_city", "event_type", "created_at"] if c in cols]
            if not wanted:
                return []
            rows = conn.execute(
                f"SELECT {', '.join(wanted)} FROM {table} ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit or 1000), 10000)),),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["full_name"] = d.get("full_name") or " ".join([_safe_text(d.get("first_name")), _safe_text(d.get("last_name"))]).strip()
                out.append(d)
            return out
        except Exception:
            return []
        finally:
            conn.close()

    def _fetch_beta_leads(self, limit: int) -> List[Dict[str, Any]]:
        if not BETA_DB_PATH.exists():
            return []
        conn = sqlite3.connect(BETA_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            table = "beta_leads"
            if not self._table_exists(conn, table):
                return []
            cols = self._table_columns(conn, table)
            wanted = [c for c in ["id", "venue_name", "website", "city", "region", "venue_type", "contact_name", "contact_email", "contact_phone", "score", "tier", "best_pitch_angle", "validation_status", "proof_snippet_1", "proof_snippet_2"] if c in cols]
            rows = conn.execute(
                f"SELECT {', '.join(wanted)} FROM {table} ORDER BY score DESC, id DESC LIMIT ?",
                (max(1, min(int(limit or 1000), 10000)),),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []
        finally:
            conn.close()

    def _fetch_timeline_events(self, limit: int) -> List[Dict[str, Any]]:
        if not NEXUS_DB_PATH.exists():
            return []
        conn = sqlite3.connect(NEXUS_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            table = "lead_timeline"
            if not self._table_exists(conn, table):
                return []
            cols = self._table_columns(conn, table)
            wanted = [c for c in ["id", "lead_id", "event_type", "details", "ts"] if c in cols]
            rows = conn.execute(
                f"SELECT {', '.join(wanted)} FROM {table} ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit or 1000), 10000)),),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []
        finally:
            conn.close()

    def _fetch_creative_feedback(self, limit: int) -> List[Dict[str, Any]]:
        # Pull from generated output manifests/scores when present.
        outputs_dir = Path.home() / "nexus" / "outputs"
        if not outputs_dir.exists():
            outputs_dir = Path.cwd() / "outputs"
        out: List[Dict[str, Any]] = []
        if not outputs_dir.exists():
            return out

        # Prefer explicit score JSON artifacts.
        for p in sorted(outputs_dir.rglob("video_scores.json"), reverse=True):
            data = _read_json(p, {})
            if isinstance(data, dict):
                for k, v in data.items():
                    score = _safe_float(v.get("score") if isinstance(v, dict) else v, 0.0)
                    out.append(
                        {
                            "artifact": str(p),
                            "creative_id": k,
                            "score": score,
                            "notes": _safe_text(v.get("notes") if isinstance(v, dict) else ""),
                        }
                    )
            elif isinstance(data, list):
                for v in data:
                    if not isinstance(v, dict):
                        continue
                    out.append(
                        {
                            "artifact": str(p),
                            "creative_id": _safe_text(v.get("id") or v.get("creative_id") or ""),
                            "score": _safe_float(v.get("score"), 0.0),
                            "notes": _safe_text(v.get("notes")),
                        }
                    )
            if len(out) >= limit:
                break
        return out[:limit]

    def _count_creative_feedback(self) -> int:
        return len(self._fetch_creative_feedback(limit=10000))

    def _count_code_samples(self) -> int:
        return len(self._collect_repo_python_files(limit=600))

    def _count_market_records(self) -> int:
        # Keep this lightweight: count beta leads and treat as market graph base rows.
        return self._count_table_rows(BETA_DB_PATH, "beta_leads")

    def _collect_repo_python_files(self, limit: int = 500) -> List[Path]:
        root = Path.cwd()
        candidates: List[Path] = []
        patterns = [
            "core/**/*.py",
            "integrations/**/*.py",
            "scripts/**/*.py",
            "telegram/**/*.py",
            "server.py",
        ]
        for pat in patterns:
            for p in root.glob(pat):
                if not p.is_file():
                    continue
                if "venv/" in str(p) or "/.git/" in str(p):
                    continue
                candidates.append(p)
                if len(candidates) >= limit:
                    return candidates
        return candidates

    # ── Heuristics / seeds ───────────────────────────────────────────────

    def _heuristic_lead_score(self, row: Dict[str, Any]) -> int:
        score = 35
        if _safe_text(row.get("phone")):
            score += 20
        if _safe_text(row.get("email")):
            score += 15
        src = _safe_text(row.get("source")).lower()
        if "facebook" in src:
            score += 10
        if "referral" in src:
            score += 12
        st = _safe_text(row.get("booking_status") or row.get("status")).lower()
        if st in {"won", "booked", "closed_won"}:
            score += 20
        elif st in {"replied", "qualified", "quote_sent"}:
            score += 10
        if _safe_text(row.get("event_city")):
            score += 5
        return max(0, min(score, 100))

    def _lead_reason(self, row: Dict[str, Any], score: int) -> str:
        reasons = []
        if _safe_text(row.get("phone")):
            reasons.append("phone_present")
        if _safe_text(row.get("email")):
            reasons.append("email_present")
        if _safe_text(row.get("source")):
            reasons.append(f"source:{_safe_text(row.get('source')).lower()}")
        if _safe_text(row.get("booking_status") or row.get("status")):
            reasons.append(f"stage:{_safe_text(row.get('booking_status') or row.get('status')).lower()}")
        if not reasons:
            reasons.append("sparse_record")
        return f"score={score}; " + ",".join(reasons)

    def _venue_reason(self, row: Dict[str, Any]) -> str:
        bits = []
        if _safe_text(row.get("tier")):
            bits.append(f"tier={_safe_text(row.get('tier'))}")
        if _safe_text(row.get("validation_status")):
            bits.append(f"validation={_safe_text(row.get('validation_status'))}")
        if _safe_text(row.get("contact_email")) or _safe_text(row.get("contact_phone")):
            bits.append("contactable")
        if _safe_text(row.get("proof_snippet_1")):
            bits.append("proof_present")
        return ", ".join(bits) if bits else "limited_evidence"

    def _creative_improvement_hints(self, row: Dict[str, Any], score: float) -> List[str]:
        if score >= 80:
            return ["Keep hook; test CTA variants.", "Preserve pacing and readability."]
        if score >= 60:
            return ["Strengthen first 2 seconds hook.", "Increase product clarity and local trust signal."]
        return ["Reject or major rewrite.", "Fix visual relevance, pacing, and CTA clarity."]

    def _subject_for_angle(self, angle: str) -> str:
        a = _safe_text(angle).lower()
        if a == "preferred_vendor":
            return "Preferred restroom partner for your events?"
        if a == "backup_vendor":
            return "Backup restroom coverage for booked-out dates"
        if a == "overflow_peak_date":
            return "Extra restroom capacity for peak event dates"
        return "Referral partner idea ($200 per booked event)"

    def _opening_for_lead(self, row: Dict[str, Any]) -> str:
        name = _safe_text(row.get("contact_name") or row.get("venue_name") or row.get("full_name") or "")
        if name and "@" not in name:
            return f"Hi {name.split()[0]}, quick local partner idea for your events."
        return "Hi there, quick local partner idea for your events."

    def _seed_lead_ranker_examples(self) -> List[Dict[str, Any]]:
        base = [
            {"full_name": "Kai Escobar", "phone": "8184489055", "email": "kaiescobar09@gmail.com", "source": "facebook_lead_form", "booking_status": "qualified"},
            {"full_name": "Unknown", "phone": "", "email": "", "source": "cold_email", "booking_status": "new"},
        ]
        out = []
        for row in base:
            score = self._heuristic_lead_score(row)
            out.append(
                {
                    "messages": [
                        {"role": "system", "content": "Score lead quality for Zoar Bathroom Rentals on 0-100 scale."},
                        {"role": "user", "content": json.dumps(row, ensure_ascii=False)},
                        {"role": "assistant", "content": json.dumps({"lead_quality_score": score, "tier": "A" if score >= 80 else "C", "reasoning": self._lead_reason(row, score)}, ensure_ascii=False)},
                    ]
                }
            )
        return out

    def _seed_venue_examples(self) -> List[Dict[str, Any]]:
        samples = [
            {"venue_name": "Calabasas Estate Venue", "city": "Calabasas", "score": 84, "tier": "A", "best_pitch_angle": "preferred_vendor"},
            {"venue_name": "Local Event Hall", "city": "Los Angeles", "score": 62, "tier": "C", "best_pitch_angle": "backup_vendor"},
        ]
        out = []
        for row in samples:
            out.append(
                {
                    "messages": [
                        {"role": "system", "content": "Rank venue partner fit for Zoar and choose first outreach angle."},
                        {"role": "user", "content": json.dumps(row, ensure_ascii=False)},
                        {"role": "assistant", "content": json.dumps({"venue_partner_score": row["score"], "tier": row["tier"], "recommended_angle": row["best_pitch_angle"], "rationale": "seed_example"}, ensure_ascii=False)},
                    ]
                }
            )
        return out

    def _seed_creative_examples(self) -> List[Dict[str, Any]]:
        return [
            {
                "messages": [
                    {"role": "system", "content": "Critique ad creative quality for Meta conversion performance."},
                    {"role": "user", "content": json.dumps({"creative_id": "seed_001", "score": 82, "notes": "clear trailer visuals + strong CTA"}, ensure_ascii=False)},
                    {"role": "assistant", "content": json.dumps({"creative_quality_score": 82, "grade": "pass", "improvements": ["Test alternate hook for weddings."]}, ensure_ascii=False)},
                ]
            },
            {
                "messages": [
                    {"role": "system", "content": "Critique ad creative quality for Meta conversion performance."},
                    {"role": "user", "content": json.dumps({"creative_id": "seed_002", "score": 48, "notes": "slideshow feel + weak pacing"}, ensure_ascii=False)},
                    {"role": "assistant", "content": json.dumps({"creative_quality_score": 48, "grade": "reject", "improvements": ["Replace slideshow with scene cuts.", "Improve hook in first 2 seconds."]}, ensure_ascii=False)},
                ]
            },
        ]

    def _seed_outreach_examples(self) -> List[Dict[str, Any]]:
        return [
            {
                "messages": [
                    {"role": "system", "content": "Select best outreach angle and first-line opener for Zoar."},
                    {"role": "user", "content": json.dumps({"venue_type": "wedding_venue", "vendor_list_signal": True, "contact_name": "Norberta"}, ensure_ascii=False)},
                    {"role": "assistant", "content": json.dumps({"best_pitch_angle": "preferred_vendor", "subject_line": "Preferred restroom partner for your events?", "opening_line": "Hi Norberta, quick local partner idea for your events."}, ensure_ascii=False)},
                ]
            },
            {
                "messages": [
                    {"role": "system", "content": "Select best outreach angle and first-line opener for Zoar."},
                    {"role": "user", "content": json.dumps({"venue_type": "event_hall", "vendor_list_signal": False, "contact_name": ""}, ensure_ascii=False)},
                    {"role": "assistant", "content": json.dumps({"best_pitch_angle": "backup_vendor", "subject_line": "Backup restroom coverage for booked-out dates", "opening_line": "Hi there, quick local partner idea for your events."}, ensure_ascii=False)},
                ]
            },
        ]

    def _seed_ops_examples(self) -> List[Dict[str, Any]]:
        return [
            {
                "messages": [
                    {"role": "system", "content": "Diagnose ops issue and propose safe next action."},
                    {"role": "user", "content": json.dumps({"event_type": "no_reply_after_first_contact", "details": "Lead has phone+email but no response in 48h"}, ensure_ascii=False)},
                    {"role": "assistant", "content": json.dumps({"diagnosis": "Follow-up timing/angle mismatch", "next_action": "A/B test backup-vendor angle vs referral angle", "risk": "low", "requires_approval": False}, ensure_ascii=False)},
                ]
            }
        ]

    def _seed_code_repair_examples(self) -> List[Dict[str, Any]]:
        return [
            {
                "messages": [
                    {"role": "system", "content": "Review code for reliability bugs and propose safe, minimal patches with tests."},
                    {"role": "user", "content": json.dumps({"file": "core/runpod_sprint.py", "snippet": "try: ... except Exception: pass"}, ensure_ascii=False)},
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "risk_level": "high",
                                "issues": ["Silent failure masks runtime outage"],
                                "patch_plan": [
                                    "Add structured error logging with context",
                                    "Add retry cap + failure status flag",
                                    "Add unit test for error path",
                                ],
                                "requires_approval": True,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ]
            }
        ]

    def _seed_research_examples(self) -> List[Dict[str, Any]]:
        return [
            {
                "messages": [
                    {"role": "system", "content": "Create a source-grounded deep research plan for LA/SFV events market opportunities."},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "market": "San Fernando Valley wedding venues",
                                "goal": "increase qualified bookings",
                            },
                            ensure_ascii=False,
                        ),
                    },
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "research_plan": [
                                    "Identify venues with outdoor/tented event indicators",
                                    "Capture vendor-list and referral policy evidence",
                                    "Rank by conversion fit and contactability",
                                ],
                                "deliverables": ["evidence_log", "top_targets_csv", "angle_test_matrix"],
                                "safety": {"no_outbound": True, "approval_required_for_actions": True},
                            },
                            ensure_ascii=False,
                        ),
                    },
                ]
            }
        ]

    def _seed_meta_ads_examples(self) -> List[Dict[str, Any]]:
        return [
            {
                "messages": [
                    {"role": "system", "content": "Act as a senior Meta ads analyst. Diagnose performance and propose high-ROI next tests."},
                    {"role": "user", "content": json.dumps({"ctr": 1.2, "cpl": 16.4, "hook": "generic_luxury"}, ensure_ascii=False)},
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "diagnosis": "Hook is generic; intent quality likely weak.",
                                "priority_changes": ["Test guest-comfort and date-urgency hooks", "Split creative by event context"],
                                "expected_impact": {"cpl_delta_pct": -10, "qualified_lead_delta_pct": 12},
                            },
                            ensure_ascii=False,
                        ),
                    },
                ]
            }
        ]

    def _seed_website_cro_examples(self) -> List[Dict[str, Any]]:
        return [
            {
                "messages": [
                    {"role": "system", "content": "Act as a senior CRO reviewer for event-service websites."},
                    {"role": "user", "content": json.dumps({"website": "https://example.com", "city": "Burbank"}, ensure_ascii=False)},
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "cro_score": 64,
                                "issues": ["Weak CTA hierarchy", "Low trust proof"],
                                "fixes": ["Add instant quote/date CTA", "Add local proof and booking photos"],
                            },
                            ensure_ascii=False,
                        ),
                    },
                ]
            }
        ]

    def _seed_media_buyer_examples(self) -> List[Dict[str, Any]]:
        return [
            {
                "messages": [
                    {"role": "system", "content": "Act as a senior media buyer. Build budget and test allocation for bookings growth."},
                    {"role": "user", "content": json.dumps({"city": "Calabasas", "venue_type": "wedding_venue"}, ensure_ascii=False)},
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "budget_split": {"prospecting": 0.6, "retargeting": 0.25, "creative_testing": 0.15},
                                "test_plan": ["Run 3-hook matrix", "Kill low-quality CPL variants within 48h"],
                                "guardrails": ["Protect CPL ceiling", "Optimize for booked-event probability, not just lead volume"],
                            },
                            ensure_ascii=False,
                        ),
                    },
                ]
            }
        ]

    def _seed_funnel_examples(self) -> List[Dict[str, Any]]:
        return [
            {
                "messages": [
                    {"role": "system", "content": "Act as a senior funnel diagnostic expert. Find drop-off causes and remediation experiments."},
                    {"role": "user", "content": json.dumps({"event_type": "lead_no_reply_48h", "details": "Phone+email present"}, ensure_ascii=False)},
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "dropoff_stage": "first_followup",
                                "root_cause_hypotheses": ["Weak urgency framing", "Missing trust proof"],
                                "next_experiment": {"name": "trust_proof_first_touch", "metric": "reply_rate", "window_hours": 48},
                            },
                            ensure_ascii=False,
                        ),
                    },
                ]
            }
        ]

    def _seed_la_market_examples(self) -> List[Dict[str, Any]]:
        return [
            {
                "messages": [
                    {"role": "system", "content": "Act as an LA/SFV market mapper. Rank neighborhood opportunity and give tactical recommendations."},
                    {"role": "user", "content": json.dumps({"city": "Malibu", "venue_type": "estate_venue", "score": 88}, ensure_ascii=False)},
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "market_opportunity_score": 90,
                                "segment": "premium",
                                "recommendations": ["Prioritize backup-vendor + premium reliability pitch", "Use high-end event proof and fast response SLA"],
                            },
                            ensure_ascii=False,
                        ),
                    },
                ]
            }
        ]

    # ── Output helpers ───────────────────────────────────────────────────

    def _write_jsonl(self, path: Path, rows: Sequence[Dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _artifact_layout_hint(self) -> Dict[str, Any]:
        return {
            "runpod_lab": {
                "training_artifacts": [
                    "datasets/*.jsonl",
                    "labels/*.json",
                    "memory/*.jsonl",
                    "market_intel/*",
                    "eval/*.json",
                    "training_configs/*.json",
                ],
                "exports": ["export_manifest_template.json", "<specialist>/adapter_model.safetensors (after training)"],
                "integration_hooks": ["integration_hooks_manifest.json", "README.md"],
            }
        }

    def _runtime(self, overrides: Dict[str, Any]) -> Dict[str, Any]:
        rt = dict(DEFAULT_CFG)
        for k, v in (overrides or {}).items():
            if k in {"base_models", "lora", "h100_profile", "swarm", "training"} and isinstance(v, dict):
                merged = dict(rt.get(k, {}))
                merged.update(v)
                rt[k] = merged
            else:
                rt[k] = v
        rt["max_examples_per_dataset"] = max(100, min(_safe_int(rt.get("max_examples_per_dataset"), 5000), 30000))
        rt["feedback_data_path"] = _safe_text(rt.get("feedback_data_path") or "")
        rt["feedback_weight"] = max(0.0, min(_safe_float(rt.get("feedback_weight"), 0.30), 0.9))
        if not isinstance(rt.get("target_specialists"), list):
            rt["target_specialists"] = list(DEFAULT_CFG["target_specialists"])
        swarm = rt.get("swarm") if isinstance(rt.get("swarm"), dict) else {}
        swarm["max_cpu_agents"] = max(1, min(_safe_int(swarm.get("max_cpu_agents"), 50), 500))
        swarm["cpu_agent_workers"] = max(1, min(_safe_int(swarm.get("cpu_agent_workers"), 4), 32))
        gckpt = bool((rt.get("h100_profile") or {}).get("gradient_checkpointing", True))
        max_train_cap = 3 if gckpt else 1
        swarm["max_concurrent_training"] = max(1, min(_safe_int(swarm.get("max_concurrent_training"), max_train_cap), max_train_cap))
        swarm["gpu_train_workers"] = max(1, min(_safe_int(swarm.get("gpu_train_workers"), 1), swarm["max_concurrent_training"]))
        swarm["logical_micro_agents"] = max(1, min(_safe_int(swarm.get("logical_micro_agents"), 256), 10000))
        swarm["max_logical_micro_agents"] = max(64, min(_safe_int(swarm.get("max_logical_micro_agents"), 4096), 20000))
        if not isinstance(swarm.get("cpu_agents"), list) or not swarm.get("cpu_agents"):
            swarm["cpu_agents"] = list((DEFAULT_CFG.get("swarm") or {}).get("cpu_agents", []))
        rt["swarm"] = swarm
        training = rt.get("training") if isinstance(rt.get("training"), dict) else {}
        training["min_examples_to_train"] = max(1, min(_safe_int(training.get("min_examples_to_train"), 32), 5000))
        if not isinstance(training.get("curriculum_order"), list):
            training["curriculum_order"] = list((DEFAULT_CFG.get("training") or {}).get("curriculum_order", []))
        rt["training"] = training
        return rt

    def _write_report(
        self,
        run_id: int,
        runtime: Dict[str, Any],
        lab_dir: Path,
        datasets: Dict[str, Any],
        labels: Dict[str, Any],
        memory_eval: Dict[str, Any],
        training: Dict[str, Any],
        export_contract: Dict[str, Any],
    ) -> Path:
        report = lab_dir / "RUNPOD_BRAIN_LAB_REPORT.md"
        report.write_text(
            (
                "# Runpod Brain Lab Report\n\n"
                f"- Run ID: {run_id}\n"
                f"- Created: {_now()}\n"
                f"- Lab Dir: `{lab_dir}`\n\n"
                "## Separation Guarantees\n"
                "1. Runpod training artifacts: `training_artifacts/`\n"
                "2. Exported adapters metadata: `exports/`\n"
                "3. Nexus integration hooks contract: `integration_hooks/`\n\n"
                "## Runtime Profile\n"
                f"```json\n{json.dumps(runtime, ensure_ascii=False, indent=2)}\n```\n\n"
                "## Artifacts\n"
                f"- Datasets manifest: `{datasets['path']}`\n"
                f"- Labeling manifest: `{labels['path']}`\n"
                f"- Memory/Eval manifest: `{memory_eval['path']}`\n"
                f"- Training manifest: `{training['path']}`\n"
                f"- Market intel manifest: `{lab_dir / 'training_artifacts' / 'market_intel_manifest.json'}`\n"
                f"- Export contract: `{export_contract['export_manifest']}`\n"
                f"- Integration hooks: `{export_contract['integration_hooks_manifest']}`\n\n"
                "## Next Step (Runpod)\n"
                "1. Upload `runpod_lab/` to H100 NVL pod.\n"
                "2. Run `training_configs/run_agent_swarm.sh` for supervisor+sub-agent pipeline.\n"
                "3. Produce adapter exports in `exports/<specialist>/`.\n"
                "4. Attach benchmark/eval reports before promotion.\n"
            ),
            encoding="utf-8",
        )
        self._artifact(run_id, "runpod_lab_report_md", report, {})
        return report

    # ── DB helpers ───────────────────────────────────────────────────────

    def _create_run(self, runtime: Dict[str, Any]) -> int:
        conn = self._conn()
        try:
            cur = conn.execute(
                """
                INSERT INTO runpod_brain_runs (status, phase, runtime_json, outputs_json, started_at)
                VALUES ('running', 'plan', ?, '{}', ?)
                """,
                (_to_json(runtime), _now()),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def _set_phase(self, run_id: int, phase: str) -> None:
        conn = self._conn()
        try:
            conn.execute("UPDATE runpod_brain_runs SET phase=? WHERE id=?", (_safe_text(phase), int(run_id)))
            conn.commit()
        finally:
            conn.close()

    def _phase(self, run_id: int) -> str:
        conn = self._conn()
        try:
            row = conn.execute("SELECT phase FROM runpod_brain_runs WHERE id=?", (int(run_id),)).fetchone()
            return _safe_text(row["phase"] if row else "")
        finally:
            conn.close()

    def _finish_run(self, run_id: int, status: str, outputs: Dict[str, Any], error: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                UPDATE runpod_brain_runs
                SET status=?, outputs_json=?, error=?, finished_at=?
                WHERE id=?
                """,
                (_safe_text(status), _to_json(outputs), _safe_text(error), _now(), int(run_id)),
            )
            conn.commit()
        finally:
            conn.close()

    def _event(self, run_id: int, level: str, phase: str, message: str, payload: Optional[Dict[str, Any]] = None) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO runpod_brain_events
                (run_id, level, phase, message, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (int(run_id), _safe_text(level, "info"), _safe_text(phase), _safe_text(message), _to_json(payload or {}), _now()),
            )
            conn.commit()
        finally:
            conn.close()

    def _artifact(self, run_id: int, artifact_type: str, file_path: Path, meta: Dict[str, Any]) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO runpod_brain_artifacts
                (run_id, artifact_type, file_path, meta_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (int(run_id), _safe_text(artifact_type), str(file_path), _to_json(meta), _now()),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (_safe_text(table),),
        ).fetchone()
        return bool(row)

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
        try:
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            return [r[1] for r in rows]
        except Exception:
            return []


_LAB: Optional[RunpodBrainLab] = None


def get_runpod_brain_lab() -> RunpodBrainLab:
    global _LAB
    if _LAB is None:
        _LAB = RunpodBrainLab()
    return _LAB
