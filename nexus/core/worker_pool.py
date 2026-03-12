"""
Nexus Async Worker Pool — Calls AI providers with automatic key rotation.

All providers are called through a unified interface. OpenAI-compatible
providers share one code path. Gemini, ApiFreeLLM, and Ollama have
specialized handlers.

Usage:
    from core.worker_pool import call_provider, call_for_task
    result = await call_provider("groq", messages, system="You are helpful.")
    result = await call_for_task("speed_critical", messages)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger("worker_pool")

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

from core.key_rotation import PROVIDERS, get_pool

# Lazy-loaded budget manager (avoids circular imports)
_budget_mgr = None  # type: Optional[Any]


def _get_budget():
    # type: () -> Any
    global _budget_mgr
    if _budget_mgr is None:
        try:
            from core.token_budget import TokenBudgetManager
            _budget_mgr = TokenBudgetManager.get_instance()
        except Exception:
            pass
    return _budget_mgr


# ── Unified Call Interface ───────────────────────────────────────────────────

async def call_provider(
    provider_id: str,
    messages: List[Dict[str, str]],
    system: str = "",
    max_tokens: int = 4096,
    temperature: float = 0.7,
    model_override: Optional[str] = None,
    agent_id: str = "",
    task_type: str = "",
    priority: str = "normal",
) -> Dict[str, Any]:
    """Call a specific provider with automatic key rotation and budget checks.

    Returns: {"ok": bool, "content": str, "provider": str, "model": str,
              "tokens_used": int, "latency_ms": int}
    """
    pool = get_pool()
    prov = PROVIDERS.get(provider_id)
    if not prov:
        return _err("Unknown provider: %s" % provider_id)

    # Budget pre-check (skip for user-priority requests)
    budget = _get_budget()
    if budget and priority != "user":
        if not budget.check_budget(provider_id):
            return _err("Budget exceeded for %s" % provider_id)
        if budget.should_defer(provider_id, priority):
            return _err("Deferred — %s near daily cap" % provider_id)

    key = pool.next_key(provider_id)
    if not key:
        return _err("No keys available for %s" % provider_id)

    model = model_override or prov["model"]
    start = time.time()

    try:
        if provider_id == "gemini":
            result = await _call_gemini(key, messages, system, max_tokens, temperature)
        elif provider_id == "zai":
            result = await _call_zai(key, messages, system, max_tokens, temperature)
        elif provider_id == "ollama":
            result = await _call_ollama(messages, system, max_tokens)
        elif provider_id == "apifreellm":
            result = await _call_apifreellm(key, messages)
        elif prov.get("openai_compat"):
            result = await _call_openai_compat(
                prov["endpoint"], key, model, messages, system,
                max_tokens, temperature, prov.get("extra_headers", {})
            )
        else:
            result = _err("No handler for %s" % provider_id)

        latency = int((time.time() - start) * 1000)
        result["provider"] = provider_id
        result["model"] = model
        result["latency_ms"] = latency

        if result["ok"]:
            pool.report_success(provider_id, key)
            # Record token usage for budget tracking
            if budget:
                budget.record_usage(
                    provider_id=provider_id,
                    tokens_total=result.get("tokens_used", 0),
                    agent_id=agent_id,
                    task_type=task_type,
                    latency_ms=result.get("latency_ms", latency),
                    model=model,
                )
        else:
            content = result.get("content", "")
            if "429" in content or "rate" in content.lower():
                cooldown = 3600 if provider_id == "gemini" else 60
                pool.report_rate_limit(provider_id, key, cooldown_secs=cooldown)
            elif "401" in content or "403" in content or "unauthorized" in content.lower():
                pool.report_error(provider_id, key)

        return result

    except Exception as e:
        pool.report_error(provider_id, key)
        latency = int((time.time() - start) * 1000)
        return {
            "ok": False,
            "content": "[Error] %s: %s" % (provider_id, str(e)),
            "provider": provider_id,
            "model": model,
            "tokens_used": 0,
            "latency_ms": latency,
        }


async def call_for_task(
    task_type: str,
    messages: List[Dict[str, str]],
    system: str = "",
    max_tokens: int = 4096,
    temperature: float = 0.7,
    agent_id: str = "",
    priority: str = "normal",
) -> Dict[str, Any]:
    """Route a task to the best available provider with automatic fallback.

    Tries providers in the routing order for the task type.
    Falls back through the chain until one succeeds.
    """
    pool = get_pool()
    pair = pool.get_provider_for_task(task_type)
    if not pair:
        return _err("No provider available for task: %s" % task_type)

    provider_id, _ = pair

    # Try primary provider
    result = await call_provider(
        provider_id, messages, system, max_tokens, temperature,
        agent_id=agent_id, task_type=task_type, priority=priority,
    )
    if result["ok"]:
        return result

    # Fallback through routing chain
    from core.key_rotation import TASK_ROUTING
    route = TASK_ROUTING.get(task_type, TASK_ROUTING["general"])
    for fallback_id in route:
        if fallback_id == provider_id:
            continue
        if not pool.has_keys(fallback_id):
            continue
        log.info("Falling back from %s to %s for task %s", provider_id, fallback_id, task_type)
        result = await call_provider(
            fallback_id, messages, system, max_tokens, temperature,
            agent_id=agent_id, task_type=task_type, priority=priority,
        )
        if result["ok"]:
            return result

    return result  # Return last error


# ── OpenAI-Compatible Handler ────────────────────────────────────────────────

async def _call_openai_compat(
    endpoint: str,
    api_key: str,
    model: str,
    messages: List[Dict[str, str]],
    system: str,
    max_tokens: int,
    temperature: float,
    extra_headers: Dict[str, str],
) -> Dict[str, Any]:
    """Unified handler for all OpenAI-compatible APIs (Groq, Cerebras, Mistral, etc.)."""
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.extend(messages)

    payload = {
        "model": model,
        "messages": msgs,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer %s" % api_key,
        "User-Agent": "Nexus/1.0",
    }
    headers.update(extra_headers)

    if HAS_HTTPX:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(endpoint, json=payload, headers=headers)
            if resp.status_code == 429:
                return _err("Rate limited (429)")
            if resp.status_code in (401, 403):
                return _err("Auth error (%d)" % resp.status_code)
            resp.raise_for_status()
            data = resp.json()
    else:
        from urllib.request import Request, urlopen
        req = Request(endpoint, data=json.dumps(payload).encode(), headers=headers)
        with urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read())

    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    usage = data.get("usage", {})
    tokens = usage.get("total_tokens", 0)

    return {"ok": True, "content": content, "tokens_used": tokens}


# ── Gemini Handler ───────────────────────────────────────────────────────────

async def _call_gemini(
    api_key: str,
    messages: List[Dict[str, str]],
    system: str,
    max_tokens: int,
    temperature: float,
) -> Dict[str, Any]:
    """Call Gemini 3 Flash via Google AI Studio."""
    # Build Gemini-format contents
    contents = []
    for m in messages:
        role = "user" if m["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": str(m["content"])}]})

    payload = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": temperature,
        },
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}

    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent?key=%s" % api_key

    if HAS_HTTPX:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 429:
                log.warning("Gemini daily quota hit (429) — caller should back off 1 hour")
                return _err("Gemini rate limited (429)")
            resp.raise_for_status()
            data = resp.json()
    else:
        from urllib.request import Request, urlopen
        req = Request(url, data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())

    try:
        parts = data["candidates"][0]["content"].get("parts", [])
        content = parts[0]["text"] if parts else ""
        if not content:
            # Gemini sometimes returns empty for very short prompts
            content = "OK"
    except (KeyError, IndexError):
        return _err("Gemini returned no content: %s" % json.dumps(data)[:200])

    usage = data.get("usageMetadata", {})
    tokens = usage.get("totalTokenCount", 0)

    return {"ok": True, "content": content, "tokens_used": tokens}


# ── Ollama Handler ───────────────────────────────────────────────────────────

async def _call_ollama(
    messages: List[Dict[str, str]],
    system: str,
    max_tokens: int,
) -> Dict[str, Any]:
    """Call local Ollama instance."""
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.extend(messages)

    payload = {
        "model": "llama3.2:3b",
        "messages": msgs,
        "stream": False,
        "options": {"num_predict": max_tokens},
    }

    if HAS_HTTPX:
        async with httpx.AsyncClient(timeout=300.0) as client:
            try:
                resp = await client.post("http://localhost:11434/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                return _err("Ollama not running: %s" % str(e))
    else:
        from urllib.request import Request, urlopen
        from urllib.error import URLError
        try:
            req = Request("http://localhost:11434/api/chat",
                          data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"})
            with urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read())
        except URLError as e:
            return _err("Ollama not running: %s" % str(e))

    content = data.get("message", {}).get("content", "")
    return {"ok": True, "content": content, "tokens_used": 0}


# ── ApiFreeLLM Handler ───────────────────────────────────────────────────────

async def _call_apifreellm(
    api_key: str,
    messages: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Call ApiFreeLLM (custom format, not OpenAI-compatible)."""
    # ApiFreeLLM uses 'message' (singular) not 'messages' (array)
    combined = "\n".join(m["content"] for m in messages if m["role"] == "user")

    payload = {"message": combined, "model": "apifreellm"}
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer %s" % api_key,
    }

    if HAS_HTTPX:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://apifreellm.com/api/v1/chat",
                json=payload,
                headers=headers,
            )
            if resp.status_code == 429:
                return _err("ApiFreeLLM rate limited")
            resp.raise_for_status()
            data = resp.json()
    else:
        from urllib.request import Request, urlopen
        req = Request("https://apifreellm.com/api/v1/chat",
                      data=json.dumps(payload).encode(), headers=headers)
        with urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())

    # ApiFreeLLM response format
    content = data.get("response", data.get("content", data.get("message", "")))
    if isinstance(content, dict):
        content = content.get("content", str(content))

    return {"ok": True, "content": str(content), "tokens_used": 0}


# ── Z.AI GLM Handler (reasoning model — content in reasoning_content) ────────

async def _call_zai(
    api_key: str,
    messages: List[Dict[str, str]],
    system: str,
    max_tokens: int,
    temperature: float,
) -> Dict[str, Any]:
    """Call Z.AI GLM models. These are reasoning models that put output in reasoning_content."""
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.extend(messages)

    payload = {
        "model": "glm-4.5-flash",
        "messages": msgs,
        "max_tokens": max(max_tokens, 8000),
        "temperature": temperature,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer %s" % api_key,
    }

    if HAS_HTTPX:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://api.z.ai/api/paas/v4/chat/completions",
                json=payload, headers=headers,
            )
            if resp.status_code == 429:
                return _err("Z.AI rate limited (429)")
            resp.raise_for_status()
            data = resp.json()
    else:
        from urllib.request import Request, urlopen
        req = Request("https://api.z.ai/api/paas/v4/chat/completions",
                      data=json.dumps(payload).encode(), headers=headers)
        with urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())

    msg = data.get("choices", [{}])[0].get("message", {})
    content = msg.get("content", "")
    if not content:
        content = msg.get("reasoning_content", "")
    usage = data.get("usage", {})
    tokens = usage.get("total_tokens", 0)

    return {"ok": bool(content), "content": content or "[Empty]", "tokens_used": tokens}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _err(msg: str) -> Dict[str, Any]:
    return {"ok": False, "content": "[Error] %s" % msg, "tokens_used": 0}
