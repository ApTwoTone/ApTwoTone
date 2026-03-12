#!/usr/bin/env python3
"""Emergency stop — activates the kill switch to halt all outbound messages.

Creates ~/.nexus/.kill_switch which is checked by SecurityGate gate 2.
When active, ALL outbound messages are blocked system-wide.

Usage:
    python scripts/emergency_stop.py              # Activate kill switch
    python scripts/emergency_stop.py --reason "spam detected"  # With reason
    python scripts/emergency_stop.py --silent      # No Telegram notification
"""

import argparse
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("emergency_stop")

KILL_SWITCH = Path.home() / ".nexus" / ".kill_switch"


def activate_kill_switch(reason: str = "Manual activation", silent: bool = False):
    """Create the kill switch file and notify."""
    if KILL_SWITCH.exists():
        print(f"  Kill switch ALREADY ACTIVE (created: {KILL_SWITCH.read_text().strip()})")
        print(f"  To resume: python scripts/emergency_resume.py")
        return False

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    KILL_SWITCH.write_text(f"{timestamp} | {reason}")
    log.info("KILL SWITCH ACTIVATED: %s", reason)

    # Log to audit trail
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from audit_helpers import log_audit
        log_audit("kill_switch_activated", "emergency_stop", "system", reason)
    except ImportError:
        pass

    # Telegram alert
    if not silent:
        try:
            msg = f"EMERGENCY STOP ACTIVATED\nReason: {reason}\nTime: {timestamp}\nAll outbound messages are now BLOCKED."
            subprocess.run(
                [sys.executable, str(Path(__file__).parent / "notify_telegram.py"), msg],
                timeout=15, capture_output=True,
            )
        except Exception as e:
            log.error("Telegram alert failed: %s", e)

    print(f"\n  KILL SWITCH ACTIVATED")
    print(f"  Time: {timestamp}")
    print(f"  Reason: {reason}")
    print(f"  All outbound messages are now BLOCKED.")
    print(f"  To resume: python scripts/emergency_resume.py")
    return True


def main():
    parser = argparse.ArgumentParser(description="Emergency stop — activate kill switch")
    parser.add_argument("--reason", type=str, default="Manual activation",
                        help="Reason for emergency stop")
    parser.add_argument("--silent", action="store_true", help="Skip Telegram notification")
    args = parser.parse_args()

    activate_kill_switch(args.reason, args.silent)


if __name__ == "__main__":
    main()
