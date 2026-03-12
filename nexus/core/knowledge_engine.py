"""
Knowledge Engine — Nexus Phase 13
Persistent knowledge base, insight system, and scheduled analysis.

SQLite-backed "brain" that stores everything Nexus learns:
- Categorized knowledge entries (business, personal, market, ads, general)
- Conversation context for AI continuity
- Derived insights and pattern detection
- Scheduled daily analysis (2 AM PT) and weekly reports (Sunday 10 AM PT)

Importable by server.py and telegram/bot.py.
"""
from __future__ import annotations
import sqlite3, json, traceback, asyncio
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DB_PATH = Path.home() / ".nexus" / "memory.db"
DB_PATH.parent.mkdir(exist_ok=True)

PT = ZoneInfo("America/Los_Angeles")

# ── Seed knowledge — injected on first init ──────────────────────────────────
SEED_KNOWLEDGE = [
    ("business", "pricing", "standard",
     "Standard rate: $1,100. With pushback: $1,000. Floor: $900 (never below). "
     "Attendant: $150-300. Premium amenity: $150-200. Peak season: 15-30% markup. "
     "Construction monthly: $800-900. Emergency/same-day: 25-50% rush premium."),
    ("business", "service_area", "coverage",
     "San Fernando Valley, Los Angeles, Ventura County, Santa Clarita, Pasadena, "
     "Burbank, Glendale, Thousand Oaks, Simi Valley, Malibu, Santa Barbara. "
     "Free delivery within 30 miles."),
    ("business", "trailer_specs", "features",
     "4 luxury stalls, fully self-contained (no hookups needed, just flat ground), "
     "climate controlled (AC + heating), LED lighting, chrome fixtures, running water, "
     "flushing toilets, Bluetooth speakers, vanity mirrors, premium hand soap. "
     "White exterior with chrome trim."),
    ("business", "contact", "public",
     "Phone: (424) 235-8979, Email: zoarbathrooms@gmail.com, "
     "Website: zoarbathroomrental.com"),
    ("personal", "profile", "kai",
     "Kai, 20 years old, solo operator running Zoar Bathroom Rentals from a Mac Mini. "
     "Goal: book trailer consistently, hire delivery crew, scale to passive income."),
]


# ═══════════════════════════════════════════════════════════════════════════════
#  Database init
# ═══════════════════════════════════════════════════════════════════════════════

def _get_conn() -> sqlite3.Connection:
    """Open a connection with WAL mode and row_factory."""
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_knowledge_db():
    """Create knowledge tables, indexes, and seed initial knowledge."""
    conn = _get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS knowledge_base (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT NOT NULL,
        subcategory TEXT DEFAULT '',
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        source TEXT DEFAULT 'manual',
        confidence REAL DEFAULT 1.0,
        access_count INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        expires_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_kb_category ON knowledge_base(category);
    CREATE INDEX IF NOT EXISTS idx_kb_key ON knowledge_base(key);
    CREATE INDEX IF NOT EXISTS idx_kb_search ON knowledge_base(category, subcategory, key);

    CREATE TABLE IF NOT EXISTS knowledge_insights (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        insight_type TEXT NOT NULL,
        content TEXT NOT NULL,
        data_sources TEXT DEFAULT '',
        actionable INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        sent_to_kai INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS conversation_context (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        role TEXT NOT NULL,
        message TEXT NOT NULL,
        intent TEXT DEFAULT '',
        entities TEXT DEFAULT '{}',
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_convo_created ON conversation_context(created_at);
    """)
    conn.commit()

    # Seed knowledge if table is empty
    row = conn.execute("SELECT COUNT(*) FROM knowledge_base").fetchone()
    if row[0] == 0:
        for cat, subcat, key, value in SEED_KNOWLEDGE:
            conn.execute(
                "INSERT INTO knowledge_base (category, subcategory, key, value, source) "
                "VALUES (?, ?, ?, ?, 'seed')",
                (cat, subcat, key, value),
            )
        conn.commit()
        print("[KnowledgeEngine] Seeded initial knowledge")

    conn.close()
    print("[KnowledgeEngine] Database tables ready")


# ═══════════════════════════════════════════════════════════════════════════════
#  Core Knowledge Operations
# ═══════════════════════════════════════════════════════════════════════════════

def store_knowledge(
    category: str,
    key: str,
    value: str,
    subcategory: str = "",
    source: str = "manual",
    confidence: float = 1.0,
    expires_at: str = None,
) -> int:
    """Store or update (upsert) a knowledge entry. Returns row ID."""
    conn = _get_conn()
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    # Check for existing entry with same category + key
    existing = conn.execute(
        "SELECT id FROM knowledge_base WHERE category = ? AND key = ?",
        (category, key),
    ).fetchone()

    if existing:
        conn.execute(
            "UPDATE knowledge_base SET value = ?, subcategory = ?, source = ?, "
            "confidence = ?, expires_at = ?, updated_at = ? WHERE id = ?",
            (value, subcategory, source, confidence, expires_at, now, existing["id"]),
        )
        conn.commit()
        row_id = existing["id"]
    else:
        cur = conn.execute(
            "INSERT INTO knowledge_base "
            "(category, key, value, subcategory, source, confidence, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (category, key, value, subcategory, source, confidence, expires_at),
        )
        conn.commit()
        row_id = cur.lastrowid

    conn.close()
    return row_id


def get_knowledge(
    category: str = None,
    key: str = None,
    subcategory: str = None,
) -> list[dict]:
    """Retrieve knowledge entries with optional filters."""
    conn = _get_conn()
    clauses = []
    params = []

    if category:
        clauses.append("category = ?")
        params.append(category)
    if key:
        clauses.append("key = ?")
        params.append(key)
    if subcategory:
        clauses.append("subcategory = ?")
        params.append(subcategory)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    # Exclude expired entries
    if where:
        where += " AND (expires_at IS NULL OR expires_at > datetime('now'))"
    else:
        where = " WHERE (expires_at IS NULL OR expires_at > datetime('now'))"

    rows = conn.execute(
        f"SELECT * FROM knowledge_base{where} ORDER BY access_count DESC, updated_at DESC",
        params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def search_knowledge(query: str, limit: int = 10) -> list[dict]:
    """Full-text search across key and value columns (LIKE %query%)."""
    conn = _get_conn()
    pattern = f"%{query}%"
    rows = conn.execute(
        "SELECT * FROM knowledge_base "
        "WHERE (key LIKE ? OR value LIKE ? OR subcategory LIKE ?) "
        "AND (expires_at IS NULL OR expires_at > datetime('now')) "
        "ORDER BY access_count DESC, confidence DESC, updated_at DESC "
        "LIMIT ?",
        (pattern, pattern, pattern, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_knowledge(
    knowledge_id: int = None,
    key: str = None,
    category: str = None,
) -> int:
    """Delete knowledge entries by id, key, or category. Returns count deleted."""
    conn = _get_conn()
    count = 0

    if knowledge_id is not None:
        cur = conn.execute("DELETE FROM knowledge_base WHERE id = ?", (knowledge_id,))
        count = cur.rowcount
    elif key and category:
        cur = conn.execute(
            "DELETE FROM knowledge_base WHERE key = ? AND category = ?",
            (key, category),
        )
        count = cur.rowcount
    elif key:
        cur = conn.execute("DELETE FROM knowledge_base WHERE key = ?", (key,))
        count = cur.rowcount
    elif category:
        cur = conn.execute("DELETE FROM knowledge_base WHERE category = ?", (category,))
        count = cur.rowcount

    conn.commit()
    conn.close()
    return count


def increment_access(knowledge_id: int):
    """Bump access_count for relevance ranking."""
    conn = _get_conn()
    conn.execute(
        "UPDATE knowledge_base SET access_count = access_count + 1 WHERE id = ?",
        (knowledge_id,),
    )
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  Note System (Kai's /note command)
# ═══════════════════════════════════════════════════════════════════════════════

# Ordered list — first match wins, so put specific categories before generic ones.
_NOTE_KEYWORDS = [
    # Events (specific niches first)
    ("wedding", "events"),
    ("party", "events"),
    ("construction", "events"),
    ("festival", "events"),
    ("graduation", "events"),
    ("event", "events"),
    # Operations
    ("trailer", "operations"),
    ("delivery", "operations"),
    ("pickup", "operations"),
    ("crew", "operations"),
    # Leads
    ("lead", "leads"),
    ("customer", "leads"),
    ("client", "leads"),
    # Marketing
    ("marketing", "marketing"),
    ("ad ", "marketing"),
    ("social", "marketing"),
    ("instagram", "marketing"),
    ("tiktok", "marketing"),
    # Ideas
    ("idea", "ideas"),
    # Pricing (generic — last so "wedding quote" hits events, not pricing)
    ("price", "pricing"),
    ("rate", "pricing"),
    ("cost", "pricing"),
    ("quote", "pricing"),
]


def _auto_categorize(text: str) -> str:
    """Try to guess a subcategory from note content."""
    lower = text.lower()
    for keyword, subcat in _NOTE_KEYWORDS:
        if keyword in lower:
            return subcat
    return "general"


def save_note(text: str, category: str = "general") -> dict:
    """Save a note from Kai. Auto-categorize if possible."""
    subcategory = _auto_categorize(text)
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    note_key = f"note_{ts.replace(' ', '_').replace(':', '')}"
    row_id = store_knowledge(
        category=category,
        key=note_key,
        value=text,
        subcategory=subcategory,
        source="manual",
    )
    return {"id": row_id, "key": note_key, "subcategory": subcategory, "text": text}


def get_recent_notes(limit: int = 20) -> list[dict]:
    """Get recent notes (entries whose key starts with 'note_')."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM knowledge_base WHERE key LIKE 'note_%' "
        "ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════════
#  Conversation Context
# ═══════════════════════════════════════════════════════════════════════════════

def store_conversation(
    role: str,
    message: str,
    intent: str = "",
    entities: dict = None,
):
    """Store a conversation turn. Keeps last ~100 entries."""
    conn = _get_conn()
    conn.execute(
        "INSERT INTO conversation_context (role, message, intent, entities) "
        "VALUES (?, ?, ?, ?)",
        (role, message, intent, json.dumps(entities or {})),
    )
    conn.commit()

    # Prune old entries beyond 100
    conn.execute(
        "DELETE FROM conversation_context WHERE id NOT IN "
        "(SELECT id FROM conversation_context ORDER BY created_at DESC LIMIT 100)"
    )
    conn.commit()
    conn.close()


def get_recent_conversation(limit: int = 20) -> list[dict]:
    """Get recent conversation history, oldest first."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM conversation_context ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    results = [dict(r) for r in rows]
    results.reverse()  # oldest first for natural reading order
    return results


# ═══════════════════════════════════════════════════════════════════════════════
#  Insight System
# ═══════════════════════════════════════════════════════════════════════════════

def store_insight(
    insight_type: str,
    content: str,
    data_sources: list = None,
    actionable: bool = False,
) -> int:
    """Store a derived insight. Returns row ID."""
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO knowledge_insights (insight_type, content, data_sources, actionable) "
        "VALUES (?, ?, ?, ?)",
        (insight_type, content, json.dumps(data_sources or []), int(actionable)),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def get_unsent_insights() -> list[dict]:
    """Get insights not yet sent to Kai."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM knowledge_insights WHERE sent_to_kai = 0 "
        "ORDER BY created_at ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_insight_sent(insight_id: int):
    """Mark an insight as delivered to Kai."""
    conn = _get_conn()
    conn.execute(
        "UPDATE knowledge_insights SET sent_to_kai = 1 WHERE id = ?",
        (insight_id,),
    )
    conn.commit()
    conn.close()


def get_recent_insights(limit: int = 10) -> list[dict]:
    """Get most recent insights."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM knowledge_insights ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════════
#  Learning & Analysis
# ═══════════════════════════════════════════════════════════════════════════════

def _cleanup_expired():
    """Remove knowledge entries past their expires_at."""
    conn = _get_conn()
    cur = conn.execute(
        "DELETE FROM knowledge_base WHERE expires_at IS NOT NULL AND expires_at < datetime('now')"
    )
    count = cur.rowcount
    conn.commit()
    conn.close()
    return count


def _analyze_lead_sources() -> list[str]:
    """Analyze which lead sources convert best."""
    insights = []
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT source, status, COUNT(*) as cnt
            FROM leads
            WHERE discovered_at >= datetime('now', '-7 days')
            GROUP BY source, status
            ORDER BY source, cnt DESC
        """).fetchall()
    except Exception:
        conn.close()
        return insights

    # Build per-source breakdown
    source_data: dict[str, dict] = {}
    for r in rows:
        src = r["source"] or "unknown"
        if src not in source_data:
            source_data[src] = {"total": 0, "statuses": {}}
        source_data[src]["total"] += r["cnt"]
        source_data[src]["statuses"][r["status"]] = r["cnt"]

    for src, data in source_data.items():
        booked = data["statuses"].get("closed", 0) + data["statuses"].get("replied", 0)
        total = data["total"]
        if total >= 3:
            rate = (booked / total) * 100
            insights.append(
                f"Source '{src}': {total} leads this week, {rate:.0f}% engagement rate"
            )

    conn.close()
    return insights


def _analyze_response_times() -> list[str]:
    """Analyze if faster responses lead to more conversions."""
    insights = []
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT l.id, l.status, l.discovered_at, l.last_contacted_at
            FROM leads l
            WHERE l.discovered_at >= datetime('now', '-14 days')
              AND l.last_contacted_at != ''
        """).fetchall()
    except Exception:
        conn.close()
        return insights

    fast_replied = 0
    fast_total = 0
    slow_replied = 0
    slow_total = 0

    for r in rows:
        try:
            discovered = datetime.strptime(r["discovered_at"], "%Y-%m-%d %H:%M:%S")
            contacted = datetime.strptime(r["last_contacted_at"], "%Y-%m-%d %H:%M:%S")
            delta_hours = (contacted - discovered).total_seconds() / 3600
            is_engaged = r["status"] in ("replied", "closed")

            if delta_hours <= 1:
                fast_total += 1
                if is_engaged:
                    fast_replied += 1
            else:
                slow_total += 1
                if is_engaged:
                    slow_replied += 1
        except (ValueError, TypeError):
            continue

    if fast_total >= 3 and slow_total >= 3:
        fast_rate = (fast_replied / fast_total) * 100
        slow_rate = (slow_replied / slow_total) * 100
        if fast_rate > slow_rate:
            insights.append(
                f"Speed matters: leads contacted within 1hr have {fast_rate:.0f}% "
                f"engagement vs {slow_rate:.0f}% for slower responses"
            )

    conn.close()
    return insights


def _analyze_time_of_day() -> list[str]:
    """Analyze which hours produce the most leads."""
    insights = []
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT CAST(strftime('%H', discovered_at) AS INTEGER) as hour,
                   COUNT(*) as cnt
            FROM leads
            WHERE discovered_at >= datetime('now', '-14 days')
            GROUP BY hour
            ORDER BY cnt DESC
            LIMIT 3
        """).fetchall()
    except Exception:
        conn.close()
        return insights

    if rows and rows[0]["cnt"] >= 3:
        peak_hours = [f"{r['hour']}:00" for r in rows[:3]]
        insights.append(f"Peak lead hours (last 2 weeks): {', '.join(peak_hours)}")

    conn.close()
    return insights


def _analyze_niche_performance() -> list[str]:
    """Analyze performance by event type / niche from notes field."""
    insights = []
    conn = _get_conn()
    try:
        rows = conn.execute("""
            SELECT notes, status FROM leads
            WHERE discovered_at >= datetime('now', '-30 days')
              AND notes != ''
        """).fetchall()
    except Exception:
        conn.close()
        return insights

    niche_counts: dict[str, dict] = {}
    niche_keywords = {
        "wedding": "weddings",
        "construction": "construction",
        "party": "parties",
        "corporate": "corporate",
        "festival": "festivals",
        "film": "film/production",
        "movie": "film/production",
        "graduation": "graduations",
    }

    for r in rows:
        lower_notes = (r["notes"] or "").lower()
        for kw, niche in niche_keywords.items():
            if kw in lower_notes:
                if niche not in niche_counts:
                    niche_counts[niche] = {"total": 0, "booked": 0}
                niche_counts[niche]["total"] += 1
                if r["status"] in ("replied", "closed"):
                    niche_counts[niche]["booked"] += 1
                break

    for niche, data in sorted(niche_counts.items(), key=lambda x: x[1]["total"], reverse=True):
        if data["total"] >= 2:
            insights.append(
                f"Niche '{niche}': {data['total']} leads, {data['booked']} engaged"
            )

    conn.close()
    return insights


def run_daily_analysis() -> list[str]:
    """
    Run daily analysis (scheduled for 2 AM PT).
    Finds patterns in lead data, cleans stale knowledge, stores insights.
    Returns list of new insight strings.
    """
    all_insights: list[str] = []

    # 1. Cleanup expired knowledge
    expired_count = _cleanup_expired()
    if expired_count:
        all_insights.append(f"Cleaned up {expired_count} expired knowledge entries")

    # 2. Lead source analysis
    try:
        all_insights.extend(_analyze_lead_sources())
    except Exception as e:
        print(f"[KnowledgeEngine] Lead source analysis error: {e}")

    # 3. Response time analysis
    try:
        all_insights.extend(_analyze_response_times())
    except Exception as e:
        print(f"[KnowledgeEngine] Response time analysis error: {e}")

    # 4. Time-of-day patterns
    try:
        all_insights.extend(_analyze_time_of_day())
    except Exception as e:
        print(f"[KnowledgeEngine] Time-of-day analysis error: {e}")

    # 5. Niche performance
    try:
        all_insights.extend(_analyze_niche_performance())
    except Exception as e:
        print(f"[KnowledgeEngine] Niche analysis error: {e}")

    # Store all insights
    for text in all_insights:
        store_insight(
            insight_type="pattern",
            content=text,
            data_sources=["daily_analysis"],
            actionable=False,
        )

    print(f"[KnowledgeEngine] Daily analysis complete: {len(all_insights)} insights")
    return all_insights


def generate_weekly_insights_report() -> str:
    """
    Generate the Sunday 10 AM PT weekly insights report.
    Pulls lead data for the past 7 days and recent insights.
    """
    conn = _get_conn()

    # -- Lead counts by status (last 7 days) --
    total_leads = 0
    status_counts: dict[str, int] = {}
    try:
        rows = conn.execute("""
            SELECT status, COUNT(*) as cnt FROM leads
            WHERE discovered_at >= datetime('now', '-7 days')
            GROUP BY status
        """).fetchall()
        for r in rows:
            status_counts[r["status"]] = r["cnt"]
            total_leads += r["cnt"]
    except Exception:
        pass

    # -- Source breakdown --
    source_counts: dict[str, int] = {}
    try:
        rows = conn.execute("""
            SELECT source, COUNT(*) as cnt FROM leads
            WHERE discovered_at >= datetime('now', '-7 days')
            GROUP BY source ORDER BY cnt DESC
        """).fetchall()
        for r in rows:
            source_counts[r["source"] or "unknown"] = r["cnt"]
    except Exception:
        pass

    # -- Messages sent this week --
    msgs_sent = 0
    try:
        row = conn.execute("""
            SELECT COUNT(*) FROM lead_messages
            WHERE ts >= datetime('now', '-7 days') AND direction = 'outbound'
        """).fetchone()
        msgs_sent = row[0] if row else 0
    except Exception:
        pass

    conn.close()

    # -- Recent insights (last 7 days) --
    insights = []
    try:
        c = _get_conn()
        rows = c.execute("""
            SELECT content, insight_type FROM knowledge_insights
            WHERE created_at >= datetime('now', '-7 days')
            ORDER BY created_at DESC LIMIT 10
        """).fetchall()
        insights = [dict(r) for r in rows]
        c.close()
    except Exception:
        pass

    # -- Build report --
    quotes_sent = status_counts.get("awaiting_approval", 0) + status_counts.get("sms_sent", 0) + \
                  status_counts.get("initial_contact", 0)
    bookings = status_counts.get("closed", 0)
    best_source = max(source_counts, key=source_counts.get) if source_counts else "N/A"

    # Determine what's working vs needs attention
    working = []
    attention = []
    recommendations = []

    for ins in insights:
        content = ins["content"]
        if any(w in content.lower() for w in ("engagement", "peak", "speed matters")):
            working.append(content)
        else:
            attention.append(content)

    if not working:
        working.append("Consistent lead tracking and follow-ups active")
    if not attention:
        attention.append("No critical issues detected this week")

    # Auto-generate recommendations
    if total_leads < 5:
        recommendations.append("Lead volume is low — consider boosting ad spend or posting on more platforms")
    if bookings == 0 and total_leads > 0:
        recommendations.append("No bookings this week — review follow-up timing and messaging")
    replied = status_counts.get("replied", 0)
    if replied > 0 and bookings == 0:
        recommendations.append(f"{replied} leads replied but none closed — review closing strategy")
    if best_source != "N/A":
        recommendations.append(f"Double down on '{best_source}' — it's your top lead source right now")
    if not recommendations:
        recommendations.append("Keep up current outreach cadence and monitor conversion rates")

    # Determine best niche from notes
    best_niche = "N/A"
    try:
        c = _get_conn()
        niche_row = c.execute("""
            SELECT notes, COUNT(*) as cnt FROM leads
            WHERE discovered_at >= datetime('now', '-7 days') AND notes != ''
            GROUP BY notes ORDER BY cnt DESC LIMIT 1
        """).fetchone()
        if niche_row and niche_row["notes"]:
            best_niche = niche_row["notes"][:30]
        c.close()
    except Exception:
        pass

    working_lines = "\n".join(f"  * {w}" for w in working[:5])
    attention_lines = "\n".join(f"  * {a}" for a in attention[:5])
    rec_lines = "\n".join(f"  * {r}" for r in recommendations[:5])

    report = f"""\U0001f9e0 NEXUS WEEKLY INSIGHTS
{'=' * 34}

\U0001f4c8 WHAT'S WORKING:
{working_lines}

\U0001f4c9 WHAT NEEDS ATTENTION:
{attention_lines}

\U0001f4a1 RECOMMENDATIONS:
{rec_lines}

\U0001f4ca THIS WEEK BY THE NUMBERS:
  * Leads: {total_leads} | Quotes sent: {quotes_sent} | Bookings: {bookings}
  * Messages sent: {msgs_sent}
  * Best performing source: {best_source}
  * Best performing niche: {best_niche}
"""
    return report


# ═══════════════════════════════════════════════════════════════════════════════
#  Telegram Command Handlers
# ═══════════════════════════════════════════════════════════════════════════════

def handle_note_command(text: str) -> str:
    """Handle /note [text] — save a note and return confirmation."""
    if not text.strip():
        return "\u270f\ufe0f Usage: `/note your note text here`"
    result = save_note(text.strip())
    return (
        f"\U0001f4dd *Note saved*\n"
        f"Category: `{result['subcategory']}`\n"
        f"ID: `{result['id']}`"
    )


def handle_learn_command() -> str:
    """Handle /learn — return what Nexus has learned recently."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT category, subcategory, key, value, source, created_at "
        "FROM knowledge_base "
        "WHERE source != 'seed' "
        "ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    conn.close()

    if not rows:
        return "\U0001f914 I haven't learned anything new yet. Use `/note` to teach me, or I'll learn from lead patterns over time."

    lines = ["\U0001f9e0 *Recent Learning:*\n"]
    for r in rows:
        r = dict(r)
        preview = r["value"][:100] + ("..." if len(r["value"]) > 100 else "")
        lines.append(
            f"  \u2022 [{r['category']}/{r['subcategory']}] `{r['key']}`\n"
            f"    {preview}\n"
            f"    _Source: {r['source']} | {r['created_at']}_"
        )

    return "\n".join(lines)


def handle_insights_command() -> str:
    """Handle /insights — return recent insights."""
    insights = get_recent_insights(limit=10)

    if not insights:
        return "\U0001f4ad No insights yet. I'll generate some during the next daily analysis (2 AM)."

    lines = ["\U0001f4a1 *Recent Insights:*\n"]
    for ins in insights:
        marker = "\u26a1" if ins["actionable"] else "\U0001f4ca"
        lines.append(f"  {marker} {ins['content']}\n    _({ins['insight_type']} | {ins['created_at']})_")

    return "\n".join(lines)


def handle_know_command(topic: str) -> str:
    """Handle /know [topic] — search knowledge base."""
    if not topic.strip():
        return "\U0001f50d Usage: `/know [topic]`\nExample: `/know pricing`, `/know service area`"

    results = search_knowledge(topic.strip(), limit=5)

    if not results:
        return f"\u2753 I don't have any knowledge about *{topic}*. Teach me with `/note`!"

    lines = [f"\U0001f9e0 *Knowledge: {topic}*\n"]
    for r in results:
        # Increment access count for ranking
        increment_access(r["id"])
        preview = r["value"][:200] + ("..." if len(r["value"]) > 200 else "")
        conf_bar = "\u2588" * int(r["confidence"] * 5) + "\u2591" * (5 - int(r["confidence"] * 5))
        lines.append(
            f"  \u2022 `{r['category']}/{r['key']}`\n"
            f"    {preview}\n"
            f"    _Confidence: [{conf_bar}] | Used {r['access_count']}x_"
        )

    return "\n".join(lines)


def handle_forget_command(topic: str) -> str:
    """Handle /forget [topic] — remove specific knowledge."""
    if not topic.strip():
        return "\U0001f5d1 Usage: `/forget [topic]`\nExample: `/forget old_note_123`"

    # Try deleting by key first
    count = delete_knowledge(key=topic.strip())
    if count:
        return f"\U0001f5d1 Removed {count} knowledge entry(s) matching key `{topic}`."

    # Fall back to search + delete
    results = search_knowledge(topic.strip(), limit=5)
    if not results:
        return f"\u2753 Couldn't find anything matching *{topic}* to forget."

    deleted = 0
    for r in results:
        deleted += delete_knowledge(knowledge_id=r["id"])

    return f"\U0001f5d1 Forgot {deleted} entries related to *{topic}*."


# ═══════════════════════════════════════════════════════════════════════════════
#  Background Scheduler
# ═══════════════════════════════════════════════════════════════════════════════

async def run_knowledge_engine(send_fn):
    """
    Background loop. Runs every 5 minutes and checks:
    1. Daily analysis at 2 AM PT
    2. Weekly insights report at 10 AM PT Sundays
    3. Unsent insights to deliver

    send_fn: async callable(text) to send Telegram messages.
    """
    # Initialize DB tables on first run
    try:
        init_knowledge_db()
    except Exception as e:
        print(f"[KnowledgeEngine] Init error: {e}")

    last_daily: str = ""      # "YYYY-MM-DD" of last daily run
    last_weekly: str = ""     # "YYYY-MM-DD" of last weekly run

    while True:
        try:
            now_pt = datetime.now(PT)
            today_str = now_pt.strftime("%Y-%m-%d")

            # ── Daily analysis at 2 AM PT ──
            if now_pt.hour == 2 and last_daily != today_str:
                last_daily = today_str
                print("[KnowledgeEngine] Running daily analysis...")
                try:
                    insights = run_daily_analysis()
                    if insights and send_fn:
                        summary = "\U0001f9e0 *Daily Analysis Complete*\n\n"
                        for i, text in enumerate(insights[:8], 1):
                            summary += f"{i}. {text}\n"
                        await send_fn(summary)
                except Exception as e:
                    print(f"[KnowledgeEngine] Daily analysis error: {e}")
                    traceback.print_exc()

            # ── Weekly report at 10 AM PT on Sundays ──
            if now_pt.weekday() == 6 and now_pt.hour == 10 and last_weekly != today_str:
                last_weekly = today_str
                print("[KnowledgeEngine] Generating weekly report...")
                try:
                    report = generate_weekly_insights_report()
                    if send_fn:
                        await send_fn(report)
                except Exception as e:
                    print(f"[KnowledgeEngine] Weekly report error: {e}")
                    traceback.print_exc()

            # ── Deliver unsent insights ──
            try:
                unsent = get_unsent_insights()
                if unsent and send_fn:
                    for ins in unsent[:3]:  # Max 3 per cycle to avoid spam
                        marker = "\u26a1" if ins["actionable"] else "\U0001f4a1"
                        await send_fn(
                            f"{marker} *New Insight*\n{ins['content']}\n_({ins['insight_type']})_"
                        )
                        mark_insight_sent(ins["id"])
            except Exception as e:
                print(f"[KnowledgeEngine] Insight delivery error: {e}")

        except Exception as e:
            print(f"[KnowledgeEngine] Scheduler error: {e}")
            traceback.print_exc()

        await asyncio.sleep(300)  # 5 minutes
