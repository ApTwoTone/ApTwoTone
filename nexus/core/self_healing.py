"""
Nexus Self-Healing Agent — Monitors system health every 5 minutes.

Checks: API endpoints, launchd daemons, DB integrity, worker processes,
error rates. On issue: check brain for known fix → apply → unknown? log
and notify via Telegram.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("self_healing")

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"
CHECK_INTERVAL = 300  # 5 minutes
PID_DIR = Path.home() / ".nexus" / "pids"


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


class SelfHealingAgent:
    """Autonomous health monitor that detects and fixes issues."""

    def __init__(self):
        self._check_history = []  # type: List[Dict]
        self._running = False

    async def run_loop(self):
        """Main monitoring loop. Call from asyncio.create_task() at startup."""
        self._running = True
        log.info("Self-healing agent started (interval: %ds)", CHECK_INTERVAL)
        while self._running:
            try:
                result = await self.run_full_check()
                self._check_history.append(result)
                # Keep last 100 checks
                if len(self._check_history) > 100:
                    self._check_history = self._check_history[-100:]
            except Exception as e:
                log.error("Self-healing check failed: %s", e)
            await asyncio.sleep(CHECK_INTERVAL)

    def stop(self):
        self._running = False

    async def run_full_check(self) -> Dict:
        """Run all health checks and return aggregated result."""
        checks = {}
        start = time.time()

        # Run all checks concurrently
        results = await asyncio.gather(
            self._check_db_integrity(),
            self._check_api_server(),
            self._check_workers(),
            self._check_launchd_daemons(),
            self._check_error_rates(),
            self._check_disk_space(),
            return_exceptions=True,
        )

        labels = ["db_integrity", "api_server", "workers", "launchd_daemons",
                   "error_rates", "disk_space"]

        issues = []
        for label, result in zip(labels, results):
            if isinstance(result, Exception):
                checks[label] = {"status": "error", "error": str(result)}
                issues.append("%s: %s" % (label, str(result)))
            else:
                checks[label] = result
                if result.get("status") != "healthy":
                    issues.append("%s: %s" % (label, result.get("message", "unhealthy")))

        overall = "healthy" if not issues else "degraded"
        elapsed_ms = int((time.time() - start) * 1000)

        report = {
            "timestamp": time.time(),
            "overall": overall,
            "checks": checks,
            "issues": issues,
            "elapsed_ms": elapsed_ms,
        }

        # Handle issues
        if issues:
            log.warning("Health check found %d issues: %s", len(issues), "; ".join(issues))
            await self._handle_issues(issues, checks)

        return report

    async def _check_db_integrity(self) -> Dict:
        """Check SQLite database integrity."""
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=5)
            result = conn.execute("PRAGMA integrity_check").fetchone()
            conn.close()
            if result and result[0] == "ok":
                return {"status": "healthy"}
            return {"status": "degraded", "message": "integrity check: %s" % str(result)}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    async def _check_api_server(self) -> Dict:
        """Check if FastAPI server is responding."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get("http://localhost:7860/api/revenue/summary")
                if r.status_code == 200:
                    return {"status": "healthy"}
                return {"status": "degraded", "message": "HTTP %d" % r.status_code}
        except Exception as e:
            return {"status": "error", "message": "API unreachable: %s" % str(e)}

    async def _check_workers(self) -> Dict:
        """Check worker process health via PID files."""
        try:
            if not PID_DIR.exists():
                return {"status": "degraded", "message": "PID dir missing"}

            alive = 0
            dead = 0
            for pid_file in PID_DIR.glob("*.pid"):
                try:
                    pid = int(pid_file.read_text().strip())
                    os.kill(pid, 0)  # Signal 0 = check if alive
                    alive += 1
                except (ProcessLookupError, ValueError, OSError):
                    dead += 1

            if dead > 0:
                return {
                    "status": "degraded",
                    "message": "%d dead workers (of %d)" % (dead, alive + dead),
                    "alive": alive, "dead": dead,
                }
            return {"status": "healthy", "alive": alive}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    async def _check_launchd_daemons(self) -> Dict:
        """Check critical launchd services."""
        expected = [
            "com.zoar.nexus-server",
            "com.zoar.telegram-bot",
            "com.zoar.process-manager",
            "com.zoar.vendor-discovery",
            "com.zoar.master-coordinator",
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                "launchctl", "list",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            output = stdout.decode()

            missing = []
            for svc in expected:
                if svc not in output:
                    missing.append(svc)

            if missing:
                return {
                    "status": "degraded",
                    "message": "Missing: %s" % ", ".join(missing),
                    "missing": missing,
                }
            return {"status": "healthy"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    async def _check_error_rates(self) -> Dict:
        """Check dead letter queue rate in last hour."""
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=5)
            conn.row_factory = sqlite3.Row

            # Use compatible SQL (no FILTER clause for older SQLite)
            dead_row = conn.execute("""
                SELECT COUNT(*) as cnt FROM fleet_tasks
                WHERE status = 'dead_letter' AND created_at > datetime('now', '-1 hour')
            """).fetchone()
            total_row = conn.execute("""
                SELECT COUNT(*) as cnt FROM fleet_tasks
                WHERE created_at > datetime('now', '-1 hour')
            """).fetchone()
            conn.close()

            total = total_row["cnt"] if total_row else 0
            dead = dead_row["cnt"] if dead_row else 0

            if total == 0:
                return {"status": "healthy", "dead_letter_pct": 0}

            pct = (dead / total) * 100
            if pct > 20:
                return {
                    "status": "degraded",
                    "message": "%.0f%% dead letter rate (%d/%d)" % (pct, dead, total),
                    "dead_letter_pct": round(pct, 1),
                }
            return {"status": "healthy", "dead_letter_pct": round(pct, 1)}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    async def _check_disk_space(self) -> Dict:
        """Check available disk space."""
        try:
            stat = os.statvfs(str(Path.home()))
            free_gb = (stat.f_bavail * stat.f_frsize) / (1024 ** 3)
            if free_gb < 5:
                return {
                    "status": "degraded",
                    "message": "Low disk: %.1f GB free" % free_gb,
                    "free_gb": round(free_gb, 1),
                }
            return {"status": "healthy", "free_gb": round(free_gb, 1)}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    async def _handle_issues(self, issues: List[str], checks: Dict):
        """Attempt to resolve known issues. Notify on Telegram."""
        from core.shared_brain import get_brain
        brain = get_brain()

        for issue in issues:
            # Check brain for known fix — LOG ONLY, never auto-dispatch
            known = brain.check_known_error("health_check", issue)
            if known and known.get("auto_fixable") and known.get("fix_code"):
                log.warning("Known fix available for: %s — use /repair command to apply", issue)
            else:
                brain.log_error("health_check", issue)

        # Send Telegram summary
        await self._notify_issues(issues)

    async def _notify_issues(self, issues: List[str]):
        """Send health alert to Telegram."""
        try:
            cfg = _load_config()
            tg_token = cfg.get("telegram_token", "")
            tg_chat = cfg.get("telegram_chat_id", "")
            if not tg_token or not tg_chat:
                return

            msg = "HEALTH ALERT\n%d issues detected:\n%s" % (
                len(issues), "\n".join("- %s" % i for i in issues[:5])
            )

            import httpx
            async with httpx.AsyncClient(timeout=10) as c:
                await c.post(
                    "https://api.telegram.org/bot%s/sendMessage" % tg_token,
                    json={"chat_id": tg_chat, "text": msg},
                )
        except Exception as e:
            log.warning("Health alert Telegram notify failed: %s", e)

    def get_history(self, limit: int = 20) -> List[Dict]:
        return self._check_history[-limit:]


# Singleton
_healer = None  # type: Optional[SelfHealingAgent]


def get_self_healer() -> SelfHealingAgent:
    global _healer
    if _healer is None:
        _healer = SelfHealingAgent()
    return _healer
