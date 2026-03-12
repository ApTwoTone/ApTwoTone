"""
Agent 4 — Instant Quote Generator

Kai forwards any inquiry message to the Telegram bot. This agent:
1. Parses the message for event type, date, location, guest count
2. Calculates exact price via tier-based pricing
3. Returns a complete ready-to-send quote within 60 seconds
4. If location is missing, asks Kai one question: "What city?"
"""

import re
import logging
from datetime import datetime
from core.pricing import (
    calculate_quote,
    generate_quote_message,
    format_price_breakdown,
    get_event_features,
)

log = logging.getLogger("instant_quote")

# ── Parsing patterns ──────────────────────────────────────────────────────────

EVENT_KEYWORDS = {
    "wedding": ["wedding", "bride", "groom", "bridal", "ceremony", "reception", "nuptial"],
    "quinceañera": ["quinceañera", "quinceanera", "quince", "quinces", "xv", "mis quince"],
    "corporate": ["corporate", "company", "business", "conference", "team building", "retreat", "office"],
    "backyard": ["backyard", "house party", "home", "garden party", "bbq", "cookout", "pool party"],
    "festival": ["festival", "fair", "carnival", "outdoor festival", "music festival", "block party"],
    "film": ["film", "filming", "production", "movie", "shoot", "commercial", "set", "crew"],
    "party": ["party", "birthday", "celebration", "gathering", "event", "reunion", "baby shower", "engagement"],
}

DATE_PATTERNS = [
    # "June 15" or "June 15, 2026" or "june 15th"
    r"(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}(?:st|nd|rd|th)?(?:\s*,?\s*\d{4})?",
    # "6/15/2026" or "6/15" or "06-15-2026"
    r"\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?",
    # "this Saturday", "next weekend", "this weekend"
    r"(?:this|next)\s+(?:saturday|sunday|weekend|friday|month)",
]

GUEST_PATTERNS = [
    r"(\d{2,4})\s*(?:guests?|people|attendees?|ppl|pax)",
    r"(?:about|around|approximately|approx|~)\s*(\d{2,4})",
    r"(?:expecting|have|hosting|inviting)\s+(?:about\s+)?(\d{2,4})",
    r"(\d{2,4})\s*(?:person|head count)",
]

# Cities/locations in SFV and Greater LA
CITY_PATTERNS = [
    r"(?:in|at|near|around)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
    r"(?:location|city|venue|area)(?:\s*(?:is|:))?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
]


def parse_inquiry(message: str) -> dict:
    """Parse a forwarded inquiry message and extract event details.

    Returns dict with: event_type, event_date, city, guest_count, name, raw_message
    """
    text = message.strip()
    lower = text.lower()
    result = {
        "event_type": "",
        "event_date": "",
        "city": "",
        "guest_count": 0,
        "name": "",
        "raw_message": text,
    }

    # Extract event type
    for etype, keywords in EVENT_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                result["event_type"] = etype
                break
        if result["event_type"]:
            break

    # Extract date
    for pattern in DATE_PATTERNS:
        match = re.search(pattern, lower, re.IGNORECASE)
        if match:
            result["event_date"] = match.group(0).strip()
            break

    # Extract guest count
    for pattern in GUEST_PATTERNS:
        match = re.search(pattern, lower, re.IGNORECASE)
        if match:
            try:
                result["guest_count"] = int(match.group(1))
            except (ValueError, IndexError):
                pass
            break

    # Extract city/location
    for pattern in CITY_PATTERNS:
        match = re.search(pattern, text)
        if match:
            city = match.group(1).strip()
            # Filter out common false positives
            if city.lower() not in ("the", "a", "an", "my", "our", "this", "that", "hi", "hello", "im", "we"):
                result["city"] = city
                break

    # Also check for known city names directly in the text
    if not result["city"]:
        from core.pricing import CITY_DISTANCES
        for city_name in sorted(CITY_DISTANCES.keys(), key=len, reverse=True):
            if city_name in lower:
                result["city"] = city_name.title()
                break

    # Try to extract a name (e.g., "Hi, I'm Sarah" or "My name is John")
    name_patterns = [
        r"(?:i'?m|my name is|this is|i am)\s+([A-Z][a-z]+)",
        r"(?:^|\n)([A-Z][a-z]+)\s+here",
        r"(?:thanks|thank you)[,.]?\s*([A-Z][a-z]+)",
        r"(?:—|--|–)\s*([A-Z][a-z]+)$",
    ]
    for pattern in name_patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            name = match.group(1).strip()
            if name.lower() not in ("the", "a", "an", "my", "our", "hi", "hello", "i"):
                result["name"] = name
                break

    return result


async def handle_forwarded_inquiry(bot, chat_id: str, message: str):
    """Handle a forwarded inquiry message from Kai.

    Parses the message, calculates a quote, and returns the result.
    If location is missing, asks for it.
    """
    parsed = parse_inquiry(message)

    # If no city found, ask Kai
    if not parsed["city"]:
        # Store the parsed data temporarily for when Kai replies with the city
        bot._pending_context[chat_id] = {
            "action": "instant_quote_city",
            "parsed": parsed,
        }
        response = (
            f"📍 I couldn't find a location in that message.\n\n"
            f"What I found:\n"
            f"• Event: {parsed['event_type'] or '(not specified)'}\n"
            f"• Date: {parsed['event_date'] or '(not specified)'}\n"
            f"• Guests: {parsed['guest_count'] or '(not specified)'}\n"
            f"• Name: {parsed['name'] or '(not found)'}\n\n"
            f"**What city is the event in?**"
        )
        await bot.send(chat_id, response)
        return

    # Generate the quote
    await _generate_and_send_quote(bot, chat_id, parsed)


async def handle_city_reply(bot, chat_id: str, city: str):
    """Handle Kai's reply with the city for an instant quote."""
    context = bot._pending_context.pop(chat_id, None)
    if not context or context.get("action") != "instant_quote_city":
        return False

    parsed = context["parsed"]
    parsed["city"] = city.strip()
    await _generate_and_send_quote(bot, chat_id, parsed)
    return True


async def _generate_and_send_quote(bot, chat_id: str, parsed: dict):
    """Generate and send the full quote response."""
    name = parsed.get("name", "") or "there"
    event_type = parsed.get("event_type", "")
    event_date = parsed.get("event_date", "")
    city = parsed.get("city", "")
    guest_count = parsed.get("guest_count", 0)

    quote_data, customer_msg = generate_quote_message(
        lead_name=name,
        event_type=event_type,
        event_date=event_date,
        event_city=city,
        guest_count=guest_count,
    )

    # Build the Telegram response for Kai
    response = (
        f"💰 INSTANT QUOTE READY\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 Name: {name}\n"
        f"🎉 Event: {event_type.title() or 'Not specified'}\n"
        f"📅 Date: {event_date or 'Not specified'}\n"
        f"📍 City: {city}\n"
        f"👥 Guests: {guest_count or 'Not specified'}\n"
        f"\n📊 PRICING:\n"
        f"{format_price_breakdown(quote_data)}\n"
        f"\n━━━━━━━━━━━━━━━━━━\n"
        f"📋 COPY-PASTE RESPONSE:\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"{customer_msg}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📌 FOLLOW-UP PLAN:\n"
        f"• If no response in 24hrs → send brief text about date availability\n"
        f"• If no response in 48hrs → final follow-up mentioning limited weekend dates\n"
        f"• After 72hrs → mark as cold, revisit in 2 weeks"
    )

    await bot.send(chat_id, response)
    log.info(f"Instant quote generated: {city} — ${quote_data['total']:,.0f}")
