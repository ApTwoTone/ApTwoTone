#!/usr/bin/env python3
"""
Nexus Full Page Audit — Playwright-based audit of all frontend pages.

The Nexus frontend is an SPA at localhost:3000. All pages are accessed by
clicking sidebar buttons (no URL routing). This script navigates via sidebar
button clicks, measures load times, captures screenshots, logs console errors,
checks API calls, and runs page-specific tests.

Usage:
    python3 scripts/nexus_audit.py
"""
import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path

FRONTEND_URL = "http://localhost:3000"
SCREENSHOT_DIR = Path(__file__).parent.parent / "audit_screenshots" / "auditor"
RESULTS_FILE = Path(__file__).parent / "audit_results.json"

# Sidebar button labels in order (exact text match)
PAGES = [
    "Bookings",
    "Talk to Nexus",
    "Leads Pipeline",
    "Vendors",
    "Email Marketing",
    "Agent Control",
    "AI Fleet",
    "Ad Performance",
    "System Health",
    "Settings",
]


async def navigate_to_page(page, page_name):
    """Click the sidebar button to navigate to a page. Returns load time in ms."""
    # Find sidebar button with exact text
    btn = await page.query_selector(f"nav button:has-text('{page_name}')")
    if not btn:
        # Try broader selector
        btn = await page.query_selector(f"button:has-text('{page_name}')")
    if not btn:
        return -1  # Page not found in sidebar

    start = time.time()
    await btn.click()
    # Wait for content to render
    await asyncio.sleep(1.5)
    load_time = int((time.time() - start) * 1000)
    return load_time


async def audit_page(page, page_name, timestamp):
    """Audit a single page after navigating via sidebar."""
    result = {
        "page_name": page_name,
        "load_time_ms": 0,
        "console_errors": [],
        "console_warnings": [],
        "api_errors": [],
        "buttons_found": 0,
        "screenshot_path": "",
        "specific_checks": {},
        "overall_status": "pass",
    }

    # Collect console messages during navigation
    console_msgs = []
    api_errors = []

    def on_console(msg):
        console_msgs.append({"type": msg.type, "text": msg.text})

    def on_response(response):
        # Only count API errors (not page navigation 404s which are expected in SPA)
        url = response.url
        if response.status >= 400 and "localhost:7860" in url:
            api_errors.append({"url": url, "status": response.status})

    page.on("console", on_console)
    page.on("response", on_response)

    # Navigate
    load_time = await navigate_to_page(page, page_name)
    if load_time < 0:
        result["overall_status"] = "fail"
        result["console_errors"].append(f"Sidebar button '{page_name}' not found")
        page.remove_listener("console", on_console)
        page.remove_listener("response", on_response)
        return result

    result["load_time_ms"] = load_time

    # Take screenshot
    safe_name = page_name.lower().replace(" ", "_")
    screenshot_path = SCREENSHOT_DIR / f"{safe_name}_{timestamp}.png"
    try:
        await page.screenshot(path=str(screenshot_path), full_page=True)
        result["screenshot_path"] = str(screenshot_path)
    except Exception as e:
        result["console_errors"].append(f"Screenshot error: {str(e)[:200]}")

    # Process console messages
    for msg in console_msgs:
        text = msg["text"]
        # Filter out common noise
        if "favicon" in text.lower() or "devtools" in text.lower():
            continue
        if msg["type"] == "error":
            result["console_errors"].append(text[:300])
        elif msg["type"] == "warning":
            result["console_warnings"].append(text[:300])

    result["api_errors"] = api_errors

    # Count visible buttons in main content area
    try:
        buttons = await page.query_selector_all("button")
        result["buttons_found"] = len(buttons)
    except:
        pass

    # Check page has real content
    try:
        # Get main content area (exclude sidebar)
        main = await page.query_selector("main, .flex-1, [class*='overflow-auto']")
        if main:
            main_text = await main.inner_text()
        else:
            main_text = await page.inner_text("body")

        if len(main_text.strip()) < 20:
            result["specific_checks"]["blank_page"] = True
            result["overall_status"] = "fail"
        else:
            result["specific_checks"]["content_length"] = len(main_text.strip())
    except:
        pass

    # Check for visible error messages
    try:
        error_els = await page.query_selector_all("[role='alert']")
        if error_els:
            result["specific_checks"]["visible_errors"] = len(error_els)
    except:
        pass

    if result["api_errors"]:
        result["overall_status"] = "fail"

    # Remove listeners
    page.remove_listener("console", on_console)
    page.remove_listener("response", on_response)

    return result


async def check_vendors_specific(page, timestamp):
    """Vendors page specific checks."""
    checks = {}
    await navigate_to_page(page, "Vendors")
    await asyncio.sleep(1)

    try:
        body_text = await page.inner_text("body")
        count_match = re.search(r"(\d[\d,]+)\s*(?:vendors?|results?|total)", body_text, re.IGNORECASE)
        if count_match:
            checks["vendor_count_shown"] = count_match.group(1)
        else:
            # Also check for count in parentheses or after header
            count_match2 = re.search(r"Vendors?\s*\((\d[\d,]+)\)", body_text, re.IGNORECASE)
            if count_match2:
                checks["vendor_count_shown"] = count_match2.group(1)
            else:
                checks["vendor_count_shown"] = "not found"
    except:
        checks["vendor_count_shown"] = "error"

    # Look for category filter
    try:
        filter_el = await page.query_selector("select, [class*='filter'], [class*='Filter'], [role='combobox'], button:has-text('Category'), button:has-text('Filter')")
        checks["category_filter_found"] = filter_el is not None
        if filter_el:
            await filter_el.click()
            await asyncio.sleep(0.5)
            body_text = await page.inner_text("body")
            checks["wedding_venues_option"] = "wedding" in body_text.lower()
            await page.keyboard.press("Escape")
    except Exception as e:
        checks["category_filter_error"] = str(e)[:200]

    # Find Email button
    try:
        email_btn = await page.query_selector("button:has-text('Email')")
        if email_btn:
            checks["email_button_found"] = True
            await email_btn.click()
            await asyncio.sleep(1)

            body_text = await page.inner_text("body")
            checks["composer_opened"] = "zoarbathrooms@gmail.com" in body_text or "Subject" in body_text
            checks["from_field_correct"] = "zoarbathrooms@gmail.com" in body_text
            checks["subject_prefilled"] = "Subject" in body_text
            checks["body_has_signature"] = "Kai" in body_text or "(424)" in body_text

            ss_path = SCREENSHOT_DIR / f"vendor_composer_{timestamp}.png"
            await page.screenshot(path=str(ss_path), full_page=True)
            checks["composer_screenshot"] = str(ss_path)
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.5)
        else:
            checks["email_button_found"] = False
    except Exception as e:
        checks["email_button_error"] = str(e)[:200]

    return checks


async def check_email_marketing_specific(page):
    """Email Marketing page specific checks."""
    checks = {}
    await navigate_to_page(page, "Email Marketing")
    await asyncio.sleep(1)

    try:
        body_text = await page.inner_text("body")
        checks["page_has_content"] = len(body_text.strip()) > 50

        skip_btn = await page.query_selector("button:has-text('Skip'), button:has-text('Next')")
        if skip_btn:
            checks["skip_button_found"] = True
            await skip_btn.click()
            await asyncio.sleep(1)
            new_body = await page.inner_text("body")
            checks["skip_loaded_next"] = new_body != body_text

            skip_btn2 = await page.query_selector("button:has-text('Skip'), button:has-text('Next')")
            if skip_btn2:
                await skip_btn2.click()
                await asyncio.sleep(1)
                checks["second_skip_works"] = True
        else:
            checks["skip_button_found"] = False
    except Exception as e:
        checks["error"] = str(e)[:200]

    return checks


async def check_chat_specific(page):
    """Talk to Nexus chat checks."""
    checks = {}
    await navigate_to_page(page, "Talk to Nexus")
    await asyncio.sleep(1)

    try:
        chat_input = await page.query_selector(
            "input[type='text'], textarea, [contenteditable='true'], "
            "input[placeholder*='message' i], input[placeholder*='chat' i], "
            "input[placeholder*='talk' i], input[placeholder*='ask' i], "
            "input[placeholder*='type' i]"
        )
        if chat_input:
            checks["chat_input_found"] = True
            body_before = await page.inner_text("body")
            await chat_input.fill("hey")
            await page.keyboard.press("Enter")
            start = time.time()
            for _ in range(20):
                await asyncio.sleep(0.5)
                body_now = await page.inner_text("body")
                if body_now != body_before and len(body_now) > len(body_before) + 10:
                    response_time = int((time.time() - start) * 1000)
                    checks["response_received"] = True
                    checks["response_time_ms"] = response_time
                    model_match = re.search(r"(groq|cerebras|gemini|zai|ollama|glm|claude)", body_now, re.IGNORECASE)
                    if model_match:
                        checks["responding_model"] = model_match.group(1)
                    break
            else:
                checks["response_received"] = False
                checks["response_time_ms"] = 10000
        else:
            checks["chat_input_found"] = False
    except Exception as e:
        checks["error"] = str(e)[:200]

    return checks


async def check_leads_specific(page):
    """Leads Pipeline page checks."""
    checks = {}
    await navigate_to_page(page, "Leads Pipeline")
    await asyncio.sleep(1)

    try:
        body_text = await page.inner_text("body")
        checks["page_has_content"] = len(body_text.strip()) > 50
        lead_els = await page.query_selector_all("[class*='lead'], [class*='Lead'], tr, [class*='card'], [class*='Card']")
        checks["lead_elements_found"] = len(lead_els)

        neg_match = re.search(r"-\d+\s*(days?|hours?|minutes?)", body_text)
        checks["negative_timestamps"] = bool(neg_match)
    except Exception as e:
        checks["error"] = str(e)[:200]

    return checks


async def check_ad_performance_specific(page):
    """Ad Performance page checks."""
    checks = {}
    await navigate_to_page(page, "Ad Performance")
    await asyncio.sleep(1)

    try:
        body_text = await page.inner_text("body")
        checks["page_loaded"] = len(body_text.strip()) > 20
        checks["has_data"] = "$" in body_text or "CPL" in body_text or "spend" in body_text.lower()
    except Exception as e:
        checks["error"] = str(e)[:200]

    return checks


async def check_agent_control_specific(page):
    """Agent Control page checks."""
    checks = {}
    await navigate_to_page(page, "Agent Control")
    await asyncio.sleep(1)

    try:
        body_text = await page.inner_text("body")
        checks["page_loaded"] = len(body_text.strip()) > 20
        running = len(re.findall(r"running|active|online", body_text, re.IGNORECASE))
        stopped = len(re.findall(r"stopped|inactive|offline|idle", body_text, re.IGNORECASE))
        checks["running_indicators"] = running
        checks["stopped_indicators"] = stopped
    except Exception as e:
        checks["error"] = str(e)[:200]

    return checks


async def run_audit():
    """Run the full audit."""
    from playwright.async_api import async_playwright

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    results = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--remote-debugging-port=9223"]
        )
        context = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await context.new_page()

        # First load the SPA
        print(f"[AUDIT] Starting full page audit at {timestamp}")
        print(f"[AUDIT] Target: {FRONTEND_URL}")
        await page.goto(FRONTEND_URL, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2)
        print(f"[AUDIT] SPA loaded, starting sidebar navigation tests")
        print()

        # Audit each page via sidebar click
        for page_name in PAGES:
            print(f"[AUDIT] Testing: {page_name}...", end=" ", flush=True)
            result = await audit_page(page, page_name, timestamp)
            status = "PASS" if result["overall_status"] == "pass" else "FAIL"
            print(f"{status} ({result['load_time_ms']}ms, {len(result['console_errors'])} errors, {result['buttons_found']} buttons)")
            results.append(result)

        print()
        print("[AUDIT] Running page-specific checks...")

        print("[AUDIT]   Vendors page...", end=" ", flush=True)
        vendor_checks = await check_vendors_specific(page, timestamp)
        print(f"done ({len(vendor_checks)} checks)")
        for r in results:
            if r["page_name"] == "Vendors":
                r["specific_checks"].update(vendor_checks)

        print("[AUDIT]   Email Marketing...", end=" ", flush=True)
        email_checks = await check_email_marketing_specific(page)
        print(f"done ({len(email_checks)} checks)")
        for r in results:
            if r["page_name"] == "Email Marketing":
                r["specific_checks"].update(email_checks)

        print("[AUDIT]   Talk to Nexus (chat)...", end=" ", flush=True)
        chat_checks = await check_chat_specific(page)
        print(f"done ({len(chat_checks)} checks)")
        for r in results:
            if r["page_name"] == "Talk to Nexus":
                r["specific_checks"].update(chat_checks)

        print("[AUDIT]   Leads Pipeline...", end=" ", flush=True)
        leads_checks = await check_leads_specific(page)
        print(f"done ({len(leads_checks)} checks)")
        for r in results:
            if r["page_name"] == "Leads Pipeline":
                r["specific_checks"].update(leads_checks)

        print("[AUDIT]   Ad Performance...", end=" ", flush=True)
        ad_checks = await check_ad_performance_specific(page)
        print(f"done ({len(ad_checks)} checks)")
        for r in results:
            if r["page_name"] == "Ad Performance":
                r["specific_checks"].update(ad_checks)

        print("[AUDIT]   Agent Control...", end=" ", flush=True)
        agent_checks = await check_agent_control_specific(page)
        print(f"done ({len(agent_checks)} checks)")
        for r in results:
            if r["page_name"] == "Agent Control":
                r["specific_checks"].update(agent_checks)

        await browser.close()

    # Save results
    with open(RESULTS_FILE, "w") as f:
        json.dump({"timestamp": timestamp, "results": results}, f, indent=2, default=str)

    # Print summary
    passing = sum(1 for r in results if r["overall_status"] == "pass")
    failing = sum(1 for r in results if r["overall_status"] == "fail")
    total_errors = sum(len(r["console_errors"]) for r in results)
    slowest = max(results, key=lambda r: r["load_time_ms"])
    failed_pages = [r["page_name"] for r in results if r["overall_status"] == "fail"]

    print()
    print("=" * 60)
    print("NEXUS AUDIT SUMMARY")
    print("=" * 60)
    print(f"Pages passing: {passing}/{len(results)}")
    print(f"Pages failing: {failing}/{len(results)}")
    print(f"Total console errors: {total_errors}")
    print(f"Slowest page: {slowest['page_name']} at {slowest['load_time_ms']}ms")
    if failed_pages:
        print(f"Failed pages: {', '.join(failed_pages)}")
    print(f"Results saved to: {RESULTS_FILE}")
    print("=" * 60)

    return results


if __name__ == "__main__":
    results = asyncio.run(run_audit())
