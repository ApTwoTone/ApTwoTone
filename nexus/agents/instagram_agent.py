"""
Instagram Engagement Agent — Browses hashtags, engages with relevant posts.
Uses Playwright with persistent profile. Focuses on building presence
in SoCal event planning community.
"""
import asyncio
import json
import random
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from agents.base import BaseAgent
from agents.config import (
    AGENT_DEFAULTS, BUSINESS, INSTAGRAM_HASHTAGS,
    ENGAGEMENT_TRIGGERS, AGENT_STATE_DIR, BROWSER_PROFILES_DIR,
)
from agents.content_agent import ContentAgent

IG_PROFILE_DIR = BROWSER_PROFILES_DIR / "ig_agent_profile"
IG_ENGAGEMENT_LOG = AGENT_STATE_DIR / "ig_engagement_log.json"


class InstagramAgent(BaseAgent):
    """Autonomous Instagram engagement agent."""

    def __init__(self):
        super().__init__("ig_engage", "instagram_engagement")
        self.content = ContentAgent()
        self._browser = None
        self._page = None
        self._engaged_posts = set()
        self._load_engaged()

    def _load_engaged(self):
        if IG_ENGAGEMENT_LOG.exists():
            try:
                log = json.loads(IG_ENGAGEMENT_LOG.read_text())
                self._engaged_posts = set(e.get("post_url", "") for e in log if e.get("post_url"))
            except Exception:
                pass

    def _save_engagement(self, entry: dict):
        log = []
        if IG_ENGAGEMENT_LOG.exists():
            try:
                log = json.loads(IG_ENGAGEMENT_LOG.read_text())
            except Exception:
                pass
        log.append(entry)
        if len(log) > 1000:
            log = log[-1000:]
        IG_ENGAGEMENT_LOG.write_text(json.dumps(log, indent=2))

    # ── Browser Setup ──────────────────────────────────────────────────────

    async def _init_browser(self):
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.log("playwright not installed", "ERROR")
            return False

        IG_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

        self._pw = await async_playwright().__aenter__()
        self._browser = await self._pw.chromium.launch_persistent_context(
            str(IG_PROFILE_DIR),
            headless=False,
            viewport={"width": 430, "height": 932},  # Mobile viewport for Instagram
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
            args=[
                "--disable-blink-features=AutomationControlled",
            ],
        )
        self._page = self._browser.pages[0] if self._browser.pages else await self._browser.new_page()
        self.log("Instagram browser ready")
        return True

    async def _check_login(self) -> bool:
        try:
            await self._page.goto("https://www.instagram.com", wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(4)
            url = self._page.url
            if "login" in url or "accounts" in url:
                self.log("Not logged into Instagram. Please log in manually.", "WARN")
                return False
            self.log("Instagram login confirmed")
            return True
        except Exception as e:
            self.log(f"Login check failed: {e}", "ERROR")
            return False

    # ── Hashtag Browsing ───────────────────────────────────────────────────

    async def browse_hashtag(self, hashtag: str) -> list:
        """Browse an Instagram hashtag and find engagement opportunities."""
        self.log(f"Browsing hashtag: {hashtag}")
        posts = []
        clean_tag = hashtag.lstrip("#")

        try:
            await self._page.goto(
                f"https://www.instagram.com/explore/tags/{clean_tag}/",
                wait_until="domcontentloaded", timeout=20000
            )
            await asyncio.sleep(random.uniform(3, 6))

            # Scroll to load posts
            for _ in range(random.randint(2, 4)):
                await self._page.evaluate("window.scrollBy(0, 600)")
                await asyncio.sleep(random.uniform(2, 5))

            # Extract post links
            post_links = await self._page.evaluate("""() => {
                const links = [];
                document.querySelectorAll('a[href*="/p/"]').forEach(a => {
                    if (links.length < 10 && !links.includes(a.href)) {
                        links.push(a.href);
                    }
                });
                return links;
            }""")

            # Visit top posts and extract details
            for link in post_links[:6]:
                if link in self._engaged_posts:
                    continue

                try:
                    await self._page.goto(link, wait_until="domcontentloaded", timeout=15000)
                    await asyncio.sleep(random.uniform(2, 4))

                    post_data = await self._page.evaluate("""() => {
                        const caption = document.querySelector('h1') ||
                                       document.querySelector('[class*="Caption"]') ||
                                       document.querySelector('span[dir="auto"]');
                        const author = document.querySelector('header a') ||
                                      document.querySelector('a[class*="username"]');
                        return {
                            caption: caption ? caption.innerText.substring(0, 500) : '',
                            author: author ? author.innerText : 'unknown',
                            url: window.location.href,
                        };
                    }""")

                    if post_data["caption"] and len(post_data["caption"]) > 15:
                        intent = self._classify_intent(post_data["caption"])
                        if intent != "none":
                            posts.append({
                                **post_data,
                                "intent": intent,
                                "hashtag": hashtag,
                            })
                except Exception as e:
                    self.log(f"Error reading post: {e}", "WARN")
                    continue

                await asyncio.sleep(random.uniform(3, 7))

            self.log(f"Found {len(posts)} opportunities for #{clean_tag}")
            return posts

        except Exception as e:
            self.log(f"Error browsing #{clean_tag}: {e}", "ERROR")
            return []

    def _classify_intent(self, text: str) -> str:
        text_lower = text.lower()
        for kw in ENGAGEMENT_TRIGGERS["high_intent"]:
            if kw in text_lower:
                return "high"
        for kw in ENGAGEMENT_TRIGGERS["medium_intent"]:
            if kw in text_lower:
                return "medium"
        for kw in ENGAGEMENT_TRIGGERS["general"]:
            if kw in text_lower:
                return "general"
        return "none"

    # ── Engagement ─────────────────────────────────────────────────────────

    async def engage_with_post(self, post: dict) -> bool:
        """Generate and queue a comment for an Instagram post."""
        try:
            comment = await self.content.generate_comment(
                post_context=post["caption"],
                platform="instagram",
                intent_level=post["intent"],
            )

            if not comment or len(comment) < 5:
                return False

            # Quality check
            quality = await self._quality_check(comment)
            if not quality:
                self.log("Comment failed quality check")
                return False

            self.log(f"IG engagement ({post['intent']}): {comment[:80]}")

            self._save_engagement({
                "post_url": post["url"],
                "author": post["author"],
                "caption": post["caption"][:200],
                "comment": comment,
                "intent": post["intent"],
                "hashtag": post.get("hashtag", ""),
                "ts": datetime.now().isoformat(),
                "status": "queued",
            })

            self._engaged_posts.add(post["url"])
            self._state["comments_posted"] = self._state.get("comments_posted", 0) + 1
            self._state["actions_taken"] = self._state.get("actions_taken", 0) + 1
            self.save_state()

            self.log_to_crm("ig_comment", f"Hashtag: {post.get('hashtag','')} | Comment: {comment[:100]}")
            return True

        except Exception as e:
            self.log(f"IG engagement error: {e}", "ERROR")
            return False

    async def _quality_check(self, comment: str) -> bool:
        check = f"""Rate 1-10 naturalness for an Instagram comment.
Comment: "{comment}"
Reply ONLY a number."""
        score_text = await self.think(check, max_tokens=5)
        try:
            score = int(re.search(r'\d+', score_text).group())
            return score >= 6
        except Exception:
            return True

    # ── Main Loop ──────────────────────────────────────────────────────────

    async def run_engagement_cycle(self, max_actions: int = 3):
        all_posts = []
        hashtags = random.sample(INSTAGRAM_HASHTAGS, min(3, len(INSTAGRAM_HASHTAGS)))

        for tag in hashtags:
            posts = await self.browse_hashtag(tag)
            all_posts.extend(posts)
            await asyncio.sleep(random.uniform(8, 20))

        if not all_posts:
            self.log("No IG opportunities found")
            return 0

        intent_order = {"high": 0, "medium": 1, "general": 2}
        all_posts.sort(key=lambda p: intent_order.get(p["intent"], 3))

        engaged = 0
        for post in all_posts[:max_actions]:
            await self.rate_limit("comment")
            if await self.engage_with_post(post):
                engaged += 1

        self.log(f"IG cycle: {engaged} engagements")
        return engaged

    async def start(self):
        await super().start()

        if not await self._init_browser():
            return

        if not await self._check_login():
            self.log("Waiting for Instagram login...")
            for _ in range(60):
                await asyncio.sleep(5)
                if await self._check_login():
                    break
            else:
                self.log("Instagram login timeout", "ERROR")
                return

        while self._running:
            try:
                await self.run_engagement_cycle(max_actions=3)
                wait = random.randint(2400, 4800)  # 40-80 min between cycles
                self.log(f"Next IG cycle in {wait//60} minutes")
                for _ in range(wait):
                    if not self._running:
                        break
                    await asyncio.sleep(1)
            except Exception as e:
                self.log(f"IG cycle error: {e}", "ERROR")
                await asyncio.sleep(300)

    async def cleanup(self):
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if hasattr(self, '_pw') and self._pw:
            try:
                await self._pw.__aexit__(None, None, None)
            except Exception:
                pass
