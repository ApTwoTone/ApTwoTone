# Last verified 2026-03-04 by autonomous coding agent
"""
Nexus Shared Brain — Persistent memory system for inter-agent knowledge sharing.

All agents read from and write to this shared brain via SQLite.
Memory types: system_knowledge, task_history, error_log, vendor_intel, improvements, code_knowledge.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("shared_brain")

DB_PATH = Path.home() / ".nexus" / "memory.db"


class SharedBrain:
    """Central knowledge repository accessible by all agents."""

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or DB_PATH

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    # ── System Knowledge ─────────────────────────────────────────────────

    def get_rule(self, category: str, key: str) -> Optional[str]:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT value FROM brain_system_knowledge WHERE category = ? AND key = ?",
                (category, key),
            ).fetchone()
            return row["value"] if row else None
        finally:
            conn.close()

    def set_rule(self, category: str, key: str, value: str, source: str = "system"):
        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO brain_system_knowledge (category, key, value, source)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(category, key) DO UPDATE SET
                     value = excluded.value, source = excluded.source,
                     updated_at = datetime('now')""",
                (category, key, value, source),
            )
            conn.commit()
        finally:
            conn.close()

    def get_all_rules(self, category: str) -> List[Dict]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT key, value, source, confidence FROM brain_system_knowledge WHERE category = ?",
                (category,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Error Log (check-before-solve pattern) ───────────────────────────

    def check_known_error(self, error_type: str, error_message: str) -> Optional[Dict]:
        """Check if this error has been seen before. Returns solution if known."""
        conn = self._conn()
        try:
            row = conn.execute(
                """SELECT id, error_type, error_message, solution, fix_code,
                          auto_fixable, solution_verified, occurrences
                   FROM brain_error_log
                   WHERE error_type = ? AND error_message = ?""",
                (error_type, error_message),
            ).fetchone()
            if row:
                # Bump occurrence count
                conn.execute(
                    "UPDATE brain_error_log SET occurrences = occurrences + 1, last_seen = datetime('now') WHERE id = ?",
                    (row["id"],),
                )
                conn.commit()
                return dict(row)
            return None
        finally:
            conn.close()

    def log_error(self, error_type: str, error_message: str,
                  context: str = "", file_path: str = ""):
        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO brain_error_log (error_type, error_message, error_context, file_path)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(error_type, error_message) DO UPDATE SET
                     occurrences = occurrences + 1,
                     last_seen = datetime('now'),
                     error_context = CASE WHEN excluded.error_context != '' THEN excluded.error_context ELSE error_context END""",
                (error_type, error_message, context, file_path),
            )
            conn.commit()
        finally:
            conn.close()

    def record_solution(self, error_id: int, solution: str,
                        fix_code: str = "", verified: bool = False):
        conn = self._conn()
        try:
            conn.execute(
                """UPDATE brain_error_log
                   SET solution = ?, fix_code = ?, solution_verified = ?, auto_fixable = ?
                   WHERE id = ?""",
                (solution, fix_code, int(verified), int(bool(fix_code)), error_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_recent_errors(self, limit: int = 20) -> List[Dict]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM brain_error_log ORDER BY last_seen DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Task History ─────────────────────────────────────────────────────

    def log_task(self, task_type: str, description: str, result_summary: str,
                 success: bool, provider: str = "", model: str = "",
                 tokens: int = 0, duration_ms: int = 0, agent_id: str = ""):
        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO brain_task_history
                   (task_type, description, result_summary, success, provider_used,
                    model_used, tokens_used, duration_ms, agent_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (task_type, description, result_summary, int(success),
                 provider, model, tokens, duration_ms, agent_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_similar_tasks(self, task_type: str, limit: int = 5) -> List[Dict]:
        conn = self._conn()
        try:
            rows = conn.execute(
                """SELECT task_type, description, result_summary, success, provider_used, model_used
                   FROM brain_task_history WHERE task_type = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (task_type, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Vendor Intelligence ──────────────────────────────────────────────

    def add_vendor_intel(self, vendor_id: int, vendor_name: str,
                         intel_type: str, intel_data: Dict,
                         relevance: float = 0.5, source_agent: str = ""):
        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO brain_vendor_intel
                   (vendor_id, vendor_name, intel_type, intel_data, relevance_score, source_agent)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (vendor_id, vendor_name, intel_type, json.dumps(intel_data),
                 relevance, source_agent),
            )
            conn.commit()
        finally:
            conn.close()

    def get_vendor_intel(self, vendor_id: int) -> List[Dict]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM brain_vendor_intel WHERE vendor_id = ? ORDER BY created_at DESC",
                (vendor_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Improvement Suggestions ──────────────────────────────────────────

    def suggest_improvement(self, agent_id: str, category: str,
                            title: str, description: str,
                            priority: str = "medium"):
        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO brain_improvements
                   (suggested_by, category, title, description, priority)
                   VALUES (?, ?, ?, ?, ?)""",
                (agent_id, category, title, description, priority),
            )
            conn.commit()
        finally:
            conn.close()

    def get_pending_improvements(self, limit: int = 20) -> List[Dict]:
        conn = self._conn()
        try:
            rows = conn.execute(
                """SELECT * FROM brain_improvements WHERE status = 'proposed'
                   ORDER BY CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                            created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Code Knowledge ───────────────────────────────────────────────────

    def scan_codebase(self, root: Optional[str] = None):
        """Walk the Nexus codebase and index all Python files."""
        root_path = Path(root) if root else Path(__file__).parent.parent
        conn = self._conn()
        try:
            count = 0
            for py_file in root_path.rglob("*.py"):
                if any(d in py_file.parts for d in ("venv", "__pycache__", ".git", "node_modules")):
                    continue
                rel = str(py_file.relative_to(root_path))
                try:
                    content = py_file.read_text(errors="ignore")
                    lines = content.count("\n") + 1
                    # Extract docstring as purpose
                    purpose = ""
                    if content.startswith('"""') or content.startswith("'''"):
                        end = content.find('"""', 3) if content.startswith('"""') else content.find("'''", 3)
                        if end > 0:
                            purpose = content[3:end].strip()[:200]
                    # Extract function names
                    funcs = []
                    for line in content.split("\n"):
                        stripped = line.strip()
                        if stripped.startswith("def ") or stripped.startswith("async def "):
                            name = stripped.split("(")[0].replace("def ", "").replace("async ", "").strip()
                            funcs.append(name)
                    stat = py_file.stat()
                    conn.execute(
                        """INSERT INTO brain_code_knowledge
                           (file_path, module_name, purpose, key_functions, line_count, last_modified)
                           VALUES (?, ?, ?, ?, ?, datetime(?, 'unixepoch'))
                           ON CONFLICT(file_path) DO UPDATE SET
                             purpose = excluded.purpose,
                             key_functions = excluded.key_functions,
                             line_count = excluded.line_count,
                             last_modified = excluded.last_modified,
                             last_scanned = datetime('now')""",
                        (rel, rel.replace("/", ".").replace(".py", ""),
                         purpose, json.dumps(funcs[:30]), lines, stat.st_mtime),
                    )
                    count += 1
                except Exception:
                    pass
            conn.commit()
            log.info("Codebase scan indexed %d files", count)
            return count
        finally:
            conn.close()

    def get_file_info(self, file_path: str) -> Optional[Dict]:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM brain_code_knowledge WHERE file_path LIKE ?",
                ("%" + file_path + "%",),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def find_files_for_task(self, keywords: str, limit: int = 10) -> List[Dict]:
        """Find relevant files by searching purpose and function names."""
        conn = self._conn()
        try:
            rows = conn.execute(
                """SELECT file_path, module_name, purpose, key_functions, line_count
                   FROM brain_code_knowledge
                   WHERE purpose LIKE ? OR key_functions LIKE ? OR file_path LIKE ?
                   ORDER BY line_count DESC LIMIT ?""",
                ("%" + keywords + "%", "%" + keywords + "%", "%" + keywords + "%", limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Learning Engine (Self-Healing Flywheel) ─────────────────────────

    def learn_from_repair(self, event: Dict, diagnosis: str,
                          fix_approach: str, files_changed: List[str],
                          model_used: str, provider: str,
                          tokens: int, duration_ms: int, success: bool):
        """After a repair attempt, store the pattern for future instant fixes."""
        # 1. Store/update fix in error log for brain lookups
        if success and fix_approach:
            known = self.check_known_error(event.get("check_type", ""), event.get("message", ""))
            if known:
                self.record_solution(
                    known["id"], diagnosis, fix_code=fix_approach, verified=True,
                )
            else:
                self.log_error(
                    event.get("check_type", "health_check"),
                    event.get("message", ""),
                    context=diagnosis,
                    file_path=files_changed[0] if files_changed else "",
                )
                # Find the newly inserted error and record solution
                new_err = self.check_known_error(
                    event.get("check_type", ""), event.get("message", ""),
                )
                if new_err:
                    self.record_solution(
                        new_err["id"], diagnosis, fix_code=fix_approach, verified=True,
                    )

        # 2. Log model performance
        self._log_model_performance(
            model=model_used, provider=provider,
            task_type="self_heal_repair", success=success,
            tokens=tokens, latency_ms=duration_ms,
        )

        # 3. Log task to brain history
        self.log_task(
            task_type="self_heal_repair",
            description="Repair: %s" % event.get("message", "")[:200],
            result_summary=fix_approach[:200] if fix_approach else "no fix",
            success=success, provider=provider, model=model_used,
            tokens=tokens, duration_ms=duration_ms,
            agent_id="health_scanner",
        )

        # 4. Re-index modified files
        if success and files_changed:
            self._reindex_files(files_changed)

    def _log_model_performance(self, model: str, provider: str,
                                task_type: str, success: bool,
                                tokens: int = 0, latency_ms: int = 0,
                                error_message: str = ""):
        """Record model performance for routing optimization."""
        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO model_performance
                   (model, provider, task_type, success, tokens_used,
                    latency_ms, error_message, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                (model, provider, task_type, int(success),
                 tokens, latency_ms, error_message),
            )
            conn.commit()
        except Exception as e:
            log.warning("model_performance insert failed: %s", e)
        finally:
            conn.close()

    def get_best_model_for_task(self, task_type: str, min_attempts: int = 5) -> Optional[str]:
        """Query model_performance to find the best model for a task type.
        Requires minimum attempts to avoid one-shot bias."""
        conn = self._conn()
        try:
            rows = conn.execute(
                """SELECT model, provider,
                          COUNT(*) as attempts,
                          SUM(success) as successes,
                          ROUND(AVG(latency_ms)) as avg_latency
                   FROM model_performance
                   WHERE task_type = ?
                   GROUP BY model, provider
                   HAVING attempts >= ?
                   ORDER BY (CAST(successes AS REAL) / attempts) DESC,
                            avg_latency ASC
                   LIMIT 5""",
                (task_type, min_attempts),
            ).fetchall()
            if rows:
                return rows[0]["model"]
            return None
        except Exception:
            return None
        finally:
            conn.close()

    def get_model_leaderboard(self, task_type: Optional[str] = None,
                               limit: int = 20) -> List[Dict]:
        """Get model performance leaderboard, optionally filtered by task type."""
        conn = self._conn()
        try:
            if task_type:
                rows = conn.execute(
                    """SELECT model, provider, task_type,
                              COUNT(*) as attempts,
                              SUM(success) as successes,
                              ROUND(100.0 * SUM(success) / COUNT(*), 1) as success_rate,
                              ROUND(AVG(latency_ms)) as avg_latency,
                              SUM(tokens_used) as total_tokens
                       FROM model_performance
                       WHERE task_type = ?
                       GROUP BY model, provider
                       ORDER BY success_rate DESC, avg_latency ASC
                       LIMIT ?""",
                    (task_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT model, provider,
                              COUNT(*) as attempts,
                              SUM(success) as successes,
                              ROUND(100.0 * SUM(success) / COUNT(*), 1) as success_rate,
                              ROUND(AVG(latency_ms)) as avg_latency,
                              SUM(tokens_used) as total_tokens
                       FROM model_performance
                       GROUP BY model, provider
                       ORDER BY success_rate DESC, avg_latency ASC
                       LIMIT ?""",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []
        finally:
            conn.close()

    def _reindex_files(self, file_paths: List[str]):
        """Re-index specific files in code knowledge after repair."""
        root = Path(__file__).parent.parent
        for fp in file_paths:
            full = root / fp if not Path(fp).is_absolute() else Path(fp)
            if full.exists() and full.suffix == ".py":
                try:
                    content = full.read_text(errors="ignore")
                    rel = str(full.relative_to(root))
                    lines = content.count("\n") + 1
                    purpose = ""
                    if content.startswith('"""') or content.startswith("'''"):
                        q = '"""' if content.startswith('"""') else "'''"
                        end = content.find(q, 3)
                        if end > 0:
                            purpose = content[3:end].strip()[:200]
                    import json as _json
                    funcs = []
                    for line in content.split("\n"):
                        s = line.strip()
                        if s.startswith("def ") or s.startswith("async def "):
                            name = s.split("(")[0].replace("def ", "").replace("async ", "").strip()
                            funcs.append(name)
                    conn = self._conn()
                    try:
                        conn.execute(
                            """INSERT INTO brain_code_knowledge
                               (file_path, module_name, purpose, key_functions, line_count, last_modified)
                               VALUES (?, ?, ?, ?, ?, datetime('now'))
                               ON CONFLICT(file_path) DO UPDATE SET
                                 purpose = excluded.purpose,
                                 key_functions = excluded.key_functions,
                                 line_count = excluded.line_count,
                                 last_modified = excluded.last_modified,
                                 last_scanned = datetime('now')""",
                            (rel, rel.replace("/", ".").replace(".py", ""),
                             purpose, _json.dumps(funcs[:30]), lines),
                        )
                        conn.commit()
                    finally:
                        conn.close()
                except Exception as e:
                    log.warning("Re-index failed for %s: %s", fp, e)

    # ── Stats ────────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, int]:
        conn = self._conn()
        try:
            tables = [
                "brain_system_knowledge", "brain_task_history", "brain_error_log",
                "brain_vendor_intel", "brain_improvements", "brain_code_knowledge",
            ]
            stats = {}
            for t in tables:
                try:
                    row = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()
                    stats[t] = row[0]
                except Exception:
                    stats[t] = 0
            return stats
        finally:
            conn.close()


# ── Singleton ────────────────────────────────────────────────────────────
_brain = None  # type: Optional[SharedBrain]


def get_brain() -> SharedBrain:
    global _brain
    if _brain is None:
        _brain = SharedBrain()
    return _brain
