#!/usr/bin/env bash
# Auto-checkout for Claude Code sessions — called by SessionEnd hook.
# Finds the most recent active session for this device and checks it out.

BASE_URL="${NEXUS_COORD_URL:-http://127.0.0.1:7860}"
DEVICE="$(scutil --get ComputerName 2>/dev/null || hostname)"
CONFIG_PATH="${NEXUS_CONFIG_PATH:-$HOME/.nexus/config.json}"
LOG_DIR="$HOME/.nexus/logs"
LOG_FILE="$LOG_DIR/coord_auto_checkin.log"

mkdir -p "$LOG_DIR"

# Read API key
API_KEY="${NEXUS_API_KEY:-}"
if [[ -z "$API_KEY" && -f "$CONFIG_PATH" ]]; then
  API_KEY="$(python3 -c "
import json
try:
    print(json.load(open('$CONFIG_PATH')).get('nexus_api_key', ''))
except: print('')
" 2>/dev/null)"
fi

HEADERS=(-H "Content-Type: application/json")
if [[ -n "$API_KEY" ]]; then
  HEADERS+=(-H "x-nexus-key: $API_KEY")
fi

# Find active session for this device
DEVICE_SHORT="$(echo "$DEVICE" | tr '[:upper:]' '[:lower:]' | sed "s/[^a-z0-9]/-/g" | sed 's/--*/-/g' | cut -c1-12)"
STATUS="$(curl -sS --connect-timeout 3 --max-time 5 "${BASE_URL%/}/api/coordination/status" 2>/dev/null)" || true

# Extract session_id of active sessions matching our device prefix
SESSION_ID="$(echo "$STATUS" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    for s in data.get('active_sessions', []):
        sid = s.get('session_id', '')
        if sid.startswith('claude-${DEVICE_SHORT}'):
            print(sid)
            break
except: pass
" 2>/dev/null)"

if [[ -z "$SESSION_ID" ]]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] checkout: no active session found for device=$DEVICE" >> "$LOG_FILE"
  exit 0
fi

PAYLOAD="{\"session_id\": \"$SESSION_ID\", \"status\": \"completed\", \"completed_work\": [\"Session ended\"]}"

URL="${BASE_URL%/}/api/coordination/checkout"
RESPONSE="$(curl -sS --connect-timeout 3 --max-time 5 -X POST "$URL" "${HEADERS[@]}" -d "$PAYLOAD" 2>&1)" || true

echo "[$(date '+%Y-%m-%d %H:%M:%S')] checkout session=$SESSION_ID response=$RESPONSE" >> "$LOG_FILE"
