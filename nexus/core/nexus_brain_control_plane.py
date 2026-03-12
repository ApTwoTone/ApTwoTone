"""
Nexus Brain Control Plane
-------------------------

Foundational runtime substrate for Nexus Brain:
  - Agent registry + heartbeat
  - Task queue + claim/complete/fail lifecycle
  - Safety approvals for risky tasks
  - Structured memory (episodic/semantic/strategic/procedural)
  - Evaluation ledger
  - Experiment registry + runs + lesson extraction

This module is intentionally local-first and dependency-light (SQLite only).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

log = logging.getLogger("nexus_brain_control_plane")

DB_PATH = Path.home() / ".nexus" / "nexus_brain.db"

MEMORY_TYPES = {"episodic", "semantic", "strategic", "procedural"}
TASK_STATUSES = {
    "queued",
    "blocked_approval",
    "claimed",
    "running",
    "completed",
    "failed",
    "cancelled",
}
APPROVAL_STATUSES = {"not_required", "pending", "approved", "rejected"}

DEFAULT_BRAIN_AGENTS: List[Dict[str, Any]] = [
    {
        "agent_id": "brain_supervisor",
        "role": "supervisor",
        "lane": "core_ops",
        "capabilities": [
            "plan",
            "prioritize",
            "delegate",
            "safety_gate",
            "final_decision_support",
        ],
    },
    {
        "agent_id": "ads_intelligence_agent",
        "role": "analyst",
        "lane": "ads_intel",
        "capabilities": [
            "ad_diagnostics",
            "creative_scoring",
            "hook_analysis",
            "audience_hypothesis",
        ],
    },
    {
        "agent_id": "lead_intelligence_agent",
        "role": "analyst",
        "lane": "lead_ops",
        "capabilities": [
            "lead_quality_scoring",
            "venue_fit_scoring",
            "contactability_checks",
            "outreach_angle_selection",
        ],
    },
    {
        "agent_id": "experiment_critic_agent",
        "role": "critic",
        "lane": "experiments",
        "capabilities": [
            "experiment_design",
            "result_scoring",
            "lesson_extraction",
            "failure_pattern_detection",
        ],
    },
    {
        "agent_id": "coding_repair_agent",
        "role": "engineer",
        "lane": "systems",
        "capabilities": [
            "diagnostics",
            "patch_proposal",
            "test_generation",
            "safe_refactor",
        ],
    },
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(v: Any, default: str = "") -> str:
    try:
        return str(v if v is not None else default).strip()
    except Exception:
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return int(default)


def _to_json(v: Any) -> str:
    return json.dumps(v if v is not None else {}, ensure_ascii=False)


def _from_json(v: Any, default: Any) -> Any:
    try:
        raw = _safe_text(v)
        if not raw:
            return default
        parsed = json.loads(raw)
        return parsed if parsed is not None else default
    except Exception:
        return default


def _score_to_grade(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


class NexusBrainControlPlane:
    def __init__(self) -> None:
        self._ensure_schema()
        self.bootstrap_default_agents()

    def _conn(self) -> sqlite3.Connection:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _ensure_schema(self) -> None:
        conn = self._conn()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS brain_agents (
                    agent_id TEXT PRIMARY KEY,
                    role TEXT NOT NULL,
                    lane TEXT DEFAULT 'core_ops',
                    parent_agent_id TEXT DEFAULT '',
                    status TEXT DEFAULT 'idle',
                    capabilities_json TEXT DEFAULT '[]',
                    config_json TEXT DEFAULT '{}',
                    current_task_id INTEGER DEFAULT 0,
                    heartbeat_at TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS brain_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_type TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    lane TEXT DEFAULT 'core_ops',
                    priority INTEGER DEFAULT 50,
                    status TEXT DEFAULT 'queued',
                    payload_json TEXT DEFAULT '{}',
                    constraints_json TEXT DEFAULT '{}',
                    parent_task_id INTEGER DEFAULT 0,
                    assigned_agent_id TEXT DEFAULT '',
                    requires_approval INTEGER DEFAULT 0,
                    approval_status TEXT DEFAULT 'not_required',
                    risk_level TEXT DEFAULT 'low',
                    result_json TEXT DEFAULT '{}',
                    error_text TEXT DEFAULT '',
                    retry_count INTEGER DEFAULT 0,
                    scheduled_for TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now')),
                    started_at TEXT DEFAULT '',
                    finished_at TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS brain_approvals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    risk_level TEXT DEFAULT 'low',
                    reason TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    requested_by TEXT DEFAULT '',
                    decision_by TEXT DEFAULT '',
                    decision_note TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now')),
                    decided_at TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS brain_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_type TEXT NOT NULL,
                    scope TEXT DEFAULT 'zoar_core',
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    tags_json TEXT DEFAULT '[]',
                    source TEXT DEFAULT '',
                    confidence REAL DEFAULT 0.5,
                    importance REAL DEFAULT 0.5,
                    outcome_score REAL DEFAULT 0,
                    linked_entity_type TEXT DEFAULT '',
                    linked_entity_id TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now')),
                    last_used_at TEXT DEFAULT '',
                    use_count INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS brain_evaluations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_type TEXT NOT NULL,
                    target_id TEXT DEFAULT '',
                    metric_name TEXT NOT NULL,
                    score REAL NOT NULL,
                    grade TEXT DEFAULT '',
                    evaluator TEXT DEFAULT '',
                    reasoning TEXT DEFAULT '',
                    signals_json TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS brain_experiments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    lane TEXT DEFAULT 'experiments',
                    goal TEXT DEFAULT '',
                    hypothesis TEXT DEFAULT '',
                    metric_name TEXT DEFAULT '',
                    success_threshold REAL DEFAULT 0,
                    status TEXT DEFAULT 'draft',
                    config_json TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS brain_experiment_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id INTEGER NOT NULL,
                    status TEXT DEFAULT 'running',
                    started_at TEXT DEFAULT (datetime('now')),
                    ended_at TEXT DEFAULT '',
                    input_snapshot_json TEXT DEFAULT '{}',
                    result_json TEXT DEFAULT '{}',
                    score REAL DEFAULT 0,
                    lesson TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS brain_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    level TEXT DEFAULT 'info',
                    event_type TEXT DEFAULT '',
                    message TEXT DEFAULT '',
                    payload_json TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT (datetime('now'))
                );
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_brain_agents_lane ON brain_agents(lane)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_brain_tasks_status ON brain_tasks(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_brain_tasks_lane_status ON brain_tasks(lane, status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_brain_tasks_priority ON brain_tasks(priority, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_brain_memory_type_scope ON brain_memory(memory_type, scope)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_brain_eval_metric ON brain_evaluations(metric_name, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_brain_exp_status ON brain_experiments(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_brain_run_exp ON brain_experiment_runs(experiment_id, id DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_brain_events_created ON brain_events(created_at DESC)")

            # FTS memory index (best effort; falls back to LIKE search when unavailable).
            try:
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS brain_memory_fts
                    USING fts5(title, body, tags, content=brain_memory, content_rowid=id)
                    """
                )
                conn.executescript(
                    """
                    CREATE TRIGGER IF NOT EXISTS brain_memory_ai AFTER INSERT ON brain_memory BEGIN
                        INSERT INTO brain_memory_fts(rowid, title, body, tags)
                        VALUES (new.id, new.title, new.body, new.tags_json);
                    END;
                    CREATE TRIGGER IF NOT EXISTS brain_memory_ad AFTER DELETE ON brain_memory BEGIN
                        INSERT INTO brain_memory_fts(brain_memory_fts, rowid, title, body, tags)
                        VALUES ('delete', old.id, old.title, old.body, old.tags_json);
                    END;
                    CREATE TRIGGER IF NOT EXISTS brain_memory_au AFTER UPDATE ON brain_memory BEGIN
                        INSERT INTO brain_memory_fts(brain_memory_fts, rowid, title, body, tags)
                        VALUES ('delete', old.id, old.title, old.body, old.tags_json);
                        INSERT INTO brain_memory_fts(rowid, title, body, tags)
                        VALUES (new.id, new.title, new.body, new.tags_json);
                    END;
                    """
                )
            except Exception as e:
                log.debug("brain_memory_fts unavailable: %s", e)

            conn.commit()
        finally:
            conn.close()

    # ── Agent registry ──────────────────────────────────────────────────

    def bootstrap_default_agents(self) -> None:
        for item in DEFAULT_BRAIN_AGENTS:
            self.register_agent(
                agent_id=item["agent_id"],
                role=item["role"],
                lane=item.get("lane", "core_ops"),
                capabilities=item.get("capabilities", []),
                config={},
                parent_agent_id=item.get("parent_agent_id", ""),
            )

    def register_agent(
        self,
        *,
        agent_id: str,
        role: str,
        lane: str = "core_ops",
        capabilities: Optional[Sequence[str]] = None,
        config: Optional[Dict[str, Any]] = None,
        parent_agent_id: str = "",
        status: str = "idle",
    ) -> Dict[str, Any]:
        aid = _safe_text(agent_id)
        if not aid:
            return {"ok": False, "error": "agent_id required"}
        conn = self._conn()
        try:
            now = _now_iso()
            conn.execute(
                """
                INSERT INTO brain_agents
                (agent_id, role, lane, parent_agent_id, status, capabilities_json, config_json, heartbeat_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    role=excluded.role,
                    lane=excluded.lane,
                    parent_agent_id=excluded.parent_agent_id,
                    status=excluded.status,
                    capabilities_json=excluded.capabilities_json,
                    config_json=excluded.config_json,
                    heartbeat_at=excluded.heartbeat_at,
                    updated_at=excluded.updated_at
                """,
                (
                    aid,
                    _safe_text(role, "worker"),
                    _safe_text(lane, "core_ops"),
                    _safe_text(parent_agent_id),
                    _safe_text(status, "idle"),
                    _to_json(list(capabilities or [])),
                    _to_json(config or {}),
                    now,
                    now,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        self._log_event("info", "agent.register", f"Registered agent {aid}", {"lane": lane, "role": role})
        return {"ok": True, "agent_id": aid}

    def heartbeat(
        self,
        *,
        agent_id: str,
        status: str = "running",
        current_task_id: int = 0,
        detail: str = "",
    ) -> Dict[str, Any]:
        aid = _safe_text(agent_id)
        if not aid:
            return {"ok": False, "error": "agent_id required"}
        conn = self._conn()
        try:
            row = conn.execute("SELECT agent_id FROM brain_agents WHERE agent_id=?", (aid,)).fetchone()
            if not row:
                return {"ok": False, "error": "agent not found"}
            conn.execute(
                """
                UPDATE brain_agents
                SET status=?, current_task_id=?, heartbeat_at=?, updated_at=?
                WHERE agent_id=?
                """,
                (_safe_text(status, "running"), int(current_task_id or 0), _now_iso(), _now_iso(), aid),
            )
            conn.commit()
        finally:
            conn.close()
        if detail:
            self._log_event("info", "agent.heartbeat", f"{aid}: {detail}", {"status": status, "task_id": current_task_id})
        return {"ok": True}

    def list_agents(self, lane: str = "", status: str = "", limit: int = 200) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            where = ["1=1"]
            params: List[Any] = []
            if lane:
                where.append("lane=?")
                params.append(_safe_text(lane))
            if status:
                where.append("status=?")
                params.append(_safe_text(status))
            params.append(max(1, min(int(limit or 200), 500)))
            rows = conn.execute(
                f"""
                SELECT agent_id, role, lane, parent_agent_id, status, capabilities_json, config_json, current_task_id,
                       heartbeat_at, created_at, updated_at
                FROM brain_agents
                WHERE {' AND '.join(where)}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
            return [
                {
                    "agent_id": r["agent_id"],
                    "role": r["role"],
                    "lane": r["lane"],
                    "parent_agent_id": r["parent_agent_id"] or "",
                    "status": r["status"],
                    "capabilities": _from_json(r["capabilities_json"], []),
                    "config": _from_json(r["config_json"], {}),
                    "current_task_id": int(r["current_task_id"] or 0),
                    "heartbeat_at": r["heartbeat_at"] or "",
                    "created_at": r["created_at"] or "",
                    "updated_at": r["updated_at"] or "",
                }
                for r in rows
            ]
        finally:
            conn.close()

    # ── Task queue + approvals ──────────────────────────────────────────

    def enqueue_task(
        self,
        *,
        task_type: str,
        objective: str,
        lane: str = "core_ops",
        priority: int = 50,
        payload: Optional[Dict[str, Any]] = None,
        constraints: Optional[Dict[str, Any]] = None,
        parent_task_id: int = 0,
        assigned_agent_id: str = "",
        requires_approval: bool = False,
        risk_level: str = "low",
        scheduled_for: str = "",
        approval_reason: str = "",
        requested_by: str = "system",
    ) -> Dict[str, Any]:
        if not _safe_text(task_type):
            return {"ok": False, "error": "task_type required"}
        if not _safe_text(objective):
            return {"ok": False, "error": "objective required"}

        status = "blocked_approval" if bool(requires_approval) else "queued"
        approval_status = "pending" if bool(requires_approval) else "not_required"

        conn = self._conn()
        try:
            cur = conn.execute(
                """
                INSERT INTO brain_tasks
                (task_type, objective, lane, priority, status, payload_json, constraints_json, parent_task_id,
                 assigned_agent_id, requires_approval, approval_status, risk_level, scheduled_for, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _safe_text(task_type),
                    _safe_text(objective),
                    _safe_text(lane, "core_ops"),
                    max(1, min(int(priority or 50), 100)),
                    status,
                    _to_json(payload or {}),
                    _to_json(constraints or {}),
                    int(parent_task_id or 0),
                    _safe_text(assigned_agent_id),
                    1 if bool(requires_approval) else 0,
                    approval_status,
                    _safe_text(risk_level, "low"),
                    _safe_text(scheduled_for),
                    _now_iso(),
                ),
            )
            task_id = int(cur.lastrowid)
            approval_id = 0
            if requires_approval:
                a = conn.execute(
                    """
                    INSERT INTO brain_approvals
                    (task_id, risk_level, reason, status, requested_by, created_at)
                    VALUES (?, ?, ?, 'pending', ?, ?)
                    """,
                    (task_id, _safe_text(risk_level, "low"), _safe_text(approval_reason), _safe_text(requested_by), _now_iso()),
                )
                approval_id = int(a.lastrowid or 0)
            conn.commit()
        finally:
            conn.close()

        self._log_event(
            "info",
            "task.enqueue",
            f"Task #{task_id} queued: {task_type}",
            {"lane": lane, "requires_approval": bool(requires_approval), "approval_id": approval_id},
        )
        return {"ok": True, "task_id": task_id, "approval_id": approval_id}

    def approve_task(self, *, task_id: int, decision: str, decision_by: str, decision_note: str = "") -> Dict[str, Any]:
        dec = _safe_text(decision).lower()
        if dec not in {"approved", "rejected"}:
            return {"ok": False, "error": "decision must be approved|rejected"}
        tid = int(task_id or 0)
        if tid <= 0:
            return {"ok": False, "error": "task_id required"}

        conn = self._conn()
        try:
            task = conn.execute("SELECT id, status, requires_approval FROM brain_tasks WHERE id=?", (tid,)).fetchone()
            if not task:
                return {"ok": False, "error": "task not found"}
            if int(task["requires_approval"] or 0) != 1:
                return {"ok": False, "error": "task does not require approval"}

            approval = conn.execute(
                "SELECT id, status FROM brain_approvals WHERE task_id=? ORDER BY id DESC LIMIT 1",
                (tid,),
            ).fetchone()
            if not approval:
                return {"ok": False, "error": "approval record missing"}
            if _safe_text(approval["status"]).lower() != "pending":
                return {"ok": False, "error": f"approval already {approval['status']}"}

            now = _now_iso()
            conn.execute(
                "UPDATE brain_approvals SET status=?, decision_by=?, decision_note=?, decided_at=? WHERE id=?",
                (dec, _safe_text(decision_by), _safe_text(decision_note), now, int(approval["id"])),
            )
            next_status = "queued" if dec == "approved" else "cancelled"
            conn.execute(
                "UPDATE brain_tasks SET approval_status=?, status=?, finished_at=? WHERE id=?",
                (dec, next_status, now if dec == "rejected" else "", tid),
            )
            conn.commit()
        finally:
            conn.close()

        self._log_event(
            "info",
            "task.approval",
            f"Task #{tid} {dec}",
            {"decision_by": _safe_text(decision_by), "note": _safe_text(decision_note)[:240]},
        )
        return {"ok": True, "task_id": tid, "decision": dec}

    def claim_next_task(self, *, agent_id: str, lane: str = "", max_priority: int = 100) -> Dict[str, Any]:
        aid = _safe_text(agent_id)
        if not aid:
            return {"ok": False, "error": "agent_id required"}

        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            where = ["status='queued'", "priority <= ?"]
            params: List[Any] = [max(1, min(int(max_priority or 100), 100))]
            if lane:
                where.append("lane=?")
                params.append(_safe_text(lane))
            # scheduled_for empty or due
            where.append("(scheduled_for='' OR scheduled_for <= ?)")
            params.append(_now_iso())

            row = conn.execute(
                f"""
                SELECT *
                FROM brain_tasks
                WHERE {' AND '.join(where)}
                ORDER BY priority ASC, created_at ASC
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()
            if not row:
                conn.execute("COMMIT")
                return {"ok": True, "task": None}

            tid = int(row["id"])
            conn.execute(
                """
                UPDATE brain_tasks
                SET status='claimed', assigned_agent_id=?, started_at=?, approval_status=CASE WHEN approval_status='' THEN 'not_required' ELSE approval_status END
                WHERE id=? AND status='queued'
                """,
                (aid, _now_iso(), tid),
            )
            changed = conn.execute("SELECT changes()").fetchone()[0]
            if not changed:
                conn.execute("ROLLBACK")
                return {"ok": True, "task": None}
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

        self.heartbeat(agent_id=aid, status="running", current_task_id=tid, detail=f"Claimed task #{tid}")
        task = self.get_task(tid)
        return {"ok": True, "task": task}

    def complete_task(self, *, task_id: int, agent_id: str = "", result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        tid = int(task_id or 0)
        if tid <= 0:
            return {"ok": False, "error": "task_id required"}
        conn = self._conn()
        try:
            row = conn.execute("SELECT id, status, assigned_agent_id FROM brain_tasks WHERE id=?", (tid,)).fetchone()
            if not row:
                return {"ok": False, "error": "task not found"}
            if _safe_text(row["status"]) in {"completed", "failed", "cancelled"}:
                return {"ok": False, "error": f"task already {row['status']}"}
            conn.execute(
                "UPDATE brain_tasks SET status='completed', result_json=?, finished_at=? WHERE id=?",
                (_to_json(result or {}), _now_iso(), tid),
            )
            if agent_id:
                conn.execute(
                    "UPDATE brain_agents SET status='idle', current_task_id=0, heartbeat_at=?, updated_at=? WHERE agent_id=?",
                    (_now_iso(), _now_iso(), _safe_text(agent_id)),
                )
            conn.commit()
        finally:
            conn.close()

        self._log_event("info", "task.complete", f"Task #{tid} completed", {"agent_id": _safe_text(agent_id)})
        return {"ok": True, "task_id": tid}

    def fail_task(self, *, task_id: int, error: str, agent_id: str = "", retry: bool = False) -> Dict[str, Any]:
        tid = int(task_id or 0)
        if tid <= 0:
            return {"ok": False, "error": "task_id required"}
        next_status = "queued" if bool(retry) else "failed"
        conn = self._conn()
        try:
            row = conn.execute("SELECT id, retry_count FROM brain_tasks WHERE id=?", (tid,)).fetchone()
            if not row:
                return {"ok": False, "error": "task not found"}
            retry_count = int(row["retry_count"] or 0) + (1 if retry else 0)
            conn.execute(
                """
                UPDATE brain_tasks
                SET status=?, error_text=?, retry_count=?, finished_at=CASE WHEN ?='failed' THEN ? ELSE '' END
                WHERE id=?
                """,
                (next_status, _safe_text(error)[:1000], retry_count, next_status, _now_iso(), tid),
            )
            if agent_id:
                conn.execute(
                    "UPDATE brain_agents SET status='idle', current_task_id=0, heartbeat_at=?, updated_at=? WHERE agent_id=?",
                    (_now_iso(), _now_iso(), _safe_text(agent_id)),
                )
            conn.commit()
        finally:
            conn.close()

        lvl = "warn" if retry else "error"
        self._log_event(lvl, "task.fail", f"Task #{tid} {next_status}", {"error": _safe_text(error)[:300], "retry": bool(retry)})
        return {"ok": True, "task_id": tid, "status": next_status}

    def get_task(self, task_id: int) -> Optional[Dict[str, Any]]:
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM brain_tasks WHERE id=?", (int(task_id),)).fetchone()
            if not row:
                return None
            return self._task_row_to_dict(row)
        finally:
            conn.close()

    def list_tasks(self, *, status: str = "", lane: str = "", limit: int = 200) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            where = ["1=1"]
            params: List[Any] = []
            if status:
                where.append("status=?")
                params.append(_safe_text(status))
            if lane:
                where.append("lane=?")
                params.append(_safe_text(lane))
            params.append(max(1, min(int(limit or 200), 1000)))
            rows = conn.execute(
                f"SELECT * FROM brain_tasks WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?",
                tuple(params),
            ).fetchall()
            return [self._task_row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def list_approvals(self, *, status: str = "pending", limit: int = 200) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            where = ["1=1"]
            params: List[Any] = []
            if status:
                where.append("status=?")
                params.append(_safe_text(status))
            params.append(max(1, min(int(limit or 200), 500)))
            rows = conn.execute(
                f"SELECT * FROM brain_approvals WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?",
                tuple(params),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Memory ────────────────────────────────────────────────────────────

    def add_memory(
        self,
        *,
        memory_type: str,
        title: str,
        body: str,
        scope: str = "zoar_core",
        tags: Optional[Sequence[str]] = None,
        source: str = "",
        confidence: float = 0.5,
        importance: float = 0.5,
        outcome_score: float = 0.0,
        linked_entity_type: str = "",
        linked_entity_id: str = "",
    ) -> Dict[str, Any]:
        mtype = _safe_text(memory_type).lower()
        if mtype not in MEMORY_TYPES:
            return {"ok": False, "error": f"memory_type must be one of {sorted(MEMORY_TYPES)}"}
        if not _safe_text(title):
            return {"ok": False, "error": "title required"}
        if not _safe_text(body):
            return {"ok": False, "error": "body required"}
        conn = self._conn()
        try:
            cur = conn.execute(
                """
                INSERT INTO brain_memory
                (memory_type, scope, title, body, tags_json, source, confidence, importance, outcome_score,
                 linked_entity_type, linked_entity_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mtype,
                    _safe_text(scope, "zoar_core"),
                    _safe_text(title),
                    _safe_text(body),
                    _to_json([_safe_text(t) for t in (tags or []) if _safe_text(t)]),
                    _safe_text(source),
                    max(0.0, min(_safe_float(confidence, 0.5), 1.0)),
                    max(0.0, min(_safe_float(importance, 0.5), 1.0)),
                    max(-1.0, min(_safe_float(outcome_score, 0.0), 1.0)),
                    _safe_text(linked_entity_type),
                    _safe_text(linked_entity_id),
                    _now_iso(),
                    _now_iso(),
                ),
            )
            mid = int(cur.lastrowid)
            conn.commit()
        finally:
            conn.close()
        self._log_event("info", "memory.add", f"Memory #{mid} added", {"memory_type": mtype, "scope": scope})
        return {"ok": True, "memory_id": mid}

    def search_memory(
        self,
        *,
        query: str,
        memory_types: Optional[Sequence[str]] = None,
        scope: str = "",
        limit: int = 20,
        min_confidence: float = 0.0,
    ) -> List[Dict[str, Any]]:
        q = _safe_text(query)
        if not q:
            return []
        limit = max(1, min(int(limit or 20), 200))
        mtypes = [m.lower() for m in (memory_types or []) if _safe_text(m).lower() in MEMORY_TYPES]

        conn = self._conn()
        try:
            rows = None
            try:
                where = ["1=1"]
                params: List[Any] = []
                if mtypes:
                    where.append("m.memory_type IN (%s)" % ",".join("?" for _ in mtypes))
                    params.extend(mtypes)
                if scope:
                    where.append("m.scope=?")
                    params.append(_safe_text(scope))
                where.append("m.confidence >= ?")
                params.append(max(0.0, min(float(min_confidence or 0.0), 1.0)))
                params.extend([q, limit])
                rows = conn.execute(
                    f"""
                    SELECT m.*
                    FROM brain_memory m
                    JOIN brain_memory_fts f ON m.id = f.rowid
                    WHERE {' AND '.join(where)} AND brain_memory_fts MATCH ?
                    ORDER BY m.importance DESC, m.outcome_score DESC, m.use_count DESC, m.id DESC
                    LIMIT ?
                    """,
                    tuple(params),
                ).fetchall()
            except Exception:
                rows = None

            if rows is None:
                where = ["1=1"]
                params = []
                if mtypes:
                    where.append("memory_type IN (%s)" % ",".join("?" for _ in mtypes))
                    params.extend(mtypes)
                if scope:
                    where.append("scope=?")
                    params.append(_safe_text(scope))
                where.append("confidence >= ?")
                params.append(max(0.0, min(float(min_confidence or 0.0), 1.0)))
                where.append("(LOWER(title) LIKE ? OR LOWER(body) LIKE ? OR LOWER(tags_json) LIKE ?)")
                like = f"%{q.lower()}%"
                params.extend([like, like, like])
                params.append(limit)
                rows = conn.execute(
                    f"""
                    SELECT *
                    FROM brain_memory
                    WHERE {' AND '.join(where)}
                    ORDER BY importance DESC, outcome_score DESC, use_count DESC, id DESC
                    LIMIT ?
                    """,
                    tuple(params),
                ).fetchall()

            ids = [int(r["id"]) for r in rows]
            if ids:
                conn.execute(
                    f"UPDATE brain_memory SET use_count=use_count+1, last_used_at=?, updated_at=? WHERE id IN ({','.join('?' for _ in ids)})",
                    tuple([_now_iso(), _now_iso(), *ids]),
                )
                conn.commit()

            return [self._memory_row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def list_memory(
        self,
        *,
        memory_type: str = "",
        scope: str = "",
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            where = ["1=1"]
            params: List[Any] = []
            mtype = _safe_text(memory_type).lower()
            if mtype in MEMORY_TYPES:
                where.append("memory_type=?")
                params.append(mtype)
            if scope:
                where.append("scope=?")
                params.append(_safe_text(scope))
            params.append(max(1, min(int(limit or 200), 500)))
            rows = conn.execute(
                f"""
                SELECT *
                FROM brain_memory
                WHERE {' AND '.join(where)}
                ORDER BY importance DESC, created_at DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
            return [self._memory_row_to_dict(r) for r in rows]
        finally:
            conn.close()

    # ── Evaluations ──────────────────────────────────────────────────────

    def record_evaluation(
        self,
        *,
        target_type: str,
        metric_name: str,
        score: float,
        target_id: str = "",
        evaluator: str = "",
        reasoning: str = "",
        signals: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not _safe_text(target_type):
            return {"ok": False, "error": "target_type required"}
        if not _safe_text(metric_name):
            return {"ok": False, "error": "metric_name required"}
        sc = max(0.0, min(_safe_float(score, 0.0), 100.0))
        grade = _score_to_grade(sc)
        conn = self._conn()
        try:
            cur = conn.execute(
                """
                INSERT INTO brain_evaluations
                (target_type, target_id, metric_name, score, grade, evaluator, reasoning, signals_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _safe_text(target_type),
                    _safe_text(target_id),
                    _safe_text(metric_name),
                    sc,
                    grade,
                    _safe_text(evaluator),
                    _safe_text(reasoning)[:2500],
                    _to_json(signals or {}),
                    _now_iso(),
                ),
            )
            eid = int(cur.lastrowid)
            conn.commit()
        finally:
            conn.close()
        self._log_event("info", "evaluation.record", f"Evaluation #{eid} recorded", {"metric": metric_name, "score": sc})
        return {"ok": True, "evaluation_id": eid, "grade": grade}

    def evaluation_summary(self, *, metric_name: str = "", target_type: str = "", days: int = 30) -> Dict[str, Any]:
        conn = self._conn()
        try:
            where = ["1=1"]
            params: List[Any] = []
            if metric_name:
                where.append("metric_name=?")
                params.append(_safe_text(metric_name))
            if target_type:
                where.append("target_type=?")
                params.append(_safe_text(target_type))
            if int(days or 0) > 0:
                since = (datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))).isoformat()
                where.append("created_at >= ?")
                params.append(since)

            row = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS n,
                    COALESCE(AVG(score),0) AS avg_score,
                    COALESCE(MIN(score),0) AS min_score,
                    COALESCE(MAX(score),0) AS max_score
                FROM brain_evaluations
                WHERE {' AND '.join(where)}
                """,
                tuple(params),
            ).fetchone()
            grade_rows = conn.execute(
                f"""
                SELECT grade, COUNT(*) AS c
                FROM brain_evaluations
                WHERE {' AND '.join(where)}
                GROUP BY grade
                ORDER BY c DESC
                """,
                tuple(params),
            ).fetchall()
            recent_rows = conn.execute(
                f"""
                SELECT id, target_type, target_id, metric_name, score, grade, evaluator, reasoning, created_at
                FROM brain_evaluations
                WHERE {' AND '.join(where)}
                ORDER BY id DESC
                LIMIT 50
                """,
                tuple(params),
            ).fetchall()
            return {
                "count": int(row["n"] if row else 0),
                "avg_score": float(row["avg_score"] if row else 0.0),
                "min_score": float(row["min_score"] if row else 0.0),
                "max_score": float(row["max_score"] if row else 0.0),
                "grade_counts": {r["grade"] or "": int(r["c"] or 0) for r in grade_rows},
                "recent": [dict(r) for r in recent_rows],
            }
        finally:
            conn.close()

    # ── Experiments ──────────────────────────────────────────────────────

    def create_experiment(
        self,
        *,
        name: str,
        goal: str,
        hypothesis: str,
        metric_name: str,
        success_threshold: float,
        lane: str = "experiments",
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not _safe_text(name):
            return {"ok": False, "error": "name required"}
        conn = self._conn()
        try:
            cur = conn.execute(
                """
                INSERT INTO brain_experiments
                (name, lane, goal, hypothesis, metric_name, success_threshold, status, config_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)
                """,
                (
                    _safe_text(name),
                    _safe_text(lane, "experiments"),
                    _safe_text(goal),
                    _safe_text(hypothesis),
                    _safe_text(metric_name),
                    _safe_float(success_threshold, 0.0),
                    _to_json(config or {}),
                    _now_iso(),
                    _now_iso(),
                ),
            )
            eid = int(cur.lastrowid)
            conn.commit()
        finally:
            conn.close()
        self._log_event("info", "experiment.create", f"Experiment #{eid} created", {"name": name, "lane": lane})
        return {"ok": True, "experiment_id": eid}

    def start_experiment_run(self, *, experiment_id: int, input_snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        eid = int(experiment_id or 0)
        if eid <= 0:
            return {"ok": False, "error": "experiment_id required"}
        conn = self._conn()
        try:
            exp = conn.execute("SELECT id, status FROM brain_experiments WHERE id=?", (eid,)).fetchone()
            if not exp:
                return {"ok": False, "error": "experiment not found"}
            conn.execute(
                "UPDATE brain_experiments SET status='running', updated_at=? WHERE id=?",
                (_now_iso(), eid),
            )
            cur = conn.execute(
                """
                INSERT INTO brain_experiment_runs
                (experiment_id, status, started_at, input_snapshot_json)
                VALUES (?, 'running', ?, ?)
                """,
                (eid, _now_iso(), _to_json(input_snapshot or {})),
            )
            rid = int(cur.lastrowid)
            conn.commit()
        finally:
            conn.close()
        self._log_event("info", "experiment.start", f"Experiment #{eid} run #{rid} started", {})
        return {"ok": True, "run_id": rid}

    def finish_experiment_run(
        self,
        *,
        run_id: int,
        status: str,
        score: float,
        result: Optional[Dict[str, Any]] = None,
        lesson: str = "",
    ) -> Dict[str, Any]:
        rid = int(run_id or 0)
        if rid <= 0:
            return {"ok": False, "error": "run_id required"}
        st = _safe_text(status).lower()
        if st not in {"completed", "failed", "cancelled"}:
            return {"ok": False, "error": "status must be completed|failed|cancelled"}

        conn = self._conn()
        try:
            row = conn.execute("SELECT id, experiment_id, status FROM brain_experiment_runs WHERE id=?", (rid,)).fetchone()
            if not row:
                return {"ok": False, "error": "run not found"}
            exp_id = int(row["experiment_id"])
            conn.execute(
                """
                UPDATE brain_experiment_runs
                SET status=?, ended_at=?, result_json=?, score=?, lesson=?
                WHERE id=?
                """,
                (st, _now_iso(), _to_json(result or {}), _safe_float(score, 0.0), _safe_text(lesson)[:2500], rid),
            )
            exp_status = "active" if st == "completed" else ("attention" if st == "failed" else "paused")
            conn.execute(
                "UPDATE brain_experiments SET status=?, updated_at=? WHERE id=?",
                (exp_status, _now_iso(), exp_id),
            )
            conn.commit()
        finally:
            conn.close()

        if _safe_text(lesson):
            # Strategic lesson writeback is the first learning loop primitive.
            self.add_memory(
                memory_type="strategic",
                scope="zoar_core",
                title=f"Experiment #{exp_id} lesson",
                body=_safe_text(lesson),
                tags=["experiment", f"experiment_{exp_id}", st],
                source="brain_experiment",
                confidence=0.7 if st == "completed" else 0.55,
                importance=0.75,
                outcome_score=max(-1.0, min(_safe_float(score, 0.0) / 100.0, 1.0)),
                linked_entity_type="experiment",
                linked_entity_id=str(exp_id),
            )

        self._log_event("info", "experiment.finish", f"Run #{rid} {st}", {"score": _safe_float(score, 0.0)})
        return {"ok": True, "run_id": rid, "status": st}

    def list_experiments(self, *, status: str = "", limit: int = 200) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            where = ["1=1"]
            params: List[Any] = []
            if status:
                where.append("status=?")
                params.append(_safe_text(status))
            params.append(max(1, min(int(limit or 200), 500)))
            rows = conn.execute(
                f"""
                SELECT id, name, lane, goal, hypothesis, metric_name, success_threshold, status, config_json, created_at, updated_at
                FROM brain_experiments
                WHERE {' AND '.join(where)}
                ORDER BY id DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
            out: List[Dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                d["config"] = _from_json(d.pop("config_json", "{}"), {})
                out.append(d)
            return out
        finally:
            conn.close()

    def list_experiment_runs(self, *, experiment_id: int = 0, limit: int = 200) -> List[Dict[str, Any]]:
        conn = self._conn()
        try:
            where = ["1=1"]
            params: List[Any] = []
            if int(experiment_id or 0) > 0:
                where.append("experiment_id=?")
                params.append(int(experiment_id))
            params.append(max(1, min(int(limit or 200), 500)))
            rows = conn.execute(
                f"""
                SELECT id, experiment_id, status, started_at, ended_at, input_snapshot_json, result_json, score, lesson
                FROM brain_experiment_runs
                WHERE {' AND '.join(where)}
                ORDER BY id DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["input_snapshot"] = _from_json(d.pop("input_snapshot_json", "{}"), {})
                d["result"] = _from_json(d.pop("result_json", "{}"), {})
                out.append(d)
            return out
        finally:
            conn.close()

    # ── Dashboard + events ───────────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        conn = self._conn()
        try:
            agent_rows = conn.execute(
                "SELECT lane, status, COUNT(*) AS c FROM brain_agents GROUP BY lane, status"
            ).fetchall()
            task_rows = conn.execute(
                "SELECT status, COUNT(*) AS c FROM brain_tasks GROUP BY status"
            ).fetchall()
            mem_rows = conn.execute(
                "SELECT memory_type, COUNT(*) AS c FROM brain_memory GROUP BY memory_type"
            ).fetchall()
            eval_row = conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(AVG(score),0) AS avg_score FROM brain_evaluations"
            ).fetchone()
            exp_rows = conn.execute(
                "SELECT status, COUNT(*) AS c FROM brain_experiments GROUP BY status"
            ).fetchall()
            approval_pending = conn.execute(
                "SELECT COUNT(*) AS c FROM brain_approvals WHERE status='pending'"
            ).fetchone()
            events = conn.execute(
                """
                SELECT id, level, event_type, message, payload_json, created_at
                FROM brain_events
                ORDER BY id DESC
                LIMIT 80
                """
            ).fetchall()
            return {
                "ok": True,
                "db_path": str(DB_PATH),
                "agents": {
                    "by_lane_status": [dict(r) for r in agent_rows],
                    "total": sum(int(r["c"] or 0) for r in agent_rows),
                },
                "tasks": {
                    "by_status": {r["status"]: int(r["c"] or 0) for r in task_rows},
                    "total": sum(int(r["c"] or 0) for r in task_rows),
                },
                "memory": {
                    "by_type": {r["memory_type"]: int(r["c"] or 0) for r in mem_rows},
                    "total": sum(int(r["c"] or 0) for r in mem_rows),
                },
                "evaluations": {
                    "count": int(eval_row["n"] or 0) if eval_row else 0,
                    "avg_score": float(eval_row["avg_score"] or 0.0) if eval_row else 0.0,
                },
                "experiments": {
                    "by_status": {r["status"]: int(r["c"] or 0) for r in exp_rows},
                    "total": sum(int(r["c"] or 0) for r in exp_rows),
                },
                "approvals_pending": int(approval_pending["c"] or 0) if approval_pending else 0,
                "recent_events": [
                    {
                        "id": int(r["id"]),
                        "level": r["level"],
                        "event_type": r["event_type"],
                        "message": r["message"],
                        "payload": _from_json(r["payload_json"], {}),
                        "created_at": r["created_at"],
                    }
                    for r in events
                ],
            }
        finally:
            conn.close()

    # ── Internals ────────────────────────────────────────────────────────

    def _task_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": int(row["id"]),
            "task_type": row["task_type"],
            "objective": row["objective"],
            "lane": row["lane"],
            "priority": int(row["priority"] or 50),
            "status": row["status"],
            "payload": _from_json(row["payload_json"], {}),
            "constraints": _from_json(row["constraints_json"], {}),
            "parent_task_id": int(row["parent_task_id"] or 0),
            "assigned_agent_id": row["assigned_agent_id"] or "",
            "requires_approval": bool(int(row["requires_approval"] or 0)),
            "approval_status": row["approval_status"] or "not_required",
            "risk_level": row["risk_level"] or "low",
            "result": _from_json(row["result_json"], {}),
            "error_text": row["error_text"] or "",
            "retry_count": int(row["retry_count"] or 0),
            "scheduled_for": row["scheduled_for"] or "",
            "created_at": row["created_at"] or "",
            "started_at": row["started_at"] or "",
            "finished_at": row["finished_at"] or "",
        }

    def _memory_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": int(row["id"]),
            "memory_type": row["memory_type"],
            "scope": row["scope"] or "zoar_core",
            "title": row["title"],
            "body": row["body"],
            "tags": _from_json(row["tags_json"], []),
            "source": row["source"] or "",
            "confidence": float(row["confidence"] or 0.0),
            "importance": float(row["importance"] or 0.0),
            "outcome_score": float(row["outcome_score"] or 0.0),
            "linked_entity_type": row["linked_entity_type"] or "",
            "linked_entity_id": row["linked_entity_id"] or "",
            "created_at": row["created_at"] or "",
            "updated_at": row["updated_at"] or "",
            "last_used_at": row["last_used_at"] or "",
            "use_count": int(row["use_count"] or 0),
        }

    def _log_event(self, level: str, event_type: str, message: str, payload: Optional[Dict[str, Any]] = None) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO brain_events (level, event_type, message, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (_safe_text(level, "info"), _safe_text(event_type), _safe_text(message), _to_json(payload or {}), _now_iso()),
            )
            conn.commit()
        finally:
            conn.close()


_CONTROL_PLANE: Optional[NexusBrainControlPlane] = None


def get_nexus_brain_control_plane() -> NexusBrainControlPlane:
    global _CONTROL_PLANE
    if _CONTROL_PLANE is None:
        _CONTROL_PLANE = NexusBrainControlPlane()
    return _CONTROL_PLANE
