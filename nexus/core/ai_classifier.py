"""
AI Classifier — Natural language message routing for Nexus CRM.

Adds natural language understanding so Kai can type naturally in Telegram
instead of memorizing slash commands. Uses Claude Haiku for fast classification
with an always-available keyword fallback for offline operation.

Classification flow:
  1. Try AI classifier (Claude Haiku, 5s timeout)
  2. On failure/timeout → keyword classifier (instant, offline)
  3. Extract entities (names, dates, phones, prices, lead IDs)
  4. Generate response + equivalent slash command

Entry point: handle_natural_language(text, send_fn=None)
  → returns (response_text, command_to_execute_or_None)

Persistence: SQLite at ~/.nexus/memory.db (classifier_stats table)
Timezone: America/Los_Angeles
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from zoneinfo import ZoneInfo

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_FILE = Path.home() / ".nexus" / "config.json"
LA_TZ = ZoneInfo("America/Los_Angeles")

# ── Intent categories ─────────────────────────────────────────────────────────

INTENTS = {
    "check_availability": "Check if a date is available for booking",
    "create_lead":        "Create a new lead / inquiry",
    "check_lead":         "Look up an existing lead's status",
    "create_booking":     "Book the trailer for a specific date",
    "check_bookings":     "List upcoming bookings",
    "send_quote":         "Generate and send a quote to a lead",
    "check_ads":          "Check ad performance / CPL / spend",
    "add_todo":           "Add a to-do item or reminder",
    "add_note":           "Save a note about a lead or the business",
    "health_check":       "Check system health / service status",
    "revenue_check":      "Check revenue / income / earnings",
    "general_question":   "General question not related to CRM",
    "unknown":            "Cannot classify",
}

# ── SoCal city names for entity extraction ────────────────────────────────────

SOCAL_CITIES = {
    "los angeles", "la", "hollywood", "beverly hills", "santa monica",
    "malibu", "pasadena", "burbank", "glendale", "long beach",
    "torrance", "inglewood", "culver city", "west hollywood", "weho",
    "encino", "sherman oaks", "studio city", "north hollywood", "noho",
    "woodland hills", "calabasas", "tarzana", "van nuys", "reseda",
    "chatsworth", "northridge", "granada hills", "porter ranch",
    "santa clarita", "valencia", "canyon country", "newhall",
    "simi valley", "thousand oaks", "westlake village", "agoura hills",
    "camarillo", "oxnard", "ventura", "moorpark", "fillmore",
    "pomona", "claremont", "glendora", "azusa", "covina",
    "rancho cucamonga", "ontario", "fontana", "riverside",
    "anaheim", "irvine", "newport beach", "huntington beach",
    "laguna beach", "san diego", "temecula", "palm springs",
    "san fernando", "sun valley", "pacoima", "sylmar", "arleta",
    "downey", "whittier", "el monte", "west covina", "arcadia",
    "monrovia", "duarte", "montrose", "la crescenta", "altadena",
    "eagle rock", "highland park", "silver lake", "echo park",
    "los feliz", "koreatown", "mid-city", "mid city", "westwood",
    "brentwood", "pacific palisades", "venice", "marina del rey",
    "playa del rey", "el segundo", "manhattan beach", "hermosa beach",
    "redondo beach", "san pedro", "wilmington", "carson",
    "compton", "paramount", "bellflower", "cerritos", "lakewood",
}

EVENT_TYPES = {
    "wedding", "corporate", "birthday", "party", "quinceañera",
    "quinceanera", "festival", "construction", "film", "filming",
    "shoot", "photo shoot", "graduation", "baby shower", "bridal shower",
    "engagement", "reception", "gala", "fundraiser", "concert",
    "outdoor event", "bbq", "barbecue", "reunion", "block party",
    "anniversary", "bar mitzvah", "bat mitzvah", "sweet 16",
}

# ── Month/day patterns for date parsing ───────────────────────────────────────

MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
    "oct": 10, "nov": 11, "dec": 12,
}

DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


# ══════════════════════════════════════════════════════════════════════════════
# CONFIG + DB HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _load_config() -> dict:
    """Load config from ~/.nexus/config.json."""
    try:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text())
    except Exception:
        pass
    return {}


def _get_anthropic_key() -> str:
    """Get Anthropic API key from config or environment."""
    cfg = _load_config()
    return cfg.get("anthropic_key", "") or os.environ.get("ANTHROPIC_API_KEY", "")


def _conn() -> sqlite3.Connection:
    """Get a database connection with WAL mode."""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_stats_table():
    """Create classifier_stats table if needed."""
    try:
        conn = _conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS classifier_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT DEFAULT (datetime('now')),
                text_input TEXT,
                intent TEXT,
                confidence REAL,
                method TEXT DEFAULT 'keyword',
                ai_intent TEXT DEFAULT '',
                keyword_intent TEXT DEFAULT '',
                agreed INTEGER DEFAULT 1,
                duration_ms REAL DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[AI] Stats table init error: {e}")


_init_stats_table()


def _log_classification(text: str, intent: str, confidence: float, method: str,
                        ai_intent: str = "", keyword_intent: str = "",
                        agreed: bool = True, duration_ms: float = 0):
    """Log a classification to the stats table."""
    try:
        conn = _conn()
        conn.execute(
            "INSERT INTO classifier_stats (text_input, intent, confidence, method, "
            "ai_intent, keyword_intent, agreed, duration_ms) VALUES (?,?,?,?,?,?,?,?)",
            (text[:500], intent, confidence, method, ai_intent, keyword_intent,
             int(agreed), duration_ms)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# ENTITY EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

def extract_entities(text: str) -> dict[str, Any]:
    """
    Extract structured entities from natural language text.

    Returns dict with keys: name, date, phone, price, lead_id, location, event_type
    """
    entities: dict[str, Any] = {}
    lower = text.lower()

    # ── Lead IDs: "#45", "lead 45", "lead number 45", "lead #45" ──
    lead_id_match = re.search(r'(?:lead\s*(?:number\s*)?#?\s*|#)(\d+)', lower)
    if lead_id_match:
        entities["lead_id"] = int(lead_id_match.group(1))

    # ── Phone numbers: various US formats ──
    phone_patterns = [
        r'\((\d{3})\)\s*(\d{3})[\s.-]?(\d{4})',          # (818) 555-1234
        r'(\d{3})[\s.-](\d{3})[\s.-](\d{4})',             # 818-555-1234, 818.555.1234
        r'(\d{3})(\d{3})(\d{4})',                          # 8185551234
    ]
    for pattern in phone_patterns:
        phone_match = re.search(pattern, text)
        if phone_match:
            digits = "".join(phone_match.groups())
            if len(digits) == 10:
                entities["phone"] = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
                break

    # ── Prices: "$1500", "$1,500", "fifteen hundred", "$1500.00" ──
    price_match = re.search(r'\$\s?([\d,]+(?:\.\d{2})?)', text)
    if price_match:
        price_str = price_match.group(1).replace(",", "")
        try:
            entities["price"] = float(price_str)
        except ValueError:
            pass

    # Word-based prices
    if "price" not in entities:
        word_prices = {
            "fifteen hundred": 1500, "twelve hundred": 1200,
            "thirteen hundred": 1300, "fourteen hundred": 1400,
            "eleven hundred": 1100, "sixteen hundred": 1600,
            "two thousand": 2000, "twenty five hundred": 2500,
            "one thousand": 1000, "a thousand": 1000,
            "nine hundred": 900, "eight hundred": 800,
        }
        for phrase, value in word_prices.items():
            if phrase in lower:
                entities["price"] = float(value)
                break

    # ── Dates ──
    now = datetime.now(LA_TZ)

    # Relative dates
    if "tomorrow" in lower:
        d = now + timedelta(days=1)
        entities["date"] = d.strftime("%B %-d")
    elif "today" in lower and ("avail" in lower or "book" in lower or "open" in lower or "free" in lower):
        entities["date"] = now.strftime("%B %-d")
    elif "next week" in lower:
        d = now + timedelta(days=(7 - now.weekday()))
        entities["date"] = d.strftime("%B %-d")
    elif "this weekend" in lower:
        days_until_sat = (5 - now.weekday()) % 7
        if days_until_sat == 0 and now.weekday() != 5:
            days_until_sat = 7
        d = now + timedelta(days=days_until_sat)
        entities["date"] = d.strftime("%B %-d")

    # "next Saturday", "this Friday"
    if "date" not in entities:
        for i, day_name in enumerate(DAY_NAMES):
            pattern = rf'(?:next|this)\s+{day_name}'
            if re.search(pattern, lower):
                current_dow = now.weekday()
                target_dow = i
                days_ahead = (target_dow - current_dow) % 7
                if "next" in lower and days_ahead == 0:
                    days_ahead = 7
                elif "next" in lower:
                    days_ahead += 7
                if days_ahead == 0:
                    days_ahead = 7
                d = now + timedelta(days=days_ahead)
                entities["date"] = d.strftime("%B %-d")
                break

    # "March 15", "march 15th", "Mar 15", "3/15", "the 15th"
    if "date" not in entities:
        # Full month + day: "March 15" or "March 15th"
        for month_name, month_num in MONTH_NAMES.items():
            pattern = rf'\b{month_name}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b'
            m = re.search(pattern, lower)
            if m:
                day = int(m.group(1))
                if 1 <= day <= 31:
                    month_full = datetime(now.year, month_num, 1).strftime("%B")
                    entities["date"] = f"{month_full} {day}"
                    break

    if "date" not in entities:
        # Numeric: "3/15", "03/15"
        m = re.search(r'\b(\d{1,2})/(\d{1,2})\b', text)
        if m:
            month_num, day = int(m.group(1)), int(m.group(2))
            if 1 <= month_num <= 12 and 1 <= day <= 31:
                month_full = datetime(now.year, month_num, 1).strftime("%B")
                entities["date"] = f"{month_full} {day}"

    if "date" not in entities:
        # "the 15th", "the 20th" — assume current or next month
        m = re.search(r'\bthe\s+(\d{1,2})(?:st|nd|rd|th)\b', lower)
        if m:
            day = int(m.group(1))
            if 1 <= day <= 31:
                # If the day has passed this month, assume next month
                if day < now.day:
                    next_month = now.month + 1 if now.month < 12 else 1
                    month_full = datetime(now.year, next_month, 1).strftime("%B")
                else:
                    month_full = now.strftime("%B")
                entities["date"] = f"{month_full} {day}"

    # ── Event types ──
    for evt in EVENT_TYPES:
        if evt in lower:
            entities["event_type"] = evt
            break

    # ── Locations (SoCal cities) ──
    for city in sorted(SOCAL_CITIES, key=len, reverse=True):
        # Check for city name as whole word (case insensitive)
        pattern = rf'\b{re.escape(city)}\b'
        if re.search(pattern, lower):
            entities["location"] = city.title()
            break

    # ── Names — heuristic extraction ──
    # Look for "name: X", "for X", "new lead X", "new lead: X"
    name_patterns = [
        r'(?:new\s+lead[:\s]+)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)',
        r'(?:for\s+)([A-Z][a-z]+(?:\s+[A-Z][a-z]+))\s+(?:wedding|party|event|birthday|corporate)',
        r'(?:the\s+)([A-Z][a-z]+(?:s)?)\s+(?:lead|wedding|party|event|booking)',
        r'(?:name[:\s]+)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)',
        r'(?:client[:\s]+)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)',
    ]
    for pattern in name_patterns:
        m = re.search(pattern, text)
        if m:
            candidate = m.group(1).strip()
            # Filter out common false positives
            if candidate.lower() not in {"the", "a", "an", "for", "new", "lead",
                                          "march", "april", "may", "june", "july",
                                          "august", "september", "october", "november",
                                          "december", "january", "february",
                                          "monday", "tuesday", "wednesday", "thursday",
                                          "friday", "saturday", "sunday"}:
                entities["name"] = candidate
                break

    # Fallback: look for capitalized name after "new lead" (case-insensitive command)
    if "name" not in entities:
        m = re.search(r'(?i)new\s+lead[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)', text)
        if m:
            entities["name"] = m.group(1).strip()

    return entities


# ══════════════════════════════════════════════════════════════════════════════
# KEYWORD-BASED CLASSIFIER (offline fallback)
# ══════════════════════════════════════════════════════════════════════════════

def classify_by_keywords(text: str) -> dict[str, Any]:
    """
    Fast keyword-based classification. Returns in <10ms.

    Returns: {intent, confidence, entities, suggested_command}
    """
    lower = text.lower().strip()
    entities = extract_entities(text)
    confidence = 0.0
    intent = "unknown"

    # ── Check availability ──
    avail_keywords = {"available", "availability", "open", "free", "booked", "taken"}
    date_indicators = bool(entities.get("date")) or any(
        m in lower for m in list(MONTH_NAMES.keys()) + ["tomorrow", "next", "weekend", "the "]
    )
    if any(kw in lower for kw in avail_keywords) and date_indicators:
        intent = "check_availability"
        confidence = 0.85
    elif any(kw in lower for kw in avail_keywords) and ("date" in lower or "day" in lower or "when" in lower):
        intent = "check_availability"
        confidence = 0.7
    elif re.search(r'\bis\b.*\b(?:the\s+\d|march|april|may|june|july|aug|sep|oct|nov|dec|tomorrow)\b', lower):
        # "is March 15 available?" — even without explicit "available"
        if "?" in text:
            intent = "check_availability"
            confidence = 0.7
    elif re.search(r'(?:do we have|anything|are we)\b.*\b(?:on the|on|for the)\b', lower) and date_indicators:
        # "do we have anything on the 20th?", "are we free on March 15?"
        intent = "check_availability"
        confidence = 0.8

    # ── Send quote (check BEFORE check_lead so "quote to lead 23" isn't misrouted) ──
    if intent == "unknown":
        quote_keywords = {"send quote", "send a quote", "create quote", "make quote",
                          "generate quote", "price quote", "estimate"}
        if any(kw in lower for kw in quote_keywords):
            intent = "send_quote"
            confidence = 0.85
        elif "quote" in lower and (entities.get("lead_id") or entities.get("price")):
            intent = "send_quote"
            confidence = 0.8
        elif "quote" in lower and ("for" in lower or "to" in lower):
            intent = "send_quote"
            confidence = 0.7

    # ── Add note (check BEFORE check_lead — "note: lead 5 prefers email" is a note, not a lead lookup) ──
    if intent == "unknown":
        if re.match(r'^(?:note|remember|fyi|important|heads up)[:\s]', lower):
            intent = "add_note"
            confidence = 0.9
        elif "add a note" in lower or "make a note" in lower or "save a note" in lower:
            intent = "add_note"
            confidence = 0.85

    # ── Create lead ──
    if intent == "unknown":
        lead_keywords = {"new lead", "add lead", "create lead", "new inquiry",
                         "new prospect", "add contact", "new contact"}
        if any(kw in lower for kw in lead_keywords):
            intent = "create_lead"
            confidence = 0.9

    # ── Check lead ──
    if intent == "unknown":
        check_lead_patterns = [
            r"(?:what'?s|how'?s|check|status|update|info)\s+(?:on|about|for|with)\s+(?:the\s+)?(?:lead|#?\d+)",
            r"(?:lead|#)\s*\d+",
            r"(?:what|how)\s+(?:is|are)\s+(?:the\s+)?(\w+)\s+lead",
            r"(?:status|update)\s+(?:on\s+)?lead",
        ]
        for pattern in check_lead_patterns:
            if re.search(pattern, lower):
                intent = "check_lead"
                confidence = 0.8
                break

    # ── Create booking ──
    if intent == "unknown":
        book_keywords = {"book", "confirm", "reserve", "schedule"}
        if any(kw in lower for kw in book_keywords):
            # Distinguish from "check_bookings" — booking creation has a date/name
            if entities.get("date") or entities.get("name") or entities.get("lead_id"):
                intent = "create_booking"
                confidence = 0.8
            elif "the trailer" in lower or "it" in lower:
                intent = "create_booking"
                confidence = 0.7

    # ── Check bookings ──
    if intent == "unknown":
        if re.search(r'(?:what|show|list|any|upcoming)\s*(?:are\s+)?(?:the\s+)?book(?:ing)?s', lower):
            intent = "check_bookings"
            confidence = 0.8
        elif "bookings" in lower and ("this" in lower or "next" in lower or "month" in lower or "week" in lower):
            intent = "check_bookings"
            confidence = 0.75
        elif lower.strip() in ("bookings", "upcoming bookings", "show bookings"):
            intent = "check_bookings"
            confidence = 0.85

    # ── Check ads ──
    if intent == "unknown":
        ads_keywords = {"ads", "ad performance", "campaign", "cpl", "cost per lead",
                        "ad spend", "spend", "facebook ads", "fb ads", "meta ads",
                        "roas", "impressions", "click-through", "ctr"}
        if any(kw in lower for kw in ads_keywords):
            intent = "check_ads"
            confidence = 0.85

    # ── Add todo / reminder ──
    if intent == "unknown":
        todo_keywords = {"remind me", "reminder", "todo", "to-do", "to do",
                         "add to list", "add to my list", "don't forget",
                         "dont forget", "need to", "remember to"}
        if any(kw in lower for kw in todo_keywords):
            intent = "add_todo"
            confidence = 0.8

    # ── Health check ──
    if intent == "unknown":
        health_keywords = {"system status", "health check", "any issues",
                           "how's the system", "hows the system", "system health",
                           "watchdog", "services status", "all systems"}
        if any(kw in lower for kw in health_keywords):
            intent = "health_check"
            confidence = 0.85
        elif lower.strip() in ("status", "health"):
            intent = "health_check"
            confidence = 0.7

    # ── Revenue check ──
    if intent == "unknown":
        rev_keywords = {"revenue", "income", "how much", "earnings", "earned",
                        "money", "profit", "total sales", "sales numbers",
                        "monthly revenue", "this month"}
        if any(kw in lower for kw in rev_keywords):
            # "how much" needs additional context to be revenue
            if "how much" in lower and any(w in lower for w in
                                            {"make", "made", "earn", "earned", "revenue",
                                             "bring", "brought", "gross", "net", "month", "week"}):
                intent = "revenue_check"
                confidence = 0.8
            elif "how much" not in lower:
                intent = "revenue_check"
                confidence = 0.8

    # ── General question (catch-all for questions) ──
    if intent == "unknown" and "?" in text:
        intent = "general_question"
        confidence = 0.4

    suggested_command = _intent_to_command(intent, entities)

    return {
        "intent": intent,
        "confidence": confidence,
        "entities": entities,
        "suggested_command": suggested_command,
        "method": "keyword",
    }


# ══════════════════════════════════════════════════════════════════════════════
# AI-POWERED CLASSIFIER (Claude Haiku)
# ══════════════════════════════════════════════════════════════════════════════

AI_SYSTEM_PROMPT = """You are a message classifier for Zoar Bathroom Rentals' CRM system (Nexus).
Zoar rents a luxury 4-stall restroom trailer in SoCal. The user is Kai, the business owner.

Classify each message into exactly one intent. Return ONLY valid JSON, no other text.

Intent categories:
- check_availability: asking if a date is free. Examples: "is March 15 available?", "do we have anything on the 20th?", "are we free next Saturday?"
- create_lead: adding a new lead/contact. Examples: "new lead: John Smith, wedding, 818-555-1234", "add lead Sarah Johnson from Malibu"
- check_lead: looking up a lead's status. Examples: "what's happening with the Johnson lead?", "status of lead 45", "how's lead #12?"
- create_booking: confirming a booking. Examples: "book the trailer for March 15 for Smith wedding", "confirm the reservation for lead 23"
- check_bookings: listing bookings. Examples: "what bookings do we have this month?", "upcoming events", "show me the schedule"
- send_quote: creating/sending a quote. Examples: "send a quote to lead 23 for $1500", "quote for the Johnson wedding"
- check_ads: ad performance. Examples: "how are the ads doing?", "what's our CPL?", "ad spend this week"
- add_todo: reminders/tasks. Examples: "remind me to call John tomorrow", "add to my list: order supplies", "don't forget to check inventory"
- add_note: saving a note. Examples: "note: the Johnsons want Saturday delivery before 9am", "remember: lead 5 prefers email"
- health_check: system status. Examples: "how's the system?", "any issues?", "status check"
- revenue_check: money/earnings. Examples: "how much did we make this month?", "revenue update", "total earnings"
- general_question: off-topic or general. Examples: "what time is it?", "how's the weather?"
- unknown: truly unclassifiable

Response format (JSON only):
{
  "intent": "one_of_the_above",
  "confidence": 0.0-1.0,
  "entities": {
    "name": "extracted name or null",
    "date": "extracted date or null",
    "phone": "extracted phone or null",
    "price": extracted_number_or_null,
    "lead_id": extracted_number_or_null,
    "location": "city name or null",
    "event_type": "wedding/corporate/etc or null"
  }
}"""


async def classify_with_ai(text: str) -> dict[str, Any] | None:
    """
    Classify message using Claude Haiku. Returns None on failure/timeout.

    Lazy-imports anthropic to avoid module-level dependency.
    """
    api_key = _get_anthropic_key()
    if not api_key:
        print("[AI] No Anthropic key — skipping AI classification")
        return None

    try:
        import anthropic
    except ImportError:
        print("[AI] anthropic package not installed — skipping AI classification")
        return None

    try:
        client = anthropic.Anthropic(api_key=api_key)
        loop = asyncio.get_running_loop()

        def _call():
            return client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                system=AI_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": text}],
            )

        # 5-second timeout
        r = await asyncio.wait_for(
            loop.run_in_executor(None, _call),
            timeout=5.0,
        )

        # Parse the response
        content = ""
        for block in r.content:
            if hasattr(block, "text"):
                content = block.text
                break

        if not content:
            return None

        # Extract JSON from response (handle markdown code blocks)
        json_str = content.strip()
        if json_str.startswith("```"):
            # Strip ```json and ``` markers
            lines = json_str.split("\n")
            json_str = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            json_str = json_str.strip()

        result = json.loads(json_str)

        # Normalize
        result.setdefault("intent", "unknown")
        result.setdefault("confidence", 0.5)
        result.setdefault("entities", {})

        # Clean null entities
        entities = result["entities"]
        result["entities"] = {k: v for k, v in entities.items() if v is not None}

        # Validate intent
        if result["intent"] not in INTENTS:
            result["intent"] = "unknown"
            result["confidence"] = 0.3

        result["method"] = "ai"
        result["suggested_command"] = _intent_to_command(result["intent"], result["entities"])
        return result

    except asyncio.TimeoutError:
        print("[AI] Claude classification timed out (5s)")
        return None
    except json.JSONDecodeError as e:
        print(f"[AI] Failed to parse AI response as JSON: {e}")
        return None
    except Exception as e:
        print(f"[AI] Classification error: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# MAIN CLASSIFY FUNCTION
# ══════════════════════════════════════════════════════════════════════════════

async def classify_message(text: str) -> dict[str, Any]:
    """
    Classify a message. Tries AI first, falls back to keywords.

    Returns: {intent, confidence, entities, suggested_command, method}
    """
    t0 = time.time()

    # Always run keyword classifier (fast baseline)
    keyword_result = classify_by_keywords(text)

    # Try AI classifier
    ai_result = await classify_with_ai(text)

    duration_ms = (time.time() - t0) * 1000

    if ai_result and ai_result.get("intent") != "unknown":
        # Use AI result, but merge in any entities keyword found that AI missed
        for key, value in keyword_result.get("entities", {}).items():
            if key not in ai_result.get("entities", {}):
                ai_result["entities"][key] = value

        # Regenerate suggested_command with merged entities
        ai_result["suggested_command"] = _intent_to_command(
            ai_result["intent"], ai_result["entities"]
        )

        # Log agreement/disagreement
        agreed = ai_result["intent"] == keyword_result["intent"]
        if not agreed and keyword_result["confidence"] >= 0.7:
            print(f"[AI] Classifier disagreement: AI={ai_result['intent']} "
                  f"vs Keyword={keyword_result['intent']} for: {text[:80]}")

        _log_classification(
            text, ai_result["intent"], ai_result["confidence"], "ai",
            ai_intent=ai_result["intent"], keyword_intent=keyword_result["intent"],
            agreed=agreed, duration_ms=duration_ms,
        )
        return ai_result

    # Fall back to keyword result
    _log_classification(
        text, keyword_result["intent"], keyword_result["confidence"], "keyword",
        ai_intent=ai_result["intent"] if ai_result else "",
        keyword_intent=keyword_result["intent"],
        agreed=True, duration_ms=duration_ms,
    )
    return keyword_result


# ══════════════════════════════════════════════════════════════════════════════
# INTENT → COMMAND MAPPING
# ══════════════════════════════════════════════════════════════════════════════

def _intent_to_command(intent: str, entities: dict) -> str | None:
    """Map an intent + entities to the equivalent slash command."""
    date_str = entities.get("date", "")
    lead_id = entities.get("lead_id")
    name = entities.get("name", "")
    phone = entities.get("phone", "")
    price = entities.get("price")
    location = entities.get("location", "")
    event_type = entities.get("event_type", "")

    if intent == "check_availability":
        if date_str:
            return f"/avail {date_str}"
        return "/avail"

    elif intent == "create_lead":
        parts = ["/lead add"]
        if name:
            parts.append(name)
        if phone:
            parts.append(phone)
        if event_type:
            parts.append(event_type)
        if location:
            parts.append(location)
        return " ".join(parts) if len(parts) > 1 else None

    elif intent == "check_lead":
        if lead_id:
            return f"/lead {lead_id}"
        return "/leads"

    elif intent == "create_booking":
        parts = ["/book"]
        if date_str:
            parts.append(date_str)
        if lead_id:
            parts.append(f"lead={lead_id}")
        if name:
            parts.append(name)
        return " ".join(parts) if len(parts) > 1 else None

    elif intent == "check_bookings":
        return "/bookings"

    elif intent == "send_quote":
        parts = ["/quote"]
        if lead_id:
            parts.append(str(lead_id))
        if date_str:
            parts.append(date_str.replace(" ", "").lower())
        if location:
            parts.append(location.lower().replace(" ", "-"))
        if price:
            parts.append(str(int(price)))
        return " ".join(parts)

    elif intent == "check_ads":
        return "/ads"

    elif intent == "add_todo":
        # Extract the actual todo text
        todo_text = _extract_todo_text(entities.get("_original_text", ""))
        if todo_text:
            return f"/todo add {todo_text}"
        return "/todo"

    elif intent == "add_note":
        # The note text is the message after the prefix
        return "/note"

    elif intent == "health_check":
        return "/health"

    elif intent == "revenue_check":
        return "/stats"

    elif intent == "general_question":
        return None

    return None


def _extract_todo_text(text: str) -> str:
    """Extract the actual task description from a todo/reminder message."""
    lower = text.lower()
    # Strip common prefixes
    prefixes = [
        "remind me to ", "reminder to ", "remind me ", "reminder ",
        "don't forget to ", "dont forget to ", "don't forget ",
        "dont forget ", "add to my list: ", "add to my list ",
        "add to list: ", "add to list ", "todo: ", "todo ",
        "to-do: ", "to-do ", "need to ",
    ]
    result = text
    for prefix in prefixes:
        if lower.startswith(prefix):
            result = text[len(prefix):]
            break
    return result.strip()


# ══════════════════════════════════════════════════════════════════════════════
# SMART RESPONSE GENERATOR
# ══════════════════════════════════════════════════════════════════════════════

def generate_response(classification: dict, context: dict | None = None) -> dict[str, Any]:
    """
    Generate a natural language response and command based on classification.

    Returns: {
        command: str or None,       # slash command to execute
        response: str,              # natural language response to show user
        execute: bool,              # whether to auto-execute the command
        needs_confirmation: bool,   # whether to ask for confirmation first
    }
    """
    intent = classification["intent"]
    entities = classification["entities"]
    confidence = classification["confidence"]
    command = classification.get("suggested_command")

    result = {
        "command": command,
        "response": "",
        "execute": False,
        "needs_confirmation": False,
    }

    # Low confidence → ask for clarification
    if confidence < 0.5 and intent != "unknown":
        result["response"] = (
            f"I think you're asking about *{intent.replace('_', ' ')}*, "
            f"but I'm not sure. Can you rephrase?"
        )
        result["command"] = None
        return result

    # ── Check availability ──
    if intent == "check_availability":
        date = entities.get("date", "")
        if date:
            result["response"] = f"Let me check *{date}* for you..."
            result["execute"] = True
        else:
            result["response"] = "What date would you like me to check?"
            result["command"] = None

    # ── Create lead ──
    elif intent == "create_lead":
        name = entities.get("name", "")
        phone = entities.get("phone", "")
        event_type = entities.get("event_type", "")
        location = entities.get("location", "")

        parts = []
        if name:
            parts.append(f"*{name}*")
        if event_type:
            parts.append(f"{event_type.title()}")
        if location:
            parts.append(f"in {location}")
        if phone:
            parts.append(f"Phone: {phone}")

        if name:
            detail = " -- ".join(parts) if parts else name
            result["response"] = f"I'll create a lead for {detail}. Should I add this?"
            result["needs_confirmation"] = True
            result["execute"] = False
        else:
            result["response"] = "I need at least a name to create a lead. Who's the new lead?"
            result["command"] = None

    # ── Check lead ──
    elif intent == "check_lead":
        lead_id = entities.get("lead_id")
        name = entities.get("name", "")
        if lead_id:
            result["response"] = f"Pulling up lead #{lead_id}..."
            result["execute"] = True
        elif name:
            result["response"] = f"Looking up the {name} lead..."
            result["execute"] = True
        else:
            result["response"] = "Which lead? Give me a name or lead number."
            result["command"] = "/leads"
            result["execute"] = True

    # ── Create booking ──
    elif intent == "create_booking":
        date = entities.get("date", "")
        name = entities.get("name", "")
        lead_id = entities.get("lead_id")

        parts = []
        if date:
            parts.append(f"on *{date}*")
        if name:
            parts.append(f"for {name}")
        if lead_id:
            parts.append(f"(lead #{lead_id})")

        if date or lead_id:
            detail = " ".join(parts)
            result["response"] = f"Ready to book the trailer {detail}. Confirm?"
            result["needs_confirmation"] = True
        else:
            result["response"] = "I need a date (and ideally a lead/name) to create a booking. When's the event?"
            result["command"] = None

    # ── Check bookings ──
    elif intent == "check_bookings":
        result["response"] = "Pulling up the booking schedule..."
        result["execute"] = True

    # ── Send quote ──
    elif intent == "send_quote":
        lead_id = entities.get("lead_id")
        price = entities.get("price")
        parts = []
        if lead_id:
            parts.append(f"lead #{lead_id}")
        if price:
            parts.append(f"for ${price:,.0f}")

        if lead_id or price:
            detail = " ".join(parts)
            result["response"] = f"I'll generate a quote for {detail}. Send it?"
            result["needs_confirmation"] = True
        else:
            result["response"] = "Who should I send the quote to? Give me a lead ID or name."
            result["command"] = None

    # ── Check ads ──
    elif intent == "check_ads":
        result["response"] = "Checking ad performance..."
        result["execute"] = True

    # ── Add todo ──
    elif intent == "add_todo":
        result["response"] = "Added to your to-do list."
        result["execute"] = True

    # ── Add note ──
    elif intent == "add_note":
        result["response"] = "Note saved."
        result["execute"] = True

    # ── Health check ──
    elif intent == "health_check":
        result["response"] = "Checking system health..."
        result["execute"] = True

    # ── Revenue check ──
    elif intent == "revenue_check":
        result["response"] = "Pulling up revenue numbers..."
        result["execute"] = True

    # ── General question ──
    elif intent == "general_question":
        result["response"] = ""  # Let the orchestrator handle it
        result["command"] = None
        result["execute"] = False

    # ── Unknown ──
    elif intent == "unknown":
        result["response"] = ""
        result["command"] = None
        result["execute"] = False

    return result


# ══════════════════════════════════════════════════════════════════════════════
# CONVERSATION CONTEXT
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PendingAction:
    """An action waiting for confirmation."""
    command: str
    intent: str
    entities: dict
    response: str
    created_at: float = field(default_factory=time.time)

    def is_expired(self, timeout_seconds: float = 120.0) -> bool:
        return (time.time() - self.created_at) > timeout_seconds


class ConversationContext:
    """
    Tracks last N messages for context-aware classification.

    Handles follow-ups like:
      - "and for the 16th?" → another availability check
      - "yes" / "do it" / "confirm" → execute pending action
    """

    def __init__(self, max_history: int = 5):
        self._history: deque[dict] = deque(maxlen=max_history)
        self._pending: PendingAction | None = None

    def add_message(self, text: str, classification: dict):
        """Record a classified message."""
        self._history.append({
            "text": text,
            "intent": classification["intent"],
            "entities": classification["entities"],
            "ts": time.time(),
        })

    def set_pending(self, action: PendingAction):
        """Set a pending action that needs confirmation."""
        self._pending = action

    def get_pending(self) -> PendingAction | None:
        """Get the pending action, if any and not expired."""
        if self._pending and not self._pending.is_expired():
            return self._pending
        self._pending = None
        return None

    def clear_pending(self):
        """Clear the pending action."""
        self._pending = None

    def last_intent(self) -> str | None:
        """Get the intent of the last message."""
        if self._history:
            return self._history[-1]["intent"]
        return None

    def last_entities(self) -> dict:
        """Get entities from the last message."""
        if self._history:
            return self._history[-1].get("entities", {})
        return {}

    def is_confirmation(self, text: str) -> bool:
        """Check if text is a confirmation response."""
        lower = text.lower().strip()
        confirmations = {
            "yes", "y", "yeah", "yep", "yup", "sure", "ok", "okay",
            "do it", "go ahead", "confirm", "confirmed", "send it",
            "go for it", "approved", "approve", "ship it", "lets go",
            "let's go", "absolutely", "for sure",
        }
        return lower in confirmations

    def is_negation(self, text: str) -> bool:
        """Check if text is a negation response."""
        lower = text.lower().strip()
        negations = {
            "no", "n", "nah", "nope", "cancel", "nevermind", "never mind",
            "forget it", "scratch that", "don't", "dont",
        }
        return lower in negations

    def is_followup(self, text: str) -> bool:
        """
        Check if text looks like a follow-up to the previous message.
        e.g., "and for the 16th?" after an availability check.
        """
        lower = text.lower().strip()
        followup_prefixes = {"and ", "also ", "what about ", "how about ",
                             "and the ", "also the ", "same for "}
        return any(lower.startswith(p) for p in followup_prefixes)

    def get_followup_context(self) -> dict | None:
        """If the current message is a follow-up, return the previous context."""
        if not self._history:
            return None
        last = self._history[-1]
        return {
            "intent": last["intent"],
            "entities": last.get("entities", {}),
        }


# Module-level conversation context (one per bot instance)
_context = ConversationContext()


def get_context() -> ConversationContext:
    return _context


# ══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

async def handle_natural_language(
    text: str,
    send_fn: Callable | None = None,
) -> tuple[str, str | None]:
    """
    Main entry point for natural language processing. Called from bot.py
    for any message that doesn't start with "/".

    Args:
        text: The raw message text
        send_fn: Optional async function to send response messages

    Returns:
        (response_text, command_to_execute_or_None)
    """
    ctx = get_context()
    lower = text.lower().strip()

    # ── Handle confirmations of pending actions ──
    pending = ctx.get_pending()
    if pending:
        if ctx.is_confirmation(text):
            ctx.clear_pending()
            print(f"[AI] Confirmed pending action: {pending.command}")
            if send_fn:
                await send_fn(f"Got it, executing: `{pending.command}`")
            return ("", pending.command)

        if ctx.is_negation(text):
            ctx.clear_pending()
            return ("Cancelled.", None)

    # ── Handle follow-up questions ──
    if ctx.is_followup(text):
        prev = ctx.get_followup_context()
        if prev:
            prev_intent = prev["intent"]
            prev_entities = prev["entities"]

            # Extract new entities from the follow-up text
            new_entities = extract_entities(text)

            # Merge: keep previous entities, override with new ones
            merged_entities = {**prev_entities, **new_entities}

            classification = {
                "intent": prev_intent,
                "confidence": 0.8,
                "entities": merged_entities,
                "suggested_command": _intent_to_command(prev_intent, merged_entities),
                "method": "followup",
            }

            ctx.add_message(text, classification)
            response = generate_response(classification, context=prev)

            if response["needs_confirmation"]:
                ctx.set_pending(PendingAction(
                    command=response["command"] or "",
                    intent=prev_intent,
                    entities=merged_entities,
                    response=response["response"],
                ))
                return (response["response"], None)

            if response["execute"] and response["command"]:
                return (response["response"], response["command"])

            return (response["response"], None)

    # ── Standard classification ──
    classification = await classify_message(text)

    # Store original text for todo extraction
    classification["entities"]["_original_text"] = text

    # Regenerate command with updated entities
    classification["suggested_command"] = _intent_to_command(
        classification["intent"], classification["entities"]
    )

    # For add_note, attach the note text to the command
    if classification["intent"] == "add_note":
        note_text = text
        # Strip common prefixes
        for prefix in ["note:", "remember:", "fyi:", "important:", "heads up:",
                       "note ", "remember ", "fyi ", "important ", "heads up "]:
            if note_text.lower().startswith(prefix):
                note_text = note_text[len(prefix):].strip()
                break
        classification["suggested_command"] = f"/note {note_text}"

    # For add_todo, build the full command
    if classification["intent"] == "add_todo":
        todo_text = _extract_todo_text(text)
        if todo_text:
            classification["suggested_command"] = f"/todo add {todo_text}"

    ctx.add_message(text, classification)
    response = generate_response(classification)

    # If action needs confirmation, set it as pending
    if response["needs_confirmation"] and response["command"]:
        ctx.set_pending(PendingAction(
            command=response["command"],
            intent=classification["intent"],
            entities=classification["entities"],
            response=response["response"],
        ))
        return (response["response"], None)

    # Auto-execute safe commands
    if response["execute"] and response["command"]:
        return (response["response"], response["command"])

    # If unknown/general, let the orchestrator handle it
    if classification["intent"] in ("unknown", "general_question"):
        return ("", None)

    return (response["response"], response.get("command"))


# ══════════════════════════════════════════════════════════════════════════════
# STATS
# ══════════════════════════════════════════════════════════════════════════════

def get_classifier_stats() -> dict[str, Any]:
    """
    Get classifier performance stats.

    Returns: {
        total: int,
        by_intent: {intent: count},
        by_method: {ai: count, keyword: count},
        agreement_rate: float (0-1),
        avg_duration_ms: float,
    }
    """
    try:
        conn = _conn()

        total = conn.execute("SELECT COUNT(*) FROM classifier_stats").fetchone()[0]

        # By intent
        rows = conn.execute(
            "SELECT intent, COUNT(*) as cnt FROM classifier_stats GROUP BY intent ORDER BY cnt DESC"
        ).fetchall()
        by_intent = {row["intent"]: row["cnt"] for row in rows}

        # By method
        rows = conn.execute(
            "SELECT method, COUNT(*) as cnt FROM classifier_stats GROUP BY method"
        ).fetchall()
        by_method = {row["method"]: row["cnt"] for row in rows}

        # Agreement rate (only where both AI and keyword ran)
        row = conn.execute(
            "SELECT COUNT(*) as total, SUM(agreed) as agreed "
            "FROM classifier_stats WHERE ai_intent != '' AND keyword_intent != ''"
        ).fetchone()
        both_total = row["total"] or 0
        both_agreed = row["agreed"] or 0
        agreement_rate = (both_agreed / both_total) if both_total > 0 else 1.0

        # Average duration
        row = conn.execute("SELECT AVG(duration_ms) as avg_ms FROM classifier_stats").fetchone()
        avg_duration = row["avg_ms"] or 0

        conn.close()

        return {
            "total": total,
            "by_intent": by_intent,
            "by_method": by_method,
            "agreement_rate": round(agreement_rate, 3),
            "avg_duration_ms": round(avg_duration, 1),
        }
    except Exception as e:
        print(f"[AI] Stats error: {e}")
        return {
            "total": 0, "by_intent": {}, "by_method": {},
            "agreement_rate": 0, "avg_duration_ms": 0,
        }
