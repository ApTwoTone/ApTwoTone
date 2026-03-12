"""
Contact Enrichment — Smart name resolution for vendor/email leads.

Prevents sending "Hey Chris" just because the email is chris@example.com
when the actual owner might be someone else entirely.

Strategies (all free, no paid APIs):
1. Extract name from email prefix (conservative — only obvious patterns)
2. Cross-reference with existing vendor data (website scrape results)
3. Flag low-confidence names for manual review
4. Use business name as fallback instead of guessing
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".nexus" / "memory.db"

# Common email prefixes that are NOT personal names
GENERIC_PREFIXES = {
    "info", "contact", "hello", "hi", "support", "help", "admin",
    "sales", "booking", "bookings", "events", "event", "office",
    "team", "staff", "general", "mail", "email", "noreply", "no-reply",
    "do-not-reply", "enquiries", "inquiries", "service", "services",
    "billing", "accounts", "management", "marketing", "reception",
    "reservations", "catering", "weddings", "planning", "coordinator",
    "rentals", "rental", "orders", "feedback", "pr", "media",
}

# Patterns that look like "firstname.lastname" or "firstnamelastname"
NAME_EMAIL_RE = re.compile(r"^([a-z]+)[._]([a-z]+)$", re.IGNORECASE)

# Single word that could be a first name (3-15 chars, no digits)
SINGLE_NAME_RE = re.compile(r"^([a-z]{3,15})$", re.IGNORECASE)


def extract_name_from_email(email: str) -> dict:
    """Try to extract a name from an email address.

    Returns:
        {
            "first_name": str or "",
            "last_name": str or "",
            "confidence": "high" | "medium" | "low" | "none",
            "source": "email_pattern",
            "note": str  # explanation
        }
    """
    if not email or "@" not in email:
        return {"first_name": "", "last_name": "", "confidence": "none",
                "source": "email_pattern", "note": "No email provided"}

    prefix = email.split("@")[0].lower().strip()

    # Remove common numeric suffixes (e.g., john123)
    clean_prefix = re.sub(r"\d+$", "", prefix)

    # Check if it's a generic/business prefix
    if clean_prefix in GENERIC_PREFIXES:
        return {"first_name": "", "last_name": "", "confidence": "none",
                "source": "email_pattern",
                "note": f"Generic prefix: {clean_prefix}"}

    # Try firstname.lastname or firstname_lastname pattern
    match = NAME_EMAIL_RE.match(clean_prefix)
    if match:
        first = match.group(1).capitalize()
        last = match.group(2).capitalize()
        # Validate they look like actual names (not random strings)
        if len(first) >= 2 and len(last) >= 2:
            return {"first_name": first, "last_name": last,
                    "confidence": "medium", "source": "email_pattern",
                    "note": f"Extracted from email pattern: {prefix}"}

    # Single name prefix — very low confidence, might be wrong
    if SINGLE_NAME_RE.match(clean_prefix) and clean_prefix not in GENERIC_PREFIXES:
        return {"first_name": clean_prefix.capitalize(), "last_name": "",
                "confidence": "low", "source": "email_pattern",
                "note": f"Single name from email: {clean_prefix} — may not match actual contact"}

    return {"first_name": "", "last_name": "", "confidence": "none",
            "source": "email_pattern",
            "note": f"Could not extract name from: {prefix}"}


def get_verified_contact_name(vendor_id: int, db_path=None) -> dict:
    """Look up vendor's verified contact name from enrichment data.

    Returns the most reliable name available:
    1. Manually verified name (highest confidence)
    2. Website-scraped owner name (medium confidence)
    3. Business name as fallback (always safe)
    """
    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT name, contact_name, contact_name_verified, "
        "contact_name_confidence, contact_name_source, "
        "business_owner_name, email "
        "FROM vendors WHERE id = ?", (vendor_id,)
    ).fetchone()
    conn.close()

    if not row:
        return {"name": "", "confidence": "none", "source": "not_found"}

    vendor = dict(row)

    # Priority 1: Manually verified name
    if vendor.get("contact_name_verified") and vendor.get("contact_name"):
        return {"name": vendor["contact_name"], "confidence": "high",
                "source": "manual_verified"}

    # Priority 2: Business owner name from enrichment
    if vendor.get("business_owner_name"):
        return {"name": vendor["business_owner_name"],
                "confidence": vendor.get("contact_name_confidence", "medium"),
                "source": vendor.get("contact_name_source", "enrichment")}

    # Priority 3: Contact name from website scraping
    if vendor.get("contact_name"):
        return {"name": vendor["contact_name"],
                "confidence": vendor.get("contact_name_confidence", "low"),
                "source": vendor.get("contact_name_source", "website_scrape")}

    # Priority 4: Try extracting from email (lowest confidence)
    if vendor.get("email"):
        email_result = extract_name_from_email(vendor["email"])
        if email_result["confidence"] in ("medium", "high"):
            return {"name": email_result["first_name"],
                    "confidence": "low",
                    "source": "email_guess"}

    # Fallback: Use business name
    return {"name": vendor.get("name", ""), "confidence": "business_name",
            "source": "business_name_fallback"}


def get_safe_greeting_name(vendor_id: int, db_path=None) -> str:
    """Get the safest name to use in outreach greetings.

    Rules:
    - High/medium confidence: Use the name ("Hi Sarah,")
    - Low confidence: Use business name ("Hi [Business Name] team,")
    - No name: Use generic ("Hi there,")

    This prevents embarrassing misgendering or wrong-name situations.
    """
    result = get_verified_contact_name(vendor_id, db_path)

    if result["confidence"] in ("high", "medium", "manual_verified"):
        return result["name"]
    elif result["confidence"] == "business_name":
        name = result["name"]
        if name:
            return f"{name} team"
    return "there"


def update_vendor_enrichment(vendor_id: int, contact_name: str,
                              source: str = "manual",
                              confidence: str = "high",
                              verified: bool = False,
                              db_path=None):
    """Update a vendor's contact name with enrichment data."""
    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path)
    conn.execute(
        "UPDATE vendors SET contact_name=?, contact_name_source=?, "
        "contact_name_confidence=?, contact_name_verified=? WHERE id=?",
        (contact_name, source, confidence, int(verified), vendor_id)
    )
    conn.commit()
    conn.close()
