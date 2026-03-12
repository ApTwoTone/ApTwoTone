#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/nexus_claude_env.sh"

LIMIT="${1:-50}"
URL="${NEXUS_COORD_URL%/}/api/coordination/log?limit=${LIMIT}"

RAW="$(curl -sS --connect-timeout 4 --max-time 10 "$URL" 2>/dev/null || true)"
if [[ -z "$RAW" ]]; then
  echo "coord_sync_shared_log: failed to fetch coordination log from $URL" >&2
  exit 1
fi

mkdir -p "$(dirname "$NEXUS_SHARED_LOG_PATH")"

python3 - <<'PY' "$RAW" "$NEXUS_SHARED_LOG_PATH"
import json, sys
raw, out_path = sys.argv[1:3]
data = json.loads(raw)
markdown = str(data.get("markdown") or "").strip()
if not markdown:
    raise SystemExit(1)
with open(out_path, "w", encoding="utf-8") as handle:
    handle.write(markdown + "\n")
print(out_path)
PY

