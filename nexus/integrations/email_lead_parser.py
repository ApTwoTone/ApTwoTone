from __future__ import annotations
"""
Email Lead Parser — Auto-ingest leads from The Knot, WeddingWire, Facebook,
and general inquiry emails into the Nexus CRM.

- Polls Gmail IMAP every 5 minutes for new unread emails
- Classifies emails by source (The Knot, WeddingWire, Facebook, general)
- Extracts lead data: name, email, phone, event type, date, location
- Deduplicates against existing leads table
- Sends Telegram notification for every new lead (NO auto-responses)
- Tracks processed email UIDs to avoid re-processing
"""
import asyncio
import imaplib
import email as email_lib
import re
import sqlite3
import traceback
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

DB_PATH = Path.home() / ".nexus" / "memory.db"
TZ = ZoneInfo("America/Los_Angeles")

GMAIL_ADDRESS = "zoarbathrooms@gmail.com"
GMAIL_APP_PASSWORD = "kxhp ebhn cbbi kzhu"
IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
POLL_INTERVAL = 300  # 5 minutes

# SoCal cities for location extraction
SOCAL_CITIES = [
    "Los Angeles", "LA", "Malibu", "Santa Monica", "Pasadena", "Burbank",
    "Glendale", "Santa Clarita", "Valencia", "Simi Valley", "Thousand Oaks",
    "Ventura", "Oxnard", "Camarillo", "Calabasas", "Agoura Hills",
    "Woodland Hills", "Encino", "Sherman Oaks", "Studio City", "Toluca Lake",
    "Van Nuys", "North Hollywood", "Northridge", "Chatsworth", "Granada Hills",
    "Porter Ranch", "Tarzana", "Reseda", "Canoga Park", "West Hills",
    "San Fernando Valley", "SFV", "Westlake Village", "Moorpark",
    "Fillmore", "Santa Paula", "Ojai", "Carpinteria", "Santa Barbara",
    "Long Beach", "Torrance", "Redondo Beach", "Manhattan Beach",
    "Hermosa Beach", "Palos Verdes", "San Pedro", "Downey", "Whittier",
    "Pomona", "Azusa", "Monrovia", "Arcadia", "Alhambra", "Montebello",
    "Topanga", "Pacific Palisades", "Bel Air", "Beverly Hills",
    "West Hollywood", "Hollywood", "Silver Lake", "Echo Park",
    "Eagle Rock", "Highland Park", "Altadena", "La Canada",
    "San Dimas", "Claremont", "Upland", "Rancho Cucamonga",
    "Riverside", "Corona", "Temecula", "Murrieta",
    "Palmdale", "Lancaster", "Acton", "Agua Dulce",
    "Newhall", "Castaic", "Stevenson Ranch", "Canyon Country",
]

# Compile city pattern once (case-insensitive, word boundary)
_CITY_PATTERN = re.compile(
    r'\b(' + '|'.join(re.escape(c) for c in sorted(SOCAL_CITIES, key=len, reverse=True)) + r')\b',
    re.IGNORECASE,
)

# Event type keywords
EVENT_KEYWORDS = {
    "wedding": ["wedding", "bridal", "bride", "groom", "nuptial", "ceremony", "reception", "rehearsal dinner"],
    "corporate": ["corporate", "company", "business event", "conference", "team building", "retreat"],
    "birthday": ["birthday", "bday", "b-day"],
    "quinceanera": ["quincea\u00f1era", "quinceanera", "quince", "quincea"],
    "party": ["party", "celebration", "gathering", "bash", "fiesta"],
    "festival": ["festival", "fair", "carnival", "outdoor event"],
    "construction": ["construction", "job site", "work site", "building site"],
    "film": ["film", "production", "movie", "tv show", "shoot", "set", "filming"],
    "graduation": ["graduation", "grad party", "commencement"],
    "baby_shower": ["baby shower", "gender reveal"],
    "memorial": ["memorial", "funeral", "celebration of life"],
    "fundraiser": ["fundraiser", "charity", "gala", "benefit"],
}

# Phone number patterns (US formats)
_PHONE_RE = re.compile(
    r'(?:\+?1[\s.-]?)?'                       # optional +1 or 1
    r'(?:'
    r'\((\d{3})\)[\s.-]?(\d{3})[\s.-]?(\d{4})'  # (xxx) xxx-xxxx
    r'|'
    r'(\d{3})[\s.-](\d{3})[\s.-](\d{4})'         # xxx-xxx-xxxx / xxx.xxx.xxxx
    r'|'
    r'(\d{10})'                                   # xxxxxxxxxx
    r')'
)

# Email address pattern
_EMAIL_RE = re.compile(
    r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}',
)

# Date patterns
_DATE_PATTERNS = [
    # "March 15, 2026" or "March 15th, 2026"
    re.compile(
        r'\b(January|February|March|April|May|June|July|August|September|October|November|December)'
        r'\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})\b',
        re.IGNORECASE,
    ),
    # "March 15" or "March 15th" (no year — assume current or next year)
    re.compile(
        r'\b(January|February|March|April|May|June|July|August|September|October|November|December)'
        r'\s+(\d{1,2})(?:st|nd|rd|th)?\b',
        re.IGNORECASE,
    ),
    # "3/15/2026" or "03-15-2026"
    re.compile(r'\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})\b'),
    # "2026-03-15" (ISO)
    re.compile(r'\b(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})\b'),
]

_MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


# ── HTML Stripper ────────────────────────────────────────────────────────────

class _HTMLStripper(HTMLParser):
    """Strip HTML tags and return plain text."""

    def __init__(self):
        super().__init__()
        self._text: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head"):
            self._skip = True
        if tag == "br":
            self._text.append("\n")
        if tag in ("p", "div", "tr", "li"):
            self._text.append("\n")

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self._text.append(data)

    def get_text(self) -> str:
        return "".join(self._text)


def strip_html(html_text: str) -> str:
    """Convert HTML to plain text."""
    if not html_text:
        return ""
    try:
        stripper = _HTMLStripper()
        stripper.feed(html_text)
        text = stripper.get_text()
    except Exception:
        # Fallback: regex strip
        text = re.sub(r'<[^>]+>', ' ', html_text)
    # Normalize whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ── Smart Extraction Helpers ─────────────────────────────────────────────────

def extract_phone_numbers(text: str) -> list[str]:
    """Extract US phone numbers from text. Returns formatted (xxx) xxx-xxxx."""
    if not text:
        return []
    phones: list[str] = []
    for m in _PHONE_RE.finditer(text):
        groups = m.groups()
        if groups[0]:  # (xxx) xxx-xxxx format
            phones.append(f"({groups[0]}) {groups[1]}-{groups[2]}")
        elif groups[3]:  # xxx-xxx-xxxx format
            phones.append(f"({groups[3]}) {groups[4]}-{groups[5]}")
        elif groups[6]:  # xxxxxxxxxx format
            d = groups[6]
            phones.append(f"({d[:3]}) {d[3:6]}-{d[6:]}")
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for p in phones:
        digits = re.sub(r'\D', '', p)
        if digits not in seen:
            seen.add(digits)
            unique.append(p)
    return unique


def extract_dates(text: str) -> list[str]:
    """Extract date strings from text. Returns ISO format YYYY-MM-DD."""
    if not text:
        return []
    dates: list[str] = []
    now = datetime.now(TZ)

    # Pattern 0: "March 15, 2026" / "March 15th, 2026"
    for m in _DATE_PATTERNS[0].finditer(text):
        month_name, day, year = m.group(1), m.group(2), m.group(3)
        month = _MONTH_MAP.get(month_name.lower())
        if month:
            try:
                d = datetime(int(year), month, int(day))
                dates.append(d.strftime("%Y-%m-%d"))
            except ValueError:
                pass

    # Pattern 1: "March 15" (no year)
    for m in _DATE_PATTERNS[1].finditer(text):
        # Skip if this match is part of a pattern-0 match (has year after)
        end = m.end()
        after = text[end:end + 10].strip()
        if after and after[0:1].isdigit() and len(after) >= 4:
            continue
        month_name, day = m.group(1), m.group(2)
        month = _MONTH_MAP.get(month_name.lower())
        if month:
            year = now.year
            try:
                d = datetime(year, month, int(day))
                if d.date() < now.date():
                    d = datetime(year + 1, month, int(day))
                dates.append(d.strftime("%Y-%m-%d"))
            except ValueError:
                pass

    # Pattern 2: "3/15/2026" or "03-15-2026"
    for m in _DATE_PATTERNS[2].finditer(text):
        mm, dd, yyyy = m.group(1), m.group(2), m.group(3)
        try:
            d = datetime(int(yyyy), int(mm), int(dd))
            dates.append(d.strftime("%Y-%m-%d"))
        except ValueError:
            pass

    # Pattern 3: "2026-03-15" (ISO)
    for m in _DATE_PATTERNS[3].finditer(text):
        yyyy, mm, dd = m.group(1), m.group(2), m.group(3)
        if int(yyyy) > 2000:  # Sanity check — must be a real year
            try:
                d = datetime(int(yyyy), int(mm), int(dd))
                dates.append(d.strftime("%Y-%m-%d"))
            except ValueError:
                pass

    # Deduplicate
    seen: set[str] = set()
    unique: list[str] = []
    for dt in dates:
        if dt not in seen:
            seen.add(dt)
            unique.append(dt)
    return unique


def extract_email_addresses(text: str) -> list[str]:
    """Extract email addresses from text body."""
    if not text:
        return []
    found = _EMAIL_RE.findall(text)
    # Filter out known service/no-reply addresses
    skip = {"noreply", "no-reply", "mailer-daemon", "postmaster", "donotreply"}
    result: list[str] = []
    seen: set[str] = set()
    for e in found:
        lower = e.lower()
        local = lower.split("@")[0]
        if local not in skip and lower not in seen:
            seen.add(lower)
            result.append(lower)
    return result


def extract_event_type(text: str) -> str:
    """Detect event type from text via keyword matching. Returns type or empty string."""
    if not text:
        return ""
    lower = text.lower()
    for event_type, keywords in EVENT_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return event_type
    return ""


def extract_location(text: str) -> str:
    """Extract SoCal city/location from text."""
    if not text:
        return ""
    m = _CITY_PATTERN.search(text)
    return m.group(0) if m else ""


def extract_guest_count(text: str) -> Optional[int]:
    """Try to extract guest count from text."""
    if not text:
        return None
    patterns = [
        re.compile(r'(\d+)\s*(?:guests?|people|attendees?|pax)', re.IGNORECASE),
        re.compile(r'(?:guests?|people|attendees?|pax)\s*:?\s*(\d+)', re.IGNORECASE),
        re.compile(r'(?:expecting|about|approximately|around|~)\s*(\d+)\s*(?:guests?|people)?', re.IGNORECASE),
    ]
    for p in patterns:
        m = p.search(text)
        if m:
            count = int(m.group(1))
            if 5 <= count <= 10000:  # Sanity range
                return count
    return None


def _split_name(full_name: str) -> tuple[str, str]:
    """Split a full name into (first, last). Handles 'First Last' and 'First & Second Last'."""
    if not full_name:
        return ("", "")
    name = full_name.strip()
    # Handle "Sarah & John Smith" → first="Sarah & John", last="Smith"
    parts = name.split()
    if len(parts) >= 2:
        return (parts[0], " ".join(parts[1:]))
    return (parts[0], "")


# ── Email Source Parsers ─────────────────────────────────────────────────────

def parse_theknot_email(subject: str, sender: str, body: str) -> dict:
    """
    Parse a lead email from The Knot.
    The Knot lead emails typically have structured fields in the body.
    """
    data: dict = {
        "source": "the_knot",
        "event_type": "wedding",
        "first_name": "",
        "last_name": "",
        "email": "",
        "phone": "",
        "event_date": "",
        "location": "",
        "notes": "",
        "guest_count": None,
    }

    # Extract couple/contact name from body
    name_patterns = [
        re.compile(r'(?:Name|From|Contact|Couple)\s*:?\s*(.+)', re.IGNORECASE),
        re.compile(r'(?:Bride|Groom)\s*:?\s*(.+)', re.IGNORECASE),
        re.compile(r'(?:First Name|First)\s*:?\s*(\S+)', re.IGNORECASE),
    ]
    for p in name_patterns:
        m = p.search(body)
        if m:
            full = m.group(1).strip().split('\n')[0].strip()
            # Clean trailing punctuation / HTML artifacts
            full = re.sub(r'[<\r].*', '', full).strip()
            if full:
                data["first_name"], data["last_name"] = _split_name(full)
                break

    # Try last name separately if we got first from a separate field
    if data["first_name"] and not data["last_name"]:
        m = re.search(r'(?:Last Name|Last)\s*:?\s*(\S+)', body, re.IGNORECASE)
        if m:
            data["last_name"] = m.group(1).strip()

    # Extract email from body (the lead's email, not theknot's)
    emails = extract_email_addresses(body)
    # Filter out theknot.com addresses
    lead_emails = [e for e in emails if "theknot.com" not in e and "tkmail" not in e]
    if lead_emails:
        data["email"] = lead_emails[0]

    # Phone
    phones = extract_phone_numbers(body)
    if phones:
        data["phone"] = phones[0]

    # Wedding date
    # Look for explicit date field first
    date_field = re.search(r'(?:Wedding Date|Event Date|Date)\s*:?\s*(.+)', body, re.IGNORECASE)
    if date_field:
        dates = extract_dates(date_field.group(1))
        if dates:
            data["event_date"] = dates[0]
    if not data["event_date"]:
        dates = extract_dates(body)
        if dates:
            data["event_date"] = dates[0]

    # Location / venue
    venue_match = re.search(r'(?:Venue|Location|Where|City)\s*:?\s*(.+)', body, re.IGNORECASE)
    if venue_match:
        venue = venue_match.group(1).strip().split('\n')[0].strip()
        venue = re.sub(r'[<\r].*', '', venue).strip()
        data["location"] = venue
    if not data["location"]:
        data["location"] = extract_location(body)

    # Guest count
    gc_match = re.search(r'(?:Guest Count|Guests|Number of Guests|Estimated Guests)\s*:?\s*(\d+)', body, re.IGNORECASE)
    if gc_match:
        data["guest_count"] = int(gc_match.group(1))
    elif not data["guest_count"]:
        data["guest_count"] = extract_guest_count(body)

    # Message / notes
    msg_match = re.search(r'(?:Message|Comments?|Additional Info|Notes?|Details?)\s*:?\s*(.+?)(?:\n\n|\Z)', body, re.IGNORECASE | re.DOTALL)
    if msg_match:
        data["notes"] = msg_match.group(1).strip()[:500]

    return data


def parse_weddingwire_email(subject: str, sender: str, body: str) -> dict:
    """
    Parse a lead email from WeddingWire.
    WeddingWire emails are similar in structure to The Knot.
    """
    data: dict = {
        "source": "weddingwire",
        "event_type": "wedding",
        "first_name": "",
        "last_name": "",
        "email": "",
        "phone": "",
        "event_date": "",
        "location": "",
        "notes": "",
        "guest_count": None,
    }

    # Name extraction
    name_patterns = [
        re.compile(r'(?:Name|From|Contact|Couple)\s*:?\s*(.+)', re.IGNORECASE),
        re.compile(r'(?:Bride|Groom)\s*:?\s*(.+)', re.IGNORECASE),
        re.compile(r'(?:First Name)\s*:?\s*(\S+)', re.IGNORECASE),
    ]
    for p in name_patterns:
        m = p.search(body)
        if m:
            full = m.group(1).strip().split('\n')[0].strip()
            full = re.sub(r'[<\r].*', '', full).strip()
            if full:
                data["first_name"], data["last_name"] = _split_name(full)
                break

    if data["first_name"] and not data["last_name"]:
        m = re.search(r'(?:Last Name|Last)\s*:?\s*(\S+)', body, re.IGNORECASE)
        if m:
            data["last_name"] = m.group(1).strip()

    # Email
    emails = extract_email_addresses(body)
    lead_emails = [e for e in emails if "weddingwire.com" not in e and "weddingwire" not in e]
    if lead_emails:
        data["email"] = lead_emails[0]

    # Phone
    phones = extract_phone_numbers(body)
    if phones:
        data["phone"] = phones[0]

    # Date
    date_field = re.search(r'(?:Wedding Date|Event Date|Date)\s*:?\s*(.+)', body, re.IGNORECASE)
    if date_field:
        dates = extract_dates(date_field.group(1))
        if dates:
            data["event_date"] = dates[0]
    if not data["event_date"]:
        dates = extract_dates(body)
        if dates:
            data["event_date"] = dates[0]

    # Location
    venue_match = re.search(r'(?:Venue|Location|Where|City)\s*:?\s*(.+)', body, re.IGNORECASE)
    if venue_match:
        venue = venue_match.group(1).strip().split('\n')[0].strip()
        venue = re.sub(r'[<\r].*', '', venue).strip()
        data["location"] = venue
    if not data["location"]:
        data["location"] = extract_location(body)

    # Guest count
    gc_match = re.search(r'(?:Guest Count|Guests|Number of Guests|Estimated Guests)\s*:?\s*(\d+)', body, re.IGNORECASE)
    if gc_match:
        data["guest_count"] = int(gc_match.group(1))
    else:
        data["guest_count"] = extract_guest_count(body)

    # Message
    msg_match = re.search(r'(?:Message|Comments?|Additional Info|Notes?|Details?)\s*:?\s*(.+?)(?:\n\n|\Z)', body, re.IGNORECASE | re.DOTALL)
    if msg_match:
        data["notes"] = msg_match.group(1).strip()[:500]

    return data


def parse_fb_notification(subject: str, sender: str, body: str) -> dict:
    """Parse a Facebook notification email (message or inquiry)."""
    data: dict = {
        "source": "facebook_email",
        "event_type": "",
        "first_name": "",
        "last_name": "",
        "email": "",
        "phone": "",
        "event_date": "",
        "location": "",
        "notes": "",
        "guest_count": None,
    }

    # Extract sender name from the email subject or body
    # Subjects like "John Smith sent you a message" or "You have a new message from John Smith"
    name_patterns = [
        re.compile(r'(.+?)\s+sent you a message', re.IGNORECASE),
        re.compile(r'new message from\s+(.+?)(?:\s*$|\s*\.)', re.IGNORECASE),
        re.compile(r'(.+?)\s+(?:commented|replied|posted)', re.IGNORECASE),
    ]
    for p in name_patterns:
        m = p.search(subject)
        if m:
            full = m.group(1).strip()
            if full and len(full) < 60:
                data["first_name"], data["last_name"] = _split_name(full)
                break

    # If no name from subject, try body
    if not data["first_name"]:
        m = re.search(r'(?:From|Sender|Name)\s*:?\s*(.+)', body, re.IGNORECASE)
        if m:
            full = m.group(1).strip().split('\n')[0].strip()
            full = re.sub(r'[<\r].*', '', full).strip()
            if full:
                data["first_name"], data["last_name"] = _split_name(full)

    # Phone from body
    phones = extract_phone_numbers(body)
    if phones:
        data["phone"] = phones[0]

    # Email from body
    emails = extract_email_addresses(body)
    lead_emails = [e for e in emails if "facebook" not in e and "facebookmail" not in e]
    if lead_emails:
        data["email"] = lead_emails[0]

    # Event type, date, location from body
    data["event_type"] = extract_event_type(body)
    dates = extract_dates(body)
    if dates:
        data["event_date"] = dates[0]
    data["location"] = extract_location(body)

    # Notes — the message body itself
    # Strip common FB email boilerplate
    clean_body = body
    for boilerplate in [
        "This message was sent to",
        "To reply to this message",
        "Go to Facebook",
        "This email was sent to",
    ]:
        idx = clean_body.find(boilerplate)
        if idx > 0:
            clean_body = clean_body[:idx]
    data["notes"] = clean_body.strip()[:500]

    return data


def parse_general_inquiry(subject: str, sender: str, body: str) -> dict:
    """Parse a general inquiry email to zoarbathrooms@gmail.com."""
    data: dict = {
        "source": "email_inquiry",
        "event_type": "",
        "first_name": "",
        "last_name": "",
        "email": "",
        "phone": "",
        "event_date": "",
        "location": "",
        "notes": "",
        "guest_count": None,
    }

    # Get name from sender display name
    display_name, from_email = parseaddr(sender)
    if display_name:
        data["first_name"], data["last_name"] = _split_name(display_name)
    elif from_email:
        # Try to derive name from email prefix (e.g., sarah.johnson@ → Sarah Johnson)
        local = from_email.split("@")[0]
        parts = re.split(r'[._\-+]', local)
        if len(parts) >= 2:
            data["first_name"] = parts[0].capitalize()
            data["last_name"] = parts[1].capitalize()
        elif parts:
            data["first_name"] = parts[0].capitalize()

    data["email"] = from_email.lower() if from_email else ""

    # Phone from body
    phones = extract_phone_numbers(body)
    if phones:
        data["phone"] = phones[0]

    # Also check if phone is in the subject
    if not data["phone"]:
        phones = extract_phone_numbers(subject)
        if phones:
            data["phone"] = phones[0]

    # Event type
    data["event_type"] = extract_event_type(subject + " " + body)

    # Date
    dates = extract_dates(body)
    if dates:
        data["event_date"] = dates[0]

    # Location
    data["location"] = extract_location(body)

    # Guest count
    data["guest_count"] = extract_guest_count(body)

    # Notes — use subject + body excerpt
    note_parts = []
    if subject:
        note_parts.append(f"Subject: {subject}")
    if body:
        note_parts.append(body.strip()[:400])
    data["notes"] = "\n".join(note_parts)[:500]

    return data


# ── Email Classification ────────────────────────────────────────────────────

def classify_email(subject: str, sender: str, body: str) -> str:
    """
    Determine which parser to use based on sender and subject.
    Returns: "theknot", "weddingwire", "facebook", "general", or "skip".
    """
    sender_lower = sender.lower()
    subject_lower = subject.lower()

    # Skip known non-lead emails
    skip_senders = [
        "noreply@google.com", "no-reply@accounts.google.com",
        "mailer-daemon@", "postmaster@",
        "security-noreply@", "calendar-notification@",
    ]
    for s in skip_senders:
        if s in sender_lower:
            return "skip"

    skip_subjects = [
        "password reset", "security alert", "sign-in",
        "verify your email", "confirm your account",
        "invoice", "receipt", "payment confirmed",
        "delivery notification", "shipping",
    ]
    for s in skip_subjects:
        if s in subject_lower:
            return "skip"

    # The Knot
    if "theknot.com" in sender_lower or "the knot" in sender_lower:
        return "theknot"
    if "the knot" in subject_lower and ("lead" in subject_lower or "inquiry" in subject_lower):
        return "theknot"

    # WeddingWire
    if "weddingwire.com" in sender_lower or "weddingwire" in sender_lower:
        return "weddingwire"
    if "weddingwire" in subject_lower and ("lead" in subject_lower or "inquiry" in subject_lower):
        return "weddingwire"

    # Facebook
    if "facebookmail.com" in sender_lower or "facebook.com" in sender_lower:
        return "facebook"

    # General inquiry — anything else that reaches the inbox
    return "general"


def parse_email(subject: str, sender: str, body: str) -> Optional[dict]:
    """
    Route to the correct parser based on classification.
    Returns parsed lead data dict, or None if email should be skipped.
    """
    source = classify_email(subject, sender, body)

    if source == "skip":
        return None
    elif source == "theknot":
        return parse_theknot_email(subject, sender, body)
    elif source == "weddingwire":
        return parse_weddingwire_email(subject, sender, body)
    elif source == "facebook":
        return parse_fb_notification(subject, sender, body)
    else:
        return parse_general_inquiry(subject, sender, body)


# ── Database ─────────────────────────────────────────────────────────────────

def init_email_parser_db() -> None:
    """Create the processed_emails tracking table."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS processed_emails (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email_uid TEXT NOT NULL,
        subject TEXT DEFAULT '',
        sender TEXT DEFAULT '',
        processed_at TEXT DEFAULT (datetime('now')),
        lead_id_created INTEGER DEFAULT NULL,
        classification TEXT DEFAULT '',
        was_duplicate INTEGER DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_processed_uid ON processed_emails(email_uid);
    """)
    conn.commit()
    conn.close()
    print("[EmailParser] Database table ready")


def _is_uid_processed(uid: str) -> bool:
    """Check if an email UID has already been processed."""
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        "SELECT id FROM processed_emails WHERE email_uid = ?", (uid,)
    ).fetchone()
    conn.close()
    return row is not None


def _record_processed(uid: str, subject: str, sender: str,
                      lead_id: Optional[int], classification: str,
                      was_duplicate: bool) -> None:
    """Record that an email UID has been processed."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "INSERT INTO processed_emails (email_uid, subject, sender, lead_id_created, "
        "classification, was_duplicate) VALUES (?, ?, ?, ?, ?, ?)",
        (uid, subject[:200], sender[:200], lead_id, classification, int(was_duplicate)),
    )
    conn.commit()
    conn.close()


# ── Lead Creation with Dedup ────────────────────────────────────────────────

def create_lead_from_email(parsed: dict) -> dict:
    """
    Insert a new lead into the leads table, or add notes to existing lead if duplicate.
    Returns {"lead_id": int, "is_new": bool, "name": str}.
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    lead_email = (parsed.get("email") or "").lower().strip()
    lead_phone = parsed.get("phone", "")
    phone_digits = re.sub(r'\D', '', lead_phone)
    if phone_digits.startswith("1") and len(phone_digits) == 11:
        phone_digits = phone_digits[1:]

    existing_id: Optional[int] = None

    # Check email dedup
    if lead_email:
        row = conn.execute(
            "SELECT id FROM leads WHERE lower(email) = ?", (lead_email,)
        ).fetchone()
        if row:
            existing_id = row["id"]

    # Check phone dedup
    if not existing_id and phone_digits and len(phone_digits) == 10:
        rows = conn.execute("SELECT id, phone FROM leads").fetchall()
        for r in rows:
            existing_digits = re.sub(r'\D', '', r["phone"] or "")
            if existing_digits.startswith("1") and len(existing_digits) == 11:
                existing_digits = existing_digits[1:]
            if existing_digits == phone_digits:
                existing_id = r["id"]
                break

    first = parsed.get("first_name", "")
    last = parsed.get("last_name", "")
    name = f"{first} {last}".strip() or "Unknown"
    now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

    if existing_id:
        # Add note to existing lead instead of creating duplicate
        note_addition = (
            f"\n[EmailParser {now_str}] Duplicate inquiry from {parsed.get('source', 'email')}. "
            f"Message: {parsed.get('notes', '')[:200]}"
        )
        conn.execute(
            "UPDATE leads SET notes = notes || ?, updated_at = ? WHERE id = ?",
            (note_addition, now_str, existing_id),
        )
        conn.commit()
        conn.close()
        print(f"[EmailParser] Duplicate lead — added note to existing #{existing_id}")
        return {"lead_id": existing_id, "is_new": False, "name": name}

    # Create new lead
    notes = parsed.get("notes", "")
    guest_count = parsed.get("guest_count")
    if guest_count:
        notes = f"Guests: ~{guest_count}\n{notes}"

    cur = conn.execute(
        "INSERT INTO leads (first_name, last_name, email, phone, source, status, notes, "
        "discovered_at, updated_at) VALUES (?, ?, ?, ?, ?, 'new', ?, ?, ?)",
        (
            first, last, lead_email, lead_phone,
            parsed.get("source", "email_inquiry"),
            notes[:1000],
            now_str, now_str,
        ),
    )
    lead_id = cur.lastrowid
    conn.commit()

    # Log the event
    conn.execute(
        "INSERT INTO lead_events (lead_id, event_type, details) VALUES (?, ?, ?)",
        (lead_id, "new_lead", f"Auto-ingested from {parsed.get('source', 'email')} via EmailParser"),
    )
    conn.commit()
    conn.close()

    print(f"[EmailParser] New lead created: #{lead_id} — {name} ({parsed.get('source')})")
    return {"lead_id": lead_id, "is_new": True, "name": name}


# ── IMAP Connection ─────────────────────────────────────────────────────────

def connect_imap() -> imaplib.IMAP4_SSL:
    """Establish IMAP connection to Gmail. Raises on failure."""
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    print("[EmailParser] IMAP connected")
    return mail


def _decode_header_value(header_val) -> str:
    """Decode a possibly-encoded email header."""
    if not header_val:
        return ""
    parts = decode_header(header_val)
    result: list[str] = []
    for data, charset in parts:
        if isinstance(data, bytes):
            result.append(data.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(str(data))
    return " ".join(result)


def _extract_body(msg) -> tuple[str, str]:
    """
    Extract email body as plain text.
    Returns (plain_text, html_text) — prefers plain, falls back to HTML stripped.
    """
    plain = ""
    html = ""

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if "attachment" in cd:
                continue
            try:
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                charset = part.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
            except Exception:
                continue

            if ct == "text/plain" and not plain:
                plain = text
            elif ct == "text/html" and not html:
                html = text
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
                if msg.get_content_type() == "text/html":
                    html = text
                else:
                    plain = text
        except Exception:
            pass

    # Use plain text if available; otherwise strip HTML
    if plain:
        return plain, html
    if html:
        return strip_html(html), html
    return "", ""


def fetch_new_emails() -> list[dict]:
    """
    Connect to IMAP, fetch unread emails not yet processed.
    Returns list of dicts with: uid, subject, sender, body, timestamp, raw_html.
    """
    results: list[dict] = []
    mail: Optional[imaplib.IMAP4_SSL] = None

    try:
        mail = connect_imap()
        mail.select("INBOX")

        # Search for unseen messages
        status, data = mail.search(None, "UNSEEN")
        if status != "OK" or not data[0]:
            print("[EmailParser] No unread emails")
            return []

        uids_raw = data[0].split()
        print(f"[EmailParser] Found {len(uids_raw)} unread emails")

        # Process up to 50 at a time to avoid timeout
        for num in uids_raw[-50:]:
            uid_str = num.decode() if isinstance(num, bytes) else str(num)

            # Check if already processed
            if _is_uid_processed(uid_str):
                continue

            try:
                status, msg_data = mail.fetch(num, "(RFC822)")
                if status != "OK":
                    continue

                msg = email_lib.message_from_bytes(msg_data[0][1])

                # Decode headers
                subject = _decode_header_value(msg.get("Subject", ""))
                sender = msg.get("From", "")
                _, sender_email = parseaddr(sender)

                # Parse timestamp
                date_str = msg.get("Date", "")
                try:
                    dt = parsedate_to_datetime(date_str)
                    timestamp = dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    timestamp = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

                # Extract body
                plain_body, html_body = _extract_body(msg)

                results.append({
                    "uid": uid_str,
                    "subject": subject,
                    "sender": sender,
                    "sender_email": sender_email.lower() if sender_email else "",
                    "body": plain_body[:5000],
                    "raw_html": html_body[:10000] if html_body else "",
                    "timestamp": timestamp,
                    "msg_num": num,
                })

            except Exception as e:
                print(f"[EmailParser] Error reading email {uid_str}: {e}")
                continue

        # Mark processed emails as read
        for item in results:
            try:
                mail.store(item["msg_num"], '+FLAGS', '\\Seen')
            except Exception:
                pass

    except imaplib.IMAP4.error as e:
        print(f"[EmailParser] IMAP error: {e}")
    except Exception as e:
        print(f"[EmailParser] Fetch error: {e}")
        traceback.print_exc()
    finally:
        if mail:
            try:
                mail.logout()
            except Exception:
                pass

    return results


# ── Telegram Notification Formatting ─────────────────────────────────────────

def _format_lead_notification(parsed: dict, result: dict) -> str:
    """Format a Telegram notification for a new email lead."""
    source_display = {
        "the_knot": "The Knot",
        "weddingwire": "WeddingWire",
        "facebook_email": "Facebook",
        "email_inquiry": "Email Inquiry",
    }
    source = source_display.get(parsed.get("source", ""), parsed.get("source", "Unknown"))
    name = result.get("name", "Unknown")
    email = parsed.get("email", "—")
    phone = parsed.get("phone", "—")

    # Event info
    event_type = parsed.get("event_type", "")
    event_date = parsed.get("event_date", "")
    if event_type and event_date:
        event_line = f"{event_type.replace('_', ' ').title()} — {event_date}"
    elif event_type:
        event_line = event_type.replace("_", " ").title()
    elif event_date:
        event_line = event_date
    else:
        event_line = "—"

    location = parsed.get("location", "—") or "—"
    lead_id = result.get("lead_id", "?")

    # Build message
    lines = [
        "\U0001F4E7 *NEW EMAIL LEAD*",
        "\u2501" * 30,
        f"*Source:* {source}",
        f"*Name:* {name}",
        f"*Email:* {email}",
        f"*Phone:* {phone}",
        f"*Event:* {event_line}",
        f"*Location:* {location}",
    ]

    guest_count = parsed.get("guest_count")
    if guest_count:
        lines.append(f"*Guests:* ~{guest_count}")

    # Original message excerpt
    notes = parsed.get("notes", "")
    if notes:
        # Truncate to reasonable length for Telegram
        excerpt = notes[:300].replace("*", "").replace("_", "")
        if len(notes) > 300:
            excerpt += "..."
        lines.append(f"\n_Original message:_\n\"{excerpt}\"")

    if result.get("is_new"):
        lines.append(f"\n\u2705 Lead created in CRM (#{lead_id})")
    else:
        lines.append(f"\n\U0001F504 Duplicate — added note to existing lead (#{lead_id})")

    return "\n".join(lines)


# ── Stats ────────────────────────────────────────────────────────────────────

def get_email_parser_stats() -> dict:
    """Get email parser statistics."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) as c FROM processed_emails").fetchone()["c"]

    # By source/classification
    by_source = conn.execute(
        "SELECT classification, COUNT(*) as c FROM processed_emails "
        "WHERE classification != 'skip' GROUP BY classification ORDER BY c DESC"
    ).fetchall()

    # Today's count
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    today_count = conn.execute(
        "SELECT COUNT(*) as c FROM processed_emails WHERE processed_at >= ?",
        (today,),
    ).fetchone()["c"]

    # Leads created
    leads_created = conn.execute(
        "SELECT COUNT(*) as c FROM processed_emails WHERE lead_id_created IS NOT NULL AND was_duplicate = 0"
    ).fetchone()["c"]

    # Duplicates caught
    duplicates = conn.execute(
        "SELECT COUNT(*) as c FROM processed_emails WHERE was_duplicate = 1"
    ).fetchone()["c"]

    # Skipped
    skipped = conn.execute(
        "SELECT COUNT(*) as c FROM processed_emails WHERE classification = 'skip'"
    ).fetchone()["c"]

    conn.close()

    return {
        "total_processed": total,
        "by_source": {r["classification"]: r["c"] for r in by_source},
        "today": today_count,
        "leads_created": leads_created,
        "duplicates_caught": duplicates,
        "skipped": skipped,
    }


def format_parser_stats() -> str:
    """Format stats for Telegram /emailstats command."""
    stats = get_email_parser_stats()

    source_display = {
        "theknot": "The Knot",
        "weddingwire": "WeddingWire",
        "facebook": "Facebook",
        "general": "General Inquiry",
    }

    lines = [
        "\U0001F4E7 *Email Parser Stats*",
        "\u2501" * 30,
        f"*Total Processed:* {stats['total_processed']}",
        f"*Leads Created:* {stats['leads_created']}",
        f"*Duplicates Caught:* {stats['duplicates_caught']}",
        f"*Skipped (non-lead):* {stats['skipped']}",
        f"*Today:* {stats['today']}",
    ]

    if stats["by_source"]:
        lines.append("\n*By Source:*")
        for src, count in stats["by_source"].items():
            display = source_display.get(src, src)
            lines.append(f"  \u2022 {display}: {count}")

    return "\n".join(lines)


# ── Main Polling Loop ────────────────────────────────────────────────────────

async def run_email_parser(send_fn) -> None:
    """
    Async loop: every 5 minutes, fetch new emails, parse, create leads, notify.

    send_fn: async def(message: str) — sends Telegram notification to Kai.
    """
    print(f"[EmailParser] Starting email parser (poll every {POLL_INTERVAL}s)")
    init_email_parser_db()

    while True:
        try:
            # Run IMAP fetch in executor to avoid blocking
            loop = asyncio.get_running_loop()
            emails = await loop.run_in_executor(None, fetch_new_emails)

            new_leads = 0
            duplicates = 0

            for email_data in emails:
                uid = email_data["uid"]
                subject = email_data["subject"]
                sender = email_data["sender"]
                body = email_data["body"]

                # Classify
                classification = classify_email(subject, sender, body)

                if classification == "skip":
                    _record_processed(uid, subject, sender, None, "skip", False)
                    print(f"[EmailParser] Skipped non-lead: {subject[:60]}")
                    continue

                # Parse
                try:
                    parsed = parse_email(subject, sender, body)
                except Exception as e:
                    print(f"[EmailParser] Parse error for '{subject[:60]}': {e}")
                    _record_processed(uid, subject, sender, None, classification, False)
                    continue

                if not parsed:
                    _record_processed(uid, subject, sender, None, classification, False)
                    continue

                # For general inquiries, ensure email is populated from sender
                if not parsed.get("email") and email_data.get("sender_email"):
                    parsed["email"] = email_data["sender_email"]

                # Require at least a name or email to create a lead
                has_identity = (
                    parsed.get("first_name")
                    or parsed.get("email")
                    or parsed.get("phone")
                )
                if not has_identity:
                    print(f"[EmailParser] No identity info found, skipping: {subject[:60]}")
                    _record_processed(uid, subject, sender, None, classification, False)
                    continue

                # Create lead (with dedup)
                try:
                    result = create_lead_from_email(parsed)
                except Exception as e:
                    print(f"[EmailParser] Lead creation error: {e}")
                    traceback.print_exc()
                    _record_processed(uid, subject, sender, None, classification, False)
                    continue

                is_new = result.get("is_new", False)
                lead_id = result.get("lead_id")

                _record_processed(uid, subject, sender, lead_id, classification, not is_new)

                if is_new:
                    new_leads += 1
                else:
                    duplicates += 1

                # Send Telegram notification
                if send_fn:
                    try:
                        notification = _format_lead_notification(parsed, result)
                        await send_fn(notification)
                    except Exception as e:
                        print(f"[EmailParser] Notification error: {e}")

                # Trigger lead pipeline for new leads (queues for Kai's approval)
                if is_new and lead_id:
                    try:
                        from core.lead_pipeline import get_pipeline
                        pipeline = get_pipeline()
                        if pipeline:
                            await pipeline.process_new_lead(lead_id)
                    except Exception as e:
                        print(f"[EmailParser] Pipeline trigger error: {e}")

            if emails:
                print(
                    f"[EmailParser] Processed {len(emails)} emails — "
                    f"{new_leads} new leads, {duplicates} duplicates"
                )

        except Exception as e:
            print(f"[EmailParser] Poll cycle error: {e}")
            traceback.print_exc()

        await asyncio.sleep(POLL_INTERVAL)


# ── Module-level init ────────────────────────────────────────────────────────

_parser_task: Optional[asyncio.Task] = None


def start_email_parser(send_fn) -> asyncio.Task:
    """Start the email parser as a background asyncio task."""
    global _parser_task
    init_email_parser_db()
    _parser_task = asyncio.create_task(run_email_parser(send_fn))
    print("[EmailParser] Background task started")
    return _parser_task


def stop_email_parser() -> None:
    """Stop the email parser background task."""
    global _parser_task
    if _parser_task and not _parser_task.done():
        _parser_task.cancel()
        print("[EmailParser] Background task stopped")
    _parser_task = None
