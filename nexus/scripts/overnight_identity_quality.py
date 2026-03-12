#!/usr/bin/env python3
"""
Overnight Identity + Queue Quality Sweep.

Massive overnight task:
- Hard-merge duplicate lead identities (SMS+email aliases)
- Stage tomorrow's queue (if missing)
- Run pre-send safety autofix pass on tomorrow queue
"""
from __future__ import annotations

import sys
import json
from datetime import datetime, timedelta
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.email_presend_gate import run_presend_gate
from core.email_queue_manager import generate_queue
from core.lead_identity import reconcile_lead_identities

LA_TZ = ZoneInfo("America/Los_Angeles")


def run_overnight_identity_quality() -> dict:
    now_pt = datetime.now(LA_TZ)
    tomorrow = (now_pt + timedelta(days=1)).strftime("%Y-%m-%d")

    queue_result = generate_queue(scheduled_date=tomorrow, count=60)
    identity_result = reconcile_lead_identities(limit_clusters=0, dry_run=False)
    # min_approved=0 for overnight prep; this still performs deterministic autofixes.
    gate_result = run_presend_gate(
        scheduled_date=tomorrow,
        min_approved=0,
        apply_autofix=True,
    )

    return {
        "ok": True,
        "ran_at": now_pt.isoformat(timespec="seconds"),
        "target_date": tomorrow,
        "queue": queue_result,
        "identity": {
            "clusters_merged": identity_result.get("clusters_merged", 0),
            "merged_leads": identity_result.get("merged_leads", 0),
        },
        "presend": {
            "autofixed_rows": gate_result.get("autofixed_rows", 0),
            "failing_rows_count": gate_result.get("failing_rows_count", 0),
            "approved_count": gate_result.get("approved_count", 0),
        },
    }


def main():
    result = run_overnight_identity_quality()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
