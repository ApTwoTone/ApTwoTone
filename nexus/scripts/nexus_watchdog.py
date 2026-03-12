#!/usr/bin/env python3
"""
Nexus Watchdog Daemon — Continuous system health monitoring.

Runs permanently in the background:
- Every 5 minutes: health check (server, database, AI providers)
- Every 30 minutes: critical Playwright tests
- Every 6 hours: database maintenance

Usage:
    python3 scripts/nexus_watchdog.py
"""
import asyncio
import json
import logging
import sqlite3
import time
import traceback
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

# Setup logging
LOG_PATH = Path(__file__).parent.parent / "watchdog.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    handlers=[
        logging.FileHandler(str(LOG_PATH)),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("watchdog")

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"
SERVER_URL = "http://localhost:7860"
FRONTEND_URL = "http://localhost:3000"

# Intervals in seconds
HEALTH_INTERVAL = 300      # 5 minutes
PLAYWRIGHT_INTERVAL = 1800  # 30 minutes
MAINTENANCE_INTERVAL = 21600  # 6 hours

_last_health = 0
_last_playwright = 0
_last_maintenance = 0


def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def send_telegram(text):
    """Send alert to Telegram (plain text, no markdown)."""
    try:
        cfg = load_config()
        token = cfg.get("telegram_token", "")
        chat_ids = cfg.get("telegram_chat_ids", [])
        if not token or not chat_ids:
            log.warning("Telegram not configured")
            return
        for cid in chat_ids:
            payload = json.dumps({"chat_id": cid, "text": text}).encode()
            req = Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=payload,
                headers={"Content-Type": "application/json"}
            )
            with urlopen(req, timeout=10):
                pass
    except Exception as e:
        log.error("Telegram send failed: %s", e)


def check_server():
    """Check if the FastAPI server responds."""
    try:
        req = Request(f"{SERVER_URL}/api/health", headers={"User-Agent": "Nexus-Watchdog/1.0"})
        with urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                return "ok"
    except:
        pass

    # Retry twice
    for _ in range(2):
        time.sleep(2)
        try:
            req = Request(f"{SERVER_URL}/api/health", headers={"User-Agent": "Nexus-Watchdog/1.0"})
            with urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    return "ok"
        except:
            pass

    return "down"


def check_database():
    """Check if database is accessible."""
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5)
        c = conn.cursor()
        c.execute("SELECT 1")
        conn.close()
        return "ok"
    except Exception as e:
        return f"down ({str(e)[:50]})"


def check_ai_provider():
    """Check if at least one AI provider responds."""
    cfg = load_config()
    key_pools = cfg.get("key_pools", {})

    # Try ZAI first
    zai_keys = key_pools.get("zai", [])
    if zai_keys:
        key = zai_keys[0] if isinstance(zai_keys[0], str) else zai_keys[0].get("key", "")
        if key:
            try:
                payload = json.dumps({
                    "model": "glm-4-flash",
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 5,
                }).encode()
                req = Request(
                    "https://open.bigmodel.cn/api/paas/v4/chat/completions",
                    data=payload,
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
                )
                with urlopen(req, timeout=8):
                    return "ok (ZAI)"
            except:
                pass

    # Try Cerebras
    cerebras_keys = key_pools.get("cerebras", [])
    if cerebras_keys:
        key = cerebras_keys[0] if isinstance(cerebras_keys[0], str) else cerebras_keys[0].get("key", "")
        if key:
            try:
                payload = json.dumps({
                    "model": "llama3.1-8b",
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 5,
                }).encode()
                req = Request(
                    "https://api.cerebras.ai/v1/chat/completions",
                    data=payload,
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
                )
                with urlopen(req, timeout=5):
                    return "ok (Cerebras)"
            except:
                pass

    return "unreachable"


def run_health_check():
    """Run the lightweight 5-minute health check."""
    server = check_server()
    database = check_database()
    ai = check_ai_provider()

    log.info("HEALTH | server: %s | database: %s | ai: %s", server, database, ai)

    # Alert on failures
    if server == "down":
        send_telegram(f"NEXUS ALERT -- Server not responding at {datetime.now().strftime('%H:%M')}. Check Mac Mini.")
    if "down" in database:
        send_telegram(f"NEXUS ALERT -- Database not accessible at {datetime.now().strftime('%H:%M')}.")
    if ai == "unreachable":
        send_telegram(f"NEXUS ALERT -- All AI providers unreachable at {datetime.now().strftime('%H:%M')}.")

    return server, database, ai


async def run_playwright_tests():
    """Run 3 critical Playwright tests (headless)."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log.warning("Playwright not available for watchdog tests")
        return

    results = {"chat": "skip", "vendors": "skip", "leads": "skip"}

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--remote-debugging-port=9225"])
            context = await browser.new_context(viewport={"width": 1440, "height": 900})
            page = await context.new_page()

            # Load SPA
            await page.goto(FRONTEND_URL, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)

            # Test 1: Chat
            try:
                await page.click("nav button:has-text('Talk to Nexus')")
                await asyncio.sleep(1)
                chat_input = await page.query_selector("input[type='text'], textarea")
                if chat_input:
                    body_before = await page.inner_text("body")
                    await chat_input.fill("hey")
                    await page.keyboard.press("Enter")
                    for _ in range(20):
                        await asyncio.sleep(0.5)
                        body_now = await page.inner_text("body")
                        if body_now != body_before and len(body_now) > len(body_before) + 10:
                            results["chat"] = "pass"
                            break
                    else:
                        results["chat"] = "fail (no response in 10s)"
                else:
                    results["chat"] = "fail (no input)"
            except Exception as e:
                results["chat"] = f"fail ({str(e)[:50]})"

            # Test 2: Vendors
            try:
                await page.click("nav button:has-text('Vendors')")
                await asyncio.sleep(2)
                body = await page.inner_text("body")
                if len(body.strip()) > 50:
                    results["vendors"] = "pass"
                else:
                    results["vendors"] = "fail (empty page)"
            except Exception as e:
                results["vendors"] = f"fail ({str(e)[:50]})"

            # Test 3: Leads Pipeline
            try:
                await page.click("nav button:has-text('Leads Pipeline')")
                await asyncio.sleep(2)
                body = await page.inner_text("body")
                if len(body.strip()) > 50:
                    results["leads"] = "pass"
                else:
                    results["leads"] = "fail (empty page)"
            except Exception as e:
                results["leads"] = f"fail ({str(e)[:50]})"

            await browser.close()

    except Exception as e:
        log.error("Playwright tests failed: %s", e)
        results = {"chat": f"error ({str(e)[:50]})", "vendors": "error", "leads": "error"}

    log.info("PLAYWRIGHT | chat: %s | vendors: %s | leads: %s",
             results["chat"], results["vendors"], results["leads"])

    # Alert on failures
    for test_name, status in results.items():
        if "fail" in status or "error" in status:
            send_telegram(
                f"NEXUS WATCHDOG -- {test_name} test FAILING at {datetime.now().strftime('%H:%M')}. "
                f"Details: {status}"
            )

    return results


def run_db_maintenance():
    """Run database maintenance every 6 hours."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        c = conn.cursor()

        # Check for non-normalized cities
        c.execute("SELECT COUNT(*) FROM vendors WHERE city LIKE '%, CA%'")
        non_norm = c.fetchone()[0]
        if non_norm > 0:
            import re
            c.execute("SELECT DISTINCT city FROM vendors WHERE city LIKE '%, CA%'")
            for (city,) in c.fetchall():
                clean = re.sub(r',?\s*CA\s*\d*$', '', city).strip()
                if clean and clean != city:
                    c.execute("UPDATE vendors SET city = ? WHERE city = ?", (clean, city))

        # Check for duplicates
        c.execute("""
            SELECT name, city, COUNT(*) FROM vendors
            GROUP BY name, city HAVING COUNT(*) > 1
        """)
        dupes = c.fetchall()
        dupe_count = 0
        for name, city, cnt in dupes:
            c.execute("""
                DELETE FROM vendors WHERE id NOT IN (
                    SELECT MIN(id) FROM vendors WHERE name = ? AND city = ?
                ) AND name = ? AND city = ?
            """, (name, city, name, city))
            dupe_count += c.rowcount

        # ANALYZE
        c.execute("ANALYZE")
        conn.commit()
        conn.close()

        log.info("MAINTENANCE | cities_fixed: %d | duplicates_removed: %d | ANALYZE: done",
                 non_norm, dupe_count)
    except Exception as e:
        log.error("Maintenance failed: %s", e)


async def watchdog_loop():
    """Main watchdog loop."""
    global _last_health, _last_playwright, _last_maintenance

    log.info("WATCHDOG STARTED -- monitoring every 5 minutes")
    send_telegram(f"NEXUS WATCHDOG STARTED -- monitoring every 5 minutes at {datetime.now().strftime('%H:%M')}")

    while True:
        try:
            now = time.time()

            # Health check every 5 minutes
            if now - _last_health >= HEALTH_INTERVAL:
                run_health_check()
                _last_health = now

            # Playwright tests every 30 minutes
            if now - _last_playwright >= PLAYWRIGHT_INTERVAL:
                await run_playwright_tests()
                _last_playwright = now

            # DB maintenance every 6 hours
            if now - _last_maintenance >= MAINTENANCE_INTERVAL:
                run_db_maintenance()
                _last_maintenance = now

            # Sleep until next check (check every 60 seconds which interval is due)
            await asyncio.sleep(60)

        except Exception as e:
            log.error("Watchdog error: %s\n%s", e, traceback.format_exc())
            await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(watchdog_loop())
