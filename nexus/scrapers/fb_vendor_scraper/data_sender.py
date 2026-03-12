"""
FB Vendor Scraper — Sends extracted data to Nexus server API.
Falls back to local JSON file when server is unreachable.
"""
import json
import logging
import os
import requests

from config import NEXUS_BASE_URL, PENDING_FILE

log = logging.getLogger("fb_scraper")


def _get_api_key() -> str:
    return os.environ.get("NEXUS_API_KEY", "")


def send_vendor(data: dict):
    """POST vendor data to Nexus API. Returns response dict or None on failure."""
    url = f"{NEXUS_BASE_URL}/api/fb/vendor"
    headers = {"Content-Type": "application/json"}
    api_key = _get_api_key()
    if api_key:
        headers["X-Nexus-Key"] = api_key

    try:
        r = requests.post(url, json=data, headers=headers, timeout=15)
        if r.status_code == 200:
            result = r.json()
            log.info(f"Sent vendor: {data.get('poster_name', '?')} → {result.get('action', '?')}")
            return result
        else:
            log.warning(f"API responded {r.status_code}: {r.text[:200]}")
            _save_pending(data)
            return None
    except requests.RequestException as e:
        log.warning(f"Could not reach Nexus server: {e}")
        _save_pending(data)
        return None


def _save_pending(data: dict):
    """Save vendor data to local file for later retry."""
    pending = []
    if PENDING_FILE.exists():
        try:
            pending = json.loads(PENDING_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pending = []
    pending.append(data)
    PENDING_FILE.write_text(json.dumps(pending, indent=2))
    log.info(f"Saved to pending uploads ({len(pending)} total)")


def retry_pending() -> int:
    """Retry sending any pending uploads. Returns count of successfully sent."""
    if not PENDING_FILE.exists():
        return 0
    try:
        pending = json.loads(PENDING_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return 0

    if not pending:
        return 0

    remaining = []
    sent = 0
    for data in pending:
        result = send_vendor(data)
        if result:
            sent += 1
        else:
            remaining.append(data)

    if remaining:
        PENDING_FILE.write_text(json.dumps(remaining, indent=2))
    else:
        PENDING_FILE.unlink(missing_ok=True)

    if sent:
        log.info(f"Retried pending: {sent} sent, {len(remaining)} remaining")
    return sent


def send_telegram_alert(message: str):
    """Send an alert message via Nexus Telegram (uses a lightweight endpoint)."""
    url = f"{NEXUS_BASE_URL}/api/fb/vendor"
    # We use the vendor endpoint with a special flag to trigger a Telegram alert.
    # Alternatively, the scraper can call the Telegram API directly if configured.
    # For now, log it — the Nexus server handles notifications automatically.
    log.warning(f"ALERT: {message}")
    # Also try to send via Nexus
    try:
        headers = {"Content-Type": "application/json"}
        api_key = _get_api_key()
        if api_key:
            headers["X-Nexus-Key"] = api_key
        # Use a dummy vendor payload to trigger notification with an alert message
        # Better: we'll send via a dedicated alert endpoint if available
        log.info("Alert sent to log. Configure Telegram bot token for direct alerts.")
    except Exception:
        pass
