#!/usr/bin/env python3
"""
fleet_status.py — Real-time CLI dashboard for the Nexus AI Fleet.
Displays status for all 44 keys and 10 providers.
"""
import os
import sys
import time
import argparse
from pathlib import Path

# Add nexus root to path
NEXUS_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(NEXUS_ROOT))

from core.key_rotation import get_pool

def format_quota(used, total):
    if total >= 999999:
        return "Unlimited"
    pct = (used / total) * 100
    color = "\033[92m" # Green
    if pct > 70: color = "\033[93m" # Yellow
    if pct > 90: color = "\033[91m" # Red
    return f"{color}{used:,}/{total:,}\033[0m"

def print_fleet():
    pool = get_pool()
    status = pool.fleet_status()

    os.system('clear' if os.name == 'posix' else 'cls')
    print("\033[1mNexus AI Fleet Command — Status Report\033[0m")
    print("-" * 80)
    print(f"Providers: {status['total_providers']} | Keys: {status['total_keys']} | Total RPM: \033[94m{status['total_rpm']:,}\033[0m")
    print(f"Parallel Workers: \033[92m{status['parallel_workers']:,}\033[0m | Calls Today: {status['total_calls_today']:,}")
    print("-" * 80)
    print(f"{'Provider':<15} {'Status':<12} {'RPM (Tot)':<12} {'Daily Quota':<20} {'Strength'}")
    print("-" * 80)

    for p in status['providers']:
        active_str = f"{p['keys_active']}/{p['keys_total']} active"
        rpm_str = f"{p['rpm_total']:,}"
        quota_str = format_quota(p['calls_today'], p['rpd_total'])

        # Color for status
        if p['keys_active'] == 0:
            status_color = "\033[91m" # Red
        elif p['keys_active'] < p['keys_total']:
            status_color = "\033[93m" # Yellow
        else:
            status_color = "\033[92m" # Green

        print(f"{p['name']:<15} {status_color}{active_str:<12}\033[0m {rpm_str:<12} {quota_str:<20} {p['strength']}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", "-w", action="store_true", help="Monitor in real-time")
    parser.add_argument("--interval", "-i", type=int, default=2, help="Watch interval")
    args = parser.parse_args()

    try:
        if args.watch:
            while True:
                print_fleet()
                time.sleep(args.interval)
        else:
            print_fleet()
    except KeyboardInterrupt:
        print("\nExiting...")

if __name__ == "__main__":
    main()
