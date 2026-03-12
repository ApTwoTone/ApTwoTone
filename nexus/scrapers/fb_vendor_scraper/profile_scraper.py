"""
FB Vendor Scraper — Profile data extraction.
Visits a poster's Facebook profile/page and extracts publicly visible info.
READ ONLY — never interacts with the profile.
"""
import re
import logging
from playwright.async_api import Page

from browser_utils import minor_delay, action_delay, safe_goto

log = logging.getLogger("fb_scraper")


async def extract_profile_data(page: Page, profile_url: str) -> dict:
    """Visit a profile URL and extract publicly visible business info.
    Returns dict with: business_name, phone, email, website, city, category, about."""
    result = {
        "business_name": "",
        "phone": "",
        "email": "",
        "website": "",
        "city": "",
        "category": "",
        "about": "",
    }

    if not profile_url or "facebook.com" not in profile_url:
        return result

    ok = await safe_goto(page, profile_url)
    if not ok:
        return result

    await minor_delay()

    try:
        page_text = await page.inner_text("body")
    except Exception:
        return result

    # Extract page/profile name (typically the h1 or large heading)
    try:
        heading = await page.query_selector("h1")
        if heading:
            result["business_name"] = (await heading.inner_text()).strip()
    except Exception:
        pass

    # Extract phone numbers from visible text
    phone_match = re.search(
        r'(?:(?:\+1|1)?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})', page_text
    )
    if phone_match:
        result["phone"] = phone_match.group(0).strip()

    # Extract email addresses
    email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', page_text)
    if email_match:
        email = email_match.group(0)
        # Filter out common false positives
        if not any(x in email.lower() for x in ["@facebook", "@fb.", "@meta.", "example.com"]):
            result["email"] = email

    # Extract website URLs from the page
    try:
        links = await page.query_selector_all('a[href]')
        for link in links:
            href = await link.get_attribute("href") or ""
            link_text = (await link.inner_text()).strip() if await link.is_visible() else ""
            # Look for external website links (not facebook/instagram/etc.)
            if href and "http" in href:
                if not any(domain in href.lower() for domain in [
                    "facebook.com", "fb.com", "instagram.com", "twitter.com",
                    "x.com", "youtube.com", "tiktok.com", "l.facebook.com"
                ]):
                    result["website"] = href
                    break
            # Also check for website text patterns
            if link_text and re.match(r'^https?://|^www\.', link_text):
                result["website"] = link_text
                break
    except Exception:
        pass

    # Extract location/city
    location_patterns = [
        r'(?:Located in|Based in|Serving)\s+([A-Z][a-zA-Z\s]+(?:,\s*[A-Z]{2})?)',
        r'(?:Los Angeles|Calabasas|Malibu|Glendale|Burbank|Pasadena|'
        r'Santa Monica|Beverly Hills|Encino|Tarzana|Woodland Hills|'
        r'Sherman Oaks|Studio City|Northridge|Van Nuys|Ventura|Oxnard|'
        r'Thousand Oaks|Simi Valley|San Fernando|Reseda|Canoga Park)',
    ]
    for pattern in location_patterns:
        match = re.search(pattern, page_text)
        if match:
            result["city"] = match.group(0).strip()
            break

    # Extract category from page info (Facebook Pages show category)
    try:
        category_els = await page.query_selector_all(
            '[data-pagelet="ProfileTilesFeed_0"] span, '
            'a[href*="/pages/category/"]'
        )
        for el in category_els:
            text = (await el.inner_text()).strip()
            if text and len(text) < 50:
                result["category"] = text
                break
    except Exception:
        pass

    # Extract About section
    try:
        # Try to find the about/intro section
        about_selectors = [
            'div[data-pagelet="ProfileTilesFeed_0"]',
            'div:has-text("About") + div',
        ]
        for sel in about_selectors:
            el = await page.query_selector(sel)
            if el:
                about_text = (await el.inner_text()).strip()
                if about_text and len(about_text) > 10:
                    result["about"] = about_text[:1000]
                    break
    except Exception:
        pass

    # Check for linked Facebook Business Page
    await _try_business_page(page, result)

    return result


async def _try_business_page(page: Page, result: dict):
    """If the profile links to a Facebook Business Page, visit it for more info."""
    try:
        # Look for "See [Page Name]" or page links in the intro section
        page_links = await page.query_selector_all('a[href*="/pages/"], a[href*="facebook.com/"][role="link"]')
        for link in page_links[:3]:  # Check first 3 potential page links
            href = await link.get_attribute("href") or ""
            if "/pages/" in href or (
                "facebook.com/" in href
                and "/profile" not in href
                and "/groups/" not in href
            ):
                link_text = (await link.inner_text()).strip()
                if link_text and len(link_text) > 2 and len(link_text) < 100:
                    # Visit the business page
                    ok = await safe_goto(page, href)
                    if not ok:
                        return
                    await minor_delay()

                    try:
                        bp_text = await page.inner_text("body")
                    except Exception:
                        return

                    # Update result with any new info found
                    if not result["business_name"]:
                        try:
                            h1 = await page.query_selector("h1")
                            if h1:
                                result["business_name"] = (await h1.inner_text()).strip()
                        except Exception:
                            pass

                    if not result["phone"]:
                        pm = re.search(
                            r'(?:(?:\+1|1)?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})',
                            bp_text,
                        )
                        if pm:
                            result["phone"] = pm.group(0).strip()

                    if not result["email"]:
                        em = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', bp_text)
                        if em and not any(
                            x in em.group(0).lower()
                            for x in ["@facebook", "@fb.", "@meta.", "example.com"]
                        ):
                            result["email"] = em.group(0)

                    return  # Only visit one page link
    except Exception:
        pass
