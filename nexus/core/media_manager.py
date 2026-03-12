from __future__ import annotations
"""
Media & Photo Management System — Zoar Bathroom Rentals CRM

Local media asset catalog and indexer for managing photos and documents:
  - Register and track media assets (photos, videos, documents)
  - Categorize by type: trailer exterior/interior, event setup, client events,
    before/after comparisons, marketing creatives
  - Link assets to CRM leads for per-client galleries
  - Create curated collections for portfolio, social, and ad creative use
  - Scan media directory for unregistered files
  - Search by tags, captions, and categories
  - Telegram commands: /media, /media search, /media tag

Database: ~/.nexus/memory.db (WAL mode)
Media storage: ~/.nexus/media/
Timezone: America/Los_Angeles
"""
import sqlite3
import json
import os
import random
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

# ── Constants ────────────────────────────────────────────────────────────────

DB_PATH = Path.home() / ".nexus" / "memory.db"
LA_TZ = ZoneInfo("America/Los_Angeles")
MEDIA_DIR = Path.home() / ".nexus" / "media"

# Supported media categories
CATEGORIES = [
    "trailer_exterior",
    "trailer_interior",
    "event_setup",
    "client_event",
    "before_after",
    "marketing",
]

CATEGORY_LABELS = {
    "trailer_exterior": "Trailer Exterior",
    "trailer_interior": "Trailer Interior",
    "event_setup": "Event Setup / Delivery",
    "client_event": "Client Event",
    "before_after": "Before & After",
    "marketing": "Marketing / Ad Creative",
}

CATEGORY_EMOJIS = {
    "trailer_exterior": "\U0001f69a",   # truck
    "trailer_interior": "\U0001f6bf",   # shower (interior)
    "event_setup": "\U0001f3aa",        # circus tent (setup)
    "client_event": "\U0001f389",       # party popper
    "before_after": "\U0001f504",       # arrows (cycle)
    "marketing": "\U0001f4f0",          # newspaper (marketing)
}

# Supported file extensions by media type
MEDIA_TYPE_MAP = {
    "photo": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".bmp", ".tiff"},
    "video": {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"},
    "document": {".pdf", ".doc", ".docx", ".txt", ".csv", ".xls", ".xlsx"},
}

# Collection purposes
COLLECTION_PURPOSES = ["portfolio", "social", "ad_creative", "client_gallery"]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _now_la() -> datetime:
    """Current time in Pacific."""
    return datetime.now(LA_TZ)


def _now_str() -> str:
    """Pacific timestamp string for DB storage."""
    return _now_la().strftime("%Y-%m-%d %H:%M:%S")


def _db() -> sqlite3.Connection:
    """Get a DB connection with WAL mode and row factory."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def _detect_media_type(filename: str) -> str:
    """Detect media type from file extension."""
    ext = Path(filename).suffix.lower()
    for media_type, extensions in MEDIA_TYPE_MAP.items():
        if ext in extensions:
            return media_type
    return "document"


def _format_file_size(size_bytes: int) -> str:
    """Format bytes into human-readable size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def _ensure_media_dir() -> Path:
    """Create media directory if it doesn't exist."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    # Also create per-category subdirectories
    for cat in CATEGORIES:
        (MEDIA_DIR / cat).mkdir(exist_ok=True)
    return MEDIA_DIR


# ── Database Init ────────────────────────────────────────────────────────────

def init_media_db() -> None:
    """Create media_assets and media_collections tables, ensure media directory."""
    _ensure_media_dir()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS media_assets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT NOT NULL DEFAULT '',
        file_path TEXT NOT NULL DEFAULT '',
        media_type TEXT NOT NULL DEFAULT 'photo',
        category TEXT NOT NULL DEFAULT 'marketing',
        tags TEXT NOT NULL DEFAULT '[]',
        lead_id INTEGER NOT NULL DEFAULT 0,
        event_date TEXT DEFAULT '',
        caption TEXT DEFAULT '',
        width INTEGER DEFAULT 0,
        height INTEGER DEFAULT 0,
        file_size INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS media_collections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL DEFAULT '',
        description TEXT DEFAULT '',
        asset_ids TEXT NOT NULL DEFAULT '[]',
        purpose TEXT NOT NULL DEFAULT 'portfolio',
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_media_category ON media_assets(category);
    CREATE INDEX IF NOT EXISTS idx_media_lead ON media_assets(lead_id);
    CREATE INDEX IF NOT EXISTS idx_media_type ON media_assets(media_type);
    CREATE INDEX IF NOT EXISTS idx_media_created ON media_assets(created_at);
    CREATE INDEX IF NOT EXISTS idx_collections_purpose ON media_collections(purpose);
    """)
    conn.commit()
    conn.close()
    print("[Media] Database tables ready")
    print(f"[Media] Media directory: {MEDIA_DIR}")


# Module-level init
init_media_db()


# ═════════════════════════════════════════════════════════════════════════════
# ASSET REGISTRATION
# ═════════════════════════════════════════════════════════════════════════════

def register_media(
    file_path: str,
    category: str,
    tags: list | None = None,
    lead_id: int = 0,
    caption: str = "",
    event_date: str = "",
) -> int:
    """
    Register a media asset in the database.

    Args:
        file_path: Path to the media file (absolute or relative to media dir)
        category: One of CATEGORIES
        tags: List of tag strings
        lead_id: Associated CRM lead ID (0 = general/unlinked)
        caption: Description or caption for the asset
        event_date: Date of event (YYYY-MM-DD)

    Returns:
        The new asset ID
    """
    if tags is None:
        tags = []

    # Validate category
    if category not in CATEGORIES:
        print(f"[Media] Warning: unknown category '{category}', defaulting to 'marketing'")
        category = "marketing"

    # Resolve file path
    fp = Path(file_path)
    if not fp.is_absolute():
        fp = MEDIA_DIR / file_path

    filename = fp.name
    media_type = _detect_media_type(filename)

    # Get file size if file exists
    file_size = 0
    if fp.exists():
        file_size = fp.stat().st_size
    else:
        print(f"[Media] Warning: file not found at {fp}")

    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO media_assets
               (filename, file_path, media_type, category, tags, lead_id,
                event_date, caption, width, height, file_size, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)""",
            (
                filename,
                str(fp),
                media_type,
                category,
                json.dumps(tags),
                lead_id,
                event_date,
                caption,
                file_size,
                _now_str(),
            ),
        )
        conn.commit()
        asset_id = cur.lastrowid
        print(f"[Media] Registered asset #{asset_id}: {filename} [{category}]")
        return asset_id
    finally:
        conn.close()


def scan_media_directory() -> dict:
    """
    Scan ~/.nexus/media/ for unregistered files and auto-register them.

    Files found in category subdirectories are assigned that category.
    Files in the root media directory are assigned 'marketing'.

    Returns:
        Dict with 'scanned', 'new', 'skipped' counts
    """
    _ensure_media_dir()
    conn = _db()
    try:
        # Get all already-registered file paths
        rows = conn.execute("SELECT file_path FROM media_assets").fetchall()
        registered_paths = {row["file_path"] for row in rows}
    finally:
        conn.close()

    all_extensions = set()
    for exts in MEDIA_TYPE_MAP.values():
        all_extensions.update(exts)

    scanned = 0
    new_count = 0
    skipped = 0

    # Scan root media directory
    for item in MEDIA_DIR.iterdir():
        if item.is_file() and item.suffix.lower() in all_extensions:
            scanned += 1
            if str(item) not in registered_paths:
                register_media(str(item), "marketing", tags=["auto_scan"])
                new_count += 1
            else:
                skipped += 1

    # Scan category subdirectories
    for cat in CATEGORIES:
        cat_dir = MEDIA_DIR / cat
        if not cat_dir.exists():
            continue
        for item in cat_dir.iterdir():
            if item.is_file() and item.suffix.lower() in all_extensions:
                scanned += 1
                if str(item) not in registered_paths:
                    register_media(str(item), cat, tags=["auto_scan"])
                    new_count += 1
                else:
                    skipped += 1

    result = {"scanned": scanned, "new": new_count, "skipped": skipped}
    print(f"[Media] Scan complete: {scanned} files scanned, {new_count} new, {skipped} existing")
    return result


# ═════════════════════════════════════════════════════════════════════════════
# QUERYING & SEARCH
# ═════════════════════════════════════════════════════════════════════════════

def get_media_by_category(category: str, limit: int = 20) -> list[dict]:
    """Get media assets filtered by category, newest first."""
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT id, filename, file_path, media_type, category, tags,
                      lead_id, event_date, caption, file_size, created_at
               FROM media_assets
               WHERE category = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (category, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_media_for_lead(lead_id: int) -> list[dict]:
    """Get all media assets linked to a specific CRM lead."""
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT id, filename, file_path, media_type, category, tags,
                      lead_id, event_date, caption, file_size, created_at
               FROM media_assets
               WHERE lead_id = ?
               ORDER BY created_at DESC""",
            (lead_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def search_media(query: str) -> list[dict]:
    """
    Search media assets by tags, caption, category, or filename.

    The query is matched against tags (JSON), caption, category, and filename
    using case-insensitive LIKE.
    """
    conn = _db()
    try:
        pattern = f"%{query}%"
        rows = conn.execute(
            """SELECT id, filename, file_path, media_type, category, tags,
                      lead_id, event_date, caption, file_size, created_at
               FROM media_assets
               WHERE tags LIKE ? OR caption LIKE ?
                  OR category LIKE ? OR filename LIKE ?
               ORDER BY created_at DESC
               LIMIT 50""",
            (pattern, pattern, pattern, pattern),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_asset_by_id(media_id: int) -> dict | None:
    """Get a single media asset by ID."""
    conn = _db()
    try:
        row = conn.execute(
            """SELECT id, filename, file_path, media_type, category, tags,
                      lead_id, event_date, caption, width, height, file_size, created_at
               FROM media_assets WHERE id = ?""",
            (media_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# COLLECTIONS
# ═════════════════════════════════════════════════════════════════════════════

def create_collection(
    name: str,
    description: str,
    asset_ids: list[int],
    purpose: str = "portfolio",
) -> int:
    """
    Create a curated media collection.

    Args:
        name: Collection name
        description: Description of the collection
        asset_ids: List of media asset IDs to include
        purpose: One of COLLECTION_PURPOSES

    Returns:
        The new collection ID
    """
    if purpose not in COLLECTION_PURPOSES:
        print(f"[Media] Warning: unknown purpose '{purpose}', defaulting to 'portfolio'")
        purpose = "portfolio"

    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO media_collections (name, description, asset_ids, purpose, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (name, description, json.dumps(asset_ids), purpose, _now_str()),
        )
        conn.commit()
        coll_id = cur.lastrowid
        print(f"[Media] Created collection #{coll_id}: '{name}' ({len(asset_ids)} assets)")
        return coll_id
    finally:
        conn.close()


def get_collections(purpose: str | None = None) -> list[dict]:
    """
    List all media collections, optionally filtered by purpose.

    Returns list of collection dicts with parsed asset_ids.
    """
    conn = _db()
    try:
        if purpose:
            rows = conn.execute(
                """SELECT id, name, description, asset_ids, purpose, created_at
                   FROM media_collections
                   WHERE purpose = ?
                   ORDER BY created_at DESC""",
                (purpose,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, name, description, asset_ids, purpose, created_at
                   FROM media_collections
                   ORDER BY created_at DESC"""
            ).fetchall()

        results = []
        for row in rows:
            d = dict(row)
            try:
                d["asset_ids"] = json.loads(d["asset_ids"])
            except (json.JSONDecodeError, TypeError):
                d["asset_ids"] = []
            d["asset_count"] = len(d["asset_ids"])
            results.append(d)
        return results
    finally:
        conn.close()


def get_collection_by_id(collection_id: int) -> dict | None:
    """Get a single collection by ID with parsed asset_ids."""
    conn = _db()
    try:
        row = conn.execute(
            """SELECT id, name, description, asset_ids, purpose, created_at
               FROM media_collections WHERE id = ?""",
            (collection_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["asset_ids"] = json.loads(d["asset_ids"])
        except (json.JSONDecodeError, TypeError):
            d["asset_ids"] = []
        d["asset_count"] = len(d["asset_ids"])
        return d
    finally:
        conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# PORTFOLIO & SOCIAL HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def get_portfolio_assets(limit: int = 10) -> list[dict]:
    """
    Get best assets for portfolio display.

    Prioritizes assets tagged with 'portfolio', 'best', or 'featured',
    then falls back to newest trailer exterior and event setup photos.
    """
    conn = _db()
    try:
        # First try tagged portfolio assets
        rows = conn.execute(
            """SELECT id, filename, file_path, media_type, category, tags,
                      lead_id, event_date, caption, file_size, created_at
               FROM media_assets
               WHERE tags LIKE '%portfolio%' OR tags LIKE '%best%'
                  OR tags LIKE '%featured%'
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()

        if len(rows) >= limit:
            return [dict(r) for r in rows]

        # Fill remaining slots with best category photos
        remaining = limit - len(rows)
        existing_ids = [dict(r)["id"] for r in rows]
        placeholder = ",".join("?" * len(existing_ids)) if existing_ids else "0"

        fill_rows = conn.execute(
            f"""SELECT id, filename, file_path, media_type, category, tags,
                       lead_id, event_date, caption, file_size, created_at
                FROM media_assets
                WHERE category IN ('trailer_exterior', 'trailer_interior', 'event_setup')
                  AND id NOT IN ({placeholder})
                ORDER BY created_at DESC
                LIMIT ?""",
            (*existing_ids, remaining) if existing_ids else (remaining,),
        ).fetchall()

        return [dict(r) for r in rows] + [dict(r) for r in fill_rows]
    finally:
        conn.close()


def get_social_ready_assets(limit: int = 5) -> list[dict]:
    """
    Get assets tagged for social media posting.

    Looks for assets tagged with 'social', 'instagram', 'facebook',
    or belonging to 'marketing' category.
    """
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT id, filename, file_path, media_type, category, tags,
                      lead_id, event_date, caption, file_size, created_at
               FROM media_assets
               WHERE tags LIKE '%social%' OR tags LIKE '%instagram%'
                  OR tags LIKE '%facebook%' OR category = 'marketing'
               ORDER BY created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# TAGGING & LINKING
# ═════════════════════════════════════════════════════════════════════════════

def tag_media(media_id: int, tags: list[str]) -> bool:
    """
    Add tags to an existing media asset (merges with existing tags).

    Args:
        media_id: The asset ID
        tags: List of tag strings to add

    Returns:
        True if asset was found and updated, False otherwise
    """
    conn = _db()
    try:
        row = conn.execute(
            "SELECT tags FROM media_assets WHERE id = ?", (media_id,)
        ).fetchone()
        if not row:
            print(f"[Media] Asset #{media_id} not found")
            return False

        try:
            existing_tags = json.loads(row["tags"])
        except (json.JSONDecodeError, TypeError):
            existing_tags = []

        # Merge tags (deduplicated, preserving order)
        merged = list(existing_tags)
        for t in tags:
            t_clean = t.strip().lower()
            if t_clean and t_clean not in merged:
                merged.append(t_clean)

        conn.execute(
            "UPDATE media_assets SET tags = ? WHERE id = ?",
            (json.dumps(merged), media_id),
        )
        conn.commit()
        print(f"[Media] Tagged asset #{media_id}: {merged}")
        return True
    finally:
        conn.close()


def link_media_to_lead(media_id: int, lead_id: int) -> bool:
    """
    Associate a media asset with a CRM lead.

    Args:
        media_id: The asset ID
        lead_id: The CRM lead ID to link

    Returns:
        True if updated successfully, False if asset not found
    """
    conn = _db()
    try:
        row = conn.execute(
            "SELECT id FROM media_assets WHERE id = ?", (media_id,)
        ).fetchone()
        if not row:
            print(f"[Media] Asset #{media_id} not found")
            return False

        conn.execute(
            "UPDATE media_assets SET lead_id = ? WHERE id = ?",
            (lead_id, media_id),
        )
        conn.commit()
        print(f"[Media] Linked asset #{media_id} to lead #{lead_id}")
        return True
    finally:
        conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# STATS & REPORTING
# ═════════════════════════════════════════════════════════════════════════════

def get_media_stats() -> dict:
    """
    Get comprehensive media library statistics.

    Returns:
        Dict with counts by category, total storage size, media type breakdown,
        linked vs unlinked counts, and collection count.
    """
    conn = _db()
    try:
        # Total count and size
        totals = conn.execute(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(file_size), 0) as total_size FROM media_assets"
        ).fetchone()

        # Counts by category
        cat_rows = conn.execute(
            """SELECT category, COUNT(*) as cnt, COALESCE(SUM(file_size), 0) as cat_size
               FROM media_assets GROUP BY category ORDER BY cnt DESC"""
        ).fetchall()
        by_category = {r["category"]: {"count": r["cnt"], "size": r["cat_size"]} for r in cat_rows}

        # Counts by media type
        type_rows = conn.execute(
            "SELECT media_type, COUNT(*) as cnt FROM media_assets GROUP BY media_type"
        ).fetchall()
        by_type = {r["media_type"]: r["cnt"] for r in type_rows}

        # Linked vs unlinked
        linked = conn.execute(
            "SELECT COUNT(*) as cnt FROM media_assets WHERE lead_id > 0"
        ).fetchone()["cnt"]
        unlinked = totals["cnt"] - linked

        # Collection count
        coll_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM media_collections"
        ).fetchone()["cnt"]

        # Recent assets (last 7 days)
        recent = conn.execute(
            """SELECT COUNT(*) as cnt FROM media_assets
               WHERE created_at >= datetime('now', '-7 days')"""
        ).fetchone()["cnt"]

        return {
            "total_assets": totals["cnt"],
            "total_size": totals["total_size"],
            "total_size_formatted": _format_file_size(totals["total_size"]),
            "by_category": by_category,
            "by_type": by_type,
            "linked_to_leads": linked,
            "unlinked": unlinked,
            "collections": coll_count,
            "added_last_7_days": recent,
        }
    finally:
        conn.close()


def format_media_summary() -> str:
    """Format a Telegram-friendly media library summary."""
    stats = get_media_stats()

    lines = [
        "\U0001f4f7 Media Library — Zoar Bathroom Rentals",
        "=" * 42,
        "",
        f"Total assets:    {stats['total_assets']}",
        f"Storage used:    {stats['total_size_formatted']}",
        f"Collections:     {stats['collections']}",
        f"Added (7 days):  {stats['added_last_7_days']}",
        "",
    ]

    # Category breakdown
    if stats["by_category"]:
        lines.append("By Category:")
        for cat in CATEGORIES:
            if cat in stats["by_category"]:
                info = stats["by_category"][cat]
                emoji = CATEGORY_EMOJIS.get(cat, "\U0001f4ce")
                label = CATEGORY_LABELS.get(cat, cat)
                size_str = _format_file_size(info["size"])
                lines.append(f"  {emoji} {label}: {info['count']} ({size_str})")
        lines.append("")

    # Media type breakdown
    if stats["by_type"]:
        lines.append("By Type:")
        type_emojis = {"photo": "\U0001f5bc", "video": "\U0001f3ac", "document": "\U0001f4c4"}
        for mtype, count in stats["by_type"].items():
            emoji = type_emojis.get(mtype, "\U0001f4ce")
            lines.append(f"  {emoji} {mtype.title()}: {count}")
        lines.append("")

    # Lead linkage
    lines.append(f"Linked to leads: {stats['linked_to_leads']}")
    lines.append(f"General/unlinked: {stats['unlinked']}")
    lines.append("")

    lines.append("Commands:")
    lines.append("  /media search [query]  — Search assets")
    lines.append("  /media tag [id] [tags] — Tag an asset")

    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ═════════════════════════════════════════════════════════════════════════════

def get_random_asset(category: str | None = None) -> dict | None:
    """
    Get a random media asset, optionally filtered by category.

    Useful for social media posting workflows.
    """
    conn = _db()
    try:
        if category and category in CATEGORIES:
            rows = conn.execute(
                """SELECT id, filename, file_path, media_type, category, tags,
                          lead_id, event_date, caption, file_size, created_at
                   FROM media_assets WHERE category = ?""",
                (category,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, filename, file_path, media_type, category, tags,
                          lead_id, event_date, caption, file_size, created_at
                   FROM media_assets"""
            ).fetchall()

        if not rows:
            return None
        chosen = random.choice(rows)
        return dict(chosen)
    finally:
        conn.close()


def get_asset_path(media_id: int) -> str | None:
    """
    Get the full file path for a media asset.

    Returns:
        Absolute file path string, or None if asset not found
    """
    conn = _db()
    try:
        row = conn.execute(
            "SELECT file_path FROM media_assets WHERE id = ?", (media_id,)
        ).fetchone()
        return row["file_path"] if row else None
    finally:
        conn.close()


def delete_asset(media_id: int, delete_file: bool = False) -> bool:
    """
    Remove a media asset from the database.

    Args:
        media_id: The asset ID to remove
        delete_file: If True, also delete the physical file

    Returns:
        True if asset was deleted, False if not found
    """
    conn = _db()
    try:
        row = conn.execute(
            "SELECT file_path FROM media_assets WHERE id = ?", (media_id,)
        ).fetchone()
        if not row:
            print(f"[Media] Asset #{media_id} not found")
            return False

        if delete_file:
            fp = Path(row["file_path"])
            if fp.exists():
                fp.unlink()
                print(f"[Media] Deleted file: {fp}")

        conn.execute("DELETE FROM media_assets WHERE id = ?", (media_id,))
        conn.commit()
        print(f"[Media] Removed asset #{media_id} from database")
        return True
    finally:
        conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# TELEGRAM COMMAND HANDLERS
# ═════════════════════════════════════════════════════════════════════════════

def handle_media_command(text: str) -> str:
    """
    Handle /media — show media library summary.

    Subcommands:
        /media           — Show full summary
        /media scan      — Scan directory for new files
        /media categories — List categories
        /media collections — List collections
    """
    parts = text.strip().split()

    # /media scan
    if len(parts) >= 2 and parts[1].lower() == "scan":
        try:
            result = scan_media_directory()
            return (
                f"\U0001f50d Media Scan Complete\n"
                f"Files scanned: {result['scanned']}\n"
                f"New registered: {result['new']}\n"
                f"Already tracked: {result['skipped']}"
            )
        except Exception as e:
            return f"Scan error: {e}"

    # /media categories
    if len(parts) >= 2 and parts[1].lower() in ("categories", "cats"):
        lines = ["\U0001f4c2 Media Categories", "=" * 35, ""]
        for cat in CATEGORIES:
            emoji = CATEGORY_EMOJIS.get(cat, "\U0001f4ce")
            label = CATEGORY_LABELS.get(cat, cat)
            lines.append(f"  {emoji} {cat} — {label}")
        return "\n".join(lines)

    # /media collections
    if len(parts) >= 2 and parts[1].lower() in ("collections", "coll"):
        try:
            colls = get_collections()
            if not colls:
                return "\U0001f4c1 No collections yet. Create one with the API."
            lines = [f"\U0001f4c1 Media Collections ({len(colls)})", "=" * 35, ""]
            for c in colls:
                lines.append(
                    f"  #{c['id']} {c['name']} — {c['asset_count']} assets [{c['purpose']}]"
                )
            return "\n".join(lines)
        except Exception as e:
            return f"Collections error: {e}"

    # Default: show full summary
    try:
        return format_media_summary()
    except Exception as e:
        return f"Media error: {e}"


def handle_media_search_command(text: str) -> str:
    """
    Handle /media search [query] — search media assets.

    Usage:
        /media search wedding
        /media search trailer exterior
        /media search featured
    """
    parts = text.strip().split(maxsplit=2)

    if len(parts) < 3:
        return (
            "\U0001f50e Media Search\n"
            "Usage: /media search [query]\n\n"
            "Examples:\n"
            "  /media search wedding\n"
            "  /media search trailer\n"
            "  /media search featured"
        )

    query = parts[2].strip()
    if not query:
        return "Please provide a search query."

    try:
        results = search_media(query)
    except Exception as e:
        return f"Search error: {e}"

    if not results:
        return f"\U0001f50e No assets found matching '{query}'"

    lines = [f"\U0001f50e Search: '{query}' — {len(results)} result(s)", "=" * 40, ""]

    for asset in results[:15]:
        cat_emoji = CATEGORY_EMOJIS.get(asset["category"], "\U0001f4ce")
        size_str = _format_file_size(asset["file_size"]) if asset["file_size"] else "?"
        caption_str = f" — {asset['caption']}" if asset.get("caption") else ""
        lead_str = f" [Lead #{asset['lead_id']}]" if asset.get("lead_id", 0) > 0 else ""

        lines.append(
            f"  {cat_emoji} #{asset['id']} {asset['filename']} ({size_str})"
            f"{caption_str}{lead_str}"
        )

        # Show tags if present
        try:
            tags = json.loads(asset["tags"]) if isinstance(asset["tags"], str) else asset["tags"]
        except (json.JSONDecodeError, TypeError):
            tags = []
        if tags:
            lines.append(f"      Tags: {', '.join(tags)}")

    if len(results) > 15:
        lines.append(f"\n  ... and {len(results) - 15} more results")

    return "\n".join(lines)


def handle_media_tag_command(text: str) -> str:
    """
    Handle /media tag [id] [tags] — tag a media asset.

    Usage:
        /media tag 5 portfolio featured
        /media tag 12 social instagram wedding
    """
    parts = text.strip().split()

    if len(parts) < 4:
        return (
            "\U0001f3f7 Media Tagging\n"
            "Usage: /media tag [id] [tag1] [tag2] ...\n\n"
            "Examples:\n"
            "  /media tag 5 portfolio featured\n"
            "  /media tag 12 social instagram"
        )

    # Parse asset ID
    try:
        media_id = int(parts[2])
    except ValueError:
        return f"Invalid asset ID: '{parts[2]}'. Must be a number."

    # Parse tags (everything after the ID)
    new_tags = [t.strip().lower() for t in parts[3:] if t.strip()]
    if not new_tags:
        return "No tags provided."

    try:
        success = tag_media(media_id, new_tags)
    except Exception as e:
        return f"Tagging error: {e}"

    if not success:
        return f"Asset #{media_id} not found."

    # Fetch updated asset for confirmation
    asset = get_asset_by_id(media_id)
    if asset:
        try:
            all_tags = json.loads(asset["tags"]) if isinstance(asset["tags"], str) else asset["tags"]
        except (json.JSONDecodeError, TypeError):
            all_tags = new_tags
        return (
            f"\U0001f3f7 Tagged asset #{media_id} ({asset['filename']})\n"
            f"Tags: {', '.join(all_tags)}"
        )

    return f"\U0001f3f7 Tags added to asset #{media_id}: {', '.join(new_tags)}"
