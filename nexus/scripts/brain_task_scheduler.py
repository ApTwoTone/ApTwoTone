#!/usr/bin/env python3
"""
Nexus Brain Task Scheduler — always-on task producer for brain_agent_runner.

Purpose:
- Keep the brain queue warm 24/7 so specialist agents never sit idle.
- Bias all work toward Zoar's primary KPI: more qualified bookings.
- Enforce safety constraints (no outbound actions, no film-industry targeting,
  and no personal-info handling in generated objectives).

Usage:
  python scripts/brain_task_scheduler.py --once
  python scripts/brain_task_scheduler.py --daemon --poll-seconds 300
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

# Ensure repo root import path when launched from launchd
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.nexus_brain_control_plane import NexusBrainControlPlane

log = logging.getLogger("brain_task_scheduler")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)

PT = ZoneInfo("America/Los_Angeles")
CONFIG_PATH = Path.home() / ".nexus" / "config.json"
MEMORY_DB = Path.home() / ".nexus" / "memory.db"

_RUNNING = True

# Safety filters — hard block banned verticals and personal identifiers.
BLOCKED_TERMS = (
    "film",
    "movie",
    "cinema",
    "hollywood",
    "grip",
    "lighting_design",
    "adult",
)
PII_MARKERS = (
    "@gmail.com",
    "@yahoo.com",
    "@outlook.com",
    "818-",
    "(818)",
    "424-",
    "(424)",
)


def _load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _now_pt() -> datetime:
    return datetime.now(PT)


def _within_booking_window(now_pt: datetime, cfg: Dict[str, Any]) -> bool:
    start_h = int(cfg.get("booking_focus_start_hour", 8) or 8)
    end_h = int(cfg.get("booking_focus_end_hour", 17) or 17)
    # Inclusive start, exclusive end
    return start_h <= int(now_pt.hour) < end_h


def _contains_blocked_text(text: str) -> bool:
    t = (text or "").lower()
    if any(term in t for term in BLOCKED_TERMS):
        return True
    if any(marker in t for marker in PII_MARKERS):
        return True
    return False


def _get_recent_reply_stats(hours: int = 24) -> Dict[str, int]:
    """
    Best-effort KPI context from memory.db, used only to enrich objectives.
    """
    if not MEMORY_DB.exists():
        return {"replies": 0, "bounces": 0}
    try:
        conn = sqlite3.connect(str(MEMORY_DB), timeout=5)
        conn.row_factory = sqlite3.Row
        replies = conn.execute(
            "SELECT COUNT(*) AS c FROM activity_log WHERE event_type='lead_reply' "
            "AND created_at >= datetime('now', ?)",
            (f"-{int(hours)} hours",),
        ).fetchone()["c"]
        bounces = conn.execute(
            "SELECT COUNT(*) AS c FROM email_bounce_log "
            "WHERE created_at >= datetime('now', ?)",
            (f"-{int(hours)} hours",),
        ).fetchone()["c"]
        conn.close()
        return {"replies": int(replies or 0), "bounces": int(bounces or 0)}
    except Exception:
        return {"replies": 0, "bounces": 0}


def _task_templates(now_pt: datetime, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    booking_window = _within_booking_window(now_pt, cfg)
    kpi = _get_recent_reply_stats(hours=24)
    base_constraints = {
        "goal": "bookings_every_5_days",
        "no_outbound": True,
        "exclude_industries": ["film_production", "grip", "lighting_design"],
        "privacy_mode": "no_personal_contact_data",
        "region_focus": ["San Fernando Valley", "Greater Los Angeles"],
    }

    templates: List[Dict[str, Any]] = [
        {
            "task_type": "lead_scoring",
            "lane": "lead_ops",
            "priority": 18,
            "target_active": 2,
            "objective": (
                "Score newest leads by booking probability and return a ranked top-20 with"
                " rationale focused on event intent, timeline urgency, and contactability."
            ),
            "payload": {"window_hours": 48, "top_n": 20},
            "constraints": base_constraints,
        },
        {
            "task_type": "venue_fit_scoring",
            "lane": "lead_ops",
            "priority": 22,
            "target_active": 2,
            "objective": (
                "Evaluate venue/partner prospects for referral likelihood. Prioritize wedding,"
                " quinceañera, corporate, and private-event fits in LA/SFV."
            ),
            "payload": {"top_n": 25},
            "constraints": base_constraints,
        },
        {
            "task_type": "ad_diagnostics",
            "lane": "ads_intel",
            "priority": 24,
            "target_active": 2,
            "objective": (
                "Analyze latest ad funnel signal for CPL/CTR quality under $100/week cap and"
                " propose 3 concrete optimizations with expected impact."
            ),
            "payload": {"budget_cap_weekly": 100, "kpi_snapshot": kpi},
            "constraints": base_constraints,
        },
        {
            "task_type": "creative_scoring",
            "lane": "ads_intel",
            "priority": 26,
            "target_active": 1,
            "objective": (
                "Score recent creative angles for booking intent (not just clicks). Output next"
                " 5 creative tests with strongest expected booking lift."
            ),
            "payload": {"language_set": ["en", "es"]},
            "constraints": base_constraints,
        },
        {
            "task_type": "experiment_design",
            "lane": "experiments",
            "priority": 28,
            "target_active": 1,
            "objective": (
                "Design one low-risk experiment to improve lead-to-reply conversion this week."
                " Include hypothesis, metric, and stop criteria."
            ),
            "payload": {"experiment_window_days": 7},
            "constraints": base_constraints,
        },
        {
            "task_type": "diagnostics",
            "lane": "systems",
            "priority": 30,
            "target_active": 1,
            "objective": (
                "Scan operational bottlenecks that suppress bookings (sync lag, queue drift,"
                " metric mismatches) and propose safe fixes."
            ),
            "payload": {"surface": "email_sms_ads_sync"},
            "constraints": base_constraints,
        },
    ]

    # Only schedule outreach-angle selection inside booking hours to align with
    # same-day operational follow-through.
    if booking_window:
        templates.append(
            {
                "task_type": "outreach_angle_selection",
                "lane": "lead_ops",
                "priority": 20,
                "target_active": 1,
                "objective": (
                    "Select best outreach angle for top warm leads due today. Keep language"
                    " concise, local, and conversion-focused."
                ),
                "payload": {"due_today_only": True},
                "constraints": base_constraints,
            }
        )
    return templates


def _count_active_by_type(cp: NexusBrainControlPlane) -> Dict[str, int]:
    active = cp.list_tasks(limit=1500)
    counts: Dict[str, int] = {}
    for task in active:
        status = str(task.get("status", "")).lower()
        if status not in {"queued", "claimed", "running"}:
            continue
        t = str(task.get("task_type", "")).strip().lower()
        if not t:
            continue
        counts[t] = counts.get(t, 0) + 1
    return counts


def seed_tasks(cp: NexusBrainControlPlane) -> Dict[str, Any]:
    cfg = _load_config()
    if bool(cfg.get("disable_brain_task_scheduler", False)):
        return {"seeded": 0, "skipped": "disabled_by_config"}

    now_pt = _now_pt()
    templates = _task_templates(now_pt, cfg)
    active_counts = _count_active_by_type(cp)

    seeded = 0
    skipped = 0
    for tmpl in templates:
        task_type = str(tmpl["task_type"]).lower()
        active_count = int(active_counts.get(task_type, 0))
        target = int(tmpl.get("target_active", 1))
        missing = max(0, target - active_count)
        if missing == 0:
            continue

        objective = str(tmpl.get("objective", "")).strip()
        if _contains_blocked_text(objective):
            skipped += 1
            continue

        payload = dict(tmpl.get("payload", {}))
        constraints = dict(tmpl.get("constraints", {}))
        if _contains_blocked_text(json.dumps(payload, ensure_ascii=False)):
            skipped += 1
            continue

        for _ in range(missing):
            res = cp.enqueue_task(
                task_type=task_type,
                objective=objective,
                lane=str(tmpl.get("lane", "core_ops")),
                priority=int(tmpl.get("priority", 50)),
                payload=payload,
                constraints=constraints,
                requested_by="brain_task_scheduler",
            )
            if res.get("ok"):
                seeded += 1
                active_counts[task_type] = active_counts.get(task_type, 0) + 1
            else:
                skipped += 1
                log.warning("enqueue failed type=%s err=%s", task_type, res.get("error", "unknown"))

    return {
        "seeded": seeded,
        "skipped": skipped,
        "active_counts": active_counts,
    }


def _handle_stop(_signum, _frame):
    global _RUNNING
    _RUNNING = False


def run_daemon(poll_seconds: int) -> int:
    cp = NexusBrainControlPlane()
    runtime = None
    try:
        from core.agent_runtime import AgentRuntime

        runtime = AgentRuntime(
            "brain-task-scheduler",
            "d0_core",
            "launchd",
            "com.zoar.brain-task-scheduler",
        )
    except Exception as e:
        log.warning("agent runtime unavailable: %s", e)

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    log.info("brain_task_scheduler daemon started (poll=%ss)", poll_seconds)
    while _RUNNING:
        try:
            result = seed_tasks(cp)
            detail = "seeded=%s skipped=%s" % (result.get("seeded", 0), result.get("skipped", 0))
            if runtime:
                runtime.heartbeat("Brain queue seeding: %s" % detail, 100)
            log.info("cycle complete: %s", detail)
        except Exception as e:
            if runtime:
                runtime.report_error(str(e), will_retry=True)
            log.exception("scheduler cycle failed: %s", e)
        time.sleep(max(30, int(poll_seconds)))

    if runtime:
        runtime.shutdown()
    log.info("brain_task_scheduler stopped")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Nexus brain task scheduler")
    parser.add_argument("--daemon", action="store_true", help="Run continuously")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--poll-seconds", type=int, default=300, help="Daemon poll interval")
    args = parser.parse_args()

    cp = NexusBrainControlPlane()
    if args.once or not args.daemon:
        result = seed_tasks(cp)
        log.info("seed summary: %s", result)
        return 0
    return run_daemon(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
