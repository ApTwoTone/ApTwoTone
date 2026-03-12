#!/usr/bin/env python3
"""Emergency resume — deactivates the kill switch to restore outbound messages.

Removes ~/.nexus/.kill_switch. Requires typing "CONFIRM RESUME".

Usage:
    python scripts/emergency_resume.py             # Interactive confirmation
    python scripts/emergency_resume.py --force      # Skip confirmation (for scripts)
    python scripts/emergency_resume.py --silent     # No Telegram notification
"""

import argparse
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("emergency_resume")

KILL_SWITCH = Path.home() / ".nexus" / ".kill_switch"


def deactivate_kill_switch(force: bool = False, silent: bool = False):
    """Remove the kill switch file after confirmation."""
    if not KILL_SWITCH.exists():
        print("  Kill switch is NOT active — system is already running normally.")
        return False

    content = KILL_SWITCH.read_text().strip()
    print(f"\n  Kill switch is ACTIVE: {content}")

    if not force:
        print(f"  Type 'CONFIRM RESUME' to deactivate:")
        confirm = input("  > ").strip()
        if confirm != "CONFIRM RESUME":
            print("  Resume cancelled. Kill switch remains active.")
            return False

    KILL_SWITCH.unlink()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log.info("KILL SWITCH DEACTIVATED at %s", timestamp)

    # Log to audit trail
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from audit_helpers import log_audit
        log_audit("kill_switch_deactivated", "emergency_resume", "system",
                  f"Previous: {content}")
    except ImportError:
        pass

    # Telegram alert
    if not silent:
        try:
            msg = f"EMERGENCY STOP LIFTED\nTime: {timestamp}\nOutbound messages are now ENABLED.\nPrevious stop: {content}"
            subprocess.run(
                [sys.executable, str(Path(__file__).parent / "notify_telegram.py"), msg],
                timeout=15, capture_output=True,
            )
        except Exception as e:
            log.error("Telegram alert failed: %s", e)

    print(f"\n  KILL SWITCH DEACTIVATED")
    print(f"  Time: {timestamp}")
    print(f"  Outbound messages are now ENABLED.")
    return True


def main():
    parser = argparse.ArgumentParser(description="Emergency resume — deactivate kill switch")
    parser.add_argument("--force", action="store_true", help="Skip confirmation")
    parser.add_argument("--silent", action="store_true", help="Skip Telegram notification")
    args = parser.parse_args()

    deactivate_kill_switch(args.force, args.silent)


if __name__ == "__main__":
    main()
