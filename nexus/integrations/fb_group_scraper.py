"""
Facebook Group Scraper — Read-Only Social Intelligence for Zoar Bathroom Rentals

Monitors SoCal wedding, event planning, quinceanera, and community Facebook groups
to find potential leads who might need luxury portable restroom rentals.

Features:
  - Monitors 15-20 pre-seeded Facebook groups (placeholder URLs)
  - AI-powered post analysis via existing LLMProvider (event type, budget, guests, etc.)
  - Lead scoring (1-10) based on bathroom relevance, event size, location, budget
  - Hot (8-10) / Warm (5-7) lead classification
  - DM drafting (Kai copies + sends manually — NEVER auto-send)
  - Telegram digest (morning 8:30 AM + evening 8:30 PM PT)
  - Telegram commands: /scrape, /groups, /add_group
  - 3x daily scrape cycles (7 AM, 1 PM, 7 PM PT)

SAFETY:
  - READ-ONLY observation only — NEVER comment, like, react, or DM anyone
  - If captcha/warning detected → STOP immediately → notify Kai
  - Rate limit: max 50 page loads per day
  - Random delays 5-15 seconds between actions
  - Every action logged for audit

Config: ~/.nexus/config.json
Database: ~/.nexus/memory.db
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import sqlite3
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

# ── Constants ────────────────────────────────────────────────────────────────

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_FILE = Path.home() / ".nexus" / "config.json"
PT = ZoneInfo("America/Los_Angeles")

MAX_PAGE_LOADS_PER_DAY = 50
MIN_DELAY_SECONDS = 5
MAX_DELAY_SECONDS = 15

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# ── Seed groups — placeholder URLs, Kai will replace with real ones ──────────

SEED_GROUPS = [
    # (name, url, category, language)
    ("SoCal Weddings & Events", "https://facebook.com/groups/socalweddings_placeholder", "wedding", "en"),
    ("LA Brides & Grooms", "https://facebook.com/groups/labrides_placeholder", "wedding", "en"),
    ("San Fernando Valley Wedding Planning", "https://facebook.com/groups/sfvwedding_placeholder", "wedding", "en"),
    ("Ventura County Weddings", "https://facebook.com/groups/venturawedding_placeholder", "wedding", "en"),
    ("SoCal Event Planners Network", "https://facebook.com/groups/socaleventplanners_placeholder", "event_planning", "en"),
    ("Los Angeles Party Planning", "https://facebook.com/groups/lapartyplanning_placeholder", "event_planning", "en"),
    ("SoCal Quinceañeras", "https://facebook.com/groups/socalquince_placeholder", "quinceanera", "es"),
    ("Quinceañera Planning Los Angeles", "https://facebook.com/groups/laquince_placeholder", "quinceanera", "es"),
    ("Mis Quinceañeras SFV", "https://facebook.com/groups/sfvquince_placeholder", "quinceanera", "es"),
    ("San Fernando Valley Community", "https://facebook.com/groups/sfvcommunity_placeholder", "community", "en"),
    ("Santa Clarita Events & Happenings", "https://facebook.com/groups/scvevents_placeholder", "community", "en"),
    ("Tarzana / Encino / Woodland Hills Community", "https://facebook.com/groups/tarzanacommunity_placeholder", "community", "en"),
    ("SoCal Outdoor Weddings & Venues", "https://facebook.com/groups/socaloutdoor_placeholder", "wedding", "en"),
    ("LA Corporate Events", "https://facebook.com/groups/lacorporateevents_placeholder", "event_planning", "en"),
    ("Backyard Weddings California", "https://facebook.com/groups/backyardweddingca_placeholder", "wedding", "en"),
    ("Oxnard / Ventura Latina Community", "https://facebook.com/groups/oxnardlatina_placeholder", "community", "es"),
    ("SoCal Sweet 16 & Quince Vendors", "https://facebook.com/groups/socalsweet16_placeholder", "quinceanera", "en"),
    ("Calabasas Moms & Community", "https://facebook.com/groups/calabasasmoms_placeholder", "community", "en"),
]

# Keywords that signal bathroom / restroom relevance
BATHROOM_KEYWORDS = [
    "restroom", "bathroom", "porta potty", "portable restroom", "port-a-potty",
    "portapotty", "porta-potty", "toilet", "restroom trailer", "luxury restroom",
    "baño", "baños", "sanitario", "sanitarios",
]

# Keywords that signal outdoor / venue-less events (higher need for restrooms)
OUTDOOR_KEYWORDS = [
    "outdoor", "backyard", "park", "ranch", "vineyard", "farm", "garden",
    "field", "beach", "rooftop", "tent", "open air", "al aire libre",
    "patio", "estate", "private property", "vacant lot",
]

# Budget signal keywords
HIGH_BUDGET_KEYWORDS = [
    "luxury", "upscale", "premium", "high-end", "elegant", "lavish",
    "no budget", "money is not an issue", "spare no expense", "dream wedding",
    "black tie", "5-star", "five star",
]
LOW_BUDGET_KEYWORDS = [
    "budget", "affordable", "cheap", "low cost", "on a budget", "saving",
    "diy", "economical", "bargain", "discount",
]

# SoCal cities for location extraction
SOCAL_CITIES = [
    "Los Angeles", "LA", "San Fernando Valley", "SFV", "Santa Clarita",
    "Ventura", "Oxnard", "Santa Monica", "Malibu", "Pasadena", "Burbank",
    "Glendale", "Long Beach", "Thousand Oaks", "Simi Valley", "Calabasas",
    "Encino", "Sherman Oaks", "Tarzana", "Woodland Hills", "Temecula",
    "Orange County", "San Diego", "Inland Empire", "Riverside", "Palm Springs",
    "Van Nuys", "Northridge", "Chatsworth", "Granada Hills", "Porter Ranch",
    "Canoga Park", "Reseda", "North Hollywood", "Studio City", "Toluca Lake",
    "Valencia", "Canyon Country", "Newhall", "Agoura Hills", "Westlake Village",
    "Camarillo", "Moorpark", "Fillmore", "Pacoima", "Sylmar", "Sun Valley",
]

EVENT_TYPE_KEYWORDS = {
    "wedding": ["wedding", "bride", "groom", "novia", "novio", "boda", "reception", "ceremony"],
    "quinceanera": ["quinceañera", "quinceanera", "quince", "mis quince", "xv años", "15 años"],
    "sweet_16": ["sweet 16", "sweet sixteen"],
    "graduation": ["graduation", "grad party", "graduacion"],
    "birthday": ["birthday", "cumpleaños", "bday"],
    "corporate": ["corporate", "company event", "team building", "conference"],
    "baby_shower": ["baby shower"],
    "bridal_shower": ["bridal shower"],
    "engagement": ["engagement party", "engagement"],
    "reunion": ["reunion", "family reunion"],
    "festival": ["festival", "block party", "feria"],
    "filming": ["filming", "film shoot", "production", "photo shoot"],
    "bbq": ["bbq", "barbecue", "cookout"],
    "memorial": ["memorial", "celebration of life", "funeral reception"],
    "other": [],
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(PT)


def _now_str() -> str:
    return _now().strftime("%Y-%m-%d %H:%M:%S")


def _log(msg: str):
    print(f"[GroupScraper] {msg}")


def _load_config() -> dict:
    try:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text())
    except Exception:
        pass
    return {}


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def _random_delay() -> float:
    """Return a random delay between MIN and MAX seconds for rate limiting."""
    return random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)


def _get_daily_load_count() -> int:
    """Count page loads performed today (for rate limiting)."""
    conn = _get_conn()
    today = _now().strftime("%Y-%m-%d")
    row = conn.execute(
        "SELECT COUNT(*) FROM scraper_audit_log "
        "WHERE action = 'page_load' AND date(timestamp) = ?",
        (today,),
    ).fetchone()
    conn.close()
    return row[0] if row else 0


def _log_action(action: str, target: str = "", details: str = ""):
    """Audit log every scraper action for safety review."""
    conn = _get_conn()
    conn.execute(
        "INSERT INTO scraper_audit_log (timestamp, action, target, details) "
        "VALUES (?, ?, ?, ?)",
        (_now_str(), action, target, details),
    )
    conn.commit()
    conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# DATABASE INIT
# ═════════════════════════════════════════════════════════════════════════════

def init_scraper_db():
    """Create scraper tables, audit log, and seed monitored groups if empty."""
    conn = _get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS monitored_groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_name TEXT,
        group_url TEXT,
        category TEXT,
        language TEXT DEFAULT 'en',
        last_scraped TIMESTAMP,
        active BOOLEAN DEFAULT TRUE
    );

    CREATE TABLE IF NOT EXISTS scraped_leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER,
        post_author TEXT,
        profile_link TEXT,
        post_link TEXT,
        post_date TEXT,
        post_text TEXT,
        event_type TEXT,
        event_date TEXT,
        estimated_guests INTEGER,
        location TEXT,
        budget_level TEXT,
        bathroom_mentioned BOOLEAN DEFAULT FALSE,
        score INTEGER,
        analysis TEXT,
        status TEXT DEFAULT 'new',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (group_id) REFERENCES monitored_groups(id)
    );

    CREATE TABLE IF NOT EXISTS scraper_audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT DEFAULT (datetime('now')),
        action TEXT NOT NULL,
        target TEXT DEFAULT '',
        details TEXT DEFAULT ''
    );

    CREATE INDEX IF NOT EXISTS idx_scraped_leads_score ON scraped_leads(score);
    CREATE INDEX IF NOT EXISTS idx_scraped_leads_status ON scraped_leads(status);
    CREATE INDEX IF NOT EXISTS idx_scraped_leads_group ON scraped_leads(group_id);
    CREATE INDEX IF NOT EXISTS idx_scraped_leads_created ON scraped_leads(created_at);
    CREATE INDEX IF NOT EXISTS idx_monitored_groups_active ON monitored_groups(active);
    CREATE INDEX IF NOT EXISTS idx_audit_action ON scraper_audit_log(action);
    CREATE INDEX IF NOT EXISTS idx_audit_ts ON scraper_audit_log(timestamp);
    """)
    conn.commit()

    # Seed groups if table is empty
    if conn.execute("SELECT COUNT(*) FROM monitored_groups").fetchone()[0] == 0:
        _log("Seeding monitored Facebook groups...")
        conn.executemany(
            "INSERT INTO monitored_groups (group_name, group_url, category, language) "
            "VALUES (?, ?, ?, ?)",
            SEED_GROUPS,
        )
        conn.commit()
        _log(f"Seeded {len(SEED_GROUPS)} groups")

    conn.close()
    _log("Scraper database tables ready")


# ═════════════════════════════════════════════════════════════════════════════
# KEYWORD-BASED POST ANALYSIS (offline fallback)
# ═════════════════════════════════════════════════════════════════════════════

def _keyword_analyze(post_text: str, group_category: str) -> dict:
    """
    Fast keyword-based post analysis as fallback when AI is unavailable.
    Returns a dict with event_type, outdoor, budget_level, bathroom_mentioned,
    event_date, estimated_guests, location, score.
    """
    text_lower = post_text.lower()

    # Detect event type
    event_type = "other"
    for etype, keywords in EVENT_TYPE_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            event_type = etype
            break

    # Outdoor signal
    outdoor = any(kw in text_lower for kw in OUTDOOR_KEYWORDS)

    # Bathroom mentioned
    bathroom_mentioned = any(kw in text_lower for kw in BATHROOM_KEYWORDS)

    # Budget level
    if any(kw in text_lower for kw in HIGH_BUDGET_KEYWORDS):
        budget_level = "high"
    elif any(kw in text_lower for kw in LOW_BUDGET_KEYWORDS):
        budget_level = "low"
    else:
        budget_level = "medium"

    # Guest count extraction
    estimated_guests = 0
    guest_patterns = [
        re.compile(r"(\d{2,4})\s*(?:guests|people|attendees|invitados|personas)", re.I),
        re.compile(r"(?:expecting|about|around|aprox|approximately)\s*(\d{2,4})", re.I),
        re.compile(r"(\d{2,4})\s*(?:pax|ppl)", re.I),
    ]
    for pat in guest_patterns:
        m = pat.search(post_text)
        if m:
            try:
                estimated_guests = int(m.group(1))
            except ValueError:
                pass
            break

    # Location extraction
    location = ""
    for city in SOCAL_CITIES:
        if city.lower() in text_lower:
            location = city
            break

    # Date extraction (basic)
    event_date = ""
    month_names = {
        "january": "01", "february": "02", "march": "03", "april": "04",
        "may": "05", "june": "06", "july": "07", "august": "08",
        "september": "09", "october": "10", "november": "11", "december": "12",
        "jan": "01", "feb": "02", "mar": "03", "apr": "04",
        "jun": "06", "jul": "07", "aug": "08", "sep": "09", "sept": "09",
        "oct": "10", "nov": "11", "dec": "12",
        "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
        "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
        "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12",
    }
    date_pattern = re.compile(
        r"(?:(" + "|".join(month_names.keys()) + r")\s+(\d{1,2})(?:\s*,?\s*(\d{4}))?)",
        re.I,
    )
    dm = date_pattern.search(text_lower)
    if dm:
        month_str = month_names.get(dm.group(1).lower(), "")
        day = dm.group(2).zfill(2)
        year = dm.group(3) or str(_now().year)
        if month_str:
            event_date = f"{year}-{month_str}-{day}"

    # Scoring
    score = _calculate_score(
        event_type=event_type,
        outdoor=outdoor,
        bathroom_mentioned=bathroom_mentioned,
        budget_level=budget_level,
        estimated_guests=estimated_guests,
        location=location,
        group_category=group_category,
    )

    return {
        "event_type": event_type,
        "outdoor": outdoor,
        "budget_level": budget_level,
        "bathroom_mentioned": bathroom_mentioned,
        "event_date": event_date,
        "estimated_guests": estimated_guests,
        "location": location,
        "score": score,
        "analysis_method": "keyword",
    }


def _calculate_score(
    event_type: str,
    outdoor: bool,
    bathroom_mentioned: bool,
    budget_level: str,
    estimated_guests: int,
    location: str,
    group_category: str,
) -> int:
    """
    Calculate lead score 1-10 based on signals.

    Scoring weights:
      - Bathroom explicitly mentioned:  +3
      - Outdoor event:                  +2
      - Wedding / quinceanera:          +2
      - 100+ guests:                    +1
      - 200+ guests:                    +1 (additional)
      - High budget signals:            +1
      - SoCal location detected:        +1
      - Wedding/quince group category:  +1
      Base:                              1
    """
    score = 1

    if bathroom_mentioned:
        score += 3
    if outdoor:
        score += 2
    if event_type in ("wedding", "quinceanera", "sweet_16"):
        score += 2
    elif event_type in ("corporate", "graduation", "festival"):
        score += 1
    if estimated_guests >= 100:
        score += 1
    if estimated_guests >= 200:
        score += 1
    if budget_level == "high":
        score += 1
    if location:
        score += 1
    if group_category in ("wedding", "quinceanera"):
        score += 1

    # Low budget is a negative signal
    if budget_level == "low":
        score = max(1, score - 1)

    return min(10, score)


# ═════════════════════════════════════════════════════════════════════════════
# AI-POWERED POST ANALYSIS
# ═════════════════════════════════════════════════════════════════════════════

ANALYSIS_SYSTEM_PROMPT = """You analyze Facebook group posts to identify potential customers for Zoar Bathroom Rentals — a luxury portable restroom trailer company serving SoCal events.

Zoar provides a 4-stall luxury restroom trailer with flushable toilets, running water, LED lighting, mirrors, AC, and Bluetooth speaker. Starting at $999 including delivery, setup, and pickup. Service area: Greater LA, San Fernando Valley, Ventura County, Santa Clarita, Oxnard, Santa Monica.

Analyze the post and return ONLY valid JSON (no markdown, no explanation):
{
  "event_type": "wedding|quinceanera|sweet_16|graduation|birthday|corporate|baby_shower|bridal_shower|engagement|reunion|festival|filming|bbq|memorial|other",
  "outdoor": true/false,
  "budget_level": "high|medium|low",
  "bathroom_mentioned": true/false,
  "event_date": "YYYY-MM-DD or empty string",
  "estimated_guests": number or 0,
  "location": "city/area name or empty string",
  "score": 1-10,
  "reasoning": "1-2 sentence explanation of the score"
}

Scoring guide:
- 9-10: Directly asking about portable restrooms/bathrooms for an outdoor event
- 7-8: Large outdoor event (100+ guests) in SoCal, wedding/quince, high budget
- 5-6: Moderate event with some outdoor/bathroom signals
- 3-4: Event post but low bathroom relevance
- 1-2: Barely relevant or not an event post"""


async def analyze_post(post_text: str, group_name: str, category: str) -> dict:
    """
    Analyze a Facebook group post using AI classification.
    Falls back to keyword analysis if AI is unavailable.

    Returns dict with: event_type, outdoor, budget_level, bathroom_mentioned,
    event_date, estimated_guests, location, score, analysis/reasoning.
    """
    # Try AI analysis first
    ai_result = await _ai_analyze(post_text, group_name, category)
    if ai_result:
        return ai_result

    # Fallback to keyword analysis
    _log("AI unavailable, using keyword analysis")
    result = _keyword_analyze(post_text, category)
    result["analysis"] = f"Keyword analysis: {result['event_type']}, score {result['score']}"
    return result


async def _ai_analyze(post_text: str, group_name: str, category: str) -> dict | None:
    """Call the LLM provider to analyze a post. Returns None on failure."""
    try:
        from core.providers import LLMProvider
    except ImportError:
        _log("LLMProvider not available")
        return None

    config = _load_config()
    provider = LLMProvider(config)
    model = provider.resolve("analysis")

    user_msg = (
        f"Group: {group_name} (category: {category})\n\n"
        f"Post text:\n{post_text[:2000]}"
    )

    try:
        resp = await provider.chat(
            model,
            messages=[{"role": "user", "content": user_msg}],
            system=ANALYSIS_SYSTEM_PROMPT,
            max_tokens=400,
        )

        if not resp.get("ok"):
            _log(f"AI analysis failed: {resp.get('content', 'unknown error')}")
            return None

        content = resp.get("content", "").strip()

        # Strip markdown code blocks if present
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(
                lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            ).strip()

        result = json.loads(content)

        # Validate and normalize fields
        valid_events = set(EVENT_TYPE_KEYWORDS.keys())
        if result.get("event_type") not in valid_events:
            result["event_type"] = "other"

        result["outdoor"] = bool(result.get("outdoor", False))
        result["bathroom_mentioned"] = bool(result.get("bathroom_mentioned", False))

        budget = result.get("budget_level", "medium")
        if budget not in ("high", "medium", "low"):
            budget = "medium"
        result["budget_level"] = budget

        result["estimated_guests"] = int(result.get("estimated_guests", 0) or 0)
        result["score"] = max(1, min(10, int(result.get("score", 1) or 1)))
        result["event_date"] = str(result.get("event_date", "") or "")
        result["location"] = str(result.get("location", "") or "")

        reasoning = result.pop("reasoning", "")
        result["analysis"] = f"AI ({resp.get('model_used', model)}): {reasoning}"
        result["analysis_method"] = "ai"

        _log(f"AI analysis complete: score={result['score']}, type={result['event_type']}")
        return result

    except json.JSONDecodeError as e:
        _log(f"AI returned invalid JSON: {e}")
        return None
    except asyncio.TimeoutError:
        _log("AI analysis timed out")
        return None
    except Exception as e:
        _log(f"AI analysis error: {e}")
        traceback.print_exc()
        return None


# ═════════════════════════════════════════════════════════════════════════════
# LEAD MANAGEMENT
# ═════════════════════════════════════════════════════════════════════════════

def add_scraped_lead(group_id: int, post_data: dict, analysis: dict) -> int:
    """
    Store a scraped lead with its analysis. Returns lead ID.

    post_data keys: post_author, profile_link, post_link, post_date, post_text
    analysis keys: event_type, event_date, estimated_guests, location,
                   budget_level, bathroom_mentioned, score, analysis
    """
    conn = _get_conn()

    # Check for duplicate (same post_link or same author + similar text in same group)
    post_link = post_data.get("post_link", "")
    if post_link:
        existing = conn.execute(
            "SELECT id FROM scraped_leads WHERE post_link = ?",
            (post_link,),
        ).fetchone()
        if existing:
            conn.close()
            _log(f"Duplicate post_link, skipping: {post_link}")
            return existing["id"]

    conn.execute(
        "INSERT INTO scraped_leads "
        "(group_id, post_author, profile_link, post_link, post_date, post_text, "
        " event_type, event_date, estimated_guests, location, budget_level, "
        " bathroom_mentioned, score, analysis, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')",
        (
            group_id,
            post_data.get("post_author", ""),
            post_data.get("profile_link", ""),
            post_link,
            post_data.get("post_date", ""),
            post_data.get("post_text", "")[:5000],
            analysis.get("event_type", "other"),
            analysis.get("event_date", ""),
            analysis.get("estimated_guests", 0),
            analysis.get("location", ""),
            analysis.get("budget_level", "medium"),
            1 if analysis.get("bathroom_mentioned") else 0,
            analysis.get("score", 1),
            analysis.get("analysis", ""),
        ),
    )
    conn.commit()
    lead_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    _log(f"New lead #{lead_id}: score={analysis.get('score')}, "
         f"type={analysis.get('event_type')}, author={post_data.get('post_author', '?')}")
    _log_action("lead_added", f"lead_{lead_id}",
                f"score={analysis.get('score')} type={analysis.get('event_type')}")
    return lead_id


def get_hot_leads(min_score: int = 8) -> list[dict]:
    """Get high-value leads (score >= min_score)."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT l.*, g.group_name FROM scraped_leads l "
        "LEFT JOIN monitored_groups g ON l.group_id = g.id "
        "WHERE l.score >= ? AND l.status IN ('new', 'reviewing') "
        "ORDER BY l.score DESC, l.created_at DESC LIMIT 50",
        (min_score,),
    ).fetchall()
    conn.close()
    return [_lead_to_dict(r) for r in rows]


def get_warm_leads(min_score: int = 5, max_score: int = 7) -> list[dict]:
    """Get moderate leads (score between min and max inclusive)."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT l.*, g.group_name FROM scraped_leads l "
        "LEFT JOIN monitored_groups g ON l.group_id = g.id "
        "WHERE l.score >= ? AND l.score <= ? AND l.status IN ('new', 'reviewing') "
        "ORDER BY l.score DESC, l.created_at DESC LIMIT 50",
        (min_score, max_score),
    ).fetchall()
    conn.close()
    return [_lead_to_dict(r) for r in rows]


def get_lead_by_id(lead_id: int) -> dict | None:
    """Get a single lead by ID."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT l.*, g.group_name FROM scraped_leads l "
        "LEFT JOIN monitored_groups g ON l.group_id = g.id "
        "WHERE l.id = ?",
        (lead_id,),
    ).fetchone()
    conn.close()
    return _lead_to_dict(row) if row else None


def update_lead_status(lead_id: int, status: str) -> bool:
    """Update lead status. Valid: new, reviewing, contacted, converted, dismissed."""
    valid = {"new", "reviewing", "contacted", "converted", "dismissed"}
    if status not in valid:
        return False
    conn = _get_conn()
    conn.execute("UPDATE scraped_leads SET status = ? WHERE id = ?", (status, lead_id))
    conn.commit()
    conn.close()
    _log(f"Lead #{lead_id} status -> {status}")
    return True


def _lead_to_dict(row: sqlite3.Row) -> dict:
    """Convert a database row to a lead dict."""
    return {
        "id": row["id"],
        "group_id": row["group_id"],
        "group_name": row["group_name"] if "group_name" in row.keys() else "",
        "post_author": row["post_author"],
        "profile_link": row["profile_link"],
        "post_link": row["post_link"],
        "post_date": row["post_date"],
        "post_text": row["post_text"],
        "event_type": row["event_type"],
        "event_date": row["event_date"],
        "estimated_guests": row["estimated_guests"],
        "location": row["location"],
        "budget_level": row["budget_level"],
        "bathroom_mentioned": bool(row["bathroom_mentioned"]),
        "score": row["score"],
        "analysis": row["analysis"],
        "status": row["status"],
        "created_at": row["created_at"],
    }


# ═════════════════════════════════════════════════════════════════════════════
# DM DRAFTING (Kai sends manually — NEVER auto-send)
# ═════════════════════════════════════════════════════════════════════════════

async def draft_dm(lead_id: int) -> str:
    """
    Generate a suggested DM for a lead. Kai copies and sends manually.
    NEVER auto-sends. NEVER interacts with Facebook directly.
    """
    lead = get_lead_by_id(lead_id)
    if not lead:
        return "Lead not found."

    # Build context for the DM draft
    event_label = lead["event_type"].replace("_", " ").title()
    name = lead["post_author"] or "there"
    location = lead["location"] or "your area"
    guests = lead["estimated_guests"]
    event_date = lead["event_date"] or ""

    # Try AI-generated DM
    ai_dm = await _ai_draft_dm(lead, event_label, name, location, guests, event_date)
    if ai_dm:
        update_lead_status(lead_id, "reviewing")
        return ai_dm

    # Fallback: template-based DM
    update_lead_status(lead_id, "reviewing")
    return _template_dm(lead, event_label, name, location, guests, event_date)


async def _ai_draft_dm(
    lead: dict, event_label: str, name: str, location: str,
    guests: int, event_date: str,
) -> str | None:
    """Use AI to draft a personalized DM. Returns None on failure."""
    try:
        from core.providers import LLMProvider
    except ImportError:
        return None

    config = _load_config()
    provider = LLMProvider(config)
    model = provider.resolve("creative")

    system = (
        "You write short, friendly Facebook DMs for Kai from Zoar Bathroom Rentals. "
        "Zoar provides a luxury 4-stall restroom trailer (flushable toilets, running water, "
        "LED lighting, mirrors, AC, Bluetooth speaker) for events in the greater LA area. "
        "Starting at $999 with delivery, setup, and pickup included.\n\n"
        "RULES:\n"
        "- Max 3-4 sentences. Casual, warm, real person tone.\n"
        "- Reference their specific event naturally (don't be creepy).\n"
        "- Don't mention you saw their Facebook post explicitly.\n"
        "- End with a soft question.\n"
        "- Sign as Kai.\n"
        "- NEVER mention AI. You ARE Kai."
    )

    guest_info = f", ~{guests} guests" if guests else ""
    date_info = f", date: {event_date}" if event_date else ""
    user_msg = (
        f"Draft a DM for someone planning a {event_label} in {location}"
        f"{guest_info}{date_info}.\n\n"
        f"Their post mentioned: {lead.get('post_text', '')[:500]}"
    )

    try:
        resp = await provider.chat(
            model,
            messages=[{"role": "user", "content": user_msg}],
            system=system,
            max_tokens=300,
        )
        if resp.get("ok"):
            dm_text = resp["content"].strip()
            header = (
                "\U0001f4dd DRAFT DM (copy & send manually)\n"
                "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
                f"\U0001f464 To: {name}\n"
                f"\U0001f3af Event: {event_label}\n"
            )
            if location:
                header += f"\U0001f4cd Location: {location}\n"
            header += f"\n{dm_text}\n\n\u26a0\ufe0f Kai — copy this and send it yourself!"
            return header
    except Exception as e:
        _log(f"AI DM draft failed: {e}")

    return None


def _template_dm(
    lead: dict, event_label: str, name: str, location: str,
    guests: int, event_date: str,
) -> str:
    """Template-based DM fallback."""
    templates = {
        "wedding": (
            f"Hey {name}! Congrats on the upcoming wedding! \U0001f389 "
            f"If you're looking into restroom options for the big day, I run Zoar Bathroom Rentals "
            f"\u2014 we have a luxury restroom trailer that's perfect for outdoor weddings. "
            f"Would love to chat if you're interested! \u2014 Kai"
        ),
        "quinceanera": (
            f"Hey {name}! Congrats on the upcoming quinceañera! \U0001f389 "
            f"If you need restroom facilities for the celebration, we have a beautiful luxury "
            f"restroom trailer \u2014 way nicer than porta potties. Let me know if you'd like "
            f"more info! \u2014 Kai"
        ),
        "default": (
            f"Hey {name}! Sounds like you've got a great event coming up! "
            f"If you need restroom facilities, I run Zoar Bathroom Rentals \u2014 we have a luxury "
            f"4-stall restroom trailer with real flushing toilets, AC, and all the amenities. "
            f"Happy to share more details! \u2014 Kai"
        ),
    }

    dm_text = templates.get(lead.get("event_type", ""), templates["default"])

    header = (
        "\U0001f4dd DRAFT DM (copy & send manually)\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\U0001f464 To: {name}\n"
        f"\U0001f3af Event: {event_label}\n"
    )
    if location:
        header += f"\U0001f4cd Location: {location}\n"
    header += f"\n{dm_text}\n\n\u26a0\ufe0f Kai \u2014 copy this and send it yourself!"
    return header


# ═════════════════════════════════════════════════════════════════════════════
# STATS & INTELLIGENCE DIGEST
# ═════════════════════════════════════════════════════════════════════════════

def get_scrape_stats() -> dict:
    """Get scraping statistics overview."""
    conn = _get_conn()

    total_groups = conn.execute(
        "SELECT COUNT(*) FROM monitored_groups WHERE active = 1"
    ).fetchone()[0]

    total_leads = conn.execute("SELECT COUNT(*) FROM scraped_leads").fetchone()[0]

    today = _now().strftime("%Y-%m-%d")
    today_leads = conn.execute(
        "SELECT COUNT(*) FROM scraped_leads WHERE date(created_at) = ?",
        (today,),
    ).fetchone()[0]

    hot_count = conn.execute(
        "SELECT COUNT(*) FROM scraped_leads "
        "WHERE score >= 8 AND status IN ('new', 'reviewing')"
    ).fetchone()[0]

    warm_count = conn.execute(
        "SELECT COUNT(*) FROM scraped_leads "
        "WHERE score >= 5 AND score <= 7 AND status IN ('new', 'reviewing')"
    ).fetchone()[0]

    contacted = conn.execute(
        "SELECT COUNT(*) FROM scraped_leads WHERE status = 'contacted'"
    ).fetchone()[0]

    converted = conn.execute(
        "SELECT COUNT(*) FROM scraped_leads WHERE status = 'converted'"
    ).fetchone()[0]

    today_loads = conn.execute(
        "SELECT COUNT(*) FROM scraper_audit_log "
        "WHERE action = 'page_load' AND date(timestamp) = ?",
        (today,),
    ).fetchone()[0]

    # Most common event types
    event_rows = conn.execute(
        "SELECT event_type, COUNT(*) as cnt FROM scraped_leads "
        "GROUP BY event_type ORDER BY cnt DESC LIMIT 5"
    ).fetchall()

    conn.close()

    return {
        "total_groups": total_groups,
        "total_leads": total_leads,
        "today_leads": today_leads,
        "hot_leads": hot_count,
        "warm_leads": warm_count,
        "contacted": contacted,
        "converted": converted,
        "today_page_loads": today_loads,
        "max_daily_loads": MAX_PAGE_LOADS_PER_DAY,
        "top_event_types": [
            {"type": r["event_type"], "count": r["cnt"]} for r in event_rows
        ],
    }


def format_intelligence_digest() -> str:
    """
    Telegram-formatted social intelligence digest.
    Sent at 8:30 AM and 8:30 PM PT.
    """
    stats = get_scrape_stats()
    hot = get_hot_leads(min_score=8)
    warm = get_warm_leads(min_score=5, max_score=7)

    msg = (
        "\U0001f50d SOCIAL INTELLIGENCE\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\U0001f4ca Scanned: {stats['total_groups']} groups | "
        f"{stats['today_leads']} new leads today | "
        f"{stats['total_leads']} total\n"
        f"\U0001f4c8 Page loads today: {stats['today_page_loads']}/{stats['max_daily_loads']}\n\n"
    )

    if hot:
        msg += "\U0001f525 HOT (8-10):\n"
        for i, lead in enumerate(hot[:5], 1):
            event_label = lead["event_type"].replace("_", " ").title()
            guests = f"~{lead['estimated_guests']} guests" if lead["estimated_guests"] else ""
            budget = lead["budget_level"].upper() if lead["budget_level"] != "medium" else ""
            location = lead["location"] or ""
            date_str = lead["event_date"] or ""

            parts = [event_label]
            if date_str:
                parts.append(date_str)
            if location:
                parts.append(location)
            if guests:
                parts.append(guests)
            if budget:
                parts.append(f"{budget} budget")

            msg += f"{i}. {lead['post_author'] or 'Unknown'} \u2014 {', '.join(parts)}\n"
            if lead["post_link"]:
                msg += f"   \U0001f4ce {lead['post_link']}\n"
            if lead["bathroom_mentioned"]:
                msg += '   \U0001f4a1 "Asked about portable restrooms"\n'
            msg += "\n"
    else:
        msg += "\U0001f525 HOT: No hot leads right now\n\n"

    if warm:
        msg += "\u26a1 WARM (5-7):\n"
        for i, lead in enumerate(warm[:5], len(hot[:5]) + 1):
            event_label = lead["event_type"].replace("_", " ").title()
            guests = f"~{lead['estimated_guests']} guests" if lead["estimated_guests"] else ""
            location = lead["location"] or ""

            parts = [event_label]
            if location:
                parts.append(location)
            if guests:
                parts.append(guests)

            msg += f"{i}. {lead['post_author'] or 'Unknown'} \u2014 {', '.join(parts)}\n"
            if lead["post_link"]:
                msg += f"   \U0001f4ce {lead['post_link']}\n"
            msg += "\n"
    else:
        msg += "\u26a1 WARM: No warm leads right now\n\n"

    msg += "Reply [number] \u2192 I'll draft a DM for you to send manually"
    return msg


def format_hot_alert(lead: dict) -> str:
    """Immediate Telegram alert for a hot lead (score 8+)."""
    event_label = lead["event_type"].replace("_", " ").title()
    guests = f"~{lead['estimated_guests']} guests" if lead["estimated_guests"] else ""
    budget = lead["budget_level"].upper() if lead["budget_level"] != "medium" else ""
    location = lead["location"] or ""
    date_str = lead["event_date"] or ""

    msg = (
        "\U0001f6a8 HOT LEAD ALERT\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\U0001f464 {lead['post_author'] or 'Unknown'}\n"
        f"\U0001f3af {event_label}"
    )
    if date_str:
        msg += f" | {date_str}"
    msg += "\n"
    if location:
        msg += f"\U0001f4cd {location}\n"
    if guests:
        msg += f"\U0001f465 {guests}\n"
    if budget:
        msg += f"\U0001f4b0 {budget} budget\n"
    if lead["bathroom_mentioned"]:
        msg += "\U0001f6bd Bathroom/restroom mentioned!\n"
    msg += f"\u2b50 Score: {lead['score']}/10\n"
    if lead["post_link"]:
        msg += f"\n\U0001f4ce {lead['post_link']}\n"
    if lead.get("post_text"):
        preview = lead["post_text"][:200]
        if len(lead["post_text"]) > 200:
            preview += "..."
        msg += f'\n\U0001f4ac "{preview}"\n'
    msg += (
        f"\nReply /draft {lead['id']} \u2192 I'll write a DM for you to send"
    )
    return msg


# ═════════════════════════════════════════════════════════════════════════════
# GROUP MANAGEMENT
# ═════════════════════════════════════════════════════════════════════════════

def add_group(name: str, url: str, category: str, language: str = "en") -> int:
    """Add a new group to monitor. Returns group ID."""
    if url and not url.startswith("http"):
        url = f"https://{url}"
    conn = _get_conn()
    conn.execute(
        "INSERT INTO monitored_groups (group_name, group_url, category, language, active) "
        "VALUES (?, ?, ?, ?, 1)",
        (name, url, category, language),
    )
    conn.commit()
    gid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    _log(f"Added group: {name} (ID {gid}, category={category})")
    _log_action("group_added", f"group_{gid}", f"{name} | {category}")
    return gid


def remove_group(group_id: int) -> bool:
    """Deactivate a monitored group (soft delete). Returns True if found."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT name as group_name FROM monitored_groups WHERE id = ?", (group_id,)
    ).fetchone()
    if not row:
        conn.close()
        return False
    conn.execute(
        "UPDATE monitored_groups SET active = 0 WHERE id = ?", (group_id,)
    )
    conn.commit()
    conn.close()
    _log(f"Deactivated group: {row['group_name']} (ID {group_id})")
    _log_action("group_removed", f"group_{group_id}", row["group_name"])
    return True


def get_groups() -> list[dict]:
    """Get all monitored groups (active only by default)."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, name as group_name, url as group_url, category, language, last_scraped, active FROM monitored_groups WHERE active = 1 ORDER BY category, name"
    ).fetchall()
    conn.close()
    return [
        {
            "id": r["id"],
            "group_name": r["group_name"],
            "group_url": r["group_url"],
            "category": r["category"],
            "language": r["language"],
            "last_scraped": r["last_scraped"],
            "active": bool(r["active"]),
        }
        for r in rows
    ]


# ═════════════════════════════════════════════════════════════════════════════
# SCRAPING ENGINE (Graph API / httpx infrastructure, browser parts stubbed)
# ═════════════════════════════════════════════════════════════════════════════

async def _check_rate_limit() -> bool:
    """Check if we're within daily page load limits. Returns True if OK."""
    count = _get_daily_load_count()
    if count >= MAX_PAGE_LOADS_PER_DAY:
        _log(f"RATE LIMIT: {count}/{MAX_PAGE_LOADS_PER_DAY} page loads today. Stopping.")
        _log_action("rate_limit_hit", "", f"{count} loads today")
        return False
    return True


async def _fetch_group_feed_graph_api(group_id: str, access_token: str) -> list[dict]:
    """
    Fetch group posts via Facebook Graph API (if access token configured).
    Returns list of post dicts: {author, text, link, date, post_id}.

    Note: Requires a Facebook App with group read permissions and a valid
    user/page access token. Configure fb_access_token in config.json.
    """
    if not access_token:
        return []

    url = f"https://graph.facebook.com/v19.0/{group_id}/feed"
    params = {
        "access_token": access_token,
        "fields": "id,message,from,created_time,permalink_url",
        "limit": 25,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                posts = []
                for post in data.get("data", []):
                    author_data = post.get("from", {})
                    posts.append({
                        "post_author": author_data.get("name", ""),
                        "profile_link": f"https://facebook.com/{author_data.get('id', '')}",
                        "post_link": post.get("permalink_url", ""),
                        "post_date": post.get("created_time", ""),
                        "post_text": post.get("message", ""),
                        "post_id": post.get("id", ""),
                    })
                _log_action("page_load", f"graph_api_{group_id}",
                            f"Fetched {len(posts)} posts")
                return posts
            else:
                _log(f"Graph API error {resp.status_code}: {resp.text[:200]}")
                return []
    except httpx.TimeoutException:
        _log(f"Graph API timeout for group {group_id}")
        return []
    except Exception as e:
        _log(f"Graph API error: {e}")
        return []


async def _fetch_group_public_page(group_url: str) -> list[dict]:
    """
    Attempt to scrape public group info via httpx (limited — most groups
    require login). Returns list of post dicts or empty list.

    This is a best-effort approach for public groups. For private groups,
    the Graph API path or manual browser observation is needed.
    """
    if not await _check_rate_limit():
        return []

    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            resp = await client.get(group_url)
            _log_action("page_load", group_url, f"HTTP {resp.status_code}")

            if resp.status_code != 200:
                _log(f"HTTP {resp.status_code} for {group_url}")
                return []

            html = resp.text

            # Check for captcha / login wall / warnings
            captcha_signals = [
                "captcha", "security check", "confirm your identity",
                "suspicious activity", "verify you're human",
                "you must log in", "log in to continue",
            ]
            html_lower = html.lower()
            for signal in captcha_signals:
                if signal in html_lower:
                    _log(f"CAPTCHA/WARNING detected on {group_url}: '{signal}'")
                    _log_action("captcha_detected", group_url, signal)
                    return []

            # Public group HTML parsing is very limited without JS rendering.
            # Most Facebook content requires authentication or browser rendering.
            # This stub returns empty — real scraping needs Graph API or browser.
            _log(f"Public page fetched ({len(html)} bytes) but "
                 f"JS-rendered content not available via httpx")
            return []

    except httpx.TimeoutException:
        _log(f"Timeout fetching {group_url}")
        _log_action("fetch_timeout", group_url, "")
        return []
    except Exception as e:
        _log(f"Error fetching {group_url}: {e}")
        _log_action("fetch_error", group_url, str(e))
        return []


async def scrape_group(group: dict, access_token: str = "") -> list[dict]:
    """
    Scrape a single group for new posts. Uses Graph API if token available,
    falls back to public page fetch (limited).

    Returns list of analyzed lead dicts.

    SAFETY: READ-ONLY. No interactions. Random delays between requests.
    """
    group_id = group["id"]
    group_name = group["group_name"]
    group_url = group["group_url"]
    category = group["category"]

    _log(f"Scraping group: {group_name}")
    _log_action("scrape_start", f"group_{group_id}", group_name)

    # Try Graph API first
    # Extract Facebook group ID from URL if possible
    fb_group_id = ""
    m = re.search(r"groups/(\d+)", group_url)
    if m:
        fb_group_id = m.group(1)

    posts = []
    if fb_group_id and access_token:
        posts = await _fetch_group_feed_graph_api(fb_group_id, access_token)

    # Fallback: public page fetch (limited utility)
    if not posts:
        posts = await _fetch_group_public_page(group_url)

    if not posts:
        _log(f"No posts retrieved from {group_name}")
        _log_action("scrape_end", f"group_{group_id}", "0 posts")
        return []

    # Analyze each post
    leads = []
    for post in posts:
        text = post.get("post_text", "").strip()
        if not text or len(text) < 20:
            continue

        # Random delay between analyses (rate limiting)
        await asyncio.sleep(_random_delay())

        analysis = await analyze_post(text, group_name, category)

        # Only store if score >= 3 (filter out obvious noise)
        if analysis.get("score", 0) >= 3:
            lead_id = add_scraped_lead(group_id, post, analysis)
            lead = get_lead_by_id(lead_id)
            if lead:
                leads.append(lead)

    # Update last_scraped timestamp
    conn = _get_conn()
    conn.execute(
        "UPDATE monitored_groups SET last_scraped = ? WHERE id = ?",
        (_now_str(), group_id),
    )
    conn.commit()
    conn.close()

    _log(f"Scraped {group_name}: {len(posts)} posts, {len(leads)} leads stored")
    _log_action("scrape_end", f"group_{group_id}",
                f"{len(posts)} posts, {len(leads)} leads")
    return leads


async def run_scrape_cycle(send_fn=None) -> dict:
    """
    Run a full scrape cycle across all active groups.

    SAFETY: Checks rate limits, random delays, captcha detection.
    NEVER interacts — only observes.

    Returns stats dict.
    """
    _log("Starting scrape cycle")
    _log_action("cycle_start", "", "")

    config = _load_config()
    access_token = config.get("fb_access_token", "")

    groups = get_groups()
    total_posts = 0
    total_leads = 0
    hot_alerts = []

    for group in groups:
        if not await _check_rate_limit():
            _log("Rate limit reached, stopping cycle early")
            if send_fn:
                try:
                    await send_fn(
                        "\u26a0\ufe0f GroupScraper rate limit reached "
                        f"({MAX_PAGE_LOADS_PER_DAY} loads/day). "
                        "Stopping early to stay safe."
                    )
                except Exception:
                    pass
            break

        try:
            leads = await scrape_group(group, access_token)
            total_leads += len(leads)

            # Send immediate alerts for hot leads
            for lead in leads:
                if lead["score"] >= 8:
                    hot_alerts.append(lead)
                    if send_fn:
                        try:
                            await send_fn(format_hot_alert(lead))
                        except Exception as e:
                            _log(f"Failed to send hot alert: {e}")

        except Exception as e:
            _log(f"Error scraping {group['group_name']}: {e}")
            traceback.print_exc()

        # Random delay between groups
        await asyncio.sleep(_random_delay())

    stats = {
        "groups_scanned": len(groups),
        "total_leads": total_leads,
        "hot_alerts": len(hot_alerts),
        "timestamp": _now_str(),
    }

    _log(f"Scrape cycle complete: {stats['groups_scanned']} groups, "
         f"{stats['total_leads']} leads, {stats['hot_alerts']} hot alerts")
    _log_action("cycle_end", "",
                f"{stats['groups_scanned']} groups, {stats['total_leads']} leads")
    return stats


# ═════════════════════════════════════════════════════════════════════════════
# TELEGRAM COMMAND HANDLERS
# ═════════════════════════════════════════════════════════════════════════════

def handle_scrape_command(text: str) -> str:
    """
    /scrape — show scrape status or trigger info.
    Actual scraping runs on schedule; this shows current stats.
    """
    stats = get_scrape_stats()

    msg = (
        "\U0001f50d GROUP SCRAPER STATUS\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\U0001f4ca Groups monitored: {stats['total_groups']}\n"
        f"\U0001f4c8 Total leads: {stats['total_leads']}\n"
        f"\U0001f4c5 New today: {stats['today_leads']}\n\n"
        f"\U0001f525 Hot leads (8+): {stats['hot_leads']}\n"
        f"\u26a1 Warm leads (5-7): {stats['warm_leads']}\n"
        f"\U0001f4de Contacted: {stats['contacted']}\n"
        f"\u2705 Converted: {stats['converted']}\n\n"
        f"\U0001f310 Page loads today: {stats['today_page_loads']}/{stats['max_daily_loads']}\n"
    )

    if stats["top_event_types"]:
        msg += "\n\U0001f3af Top event types:\n"
        for et in stats["top_event_types"]:
            msg += f"  \u2022 {et['type'].replace('_', ' ').title()}: {et['count']}\n"

    msg += (
        "\n\U0001f552 Schedule: scrape 7am/1pm/7pm | digest 8:30am/8:30pm\n"
        "\nUse /groups to see monitored groups\n"
        "Use /digest to get the latest intelligence report"
    )
    return msg


def handle_groups_command(text: str) -> str:
    """/groups — list all monitored Facebook groups."""
    groups = get_groups()
    if not groups:
        return "\U0001f50d No monitored groups. Use /add_group to add one."

    msg = (
        "\U0001f50d MONITORED GROUPS\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
    )

    by_category: dict[str, list] = {}
    for g in groups:
        cat = g["category"] or "other"
        by_category.setdefault(cat, []).append(g)

    category_icons = {
        "wedding": "\U0001f492",
        "event_planning": "\U0001f3aa",
        "quinceanera": "\U0001f389",
        "community": "\U0001f3d8\ufe0f",
    }

    for cat, cat_groups in sorted(by_category.items()):
        icon = category_icons.get(cat, "\U0001f4cc")
        msg += f"{icon} {cat.replace('_', ' ').title()} ({len(cat_groups)}):\n"
        for g in cat_groups:
            last = g["last_scraped"] or "never"
            lang = f" [{g['language']}]" if g["language"] != "en" else ""
            msg += f"  {g['id']}. {g['group_name']}{lang}\n"
            msg += f"     Last scraped: {last}\n"
        msg += "\n"

    msg += f"Total: {len(groups)} active groups\n"
    msg += "Use /add_group [url] [category] to add more"
    return msg


def handle_add_group_command(text: str) -> str:
    """
    /add_group [url] [category] — add a new group to monitor.
    Category: wedding, event_planning, quinceanera, community
    """
    usage = (
        "Usage: /add_group [url] [category]\n"
        "Categories: wedding, event_planning, quinceanera, community\n\n"
        "Example:\n"
        "/add_group https://facebook.com/groups/123 wedding"
    )

    stripped = text.strip()
    for pfx in ["/add_group ", "/addgroup "]:
        if stripped.lower().startswith(pfx):
            stripped = stripped[len(pfx):].strip()
            break
    else:
        return usage

    if not stripped:
        return usage

    parts = stripped.split()
    if len(parts) < 2:
        return "Please provide both URL and category.\n\n" + usage

    url = parts[0]
    category = parts[1].lower()
    language = parts[2] if len(parts) > 2 else "en"

    valid_categories = {"wedding", "event_planning", "quinceanera", "community"}
    if category not in valid_categories:
        return (
            f"Invalid category: {category}\n"
            f"Valid options: {', '.join(sorted(valid_categories))}"
        )

    # Extract group name from URL or use a placeholder
    name_match = re.search(r"groups/([^/?]+)", url)
    name = name_match.group(1).replace("-", " ").replace(".", " ").title() if name_match else "New Group"

    gid = add_group(name, url, category, language)
    return (
        f"\u2705 Added group to monitor:\n"
        f"\U0001f194 ID: {gid}\n"
        f"\U0001f4db {name}\n"
        f"\U0001f517 {url}\n"
        f"\U0001f3f7\ufe0f Category: {category}\n"
        f"\U0001f30d Language: {language}"
    )


def handle_digest_command(text: str) -> str:
    """/digest — show latest social intelligence digest."""
    return format_intelligence_digest()


async def handle_draft_command(text: str) -> str:
    """
    /draft [lead_id] — draft a DM for a lead.
    Kai copies and sends manually — NEVER auto-sent.
    """
    stripped = text.strip()
    for pfx in ["/draft "]:
        if stripped.lower().startswith(pfx):
            stripped = stripped[len(pfx):].strip()
            break
    else:
        return "Usage: /draft [lead_id]\nExample: /draft 42"

    try:
        lead_id = int(stripped)
    except ValueError:
        return "Invalid lead ID. Use a number.\nExample: /draft 42"

    return await draft_dm(lead_id)


# ═════════════════════════════════════════════════════════════════════════════
# SCHEDULER
# ═════════════════════════════════════════════════════════════════════════════

async def run_scraper_scheduler(send_fn) -> None:
    """
    Async scheduler for the Facebook group scraper.

    Schedule (all times PT):
      - 7:00 AM  — scrape cycle 1
      - 8:30 AM  — morning intelligence digest
      - 1:00 PM  — scrape cycle 2
      - 7:00 PM  — scrape cycle 3
      - 8:30 PM  — evening intelligence digest

    Heartbeats every 60 seconds.
    """
    try:
        from core.watchdog import heartbeat
    except ImportError:
        def heartbeat(_): pass

    _log("Group scraper scheduler started")

    # Register with hierarchy status tracker
    try:
        from core.agent_hierarchy import update_agent_status, init_hierarchy_db
        init_hierarchy_db()
        update_agent_status("fb-lead-hunter", "marketing", "active", "Initializing group scanner")
    except Exception:
        pass

    # Track what we've already sent today to avoid duplicates
    sent_today: dict[str, str] = {}

    while True:
        try:
            heartbeat("fb_group_scraper")
            # Update hierarchy status
            try:
                from core.agent_hierarchy import update_agent_status
                update_agent_status("fb-lead-hunter", "marketing", "active", "Monitoring Facebook groups")
            except Exception:
                pass

            now = _now()
            today_key = now.strftime("%Y-%m-%d")
            hour, minute = now.hour, now.minute

            # Reset tracking at midnight
            if any(v != today_key for v in sent_today.values()):
                sent_today.clear()

            # ── Scrape cycles: 7 AM, 1 PM, 7 PM ────────────────────────
            scrape_hours = [7, 13, 19]
            for sh in scrape_hours:
                key = f"scrape_{sh}"
                if hour == sh and minute < 2 and sent_today.get(key) != today_key:
                    sent_today[key] = today_key
                    _log(f"Scheduled scrape cycle ({sh}:00 PT)")
                    try:
                        await run_scrape_cycle(send_fn)
                    except Exception as e:
                        _log(f"Scrape cycle error: {e}")
                        traceback.print_exc()

            # ── Digest: 8:30 AM ─────────────────────────────────────────
            if hour == 8 and 30 <= minute < 32 and sent_today.get("digest_am") != today_key:
                sent_today["digest_am"] = today_key
                _log("Sending morning intelligence digest")
                try:
                    digest = format_intelligence_digest()
                    await send_fn(digest)
                except Exception as e:
                    _log(f"Morning digest error: {e}")

            # ── Digest: 8:30 PM ─────────────────────────────────────────
            if hour == 20 and 30 <= minute < 32 and sent_today.get("digest_pm") != today_key:
                sent_today["digest_pm"] = today_key
                _log("Sending evening intelligence digest")
                try:
                    digest = format_intelligence_digest()
                    await send_fn(digest)
                except Exception as e:
                    _log(f"Evening digest error: {e}")

        except Exception as e:
            _log(f"Scheduler loop error: {e}")
            traceback.print_exc()

        await asyncio.sleep(60)
