from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib import request as urlrequest
from urllib.error import URLError, HTTPError

from core.brain_models import (
    load_deployment_state,
    specialist_deployment_record,
    specialist_mode,
    specialist_names,
    specialist_trained_ready,
)

REGISTRY_PATH = Path.home() / ".nexus" / "brain_specialists.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(v: Any) -> str:
    try:
        return str(v if v is not None else "").strip()
    except Exception:
        return ""


class SpecialistRouter:
    def __init__(self, registry_path: Path = REGISTRY_PATH):
        self.registry_path = registry_path
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self._registry = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self.registry_path.exists():
            return {"updated_at": _now(), "specialists": {}}
        try:
            return json.loads(self.registry_path.read_text(encoding="utf-8"))
        except Exception:
            return {"updated_at": _now(), "specialists": {}}

    def _save(self) -> None:
        self._registry["updated_at"] = _now()
        self.registry_path.write_text(json.dumps(self._registry, ensure_ascii=False, indent=2), encoding="utf-8")

    def register(self, name: str, endpoint: str = "", version: str = "", deployed: bool = False) -> Dict[str, Any]:
        n = _safe_text(name)
        if not n:
            return {"ok": False, "error": "name is required"}
        self._registry.setdefault("specialists", {})[n] = {
            "name": n,
            "endpoint": _safe_text(endpoint),
            "version": _safe_text(version),
            "deployed": bool(deployed),
            "updated_at": _now(),
        }
        self._save()
        return {"ok": True, "specialist": self._registry["specialists"][n]}

    def status(self) -> Dict[str, Any]:
        specialists = self._registry.get("specialists", {})
        deployment = load_deployment_state()
        models_state = deployment.get("models") if isinstance(deployment, dict) else {}
        enriched: Dict[str, Any] = {}
        for name in specialist_names():
            row = specialists.get(name, {}) if isinstance(specialists, dict) else {}
            dep_rec = specialist_deployment_record(name)
            mode = specialist_mode(name)
            trained_ready = specialist_trained_ready(name)
            runtime_available = bool(row.get("deployed", False))
            enriched[name] = {
                **row,
                "mode": mode,
                "trained_ready": trained_ready,
                "runtime_available": runtime_available,
                "deployment_record": dep_rec if isinstance(dep_rec, dict) else {},
            }
            if not row and isinstance(models_state, dict) and name in models_state:
                enriched[name]["runtime_available"] = str((models_state[name] or {}).get("ok", "")).strip().lower() == "true"
        return {
            "ok": True,
            "registry_path": str(self.registry_path),
            "deployment_state_path": str((Path.home() / ".nexus" / "brain_lab" / "deployment_state.json")),
            "specialists": enriched,
        }

    def _http_call(self, endpoint: str, payload: Dict[str, Any], timeout_sec: int = 20) -> Dict[str, Any]:
        req = urlrequest.Request(
            endpoint,
            method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlrequest.urlopen(req, timeout=timeout_sec) as resp:
                data = resp.read().decode("utf-8", errors="ignore")
                return json.loads(data) if data else {"ok": True}
        except (HTTPError, URLError, TimeoutError) as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _ollama_generate(self, model: str, prompt: str, timeout_sec: int = 30) -> Dict[str, Any]:
        req = urlrequest.Request(
            "http://localhost:11434/api/generate",
            method="POST",
            data=json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlrequest.urlopen(req, timeout=timeout_sec) as resp:
                data = resp.read().decode("utf-8", errors="ignore")
                body = json.loads(data) if data else {}
                return {"ok": True, "response": _safe_text(body.get("response"))}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _ollama_route(self, specialist: str, model: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        s = _safe_text(specialist)
        model_name = _safe_text(model)
        if not model_name:
            return {"ok": False, "error": "missing_model"}
        if s == "lead_ranker":
            lead = payload.get("lead") if isinstance(payload.get("lead"), dict) else {}
            prompt = (
                "Score this lead 0-100 for booking probability. Return only a number.\n\n"
                + json.dumps(lead, ensure_ascii=False)
            )
            out = self._ollama_generate(model_name, prompt)
            if not out.get("ok"):
                return out
            txt = _safe_text(out.get("response"))
            digits = "".join(ch for ch in txt if ch.isdigit() or ch == ".")
            try:
                score = float(digits) if digits else float(payload.get("heuristic_score", 50))
            except Exception:
                score = float(payload.get("heuristic_score", 50))
            score = max(0.0, min(100.0, score))
            return {"ok": True, "score": score, "source": "ollama"}
        if s == "conversion_specialist":
            prompt = (
                "Write one concise conversion-focused quote reply for Zoar Bathroom Rentals.\n"
                "Use this context JSON and output plain text only.\n\n"
                + json.dumps(payload, ensure_ascii=False)
            )
            out = self._ollama_generate(model_name, prompt)
            if not out.get("ok"):
                return out
            return {"ok": True, "message": _safe_text(out.get("response")), "source": "ollama"}
        if s == "objection_resolver":
            prompt = (
                "Give 2 concise objection responses for Zoar Bathroom Rentals. "
                "Return each response on a new line.\n\n"
                + json.dumps(payload, ensure_ascii=False)
            )
            out = self._ollama_generate(model_name, prompt)
            if not out.get("ok"):
                return out
            lines = [ln.strip("-• \t") for ln in _safe_text(out.get("response")).splitlines() if ln.strip()]
            return {"ok": True, "responses": lines[:5], "source": "ollama"}
        prompt = (
            f"You are {s} for Zoar Bathroom Rentals. Return JSON with keys: ok, summary.\n\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        out = self._ollama_generate(model_name, prompt)
        if not out.get("ok"):
            return out
        return {"ok": True, "summary": _safe_text(out.get("response")), "source": "ollama"}

    def route(self, specialist: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        spec = self._registry.get("specialists", {}).get(_safe_text(specialist), {})
        endpoint = _safe_text(spec.get("endpoint"))
        if endpoint:
            if endpoint.startswith("ollama://"):
                model = endpoint.replace("ollama://", "", 1).strip()
                routed = self._ollama_route(specialist, model, payload)
                if routed.get("ok", False):
                    return routed
            res = self._http_call(endpoint, payload)
            if res.get("ok", True):
                return res
        return self._fallback(specialist, payload)

    def _fallback(self, specialist: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        s = _safe_text(specialist)
        if s == "lead_ranker":
            lead = payload.get("lead") if isinstance(payload.get("lead"), dict) else {}
            score = 35
            if _safe_text(lead.get("phone")):
                score += 20
            if _safe_text(lead.get("email")):
                score += 15
            src = _safe_text(lead.get("source")).lower()
            if "facebook" in src:
                score += 10
            return {"ok": True, "score": max(0, min(100, score)), "source": "fallback"}
        if s == "conversion_specialist":
            event_type = _safe_text(payload.get("event_type") or "event")
            quote = int(payload.get("quote_price") or 999)
            return {
                "ok": True,
                "message": f"For your {event_type}, pricing starts around ${quote}. A $160 deposit can lock your date today.",
                "source": "fallback",
            }
        if s == "objection_resolver":
            objection = _safe_text(payload.get("objection") or "concern")
            return {
                "ok": True,
                "responses": [
                    f"Totally fair on {objection.replace('_', ' ')} — we can secure your date with a small $160 deposit.",
                    "If helpful, I can send a shorter option and a premium option so you can compare quickly.",
                ],
                "source": "fallback",
            }
        return {"ok": True, "source": "fallback", "note": f"No specialized fallback for {s}"}


_ROUTER: Optional[SpecialistRouter] = None


def get_specialist_router() -> SpecialistRouter:
    global _ROUTER
    if _ROUTER is None:
        _ROUTER = SpecialistRouter()
        # Auto-register known specialists as undeployed placeholders.
        known = [
            "lead_ranker",
            "venue_partner_ranker",
            "outreach_angle_selector",
            "conversion_specialist",
            "objection_resolver",
            "fb_media_buyer",
            "venue_partnership_closer",
            "follow_up_cadence_specialist",
            "quote_personalizer",
            "ad_creative_specialist",
            "vendor_referral_specialist",
            "seasonal_demand_predictor",
            "autonomous_entrepreneur",
        ]
        for name in known:
            if name not in _ROUTER._registry.get("specialists", {}):
                _ROUTER.register(name=name, endpoint="", version="", deployed=False)
    return _ROUTER
