"""
Beta Research Engine
--------------------

Safe, read-only lead intelligence pipeline for overnight experimentation.

Key guarantees:
1) Never sends outbound messages/emails/calls
2) Never logs in to social platforms (Facebook/Instagram/etc.)
3) Stores all data in a separate database (~/.nexus/beta_research.db)
4) Produces scored, validated, manually reviewable leads only
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

try:
    import httpx
except Exception:  # pragma: no cover - optional dependency
    httpx = None

log = logging.getLogger("beta_research")

DB_PATH = Path.home() / ".nexus" / "beta_research.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"

STAGE_ORDER = [
    "public_signals",
    "collection",
    "extraction",
    "scoring",
    "validation",
    "decision",
    "launch",
    "feedback",
    "reinforcement",
]

BLOCKED_PLATFORM_LOGINS = [
    "facebook.com",
    "instagram.com",
    "messenger.com",
    "x.com",
    "twitter.com",
    "linkedin.com",
    "tiktok.com",
]

SAFE_PUBLIC_SOURCES = ["google_maps", "google_search", "yelp", "public_website_metadata"]

REGION_BUCKETS = {
    "sfv_core": {
        "san fernando",
        "pacoima",
        "sylmar",
        "van nuys",
        "north hollywood",
        "panorama city",
        "sun valley",
        "arleta",
        "granada hills",
        "northridge",
        "reseda",
        "encino",
        "tarzana",
        "winnetka",
        "canoga park",
        "woodland hills",
        "sherman oaks",
        "studio city",
    },
    "nearby_core": {
        "burbank",
        "glendale",
        "pasadena",
        "calabasas",
        "hidden hills",
        "newhall",
        "santa clarita",
        "valencia",
        "canyon country",
    },
    "premium_outer": {
        "malibu",
        "thousand oaks",
        "westlake village",
        "agoura hills",
        "simi valley",
    },
}

BETA_CATEGORY_PLAN = [
    {
        "id": "wedding_venue",
        "label": "Wedding Venues",
        "queries": ["outdoor wedding venue", "estate wedding venue", "garden wedding venue"],
    },
    {
        "id": "ranch_vineyard",
        "label": "Ranch / Vineyard Venues",
        "queries": ["ranch wedding venue", "vineyard wedding venue", "barn wedding venue"],
    },
    {
        "id": "private_event_venue",
        "label": "Private Event Venues",
        "queries": ["private event venue", "outdoor event venue", "tented reception venue"],
    },
    {
        "id": "venue_partners",
        "label": "Venue Partner Signals",
        "queries": ["venue preferred vendors", "venue vendor list", "outside vendors allowed wedding venue"],
    },
]

LOCATION_PRIORITY = [
    "San Fernando, CA",
    "Burbank, CA",
    "Glendale, CA",
    "Calabasas, CA",
    "Hidden Hills, CA",
    "Pasadena, CA",
    "Santa Clarita, CA",
    "Newhall, CA",
    "Thousand Oaks, CA",
    "Agoura Hills, CA",
    "Westlake Village, CA",
]

KEYWORD_GROUPS = {
    "outdoor": {
        "outdoor",
        "garden",
        "ranch",
        "vineyard",
        "estate",
        "barn",
        "lawn",
        "courtyard",
        "farm",
        "tented",
        "tent",
    },
    "vendor_list": {
        "preferred vendor",
        "vendor list",
        "recommended vendors",
        "outside vendors",
        "bring your own vendors",
    },
    "premium": {
        "luxury",
        "estate",
        "country club",
        "vineyard",
        "private estate",
        "resort",
    },
    "venue": {
        "venue",
        "wedding",
        "events",
        "banquet",
        "celebration",
        "reception",
    },
}

DEFAULT_POLICY = {
    "min_score_to_keep": 50,
    "min_score_tier_a": 80,
    "min_score_tier_b": 65,
    "penalty_website_unreachable": 18,
    "penalty_no_contact_path": 15,
    "penalty_social_only_website": 10,
    "penalty_low_venue_confidence": 18,
    "penalty_ai_only_source": 6,
    "bonus_multi_source_validation": 6,
    "bonus_vendor_list_proof": 5,
}

POSITIVE_VENUE_TERMS = {
    "venue",
    "wedding",
    "events",
    "event",
    "banquet",
    "estate",
    "ranch",
    "vineyard",
    "garden",
    "club",
    "resort",
    "ceremony",
    "reception",
}

NEGATIVE_VENUE_TERMS = {
    "market",
    "nursery",
    "landscaping",
    "hardware",
    "auto",
    "repair",
    "school",
    "clinic",
    "hospital",
    "grocery",
    "wholesale",
    "warehouse",
}

SOCIAL_ONLY_DOMAINS = {
    "facebook.com",
    "instagram.com",
    "m.facebook.com",
    "yelp.com",
    "maps.google.com",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _norm_phone(phone: str) -> str:
    digits = re.sub(r"[^\d]", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def _safe_str(v: Any) -> str:
    return (str(v or "")).strip()


def _norm_domain(url: str) -> str:
    raw = _safe_str(url).lower()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    try:
        host = (urlparse(raw).hostname or "").lower()
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def _norm_city(city: str) -> str:
    return _safe_str(city).split(",")[0].strip().lower()


def _region_for_city(city: str) -> str:
    c = _norm_city(city)
    for bucket, values in REGION_BUCKETS.items():
        if c in values:
            return bucket
    return "outer"


def _keyword_hits(text: str, group: str) -> int:
    lowered = _safe_str(text).lower()
    if not lowered:
        return 0
    return sum(1 for kw in KEYWORD_GROUPS[group] if kw in lowered)


def _tier(score: float, policy: Dict[str, Any]) -> str:
    if score >= float(policy.get("min_score_tier_a", 80)):
        return "A"
    if score >= float(policy.get("min_score_tier_b", 65)):
        return "B"
    if score >= float(policy.get("min_score_to_keep", 50)):
        return "C"
    return "BACKLOG"


def _best_pitch_angle(data: Dict[str, Any]) -> str:
    if data.get("preferred_vendor_bonus", 0) > 0 or data.get("vendor_referral_potential_score", 0) >= 12:
        return "preferred_vendor"
    if data.get("backup_vendor_fit_score", 0) >= 4 or data.get("restroom_need_likelihood_score", 0) >= 11:
        return "backup_vendor"
    if data.get("outdoor_event_fit_score", 0) >= 14:
        return "overflow_peak_date"
    return "referral_partner"


def _first_name_from_name(name: str) -> str:
    parts = [p for p in re.split(r"\s+", _safe_str(name)) if p]
    if not parts:
        return "there"
    first = parts[0].strip(" ,.-_")
    return first if first else "there"


class BetaResearchEngine:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._running = False
        self._current_run_id: Optional[int] = None
        self._last_error = ""
        self.init_tables()

    # ── DB ──────────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(DB_PATH), timeout=20)
        c.row_factory = sqlite3.Row
        return c

    def init_tables(self):
        conn = self._conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS beta_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mode TEXT DEFAULT 'manual',
                    status TEXT DEFAULT 'running',
                    started_at TEXT DEFAULT (datetime('now')),
                    ended_at TEXT DEFAULT '',
                    duration_ms INTEGER DEFAULT 0,
                    summary_json TEXT DEFAULT '{}',
                    error TEXT DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS beta_stage_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    stage_name TEXT NOT NULL,
                    input_count INTEGER DEFAULT 0,
                    output_count INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'ok',
                    duration_ms INTEGER DEFAULT 0,
                    notes TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS beta_leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    dedup_key TEXT NOT NULL,
                    venue_name TEXT DEFAULT '',
                    website TEXT DEFAULT '',
                    city TEXT DEFAULT '',
                    region TEXT DEFAULT '',
                    venue_type TEXT DEFAULT '',
                    category TEXT DEFAULT '',
                    signal_source TEXT DEFAULT '',
                    contact_name TEXT DEFAULT '',
                    contact_email TEXT DEFAULT '',
                    contact_phone TEXT DEFAULT '',
                    contact_path TEXT DEFAULT '',
                    score REAL DEFAULT 0,
                    tier TEXT DEFAULT 'C',
                    best_pitch_angle TEXT DEFAULT 'referral_partner',
                    status TEXT DEFAULT 'queued_review',
                    validation_status TEXT DEFAULT 'inferred',
                    validation_flags TEXT DEFAULT '[]',
                    proof_snippet_1 TEXT DEFAULT '',
                    proof_snippet_2 TEXT DEFAULT '',
                    proof_snippet_3 TEXT DEFAULT '',
                    personalization_seed_1 TEXT DEFAULT '',
                    personalization_seed_2 TEXT DEFAULT '',
                    personalization_seed_3 TEXT DEFAULT '',
                    score_breakdown_json TEXT DEFAULT '{}',
                    raw_payload TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(run_id, dedup_key)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS beta_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER,
                    stage_name TEXT DEFAULT '',
                    level TEXT DEFAULT 'info',
                    message TEXT DEFAULT '',
                    payload TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS beta_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lead_id INTEGER NOT NULL,
                    verdict TEXT NOT NULL,
                    notes TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS beta_policy (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_beta_runs_started ON beta_runs(started_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_beta_leads_run ON beta_leads(run_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_beta_leads_score ON beta_leads(score DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_beta_stage_run ON beta_stage_metrics(run_id, stage_name)")
            conn.commit()
            self._ensure_policy(conn)
        finally:
            conn.close()

    def _ensure_policy(self, conn: sqlite3.Connection):
        row = conn.execute("SELECT value_json FROM beta_policy WHERE key='weights'").fetchone()
        if row:
            return
        conn.execute(
            "INSERT INTO beta_policy (key, value_json, updated_at) VALUES ('weights', ?, ?)",
            (json.dumps(DEFAULT_POLICY), _now()),
        )
        conn.commit()

    def _load_policy(self) -> Dict[str, Any]:
        conn = self._conn()
        try:
            row = conn.execute("SELECT value_json FROM beta_policy WHERE key='weights'").fetchone()
            if not row:
                return dict(DEFAULT_POLICY)
            data = json.loads(row["value_json"] or "{}")
            merged = dict(DEFAULT_POLICY)
            merged.update(data)
            return merged
        except Exception:
            return dict(DEFAULT_POLICY)
        finally:
            conn.close()

    def _load_runtime_config(self) -> Dict[str, Any]:
        if CONFIG_PATH.exists():
            try:
                return json.loads(CONFIG_PATH.read_text())
            except Exception:
                return {}
        return {}

    # ── Status / Read APIs ───────────────────────────────────────────────

    def safety_profile(self) -> Dict[str, Any]:
        return {
            "mode": "research_only",
            "outbound_disabled": True,
            "platform_logins_disabled": True,
            "blocked_platform_logins": BLOCKED_PLATFORM_LOGINS,
            "safe_sources": SAFE_PUBLIC_SOURCES,
            "writes_to_production_db": False,
            "separate_beta_db": str(DB_PATH),
            "launch_behavior": "queue_only_no_send",
        }

    def provider_snapshot(self) -> Dict[str, Any]:
        cfg = self._load_runtime_config()

        key_names = [
            "google_places_api_key",
            "yelp_api_key",
            "gemini_key",
            "openai_key",
            "anthropic_key",
            "moonshot_key",
            "groq_api_key",
            "zai_api_key",
        ]
        providers = []
        for key in key_names:
            value = _safe_str(cfg.get(key, ""))
            providers.append(
                {
                    "key_name": key,
                    "configured": bool(value),
                    # Never expose key fragments in UI/API responses.
                    "preview": "configured" if value else "",
                    "used_for_beta": key in {"google_places_api_key", "yelp_api_key"},
                }
            )
        return {"providers": providers}

    def get_status(self) -> Dict[str, Any]:
        conn = self._conn()
        try:
            last_run = conn.execute(
                "SELECT * FROM beta_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            total_runs = conn.execute("SELECT COUNT(*) AS c FROM beta_runs").fetchone()["c"]
            total_leads = conn.execute("SELECT COUNT(*) AS c FROM beta_leads").fetchone()["c"]
            tier_rows = conn.execute(
                "SELECT tier, COUNT(*) AS c FROM beta_leads GROUP BY tier"
            ).fetchall()
            tier_counts = {r["tier"]: r["c"] for r in tier_rows}
            queue_ready = conn.execute(
                "SELECT COUNT(*) AS c FROM beta_leads WHERE status='queued_review'"
            ).fetchone()["c"]
        finally:
            conn.close()

        return {
            "running": self._running,
            "current_run_id": self._current_run_id,
            "total_runs": total_runs,
            "total_beta_leads": total_leads,
            "queued_review": queue_ready,
            "tier_counts": tier_counts,
            "last_error": self._last_error,
            "safety": self.safety_profile(),
            "policy": self._load_policy(),
            "last_run": dict(last_run) if last_run else None,
            "providers": self.provider_snapshot()["providers"],
            "latest_diagnostics": self.latest_diagnostics(),
        }

    def list_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM beta_runs ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def list_stage_metrics(self, run_id: Optional[int] = None) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            if run_id is None:
                row = conn.execute("SELECT id FROM beta_runs ORDER BY id DESC LIMIT 1").fetchone()
                if not row:
                    return []
                run_id = int(row["id"])
            rows = conn.execute(
                """
                SELECT run_id, stage_name, input_count, output_count, status, duration_ms, notes, created_at
                FROM beta_stage_metrics
                WHERE run_id = ?
                ORDER BY id ASC
                """,
                (int(run_id),),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def list_events(
        self,
        run_id: Optional[int] = None,
        limit: int = 200,
        stage_name: str = "",
        level: str = "",
    ) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            if run_id is None:
                row = conn.execute("SELECT id FROM beta_runs ORDER BY id DESC LIMIT 1").fetchone()
                if not row:
                    return []
                run_id = int(row["id"])

            where = ["run_id = ?"]
            params: List[Any] = [int(run_id)]
            if stage_name:
                where.append("stage_name = ?")
                params.append(_safe_str(stage_name))
            if level:
                where.append("level = ?")
                params.append(_safe_str(level))
            params.append(max(1, min(int(limit or 200), 1000)))

            rows = conn.execute(
                """
                SELECT id, run_id, stage_name, level, message, payload, created_at
                FROM beta_events
                WHERE %s
                ORDER BY id DESC
                LIMIT ?
                """
                % (" AND ".join(where)),
                tuple(params),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                payload_raw = d.get("payload", "")
                try:
                    d["payload"] = json.loads(payload_raw or "{}")
                except Exception:
                    d["payload"] = {}
                out.append(d)
            return out
        finally:
            conn.close()

    def latest_diagnostics(self) -> Dict[str, Any]:
        run_rows = self.list_runs(limit=1)
        if not run_rows:
            return {"summary": "No runs yet", "reason": "Run one safe cycle to generate diagnostics."}
        run = run_rows[0]
        run_id = int(run.get("id", 0) or 0)
        stages = self.list_stage_metrics(run_id=run_id)
        events = self.list_events(run_id=run_id, limit=120)

        cfg = self._load_runtime_config()
        google_key_set = bool(_safe_str(cfg.get("google_places_api_key", "")))
        yelp_key_set = bool(_safe_str(cfg.get("yelp_api_key", "")))

        drop_stage = ""
        for s in stages:
            stage_name = _safe_str(s.get("stage_name"))
            if stage_name in {"feedback", "reinforcement"}:
                continue
            if int(s.get("input_count", 0)) > 0 and int(s.get("output_count", 0)) == 0:
                drop_stage = stage_name
                break

        reason = ""
        if drop_stage:
            # events are DESC by id
            for e in events:
                if e.get("stage_name") == drop_stage and e.get("level") in {"warn", "error"}:
                    reason = _safe_str(e.get("message"))
                    break

        if not reason:
            if drop_stage == "collection":
                if not google_key_set and not yelp_key_set:
                    reason = "Collection relied on scrape fallback only (no Google Places/Yelp API keys set), and returned zero rows."
                elif not google_key_set:
                    reason = "Google Places key missing; collection used fallback scraping and returned zero rows."
                else:
                    reason = "Collection produced zero rows for current query/location seeds."
            elif drop_stage:
                reason = f"{drop_stage} produced zero output."
            else:
                reason = "Run completed without a hard drop-off."

        return {
            "run_id": run_id,
            "drop_stage": drop_stage or "",
            "reason": reason,
            "google_places_key_configured": google_key_set,
            "yelp_key_configured": yelp_key_set,
            "summary": f"Latest run #{run_id}: {reason}",
        }

    def list_leads(
        self,
        run_id: Optional[int] = None,
        limit: int = 100,
        tier: Optional[str] = None,
        min_score: float = 0,
    ) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            params: List[Any] = []
            where = ["score >= ?"]
            params.append(float(min_score))
            if run_id is not None:
                where.append("run_id = ?")
                params.append(int(run_id))
            if tier:
                where.append("tier = ?")
                params.append(str(tier).upper())
            query = (
                "SELECT * FROM beta_leads WHERE %s ORDER BY score DESC, id DESC LIMIT ?"
                % (" AND ".join(where))
            )
            params.append(max(1, min(limit, 500)))
            rows = conn.execute(query, tuple(params)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def save_feedback(self, lead_id: int, verdict: str, notes: str = "") -> Dict[str, Any]:
        verdict_clean = _safe_str(verdict).lower()
        if verdict_clean not in {"good", "bad", "needs_review"}:
            return {"ok": False, "error": "Invalid verdict"}
        conn = self._conn()
        try:
            row = conn.execute("SELECT id FROM beta_leads WHERE id = ?", (int(lead_id),)).fetchone()
            if not row:
                return {"ok": False, "error": "Lead not found"}
            conn.execute(
                "INSERT INTO beta_feedback (lead_id, verdict, notes, created_at) VALUES (?, ?, ?, ?)",
                (int(lead_id), verdict_clean, _safe_str(notes)[:500], _now()),
            )
            status = "queued_review"
            if verdict_clean == "good":
                status = "approved_for_manual_outreach"
            elif verdict_clean == "bad":
                status = "rejected"
            conn.execute("UPDATE beta_leads SET status=? WHERE id=?", (status, int(lead_id)))
            conn.commit()
            return {"ok": True, "lead_id": int(lead_id), "status": status}
        finally:
            conn.close()

    # ── Pipeline Run ──────────────────────────────────────────────────────

    async def run_cycle(
        self,
        *,
        max_categories: int = 2,
        max_locations_per_category: int = 2,
        max_per_signal: int = 8,
        parallel_signals: int = 3,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        if self._running:
            return {"ok": False, "error": "Beta research is already running", "run_id": self._current_run_id}

        async with self._lock:
            if self._running:
                return {"ok": False, "error": "Beta research is already running", "run_id": self._current_run_id}

            self._running = True
            self._last_error = ""
            run_id = self._create_run(mode="manual")
            self._current_run_id = run_id
            started = time.time()
            policy = self._load_policy()

            try:
                result = await self._run_pipeline(
                    run_id=run_id,
                    policy=policy,
                    max_categories=max_categories,
                    max_locations_per_category=max_locations_per_category,
                    max_per_signal=max_per_signal,
                    parallel_signals=parallel_signals,
                    dry_run=dry_run,
                )
                duration_ms = int((time.time() - started) * 1000)
                self._finish_run(run_id, status="completed", duration_ms=duration_ms, summary=result)
                result.update({"ok": True, "run_id": run_id, "duration_ms": duration_ms})
                return result
            except Exception as e:
                duration_ms = int((time.time() - started) * 1000)
                self._last_error = str(e)
                self._log_event(run_id, "pipeline", "error", "Run failed", {"error": str(e)})
                self._finish_run(run_id, status="failed", duration_ms=duration_ms, summary={}, error=str(e))
                return {"ok": False, "run_id": run_id, "error": str(e), "duration_ms": duration_ms}
            finally:
                self._running = False
                self._current_run_id = None

    async def _run_pipeline(
        self,
        *,
        run_id: int,
        policy: Dict[str, Any],
        max_categories: int,
        max_locations_per_category: int,
        max_per_signal: int,
        parallel_signals: int,
        dry_run: bool,
    ) -> Dict[str, Any]:
        from core.vendor_research import VendorResearcher

        self._log_event(
            run_id,
            "pipeline",
            "info",
            "Beta run started",
            {
                "max_categories": max_categories,
                "max_locations_per_category": max_locations_per_category,
                "max_per_signal": max_per_signal,
                "parallel_signals": parallel_signals,
                "dry_run": bool(dry_run),
            },
        )

        # Stage 1: Public Signals
        t0 = time.time()
        signals = self._build_signals(max_categories=max_categories, max_locations=max_locations_per_category)
        self._record_stage(run_id, "public_signals", 0, len(signals), "ok", t0, notes="Public query seeds generated")
        self._log_event(run_id, "public_signals", "info", "Generated signal seeds", {"count": len(signals)})

        # Stage 2: Collection
        t1 = time.time()
        researcher = VendorResearcher()
        collected = await self._collect_signals(
            run_id=run_id,
            researcher=researcher,
            signals=signals,
            max_per_signal=max_per_signal,
            parallel_signals=parallel_signals,
        )
        self._record_stage(run_id, "collection", len(signals), len(collected), "ok", t1, notes="Fetched from safe public sources only")
        self._log_event(run_id, "collection", "info", "Collection complete", {"input": len(signals), "output": len(collected)})

        # Stage 3: Extraction
        t2 = time.time()
        extracted = self._extract_candidates(collected)
        self._record_stage(run_id, "extraction", len(collected), len(extracted), "ok", t2, notes="Normalized venue entities")
        self._log_event(run_id, "extraction", "info", "Extraction complete", {"input": len(collected), "output": len(extracted)})

        # Stage 4: Scoring
        t3 = time.time()
        scored = [self._score_candidate(c, policy) for c in extracted]
        self._record_stage(run_id, "scoring", len(extracted), len(scored), "ok", t3, notes="Weighted 0-100 scoring rubric")
        self._log_event(run_id, "scoring", "info", "Scoring complete", {"input": len(extracted), "output": len(scored)})

        # Stage 5: Validation
        t4 = time.time()
        validated = await self._validate_candidates(scored, policy)
        self._record_stage(run_id, "validation", len(scored), len(validated), "ok", t4, notes="Dedup + contact-path + website checks")
        self._log_event(run_id, "validation", "info", "Validation complete", {"input": len(scored), "output": len(validated)})

        # Stage 6: Decision
        t5 = time.time()
        decided = [self._decisionize(v) for v in validated]
        self._record_stage(run_id, "decision", len(validated), len(decided), "ok", t5, notes="Assigned pitch angle + review status")
        self._log_event(run_id, "decision", "info", "Decision complete", {"input": len(validated), "output": len(decided)})

        # Stage 7: Launch (queue only)
        t6 = time.time()
        inserted = 0 if dry_run else self._store_leads(run_id, decided)
        self._record_stage(
            run_id,
            "launch",
            len(decided),
            inserted if not dry_run else len(decided),
            "ok",
            t6,
            notes="Queued for manual review only (no outbound sends)",
        )
        self._log_event(run_id, "launch", "info", "Launch stage complete (queue only)", {"input": len(decided), "stored": inserted if not dry_run else len(decided), "dry_run": bool(dry_run)})

        # Stage 8: Feedback
        t7 = time.time()
        feedback_snapshot = self._feedback_snapshot()
        self._record_stage(run_id, "feedback", len(decided), feedback_snapshot.get("count", 0), "ok", t7, notes="Manual feedback trend snapshot")

        # Stage 9: Reinforcement
        t8 = time.time()
        policy_updates = self._reinforcement_update(policy, feedback_snapshot)
        self._record_stage(run_id, "reinforcement", feedback_snapshot.get("count", 0), len(policy_updates), "ok", t8, notes="Policy update from feedback patterns")
        self._log_event(run_id, "reinforcement", "info", "Run finished", {"policy_updates": len(policy_updates), "feedback_count": feedback_snapshot.get("count", 0)})

        top = sorted(decided, key=lambda x: x.get("total_score", 0), reverse=True)[:50]
        tier_counts: Dict[str, int] = {}
        for d in decided:
            tier_counts[d.get("tier", "C")] = tier_counts.get(d.get("tier", "C"), 0) + 1

        return {
            "signals": len(signals),
            "collected": len(collected),
            "extracted": len(extracted),
            "scored": len(scored),
            "validated": len(validated),
            "decisioned": len(decided),
            "stored": inserted if not dry_run else len(decided),
            "tier_counts": tier_counts,
            "top_leads_preview": top[:15],
            "feedback_snapshot": feedback_snapshot,
            "policy_updates": policy_updates,
            "dry_run": bool(dry_run),
            "safety": self.safety_profile(),
        }

    # ── Stage internals ───────────────────────────────────────────────────

    def _build_signals(self, *, max_categories: int, max_locations: int) -> List[Dict[str, str]]:
        categories = BETA_CATEGORY_PLAN[: max(1, min(max_categories, len(BETA_CATEGORY_PLAN)))]
        locs = LOCATION_PRIORITY[: max(1, min(max_locations, len(LOCATION_PRIORITY)))]
        signals: List[Dict[str, str]] = []
        for c in categories:
            for q in c["queries"][:2]:
                for loc in locs:
                    signals.append(
                        {
                            "category": c["id"],
                            "category_label": c["label"],
                            "query": q,
                            "location": loc,
                        }
                    )
        return signals

    async def _collect_signals(
        self,
        *,
        run_id: int,
        researcher: Any,
        signals: List[Dict[str, str]],
        max_per_signal: int,
        parallel_signals: int,
    ) -> List[Dict[str, Any]]:
        all_rows: List[Dict[str, Any]] = []
        cfg = self._load_runtime_config()
        google_places_configured = bool(_safe_str(cfg.get("google_places_api_key", "")))
        yelp_configured = bool(_safe_str(cfg.get("yelp_api_key", "")))
        worker_count = max(1, min(int(parallel_signals or 1), 6))
        self._log_event(
            run_id,
            "collection",
            "info",
            "Collection workers initialized",
            {"parallel_signals": worker_count, "signals": len(signals)},
        )
        if not google_places_configured:
            self._log_event(
                run_id,
                "collection",
                "warn",
                "google_places_api_key not configured; using scrape fallback",
                {"key_name": "google_places_api_key"},
            )
        if not yelp_configured:
            self._log_event(
                run_id,
                "collection",
                "warn",
                "yelp_api_key not configured; using scrape fallback",
                {"key_name": "yelp_api_key"},
            )

        async def process_signal(idx: int, signal: Dict[str, str]) -> List[Dict[str, Any]]:
            local_rows: List[Dict[str, Any]] = []
            query = signal["query"]
            location = signal["location"]
            category = signal["category"]
            self._log_event(run_id, "collection", "info", f"Collecting: {query} @ {location}")

            # Google Maps (primary)
            try:
                maps = await researcher.search_google_maps(query, location)
            except Exception as e:
                maps = []
                self._log_event(run_id, "collection", "warn", "Google Maps failed", {"error": str(e), **signal})
            for row in maps[:max_per_signal]:
                merged = dict(row)
                merged.update({"category": category, "location": location, "signal_query": query, "signal_source": "google_maps"})
                local_rows.append(merged)

            # Google organic (secondary)
            try:
                google = await researcher.search_google(f"{query} near {location}")
            except Exception as e:
                google = []
                self._log_event(run_id, "collection", "warn", "Google search failed", {"error": str(e), **signal})
            for row in google[: max(2, max_per_signal // 2)]:
                merged = dict(row)
                merged.update({"category": category, "location": location, "signal_query": query, "signal_source": "google_search"})
                local_rows.append(merged)

            # Yelp (tertiary; lower volume to control runtime)
            if idx < max(2, len(signals) // 3):
                try:
                    yelp = await researcher.search_yelp(query, location)
                except Exception as e:
                    yelp = []
                    self._log_event(run_id, "collection", "warn", "Yelp failed", {"error": str(e), **signal})
                for row in yelp[: max(2, max_per_signal // 2)]:
                    merged = dict(row)
                    merged.update({"category": category, "location": location, "signal_query": query, "signal_source": "yelp"})
                    local_rows.append(merged)
            else:
                yelp = []

            self._log_event(
                run_id,
                "collection",
                "info",
                "Signal complete",
                {
                    "query": query,
                    "location": location,
                    "category": category,
                    "google_maps_count": len(maps),
                    "google_search_count": len(google),
                    "yelp_count": len(yelp),
                    "total_added_this_signal": len(local_rows),
                },
            )
            if len(maps) + len(google) + len(yelp) == 0:
                ai_rows: List[Dict[str, Any]] = []
                try:
                    ai_rows = await researcher.search_ai(category, location)
                except Exception as e:
                    self._log_event(
                        run_id,
                        "collection",
                        "warn",
                        "AI fallback search failed",
                        {"query": query, "location": location, "category": category, "error": str(e)},
                    )
                for row in ai_rows[:max_per_signal]:
                    merged = dict(row)
                    merged.update({"category": category, "location": location, "signal_query": query, "signal_source": "ai_research"})
                    local_rows.append(merged)

                if ai_rows:
                    self._log_event(
                        run_id,
                        "collection",
                        "info",
                        "AI fallback recovered candidates",
                        {
                        "query": query,
                        "location": location,
                        "category": category,
                        "ai_provider": "groq",
                            "ai_count": len(ai_rows),
                        },
                    )
                    return local_rows

                self._log_event(
                    run_id,
                    "collection",
                    "warn",
                    "Signal produced zero candidates",
                    {"query": query, "location": location, "category": category},
                )
            return local_rows

        sem = asyncio.Semaphore(worker_count)

        async def worker(idx: int, signal: Dict[str, str]) -> List[Dict[str, Any]]:
            async with sem:
                return await process_signal(idx, signal)

        collected_by_signal = await asyncio.gather(
            *(worker(idx, signal) for idx, signal in enumerate(signals)),
            return_exceptions=True,
        )
        for idx, rows in enumerate(collected_by_signal):
            if isinstance(rows, Exception):
                self._log_event(
                    run_id,
                    "collection",
                    "warn",
                    "Signal worker crashed",
                    {"signal_index": idx, "error": str(rows)},
                )
                continue
            all_rows.extend(rows)

        if not all_rows:
            self._log_event(
                run_id,
                "collection",
                "warn",
                "Collection produced zero rows across all signals",
                {
                    "signals": len(signals),
                    "google_places_api_key_configured": google_places_configured,
                    "yelp_api_key_configured": yelp_configured,
                },
            )

        return all_rows

    def _extract_candidates(self, collected: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out_map: Dict[str, Dict[str, Any]] = {}
        for r in collected:
            name = _safe_str(r.get("name"))
            if not name:
                continue
            website = _safe_str(r.get("website"))
            domain = _norm_domain(website)
            email = _safe_str(r.get("email")).lower()
            phone = _norm_phone(_safe_str(r.get("phone")))
            city = _safe_str(r.get("city") or r.get("location"))
            city_norm = _norm_city(city)
            name_norm = name.lower()
            category = _safe_str(r.get("category") or "other")
            source = _safe_str(r.get("signal_source") or r.get("source") or "unknown")
            region = _region_for_city(city)
            venue_type = self._infer_venue_type(name=name, category=category, website=website)

            # Deduplicate primarily by venue identity (name + domain + city).
            # Contact fields are often incomplete/inconsistent across sources.
            if domain:
                dedup_key = f"{name_norm}|{domain}|{city_norm}".strip("|")
            elif phone:
                dedup_key = f"{name_norm}|{phone}|{city_norm}".strip("|")
            elif email:
                dedup_key = f"{name_norm}|{email}|{city_norm}".strip("|")
            else:
                dedup_key = f"{name_norm}|{city_norm}".strip("|")
            if not dedup_key:
                continue

            evidence = self._generate_evidence(name=name, category=category, website=website, city=city)
            existing = out_map.get(dedup_key)
            if existing:
                if phone and not existing.get("contact_phone"):
                    existing["contact_phone"] = phone
                if email and not existing.get("contact_email"):
                    existing["contact_email"] = email
                if website and not existing.get("website"):
                    existing["website"] = website
                    existing["contact_path"] = website
                existing_sources = existing.get("_sources") or set()
                if isinstance(existing_sources, set):
                    existing_sources.add(source)
                    existing["_sources"] = existing_sources
                ev = list(existing.get("evidence", []))
                for snip in evidence:
                    if snip and snip not in ev:
                        ev.append(snip)
                existing["evidence"] = ev[:3]
                continue

            out_map[dedup_key] = {
                    "dedup_key": dedup_key,
                    "venue_name": name,
                    "website": website,
                    "city": city,
                    "region": region,
                    "category": category,
                    "venue_type": venue_type,
                    "signal_source": source,
                    "contact_phone": phone,
                    "contact_email": email,
                    "contact_path": website or email or phone,
                    "raw_payload": r,
                    "evidence": evidence,
                    "_sources": {source},
                }

        out: List[Dict[str, Any]] = []
        for item in out_map.values():
            sources = item.get("_sources") or set()
            if not isinstance(sources, set):
                sources = set()
            item["source_count"] = len(sources) if sources else 1
            item["signal_source"] = ",".join(sorted(sources)) if sources else _safe_str(item.get("signal_source") or "unknown")
            item.pop("_sources", None)
            out.append(item)
        return out

    def _infer_venue_type(self, *, name: str, category: str, website: str) -> str:
        text = " ".join([name, category, website]).lower()
        if any(k in text for k in ("vineyard", "winery")):
            return "vineyard_venue"
        if any(k in text for k in ("ranch", "barn", "farm")):
            return "ranch_venue"
        if any(k in text for k in ("estate", "manor")):
            return "estate_venue"
        if "garden" in text:
            return "garden_venue"
        if any(k in text for k in ("wedding", "venue", "banquet")):
            return "event_venue"
        return "partner_business"

    def _generate_evidence(self, *, name: str, category: str, website: str, city: str) -> List[str]:
        text = " ".join([name, category, website, city]).lower()
        snippets = []
        if _keyword_hits(text, "outdoor"):
            snippets.append("Outdoor/tented event signals detected")
        if _keyword_hits(text, "vendor_list"):
            snippets.append("Vendor-list or partner signal detected")
        if _keyword_hits(text, "premium"):
            snippets.append("Premium clientele signal detected")
        if _keyword_hits(text, "venue"):
            snippets.append("Venue/event-hosting signal detected")
        if city:
            snippets.append(f"Geographic fit: {city}")
        return snippets[:3]

    def _score_candidate(self, row: Dict[str, Any], policy: Dict[str, Any]) -> Dict[str, Any]:
        text = " ".join(
            [
                _safe_str(row.get("venue_name")),
                _safe_str(row.get("category")),
                _safe_str(row.get("website")),
                _safe_str(row.get("city")),
                " ".join(row.get("evidence", [])),
            ]
        ).lower()

        outdoor_fit = min(20, 6 + 3 * _keyword_hits(text, "outdoor"))
        vendor_ref = min(15, 4 + 3 * _keyword_hits(text, "vendor_list") + (2 if "venue" in text else 0))
        luxury_fit = min(15, 2 + 3 * _keyword_hits(text, "premium") + (2 if "country club" in text else 0))
        restroom_need = min(15, 4 + 2 * _keyword_hits(text, "outdoor") + (2 if "remote" in text or "ranch" in text else 0))
        recurring_volume = min(10, 5 + (2 if "wedding" in text else 0) + (2 if "events" in text else 0))
        geographic_fit = 10 if row.get("region") in {"sfv_core", "nearby_core"} else (8 if row.get("region") == "premium_outer" else 5)
        contactability = 0
        if row.get("contact_phone"):
            contactability += 4
        if row.get("contact_email"):
            contactability += 3
        if row.get("website"):
            contactability += 3
        contactability = min(10, contactability)
        backup_fit = min(5, 2 + (2 if "venue" in text else 0) + (1 if "event" in text else 0))
        preferred_bonus = int(policy.get("bonus_vendor_list_proof", 5)) if _keyword_hits(text, "vendor_list") else 0

        total_score = (
            outdoor_fit
            + vendor_ref
            + luxury_fit
            + restroom_need
            + recurring_volume
            + geographic_fit
            + contactability
            + backup_fit
            + preferred_bonus
        )
        total_score = max(0, min(100, total_score))

        scored = dict(row)
        scored.update(
            {
                "outdoor_event_fit_score": outdoor_fit,
                "vendor_referral_potential_score": vendor_ref,
                "luxury_budget_fit_score": luxury_fit,
                "restroom_need_likelihood_score": restroom_need,
                "recurring_event_volume_score": recurring_volume,
                "geographic_fit_score": geographic_fit,
                "contactability_score": contactability,
                "backup_vendor_fit_score": backup_fit,
                "preferred_vendor_bonus": preferred_bonus,
                "total_score": float(total_score),
                "tier": _tier(float(total_score), policy),
            }
        )
        return scored

    async def _validate_candidates(self, scored: List[Dict[str, Any]], policy: Dict[str, Any]) -> List[Dict[str, Any]]:
        # website checks on top score slice only (runtime protection)
        website_status = await self._website_status_map(scored, max_checks=35)

        best_by_dedup: Dict[str, Dict[str, Any]] = {}
        min_keep = float(policy.get("min_score_to_keep", 50))
        no_contact_penalty = float(policy.get("penalty_no_contact_path", 15))
        dead_website_penalty = float(policy.get("penalty_website_unreachable", 18))
        social_only_penalty = float(policy.get("penalty_social_only_website", 10))
        low_venue_penalty = float(policy.get("penalty_low_venue_confidence", 18))
        ai_only_penalty = float(policy.get("penalty_ai_only_source", 6))
        multi_source_bonus = float(policy.get("bonus_multi_source_validation", 6))

        for s in scored:
            flags: List[str] = []
            score = float(s.get("total_score", 0))
            key = s.get("dedup_key", "")
            if not key:
                continue

            has_contact = bool(s.get("contact_phone") or s.get("contact_email") or s.get("website"))
            if not has_contact:
                flags.append("no_contact_path")
                score -= no_contact_penalty

            ws = website_status.get(key)
            if ws is False:
                flags.append("website_unreachable")
                score -= dead_website_penalty
            elif ws is True:
                flags.append("website_live")

            if self._is_social_only_website(_safe_str(s.get("website"))):
                flags.append("social_only_website")
                score -= social_only_penalty

            venue_confidence = self._venue_confidence_score(s)
            if venue_confidence < 0:
                flags.append("low_venue_confidence")
                score -= low_venue_penalty

            source_count = int(s.get("source_count", 1) or 1)
            signal_source = _safe_str(s.get("signal_source"))
            if source_count >= 2:
                flags.append("multi_source")
                score += multi_source_bonus
            elif signal_source == "ai_research":
                flags.append("ai_only_source")
                score -= ai_only_penalty

            score = max(0.0, min(100.0, score))
            s2 = dict(s)
            s2["total_score"] = score
            s2["tier"] = _tier(score, policy)
            s2["validation_flags"] = flags
            s2["validation_status"] = "verified" if ws is True else ("inferred" if ws is None else "needs_review")

            if score < min_keep:
                continue

            old = best_by_dedup.get(key)
            if not old or float(old.get("total_score", 0)) < score:
                best_by_dedup[key] = s2

        return list(best_by_dedup.values())

    def _is_social_only_website(self, website: str) -> bool:
        domain = _norm_domain(website)
        return domain in SOCIAL_ONLY_DOMAINS if domain else False

    def _venue_confidence_score(self, row: Dict[str, Any]) -> int:
        text = " ".join(
            [
                _safe_str(row.get("venue_name")),
                _safe_str(row.get("category")),
                _safe_str(row.get("venue_type")),
                _safe_str(row.get("website")),
            ]
        ).lower()
        pos = sum(1 for t in POSITIVE_VENUE_TERMS if t in text)
        neg = sum(1 for t in NEGATIVE_VENUE_TERMS if t in text)
        return pos - neg

    async def _website_status_map(self, rows: List[Dict[str, Any]], max_checks: int = 35) -> Dict[str, Optional[bool]]:
        top = sorted(rows, key=lambda x: float(x.get("total_score", 0)), reverse=True)
        items = [r for r in top if _safe_str(r.get("website"))][: max(0, max_checks)]
        out: Dict[str, Optional[bool]] = {}
        if not items:
            return out
        if httpx is None:
            for r in items:
                out[str(r["dedup_key"])] = None
            return out

        sem = asyncio.Semaphore(6)

        async def check_row(r: Dict[str, Any]):
            key = str(r["dedup_key"])
            website = _safe_str(r.get("website"))
            if not website:
                out[key] = None
                return
            if "://" not in website:
                website = "https://" + website
            async with sem:
                try:
                    async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as client:
                        resp = await client.head(website)
                        if resp.status_code >= 400:
                            resp = await client.get(website)
                        out[key] = resp.status_code < 400
                except Exception:
                    out[key] = False

        await asyncio.gather(*(check_row(r) for r in items))
        return out

    def _decisionize(self, row: Dict[str, Any]) -> Dict[str, Any]:
        d = dict(row)
        angle = _best_pitch_angle(d)
        first_name = _first_name_from_name(d.get("venue_name", ""))
        d["best_pitch_angle"] = angle
        d["status"] = "queued_review"
        d["proof_snippet_1"] = (d.get("evidence") or [""])[0] if d.get("evidence") else ""
        d["proof_snippet_2"] = (d.get("evidence") or ["", ""])[1] if len(d.get("evidence", [])) > 1 else ""
        d["proof_snippet_3"] = (d.get("evidence") or ["", "", ""])[2] if len(d.get("evidence", [])) > 2 else ""
        d["personalization_seed_1"] = f"{d.get('venue_name', 'This venue')} looks like a strong fit for outdoor/private events."
        d["personalization_seed_2"] = f"If you keep a vendor list, Zoar can be your reliable restroom backup option."
        d["personalization_seed_3"] = f"If referrals are easier, we pay a flat $200 per closed booking."
        d["contact_name"] = first_name if first_name != "there" else ""
        return d

    def _store_leads(self, run_id: int, leads: Iterable[Dict[str, Any]]) -> int:
        conn = self._conn()
        count = 0
        try:
            for l in leads:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO beta_leads (
                        run_id, dedup_key, venue_name, website, city, region, venue_type, category, signal_source,
                        contact_name, contact_email, contact_phone, contact_path, score, tier, best_pitch_angle,
                        status, validation_status, validation_flags, proof_snippet_1, proof_snippet_2, proof_snippet_3,
                        personalization_seed_1, personalization_seed_2, personalization_seed_3,
                        score_breakdown_json, raw_payload, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        _safe_str(l.get("dedup_key")),
                        _safe_str(l.get("venue_name")),
                        _safe_str(l.get("website")),
                        _safe_str(l.get("city")),
                        _safe_str(l.get("region")),
                        _safe_str(l.get("venue_type")),
                        _safe_str(l.get("category")),
                        _safe_str(l.get("signal_source")),
                        _safe_str(l.get("contact_name")),
                        _safe_str(l.get("contact_email")),
                        _safe_str(l.get("contact_phone")),
                        _safe_str(l.get("contact_path")),
                        float(l.get("total_score", 0)),
                        _safe_str(l.get("tier")),
                        _safe_str(l.get("best_pitch_angle")),
                        _safe_str(l.get("status") or "queued_review"),
                        _safe_str(l.get("validation_status") or "inferred"),
                        json.dumps(l.get("validation_flags", [])),
                        _safe_str(l.get("proof_snippet_1")),
                        _safe_str(l.get("proof_snippet_2")),
                        _safe_str(l.get("proof_snippet_3")),
                        _safe_str(l.get("personalization_seed_1")),
                        _safe_str(l.get("personalization_seed_2")),
                        _safe_str(l.get("personalization_seed_3")),
                        json.dumps(
                            {
                                "outdoor_event_fit_score": l.get("outdoor_event_fit_score", 0),
                                "vendor_referral_potential_score": l.get("vendor_referral_potential_score", 0),
                                "luxury_budget_fit_score": l.get("luxury_budget_fit_score", 0),
                                "restroom_need_likelihood_score": l.get("restroom_need_likelihood_score", 0),
                                "recurring_event_volume_score": l.get("recurring_event_volume_score", 0),
                                "geographic_fit_score": l.get("geographic_fit_score", 0),
                                "contactability_score": l.get("contactability_score", 0),
                                "backup_vendor_fit_score": l.get("backup_vendor_fit_score", 0),
                                "preferred_vendor_bonus": l.get("preferred_vendor_bonus", 0),
                            }
                        ),
                        json.dumps(l.get("raw_payload", {}), default=str),
                        _now(),
                    ),
                )
                count += 1
            conn.commit()
            return count
        finally:
            conn.close()

    def _feedback_snapshot(self) -> Dict[str, Any]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT verdict, COUNT(*) AS c FROM beta_feedback GROUP BY verdict"
            ).fetchall()
            out = {r["verdict"]: r["c"] for r in rows}
            out["count"] = sum(out.values())
            return out
        finally:
            conn.close()

    def _reinforcement_update(self, policy: Dict[str, Any], feedback: Dict[str, Any]) -> Dict[str, Any]:
        # Conservative policy update from manual verdict trends.
        total = int(feedback.get("count", 0))
        if total < 8:
            return {}

        good = int(feedback.get("good", 0))
        bad = int(feedback.get("bad", 0))
        good_rate = good / max(1, total)

        updated = dict(policy)
        changed = False
        if good_rate < 0.35 and bad > good:
            updated["min_score_to_keep"] = min(65, int(policy.get("min_score_to_keep", 50)) + 2)
            changed = True
        elif good_rate > 0.70:
            updated["min_score_to_keep"] = max(45, int(policy.get("min_score_to_keep", 50)) - 1)
            changed = True

        if changed:
            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE beta_policy SET value_json=?, updated_at=? WHERE key='weights'",
                    (json.dumps(updated), _now()),
                )
                conn.commit()
            finally:
                conn.close()
            return {"weights": updated}
        return {}

    # ── Run bookkeeping ────────────────────────────────────────────────────

    def _create_run(self, mode: str) -> int:
        conn = self._conn()
        try:
            cur = conn.execute(
                "INSERT INTO beta_runs (mode, status, started_at, summary_json) VALUES (?, 'running', ?, '{}')",
                (_safe_str(mode or "manual"), _now()),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def _finish_run(
        self,
        run_id: int,
        *,
        status: str,
        duration_ms: int,
        summary: Dict[str, Any],
        error: str = "",
    ):
        conn = self._conn()
        try:
            conn.execute(
                """
                UPDATE beta_runs
                SET status = ?, ended_at = ?, duration_ms = ?, summary_json = ?, error = ?
                WHERE id = ?
                """,
                (status, _now(), int(duration_ms), json.dumps(summary, default=str), _safe_str(error), int(run_id)),
            )
            conn.commit()
        finally:
            conn.close()

    def _record_stage(
        self,
        run_id: int,
        stage_name: str,
        input_count: int,
        output_count: int,
        status: str,
        t_start: float,
        notes: str = "",
    ):
        duration_ms = int((time.time() - t_start) * 1000)
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO beta_stage_metrics
                (run_id, stage_name, input_count, output_count, status, duration_ms, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(run_id),
                    _safe_str(stage_name),
                    int(input_count),
                    int(output_count),
                    _safe_str(status or "ok"),
                    duration_ms,
                    _safe_str(notes),
                    _now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _log_event(
        self,
        run_id: Optional[int],
        stage_name: str,
        level: str,
        message: str,
        payload: Optional[Dict[str, Any]] = None,
    ):
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO beta_events (run_id, stage_name, level, message, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    int(run_id) if run_id else None,
                    _safe_str(stage_name),
                    _safe_str(level or "info"),
                    _safe_str(message),
                    json.dumps(payload or {}, default=str),
                    _now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()


_ENGINE: Optional[BetaResearchEngine] = None


def get_beta_research_engine() -> BetaResearchEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = BetaResearchEngine()
    return _ENGINE
