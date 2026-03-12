"""
Nexus Multi-Agent Build Pipeline — 5-stage feature development.

Stage 1: Architect — produces implementation plan
Stage 2: Builder  — implements files to staging directory
Stage 3: Reviewer — reviews staged files, line-level feedback
Stage 4: Patcher  — fixes review issues, re-saves patched files
Stage 5: Claude Gate — Telegram notification with diff + review for Kai

Default: Free models (Gemini/Groq/Mistral) via call_for_task().
If free models fail, Kai gets a Telegram asking to upgrade to Claude Sonnet ($).
Upgrade triggers re-run with AsyncAnthropic streaming.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("build_pipeline")

DB_PATH = Path.home() / ".nexus" / "memory.db"
STAGING_ROOT = Path.home() / "nexus" / "staging"
PROJECT_ROOT = Path.home() / "nexus"

# Maximum file size to read for context (100KB)
MAX_CONTEXT_BYTES = 100_000

# Claude Sonnet configuration (used only when upgraded)
CLAUDE_MODEL = "claude-sonnet-4-20250514"
INPUT_COST_PER_M = 3.00    # $/1M input tokens
OUTPUT_COST_PER_M = 15.00  # $/1M output tokens


class BuildPipeline:
    """Orchestrates multi-agent feature builds through 5 stages.

    Default: free models. Falls back to Claude Sonnet on Kai's approval.
    """

    def __init__(self):
        STAGING_ROOT.mkdir(parents=True, exist_ok=True)
        self._build_streams: Dict[int, List[asyncio.Queue]] = {}

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _get_claude_client(self):
        """Create AsyncAnthropic client from config."""
        from anthropic import AsyncAnthropic
        cfg_path = Path.home() / ".nexus" / "config.json"
        cfg = json.loads(cfg_path.read_text())
        return AsyncAnthropic(api_key=cfg["anthropic_key"])

    # ── SSE Streaming Pub/Sub ─────────────────────────────────────────────────

    def subscribe(self, build_id: int) -> asyncio.Queue:
        q = asyncio.Queue()  # type: asyncio.Queue
        self._build_streams.setdefault(build_id, []).append(q)
        return q

    def unsubscribe(self, build_id: int, q: asyncio.Queue):
        if build_id in self._build_streams:
            try:
                self._build_streams[build_id].remove(q)
            except ValueError:
                pass
            if not self._build_streams[build_id]:
                del self._build_streams[build_id]

    async def _broadcast(self, build_id: int, event: dict):
        for q in self._build_streams.get(build_id, []):
            try:
                await q.put(event)
            except Exception:
                pass

    # ── Build Lifecycle ──────────────────────────────────────────────────────

    def start_build(self, description: str, started_by: str = "system") -> Optional[int]:
        """Create a new build and launch the pipeline with free models."""
        now = datetime.utcnow().isoformat()
        conn = self._conn()
        try:
            cur = conn.execute(
                """INSERT INTO build_plans (description, status, started_at, created_at)
                   VALUES (?, 'architect', ?, ?)""",
                (description, now, now),
            )
            conn.commit()
            build_id = cur.lastrowid
        finally:
            conn.close()

        staging = STAGING_ROOT / ("build_%d" % build_id)
        staging.mkdir(parents=True, exist_ok=True)

        conn = self._conn()
        try:
            conn.execute(
                "UPDATE build_plans SET staging_path = ? WHERE id = ?",
                (str(staging), build_id),
            )
            conn.commit()
        finally:
            conn.close()

        log.info("Build %d created: %s", build_id, description[:80])

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._run_full_pipeline(build_id, use_claude=False))
        except RuntimeError:
            log.warning("Build %d: no event loop, pipeline must be started manually", build_id)
        return build_id

    def upgrade_build(self, build_id: int) -> bool:
        """Re-run a failed/stalled build from its failed stage using Claude Sonnet.
        Called when Kai approves the upgrade via Telegram /claude_build command.
        """
        build = self.get_build(build_id)
        if not build:
            return False

        status = build["status"]
        if status not in ("failed", "escalated"):
            log.warning("Build %d cannot be upgraded (status: %s)", build_id, status)
            return False

        log.info("Build %d: upgrading to Claude Sonnet", build_id)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._run_full_pipeline(build_id, use_claude=True))
            return True
        except RuntimeError:
            log.warning("Build %d: no event loop for upgrade", build_id)
            return False

    async def _run_full_pipeline(self, build_id: int, use_claude: bool = False):
        """Run all 4 stages, then send Claude Gate."""
        mode = "Claude Sonnet" if use_claude else "free models"
        log.info("Build %d: starting pipeline with %s", build_id, mode)

        try:
            # Determine start stage (for upgrades, resume from failed stage)
            build = self.get_build(build_id)
            start_stage = self._determine_start_stage(build) if use_claude else "architect"

            stages = ["architect", "builder", "reviewer", "patcher"]
            start_idx = stages.index(start_stage) if start_stage in stages else 0

            for stage in stages[start_idx:]:
                if stage == "architect":
                    ok = await self.run_architect(build_id, use_claude)
                elif stage == "builder":
                    ok = await self.run_builder(build_id, use_claude)
                elif stage == "reviewer":
                    ok = await self.run_reviewer(build_id, use_claude)
                elif stage == "patcher":
                    ok = await self.run_patcher(build_id, use_claude)

                if not ok:
                    await self._broadcast(build_id, {
                        "type": "error", "stage": stage, "message": "%s stage failed" % stage
                    })
                    if not use_claude:
                        # Free model failed — escalate to Kai
                        self._escalate_to_claude(build_id, stage)
                    return

            # All stages passed
            build = self.get_build(build_id)
            total_cost = build.get("cost_usd", 0) if build else 0

            await self._broadcast(build_id, {
                "type": "build_complete",
                "build_id": build_id,
                "total_cost": round(total_cost, 4),
            })

            self._send_claude_gate(build_id)

            if use_claude:
                self._check_first_build_milestone(build_id)

        except Exception as e:
            log.error("Build %d pipeline error: %s", build_id, e)
            self._update_status(build_id, "failed")
            await self._broadcast(build_id, {
                "type": "error", "stage": "pipeline", "message": str(e)
            })

    def _determine_start_stage(self, build: Optional[Dict]) -> str:
        """Figure out which stage to resume from based on build state."""
        if not build:
            return "architect"
        status = build.get("status", "")
        plan = build.get("plan_json", {})
        files = build.get("files", [])
        has_staged = any(f.get("staged_content") for f in files)

        if not plan or not plan.get("files"):
            return "architect"
        if not has_staged:
            return "builder"
        if not build.get("review_report"):
            return "reviewer"
        return "patcher"

    def _escalate_to_claude(self, build_id: int, failed_stage: str):
        """Notify Kai that free models failed and offer Claude upgrade."""
        self._update_status(build_id, "escalated")

        build = self.get_build(build_id)
        desc = build.get("description", "")[:150] if build else ""
        est = self._estimate_claude_cost(build_id, failed_stage)

        msg = (
            "*BUILD #%d — Free models failed*\n\n"
            "*Description:* %s\n"
            "*Failed at:* %s stage\n"
            "*Estimated Claude cost:* $%.2f\n\n"
            "To retry with Claude Sonnet, reply:\n"
            "`/claude_build %d`\n\n"
            "To reject: `/reject %d not worth the cost`"
        ) % (build_id, desc, failed_stage, est, build_id, build_id)

        self._notify_telegram(msg)
        log.info("Build %d: escalated to Kai (failed at %s, est $%.2f)",
                 build_id, failed_stage, est)

    def _estimate_claude_cost(self, build_id: int, from_stage: str) -> float:
        """Rough cost estimate for running remaining stages with Claude."""
        build = self.get_build(build_id)
        num_files = len((build or {}).get("plan_json", {}).get("files", [])) or 3

        # Per-stage estimates based on typical token usage
        stage_costs = {
            "architect": 0.05,
            "builder": 0.25 * num_files,  # ~$0.25 per file
            "reviewer": 0.10,
            "patcher": 0.15 * num_files,  # ~$0.15 per file with fixes
        }

        stages = ["architect", "builder", "reviewer", "patcher"]
        start_idx = stages.index(from_stage) if from_stage in stages else 0
        return sum(stage_costs.get(s, 0.10) for s in stages[start_idx:])

    # ── Build Queries ────────────────────────────────────────────────────────

    def get_build(self, build_id: int) -> Optional[Dict]:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM build_plans WHERE id = ?", (build_id,)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            d["plan_json"] = json.loads(d.get("plan_json") or "{}")
            d["files_written"] = json.loads(d.get("files_written") or "[]")
            d["patch_log"] = json.loads(d.get("patch_log") or "[]")
            d["stage_costs"] = json.loads(d.get("stage_costs") or "{}")

            files = conn.execute(
                "SELECT * FROM build_files WHERE build_id = ? ORDER BY id",
                (build_id,),
            ).fetchall()
            d["files"] = [dict(f) for f in files]
            return d
        finally:
            conn.close()

    def list_builds(self, status: Optional[str] = None, limit: int = 0) -> List[Dict]:
        conn = self._conn()
        try:
            if status:
                sql = "SELECT * FROM build_plans WHERE status = ? ORDER BY id DESC"
                params = (status,)  # type: Any
            else:
                sql = "SELECT * FROM build_plans ORDER BY id DESC"
                params = ()
            if limit > 0:
                sql += " LIMIT ?"
                params = params + (limit,)
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def reject_build(self, build_id: int, reason: str = "") -> bool:
        now = datetime.utcnow().isoformat()
        conn = self._conn()
        try:
            conn.execute(
                """UPDATE build_plans SET status = 'rejected',
                   claude_verdict = ?, completed_at = ? WHERE id = ?""",
                (reason or "Rejected by reviewer", now, build_id),
            )
            conn.commit()
            log.info("Build %d rejected: %s", build_id, reason[:80])
            return True
        finally:
            conn.close()

    # ── Stage 1: Architect ────────────────────────────────────────────────────

    async def run_architect(self, build_id: int, use_claude: bool = False) -> bool:
        """Generate implementation plan."""
        build = self.get_build(build_id)
        if not build:
            log.error("Build %d not found", build_id)
            return False

        description = build["description"]
        model_name = CLAUDE_MODEL if use_claude else "free"
        self._update_status(build_id, "architect", model_name)
        await self._broadcast(build_id, {
            "type": "stage_start", "stage": "architect",
            "model": model_name,
        })

        codebase_summary = self._get_codebase_summary()

        system = (
            "You are a software architect for a Python FastAPI monolith with SQLite.\n"
            "Produce detailed implementation plans with exact file paths and logic.\n"
            "Always output valid JSON with this structure:\n"
            '{"files": [{"path": "relative/path.py", "action": "create|modify", '
            '"description": "detailed description of changes"}], '
            '"summary": "3-sentence summary", '
            '"dependencies": ["any new pip packages"], '
            '"migration_needed": false, '
            '"notes": "important considerations"}'
        )

        if use_claude:
            context = self._build_context(build_id, "architect")
            user_msg = (
                "%s\n\n"
                "Create an implementation plan for this feature:\n\n%s\n\n"
                "Output ONLY valid JSON. No markdown, no explanation."
            ) % (context, description)
            collected, input_tokens, output_tokens = await self._call_claude(
                build_id, "architect", system, user_msg,
                max_tokens=8192, temperature=0.3,
            )
            if collected is None:
                return False
        else:
            user_msg = (
                "Create an implementation plan for this feature:\n\n"
                "%s\n\nExisting codebase structure:\n%s\n\n"
                "Output ONLY valid JSON. No markdown, no explanation."
            ) % (description, codebase_summary)

            from core.worker_pool import call_for_task
            result = await call_for_task(
                "build_architect",
                [{"role": "user", "content": user_msg}],
                system=system, max_tokens=4096, temperature=0.3,
            )
            if not result["ok"]:
                log.error("Architect failed for build %d: %s",
                          build_id, result.get("content", "")[:200])
                self._update_status(build_id, "failed")
                return False
            collected = result["content"]
            input_tokens = output_tokens = 0

        await self._broadcast(build_id, {
            "type": "stage_complete", "stage": "architect",
            "tokens": input_tokens + output_tokens,
            "cost": round(self._calc_cost(input_tokens, output_tokens), 4),
        })

        # Parse plan JSON
        plan = self._extract_json(collected)
        if not plan or "files" not in plan:
            log.error("Architect returned invalid plan for build %d", build_id)
            self._update_status(build_id, "failed")
            return False

        # Save plan and create build_files records
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE build_plans SET plan_json = ? WHERE id = ?",
                (json.dumps(plan), build_id),
            )
            now = datetime.utcnow().isoformat()
            for f in plan["files"]:
                original = ""
                if f.get("action") == "modify":
                    fpath = PROJECT_ROOT / f["path"]
                    if fpath.exists() and fpath.stat().st_size < MAX_CONTEXT_BYTES:
                        original = fpath.read_text(errors="replace")

                conn.execute(
                    """INSERT INTO build_files
                       (build_id, file_path, action, original_content, status, created_at)
                       VALUES (?, ?, ?, ?, 'pending', ?)""",
                    (build_id, f["path"], f.get("action", "create"), original, now),
                )
            conn.commit()
        finally:
            conn.close()

        log.info("Build %d architect complete: %d files planned", build_id, len(plan["files"]))
        self._update_status(build_id, "building", model_name)
        return True

    # ── Stage 2: Builder ──────────────────────────────────────────────────────

    async def run_builder(self, build_id: int, use_claude: bool = False) -> bool:
        """Implement each file."""
        build = self.get_build(build_id)
        if not build:
            return False

        plan = build["plan_json"]
        staging = Path(build.get("staging_path") or str(STAGING_ROOT / ("build_%d" % build_id)))
        staging.mkdir(parents=True, exist_ok=True)

        model_name = CLAUDE_MODEL if use_claude else "free"
        self._update_status(build_id, "building", model_name)
        await self._broadcast(build_id, {
            "type": "stage_start", "stage": "builder", "model": model_name,
        })

        files_written = []
        total_input = 0
        total_output = 0

        # Give all models codebase context, but trim for free models
        if use_claude:
            context = self._build_context(build_id, "builder")
        else:
            # Free models: lightweight context only (codebase structure + schema, no full files)
            context = "=== Codebase ===\n%s\n\n=== DB Schema ===\n%s" % (
                self._get_codebase_summary(), self._get_db_schema()[:4000]
            )

        for bf in build.get("files", []):
            file_path = bf["file_path"]
            action = bf.get("action", "create")

            file_desc = ""
            for pf in plan.get("files", []):
                if pf["path"] == file_path:
                    file_desc = pf.get("description", "")
                    break

            system = (
                "You are an expert Python developer. Write clean, production-ready code.\n"
                "Follow project conventions: async/await, logging (not print), "
                "parameterized SQL, Path.home() / '.nexus' for paths.\n"
                "Output ONLY the complete file content. No markdown fences, no explanation.\n"
                "Write the ENTIRE file — never output partial content or placeholders."
            )

            if action == "modify" and bf.get("original_content"):
                user_msg = (
                    "%s\n\n"
                    "Modify this existing file:\nFile: %s\nTask: %s\n\n"
                    "Current content:\n```\n%s\n```\n\n"
                    "Overall build plan:\n%s\n\n"
                    "Output the COMPLETE modified file."
                ) % (context, file_path, file_desc,
                     bf["original_content"][:80000 if use_claude else 50000],
                     plan.get("summary", ""))
            else:
                user_msg = (
                    "%s\n\n"
                    "Create this new file:\nFile: %s\nTask: %s\n\n"
                    "Overall build plan:\n%s\n\n"
                    "Output the COMPLETE file content."
                ) % (context, file_path, file_desc, plan.get("summary", ""))

            if use_claude:
                collected, inp, out = await self._call_claude(
                    build_id, "builder", system, user_msg,
                    max_tokens=16384, temperature=0.2, file_path=file_path,
                )
                if collected is None:
                    continue
                total_input += inp
                total_output += out
            else:
                from core.worker_pool import call_for_task
                result = await call_for_task(
                    "build_implement",
                    [{"role": "user", "content": user_msg}],
                    system=system, max_tokens=8192, temperature=0.2,
                )
                if not result["ok"]:
                    log.warning("Builder failed for %s in build %d", file_path, build_id)
                    continue
                collected = result["content"]

            content = self._strip_code_fences(collected)

            # Validate Python syntax before accepting
            if file_path.endswith(".py"):
                valid, err = self._validate_python(content)
                if not valid:
                    log.warning("Build %d: %s failed syntax check: %s",
                                build_id, file_path, err)
                    # For free models, try one more time with error feedback
                    if not use_claude:
                        retry_msg = (
                            "The code you generated for %s has a syntax error:\n%s\n\n"
                            "Fix the error and output the COMPLETE corrected file. "
                            "No markdown fences, no explanation, ONLY the Python code."
                        ) % (file_path, err)
                        retry_result = await call_for_task(
                            "build_implement",
                            [{"role": "user", "content": user_msg},
                             {"role": "assistant", "content": collected[:8000]},
                             {"role": "user", "content": retry_msg}],
                            system=system, max_tokens=8192, temperature=0.1,
                        )
                        if retry_result.get("ok"):
                            content = self._strip_code_fences(retry_result["content"])
                            valid2, err2 = self._validate_python(content)
                            if not valid2:
                                log.error("Build %d: %s failed syntax retry: %s",
                                          build_id, file_path, err2)
                                await self._broadcast(build_id, {
                                    "type": "error",
                                    "message": "Syntax error in %s after retry" % file_path,
                                })
                                continue
                        else:
                            continue

            staged_path = staging / file_path
            staged_path.parent.mkdir(parents=True, exist_ok=True)
            staged_path.write_text(content)
            files_written.append(file_path)

            conn = self._conn()
            try:
                conn.execute(
                    """UPDATE build_files SET staged_content = ?, status = 'written'
                       WHERE build_id = ? AND file_path = ?""",
                    (content, build_id, file_path),
                )
                conn.commit()
            finally:
                conn.close()

            log.info("Build %d: wrote %s (%d bytes)", build_id, file_path, len(content))

        if use_claude and (total_input or total_output):
            cost = self._calc_cost(total_input, total_output)
            self._record_stage_cost(build_id, "builder", total_input, total_output, cost)

        await self._broadcast(build_id, {
            "type": "stage_complete", "stage": "builder",
            "tokens": total_input + total_output,
            "cost": round(self._calc_cost(total_input, total_output), 4),
            "files_written": len(files_written),
        })

        conn = self._conn()
        try:
            conn.execute(
                "UPDATE build_plans SET files_written = ? WHERE id = ?",
                (json.dumps(files_written), build_id),
            )
            conn.commit()
        finally:
            conn.close()

        if not files_written:
            log.error("Build %d: no files written", build_id)
            self._update_status(build_id, "failed")
            return False

        log.info("Build %d builder complete: %d files", build_id, len(files_written))
        self._update_status(build_id, "reviewing", model_name)
        return True

    # ── Stage 3: Reviewer ─────────────────────────────────────────────────────

    async def run_reviewer(self, build_id: int, use_claude: bool = False) -> bool:
        """Review all staged files."""
        build = self.get_build(build_id)
        if not build:
            return False

        model_name = CLAUDE_MODEL if use_claude else "free"
        self._update_status(build_id, "reviewing", model_name)
        await self._broadcast(build_id, {
            "type": "stage_start", "stage": "reviewer", "model": model_name,
        })

        files_text = []
        for bf in build.get("files", []):
            if bf.get("staged_content"):
                files_text.append("=== %s ===\n%s" % (
                    bf["file_path"], bf["staged_content"][:50000]
                ))

        if not files_text:
            log.error("Build %d: no staged files to review", build_id)
            self._update_status(build_id, "failed")
            return False

        system = (
            "You are a senior code reviewer. Review the code for:\n"
            "1. Bugs and logic errors\n"
            "2. Security issues (SQL injection, path traversal, command injection)\n"
            "3. Missing imports or undefined references\n"
            "4. Convention violations (logging not print, async/await, parameterized SQL)\n\n"
            "Output JSON with this structure:\n"
            '{"score": 1-10, "issues": [{"file": "path", "line": N, '
            '"severity": "critical|warning|info", "message": "description", '
            '"fix": "suggested fix"}], "summary": "overall assessment"}'
        )

        all_files = "\n\n".join(files_text)
        user_msg = (
            "Review these files from build #%d:\n\n"
            "Build description: %s\n\n%s\n\n"
            "Output ONLY valid JSON."
        ) % (build_id, build.get("description", ""), all_files)

        if use_claude:
            collected, input_tokens, output_tokens = await self._call_claude(
                build_id, "reviewer", system, user_msg,
                max_tokens=8192, temperature=0.2,
            )
            if collected is None:
                return False
        else:
            from core.worker_pool import call_for_task
            result = await call_for_task(
                "build_review",
                [{"role": "user", "content": user_msg}],
                system=system, max_tokens=4096, temperature=0.2,
            )
            if not result["ok"]:
                log.error("Reviewer failed for build %d", build_id)
                self._update_status(build_id, "failed")
                return False
            collected = result["content"]
            input_tokens = output_tokens = 0

        if use_claude and (input_tokens or output_tokens):
            cost = self._calc_cost(input_tokens, output_tokens)
            self._record_stage_cost(build_id, "reviewer", input_tokens, output_tokens, cost)

        await self._broadcast(build_id, {
            "type": "stage_complete", "stage": "reviewer",
            "tokens": input_tokens + output_tokens,
            "cost": round(self._calc_cost(input_tokens, output_tokens), 4),
        })

        review = self._extract_json(collected)
        review_text = json.dumps(review) if review else collected

        conn = self._conn()
        try:
            conn.execute(
                "UPDATE build_plans SET review_report = ? WHERE id = ?",
                (review_text, build_id),
            )
            if review and "issues" in review:
                for issue in review["issues"]:
                    fpath = issue.get("file", "")
                    note = "[%s] L%s: %s" % (
                        issue.get("severity", "info"),
                        issue.get("line", "?"),
                        issue.get("message", ""),
                    )
                    conn.execute(
                        """UPDATE build_files SET review_notes =
                           COALESCE(review_notes, '') || ? || char(10)
                           WHERE build_id = ? AND file_path = ?""",
                        (note, build_id, fpath),
                    )
            conn.commit()
        finally:
            conn.close()

        score = review.get("score", 0) if review else 0
        log.info("Build %d review complete: score %d/10", build_id, score)

        # High score + no critical issues → skip patching
        has_critical = any(
            i.get("severity") == "critical"
            for i in (review or {}).get("issues", [])
        )
        if score >= 8 and not has_critical:
            log.info("Build %d: score %d, skipping patch", build_id, score)
            self._update_status(build_id, "claude_review")
            return True

        self._update_status(build_id, "patching", model_name)
        return True

    # ── Stage 4: Patcher ──────────────────────────────────────────────────────

    async def run_patcher(self, build_id: int, use_claude: bool = False) -> bool:
        """Fix issues found by reviewer."""
        build = self.get_build(build_id)
        if not build:
            return False

        # Check if patching is needed
        review_report = build.get("review_report", "")
        try:
            review = json.loads(review_report) if isinstance(review_report, str) else review_report
            score = review.get("score", 0) if isinstance(review, dict) else 0
            has_critical = any(
                i.get("severity") == "critical"
                for i in (review or {}).get("issues", [])
            )
            if score >= 8 and not has_critical:
                self._update_status(build_id, "claude_review")
                return True
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

        model_name = CLAUDE_MODEL if use_claude else "free"
        self._update_status(build_id, "patching", model_name)
        await self._broadcast(build_id, {
            "type": "stage_start", "stage": "patcher", "model": model_name,
        })

        staging = Path(build.get("staging_path") or str(STAGING_ROOT / ("build_%d" % build_id)))
        patch_log = []
        total_input = 0
        total_output = 0

        for bf in build.get("files", []):
            if not bf.get("staged_content"):
                continue

            review_notes = bf.get("review_notes", "").strip()
            if not review_notes:
                conn = self._conn()
                try:
                    conn.execute(
                        """UPDATE build_files SET patched_content = staged_content,
                           status = 'patched' WHERE build_id = ? AND file_path = ?""",
                        (build_id, bf["file_path"]),
                    )
                    conn.commit()
                finally:
                    conn.close()
                continue

            system = (
                "You are a code patcher. Fix all the issues described below.\n"
                "Output ONLY the complete fixed file. No markdown fences, no explanation.\n"
                "Preserve all working code. Only fix the identified issues."
            )

            user_msg = (
                "Fix the following issues in this file:\n\n"
                "File: %s\nIssues:\n%s\n\n"
                "Current content:\n```\n%s\n```\n\n"
                "Output the COMPLETE fixed file."
            ) % (bf["file_path"], review_notes, bf["staged_content"][:80000])

            if use_claude:
                collected, inp, out = await self._call_claude(
                    build_id, "patcher", system, user_msg,
                    max_tokens=16384, temperature=0.1,
                    file_path=bf["file_path"],
                )
                if collected is None:
                    patch_log.append({"file": bf["file_path"], "status": "failed"})
                    continue
                total_input += inp
                total_output += out
            else:
                from core.worker_pool import call_for_task
                result = await call_for_task(
                    "build_patch",
                    [{"role": "user", "content": user_msg}],
                    system=system, max_tokens=8192, temperature=0.1,
                )
                if not result["ok"]:
                    log.warning("Patcher failed for %s in build %d", bf["file_path"], build_id)
                    patch_log.append({"file": bf["file_path"], "status": "failed"})
                    continue
                collected = result["content"]

            patched = self._strip_code_fences(collected)

            staged_path = staging / bf["file_path"]
            staged_path.parent.mkdir(parents=True, exist_ok=True)
            staged_path.write_text(patched)

            conn = self._conn()
            try:
                conn.execute(
                    """UPDATE build_files SET patched_content = ?, status = 'patched'
                       WHERE build_id = ? AND file_path = ?""",
                    (patched, build_id, bf["file_path"]),
                )
                conn.commit()
            finally:
                conn.close()

            patch_log.append({"file": bf["file_path"], "status": "patched"})
            log.info("Build %d: patched %s", build_id, bf["file_path"])

        if use_claude and (total_input or total_output):
            cost = self._calc_cost(total_input, total_output)
            self._record_stage_cost(build_id, "patcher", total_input, total_output, cost)

        await self._broadcast(build_id, {
            "type": "stage_complete", "stage": "patcher",
            "tokens": total_input + total_output,
            "cost": round(self._calc_cost(total_input, total_output), 4),
        })

        conn = self._conn()
        try:
            conn.execute(
                "UPDATE build_plans SET patch_log = ? WHERE id = ?",
                (json.dumps(patch_log), build_id),
            )
            conn.commit()
        finally:
            conn.close()

        log.info("Build %d patcher complete: %d files", build_id, len(patch_log))
        self._update_status(build_id, "claude_review")
        return True

    # ── Claude API Helper ─────────────────────────────────────────────────────

    async def _call_claude(self, build_id: int, stage: str,
                           system: str, user_msg: str,
                           max_tokens: int = 8192,
                           temperature: float = 0.3,
                           file_path: str = "") -> tuple:
        """Call Claude Sonnet with streaming. Returns (text, input_tokens, output_tokens) or (None, 0, 0)."""
        client = self._get_claude_client()
        collected = ""
        input_tokens = 0
        output_tokens = 0

        try:
            async with client.messages.stream(
                model=CLAUDE_MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_msg}],
                temperature=temperature,
            ) as stream:
                async for text in stream.text_stream:
                    collected += text
                    event = {"type": "token", "stage": stage, "text": text}
                    if file_path:
                        event["file"] = file_path
                    await self._broadcast(build_id, event)

                final = await stream.get_final_message()
                input_tokens = final.usage.input_tokens
                output_tokens = final.usage.output_tokens

        except Exception as e:
            log.error("Claude API error (build %d, %s): %s", build_id, stage, e)
            self._update_status(build_id, "failed")
            return None, 0, 0

        cost = self._calc_cost(input_tokens, output_tokens)
        self._record_stage_cost(build_id, stage, input_tokens, output_tokens, cost)
        return collected, input_tokens, output_tokens

    # ── Stage 5: Claude Gate (Telegram) ───────────────────────────────────────

    def _send_claude_gate(self, build_id: int):
        build = self.get_build(build_id)
        if not build:
            return

        plan = build.get("plan_json", {})
        review = build.get("review_report", "")
        files = build.get("files", [])
        file_count = len([f for f in files if f.get("status") in ("written", "patched")])

        score = "?"
        try:
            r = json.loads(review) if isinstance(review, str) else review
            score = str(r.get("score", "?"))
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

        cost_usd = build.get("cost_usd", 0)
        stage_costs = build.get("stage_costs", {})
        cost_parts = []
        for sn in ("architect", "builder", "reviewer", "patcher"):
            sc = stage_costs.get(sn, {})
            if sc:
                cost_parts.append("%s $%.2f" % (sn, sc.get("cost", 0)))
        cost_line = " | ".join(cost_parts) if cost_parts else "free models"

        model = build.get("stage_model", "free")

        msg = (
            "*BUILD #%d — Ready for Review*\n\n"
            "*Description:* %s\n"
            "*Files:* %d | *Review score:* %s/10\n"
            "*Model:* %s\n"
        ) % (
            build_id,
            build.get("description", "")[:200],
            file_count,
            score,
            model,
        )

        if cost_usd > 0:
            msg += "*Cost:* $%.2f (%s)\n" % (cost_usd, cost_line)

        msg += (
            "\n*Summary:* %s\n\n"
            "Use `/approve %d` to merge or `/reject %d <reason>` to reject.\n"
            "View diff: `GET /api/builds/%d/diff`"
        ) % (
            plan.get("summary", "No summary")[:300],
            build_id, build_id, build_id,
        )

        self._notify_telegram(msg)
        log.info("Build %d: Claude Gate sent", build_id)

    # ── Merge ─────────────────────────────────────────────────────────────────

    def merge_build(self, build_id: int) -> Dict[str, Any]:
        build = self.get_build(build_id)
        if not build:
            return {"ok": False, "error": "Build not found"}

        if build["status"] not in ("claude_review", "approved"):
            return {"ok": False, "error": "Build not in reviewable state: %s" % build["status"]}

        merged_files = []
        errors = []

        for bf in build.get("files", []):
            file_path = bf["file_path"]
            content = bf.get("patched_content") or bf.get("staged_content")
            if not content:
                errors.append({"file": file_path, "error": "No content"})
                continue

            target = PROJECT_ROOT / file_path
            target.parent.mkdir(parents=True, exist_ok=True)

            if target.exists():
                backup = target.with_suffix(target.suffix + ".bak")
                shutil.copy2(target, backup)

            target.write_text(content)
            merged_files.append(file_path)

            conn = self._conn()
            try:
                conn.execute(
                    "UPDATE build_files SET status = 'merged' WHERE build_id = ? AND file_path = ?",
                    (build_id, file_path),
                )
                conn.commit()
            finally:
                conn.close()

            log.info("Build %d: merged %s", build_id, file_path)

        now = datetime.utcnow().isoformat()
        conn = self._conn()
        try:
            conn.execute(
                """UPDATE build_plans SET status = 'merged', claude_verdict = 'approved',
                   completed_at = ? WHERE id = ?""",
                (now, build_id),
            )
            conn.commit()
        finally:
            conn.close()

        report = {
            "ok": len(errors) == 0,
            "build_id": build_id,
            "merged": merged_files,
            "errors": errors,
        }

        self._notify_telegram(
            "*BUILD #%d MERGED*\n%d files deployed.\n%s" % (
                build_id, len(merged_files),
                "\n".join("- %s" % f for f in merged_files[:10]),
            )
        )
        log.info("Build %d merged: %d files, %d errors",
                 build_id, len(merged_files), len(errors))
        return report

    # ── Diff ──────────────────────────────────────────────────────────────────

    def get_build_diff(self, build_id: int) -> List[Dict]:
        build = self.get_build(build_id)
        if not build:
            return []

        diffs = []
        for bf in build.get("files", []):
            original = bf.get("original_content", "")
            final = bf.get("patched_content") or bf.get("staged_content", "")
            if not final:
                continue
            diffs.append({
                "file": bf["file_path"],
                "action": bf.get("action", "create"),
                "original_lines": original.count("\n"),
                "final_lines": final.count("\n"),
                "original_preview": original[:500] if original else "",
                "final_preview": final[:500],
            })
        return diffs

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _update_status(self, build_id: int, status: str, model: str = ""):
        conn = self._conn()
        try:
            if model:
                conn.execute(
                    "UPDATE build_plans SET status = ?, stage_model = ? WHERE id = ?",
                    (status, model, build_id),
                )
            else:
                conn.execute(
                    "UPDATE build_plans SET status = ? WHERE id = ?",
                    (status, build_id),
                )
            conn.commit()
        finally:
            conn.close()

    def _notify_telegram(self, text: str):
        try:
            cfg_path = Path.home() / ".nexus" / "config.json"
            if not cfg_path.exists():
                return
            cfg = json.loads(cfg_path.read_text())
            token = cfg.get("telegram_token", "")
            chat_ids = cfg.get("telegram_chat_ids", [])
            if not token or not chat_ids:
                return

            from urllib.request import Request, urlopen
            url = "https://api.telegram.org/bot%s/sendMessage" % token
            for cid in chat_ids:
                payload = json.dumps({
                    "chat_id": str(cid),
                    "text": text,
                    "parse_mode": "Markdown",
                }).encode()
                req = Request(url, data=payload,
                              headers={"Content-Type": "application/json"})
                urlopen(req, timeout=10)
        except Exception as e:
            log.warning("Telegram notification failed: %s", e)

    def _build_context(self, build_id: int, stage: str) -> str:
        """Build rich context string for Claude."""
        parts = []

        claude_md_path = PROJECT_ROOT / "CLAUDE.md"
        if claude_md_path.exists():
            parts.append("=== CLAUDE.md ===\n" + claude_md_path.read_text(errors="replace")[:8000])

        parts.append("=== Codebase ===\n" + self._get_codebase_summary())

        if stage != "architect":
            build = self.get_build(build_id)
            if build:
                for bf in build.get("files", []):
                    fpath = PROJECT_ROOT / bf["file_path"]
                    if fpath.exists() and fpath.stat().st_size < MAX_CONTEXT_BYTES:
                        parts.append("=== %s ===\n%s" % (
                            bf["file_path"], fpath.read_text(errors="replace")
                        ))

        parts.append("=== DB Schema ===\n" + self._get_db_schema())

        summaries = self._get_recent_build_summaries()
        if summaries:
            parts.append("=== Recent Builds ===\n" + summaries)

        return "\n\n".join(parts)

    def _get_codebase_summary(self) -> str:
        lines = []
        for d in ["core", "integrations", "agents", "telegram", "scripts", "static"]:
            dp = PROJECT_ROOT / d
            if dp.is_dir():
                files = sorted(f.name for f in dp.iterdir() if f.suffix == ".py")
                if files:
                    lines.append("%s/: %s" % (d, ", ".join(files[:20])))
        srv = PROJECT_ROOT / "server.py"
        if srv.exists():
            lines.append("server.py: %d lines" % sum(1 for _ in srv.open()))
        return "\n".join(lines)

    def _get_db_schema(self) -> str:
        try:
            conn = self._conn()
            rows = conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            conn.close()
            parts = []
            for row in rows:
                if row["name"].startswith("sqlite_"):
                    continue
                parts.append(row["sql"])
            return "\n\n".join(parts)
        except Exception:
            return "Schema unavailable"

    def _get_recent_build_summaries(self) -> str:
        try:
            conn = self._conn()
            rows = conn.execute(
                """SELECT id, description, status, cost_usd, review_report, completed_at
                   FROM build_plans WHERE status IN ('merged', 'rejected', 'completed')
                   ORDER BY id DESC LIMIT 3"""
            ).fetchall()
            conn.close()
            if not rows:
                return ""
            parts = []
            for r in rows:
                score = "?"
                try:
                    rev = json.loads(r["review_report"] or "{}")
                    score = str(rev.get("score", "?"))
                except (json.JSONDecodeError, TypeError):
                    pass
                parts.append("Build #%d (%s): %s | score %s | $%.2f" % (
                    r["id"], r["status"], r["description"][:100],
                    score, r["cost_usd"] or 0,
                ))
            return "\n".join(parts)
        except Exception:
            return ""

    @staticmethod
    def _calc_cost(input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * INPUT_COST_PER_M / 1_000_000) + \
               (output_tokens * OUTPUT_COST_PER_M / 1_000_000)

    def _record_stage_cost(self, build_id: int, stage: str,
                           inp: int, out: int, cost: float):
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT input_tokens, output_tokens, cost_usd, stage_costs "
                "FROM build_plans WHERE id = ?", (build_id,)
            ).fetchone()
            if not row:
                return

            stage_costs = json.loads(row["stage_costs"] or "{}")
            stage_costs[stage] = {"input": inp, "output": out, "cost": round(cost, 4)}

            conn.execute(
                """UPDATE build_plans
                   SET input_tokens = ?, output_tokens = ?, cost_usd = ?, stage_costs = ?
                   WHERE id = ?""",
                (
                    (row["input_tokens"] or 0) + inp,
                    (row["output_tokens"] or 0) + out,
                    round((row["cost_usd"] or 0) + cost, 4),
                    json.dumps(stage_costs),
                    build_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _check_first_build_milestone(self, build_id: int):
        try:
            conn = self._conn()
            count = conn.execute(
                "SELECT COUNT(*) FROM build_plans WHERE cost_usd > 0 AND status != 'failed'"
            ).fetchone()[0]
            conn.close()

            if count == 1:
                build = self.get_build(build_id)
                if not build:
                    return
                score = "?"
                try:
                    rev = json.loads(build.get("review_report") or "{}")
                    score = str(rev.get("score", "?"))
                except (json.JSONDecodeError, TypeError):
                    pass

                self._notify_telegram(
                    "*MILESTONE: First Claude-Powered Build Complete*\n\n"
                    "Build #%d: %s\n"
                    "Model: %s\n"
                    "Cost: $%.2f | Quality score: %s/10\n\n"
                    "The Build Pipeline is now running on Claude intelligence."
                    % (build_id, build.get("description", "")[:100],
                       CLAUDE_MODEL, build.get("cost_usd", 0), score)
                )
        except Exception as e:
            log.warning("First build milestone check failed: %s", e)

    @staticmethod
    def _validate_python(content: str) -> tuple:
        """Check if content is valid Python. Returns (ok, error_msg)."""
        import ast as _ast
        try:
            _ast.parse(content)
            return (True, "")
        except SyntaxError as e:
            return (False, "Line %s: %s" % (e.lineno, e.msg))

    @staticmethod
    def _extract_json(text: str) -> Optional[Dict]:
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    pass
        return None

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            return "\n".join(lines)
        return text


# ── Singleton ─────────────────────────────────────────────────────────────────

_pipeline = None  # type: Optional[BuildPipeline]


def get_build_pipeline() -> BuildPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = BuildPipeline()
    return _pipeline
