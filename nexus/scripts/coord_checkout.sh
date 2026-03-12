#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   scripts/coord_checkout.sh <session_id> <completed_work> [file1 file2 ...]
#
# Examples:
#   scripts/coord_checkout.sh codex-macmini "Implemented A/B email variants" core/email_sequences.py
#   NEXUS_COORD_URL="https://crm.zoarbathroomrental.com" \
#     scripts/coord_checkout.sh claude-mbp "Finished FB creatives" scripts/create_fb_campaign.py

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <session_id> <completed_work> [file1 file2 ...]" >&2
  exit 1
fi

SESSION_ID="$1"
COMPLETED_WORK="$2"
shift 2
FILES_CHANGED=("$@")

BASE_URL="${NEXUS_COORD_URL:-http://127.0.0.1:7860}"
STATUS="${NEXUS_COORD_STATUS:-completed}"
CONFIG_PATH="${NEXUS_CONFIG_PATH:-$HOME/.nexus/config.json}"

API_KEY="${NEXUS_API_KEY:-}"
if [[ -z "$API_KEY" && -f "$CONFIG_PATH" ]]; then
  API_KEY="$(python3 - <<'PY' "$CONFIG_PATH"
import json, sys
try:
    cfg = json.load(open(sys.argv[1], "r"))
    print(cfg.get("nexus_api_key", ""))
except Exception:
    print("")
PY
)"
fi

PAYLOAD="$(python3 - <<'PY' "$SESSION_ID" "$STATUS" "$COMPLETED_WORK" ${FILES_CHANGED[@]+"${FILES_CHANGED[@]}"}
import json, sys
session_id, status, completed = sys.argv[1:4]
files_changed = [f for f in sys.argv[4:] if f.strip()]
print(json.dumps({
    "session_id": session_id,
    "status": status,
    "completed_work": [completed],
    "files_changed": files_changed,
}, ensure_ascii=False))
PY
)"

HEADERS=(-H "Content-Type: application/json")
if [[ -n "$API_KEY" ]]; then
  HEADERS+=(-H "x-nexus-key: $API_KEY")
fi

URL="${BASE_URL%/}/api/coordination/checkout"
echo "[coord_checkout] POST $URL"
RESPONSE="$(curl -sS --fail-with-body -X POST "$URL" "${HEADERS[@]}" -d "$PAYLOAD")"
echo "$RESPONSE"

if printf '%s' "$RESPONSE" | grep -q '"status":"ok"'; then
  NEXUS_SKIP_REMOTE_ACTIVITY_LOG=""
  export NEXUS_SKIP_REMOTE_ACTIVITY_LOG
  if [[ -x "$(dirname "$0")/agent_change_log.sh" ]]; then
    "$(dirname "$0")/agent_change_log.sh" "$SESSION_ID" "$COMPLETED_WORK" ${FILES_CHANGED[@]+"${FILES_CHANGED[@]}"} >/dev/null 2>&1 || true
  fi
fi
