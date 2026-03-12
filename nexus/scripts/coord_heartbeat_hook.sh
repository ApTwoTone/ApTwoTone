#!/usr/bin/env bash
# Claude Code hook handler — fires on UserPromptSubmit to auto-update agent status.
# Reads the user's prompt from stdin, extracts a brief description, and sends
# a heartbeat to the coordination API so other agents see current status.
#
# Called by Claude Code hook, NOT manually.

BASE_URL="${NEXUS_COORD_URL:-http://127.0.0.1:7860}"
CONFIG_PATH="${NEXUS_CONFIG_PATH:-$HOME/.nexus/config.json}"
LOG_FILE="$HOME/.nexus/logs/coord_heartbeat_hook.log"
mkdir -p "$(dirname "$LOG_FILE")"

# Read the hook input from stdin (JSON with prompt field)
INPUT="$(cat)"

# Extract the user's prompt text, truncate to 80 chars for status
STATUS="$(echo "$INPUT" | python3 -c "
import json, sys, re
try:
    data = json.load(sys.stdin)
    prompt = data.get('prompt', data.get('content', ''))
    # Clean up: collapse whitespace, strip leading slashes for commands
    prompt = re.sub(r'\s+', ' ', prompt).strip()
    if not prompt:
        sys.exit(0)
    # Truncate to 80 chars
    if len(prompt) > 80:
        prompt = prompt[:77] + '...'
    print(prompt)
except Exception:
    sys.exit(0)
" 2>/dev/null)" || true

# Skip if no meaningful status extracted
if [[ -z "$STATUS" ]]; then
  exit 0
fi

# Find our active session ID by device prefix (same logic as auto-checkout)
DEVICE="$(scutil --get ComputerName 2>/dev/null || hostname)"
DEVICE_SHORT="$(echo "$DEVICE" | tr '[:upper:]' '[:lower:]' | sed "s/[^a-z0-9]/-/g" | sed 's/--*/-/g' | cut -c1-12)"

SESSION_ID="$(curl -sS --connect-timeout 2 --max-time 3 "${BASE_URL%/}/api/coordination/status" 2>/dev/null | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    for s in data.get('active_sessions', []):
        sid = s.get('session_id', '')
        if sid.startswith('claude-') and '${DEVICE_SHORT}' in sid.lower():
            print(sid)
            break
except: pass
" 2>/dev/null)" || true

if [[ -z "$SESSION_ID" ]]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] hook: no active session found for device=$DEVICE" >> "$LOG_FILE"
  exit 0
fi

# Read API key
API_KEY="${NEXUS_API_KEY:-}"
if [[ -z "$API_KEY" && -f "$CONFIG_PATH" ]]; then
  API_KEY="$(python3 -c "
import json
try: print(json.load(open('$CONFIG_PATH')).get('nexus_api_key', ''))
except: print('')
" 2>/dev/null)" || true
fi

HEADERS=(-H "Content-Type: application/json")
if [[ -n "$API_KEY" ]]; then
  HEADERS+=(-H "x-nexus-key: $API_KEY")
fi

PAYLOAD="$(python3 -c "
import json, sys
print(json.dumps({'session_id': sys.argv[1], 'working_on': sys.argv[2]}))
" "$SESSION_ID" "$STATUS" 2>/dev/null)" || true

if [[ -n "$PAYLOAD" ]]; then
  curl -sS --connect-timeout 2 --max-time 3 -X POST \
    "${BASE_URL%/}/api/coordination/heartbeat" \
    "${HEADERS[@]}" -d "$PAYLOAD" >/dev/null 2>&1 || true

  echo "[$(date '+%Y-%m-%d %H:%M:%S')] hook: updated session=$SESSION_ID status='$STATUS'" >> "$LOG_FILE"
fi
