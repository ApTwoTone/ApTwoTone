#!/usr/bin/env python3
"""
Standalone Telegram notification script for Claude sessions.

Usage:
    python3 ~/nexus/scripts/notify_telegram.py "Task done: fixed auth middleware"
    python3 ~/nexus/scripts/notify_telegram.py --session wt-1 "3 files changed, tests pass"

Reads telegram_token and telegram_chat_ids from ~/.nexus/config.json.
"""
import asyncio
import sys
import json
import argparse
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.telegram_policy import should_send_notification

CONFIG = Path.home() / ".nexus" / "config.json"


def load_config():
    if CONFIG.exists():
        return json.loads(CONFIG.read_text())
    return {}


def send_telegram(
    token: str,
    chat_id: str,
    text: str,
    *,
    category: str = None,
    source: str = "scripts.notify_telegram",
    force: bool = False,
):
    decision = should_send_notification(
        text,
        source=source,
        category=category,
        force=force,
        recipient=chat_id,
    )
    if not decision.allowed:
        return {"ok": True, "suppressed": True, "reason": decision.reason, "category": decision.category}

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }).encode()
    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def main():
    parser = argparse.ArgumentParser(description="Send task notification to Telegram")
    parser.add_argument("message", help="Notification message")
    parser.add_argument("--session", default="main", help="Session identifier (e.g. wt-1)")
    parser.add_argument("--category", choices=["lead", "booking", "other"], default=None)
    parser.add_argument("--force", action="store_true", help="Bypass notification filtering and dedup")
    args = parser.parse_args()

    cfg = load_config()
    token = cfg.get("telegram_token", "")
    chat_ids = cfg.get("telegram_chat_ids", [])

    if not token or not chat_ids:
        print("Error: telegram_token or telegram_chat_ids not set in ~/.nexus/config.json")
        sys.exit(1)

    text = args.message
    if args.session != "main":
        text = (
            f"*Task Complete* \\[Session: {args.session}\\]\n\n"
            f"{args.message}\n\n"
            f"_Reply with next task for this session, or /skip_"
        )

    for cid in chat_ids:
        try:
            result = send_telegram(
                token,
                str(cid),
                text,
                category=args.category,
                source=f"scripts.notify_telegram:{args.session}",
                force=args.force,
            )
            if result.get("suppressed"):
                print(f"Suppressed for chat {cid}: {result.get('reason')} ({result.get('category')})")
            else:
                print(f"Sent to chat {cid}")
        except Exception as e:
            print(f"Failed to send to {cid}: {e}")


async def send_message(message: str, *, category: str = None, session: str = "main", force: bool = False):
    cfg = load_config()
    token = cfg.get("telegram_token", "")
    chat_ids = cfg.get("telegram_chat_ids", [])

    if not token or not chat_ids:
        return {"ok": False, "error": "telegram_token or telegram_chat_ids not configured"}

    text = message
    if session != "main":
        text = (
            f"*Task Complete* \\[Session: {session}\\]\n\n"
            f"{message}\n\n"
            f"_Reply with next task for this session, or /skip_"
        )

    last_result = None
    for cid in chat_ids:
        last_result = send_telegram(
            token,
            str(cid),
            text,
            category=category,
            source=f"scripts.notify_telegram:{session}",
            force=force,
        )
    return last_result or {"ok": True, "suppressed": True}


def send_message_sync(message: str, **kwargs):
    return asyncio.run(send_message(message, **kwargs))


if __name__ == "__main__":
    main()
