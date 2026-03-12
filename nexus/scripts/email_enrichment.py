"""
Email Enrichment — Scrape vendor websites to find email addresses.

For vendors that have a website but no email, visits their site and extracts
email addresses from page text, mailto links, and contact pages.
Validates found emails with MX record checks.

Usage:
    python scripts/email_enrichment.py [--limit N] [--no-headless]
"""
import logging
import random
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

log = logging.getLogger("email_enrichment")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

DB_PATH = Path.home() / ".nexus" / "memory.db"

EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')

# Emails to skip — generic/system addresses
SKIP_PATTERNS = {
    "noreply@", "no-reply@", "support@google", "privacy@",
    "info@example", "admin@", "webmaster@", "abuse@", "postmaster@",
    "hostmaster@", "root@", "mailer-daemon@", "donotreply@",
    "sentry@", "error@", "alerts@", "notifications@",
    "@sentry.io", "@wix.com", "@squarespace.com", "@wordpress.com",
    "@godaddy.com", "@google.com", "@facebook.com",
}

CONTACT_PATHS = ["contact", "contact-us", "about", "about-us", "get-in-touch"]


def _should_skip_email(email):
    """Return True if this email should be filtered out."""
    el = email.lower()
    return any(skip in el for skip in SKIP_PATTERNS)


def _validate_mx(email):
    """Check if the email domain has valid MX records."""
    try:
        import dns.resolver
        domain = email.split("@")[1]
        dns.resolver.resolve(domain, "MX")
        return True
    except Exception:
        return False


def _extract_emails_from_page(page):
    """Extract email addresses from current page."""
    emails = set()

    # Get mailto links
    try:
        mailto_emails = page.eval_on_selector_all(
            'a[href^="mailto:"]',
            'els => els.map(e => e.href.replace("mailto:", "").split("?")[0])'
        )
        for e in mailto_emails:
            e = e.strip().lower()
            if e and "@" in e:
                emails.add(e)
    except Exception:
        pass

    # Get emails from page text
    try:
        text = page.inner_text("body", timeout=5000)
        for e in EMAIL_RE.findall(text):
            emails.add(e.lower())
    except Exception:
        pass

    return emails


def enrich_vendor_emails(limit=200, headless=True):
    """Find emails for vendors with websites but no email."""
    from playwright.sync_api import sync_playwright

    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row

    # Get vendors needing email enrichment
    rows = conn.execute(
        "SELECT id, name, website FROM vendors "
        "WHERE (email IS NULL OR email = '') AND website IS NOT NULL AND website != '' "
        "LIMIT ?",
        (limit,),
    ).fetchall()

    if not rows:
        log.info("No vendors need email enrichment")
        return 0

    log.info("Enriching %d vendors with website but no email", len(rows))
    found_count = 0
    failed_count = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        for i, row in enumerate(rows):
            vid = row["id"]
            name = row["name"]
            website = row["website"]

            if i > 0 and i % 10 == 0:
                log.info("Progress: %d/%d vendors processed, %d emails found", i, len(rows), found_count)

            # Normalize URL
            if not website.startswith("http"):
                website = "https://" + website

            found_emails = set()

            # Check homepage
            try:
                page.goto(website, timeout=10000, wait_until="domcontentloaded")
                time.sleep(1)
                found_emails.update(_extract_emails_from_page(page))
            except Exception:
                pass

            # Check contact pages
            if not found_emails:
                base = website.rstrip("/")
                for path in CONTACT_PATHS:
                    try:
                        page.goto("%s/%s" % (base, path), timeout=8000, wait_until="domcontentloaded")
                        time.sleep(0.5)
                        found_emails.update(_extract_emails_from_page(page))
                        if found_emails:
                            break
                    except Exception:
                        continue

            # Filter and validate
            valid_emails = [
                e for e in found_emails
                if not _should_skip_email(e) and _validate_mx(e)
            ]

            if valid_emails:
                email = valid_emails[0]
                conn.execute(
                    "UPDATE vendors SET email = ?, email_valid = 1 WHERE id = ?",
                    (email, vid),
                )
                conn.commit()
                found_count += 1
                log.info("  [%d] %s → %s", vid, name, email)
            else:
                failed_count += 1

            time.sleep(random.uniform(2, 4))

        browser.close()

    conn.close()
    log.info("DONE: %d emails found, %d failed out of %d vendors", found_count, failed_count, len(rows))
    return found_count


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Enrich vendors with emails from their websites")
    parser.add_argument("--limit", type=int, default=200, help="Max vendors to process")
    parser.add_argument("--no-headless", action="store_true")
    args = parser.parse_args()

    count = enrich_vendor_emails(limit=args.limit, headless=not args.no_headless)
    print("Email enrichment complete: %d emails found" % count)
