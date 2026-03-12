"""
Unified Conversation Manager for Zoar Bathroom Rentals.
Merges messages from Gmail, Google Voice, Facebook, and manual entries
into a single conversation timeline per contact.

Tables: lead_conversations, lead_conversation_messages
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

log = logging.getLogger("conversation_manager")
DB_PATH = Path.home() / ".nexus" / "memory.db"


def _conn():
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def match_contact_to_lead(
    email: Optional[str] = None,
    phone: Optional[str] = None,
) -> Optional[int]:
    """Search leads table for a matching contact. Returns lead_id or None."""
    conn = _conn()
    lead_id = None

    if email:
        row = conn.execute(
            "SELECT id FROM leads WHERE email = ? LIMIT 1", (email,)
        ).fetchone()
        if row:
            lead_id = row["id"]

    if not lead_id and phone:
        # Normalize: strip non-digits for comparison
        digits = "".join(c for c in phone if c.isdigit())
        if len(digits) >= 10:
            row = conn.execute(
                "SELECT id FROM leads WHERE REPLACE(REPLACE(REPLACE(phone, '-', ''), '(', ''), ')', '') LIKE ? LIMIT 1",
                (f"%{digits[-10:]}",)
            ).fetchone()
            if row:
                lead_id = row["id"]

    conn.close()
    return lead_id


def match_contact_to_vendor(
    email: Optional[str] = None,
    phone: Optional[str] = None,
) -> Optional[int]:
    """Search vendors table for a matching contact. Returns vendor_id or None."""
    conn = _conn()
    vendor_id = None

    if email:
        row = conn.execute(
            "SELECT id FROM vendors WHERE email = ? LIMIT 1", (email,)
        ).fetchone()
        if row:
            vendor_id = row["id"]

    if not vendor_id and phone:
        digits = "".join(c for c in phone if c.isdigit())
        if len(digits) >= 10:
            row = conn.execute(
                "SELECT id FROM vendors WHERE REPLACE(REPLACE(REPLACE(phone, '-', ''), '(', ''), ')', '') LIKE ? LIMIT 1",
                (f"%{digits[-10:]}",)
            ).fetchone()
            if row:
                vendor_id = row["id"]

    conn.close()
    return vendor_id


def get_or_create_conversation(
    lead_id: Optional[int] = None,
    vendor_id: Optional[int] = None,
    contact_email: Optional[str] = None,
    contact_phone: Optional[str] = None,
    contact_name: Optional[str] = None,
    channel: str = "email",
) -> Dict[str, Any]:
    """Find existing conversation or create a new one for this contact."""
    conn = _conn()

    # Try to find existing conversation
    if lead_id:
        row = conn.execute(
            "SELECT * FROM lead_conversations WHERE lead_id = ? LIMIT 1",
            (lead_id,)
        ).fetchone()
        if row:
            conn.close()
            return dict(row)

    if vendor_id:
        row = conn.execute(
            "SELECT * FROM lead_conversations WHERE vendor_id = ? LIMIT 1",
            (vendor_id,)
        ).fetchone()
        if row:
            conn.close()
            return dict(row)

    if contact_email:
        row = conn.execute(
            "SELECT * FROM lead_conversations WHERE contact_email = ? LIMIT 1",
            (contact_email,)
        ).fetchone()
        if row:
            conn.close()
            return dict(row)

    if contact_phone:
        row = conn.execute(
            "SELECT * FROM lead_conversations WHERE contact_phone = ? LIMIT 1",
            (contact_phone,)
        ).fetchone()
        if row:
            conn.close()
            return dict(row)

    # Auto-detect lead_id / vendor_id if not provided
    if not lead_id:
        lead_id = match_contact_to_lead(contact_email, contact_phone)
    if not vendor_id:
        vendor_id = match_contact_to_vendor(contact_email, contact_phone)

    # Create new conversation
    now = datetime.utcnow().isoformat()
    cursor = conn.execute(
        """INSERT INTO lead_conversations
           (lead_id, vendor_id, contact_name, contact_phone, contact_email,
            channel_types, status, lead_stage, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 'active', 'new', ?, ?)""",
        (lead_id, vendor_id, contact_name or "", contact_phone or "",
         contact_email or "", channel, now, now)
    )
    conn.commit()
    conv_id = cursor.lastrowid
    row = conn.execute("SELECT * FROM lead_conversations WHERE id = ?", (conv_id,)).fetchone()
    conn.close()
    log.info("Created conversation id=%d for %s", conv_id, contact_name or contact_email or contact_phone)
    return dict(row)


def add_message(
    conversation_id: int,
    direction: str,
    channel: str,
    body_plain: str,
    message_timestamp: str,
    source_message_id: Optional[str] = None,
    source_account: Optional[str] = None,
    sender: Optional[str] = None,
    recipient: Optional[str] = None,
    subject: Optional[str] = None,
    body_html: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Add a message to a conversation. Deduplicates by source_message_id."""
    conn = _conn()

    # Deduplicate
    if source_message_id:
        existing = conn.execute(
            "SELECT id FROM lead_conversation_messages WHERE source_message_id = ?",
            (source_message_id,)
        ).fetchone()
        if existing:
            conn.close()
            return None  # Already ingested

    cursor = conn.execute(
        """INSERT INTO lead_conversation_messages
           (conversation_id, direction, channel, sender, recipient, subject,
            body_plain, body_html, source_account, source_message_id,
            message_timestamp)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (conversation_id, direction, channel, sender or "", recipient or "",
         subject or "", body_plain, body_html or "", source_account or "",
         source_message_id or "", message_timestamp)
    )
    msg_id = cursor.lastrowid

    # Update conversation counters
    updates = ["message_count = message_count + 1", "last_message_at = ?",
               "updated_at = datetime('now')"]
    params = [message_timestamp]

    if direction == "inbound":
        updates.append("inbound_count = inbound_count + 1")
        updates.append("last_inbound_at = ?")
        updates.append("unread_count = unread_count + 1")
        params.append(message_timestamp)
    elif direction == "outbound":
        updates.append("outbound_count = outbound_count + 1")
        updates.append("last_outbound_at = ?")
        params.append(message_timestamp)

    # Update channel_types if new channel
    conv = conn.execute(
        "SELECT channel_types FROM lead_conversations WHERE id = ?",
        (conversation_id,)
    ).fetchone()
    if conv:
        existing_channels = set(conv["channel_types"].split(",")) if conv["channel_types"] else set()
        if channel not in existing_channels:
            existing_channels.add(channel)
            updates.append("channel_types = ?")
            params.append(",".join(sorted(existing_channels)))

    params.append(conversation_id)
    conn.execute(
        f"UPDATE lead_conversations SET {', '.join(updates)} WHERE id = ?",
        params
    )
    conn.commit()

    row = conn.execute("SELECT * FROM lead_conversation_messages WHERE id = ?", (msg_id,)).fetchone()
    conn.close()
    return dict(row)


def get_conversations(
    lead_id: Optional[int] = None,
    vendor_id: Optional[int] = None,
    status: Optional[str] = None,
    needs_follow_up: Optional[bool] = None,
    lead_stage: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    """List conversations with optional filters."""
    conn = _conn()
    where, params = [], []

    if lead_id:
        where.append("lead_id = ?")
        params.append(lead_id)
    if vendor_id:
        where.append("vendor_id = ?")
        params.append(vendor_id)
    if status:
        where.append("status = ?")
        params.append(status)
    if needs_follow_up is not None:
        where.append("needs_follow_up = ?")
        params.append(1 if needs_follow_up else 0)
    if lead_stage:
        where.append("lead_stage = ?")
        params.append(lead_stage)

    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) as cnt FROM lead_conversations {clause}", params).fetchone()["cnt"]
    rows = conn.execute(
        f"SELECT * FROM lead_conversations {clause} ORDER BY last_message_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset]
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows], total


def get_messages(
    conversation_id: int,
    limit: int = 100,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    """Get messages for a conversation, sorted chronologically."""
    conn = _conn()
    total = conn.execute(
        "SELECT COUNT(*) as cnt FROM lead_conversation_messages WHERE conversation_id = ?",
        (conversation_id,)
    ).fetchone()["cnt"]
    rows = conn.execute(
        "SELECT * FROM lead_conversation_messages WHERE conversation_id = ? "
        "ORDER BY message_timestamp ASC LIMIT ? OFFSET ?",
        (conversation_id, limit, offset)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows], total


def mark_read(conversation_id: int) -> None:
    """Mark all messages in a conversation as read."""
    conn = _conn()
    now = datetime.utcnow().isoformat()
    conn.execute(
        "UPDATE lead_conversation_messages SET read_at = ? "
        "WHERE conversation_id = ? AND read_at = ''",
        (now, conversation_id)
    )
    conn.execute(
        "UPDATE lead_conversations SET unread_count = 0 WHERE id = ?",
        (conversation_id,)
    )
    conn.commit()
    conn.close()
