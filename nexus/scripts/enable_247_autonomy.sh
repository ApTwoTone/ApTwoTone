#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="$ROOT_DIR/venv/bin/python3"
LAUNCH_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/.nexus/logs"
DOMAIN="gui/$(id -u)"

mkdir -p "$LAUNCH_DIR" "$LOG_DIR"

write_plist() {
  local label="$1"
  local path="$2"
  local body="$3"
  printf "%s\n" "$body" > "$path"
  chmod 644 "$path"
  echo "[autonomy] wrote $label"
}

TASK_WORKER_PLIST="$LAUNCH_DIR/com.zoar.task-worker.plist"
BRAIN_SCHED_PLIST="$LAUNCH_DIR/com.zoar.brain-task-scheduler.plist"

write_plist "com.zoar.task-worker" "$TASK_WORKER_PLIST" "<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
<plist version=\"1.0\">
<dict>
  <key>Label</key><string>com.zoar.task-worker</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON_BIN</string>
    <string>-u</string>
    <string>$ROOT_DIR/scripts/task_worker.py</string>
    <string>--loop</string>
    <string>--live</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT_DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$LOG_DIR/task-worker.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/task-worker.err</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONPATH</key><string>$ROOT_DIR</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
</dict>
</plist>"

write_plist "com.zoar.brain-task-scheduler" "$BRAIN_SCHED_PLIST" "<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
<plist version=\"1.0\">
<dict>
  <key>Label</key><string>com.zoar.brain-task-scheduler</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON_BIN</string>
    <string>-u</string>
    <string>$ROOT_DIR/scripts/brain_task_scheduler.py</string>
    <string>--daemon</string>
    <string>--poll-seconds</string>
    <string>300</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT_DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$LOG_DIR/brain-task-scheduler.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/brain-task-scheduler.err</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONPATH</key><string>$ROOT_DIR</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
</dict>
</plist>"

LABELS=(
  "com.zoar.process-manager"
  "com.zoar.master-coordinator"
  "com.zoar.brain-agent-runner"
  "com.zoar.brain-task-scheduler"
  "com.zoar.task-worker"
)

for label in "${LABELS[@]}"; do
  plist="$LAUNCH_DIR/$label.plist"
  if [[ ! -f "$plist" ]]; then
    echo "[autonomy] missing plist for $label at $plist (skipping)"
    continue
  fi
  launchctl bootout "$DOMAIN/$label" >/dev/null 2>&1 || true
  launchctl bootstrap "$DOMAIN" "$plist" >/dev/null 2>&1 || true
  launchctl kickstart -k "$DOMAIN/$label" >/dev/null 2>&1 || true
  echo "[autonomy] refreshed $label"
done

echo ""
echo "Autonomy stack refreshed."
echo "Check logs:"
echo "  tail -f $LOG_DIR/task-worker.log"
echo "  tail -f $LOG_DIR/brain-task-scheduler.log"
