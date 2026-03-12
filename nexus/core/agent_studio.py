"""
Nexus Parallel Agent Studio

Five permanent specialized agents work on tasks simultaneously with
file-level locking to prevent conflicts.

Agents:
    Architect  — Gemini 3 Flash — plans only, never writes code
    Builder    — Qwen3 (Ollama) / OpenRouter — writes code from plans
    Reviewer   — ZAI GLM — reviews code, flags issues
    Patcher    — ZAI GLM + Cerebras — fixes issues from reviewer
    Bug Hunter — see core/bug_hunter.py (independent scanner)

Concurrency: Max 5 parallel builds, 10 sub-agents per task, 20 total.
Claude is never called by studio agents.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("agent_studio")

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"

MAX_PARALLEL_BUILDS = 5
MAX_SUB_AGENTS_PER_TASK = 10
MAX_TOTAL_CONCURRENT = 20


# ── File Lock Manager ────────────────────────────────────────────────────────

class FileLockManager:
    """Atomic file-level locking via SQLite for parallel agent safety."""

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or DB_PATH

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn

    def acquire(self, file_path: str, agent_id: str,
                task_id: str = "", ttl: int = 300) -> bool:
        """Try to acquire a lock on file_path. Returns True if acquired."""
        conn = self._conn()
        try:
            # Clean expired locks first
            conn.execute(
                "DELETE FROM file_locks WHERE "
                "datetime(locked_at, '+' || ttl_seconds || ' seconds') < datetime('now')"
            )
            # Attempt atomic insert (UNIQUE on file_path prevents duplicates)
            conn.execute(
                "INSERT OR IGNORE INTO file_locks (file_path, agent_id, task_id, ttl_seconds) "
                "VALUES (?, ?, ?, ?)",
                (file_path, agent_id, task_id, ttl),
            )
            conn.commit()
            # Check if we actually got it
            row = conn.execute(
                "SELECT agent_id FROM file_locks WHERE file_path = ?",
                (file_path,),
            ).fetchone()
            return row is not None and row["agent_id"] == agent_id
        except Exception as e:
            log.warning("Lock acquire error for %s: %s", file_path, e)
            return False
        finally:
            conn.close()

    def release(self, file_path: str, agent_id: str) -> bool:
        """Release a lock only if owned by agent_id."""
        conn = self._conn()
        try:
            cur = conn.execute(
                "DELETE FROM file_locks WHERE file_path = ? AND agent_id = ?",
                (file_path, agent_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def release_all(self, agent_id: str) -> int:
        """Release all locks held by an agent."""
        conn = self._conn()
        try:
            cur = conn.execute(
                "DELETE FROM file_locks WHERE agent_id = ?", (agent_id,)
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def cleanup_expired(self) -> int:
        """Remove all expired locks."""
        conn = self._conn()
        try:
            cur = conn.execute(
                "DELETE FROM file_locks WHERE "
                "datetime(locked_at, '+' || ttl_seconds || ' seconds') < datetime('now')"
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def get_locks(self) -> List[Dict[str, Any]]:
        """Return all active (non-expired) locks."""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT file_path, agent_id, task_id, locked_at, ttl_seconds "
                "FROM file_locks WHERE "
                "datetime(locked_at, '+' || ttl_seconds || ' seconds') >= datetime('now') "
                "ORDER BY locked_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def is_locked(self, file_path: str) -> Optional[str]:
        """Check if file is locked. Returns agent_id or None."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT agent_id FROM file_locks WHERE file_path = ? AND "
                "datetime(locked_at, '+' || ttl_seconds || ' seconds') >= datetime('now')",
                (file_path,),
            ).fetchone()
            return row["agent_id"] if row else None
        finally:
            conn.close()


# ── Studio Agent Base ─────────────────────────────────────────────────────────

class StudioAgent:
    """Base class for all studio agents."""

    AGENT_TYPE = "base"
    DEFAULT_MODEL = ""
    TASK_TYPE = "general"
    TIMEOUT = 120

    def __init__(self, lock_mgr: FileLockManager):
        self.agent_id = "%s-%s" % (self.AGENT_TYPE, uuid.uuid4().hex[:6])
        self.lock_mgr = lock_mgr
        self.status = "idle"  # idle, working, error
        self.current_task = None  # type: Optional[Dict]
        self.last_result = None  # type: Optional[Dict]
        self.tasks_completed = 0
        self.last_active = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_type": self.AGENT_TYPE,
            "model": self.DEFAULT_MODEL,
            "status": self.status,
            "current_task": self.current_task,
            "tasks_completed": self.tasks_completed,
            "last_active": self.last_active,
        }

    async def execute(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a task. Handles locking, metrics, and error recovery."""
        self.status = "working"
        self.current_task = task
        start = time.time()
        metric_id = self._record_metric_start(task)

        try:
            result = await asyncio.wait_for(
                self._run(task),
                timeout=self.TIMEOUT,
            )
            duration = time.time() - start
            self._record_metric_end(metric_id, duration, True,
                                    result.get("provider", ""),
                                    result.get("tokens", 0))
            self.tasks_completed += 1
            self.last_result = result
            self.status = "idle"
            self.last_active = time.time()
            return result
        except asyncio.TimeoutError:
            log.error("%s timed out on task %s", self.AGENT_TYPE, task.get("task_id", ""))
            self._record_metric_end(metric_id, time.time() - start, False)
            self.status = "error"
            return {"ok": False, "error": "timeout"}
        except Exception as e:
            log.error("%s error: %s", self.AGENT_TYPE, e)
            self._record_metric_end(metric_id, time.time() - start, False)
            self.status = "error"
            return {"ok": False, "error": str(e)}
        finally:
            self.current_task = None
            self.lock_mgr.release_all(self.agent_id)

    async def _run(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """Override in subclass."""
        raise NotImplementedError

    def _call_llm(self, messages: List[Dict], task_type: str = "") -> Dict[str, Any]:
        """Call LLM via key rotation pool."""
        from core.key_rotation import get_pool, PROVIDERS
        import requests

        pool = get_pool()
        tt = task_type or self.TASK_TYPE
        pair = pool.get_provider_for_task(tt)
        if not pair:
            return {"ok": False, "error": "no provider available"}

        provider_id, key = pair
        prov = PROVIDERS.get(provider_id, {})
        endpoint = prov.get("endpoint", "")
        model = prov.get("model", "")
        headers = dict(prov.get("extra_headers", {}))
        timeout = min(self.TIMEOUT, prov.get("timeout", 60))

        # Build request based on provider type
        if prov.get("openai_compat"):
            headers["Authorization"] = "Bearer %s" % key
            headers["Content-Type"] = "application/json"
            payload = {"model": model, "messages": messages, "max_tokens": 2000}
            try:
                resp = requests.post(endpoint, json=payload, headers=headers, timeout=timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    tokens = data.get("usage", {}).get("total_tokens", 0)
                    pool.report_success(provider_id, key)
                    return {"ok": True, "content": content, "provider": provider_id,
                            "model": model, "tokens": tokens}
                elif resp.status_code == 429:
                    pool.report_rate_limit(provider_id, key)
                else:
                    pool.report_error(provider_id, key)
                return {"ok": False, "error": "HTTP %d" % resp.status_code}
            except Exception as e:
                pool.report_error(provider_id, key)
                return {"ok": False, "error": str(e)}

        elif provider_id == "zai":
            headers["Authorization"] = "Bearer %s" % key
            headers["Content-Type"] = "application/json"
            payload = {"model": model, "messages": messages, "max_tokens": 2000}
            try:
                resp = requests.post(endpoint, json=payload, headers=headers, timeout=timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get("choices", [{}])
                    msg = choices[0].get("message", {}) if choices else {}
                    # ZAI: reasoning model, check reasoning_content first
                    content = msg.get("content", "") or msg.get("reasoning_content", "")
                    tokens = data.get("usage", {}).get("total_tokens", 0)
                    pool.report_success(provider_id, key)
                    return {"ok": True, "content": content, "provider": provider_id,
                            "model": model, "tokens": tokens}
                elif resp.status_code == 429:
                    pool.report_rate_limit(provider_id, key)
                else:
                    pool.report_error(provider_id, key)
                return {"ok": False, "error": "HTTP %d" % resp.status_code}
            except Exception as e:
                pool.report_error(provider_id, key)
                return {"ok": False, "error": str(e)}

        elif provider_id == "gemini":
            url = endpoint.replace(":generateContent", ":generateContent?key=%s" % key)
            parts = []
            sys_text = ""
            for m in messages:
                if m["role"] == "system":
                    sys_text = m["content"]
                else:
                    parts.append({"role": "user" if m["role"] == "user" else "model",
                                  "parts": [{"text": m["content"]}]})
            payload = {"contents": parts, "generationConfig": {"maxOutputTokens": 8192}}
            if sys_text:
                payload["systemInstruction"] = {"parts": [{"text": sys_text}]}
            try:
                resp = requests.post(url, json=payload,
                                     headers={"Content-Type": "application/json"},
                                     timeout=timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    cands = data.get("candidates", [{}])
                    content = ""
                    if cands:
                        parts_out = cands[0].get("content", {}).get("parts", [])
                        content = parts_out[0].get("text", "") if parts_out else ""
                    pool.report_success(provider_id, key)
                    return {"ok": True, "content": content, "provider": provider_id,
                            "model": model, "tokens": 0}
                elif resp.status_code == 429:
                    pool.report_rate_limit(provider_id, key)
                else:
                    pool.report_error(provider_id, key)
                return {"ok": False, "error": "HTTP %d" % resp.status_code}
            except Exception as e:
                pool.report_error(provider_id, key)
                return {"ok": False, "error": str(e)}

        elif provider_id == "ollama":
            payload = {"model": model, "messages": messages, "stream": False}
            try:
                resp = requests.post(endpoint, json=payload, timeout=timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    content = data.get("message", {}).get("content", "")
                    return {"ok": True, "content": content, "provider": "ollama",
                            "model": model, "tokens": 0}
                return {"ok": False, "error": "HTTP %d" % resp.status_code}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        return {"ok": False, "error": "unsupported provider %s" % provider_id}

    def _record_metric_start(self, task: Dict) -> int:
        """Insert a studio_metrics row, return its id."""
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=5)
            cur = conn.execute(
                "INSERT INTO studio_metrics (agent_type, task_id) VALUES (?, ?)",
                (self.AGENT_TYPE, task.get("task_id", "")),
            )
            conn.commit()
            mid = cur.lastrowid
            conn.close()
            return mid
        except Exception:
            return 0

    def _record_metric_end(self, metric_id: int, duration: float,
                           success: bool, provider: str = "",
                           tokens: int = 0):
        if not metric_id:
            return
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=5)
            conn.execute(
                "UPDATE studio_metrics SET completed_at=datetime('now'), "
                "duration_seconds=?, success=?, provider=?, tokens_used=? "
                "WHERE id=?",
                (round(duration, 2), 1 if success else 0, provider, tokens, metric_id),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass


# ── Architect Agent ───────────────────────────────────────────────────────────

class ArchitectAgent(StudioAgent):
    """Plans implementations — never writes code."""

    AGENT_TYPE = "architect"
    DEFAULT_MODEL = "gemini-3-flash"
    TASK_TYPE = "studio_architect"
    TIMEOUT = 180

    async def _run(self, task: Dict[str, Any]) -> Dict[str, Any]:
        description = task.get("description", "")
        messages = [
            {"role": "system", "content": (
                "You are the Architect agent for Nexus. "
                "Given a task description, produce a detailed implementation plan. "
                "Output JSON with keys: files (list of file paths to create/modify), "
                "approach (string describing the strategy), "
                "dependencies (list of existing files that must be read), "
                "estimated_complexity (1-10). "
                "Never write actual code — only plan."
            )},
            {"role": "user", "content": description},
        ]
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._call_llm(messages, self.TASK_TYPE)
        )
        if not result.get("ok"):
            return result
        # Try to parse JSON from response
        content = result.get("content", "")
        try:
            # Extract JSON from markdown code blocks if present
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
            plan = json.loads(content)
        except (json.JSONDecodeError, IndexError):
            plan = {"approach": content, "files": [], "dependencies": [],
                    "estimated_complexity": 5}
        return {"ok": True, "plan": plan, "provider": result.get("provider", ""),
                "tokens": result.get("tokens", 0)}


# ── Builder Agent ─────────────────────────────────────────────────────────────

class BuilderAgent(StudioAgent):
    """Writes code from architect plans. Acquires file locks."""

    AGENT_TYPE = "builder"
    DEFAULT_MODEL = "qwen3:8b"
    TASK_TYPE = "studio_build"
    TIMEOUT = 300

    async def _run(self, task: Dict[str, Any]) -> Dict[str, Any]:
        plan = task.get("plan", {})
        files = plan.get("files", [])
        description = task.get("description", "")

        # Acquire file locks
        locked = []
        for fp in files:
            if self.lock_mgr.acquire(fp, self.agent_id, task.get("task_id", "")):
                locked.append(fp)
            else:
                holder = self.lock_mgr.is_locked(fp)
                log.warning("Cannot lock %s — held by %s", fp, holder)
                # Release what we got and abort
                for lf in locked:
                    self.lock_mgr.release(lf, self.agent_id)
                return {"ok": False, "error": "file lock conflict on %s" % fp}

        # Read existing file contents for context
        file_contexts = []
        for fp in files:
            full_path = Path.cwd() / fp
            if full_path.exists():
                try:
                    content = full_path.read_text()[:4000]
                    file_contexts.append("--- %s ---\n%s" % (fp, content))
                except Exception:
                    pass

        context_str = "\n\n".join(file_contexts) if file_contexts else "No existing files."
        approach = plan.get("approach", description)

        messages = [
            {"role": "system", "content": (
                "You are the Builder agent for Nexus. "
                "Given a plan and existing file contents, write the code changes. "
                "Output a JSON array of objects: [{\"file\": \"path\", \"content\": \"full file content\"}]. "
                "Write complete file contents, not diffs. Python 3.9+ compatible."
            )},
            {"role": "user", "content": (
                "Plan: %s\n\nExisting files:\n%s\n\n"
                "Implement the plan. Return JSON array of file changes." % (approach, context_str)
            )},
        ]
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._call_llm(messages, self.TASK_TYPE)
        )
        if not result.get("ok"):
            return result

        content = result.get("content", "")
        try:
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
            changes = json.loads(content)
        except (json.JSONDecodeError, IndexError):
            changes = []

        # Write files
        files_written = []
        for change in changes:
            fp = change.get("file", "")
            fc = change.get("content", "")
            if fp and fc:
                try:
                    full_path = Path.cwd() / fp
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(fc)
                    files_written.append(fp)
                except Exception as e:
                    log.error("Failed to write %s: %s", fp, e)

        return {"ok": True, "files_written": files_written,
                "changes": changes, "provider": result.get("provider", ""),
                "tokens": result.get("tokens", 0)}


# ── Reviewer Agent ────────────────────────────────────────────────────────────

class ReviewerAgent(StudioAgent):
    """Reviews code changes. Read-only — never modifies files."""

    AGENT_TYPE = "reviewer"
    DEFAULT_MODEL = "glm-4.5-flash"
    TASK_TYPE = "studio_review"
    TIMEOUT = 120

    async def _run(self, task: Dict[str, Any]) -> Dict[str, Any]:
        files_written = task.get("files_written", [])
        if not files_written:
            return {"ok": True, "verdict": "pass", "issues": []}

        # Read the files that were written
        file_contents = []
        for fp in files_written:
            full_path = Path.cwd() / fp
            if full_path.exists():
                try:
                    content = full_path.read_text()[:4000]
                    file_contents.append("--- %s ---\n%s" % (fp, content))
                except Exception:
                    pass

        messages = [
            {"role": "system", "content": (
                "You are the Reviewer agent for Nexus. "
                "Review the provided code for bugs, security issues, and style problems. "
                "Output JSON: {\"verdict\": \"pass\" or \"fail\", "
                "\"issues\": [{\"file\": \"path\", \"line\": N, \"severity\": \"error|warning|info\", "
                "\"description\": \"what is wrong\", \"suggestion\": \"how to fix\"}]}. "
                "Only fail for actual bugs or security issues, not style preferences."
            )},
            {"role": "user", "content": "\n\n".join(file_contents)},
        ]
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._call_llm(messages, self.TASK_TYPE)
        )
        if not result.get("ok"):
            return result

        content = result.get("content", "")
        try:
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
            review = json.loads(content)
        except (json.JSONDecodeError, IndexError):
            review = {"verdict": "pass", "issues": []}

        return {"ok": True, "verdict": review.get("verdict", "pass"),
                "issues": review.get("issues", []),
                "provider": result.get("provider", ""),
                "tokens": result.get("tokens", 0)}


# ── Patcher Agent ─────────────────────────────────────────────────────────────

class PatcherAgent(StudioAgent):
    """Fixes issues found by the reviewer."""

    AGENT_TYPE = "patcher"
    DEFAULT_MODEL = "glm-4.5-flash"
    TASK_TYPE = "studio_patch"
    TIMEOUT = 180

    async def _run(self, task: Dict[str, Any]) -> Dict[str, Any]:
        issues = task.get("issues", [])
        files = task.get("files_written", [])
        if not issues:
            return {"ok": True, "patched": False}

        # Acquire locks on affected files
        locked = []
        for fp in files:
            if self.lock_mgr.acquire(fp, self.agent_id, task.get("task_id", "")):
                locked.append(fp)
            else:
                for lf in locked:
                    self.lock_mgr.release(lf, self.agent_id)
                return {"ok": False, "error": "file lock conflict"}

        # Read current file contents
        file_contents = []
        for fp in files:
            full_path = Path.cwd() / fp
            if full_path.exists():
                try:
                    content = full_path.read_text()[:4000]
                    file_contents.append("--- %s ---\n%s" % (fp, content))
                except Exception:
                    pass

        issues_str = json.dumps(issues, indent=2)
        messages = [
            {"role": "system", "content": (
                "You are the Patcher agent for Nexus. "
                "Fix the issues listed in the review. "
                "Output a JSON array: [{\"file\": \"path\", \"content\": \"full corrected content\"}]. "
                "Only modify files that have issues. Python 3.9+ compatible."
            )},
            {"role": "user", "content": (
                "Issues:\n%s\n\nCurrent files:\n%s\n\n"
                "Fix all issues and return the corrected files." % (issues_str, "\n\n".join(file_contents))
            )},
        ]
        result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._call_llm(messages, self.TASK_TYPE)
        )
        if not result.get("ok"):
            return result

        content = result.get("content", "")
        try:
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
            patches = json.loads(content)
        except (json.JSONDecodeError, IndexError):
            patches = []

        # Apply patches
        files_patched = []
        for patch in patches:
            fp = patch.get("file", "")
            fc = patch.get("content", "")
            if fp and fc:
                try:
                    full_path = Path.cwd() / fp
                    full_path.write_text(fc)
                    files_patched.append(fp)
                except Exception as e:
                    log.error("Failed to patch %s: %s", fp, e)

        return {"ok": True, "patched": True, "files_patched": files_patched,
                "provider": result.get("provider", ""),
                "tokens": result.get("tokens", 0)}


# ── Agent Studio Orchestrator ─────────────────────────────────────────────────

class AgentStudio:
    """Orchestrates the 5 permanent agents with parallel task execution."""

    def __init__(self):
        self.lock_mgr = FileLockManager()
        self.architect = ArchitectAgent(self.lock_mgr)
        self.builder = BuilderAgent(self.lock_mgr)
        self.reviewer = ReviewerAgent(self.lock_mgr)
        self.patcher = PatcherAgent(self.lock_mgr)
        self._active_builds = 0
        self._total_sub_agents = 0
        self._task_results = {}  # type: Dict[str, Dict]
        self._running = False

    def get_status(self) -> Dict[str, Any]:
        """Return full studio status for the API."""
        return {
            "agents": {
                "architect": self.architect.to_dict(),
                "builder": self.builder.to_dict(),
                "reviewer": self.reviewer.to_dict(),
                "patcher": self.patcher.to_dict(),
            },
            "active_builds": self._active_builds,
            "total_sub_agents": self._total_sub_agents,
            "max_parallel_builds": MAX_PARALLEL_BUILDS,
            "max_total_concurrent": MAX_TOTAL_CONCURRENT,
            "file_locks": self.lock_mgr.get_locks(),
            "running": self._running,
        }

    async def submit_task(self, description: str, priority: int = 5) -> Dict[str, Any]:
        """Submit a new task to the studio pipeline."""
        if self._active_builds >= MAX_PARALLEL_BUILDS:
            # Queue via fleet task queue
            from core.fleet_task_queue import FleetTaskQueue
            q = FleetTaskQueue()
            task_id = q.enqueue(
                "studio_architect", {"description": description},
                tier=5, priority=priority,
            )
            return {"queued": True, "task_id": task_id,
                    "message": "Build queue full (%d/%d). Task queued." % (
                        self._active_builds, MAX_PARALLEL_BUILDS)}

        # Execute immediately
        task_id = "studio-%s" % uuid.uuid4().hex[:8]
        # Run pipeline in background
        asyncio.create_task(self._run_pipeline(task_id, description))
        return {"queued": False, "task_id": task_id,
                "message": "Build started. Track in Agent Studio."}

    async def _run_pipeline(self, task_id: str, description: str):
        """Full pipeline: Architect → Builder → Reviewer → (Patcher) → done."""
        self._active_builds += 1
        try:
            log.info("Studio pipeline started: %s — %s", task_id, description[:80])

            # Stage 1: Architect
            arch_result = await self.architect.execute({
                "task_id": task_id, "description": description,
            })
            if not arch_result.get("ok"):
                self._task_results[task_id] = {
                    "status": "failed", "stage": "architect",
                    "error": arch_result.get("error", "architect failed"),
                }
                return

            plan = arch_result.get("plan", {})
            log.info("Architect plan: %d files, complexity %s",
                     len(plan.get("files", [])),
                     plan.get("estimated_complexity", "?"))

            # Stage 2: Builder
            build_result = await self.builder.execute({
                "task_id": task_id, "description": description, "plan": plan,
            })
            if not build_result.get("ok"):
                self._task_results[task_id] = {
                    "status": "failed", "stage": "builder",
                    "error": build_result.get("error", "builder failed"),
                }
                return

            files_written = build_result.get("files_written", [])
            log.info("Builder wrote %d files", len(files_written))

            # Stage 3: Reviewer
            review_result = await self.reviewer.execute({
                "task_id": task_id, "files_written": files_written,
            })
            if not review_result.get("ok"):
                self._task_results[task_id] = {
                    "status": "failed", "stage": "reviewer",
                    "error": review_result.get("error", "reviewer failed"),
                }
                return

            verdict = review_result.get("verdict", "pass")
            issues = review_result.get("issues", [])

            # Stage 4: Patcher (only if reviewer found issues)
            if verdict == "fail" and issues:
                log.info("Reviewer found %d issues — patching", len(issues))
                patch_result = await self.patcher.execute({
                    "task_id": task_id, "files_written": files_written,
                    "issues": issues,
                })
                if not patch_result.get("ok"):
                    log.warning("Patcher failed: %s", patch_result.get("error"))
            else:
                log.info("Reviewer passed — no patching needed")

            self._task_results[task_id] = {
                "status": "completed",
                "files_written": files_written,
                "review_verdict": verdict,
                "issues_found": len(issues),
            }
            log.info("Studio pipeline completed: %s", task_id)

        except Exception as e:
            log.error("Studio pipeline error: %s — %s", task_id, e)
            self._task_results[task_id] = {
                "status": "failed", "stage": "pipeline", "error": str(e),
            }
        finally:
            self._active_builds -= 1

    async def submit_to_agent(self, agent_type: str, description: str) -> Dict[str, Any]:
        """Submit a task directly to a specific agent (via @agent mention)."""
        task_id = "studio-%s" % uuid.uuid4().hex[:8]
        task = {"task_id": task_id, "description": description}

        if agent_type == "architect":
            result = await self.architect.execute(task)
        elif agent_type == "builder":
            # For direct builder tasks, create a simple plan
            task["plan"] = {"files": [], "approach": description}
            result = await self.builder.execute(task)
        elif agent_type == "reviewer":
            # Extract file paths from description
            files = [w for w in description.split() if "/" in w or w.endswith(".py")]
            task["files_written"] = files
            result = await self.reviewer.execute(task)
        elif agent_type == "patcher":
            task["issues"] = [{"description": description}]
            task["files_written"] = []
            result = await self.patcher.execute(task)
        else:
            return {"ok": False, "error": "unknown agent type: %s" % agent_type}

        self._task_results[task_id] = {
            "status": "completed" if result.get("ok") else "failed",
            "agent": agent_type,
            "result": result,
        }
        return {"ok": True, "task_id": task_id, "result": result}

    def get_task_result(self, task_id: str) -> Optional[Dict]:
        return self._task_results.get(task_id)

    def get_metrics(self) -> Dict[str, Any]:
        """Get agent performance metrics from DB."""
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=5)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT agent_type,
                       COUNT(*) as total_tasks,
                       SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) as successes,
                       ROUND(AVG(duration_seconds), 2) as avg_duration,
                       SUM(tokens_used) as total_tokens
                FROM studio_metrics
                GROUP BY agent_type
            """).fetchall()
            conn.close()
            return {
                "by_agent": [dict(r) for r in rows],
                "active_builds": self._active_builds,
            }
        except Exception:
            return {"by_agent": [], "active_builds": self._active_builds}


# ── Global Singleton ──────────────────────────────────────────────────────────

_studio = None  # type: Optional[AgentStudio]


def get_studio() -> AgentStudio:
    """Get or create the global AgentStudio singleton."""
    global _studio
    if _studio is None:
        _studio = AgentStudio()
    return _studio


# ── Standalone Runner ─────────────────────────────────────────────────────────

async def _studio_loop():
    """Main loop: pull studio tasks from fleet queue and process them."""
    from core.fleet_task_queue import FleetTaskQueue
    from core.db_migrate import run_migrations

    run_migrations()
    studio = get_studio()
    studio._running = True
    q = FleetTaskQueue()
    log.info("Agent Studio daemon started")

    while True:
        try:
            # Clean expired locks every cycle
            expired = studio.lock_mgr.cleanup_expired()
            if expired:
                log.info("Cleaned %d expired file locks", expired)

            # Check for queued studio tasks
            if studio._active_builds < MAX_PARALLEL_BUILDS:
                task = q.claim("agent-studio", tier=5)
                if task:
                    task_type = task.get("task_type", "")
                    if task_type.startswith("studio_"):
                        input_data = json.loads(task.get("input_data", "{}"))
                        desc = input_data.get("description", "")
                        task_id = task.get("task_id", "")
                        if desc:
                            asyncio.create_task(
                                studio._run_pipeline(task_id, desc)
                            )
                            q.complete(task_id, {"started": True})

            await asyncio.sleep(5)
        except Exception as e:
            log.error("Studio loop error: %s", e)
            await asyncio.sleep(10)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    asyncio.run(_studio_loop())
