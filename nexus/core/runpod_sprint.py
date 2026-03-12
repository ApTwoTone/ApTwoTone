"""
Runpod Sprint Orchestrator
--------------------------

One-time, hard-capped sprint runner for:
1) Lead intelligence (isolated beta DB only)
2) Creative factory (bulk static + short videos)
3) Paused Meta campaign build pack

Safety defaults:
- No browser logins to social platforms.
- No outbound outreach messages.
- Meta operations are PAUSED objects only.
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import logging
import random
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

try:
    from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps, ImageStat
except Exception:  # pragma: no cover - optional dependency
    Image = None
    ImageDraw = None
    ImageEnhance = None
    ImageFont = None
    ImageOps = None
    ImageStat = None

try:
    import httpx
except Exception:  # pragma: no cover - optional dependency
    httpx = None

from core.beta_research import get_beta_research_engine

log = logging.getLogger("runpod_sprint")

DB_PATH = Path.home() / ".nexus" / "beta_research.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"
RUNS_ROOT = Path.home() / ".nexus" / "runpod_sprint"
WEBSITE_IMAGE_DIR = Path.home() / "nexus" / "website" / "images" / "bathroom"
MEDIA_IMAGE_DIR = Path.home() / ".nexus" / "media"
HIGGSFIELD_RAW_DIR = Path.home() / ".nexus" / "higgsfield" / "static_ads_raw"
HIGGSFIELD_READY_DIR = Path.home() / ".nexus" / "higgsfield" / "static_ads"
VIDEO_MUSIC_DIR = Path.home() / "nexus" / "assets" / "music"
VIDEO_MUSIC_FALLBACK_DIR = Path.home() / ".nexus" / "media" / "music"
VOICE_CACHE_DIR = Path.home() / ".nexus" / "cache" / "voiceovers"


def _resolve_ffmpeg_bin() -> str:
    direct = shutil.which("ffmpeg")
    if direct:
        return direct
    for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"):
        if Path(candidate).exists():
            return candidate
    return ""


FFMPEG_BIN = _resolve_ffmpeg_bin()

PHASES = ["preflight", "leads", "creative", "campaign", "finalize"]

DEFAULT_RUNPOD_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "max_credits_usd": 17.0,
    "phase_caps": [5.0, 10.0, 15.0, 17.0],
    "safety_mode": "no_platform_logins",
    # Default stays off Meta ad-account writes. User can still switch explicitly.
    "launch_mode": "research_creative_only",
    "parallel_signals": 3,
    "parallel_max": 6,
    # Creative source guardrails
    "include_higgsfield_images": True,
    "include_media_images": False,
    "strict_brand_relevance": True,
    "target_leads_min": 300,
    "target_leads_max": 600,
    "target_statics_min": 120,
    "target_statics_max": 250,
    "target_videos_min": 20,
    "target_videos_max": 40,
    "video_use_music": True,
    "video_use_voiceover": True,
    "video_local_tts_fallback": False,
    "include_website_videos": True,
    "video_candidate_multiplier": 3,
    "lead_max_cycles": 4,
    "lead_max_categories": 4,
    "lead_max_locations_per_category": 4,
    "lead_max_per_signal": 16,
    "cold_email_top_n": 250,
    "cold_email_enrich_max": 120,
    "cold_email_min_score": 58,
}

LANGUAGES = ("EN", "ES")
ANGLES = ("luxury", "guest_comfort", "urgency", "social_proof", "value_starting_at_999")
CONTEXTS = ("wedding", "quince", "private_outdoor", "corporate_outdoor")
FORMATS: Dict[str, Tuple[int, int]] = {"1x1": (1080, 1080), "4x5": (1080, 1350), "9x16": (1080, 1920)}

BRAND_ALLOW_KEYWORDS = {
    "bathroom",
    "restroom",
    "toilet",
    "trailer",
    "luxury",
    "portable",
    "zoar",
    "event",
    "wedding",
    "venue",
}

BRAND_BLOCK_KEYWORDS = {
    "shoe",
    "sneaker",
    "apparel",
    "fashion",
    "model",
    "runway",
    "outfit",
    "jacket",
    "pants",
    "dress",
    "heels",
    "bag",
    "handbag",
    "watch",
    "jewelry",
}

EMAIL_REGEX = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.I)
ROLE_BASED_EMAIL_LOCALS = {
    "info",
    "contact",
    "hello",
    "admin",
    "support",
    "sales",
    "office",
    "booking",
    "events",
    "weddings",
    "team",
    "service",
    "customerservice",
    "membership",
    "reservation",
    "reservations",
    "inquiry",
    "inquiries",
    "enquiry",
    "enquiries",
    "marketing",
    "bookings",
    "coordinator",
    "manager",
    "director",
    "noreply",
    "no-reply",
    "press",
    "reception",
    "media",
    "frontdesk",
    "front-desk",
}

NON_PERSON_NAME_MARKERS = {
    "unknown",
    "n/a",
    "na",
    "none",
    "team",
    "events",
    "weddings",
    "sales",
    "support",
    "admin",
    "office",
    "contact",
    "info",
    "venue",
    "wedding",
    "events",
    "event",
    "church",
    "ranch",
    "vineyard",
    "club",
    "hall",
    "center",
    "centre",
}

HIGH_INTENT_VENUE_TERMS = {
    "wedding",
    "venue",
    "events",
    "event",
    "ranch",
    "estate",
    "vineyard",
    "banquet",
    "resort",
    "club",
    "garden",
    "hall",
    "ceremony",
    "reception",
    "private",
    "outdoor",
}

LOW_INTENT_BUSINESS_TERMS = {
    "market",
    "grocery",
    "nursery",
    "landscaping",
    "school",
    "academy",
    "clinic",
    "hospital",
    "hardware",
    "auto",
    "repair",
    "deli",
    "restaurant",
    "cafe",
}

PLACEHOLDER_EMAIL_DOMAINS = {
    "example.com",
    "domain.com",
    "email.com",
    "test.com",
    "yourdomain.com",
    "sample.com",
    "example.org",
    "example.net",
}

PLACEHOLDER_EMAIL_LOCALS = {
    "test",
    "user",
    "example",
    "mail",
    "name",
    "yourname",
    "firstname",
    "lastname",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _safe_str(v: Any) -> str:
    return (str(v or "")).strip()


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return int(default)


def _json(v: Any) -> str:
    return json.dumps(v, default=str, ensure_ascii=True)


@dataclass
class SprintPhaseResult:
    ok: bool
    outputs: Dict[str, Any]
    notes: str = ""
    error: str = ""


class RunpodSprintEngine:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._running = False
        self._stop_requested = False
        self._current_run_id: Optional[int] = None
        self._task: Optional[asyncio.Task] = None
        self._last_error = ""
        self._active_runtime: Dict[str, Any] = {}
        RUNS_ROOT.mkdir(parents=True, exist_ok=True)
        self.init_tables()

    # ── DB ──────────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(DB_PATH), timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def init_tables(self):
        conn = self._conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runpod_sprint_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    status TEXT DEFAULT 'idle',
                    phase TEXT DEFAULT 'preflight',
                    started_at TEXT DEFAULT (datetime('now')),
                    ended_at TEXT DEFAULT '',
                    duration_ms INTEGER DEFAULT 0,
                    spend_usd REAL DEFAULT 0,
                    max_credits_usd REAL DEFAULT 17,
                    phase_caps_json TEXT DEFAULT '[5,10,15,17]',
                    runtime_json TEXT DEFAULT '{}',
                    preflight_json TEXT DEFAULT '{}',
                    outputs_json TEXT DEFAULT '{}',
                    report_path TEXT DEFAULT '',
                    promoted INTEGER DEFAULT 0,
                    error TEXT DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runpod_sprint_phase_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    phase_name TEXT NOT NULL,
                    input_count INTEGER DEFAULT 0,
                    output_count INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'ok',
                    duration_ms INTEGER DEFAULT 0,
                    spend_after_usd REAL DEFAULT 0,
                    notes TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runpod_sprint_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER,
                    phase_name TEXT DEFAULT '',
                    level TEXT DEFAULT 'info',
                    message TEXT DEFAULT '',
                    payload TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runpod_sprint_artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    artifact_type TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    meta_json TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rs_runs_started ON runpod_sprint_runs(started_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rs_phase_run ON runpod_sprint_phase_metrics(run_id, phase_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rs_events_run ON runpod_sprint_events(run_id, id DESC)")
            conn.commit()
        finally:
            conn.close()

    # ── Config / Plan ───────────────────────────────────────────────────

    def _load_config(self) -> Dict[str, Any]:
        if CONFIG_PATH.exists():
            try:
                return json.loads(CONFIG_PATH.read_text())
            except Exception:
                return {}
        return {}

    def _resolve_runtime(self, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        cfg = self._load_config()
        runpod_cfg = dict(DEFAULT_RUNPOD_CONFIG)
        if isinstance(cfg.get("runpod"), dict):
            runpod_cfg.update(cfg.get("runpod") or {})
        if isinstance(overrides, dict):
            runpod_cfg.update(overrides)

        max_credits = max(1.0, _safe_float(runpod_cfg.get("max_credits_usd"), 17.0))
        caps = runpod_cfg.get("phase_caps") or [5.0, 10.0, 15.0, max_credits]
        if not isinstance(caps, Sequence):
            caps = [5.0, 10.0, 15.0, max_credits]
        caps_clean = []
        for c in caps:
            v = _safe_float(c, 0.0)
            if v > 0:
                caps_clean.append(v)
        if not caps_clean:
            caps_clean = [5.0, 10.0, 15.0, max_credits]
        caps_clean = sorted(caps_clean)
        if caps_clean[-1] != max_credits:
            caps_clean[-1] = max_credits

        runpod_cfg["max_credits_usd"] = max_credits
        runpod_cfg["phase_caps"] = caps_clean
        runpod_cfg["parallel_signals"] = max(1, min(_safe_int(runpod_cfg.get("parallel_signals"), 3), 12))
        runpod_cfg["parallel_max"] = max(runpod_cfg["parallel_signals"], min(_safe_int(runpod_cfg.get("parallel_max"), 6), 24))
        runpod_cfg["target_leads_min"] = max(50, _safe_int(runpod_cfg.get("target_leads_min"), 300))
        runpod_cfg["target_leads_max"] = max(runpod_cfg["target_leads_min"], _safe_int(runpod_cfg.get("target_leads_max"), 600))
        runpod_cfg["target_statics_min"] = max(30, _safe_int(runpod_cfg.get("target_statics_min"), 120))
        runpod_cfg["target_statics_max"] = max(runpod_cfg["target_statics_min"], _safe_int(runpod_cfg.get("target_statics_max"), 250))
        runpod_cfg["target_videos_min"] = max(4, _safe_int(runpod_cfg.get("target_videos_min"), 20))
        runpod_cfg["target_videos_max"] = max(runpod_cfg["target_videos_min"], _safe_int(runpod_cfg.get("target_videos_max"), 40))
        runpod_cfg["lead_max_cycles"] = max(1, min(_safe_int(runpod_cfg.get("lead_max_cycles"), 4), 12))
        runpod_cfg["lead_max_categories"] = max(1, min(_safe_int(runpod_cfg.get("lead_max_categories"), 4), 8))
        runpod_cfg["lead_max_locations_per_category"] = max(1, min(_safe_int(runpod_cfg.get("lead_max_locations_per_category"), 4), 8))
        runpod_cfg["lead_max_per_signal"] = max(4, min(_safe_int(runpod_cfg.get("lead_max_per_signal"), 16), 40))
        runpod_cfg["cold_email_top_n"] = max(20, min(_safe_int(runpod_cfg.get("cold_email_top_n"), 250), 1000))
        runpod_cfg["cold_email_enrich_max"] = max(0, min(_safe_int(runpod_cfg.get("cold_email_enrich_max"), 120), 500))
        runpod_cfg["cold_email_min_score"] = max(0, min(_safe_int(runpod_cfg.get("cold_email_min_score"), 58), 100))
        runpod_cfg["include_higgsfield_images"] = bool(runpod_cfg.get("include_higgsfield_images", True))
        runpod_cfg["include_media_images"] = bool(runpod_cfg.get("include_media_images", False))
        runpod_cfg["strict_brand_relevance"] = bool(runpod_cfg.get("strict_brand_relevance", True))
        runpod_cfg["video_use_music"] = bool(runpod_cfg.get("video_use_music", True))
        runpod_cfg["video_use_voiceover"] = bool(runpod_cfg.get("video_use_voiceover", True))
        runpod_cfg["video_local_tts_fallback"] = bool(runpod_cfg.get("video_local_tts_fallback", False))
        runpod_cfg["include_website_videos"] = bool(runpod_cfg.get("include_website_videos", True))
        runpod_cfg["video_candidate_multiplier"] = max(1, min(_safe_int(runpod_cfg.get("video_candidate_multiplier"), 3), 8))
        return runpod_cfg

    def _is_brand_relevant_asset(self, p: Path, strict: bool) -> bool:
        lower_path = str(p).lower()
        if "website/images/bathroom" in lower_path:
            return True
        if "/.nexus/higgsfield/static_ads_raw/" in lower_path or "/.nexus/higgsfield/static_ads/" in lower_path:
            return True
        name = p.name.lower()
        for bad in BRAND_BLOCK_KEYWORDS:
            if bad in name or bad in lower_path:
                return False
        if not strict:
            return True
        return any(g in name or g in lower_path for g in BRAND_ALLOW_KEYWORDS)

    def _image_content_key(self, p: Path) -> str:
        try:
            with p.open("rb") as f:
                head = f.read(256 * 1024)
            st = p.stat()
            h = hashlib.sha1()
            h.update(str(st.st_size).encode("utf-8"))
            h.update(head)
            return h.hexdigest()
        except Exception:
            return str(p.resolve()) if p.exists() else str(p)

    def _source_images(self, runtime: Optional[Dict[str, Any]] = None) -> List[Path]:
        runtime = runtime or {}
        include_hf = bool(runtime.get("include_higgsfield_images", True))
        include_media = bool(runtime.get("include_media_images", False))
        strict_brand = bool(runtime.get("strict_brand_relevance", True))
        files: List[Path] = []
        bases = []
        if include_hf:
            bases.append(HIGGSFIELD_RAW_DIR)
        bases.append(WEBSITE_IMAGE_DIR)
        if include_media:
            bases.append(MEDIA_IMAGE_DIR)
        for base in bases:
            if not base.exists():
                continue
            files.extend(base.rglob("*.jpg"))
            files.extend(base.rglob("*.jpeg"))
            files.extend(base.rglob("*.png"))
            files.extend(base.rglob("*.webp"))
        unique: Dict[str, Path] = {}
        for p in files:
            key = self._image_content_key(p)
            unique[key] = p
        filtered = [p for p in unique.values() if self._is_brand_relevant_asset(p, strict_brand)]
        return filtered

    def _source_video_files(self, runtime: Optional[Dict[str, Any]] = None) -> List[Path]:
        runtime = runtime or {}
        if not bool(runtime.get("include_website_videos", True)):
            return []
        base = Path.home() / "nexus" / "website" / "videos"
        if not base.exists():
            return []
        files: List[Path] = []
        for ext in ("*.mp4", "*.mov", "*.mkv", "*.webm"):
            files.extend(base.rglob(ext))
        out: List[Path] = []
        for p in sorted(files):
            name = p.name.lower()
            if any(bad in name for bad in BRAND_BLOCK_KEYWORDS):
                continue
            try:
                if p.stat().st_size < 180_000:
                    continue
            except Exception:
                continue
            out.append(p)
        return out

    def _ffprobe_video_meta(self, video_path: Path) -> Dict[str, Any]:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size:stream=codec_type,width,height,avg_frame_rate",
            "-of",
            "json",
            str(video_path),
        ]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True)
            if p.returncode != 0:
                return {}
            data = json.loads(p.stdout or "{}")
            streams = data.get("streams") or []
            video = next((s for s in streams if s.get("codec_type") == "video"), {})
            duration = _safe_float((data.get("format") or {}).get("duration"), 0)
            size = _safe_int((data.get("format") or {}).get("size"), 0)
            return {
                "duration": duration,
                "size": size,
                "width": _safe_int(video.get("width"), 0),
                "height": _safe_int(video.get("height"), 0),
            }
        except Exception:
            return {}

    def _preflight(self, runtime: Dict[str, Any]) -> Dict[str, Any]:
        cfg = self._load_config()
        missing: List[str] = []
        warnings: List[str] = []

        required_meta = ["fb_page_access_token", "fb_ad_account_id", "fb_page_id"]
        meta_ok = True
        for k in required_meta:
            if not _safe_str(cfg.get(k, "")):
                meta_ok = False
                missing.append(k)

        beta_db_ok = True
        try:
            conn = self._conn()
            conn.execute("SELECT 1").fetchone()
            conn.close()
        except Exception:
            beta_db_ok = False
            missing.append("beta_research_db_writable")

        template_paths = [
            Path.home() / "nexus" / "core" / "ad_prompts.py",
            Path.home() / "nexus" / "core" / "meta_ads.py",
            Path.home() / "nexus" / "scripts" / "generate_video.py",
        ]
        template_ok = all(p.exists() for p in template_paths)
        if not template_ok:
            missing.append("template_pack_missing")

        images = self._source_images(runtime)
        videos = self._source_video_files(runtime)
        if len(images) < 20:
            warnings.append("Low source image count for creative generation; outputs may be repetitive.")
        if not videos:
            warnings.append("No website source videos found; motion quality may be lower.")

        has_ffmpeg = bool(FFMPEG_BIN)
        if not has_ffmpeg:
            missing.append("ffmpeg_missing")

        if Image is None:
            missing.append("pillow_missing")

        requires_meta = _safe_str(runtime.get("launch_mode")) == "auto_create_paused_campaigns"
        preflight_ok = beta_db_ok and template_ok and Image is not None and has_ffmpeg and (meta_ok or not requires_meta)

        preflight = {
            "ok": preflight_ok,
            "meta_credentials_ok": meta_ok,
            "beta_db_ok": beta_db_ok,
            "template_pack_ok": template_ok,
            "ffmpeg_ok": has_ffmpeg,
            "pillow_ok": Image is not None,
            "source_image_count": len(images),
            "source_video_count": len(videos),
            "missing_dependencies": missing,
            "warnings": warnings,
            "requires_meta_for_launch": requires_meta,
            "runtime": runtime,
        }

        RUNS_ROOT.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        cfg_out = RUNS_ROOT / f"runpod_sprint_start_{stamp}.json"
        cfg_out.write_text(_json(preflight))
        preflight["start_config_json"] = str(cfg_out)
        return preflight

    def plan(self, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        runtime = self._resolve_runtime((overrides or {}).get("runpod") if isinstance(overrides, dict) else None)
        preflight = self._preflight(runtime)

        signals = runtime["lead_max_categories"] * runtime["lead_max_locations_per_category"] * 2
        est_collected = max(120, signals * runtime["lead_max_per_signal"])
        est_validated = min(runtime["target_leads_max"], max(runtime["target_leads_min"], int(est_collected * 0.42)))

        concepts = len(LANGUAGES) * len(ANGLES) * len(CONTEXTS)
        est_statics = max(runtime["target_statics_min"], min(runtime["target_statics_max"], concepts * len(FORMATS)))
        est_videos = runtime["target_videos_min"]
        if runtime["target_videos_max"] > runtime["target_videos_min"]:
            est_videos = min(runtime["target_videos_max"], runtime["target_videos_min"] + 10)

        phase_caps = runtime["phase_caps"]
        return {
            "ok": True,
            "mode": "dry_run_plan",
            "runtime": runtime,
            "preflight": preflight,
            "estimate": {
                "lead_output_validated": est_validated,
                "cold_email_priority_leads": min(runtime["cold_email_top_n"], est_validated),
                "creative_statics_usable": est_statics,
                "creative_videos_usable": est_videos,
                "campaign_entities": {"campaigns": 1, "adsets": 3, "ads": 18},
            },
            "phases": [
                {"phase": "leads", "checkpoint_cap_usd": phase_caps[0]},
                {"phase": "creative", "checkpoint_cap_usd": phase_caps[min(1, len(phase_caps) - 1)]},
                {"phase": "campaign", "checkpoint_cap_usd": phase_caps[min(2, len(phase_caps) - 1)]},
                {"phase": "finalize", "checkpoint_cap_usd": phase_caps[-1]},
            ],
            "hard_stop_cap_usd": runtime["max_credits_usd"],
            "quality_gates": {
                "lead_min": runtime["target_leads_min"],
                "cold_email_min_score": runtime["cold_email_min_score"],
                "static_min": runtime["target_statics_min"],
                "video_min": runtime["target_videos_min"],
                "paused_only_launch": True,
            },
        }

    # ── Public API ───────────────────────────────────────────────────────

    async def start(self, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self._running:
            return {"ok": False, "error": "Runpod sprint already running", "run_id": self._current_run_id}

        async with self._lock:
            if self._running:
                return {"ok": False, "error": "Runpod sprint already running", "run_id": self._current_run_id}

            runtime = self._resolve_runtime((overrides or {}).get("runpod") if isinstance(overrides, dict) else None)
            if _safe_str(runtime.get("safety_mode")) != "no_platform_logins":
                return {"ok": False, "error": "Unsafe safety_mode rejected. Must be no_platform_logins."}

            planned = self.plan({"runpod": runtime})
            preflight = planned.get("preflight", {})
            if not preflight.get("ok"):
                return {"ok": False, "error": "Preflight failed", "preflight": preflight}

            run_id = self._create_run(runtime=runtime, preflight=preflight)
            self._running = True
            self._stop_requested = False
            self._current_run_id = run_id
            self._last_error = ""
            self._active_runtime = runtime
            self._log_event(run_id, "preflight", "info", "Runpod sprint started", {"runtime": runtime})

            self._task = asyncio.create_task(self._run_pipeline(run_id=run_id, runtime=runtime, planned=planned))
            return {"ok": True, "run_id": run_id, "planned": planned}

    async def stop(self, run_id: int = 0) -> Dict[str, Any]:
        target = int(run_id or 0)
        if target and self._current_run_id and target != self._current_run_id:
            return {"ok": False, "error": f"Run #{target} is not the active sprint"}
        if not self._running:
            return {"ok": True, "message": "No active run to stop"}
        self._stop_requested = True
        self._log_event(self._current_run_id, self._phase_of(self._current_run_id), "warn", "Stop requested by operator")
        return {"ok": True, "run_id": self._current_run_id, "status": "stopping"}

    def status(self, run_id: int = 0, include_events: bool = True) -> Dict[str, Any]:
        rid = int(run_id or 0)
        conn = self._conn()
        try:
            if rid > 0:
                run = conn.execute("SELECT * FROM runpod_sprint_runs WHERE id=?", (rid,)).fetchone()
            else:
                run = conn.execute("SELECT * FROM runpod_sprint_runs ORDER BY id DESC LIMIT 1").fetchone()

            if not run:
                return {
                    "running": self._running,
                    "current_run_id": self._current_run_id,
                    "stop_requested": self._stop_requested,
                    "last_error": self._last_error,
                    "run": None,
                    "preflight": {},
                    "outputs": {},
                    "phase_metrics": [],
                    "events": [],
                    "artifacts": [],
                    "runtime": self._active_runtime or self._resolve_runtime(),
                    "spend_meter": {"current_usd": 0.0, "max_usd": float((self._active_runtime or self._resolve_runtime()).get("max_credits_usd", 17.0)), "percent": 0.0},
                    "phase_checkpoints": [],
                }

            run_id_final = int(run["id"])
            phase_rows = conn.execute(
                """
                SELECT phase_name, input_count, output_count, status, duration_ms, spend_after_usd, notes, created_at
                FROM runpod_sprint_phase_metrics
                WHERE run_id = ?
                ORDER BY id ASC
                """,
                (run_id_final,),
            ).fetchall()
            events = []
            if include_events:
                event_rows = conn.execute(
                    """
                    SELECT id, run_id, phase_name, level, message, payload, created_at
                    FROM runpod_sprint_events
                    WHERE run_id = ?
                    ORDER BY id DESC LIMIT 220
                    """,
                    (run_id_final,),
                ).fetchall()
                for r in event_rows:
                    d = dict(r)
                    try:
                        d["payload"] = json.loads(d.get("payload") or "{}")
                    except Exception:
                        d["payload"] = {}
                    events.append(d)

            artifact_rows = conn.execute(
                """
                SELECT id, artifact_type, file_path, meta_json, created_at
                FROM runpod_sprint_artifacts
                WHERE run_id = ?
                ORDER BY id DESC
                """,
                (run_id_final,),
            ).fetchall()
            artifacts = []
            for r in artifact_rows:
                d = dict(r)
                try:
                    d["meta"] = json.loads(d.get("meta_json") or "{}")
                except Exception:
                    d["meta"] = {}
                d.pop("meta_json", None)
                artifacts.append(d)
        finally:
            conn.close()

        run_d = dict(run)
        runtime = {}
        preflight = {}
        outputs = {}
        caps = []
        try:
            runtime = json.loads(run_d.get("runtime_json") or "{}")
        except Exception:
            runtime = {}
        try:
            preflight = json.loads(run_d.get("preflight_json") or "{}")
        except Exception:
            preflight = {}
        try:
            outputs = json.loads(run_d.get("outputs_json") or "{}")
        except Exception:
            outputs = {}
        try:
            caps = list(json.loads(run_d.get("phase_caps_json") or "[]"))
        except Exception:
            caps = []
        caps = [float(c) for c in caps if _safe_float(c, 0) > 0]
        if not caps:
            caps = [5.0, 10.0, 15.0, float(runtime.get("max_credits_usd", 17.0))]

        spend = float(run_d.get("spend_usd") or 0.0)
        max_credits = float(run_d.get("max_credits_usd") or runtime.get("max_credits_usd") or 17.0)
        spend_pct = 0.0 if max_credits <= 0 else min(100.0, (spend / max_credits) * 100.0)

        checkpoints = []
        for idx, cap in enumerate(caps):
            checkpoints.append(
                {
                    "index": idx + 1,
                    "cap_usd": cap,
                    "passed": spend >= cap or run_d.get("status") in {"completed", "stopped", "failed"},
                    "active": (idx == 0 and spend < cap) or (idx > 0 and spend >= caps[idx - 1] and spend < cap),
                }
            )

        return {
            "running": self._running,
            "current_run_id": self._current_run_id,
            "stop_requested": self._stop_requested,
            "last_error": self._last_error,
            "run": run_d,
            "runtime": runtime,
            "preflight": preflight,
            "outputs": outputs,
            "phase_metrics": [dict(r) for r in phase_rows],
            "events": events,
            "artifacts": artifacts,
            "spend_meter": {
                "current_usd": round(spend, 3),
                "max_usd": round(max_credits, 3),
                "percent": round(spend_pct, 2),
            },
            "phase_checkpoints": checkpoints,
        }

    def promote(self, run_id: int = 0) -> Dict[str, Any]:
        status = self.status(run_id=run_id, include_events=False)
        run = status.get("run") or {}
        rid = int(run.get("id") or 0)
        if rid <= 0:
            return {"ok": False, "error": "No run available to promote"}
        outputs = status.get("outputs") or {}
        run_dir = RUNS_ROOT / f"run_{rid}"
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "run_id": rid,
            "promoted_at": _now(),
            "mode": "paused_campaign_launch_pack",
            "outputs": outputs,
            "artifacts": status.get("artifacts") or [],
            "notes": "Promotion marks this run as reviewed for Campaign Builder handoff.",
        }
        manifest_path = run_dir / "promotion_manifest.json"
        manifest_path.write_text(_json(manifest))

        conn = self._conn()
        try:
            conn.execute(
                "UPDATE runpod_sprint_runs SET promoted = 1 WHERE id = ?",
                (rid,),
            )
            conn.commit()
        finally:
            conn.close()
        self._insert_artifact(rid, "promotion_manifest", manifest_path, {"promoted": True})
        return {"ok": True, "run_id": rid, "manifest_path": str(manifest_path), "outputs": outputs}

    # ── Runner internals ────────────────────────────────────────────────

    async def _run_pipeline(self, *, run_id: int, runtime: Dict[str, Any], planned: Dict[str, Any]):
        started = time.time()
        outputs: Dict[str, Any] = {
            "leads_validated": 0,
            "cold_email_priority_count": 0,
            "cold_email_enriched_count": 0,
            "statics_usable": 0,
            "videos_usable": 0,
            "campaigns_created": 0,
            "adsets_created": 0,
            "ads_created": 0,
        }
        try:
            run_dir = RUNS_ROOT / f"run_{run_id}"
            run_dir.mkdir(parents=True, exist_ok=True)

            # Phase 0: Preflight (already validated, record stage)
            preflight_start = time.time()
            self._update_phase(run_id, "preflight")
            self._record_phase(
                run_id,
                "preflight",
                1,
                1,
                "ok",
                preflight_start,
                "Preflight passed; frozen start config generated",
            )
            self._advance_spend_to_cap(run_id, runtime, checkpoint_index=0, fraction=0.0)
            if self._should_stop(run_id):
                self._finish_run(run_id, "stopped", int((time.time() - started) * 1000), outputs, error="Stopped by operator")
                return

            # Phase 1: Leads
            self._update_phase(run_id, "leads")
            lead_start = time.time()
            lead_result = await self._run_lead_phase(run_id, runtime, run_dir)
            if not lead_result.ok:
                raise RuntimeError(lead_result.error or "Lead phase failed")
            outputs.update(lead_result.outputs)
            self._set_outputs(run_id, outputs)
            self._record_phase(
                run_id,
                "leads",
                lead_result.outputs.get("signals", 0),
                lead_result.outputs.get("leads_validated", 0),
                "ok",
                lead_start,
                lead_result.notes,
            )
            self._advance_spend_to_cap(run_id, runtime, checkpoint_index=0, fraction=1.0)
            self._log_event(run_id, "leads", "info", "Lead checkpoint reached", {"cap_usd": runtime["phase_caps"][0]})
            if self._should_stop(run_id):
                self._finish_run(run_id, "stopped", int((time.time() - started) * 1000), outputs, error="Stopped by operator")
                return

            # Phase 2: Creative
            self._update_phase(run_id, "creative")
            creative_start = time.time()
            creative_result = await self._run_creative_phase(run_id, runtime, run_dir)
            if not creative_result.ok:
                raise RuntimeError(creative_result.error or "Creative phase failed")
            outputs.update(creative_result.outputs)
            self._set_outputs(run_id, outputs)
            self._record_phase(
                run_id,
                "creative",
                creative_result.outputs.get("source_images", 0),
                creative_result.outputs.get("statics_usable", 0) + creative_result.outputs.get("videos_usable", 0),
                "ok",
                creative_start,
                creative_result.notes,
            )
            self._advance_spend_to_cap(run_id, runtime, checkpoint_index=1, fraction=1.0)
            self._log_event(run_id, "creative", "info", "Creative checkpoint reached", {"cap_usd": runtime["phase_caps"][1]})
            if self._should_stop(run_id):
                self._finish_run(run_id, "stopped", int((time.time() - started) * 1000), outputs, error="Stopped by operator")
                return

            # Phase 3: Campaign builder (optional)
            self._update_phase(run_id, "campaign")
            campaign_start = time.time()
            launch_mode = _safe_str(runtime.get("launch_mode") or "research_creative_only")
            if launch_mode == "auto_create_paused_campaigns":
                campaign_result = await self._run_campaign_phase(run_id, runtime, run_dir)
            else:
                campaign_result = SprintPhaseResult(
                    ok=True,
                    outputs={"campaigns_created": 0, "adsets_created": 0, "ads_created": 0},
                    notes=f"Campaign phase skipped (launch_mode={launch_mode})",
                )
            if not campaign_result.ok:
                self._log_event(run_id, "campaign", "warn", "Campaign phase incomplete", {"error": campaign_result.error})
            outputs.update(campaign_result.outputs)
            self._set_outputs(run_id, outputs)
            self._record_phase(
                run_id,
                "campaign",
                outputs.get("statics_usable", 0) + outputs.get("videos_usable", 0),
                outputs.get("campaigns_created", 0) + outputs.get("adsets_created", 0) + outputs.get("ads_created", 0),
                "ok" if campaign_result.ok else "warn",
                campaign_start,
                campaign_result.notes or campaign_result.error,
            )
            self._advance_spend_to_cap(run_id, runtime, checkpoint_index=2, fraction=1.0)
            if self._should_stop(run_id):
                self._finish_run(run_id, "stopped", int((time.time() - started) * 1000), outputs, error="Stopped by operator")
                return

            # Phase 4: Finalize report
            self._update_phase(run_id, "finalize")
            final_start = time.time()
            report = self._write_report(run_id, runtime, outputs, planned)
            self._record_phase(run_id, "finalize", 1, 1, "ok", final_start, "Audit artifacts + summary written")
            self._advance_spend_to_cap(run_id, runtime, checkpoint_index=3, fraction=1.0)
            self._finish_run(run_id, "completed", int((time.time() - started) * 1000), outputs, report_path=str(report))
            self._log_event(run_id, "finalize", "info", "Runpod sprint completed", {"report_path": str(report), "outputs": outputs})
        except Exception as e:
            self._last_error = str(e)
            self._log_event(run_id, self._phase_of(run_id), "error", "Runpod sprint failed", {"error": str(e)})
            self._finish_run(run_id, "failed", int((time.time() - started) * 1000), outputs, error=str(e))
        finally:
            self._running = False
            self._stop_requested = False
            self._current_run_id = None
            self._task = None

    async def _run_lead_phase(self, run_id: int, runtime: Dict[str, Any], run_dir: Path) -> SprintPhaseResult:
        engine = get_beta_research_engine()
        target_min = runtime["target_leads_min"]
        max_cycles = runtime["lead_max_cycles"]
        parallel = runtime["parallel_signals"]
        max_parallel = runtime["parallel_max"]
        cycle_results: List[Dict[str, Any]] = []
        lead_map: Dict[str, Dict[str, Any]] = {}
        total_signals = 0

        for cycle in range(max_cycles):
            if self._should_stop(run_id):
                break
            self._log_event(
                run_id,
                "leads",
                "info",
                "Starting beta lead cycle",
                {
                    "cycle": cycle + 1,
                    "parallel_signals": parallel,
                    "max_categories": runtime["lead_max_categories"],
                    "max_locations_per_category": runtime["lead_max_locations_per_category"],
                    "max_per_signal": runtime["lead_max_per_signal"],
                },
            )
            result = await engine.run_cycle(
                max_categories=runtime["lead_max_categories"],
                max_locations_per_category=runtime["lead_max_locations_per_category"],
                max_per_signal=runtime["lead_max_per_signal"],
                parallel_signals=parallel,
                dry_run=False,
            )
            cycle_results.append(result)
            total_signals += _safe_int(result.get("signals"), 0)
            if result.get("ok") and _safe_int(result.get("validated"), 0) > 0:
                parallel = min(max_parallel, parallel + 1)

            run_id_beta = _safe_int(result.get("run_id"), 0)
            if run_id_beta > 0:
                rows = engine.list_leads(run_id=run_id_beta, limit=1200, min_score=50)
                for row in rows:
                    key = _safe_str(row.get("dedup_key") or f"id:{row.get('id')}")
                    if not key:
                        continue
                    old = lead_map.get(key)
                    if (not old) or (_safe_float(old.get("score"), 0) < _safe_float(row.get("score"), 0)):
                        lead_map[key] = row

            if len(lead_map) >= target_min:
                break

        leads_raw = sorted(lead_map.values(), key=lambda r: _safe_float(r.get("score"), 0), reverse=True)
        leads = [r for r in leads_raw if self._is_high_intent_venue(r)]
        filtered_out = max(0, len(leads_raw) - len(leads))
        if filtered_out:
            self._log_event(
                run_id,
                "leads",
                "info",
                "Applied high-intent venue filter",
                {"kept": len(leads), "filtered_out": filtered_out},
            )
        csv_path = run_dir / "leads_validated.csv"
        cols = [
            "id",
            "venue_name",
            "city",
            "region",
            "website",
            "contact_name",
            "contact_email",
            "contact_phone",
            "score",
            "tier",
            "best_pitch_angle",
            "signal_source",
            "validation_status",
        ]
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=cols)
            writer.writeheader()
            for row in leads:
                writer.writerow({c: row.get(c, "") for c in cols})
        self._insert_artifact(run_id, "leads_csv", csv_path, {"count": len(leads)})
        targeting_summary_path = self._write_lead_targeting_summary(run_id, leads, run_dir)
        if targeting_summary_path:
            self._insert_artifact(run_id, "lead_targeting_summary", targeting_summary_path, {"count": len(leads)})
        cold_email_pack = await self._build_cold_email_priority_pack(run_id, leads, runtime, run_dir)

        cycles_ok = sum(1 for r in cycle_results if r.get("ok"))
        cycle_errors = len(cycle_results) - cycles_ok
        err_rate = 0.0 if not cycle_results else cycle_errors / len(cycle_results)
        notes = (
            f"Cycles={len(cycle_results)}, ok={cycles_ok}, error_rate={err_rate:.2f}, "
            f"validated={len(leads)}, filtered_out={filtered_out}, cold_email_priority={cold_email_pack.get('count', 0)}."
        )
        if len(leads) < target_min:
            notes += f" Below target ({target_min}) but retained best-possible validated set."
            self._log_event(
                run_id,
                "leads",
                "warn",
                "Lead target not met within cap; preserving best-effort dataset",
                {"target_min": target_min, "validated": len(leads)},
            )

        return SprintPhaseResult(
            ok=len(leads) > 0,
            outputs={
                "signals": total_signals,
                "lead_cycles": len(cycle_results),
                "lead_error_rate": round(err_rate, 4),
                "leads_validated": len(leads),
                "cold_email_priority_count": _safe_int(cold_email_pack.get("count"), 0),
                "cold_email_enriched_count": _safe_int(cold_email_pack.get("enriched_count"), 0),
                "cold_email_direct_count": _safe_int(cold_email_pack.get("direct_count"), 0),
                "cold_email_role_based_count": _safe_int(cold_email_pack.get("role_based_count"), 0),
                "cold_email_needs_enrichment_count": _safe_int(cold_email_pack.get("needs_enrichment_count"), 0),
            },
            notes=notes,
            error="" if len(leads) > 0 else "No validated leads generated",
        )

    def _is_high_intent_venue(self, row: Dict[str, Any]) -> bool:
        name = _safe_str(row.get("venue_name")).lower()
        category = _safe_str(row.get("category")).lower()
        venue_type = _safe_str(row.get("venue_type")).lower()
        website = _safe_str(row.get("website")).lower()
        text = " ".join(x for x in [name, category, venue_type, website] if x).strip()
        if not text:
            return False
        positive_hits = sum(1 for t in HIGH_INTENT_VENUE_TERMS if t in text)
        negative_hits = sum(1 for t in LOW_INTENT_BUSINESS_TERMS if t in text)

        # Must have some event/venue signal.
        if positive_hits <= 0:
            return False
        # Reject low-intent businesses unless there is strong venue evidence.
        if negative_hits > 0 and positive_hits < 2:
            return False
        # Social-only websites are low-confidence for conversion.
        website_domain = self._domain_from_url(website)
        if website_domain in {"facebook.com", "instagram.com", "yelp.com"}:
            return False
        return True

    def _write_lead_targeting_summary(self, run_id: int, leads: List[Dict[str, Any]], run_dir: Path) -> Optional[Path]:
        if not leads:
            return None
        by_city: Dict[str, int] = {}
        by_category: Dict[str, int] = {}
        by_angle: Dict[str, int] = {}
        by_tier: Dict[str, int] = {}
        for row in leads:
            city = _safe_str(row.get("city") or "Unknown")
            category = _safe_str(row.get("category") or "unknown")
            angle = _safe_str(row.get("best_pitch_angle") or "referral_partner")
            tier = _safe_str(row.get("tier") or "C")
            by_city[city] = by_city.get(city, 0) + 1
            by_category[category] = by_category.get(category, 0) + 1
            by_angle[angle] = by_angle.get(angle, 0) + 1
            by_tier[tier] = by_tier.get(tier, 0) + 1

        top_cities = sorted(by_city.items(), key=lambda x: x[1], reverse=True)[:10]
        top_categories = sorted(by_category.items(), key=lambda x: x[1], reverse=True)[:8]
        top_angles = sorted(by_angle.items(), key=lambda x: x[1], reverse=True)[:6]
        top_leads = sorted(leads, key=lambda r: _safe_float(r.get("score"), 0), reverse=True)[:25]

        lines = [
            f"# Targeting Summary (Run {run_id})",
            "",
            "## Why These Targets",
            "- Prioritized venues/partners with strongest score, contactability, and event-fit evidence.",
            "- Focus on cities and categories with repeated high-scoring opportunities.",
            "- Angle recommendations are mapped per-lead for outreach fit (preferred/backup/referral).",
            "",
            "## Top Cities",
        ]
        lines.extend([f"- {city}: {count}" for city, count in top_cities])
        lines.append("")
        lines.append("## Top Venue Categories")
        lines.extend([f"- {cat}: {count}" for cat, count in top_categories])
        lines.append("")
        lines.append("## Recommended Pitch Angles")
        lines.extend([f"- {angle}: {count}" for angle, count in top_angles])
        lines.append("")
        lines.append("## Tier Mix")
        lines.extend([f"- Tier {tier}: {count}" for tier, count in sorted(by_tier.items(), key=lambda x: x[0])])
        lines.append("")
        lines.append("## Top 25 Priority Targets")
        for row in top_leads:
            venue = _safe_str(row.get("venue_name") or "Unknown")
            city = _safe_str(row.get("city") or "Unknown")
            score = int(round(_safe_float(row.get("score"), 0)))
            angle = _safe_str(row.get("best_pitch_angle") or "referral_partner")
            contact = _safe_str(row.get("contact_phone") or row.get("contact_email") or "No direct contact")
            reason = _safe_str(row.get("proof_snippet_1") or row.get("proof_snippet_2") or "High fit by scoring model")
            lines.append(f"- {venue} ({city}) | Score {score} | Angle {angle} | Contact {contact} | Why: {reason}")

        out = run_dir / "lead_targeting_summary.md"
        out.write_text("\n".join(lines))
        return out

    def _domain_from_url(self, url: str) -> str:
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

    def _domain_from_email(self, email: str) -> str:
        value = _safe_str(email).lower()
        if "@" not in value:
            return ""
        return value.split("@", 1)[1].strip()

    def _email_local_part(self, email: str) -> str:
        value = _safe_str(email).lower()
        if "@" not in value:
            return ""
        return value.split("@", 1)[0].strip()

    def _is_role_based_email(self, email: str) -> bool:
        local = self._email_local_part(email)
        if not local:
            return True
        local_root = re.split(r"[+._\-]", local)[0]
        return local in ROLE_BASED_EMAIL_LOCALS or local_root in ROLE_BASED_EMAIL_LOCALS

    def _name_is_usable(self, name: str, venue_name: str = "") -> bool:
        candidate = _safe_str(name)
        if not candidate:
            return False
        lowered = candidate.lower()
        if lowered in NON_PERSON_NAME_MARKERS:
            return False
        if "@" in candidate:
            return False
        if any(ch.isdigit() for ch in candidate):
            return False
        pieces = [p for p in re.split(r"\s+", candidate) if p]
        if not pieces:
            return False
        first = pieces[0].strip(" ,.-_").lower()
        if first in NON_PERSON_NAME_MARKERS:
            return False
        venue_tokens = {
            t.strip(" ,.-_").lower()
            for t in re.split(r"[\s/&\-]+", _safe_str(venue_name))
            if t.strip(" ,.-_")
        }
        if first in venue_tokens and len(pieces) <= 2:
            return False
        if len(pieces) == 1 and first in {"team", "events", "weddings", "venue", "office"}:
            return False
        return True

    def _greeting_for_row(self, row: Dict[str, Any], email: str) -> str:
        name = _safe_str(row.get("contact_name"))
        venue_name = _safe_str(row.get("venue_name"))
        if self._name_is_usable(name, venue_name=venue_name):
            first = re.split(r"\s+", name.strip())[0].strip(" ,.-_")
            return f"Hi {first},"
        return "Hi there,"

    def _parse_flags(self, row: Dict[str, Any]) -> List[str]:
        raw = row.get("validation_flags")
        if isinstance(raw, list):
            return [str(x) for x in raw]
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return []
            if text.startswith("["):
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, list):
                        return [str(x) for x in parsed]
                except Exception:
                    pass
            return [text]
        return []

    def _extract_emails(self, content: str) -> List[str]:
        if not content:
            return []
        seen: Dict[str, str] = {}
        for m in EMAIL_REGEX.findall(content):
            cleaned = m.strip().strip(".,;:()[]{}<>\"'").lower()
            if not cleaned or "@" not in cleaned:
                continue
            local, domain = cleaned.split("@", 1)
            if len(local) < 1 or len(domain) < 3 or "." not in domain:
                continue
            if self._is_placeholder_email(cleaned):
                continue
            seen[cleaned] = cleaned
        return list(seen.values())

    def _is_placeholder_email(self, email: str) -> bool:
        e = _safe_str(email).lower()
        if "@" not in e:
            return True
        local = self._email_local_part(e)
        domain = self._domain_from_email(e)
        local_root = re.split(r"[+._\-]", local)[0]
        if domain in PLACEHOLDER_EMAIL_DOMAINS:
            return True
        if local in PLACEHOLDER_EMAIL_LOCALS or local_root in PLACEHOLDER_EMAIL_LOCALS:
            return True
        if local.startswith("test") or local.startswith("example"):
            return True
        return False

    def _best_email(self, emails: List[str], website_domain: str) -> str:
        if not emails:
            return ""

        def score(email: str) -> int:
            s = 0
            if self._is_placeholder_email(email):
                return -100
            domain = self._domain_from_email(email)
            role = self._is_role_based_email(email)
            if website_domain and domain == website_domain:
                s += 30
            elif website_domain and domain.endswith("." + website_domain):
                s += 20
            elif website_domain and website_domain.endswith("." + domain):
                s += 16
            if role:
                s += 4
            else:
                s += 14
            # penalize obvious non-business catchalls
            if domain in {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com"}:
                s -= 6
            return s

        ranked = sorted(emails, key=score, reverse=True)
        return ranked[0] if ranked else ""

    async def _fetch_website_emails(self, website: str) -> List[str]:
        if httpx is None:
            return []
        base = _safe_str(website)
        if not base:
            return []
        if "://" not in base:
            base = "https://" + base
        try:
            parsed = urlparse(base)
            origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
            if not parsed.netloc:
                return []
        except Exception:
            return []

        paths = ["", "/contact", "/contact-us", "/about", "/events", "/weddings"]
        found: Dict[str, str] = {}
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        }
        timeout = httpx.Timeout(6.0, connect=4.0)
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
                for path in paths:
                    url = origin + path
                    try:
                        resp = await client.get(url)
                    except Exception:
                        continue
                    if resp.status_code >= 400:
                        continue
                    body = resp.text[:260000]
                    for email in self._extract_emails(body):
                        found[email] = email
        except Exception:
            return []
        return list(found.values())

    def _cold_email_score(
        self,
        row: Dict[str, Any],
        *,
        contact_email: str,
        email_enriched: bool,
        role_based: bool,
        flags: List[str],
    ) -> Tuple[float, str]:
        score = _safe_float(row.get("score"), 0.0)
        reasons: List[str] = []
        website = _safe_str(row.get("website"))
        website_domain = self._domain_from_url(website)
        email_domain = self._domain_from_email(contact_email)

        if contact_email:
            score += 16
            reasons.append("has email")
        else:
            score -= 24
            reasons.append("missing email")

        if contact_email and role_based:
            score -= 5
            reasons.append("role inbox")
        elif contact_email:
            score += 9
            reasons.append("person-like inbox")
        if self._is_placeholder_email(contact_email):
            score -= 40
            reasons.append("placeholder email")

        if website_domain and email_domain:
            if email_domain == website_domain or email_domain.endswith("." + website_domain):
                score += 8
                reasons.append("email/domain match")
            else:
                score -= 6
                reasons.append("email/domain mismatch")

        if "website_live" in flags:
            score += 6
            reasons.append("site reachable")
        if "website_unreachable" in flags:
            score -= 20
            reasons.append("site unreachable")
        if "social_only_website" in flags:
            score -= 12
            reasons.append("social-only site")
        if "ai_only_source" in flags:
            score -= 8
            reasons.append("ai-only source")
        if not website:
            score -= 8
            reasons.append("no website")

        tier = _safe_str(row.get("tier")).upper()
        if tier == "A":
            score += 5
        elif tier == "B":
            score += 3
        elif tier == "C":
            score += 1

        if self._name_is_usable(_safe_str(row.get("contact_name")), venue_name=_safe_str(row.get("venue_name"))):
            score += 5
            reasons.append("usable name")
        if _safe_str(row.get("contact_phone")):
            score += 2

        angle = _safe_str(row.get("best_pitch_angle"))
        if angle in {"preferred_vendor", "backup_vendor"}:
            score += 3
        if email_enriched:
            score += 2
            reasons.append("email enriched")

        score = max(0.0, min(100.0, round(score, 1)))
        reason_text = ", ".join(reasons[:5]) if reasons else "scoring baseline"
        return score, reason_text

    def _subject_for_row(self, row: Dict[str, Any]) -> str:
        angle = _safe_str(row.get("best_pitch_angle") or "referral_partner")
        venue = _safe_str(row.get("venue_name") or "").strip()
        if angle == "preferred_vendor":
            return f"Preferred vendor idea for {venue}" if venue else "Preferred vendor idea"
        if angle == "backup_vendor":
            return "Backup restroom coverage for peak dates"
        if angle == "overflow_peak_date":
            return "Extra restroom capacity for outdoor event dates"
        return "$200 referral per booked event"

    def _opening_line_for_row(self, row: Dict[str, Any]) -> str:
        angle = _safe_str(row.get("best_pitch_angle") or "referral_partner")
        city = _safe_str(row.get("city") or "your area")
        venue = _safe_str(row.get("venue_name") or "your venue")
        if angle == "preferred_vendor":
            return f"We support outdoor/private events in {city} and would be a strong preferred restroom trailer option for {venue}."
        if angle == "backup_vendor":
            return f"If your usual restroom provider is booked, we can cover backup dates for {venue} with our 4-stall luxury trailer."
        if angle == "overflow_peak_date":
            return f"For busy event dates in {city}, we can add overflow restroom capacity with full delivery and pickup."
        return f"If a client at {venue} needs a luxury restroom trailer, we pay a $200 referral per closed booking."

    async def _build_cold_email_priority_pack(
        self,
        run_id: int,
        leads: List[Dict[str, Any]],
        runtime: Dict[str, Any],
        run_dir: Path,
    ) -> Dict[str, Any]:
        top_n = max(1, _safe_int(runtime.get("cold_email_top_n"), 250))
        enrich_budget = max(0, _safe_int(runtime.get("cold_email_enrich_max"), 120))
        min_score = float(_safe_float(runtime.get("cold_email_min_score"), 58))

        website_cache: Dict[str, List[str]] = {}
        enrich_count = 0
        direct_count = 0
        role_count = 0
        rows_out: List[Dict[str, Any]] = []
        needs_enrichment: List[Dict[str, Any]] = []

        for row in leads:
            venue = _safe_str(row.get("venue_name"))
            if not venue:
                continue
            website = _safe_str(row.get("website"))
            website_domain = self._domain_from_url(website)
            email_existing = _safe_str(row.get("contact_email")).lower()
            email = email_existing
            email_source = "existing" if email else ""
            email_enriched = False
            if email and self._is_placeholder_email(email):
                email = ""
                email_source = ""

            if (not email) and website and httpx is not None and enrich_count < enrich_budget:
                if website not in website_cache:
                    website_cache[website] = await self._fetch_website_emails(website)
                candidates = website_cache.get(website, [])
                selected = self._best_email(candidates, website_domain)
                if selected:
                    email = selected
                    email_source = "website_enriched"
                    email_enriched = True
                    enrich_count += 1

            # cold email list requires a verified-ish email contact path
            if (not email) or self._is_placeholder_email(email):
                if website_domain:
                    baseline = _safe_float(row.get("score"), 0)
                    if baseline >= min_score:
                        needs_enrichment.append(
                            {
                                "lead_id": row.get("id", ""),
                                "venue_name": venue,
                                "city": _safe_str(row.get("city")),
                                "website": website,
                                "contact_phone": _safe_str(row.get("contact_phone")),
                                "score": baseline,
                                "tier": _safe_str(row.get("tier")),
                                "best_pitch_angle": _safe_str(row.get("best_pitch_angle")),
                                "suggested_role_email": f"info@{website_domain}",
                                "reason": "No verified email extracted; manual enrichment recommended",
                            }
                )
                continue

            email_domain = self._domain_from_email(email)
            domain_matches_website = bool(
                website_domain
                and email_domain
                and (
                    email_domain == website_domain
                    or email_domain.endswith("." + website_domain)
                    or website_domain.endswith("." + email_domain)
                )
            )
            if email_source == "website_enriched" and website_domain and not domain_matches_website:
                baseline = _safe_float(row.get("score"), 0)
                needs_enrichment.append(
                    {
                        "lead_id": row.get("id", ""),
                        "venue_name": venue,
                        "city": _safe_str(row.get("city")),
                        "website": website,
                        "contact_phone": _safe_str(row.get("contact_phone")),
                        "score": baseline,
                        "tier": _safe_str(row.get("tier")),
                        "best_pitch_angle": _safe_str(row.get("best_pitch_angle")),
                        "suggested_role_email": f"info@{website_domain}",
                        "reason": f"Extracted email domain mismatch ({email_domain}); manual verification required",
                    }
                )
                continue

            role_based = self._is_role_based_email(email)
            if role_based:
                role_count += 1
            else:
                direct_count += 1

            flags = self._parse_flags(row)
            outreach_score, rationale = self._cold_email_score(
                row,
                contact_email=email,
                email_enriched=email_enriched,
                role_based=role_based,
                flags=flags,
            )
            if outreach_score < min_score:
                continue

            confidence = "high" if (outreach_score >= 75 and not role_based) else ("medium" if outreach_score >= 64 else "low")
            out = {
                "lead_id": row.get("id", ""),
                "venue_name": venue,
                "city": _safe_str(row.get("city")),
                "region": _safe_str(row.get("region")),
                "venue_type": _safe_str(row.get("venue_type")),
                "website": website,
                "contact_name": _safe_str(row.get("contact_name")),
                "greeting": self._greeting_for_row(row, email),
                "contact_email": email,
                "email_source": email_source,
                "email_type": "role_based" if role_based else "direct_or_personal",
                "contact_phone": _safe_str(row.get("contact_phone")),
                "outreach_score": outreach_score,
                "confidence": confidence,
                "tier": _safe_str(row.get("tier")),
                "best_pitch_angle": _safe_str(row.get("best_pitch_angle")),
                "subject_line": self._subject_for_row(row),
                "opening_line": self._opening_line_for_row(row),
                "rationale": rationale,
                "proof_snippet_1": _safe_str(row.get("proof_snippet_1")),
                "proof_snippet_2": _safe_str(row.get("proof_snippet_2")),
                "validation_flags": ",".join(flags),
                "signal_source": _safe_str(row.get("signal_source")),
            }
            rows_out.append(out)

        tier_rank = {"A": 3, "B": 2, "C": 1}
        rows_out.sort(
            key=lambda r: (
                _safe_float(r.get("outreach_score"), 0),
                tier_rank.get(_safe_str(r.get("tier")).upper(), 0),
            ),
            reverse=True,
        )
        if len(rows_out) > top_n:
            rows_out = rows_out[:top_n]

        cold_csv = run_dir / "cold_email_priority_leads.csv"
        fields = [
            "lead_id",
            "venue_name",
            "city",
            "region",
            "venue_type",
            "website",
            "contact_name",
            "greeting",
            "contact_email",
            "email_source",
            "email_type",
            "contact_phone",
            "outreach_score",
            "confidence",
            "tier",
            "best_pitch_angle",
            "subject_line",
            "opening_line",
            "rationale",
            "proof_snippet_1",
            "proof_snippet_2",
            "validation_flags",
            "signal_source",
        ]
        with cold_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for row in rows_out:
                writer.writerow({k: row.get(k, "") for k in fields})
        self._insert_artifact(run_id, "cold_email_priority_csv", cold_csv, {"count": len(rows_out)})

        top_50 = rows_out[:50]
        top_50_csv = run_dir / "cold_email_top_50.csv"
        with top_50_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for row in top_50:
                writer.writerow({k: row.get(k, "") for k in fields})
        self._insert_artifact(run_id, "cold_email_top_50_csv", top_50_csv, {"count": len(top_50)})

        if needs_enrichment:
            needs_enrichment.sort(key=lambda r: _safe_float(r.get("score"), 0), reverse=True)
            enrich_csv = run_dir / "cold_email_enrichment_needed.csv"
            enrich_fields = [
                "lead_id",
                "venue_name",
                "city",
                "website",
                "contact_phone",
                "score",
                "tier",
                "best_pitch_angle",
                "suggested_role_email",
                "reason",
            ]
            with enrich_csv.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=enrich_fields)
                writer.writeheader()
                for row in needs_enrichment[:500]:
                    writer.writerow({k: row.get(k, "") for k in enrich_fields})
            self._insert_artifact(
                run_id,
                "cold_email_enrichment_needed_csv",
                enrich_csv,
                {"count": min(500, len(needs_enrichment))},
            )

        preview_md = run_dir / "cold_email_preview.md"
        lines = [f"# Cold Email Priority Preview (Run {run_id})", "", f"- Total prioritized: {len(rows_out)}", ""]
        for row in rows_out[:20]:
            lines.append(
                f"- {row['venue_name']} ({row['city']}) | score {row['outreach_score']} | "
                f"{row['contact_email']} | {row['subject_line']} | {row['greeting']}"
            )
        preview_md.write_text("\n".join(lines))
        self._insert_artifact(run_id, "cold_email_preview_md", preview_md, {"count": min(20, len(rows_out))})
        self._log_event(
            run_id,
            "leads",
            "info",
            "Cold email priority pack generated",
            {
                "count": len(rows_out),
                "enriched_count": enrich_count,
                "direct_count": direct_count,
                "role_based_count": role_count,
                "needs_enrichment_count": len(needs_enrichment),
                "min_score": min_score,
            },
        )
        if not rows_out:
            self._log_event(
                run_id,
                "leads",
                "warn",
                "No cold-email leads met quality threshold (likely missing verified emails).",
                {"needs_enrichment_count": len(needs_enrichment), "min_score": min_score},
            )
        return {
            "count": len(rows_out),
            "enriched_count": enrich_count,
            "direct_count": direct_count,
            "role_based_count": role_count,
            "needs_enrichment_count": len(needs_enrichment),
            "file_path": str(cold_csv),
            "top_50_path": str(top_50_csv),
        }

    async def _run_creative_phase(self, run_id: int, runtime: Dict[str, Any], run_dir: Path) -> SprintPhaseResult:
        if Image is None:
            return SprintPhaseResult(ok=False, outputs={}, error="Pillow is not available")

        source_images = self._source_images(runtime)
        source_videos = self._source_video_files(runtime)
        if not source_images:
            return SprintPhaseResult(ok=False, outputs={"source_images": 0}, error="No source images available")

        static_dir = run_dir / "creative" / "static"
        video_dir = run_dir / "creative" / "video"
        static_dir.mkdir(parents=True, exist_ok=True)
        video_dir.mkdir(parents=True, exist_ok=True)

        planned_static = max(runtime["target_statics_min"], min(runtime["target_statics_max"], len(LANGUAGES) * len(ANGLES) * len(CONTEXTS) * len(FORMATS)))
        planned_video = max(runtime["target_videos_min"], min(runtime["target_videos_max"], runtime["target_videos_min"]))

        static_out, static_rejects = self._generate_static_assets(run_id, source_images, static_dir, target=planned_static)
        video_sources = source_images if source_images else static_out
        video_out, video_rejects = await self._generate_videos(
            run_id,
            video_sources,
            video_dir,
            target=planned_video,
            runtime=runtime,
            source_videos=source_videos,
        )

        static_index = run_dir / "creative" / "static_manifest.json"
        video_index = run_dir / "creative" / "video_manifest.json"
        static_index.write_text(_json({"count": len(static_out), "files": [str(p) for p in static_out]}))
        video_index.write_text(_json({"count": len(video_out), "files": [str(p) for p in video_out]}))
        self._insert_artifact(run_id, "static_manifest", static_index, {"count": len(static_out), "rejected": static_rejects})
        self._insert_artifact(run_id, "video_manifest", video_index, {"count": len(video_out), "rejected": video_rejects})
        video_scores = video_dir / "video_scores.json"
        if video_scores.exists():
            self._insert_artifact(run_id, "video_scores", video_scores, {})
        video_feedback = video_dir / "video_feedback.json"
        if video_feedback.exists():
            self._insert_artifact(run_id, "video_feedback", video_feedback, {})

        ok = len(static_out) >= runtime["target_statics_min"] and len(video_out) >= runtime["target_videos_min"]
        notes = (
            f"Statics {len(static_out)} (rejected {static_rejects}), "
            f"Videos {len(video_out)} (rejected {video_rejects})."
        )
        if not ok:
            notes += " Below one or more creative targets."
        return SprintPhaseResult(
            ok=len(static_out) > 0 and len(video_out) > 0,
            outputs={
                "source_images": len(source_images),
                "source_videos": len(source_videos),
                "statics_usable": len(static_out),
                "videos_usable": len(video_out),
                "static_rejected": static_rejects,
                "video_rejected": video_rejects,
            },
            notes=notes,
            error="" if (len(static_out) > 0 and len(video_out) > 0) else "Creative output generation failed",
        )

    async def _run_campaign_phase(self, run_id: int, runtime: Dict[str, Any], run_dir: Path) -> SprintPhaseResult:
        # Campaign launch pack creates PAUSED objects only.
        try:
            from core.meta_ads import MetaAdsManager, TARGETING_PRESETS
        except Exception as e:
            return SprintPhaseResult(ok=False, outputs={}, error=f"MetaAds import failed: {e}")

        cfg = self._load_config()
        token = _safe_str(cfg.get("fb_page_access_token"))
        if not token:
            return SprintPhaseResult(ok=False, outputs={}, error="Missing fb_page_access_token")

        static_dir = run_dir / "creative" / "static"
        video_dir = run_dir / "creative" / "video"
        static_files = sorted(static_dir.glob("*.jpg"))[:18]
        video_files = sorted(video_dir.glob("*.mp4"))[:6]
        if not static_files:
            return SprintPhaseResult(ok=False, outputs={}, error="No static assets available for campaign build")

        mgr = MetaAdsManager()
        if not getattr(mgr, "_account", None):
            return SprintPhaseResult(ok=False, outputs={}, error="Meta ad account not initialized")

        day_tag = datetime.now().strftime("%Y%m%d")
        campaign_name = f"ZOAR_BROAD_MIX_{day_tag}_01"
        campaign = mgr.create_campaign(name=campaign_name, objective="OUTCOME_LEADS", budget_cents=3000, status="PAUSED")
        if campaign.get("error"):
            return SprintPhaseResult(ok=False, outputs={}, error=f"Campaign create failed: {campaign.get('error')}")

        campaign_id = _safe_str(campaign.get("id"))
        adsets_created = 0
        ads_created = 0
        created_records: List[Dict[str, Any]] = []
        form_id = _safe_str(cfg.get("fb_lead_form_id") or cfg.get("fb_form_id") or "")

        adset_specs = [
            ("EN", "broad_advantage_plus", {"locales": [6]}),
            ("ES", "broad_advantage_plus", {"locales": [24]}),
            ("MIX", "broad_advantage_plus", {"locales": [6, 24]}),
        ]

        # Upload media upfront (bounded)
        image_hashes: List[str] = []
        for img in static_files[:9]:
            try:
                image_hash = mgr.upload_image(str(img))
                if image_hash:
                    image_hashes.append(image_hash)
            except Exception as e:
                self._log_event(run_id, "campaign", "warn", "Image upload failed", {"path": str(img), "error": str(e)})

        video_ids: List[str] = []
        for vid in video_files[:3]:
            try:
                video_id = mgr.upload_video(str(vid))
                if video_id:
                    video_ids.append(video_id)
            except Exception as e:
                self._log_event(run_id, "campaign", "warn", "Video upload failed", {"path": str(vid), "error": str(e)})

        if not image_hashes and not video_ids:
            return SprintPhaseResult(ok=False, outputs={}, error="No media uploaded to Meta")

        for idx, (lang, preset, lang_targeting) in enumerate(adset_specs, start=1):
            adset_name = f"ZOAR_BROAD_{lang}_MIX_{day_tag}_{idx:02d}"
            base_targeting = json.loads(json.dumps(TARGETING_PRESETS.get(preset, TARGETING_PRESETS.get("broad_advantage_plus", {}))))
            base_targeting.update(dict(lang_targeting))
            adset = mgr.create_ad_set(
                campaign_id=campaign_id,
                name=adset_name,
                targeting_preset=None,
                targeting=base_targeting,
                budget_cents=1000,
                optimization_goal="LEAD_GENERATION",
            )
            if adset.get("error"):
                self._log_event(run_id, "campaign", "warn", "Adset create failed", {"name": adset_name, "error": adset.get("error")})
                continue
            adset_id = _safe_str(adset.get("id"))
            adsets_created += 1

            # 6 ads per adset (4 static + 2 video when available)
            media_pool: List[Tuple[str, str]] = []
            for h in image_hashes[:4]:
                media_pool.append(("image", h))
            for v in video_ids[:2]:
                media_pool.append(("video", v))
            for m_idx, (m_type, media_ref) in enumerate(media_pool, start=1):
                ad_tag = f"{lang}_{m_type}_{m_idx:02d}"
                ad_name = f"ZOAR_BROAD_{lang}_A_{day_tag}_{m_idx:02d}"
                creative = mgr.create_creative(
                    name=f"CR_{ad_name}",
                    image_hash_or_video_id=media_ref,
                    primary_text=self._primary_text(lang=lang),
                    headline=self._headline(lang=lang),
                    description=self._description(lang=lang),
                    cta="SIGN_UP",
                    link="https://zoarbathroomrental.com?utm_source=meta&utm_medium=paid&utm_campaign="
                    f"{campaign_name}&utm_content={ad_tag}",
                    form_id=form_id or None,
                )
                if creative.get("error"):
                    self._log_event(run_id, "campaign", "warn", "Creative create failed", {"ad": ad_name, "error": creative.get("error")})
                    continue
                ad = mgr.create_ad(ad_set_id=adset_id, creative_id=_safe_str(creative.get("id")), name=ad_name, status="PAUSED")
                if ad.get("error"):
                    self._log_event(run_id, "campaign", "warn", "Ad create failed", {"ad": ad_name, "error": ad.get("error")})
                    continue
                ads_created += 1
                created_records.append(
                    {
                        "campaign_id": campaign_id,
                        "adset_id": adset_id,
                        "creative_id": _safe_str(creative.get("id")),
                        "ad_id": _safe_str(ad.get("id")),
                        "lang": lang,
                        "media_type": m_type,
                        "name": ad_name,
                    }
                )

        out_json = run_dir / "campaign_build_manifest.json"
        out_json.write_text(_json({"campaign": campaign, "records": created_records}))
        self._insert_artifact(run_id, "campaign_manifest", out_json, {"ads": ads_created, "adsets": adsets_created})

        return SprintPhaseResult(
            ok=adsets_created > 0 and ads_created > 0,
            outputs={"campaigns_created": 1, "adsets_created": adsets_created, "ads_created": ads_created},
            notes=f"Paused launch pack created: campaigns=1 adsets={adsets_created} ads={ads_created}",
            error="" if (adsets_created > 0 and ads_created > 0) else "No campaign objects created",
        )

    # ── Creative generation helpers ─────────────────────────────────────

    def _primary_text(self, lang: str) -> str:
        if lang == "ES":
            return "Baños de lujo para eventos al aire libre. Desde $999. Entrega, instalación y retiro incluidos."
        return "Luxury restroom trailer rentals for outdoor events. Starting at $999. Delivery, setup, and pickup included."

    def _headline(self, lang: str) -> str:
        return "Luxury Event Restrooms" if lang != "ES" else "Baños de Lujo para Eventos"

    def _description(self, lang: str) -> str:
        return "LA + SFV coverage • Fast quote" if lang != "ES" else "Cobertura LA + SFV • Cotización rápida"

    def _copy_line(self, lang: str, angle: str, context: str) -> str:
        en = {
            "luxury": "Luxury comfort your guests notice.",
            "guest_comfort": "Keep guests comfortable all event long.",
            "urgency": "Peak dates fill fast. Reserve early.",
            "social_proof": "Trusted at weddings and private events.",
            "value_starting_at_999": "Premium setup starting at $999.",
        }
        es = {
            "luxury": "Comodidad de lujo para tus invitados.",
            "guest_comfort": "Invitados cómodos durante todo el evento.",
            "urgency": "Fechas pico se llenan rápido. Reserva hoy.",
            "social_proof": "Confiado para bodas y eventos privados.",
            "value_starting_at_999": "Servicio premium desde $999.",
        }
        suffix_en = {
            "wedding": "For outdoor weddings.",
            "quince": "Perfect for quince celebrations.",
            "private_outdoor": "Built for private outdoor events.",
            "corporate_outdoor": "Reliable for corporate outdoor events.",
        }
        suffix_es = {
            "wedding": "Ideal para bodas al aire libre.",
            "quince": "Perfecto para quinceañeras.",
            "private_outdoor": "Hecho para eventos privados al aire libre.",
            "corporate_outdoor": "Confiable para eventos corporativos al aire libre.",
        }
        if lang == "ES":
            return f"{es.get(angle, 'Baños de lujo para eventos.')} {suffix_es.get(context, '')}".strip()
        return f"{en.get(angle, 'Luxury restrooms for events.')} {suffix_en.get(context, '')}".strip()

    def _headline_copy(self, lang: str, angle: str) -> str:
        en = {
            "luxury": "Luxury Restrooms For Your Event",
            "guest_comfort": "Guest Comfort Without Compromise",
            "urgency": "Prime Dates Book Fast",
            "social_proof": "Trusted At Real LA Events",
            "value_starting_at_999": "Starting At $999",
        }
        es = {
            "luxury": "Baños De Lujo Para Tu Evento",
            "guest_comfort": "Comodidad Total Para Tus Invitados",
            "urgency": "Las Mejores Fechas Se Llenan Rápido",
            "social_proof": "Confiado En Eventos Reales De LA",
            "value_starting_at_999": "Desde $999",
        }
        return es.get(angle, "Baños De Lujo Para Eventos") if lang == "ES" else en.get(angle, "Luxury Event Restrooms")

    def _feature_lines(self, lang: str, context: str) -> List[str]:
        if lang == "ES":
            lines = [
                "4 stalls • descarga real",
                "agua corriente + A/C",
                "entrega + instalación + retiro",
            ]
            if context == "wedding":
                lines[0] = "Ideal para bodas al aire libre"
            elif context == "quince":
                lines[0] = "Perfecto para quinceañeras"
            elif context == "corporate_outdoor":
                lines[0] = "Listo para eventos corporativos"
            return lines
        lines = [
            "4 stalls • real flush toilets",
            "running water sinks + A/C",
            "delivery + setup + pickup included",
        ]
        if context == "wedding":
            lines[0] = "Built for outdoor weddings"
        elif context == "quince":
            lines[0] = "Perfect for quince celebrations"
        elif context == "corporate_outdoor":
            lines[0] = "Reliable for corporate events"
        return lines

    def _open_source_image(self, src: Path):
        if Image is None:
            raise RuntimeError("Pillow is required")
        with Image.open(src) as raw:
            im = raw.copy()
        if ImageOps is not None:
            try:
                im = ImageOps.exif_transpose(im)
            except Exception:
                pass
        if im.mode != "RGB":
            im = im.convert("RGB")
        return im

    def _font(self, size: int, *, bold: bool = False):
        if ImageFont is None:
            return None
        cache_name = "_font_cache"
        if not hasattr(self, cache_name):
            setattr(self, cache_name, {})
        cache: Dict[Tuple[int, bool], Any] = getattr(self, cache_name)
        key = (size, bold)
        if key in cache:
            return cache[key]
        candidates = []
        if bold:
            candidates.extend(
                [
                    "/System/Library/Fonts/HelveticaNeue.ttc",
                    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                ]
            )
        else:
            candidates.extend(
                [
                    "/System/Library/Fonts/HelveticaNeue.ttc",
                    "/System/Library/Fonts/Supplemental/Arial.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                ]
            )
        for c in candidates:
            if not Path(c).exists():
                continue
            try:
                font = ImageFont.truetype(c, size=size)
                cache[key] = font
                return font
            except Exception:
                continue
        font = ImageFont.load_default()
        cache[key] = font
        return font

    def _text_box(self, draw: Any, text: str, font: Any) -> Tuple[int, int]:
        try:
            box = draw.textbbox((0, 0), text, font=font)
            return box[2] - box[0], box[3] - box[1]
        except Exception:
            try:
                return draw.textsize(text, font=font)
            except Exception:
                return (len(text) * 8, 18)

    def _wrap_text(self, draw: Any, text: str, font: Any, max_width: int, max_lines: int = 3) -> List[str]:
        words = text.split()
        if not words:
            return [text]
        lines: List[str] = []
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}".strip()
            w, _ = self._text_box(draw, candidate, font)
            if w <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
                if len(lines) >= max_lines - 1:
                    break
        if current:
            lines.append(current)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
        if len(lines) == max_lines and words:
            last = lines[-1]
            while len(last) > 3:
                w, _ = self._text_box(draw, f"{last}…", font)
                if w <= max_width:
                    lines[-1] = f"{last}…"
                    break
                last = last[:-1]
        return lines

    def _rounded_rect(self, draw: Any, xy: Tuple[int, int, int, int], radius: int, fill: Tuple[int, int, int, int]):
        try:
            draw.rounded_rectangle(xy, radius=radius, fill=fill)
        except Exception:
            x0, y0, x1, y1 = xy
            draw.rectangle((x0, y0, x1, y1), fill=fill)

    def _compose_static(
        self,
        *,
        primary: Any,
        secondary: Sequence[Any],
        w: int,
        h: int,
        lang: str,
        angle: str,
        context: str,
        template: str,
    ):
        if Image is None or ImageDraw is None:
            raise RuntimeError("Pillow draw modules unavailable")

        accent = {
            "luxury": (219, 174, 88),
            "guest_comfort": (92, 197, 255),
            "urgency": (255, 117, 117),
            "social_proof": (116, 206, 155),
            "value_starting_at_999": (255, 203, 106),
        }.get(angle, (116, 184, 255))

        base = self._cover_resize(primary, w, h)
        canvas = Image.new("RGB", (w, h), (14, 17, 24))
        canvas.paste(base, (0, 0))
        draw = ImageDraw.Draw(canvas, "RGBA")

        # Add subtle top/bottom readability gradients.
        top_h = max(140, int(h * 0.16))
        bottom_h = max(260, int(h * 0.30))
        for y in range(top_h):
            alpha = int(140 * (1 - (y / max(1, top_h))))
            draw.rectangle((0, y, w, y + 1), fill=(6, 8, 14, alpha))
        for i in range(bottom_h):
            y = h - bottom_h + i
            alpha = int(210 * (i / max(1, bottom_h)))
            draw.rectangle((0, y, w, y + 1), fill=(8, 10, 18, alpha))

        headline = self._headline_copy(lang, angle)
        line = self._copy_line(lang, angle, context)
        features = self._feature_lines(lang, context)
        cta = "Get Quote Now" if lang != "ES" else "Cotiza Ahora"

        font_h = self._font(max(32, int(h * 0.040)), bold=True)
        font_body = self._font(max(22, int(h * 0.024)), bold=False)
        font_meta = self._font(max(18, int(h * 0.019)), bold=False)
        font_cta = self._font(max(22, int(h * 0.023)), bold=True)

        # Template variants avoid "single image + one bar" repetition.
        if template == "framed":
            frame_w = int(w * 0.86)
            frame_h = int(h * 0.56)
            fx = (w - frame_w) // 2
            fy = int(h * 0.11)
            draw.rectangle((fx - 10, fy - 10, fx + frame_w + 10, fy + frame_h + 10), fill=(0, 0, 0, 80))
            frame_img = self._cover_resize(primary, frame_w, frame_h)
            canvas.paste(frame_img, (fx, fy))
            if secondary:
                mini_w = int(frame_w * 0.30)
                mini_h = int(frame_h * 0.38)
                mini = self._cover_resize(secondary[0], mini_w, mini_h)
                mx = fx + frame_w - mini_w - 18
                my = fy + 18
                draw.rectangle((mx - 8, my - 8, mx + mini_w + 8, my + mini_h + 8), fill=(12, 15, 24, 190))
                canvas.paste(mini, (mx, my))
        elif template == "split":
            top_h_split = int(h * 0.62)
            left_w = int(w * 0.56)
            first = self._cover_resize(primary, left_w, top_h_split)
            canvas.paste(first, (0, 0))
            right_x = left_w
            right_w = w - left_w
            if secondary:
                second = self._cover_resize(secondary[0], right_w, top_h_split)
                canvas.paste(second, (right_x, 0))
            draw.rectangle((left_w - 2, 0, left_w + 2, top_h_split), fill=(255, 255, 255, 90))
        elif template == "collage":
            left_w = int(w * 0.62)
            left = self._cover_resize(primary, left_w, h)
            canvas.paste(left, (0, 0))
            right_w = w - left_w
            if secondary:
                top = self._cover_resize(secondary[0], right_w, h // 2)
                canvas.paste(top, (left_w, 0))
            if len(secondary) > 1:
                bot = self._cover_resize(secondary[1], right_w, h - (h // 2))
                canvas.paste(bot, (left_w, h // 2))
            draw.rectangle((left_w - 2, 0, left_w + 2, h), fill=(255, 255, 255, 70))
            draw.rectangle((left_w, h // 2 - 2, w, h // 2 + 2), fill=(255, 255, 255, 70))

        # Top brand chip.
        chip_pad_x = int(w * 0.03)
        chip_x0 = chip_pad_x
        chip_y0 = int(h * 0.03)
        chip_text = "Zoar Bathroom Rentals"
        tw, th = self._text_box(draw, chip_text, font_meta)
        chip_w = tw + 24
        chip_h = th + 16
        self._rounded_rect(draw, (chip_x0, chip_y0, chip_x0 + chip_w, chip_y0 + chip_h), 14, (11, 15, 26, 220))
        draw.text((chip_x0 + 12, chip_y0 + 8), chip_text, fill=(224, 231, 245, 255), font=font_meta)

        # Headline + body.
        text_x = int(w * 0.06)
        text_max_w = int(w * 0.88)
        y = int(h * 0.68)
        for line_head in self._wrap_text(draw, headline, font_h, text_max_w, max_lines=2):
            draw.text((text_x + 2, y + 2), line_head, fill=(0, 0, 0, 145), font=font_h)
            draw.text((text_x, y), line_head, fill=(248, 250, 255, 255), font=font_h)
            _, lh = self._text_box(draw, line_head, font_h)
            y += lh + 6
        y += 8
        for body_line in self._wrap_text(draw, line, font_body, text_max_w, max_lines=2):
            draw.text((text_x, y), body_line, fill=(220, 226, 238, 246), font=font_body)
            _, lh = self._text_box(draw, body_line, font_body)
            y += lh + 4
        y += 6
        for feature in features[:2]:
            bullet = f"• {feature}"
            draw.text((text_x, y), bullet, fill=(196, 208, 228, 245), font=font_meta)
            _, lh = self._text_box(draw, bullet, font_meta)
            y += lh + 3

        # CTA.
        cta_w, cta_h = self._text_box(draw, cta, font_cta)
        btn_x = text_x
        btn_pad_x = 22
        btn_pad_y = 12
        btn_h_total = cta_h + (btn_pad_y * 2)
        btn_y = min(h - int(h * 0.11), y + 12)
        btn_y = min(btn_y, h - btn_h_total - 14)
        btn_y = max(int(h * 0.12), btn_y)
        self._rounded_rect(
            draw,
            (btn_x, btn_y, btn_x + cta_w + (btn_pad_x * 2), btn_y + cta_h + (btn_pad_y * 2)),
            16,
            (accent[0], accent[1], accent[2], 238),
        )
        draw.text((btn_x + btn_pad_x, btn_y + btn_pad_y), cta, fill=(17, 22, 31, 255), font=font_cta)
        draw.text((w - int(w * 0.34), h - int(h * 0.05)), "zoarbathroomrental.com", fill=(190, 203, 224, 230), font=font_meta)

        if ImageEnhance is not None:
            canvas = ImageEnhance.Contrast(canvas).enhance(1.03)
        return canvas

    def _generate_static_assets(
        self,
        run_id: int,
        source_images: List[Path],
        out_dir: Path,
        target: int,
    ) -> Tuple[List[Path], int]:
        out: List[Path] = []
        rejected = 0
        if Image is None:
            return out, target

        concepts = [(lang, angle, ctx) for lang in LANGUAGES for angle in ANGLES for ctx in CONTEXTS]
        random.shuffle(concepts)
        source_cycle = list(source_images)
        random.shuffle(source_cycle)
        if not source_cycle:
            return out, target

        templates = ("hero", "framed", "split", "collage")
        idx = 0
        attempts = 0
        max_attempts = max(target * 8, target + 20)
        while len(out) < target and attempts < max_attempts:
            lang, angle, ctx = concepts[idx % len(concepts)]
            fmt_key = list(FORMATS.keys())[idx % len(FORMATS)]
            w, h = FORMATS[fmt_key]
            src = source_cycle[idx % len(source_cycle)]
            sec_1 = source_cycle[(idx + 7) % len(source_cycle)]
            sec_2 = source_cycle[(idx + 13) % len(source_cycle)]
            template = templates[idx % len(templates)]
            idx += 1
            attempts += 1
            file_name = f"{lang}_{angle}_{ctx}_{fmt_key}_{idx:04d}.jpg"
            dst = out_dir / file_name

            try:
                primary = self._open_source_image(src)
                secondary = [self._open_source_image(sec_1), self._open_source_image(sec_2)]
                im = self._compose_static(
                    primary=primary,
                    secondary=secondary,
                    w=w,
                    h=h,
                    lang=lang,
                    angle=angle,
                    context=ctx,
                    template=template,
                )

                if not self._passes_image_quality(im):
                    rejected += 1
                    continue
                im.save(dst, format="JPEG", quality=92, optimize=True)
                out.append(dst)
            except Exception as e:
                rejected += 1
                self._log_event(run_id, "creative", "warn", "Static asset rejected", {"source": str(src), "error": str(e)})
                continue
        return out, rejected

    def _ffmpeg_text(self, text: str) -> str:
        return (
            text.replace("\\", "\\\\")
            .replace(":", r"\:")
            .replace("'", r"\'")
            .replace(",", r"\,")
            .replace("[", r"\[")
            .replace("]", r"\]")
        )

    def _video_copy_pack(self, idx: int) -> Dict[str, str]:
        packs = [
            {
                "hook": "Need Restrooms For Your Event?",
                "sub": "Luxury 4-stall trailer • Clean • Climate controlled",
                "cta": "Get quote now • Starting at $999",
            },
            {
                "hook": "Outdoor Wedding Coming Up?",
                "sub": "Give guests real comfort, not porta-potty lines",
                "cta": "Check date availability today",
            },
            {
                "hook": "Bookings Increase When Guests Feel Comfortable",
                "sub": "Running water sinks + flush toilets + premium interior",
                "cta": "Reserve your date in 60 seconds",
            },
            {
                "hook": "Hosting A Private Outdoor Event?",
                "sub": "Upgrade restroom experience with Zoar",
                "cta": "Fast quote • LA + SFV coverage",
            },
            {
                "hook": "Premium Restrooms. Zero Headaches.",
                "sub": "Delivery, setup, and pickup included",
                "cta": "Tap to get event pricing",
            },
        ]
        return packs[idx % len(packs)]

    def _voice_script(self, copy: Dict[str, str]) -> str:
        raw = f"{copy.get('hook', '')}. {copy.get('sub', '')}. {copy.get('cta', '')}."
        text = re.sub(r"[•|]", ", ", raw)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _elevenlabs_api_key(self) -> str:
        env_key = _safe_str((__import__("os").environ.get("ELEVENLABS_API_KEY") or "").strip())
        if env_key:
            return env_key
        cfg = self._load_config()
        for k in ("elevenlabs_api_key", "eleven_labs_api_key", "elevenlabs_key"):
            v = _safe_str(cfg.get(k, ""))
            if v:
                return v
        runpod_cfg = cfg.get("runpod") if isinstance(cfg.get("runpod"), dict) else {}
        for k in ("elevenlabs_api_key", "eleven_labs_api_key", "elevenlabs_key"):
            v = _safe_str(runpod_cfg.get(k, ""))
            if v:
                return v
        return ""

    def _elevenlabs_voice_id(self) -> str:
        cfg = self._load_config()
        v = _safe_str(cfg.get("elevenlabs_voice_id", ""))
        if v:
            return v
        runpod_cfg = cfg.get("runpod") if isinstance(cfg.get("runpod"), dict) else {}
        rv = _safe_str(runpod_cfg.get("elevenlabs_voice_id", ""))
        if rv:
            return rv
        return "21m00Tcm4TlvDq8ikWAM"

    def _voice_cache_path(self, text: str, voice_id: str, model_id: str = "eleven_multilingual_v2") -> Path:
        VOICE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha1(f"{voice_id}|{model_id}|{text}".encode("utf-8")).hexdigest()
        return VOICE_CACHE_DIR / f"{key}.mp3"

    def _synthesize_voiceover_elevenlabs(self, text: str) -> Optional[Path]:
        api_key = self._elevenlabs_api_key()
        if not api_key or httpx is None:
            return None
        voice_id = self._elevenlabs_voice_id()
        model_id = "eleven_multilingual_v2"
        cached = self._voice_cache_path(text, voice_id, model_id)
        if cached.exists() and cached.stat().st_size > 2_000:
            return cached
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        payload = {
            "text": text,
            "model_id": model_id,
            "voice_settings": {
                "stability": 0.42,
                "similarity_boost": 0.80,
                "style": 0.30,
                "use_speaker_boost": True,
            },
        }
        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(
                    url,
                    headers={
                        "xi-api-key": api_key,
                        "accept": "audio/mpeg",
                        "content-type": "application/json",
                    },
                    json=payload,
                )
            if resp.status_code == 200 and resp.content:
                cached.write_bytes(resp.content)
                if cached.stat().st_size > 2_000:
                    return cached
            return None
        except Exception:
            return None

    def _synthesize_music_bed(self, duration: float, dst: Path) -> Optional[Path]:
        if not FFMPEG_BIN:
            return None
        fade_out = max(0.6, duration - 1.2)
        cmd = [
            FFMPEG_BIN or "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=98:sample_rate=44100:duration={duration:.2f}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=196:sample_rate=44100:duration={duration:.2f}",
            "-f",
            "lavfi",
            "-i",
            f"anoisesrc=color=pink:sample_rate=44100:duration={duration:.2f}",
            "-filter_complex",
            (
                "[0:a]volume=0.06,lowpass=f=240[bass];"
                "[1:a]volume=0.03,lowpass=f=1200,bandpass=f=540:w=140[mid];"
                "[2:a]volume=0.012,highpass=f=2800[air];"
                f"[bass][mid][air]amix=inputs=3:normalize=0,afade=t=in:st=0:d=0.8,"
                f"afade=t=out:st={fade_out:.2f}:d=1.0[aout]"
            ),
            "-map",
            "[aout]",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(dst),
        ]
        rc = self._run_subprocess(cmd)
        if rc == 0 and dst.exists() and dst.stat().st_size > 6_000:
            return dst
        return None

    def _pick_music_track(self, duration: float, tmp_dir: Optional[Path] = None, seed: int = 0) -> Optional[Path]:
        bases = [VIDEO_MUSIC_DIR, VIDEO_MUSIC_FALLBACK_DIR]
        candidates: List[Path] = []
        for base in bases:
            if not base.exists():
                continue
            candidates.extend(base.rglob("*.mp3"))
            candidates.extend(base.rglob("*.wav"))
            candidates.extend(base.rglob("*.m4a"))
            candidates.extend(base.rglob("*.aac"))
        if candidates:
            ordered = sorted(candidates, key=lambda p: str(p))
            return ordered[int(seed) % len(ordered)]
        if tmp_dir is None:
            return None
        synth = tmp_dir / "generated_music_bed.m4a"
        return self._synthesize_music_bed(duration, synth)

    def _synthesize_voiceover(self, text: str, dst: Path, allow_local_fallback: bool = False) -> Optional[Path]:
        eleven = self._synthesize_voiceover_elevenlabs(text)
        if eleven is not None and eleven.exists():
            return eleven
        if not allow_local_fallback:
            return None
        say_bin = shutil.which("say")
        if not say_bin:
            return None
        # Use built-in macOS voice for fast, local TTS with no external API cost.
        cmd = [say_bin, "-v", "Samantha", "-r", "184", "-o", str(dst), text]
        rc = self._run_subprocess(cmd)
        if rc == 0 and dst.exists() and dst.stat().st_size > 1_000:
            return dst
        return None

    def _render_video_overlay(self, dst: Path, copy: Dict[str, str]) -> None:
        if Image is None or ImageDraw is None:
            raise RuntimeError("Pillow is required for video overlays")
        w, h = 1080, 1920
        ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(ov, "RGBA")
        draw.rectangle((0, 0, w, 240), fill=(10, 15, 24, 152))
        draw.rectangle((0, h - 260, w, h), fill=(10, 15, 24, 178))

        font_hook = self._font(58, bold=True)
        font_sub = self._font(34, bold=False)
        font_cta = self._font(46, bold=True)
        font_url = self._font(30, bold=False)

        hook_lines = self._wrap_text(draw, copy["hook"], font_hook, int(w * 0.90), max_lines=2)
        y = 58
        for line in hook_lines:
            tw, th = self._text_box(draw, line, font_hook)
            x = (w - tw) // 2
            draw.text((x + 2, y + 2), line, fill=(0, 0, 0, 140), font=font_hook)
            draw.text((x, y), line, fill=(245, 248, 255, 255), font=font_hook)
            y += th + 8

        sub_lines = self._wrap_text(draw, copy["sub"], font_sub, int(w * 0.92), max_lines=2)
        y = 168
        for line in sub_lines[:2]:
            tw, th = self._text_box(draw, line, font_sub)
            x = (w - tw) // 2
            draw.text((x, y), line, fill=(214, 224, 242, 250), font=font_sub)
            y += th + 4

        cta = copy["cta"]
        ctw, cth = self._text_box(draw, cta, font_cta)
        btn_pad_x = 26
        btn_pad_y = 12
        btn_w = ctw + (btn_pad_x * 2)
        btn_h = cth + (btn_pad_y * 2)
        btn_x = (w - btn_w) // 2
        btn_y = h - 208
        self._rounded_rect(draw, (btn_x, btn_y, btn_x + btn_w, btn_y + btn_h), 16, (110, 194, 255, 234))
        draw.text((btn_x + btn_pad_x, btn_y + btn_pad_y), cta, fill=(18, 24, 35, 255), font=font_cta)

        url = "zoarbathroomrental.com"
        uw, uh = self._text_box(draw, url, font_url)
        draw.text(((w - uw) // 2, h - 78), url, fill=(202, 214, 236, 235), font=font_url)
        ov.save(dst, format="PNG")

    def _render_video_overlay_stage(self, dst: Path, text: str, stage: str) -> None:
        if Image is None or ImageDraw is None:
            raise RuntimeError("Pillow is required for video overlays")
        w, h = 1080, 1920
        ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(ov, "RGBA")
        font_main = self._font(54, bold=True)
        font_sub = self._font(36, bold=False)

        if stage == "hook":
            draw.rectangle((0, 0, w, 260), fill=(8, 13, 21, 176))
            lines = self._wrap_text(draw, text, font_main, int(w * 0.90), max_lines=2)
            y = 60
            for line in lines:
                tw, th = self._text_box(draw, line, font_main)
                x = (w - tw) // 2
                draw.text((x + 2, y + 2), line, fill=(0, 0, 0, 135), font=font_main)
                draw.text((x, y), line, fill=(246, 249, 255, 255), font=font_main)
                y += th + 10
        elif stage == "proof":
            draw.rectangle((0, h - 600, w, h - 320), fill=(8, 13, 21, 164))
            lines = self._wrap_text(draw, text, font_sub, int(w * 0.90), max_lines=3)
            y = h - 560
            for line in lines:
                tw, th = self._text_box(draw, line, font_sub)
                x = (w - tw) // 2
                draw.text((x, y), line, fill=(228, 236, 250, 255), font=font_sub)
                y += th + 8
        else:
            draw.rectangle((0, h - 300, w, h), fill=(8, 13, 21, 192))
            cta_font = self._font(48, bold=True)
            ctw, cth = self._text_box(draw, text, cta_font)
            pad_x = 24
            pad_y = 10
            bw = ctw + (pad_x * 2)
            bh = cth + (pad_y * 2)
            bx = (w - bw) // 2
            by = h - 230
            self._rounded_rect(draw, (bx, by, bx + bw, by + bh), 16, (110, 194, 255, 235))
            draw.text((bx + pad_x, by + pad_y), text, fill=(18, 24, 35, 255), font=cta_font)
            url = "zoarbathroomrental.com"
            url_font = self._font(30, bold=False)
            uw, _ = self._text_box(draw, url, url_font)
            draw.text(((w - uw) // 2, h - 82), url, fill=(204, 216, 238, 248), font=url_font)
        ov.save(dst, format="PNG")

    def _score_video_candidate(
        self,
        *,
        duration: float,
        scene_count: int,
        video_scene_count: int,
        has_music: bool,
        has_voice: bool,
        motion_modes: Sequence[str],
    ) -> Tuple[int, List[str]]:
        score = 20
        notes: List[str] = []
        if 9.8 <= duration <= 14.8:
            score += 8
            notes.append("duration_in_meta_window")
        else:
            score -= 6
            notes.append("duration_outside_meta_window")

        if 5 <= scene_count <= 7:
            score += 14
            notes.append("fast_scene_density")
        elif scene_count == 4:
            score += 8
            notes.append("medium_scene_density")
        else:
            score += 2
            notes.append("slow_scene_density")

        if 2 <= video_scene_count <= 5:
            score += 20
            notes.append("video_scene_mix_ideal")
        elif video_scene_count >= 1:
            score += 12
            notes.append("video_scene_mix_ok")
        else:
            score -= 16
            notes.append("no_live_video_clips")

        if video_scene_count == 0:
            pass
        elif video_scene_count == scene_count:
            score -= 6
            notes.append("too_many_live_scenes")
        elif video_scene_count >= 2:
            notes.append("has_live_video_clips")

        unique_motion = len(set([m for m in motion_modes if m]))
        score += min(9, max(0, (unique_motion - 1) * 3))
        if unique_motion >= 3:
            notes.append("motion_variety_good")
        elif unique_motion <= 1:
            score -= 5
            notes.append("motion_variety_low")

        if has_voice:
            score += 12
            notes.append("voiceover_on")
        else:
            score -= 10
            notes.append("voiceover_off")
        if has_music:
            score += 8
            notes.append("music_bed_on")
        else:
            score -= 5
            notes.append("music_bed_off")

        return max(0, min(100, int(round(score)))), notes

    async def _generate_videos(
        self,
        run_id: int,
        static_files: List[Path],
        out_dir: Path,
        target: int,
        runtime: Optional[Dict[str, Any]] = None,
        source_videos: Optional[List[Path]] = None,
    ) -> Tuple[List[Path], int]:
        out: List[Path] = []
        rejected = 0
        if not static_files:
            return out, target

        runtime = runtime or {}
        use_music = bool(runtime.get("video_use_music", True))
        use_voice = bool(runtime.get("video_use_voiceover", True))
        allow_local_tts = bool(runtime.get("video_local_tts_fallback", False))
        candidate_mult = max(1, _safe_int(runtime.get("video_candidate_multiplier"), 3))

        srcs = list(static_files)
        random.shuffle(srcs)
        video_pool = list(source_videos or [])
        video_meta_cache: Dict[str, Dict[str, Any]] = {}
        candidates: List[Dict[str, Any]] = []

        candidate_target = max(target, target * candidate_mult)
        for idx in range(candidate_target):
            duration = random.uniform(10.5, 13.5)
            dst = out_dir / f"ad_{idx + 1:03d}.mp4"
            scene_count = max(5, min(7, len(srcs) if len(srcs) >= 5 else 5))
            transition = 0.34
            scene_duration = (duration + (transition * (scene_count - 1))) / max(1, scene_count)

            with tempfile.TemporaryDirectory(prefix="nexus_vidsrc_") as td:
                tmp_dir = Path(td)
                scene_plan: List[Dict[str, Any]] = []
                motion_modes: List[str] = []
                try:
                    for scene_idx in range(scene_count):
                        use_video_scene = bool(video_pool) and (scene_idx % 2 == 1 or random.random() < 0.45)
                        if use_video_scene:
                            vid = video_pool[(idx + scene_idx) % len(video_pool)]
                            key = str(vid)
                            if key not in video_meta_cache:
                                video_meta_cache[key] = self._ffprobe_video_meta(vid)
                            vmeta = video_meta_cache.get(key) or {}
                            vdur = _safe_float(vmeta.get("duration"), 0)
                            start_max = max(0.0, vdur - scene_duration - 0.1)
                            clip_start = random.uniform(0.0, start_max) if start_max > 0.05 else 0.0
                            scene_plan.append({"kind": "video", "path": vid, "start": clip_start, "mode": "live"})
                            motion_modes.append("live")
                        else:
                            src = srcs[(idx * scene_count + (scene_idx * 3)) % len(srcs)]
                            im = self._open_source_image(src)
                            tmp_img = tmp_dir / f"scene_{scene_idx:02d}.jpg"
                            im.save(tmp_img, format="JPEG", quality=94, optimize=True)
                            mode = random.choice(["zoom_in", "zoom_out", "pan_lr", "pan_rl", "pan_ud"])
                            scene_plan.append({"kind": "image", "path": tmp_img, "mode": mode})
                            motion_modes.append(mode)
                except Exception as e:
                    rejected += 1
                    self._log_event(
                        run_id,
                        "creative",
                        "warn",
                        "Video source normalize failed",
                        {"error": str(e), "video_index": idx + 1},
                    )
                    continue

                copy = self._video_copy_pack(idx)
                voice_script = self._voice_script(copy)
                hook_png = tmp_dir / "overlay_hook.png"
                proof_png = tmp_dir / "overlay_proof.png"
                cta_png = tmp_dir / "overlay_cta.png"
                try:
                    self._render_video_overlay_stage(hook_png, copy.get("hook", ""), "hook")
                    self._render_video_overlay_stage(proof_png, copy.get("sub", ""), "proof")
                    self._render_video_overlay_stage(cta_png, copy.get("cta", ""), "cta")
                except Exception as e:
                    rejected += 1
                    self._log_event(
                        run_id,
                        "creative",
                        "warn",
                        "Video overlay render failed",
                        {"error": str(e), "video_index": idx + 1},
                    )
                    continue

                music_track: Optional[Path] = None
                if use_music:
                    music_track = self._pick_music_track(duration=duration, tmp_dir=tmp_dir, seed=idx)

                voice_track: Optional[Path] = None
                if use_voice:
                    voice_track = self._synthesize_voiceover(voice_script, tmp_dir / "voice.aiff", allow_local_fallback=allow_local_tts)

                cmd = [FFMPEG_BIN or "ffmpeg", "-y"]
                for scene in scene_plan:
                    if scene.get("kind") == "video":
                        cmd.extend(
                            [
                                "-ss",
                                f"{_safe_float(scene.get('start'), 0):.3f}",
                                "-t",
                                f"{scene_duration + transition:.3f}",
                                "-i",
                                str(scene.get("path")),
                            ]
                        )
                    else:
                        cmd.extend(["-loop", "1", "-t", f"{scene_duration + transition:.3f}", "-i", str(scene.get("path"))])

                cmd.extend(["-loop", "1", "-t", f"{duration:.2f}", "-i", str(hook_png)])
                cmd.extend(["-loop", "1", "-t", f"{duration:.2f}", "-i", str(proof_png)])
                cmd.extend(["-loop", "1", "-t", f"{duration:.2f}", "-i", str(cta_png)])

                overlay_hook_idx = scene_count
                overlay_proof_idx = scene_count + 1
                overlay_cta_idx = scene_count + 2
                next_input_idx = overlay_cta_idx + 1
                music_idx = -1
                voice_idx = -1
                if music_track is not None:
                    if str(music_track).lower().endswith((".mp3", ".wav", ".m4a", ".aac")):
                        cmd.extend(["-stream_loop", "-1", "-i", str(music_track)])
                    else:
                        cmd.extend(["-i", str(music_track)])
                    music_idx = next_input_idx
                    next_input_idx += 1
                if voice_track is not None:
                    cmd.extend(["-i", str(voice_track)])
                    voice_idx = next_input_idx
                    next_input_idx += 1

                filter_parts: List[str] = []
                for scene_idx, scene in enumerate(scene_plan):
                    mode = _safe_str(scene.get("mode"))
                    if scene.get("kind") == "video":
                        filter_parts.append(
                            f"[{scene_idx}:v]"
                            f"scale=1240:2200:force_original_aspect_ratio=increase,"
                            f"crop=1080:1920:x='(iw-1080)/2':y='(ih-1920)/2',"
                            f"eq=contrast=1.05:saturation=1.08,"
                            f"trim=duration={scene_duration:.3f},setpts=PTS-STARTPTS,fps=30,settb=1/30,format=yuv420p[v{scene_idx}]"
                        )
                        continue

                    frames = max(30, int(scene_duration * 30))
                    if mode == "zoom_out":
                        zoom_expr = "max(1.00,1.11-0.0010*on)"
                        filter_parts.append(
                            f"[{scene_idx}:v]"
                            f"scale=1700:3022:force_original_aspect_ratio=increase,"
                            f"zoompan=z='{zoom_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1920:fps=30,"
                            f"trim=duration={scene_duration:.3f},setpts=PTS-STARTPTS,fps=30,settb=1/30,format=yuv420p[v{scene_idx}]"
                        )
                    elif mode == "pan_lr":
                        filter_parts.append(
                            f"[{scene_idx}:v]"
                            f"scale=1700:3022:force_original_aspect_ratio=increase,"
                            f"crop=1080:1920:x='(iw-1080)*min(1,n/{frames})':y='(ih-1920)/2',"
                            f"trim=duration={scene_duration:.3f},setpts=PTS-STARTPTS,fps=30,settb=1/30,format=yuv420p[v{scene_idx}]"
                        )
                    elif mode == "pan_rl":
                        filter_parts.append(
                            f"[{scene_idx}:v]"
                            f"scale=1700:3022:force_original_aspect_ratio=increase,"
                            f"crop=1080:1920:x='(iw-1080)*(1-min(1,n/{frames}))':y='(ih-1920)/2',"
                            f"trim=duration={scene_duration:.3f},setpts=PTS-STARTPTS,fps=30,settb=1/30,format=yuv420p[v{scene_idx}]"
                        )
                    elif mode == "pan_ud":
                        filter_parts.append(
                            f"[{scene_idx}:v]"
                            f"scale=1700:3022:force_original_aspect_ratio=increase,"
                            f"crop=1080:1920:x='(iw-1080)/2':y='(ih-1920)*min(1,n/{frames})',"
                            f"trim=duration={scene_duration:.3f},setpts=PTS-STARTPTS,fps=30,settb=1/30,format=yuv420p[v{scene_idx}]"
                        )
                    else:
                        zoom_expr = "min(1.12,1+0.0012*on)"
                        filter_parts.append(
                            f"[{scene_idx}:v]"
                            f"scale=1700:3022:force_original_aspect_ratio=increase,"
                            f"zoompan=z='{zoom_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1920:fps=30,"
                            f"trim=duration={scene_duration:.3f},setpts=PTS-STARTPTS,fps=30,settb=1/30,format=yuv420p[v{scene_idx}]"
                        )

                chain = "v0"
                for scene_idx in range(1, scene_count):
                    out_label = "mix" if scene_idx == scene_count - 1 else f"mix{scene_idx}"
                    offset = scene_idx * (scene_duration - transition)
                    filter_parts.append(
                        f"[{chain}][v{scene_idx}]xfade=transition=fade:duration={transition:.3f}:offset={offset:.3f}[{out_label}]"
                    )
                    chain = out_label

                hook_end = min(duration * 0.34, 4.2)
                proof_start = max(1.6, hook_end - 0.45)
                proof_end = min(duration * 0.72, duration - 2.6)
                cta_start = max(duration - 3.0, proof_end - 0.15)
                filter_parts.append(f"[{chain}][{overlay_hook_idx}:v]overlay=0:0:enable='between(t,0,{hook_end:.2f})'[ovh]")
                filter_parts.append(
                    f"[ovh][{overlay_proof_idx}:v]overlay=0:0:enable='between(t,{proof_start:.2f},{proof_end:.2f})'[ovp]"
                )
                filter_parts.append(
                    f"[ovp][{overlay_cta_idx}:v]overlay=0:0:enable='between(t,{cta_start:.2f},{duration:.2f})'[vout]"
                )
                has_audio = False
                if music_idx >= 0:
                    fade_out_start = max(0.1, duration - 0.9)
                    filter_parts.append(
                        f"[{music_idx}:a]"
                        f"atrim=duration={duration:.2f},asetpts=PTS-STARTPTS,"
                        f"afade=t=in:st=0:d=0.5,afade=t=out:st={fade_out_start:.2f}:d=0.8,"
                        f"highpass=f=40,lowpass=f=7800,volume=0.28[musicbed]"
                    )
                    has_audio = True
                if voice_idx >= 0:
                    filter_parts.append(
                        f"[{voice_idx}:a]"
                        f"atrim=duration={max(0.1, duration - 0.2):.2f},asetpts=PTS-STARTPTS,"
                        f"adelay=220|220,highpass=f=95,lowpass=f=7600,"
                        f"acompressor=threshold=-18dB:ratio=3:attack=25:release=180,volume=1.45[voiceover]"
                    )
                    has_audio = True

                if music_idx >= 0 and voice_idx >= 0:
                    filter_parts.append("[musicbed][voiceover]amix=inputs=2:duration=first:dropout_transition=2[aout]")
                elif music_idx >= 0:
                    filter_parts.append("[musicbed]anull[aout]")
                elif voice_idx >= 0:
                    filter_parts.append("[voiceover]anull[aout]")

                cmd.extend(
                    [
                        "-filter_complex",
                        ";".join(filter_parts),
                        "-map",
                        "[vout]",
                    ]
                )
                if has_audio:
                    cmd.extend(["-map", "[aout]", "-c:a", "aac", "-b:a", "160k"])
                cmd.extend(
                    [
                        "-r",
                        "30",
                        "-t",
                        f"{duration:.2f}",
                        "-c:v",
                        "libx264",
                        "-profile:v",
                        "high",
                        "-preset",
                        "slow",
                        "-crf",
                        "17",
                        "-pix_fmt",
                        "yuv420p",
                        "-movflags",
                        "+faststart",
                        str(dst),
                    ]
                )

                rc = await asyncio.to_thread(self._run_subprocess, cmd)
                if rc != 0 or (not dst.exists()) or dst.stat().st_size < 180_000:
                    rejected += 1
                    self._log_event(
                        run_id,
                        "creative",
                        "warn",
                        "Video asset rejected",
                        {"target": str(dst), "return_code": rc, "scene_count": scene_count},
                    )
                    try:
                        if dst.exists():
                            dst.unlink()
                    except Exception:
                        pass
                    continue
                video_scene_count = sum(1 for s in scene_plan if s.get("kind") == "video")
                score, notes = self._score_video_candidate(
                    duration=duration,
                    scene_count=scene_count,
                    video_scene_count=video_scene_count,
                    has_music=(music_track is not None),
                    has_voice=(voice_track is not None),
                    motion_modes=motion_modes,
                )
                candidates.append(
                    {
                        "path": str(dst),
                        "score": score,
                        "notes": notes,
                        "duration": round(duration, 2),
                        "scene_count": scene_count,
                        "video_scene_count": video_scene_count,
                        "has_music": bool(music_track is not None),
                        "has_voice": bool(voice_track is not None),
                        "motion_modes": motion_modes,
                    }
                )

        candidates.sort(key=lambda c: _safe_int(c.get("score"), 0), reverse=True)
        keep = candidates[:target]
        keep_paths = {str(c.get("path")) for c in keep}

        for c in candidates[target:]:
            try:
                p = Path(_safe_str(c.get("path")))
                if p.exists():
                    p.unlink()
            except Exception:
                pass

        out = [Path(_safe_str(c.get("path"))) for c in keep if _safe_str(c.get("path"))]
        score_payload: List[Dict[str, Any]] = []
        for rank, c in enumerate(candidates, start=1):
            row = dict(c)
            row["rank"] = rank
            row["kept"] = bool(_safe_str(c.get("path")) in keep_paths)
            score_payload.append(row)
        try:
            (out_dir / "video_scores.json").write_text(_json({"total_candidates": len(candidates), "kept": len(out), "scores": score_payload}))
        except Exception:
            pass
        try:
            kept_scores = [_safe_int(c.get("score"), 0) for c in keep]
            all_scores = [_safe_int(c.get("score"), 0) for c in candidates]
            avg_kept = round(sum(kept_scores) / max(1, len(kept_scores)), 2)
            avg_all = round(sum(all_scores) / max(1, len(all_scores)), 2)

            def _mode_freq(rows: Sequence[Dict[str, Any]]) -> Dict[str, int]:
                freq: Dict[str, int] = {}
                for r in rows:
                    for m in (r.get("motion_modes") or []):
                        k = _safe_str(m) or "unknown"
                        freq[k] = freq.get(k, 0) + 1
                return dict(sorted(freq.items(), key=lambda kv: kv[1], reverse=True))

            top_freq = _mode_freq(keep)
            bottom = candidates[-max(1, len(keep)) :] if candidates else []
            low_freq = _mode_freq(bottom)
            feedback = {
                "summary": {
                    "candidates": len(candidates),
                    "kept": len(keep),
                    "avg_score_kept": avg_kept,
                    "avg_score_all": avg_all,
                },
                "more_of": list(top_freq.keys())[:3],
                "less_of": [k for k in list(low_freq.keys()) if k not in set(list(top_freq.keys())[:3])][:2],
                "top_motion_modes": top_freq,
                "lowest_motion_modes": low_freq,
                "notes": [
                    "Prefer mixes with 2-5 live video scenes plus varied motion modes.",
                    "Avoid all-live or all-static compositions.",
                    "Keep hook early, proof mid, CTA final 2-3 seconds.",
                ],
            }
            (out_dir / "video_feedback.json").write_text(_json(feedback))
        except Exception:
            pass

        pruned = max(0, len(candidates) - len(out))
        return out, (rejected + pruned)

    def _run_subprocess(self, cmd: List[str]) -> int:
        try:
            p = subprocess.run(cmd, capture_output=True, text=True)
            if int(p.returncode) != 0 and _safe_str((__import__("os").environ.get("NEXUS_DEBUG_SUBPROCESS") or "")) == "1":
                try:
                    msg = _safe_str(p.stderr or p.stdout or "")
                    print(f"[subprocess-error] rc={p.returncode} cmd={' '.join(cmd[:8])} ...")
                    if msg:
                        tail = msg[-2200:] if len(msg) > 2200 else msg
                        print(tail)
                except Exception:
                    pass
            return int(p.returncode)
        except Exception:
            return 99

    def _cover_resize(self, im: Any, w: int, h: int):
        src_w, src_h = im.size
        ratio = max(w / max(1, src_w), h / max(1, src_h))
        nw, nh = int(src_w * ratio), int(src_h * ratio)
        if Image is not None and hasattr(Image, "Resampling"):
            resample = Image.Resampling.LANCZOS
        else:
            resample = Image.LANCZOS if Image is not None else 1
        resized = im.resize((nw, nh), resample)
        left = max(0, (nw - w) // 2)
        top = max(0, (nh - h) // 2)
        return resized.crop((left, top, left + w, top + h))

    def _passes_image_quality(self, im: Any) -> bool:
        if ImageStat is None:
            return True
        stat = ImageStat.Stat(im)
        mean = sum(stat.mean) / max(1, len(stat.mean))
        if mean < 18 or mean > 238:
            return False
        # simple color richness check
        if sum(stat.stddev) / max(1, len(stat.stddev)) < 12:
            return False
        return True

    # ── Report / bookkeeping helpers ────────────────────────────────────

    def _write_report(self, run_id: int, runtime: Dict[str, Any], outputs: Dict[str, Any], planned: Dict[str, Any]) -> Path:
        run_dir = RUNS_ROOT / f"run_{run_id}"
        run_dir.mkdir(parents=True, exist_ok=True)
        csv_summary = run_dir / "output_summary.csv"
        with csv_summary.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["metric", "value"])
            for k in sorted(outputs.keys()):
                writer.writerow([k, outputs.get(k)])
            writer.writerow(["max_credits_usd", runtime.get("max_credits_usd")])
            writer.writerow(["phase_caps", ",".join(str(x) for x in runtime.get("phase_caps", []))])

        md = [
            f"# Runpod Sprint Report #{run_id}",
            "",
            f"- Completed at: {_now()}",
            f"- Max credits cap: ${runtime.get('max_credits_usd')}",
            f"- Safety mode: `{runtime.get('safety_mode')}`",
            f"- Launch mode: `{runtime.get('launch_mode')}`",
            "",
            "## Outputs",
            f"- Validated leads: {outputs.get('leads_validated', 0)}",
            f"- Cold-email priority leads: {outputs.get('cold_email_priority_count', 0)}",
            f"- Cold-email enriched contacts: {outputs.get('cold_email_enriched_count', 0)}",
            f"- Cold-email leads needing enrichment: {outputs.get('cold_email_needs_enrichment_count', 0)}",
            f"- Usable statics: {outputs.get('statics_usable', 0)}",
            f"- Usable videos: {outputs.get('videos_usable', 0)}",
            f"- Campaigns created (paused): {outputs.get('campaigns_created', 0)}",
            f"- Ad sets created (paused): {outputs.get('adsets_created', 0)}",
            f"- Ads created (paused): {outputs.get('ads_created', 0)}",
            "",
            "## Quality Gate Notes",
            f"- Lead target min: {runtime.get('target_leads_min')}",
            f"- Static target min: {runtime.get('target_statics_min')}",
            f"- Video target min: {runtime.get('target_videos_min')}",
            "",
            "## Planning Snapshot",
            "```json",
            json.dumps(planned.get("estimate", {}), indent=2),
            "```",
            "",
        ]
        report_path = run_dir / "runpod_sprint_report.md"
        report_path.write_text("\n".join(md))
        self._insert_artifact(run_id, "report_md", report_path, {"outputs": outputs})
        self._insert_artifact(run_id, "summary_csv", csv_summary, {"rows": len(outputs)})
        return report_path

    def _create_run(self, *, runtime: Dict[str, Any], preflight: Dict[str, Any]) -> int:
        conn = self._conn()
        try:
            cur = conn.execute(
                """
                INSERT INTO runpod_sprint_runs
                (status, phase, started_at, spend_usd, max_credits_usd, phase_caps_json, runtime_json, preflight_json, outputs_json)
                VALUES ('running', 'preflight', ?, 0, ?, ?, ?, ?, '{}')
                """,
                (
                    _now(),
                    float(runtime.get("max_credits_usd", 17.0)),
                    _json(runtime.get("phase_caps", [5.0, 10.0, 15.0, 17.0])),
                    _json(runtime),
                    _json(preflight),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def _update_phase(self, run_id: int, phase: str):
        conn = self._conn()
        try:
            conn.execute("UPDATE runpod_sprint_runs SET phase = ? WHERE id = ?", (_safe_str(phase), int(run_id)))
            conn.commit()
        finally:
            conn.close()

    def _phase_of(self, run_id: Optional[int]) -> str:
        if not run_id:
            return "pipeline"
        conn = self._conn()
        try:
            row = conn.execute("SELECT phase FROM runpod_sprint_runs WHERE id = ?", (int(run_id),)).fetchone()
            return _safe_str(row["phase"]) if row else "pipeline"
        finally:
            conn.close()

    def _set_outputs(self, run_id: int, outputs: Dict[str, Any]):
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE runpod_sprint_runs SET outputs_json = ? WHERE id = ?",
                (_json(outputs), int(run_id)),
            )
            conn.commit()
        finally:
            conn.close()

    def _current_spend(self, run_id: int) -> float:
        conn = self._conn()
        try:
            row = conn.execute("SELECT spend_usd FROM runpod_sprint_runs WHERE id = ?", (int(run_id),)).fetchone()
            if not row:
                return 0.0
            return _safe_float(row["spend_usd"], 0.0)
        finally:
            conn.close()

    def _set_spend(self, run_id: int, value: float):
        conn = self._conn()
        try:
            conn.execute("UPDATE runpod_sprint_runs SET spend_usd = ? WHERE id = ?", (float(value), int(run_id)))
            conn.commit()
        finally:
            conn.close()

    def _advance_spend_to_cap(self, run_id: int, runtime: Dict[str, Any], checkpoint_index: int, fraction: float = 1.0):
        caps = list(runtime.get("phase_caps") or [5.0, 10.0, 15.0, runtime.get("max_credits_usd", 17.0)])
        if checkpoint_index < 0 or checkpoint_index >= len(caps):
            return
        cur = self._current_spend(run_id)
        target = caps[checkpoint_index] * max(0.0, min(1.0, float(fraction)))
        target = max(cur, target)
        target = min(float(runtime.get("max_credits_usd", 17.0)), target)
        self._set_spend(run_id, target)

    def _finish_run(self, run_id: int, status: str, duration_ms: int, outputs: Dict[str, Any], report_path: str = "", error: str = ""):
        conn = self._conn()
        try:
            conn.execute(
                """
                UPDATE runpod_sprint_runs
                SET status = ?, ended_at = ?, duration_ms = ?, outputs_json = ?, report_path = ?, error = ?
                WHERE id = ?
                """,
                (_safe_str(status), _now(), int(duration_ms), _json(outputs), _safe_str(report_path), _safe_str(error), int(run_id)),
            )
            conn.commit()
        finally:
            conn.close()

    def _record_phase(
        self,
        run_id: int,
        phase: str,
        input_count: int,
        output_count: int,
        status: str,
        started_at: float,
        notes: str = "",
    ):
        duration_ms = int((time.time() - started_at) * 1000)
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO runpod_sprint_phase_metrics
                (run_id, phase_name, input_count, output_count, status, duration_ms, spend_after_usd, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(run_id),
                    _safe_str(phase),
                    int(input_count),
                    int(output_count),
                    _safe_str(status),
                    duration_ms,
                    float(self._current_spend(run_id)),
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
        phase: str,
        level: str,
        message: str,
        payload: Optional[Dict[str, Any]] = None,
    ):
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO runpod_sprint_events (run_id, phase_name, level, message, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    int(run_id) if run_id else None,
                    _safe_str(phase),
                    _safe_str(level),
                    _safe_str(message),
                    _json(payload or {}),
                    _now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _insert_artifact(self, run_id: int, artifact_type: str, file_path: Path, meta: Optional[Dict[str, Any]] = None):
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO runpod_sprint_artifacts (run_id, artifact_type, file_path, meta_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (int(run_id), _safe_str(artifact_type), str(file_path), _json(meta or {}), _now()),
            )
            conn.commit()
        finally:
            conn.close()

    def _should_stop(self, run_id: int) -> bool:
        if self._stop_requested:
            self._log_event(run_id, self._phase_of(run_id), "warn", "Stop requested; terminating phase safely")
            return True
        return False

    def _load_outputs(self, run_id: int) -> Dict[str, Any]:
        conn = self._conn()
        try:
            row = conn.execute("SELECT outputs_json FROM runpod_sprint_runs WHERE id = ?", (int(run_id),)).fetchone()
            if not row:
                return {}
            try:
                return json.loads(row["outputs_json"] or "{}")
            except Exception:
                return {}
        finally:
            conn.close()


_SPRINT_ENGINE: Optional[RunpodSprintEngine] = None


def get_runpod_sprint_engine() -> RunpodSprintEngine:
    global _SPRINT_ENGINE
    if _SPRINT_ENGINE is None:
        _SPRINT_ENGINE = RunpodSprintEngine()
    return _SPRINT_ENGINE
