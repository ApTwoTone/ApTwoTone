"""
Email Queue API — FastAPI endpoints for morning email review system.

Usage in server.py:
    from core.email_queue_api import register_email_queue_routes
    register_email_queue_routes(app, verify_action_auth=verify_action_auth)
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, Request, Depends
from fastapi.responses import JSONResponse

log = logging.getLogger("email_queue_api")


def register_email_queue_routes(app: FastAPI, verify_action_auth=None):
    """Register all email queue API endpoints."""

    write_deps = [Depends(verify_action_auth)] if verify_action_auth else []

    # ── GET /api/email-queue — list queued emails ──────────────────────────

    @app.get("/api/email-queue")
    async def api_list_email_queue(
        scheduled_date: Optional[str] = None,
        status: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ):
        from core.email_queue_manager import get_queue
        emails, total = get_queue(scheduled_date, status, category, limit, offset)
        return JSONResponse({"emails": emails, "total": total, "limit": limit, "offset": offset})

    # ── GET /api/email-queue/stats — batch summary ─────────────────────────

    @app.get("/api/email-queue/stats")
    async def api_email_queue_stats(scheduled_date: Optional[str] = None):
        from core.email_queue_manager import get_queue_stats
        return JSONResponse(get_queue_stats(scheduled_date))

    # ── GET /api/email-queue/{queue_id} — single email ─────────────────────

    @app.get("/api/email-queue/{queue_id}")
    async def api_get_queue_email(queue_id: int):
        from core.email_queue_manager import get_queue_email
        email = get_queue_email(queue_id)
        if not email:
            return JSONResponse({"error": "Email not found"}, status_code=404)
        return JSONResponse({"email": email})

    # ── POST /api/email-queue/generate — generate tomorrow's batch ─────────

    @app.post("/api/email-queue/generate", dependencies=write_deps)
    async def api_generate_queue(request: Request):
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        scheduled_date = body.get("scheduled_date")
        count = body.get("count", 30)

        from core.email_queue_manager import generate_queue
        result = generate_queue(scheduled_date, count)
        return JSONResponse(result)

    # ── POST /api/email-queue/{queue_id}/approve — approve single ──────────

    @app.post("/api/email-queue/{queue_id}/approve", dependencies=write_deps)
    async def api_approve_email(queue_id: int):
        from core.email_queue_manager import approve_email
        result = approve_email(queue_id)
        if not result:
            return JSONResponse({"error": "Email not found"}, status_code=404)
        return JSONResponse({"ok": True, "email": result})

    # ── POST /api/email-queue/approve-all — bulk approve ───────────────────

    @app.post("/api/email-queue/approve-all", dependencies=write_deps)
    async def api_approve_all(request: Request):
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        scheduled_date = body.get("scheduled_date")

        from core.email_queue_manager import approve_all
        count = approve_all(scheduled_date)
        return JSONResponse({"ok": True, "approved": count})

    # ── PUT /api/email-queue/{queue_id} — edit draft ───────────────────────

    @app.put("/api/email-queue/{queue_id}", dependencies=write_deps)
    async def api_edit_email(queue_id: int, request: Request):
        body = await request.json()
        from core.email_queue_manager import edit_email
        result = edit_email(
            queue_id,
            subject=body.get("subject"),
            body_plain=body.get("body_plain"),
            body_html=body.get("body_html"),
            editor=body.get("editor", "kai"),
        )
        if not result:
            return JSONResponse({"error": "Email not found"}, status_code=404)
        return JSONResponse({"ok": True, "email": result})

    # ── DELETE /api/email-queue/{queue_id} — remove from batch ─────────────

    @app.delete("/api/email-queue/{queue_id}", dependencies=write_deps)
    async def api_remove_email(queue_id: int):
        from core.email_queue_manager import remove_email
        result = remove_email(queue_id)
        return JSONResponse(result)

    # ── POST /api/email-queue/{queue_id}/restore — undo removal ────────────

    @app.post("/api/email-queue/{queue_id}/restore", dependencies=write_deps)
    async def api_restore_email(queue_id: int):
        from core.email_queue_manager import restore_email
        result = restore_email(queue_id)
        return JSONResponse(result)

    # ── POST /api/email-queue/send — send all approved ─────────────────────

    @app.post("/api/email-queue/send", dependencies=write_deps)
    async def api_send_queue(request: Request):
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        scheduled_date = body.get("scheduled_date")
        max_count = body.get("max_count", 30)

        from core.email_queue_manager import send_approved
        result = send_approved(scheduled_date, max_count)
        return JSONResponse(result)
