#!/usr/bin/env bash
# Auto-checkin for Claude Code sessions — called by SessionStart hook.
# Generates session ID from device name + timestamp, registers with coordination API.
# Non-blocking: errors are logged but don't break the session.

BASE_URL="${NEXUS_COORD_URL:-http://127.0.0.1:7860}"
DEVICE="$(scutil --get ComputerName 2>/dev/null || hostname)"
AGENT_TYPE="claude_code"
CONFIG_PATH="${NEXUS_CONFIG_PATH:-$HOME/.nexus/config.json}"
LOG_DIR="$HOME/.nexus/logs"
LOG_FILE="$LOG_DIR/coord_auto_checkin.log"

mkdir -p "$LOG_DIR"

# Generate session ID: claude-<device_short>-<timestamp>
DEVICE_SHORT="$(echo "$DEVICE" | tr '[:upper:]' '[:lower:]' | sed "s/[^a-z0-9]/-/g" | sed 's/--*/-/g' | cut -c1-12)"
TIMESTAMP="$(date +%H%M%S)"
SESSION_ID="claude-${DEVICE_SHORT}-${TIMESTAMP}"

# Read API key from config
API_KEY="${NEXUS_API_KEY:-}"
if [[ -z "$API_KEY" && -f "$CONFIG_PATH" ]]; then
  API_KEY="$(python3 -c "
import json
try:
    print(json.load(open('$CONFIG_PATH')).get('nexus_api_key', ''))
except: print('')
" 2>/dev/null)"
fi

PAYLOAD="{\"session_id\": \"$SESSION_ID\", \"device\": \"$DEVICE\", \"agent_type\": \"$AGENT_TYPE\", \"working_on\": \"Starting session...\"}"

HEADERS=(-H "Content-Type: application/json")
if [[ -n "$API_KEY" ]]; then
  HEADERS+=(-H "x-nexus-key: $API_KEY")
fi

URL="${BASE_URL%/}/api/coordination/checkin"

RESPONSE="$(curl -sS --connect-timeout 3 --max-time 5 -X POST "$URL" "${HEADERS[@]}" -d "$PAYLOAD" 2>&1)" || true

echo "[$(date '+%Y-%m-%d %H:%M:%S')] checkin session=$SESSION_ID device=$DEVICE response=$RESPONSE" >> "$LOG_FILE"

# Output full system context for the new session
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$SCRIPT_DIR/nexus_context_snapshot.py" 2>/dev/null || echo "Session registered: $SESSION_ID (context snapshot unavailable)"
