"""
Slack Notification — Secondary channel for lead alerts.

Uses Slack Incoming Webhook (free, no app install needed).
Setup: Create webhook at https://api.slack.com/messaging/webhooks

Config key in ~/.nexus/config.json:
  "slack_webhook_url": "https://slack.invalid/services/example-webhook"
"""
from __future__ import annotations
import httpx
from pathlib import Path
import json

CONFIG_FILE = Path.home() / ".nexus" / "config.json"


def _get_webhook_url() -> str:
    """Read Slack webhook URL from config."""
    try:
        cfg = json.loads(CONFIG_FILE.read_text())
        return cfg.get("slack_webhook_url", "")
    except Exception:
        return ""


async def send_slack(text: str, channel: str = None) -> bool:
    """Send a message to Slack via incoming webhook. Returns True if sent."""
    url = _get_webhook_url()
    if not url:
        return False

    payload = {"text": text}
    if channel:
        payload["channel"] = channel

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(url, json=payload)
            return r.status_code == 200
    except Exception as e:
        print(f"[Slack] Send error: {e}")
        return False


async def send_slack_rich(blocks: list, text: str = "") -> bool:
    """Send a rich message with Slack Block Kit."""
    url = _get_webhook_url()
    if not url:
        return False

    payload = {"blocks": blocks}
    if text:
        payload["text"] = text  # Fallback text for notifications

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(url, json=payload)
            return r.status_code == 200
    except Exception as e:
        print(f"[Slack] Rich send error: {e}")
        return False
