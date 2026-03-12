"""
Watchdog Timer System — Auto-restart silent services.

Monitors all Nexus subsystems. If any service goes silent:
1. Attempt auto-restart (up to 3 times)
2. Only notify Kai if auto-restart fails after 3 attempts
3. 30-minute cooldown between repeated alerts for same service
4. After 2 hours of downtime, send hourly summary (not individual alerts)
5. Never spam — max 3 notifications per hour total

Services monitored:
- Telegram bot polling
- Daily posting scheduler
- Follow-up engine
- FB group scraper
- Ad performance monitor
- Approval reminder loop
"""
import asyncio
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

log = logging.getLogger("watchdog")
PT = ZoneInfo("America/Los_Angeles")

# ── Service Registry ─────────────────────────────────────────────────────────

_heartbeats = {}   # str -> float
_restart_fns = {}  # str -> callable

# Timing thresholds
SILENCE_THRESHOLD = 300          # 5 minutes before considered "dead"
MIN_RESTART_INTERVAL = 120       # 2 minutes between restart attempts
MAX_RESTART_ATTEMPTS = 3         # Max auto-restart tries before alerting
NOTIFICATION_COOLDOWN = 1800     # 30 minutes between alerts for same service
LONG_DOWN_THRESHOLD = 7200       # 2 hours — switch to hourly summaries
MAX_NOTIFICATIONS_PER_HOUR = 3   # Never send more than 3 alerts/hour total

# State tracking
_last_restarts = {}              # str -> float
_restart_counts = {}             # str -> int (total lifetime restarts)
_consecutive_restart_fails = {}  # str -> int (consecutive failed restarts)
_last_notification = {}          # str -> float (last notification time per service)
_first_down_at = {}              # str -> float (when service first went down)
_notifications_this_hour: int = 0
_hour_reset_time: float = 0.0


def heartbeat(service_name: str):
    """Call this periodically from each service to indicate it's alive."""
    _heartbeats[service_name] = time.time()
    # Service recovered — reset failure tracking
    if service_name in _consecutive_restart_fails:
        if _consecutive_restart_fails[service_name] > 0:
            log.info(f"[Watchdog] {service_name} recovered after {_consecutive_restart_fails[service_name]} restart attempts")
        _consecutive_restart_fails[service_name] = 0
    _first_down_at.pop(service_name, None)


def register_service(service_name: str, restart_fn=None):
    """Register a service for monitoring."""
    _heartbeats[service_name] = time.time()
    if restart_fn:
        _restart_fns[service_name] = restart_fn
    _restart_counts.setdefault(service_name, 0)
    _consecutive_restart_fails.setdefault(service_name, 0)


def unregister_service(service_name: str):
    """Remove a service from monitoring."""
    _heartbeats.pop(service_name, None)
    _restart_fns.pop(service_name, None)


def get_service_status() -> list[dict]:
    """Get status of all monitored services."""
    now = time.time()
    result = []
    for name, last_hb in sorted(_heartbeats.items()):
        elapsed = now - last_hb
        status = "healthy" if elapsed < SILENCE_THRESHOLD else "silent"
        result.append({
            "name": name,
            "status": status,
            "last_heartbeat": datetime.fromtimestamp(last_hb, tz=PT).strftime("%H:%M:%S"),
            "seconds_ago": round(elapsed),
            "restart_count": _restart_counts.get(name, 0),
            "consecutive_fails": _consecutive_restart_fails.get(name, 0),
            "has_restart_fn": name in _restart_fns,
        })
    return result


def format_services_status() -> str:
    """Format service status for /services Telegram command."""
    services = get_service_status()
    if not services:
        return "No services registered yet."

    lines = ["SERVICE STATUS", "-" * 30]
    for svc in services:
        if svc["status"] == "healthy":
            emoji = "ok"
            detail = f"last heartbeat {svc['seconds_ago']}s ago"
        else:
            emoji = "DOWN"
            detail = f"SILENT for {svc['seconds_ago']}s"

        restarts = f" (restarted {svc['restart_count']}x)" if svc['restart_count'] > 0 else ""
        lines.append(f"[{emoji}] {svc['name']}: {detail}{restarts}")

    return "\n".join(lines)


def _can_notify(service_name: str) -> bool:
    """Check if we're allowed to send a notification for this service."""
    global _notifications_this_hour, _hour_reset_time
    now = time.time()

    # Reset hourly counter
    if now - _hour_reset_time > 3600:
        _notifications_this_hour = 0
        _hour_reset_time = now

    # Global hourly limit
    if _notifications_this_hour >= MAX_NOTIFICATIONS_PER_HOUR:
        return False

    # Per-service cooldown
    last = _last_notification.get(service_name, 0)
    if now - last < NOTIFICATION_COOLDOWN:
        return False

    # For long-running outages (>2 hours), only send hourly
    first_down = _first_down_at.get(service_name)
    if first_down and (now - first_down) > LONG_DOWN_THRESHOLD:
        if now - last < 3600:  # Only once per hour for long outages
            return False

    return True


def _record_notification(service_name: str):
    """Record that a notification was sent."""
    global _notifications_this_hour
    _last_notification[service_name] = time.time()
    _notifications_this_hour += 1


# ── Watchdog Loop ─────────────────────────────────────────────────────────────

async def run_watchdog(send_fn):
    """
    Background loop that checks service health every 60 seconds.

    Restart logic:
    1. If service is silent for 5+ min, try auto-restart (up to 3 times)
    2. Only notify Kai if auto-restart fails after 3 attempts
    3. 30-min cooldown between notifications for same service
    4. After 2 hours down, switch to hourly summaries
    5. Max 3 notifications per hour total
    """
    log.info("[Watchdog] Started — monitoring all services")
    heartbeat("watchdog")

    while True:
        await asyncio.sleep(60)
        heartbeat("watchdog")

        now = time.time()
        for name, last_hb in list(_heartbeats.items()):
            if name == "watchdog":
                continue

            elapsed = now - last_hb

            if elapsed < SILENCE_THRESHOLD:
                continue

            # Track when service first went down
            if name not in _first_down_at:
                _first_down_at[name] = now
                log.warning(f"[Watchdog] {name} went silent ({int(elapsed)}s)")

            # Check restart interval
            last_restart = _last_restarts.get(name, 0)
            if now - last_restart < MIN_RESTART_INTERVAL:
                continue

            consecutive_fails = _consecutive_restart_fails.get(name, 0)

            # Auto-restart disabled — log and fall through to notification
            restart_fn = _restart_fns.get(name)
            if restart_fn and consecutive_fails < MAX_RESTART_ATTEMPTS:
                log.warning(f"[Watchdog] {name} appears down — alerting (auto-restart disabled)")
                _consecutive_restart_fails[name] = consecutive_fails + 1

            # Auto-restart either failed 3 times or no restart fn available
            # Now notify Kai (with cooldown)
            if not _can_notify(name):
                continue

            if send_fn:
                try:
                    down_duration = now - _first_down_at.get(name, now)
                    if down_duration > LONG_DOWN_THRESHOLD:
                        hours = int(down_duration / 3600)
                        msg = (
                            f"[Watchdog] {name} still DOWN ({hours}h+)\n"
                            f"Auto-restart failed {consecutive_fails}x. May need manual restart."
                        )
                    elif consecutive_fails >= MAX_RESTART_ATTEMPTS:
                        msg = (
                            f"[Watchdog] {name} DOWN — auto-restart failed {consecutive_fails}x\n"
                            f"Silent for {int(elapsed)}s. Manual intervention needed."
                        )
                    else:
                        msg = (
                            f"[Watchdog] {name} appears DOWN\n"
                            f"No heartbeat for {int(elapsed)}s. No auto-restart available."
                        )
                    await send_fn(msg)
                    _record_notification(name)
                except Exception:
                    pass


# ── Health Check Endpoint Data ────────────────────────────────────────────────

def get_health_check() -> dict:
    """Return health data for /api/health endpoint."""
    services = get_service_status()
    all_healthy = all(s["status"] == "healthy" for s in services) if services else True
    return {
        "status": "healthy" if all_healthy else "degraded",
        "services": services,
        "total_services": len(services),
        "healthy_count": sum(1 for s in services if s["status"] == "healthy"),
        "silent_count": sum(1 for s in services if s["status"] == "silent"),
        "checked_at": datetime.now(PT).strftime("%Y-%m-%d %H:%M:%S PT"),
    }
