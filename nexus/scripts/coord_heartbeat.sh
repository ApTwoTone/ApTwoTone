#!/usr/bin/env bash
# Lightweight status updater for agent sessions.
# Calls the heartbeat endpoint to update working_on and/or files_locked.
#
# Usage:
#   scripts/coord_heartbeat.sh <session_id> ["status message"] [file1 file2 ...]
#
# Examples:
#   scripts/coord_heartbeat.sh claude-2-macmini "Building live dashboard" static/agents.html
#   scripts/coord_heartbeat.sh claude-2-macmini   # heartbeat-only, no status change

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <session_id> [working_on] [file1 file2 ...]" >&2
  exit 1
fi

SESSION_ID="$1"
WORKING_ON="${2:-}"
shift
[[ $# -gt 0 ]] && shift
FILES=("${@+"$@"}")

BASE_URL="${NEXUS_COORD_URL:-http://127.0.0.1:7860}"
CONFIG_PATH="${NEXUS_CONFIG_PATH:-$HOME/.nexus/config.json}"

API_KEY="${NEXUS_API_KEY:-}"
if [[ -z "$API_KEY" && -f "$CONFIG_PATH" ]]; then
  API_KEY="$(python3 -c "
import json
try: print(json.load(open('$CONFIG_PATH')).get('nexus_api_key', ''))
except: print('')
" 2>/dev/null)" || true
fi

# Build JSON payload — only include fields that have values
PAYLOAD="$(python3 -c "
import json, sys
session_id = sys.argv[1]
working_on = sys.argv[2] if len(sys.argv) > 2 else ''
files = [f for f in sys.argv[3:] if f.strip()]
d = {'session_id': session_id}
if working_on:
    d['working_on'] = working_on
if files:
    d['files_locked'] = files
print(json.dumps(d, ensure_ascii=False))
" "$SESSION_ID" "$WORKING_ON" ${FILES[@]+"${FILES[@]}"})"

HEADERS=(-H "Content-Type: application/json")
if [[ -n "$API_KEY" ]]; then
  HEADERS+=(-H "x-nexus-key: $API_KEY")
fi

URL="${BASE_URL%/}/api/coordination/heartbeat"
RESPONSE="$(curl -sS --connect-timeout 3 --max-time 5 -X POST "$URL" "${HEADERS[@]}" -d "$PAYLOAD" 2>&1)" || true
echo "$RESPONSE"
