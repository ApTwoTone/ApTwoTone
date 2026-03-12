"""
Referral & Event Leads API — FastAPI endpoints for Facebook-sourced leads.

Coder_2 writes data into referral_leads and event_leads tables.
This module provides the read/update API for the frontend (Coder_3).

Usage in server.py:
    from core.referral_api import register_referral_routes
    register_referral_routes(app, verify_action_auth=verify_action_auth)
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Depends
from fastapi.responses import JSONResponse

log = logging.getLogger("referral_api")
DB_PATH = Path.home() / ".nexus" / "memory.db"


def _conn():
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def register_referral_routes(app: FastAPI, verify_action_auth=None):
    """Register referral lead and event lead API endpoints."""

    write_deps = [Depends(verify_action_auth)] if verify_action_auth else []

    # ══════════════════════════════════════════════════════════════════════════
    # REFERRAL LEADS
    # ══════════════════════════════════════════════════════════════════════════

    @app.get("/api/referral-leads")
    async def api_list_referral_leads(
        job_title: Optional[str] = None,
        city: Optional[str] = None,
        in_sfv: Optional[int] = None,
        outreach_status: Optional[str] = None,
        min_score: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ):
        conn = _conn()
        where, params = [], []

        if job_title:
            where.append("job_title = ?")
            params.append(job_title)
        if city:
            where.append("city LIKE ?")
            params.append(f"%{city}%")
        if in_sfv is not None:
            where.append("in_sfv = ?")
            params.append(in_sfv)
        if outreach_status:
            where.append("outreach_status = ?")
            params.append(outreach_status)
        if min_score is not None:
            where.append("qualification_score >= ?")
            params.append(min_score)

        clause = ("WHERE " + " AND ".join(where)) if where else ""
        total = conn.execute(f"SELECT COUNT(*) as cnt FROM referral_leads {clause}", params).fetchone()["cnt"]
        rows = conn.execute(
            f"SELECT * FROM referral_leads {clause} ORDER BY qualification_score DESC, id DESC LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()
        conn.close()
        return JSONResponse({"referral_leads": [dict(r) for r in rows], "total": total})

    @app.get("/api/referral-leads/stats")
    async def api_referral_stats():
        conn = _conn()
        total = conn.execute("SELECT COUNT(*) as cnt FROM referral_leads").fetchone()["cnt"]

        job_rows = conn.execute(
            "SELECT job_title, COUNT(*) as cnt FROM referral_leads WHERE job_title != '' GROUP BY job_title ORDER BY cnt DESC"
        ).fetchall()
        by_job = {r["job_title"]: r["cnt"] for r in job_rows}

        status_rows = conn.execute(
            "SELECT outreach_status, COUNT(*) as cnt FROM referral_leads GROUP BY outreach_status"
        ).fetchall()
        by_status = {r["outreach_status"]: r["cnt"] for r in status_rows}

        in_sfv = conn.execute("SELECT COUNT(*) as cnt FROM referral_leads WHERE in_sfv = 1").fetchone()["cnt"]
        conn.close()

        return JSONResponse({"total": total, "by_job": by_job, "by_status": by_status, "in_sfv": in_sfv})

    @app.get("/api/referral-leads/{lead_id}")
    async def api_get_referral_lead(lead_id: int):
        conn = _conn()
        row = conn.execute("SELECT * FROM referral_leads WHERE id = ?", (lead_id,)).fetchone()
        conn.close()
        if not row:
            return JSONResponse({"error": "Referral lead not found"}, status_code=404)
        return JSONResponse({"referral_lead": dict(row)})

    @app.post("/api/referral-leads", dependencies=write_deps)
    async def api_create_referral_lead(request: Request):
        body = await request.json()
        if not body.get("name"):
            return JSONResponse({"error": "name is required"}, status_code=400)

        conn = _conn()
        cols = [
            "name", "first_name", "username", "phone", "email", "website",
            "website_works", "profile_url", "job_title", "business_name",
            "location", "city", "state", "in_sfv", "in_la", "in_service_area",
            "facebook_group", "facebook_group_url", "post_text", "post_date",
            "post_url", "is_actively_promoting", "qualification_score",
            "qualification_reason", "outreach_status", "notes", "source",
        ]
        insert_cols, insert_vals = [], []
        for col in cols:
            if col in body:
                insert_cols.append(col)
                insert_vals.append(body[col])

        placeholders = ", ".join(["?"] * len(insert_cols))
        cursor = conn.execute(
            f"INSERT INTO referral_leads ({', '.join(insert_cols)}) VALUES ({placeholders})",
            insert_vals,
        )
        conn.commit()
        row = conn.execute("SELECT * FROM referral_leads WHERE id = ?", (cursor.lastrowid,)).fetchone()
        conn.close()
        return JSONResponse({"ok": True, "referral_lead": dict(row)})

    @app.put("/api/referral-leads/{lead_id}", dependencies=write_deps)
    async def api_update_referral_lead(lead_id: int, request: Request):
        body = await request.json()
        conn = _conn()

        allowed = {
            "outreach_status", "outreach_channel", "notes", "qualification_score",
            "qualification_reason", "referral_fee_offered", "phone", "email",
        }
        sets, vals = [], []
        for k, v in body.items():
            if k in allowed:
                sets.append(f"{k} = ?")
                vals.append(v)
        if not sets:
            conn.close()
            return JSONResponse({"error": "No valid fields to update"}, status_code=400)

        sets.append("updated_at = datetime('now')")
        vals.append(lead_id)
        conn.execute(f"UPDATE referral_leads SET {', '.join(sets)} WHERE id = ?", vals)
        conn.commit()
        row = conn.execute("SELECT * FROM referral_leads WHERE id = ?", (lead_id,)).fetchone()
        conn.close()
        if not row:
            return JSONResponse({"error": "Referral lead not found"}, status_code=404)
        return JSONResponse({"ok": True, "referral_lead": dict(row)})

    # ══════════════════════════════════════════════════════════════════════════
    # EVENT LEADS
    # ══════════════════════════════════════════════════════════════════════════

    @app.get("/api/event-leads")
    async def api_list_event_leads(
        event_type: Optional[str] = None,
        event_city: Optional[str] = None,
        outreach_status: Optional[str] = None,
        min_score: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ):
        conn = _conn()
        where, params = [], []

        if event_type:
            where.append("event_type = ?")
            params.append(event_type)
        if event_city:
            where.append("event_city LIKE ?")
            params.append(f"%{event_city}%")
        if outreach_status:
            where.append("outreach_status = ?")
            params.append(outreach_status)
        if min_score is not None:
            where.append("restroom_need_score >= ?")
            params.append(min_score)

        clause = ("WHERE " + " AND ".join(where)) if where else ""
        total = conn.execute(f"SELECT COUNT(*) as cnt FROM event_leads {clause}", params).fetchone()["cnt"]
        rows = conn.execute(
            f"SELECT * FROM event_leads {clause} ORDER BY event_date ASC, restroom_need_score DESC LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()
        conn.close()
        return JSONResponse({"event_leads": [dict(r) for r in rows], "total": total})

    @app.get("/api/event-leads/stats")
    async def api_event_lead_stats():
        conn = _conn()
        total = conn.execute("SELECT COUNT(*) as cnt FROM event_leads").fetchone()["cnt"]
        upcoming = conn.execute(
            "SELECT COUNT(*) as cnt FROM event_leads WHERE event_date >= date('now')"
        ).fetchone()["cnt"]

        type_rows = conn.execute(
            "SELECT event_type, COUNT(*) as cnt FROM event_leads WHERE event_type != '' GROUP BY event_type ORDER BY cnt DESC"
        ).fetchall()
        by_type = {r["event_type"]: r["cnt"] for r in type_rows}

        status_rows = conn.execute(
            "SELECT outreach_status, COUNT(*) as cnt FROM event_leads GROUP BY outreach_status"
        ).fetchall()
        by_status = {r["outreach_status"]: r["cnt"] for r in status_rows}

        conn.close()
        return JSONResponse({"total": total, "upcoming": upcoming, "by_type": by_type, "by_status": by_status})

    @app.get("/api/event-leads/{lead_id}")
    async def api_get_event_lead(lead_id: int):
        conn = _conn()
        row = conn.execute("SELECT * FROM event_leads WHERE id = ?", (lead_id,)).fetchone()
        conn.close()
        if not row:
            return JSONResponse({"error": "Event lead not found"}, status_code=404)
        return JSONResponse({"event_lead": dict(row)})

    @app.post("/api/event-leads", dependencies=write_deps)
    async def api_create_event_lead(request: Request):
        body = await request.json()
        if not body.get("event_name"):
            return JSONResponse({"error": "event_name is required"}, status_code=400)

        conn = _conn()
        cols = [
            "event_name", "organizer_name", "organizer_phone", "organizer_email",
            "organizer_website", "event_date", "event_time", "event_location",
            "event_city", "event_address", "event_type", "expected_attendance",
            "vendor_spots_available", "vendor_fee", "is_outdoor",
            "restroom_need_score", "restroom_need_reason", "facebook_group",
            "facebook_group_url", "post_text", "post_url", "post_date",
            "poster_name", "poster_profile_url", "event_page_url",
            "signup_url", "notes", "source",
        ]
        insert_cols, insert_vals = [], []
        for col in cols:
            if col in body:
                insert_cols.append(col)
                insert_vals.append(body[col])

        placeholders = ", ".join(["?"] * len(insert_cols))
        cursor = conn.execute(
            f"INSERT INTO event_leads ({', '.join(insert_cols)}) VALUES ({placeholders})",
            insert_vals,
        )
        conn.commit()
        row = conn.execute("SELECT * FROM event_leads WHERE id = ?", (cursor.lastrowid,)).fetchone()
        conn.close()
        return JSONResponse({"ok": True, "event_lead": dict(row)})

    @app.put("/api/event-leads/{lead_id}", dependencies=write_deps)
    async def api_update_event_lead(lead_id: int, request: Request):
        body = await request.json()
        conn = _conn()

        allowed = {
            "outreach_status", "notes", "restroom_need_score",
            "restroom_need_reason", "organizer_phone", "organizer_email",
        }
        sets, vals = [], []
        for k, v in body.items():
            if k in allowed:
                sets.append(f"{k} = ?")
                vals.append(v)
        if not sets:
            conn.close()
            return JSONResponse({"error": "No valid fields to update"}, status_code=400)

        sets.append("updated_at = datetime('now')")
        vals.append(lead_id)
        conn.execute(f"UPDATE event_leads SET {', '.join(sets)} WHERE id = ?", vals)
        conn.commit()
        row = conn.execute("SELECT * FROM event_leads WHERE id = ?", (lead_id,)).fetchone()
        conn.close()
        if not row:
            return JSONResponse({"error": "Event lead not found"}, status_code=404)
        return JSONResponse({"ok": True, "event_lead": dict(row)})
