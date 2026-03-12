"""
Agent Hierarchy — 4-Division commander structure for Zoar operations.

Structure:
  Master Coordinator (root)
    ├── Lead Generation Commander
    │   ├── Facebook Lead Monitor
    │   ├── Vendor Scraper
    │   ├── Lead Qualifier
    │   └── CRM Updater
    ├── Ad Intelligence Commander
    │   ├── Ad Performance Monitor
    │   ├── Creative Analyzer
    │   ├── Budget Optimizer
    │   └── Audience Researcher
    ├── Vendor Outreach Commander
    │   ├── Email Composer
    │   ├── Email Sender
    │   ├── Reply Monitor
    │   └── Follow-up Agent
    └── System Operations Commander
        ├── Health Monitor
        ├── API Key Manager
        ├── Database Cleaner
        └── Error Logger

Each agent maps to a real task type in the fleet queue.
"""

import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("agent_hierarchy")
DB_PATH = Path.home() / ".nexus" / "memory.db"

# ── Department & Agent Definitions ───────────────────────────────────────────

DEPARTMENTS = [
    {
        "id": "lead_gen",
        "name": "Lead Generation Commander",
        "model": "Groq",
        "description": "Finds leads, qualifies them, updates CRM",
        "agents": [
            {"id": "fb-lead-monitor", "name": "Facebook Lead Monitor", "model": "Groq", "task_type": "lead_monitor",
             "description": "Pulls new leads from FB Lead API every 15min"},
            {"id": "vendor-scraper", "name": "Vendor Scraper", "model": "Groq", "task_type": "vendor_research",
             "description": "Discovers new SFV vendors continuously"},
            {"id": "lead-qualifier", "name": "Lead Qualifier", "model": "Cerebras", "task_type": "lead_scoring",
             "description": "Scores new leads 1-10, flags hot leads"},
            {"id": "crm-updater", "name": "CRM Updater", "model": "Groq", "task_type": "crm_update",
             "description": "Syncs lead data, updates statuses"},
        ],
    },
    {
        "id": "ad_intel",
        "name": "Ad Intelligence Commander",
        "model": "Gemini",
        "description": "Monitors ads, optimizes budget, researches audience",
        "agents": [
            {"id": "ad-perf-monitor", "name": "Ad Performance Monitor", "model": "Gemini", "task_type": "ad_monitoring",
             "description": "Pulls FB Ads data every 6h, alerts on high CPL"},
            {"id": "creative-analyzer", "name": "Creative Analyzer", "model": "ZAI", "task_type": "creative_writing",
             "description": "Analyzes ad copy performance, generates variations"},
            {"id": "budget-optimizer", "name": "Budget Optimizer", "model": "Gemini", "task_type": "ad_optimization",
             "description": "Daily budget reallocation recommendations"},
            {"id": "audience-researcher", "name": "Audience Researcher", "model": "Gemini", "task_type": "audience_research",
             "description": "Daily market research on SFV wedding trends"},
        ],
    },
    {
        "id": "vendor_outreach",
        "name": "Vendor Outreach Commander",
        "model": "Groq",
        "description": "Composes emails, sends outreach, monitors replies",
        "agents": [
            {"id": "email-composer", "name": "Email Composer", "model": "Groq", "task_type": "email_drafting",
             "description": "Generates personalized cold emails from templates"},
            {"id": "email-sender", "name": "Email Sender", "model": "System", "task_type": "email_sending",
             "description": "Sends max 50 emails/day, 8am-6pm PT"},
            {"id": "reply-monitor", "name": "Reply Monitor", "model": "System", "task_type": "reply_checking",
             "description": "Checks Gmail IMAP every 30min for vendor replies"},
            {"id": "followup-agent", "name": "Follow-up Agent", "model": "Groq", "task_type": "follow_up",
             "description": "Drafts re-engagement for 48h+ silent vendors"},
        ],
    },
    {
        "id": "sys_ops",
        "name": "System Operations Commander",
        "model": "Ollama",
        "description": "Health monitoring, key management, cleanup, error logging",
        "agents": [
            {"id": "health-monitor", "name": "Health Monitor", "model": "System", "task_type": "health_scan",
             "description": "Full system scan every 10min"},
            {"id": "api-key-manager", "name": "API Key Manager", "model": "System", "task_type": "key_rotation",
             "description": "Rotates keys, tracks quotas, reports health"},
            {"id": "db-cleaner", "name": "Database Cleaner", "model": "Ollama", "task_type": "db_maintenance",
             "description": "Prunes old logs, optimizes tables weekly"},
            {"id": "error-logger", "name": "Error Logger", "model": "System", "task_type": "error_logging",
             "description": "Aggregates errors, sends critical alerts"},
        ],
    },
]


def _db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_hierarchy_db():
    """Create tables for inter-agent task queue and communication."""
    conn = _db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agent_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_agent TEXT NOT NULL,
            from_dept TEXT NOT NULL,
            to_agent TEXT NOT NULL,
            to_dept TEXT NOT NULL,
            task_type TEXT DEFAULT 'request',
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            priority TEXT DEFAULT 'normal',
            status TEXT DEFAULT 'pending',
            result TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            started_at TEXT DEFAULT '',
            completed_at TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_agent_tasks_to ON agent_tasks(to_dept, status);
        CREATE INDEX IF NOT EXISTS idx_agent_tasks_status ON agent_tasks(status);

        CREATE TABLE IF NOT EXISTS agent_comms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_agent TEXT NOT NULL,
            from_dept TEXT NOT NULL,
            to_agent TEXT DEFAULT '',
            to_dept TEXT DEFAULT '',
            message_type TEXT DEFAULT 'info',
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_agent_comms_ts ON agent_comms(created_at DESC);

        CREATE TABLE IF NOT EXISTS agent_status (
            agent_id TEXT PRIMARY KEY,
            dept_id TEXT NOT NULL,
            status TEXT DEFAULT 'idle',
            current_task TEXT DEFAULT '',
            progress_pct INTEGER DEFAULT 0,
            tasks_today INTEGER DEFAULT 0,
            runtime_seconds INTEGER DEFAULT 0,
            last_active TEXT DEFAULT '',
            error_message TEXT DEFAULT ''
        );
    """)
    conn.commit()
    conn.close()
    log.info("[Hierarchy] Database tables ready")


# ── Agent Status ─────────────────────────────────────────────────────────────

def update_agent_status(agent_id: str, dept_id: str, status: str = "active",
                        current_task: str = "", progress_pct: int = 0,
                        error_message: str = ""):
    """Update an agent's real-time status."""
    conn = _db()
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO agent_status (agent_id, dept_id, status, current_task, progress_pct, last_active, error_message)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(agent_id) DO UPDATE SET
            status = excluded.status,
            current_task = excluded.current_task,
            progress_pct = excluded.progress_pct,
            last_active = excluded.last_active,
            error_message = excluded.error_message,
            tasks_today = CASE
                WHEN excluded.status = 'completed' THEN agent_status.tasks_today + 1
                ELSE agent_status.tasks_today
            END
    """, (agent_id, dept_id, status, current_task, progress_pct, now, error_message))
    conn.commit()
    conn.close()


def get_all_status():
    """Get status for all agents."""
    conn = _db()
    rows = conn.execute("SELECT * FROM agent_status").fetchall()
    conn.close()
    return {r["agent_id"]: dict(r) for r in rows}


# ── Inter-Agent Task Queue ───────────────────────────────────────────────────

def create_task(from_agent: str, from_dept: str, to_agent: str, to_dept: str,
                title: str, description: str = "", priority: str = "normal") -> int:
    """Create a task from one agent to another."""
    conn = _db()
    now = datetime.utcnow().isoformat()
    cur = conn.execute("""
        INSERT INTO agent_tasks (from_agent, from_dept, to_agent, to_dept, title, description, priority, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (from_agent, from_dept, to_agent, to_dept, title, description, priority, now))
    task_id = cur.lastrowid
    conn.commit()
    conn.close()

    # Log the communication
    log_comm(from_agent, from_dept, to_agent, to_dept, "task",
             f"[Task #{task_id}] {title}")
    return task_id


def get_department_queue(dept_id: str):
    """Get pending tasks for a department."""
    conn = _db()
    rows = conn.execute(
        "SELECT * FROM agent_tasks WHERE to_dept = ? AND status = 'pending' ORDER BY priority DESC, created_at ASC",
        (dept_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def complete_task(task_id: int, result: str = ""):
    """Mark a task as completed."""
    conn = _db()
    now = datetime.utcnow().isoformat()
    conn.execute(
        "UPDATE agent_tasks SET status = 'completed', result = ?, completed_at = ? WHERE id = ?",
        (result, now, task_id)
    )
    conn.commit()
    conn.close()


# ── Communication Feed ───────────────────────────────────────────────────────

def log_comm(from_agent: str, from_dept: str, to_agent: str = "",
             to_dept: str = "", msg_type: str = "info", message: str = ""):
    """Log an inter-agent communication."""
    conn = _db()
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO agent_comms (from_agent, from_dept, to_agent, to_dept, message_type, message, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (from_agent, from_dept, to_agent, to_dept, msg_type, message, now))
    conn.commit()
    conn.close()


def get_recent_comms(limit: int = 50):
    """Get recent inter-agent communications."""
    conn = _db()
    rows = conn.execute(
        "SELECT * FROM agent_comms ORDER BY created_at DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Hierarchy Tree API ───────────────────────────────────────────────────────

def get_hierarchy_tree():
    """Build the full hierarchy tree with live status and token data."""
    statuses = get_all_status()

    # Token usage per agent (today)
    agent_tokens = {}
    try:
        conn = _db()
        rows = conn.execute(
            "SELECT agent_id, COUNT(*) as calls, COALESCE(SUM(tokens_total),0) as tokens "
            "FROM token_usage WHERE DATE(created_at) = DATE('now') AND agent_id != '' "
            "GROUP BY agent_id"
        ).fetchall()
        conn.close()
        for r in rows:
            agent_tokens[r["agent_id"]] = {"calls": r["calls"], "tokens": r["tokens"]}
    except Exception:
        pass

    tree = {
        "id": "master-coordinator",
        "name": "Master Coordinator",
        "model": "Orchestrator",
        "description": "Central autonomous work engine — 4 divisions",
        "status": "running",
        "divisions": [],
    }

    for dept in DEPARTMENTS:
        div_node = {
            "id": dept["id"],
            "name": dept["name"],
            "model": dept["model"],
            "description": dept["description"],
            "agents": [],
        }

        div_has_active = False
        for agent in dept["agents"]:
            agent_status = statuses.get(agent["id"], {})
            status = agent_status.get("status", "idle")
            if status in ("active", "running"):
                div_has_active = True

            tok = agent_tokens.get(agent["id"], {"calls": 0, "tokens": 0})

            div_node["agents"].append({
                "id": agent["id"],
                "name": agent["name"],
                "model": agent.get("model", dept["model"]),
                "taskType": agent.get("task_type", ""),
                "description": agent["description"],
                "status": status,
                "currentTask": agent_status.get("current_task", ""),
                "tasksToday": agent_status.get("tasks_today", 0),
                "tokensToday": tok["tokens"],
                "callsToday": tok["calls"],
                "lastActive": agent_status.get("last_active", ""),
                "errorMessage": agent_status.get("error_message", ""),
            })

        div_node["status"] = "active" if div_has_active else "idle"
        tree["divisions"].append(div_node)

    return tree


def get_agent_comms(agent_id, limit=10):
    """Get recent communications for a specific agent."""
    conn = _db()
    rows = conn.execute(
        "SELECT * FROM agent_comms WHERE from_agent = ? OR to_agent = ? "
        "ORDER BY created_at DESC LIMIT ?",
        (agent_id, agent_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
