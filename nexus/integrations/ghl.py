from __future__ import annotations
"""
GoHighLevel Browser Scraper
- Uses Playwright to log into app.gohighlevel.com
- Scrapes the contacts page for lead info
- Polls on a timer and feeds new contacts into the lead pipeline
- No API key needed (works with free GHL plan)

First-time: launches headed browser for manual login, saves session state.
After that: headless, loads saved state, scrapes contacts page.
"""
import asyncio, json, sqlite3, traceback
from pathlib import Path
from datetime import datetime

DB_PATH = Path.home() / ".nexus" / "memory.db"
STATE_DIR = Path.home() / ".nexus" / "browser_state"
GHL_STATE_FILE = STATE_DIR / "ghl_state.json"

_ghl_scraper = None
_poller = None
_shared_browser = None


class GHLScraper:
    """Manages a Playwright BrowserContext for GHL."""

    def __init__(self, browser, config: dict):
        self.browser = browser
        self.config = config
        self.context = None
        self.page = None
        self.logged_in = False

    async def init_context(self):
        """Create or restore browser context from saved state."""
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        if GHL_STATE_FILE.exists():
            try:
                self.context = await self.browser.new_context(
                    storage_state=str(GHL_STATE_FILE),
                    viewport={"width": 1280, "height": 900}
                )
                self.page = await self.context.new_page()
                print("[GHL] Restored saved session state")
                return True
            except Exception as e:
                print(f"[GHL] Failed to restore state: {e}")

        self.context = await self.browser.new_context(viewport={"width": 1280, "height": 900})
        self.page = await self.context.new_page()
        return False

    async def ensure_login(self) -> bool:
        """Check if session is valid by navigating to contacts page."""
        try:
            await self.page.goto("https://app.gohighlevel.com/contacts/", wait_until="networkidle", timeout=20000)
            url = self.page.url
            # If we're on the contacts page (not redirected to login), we're good
            if "contacts" in url and "login" not in url:
                self.logged_in = True
                return True
            self.logged_in = False
            return False
        except Exception as e:
            print(f"[GHL] Login check failed: {e}")
            return False

    async def manual_login(self) -> bool:
        """
        Launch a headed browser for user to log in manually.
        Waits for navigation to the contacts/dashboard page.
        Saves storage state after successful login.
        Returns True if login succeeded.
        """
        try:
            from playwright.async_api import async_playwright
            pw = await async_playwright().start()
            # Use system Chrome so Google/GHL don't block sign-in
            headed_browser = await pw.chromium.launch(headless=False, channel="chrome")
            context = await headed_browser.new_context(viewport={"width": 1280, "height": 900})
            page = await context.new_page()
            await page.goto("https://app.gohighlevel.com/")

            print("[GHL] Waiting for manual login... (log in, then navigate to Contacts)")

            # Wait for user to reach a page that indicates successful login
            # Poll every 2 seconds for up to 5 minutes
            for _ in range(150):
                await asyncio.sleep(2)
                url = page.url
                if any(x in url for x in ["contacts", "dashboard", "conversations", "opportunities"]):
                    print(f"[GHL] Login detected! URL: {url}")
                    # Save state
                    STATE_DIR.mkdir(parents=True, exist_ok=True)
                    await context.storage_state(path=str(GHL_STATE_FILE))
                    print(f"[GHL] Session state saved to {GHL_STATE_FILE}")
                    await headed_browser.close()
                    await pw.stop()

                    # Reload our headless context with the new state
                    await self.close()
                    await self.init_context()
                    self.logged_in = True
                    return True

            print("[GHL] Login timeout (5 min)")
            await headed_browser.close()
            await pw.stop()
            return False

        except Exception as e:
            print(f"[GHL] Manual login error: {e}")
            return False

    async def scrape_contacts(self) -> list[dict]:
        """
        Navigate to contacts page, extract contact data from DOM.
        Returns list of dicts with contact info.
        """
        if not self.page:
            return []

        try:
            await self.page.goto("https://app.gohighlevel.com/contacts/", wait_until="networkidle", timeout=30000)

            # Wait for the contacts table/list to load
            await self.page.wait_for_selector("[class*='contact'], table, [data-testid]", timeout=15000)
            await asyncio.sleep(2)  # Let dynamic content settle

            # GHL contacts page structure varies — try multiple approaches
            contacts = await self.page.evaluate("""() => {
                const results = [];

                // Approach 1: Table rows
                const rows = document.querySelectorAll('table tbody tr, [class*="contact-row"], [class*="ContactRow"]');
                for (const row of rows) {
                    const cells = row.querySelectorAll('td, [class*="cell"], [class*="Cell"]');
                    const links = row.querySelectorAll('a');
                    const text = row.innerText || '';

                    // Try to extract from structured cells
                    if (cells.length >= 2) {
                        const nameEl = cells[0];
                        const name = (nameEl.innerText || '').trim();
                        const parts = name.split(' ');

                        results.push({
                            first_name: parts[0] || '',
                            last_name: parts.slice(1).join(' ') || '',
                            full_text: text,
                            email: (text.match(/[\\w.-]+@[\\w.-]+\\.[a-z]{2,}/i) || [''])[0],
                            phone: (text.match(/(\\+?1?\\s?\\(?\\d{3}\\)?[\\s.-]?\\d{3}[\\s.-]?\\d{4})/) || [''])[0],
                            ghl_id: (links[0]?.href?.match(/contact\\/(\\w+)/) || [,''])[1],
                            source: '',
                        });
                    }
                }

                // Approach 2: Card-based layout
                if (results.length === 0) {
                    const cards = document.querySelectorAll('[class*="contact-card"], [class*="ContactCard"]');
                    for (const card of cards) {
                        const text = card.innerText || '';
                        const nameMatch = text.match(/^(.+?)\\n/);
                        const name = nameMatch ? nameMatch[1].trim() : '';
                        const parts = name.split(' ');

                        results.push({
                            first_name: parts[0] || '',
                            last_name: parts.slice(1).join(' ') || '',
                            full_text: text,
                            email: (text.match(/[\\w.-]+@[\\w.-]+\\.[a-z]{2,}/i) || [''])[0],
                            phone: (text.match(/(\\+?1?\\s?\\(?\\d{3}\\)?[\\s.-]?\\d{3}[\\s.-]?\\d{4})/) || [''])[0],
                            ghl_id: '',
                            source: '',
                        });
                    }
                }

                return results;
            }""")

            print(f"[GHL] Scraped {len(contacts)} contacts")
            return contacts

        except Exception as e:
            print(f"[GHL] Scrape error: {e}")
            # Take debug screenshot
            try:
                debug_dir = STATE_DIR / "debug"
                debug_dir.mkdir(exist_ok=True)
                await self.page.screenshot(path=str(debug_dir / f"ghl_error_{datetime.now().strftime('%H%M%S')}.png"))
            except:
                pass
            return []

    async def save_state(self):
        """Save browser storage state to disk."""
        if self.context:
            try:
                STATE_DIR.mkdir(parents=True, exist_ok=True)
                await self.context.storage_state(path=str(GHL_STATE_FILE))
            except Exception as e:
                print(f"[GHL] Save state error: {e}")

    async def close(self):
        if self.context:
            try:
                await self.context.close()
            except:
                pass
            self.context = None
            self.page = None


class GHLPoller:
    """Polls GHL for new contacts on a timer."""

    def __init__(self, scraper: GHLScraper, interval: int, callbacks: list):
        self.scraper = scraper
        self.interval = interval
        self.callbacks = callbacks
        self._running = False

    async def run(self):
        """Main polling loop."""
        self._running = True
        print(f"[GHL] Poller started (every {self.interval}s)")
        while self._running:
            try:
                if self.scraper.logged_in:
                    contacts = await self.scraper.scrape_contacts()
                    new_leads = self._find_new_contacts(contacts)
                    for lead in new_leads:
                        self._save_lead(lead)
                        await self._notify_all(
                            f"New lead from GHL: {lead['first_name']} {lead['last_name']} "
                            f"· {lead.get('phone', '')} · {lead.get('email', '')}"
                        )
                    # Save session state periodically
                    await self.scraper.save_state()
                else:
                    print("[GHL] Not logged in — skipping scrape")
            except Exception as e:
                print(f"[GHL] Poll error: {e}")
                traceback.print_exc()
            await asyncio.sleep(self.interval)

    def _find_new_contacts(self, contacts: list) -> list:
        """Compare against leads table, return only new ones."""
        if not contacts:
            return []
        conn = sqlite3.connect(str(DB_PATH))
        existing_phones = set()
        existing_emails = set()
        existing_ghl_ids = set()
        for row in conn.execute("SELECT phone, email, ghl_contact_id FROM leads").fetchall():
            if row[0]: existing_phones.add(_clean_digits(row[0]))
            if row[1]: existing_emails.add(row[1].lower().strip())
            if row[2]: existing_ghl_ids.add(row[2])
        conn.close()

        new = []
        for c in contacts:
            phone_digits = _clean_digits(c.get("phone", ""))
            email = (c.get("email", "") or "").lower().strip()
            ghl_id = c.get("ghl_id", "")

            # Skip if we already have this contact
            if ghl_id and ghl_id in existing_ghl_ids:
                continue
            if phone_digits and phone_digits in existing_phones:
                continue
            if email and email in existing_emails:
                continue
            # Skip if no contact info at all
            if not phone_digits and not email:
                continue
            new.append(c)

        return new

    def _save_lead(self, contact: dict):
        """INSERT into leads table with status='new'."""
        conn = sqlite3.connect(str(DB_PATH))
        try:
            conn.execute(
                "INSERT OR IGNORE INTO leads (ghl_contact_id, first_name, last_name, email, phone, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (contact.get("ghl_id", ""), contact.get("first_name", ""),
                 contact.get("last_name", ""), contact.get("email", ""),
                 contact.get("phone", ""), contact.get("source", "GHL"))
            )
            conn.commit()
        except Exception as e:
            print(f"[GHL] Save lead error: {e}")
        finally:
            conn.close()

    async def _notify_all(self, msg: str):
        for cb in self.callbacks:
            try:
                await cb(msg)
            except Exception as e:
                print(f"[GHL] Callback error: {e}")

    def stop(self):
        self._running = False


def _clean_digits(p: str) -> str:
    d = "".join(c for c in str(p) if c.isdigit())
    if len(d) == 11 and d[0] == "1":
        d = d[1:]
    return d


# ── Module-level functions matching server.py imports ─────────────────────────

async def init_ghl(config: dict, callbacks: list):
    """
    Initialize GHL scraper and poller.
    Returns (GHLPoller | None, error_string).
    Called from server.py startup.
    """
    global _ghl_scraper, _poller, _shared_browser

    try:
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        _shared_browser = await pw.chromium.launch(headless=True)

        _ghl_scraper = GHLScraper(_shared_browser, config)
        await _ghl_scraper.init_context()

        # Check if we have a valid session
        logged_in = await _ghl_scraper.ensure_login()
        if not logged_in:
            print("[GHL] No valid session — use /api/browser/login-ghl to log in")

        interval = int(config.get("ghl_poll_interval", 300))
        _poller = GHLPoller(_ghl_scraper, interval, callbacks)
        return _poller, ""

    except ImportError:
        return None, "Playwright not installed. Run: pip install playwright && playwright install chromium"
    except Exception as e:
        return None, str(e)


def get_ghl():
    return _ghl_scraper


def get_poller():
    return _poller


def get_shared_browser():
    return _shared_browser


def get_messenger():
    """Returns the Messenger from messaging.py (server.py calls this from GHL endpoints)."""
    from integrations.messaging import get_messenger as _gm
    return _gm()


async def get_message_log(limit=50) -> list:
    """Query lead_messages table for recent outbound messages."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT lm.*, l.first_name, l.last_name, l.phone FROM lead_messages lm "
            "LEFT JOIN leads l ON lm.lead_id = l.id "
            "ORDER BY lm.ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [{"ts": r["ts"], "name": f"{r['first_name']} {r['last_name']}".strip(),
                 "phone": r["phone"] or "", "message": r["content"],
                 "channel": r["channel"], "method": r["method"],
                 "success": r["status"] == "sent"} for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


async def search_contacts_tool(query="", limit=50) -> dict:
    """Query leads table. Compatible with server.py /api/ghl/contacts endpoint."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        if query:
            rows = conn.execute(
                "SELECT * FROM leads WHERE first_name LIKE ? OR last_name LIKE ? OR phone LIKE ? OR email LIKE ? "
                "ORDER BY updated_at DESC LIMIT ?",
                (f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%", limit)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM leads ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        contacts = [{"firstName": r["first_name"], "lastName": r["last_name"],
                      "phone": r["phone"], "email": r["email"],
                      "source": r["source"], "dateAdded": r["discovered_at"],
                      "status": r["status"]} for r in rows]
        return {"contacts": contacts, "total": len(contacts)}
    except Exception as e:
        return {"contacts": [], "total": 0, "error": str(e)}
    finally:
        conn.close()
