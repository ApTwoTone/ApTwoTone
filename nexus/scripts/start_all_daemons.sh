#!/bin/bash
# start_all_daemons.sh — Ensures all Nexus daemons are running.
#
# Checks each daemon by process pattern. If not running, starts it.
# Meant as a fallback — process_manager handles day-to-day supervision.
#
# Usage: bash scripts/start_all_daemons.sh

set -e

NEXUS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PYTHON="$NEXUS_DIR/venv/bin/python3"
LOG_DIR="$HOME/.nexus/logs"
PID_DIR="$HOME/.nexus/pids"

mkdir -p "$LOG_DIR" "$PID_DIR"

started=0
already=0

find_service_pid() {
    local name="$1"
    shift
    local pid_file="$PID_DIR/${name}.pid"
    python3 - "$pid_file" "$@" <<'PY'
import subprocess
import sys
from pathlib import Path
import os

pid_file = Path(sys.argv[1])
fragments = [arg for arg in sys.argv[2:] if arg]
if fragments:
    first = os.path.basename(fragments[0]).lower()
    if first.startswith("python"):
        fragments = fragments[1:]

def command_for_pid(pid: int) -> str:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()

def matches(command: str) -> bool:
    return bool(command) and all(fragment in command for fragment in fragments)

if pid_file.exists():
    raw = pid_file.read_text(encoding="utf-8").strip()
    if raw.isdigit():
        command = command_for_pid(int(raw))
        if matches(command):
            print(raw)
            raise SystemExit(0)
    pid_file.unlink(missing_ok=True)

try:
    result = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        capture_output=True,
        text=True,
        timeout=5,
    )
except Exception:
    raise SystemExit(1)

if result.returncode != 0:
    raise SystemExit(1)

for line in result.stdout.splitlines():
    parts = line.strip().split(None, 1)
    if len(parts) != 2 or not parts[0].isdigit():
        continue
    pid = parts[0]
    command = parts[1]
    if matches(command):
        pid_file.write_text(pid, encoding="utf-8")
        print(pid)
        raise SystemExit(0)

raise SystemExit(1)
PY
}

voice_port() {
    python3 - "$NEXUS_DIR" <<'PY'
import sys
from pathlib import Path
root = Path(sys.argv[1])
if str(root) not in sys.path:
    sys.path.insert(0, str(root))
from core import voice_control
print(voice_control.load_voice_port())
PY
}

find_voice_listener_pid() {
    local port
    port="$(voice_port)"
    python3 - "$port" <<'PY'
import subprocess
import sys

port = str(sys.argv[1]).strip()
if not port.isdigit():
    raise SystemExit(1)

try:
    result = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        text=True,
        timeout=5,
    )
except Exception:
    raise SystemExit(1)

if result.returncode != 0:
    raise SystemExit(1)

for raw_pid in result.stdout.splitlines():
    pid = raw_pid.strip()
    if not pid.isdigit():
        continue
    try:
        command = subprocess.run(
            ["ps", "-p", pid, "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except Exception:
        command = ""
    if "voice_control_server.py" in command:
        print(pid)
        raise SystemExit(0)

raise SystemExit(1)
PY
}

start_if_missing() {
    local name="$1"
    local pattern="$2"
    shift 2
    local cmd=("$@")

    local pid
    if pid="$(find_service_pid "$name" "${cmd[@]}")"; then
        echo "  [OK] $name already running (PID $pid)"
        already=$((already + 1))
    else
        echo "  [>>] Starting $name..."
        nohup "${cmd[@]}" >> "$LOG_DIR/${name}.log" 2>&1 &
        echo $! > "$PID_DIR/${name}.pid"
        started=$((started + 1))
        sleep 1
    fi
}

ensure_voice_control_health() {
    local port
    port="$(voice_port)"
    local health_url="http://127.0.0.1:${port}/voice/healthz"
    local stream_health_url="http://127.0.0.1:${port}/voice/stream/healthz"
    if curl -fsS "$health_url" > /dev/null 2>&1 && curl -fsS "$stream_health_url" > /dev/null 2>&1; then
        echo "  [OK] voice_control_server health checks passed on $health_url and $stream_health_url"
        return
    fi

    echo "  [!!] voice_control_server health check failed on $health_url or $stream_health_url"
    local pid
    if pid="$(find_service_pid "voice_control_server" "$VENV_PYTHON" "$NEXUS_DIR/scripts/voice_control_server.py")"; then
        kill "$pid" 2>/dev/null || true
        rm -f "$PID_DIR/voice_control_server.pid"
        sleep 1
    elif pid="$(find_voice_listener_pid)"; then
        kill "$pid" 2>/dev/null || true
        rm -f "$PID_DIR/voice_control_server.pid"
        sleep 1
    fi
    echo "  [>>] Restarting voice_control_server..."
    nohup "$VENV_PYTHON" "$NEXUS_DIR/scripts/voice_control_server.py" >> "$LOG_DIR/voice_control_server.log" 2>&1 &
    echo $! > "$PID_DIR/voice_control_server.pid"
    sleep 2
    if curl -fsS "$health_url" > /dev/null 2>&1 && curl -fsS "$stream_health_url" > /dev/null 2>&1; then
        echo "  [OK] voice_control_server recovered"
    else
        echo "  [!!] voice_control_server still failing health check"
    fi
}

config_flag_true() {
    local key="$1"
    python3 - "$key" <<'PY'
import json, sys
from pathlib import Path
key = sys.argv[1]
cfg_path = Path.home() / ".nexus" / "config.json"
try:
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
except Exception:
    cfg = {}
v = cfg.get(key, False)
print("1" if bool(v) else "0")
PY
}

echo "=================================="
echo "  Nexus Daemon Startup"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "=================================="

# Core server
start_if_missing "server" "nexus/server.py" \
    "$VENV_PYTHON" "$NEXUS_DIR/server.py"

# Process manager (supervises workers + external services)
start_if_missing "process_manager" "process_manager.py" \
    "$VENV_PYTHON" "$NEXUS_DIR/scripts/process_manager.py"

# Outbound monitor
start_if_missing "outbound_monitor" "outbound_monitor.py --daemon" \
    "$VENV_PYTHON" "$NEXUS_DIR/scripts/outbound_monitor.py" --daemon

# Bounce monitor
start_if_missing "bounce_monitor" "bounce_monitor.py --daemon" \
    "$VENV_PYTHON" "$NEXUS_DIR/scripts/bounce_monitor.py" --daemon

# Reply monitor
start_if_missing "reply_monitor" "reply_monitor.py --daemon" \
    "$VENV_PYTHON" "$NEXUS_DIR/scripts/reply_monitor.py" --daemon

# Lead discovery daemon
start_if_missing "discovery_daemon" "lead_discovery_daemon.py" \
    "$VENV_PYTHON" "$NEXUS_DIR/scripts/lead_discovery_daemon.py"

# Brain task scheduler (keeps brain queue warm 24/7)
start_if_missing "brain_task_scheduler" "brain_task_scheduler.py --daemon" \
    "$VENV_PYTHON" "$NEXUS_DIR/scripts/brain_task_scheduler.py" --daemon

# Brain agent runner (consumes brain queue tasks)
start_if_missing "brain_agent_runner" "brain_agent_runner.py" \
    "$VENV_PYTHON" "$NEXUS_DIR/scripts/brain_agent_runner.py"

# Autonomous task worker (live mode; safe files/complexity guards in script)
DISABLE_AUTONOMY="$(config_flag_true disable_autonomy_system)"
DISABLE_TASK_WORKER="$(config_flag_true disable_task_worker_live)"
if [ "$DISABLE_AUTONOMY" = "1" ] || [ "$DISABLE_TASK_WORKER" = "1" ]; then
    echo "  [SKIP] task_worker disabled by config"
else
    start_if_missing "task_worker" "task_worker.py --loop --live" \
        "$VENV_PYTHON" "$NEXUS_DIR/scripts/task_worker.py" --loop --live
fi

# Facebook group scraper (can be disabled in config)
DISABLE_FB="$(config_flag_true disable_facebook_scraper)"
DISABLE_BROWSER="$(config_flag_true disable_browser_scrapers)"
if [ "$DISABLE_FB" = "1" ] || [ "$DISABLE_BROWSER" = "1" ]; then
    echo "  [SKIP] facebook_scraper disabled by config"
else
    start_if_missing "facebook_scraper" "facebook_group_scraper.py" \
        "$VENV_PYTHON" "$NEXUS_DIR/scripts/facebook_group_scraper.py" --interval 30
fi

# Gmail ingester
start_if_missing "gmail_ingester" "gmail_ingester.py --daemon" \
    "$VENV_PYTHON" "$NEXUS_DIR/core/gmail_ingester.py" --daemon

# SMS control daemon (Google Voice -> task board bridge)
DISABLE_SMS_CONTROL="$(config_flag_true disable_sms_control_daemon)"
if [ "$DISABLE_SMS_CONTROL" = "1" ]; then
    echo "  [SKIP] sms_control_daemon disabled by config"
else
    start_if_missing "sms_control_daemon" "sms_control_daemon.py" \
        "$VENV_PYTHON" "$NEXUS_DIR/scripts/sms_control_daemon.py"
fi

# Voice control server (Twilio/webhook voice control)
DISABLE_VOICE_CONTROL="$(config_flag_true disable_voice_control_server)"
if [ "$DISABLE_VOICE_CONTROL" = "1" ]; then
    echo "  [SKIP] voice_control_server disabled by config"
else
    start_if_missing "voice_control_server" "$NEXUS_DIR/scripts/voice_control_server.py" \
        "$VENV_PYTHON" "$NEXUS_DIR/scripts/voice_control_server.py"
    ensure_voice_control_health
fi

# Feedback collector daemon
start_if_missing "feedback_collector" "feedback_collector_daemon.py" \
    "$VENV_PYTHON" "$NEXUS_DIR/scripts/feedback_collector_daemon.py" --poll-seconds 60

echo ""
echo "=================================="
echo "  Started: $started | Already running: $already"
echo "=================================="

# Show agent registry status
if [ -f "$HOME/.nexus/memory.db" ]; then
    echo ""
    echo "Agent Registry:"
    sqlite3 "$HOME/.nexus/memory.db" \
        "SELECT name, status, current_task, last_heartbeat FROM agent_registry ORDER BY name;" \
        2>/dev/null || echo "  (no agent_registry data yet)"
fi
