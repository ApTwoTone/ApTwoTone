#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <session_id> <summary> [file1 file2 ...]" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/nexus_claude_env.sh"

SESSION_ID="$1"
SUMMARY="$2"
shift 2
FILES=("$@")

AGENT_LABEL="${NEXUS_AGENT_TYPE:-}"
case "$SESSION_ID" in
  codex-*)
    AGENT_LABEL="codex"
    ;;
  claude-*)
    AGENT_LABEL="claude_code"
    ;;
esac
if [[ -z "$AGENT_LABEL" ]]; then
  AGENT_LABEL="unknown"
fi

mkdir -p "$(dirname "$NEXUS_SHARED_LOG_PATH")"
touch "$NEXUS_SHARED_LOG_PATH"

TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S %Z')"
FILES_TEXT="$(python3 - <<'PY' "${FILES[@]}"
import sys
files = [item for item in sys.argv[1:] if item.strip()]
print(", ".join(files) if files else "(none listed)")
PY
)"

SYNCED=0
if [[ -n "${NEXUS_API_KEY:-}" && -n "${NEXUS_COORD_URL:-}" && -z "${NEXUS_SKIP_REMOTE_ACTIVITY_LOG:-}" ]]; then
  PAYLOAD="$(python3 - <<'PY' "$SESSION_ID" "$NEXUS_DEVICE" "$AGENT_LABEL" "$SUMMARY" ${FILES[@]+"${FILES[@]}"}
import json, sys
session_id, device, agent_type, summary = sys.argv[1:5]
files_changed = [item for item in sys.argv[5:] if item.strip()]
print(json.dumps({
    "session_id": session_id,
    "device": device,
    "agent_type": agent_type,
    "entry_kind": "update",
    "summary": summary,
    "files_changed": files_changed,
}, ensure_ascii=False))
PY
)"
  RESPONSE="$(curl -sS --connect-timeout 4 --max-time 10 \
    -H "Content-Type: application/json" \
    -H "x-nexus-key: $NEXUS_API_KEY" \
    -X POST "${NEXUS_COORD_URL%/}/api/coordination/log" \
    -d "$PAYLOAD" 2>/dev/null || true)"
  if [[ -n "$RESPONSE" ]]; then
    if "$SCRIPT_DIR/coord_sync_shared_log.sh" >/dev/null 2>&1; then
      SYNCED=1
    fi
  fi
fi

if [[ "$SYNCED" -ne 1 ]]; then
  {
    echo "## $TIMESTAMP | $SESSION_ID"
    echo "Device: $NEXUS_DEVICE"
    echo "Agent: $AGENT_LABEL"
    echo "Summary: $SUMMARY"
    echo "Files: $FILES_TEXT"
    echo
  } >> "$NEXUS_SHARED_LOG_PATH"
fi

echo "$NEXUS_SHARED_LOG_PATH"
