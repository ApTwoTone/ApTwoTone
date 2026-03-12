#!/usr/bin/env python3
"""
Generate Email Queue — creates tomorrow's email batch for morning review.

Run via launchd (com.zoar.email-queue-generator) at 8 PM daily,
or manually: python scripts/generate_email_queue.py
"""
import sys
import os
import logging

# Ensure nexus root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(message)s",
)

from core.email_queue_manager import generate_queue

if __name__ == "__main__":
    try:
        from core.agent_runtime import AgentRuntime
        _rt = AgentRuntime("email-queue-gen", "d1_vendor",
                           launchd_label="com.zoar.email-queue-generator")
        _rt.heartbeat("Generating email queue")
    except Exception:
        _rt = None

    result = generate_queue()
    print(f"Email queue generation: {result}")

    if _rt:
        _rt.complete_task(f"Queue: {result.get('status', 'unknown')}")

    sys.exit(0 if result.get("status") in ("ok", "already_exists", "no_vendors") else 1)
