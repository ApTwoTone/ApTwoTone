#!/usr/bin/env python3
"""
Facebook Group Scraper — Referral Leads + Event Opportunities
=============================================================
Scrapes Facebook vendor/event groups using Playwright to find:

TYPE 1: REFERRAL LEADS (independent contractors)
  Wedding photographers, caterers, florists, DJs, makeup artists,
  bartenders, event planners, tent rental companies, etc.
  These people work at events and can REFER bathroom rentals to clients.

TYPE 2: EVENT OPPORTUNITY LEADS (events needing vendors)
  Vendor markets, fairs, swap meets, festivals, community events.
  Outdoor events with many attendees = bathroom rental opportunity.

Reuses the async Playwright infrastructure from scrapers/fb_vendor_scraper/
for browser stealth, post extraction, profile enrichment, and geo-filtering.

Safety:
  - READ-ONLY: never comment, like, post, DM, or interact (Rule 24)
  - Lockfile prevents Chrome profile contention
  - Rate limited: 8-20s between groups, 1.5-3s between scrolls
  - Headless=False (Facebook detects headless browsers)

Usage:
    python scripts/facebook_group_scraper.py --once      # Single cycle
    python scripts/facebook_group_scraper.py --interval 360  # Daemon mode
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Optional

# ── Path setup: import from scrapers/fb_vendor_scraper/ ─────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scrapers" / "fb_vendor_scraper"))
sys.path.insert(0, str(PROJECT_ROOT))

from browser_utils import (
    launch_browser, is_logged_in, check_for_blocking,
    action_delay, random_delay, scroll_page, click_see_more,
    move_mouse_randomly, safe_goto, setup_page_optimizations,
)
from post_filter import (
    is_relevant_post, classify_post_type, detect_category,
    LOCAL_AREA_KEYWORDS, EXCLUDE_AREA_KEYWORDS,
)
from config import (
    BROWSER_DATA_DIR, GROUPS_FILE,
    GROUP_BREAK_MIN, GROUP_BREAK_MAX,
    MAX_SESSION_MINUTES, MARATHON_SESSION_MINUTES, WARM_RESTART_MINUTES,
)
from profile_scraper import extract_profile_data
from graphql_capture import GraphQLCapture
from core.agent_runtime import AgentRuntime

# ── Constants ────────────────────────────────────────────────────────────────

DB_PATH = Path.home() / ".nexus" / "memory.db"
LOG_DIR = Path.home() / ".nexus" / "logs"
LOCK_FILE = Path.home() / ".nexus" / "fb_scraper.lock"
STATUS_FILE = Path.home() / ".nexus" / "coordination" / "coder2-status.md"
SCRAPE_STATE_FILE = Path.home() / ".nexus" / "fb_scraper_state.json"

# Categories that qualify as referral partners
REFERRAL_CATEGORIES = {
    "photographer", "caterer", "dj", "florist", "event_planner",
    "wedding_planner", "decorator", "lighting", "photo_booth",
    "bounce_house", "tent_rental", "party_rental", "setup_crew",
    "dance_floor", "valet", "venue", "generator_rental",
    "bathroom_rental",
}

# High-value referral roles (direct access to event hosts)
HIGH_VALUE_ROLES = {
    "event_planner", "wedding_planner", "caterer",
    "tent_rental", "party_rental", "venue",
}

# Keywords indicating an event opportunity (vendor markets, fairs, festivals)
EVENT_KEYWORDS = [
    "vendor market", "vendor fair", "vendor event", "pop-up market",
    "pop up market", "craft fair", "night market", "flea market",
    "festival", "street fair", "community fair", "farmers market",
    "farmer's market", "vendor booth", "booth space", "vendor spot",
    "vendors wanted", "vendor application", "vendor registration",
    "calling all vendors", "swap meet", "bazaar", "expo",
    "artisan market", "holiday market", "vendor spots open",
    "reserve your spot", "secure your booth", "vendor invitation",
]

# Outdoor indicators for bathroom relevance scoring
OUTDOOR_KEYWORDS = [
    "outdoor", "park", "beach", "field", "ranch", "farm", "vineyard",
    "garden", "backyard", "patio", "estate", "open air", "tent",
]

BATHROOM_NEED_KEYWORDS = [
    "restroom", "restrooms", "bathroom", "bathrooms", "bathroom trailer",
    "restroom trailer", "portable restroom", "portable restrooms",
    "portable toilet", "portable toilets", "porta potty", "porta potties",
    "toilet trailer", "toilet trailers", "sanitation station",
]

EVENT_REQUEST_KEYWORDS = [
    "looking for vendors", "looking for a vendor", "need vendors", "need a vendor",
    "vendor needed", "vendors wanted", "calling all vendors", "recommend a vendor",
    "recommend vendors", "event help", "event support", "planning an event",
    "hosting an event", "need rentals", "need services",
]

HOST_INTENT_KEYWORDS = [
    "looking for", "need a", "need an", "need help", "recommend", "recommendation",
    "who knows", "anyone know", "searching for", "planning a", "planning an",
    "help me find", "suggestions for", "iso ", "in search of",
    "can anyone recommend", "does anyone know", "have an event", "have a wedding",
    "our event", "my event", "our wedding", "my wedding", "birthday party",
    "baby shower", "bridal shower", "graduation party", "quince", "quinceañera",
]

PRIVATE_PROPERTY_KEYWORDS = [
    "private property", "backyard", "estate", "ranch", "farm", "vineyard",
    "private event", "private venue", "private residence", "house party",
    "open field", "private lot", "park", "garden", "outdoor wedding",
]

ACTIONABLE_EVENT_KEYWORDS = [
    "wedding", "reception", "ceremony", "birthday", "baby shower", "bridal shower",
    "graduation", "quince", "quinceañera", "festival", "fair", "market",
    "block party", "community event", "corporate event", "family reunion",
]

NON_ACTIONABLE_NAME_MARKERS = {
    "anonymous participant",
    "anonymous member",
    "anonymous attendee",
    "participant",
    "member",
    "admin",
    "moderator",
}

POST_URL_HINTS = ("/posts/", "/permalink/", "story_fbid=", "multi_permalinks=", "/events/")
MONTH_NAME_PATTERN = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
HOT_EVENT_ALERT_THRESHOLD = 7


# ── Logging ──────────────────────────────────────────────────────────────────

def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "fb_group_scraper.log"
    handler = TimedRotatingFileHandler(
        str(log_file), when="D", interval=1, backupCount=14,
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger = logging.getLogger("fb_group_scraper")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        logger.addHandler(handler)
        logger.addHandler(console)
    return logger


log = setup_logging()


# ── Database ─────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db():
    """Ensure referral_leads and event_leads tables exist with required columns.

    Tables are created by Coder_1. This function adds any columns we need
    that may not exist yet (e.g. content_hash for event dedup).
    """
    conn = _conn()

    # Create tables if Coder_1 hasn't run yet
    conn.execute("""
        CREATE TABLE IF NOT EXISTS referral_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            first_name TEXT DEFAULT '',
            username TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            email TEXT DEFAULT '',
            website TEXT DEFAULT '',
            website_works INTEGER DEFAULT -1,
            profile_url TEXT DEFAULT '',
            job_title TEXT DEFAULT '',
            business_name TEXT DEFAULT '',
            location TEXT DEFAULT '',
            city TEXT DEFAULT '',
            state TEXT DEFAULT 'CA',
            in_sfv INTEGER DEFAULT 0,
            in_la INTEGER DEFAULT 0,
            in_service_area INTEGER DEFAULT 0,
            facebook_group TEXT DEFAULT '',
            facebook_group_url TEXT DEFAULT '',
            post_text TEXT DEFAULT '',
            post_date TEXT DEFAULT '',
            post_url TEXT DEFAULT '',
            is_actively_promoting INTEGER DEFAULT 0,
            last_post_date TEXT DEFAULT '',
            has_dm_access INTEGER DEFAULT -1,
            qualification_score INTEGER DEFAULT 0,
            qualification_reason TEXT DEFAULT '',
            outreach_status TEXT DEFAULT 'new',
            outreach_channel TEXT DEFAULT '',
            referral_fee_offered REAL DEFAULT 0,
            bookings_referred INTEGER DEFAULT 0,
            revenue_generated REAL DEFAULT 0,
            notes TEXT DEFAULT '',
            source TEXT DEFAULT 'fb_group_scraper',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS event_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_name TEXT NOT NULL,
            organizer_name TEXT DEFAULT '',
            organizer_phone TEXT DEFAULT '',
            organizer_email TEXT DEFAULT '',
            organizer_website TEXT DEFAULT '',
            event_date TEXT DEFAULT '',
            event_time TEXT DEFAULT '',
            event_location TEXT DEFAULT '',
            event_city TEXT DEFAULT '',
            event_address TEXT DEFAULT '',
            event_type TEXT DEFAULT '',
            expected_attendance INTEGER DEFAULT 0,
            vendor_spots_available INTEGER DEFAULT 0,
            vendor_fee REAL DEFAULT 0,
            is_outdoor INTEGER DEFAULT 1,
            restroom_need_score INTEGER DEFAULT 0,
            restroom_need_reason TEXT DEFAULT '',
            facebook_group TEXT DEFAULT '',
            facebook_group_url TEXT DEFAULT '',
            post_text TEXT DEFAULT '',
            post_url TEXT DEFAULT '',
            post_date TEXT DEFAULT '',
            poster_name TEXT DEFAULT '',
            poster_profile_url TEXT DEFAULT '',
            event_page_url TEXT DEFAULT '',
            signup_url TEXT DEFAULT '',
            outreach_status TEXT DEFAULT 'new',
            notes TEXT DEFAULT '',
            source TEXT DEFAULT 'fb_group_scraper',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Add content_hash column for event dedup if missing
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(event_leads)").fetchall()]
        if "content_hash" not in cols:
            conn.execute("ALTER TABLE event_leads ADD COLUMN content_hash TEXT DEFAULT ''")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_el_content_hash ON event_leads(content_hash)")
    except Exception as e:
        log.warning(f"Could not add content_hash column: {e}")

    # Repeat organizer tracking — same person posting multiple events = high-value target
    conn.execute("""
        CREATE TABLE IF NOT EXISTS repeat_organizers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organizer_name TEXT NOT NULL,
            profile_url TEXT DEFAULT '',
            event_count INTEGER DEFAULT 1,
            first_seen TEXT DEFAULT (datetime('now')),
            last_seen TEXT DEFAULT (datetime('now')),
            escalated INTEGER DEFAULT 0
        )
    """)

    # Ensure indexes exist
    for sql in [
        "CREATE INDEX IF NOT EXISTS idx_rl_profile ON referral_leads(profile_url)",
        "CREATE INDEX IF NOT EXISTS idx_el_content_hash ON event_leads(content_hash)",
        "CREATE INDEX IF NOT EXISTS idx_ro_profile ON repeat_organizers(profile_url)",
        "CREATE INDEX IF NOT EXISTS idx_ro_name ON repeat_organizers(organizer_name)",
    ]:
        try:
            conn.execute(sql)
        except Exception:
            pass

    conn.commit()
    conn.close()
    repairs = repair_existing_facebook_leads()
    if repairs["referral_updates"] or repairs["event_updates"]:
        log.info(
            "Repaired historical Facebook leads: %s referral rows, %s event rows",
            repairs["referral_updates"],
            repairs["event_updates"],
        )
    log.info("Database tables verified")


# ── Classification ───────────────────────────────────────────────────────────

def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def clean_poster_name(raw_name: str) -> str:
    """Remove common Facebook UI/time chrome from scraped poster labels."""
    name = _normalize_text(raw_name).strip(" -:;,.|•·")
    if not name:
        return ""

    name = re.sub(r"\b(?:top fan|top contributor|admin|moderator)\b", "", name, flags=re.IGNORECASE)
    name = re.sub(
        r"(?:\s+|^)(?:just now|yesterday|today|tomorrow|"
        r"(?:an?|the)\s+(?:minute|hour|day|week|month|year)\s+ago|"
        r"\d+\s*(?:min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|wk|wks|week|weeks|mo|mos|month|months|yr|yrs|year|years)\s*(?:ago)?"
        r")$",
        "",
        name,
        flags=re.IGNORECASE,
    )
    name = _normalize_text(name).strip(" -:;,.|•·")
    lowered = name.lower()
    if lowered in NON_ACTIONABLE_NAME_MARKERS:
        return ""
    if any(token in lowered for token in (" posted", " shared", " replied", " commented")):
        return ""
    return name[:80]


def is_event_host_request(text: str) -> bool:
    """Detect posts where an event host is actively asking for help/services."""
    if not text:
        return False
    lower = text.lower()
    host_intent = classify_post_type(text) == "event_announcement" or any(
        kw in lower for kw in HOST_INTENT_KEYWORDS + EVENT_REQUEST_KEYWORDS
    )
    if not host_intent:
        return False
    event_context = any(kw in lower for kw in ACTIONABLE_EVENT_KEYWORDS + PRIVATE_PROPERTY_KEYWORDS + OUTDOOR_KEYWORDS)
    metadata_signal = bool(
        extract_event_date(text)
        or extract_event_time(text)
        or extract_event_location(text)
        or extract_expected_attendance(text)
    )
    explicit_restroom_need = any(kw in lower for kw in BATHROOM_NEED_KEYWORDS)
    return explicit_restroom_need or event_context or metadata_signal


def is_actionable_referral_candidate(post: dict) -> bool:
    """Require some way to identify or contact a referral partner."""
    cleaned_name = clean_poster_name(post.get("poster_name", ""))
    has_contact_path = any(
        _normalize_text(post.get(key, "")) for key in ("profile_url", "phone", "email", "website", "instagram")
    )
    return bool(cleaned_name or has_contact_path)

def classify_lead_type(text: str, category: str) -> str:
    """Classify a post as REFERRAL_LEAD, EVENT_OPPORTUNITY, or SKIP."""
    lower = text.lower() if text else ""

    if any(kw in lower for kw in BATHROOM_NEED_KEYWORDS):
        return "EVENT_OPPORTUNITY"

    if is_event_host_request(text):
        return "EVENT_OPPORTUNITY"

    # Check event keywords first (more specific signal)
    for kw in EVENT_KEYWORDS:
        if kw in lower:
            return "EVENT_OPPORTUNITY"

    # Check if the poster's detected category is a referral-worthy role
    if category in REFERRAL_CATEGORIES and classify_post_type(text) != "event_announcement":
        return "REFERRAL_LEAD"

    return "SKIP"


# ── Scoring ──────────────────────────────────────────────────────────────────

def score_referral_lead(post_data: dict, profile_data: dict) -> int:
    """Score a referral lead 0-10 on value as a referral partner."""
    score = 0

    # Contact info availability
    phone = post_data.get("phone") or profile_data.get("phone", "")
    email = post_data.get("email") or profile_data.get("email", "")
    website = post_data.get("website") or profile_data.get("website", "")
    if phone:
        score += 2
    if email:
        score += 2
    if website:
        score += 1

    # Location — check post text and profile city
    combined_text = f"{post_data.get('post_content', '')} {profile_data.get('city', '')}".lower()
    sfv_keywords = {
        "san fernando", "sfv", "van nuys", "encino", "woodland hills",
        "burbank", "glendale", "northridge", "north hollywood",
        "sherman oaks", "calabasas", "tarzana", "chatsworth",
        "canoga park", "panorama city", "sylmar", "pacoima",
    }
    la_keywords = {"los angeles", "la ", "socal", "southern california", "pasadena", "santa monica"}

    if any(kw in combined_text for kw in sfv_keywords):
        score += 3
    elif any(kw in combined_text for kw in la_keywords):
        score += 2

    # High-value role
    category = post_data.get("_category", "")
    if category in HIGH_VALUE_ROLES:
        score += 1

    return min(10, score)


def score_bathroom_relevance(text: str) -> int:
    """Score 0-10 how likely an event needs restroom facilities.

    Scoring signals (additive, capped at 10):
    - Explicit bathroom/restroom mention: +4
    - High-value event type (wedding/quince) + outdoor + local: +3
    - High-value event type + actively seeking + local: +2
    - Outdoor / private property: +2 each
    - Large gathering signals: +2
    - Event request language: +2
    - Duration indicators: +2
    - In service area: +2
    - Event host request: +2
    - Has date/time: +1 each
    """
    score = 0
    lower = text.lower() if text else ""

    if is_event_host_request(text):
        score += 2

    # Outdoor indicators
    is_outdoor = any(kw in lower for kw in OUTDOOR_KEYWORDS)
    is_private_property = any(kw in lower for kw in PRIVATE_PROPERTY_KEYWORDS)
    if is_outdoor:
        score += 2
    if is_private_property:
        score += 2

    # Vendor/festival/market signals (large gatherings)
    if any(kw in lower for kw in ["vendor", "fair", "festival", "market", "community event"]):
        score += 2

    # Strong buying-intent language for event help.
    is_seeking = any(kw in lower for kw in EVENT_REQUEST_KEYWORDS)
    if is_seeking:
        score += 2

    # Duration indicators
    if any(kw in lower for kw in ["all day", "full day", "6 hours", "8 hours", "10am", "9am"]):
        score += 2

    # In service area
    is_local = any(kw in lower for kw in LOCAL_AREA_KEYWORDS)
    if is_local:
        score += 2

    # Explicit restroom/bathroom mention
    if any(kw in lower for kw in BATHROOM_NEED_KEYWORDS):
        score += 4

    # Crowd size / attendance hints.
    if re.search(r"\b(?:\d{2,4}\+?\s*(?:guests?|attendees?|people|vendors?|booths?))\b", lower):
        score += 2

    if extract_event_date(text):
        score += 1
    if extract_event_time(text):
        score += 1

    # Compound signals: wedding/quince + outdoor + local area = almost certainly
    # needs a restroom trailer even if they didn't say "bathroom"
    is_high_value_event = any(kw in lower for kw in [
        "wedding", "quinceañera", "quince", "reception", "ceremony",
    ])
    if is_high_value_event and (is_outdoor or is_private_property) and is_local:
        score += 3
    if is_high_value_event and is_seeking and is_local:
        score += 2

    return min(10, score)


def parse_reference_datetime(value: str | datetime | None) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raw = str(value or "").strip()
    if not raw:
        return None
    candidates = [raw, raw.replace("Z", "+00:00")]
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def extract_event_date(text: str, reference_dt: datetime | None = None) -> str:
    """Extract a best-effort event date and normalize it to YYYY-MM-DD when possible."""
    if not text:
        return ""

    now = reference_dt or datetime.now(timezone.utc)
    normalized = re.sub(r"\s+", " ", text).strip()

    month_match = re.search(
        rf"\b(?:on\s+)?(?P<month>{MONTH_NAME_PATTERN})\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s*(?P<year>\d{{4}}))?",
        normalized,
        re.IGNORECASE,
    )
    if month_match:
        month_text = month_match.group("month")[:3].title()
        month = datetime.strptime(month_text, "%b").month
        day = int(month_match.group("day"))
        year = int(month_match.group("year") or now.year)
        candidate = datetime(year, month, day, tzinfo=timezone.utc)
        if (
            not month_match.group("year")
            and candidate.date() < now.date()
            and (now.date() - candidate.date()).days > 14
        ):
            candidate = datetime(year + 1, month, day, tzinfo=timezone.utc)
        return candidate.date().isoformat()

    numeric_match = re.search(r"\b(?P<month>\d{1,2})/(?P<day>\d{1,2})(?:/(?P<year>\d{2,4}))?\b", normalized)
    if numeric_match:
        month = int(numeric_match.group("month"))
        day = int(numeric_match.group("day"))
        raw_year = numeric_match.group("year")
        year = now.year
        if raw_year:
            year = int(raw_year)
            if year < 100:
                year += 2000
        candidate = datetime(year, month, day, tzinfo=timezone.utc)
        if not raw_year and candidate.date() < now.date() and (now.date() - candidate.date()).days > 14:
            candidate = datetime(year + 1, month, day, tzinfo=timezone.utc)
        return candidate.date().isoformat()

    relative_map = {
        "today": 0,
        "tomorrow": 1,
    }
    for token, delta_days in relative_map.items():
        if re.search(rf"\b{token}\b", normalized, re.IGNORECASE):
            return (now + timedelta(days=delta_days)).date().isoformat()

    weekday_match = re.search(r"\b(this|next)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", normalized, re.IGNORECASE)
    if weekday_match:
        mode = weekday_match.group(1).lower()
        weekday = WEEKDAY_INDEX[weekday_match.group(2).lower()]
        days_ahead = (weekday - now.weekday()) % 7
        if mode == "next":
            days_ahead = days_ahead + 7 if days_ahead == 0 else days_ahead + 7
        elif days_ahead == 0:
            days_ahead = 7
        return (now + timedelta(days=days_ahead)).date().isoformat()

    return ""


def extract_event_time(text: str) -> str:
    """Extract a best-effort event time window string."""
    if not text:
        return ""
    match = re.search(
        r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm))(?:\s*(?:-|–|—|to|until)\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)))?",
        text,
        re.IGNORECASE,
    )
    if not match:
        return ""
    start = re.sub(r"\s+", " ", match.group(1)).strip()
    end = re.sub(r"\s+", " ", match.group(2) or "").strip()
    return f"{start} - {end}" if end else start


def extract_event_location(text: str) -> str:
    """Extract a best-effort location phrase."""
    if not text:
        return ""
    patterns = [
        r"\b(?:located at|location|venue)\s*(?::|-)?\s*[\r\n]*\s*([^.\n]{6,120})",
        r"\bat\s+([A-Z][^.\n]{6,120})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            location = match.group(1).strip(" -:;,")
            location = re.split(r"(?:📅|⏰|\bdate\b|\btime\b)", location, maxsplit=1, flags=re.IGNORECASE)[0]
            location = location.split("!")[0]
            location = re.sub(r"^[^\w(]+", "", location)
            location = re.sub(r"\s+", " ", location).strip(" -:;,")
            if len(location) >= 6:
                return location[:120]
    return ""


def extract_expected_attendance(text: str) -> int:
    """Extract a best-effort expected attendance count."""
    if not text:
        return 0
    match = re.search(r"\b(\d{2,4})\+?\s*(?:guests?|attendees?|people)\b", text, re.IGNORECASE)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except Exception:
        return 0


def should_alert_event_lead(post: dict, score: int) -> bool:
    """Return True when a Facebook finding is hot enough to alert immediately."""
    text = str(post.get("post_content") or "")
    lower = text.lower()
    group_name = str(post.get("group_name") or "").lower()
    in_service_area = bool(
        extract_city_from_text(text)
        or any(kw in lower for kw in LOCAL_AREA_KEYWORDS)
        or any(kw in group_name for kw in LOCAL_AREA_KEYWORDS if len(kw) > 3)
    )
    explicit_restroom_need = any(kw in lower for kw in BATHROOM_NEED_KEYWORDS)
    vendor_request = any(kw in lower for kw in EVENT_REQUEST_KEYWORDS)
    host_request = is_event_host_request(text)
    return (
        explicit_restroom_need
        or (in_service_area and score >= HOT_EVENT_ALERT_THRESHOLD)
        or (in_service_area and vendor_request and score >= HOT_EVENT_ALERT_THRESHOLD - 1)
        or (in_service_area and host_request and score >= HOT_EVENT_ALERT_THRESHOLD - 2)
    )


def extract_event_name(text: str) -> str:
    """Try to extract an event name from post text."""
    if not text:
        return ""
    # Look for common patterns like "Join us at the [Event Name]"
    patterns = [
        r"(?:join\s+us\s+at\s+(?:the\s+)?|welcome\s+to\s+(?:the\s+)?|"
        r"presenting\s+(?:the\s+)?|announcing\s+(?:the\s+)?)"
        r"([A-Z][A-Za-z0-9\s&'-]{5,50})",
        r"^([A-Z][A-Z\s&'-]{5,50}(?:FAIR|MARKET|FESTIVAL|EXPO|BAZAAR))",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if m:
            return m.group(1).strip()
    return ""


def extract_event_type(text: str) -> str:
    """Classify the event type from text."""
    lower = text.lower() if text else ""
    type_map = [
        ("vendor_market", ["vendor market", "vendor fair", "vendor event"]),
        ("festival", ["festival", "music festival", "food festival"]),
        ("fair", ["craft fair", "street fair", "community fair", "art fair"]),
        ("farmers_market", ["farmers market", "farmer's market"]),
        ("swap_meet", ["swap meet", "flea market"]),
        ("expo", ["expo", "trade show"]),
        ("night_market", ["night market", "pop-up market", "pop up market"]),
    ]
    for etype, keywords in type_map:
        for kw in keywords:
            if kw in lower:
                return etype
    return "other"


def extract_city_from_text(text: str) -> str:
    """Extract city name from text using known service area cities."""
    lower = text.lower() if text else ""
    for kw in LOCAL_AREA_KEYWORDS:
        if kw in lower and len(kw) > 3:  # Skip short matches like "la"
            return kw.title()
    return ""


# ── Deduplication ────────────────────────────────────────────────────────────

def is_duplicate_referral(profile_url: str, name: str = "", email: str = "") -> bool:
    """Check if referral lead already exists in referral_leads, fb_vendor_prospects, or vendors."""
    if not profile_url and not email and not name:
        return False

    conn = _conn()
    try:
        if profile_url:
            row = conn.execute(
                "SELECT 1 FROM referral_leads WHERE profile_url = ?", (profile_url,)
            ).fetchone()
            if row:
                return True

            row = conn.execute(
                "SELECT 1 FROM fb_vendor_prospects WHERE profile_url = ?", (profile_url,)
            ).fetchone()
            if row:
                return True

        if email:
            row = conn.execute(
                "SELECT 1 FROM vendors WHERE LOWER(email) = LOWER(?)", (email,)
            ).fetchone()
            if row:
                return True

            row = conn.execute(
                "SELECT 1 FROM referral_leads WHERE LOWER(email) = LOWER(?)", (email,)
            ).fetchone()
            if row:
                return True
    finally:
        conn.close()

    return False


def is_duplicate_event(content_hash: str) -> bool:
    """Check if event lead already exists by content hash."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM event_leads WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


# ── Database Writes ──────────────────────────────────────────────────────────

def _check_service_area(text: str, city: str = "") -> tuple:
    """Check if text/city mentions our service area. Returns (in_sfv, in_la, in_area)."""
    combined = f"{text} {city}".lower()
    sfv_kw = {
        "san fernando", "sfv", "van nuys", "encino", "woodland hills",
        "burbank", "glendale", "northridge", "north hollywood",
        "sherman oaks", "calabasas", "tarzana", "chatsworth",
        "canoga park", "panorama city", "sylmar", "pacoima",
    }
    la_kw = {"los angeles", "socal", "southern california", "pasadena", "santa monica"}

    in_sfv = 1 if any(kw in combined for kw in sfv_kw) else 0
    in_la = 1 if any(kw in combined for kw in la_kw) else 0
    in_area = 1 if (in_sfv or in_la or any(kw in combined for kw in LOCAL_AREA_KEYWORDS)) else 0
    return in_sfv, in_la, in_area


def save_referral(post: dict, profile: dict, category: str, score: int) -> int:
    """Insert a referral lead matching Coder_1's schema. Returns row ID or 0."""
    poster_name = clean_poster_name(post.get("poster_name", ""))
    text = post.get("post_content", "") or ""
    city = profile.get("city", "")
    in_sfv, in_la, in_area = _check_service_area(text, city)

    # Extract first name
    first_name = poster_name.split()[0] if poster_name else ""

    # Build qualification reason
    reasons = []
    phone = post.get("phone", "") or profile.get("phone", "")
    email = post.get("email", "") or profile.get("email", "")
    website = post.get("website", "") or profile.get("website", "")
    if phone:
        reasons.append("has phone")
    if email:
        reasons.append("has email")
    if website:
        reasons.append("has website")
    if in_sfv:
        reasons.append("in SFV")
    elif in_la:
        reasons.append("in LA")
    if category in HIGH_VALUE_ROLES:
        reasons.append(f"high-value: {category}")

    conn = _conn()
    try:
        conn.execute("""
            INSERT INTO referral_leads
                (name, first_name, phone, email, website,
                 profile_url, job_title, business_name,
                 city, state, in_sfv, in_la, in_service_area,
                 facebook_group, facebook_group_url,
                 post_text, post_date, post_url, last_post_date,
                 is_actively_promoting, qualification_score, qualification_reason,
                 source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'CA', ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'fb_group_scraper')
        """, (
            poster_name,
            first_name,
            phone,
            email,
            website,
            post.get("profile_url", ""),
            category,
            profile.get("business_name", ""),
            city,
            in_sfv, in_la, in_area,
            post.get("group_name", ""),
            post.get("group_url", ""),
            text[:2000],
            post.get("post_date", ""),
            post.get("post_url", "") or post.get("group_url", ""),
            post.get("post_date", ""),
            score,
            "; ".join(reasons),
        ))
        conn.commit()
        row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return row_id
    except Exception as e:
        log.error(f"Failed to save referral lead: {e}")
        return 0
    finally:
        conn.close()


def _explain_restroom_need(text: str) -> str:
    """Explain why this event might need restrooms."""
    reasons = []
    lower = text.lower() if text else ""
    if is_event_host_request(text):
        reasons.append("event host seeking services")
    if any(kw in lower for kw in OUTDOOR_KEYWORDS):
        reasons.append("outdoor event")
    if any(kw in lower for kw in PRIVATE_PROPERTY_KEYWORDS):
        reasons.append("private-property venue")
    if any(kw in lower for kw in ["vendor", "fair", "festival", "market"]):
        reasons.append("large gathering")
    if any(kw in lower for kw in ["all day", "full day"]):
        reasons.append("long duration")
    if any(kw in lower for kw in LOCAL_AREA_KEYWORDS):
        reasons.append("in service area")
    if any(kw in lower for kw in BATHROOM_NEED_KEYWORDS):
        reasons.append("explicit restroom mention")
    if any(kw in lower for kw in EVENT_REQUEST_KEYWORDS):
        reasons.append("active vendor request")
    if re.search(r"\b(\d{2,4})\+?\s*(?:guests?|attendees?|people)\b", lower):
        reasons.append("attendance noted")
    if extract_event_date(text):
        reasons.append("event date provided")
    return "; ".join(reasons) if reasons else "vendor event"


def save_event(post: dict, content_hash: str) -> int:
    """Insert an event lead matching Coder_1's schema. Returns row ID or 0."""
    text = post.get("post_content", "") or ""
    reference_dt = parse_reference_datetime(post.get("post_date"))
    relevance = score_bathroom_relevance(text)
    is_outdoor = 1 if any(kw in text.lower() for kw in OUTDOOR_KEYWORDS) else 0
    event_name = extract_event_name(text) or "Unnamed Event"
    event_date = post.get("event_date") or extract_event_date(text, reference_dt=reference_dt)
    event_time = post.get("event_time") or extract_event_time(text)
    event_location = post.get("event_location") or extract_event_location(text)
    event_city = post.get("event_city") or extract_city_from_text(text)
    expected_attendance = int(post.get("expected_attendance") or extract_expected_attendance(text) or 0)
    organizer_website = post.get("website", "")
    post_url = post.get("post_url", "") or post.get("group_url", "")
    event_page_url = post.get("event_page_url", "")
    signup_url = post.get("signup_url", "") or organizer_website

    conn = _conn()
    try:
        conn.execute("""
            INSERT INTO event_leads
                (event_name, event_date, event_time, event_location, event_city,
                 event_type, expected_attendance,
                 is_outdoor, restroom_need_score, restroom_need_reason,
                 facebook_group, facebook_group_url,
                 post_text, post_url, post_date,
                 poster_name, poster_profile_url,
                 organizer_phone, organizer_email, organizer_website,
                 event_page_url, signup_url,
                 content_hash, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'fb_group_scraper')
        """, (
            event_name,
            event_date,
            event_time,
            event_location,
            event_city,
            extract_event_type(text),
            expected_attendance,
            is_outdoor,
            relevance,
            _explain_restroom_need(text),
            post.get("group_name", ""),
            post.get("group_url", ""),
            text[:2000],
            post_url,
            post.get("post_date", ""),
            clean_poster_name(post.get("poster_name", "")),
            post.get("profile_url", ""),
            post.get("phone", ""),
            post.get("email", ""),
            organizer_website,
            event_page_url,
            signup_url,
            content_hash,
        ))
        conn.commit()
        row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return row_id
    except Exception as e:
        log.error(f"Failed to save event lead: {e}")
        return 0
    finally:
        conn.close()


def track_repeat_organizer(name: str, profile_url: str) -> Optional[dict]:
    """Track organizer appearances. Returns dict with event_count if repeat (2+), else None."""
    if not name and not profile_url:
        return None
    conn = _conn()
    try:
        # Match by profile URL first (most reliable), then by name
        row = None
        if profile_url:
            row = conn.execute(
                "SELECT id, event_count, escalated FROM repeat_organizers WHERE profile_url = ?",
                (profile_url,),
            ).fetchone()
        if not row and name:
            row = conn.execute(
                "SELECT id, event_count, escalated FROM repeat_organizers WHERE organizer_name = ?",
                (name,),
            ).fetchone()

        if row:
            new_count = row["event_count"] + 1
            conn.execute(
                "UPDATE repeat_organizers SET event_count = ?, last_seen = datetime('now') WHERE id = ?",
                (new_count, row["id"]),
            )
            conn.commit()
            return {"event_count": new_count, "escalated": row["escalated"], "id": row["id"]}
        else:
            conn.execute(
                "INSERT INTO repeat_organizers (organizer_name, profile_url) VALUES (?, ?)",
                (name, profile_url),
            )
            conn.commit()
            return None
    except Exception as e:
        log.warning(f"Could not track repeat organizer: {e}")
        return None
    finally:
        conn.close()


def mark_organizer_escalated(organizer_id: int):
    """Mark a repeat organizer as escalated so we don't re-alert."""
    conn = _conn()
    try:
        conn.execute("UPDATE repeat_organizers SET escalated = 1 WHERE id = ?", (organizer_id,))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def repair_existing_facebook_leads() -> dict:
    """Backfill missing metadata on older Facebook leads written before parser improvements."""
    conn = _conn()
    referral_updates = 0
    event_updates = 0
    try:
        referral_rows = conn.execute(
            """
            SELECT id, name, post_text, post_url, facebook_group_url
            FROM referral_leads
            """
        ).fetchall()
        for row in referral_rows:
            updates = {}
            cleaned_name = clean_poster_name(row["name"] or "")
            if cleaned_name != (row["name"] or ""):
                updates["name"] = cleaned_name
            if not _normalize_text(row["post_url"]) and _normalize_text(row["facebook_group_url"]):
                updates["post_url"] = row["facebook_group_url"]
            if updates:
                sets = ", ".join(f"{col} = ?" for col in updates)
                values = list(updates.values()) + [row["id"]]
                conn.execute(f"UPDATE referral_leads SET {sets}, updated_at = datetime('now') WHERE id = ?", values)
                referral_updates += 1

        event_rows = conn.execute(
            """
            SELECT id, event_name, event_date, event_time, event_location, event_city,
                   expected_attendance, restroom_need_score, restroom_need_reason,
                   post_text, post_date, post_url, facebook_group_url
            FROM event_leads
            """
        ).fetchall()
        for row in event_rows:
            text = row["post_text"] or ""
            reference_dt = parse_reference_datetime(row["post_date"])
            updates = {}
            extracted_name = extract_event_name(text)
            if (row["event_name"] or "") in {"", "Unnamed Event"} and extracted_name:
                updates["event_name"] = extracted_name
            candidate = extract_event_date(text, reference_dt=reference_dt)
            if candidate and candidate != _normalize_text(row["event_date"]):
                updates["event_date"] = candidate
            if not _normalize_text(row["event_time"]):
                candidate = extract_event_time(text)
                if candidate:
                    updates["event_time"] = candidate
            if not _normalize_text(row["event_location"]):
                candidate = extract_event_location(text)
                if candidate:
                    updates["event_location"] = candidate
            else:
                candidate = extract_event_location(text)
                if candidate and candidate != _normalize_text(row["event_location"]):
                    if any(token in (row["event_location"] or "") for token in ("📅", "⏰", "!")) or len(row["event_location"] or "") > 80:
                        updates["event_location"] = candidate
            if not _normalize_text(row["event_city"]):
                candidate = extract_city_from_text(text)
                if candidate:
                    updates["event_city"] = candidate
            if not int(row["expected_attendance"] or 0):
                candidate = extract_expected_attendance(text)
                if candidate:
                    updates["expected_attendance"] = candidate
            if not int(row["restroom_need_score"] or 0):
                updates["restroom_need_score"] = score_bathroom_relevance(text)
            if not _normalize_text(row["restroom_need_reason"]):
                updates["restroom_need_reason"] = _explain_restroom_need(text)
            if not _normalize_text(row["post_url"]) and _normalize_text(row["facebook_group_url"]):
                updates["post_url"] = row["facebook_group_url"]
            if updates:
                sets = ", ".join(f"{col} = ?" for col in updates)
                values = list(updates.values()) + [row["id"]]
                conn.execute(f"UPDATE event_leads SET {sets}, updated_at = datetime('now') WHERE id = ?", values)
                event_updates += 1

        conn.commit()
    finally:
        conn.close()

    return {"referral_updates": referral_updates, "event_updates": event_updates}


# ── Post Extraction ──────────────────────────────────────────────────────────

def _extract_contacts_from_text(text: str) -> dict:
    """Extract phone, email, website, instagram from post text."""
    contacts = {}

    # Phone
    phone_match = re.search(
        r'(?:(?:\+1|1)?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})', text
    )
    if phone_match:
        contacts["phone"] = phone_match.group(0).strip()

    # Email
    email_match = re.search(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', text)
    if email_match:
        email = email_match.group(0)
        if not any(x in email.lower() for x in [
            "@facebook", "@fb.", "@meta.", "example.com",
        ]):
            contacts["email"] = email

    # Website
    url_match = re.search(
        r'(?:https?://|www\.)[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}[^\s,)]*', text
    )
    if url_match:
        url = url_match.group(0)
        skip_domains = [
            "facebook.com", "fb.com", "instagram.com", "tiktok.com",
            "twitter.com", "x.com", "youtube.com", "l.facebook.com",
        ]
        if not any(d in url.lower() for d in skip_domains):
            contacts["website"] = url

    # Instagram
    ig_patterns = [
        r'(?:ig|insta|instagram)[:\s]*@?([A-Za-z0-9_.]{3,30})',
        r'instagram\.com/([A-Za-z0-9_.]{3,30})',
    ]
    for pattern in ig_patterns:
        ig_match = re.search(pattern, text, re.IGNORECASE)
        if ig_match:
            contacts["instagram"] = ig_match.group(1)
            break

    return contacts


def _parse_relative_time(text: str) -> str:
    """Convert '3h', '2d', 'just now' to ISO format."""
    now = datetime.now(timezone.utc)
    lower = text.lower().strip()
    if "just now" in lower or "now" in lower:
        return now.isoformat()
    match = re.search(r'(\d+)\s*m', lower)
    if match:
        return (now - timedelta(minutes=int(match.group(1)))).isoformat()
    match = re.search(r'(\d+)\s*h', lower)
    if match:
        return (now - timedelta(hours=int(match.group(1)))).isoformat()
    match = re.search(r'(\d+)\s*d', lower)
    if match:
        return (now - timedelta(days=int(match.group(1)))).isoformat()
    if "yesterday" in lower:
        return (now - timedelta(days=1)).isoformat()
    return now.isoformat()


def _normalize_fb_href(href: str) -> str:
    raw = str(href or "").strip()
    if not raw or raw == "#":
        return ""
    if raw.startswith("/"):
        raw = "https://www.facebook.com" + raw
    return raw.split("&__")[0].strip()


async def _extract_single_post(el, group_name: str, group_url: str) -> dict:
    """Extract data from a single [role='article'] element."""
    try:
        text = await el.inner_text()
    except Exception:
        return None

    if not text or len(text) < 20:
        return None

    poster_name = ""
    profile_url = ""
    skip_paths = (
        "/reel/", "/watch/", "/video/", "/photo/",
        "/stories/", "/events/", "/hashtag/", "/share/", "/permalink/",
    )
    skip_labels = {
        "like", "comment", "share", "see more", "reply",
        "view more comments", "send", "follow", "join",
        "add friend", "message", "close", "menu",
    }

    # Strategy A: aria-label
    try:
        aria = await el.get_attribute("aria-label") or ""
        if aria:
            m = re.match(r"(?:Post by|Comment by|Story by)\s+(.+)", aria, re.IGNORECASE)
            if m:
                poster_name = m.group(1).strip()
    except Exception:
        pass

    # Strategy B: link-based extraction
    if not poster_name:
        try:
            selectors = [
                'h2 a[href]', 'h3 a[href]', 'strong a[href]',
                'a[role="link"]', 'a[href*="/profile"]',
                'a[href*="/groups/"][href*="/user/"]', 'a[href]',
            ]
            for selector in selectors:
                if poster_name:
                    break
                try:
                    name_links = await el.query_selector_all(selector)
                except Exception:
                    continue
                for link in name_links[:8]:
                    try:
                        href = await link.get_attribute("href") or ""
                        if not href:
                            continue
                        if href.startswith("/") and "facebook.com" not in href:
                            href = "https://www.facebook.com" + href
                        if "facebook.com" not in href:
                            continue
                        if any(sp in href for sp in skip_paths):
                            continue
                        if "/groups/" in href and "/user/" not in href and "/posts/" not in href:
                            continue
                        if href in ("#",) or href.endswith("facebook.com/") or href.endswith("facebook.com"):
                            continue

                        link_text = (await link.inner_text()).strip()
                        if not link_text or len(link_text) < 2:
                            try:
                                inner = await link.query_selector("strong, span")
                                if inner:
                                    link_text = (await inner.inner_text()).strip()
                            except Exception:
                                pass
                        if not link_text or len(link_text) < 2:
                            continue
                        if link_text.lower() in skip_labels:
                            continue
                        if re.match(r'^(\d+[hmdw]|just now|yesterday|\d+ min)$', link_text.lower()):
                            continue

                        poster_name = link_text
                        profile_url = href.split("?")[0]
                        break
                    except Exception:
                        continue
        except Exception:
            pass

    # Strategy C: first <strong> text
    if not poster_name:
        try:
            strong_els = await el.query_selector_all("strong")
            for strong in strong_els[:3]:
                strong_text = (await strong.inner_text()).strip()
                if strong_text and 2 < len(strong_text) < 60:
                    if strong_text.lower() not in skip_labels:
                        parent_link = await strong.query_selector("xpath=ancestor::a")
                        if parent_link:
                            href = await parent_link.get_attribute("href") or ""
                            if href.startswith("/"):
                                href = "https://www.facebook.com" + href
                            if "facebook.com" in href and not any(sp in href for sp in skip_paths):
                                poster_name = strong_text
                                profile_url = href.split("?")[0]
                                break
                        else:
                            poster_name = strong_text
                            break
        except Exception:
            pass

    # Clean poster name — strip timestamps
    if poster_name:
        poster_name = re.sub(
            r'\s*(?:\d+\s*(?:hours?|hrs?|minutes?|mins?|days?|weeks?|months?|yrs?|years?)\s*ago'
            r'|about\s+(?:an?\s+)?(?:hour|minute|day|week|month|year)\s*ago'
            r'|just\s+now|yesterday|tomorrow'
            r'|\d+[hmdw]'
            r'|(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:\s+at\s+\d{1,2}:\d{2}\s*[AP]M)?'
            r'|\d{1,2}/\d{1,2}/\d{2,4})'
            r'\s*$',
            '', poster_name, flags=re.IGNORECASE
        ).strip()
        poster_name = re.sub(r'[\s·•\-]+$', '', poster_name).strip()

    poster_name = clean_poster_name(poster_name)

    # Post date
    post_date = ""
    post_url = ""
    try:
        time_els = await el.query_selector_all("abbr, span[id*='jsc'] a")
        for te in time_els:
            time_text = (await te.inner_text()).strip()
            href = _normalize_fb_href(await te.get_attribute("href") or "")
            if href and not post_url and any(hint in href.lower() for hint in POST_URL_HINTS):
                post_url = href
            if any(x in time_text.lower() for x in ["h", "min", "just now", "yesterday", "d"]):
                post_date = _parse_relative_time(time_text)
                break
            aria = await te.get_attribute("aria-label") or ""
            if aria:
                post_date = aria
                break
    except Exception:
        pass
    if not post_date:
        post_date = datetime.now(timezone.utc).isoformat()

    # Extract contacts from post text
    contacts = _extract_contacts_from_text(text)

    reference_dt = parse_reference_datetime(post_date)
    event_date = extract_event_date(text, reference_dt=reference_dt)
    event_time = extract_event_time(text)
    event_location = extract_event_location(text)
    expected_attendance = extract_expected_attendance(text)

    event_page_url = ""
    external_links = []
    try:
        links = await el.query_selector_all("a[href]")
        for link in links[:16]:
            href = _normalize_fb_href(await link.get_attribute("href") or "")
            if not href:
                continue
            lower_href = href.lower()
            if "facebook.com" in lower_href:
                if "/events/" in lower_href and not event_page_url:
                    event_page_url = href
                if not post_url and any(hint in lower_href for hint in POST_URL_HINTS):
                    post_url = href
                continue
            external_links.append(href)
    except Exception:
        pass

    if not poster_name and not (profile_url or post_url or contacts.get("phone") or contacts.get("email")):
        return None

    # Extract visible comments — these often contain "I need a restroom trailer" replies
    comment_leads = []
    try:
        # Comments are nested article elements inside the parent article
        comment_els = await el.query_selector_all('[role="article"]')
        for cel in comment_els[:5]:  # Cap at 5 comments per post
            try:
                ct = await cel.inner_text()
                if not ct or len(ct) < 25:
                    continue
                # Only extract comments with lead signals
                ct_lower = ct.lower()
                has_lead_signal = (
                    any(kw in ct_lower for kw in BATHROOM_NEED_KEYWORDS)
                    or any(kw in ct_lower for kw in HOST_INTENT_KEYWORDS)
                    or any(kw in ct_lower for kw in EVENT_REQUEST_KEYWORDS)
                )
                if not has_lead_signal:
                    continue
                # Try to get commenter name from aria-label
                commenter_name = ""
                commenter_url = ""
                c_aria = await cel.get_attribute("aria-label") or ""
                if c_aria:
                    m = re.match(r"(?:Comment by)\s+(.+)", c_aria, re.IGNORECASE)
                    if m:
                        commenter_name = clean_poster_name(m.group(1).strip())
                if not commenter_name:
                    c_links = await cel.query_selector_all("a[href]")
                    for cl in c_links[:4]:
                        ch = await cl.get_attribute("href") or ""
                        if "facebook.com" in ch and "/groups/" not in ch:
                            cl_text = (await cl.inner_text()).strip()
                            if cl_text and 2 < len(cl_text) < 60 and cl_text.lower() not in skip_labels:
                                commenter_name = clean_poster_name(cl_text)
                                commenter_url = ch.split("?")[0]
                                break
                c_contacts = _extract_contacts_from_text(ct)
                comment_leads.append({
                    "commenter_name": commenter_name,
                    "commenter_url": commenter_url,
                    "comment_text": ct[:1000],
                    "phone": c_contacts.get("phone", ""),
                    "email": c_contacts.get("email", ""),
                    "website": c_contacts.get("website", ""),
                })
            except Exception:
                continue
    except Exception:
        pass

    return {
        "poster_name": poster_name,
        "profile_url": profile_url,
        "post_content": text[:5000],
        "post_date": post_date,
        "post_url": post_url or group_url,
        "group_name": group_name,
        "group_url": group_url,
        "event_date": event_date,
        "event_time": event_time,
        "event_location": event_location,
        "event_city": extract_city_from_text(text),
        "expected_attendance": expected_attendance,
        "event_page_url": event_page_url,
        "signup_url": external_links[0] if external_links else "",
        "phone": contacts.get("phone", ""),
        "email": contacts.get("email", ""),
        "website": contacts.get("website", "") or (external_links[0] if external_links else ""),
        "instagram": contacts.get("instagram", ""),
        "comment_leads": comment_leads,
    }


async def extract_posts_from_group(page, group: dict, gql_capture: GraphQLCapture) -> list:
    """Scrape posts from a single Facebook group. Returns list of post dicts."""
    group_url = group["url"]
    group_name = group.get("name", "Unknown")
    log.info(f"Scraping group: {group_name}")

    gql_capture.reset()

    ok = await safe_goto(page, group_url)
    if not ok:
        return []

    # Click Discussion/Posts tab
    for tab_text in ["Discussion", "Posts", "Recent"]:
        try:
            tab = await page.query_selector(f'a[role="tab"]:has-text("{tab_text}")')
            if not tab:
                tab = await page.query_selector(f'a:has-text("{tab_text}")[href*="/groups/"]')
            if tab and await tab.is_visible():
                await tab.click()
                log.info(f"Clicked '{tab_text}' tab for {group_name}")
                await asyncio.sleep(2)
                break
        except Exception:
            continue

    # Wait for feed to load
    feed_loaded = False
    for _ in range(12):
        if await page.query_selector('[role="feed"]') or await page.query_selector('[role="article"]'):
            feed_loaded = True
            break
        await asyncio.sleep(1)

    if not feed_loaded:
        log.warning(f"Feed never loaded for {group_name} — skipping")
        return []

    await asyncio.sleep(1.5)

    # Determine article selector
    feed_el = await page.query_selector('[role="feed"]')
    article_selector = '[role="feed"] [role="article"]' if feed_el else '[role="article"]'

    posts = []
    seen_identities = set()
    scroll_count = 0
    # High-yield groups get deeper scrolling to catch more leads
    max_scrolls = 30 if group.get("posts_scraped", 0) >= 5 else 15
    no_new_count = 0
    prev_count = 0

    while scroll_count < max_scrolls:
        # Guard: stay on group page
        if "/groups/" not in page.url:
            log.warning(f"Navigated away ({page.url}) — going back")
            await safe_goto(page, group_url)
            for _ in range(8):
                if await page.query_selector(article_selector):
                    break
                await asyncio.sleep(1)

        await click_see_more(page)

        # Extract visible posts
        try:
            elements = await page.query_selector_all(article_selector)
            extracted = 0

            for el in elements:
                try:
                    post_data = await _extract_single_post(el, group_name, group_url)
                    if not post_data:
                        continue

                    identity_key = (
                        post_data.get("profile_url")
                        or post_data.get("post_url")
                        or hashlib.sha1((post_data.get("post_content", "")[:200]).encode("utf-8")).hexdigest()
                    )
                    if identity_key in seen_identities:
                        continue

                    if not is_relevant_post(post_data.get("post_content", ""), group_name):
                        continue

                    seen_identities.add(identity_key)
                    posts.append(post_data)
                    extracted += 1
                except Exception:
                    continue

            if scroll_count % 3 == 0 or extracted > 0:
                log.info(
                    f"[{group_name}] scroll {scroll_count}: "
                    f"{len(elements)} articles, {extracted} new"
                )
        except Exception as e:
            log.warning(f"Extract error: {e}")

        # Scroll
        try:
            elements = await page.query_selector_all(article_selector)
            if elements:
                await elements[-1].scroll_into_view_if_needed()
                await asyncio.sleep(0.3)
        except Exception:
            pass
        await scroll_page(page)
        await move_mouse_randomly(page)
        await asyncio.sleep(0.5)

        # Check for new content
        current_count = len(await page.query_selector_all(article_selector))
        if current_count <= prev_count and extracted == 0:
            no_new_count += 1
            if no_new_count >= 4:
                log.info(f"No new content after {scroll_count} scrolls — done with {group_name}")
                break
        else:
            no_new_count = 0
        prev_count = current_count
        scroll_count += 1

    # Merge GraphQL-captured posts
    gql_added = 0
    for gp in gql_capture.get_posts():
        author = gp.get("author_name", "")
        author_url = gp.get("author_url", "")
        text = gp.get("text", "")
        if not author or len(author) < 2:
            continue
        identity_key = author_url or gp.get("post_url") or hashlib.sha1((text[:200]).encode("utf-8")).hexdigest()
        if identity_key in seen_identities:
            continue
        if not is_relevant_post(text, group_name) and text:
            continue

        ts = gp.get("timestamp")
        if isinstance(ts, (int, float)) and ts > 0:
            post_date = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        else:
            post_date = datetime.now(timezone.utc).isoformat()

        contacts = _extract_contacts_from_text(text) if text else {}
        reference_dt = parse_reference_datetime(post_date)
        event_date = extract_event_date(text, reference_dt=reference_dt)
        event_time = extract_event_time(text)
        event_location = extract_event_location(text)
        posts.append({
            "poster_name": author,
            "profile_url": author_url or "",
            "post_content": text,
            "post_date": post_date,
            "post_url": gp.get("post_url") or group_url,
            "group_name": group_name,
            "group_url": group_url,
            "event_date": event_date,
            "event_time": event_time,
            "event_location": event_location,
            "event_city": extract_city_from_text(text),
            "expected_attendance": extract_expected_attendance(text),
            "event_page_url": gp.get("post_url") if "/events/" in str(gp.get("post_url") or "") else "",
            "signup_url": contacts.get("website", ""),
            "phone": contacts.get("phone", ""),
            "email": contacts.get("email", ""),
            "website": contacts.get("website", ""),
            "instagram": contacts.get("instagram", ""),
        })
        seen_identities.add(identity_key)
        gql_added += 1

    dom_count = len(posts) - gql_added
    log.info(
        f"Extracted {len(posts)} posts from {group_name} "
        f"(DOM: {dom_count}, GraphQL: {gql_added}, {scroll_count} scrolls)"
    )
    return posts


# ── Main Loop ────────────────────────────────────────────────────────────────

def load_groups() -> list:
    """Load groups from scrapers/fb_vendor_scraper/groups.json.

    Returns groups sorted for smart rotation:
    1. High-yield groups (5+ leads previously) — scraped every cycle
    2. Stalest groups (longest since last scrape) — fill remaining slots
    3. Never-scraped groups — cycled in gradually (5 per cycle)
    """
    if not GROUPS_FILE.exists():
        log.warning("No groups.json found — use --groups to specify URLs")
        return []
    try:
        with open(GROUPS_FILE) as f:
            groups = json.load(f)
    except Exception as e:
        log.error(f"Failed to load groups.json: {e}")
        return []

    high_yield = []
    scraped = []
    never_scraped = []

    for g in groups:
        if g.get("posts_scraped", 0) >= 5:
            high_yield.append(g)
        elif g.get("last_scraped"):
            scraped.append(g)
        else:
            never_scraped.append(g)

    # High-yield first, then stalest scraped, then up to 5 new groups per cycle
    scraped.sort(key=lambda g: g.get("last_scraped", ""))
    NEW_GROUPS_PER_CYCLE = 5
    ordered = high_yield + scraped + never_scraped[:NEW_GROUPS_PER_CYCLE]

    log.info(
        f"Loaded {len(groups)} groups — {len(high_yield)} high-yield, "
        f"{len(scraped)} previously scraped, "
        f"{min(len(never_scraped), NEW_GROUPS_PER_CYCLE)}/{len(never_scraped)} new this cycle"
    )
    return ordered


def _update_group_stats(group_url: str, posts_found: int):
    """Update last_scraped and posts_scraped for a group in groups.json."""
    if not GROUPS_FILE.exists():
        return
    try:
        with open(GROUPS_FILE) as f:
            all_groups = json.load(f)
        now_iso = datetime.now(timezone.utc).isoformat()
        for g in all_groups:
            if g.get("url") == group_url:
                g["last_scraped"] = now_iso
                g["posts_scraped"] = g.get("posts_scraped", 0) + posts_found
                break
        with open(GROUPS_FILE, "w") as f:
            json.dump(all_groups, f, indent=4, ensure_ascii=False)
    except Exception as e:
        log.warning(f"Could not update groups.json stats: {e}")


async def run_cycle(page, groups: list) -> dict:
    """Run one scraping cycle across all groups."""
    session_start = datetime.now(timezone.utc)
    gql_capture = GraphQLCapture()
    page.on("response", gql_capture.handle_response)
    await setup_page_optimizations(page)

    stats = {"referrals": 0, "events": 0, "alerts": 0, "dupes": 0, "skipped": 0, "groups": 0}

    for gi, group in enumerate(groups, 1):
        # Session time limit
        elapsed = (datetime.now(timezone.utc) - session_start).total_seconds() / 60
        if elapsed >= MAX_SESSION_MINUTES:
            log.info(f"Session time limit ({MAX_SESSION_MINUTES} min) — stopping")
            break

        log.info(f"\n--- Group {gi}/{len(groups)} ---")
        posts = await extract_posts_from_group(page, group, gql_capture)
        stats["groups"] += 1
        _update_group_stats(group["url"], len(posts))

        for post in posts:
            text = post.get("post_content", "")
            category = detect_category(text)
            lead_type = classify_lead_type(text, category)
            post["_category"] = category
            post["poster_name"] = clean_poster_name(post.get("poster_name", ""))

            if lead_type == "REFERRAL_LEAD":
                if not is_actionable_referral_candidate(post):
                    stats["skipped"] += 1
                    continue
                email = post.get("email", "")
                if is_duplicate_referral(post.get("profile_url", ""), post.get("poster_name", ""), email):
                    stats["dupes"] += 1
                    continue

                # Profile enrichment
                profile = {}
                if post.get("profile_url"):
                    profile = await extract_profile_data(page, post["profile_url"])

                score = score_referral_lead(post, profile)
                row_id = save_referral(post, profile, category, score)
                if row_id:
                    stats["referrals"] += 1
                    log.info(
                        f"  REFERRAL #{row_id}: {post['poster_name']} — "
                        f"{category} (score={score})"
                    )

                    # Log event
                    try:
                        from core.nexus_coordination import log_event
                        log_event("REFERRAL_LEAD_FOUND", {
                            "name": post["poster_name"],
                            "category": category,
                            "group": post.get("group_name", ""),
                            "score": score,
                        }, source="fb_group_scraper", push_alert=False)
                    except Exception:
                        pass

                    # Hand off high-quality referrals to deep research agent
                    if score >= 6:
                        enqueue_deep_research("REFERRAL_LEAD", row_id, post, score)

            elif lead_type == "EVENT_OPPORTUNITY":
                content_hash = hashlib.sha256(text.encode()).hexdigest()
                if is_duplicate_event(content_hash):
                    stats["dupes"] += 1
                    continue

                event_score = score_bathroom_relevance(text)
                row_id = save_event(post, content_hash)
                if row_id:
                    stats["events"] += 1
                    event_name = extract_event_name(text) or "(unnamed)"
                    log.info(
                        f"  EVENT #{row_id}: {event_name} — "
                        f"{extract_event_type(text)} (relevance={event_score})"
                    )

                    try:
                        from core.nexus_coordination import log_event
                        payload = {
                            "event": event_name,
                            "type": extract_event_type(text),
                            "poster_name": post.get("poster_name", ""),
                            "city": post.get("event_city") or extract_city_from_text(text),
                            "group": post.get("group_name", ""),
                            "group_url": post.get("group_url", ""),
                            "post_url": post.get("post_url", "") or post.get("group_url", ""),
                            "post_date": post.get("post_date", ""),
                            "event_date": post.get("event_date", "") or extract_event_date(text, reference_dt=parse_reference_datetime(post.get("post_date"))),
                            "event_time": post.get("event_time", "") or extract_event_time(text),
                            "event_location": post.get("event_location", "") or extract_event_location(text),
                            "phone": post.get("phone", ""),
                            "email": post.get("email", ""),
                            "website": post.get("website", ""),
                            "restroom_need_score": event_score,
                            "restroom_need_reason": _explain_restroom_need(text),
                            "snippet": text[:240],
                            "event_lead_id": row_id,
                        }
                        log_event("EVENT_OPPORTUNITY_FOUND", payload, source="fb_group_scraper", push_alert=False)
                        if should_alert_event_lead(post, event_score):
                            log_event("NEW_LEAD", payload, source="fb_group_scraper", push_alert=True)
                            stats["alerts"] += 1
                    except Exception:
                        pass

                    # Track repeat organizers — same person posting 2+ events = P1 target
                    organizer_name = post.get("poster_name", "")
                    organizer_url = post.get("profile_url", "")
                    repeat = track_repeat_organizer(organizer_name, organizer_url)
                    if repeat and repeat["event_count"] >= 2 and not repeat["escalated"]:
                        mark_organizer_escalated(repeat["id"])
                        log.info(
                            f"  REPEAT ORGANIZER: {organizer_name} has posted "
                            f"{repeat['event_count']} events — escalating to P1"
                        )
                        try:
                            from core.nexus_coordination import log_event
                            log_event("REPEAT_ORGANIZER", {
                                "name": organizer_name,
                                "profile_url": organizer_url,
                                "event_count": repeat["event_count"],
                                "group": post.get("group_name", ""),
                            }, source="fb_group_scraper", push_alert=True)
                        except Exception:
                            pass

                    # Hand off high-quality events to deep research agent
                    if event_score >= 7:
                        enqueue_deep_research("EVENT_OPPORTUNITY", row_id, post, event_score)
            else:
                stats["skipped"] += 1

            # Process comment leads — comments with bathroom/event host signals
            for cl in post.get("comment_leads", []):
                comment_text = cl.get("comment_text", "")
                comment_hash = hashlib.sha256(comment_text.encode()).hexdigest()
                if is_duplicate_event(comment_hash):
                    stats["dupes"] += 1
                    continue
                comment_post = {
                    "poster_name": cl.get("commenter_name", ""),
                    "profile_url": cl.get("commenter_url", ""),
                    "post_content": comment_text,
                    "post_date": post.get("post_date", ""),
                    "post_url": post.get("post_url", ""),
                    "group_name": post.get("group_name", ""),
                    "group_url": post.get("group_url", ""),
                    "phone": cl.get("phone", ""),
                    "email": cl.get("email", ""),
                    "website": cl.get("website", ""),
                }
                comment_score = score_bathroom_relevance(comment_text)
                row_id = save_event(comment_post, comment_hash)
                if row_id:
                    stats["events"] += 1
                    log.info(
                        f"  COMMENT LEAD #{row_id}: {cl.get('commenter_name', '?')} — "
                        f"relevance={comment_score} (from comment on {post.get('poster_name', '?')}'s post)"
                    )
                    try:
                        from core.nexus_coordination import log_event
                        payload = {
                            "event": "Comment lead",
                            "poster_name": cl.get("commenter_name", ""),
                            "city": extract_city_from_text(comment_text),
                            "group": post.get("group_name", ""),
                            "post_url": post.get("post_url", ""),
                            "restroom_need_score": comment_score,
                            "snippet": comment_text[:240],
                            "event_lead_id": row_id,
                            "source_type": "comment",
                        }
                        log_event("EVENT_OPPORTUNITY_FOUND", payload, source="fb_group_scraper", push_alert=False)
                        if should_alert_event_lead(comment_post, comment_score):
                            log_event("NEW_LEAD", payload, source="fb_group_scraper", push_alert=True)
                            stats["alerts"] += 1
                    except Exception:
                        pass

        # Rate limit between groups
        if gi < len(groups):
            await random_delay(GROUP_BREAK_MIN, GROUP_BREAK_MAX)

    return stats


# ── Status Reporting ─────────────────────────────────────────────────────────

def write_status(stats: dict, mode: str = "IDLE"):
    """Write coordination status file for other agents to read."""
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)

    conn = _conn()
    try:
        total_referrals = conn.execute("SELECT COUNT(*) FROM referral_leads").fetchone()[0]
        total_events = conn.execute("SELECT COUNT(*) FROM event_leads").fetchone()[0]
    except Exception:
        total_referrals = "?"
        total_events = "?"
    finally:
        conn.close()

    now = datetime.now().strftime("%Y-%m-%d %H:%M PT")
    content = f"""# Coder_2 Status — FB Group Scraper

Last run: {now}
Referral leads found this cycle: {stats.get('referrals', 0)}
Event leads found this cycle: {stats.get('events', 0)}
Hot bathroom alerts this cycle: {stats.get('alerts', 0)}
Duplicates skipped: {stats.get('dupes', 0)}
Groups scraped: {stats.get('groups', 0)}
Posts skipped (irrelevant): {stats.get('skipped', 0)}

Total referral_leads in DB: {total_referrals}
Total event_leads in DB: {total_events}

Status: {mode}
"""
    STATUS_FILE.write_text(content)
    log.info(f"Status written to {STATUS_FILE}")


# ── Lockfile ─────────────────────────────────────────────────────────────────

def acquire_lock():
    """Acquire lockfile to prevent concurrent Chrome profile access."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
        return lock_fd
    except BlockingIOError:
        lock_fd.close()
        log.error(
            "Another FB scraper is running (lockfile held). "
            "Wait for it to finish or remove ~/.nexus/fb_scraper.lock"
        )
        return None


def release_lock(lock_fd):
    if lock_fd:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()
            LOCK_FILE.unlink(missing_ok=True)
        except Exception:
            pass


# ── Progress Persistence ─────────────────────────────────────────────────────

def save_scrape_progress(completed_urls: list, stats: dict):
    """Save scrape progress so marathon sessions can resume after crashes."""
    state = {
        "completed_urls": completed_urls,
        "stats": stats,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        SCRAPE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(SCRAPE_STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        log.warning(f"Could not save scrape progress: {e}")


def load_scrape_progress() -> tuple:
    """Load previous scrape progress. Returns (completed_urls, stats) or ([], {})."""
    if not SCRAPE_STATE_FILE.exists():
        return [], {}
    try:
        with open(SCRAPE_STATE_FILE) as f:
            state = json.load(f)
        saved_at = state.get("saved_at", "")
        # Only resume if saved less than 12 hours ago
        if saved_at:
            saved_dt = datetime.fromisoformat(saved_at)
            if (datetime.now(timezone.utc) - saved_dt).total_seconds() > 43200:
                log.info("Scrape progress too old (>12h) — starting fresh")
                return [], {}
        log.info(f"Resuming from {len(state.get('completed_urls', []))} previously scraped groups")
        return state.get("completed_urls", []), state.get("stats", {})
    except Exception:
        return [], {}


def clear_scrape_progress():
    """Clear saved progress after a complete run."""
    try:
        SCRAPE_STATE_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# ── Fleet Queue Handoff ─────────────────────────────────────────────────────

def enqueue_deep_research(lead_type: str, row_id: int, post: dict, score: int):
    """Enqueue a high-quality lead for deep research by the research agent."""
    try:
        from core.fleet_task_queue import FleetTaskQueue
        queue = FleetTaskQueue()
        text = post.get("post_content", "")
        task_id = queue.enqueue(
            task_type="deep_lead_research",
            input_data={
                "lead_type": lead_type,
                "lead_id": row_id,
                "table": "event_leads" if lead_type == "EVENT_OPPORTUNITY" else "referral_leads",
                "poster_name": post.get("poster_name", ""),
                "profile_url": post.get("profile_url", ""),
                "website": post.get("website", ""),
                "email": post.get("email", ""),
                "phone": post.get("phone", ""),
                "post_text": text[:2000],
                "post_url": post.get("post_url", ""),
                "group_name": post.get("group_name", ""),
                "event_date": post.get("event_date", ""),
                "event_location": post.get("event_location", ""),
                "score": score,
            },
            tier=2,
            priority=max(1, 10 - score),  # higher score = higher priority (lower number)
        )
        log.info(f"  Enqueued deep research task {task_id} for {post.get('poster_name', '?')} (score={score})")
    except Exception as e:
        log.warning(f"Could not enqueue deep research task: {e}")


# ── Entry Point ──────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Facebook Group Scraper — Referral + Event Leads")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--marathon", action="store_true", help="Extended 8-hour session with warm browser restarts")
    parser.add_argument("--interval", type=int, default=360, help="Minutes between cycles (default 360)")
    parser.add_argument("--groups", type=str, help="Comma-separated group URLs (overrides groups.json)")
    args = parser.parse_args()

    # Marathon mode overrides session limit
    session_limit = MARATHON_SESSION_MINUTES if args.marathon else MAX_SESSION_MINUTES

    init_db()

    # Load groups
    if args.groups:
        groups = [
            {"name": f"Group {i}", "url": url.strip()}
            for i, url in enumerate(args.groups.split(","), 1)
        ]
    else:
        groups = load_groups()

    if not groups:
        log.error("No groups to scrape. Provide --groups or populate groups.json")
        return

    # Acquire lock
    lock_fd = acquire_lock()
    if not lock_fd:
        return

    try:
        _agent = AgentRuntime('facebook_scraper', 'scraper', 'lead_generation')
        _agent.start()

        if args.marathon:
            # Marathon mode: run for hours with warm browser restarts
            log.info(f"MARATHON MODE: {session_limit} min session with browser restart every {WARM_RESTART_MINUTES} min")
            marathon_start = datetime.now(timezone.utc)
            completed_urls, cumulative_stats = load_scrape_progress()
            if not cumulative_stats:
                cumulative_stats = {"referrals": 0, "events": 0, "alerts": 0, "dupes": 0, "skipped": 0, "groups": 0}
            segment = 0

            while True:
                elapsed_total = (datetime.now(timezone.utc) - marathon_start).total_seconds() / 60
                if elapsed_total >= session_limit:
                    log.info(f"Marathon session complete after {elapsed_total:.0f} minutes")
                    break

                segment += 1
                remaining_groups = [g for g in groups if g["url"] not in completed_urls]
                if not remaining_groups:
                    log.info("All groups scraped this marathon — reloading fresh rotation")
                    groups = load_groups()
                    completed_urls = []
                    remaining_groups = groups

                log.info(f"\n{'='*60}")
                log.info(f"MARATHON SEGMENT {segment}: {len(remaining_groups)} groups remaining, "
                         f"{elapsed_total:.0f}/{session_limit} min elapsed")
                log.info(f"{'='*60}")

                # Launch browser for this segment
                pw, context = await launch_browser()
                page = context.pages[0] if context.pages else await context.new_page()

                if not await is_logged_in(page):
                    log.error("Not logged into Facebook. Log in manually first.")
                    write_status(cumulative_stats, "ERROR: Not logged in")
                    await context.close()
                    await pw.stop()
                    break

                _agent.heartbeat(f"Marathon segment {segment}: {len(remaining_groups)} groups")

                try:
                    # Override session limit for this segment (warm restart interval)
                    import config as _cfg
                    original_limit = _cfg.MAX_SESSION_MINUTES
                    _cfg.MAX_SESSION_MINUTES = min(WARM_RESTART_MINUTES, session_limit - elapsed_total)

                    stats = await run_cycle(page, remaining_groups)

                    _cfg.MAX_SESSION_MINUTES = original_limit

                    # Accumulate stats
                    for key in cumulative_stats:
                        cumulative_stats[key] = cumulative_stats.get(key, 0) + stats.get(key, 0)

                    # Track completed groups
                    for g in remaining_groups[:stats.get("groups", 0)]:
                        completed_urls.append(g["url"])

                    save_scrape_progress(completed_urls, cumulative_stats)
                    write_status(cumulative_stats, f"MARATHON segment {segment}")
                    log.info(f"Segment {segment} done: {stats} | Cumulative: {cumulative_stats}")

                    try:
                        from core.nexus_coordination import log_event
                        log_event("FB_GROUP_SCRAPE_SEGMENT", {
                            **stats, "segment": segment, "cumulative": cumulative_stats,
                        }, "fb_group_scraper", push_alert=False)
                    except Exception:
                        pass

                except Exception as e:
                    log.error(f"Marathon segment {segment} error: {e}")
                    _agent.report_error(str(e), will_retry=True)
                finally:
                    await context.close()
                    await pw.stop()

                # Brief pause before next browser launch
                log.info("Browser restart pause (30s)...")
                await asyncio.sleep(30)

            clear_scrape_progress()
            write_status(cumulative_stats, "MARATHON COMPLETE")
            log.info(f"Marathon complete: {cumulative_stats}")
            try:
                from core.nexus_coordination import log_event
                log_event("FB_GROUP_SCRAPE_COMPLETE", {
                    **cumulative_stats, "mode": "marathon", "segments": segment,
                }, "fb_group_scraper", push_alert=False)
            except Exception:
                pass

        else:
            # Standard mode: single browser session
            pw, context = await launch_browser()
            page = context.pages[0] if context.pages else await context.new_page()

            if not await is_logged_in(page):
                log.error("Not logged into Facebook. Log in manually in the browser window first.")
                write_status({"referrals": 0, "events": 0, "dupes": 0, "skipped": 0, "groups": 0}, "ERROR: Not logged in")
                await context.close()
                await pw.stop()
                return

            log.info("Facebook login confirmed")

            if args.once:
                stats = await run_cycle(page, groups)
                write_status(stats, "COMPLETE")
                log.info(f"Cycle complete: {stats}")

                try:
                    from core.nexus_coordination import log_event
                    log_event("FB_GROUP_SCRAPE_COMPLETE", stats, "fb_group_scraper", push_alert=False)
                except Exception:
                    pass
            else:
                cycle = 0
                while True:
                    cycle += 1
                    _agent.heartbeat(f"Cycle {cycle}: scanning {len(groups)} groups")
                    log.info(f"\n{'='*60}")
                    log.info(f"DAEMON CYCLE {cycle}")
                    log.info(f"{'='*60}")

                    try:
                        stats = await run_cycle(page, groups)
                        _agent.heartbeat(
                            f"Cycle {cycle} done: {stats.get('referrals', 0)} referrals, "
                            f"{stats.get('events', 0)} events"
                        )
                        write_status(stats, f"IDLE (next in {args.interval} min)")
                        log.info(f"Cycle {cycle} complete: {stats}")

                        try:
                            from core.nexus_coordination import log_event
                            log_event("FB_GROUP_SCRAPE_COMPLETE", stats, "fb_group_scraper", push_alert=False)
                        except Exception:
                            pass
                    except Exception as e:
                        log.error(f"Cycle {cycle} error: {e}")
                        _agent.report_error(str(e), will_retry=True)
                        write_status({"error": str(e)}, "ERROR")

                    log.info(f"Sleeping {args.interval} minutes...")
                    await asyncio.sleep(args.interval * 60)

            await context.close()
            await pw.stop()

    finally:
        try:
            _agent.shutdown()
        except Exception:
            pass
        release_lock(lock_fd)


if __name__ == "__main__":
    asyncio.run(main())
