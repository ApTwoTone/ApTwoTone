#!/usr/bin/env python3
"""
Lead Discovery Daemon — Continuously discovers new vendors, enriches emails,
and feeds the outreach pipeline. Runs 24/7 with configurable cycle intervals.

Orchestrates existing modules:
  - core/vendor_research.py  (AI-powered vendor discovery via Groq)
  - scripts/google_maps_scraper.py  (Playwright Google Maps scraper)
  - scripts/craigslist_scraper.py  (BeautifulSoup Craigslist scraper)
  - scripts/email_enrichment.py  (Email extraction + MX validation)
  - core/vendor_enrichment.py  (Scoring + campaign eligibility)

Usage:
    python scripts/lead_discovery_daemon.py              # Run forever (30-min cycles)
    python scripts/lead_discovery_daemon.py --once       # Single cycle then exit
    python scripts/lead_discovery_daemon.py --interval 15 --queries 8
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure project root is on path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from core.vendor_db import bulk_save_vendors, get_vendor_stats
from core.vendor_research import VendorResearcher, CATEGORY_QUERIES, DEFAULT_LOCATIONS
from core.vendor_enrichment import score_all_vendors

# Optional imports — graceful fallback if modules missing
try:
    from core.nexus_coordination import log_event
except ImportError:
    def log_event(*args, **kwargs):
        return 0

# ── Paths ────────────────────────────────────────────────────────────────────

DB_PATH = Path.home() / ".nexus" / "memory.db"
LOG_DIR = Path.home() / ".nexus" / "logs"
PID_DIR = Path.home() / ".nexus" / "pids"
VENV_PYTHON = str(ROOT_DIR / "venv" / "bin" / "python3")

# ── Logging ──────────────────────────────────────────────────────────────────

LOG_DIR.mkdir(parents=True, exist_ok=True)
PID_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [DiscoveryDaemon] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(LOG_DIR / "discovery_daemon.log"), mode="a"),
    ],
)
log = logging.getLogger("discovery_daemon")

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_INTERVAL = 30       # minutes between cycles
DEFAULT_QUERIES = 12        # AI queries per cycle
TELEGRAM_COOLDOWN = 900     # 15 min between Telegram messages
SUBPROCESS_TIMEOUT = 600    # 10 min max per subprocess

# Categories to skip in discovery
BLACKLISTED_CATEGORIES = {
    "film_production", "grip", "lighting", "lighting_design",
    "construction", "bathroom_rental",
    # Added 2026-03-08: expanded to match vendor_blacklist.py
    "stage_rental", "coffee_cart", "community_center", "restaurant",
    "jewelry", "lighting_equipment", "porta_potty_competitor",
    "security_service", "beauty_services", "character_company",
    "limo_service", "valet_service", "valet",
}


# ── Query Roster ─────────────────────────────────────────────────────────────

def _build_query_roster() -> List[Tuple[str, str]]:
    """Build (category, location) pairs from existing constants, skipping blacklisted."""
    roster = []
    for category in CATEGORY_QUERIES:
        if category in BLACKLISTED_CATEGORIES:
            continue
        for location in DEFAULT_LOCATIONS:
            roster.append((category, location))
    return roster


# ── Daemon Class ─────────────────────────────────────────────────────────────

class LeadDiscoveryDaemon:

    def __init__(self, interval_minutes: int = DEFAULT_INTERVAL,
                 queries_per_cycle: int = DEFAULT_QUERIES):
        self._running = True
        self._cycle_count = 0
        self._query_index = 0
        self._last_telegram_time = 0.0
        self._interval = interval_minutes
        self._queries_per_cycle = queries_per_cycle
        self._roster = _build_query_roster()
        self._researcher = VendorResearcher()

        log.info("Daemon initialized: %d query pairs in roster, %d per cycle, %d-min interval",
                 len(self._roster), self._queries_per_cycle, self._interval)

    # ── Signal handling ──────────────────────────────────────────────────

    def _handle_signal(self, signum, frame):
        log.info("Received signal %d — shutting down gracefully...", signum)
        self._running = False

    # ── Process registration ─────────────────────────────────────────────

    def _register_process(self, status: str = "running"):
        """Register/update in managed_processes table."""
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute(
                """INSERT INTO managed_processes (name, pid, process_type, tier, status, started_at, last_heartbeat)
                   VALUES ('discovery_daemon', ?, 'daemon', 3, ?, datetime('now'), datetime('now'))
                   ON CONFLICT(name) DO UPDATE SET
                     pid = ?, status = ?, last_heartbeat = datetime('now')""",
                (os.getpid(), status, os.getpid(), status),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            log.warning("Process registration failed: %s", e)

    def _write_heartbeat(self):
        """Update heartbeat timestamp."""
        self._register_process("running")

    # ── Subprocess runner ────────────────────────────────────────────────

    def _run_subprocess(self, script_args: List[str],
                        timeout: int = SUBPROCESS_TIMEOUT) -> Tuple[bool, str]:
        """Run a script as a subprocess. Returns (success, last 500 chars of output)."""
        cmd = [VENV_PYTHON] + script_args
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(ROOT_DIR),
            )
            output = (result.stdout or "") + (result.stderr or "")
            return result.returncode == 0, output[-500:]
        except subprocess.TimeoutExpired:
            return False, "timeout after %ds" % timeout
        except Exception as e:
            return False, str(e)

    # ── Telegram (rate-limited) ──────────────────────────────────────────

    def _notify_telegram(self, message: str):
        """Send Telegram notification with 15-min cooldown."""
        now = time.time()
        if now - self._last_telegram_time < TELEGRAM_COOLDOWN:
            log.info("Telegram suppressed (cooldown)")
            return
        try:
            subprocess.run(
                [VENV_PYTHON, str(ROOT_DIR / "scripts" / "notify_telegram.py"), message],
                timeout=15,
                cwd=str(ROOT_DIR),
                capture_output=True,
            )
            self._last_telegram_time = now
            log.info("Telegram notification sent")
        except Exception as e:
            log.error("Telegram failed: %s", e)

    # ── Phase 1: AI Discovery ────────────────────────────────────────────

    def _run_ai_discovery(self) -> Dict[str, int]:
        """Run AI discovery for the next batch of (category, location) pairs."""
        queries = []
        for _ in range(self._queries_per_cycle):
            queries.append(self._roster[self._query_index % len(self._roster)])
            self._query_index += 1

        categories_hit = {}
        for cat, _ in queries:
            categories_hit[cat] = categories_hit.get(cat, 0) + 1

        log.info("AI Discovery: %d queries — %s",
                 len(queries),
                 ", ".join("%s x%d" % (c, n) for c, n in categories_hit.items()))

        async def _discover():
            all_vendors = []  # type: List[Dict[str, Any]]
            for category, location in queries:
                if not self._running:
                    break
                try:
                    results = await self._researcher.search_ai(category, location)
                    for r in results:
                        r.setdefault("category", category)
                        r.setdefault("source", "ai_research")
                    all_vendors.extend(results)
                    log.info("  %s / %s -> %d vendors", category, location, len(results))
                except Exception as e:
                    log.error("  %s / %s FAILED: %s", category, location, e)
            return all_vendors

        try:
            vendors = asyncio.run(_discover())
        except Exception as e:
            log.error("AI discovery async error: %s", e)
            return {"created": 0, "updated": 0, "skipped": 0, "rejected": 0, "errors": 1}

        if not vendors:
            return {"created": 0, "updated": 0, "skipped": 0, "rejected": 0, "errors": 0}

        log.info("Saving %d AI-discovered vendors...", len(vendors))
        return bulk_save_vendors(vendors)

    # ── Phase 2: Google Maps ─────────────────────────────────────────────

    def _run_google_maps(self) -> str:
        """Run Google Maps scraper as subprocess (every 3rd cycle)."""
        log.info("Google Maps scraper starting (subprocess)...")
        ok, output = self._run_subprocess([
            str(ROOT_DIR / "scripts" / "google_maps_scraper.py"),
            "--headless",
            "--max-queries", "10",
        ])
        status = "OK" if ok else "FAILED"
        log.info("Google Maps scraper %s", status)
        return output

    # ── Phase 3: Craigslist ──────────────────────────────────────────────

    def _run_craigslist(self) -> str:
        """Run Craigslist scraper as subprocess (every 2nd cycle)."""
        log.info("Craigslist scraper starting (subprocess)...")
        ok, output = self._run_subprocess([
            str(ROOT_DIR / "scripts" / "craigslist_scraper.py"),
        ])
        status = "OK" if ok else "FAILED"
        log.info("Craigslist scraper %s", status)
        return output

    # ── Phase 4: Email Enrichment ────────────────────────────────────────

    def _run_email_enrichment(self) -> str:
        """Run email enrichment as subprocess."""
        log.info("Email enrichment starting (subprocess)...")
        ok, output = self._run_subprocess([
            str(ROOT_DIR / "scripts" / "email_enrichment.py"),
            "--limit", "50",
        ])
        status = "OK" if ok else "FAILED"
        log.info("Email enrichment %s", status)
        return output

    # ── Phase 5: Scoring ─────────────────────────────────────────────────

    # ── Phase 6: Event Lead Discovery ────────────────────────────────────

    # Keywords that signal a genuine event bathroom need
    _LEAD_KEYWORDS = [
        "bathroom", "restroom", "portable bathroom", "restroom trailer",
        "outdoor wedding", "backyard wedding", "outdoor event", "no bathroom",
        "porta potty alternative", "quinceanera", "quinceañera",
        "100 guests", "150 guests", "200 guests", "outdoor venue",
        "corporate event", "backyard party",
    ]

    def _run_event_lead_discovery(self) -> Dict[str, int]:
        """
        Scan Craigslist event services section and public event listings for
        people who need a bathroom trailer. Save matches to the leads table
        and alert Kai for any with score >= 60.
        """
        import re
        try:
            import requests as _req
            from bs4 import BeautifulSoup
        except ImportError:
            log.warning("Phase 6: requests/bs4 not available — skipping")
            return {"found": 0, "saved": 0, "alerted": 0}

        found = 0
        saved = 0
        alerted = 0

        # Craigslist event services — Los Angeles + San Fernando Valley
        cl_targets = [
            "https://losangeles.craigslist.org/search/evs",
            "https://sfv.craigslist.org/search/evs",
        ]
        raw_posts: List[Dict] = []

        for url in cl_targets:
            try:
                resp = _req.get(url, timeout=15, headers={"User-Agent": "Nexus/1.0"})
                if resp.status_code != 200:
                    continue
                soup = BeautifulSoup(resp.text, "html.parser")
                for item in soup.select("li.cl-search-result")[:40]:
                    title_el = item.select_one("a.cl-app-anchor span.label")
                    link_el = item.select_one("a.cl-app-anchor")
                    if not title_el or not link_el:
                        continue
                    title = title_el.get_text(strip=True)
                    link = link_el.get("href", "")
                    tl = title.lower()
                    if any(kw in tl for kw in self._LEAD_KEYWORDS):
                        raw_posts.append({"title": title, "url": link, "source": "craigslist_evs"})
                        found += 1
            except Exception as e:
                log.warning("Phase 6 Craigslist (%s) failed: %s", url, e)

        if not raw_posts:
            log.info("Phase 6: no event lead signals found")
            return {"found": 0, "saved": 0, "alerted": 0}

        # Save matches to leads table and score them
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        try:
            for post in raw_posts:
                # Avoid duplicates — check source URL
                existing = conn.execute(
                    "SELECT id FROM leads WHERE notes LIKE ? LIMIT 1",
                    ("%%%s%%" % post["url"][:80],),
                ).fetchone()
                if existing:
                    continue

                now_iso = datetime.now(timezone.utc).isoformat()
                try:
                    cur = conn.execute(
                        """INSERT INTO leads
                           (name, source, status, notes, created_at, updated_at, requires_manual_approval)
                           VALUES (?, ?, 'new', ?, ?, ?, 1)""",
                        (
                            post["title"][:120],
                            post["source"],
                            "Auto-discovered via Phase 6 event scan. URL: " + post["url"],
                            now_iso,
                            now_iso,
                        ),
                    )
                    conn.commit()
                    lead_id = cur.lastrowid
                    saved += 1
                    log.info("Phase 6: saved lead #%d — %s", lead_id, post["title"][:60])
                except Exception as e:
                    log.warning("Phase 6: failed to save lead '%s': %s", post["title"][:60], e)
                    continue

                # Score the new lead
                try:
                    from core.lead_scoring import LeadScorer
                    scorer = LeadScorer()
                    score_result = scorer.score_lead(lead_id)
                    score = score_result.get("score", 0) if isinstance(score_result, dict) else 0
                    if score >= 60:
                        alerted += 1
                        self._notify_telegram(
                            "New event lead found (score %d)\n%s\n%s" % (
                                score, post["title"][:80], post["url"]
                            )
                        )
                except Exception as e:
                    log.warning("Phase 6: scoring failed for lead #%d: %s", lead_id, e)
        finally:
            conn.close()

        log.info("Phase 6 complete: %d signals found, %d saved, %d high-score alerts", found, saved, alerted)
        return {"found": found, "saved": saved, "alerted": alerted}

    def _run_scoring(self) -> Optional[Dict]:
        """Re-score all vendors for campaign eligibility."""
        log.info("Scoring vendors...")
        try:
            result = score_all_vendors()
            log.info("Scoring complete")
            return result
        except Exception as e:
            log.error("Scoring failed: %s", e)
            return None

    # ── Full Cycle ───────────────────────────────────────────────────────

    def run_cycle(self) -> Dict[str, Any]:
        """Execute one complete discovery cycle."""
        self._cycle_count += 1
        cycle = self._cycle_count
        start = time.time()

        log.info("=" * 60)
        log.info("DISCOVERY CYCLE #%d — %s", cycle,
                 datetime.now().strftime("%Y-%m-%d %H:%M"))
        log.info("=" * 60)

        results = {
            "cycle": cycle,
            "ai": {"created": 0, "updated": 0, "skipped": 0, "rejected": 0, "errors": 0},
            "google_maps": "skipped",
            "craigslist": "skipped",
            "enrichment": "skipped",
            "event_leads": {"found": 0, "saved": 0, "alerted": 0},
        }

        # Phase 1: AI Discovery (every cycle)
        try:
            results["ai"] = self._run_ai_discovery()
        except Exception as e:
            log.error("Phase 1 (AI) error: %s", e)

        if not self._running:
            return results

        # Phase 2: Google Maps (every 3rd cycle)
        if cycle % 3 == 0:
            try:
                results["google_maps"] = self._run_google_maps()
            except Exception as e:
                log.error("Phase 2 (Google Maps) error: %s", e)
                results["google_maps"] = "error: %s" % e

        if not self._running:
            return results

        # Phase 3: Craigslist (every 2nd cycle)
        if cycle % 2 == 0:
            try:
                results["craigslist"] = self._run_craigslist()
            except Exception as e:
                log.error("Phase 3 (Craigslist) error: %s", e)
                results["craigslist"] = "error: %s" % e

        if not self._running:
            return results

        # Phase 4: Email Enrichment (every cycle)
        try:
            results["enrichment"] = self._run_email_enrichment()
        except Exception as e:
            log.error("Phase 4 (Enrichment) error: %s", e)
            results["enrichment"] = "error: %s" % e

        if not self._running:
            return results

        # Phase 5: Scoring
        try:
            self._run_scoring()
        except Exception as e:
            log.error("Phase 5 (Scoring) error: %s", e)

        if not self._running:
            return results

        # Phase 6: Event Lead Discovery (every 4th cycle)
        results["event_leads"] = {"found": 0, "saved": 0, "alerted": 0}
        if cycle % 4 == 0:
            try:
                results["event_leads"] = self._run_event_lead_discovery()
            except Exception as e:
                log.error("Phase 6 (Event Leads) error: %s", e)
                results["event_leads"] = {"error": str(e)}

        # Stats + Logging + Telegram
        elapsed = time.time() - start
        ai = results["ai"]
        new_created = ai.get("created", 0)

        try:
            stats = get_vendor_stats()
            total = stats.get("total", 0)
            eligible = stats.get("campaign_eligible", 0)
        except Exception:
            total = "?"
            eligible = "?"

        summary = (
            "CYCLE #%d COMPLETE (%ds)\n"
            "AI: %d new, %d updated, %d skipped, %d rejected\n"
            "Google Maps: %s | Craigslist: %s\n"
            "Email enrichment: %s\n"
            "DB total: %s | Campaign eligible: %s"
        ) % (
            cycle, int(elapsed),
            ai.get("created", 0), ai.get("updated", 0),
            ai.get("skipped", 0), ai.get("rejected", 0),
            "ran" if cycle % 3 == 0 else "skipped",
            "ran" if cycle % 2 == 0 else "skipped",
            "ran" if results["enrichment"] != "skipped" else "skipped",
            total, eligible,
        )

        log.info(summary.replace("\n", " | "))

        # Log event
        try:
            log_event("DISCOVERY_CYCLE", {
                "cycle": cycle,
                "new_vendors": new_created,
                "ai_results": ai,
                "elapsed_seconds": int(elapsed),
            }, source="discovery_daemon", push_alert=False)
        except Exception as e:
            log.warning("log_event failed: %s", e)

        # Telegram (only if we found new vendors)
        if new_created > 0:
            self._notify_telegram(
                "Discovery cycle #%d: %d new vendors\n"
                "Total: %s | Eligible: %s\n"
                "Next cycle in %d min" % (cycle, new_created, total, eligible, self._interval)
            )

        self._write_heartbeat()
        return results

    # ── Main Loop ────────────────────────────────────────────────────────

    def run(self):
        """Main daemon loop — runs forever until SIGTERM/SIGINT."""
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        # Write PID file
        pid_file = PID_DIR / "discovery_daemon.pid"
        pid_file.write_text(str(os.getpid()))

        self._register_process("running")
        log.info("Discovery daemon started (PID %d)", os.getpid())

        try:
            from core.agent_runtime import AgentRuntime
            self._agent = AgentRuntime('lead_discovery', 'scraper', 'lead_generation')
            self._agent.start()
        except Exception:
            self._agent = None

        try:
            while self._running:
                if self._agent:
                    self._agent.heartbeat(
                        f"Cycle {self._cycle_count + 1}: discovering vendors "
                        f"({self._queries_per_cycle} queries)"
                    )
                self.run_cycle()

                if not self._running:
                    break

                # Sleep in 1-second increments for responsive shutdown
                wait_until = time.time() + self._interval * 60
                log.info("Next cycle in %d minutes...", self._interval)
                while self._running and time.time() < wait_until:
                    time.sleep(1)
        except KeyboardInterrupt:
            log.info("KeyboardInterrupt — stopping")
        finally:
            self._register_process("stopped")
            if self._agent:
                self._agent.stop()
            try:
                pid_file.unlink(missing_ok=True)
            except Exception:
                pass
            log.info("Discovery daemon stopped")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Nexus Lead Discovery Daemon")
    parser.add_argument("--once", action="store_true",
                        help="Run a single cycle then exit")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL,
                        help="Minutes between cycles (default: %d)" % DEFAULT_INTERVAL)
    parser.add_argument("--queries", type=int, default=DEFAULT_QUERIES,
                        help="AI queries per cycle (default: %d)" % DEFAULT_QUERIES)
    args = parser.parse_args()

    daemon = LeadDiscoveryDaemon(
        interval_minutes=args.interval,
        queries_per_cycle=args.queries,
    )

    if args.once:
        daemon.run_cycle()
    else:
        daemon.run()


if __name__ == "__main__":
    main()
