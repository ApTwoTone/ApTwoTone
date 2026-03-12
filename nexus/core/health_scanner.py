"""
Nexus Health Scanner — Deep health monitoring for the self-healing flywheel.

Division 4 (Self-Healing) runs this every 60 seconds. Six scan types detect
issues, then the diagnosis/repair/verify/learn pipeline resolves them
autonomously using the free model fleet.

Scan types: endpoints, logs, db_schema, imports, frontend, processes.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("health_scanner")

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"
SESSION_LOG_DIR = Path.home() / ".nexus" / "session_logs"
NEXUS_ROOT = Path(__file__).parent.parent

API_BASE = "http://localhost:7860"
FRONTEND_BASE = "http://localhost:3000"

# Endpoints to check: (path, method, expected_status)
CRITICAL_ENDPOINTS = [
    ("/api/health", "GET", 200),
    ("/api/fleet/status", "GET", 200),
    ("/api/builds?status=", "GET", 200),
    ("/api/talk/history?limit=1", "GET", 200),
    ("/api/coordinator/status", "GET", 200),
]

REQUIRED_DAEMONS = [
    "com.zoar.nexus-server",
    "com.zoar.telegram-bot",
    "com.zoar.master-coordinator",
    "com.zoar.nextjs-dev",
    "com.zoar.heartbeat",
]

# Expected DB tables (subset — the critical ones)
EXPECTED_TABLES = [
    "leads", "lead_events", "vendors", "vendor_outreach",
    "fleet_tasks", "task_chains", "build_plans", "build_files",
    "nexus_chat", "brain_system_knowledge", "brain_error_log",
    "brain_task_history", "health_events", "model_performance",
    "repair_history", "managed_processes",
]

# Core modules that must import cleanly
CORE_MODULES = [
    "core.db_migrate", "core.shared_brain", "core.key_rotation",
    "core.worker_pool", "core.fleet_task_queue", "core.task_chains",
    "core.build_pipeline", "core.self_healing", "core.agent_activity",
    "core.resource_monitor", "core.master_coordinator", "core.coding_agent",
    "core.nexus_router", "core.worker_tiers",
]

# Max severity that can be auto-fixed (9-10 = Telegram only)
MAX_AUTO_FIX_SEVERITY = 8
# Security-sensitive paths — never auto-fix
PROTECTED_PATHS = ["core/auth.py", "core/claude_guard.py", "integrations/messaging.py"]


def _now_iso():
    # type: () -> str
    return datetime.utcnow().isoformat()


def _conn():
    # type: () -> sqlite3.Connection
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# ── Health Event persistence ──────────────────────────────────────────

def _save_event(event):
    # type: (Dict) -> int
    """Write a health event to the DB. Returns the event id."""
    conn = _conn()
    try:
        cur = conn.execute(
            """INSERT INTO health_events (check_type, target, status, message, detected_at)
               VALUES (?, ?, ?, ?, ?)""",
            (event["check_type"], event["target"], event["status"],
             event.get("message", ""), event.get("detected_at", _now_iso())),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _resolve_event(event_id, resolved_by):
    # type: (int, str) -> None
    conn = _conn()
    try:
        conn.execute(
            "UPDATE health_events SET resolved_by = ?, resolved_at = ? WHERE id = ?",
            (resolved_by, _now_iso(), event_id),
        )
        conn.commit()
    finally:
        conn.close()


def _save_repair(repair):
    # type: (Dict) -> int
    conn = _conn()
    try:
        cur = conn.execute(
            """INSERT INTO repair_history
               (health_event_id, chain_id, diagnosis, fix_approach,
                files_changed, diff, status, model_used, tokens_used,
                duration_ms, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (repair.get("health_event_id"), repair.get("chain_id", ""),
             repair.get("diagnosis", ""), repair.get("fix_approach", ""),
             json.dumps(repair.get("files_changed", [])),
             repair.get("diff", ""), repair["status"],
             repair.get("model_used", ""), repair.get("tokens_used", 0),
             repair.get("duration_ms", 0), _now_iso()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _save_model_perf(model, provider, task_type, success, tokens=0, latency=0, error_msg=""):
    # type: (str, str, str, bool, int, int, str) -> None
    conn = _conn()
    try:
        conn.execute(
            """INSERT INTO model_performance
               (model, provider, task_type, success, tokens_used,
                latency_ms, error_message, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (model, provider, task_type, int(success), tokens,
             latency, error_msg, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


# ── Scanner Class ─────────────────────────────────────────────────────

class HealthScanner:
    """Deep health scanner for the Nexus self-healing flywheel."""

    def __init__(self):
        self._last_scan_result = {}  # type: Dict[str, Any]
        self._scan_count = 0

    # ── Public API ────────────────────────────────────────────────────

    async def run_full_scan(self):
        # type: () -> List[Dict]
        """Run all 6 scans concurrently. Returns list of issues (non-ok events)."""
        self._scan_count += 1
        start = time.time()

        results = await asyncio.gather(
            self.scan_endpoints(),
            self.scan_logs(),
            self.scan_schema(),
            self.scan_imports(),
            self.scan_frontend(),
            self.scan_processes(),
            return_exceptions=True,
        )

        all_events = []  # type: List[Dict]
        labels = ["endpoints", "logs", "db_schema", "imports", "frontend", "processes"]
        for label, result in zip(labels, results):
            if isinstance(result, Exception):
                log.error("Scan %s failed: %s", label, result)
                all_events.append({
                    "check_type": label,
                    "target": "scan_itself",
                    "status": "error",
                    "message": "Scan crashed: %s" % str(result)[:200],
                    "detected_at": _now_iso(),
                })
            else:
                all_events.extend(result)

        issues = [e for e in all_events if e["status"] != "ok"]
        elapsed = time.time() - start

        # Persist issues to DB
        for issue in issues:
            issue["id"] = _save_event(issue)

        self._last_scan_result = {
            "scan_number": self._scan_count,
            "elapsed_ms": int(elapsed * 1000),
            "total_checks": len(all_events),
            "issues_found": len(issues),
            "timestamp": _now_iso(),
        }

        if issues:
            log.warning("Health scan #%d: %d issues found in %dms",
                        self._scan_count, len(issues), int(elapsed * 1000))
        else:
            log.debug("Health scan #%d: all clear (%dms)",
                      self._scan_count, int(elapsed * 1000))

        # Broadcast via agent activity
        try:
            from core.agent_activity import log_activity
            log_activity(
                agent_id="health_scanner", dept_id="d4",
                action="scan_complete",
                detail="Scan #%d: %d issues, %dms" % (
                    self._scan_count, len(issues), int(elapsed * 1000)),
                target="system", status="warning" if issues else "ok",
            )
        except Exception:
            pass

        return issues

    def get_last_scan(self):
        # type: () -> Dict
        return self._last_scan_result

    # ── Scan Type 1: Endpoints ────────────────────────────────────────

    async def scan_endpoints(self):
        # type: () -> List[Dict]
        """Hit critical API endpoints, check status codes and response times."""
        import httpx
        events = []
        async with httpx.AsyncClient(timeout=5.0) as client:
            for path, method, expected in CRITICAL_ENDPOINTS:
                url = API_BASE + path
                try:
                    start = time.time()
                    if method == "GET":
                        resp = await client.get(url)
                    else:
                        resp = await client.post(url)
                    elapsed = time.time() - start

                    if resp.status_code != expected:
                        events.append({
                            "check_type": "endpoint",
                            "target": path,
                            "status": "error",
                            "message": "Expected %d, got %d (%.1fs)" % (
                                expected, resp.status_code, elapsed),
                            "detected_at": _now_iso(),
                        })
                    elif elapsed > 5.0:
                        events.append({
                            "check_type": "endpoint",
                            "target": path,
                            "status": "warning",
                            "message": "Slow response: %.1fs" % elapsed,
                            "detected_at": _now_iso(),
                        })
                    else:
                        events.append({
                            "check_type": "endpoint",
                            "target": path,
                            "status": "ok",
                            "message": "%d in %.1fs" % (resp.status_code, elapsed),
                            "detected_at": _now_iso(),
                        })
                except Exception as e:
                    events.append({
                        "check_type": "endpoint",
                        "target": path,
                        "status": "critical",
                        "message": "Connection failed: %s" % str(e)[:200],
                        "detected_at": _now_iso(),
                    })
        return events

    # ── Scan Type 2: Log Errors ───────────────────────────────────────

    async def scan_logs(self):
        # type: () -> List[Dict]
        """Parse recent server logs for errors and exceptions."""
        events = []
        if not SESSION_LOG_DIR.exists():
            return events

        error_sigs = {}  # type: Dict[str, int]

        for log_file in sorted(SESSION_LOG_DIR.glob("*.log"), reverse=True)[:3]:
            try:
                text = log_file.read_text(errors="replace")
                lines = text.split("\n")
                for line in lines:
                    if not any(kw in line for kw in ["ERROR", "CRITICAL", "Traceback"]):
                        continue
                    sig = line.strip()[:200]
                    if sig in error_sigs:
                        error_sigs[sig] += 1
                    else:
                        error_sigs[sig] = 1
            except Exception:
                continue

        for sig, count in list(error_sigs.items())[:10]:
            severity = "error" if count > 3 else "warning"
            events.append({
                "check_type": "log_error",
                "target": sig[:100],
                "status": severity,
                "message": "%s (x%d in recent logs)" % (sig[:200], count),
                "detected_at": _now_iso(),
            })

        if not error_sigs:
            events.append({
                "check_type": "log_error",
                "target": "logs",
                "status": "ok",
                "message": "No errors in recent logs",
                "detected_at": _now_iso(),
            })

        return events

    # ── Scan Type 3: DB Schema Drift ──────────────────────────────────

    async def scan_schema(self):
        # type: () -> List[Dict]
        """Compare expected tables against actual DB schema."""
        events = []
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            actual_tables = {r["name"] for r in rows}

            missing = []
            for table in EXPECTED_TABLES:
                if table not in actual_tables:
                    missing.append(table)

            if missing:
                events.append({
                    "check_type": "db_schema",
                    "target": "tables",
                    "status": "error",
                    "message": "Missing tables: %s" % ", ".join(missing),
                    "detected_at": _now_iso(),
                })
            else:
                events.append({
                    "check_type": "db_schema",
                    "target": "tables",
                    "status": "ok",
                    "message": "%d expected tables present" % len(EXPECTED_TABLES),
                    "detected_at": _now_iso(),
                })
        except Exception as e:
            events.append({
                "check_type": "db_schema",
                "target": "database",
                "status": "critical",
                "message": "DB access failed: %s" % str(e)[:200],
                "detected_at": _now_iso(),
            })
        finally:
            conn.close()
        return events

    # ── Scan Type 4: Python Import Check ──────────────────────────────

    async def scan_imports(self):
        # type: () -> List[Dict]
        """Try importing core modules to catch broken imports."""
        events = []
        for module_name in CORE_MODULES:
            try:
                importlib.import_module(module_name)
                events.append({
                    "check_type": "import",
                    "target": module_name,
                    "status": "ok",
                    "message": "Import OK",
                    "detected_at": _now_iso(),
                })
            except Exception as e:
                events.append({
                    "check_type": "import",
                    "target": module_name,
                    "status": "error",
                    "message": "Import failed: %s" % str(e)[:200],
                    "detected_at": _now_iso(),
                })
        return events

    # ── Scan Type 5: Frontend Health ──────────────────────────────────

    async def scan_frontend(self):
        # type: () -> List[Dict]
        """Check if Next.js dev server responds."""
        import httpx
        events = []
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(FRONTEND_BASE)
                if resp.status_code == 200:
                    events.append({
                        "check_type": "frontend",
                        "target": FRONTEND_BASE,
                        "status": "ok",
                        "message": "Next.js dev server responding",
                        "detected_at": _now_iso(),
                    })
                else:
                    events.append({
                        "check_type": "frontend",
                        "target": FRONTEND_BASE,
                        "status": "error",
                        "message": "Status %d" % resp.status_code,
                        "detected_at": _now_iso(),
                    })
        except Exception as e:
            events.append({
                "check_type": "frontend",
                "target": FRONTEND_BASE,
                "status": "critical",
                "message": "Frontend unreachable: %s" % str(e)[:200],
                "detected_at": _now_iso(),
            })
        return events

    # ── Scan Type 6: Process Scanner ──────────────────────────────────

    async def scan_processes(self):
        # type: () -> List[Dict]
        """Check launchd daemons are loaded and running."""
        events = []
        try:
            proc = await asyncio.create_subprocess_exec(
                "launchctl", "list",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            output = stdout.decode("utf-8", errors="replace")

            for daemon in REQUIRED_DAEMONS:
                found = False
                for line in output.split("\n"):
                    if daemon in line:
                        found = True
                        parts = line.split()
                        pid = parts[0] if len(parts) >= 1 else "-"
                        exit_code = parts[1] if len(parts) >= 2 else "0"
                        if pid == "-":
                            events.append({
                                "check_type": "process",
                                "target": daemon,
                                "status": "warning",
                                "message": "Loaded but not running (exit: %s)" % exit_code,
                                "detected_at": _now_iso(),
                            })
                        else:
                            events.append({
                                "check_type": "process",
                                "target": daemon,
                                "status": "ok",
                                "message": "Running (PID %s)" % pid,
                                "detected_at": _now_iso(),
                            })
                        break
                if not found:
                    events.append({
                        "check_type": "process",
                        "target": daemon,
                        "status": "error",
                        "message": "Daemon not loaded",
                        "detected_at": _now_iso(),
                    })
        except Exception as e:
            events.append({
                "check_type": "process",
                "target": "launchctl",
                "status": "error",
                "message": "Failed to query launchd: %s" % str(e)[:200],
                "detected_at": _now_iso(),
            })
        return events

    # ── Diagnosis Engine ──────────────────────────────────────────────

    async def diagnose(self, event):
        # type: (Dict) -> Dict
        """Determine root cause and fix approach for a health event."""
        from core.shared_brain import SharedBrain
        brain = SharedBrain()

        # Step 1: Check brain for known fix
        known = brain.check_known_error(event["check_type"], event.get("message", ""))
        if known and known.get("auto_fixable") and known.get("occurrences", 0) >= 2:
            log.info("Brain has known fix for %s: %s", event["check_type"], event["target"])
            return {
                "source": "brain_lookup",
                "root_cause": known.get("solution", "Known issue"),
                "fix_approach": "Apply known fix from brain",
                "fix_code": known.get("fix_code", ""),
                "files": [],
                "severity": 2,
                "auto_fixable": True,
            }

        # Step 2: For process issues, use direct fix (no AI needed)
        if event["check_type"] == "process":
            daemon = event["target"]
            return {
                "source": "direct",
                "root_cause": "Daemon %s not running" % daemon,
                "fix_approach": "Restart via launchctl",
                "fix_code": "",
                "files": [],
                "severity": 3,
                "auto_fixable": True,
                "restart_daemon": daemon,
            }

        # Step 3: For DB schema issues, re-run migrations
        if event["check_type"] == "db_schema":
            return {
                "source": "direct",
                "root_cause": event.get("message", "Schema drift"),
                "fix_approach": "Re-run migrations",
                "fix_code": "from core.db_migrate import run_migrations; run_migrations()",
                "files": ["core/db_migrate.py"],
                "severity": 3,
                "auto_fixable": True,
            }

        # Step 4: For import errors, identify the file
        if event["check_type"] == "import":
            module = event["target"]
            return {
                "source": "direct",
                "root_cause": "Module %s has import error" % module,
                "fix_approach": "Analyze and fix import",
                "fix_code": "",
                "files": [module.replace(".", "/") + ".py"],
                "severity": 5,
                "auto_fixable": True,
            }

        # Step 5: For endpoint/log errors, use AI diagnosis
        try:
            from core.worker_pool import call_for_task
            prompt = self._build_diagnosis_prompt(event)
            result = await call_for_task(
                "analysis",
                [{"role": "user", "content": prompt}],
                system=(
                    "You are a Python debugging expert analyzing a Nexus system health issue. "
                    "Return JSON only: {\"root_cause\": \"...\", \"files\": [\"path/to/file.py\"], "
                    "\"severity\": 1-10, \"fix_approach\": \"...\"}"
                ),
                max_tokens=1000,
                temperature=0.2,
            )
            if result.get("ok"):
                content = result["content"]
                match = re.search(r'\{[^}]+\}', content, re.DOTALL)
                if match:
                    diag = json.loads(match.group())
                    severity = min(max(int(diag.get("severity", 5)), 1), 10)
                    files = diag.get("files", [])
                    protected = any(p in f for f in files for p in PROTECTED_PATHS)
                    return {
                        "source": "ai_diagnosis",
                        "root_cause": diag.get("root_cause", "Unknown"),
                        "fix_approach": diag.get("fix_approach", ""),
                        "fix_code": "",
                        "files": files,
                        "severity": severity,
                        "auto_fixable": severity <= MAX_AUTO_FIX_SEVERITY and not protected,
                        "model": result.get("model", ""),
                        "provider": result.get("provider", ""),
                    }
        except Exception as e:
            log.error("AI diagnosis failed: %s", e)

        # Fallback: unknown issue, escalate
        return {
            "source": "fallback",
            "root_cause": "Could not determine root cause",
            "fix_approach": "",
            "fix_code": "",
            "files": [],
            "severity": 7,
            "auto_fixable": False,
        }

    def _build_diagnosis_prompt(self, event):
        # type: (Dict) -> str
        parts = [
            "A health check detected an issue in the Nexus system.",
            "Check type: %s" % event["check_type"],
            "Target: %s" % event["target"],
            "Status: %s" % event["status"],
            "Message: %s" % event.get("message", ""),
            "",
            "The Nexus project is a FastAPI monolith (server.py) with modules in core/, "
            "integrations/, agents/, telegram/. Database is SQLite at ~/.nexus/memory.db.",
            "",
            "Analyze the issue and return JSON with root_cause, files (paths relative to "
            "project root), severity (1-10), and fix_approach.",
        ]
        return "\n".join(parts)

    # ── Repair Engine ─────────────────────────────────────────────────

    async def repair(self, event, diagnosis):
        # type: (Dict, Dict) -> Dict
        """Auto-repair disabled. All repairs require manual /repair command."""
        log.warning("Auto-repair disabled for %s/%s — use /repair command",
                     event.get("check_type", "?"), event.get("target", "?"))
        return {"ok": False, "method": "disabled",
                "detail": "Auto-repair disabled. Use /repair command."}

    async def _repair_restart_daemon(self, event, diagnosis):
        # type: (Dict, Dict) -> Dict
        """Restart a launchd daemon."""
        daemon = diagnosis["restart_daemon"]
        try:
            uid = os.getuid()
            proc = await asyncio.create_subprocess_exec(
                "launchctl", "kickstart", "-k", "gui/%d/%s" % (uid, daemon),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode == 0:
                log.info("Restarted daemon %s", daemon)
                return {"ok": True, "method": "restart_daemon", "detail": daemon}
            # Fallback: try bootout + bootstrap
            plist = Path.home() / "Library" / "LaunchAgents" / ("%s.plist" % daemon)
            if plist.exists():
                await asyncio.create_subprocess_exec(
                    "launchctl", "bootout", "gui/%d/%s" % (uid, daemon),
                )
                await asyncio.sleep(1)
                proc2 = await asyncio.create_subprocess_exec(
                    "launchctl", "bootstrap", "gui/%d" % uid, str(plist),
                )
                await proc2.wait()
                return {"ok": True, "method": "restart_daemon", "detail": "bootstrap %s" % daemon}
            return {"ok": False, "method": "restart_daemon", "detail": "plist not found"}
        except Exception as e:
            return {"ok": False, "method": "restart_daemon", "detail": str(e)[:200]}

    async def _repair_direct(self, event, diagnosis):
        # type: (Dict, Dict) -> Dict
        """Apply a direct fix via Python code from the brain.
        Note: fix_code contains trusted, system-generated Python snippets
        stored in the brain's error_log table (e.g., 'from core.db_migrate import
        run_migrations; run_migrations()'). These are NOT user-supplied."""
        fix_code = diagnosis["fix_code"]
        try:
            compiled = compile(fix_code, "<brain_fix>", "exec")
            exec_scope = {}  # type: Dict[str, Any]
            exec(compiled, exec_scope)  # nosec — trusted brain-stored code only
            log.info("Direct fix applied for %s: %s", event["check_type"], event["target"])
            return {"ok": True, "method": "direct_fix", "detail": fix_code[:200]}
        except Exception as e:
            log.error("Direct fix failed: %s", e)
            return {"ok": False, "method": "direct_fix", "detail": str(e)[:200]}

    async def _repair_with_agent(self, event, diagnosis):
        # type: (Dict, Dict) -> Dict
        """Use CodingAgent (Aider) to generate and apply a code fix."""
        try:
            from core.coding_agent import CodingAgent
            agent = CodingAgent()

            task_desc = (
                "Fix this issue in the Nexus codebase:\n\n"
                "Error: %s\n"
                "Root cause: %s\n"
                "Files to modify: %s\n"
                "Fix approach: %s\n\n"
                "Apply the minimum change needed. Do not refactor unrelated code."
            ) % (
                event.get("message", "")[:500],
                diagnosis.get("root_cause", ""),
                ", ".join(diagnosis.get("files", [])),
                diagnosis.get("fix_approach", ""),
            )

            result = await agent.execute_task(
                description=task_desc,
                task_id="autofix_%d" % event.get("id", 0),
                target_files=diagnosis.get("files"),
            )
            return {
                "ok": result.get("ok", False),
                "method": "coding_agent",
                "detail": result.get("output", "")[:500],
                "branch": result.get("branch", ""),
                "files_changed": result.get("files_changed", []),
                "model": result.get("model", ""),
                "provider": result.get("provider", ""),
            }
        except Exception as e:
            log.error("CodingAgent repair failed: %s", e)
            return {"ok": False, "method": "coding_agent", "detail": str(e)[:200]}

    # ── Verify ────────────────────────────────────────────────────────

    async def verify(self, event):
        # type: (Dict) -> bool
        """Re-run the specific scan that detected the issue to confirm fix."""
        check_type = event["check_type"]
        target = event["target"]

        scan_map = {
            "endpoint": self.scan_endpoints,
            "log_error": self.scan_logs,
            "db_schema": self.scan_schema,
            "import": self.scan_imports,
            "frontend": self.scan_frontend,
            "process": self.scan_processes,
        }

        scan_fn = scan_map.get(check_type)
        if not scan_fn:
            return False

        try:
            results = await scan_fn()
            for r in results:
                if r["target"] == target:
                    return r["status"] == "ok"
            return True
        except Exception as e:
            log.error("Verification failed: %s", e)
            return False

    # ── Full Heal Pipeline ────────────────────────────────────────────

    async def heal(self, event):
        # type: (Dict) -> Dict
        """Full pipeline: diagnose -> repair -> verify -> learn."""
        event_id = event.get("id", 0)
        log.info("Starting heal for event #%d: %s %s",
                 event_id, event["check_type"], event["target"])

        # 1. Diagnose
        diagnosis = await self.diagnose(event)
        log.info("Diagnosis: severity=%d, source=%s, auto_fixable=%s",
                 diagnosis["severity"], diagnosis["source"],
                 diagnosis.get("auto_fixable", False))

        # 2. Check severity — escalate if too high or not auto-fixable
        if diagnosis["severity"] >= 9 or not diagnosis.get("auto_fixable"):
            await self._escalate(event, diagnosis)
            _save_repair({
                "health_event_id": event_id,
                "diagnosis": diagnosis.get("root_cause", ""),
                "fix_approach": diagnosis.get("fix_approach", "escalated"),
                "status": "escalated",
            })
            return {"ok": False, "action": "escalated", "diagnosis": diagnosis}

        # 3. Repair
        repair_result = await self.repair(event, diagnosis)

        if not repair_result.get("ok"):
            log.warning("Repair failed for event #%d: %s", event_id, repair_result.get("detail"))
            _save_repair({
                "health_event_id": event_id,
                "diagnosis": diagnosis.get("root_cause", ""),
                "fix_approach": diagnosis.get("fix_approach", ""),
                "files_changed": repair_result.get("files_changed", []),
                "diff": repair_result.get("detail", ""),
                "status": "failed",
                "model_used": repair_result.get("model", ""),
                "duration_ms": repair_result.get("duration_ms", 0),
            })
            if diagnosis["severity"] >= 5:
                await self._escalate(event, diagnosis, repair_result)
            return {"ok": False, "action": "repair_failed", "diagnosis": diagnosis,
                    "repair": repair_result}

        # 4. Verify
        await asyncio.sleep(2)
        verified = await self.verify(event)

        if verified:
            log.info("Event #%d verified fixed via %s", event_id, repair_result["method"])
            _resolve_event(event_id, "auto:%s" % repair_result["method"])
            _save_repair({
                "health_event_id": event_id,
                "diagnosis": diagnosis.get("root_cause", ""),
                "fix_approach": diagnosis.get("fix_approach", ""),
                "files_changed": repair_result.get("files_changed", []),
                "diff": repair_result.get("detail", ""),
                "status": "success",
                "model_used": repair_result.get("model", ""),
                "tokens_used": repair_result.get("tokens_used", 0),
                "duration_ms": repair_result.get("duration_ms", 0),
            })

            # 5. Learn
            await self._learn(event, diagnosis, repair_result)

            if repair_result.get("model"):
                _save_model_perf(
                    model=repair_result["model"],
                    provider=repair_result.get("provider", ""),
                    task_type="code_repair",
                    success=True,
                    tokens=repair_result.get("tokens_used", 0),
                    latency=repair_result.get("duration_ms", 0),
                )

            try:
                from core.agent_activity import log_activity
                log_activity(
                    agent_id="health_scanner", dept_id="d4",
                    action="auto_repair",
                    detail="Fixed: %s via %s" % (event["target"][:50], repair_result["method"]),
                    target=event["target"], status="ok",
                )
            except Exception:
                pass

            return {"ok": True, "action": "repaired", "method": repair_result["method"],
                    "diagnosis": diagnosis}
        else:
            log.warning("Verification failed for event #%d after repair", event_id)
            _save_repair({
                "health_event_id": event_id,
                "diagnosis": diagnosis.get("root_cause", ""),
                "fix_approach": diagnosis.get("fix_approach", ""),
                "status": "failed",
                "model_used": repair_result.get("model", ""),
            })
            return {"ok": False, "action": "verify_failed", "diagnosis": diagnosis}

    async def _learn(self, event, diagnosis, repair_result):
        # type: (Dict, Dict, Dict) -> None
        """Store successful fix pattern in brain for future use."""
        try:
            from core.shared_brain import SharedBrain
            brain = SharedBrain()

            brain.log_error(
                error_type=event["check_type"],
                error_message=event.get("message", ""),
                context="auto-healed via %s" % repair_result["method"],
            )

            conn = _conn()
            try:
                row = conn.execute(
                    "SELECT id FROM brain_error_log WHERE error_type = ? AND error_message = ?",
                    (event["check_type"], event.get("message", "")),
                ).fetchone()
                if row:
                    brain.record_solution(
                        error_id=row["id"],
                        solution=diagnosis.get("root_cause", ""),
                        fix_code=diagnosis.get("fix_code", ""),
                        verified=True,
                    )
            finally:
                conn.close()

            log.info("Brain updated with fix pattern for %s", event["check_type"])
        except Exception as e:
            log.error("Failed to update brain: %s", e)

    async def _escalate(self, event, diagnosis, repair_result=None):
        # type: (Dict, Dict, Optional[Dict]) -> None
        """Send Telegram alert for issues that can't be auto-fixed."""
        try:
            from scripts.notify_telegram import send_telegram
            attempted = ""
            if repair_result:
                status = "attempted and failed" if not repair_result.get("ok") else "succeeded"
                attempted = "\nAuto-fix: %s (%s)" % (status, repair_result.get("method", ""))

            msg = (
                "NEXUS SELF-HEAL\n"
                "Type: %s\n"
                "Target: %s\n"
                "Severity: %d/10\n"
                "Cause: %s%s"
            ) % (
                event["check_type"], event["target"][:60],
                diagnosis.get("severity", 0),
                diagnosis.get("root_cause", "Unknown")[:100],
                attempted,
            )
            await send_telegram(msg)
        except Exception as e:
            log.error("Failed to send Telegram escalation: %s", e)


# ── Singleton ─────────────────────────────────────────────────────────

_scanner = None  # type: Optional[HealthScanner]


def get_health_scanner():
    # type: () -> HealthScanner
    global _scanner
    if _scanner is None:
        _scanner = HealthScanner()
    return _scanner


# ── Query helpers for API endpoints ───────────────────────────────────

def get_recent_events(limit=50):
    # type: (int) -> List[Dict]
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM health_events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_recent_repairs(limit=20):
    # type: (int) -> List[Dict]
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM repair_history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_model_performance():
    # type: () -> List[Dict]
    conn = _conn()
    try:
        rows = conn.execute("""
            SELECT model, provider, task_type,
                   COUNT(*) as total,
                   SUM(success) as successes,
                   ROUND(100.0 * SUM(success) / COUNT(*), 1) as success_rate,
                   ROUND(AVG(latency_ms)) as avg_latency_ms,
                   SUM(tokens_used) as total_tokens
            FROM model_performance
            WHERE created_at > datetime('now', '-7 days')
            GROUP BY model, provider, task_type
            HAVING total >= 2
            ORDER BY success_rate DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_brain_stats():
    # type: () -> Dict
    conn = _conn()
    try:
        known_fixes = conn.execute(
            "SELECT COUNT(*) as c FROM brain_error_log WHERE auto_fixable = 1"
        ).fetchone()["c"]
        total_errors = conn.execute(
            "SELECT COUNT(*) as c FROM brain_error_log"
        ).fetchone()["c"]
        code_modules = conn.execute(
            "SELECT COUNT(*) as c FROM brain_code_knowledge"
        ).fetchone()["c"]
        repairs_success = conn.execute(
            "SELECT COUNT(*) as c FROM repair_history WHERE status = 'success'"
        ).fetchone()["c"]
        repairs_total = conn.execute(
            "SELECT COUNT(*) as c FROM repair_history"
        ).fetchone()["c"]
        return {
            "known_fixes": known_fixes,
            "total_errors_logged": total_errors,
            "code_modules_indexed": code_modules,
            "repairs_successful": repairs_success,
            "repairs_total": repairs_total,
        }
    finally:
        conn.close()
