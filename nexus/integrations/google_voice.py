from __future__ import annotations
"""
Google Voice SMS via Playwright browser automation.
- Sends SMS from user's Google Voice number
- Polls for new message replies
- Uses a persistent Chrome profile (no cookie export/import)
- asyncio.Lock prevents concurrent page operations

First-time: headed browser for manual Google login, saves to Chrome profile.
After that: headed (off-screen), reuses the same Chrome profile.
"""
import asyncio, json, os, re, signal, subprocess, time, traceback
from pathlib import Path
from datetime import datetime

STATE_DIR = Path.home() / ".nexus" / "browser_state"
GV_PROFILE = STATE_DIR / "gv_chrome_profile"
GV_URL = "https://voice.google.com"
GV_RUNTIME_STATE = STATE_DIR / "gv_runtime.json"
GV_HIDDEN_BOUNDS = (-32000, -32000, -31880, -31880)
GV_WINDOW_KEEPER_INTERVAL_SECONDS = 2.0

_gv = None


def _write_runtime_state(**updates):
    state = read_google_voice_runtime_state()
    state.update(updates)
    state["updated_at"] = datetime.utcnow().isoformat() + "Z"
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        GV_RUNTIME_STATE.write_text(json.dumps(state, indent=2))
    except Exception:
        pass


def read_google_voice_runtime_state() -> dict:
    try:
        raw = json.loads(GV_RUNTIME_STATE.read_text())
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def is_google_voice_runtime_ready(max_age_seconds: int = 86400) -> bool:
    state = read_google_voice_runtime_state()
    if not state.get("browser_ready") or not state.get("logged_in"):
        return False
    updated_at = str(state.get("updated_at") or "").strip()
    if max_age_seconds <= 0 or not updated_at:
        return True
    try:
        updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        age = (datetime.utcnow() - updated.replace(tzinfo=None)).total_seconds()
        return age <= max_age_seconds
    except Exception:
        return True


def _sanitize_thread_message(message: str, phone: str = "") -> str:
    """Normalize thread preview text into clean inbound body text."""
    text = (message or "")
    # Google Voice injects directional unicode marks in previews.
    text = re.sub(r"[\u200e\u200f\u202a-\u202e]", "", text).strip()
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""

    # Remove directional prefixes that sometimes appear in list previews.
    text = re.sub(r"^\s*you\s*:\s*", "", text, flags=re.IGNORECASE)

    # Ignore non-message thread chrome labels.
    if text.lower() in {"unread", "read", "person", "report", "suspected spam"}:
        return ""
    if re.fullmatch(r"[.\-•·]+", text):
        return ""

    # Remove leading sender phone label such as "(818) 448-9055 • hello".
    text = re.sub(
        r"^\s*\+?1?\s*\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}(?:\s*[•·\-:]\s*|\s+)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # Remove spaced-digit phone labels such as "8 1 8 4 4 8 9 0 5 5 • hello".
    text = re.sub(r"^\s*(?:\d\s*){10,11}(?:\s*[•·\-:]\s*|\s+)", "", text)
    # Strip trailing unread marker if appended in subtitle text.
    text = re.sub(r"\s*[•·\-:]*\s*(unread|read)\s*$", "", text, flags=re.IGNORECASE)

    if phone:
        phone_variants = {
            phone,
            f"+1{phone}" if len(phone) == 10 else phone,
            f"{phone[:3]}-{phone[3:6]}-{phone[6:]}" if len(phone) == 10 else phone,
            f"({phone[:3]}) {phone[3:6]}-{phone[6:]}" if len(phone) == 10 else phone,
        }
        for variant in phone_variants:
            if variant and text.lower().startswith(variant.lower()):
                tail = text[len(variant):].lstrip(" •·-:")
                if tail:
                    text = tail
                break

    return text.strip()


def _kill_stale_chrome():
    """Kill any Chrome processes using the GV profile and clean lock files."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "gv_chrome_profile"],
            capture_output=True, text=True, timeout=5
        )
        pids = result.stdout.strip().split("\n")
        my_pid = os.getpid()
        for pid_str in pids:
            pid_str = pid_str.strip()
            if pid_str and pid_str.isdigit():
                pid = int(pid_str)
                if pid != my_pid:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        if any(p.strip() for p in pids):
            print(f"[GV] Killed stale Chrome processes: {[p.strip() for p in pids if p.strip()]}")
    except Exception:
        pass
    # Clean Chrome profile lock files
    for lock in ["SingletonLock", "SingletonSocket", "SingletonCookie"]:
        (GV_PROFILE / lock).unlink(missing_ok=True)


def _hide_google_voice_windows():
    """Minimize only Google Voice Chrome windows so automation stays in the background."""
    left, top, right, bottom = GV_HIDDEN_BOUNDS
    script_lines = [
        'tell application "Google Chrome"',
        "repeat with w in every window",
        "try",
        "set shouldHide to false",
        "repeat with t in every tab of w",
        'set tabUrl to URL of t',
        'if tabUrl contains "voice.google.com" then',
        "set shouldHide to true",
        "exit repeat",
        "end if",
        "end repeat",
        "if shouldHide then",
        f"set bounds of w to {{{left}, {top}, {right}, {bottom}}}",
        "set miniaturized of w to true",
        "end if",
        "end try",
        "end repeat",
        "end tell",
    ]
    try:
        cmd = ["osascript"]
        for line in script_lines:
            cmd.extend(["-e", line])
        subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
    except Exception:
        pass


class GoogleVoiceSMS:
    def __init__(
        self,
        from_number: str = "8184489055",
        background_mode: bool = True,
        hide_window_interval_seconds: float = GV_WINDOW_KEEPER_INTERVAL_SECONDS,
    ):
        self.context = None
        self.page = None
        self.from_number = from_number
        self.background_mode = bool(background_mode)
        self.hide_window_interval_seconds = max(0.5, float(hide_window_interval_seconds or GV_WINDOW_KEEPER_INTERVAL_SECONDS))
        self.logged_in = False
        self._pw = None  # playwright instance
        self._lock = asyncio.Lock()  # serialize all page operations
        self._window_keeper_task = None
        # phone -> {"message": str, "signature": str, "is_unread": bool}
        # Signature includes more than just the preview line so repeated
        # short replies like "Hey" don't get dropped as duplicates.
        self._known_threads: dict = {}
        self._last_messages_nav_monotonic = 0.0

    @staticmethod
    def _is_messages_url(url: str) -> bool:
        return str(url or "").startswith(f"{GV_URL}/u/0/messages")

    async def _messages_view_is_ready(self) -> bool:
        if not self.page:
            return False
        try:
            return bool(
                await self.page.query_selector(
                    'cdk-virtual-scroll-viewport ol.list > li.list-item, '
                    '[role="tab"][aria-label="Messages"], '
                    '[aria-label="Call panel"]'
                )
            )
        except Exception:
            return False

    async def _background_window(self, force: bool = False) -> None:
        if not self.background_mode:
            return
        if not force and not self.logged_in:
            return
        await asyncio.to_thread(_hide_google_voice_windows)

    async def _window_keeper_loop(self) -> None:
        while True:
            try:
                if self.page:
                    await self._background_window(force=True)
                await asyncio.sleep(self.hide_window_interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(self.hide_window_interval_seconds)

    def _start_window_keeper(self) -> None:
        if not self.background_mode:
            return
        if self._window_keeper_task and not self._window_keeper_task.done():
            return
        self._window_keeper_task = asyncio.create_task(self._window_keeper_loop())

    async def _stop_window_keeper(self) -> None:
        task = self._window_keeper_task
        self._window_keeper_task = None
        if not task:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _goto_messages(
        self,
        timeout: int = 30000,
        wait_until: str = "domcontentloaded",
        force_refresh: bool = False,
    ) -> None:
        if not self.page:
            raise RuntimeError("Google Voice page is not initialized")
        current_url = ""
        try:
            current_url = str(self.page.url or "")
        except Exception:
            current_url = ""
        on_messages_page = self._is_messages_url(current_url)
        if on_messages_page:
            try:
                await self.page.wait_for_load_state(wait_until, timeout=min(timeout, 5000))
            except Exception:
                pass
            recently_navigated = (time.monotonic() - self._last_messages_nav_monotonic) < 90
            if not force_refresh or recently_navigated or await self._messages_view_is_ready():
                await self._background_window(force=True)
                return
            try:
                await self.page.reload(wait_until=wait_until, timeout=timeout)
            except Exception:
                await self.page.goto(f"{GV_URL}/u/0/messages", wait_until=wait_until, timeout=timeout)
        else:
            await self.page.goto(f"{GV_URL}/u/0/messages", wait_until=wait_until, timeout=timeout)
        self._last_messages_nav_monotonic = time.monotonic()
        await self._background_window(force=True)

    async def init_context(self):
        """Launch persistent Chrome profile in headed mode (off-screen)."""
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        GV_PROFILE.mkdir(parents=True, exist_ok=True)

        # Kill stale Chrome processes using our profile (from previous server runs)
        _kill_stale_chrome()

        for attempt in range(2):
            try:
                from playwright.async_api import async_playwright
                self._pw = await async_playwright().start()
                # Headed mode — Google blocks headless.
                # On macOS desktop this just means a background Chrome window.
                self.context = await self._pw.chromium.launch_persistent_context(
                    str(GV_PROFILE),
                    headless=False,
                    channel="chrome",
                    viewport={"width": 1280, "height": 900},
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--start-minimized",
                        "--window-position=-32000,-32000",  # push off-screen harder than a second monitor edge
                    ],
                    ignore_default_args=["--enable-automation"],
                )
                self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
                self._start_window_keeper()
                await self._background_window(force=True)
                _write_runtime_state(
                    browser_ready=True,
                    logged_in=False,
                    from_number=self.from_number,
                    pid=os.getpid(),
                )
                print("[GV] Persistent context loaded (headed)")
                return True
            except Exception as e:
                _write_runtime_state(
                    browser_ready=False,
                    logged_in=False,
                    from_number=self.from_number,
                    pid=os.getpid(),
                    last_error=str(e)[:300],
                )
                print(f"[GV] init_context attempt {attempt+1} failed: {e}")
                if attempt == 0:
                    # Force cleanup and retry
                    _kill_stale_chrome()
                    await asyncio.sleep(3)
                    if self._pw:
                        try: await self._pw.stop()
                        except: pass
                        self._pw = None
        print("[GV] Failed to init context after retries")
        return False

    async def _reconnect(self) -> bool:
        """Close and reinitialize the browser context."""
        print("[GV] Reconnecting browser...")
        await self.close()
        ok = await self.init_context()
        if ok:
            await self.ensure_login()
        return self.logged_in

    async def ensure_login(self) -> bool:
        """Navigate to voice.google.com, check if logged in."""
        try:
            await self._goto_messages(timeout=30000, force_refresh=False)
            # Give the page extra time to settle / redirect
            await asyncio.sleep(3)
            url = self.page.url
            print(f"[GV] Login check URL: {url}")
            if "voice.google.com" in url and "accounts.google.com" not in url:
                self.logged_in = True
                _write_runtime_state(
                    browser_ready=True,
                    logged_in=True,
                    from_number=self.from_number,
                    pid=os.getpid(),
                    page_url=url,
                    last_error="",
                )
                await self._background_window()
                print("[GV] Session is valid")
                return True
            self.logged_in = False
            _write_runtime_state(
                browser_ready=True,
                logged_in=False,
                from_number=self.from_number,
                pid=os.getpid(),
                page_url=url,
                last_error="redirected_to_login",
            )
            await self._debug_screenshot("gv_not_logged_in")
            print("[GV] Not logged in (redirected to Google login)")
            return False
        except Exception as e:
            _write_runtime_state(
                browser_ready=bool(self.page),
                logged_in=False,
                from_number=self.from_number,
                pid=os.getpid(),
                last_error=str(e)[:300],
            )
            print(f"[GV] Login check failed: {e}")
            await self._debug_screenshot("gv_login_check_error")
            return False

    async def manual_login(self) -> bool:
        """
        Launch headed browser for user to log in to Google.
        Uses the same persistent Chrome profile so cookies persist.
        """
        try:
            # Close any existing context first (can't share the profile)
            await self.close()

            from playwright.async_api import async_playwright
            pw = await async_playwright().start()
            GV_PROFILE.mkdir(parents=True, exist_ok=True)
            context = await pw.chromium.launch_persistent_context(
                str(GV_PROFILE),
                headless=False,
                channel="chrome",
                viewport={"width": 1280, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"],
            )
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(f"{GV_URL}/u/0/messages")

            print("[GV] Waiting for manual login... (sign into your Google account)")

            for _ in range(150):  # 5 min timeout
                await asyncio.sleep(2)
                url = page.url
                if "voice.google.com" in url and "accounts.google.com" not in url:
                    print(f"[GV] Login detected! URL: {url}")
                    await context.close()
                    await pw.stop()

                    # Re-init context with same profile (now has cookies)
                    await self.init_context()
                    self.logged_in = True
                    return True

            print("[GV] Login timeout (5 min)")
            await context.close()
            await pw.stop()
            # Re-init context even if login failed
            await self.init_context()
            return False

        except Exception as e:
            print(f"[GV] Manual login error: {e}")
            traceback.print_exc()
            return False

    async def send_sms(self, to_phone: str, message: str, approval_id: int = None) -> dict:
        """Send SMS via Google Voice web interface (serialized with lock, auto-retry)."""
        from integrations.messaging import outbound_gate
        gate = outbound_gate("sms", to_phone, "", message,
                             approval_id=approval_id, code_path="GoogleVoice.send_sms")
        if not gate["ok"]:
            return {"ok": False, "error": f"BLOCKED: {gate['reason']}", "method": "blocked"}
        async with self._lock:
            result = await self._send_sms_inner(to_phone, message)
            # If Chrome died, reconnect and retry once
            if not result.get("ok") and "closed" in result.get("error", "").lower():
                print("[GV] Browser died during send — reconnecting and retrying...")
                if await self._reconnect():
                    result = await self._send_sms_inner(to_phone, message)
            return result

    async def place_call(self, to_phone: str, message: str = "") -> dict:
        """Place a Google Voice browser-based alert call using the active session."""
        async with self._lock:
            result = await self._place_call_inner(to_phone, message)
            if not result.get("ok") and "closed" in result.get("error", "").lower():
                print("[GV] Browser died during call placement — reconnecting and retrying...")
                if await self._reconnect():
                    result = await self._place_call_inner(to_phone, message)
            return result

    async def _place_call_inner(self, to_phone: str, message: str = "") -> dict:
        if not self.logged_in or not self.page:
            return {"ok": False, "error": "Not logged in to Google Voice", "method": "google_voice_call"}

        digits = _clean_phone(to_phone)
        if len(digits) != 10:
            return {"ok": False, "error": f"Invalid phone: {to_phone}", "method": "google_voice_call"}

        panel_selector = '[aria-label="Call panel"]'
        input_selector = f'{panel_selector} input[placeholder="Enter a name or number"]'
        call_button_selector = f'{panel_selector} button[gv-test-id="new-call-button"]'

        try:
            await self._goto_messages(timeout=30000, force_refresh=False)
            await asyncio.sleep(2)

            try:
                await self.page.wait_for_selector(panel_selector, timeout=5000)
            except Exception:
                await self.page.click('[role="tab"][aria-label="Calls"]', timeout=5000)
                await asyncio.sleep(2)
                await self.page.wait_for_selector(panel_selector, timeout=8000)

            phone_input = await self.page.wait_for_selector(input_selector, timeout=8000)
            await phone_input.click()
            await phone_input.fill("")
            await phone_input.fill(f"+1{digits}")
            await asyncio.sleep(1.5)

            await self.page.wait_for_function(
                """({selector, digits}) => {
                    const btn = document.querySelector(selector);
                    if (!btn || btn.disabled) return false;
                    const label = (btn.getAttribute('aria-label') || '').replace(/\\D/g, '');
                    const want = `1${digits}`;
                    return !label || label.endsWith(want) || label.endsWith(digits);
                }""",
                arg={"selector": call_button_selector, "digits": digits},
                timeout=6000,
            )
            call_button = await self.page.query_selector(call_button_selector)
            if not call_button:
                await self._debug_screenshot("gv_call_no_button")
                return {
                    "ok": False,
                    "error": "Could not find Google Voice call button",
                    "method": "google_voice_call",
                }

            await call_button.click()
            await asyncio.sleep(2)

            call_active = await self.page.evaluate(
                """(panelSelector) => {
                    const panel = document.querySelector(panelSelector);
                    if (!panel) return false;
                    const activeRoot = panel.querySelector('.messages-view:not(.no-active-call), .active-call, .in-call');
                    if (activeRoot) return true;
                    const labels = Array.from(panel.querySelectorAll('button,[role="button"]'))
                        .map(el => `${el.getAttribute('aria-label') || ''} ${el.innerText || el.textContent || ''}`.toLowerCase());
                    return labels.some(label => /end call|hang up|mute|keypad|dialpad|calling|call in progress|speaker/.test(label));
                }""",
                panel_selector,
            )
            note = f"Placed a Google Voice alert call to {digits}."
            if not call_active:
                note = (
                    f"Triggered a Google Voice alert call to {digits}; "
                    "waiting for the browser call panel to confirm the live session."
                )
            if message:
                note += " Spoken voice prompts still require Twilio."
            return {
                "ok": True,
                "to": digits,
                "method": "google_voice_call",
                "call_active": bool(call_active),
                "note": note,
            }
        except Exception as e:
            await self._debug_screenshot("gv_call_error")
            return {"ok": False, "error": str(e), "method": "google_voice_call"}

    async def _send_sms_inner(self, to_phone: str, message: str) -> dict:
        if not self.logged_in or not self.page:
            return {"ok": False, "error": "Not logged in to Google Voice"}

        digits = _clean_phone(to_phone)
        if len(digits) != 10:
            return {"ok": False, "error": f"Invalid phone: {to_phone}"}

        try:
            # Navigate to messages
            await self._goto_messages(timeout=30000, force_refresh=False)
            await asyncio.sleep(2)

            # Step 1: Click "Send new message" — use text matching (most reliable)
            try:
                await self.page.click('text="Send new message"', timeout=5000)
                print("[GV] Clicked 'Send new message'")
            except Exception:
                # Fallback: try aria-label or other selectors
                try:
                    await self.page.click('[aria-label*="Send new message"]', timeout=3000)
                except Exception:
                    await self._debug_screenshot("gv_no_send_btn")
                    return {"ok": False, "error": "Could not find 'Send new message' button"}
            await asyncio.sleep(2)

            # Step 2: Wait for the "To" recipient input in the compose panel
            # This is different from the Call panel's "Enter a name or number"
            try:
                phone_input = await self.page.wait_for_selector(
                    'input[aria-label*="Type a name or phone"], '
                    'input[placeholder*="Type a name"], '
                    'input[aria-label*="Add people"]',
                    timeout=8000
                )
            except Exception:
                # Broader fallback — but avoid the call panel
                await self._debug_screenshot("gv_compose_not_open")
                # Try to find any input that appeared after clicking Send new message
                phone_input = await self.page.query_selector(
                    'input[placeholder*="name or phone"], input[placeholder*="Type a name"]'
                )
                if not phone_input:
                    return {"ok": False, "error": "Compose panel didn't open — could not find recipient input"}

            # Step 3: Type phone number
            formatted = f"+1{digits}"
            await phone_input.fill(formatted)
            await asyncio.sleep(2)

            # Step 4: Click the "Send to (XXX) XXX-XXXX" suggestion button
            # GV shows a dropdown with a send_to_button after typing the number
            try:
                send_to = await self.page.wait_for_selector(
                    '.send-to-button, [id^="send_to_button"]',
                    timeout=5000
                )
                await send_to.click()
                print("[GV] Clicked 'Send to' suggestion")
            except Exception:
                # Fallback: try clicking text that contains "Send to"
                try:
                    await self.page.click('text="Send to"', timeout=3000)
                except Exception:
                    # Last resort: press Enter and hope it works
                    await phone_input.press("Enter")
            await asyncio.sleep(3)

            # Step 5: Dismiss any remaining overlay by clicking the message area
            # The CDK overlay might still be present
            try:
                backdrop = await self.page.query_selector('.cdk-overlay-backdrop')
                if backdrop:
                    await self.page.evaluate('document.querySelector(".cdk-overlay-backdrop")?.remove()')
                    await asyncio.sleep(0.5)
            except Exception:
                pass

            # Step 6: Wait for message input to appear
            msg_input = None
            for selector in [
                'textarea[aria-label*="Type a message"]',
                'textarea[placeholder*="Type a message"]',
                '[contenteditable="true"][aria-label*="message"]',
                'textarea',
                '[contenteditable="true"][role="textbox"]',
            ]:
                try:
                    msg_input = await self.page.wait_for_selector(selector, timeout=3000)
                    if msg_input:
                        break
                except Exception:
                    continue

            if not msg_input:
                await self._debug_screenshot("gv_no_msg_input")
                return {"ok": False, "error": "Could not find message input after selecting recipient"}

            # Step 7: Type and send message
            await msg_input.click()
            await asyncio.sleep(0.3)
            await msg_input.fill(message)
            await asyncio.sleep(0.5)

            # Click send button or press Enter
            send_msg_btn = await self.page.query_selector(
                'button[aria-label="Send message"], '
                'button[aria-label="Send"], '
                '[data-tooltip="Send message"], '
                'gv-icon-button[icon-name="send"] button, '
                'button[class*="send"]'
            )
            if send_msg_btn:
                await send_msg_btn.click()
            else:
                await msg_input.press("Enter")

            await asyncio.sleep(2)

            print(f"[GV] SMS sent to {digits}: {message[:50]}")
            return {"ok": True, "to": digits, "method": "google_voice"}

        except Exception as e:
            await self._debug_screenshot("gv_send_error")
            return {"ok": False, "error": str(e)}

    async def check_replies(self) -> list[dict]:
        """Check for new replies (serialized with lock, auto-reconnect)."""
        async with self._lock:
            try:
                return await self._check_replies_inner()
            except Exception as e:
                if "closed" in str(e).lower():
                    print("[GV] Browser died during reply check — reconnecting...")
                    await self._reconnect()
                return []

    async def _check_replies_inner(self) -> list[dict]:
        if not self.page or not self.logged_in:
            return []

        try:
            await self._goto_messages(timeout=30000, force_refresh=False)
            await asyncio.sleep(2)

            # Extract conversation list with last messages
            threads = await self.page.evaluate("""() => {
                const results = [];
                const seen = new Set();

                function cleanPhone(text) {
                    if (!text) return '';
                    const digits = text.replace(/\\D/g, '');
                    if (digits.length === 11 && digits[0] === '1') return digits.slice(1);
                    return digits;
                }

                function pushThread(item, text, phoneHint = '') {
                    const lines = (text || '').split('\\n').map(l => l.trim()).filter(Boolean);
                    const phoneMatch = (text || '').match(/(\\+?1?\\s?\\(?\\d{3}\\)?[\\s.-]?\\d{3}[\\s.-]?\\d{4})/);
                    const rawPhone = phoneHint || (phoneMatch ? phoneMatch[1] : '');
                    const phone = cleanPhone(rawPhone);
                    if (!phone || phone.length !== 10) return;
                    if (seen.has(phone)) return;
                    seen.add(phone);

                    const noise = /^(Mon|Tue|Wed|Thu|Fri|Sat|Sun|Today|Yesterday|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|\\d{1,2}:\\d{2}|\\(?\\d{3}\\)?[\\s.-]?\\d{3}[\\s.-]?\\d{4})/i;
                    const spacedDigits = /^(?:\\d\\s*){10,11}$/;
                    function isMetaLine(ln) {
                        const s = (ln || '').trim();
                        if (!s) return true;
                        if (noise.test(s)) return true;
                        if (/^[.·•-]+$/.test(s)) return true;
                        if (spacedDigits.test(s)) return true;
                        const lower = s.toLowerCase();
                        if (lower === 'person' || lower === 'report' || lower === 'suspected spam') return true;
                        if (lower === 'unread' || lower === 'read') return true;
                        return false;
                    }

                    const previewCandidates = [
                        item.querySelector('gv-annotation.preview'),
                        item.querySelector('.subtitle gv-annotation'),
                        item.querySelector('.subtitle'),
                    ];
                    let previewText = '';
                    for (const node of previewCandidates) {
                        if (!node) continue;
                        const raw = (node.textContent || '').replace(/\\u202a|\\u202c/g, '');
                        const candidate = raw.split('\\n').map(v => v.trim()).filter(Boolean)
                            .find((v) => !isMetaLine(v));
                        if (candidate) {
                            previewText = candidate;
                            break;
                        }
                    }

                    const contentLines = lines.filter((ln) => !isMetaLine(ln) && !/^you:$/i.test(ln));

                    const isUnread =
                        item.querySelector('[class*="unread"], [class*="bold"], [aria-label*="unread"]') !== null ||
                        (item.className || '').toString().toLowerCase().includes('unread') ||
                        /(^|\\n)\\s*unread\\s*($|\\n)/i.test(text || '');

                    const name = contentLines[0] || lines[0] || '';
                    const lastMsg = previewText || (
                        contentLines.length > 1
                            ? contentLines[contentLines.length - 1]
                            : (contentLines[0] || '')
                    );

                    results.push({
                        phone: rawPhone,
                        phone_digits: phone,
                        name: name,
                        last_message: lastMsg,
                        last_message_raw: previewText || lastMsg,
                        is_unread: !!isUnread,
                        full_text: (text || '').slice(0, 500),
                    });
                }

                // Preferred selector: GV thread list (most stable)
                const lis = document.querySelectorAll('cdk-virtual-scroll-viewport ol.list > li.list-item');
                for (const li of lis) {
                    const text = li.innerText || '';
                    const phoneEl = li.querySelector('gv-annotation.participants');
                    const phoneText = phoneEl ? (phoneEl.textContent || '') : '';
                    pushThread(li, text, phoneText);
                }

                // Fallback selector for layout changes
                if (results.length === 0) {
                    const items = document.querySelectorAll(
                        '[class*="thread-item"], [class*="message-item"], ' +
                        '[class*="conversation-item"], [role="listitem"]'
                    );
                    for (const item of items) {
                        pushThread(item, item.innerText || '');
                    }
                }
                return results;
            }""")

            new_replies = []
            for thread in threads:
                raw_msg = str(thread.get("last_message_raw", "") or thread.get("last_message", "") or "")
                phone = _clean_phone(thread.get("phone_digits") or thread.get("phone", ""))
                msg = _sanitize_thread_message(thread.get("last_message", ""), phone)
                if not phone or not msg:
                    continue

                cache_key = phone
                full_text = str(thread.get("full_text", "") or "").strip()
                signature = full_text[:500] if full_text else msg
                is_unread = bool(thread.get("is_unread"))

                prev_state = self._known_threads.get(cache_key) or {}
                if isinstance(prev_state, str):
                    prev_msg = prev_state
                    prev_sig = prev_state
                    prev_unread = False
                else:
                    prev_msg = str(prev_state.get("message", "") or "")
                    prev_sig = str(prev_state.get("signature", "") or "")
                    prev_unread = bool(prev_state.get("is_unread"))

                # First sighting: if the thread is unread, surface it immediately.
                if not prev_msg and not prev_sig:
                    self._known_threads[cache_key] = {
                        "message": msg,
                        "signature": signature,
                        "is_unread": is_unread,
                    }
                    if not is_unread:
                        continue
                else:
                    # No change since last poll.
                    same_preview = (prev_msg == msg)
                    same_signature = (prev_sig == signature)
                    unread_became_true = (is_unread and not prev_unread)
                    if same_preview and same_signature and not unread_became_true:
                        self._known_threads[cache_key] = {
                            "message": msg,
                            "signature": signature,
                            "is_unread": is_unread,
                        }
                        continue

                # Best-effort guard against classifying our own outbound as inbound.
                raw_lower = raw_msg.strip().lower()
                msg_lower = msg.strip().lower()
                if raw_lower.startswith("you:") or raw_lower.startswith("you ") or msg_lower.startswith("you:") or msg_lower.startswith("you "):
                    self._known_threads[cache_key] = {
                        "message": msg,
                        "signature": signature,
                        "is_unread": is_unread,
                    }
                    continue

                self._known_threads[cache_key] = {
                    "message": msg,
                    "signature": signature,
                    "is_unread": is_unread,
                }

                new_replies.append({
                    "phone": phone,
                    "message": msg,
                    "timestamp": datetime.utcnow().isoformat(),
                    "name": thread.get("name", ""),
                })

            if new_replies:
                print(f"[GV] Found {len(new_replies)} new replies")

            return new_replies

        except Exception as e:
            print(f"[GV] Reply check error: {e}")
            # Bubble up closed-browser errors so check_replies() can reconnect.
            if "closed" in str(e).lower():
                raise
            return []

    async def fetch_conversation_history(self, phone: str) -> list[dict]:
        """Fetch full conversation history with a phone number from Google Voice."""
        async with self._lock:
            return await self._fetch_conversation_inner(phone)

    async def _fetch_conversation_inner(self, phone: str) -> list[dict]:
        if not self.page or not self.logged_in:
            return []
        target = _clean_phone(phone)
        if not target:
            return []

        try:
            # Navigate to messages list
            await self._goto_messages(timeout=30000)
            await asyncio.sleep(5)

            # Find and click the thread — use same selectors as _fetch_all_inner (proven working)
            clicked = False
            for scroll in range(30):
                idx = await self.page.evaluate("""(targetPhone) => {
                    const lis = document.querySelectorAll('cdk-virtual-scroll-viewport ol.list > li.list-item');
                    for (let i = 0; i < lis.length; i++) {
                        const phoneEl = lis[i].querySelector('gv-annotation.participants');
                        const phone = phoneEl ? phoneEl.textContent.replace(/\\u202a|\\u202c/g, '').replace(/[^0-9]/g, '') : '';
                        const clean = phone.length === 11 && phone[0] === '1' ? phone.slice(1) : phone;
                        if (clean === targetPhone) return i;
                    }
                    return -1;
                }""", target)
                if idx >= 0:
                    try:
                        await self.page.click(f"cdk-virtual-scroll-viewport ol.list > li.list-item:nth-child({idx+1})")
                        clicked = True
                    except Exception:
                        pass
                    break
                # Scroll to load more threads
                await self.page.evaluate("""() => {
                    const vp = document.querySelector('cdk-virtual-scroll-viewport');
                    if (vp) vp.scrollTop += 400;
                }""")
                await asyncio.sleep(0.5)

            if not clicked:
                print(f"[GV] No conversation thread found for {phone}")
                return []

            await asyncio.sleep(4)

            # Use the proven message scraper
            messages = await self._scrape_conversation_messages()

            # Navigate back to prevent state issues
            await self._goto_messages(timeout=15000, force_refresh=False)

            print(f"[GV] Fetched {len(messages)} messages for {phone}")
            return messages

        except Exception as e:
            print(f"[GV] Conversation history error: {e}")
            return []

    async def fetch_all_conversations(self) -> list[dict]:
        """Fetch ALL conversation threads from Google Voice messages page.
        Returns list of {phone, name, messages: [{direction, content, timestamp, channel}]}.
        Scrolls the conversation list to load all threads, then opens each one to scrape messages.
        """
        async with self._lock:
            return await self._fetch_all_inner()

    async def _fetch_all_inner(self) -> list[dict]:
        if not self.page or not self.logged_in:
            return []
        try:
            await self._goto_messages(timeout=30000, force_refresh=False)
            await asyncio.sleep(2)

            # GV uses Angular CDK virtual scroll — only ~10 items render at a time.
            # We need to scroll the viewport to make all items load, collecting phone numbers as we go.
            all_phones = []
            seen = set()
            for scroll_attempt in range(50):
                # Extract currently visible thread items
                items = await self.page.evaluate("""() => {
                    const results = [];
                    const lis = document.querySelectorAll('cdk-virtual-scroll-viewport ol.list > li.list-item');
                    for (const li of lis) {
                        const phoneEl = li.querySelector('gv-annotation.participants');
                        const phone = phoneEl ? phoneEl.textContent.replace(/\\u202a|\\u202c/g, '').trim() : '';
                        const text = (li.innerText || '').split('\\n').filter(l => l.trim());
                        results.push({phone: phone, name: text[0] || ''});
                    }
                    return results;
                }""")

                new_found = 0
                for item in items:
                    phone = _clean_phone(item.get("phone", ""))
                    if phone and len(phone) >= 10 and phone not in seen:
                        seen.add(phone)
                        # Clean the name — GV shows "person" for the avatar icon, skip that
                        name = item.get("name", "")
                        if name.lower() in ("person", "report", ""):
                            name = ""
                        all_phones.append({"phone": phone, "name": name})
                        new_found += 1

                if new_found == 0 and scroll_attempt > 2:
                    break

                # Scroll the virtual viewport down
                await self.page.evaluate("""() => {
                    const vp = document.querySelector('cdk-virtual-scroll-viewport');
                    if (vp) vp.scrollTop += 500;
                }""")
                await asyncio.sleep(1.0)

            print(f"[GV] Discovered {len(all_phones)} unique contacts")

            # Now click into each contact and scrape their messages
            all_convos = []
            for i, contact in enumerate(all_phones):
                phone = contact["phone"]
                try:
                    # Navigate fresh each time to ensure clean state
                    await self._goto_messages(timeout=15000, force_refresh=False)
                    await asyncio.sleep(2)

                    # Find and click the thread for this phone using Playwright click
                    clicked = False
                    for _ in range(30):
                        # Check if the phone is visible in the list
                        idx = await self.page.evaluate("""(targetPhone) => {
                            const lis = document.querySelectorAll('cdk-virtual-scroll-viewport ol.list > li.list-item');
                            for (let i = 0; i < lis.length; i++) {
                                const phoneEl = lis[i].querySelector('gv-annotation.participants');
                                const phone = phoneEl ? phoneEl.textContent.replace(/\\u202a|\\u202c/g, '').replace(/[^0-9]/g, '') : '';
                                const clean = phone.length === 11 && phone[0] === '1' ? phone.slice(1) : phone;
                                if (clean === targetPhone) return i;
                            }
                            return -1;
                        }""", phone)
                        if idx >= 0:
                            # Use Playwright's click on the nth-child
                            try:
                                await self.page.click(f"cdk-virtual-scroll-viewport ol.list > li.list-item:nth-child({idx+1})")
                                clicked = True
                            except:
                                pass
                            break
                        await self.page.evaluate("""() => {
                            const vp = document.querySelector('cdk-virtual-scroll-viewport');
                            if (vp) vp.scrollTop += 400;
                        }""")
                        await asyncio.sleep(0.5)

                    if not clicked:
                        print(f"[GV] Could not find thread for {phone}, skipping")
                        continue

                    await asyncio.sleep(4)

                    # Scrape messages from the conversation view
                    messages = await self._scrape_conversation_messages()

                    all_convos.append({
                        "phone": phone,
                        "name": contact.get("name", ""),
                        "messages": messages,
                    })
                    print(f"[GV] Thread {i+1}/{len(all_phones)}: {phone} — {len(messages)} msgs")

                except Exception as e:
                    print(f"[GV] Error scraping thread for {phone}: {e}")
                    continue

            print(f"[GV] Total: scraped {len(all_convos)} conversations with {sum(len(c['messages']) for c in all_convos)} messages")
            return all_convos

        except Exception as e:
            print(f"[GV] fetch_all_conversations error: {e}")
            import traceback
            traceback.print_exc()
            return []

    async def fetch_contact_names(self) -> list[dict]:
        """Fetch contact names from GV thread headers.
        Opens each conversation thread and extracts the contact name from the header.
        Returns list of {phone, name} dicts where name is non-empty.
        """
        async with self._lock:
            return await self._fetch_names_inner()

    async def _fetch_names_inner(self) -> list[dict]:
        if not self.page or not self.logged_in:
            return []
        try:
            await self._goto_messages(timeout=30000, force_refresh=False)
            await asyncio.sleep(2)

            # Collect all phone numbers from the thread list
            all_phones = []
            seen = set()
            for scroll_attempt in range(50):
                items = await self.page.evaluate("""() => {
                    const results = [];
                    const lis = document.querySelectorAll('cdk-virtual-scroll-viewport ol.list > li.list-item');
                    for (const li of lis) {
                        const phoneEl = li.querySelector('gv-annotation.participants');
                        const phone = phoneEl ? phoneEl.textContent.replace(/\\u202a|\\u202c/g, '').trim() : '';
                        // Try to get name from the thread item text (first line that isn't the phone number)
                        const text = (li.innerText || '').split('\\n').filter(l => l.trim());
                        let name = '';
                        for (const line of text) {
                            const clean = line.trim();
                            // Skip phone numbers, timestamps, UI elements, dates, junk
                            if (clean.match(/^[\\d\\s().+-]+$/) || clean.match(/^\\d{1,2}:\\d{2}/)
                                || ['person', 'report', 'add_2', 'more_vert', 'suspected spam'].includes(clean.toLowerCase())
                                || clean.startsWith('Messages with')
                                || clean.length > 40 || clean.length < 3) continue;
                            // Skip month-day patterns like "Feb 3", "Jan 20", "Aug 14, 2024"
                            if (clean.match(/^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\\s+\\d/i)) continue;
                            // Skip day-of-week patterns
                            if (clean.match(/^(Mon|Tue|Wed|Thu|Fri|Sat|Sun|Today|Yesterday)/i)) continue;
                            // Must have at least 2 letters and look like a name (not all caps/numbers)
                            if (clean.match(/[a-zA-Z]{2,}/) && !clean.match(/^(You|Me|SMS|MMS):/i)) {
                                name = clean;
                                break;
                            }
                        }
                        results.push({phone: phone, name: name});
                    }
                    return results;
                }""")

                new_found = 0
                for item in items:
                    phone = _clean_phone(item.get("phone", ""))
                    if phone and len(phone) >= 10 and phone not in seen:
                        seen.add(phone)
                        name = item.get("name", "")
                        if name.lower() in ("person", "report", "", "add_2", "more_vert"):
                            name = ""
                        all_phones.append({"phone": phone, "name": name})
                        new_found += 1

                if new_found == 0 and scroll_attempt > 2:
                    break

                await self.page.evaluate("""() => {
                    const vp = document.querySelector('cdk-virtual-scroll-viewport');
                    if (vp) vp.scrollTop += 500;
                }""")
                await asyncio.sleep(1.0)

            # For contacts without names, try opening the thread to check the header
            results = []
            for contact in all_phones:
                phone = contact["phone"]
                name = contact.get("name", "")
                if name:
                    results.append({"phone": phone, "name": name})
                    print(f"[GV] Name from list: {phone} → {name}")
                    continue

                # Open thread to get header name
                try:
                    await self._goto_messages(timeout=15000, force_refresh=False)
                    await asyncio.sleep(2)

                    # Find and click the thread
                    for _ in range(30):
                        idx = await self.page.evaluate("""(targetPhone) => {
                            const lis = document.querySelectorAll('cdk-virtual-scroll-viewport ol.list > li.list-item');
                            for (let i = 0; i < lis.length; i++) {
                                const phoneEl = lis[i].querySelector('gv-annotation.participants');
                                const phone = phoneEl ? phoneEl.textContent.replace(/\\u202a|\\u202c/g, '').replace(/[^0-9]/g, '') : '';
                                const clean = phone.length === 11 && phone[0] === '1' ? phone.slice(1) : phone;
                                if (clean === targetPhone) return i;
                            }
                            return -1;
                        }""", phone)
                        if idx >= 0:
                            await self.page.click(f"cdk-virtual-scroll-viewport ol.list > li.list-item:nth-child({idx+1})")
                            break
                        await self.page.evaluate("""() => {
                            const vp = document.querySelector('cdk-virtual-scroll-viewport');
                            if (vp) vp.scrollTop += 400;
                        }""")
                        await asyncio.sleep(0.5)
                    else:
                        continue

                    await asyncio.sleep(2)

                    # Extract contact name from thread header
                    header_name = await self.page.evaluate("""() => {
                        // GV shows the contact name in the thread header
                        // Look for the main heading/name element
                        const selectors = [
                            'gv-contact-header [class*="name"]',
                            'gv-contact-header',
                            '[class*="thread-header"] [class*="name"]',
                            '[class*="contact-name"]',
                            'h2', 'h3',
                        ];
                        for (const sel of selectors) {
                            const el = document.querySelector(sel);
                            if (el) {
                                const text = el.textContent.trim();
                                // Skip if it's just a phone number
                                if (text && !text.match(/^[\\d\\s().+\\-\\u202a\\u202c]+$/)) {
                                    return text;
                                }
                            }
                        }
                        return '';
                    }""")

                    if header_name and header_name.lower() not in ("person", "report", "messages", ""):
                        results.append({"phone": phone, "name": header_name})
                        print(f"[GV] Name from header: {phone} → {header_name}")
                    else:
                        print(f"[GV] No name found for {phone}")

                except Exception as e:
                    print(f"[GV] Error checking name for {phone}: {e}")

            return results

        except Exception as e:
            print(f"[GV] fetch_contact_names error: {e}")
            import traceback
            traceback.print_exc()
            return []

    async def _scrape_conversation_messages(self) -> list[dict]:
        """Scrape messages from the currently open GV conversation thread."""
        messages = await self.page.evaluate("""() => {
            const results = [];
            const phoneLike = /^\\+?1?\\s*\\(?\\d{3}\\)?[\\s.\\-]?\\d{3}[\\s.\\-]?\\d{4}$/;
            const spacedDigits = /^(?:\\d\\s*){10,11}$/;
            const dayOrMonth = /^(Mon|Tue|Wed|Thu|Fri|Sat|Sun|Today|Yesterday|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)/i;
            function isUiLine(line) {
                const t = (line || '').trim();
                if (!t) return true;
                if (t === 'more_vert' || t === 'person' || t === 'report') return true;
                if (t.startsWith('Message from') || t.startsWith('Suspected spam')) return true;
                if (/^[•·.\\-]+$/.test(t)) return true;
                if (phoneLike.test(t) || spacedDigits.test(t)) return true;
                if (dayOrMonth.test(t) || /^\\d{1,2}:\\d{2}/.test(t)) return true;
                return false;
            }

            function parseFromAria(ariaLabel) {
                if (!ariaLabel || !ariaLabel.startsWith('Message from')) return '';
                const firstComma = ariaLabel.indexOf(',');
                if (firstComma < 0) return '';
                const dayIdx = ariaLabel.search(/,\\s*(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),/);
                if (dayIdx < 0 || dayIdx <= firstComma) return '';
                return ariaLabel.slice(firstComma + 1, dayIdx).trim();
            }

            // GV uses gv-text-message-item for each message
            const items = document.querySelectorAll('gv-text-message-item');
            for (const item of items) {
                // Check direction via the container class
                const container = item.querySelector('[class*="outgoing"], [class*="incoming"]');
                const isOutgoing = container ? container.classList.toString().includes('outgoing') : false;

                // Get the aria-label for full message info including timestamp
                const ariaLabel = item.getAttribute('aria-label') || '';
                // aria-label format: "Message from you, test, Wednesday, February 25 2026, 9:03 PM."
                // or "Message from (323) 812-2740, hello, Thursday, February 26 2026, 10:00 AM."

                // Prefer aria-label parsing first: it's more stable than innerText line ordering.
                let msgText = parseFromAria(ariaLabel);

                // Fallback to visible text parsing when aria-label extraction fails.
                const allText = (item.innerText || '').split('\\n').filter(l => l.trim());
                if (!msgText) {
                    msgText = allText.filter((l) => !isUiLine(l)).join(' ').trim();
                }

                if (!msgText || msgText.length < 1) continue;

                // Parse timestamp from aria-label
                let timestamp = '';
                const dateMatch = ariaLabel.match(/(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\\s*(.+?)\\./);
                if (dateMatch) {
                    timestamp = dateMatch[2].trim();
                }

                results.push({
                    direction: isOutgoing ? 'outbound' : 'inbound',
                    content: msgText.slice(0, 2000),
                    timestamp: timestamp,
                    channel: 'sms',
                });
            }
            return results;
        }""")
        return messages

    async def close(self):
        await self._stop_window_keeper()
        if self.context:
            try:
                await self.context.close()
            except:
                pass
            self.context = None
            self.page = None
        if self._pw:
            try:
                await self._pw.stop()
            except:
                pass
            self._pw = None
        _write_runtime_state(
            browser_ready=False,
            logged_in=False,
            from_number=self.from_number,
            pid=os.getpid(),
        )

    async def _debug_screenshot(self, name: str):
        try:
            debug_dir = STATE_DIR / "debug"
            debug_dir.mkdir(exist_ok=True)
            if self.page:
                await self.page.screenshot(
                    path=str(debug_dir / f"{name}_{datetime.now().strftime('%H%M%S')}.png")
                )
        except:
            pass


class GVReplyPoller:
    """Background task to check Google Voice for new replies."""

    def __init__(self, gv: GoogleVoiceSMS, interval: int = 30):
        self.gv = gv
        self.interval = interval
        self._running = False
        self._reply_callback = None

    def on_reply(self, callback):
        self._reply_callback = callback

    async def run(self):
        self._running = True
        print(f"[GV] Reply poller started (every {self.interval}s)")
        while self._running:
            try:
                if self.gv.logged_in:
                    replies = await self.gv.check_replies()
                    for r in replies:
                        if self._reply_callback:
                            await self._reply_callback(
                                r["phone"],
                                r["message"],
                                r["timestamp"],
                                r.get("name", ""),
                            )
            except Exception as e:
                print(f"[GV] Reply poller error: {e}")
                traceback.print_exc()
            await asyncio.sleep(self.interval)

    def stop(self):
        self._running = False


def _clean_phone(p: str) -> str:
    d = "".join(c for c in str(p) if c.isdigit())
    if len(d) == 11 and d[0] == "1":
        d = d[1:]
    return d


# ── Module-level init ─────────────────────────────────────────────────────────

async def init_google_voice(config: dict):
    """
    Initialize Google Voice SMS.
    Returns (GoogleVoiceSMS, GVReplyPoller, error_string).
    """
    global _gv
    try:
        from_number = config.get("gv_number", "8184489055")
        background_mode = config.get("google_voice_background_mode", True)
        hide_window_interval_seconds = config.get(
            "google_voice_hide_interval_seconds",
            GV_WINDOW_KEEPER_INTERVAL_SECONDS,
        )
        _gv = GoogleVoiceSMS(
            from_number,
            background_mode=background_mode,
            hide_window_interval_seconds=hide_window_interval_seconds,
        )
        ctx_ready = await _gv.init_context()
        if not ctx_ready:
            _gv = None
            return None, None, "Google Voice browser context failed to initialize"

        logged_in = await _gv.ensure_login()
        if not logged_in:
            print("[GV] No valid session — use /api/browser/login-gv to log in")

        poller = GVReplyPoller(_gv, int(config.get("gv_poll_interval", 10)))
        return _gv, poller, ""

    except Exception as e:
        return None, None, str(e)


def get_gv() -> GoogleVoiceSMS | None:
    return _gv
