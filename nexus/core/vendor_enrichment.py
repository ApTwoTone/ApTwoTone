"""
Vendor Enrichment Pipeline — Scores, filters, and ranks 173K vendors
for cold email campaigns. Pure Python, zero API calls.

Reuses CATEGORY_SCORES from vendor_scoring.py for referral scoring.
Adds: distance tier (city-based), email validation, campaign eligibility.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("vendor_enrichment")

DB_PATH = Path.home() / ".nexus" / "memory.db"

# ── Category Tiers ────────────────────────────────────────────────────────────

# Categories worth emailing (mapped to referral score bonus)
CATEGORY_TIERS = {
    # Tier 1: Event logistics — directly book portable bathrooms
    1: [
        "event_planner", "wedding_planner", "quinceanera_planner",
        "tent_rental", "party_rental", "setup_crew",
    ],
    # Tier 2: Equipment/service companies — natural cross-sell
    2: [
        "catering", "caterer", "event_decorator", "decorator",
        "furniture_rental", "bounce_house", "corporate_event_planner",
        "festival_organizer", "generator_rental",
    ],
    # Tier 3: Venue partners — have events, may need restroom support
    3: [
        "wedding_venue", "quinceanera_venue", "farm_ranch_venue",
        "winery_venue", "winery", "church", "community_center",
        "hotel_venue", "venue", "backyard_party",
    ],
    # Tier 4: Peripheral — present at events, can refer
    4: [
        "dj", "photography", "photographer", "videography",
        "photo_booth", "lighting_design", "lighting", "florist",
        "bartending_mobile_bar", "bartending", "food_truck",
        "coffee_cart", "stage_rental", "dance_floor",
    ],
}

# Categories to NEVER email — irrelevant or competitors
EXCLUDED_CATEGORIES = {
    "construction", "bridal_shop", "wedding_cake", "film_production",
    "security_service", "beauty_services", "character_company",
    "porta_potty_competitor", "bathroom_rental",
    "face_painter", "dessert_catering", "wedding_invitation",
    "limo_service", "live_band", "magician", "valet_service",
    "valet", "kids_party", "officiant", "balloon_artist",
    "quinceanera_dress", "airbnb_property", "team_building",
    # Added 2026-03-08: junk categories that slipped through
    "stage_rental", "coffee_cart", "community_center", "restaurant",
    "jewelry", "grip", "lighting_design", "lighting_equipment",
}

# Build reverse lookup: category → tier number
_CAT_TO_TIER = {}  # type: Dict[str, int]
for _tier, _cats in CATEGORY_TIERS.items():
    for _cat in _cats:
        _CAT_TO_TIER[_cat] = _tier

# ── City → Distance Tier ─────────────────────────────────────────────────────

CITY_TIERS = {
    # Tier 1: 0-10 miles from San Fernando, CA 91340
    1: {
        "san fernando", "sylmar", "pacoima", "arleta", "sun valley",
        "north hollywood", "van nuys", "reseda", "northridge",
        "granada hills", "mission hills", "panorama city",
        "lake view terrace", "lakeview terrace", "sunland", "tujunga",
        "shadow hills",
    },
    # Tier 2: 10-20 miles
    2: {
        "chatsworth", "canoga park", "woodland hills", "tarzana", "encino",
        "sherman oaks", "studio city", "burbank", "glendale", "pasadena",
        "la crescenta", "montrose", "santa clarita", "newhall", "valencia",
        "canyon country", "la canada flintridge", "la canada", "eagle rock",
        "highland park", "altadena", "calabasas", "west hills",
        "porter ranch", "simi valley", "winnetka", "lake balboa",
        "toluca lake",
    },
    # Tier 3: 20+ miles (still in Greater LA / Ventura)
    3: {
        "los angeles", "la", "hollywood", "west hollywood",
        "beverly hills", "santa monica", "culver city", "venice",
        "marina del rey", "malibu", "thousand oaks", "moorpark",
        "camarillo", "oxnard", "ventura", "long beach", "torrance",
        "gardena", "inglewood", "downey", "whittier", "pomona",
        "west covina", "arcadia", "monrovia", "azusa", "glendora",
        "claremont", "rancho cucamonga", "ontario", "upland",
        "covina", "el monte", "alhambra", "monterey park",
        "san gabriel", "south pasadena", "la verne", "diamond bar",
        "rowland heights", "hacienda heights", "fullerton", "anaheim",
        "costa mesa", "irvine", "riverside", "san bernardino",
        "palmdale", "lancaster",
    },
}

# Build reverse lookup: city → tier
_CITY_TO_TIER = {}  # type: Dict[str, int]
for _tier, _cities in CITY_TIERS.items():
    for _city in _cities:
        _CITY_TO_TIER[_city] = _tier

# ── Referral Score (adapted from vendor_scoring.py) ───────────────────────────

# Reuse the scoring from vendor_scoring but adapted for vendors table columns
from core.vendor_scoring import CATEGORY_SCORES

def _compute_score(category, city, phone, email, website, rating, review_count):
    # type: (str, str, str, str, str, float, int) -> Tuple[int, int]
    """Compute referral_score (0-100) and activity_level (1-5) for a vendor."""
    cat_lower = (category or "").lower().replace(" ", "_")

    # Base score from category
    score = CATEGORY_SCORES.get(cat_lower, 30)

    # Contact info bonuses
    if phone and phone.strip():
        score += 3
    if email and email.strip() and "@" in email:
        score += 3
    if website and website.strip() and "." in website:
        score += 2

    # Local area bonus
    city_lower = (city or "").lower().strip()
    if city_lower in _CITY_TO_TIER and _CITY_TO_TIER[city_lower] <= 2:
        score += 5

    # Rating bonus
    if rating and rating >= 4.5:
        score += 3
    elif rating and rating >= 4.0:
        score += 1

    # Review count bonus
    if review_count and review_count >= 50:
        score += 3
    elif review_count and review_count >= 20:
        score += 1

    score = max(0, min(100, score))

    # Activity level (1-5 based on available data)
    activity = 1
    if phone and phone.strip():
        activity += 1
    if email and email.strip() and "@" in email:
        activity += 1
    if website and website.strip():
        activity += 1
    if review_count and review_count >= 10:
        activity += 1
    activity = min(5, activity)

    return score, activity


# ── Email Validation ──────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)

_INVALID_PREFIXES = {
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "mailer-daemon", "postmaster", "abuse", "admin",
}

_INVALID_DOMAINS = {
    "example.com", "test.com", "localhost", "mailinator.com",
    "guerrillamail.com", "tempmail.com", "throwaway.email",
}


def _validate_email(email):
    # type: (str) -> bool
    """Basic email syntax validation. No API calls."""
    if not email or not email.strip():
        return False
    email = email.strip().lower()
    if not _EMAIL_RE.match(email):
        return False
    local, _, domain = email.partition("@")
    digit_count = sum(ch.isdigit() for ch in local)
    if digit_count >= 7 and re.search(r"(info|sales|contact|office|events?|weddings?|bookings?)$", local):
        return False
    if len(local) > 48:
        return False
    if local in _INVALID_PREFIXES:
        return False
    if domain in _INVALID_DOMAINS:
        return False
    return True


# ── Main Pipeline ─────────────────────────────────────────────────────────────

def score_all_vendors(db_path=None):
    # type: (Optional[str]) -> Dict
    """Score and filter all vendors in batches. Returns summary stats."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")

    start = time.time()
    total = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
    log.info("Starting vendor enrichment for %d vendors", total)

    batch_size = 5000
    processed = 0
    eligible_count = 0
    tier_counts = {0: 0, 1: 0, 2: 0, 3: 0}
    cat_counts = {}  # type: Dict[str, int]
    seen_emails = set()  # type: set
    dupes_found = 0

    offset = 0
    while offset < total:
        rows = conn.execute(
            "SELECT id, name, category, city, phone, email, website, "
            "rating, review_count, vetting_status, outreach_status FROM vendors LIMIT ? OFFSET ?",
            (batch_size, offset),
        ).fetchall()

        if not rows:
            break

        updates = []  # type: List[Tuple]
        for row in rows:
            vid = row["id"]
            cat = (row["category"] or "").lower().replace(" ", "_")
            city = (row["city"] or "").lower().strip()
            email = (row["email"] or "").strip().lower()
            outreach_status = (row["outreach_status"] or "").strip().lower()

            # Score
            score, activity = _compute_score(
                cat, city, row["phone"], email,
                row["website"], row["rating"], row["review_count"],
            )

            # Distance tier
            dist_tier = _CITY_TO_TIER.get(city, 0)
            tier_counts[dist_tier] = tier_counts.get(dist_tier, 0) + 1

            # Email validation + dedup
            email_valid = 0
            if _validate_email(email):
                if email not in seen_emails:
                    email_valid = 1
                    seen_emails.add(email)
                else:
                    dupes_found += 1
            else:
                email_valid = 0

            # Campaign eligibility
            eligible = (
                cat not in EXCLUDED_CATEGORIES
                and email_valid == 1
                and dist_tier in (1, 2, 3)
                and score >= 30
            )
            campaign_eligible = 1 if eligible else 0

            if outreach_status == "bounced":
                email_valid = 0
                campaign_eligible = 0
            elif outreach_status in {"unsubscribed", "do_not_contact"}:
                campaign_eligible = 0

            # Block vendors that failed vetting from campaigns
            try:
                if row["vetting_status"] == "failed":
                    campaign_eligible = 0
            except (IndexError, KeyError):
                pass
            if eligible and campaign_eligible:
                eligible_count += 1
                cat_counts[cat] = cat_counts.get(cat, 0) + 1

            updates.append((score, activity, dist_tier, email_valid,
                            campaign_eligible, vid))

        # Batch update
        conn.executemany(
            "UPDATE vendors SET referral_score=?, activity_level=?, "
            "distance_tier=?, email_valid=?, campaign_eligible=? "
            "WHERE id=?",
            updates,
        )
        conn.commit()

        offset += batch_size
        processed += len(rows)
        if processed % 20000 == 0:
            log.info("Enrichment progress: %d/%d (%.0f%%)",
                     processed, total, processed / total * 100)

    elapsed = time.time() - start

    # Top categories
    top_cats = sorted(cat_counts.items(), key=lambda x: -x[1])[:15]

    summary = {
        "total_vendors": total,
        "processed": processed,
        "eligible": eligible_count,
        "excluded": processed - eligible_count,
        "duplicate_emails": dupes_found,
        "tier_1": tier_counts.get(1, 0),
        "tier_2": tier_counts.get(2, 0),
        "tier_3": tier_counts.get(3, 0),
        "out_of_area": tier_counts.get(0, 0),
        "top_categories": top_cats,
        "elapsed_seconds": round(elapsed, 1),
    }

    log.info(
        "Enrichment complete: %d vendors, %d eligible (%.1f%%), %.1fs",
        total, eligible_count, eligible_count / max(total, 1) * 100, elapsed,
    )

    conn.close()
    return summary


def get_eligible_vendors(limit=50, min_score=30, tiers=None, categories=None, db_path=None):
    # type: (int, int, Optional[List[int]], Optional[List[str]], Optional[str]) -> List[Dict]
    """Query top eligible vendors sorted by referral score."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row

    query = (
        "SELECT id, name, email, phone, city, category, referral_score, "
        "distance_tier, activity_level, website, rating, review_count "
        "FROM vendors WHERE campaign_eligible = 1 AND referral_score >= ?"
    )
    params = [min_score]  # type: List

    if tiers:
        placeholders = ",".join("?" * len(tiers))
        query += " AND distance_tier IN (%s)" % placeholders
        params.extend(tiers)

    if categories:
        placeholders = ",".join("?" * len(categories))
        query += " AND category IN (%s)" % placeholders
        params.extend(categories)

    query += " ORDER BY referral_score DESC, distance_tier ASC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_enrichment_stats(db_path=None):
    # type: (Optional[str],) -> Dict
    """Quick stats on enrichment status."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
    eligible = conn.execute(
        "SELECT COUNT(*) FROM vendors WHERE campaign_eligible = 1"
    ).fetchone()[0]
    scored = conn.execute(
        "SELECT COUNT(*) FROM vendors WHERE referral_score > 0"
    ).fetchone()[0]

    tier_rows = conn.execute(
        "SELECT distance_tier, COUNT(*) as cnt FROM vendors "
        "WHERE campaign_eligible = 1 GROUP BY distance_tier ORDER BY distance_tier"
    ).fetchall()

    cat_rows = conn.execute(
        "SELECT category, COUNT(*) as cnt FROM vendors "
        "WHERE campaign_eligible = 1 GROUP BY category ORDER BY cnt DESC LIMIT 10"
    ).fetchall()

    conn.close()
    return {
        "total": total,
        "scored": scored,
        "eligible": eligible,
        "tiers": {r["distance_tier"]: r["cnt"] for r in tier_rows},
        "top_categories": [(r["category"], r["cnt"]) for r in cat_rows],
    }
