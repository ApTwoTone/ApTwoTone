"""
Nexus CodingAgent — Wraps Aider for autonomous code changes using free models.

Routes coding tasks through Aider (Groq/Gemini), NOT Claude.
Creates branches, runs aider, captures output, reports results.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("coding_agent")

NEXUS_ROOT = Path(__file__).parent.parent
VENV_PYTHON = NEXUS_ROOT / "venv" / "bin" / "python3"
AIDER_BIN = NEXUS_ROOT / "venv" / "bin" / "aider"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"
LOG_DIR = Path.home() / ".nexus" / "coding_agent_logs"


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


class CodingAgent:
    """Autonomous coding agent wrapping Aider with free models."""

    def __init__(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self._active_tasks = {}  # type: Dict[str, dict]

    async def execute_task(
        self,
        description: str,
        task_id: str = "",
        target_files: Optional[List[str]] = None,
        priority: int = 5,
        model: str = "",
    ) -> Dict:
        """Run Aider to complete a coding task.

        Returns: {"ok": bool, "output": str, "branch": str, "files_changed": list}
        """
        task_id = task_id or ("code-%d" % int(time.time()))
        branch = "aider/%s" % task_id
        log.info("CodingAgent starting task %s: %s", task_id, description[:100])

        self._active_tasks[task_id] = {
            "status": "running",
            "description": description,
            "started_at": time.time(),
        }

        try:
            # Create branch
            await self._run_git(["checkout", "-b", branch])

            # Run aider
            output = await self._run_aider(description, target_files, model)

            # Check what changed
            diff_out = await self._run_git(["diff", "--stat", "HEAD~1"])
            files_changed = [
                line.split("|")[0].strip()
                for line in diff_out.strip().split("\n")
                if "|" in line
            ]

            # Switch back to original branch
            await self._run_git(["checkout", "-"])

            self._active_tasks[task_id]["status"] = "completed"

            result = {
                "ok": True,
                "task_id": task_id,
                "output": output[-2000:],  # Last 2K chars
                "branch": branch,
                "files_changed": files_changed,
            }

            # Log to shared brain
            try:
                from core.shared_brain import get_brain
                get_brain().log_task(
                    "coding", description[:200], output[-500:],
                    True, "aider", model or "groq/llama-3.3-70b",
                    0, int((time.time() - self._active_tasks[task_id]["started_at"]) * 1000),
                    "coding_agent",
                )
            except Exception:
                pass

            # Notify via Telegram
            await self._notify_result(task_id, description, result)

            log.info("CodingAgent completed %s: %d files changed", task_id, len(files_changed))
            return result

        except Exception as e:
            log.error("CodingAgent task %s failed: %s", task_id, e)
            self._active_tasks[task_id]["status"] = "failed"

            # Switch back to original branch on failure
            try:
                await self._run_git(["checkout", "-"])
            except Exception:
                pass

            return {
                "ok": False,
                "task_id": task_id,
                "output": str(e),
                "branch": branch,
                "files_changed": [],
            }
        finally:
            self._active_tasks.pop(task_id, None)

    async def _run_aider(
        self,
        prompt: str,
        target_files: Optional[List[str]] = None,
        model: str = "",
    ) -> str:
        """Run aider as subprocess with timeout."""
        env = self._build_env()

        cmd = [str(AIDER_BIN)]

        if model:
            cmd.extend(["--model", model])

        # Add target files
        if target_files:
            for f in target_files:
                cmd.append(str(f))

        cmd.extend(["--message", prompt])

        log_file = LOG_DIR / ("aider-%d.log" % int(time.time()))

        log.info("Running aider: %s", " ".join(cmd[:6]) + "...")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(NEXUS_ROOT),
            env=env,
        )

        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
            output = stdout.decode("utf-8", errors="replace")
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            output = "TIMEOUT: Aider did not complete within 5 minutes"
            log.warning("Aider timed out for prompt: %s", prompt[:100])

        # Save log
        try:
            log_file.write_text(output)
        except Exception:
            pass

        return output

    async def _run_git(self, args: List[str]) -> str:
        """Run a git command and return output."""
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(NEXUS_ROOT),
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError("git %s failed: %s" % (args[0], stderr.decode()))
        return stdout.decode("utf-8", errors="replace")

    async def _notify_result(self, task_id: str, description: str, result: Dict):
        """Send Telegram notification about task completion."""
        try:
            cfg = _load_config()
            tg_token = cfg.get("telegram_token", "")
            tg_chat = cfg.get("telegram_chat_id", "")
            if not tg_token or not tg_chat:
                return

            status = "Done" if result["ok"] else "FAILED"
            files = ", ".join(result.get("files_changed", [])[:5]) or "none"
            msg = (
                "AIDER %s\n"
                "Task: %s\n"
                "Branch: %s\n"
                "Files: %s"
            ) % (status, description[:100], result.get("branch", "?"), files)

            import httpx
            async with httpx.AsyncClient(timeout=10) as c:
                await c.post(
                    "https://api.telegram.org/bot%s/sendMessage" % tg_token,
                    json={"chat_id": tg_chat, "text": msg},
                )
        except Exception as e:
            log.warning("Telegram notify failed: %s", e)

    def _build_env(self) -> dict:
        """Build environment with API keys for Aider."""
        env = os.environ.copy()
        cfg = _load_config()

        # Groq (primary for Aider)
        groq_key = cfg.get("groq_api_key", "") or os.getenv("GROQ_API_KEY", "")
        if groq_key:
            env["GROQ_API_KEY"] = groq_key

        # Gemini (fallback)
        gemini_key = cfg.get("gemini_key", "") or os.getenv("GEMINI_API_KEY", "")
        if gemini_key:
            env["GEMINI_API_KEY"] = gemini_key

        # OpenRouter (secondary fallback)
        or_key = cfg.get("openrouter_api_key", "") or os.getenv("OPENROUTER_API_KEY", "")
        if or_key:
            env["OPENROUTER_API_KEY"] = or_key

        env["PYTHONPATH"] = str(NEXUS_ROOT)
        return env

    def get_active_tasks(self) -> Dict:
        return dict(self._active_tasks)


# Singleton
_agent = None  # type: Optional[CodingAgent]


def get_coding_agent() -> CodingAgent:
    global _agent
    if _agent is None:
        _agent = CodingAgent()
    return _agent
