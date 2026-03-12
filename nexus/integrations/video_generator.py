"""
Video Generation Pipeline — Zoar Bathroom Rentals

Creates promotional Instagram Reels from trailer photos using FFmpeg:
  - Ken Burns effect (zoom/pan) on each photo with crossfade transitions
  - Branded intro/outro frames with contact info
  - Optional background music mixed at low volume
  - 10 rotating content angles for weekly variety
  - Exports vertical H.264 MP4 (1080x1920, 30fps, 15-25 sec)
  - SQLite: music_library + generated_videos tables
  - Scheduler: Sunday 9 PM generate, Mon/Wed/Fri 7 AM deliver
  - Telegram: /video, /video_status commands

Config: ~/.nexus/config.json
Database: ~/.nexus/memory.db (WAL mode)
Photos: ~/.nexus/media/
Music: ~/nexus/assets/music/
Output: ~/nexus/assets/generated_videos/
Requires: FFmpeg (brew install ffmpeg)
"""
from __future__ import annotations
import asyncio
import json
import math
import os
import random
import shutil
import sqlite3
import subprocess
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# ── Constants ────────────────────────────────────────────────────────────────

DB_PATH = Path.home() / ".nexus" / "memory.db"
PT = ZoneInfo("America/Los_Angeles")
CONFIG_PATH = Path.home() / ".nexus" / "config.json"

MEDIA_DIR = Path.home() / ".nexus" / "media"
MUSIC_DIR = Path.home() / "nexus" / "assets" / "music"
OUTPUT_DIR = Path.home() / "nexus" / "assets" / "generated_videos"

# Business info
BUSINESS_NAME = "ZOAR BATHROOM RENTALS"
BUSINESS_EMAIL = "zoarbathrooms@gmail.com"
BUSINESS_PHONE = "(424) 235-8979"
BUSINESS_CTA = f"Book Now \u2022 {BUSINESS_EMAIL}"

# Video defaults
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1920
DEFAULT_FPS = 30
DEFAULT_DURATION_PER_PHOTO = 3.5
DEFAULT_TRANSITION_DURATION = 0.5
INTRO_DURATION = 2.0
OUTRO_DURATION = 3.0
MUSIC_VOLUME = 0.15  # 15% — background level

# Photo extensions to scan
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp", ".tiff"}

# Cache drawtext availability (checked once at import time)
_DRAWTEXT_AVAILABLE: bool | None = None


def _has_drawtext() -> bool:
    """Check if FFmpeg supports the drawtext filter (requires libfreetype)."""
    global _DRAWTEXT_AVAILABLE
    if _DRAWTEXT_AVAILABLE is not None:
        return _DRAWTEXT_AVAILABLE
    try:
        result = subprocess.run(
            ["ffmpeg", "-filters"],
            capture_output=True, text=True, timeout=10,
        )
        _DRAWTEXT_AVAILABLE = "drawtext" in result.stdout
        if not _DRAWTEXT_AVAILABLE:
            _log("⚠️ FFmpeg drawtext filter not available (libfreetype missing). "
                 "Videos will generate without text overlays.")
    except Exception:
        _DRAWTEXT_AVAILABLE = False
    return _DRAWTEXT_AVAILABLE

# ── Content Angles ───────────────────────────────────────────────────────────

CONTENT_ANGLES = [
    {
        "id": 1,
        "slug": "luxury_reveal",
        "title": "What your guests actually want",
        "caption": "Upgrade your event restrooms. Your guests will thank you.\n#luxuryrestrooms #eventplanning #zoarbathrooms",
        "overlays": [
            "What your guests",
            "actually want...",
            "Real flushable toilets",
            "Running water sinks",
            "Climate controlled",
            "LED lit interiors",
        ],
    },
    {
        "id": 2,
        "slug": "contrast_hook",
        "title": "Porta-potty vs. THIS",
        "caption": "There's porta-potties... then there's Zoar.\n#portapotty #upgrade #luxurytrailer #eventrentals",
        "overlays": [
            "Porta-potty vs. THIS",
            "No more complaints",
            "4 private stalls",
            "Mirrors & lighting",
            "Full AC system",
            "The upgrade you need",
        ],
    },
    {
        "id": 3,
        "slug": "value_showcase",
        "title": "$999 for THIS?!",
        "caption": "All this for $999? Delivery, setup & pickup included.\n#eventrentals #value #losangeles #zoarbathrooms",
        "overlays": [
            "$999 for THIS?!",
            "4 stall luxury trailer",
            "Delivery included",
            "Setup included",
            "Pickup included",
            "Best value in LA",
        ],
    },
    {
        "id": 4,
        "slug": "interior_tour",
        "title": "Interior Feature Tour",
        "caption": "Step inside our luxury restroom trailer. Every detail counts.\n#interiortour #luxury #eventplanning #zoarbathrooms",
        "overlays": [
            "Step inside...",
            "Flushable porcelain",
            "Vanity mirrors",
            "LED accent lighting",
            "Bluetooth speaker",
            "Premium finishes",
        ],
    },
    {
        "id": 5,
        "slug": "outdoor_wedding",
        "title": "Your outdoor wedding needs this",
        "caption": "Don't let restrooms ruin your dream wedding.\n#outdoorwedding #weddingplanning #luxuryrentals",
        "overlays": [
            "Your outdoor wedding",
            "needs this...",
            "Guest-approved comfort",
            "Elegant interiors",
            "Photo-ready vanity",
            "Book your date now",
        ],
    },
    {
        "id": 6,
        "slug": "zero_complaints",
        "title": "4 stalls, 0 complaints",
        "caption": "4 stalls. 0 complaints. 100% guest satisfaction.\n#eventrentals #guestsatisfaction #zoarbathrooms",
        "overlays": [
            "4 stalls",
            "0 complaints",
            "Men's & Women's",
            "No waiting lines",
            "5-star reviews",
            "Book today",
        ],
    },
    {
        "id": 7,
        "slug": "climate_controlled",
        "title": "Climate controlled comfort",
        "caption": "AC in summer. Heat in winter. Comfort year-round.\n#climatecontrolled #allseasons #eventcomfort",
        "overlays": [
            "Climate controlled",
            "AC in summer",
            "Heat in winter",
            "Always comfortable",
            "Rain or shine ready",
            "Year-round events",
        ],
    },
    {
        "id": 8,
        "slug": "led_evening",
        "title": "LED lighting for evening events",
        "caption": "Evening events deserve the right ambiance. LED-lit luxury.\n#eveningevent #ledlighting #ambiance #zoarbathrooms",
        "overlays": [
            "Evening events?",
            "LED ambient lighting",
            "Safe & well-lit paths",
            "Vanity mirror lights",
            "Perfect for galas",
            "Elegant after dark",
        ],
    },
    {
        "id": 9,
        "slug": "seasonal_push",
        "title": "Book before we're booked",
        "caption": "Peak season fills fast. Lock in your date today!\n#peakseason #bookearly #eventplanning #zoarbathrooms",
        "overlays": [
            "Peak season is here",
            "Dates filling fast!",
            "Don't wait",
            "Premium amenities",
            "All-inclusive pricing",
            "Reserve yours now",
        ],
    },
    {
        "id": 10,
        "slug": "service_area",
        "title": "Serving LA, SFV, Ventura & more",
        "caption": "Covering Los Angeles, San Fernando Valley, Ventura & beyond.\n#losangeles #ventura #sfv #eventrentals #zoarbathrooms",
        "overlays": [
            "Serving SoCal",
            "Los Angeles",
            "San Fernando Valley",
            "Ventura County",
            "Santa Clarita",
            "& surrounding areas",
        ],
    },
]

# ── Helpers ──────────────────────────────────────────────────────────────────


def _now_pt() -> datetime:
    return datetime.now(PT)


def _now_str() -> str:
    return _now_pt().strftime("%Y-%m-%d %H:%M:%S")


def _today_str() -> str:
    return _now_pt().strftime("%Y-%m-%d")


def _log(msg: str):
    print(f"[VideoGen] {msg}")


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass
    return {}


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_dirs():
    """Create asset directories if they do not exist."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _check_ffmpeg() -> bool:
    """Verify FFmpeg is available on the system."""
    return shutil.which("ffmpeg") is not None


def _probe_duration(path: str) -> float:
    """Get media file duration in seconds via ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True, text=True, timeout=10,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


# ═════════════════════════════════════════════════════════════════════════════
# DATABASE INIT
# ═════════════════════════════════════════════════════════════════════════════

def init_video_db():
    """Create music_library and generated_videos tables."""
    _ensure_dirs()
    conn = _get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS music_library (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT,
        track_name TEXT,
        duration_seconds REAL,
        vibe TEXT,
        license_info TEXT,
        last_used_date TEXT,
        times_used INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS generated_videos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT,
        output_path TEXT,
        photos_used TEXT,
        music_track TEXT,
        angle TEXT,
        duration_seconds REAL,
        resolution TEXT,
        status TEXT DEFAULT 'generated',
        scheduled_for TEXT,
        delivered_at TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_gv_status ON generated_videos(status);
    CREATE INDEX IF NOT EXISTS idx_gv_scheduled ON generated_videos(scheduled_for);
    CREATE INDEX IF NOT EXISTS idx_ml_vibe ON music_library(vibe);
    CREATE INDEX IF NOT EXISTS idx_ml_used ON music_library(times_used);
    """)
    conn.commit()
    conn.close()
    _log("Database tables ready")


# ═════════════════════════════════════════════════════════════════════════════
# MUSIC LIBRARY
# ═════════════════════════════════════════════════════════════════════════════

def scan_music_library() -> dict:
    """
    Scan ~/nexus/assets/music/ for audio files and register new tracks.

    Returns dict with 'scanned', 'new', 'existing' counts.
    """
    _ensure_dirs()
    audio_exts = {".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac"}

    conn = _get_conn()
    existing = {
        row["filename"]
        for row in conn.execute("SELECT filename FROM music_library").fetchall()
    }

    scanned = 0
    new_count = 0

    if not MUSIC_DIR.exists():
        conn.close()
        _log(f"Music directory not found: {MUSIC_DIR}")
        return {"scanned": 0, "new": 0, "existing": 0}

    for item in MUSIC_DIR.iterdir():
        if not item.is_file() or item.suffix.lower() not in audio_exts:
            continue
        scanned += 1
        if item.name in existing:
            continue

        # Probe duration
        duration = _probe_duration(str(item))

        # Guess vibe from filename
        name_lower = item.stem.lower().replace("_", " ").replace("-", " ")
        vibe = "upbeat"
        for v in ["chill", "ambient", "calm", "soft", "mellow", "relaxing"]:
            if v in name_lower:
                vibe = "chill"
                break
        for v in ["hype", "energy", "pump", "epic", "power"]:
            if v in name_lower:
                vibe = "hype"
                break
        for v in ["corporate", "inspire", "motivat", "uplifting"]:
            if v in name_lower:
                vibe = "corporate"
                break

        track_name = item.stem.replace("_", " ").replace("-", " ").title()

        conn.execute(
            """INSERT INTO music_library
               (filename, track_name, duration_seconds, vibe, license_info,
                last_used_date, times_used)
               VALUES (?, ?, ?, ?, '', '', 0)""",
            (item.name, track_name, duration, vibe),
        )
        new_count += 1
        _log(f"Registered track: {track_name} ({duration:.1f}s, {vibe})")

    conn.commit()
    conn.close()

    result = {"scanned": scanned, "new": new_count, "existing": scanned - new_count}
    _log(f"Music scan: {scanned} files, {new_count} new, {result['existing']} existing")
    return result


def select_music_track(vibe: str | None = None) -> str | None:
    """
    Select a music track from the library, preferring least-used.

    Args:
        vibe: Optional filter ('upbeat', 'chill', 'hype', 'corporate')

    Returns:
        Full path to the music file, or None if library is empty.
    """
    conn = _get_conn()
    if vibe:
        rows = conn.execute(
            """SELECT * FROM music_library
               WHERE vibe = ?
               ORDER BY times_used ASC, RANDOM()
               LIMIT 5""",
            (vibe,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM music_library
               ORDER BY times_used ASC, RANDOM()
               LIMIT 5""",
        ).fetchall()

    if not rows:
        # Fallback: try without vibe filter
        if vibe:
            rows = conn.execute(
                "SELECT * FROM music_library ORDER BY times_used ASC, RANDOM() LIMIT 5"
            ).fetchall()

    if not rows:
        conn.close()
        _log("No music tracks in library")
        return None

    chosen = rows[0]
    track_path = MUSIC_DIR / chosen["filename"]

    # Update usage stats
    conn.execute(
        """UPDATE music_library
           SET times_used = times_used + 1, last_used_date = ?
           WHERE id = ?""",
        (_today_str(), chosen["id"]),
    )
    conn.commit()
    conn.close()

    if not track_path.exists():
        _log(f"Track file missing: {track_path}")
        return None

    _log(f"Selected track: {chosen['track_name']} ({chosen['filename']})")
    return str(track_path)


# ═════════════════════════════════════════════════════════════════════════════
# PHOTO SELECTION
# ═════════════════════════════════════════════════════════════════════════════

def select_photos_for_video(count: int = 6) -> list[str]:
    """
    Select photos from the media catalog for video generation.

    Prioritizes:
      1. Photos tagged 'video', 'reel', 'featured', or 'portfolio'
      2. Trailer exterior/interior and event setup categories
      3. Random fill from all photos

    Args:
        count: Number of photos to select (5-8 recommended)

    Returns:
        List of absolute file paths.
    """
    conn = _get_conn()
    selected: list[str] = []
    seen_ids: set[int] = set()

    # Priority 1: tagged for video use
    tagged = conn.execute(
        """SELECT id, file_path FROM media_assets
           WHERE media_type = 'photo'
             AND (tags LIKE '%video%' OR tags LIKE '%reel%'
                  OR tags LIKE '%featured%' OR tags LIKE '%portfolio%')
           ORDER BY RANDOM()
           LIMIT ?""",
        (count,),
    ).fetchall()

    for row in tagged:
        fp = row["file_path"]
        if Path(fp).exists() and row["id"] not in seen_ids:
            selected.append(fp)
            seen_ids.add(row["id"])
            if len(selected) >= count:
                break

    # Priority 2: best categories
    if len(selected) < count:
        remaining = count - len(selected)
        placeholder = ",".join("?" * len(seen_ids)) if seen_ids else "0"
        cat_rows = conn.execute(
            f"""SELECT id, file_path FROM media_assets
                WHERE media_type = 'photo'
                  AND category IN ('trailer_exterior', 'trailer_interior', 'event_setup')
                  AND id NOT IN ({placeholder})
                ORDER BY RANDOM()
                LIMIT ?""",
            (*seen_ids, remaining) if seen_ids else (remaining,),
        ).fetchall()
        for row in cat_rows:
            fp = row["file_path"]
            if Path(fp).exists() and row["id"] not in seen_ids:
                selected.append(fp)
                seen_ids.add(row["id"])
                if len(selected) >= count:
                    break

    # Priority 3: any photo
    if len(selected) < count:
        remaining = count - len(selected)
        placeholder = ",".join("?" * len(seen_ids)) if seen_ids else "0"
        fill_rows = conn.execute(
            f"""SELECT id, file_path FROM media_assets
                WHERE media_type = 'photo'
                  AND id NOT IN ({placeholder})
                ORDER BY RANDOM()
                LIMIT ?""",
            (*seen_ids, remaining) if seen_ids else (remaining,),
        ).fetchall()
        for row in fill_rows:
            fp = row["file_path"]
            if Path(fp).exists() and row["id"] not in seen_ids:
                selected.append(fp)
                seen_ids.add(row["id"])
                if len(selected) >= count:
                    break

    conn.close()

    # Fallback: scan media directory directly if DB has nothing
    if not selected:
        _log("No photos in DB, scanning media directory directly")
        for item in MEDIA_DIR.rglob("*"):
            if item.is_file() and item.suffix.lower() in PHOTO_EXTENSIONS:
                selected.append(str(item))
                if len(selected) >= count:
                    break

    _log(f"Selected {len(selected)} photos for video")
    return selected


# ═════════════════════════════════════════════════════════════════════════════
# FFMPEG PIPELINE
# ═════════════════════════════════════════════════════════════════════════════

def _random_ken_burns(idx: int, duration: float, w: int, h: int) -> str:
    """
    Generate a Ken Burns (zoom/pan) expression for a single photo.

    Randomly picks one of: zoom-in, zoom-out, pan-left, pan-right.
    Returns an FFmpeg zoompan filter string.

    Args:
        idx: Photo index (for deterministic seed variety)
        duration: Duration this photo is shown (seconds)
        w: Target width
        h: Target height

    Returns:
        zoompan filter string
    """
    total_frames = int(duration * DEFAULT_FPS)
    effects = ["zoom_in", "zoom_out", "pan_left", "pan_right"]
    effect = effects[(idx + random.randint(0, 3)) % len(effects)]

    # zoompan: z = zoom level, x/y = pan position
    # All expressions are in terms of 'on' (output frame number)
    if effect == "zoom_in":
        # Start at 1.0, end at 1.15
        z_expr = f"min(1+0.15*on/{total_frames},1.15)"
        x_expr = f"iw/2-(iw/zoom/2)"
        y_expr = f"ih/2-(ih/zoom/2)"
    elif effect == "zoom_out":
        # Start at 1.15, end at 1.0
        z_expr = f"max(1.15-0.15*on/{total_frames},1.0)"
        x_expr = f"iw/2-(iw/zoom/2)"
        y_expr = f"ih/2-(ih/zoom/2)"
    elif effect == "pan_left":
        # Slight zoom at 1.1, pan from right to left
        z_expr = "1.1"
        x_expr = f"iw*0.1*(1-on/{total_frames})"
        y_expr = f"ih/2-(ih/zoom/2)"
    else:  # pan_right
        z_expr = "1.1"
        x_expr = f"iw*0.1*on/{total_frames}"
        y_expr = f"ih/2-(ih/zoom/2)"

    return (
        f"zoompan=z='{z_expr}':"
        f"x='{x_expr}':y='{y_expr}':"
        f"d={total_frames}:s={w}x{h}:fps={DEFAULT_FPS}"
    )


def _build_text_overlay_filter(
    text: str,
    start_time: float,
    end_time: float,
    position: str = "bottom_third",
    fontsize: int = 48,
) -> str:
    """
    Build an FFmpeg drawtext filter for a text overlay.

    Args:
        text: The text to display
        start_time: Show text starting at this time (seconds)
        end_time: Hide text after this time (seconds)
        position: 'center', 'bottom_third', or 'top_third'
        fontsize: Font size in pixels

    Returns:
        drawtext filter string
    """
    # Escape special characters for FFmpeg drawtext
    escaped = (
        text.replace("\\", "\\\\")
        .replace("'", "\u2019")  # curly apostrophe to avoid FFmpeg escaping issues
        .replace(":", "\\:")
        .replace("%", "%%")
    )

    if position == "center":
        x_expr = "(w-text_w)/2"
        y_expr = "(h-text_h)/2"
    elif position == "top_third":
        x_expr = "(w-text_w)/2"
        y_expr = "h/6-text_h/2"
    else:  # bottom_third
        x_expr = "(w-text_w)/2"
        y_expr = "h*2/3-text_h/2"

    return (
        f"drawtext=text='{escaped}':"
        f"fontsize={fontsize}:"
        f"fontcolor=white:"
        f"shadowcolor=black@0.7:shadowx=3:shadowy=3:"
        f"x={x_expr}:y={y_expr}:"
        f"enable='between(t,{start_time:.2f},{end_time:.2f})'"
    )


def _build_ffmpeg_command(
    photos: list[str],
    text_overlays: list[str],
    music_path: str | None,
    output_path: str,
    duration_per_photo: float = DEFAULT_DURATION_PER_PHOTO,
    transition_duration: float = DEFAULT_TRANSITION_DURATION,
    intro_text: str = BUSINESS_NAME,
    outro_text: str = BUSINESS_CTA,
    resolution: tuple[int, int] = (DEFAULT_WIDTH, DEFAULT_HEIGHT),
) -> list[str]:
    """
    Build the full FFmpeg command list for reel generation.

    Pipeline:
      1. Scale/crop each photo to target resolution (center-crop)
      2. Apply Ken Burns effect per photo
      3. Crossfade transitions between segments
      4. Text overlays on each photo segment
      5. Branded intro (solid + text) and outro (solid + CTA)
      6. Mix background music at low volume
      7. Export as H.264 MP4

    Args:
        photos: List of photo file paths
        text_overlays: Text for each photo (same length as photos)
        music_path: Path to background music file (or None)
        output_path: Path for output MP4
        duration_per_photo: Seconds each photo is shown
        transition_duration: Crossfade duration in seconds
        intro_text: Text for intro frame
        outro_text: Text for outro frame
        resolution: (width, height) tuple

    Returns:
        Command list suitable for subprocess.run()
    """
    w, h = resolution
    num_photos = len(photos)

    # ── Build input args ────────────────────────────────────────────────
    inputs: list[str] = []
    for photo in photos:
        inputs.extend(["-loop", "1", "-t", str(duration_per_photo), "-i", photo])

    # If music, add as last input
    if music_path:
        inputs.extend(["-i", music_path])

    # ── Build filter_complex ────────────────────────────────────────────
    filters: list[str] = []
    segment_labels: list[str] = []

    # --- Step 1+2: Scale, crop, Ken Burns for each photo ---
    for i in range(num_photos):
        # Scale to cover target, then center-crop
        scale_crop = (
            f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},"
            f"setsar=1,"
            f"{_random_ken_burns(i, duration_per_photo, w, h)},"
            f"setpts=PTS-STARTPTS,format=yuv420p"
            f"[photo{i}]"
        )
        filters.append(scale_crop)
        segment_labels.append(f"[photo{i}]")

    # --- Step 3: Crossfade transitions ---
    if num_photos == 1:
        # Single photo, no crossfade needed
        current_label = "photo0"
    else:
        # Chain crossfades: photo0 xfade photo1 -> xf0, xf0 xfade photo2 -> xf1, ...
        current_label = "photo0"
        running_offset = duration_per_photo - transition_duration

        for i in range(1, num_photos):
            out_label = f"xf{i - 1}"
            filters.append(
                f"[{current_label}][photo{i}]xfade=transition=fade:"
                f"duration={transition_duration}:"
                f"offset={running_offset:.2f}"
                f"[{out_label}]"
            )
            current_label = out_label
            running_offset += duration_per_photo - transition_duration

    # --- Step 4: Text overlays on photo segments ---
    # Calculate the time offset for each photo's text (skip if drawtext unavailable)
    text_filters: list[str] = []
    if _has_drawtext():
        for i, overlay_text in enumerate(text_overlays):
            if not overlay_text:
                continue
            start_t = i * (duration_per_photo - transition_duration) + 0.3
            end_t = start_t + duration_per_photo - 0.6
            text_filters.append(
                _build_text_overlay_filter(
                    overlay_text, start_t, end_t,
                    position="bottom_third", fontsize=52,
                )
            )

    # --- Step 5: Intro and outro frames ---
    # Total duration of the photo chain
    photo_chain_duration = (
        num_photos * duration_per_photo
        - (num_photos - 1) * transition_duration
    )

    # Generate intro: dark blue frame with business name
    has_dt = _has_drawtext()
    if has_dt:
        intro_filter = (
            f"color=c=0x1a1a2e:s={w}x{h}:d={INTRO_DURATION}:r={DEFAULT_FPS},"
            f"format=yuv420p,"
            f"drawtext=text='{intro_text}':"
            f"fontsize=64:fontcolor=white:"
            f"shadowcolor=black@0.8:shadowx=3:shadowy=3:"
            f"x=(w-text_w)/2:y=(h-text_h)/2-40,"
            f"drawtext=text='Luxury Restroom Trailers':"
            f"fontsize=36:fontcolor=white@0.8:"
            f"x=(w-text_w)/2:y=(h/2)+30"
            f"[intro]"
        )
    else:
        # Plain color frame when drawtext is unavailable
        intro_filter = (
            f"color=c=0x1a1a2e:s={w}x{h}:d={INTRO_DURATION}:r={DEFAULT_FPS},"
            f"format=yuv420p"
            f"[intro]"
        )
    filters.append(intro_filter)

    # Generate outro: dark frame with CTA
    if has_dt:
        phone_escaped = BUSINESS_PHONE.replace("(", "\\(").replace(")", "\\)")
        outro_filter = (
            f"color=c=0x1a1a2e:s={w}x{h}:d={OUTRO_DURATION}:r={DEFAULT_FPS},"
            f"format=yuv420p,"
            f"drawtext=text='{BUSINESS_NAME}':"
            f"fontsize=56:fontcolor=white:"
            f"shadowcolor=black@0.8:shadowx=3:shadowy=3:"
            f"x=(w-text_w)/2:y=(h/2)-100,"
            f"drawtext=text='{phone_escaped}':"
            f"fontsize=42:fontcolor=white@0.9:"
            f"x=(w-text_w)/2:y=(h/2),"
            f"drawtext=text='{BUSINESS_EMAIL}':"
            f"fontsize=38:fontcolor=white@0.8:"
            f"x=(w-text_w)/2:y=(h/2)+70,"
            f"drawtext=text='Book Now!':"
            f"fontsize=48:fontcolor=#FFD700:"
            f"x=(w-text_w)/2:y=(h/2)+150"
            f"[outro]"
        )
    else:
        outro_filter = (
            f"color=c=0x1a1a2e:s={w}x{h}:d={OUTRO_DURATION}:r={DEFAULT_FPS},"
            f"format=yuv420p"
            f"[outro]"
        )
    filters.append(outro_filter)

    # --- Combine: intro + photo chain (with text) + outro ---
    # First apply text overlays to the photo chain
    if text_filters:
        text_chain = ",".join(text_filters)
        filters.append(f"[{current_label}]{text_chain}[textchain]")
        chain_with_text = "textchain"
    else:
        chain_with_text = current_label

    # Crossfade intro into photo chain
    filters.append(
        f"[intro][{chain_with_text}]xfade=transition=fade:"
        f"duration={transition_duration}:"
        f"offset={INTRO_DURATION - transition_duration:.2f}"
        f"[intro_chain]"
    )

    # Crossfade photo chain into outro
    intro_chain_duration = (
        INTRO_DURATION + photo_chain_duration - transition_duration
    )
    filters.append(
        f"[intro_chain][outro]xfade=transition=fade:"
        f"duration={transition_duration}:"
        f"offset={intro_chain_duration - transition_duration:.2f}"
        f"[final_video]"
    )

    # --- Step 7: Audio ---
    total_duration = (
        INTRO_DURATION + photo_chain_duration + OUTRO_DURATION
        - 2 * transition_duration  # two xfade overlaps (intro->chain, chain->outro)
    )

    if music_path:
        music_input_idx = num_photos  # music is the last input
        filters.append(
            f"[{music_input_idx}:a]afade=t=in:d=1.0,"
            f"afade=t=out:st={total_duration - 2.0}:d=2.0,"
            f"volume={MUSIC_VOLUME},"
            f"atrim=0:{total_duration:.2f},"
            f"asetpts=PTS-STARTPTS"
            f"[final_audio]"
        )

    # ── Assemble command ────────────────────────────────────────────────
    filter_complex = ";\n".join(filters)

    cmd = ["ffmpeg", "-y"]
    cmd.extend(inputs)
    cmd.extend(["-filter_complex", filter_complex])

    if music_path:
        cmd.extend(["-map", "[final_video]", "-map", "[final_audio]"])
    else:
        cmd.extend(["-map", "[final_video]"])

    cmd.extend([
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-r", str(DEFAULT_FPS),
        "-movflags", "+faststart",
    ])

    if music_path:
        cmd.extend(["-c:a", "aac", "-b:a", "128k"])

    cmd.extend(["-t", f"{total_duration:.2f}"])
    cmd.append(output_path)

    return cmd


# ═════════════════════════════════════════════════════════════════════════════
# MAIN GENERATION FUNCTION
# ═════════════════════════════════════════════════════════════════════════════

def generate_reel(
    photos: list[str],
    text_overlays: list[str],
    music_path: str | None = None,
    output_path: str | None = None,
    duration_per_photo: float = DEFAULT_DURATION_PER_PHOTO,
    transition_duration: float = DEFAULT_TRANSITION_DURATION,
    intro_text: str = BUSINESS_NAME,
    outro_text: str = BUSINESS_CTA,
    resolution: tuple[int, int] = (DEFAULT_WIDTH, DEFAULT_HEIGHT),
    angle_slug: str = "",
) -> str:
    """
    Generate a promotional Reel video from photos using FFmpeg.

    Args:
        photos: 5-8 photo file paths
        text_overlays: Text for each photo (matched by index)
        music_path: Path to royalty-free background track (optional)
        output_path: Output file path (auto-generated if None)
        duration_per_photo: Seconds per photo (default 3.5)
        transition_duration: Crossfade overlap (default 0.5)
        intro_text: Business name for intro frame
        outro_text: CTA text for outro frame
        resolution: (width, height) tuple for vertical Reels
        angle_slug: Content angle identifier for tracking

    Returns:
        Absolute path to the generated MP4 file.

    Raises:
        RuntimeError: If FFmpeg is not installed or encoding fails.
        ValueError: If no photos are provided.
    """
    if not _check_ffmpeg():
        raise RuntimeError(
            "FFmpeg not found. Install with: brew install ffmpeg"
        )

    if not photos:
        raise ValueError("At least one photo is required")

    # Ensure overlays list matches photos length
    while len(text_overlays) < len(photos):
        text_overlays.append("")
    text_overlays = text_overlays[: len(photos)]

    # Validate photo files exist
    valid_photos: list[str] = []
    valid_overlays: list[str] = []
    for i, photo in enumerate(photos):
        if Path(photo).exists():
            valid_photos.append(photo)
            valid_overlays.append(text_overlays[i])
        else:
            _log(f"Skipping missing photo: {photo}")

    if not valid_photos:
        raise ValueError("No valid photo files found")

    photos = valid_photos
    text_overlays = valid_overlays

    # Generate output path if not specified
    _ensure_dirs()
    if not output_path:
        timestamp = _now_pt().strftime("%Y%m%d_%H%M%S")
        slug = angle_slug or "reel"
        filename = f"zoar_{slug}_{timestamp}.mp4"
        output_path = str(OUTPUT_DIR / filename)

    _log(f"Generating reel: {len(photos)} photos -> {output_path}")
    _log(f"  Resolution: {resolution[0]}x{resolution[1]}, {duration_per_photo}s/photo")
    if music_path:
        _log(f"  Music: {Path(music_path).name}")

    # Build and run FFmpeg command
    cmd = _build_ffmpeg_command(
        photos=photos,
        text_overlays=text_overlays,
        music_path=music_path,
        output_path=output_path,
        duration_per_photo=duration_per_photo,
        transition_duration=transition_duration,
        intro_text=intro_text,
        outro_text=outro_text,
        resolution=resolution,
    )

    _log(f"Running FFmpeg ({len(cmd)} args)...")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 min timeout
        )
    except subprocess.TimeoutExpired:
        _log("FFmpeg timed out after 5 minutes")
        raise RuntimeError("FFmpeg encoding timed out (5 min limit)")

    if result.returncode != 0:
        stderr_tail = result.stderr[-2000:] if result.stderr else "(no stderr)"
        _log(f"FFmpeg failed (rc={result.returncode}): {stderr_tail}")
        raise RuntimeError(f"FFmpeg encoding failed (rc={result.returncode})")

    # Verify output file
    out = Path(output_path)
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError(f"Output file missing or empty: {output_path}")

    file_size_mb = out.stat().st_size / (1024 * 1024)
    duration = _probe_duration(output_path)

    _log(f"Reel generated: {out.name} ({file_size_mb:.1f} MB, {duration:.1f}s)")

    # Record in database
    w, h = resolution
    conn = _get_conn()
    conn.execute(
        """INSERT INTO generated_videos
           (filename, output_path, photos_used, music_track, angle,
            duration_seconds, resolution, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'generated', ?)""",
        (
            out.name,
            str(out),
            json.dumps([Path(p).name for p in photos]),
            Path(music_path).name if music_path else "",
            angle_slug,
            duration,
            f"{w}x{h}",
            _now_str(),
        ),
    )
    conn.commit()
    conn.close()

    return str(out)


# ═════════════════════════════════════════════════════════════════════════════
# SCHEDULING & WEEKLY GENERATION
# ═════════════════════════════════════════════════════════════════════════════

def _get_next_angle() -> dict:
    """
    Pick the next content angle, rotating through all 10.

    Selects the angle with the fewest generated videos, breaking ties
    by ID order for round-robin fairness.
    """
    conn = _get_conn()
    # Count how many times each angle has been used
    rows = conn.execute(
        "SELECT angle, COUNT(*) as cnt FROM generated_videos GROUP BY angle"
    ).fetchall()
    usage = {row["angle"]: row["cnt"] for row in rows}
    conn.close()

    # Find least used angle
    candidates = sorted(
        CONTENT_ANGLES,
        key=lambda a: (usage.get(a["slug"], 0), a["id"]),
    )
    chosen = candidates[0]
    _log(f"Next angle: #{chosen['id']} {chosen['slug']} (used {usage.get(chosen['slug'], 0)}x)")
    return chosen


def schedule_weekly_videos() -> list[dict]:
    """
    Generate 3 videos for the upcoming week (Mon / Wed / Fri).

    Each video uses a different content angle, selects photos from the
    media catalog, and optionally adds background music.

    Returns:
        List of dicts with 'path', 'angle', 'scheduled_for' for each video.
    """
    _log("Generating weekly video batch...")

    now = _now_pt()
    # Find next Monday, Wednesday, Friday
    schedule_days: list[datetime] = []
    for days_ahead in range(1, 8):
        future = now + timedelta(days=days_ahead)
        if future.weekday() in (0, 2, 4):  # Mon, Wed, Fri
            schedule_days.append(future)
        if len(schedule_days) >= 3:
            break

    generated: list[dict] = []
    conn = _get_conn()

    for target_day in schedule_days:
        target_str = target_day.strftime("%Y-%m-%d 07:00:00")

        # Check if already generated for this date
        existing = conn.execute(
            "SELECT id FROM generated_videos WHERE scheduled_for = ?",
            (target_str,),
        ).fetchone()
        if existing:
            _log(f"Video already scheduled for {target_str}, skipping")
            continue

        try:
            angle = _get_next_angle()
            photos = select_photos_for_video(count=6)
            if len(photos) < 3:
                _log(f"Not enough photos ({len(photos)}), skipping {target_str}")
                continue

            overlays = angle["overlays"][: len(photos)]
            music = select_music_track(vibe="upbeat")

            output = generate_reel(
                photos=photos,
                text_overlays=overlays,
                music_path=music,
                angle_slug=angle["slug"],
            )

            # Update schedule
            conn.execute(
                "UPDATE generated_videos SET scheduled_for = ? WHERE output_path = ?",
                (target_str, output),
            )
            conn.commit()

            info = {
                "path": output,
                "angle": angle["slug"],
                "title": angle["title"],
                "caption": angle["caption"],
                "scheduled_for": target_str,
            }
            generated.append(info)
            _log(f"Scheduled: {angle['title']} for {target_str}")

        except Exception as e:
            _log(f"Failed to generate video for {target_str}: {e}")
            traceback.print_exc()

    conn.close()
    _log(f"Weekly batch complete: {len(generated)} videos generated")
    return generated


# ═════════════════════════════════════════════════════════════════════════════
# STATS & REPORTING
# ═════════════════════════════════════════════════════════════════════════════

def get_video_stats() -> dict:
    """
    Get generation statistics.

    Returns:
        Dict with total count, by status, by angle, storage size, etc.
    """
    conn = _get_conn()

    total = conn.execute(
        "SELECT COUNT(*) as cnt FROM generated_videos"
    ).fetchone()["cnt"]

    by_status = {}
    for row in conn.execute(
        "SELECT status, COUNT(*) as cnt FROM generated_videos GROUP BY status"
    ).fetchall():
        by_status[row["status"]] = row["cnt"]

    by_angle = {}
    for row in conn.execute(
        "SELECT angle, COUNT(*) as cnt FROM generated_videos GROUP BY angle ORDER BY cnt DESC"
    ).fetchall():
        by_angle[row["angle"]] = row["cnt"]

    pending = conn.execute(
        """SELECT id, filename, scheduled_for, angle FROM generated_videos
           WHERE status = 'generated' AND scheduled_for IS NOT NULL
             AND scheduled_for > ?
           ORDER BY scheduled_for ASC""",
        (_now_str(),),
    ).fetchall()
    upcoming = [
        {"id": r["id"], "filename": r["filename"],
         "scheduled_for": r["scheduled_for"], "angle": r["angle"]}
        for r in pending
    ]

    recent = conn.execute(
        """SELECT id, filename, angle, duration_seconds, status, created_at
           FROM generated_videos ORDER BY created_at DESC LIMIT 5"""
    ).fetchall()
    recent_list = [dict(r) for r in recent]

    music_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM music_library"
    ).fetchone()["cnt"]

    # Calculate total storage
    total_size = 0
    for row in conn.execute(
        "SELECT output_path FROM generated_videos"
    ).fetchall():
        p = Path(row["output_path"])
        if p.exists():
            total_size += p.stat().st_size

    conn.close()

    return {
        "total_videos": total,
        "by_status": by_status,
        "by_angle": by_angle,
        "upcoming": upcoming,
        "recent": recent_list,
        "music_tracks": music_count,
        "total_storage_bytes": total_size,
        "total_storage_mb": round(total_size / (1024 * 1024), 1) if total_size else 0,
    }


def format_video_summary() -> str:
    """Telegram-formatted video generation summary."""
    stats = get_video_stats()

    lines = [
        "\U0001f3ac VIDEO GENERATION — Zoar Reels",
        "\u2501" * 35,
        "",
        f"Total videos:  {stats['total_videos']}",
        f"Music tracks:  {stats['music_tracks']}",
        f"Storage used:  {stats['total_storage_mb']} MB",
        "",
    ]

    # Status breakdown
    if stats["by_status"]:
        lines.append("By Status:")
        status_icons = {
            "generated": "\U0001f7e2",
            "delivered": "\u2705",
            "posted": "\U0001f4e4",
            "failed": "\u274c",
        }
        for status, count in stats["by_status"].items():
            icon = status_icons.get(status, "\u2022")
            lines.append(f"  {icon} {status.title()}: {count}")
        lines.append("")

    # Upcoming deliveries
    if stats["upcoming"]:
        lines.append("Upcoming Deliveries:")
        for v in stats["upcoming"][:5]:
            date_str = v["scheduled_for"][:10] if v["scheduled_for"] else "?"
            angle_name = v["angle"] or "?"
            lines.append(f"  \U0001f4c5 {date_str} — {angle_name}")
        lines.append("")

    # Recent videos
    if stats["recent"]:
        lines.append("Recent:")
        for v in stats["recent"][:3]:
            dur = f"{v['duration_seconds']:.0f}s" if v["duration_seconds"] else "?"
            lines.append(f"  \U0001f3ac {v['filename']} ({dur})")
        lines.append("")

    # Content angle usage
    if stats["by_angle"]:
        lines.append("Angle Usage:")
        for angle, count in list(stats["by_angle"].items())[:5]:
            lines.append(f"  \u2022 {angle}: {count}x")
        lines.append("")

    lines.append("Commands:")
    lines.append("  /video          — Generate a reel now")
    lines.append("  /video_status   — View scheduled videos")

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# TELEGRAM COMMAND HANDLERS
# ═════════════════════════════════════════════════════════════════════════════

def handle_video_command(text: str) -> str:
    """
    /video — Generate a video on demand or show status.

    Subcommands:
        /video           — Show summary + generate prompt
        /video generate  — Generate a reel now
        /video angles    — List all content angles
        /video scan      — Scan music directory
    """
    parts = text.strip().split()

    # /video generate
    if len(parts) >= 2 and parts[1].lower() in ("generate", "gen", "create"):
        try:
            if not _check_ffmpeg():
                return "\u274c FFmpeg not installed. Run: brew install ffmpeg"

            angle = _get_next_angle()
            photos = select_photos_for_video(count=6)
            if len(photos) < 2:
                return (
                    "\u274c Not enough photos in media library.\n"
                    f"Add photos to {MEDIA_DIR} and run /media scan"
                )

            overlays = angle["overlays"][: len(photos)]
            music = select_music_track()

            output = generate_reel(
                photos=photos,
                text_overlays=overlays,
                music_path=music,
                angle_slug=angle["slug"],
            )

            size_mb = Path(output).stat().st_size / (1024 * 1024)
            duration = _probe_duration(output)

            return (
                f"\u2705 Reel Generated!\n"
                f"\u2501" * 30 + "\n"
                f"\U0001f3ac {Path(output).name}\n"
                f"\U0001f4cf {duration:.0f}s | {size_mb:.1f} MB\n"
                f"\U0001f3af Angle: {angle['title']}\n"
                f"\U0001f5bc Photos: {len(photos)}\n"
                f"\U0001f3b5 Music: {'Yes' if music else 'No'}\n\n"
                f"Caption:\n{angle['caption']}\n\n"
                f"File: {output}"
            )

        except Exception as e:
            _log(f"Video generation error: {e}")
            traceback.print_exc()
            return f"\u274c Video generation failed: {e}"

    # /video angles
    if len(parts) >= 2 and parts[1].lower() in ("angles", "angle", "ideas"):
        lines = ["\U0001f3af Content Angles (10 rotating)", "\u2501" * 35, ""]
        for a in CONTENT_ANGLES:
            lines.append(f"  {a['id']:>2}. {a['title']}")
            lines.append(f"      {a['slug']}")
        return "\n".join(lines)

    # /video scan (music)
    if len(parts) >= 2 and parts[1].lower() == "scan":
        try:
            result = scan_music_library()
            return (
                f"\U0001f3b5 Music Library Scan\n"
                f"Files scanned: {result['scanned']}\n"
                f"New tracks:    {result['new']}\n"
                f"Already known: {result['existing']}"
            )
        except Exception as e:
            return f"\u274c Music scan error: {e}"

    # Default: show summary
    try:
        return format_video_summary()
    except Exception as e:
        return f"\u274c Video status error: {e}"


def handle_video_status_command(text: str) -> str:
    """
    /video_status — Show scheduled and recent videos.
    """
    conn = _get_conn()

    # Upcoming scheduled
    upcoming = conn.execute(
        """SELECT filename, angle, scheduled_for, status
           FROM generated_videos
           WHERE scheduled_for IS NOT NULL AND scheduled_for > ?
           ORDER BY scheduled_for ASC LIMIT 10""",
        (_now_str(),),
    ).fetchall()

    # Recently delivered
    delivered = conn.execute(
        """SELECT filename, angle, delivered_at, status
           FROM generated_videos
           WHERE status = 'delivered'
           ORDER BY delivered_at DESC LIMIT 5"""
    ).fetchall()

    # Recently generated
    recent = conn.execute(
        """SELECT filename, angle, duration_seconds, created_at, status
           FROM generated_videos
           ORDER BY created_at DESC LIMIT 5"""
    ).fetchall()

    conn.close()

    lines = ["\U0001f4c5 VIDEO SCHEDULE", "\u2501" * 30, ""]

    if upcoming:
        lines.append(f"Upcoming ({len(upcoming)}):")
        for v in upcoming:
            date = v["scheduled_for"][:10] if v["scheduled_for"] else "?"
            angle = v["angle"] or "?"
            lines.append(f"  \U0001f4c5 {date} | {angle} | {v['filename']}")
        lines.append("")
    else:
        lines.append("No upcoming deliveries.")
        lines.append("Run /video generate or wait for Sunday 9 PM auto-batch.")
        lines.append("")

    if delivered:
        lines.append(f"Recently Delivered ({len(delivered)}):")
        for v in delivered:
            date = v["delivered_at"][:10] if v["delivered_at"] else "?"
            lines.append(f"  \u2705 {date} | {v['angle']} | {v['filename']}")
        lines.append("")

    if recent:
        lines.append(f"Recent ({len(recent)}):")
        for v in recent:
            dur = f"{v['duration_seconds']:.0f}s" if v["duration_seconds"] else "?"
            status_icon = {
                "generated": "\U0001f7e2",
                "delivered": "\u2705",
                "posted": "\U0001f4e4",
                "failed": "\u274c",
            }.get(v["status"], "\u2022")
            lines.append(
                f"  {status_icon} {v['filename']} ({dur}) [{v['status']}]"
            )

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# ASYNC SCHEDULER
# ═════════════════════════════════════════════════════════════════════════════

async def _deliver_video(send_fn, video_row: dict) -> bool:
    """
    Deliver a single video via Telegram (send file path + caption).

    Args:
        send_fn: Async function to send Telegram messages
        video_row: Dict with video info from DB

    Returns:
        True if delivered successfully.
    """
    output_path = video_row["output_path"]
    angle_slug = video_row["angle"]

    if not Path(output_path).exists():
        _log(f"Delivery failed — file missing: {output_path}")
        return False

    # Find matching angle for caption
    caption = ""
    title = angle_slug
    for a in CONTENT_ANGLES:
        if a["slug"] == angle_slug:
            caption = a["caption"]
            title = a["title"]
            break

    size_mb = Path(output_path).stat().st_size / (1024 * 1024)
    duration = video_row.get("duration_seconds", 0) or _probe_duration(output_path)

    msg = (
        f"\U0001f3ac REEL READY FOR POSTING\n"
        f"\u2501" * 30 + "\n\n"
        f"\U0001f3af {title}\n"
        f"\U0001f4cf {duration:.0f}s | {size_mb:.1f} MB\n\n"
        f"Caption:\n{caption}\n\n"
        f"File: {output_path}"
    )

    try:
        await send_fn(msg)
    except Exception as e:
        _log(f"Delivery send error: {e}")
        return False

    # Update status
    conn = _get_conn()
    conn.execute(
        "UPDATE generated_videos SET status = 'delivered', delivered_at = ? WHERE id = ?",
        (_now_str(), video_row["id"]),
    )
    conn.commit()
    conn.close()

    _log(f"Delivered video #{video_row['id']}: {video_row['filename']}")
    return True


async def run_video_scheduler(send_fn) -> None:
    """
    Async scheduler loop for video generation and delivery.

    Schedule:
      - Sunday 9 PM PT: Generate 3 videos for Mon/Wed/Fri
      - Mon/Wed/Fri 7 AM PT: Deliver that day's video + caption to Telegram

    Args:
        send_fn: Async function to send Telegram messages
    """
    try:
        from core.watchdog import heartbeat
    except ImportError:
        def heartbeat(_: str) -> None:
            pass

    _log("Video scheduler started")
    generated_week = ""
    delivered_dates: set[str] = set()

    while True:
        try:
            heartbeat("video_generator")
            now = _now_pt()
            week_key = now.strftime("%Y-W%W")
            today_key = now.strftime("%Y-%m-%d")

            # ── Sunday 9 PM: generate weekly batch ──────────────────────
            if (
                now.weekday() == 6
                and now.hour == 21
                and week_key != generated_week
            ):
                _log("Sunday 9 PM — generating weekly video batch")
                generated_week = week_key

                try:
                    results = schedule_weekly_videos()
                    if results:
                        msg = (
                            f"\U0001f3ac WEEKLY VIDEOS GENERATED\n"
                            f"\u2501" * 30 + "\n\n"
                            f"Created {len(results)} reel(s) for this week:\n"
                        )
                        for r in results:
                            date = r["scheduled_for"][:10]
                            msg += f"  \U0001f4c5 {date} — {r['title']}\n"
                        msg += "\nDelivery: Mon/Wed/Fri at 7 AM PT"
                        try:
                            await send_fn(msg)
                        except Exception as e:
                            _log(f"Failed to send batch summary: {e}")
                    else:
                        _log("No videos generated (not enough photos?)")
                except Exception as e:
                    _log(f"Weekly generation error: {e}")
                    traceback.print_exc()

            # ── Mon/Wed/Fri 7 AM: deliver scheduled video ───────────────
            if (
                now.weekday() in (0, 2, 4)
                and now.hour == 7
                and today_key not in delivered_dates
            ):
                delivered_dates.add(today_key)
                target_time = f"{today_key} 07:00:00"

                conn = _get_conn()
                row = conn.execute(
                    """SELECT * FROM generated_videos
                       WHERE scheduled_for = ? AND status = 'generated'
                       LIMIT 1""",
                    (target_time,),
                ).fetchone()
                conn.close()

                if row:
                    _log(f"Delivering video for {today_key}")
                    try:
                        await _deliver_video(send_fn, dict(row))
                    except Exception as e:
                        _log(f"Delivery error: {e}")
                        traceback.print_exc()
                else:
                    _log(f"No video scheduled for {target_time}")

            # ── Housekeeping: prune delivered_dates older than 7 days ──
            if len(delivered_dates) > 30:
                cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d")
                delivered_dates = {d for d in delivered_dates if d >= cutoff}

        except Exception as e:
            _log(f"Scheduler error: {e}")
            traceback.print_exc()

        await asyncio.sleep(60)


# ═════════════════════════════════════════════════════════════════════════════
# MODULE INIT
# ═════════════════════════════════════════════════════════════════════════════

init_video_db()

if not _check_ffmpeg():
    _log("WARNING: FFmpeg not found. Install with: brew install ffmpeg")
else:
    _log("FFmpeg available")

_log(f"Media dir:  {MEDIA_DIR}")
_log(f"Music dir:  {MUSIC_DIR}")
_log(f"Output dir: {OUTPUT_DIR}")
