"""
NEXUS VENDOR DISCOVERY PIPELINE — Master Orchestrator

Runs all discovery sources in order, deduplicates, scores, enriches emails,
and sends a Telegram summary.

Usage:
    python scripts/run_all_discovery.py [--skip-gmaps] [--skip-craigslist] [--skip-yelp] [--skip-dirs] [--skip-email]
"""
import json
import logging
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

log = logging.getLogger("discovery_pipeline")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"


def _send_telegram(message):
    """Send a Telegram notification."""
    try:
        config = json.load(open(CONFIG_PATH))
        token = config.get("telegram_token", "")
        chat_id = config.get("telegram_chat_ids", ["8540603351"])[0]
        if not token:
            log.warning("No Telegram token configured")
            return
        url = "https://api.telegram.org/bot%s/sendMessage" % token
        data = json.dumps({"chat_id": str(chat_id), "text": message[:4000]}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log.error("Telegram send failed: %s", e)


def _get_stats():
    """Get current vendor database statistics."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    stats = {}
    stats["total"] = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
    stats["with_email"] = conn.execute(
        "SELECT COUNT(*) FROM vendors WHERE email IS NOT NULL AND email != ''"
    ).fetchone()[0]
    stats["with_phone"] = conn.execute(
        "SELECT COUNT(*) FROM vendors WHERE phone IS NOT NULL AND phone != ''"
    ).fetchone()[0]
    stats["with_website"] = conn.execute(
        "SELECT COUNT(*) FROM vendors WHERE website IS NOT NULL AND website != ''"
    ).fetchone()[0]
    stats["campaign_eligible"] = conn.execute(
        "SELECT COUNT(*) FROM vendors WHERE campaign_eligible = 1"
    ).fetchone()[0]

    # By source
    rows = conn.execute(
        "SELECT source, COUNT(*) FROM vendors GROUP BY source ORDER BY COUNT(*) DESC"
    ).fetchall()
    stats["by_source"] = {r[0]: r[1] for r in rows}

    # By category (top 10)
    rows = conn.execute(
        "SELECT category, COUNT(*) FROM vendors GROUP BY category ORDER BY COUNT(*) DESC LIMIT 10"
    ).fetchall()
    stats["by_category"] = {r[0]: r[1] for r in rows}

    conn.close()
    return stats


def run_pipeline(skip_gmaps=False, skip_craigslist=False, skip_yelp=False,
                 skip_dirs=False, skip_email=False):
    """Run the full vendor discovery pipeline."""
    start_time = time.time()
    results = {}

    # Get initial count
    initial_stats = _get_stats()
    log.info("Starting discovery. Current vendor count: %d", initial_stats["total"])

    # ── Source 1: Google Maps ────────────────────────────────────────────
    if not skip_gmaps:
        log.info("=" * 60)
        log.info("SOURCE 1: GOOGLE MAPS")
        log.info("=" * 60)
        try:
            from scripts.google_maps_scraper import scrape_google_maps
            count = scrape_google_maps(headless=True)
            results["Google Maps"] = {"count": count, "status": "OK"}
            log.info("Google Maps: %d vendors saved", count)
        except Exception as e:
            results["Google Maps"] = {"count": 0, "status": "FAILED: %s" % str(e)[:100]}
            log.error("Google Maps failed: %s", e)
    else:
        results["Google Maps"] = {"count": 0, "status": "SKIPPED"}

    # ── Source 2: Craigslist ─────────────────────────────────────────────
    if not skip_craigslist:
        log.info("=" * 60)
        log.info("SOURCE 2: CRAIGSLIST")
        log.info("=" * 60)
        try:
            from scripts.craigslist_scraper import scrape_craigslist
            count = scrape_craigslist()
            results["Craigslist"] = {"count": count, "status": "OK"}
            log.info("Craigslist: %d vendors saved", count)
        except Exception as e:
            results["Craigslist"] = {"count": 0, "status": "FAILED: %s" % str(e)[:100]}
            log.error("Craigslist failed: %s", e)
    else:
        results["Craigslist"] = {"count": 0, "status": "SKIPPED"}

    # ── Source 3: Yelp ───────────────────────────────────────────────────
    if not skip_yelp:
        log.info("=" * 60)
        log.info("SOURCE 3: YELP")
        log.info("=" * 60)
        try:
            from scripts.yelp_scraper import scrape_yelp
            count = scrape_yelp(headless=True)
            results["Yelp"] = {"count": count, "status": "OK"}
            log.info("Yelp: %d vendors saved", count)
        except Exception as e:
            results["Yelp"] = {"count": 0, "status": "FAILED: %s" % str(e)[:100]}
            log.error("Yelp failed: %s", e)
    else:
        results["Yelp"] = {"count": 0, "status": "SKIPPED"}

    # ── Source 4: Wedding Directories ────────────────────────────────────
    if not skip_dirs:
        log.info("=" * 60)
        log.info("SOURCE 4: WEDDING DIRECTORIES")
        log.info("=" * 60)
        try:
            from scripts.wedding_directory_scraper import scrape_wedding_directories
            count = scrape_wedding_directories(headless=True)
            results["Wedding Dirs"] = {"count": count, "status": "OK"}
            log.info("Wedding Directories: %d vendors saved", count)
        except Exception as e:
            results["Wedding Dirs"] = {"count": 0, "status": "FAILED: %s" % str(e)[:100]}
            log.error("Wedding Directories failed: %s", e)
    else:
        results["Wedding Dirs"] = {"count": 0, "status": "SKIPPED"}

    # ── Score all vendors ────────────────────────────────────────────────
    log.info("=" * 60)
    log.info("SCORING ALL VENDORS")
    log.info("=" * 60)
    try:
        from core.vendor_enrichment import score_all_vendors
        score_all_vendors()
        log.info("Scoring complete")
    except Exception as e:
        log.error("Scoring failed: %s", e)

    # ── Source 5: Email Enrichment ────────────────────────────────────────
    if not skip_email:
        log.info("=" * 60)
        log.info("SOURCE 5: EMAIL ENRICHMENT")
        log.info("=" * 60)
        try:
            from scripts.email_enrichment import enrich_vendor_emails
            count = enrich_vendor_emails(limit=300, headless=True)
            results["Email Enrichment"] = {"count": count, "status": "OK"}
            log.info("Email Enrichment: %d emails found", count)
        except Exception as e:
            results["Email Enrichment"] = {"count": 0, "status": "FAILED: %s" % str(e)[:100]}
            log.error("Email Enrichment failed: %s", e)
    else:
        results["Email Enrichment"] = {"count": 0, "status": "SKIPPED"}

    # ── Re-score after enrichment ────────────────────────────────────────
    try:
        from core.vendor_enrichment import score_all_vendors
        score_all_vendors()
    except Exception:
        pass

    # ── Final report ─────────────────────────────────────────────────────
    elapsed = time.time() - start_time
    final_stats = _get_stats()

    source_lines = []
    for name, data in results.items():
        source_lines.append("  %s: %d (%s)" % (name, data["count"], data["status"]))

    category_lines = []
    for cat, count in list(final_stats["by_category"].items())[:8]:
        category_lines.append("  %s: %d" % (cat, count))

    report = (
        "VENDOR DISCOVERY COMPLETE\n"
        "========================\n"
        "Total vendors: %d (was %d)\n"
        "With email: %d\n"
        "With phone: %d\n"
        "With website: %d\n"
        "Campaign eligible: %d\n\n"
        "Sources:\n%s\n\n"
        "Top categories:\n%s\n\n"
        "Time: %.0f minutes\n\n"
        "Facebook Groups: BLOCKED — waiting for cookies from Kai"
    ) % (
        final_stats["total"], initial_stats["total"],
        final_stats["with_email"],
        final_stats["with_phone"],
        final_stats["with_website"],
        final_stats["campaign_eligible"],
        "\n".join(source_lines),
        "\n".join(category_lines),
        elapsed / 60,
    )

    log.info("\n%s", report)
    _send_telegram(report)

    return final_stats


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run full vendor discovery pipeline")
    parser.add_argument("--skip-gmaps", action="store_true")
    parser.add_argument("--skip-craigslist", action="store_true")
    parser.add_argument("--skip-yelp", action="store_true")
    parser.add_argument("--skip-dirs", action="store_true")
    parser.add_argument("--skip-email", action="store_true")
    args = parser.parse_args()

    run_pipeline(
        skip_gmaps=args.skip_gmaps,
        skip_craigslist=args.skip_craigslist,
        skip_yelp=args.skip_yelp,
        skip_dirs=args.skip_dirs,
        skip_email=args.skip_email,
    )
