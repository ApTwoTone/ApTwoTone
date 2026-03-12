from __future__ import annotations
"""
Browser Agent — Playwright-powered web research for Nexus.

Wraps Playwright (optional dependency) for automated web browsing:
  - Browse any URL, extract text/links/metadata
  - Google search with result parsing
  - Venue research (wedding/event venues)
  - Planner research (event planners/coordinators)
  - Session tracking in SQLite
  - Telegram command: /browse [url]

Playwright is NOT required — all functions degrade gracefully
with clear install instructions if missing.

Config: ~/.nexus/config.json
Database: ~/.nexus/memory.db
"""
import asyncio, json, re, sqlite3, traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# ── Constants ────────────────────────────────────────────────────────────────

DB_PATH = Path.home() / ".nexus" / "memory.db"
PT = ZoneInfo("America/Los_Angeles")

INSTALL_MSG = (
    "Playwright not installed. Run:\n"
    "  pip install playwright && playwright install chromium"
)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

GOOGLE_SEARCH_URL = "https://www.google.com/search"

# Max text length to store per page (chars)
MAX_TEXT_LENGTH = 50_000
# Max links to extract per page
MAX_LINKS = 200

# ── Helpers ──────────────────────────────────────────────────────────────────

def _now_pt() -> datetime:
    return datetime.now(PT)

def _now_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def _log(msg: str):
    print(f"[Browser] {msg}")

def _load_config() -> dict:
    p = Path.home() / ".nexus" / "config.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


# ═════════════════════════════════════════════════════════════════════════════
# PLAYWRIGHT AVAILABILITY CHECK
# ═════════════════════════════════════════════════════════════════════════════

def is_playwright_available() -> bool:
    """Check if playwright and a browser are installed."""
    try:
        from playwright.async_api import async_playwright  # noqa: F401
        return True
    except ImportError:
        return False


def _pw_missing_result(action: str = "browse") -> dict:
    """Standard error dict when playwright is not installed."""
    return {
        "success": False,
        "error": f"Cannot {action}: {INSTALL_MSG}",
        "install_hint": INSTALL_MSG,
    }


# ═════════════════════════════════════════════════════════════════════════════
# DATABASE
# ═════════════════════════════════════════════════════════════════════════════

def init_browser_db():
    """Create browser_sessions table."""
    conn = _get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS browser_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_description TEXT,
        url TEXT,
        status TEXT DEFAULT 'pending',
        result_summary TEXT DEFAULT '',
        screenshots TEXT DEFAULT '[]',
        started_at TEXT,
        completed_at TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_browser_status ON browser_sessions(status);
    CREATE INDEX IF NOT EXISTS idx_browser_created ON browser_sessions(created_at);
    """)
    conn.commit()
    conn.close()
    _log("Database tables ready")


def _create_session(task_description: str, url: str) -> int:
    """Insert a new browser session, return its ID."""
    conn = _get_conn()
    conn.execute(
        "INSERT INTO browser_sessions (task_description, url, status, started_at) "
        "VALUES (?, ?, 'in_progress', ?)",
        (task_description, url, _now_str()),
    )
    conn.commit()
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return sid


def _complete_session(session_id: int, result_summary: str, screenshots: list | None = None):
    """Mark a session as completed."""
    conn = _get_conn()
    conn.execute(
        "UPDATE browser_sessions SET status='completed', result_summary=?, "
        "screenshots=?, completed_at=? WHERE id=?",
        (result_summary, json.dumps(screenshots or []), _now_str(), session_id),
    )
    conn.commit()
    conn.close()


def _fail_session(session_id: int, error: str):
    """Mark a session as failed."""
    conn = _get_conn()
    conn.execute(
        "UPDATE browser_sessions SET status='failed', result_summary=?, "
        "completed_at=? WHERE id=?",
        (f"ERROR: {error}", _now_str(), session_id),
    )
    conn.commit()
    conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# CORE BROWSING
# ═════════════════════════════════════════════════════════════════════════════

async def browse_url(url: str, extract_text: bool = True) -> dict:
    """
    Browse a URL and extract page content.

    Returns dict with:
      - success: bool
      - title: page title
      - url: final URL (after redirects)
      - text: extracted visible text (if extract_text=True)
      - links: list of {href, text} dicts
      - meta_description: meta description tag content
      - session_id: database session ID
    """
    if not is_playwright_available():
        return _pw_missing_result("browse")

    if not url.startswith("http"):
        url = f"https://{url}"

    session_id = _create_session(f"Browse: {url}", url)
    _log(f"Browsing: {url} (session {session_id})")

    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1280, "height": 800},
            )
            page = await context.new_page()

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2000)  # let JS render
            except Exception as e:
                _log(f"Navigation error: {e}")
                _fail_session(session_id, str(e))
                await browser.close()
                return {
                    "success": False, "error": str(e),
                    "session_id": session_id,
                }

            title = await page.title()
            final_url = page.url

            # Extract meta description
            meta_desc = ""
            try:
                meta_el = await page.query_selector('meta[name="description"]')
                if meta_el:
                    meta_desc = await meta_el.get_attribute("content") or ""
            except Exception:
                pass

            # Extract visible text
            text = ""
            if extract_text:
                try:
                    text = await page.evaluate("""
                        () => {
                            const sel = document.querySelectorAll(
                                'p, h1, h2, h3, h4, h5, h6, li, td, th, span, a, div'
                            );
                            const parts = [];
                            for (const el of sel) {
                                const t = el.innerText || el.textContent || '';
                                if (t.trim().length > 2) parts.push(t.trim());
                            }
                            return [...new Set(parts)].join('\\n');
                        }
                    """)
                    if len(text) > MAX_TEXT_LENGTH:
                        text = text[:MAX_TEXT_LENGTH] + "\n...[truncated]"
                except Exception as e:
                    _log(f"Text extraction error: {e}")

            # Extract links
            links = []
            try:
                raw_links = await page.evaluate("""
                    () => {
                        const anchors = document.querySelectorAll('a[href]');
                        return Array.from(anchors).slice(0, %d).map(a => ({
                            href: a.href,
                            text: (a.innerText || a.textContent || '').trim().substring(0, 200)
                        }));
                    }
                """ % MAX_LINKS)
                links = [lk for lk in raw_links if lk.get("href", "").startswith("http")]
            except Exception as e:
                _log(f"Link extraction error: {e}")

            await browser.close()

            summary = f"Title: {title}\nLinks: {len(links)}\nText length: {len(text)} chars"
            _complete_session(session_id, summary)
            _log(f"Done: {title} ({len(links)} links, {len(text)} chars)")

            return {
                "success": True,
                "title": title,
                "url": final_url,
                "text": text,
                "links": links,
                "meta_description": meta_desc,
                "session_id": session_id,
            }

    except Exception as e:
        _log(f"Browse error: {e}")
        traceback.print_exc()
        _fail_session(session_id, str(e))
        return {"success": False, "error": str(e), "session_id": session_id}


# ═════════════════════════════════════════════════════════════════════════════
# GOOGLE SEARCH
# ═════════════════════════════════════════════════════════════════════════════

async def search_google(query: str, num_results: int = 5) -> list[dict]:
    """
    Search Google and return parsed results.

    Returns list of dicts: {title, url, snippet}
    Falls back gracefully if playwright missing.
    """
    if not is_playwright_available():
        return [_pw_missing_result("search")]

    session_id = _create_session(f"Google: {query}", GOOGLE_SEARCH_URL)
    _log(f"Google search: '{query}' (session {session_id})")

    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1280, "height": 800},
            )
            page = await context.new_page()

            search_url = f"{GOOGLE_SEARCH_URL}?q={query}&num={num_results + 5}"
            try:
                await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2000)
            except Exception as e:
                _log(f"Google nav error: {e}")
                _fail_session(session_id, str(e))
                await browser.close()
                return []

            # Parse search results from DOM
            results = await page.evaluate("""
                () => {
                    const items = [];
                    // Standard Google result selectors
                    const containers = document.querySelectorAll('div.g, div[data-hveid]');
                    for (const el of containers) {
                        const linkEl = el.querySelector('a[href^="http"]');
                        const titleEl = el.querySelector('h3');
                        const snippetEl = el.querySelector(
                            'div[data-sncf], div.VwiC3b, span.aCOpRe, div[style*="-webkit-line-clamp"]'
                        );
                        if (linkEl && titleEl) {
                            items.push({
                                title: titleEl.innerText || '',
                                url: linkEl.href || '',
                                snippet: snippetEl ? (snippetEl.innerText || '') : ''
                            });
                        }
                    }
                    return items;
                }
            """)

            await browser.close()

            # Deduplicate by URL
            seen = set()
            unique = []
            for r in results:
                if r["url"] not in seen and r["title"]:
                    seen.add(r["url"])
                    unique.append(r)
                if len(unique) >= num_results:
                    break

            summary = f"Query: {query} | {len(unique)} results"
            _complete_session(session_id, summary)
            _log(f"Search done: {len(unique)} results for '{query}'")
            return unique

    except Exception as e:
        _log(f"Search error: {e}")
        traceback.print_exc()
        _fail_session(session_id, str(e))
        return []


# ═════════════════════════════════════════════════════════════════════════════
# VENUE RESEARCH
# ═════════════════════════════════════════════════════════════════════════════

async def research_venue(venue_name: str) -> dict:
    """
    Research a wedding/event venue by name.

    Searches Google, browses the top result, extracts:
      - venue name, location, capacity
      - contact info, pricing hints
      - services offered, event types
    """
    if not is_playwright_available():
        return _pw_missing_result("research venue")

    _log(f"Researching venue: {venue_name}")
    query = f"{venue_name} wedding event venue Los Angeles California"
    search_results = await search_google(query, num_results=3)

    if not search_results or not isinstance(search_results[0], dict):
        return {"success": False, "error": "No search results", "venue_name": venue_name}

    venue_data = {
        "success": True,
        "venue_name": venue_name,
        "search_results": search_results[:3],
        "details": {},
        "contact": {},
        "source_urls": [],
    }

    # Browse the top result
    top_url = search_results[0].get("url", "")
    if top_url:
        venue_data["source_urls"].append(top_url)
        page_data = await browse_url(top_url, extract_text=True)

        if page_data.get("success"):
            text = page_data.get("text", "")

            # Extract phone numbers
            phones = re.findall(
                r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", text
            )
            if phones:
                venue_data["contact"]["phone"] = phones[0]

            # Extract emails
            emails = re.findall(
                r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text
            )
            if emails:
                venue_data["contact"]["email"] = emails[0]

            # Extract capacity hints
            cap_match = re.search(
                r"(?:capacity|accommodates?|up\s+to|holds?|seats?)\s*:?\s*(\d[\d,]*)",
                text, re.I,
            )
            if cap_match:
                venue_data["details"]["capacity"] = cap_match.group(1).replace(",", "")

            # Extract pricing hints
            price_match = re.search(
                r"\$\s?([\d,]+(?:\.\d{2})?)", text
            )
            if price_match:
                venue_data["details"]["price_hint"] = f"${price_match.group(1)}"

            # Check for relevant keywords
            text_lower = text.lower()
            event_types = []
            for kw in ["wedding", "corporate", "birthday", "quincea", "mitzvah",
                        "reception", "ceremony", "gala", "banquet", "outdoor"]:
                if kw in text_lower:
                    event_types.append(kw)
            venue_data["details"]["event_types"] = event_types

            venue_data["details"]["description"] = (
                page_data.get("meta_description", "") or text[:500]
            )

    _log(f"Venue research complete: {venue_name}")
    return venue_data


# ═════════════════════════════════════════════════════════════════════════════
# PLANNER RESEARCH
# ═════════════════════════════════════════════════════════════════════════════

async def research_planner(planner_name: str) -> dict:
    """
    Research an event planner/coordinator by name.

    Searches Google, browses top result, extracts:
      - planner name, company, location
      - contact info, services
      - reviews/ratings, social links
    """
    if not is_playwright_available():
        return _pw_missing_result("research planner")

    _log(f"Researching planner: {planner_name}")
    query = f"{planner_name} event wedding planner Los Angeles California"
    search_results = await search_google(query, num_results=3)

    if not search_results or not isinstance(search_results[0], dict):
        return {"success": False, "error": "No search results", "planner_name": planner_name}

    planner_data = {
        "success": True,
        "planner_name": planner_name,
        "search_results": search_results[:3],
        "details": {},
        "contact": {},
        "social_links": [],
        "source_urls": [],
    }

    # Browse the top result
    top_url = search_results[0].get("url", "")
    if top_url:
        planner_data["source_urls"].append(top_url)
        page_data = await browse_url(top_url, extract_text=True)

        if page_data.get("success"):
            text = page_data.get("text", "")
            links = page_data.get("links", [])

            # Extract phone numbers
            phones = re.findall(
                r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", text
            )
            if phones:
                planner_data["contact"]["phone"] = phones[0]

            # Extract emails
            emails = re.findall(
                r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text
            )
            if emails:
                planner_data["contact"]["email"] = emails[0]

            # Extract social links
            social_domains = ["instagram.com", "facebook.com", "tiktok.com",
                              "pinterest.com", "linkedin.com", "twitter.com"]
            for link in links:
                href = link.get("href", "").lower()
                for sd in social_domains:
                    if sd in href and href not in [sl.get("url") for sl in planner_data["social_links"]]:
                        planner_data["social_links"].append({
                            "platform": sd.split(".")[0],
                            "url": link["href"],
                        })

            # Services offered
            text_lower = text.lower()
            services = []
            for kw in ["wedding planning", "day-of coordination", "full service",
                        "partial planning", "event design", "floral",
                        "decor", "catering", "venue selection", "vendor management"]:
                if kw in text_lower:
                    services.append(kw)
            planner_data["details"]["services"] = services

            planner_data["details"]["description"] = (
                page_data.get("meta_description", "") or text[:500]
            )

    _log(f"Planner research complete: {planner_name}")
    return planner_data


# ═════════════════════════════════════════════════════════════════════════════
# SESSION STATS
# ═════════════════════════════════════════════════════════════════════════════

def get_session_stats() -> dict:
    """Get browsing session statistics."""
    conn = _get_conn()

    total = conn.execute("SELECT COUNT(*) FROM browser_sessions").fetchone()[0]
    completed = conn.execute(
        "SELECT COUNT(*) FROM browser_sessions WHERE status='completed'"
    ).fetchone()[0]
    failed = conn.execute(
        "SELECT COUNT(*) FROM browser_sessions WHERE status='failed'"
    ).fetchone()[0]
    in_progress = conn.execute(
        "SELECT COUNT(*) FROM browser_sessions WHERE status='in_progress'"
    ).fetchone()[0]

    # Today's sessions
    today = _now_pt().strftime("%Y-%m-%d")
    today_count = conn.execute(
        "SELECT COUNT(*) FROM browser_sessions WHERE created_at >= ?",
        (today,),
    ).fetchone()[0]

    # Recent sessions
    recent = conn.execute(
        "SELECT id, task_description, url, status, created_at "
        "FROM browser_sessions ORDER BY id DESC LIMIT 5"
    ).fetchall()

    conn.close()

    return {
        "total_sessions": total,
        "completed": completed,
        "failed": failed,
        "in_progress": in_progress,
        "today": today_count,
        "playwright_available": is_playwright_available(),
        "recent": [
            {
                "id": r["id"],
                "task": r["task_description"],
                "url": r["url"],
                "status": r["status"],
                "created_at": r["created_at"],
            }
            for r in recent
        ],
    }


def format_session_stats() -> str:
    """Telegram-formatted session stats."""
    stats = get_session_stats()
    pw_status = "installed" if stats["playwright_available"] else "NOT installed"

    msg = (
        "\U0001f310 BROWSER AGENT\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"Playwright: {pw_status}\n"
        f"Sessions today: {stats['today']}\n"
        f"Total: {stats['total_sessions']} "
        f"({stats['completed']} done, {stats['failed']} failed)\n"
    )

    if stats["recent"]:
        msg += "\nRecent:\n"
        for r in stats["recent"]:
            icon = "\u2705" if r["status"] == "completed" else (
                "\u274c" if r["status"] == "failed" else "\u23f3"
            )
            task = (r["task"] or "")[:40]
            msg += f"  {icon} {task}\n"

    return msg


# ═════════════════════════════════════════════════════════════════════════════
# TELEGRAM COMMAND HANDLER
# ═════════════════════════════════════════════════════════════════════════════

async def handle_browse_command(text: str) -> str:
    """
    /browse [url] — Browse a URL and return summary.
    /browse stats  — Show session stats.
    """
    stripped = text.strip()
    for prefix in ["/browse ", "/browse"]:
        if stripped.lower().startswith(prefix):
            stripped = stripped[len(prefix):].strip()
            break

    if not stripped or stripped.lower() == "help":
        return (
            "\U0001f310 Browser Agent\n"
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            "Usage:\n"
            "  /browse [url] \u2014 Browse a URL\n"
            "  /browse stats \u2014 Session stats\n\n"
            "Examples:\n"
            "  /browse example.com\n"
            "  /browse https://theknot.com/venue/12345"
        )

    if stripped.lower() == "stats":
        return format_session_stats()

    # Browse the URL
    url = stripped
    if not url.startswith("http"):
        url = f"https://{url}"

    result = await browse_url(url, extract_text=True)

    if not result.get("success"):
        error = result.get("error", "Unknown error")
        return f"\u274c Browse failed: {error}"

    title = result.get("title", "No title")
    final_url = result.get("url", url)
    text_content = result.get("text", "")
    links = result.get("links", [])
    meta = result.get("meta_description", "")

    # Build response
    msg = (
        f"\U0001f310 {title}\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"URL: {final_url}\n"
    )

    if meta:
        msg += f"\n{meta[:200]}\n"

    # Preview of text content
    preview = text_content[:500].strip() if text_content else "(no text extracted)"
    msg += f"\n{preview}"
    if len(text_content) > 500:
        msg += f"\n\n... ({len(text_content):,} chars total)"

    msg += f"\n\n\U0001f517 {len(links)} links found"

    return msg
