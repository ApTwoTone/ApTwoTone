"""
Competitor Monitoring System — Zoar Bathroom Rentals

Tracks competitors in the SoCal luxury portable restroom market:
  - Competitor profiles in SQLite (competitors, alerts, snapshots)
  - Scrapes websites for pricing / service changes
  - Alerts on price changes, new competitors, review shifts
  - Market intel: pricing position, ratings, overlap analysis
  - Telegram: /competitors, /competitor add, /alerts
  - Weekly scan scheduler (Monday 9 AM PT)
  - Google search discovery for new local competitors

Config: ~/.nexus/config.json
Database: ~/.nexus/memory.db
"""
from __future__ import annotations
import asyncio, json, re, sqlite3, traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import httpx

# ── Constants ────────────────────────────────────────────────────────────────

DB_PATH = Path.home() / ".nexus" / "memory.db"
PT = ZoneInfo("America/Los_Angeles")

ZOAR_MIN_PRICE, ZOAR_MAX_PRICE = 1000.0, 1100.0
ZOAR_RATING, ZOAR_REVIEW_COUNT = 5.0, 12
ZOAR_SPECIALTIES = [
    "4-stall luxury trailer", "flushable toilets", "running water",
    "LED lighting", "mirrors", "AC", "Bluetooth speaker",
    "delivery + setup + pickup included",
]

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

ALERT_ICONS = {
    "price_change": "\U0001f4b0", "new_competitor": "\U0001f195",
    "review_change": "\u2b50", "new_service": "\U0001f527",
}

# ── Seed data ────────────────────────────────────────────────────────────────

SEED_COMPETITORS = [
    # (name, website, service_area, specialties_json, notes)
    ("Royal Restrooms",  "https://royalrestrooms.com", "National (SoCal presence)", '["luxury trailers","national fleet","weddings"]', "Major national, premium"),
    ("Luxury Loo",       "https://luxuryloo.com",      "Los Angeles area",          '["luxury trailers","LA events","weddings"]',      "Direct LA competitor"),
    ("VIP To Go",        "https://viptogo.com",        "National",                  '["luxury trailers","restroom trailers"]',         "National, wide range"),
    ("Comfort House",    "https://comforthouse.com",   "Southern California",       '["portable restrooms","SoCal events"]',           "SoCal focused"),
    ("Porta Potty Dogs", "https://portapottydogs.com", "Los Angeles",               '["porta potties","budget rentals"]',              "Budget segment"),
    ("John To Go",       "https://johntogo.com",       "National",                  '["portable restrooms","luxury trailers"]',        "National, broad line"),
    ("A Royal Flush",    "https://aroyalflush.com",    "Southern California",       '["luxury trailers","portable restrooms"]',        "SoCal luxury"),
    ("CALLAHEAD",        "https://callahead.com",      "National (NY-based)",       '["luxury portable restrooms"]',                   "Premium, East Coast"),
    ("Privy",            "https://privy.com",          "National",                  '["luxury restroom trailers"]',                    "Luxury segment"),
]

# ── Patterns ─────────────────────────────────────────────────────────────────

PRICE_PATTERNS = [
    re.compile(r"\$\s?([\d,]+(?:\.\d{2})?)"),
    re.compile(r"(?:starting\s+at|from|as\s+low\s+as)\s+\$?\s?([\d,]+)", re.I),
    re.compile(r"\$\s?([\d,]+)\s*(?:/\s*day|per\s+day)", re.I),
    re.compile(r"(?:rental\s+fee|rental\s+rate|price)[:\s]+\$?\s?([\d,]+)", re.I),
]
PHONE_PATTERN = re.compile(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")

SOCAL_CITIES = [
    "Los Angeles", "LA", "San Fernando Valley", "Santa Clarita", "Ventura",
    "Oxnard", "Santa Monica", "Malibu", "Pasadena", "Burbank", "Glendale",
    "Long Beach", "Thousand Oaks", "Simi Valley", "Calabasas", "Encino",
    "Sherman Oaks", "Tarzana", "Woodland Hills", "Orange County", "San Diego",
    "Inland Empire", "Riverside", "Palm Springs", "Temecula", "San Bernardino",
]

SERVICE_KEYWORDS = [
    "luxury trailer", "restroom trailer", "portable restroom", "porta potty",
    "wedding", "corporate event", "construction", "ADA accessible", "handicap",
    "shower trailer", "laundry trailer", "VIP restroom", "climate controlled",
    "air conditioned", "running water", "flushable", "hand washing", "sink",
]

# ── Helpers ──────────────────────────────────────────────────────────────────

def _now_pt() -> datetime:  return datetime.now(PT)
def _now_str() -> str:      return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
def _today_str() -> str:    return _now_pt().strftime("%Y-%m-%d")
def _log(msg: str):         print(f"[Competitor] {msg}")

def _load_config() -> dict:
    p = Path.home() / ".nexus" / "config.json"
    if p.exists():
        try: return json.loads(p.read_text())
        except Exception: pass
    return {}

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn

def _icon(alert_type: str) -> str:
    return ALERT_ICONS.get(alert_type, "\U0001f4cc")


# ═════════════════════════════════════════════════════════════════════════════
# DATABASE INIT
# ═════════════════════════════════════════════════════════════════════════════

def init_competitor_db():
    """Create competitor tables and seed known competitors if empty."""
    conn = _get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS competitors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, website TEXT DEFAULT '', phone TEXT DEFAULT '',
        service_area TEXT DEFAULT '', min_price REAL DEFAULT 0,
        max_price REAL DEFAULT 0, specialties TEXT DEFAULT '[]',
        review_rating REAL DEFAULT 0, review_count INTEGER DEFAULT 0,
        notes TEXT DEFAULT '', last_checked TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS competitor_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        competitor_id INTEGER, alert_type TEXT NOT NULL,
        details TEXT DEFAULT '', seen INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (competitor_id) REFERENCES competitors(id)
    );
    CREATE TABLE IF NOT EXISTS competitor_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        competitor_id INTEGER, data_json TEXT DEFAULT '{}',
        snapshot_date TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (competitor_id) REFERENCES competitors(id)
    );
    CREATE INDEX IF NOT EXISTS idx_comp_name ON competitors(name);
    CREATE INDEX IF NOT EXISTS idx_comp_alerts_seen ON competitor_alerts(seen);
    CREATE INDEX IF NOT EXISTS idx_comp_snap_cid ON competitor_snapshots(competitor_id);
    """)
    conn.commit()

    if conn.execute("SELECT COUNT(*) FROM competitors").fetchone()[0] == 0:
        _log("Seeding known competitors...")
        conn.executemany(
            "INSERT INTO competitors (name,website,service_area,specialties,notes) "
            "VALUES (?,?,?,?,?)", SEED_COMPETITORS,
        )
        conn.commit()
        _log(f"Seeded {len(SEED_COMPETITORS)} competitors")

    conn.close()
    _log("Database tables ready")

# ═════════════════════════════════════════════════════════════════════════════
# WEBSITE SCRAPING
# ═════════════════════════════════════════════════════════════════════════════

def _extract_prices(text: str) -> list[float]:
    prices = []
    for pat in PRICE_PATTERNS:
        for m in pat.finditer(text):
            try:
                v = float(m.group(1).replace(",", ""))
                if 50 <= v <= 20000:
                    prices.append(v)
            except (ValueError, IndexError):
                continue
    return sorted(set(prices))

def _extract_phones(text: str) -> list[str]:
    return list(set(PHONE_PATTERN.findall(text)))

def _extract_cities(text: str) -> list[str]:
    tl = text.lower()
    return [c for c in SOCAL_CITIES if c.lower() in tl]

def _extract_services(text: str) -> list[str]:
    tl = text.lower()
    return [kw for kw in SERVICE_KEYWORDS if kw.lower() in tl]

async def _fetch_page(url: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True,
                                     headers={"User-Agent": USER_AGENT}) as c:
            r = await c.get(url)
            if r.status_code == 200:
                return r.text
            _log(f"HTTP {r.status_code} fetching {url}")
    except httpx.TimeoutException:
        _log(f"Timeout fetching {url}")
    except Exception as e:
        _log(f"Error fetching {url}: {e}")
    return None

# ═════════════════════════════════════════════════════════════════════════════
# CHECK / SCAN
# ═════════════════════════════════════════════════════════════════════════════

async def check_competitor(competitor_id: int) -> dict:
    """Scrape a competitor's homepage, store snapshot, generate alerts."""
    conn = _get_conn()
    row = conn.execute("SELECT * FROM competitors WHERE id = ?", (competitor_id,)).fetchone()
    if not row:
        conn.close()
        _log(f"Competitor ID {competitor_id} not found")
        return {"error": "not found"}

    name, website = row["name"], row["website"]
    old_min, old_max = row["min_price"], row["max_price"]
    result = {"id": competitor_id, "name": name, "website": website,
              "checked_at": _now_str(), "fetch_ok": False, "prices_found": [],
              "phones_found": [], "cities_found": [], "services_found": [],
              "alerts_generated": []}

    if not website:
        conn.close()
        return result

    html = await _fetch_page(website)
    if not html:
        conn.execute(
            "INSERT INTO competitor_snapshots (competitor_id,data_json,snapshot_date) VALUES (?,?,?)",
            (competitor_id, json.dumps({"error": "fetch_failed"}), _now_str()))
        conn.execute("UPDATE competitors SET last_checked=? WHERE id=?", (_now_str(), competitor_id))
        conn.commit(); conn.close()
        return result

    result["fetch_ok"] = True
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)

    prices   = _extract_prices(text)
    phones   = _extract_phones(text)
    cities   = _extract_cities(text)
    services = _extract_services(text)
    result.update(prices_found=prices, phones_found=phones,
                  cities_found=cities, services_found=services)

    snapshot = {"prices": prices, "phones": phones, "cities": cities,
                "services": services, "page_length": len(html)}
    conn.execute(
        "INSERT INTO competitor_snapshots (competitor_id,data_json,snapshot_date) VALUES (?,?,?)",
        (competitor_id, json.dumps(snapshot), _now_str()))

    # Build dynamic update
    upd: dict = {"last_checked": _now_str()}
    if phones and not row["phone"]:          upd["phone"] = phones[0]
    if cities:                               upd["service_area"] = ", ".join(cities)
    if services:                             upd["specialties"] = json.dumps(services)
    new_min = min(prices) if prices else 0
    new_max = max(prices) if prices else 0
    if prices:
        upd["min_price"] = new_min
        upd["max_price"] = new_max

    set_clause = ", ".join(f"{k}=?" for k in upd)
    conn.execute(f"UPDATE competitors SET {set_clause} WHERE id=?",
                 [*upd.values(), competitor_id])

    # ── Alerts ───────────────────────────────────────────────────────────
    def _alert(atype, detail):
        conn.execute(
            "INSERT INTO competitor_alerts (competitor_id,alert_type,details) VALUES (?,?,?)",
            (competitor_id, atype, detail))
        result["alerts_generated"].append({"type": atype, "detail": detail})
        _log(f"Alert: {detail}")

    if prices and old_min > 0 and (new_min != old_min or new_max != old_max):
        _alert("price_change",
               f"{name}: pricing changed ${old_min:,.0f}-${old_max:,.0f} "
               f"-> ${new_min:,.0f}-${new_max:,.0f}")

    prev = conn.execute(
        "SELECT data_json FROM competitor_snapshots "
        "WHERE competitor_id=? ORDER BY id DESC LIMIT 1 OFFSET 1",
        (competitor_id,)).fetchone()
    if prev:
        try:
            prev_svc = set(json.loads(prev["data_json"]).get("services", []))
            new_svc = set(services) - prev_svc
            if new_svc:
                _alert("new_service", f"{name}: new services — {', '.join(new_svc)}")
        except (json.JSONDecodeError, KeyError):
            pass

    conn.commit(); conn.close()
    _log(f"Checked {name} — {len(prices)} prices, {len(services)} services")
    return result


async def scan_all_competitors(send_fn=None) -> list[dict]:
    """Run check_competitor for every tracked competitor, notify on alerts."""
    competitors = get_competitor_list()
    results, all_alerts = [], []

    for c in competitors:
        try:
            r = await check_competitor(c["id"])
            results.append(r)
            all_alerts.extend(r.get("alerts_generated", []))
        except Exception as e:
            _log(f"Error scanning {c['name']}: {e}")
            traceback.print_exc()
        await asyncio.sleep(2)  # polite delay

    if all_alerts and send_fn:
        msg = (f"\U0001f50d COMPETITOR SCAN COMPLETE\n"
               f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
               f"Scanned {len(competitors)} competitors\n\n"
               f"\u26a0\ufe0f {len(all_alerts)} alert(s):\n")
        for a in all_alerts:
            msg += f"  {_icon(a['type'])} {a['detail']}\n"
        try: await send_fn(msg)
        except Exception as e: _log(f"Failed to send alert: {e}")

    _log(f"Scan complete: {len(competitors)} competitors, {len(all_alerts)} alerts")
    return results

# ═════════════════════════════════════════════════════════════════════════════
# CRUD
# ═════════════════════════════════════════════════════════════════════════════

def add_competitor(name: str, website: str, notes: str = "") -> int:
    """Add a new competitor. Returns competitor ID."""
    if website and not website.startswith("http"):
        website = f"https://{website}"
    conn = _get_conn()
    conn.execute("INSERT INTO competitors (name,website,notes) VALUES (?,?,?)",
                 (name, website, notes))
    conn.commit()
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO competitor_alerts (competitor_id,alert_type,details) VALUES (?,?,?)",
        (cid, "new_competitor", f"Added new competitor: {name} ({website})"))
    conn.commit(); conn.close()
    _log(f"Added competitor: {name} (ID {cid})")
    return cid


def remove_competitor(competitor_id: int) -> bool:
    """Remove a competitor and all related data."""
    conn = _get_conn()
    row = conn.execute("SELECT name FROM competitors WHERE id=?", (competitor_id,)).fetchone()
    if not row:
        conn.close(); return False
    conn.execute("DELETE FROM competitor_snapshots WHERE competitor_id=?", (competitor_id,))
    conn.execute("DELETE FROM competitor_alerts WHERE competitor_id=?", (competitor_id,))
    conn.execute("DELETE FROM competitors WHERE id=?", (competitor_id,))
    conn.commit(); conn.close()
    _log(f"Removed competitor: {row['name']} (ID {competitor_id})")
    return True


def get_competitor_list() -> list[dict]:
    """Get all tracked competitors with their latest data."""
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM competitors ORDER BY name ASC").fetchall()
    conn.close()
    def _parse(r):
        try: specs = json.loads(r["specialties"]) if r["specialties"] else []
        except (json.JSONDecodeError, TypeError): specs = []
        return {"id": r["id"], "name": r["name"], "website": r["website"],
                "phone": r["phone"], "service_area": r["service_area"],
                "min_price": r["min_price"], "max_price": r["max_price"],
                "specialties": specs, "review_rating": r["review_rating"],
                "review_count": r["review_count"], "notes": r["notes"],
                "last_checked": r["last_checked"], "created_at": r["created_at"]}
    return [_parse(r) for r in rows]


def get_competitor_alerts(unseen_only: bool = True) -> list[dict]:
    """Get alerts, optionally filtered to unseen only."""
    conn = _get_conn()
    where = "WHERE a.seen = 0" if unseen_only else ""
    limit = "" if unseen_only else "LIMIT 50"
    rows = conn.execute(
        f"SELECT a.*, c.name as competitor_name "
        f"FROM competitor_alerts a LEFT JOIN competitors c ON a.competitor_id = c.id "
        f"{where} ORDER BY a.created_at DESC {limit}"
    ).fetchall()
    conn.close()
    return [{
        "id": r["id"], "competitor_id": r["competitor_id"],
        "competitor_name": r["competitor_name"] or "Unknown",
        "alert_type": r["alert_type"], "details": r["details"],
        "seen": bool(r["seen"]), "created_at": r["created_at"],
    } for r in rows]


def mark_alert_seen(alert_id: int) -> None:
    conn = _get_conn()
    conn.execute("UPDATE competitor_alerts SET seen=1 WHERE id=?", (alert_id,))
    conn.commit(); conn.close()

# ═════════════════════════════════════════════════════════════════════════════
# MARKET INTELLIGENCE
# ═════════════════════════════════════════════════════════════════════════════

def get_market_intel() -> dict:
    """Pricing analysis, rating comparisons, and competitive positioning."""
    comps = get_competitor_list()
    with_p = [c for c in comps if c["min_price"] > 0]
    with_r = [c for c in comps if c["review_rating"] > 0]

    all_mins = [c["min_price"] for c in with_p]
    all_maxs = [c["max_price"] for c in with_p]
    avg_min = sum(all_mins) / len(all_mins) if all_mins else 0
    avg_max = sum(all_maxs) / len(all_maxs) if all_maxs else 0

    # Zoar's market position
    if avg_min > 0:
        mid = (avg_min + avg_max) / 2
        zoar_mid = (ZOAR_MIN_PRICE + ZOAR_MAX_PRICE) / 2
        if   zoar_mid < mid * 0.85: position = "Budget"
        elif zoar_mid < mid * 1.15: position = "Mid-market"
        elif zoar_mid < mid * 1.40: position = "Mid-premium"
        else:                       position = "Premium"
    else:
        position = "Unknown (no competitor pricing data)"

    rated = sorted(with_r, key=lambda c: c["review_rating"], reverse=True)

    # Service area overlap
    city_counts: dict[str, int] = {}
    for c in comps:
        area = (c.get("service_area") or "").lower()
        for city in SOCAL_CITIES:
            if city.lower() in area: city_counts[city] = city_counts.get(city, 0) + 1

    all_specs = {s.lower() for c in comps for s in c.get("specialties", [])}
    zoar_unique = [s for s in ZOAR_SPECIALTIES if s.lower() not in all_specs]
    budget = min(with_p, key=lambda c: c["min_price"]) if with_p else None
    premium = max(with_p, key=lambda c: c["max_price"]) if with_p else None

    def _rng(c): return f"${c['min_price']:,.0f}-${c['max_price']:,.0f}" if c else ""

    return {
        "total_tracked": len(comps), "with_pricing": len(with_p),
        "avg_min_price": round(avg_min, 2), "avg_max_price": round(avg_max, 2),
        "price_range": {"low": min(all_mins, default=0), "high": max(all_maxs, default=0)},
        "zoar_position": position, "zoar_price_range": {"min": ZOAR_MIN_PRICE, "max": ZOAR_MAX_PRICE},
        "competitors_by_rating": [{"name": c["name"], "rating": c["review_rating"],
                                   "count": c["review_count"]} for c in rated],
        "service_area_overlap": dict(sorted(city_counts.items(), key=lambda x: x[1], reverse=True)),
        "competitive_advantages": zoar_unique,
        "budget_competitor": {"name": budget["name"], "range": _rng(budget)} if budget else None,
        "premium_competitor": {"name": premium["name"], "range": _rng(premium)} if premium else None,
    }

# ═════════════════════════════════════════════════════════════════════════════
# TELEGRAM FORMATTING
# ═════════════════════════════════════════════════════════════════════════════

def format_competitor_summary() -> str:
    """Telegram-formatted competitor intelligence summary."""
    comps = get_competitor_list()
    intel = get_market_intel()
    alerts = get_competitor_alerts(unseen_only=True)

    msg = "\U0001f50d COMPETITOR INTEL\n\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
    msg += f"\U0001f4ca {len(comps)} competitors tracked\n\n"

    msg += "\U0001f4b0 Market Pricing:\n"
    if intel["with_pricing"] > 0:
        msg += f"  Avg range: ${intel['avg_min_price']:,.0f}-${intel['avg_max_price']:,.0f}/day\n"
        if (b := intel["budget_competitor"]):  msg += f"  Budget: {b['name']} ({b['range']})\n"
        if (p := intel["premium_competitor"]): msg += f"  Premium: {p['name']} ({p['range']})\n"
        msg += f"  Zoar position: {intel['zoar_position']} \u2713\n"
    else:
        msg += "  No pricing data yet \u2014 run a scan\n"
    msg += f"\n\u2b50 Ratings:\n"
    if intel["competitors_by_rating"]:
        top = intel["competitors_by_rating"][0]
        msg += f"  Top: {top['name']} ({top['rating']}\u2605 / {top['count']} reviews)\n"
    msg += f"  Zoar: {ZOAR_RATING}\u2605 / {ZOAR_REVIEW_COUNT} reviews\n\n"

    msg += f"\U0001f514 Recent Alerts: {len(alerts)} unseen\n"
    if alerts:
        for a in alerts[:5]:
            label = a["alert_type"].replace("_", " ").title()
            msg += f"  - [{label}] {a['details']}\n"
    else:
        msg += "  No new alerts\n"
    return msg


def format_alerts() -> str:
    """Telegram-formatted alerts list."""
    alerts = get_competitor_alerts(unseen_only=False)
    if not alerts:
        return "\U0001f50d No competitor alerts yet."

    unseen = [a for a in alerts if not a["seen"]]
    msg = f"\U0001f514 COMPETITOR ALERTS ({len(unseen)} unseen)\n"
    msg += "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
    for a in alerts[:15]:
        seen_mark = "" if a["seen"] else " \U0001f534"
        date = a["created_at"][:10] if a["created_at"] else ""
        msg += f"{_icon(a['alert_type'])} [{date}]{seen_mark}\n   {a['details']}\n\n"
    if len(alerts) > 15:
        msg += f"... and {len(alerts) - 15} more\n"
    return msg


# ═════════════════════════════════════════════════════════════════════════════
# GOOGLE SEARCH DISCOVERY
# ═════════════════════════════════════════════════════════════════════════════

async def search_google_for_competitors(
    query: str = "luxury portable restroom rental Los Angeles",
) -> list[dict]:
    """Search Google for potential new competitors not yet tracked."""
    _log(f"Searching Google: {query}")
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True,
                                     headers={"User-Agent": USER_AGENT}) as client:
            resp = await client.get("https://www.google.com/search",
                                    params={"q": query, "num": 20})
            if resp.status_code != 200:
                _log(f"Google returned HTTP {resp.status_code}")
                return []
            html = resp.text
    except Exception as e:
        _log(f"Google search failed: {e}")
        return []

    links = re.findall(r'href="(?:/url\?q=)?(https?://[^"&]+)"', html)
    skip = {"google.com", "youtube.com", "wikipedia.org", "facebook.com",
            "twitter.com", "instagram.com", "yelp.com", "bbb.org",
            "angi.com", "homeadvisor.com", "thumbtack.com"}
    existing = {re.sub(r"https?://(?:www\.)?", "", c["website"]).rstrip("/").lower()
                for c in get_competitor_list() if c["website"]}

    seen, results = set(), []
    for link in links:
        dm = re.match(r"https?://(?:www\.)?([^/]+)", link)
        if not dm: continue
        domain = dm.group(1).lower()
        if any(s in domain for s in skip) or domain.rstrip("/") in existing or domain in seen:
            continue
        seen.add(domain)
        results.append({"name": domain.split(".")[0].replace("-", " ").title(),
                        "website": f"https://{domain}", "domain": domain,
                        "source": "google_search"})
    _log(f"Found {len(results)} potential new competitors")
    return results[:20]


# ═════════════════════════════════════════════════════════════════════════════
# TELEGRAM COMMAND HANDLERS
# ═════════════════════════════════════════════════════════════════════════════

def handle_competitors_command(text: str) -> str:
    """/competitors — show summary."""
    return format_competitor_summary()


def handle_competitor_add_command(text: str) -> str:
    """/competitor add [name] [website]"""
    usage = "Usage: /competitor add [name] [website]\nExample: /competitor add SoCal Thrones socalthrones.com"
    stripped = text.strip()
    for pfx in ["/competitor add ", "/competitor_add "]:
        if stripped.lower().startswith(pfx):
            stripped = stripped[len(pfx):].strip(); break
    else:
        return usage
    if not stripped: return usage

    quoted = re.match(r'"([^"]+)"\s+(\S+)', stripped)
    if quoted:
        name, website = quoted.group(1), quoted.group(2)
    else:
        parts = stripped.rsplit(None, 1)
        if len(parts) < 2: return "Please provide both name and website."
        name, website = parts[0], parts[1]

    cid = add_competitor(name, website)
    return f"\u2705 Added competitor: {name}\n\U0001f310 {website}\n\U0001f194 ID: {cid}"


def handle_competitor_alerts_command(text: str) -> str:
    """/alerts — show unseen competitor alerts."""
    alerts = get_competitor_alerts(unseen_only=True)
    if not alerts:
        return "\u2705 No unseen competitor alerts."

    msg = f"\U0001f514 {len(alerts)} Unseen Alert(s)\n\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
    for a in alerts:
        msg += f"{_icon(a['alert_type'])} {a['details']}\n   ({a['created_at'][:10]})\n\n"
        mark_alert_seen(a["id"])
    return msg + "All alerts marked as seen \u2713"


# ═════════════════════════════════════════════════════════════════════════════
# ASYNC SCHEDULER
# ═════════════════════════════════════════════════════════════════════════════

async def run_competitor_scan(send_fn) -> None:
    """
    Async scheduler — runs every Monday at 9 AM PT.
    Scans all competitors, sends summary, heartbeats every 60s.
    """
    try:
        from core.watchdog import heartbeat
    except ImportError:
        def heartbeat(_): pass

    _log("Competitor scan scheduler started")
    sent_week = ""

    while True:
        try:
            heartbeat("competitor_monitor")
            now = _now_pt()
            wk = now.strftime("%Y-W%W")

            if now.weekday() == 0 and now.hour == 9 and wk != sent_week:
                _log("Weekly competitor scan triggered")
                sent_week = wk
                try:
                    await scan_all_competitors(send_fn)
                    await send_fn(format_competitor_summary())
                    _log("Weekly scan complete, summary sent")
                except Exception as e:
                    _log(f"Scan error: {e}")
                    traceback.print_exc()

                # Monthly new-competitor discovery (first Monday of month)
                if now.day <= 7:
                    _log("Monthly new competitor search")
                    try:
                        found = await search_google_for_competitors()
                        if found:
                            msg = "\U0001f50d NEW COMPETITOR CANDIDATES\n\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
                            for nf in found[:10]:
                                msg += f"\u2022 {nf['name']}\n  \U0001f310 {nf['website']}\n\n"
                            msg += "Use /competitor add [name] [website] to track any."
                            await send_fn(msg)
                    except Exception as e:
                        _log(f"Google search error: {e}")

        except Exception as e:
            _log(f"Scheduler error: {e}")
            traceback.print_exc()

        await asyncio.sleep(60)
