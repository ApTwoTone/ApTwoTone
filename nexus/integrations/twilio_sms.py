from __future__ import annotations
"""
Twilio SMS Fallback
- Simple REST API via httpx (no Twilio SDK needed)
- Falls back from Google Voice when GV automation fails
- Supports sending and checking for inbound replies via polling
"""
import httpx, asyncio
from datetime import datetime, timedelta

_twilio = None


class TwilioSMS:
    def __init__(self, account_sid: str, auth_token: str, from_number: str):
        self.sid = account_sid
        self.token = auth_token
        self.from_number = from_number
        self.base_url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}"
        self._seen_sids: set = set()  # Track message SIDs we've already processed

    async def send_sms(self, to_phone: str, message: str, approval_id: int = None) -> dict:
        """
        Send SMS via Twilio Messages API.
        Returns {"ok": True/False, "sid": "...", "error": "..."}
        """
        from integrations.messaging import outbound_gate
        gate = outbound_gate("sms", to_phone, "", message,
                             approval_id=approval_id, code_path="TwilioSMS.send_sms")
        if not gate["ok"]:
            return {"ok": False, "error": f"BLOCKED: {gate['reason']}", "method": "blocked"}
        digits = _clean_phone(to_phone)
        if len(digits) != 10:
            return {"ok": False, "error": f"Invalid phone: need 10 digits, got {len(digits)}"}

        to_formatted = f"+1{digits}"
        url = f"{self.base_url}/Messages.json"

        async with httpx.AsyncClient(timeout=15) as client:
            try:
                resp = await client.post(
                    url,
                    auth=(self.sid, self.token),
                    data={"To": to_formatted, "From": self.from_number, "Body": message}
                )
                if resp.status_code in (200, 201):
                    data = resp.json()
                    sid = data.get("sid", "")
                    self._seen_sids.add(sid)
                    print(f"[Twilio] SMS sent to {digits} (SID: {sid})")
                    return {"ok": True, "sid": sid, "method": "twilio"}
                else:
                    error = f"Twilio {resp.status_code}: {resp.text[:200]}"
                    print(f"[Twilio] Send failed: {error}")
                    return {"ok": False, "error": error}
            except Exception as e:
                return {"ok": False, "error": str(e)}

    async def check_replies(self, since_minutes: int = 5) -> list[dict]:
        """
        GET recent inbound messages from Twilio API.
        Returns list of {from_phone, message, timestamp, sid} for new inbound messages.
        """
        url = f"{self.base_url}/Messages.json"
        since = (datetime.utcnow() - timedelta(minutes=since_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")

        async with httpx.AsyncClient(timeout=15) as client:
            try:
                resp = await client.get(
                    url,
                    auth=(self.sid, self.token),
                    params={
                        "To": self.from_number,
                        "DateSent>": since,
                        "PageSize": 20,
                    }
                )
                if resp.status_code != 200:
                    return []

                messages = resp.json().get("messages", [])
                new_replies = []
                for m in messages:
                    if m.get("direction") != "inbound":
                        continue
                    sid = m.get("sid", "")
                    if sid in self._seen_sids:
                        continue
                    self._seen_sids.add(sid)
                    new_replies.append({
                        "from_phone": m.get("from", ""),
                        "message": m.get("body", ""),
                        "timestamp": m.get("date_sent", ""),
                        "sid": sid,
                    })

                if new_replies:
                    print(f"[Twilio] Found {len(new_replies)} new inbound messages")
                return new_replies

            except Exception as e:
                print(f"[Twilio] Check replies error: {e}")
                return []

    async def place_brief_call(self, to_phone: str, ring_seconds: int = 8) -> dict:
        """
        Place a short outbound call attempt (ring-only intent).
        Useful for showing immediate contact attempt after an inbound lead form.
        """
        digits = _clean_phone(to_phone)
        if len(digits) != 10:
            return {"ok": False, "error": f"Invalid phone: need 10 digits, got {len(digits)}"}

        to_formatted = f"+1{digits}"
        url = f"{self.base_url}/Calls.json"
        # TwiML: very short pause then hangup if call is answered.
        twiml_url = (
            "https://twimlets.com/echo?"
            "Twiml=%3CResponse%3E%3CPause%20length%3D1%20/%3E%3CHangup/%3E%3C/Response%3E"
        )
        timeout = max(6, min(int(ring_seconds or 8), 20))

        async with httpx.AsyncClient(timeout=15) as client:
            try:
                resp = await client.post(
                    url,
                    auth=(self.sid, self.token),
                    data={
                        "To": to_formatted,
                        "From": self.from_number,
                        "Url": twiml_url,
                        "Method": "POST",
                        "Timeout": str(timeout),
                    },
                )
                if resp.status_code in (200, 201):
                    data = resp.json()
                    call_sid = data.get("sid", "")
                    print(f"[Twilio] Brief call attempt placed to {digits} (SID: {call_sid})")
                    return {"ok": True, "sid": call_sid, "method": "twilio_call", "timeout": timeout}
                return {"ok": False, "error": f"Twilio {resp.status_code}: {resp.text[:200]}"}
            except Exception as e:
                return {"ok": False, "error": str(e)}


def _clean_phone(p: str) -> str:
    d = "".join(c for c in str(p) if c.isdigit())
    if len(d) == 11 and d[0] == "1":
        d = d[1:]
    return d


# ── Module-level init ─────────────────────────────────────────────────────────

def init_twilio(config: dict):
    """
    Initialize Twilio from config.
    Returns TwilioSMS instance or None if creds missing.
    Config keys: twilio_sid, twilio_token, twilio_from
    """
    global _twilio
    sid = config.get("twilio_sid", "")
    token = config.get("twilio_token", "")
    from_num = config.get("twilio_from", "")
    if sid and token and from_num:
        _twilio = TwilioSMS(sid, token, from_num)
        print(f"[Twilio] Initialized (from: {from_num})")
        return _twilio
    return None


def get_twilio() -> TwilioSMS | None:
    return _twilio
