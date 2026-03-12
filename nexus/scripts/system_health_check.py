#!/usr/bin/env python3
"""System health check — on-demand RAM, disk, DB, server, and vendor monitoring.

Usage:
    python scripts/system_health_check.py              # Run all checks, print results
    python scripts/system_health_check.py --alert      # Send Telegram alert on any warning/critical
    python scripts/system_health_check.py --json       # Output JSON for scripting
"""

import argparse
import json
import logging
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("health_check")

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_PATH = Path.home() / ".nexus" / "config.json"
SERVER_PORT = 7860

# Thresholds
RAM_WARN = 85   # percent
RAM_CRIT = 90
DISK_WARN_GB = 20
DISK_CRIT_GB = 5
DB_SIZE_WARN_MB = 500
DB_FREELIST_WARN = 40  # percent
VENDOR_INCREASE_ALERT = 5  # if count increases by this much, alert


def check_ram() -> Dict[str, Any]:
    """Check system RAM usage via vm_stat (macOS)."""
    try:
        out = subprocess.check_output(["vm_stat"], text=True)
        lines = out.strip().split("\n")
        page_size = 16384  # default
        if "page size of" in lines[0]:
            page_size = int(lines[0].split("page size of")[1].strip().split()[0])

        stats = {}
        for line in lines[1:]:
            if ":" in line:
                key, val = line.split(":", 1)
                val = val.strip().rstrip(".")
                try:
                    stats[key.strip()] = int(val)
                except ValueError:
                    pass

        free = stats.get("Pages free", 0) * page_size
        active = stats.get("Pages active", 0) * page_size
        inactive = stats.get("Pages inactive", 0) * page_size
        wired = stats.get("Pages wired down", 0) * page_size
        compressed = stats.get("Pages occupied by compressor", 0) * page_size

        total = free + active + inactive + wired + compressed
        used = active + wired + compressed
        pct = (used / total * 100) if total > 0 else 0

        status = "ok"
        if pct >= RAM_CRIT:
            status = "critical"
        elif pct >= RAM_WARN:
            status = "warning"

        return {
            "check": "ram",
            "status": status,
            "used_pct": round(pct, 1),
            "used_gb": round(used / (1024**3), 1),
            "total_gb": round(total / (1024**3), 1),
            "free_gb": round(free / (1024**3), 1),
        }
    except Exception as e:
        return {"check": "ram", "status": "error", "message": str(e)}


def check_disk() -> Dict[str, Any]:
    """Check disk space on root volume."""
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = total - free
        free_gb = free / (1024**3)

        status = "ok"
        if free_gb < DISK_CRIT_GB:
            status = "critical"
        elif free_gb < DISK_WARN_GB:
            status = "warning"

        return {
            "check": "disk",
            "status": status,
            "free_gb": round(free_gb, 1),
            "total_gb": round(total / (1024**3), 1),
            "used_pct": round(used / total * 100, 1),
        }
    except Exception as e:
        return {"check": "disk", "status": "error", "message": str(e)}


def check_db() -> Dict[str, Any]:
    """Check database size, integrity, and freelist."""
    try:
        if not DB_PATH.exists():
            return {"check": "db", "status": "critical", "message": "Database not found"}

        size_mb = DB_PATH.stat().st_size / (1024 * 1024)
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("PRAGMA query_only = ON")

        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        page_count = conn.execute("PRAGMA page_count").fetchone()[0]
        freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]
        freelist_pct = (freelist / page_count * 100) if page_count > 0 else 0

        vendor_count = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
        lead_count = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        conn.close()

        status = "ok"
        if integrity != "ok":
            status = "critical"
        elif size_mb > DB_SIZE_WARN_MB or freelist_pct > DB_FREELIST_WARN:
            status = "warning"

        return {
            "check": "db",
            "status": status,
            "size_mb": round(size_mb, 1),
            "integrity": integrity,
            "freelist_pct": round(freelist_pct, 1),
            "vendors": vendor_count,
            "leads": lead_count,
        }
    except Exception as e:
        return {"check": "db", "status": "error", "message": str(e)}


def check_server() -> Dict[str, Any]:
    """Check if the Nexus server is responding on port 7860."""
    try:
        import urllib.request
        url = f"http://localhost:{SERVER_PORT}/api/health"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            code = resp.getcode()
            return {
                "check": "server",
                "status": "ok" if code == 200 else "warning",
                "http_code": code,
                "port": SERVER_PORT,
            }
    except Exception as e:
        return {
            "check": "server",
            "status": "critical",
            "message": f"Server not responding: {e}",
            "port": SERVER_PORT,
        }


def check_vendor_count() -> Dict[str, Any]:
    """Check if vendor count has increased (sign of rogue daemon)."""
    count_file = Path.home() / ".nexus" / "vendor_count_last.txt"

    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("PRAGMA query_only = ON")
        current = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
        last_insert = conn.execute("SELECT MAX(created_at) FROM vendors").fetchone()[0]
        conn.close()

        previous = None
        if count_file.exists():
            try:
                previous = int(count_file.read_text().strip())
            except ValueError:
                pass

        # Save current count for next run
        count_file.write_text(str(current))

        increase = (current - previous) if previous is not None else 0
        status = "ok"
        if increase > VENDOR_INCREASE_ALERT:
            status = "critical"

        return {
            "check": "vendor_count",
            "status": status,
            "current": current,
            "previous": previous,
            "increase": increase,
            "last_insert": last_insert,
        }
    except Exception as e:
        return {"check": "vendor_count", "status": "error", "message": str(e)}


def send_telegram_alert(results: List[Dict[str, Any]]):
    """Send Telegram alert for any non-ok results."""
    alerts = [r for r in results if r["status"] not in ("ok",)]
    if not alerts:
        return

    lines = ["NEXUS HEALTH ALERT", ""]
    for a in alerts:
        icon = "!!" if a["status"] == "critical" else "!"
        detail = ""
        if a["check"] == "ram":
            detail = f"RAM {a.get('used_pct', '?')}% used"
        elif a["check"] == "disk":
            detail = f"Disk {a.get('free_gb', '?')}GB free"
        elif a["check"] == "db":
            detail = a.get("message", f"DB {a.get('size_mb', '?')}MB, freelist {a.get('freelist_pct', '?')}%")
        elif a["check"] == "server":
            detail = a.get("message", "Server issue")
        elif a["check"] == "vendor_count":
            detail = f"Vendors increased by {a.get('increase', '?')} (now {a.get('current', '?')})"
        lines.append(f"{icon} {a['check']}: {detail}")

    message = "\n".join(lines)

    try:
        config = json.loads(CONFIG_PATH.read_text())
        token = config.get("telegram_bot_token", "")
        chat_id = config.get("telegram_chat_id", "8540603351")
        if not token:
            log.warning("No Telegram bot token — cannot send alert")
            return

        import urllib.request
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = json.dumps({"chat_id": chat_id, "text": message}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        log.info("Telegram alert sent")
    except Exception as e:
        log.error("Failed to send Telegram alert: %s", e)


def main():
    parser = argparse.ArgumentParser(description="Nexus system health check")
    parser.add_argument("--alert", action="store_true", help="Send Telegram alert on warnings/critical")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    results = [
        check_ram(),
        check_disk(),
        check_db(),
        check_server(),
        check_vendor_count(),
    ]

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print("\n" + "=" * 50)
        print("  NEXUS SYSTEM HEALTH CHECK")
        print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S PT')}")
        print("=" * 50)

        for r in results:
            icon = {"ok": "OK", "warning": "WARN", "critical": "CRIT", "error": "ERR"}.get(r["status"], "?")
            print(f"\n  [{icon}] {r['check'].upper()}")
            for k, v in r.items():
                if k not in ("check", "status"):
                    print(f"        {k}: {v}")

        overall = "HEALTHY" if all(r["status"] == "ok" for r in results) else "ISSUES DETECTED"
        print(f"\n  Overall: {overall}")
        print("=" * 50)

    if args.alert:
        send_telegram_alert(results)


if __name__ == "__main__":
    main()
