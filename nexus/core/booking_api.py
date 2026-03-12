"""
Booking API — FastAPI endpoints for booking management.

Replaces the 4 inline booking endpoints in server.py (lines 7906-7951).

Usage in server.py:
    from core.booking_api import register_booking_routes
    register_booking_routes(app, verify_action_auth=verify_action_auth)
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, Request, Depends
from fastapi.responses import JSONResponse

log = logging.getLogger("booking_api")


def register_booking_routes(app: FastAPI, verify_action_auth=None):
    """Register all booking API endpoints."""

    # ── GET /api/bookings — list bookings with filters ──────────────────────

    @app.get("/api/bookings")
    async def api_list_bookings(
        status: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ):
        from core.booking_db import get_bookings
        bookings, total = get_bookings(status, date_from, date_to, source, limit, offset)
        return JSONResponse({"bookings": bookings, "total": total, "limit": limit, "offset": offset})

    # ── GET /api/bookings/stats — dashboard stats ──────────────────────────

    @app.get("/api/bookings/stats")
    async def api_booking_stats():
        from core.booking_db import get_booking_stats
        return JSONResponse(get_booking_stats())

    # ── GET /api/bookings/availability — check date ────────────────────────

    @app.get("/api/bookings/availability")
    async def api_check_availability(date: str = ""):
        if not date:
            return JSONResponse({"error": "date parameter required"}, status_code=400)
        from core.booking_db import check_date_available
        return JSONResponse(check_date_available(date))

    # ── GET /api/bookings/pipeline — pipeline view ─────────────────────────

    @app.get("/api/bookings/pipeline")
    async def api_booking_pipeline():
        from core.booking_db import get_bookings, get_booking_stats
        stats = get_booking_stats()
        inquiry, _ = get_bookings(status="inquiry")
        quoted, _ = get_bookings(status="quoted")
        confirmed, _ = get_bookings(status="confirmed")
        deposit_paid, _ = get_bookings(status="deposit_paid")
        return JSONResponse({
            "pipeline": {
                "inquiry": inquiry,
                "quoted": quoted,
                "confirmed": confirmed,
                "deposit_paid": deposit_paid,
            },
            "stats": stats,
        })

    # ── GET /api/bookings/calendar — combined calendar view ────────────────

    @app.get("/api/bookings/calendar")
    async def api_booking_calendar(
        month: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ):
        from core.booking_db import get_bookings
        from core.calendar_sync import get_calendar_events

        # Default to current month if no params
        if month and not date_from:
            date_from = f"{month}-01"
            # Approximate month end
            parts = month.split("-")
            year, mon = int(parts[0]), int(parts[1])
            if mon == 12:
                date_to = f"{year + 1}-01-01"
            else:
                date_to = f"{year}-{mon + 1:02d}-01"

        bookings, _ = get_bookings(date_from=date_from, date_to=date_to, limit=200)
        cal_events = get_calendar_events(date_from=date_from, date_to=date_to)

        return JSONResponse({
            "bookings": bookings,
            "calendar_events": cal_events,
            "date_from": date_from,
            "date_to": date_to,
        })

    # ── GET /api/bookings/{booking_id} — single booking ────────────────────

    @app.get("/api/bookings/{booking_id}")
    async def api_get_booking(booking_id: int):
        from core.booking_db import get_booking
        booking = get_booking(booking_id)
        if not booking:
            return JSONResponse({"error": "Booking not found"}, status_code=404)
        return JSONResponse({"booking": booking})

    # ── POST /api/bookings — create booking ────────────────────────────────

    write_deps = [Depends(verify_action_auth)] if verify_action_auth else []

    @app.post("/api/bookings", dependencies=write_deps)
    async def api_create_booking(request: Request):
        body = await request.json()
        if not body.get("event_date"):
            return JSONResponse({"error": "event_date is required"}, status_code=400)

        from core.booking_db import create_booking
        booking = create_booking(body)
        return JSONResponse({"ok": True, "booking": booking})

    # ── PUT /api/bookings/{booking_id} — update booking ────────────────────

    @app.put("/api/bookings/{booking_id}", dependencies=write_deps)
    async def api_update_booking(booking_id: int, request: Request):
        body = await request.json()
        from core.booking_db import update_booking
        booking = update_booking(booking_id, body)
        if not booking:
            return JSONResponse({"error": "Booking not found"}, status_code=404)
        return JSONResponse({"ok": True, "booking": booking})

    # ── POST /api/bookings/{booking_id}/calendar-sync — sync to calendar ───

    @app.post("/api/bookings/{booking_id}/calendar-sync", dependencies=write_deps)
    async def api_sync_booking_calendar(booking_id: int):
        from core.calendar_sync import create_booking_event
        result = create_booking_event(booking_id)
        return JSONResponse(result)
