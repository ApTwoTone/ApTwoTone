# Voice AI Setup

This repo now has two outbound voice paths:

- `google_voice`: browser automation only. It can place an alert/ring attempt from the signed-in Google Voice tab, but it does not support a full conversational AI audio loop.
- `twilio`: real outbound telephony. This is the supported path for an actual phone conversation with speech recognition and spoken responses.

## Reality Check

Google Voice is not the right transport for a real AI phone bot here. The Nexus Google Voice integration can automate the web UI, but it cannot stream call audio into the app or run a fast bidirectional speech loop. Use Twilio for that.

## What Is Already Wired

- `scripts/voice_control_server.py`
  - `/voice/outbound`
  - `/voice/instructions`
  - `/voice/stream`
  - `/voice/conversationrelay`
  - `/voice/status`
- `core/voice_control.py`
  - stateful TwiML Gather fallback
  - Twilio ConversationRelay websocket transport
  - owner-number restriction
  - call event logging for Twilio status callbacks
- `core/sms_control.py`
  - outbound owner-only call trigger
  - Twilio call creation with status callbacks

## Required Config

Add these to `~/.nexus/config.json`:

```json
{
  "twilio_sid": "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "twilio_token": "your_twilio_auth_token",
  "twilio_from": "+1xxxxxxxxxx",
  "voice_control_transport": "conversation_relay",
  "voice_control_allowed_numbers": ["8184489055"]
}
```

Optional voice quality settings:

```json
{
  "voice_control_tts_provider": "Google",
  "voice_control_tts_voice": "en-US-Journey-O",
  "voice_control_language": "en-US",
  "voice_control_transcription_provider": "Deepgram",
  "voice_control_speech_model": "nova-3"
}
```

If you want the older speech-recognition loop instead of ConversationRelay:

```json
{
  "voice_control_transport": "gather"
}
```

## Twilio Console

Point the Twilio voice webhook for the Twilio number at:

```text
https://crm.zoarbathroomrental.com/voice/outbound
```

Status callback URL:

```text
https://crm.zoarbathroomrental.com/voice/status
```

## Local Verification

Voice server health:

```bash
curl -sS http://127.0.0.1:8790/voice/healthz
```

ConversationRelay TwiML preview:

```bash
curl -sS "http://127.0.0.1:8790/voice/conversationrelay-twiml?phone=8184489055&call_sid=CApreview"
```

Gather fallback TwiML preview:

```bash
curl -sS "http://127.0.0.1:8790/voice/outbound?phone=8184489055&message=Test"
```

## Current Limitation

Until Twilio credentials and a Twilio voice-capable number are added, Nexus cannot place a real conversational outbound call. The Google Voice path remains alert-call only.
