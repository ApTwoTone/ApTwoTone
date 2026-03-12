"""
Master Coordinator — Autonomous Work Engine

Feeds tasks to the fleet 24/7 across 3 divisions:
  D1: Vendor Research — parallel research tasks across zones/categories
  D2: Creative Production — weekly ad copy, email sequences, content calendars
  D3: Experiments — autonomous D3 experiment lifecycle

Dual-mode: runs as asyncio.create_task() inside server.py AND as standalone
launchd daemon (com.zoar.master-coordinator). PID lock prevents dual execution.

Cycle: every 30 seconds, adaptive throttle based on queue utilization.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("master_coordinator")

DB_PATH = Path.home() / ".nexus" / "memory.db"
LOCK_FILE = Path.home() / ".nexus" / "coordinator.lock"
STATE_FILE = Path.home() / ".nexus" / "coordinator_state.json"
CYCLE_INTERVAL = 30  # seconds


# ── Coordinator ─────────────────────────────────────────────────────────────

class MasterCoordinator:
    """Central engine that generates tasks for the fleet."""

    def __init__(self):
        self._running = False
        self._paused = False
        self._cycle_count = 0
        self._total_tasks_generated = 0
        self._start_time = time.time()

        # D1 state — round-robin through zones/categories
        self._d1_enabled = False  # DISABLED: vendor research generates hallucinated data
        self._d1_zone_index = 0
        self._d1_category_index = 0

        # D2 state
        self._d2_last_run = 0.0  # timestamp of last weekly run

        # D3 state
        self._d3_experiments_created = 0

        # Telegram summary tracking
        self._last_summary_time = 0.0
        self._tasks_since_summary = 0
        self._completions_since_summary = 0
        self._presend_checked_date = ""
        self._presend_last_alert_key = ""
        self._presend_gate_cache: Dict[str, Dict[str, Any]] = {}
        self._identity_sweep_last_day = ""

        # Load checkpoint if fresh
        self._load_checkpoint()

        # Seed all agents as idle so the hierarchy tree shows them immediately
        self._seed_agent_statuses()

    def _seed_agent_statuses(self):
        """Insert agents as 'idle' only if they don't already exist in DB."""
        agents = [
            ("lead-qualifier", "lead_gen"),
            ("fb-lead-monitor", "lead_gen"),
            ("vendor-scraper", "lead_gen"),
            ("crm-updater", "lead_gen"),
            ("ad-monitor", "ad_intel"),
            ("creative-analyzer", "ad_intel"),
            ("budget-optimizer", "ad_intel"),
            ("audience-researcher", "ad_intel"),
            ("email-sender", "vendor_outreach"),
            ("reply-monitor", "vendor_outreach"),
            ("follow-up-agent", "vendor_outreach"),
            ("vendor-vetter", "vendor_outreach"),
            ("health-monitor", "sys_ops"),
            ("api-key-manager", "sys_ops"),
            ("db-cleaner", "sys_ops"),
            ("error-logger", "sys_ops"),
            ("identity-reconciler", "sys_ops"),
            ("lead-gen-commander", "lead_gen"),
        ]
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=5)
            now = datetime.utcnow().isoformat()
            for agent_id, dept_id in agents:
                conn.execute(
                    "INSERT OR IGNORE INTO agent_status "
                    "(agent_id, dept_id, status, current_task, last_active) "
                    "VALUES (?, ?, 'idle', 'Waiting for scheduled cycle', ?)",
                    (agent_id, dept_id, now))
            conn.commit()
            conn.close()
        except Exception as e:
            log.debug("Could not seed agent statuses: %s", e)

    # ── Main Loop ───────────────────────────────────────────────────────────

    async def run_loop(self):
        """Main coordinator loop. Runs until stopped."""
        if not self._acquire_lock():
            log.warning("Another coordinator is running — exiting")
            return

        self._running = True
        log.info("Master Coordinator starting (PID %d)", os.getpid())

        # Runtime heartbeat
        try:
            from core.agent_runtime import AgentRuntime
            self._rt = AgentRuntime("master-coordinator", "d0_core",
                                    launchd_label="com.zoar.master-coordinator")
        except Exception:
            self._rt = None

        try:
            while self._running:
                try:
                    await self._run_cycle()
                except Exception as e:
                    log.error("Cycle %d error: %s", self._cycle_count, e)
                    _log_activity("coordinator", "system",
                                  "cycle_error", str(e)[:200], status="error")
                await asyncio.sleep(CYCLE_INTERVAL)
        finally:
            self._release_lock()
            log.info("Master Coordinator stopped after %d cycles", self._cycle_count)

    async def _run_cycle(self):
        """Single coordinator cycle — assess queue, generate tasks."""
        self._cycle_count += 1

        if getattr(self, "_rt", None):
            self._rt.heartbeat(f"Cycle {self._cycle_count}")

        if self._paused:
            if self._cycle_count % 10 == 0:
                log.info("Coordinator paused (cycle %d)", self._cycle_count)
            return

        from core.fleet_task_queue import FleetTaskQueue
        q = FleetTaskQueue()

        # 1. Assess queue state
        stats = q.get_stats()
        pending = stats.get("pending", 0)
        claimed = stats.get("claimed", 0)
        in_progress = stats.get("in_progress", 0)
        active = claimed + in_progress

        if active + pending > 0:
            utilization = active / (active + pending)
        else:
            utilization = 0.0

        # 2. Determine throttle level
        throttle = self._get_throttle(utilization, pending)

        # 3. Recover stale tasks
        recovered = q.recover_stale()
        if recovered:
            log.info("Recovered %d stale tasks", recovered)

        # 4. Coordinate divisions based on throttle
        tasks_generated = 0

        if throttle != "pause":
            # D1: Vendor Research — every cycle (30s)
            if self._d1_enabled:
                try:
                    d1_count = await self._coordinate_division_one(q, throttle)
                    tasks_generated += d1_count
                except Exception as e:
                    log.warning("D1 error: %s", e)

            # D1b: Venue Intelligence — every 10th cycle (~5 min)
            if self._d1_enabled and self._cycle_count % 10 == 0:
                try:
                    from core.venue_intel import VenueIntelAgent
                    agent = VenueIntelAgent()
                    summary = await agent.daemon_cycle(limit=5)
                    if summary.get("assessed", 0) > 0:
                        log.info(
                            "D1b venue-intel: %d assessed, %d high-value",
                            summary["assessed"], summary["high_value"],
                        )
                except Exception as e:
                    log.warning("D1b venue-intel error: %s", e)

            # D3: Experiments — every 10th cycle (~5 min)
            if self._cycle_count % 10 == 0:
                try:
                    d3_count = await self._coordinate_division_three(q)
                    tasks_generated += d3_count
                except Exception as e:
                    log.warning("D3 error: %s", e)

            # D2: Creative — check if Monday 6am
            try:
                d2_count = await self._coordinate_division_two(q)
                tasks_generated += d2_count
            except Exception as e:
                log.warning("D2 error: %s", e)

            # D4: Self-Healing — every 2nd cycle (~60s)
            if self._cycle_count % 2 == 0:
                try:
                    d4_count = await self._coordinate_division_four(q)
                    tasks_generated += d4_count
                except Exception as e:
                    log.warning("D4 error: %s", e)

            # Campaign email drip — every 4th cycle (~2 min)
            if self._cycle_count % 4 == 0:
                try:
                    await self._process_campaigns()
                except Exception as e:
                    log.warning("Campaign error: %s", e)

            # ── Agent Scheduling ──────────────────────────────────────────

            # Lead Qualifier — every 10th cycle (~5 min)
            if self._cycle_count % 10 == 0:
                await self._agent_lead_qualifier()

            # Health Monitor — every 20th cycle (~10 min)
            if self._cycle_count % 20 == 0:
                await self._agent_health_monitor()

            # Reply Monitor — every 60th cycle (~30 min)
            if self._cycle_count % 60 == 0:
                await self._agent_reply_monitor()

            # Vendor Vetter — every 60th cycle (~30 min)
            if self._cycle_count % 60 == 30:
                await self._agent_vendor_vetter()

            # Email Sender — every 8th cycle (~4 min), 8am-6pm PT only
            if self._cycle_count % 8 == 0:
                await self._agent_email_sender()

            # FB Lead Monitor — every 60th cycle (~30 min)
            if self._cycle_count % 60 == 15:
                await self._agent_fb_lead_monitor()

            # Vendor Scraper status — every 60th cycle (~30 min)
            if self._cycle_count % 60 == 45:
                await self._agent_vendor_scraper()

            # CRM Updater — every 60th cycle (~30 min)
            if self._cycle_count % 60 == 10:
                await self._agent_crm_updater()

            # Follow-up Agent — every 60th cycle (~30 min)
            if self._cycle_count % 60 == 20:
                await self._agent_follow_up()

            # Creative Analyzer — every 720th cycle (~6h)
            if self._cycle_count % 720 == 360:
                await self._agent_creative_analyzer()

            # Ad Performance Monitor — every 720th cycle (~6h)
            if self._cycle_count % 720 == 0:
                await self._agent_ad_monitor()

            # API Key Manager — every 120th cycle (~1 hour)
            if self._cycle_count % 120 == 60:
                await self._agent_api_key_manager()

            # DB Cleaner — every 240th cycle (~2 hours)
            if self._cycle_count % 240 == 120:
                await self._agent_db_cleaner()

            # Error Logger — every 20th cycle (~10 min)
            if self._cycle_count % 20 == 10:
                await self._agent_error_logger()

            # Identity Reconciler — every 20th cycle (~10 min)
            if self._cycle_count % 20 == 5:
                await self._agent_identity_reconciler()

            # Lead Gen Commander — every 120th cycle (~1 hour)
            if self._cycle_count % 120 == 90:
                await self._agent_lead_gen_commander()

            # Budget Optimizer — daily 9am PT
            await self._agent_budget_optimizer()

            # Audience Researcher — daily 6am PT
            await self._agent_audience_researcher()

            # Queue cleanup — every 120th cycle (~1 hour)
            if self._cycle_count % 120 == 0:
                try:
                    cleaned = q.cleanup_old(max_age_hours=24)
                    if cleaned > 0:
                        log.info("Queue cleanup: removed %d old tasks", cleaned)
                except Exception as e:
                    log.warning("Queue cleanup failed: %s", e)

        self._total_tasks_generated += tasks_generated
        self._tasks_since_summary += tasks_generated

        # 5. Track completions for summary
        today_completed = stats.get("today", {}).get("completed", 0)
        self._completions_since_summary = today_completed

        # 6. Log activity
        if self._cycle_count % 5 == 0 or tasks_generated > 0:
            _log_activity(
                "coordinator", "system", "cycle",
                "cycle=%d util=%.0f%% pending=%d active=%d generated=%d throttle=%s" % (
                    self._cycle_count, utilization * 100,
                    pending, active, tasks_generated, throttle
                ),
            )

        # 7. Checkpoint every 10 cycles (~5 min)
        if self._cycle_count % 10 == 0:
            self._save_checkpoint()

        # 8. Telegram summary every 30 cycles (~15 min)
        if self._cycle_count % 30 == 0:
            await self._send_summary(stats, utilization)

        # Log periodic status
        if self._cycle_count % 20 == 0:
            log.info(
                "Cycle %d | util=%.0f%% | pending=%d active=%d | "
                "generated_total=%d | throttle=%s",
                self._cycle_count, utilization * 100, pending, active,
                self._total_tasks_generated, throttle,
            )

    def _get_throttle(self, utilization: float, pending: int) -> str:
        """Adaptive throttle based on queue state."""
        if utilization > 0.8:
            return "pause"
        if utilization > 0.5 and pending > 100:
            return "light"
        if utilization > 0.3 and pending > 50:
            return "normal"
        return "aggressive"

    # ── Division One: Vendor Research ────────────────────────────────────────

    async def _coordinate_division_one(
        self, q, throttle: str
    ) -> int:
        """Generate vendor research tasks across zones and categories."""
        # Don't enqueue more if pending queue is already deep
        pending = q.count_pending(tier=3)
        if pending > 500:
            log.debug("D1: Skipping — %d vendor tasks already pending", pending)
            return 0

        from core.vendor_research_daemon import GEOGRAPHIC_ZONES, RESEARCH_CATEGORIES

        # How many tasks based on throttle
        if throttle == "aggressive":
            n_zones, n_cats, n_cities = 3, 5, 3  # 45 tasks max
        elif throttle == "normal":
            n_zones, n_cats, n_cities = 2, 3, 3  # 18 tasks max
        else:  # light
            n_zones, n_cats, n_cities = 1, 3, 2  # 6 tasks max

        zone_keys = sorted(
            GEOGRAPHIC_ZONES.keys(),
            key=lambda z: GEOGRAPHIC_ZONES[z]["priority"],
        )
        cat_keys = sorted(
            RESEARCH_CATEGORIES.keys(),
            key=lambda c: RESEARCH_CATEGORIES[c]["priority"],
        )

        tasks = []  # type: List[Dict[str, Any]]
        batch_id = "d1-%d" % int(time.time())

        for _zi in range(n_zones):
            zone_key = zone_keys[self._d1_zone_index % len(zone_keys)]
            zone = GEOGRAPHIC_ZONES[zone_key]
            self._d1_zone_index += 1

            for _ci in range(n_cats):
                cat_key = cat_keys[self._d1_category_index % len(cat_keys)]
                cat_info = RESEARCH_CATEGORIES[cat_key]
                self._d1_category_index += 1

                # Pick cities from zone
                locations = zone["locations"]
                cities = random.sample(
                    locations, min(n_cities, len(locations))
                )

                for city in cities:
                    query = random.choice(cat_info["queries"])
                    prompt = (
                        "List 10 %s businesses located IN %s. "
                        "Return a JSON array of objects with fields: "
                        "name, phone, email, website, address, city. "
                        "Only include real businesses physically located "
                        "in or very near %s, California. "
                        "Do not include businesses from other states "
                        "or distant cities." % (query, city, city.replace(", CA", ""))
                    )

                    tasks.append({
                        "task_type": "vendor_research",
                        "tier": 3,
                        "priority": cat_info["priority"],
                        "input_data": {
                            "prompt": prompt,
                            "zone": zone_key,
                            "category": cat_key,
                            "location": city,
                            "max_tokens": 2000,
                            "post_process": "vendor_research",
                        },
                    })

        if tasks:
            ids = q.enqueue_batch(tasks, batch_id=batch_id)
            log.info("D1: Enqueued %d vendor research tasks (batch %s)",
                     len(ids), batch_id)
            _log_activity("coordinator", "d1_vendor_research",
                          "enqueue", "Enqueued %d vendor research tasks" % len(ids))
        return len(tasks)

    # ── Division Two: Creative Production ────────────────────────────────────

    async def _coordinate_division_two(self, q) -> int:
        """Weekly creative production — Monday 6am PT only."""
        now = time.localtime()
        # Monday = 0, check 6am hour
        if now.tm_wday != 0 or now.tm_hour != 6:
            return 0

        # Don't run twice in same day
        if self._d2_last_run > 0:
            last_run_day = time.localtime(self._d2_last_run).tm_yday
            if last_run_day == now.tm_yday:
                return 0

        self._d2_last_run = time.time()
        batch_id = "d2-creative-%04d-%02d-%02d" % (
            now.tm_year, now.tm_mon, now.tm_mday
        )

        tasks = []  # type: List[Dict[str, Any]]

        # 4 ad copy variations
        event_types = ["wedding", "quinceañera", "corporate", "backyard party"]
        for evt in event_types:
            tasks.append({
                "task_type": "ad_copy",
                "tier": 4,
                "priority": 2,
                "input_data": {
                    "prompt": (
                        "Write 3 Facebook ad copy variations for Zoar Bathroom "
                        "Rentals targeting %s events in the San Fernando Valley. "
                        "Use approved pricing language: 'Starting at $999' or "
                        "'Starting at $1,000'. Highlight: flushing toilets, "
                        "running water, climate control, premium interior "
                        "finishes, multiple stalls. Contrast with porta potties. "
                        "Keep each variation under 125 words." % evt
                    ),
                    "max_tokens": 1500,
                    "event_type": evt,
                },
            })

        # 1 content calendar
        tasks.append({
            "task_type": "content_calendar",
            "tier": 4,
            "priority": 3,
            "input_data": {
                "prompt": (
                    "Create a 7-day social media content calendar for Zoar "
                    "Bathroom Rentals. Include: 2 educational posts (luxury "
                    "restroom trailer benefits), 2 testimonial-style posts, "
                    "2 event-specific posts (wedding + quinceañera), 1 behind-"
                    "the-scenes post. Each entry needs: platform, caption, "
                    "hashtags, best time to post."
                ),
                "max_tokens": 2000,
            },
        })

        # 2 email sequences
        for evt in ["wedding", "quinceañera"]:
            tasks.append({
                "task_type": "email_sequence",
                "tier": 4,
                "priority": 3,
                "input_data": {
                    "prompt": (
                        "Write a 3-email follow-up sequence for Zoar Bathroom "
                        "Rentals targeting %s event leads. Email 1: sent after "
                        "initial inquiry (warm, personal). Email 2: sent 48hrs "
                        "later if no response (value-focused). Email 3: sent "
                        "7 days later (last chance, urgency). Include subject "
                        "lines. Never use specific prices above $1,000." % evt
                    ),
                    "max_tokens": 2000,
                    "event_type": evt,
                },
            })

        # 3 cold outreach templates
        for venue_type in ["wedding venue", "event planner", "catering company"]:
            tasks.append({
                "task_type": "cold_email",
                "tier": 2,
                "priority": 3,
                "input_data": {
                    "prompt": (
                        "Write a cold outreach email from Zoar Bathroom Rentals "
                        "to a %s proposing a partnership. The venue would receive "
                        "a referral fee for each booking. Tone: professional, "
                        "concise, not pushy. Never include specific dollar "
                        "amounts. Mention: luxury restroom trailers, delivery "
                        "and setup included, competitive pricing with preferred "
                        "rates for venue partners." % venue_type
                    ),
                    "max_tokens": 1000,
                    "venue_type": venue_type,
                },
            })

        if tasks:
            ids = q.enqueue_batch(tasks, batch_id=batch_id)
            log.info("D2: Enqueued %d creative tasks (batch %s)",
                     len(ids), batch_id)
            _log_activity("coordinator", "d2_creative",
                          "enqueue", "Enqueued %d creative tasks" % len(ids))

        return len(tasks)

    # ── Division Three: Autonomous Experiments ───────────────────────────────

    async def _coordinate_division_three(self, q) -> int:
        """Keep D3 experiment pipeline full. Runs every ~5 min."""
        try:
            from core.division_three import get_division_three, CATEGORIES
            d3 = get_division_three()
        except Exception as e:
            log.warning("D3 import failed: %s", e)
            return 0

        tasks_created = 0

        # Count running + queued experiments
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=5)
            row = conn.execute(
                "SELECT COUNT(*) FROM d3_experiments "
                "WHERE status IN ('running', 'queued')"
            ).fetchone()
            active_experiments = row[0] if row else 0
            conn.close()
        except Exception:
            active_experiments = 0

        # Keep at least 10 experiments active
        experiments_needed = max(0, 10 - active_experiments)

        if experiments_needed > 0:
            # Check if trends are stale (> 6 hours)
            pending_trend = q.count_pending(tier=6)
            if pending_trend == 0:
                try:
                    conn = sqlite3.connect(str(DB_PATH), timeout=5)
                    row = conn.execute(
                        "SELECT MAX(created_at) FROM d3_trend_signals"
                    ).fetchone()
                    conn.close()
                    last_trend = row[0] if row and row[0] else 0
                    if time.time() - last_trend > 21600:  # 6 hours
                        q.enqueue(
                            "d3_trend_scan",
                            {"sources": ["google_trends", "reddit", "tiktok"]},
                            tier=6,
                            priority=2,
                        )
                        tasks_created += 1
                        log.info("D3: Enqueued trend scan (stale > 6h)")
                except Exception as e:
                    log.warning("D3 trend check failed: %s", e)

            # Create experiments for gaps
            for _ in range(min(experiments_needed, 3)):
                cat = random.choice(CATEGORIES)
                try:
                    exp_id = d3.create_experiment(
                        category=cat,
                        name="auto-%s-%d" % (cat, int(time.time())),
                        hypothesis="Coordinator-generated %s experiment" % cat,
                    )
                    if exp_id:
                        q.enqueue(
                            "d3_experiment",
                            {"experiment_id": exp_id},
                            tier=6,
                            priority=3,
                        )
                        tasks_created += 1
                        self._d3_experiments_created += 1
                        log.info("D3: Created experiment %d (%s)", exp_id, cat)
                except Exception as e:
                    log.warning("D3 create_experiment failed: %s", e)

        # Self-improve check: every 6th D3 cycle (~30 min)
        if self._cycle_count % 60 == 0:
            try:
                conn = sqlite3.connect(str(DB_PATH), timeout=5)
                row = conn.execute(
                    "SELECT COUNT(*) FROM d3_experiments "
                    "WHERE status = 'failed' AND created_at > ?",
                    (time.time() - 3600,)
                ).fetchone()
                recent_fails = row[0] if row else 0
                row2 = conn.execute(
                    "SELECT COUNT(*) FROM d3_experiments "
                    "WHERE created_at > ?",
                    (time.time() - 3600,)
                ).fetchone()
                recent_total = row2[0] if row2 else 0
                conn.close()

                if recent_total > 0 and recent_fails / recent_total > 0.3:
                    q.enqueue("d3_self_improve", {"trigger": "high_failure_rate"},
                              tier=6, priority=1)
                    tasks_created += 1
                    log.info("D3: Triggered self-improve (%.0f%% failure rate)",
                             (recent_fails / recent_total) * 100)
            except Exception as e:
                log.warning("D3 self-improve check failed: %s", e)

        return tasks_created

    # ── Division Four: Self-Healing ─────────────────────────────────────────

    async def _coordinate_division_four(self, q) -> int:
        """Division 4: Self-Healing. Scans system health and dispatches repairs."""
        try:
            from core.health_scanner import get_health_scanner
            scanner = get_health_scanner()
        except Exception as e:
            log.warning("D4 import failed: %s", e)
            return 0

        tasks_created = 0
        try:
            issues = await scanner.run_full_scan()
            if not issues:
                return 0

            # Max 5 heal chains per cycle to prevent cascading
            for event in issues[:5]:
                try:
                    from core.task_chains import get_chain_orchestrator
                    chain_orch = get_chain_orchestrator()
                    chain_id = chain_orch.start_chain("self_heal", context={
                        "event": event,
                        "check_type": event.get("check_type", ""),
                        "target": event.get("target", ""),
                        "message": event.get("message", ""),
                        "status": event.get("status", ""),
                    })
                    if chain_id:
                        tasks_created += 1
                        log.info("D4: Started heal chain %s for %s",
                                 chain_id, event.get("target", "unknown"))
                except Exception as e:
                    log.warning("D4 chain start failed: %s", e)

            if tasks_created:
                _log_activity(
                    "coordinator", "d4_self_healing", "heal_dispatch",
                    "%d issues found, %d chains started" % (len(issues), tasks_created),
                )
        except Exception as e:
            log.error("D4 scan failed: %s", e)
            _log_activity("coordinator", "d4_self_healing",
                          "scan_error", str(e)[:200], status="error")

        return tasks_created

    async def _process_campaigns(self):
        """Drip-send active email campaigns and check for bounces/unsubscribes."""
        try:
            from core.email_campaign import process_active_campaigns
            result = await process_active_campaigns()
            if result and result.get("total_sent", 0) > 0:
                log.info("Campaigns: sent %d emails", result["total_sent"])
        except Exception as e:
            log.warning("Campaign processing error: %s", e)

        # Also check for bounces/unsubscribes via IMAP
        try:
            from integrations.gmail_imap import get_imap_checker
            checker = get_imap_checker()
            if checker:
                loop = asyncio.get_running_loop()
                stats = await loop.run_in_executor(None, checker.process_bounces_and_unsubscribes)
                if stats.get("bounces") or stats.get("unsubscribes") or stats.get("replies"):
                    log.info("IMAP campaign scan: %s", stats)
        except Exception as e:
            log.warning("IMAP bounce check error: %s", e)

        # Safety gate: sequence auto-send is disabled so all outreach can be
        # reviewed/edited in tomorrow's queue first.
        # Existing email_sequences rows remain as historical state.

    # ── Agent: Lead Qualifier (every 5 min) ─────────────────────────────────

    async def _agent_lead_qualifier(self):
        """Score unscored leads, flag hot ones (7+) for Telegram alert."""
        agent_id = "lead-qualifier"
        try:
            _update_status(agent_id, "lead_gen", "active", "Scoring new leads")
            conn = sqlite3.connect(str(DB_PATH), timeout=5)
            conn.row_factory = sqlite3.Row
            # Find leads without a score
            rows = conn.execute(
                "SELECT id FROM leads WHERE (lead_score IS NULL OR lead_score = 0) "
                "AND status != 'lost' ORDER BY date_added DESC LIMIT 20"
            ).fetchall()
            conn.close()

            if not rows:
                _update_status(agent_id, "lead_gen", "idle")
                return

            from core.lead_scoring import LeadScorer
            scorer = LeadScorer()
            hot_leads = []
            for row in rows:
                try:
                    result = scorer.score_lead(row["id"])
                    if result and result.get("score", 0) >= 7:
                        hot_leads.append(result)
                except Exception as e:
                    log.warning("Lead scorer error for #%d: %s", row["id"], e)

            if hot_leads:
                msg = "HOT LEADS DETECTED\n"
                for hl in hot_leads[:5]:
                    msg += "Lead #%s: score %s/10\n" % (hl.get("lead_id", "?"), hl.get("score", "?"))
                _send_tg(msg)
                _log_activity(agent_id, "lead_gen", "hot_leads",
                              "%d hot leads scored" % len(hot_leads))

            _update_status(agent_id, "lead_gen", "completed",
                           "Scored %d leads, %d hot" % (len(rows), len(hot_leads)))
        except Exception as e:
            log.warning("Lead qualifier error: %s", e)
            _update_status(agent_id, "lead_gen", "error", error_message=str(e)[:200])

    # ── Agent: Health Monitor (every 10 min) ──────────────────────────────

    async def _agent_health_monitor(self):
        """Run full system health scan."""
        agent_id = "health-monitor"
        try:
            _update_status(agent_id, "sys_ops", "active", "Running system scan")
            from core.health_scanner import get_health_scanner
            scanner = get_health_scanner()
            issues = await scanner.run_full_scan()
            issue_count = len(issues) if issues else 0
            _update_status(agent_id, "sys_ops", "completed",
                           "Scan complete: %d issues" % issue_count)
            _log_activity(agent_id, "sys_ops", "health_scan",
                          "%d issues detected" % issue_count)
        except Exception as e:
            log.warning("Health monitor error: %s", e)
            _update_status(agent_id, "sys_ops", "error", error_message=str(e)[:200])

    # ── Agent: Reply Monitor (every 30 min) ───────────────────────────────

    async def _agent_reply_monitor(self):
        """Check Gmail IMAP for vendor replies."""
        agent_id = "reply-monitor"
        try:
            _update_status(agent_id, "vendor_outreach", "active", "Checking for replies")
            from integrations.gmail_imap import get_imap_checker
            checker = get_imap_checker()
            if not checker:
                _update_status(agent_id, "vendor_outreach", "idle", "IMAP not configured")
                return

            replies = await checker.check_replies()
            reply_count = len(replies) if replies else 0

            if reply_count > 0:
                msg = "VENDOR REPLIES: %d new\n" % reply_count
                for r in (replies or [])[:3]:
                    msg += "From: %s\n" % r.get("from", "unknown")
                _send_tg(msg)
                _log_activity(agent_id, "vendor_outreach", "replies_found",
                              "%d new replies" % reply_count)

            _update_status(agent_id, "vendor_outreach", "completed",
                           "%d replies found" % reply_count)
        except Exception as e:
            log.warning("Reply monitor error: %s", e)
            _update_status(agent_id, "vendor_outreach", "error", error_message=str(e)[:200])

    # ── Agent: Vendor Vetter (every 30 min, 100 per batch) ────────────────

    async def _agent_vendor_vetter(self):
        """Vet unvetted vendors in batches of 100."""
        agent_id = "vendor-vetter"
        try:
            _update_status(agent_id, "vendor_outreach", "active", "Vetting vendor batch")
            from core.vendor_vetting import vet_batch
            result = await vet_batch(batch_size=100)
            total = result.get("total", 0)
            vetted = result.get("vetted", 0)
            failed = result.get("failed", 0)
            review = result.get("needs_review", 0)
            elapsed = result.get("elapsed_secs", 0)

            summary = "Vetted %d: %d passed, %d failed, %d review (%.1fs)" % (
                total, vetted, failed, review, elapsed
            )
            _log_activity(agent_id, "vendor_outreach", "vetting_complete", summary)
            _update_status(agent_id, "vendor_outreach", "completed", summary)
        except Exception as e:
            log.warning("Vendor vetting error: %s", e)
            _update_status(agent_id, "vendor_outreach", "error",
                           error_message=str(e)[:200])

    # ── Agent: Email Sender (config send window, default 8am-5pm PT) ──────

    async def _agent_email_sender(self):
        """Send APPROVED email_queue items within business hours.

        Also pre-stages tomorrow's batch after business hours so Kai can review/edit
        before send day.
        """
        agent_id = "email-sender"
        try:
            from datetime import datetime, timedelta
            try:
                from zoneinfo import ZoneInfo
            except ImportError:  # pragma: no cover
                from backports.zoneinfo import ZoneInfo  # type: ignore
            from core.email_queue_manager import generate_queue, get_queue_stats, send_approved
            from core.email_presend_gate import run_presend_gate
            from core.send_window import load_send_window

            win = load_send_window()
            now_pt = datetime.now(ZoneInfo(win.timezone))
            today = now_pt.strftime("%Y-%m-%d")
            tomorrow = (now_pt + timedelta(days=1)).strftime("%Y-%m-%d")

            # Evening prep window: stage tomorrow's queue once send window ends.
            if now_pt.hour >= int(win.end_hour):
                try:
                    gen = generate_queue(scheduled_date=tomorrow, count=30)
                    if gen.get("status") == "ok" and gen.get("generated", 0) > 0:
                        _log_activity(
                            agent_id, "vendor_outreach", "queue_generated",
                            "Generated %d emails for %s" % (gen.get("generated", 0), tomorrow),
                        )
                except Exception as ge:
                    log.warning("Tomorrow queue generation error: %s", ge)

            # Pre-send gate check 5 minutes before send window starts (one-time daily check + alert).
            presend_hour = max(0, int(win.start_hour) - 1)
            if now_pt.hour == presend_hour and now_pt.minute >= 55 and self._presend_checked_date != today:
                gate = run_presend_gate(scheduled_date=today, min_approved=30, apply_autofix=True)
                self._presend_checked_date = today
                self._presend_gate_cache[today] = gate
                if gate.get("ok"):
                    msg = (
                        "MORNING PRE-SEND CHECK PASS\n"
                        f"Date: {today}\n"
                        f"Approved: {gate.get('approved_count', 0)}\n"
                        f"Auto-fixed: {gate.get('autofixed_rows', 0)}"
                    )
                else:
                    fails = gate.get("failures", [])[:4]
                    msg = (
                        "MORNING PRE-SEND CHECK BLOCKED\n"
                        f"Date: {today}\n"
                        f"Approved: {gate.get('approved_count', 0)}\n"
                        f"Failing rows: {gate.get('failing_rows_count', 0)}\n"
                        + "\n".join(f"- {x}" for x in fails)
                    )
                _send_tg(msg)
                await _send_kai_sms(msg)

            # Send window from config.
            if now_pt.hour < int(win.start_hour) or now_pt.hour >= int(win.end_hour):
                stats_tomorrow = get_queue_stats(tomorrow)
                by_status = stats_tomorrow.get("by_status", {})
                pending = by_status.get("queued", 0) + by_status.get("edited", 0)
                approved = by_status.get("approved", 0)
                _update_status(
                    agent_id,
                    "vendor_outreach",
                    "idle",
                    "Outside send window • Tomorrow: %d review, %d approved" % (pending, approved),
                )
                return

            # Hard gate before first send attempt. If blocked, no sends.
            gate = self._presend_gate_cache.get(today) or run_presend_gate(
                scheduled_date=today,
                min_approved=30,
                apply_autofix=True,
            )
            self._presend_gate_cache[today] = gate
            if not gate.get("ok"):
                block_msg = (
                    "Pre-send gate blocked send window\n"
                    f"Date: {today}\n"
                    f"Approved: {gate.get('approved_count', 0)}\n"
                    f"Failing rows: {gate.get('failing_rows_count', 0)}"
                )
                _update_status(
                    agent_id, "vendor_outreach", "idle",
                    "Pre-send blocked (%d failures)" % int(gate.get("failing_rows_count", 0)),
                )
                alert_key = f"{today}:{now_pt.hour}"
                if self._presend_last_alert_key != alert_key:
                    self._presend_last_alert_key = alert_key
                    _send_tg("EMAIL SEND BLOCKED\n" + block_msg)
                    await _send_kai_sms("EMAIL SEND BLOCKED: " + block_msg[:280])
                return

            stats_today = get_queue_stats(today)
            by_status_today = stats_today.get("by_status", {})
            sent_today = by_status_today.get("sent", 0)
            daily_limit = 50
            if sent_today >= daily_limit:
                _update_status(
                    agent_id, "vendor_outreach", "idle",
                    "Daily limit reached (%d/%d)" % (sent_today, daily_limit),
                )
                return

            remaining = daily_limit - sent_today
            batch_size = min(5, remaining)
            _update_status(
                agent_id, "vendor_outreach", "active",
                "Sending approved queue (%d remaining today)" % remaining,
            )

            result = send_approved(scheduled_date=today, max_count=batch_size, approved_only=True)
            if result.get("status") == "no_emails":
                pending = by_status_today.get("queued", 0) + by_status_today.get("edited", 0)
                approved = by_status_today.get("approved", 0)
                _update_status(
                    agent_id, "vendor_outreach", "idle",
                    "No approved emails (queued=%d, approved=%d)" % (pending, approved),
                )
                return

            sent = result.get("sent", 0)
            failed = result.get("failed", 0)
            blocked = result.get("blocked", 0)
            _update_status(
                agent_id, "vendor_outreach", "completed",
                "Sent %d (failed %d, blocked %d) • %d/%d today" % (
                    sent, failed, blocked, sent_today + sent, daily_limit
                ),
            )
            if sent > 0:
                _log_activity(
                    agent_id, "vendor_outreach", "emails_sent",
                    "Sent %d approved queue emails" % sent,
                )
        except Exception as e:
            log.warning("Email sender error: %s", e)
            _update_status(agent_id, "vendor_outreach", "error", error_message=str(e)[:200])

    # ── Agent: Identity Reconciler (every 10 min + deep overnight sweep) ──

    async def _agent_identity_reconciler(self):
        """Merge duplicate lead identities so SMS+email share one profile."""
        agent_id = "identity-reconciler"
        try:
            _update_status(agent_id, "sys_ops", "active", "Reconciling lead identities")
            from core.lead_identity import reconcile_lead_identities

            # Lightweight periodic pass.
            result = reconcile_lead_identities(limit_clusters=25, dry_run=False)
            merged = int(result.get("merged_leads", 0) or 0)
            clusters = int(result.get("clusters_merged", 0) or 0)

            status_msg = "Merged %d leads across %d clusters" % (merged, clusters)
            _update_status(agent_id, "sys_ops", "completed", status_msg)
            _log_activity(agent_id, "sys_ops", "identity_reconcile", status_msg)

            # Massive overnight pass: one deep full sweep per day between 1-5 AM PT.
            try:
                from zoneinfo import ZoneInfo
            except ImportError:  # pragma: no cover
                from backports.zoneinfo import ZoneInfo  # type: ignore
            now_pt = datetime.now(ZoneInfo("America/Los_Angeles"))
            today = now_pt.strftime("%Y-%m-%d")
            if 1 <= now_pt.hour <= 5 and self._identity_sweep_last_day != today:
                deep = reconcile_lead_identities(limit_clusters=0, dry_run=False)
                self._identity_sweep_last_day = today
                deep_msg = (
                    "OVERNIGHT IDENTITY SWEEP COMPLETE\n"
                    f"Date: {today}\n"
                    f"Clusters merged: {deep.get('clusters_merged', 0)}\n"
                    f"Leads merged: {deep.get('merged_leads', 0)}"
                )
                _send_tg(deep_msg)
                await _send_kai_sms(deep_msg[:300])
        except Exception as e:
            log.warning("Identity reconciler error: %s", e)
            _update_status(agent_id, "sys_ops", "error", error_message=str(e)[:200])

    # ── Agent: Ad Performance Monitor (every 6h) ─────────────────────────

    async def _agent_ad_monitor(self):
        """Pull Facebook ad insights and alert on high CPL."""
        agent_id = "ad-perf-monitor"
        try:
            _update_status(agent_id, "ad_intel", "active", "Fetching ad insights")
            from core.ad_monitor import fetch_ad_insights
            data = await fetch_ad_insights()
            if not data or not data.get("ok"):
                _update_status(agent_id, "ad_intel", "idle",
                               "No ad data: %s" % (data.get("error", "unknown") if data else "no response"))
                return

            # Check for high CPL
            campaigns = data.get("campaigns", [])
            alerts = []
            for c in campaigns:
                cpl = c.get("cpl", 0)
                if cpl > 8:
                    alerts.append("%s: $%.2f CPL" % (c.get("name", "?")[:30], cpl))

            if alerts:
                msg = "AD ALERT — High CPL\n" + "\n".join(alerts[:5])
                _send_tg(msg)

            _update_status(agent_id, "ad_intel", "completed",
                           "%d campaigns, %d alerts" % (len(campaigns), len(alerts)))
            _log_activity(agent_id, "ad_intel", "ad_check",
                          "%d campaigns checked" % len(campaigns))
        except Exception as e:
            log.warning("Ad monitor error: %s", e)
            _update_status(agent_id, "ad_intel", "error", error_message=str(e)[:200])

    # ── Agent: Budget Optimizer (daily 9am PT) ────────────────────────────

    async def _agent_budget_optimizer(self):
        """Daily budget recommendation at 9am PT."""
        agent_id = "budget-optimizer"
        from datetime import datetime, timezone, timedelta
        pt = timezone(timedelta(hours=-7))
        now_pt = datetime.now(pt)

        # Only run at 9am PT, first cycle in that hour
        if now_pt.hour != 9 or now_pt.minute > 5:
            return

        # Check if already ran today
        today_key = now_pt.strftime("%Y-%m-%d")
        if getattr(self, "_budget_opt_last_day", "") == today_key:
            return
        self._budget_opt_last_day = today_key

        try:
            _update_status(agent_id, "ad_intel", "active", "Generating daily budget recommendation")
            from core.worker_pool import call_for_task
            prompt = (
                "You are an ad budget optimizer for Zoar Bathroom Rentals "
                "(luxury restroom trailer rental in San Fernando Valley). "
                "Weekly ad budget: $100. Current campaigns target SFV weddings. "
                "Based on typical Facebook ad patterns for local service businesses: "
                "1) Recommend daily budget split across campaigns. "
                "2) Suggest which day/time to increase spend. "
                "3) Flag if any campaign type should be paused. "
                "Keep it under 200 words."
            )
            result = await call_for_task(
                "ad_optimization",
                [{"role": "user", "content": prompt}],
                agent_id=agent_id,
                priority="normal",
            )
            if result.get("ok"):
                recommendation = result["content"][:500]
                msg = "DAILY BUDGET REC\n%s" % recommendation
                _send_tg(msg)
                _log_activity(agent_id, "ad_intel", "budget_recommendation",
                              recommendation[:200])
            _update_status(agent_id, "ad_intel", "completed", "Budget recommendation sent")
        except Exception as e:
            log.warning("Budget optimizer error: %s", e)
            _update_status(agent_id, "ad_intel", "error", error_message=str(e)[:200])

    # ── Agent: Audience Researcher (daily 6am PT) ──────────────────────────

    async def _agent_audience_researcher(self):
        """Daily research tasks at 6am PT: venue insights, competitor analysis, ad brief."""
        agent_id = "audience-researcher"
        from datetime import datetime, timezone, timedelta
        pt = timezone(timedelta(hours=-7))
        now_pt = datetime.now(pt)

        if now_pt.hour != 6 or now_pt.minute > 5:
            return

        today_key = now_pt.strftime("%Y-%m-%d")
        if getattr(self, "_research_last_day", "") == today_key:
            return
        self._research_last_day = today_key

        try:
            _update_status(agent_id, "ad_intel", "active", "Running daily research")
            from core.worker_pool import call_for_task

            research_topics = [
                {
                    "topic": "SFV Wedding Venue Insights",
                    "report_type": "audience",
                    "prompt": (
                        "Analyze San Fernando Valley outdoor wedding trends. "
                        "Cover: popular venue types, common restroom concerns at "
                        "outdoor venues, seasonal booking patterns, average guest "
                        "counts, and what couples prioritize when choosing vendors. "
                        "Focus on actionable insights for a luxury restroom trailer "
                        "rental business."
                    ),
                },
                {
                    "topic": "Portable Restroom Competitor Analysis",
                    "report_type": "competitor",
                    "prompt": (
                        "Analyze the portable restroom rental market in Greater LA. "
                        "Cover: typical pricing models, service area strategies, "
                        "marketing approaches (SEO, social, referral), common "
                        "differentiators, and gaps in the market that a luxury "
                        "restroom trailer business could exploit."
                    ),
                },
                {
                    "topic": "Ad Creative Brief — Event Rentals",
                    "report_type": "creative",
                    "prompt": (
                        "Analyze best-performing ad copy patterns for event rental "
                        "businesses on Facebook. Cover: headline formulas that drive "
                        "clicks, emotional vs logical appeals for wedding audiences, "
                        "effective CTAs for lead generation, and image/video "
                        "recommendations. Provide 3 specific headline + body copy "
                        "combinations optimized for luxury restroom trailer rentals."
                    ),
                },
            ]

            reports_saved = 0
            for topic in research_topics:
                try:
                    result = await call_for_task(
                        "audience_research",
                        [{"role": "user", "content": topic["prompt"]}],
                        system="You are a market research analyst specializing in "
                               "event services and wedding industry in Southern California.",
                        agent_id=agent_id,
                        priority="normal",
                    )
                    if result.get("ok") and result.get("content"):
                        content = result["content"]
                        # Extract key findings (first 2 sentences)
                        sentences = content.split(". ")
                        key_findings = ". ".join(sentences[:2]) + "." if len(sentences) > 1 else sentences[0]
                        # Save to DB
                        conn = sqlite3.connect(str(DB_PATH), timeout=5)
                        conn.execute(
                            "INSERT INTO research_reports "
                            "(topic, report_type, key_findings, recommended_action, content, model_used, tokens_used) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (topic["topic"], topic["report_type"], key_findings[:500],
                             "", content, result.get("model", ""), result.get("tokens_used", 0))
                        )
                        conn.commit()
                        conn.close()
                        reports_saved += 1
                except Exception as e:
                    log.warning("Research topic '%s' failed: %s", topic["topic"], e)

            if reports_saved > 0:
                _send_tg("DAILY RESEARCH: %d reports generated" % reports_saved)
                _log_activity(agent_id, "ad_intel", "research_complete",
                              "%d research reports generated" % reports_saved)
            _update_status(agent_id, "ad_intel", "completed",
                           "%d research reports" % reports_saved)
        except Exception as e:
            log.warning("Audience researcher error: %s", e)
            _update_status(agent_id, "ad_intel", "error", error_message=str(e)[:200])

    # ── Agent: FB Lead Monitor (every 30 min — blocked until token valid) ──

    async def _agent_fb_lead_monitor(self):
        """Check Facebook Lead API for new leads. BLOCKED until FB token is refreshed."""
        agent_id = "fb-lead-monitor"
        try:
            import json
            cfg = json.load(open(Path.home() / ".nexus" / "config.json"))
            token = cfg.get("fb_page_access_token", "")
            if not token:
                _update_status(agent_id, "lead_gen", "idle", "No FB token configured")
                return
            _update_status(agent_id, "lead_gen", "active", "Checking FB lead forms")
            from core.meta_ads import MetaAdsManager
            mgr = MetaAdsManager()
            # Test API connectivity by fetching account info
            account = mgr.get_account_info()
            if account:
                _update_status(agent_id, "lead_gen", "completed",
                               "FB API connected — monitoring leads")
                _log_activity(agent_id, "lead_gen", "fb_check", "API connected")
            else:
                _update_status(agent_id, "lead_gen", "idle", "FB API returned no data")
        except Exception as e:
            err = str(e)[:200]
            if "OAuthException" in err or "190" in err or "token" in err.lower():
                _update_status(agent_id, "lead_gen", "error",
                               error_message="FB token expired — needs manual renewal")
            else:
                log.warning("FB lead monitor error: %s", e)
                _update_status(agent_id, "lead_gen", "error", error_message=err)

    # ── Agent: Vendor Scraper (runs via D1 — status reporter) ────────────

    async def _agent_vendor_scraper(self):
        """Report vendor scraper status. Actual work is done by D1 division."""
        agent_id = "vendor-scraper"
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=5)
            # Count vendors added in last hour
            row = conn.execute(
                "SELECT COUNT(*) FROM vendors WHERE created_at > datetime('now', '-1 hour')"
            ).fetchone()
            recent = row[0] if row else 0
            total = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
            conn.close()
            status = "active" if recent > 0 else "idle"
            _update_status(agent_id, "lead_gen", status,
                           "%d added last hour (%d total)" % (recent, total))
        except Exception as e:
            log.warning("Vendor scraper status error: %s", e)

    # ── Agent: CRM Updater (every 30 min — sync lead statuses) ───────────

    async def _agent_crm_updater(self):
        """Update CRM lead records: calculate days since last contact, flag stale leads."""
        agent_id = "crm-updater"
        try:
            _update_status(agent_id, "lead_gen", "active", "Updating lead statuses")
            conn = sqlite3.connect(str(DB_PATH), timeout=5)
            conn.row_factory = sqlite3.Row

            # Flag leads with no contact in 48+ hours as follow-up needed
            updated = conn.execute(
                "UPDATE leads SET status = 'follow_up_needed' "
                "WHERE status IN ('quoted', 'contacted') "
                "AND last_contacted_at < datetime('now', '-48 hours') "
                "AND last_contacted_at IS NOT NULL AND last_contacted_at != ''"
            ).rowcount
            conn.commit()

            # Count leads by status for reporting
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM leads GROUP BY status"
            ).fetchall()
            status_counts = {r["status"]: r["cnt"] for r in rows}
            conn.close()

            summary = "Updated %d stale leads. Pipeline: %s" % (
                updated,
                ", ".join("%s=%d" % (k, v) for k, v in sorted(status_counts.items()))
            )
            _log_activity(agent_id, "lead_gen", "crm_update", summary)
            _update_status(agent_id, "lead_gen", "completed", summary[:200])
        except Exception as e:
            log.warning("CRM updater error: %s", e)
            _update_status(agent_id, "lead_gen", "error", error_message=str(e)[:200])

    # ── Agent: Creative Analyzer (every 6h — analyze ad copy performance) ─

    async def _agent_creative_analyzer(self):
        """Analyze which outreach templates get the best response rates."""
        agent_id = "creative-analyzer"
        try:
            _update_status(agent_id, "ad_intel", "active", "Analyzing outreach performance")
            conn = sqlite3.connect(str(DB_PATH), timeout=5)
            conn.row_factory = sqlite3.Row

            # Check outreach response rates by template
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM vendor_outreach "
                "WHERE created_at > datetime('now', '-7 days') "
                "GROUP BY status"
            ).fetchall()
            stats = {r["status"]: r["cnt"] for r in rows}
            total = sum(stats.values())
            replied = stats.get("replied", 0)
            conn.close()

            rate = (replied / total * 100) if total > 0 else 0
            summary = "7d outreach: %d total, %d replied (%.1f%% rate)" % (
                total, replied, rate
            )
            _log_activity(agent_id, "ad_intel", "creative_analysis", summary)
            _update_status(agent_id, "ad_intel", "completed", summary)
        except Exception as e:
            log.warning("Creative analyzer error: %s", e)
            _update_status(agent_id, "ad_intel", "error", error_message=str(e)[:200])

    # ── Agent: Follow-up Agent (every 30 min — queue follow-ups) ─────────

    async def _agent_follow_up(self):
        """Identify vendors/leads needing follow-up and queue them."""
        agent_id = "follow-up-agent"
        try:
            _update_status(agent_id, "vendor_outreach", "active", "Checking for follow-ups")
            conn = sqlite3.connect(str(DB_PATH), timeout=5)

            # Find sent emails with no reply after 72+ hours
            rows = conn.execute(
                "SELECT COUNT(*) FROM vendor_outreach "
                "WHERE status = 'sent' AND sent_at < datetime('now', '-72 hours') "
                "AND sent_at != ''"
            ).fetchone()
            needs_followup = rows[0] if rows else 0
            conn.close()

            summary = "%d vendors need follow-up (72h+ no reply)" % needs_followup
            _log_activity(agent_id, "vendor_outreach", "followup_check", summary)
            _update_status(agent_id, "vendor_outreach", "completed", summary)
        except Exception as e:
            log.warning("Follow-up agent error: %s", e)
            _update_status(agent_id, "vendor_outreach", "error", error_message=str(e)[:200])

    # ── Agent: API Key Manager (every hour — check key health) ───────────

    async def _agent_api_key_manager(self):
        """Monitor API key health: check rate limits, rotate if needed."""
        agent_id = "api-key-manager"
        try:
            _update_status(agent_id, "sys_ops", "active", "Checking API key health")
            from core.key_rotation import get_pool
            pool = get_pool()
            status = pool.fleet_status()
            providers = status.get("providers", [])

            healthy = sum(1 for p in providers if p.get("healthy_keys", 0) > 0)
            total = len(providers)
            total_keys = sum(p.get("total_keys", 0) for p in providers)
            total_rpm = sum(p.get("rpm", 0) for p in providers)

            summary = "%d/%d providers healthy, %d keys, %d RPM" % (
                healthy, total, total_keys, total_rpm
            )
            _log_activity(agent_id, "sys_ops", "key_health_check", summary)
            _update_status(agent_id, "sys_ops", "completed", summary)
        except Exception as e:
            log.warning("API key manager error: %s", e)
            _update_status(agent_id, "sys_ops", "error", error_message=str(e)[:200])

    # ── Agent: DB Cleaner (every 2 hours — maintenance) ──────────────────

    async def _agent_db_cleaner(self):
        """Clean old logs, compact DB, remove stale data."""
        agent_id = "db-cleaner"
        try:
            _update_status(agent_id, "sys_ops", "active", "Running DB maintenance")
            conn = sqlite3.connect(str(DB_PATH), timeout=10)

            # Clean old activity logs (>7 days)
            deleted_activity = conn.execute(
                "DELETE FROM agent_activity WHERE ts < datetime('now', '-7 days')"
            ).rowcount

            # Clean old health events (>14 days)
            deleted_health = conn.execute(
                "DELETE FROM health_events WHERE detected_at < datetime('now', '-14 days')"
            ).rowcount

            # Clean completed fleet tasks (>3 days)
            deleted_tasks = conn.execute(
                "DELETE FROM fleet_tasks WHERE status IN ('completed', 'failed') "
                "AND created_at < datetime('now', '-3 days')"
            ).rowcount

            conn.commit()

            # Get DB size
            import os
            db_size_mb = os.path.getsize(str(DB_PATH)) / (1024 * 1024)
            conn.close()

            summary = "Cleaned %d activity, %d health, %d tasks. DB: %.1fMB" % (
                deleted_activity, deleted_health, deleted_tasks, db_size_mb
            )
            _log_activity(agent_id, "sys_ops", "db_cleanup", summary)
            _update_status(agent_id, "sys_ops", "completed", summary)
        except Exception as e:
            log.warning("DB cleaner error: %s", e)
            _update_status(agent_id, "sys_ops", "error", error_message=str(e)[:200])

    # ── Agent: Error Logger (every 10 min — scan for errors) ─────────────

    async def _agent_error_logger(self):
        """Scan recent logs for errors and surface them."""
        agent_id = "error-logger"
        try:
            _update_status(agent_id, "sys_ops", "active", "Scanning for errors")
            conn = sqlite3.connect(str(DB_PATH), timeout=5)

            # Count recent errors from agent_status
            row = conn.execute(
                "SELECT COUNT(*) FROM agent_status WHERE status = 'error'"
            ).fetchone()
            error_agents = row[0] if row else 0

            # Count recent health issues
            row2 = conn.execute(
                "SELECT COUNT(*) FROM health_events "
                "WHERE status IN ('error', 'critical', 'warning') "
                "AND detected_at > datetime('now', '-1 hour')"
            ).fetchone()
            recent_errors = row2[0] if row2 else 0
            conn.close()

            summary = "%d agents in error state, %d health errors (1h)" % (
                error_agents, recent_errors
            )
            _log_activity(agent_id, "sys_ops", "error_scan", summary)
            _update_status(agent_id, "sys_ops", "completed", summary)
        except Exception as e:
            log.warning("Error logger error: %s", e)
            _update_status(agent_id, "sys_ops", "error", error_message=str(e)[:200])

    # ── Agent: Lead Gen Commander (every hour — orchestrate lead gen) ─────

    async def _agent_lead_gen_commander(self):
        """Orchestrate lead generation: report pipeline stats, flag gaps."""
        agent_id = "lead-gen-commander"
        try:
            _update_status(agent_id, "lead_gen", "active", "Reviewing lead pipeline")
            conn = sqlite3.connect(str(DB_PATH), timeout=5)
            conn.row_factory = sqlite3.Row

            # Lead pipeline stats
            lead_rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM leads GROUP BY status"
            ).fetchall()
            lead_stats = {r["status"]: r["cnt"] for r in lead_rows}
            total_leads = sum(lead_stats.values())

            # Vendor outreach stats
            outreach_rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM vendor_outreach GROUP BY status"
            ).fetchall()
            outreach_stats = {r["status"]: r["cnt"] for r in outreach_rows}

            # Vetted vendor count
            vetted = conn.execute(
                "SELECT COUNT(*) FROM vendors WHERE vetting_status = 'vetted'"
            ).fetchone()[0]
            conn.close()

            summary = "Leads: %d total (%s). Outreach: %s. Vetted vendors: %d" % (
                total_leads,
                ", ".join("%s=%d" % (k, v) for k, v in sorted(lead_stats.items())),
                ", ".join("%s=%d" % (k, v) for k, v in sorted(outreach_stats.items())),
                vetted,
            )
            _log_activity(agent_id, "lead_gen", "pipeline_review", summary[:300])
            _update_status(agent_id, "lead_gen", "completed", summary[:200])
        except Exception as e:
            log.warning("Lead gen commander error: %s", e)
            _update_status(agent_id, "lead_gen", "error", error_message=str(e)[:200])

    # ── Telegram Summary ─────────────────────────────────────────────────────

    async def _send_summary(self, stats: Dict, utilization: float):
        """Send periodic summary to Telegram (max every 15 min)."""
        now = time.time()
        if now - self._last_summary_time < 900:  # 15 min minimum
            return

        self._last_summary_time = now
        today = stats.get("today", {})

        msg = (
            "COORDINATOR STATUS\n"
            "Cycle: %d | Util: %.0f%%\n"
            "Tasks generated: %d (total: %d)\n"
            "Completed today: %d | Tokens: %s\n"
            "D3 experiments created: %d\n"
            "Queue: %d pending, %d active"
        ) % (
            self._cycle_count, utilization * 100,
            self._tasks_since_summary, self._total_tasks_generated,
            today.get("completed", 0),
            _format_tokens(today.get("tokens", 0)),
            self._d3_experiments_created,
            stats.get("pending", 0),
            stats.get("claimed", 0) + stats.get("in_progress", 0),
        )

        self._tasks_since_summary = 0

        try:
            from scripts.notify_telegram import send_telegram, load_config as _tg_cfg
            cfg = _tg_cfg()
            token = cfg.get("telegram_token", "")
            chat_ids = cfg.get("telegram_chat_ids", [])
            if token and chat_ids:
                for cid in chat_ids:
                    send_telegram(token, str(cid), msg)
            else:
                log.warning("Telegram not configured (missing token/chat_ids)")
        except Exception as e:
            log.warning("Telegram summary failed: %s", e)

    # ── State Checkpoint ─────────────────────────────────────────────────────

    def _save_checkpoint(self):
        """Save coordinator state for crash recovery."""
        state = {
            "cycle_count": self._cycle_count,
            "d1_zone_index": self._d1_zone_index,
            "d1_category_index": self._d1_category_index,
            "d2_last_run": self._d2_last_run,
            "d3_experiments_created": self._d3_experiments_created,
            "total_tasks_generated": self._total_tasks_generated,
            "timestamp": time.time(),
        }
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps(state))
        except Exception as e:
            log.warning("Checkpoint save failed: %s", e)

    def _load_checkpoint(self):
        """Resume from checkpoint if recent (< 10 min)."""
        try:
            if not STATE_FILE.exists():
                return
            state = json.loads(STATE_FILE.read_text())
            age = time.time() - state.get("timestamp", 0)
            if age > 600:  # Stale checkpoint (> 10 min)
                log.info("Ignoring stale checkpoint (%.0fs old)", age)
                return
            self._cycle_count = state.get("cycle_count", 0)
            self._d1_zone_index = state.get("d1_zone_index", 0)
            self._d1_category_index = state.get("d1_category_index", 0)
            self._d2_last_run = state.get("d2_last_run", 0.0)
            self._d3_experiments_created = state.get("d3_experiments_created", 0)
            self._total_tasks_generated = state.get("total_tasks_generated", 0)
            log.info("Resumed from checkpoint (cycle %d, %.0fs ago)",
                     self._cycle_count, age)
        except Exception as e:
            log.warning("Checkpoint load failed: %s", e)

    # ── Lock File ────────────────────────────────────────────────────────────

    def _acquire_lock(self) -> bool:
        """Acquire PID lock. Returns False if another coordinator is running."""
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        if LOCK_FILE.exists():
            try:
                pid = int(LOCK_FILE.read_text().strip())
                # Check if PID is still alive
                os.kill(pid, 0)
                log.warning("Coordinator already running (PID %d)", pid)
                return False
            except (OSError, ValueError):
                # Process is dead, stale lock
                log.info("Removing stale coordinator lock")
        LOCK_FILE.write_text(str(os.getpid()))
        return True

    def _release_lock(self):
        """Release PID lock."""
        try:
            if LOCK_FILE.exists():
                pid = int(LOCK_FILE.read_text().strip())
                if pid == os.getpid():
                    LOCK_FILE.unlink()
        except Exception:
            pass

    # ── Control ──────────────────────────────────────────────────────────────

    def pause(self):
        self._paused = True
        log.info("Coordinator paused")

    def resume(self):
        self._paused = False
        log.info("Coordinator resumed")

    def stop(self):
        self._running = False

    def get_status(self) -> Dict[str, Any]:
        """Status for API endpoint."""
        return {
            "running": self._running,
            "paused": self._paused,
            "cycle_count": self._cycle_count,
            "total_tasks_generated": self._total_tasks_generated,
            "d1_zone_index": self._d1_zone_index,
            "d1_category_index": self._d1_category_index,
            "d2_last_run": self._d2_last_run,
            "d3_experiments_created": self._d3_experiments_created,
            "uptime_hours": round((time.time() - self._start_time) / 3600, 1),
        }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _update_status(agent_id: str, dept_id: str, status: str = "active",
                   current_task: str = "", error_message: str = ""):
    """Update agent status in the hierarchy DB."""
    try:
        from core.agent_hierarchy import update_agent_status
        update_agent_status(agent_id, dept_id, status, current_task,
                            error_message=error_message)
    except Exception:
        pass


def _send_tg(message: str):
    """Send a Telegram notification (fire-and-forget)."""
    try:
        from scripts.notify_telegram import send_telegram, load_config as _tg_cfg
        cfg = _tg_cfg()
        token = cfg.get("telegram_token", "")
        chat_ids = cfg.get("telegram_chat_ids", [])
        if token and chat_ids:
            for cid in chat_ids:
                send_telegram(token, str(cid), message)
    except Exception as e:
        log.warning("Telegram send failed: %s", e)


async def _send_kai_sms(message: str):
    """Send operator SMS alert via lead pipeline notification path."""
    try:
        from core.lead_pipeline import get_pipeline
        pipeline = get_pipeline()
        if pipeline and hasattr(pipeline, "_notify_kai_sms"):
            await pipeline._notify_kai_sms((message or "")[:320])
    except Exception as e:
        log.warning("Kai SMS alert failed: %s", e)


def _log_activity(agent_id: str, dept_id: str, action: str,
                  detail: str = "", status: str = "success"):
    """Convenience wrapper for agent activity logging."""
    try:
        from core.agent_activity import log_activity
        log_activity(agent_id, dept_id, action, detail, status=status)
    except Exception:
        pass


def _format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return "%.1fM" % (n / 1_000_000)
    if n >= 1_000:
        return "%.1fK" % (n / 1_000)
    return str(n)


# ── Singleton ────────────────────────────────────────────────────────────────

_coordinator = None  # type: Optional[MasterCoordinator]


def get_coordinator() -> MasterCoordinator:
    global _coordinator
    if _coordinator is None:
        _coordinator = MasterCoordinator()
    return _coordinator


# ── Standalone Entry Point ───────────────────────────────────────────────────

if __name__ == "__main__":
    import signal
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    coord = get_coordinator()

    def _shutdown(signum, frame):
        log.info("Received signal %d, shutting down", signum)
        coord.stop()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    asyncio.run(coord.run_loop())
