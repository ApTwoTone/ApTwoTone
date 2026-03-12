from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


MEMORY_DB = Path.home() / ".nexus" / "memory.db"
DEFAULT_FEEDBACK_DIR = Path("/workspace/nexus_brain_lab/feedback")
if not DEFAULT_FEEDBACK_DIR.exists():
    DEFAULT_FEEDBACK_DIR = Path.home() / ".nexus" / "runpod_feedback"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(v: Any) -> str:
    try:
        return str(v if v is not None else "").strip()
    except Exception:
        return ""


def _parse_dt(v: Any) -> Optional[datetime]:
    text = _safe_text(v)
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


@dataclass
class FeedbackCollector:
    db_path: Path = MEMORY_DB
    feedback_dir: Path = DEFAULT_FEEDBACK_DIR

    def __post_init__(self) -> None:
        self.feedback_dir.mkdir(parents=True, exist_ok=True)
        self.positive_path = self.feedback_dir / "positive_examples.jsonl"
        self.negative_path = self.feedback_dir / "negative_examples.jsonl"
        self.weekly_delta_path = self.feedback_dir / "weekly_training_delta.jsonl"
        self.state_path = self.feedback_dir / "feedback_state.json"
        self.state = _read_json(
            self.state_path,
            {"processed_outcomes": {}, "last_scan_at": "", "last_delta_export_at": ""},
        )

    def _save_state(self) -> None:
        self.state_path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _table_exists(self, conn: sqlite3.Connection, table: str) -> bool:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        return bool(row)

    def _table_columns(self, conn: sqlite3.Connection, table: str) -> List[str]:
        try:
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            return [r[1] for r in rows]
        except Exception:
            return []

    def _status_column(self, cols: List[str]) -> str:
        for c in ("booking_status", "status", "lead_status"):
            if c in cols:
                return c
        return ""

    def _updated_column(self, cols: List[str]) -> str:
        for c in ("updated_at", "score_updated_at", "created_at"):
            if c in cols:
                return c
        return ""

    def _append_jsonl(self, path: Path, row: Dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _fetch_last_quote_context(self, conn: sqlite3.Connection, lead_id: int) -> Dict[str, Any]:
        if not self._table_exists(conn, "lead_messages"):
            return {}
        cols = self._table_columns(conn, "lead_messages")
        wanted = [c for c in ("id", "lead_id", "direction", "channel", "content", "subject", "created_at", "ts") if c in cols]
        if not wanted:
            return {}
        rows = conn.execute(
            f"SELECT {', '.join(wanted)} FROM lead_messages WHERE lead_id=? ORDER BY id DESC LIMIT 25",
            (lead_id,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if _safe_text(d.get("direction")).lower() != "outbound":
                continue
            out.append(d)
        return {"recent_outbound": out[:8], "last_outbound": (out[0] if out else {})}

    def collect_once(self) -> Dict[str, Any]:
        if not self.db_path.exists():
            return {"ok": False, "error": f"DB not found: {self.db_path}"}
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        positives = 0
        negatives = 0
        scanned = 0
        try:
            if not self._table_exists(conn, "leads"):
                return {"ok": False, "error": "leads table not found"}
            cols = self._table_columns(conn, "leads")
            status_col = self._status_column(cols)
            updated_col = self._updated_column(cols)
            if not status_col:
                return {"ok": False, "error": "no status column on leads table"}

            wanted = [c for c in ("id", "first_name", "last_name", "full_name", "phone", "email", "event_type", "guest_count", "event_city", "source", "created_at") if c in cols]
            if updated_col and updated_col not in wanted:
                wanted.append(updated_col)
            if status_col not in wanted:
                wanted.append(status_col)
            rows = conn.execute(f"SELECT {', '.join(wanted)} FROM leads").fetchall()
            for r in rows:
                row = dict(r)
                scanned += 1
                lead_id = int(row.get("id") or 0)
                status = _safe_text(row.get(status_col)).lower()
                if status not in {"booked", "won", "closed_won", "lost", "closed_lost", "no_response"}:
                    continue
                key = f"{lead_id}:{status}"
                if key in (self.state.get("processed_outcomes") or {}):
                    continue
                quote_ctx = self._fetch_last_quote_context(conn, lead_id)
                created = _parse_dt(row.get("created_at"))
                updated = _parse_dt(row.get(updated_col)) if updated_col else None
                time_to_booking_h = None
                if created and updated:
                    time_to_booking_h = round((updated - created).total_seconds() / 3600.0, 2)

                payload = {
                    "created_at": _now(),
                    "lead_id": lead_id,
                    "outcome": "booked" if status in {"booked", "won", "closed_won"} else "lost",
                    "status_raw": status,
                    "lead": {
                        "name": _safe_text(row.get("full_name")) or f"{_safe_text(row.get('first_name'))} {_safe_text(row.get('last_name'))}".strip(),
                        "phone": _safe_text(row.get("phone")),
                        "email": _safe_text(row.get("email")),
                        "event_type": _safe_text(row.get("event_type")),
                        "guest_count": row.get("guest_count"),
                        "event_city": _safe_text(row.get("event_city")),
                        "source": _safe_text(row.get("source")),
                    },
                    "quote_content": quote_ctx.get("last_outbound", {}),
                    "follow_up_sequence": quote_ctx.get("recent_outbound", []),
                    "time_to_booking_hours": time_to_booking_h,
                    "distance_tier": None,
                    "suspected_loss_reason": "no_response_or_price_friction" if status not in {"booked", "won", "closed_won"} else "",
                }
                if payload["outcome"] == "booked":
                    self._append_jsonl(self.positive_path, payload)
                    positives += 1
                else:
                    self._append_jsonl(self.negative_path, payload)
                    negatives += 1
                self.state.setdefault("processed_outcomes", {})[key] = _now()

            self.state["last_scan_at"] = _now()
            self._save_state()
            return {"ok": True, "scanned": scanned, "positive_written": positives, "negative_written": negatives}
        finally:
            conn.close()

    def export_weekly_delta(self) -> Dict[str, Any]:
        last_export = _parse_dt(self.state.get("last_delta_export_at")) or (datetime.now(timezone.utc) - timedelta(days=7))
        rows: List[Dict[str, Any]] = []
        for path in (self.positive_path, self.negative_path):
            if not path.exists():
                continue
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    ts = _parse_dt(row.get("created_at"))
                    if ts and ts >= last_export:
                        rows.append(row)
        with self.weekly_delta_path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.state["last_delta_export_at"] = _now()
        self._save_state()
        return {"ok": True, "rows": len(rows), "path": str(self.weekly_delta_path)}

    def run_forever(self, poll_seconds: int = 60) -> None:
        while True:
            self.collect_once()
            time.sleep(max(5, int(poll_seconds)))


_COLLECTOR: Optional[FeedbackCollector] = None


def get_feedback_collector() -> FeedbackCollector:
    global _COLLECTOR
    if _COLLECTOR is None:
        _COLLECTOR = FeedbackCollector()
    return _COLLECTOR
