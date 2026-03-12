#!/usr/bin/env bash
set -euo pipefail

# Fetches live coordination + DB context snapshot and writes JSON + markdown.
#
# Usage:
#   scripts/coord_context.sh [session_id]
#
# Env:
#   NEXUS_COORD_URL      (default http://127.0.0.1:7860)
#   NEXUS_API_KEY        (optional; auto-reads ~/.nexus/config.json)
#   NEXUS_COORD_LIMIT    (default 20)
#   NEXUS_CONTEXT_OUT    (default ~/.nexus/coordination/<session_or_latest>_context.json)
#   NEXUS_CONTEXT_MD_OUT (default ~/.nexus/coordination/<session_or_latest>_context.md)

SESSION_ID="${1:-latest}"
BASE_URL="${NEXUS_COORD_URL:-http://127.0.0.1:7860}"
LIMIT="${NEXUS_COORD_LIMIT:-20}"
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

OUT_JSON="${NEXUS_CONTEXT_OUT:-$HOME/.nexus/coordination/${SESSION_ID}_context.json}"
OUT_MD="${NEXUS_CONTEXT_MD_OUT:-$HOME/.nexus/coordination/${SESSION_ID}_context.md}"
mkdir -p "$(dirname "$OUT_JSON")"
mkdir -p "$(dirname "$OUT_MD")"

HEADERS=(-H "Content-Type: application/json")
if [[ -n "$API_KEY" ]]; then
  HEADERS+=(-H "x-nexus-key: $API_KEY")
fi

URL="${BASE_URL%/}/api/coordination/context-pack?limit=${LIMIT}"
echo "[coord_context] GET $URL"
curl -sS --fail-with-body "$URL" "${HEADERS[@]}" > "$OUT_JSON"

python3 - <<'PY' "$OUT_JSON" "$OUT_MD"
import json, sys
from datetime import datetime

json_path, md_path = sys.argv[1], sys.argv[2]
data = json.load(open(json_path, "r"))

coord = data.get("coordination", {})
db = data.get("database_snapshot", {})

lines = []
lines.append("# Nexus Coordination Context Pack")
lines.append("")
lines.append(f"Generated: {data.get('generated_at', datetime.utcnow().isoformat() + 'Z')}")
lines.append("")

active = coord.get("active_sessions", [])
lines.append("## Active Sessions")
if active:
    for s in active:
        files = ", ".join(s.get("files_locked", [])) or "(none)"
        lines.append(f"- {s.get('session_id','?')} | {s.get('agent_type','?')} | {s.get('working_on','-')} | files: {files}")
else:
    lines.append("- (none)")
lines.append("")

lines.append("## Locked Files")
locked = coord.get("locked_files", {})
if locked:
    for fp, owner in sorted(locked.items()):
        lines.append(f"- {fp} -> {owner}")
else:
    lines.append("- (none)")
lines.append("")

lines.append("## Database Snapshot")
lines.append(f"- leads_total: {db.get('leads_total', 0)}")
lines.append(f"- conversations_total: {db.get('conversations_total', 0)}")
email_stats = db.get("email_stats", {})
lines.append(f"- sent_total: {email_stats.get('sent_total', 0)}")
lines.append(f"- sent_today: {email_stats.get('sent_today', 0)}")
lines.append(f"- bounced_total: {email_stats.get('bounced_total', 0)}")
lines.append(f"- pending_queue: {email_stats.get('pending_queue', 0)}")
lines.append("")

lines.append("## Lead Status Breakdown")
status_map = db.get("leads_by_status", {})
if status_map:
    for k, v in sorted(status_map.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"- {k}: {v}")
else:
    lines.append("- (none)")
lines.append("")

lines.append("## Recent Leads")
recent = db.get("recent_leads", [])
if recent:
    for lead in recent[:20]:
        lines.append(
            f"- #{lead.get('id')} | {lead.get('name','Unknown')} | {lead.get('phone','')} | "
            f"{lead.get('email','')} | {lead.get('booking_status') or lead.get('status') or 'unknown'}"
        )
else:
    lines.append("- (none)")
lines.append("")

lines.append("## Recent Conversations")
rc = db.get("recent_conversations", [])
if rc:
    for c in rc[:20]:
        lines.append(
            f"- lead_id={c.get('lead_id')} | {c.get('channel','')} | {c.get('last_direction','')} | "
            f"{(c.get('last_message_text','') or '').strip()[:120]}"
        )
else:
    lines.append("- (none)")
lines.append("")

open(md_path, "w").write("\n".join(lines).strip() + "\n")
print(md_path)
PY

echo "[coord_context] JSON: $OUT_JSON"
echo "[coord_context] MD:   $OUT_MD"
