#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from urllib.parse import parse_qs

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import voice_control

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [voice_control] %(levelname)s %(message)s",
)
log = logging.getLogger("voice_control")

app = FastAPI(title="Nexus Voice Control", docs_url=None, redoc_url=None, openapi_url=None)


def _query_mapping(request: Request) -> dict[str, list[str]]:
    return parse_qs(str(request.url.query or ""), keep_blank_values=True)


@app.api_route("/healthz", methods=["GET", "POST"])
@app.api_route("/voice/healthz", methods=["GET", "POST"])
@app.api_route("/voice/stream-config", methods=["GET", "POST"])
@app.api_route("/voice/stream/healthz", methods=["GET", "POST"])
@app.api_route("/voice/stream-twiml", methods=["GET", "POST"])
@app.api_route("/voice/conversationrelay-twiml", methods=["GET", "POST"])
@app.api_route("/voice/conversationrelay/healthz", methods=["GET", "POST"])
@app.api_route("/voice/welcome", methods=["GET", "POST"])
@app.api_route("/voice/outbound", methods=["GET", "POST"])
@app.api_route("/voice/instructions", methods=["GET", "POST"])
@app.api_route("/voice/status", methods=["GET", "POST"])
async def dispatch_voice_http(request: Request) -> Response:
    body = await request.body()
    form = voice_control.parse_form_body(body)
    status, content_type, response_body = voice_control.dispatch_http_request(
        request.method,
        request.url.path,
        _query_mapping(request),
        form,
    )
    return Response(
        content=response_body,
        status_code=status,
        headers={"Content-Type": content_type},
    )


@app.websocket("/voice/stream")
async def voice_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    call_sid = str(websocket.query_params.get("call_sid") or "").strip() or "stream-session"
    phone = str(websocket.query_params.get("phone") or "").strip()
    session = voice_control.stream_session_state(call_sid, phone)

    if session.phone and not voice_control.sms_control.is_authorized_voice_phone(session.phone):
        await websocket.send_text(
            json.dumps(
                {
                    "type": "error",
                    "code": "unauthorized",
                    "message": "owner-only voice stream",
                }
            )
        )
        await websocket.close(code=1008)
        return

    try:
        while True:
            envelope = await websocket.receive()
            if envelope.get("type") == "websocket.disconnect":
                return

            raw = ""
            if envelope.get("text") is not None:
                raw = str(envelope.get("text") or "")
            elif envelope.get("bytes") is not None:
                raw = bytes(envelope.get("bytes") or b"").decode("utf-8", errors="replace")

            frames = voice_control.handle_stream_message(session, raw)
            for frame in frames:
                await websocket.send_text(json.dumps(frame))
                if str(frame.get("type") or "").lower() == "hangup":
                    await websocket.close(code=1000)
                    return
    except WebSocketDisconnect:
        return
    except Exception as exc:
        log.warning("voice stream websocket error: %s", exc)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass


@app.websocket("/voice/conversationrelay")
async def voice_conversation_relay(websocket: WebSocket) -> None:
    await websocket.accept()
    call_sid = str(websocket.query_params.get("call_sid") or "").strip() or "conversationrelay-session"
    phone = str(websocket.query_params.get("phone") or "").strip()
    session = voice_control.stream_session_state(call_sid, phone)

    if session.phone and not voice_control.sms_control.is_authorized_voice_phone(session.phone):
        await websocket.send_text(
            json.dumps(
                {
                    "type": "text",
                    "token": "This voice control line only accepts your owner control number.",
                    "last": True,
                }
            )
        )
        await websocket.send_text(json.dumps({"type": "end"}))
        await websocket.close(code=1008)
        return

    try:
        while True:
            envelope = await websocket.receive()
            if envelope.get("type") == "websocket.disconnect":
                return

            raw = ""
            if envelope.get("text") is not None:
                raw = str(envelope.get("text") or "")
            elif envelope.get("bytes") is not None:
                raw = bytes(envelope.get("bytes") or b"").decode("utf-8", errors="replace")

            frames = voice_control.handle_conversation_relay_message(session, raw)
            for frame in frames:
                await websocket.send_text(json.dumps(frame))
                if str(frame.get("type") or "").lower() == "end":
                    await websocket.close(code=1000)
                    return
    except WebSocketDisconnect:
        return
    except Exception as exc:
        log.warning("conversation relay websocket error: %s", exc)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass


def main() -> int:
    port = voice_control.load_voice_port()
    log.info(
        "voice control server listening on %s (public base: %s, stream: %s)",
        port,
        voice_control.sms_control.load_voice_public_base_url() or "<unset>",
        voice_control.load_voice_stream_public_url() or voice_control._local_stream_url(),
    )
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info",
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
