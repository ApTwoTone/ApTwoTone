#!/usr/bin/env python3
"""API key audit — checks health of all configured provider keys.

Tests each key with a minimal API call and reports status.

Usage:
    python scripts/api_key_audit.py           # Full audit
    python scripts/api_key_audit.py --json    # JSON output
    python scripts/api_key_audit.py --quick   # Skip live tests, just count keys
"""

import argparse
import json
import logging
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("key_audit")

CONFIG_PATH = Path.home() / ".nexus" / "config.json"

# Provider test endpoints and configs
PROVIDER_TESTS = {
    "groq": {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "model": "llama-3.3-70b-versatile",
        "headers": {"User-Agent": "Nexus/1.0"},
    },
    "cerebras": {
        "url": "https://api.cerebras.ai/v1/chat/completions",
        "model": "llama3.1-8b",
    },
    "gemini": {
        "url_template": "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}",
        "method": "gemini",
    },
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/auth/key",
        "method": "auth_check",
    },
    "mistral": {
        "url": "https://api.mistral.ai/v1/chat/completions",
        "model": "open-mistral-nemo",
    },
    "zai": {
        "url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "model": "glm-4-flash",
    },
}


def _test_openai_compatible(url: str, key: str, model: str,
                            extra_headers: dict = None) -> dict:
    """Test an OpenAI-compatible endpoint."""
    data = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 5,
    }).encode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    }
    if extra_headers:
        headers.update(extra_headers)

    req = urllib.request.Request(url, data=data, headers=headers)
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            latency = time.time() - start
            return {"status": "ok", "code": resp.getcode(), "latency_ms": round(latency * 1000)}
    except urllib.error.HTTPError as e:
        latency = time.time() - start
        return {"status": "error", "code": e.code, "latency_ms": round(latency * 1000)}
    except Exception as e:
        return {"status": "error", "code": None, "error": str(e)[:80]}


def _test_gemini(key: str) -> dict:
    """Test Gemini API key."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}"
    data = json.dumps({
        "contents": [{"parts": [{"text": "hi"}]}],
        "generationConfig": {"maxOutputTokens": 5},
    }).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            latency = time.time() - start
            return {"status": "ok", "code": resp.getcode(), "latency_ms": round(latency * 1000)}
    except urllib.error.HTTPError as e:
        latency = time.time() - start
        return {"status": "error", "code": e.code, "latency_ms": round(latency * 1000)}
    except Exception as e:
        return {"status": "error", "code": None, "error": str(e)[:80]}


def _test_auth_check(url: str, key: str) -> dict:
    """Check key validity via auth endpoint."""
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            latency = time.time() - start
            return {"status": "ok", "code": resp.getcode(), "latency_ms": round(latency * 1000)}
    except urllib.error.HTTPError as e:
        latency = time.time() - start
        return {"status": "error", "code": e.code, "latency_ms": round(latency * 1000)}
    except Exception as e:
        return {"status": "error", "code": None, "error": str(e)[:80]}


def test_key(provider: str, key: str) -> dict:
    """Test a single API key."""
    config = PROVIDER_TESTS.get(provider)
    if not config:
        return {"status": "skip", "reason": f"No test config for {provider}"}

    method = config.get("method", "openai")
    if method == "gemini":
        return _test_gemini(key)
    elif method == "auth_check":
        return _test_auth_check(config["url"], key)
    else:
        return _test_openai_compatible(
            config["url"], key, config.get("model", ""),
            config.get("headers"),
        )


def audit_keys(quick: bool = False) -> dict:
    """Audit all configured API keys."""
    if not CONFIG_PATH.exists():
        return {"error": "Config not found"}

    config = json.loads(CONFIG_PATH.read_text())
    key_pools = config.get("key_pools", {})

    results = {}
    for provider, keys in key_pools.items():
        if not isinstance(keys, list):
            keys = [keys]

        provider_result = {
            "total_keys": len(keys),
            "valid_keys": 0,
            "invalid_keys": 0,
            "key_results": [],
        }

        for i, key in enumerate(keys):
            if not key or len(key) < 10:
                provider_result["key_results"].append({
                    "key_index": i, "status": "invalid", "reason": "empty or too short"
                })
                provider_result["invalid_keys"] += 1
                continue

            if quick:
                provider_result["key_results"].append({
                    "key_index": i, "status": "present",
                    "key_preview": f"{key[:8]}...{key[-4:]}"
                })
                provider_result["valid_keys"] += 1
            else:
                log.info("Testing %s key %d/%d...", provider, i + 1, len(keys))
                result = test_key(provider, key)
                result["key_index"] = i
                result["key_preview"] = f"{key[:8]}...{key[-4:]}"
                provider_result["key_results"].append(result)
                if result["status"] == "ok":
                    provider_result["valid_keys"] += 1
                else:
                    provider_result["invalid_keys"] += 1

        results[provider] = provider_result

    # Check non-pool keys
    standalone = {}
    for key_name in ["telegram_bot_token", "gmail_app_password", "google_places_api_key"]:
        val = config.get(key_name, "")
        standalone[key_name] = "present" if val and len(val) > 5 else "missing"

    return {"key_pools": results, "standalone_keys": standalone}


def main():
    parser = argparse.ArgumentParser(description="API key audit")
    parser.add_argument("--quick", action="store_true", help="Skip live tests")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    result = audit_keys(quick=args.quick)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\n  API Key Audit {'(quick mode)' if args.quick else ''}")
        print(f"  {'=' * 55}")

        for provider, data in result.get("key_pools", {}).items():
            icon = "OK" if data["invalid_keys"] == 0 else "WARN"
            print(f"\n  [{icon}] {provider}: {data['valid_keys']}/{data['total_keys']} keys working")
            for kr in data["key_results"]:
                status = kr["status"]
                extra = ""
                if "latency_ms" in kr:
                    extra = f" ({kr['latency_ms']}ms)"
                elif "code" in kr and kr["code"]:
                    extra = f" (HTTP {kr['code']})"
                elif "reason" in kr:
                    extra = f" ({kr['reason']})"
                print(f"      Key {kr.get('key_index', '?')}: {status}{extra}")

        print(f"\n  Standalone Keys:")
        for name, status in result.get("standalone_keys", {}).items():
            icon = "OK" if status == "present" else "MISS"
            print(f"    [{icon}] {name}: {status}")


if __name__ == "__main__":
    main()
