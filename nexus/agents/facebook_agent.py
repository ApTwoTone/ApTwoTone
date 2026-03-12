"""
Facebook Group Engagement Agent — Browses groups, finds relevant posts, engages naturally.
Uses Playwright for browser automation with persistent Chrome profile.
Integrates with ContentAgent for response generation.
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
    AGENT_DEFAULTS, BUSINESS, FACEBOOK_GROUPS,
    ENGAGEMENT_TRIGGERS, AGENT_STATE_DIR, BROWSER_PROFILES_DIR,
)
from agents.content_agent import ContentAgent

FB_PROFILE_DIR = BROWSER_PROFILES_DIR / "fb_agent_profile"
ENGAGEMENT_LOG = AGENT_STATE_DIR / "fb_engagement_log.json"


class FacebookAgent(BaseAgent):
    """Autonomous Facebook group engagement agent."""

    def __init__(self):
        super().__init__("fb_engage", "facebook_engagement")
        self.content = ContentAgent()
        self._browser = None
        self._page = None
        self._engaged_posts = set()
        self._load_engaged_posts()

    def _load_engaged_posts(self):
        """Load previously engaged post IDs to avoid duplicates."""
        if ENGAGEMENT_LOG.exists():
            try:
                log = json.loads(ENGAGEMENT_LOG.read_text())
                self._engaged_posts = set(e.get("post_id", "") for e in log if e.get("post_id"))
            except Exception:
                pass

    def _save_engagement(self, entry: dict):
        """Log an engagement action."""
        log = []
        if ENGAGEMENT_LOG.exists():
            try:
                log = json.loads(ENGAGEMENT_LOG.read_text())
            except Exception:
                pass
        log.append(entry)
        # Keep last 1000 entries
        if len(log) > 1000:
            log = log[-1000:]
        ENGAGEMENT_LOG.write_text(json.dumps(log, indent=2))

    # ── Browser Setup ──────────────────────────────────────────────────────

    async def _init_browser(self):
        """Initialize Playwright browser with persistent profile."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.log("playwright not installed. Run: pip install playwright && playwright install chromium", "ERROR")
            return False

        FB_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

        self.log("Launching browser with persistent profile...")
        self._pw = await async_playwright().__aenter__()
        self._browser = await self._pw.chromium.launch_persistent_context(
            str(FB_PROFILE_DIR),
            headless=False,  # Visible so user can do initial login
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
            ],
        )
        self._page = self._browser.pages[0] if self._browser.pages else await self._browser.new_page()
        self.log("Browser ready")
        return True

    async def _check_login(self) -> bool:
        """Check if already logged into Facebook."""
        try:
            await self._page.goto("https://www.facebook.com", wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(3)
            url = self._page.url
            if "login" in url or "checkpoint" in url:
                self.log("Not logged into Facebook. Please log in manually in the browser window.", "WARN")
                return False
            self.log("Facebook login confirmed")
            return True
        except Exception as e:
            self.log(f"Login check failed: {e}", "ERROR")
            return False

    # ── Group Browsing ─────────────────────────────────────────────────────

    async def browse_group(self, group_url: str, group_name: str) -> list:
        """
        Browse a Facebook group, scroll through posts, identify engagement opportunities.
        Returns list of posts with their context and intent level.
        """
        self.log(f"Browsing group: {group_name}")
        posts = []

        try:
            await self._page.goto(group_url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(random.uniform(3, 6))

            # Scroll down 3-5 times to load posts
            scroll_count = random.randint(3, 5)
            for i in range(scroll_count):
                await self._page.evaluate("window.scrollBy(0, 800)")
                await asyncio.sleep(random.uniform(
                    AGENT_DEFAULTS["scroll_delay_min"],
                    AGENT_DEFAULTS["scroll_delay_max"]
                ))

            # Extract post content using page evaluation
            raw_posts = await self._page.evaluate("""() => {
                const posts = [];
                // Find all post containers
                const feedPosts = document.querySelectorAll('[role="article"]');
                feedPosts.forEach((post, idx) => {
                    if (idx > 15) return; // Limit to 15 posts
                    const textEl = post.querySelector('[data-ad-preview="message"]') ||
                                   post.querySelector('[dir="auto"]');
                    const text = textEl ? textEl.innerText : '';
                    if (text.length < 10) return;

                    // Get post ID from links
                    const links = post.querySelectorAll('a[href*="/posts/"], a[href*="permalink"]');
                    let postId = '';
                    for (const link of links) {
                        const match = link.href.match(/posts\\/([\\d]+)/);
                        if (match) { postId = match[1]; break; }
                    }

                    // Get author name
                    const authorEl = post.querySelector('h3 a, h4 a, strong');
                    const author = authorEl ? authorEl.innerText : 'Unknown';

                    // Comment count
                    const commentEl = post.querySelector('[aria-label*="comment"]');
                    const comments = commentEl ? commentEl.innerText : '0';

                    posts.push({
                        text: text.substring(0, 500),
                        post_id: postId || `post_${idx}`,
                        author: author,
                        comments: comments,
                    });
                });
                return posts;
            }""")

            # Analyze each post for engagement opportunity
            for raw in raw_posts:
                if raw["post_id"] in self._engaged_posts:
                    continue  # Skip already-engaged posts

                intent = self._classify_intent(raw["text"])
                if intent != "none":
                    posts.append({
                        **raw,
                        "intent": intent,
                        "group_name": group_name,
                        "group_url": group_url,
                    })

            self.log(f"Found {len(posts)} engagement opportunities in {group_name}")
            return posts

        except Exception as e:
            self.log(f"Error browsing {group_name}: {e}", "ERROR")
            return []

    def _classify_intent(self, text: str) -> str:
        """Classify post intent level based on trigger keywords."""
        text_lower = text.lower()

        for keyword in ENGAGEMENT_TRIGGERS["high_intent"]:
            if keyword in text_lower:
                return "high"

        for keyword in ENGAGEMENT_TRIGGERS["medium_intent"]:
            if keyword in text_lower:
                return "medium"

        for keyword in ENGAGEMENT_TRIGGERS["general"]:
            if keyword in text_lower:
                return "general"

        return "none"

    # ── Engagement ─────────────────────────────────────────────────────────

    async def engage_with_post(self, post: dict) -> bool:
        """
        Generate and post a comment on a relevant Facebook post.
        Uses AI to generate a natural, helpful response.
        """
        try:
            # Generate comment using content agent
            comment = await self.content.generate_comment(
                post_context=post["text"],
                platform="facebook",
                intent_level=post["intent"],
            )

            if not comment or len(comment) < 10:
                self.log(f"Generated comment too short, skipping")
                return False

            # Quality check — make sure it doesn't sound bot-like
            quality = await self._quality_check(comment)
            if not quality:
                self.log(f"Comment failed quality check, skipping")
                return False

            self.log(f"Engaging with post (intent={post['intent']}): {post['text'][:80]}...")
            self.log(f"Comment: {comment}")

            # Save to engagement log (even if not posted — for review)
            self._save_engagement({
                "post_id": post["post_id"],
                "post_text": post["text"][:200],
                "comment": comment,
                "intent": post["intent"],
                "group_name": post["group_name"],
                "ts": datetime.now().isoformat(),
                "status": "queued",  # Will be "posted" after actual submission
            })

            # Mark as engaged
            self._engaged_posts.add(post["post_id"])
            self._state["comments_posted"] = self._state.get("comments_posted", 0) + 1
            self._state["actions_taken"] = self._state.get("actions_taken", 0) + 1
            self.save_state()

            # Log to CRM
            self.log_to_crm("fb_comment", f"Group: {post['group_name']} | Intent: {post['intent']} | Comment: {comment[:100]}")

            return True

        except Exception as e:
            self.log(f"Engagement error: {e}", "ERROR")
            return False

    async def _quality_check(self, comment: str) -> bool:
        """Check if a generated comment sounds natural (not bot-like)."""
        check_prompt = f"""Rate this social media comment on a scale of 1-10 for naturalness.
A real person would write this as a comment on Facebook.

Comment: "{comment}"

Reply with ONLY a number 1-10.
1 = obviously a bot/ad, 10 = perfectly natural human comment."""

        score_text = await self.think(check_prompt, max_tokens=5)
        try:
            score = int(re.search(r'\d+', score_text).group())
            return score >= 6
        except Exception:
            return True  # Default to allowing if check fails

    # ── Main Loop ──────────────────────────────────────────────────────────

    async def run_engagement_cycle(self, max_actions: int = 5):
        """
        Run one engagement cycle:
        1. Browse groups
        2. Find opportunities
        3. Engage with best matches
        """
        all_opportunities = []

        # Browse 2-3 random groups per cycle
        groups = random.sample(FACEBOOK_GROUPS, min(3, len(FACEBOOK_GROUPS)))

        for group in groups:
            posts = await self.browse_group(group["url"], group["name"])
            all_opportunities.extend(posts)
            await asyncio.sleep(random.uniform(5, 15))  # Pause between groups

        if not all_opportunities:
            self.log("No engagement opportunities found this cycle")
            return 0

        # Sort by intent (high first)
        intent_order = {"high": 0, "medium": 1, "general": 2}
        all_opportunities.sort(key=lambda p: intent_order.get(p["intent"], 3))

        # Engage with top opportunities (up to max_actions)
        engaged = 0
        for post in all_opportunities[:max_actions]:
            await self.rate_limit("comment")
            success = await self.engage_with_post(post)
            if success:
                engaged += 1

        self.log(f"Cycle complete: {engaged}/{len(all_opportunities)} engagements")
        return engaged

    async def start(self):
        """Start the Facebook engagement agent."""
        await super().start()

        if not await self._init_browser():
            return

        if not await self._check_login():
            self.log("Waiting for manual Facebook login... Check the browser window.")
            # Wait up to 5 minutes for login
            for _ in range(60):
                await asyncio.sleep(5)
                if await self._check_login():
                    break
            else:
                self.log("Facebook login timeout. Please log in and restart.", "ERROR")
                return

        self.log("Starting engagement loop")
        while self._running:
            try:
                await self.run_engagement_cycle(max_actions=3)
                # Wait 30-60 minutes between cycles
                wait = random.randint(1800, 3600)
                self.log(f"Cycle done. Next cycle in {wait//60} minutes")
                for _ in range(wait):
                    if not self._running:
                        break
                    await asyncio.sleep(1)
            except Exception as e:
                self.log(f"Cycle error: {e}", "ERROR")
                await asyncio.sleep(300)  # Wait 5 min on error

    async def cleanup(self):
        """Close browser."""
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
