# Nexus Task Board

## HIGH PRIORITY
- [ ] #4: Test: add debug log to task_worker (review_needed, unassigned) — Provider: groq | Model: meta-llama/llama-4-scout-17b-16e-instruct

DRY RUN -- output saved to progress_note only:

To add a debug log line at the start of the `get_next_task()` function in `scripts/task_worker.py` that logs "Checking for available tasks", you can modify the function as follows:

--- FILE: scripts/task_worker.py ---
```python
def get_next_task() -> Optional[Dict[str, Any]]:
    """Find highest-priority open, unassigned task with complexity < MAX_COMPLEXITY."""
    log.debug("Checking for available tasks")
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM task_board "
            "WHERE status = 'open' AND (assigned_to = '' OR assigned_to IS NULL) "
            "AND complexity < ? "
            "ORDER BY priority DESC, created_at ASC LIMIT 1",
            (MAX_COMPLEXITY,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
```
- [~] #2: Build instant quote responder (claimed, antigravity-coder-1)

## MEDIUM PRIORITY
- [~] #3: Import FB group leads into CRM (claimed, antigravity-coder-1)

## LOW PRIORITY
- (none)

## COMPLETED TODAY
- #1: Fix 49% email bounce rate — codex-macmini — 2026-03-10T23:24:50.073585

## RULES
1. Read this file at session start to see what Kai wants done.
2. Claim a task via `POST /api/tasks/{id}/claim` before starting.
3. Update progress as you work via `POST /api/tasks/{id}/update`.
4. Mark done when complete. TASKS.md auto-regenerates.
