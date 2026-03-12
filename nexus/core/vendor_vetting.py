"""
Vendor Vetting Pipeline — validates vendor data quality across 8 dimensions.

Checks (ordered cheapest to most expensive):
  1. Category relevance filter       (set lookup, 0ms)
  2. Email syntax validation         (regex, 0ms)
  3. Email MX record check           (dns.resolver, ~100ms)
  4. Website DNS resolution          (socket, ~50ms)
  5. Website HTTP liveness check     (httpx HEAD, ~500ms)
  6. Phone format validation         (phonenumbers, 0ms)
  7. Duplicate detection             (DB query, ~5ms)
  8. Business activity signals       (reuses #5 response, 0ms)

All checks use free tools only. No paid APIs.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import socket
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

log = logging.getLogger("vendor_vetting")
DB_PATH = Path.home() / ".nexus" / "memory.db"

# ── Caches (module-level, 24h TTL) ──────────────────────────────────────────

_mx_cache = {}      # type: Dict[str, Tuple[int, float]]  # domain -> (result, timestamp)
_dns_cache = {}     # type: Dict[str, Tuple[int, float]]   # hostname -> (result, timestamp)
_CACHE_TTL = 86400  # 24 hours


def _cache_get(cache, key):
    # type: (dict, str) -> Optional[int]
    entry = cache.get(key)
    if entry and (time.time() - entry[1]) < _CACHE_TTL:
        return entry[0]
    return None


def _cache_set(cache, key, value):
    # type: (dict, str, int) -> None
    cache[key] = (value, time.time())


# ── Reuse from vendor_enrichment ─────────────────────────────────────────────

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
    "invalid.com", "fake.com", "none.com",
}


def _get_excluded_categories():
    # type: () -> set
    try:
        from core.vendor_enrichment import EXCLUDED_CATEGORIES
        return EXCLUDED_CATEGORIES
    except ImportError:
        return set()


# ── Check 1: Category Relevance ─────────────────────────────────────────────

def check_category_relevance(category):
    # type: (str) -> Tuple[bool, str]
    if not category:
        return False, "no category"
    cat = category.strip().lower().replace(" ", "_")
    excluded = _get_excluded_categories()
    if cat in excluded:
        return False, "excluded category: %s" % cat
    return True, ""


# ── Check 2: Email Syntax ───────────────────────────────────────────────────

def check_email_syntax(email):
    # type: (str) -> Tuple[bool, str]
    if not email or not email.strip():
        return False, "no email"
    email = email.strip().lower()
    if not _EMAIL_RE.match(email):
        return False, "invalid email syntax"
    local, _, domain = email.partition("@")
    if local in _INVALID_PREFIXES:
        return False, "invalid prefix: %s" % local
    if domain in _INVALID_DOMAINS:
        return False, "invalid domain: %s" % domain
    return True, ""


# ── Check 3: Email MX Record ────────────────────────────────────────────────

def check_email_mx(email):
    # type: (str) -> Tuple[int, str]
    """Check if the email domain has valid MX records. Returns (1=valid, 0=invalid, -1=error)."""
    if not email or "@" not in email:
        return 0, "no email"
    domain = email.strip().lower().split("@")[1]

    cached = _cache_get(_mx_cache, domain)
    if cached is not None:
        return cached, "cached"

    try:
        import dns.resolver
        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        if answers:
            _cache_set(_mx_cache, domain, 1)
            return 1, "MX valid (%d records)" % len(answers)
        _cache_set(_mx_cache, domain, 0)
        return 0, "no MX records"
    except ImportError:
        return -1, "dnspython not installed"
    except dns.resolver.NXDOMAIN:
        _cache_set(_mx_cache, domain, 0)
        return 0, "domain does not exist"
    except dns.resolver.NoAnswer:
        _cache_set(_mx_cache, domain, 0)
        return 0, "no MX answer"
    except dns.resolver.NoNameservers:
        _cache_set(_mx_cache, domain, 0)
        return 0, "no nameservers"
    except Exception as e:
        return -1, "MX error: %s" % str(e)[:100]


# ── Check 4: Website DNS ────────────────────────────────────────────────────

def _extract_hostname(website):
    # type: (str) -> Optional[str]
    if not website:
        return None
    url = website.strip()
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        if host and "." in host:
            return host.lower()
    except Exception:
        pass
    return None


def check_website_dns(website):
    # type: (str) -> Tuple[int, str]
    """Check if website domain resolves via DNS. Returns (1=resolves, 0=no DNS, -1=error)."""
    hostname = _extract_hostname(website)
    if not hostname:
        return 0, "no website"

    cached = _cache_get(_dns_cache, hostname)
    if cached is not None:
        return cached, "cached"

    old_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(5)
        socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        _cache_set(_dns_cache, hostname, 1)
        return 1, "resolves"
    except socket.gaierror:
        _cache_set(_dns_cache, hostname, 0)
        return 0, "DNS lookup failed"
    except socket.timeout:
        return -1, "DNS timeout"
    except Exception as e:
        return -1, "DNS error: %s" % str(e)[:100]
    finally:
        socket.setdefaulttimeout(old_timeout)


# ── Check 5: Website HTTP Liveness ──────────────────────────────────────────

async def check_website_http(website):
    # type: (str) -> Tuple[int, int, str]
    """HTTP HEAD request to verify website is live. Returns (status, http_code, notes)."""
    hostname = _extract_hostname(website)
    if not hostname:
        return 0, 0, "no website"

    url = website.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        import httpx
        async with httpx.AsyncClient(
            timeout=5.0,
            follow_redirects=True,
            verify=False,  # Some vendors have bad SSL
        ) as client:
            resp = await client.head(url)
            code = resp.status_code

            if 200 <= code < 400:
                # Check for parked domain signals
                content_length = int(resp.headers.get("content-length", "99999"))
                server = resp.headers.get("server", "").lower()
                if content_length < 500 and "parking" in server:
                    return 0, code, "parked domain"
                return 1, code, "live"
            else:
                return 0, code, "HTTP %d" % code
    except ImportError:
        return -1, 0, "httpx not installed"
    except Exception as e:
        return 0, 0, "HTTP error: %s" % str(e)[:100]


# ── Check 6: Phone Format ───────────────────────────────────────────────────

def check_phone_format(phone):
    # type: (str) -> Tuple[int, str, str]
    """Validate US phone number. Returns (valid, formatted_e164, notes)."""
    if not phone or not phone.strip():
        return 0, "", "no phone"

    try:
        import phonenumbers
        parsed = phonenumbers.parse(phone.strip(), "US")
        if phonenumbers.is_valid_number(parsed):
            formatted = phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.E164
            )
            return 1, formatted, "valid"
        return 0, "", "invalid number"
    except ImportError:
        # Fallback: basic 10-digit US check
        digits = re.sub(r"\D", "", phone)
        if digits.startswith("1") and len(digits) == 11:
            digits = digits[1:]
        if len(digits) == 10:
            return 1, "+1" + digits, "regex fallback"
        return 0, "", "invalid format (no phonenumbers lib)"
    except Exception:
        return 0, "", "parse error"


# ── Check 7: Duplicate Detection ────────────────────────────────────────────

def check_duplicates(vendor_id, email, phone, db_path=None):
    # type: (int, str, str, Optional[str]) -> Tuple[bool, str]
    """Check for duplicate email/phone across vendors. Returns (has_dupes, detail)."""
    path = db_path or str(DB_PATH)
    dupes = []
    try:
        conn = sqlite3.connect(path, timeout=5)
        conn.row_factory = sqlite3.Row

        if email and "@" in email:
            rows = conn.execute(
                "SELECT id, name FROM vendors WHERE LOWER(email) = ? AND id != ? LIMIT 3",
                (email.strip().lower(), vendor_id),
            ).fetchall()
            if rows:
                dupes.append("email dupe with #%d (%s)" % (rows[0]["id"], rows[0]["name"][:30]))

        if phone and phone.strip():
            digits = re.sub(r"\D", "", phone)
            if len(digits) >= 10:
                # Match last 10 digits
                last10 = digits[-10:]
                rows = conn.execute(
                    "SELECT id, name, phone FROM vendors WHERE id != ? AND phone != '' LIMIT 5000",
                    (vendor_id,),
                ).fetchall()
                for r in rows:
                    other_digits = re.sub(r"\D", "", r["phone"] or "")
                    if other_digits.endswith(last10):
                        dupes.append("phone dupe with #%d (%s)" % (r["id"], r["name"][:30]))
                        break

        conn.close()
    except Exception as e:
        log.debug("Duplicate check error: %s", e)

    if dupes:
        return True, "; ".join(dupes)
    return False, ""


# ── Check 8: Business Activity Signals ──────────────────────────────────────

def check_business_signals(http_status, http_code, http_notes):
    # type: (int, int, str) -> Tuple[int, str]
    """Score business activity from HTTP response data. Returns (score 0-10, notes)."""
    score = 0
    notes = []

    if http_status == 1:
        score += 5  # Website is live
        if http_code == 200:
            score += 3  # Clean 200 (not redirect)
        elif 300 <= http_code < 400:
            score += 1  # Redirect (still alive)
        if "parked" not in http_notes:
            score += 2
        notes.append("live site")
    else:
        notes.append("no live site")

    return min(10, score), "; ".join(notes) if notes else ""


# ── Main Vetting Function ───────────────────────────────────────────────────

async def vet_vendor(vendor_id, db_path=None):
    # type: (int, Optional[str]) -> Dict[str, Any]
    """Run all 8 vetting checks on a single vendor. Writes results to DB."""
    path = db_path or str(DB_PATH)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
    if not row:
        conn.close()
        return {"error": "vendor not found", "vendor_id": vendor_id}

    vendor = dict(row)
    conn.close()

    score = 0
    notes_parts = []

    # Check 1: Category relevance (+20 or short-circuit fail)
    cat_ok, cat_reason = check_category_relevance(vendor.get("category", ""))
    if not cat_ok:
        _write_vetting_result(vendor_id, "failed", 0, "excluded: %s" % cat_reason,
                              path=path)
        return {"vendor_id": vendor_id, "status": "failed", "score": 0,
                "reason": cat_reason}
    score += 20

    # Check 2: Email syntax (+10)
    email = (vendor.get("email") or "").strip().lower()
    email_ok, email_reason = check_email_syntax(email)
    if email_ok:
        score += 10
    else:
        notes_parts.append("email: %s" % email_reason)

    # Check 3: Email MX (+20)
    mx_result = 0
    if email_ok:
        mx_result, mx_note = check_email_mx(email)
        if mx_result == 1:
            score += 20
        else:
            notes_parts.append("mx: %s" % mx_note)
    else:
        mx_result = 0

    # Check 4: Website DNS (+10)
    website = (vendor.get("website") or "").strip()
    ws_dns, ws_dns_note = check_website_dns(website)
    if ws_dns == 1:
        score += 10
    elif website:
        notes_parts.append("dns: %s" % ws_dns_note)

    # Check 5: Website HTTP (+15)
    ws_http = 0
    ws_http_code = 0
    ws_http_note = ""
    if ws_dns == 1:
        ws_http, ws_http_code, ws_http_note = await check_website_http(website)
        if ws_http == 1:
            score += 15
        else:
            notes_parts.append("http: %s" % ws_http_note)
    else:
        ws_http = 0

    # Check 6: Phone format (+10)
    phone = (vendor.get("phone") or "").strip()
    ph_valid, ph_formatted, ph_note = check_phone_format(phone)
    if ph_valid == 1:
        score += 10
    elif phone:
        notes_parts.append("phone: %s" % ph_note)

    # Check 7: Duplicate detection (+5)
    has_dupes, dupe_detail = check_duplicates(vendor_id, email, phone, db_path=path)
    if not has_dupes:
        score += 5
    else:
        notes_parts.append("dupes: %s" % dupe_detail)

    # Check 8: Business activity signals (+10)
    biz_score, biz_note = check_business_signals(ws_http, ws_http_code, ws_http_note)
    score += biz_score
    if biz_note:
        notes_parts.append("biz: %s" % biz_note)

    # Determine status
    if score >= 40:
        status = "vetted"
    elif score >= 20:
        status = "needs_review"
    else:
        status = "failed"

    vetting_notes = "; ".join(notes_parts)[:500]

    _write_vetting_result(
        vendor_id, status, score, vetting_notes,
        mx_valid=mx_result,
        website_status=ws_http if ws_dns == 1 else ws_dns,
        website_http_code=ws_http_code,
        phone_valid=ph_valid,
        phone_formatted=ph_formatted,
        path=path,
    )

    return {
        "vendor_id": vendor_id,
        "name": vendor.get("name", ""),
        "status": status,
        "score": score,
        "notes": vetting_notes,
    }


def _write_vetting_result(vendor_id, status, score, notes, mx_valid=-1,
                          website_status=-1, website_http_code=0,
                          phone_valid=-1, phone_formatted="", path=None):
    # type: (...) -> None
    db_path = path or str(DB_PATH)
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        conn.execute(
            "UPDATE vendors SET vetting_status=?, vetting_score=?, vetted_at=?, "
            "mx_valid=?, website_status=?, website_http_code=?, "
            "phone_valid=?, phone_formatted=?, vetting_notes=? "
            "WHERE id=?",
            (status, score, now, mx_valid, website_status, website_http_code,
             phone_valid, phone_formatted, notes[:500], vendor_id),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning("Failed to write vetting result for vendor %d: %s", vendor_id, e)


# ── Batch Vetting ────────────────────────────────────────────────────────────

async def vet_batch(batch_size=100, db_path=None):
    # type: (int, Optional[str]) -> Dict[str, Any]
    """Vet a batch of unvetted vendors, prioritizing campaign-eligible ones."""
    path = db_path or str(DB_PATH)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT id FROM vendors WHERE vetting_status = 'unvetted' "
        "ORDER BY campaign_eligible DESC, referral_score DESC "
        "LIMIT ?",
        (batch_size,),
    ).fetchall()
    conn.close()

    if not rows:
        return {"total": 0, "vetted": 0, "failed": 0, "needs_review": 0, "errors": 0}

    results = {"total": len(rows), "vetted": 0, "failed": 0, "needs_review": 0, "errors": 0}
    start = time.time()

    for row in rows:
        try:
            result = await vet_vendor(row["id"], db_path=path)
            status = result.get("status", "")
            if status == "vetted":
                results["vetted"] += 1
            elif status == "failed":
                results["failed"] += 1
            elif status == "needs_review":
                results["needs_review"] += 1
        except Exception as e:
            results["errors"] += 1
            log.warning("Vetting error for vendor %d: %s", row["id"], e)

        await asyncio.sleep(0.05)  # 50ms between vendors to avoid flooding

    results["elapsed_secs"] = round(time.time() - start, 1)
    log.info(
        "Vetting batch complete: %d total, %d vetted, %d failed, %d review, %d errors (%.1fs)",
        results["total"], results["vetted"], results["failed"],
        results["needs_review"], results["errors"], results["elapsed_secs"],
    )
    return results


# ── Quick Vet (Pre-Insert Gate) ──────────────────────────────────────────────

# ALLOWLIST: Only these categories are relevant for restroom trailer referrals
APPROVED_CATEGORIES = {
    "wedding_planner", "event_planner", "event_coordinator",
    "quinceanera_planner", "party_rental", "event_venue",
    "outdoor_venue", "banquet_hall", "catering_company", "catering",
    "wedding_venue", "construction_company",
    "country_club", "restaurant", "hotel", "community_center",
    "winery", "brewery", "corporate_event", "festival_organizer",
    # Event service providers — key referral partners
    "dj", "dj_entertainment", "photographer", "photography",
    "videography", "florist", "tent_rental", "lighting",
    "lighting_design", "bartending", "bartending_mobile_bar",
    "event_decorator", "decorator", "balloon_artist",
    "officiant", "hair_makeup", "wedding_cake",
    "photo_booth", "bounce_house", "furniture_rental",
    "food_truck", "coffee_cart", "dance_floor",
    "live_band", "construction", "quinceanera",
    # Broader matches for AI-generated category names
    "venue", "planner", "rental", "banquet", "hall",
}

# BLACKLISTED categories — permanently rejected, never re-added
BLACKLISTED_CATEGORIES = {
    "film_production", "production_company", "video_production",
    "tv_production", "production_studio",
}

# Fake phone last-4 patterns
_FAKE_PHONE_ENDINGS = {
    "0000", "1111", "2222", "3333", "4444",
    "5555", "6666", "7777", "8888", "9999",
    "1234", "4321", "2345", "3456", "4567", "5678", "6789",
}

# Known non-vendor businesses (lowercase)
_KNOWN_NON_VENDORS = {
    "the ups store", "ups store", "walmart", "target", "costco",
    "home depot", "lowes", "lowe's", "best buy", "walgreens",
    "cvs", "rite aid", "dollar tree", "dollar general",
    "starbucks", "mcdonalds", "mcdonald's", "subway",
    "taco bell", "burger king", "wendys", "wendy's",
    "usps", "fedex", "amazon", "david copperfield",
    "post office", "bank of america", "chase bank", "wells fargo",
}


def _log_rejection_to_db(vendor, reason):
    # type: (Dict[str, Any], str) -> None
    """Log rejected vendor to vendor_rejections table."""
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5)
        conn.execute(
            "INSERT INTO vendor_rejections "
            "(business_name, category, city, state, phone, email, website, rejection_reason, raw_data) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (vendor.get("name") or "")[:200],
                (vendor.get("category") or "")[:100],
                (vendor.get("city") or "")[:100],
                (vendor.get("state") or "")[:10],
                (vendor.get("phone") or "")[:30],
                (vendor.get("email") or "")[:200],
                (vendor.get("website") or "")[:500],
                reason[:500],
                json.dumps(vendor, default=str)[:2000],
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # Never let rejection logging break the gate


def _category_approved(cat):
    # type: (str) -> bool
    """Check if category matches any approved category (substring match)."""
    if cat in APPROVED_CATEGORIES:
        return True
    # Substring check: if any approved keyword is IN the category
    for approved in APPROVED_CATEGORIES:
        if approved in cat or cat in approved:
            return True
    return False


def quick_vet(vendor):
    # type: (Dict[str, Any]) -> Tuple[bool, str]
    """Synchronous, zero-network pre-insert gate. ALLOWLIST approach.

    Checks:
    1. Category must be in APPROVED_CATEGORIES (allowlist, not blocklist)
    2. Phone must not have obvious fabrication patterns
    3. Business name must not match known non-vendor entities
    """
    cat = (vendor.get("category") or "").strip().lower().replace(" ", "_")
    name = (vendor.get("name") or "").strip().lower()
    phone = (vendor.get("phone") or "").strip()

    # 0. Category BLACKLIST — permanently banned categories
    if cat in BLACKLISTED_CATEGORIES or any(bl in cat for bl in BLACKLISTED_CATEGORIES):
        reason = "blacklisted_category_production"
        _log_rejection_to_db(vendor, reason)
        return False, reason

    # 1. Category ALLOWLIST — reject if not approved
    if not cat or not _category_approved(cat):
        reason = "category not approved: %s" % cat
        _log_rejection_to_db(vendor, reason)
        return False, reason

    # 1b. Website sanity check — reject clearly dead domains.
    website = (vendor.get("website") or "").strip()
    if website:
        ws_dns, ws_note = check_website_dns(website)
        # Definitive DNS failure should not enter the outreach pool.
        if ws_dns == 0:
            reason = "website DNS failed: %s" % ws_note
            _log_rejection_to_db(vendor, reason)
            return False, reason

    # 2. Phone fake pattern check
    if phone:
        digits = re.sub(r"\D", "", phone)
        if len(digits) >= 4:
            last4 = digits[-4:]
            if last4 in _FAKE_PHONE_ENDINGS:
                reason = "fake phone pattern: %s (last4: %s)" % (phone, last4)
                _log_rejection_to_db(vendor, reason)
                return False, reason
        # Known fully-fake numbers
        if digits in ("0000000000", "1234567890", "8181234567", "5555555555"):
            reason = "known fake phone: %s" % phone
            _log_rejection_to_db(vendor, reason)
            return False, reason

    # 3. Business name sanity — known non-vendors
    if name and name in _KNOWN_NON_VENDORS:
        reason = "known non-vendor: %s" % name
        _log_rejection_to_db(vendor, reason)
        return False, reason

    # 4. Email domain check (keep from original — blocks mailinator etc.)
    email = (vendor.get("email") or "").strip().lower()
    if email:
        _, _, domain = email.partition("@")
        if domain in _INVALID_DOMAINS:
            reason = "invalid email domain: %s" % domain
            _log_rejection_to_db(vendor, reason)
            return False, reason

    return True, ""


# ── Stats ────────────────────────────────────────────────────────────────────

def get_vetting_stats(db_path=None):
    # type: (Optional[str]) -> Dict[str, Any]
    """Return aggregate vetting statistics."""
    path = db_path or str(DB_PATH)
    try:
        conn = sqlite3.connect(path, timeout=5)
        conn.row_factory = sqlite3.Row

        # Counts by status
        status_rows = conn.execute(
            "SELECT vetting_status, COUNT(*) as cnt FROM vendors GROUP BY vetting_status"
        ).fetchall()
        status_counts = {r["vetting_status"]: r["cnt"] for r in status_rows}

        # Average score for vetted vendors
        avg_row = conn.execute(
            "SELECT AVG(vetting_score) as avg_score FROM vendors WHERE vetting_status = 'vetted'"
        ).fetchone()
        avg_score = round(avg_row["avg_score"] or 0, 1)

        # MX breakdown
        mx_rows = conn.execute(
            "SELECT mx_valid, COUNT(*) as cnt FROM vendors WHERE vetting_status != 'unvetted' "
            "GROUP BY mx_valid"
        ).fetchall()
        mx_counts = {str(r["mx_valid"]): r["cnt"] for r in mx_rows}

        # Website breakdown
        ws_rows = conn.execute(
            "SELECT website_status, COUNT(*) as cnt FROM vendors WHERE vetting_status != 'unvetted' "
            "GROUP BY website_status"
        ).fetchall()
        ws_counts = {str(r["website_status"]): r["cnt"] for r in ws_rows}

        # Phone breakdown
        ph_rows = conn.execute(
            "SELECT phone_valid, COUNT(*) as cnt FROM vendors WHERE vetting_status != 'unvetted' "
            "GROUP BY phone_valid"
        ).fetchall()
        ph_counts = {str(r["phone_valid"]): r["cnt"] for r in ph_rows}

        total = sum(status_counts.values())
        conn.close()

        return {
            "total_vendors": total,
            "by_status": status_counts,
            "avg_vetted_score": avg_score,
            "mx_breakdown": mx_counts,
            "website_breakdown": ws_counts,
            "phone_breakdown": ph_counts,
            "cache_sizes": {
                "mx_cache": len(_mx_cache),
                "dns_cache": len(_dns_cache),
            },
        }
    except Exception as e:
        return {"error": str(e), "total_vendors": 0, "by_status": {}}
