"""
Conversation API — FastAPI endpoints for unified conversation timeline.

Usage in server.py:
    from core.conversation_api import register_conversation_routes
    register_conversation_routes(app, verify_action_auth=verify_action_auth)
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, Request, Depends
from fastapi.responses import JSONResponse

log = logging.getLogger("conversation_api")


def register_conversation_routes(app: FastAPI, verify_action_auth=None):
    """Register all conversation API endpoints."""

    # ── GET /api/conversations — list conversations ────────────────────────

    @app.get("/api/conversations")
    async def api_list_conversations(
        lead_id: Optional[int] = None,
        vendor_id: Optional[int] = None,
        status: Optional[str] = None,
        lead_stage: Optional[str] = None,
        needs_follow_up: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ):
        from core.conversation_manager import get_conversations
        follow_up = None
        if needs_follow_up is not None:
            follow_up = bool(needs_follow_up)
        convs, total = get_conversations(
            lead_id=lead_id, vendor_id=vendor_id, status=status,
            needs_follow_up=follow_up, lead_stage=lead_stage,
            limit=limit, offset=offset,
        )
        return JSONResponse({"conversations": convs, "total": total, "limit": limit, "offset": offset})

    # ── GET /api/conversations/{conv_id} — single conversation ─────────────

    @app.get("/api/conversations/{conv_id}")
    async def api_get_conversation(conv_id: int):
        from core.conversation_manager import get_conversations, get_messages
        import sqlite3
        from pathlib import Path

        db = sqlite3.connect(str(Path.home() / ".nexus" / "memory.db"))
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM lead_conversations WHERE id = ?", (conv_id,)).fetchone()
        db.close()

        if not row:
            return JSONResponse({"error": "Conversation not found"}, status_code=404)

        messages, msg_total = get_messages(conv_id, limit=50)
        return JSONResponse({
            "conversation": dict(row),
            "messages": messages,
            "message_count": msg_total,
        })

    # ── GET /api/conversations/{conv_id}/messages — paginated messages ─────

    @app.get("/api/conversations/{conv_id}/messages")
    async def api_get_messages(
        conv_id: int,
        page: int = 1,
        per_page: int = 50,
    ):
        from core.conversation_manager import get_messages
        offset = (page - 1) * per_page
        messages, total = get_messages(conv_id, limit=per_page, offset=offset)
        return JSONResponse({
            "messages": messages,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page if per_page > 0 else 0,
        })

    # ── POST /api/conversations/{conv_id}/messages — add manual message ────

    write_deps = [Depends(verify_action_auth)] if verify_action_auth else []

    @app.post("/api/conversations/{conv_id}/messages", dependencies=write_deps)
    async def api_add_message(conv_id: int, request: Request):
        body = await request.json()
        direction = body.get("direction", "outbound")
        channel = body.get("channel", "phone_call")
        body_plain = body.get("body", body.get("body_plain", ""))
        timestamp = body.get("message_timestamp", body.get("timestamp", ""))

        if not body_plain:
            return JSONResponse({"error": "body is required"}, status_code=400)
        if not timestamp:
            from datetime import datetime
            timestamp = datetime.utcnow().isoformat()

        from core.conversation_manager import add_message
        msg = add_message(
            conversation_id=conv_id,
            direction=direction,
            channel=channel,
            body_plain=body_plain,
            message_timestamp=timestamp,
            sender=body.get("sender"),
            recipient=body.get("recipient"),
            subject=body.get("subject"),
        )
        if msg:
            return JSONResponse({"ok": True, "message": msg})
        return JSONResponse({"ok": True, "message": None, "note": "duplicate"})

    # ── POST /api/conversations/ingest — trigger manual email ingest ───────

    @app.post("/api/conversations/ingest", dependencies=write_deps)
    async def api_trigger_ingest():
        from core.gmail_ingester import ingest_recent_emails
        result = ingest_recent_emails()
        return JSONResponse(result)

    # ── POST /api/conversations/{conv_id}/read — mark as read ──────────────

    @app.post("/api/conversations/{conv_id}/read", dependencies=write_deps)
    async def api_mark_read(conv_id: int):
        from core.conversation_manager import mark_read
        mark_read(conv_id)
        return JSONResponse({"ok": True})
