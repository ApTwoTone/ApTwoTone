#!/bin/bash
set -euo pipefail

DOMAIN="gui/$(id -u)"
LABELS=(
  "com.zoar.nexus-server"
  "com.zoar.process-manager"
  "com.zoar.master-coordinator"
  "com.zoar.brain-agent-runner"
  "com.zoar.brain-task-scheduler"
  "com.zoar.task-worker"
  "com.zoar.email-outreach"
  "com.zoar.prelaunch-check"
)

for label in "${LABELS[@]}"; do
  plist="$HOME/Library/LaunchAgents/${label}.plist"
  if [ -f "$plist" ]; then
    if ! launchctl print "$DOMAIN/$label" >/dev/null 2>&1; then
      launchctl bootstrap "$DOMAIN" "$plist" >/dev/null 2>&1 || true
    fi
    launchctl kickstart -k "$DOMAIN/$label" >/dev/null 2>&1 || true
  fi
done

echo "start_nexus: launchd services refreshed at $(date '+%Y-%m-%d %H:%M:%S')"
