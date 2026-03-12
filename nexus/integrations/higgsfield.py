from __future__ import annotations
"""
Higgsfield image generation via Playwright browser automation.
- Automates bulk image generation on higgsfield.ai
- Uses Nano Banana Pro model, 9:16 aspect ratio, 2K quality
- Manages queue (max 4 concurrent), unlimited mode verification
- Persistent Chrome profile for Google login persistence
- Progress persistence across server restarts
- Delete/regenerate support

Modeled after integrations/google_voice.py.
"""
import asyncio, json, os, re, signal, subprocess, traceback, base64, hashlib, logging
from pathlib import Path
from datetime import datetime

# File-based logger for debugging — writes to ~/.nexus/higgsfield/hf_debug.log
_hf_log_path = Path.home() / ".nexus" / "higgsfield" / "hf_debug.log"
_hf_log_path.parent.mkdir(parents=True, exist_ok=True)
hf_logger = logging.getLogger("hf_static_ads")
hf_logger.setLevel(logging.DEBUG)
if not hf_logger.handlers:
    _fh = logging.FileHandler(str(_hf_log_path), mode="a")
    _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    hf_logger.addHandler(_fh)

STATE_DIR = Path.home() / ".nexus" / "browser_state"
HF_PROFILE = STATE_DIR / "hf_chrome_profile"
HF_STORAGE = Path.home() / ".nexus" / "higgsfield"
PROGRESS_FILE = HF_STORAGE / "progress.json"
HF_URL = "https://higgsfield.ai"
HF_IMAGE_URL = "https://higgsfield.ai/image/nano_banana_2"

_hf = None


def _kill_stale_chrome_hf():
    """Kill any Chrome processes using the HF profile and clean lock files."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "hf_chrome_profile"],
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
            print(f"[HF] Killed stale Chrome processes: {[p.strip() for p in pids if p.strip()]}")
    except Exception:
        pass
    for lock in ["SingletonLock", "SingletonSocket", "SingletonCookie"]:
        (HF_PROFILE / lock).unlink(missing_ok=True)


def _hash_scenes(scenes: list) -> str:
    """Hash scenes list for progress tracking."""
    return hashlib.md5(json.dumps(scenes).encode()).hexdigest()[:12]


class HiggsFieldBot:
    def __init__(self):
        self.context = None
        self.page = None
        self._pw = None
        self._lock = asyncio.Lock()
        self.logged_in = False
        self._running = False
        self._stop_requested = False
        self._broadcast_fn = None
        self._ref_uploaded = False  # tracks if ref image is in prompt area
        self._scenes_hash = ""
        self._status = {
            "state": "idle",
            "current_scene": 0,
            "total_scenes": 0,
            "current_frame": "",
            "images_done": 0,
            "images_total": 30,
            "queue_active": 0,
            "queue_max": 4,
            "error": "",
            "generated_images": [],
        }

    # ── Progress persistence ─────────────────────────────────────────────────

    def _load_progress(self, scenes_hash: str) -> dict:
        """Load saved progress from disk. Returns completed dict keyed by '1_start', '1_end', etc."""
        try:
            if PROGRESS_FILE.exists():
                data = json.loads(PROGRESS_FILE.read_text())
                if data.get("script_hash") == scenes_hash:
                    completed = data.get("completed", {})
                    total = sum(len(v) for v in completed.values())
                    if total > 0:
                        print(f"[HF] Resuming previous session — {total} images already done")
                    return completed
                else:
                    print("[HF] Different script detected — starting fresh")
        except Exception as e:
            print(f"[HF] Load progress error: {e}")
        return {}

    def _save_progress(self, scenes: list, ref_image: str, completed: dict):
        """Save progress to disk after each image."""
        try:
            HF_STORAGE.mkdir(parents=True, exist_ok=True)
            data = {
                "script_hash": self._scenes_hash,
                "scenes": scenes,
                "ref_image": ref_image,
                "completed": completed,
                "updated": datetime.now().isoformat(),
            }
            PROGRESS_FILE.write_text(json.dumps(data, indent=2))
        except Exception as e:
            print(f"[HF] Save progress error: {e}")

    def delete_image(self, scene: int, frame: str, index: int) -> bool:
        """Delete an image from progress (user rejected it)."""
        key = f"{scene}_{frame}"
        try:
            if PROGRESS_FILE.exists():
                data = json.loads(PROGRESS_FILE.read_text())
                completed = data.get("completed", {})
                if key in completed:
                    before = len(completed[key])
                    completed[key] = [img for img in completed[key] if img.get("index") != index]
                    if len(completed[key]) < before:
                        data["completed"] = completed
                        PROGRESS_FILE.write_text(json.dumps(data, indent=2))
                        self._status["generated_images"] = [
                            img for img in self._status["generated_images"]
                            if not (img["scene"] == scene and img["frame"] == frame and img["index"] == index)
                        ]
                        print(f"[HF] Deleted image: scene {scene} {frame} #{index}")
                        return True
        except Exception as e:
            print(f"[HF] Delete error: {e}")
        return False

    def get_progress_summary(self) -> dict:
        """Get progress summary for the frontend."""
        try:
            if PROGRESS_FILE.exists():
                data = json.loads(PROGRESS_FILE.read_text())
                completed = data.get("completed", {})
                return {
                    "script_hash": data.get("script_hash", ""),
                    "counts": {k: len(v) for k, v in completed.items()},
                    "total_images": sum(len(v) for v in completed.values()),
                    "ref_image": data.get("ref_image", ""),
                    "scenes": data.get("scenes", []),
                }
        except Exception:
            pass
        return {"counts": {}, "total_images": 0}

    # ── Browser management ───────────────────────────────────────────────────

    async def init_context(self):
        """Launch persistent Chrome profile in headed mode (off-screen)."""
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        HF_PROFILE.mkdir(parents=True, exist_ok=True)
        HF_STORAGE.mkdir(parents=True, exist_ok=True)

        _kill_stale_chrome_hf()

        for attempt in range(2):
            try:
                from playwright.async_api import async_playwright
                self._pw = await async_playwright().start()
                self.context = await self._pw.chromium.launch_persistent_context(
                    str(HF_PROFILE),
                    headless=False,
                    channel="chrome",
                    viewport={"width": 1440, "height": 900},
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--window-position=2000,2000",
                    ],
                    ignore_default_args=["--enable-automation"],
                )
                self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
                print("[HF] Persistent context loaded (headed)")
                return True
            except Exception as e:
                print(f"[HF] init_context attempt {attempt+1} failed: {e}")
                if attempt == 0:
                    _kill_stale_chrome_hf()
                    await asyncio.sleep(3)
                    if self._pw:
                        try: await self._pw.stop()
                        except: pass
                        self._pw = None
        print("[HF] Failed to init context after retries")
        return False

    async def _reconnect(self) -> bool:
        """Close and reinitialize the browser context."""
        print("[HF] Reconnecting browser...")
        await self.close()
        ok = await self.init_context()
        if ok:
            await self.ensure_login()
        return self.logged_in

    async def ensure_login(self) -> bool:
        """Navigate to higgsfield.ai, check if logged in."""
        try:
            await self.page.goto(HF_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(5)
            result = await self.page.evaluate("""() => {
                const assets = document.querySelector('a[href*="assets"], [class*="assets"]');
                if (assets) return 'assets';
                const upgrade = document.querySelector('a[href*="upgrade"]');
                if (upgrade) return 'upgrade';
                const imgs = document.querySelectorAll('img');
                for (const img of imgs) {
                    const style = window.getComputedStyle(img);
                    const isCircle = style.borderRadius === '50%' || style.borderRadius === '9999px' ||
                                     img.className.includes('rounded-full') || img.className.includes('circle');
                    const rect = img.getBoundingClientRect();
                    if (isCircle && rect.width > 20 && rect.width < 60 && rect.top < 80 && rect.right > window.innerWidth - 200) {
                        return 'profile_img';
                    }
                }
                const signIn = document.querySelector('a[href*="login"], a[href*="signin"]');
                const hasNav = document.querySelector('nav, header');
                if (hasNav && !signIn) return 'no_signin';
                return null;
            }""")
            if result:
                self.logged_in = True
                print(f"[HF] Session is valid (detected via '{result}')")
                return True
            self.logged_in = False
            print("[HF] Not logged in")
            return False
        except Exception as e:
            print(f"[HF] Login check failed: {e}")
            return False

    async def manual_login(self) -> bool:
        """Launch headed browser on-screen for user to log into Higgsfield via Google."""
        pw = None
        context = None
        try:
            await self.close()
            _kill_stale_chrome_hf()
            await asyncio.sleep(3)
            _kill_stale_chrome_hf()

            from playwright.async_api import async_playwright
            HF_PROFILE.mkdir(parents=True, exist_ok=True)
            pw = await async_playwright().start()
            context = await pw.chromium.launch_persistent_context(
                str(HF_PROFILE),
                headless=False,
                channel="chrome",
                viewport={"width": 1440, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"],
            )
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(HF_URL, wait_until="domcontentloaded", timeout=30000)
            print("[HF] Waiting for manual login... (sign in via Google)")

            for _ in range(150):  # 5 min timeout
                await asyncio.sleep(2)
                try:
                    url = page.url
                    if "accounts.google.com" in url:
                        continue
                    logged_in = await page.evaluate("""() => {
                        const assets = document.querySelector('a[href*="assets"], [class*="assets"]');
                        if (assets) return 'assets';
                        const upgrade = document.querySelector('a[href*="upgrade"]');
                        if (upgrade) return 'upgrade';
                        const imgs = document.querySelectorAll('img');
                        for (const img of imgs) {
                            const s = window.getComputedStyle(img);
                            const circle = s.borderRadius === '50%' || s.borderRadius === '9999px' ||
                                           img.className.includes('rounded-full');
                            const r = img.getBoundingClientRect();
                            if (circle && r.width > 20 && r.width < 60 && r.top < 80) return 'profile_img';
                        }
                        const btns = document.querySelectorAll('button');
                        for (const b of btns) {
                            if (b.textContent.trim().toLowerCase().includes('upgrade')) return 'upgrade_btn';
                        }
                        const hasLogin = document.querySelector('a[href*="login"], a[href*="signin"]');
                        let hasSignInBtn = false;
                        for (const b of btns) {
                            const t = b.textContent.trim().toLowerCase();
                            if (t.includes('sign in') || t.includes('log in') || t.includes('sign up')) {
                                hasSignInBtn = true; break;
                            }
                        }
                        if (!hasLogin && !hasSignInBtn && document.querySelector('nav, header')) return 'no_signin';
                        return null;
                    }""")
                    if logged_in:
                        print(f"[HF] Login detected via '{logged_in}'!")
                        self.logged_in = True
                        await context.close()
                        await pw.stop()
                        return True
                except Exception as check_err:
                    print(f"[HF] Login check: {check_err}")
                    continue

            print("[HF] Login timeout (5 min)")
            await context.close()
            await pw.stop()
            return False

        except Exception as e:
            print(f"[HF] Manual login error: {e}")
            traceback.print_exc()
            try:
                if context: await context.close()
            except Exception: pass
            try:
                if pw: await pw.stop()
            except Exception: pass
            return False

    # ── Generation ───────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Return current generation status."""
        return dict(self._status)

    async def start_generation(self, scenes: list, ref_image_path: str,
                                images_per_frame: int = 30,
                                broadcast_fn=None):
        """Main automation loop with progress tracking and resume support."""
        if self._running:
            return {"ok": False, "error": "Generation already running"}

        self._running = True
        self._stop_requested = False
        self._broadcast_fn = broadcast_fn
        self._scenes_hash = _hash_scenes(scenes)

        # Load existing progress for this script
        completed = self._load_progress(self._scenes_hash)

        # Rebuild status from saved progress
        self._status["generated_images"] = []
        for key, images in completed.items():
            for img in images:
                self._status["generated_images"].append(img)

        self._status["total_scenes"] = len(scenes)
        self._status["images_total"] = images_per_frame
        self._status["state"] = "generating"
        self._status["error"] = ""

        try:
            # Ensure browser is ready
            if not self.page:
                print("[HF] Opening browser for generation...")
                _kill_stale_chrome_hf()
                await asyncio.sleep(2)
                ok = await self.init_context()
                if not ok or not self.page:
                    raise RuntimeError("Failed to open browser for generation")

            # Navigate to image page
            await self.page.goto(HF_IMAGE_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)
            await self._setup_settings()

            for scene_idx, scene_desc in enumerate(scenes):
                if self._stop_requested:
                    break

                self._status["current_scene"] = scene_idx + 1

                for frame_type in ["start", "end"]:
                    if self._stop_requested:
                        break

                    key = f"{scene_idx + 1}_{frame_type}"
                    existing = completed.get(key, [])
                    existing_count = len(existing)
                    needed = images_per_frame - existing_count

                    if needed <= 0:
                        print(f"[HF] Scene {scene_idx+1} {frame_type}: {existing_count}/{images_per_frame} done — skipping")
                        continue

                    if existing_count > 0:
                        print(f"[HF] Scene {scene_idx+1} {frame_type}: resuming — {existing_count} done, {needed} remaining")

                    self._status["current_frame"] = frame_type
                    self._status["images_done"] = existing_count
                    await self._broadcast_status(
                        f"Scene {scene_idx+1}/{len(scenes)} — {frame_type} frames ({needed} remaining)"
                    )

                    # ── Feedback-driven generation loop ──
                    # Phase 1: Generate first pilot batch (5 images) with base prompts
                    # Phase 2: Analyze pilot batch with vision AI
                    # Phase 3: Refine prompts using feedback, generate remaining images
                    pilot_size = min(5, needed)
                    feedback = self._status.get("_feedback", "")  # Carry feedback from prior batches

                    pilot_prompts = await self._generate_prompts(scene_desc, frame_type, pilot_size, feedback=feedback)

                    # Upload reference image ONCE for this batch
                    self._ref_uploaded = False
                    await self._ensure_ref_image(ref_image_path)

                    prompt_global_idx = 0
                    batch_records = []

                    # Submit pilot batch
                    for prompt in pilot_prompts:
                        if self._stop_requested:
                            break
                        await self._wait_for_queue_slot()
                        if self._stop_requested:
                            break

                        self._status["state"] = "generating"
                        ok = await self._submit_prompt(prompt, ref_image_path)
                        if ok:
                            img_index = existing_count + prompt_global_idx + 1
                            img_record = {
                                "scene": scene_idx + 1,
                                "frame": frame_type,
                                "index": img_index,
                                "prompt": prompt[:100],
                                "full_prompt": prompt,
                                "timestamp": datetime.now().isoformat(),
                            }
                            self._status["images_done"] = img_index
                            self._status["generated_images"].append(img_record)
                            if key not in completed:
                                completed[key] = []
                            completed[key].append(img_record)
                            batch_records.append(img_record)
                            self._save_progress(scenes, ref_image_path, completed)
                        prompt_global_idx += 1
                        await asyncio.sleep(2)

                    # Wait for pilot images to finish generating, then scrape URLs + cache
                    if batch_records and not self._stop_requested:
                        await self._broadcast_status("Waiting for pilot batch to complete for analysis...")
                        for _ in range(12):  # Wait up to 60s for images to complete
                            await asyncio.sleep(5)
                            await self._update_image_urls(completed)
                            await self._cache_all_images(completed)
                            # Check if pilot images have local paths
                            cached_count = sum(1 for r in batch_records if r.get("local_path"))
                            if cached_count >= len(batch_records) * 0.6:
                                break

                        # ── Phase 2: Analyze pilot batch ──
                        analyzable = [r for r in batch_records if r.get("local_path")]
                        if analyzable:
                            self._status["state"] = "analyzing"
                            await self._broadcast_status(f"Analyzing {len(analyzable)} images for realism...")
                            analysis_result = await self.analyze_batch(analyzable, scene_desc)

                            # Store scores on image records
                            for a in analysis_result.get("analyses", []):
                                for rec in completed.get(key, []):
                                    if rec.get("index") == a.get("index"):
                                        rec["quality_score"] = a.get("score", 0)
                                        rec["verdict"] = a.get("verdict", "keep")
                                        rec["issues"] = a.get("realism_issues", [])[:3]
                                        break
                                # Also update in-memory status
                                for rec in self._status.get("generated_images", []):
                                    if rec.get("scene") == a.get("scene") and rec.get("frame") == a.get("frame") and rec.get("index") == a.get("index"):
                                        rec["quality_score"] = a.get("score", 0)
                                        rec["verdict"] = a.get("verdict", "keep")
                                        break

                            feedback = analysis_result.get("feedback_summary", "")
                            avg_score = analysis_result.get("avg_score", 0)
                            self._status["_feedback"] = feedback
                            self._status["avg_quality"] = round(avg_score, 1)
                            self._save_progress(scenes, ref_image_path, completed)

                            await self._broadcast_status(
                                f"Analysis complete: avg quality {avg_score:.1f}/10. Refining prompts..."
                            )
                        else:
                            print("[HF] No images cached yet for analysis, continuing without feedback")

                    # ── Phase 3: Generate remaining images with refined prompts ──
                    remaining = needed - prompt_global_idx
                    if remaining > 0 and not self._stop_requested:
                        refined_prompts = await self._generate_prompts(
                            scene_desc, frame_type, remaining, feedback=feedback
                        )

                        for prompt in refined_prompts:
                            if self._stop_requested:
                                break
                            await self._wait_for_queue_slot()
                            if self._stop_requested:
                                break

                            self._status["state"] = "generating"
                            ok = await self._submit_prompt(prompt, ref_image_path)
                            if ok:
                                img_index = existing_count + prompt_global_idx + 1
                                img_record = {
                                    "scene": scene_idx + 1,
                                    "frame": frame_type,
                                    "index": img_index,
                                    "prompt": prompt[:100],
                                    "full_prompt": prompt,
                                    "timestamp": datetime.now().isoformat(),
                                }
                                self._status["images_done"] = img_index
                                self._status["generated_images"].append(img_record)
                                if key not in completed:
                                    completed[key] = []
                                completed[key].append(img_record)
                                self._save_progress(scenes, ref_image_path, completed)
                            prompt_global_idx += 1

                            # Scrape URLs every 5 images
                            if prompt_global_idx % 5 == 0:
                                await self._update_image_urls(completed)

                            await asyncio.sleep(2)

                    # End of batch — final URL scrape + cache
                    await self._update_image_urls(completed)
                    await self._cache_all_images(completed)
                    self._save_progress(scenes, ref_image_path, completed)

            self._status["state"] = "done" if not self._stop_requested else "stopped"
            await self._broadcast_status(
                "Generation complete!" if not self._stop_requested else "Generation stopped"
            )

        except Exception as e:
            self._status["state"] = "error"
            self._status["error"] = str(e)
            print(f"[HF] Generation error: {e}")
            traceback.print_exc()
            await self._debug_screenshot("hf_generation_error")
        finally:
            self._running = False

    async def _setup_settings(self):
        """Verify and auto-fix ALL settings: model, aspect ratio, quality, unlimited.
        Order: model → unlimited → aspect → quality (unlimited before quality because
        toggling unlimited can reset quality). Retries once if verification fails."""
        for attempt in range(2):
            try:
                await asyncio.sleep(2)
                await self._dismiss_dropdown()

                # 1. MODEL — must be "Nano Banana Pro"
                model_ok = await self._ensure_setting_model()
                if not model_ok:
                    print("[HF] WARNING: Could not set model, continuing anyway...")
                await asyncio.sleep(2)

                # 2. UNLIMITED — toggle ON (do this BEFORE quality, toggling may reset quality)
                await self._ensure_setting_unlimited()
                await asyncio.sleep(2)

                # 3. ASPECT RATIO — must be "9:16"
                await self._ensure_setting_aspect()
                await asyncio.sleep(2)

                # 4. QUALITY — must be "2K" (do this LAST since other toggles can reset it)
                await self._ensure_setting_quality()
                await asyncio.sleep(2)

                # Final verification
                all_ok = await self._verify_all_settings()
                if all_ok:
                    print("[HF] All settings verified: Nano Banana Pro, 9:16, 2K, Unlimited ON")
                    return

                if attempt == 0:
                    print(f"[HF] Settings verification failed on attempt 1 — retrying...")
                    await self._debug_screenshot("hf_settings_retry")
                    await asyncio.sleep(2)
                    continue

                # Second attempt failed — log but don't crash, let generation try anyway
                print("[HF] WARNING: Settings verification failed after retry — proceeding anyway")
                await self._debug_screenshot("hf_settings_bad")

            except Exception as e:
                print(f"[HF] Settings setup error: {e}")
                if attempt == 0:
                    await asyncio.sleep(2)
                    continue
                print("[HF] Settings setup failed after retry — proceeding anyway")

    async def _ensure_setting_model(self) -> bool:
        """Ensure Nano Banana Pro is selected. Click model pill and select if needed."""
        try:
            # Check current model from the bottom bar text
            current = await self.page.evaluate("""() => {
                const els = document.querySelectorAll('button, span, div');
                for (const el of els) {
                    const t = el.textContent.trim();
                    if (t.includes('Nano Banana Pro') && el.getBoundingClientRect().bottom > window.innerHeight - 100) {
                        return 'nano_banana_pro';
                    }
                }
                return 'unknown';
            }""")
            if current == 'nano_banana_pro':
                print("[HF] Model: Nano Banana Pro ✓")
                return True

            # Need to change — click the model selector pill
            print("[HF] Model not set — selecting Nano Banana Pro...")
            # Click the model pill button in the bottom bar
            await self.page.evaluate("""() => {
                const els = document.querySelectorAll('button, [role="button"]');
                for (const el of els) {
                    const t = el.textContent.trim().toLowerCase();
                    const r = el.getBoundingClientRect();
                    if (r.bottom > window.innerHeight - 100 && (t.includes('nano banana') || t.includes('model') || t.includes('auto'))) {
                        el.click();
                        return true;
                    }
                }
                // Try clicking any model-related element in bottom bar
                const bottomEls = document.querySelectorAll('[class*="model"], [class*="selector"]');
                for (const el of bottomEls) {
                    const r = el.getBoundingClientRect();
                    if (r.bottom > window.innerHeight - 100) { el.click(); return true; }
                }
                return false;
            }""")
            await asyncio.sleep(2)

            # Look for "Nano Banana Pro" in the dropdown and click it
            clicked = await self.page.evaluate("""() => {
                const items = document.querySelectorAll('div, span, button, li, a');
                for (const item of items) {
                    const t = item.textContent.trim();
                    if (t === 'Nano Banana Pro' || (t.includes('Nano Banana Pro') && t.length < 50)) {
                        const r = item.getBoundingClientRect();
                        if (r.width > 100 && r.height > 20 && r.height < 80) {
                            item.click();
                            return true;
                        }
                    }
                }
                return false;
            }""")
            if clicked:
                await asyncio.sleep(0.5)
                await self._dismiss_dropdown()
                print("[HF] Model: Selected Nano Banana Pro")
                await asyncio.sleep(1)
                return True
            else:
                await self._dismiss_dropdown()
                print("[HF] WARNING: Could not find Nano Banana Pro in dropdown")
                await self._debug_screenshot("hf_model_select")
                return False

        except Exception as e:
            print(f"[HF] Model setting error: {e}")
            return False

    async def _ensure_setting_aspect(self):
        """Ensure aspect ratio is 9:16."""
        try:
            current = await self.page.evaluate("""() => {
                const els = document.querySelectorAll('button, span, div');
                for (const el of els) {
                    const t = el.textContent.trim();
                    const r = el.getBoundingClientRect();
                    if (r.bottom > window.innerHeight - 100 && (t === '9:16' || t === '16:9' || t === '1:1' || t === '4:3' || t === '3:4')) {
                        return t;
                    }
                }
                return 'unknown';
            }""")
            if current == '9:16':
                print("[HF] Aspect ratio: 9:16 ✓")
                return

            print(f"[HF] Aspect ratio is {current} — changing to 9:16...")
            # Click the aspect ratio pill
            await self.page.evaluate("""() => {
                const els = document.querySelectorAll('button, [role="button"]');
                for (const el of els) {
                    const t = el.textContent.trim();
                    const r = el.getBoundingClientRect();
                    if (r.bottom > window.innerHeight - 100 && /^\\d+:\\d+$/.test(t)) {
                        el.click(); return true;
                    }
                }
                return false;
            }""")
            await asyncio.sleep(1)

            # Select 9:16 from options
            await self.page.evaluate("""() => {
                const items = document.querySelectorAll('div, span, button, li, a');
                for (const item of items) {
                    if (item.textContent.trim() === '9:16') {
                        item.click(); return true;
                    }
                }
                return false;
            }""")
            await asyncio.sleep(0.5)
            await self._dismiss_dropdown()
            print("[HF] Aspect ratio: Set to 9:16")

        except Exception as e:
            print(f"[HF] Aspect ratio setting error: {e}")

    async def _dismiss_dropdown(self):
        """Dismiss any open dropdown/popup by pressing Escape and clicking a neutral area."""
        try:
            await self.page.keyboard.press("Escape")
            await asyncio.sleep(0.3)
            # Click a neutral area (top-left of prompt area) to dismiss
            await self.page.evaluate("""() => {
                const textarea = document.querySelector('textarea, [contenteditable="true"]');
                if (textarea) { textarea.blur(); }
                document.body.click();
            }""")
            await asyncio.sleep(0.3)
        except Exception:
            pass

    async def _ensure_setting_quality(self):
        """Ensure quality is 2K."""
        try:
            current = await self.page.evaluate("""() => {
                // Scan visible leaf/near-leaf elements in the bottom settings bar
                const els = document.querySelectorAll('button, span, div, p');
                for (const el of els) {
                    const r = el.getBoundingClientRect();
                    if (r.bottom < window.innerHeight - 120 || r.width <= 0 || r.height <= 0) continue;
                    // Visibility check
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity) < 0.1) continue;
                    // Only match small elements (pills), not large containers
                    if (r.width > 80 || r.height > 50) continue;
                    const t = el.innerText.trim();
                    if (t === '1K' || t === '2K' || t === '4K') return t;
                }
                // Broader search if no small pill found
                for (const el of els) {
                    const r = el.getBoundingClientRect();
                    if (r.bottom < window.innerHeight - 120 || r.width <= 0 || r.height <= 0) continue;
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity) < 0.1) continue;
                    const t = el.innerText.trim();
                    if ((t === '1K' || t === '2K' || t === '4K') && el.childElementCount <= 1) return t;
                    if ((t.startsWith('1K') || t.startsWith('2K') || t.startsWith('4K')) && t.length < 20 && el.childElementCount <= 1) return t.substring(0, 2);
                }
                return 'unknown';
            }""")
            if current == '2K':
                print("[HF] Quality: 2K ✓")
                return

            print(f"[HF] Quality is '{current}' — changing to 2K...")
            # Click the quality pill in the bottom bar
            await self.page.evaluate("""() => {
                const els = document.querySelectorAll('button, [role="button"], span, div');
                for (const el of els) {
                    const r = el.getBoundingClientRect();
                    if (r.bottom < window.innerHeight - 120 || r.width <= 0 || r.height <= 0) continue;
                    if (r.width > 80 || r.height > 50) continue;
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    const t = el.innerText.trim();
                    if (t === '1K' || t === '2K' || t === '4K' || t === 'HD') {
                        el.click(); return true;
                    }
                }
                // Broader: any clickable element with quality text
                for (const el of els) {
                    const r = el.getBoundingClientRect();
                    if (r.bottom < window.innerHeight - 120 || r.width <= 0 || r.height <= 0) continue;
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    const t = el.innerText.trim();
                    if ((t.includes('1K') || t.includes('2K') || t.includes('4K')) && t.length < 15 && el.childElementCount <= 1) {
                        el.click(); return true;
                    }
                }
                return false;
            }""")
            await asyncio.sleep(1.5)

            # Select 2K from dropdown — text may be "2K", "2K Unlimited", etc.
            clicked = await self.page.evaluate("""() => {
                const items = document.querySelectorAll('div, span, button, li, a');
                // First try exact match
                for (const item of items) {
                    if (item.textContent.trim() === '2K') {
                        item.click(); return true;
                    }
                }
                // Then try startsWith match (handles "2K Unlimited" etc.)
                for (const item of items) {
                    const t = item.textContent.trim();
                    if (t.startsWith('2K') && t.length < 30) {
                        const r = item.getBoundingClientRect();
                        if (r.width > 40 && r.height > 15 && r.height < 80) {
                            item.click(); return true;
                        }
                    }
                }
                return false;
            }""")
            if clicked:
                print("[HF] Quality: Set to 2K")
            else:
                print("[HF] WARNING: Could not find 2K in dropdown")
                await self._debug_screenshot("hf_quality_select")

            await asyncio.sleep(0.5)
            await self._dismiss_dropdown()

        except Exception as e:
            print(f"[HF] Quality setting error: {e}")

    async def _ensure_setting_unlimited(self):
        """Ensure Unlimited toggle is ON. This is critical for the 'unlimited mode' feature."""
        try:
            status = await self.page.evaluate("""() => {
                // Check generate button text — most reliable indicator
                const buttons = document.querySelectorAll('button');
                for (const btn of buttons) {
                    const text = btn.textContent.trim().toLowerCase();
                    if (text.includes('unlimited')) return 'on';
                    if (text.includes('generate') && /\\d/.test(text)) return 'off';
                }
                // Also check for any toggle/switch with "unlimited" nearby
                const allText = document.body.innerText.toLowerCase();
                if (allText.includes('unlimited mode') || allText.match(/unlimited\\s*(on|enabled)/)) return 'on';
                return 'unknown';
            }""")
            if status == 'on':
                print("[HF] Unlimited: ON ✓")
                return

            print(f"[HF] Unlimited is {status} — turning ON...")
            # Try multiple strategies to enable unlimited
            clicked = await self.page.evaluate("""() => {
                // Strategy 1: Click button/toggle in the bottom settings bar
                const els = document.querySelectorAll('button, [role="switch"], input[type="checkbox"], label, div[role="button"]');
                for (const el of els) {
                    const t = (el.textContent || '').trim().toLowerCase();
                    const r = el.getBoundingClientRect();
                    if (r.bottom > window.innerHeight - 120 && r.width > 0 && r.height > 0) {
                        if (t.includes('unlimited') || t.includes('∞')) {
                            el.click(); return 'text_match';
                        }
                    }
                }
                // Strategy 2: Click toggle/switch elements near bottom bar
                const toggles = document.querySelectorAll('[class*="toggle"], [class*="switch"], [class*="Toggle"], [class*="Switch"]');
                for (const t of toggles) {
                    const r = t.getBoundingClientRect();
                    if (r.bottom > window.innerHeight - 120 && r.width > 0 && r.height > 0) {
                        t.click(); return 'toggle';
                    }
                }
                // Strategy 3: Look for a checkbox or toggle input
                const inputs = document.querySelectorAll('input[type="checkbox"]');
                for (const inp of inputs) {
                    const r = inp.getBoundingClientRect();
                    if (r.bottom > window.innerHeight - 120) {
                        inp.click(); return 'checkbox';
                    }
                    // Also check parent for visibility
                    const label = inp.closest('label');
                    if (label) {
                        const lr = label.getBoundingClientRect();
                        if (lr.bottom > window.innerHeight - 120 && lr.width > 0) {
                            inp.click(); return 'label_checkbox';
                        }
                    }
                }
                return false;
            }""")
            await asyncio.sleep(2)

            # Verify it worked
            verify = await self.page.evaluate("""() => {
                const buttons = document.querySelectorAll('button');
                for (const btn of buttons) {
                    if (btn.textContent.trim().toLowerCase().includes('unlimited')) return true;
                }
                return false;
            }""")
            if verify:
                print(f"[HF] Unlimited: Toggled ON (via {clicked}) ✓")
            else:
                print(f"[HF] WARNING: Unlimited toggle attempted ({clicked}) but could not verify")
                await self._debug_screenshot("hf_unlimited_unverified")

        except Exception as e:
            print(f"[HF] Unlimited setting error: {e}")

    async def _verify_all_settings(self) -> bool:
        """Final check that all 4 settings are correct before generating."""
        try:
            result = await self.page.evaluate("""() => {
                const checks = {model: false, aspect: false, quality: false, unlimited: false};
                const els = document.querySelectorAll('button, span, div, p');
                for (const el of els) {
                    const r = el.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) continue;
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity) < 0.1) continue;
                    const t = el.innerText.trim();
                    if (r.bottom > window.innerHeight - 120) {
                        if (t.includes('Nano Banana Pro')) checks.model = true;
                        if (t === '9:16') checks.aspect = true;
                        // Quality: only match small visible pills
                        if ((t === '2K' || t.startsWith('2K')) && r.width < 80 && el.childElementCount <= 1) {
                            checks.quality = true;
                        }
                    }
                }
                // Check generate button for unlimited (can be anywhere)
                const buttons = document.querySelectorAll('button');
                for (const btn of buttons) {
                    const style = window.getComputedStyle(btn);
                    if (style.display === 'none' || style.visibility === 'hidden') continue;
                    if (btn.innerText.trim().toLowerCase().includes('unlimited')) {
                        checks.unlimited = true;
                    }
                }
                return checks;
            }""")
            all_ok = all(result.values())
            for setting, ok in result.items():
                if not ok:
                    print(f"[HF] FAILED: {setting} not set correctly")
            return all_ok
        except Exception as e:
            print(f"[HF] Verify settings error: {e}")
            return False

    async def _get_queue_count(self) -> int:
        """Count generating/queued images on the page."""
        try:
            count = await self.page.evaluate("""() => {
                let count = 0;
                const items = document.querySelectorAll(
                    '[class*="generating"], [class*="queued"], ' +
                    '[class*="progress"], [class*="loading"], [class*="pending"]'
                );
                count = items.length;
                if (count === 0) {
                    const allText = document.body.innerText;
                    const genMatch = allText.match(/generating/gi);
                    const queueMatch = allText.match(/queued/gi);
                    count = (genMatch ? genMatch.length : 0) + (queueMatch ? queueMatch.length : 0);
                }
                if (count === 0) {
                    const spinners = document.querySelectorAll(
                        '[class*="spinner"], [class*="animate-spin"], [class*="animate-pulse"]'
                    );
                    for (const s of spinners) {
                        const rect = s.getBoundingClientRect();
                        if (rect.top > 100 && rect.top < 800) count++;
                    }
                }
                return count;
            }""")
            return int(count)
        except Exception as e:
            print(f"[HF] Queue count error: {e}")
            return 0

    async def _wait_for_queue_slot(self):
        """Poll until queue < 4. Also scrapes image URLs while waiting."""
        while True:
            if self._stop_requested:
                return
            count = await self._get_queue_count()
            self._status["queue_active"] = count
            if count < 4:
                return
            self._status["state"] = "waiting_queue"
            await self._broadcast_status(f"Queue full ({count}/4) — waiting...")
            # While waiting, try to scrape completed image URLs
            try:
                if PROGRESS_FILE.exists():
                    data = json.loads(PROGRESS_FILE.read_text())
                    await self._update_image_urls(data.get("completed", {}))
            except Exception:
                pass
            await asyncio.sleep(5)

    async def _ensure_ref_image(self, ref_image_path: str):
        """Upload reference image if not already in prompt area."""
        if self._ref_uploaded:
            return
        # Clear any stale uploaded images first
        await self._clear_uploaded_images()
        await asyncio.sleep(0.5)
        await self._upload_reference_image(ref_image_path)
        self._ref_uploaded = True

    async def _clear_uploaded_images(self):
        """Remove existing uploaded images from the prompt area."""
        try:
            removed = await self.page.evaluate("""() => {
                let removed = 0;
                // Look for image preview containers in the prompt area (bottom of page)
                // and find their close/remove buttons
                const allBtns = document.querySelectorAll('button, [role="button"]');
                for (const btn of allBtns) {
                    const r = btn.getBoundingClientRect();
                    // Close buttons are small, near bottom of viewport (prompt area)
                    if (r.width < 28 && r.height < 28 && r.bottom > window.innerHeight - 300) {
                        const svg = btn.querySelector('svg');
                        const text = btn.textContent.trim();
                        if (svg || text === '×' || text === 'x' || text === '') {
                            // Check if this button is near an image thumbnail
                            const nearby = btn.closest('[class*="image"], [class*="preview"], [class*="thumb"], [class*="upload"]');
                            if (nearby || r.top > window.innerHeight - 300) {
                                btn.click();
                                removed++;
                            }
                        }
                    }
                }
                return removed;
            }""")
            if removed > 0:
                print(f"[HF] Cleared {removed} stale reference images")
                await asyncio.sleep(0.5)
        except Exception as e:
            print(f"[HF] Clear images: {e}")

    async def _upload_reference_image(self, image_path: str):
        """Upload reference image via the + button."""
        try:
            add_btn = await self.page.query_selector(
                'button:has-text("+"), '
                '[class*="add-image"], [class*="upload"], '
                '[aria-label*="Add"], [aria-label*="Upload"], '
                '[aria-label*="add"], [aria-label*="upload"]'
            )
            if add_btn:
                await add_btn.click()
                await asyncio.sleep(1)

            file_input = await self.page.query_selector('input[type="file"]')
            if file_input:
                await file_input.set_input_files(image_path)
                await asyncio.sleep(2)
                print(f"[HF] Uploaded reference image: {image_path}")
            else:
                print("[HF] No file input found for image upload")
                await self._debug_screenshot("hf_no_file_input")
        except Exception as e:
            print(f"[HF] Image upload error: {e}")
            await self._debug_screenshot("hf_upload_error")

    async def _submit_prompt(self, prompt: str, ref_image_path: str) -> bool:
        """Fill prompt and click generate. Re-uploads ref image if cleared after generation."""
        async with self._lock:
            try:
                # Dismiss any open dropdowns/popups first
                await self._dismiss_dropdown()

                # Find prompt input
                prompt_input = await self.page.query_selector(
                    'textarea[placeholder*="Describe"], '
                    'textarea[placeholder*="describe"], '
                    'input[placeholder*="Describe"], '
                    'input[placeholder*="describe"], '
                    'textarea, [contenteditable="true"]'
                )
                if not prompt_input:
                    print("[HF] Could not find prompt input")
                    await self._debug_screenshot("hf_no_prompt_input")
                    return False

                await prompt_input.click(timeout=10000)
                await asyncio.sleep(0.3)
                await prompt_input.fill("")
                await asyncio.sleep(0.2)
                await prompt_input.fill(prompt)
                await asyncio.sleep(0.5)

                # Quick settings check before clicking generate
                settings_ok = await self._verify_all_settings()
                if not settings_ok:
                    print("[HF] Settings may have changed — attempting fix...")
                    await self._setup_settings()
                    # Don't block — proceed with generation even if verify is flaky

                # Click generate
                clicked = await self.page.evaluate("""() => {
                    const buttons = document.querySelectorAll('button');
                    for (const btn of buttons) {
                        if (btn.textContent.trim().toLowerCase().includes('unlimited')) {
                            btn.click();
                            return true;
                        }
                    }
                    return false;
                }""")
                if not clicked:
                    try:
                        await self.page.click('button:has-text("Unlimited")', timeout=3000)
                        clicked = True
                    except Exception:
                        pass

                if not clicked:
                    print("[HF] Could not click generate button")
                    await self._debug_screenshot("hf_no_generate_btn")
                    return False

                await asyncio.sleep(2)

                # Check if ref image was cleared after generation — re-upload if needed
                has_ref = await self.page.evaluate("""() => {
                    const imgs = document.querySelectorAll('img');
                    for (const img of imgs) {
                        const r = img.getBoundingClientRect();
                        if (r.bottom > window.innerHeight - 200 && r.width < 100 && r.width > 20) return true;
                    }
                    return false;
                }""")
                if not has_ref:
                    self._ref_uploaded = False
                    await self._ensure_ref_image(ref_image_path)

                print(f"[HF] Submitted: {prompt[:60]}...")
                return True

            except Exception as e:
                print(f"[HF] Submit error: {e}")
                await self._debug_screenshot("hf_submit_error")
                return False

    # ── Prompt generation ────────────────────────────────────────────────────

    async def _generate_prompts(self, scene_desc: str, frame_type: str,
                                 count: int = 30, feedback: str = "") -> list:
        """Generate image prompts using LLM. Prompts work WITH the uploaded reference photo —
        they describe the SCENE and SETTING, not the trailer itself (the reference handles that).
        If feedback is provided, it's used to refine prompts based on prior image analysis."""
        try:
            from core.providers import LLMProvider
            from server import load_config
            config = load_config()
            provider = LLMProvider(config)
            model = provider.resolve("creative")

            feedback_block = ""
            if feedback:
                feedback_block = f"""

CRITICAL — LEARNED FROM PREVIOUS IMAGE ANALYSIS:
The following rules were derived from analyzing previously generated images for realism.
You MUST follow these to avoid generating unrealistic/fake-looking images:

{feedback}

These rules override any conflicting guidance below. Realism is the #1 priority."""

            system = f"""You write image generation prompts for Higgsfield AI (Nano Banana Pro model).

CRITICAL CONTEXT: The user has uploaded a REFERENCE PHOTO of their actual restroom trailer. The AI model will use this reference to know what the trailer looks like. Your prompts should describe the SCENE, ENVIRONMENT, and MOOD around the trailer — NOT describe the trailer itself in detail.

Think of it like this: the reference photo says "this is what the trailer looks like" and your prompt says "put it in THIS setting with THIS lighting."

THE #1 GOAL: These images are for FACEBOOK ADS. They must look like REAL PHOTOGRAPHS that a professional event photographer took. Not AI art. Not renders. Not illustrations. REAL PHOTOS.
{feedback_block}

PROMPT FORMAT (30-60 words each):
- Start with the scene/environment description
- Include time of day and lighting mood
- Describe the atmosphere and surrounding elements
- End with photography style cue
- The model already knows the trailer from the reference — focus on WHERE and HOW it's shown

REALISM TIPS:
- Say "photograph of" or "professional photo of" to anchor photorealism
- Specify "shot on Canon EOS R5" or "shot on iPhone 15 Pro" for camera realism
- Include "natural lighting" or "available light" — avoid fantasy/dramatic lighting
- Keep human figures small/distant/blurred to avoid uncanny valley faces
- Avoid text, signs, logos in the scene — AI mangles them
- Keep architecture simple and plausible — avoid impossible structures
- "Documentary style" and "candid photography" produce more realistic results than "dramatic" or "cinematic"

GOOD EXAMPLE: "Professional photograph of an upscale outdoor wedding reception at golden hour, the trailer parked on a manicured lawn flanked by string lights, warm natural sunlight, blurred guests in background, shot on Canon EOS R5, candid event photography"

BAD EXAMPLE: "A luxury portable restroom trailer with brushed stainless steel panels and matte black fixtures..." (DON'T describe the trailer — the reference photo handles that)

RULES:
- 30-60 words per prompt
- ALWAYS start with "Professional photograph of" or "Photo of" or "Real photo of"
- Focus on SETTING, LIGHTING, MOOD, ATMOSPHERE
- Include specific environmental details (string lights, flowers, tables)
- Keep people blurred, distant, or from behind — NEVER describe faces
- Vary time of day, weather, venue type, camera perspective
- End with a style anchor and camera reference
- NO negative prompts, NO fantasy elements
- 9:16 vertical portrait format"""

            frame_guidance = (
                "hero/establishing shot — dramatic, eye-catching, shows the full scene"
                if frame_type == "start" else
                "closing shot — intimate, detail-focused, emotional, memorable"
            )

            user_msg = f"""Generate exactly {count} unique prompts for Higgsfield Nano Banana Pro.

IMPORTANT: A reference photo of the trailer is already uploaded. Your prompts describe the SCENE around it, not the trailer.

Scene context: {scene_desc}
Frame type: {frame_type} ({frame_guidance})
Purpose: Facebook video ad for luxury portable restroom trailer rental (Zoar Bathroom Rentals)
Format: 9:16 vertical portrait

Vary these across all {count} prompts:
- Venues: garden wedding, vineyard, beach resort, rooftop party, corporate gala, country estate, polo match, art gallery opening, lakeside rehearsal dinner
- Time: golden hour, blue hour, twilight, bright midday, overcast, evening with string lights, night with warm lanterns
- Perspective: wide scene, medium distance, close approach, from guest's POV, aerial-style view
- Atmosphere: romantic, elegant, festive, serene, sophisticated, lively

REMEMBER: Each prompt MUST start with "Professional photograph of" or "Photo of" to anchor photorealism.
{"APPLY the learned feedback rules above to every prompt." if feedback else ""}

Return ONLY a JSON array of {count} prompt strings."""

            resp = await provider.chat(model, [{"role": "user", "content": user_msg}],
                                       system=system, max_tokens=5000)

            if resp.get("ok"):
                content = resp["content"]
                if "```" in content:
                    parts = content.split("```")
                    if len(parts) >= 2:
                        content = parts[1]
                        if content.startswith("json"):
                            content = content[4:]
                try:
                    prompts = json.loads(content.strip())
                    if isinstance(prompts, list) and len(prompts) >= 1:
                        print(f"[HF] Generated {len(prompts)} prompts for scene ({frame_type} frame)")
                        return prompts[:count]
                except json.JSONDecodeError:
                    print("[HF] Failed to parse prompts JSON, using fallback")

        except Exception as e:
            print(f"[HF] Prompt generation error: {e}")
            traceback.print_exc()

        # Fallback prompts — scene-focused (reference photo handles the trailer)
        print(f"[HF] Using fallback prompts for scene ({frame_type} frame)")
        scenes = [
            "Upscale garden wedding at golden hour, manicured lawn with string lights and white linen tables, warm amber sunlight, guests in formal attire, professional event photography",
            "Vineyard estate during blue hour, rows of grapevines, warm glow from lanterns along a stone path, deep blue twilight sky, editorial luxury lifestyle",
            "Beachside resort at sunset, palm trees silhouetted against pink and orange sky, sandy path with tiki torches, ocean waves in background, tropical luxury photography",
            "Rooftop cocktail party at night, city skyline backdrop, Edison bulb string lights, sleek modern terrace, well-dressed guests with cocktails, architectural photography",
            "Countryside estate on an overcast afternoon, rolling green hills, wildflower borders along a stone pathway, soft diffused natural light, English countryside editorial",
            "Corporate gala on a manicured lawn at twilight, elegant white tent in background, lantern-lined walkway, formal landscaping, event photography",
            "Lakeside rehearsal dinner at dusk, calm water reflecting warm lights, wooden dock, candles on tables, romantic and intimate atmosphere, fine art photography",
            "Polo match grounds on a sunny afternoon, white fences and green fields, luxury cars parked nearby, preppy elegant atmosphere, lifestyle photography",
            "Art gallery opening in a sculpture garden at night, modern art pieces, dramatic uplighting, champagne glasses, sophisticated urban atmosphere, editorial photography",
            "Mountain resort wedding, pine trees and mountain peaks in background, wooden arch with flowers, golden hour sunlight streaming through trees, destination wedding photography",
        ]
        return [
            f"{scenes[i % len(scenes)]}. {scene_desc}"
            for i in range(count)
        ]

    # ── Vision Feedback Loop ──────────────────────────────────────────────

    async def analyze_image(self, image_path: str, prompt_used: str, scene_desc: str) -> dict:
        """Use Claude vision to analyze a generated image for realism and quality.
        Returns: {score: 1-10, realism: str, issues: [str], strengths: [str],
                  prompt_feedback: str, verdict: 'keep'|'reject'}"""
        try:
            import base64
            from core.providers import LLMProvider
            from server import load_config

            path = Path(image_path)
            if not path.exists():
                return {"score": 0, "verdict": "skip", "error": "File not found"}

            img_data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
            ext = path.suffix.lower()
            media = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png" if ext == ".png" else "image/webp"

            config = load_config()
            provider = LLMProvider(config)

            # Use Claude for vision analysis (it natively supports image content blocks)
            messages = [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media, "data": img_data}},
                    {"type": "text", "text": f"""Analyze this AI-generated image for a FACEBOOK AD for a luxury portable restroom trailer rental company.

CONTEXT:
- This will be used as a frame in a Facebook video ad
- Target audience scrolling Facebook — must look REAL, not AI-generated
- The image should feature a luxury portable restroom/bathroom trailer in an upscale event setting
- Scene description: {scene_desc}
- Prompt used: {prompt_used}

RATE THIS IMAGE (respond in JSON only):
{{
  "score": <1-10, where 10 = indistinguishable from real photo, 1 = obviously fake>,
  "realism_issues": ["list specific things that look fake/AI-generated, e.g. 'warped text on sign', 'extra fingers on person', 'impossible architecture', 'plastic-looking skin', 'inconsistent shadows'"],
  "strengths": ["what looks good/realistic about this image"],
  "prompt_feedback": "specific suggestions to improve the prompt — what to ADD, REMOVE, or CHANGE to get more realistic results. Be very specific.",
  "verdict": "<keep if score >= 6, reject if score < 6>"
}}

Be BRUTALLY HONEST. Facebook ad viewers are skeptical — any AI tell will ruin the ad. Focus on:
- Human faces/hands (biggest AI giveaway)
- Text/signage (AI text is always mangled)
- Lighting consistency and shadows
- Architectural plausibility
- Overall "uncanny valley" feeling
- Does it look like a real event photo someone took with their phone/camera?"""}
                ]
            }]

            resp = await provider.chat("claude-haiku", messages, max_tokens=1000)

            if resp.get("ok"):
                content = resp["content"]
                # Parse JSON from response
                if "```" in content:
                    parts = content.split("```")
                    if len(parts) >= 2:
                        content = parts[1]
                        if content.startswith("json"):
                            content = content[4:]
                try:
                    result = json.loads(content.strip())
                    result["score"] = int(result.get("score", 5))
                    result["verdict"] = "keep" if result["score"] >= 6 else "reject"
                    return result
                except json.JSONDecodeError:
                    print(f"[HF] Failed to parse analysis JSON: {content[:200]}")
                    return {"score": 5, "verdict": "keep", "error": "Parse error"}
            else:
                print(f"[HF] Vision analysis failed: {resp.get('content', 'unknown')}")
                return {"score": 5, "verdict": "keep", "error": resp.get("content", "")}

        except Exception as e:
            print(f"[HF] Image analysis error: {e}")
            return {"score": 5, "verdict": "keep", "error": str(e)}

    async def analyze_batch(self, images: list, scene_desc: str) -> dict:
        """Analyze a batch of images and compile feedback.
        images: list of dicts with 'local_path', 'prompt', 'scene', 'frame', 'index'
        Returns: {analyses: [...], feedback_summary: str, avg_score: float}"""
        analyses = []
        for img in images:
            local = img.get("local_path", "")
            if not local or not Path(local).exists():
                continue
            print(f"[HF] Analyzing S{img['scene']} {img['frame']} #{img['index']}...")
            analysis = await self.analyze_image(local, img.get("prompt", ""), scene_desc)
            analysis["scene"] = img["scene"]
            analysis["frame"] = img["frame"]
            analysis["index"] = img["index"]
            analyses.append(analysis)
            await asyncio.sleep(0.5)  # Rate limit courtesy

        if not analyses:
            return {"analyses": [], "feedback_summary": "", "avg_score": 0}

        avg = sum(a.get("score", 5) for a in analyses) / len(analyses)
        kept = [a for a in analyses if a.get("verdict") == "keep"]
        rejected = [a for a in analyses if a.get("verdict") == "reject"]

        # Compile feedback into actionable prompt guidance
        feedback = await self._compile_feedback(analyses, scene_desc)

        print(f"[HF] Batch analysis: avg={avg:.1f}, kept={len(kept)}, rejected={len(rejected)}")
        return {"analyses": analyses, "feedback_summary": feedback, "avg_score": avg}

    async def _compile_feedback(self, analyses: list, scene_desc: str) -> str:
        """Use LLM to compile image analyses into actionable prompt improvements."""
        try:
            from core.providers import LLMProvider
            from server import load_config

            # Build summary of what worked and what didn't
            good = [a for a in analyses if a.get("score", 0) >= 7]
            bad = [a for a in analyses if a.get("score", 0) < 6]

            issues_list = []
            strengths_list = []
            prompt_tips = []
            for a in analyses:
                issues_list.extend(a.get("realism_issues", []))
                strengths_list.extend(a.get("strengths", []))
                if a.get("prompt_feedback"):
                    prompt_tips.append(a["prompt_feedback"])

            config = load_config()
            provider = LLMProvider(config)
            model = provider.resolve("creative")

            resp = await provider.chat(model, [{
                "role": "user",
                "content": f"""You are refining image generation prompts for Facebook ads. Based on analysis of {len(analyses)} generated images:

AVERAGE REALISM SCORE: {sum(a.get('score',5) for a in analyses)/len(analyses):.1f}/10

COMMON ISSUES FOUND:
{chr(10).join('- ' + i for i in issues_list[:20])}

THINGS THAT WORKED:
{chr(10).join('- ' + s for s in strengths_list[:15])}

PROMPT IMPROVEMENT SUGGESTIONS:
{chr(10).join('- ' + t for t in prompt_tips[:15])}

Scene context: {scene_desc}

Write a CONCISE set of PROMPT RULES (max 8 bullet points) that should be followed for ALL future prompts to maximize realism. Focus on:
1. What specific things to AVOID in prompts (based on issues)
2. What specific things to INCLUDE in prompts (based on strengths)
3. Key wording patterns that produce more realistic results

Return ONLY the bullet points, no preamble."""
            }], system="You are an expert at crafting AI image generation prompts that produce photorealistic results.", max_tokens=600)

            if resp.get("ok"):
                return resp["content"].strip()
        except Exception as e:
            print(f"[HF] Compile feedback error: {e}")

        # Fallback: simple concatenation
        return "\n".join(f"- {t}" for t in prompt_tips[:8])

    async def _scrape_generated_images(self) -> list:
        """Scrape generated image URLs from the Higgsfield page (history/feed area)."""
        try:
            images = await self.page.evaluate("""() => {
                const imgs = [];
                const imgElements = document.querySelectorAll('img');
                for (const img of imgElements) {
                    const src = img.src || img.getAttribute('src') || '';
                    if (!src || src.startsWith('data:')) continue;
                    // Skip nav/avatar/logo images
                    if (src.includes('avatar') || src.includes('logo') || src.includes('icon') || src.includes('favicon')) continue;
                    const r = img.getBoundingClientRect();
                    // Generated images are in the main content area and reasonably sized
                    if (r.width > 80 && r.height > 80 && r.top > 50) {
                        imgs.push(src);
                    }
                }
                return [...new Set(imgs)];
            }""")
            return images
        except Exception:
            return []

    async def _update_image_urls(self, completed: dict):
        """Scrape completed image URLs from HF history and attach them to progress records.
        Called periodically during generation so the Nexus dashboard can show thumbnails."""
        try:
            # Get all completed image URLs from the history section
            # (skip images that are still generating/queued — those have overlay text)
            urls = await self.page.evaluate("""() => {
                const urls = [];
                // Find the history/feed area — look for the grid of generated images
                const allImgs = document.querySelectorAll('img');
                for (const img of allImgs) {
                    const src = img.src || '';
                    if (!src || src.startsWith('data:') || src.includes('avatar') || src.includes('logo') ||
                        src.includes('icon') || src.includes('favicon') || src.includes('profile')) continue;
                    const r = img.getBoundingClientRect();
                    // Must be in the main content area, reasonably sized (generated images)
                    if (r.width < 80 || r.height < 80 || r.top < 50) continue;
                    // Skip images near the prompt area (bottom of page — these are reference uploads)
                    if (r.top > window.innerHeight - 200) continue;
                    // Check parent for generating/queued status
                    const parent = img.closest('[class*="card"], [class*="item"], [class*="grid"]') || img.parentElement;
                    const parentText = parent ? parent.innerText.toLowerCase() : '';
                    if (parentText.includes('generating') || parentText.includes('queued') || parentText.includes('loading')) continue;
                    urls.push(src);
                }
                return urls;
            }""")

            if not urls:
                return

            # Assign URLs to progress records that don't have them yet
            # Images on HF page are in reverse chronological order (newest first)
            # Our progress records are in submission order
            # Reverse the URLs so oldest completed = first record
            urls_reversed = list(reversed(urls))
            url_idx = 0

            updated = False
            for key in sorted(completed.keys()):
                for img_record in completed[key]:
                    if img_record.get("url"):
                        continue  # already has URL
                    if url_idx < len(urls_reversed):
                        img_record["url"] = urls_reversed[url_idx]
                        url_idx += 1
                        updated = True

            # Also update in-memory status images
            if updated:
                url_idx_2 = 0
                for img in self._status.get("generated_images", []):
                    if img.get("url"):
                        continue
                    if url_idx_2 < len(urls_reversed):
                        img["url"] = urls_reversed[url_idx_2]
                        url_idx_2 += 1

                # Persist
                if PROGRESS_FILE.exists():
                    try:
                        data = json.loads(PROGRESS_FILE.read_text())
                        data["completed"] = completed
                        PROGRESS_FILE.write_text(json.dumps(data, indent=2))
                    except Exception:
                        pass

                print(f"[HF] Updated {url_idx} image URLs from history")

        except Exception as e:
            print(f"[HF] Update image URLs error: {e}")

    @staticmethod
    def _cache_image_sync(url: str, scene: int, frame: str, index: int) -> str:
        """Download a single image to local cache. Returns local path or empty string."""
        from urllib.request import urlopen, Request
        cache_dir = HF_STORAGE / "cache" / f"scene_{scene}" / frame
        cache_dir.mkdir(parents=True, exist_ok=True)
        local_path = cache_dir / f"{index:03d}.jpg"
        if local_path.exists():
            return str(local_path)
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://higgsfield.ai/"})
            with urlopen(req, timeout=20) as resp:
                local_path.write_bytes(resp.read())
            return str(local_path)
        except Exception as e:
            print(f"[HF] Cache image error (s{scene}/{frame}/#{index}): {e}")
            return ""

    async def _cache_all_images(self, completed: dict) -> int:
        """Cache all images with URLs to local disk. Returns count cached."""
        cached = 0
        loop = asyncio.get_event_loop()
        for key in sorted(completed.keys()):
            for img in completed[key]:
                url = img.get("url")
                if not url or img.get("local_path"):
                    continue
                local = await loop.run_in_executor(
                    None, self._cache_image_sync, url,
                    img.get("scene", 0), img.get("frame", "start"), img.get("index", 0)
                )
                if local:
                    img["local_path"] = local
                    cached += 1
        return cached

    async def refresh_image_urls(self) -> dict:
        """Navigate to HF history page, scrape all image URLs, and backfill progress records.
        Use this when images were generated but URLs weren't captured during generation."""
        try:
            if not self.page:
                await self.init_context()
            # Navigate to history/image page
            await self.page.goto("https://higgsfield.ai/image/nano_banana_2", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            # Scroll down to load more images
            for _ in range(10):
                await self.page.evaluate("window.scrollBy(0, 800)")
                await asyncio.sleep(0.8)

            # Scrape all image URLs
            urls = await self.page.evaluate("""() => {
                const urls = [];
                const allImgs = document.querySelectorAll('img');
                for (const img of allImgs) {
                    const src = img.src || '';
                    if (!src || src.startsWith('data:') || src.includes('avatar') || src.includes('logo') ||
                        src.includes('icon') || src.includes('favicon') || src.includes('profile')) continue;
                    const r = img.getBoundingClientRect();
                    if (r.width < 80 || r.height < 80) continue;
                    const parent = img.closest('[class*="card"], [class*="item"], [class*="grid"]') || img.parentElement;
                    const parentText = parent ? parent.innerText.toLowerCase() : '';
                    if (parentText.includes('generating') || parentText.includes('queued') || parentText.includes('loading')) continue;
                    urls.push(src);
                }
                return urls;
            }""")

            if not urls:
                return {"ok": False, "error": "No images found on page", "count": 0}

            # Load progress
            if not PROGRESS_FILE.exists():
                return {"ok": False, "error": "No progress file", "count": 0}

            data = json.loads(PROGRESS_FILE.read_text())
            completed = data.get("completed", {})

            # HF shows newest first — reverse so oldest = first index
            urls_reversed = list(reversed(urls))
            url_idx = 0
            updated = 0

            for key in sorted(completed.keys()):
                for img_record in completed[key]:
                    if img_record.get("url"):
                        continue
                    if url_idx < len(urls_reversed):
                        img_record["url"] = urls_reversed[url_idx]
                        url_idx += 1
                        updated += 1

            # Cache images locally for fast loading
            cached = await self._cache_all_images(completed)

            # Save updated progress (with URLs + local paths)
            data["completed"] = completed
            PROGRESS_FILE.write_text(json.dumps(data, indent=2))

            # Update in-memory status
            self._status["generated_images"] = []
            for key, images in completed.items():
                for img in images:
                    self._status["generated_images"].append(img)

            print(f"[HF] Refreshed {updated} URLs, cached {cached} images locally")
            return {"ok": True, "updated": updated, "cached": cached, "total_scraped": len(urls)}

        except Exception as e:
            print(f"[HF] Refresh image URLs error: {e}")
            return {"ok": False, "error": str(e), "count": 0}

    async def download_images(self) -> dict:
        """Download all generated images from Higgsfield to local folders.
        Organizes as: ~/.nexus/higgsfield/downloads/scene_N/start/NNN.jpg
        Uses urllib (stdlib) to avoid aiohttp dependency."""
        from urllib.request import urlopen, Request
        downloads_dir = HF_STORAGE / "downloads"
        results = {"downloaded": 0, "errors": 0, "paths": []}

        if not self.page:
            return {"error": "Browser not open — log in first"}

        try:
            # Navigate to history/feed to scrape images
            await self.page.evaluate("""() => {
                const tabs = document.querySelectorAll('button, a, div[role="tab"]');
                for (const tab of tabs) {
                    const t = tab.textContent.trim().toLowerCase();
                    if (t.includes('history') || t.includes('feed') || t.includes('gallery')) {
                        tab.click(); return true;
                    }
                }
                return false;
            }""")
            await asyncio.sleep(2)

            # Scrape all image URLs from the page
            image_urls = await self._scrape_generated_images()
            print(f"[HF] Found {len(image_urls)} images to download")

            if not image_urls:
                for _ in range(5):
                    await self.page.evaluate("window.scrollBy(0, 800)")
                    await asyncio.sleep(1)
                image_urls = await self._scrape_generated_images()
                print(f"[HF] After scrolling: {len(image_urls)} images")

            # Load progress to map images to scenes/frames
            progress = {}
            if PROGRESS_FILE.exists():
                try:
                    progress = json.loads(PROGRESS_FILE.read_text()).get("completed", {})
                except Exception:
                    pass

            img_idx = 0
            for scene_key, images_list in progress.items():
                parts = scene_key.split("_")
                scene_num = parts[0]
                frame_type = parts[1] if len(parts) > 1 else "start"
                scene_dir = downloads_dir / f"scene_{scene_num}" / frame_type
                scene_dir.mkdir(parents=True, exist_ok=True)

                for i, img_record in enumerate(images_list):
                    file_path = scene_dir / f"{i+1:03d}.jpg"
                    if file_path.exists():
                        results["paths"].append(str(file_path))
                        img_record["local_path"] = str(file_path)
                        continue

                    if img_idx < len(image_urls):
                        url = image_urls[img_idx]
                        img_idx += 1
                        try:
                            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
                            resp = urlopen(req, timeout=30)
                            data = resp.read()
                            file_path.write_bytes(data)
                            results["downloaded"] += 1
                            results["paths"].append(str(file_path))
                            img_record["local_path"] = str(file_path)
                            img_record["url"] = url
                        except Exception as e:
                            print(f"[HF] Download error for {url}: {e}")
                            results["errors"] += 1

            # Save updated progress with local paths
            if progress and PROGRESS_FILE.exists():
                try:
                    data = json.loads(PROGRESS_FILE.read_text())
                    data["completed"] = progress
                    PROGRESS_FILE.write_text(json.dumps(data, indent=2))
                except Exception:
                    pass

            print(f"[HF] Downloaded {results['downloaded']} images, {results['errors']} errors")
            return results

        except Exception as e:
            print(f"[HF] Download images error: {e}")
            traceback.print_exc()
            return {"error": str(e)}

    async def download_from_history_page(self) -> dict:
        """Navigate to Higgsfield history and download all visible images.
        Also tries organized download (by scene/frame) if progress exists."""
        from urllib.request import urlopen, Request

        # First try the organized download
        organized = await self.download_images()
        if organized.get("downloaded", 0) > 0:
            return organized

        # Fallback: download everything into a flat directory
        downloads_dir = HF_STORAGE / "downloads" / "all"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        results = {"downloaded": 0, "errors": 0, "dir": str(downloads_dir)}

        if not self.page:
            return {"error": "Browser not open — log in and start generation first"}

        try:
            image_urls = await self._scrape_generated_images()

            # Scroll to load more
            for scroll in range(10):
                prev_count = len(image_urls)
                await self.page.evaluate("window.scrollBy(0, 1000)")
                await asyncio.sleep(1.5)
                image_urls = await self._scrape_generated_images()
                if len(image_urls) == prev_count:
                    break

            print(f"[HF] Found {len(image_urls)} images total to download")

            for idx, url in enumerate(image_urls):
                file_path = downloads_dir / f"{idx+1:04d}.jpg"
                if file_path.exists():
                    continue
                try:
                    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    resp = urlopen(req, timeout=30)
                    data = resp.read()
                    file_path.write_bytes(data)
                    results["downloaded"] += 1
                except Exception:
                    results["errors"] += 1

            print(f"[HF] Downloaded {results['downloaded']} images to {downloads_dir}")
            return results
        except Exception as e:
            return {"error": str(e)}

    # ── End frame from liked start frame ────────────────────────────────────

    async def generate_end_frames(self, scene_idx: int, scene_desc: str,
                                   liked_frame_path: str, images_per_frame: int = 30,
                                   broadcast_fn=None):
        """Generate end frames for a specific scene using a liked start frame as reference.
        The user picks a start frame they like, uploads it, and we use it as the new
        reference image to generate end frame variations."""
        if self._running:
            return {"ok": False, "error": "Generation already running"}

        self._running = True
        self._stop_requested = False
        self._broadcast_fn = broadcast_fn

        # Load existing progress
        completed = {}
        if PROGRESS_FILE.exists():
            try:
                data = json.loads(PROGRESS_FILE.read_text())
                self._scenes_hash = data.get("script_hash", "")
                completed = data.get("completed", {})
            except Exception:
                pass

        key = f"{scene_idx}_end"
        existing = completed.get(key, [])
        needed = images_per_frame - len(existing)

        self._status.update({
            "state": "generating",
            "current_scene": scene_idx,
            "current_frame": "end",
            "images_done": len(existing),
            "images_total": images_per_frame,
            "error": "",
        })

        try:
            if not self.page:
                _kill_stale_chrome_hf()
                await asyncio.sleep(2)
                ok = await self.init_context()
                if not ok:
                    raise RuntimeError("Failed to open browser")

            await self.page.goto(HF_IMAGE_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)
            await self._setup_settings()

            if needed <= 0:
                print(f"[HF] Scene {scene_idx} end: already at {images_per_frame} — done")
                self._status["state"] = "done"
                self._running = False
                return {"ok": True, "message": "Already complete"}

            print(f"[HF] Generating {needed} end frames for scene {scene_idx} using liked frame")
            await self._broadcast_status(f"Scene {scene_idx} end frames — {needed} remaining (using liked start frame)")

            # Generate end-frame prompts focused on the closing scene
            prompts = await self._generate_prompts(scene_desc, "end", needed)

            # Upload the liked start frame as the new reference
            self._ref_uploaded = False
            await self._ensure_ref_image(liked_frame_path)

            for prompt_idx, prompt in enumerate(prompts):
                if self._stop_requested:
                    break
                await self._wait_for_queue_slot()
                if self._stop_requested:
                    break

                self._status["state"] = "generating"
                ok = await self._submit_prompt(prompt, liked_frame_path)

                if ok:
                    img_index = len(existing) + prompt_idx + 1
                    img_record = {
                        "scene": scene_idx,
                        "frame": "end",
                        "index": img_index,
                        "prompt": prompt[:100],
                        "ref_frame": liked_frame_path,
                        "timestamp": datetime.now().isoformat(),
                    }
                    self._status["images_done"] = img_index
                    self._status["generated_images"].append(img_record)

                    if key not in completed:
                        completed[key] = []
                    completed[key].append(img_record)

                    # Save progress
                    if PROGRESS_FILE.exists():
                        try:
                            data = json.loads(PROGRESS_FILE.read_text())
                            data["completed"] = completed
                            PROGRESS_FILE.write_text(json.dumps(data, indent=2))
                        except Exception:
                            pass

                await asyncio.sleep(2)

            self._status["state"] = "done" if not self._stop_requested else "stopped"
            await self._broadcast_status("End frame generation complete!")
            return {"ok": True}

        except Exception as e:
            self._status["state"] = "error"
            self._status["error"] = str(e)
            print(f"[HF] End frame generation error: {e}")
            traceback.print_exc()
            return {"ok": False, "error": str(e)}
        finally:
            self._running = False

    # ── Static Ad Generation (uses pre-built prompts + CTA overlays) ─────────

    async def start_static_ads(self, campaign_ids: list, ref_image_path: str,
                                images_per_campaign: int = 4,
                                broadcast_fn=None) -> dict:
        """Generate static ad images using pre-built prompt library + CTA overlays.

        This is the 'unlimited nano banana pro' mode for static ads:
        1. Uses battle-tested prompts from the ad_prompts library
        2. Sends each prompt to Higgsfield with the reference image
        3. Waits for generation + downloads the images
        4. Applies CTA overlays (headline + button) using Pillow
        5. Saves FB/IG-ready PNGs to ~/.nexus/higgsfield/static_ads/

        Args:
            campaign_ids: List of campaign IDs from ad_prompts.AD_CAMPAIGNS
                          Pass ["all"] to run ALL campaigns
            ref_image_path: Path to the reference trailer image
            images_per_campaign: How many images per campaign (default 4 for A/B testing)
        """
        if self._running:
            return {"ok": False, "error": "Generation already running"}

        from core.ad_prompts import get_campaign_by_id, get_campaigns_by_category, AD_CAMPAIGNS, build_full_prompt
        from core.cta_overlay import apply_cta_overlay, OUTPUT_DIR

        self._running = True
        self._stop_requested = False
        self._broadcast_fn = broadcast_fn

        # Resolve campaign list
        if campaign_ids == ["all"]:
            campaigns = list(AD_CAMPAIGNS)
        else:
            campaigns = []
            for cid in campaign_ids:
                c = get_campaign_by_id(cid)
                if c:
                    campaigns.append(c)
                else:
                    # Try as category
                    campaigns.extend(get_campaigns_by_category(cid))

        if not campaigns:
            self._running = False
            return {"ok": False, "error": "No valid campaigns found"}

        total_images = len(campaigns) * images_per_campaign
        self._status.update({
            "state": "generating",
            "current_scene": 0,
            "total_scenes": len(campaigns),
            "images_done": 0,
            "images_total": total_images,
            "error": "",
            "mode": "static_ads",
        })

        results = {"ok": True, "campaigns": [], "total_generated": 0, "total_overlaid": 0, "ad_images": []}

        hf_logger.info(f"═══ START static_ads: {len(campaigns)} campaigns × {images_per_campaign} images ═══")
        hf_logger.info(f"ref_image_path={ref_image_path}")
        hf_logger.info(f"ref image exists={os.path.exists(ref_image_path) if ref_image_path else 'NO PATH'}")
        hf_logger.info(f"page={self.page is not None}, logged_in={self.logged_in}")

        try:
            # Ensure browser is ready
            if not self.page:
                hf_logger.info("No page — opening browser...")
                print("[HF] Opening browser for static ad generation...")
                _kill_stale_chrome_hf()
                await asyncio.sleep(2)
                ok = await self.init_context()
                hf_logger.info(f"init_context result: ok={ok}, page={self.page is not None}")
                if not ok or not self.page:
                    raise RuntimeError("Failed to open browser for generation")
            else:
                hf_logger.info("Browser already open, reusing page")

            # Navigate to image page and configure settings
            hf_logger.info(f"Navigating to {HF_IMAGE_URL}")
            await self.page.goto(HF_IMAGE_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)
            hf_logger.info("Navigation done, setting up settings...")
            await self._setup_settings()
            hf_logger.info("Settings configured")

            # Upload reference image once
            self._ref_uploaded = False
            hf_logger.info(f"Uploading ref image: {ref_image_path}")
            await self._ensure_ref_image(ref_image_path)
            hf_logger.info("Ref image uploaded")

            # ══════════════════════════════════════════════════════════
            # CONTINUOUS PIPELINE: Submit ALL prompts, keep queue full
            # Then download + overlay as images complete
            # ══════════════════════════════════════════════════════════
            from urllib.request import urlopen, Request

            # Build flat queue: [(campaign, prompt, img_i), ...]
            prompt_variations = [
                "",  # original
                " Slightly different camera angle.",
                " Alternative composition.",
                " Different time of day.",
            ]
            all_submissions = []
            for campaign in campaigns:
                full_prompt = build_full_prompt(campaign)
                for img_i in range(images_per_campaign):
                    variation = full_prompt
                    if img_i > 0:
                        variation += prompt_variations[img_i % len(prompt_variations)]
                    all_submissions.append({
                        "campaign": campaign,
                        "prompt": variation,
                        "img_i": img_i,
                    })

            total_expected = len(all_submissions)
            hf_logger.info(f"Pipeline: {total_expected} total prompts across {len(campaigns)} campaigns")

            # Take ONE before-snapshot (all URLs currently on page)
            before_set = set(await self._scrape_generated_images())
            hf_logger.info(f"Before snapshot: {len(before_set)} images on page")

            # ── PHASE 1: Submit ALL prompts, keeping queue at capacity ──
            submitted_count = 0
            for sub_idx, sub in enumerate(all_submissions):
                if self._stop_requested:
                    break

                camp = sub["campaign"]
                camp_id = camp["id"]

                # Update status display
                camp_idx = next(i for i, c in enumerate(campaigns) if c["id"] == camp_id)
                self._status["current_scene"] = camp_idx + 1
                self._status["current_frame"] = camp["concept"]
                await self._broadcast_status(
                    f"Submitting {sub_idx+1}/{total_expected}: {camp['concept']} #{sub['img_i']+1}"
                )

                await self._wait_for_queue_slot()
                if self._stop_requested:
                    break

                hf_logger.info(f"Submitting [{sub_idx+1}/{total_expected}] {camp_id} #{sub['img_i']+1}")
                ok = await self._submit_prompt(sub["prompt"], ref_image_path)

                if ok:
                    submitted_count += 1
                    self._status["images_done"] = sub_idx + 1
                    hf_logger.info(f"✓ Submitted [{sub_idx+1}] {camp_id} #{sub['img_i']+1}")
                else:
                    hf_logger.warning(f"✗ Submit FAILED [{sub_idx+1}] {camp_id} #{sub['img_i']+1}")

                # 1.5s between submissions (just enough to not overwhelm)
                await asyncio.sleep(1.5)

            hf_logger.info(f"═══ All submitted: {submitted_count}/{total_expected} ═══")

            # ── PHASE 2: Download + overlay as images complete ──────────
            if submitted_count > 0 and not self._stop_requested:
                await self._broadcast_status(
                    f"Downloading {submitted_count} images as they complete..."
                )

                downloaded_count = 0
                downloaded_urls = set()
                camp_results_map = {}  # camp_id → camp_result dict

                # Poll until all downloaded or timeout (25 min max for 56 images)
                max_polls = 300  # 300 × 5s = 25 minutes
                for poll in range(max_polls):
                    if self._stop_requested:
                        break
                    if downloaded_count >= submitted_count:
                        break

                    await asyncio.sleep(5)

                    # Scrape current images (newest-first from DOM)
                    current_list = await self._scrape_generated_images()
                    current_set = set(current_list)

                    # Find new images we haven't downloaded yet
                    new_ready = current_set - before_set - downloaded_urls
                    hf_logger.info(
                        f"Poll {poll+1}: {len(current_set)} total, "
                        f"{downloaded_count} downloaded, {len(new_ready)} new ready "
                        f"(need {submitted_count - downloaded_count} more)"
                    )

                    if not new_ready:
                        continue

                    # Download each new image and assign to next campaign in order
                    for url in new_ready:
                        if downloaded_count >= submitted_count:
                            break

                        # Assign to campaign based on submission order
                        sub = all_submissions[downloaded_count]
                        campaign = sub["campaign"]
                        camp_id = campaign["id"]
                        camp_name = campaign["concept"]

                        # Ensure camp result exists
                        if camp_id not in camp_results_map:
                            camp_results_map[camp_id] = {
                                "id": camp_id, "name": camp_name,
                                "generated": 0, "overlaid": 0, "images": []
                            }
                        camp_result = camp_results_map[camp_id]

                        try:
                            raw_dir = HF_STORAGE / "static_ads_raw" / camp_id
                            raw_dir.mkdir(parents=True, exist_ok=True)
                            existing = list(raw_dir.glob("*.jpg"))
                            raw_path = raw_dir / f"{len(existing)+1:03d}.jpg"

                            req = Request(url, headers={
                                "User-Agent": "Mozilla/5.0",
                                "Referer": "https://higgsfield.ai/"
                            })
                            with urlopen(req, timeout=30) as resp:
                                raw_path.write_bytes(resp.read())

                            hf_logger.info(
                                f"Downloaded [{downloaded_count+1}/{submitted_count}] "
                                f"{camp_id}: {raw_path.name} ({raw_path.stat().st_size // 1024} KB)"
                            )

                            # Apply this campaign's CTA overlay
                            ad_path = apply_cta_overlay(
                                image_path=str(raw_path),
                                headline=campaign["headline"],
                                cta_text=campaign["cta"],
                                subline=campaign.get("subline", ""),
                                palette=campaign.get("palette", "deep_navy"),
                                campaign_id=camp_id,
                            )
                            camp_result["overlaid"] += 1
                            camp_result["generated"] += 1
                            camp_result["images"].append({
                                "raw": str(raw_path),
                                "ad": ad_path,
                                "campaign": camp_id,
                                "headline": campaign["headline"],
                                "cta": campaign["cta"],
                            })
                            results["ad_images"].append(ad_path)
                            downloaded_urls.add(url)
                            downloaded_count += 1

                            self._status["current_frame"] = f"Downloaded {downloaded_count}/{submitted_count}"
                            await self._broadcast_status(
                                f"✓ Ad {downloaded_count}/{submitted_count}: {camp_name}"
                            )
                            hf_logger.info(f"✓ Ad [{downloaded_count}]: {ad_path}")
                            print(f"[HF-AD] ✓ [{downloaded_count}/{submitted_count}] {ad_path}")

                        except Exception as e:
                            hf_logger.error(f"Download/overlay error [{downloaded_count+1}]: {e}")
                            # Skip this URL but still count it to keep alignment
                            downloaded_urls.add(url)
                            downloaded_count += 1

                # Compile results from map
                for camp_id, cr in camp_results_map.items():
                    results["total_generated"] += cr["generated"]
                    results["total_overlaid"] += cr["overlaid"]
                    results["campaigns"].append(cr)

            self._status["state"] = "done" if not self._stop_requested else "stopped"
            self._status["mode"] = ""
            total_ads = results["total_overlaid"]
            await self._broadcast_status(
                f"Static ad generation complete! {total_ads} ready-to-post images in ~/.nexus/higgsfield/static_ads/"
            )
            print(f"\n[HF-AD] ═══ DONE: {total_ads} ad images ready ═══")
            print(f"[HF-AD] Output: {OUTPUT_DIR}")
            return results

        except Exception as e:
            self._status["state"] = "error"
            self._status["error"] = str(e)
            hf_logger.error(f"FATAL ERROR: {e}")
            hf_logger.error(traceback.format_exc())
            print(f"[HF-AD] Error: {e}")
            traceback.print_exc()
            return {"ok": False, "error": str(e)}
        finally:
            self._running = False
            hf_logger.info("═══ static_ads task finished ═══")

    # ── Control ──────────────────────────────────────────────────────────────

    async def stop(self):
        """Gracefully stop the generation loop."""
        self._stop_requested = True
        self._status["state"] = "stopping"
        print("[HF] Stop requested")

    async def close(self):
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
        self._ref_uploaded = False

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

    async def _broadcast_status(self, message: str):
        if self._broadcast_fn:
            try:
                await self._broadcast_fn({
                    "type": "hf_event",
                    "message": message,
                    "status": self.get_status(),
                })
            except:
                pass


# ── Module-level init ─────────────────────────────────────────────────────────

async def init_higgsfield():
    """Initialize Higgsfield bot (no browser launched — lazy init on first use)."""
    global _hf
    _hf = HiggsFieldBot()
    print("[HF] Bot created — use /api/higgsfield/login to log in")
    return _hf


def get_hf() -> HiggsFieldBot | None:
    return _hf
