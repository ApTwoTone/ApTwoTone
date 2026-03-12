"""
Pre-send safety gate for morning cold email batches.

Purpose:
- Validate approved queue rows before any send starts.
- Optionally auto-fix safe copy issues.
- Return a deterministic pass/fail report with actionable reasons.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

DB_PATH = Path.home() / ".nexus" / "memory.db"
LA_TZ = ZoneInfo("America/Los_Angeles")

_LOW_INTENT_KEYWORDS = (
    "golf club",
    "golf course",
    "country club",
    "resort",
    "marriott",
    "hilton",
    "hyatt",
    "sheraton",
    "ritz",
)

_GENERIC_LOCAL_PARTS = {
    "info", "hello", "contact", "admin", "support", "team",
    "events", "event", "weddings", "wedding", "bookings", "booking", "book",
    "sales", "office", "frontdesk", "mail", "inquiry", "inquiries",
    "service", "services",
}


def _conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _pt_today() -> str:
    return datetime.now(LA_TZ).strftime("%Y-%m-%d")


def _email_local(email_addr: str) -> str:
    s = (email_addr or "").strip().lower()
    if "@" not in s:
        return ""
    return s.split("@", 1)[0]


def _first_line(body: str) -> str:
    for line in (body or "").splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _looks_like_bad_greeting(first_line: str, recipient_email: str) -> bool:
    line = (first_line or "").strip()
    if not line:
        return True
    low = line.lower()
    if low in {"hi there!", "hey there!", "hello there!"}:
        return False
    m = re.match(r"^(hi|hey|hello)\s+([A-Za-z0-9._+\-']+)\!$", line, flags=re.IGNORECASE)
    if not m:
        return False
    token = (m.group(2) or "").strip().lower()
    if not token:
        return True
    local = _email_local(recipient_email)
    if token == local:
        return True
    if token in _GENERIC_LOCAL_PARTS:
        return True
    if any(ch.isdigit() for ch in token):
        return True
    if "." in token or "_" in token or "-" in token:
        return True
    return False


def _normalize_body(body: str, recipient_email: str) -> Tuple[str, bool]:
    """Apply safe deterministic copy cleanup. Returns (body, changed)."""
    src = (body or "").replace("\r\n", "\n")
    lines = src.split("\n")
    out: List[str] = []
    changed = False

    for line in lines:
        low = line.strip().lower()
        if low in {"--", "---"}:
            changed = True
            continue
        if "1-sentence script" in low:
            changed = True
            continue
        if "copy/paste to clients" in low:
            changed = True
            continue
        out.append(line)

    cleaned = "\n".join(out)
    cleaned = cleaned.replace("—", "-").replace("–", "-")
    cleaned = cleaned.replace("\n---\n", "\n\n")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    first = _first_line(cleaned)
    if _looks_like_bad_greeting(first, recipient_email):
        repl = "Hi there!"
        if first:
            cleaned = cleaned.replace(first, repl, 1)
        else:
            cleaned = repl + "\n\n" + cleaned
        changed = True

    if cleaned != src:
        changed = True
    return cleaned.strip() + "\n", changed


def _row_failures(row: Dict[str, Any]) -> List[str]:
    failures: List[str] = []
    subject = (row.get("subject") or "").strip()
    body = row.get("body_plain") or ""
    name = (row.get("recipient_name") or "").strip().lower()
    category = (row.get("category") or "").strip().lower()

    if not subject or len(subject) < 6:
        failures.append("missing_subject")
    if len(body.strip()) < 80:
        failures.append("body_too_short")
    if "1-sentence script" in body.lower():
        failures.append("contains_script_line")
    if "---" in body or "\n--\n" in body:
        failures.append("contains_ai_separator")
    if _looks_like_bad_greeting(_first_line(body), row.get("recipient_email", "")):
        failures.append("bad_greeting")
    if any(k in name for k in _LOW_INTENT_KEYWORDS):
        failures.append("low_intent_target")
    if category in {"country_club", "golf_club", "golf_course", "hotel", "hotel_venue", "resort"}:
        failures.append("low_intent_category")
    return failures


def run_presend_gate(
    scheduled_date: str = "",
    min_approved: int = 30,
    apply_autofix: bool = True,
) -> Dict[str, Any]:
    """Validate approved queue rows and optionally auto-fix safe issues."""
    # Reuse queue manager deliverability gate so existing queued rows get
    # screened before send time as well.
    from core.email_queue_manager import _email_preflight_reason

    target_date = (scheduled_date or "").strip() or _pt_today()
    conn = _conn()

    rows = conn.execute(
        """
        SELECT id, vendor_id, recipient_name, recipient_email, category, subject, body_plain, status
        FROM email_queue
        WHERE scheduled_date = ? AND status = 'approved'
        ORDER BY batch_number ASC, id ASC
        """,
        (target_date,),
    ).fetchall()

    approved_count = len(rows)
    effective_approved_count = approved_count
    autofixed = 0
    removed_for_deliverability = 0
    failing_rows: List[Dict[str, Any]] = []

    for raw in rows:
        row = dict(raw)
        if apply_autofix:
            normalized, changed = _normalize_body(row.get("body_plain", ""), row.get("recipient_email", ""))
            if changed:
                conn.execute(
                    "UPDATE email_queue SET body_plain = ?, edited_by = ?, edited_at = datetime('now') WHERE id = ?",
                    (normalized, "presend_gate_autofix", int(row["id"])),
                )
                row["body_plain"] = normalized
                autofixed += 1

        preflight_reason = _email_preflight_reason(conn, row.get("recipient_email", ""))
        if preflight_reason:
            if apply_autofix:
                conn.execute(
                    """
                    UPDATE email_queue
                    SET status = 'removed',
                        send_result = CASE
                            WHEN send_result IS NULL OR trim(send_result) = '' THEN ?
                            ELSE send_result || ' | ' || ?
                        END,
                        edited_by = ?,
                        edited_at = datetime('now')
                    WHERE id = ?
                    """,
                    (
                        f"presend_removed:{preflight_reason}",
                        f"presend_removed:{preflight_reason}",
                        "presend_gate_autofix",
                        int(row["id"]),
                    ),
                )
                removed_for_deliverability += 1
                effective_approved_count = max(0, effective_approved_count - 1)
                continue
            else:
                failing_rows.append(
                    {
                        "queue_id": int(row["id"]),
                        "vendor_id": int(row.get("vendor_id") or 0),
                        "recipient_name": row.get("recipient_name", ""),
                        "recipient_email": row.get("recipient_email", ""),
                        "reasons": [f"deliverability:{preflight_reason}"],
                    }
                )
                continue

        fails = _row_failures(row)
        if fails:
            failing_rows.append(
                {
                    "queue_id": int(row["id"]),
                    "vendor_id": int(row.get("vendor_id") or 0),
                    "recipient_name": row.get("recipient_name", ""),
                    "recipient_email": row.get("recipient_email", ""),
                    "reasons": fails,
                }
            )

    conn.commit()
    conn.close()

    failures: List[str] = []
    if effective_approved_count < int(min_approved):
        failures.append(f"approved_count_below_target:{effective_approved_count}/{int(min_approved)}")
    if failing_rows:
        failures.append(f"rows_failed_copy_checks:{len(failing_rows)}")

    passed = len(failures) == 0
    summary = (
        f"PASS • {effective_approved_count} approved, 0 failing rows"
        if passed
        else f"BLOCKED • {effective_approved_count} approved, {len(failing_rows)} failing rows"
    )
    return {
        "ok": passed,
        "scheduled_date": target_date,
        "approved_count": approved_count,
        "effective_approved_count": effective_approved_count,
        "min_approved": int(min_approved),
        "autofixed_rows": autofixed,
        "removed_for_deliverability": removed_for_deliverability,
        "failing_rows_count": len(failing_rows),
        "failing_rows": failing_rows[:30],
        "failures": failures,
        "summary": summary,
        "checked_at": datetime.now(LA_TZ).isoformat(timespec="seconds"),
    }
