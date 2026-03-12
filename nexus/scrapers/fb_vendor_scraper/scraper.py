#!/usr/bin/env python3
"""
FB Vendor Scraper — Main orchestrator.
Runs a full pipeline: discover groups → geo-filter → auto-join → scrape for leads.
Each stage saves progress to disk so it picks up where it left off on re-run.

Usage:
    python scraper.py              # Full pipeline (discover → join → scrape)
    python scraper.py --discover   # Discover new groups only
    python scraper.py --join       # Geo-filter + auto-join only
    python scraper.py --scrape     # Scrape already-joined groups only
    python scraper.py --schedule   # Run on daily schedule + listen for triggers
"""
import sys
import os
import json
import asyncio
import logging
import argparse
import random
import re
from datetime import datetime, timezone, timedelta
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

# Ensure the scraper directory is on the path
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    GROUPS_FILE, LOG_FILE, STATE_FILE, DISCOVERED_FILE,
    MAX_SESSION_MINUTES, FIRST_RUN_LOOKBACK_DAYS,
    DAILY_SCHEDULE_HOUR, SEARCH_TERMS,
    GROUP_BREAK_MIN, GROUP_BREAK_MAX,
    PROFILE_BATCH_SIZE, PROFILE_BATCH_BREAK_MIN, PROFILE_BATCH_BREAK_MAX,
    JOIN_DELAY_MIN, JOIN_DELAY_MAX,
    JOIN_BATCH_SIZE, JOIN_BATCH_BREAK_MIN, JOIN_BATCH_BREAK_MAX,
)
from browser_utils import (
    launch_browser, reopen_chrome, is_logged_in, check_login_no_navigate,
    check_for_blocking, action_delay, minor_delay, scroll_page, click_see_more,
    idle_browse, move_mouse_randomly, safe_goto, random_delay,
    setup_page_optimizations,
)
from post_filter import is_relevant_post, classify_post_type, detect_category, is_relevant_group, is_local_group
from profile_scraper import extract_profile_data
from data_sender import send_vendor, retry_pending
from graphql_capture import GraphQLCapture
from listener import (
    start_listener, is_scrape_requested, clear_scrape_request,
    is_stop_requested, clear_stop_request,
)


# ── Logging setup ────────────────────────────────────────────────────────────

def setup_logging():
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    handler = TimedRotatingFileHandler(
        str(LOG_FILE), when="D", interval=1, backupCount=30,
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log = logging.getLogger("fb_scraper")
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    log.addHandler(console)
    return log


log = setup_logging()


# ── State management ─────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"groups": {}}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def load_groups() -> list:
    if GROUPS_FILE.exists():
        try:
            return json.loads(GROUPS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return []


def save_groups(groups: list):
    GROUPS_FILE.write_text(json.dumps(groups, indent=2))


async def ensure_logged_in(page) -> bool:
    """Check Facebook login, prompt user if needed. Returns True if logged in."""
    if await is_logged_in(page):
        log.info("Facebook login confirmed")
        return True
    log.info("Not logged into Facebook yet.")
    print("\n" + "=" * 60)
    print("FIRST RUN — Please log into Facebook in the Chrome window.")
    print("Once you're on your Facebook feed, press ENTER here to continue...")
    print("=" * 60)
    await asyncio.get_event_loop().run_in_executor(None, input)
    if not await check_login_no_navigate(page):
        log.error("Still not logged in. Please try again.")
        return False
    log.info("Facebook login confirmed — session saved for future runs.")
    return True


# ── Group Discovery ──────────────────────────────────────────────────────────

async def discover_groups(page):
    """Search Facebook for relevant groups and return list of found groups."""
    log.info("Starting group discovery...")
    discovered = []

    total_terms = len(SEARCH_TERMS)
    for idx, term in enumerate(SEARCH_TERMS, 1):
        if is_stop_requested():
            break

        print(f"  [{idx}/{total_terms}] Searching: \"{term}\"...", flush=True)
        search_url = f"https://www.facebook.com/search/groups/?q={term.replace(' ', '%20')}"
        ok = await safe_goto(page, search_url, timeout=20000)
        if not ok:
            print(f"    Could not load results", flush=True)
            log.warning(f"Could not load search for: {term}")
            continue

        # Scroll to load more results
        print(f"    Scrolling for results...", flush=True)
        for _ in range(3):
            await scroll_page(page)
            await minor_delay()

        # Extract group cards from search results
        try:
            links = await page.query_selector_all('a[href*="/groups/"]')
            for link in links:
                try:
                    href = await link.get_attribute("href") or ""
                    if "/groups/" not in href or "/search/" in href:
                        continue
                    # Clean the URL
                    group_url = href.split("?")[0]
                    if not group_url.startswith("http"):
                        group_url = "https://www.facebook.com" + group_url

                    # Get surrounding text for name/description
                    parent = await link.evaluate_handle("el => el.closest('[role=\"article\"], div')")
                    text = ""
                    try:
                        text = await parent.as_element().inner_text()
                    except Exception:
                        text = await link.inner_text()

                    lines = [l.strip() for l in text.split("\n") if l.strip()]
                    name = lines[0] if lines else ""
                    description = " ".join(lines[1:4]) if len(lines) > 1 else ""

                    # Extract member count
                    member_count = "?"
                    for line in lines:
                        mc = re.search(r'([\d,.]+[KkMm]?)\s*members?', line)
                        if mc:
                            member_count = mc.group(1)
                            break

                    # Filter relevance
                    if not name or not is_relevant_group(name, description):
                        continue

                    # Deduplicate
                    if any(d["url"] == group_url for d in discovered):
                        continue

                    discovered.append({
                        "name": name[:100],
                        "url": group_url,
                        "member_count": member_count,
                        "description": description[:200],
                    })
                    print(f"    + {name[:60]} ({member_count} members)  [{len(discovered)} total]", flush=True)
                except Exception:
                    continue
        except Exception as e:
            log.warning(f"Error extracting groups for '{term}': {e}")

        # Natural pause between searches — scroll around, move mouse
        print(f"    Idle browsing before next search...", flush=True)
        await move_mouse_randomly(page)
        await idle_browse(page)
        await action_delay()

    log.info(f"Discovered {len(discovered)} relevant groups")
    return discovered


# ── Popup / dialog dismisser ─────────────────────────────────────────────────

async def dismiss_popup(page):
    """Dismiss any overlay dialog (welcome popups, notifications, etc.)."""
    try:
        for sel in [
            'div[role="dialog"] [aria-label="Close"]',
            'div[role="dialog"] [role="button"]:has-text("Not now")',
            'div[role="dialog"] [role="button"]:has-text("OK")',
            'div[role="dialog"] [role="button"]:has-text("Got it")',
            'div[role="dialog"] [role="button"]:has-text("Close")',
        ]:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                await btn.click()
                await asyncio.sleep(0.5)
                return
    except Exception:
        pass


# ── Post Extraction ──────────────────────────────────────────────────────────

async def extract_posts(page, group: dict, state: dict, gql_capture: GraphQLCapture = None) -> list:
    """Scrape posts from a single group. Returns list of raw post dicts.
    Uses GraphQL interception as primary source, DOM parsing as fallback."""
    group_url = group["url"]
    group_name = group.get("name", "Unknown")
    log.info(f"Scraping group: {group_name}")

    # Reset GraphQL capture for this group
    if gql_capture:
        gql_capture.reset()

    ok = await safe_goto(page, group_url)
    if not ok:
        return []

    # Dismiss any popup/welcome dialog that might overlay the page
    await dismiss_popup(page)

    # ── Click the Discussion/Posts tab to ensure the feed is showing ──
    # Facebook groups sometimes load on About, Members, or other tabs.
    # We need the Discussion (or Posts) tab for actual post content.
    for tab_text in ["Discussion", "Posts", "Recent"]:
        try:
            tab = await page.query_selector(f'a[role="tab"]:has-text("{tab_text}")')
            if not tab:
                tab = await page.query_selector(f'a:has-text("{tab_text}")[href*="/groups/"]')
            if tab and await tab.is_visible():
                await tab.click()
                log.info(f"Clicked '{tab_text}' tab for {group_name}")
                await asyncio.sleep(2)
                break
        except Exception:
            continue

    # ── Wait for feed content to actually load ──
    # safe_goto fires on domcontentloaded, but Facebook posts load async.
    # Wait up to 12s for [role="feed"] or [role="article"] to appear.
    feed_loaded = False
    for wait_attempt in range(12):
        feed_el = await page.query_selector('[role="feed"]')
        article_el = await page.query_selector('[role="article"]')
        if feed_el or article_el:
            feed_loaded = True
            log.info(f"Feed loaded after {wait_attempt + 1}s for {group_name}")
            break
        await asyncio.sleep(1)

    if not feed_loaded:
        log.warning(f"Feed never loaded for {group_name} — skipping")
        print(f"    Feed failed to load — skipping", flush=True)
        return []

    # Extra settle time for more posts to render
    await asyncio.sleep(1.5)

    # Dismiss any popup that appeared during load
    await dismiss_popup(page)

    # Determine how far to scroll back
    group_state = state.get("groups", {}).get(group_url, {})
    last_post_date = group_state.get("last_post_date", "")
    is_first_run = not last_post_date

    if is_first_run:
        lookback = datetime.now(timezone.utc) - timedelta(days=FIRST_RUN_LOOKBACK_DAYS)
        max_scrolls = 20  # Enough to grab recent posts; don't deep-dive one group
        log.info(f"First run for {group_name} — looking back {FIRST_RUN_LOOKBACK_DAYS} days")
    else:
        lookback = None
        max_scrolls = 12

    posts = []
    seen_profiles = set()
    scroll_count = 0
    no_new_content_count = 0
    prev_article_count = 0
    total_articles_seen = 0

    # Determine the best article selector — prefer articles INSIDE [role="feed"]
    # to avoid grabbing sidebar widgets, "About" cards, or pinned admin content
    feed_el = await page.query_selector('[role="feed"]')
    if feed_el:
        article_selector = '[role="feed"] [role="article"]'
        log.info(f"Using feed-scoped article selector for {group_name}")
    else:
        article_selector = '[role="article"]'
        log.info(f"No [role='feed'] found — using global article selector for {group_name}")

    while scroll_count < max_scrolls:
        if is_stop_requested():
            log.info("Stop requested — finishing current group")
            break

        # Guard: if we drifted away from the group page, navigate back
        if "/groups/" not in page.url:
            log.warning(f"Navigated away from group ({page.url}) — going back")
            await safe_goto(page, group_url)
            for _ in range(8):
                if await page.query_selector(article_selector):
                    break
                await asyncio.sleep(1)

        # Click See more buttons
        await click_see_more(page)

        # Extract posts from current view
        try:
            post_elements = await page.query_selector_all(article_selector)
            articles_this_scroll = len(post_elements)
            total_articles_seen = max(total_articles_seen, articles_this_scroll)
            extracted_this_scroll = 0
            skipped_no_data = 0
            skipped_seen = 0
            skipped_old = 0
            skipped_irrelevant = 0

            for el in post_elements:
                try:
                    post_data = await _extract_single_post(el, group_name, group_url)
                    if not post_data:
                        skipped_no_data += 1
                        continue

                    # Skip if already seen this poster in this session
                    profile = post_data.get("profile_url", "")
                    if profile in seen_profiles:
                        skipped_seen += 1
                        continue

                    # Skip if post is older than our cutoff
                    if last_post_date and post_data.get("post_date", "") <= last_post_date:
                        skipped_old += 1
                        continue

                    # Check relevance (group-aware: vendor groups accept all posts)
                    if not is_relevant_post(post_data.get("post_content", ""), group_name):
                        skipped_irrelevant += 1
                        continue

                    seen_profiles.add(profile)
                    posts.append(post_data)
                    extracted_this_scroll += 1

                    # Comment mining: if this is a "looking for" post,
                    # the replies contain vendors advertising themselves
                    if post_data.get("post_content") and classify_post_type(post_data["post_content"]) == "event_announcement":
                        comment_leads = await _mine_comments(el, group_name, group_url, seen_profiles)
                        for cl in comment_leads:
                            seen_profiles.add(cl["profile_url"])
                            posts.append(cl)
                            extracted_this_scroll += 1
                except Exception:
                    continue

            # Debug logging every few scrolls
            if scroll_count % 3 == 0 or extracted_this_scroll > 0:
                log.info(
                    f"[{group_name}] scroll {scroll_count}: "
                    f"{articles_this_scroll} articles, "
                    f"{extracted_this_scroll} new, "
                    f"{skipped_no_data} no-data, "
                    f"{skipped_seen} seen, "
                    f"{skipped_old} old, "
                    f"{skipped_irrelevant} irrelevant"
                )
        except Exception as e:
            log.warning(f"Error extracting posts: {e}")

        # Scroll down — try scrolling to the last article for better lazy-load trigger
        try:
            last_article = post_elements[-1] if post_elements else None
            if last_article:
                await last_article.scroll_into_view_if_needed()
                await asyncio.sleep(0.3)
        except Exception:
            pass
        await scroll_page(page)
        await move_mouse_randomly(page)

        # Small extra wait for Facebook to lazy-load content after scroll
        await asyncio.sleep(random.uniform(0.5, 1.5))

        # Check if we got new articles (more reliable than page height for feeds)
        current_article_count = len(await page.query_selector_all(article_selector))

        if current_article_count <= prev_article_count and extracted_this_scroll == 0:
            no_new_content_count += 1
            if no_new_content_count >= 4:
                log.info(f"No new content after {scroll_count} scrolls — done with {group_name}")
                break
        else:
            no_new_content_count = 0
        prev_article_count = current_article_count
        scroll_count += 1

    # ── Merge GraphQL-captured posts ──
    # GraphQL interception captures structured data from Facebook's internal API.
    # Merge any posts the DOM extraction missed.
    gql_added = 0
    if gql_capture:
        gql_posts = gql_capture.get_posts()
        for gp in gql_posts:
            author = gp.get("author_name", "")
            author_url = gp.get("author_url", "")
            text = gp.get("text", "")
            if not author or len(author) < 2:
                continue
            # Skip if we already have this author from DOM extraction
            if author_url and author_url in seen_profiles:
                continue
            if any(p.get("poster_name") == author for p in posts):
                continue
            # Check relevance
            if not is_relevant_post(text, group_name) and text:
                continue

            ts = gp.get("timestamp")
            if isinstance(ts, (int, float)) and ts > 0:
                from datetime import datetime, timezone
                post_date = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            else:
                post_date = datetime.now(timezone.utc).isoformat()

            # Extract contacts from text
            contacts = _extract_contacts_from_text(text) if text else {}

            post_data = {
                "poster_name": author,
                "profile_url": author_url or "",
                "post_content": text,
                "post_date": post_date,
                "group_name": group_name,
                "group_url": group_url,
                "image_count": 0,
                "post_links": [],
                "phone": contacts.get("phone", ""),
                "email": contacts.get("email", ""),
                "website": contacts.get("website", ""),
                "instagram": contacts.get("instagram", ""),
                "_source": "graphql",
            }
            if author_url:
                seen_profiles.add(author_url)
            posts.append(post_data)
            gql_added += 1

        if gql_added > 0:
            log.info(f"GraphQL interception added {gql_added} extra posts for {group_name}")

    dom_count = len(posts) - gql_added
    log.info(
        f"Extracted {len(posts)} total posts from {group_name} "
        f"(DOM: {dom_count}, GraphQL: {gql_added}, {scroll_count} scrolls)"
    )
    return posts


async def _extract_single_post(el, group_name: str, group_url: str):
    """Extract data from a single post element."""
    try:
        text = await el.inner_text()
    except Exception:
        return None

    if not text or len(text) < 20:
        return None

    # Extract poster name and profile URL
    poster_name = ""
    profile_url = ""
    skip_paths = ("/reel/", "/watch/", "/video/", "/photo/", "/stories/", "/events/", "/hashtag/", "/share/", "/permalink/")
    skip_labels = {"like", "comment", "share", "see more", "reply", "view more comments",
                   "send", "follow", "join", "add friend", "message", "close", "menu"}

    # Strategy A: Try to get name from the article's aria-label (e.g. "Post by John Doe")
    try:
        aria = await el.get_attribute("aria-label") or ""
        if aria:
            m = re.match(r"(?:Post by|Comment by|Story by)\s+(.+)", aria, re.IGNORECASE)
            if m:
                poster_name = m.group(1).strip()
    except Exception:
        pass

    # Strategy B: Link-based extraction with multiple selector patterns
    if not poster_name:
        try:
            selectors = [
                'h2 a[href]',           # FB groups often wrap poster name in h2
                'h3 a[href]',           # some layouts use h3
                'strong a[href]',       # name is often bold-linked
                'a[role="link"]',       # standard FB role attribute
                'a[data-hovercard]',    # FB adds hovercards to profile links
                'a[href*="/profile"]',  # direct profile links
                'a[href*="/people"]',   # people links
                'a[href*="/groups/"][href*="/user/"]',  # group member links
                'a[href]',             # any link as last resort
            ]
            for selector in selectors:
                if poster_name:
                    break
                try:
                    name_links = await el.query_selector_all(selector)
                except Exception:
                    continue
                for link in name_links[:8]:
                    try:
                        href = await link.get_attribute("href") or ""
                        if not href:
                            continue
                        # Normalize relative URLs
                        if href.startswith("/") and "facebook.com" not in href:
                            href = "https://www.facebook.com" + href
                        if "facebook.com" not in href:
                            continue
                        # Skip non-profile links
                        if any(sp in href for sp in skip_paths):
                            continue
                        if "/groups/" in href and "/user/" not in href and "/posts/" not in href:
                            continue
                        if "#" == href or href.endswith("facebook.com/") or href.endswith("facebook.com"):
                            continue

                        link_text = (await link.inner_text()).strip()
                        # If the link text is empty, check for a nested strong/span
                        if not link_text or len(link_text) < 2:
                            try:
                                inner = await link.query_selector("strong, span")
                                if inner:
                                    link_text = (await inner.inner_text()).strip()
                            except Exception:
                                pass
                        if not link_text or len(link_text) < 2:
                            continue
                        # Skip obvious non-name text
                        if link_text.lower() in skip_labels:
                            continue
                        # Skip if it looks like a timestamp (e.g. "2h", "3d", "Just now")
                        if re.match(r'^(\d+[hmdw]|just now|yesterday|\d+ min)$', link_text.lower()):
                            continue

                        poster_name = link_text
                        profile_url = href.split("?")[0]
                        break
                    except Exception:
                        continue
        except Exception:
            pass

    # Strategy C: If we still have no name, try the first <strong> text as the name
    # (in many FB layouts the poster name is the first bold text)
    if not poster_name:
        try:
            strong_els = await el.query_selector_all("strong")
            for strong in strong_els[:3]:
                strong_text = (await strong.inner_text()).strip()
                if strong_text and 2 < len(strong_text) < 60:
                    if strong_text.lower() not in skip_labels:
                        # Try to find a profile link near this strong element
                        parent_link = await strong.query_selector("xpath=ancestor::a")
                        if parent_link:
                            href = await parent_link.get_attribute("href") or ""
                            if href.startswith("/"):
                                href = "https://www.facebook.com" + href
                            if "facebook.com" in href and not any(sp in href for sp in skip_paths):
                                poster_name = strong_text
                                profile_url = href.split("?")[0]
                                break
                        else:
                            # No link — still use the name if it looks like a person/business
                            poster_name = strong_text
                            break
        except Exception:
            pass

    # Clean poster name — strip timestamps that Facebook renders adjacent to the name
    # e.g. "Nicole Trejo 2 hours ago" → "Nicole Trejo"
    if poster_name:
        poster_name = re.sub(
            r'\s*(?:\d+\s*(?:hours?|hrs?|minutes?|mins?|days?|weeks?|months?|yrs?|years?)\s*ago'
            r'|about\s+(?:an?\s+)?(?:hour|minute|day|week|month|year)\s*ago'
            r'|just\s+now|yesterday|tomorrow'
            r'|\d+[hmdw]'
            r'|(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:\s+at\s+\d{1,2}:\d{2}\s*[AP]M)?'
            r'|\d{1,2}/\d{1,2}/\d{2,4})'
            r'\s*$',
            '', poster_name, flags=re.IGNORECASE
        ).strip()
        # Also strip leading/trailing special chars and "·" separators
        poster_name = re.sub(r'[\s·•\-]+$', '', poster_name).strip()

    if not poster_name or len(poster_name) < 2:
        # Log first 100 chars of post text to help diagnose what's being missed
        snippet = text[:100].replace("\n", " ") if text else "(empty)"
        log.debug(f"[no-data] Could not extract poster name. Text starts: {snippet}")
        return None

    # Count images
    image_count = 0
    try:
        images = await el.query_selector_all('img[src*="scontent"]')
        image_count = len(images)
    except Exception:
        pass

    # Extract links from the post
    post_links = []
    try:
        links = await el.query_selector_all("a[href]")
        for link in links[:10]:
            href = await link.get_attribute("href") or ""
            if href and "http" in href and "facebook.com" not in href:
                post_links.append(href)
    except Exception:
        pass

    # Estimate post date from timestamps
    post_date = ""
    try:
        time_els = await el.query_selector_all("abbr, span[id*='jsc'] a")
        for te in time_els:
            time_text = (await te.inner_text()).strip()
            if any(x in time_text.lower() for x in ["h", "min", "just now", "yesterday", "d"]):
                post_date = _parse_relative_time(time_text)
                break
            aria = await te.get_attribute("aria-label") or ""
            if aria:
                post_date = aria
                break
    except Exception:
        pass

    if not post_date:
        post_date = datetime.now(timezone.utc).isoformat()

    # Extract contact info directly from post text
    # (vendors often include phone, email, website, IG in posts)
    contacts = _extract_contacts_from_text(text)

    return {
        "poster_name": poster_name,
        "profile_url": profile_url,
        "post_content": text[:5000],
        "post_date": post_date,
        "group_name": group_name,
        "group_url": group_url,
        "image_count": image_count,
        "post_links": post_links,
        "phone": contacts.get("phone", ""),
        "email": contacts.get("email", ""),
        "website": contacts.get("website", "") or (post_links[0] if post_links else ""),
        "instagram": contacts.get("instagram", ""),
    }


async def _mine_comments(post_el, group_name: str, group_url: str, seen_profiles: set) -> list:
    """Extract vendor leads from comments on a post (especially recommendation threads).
    When someone asks "looking for a tent rental," the replies are vendor gold."""
    leads = []
    try:
        # Try to expand comments (click "View more comments" etc.)
        for sel in [
            '[role="button"]:has-text("View more comments")',
            '[role="button"]:has-text("more comments")',
            '[role="button"]:has-text("View all")',
        ]:
            try:
                btn = await post_el.query_selector(sel)
                if btn and await btn.is_visible():
                    await btn.click()
                    await asyncio.sleep(1.5)
                    break
            except Exception:
                continue

        # Comments are nested articles or list items inside the post
        comment_selectors = [
            'ul li div[role="article"]',
            'div[aria-label*="comment"] div[role="article"]',
        ]
        for sel in comment_selectors:
            comments = await post_el.query_selector_all(sel)
            if comments:
                break
        else:
            comments = []

        for comment in comments[:15]:
            try:
                comment_text = await comment.inner_text()
                if not comment_text or len(comment_text) < 15:
                    continue

                # Extract commenter name/profile
                link = await comment.query_selector('a[role="link"], a[href*="facebook.com"]')
                if not link:
                    continue
                href = await link.get_attribute("href") or ""
                name = (await link.inner_text()).strip()
                if not name or len(name) < 2 or not href:
                    continue
                if "facebook.com" not in href and not href.startswith("/"):
                    continue
                if href.startswith("/"):
                    href = "https://www.facebook.com" + href

                profile_url = href.split("?")[0]
                if profile_url in seen_profiles:
                    continue
                # Skip group links
                if "/groups/" in profile_url:
                    continue

                contacts = _extract_contacts_from_text(comment_text)

                leads.append({
                    "poster_name": name,
                    "profile_url": profile_url,
                    "post_content": comment_text[:2000],
                    "post_date": datetime.now(timezone.utc).isoformat(),
                    "group_name": group_name,
                    "group_url": group_url,
                    "image_count": 0,
                    "post_links": [],
                    "phone": contacts.get("phone", ""),
                    "email": contacts.get("email", ""),
                    "website": contacts.get("website", ""),
                    "instagram": contacts.get("instagram", ""),
                })
            except Exception:
                continue

        if leads:
            log.info(f"Comment mining: found {len(leads)} vendor leads from replies in {group_name}")
    except Exception as e:
        log.debug(f"Comment mining error: {e}")

    return leads


def _parse_relative_time(text: str) -> str:
    """Convert relative time strings like '3h', '2d' to ISO format."""
    now = datetime.now(timezone.utc)
    lower = text.lower().strip()
    if "just now" in lower or "now" in lower:
        return now.isoformat()
    match = re.search(r'(\d+)\s*m', lower)
    if match:
        return (now - timedelta(minutes=int(match.group(1)))).isoformat()
    match = re.search(r'(\d+)\s*h', lower)
    if match:
        return (now - timedelta(hours=int(match.group(1)))).isoformat()
    match = re.search(r'(\d+)\s*d', lower)
    if match:
        return (now - timedelta(days=int(match.group(1)))).isoformat()
    if "yesterday" in lower:
        return (now - timedelta(days=1)).isoformat()
    return now.isoformat()


def _extract_contacts_from_text(text: str) -> dict:
    """Extract phone, email, website, and Instagram from post text.
    Vendors frequently include contact info directly in their posts."""
    contacts = {}

    # Phone numbers (US formats)
    phone_match = re.search(
        r'(?:(?:\+1|1)?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})', text
    )
    if phone_match:
        contacts["phone"] = phone_match.group(0).strip()

    # Email addresses
    email_match = re.search(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', text)
    if email_match:
        email = email_match.group(0)
        if not any(x in email.lower() for x in ["@facebook", "@fb.", "@meta.", "example.com", "@gmail.com"]):
            contacts["email"] = email

    # Website URLs (skip social media domains)
    url_match = re.search(
        r'(?:https?://|www\.)[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}[^\s,)]*', text
    )
    if url_match:
        url = url_match.group(0)
        skip_domains = ["facebook.com", "fb.com", "instagram.com", "tiktok.com",
                        "twitter.com", "x.com", "youtube.com", "l.facebook.com"]
        if not any(d in url.lower() for d in skip_domains):
            contacts["website"] = url

    # Instagram handles (vendors almost always include these)
    ig_patterns = [
        r'(?:ig|insta|instagram)[:\s]*@?([A-Za-z0-9_.]{3,30})',
        r'instagram\.com/([A-Za-z0-9_.]{3,30})',
        r'(?:follow\s+(?:us\s+)?(?:on\s+)?(?:ig|insta)\s*:?\s*@?)([A-Za-z0-9_.]{3,30})',
    ]
    for pattern in ig_patterns:
        ig_match = re.search(pattern, text, re.IGNORECASE)
        if ig_match:
            contacts["instagram"] = ig_match.group(1)
            break

    return contacts


def _is_business_name(name: str) -> bool:
    """Detect if a poster name looks like a business (not a person)."""
    patterns = [
        r'\b(?:LLC|Inc|Co\.?|Corp|Ltd|Enterprise|Services?|Rentals?|Studio|Productions?)\b',
        r'\b(?:Photography|Catering|Entertainment|Events?|Party|Wedding|Floral)\b',
        r'\b(?:Company|Agency|Group|Team|Collective|Designs?)\b',
        r"'s\s+\w+",  # Possessive: "Joe's Tents"
    ]
    for pattern in patterns:
        if re.search(pattern, name, re.IGNORECASE):
            return True
    return False


# ── Scrape helpers ───────────────────────────────────────────────────────────

async def scrape_groups(page, groups: list):
    """Scrape all groups for vendor leads.

    Hybrid extraction: GraphQL interception captures structured data from
    Facebook's internal API (primary), DOM parsing fills gaps (fallback).
    Resource blocking speeds up page loads by 50-70%.

    Speed strategy: collect post-level data from ALL groups first (fast — just
    scrolling), send each vendor to the API immediately from post data alone.
    Profile enrichment runs automatically after all groups are done.
    """
    session_start = datetime.now(timezone.utc)
    state = load_state()
    retry_pending()

    # Set up GraphQL interception — captures structured post data from FB's API
    gql_capture = GraphQLCapture()
    page.on("response", gql_capture.handle_response)
    log.info("GraphQL response interception active")

    # Set up resource blocking + stealth (images/video/fonts blocked for speed)
    await setup_page_optimizations(page)

    total_vendors = 0
    total_groups = len(groups)

    for gi, group in enumerate(groups, 1):
        elapsed = (datetime.now(timezone.utc) - session_start).total_seconds() / 60
        if elapsed >= MAX_SESSION_MINUTES:
            print(f"\n  Time limit reached ({MAX_SESSION_MINUTES} min) — stopping", flush=True)
            log.info(f"Session time limit reached ({MAX_SESSION_MINUTES} min)")
            break
        if is_stop_requested():
            log.info("Stop requested — ending scrape")
            break

        group_name = group.get("name", "Unknown")
        remaining_min = int(MAX_SESSION_MINUTES - elapsed)
        print(f"\n  [{gi}/{total_groups}] Scraping: {group_name}  ({remaining_min} min left)...", flush=True)
        posts = await extract_posts(page, group, state, gql_capture=gql_capture)
        print(f"    Found {len(posts)} relevant posts", flush=True)

        # Send each vendor to API immediately from post data (no profile visit)
        for post in posts:
            post_type = classify_post_type(post.get("post_content", ""))
            category = detect_category(post.get("post_content", ""))
            poster = post["poster_name"]

            # If the poster name looks like a business, use it as business_name
            biz_name = poster if _is_business_name(poster) else ""

            vendor = {
                "poster_name": poster,
                "profile_url": post["profile_url"],
                "business_name": biz_name,
                "phone": post.get("phone", ""),
                "email": post.get("email", ""),
                "website": post.get("website", ""),
                "city": "",
                "category": category,
                "about": "",
                "post_content": post.get("post_content", ""),
                "post_date": post.get("post_date", ""),
                "post_url": "",
                "group_name": post.get("group_name", ""),
                "group_url": post.get("group_url", ""),
                "post_type": post_type,
                "image_count": post.get("image_count", 0),
            }

            result = send_vendor(vendor)
            if result and result.get("action") == "created":
                total_vendors += 1
                extras = []
                if vendor["phone"]:
                    extras.append(f"📞 {vendor['phone']}")
                if vendor["website"]:
                    extras.append(f"🌐")
                if post.get("instagram"):
                    extras.append(f"📸 @{post['instagram']}")
                extra_str = f"  [{', '.join(extras)}]" if extras else ""
                print(f"    ✓ VENDOR #{total_vendors}: {poster} ({category}){extra_str}", flush=True)
                log.info(f"NEW VENDOR: {poster} ({category}) from {vendor['group_name']}")

        # Save progress after each group
        if posts:
            newest_date = max(p.get("post_date", "") for p in posts)
            if "groups" not in state:
                state["groups"] = {}
            state["groups"][group["url"]] = {
                "last_post_date": newest_date,
                "last_scraped": datetime.now(timezone.utc).isoformat(),
                "posts_scraped": state.get("groups", {}).get(group["url"], {}).get("posts_scraped", 0) + len(posts),
            }
            save_state(state)
            group["posts_scraped"] = state["groups"][group["url"]]["posts_scraped"]
            group["last_scraped"] = state["groups"][group["url"]]["last_scraped"]
            save_groups(groups)

        print(f"    Done — {total_vendors} vendors so far  |  break {int(GROUP_BREAK_MIN)}-{int(GROUP_BREAK_MAX)}s", flush=True)
        await random_delay(GROUP_BREAK_MIN, GROUP_BREAK_MAX)

    return total_vendors


# ── Full pipeline ────────────────────────────────────────────────────────────

async def run_pipeline():
    """Full pipeline: discover → geo-filter → join → scrape.
    Each stage checks what's already done and picks up where it left off.
    One browser session, one login check.
    """
    log.info("=" * 60)
    log.info("FULL PIPELINE STARTED")
    log.info("=" * 60)

    pw, context = await launch_browser()
    page = await context.new_page()

    try:
        if not await ensure_logged_in(page):
            return

        # ── Stage 1: Discovery ───────────────────────────────────────
        if DISCOVERED_FILE.exists():
            discovered = json.loads(DISCOVERED_FILE.read_text())
            print(f"\n[Stage 1] SKIP — {len(discovered)} groups already discovered")
            log.info(f"Stage 1 SKIP — loaded {len(discovered)} discovered groups from disk")
        else:
            print(f"\n[Stage 1] Discovering groups...")
            discovered = await discover_groups(page)
            if not discovered:
                log.warning("No groups discovered. Exiting.")
                print("No relevant groups found.")
                return
            DISCOVERED_FILE.write_text(json.dumps(discovered, indent=2))
            print(f"[Stage 1] DONE — discovered {len(discovered)} groups")
            log.info(f"Stage 1 DONE — discovered {len(discovered)} groups, saved to disk")

        if is_stop_requested():
            log.info("Stop requested after discovery")
            return

        # ── Stage 2: Geo-filter + Join ───────────────────────────────
        local_groups = filter_discovered_groups(discovered)
        if not local_groups:
            log.warning("No local groups after geo-filter. Exiting.")
            print("No local groups passed the geo-filter.")
            return

        existing = load_groups()
        existing_urls = {g["url"] for g in existing}
        new_groups = [g for g in local_groups if g["url"] not in existing_urls]

        if new_groups:
            print(f"\n[Stage 2] Joining {len(new_groups)} local groups...")
            print(f"  Discovered: {len(discovered)}")
            print(f"  After geo-filter: {len(local_groups)}")
            print(f"  Already joined: {len(local_groups) - len(new_groups)}")
            print(f"  To join now: {len(new_groups)}")

            joined = await join_groups(page, new_groups)

            if joined:
                for g in joined:
                    g["joined_at"] = datetime.now(timezone.utc).isoformat()
                existing.extend(joined)
                save_groups(existing)

            print(f"[Stage 2] DONE — joined {len(joined)}/{len(new_groups)} groups")
            log.info(f"Stage 2 DONE — joined {len(joined)} groups (total: {len(existing)})")
        else:
            print(f"\n[Stage 2] SKIP — all {len(local_groups)} local groups already joined")
            log.info(f"Stage 2 SKIP — all local groups already in groups.json")

        if is_stop_requested():
            log.info("Stop requested after joining")
            return

        # ── Stage 3: Scrape ──────────────────────────────────────────
        groups = load_groups()
        if not groups:
            log.warning("No groups to scrape.")
            print("No groups to scrape.")
            return

        print(f"\n[Stage 3] Scraping {len(groups)} groups for vendor leads...")
        total_vendors = await scrape_groups(page, groups)
        print(f"[Stage 3] DONE — {total_vendors} new vendors found")

        if is_stop_requested():
            log.info("Stop requested after scraping")
            return

        # ── Stage 4: Enrich ──────────────────────────────────────────
        if total_vendors > 0:
            print(f"\n[Stage 4] Enriching vendors missing contact info...")
            enriched = await _enrich_vendors(page)
            print(f"[Stage 4] DONE — {enriched} vendors enriched")
        else:
            print(f"\n[Stage 4] SKIP — no new vendors to enrich")

        print(f"\n{'=' * 60}")
        print(f"PIPELINE COMPLETE")
        print(f"  Groups joined: {len(existing)}")
        print(f"  New vendors found: {total_vendors}")
        print(f"{'=' * 60}")

    except Exception as e:
        log.error(f"Pipeline error: {e}", exc_info=True)
    finally:
        await context.close()
        await pw.stop()
        clear_stop_request()


# ── Standalone modes (for running individual stages) ─────────────────────────

async def run_discovery():
    """Standalone: discover groups only."""
    pw, context = await launch_browser()
    page = await context.new_page()
    try:
        if not await ensure_logged_in(page):
            return
        groups = await discover_groups(page)
        if groups:
            DISCOVERED_FILE.write_text(json.dumps(groups, indent=2))
            print(f"\nDiscovered {len(groups)} groups (saved to {DISCOVERED_FILE})")
            for i, g in enumerate(groups, 1):
                print(f"  {i}. {g['name']} ({g['member_count']} members)")
        else:
            print("No relevant groups found.")
    finally:
        await context.close()
        await pw.stop()


async def run_join():
    """Standalone: geo-filter + auto-join discovered groups."""
    if not DISCOVERED_FILE.exists():
        print("No discovered groups. Run: python scraper.py --discover")
        return
    discovered = json.loads(DISCOVERED_FILE.read_text())
    local_groups = filter_discovered_groups(discovered)
    if not local_groups:
        print("No local groups after geo-filter.")
        return
    existing = load_groups()
    existing_urls = {g["url"] for g in existing}
    new_groups = [g for g in local_groups if g["url"] not in existing_urls]
    if not new_groups:
        print(f"All {len(local_groups)} local groups already joined.")
        return

    print(f"Joining {len(new_groups)} local groups...")
    pw, context = await launch_browser()
    page = await context.new_page()
    try:
        if not await ensure_logged_in(page):
            return
        joined = await join_groups(page, new_groups)
        if joined:
            for g in joined:
                g["joined_at"] = datetime.now(timezone.utc).isoformat()
            existing.extend(joined)
            save_groups(existing)
        print(f"Joined {len(joined)}/{len(new_groups)} groups")
    except Exception as e:
        log.error(f"Join error: {e}", exc_info=True)
    finally:
        await context.close()
        await pw.stop()
        clear_stop_request()


async def run_scrape():
    """Standalone: scrape already-joined groups, then enrich vendors missing contact info."""
    groups = load_groups()
    if not groups:
        print("No groups to scrape. Run the full pipeline first: python scraper.py")
        return
    pw, context = await launch_browser()
    page = await context.new_page()
    try:
        if not await ensure_logged_in(page):
            return
        print(f"Scraping {len(groups)} groups for vendor leads...")
        total = await scrape_groups(page, groups)
        print(f"Scraping done — {total} new vendors found")

        # Auto-enrich: visit profiles for vendors missing contact info
        if not is_stop_requested() and total > 0:
            print("\n" + "=" * 60)
            print("AUTO-ENRICH: Visiting profiles for vendors missing contact info...")
            print("=" * 60)
            await _enrich_vendors(page)
    except Exception as e:
        log.error(f"Scrape error: {e}", exc_info=True)
    finally:
        await context.close()
        await pw.stop()
        clear_stop_request()


# ── Join helpers ─────────────────────────────────────────────────────────────

def filter_discovered_groups(discovered: list) -> list:
    """Apply geo-filter to discovered groups. Returns only local groups."""
    local = []
    excluded = []
    for g in discovered:
        if is_local_group(g.get("name", ""), g.get("description", "")):
            local.append(g)
        else:
            excluded.append(g)
    if excluded:
        log.info(f"Geo-filter excluded {len(excluded)} non-local groups:")
        for g in excluded:
            log.info(f"  - {g['name']}")
    log.info(f"Geo-filter kept {len(local)} local groups")
    return local


async def join_groups(page, groups_to_join: list) -> list:
    """Auto-join Facebook groups with human-like delays.
    Returns list of successfully joined groups.
    """
    joined = []
    skipped_questions = 0

    for i, group in enumerate(groups_to_join, 1):
        if is_stop_requested():
            log.info("Stop requested — halting joins")
            break

        name = group.get("name", "Unknown")
        url = group["url"]
        print(f"  [{i}/{len(groups_to_join)}] {name}...", flush=True)
        log.info(f"[{i}/{len(groups_to_join)}] Attempting to join: {name}")

        ok = await safe_goto(page, url, timeout=20000)
        if not ok:
            print(f"    Could not load page", flush=True)
            log.warning(f"Could not load group page: {name}")
            continue

        # Wait for page content to render
        await asyncio.sleep(2)

        # Dismiss any welcome/notification popup
        await dismiss_popup(page)

        # Check if already a member — try multiple selectors
        already_member = False
        for sel in [
            '[role="button"]:has-text("Joined")',
            '[role="button"]:has-text("Leave group")',
            '[aria-label*="Joined"]',
            'text="Joined"',
        ]:
            el = await page.query_selector(sel)
            if el:
                already_member = True
                break
        if already_member:
            print(f"    Already a member", flush=True)
            log.info(f"Already a member of: {name}")
            joined.append(group)
            await random_delay(JOIN_DELAY_MIN, JOIN_DELAY_MAX)
            continue

        # Look for Join button — try multiple selectors
        join_btn = None
        for sel in [
            '[role="button"]:has-text("Join group")',
            '[role="button"]:has-text("Join Group")',
            'a:has-text("Join group")',
            'a:has-text("Join Group")',
            '[aria-label*="Join"]',
            'text="Join group"',
            'text="Join Group"',
        ]:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                join_btn = el
                break
        if not join_btn:
            print(f"    No Join button found", flush=True)
            log.warning(f"No Join button found for: {name}")
            await random_delay(JOIN_DELAY_MIN, JOIN_DELAY_MAX)
            continue

        try:
            await join_btn.click()
            await minor_delay()

            # Check if a membership questions dialog appeared
            questions_dialog = await page.query_selector(
                'div[role="dialog"]:has-text("answer"), '
                'div[role="dialog"]:has-text("question"), '
                'div[role="dialog"] textarea'
            )
            if questions_dialog:
                cancel_btn = await page.query_selector(
                    'div[role="dialog"] div[role="button"]:has-text("Cancel"), '
                    'div[role="dialog"] [aria-label="Close"]'
                )
                if cancel_btn:
                    await cancel_btn.click()
                    await minor_delay()
                skipped_questions += 1
                print(f"    Skipped (requires questions)", flush=True)
                log.info(f"Skipped (has questions): {name}")
                await random_delay(JOIN_DELAY_MIN, JOIN_DELAY_MAX)
                continue

            joined.append(group)
            print(f"    Joined! ({len(joined)} total)", flush=True)
            log.info(f"Joined: {name}")

        except Exception as e:
            log.warning(f"Error joining {name}: {e}")

        # Human-like delay between joins
        await random_delay(JOIN_DELAY_MIN, JOIN_DELAY_MAX)

        # Batch break every N joins
        if i % JOIN_BATCH_SIZE == 0 and i < len(groups_to_join):
            print(f"\n  Batch break ({int(JOIN_BATCH_BREAK_MIN)}-{int(JOIN_BATCH_BREAK_MAX)}s)...", flush=True)
            log.info(f"Batch break after {i} groups (60-120s)...")
            await random_delay(JOIN_BATCH_BREAK_MIN, JOIN_BATCH_BREAK_MAX)

    if skipped_questions:
        log.info(f"Skipped {skipped_questions} groups that required answering questions")
    return joined


# ── Profile enrichment mode ──────────────────────────────────────────────────

async def _enrich_vendors(page) -> int:
    """Core enrichment logic — visit profiles for vendors missing contact info.
    Reuses an existing browser page. Returns count of enriched vendors."""
    import requests as req
    from config import NEXUS_BASE_URL

    url = f"{NEXUS_BASE_URL}/api/fb/prospects/scored?limit=500&sort=referral_score"
    try:
        r = req.get(url, timeout=15)
        prospects = r.json().get("prospects", [])
    except Exception as e:
        print(f"Could not fetch prospects from API: {e}")
        return 0

    # Filter to vendors missing key contact info
    needs_enrichment = [
        p for p in prospects
        if p.get("profile_url")
        and not p.get("phone")
        and not p.get("email")
        and not p.get("website")
        and p.get("status", "new") != "skip"
    ]

    if not needs_enrichment:
        print("All vendors already have contact info — nothing to enrich.")
        return 0

    print(f"\nEnriching {len(needs_enrichment)} vendors (visiting profiles for contact info)...")

    enriched = 0
    for i, prospect in enumerate(needs_enrichment, 1):
        if is_stop_requested():
            break

        name = prospect.get("poster_name", "?")
        profile_url = prospect["profile_url"]
        print(f"  [{i}/{len(needs_enrichment)}] {name}...", end=" ", flush=True)

        profile_data = await extract_profile_data(page, profile_url)

        problem = await check_for_blocking(page)
        if problem:
            log.error(f"BLOCKING DETECTED: {problem} — stopping enrichment")
            break

        # Build update payload with only non-empty fields
        updates = {}
        for field in ("business_name", "phone", "email", "website", "city", "about"):
            val = profile_data.get(field, "")
            if val:
                updates[field] = val

        if profile_data.get("category"):
            cat = detect_category(profile_data["category"])
            if cat != "other":
                updates["category"] = cat

        if updates:
            # PATCH the vendor via API
            patch_url = f"{NEXUS_BASE_URL}/api/fb/vendor/{prospect['id']}"
            try:
                pr = req.patch(patch_url, json=updates, timeout=10)
                if pr.status_code == 200:
                    enriched += 1
                    found = ", ".join(f"{k}={v[:30]}" for k, v in updates.items())
                    print(f"ENRICHED ({found})", flush=True)
                else:
                    print(f"API error {pr.status_code}", flush=True)
            except Exception as e:
                print(f"error: {e}", flush=True)
        else:
            print("no new info found", flush=True)

        # Human-like pause between profile visits
        await action_delay()
        if i % PROFILE_BATCH_SIZE == 0:
            print(f"\n  Batch break ({int(PROFILE_BATCH_BREAK_MIN)}-{int(PROFILE_BATCH_BREAK_MAX)}s)...", flush=True)
            await random_delay(PROFILE_BATCH_BREAK_MIN, PROFILE_BATCH_BREAK_MAX)

    print(f"\nEnrichment complete — {enriched}/{len(needs_enrichment)} vendors updated")
    return enriched


async def run_enrich():
    """Standalone: enrich existing vendors with profile data."""
    pw, context = await launch_browser()
    page = await context.new_page()
    try:
        if not await ensure_logged_in(page):
            return
        await _enrich_vendors(page)
    except Exception as e:
        log.error(f"Enrichment error: {e}", exc_info=True)
    finally:
        await context.close()
        await pw.stop()
        clear_stop_request()


# ── Schedule mode ────────────────────────────────────────────────────────────

async def run_scheduled():
    """Run the listener + daily schedule loop."""
    import schedule
    import time

    # Start HTTP listener for Telegram triggers
    start_listener()
    log.info(f"Listener running on port 7899")
    log.info(f"Daily scrape scheduled at {DAILY_SCHEDULE_HOUR}:00 AM Pacific")

    # Schedule daily run
    schedule.every().day.at(f"{DAILY_SCHEDULE_HOUR:02d}:00").do(
        lambda: asyncio.get_event_loop().create_task(_scheduled_scrape())
    )

    while True:
        schedule.run_pending()

        # Check for manual trigger
        if is_scrape_requested():
            clear_scrape_request()
            log.info("Manual scrape triggered")
            await run_scrape()

        await asyncio.sleep(5)


async def _scheduled_scrape():
    log.info("Daily scheduled scrape starting...")
    await run_scrape()


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Facebook Group Vendor Scraper")
    parser.add_argument("--discover", action="store_true", help="Discover new groups only")
    parser.add_argument("--join", action="store_true", help="Geo-filter + auto-join only")
    parser.add_argument("--scrape", action="store_true", help="Scrape already-joined groups only")
    parser.add_argument("--enrich", action="store_true", help="Enrich existing vendors with profile data (phone/email/website)")
    parser.add_argument("--schedule", action="store_true", help="Run on daily schedule + listen for triggers")
    args = parser.parse_args()

    if args.discover:
        asyncio.run(run_discovery())
    elif args.join:
        asyncio.run(run_join())
    elif args.scrape:
        asyncio.run(run_scrape())
    elif args.enrich:
        asyncio.run(run_enrich())
    elif args.schedule:
        asyncio.run(run_scheduled())
    else:
        asyncio.run(run_pipeline())


if __name__ == "__main__":
    main()
