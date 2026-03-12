#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   scripts/coord_checkin.sh <session_id> <working_on> [file1 file2 ...]
#
# Examples:
#   scripts/coord_checkin.sh codex-macmini "Fix email stats" core/vendor_api.py
#   NEXUS_COORD_URL="https://crm.zoarbathroomrental.com" \
#     scripts/coord_checkin.sh claude-mbp "FB creative run" scripts/create_fb_campaign.py

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <session_id> [working_on] [file1 file2 ...]" >&2
  exit 1
fi

SESSION_ID="$1"
WORKING_ON="${2:-Starting session...}"
shift
[[ $# -gt 0 ]] && shift
FILES=("${@+"$@"}")

BASE_URL="${NEXUS_COORD_URL:-http://127.0.0.1:7860}"
AGENT_TYPE="${NEXUS_AGENT_TYPE:-codex}"
DEVICE="${NEXUS_DEVICE:-$(scutil --get ComputerName 2>/dev/null || hostname)}"
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

PAYLOAD="$(python3 - <<'PY' "$SESSION_ID" "$DEVICE" "$AGENT_TYPE" "$WORKING_ON" ${FILES[@]+"${FILES[@]}"}
import json, sys
session_id, device, agent_type, working_on = sys.argv[1:5]
files = [f for f in sys.argv[5:] if f.strip()]
print(json.dumps({
    "session_id": session_id,
    "device": device,
    "agent_type": agent_type,
    "working_on": working_on,
    "files_locked": files,
}, ensure_ascii=False))
PY
)"

HEADERS=(-H "Content-Type: application/json")
if [[ -n "$API_KEY" ]]; then
  HEADERS+=(-H "x-nexus-key: $API_KEY")
fi

URL="${BASE_URL%/}/api/coordination/checkin"
echo "[coord_checkin] POST $URL"
RESPONSE="$(curl -sS --fail-with-body -X POST "$URL" "${HEADERS[@]}" -d "$PAYLOAD")"
echo "$RESPONSE"
