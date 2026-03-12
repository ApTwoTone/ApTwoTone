"""
Nexus Router Agent — Classifies user messages and routes to the right handler.

Uses Groq for near-instant classification (<200ms), then dispatches:
  - build    → Multi-Agent Build Pipeline
  - vendor   → Division One vendor workers
  - creative → Division Two creative workers (when built)
  - revenue  → Division Three experiments (when built)
  - data     → Query Agent (reads DB, returns formatted answer)
  - system   → System Control Agent
  - general  → Gemini 3 Flash for direct answer
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("nexus_router")

DB_PATH = Path.home() / ".nexus" / "memory.db"

CATEGORIES = ["build", "vendor", "creative", "revenue", "data", "system", "general"]

ROUTER_SYSTEM_PROMPT = (
    "You are a message classifier for Nexus, a business automation system for a luxury "
    "restroom trailer rental company. Classify the user's message into exactly ONE category.\n\n"
    "Categories:\n"
    "- build: Requests for code changes, new features, fixing bugs, creating screens. "
    "Example: 'add dark mode', 'fix the vendor counter', 'create a new API endpoint'\n"
    "- vendor: Vendor research, outreach, vendor database queries, email campaigns, filtering vendors, scoring leads. "
    "Example: 'find wedding venues in Pasadena', 'filter my vendors', 'send test batch', 'campaign status', 'top leads'\n"
    "- creative: Ad creatives, scripts, Facebook campaigns, ad copy. "
    "Example: 'write new ad copy', 'create a wedding ad', 'build weekly ad brief'\n"
    "- revenue: Revenue experiments, pricing, A/B tests. "
    "Example: 'test new pricing tier', 'run a discount experiment'\n"
    "- data: Asking for data/information from the system. "
    "Example: 'what is our CPL', 'show leads from this week', 'how many vendors in zone 3'\n"
    "- system: System control commands. "
    "Example: 'restart server', 'check health', 'pause vendor discovery', 'show worker status'\n"
    "- general: Anything else, general questions, conversation. "
    "Example: 'what time is it', 'explain how the fleet works'\n\n"
    "Output ONLY a JSON object: {\"category\": \"...\", \"confidence\": 0.0-1.0}\n"
    "No explanation, no markdown."
)


# ── Follow-up reference resolution ────────────────────────────────────────────

_FOLLOWUP_REFS = frozenset([
    "them", "they", "those", "that one", "the first", "the second",
    "the third", "the fourth", "the fifth",
    "number one", "number two", "number three", "number four", "number five",
    "number 1", "number 2", "number 3", "number 4", "number 5",
    "#1", "#2", "#3", "#4", "#5", "for it", "first one", "second one",
    "third one", "fourth one", "fifth one",
])

_ORDINAL_MAP = {
    "them": None, "they": None, "those": None, "that one": None, "for it": None,
    "the first": 1, "first one": 1, "number one": 1, "number 1": 1, "#1": 1,
    "the second": 2, "second one": 2, "number two": 2, "number 2": 2, "#2": 2,
    "the third": 3, "third one": 3, "number three": 3, "number 3": 3, "#3": 3,
    "the fourth": 4, "fourth one": 4, "number four": 4, "number 4": 4, "#4": 4,
    "the fifth": 5, "fifth one": 5, "number five": 5, "number 5": 5, "#5": 5,
}


def _is_followup_reference(tl: str) -> bool:
    """Check if text contains a pronoun or ordinal reference to a previous message."""
    return any(ref in tl for ref in _FOLLOWUP_REFS)


def _resolve_followup(tl: str, history: List[Dict]) -> Optional[str]:
    """Resolve 'them'/'number one'/etc. from the last assistant message's vendor list.

    Scans previous assistant messages for **Bold Name** patterns (vendor lists)
    and rewrites the request with the explicit vendor name.
    """
    import re as _re

    # Find the most recent assistant message with vendor names
    vendor_names = []  # type: List[str]
    for msg in reversed(history):
        role = msg.get("role", "")
        content = msg.get("content", "") or msg.get("response", "") or ""
        if role != "assistant" or not content:
            continue
        # Extract **Name** patterns (vendor list format: "1. **Vendor Name** — ...")
        found = _re.findall(r'\*\*(.+?)\*\*', content)
        if found:
            vendor_names = found
            break

    if not vendor_names:
        return None

    # Determine which vendor is referenced
    idx = None  # type: Optional[int]
    for ref, ordinal in _ORDINAL_MAP.items():
        if ref in tl:
            idx = ordinal
            break

    if idx is None:
        # Generic reference ("them", "they") — use first vendor
        target = vendor_names[0]
    elif 1 <= idx <= len(vendor_names):
        target = vendor_names[idx - 1]
    else:
        return None

    # Rewrite the request to be explicit
    if "full" in tl or "show" in tl or "give" in tl:
        return "show full email for %s" % target
    if "email" in tl:
        return "draft one email for %s" % target
    return "draft one email for %s" % target


class NexusRouter:
    """Routes user messages to the appropriate handler."""

    def __init__(self):
        pass

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    async def process_message(self, text: str, source: str = "web") -> Dict[str, Any]:
        """Route a message and return the response. Main entry point."""
        start = time.time()

        # /claude gate — route single message to Claude Sonnet
        force_claude = False
        if text.strip().lower().startswith("/claude "):
            text = text.strip()[8:]  # Strip "/claude " prefix
            force_claude = True

        # Save user message
        user_msg_id = self._save_message("user", text, source=source)

        if force_claude:
            from core.smart_router import get_smart_router
            response = await get_smart_router().route(text, force_claude=True)
            category = "general"
        else:
            # Classify
            category = await self._classify(text)
            # Route to handler
            response = await self._handle(category, text)

        latency = int((time.time() - start) * 1000)

        # Save assistant response
        resp_id = self._save_message(
            role="assistant",
            content=response.get("content", ""),
            agent=response.get("agent", ""),
            model=response.get("model", ""),
            category=category,
            latency_ms=latency,
            build_id=response.get("build_id"),
            build_status=response.get("build_status", ""),
            source=source,
        )

        return {
            "message": {
                "id": resp_id,
                "role": "assistant",
                "content": response.get("content", ""),
                "agent": response.get("agent", ""),
                "model": response.get("model", ""),
                "category": category,
                "latency_ms": latency,
                "build_id": response.get("build_id"),
                "build_status": response.get("build_status", ""),
                "created_at": datetime.utcnow().isoformat(),
            }
        }

    async def _classify(self, text: str) -> str:
        """Classify message using Groq for speed."""
        # Quick pattern matching for obvious categories
        tl = text.lower().strip()

        # Questions are NEVER builds — route to general/data
        is_question = (
            tl.endswith("?")
            or tl.startswith(("what ", "how ", "why ", "who ", "where ", "when ",
                              "can ", "could ", "should ", "is ", "are ", "do ", "does ",
                              "show ", "tell ", "explain "))
        )
        if is_question:
            # Let data/system/vendor matchers below handle specific questions,
            # otherwise fall through to general
            pass
        elif tl.startswith(("build ", "implement ", "refactor ")):
            # Only these prefixes are unambiguous build triggers
            return "build"
        elif tl.startswith(("fix ", "create ", "add ")):
            # These are ambiguous — only build if it looks like a feature
            # "fix the typo" or "add a comment" = small action, not build
            feature_signals = [
                "feature", "component", "page", "endpoint", "api",
                "module", "system", "dashboard", "pipeline", "integration",
                "agent", "worker", "daemon", "service", "tab",
            ]
            if any(sig in tl for sig in feature_signals):
                return "build"
            # Small action — route to general (SmartRouter handles it)
            return "general"

        # Vendor/campaign commands — check BEFORE system (campaign/vendor phrases
        # contain "status", "pause", "resume" which would otherwise match system)
        vendor_phrases = ["outreach", "email campaign", "cold email",
                          "enrich", "test batch", "send test", "start campaign",
                          "campaign status", "vendor filter",
                          "pause campaign", "resume campaign"]
        if any(kw in tl for kw in vendor_phrases):
            return "vendor"
        if ("filter" in tl or "score" in tl or "eligible" in tl) and ("vendor" in tl or "lead" in tl):
            return "vendor"
        if "top" in tl and ("lead" in tl or "vendor" in tl):
            return "vendor"
        if "campaign" in tl:
            return "vendor"
        # Email preview / draft commands
        if ("preview" in tl or "draft" in tl or "sample" in tl) and ("email" in tl or "outreach" in tl):
            return "vendor"
        if "email" in tl and any(kw in tl for kw in ["show", "preview", "draft", "look like", "template"]):
            return "vendor"

        if any(kw in tl for kw in ["health", "restart", "status", "pause", "resume", "workers", "kill"]):
            return "system"
        if any(kw in tl for kw in ["experiment", "persona", "revenue lab", "division three", "d3 ", "trend scan", "portfolio", "niche", "arbitrage"]):
            return "revenue"
        if any(kw in tl for kw in ["how many", "show me", "list ", "count ", "what is my", "cpl", "leads from"]):
            return "data"

        # AI classification for ambiguous messages
        try:
            from core.worker_pool import call_for_task
            result = await call_for_task(
                "speed_critical",
                [{"role": "user", "content": text}],
                system=ROUTER_SYSTEM_PROMPT,
                max_tokens=64,
                temperature=0.0,
            )
            if result.get("ok"):
                parsed = self._extract_json(result["content"])
                if parsed and parsed.get("category") in CATEGORIES:
                    return parsed["category"]
        except Exception as e:
            log.warning("Router classification failed: %s", e)

        return "general"

    async def _handle(self, category: str, text: str) -> Dict[str, Any]:
        """Dispatch to the appropriate handler."""
        handlers = {
            "build": self._handle_build,
            "vendor": self._handle_vendor,
            "creative": self._handle_creative,
            "revenue": self._handle_revenue,
            "data": self._handle_data,
            "system": self._handle_system,
            "general": self._handle_general,
        }
        handler = handlers.get(category, self._handle_general)
        try:
            return await handler(text)
        except Exception as e:
            log.error("Handler %s failed: %s", category, e)
            return {"content": "Error processing request: %s" % str(e), "agent": "system"}

    # ── Handlers ─────────────────────────────────────────────────────────────

    async def _handle_build(self, text: str) -> Dict[str, Any]:
        """Route to Multi-Agent Build Pipeline."""
        from core.build_pipeline import get_build_pipeline
        bp = get_build_pipeline()
        build_id = bp.start_build(text, started_by="talk-to-nexus")
        return {
            "content": "Build #%d queued. Stage 1/5: Architect (Gemini) starting now.\n\n%s" % (
                build_id, text[:200]),
            "agent": "Build Pipeline",
            "model": "gemini",
            "build_id": build_id,
            "build_status": "architect",
        }

    async def _handle_vendor(self, text: str, history: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Handle vendor enrichment, outreach, and campaign commands."""
        tl = text.lower().strip()

        # Resolve follow-up references ("them", "number one", "#2", etc.)
        if history and _is_followup_reference(tl):
            resolved = _resolve_followup(tl, history)
            if resolved:
                text = resolved
                tl = text.lower().strip()

        try:
            if any(kw in tl for kw in ["preview", "draft", "sample"]) and "email" in tl:
                return await self._vendor_preview_email(text)
            if "email" in tl and any(kw in tl for kw in ["show", "look like", "template"]):
                return await self._vendor_preview_email(text)
            if any(kw in tl for kw in ["filter", "enrich", "score"]):
                return await self._vendor_filter()
            if ("top" in tl and ("lead" in tl or "vendor" in tl)) or "eligible" in tl:
                return await self._vendor_top_leads(text)
            if "test batch" in tl or "send test" in tl:
                return await self._vendor_test_batch()
            if "start campaign" in tl or "begin campaign" in tl:
                return await self._vendor_start_campaign(text)
            if "campaign status" in tl or "outreach status" in tl:
                return await self._vendor_campaign_status()
            if "pause campaign" in tl:
                return await self._vendor_pause_campaign()
            if "resume campaign" in tl:
                return await self._vendor_resume_campaign()
        except Exception as e:
            log.error("Vendor handler error: %s", e)
            return {"content": "Error: %s" % str(e), "agent": "vendor", "model": "system"}

        return await self._handle_data(text)

    async def _vendor_filter(self) -> Dict[str, Any]:
        """Run vendor enrichment pipeline."""
        from core.vendor_enrichment import score_all_vendors
        result = score_all_vendors()
        content = (
            "**Vendor Enrichment Complete** (%.1fs)\n\n"
            "**%d** vendors scored, **%d** campaign-eligible (%.1f%%)\n\n"
            "**By Distance:**\n"
            "- Tier 1 (0-10 mi): %d\n"
            "- Tier 2 (10-20 mi): %d\n"
            "- Tier 3 (20+ mi): %d\n"
            "- Out of area: %d\n\n"
            "**Duplicate emails removed:** %d\n\n"
            "**Top Categories:**\n%s"
        ) % (
            result["elapsed_seconds"],
            result["total_vendors"], result["eligible"],
            result["eligible"] / max(result["total_vendors"], 1) * 100,
            result["tier_1"], result["tier_2"], result["tier_3"],
            result["out_of_area"], result["duplicate_emails"],
            "\n".join("- %s: %d" % (c, n) for c, n in result["top_categories"][:10]),
        )
        return {"content": content, "agent": "vendor_enrichment", "model": "system"}

    async def _vendor_top_leads(self, text: str) -> Dict[str, Any]:
        """Show top eligible vendors."""
        import re as _re
        # Parse count from text
        m = _re.search(r"(\d+)", text)
        limit = min(int(m.group(1)), 100) if m else 20

        from core.vendor_enrichment import get_eligible_vendors
        vendors = get_eligible_vendors(limit=limit)

        if not vendors:
            return {"content": "No eligible vendors found. Run `filter vendors` first.",
                    "agent": "vendor", "model": "system"}

        lines = ["**Top %d Eligible Vendors:**\n" % len(vendors)]
        for i, v in enumerate(vendors, 1):
            lines.append(
                "%d. **%s** — %s, %s (Score: %d, Tier %d)" % (
                    i, v["name"], v["category"], v["city"],
                    v["referral_score"], v["distance_tier"],
                )
            )
        return {"content": "\n".join(lines), "agent": "vendor", "model": "system"}

    async def _vendor_preview_email(self, text: str) -> Dict[str, Any]:
        """Preview cold emails for specific or top eligible vendors — NO emails sent."""
        import re as _re
        import sqlite3
        from pathlib import Path
        from core.cold_email import generate_cold_email

        DB_PATH = Path.home() / ".nexus" / "memory.db"

        # Parse count: digits or word numbers
        _WORD_NUMS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                      "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
        m = _re.search(r"(\d+)", text)
        if m:
            limit = min(int(m.group(1)), 20)
        else:
            # Check word numbers
            tl = text.lower()
            limit = 5  # default
            for word, num in _WORD_NUMS.items():
                if word in tl.split():
                    limit = num
                    break

        # Extract vendor name from "for X" pattern
        vendor_name = None
        name_match = _re.search(r'\bfor\s+(.+?)(?:\s*$)', text, _re.IGNORECASE)
        if name_match:
            vendor_name = name_match.group(1).strip().rstrip(".")

        vendors = []
        if vendor_name:
            # Search vendors by name
            try:
                conn = sqlite3.connect(str(DB_PATH), timeout=5)
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT id, name, email, category, city FROM vendors "
                    "WHERE name LIKE ? ORDER BY referral_score DESC LIMIT ?",
                    ("%" + vendor_name + "%", limit),
                ).fetchall()
                conn.close()
                vendors = [dict(r) for r in rows]
            except Exception:
                pass

        if not vendors:
            # Fall back to top eligible vendors
            from core.vendor_enrichment import get_eligible_vendors
            vendors = get_eligible_vendors(limit=limit)

        if not vendors:
            return {"content": "No vendors found. Run `filter vendors` first.",
                    "agent": "vendor", "model": "system"}

        show_full = len(vendors) <= 3  # Show full body for small counts
        lines = [
            "**EMAIL PREVIEW ONLY — NO EMAILS SENT**\n",
            "Showing %d draft%s:\n" % (len(vendors), "s" if len(vendors) > 1 else ""),
        ]
        for i, v in enumerate(vendors, 1):
            email = generate_cold_email(
                v["name"], v.get("category", ""), "", city=v.get("city", ""))
            if "error" in email:
                lines.append("%d. **%s** — error generating email" % (i, v["name"]))
                continue
            if show_full:
                lines.append(
                    "%d. **%s** (%s, %s)\n"
                    "   Subject: *%s*\n\n%s\n" % (
                        i, v["name"], v.get("category", ""), v.get("city", ""),
                        email["subject"], email["plain_body"],
                    )
                )
            else:
                body_preview = email["plain_body"][:200].replace("\n", " ")
                if "zoarbathroomrental.com" not in body_preview:
                    body_preview += "... [Kai | (424) 235-8979 | zoarbathroomrental.com]"
                lines.append(
                    "%d. **%s** (%s, %s)\n"
                    "   Subject: *%s*\n"
                    "   Preview: %s\n" % (
                        i, v["name"], v.get("category", ""), v.get("city", ""),
                        email["subject"], body_preview,
                    )
                )
        return {"content": "\n".join(lines), "agent": "vendor_preview", "model": "system"}

    async def _vendor_test_batch(self) -> Dict[str, Any]:
        """Create campaign + send 20 test emails to Kai."""
        from core.email_campaign import create_campaign, send_test_batch

        # Create a campaign if none exists
        campaign = create_campaign(
            name="Partnership Outreach",
            target_tiers=[1, 2, 3],
            min_score=30,
        )

        result = await send_test_batch(campaign["campaign_id"], count=20)

        if result.get("ok"):
            content = (
                "**Test Batch Sent**\n\n"
                "Campaign #%d created: **%d** vendors queued\n"
                "Sent **%d** test emails to `%s`\n\n"
                "Check your inbox. When ready, say **\"start campaign\"** to begin live sending."
            ) % (
                campaign["campaign_id"], campaign["total_target"],
                result["sent_count"], result["test_email"],
            )
            if result.get("errors"):
                content += "\n\n%d errors:\n%s" % (
                    len(result["errors"]),
                    "\n".join("- %s: %s" % (e["vendor"], e["error"]) for e in result["errors"][:5]),
                )
        else:
            content = "Error sending test batch: %s" % result.get("error", "unknown")

        return {"content": content, "agent": "email_campaign", "model": "system"}

    async def _vendor_start_campaign(self, text: str) -> Dict[str, Any]:
        """Start campaign with Telegram approval."""
        from core.email_campaign import list_campaigns, get_campaign

        campaigns = list_campaigns()
        if not campaigns:
            return {"content": "No campaigns found. Run `send test batch` first to create one.",
                    "agent": "vendor", "model": "system"}

        # Find the most recent draft/testing campaign
        campaign = None
        for c in campaigns:
            if c["status"] in ("draft", "testing"):
                campaign = c
                break

        if not campaign:
            # Check if there's already an active one
            for c in campaigns:
                if c["status"] == "active":
                    return {"content": "Campaign #%d is already active. %d sent so far." % (
                        c["id"], c["sent_count"]), "agent": "vendor", "model": "system"}
            return {"content": "No pending campaigns found. Run `send test batch` first.",
                    "agent": "vendor", "model": "system"}

        # Send Telegram approval
        try:
            from scripts.notify_telegram import send_message
            msg = (
                "CAMPAIGN APPROVAL REQUEST\n"
                "Campaign: %s (#%d)\n"
                "Vendors queued: %d\n"
                "Daily limit: starts at 30, ramps to 100\n"
                "Reply APPROVE to start sending."
            ) % (campaign["name"], campaign["id"], campaign["total_target"])
            await send_message(msg)
        except Exception:
            pass

        return {
            "content": (
                "**Campaign #%d** ready: **%d** vendors queued.\n"
                "Telegram approval sent. Reply APPROVE in Telegram to begin sending.\n"
                "Warm-up: 30/day → 100/day over 8 days."
            ) % (campaign["id"], campaign["total_target"]),
            "agent": "email_campaign",
            "model": "system",
        }

    async def _vendor_campaign_status(self) -> Dict[str, Any]:
        """Show campaign stats."""
        from core.email_campaign import list_campaigns, get_campaign

        campaigns = list_campaigns()
        if not campaigns:
            return {"content": "No campaigns found.", "agent": "vendor", "model": "system"}

        lines = ["**Email Campaigns:**\n"]
        for c in campaigns[:5]:
            detail = get_campaign(c["id"])
            stats = detail.get("send_stats", {}) if detail else {}
            lines.append(
                "**#%d** %s — Status: **%s**\n"
                "  Target: %d | Sent: %d | Bounced: %d | Replied: %d | Queued: %d | Today: %d"
                % (
                    c["id"], c["name"], c["status"],
                    c["total_target"], stats.get("sent", 0),
                    stats.get("bounced", 0), stats.get("replied", 0),
                    stats.get("queued", 0),
                    detail.get("sent_today", 0) if detail else 0,
                )
            )
        return {"content": "\n".join(lines), "agent": "email_campaign", "model": "system"}

    async def _vendor_pause_campaign(self) -> Dict[str, Any]:
        from core.email_campaign import list_campaigns, pause_campaign
        for c in list_campaigns():
            if c["status"] == "active":
                pause_campaign(c["id"])
                return {"content": "Campaign #%d paused." % c["id"],
                        "agent": "email_campaign", "model": "system"}
        return {"content": "No active campaign to pause.", "agent": "vendor", "model": "system"}

    async def _vendor_resume_campaign(self) -> Dict[str, Any]:
        from core.email_campaign import list_campaigns, resume_campaign
        for c in list_campaigns():
            if c["status"] == "paused":
                resume_campaign(c["id"])
                return {"content": "Campaign #%d resumed." % c["id"],
                        "agent": "email_campaign", "model": "system"}
        return {"content": "No paused campaign to resume.", "agent": "vendor", "model": "system"}

    async def _handle_creative(self, text: str) -> Dict[str, Any]:
        """Route creative tasks — Division Two not yet built."""
        return {
            "content": "Creative tasks route to Division Two (not yet built). "
                       "Use /build to create it via the Build Pipeline.",
            "agent": "Router",
            "model": "",
        }

    async def _handle_revenue(self, text: str) -> Dict[str, Any]:
        """Route revenue experiment queries via SmartRouter + D3 context."""
        try:
            from core.division_three import get_division_three
            d3 = get_division_three()
            dashboard = d3.get_dashboard()
            d3_context = (
                "Division Three dashboard:\n"
                "Active experiments: %d\n"
                "Total experiments: %d\n"
                "Active personas: %d\n"
                "Revenue: $%.2f\n"
                "Trends today: %d\n"
                "By category: %s"
            ) % (
                dashboard["active_experiments"], dashboard["total_experiments"],
                dashboard["total_personas"], dashboard["total_revenue"],
                dashboard["trends_today"],
                ", ".join("%s: %d total/%d running" % (k, v["total"], v["running"])
                          for k, v in dashboard["by_category"].items()),
            )
        except Exception:
            d3_context = "Division Three data unavailable."

        from core.smart_router import get_smart_router
        return await get_smart_router().route(text, data_context=d3_context)

    async def _handle_data(self, text: str) -> Dict[str, Any]:
        """Query the database via SmartRouter with data context injection."""
        from core.smart_router import get_smart_router
        data_context = self._get_data_context()
        return await get_smart_router().route(text, data_context=data_context)

    async def _handle_system(self, text: str) -> Dict[str, Any]:
        """Handle system control commands."""
        tl = text.lower()

        # Health check
        if "health" in tl:
            try:
                from core.self_healing import get_self_healer
                result = await get_self_healer().run_full_check()
                status = "healthy" if result.get("healthy") else "ISSUES DETECTED"
                return {
                    "content": "System %s\nChecks: %d passed, %d failed" % (
                        status,
                        result.get("passed", 0),
                        result.get("failed", 0)),
                    "agent": "System Control",
                    "model": "",
                }
            except Exception:
                pass

        # Worker status
        if any(kw in tl for kw in ["worker", "process", "fleet"]):
            try:
                from scripts.process_manager import ProcessManager
                pm = ProcessManager()
                statuses = pm.get_status()
                alive = [s for s in statuses if s.get("actually_alive")]
                return {
                    "content": "Workers: %d alive / %d total\n%s" % (
                        len(alive), len(statuses),
                        "\n".join("- %s (tier %s): %s" % (
                            s["name"], s.get("tier", "?"),
                            "alive" if s.get("actually_alive") else "dead"
                        ) for s in statuses[:10])),
                    "agent": "System Control",
                    "model": "",
                }
            except Exception:
                pass

        return {
            "content": "System command received: %s\nProcessing..." % text[:100],
            "agent": "System Control",
            "model": "",
        }

    async def _handle_general(self, text: str) -> Dict[str, Any]:
        """Route to SmartRouter (ZAI default brain, free models only)."""
        from core.smart_router import get_smart_router
        return await get_smart_router().route(text)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _save_message(self, role: str, content: str, agent: str = "",
                      model: str = "", category: str = "general",
                      latency_ms: int = 0, build_id: Optional[int] = None,
                      build_status: str = "", source: str = "web") -> int:
        """Save a chat message to the database. Returns message ID."""
        now = datetime.utcnow().isoformat()
        conn = self._conn()
        try:
            cur = conn.execute(
                """INSERT INTO nexus_chat
                   (role, content, agent, model, category, latency_ms,
                    build_id, build_status, source, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (role, content, agent, model, category, latency_ms,
                 build_id, build_status, source, now),
            )
            conn.commit()
            return cur.lastrowid or 0
        finally:
            conn.close()

    def get_history(self, limit: int = 50) -> List[Dict]:
        """Get recent chat history."""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM nexus_chat ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            # Return in chronological order
            return [dict(r) for r in reversed(rows)]
        finally:
            conn.close()

    def get_snapshot(self) -> Dict[str, Any]:
        """Get system snapshot for the quick actions sidebar."""
        conn = self._conn()
        try:
            vendor_count = 0
            try:
                row = conn.execute("SELECT COUNT(*) as c FROM vendors").fetchone()
                vendor_count = row["c"] if row else 0
            except Exception:
                pass

            active_workers = 0
            try:
                row = conn.execute(
                    "SELECT COUNT(*) as c FROM managed_processes WHERE status = 'running'"
                ).fetchone()
                active_workers = row["c"] if row else 0
            except Exception:
                pass

            pending_approvals = 0
            try:
                row = conn.execute(
                    "SELECT COUNT(*) as c FROM message_approvals WHERE status = 'pending'"
                ).fetchone()
                pending_approvals = row["c"] if row else 0
            except Exception:
                pass

            active_builds = 0
            try:
                row = conn.execute(
                    "SELECT COUNT(*) as c FROM build_plans WHERE status NOT IN ('merged','approved','rejected','failed')"
                ).fetchone()
                active_builds = row["c"] if row else 0
            except Exception:
                pass

            return {
                "vendor_count": vendor_count,
                "active_workers": active_workers,
                "pending_approvals": pending_approvals,
                "active_builds": active_builds,
            }
        finally:
            conn.close()

    def _get_data_context(self) -> str:
        """Build rich database context for data query agent."""
        lines = []
        conn = self._conn()
        try:
            # Vendor stats
            try:
                row = conn.execute("SELECT COUNT(*) as c FROM vendors").fetchone()
                lines.append("Total vendors: %d" % (row["c"] if row else 0))
                # By zone
                rows = conn.execute(
                    "SELECT zone, COUNT(*) as c FROM vendors WHERE zone != '' "
                    "GROUP BY zone ORDER BY c DESC LIMIT 10"
                ).fetchall()
                if rows:
                    lines.append("Vendors by zone: %s" % ", ".join(
                        "%s=%d" % (r["zone"], r["c"]) for r in rows))
            except Exception:
                pass

            # Lead stats
            try:
                row = conn.execute("SELECT COUNT(*) as c FROM leads").fetchone()
                lines.append("Total leads: %d" % (row["c"] if row else 0))
                # By status
                rows = conn.execute(
                    "SELECT status, COUNT(*) as c FROM leads GROUP BY status"
                ).fetchall()
                if rows:
                    lines.append("Leads by status: %s" % ", ".join(
                        "%s=%d" % (r["status"] or "unknown", r["c"]) for r in rows))
                # Recent leads
                rows = conn.execute(
                    "SELECT name, event_type, status, created_at FROM leads "
                    "ORDER BY id DESC LIMIT 5"
                ).fetchall()
                if rows:
                    lines.append("Recent leads: %s" % "; ".join(
                        "%s (%s, %s)" % (r["name"] or "?", r["event_type"] or "?", r["status"] or "?")
                        for r in rows))
            except Exception:
                pass

            # Approval queue
            try:
                rows = conn.execute(
                    "SELECT task_type, status, COUNT(*) as c FROM fleet_tasks "
                    "WHERE status IN ('pending','claimed') "
                    "GROUP BY task_type, status ORDER BY c DESC"
                ).fetchall()
                if rows:
                    lines.append("Pending fleet tasks: %s" % ", ".join(
                        "%s (%s)=%d" % (r["task_type"], r["status"], r["c"]) for r in rows))
                else:
                    lines.append("Pending fleet tasks: 0")
            except Exception:
                pass

            # Message approvals
            try:
                row = conn.execute(
                    "SELECT COUNT(*) as c FROM message_approvals WHERE status = 'pending'"
                ).fetchone()
                lines.append("Pending message approvals: %d" % (row["c"] if row else 0))
                rows = conn.execute(
                    "SELECT channel, COUNT(*) as c FROM message_approvals "
                    "WHERE status = 'pending' GROUP BY channel"
                ).fetchall()
                if rows:
                    lines.append("Approvals by channel: %s" % ", ".join(
                        "%s=%d" % (r["channel"], r["c"]) for r in rows))
            except Exception:
                pass

            # Autonomous tasks pending
            try:
                row = conn.execute(
                    "SELECT COUNT(*) as c FROM autonomous_tasks WHERE status = 'pending'"
                ).fetchone()
                if row and row["c"] > 0:
                    lines.append("Pending autonomous tasks: %d" % row["c"])
            except Exception:
                pass

            # Build stats
            try:
                row = conn.execute("SELECT COUNT(*) as c FROM build_plans").fetchone()
                lines.append("Total builds: %d" % (row["c"] if row else 0))
                rows = conn.execute(
                    "SELECT status, COUNT(*) as c FROM build_plans GROUP BY status"
                ).fetchall()
                if rows:
                    lines.append("Builds by status: %s" % ", ".join(
                        "%s=%d" % (r["status"], r["c"]) for r in rows))
            except Exception:
                pass

            # Fleet task stats
            try:
                row = conn.execute(
                    "SELECT COUNT(*) as c FROM fleet_tasks WHERE status = 'completed'"
                ).fetchone()
                lines.append("Completed fleet tasks: %d" % (row["c"] if row else 0))
            except Exception:
                pass
        finally:
            conn.close()

        return "\n".join(lines) if lines else "No data available."

    @staticmethod
    def _extract_json(text: str) -> Optional[Dict]:
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    pass
        return None


# ── Singleton ────────────────────────────────────────────────────────────────

_router = None  # type: Optional[NexusRouter]


def get_nexus_router() -> NexusRouter:
    global _router
    if _router is None:
        _router = NexusRouter()
    return _router
