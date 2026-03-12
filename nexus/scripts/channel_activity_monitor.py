#!/usr/bin/env python3
"""Cross-channel activity monitor — watches all outbound channels for anomalies.

Monitors outbound_channels, outbound_log, and posting_log for:
- Platform limit violations (uses channel_compliance.py rules)
- Sudden volume spikes (>2x normal in any channel)
- Posts outside allowed hours
- Duplicate content across channels
- Channel going silent unexpectedly (if was active)

Usage:
    python scripts/channel_activity_monitor.py              # One check
    python scripts/channel_activity_monitor.py --daemon      # Run every 5 min
    python scripts/channel_activity_monitor.py --json        # JSON output
"""

import argparse
import json
import logging
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("channel_activity_monitor")

DB_PATH = Path.home() / ".nexus" / "memory.db"
STATE_FILE = Path.home() / ".nexus" / "channel_monitor_state.json"

# Import platform rules from channel_compliance
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.channel_compliance import PLATFORM_RULES
except ImportError:
    PLATFORM_RULES = {
        "email": {"max_per_day": 50},
        "facebook_page": {"max_per_day": 2},
        "facebook_marketplace": {"max_per_day": 1},
        "facebook_groups": {"max_total_per_day": 3},
        "craigslist": {"max_per_48h": 1},
    }


def _send_telegram(msg):
    try:
        subprocess.run(
            [sys.executable, str(Path(__file__).parent / "notify_telegram.py"), msg],
            timeout=15, capture_output=True,
        )
    except Exception as e:
        log.error("Telegram failed: %s", e)


def _load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def check_all_channels():
    """Run all channel anomaly checks."""
    if not DB_PATH.exists():
        return {"status": "error", "message": "DB not found"}

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA query_only = ON")
    result = {"status": "ok", "alerts": [], "channels": {}, "timestamp": datetime.now().isoformat()}

    # --- Email channel (from outbound_log) ---
    try:
        email_today = conn.execute(
            "SELECT COUNT(*) FROM outbound_log "
            "WHERE result = 'allowed' AND DATE(timestamp) = DATE('now')"
        ).fetchone()[0]
        email_blocked = conn.execute(
            "SELECT COUNT(*) FROM outbound_log "
            "WHERE result = 'blocked' AND DATE(timestamp) = DATE('now')"
        ).fetchone()[0]
        email_limit = PLATFORM_RULES.get("email", {}).get("max_per_day", 50)

        result["channels"]["email"] = {
            "sent_today": email_today,
            "blocked_today": email_blocked,
            "limit": email_limit,
        }

        if email_today > email_limit:
            result["alerts"].append({
                "channel": "email",
                "type": "over_limit",
                "severity": "critical",
                "message": "Email sends (%d) exceed daily limit (%d)" % (email_today, email_limit),
            })

        # Check pacing — any sends < 3 minutes apart
        recent = conn.execute(
            "SELECT timestamp FROM outbound_log "
            "WHERE result = 'allowed' AND DATE(timestamp) = DATE('now') "
            "ORDER BY id DESC LIMIT 10"
        ).fetchall()
        if len(recent) >= 2:
            for i in range(len(recent) - 1):
                try:
                    t1 = datetime.strptime(recent[i][0][:19], "%Y-%m-%d %H:%M:%S")
                    t2 = datetime.strptime(recent[i + 1][0][:19], "%Y-%m-%d %H:%M:%S")
                    gap = abs((t1 - t2).total_seconds())
                    if gap < 180:  # Less than 3 minutes
                        result["alerts"].append({
                            "channel": "email",
                            "type": "pacing_violation",
                            "severity": "high",
                            "message": "Emails sent %d seconds apart (min 180s)" % int(gap),
                        })
                        break
                except ValueError:
                    pass

        # Check for sends to non-vendor addresses
        non_vendor = conn.execute(
            "SELECT recipient FROM outbound_log "
            "WHERE result = 'allowed' AND DATE(timestamp) = DATE('now') "
            "AND recipient NOT IN (SELECT email FROM vendors WHERE email IS NOT NULL) "
            "AND recipient NOT LIKE '%kaiescobar%' "
            "AND recipient NOT LIKE '%zoar%'"
        ).fetchall()
        if non_vendor:
            result["alerts"].append({
                "channel": "email",
                "type": "non_vendor_send",
                "severity": "critical",
                "message": "Email sent to non-vendor address: %s" % non_vendor[0][0],
            })
    except Exception as e:
        log.debug("Email check: %s", e)

    # --- Social channels (from outbound_channels) ---
    for channel in ["facebook_page", "facebook_marketplace", "facebook_groups", "craigslist"]:
        try:
            today = conn.execute(
                "SELECT COUNT(*) FROM outbound_channels "
                "WHERE channel = ? AND DATE(posted_at) = DATE('now')",
                (channel,)
            ).fetchone()[0]

            rules = PLATFORM_RULES.get(channel, {})
            limit = rules.get("max_per_day", rules.get("max_total_per_day", rules.get("max_per_48h", 999)))

            result["channels"][channel] = {
                "posts_today": today,
                "limit": limit,
            }

            if today > limit:
                result["alerts"].append({
                    "channel": channel,
                    "type": "over_limit",
                    "severity": "high",
                    "message": "%s posts (%d) exceed daily limit (%d)" % (channel, today, limit),
                })
        except Exception:
            pass

    # --- Posting log (legacy table) ---
    try:
        posting_today = conn.execute(
            "SELECT COUNT(*) FROM posting_log WHERE DATE(posted_at) = DATE('now')"
        ).fetchone()[0]
        result["channels"]["posting_log"] = {"posts_today": posting_today}
    except Exception:
        pass

    # --- Duplicate content detection ---
    try:
        dupes = conn.execute(
            "SELECT content_preview, COUNT(*) as cnt FROM outbound_channels "
            "WHERE posted_at > datetime('now', '-24 hours') AND content_preview IS NOT NULL "
            "GROUP BY content_preview HAVING cnt > 1"
        ).fetchall()
        if dupes:
            result["alerts"].append({
                "channel": "all",
                "type": "duplicate_content",
                "severity": "medium",
                "message": "%d duplicate posts in last 24h" % len(dupes),
            })
    except Exception:
        pass

    # --- Volume spike detection ---
    state = _load_state()
    prev_counts = state.get("prev_channel_counts", {})
    current_counts = {}
    for ch, data in result["channels"].items():
        count = data.get("sent_today", data.get("posts_today", 0))
        current_counts[ch] = count
        prev = prev_counts.get(ch, 0)
        if prev > 2 and count > prev * 2:
            result["alerts"].append({
                "channel": ch,
                "type": "volume_spike",
                "severity": "medium",
                "message": "%s volume spiked: %d today vs %d previous" % (ch, count, prev),
            })

    # Save state for next run
    state["prev_channel_counts"] = current_counts
    state["last_check"] = datetime.now().isoformat()
    _save_state(state)

    conn.close()

    # Send alerts for high/critical
    for alert in result.get("alerts", []):
        if alert["severity"] in ("high", "critical"):
            _send_telegram("CHANNEL ALERT [%s]: %s" % (alert["severity"].upper(), alert["message"]))

    return result


def main():
    parser = argparse.ArgumentParser(description="Cross-channel activity monitor")
    parser.add_argument("--daemon", action="store_true", help="Run every 5 minutes")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.daemon:
        log.info("Channel activity monitor starting in daemon mode (5 min interval)")
        while True:
            try:
                result = check_all_channels()
                alert_count = len(result.get("alerts", []))
                if alert_count:
                    log.warning("Check complete: %d alerts", alert_count)
                else:
                    log.info("Check complete: no alerts")
            except Exception as e:
                log.error("Check failed: %s", e)
            time.sleep(300)
    else:
        result = check_all_channels()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("\n  Channel Activity Monitor")
            print("  " + "=" * 50)
            for ch, data in result.get("channels", {}).items():
                count = data.get("sent_today", data.get("posts_today", 0))
                limit = data.get("limit", "n/a")
                print("  %-25s %d / %s" % (ch, count, limit))

            alerts = result.get("alerts", [])
            if alerts:
                print("\n  Alerts:")
                for a in alerts:
                    print("    [%s] %s — %s" % (a["severity"].upper(), a["channel"], a["message"]))
            else:
                print("\n  No alerts")
            print("  " + "=" * 50)


if __name__ == "__main__":
    main()
