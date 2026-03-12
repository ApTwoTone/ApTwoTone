#!/usr/bin/env python3
"""
Vendor Data Enrichment — Visit vendor websites to find email addresses and contact names.

Uses Playwright headless browser to scrape vendor websites for:
- Email addresses (regex extraction)
- Contact names (from About/Contact pages)

Updates vendor records in ~/.nexus/memory.db with found data.

Usage:
    python3 scripts/enrich_vendors.py              # Run enrichment batch
    python3 scripts/enrich_vendors.py --limit 20   # Limit batch size
"""
import asyncio
import json
import re
import sqlite3
import sys
import argparse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DB_PATH = Path.home() / ".nexus" / "memory.db"
REPORT_PATH = Path(__file__).parent.parent / "ENRICHMENT_REPORT.md"

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
NAME_TITLE_RE = re.compile(
    r"(?:owner|founder|manager|coordinator|director|president|ceo|proprietor)"
    r"[:\s,\-]+([A-Z][a-z]+ [A-Z][a-z]+)",
    re.IGNORECASE
)
# Also match "Name, Title" pattern
NAME_BEFORE_TITLE_RE = re.compile(
    r"([A-Z][a-z]+ [A-Z][a-z]+)[,\s\-]+(?:owner|founder|manager|coordinator|director|president|ceo|proprietor)",
    re.IGNORECASE
)

# Emails to ignore (generic, not useful)
IGNORE_EMAILS = {
    "info@example.com", "test@test.com", "email@email.com",
    "name@domain.com", "your@email.com", "example@example.com",
}


def get_vendors_to_enrich(limit=50):
    """Get vendors with website but no email, not yet enriched."""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("""
        SELECT id, name, website FROM vendors
        WHERE (email IS NULL OR email = '')
        AND website IS NOT NULL AND website != ''
        AND (enriched IS NULL OR enriched = 0)
        LIMIT ?
    """, (limit,))
    vendors = c.fetchall()
    conn.close()
    return vendors


def update_vendor(vendor_id, email=None, contact_name=None, enriched=True):
    """Update vendor record with found data."""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    if email:
        c.execute("UPDATE vendors SET email = ?, enriched = 1 WHERE id = ?", (email, vendor_id))
    if contact_name:
        c.execute("UPDATE vendors SET contact_name = ? WHERE id = ?", (contact_name, vendor_id))
    if enriched and not email:
        # Mark as enriched even if nothing found (so we don't retry)
        c.execute("UPDATE vendors SET enriched = 1 WHERE id = ?", (vendor_id,))
    conn.commit()
    conn.close()


def extract_emails(text):
    """Extract valid emails from text, filtering junk."""
    found = set(EMAIL_RE.findall(text.lower()))
    # Filter out image extensions, CSS files, JS files
    filtered = set()
    for email in found:
        if email in IGNORE_EMAILS:
            continue
        domain = email.split("@")[1]
        # Skip if domain looks like a file extension
        if domain.endswith((".png", ".jpg", ".gif", ".css", ".js", ".svg")):
            continue
        # Skip if email is too long (likely a URL artifact)
        if len(email) > 60:
            continue
        filtered.add(email)
    return filtered


def extract_contact_name(text):
    """Try to extract a contact name near title keywords."""
    # Try "Title: Name" pattern
    match = NAME_TITLE_RE.search(text)
    if match:
        return match.group(1).strip()

    # Try "Name, Title" pattern
    match = NAME_BEFORE_TITLE_RE.search(text)
    if match:
        return match.group(1).strip()

    return None


async def enrich_vendor(page, vendor_id, name, website):
    """Visit vendor website and extract email/contact name."""
    result = {"id": vendor_id, "name": name, "website": website,
              "email": None, "contact_name": None, "status": "unknown"}

    # Ensure URL has protocol
    url = website.strip()
    if not url.startswith("http"):
        url = "https://" + url

    try:
        # Visit homepage
        response = await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        if not response or response.status >= 400:
            result["status"] = "unreachable"
            return result

        await asyncio.sleep(1)
        body_text = await page.inner_text("body")
        page_content = await page.content()

        # Extract emails from homepage
        emails = extract_emails(body_text) | extract_emails(page_content)
        contact_name = extract_contact_name(body_text)

        # Try Contact or About page
        for link_text in ["Contact", "About", "About Us", "Contact Us", "Meet the Team", "Our Team"]:
            try:
                link = await page.query_selector(
                    f"a:has-text('{link_text}'), "
                    f"a[href*='contact'], a[href*='about']"
                )
                if link:
                    href = await link.get_attribute("href")
                    if href and href != "#" and "javascript:" not in href:
                        await link.click()
                        await asyncio.sleep(1.5)
                        sub_text = await page.inner_text("body")
                        sub_content = await page.content()
                        emails |= extract_emails(sub_text) | extract_emails(sub_content)
                        if not contact_name:
                            contact_name = extract_contact_name(sub_text)
                        break  # Only visit one sub-page
            except:
                continue

        if emails:
            # Prefer business-looking emails over generic ones
            best = None
            for email in emails:
                if any(word in email for word in ["info", "contact", "hello", "book", "event"]):
                    best = email
                    break
            if not best:
                best = sorted(emails)[0]
            result["email"] = best
            result["status"] = "email_found"
        elif contact_name:
            result["status"] = "name_only"
        else:
            result["status"] = "no_data"

        result["contact_name"] = contact_name

    except Exception as e:
        error_str = str(e)[:100]
        if "Timeout" in error_str or "timeout" in error_str:
            result["status"] = "timeout"
        else:
            result["status"] = f"error"

    return result


async def run_enrichment(batch_size=50):
    """Run enrichment on a batch of vendors."""
    from playwright.async_api import async_playwright

    vendors = get_vendors_to_enrich(batch_size)
    if not vendors:
        print("[ENRICH] No vendors to enrich (all have emails or already enriched)")
        return []

    print(f"[ENRICH] Starting enrichment of {len(vendors)} vendors")
    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--remote-debugging-port=9224"]  # Different port from audit
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )
        page = await context.new_page()

        for i, (vid, name, website) in enumerate(vendors):
            print(f"[ENRICH] [{i+1}/{len(vendors)}] {name} ({website[:50]})...", end=" ", flush=True)
            result = await enrich_vendor(page, vid, name, website)

            if result["email"]:
                update_vendor(vid, email=result["email"], contact_name=result["contact_name"])
                print(f"EMAIL: {result['email']}", end="")
                if result["contact_name"]:
                    print(f" | NAME: {result['contact_name']}", end="")
                print()
            elif result["contact_name"]:
                update_vendor(vid, contact_name=result["contact_name"])
                print(f"NAME: {result['contact_name']}")
            else:
                update_vendor(vid, enriched=True)  # Mark as tried
                print(f"({result['status']})")

            results.append(result)

        await browser.close()

    # Summary
    emails_found = sum(1 for r in results if r["email"])
    names_found = sum(1 for r in results if r["contact_name"])
    unreachable = sum(1 for r in results if r["status"] in ("unreachable", "timeout", "error"))
    no_data = sum(1 for r in results if r["status"] == "no_data")

    print()
    print("=" * 60)
    print("ENRICHMENT SUMMARY")
    print("=" * 60)
    print(f"Vendors visited: {len(results)}")
    print(f"Emails found: {emails_found}")
    print(f"Contact names found: {names_found}")
    print(f"Unreachable/timeout: {unreachable}")
    print(f"No data found: {no_data}")
    print("=" * 60)

    return results


def write_report(results):
    """Write enrichment results to ENRICHMENT_REPORT.md."""
    emails_found = [r for r in results if r["email"]]
    names_found = [r for r in results if r["contact_name"]]
    unreachable = [r for r in results if r["status"] in ("unreachable", "timeout", "error")]

    report = f"""# ENRICHMENT REPORT

Generated by Nexus Auditor — {datetime.now().strftime("%Y-%m-%d %H:%M")}

---

## Summary
- Vendors visited: {len(results)}
- Emails found: {len(emails_found)}
- Contact names found: {len(names_found)}
- Unreachable/timeout: {len(unreachable)}

## Emails Found
"""
    for r in emails_found:
        report += f"- **{r['name']}** ({r['website'][:50]}): {r['email']}"
        if r["contact_name"]:
            report += f" (Contact: {r['contact_name']})"
        report += "\n"

    if names_found:
        report += "\n## Contact Names Found (no email)\n"
        for r in names_found:
            if not r["email"]:
                report += f"- **{r['name']}**: {r['contact_name']}\n"

    if unreachable:
        report += "\n## Unreachable Sites\n"
        for r in unreachable:
            report += f"- {r['name']} ({r['website'][:50]}): {r['status']}\n"

    with open(REPORT_PATH, "w") as f:
        f.write(report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    results = asyncio.run(run_enrichment(args.limit))
    if results:
        write_report(results)
        print(f"\nReport written to {REPORT_PATH}")
