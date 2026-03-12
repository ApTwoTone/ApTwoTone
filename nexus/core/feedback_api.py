from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from core.feedback_collector import get_feedback_collector


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(v: Any) -> str:
    try:
        return str(v if v is not None else "").strip()
    except Exception:
        return ""


def _append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def record_feedback(payload: Dict[str, Any]) -> Dict[str, Any]:
    collector = get_feedback_collector()
    lead_id_raw = payload.get("lead_id")
    lead_id_text = _safe_text(lead_id_raw)
    lead_id: Any
    if not lead_id_text:
        lead_id = 0
    else:
        try:
            lead_id = int(lead_id_text)
        except Exception:
            lead_id = lead_id_text
    outcome = _safe_text(payload.get("outcome")).lower()
    if (isinstance(lead_id, int) and lead_id <= 0) or (isinstance(lead_id, str) and not lead_id):
        return {"ok": False, "error": "lead_id is required"}
    if outcome not in {"booked", "lost"}:
        return {"ok": False, "error": "outcome must be 'booked' or 'lost'"}

    row = {
        "created_at": _now(),
        "lead_id": lead_id,
        "outcome": outcome,
        "specialist_used": _safe_text(payload.get("specialist_used")),
        "model_version": _safe_text(payload.get("model_version")),
        "source": _safe_text(payload.get("source") or "api"),
        "details": payload.get("details") if isinstance(payload.get("details"), dict) else {},
    }
    if outcome == "booked":
        _append_jsonl(collector.positive_path, row)
    else:
        _append_jsonl(collector.negative_path, row)
    collector.state.setdefault("processed_outcomes", {})[f"{lead_id}:{outcome}"] = _now()
    collector._save_state()
    return {"ok": True, "row": row}
