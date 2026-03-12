"""
B2B Lead Service — CRUD operations, duplicate detection, status management.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path.home() / ".nexus" / "memory.db"


def _conn(db_path=None):
    c = sqlite3.connect(str(db_path or DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def get_lead(lead_id: int, db_path=None) -> dict | None:
    conn = _conn(db_path)
    row = conn.execute("SELECT * FROM b2b_leads WHERE id = ?", (lead_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_leads(db_path=None, **filters) -> list[dict]:
    """Get leads with optional filters: tier, status, category, rating, city."""
    conn = _conn(db_path)
    query = "SELECT * FROM b2b_leads WHERE 1=1"
    params = []

    if "tier" in filters and filters["tier"]:
        query += " AND pricing_tier = ?"
        params.append(int(filters["tier"]))
    if "status" in filters and filters["status"]:
        query += " AND email_status = ?"
        params.append(filters["status"])
    if "category" in filters and filters["category"]:
        query += " AND category = ?"
        params.append(filters["category"])
    if "rating" in filters and filters["rating"]:
        query += " AND rating = ?"
        params.append(filters["rating"])
    if "city" in filters and filters["city"]:
        query += " AND city = ?"
        params.append(filters["city"])
    if "section" in filters and filters["section"]:
        query += " AND section = ?"
        params.append(filters["section"])

    query += " ORDER BY lead_number ASC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def search_leads(term: str, db_path=None) -> list[dict]:
    """Search by business name or city (case-insensitive)."""
    conn = _conn(db_path)
    like = f"%{term}%"
    rows = conn.execute(
        "SELECT * FROM b2b_leads WHERE business_name LIKE ? OR city LIKE ? OR category LIKE ? ORDER BY lead_number",
        (like, like, like)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_lead(lead_id: int, updates: dict, db_path=None) -> bool:
    """Update specific fields on a lead."""
    allowed = {
        "contact_name", "email", "phone", "website", "rating", "notes",
        "email_status", "email_sent_at", "email_content", "reply_status",
        "reply_content", "reply_received_at", "outcome",
        "telegram_approval_status", "telegram_message_id",
        "do_not_contact", "is_duplicate",
    }
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        return False

    filtered["updated_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    sets = ", ".join(f"{k} = ?" for k in filtered)
    vals = list(filtered.values()) + [lead_id]

    conn = _conn(db_path)
    conn.execute(f"UPDATE b2b_leads SET {sets} WHERE id = ?", vals)
    conn.commit()
    conn.close()
    return True


def mark_sent(lead_id: int, email_content: str, db_path=None):
    """Mark a lead's email as sent."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn = _conn(db_path)
    conn.execute(
        "UPDATE b2b_leads SET email_status = 'sent', email_sent_at = ?, "
        "email_content = ?, telegram_approval_status = 'approved', "
        "outcome = 'in_sequence', updated_at = ? WHERE id = ?",
        (now, email_content, now, lead_id)
    )
    conn.commit()
    conn.close()


def mark_denied(lead_id: int, db_path=None):
    """Mark a lead as denied (email not sent)."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn = _conn(db_path)
    conn.execute(
        "UPDATE b2b_leads SET email_status = 'not_sent', "
        "telegram_approval_status = 'denied', updated_at = ? WHERE id = ?",
        (now, lead_id)
    )
    conn.commit()
    conn.close()


def mark_replied(lead_id: int, reply_content: str, db_path=None):
    """Mark a lead as having replied."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn = _conn(db_path)
    conn.execute(
        "UPDATE b2b_leads SET email_status = 'replied', reply_status = 'positive', "
        "reply_content = ?, reply_received_at = ?, outcome = 'interested', updated_at = ? "
        "WHERE id = ?",
        (reply_content, now, now, lead_id)
    )
    conn.commit()
    conn.close()


def mark_unsubscribed(lead_id: int, db_path=None):
    """Mark a lead as unsubscribed — do_not_contact."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn = _conn(db_path)
    conn.execute(
        "UPDATE b2b_leads SET do_not_contact = 1, reply_status = 'unsubscribed', "
        "outcome = 'not_interested', updated_at = ? WHERE id = ?",
        (now, lead_id)
    )
    conn.commit()
    conn.close()


def is_email_ever_sent(email_address: str, db_path=None) -> bool:
    """Check if an email address has EVER been sent to."""
    if not email_address:
        return False
    conn = _conn(db_path)
    row = conn.execute(
        "SELECT id FROM b2b_leads WHERE email = ? AND email_sent_at IS NOT NULL",
        (email_address.lower().strip(),)
    ).fetchone()
    conn.close()
    return row is not None


def is_business_contacted(business_name: str, db_path=None) -> bool:
    """Check if a business has ever been contacted."""
    if not business_name:
        return False
    conn = _conn(db_path)
    row = conn.execute(
        "SELECT id FROM b2b_leads WHERE business_name = ? AND email_sent_at IS NOT NULL",
        (business_name,)
    ).fetchone()
    conn.close()
    return row is not None


def get_b2b_emails() -> set:
    """Get all known B2B lead email addresses (for IMAP reply matching)."""
    conn = _conn()
    rows = conn.execute(
        "SELECT DISTINCT lower(email) FROM b2b_leads WHERE email != '' AND email IS NOT NULL"
    ).fetchall()
    conn.close()
    return {r[0] for r in rows}


def find_lead_by_email(email_address: str, db_path=None) -> dict | None:
    """Find a B2B lead by email address."""
    if not email_address:
        return None
    conn = _conn(db_path)
    row = conn.execute(
        "SELECT * FROM b2b_leads WHERE lower(email) = ?",
        (email_address.lower().strip(),)
    ).fetchone()
    conn.close()
    return dict(row) if row else None
