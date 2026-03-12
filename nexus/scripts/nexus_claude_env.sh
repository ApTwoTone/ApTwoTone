#!/usr/bin/env bash

_nexus_claude_load_config_value() {
  local config_path="$1"
  local key="$2"
  python3 - <<'PY' "$config_path" "$key"
import json, sys
path, key = sys.argv[1:3]
try:
    data = json.load(open(path, "r"))
    value = str(data.get(key, "") or "").strip()
    print(value)
except Exception:
    print("")
PY
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_PATH="${NEXUS_CONFIG_PATH:-$HOME/.nexus/config.json}"
SHARED_ENV_FILE="${NEXUS_SHARED_ENV_FILE:-$HOME/.nexus/claude_coord.env}"

if [[ -f "$SHARED_ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$SHARED_ENV_FILE"
fi

if [[ -z "${NEXUS_COORD_URL:-}" ]]; then
  NEXUS_COORD_URL="$(_nexus_claude_load_config_value "$CONFIG_PATH" "fb_webhook_domain")"
fi
if [[ -z "${NEXUS_COORD_URL:-}" ]]; then
  NEXUS_COORD_URL="$(_nexus_claude_load_config_value "$CONFIG_PATH" "public_base_url")"
fi
if [[ -z "${NEXUS_COORD_URL:-}" ]]; then
  NEXUS_COORD_URL="https://crm.zoarbathroomrental.com"
fi

if [[ -z "${NEXUS_API_KEY:-}" ]]; then
  NEXUS_API_KEY="$(_nexus_claude_load_config_value "$CONFIG_PATH" "nexus_api_key")"
fi

if [[ -z "${NEXUS_AGENT_TYPE:-}" ]]; then
  NEXUS_AGENT_TYPE="claude_code"
fi

if [[ -z "${NEXUS_DEVICE:-}" ]]; then
  NEXUS_DEVICE="$(scutil --get ComputerName 2>/dev/null || hostname)"
fi

if [[ -z "${NEXUS_SHARED_LOG_PATH:-}" ]]; then
  NEXUS_SHARED_LOG_PATH="$REPO_ROOT/coordination/SHARED_AGENT_LOG.md"
fi

export NEXUS_REPO_ROOT="$REPO_ROOT"
export NEXUS_COORD_URL
export NEXUS_API_KEY
export NEXUS_AGENT_TYPE
export NEXUS_DEVICE
export NEXUS_SHARED_LOG_PATH
export NEXUS_COORD_STATUS_URL="${NEXUS_COORD_URL%/}/api/coordination/status"

