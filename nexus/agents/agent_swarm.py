#!/usr/bin/env python3
"""
Runpod CPU Swarm for Nexus Brain
--------------------------------

Generates high-volume, domain-specific training datasets in parallel while
GPU specialist training runs independently.
"""
from __future__ import annotations

import json
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _jsonl_dump(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


class CPUSwarm:
    def __init__(self, run_lab_dir: Path, max_cpu_agents: int = 50, stall_timeout_sec: int = 300):
        self.run_lab_dir = run_lab_dir
        self.exports_dir = run_lab_dir / "exports"
        self.training_data_dir = run_lab_dir / "training_data"
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        self.training_data_dir.mkdir(parents=True, exist_ok=True)
        self.max_cpu_agents = max(1, min(int(max_cpu_agents), 500))
        self.stall_timeout_sec = max(60, int(stall_timeout_sec))
        self.dashboard_path = self.exports_dir / "swarm_dashboard.json"
        self.state: Dict[str, Any] = {
            "started_at": _now(),
            "agents": {},
            "tasks_completed": 0,
            "max_cpu_agents": self.max_cpu_agents,
            "stall_timeout_sec": self.stall_timeout_sec,
        }

    def _update_dashboard(self) -> None:
        snapshot = dict(self.state)
        snapshot["updated_at"] = _now()
        _json_dump(self.dashboard_path, snapshot)

    def _run_with_restart(self, name: str, fn: Callable[[], Dict[str, Any]], max_restarts: int = 1) -> Dict[str, Any]:
        attempt = 0
        while attempt <= max_restarts:
            started = time.time()
            self.state["agents"][name] = {"status": "running", "attempt": attempt + 1, "started_at": _now()}
            self._update_dashboard()
            with ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(fn)
                try:
                    result = fut.result(timeout=self.stall_timeout_sec)
                    elapsed = round(time.time() - started, 2)
                    out = {"status": "ok", "attempt": attempt + 1, "elapsed_sec": elapsed, "result": result}
                    self.state["agents"][name] = out
                    self.state["tasks_completed"] += 1
                    self._update_dashboard()
                    return out
                except Exception as e:
                    elapsed = round(time.time() - started, 2)
                    out = {"status": "error", "attempt": attempt + 1, "elapsed_sec": elapsed, "error": str(e)}
                    self.state["agents"][name] = out
                    self._update_dashboard()
                    attempt += 1
                    if attempt > max_restarts:
                        return out
                    self.state["agents"][name] = {
                        "status": "restarting",
                        "attempt": attempt + 1,
                        "last_error": str(e),
                        "updated_at": _now(),
                    }
                    self._update_dashboard()
        return {"status": "error", "error": "unreachable"}

    def generate_synthetic_conversations(self, target_rows: int = 5000) -> Dict[str, Any]:
        random.seed(91340)
        event_types = ["wedding", "quinceanera", "corporate", "backyard_party", "birthday", "festival"]
        lead_temps = ["hot", "warm", "cold"]
        objection_patterns = [
            "too_expensive",
            "need_to_think",
            "need_partner_approval",
            "found_cheaper_option",
            "timing_uncertain",
        ]
        distance_tiers = [1, 2, 3]
        guest_buckets = [50, 80, 120, 180, 250, 350, 500]
        rows: List[Dict[str, Any]] = []
        for i in range(target_rows):
            event_type = event_types[i % len(event_types)]
            temp = lead_temps[(i // 2) % len(lead_temps)]
            objection = objection_patterns[(i // 3) % len(objection_patterns)]
            tier = distance_tiers[(i // 5) % len(distance_tiers)]
            guests = guest_buckets[(i // 7) % len(guest_buckets)]
            quote = 999 if tier == 1 else (1199 if tier == 2 else 1499)
            rows.append(
                {
                    "id": f"conv_{i+1:05d}",
                    "event_type": event_type,
                    "guest_count": guests,
                    "distance_tier": tier,
                    "lead_temperature": temp,
                    "objection_pattern": objection,
                    "conversation": [
                        {"role": "lead", "text": f"Hi, I need bathrooms for a {event_type} with around {guests} guests."},
                        {"role": "zoar", "text": f"Thanks for reaching out. We have a luxury 4-stall trailer starting at ${quote}. A $160 deposit can lock your date."},
                        {"role": "lead", "text": f"My concern is: {objection.replace('_', ' ')}."},
                        {"role": "zoar", "text": "Totally fair. I can hold your date while we finalize details. Want me to send a quick quote summary now?"},
                    ],
                    "target_outcome": "booked" if temp in {"hot", "warm"} else "nurture",
                }
            )
        out_path = self.training_data_dir / "conversations.jsonl"
        _jsonl_dump(out_path, rows)
        return {"rows": len(rows), "path": str(out_path)}

    def generate_competitor_patterns(self) -> Dict[str, Any]:
        payload = {
            "generated_at": _now(),
            "focus": "local event-rental outreach patterns",
            "patterns": [
                {"pattern": "rapid_first_response", "impact": "high", "note": "responses within 5-15 minutes improve reply probability"},
                {"pattern": "event_specific_personalization", "impact": "high", "note": "wedding vs quince messaging materially changes engagement"},
                {"pattern": "date_availability_urgency", "impact": "medium", "note": "effective when paired with clear next step"},
                {"pattern": "social_proof_before_price_push", "impact": "medium", "note": "reduces price sensitivity in premium segments"},
            ],
        }
        out_path = self.exports_dir / "competitor_patterns.json"
        _json_dump(out_path, payload)
        return {"rows": len(payload["patterns"]), "path": str(out_path)}

    def generate_la_market_context(self) -> Dict[str, Any]:
        payload = {
            "generated_at": _now(),
            "service_anchor_zip": "91340",
            "seasonality": {
                "spring": {"wedding": "high", "quinceanera": "medium", "corporate": "medium"},
                "summer": {"wedding": "high", "quinceanera": "high", "corporate": "medium"},
                "fall": {"wedding": "medium", "quinceanera": "medium", "corporate": "high"},
                "winter": {"wedding": "low", "quinceanera": "low", "corporate": "medium"},
            },
            "typical_guest_counts": {
                "wedding": [120, 250],
                "quinceanera": [100, 220],
                "corporate": [80, 300],
                "backyard_party": [40, 120],
            },
            "pricing_sensitivity_by_zip_tier": {
                "premium": {"example_zips": ["90210", "91302", "90265"], "sensitivity": "lower"},
                "core": {"example_zips": ["91340", "91405", "91605"], "sensitivity": "medium"},
                "value": {"example_zips": ["91331", "91402"], "sensitivity": "higher"},
            },
            "booking_goal": "at least one booking every 5 days",
        }
        out_path = self.exports_dir / "la_market_context.json"
        _json_dump(out_path, payload)
        return {"rows": 1, "path": str(out_path)}

    def generate_objection_dataset(self, target_rows: int = 1200) -> Dict[str, Any]:
        event_types = ["wedding", "quinceanera", "corporate", "backyard_party"]
        objections = ["too_expensive", "need_to_think", "check_with_partner", "found_cheaper", "not_ready"]
        strategies = ["value_reframe", "deposit_anchor", "date_urgency", "social_proof", "soft_nurture"]
        rows: List[Dict[str, Any]] = []
        for i in range(target_rows):
            event = event_types[i % len(event_types)]
            obj = objections[(i // 2) % len(objections)]
            strat = strategies[(i // 3) % len(strategies)]
            tier = 1 + ((i // 5) % 3)
            conv_prob = round(0.32 + (0.08 if strat in {"deposit_anchor", "value_reframe"} else 0.0) + (0.05 if event == "wedding" else 0.0), 2)
            rows.append(
                {
                    "event_type": event,
                    "price_tier": tier,
                    "objection_type": obj,
                    "response_strategy": strat,
                    "response": f"I hear you. For {event}, we can lock your date with a small $160 deposit and finalize details after.",
                    "predicted_conversion_probability": max(0.05, min(conv_prob, 0.95)),
                }
            )
        out_path = self.exports_dir / "objection_dataset.jsonl"
        _jsonl_dump(out_path, rows)
        return {"rows": len(rows), "path": str(out_path)}

    def generate_ad_copy_variants(self, target_rows: int = 500) -> Dict[str, Any]:
        formats = ["single_image", "carousel", "video_script"]
        audiences = ["wedding", "quinceanera", "corporate", "backyard"]
        angles = ["luxury_vs_porta", "climate_control", "multi_stall", "convenience"]
        rows = []
        for i in range(target_rows):
            fmt = formats[i % len(formats)]
            aud = audiences[(i // 2) % len(audiences)]
            angle = angles[(i // 3) % len(angles)]
            rows.append(
                {
                    "id": f"ad_{i+1:04d}",
                    "format": fmt,
                    "audience": aud,
                    "angle": angle,
                    "headline": "Luxury Restroom Trailer Starting at $999",
                    "primary_text": f"{aud.title()} event coming up? Upgrade guest comfort with a climate-controlled 4-stall trailer.",
                    "cta": "Check Date Availability",
                }
            )
        out_path = self.exports_dir / "ad_copy_variants.json"
        _json_dump(out_path, {"generated_at": _now(), "rows": rows})
        return {"rows": len(rows), "path": str(out_path)}

    def generate_venue_templates(self, target_rows: int = 200) -> Dict[str, Any]:
        venue_types = ["outdoor_wedding_venue", "event_hall", "backyard_space", "corporate_campus"]
        personas = ["coordinator", "events_manager", "owner", "operations_lead"]
        rows = []
        for i in range(target_rows):
            vt = venue_types[i % len(venue_types)]
            persona = personas[(i // 2) % len(personas)]
            rows.append(
                {
                    "id": f"venue_tpl_{i+1:03d}",
                    "venue_type": vt,
                    "persona": persona,
                    "subject": "Backup Restroom Coverage for Peak Event Dates",
                    "body": (
                        "Hi there, quick idea: Zoar can be your backup restroom trailer partner for dates when capacity is tight. "
                        "If a client books through your referral, we can structure a partner payout. "
                        "Happy to share a simple one-page partner overview."
                    ),
                }
            )
        out_path = self.exports_dir / "venue_templates.json"
        _json_dump(out_path, {"generated_at": _now(), "rows": rows})
        return {"rows": len(rows), "path": str(out_path)}

    def generate_autonomous_operator_pairs(self, target_rows: int = 2000) -> Dict[str, Any]:
        platforms = ["youtube", "etsy", "affiliate_blog", "newsletter", "marketplace", "reddit_content"]
        situations = [
            "ctr_low", "watch_time_drop", "conversion_drop", "traffic_up_low_revenue",
            "new_asset_launch", "retention_decline", "weekly_goal_miss", "stale_creative",
        ]
        rows: List[Dict[str, Any]] = []
        for i in range(target_rows):
            platform = platforms[i % len(platforms)]
            situation = situations[(i // 2) % len(situations)]
            rows.append(
                {
                    "instruction": (
                        "Given this platform situation, produce reflection + decision + action plan with measurable KPI "
                        "and strict safety/approval gating."
                    ),
                    "input": {"platform": platform, "situation": situation, "goal": "increase durable revenue"},
                    "output": {
                        "reflection": "Diagnose bottleneck and avoid random action changes.",
                        "decision": "Run one targeted experiment with stop-loss.",
                        "action": ["adjust primary hook", "improve CTA clarity", "measure 24h and 72h"],
                        "kpi": "qualified_conversion_rate",
                        "safety": {"requires_approval": True, "no_policy_bypass": True},
                    },
                }
            )
        out_path = self.training_data_dir / "autonomous_operator_pairs.jsonl"
        _jsonl_dump(out_path, rows)
        return {"rows": len(rows), "path": str(out_path)}

    def generate_platform_survival_pairs(self, target_rows: int = 800) -> Dict[str, Any]:
        platforms = ["youtube", "reddit", "etsy", "instagram", "facebook"]
        rows: List[Dict[str, Any]] = []
        for i in range(target_rows):
            platform = platforms[i % len(platforms)]
            rows.append(
                {
                    "instruction": "Generate compliant growth behavior for early account lifecycle.",
                    "input": {"platform": platform, "account_age_days": 2 + (i % 20)},
                    "output": {
                        "safe_behavior": ["post low frequency", "prioritize value content", "avoid spam patterns"],
                        "avoid": ["mass posting", "link dumping", "deceptive claims"],
                        "notes": "Operate as policy-compliant brand account with gradual trust building.",
                    },
                }
            )
        out_path = self.training_data_dir / "platform_survival_pairs.jsonl"
        _jsonl_dump(out_path, rows)
        return {"rows": len(rows), "path": str(out_path)}

    def generate_computer_use_pairs(self, target_rows: int = 1000) -> Dict[str, Any]:
        screens = ["dashboard_overview", "analytics_page", "editor_page", "comments_page", "listing_page"]
        rows: List[Dict[str, Any]] = []
        for i in range(target_rows):
            screen = screens[i % len(screens)]
            rows.append(
                {
                    "instruction": "From current screen context, choose a single next best action in sandbox mode.",
                    "input": {"screen_state": screen, "constraints": ["no_auto_publish", "approval_for_risky_actions"]},
                    "output": {
                        "next_action": "prepare_asset_variant",
                        "reasoning": "Improve underperforming metric before creating new campaign complexity.",
                        "verification": "capture_before_after_metrics",
                    },
                }
            )
        out_path = self.training_data_dir / "computer_use_pairs.jsonl"
        _jsonl_dump(out_path, rows)
        return {"rows": len(rows), "path": str(out_path)}

    def generate_obstacle_recovery_pairs(self, target_rows: int = 600) -> Dict[str, Any]:
        obstacles = ["policy_warning", "asset_rejection", "low_quality_score", "account_review", "sudden_drop"]
        rows: List[Dict[str, Any]] = []
        for i in range(target_rows):
            obstacle = obstacles[i % len(obstacles)]
            rows.append(
                {
                    "instruction": "Recover from obstacle with compliant remediation and fallback plan.",
                    "input": {"obstacle": obstacle},
                    "output": {
                        "root_cause_hypothesis": "activity pattern or content mismatch",
                        "recovery_steps": ["slow pace", "audit recent changes", "prepare compliant revision"],
                        "fallback": "switch to alternate approved experiment lane",
                    },
                }
            )
        out_path = self.training_data_dir / "obstacle_recovery_pairs.jsonl"
        _jsonl_dump(out_path, rows)
        return {"rows": len(rows), "path": str(out_path)}

    def generate_memory_update_pairs(self, target_rows: int = 400) -> Dict[str, Any]:
        rows: List[Dict[str, Any]] = []
        for i in range(target_rows):
            rows.append(
                {
                    "instruction": "Update memory after experiment outcome and produce next iteration plan.",
                    "input": {
                        "outcome": "win" if i % 3 else "loss",
                        "metric_delta": round(random.uniform(-0.4, 0.8), 3),
                    },
                    "output": {
                        "memory_update": "store what changed and measured impact",
                        "do_not_repeat": "repeat losing variant without new hypothesis" if i % 3 == 0 else "",
                        "next_experiment": "small controlled variant with clear KPI threshold",
                    },
                }
            )
        out_path = self.training_data_dir / "memory_update_pairs.jsonl"
        _jsonl_dump(out_path, rows)
        return {"rows": len(rows), "path": str(out_path)}

    def run(self) -> Dict[str, Any]:
        tasks: Dict[str, Callable[[], Dict[str, Any]]] = {
            "synthetic_conversation_generator": lambda: self.generate_synthetic_conversations(5000),
            "competitor_pattern_analyzer": self.generate_competitor_patterns,
            "la_market_context_builder": self.generate_la_market_context,
            "objection_dataset_builder": lambda: self.generate_objection_dataset(1200),
            "ad_copy_variation_generator": lambda: self.generate_ad_copy_variants(500),
            "venue_outreach_template_builder": lambda: self.generate_venue_templates(200),
            "autonomous_operator_pairs_builder": lambda: self.generate_autonomous_operator_pairs(2000),
            "platform_survival_pairs_builder": lambda: self.generate_platform_survival_pairs(800),
            "computer_use_pairs_builder": lambda: self.generate_computer_use_pairs(1000),
            "obstacle_recovery_pairs_builder": lambda: self.generate_obstacle_recovery_pairs(600),
            "memory_update_pairs_builder": lambda: self.generate_memory_update_pairs(400),
        }
        results: Dict[str, Any] = {}
        self._update_dashboard()
        max_workers = min(len(tasks), self.max_cpu_agents)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            fut_map = {
                pool.submit(self._run_with_restart, name, fn): name
                for name, fn in tasks.items()
            }
            for fut in fut_map:
                name = fut_map[fut]
                try:
                    results[name] = fut.result()
                except Exception as e:
                    results[name] = {"status": "error", "error": str(e)}
                self._update_dashboard()
        self.state["finished_at"] = _now()
        self.state["results"] = results
        self._update_dashboard()
        return {"ok": True, "results": results, "dashboard": str(self.dashboard_path)}


def main() -> None:
    run_lab = Path(os.environ.get("RUNPOD_LAB_DIR", "/workspace/nexus_brain_lab/run_14/runpod_lab")).resolve()
    max_cpu_agents = int(os.environ.get("MAX_CPU_AGENTS", "50"))
    stall_timeout = int(os.environ.get("AGENT_STALL_TIMEOUT_SEC", "300"))
    swarm = CPUSwarm(run_lab, max_cpu_agents=max_cpu_agents, stall_timeout_sec=stall_timeout)
    result = swarm.run()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
