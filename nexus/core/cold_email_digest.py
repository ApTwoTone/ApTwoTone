"""
B2B Cold Email Digest — Batch generator for Telegram approval workflow.

Selects next N eligible leads, generates personalized emails, queues for approval.
Prioritizes: Tier 1 first, then HOT > WARM > COOL.
Triple-checks no duplicates are sent.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path.home() / ".nexus" / "memory.db"

_RATING_ORDER = {"HOT": 1, "WARM": 2, "COOL": 3}


def generate_batch(batch_size: int = 10, db_path=None) -> list[dict]:
    """
    Select next batch of eligible leads and generate emails for each.
    Returns list of dicts with lead info + generated email content.

    Eligibility:
    - email_status = 'not_sent'
    - has a non-empty email address
    - not do_not_contact
    - not is_duplicate
    - email_sent_at IS NULL (never been emailed)
    """
    from core.cold_email import generate_cold_email

    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row

    # Select eligible leads, prioritized by tier then rating
    rows = conn.execute("""
        SELECT * FROM b2b_leads
        WHERE email_status = 'not_sent'
          AND email != ''
          AND do_not_contact = 0
          AND is_duplicate = 0
          AND email_sent_at IS NULL
        ORDER BY pricing_tier ASC,
                 CASE rating
                     WHEN 'HOT' THEN 1
                     WHEN 'WARM' THEN 2
                     WHEN 'COOL' THEN 3
                     ELSE 4
                 END ASC,
                 lead_number ASC
        LIMIT ?
    """, (batch_size,)).fetchall()

    batch = []
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    for row in rows:
        lead = dict(row)

        # Triple-check: has this email EVER been sent to?
        existing = conn.execute(
            "SELECT id FROM b2b_leads WHERE email = ? AND email_sent_at IS NOT NULL AND id != ?",
            (lead["email"], lead["id"])
        ).fetchone()
        if existing:
            # Mark as duplicate and skip
            conn.execute(
                "UPDATE b2b_leads SET is_duplicate = 1, updated_at = ? WHERE id = ?",
                (now, lead["id"])
            )
            continue

        # Generate category-specific email
        email_data = generate_cold_email(
            lead["business_name"],
            category=lead.get("category", ""),
            contact_name=lead.get("contact_name", ""),
        )
        if "error" in email_data:
            print(f"[B2B Digest] Skipping {lead['business_name']}: {email_data['error']}")
            continue

        # Update status to pending_approval
        conn.execute(
            "UPDATE b2b_leads SET email_status = 'pending_approval', "
            "email_content = ?, telegram_approval_status = 'pending', updated_at = ? "
            "WHERE id = ?",
            (email_data["plain_body"], now, lead["id"])
        )

        lead.update({
            "subject": email_data["subject"],
            "plain_body": email_data["plain_body"],
            "html_body": email_data["html_body"],
        })
        batch.append(lead)

    conn.commit()
    conn.close()

    print(f"[B2B Digest] Generated batch of {len(batch)} emails for approval")
    return batch


def get_batch_stats(db_path=None) -> dict:
    """Get summary stats for B2B outreach."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))

    total = conn.execute("SELECT COUNT(*) FROM b2b_leads").fetchone()[0]
    not_sent = conn.execute("SELECT COUNT(*) FROM b2b_leads WHERE email_status = 'not_sent'").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM b2b_leads WHERE email_status = 'pending_approval'").fetchone()[0]
    sent = conn.execute("SELECT COUNT(*) FROM b2b_leads WHERE email_status = 'sent'").fetchone()[0]
    replied = conn.execute("SELECT COUNT(*) FROM b2b_leads WHERE email_status = 'replied'").fetchone()[0]
    denied = conn.execute("SELECT COUNT(*) FROM b2b_leads WHERE telegram_approval_status = 'denied'").fetchone()[0]
    no_email = conn.execute("SELECT COUNT(*) FROM b2b_leads WHERE email = '' OR email IS NULL").fetchone()[0]
    do_not_contact = conn.execute("SELECT COUNT(*) FROM b2b_leads WHERE do_not_contact = 1").fetchone()[0]

    # Eligible for next batch
    eligible = conn.execute("""
        SELECT COUNT(*) FROM b2b_leads
        WHERE email_status = 'not_sent'
          AND email != ''
          AND do_not_contact = 0
          AND is_duplicate = 0
          AND email_sent_at IS NULL
    """).fetchone()[0]

    # By category
    categories = {}
    for row in conn.execute(
        "SELECT category, COUNT(*), SUM(CASE WHEN email_status='sent' THEN 1 ELSE 0 END) "
        "FROM b2b_leads GROUP BY category"
    ).fetchall():
        categories[row[0]] = {"total": row[1], "sent": row[2] or 0}

    conn.close()

    return {
        "total": total,
        "not_sent": not_sent,
        "pending_approval": pending,
        "sent": sent,
        "replied": replied,
        "denied": denied,
        "no_email": no_email,
        "do_not_contact": do_not_contact,
        "eligible_next_batch": eligible,
        "by_category": categories,
    }
