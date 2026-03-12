from __future__ import annotations
"""
Autonomous Task Queue — Nexus is NEVER idle.

Manages a priority-based task queue that keeps Nexus continuously working:
  - 12 task types from critical (lead notifications) to routine (DB maintenance)
  - Natural language classification — route /do requests to the right handler
  - Daily task population at midnight PT
  - Continuous scheduler loop: pick next task, execute, log, repeat
  - Telegram commands: /tasks, /do [request]
  - Full stats tracking: completion rates, durations, failures

Config: ~/.nexus/config.json
Database: ~/.nexus/memory.db
"""
import asyncio, json, re, sqlite3, time, traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# ── Constants ────────────────────────────────────────────────────────────────

DB_PATH = Path.home() / ".nexus" / "memory.db"
PT = ZoneInfo("America/Los_Angeles")

# Task types in priority order (1 = highest)
TASK_TYPES = {
    "lead_notification":   {"priority": 1,  "label": "Lead Notifications",    "icon": "\U0001f4e9"},
    "lead_reply_check":    {"priority": 2,  "label": "Lead Reply Check",      "icon": "\U0001f4ec"},
    "ad_monitor":          {"priority": 3,  "label": "Ad Performance",        "icon": "\U0001f4ca"},
    "group_scrape":        {"priority": 4,  "label": "FB Group Scrape",       "icon": "\U0001f50d"},
    "lead_analysis":       {"priority": 5,  "label": "Lead Analysis",         "icon": "\U0001f4cb"},
    "competitor_check":    {"priority": 6,  "label": "Competitor Check",      "icon": "\U0001f575"},
    "content_generation":  {"priority": 7,  "label": "Content Generation",    "icon": "\u270d\ufe0f"},
    "video_generation":    {"priority": 8,  "label": "Video Generation",      "icon": "\U0001f3ac"},
    "venue_research":      {"priority": 9,  "label": "Venue Research",        "icon": "\U0001f3e0"},
    "knowledge_analysis":  {"priority": 10, "label": "Knowledge Analysis",    "icon": "\U0001f4da"},
    "system_health":       {"priority": 11, "label": "System Health",         "icon": "\U0001f6e0\ufe0f"},
    "db_optimization":     {"priority": 12, "label": "DB Maintenance",        "icon": "\U0001f5c4\ufe0f"},
}

# Scheduler timing
SCHEDULER_INTERVAL = 30      # seconds between task checks
HEARTBEAT_INTERVAL = 60      # seconds between heartbeat logs
TASK_TIMEOUT = 300            # max seconds per task before forced fail
DAILY_RESET_HOUR = 0         # midnight PT for daily task population

# ── Helpers ──────────────────────────────────────────────────────────────────

def _now_pt() -> datetime:
    return datetime.now(PT)

def _now_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def _today_str() -> str:
    return _now_pt().strftime("%Y-%m-%d")

def _log(msg: str):
    print(f"[TaskQueue] {msg}")

def _load_config() -> dict:
    p = Path.home() / ".nexus" / "config.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


# ═════════════════════════════════════════════════════════════════════════════
# DATABASE
# ═════════════════════════════════════════════════════════════════════════════

def init_task_queue_db():
    """Create autonomous_tasks table and indexes."""
    conn = _get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS autonomous_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_type TEXT NOT NULL,
        description TEXT,
        priority INTEGER DEFAULT 5,
        source TEXT DEFAULT 'auto',
        status TEXT DEFAULT 'queued',
        result TEXT DEFAULT '',
        started_at TEXT,
        completed_at TEXT,
        duration_seconds REAL DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_tasks_status ON autonomous_tasks(status);
    CREATE INDEX IF NOT EXISTS idx_tasks_priority ON autonomous_tasks(priority);
    CREATE INDEX IF NOT EXISTS idx_tasks_type ON autonomous_tasks(task_type);
    CREATE INDEX IF NOT EXISTS idx_tasks_created ON autonomous_tasks(created_at);
    """)
    conn.commit()
    conn.close()
    _log("Database tables ready")


# ═════════════════════════════════════════════════════════════════════════════
# QUEUE OPERATIONS
# ═════════════════════════════════════════════════════════════════════════════

def queue_task(
    task_type: str,
    description: str = "",
    priority: int | None = None,
    source: str = "auto",
) -> int:
    """
    Add a task to the queue. Returns the task ID.

    priority: overrides default if given; otherwise uses TASK_TYPES default.
    source: 'auto' | 'telegram' | 'schedule' | 'api' | 'system'
    """
    if priority is None:
        priority = TASK_TYPES.get(task_type, {}).get("priority", 5)

    label = TASK_TYPES.get(task_type, {}).get("label", task_type)
    if not description:
        description = label

    conn = _get_conn()
    conn.execute(
        "INSERT INTO autonomous_tasks (task_type, description, priority, source, status) "
        "VALUES (?, ?, ?, ?, 'queued')",
        (task_type, description, priority, source),
    )
    conn.commit()
    task_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    _log(f"Queued: [{task_type}] {description} (ID {task_id}, P{priority})")
    return task_id


def get_next_task() -> dict | None:
    """
    Get the highest-priority queued task.

    Returns dict with task data, or None if queue is empty.
    Priority order: lowest number first, then oldest first.
    """
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM autonomous_tasks "
        "WHERE status = 'queued' "
        "ORDER BY priority ASC, created_at ASC "
        "LIMIT 1"
    ).fetchone()
    conn.close()

    if not row:
        return None

    return {
        "id": row["id"],
        "task_type": row["task_type"],
        "description": row["description"],
        "priority": row["priority"],
        "source": row["source"],
        "status": row["status"],
        "created_at": row["created_at"],
    }


def start_task(task_id: int) -> bool:
    """Mark a task as in_progress. Returns True if updated."""
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE autonomous_tasks SET status='in_progress', started_at=? "
        "WHERE id=? AND status='queued'",
        (_now_str(), task_id),
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def complete_task(task_id: int, result: str = "") -> bool:
    """Mark a task as completed with result. Records duration."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT started_at FROM autonomous_tasks WHERE id=?", (task_id,)
    ).fetchone()

    duration = 0.0
    if row and row["started_at"]:
        try:
            started = datetime.strptime(row["started_at"], "%Y-%m-%d %H:%M:%S")
            duration = (datetime.utcnow() - started).total_seconds()
        except (ValueError, TypeError):
            pass

    cur = conn.execute(
        "UPDATE autonomous_tasks SET status='completed', result=?, "
        "completed_at=?, duration_seconds=? WHERE id=?",
        (result[:2000], _now_str(), round(duration, 2), task_id),
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def fail_task(task_id: int, error: str = "") -> bool:
    """Mark a task as failed with error message. Records duration."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT started_at FROM autonomous_tasks WHERE id=?", (task_id,)
    ).fetchone()

    duration = 0.0
    if row and row["started_at"]:
        try:
            started = datetime.strptime(row["started_at"], "%Y-%m-%d %H:%M:%S")
            duration = (datetime.utcnow() - started).total_seconds()
        except (ValueError, TypeError):
            pass

    cur = conn.execute(
        "UPDATE autonomous_tasks SET status='failed', result=?, "
        "completed_at=?, duration_seconds=? WHERE id=?",
        (f"FAILED: {error}"[:2000], _now_str(), round(duration, 2), task_id),
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def cancel_task(task_id: int) -> bool:
    """Cancel a queued task. Only works on 'queued' tasks."""
    conn = _get_conn()
    cur = conn.execute(
        "UPDATE autonomous_tasks SET status='cancelled', completed_at=? "
        "WHERE id=? AND status='queued'",
        (_now_str(), task_id),
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


# ═════════════════════════════════════════════════════════════════════════════
# QUEUE STATUS & STATS
# ═════════════════════════════════════════════════════════════════════════════

def get_queue_status() -> dict:
    """Overview of all tasks by status and type."""
    conn = _get_conn()

    # Counts by status
    status_counts = {}
    for row in conn.execute(
        "SELECT status, COUNT(*) as cnt FROM autonomous_tasks GROUP BY status"
    ).fetchall():
        status_counts[row["status"]] = row["cnt"]

    # In-progress tasks
    in_progress = conn.execute(
        "SELECT id, task_type, description, started_at "
        "FROM autonomous_tasks WHERE status='in_progress' "
        "ORDER BY started_at ASC"
    ).fetchall()

    # Queued tasks (next 10)
    queued = conn.execute(
        "SELECT id, task_type, description, priority "
        "FROM autonomous_tasks WHERE status='queued' "
        "ORDER BY priority ASC, created_at ASC LIMIT 10"
    ).fetchall()

    # Today's completed
    today = _today_str()
    today_completed = conn.execute(
        "SELECT COUNT(*) FROM autonomous_tasks "
        "WHERE status='completed' AND completed_at >= ?",
        (today,),
    ).fetchone()[0]

    today_failed = conn.execute(
        "SELECT COUNT(*) FROM autonomous_tasks "
        "WHERE status='failed' AND completed_at >= ?",
        (today,),
    ).fetchone()[0]

    conn.close()

    return {
        "status_counts": status_counts,
        "queued_count": status_counts.get("queued", 0),
        "in_progress_count": status_counts.get("in_progress", 0),
        "completed_count": status_counts.get("completed", 0),
        "failed_count": status_counts.get("failed", 0),
        "in_progress": [
            {
                "id": r["id"], "type": r["task_type"],
                "description": r["description"], "started_at": r["started_at"],
            }
            for r in in_progress
        ],
        "next_queued": [
            {
                "id": r["id"], "type": r["task_type"],
                "description": r["description"], "priority": r["priority"],
            }
            for r in queued
        ],
        "today_completed": today_completed,
        "today_failed": today_failed,
    }


def get_task_stats(days: int = 7) -> dict:
    """Completion stats over the last N days."""
    conn = _get_conn()
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    # Overall stats in period
    total = conn.execute(
        "SELECT COUNT(*) FROM autonomous_tasks WHERE created_at >= ?", (cutoff,)
    ).fetchone()[0]
    completed = conn.execute(
        "SELECT COUNT(*) FROM autonomous_tasks "
        "WHERE status='completed' AND created_at >= ?", (cutoff,)
    ).fetchone()[0]
    failed = conn.execute(
        "SELECT COUNT(*) FROM autonomous_tasks "
        "WHERE status='failed' AND created_at >= ?", (cutoff,)
    ).fetchone()[0]

    # Average duration of completed tasks
    avg_dur = conn.execute(
        "SELECT AVG(duration_seconds) FROM autonomous_tasks "
        "WHERE status='completed' AND duration_seconds > 0 AND created_at >= ?",
        (cutoff,),
    ).fetchone()[0] or 0.0

    # Per-type breakdown
    by_type = {}
    for row in conn.execute(
        "SELECT task_type, status, COUNT(*) as cnt "
        "FROM autonomous_tasks WHERE created_at >= ? "
        "GROUP BY task_type, status ORDER BY task_type",
        (cutoff,),
    ).fetchall():
        tt = row["task_type"]
        if tt not in by_type:
            by_type[tt] = {"total": 0, "completed": 0, "failed": 0}
        by_type[tt]["total"] += row["cnt"]
        if row["status"] == "completed":
            by_type[tt]["completed"] = row["cnt"]
        elif row["status"] == "failed":
            by_type[tt]["failed"] = row["cnt"]

    conn.close()

    completion_rate = round(completed / total * 100, 1) if total > 0 else 0

    return {
        "period_days": days,
        "total_tasks": total,
        "completed": completed,
        "failed": failed,
        "completion_rate": completion_rate,
        "avg_duration_seconds": round(avg_dur, 2),
        "by_type": by_type,
    }


# ═════════════════════════════════════════════════════════════════════════════
# DAILY TASK POPULATION
# ═════════════════════════════════════════════════════════════════════════════

def populate_daily_tasks() -> int:
    """
    Add all standard daily tasks to the queue.

    Called at midnight PT to ensure Nexus always has work.
    Skips tasks that are already queued or in_progress for today.
    Returns number of tasks added.
    """
    conn = _get_conn()
    today = _today_str()

    # Check which task types are already queued/running today
    existing = set()
    for row in conn.execute(
        "SELECT DISTINCT task_type FROM autonomous_tasks "
        "WHERE status IN ('queued', 'in_progress') AND created_at >= ?",
        (today,),
    ).fetchall():
        existing.add(row["task_type"])

    conn.close()

    daily_tasks = [
        ("lead_notification", "Process pending lead notifications"),
        ("lead_reply_check", "Check for new lead replies across all channels"),
        ("ad_monitor", "Monitor ad performance and spend"),
        ("group_scrape", "Scrape Facebook groups for new leads"),
        ("lead_analysis", "Analyze today's lead interactions and pipeline health"),
        ("competitor_check", "Check competitor activity and pricing"),
        ("content_generation", "Generate tomorrow's social media posts"),
        ("video_generation", "Generate scheduled video content"),
        ("venue_research", "Research new venues for partnership outreach"),
        ("knowledge_analysis", "Run knowledge base analysis and updates"),
        ("system_health", "Run system health checks on all services"),
        ("db_optimization", "Database maintenance: vacuum, analyze, cleanup"),
    ]

    added = 0
    for task_type, description in daily_tasks:
        if task_type not in existing:
            queue_task(task_type, description, source="schedule")
            added += 1

    _log(f"Daily tasks populated: {added} added, {len(existing)} already queued")
    return added


# ═════════════════════════════════════════════════════════════════════════════
# NATURAL LANGUAGE CLASSIFICATION
# ═════════════════════════════════════════════════════════════════════════════

# Keyword → task_type mapping for classify_request
_CLASSIFICATION_RULES = [
    # (patterns, task_type, category)
    (r"lead|prospect|contact|follow.?up|reply|respond", "lead_notification", "leads"),
    (r"ad\b|ads\b|campaign|spend|performance|facebook ad|meta ad|cpc|cpm|roas",
     "ad_monitor", "advertising"),
    (r"group|scrape|facebook group|fb group", "group_scrape", "scraping"),
    (r"competitor|competition|rival|pricing.?compare|market.?intel",
     "competitor_check", "competitive"),
    (r"post|content|caption|social.?media|instagram|facebook post|schedule.?post",
     "content_generation", "content"),
    (r"video|reel|tiktok|clip|footage|render", "video_generation", "video"),
    (r"venue|location|event.?space|banquet|reception.?hall|wedding.?venue",
     "venue_research", "research"),
    (r"planner|coordinator|event.?planner|wedding.?planner",
     "venue_research", "research"),
    (r"knowledge|analyze.?data|insight|report|analytics|stats",
     "knowledge_analysis", "analytics"),
    (r"health|status|service|uptime|monitor|check.?system",
     "system_health", "system"),
    (r"database|db|cleanup|vacuum|optimize|maintenance|backup",
     "db_optimization", "system"),
    (r"analyz|analysis|review.?lead|pipeline.?report", "lead_analysis", "analytics"),
]


def classify_request(message: str) -> dict:
    """
    Route a natural language message to the right task handler.

    Returns:
        {
            "category": str,     — broad category (leads, advertising, etc.)
            "task_type": str,    — matching TASK_TYPES key
            "extracted_data": {} — any extracted entities (names, URLs, etc.)
            "confidence": float  — 0.0 to 1.0
        }
    """
    msg_lower = message.lower().strip()
    extracted_data = {}

    # Extract URLs
    urls = re.findall(r"https?://\S+", message)
    if urls:
        extracted_data["urls"] = urls

    # Extract names (quoted strings)
    quoted = re.findall(r'"([^"]+)"', message)
    if quoted:
        extracted_data["names"] = quoted

    # Match against classification rules
    best_match = None
    best_score = 0

    for pattern, task_type, category in _CLASSIFICATION_RULES:
        matches = re.findall(pattern, msg_lower)
        score = len(matches)
        if score > best_score:
            best_score = score
            best_match = (task_type, category)

    if best_match:
        task_type, category = best_match
        confidence = min(1.0, best_score * 0.3 + 0.4)
        return {
            "category": category,
            "task_type": task_type,
            "extracted_data": extracted_data,
            "confidence": round(confidence, 2),
        }

    # Default: knowledge analysis (catch-all for unknown requests)
    return {
        "category": "general",
        "task_type": "knowledge_analysis",
        "extracted_data": extracted_data,
        "confidence": 0.2,
    }


# ═════════════════════════════════════════════════════════════════════════════
# TELEGRAM FORMATTING
# ═════════════════════════════════════════════════════════════════════════════

def format_task_summary() -> str:
    """Telegram-formatted summary of the task queue."""
    status = get_queue_status()
    stats = get_task_stats(days=1)

    msg = (
        "\U0001f4cb TASK QUEUE\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
    )

    # Current work
    if status["in_progress"]:
        msg += "\u26a1 In Progress:\n"
        for task in status["in_progress"]:
            icon = TASK_TYPES.get(task["type"], {}).get("icon", "\u2699\ufe0f")
            msg += f"  {icon} {task['description']}\n"
        msg += "\n"
    else:
        msg += "\u23f8\ufe0f No task running\n\n"

    # Queue
    msg += f"\U0001f4e5 Queue: {status['queued_count']} tasks waiting\n"
    if status["next_queued"]:
        for task in status["next_queued"][:5]:
            icon = TASK_TYPES.get(task["type"], {}).get("icon", "\u2699\ufe0f")
            msg += f"  {icon} P{task['priority']} \u2014 {task['description']}\n"
        if status["queued_count"] > 5:
            msg += f"  ... and {status['queued_count'] - 5} more\n"
    msg += "\n"

    # Today's stats
    msg += (
        f"\U0001f4ca Today:\n"
        f"  \u2705 Completed: {status['today_completed']}\n"
        f"  \u274c Failed: {status['today_failed']}\n"
    )

    if stats["avg_duration_seconds"] > 0:
        avg = stats["avg_duration_seconds"]
        if avg >= 60:
            msg += f"  \u23f1\ufe0f Avg duration: {avg / 60:.1f}m\n"
        else:
            msg += f"  \u23f1\ufe0f Avg duration: {avg:.0f}s\n"

    return msg


# ═════════════════════════════════════════════════════════════════════════════
# TELEGRAM COMMAND HANDLERS
# ═════════════════════════════════════════════════════════════════════════════

def handle_tasks_command(text: str) -> str:
    """
    /tasks — Show queue and current work.
    /tasks stats — Show 7-day completion stats.
    /tasks all — Show all task types.
    """
    stripped = text.strip()
    for prefix in ["/tasks ", "/tasks"]:
        if stripped.lower().startswith(prefix):
            stripped = stripped[len(prefix):].strip()
            break

    if stripped.lower() == "stats":
        stats = get_task_stats(days=7)
        msg = (
            "\U0001f4ca TASK STATS (7 days)\n"
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            f"Total: {stats['total_tasks']}\n"
            f"Completed: {stats['completed']} ({stats['completion_rate']}%)\n"
            f"Failed: {stats['failed']}\n"
        )
        if stats["avg_duration_seconds"] > 0:
            msg += f"Avg duration: {stats['avg_duration_seconds']:.1f}s\n"

        if stats["by_type"]:
            msg += "\nBy type:\n"
            for tt, data in sorted(stats["by_type"].items()):
                icon = TASK_TYPES.get(tt, {}).get("icon", "\u2699\ufe0f")
                rate = round(data["completed"] / data["total"] * 100) if data["total"] > 0 else 0
                msg += f"  {icon} {tt}: {data['completed']}/{data['total']} ({rate}%)\n"
        return msg

    if stripped.lower() == "all":
        msg = "\U0001f4cb ALL TASK TYPES\n\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        for tt, info in sorted(TASK_TYPES.items(), key=lambda x: x[1]["priority"]):
            msg += f"  {info['icon']} P{info['priority']} \u2014 {info['label']} ({tt})\n"
        return msg

    return format_task_summary()


def handle_do_command(text: str) -> str:
    """
    /do [request] — Queue a task from natural language.

    Examples:
      /do check competitor pricing
      /do research The 1909 venue
      /do generate tomorrow's posts
    """
    stripped = text.strip()
    for prefix in ["/do "]:
        if stripped.lower().startswith(prefix):
            stripped = stripped[len(prefix):].strip()
            break

    if not stripped:
        return (
            "\U0001f4cb Quick Task\n"
            "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            "Usage: /do [what you need]\n\n"
            "Examples:\n"
            "  /do check competitor pricing\n"
            "  /do research The 1909 venue\n"
            "  /do generate tomorrow's posts\n"
            "  /do run system health check\n"
            "  /do analyze this week's leads"
        )

    classification = classify_request(stripped)
    task_type = classification["task_type"]
    category = classification["category"]
    confidence = classification["confidence"]
    info = TASK_TYPES.get(task_type, {})
    icon = info.get("icon", "\u2699\ufe0f")
    label = info.get("label", task_type)

    task_id = queue_task(
        task_type=task_type,
        description=stripped,
        source="telegram",
    )

    conf_bar = "\u2588" * int(confidence * 10) + "\u2591" * (10 - int(confidence * 10))

    return (
        f"\u2705 Task Queued\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"{icon} {label}\n"
        f"\U0001f194 Task #{task_id}\n"
        f"\U0001f3af Category: {category}\n"
        f"\U0001f4ca Confidence: {conf_bar} {confidence:.0%}\n\n"
        f"\"{stripped}\"\n\n"
        f"Will execute in priority order."
    )


# ═════════════════════════════════════════════════════════════════════════════
# TASK EXECUTORS
# ═════════════════════════════════════════════════════════════════════════════

async def _execute_task(task: dict, send_fn=None) -> str:
    """
    Execute a single task by type. Returns result string.

    Each task type tries to import and call the relevant module.
    If the module isn't available, returns a graceful skip message.

    IMPORTANT: No outbound messages are auto-sent. All actions
    that would contact a lead require approval.
    """
    task_type = task["task_type"]
    description = task.get("description", "")
    _log(f"Executing: [{task_type}] {description}")

    try:
        if task_type == "lead_notification":
            try:
                from core.lead_pipeline import _pipeline
                if _pipeline:
                    pending = _pipeline._get_pending_leads()
                    return f"Checked lead notifications: {len(pending)} pending"
                return "Lead pipeline not initialized"
            except ImportError:
                return "Lead pipeline module not available"

        elif task_type == "lead_reply_check":
            try:
                from integrations.gmail_imap import check_inbox
                count = await check_inbox()
                return f"Checked inbox: {count} new messages"
            except (ImportError, Exception) as e:
                return f"Reply check: {e}"

        elif task_type == "ad_monitor":
            try:
                from core.ad_monitor import check_ad_performance
                result = await check_ad_performance()
                return f"Ad monitor: {json.dumps(result)[:500]}"
            except (ImportError, Exception) as e:
                return f"Ad monitor: {e}"

        elif task_type == "group_scrape":
            return "Group scrape: requires browser automation approval"

        elif task_type == "lead_analysis":
            try:
                from core.lead_scoring import get_scoring_summary
                summary = get_scoring_summary()
                return f"Lead analysis complete: {summary.get('total_leads', 0)} leads scored"
            except (ImportError, Exception) as e:
                return f"Lead analysis: {e}"

        elif task_type == "competitor_check":
            try:
                from core.competitor_monitor import scan_all_competitors
                results = await scan_all_competitors(send_fn)
                alerts = sum(len(r.get("alerts_generated", [])) for r in results)
                return f"Competitor check: {len(results)} scanned, {alerts} alerts"
            except (ImportError, Exception) as e:
                return f"Competitor check: {e}"

        elif task_type == "content_generation":
            return "Content generation: queued for AI processing (requires approval)"

        elif task_type == "video_generation":
            try:
                from integrations.higgsfield import get_queue_status as hf_status
                status = hf_status()
                return f"Video generation: {json.dumps(status)[:500]}"
            except (ImportError, Exception) as e:
                return f"Video generation: {e}"

        elif task_type == "venue_research":
            try:
                from integrations.browser_agent import is_playwright_available
                if is_playwright_available():
                    return "Venue research: browser ready, awaiting specific venue names"
                return "Venue research: playwright not installed"
            except (ImportError, Exception) as e:
                return f"Venue research: {e}"

        elif task_type == "knowledge_analysis":
            try:
                from core.knowledge_engine import get_knowledge_stats
                stats = get_knowledge_stats()
                return f"Knowledge analysis: {json.dumps(stats)[:500]}"
            except (ImportError, Exception) as e:
                return f"Knowledge analysis: {e}"

        elif task_type == "system_health":
            try:
                from core.watchdog import get_health_check
                health = get_health_check()
                return (
                    f"System health: {health['status']} "
                    f"({health['healthy_count']}/{health['total_services']} services up)"
                )
            except (ImportError, Exception) as e:
                return f"System health: {e}"

        elif task_type == "db_optimization":
            try:
                conn = _get_conn()
                conn.execute("PRAGMA optimize")

                # Clean up old completed tasks (keep last 30 days)
                cutoff = (datetime.utcnow() - timedelta(days=30)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                deleted = conn.execute(
                    "DELETE FROM autonomous_tasks "
                    "WHERE status IN ('completed', 'cancelled') AND completed_at < ?",
                    (cutoff,),
                ).rowcount
                conn.commit()

                # ANALYZE for query optimizer
                conn.execute("ANALYZE")
                conn.commit()
                conn.close()

                return f"DB optimization: ANALYZE done, {deleted} old tasks cleaned"
            except Exception as e:
                return f"DB optimization: {e}"

        else:
            return f"Unknown task type: {task_type}"

    except Exception as e:
        _log(f"Task execution error: {e}")
        traceback.print_exc()
        return f"Error: {e}"


# ═════════════════════════════════════════════════════════════════════════════
# SCHEDULER
# ═════════════════════════════════════════════════════════════════════════════

async def run_task_queue_scheduler(send_fn=None):
    """
    Continuous task queue scheduler.

    Loop:
      1. Check for next queued task
      2. Execute it, log result
      3. Heartbeat every 60s
      4. Populate daily tasks at midnight PT
      5. Repeat forever

    send_fn: async function to send Telegram notifications.
    """
    try:
        from core.watchdog import heartbeat
    except ImportError:
        def heartbeat(_):
            pass

    _log("Task queue scheduler started")
    last_heartbeat = 0
    last_daily_date = ""

    while True:
        try:
            now = time.time()

            # Heartbeat
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                heartbeat("task_queue")
                last_heartbeat = now

            # Daily task population at midnight PT
            today = _today_str()
            now_pt = _now_pt()
            if now_pt.hour == DAILY_RESET_HOUR and today != last_daily_date:
                _log("Midnight — populating daily tasks")
                last_daily_date = today
                added = populate_daily_tasks()
                if send_fn and added > 0:
                    try:
                        await send_fn(
                            f"\U0001f305 Daily tasks loaded: {added} tasks queued\n"
                            f"Nexus is ready to work."
                        )
                    except Exception as e:
                        _log(f"Failed to send daily notification: {e}")

            # Timeout stale in_progress tasks
            conn = _get_conn()
            stale_cutoff = (
                datetime.utcnow() - timedelta(seconds=TASK_TIMEOUT)
            ).strftime("%Y-%m-%d %H:%M:%S")
            stale = conn.execute(
                "SELECT id, task_type FROM autonomous_tasks "
                "WHERE status='in_progress' AND started_at < ?",
                (stale_cutoff,),
            ).fetchall()
            for row in stale:
                _log(f"Timing out stale task #{row['id']} ({row['task_type']})")
                conn.execute(
                    "UPDATE autonomous_tasks SET status='failed', "
                    "result='FAILED: Timed out', completed_at=? WHERE id=?",
                    (_now_str(), row["id"]),
                )
            if stale:
                conn.commit()
            conn.close()

            # Pick and execute next task
            task = get_next_task()
            if task:
                task_id = task["id"]
                task_type = task["task_type"]
                info = TASK_TYPES.get(task_type, {})
                icon = info.get("icon", "\u2699\ufe0f")

                if not start_task(task_id):
                    _log(f"Task #{task_id} already started by another worker")
                    await asyncio.sleep(2)
                    continue

                _log(f"Started: {icon} [{task_type}] {task['description']}")
                start_time = time.time()

                try:
                    result = await asyncio.wait_for(
                        _execute_task(task, send_fn),
                        timeout=TASK_TIMEOUT,
                    )
                    elapsed = time.time() - start_time
                    complete_task(task_id, result)
                    _log(
                        f"Completed: {icon} [{task_type}] in {elapsed:.1f}s — {result[:100]}"
                    )
                except asyncio.TimeoutError:
                    fail_task(task_id, "Execution timed out")
                    _log(f"Timeout: {icon} [{task_type}] after {TASK_TIMEOUT}s")
                except Exception as e:
                    fail_task(task_id, str(e))
                    _log(f"Failed: {icon} [{task_type}] — {e}")
                    traceback.print_exc()

                # Short pause between tasks
                await asyncio.sleep(2)
            else:
                # No tasks — wait before checking again
                await asyncio.sleep(SCHEDULER_INTERVAL)

        except Exception as e:
            _log(f"Scheduler error: {e}")
            traceback.print_exc()
            await asyncio.sleep(SCHEDULER_INTERVAL)
