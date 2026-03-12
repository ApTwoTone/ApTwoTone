"""
FB Vendor Scraper — Playwright browser helpers + human behavior simulation.
Uses installed Google Chrome (channel="chrome") with its own persistent
profile at ~/.nexus/fb_browser_data. One-time Facebook login required —
session persists across all future runs. Your real Chrome is never touched.
"""
import asyncio
import logging
import os
import random
import signal
import subprocess
from playwright.async_api import async_playwright, Page

try:
    from playwright_stealth import stealth_async
except ImportError:
    stealth_async = None

from config import (
    VIEWPORT_WIDTH, VIEWPORT_HEIGHT,
    SCROLL_DELAY_MIN, SCROLL_DELAY_MAX,
    ACTION_DELAY_MIN, ACTION_DELAY_MAX,
    MINOR_DELAY_MIN, MINOR_DELAY_MAX,
    BROWSER_DATA_DIR,
)

log = logging.getLogger("fb_scraper")


def reopen_chrome():
    """No-op — kept for import compatibility. Real Chrome is never closed."""
    pass


def _kill_stale_profile_processes():
    """Kill only Chrome processes using the dedicated Facebook scraper profile."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", str(BROWSER_DATA_DIR)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        result = None

    my_pid = os.getpid()
    if result:
        for pid_text in result.stdout.splitlines():
            pid_text = pid_text.strip()
            if not pid_text.isdigit():
                continue
            pid = int(pid_text)
            if pid == my_pid:
                continue
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except Exception:
                continue

    for lock_name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        try:
            (BROWSER_DATA_DIR / lock_name).unlink(missing_ok=True)
        except Exception:
            continue


async def launch_browser():
    """Launch Chrome with a dedicated scraper profile. Returns (playwright, context).

    Uses channel="chrome" so Playwright drives the real Google Chrome binary
    (same fingerprint, user-agent, TLS stack as your normal browser). The
    profile at BROWSER_DATA_DIR is separate from your main Chrome — it keeps
    its own cookies and login sessions across runs.

    First run: you'll need to log into Facebook once in this window.
    Every run after: already logged in automatically.
    """
    BROWSER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _kill_stale_profile_processes()

    pw = await async_playwright().start()
    context = await pw.chromium.launch_persistent_context(
        user_data_dir=str(BROWSER_DATA_DIR),
        headless=False,
        channel="chrome",
        viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
        locale="en-US",
        timezone_id="America/Los_Angeles",
        ignore_default_args=["--enable-automation"],
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--start-minimized",
            "--window-position=2000,2000",
        ],
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9",
        },
    )

    # Enhanced anti-detection: hide automation flags
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5],
        });
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en'],
        });
    """)

    log.info("Chrome launched with scraper profile")
    return pw, context


async def setup_page_optimizations(page: Page):
    """Block heavy resources to speed up scraping.
    Uses targeted URL patterns instead of catch-all to avoid crashing Chrome."""
    # Block specific heavy resource patterns — targeted, not catch-all.
    # Guard route setup because Playwright raises if page/context was closed mid-cycle.
    patterns = [
        "**/*.{mp4,webm,mov,avi}",
        "**/*.{woff,woff2,ttf,otf,eot}",
        "**/tr?*",          # Facebook pixel
        "**/*pixel*",
        "**/*analytics*",
    ]
    applied = 0
    for pattern in patterns:
        try:
            await page.route(pattern, lambda route: route.abort())
            applied += 1
        except Exception as e:
            msg = str(e)
            if "Target page, context or browser has been closed" in msg:
                log.warning("Skipping route setup (%s): page/context closed", pattern)
                break
            log.warning("Route setup failed (%s): %s", pattern, msg[:180])

    log.info("Page optimizations applied (targeted resource blocking): %d/%d", applied, len(patterns))


async def is_logged_in(page: Page) -> bool:
    """Navigate to Facebook and check if we're logged in."""
    try:
        await page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(3)
        return await _check_page_logged_in(page)
    except Exception as e:
        log.warning(f"Login check failed: {e}")
        return False


async def _check_page_logged_in(page: Page) -> bool:
    """Check if the current page shows a logged-in Facebook state."""
    url = page.url.lower()
    if "login" in url or "checkpoint" in url:
        return False

    # Check for login form (appears when NOT logged in, even at facebook.com/)
    login_form = await page.query_selector('input[name="email"], input[name="pass"], #loginbutton')
    if login_form:
        return False

    # Check for elements that only appear when logged in
    nav = await page.query_selector('[aria-label="Facebook"]')
    if nav:
        return True
    feed = await page.query_selector('[role="feed"]')
    if feed:
        return True
    # Logged-in pages have the composer ("What's on your mind")
    composer = await page.query_selector('[role="textbox"][aria-label*="mind"], [data-pagelet="FeedComposer"]')
    if composer:
        return True

    return False


async def check_login_no_navigate(page: Page) -> bool:
    """Check if current page shows logged-in state without navigating again."""
    await asyncio.sleep(2)
    return await _check_page_logged_in(page)


async def check_for_blocking(page: Page):
    """Check if Facebook is showing CAPTCHA, checkpoint, or error.
    Returns a description of the problem, or None if OK."""
    url = page.url.lower()
    if "checkpoint" in url:
        return "Security checkpoint detected"
    if "login" in url and "facebook.com/login" in url:
        return "Redirected to login page"

    # Check for visible CAPTCHA elements (not just the word in JS source)
    captcha_iframe = await page.query_selector('iframe[src*="recaptcha"], iframe[src*="captcha"]')
    if captcha_iframe and await captcha_iframe.is_visible():
        return "CAPTCHA detected"

    # Check visible page text for blocking messages
    try:
        body_text = await page.inner_text("body")
        lower_text = body_text.lower()
        if "temporarily blocked" in lower_text:
            return "Account temporarily blocked"
        if "something went wrong" in lower_text and len(body_text) < 500:
            return "Facebook error page detected"
    except Exception:
        pass

    return None


# ── Human behavior simulation ────────────────────────────────────────────────

async def random_delay(min_s: float, max_s: float):
    """Wait a random duration between min_s and max_s seconds."""
    delay = random.uniform(min_s, max_s)
    await asyncio.sleep(delay)


async def action_delay():
    """Delay between major actions (navigating, scrolling)."""
    await random_delay(ACTION_DELAY_MIN, ACTION_DELAY_MAX)


async def minor_delay():
    """Delay for minor actions (clicking See more, loading profile)."""
    await random_delay(MINOR_DELAY_MIN, MINOR_DELAY_MAX)


async def scroll_delay():
    """Delay between scroll actions."""
    await random_delay(SCROLL_DELAY_MIN, SCROLL_DELAY_MAX)


async def move_mouse_randomly(page: Page):
    """Move mouse to a random position to simulate natural behavior."""
    x = random.randint(100, VIEWPORT_WIDTH - 100)
    y = random.randint(100, VIEWPORT_HEIGHT - 100)
    await page.mouse.move(x, y)


async def scroll_page(page: Page, distance=None):
    """Scroll down using mouse wheel events (looks human, fires native events)."""
    if distance is None:
        distance = random.randint(300, 800)

    # Simulate mouse wheel scrolling in multiple small increments
    # Real humans scroll in bursts with varying speed, not one instant jump
    remaining = distance
    while remaining > 0:
        # Each wheel tick scrolls 40-120px (varies like a real trackpad/wheel)
        chunk = min(remaining, random.randint(40, 120))
        await page.mouse.wheel(0, chunk)
        remaining -= chunk
        # Tiny pause between wheel ticks (real scroll events are ~8-16ms apart)
        await asyncio.sleep(random.uniform(0.01, 0.04))

    await scroll_delay()


async def idle_browse(page: Page):
    """Scroll around for a few seconds without clicking — simulates natural browsing."""
    duration = random.uniform(5.0, 10.0)
    end_time = asyncio.get_event_loop().time() + duration
    while asyncio.get_event_loop().time() < end_time:
        await scroll_page(page, random.randint(100, 400))
        await move_mouse_randomly(page)
        await asyncio.sleep(random.uniform(0.5, 2.0))


async def click_see_more(page: Page):
    """Click 'See more' buttons that expand post text (not links that navigate away)."""
    url_before = page.url
    see_more_buttons = await page.query_selector_all(
        'div[role="button"]:has-text("See more")'
    )
    clicked = 0
    for btn in see_more_buttons:
        try:
            if not await btn.is_visible():
                continue
            # Skip if this element is inside an <a> tag (would navigate away)
            is_link = await btn.evaluate('el => !!el.closest("a")')
            if is_link:
                continue
            await btn.click()
            clicked += 1
            await minor_delay()
            # If we accidentally navigated away, go back immediately
            if page.url != url_before:
                await page.goto(url_before, wait_until="domcontentloaded", timeout=15000)
                await action_delay()
                break
        except Exception:
            pass
    return clicked


async def safe_goto(page: Page, url: str, timeout: int = 20000) -> bool:
    """Navigate to a URL with error handling. Returns True on success."""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        await action_delay()
        problem = await check_for_blocking(page)
        if problem:
            log.error(f"Blocking detected after navigating to {url}: {problem}")
            return False
        return True
    except Exception as e:
        log.error(f"Navigation failed for {url}: {e}")
        return False
