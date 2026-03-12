#!/usr/bin/env bash
set -euo pipefail

# Session watcher:
# - check in once
# - send periodic heartbeat
# - check out automatically on Ctrl+C / terminate
#
# Usage:
#   scripts/coord_watch.sh <session_id> "<working_on>" [file1 file2 ...]
#
# Env:
#   NEXUS_COORD_URL
#   NEXUS_AGENT_TYPE
#   NEXUS_DEVICE
#   NEXUS_HEARTBEAT_SECONDS (default 60)
#   NEXUS_CHECKOUT_MESSAGE  (default "Session stopped")

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <session_id> <working_on> [file1 file2 ...]" >&2
  exit 1
fi

SESSION_ID="$1"
WORKING_ON="$2"
shift 2
FILES=("$@")

BASE_URL="${NEXUS_COORD_URL:-http://127.0.0.1:7860}"
AGENT_TYPE="${NEXUS_AGENT_TYPE:-codex}"
DEVICE="${NEXUS_DEVICE:-$(scutil --get ComputerName 2>/dev/null || hostname)}"
INTERVAL="${NEXUS_HEARTBEAT_SECONDS:-60}"
CHECKOUT_MSG="${NEXUS_CHECKOUT_MESSAGE:-Session stopped}"
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

export NEXUS_COORD_URL="$BASE_URL"
if [[ -n "$API_KEY" ]]; then
  export NEXUS_API_KEY="$API_KEY"
fi
export NEXUS_AGENT_TYPE="$AGENT_TYPE"
export NEXUS_DEVICE="$DEVICE"

scripts/coord_checkin.sh "$SESSION_ID" "$WORKING_ON" "${FILES[@]}"

RUNNING=1
on_stop() {
  RUNNING=0
}
trap on_stop INT TERM

HEADERS=(-H "Content-Type: application/json")
if [[ -n "$API_KEY" ]]; then
  HEADERS+=(-H "x-nexus-key: $API_KEY")
fi
HB_URL="${BASE_URL%/}/api/coordination/heartbeat"

echo "[coord_watch] running heartbeat loop every ${INTERVAL}s for session: $SESSION_ID"
while [[ "$RUNNING" -eq 1 ]]; do
  PAYLOAD="$(python3 - <<'PY' "$SESSION_ID" "$DEVICE" "$AGENT_TYPE" "$WORKING_ON" "${FILES[@]}"
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
  curl -sS --fail-with-body -X POST "$HB_URL" "${HEADERS[@]}" -d "$PAYLOAD" >/dev/null || true
  sleep "$INTERVAL"
done

scripts/coord_checkout.sh "$SESSION_ID" "$CHECKOUT_MSG" "${FILES[@]}" >/dev/null 2>&1 || true
echo "[coord_watch] checked out: $SESSION_ID"
