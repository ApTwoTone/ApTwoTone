#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/nexus_claude_env.sh"

if [[ -z "${NEXUS_API_KEY:-}" ]]; then
  echo "Missing nexus_api_key in ~/.nexus/config.json or ~/.nexus/claude_coord.env" >&2
  exit 1
fi

python3 - <<'PY' "$NEXUS_COORD_URL" "$NEXUS_API_KEY"
import shlex, sys
coord_url, api_key = sys.argv[1:3]
print("mkdir -p ~/.nexus")
print("cat > ~/.nexus/claude_coord.env <<'EOF'")
print(f"export NEXUS_COORD_URL={shlex.quote(coord_url)}")
print(f"export NEXUS_API_KEY={shlex.quote(api_key)}")
print("export NEXUS_AGENT_TYPE='claude_code'")
print("EOF")
print("chmod 600 ~/.nexus/claude_coord.env")
PY

