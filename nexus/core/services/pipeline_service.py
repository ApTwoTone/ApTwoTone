"""
Pipeline Service — CRM stage transitions with validation.
Manages the booking_status field (the CRM-facing stage).
The old `status` field is preserved for backward-compatible scheduler logic.
"""
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path.home() / ".nexus" / "memory.db"

STAGES = {
    "new_lead":         {"label": "New Lead",         "color": "blue"},
    "auto_contacted":   {"label": "Auto Contacted",   "color": "cyan"},
    "qualifying":       {"label": "Qualifying",       "color": "yellow"},
    "quote_sent":       {"label": "Quote Sent",       "color": "purple"},
    "deposit_pending":  {"label": "Deposit Pending",  "color": "orange"},
    "booked":           {"label": "Booked",           "color": "green"},
    "completed":        {"label": "Completed",        "color": "emerald"},
    "lost":             {"label": "Lost",             "color": "red"},
}

TRANSITIONS = {
    "new_lead":        ["auto_contacted", "qualifying", "lost"],
    "auto_contacted":  ["qualifying", "quote_sent", "lost"],
    "qualifying":      ["quote_sent", "lost"],
    "quote_sent":      ["deposit_pending", "qualifying", "lost"],
    "deposit_pending": ["booked", "quote_sent", "lost"],
    "booked":          ["completed", "lost"],
    "completed":       [],
    "lost":            ["new_lead"],  # can reopen
}

# Maps pipeline actions to auto-transitions
ACTION_TRANSITIONS = {
    "initial_contact":  "auto_contacted",
    "reply_received":   "qualifying",
    "quote_sent":       "quote_sent",
    "deposit_sent":     "deposit_pending",
    "deposit_paid":     "booked",
    "event_completed":  "completed",
    "opted_out":        "lost",
    "closed":           "lost",
}


class PipelineService:
    def __init__(self, db_path=None, lead_service=None, timeline_service=None, notification_service=None):
        self.db_path = str(db_path or DB_PATH)
        self.lead_svc = lead_service
        self.timeline = timeline_service
        self.notify_svc = notification_service

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def transition(self, lead_id: int, to_stage: str, actor: str = "system", reason: str = "") -> dict:
        """Transition a lead to a new stage. Validates the transition is allowed."""
        if to_stage not in STAGES:
            return {"ok": False, "error": f"Invalid stage: {to_stage}"}

        conn = self._conn()
        row = conn.execute("SELECT booking_status, first_name, last_name FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not row:
            conn.close()
            return {"ok": False, "error": "Lead not found"}

        from_stage = row[0] or "new_lead"
        name = f"{row[1] or ''} {row[2] or ''}".strip()

        # Validate transition
        allowed = TRANSITIONS.get(from_stage, [])
        if to_stage not in allowed and from_stage != to_stage:
            return {"ok": False, "error": f"Cannot transition from {from_stage} to {to_stage}. Allowed: {allowed}"}

        conn.execute("UPDATE leads SET booking_status = ?, updated_at = ? WHERE id = ?",
                      (to_stage, datetime.utcnow().isoformat(timespec="seconds"), lead_id))
        conn.commit()
        conn.close()

        # Log timeline
        if self.timeline:
            detail = f"Stage: {STAGES[from_stage]['label']} → {STAGES[to_stage]['label']}"
            if reason:
                detail += f" ({reason})"
            self.timeline.log_event(lead_id, "stage_changed", detail, actor,
                {"from_stage": from_stage, "to_stage": to_stage, "reason": reason})

        return {"ok": True, "previous_stage": from_stage, "new_stage": to_stage, "lead_name": name}

    def auto_transition_on_action(self, lead_id: int, action_type: str):
        """Auto-advance the stage based on a pipeline action (e.g. initial_contact, reply_received)."""
        target = ACTION_TRANSITIONS.get(action_type)
        if not target:
            return

        conn = self._conn()
        row = conn.execute("SELECT booking_status FROM leads WHERE id = ?", (lead_id,)).fetchone()
        conn.close()
        if not row:
            return

        current = row[0] or "new_lead"
        allowed = TRANSITIONS.get(current, [])

        # Only advance if the target is a valid transition from current
        if target in allowed:
            self.transition(lead_id, target, actor="system", reason=f"Auto: {action_type}")

    def get_stage_counts(self) -> dict:
        conn = self._conn()
        rows = conn.execute("SELECT booking_status, COUNT(*) FROM leads GROUP BY booking_status").fetchall()
        conn.close()
        counts = {stage: 0 for stage in STAGES}
        for status, count in rows:
            if status in counts:
                counts[status] = count
        return counts

    def get_stages_info(self) -> list:
        """Return stage definitions with current counts."""
        counts = self.get_stage_counts()
        return [
            {"key": key, "label": info["label"], "color": info["color"], "count": counts.get(key, 0)}
            for key, info in STAGES.items()
        ]
