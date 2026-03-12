#!/usr/bin/env python3
"""
ProcessManager — Manages OS-level worker processes independent of VS Code.

Runs as a launchd daemon (com.zoar.process-manager). Spawns worker_runner
processes, monitors PIDs every 30s, auto-restarts dead processes.

Workers survive: VS Code close, Claude Code prompts, screen lock.
Only stops via: Emergency Stop from Nexus UI or Telegram /estop command.

Usage: python3 scripts/process_manager.py [--workers N] [--tier TIER]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from core.service_control import is_process_disabled

# Ensure nexus root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"
PID_DIR = Path.home() / ".nexus" / "pids"
LOG_DIR = Path.home() / ".nexus" / "process_logs"
VENV_PYTHON = Path(__file__).parent.parent / "venv" / "bin" / "python3"
WORKER_SCRIPT = Path(__file__).parent / "worker_runner.py"
ROOT_DIR = Path(__file__).parent.parent

CHECK_INTERVAL = 30  # seconds
MAX_RESTARTS_PER_HOUR = 10

PID_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# External daemons supervised by ProcessManager (additive to worker fleet).
EXTERNAL_SERVICES: List[Dict[str, object]] = [
    {
        "name": "server",
        "pattern": "/Users/kai/nexus/server.py",
        "command": [str(VENV_PYTHON), "server.py"],
        "cwd": str(ROOT_DIR),
        "auto_restart": True,
        "max_restarts": 3,
    },
    {
        "name": "outbound_monitor",
        "pattern": "scripts/outbound_monitor.py --daemon",
        "command": [str(VENV_PYTHON), "scripts/outbound_monitor.py", "--daemon"],
        "cwd": str(ROOT_DIR),
        "auto_restart": True,
        "max_restarts": 3,
    },
    {
        "name": "bounce_monitor",
        "pattern": "scripts/bounce_monitor.py --daemon",
        "command": [str(VENV_PYTHON), "scripts/bounce_monitor.py", "--daemon"],
        "cwd": str(ROOT_DIR),
        "auto_restart": True,
        "max_restarts": 3,
    },
    {
        "name": "reply_monitor",
        "pattern": "scripts/reply_monitor.py --daemon",
        "command": [str(VENV_PYTHON), "scripts/reply_monitor.py", "--daemon"],
        "cwd": str(ROOT_DIR),
        "auto_restart": True,
        "max_restarts": 3,
    },
    {
        "name": "facebook_scraper",
        "pattern": "facebook_group_scraper.py",
        "command": [str(VENV_PYTHON), "scripts/facebook_group_scraper.py", "--interval", "30"],
        "cwd": str(ROOT_DIR),
        "auto_restart": True,
        "max_restarts": 3,
    },
    {
        "name": "feedback_collector",
        "pattern": "feedback_collector_daemon.py",
        "command": [str(VENV_PYTHON), "scripts/feedback_collector_daemon.py", "--poll-seconds", "60"],
        "cwd": str(ROOT_DIR),
        "auto_restart": True,
        "max_restarts": 3,
    },
    {
        "name": "gmail_ingester",
        "pattern": "gmail_ingester.py --daemon",
        "command": [str(VENV_PYTHON), "core/gmail_ingester.py", "--daemon"],
        "cwd": str(ROOT_DIR),
        "auto_restart": True,
        "max_restarts": 3,
    },
    {
        "name": "sms_control_daemon",
        "pattern": "sms_control_daemon.py",
        "command": [str(VENV_PYTHON), "scripts/sms_control_daemon.py"],
        "cwd": str(ROOT_DIR),
        "auto_restart": True,
        "max_restarts": 3,
    },
    {
        "name": "voice_control_server",
        "pattern": "/Users/kai/nexus/scripts/voice_control_server.py",
        "command": [str(VENV_PYTHON), "scripts/voice_control_server.py"],
        "cwd": str(ROOT_DIR),
        "auto_restart": True,
        "max_restarts": 3,
    },
    {
        "name": "discovery_daemon",
        "pattern": "lead_discovery_daemon.py",
        "command": [str(VENV_PYTHON), "scripts/lead_discovery_daemon.py"],
        "cwd": str(ROOT_DIR),
        "auto_restart": True,
        "max_restarts": 3,
    },
    {
        "name": "task_worker",
        "pattern": "scripts/task_worker.py --loop --live",
        "command": [str(VENV_PYTHON), "scripts/task_worker.py", "--loop", "--live"],
        "cwd": str(ROOT_DIR),
        "auto_restart": True,
        "max_restarts": 3,
    },
    {
        "name": "brain_agent_runner",
        "pattern": "scripts/brain_agent_runner.py",
        "command": [str(VENV_PYTHON), "scripts/brain_agent_runner.py"],
        "cwd": str(ROOT_DIR),
        "auto_restart": True,
        "max_restarts": 3,
    },
    {
        "name": "brain_task_scheduler",
        "pattern": "scripts/brain_task_scheduler.py --daemon",
        "command": [str(VENV_PYTHON), "scripts/brain_task_scheduler.py", "--daemon"],
        "cwd": str(ROOT_DIR),
        "auto_restart": True,
        "max_restarts": 3,
    },
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ProcessManager] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(LOG_DIR / "manager.log"), mode="a"),
    ],
)
log = logging.getLogger("process_manager")


class ProcessManager:
    def __init__(self):
        self._processes = {}  # type: Dict[str, subprocess.Popen]
        self._restart_counts = {}  # type: Dict[str, List[float]]
        self._running = True
        PID_DIR.mkdir(parents=True, exist_ok=True)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self._register_external_services()

    def _load_runtime_config(self) -> Dict[str, object]:
        try:
            if CONFIG_PATH.exists():
                raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                return raw if isinstance(raw, dict) else {}
        except Exception:
            pass
        return {}

    def _service_disabled(self, service_name: str) -> bool:
        name = str(service_name or "").strip().lower()
        # Server lifecycle is owned by launchd (com.zoar.nexus-server).
        # Do not double-manage it here or we get restart loops / port flapping.
        if name == "server":
            return True
        cfg = self._load_runtime_config()
        if is_process_disabled(name, cfg):
            return True
        disable_all_autonomy = bool(cfg.get("disable_autonomy_system", False))
        if disable_all_autonomy and name in {"task_worker", "brain_agent_runner", "brain_task_scheduler"}:
            return True
        if name == "task_worker" and bool(cfg.get("disable_task_worker_live", False)):
            return True
        if name == "brain_task_scheduler" and bool(cfg.get("disable_brain_task_scheduler", False)):
            return True
        if name == "brain_agent_runner" and bool(cfg.get("disable_brain_agent_runner", False)):
            return True
        if name == "voice_control_server" and bool(cfg.get("disable_voice_control_server", False)):
            return True
        if name == "facebook_scraper":
            if bool(cfg.get("disable_facebook_scraper", False)) or bool(cfg.get("disable_browser_scrapers", False)):
                return True
            env_disable = str(os.environ.get("NEXUS_DISABLE_FACEBOOK_SCRAPER", "")).strip().lower()
            if env_disable in {"1", "true", "yes", "on"}:
                return True
        return False

    def _service_by_name(self, name: str) -> Optional[Dict[str, object]]:
        needle = str(name or "").strip()
        if not needle:
            return None
        for svc in EXTERNAL_SERVICES:
            if str(svc.get("name")) == needle:
                return svc
        return None

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def start_worker(self, name: str, tier: int = 3,
                     config: Optional[Dict] = None) -> bool:
        """Spawn a new worker as an OS-level process."""
        if name in self._processes and self._is_alive(self._processes[name]):
            log.warning("Worker %s already running (PID %d)", name, self._processes[name].pid)
            return False

        # Prevent duplicate workers across manager restarts.
        existing_pid = self._find_pid_by_pattern("worker_runner.py --name %s" % name)
        if existing_pid > 0 and self._is_pid_alive(existing_pid):
            self._update_db(name, "running", existing_pid, tier=tier, process_type="worker")
            log.info("Worker %s already alive in OS (PID %d), skip spawn", name, existing_pid)
            return False

        log_path = LOG_DIR / ("%s.log" % name)
        cmd = [
            str(VENV_PYTHON),
            str(WORKER_SCRIPT),
            "--name", name,
            "--tier", str(tier),
        ]

        try:
            log_file = open(str(log_path), "a")
            proc = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=str(Path(__file__).parent.parent),
                env=self._build_env(),
                start_new_session=True,  # Detach from parent terminal
            )
            self._processes[name] = proc

            # Write PID file
            (PID_DIR / ("%s.pid" % name)).write_text(str(proc.pid))

            # Update DB
            self._update_db(name, "running", proc.pid, tier=tier)

            log.info("Started worker %s (PID %d, tier %d)", name, proc.pid, tier)
            return True
        except Exception as e:
            log.error("Failed to start worker %s: %s", name, e)
            self._update_db(name, "error", error=str(e))
            return False

    def stop_worker(self, name: str) -> bool:
        """Gracefully stop a worker (SIGTERM, wait 10s, SIGKILL)."""
        proc = self._processes.get(name)
        if not proc:
            # Try to find PID from file
            pid_file = PID_DIR / ("%s.pid" % name)
            if pid_file.exists():
                try:
                    pid = int(pid_file.read_text().strip())
                    os.kill(pid, signal.SIGTERM)
                    pid_file.unlink(missing_ok=True)
                    self._update_db(name, "stopped")
                    log.info("Stopped worker %s (PID %d) via PID file", name, pid)
                    return True
                except (ProcessLookupError, ValueError):
                    pass
            return False

        try:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        except Exception as e:
            log.warning("Error stopping worker %s: %s", name, e)

        self._processes.pop(name, None)
        (PID_DIR / ("%s.pid" % name)).unlink(missing_ok=True)
        self._update_db(name, "stopped")
        log.info("Stopped worker %s", name)
        return True

    def start_process(self, name: str, tier: int = 3, config: Optional[Dict] = None) -> bool:
        svc = self._service_by_name(name)
        if not svc:
            return self.start_worker(name, tier=tier, config=config)

        if self._service_disabled(name):
            self._update_db(
                name,
                "paused",
                0,
                process_type="service",
                max_restarts=int(svc.get("max_restarts", 3) or 3),
                config_json=json.dumps(svc),
            )
            log.info("Service %s is disabled by config; refusing start", name)
            return False

        pid = self._find_service_pid(svc)
        if pid > 0 and self._service_healthcheck_ok(svc):
            self._write_pid_file(name, pid)
            self._update_db(
                name,
                "running",
                pid,
                process_type="service",
                max_restarts=int(svc.get("max_restarts", 3) or 3),
                config_json=json.dumps(svc),
            )
            log.info("Service %s already running (PID %d)", name, pid)
            return True

        return self._start_external_service(svc)

    def stop_process(self, name: str) -> bool:
        svc = self._service_by_name(name)
        if not svc:
            return self.stop_worker(name)

        pid = self._find_service_pid(svc)
        if pid <= 0:
            self._clear_pid_file(name)
            self._update_db(
                name,
                "paused" if self._service_disabled(name) else "stopped",
                0,
                process_type="service",
                max_restarts=int(svc.get("max_restarts", 3) or 3),
                config_json=json.dumps(svc),
            )
            return False

        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(1)
            if self._is_pid_alive(pid):
                os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception as e:
            log.warning("Error stopping service %s (PID %d): %s", name, pid, e)

        self._processes.pop(name, None)
        self._clear_pid_file(name)
        self._update_db(
            name,
            "paused" if self._service_disabled(name) else "stopped",
            0,
            process_type="service",
            max_restarts=int(svc.get("max_restarts", 3) or 3),
            config_json=json.dumps(svc),
        )
        log.info("Stopped service %s (PID %d)", name, pid)
        return True

    def stop_all(self) -> int:
        """Emergency stop all workers."""
        names = list(self._processes.keys())
        count = 0
        for name in names:
            if self.stop_worker(name):
                count += 1
        log.warning("EMERGENCY STOP: killed %d workers", count)
        return count

    def check_all(self):
        """Check if all registered workers are alive. Restart dead ones."""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT name, pid, tier, status FROM managed_processes "
                "WHERE status = 'running' AND process_type = 'worker' AND name != 'process_manager'"
            ).fetchall()
        finally:
            conn.close()

        for row in rows:
            name = row["name"]
            pid = row["pid"]
            tier = row["tier"]

            proc = self._processes.get(name)
            alive = False

            if proc:
                alive = self._is_alive(proc)
            elif pid > 0:
                alive = self._is_pid_alive(pid)

            if not alive:
                log.warning("Worker %s (PID %d) is dead. Restarting...", name, pid)
                self._processes.pop(name, None)

                # Check restart rate
                if self._too_many_restarts(name):
                    log.error("Worker %s restarted too many times. Pausing.", name)
                    self._update_db(name, "error", error="Too many restarts")
                    continue

                self.start_worker(name, tier)

        # Keep external daemons supervised in the same loop.
        self._check_external_services()

    def get_status(self) -> List[Dict]:
        """Return status of all managed processes for API."""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM managed_processes ORDER BY name"
            ).fetchall()
            result = []
            for row in rows:
                d = dict(row)
                # Check actual liveness
                proc = self._processes.get(d["name"])
                if proc:
                    d["actually_alive"] = self._is_alive(proc)
                elif d["pid"] > 0:
                    d["actually_alive"] = self._is_pid_alive(d["pid"])
                else:
                    d["actually_alive"] = False
                result.append(d)
            return result
        finally:
            conn.close()

    def spawn_initial_workers(self, count: int = 5, tier: int = 2):
        """Spawn the initial set of workers.

        If count/tier are defaults, spawns multi-tier fleet:
          3 vendor research (T3), 2 D3 experiments (T6), 1 background (T7).
        """
        if count == 5 and tier == 2:
            # Multi-tier fleet
            fleet = [
                ("vendor-1-t3", 3), ("vendor-2-t3", 3), ("vendor-3-t3", 3),
                ("d3-1-t6", 6), ("d3-2-t6", 6),
                ("bg-1-t7", 7),
            ]
            for name, t in fleet:
                self.start_worker(name, t)
            return

        for i in range(count):
            name = "worker-%d-t%d" % (i + 1, tier)
            self.start_worker(name, tier)

    def run_forever(self):
        """Main loop: check every 30 seconds, restart dead workers."""
        log.info("ProcessManager starting (PID %d)...", os.getpid())

        # Write own PID
        (PID_DIR / "process_manager.pid").write_text(str(os.getpid()))

        # Handle SIGTERM gracefully
        def _handle_term(signum, frame):
            log.info("Received SIGTERM, stopping all workers...")
            self._running = False
            self.stop_all()
            sys.exit(0)

        signal.signal(signal.SIGTERM, _handle_term)
        signal.signal(signal.SIGINT, _handle_term)

        while self._running:
            try:
                self.check_all()
                self._write_heartbeat()
            except Exception as e:
                log.error("ProcessManager check error: %s", e)
            time.sleep(CHECK_INTERVAL)

    # ── Internal helpers ─────────────────────────────────────────────────

    def _is_alive(self, proc: subprocess.Popen) -> bool:
        return proc.poll() is None

    def _is_pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def _too_many_restarts(self, name: str) -> bool:
        now = time.time()
        history = self._restart_counts.get(name, [])
        # Keep only restarts in the last hour
        history = [t for t in history if now - t < 3600]
        history.append(now)
        self._restart_counts[name] = history
        return len(history) > MAX_RESTARTS_PER_HOUR

    def _update_db(self, name: str, status: str, pid: int = 0,
                   tier: int = 0, error: str = "",
                   process_type: str = "", max_restarts: int = 0,
                   config_json: str = ""):
        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO managed_processes
                   (name, pid, status, tier, process_type, max_restarts, config, started_at, error_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)
                   ON CONFLICT(name) DO UPDATE SET
                     pid = CASE WHEN ? > 0 THEN ? ELSE pid END,
                     status = ?,
                     tier = CASE WHEN ? > 0 THEN ? ELSE tier END,
                     process_type = CASE WHEN ? != '' THEN ? ELSE process_type END,
                     max_restarts = CASE WHEN ? > 0 THEN ? ELSE max_restarts END,
                     config = CASE WHEN ? != '' THEN ? ELSE config END,
                     last_heartbeat = datetime('now'),
                     restart_count = CASE WHEN ? = 'running' AND status != 'running'
                                     THEN restart_count + 1 ELSE restart_count END,
                     error_message = CASE WHEN ? != '' THEN ? ELSE error_message END,
                     started_at = CASE WHEN ? = 'running' THEN datetime('now') ELSE started_at END""",
                (
                    name, pid, status, tier, process_type, max_restarts, config_json, error,
                    pid, pid, status, tier, tier,
                    process_type, process_type,
                    max_restarts, max_restarts,
                    config_json, config_json,
                    status, error, error, status,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _write_heartbeat(self):
        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO managed_processes
                   (name, pid, status, process_type, tier, last_heartbeat)
                   VALUES ('process_manager', ?, 'running', 'manager', 0, datetime('now'))
                   ON CONFLICT(name) DO UPDATE SET
                     pid = ?, status = 'running', process_type = 'manager',
                     tier = 0, last_heartbeat = datetime('now')""",
                (os.getpid(), os.getpid()),
            )
            conn.commit()
        finally:
            conn.close()

    def _build_env(self) -> dict:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).parent.parent)
        env["HOME"] = str(Path.home())
        return env

    def _pid_file_path(self, name: str) -> Path:
        return PID_DIR / ("%s.pid" % name)

    def _read_pid_file(self, name: str) -> int:
        pid_file = self._pid_file_path(name)
        if not pid_file.exists():
            return 0
        try:
            raw = pid_file.read_text().strip()
            return int(raw) if raw.isdigit() else 0
        except Exception:
            return 0

    def _write_pid_file(self, name: str, pid: int) -> None:
        self._pid_file_path(name).write_text(str(int(pid)))

    def _clear_pid_file(self, name: str) -> None:
        self._pid_file_path(name).unlink(missing_ok=True)

    def _service_command_fragments(self, svc: Dict[str, object]) -> List[str]:
        parts = [str(part) for part in list(svc.get("command", [])) if str(part)]
        if parts:
            first = os.path.basename(parts[0]).lower()
            if first.startswith("python"):
                parts = parts[1:]
        return parts

    def _register_external_services(self) -> None:
        """Ensure required external daemons are represented in managed_processes."""
        conn = self._conn()
        try:
            for svc in EXTERNAL_SERVICES:
                name = str(svc["name"])
                cfg_json = json.dumps(svc)
                initial_status = "paused" if self._service_disabled(name) else "stopped"
                conn.execute(
                    """INSERT INTO managed_processes
                       (name, process_type, status, tier, max_restarts, config, started_at)
                       VALUES (?, 'service', 'stopped', 0, ?, ?, '')
                       ON CONFLICT(name) DO UPDATE SET
                         process_type='service',
                         max_restarts=?,
                         config=?""",
                    (name, int(svc.get("max_restarts", 3)), cfg_json,
                     int(svc.get("max_restarts", 3)), cfg_json),
                )
                conn.execute(
                    "UPDATE managed_processes SET status=? WHERE name=? AND (status IS NULL OR status IN ('stopped','running'))",
                    (initial_status, name),
                )
            conn.commit()
        finally:
            conn.close()

    def _find_pid_by_pattern(self, pattern: str) -> int:
        try:
            result = subprocess.run(
                ["pgrep", "-f", pattern],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return 0
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.isdigit():
                    pid = int(line)
                    if pid != os.getpid():
                        return pid
            return 0
        except Exception:
            return 0

    def _pid_matches_service(self, pid: int, svc: Dict[str, object]) -> bool:
        if pid <= 0 or not self._is_pid_alive(pid):
            return False

        expected = self._service_command_fragments(svc)
        if not expected:
            return False
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return False
            command_line = result.stdout.strip()
            if not command_line:
                return False
            return all(fragment in command_line for fragment in expected)
        except Exception:
            return False

    def _command_for_pid(self, pid: int) -> str:
        if pid <= 0:
            return ""
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return ""
            return result.stdout.strip()
        except Exception:
            return ""

    def _find_listening_pid_by_port(self, port: int) -> int:
        if int(port or 0) <= 0:
            return 0
        try:
            result = subprocess.run(
                ["lsof", "-nP", f"-iTCP:{int(port)}", "-sTCP:LISTEN", "-t"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            return 0

        if result.returncode != 0:
            return 0

        for line in result.stdout.splitlines():
            line = line.strip()
            if line.isdigit():
                pid = int(line)
                if pid != os.getpid():
                    return pid
        return 0

    def _find_pid_by_command(self, expected: List[str]) -> int:
        try:
            result = subprocess.run(
                ["ps", "-axo", "pid=,command="],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            return 0

        if result.returncode != 0:
            return 0

        fragments = [str(part) for part in expected if str(part)]
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) != 2 or not parts[0].isdigit():
                continue
            pid = int(parts[0])
            if pid == os.getpid():
                continue
            command_line = parts[1]
            if all(fragment in command_line for fragment in fragments):
                return pid
        return 0

    def _find_service_pid(self, svc: Dict[str, object]) -> int:
        name = str(svc["name"])
        pid = self._read_pid_file(name)
        if self._pid_matches_service(pid, svc):
            return pid

        command = self._service_command_fragments(svc)
        pid = self._find_pid_by_command(command)
        if pid > 0 and self._pid_matches_service(pid, svc):
            self._write_pid_file(name, pid)
            return pid

        pattern = str(svc.get("pattern", ""))
        pid = self._find_pid_by_pattern(pattern) if pattern else 0
        if self._pid_matches_service(pid, svc):
            self._write_pid_file(name, pid)
            return pid

        if name == "voice_control_server":
            try:
                from core import voice_control

                port_pid = self._find_listening_pid_by_port(voice_control.load_voice_port())
                command_line = self._command_for_pid(port_pid)
                if port_pid > 0 and "voice_control_server.py" in command_line:
                    self._write_pid_file(name, port_pid)
                    return port_pid
            except Exception:
                pass
        return 0

    def _service_healthcheck_ok(self, svc: Dict[str, object]) -> bool:
        name = str(svc["name"])
        if name != "voice_control_server":
            return True
        try:
            from core import voice_control

            port = voice_control.load_voice_port()
            for path in ("/voice/healthz", "/voice/stream/healthz"):
                url = "http://127.0.0.1:%d%s" % (port, path)
                with urllib.request.urlopen(url, timeout=4) as response:
                    if response.status != 200:
                        return False
            return True
        except urllib.error.URLError as exc:
            log.warning("Voice health check failed: %s", exc)
            return False
        except Exception as exc:
            log.warning("Voice health check error: %s", exc)
            return False

    def _start_external_service(self, svc: Dict[str, object]) -> bool:
        name = str(svc["name"])
        cmd = list(svc.get("command", []))
        cwd = str(svc.get("cwd", str(ROOT_DIR)))
        if not cmd:
            return False
        log_path = LOG_DIR / ("%s.log" % name)
        try:
            log_file = open(str(log_path), "a")
            proc = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=cwd,
                env=self._build_env(),
                start_new_session=True,
            )
            self._processes[name] = proc
            self._write_pid_file(name, proc.pid)
            self._update_db(
                name, "running", proc.pid,
                process_type="service",
                max_restarts=int(svc.get("max_restarts", 3) or 3),
                config_json=json.dumps(svc),
            )
            log.info("Started service %s (PID %d)", name, proc.pid)
            return True
        except Exception as e:
            self._clear_pid_file(name)
            self._update_db(
                name, "error", error=str(e), process_type="service",
                max_restarts=int(svc.get("max_restarts", 3) or 3),
                config_json=json.dumps(svc),
            )
            log.error("Failed to start service %s: %s", name, e)
            return False

    def _check_external_services(self) -> None:
        for svc in EXTERNAL_SERVICES:
            name = str(svc["name"])
            disabled = self._service_disabled(name)
            pid = self._find_service_pid(svc)
            if disabled:
                if pid > 0:
                    try:
                        os.kill(pid, signal.SIGTERM)
                        log.info("Stopped disabled service %s (PID %d)", name, pid)
                    except Exception:
                        pass
                self._clear_pid_file(name)
                self._update_db(
                    name, "paused", 0,
                    process_type="service",
                    max_restarts=int(svc.get("max_restarts", 3) or 3),
                    config_json=json.dumps(svc),
                )
                continue
            if pid > 0 and self._service_healthcheck_ok(svc):
                self._update_db(
                    name, "running", pid,
                    process_type="service",
                    max_restarts=int(svc.get("max_restarts", 3) or 3),
                    config_json=json.dumps(svc),
                )
                continue
            if pid > 0:
                log.warning("Service %s has a live PID %d but failed health checks", name, pid)
                try:
                    os.kill(pid, signal.SIGTERM)
                except Exception:
                    pass
                self._clear_pid_file(name)

            self._update_db(
                name, "stopped", 0,
                process_type="service",
                max_restarts=int(svc.get("max_restarts", 3) or 3),
                config_json=json.dumps(svc),
            )

            if not bool(svc.get("auto_restart", False)):
                continue
            if self._too_many_restarts(name):
                self._update_db(
                    name, "error", error="Too many restarts",
                    process_type="service",
                    max_restarts=int(svc.get("max_restarts", 3) or 3),
                    config_json=json.dumps(svc),
                )
                continue

            log.warning("Service %s not running. Restarting...", name)
            self._start_external_service(svc)


def main():
    parser = argparse.ArgumentParser(description="Nexus ProcessManager")
    parser.add_argument("--workers", type=int, default=5, help="Initial worker count")
    parser.add_argument("--tier", type=int, default=2, help="Default worker tier")
    args = parser.parse_args()

    pm = ProcessManager()
    pm.spawn_initial_workers(args.workers, args.tier)
    pm.run_forever()


if __name__ == "__main__":
    main()
