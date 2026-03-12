#!/usr/bin/env python3
"""
Nexus Context Snapshot — DB-direct system state for agent coordination.

Reads ~/.nexus/memory.db directly (no server dependency) and outputs
a structured context summary that any Claude/Codex session can consume.

Usage:
  python3 scripts/nexus_context_snapshot.py          # plain text
  python3 scripts/nexus_context_snapshot.py --json   # JSON output
"""
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = Path.home() / ".nexus" / "memory.db"
TODAY = datetime.now().strftime("%Y-%m-%d")


def get_snapshot():
    if not DB_PATH.exists():
        return {"error": "Database not found at " + str(DB_PATH)}

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    snap = {"generated_at": datetime.now().isoformat(), "today": TODAY}

    # ── Agent Sessions ──
    try:
        active = conn.execute(
            "SELECT session_id, device, agent_type, working_on, files_locked, last_heartbeat "
            "FROM agent_sessions WHERE status = 'active' ORDER BY last_heartbeat DESC"
        ).fetchall()
        snap["active_sessions"] = [dict(r) for r in active]

        completed = conn.execute(
            "SELECT session_id, completed_work, files_locked AS files_changed, last_heartbeat "
            "FROM agent_sessions WHERE status = 'completed' AND date(last_heartbeat) = ? "
            "ORDER BY last_heartbeat DESC LIMIT 15", (TODAY,)
        ).fetchall()
        snap["completed_today"] = [dict(r) for r in completed]

        # File locks
        locks = {}
        for s in active:
            try:
                files = json.loads(s["files_locked"]) if s["files_locked"] else []
            except (json.JSONDecodeError, TypeError):
                files = [s["files_locked"]] if s["files_locked"] else []
            for f in files:
                if f and f.strip():
                    locks[f.strip()] = s["session_id"]
        snap["file_locks"] = locks
    except Exception:
        snap["active_sessions"] = []
        snap["completed_today"] = []
        snap["file_locks"] = {}

    # ── Leads ──
    try:
        total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        by_status = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM leads GROUP BY status ORDER BY cnt DESC"
        ).fetchall()
        by_source = conn.execute(
            "SELECT source, COUNT(*) as cnt FROM leads GROUP BY source ORDER BY cnt DESC LIMIT 5"
        ).fetchall()
        recent = conn.execute(
            "SELECT id, full_name, email, phone, source, status, event_type, event_date "
            "FROM leads ORDER BY id DESC LIMIT 5"
        ).fetchall()
        snap["leads"] = {
            "total": total,
            "by_status": {r["status"]: r["cnt"] for r in by_status},
            "top_sources": {r["source"]: r["cnt"] for r in by_source},
            "recent": [dict(r) for r in recent],
        }
    except Exception:
        snap["leads"] = {"total": 0, "by_status": {}, "top_sources": {}, "recent": []}

    # ── Vendors ──
    try:
        v_total = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
        v_with_email = conn.execute(
            "SELECT COUNT(*) FROM vendors WHERE email IS NOT NULL AND email != ''"
        ).fetchone()[0]
        snap["vendors"] = {"total": v_total, "with_email": v_with_email}
    except Exception:
        snap["vendors"] = {"total": 0, "with_email": 0}

    # ── Email Activity ──
    try:
        today_sends = conn.execute(
            "SELECT COUNT(*) FROM outbound_log WHERE timestamp LIKE ?", (TODAY + "%",)
        ).fetchone()[0]
        today_results = conn.execute(
            "SELECT result, COUNT(*) as cnt FROM outbound_log WHERE timestamp LIKE ? GROUP BY result",
            (TODAY + "%",)
        ).fetchall()
        queued = conn.execute(
            "SELECT COUNT(*) FROM email_queue WHERE status = 'queued'"
        ).fetchone()[0]
        snap["email"] = {
            "sent_today": today_sends,
            "results": {r["result"]: r["cnt"] for r in today_results},
            "queued": queued,
        }
    except Exception:
        snap["email"] = {"sent_today": 0, "results": {}, "queued": 0}

    # ── Task Board ──
    try:
        open_tasks = conn.execute(
            "SELECT id, title, priority, status, assigned_to, progress_note "
            "FROM task_board WHERE status != 'done' ORDER BY priority DESC, created_at ASC"
        ).fetchall()
        done_today = conn.execute(
            "SELECT id, title, assigned_to, completed_at "
            "FROM task_board WHERE status = 'done' AND date(completed_at) = ? "
            "ORDER BY completed_at DESC LIMIT 10", (TODAY,)
        ).fetchall()
        snap["task_board"] = {
            "open": [dict(r) for r in open_tasks],
            "done_today": [dict(r) for r in done_today],
        }
    except Exception:
        snap["task_board"] = {"open": [], "done_today": []}

    # ── Shared Agent Activity ──
    try:
        rows = conn.execute(
            "SELECT session_id, device, agent_type, summary, files_changed, created_at "
            "FROM agent_activity_log ORDER BY id DESC LIMIT 8"
        ).fetchall()
        items = []
        for row in rows:
            try:
                files_changed = json.loads(row["files_changed"]) if row["files_changed"] else []
            except Exception:
                files_changed = [row["files_changed"]] if row["files_changed"] else []
            items.append(
                {
                    "session_id": row["session_id"],
                    "device": row["device"],
                    "agent_type": row["agent_type"],
                    "summary": row["summary"],
                    "files_changed": files_changed,
                    "created_at": row["created_at"],
                }
            )
        snap["recent_activity"] = items
    except Exception:
        snap["recent_activity"] = []

    conn.close()
    return snap


def format_text(snap):
    lines = []
    lines.append("=" * 55)
    lines.append("NEXUS SYSTEM CONTEXT — " + snap.get("today", ""))
    lines.append("=" * 55)

    # Active agents
    sessions = snap.get("active_sessions", [])
    lines.append(f"\nACTIVE AGENTS: {len(sessions)}")
    for s in sessions:
        files = s.get("files_locked", "")
        if isinstance(files, str):
            try:
                files = json.loads(files)
            except (json.JSONDecodeError, TypeError):
                files = [files] if files else []
        file_str = ", ".join(f for f in files if f) if files else "none"
        lines.append(f"  {s['session_id']} ({s.get('agent_type','?')}) — {s.get('working_on','idle')}")
        lines.append(f"    files: {file_str} | heartbeat: {s.get('last_heartbeat','?')}")

    # File locks
    locks = snap.get("file_locks", {})
    if locks:
        lines.append(f"\nFILE LOCKS (DO NOT TOUCH):")
        for f, sid in locks.items():
            lines.append(f"  {f} -> {sid}")

    # DB state
    leads = snap.get("leads", {})
    lines.append(f"\nLEADS: {leads.get('total', 0)} total")
    for status, cnt in leads.get("by_status", {}).items():
        lines.append(f"  {status}: {cnt}")
    lines.append(f"  Top sources: {leads.get('top_sources', {})}")
    recent = leads.get("recent", [])
    if recent:
        lines.append(f"  Recent:")
        for r in recent[:3]:
            lines.append(f"    #{r['id']} {r.get('full_name','')} | {r.get('email','')} | {r.get('source','')} | {r.get('status','')}")

    vendors = snap.get("vendors", {})
    lines.append(f"\nVENDORS: {vendors.get('total', 0)} total, {vendors.get('with_email', 0)} with email")

    email = snap.get("email", {})
    lines.append(f"\nEMAIL TODAY: {email.get('sent_today', 0)} sent")
    for result, cnt in email.get("results", {}).items():
        lines.append(f"  {result}: {cnt}")
    lines.append(f"  Queued: {email.get('queued', 0)}")

    # Task board
    tb = snap.get("task_board", {})
    open_tasks = tb.get("open", [])
    if open_tasks:
        lines.append(f"\nTASK BOARD: {len(open_tasks)} open")
        for t in open_tasks:
            assignee = t.get("assigned_to") or "unassigned"
            note = f" — {t['progress_note']}" if t.get("progress_note") else ""
            lines.append(f"  #{t['id']} [P{t.get('priority',3)}] {t['title']} ({t['status']}, {assignee}){note}")
    else:
        lines.append(f"\nTASK BOARD: no open tasks")
    done_tasks = tb.get("done_today", [])
    if done_tasks:
        lines.append(f"  Done today: {len(done_tasks)}")
        for t in done_tasks[:3]:
            lines.append(f"    #{t['id']} {t['title']} — {t.get('assigned_to', '?')}")

    # Completed today
    completed = snap.get("completed_today", [])
    if completed:
        lines.append(f"\nCOMPLETED TODAY: {len(completed)} sessions")
        for c in completed[:5]:
            work = c.get("completed_work", "")
            if isinstance(work, str):
                try:
                    work = json.loads(work)
                except (json.JSONDecodeError, TypeError):
                    work = [work] if work else []
            work_str = ", ".join(work) if isinstance(work, list) else str(work)
            lines.append(f"  {c['session_id']}: {work_str}")
        if len(completed) > 5:
            lines.append(f"  ...and {len(completed) - 5} more")

    recent_activity = snap.get("recent_activity", [])
    if recent_activity:
        lines.append(f"\nRECENT SHARED ACTIVITY: {len(recent_activity)} entries")
        for item in recent_activity[:5]:
            files = ", ".join(item.get("files_changed", []) or []) or "none"
            lines.append(f"  {item.get('created_at', '?')} | {item.get('session_id', '?')} | {item.get('summary', '-')}")
            lines.append(f"    files: {files}")

    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    snap = get_snapshot()
    if "--json" in sys.argv:
        print(json.dumps(snap, indent=2, default=str))
    else:
        print(format_text(snap))
