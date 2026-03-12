"""
Vendor Referral Partner API — FastAPI endpoints for vendor management.

Registers endpoints on the FastAPI app for:
  - Listing/searching/filtering vendors
  - Vendor detail views
  - Aggregate stats by category/source/status
  - Outreach draft creation and approval
  - Outreach queue management

Usage in server.py:
    from core.vendor_api import register_vendor_routes
    register_vendor_routes(app)
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import FastAPI, Request, Depends
from fastapi.responses import JSONResponse

log = logging.getLogger("vendor_api")


def register_vendor_routes(app: FastAPI, verify_action_auth=None):
    """Register all vendor API endpoints on the FastAPI app.

    Args:
        app: The FastAPI application instance.
        verify_action_auth: Auth dependency for write endpoints (optional).
    """
    def _resolve_active_morning_batch_date() -> str:
        """Pick the review/scheduling date the UI should operate on.

        Behavior:
        - If there are unsent queue items (queued/edited/approved) for today and
          tomorrow has no active batch yet, keep reviewing/scheduling against today.
        - Otherwise default to tomorrow.
        """
        import sqlite3
        from datetime import datetime, timedelta
        try:
            from zoneinfo import ZoneInfo
        except ImportError:  # pragma: no cover
            from backports.zoneinfo import ZoneInfo  # type: ignore
        from pathlib import Path

        now_pt = datetime.now(ZoneInfo("America/Los_Angeles"))
        today = now_pt.strftime("%Y-%m-%d")
        tomorrow = (now_pt + timedelta(days=1)).strftime("%Y-%m-%d")

        db_path = Path.home() / ".nexus" / "memory.db"
        conn = sqlite3.connect(str(db_path))
        try:
            active_today = conn.execute(
                "SELECT COUNT(*) FROM email_queue WHERE scheduled_date = ? AND status IN ('queued','edited','approved')",
                (today,),
            ).fetchone()[0]
            active_tomorrow = conn.execute(
                "SELECT COUNT(*) FROM email_queue WHERE scheduled_date = ? AND status IN ('queued','edited','approved')",
                (tomorrow,),
            ).fetchone()[0]
        finally:
            conn.close()

        if active_today > 0 and active_tomorrow == 0:
            return today
        return tomorrow

    # Shared dependency list for write endpoints.
    write_deps = [Depends(verify_action_auth)] if verify_action_auth else []

    # ── GET /api/vendors — List vendors with filters ────────────────────────

    @app.get("/api/vendors")
    async def api_list_vendors(
        category: Optional[str] = None,
        status: Optional[str] = None,
        source: Optional[str] = None,
        city: Optional[str] = None,
        search: Optional[str] = None,
        outreach_status: Optional[str] = None,
        send_ready: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
    ):
        """List vendors with optional filters."""
        from core.vendor_db import get_vendors
        try:
            vendors, total = get_vendors(
                category=category,
                status=status,
                source=source,
                city=city,
                search=search,
                outreach_status=outreach_status,
                send_ready=bool(send_ready) if send_ready is not None else False,
                limit=limit,
                offset=offset,
            )
            return JSONResponse({
                "vendors": vendors,
                "total": total,
                "limit": limit,
                "offset": offset,
            })
        except Exception as e:
            log.error("Error listing vendors: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    # ── GET /api/vendors/stats — Aggregate counts ───────────────────────────

    @app.get("/api/vendors/stats")
    async def api_vendor_stats():
        """Get vendor counts by category, source, status, city, and outreach stats."""
        from core.vendor_db import get_vendor_stats
        try:
            stats = get_vendor_stats()
            return JSONResponse(stats)
        except Exception as e:
            log.error("Error fetching vendor stats: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    # ── GET /api/vendors/categories — List all categories ───────────────────

    @app.get("/api/vendors/categories")
    async def api_vendor_categories():
        """Return all supported vendor categories with query mappings."""
        from core.vendor_research import CATEGORY_QUERIES
        return JSONResponse({
            "categories": list(CATEGORY_QUERIES.keys()),
            "queries": {k: v for k, v in CATEGORY_QUERIES.items()},
        })

    # ── GET /api/vendors/outreach-queue — Pending outreach drafts ───────────
    # NOTE: Must be registered BEFORE {vendor_id} to avoid route conflict

    @app.get("/api/vendors/outreach-queue")
    async def api_outreach_queue(status: Optional[str] = None, limit: int = 50):
        """Get the outreach queue. Optionally filter by status (draft, pending_approval, approved, sent)."""
        from core.vendor_db import get_outreach_queue
        try:
            queue = get_outreach_queue(status=status, limit=limit)
            return JSONResponse({
                "queue": queue,
                "count": len(queue),
            })
        except Exception as e:
            log.error("Error fetching outreach queue: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    # ── Static sub-paths — must be before {vendor_id} catch-all ─────────────

    @app.get("/api/vendors/eligible")
    async def api_eligible_vendors(limit: int = 50):
        from core.vendor_enrichment import get_eligible_vendors
        vendors = get_eligible_vendors(limit=limit)
        return JSONResponse({"status": "ok", "count": len(vendors), "vendors": vendors})

    @app.get("/api/vendors/vetting-stats")
    async def api_vetting_stats():
        from core.vendor_vetting import get_vetting_stats
        stats = get_vetting_stats()
        return JSONResponse({"status": "ok", **stats})

    @app.get("/api/vendors/data-audit")
    async def api_vendor_data_audit(notify: int = 0):
        """Run data quality checks on the vendor database. Pass notify=1 to send to Telegram."""
        from core.vendor_db import run_data_audit
        try:
            audit = run_data_audit()
            if notify:
                try:
                    import json as _json
                    from urllib.request import Request as _Req, urlopen as _urlopen
                    cfg_file = Path.home() / ".nexus" / "config.json"
                    cfg = _json.loads(cfg_file.read_text()) if cfg_file.exists() else {}
                    token = cfg.get("telegram_token", "")
                    chat_ids = cfg.get("telegram_chat_ids", [])
                    if token and chat_ids:
                        text = (
                            "VENDOR DATA AUDIT\n"
                            "Total: %d\n"
                            "Send-ready: %d (%s%%)\n"
                            "Missing email: %d\n"
                            "Duplicate phones: %d\n"
                            "Placeholder websites: %d"
                        ) % (
                            audit.get("total", 0),
                            audit.get("send_ready", 0),
                            audit.get("send_ready_pct", "0"),
                            audit.get("missing_emails", 0),
                            audit.get("duplicate_phones", 0),
                            audit.get("placeholder_websites", 0),
                        )
                        for cid in chat_ids:
                            url = "https://api.telegram.org/bot%s/sendMessage" % token
                            payload = _json.dumps({"chat_id": str(cid), "text": text}).encode()
                            req = _Req(url, data=payload, headers={"Content-Type": "application/json"})
                            _urlopen(req, timeout=10)
                except Exception as e:
                    log.warning("Could not send audit to Telegram: %s", e)
            return JSONResponse(audit)
        except Exception as e:
            log.error("Error running vendor data audit: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    # ── Venue Intelligence endpoints — before {vendor_id} catch-all ─────────

    @app.get("/api/vendors/venue-intel/stats")
    async def api_venue_intel_stats():
        """Venue assessment statistics."""
        try:
            from core.venue_intel import VenueIntelAgent
            agent = VenueIntelAgent()
            stats = agent.get_stats()
            return JSONResponse({"status": "ok", **stats})
        except Exception as e:
            log.error("Venue intel stats error: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/vendors/venue-intel/targets")
    async def api_venue_intel_targets(min_score: int = 7, limit: int = 50):
        """Ranked list of high-value venue targets."""
        try:
            from core.venue_intel import VenueIntelAgent
            agent = VenueIntelAgent()
            targets = agent.get_top_targets(min_score=min_score, limit=limit)
            return JSONResponse({"status": "ok", "count": len(targets), "targets": targets})
        except Exception as e:
            log.error("Venue intel targets error: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/vendors/venue-intel/{vid}")
    async def api_venue_intel_detail(vid: int):
        """Get venue assessment for a specific vendor."""
        try:
            conn = _conn()
            row = conn.execute(
                "SELECT * FROM venue_assessments WHERE vendor_id = ?", (vid,)
            ).fetchone()
            conn.close()
            if not row:
                return JSONResponse({"status": "error", "detail": "No assessment found"}, status_code=404)
            return JSONResponse({"status": "ok", "assessment": dict(row)})
        except Exception as e:
            log.error("Venue intel detail error for #%d: %s", vid, e)
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/vendors/venue-intel/batch", dependencies=write_deps)
    async def api_venue_intel_batch(request: Request):
        """Trigger batch venue assessment. Body: {vendor_ids: [int]} or {category: str, limit: int}."""
        import asyncio as _aio
        try:
            body = await request.json()
            from core.venue_intel import VenueIntelAgent
            agent = VenueIntelAgent()

            vendor_ids = body.get("vendor_ids")
            if vendor_ids:
                results = await agent.assess_venues(vendor_ids)
            else:
                category = body.get("category", "")
                limit = body.get("limit", 10)
                conn = _conn()
                if category:
                    rows = conn.execute(
                        "SELECT id FROM vendors WHERE venue_assessed = 0 AND category = ? "
                        "AND website IS NOT NULL AND website <> '' AND website <> 'N/A' "
                        "ORDER BY referral_score DESC LIMIT ?",
                        (category, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT id FROM vendors WHERE venue_assessed = 0 "
                        "AND category IN ('wedding_venue','venue','event_venue','banquet_hall',"
                        "'country_club','hotel_venue','winery','ranch','garden_venue','estate',"
                        "'barn_venue','park_venue') "
                        "AND website IS NOT NULL AND website <> '' AND website <> 'N/A' "
                        "ORDER BY referral_score DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
                conn.close()
                vendor_ids = [r["id"] for r in rows]
                if not vendor_ids:
                    return JSONResponse({"status": "ok", "message": "No unassessed venues found", "results": []})
                results = await agent.assess_venues(vendor_ids)

            high = [r for r in results if r.get("ok") and r.get("fit_score", 0) >= 7]
            failed = [r for r in results if not r.get("ok")]
            return JSONResponse({
                "status": "ok",
                "assessed": len(results),
                "high_value": len(high),
                "failed": len(failed),
                "results": results,
            })
        except Exception as e:
            log.error("Venue intel batch error: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    # ── POST /api/vendors/referral-blast — Queue referral partnership emails ─

    HIGH_VALUE_CATEGORIES = (
        "wedding_planner", "event_planner", "wedding_venue", "event_venue",
        "venue", "banquet_hall", "catering", "caterer", "florist",
        "dj_entertainment", "dj", "photography", "photographer",
        "quinceanera_planner", "quinceanera", "party_rental",
        "tent_rental", "photo_booth", "officiant", "hair_makeup",
        "wedding_cake", "event_decorator", "decorator", "videography",
    )

    @app.post("/api/vendors/referral-blast", dependencies=write_deps)
    async def api_referral_blast(request: Request):
        """Queue referral partnership emails for high-value vendor categories.

        Body (all optional):
            categories: list[str] — override default high-value categories
            limit: int — max vendors to queue (default 50)
            scheduled_date: str — YYYY-MM-DD (default tomorrow)
        """
        import sqlite3
        from datetime import datetime, timedelta
        from pathlib import Path
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo  # type: ignore
        from core.cold_email import generate_referral_email

        try:
            body = await request.json()
        except Exception:
            body = {}

        categories = body.get("categories") or list(HIGH_VALUE_CATEGORIES)
        limit = min(int(body.get("limit", 50)), 200)
        now_pt = datetime.now(ZoneInfo("America/Los_Angeles"))
        scheduled_date = body.get("scheduled_date") or (
            now_pt + timedelta(days=1)
        ).strftime("%Y-%m-%d")

        db_path = Path.home() / ".nexus" / "memory.db"
        conn = sqlite3.connect(str(db_path), timeout=10)
        conn.row_factory = sqlite3.Row

        # Build category filter
        placeholders = ",".join("?" for _ in categories)

        # Find eligible vendors: has email, in high-value category,
        # not already sent a referral_partnership email, not blocklisted
        vendors = conn.execute(
            """
            SELECT v.id, v.name, v.email, v.category, v.city,
                   v.contact_name
            FROM vendors v
            LEFT JOIN contact_blocklist cb
                ON (cb.email = v.email AND cb.active = 1)
            LEFT JOIN email_queue eq
                ON eq.vendor_id = v.id
                AND eq.template_type = 'referral_partnership'
                AND eq.status NOT IN ('failed', 'removed')
            WHERE v.email IS NOT NULL AND v.email != ''
              AND v.category IN (%s)
              AND cb.id IS NULL
              AND eq.id IS NULL
            ORDER BY v.referral_score DESC, v.id
            LIMIT ?
            """ % placeholders,
            (*categories, limit),
        ).fetchall()

        queued = 0
        errors = 0
        # Determine next batch number
        next_batch = conn.execute(
            "SELECT COALESCE(MAX(batch_number), 0) + 1 AS n "
            "FROM email_queue WHERE scheduled_date = ?",
            (scheduled_date,),
        ).fetchone()["n"]

        for v in vendors:
            result = generate_referral_email(
                business_name=v["name"] or "",
                category=v["category"] or "",
                contact_name=v["contact_name"] if "contact_name" in v.keys() else "",
                city=v["city"] or "",
                vendor_id=v["id"],
            )
            if "error" in result:
                errors += 1
                log.warning("Referral email gen error for vendor #%d: %s",
                            v["id"], result["error"])
                continue

            conn.execute(
                """
                INSERT INTO email_queue
                   (vendor_id, recipient_email, recipient_name, category,
                    subject, body_plain, body_html, template_type,
                    status, scheduled_date, batch_number, priority_score, tags)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'referral_partnership',
                        'queued', ?, ?, 80, ?)
                """,
                (
                    v["id"],
                    v["email"],
                    v["name"] or "",
                    v["category"] or "",
                    result["subject"],
                    result["plain_body"],
                    result["html_body"],
                    scheduled_date,
                    next_batch,
                    json.dumps({"campaign": "referral_blast",
                                "category": v["category"] or "",
                                "variant": result["variant"]}),
                ),
            )
            queued += 1
            next_batch += 1

        conn.commit()
        conn.close()

        log.info("Referral blast: queued %d emails for %s (errors: %d)",
                 queued, scheduled_date, errors)
        return JSONResponse({
            "status": "ok",
            "queued": queued,
            "errors": errors,
            "scheduled_date": scheduled_date,
            "categories": categories,
        })

    # ── GET /api/vendors/{vendor_id} — Single vendor detail ─────────────────

    @app.get("/api/vendors/{vendor_id}")
    async def api_get_vendor(vendor_id: int):
        """Get a single vendor with their outreach history."""
        from core.vendor_db import get_vendor, get_vendor_outreach
        try:
            vendor = get_vendor(vendor_id)
            if not vendor:
                return JSONResponse({"error": "Vendor not found"}, status_code=404)

            outreach = get_vendor_outreach(vendor_id)
            return JSONResponse({
                "vendor": vendor,
                "outreach_history": outreach,
            })
        except Exception as e:
            log.error("Error fetching vendor #%d: %s", vendor_id, e)
            return JSONResponse({"error": str(e)}, status_code=500)

    # ── POST /api/vendors/{vendor_id}/status — Update vendor status ─────────

    @app.post("/api/vendors/{vendor_id}/status", dependencies=write_deps)
    async def api_update_vendor_status(vendor_id: int, request: Request):
        """Update a vendor's status, outreach_status, or notes.

        Body: {"status": "...", "outreach_status": "...", "notes": "..."}
        """
        from core.vendor_db import get_vendor, update_vendor_status
        try:
            vendor = get_vendor(vendor_id)
            if not vendor:
                return JSONResponse({"error": "Vendor not found"}, status_code=404)

            body = await request.json()
            updated = update_vendor_status(
                vendor_id,
                status=body.get("status"),
                outreach_status=body.get("outreach_status"),
                notes=body.get("notes"),
            )
            if updated:
                return JSONResponse({"ok": True, "vendor_id": vendor_id})
            return JSONResponse(
                {"ok": False, "error": "No valid fields to update"},
                status_code=400,
            )
        except Exception as e:
            log.error("Error updating vendor #%d: %s", vendor_id, e)
            return JSONResponse({"error": str(e)}, status_code=500)

    # ── POST /api/vendors/{vendor_id}/outreach — Create outreach draft ──────

    @app.post("/api/vendors/{vendor_id}/outreach", dependencies=write_deps)
    async def api_create_outreach(vendor_id: int, request: Request):
        """Create an outreach draft for a vendor.

        Body: {"channel": "email", "message_draft": "Hello..."}
        """
        from core.vendor_db import get_vendor, save_outreach_draft
        try:
            vendor = get_vendor(vendor_id)
            if not vendor:
                return JSONResponse({"error": "Vendor not found"}, status_code=404)

            body = await request.json()
            channel = body.get("channel", "email")
            message = body.get("message_draft", "")
            if not message:
                return JSONResponse(
                    {"error": "message_draft is required"},
                    status_code=400,
                )

            outreach_id = save_outreach_draft(vendor_id, channel, message)
            return JSONResponse({
                "ok": True,
                "outreach_id": outreach_id,
                "vendor_id": vendor_id,
                "channel": channel,
            })
        except Exception as e:
            log.error("Error creating outreach for vendor #%d: %s", vendor_id, e)
            return JSONResponse({"error": str(e)}, status_code=500)

    # ── POST /api/vendors/{vendor_id}/approve-outreach — Approve outreach ───

    @app.post("/api/vendors/{vendor_id}/approve-outreach", dependencies=write_deps)
    async def api_approve_outreach(vendor_id: int, request: Request):
        """Approve a pending outreach draft (Kai only).

        Body: {"outreach_id": 123}
        """
        from core.vendor_db import get_vendor, approve_outreach
        try:
            vendor = get_vendor(vendor_id)
            if not vendor:
                return JSONResponse({"error": "Vendor not found"}, status_code=404)

            body = await request.json()
            outreach_id = body.get("outreach_id")
            if not outreach_id:
                return JSONResponse(
                    {"error": "outreach_id is required"},
                    status_code=400,
                )

            approved = approve_outreach(outreach_id)
            return JSONResponse({
                "ok": approved,
                "outreach_id": outreach_id,
                "vendor_id": vendor_id,
            })
        except Exception as e:
            log.error("Error approving outreach #%d: %s", outreach_id, e)
            return JSONResponse({"error": str(e)}, status_code=500)

    # ── POST /api/vendors/search — Trigger a vendor research search ─────────

    @app.post("/api/vendors/search", dependencies=write_deps)
    async def api_trigger_vendor_search(request: Request):
        """Trigger a vendor research search for a specific category and location.

        Body: {"category": "wedding_venue", "locations": ["San Fernando Valley, CA"]}
        Returns results found and saved count.
        """
        from core.vendor_research import VendorResearcher
        from core.vendor_db import bulk_save_vendors

        try:
            body = await request.json()
            category = body.get("category", "")
            locations = body.get("locations")

            if not category:
                return JSONResponse(
                    {"error": "category is required"},
                    status_code=400,
                )

            researcher = VendorResearcher()
            results = await researcher.search_category(category, locations)

            # Assign category to all results
            for r in results:
                r["category"] = category

            counts = bulk_save_vendors(results)
            return JSONResponse({
                "ok": True,
                "category": category,
                "raw_results": len(results),
                "created": counts["created"],
                "updated": counts["updated"],
                "skipped": counts["skipped"],
            })
        except Exception as e:
            log.error("Error in vendor search: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    # ── POST /api/vendors/bulk-import — Import vendors from external data ───

    @app.post("/api/vendors/bulk-import", dependencies=write_deps)
    async def api_bulk_import_vendors(request: Request):
        """Import a list of vendors from external data.

        Body: {"vendors": [{"name": "...", "phone": "...", ...}, ...]}
        """
        from core.vendor_db import bulk_save_vendors
        try:
            body = await request.json()
            vendors = body.get("vendors", [])
            if not vendors:
                return JSONResponse(
                    {"error": "vendors list is required"},
                    status_code=400,
                )

            counts = bulk_save_vendors(vendors)
            return JSONResponse({
                "ok": True,
                "total_submitted": len(vendors),
                "created": counts["created"],
                "updated": counts["updated"],
                "skipped": counts["skipped"],
                "errors": counts["errors"],
            })
        except Exception as e:
            log.error("Error in vendor bulk import: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    # ── POST /api/vendors/{vendor_id}/draft-email — Generate email draft ────

    @app.post("/api/vendors/{vendor_id}/draft-email")
    async def api_draft_vendor_email(vendor_id: int):
        """Generate a cold email draft for a vendor using templates. No email sent."""
        from core.vendor_db import get_vendor
        from core.cold_email import generate_cold_email
        try:
            vendor = get_vendor(vendor_id)
            if not vendor:
                return JSONResponse({"error": "Vendor not found"}, status_code=404)

            result = generate_cold_email(
                business_name=vendor["name"],
                category=vendor.get("category", ""),
                contact_name=vendor.get("contact_name", ""),
                city=vendor.get("city", ""),
            )
            if "error" in result:
                return JSONResponse({"error": result["error"]}, status_code=400)

            return JSONResponse({
                "to": vendor.get("email", ""),
                "subject": result["subject"],
                "plain_body": result["plain_body"],
                "html_body": result["html_body"],
                "vendor_name": vendor["name"],
                "template_type": result.get("template_type", "referral"),
            })
        except Exception as e:
            log.error("Error drafting email for vendor #%d: %s", vendor_id, e)
            return JSONResponse({"error": str(e)}, status_code=500)

    # ── POST /api/vendors/{vendor_id}/send-email — Send email via SMTP ────────

    @app.post("/api/vendors/{vendor_id}/send-email", dependencies=write_deps)
    async def api_send_vendor_email(vendor_id: int, request: Request):
        """Send a cold email to a vendor. Runs outbound_gate FIRST (Rule 0).

        Body: {"to_email": "...", "subject": "...", "plain_body": "...", "html_body": "..."}
        """
        from core.vendor_db import get_vendor, save_outreach_draft, mark_outreach_sent, update_vendor_status
        try:
            vendor = get_vendor(vendor_id)
            if not vendor:
                return JSONResponse({"error": "Vendor not found"}, status_code=404)

            body = await request.json()
            to_email = body.get("to_email", vendor.get("email", ""))
            subject = body.get("subject", "")
            plain_body = body.get("plain_body", "")
            html_body = body.get("html_body", "")

            if not to_email or "@" not in to_email:
                return JSONResponse({"error": "Invalid email address"}, status_code=400)
            if not subject or not plain_body:
                return JSONResponse({"error": "subject and plain_body required"}, status_code=400)

            # Save outreach record as draft first
            outreach_id = save_outreach_draft(vendor_id, "email", plain_body)

            # Send via existing SMTP + outbound_gate (Rule 0)
            from integrations.zoar_bot import send_email as smtp_send
            import json as _json
            cfg_path = __import__("pathlib").Path.home() / ".nexus" / "config.json"
            cfg = _json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
            gmail = cfg.get("gmail_address", cfg.get("zoar_gmail", "zoarbathrooms@gmail.com"))
            app_pw = cfg.get("gmail_app_password", cfg.get("zoar_gmail_app_password", ""))

            if not app_pw:
                return JSONResponse({"error": "Gmail app password not configured"}, status_code=500)

            result = await smtp_send(gmail, app_pw, to_email, subject, plain_body, html_body=html_body)

            if result.get("ok"):
                mark_outreach_sent(outreach_id)
                update_vendor_status(vendor_id, outreach_status="sent", status="contacted")
                return JSONResponse({"ok": True, "outreach_id": outreach_id, "to": to_email})
            else:
                return JSONResponse(
                    {"error": result.get("error", "Send failed"), "outreach_id": outreach_id},
                    status_code=500,
                )
        except Exception as e:
            log.error("Error sending email to vendor #%d: %s", vendor_id, e)
            return JSONResponse({"error": str(e)}, status_code=500)

    # ── POST /api/vendors/bulk-send — Send emails to multiple vendors ─────────
    # NOTE: Must be registered BEFORE {vendor_id} routes — already is by position

    @app.post("/api/vendors/bulk-send", dependencies=write_deps)
    async def api_bulk_send_emails(request: Request):
        """Send cold emails to multiple vendors with 3s delay between sends.

        Body: {"vendor_ids": [1, 2, 3]}
        """
        import asyncio
        from core.vendor_db import get_vendor, save_outreach_draft, mark_outreach_sent, update_vendor_status
        from core.cold_email import generate_cold_email
        from integrations.zoar_bot import send_email as smtp_send
        import json as _json

        try:
            body = await request.json()
            vendor_ids = body.get("vendor_ids", [])
            if not vendor_ids:
                return JSONResponse({"error": "vendor_ids required"}, status_code=400)

            cfg_path = __import__("pathlib").Path.home() / ".nexus" / "config.json"
            cfg = _json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
            gmail = cfg.get("gmail_address", cfg.get("zoar_gmail", "zoarbathrooms@gmail.com"))
            app_pw = cfg.get("gmail_app_password", cfg.get("zoar_gmail_app_password", ""))

            if not app_pw:
                return JSONResponse({"error": "Gmail app password not configured"}, status_code=500)

            results = {"sent": 0, "blocked": 0, "errors": [], "details": []}

            for vid in vendor_ids:
                vendor = get_vendor(vid)
                if not vendor:
                    results["errors"].append({"vendor_id": vid, "error": "Not found"})
                    continue

                to_email = vendor.get("email", "")
                if not to_email or "@" not in to_email:
                    results["errors"].append({"vendor_id": vid, "error": "No email"})
                    continue

                email = generate_cold_email(
                    business_name=vendor["name"],
                    category=vendor.get("category", ""),
                    contact_name=vendor.get("contact_name", ""),
                    city=vendor.get("city", ""),
                )
                if "error" in email:
                    results["errors"].append({"vendor_id": vid, "error": email["error"]})
                    continue

                outreach_id = save_outreach_draft(vid, "email", email["plain_body"])
                send_result = await smtp_send(
                    gmail, app_pw, to_email, email["subject"],
                    email["plain_body"], html_body=email["html_body"],
                )

                if send_result.get("ok"):
                    mark_outreach_sent(outreach_id)
                    update_vendor_status(vid, outreach_status="sent", status="contacted")
                    results["sent"] += 1
                    results["details"].append({"vendor_id": vid, "name": vendor["name"], "status": "sent"})
                else:
                    err = send_result.get("error", "Unknown")
                    if "BLOCK" in str(err).upper():
                        results["blocked"] += 1
                    results["errors"].append({"vendor_id": vid, "error": err})

                # 3 second delay between sends to avoid spam filters
                if vid != vendor_ids[-1]:
                    await asyncio.sleep(3)

            return JSONResponse(results)
        except Exception as e:
            log.error("Error in bulk send: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    # /api/vendors/data-audit moved before {vendor_id} catch-all (see ~line 113)

    # ══════════════════════════════════════════════════════════════════════════
    # ── EMAIL MARKETING — Batch Preview → Approve → Send ──────────────────
    # ══════════════════════════════════════════════════════════════════════════

    @app.get("/api/email-marketing/preview-batch")
    async def api_email_marketing_preview(
        segment: Optional[str] = None,
        count: int = 10,
        min_quality: int = 40,
        scheduled_date: Optional[str] = None,
        include_removed: int = 0,
        refresh: int = 0,
    ):
        """Preview tomorrow's staged email queue for review/edit before send."""
        from core.email_sequences import map_category_to_segment
        from core.email_queue_manager import (
            evaluate_vendor_candidate,
            generate_queue,
            get_queue,
            get_queue_stats,
            normalize_unsent_queue_copy,
            prune_queue_quality,
        )
        from core.vendor_db import get_vendor

        try:
            if not scheduled_date:
                scheduled_date = _resolve_active_morning_batch_date()

            rows, _ = get_queue(
                scheduled_date=scheduled_date,
                status=None,
                category=None,
                limit=500,
                offset=0,
            )
            pruned = prune_queue_quality(scheduled_date)
            if pruned.get("removed", 0):
                rows, _ = get_queue(
                    scheduled_date=scheduled_date,
                    status=None,
                    category=None,
                    limit=500,
                    offset=0,
                )
            active_rows = [
                r for r in rows
                if (r.get("status") or "").lower() in ("queued", "edited", "approved")
            ]

            generate_result = {"status": "skip_existing", "generated": 0}
            normalized = {"updated": 0, "skipped": 0}

            # Avoid expensive queue regeneration on every UI refresh.
            target_pool = max(30, int(count) * 3)
            if refresh or len(active_rows) < max(int(count), 5):
                generate_result = generate_queue(scheduled_date=scheduled_date, count=target_pool)
                if refresh or int(generate_result.get("generated", 0) or 0) > 0:
                    normalized = normalize_unsent_queue_copy(scheduled_date)
                rows, _ = get_queue(
                    scheduled_date=scheduled_date,
                    status=None,
                    category=None,
                    limit=500,
                    offset=0,
                )

            stats = get_queue_stats(scheduled_date)

            batch = []
            for row in rows:
                status = (row.get("status") or "").lower()
                if not include_removed and status in ("removed", "failed", "sent"):
                    continue

                vendor = {}
                if row.get("vendor_id"):
                    try:
                        vendor = get_vendor(int(row["vendor_id"])) or {}
                    except Exception:
                        vendor = {}

                cat = row.get("category") or vendor.get("category") or "other"
                seg = map_category_to_segment(cat)
                queue_quality = int(
                    (row.get("priority_score") or 0)
                    or (vendor.get("campaign_quality", 0) or 0)
                )
                vendor_name = row.get("recipient_name") or vendor.get("name", "")
                website = vendor.get("website", "")
                decision = evaluate_vendor_candidate(vendor or {})
                quality = max(queue_quality, int(decision.get("quality_score", 0)))

                raw_tags = row.get("tags") or ""
                tag_data = {}
                if raw_tags and str(raw_tags).strip().startswith("{"):
                    try:
                        tag_data = json.loads(raw_tags)
                    except Exception:
                        tag_data = {}
                template_type = row.get("template_type", "initial_outreach")
                try:
                    step_number = int(tag_data.get("step_number") or 0)
                except Exception:
                    step_number = 0
                if step_number <= 0 and str(template_type).startswith("follow_up_"):
                    try:
                        step_number = max(2, int(str(template_type).split("_")[-1]))
                    except Exception:
                        step_number = 2
                if step_number <= 0:
                    step_number = 1

                if segment and seg != segment:
                    continue
                # Keep already-queued rows even when legacy data has quality=0.
                # Only enforce min_quality when we have a positive quality score.
                if min_quality and quality > 0 and quality < min_quality:
                    continue
                batch.append({
                    "queue_id": row["id"],
                    "vendor_id": row.get("vendor_id"),
                    "vendor_name": vendor_name,
                    "email": row.get("recipient_email") or vendor.get("email", ""),
                    "phone": vendor.get("phone", ""),
                    "website": website,
                    "category": cat,
                    "city": vendor.get("city", ""),
                    "campaign_quality": quality,
                    "quality_score": quality,
                    "quality_reasons": decision.get("reasons", []),
                    "quality_flags": decision.get("quality_flags", []),
                    "eligible_now": bool(decision.get("eligible", True)),
                    "contactability_signals": decision.get("contactability_signals", 0),
                    "generic_inbox": bool(decision.get("generic_inbox", False)),
                    "segment": seg,
                    "subject": row.get("subject", ""),
                    "plain_body": row.get("body_plain", ""),
                    "html_body": row.get("body_html", ""),
                    "template_type": template_type,
                    "step_number": step_number,
                    "sequence_segment": tag_data.get("sequence_segment", seg),
                    "ab_subject_variant": tag_data.get("ab_subject_variant", ""),
                    "ab_body_variant": tag_data.get("ab_body_variant", ""),
                    "status": row.get("status", "queued"),
                    "scheduled_date": row.get("scheduled_date", scheduled_date),
                    "name_source": tag_data.get("name_source", "none"),
                    "name_confidence": tag_data.get("name_confidence", 0),
                    "name_personalized": bool(tag_data.get("name_personalized", False)),
                    "proof_snippet": decision.get("proof_snippet", ""),
                })

            total_matching = len(batch)
            batch = batch[:max(1, count)]

            return JSONResponse({
                "batch": batch,
                "count": len(batch),
                "total_eligible": total_matching,
                "segment": segment,
                "scheduled_date": scheduled_date,
                "by_status": stats.get("by_status", {}),
                "queue_status": generate_result.get("status", "ok"),
                "normalized": normalized,
                "pruned": pruned,
                "rejected_reasons": generate_result.get("rejected_reasons", {}),
            })
        except Exception as e:
            log.error("Error generating preview batch: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.put("/api/email-marketing/queue/{queue_id}", dependencies=write_deps)
    async def api_email_marketing_edit_queue_item(queue_id: int, request: Request):
        """Edit a staged queue email without sending."""
        from core.email_queue_manager import edit_email
        try:
            body = await request.json()
            edited = edit_email(
                queue_id=queue_id,
                subject=body.get("subject"),
                body_plain=body.get("plain_body") if "plain_body" in body else body.get("body_plain"),
                body_html=body.get("html_body"),
                editor=body.get("editor", "kai"),
            )
            if not edited:
                return JSONResponse({"error": "Queue email not found"}, status_code=404)
            return JSONResponse({"ok": True, "email": edited})
        except Exception as e:
            log.error("Error editing queue email #%d: %s", queue_id, e)
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/email-marketing/approve-batch", dependencies=write_deps)
    async def api_email_marketing_approve(request: Request):
        """Approve a batch for scheduled sending (default: tomorrow).

        Body: {
            "emails": [
                {"vendor_id": 1, "queue_id": 12, "subject": "...", "plain_body": "...", "html_body": "..."},
                ...
            ],
            "scheduled_date": "YYYY-MM-DD"  // optional, defaults to tomorrow PT
        }
        """
        from core.vendor_db import get_vendor
        from core.email_queue_manager import (
            approve_email,
            edit_email,
            evaluate_vendor_candidate,
            get_queue_email,
            get_vendor_queue_email,
            stage_email,
        )

        try:
            body = await request.json()
            emails = body.get("emails", [])
            if not emails:
                return JSONResponse({"error": "emails list required"}, status_code=400)

            scheduled_date = body.get("scheduled_date")
            if not scheduled_date:
                scheduled_date = _resolve_active_morning_batch_date()
            editor = body.get("editor", "kai")

            results = {
                "status": "scheduled_for_tomorrow",
                "approved": 0,
                "queued": 0,
                "sent": 0,  # Compatibility: sends now happen via scheduled sender, not immediate API send.
                "queued_for_tomorrow": 0,
                "errors": [],
                "details": [],
                "scheduled_date": scheduled_date,
            }

            for item in emails:
                vid = item.get("vendor_id")
                vendor = get_vendor(vid) if vid else None
                if not vendor:
                    results["errors"].append({"vendor_id": vid, "error": "Not found"})
                    continue
                decision = evaluate_vendor_candidate(vendor or {})
                if not decision.get("eligible"):
                    results["errors"].append({
                        "vendor_id": vid,
                        "error": "Lead failed quality gate",
                        "reasons": decision.get("reasons", []),
                    })
                    continue

                to_email = vendor.get("email", "")
                if not to_email or "@" not in to_email:
                    results["errors"].append({"vendor_id": vid, "error": "No email"})
                    continue

                subject = item.get("subject", "")
                plain_body = item.get("plain_body", "")
                html_body = item.get("html_body", "")

                if not subject or not plain_body:
                    results["errors"].append({"vendor_id": vid, "error": "Missing subject/body"})
                    continue

                queue_id = item.get("queue_id")
                queue_email = get_queue_email(int(queue_id)) if queue_id else None
                if not queue_email:
                    queue_email = get_vendor_queue_email(vid, scheduled_date=scheduled_date, active_only=True)

                if not queue_email:
                    step_number = int(item.get("step_number") or 1)
                    template_type = item.get("template_type", "initial_outreach")
                    if step_number > 1 and template_type == "initial_outreach":
                        template_type = "follow_up_%d" % step_number
                    tag_payload = {
                        "category": vendor.get("category", "other"),
                        "sequence_segment": item.get("sequence_segment", ""),
                        "step_number": step_number,
                        "ab_subject_variant": item.get("ab_subject_variant", ""),
                        "ab_body_variant": item.get("ab_body_variant", ""),
                    }
                    queue_email = stage_email(
                        vendor_id=vid,
                        recipient_email=to_email,
                        recipient_name=vendor.get("name", ""),
                        category=vendor.get("category", ""),
                        subject=subject,
                        body_plain=plain_body,
                        body_html=html_body,
                        template_type=template_type,
                        scheduled_date=scheduled_date,
                        priority_score=int(vendor.get("campaign_quality", 0) or 0),
                        tags=json.dumps(tag_payload),
                    )
                    results["queued"] += 1

                queue_id = int(queue_email["id"])
                edited = edit_email(
                    queue_id,
                    subject=subject,
                    body_plain=plain_body,
                    body_html=html_body,
                    editor=editor,
                )
                if not edited:
                    results["errors"].append({"vendor_id": vid, "error": "Queue row not found"})
                    continue

                approved = approve_email(queue_id, approved_by=editor)
                if not approved:
                    results["errors"].append({"vendor_id": vid, "error": "Could not approve queue row"})
                    continue

                results["approved"] += 1
                results["queued_for_tomorrow"] += 1
                results["details"].append({
                    "vendor_id": vid,
                    "queue_id": queue_id,
                    "name": vendor["name"],
                    "email": to_email,
                    "status": approved.get("status", "approved"),
                    "scheduled_date": approved.get("scheduled_date", scheduled_date),
                })

            return JSONResponse(results)
        except Exception as e:
            log.error("Error in batch approve: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/email-marketing/stats")
    async def api_email_marketing_stats():
        """Campaign stats: eligible by segment, sent today, totals."""
        import sqlite3
        from pathlib import Path
        from core.email_sequences import CATEGORY_TO_SEGMENT

        try:
            db_path = Path.home() / ".nexus" / "memory.db"
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row

            # Total vendors
            total = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]

            # With email
            with_email = conn.execute(
                "SELECT COUNT(*) FROM vendors WHERE email != '' AND email IS NOT NULL"
            ).fetchone()[0]

            # Already contacted
            contacted = conn.execute(
                "SELECT COUNT(*) FROM vendors WHERE outreach_status IN ('sent', 'emailed', 'replied', 'approved', 'bounced')"
            ).fetchone()[0]

            # Fast eligibility snapshot (avoid slow DNS checks inside request path).
            blocked_like = (
                "%construction%",
                "%balloon%",
                "%dessert%",
                "%bakery%",
                "%cake%",
                "%candy%",
                "%ice_cream%",
                "%coffee_cart%",
            )
            eligible_raw = conn.execute(
                """
                SELECT COUNT(*)
                FROM vendors
                WHERE email != '' AND email IS NOT NULL
                  AND website != '' AND website IS NOT NULL
                  AND COALESCE(campaign_eligible, 1) != 0
                  AND COALESCE(outreach_status, 'none') IN ('none', 'new', '')
                  AND lower(COALESCE(category, '')) NOT LIKE ?
                  AND lower(COALESCE(category, '')) NOT LIKE ?
                  AND lower(COALESCE(category, '')) NOT LIKE ?
                  AND lower(COALESCE(category, '')) NOT LIKE ?
                  AND lower(COALESCE(category, '')) NOT LIKE ?
                  AND lower(COALESCE(category, '')) NOT LIKE ?
                  AND lower(COALESCE(category, '')) NOT LIKE ?
                  AND lower(COALESCE(category, '')) NOT LIKE ?
                """,
                blocked_like,
            ).fetchone()[0]

            # By segment (top categories)
            segment_counts = {}
            for seg_name in ["venue", "vendor", "planner"]:
                cats = [c for c, s in CATEGORY_TO_SEGMENT.items() if s == seg_name]
                if cats:
                    placeholders = ",".join("?" * len(cats))
                    segment_counts[seg_name] = conn.execute(
                        (
                            "SELECT COUNT(*) FROM vendors WHERE category IN (%s) "
                            "AND email != '' AND email IS NOT NULL "
                            "AND website != '' AND website IS NOT NULL "
                            "AND COALESCE(campaign_eligible, 1) != 0 "
                            "AND COALESCE(outreach_status, 'none') IN ('none', 'new', '')"
                        ) % placeholders,
                        cats,
                    ).fetchone()[0]

            batch_date = _resolve_active_morning_batch_date()

            # Outbound log is source of truth for actual sends (result='sent').
            sent_total = conn.execute(
                "SELECT COUNT(*) FROM outbound_log "
                "WHERE channel = 'email' AND result IN ('sent', 'allowed')"
            ).fetchone()[0]

            replied_vo = conn.execute(
                "SELECT COUNT(DISTINCT vendor_id) FROM vendor_outreach "
                "WHERE status = 'replied' AND vendor_id IS NOT NULL"
            ).fetchone()[0]
            replied_vendor = conn.execute(
                "SELECT COUNT(*) FROM vendors WHERE outreach_status = 'replied'"
            ).fetchone()[0]
            replied_total = max(int(replied_vo or 0), int(replied_vendor or 0))

            bounced_total = conn.execute(
                "SELECT COUNT(DISTINCT lower(recipient)) FROM outbound_log "
                "WHERE channel = 'email' AND result = 'bounced'"
            ).fetchone()[0]

            # Sent today
            sent_today = conn.execute(
                "SELECT COUNT(*) FROM outbound_log "
                "WHERE channel = 'email' AND result IN ('sent', 'allowed') "
                "AND DATE(timestamp, 'localtime') = DATE('now', 'localtime')"
            ).fetchone()[0]

            queued_tomorrow = conn.execute(
                "SELECT COUNT(*) FROM email_queue WHERE scheduled_date = ? AND status IN ('queued', 'edited')",
                (batch_date,),
            ).fetchone()[0]
            approved_tomorrow = conn.execute(
                "SELECT COUNT(*) FROM email_queue WHERE scheduled_date = ? AND status = 'approved'",
                (batch_date,),
            ).fetchone()[0]
            tomorrow_steps = conn.execute(
                """
                SELECT template_type, COUNT(*) AS cnt
                FROM email_queue
                WHERE scheduled_date = ?
                  AND status IN ('queued', 'edited', 'approved')
                GROUP BY template_type
                """,
                (batch_date,),
            ).fetchall()
            step_breakdown = {
                (r["template_type"] or "initial_outreach"): int(r["cnt"] or 0)
                for r in tomorrow_steps
            }

            conn.close()

            reply_rate = round(replied_total / max(sent_total, 1) * 100, 1)
            bounce_rate = round(bounced_total / max(sent_total, 1) * 100, 1)

            return JSONResponse({
                "total_vendors": total,
                "with_email": with_email,
                "contacted": contacted,
                "eligible_uncontacted": eligible_raw,
                "by_segment": segment_counts,
                "sent_total": sent_total,
                "sent_today": sent_today,
                "queued_tomorrow": queued_tomorrow,
                "approved_tomorrow": approved_tomorrow,
                "tomorrow_step_breakdown": step_breakdown,
                "tomorrow_date": batch_date,
                "batch_date": batch_date,
                "replied": replied_total,
                "bounced": bounced_total,
                "reply_rate_pct": reply_rate,
                "bounce_rate_pct": bounce_rate,
            })
        except Exception as e:
            log.error("Error fetching email marketing stats: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/email-marketing/skip", dependencies=write_deps)
    async def api_email_marketing_skip(request: Request):
        """Skip specific vendors from a scheduled review batch.

        Body: {"vendor_ids": [1, 2, 3], "scheduled_date": "YYYY-MM-DD", "permanent": false}
        """
        from core.vendor_db import update_vendor_status
        from core.email_queue_manager import remove_vendor_from_queue
        try:
            body = await request.json()
            vendor_ids = body.get("vendor_ids", [])
            if not vendor_ids:
                return JSONResponse({"error": "vendor_ids required"}, status_code=400)

            scheduled_date = body.get("scheduled_date")
            if not scheduled_date:
                scheduled_date = _resolve_active_morning_batch_date()
            permanent = bool(body.get("permanent", False))

            skipped = 0
            removed_from_queue = 0
            for vid in vendor_ids:
                removed_from_queue += remove_vendor_from_queue(int(vid), scheduled_date=scheduled_date)
                if permanent:
                    update_vendor_status(int(vid), outreach_status="skipped")
                skipped += 1

            return JSONResponse({
                "ok": True,
                "skipped": skipped,
                "removed_from_queue": removed_from_queue,
                "scheduled_date": scheduled_date,
                "permanent": permanent,
            })
        except Exception as e:
            log.error("Error skipping vendors: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/email-marketing/regenerate")
    async def api_email_marketing_regenerate(request: Request):
        """Generate a fresh subject/body variation for a queued vendor draft."""
        from core.email_queue_manager import get_queue_email, render_sequence_step_for_vendor
        from core.email_sequences import get_sequence, get_step, map_category_to_segment
        from core.vendor_db import get_vendor

        try:
            body = await request.json()
            vendor_id = body.get("vendor_id")
            queue_id = body.get("queue_id")
            step_number = int(body.get("step_number") or 1)
            sender_name = (body.get("sender_name") or "Kai").strip() or "Kai"
            current_subject = body.get("current_subject", "")
            current_plain_body = body.get("current_plain_body", "")
            subject_variant = (body.get("ab_subject_variant") or "").strip().upper()

            queue_row = None
            queue_tags = {}
            if queue_id:
                queue_row = get_queue_email(int(queue_id))
                if queue_row and not vendor_id:
                    vendor_id = queue_row.get("vendor_id")
                if queue_row and not current_subject:
                    current_subject = queue_row.get("subject", "")
                if queue_row and not current_plain_body:
                    current_plain_body = queue_row.get("body_plain", "")
                raw_tags = (queue_row or {}).get("tags") or ""
                if raw_tags and str(raw_tags).strip().startswith("{"):
                    try:
                        queue_tags = json.loads(raw_tags)
                    except Exception:
                        queue_tags = {}
                if step_number <= 1:
                    step_number = int(queue_tags.get("step_number") or 0)
                    if step_number <= 0 and str((queue_row or {}).get("template_type", "")).startswith("follow_up_"):
                        try:
                            step_number = max(2, int(str((queue_row or {}).get("template_type", "")).split("_")[-1]))
                        except Exception:
                            step_number = 2
                    if step_number <= 0:
                        step_number = 1
                if not subject_variant:
                    subject_variant = str(queue_tags.get("ab_subject_variant") or "").strip().upper()

            if not vendor_id:
                return JSONResponse({"error": "vendor_id or queue_id required"}, status_code=400)

            vendor = get_vendor(int(vendor_id))
            if not vendor:
                return JSONResponse({"error": "Vendor not found"}, status_code=404)

            segment = (queue_tags.get("sequence_segment") or "").strip().lower()
            if not segment:
                segment = map_category_to_segment(vendor.get("category", "other"))
            step = get_step(segment, step_number) or get_sequence(segment)[0]
            step_number = int(step.get("step", step_number))

            regenerated = None
            for _ in range(6):
                candidate = render_sequence_step_for_vendor(
                    vendor,
                    sequence_segment=segment,
                    step_number=step_number,
                    subject_variant=subject_variant,
                    sender_name=sender_name,
                ) or {}
                regenerated = candidate
                if (
                    candidate.get("subject", "") != current_subject
                    or candidate.get("plain_body", "") != current_plain_body
                ):
                    if candidate.get("subject_variant"):
                        subject_variant = candidate.get("subject_variant")
                    break
                # Flip AB arm if we matched the previous draft and try again.
                subject_variant = "B" if (subject_variant or "A") == "A" else "A"

            return JSONResponse({
                "ok": True,
                "vendor_id": int(vendor_id),
                "queue_id": int(queue_id) if queue_id else None,
                "segment": segment,
                "step_number": step_number,
                "subject": regenerated.get("subject", ""),
                "plain_body": regenerated.get("plain_body", ""),
                "html_body": regenerated.get("html_body", ""),
                "ab_subject_variant": regenerated.get("subject_variant", subject_variant),
                "ab_body_variant": regenerated.get("body_variant", queue_tags.get("ab_body_variant", "baseline")),
                "template_type": (queue_row or {}).get("template_type", "initial_outreach"),
            })
        except Exception as e:
            log.error("Error regenerating email draft: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/email-marketing/reject-delete", dependencies=write_deps)
    async def api_email_marketing_reject_delete(request: Request):
        """Reject and fully delete a lead from morning batch + vendors database."""
        from core.email_queue_manager import get_queue_email
        from core.vendor_db import reject_and_delete_vendor

        try:
            body = await request.json()
            vendor_id = body.get("vendor_id")
            queue_id = body.get("queue_id")
            reason = (body.get("reason") or "").strip()
            rejected_by = (body.get("rejected_by") or "kai").strip() or "kai"

            if not vendor_id and queue_id:
                queue_row = get_queue_email(int(queue_id))
                if queue_row:
                    vendor_id = queue_row.get("vendor_id")

            if not vendor_id:
                return JSONResponse({"error": "vendor_id or queue_id required"}, status_code=400)

            result = reject_and_delete_vendor(
                int(vendor_id),
                reason=reason,
                rejected_by=rejected_by,
            )
            if not result.get("ok"):
                status_code = 404 if result.get("error") == "Vendor not found" else 400
                return JSONResponse(result, status_code=status_code)
            return JSONResponse(result)
        except Exception as e:
            log.error("Error rejecting/deleting vendor: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/email-marketing/campaign-health")
    async def api_email_marketing_health():
        """Campaign health metrics: daily limits, warm-up status."""
        import sqlite3
        from pathlib import Path

        try:
            db_path = Path.home() / ".nexus" / "memory.db"
            conn = sqlite3.connect(str(db_path))
            batch_date = _resolve_active_morning_batch_date()

            # Today's sends (source of truth: outbound_log)
            sent_today = conn.execute(
                "SELECT COUNT(*) FROM outbound_log "
                "WHERE channel = 'email' AND result = 'sent' "
                "AND DATE(timestamp, 'localtime') = DATE('now', 'localtime')"
            ).fetchone()[0]

            # This hour's sends
            sent_this_hour = conn.execute(
                "SELECT COUNT(*) FROM outbound_log "
                "WHERE channel = 'email' AND result = 'sent' "
                "AND timestamp >= datetime('now', '-1 hour')"
            ).fetchone()[0]

            pending_review_tomorrow = conn.execute(
                "SELECT COUNT(*) FROM email_queue WHERE scheduled_date = ? AND status IN ('queued', 'edited')",
                (batch_date,),
            ).fetchone()[0]
            approved_tomorrow = conn.execute(
                "SELECT COUNT(*) FROM email_queue WHERE scheduled_date = ? AND status = 'approved'",
                (batch_date,),
            ).fetchone()[0]

            conn.close()

            daily_limit = 100  # Gmail free account limit
            hourly_limit = 15

            return JSONResponse({
                "sent_today": sent_today,
                "daily_limit": daily_limit,
                "daily_remaining": max(0, daily_limit - sent_today),
                "sent_this_hour": sent_this_hour,
                "hourly_limit": hourly_limit,
                "hourly_remaining": max(0, hourly_limit - sent_this_hour),
                "tomorrow_date": batch_date,
                "batch_date": batch_date,
                "pending_review_tomorrow": pending_review_tomorrow,
                "approved_tomorrow": approved_tomorrow,
                "health": "good" if sent_today < daily_limit * 0.8 else "caution",
            })
        except Exception as e:
            log.error("Error checking campaign health: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/email-marketing/pre-send-check", dependencies=write_deps)
    async def api_email_marketing_presend_check(request: Request):
        """Run strict pre-send gate on the active morning batch."""
        from core.email_presend_gate import run_presend_gate
        try:
            body = await request.json() if request else {}
            scheduled_date = body.get("scheduled_date")
            if not scheduled_date:
                scheduled_date = _resolve_active_morning_batch_date()
            min_approved = int(body.get("min_approved") or 30)
            apply_autofix = bool(body.get("apply_autofix", True))
            result = run_presend_gate(
                scheduled_date=scheduled_date,
                min_approved=min_approved,
                apply_autofix=apply_autofix,
            )
            return JSONResponse(result)
        except Exception as e:
            log.error("Error running pre-send check: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    log.info("Vendor API routes registered")
