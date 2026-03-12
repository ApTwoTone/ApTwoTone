"""
Nexus Division Three — Autonomous Zero-Cost Revenue Experimentation.

Manages synthetic personas, trend intelligence, and 5 experiment categories:
1. Viral Videos — scripts, thumbnails, titles, posting strategies
2. SEO Affiliate — articles, metadata, affiliate placement
3. Digital Products — ebooks, courses, templates, pricing
4. Social Growth — content calendars, engagement strategies
5. Service Arbitrage — listings, pricing analysis, pitch templates

All content generated via free AI providers (ZAI GLM primary, Ollama fallback).
Zero personal data. Fully autonomous. Self-improving via BuildPipeline.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("division_three")

DB_PATH = Path.home() / ".nexus" / "memory.db"

CATEGORIES = [
    "viral_video",
    "seo_affiliate",
    "digital_product",
    "social_growth",
    "service_arbitrage",
    "autonomous_entrepreneur",
]

# Paths for autonomous entrepreneur file-based memory
_AGENT_DIR = Path.home() / ".nexus" / "autonomous_agent"
_BROWSERS_DIR = _AGENT_DIR / "browsers"
_MEMORIES_DIR = _AGENT_DIR / "memories"
_JOURNEY_DIR = _AGENT_DIR / "journey_log"
_CONTROL_DIR = _AGENT_DIR / "control"
_RUNSTATE_PATH = _CONTROL_DIR / "runtime_state.json"
_REVENUE_DB = _AGENT_DIR / "revenue_tracker.db"

PLATFORMS = ["tiktok", "youtube", "reddit", "twitter", "medium"]

TREND_SOURCES = ["tiktok", "youtube", "reddit", "google_trends"]


class DivisionThree:
    """Autonomous zero-cost revenue experimentation engine."""

    def __init__(self):
        self._last_self_improve = 0.0  # Rate limit: 1 per hour
        self._loop_task = None  # type: Optional[asyncio.Task]
        self._loop_running = False
        self._loop_interval_sec = 1800
        self._loop_parallelism = 4
        self._loop_last_cycle = ""
        self._loop_last_summary = {}
        self._runtime_lock = asyncio.Lock()
        self._init_agent_workspace()

    def _init_agent_workspace(self) -> None:
        for p in (_AGENT_DIR, _BROWSERS_DIR, _MEMORIES_DIR, _JOURNEY_DIR, _CONTROL_DIR):
            p.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_REVENUE_DB), timeout=10)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS revenue_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT DEFAULT (datetime('now')),
                    persona_id TEXT DEFAULT '',
                    experiment_id INTEGER DEFAULT 0,
                    stream TEXT DEFAULT '',
                    action_id TEXT DEFAULT '',
                    action_name TEXT DEFAULT '',
                    delta REAL DEFAULT 0.0,
                    notes TEXT DEFAULT ''
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_revenue_ts ON revenue_events(ts DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_revenue_persona ON revenue_events(persona_id, ts DESC)")
            conn.commit()
        finally:
            conn.close()
        if not _RUNSTATE_PATH.exists():
            _RUNSTATE_PATH.write_text(
                json.dumps(
                    {
                        "running": False,
                        "interval_sec": self._loop_interval_sec,
                        "parallelism": self._loop_parallelism,
                        "last_cycle": "",
                        "last_summary": {},
                        "updated_at": datetime.utcnow().isoformat(),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _save_runtime_state(self) -> None:
        payload = {
            "running": bool(self._loop_running),
            "interval_sec": int(self._loop_interval_sec),
            "parallelism": int(self._loop_parallelism),
            "last_cycle": self._loop_last_cycle,
            "last_summary": self._loop_last_summary,
            "updated_at": datetime.utcnow().isoformat(),
        }
        _CONTROL_DIR.mkdir(parents=True, exist_ok=True)
        _RUNSTATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    def _runtime_state(self) -> Dict[str, Any]:
        if not _RUNSTATE_PATH.exists():
            return {
                "running": False,
                "interval_sec": self._loop_interval_sec,
                "parallelism": self._loop_parallelism,
                "last_cycle": "",
                "last_summary": {},
            }
        try:
            return json.loads(_RUNSTATE_PATH.read_text())
        except Exception:
            return {
                "running": self._loop_running,
                "interval_sec": self._loop_interval_sec,
                "parallelism": self._loop_parallelism,
                "last_cycle": self._loop_last_cycle,
                "last_summary": self._loop_last_summary,
            }

    def _revenue_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(_REVENUE_DB), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _ensure_persona_workspace(self, persona_id: str, stream: str = "") -> Dict[str, str]:
        safe_persona = "".join(ch for ch in str(persona_id) if ch.isalnum() or ch in ("_", "-")) or "persona"
        profile_name = ("%s_persona_%s" % ((stream or "general"), safe_persona)).lower()
        profile_dir = _BROWSERS_DIR / profile_name
        profile_dir.mkdir(parents=True, exist_ok=True)
        meta_path = profile_dir / "profile_meta.json"
        if not meta_path.exists():
            meta_path.write_text(
                json.dumps(
                    {
                        "persona_id": safe_persona,
                        "profile_name": profile_name,
                        "stream": stream or "general",
                        "created_at": datetime.utcnow().isoformat(),
                        "notes": "Persistent browser profile workspace (no automated external posting without approval).",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        return {
            "persona_id": safe_persona,
            "profile_name": profile_name,
            "profile_dir": str(profile_dir),
            "meta_path": str(meta_path),
        }

    def _record_revenue_event(
        self,
        persona_id: str,
        experiment_id: int,
        stream: str,
        action_id: str,
        action_name: str,
        delta: float,
        notes: str = "",
    ) -> None:
        conn = self._revenue_conn()
        try:
            conn.execute(
                """
                INSERT INTO revenue_events (ts, persona_id, experiment_id, stream, action_id, action_name, delta, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.utcnow().isoformat(),
                    str(persona_id),
                    int(experiment_id or 0),
                    str(stream or ""),
                    str(action_id or ""),
                    str(action_name or ""),
                    float(delta or 0.0),
                    str(notes or ""),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _get_revenue_summary(self) -> Dict[str, Any]:
        conn = self._revenue_conn()
        try:
            total_row = conn.execute("SELECT COALESCE(SUM(delta), 0) AS total FROM revenue_events").fetchone()
            by_stream = conn.execute(
                "SELECT stream, COALESCE(SUM(delta), 0) AS total FROM revenue_events GROUP BY stream ORDER BY total DESC"
            ).fetchall()
            recent = conn.execute("SELECT * FROM revenue_events ORDER BY ts DESC LIMIT 25").fetchall()
            return {
                "total": float(total_row["total"] if total_row else 0.0),
                "by_stream": [dict(r) for r in by_stream],
                "recent_events": [dict(r) for r in recent],
            }
        finally:
            conn.close()

    # ── Persona Management ────────────────────────────────────────────────────

    async def create_persona(self, platform: str, niche: str) -> Optional[int]:
        """Generate and store a synthetic persona for a platform/niche."""
        from core.worker_pool import call_for_task

        system = (
            "You generate fictional social media personas. Output JSON only.\n"
            "Schema: {\"name\": str, \"personality\": {\"tone\": str, \"style\": str, "
            "\"interests\": [str], \"posting_schedule\": str}, "
            "\"bio\": str, \"target_audience\": str}"
        )
        messages = [{"role": "user", "content": (
            "Create a fictional persona for %s in the %s niche.\n"
            "Make it unique, authentic-sounding, and optimized for engagement.\n"
            "Output ONLY valid JSON."
        ) % (platform, niche)}]

        result = await call_for_task(
            "d3_persona_gen", messages, system=system,
            max_tokens=1024, temperature=0.8,
        )

        if not result["ok"]:
            log.error("Persona generation failed: %s", result.get("content", "")[:200])
            return None

        persona_data = self._extract_json(result["content"])
        if not persona_data:
            persona_data = {"name": "Persona_%d" % int(time.time()), "personality": {}}

        name = persona_data.get("name", "Persona_%d" % int(time.time()))
        personality = {
            k: persona_data.get(k, persona_data.get("personality", {}).get(k, ""))
            for k in ["tone", "style", "interests", "posting_schedule", "bio", "target_audience"]
        }

        now = datetime.utcnow().isoformat()
        conn = self._conn()
        try:
            cur = conn.execute(
                """INSERT INTO d3_personas (name, platform, niche, personality, metrics, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, '{}', 'active', ?, ?)""",
                (name, platform, niche, json.dumps(personality), now, now),
            )
            conn.commit()
            persona_id = cur.lastrowid
            self._ensure_persona_workspace(str(persona_id), platform)
            log.info("Created persona %d: %s (%s/%s)", persona_id, name, platform, niche)
            return persona_id
        finally:
            conn.close()

    def get_personas(self, platform: str = "", status: str = "") -> List[Dict]:
        """List personas, optionally filtered."""
        conn = self._conn()
        try:
            sql = "SELECT * FROM d3_personas WHERE 1=1"
            params = []  # type: List[Any]
            if platform:
                sql += " AND platform = ?"
                params.append(platform)
            if status:
                sql += " AND status = ?"
                params.append(status)
            sql += " ORDER BY id DESC"
            rows = conn.execute(sql, params).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["personality"] = json.loads(d.get("personality") or "{}")
                d["metrics"] = json.loads(d.get("metrics") or "{}")
                result.append(d)
            return result
        finally:
            conn.close()

    def retire_persona(self, persona_id: int) -> bool:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE d3_personas SET status = 'retired', updated_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), persona_id),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    # ── Trend Intelligence ────────────────────────────────────────────────────

    async def scan_trends(self, sources: Optional[List[str]] = None) -> List[Dict]:
        """Scan for trending topics across platforms using AI."""
        from core.worker_pool import call_for_task

        sources = sources or TREND_SOURCES
        all_signals = []

        system = (
            "You are a trend analyst. Generate realistic trending topics for the given platform.\n"
            "Output JSON array: [{\"keyword\": str, \"score\": 0-100, "
            "\"context\": {\"related_topics\": [str], \"growth_rate\": str, \"region\": str}}]\n"
            "Generate 5-8 trends. Be specific and realistic."
        )

        for source in sources:
            messages = [{"role": "user", "content": (
                "What are the current trending topics on %s that could be monetized?\n"
                "Focus on niches with: high engagement, low competition, and affiliate/product potential.\n"
                "Output ONLY valid JSON array."
            ) % source}]

            result = await call_for_task(
                "d3_trend_scan", messages, system=system,
                max_tokens=8192, temperature=0.7,
            )

            if not result["ok"]:
                log.warning("Trend scan failed for %s", source)
                continue

            trends = self._extract_json_array(result["content"])
            now = datetime.utcnow().isoformat()

            conn = self._conn()
            try:
                for t in trends:
                    keyword = t.get("keyword", "")
                    if not keyword:
                        continue
                    score = min(100, max(0, float(t.get("score", 50))))
                    context = json.dumps(t.get("context", {}))
                    conn.execute(
                        """INSERT INTO d3_trend_signals (source, keyword, score, context, captured_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        (source, keyword, score, context, now),
                    )
                    all_signals.append({
                        "source": source, "keyword": keyword,
                        "score": score, "context": t.get("context", {}),
                    })
                conn.commit()
            finally:
                conn.close()

            log.info("Scanned %s: %d trends", source, len(trends))

        return all_signals

    def get_trends(self, source: str = "", limit: int = 50) -> List[Dict]:
        conn = self._conn()
        try:
            sql = "SELECT * FROM d3_trend_signals"
            params = []  # type: List[Any]
            if source:
                sql += " WHERE source = ?"
                params.append(source)
            sql += " ORDER BY captured_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["context"] = json.loads(d.get("context") or "{}")
                result.append(d)
            return result
        finally:
            conn.close()

    # ── Experiment Lifecycle ──────────────────────────────────────────────────

    def create_experiment(self, category: str, name: str, hypothesis: str = "",
                         persona_id: Optional[int] = None,
                         config: Optional[Dict] = None) -> Optional[int]:
        if category not in CATEGORIES:
            log.error("Invalid category: %s", category)
            return None

        now = datetime.utcnow().isoformat()
        conn = self._conn()
        try:
            cur = conn.execute(
                """INSERT INTO d3_experiments
                   (category, name, hypothesis, persona_id, config, status, created_at)
                   VALUES (?, ?, ?, ?, ?, 'queued', ?)""",
                (category, name, hypothesis, persona_id,
                 json.dumps(config or {}), now),
            )
            conn.commit()
            exp_id = cur.lastrowid
            log.info("Created experiment %d: %s (%s)", exp_id, name, category)
            return exp_id
        finally:
            conn.close()

    def get_experiment(self, experiment_id: int) -> Optional[Dict]:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM d3_experiments WHERE id = ?", (experiment_id,)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            d["config"] = json.loads(d.get("config") or "{}")
            outcomes = conn.execute(
                "SELECT * FROM d3_experiment_outcomes WHERE experiment_id = ? ORDER BY recorded_at DESC",
                (experiment_id,),
            ).fetchall()
            d["outcomes"] = [dict(o) for o in outcomes]
            return d
        finally:
            conn.close()

    def list_experiments(self, category: str = "", status: str = "") -> List[Dict]:
        conn = self._conn()
        try:
            sql = "SELECT * FROM d3_experiments WHERE 1=1"
            params = []  # type: List[Any]
            if category:
                sql += " AND category = ?"
                params.append(category)
            if status:
                sql += " AND status = ?"
                params.append(status)
            sql += " ORDER BY id DESC"
            rows = conn.execute(sql, params).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["config"] = json.loads(d.get("config") or "{}")
                result.append(d)
            return result
        finally:
            conn.close()

    async def run_experiment(self, experiment_id: int) -> Dict[str, Any]:
        """Run an experiment through its category handler."""
        exp = self.get_experiment(experiment_id)
        if not exp:
            return {"ok": False, "error": "Experiment not found"}

        if exp["status"] not in ("queued", "paused"):
            return {"ok": False, "error": "Experiment not in runnable state: %s" % exp["status"]}

        # Mark running
        now = datetime.utcnow().isoformat()
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE d3_experiments SET status = 'running', started_at = ? WHERE id = ?",
                (now, experiment_id),
            )
            conn.commit()
        finally:
            conn.close()

        handlers = {
            "viral_video": self._run_viral_video,
            "seo_affiliate": self._run_seo_affiliate,
            "digital_product": self._run_digital_product,
            "social_growth": self._run_social_growth,
            "service_arbitrage": self._run_service_arbitrage,
            "autonomous_entrepreneur": self._run_autonomous_entrepreneur,
        }

        handler = handlers.get(exp["category"])
        if not handler:
            return {"ok": False, "error": "Unknown category: %s" % exp["category"]}

        try:
            result = await handler(exp)
            # Auto-complete with content outcome
            if result.get("ok"):
                self.record_outcome(experiment_id, "content", 1.0,
                                    json.dumps(result.get("content", {})))
            return result
        except Exception as e:
            log.error("Experiment %d failed: %s", experiment_id, e)
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE d3_experiments SET status = 'failed' WHERE id = ?",
                    (experiment_id,),
                )
                conn.commit()
            finally:
                conn.close()
            return {"ok": False, "error": str(e)}

    def pause_experiment(self, experiment_id: int) -> bool:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE d3_experiments SET status = 'paused' WHERE id = ?",
                (experiment_id,),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def complete_experiment(self, experiment_id: int) -> bool:
        now = datetime.utcnow().isoformat()
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE d3_experiments SET status = 'completed', completed_at = ? WHERE id = ?",
                (now, experiment_id),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    # ── Category Handlers ─────────────────────────────────────────────────────

    async def _run_viral_video(self, exp: Dict) -> Dict[str, Any]:
        """Generate viral video content: script, thumbnail, title, tags."""
        from core.worker_pool import call_for_task

        persona = self._get_persona(exp.get("persona_id"))
        persona_ctx = ""
        if persona:
            persona_ctx = "Persona: %s (%s/%s). Style: %s." % (
                persona["name"], persona["platform"], persona["niche"],
                persona.get("personality", {}).get("tone", "engaging"),
            )

        system = (
            "You create viral video content strategies. Output JSON with:\n"
            "{\"title\": str, \"script\": str (60-90 seconds), \"thumbnail_concept\": str, "
            "\"tags\": [str], \"hook\": str (first 3 seconds), \"posting_time\": str, "
            "\"platform_notes\": str}"
        )
        messages = [{"role": "user", "content": (
            "Create a viral video concept.\n"
            "Topic/Hypothesis: %s\n"
            "%s\n"
            "Make the hook irresistible. Script should be 60-90 seconds.\n"
            "Output ONLY valid JSON."
        ) % (exp.get("hypothesis", exp["name"]), persona_ctx)}]

        result = await call_for_task(
            "d3_content_gen", messages, system=system,
            max_tokens=2048, temperature=0.8,
        )

        if not result["ok"]:
            return {"ok": False, "error": "Content generation failed"}

        content = self._extract_json(result["content"]) or {"raw": result["content"]}
        log.info("Experiment %d: viral video generated — %s", exp["id"], content.get("title", "")[:50])
        return {"ok": True, "content": content, "model": result.get("model", "")}

    async def _run_seo_affiliate(self, exp: Dict) -> Dict[str, Any]:
        """Generate SEO article with affiliate placement strategy."""
        from core.worker_pool import call_for_task

        system = (
            "You create SEO-optimized affiliate content. Output JSON with:\n"
            "{\"title\": str, \"meta_description\": str, \"outline\": [str], "
            "\"article\": str (1500+ words), \"target_keywords\": [str], "
            "\"affiliate_placements\": [{\"position\": str, \"product_type\": str, \"cta\": str}]}"
        )
        messages = [{"role": "user", "content": (
            "Write an SEO article for affiliate revenue.\n"
            "Topic: %s\n"
            "Hypothesis: %s\n"
            "Make it informative, well-structured, and naturally include affiliate opportunities.\n"
            "Output ONLY valid JSON."
        ) % (exp["name"], exp.get("hypothesis", ""))}]

        result = await call_for_task(
            "d3_seo_gen", messages, system=system,
            max_tokens=4096, temperature=0.6,
        )

        if not result["ok"]:
            return {"ok": False, "error": "SEO content generation failed"}

        content = self._extract_json(result["content"]) or {"raw": result["content"]}
        log.info("Experiment %d: SEO article generated — %s", exp["id"], content.get("title", "")[:50])
        return {"ok": True, "content": content, "model": result.get("model", "")}

    async def _run_digital_product(self, exp: Dict) -> Dict[str, Any]:
        """Generate digital product concept and outline."""
        from core.worker_pool import call_for_task

        system = (
            "You create digital product strategies. Output JSON with:\n"
            "{\"product_name\": str, \"type\": str (ebook|course|template|toolkit), "
            "\"outline\": [str], \"sample_content\": str, \"pricing_strategy\": str, "
            "\"target_audience\": str, \"distribution_channels\": [str], "
            "\"estimated_effort_hours\": int}"
        )
        messages = [{"role": "user", "content": (
            "Design a digital product for passive income.\n"
            "Concept: %s\n"
            "Hypothesis: %s\n"
            "Focus on: low effort, high perceived value, zero production cost.\n"
            "Output ONLY valid JSON."
        ) % (exp["name"], exp.get("hypothesis", ""))}]

        result = await call_for_task(
            "d3_product_gen", messages, system=system,
            max_tokens=3072, temperature=0.7,
        )

        if not result["ok"]:
            return {"ok": False, "error": "Product generation failed"}

        content = self._extract_json(result["content"]) or {"raw": result["content"]}
        log.info("Experiment %d: product generated — %s", exp["id"], content.get("product_name", "")[:50])
        return {"ok": True, "content": content, "model": result.get("model", "")}

    async def _run_social_growth(self, exp: Dict) -> Dict[str, Any]:
        """Generate social media growth strategy and content calendar."""
        from core.worker_pool import call_for_task

        persona = self._get_persona(exp.get("persona_id"))
        persona_ctx = ""
        if persona:
            persona_ctx = "Persona: %s on %s, niche: %s." % (
                persona["name"], persona["platform"], persona["niche"],
            )

        system = (
            "You create social media growth strategies. Output JSON with:\n"
            "{\"platform\": str, \"content_calendar\": [{\"day\": str, \"type\": str, "
            "\"topic\": str, \"caption\": str}], \"hashtag_strategy\": [str], "
            "\"engagement_tactics\": [str], \"growth_targets\": {\"week1\": int, "
            "\"month1\": int, \"month3\": int}}"
        )
        messages = [{"role": "user", "content": (
            "Create a social media growth plan.\n"
            "Focus: %s\n"
            "%s\n"
            "Generate a 7-day content calendar with specific posts.\n"
            "Output ONLY valid JSON."
        ) % (exp.get("hypothesis", exp["name"]), persona_ctx)}]

        result = await call_for_task(
            "d3_content_gen", messages, system=system,
            max_tokens=3072, temperature=0.7,
        )

        if not result["ok"]:
            return {"ok": False, "error": "Social growth plan failed"}

        content = self._extract_json(result["content"]) or {"raw": result["content"]}
        log.info("Experiment %d: social growth plan generated", exp["id"])
        return {"ok": True, "content": content, "model": result.get("model", "")}

    async def _run_service_arbitrage(self, exp: Dict) -> Dict[str, Any]:
        """Generate service arbitrage strategy and pitch templates."""
        from core.worker_pool import call_for_task

        system = (
            "You create service arbitrage strategies. Output JSON with:\n"
            "{\"service_type\": str, \"source_platform\": str, \"sell_platform\": str, "
            "\"pricing\": {\"buy_price\": str, \"sell_price\": str, \"margin\": str}, "
            "\"listing_copy\": str, \"pitch_template\": str, "
            "\"fulfillment_plan\": str, \"risks\": [str]}"
        )
        messages = [{"role": "user", "content": (
            "Design a service arbitrage opportunity.\n"
            "Concept: %s\n"
            "Hypothesis: %s\n"
            "Focus: zero upfront cost, leverage free platforms, realistic margins.\n"
            "Output ONLY valid JSON."
        ) % (exp["name"], exp.get("hypothesis", ""))}]

        result = await call_for_task(
            "d3_content_gen", messages, system=system,
            max_tokens=2048, temperature=0.7,
        )

        if not result["ok"]:
            return {"ok": False, "error": "Arbitrage strategy failed"}

        content = self._extract_json(result["content"]) or {"raw": result["content"]}
        log.info("Experiment %d: arbitrage strategy generated", exp["id"])
        return {"ok": True, "content": content, "model": result.get("model", "")}

    # ── Outcomes ──────────────────────────────────────────────────────────────

    def record_outcome(self, experiment_id: int, metric_type: str,
                       value: float, notes: str = "") -> Optional[int]:
        now = datetime.utcnow().isoformat()
        conn = self._conn()
        try:
            cur = conn.execute(
                """INSERT INTO d3_experiment_outcomes
                   (experiment_id, metric_type, value, notes, recorded_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (experiment_id, metric_type, value, notes, now),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    # ── Portfolio Analysis ────────────────────────────────────────────────────

    async def analyze_portfolio(self) -> Dict[str, Any]:
        """AI-powered portfolio analysis across all experiments."""
        from core.worker_pool import call_for_task

        conn = self._conn()
        try:
            # Gather stats
            by_cat = {}
            for cat in CATEGORIES:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM d3_experiments WHERE category = ?", (cat,)
                ).fetchone()
                total = row["cnt"] if row else 0
                completed = conn.execute(
                    "SELECT COUNT(*) as cnt FROM d3_experiments WHERE category = ? AND status = 'completed'",
                    (cat,),
                ).fetchone()
                failed = conn.execute(
                    "SELECT COUNT(*) as cnt FROM d3_experiments WHERE category = ? AND status = 'failed'",
                    (cat,),
                ).fetchone()
                rev = conn.execute(
                    """SELECT COALESCE(SUM(o.value), 0) as total
                       FROM d3_experiment_outcomes o
                       JOIN d3_experiments e ON o.experiment_id = e.id
                       WHERE e.category = ? AND o.metric_type = 'revenue'""",
                    (cat,),
                ).fetchone()
                by_cat[cat] = {
                    "total": total,
                    "completed": completed["cnt"] if completed else 0,
                    "failed": failed["cnt"] if failed else 0,
                    "revenue": rev["total"] if rev else 0,
                }

            total_rev = sum(c["revenue"] for c in by_cat.values())
            total_exp = sum(c["total"] for c in by_cat.values())
        finally:
            conn.close()

        # Ask AI for analysis
        system = (
            "You are a portfolio analyst for revenue experiments. "
            "Provide actionable insights. Output JSON: "
            "{\"summary\": str, \"best_category\": str, \"worst_category\": str, "
            "\"recommendations\": [str], \"risk_assessment\": str}"
        )
        messages = [{"role": "user", "content": (
            "Analyze this experiment portfolio:\n%s\n"
            "Total experiments: %d, Total revenue: $%.2f\n"
            "Output ONLY valid JSON."
        ) % (json.dumps(by_cat, indent=2), total_exp, total_rev)}]

        result = await call_for_task(
            "d3_analysis", messages, system=system,
            max_tokens=8192, temperature=0.4,
        )

        analysis = {}
        if result["ok"]:
            analysis = self._extract_json(result["content"]) or {}

        return {
            "by_category": by_cat,
            "total_experiments": total_exp,
            "total_revenue": total_rev,
            "analysis": analysis,
        }

    # ── Self-Improvement ──────────────────────────────────────────────────────

    async def self_improve(self, trigger: str = "scheduled") -> Dict[str, Any]:
        """Analyze failures and trigger self-improvement builds if needed."""
        # Rate limit: 1 per hour
        now = time.time()
        if now - self._last_self_improve < 3600:
            return {"triggered": False, "reason": "Rate limited (1/hour)"}

        from core.worker_pool import call_for_task

        # Gather failure data
        failures = self.get_failure_patterns()
        if not failures:
            return {"triggered": False, "reason": "No failures to analyze"}

        system = (
            "You analyze experiment failure patterns and suggest code improvements.\n"
            "Output JSON: {\"needs_code_change\": bool, \"description\": str, "
            "\"target_files\": [str], \"improvement_type\": str (prompt|logic|config), "
            "\"priority\": 1-10}"
        )
        messages = [{"role": "user", "content": (
            "Analyze these experiment failure patterns and determine if "
            "code changes could improve results:\n\n%s\n\n"
            "Only suggest code changes if there's a clear pattern of failures "
            "that could be fixed programmatically.\n"
            "Output ONLY valid JSON."
        ) % json.dumps(failures[:20], indent=2)}]

        result = await call_for_task(
            "d3_self_improve", messages, system=system,
            max_tokens=8192, temperature=0.3,
        )

        if not result["ok"]:
            return {"triggered": False, "reason": "Analysis failed"}

        analysis = self._extract_json(result["content"])
        if not analysis or not analysis.get("needs_code_change"):
            self._last_self_improve = now
            return {
                "triggered": False,
                "reason": "No code change needed",
                "analysis": analysis,
            }

        # Trigger build via BuildPipeline
        from core.build_pipeline import get_build_pipeline
        pipeline = get_build_pipeline()
        desc = "[D3-SELF] %s" % analysis.get("description", "Self-improvement")[:200]
        build_id = pipeline.start_build(desc, started_by="d3_self_improve")

        self._last_self_improve = now
        self.record_outcome(0, "self_improve", 1.0, json.dumps({
            "build_id": build_id,
            "trigger": trigger,
            "analysis": analysis,
        }))

        log.info("Self-improvement triggered: build %s — %s", build_id, desc[:80])
        return {
            "triggered": True,
            "build_id": build_id,
            "analysis": analysis,
        }

    # ── Failure Database ──────────────────────────────────────────────────────

    def get_failure_patterns(self, category: str = "") -> List[Dict]:
        """Analyze failure patterns from experiment outcomes."""
        conn = self._conn()
        try:
            sql = """
                SELECT e.category, e.name, e.hypothesis, o.notes, o.recorded_at
                FROM d3_experiment_outcomes o
                JOIN d3_experiments e ON o.experiment_id = e.id
                WHERE e.status = 'failed'
            """
            params = []  # type: List[Any]
            if category:
                sql += " AND e.category = ?"
                params.append(category)
            sql += " ORDER BY o.recorded_at DESC LIMIT 100"
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Dashboard Data ────────────────────────────────────────────────────────

    def get_dashboard(self) -> Dict[str, Any]:
        """Full dashboard data for the Revenue Lab frontend."""
        conn = self._conn()
        try:
            # Counts
            active = conn.execute(
                "SELECT COUNT(*) as cnt FROM d3_experiments WHERE status = 'running'"
            ).fetchone()
            total_exp = conn.execute(
                "SELECT COUNT(*) as cnt FROM d3_experiments"
            ).fetchone()
            total_personas = conn.execute(
                "SELECT COUNT(*) as cnt FROM d3_personas WHERE status = 'active'"
            ).fetchone()
            total_rev = conn.execute(
                "SELECT COALESCE(SUM(value), 0) as total FROM d3_experiment_outcomes WHERE metric_type = 'revenue'"
            ).fetchone()

            # By category
            by_category = {}
            for cat in CATEGORIES:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM d3_experiments WHERE category = ?", (cat,)
                ).fetchone()
                running = conn.execute(
                    "SELECT COUNT(*) as cnt FROM d3_experiments WHERE category = ? AND status = 'running'",
                    (cat,),
                ).fetchone()
                by_category[cat] = {
                    "total": row["cnt"] if row else 0,
                    "running": running["cnt"] if running else 0,
                }

            # Recent outcomes
            recent = conn.execute(
                """SELECT o.*, e.name as experiment_name, e.category
                   FROM d3_experiment_outcomes o
                   JOIN d3_experiments e ON o.experiment_id = e.id
                   ORDER BY o.recorded_at DESC LIMIT 10"""
            ).fetchall()

            # Recent trends
            trends_today = conn.execute(
                "SELECT COUNT(*) as cnt FROM d3_trend_signals WHERE captured_at >= date('now')"
            ).fetchone()

            return {
                "active_experiments": active["cnt"] if active else 0,
                "total_experiments": total_exp["cnt"] if total_exp else 0,
                "total_personas": total_personas["cnt"] if total_personas else 0,
                "total_revenue": total_rev["total"] if total_rev else 0,
                "by_category": by_category,
                "recent_outcomes": [dict(r) for r in recent],
                "trends_today": trends_today["cnt"] if trends_today else 0,
                "entrepreneur_runtime": self.get_runtime_status(),
            }
        finally:
            conn.close()

    # ── Fleet Worker Entry Point ──────────────────────────────────────────────

    async def handle_task(self, task_type: str, input_data: Dict) -> Dict[str, Any]:
        """Entry point called by fleet workers for D3 tasks."""
        log.info("D3 handling task: %s", task_type)

        if task_type == "d3_persona_gen":
            pid = await self.create_persona(
                input_data.get("platform", "tiktok"),
                input_data.get("niche", "general"),
            )
            return {"ok": pid is not None, "persona_id": pid}

        elif task_type == "d3_trend_scan":
            signals = await self.scan_trends(input_data.get("sources"))
            return {"ok": True, "signals": len(signals)}

        elif task_type in ("d3_content_gen", "d3_seo_gen", "d3_product_gen"):
            exp_id = input_data.get("experiment_id")
            if exp_id:
                result = await self.run_experiment(exp_id)
                return result
            return {"ok": False, "error": "No experiment_id"}

        elif task_type == "d3_experiment_plan":
            # Plan a new experiment based on trends
            return await self._plan_experiment(input_data)

        elif task_type == "d3_analysis":
            result = await self.analyze_portfolio()
            return {"ok": True, "analysis": result}

        elif task_type == "d3_self_improve":
            result = await self.self_improve(trigger="fleet")
            return result

        elif task_type == "d3_entrepreneur_run":
            exp_id = input_data.get("experiment_id")
            if exp_id:
                return await self.run_experiment(exp_id)
            return {"ok": False, "error": "No experiment_id"}

        elif task_type == "d3_entrepreneur_dashboard":
            return {"ok": True, "dashboard": self.get_entrepreneur_dashboard()}

        elif task_type == "d3_entrepreneur_cycle":
            parallelism = int(input_data.get("parallelism", 4) or 4)
            limit = int(input_data.get("limit", 20) or 20)
            return await self.run_parallel_entrepreneur_cycles(parallelism=parallelism, limit=limit)

        elif task_type == "d3_entrepreneur_loop_start":
            interval_sec = int(input_data.get("interval_sec", 1800) or 1800)
            parallelism = int(input_data.get("parallelism", 4) or 4)
            return await self.start_autonomous_loop(interval_sec=interval_sec, parallelism=parallelism)

        elif task_type == "d3_entrepreneur_loop_stop":
            return await self.stop_autonomous_loop()

        elif task_type == "d3_entrepreneur_runtime":
            return {"ok": True, "runtime": self.get_runtime_status()}

        return {"ok": False, "error": "Unknown D3 task type: %s" % task_type}

    async def _plan_experiment(self, input_data: Dict) -> Dict[str, Any]:
        """Plan a new experiment based on trend data."""
        from core.worker_pool import call_for_task

        trends = self.get_trends(limit=10)
        trend_text = "\n".join(
            "- [%s] %s (score: %.0f)" % (t["source"], t["keyword"], t["score"])
            for t in trends
        ) or "No recent trends."

        system = (
            "You plan revenue experiments. Output JSON:\n"
            "{\"category\": str, \"name\": str, \"hypothesis\": str, "
            "\"config\": {\"platform\": str, \"content_type\": str}}"
        )
        messages = [{"role": "user", "content": (
            "Based on these trends, plan the best revenue experiment:\n%s\n"
            "Categories: %s\n"
            "Output ONLY valid JSON."
        ) % (trend_text, ", ".join(CATEGORIES))}]

        result = await call_for_task(
            "d3_experiment_plan", messages, system=system,
            max_tokens=1024, temperature=0.6,
        )

        if not result["ok"]:
            return {"ok": False, "error": "Planning failed"}

        plan = self._extract_json(result["content"])
        if not plan:
            return {"ok": False, "error": "Invalid plan output"}

        exp_id = self.create_experiment(
            category=plan.get("category", "viral_video"),
            name=plan.get("name", "Auto-planned experiment"),
            hypothesis=plan.get("hypothesis", ""),
            config=plan.get("config"),
        )

        return {"ok": True, "experiment_id": exp_id, "plan": plan}

    # ── Autonomous Entrepreneur ───────────────────────────────────────────────

    def _load_agent_memory(self, persona_id: str) -> Dict:
        """Load persona memory from file. Returns empty scaffold if missing."""
        _MEMORIES_DIR.mkdir(parents=True, exist_ok=True)
        path = _MEMORIES_DIR / ("%s.json" % persona_id)
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                pass
        return {
            "persona_id": persona_id,
            "learned_patterns": {},
            "account_health": {},
            "obstacles_overcome": [],
            "current_experiments": [],
            "revenue_to_date": {},
            "do_not_repeat": [],
            "actions_today": 0,
            "last_updated": "",
        }

    def _save_agent_memory(self, persona_id: str, memory: Dict) -> None:
        """Persist persona memory to file."""
        _MEMORIES_DIR.mkdir(parents=True, exist_ok=True)
        memory["last_updated"] = datetime.utcnow().isoformat()
        path = _MEMORIES_DIR / ("%s.json" % persona_id)
        path.write_text(json.dumps(memory, indent=2, ensure_ascii=False))

    def _log_journey(
        self,
        persona_id: str,
        action: str,
        outcome: str,
        revenue_delta: float = 0.0,
        obstacle: str = "",
        resolution: str = "",
        stream: str = "",
        action_id: str = "",
        phase: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Append one entry to the journey log JSONL file."""
        _JOURNEY_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        path = _JOURNEY_DIR / ("%s.jsonl" % date_str)
        entry = {
            "ts": datetime.utcnow().isoformat(),
            "persona_id": persona_id,
            "action": action,
            "outcome": outcome,
            "revenue_delta": revenue_delta,
            "obstacle": obstacle,
            "resolution": resolution,
            "stream": stream,
            "action_id": action_id,
            "phase": phase,
            "details": details or {},
        }
        with open(str(path), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    async def _run_sub_agent(
        self,
        task_name: str,
        system: str,
        prompt: str,
        max_tokens: int = 1024,
        temperature: float = 0.4,
    ) -> Dict[str, Any]:
        from core.worker_pool import call_for_task

        result = await call_for_task(
            task_name,
            [{"role": "user", "content": prompt}],
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if not result.get("ok"):
            return {"ok": False, "error": result.get("error") or result.get("content") or "sub-agent failed"}
        parsed = self._extract_json(result.get("content", ""))
        return {
            "ok": True,
            "data": parsed or {"raw": result.get("content", "")},
            "model": result.get("model", ""),
            "raw": result.get("content", ""),
        }

    @staticmethod
    def _coerce_float(v: Any, default: float = 0.0) -> float:
        try:
            return float(v)
        except Exception:
            return float(default)

    def _derive_revenue_delta(self, operator_data: Dict[str, Any], critic_data: Dict[str, Any]) -> float:
        keys = ("revenue_delta", "expected_revenue_delta", "projected_delta", "value_delta")
        for k in keys:
            if k in critic_data:
                return max(-5000.0, min(5000.0, self._coerce_float(critic_data.get(k), 0.0)))
            if k in operator_data:
                return max(-5000.0, min(5000.0, self._coerce_float(operator_data.get(k), 0.0)))
        confidence = self._coerce_float(critic_data.get("confidence"), 0.0)
        if confidence >= 0.85:
            return 5.0
        if confidence >= 0.65:
            return 2.0
        return 0.0

    async def _run_autonomous_entrepreneur(self, exp: Dict) -> Dict[str, Any]:
        """
        Autonomous entrepreneur cycle with sub-agents:
        planner -> operator -> critic -> memory update.
        """
        persona_id = str(exp.get("persona_id") or exp["id"])
        config = json.loads(exp.get("config") or "{}") if isinstance(exp.get("config"), str) else (exp.get("config") or {})
        stream = str(config.get("stream") or "youtube").strip().lower()
        action_id = uuid.uuid4().hex[:12]

        workspace = self._ensure_persona_workspace(persona_id, stream)
        memory = self._load_agent_memory(persona_id)
        memory_summary = json.dumps(
            {
                "learned_patterns": memory.get("learned_patterns", {}),
                "account_health": memory.get("account_health", {}),
                "do_not_repeat": memory.get("do_not_repeat", [])[-8:],
                "revenue_to_date": memory.get("revenue_to_date", {}),
                "current_experiments": memory.get("current_experiments", [])[-6:],
            },
            ensure_ascii=False,
        )

        planner = await self._run_sub_agent(
            "d3_entrepreneur_planner",
            (
                "You are an entrepreneurial planner. Focus on legal/compliant growth actions only. "
                "No stealth, evasion, impersonation, spam, or policy-bypass tactics. "
                "Output JSON: {\"priority_goal\": str, \"next_action\": str, \"why\": str, "
                "\"kpi\": str, \"risk_notes\": str}"
            ),
            (
                "Experiment: %s\nHypothesis: %s\nStream: %s\nWorkspace: %s\nMemory: %s\n"
                "Return the highest-value next action for the next cycle."
            )
            % (exp["name"], exp.get("hypothesis", ""), stream, workspace["profile_dir"], memory_summary),
            max_tokens=900,
            temperature=0.45,
        )
        if not planner.get("ok"):
            self._log_journey(
                persona_id,
                "planner_failed",
                "error",
                stream=stream,
                action_id=action_id,
                phase="planner",
                obstacle=str(planner.get("error", "planner failure")),
            )
            return {"ok": False, "error": "planner_failed", "details": planner}

        operator = await self._run_sub_agent(
            "d3_entrepreneur_operator",
            (
                "You are an execution operator. Convert plan into one concrete, sandbox-safe action package. "
                "Do not perform outbound posting/sending; produce a prepared action only. "
                "Output JSON: {\"action_name\": str, \"step_list\": [str], \"asset_or_text\": str, "
                "\"expected_revenue_delta\": number, \"requires_approval\": bool}"
            ),
            (
                "Plan: %s\nMemory: %s\nGenerate one action package now."
                % (json.dumps(planner["data"], ensure_ascii=False), memory_summary)
            ),
            max_tokens=1200,
            temperature=0.5,
        )
        if not operator.get("ok"):
            self._log_journey(
                persona_id,
                "operator_failed",
                "error",
                stream=stream,
                action_id=action_id,
                phase="operator",
                obstacle=str(operator.get("error", "operator failure")),
            )
            return {"ok": False, "error": "operator_failed", "details": operator}

        critic = await self._run_sub_agent(
            "d3_entrepreneur_critic",
            (
                "You are a critic for safety + ROI. Reject risky actions and require approval where needed. "
                "Output JSON: {\"approve_for_execution\": bool, \"risk_level\": \"low|medium|high\", "
                "\"confidence\": number, \"expected_revenue_delta\": number, \"learning\": str, "
                "\"do_not_repeat\": str, \"notes\": str}"
            ),
            (
                "Plan: %s\nAction package: %s\n"
                "Apply strict safety and ROI judgment."
                % (
                    json.dumps(planner["data"], ensure_ascii=False),
                    json.dumps(operator["data"], ensure_ascii=False),
                )
            ),
            max_tokens=1000,
            temperature=0.3,
        )
        if not critic.get("ok"):
            self._log_journey(
                persona_id,
                "critic_failed",
                "error",
                stream=stream,
                action_id=action_id,
                phase="critic",
                obstacle=str(critic.get("error", "critic failure")),
            )
            return {"ok": False, "error": "critic_failed", "details": critic}

        critic_data = critic.get("data", {}) if isinstance(critic.get("data"), dict) else {}
        operator_data = operator.get("data", {}) if isinstance(operator.get("data"), dict) else {}
        action_name = str(operator_data.get("action_name") or planner["data"].get("next_action") or "prepare_action").strip()
        approved = bool(critic_data.get("approve_for_execution"))
        risk_level = str(critic_data.get("risk_level") or "medium").lower()
        if risk_level == "high":
            approved = False
        if bool(operator_data.get("requires_approval", False)):
            approved = False

        revenue_delta = self._derive_revenue_delta(operator_data, critic_data) if approved else 0.0
        outcome = "prepared_action" if approved else "queued_for_human_approval"
        obstacle = "" if approved else "approval_required_or_high_risk"
        learning = str(critic_data.get("learning") or "").strip()
        do_not_repeat = str(critic_data.get("do_not_repeat") or "").strip()

        # Memory update
        learned = memory.setdefault("learned_patterns", {})
        stream_patterns = learned.setdefault(stream, {})
        stream_patterns["last_action_name"] = action_name
        stream_patterns["last_risk_level"] = risk_level
        if learning:
            stream_patterns["last_learning"] = learning
        current_exp = memory.setdefault("current_experiments", [])
        current_exp.append(
            {
                "ts": datetime.utcnow().isoformat(),
                "action_id": action_id,
                "action_name": action_name,
                "stream": stream,
                "approved": approved,
                "expected_revenue_delta": revenue_delta,
            }
        )
        memory["current_experiments"] = current_exp[-25:]
        revenue_to_date = memory.setdefault("revenue_to_date", {})
        revenue_to_date[stream] = round(self._coerce_float(revenue_to_date.get(stream), 0.0) + float(revenue_delta), 2)
        if do_not_repeat:
            dnr = memory.setdefault("do_not_repeat", [])
            if do_not_repeat not in dnr:
                dnr.append(do_not_repeat)
            memory["do_not_repeat"] = dnr[-50:]
        memory["actions_today"] = int(memory.get("actions_today", 0) or 0) + 1
        self._save_agent_memory(persona_id, memory)

        self._record_revenue_event(
            persona_id=persona_id,
            experiment_id=int(exp.get("id") or 0),
            stream=stream,
            action_id=action_id,
            action_name=action_name,
            delta=revenue_delta,
            notes=str(critic_data.get("notes") or ""),
        )

        self._log_journey(
            persona_id=persona_id,
            action=action_name,
            outcome=outcome,
            revenue_delta=revenue_delta,
            obstacle=obstacle,
            resolution=learning,
            stream=stream,
            action_id=action_id,
            phase="planner_operator_critic",
            details={
                "planner": planner.get("data", {}),
                "operator": operator_data,
                "critic": critic_data,
                "workspace": workspace,
            },
        )

        log.info(
            "Entrepreneur cycle persona=%s stream=%s action=%s approved=%s delta=%.2f",
            persona_id, stream, action_name, approved, revenue_delta,
        )
        return {
            "ok": True,
            "stream": stream,
            "action_id": action_id,
            "approved": approved,
            "risk_level": risk_level,
            "revenue_delta": revenue_delta,
            "content": {
                "planner": planner.get("data", {}),
                "operator": operator_data,
                "critic": critic_data,
                "outcome": outcome,
            },
            "models": {
                "planner": planner.get("model", ""),
                "operator": operator.get("model", ""),
                "critic": critic.get("model", ""),
            },
        }

    def _list_autonomous_experiments(self, statuses: Optional[List[str]] = None, limit: int = 50) -> List[Dict[str, Any]]:
        statuses = statuses or ["running", "queued"]
        conn = self._conn()
        try:
            placeholders = ",".join(["?"] * len(statuses))
            rows = conn.execute(
                (
                    "SELECT * FROM d3_experiments WHERE category = 'autonomous_entrepreneur' "
                    "AND status IN (%s) ORDER BY id DESC LIMIT ?"
                )
                % placeholders,
                [*statuses, max(1, min(int(limit), 200))],
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["config"] = json.loads(d.get("config") or "{}")
                except Exception:
                    d["config"] = {}
                out.append(d)
            return out
        finally:
            conn.close()

    async def run_parallel_entrepreneur_cycles(self, parallelism: int = 4, limit: int = 20) -> Dict[str, Any]:
        exps = self._list_autonomous_experiments(statuses=["running", "queued"], limit=limit)
        if not exps:
            summary = {"ok": True, "processed": 0, "succeeded": 0, "failed": 0, "revenue_delta": 0.0, "results": []}
            self._loop_last_cycle = datetime.utcnow().isoformat()
            self._loop_last_summary = summary
            self._save_runtime_state()
            return summary

        # Promote queued -> running.
        queued_ids = [int(e["id"]) for e in exps if str(e.get("status")) == "queued"]
        if queued_ids:
            now = datetime.utcnow().isoformat()
            conn = self._conn()
            try:
                conn.executemany(
                    "UPDATE d3_experiments SET status='running', started_at=? WHERE id=?",
                    [(now, qid) for qid in queued_ids],
                )
                conn.commit()
            finally:
                conn.close()

        sem = asyncio.Semaphore(max(1, min(int(parallelism), 64)))

        async def _runner(exp: Dict[str, Any]) -> Dict[str, Any]:
            async with sem:
                try:
                    out = await self._run_autonomous_entrepreneur(exp)
                    if out.get("ok"):
                        self.record_outcome(exp["id"], "content", 1.0, json.dumps(out.get("content", {}), ensure_ascii=False))
                        if float(out.get("revenue_delta", 0.0)) != 0.0:
                            self.record_outcome(exp["id"], "revenue", float(out.get("revenue_delta", 0.0)), "autonomous_cycle")
                    return {"experiment_id": exp["id"], **out}
                except Exception as e:
                    return {"experiment_id": exp["id"], "ok": False, "error": str(e)}

        results = await asyncio.gather(*[_runner(e) for e in exps], return_exceptions=False)
        succeeded = len([r for r in results if r.get("ok")])
        failed = len(results) - succeeded
        revenue_delta = round(sum(self._coerce_float(r.get("revenue_delta"), 0.0) for r in results if isinstance(r, dict)), 2)
        summary = {
            "ok": True,
            "processed": len(results),
            "succeeded": succeeded,
            "failed": failed,
            "revenue_delta": revenue_delta,
            "parallelism": max(1, min(int(parallelism), 64)),
            "results": results,
        }
        self._loop_last_cycle = datetime.utcnow().isoformat()
        self._loop_last_summary = summary
        self._save_runtime_state()
        return summary

    async def _autonomous_loop_worker(self) -> None:
        try:
            while self._loop_running:
                try:
                    await self.run_parallel_entrepreneur_cycles(parallelism=self._loop_parallelism, limit=50)
                except Exception as e:
                    log.error("Autonomous loop cycle failed: %s", e)
                await asyncio.sleep(max(30, int(self._loop_interval_sec)))
        except asyncio.CancelledError:
            return

    async def start_autonomous_loop(self, interval_sec: int = 1800, parallelism: int = 4) -> Dict[str, Any]:
        async with self._runtime_lock:
            if self._loop_running and self._loop_task and not self._loop_task.done():
                return {"ok": True, "status": "already_running", "runtime": self.get_runtime_status()}
            self._loop_interval_sec = max(30, int(interval_sec))
            self._loop_parallelism = max(1, min(int(parallelism), 64))
            self._loop_running = True
            self._loop_task = asyncio.create_task(self._autonomous_loop_worker())
            self._save_runtime_state()
            return {"ok": True, "status": "started", "runtime": self.get_runtime_status()}

    async def stop_autonomous_loop(self) -> Dict[str, Any]:
        async with self._runtime_lock:
            self._loop_running = False
            if self._loop_task and not self._loop_task.done():
                self._loop_task.cancel()
                try:
                    await self._loop_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            self._save_runtime_state()
            return {"ok": True, "status": "stopped", "runtime": self.get_runtime_status()}

    def get_runtime_status(self) -> Dict[str, Any]:
        persisted = self._runtime_state()
        return {
            "running": bool(self._loop_running) or bool(persisted.get("running")),
            "interval_sec": int(self._loop_interval_sec),
            "parallelism": int(self._loop_parallelism),
            "last_cycle": self._loop_last_cycle or str(persisted.get("last_cycle") or ""),
            "last_summary": self._loop_last_summary or dict(persisted.get("last_summary") or {}),
            "task_alive": bool(self._loop_task and not self._loop_task.done()),
            "updated_at": datetime.utcnow().isoformat(),
        }

    def get_entrepreneur_dashboard(self) -> Dict[str, Any]:
        """Dashboard for all autonomous entrepreneur experiments — journey, revenue, obstacles."""
        _JOURNEY_DIR.mkdir(parents=True, exist_ok=True)
        _MEMORIES_DIR.mkdir(parents=True, exist_ok=True)
        _BROWSERS_DIR.mkdir(parents=True, exist_ok=True)

        # Load all persona memories
        personas = {}
        for mem_file in sorted(_MEMORIES_DIR.glob("*.json")):
            try:
                mem = json.loads(mem_file.read_text())
                personas[mem_file.stem] = mem
            except Exception:
                pass

        # Load today's journey log
        today = datetime.utcnow().strftime("%Y-%m-%d")
        actions_today = []
        obstacles_today = []
        revenue_today = 0.0
        journal_path = _JOURNEY_DIR / ("%s.jsonl" % today)
        if journal_path.exists():
            for line in journal_path.read_text().splitlines():
                try:
                    entry = json.loads(line)
                    actions_today.append(entry)
                    revenue_today += entry.get("revenue_delta", 0.0)
                    if entry.get("obstacle"):
                        obstacles_today.append(entry)
                except Exception:
                    pass

        # Load last 10 obstacles across all logs
        recent_obstacles = []
        for log_file in sorted(_JOURNEY_DIR.glob("*.jsonl"), reverse=True)[:7]:
            for line in log_file.read_text().splitlines():
                try:
                    entry = json.loads(line)
                    if entry.get("obstacle"):
                        recent_obstacles.append(entry)
                except Exception:
                    pass
            if len(recent_obstacles) >= 10:
                break
        recent_obstacles = recent_obstacles[:10]

        # Total revenue across all memories
        total_revenue = sum(
            sum(v for v in mem.get("revenue_to_date", {}).values() if isinstance(v, (int, float)))
            for mem in personas.values()
        )
        revenue_summary = self._get_revenue_summary()

        # Active entrepreneur experiments from DB
        conn = self._conn()
        try:
            active_exps = conn.execute(
                "SELECT id, name, category, status, created_at FROM d3_experiments "
                "WHERE category = 'autonomous_entrepreneur' ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
            active_exps = [dict(r) for r in active_exps]
        except Exception:
            active_exps = []
        finally:
            conn.close()

        browser_profiles = []
        for p in sorted(_BROWSERS_DIR.glob("*")):
            if p.is_dir():
                browser_profiles.append({"name": p.name, "path": str(p)})

        return {
            "total_revenue": total_revenue,
            "tracked_revenue_total": revenue_summary.get("total", 0.0),
            "modeled_revenue_total": revenue_summary.get("total", 0.0),
            "memory_revenue_total": total_revenue,
            "verified_revenue_total": 0.0,
            "revenue_verification": "modeled_internal_expected_delta",
            "revenue_today": revenue_today,
            "actions_today": len(actions_today),
            "active_experiments": [e for e in active_exps if e["status"] == "running"],
            "all_experiments": active_exps,
            "personas": personas,
            "recent_obstacles": recent_obstacles,
            "recent_actions": actions_today[-20:],
            "runtime": self.get_runtime_status(),
            "browser_profiles": browser_profiles,
            "revenue_events": revenue_summary.get("recent_events", []),
            "revenue_by_stream": revenue_summary.get("by_stream", []),
        }

    def get_journey_entries(self, persona_id: str = "", days: int = 7, limit: int = 200) -> List[Dict[str, Any]]:
        _JOURNEY_DIR.mkdir(parents=True, exist_ok=True)
        entries: List[Dict[str, Any]] = []
        files = sorted(_JOURNEY_DIR.glob("*.jsonl"), reverse=True)[: max(1, min(int(days), 30))]
        for log_file in files:
            try:
                for line in log_file.read_text().splitlines():
                    row = json.loads(line)
                    if persona_id and str(row.get("persona_id")) != str(persona_id):
                        continue
                    entries.append(row)
                    if len(entries) >= max(1, min(int(limit), 2000)):
                        return entries
            except Exception:
                continue
        return entries

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_persona(self, persona_id: Optional[int]) -> Optional[Dict]:
        if not persona_id:
            return None
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM d3_personas WHERE id = ?", (persona_id,)
            ).fetchone()
            if row:
                d = dict(row)
                d["personality"] = json.loads(d.get("personality") or "{}")
                d["metrics"] = json.loads(d.get("metrics") or "{}")
                return d
            return None
        finally:
            conn.close()

    @staticmethod
    def _extract_json(text: str) -> Optional[Dict]:
        """Extract JSON object from AI response."""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end + 1])
                except (json.JSONDecodeError, ValueError):
                    pass
        return None

    @staticmethod
    def _extract_json_array(text: str) -> List[Dict]:
        """Extract JSON array from AI response."""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        try:
            result = json.loads(text)
            if isinstance(result, list):
                return result
        except (json.JSONDecodeError, ValueError):
            start = text.find("[")
            end = text.rfind("]")
            if start >= 0 and end > start:
                try:
                    result = json.loads(text[start:end + 1])
                    if isinstance(result, list):
                        return result
                except (json.JSONDecodeError, ValueError):
                    pass
        return []


# ── Singleton ─────────────────────────────────────────────────────────────────

_d3 = None  # type: Optional[DivisionThree]


def get_division_three() -> DivisionThree:
    global _d3
    if _d3 is None:
        _d3 = DivisionThree()
    return _d3
