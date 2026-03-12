#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/nexus_claude_env.sh"

mkdir -p "$(dirname "$NEXUS_SHARED_LOG_PATH")"
touch "$NEXUS_SHARED_LOG_PATH"

DEVICE_SHORT="$(printf '%s' "$NEXUS_DEVICE" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | sed 's/--*/-/g' | sed 's/^-//; s/-$//' | cut -c1-16)"
DEFAULT_SESSION_ID="claude-${DEVICE_SHORT:-device}-$(date +%H%M%S)"
SESSION_ID="${1:-${NEXUS_SESSION_ID:-$DEFAULT_SESSION_ID}}"
WORKING_ON="${2:-Claude Code connected to Nexus coordination}"
SESSION_ENV_FILE="${NEXUS_SESSION_ENV_FILE:-$HOME/.nexus/current_nexus_session.env}"

mkdir -p "$(dirname "$SESSION_ENV_FILE")"
cat > "$SESSION_ENV_FILE" <<EOF
export NEXUS_SESSION_ID=$(python3 - <<'PY' "$SESSION_ID"
import shlex, sys
print(shlex.quote(sys.argv[1]))
PY
)
export NEXUS_COORD_URL=$(python3 - <<'PY' "$NEXUS_COORD_URL"
import shlex, sys
print(shlex.quote(sys.argv[1]))
PY
)
export NEXUS_SHARED_LOG_PATH=$(python3 - <<'PY' "$NEXUS_SHARED_LOG_PATH"
import shlex, sys
print(shlex.quote(sys.argv[1]))
PY
)
export NEXUS_AGENT_TYPE=$(python3 - <<'PY' "$NEXUS_AGENT_TYPE"
import shlex, sys
print(shlex.quote(sys.argv[1]))
PY
)
export NEXUS_DEVICE=$(python3 - <<'PY' "$NEXUS_DEVICE"
import shlex, sys
print(shlex.quote(sys.argv[1]))
PY
)
EOF

echo "[nexus_claude_bootstrap] repo=$NEXUS_REPO_ROOT"
echo "[nexus_claude_bootstrap] coord_url=$NEXUS_COORD_URL"
echo "[nexus_claude_bootstrap] shared_log=$NEXUS_SHARED_LOG_PATH"
echo "[nexus_claude_bootstrap] session_env=$SESSION_ENV_FILE"
echo "[nexus_claude_bootstrap] session_id=$SESSION_ID"

MODE="read-only"
if [[ -n "${NEXUS_API_KEY:-}" ]]; then
  if "$SCRIPT_DIR/coord_checkin.sh" "$SESSION_ID" "$WORKING_ON" >/dev/null 2>&1; then
    "$SCRIPT_DIR/coord_context.sh" "$SESSION_ID" >/dev/null 2>&1 || true
    MODE="read-write"
  else
    echo "[nexus_claude_bootstrap] checkin failed; staying in read-only mode"
  fi
else
  echo "[nexus_claude_bootstrap] no API key found; live check-in disabled"
fi
echo "[nexus_claude_bootstrap] mode=$MODE"

if "$SCRIPT_DIR/coord_sync_shared_log.sh" >/dev/null 2>&1; then
  echo "[nexus_claude_bootstrap] shared log synced from server"
fi

STATUS_JSON="$(curl -sS --connect-timeout 4 --max-time 8 "$NEXUS_COORD_STATUS_URL" 2>/dev/null || true)"
if [[ -n "$STATUS_JSON" ]]; then
  python3 - <<'PY' "$STATUS_JSON"
import json, sys
raw = sys.argv[1]
try:
    data = json.loads(raw)
except Exception:
    print("[nexus_claude_bootstrap] failed to parse coordination status")
    raise SystemExit(0)

active = data.get("active_sessions", [])[:8]
completed = data.get("completed_today", [])[:5]
print("[nexus_claude_bootstrap] active_sessions:")
if not active:
    print("  - none")
for item in active:
    files = ", ".join(item.get("files_locked", []) or []) or "(none)"
    print(f"  - {item.get('session_id', '?')} | {item.get('agent_type', '?')} | {item.get('working_on', '-')}")
    print(f"    files: {files}")

print("[nexus_claude_bootstrap] completed_today:")
if not completed:
    print("  - none")
for item in completed:
    what = "; ".join(item.get("completed_work", []) or []) or "-"
    files = ", ".join(item.get("files_changed", []) or []) or "(none)"
    print(f"  - {item.get('session_id', '?')} | {what}")
    print(f"    files: {files}")
PY
fi

if [[ -s "$NEXUS_SHARED_LOG_PATH" ]]; then
  echo "[nexus_claude_bootstrap] shared log tail:"
  tail -n 40 "$NEXUS_SHARED_LOG_PATH"
else
  echo "[nexus_claude_bootstrap] shared log is empty"
fi

echo
echo "Next commands:"
echo "  source \"$SESSION_ENV_FILE\""
echo "  ./scripts/agent_change_log.sh \"$SESSION_ID\" \"<summary>\" <files...>"
echo "  ./scripts/coord_heartbeat.sh \"$SESSION_ID\" \"<working_on>\" <files...>"
if [[ "$MODE" == "read-write" ]]; then
  echo "  ./scripts/coord_checkout.sh \"$SESSION_ID\" \"<completed_work>\" <files...>"
else
  echo "  Run ./scripts/print_macbook_claude_setup.sh on the Mac mini, then paste its output once on the MacBook to enable live check-in."
fi
