#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from pathlib import Path
from typing import Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.sms_control import (
    _place_voice_call,
    claim_pending_voice_call_request,
    complete_voice_call_request,
    create_system_sms_approval,
    format_help,
    get_orchestrator_idle_prompt,
    get_pending_brain_completion_notifications,
    get_pending_completion_notifications,
    get_pending_critical_voice_alerts,
    get_pending_operator_event_notifications,
    handle_inbound_sms,
    is_authorized_phone,
    is_voice_call_configured,
    load_allowed_numbers,
    mark_operator_event_sms_notified,
    mark_operator_event_voice_notified,
    mark_brain_task_notification_sent,
    mark_orchestrator_prompt_sent,
    mark_task_notification_sent,
    normalize_phone,
)
from integrations.google_voice import GVReplyPoller, GoogleVoiceSMS, init_google_voice

CONFIG_PATH = Path.home() / ".nexus" / "config.json"
POLL_NOTIFICATIONS_SECONDS = 20
POLL_VOICE_CALLS_SECONDS = 3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [sms_control] %(levelname)s %(message)s",
)
log = logging.getLogger("sms_control")


def _load_config() -> Dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def _clean_sms_text(text: str) -> str:
    cleaned = str(text or "")
    cleaned = cleaned.replace("*", "")
    cleaned = cleaned.replace("`", "")
    cleaned = cleaned.replace("_", "")
    cleaned = cleaned.replace("•", "-")
    return "\n".join(line.strip() for line in cleaned.splitlines()).strip()


class SMSControlDaemon:
    def __init__(self) -> None:
        self.config = _load_config()
        self.gv = None  # type: Optional[GoogleVoiceSMS]
        self.poller = None  # type: Optional[GVReplyPoller]
        self._running = True

    async def start(self) -> bool:
        if self.config.get("disable_sms_control_daemon", False):
            log.info("SMS control daemon disabled in config")
            return False

        gv, poller, err = await init_google_voice(self.config)
        if err:
            log.warning("Google Voice init warning: %s", err)
        if not gv:
            return False

        self.gv = gv
        self.poller = poller
        if self.poller:
            self.poller.on_reply(self._handle_reply)

        allowed = load_allowed_numbers()
        log.info("SMS control authorized numbers: %s", allowed or ["<none>"])
        log.info("SMS control commands: HELP, STATUS, TASK <id>, TASK B#<id>, APPROVE <ref>, REJECT <ref>, RUN:, ORCH:, CODEX:, ADS:, LEADS:")
        return True

    async def send_sms(self, phone: str, text: str) -> bool:
        if not self.gv:
            return False

        clean_phone = normalize_phone(phone)
        clean_text = _clean_sms_text(text)
        approval_id = create_system_sms_approval(clean_phone, clean_text)
        result = await self.gv.send_sms(clean_phone, clean_text, approval_id=approval_id)
        if not result.get("ok"):
            log.warning("SMS send failed to %s: %s", phone, result.get("error", "unknown"))
            return False
        return True

    async def _handle_reply(self, phone: str, message: str, timestamp: str, name: str) -> None:
        normalized_phone = normalize_phone(phone)
        if not is_authorized_phone(normalized_phone):
            return

        log.info("Inbound SMS control message from %s: %s", normalized_phone, (message or "")[:120])
        response = handle_inbound_sms(normalized_phone, message, source_name=name or "")
        if response:
            await self.send_sms(normalized_phone, response)

    async def _send_startup_help(self) -> None:
        for phone in load_allowed_numbers():
            await self.send_sms(phone, format_help())

    async def _notification_loop(self) -> None:
        while self._running:
            try:
                for phone in load_allowed_numbers():
                    sent_any = False
                    messages = get_pending_completion_notifications(phone)
                    for msg in messages:
                        sent = await self.send_sms(phone, msg)
                        if not sent:
                            break
                        sent_any = True
                        first_line = msg.splitlines()[0]
                        parts = first_line.split()
                        if len(parts) >= 2 and parts[1].startswith("#"):
                            try:
                                task_id = int(parts[1][1:])
                                status_word = parts[2].rstrip(".") if len(parts) >= 3 else ""
                                mark_task_notification_sent(phone, task_id, {
                                    "done": "done",
                                    "failed": "failed",
                                    "blocked": "blocked",
                                    "needs": "review_needed",
                                }.get(status_word, status_word))
                            except ValueError:
                                pass

                    brain_messages = get_pending_brain_completion_notifications(phone)
                    for item in brain_messages:
                        sent = await self.send_sms(phone, item["message"])
                        if not sent:
                            break
                        sent_any = True
                        mark_brain_task_notification_sent(phone, int(item["task_id"]), str(item["status"]))

                    operator_messages = get_pending_operator_event_notifications(phone)
                    for item in operator_messages:
                        sent = await self.send_sms(phone, item["message"])
                        if not sent:
                            break
                        sent_any = True
                        mark_operator_event_sms_notified(int(item["event_id"]))

                    critical_voice_alerts = get_pending_critical_voice_alerts(phone)
                    if is_voice_call_configured():
                        for item in critical_voice_alerts:
                            ok, note = _place_voice_call(phone, item["message"])
                            if ok:
                                mark_operator_event_voice_notified(int(item["event_id"]))
                                log.info("Critical voice alert placed to %s: %s", phone, item["message"][:120])
                            else:
                                log.warning("Critical voice alert failed for %s: %s", phone, note)

                    if not sent_any:
                        prompt = get_orchestrator_idle_prompt(phone)
                        if prompt:
                            sent = await self.send_sms(phone, prompt)
                            if sent:
                                mark_orchestrator_prompt_sent(phone)
                await asyncio.sleep(POLL_NOTIFICATIONS_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("Notification loop error: %s", e)
                await asyncio.sleep(POLL_NOTIFICATIONS_SECONDS)

    async def _voice_call_loop(self) -> None:
        while self._running:
            try:
                if not self.gv:
                    await asyncio.sleep(POLL_VOICE_CALLS_SECONDS)
                    continue

                item = claim_pending_voice_call_request()
                while item:
                    request_id = int(item.get("id") or 0)
                    phone = normalize_phone(str(item.get("phone") or ""))
                    message = str(item.get("message") or "")
                    result = await self.gv.place_call(phone, message)
                    ok = bool(result.get("ok"))
                    note = str(result.get("note") or result.get("error") or "Google Voice call finished.")
                    provider = str(result.get("method") or "google_voice")
                    complete_voice_call_request(request_id, ok, note, provider=provider)
                    if ok:
                        log.info("Queued Google Voice alert call placed to %s (request %s)", phone, request_id)
                    else:
                        log.warning("Queued Google Voice alert call failed for %s (request %s): %s", phone, request_id, note)
                    item = claim_pending_voice_call_request()

                await asyncio.sleep(POLL_VOICE_CALLS_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("Voice call loop error: %s", e)
                await asyncio.sleep(POLL_VOICE_CALLS_SECONDS)

    async def run(self) -> None:
        if not self.gv or not self.poller:
            raise RuntimeError("Daemon not started")

        notification_task = asyncio.create_task(self._notification_loop())
        poller_task = asyncio.create_task(self.poller.run())
        voice_call_task = asyncio.create_task(self._voice_call_loop())

        if self.config.get("sms_control_send_startup_help", False):
            await self._send_startup_help()

        try:
            while self._running:
                await asyncio.sleep(1)
        finally:
            notification_task.cancel()
            poller_task.cancel()
            voice_call_task.cancel()
            await asyncio.gather(notification_task, poller_task, voice_call_task, return_exceptions=True)

    def stop(self) -> None:
        self._running = False
        if self.poller:
            self.poller.stop()


async def _async_main() -> int:
    daemon = SMSControlDaemon()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, daemon.stop)
        except NotImplementedError:
            pass

    started = await daemon.start()
    if not started:
        return 1

    await daemon.run()
    return 0


def main() -> int:
    try:
        return asyncio.run(_async_main())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
