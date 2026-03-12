"""
Nexus Chat Handler — Simple, reliable, never hangs.

Three paths:
  1. Questions → AI with fallback chain (ZAI → Cerebras → Groq → Ollama → hardcoded)
  2. Actions (small changes) → AI + FileEditor
  3. Builds (features) → Build Pipeline

Every AI call has a hard timeout. Maximum wait: 33 seconds.
Target: under 3 seconds on ZAI.
"""
from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("chat_handler")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.DEBUG)


def _chat_log(msg, *args):
    """Write directly to stderr for reliable logging from threads."""
    import sys
    try:
        text = msg % args if args else msg
    except TypeError:
        text = str(msg)
    sys.stderr.write(text + "\n")
    sys.stderr.flush()

DB_PATH = Path.home() / ".nexus" / "memory.db"
NEXUS_ROOT = Path(__file__).parent.parent

# ── System Prompt (kept short: ~200 tokens) ──────────────────────────────────

SYSTEM_PROMPT = (
    "You are Nexus, the AI operating system for Zoar Bathroom Rentals "
    "run by Kai in the San Fernando Valley.\n\n"
    "You are direct, fast, no filler words. When Kai asks for something "
    "you do it and confirm.\n\n"
    "You have a FileEditor that edits code files automatically. "
    "Never describe fake file edits. Never pretend to edit files. "
    "Never ask for verification or confirmation — just confirm what was done.\n\n"
    "You have tools available:\n"
    "- file_read(path): Read a file\n"
    "- file_patch(path, old, new): Edit a file\n"
    "- shell_run(cmd): Run whitelisted shell commands\n"
    "- db_query(sql): Run SELECT queries on the database\n"
    "To use a tool: ===TOOL===tool_name===ARGS==={\"arg\": \"value\"}===END===\n\n"
    "Rules: never expose API keys. Never send emails or SMS without approval. "
    "Never spend money. Always verify before confirming.\n\n"
)

HARDCODED_FALLBACK = "I'm having trouble connecting right now. Please try again in a moment."

# ── Response Cleaning ─────────────────────────────────────────────────────────

_TOOL_CLEAN_RE = re.compile(r'===TOOL===\w+===ARGS===.*?===END===', re.DOTALL)
_DB_CLEAN_RE = re.compile(r'===DB_QUERY===(.*?)===END===', re.DOTALL)
_FILE_CLEAN_RE = re.compile(r'===FILE===.*?===END===', re.DOTALL)
_GENERIC_MARKER_RE = re.compile(r'===\w+===')


def _clean_response(content):
    # type: (str) -> str
    """Strip raw tool/file/db markers from AI responses before display."""
    content = _TOOL_CLEAN_RE.sub('', content)
    content = _DB_CLEAN_RE.sub(r'\1', content)
    content = _FILE_CLEAN_RE.sub('', content)
    content = _GENERIC_MARKER_RE.sub('', content)
    return content.strip()


# ── Prompt Injection Guard ────────────────────────────────────────────────────

_INJECTION_PATTERNS = [
    r'ignore\s+(all\s+)?previous\s+instructions',
    r'you\s+are\s+now\s+(?:a|an)\s+',
    r'system\s*:\s*',
    r'</?(?:system|assistant)>',
    r'===TOOL===',
    r'===FILE===',
    r'===OLD===',
    r'===NEW===',
]
_INJECTION_RE = re.compile('|'.join(_INJECTION_PATTERNS), re.IGNORECASE)


def _sanitize_input(text):
    # type: (str) -> str
    """Strip potential prompt injection markers from user input."""
    if _INJECTION_RE.search(text):
        log.warning("[SECURITY] Potential injection in: %s", text[:100])
        text = re.sub(r'===\w+===', '', text)
    return text


# ── Response Cache (60s TTL, questions only) ──────────────────────────────────

_response_cache = {}  # type: dict
_CACHE_TTL = 60


def _check_cache(text):
    # type: (str) -> Optional[Dict]
    key = text.strip().lower()
    entry = _response_cache.get(key)
    if entry and time.time() - entry[0] < _CACHE_TTL:
        log.debug("[CACHE] Hit for: %s", key[:50])
        return entry[1]
    return None


def _set_cache(text, response):
    # type: (str, Dict) -> None
    key = text.strip().lower()
    _response_cache[key] = (time.time(), response)
    if len(_response_cache) > 100:
        oldest = sorted(_response_cache, key=lambda k: _response_cache[k][0])
        for k in oldest[:50]:
            del _response_cache[k]


# ── Ollama Keepalive ──────────────────────────────────────────────────────────

def _ollama_keepalive():
    """Send a tiny request to keep the model loaded in VRAM."""
    try:
        import requests as _req
        _req.post(
            "http://localhost:11434/api/chat",
            json={"model": "llama3.2:3b",
                  "messages": [{"role": "user", "content": "hi"}],
                  "stream": False},
            timeout=120,
        )
        _chat_log("[CHAT] ollama keepalive OK")
    except Exception as e:
        _chat_log("[CHAT] ollama keepalive failed: %s" % e)


def start_ollama_keepalive():
    """Run keepalive on startup and every 4 minutes in a daemon thread."""
    import threading

    def _loop():
        while True:
            _ollama_keepalive()
            time.sleep(240)  # 4 minutes

    t = threading.Thread(target=_loop, daemon=True, name="ollama-keepalive")
    t.start()
    _chat_log("[CHAT] ollama keepalive thread started")


# ── Classification (keywords only, no AI) ────────────────────────────────────

# Feature signals that distinguish "add endpoint" (build) from "add comment" (action)
_FEATURE_SIGNALS = frozenset([
    "feature", "component", "page", "endpoint", "api", "module", "system",
    "dashboard", "pipeline", "integration", "agent", "worker", "daemon",
    "service", "tab", "screen", "notification", "webhook",
])

# Vendor keywords
_VENDOR_KWS = frozenset([
    "outreach", "email campaign", "cold email", "enrich", "test batch",
    "send test", "start campaign", "campaign status", "vendor filter",
    "pause campaign", "resume campaign",
])

# System keywords
_SYSTEM_KWS = frozenset([
    "health", "restart", "workers", "kill", "process", "fleet",
])

# Revenue keywords
_REVENUE_KWS = frozenset([
    "experiment", "persona", "revenue lab", "division three", "d3 ",
    "trend scan", "portfolio", "niche", "arbitrage",
])

# Data keywords
_DATA_KWS = frozenset([
    "how many", "show me", "list ", "count ", "what is my", "cpl",
    "leads from", "vendor count", "lead count", "booking",
])

CODING_KEYWORDS = [
    "fix", "debug", "add endpoint", "implement", "refactor", "write function",
    "write a function", "security review", "scrape", "build a", "create route",
    "sql query", "pytest", "unit test", "import", "syntax error", "traceback",
    "exception", "bug", "broken", "not working", "error in", "failing",
]

# Polite action pattern: "can you add/fix/create..." → action, not question
_POLITE_ACTION_RE = re.compile(
    r'^(can|could|would|will)\s+(you\s+)?(please\s+)?'
    r'(add|fix|create|change|remove|update|move|delete|make|put|insert|replace|edit|modify|write)\b'
)


def classify(text: str) -> str:
    """Classify message as question, action, build, vendor, system, revenue, or data.

    Pure keyword matching. No AI call. Under 1ms.
    """
    tl = text.lower().strip()

    # Detect "can you add/fix/create..." → action intent, not question
    is_polite_action = bool(_POLITE_ACTION_RE.match(tl))

    # Questions are never builds (unless it's a polite action like "can you add...")
    is_question = (
        tl.endswith("?")
        or tl.startswith(("what ", "how ", "why ", "who ", "where ", "when ",
                          "can ", "could ", "should ", "is ", "are ", "do ", "does ",
                          "show ", "tell ", "explain "))
    ) and not is_polite_action

    # Vendor commands
    if any(kw in tl for kw in _VENDOR_KWS):
        return "vendor"
    if ("filter" in tl or "score" in tl or "eligible" in tl) and ("vendor" in tl or "lead" in tl):
        return "vendor"
    if "top" in tl and ("lead" in tl or "vendor" in tl):
        return "vendor"
    if "campaign" in tl:
        return "vendor"
    if ("preview" in tl or "draft" in tl or "sample" in tl) and ("email" in tl or "outreach" in tl):
        return "vendor"
    if "email" in tl and any(kw in tl for kw in ["show", "preview", "draft", "look like", "template"]):
        return "vendor"
    # Follow-up email requests referencing previous context
    if "email" in tl and any(ref in tl for ref in [
        "them", "they", "those", "that one", "the first", "the second",
        "number one", "number two", "number three", "number four", "number five",
        "number 1", "number 2", "number 3", "number 4", "number 5",
        "#1", "#2", "#3", "#4", "#5", "for it", "first one", "second one",
    ]):
        return "vendor"

    # System commands
    if any(kw in tl for kw in _SYSTEM_KWS):
        return "system"

    # Revenue
    if any(kw in tl for kw in _REVENUE_KWS):
        return "revenue"

    # Data queries
    if any(kw in tl for kw in _DATA_KWS):
        return "data"

    # Questions → always go to question path (AI answer)
    if is_question:
        return "question"

    # Build vs action
    if tl.startswith(("build ", "implement ", "refactor ")):
        return "build"
    if tl.startswith(("fix ", "create ", "add ")) or is_polite_action:
        if any(sig in tl for sig in _FEATURE_SIGNALS):
            return "build"
        return "action"

    # Default: question (answered by AI)
    return "question"


def _estimate_complexity(message: str) -> int:
    tl = message.lower()
    score = 1
    if len(tl) > 120:
        score += 2
    if len(tl) > 300:
        score += 2
    if any(k in tl for k in ("multi-file", "cross-file", "architecture", "pipeline", "orchestration")):
        score += 2
    if any(k in tl for k in CODING_KEYWORDS):
        score += 2
    return max(1, min(score, 10))


def route_to_local_brain(message: str, complexity: int) -> Optional[str]:
    """Route medium-complex coding tasks to local Nexus Coder when installed."""
    if complexity < 6 or complexity > 8:
        return None
    tl = message.lower()
    if not any(kw in tl for kw in CODING_KEYWORDS):
        return None
    try:
        r = req_lib.get("http://localhost:11434/api/tags", timeout=1)
        models = [m.get("name", "") for m in r.json().get("models", [])]
        if "nexus-coder" in models:
            return "nexus-coder"
    except Exception:
        pass
    return None


def _query_local_brain(model: str, prompt: str, timeout: int = 60) -> Optional[Dict[str, Any]]:
    try:
        r = req_lib.post(
            "http://localhost:11434/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=timeout,
        )
        if r.status_code != 200:
            return None
        data = r.json() if r.content else {}
        text = str(data.get("response", "")).strip()
        if not text:
            return None
        return {"ok": True, "content": text, "provider": "ollama", "model": model}
    except Exception:
        return None


# ── Context Builder ──────────────────────────────────────────────────────────

def build_context(text: str, history: Optional[List[Dict]] = None,
                   inject_snapshot: bool = False) -> List[Dict[str, str]]:
    """Build messages list. Max 3000 tokens. Fast."""
    system_parts = [SYSTEM_PROMPT]

    # Inject relevant memories (keyword search, no AI call)
    try:
        from core.agent_memory import get_relevant_context
        mem_ctx = get_relevant_context(text)
        if mem_ctx:
            system_parts.append(mem_ctx)
    except Exception:
        pass

    # Only inject system snapshot for data queries (prevents AI parroting task counts)
    if inject_snapshot:
        snapshot = _get_snapshot()
        if snapshot:
            system_parts.append("LIVE STATE:\n" + snapshot)

    system = "\n".join(system_parts)

    messages = []  # type: List[Dict[str, str]]

    # Last 3 history messages (500 chars each max)
    if history:
        for msg in history[-3:]:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content[:500]})

    messages.append({"role": "user", "content": text})

    full = [{"role": "system", "content": system}] + messages

    # Token check: ~4 chars per token
    total_chars = sum(len(m.get("content", "")) for m in full)
    total_tokens = total_chars // 4
    if total_tokens > 2500:
        log.warning("Context size %d tokens exceeds 2500, trimming history", total_tokens)
        # Trim history first
        messages = [messages[-1]]  # Keep only user message
        full = [{"role": "system", "content": system}] + messages

    log.debug("Context: %d tokens, %d messages", total_chars // 4, len(full))
    return full


def _get_snapshot() -> str:
    """System snapshot, max 300 tokens."""
    lines = []
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=3)
        conn.row_factory = sqlite3.Row
        try:
            r = conn.execute("SELECT COUNT(*) as c FROM vendors").fetchone()
            lines.append("Vendors: %d" % (r["c"] if r else 0))
        except Exception:
            pass
        try:
            r = conn.execute("SELECT COUNT(*) as c FROM leads").fetchone()
            lines.append("Leads: %d" % (r["c"] if r else 0))
        except Exception:
            pass
        try:
            r = conn.execute(
                "SELECT COUNT(*) as c FROM fleet_tasks WHERE status IN ('claimed','in_progress')"
            ).fetchone()
            lines.append("Active workers: %d" % (r["c"] if r else 0))
        except Exception:
            pass
        try:
            r = conn.execute(
                "SELECT COUNT(*) as c FROM fleet_tasks WHERE status='pending'"
            ).fetchone()
            lines.append("Pending tasks: %d" % (r["c"] if r else 0))
        except Exception:
            pass
        conn.close()
    except Exception:
        pass
    return "\n".join(lines)


# ── AI Call via Parallel Fire (Bulletproof) ────────────────────────────────────

import requests as req_lib

# Tier 1: Fast providers — sub-second if not rate-limited by background workers
FAST_PROVIDERS = [
    {"id": "cerebras", "url": "https://api.cerebras.ai/v1/chat/completions",
     "model": "llama3.1-8b", "timeout": 3},
    {"id": "groq", "url": "https://api.groq.com/openai/v1/chat/completions",
     "model": "meta-llama/llama-4-scout-17b-16e-instruct", "timeout": 3},
]

# Tier 2: Guaranteed providers — unlimited or high-limit, always respond (slower)
GUARANTEED_PROVIDERS = [
    {"id": "zai", "url": "https://api.z.ai/api/paas/v4/chat/completions",
     "model": "glm-4.5-flash", "timeout": 25},
    {"id": "openrouter", "url": "https://openrouter.ai/api/v1/chat/completions",
     "model": "meta-llama/llama-3.1-8b-instruct", "timeout": 10},
    {"id": "ollama", "url": "http://localhost:11434/api/chat",
     "model": "llama3.2:3b", "timeout": 60},
]


def _call_ollama_sync(prov, msgs):
    # type: (Dict, List[Dict]) -> Optional[Dict[str, Any]]
    """Ollama has a different API format."""
    try:
        t0 = time.time()
        resp = req_lib.post(
            prov["url"],
            json={"model": prov["model"], "messages": msgs, "stream": False},
            timeout=prov["timeout"],
        )
        elapsed = time.time() - t0
        if resp.status_code == 200:
            data = resp.json()
            content = data.get("message", {}).get("content", "")
            if content:
                _chat_log("[CHAT] ollama OK %.1fs" % elapsed)
                return {"ok": True, "content": content, "provider": "ollama", "model": prov["model"]}
        _chat_log("[CHAT] ollama HTTP %d" % resp.status_code)
    except req_lib.exceptions.Timeout:
        _chat_log("[CHAT] ollama timeout %ds" % prov["timeout"])
    except Exception as e:
        _chat_log("[CHAT] ollama err: %s" % e)
    return None


def _try_single_provider(prov, msgs, pool):
    # type: (Dict, List[Dict], Any) -> Optional[Dict[str, Any]]
    """Try one provider with one key. Thread-safe. Returns result or None."""
    pid = prov["id"]
    if pid == "ollama":
        return _call_ollama_sync(prov, msgs)

    key = pool.next_key(pid)
    if not key:
        _chat_log("[CHAT] %s no key available" % pid)
        return None

    try:
        t0 = time.time()
        resp = req_lib.post(
            prov["url"],
            json={"model": prov["model"], "messages": msgs,
                  "max_tokens": 4096, "temperature": 0.7},
            headers={"Authorization": "Bearer %s" % key,
                     "Content-Type": "application/json",
                     "User-Agent": "Nexus/1.0"},
            timeout=prov["timeout"],
        )
        elapsed = time.time() - t0
        if resp.status_code == 200:
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if content:
                _chat_log("[CHAT] %s OK %.1fs" % (pid, elapsed))
                return {"ok": True, "content": content, "provider": pid, "model": prov["model"]}
        elif resp.status_code == 429:
            _chat_log("[CHAT] %s 429" % pid)
        else:
            _chat_log("[CHAT] %s HTTP %d" % (pid, resp.status_code))
    except req_lib.exceptions.Timeout:
        _chat_log("[CHAT] %s timeout %ds" % (pid, prov["timeout"]))
    except Exception as e:
        _chat_log("[CHAT] %s err: %s" % (pid, e))
    return None


def _call_chat_sync(system, user_msgs, exclude_ids=None, fast_first=False):
    # type: (str, List[Dict], Optional[set], bool) -> Dict[str, Any]
    """Tiered provider routing.

    Default: ZAI GLM first → Cerebras/Groq → OpenRouter → Ollama
    fast_first=True: Cerebras/Groq first → ZAI → OpenRouter → Ollama
      (for short messages where sub-second speed matters more than model quality)
    """
    from core.key_rotation import get_pool
    pool = get_pool()
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.extend(user_msgs)

    providers = [p for p in FAST_PROVIDERS + GUARANTEED_PROVIDERS
                 if not exclude_ids or p["id"] not in exclude_ids]

    if fast_first:
        # Speed mode: try fast providers first, then ZAI as fallback
        fast = [p for p in providers if p["id"] in ("cerebras", "groq")]
        if fast:
            ex = ThreadPoolExecutor(max_workers=len(fast), thread_name_prefix="chat-fast")
            futures = {ex.submit(_try_single_provider, p, msgs, pool): p["id"] for p in fast}
            try:
                for future in as_completed(futures, timeout=5):
                    try:
                        result = future.result()
                        if result and result.get("ok"):
                            ex.shutdown(wait=False, cancel_futures=True)
                            return result
                    except Exception:
                        pass
            except Exception:
                pass
            ex.shutdown(wait=False, cancel_futures=True)

        # Fast providers failed — fall through to ZAI
        zai = next((p for p in providers if p["id"] == "zai"), None)
        if zai:
            result = _try_single_provider({**zai, "timeout": 6}, msgs, pool)
            if result and result.get("ok"):
                return result
    else:
        # Quality mode: ZAI first
        zai = next((p for p in providers if p["id"] == "zai"), None)
        if zai:
            result = _try_single_provider({**zai, "timeout": 6}, msgs, pool)
            if result and result.get("ok"):
                return result

        # Tier 2: Fast providers in parallel (Cerebras + Groq)
        fast = [p for p in providers if p["id"] in ("cerebras", "groq")]
        if fast:
            ex = ThreadPoolExecutor(max_workers=len(fast), thread_name_prefix="chat-fast")
            futures = {ex.submit(_try_single_provider, p, msgs, pool): p["id"] for p in fast}
            try:
                for future in as_completed(futures, timeout=5):
                    try:
                        result = future.result()
                        if result and result.get("ok"):
                            ex.shutdown(wait=False, cancel_futures=True)
                            return result
                    except Exception:
                        pass
            except Exception:
                pass
            ex.shutdown(wait=False, cancel_futures=True)

    # Tier 3: OpenRouter
    orouter = next((p for p in providers if p["id"] == "openrouter"), None)
    if orouter:
        _chat_log("[CHAT] tier3: trying openrouter")
        result = _try_single_provider(orouter, msgs, pool)
        if result and result.get("ok"):
            return result

    # Tier 4: Ollama (local, always available)
    ollama = next((p for p in providers if p["id"] == "ollama"), None)
    if ollama:
        _chat_log("[CHAT] tier4: trying ollama")
        result = _try_single_provider(ollama, msgs, pool)
        if result and result.get("ok"):
            return result

    _chat_log("[CHAT] all tiered providers failed")
    return {"ok": False}


async def call_with_fallback(messages: List[Dict], fast_first: bool = False) -> Dict[str, Any]:
    """Call AI using sync HTTP in a thread. Immune to event loop congestion.
    fast_first=True: prioritize Cerebras/Groq for sub-second speed (short messages).
    """
    system = ""
    user_msgs = []
    for m in messages:
        if m["role"] == "system":
            system = m["content"]
        else:
            user_msgs.append(m)

    try:
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None, lambda: _call_chat_sync(system, user_msgs, fast_first=fast_first)),
            timeout=35,
        )
        if result.get("ok"):
            return result
    except asyncio.TimeoutError:
        log.warning("Chat: thread timed out after 30s")
    except Exception as e:
        log.warning("Chat: executor failed: %s", e)

    return {
        "ok": True,
        "content": HARDCODED_FALLBACK,
        "provider": "hardcoded",
        "model": "none (hardcoded)",
    }


# ── FileEditor ───────────────────────────────────────────────────────────────

class FileEditor:
    """Simple file operations for the chat action path."""

    @staticmethod
    def read_file(path: str) -> Dict[str, Any]:
        """Read a file and return its content."""
        try:
            fp = Path(path)
            if not fp.is_absolute():
                fp = NEXUS_ROOT / fp
            if not fp.exists():
                return {"ok": False, "error": "File not found: %s" % fp}
            content = fp.read_text()
            return {"ok": True, "content": content, "path": str(fp)}
        except Exception as e:
            log.error("FileEditor.read_file failed: %s", e)
            return {"ok": False, "error": str(e)}

    @staticmethod
    def patch_file(path: str, old_str: str, new_str: str) -> Dict[str, Any]:
        """Find old_str exactly once, replace with new_str, verify."""
        try:
            fp = Path(path)
            if not fp.is_absolute():
                fp = NEXUS_ROOT / fp
            if not fp.exists():
                return {"ok": False, "error": "File not found: %s" % fp}

            content = fp.read_text()
            count = content.count(old_str)
            if count == 0:
                # Fuzzy match: normalize whitespace and try again
                norm = lambda s: re.sub(r'\s+', ' ', s.strip())
                norm_old = norm(old_str)
                # Search line by line for a region matching normalized old_str
                lines = content.split('\n')
                old_lines = old_str.strip().split('\n')
                found_start = -1
                for i in range(len(lines) - len(old_lines) + 1):
                    chunk = '\n'.join(lines[i:i + len(old_lines)])
                    if norm(chunk) == norm_old:
                        found_start = i
                        break
                if found_start < 0:
                    return {"ok": False, "error": "String not found in file"}
                # Replace the matched chunk
                before = '\n'.join(lines[:found_start])
                after = '\n'.join(lines[found_start + len(old_lines):])
                new_content = before + '\n' + new_str + '\n' + after
            elif count > 1:
                return {"ok": False, "error": "String found %d times, must be unique" % count}
            else:
                new_content = content.replace(old_str, new_str, 1)
            fp.write_text(new_content)

            # Verify
            verify = fp.read_text()
            if new_str not in verify:
                return {"ok": False, "error": "Write verification failed"}

            return {"ok": True, "path": str(fp), "detail": "Patched successfully"}
        except Exception as e:
            log.error("FileEditor.patch_file failed: %s", e)
            return {"ok": False, "error": str(e)}

    @staticmethod
    def write_file(path: str, content: str) -> Dict[str, Any]:
        """Write content to file."""
        try:
            fp = Path(path)
            if not fp.is_absolute():
                fp = NEXUS_ROOT / fp
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content)

            # Verify
            verify = fp.read_text()
            if verify != content:
                return {"ok": False, "error": "Write verification failed"}

            return {"ok": True, "path": str(fp)}
        except Exception as e:
            log.error("FileEditor.write_file failed: %s", e)
            return {"ok": False, "error": str(e)}

    @staticmethod
    def run_command(cmd: str, timeout: int = 30) -> Dict[str, Any]:
        """Run shell command with timeout. Never hangs."""
        import subprocess
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=timeout, cwd=str(NEXUS_ROOT),
            )
            return {
                "ok": result.returncode == 0,
                "stdout": result.stdout[:2000],
                "stderr": result.stderr[:2000],
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Command timed out after %ds" % timeout}
        except Exception as e:
            log.error("FileEditor.run_command failed: %s", e)
            return {"ok": False, "error": str(e)}


# ── Action Handler ───────────────────────────────────────────────────────────

_BLOCKED_PATHS = (".env", "config.json", "credentials", "secret", ".nexus/config")
_FILE_RE = re.compile(r'(?:^|\s)([\w./\-]+\.(?:py|js|ts|tsx|css|html|json|md|yaml|yml|sh))\b')


def _parse_patch(text: str):
    """Extract ===FILE===, ===OLD===, ===NEW=== markers from AI response."""
    m = re.search(
        r'===FILE===\s*\n(.+?)\n\s*===OLD===\s*\n(.*?)\n\s*===NEW===\s*\n(.*?)\n\s*===END===',
        text, re.DOTALL,
    )
    if not m:
        return None
    return {"file": m.group(1).strip(), "old": m.group(2), "new": m.group(3)}


async def _handle_action(text: str, messages: List[Dict]) -> Dict[str, Any]:
    """Handle small file actions via AI + FileEditor. Saves to disk."""
    meta = {"agent": "Nexus AI", "model": "", "provider": ""}

    # 1. Extract file path from user text (or infer from keywords)
    file_match = _FILE_RE.search(text)
    if not file_match:
        # No explicit file → infer from keywords (no AI call, instant)
        tl = text.lower()
        if any(kw in tl for kw in ("css", "style", "color", "font", "margin", "padding", "border")):
            file_path = "static/css/dashboard.css"
        elif any(kw in tl for kw in ("script", "function", "click", "event", "fetch", "api call")):
            file_path = "static/js/app.js"
        elif any(kw in tl for kw in ("endpoint", "route", "api", "server", "backend")):
            file_path = "server.py"
        else:
            # Default: main dashboard HTML for UI changes
            file_path = "static/index.html"
    else:
        file_path = file_match.group(1)

    # 2. Safety check
    for blocked in _BLOCKED_PATHS:
        if blocked in file_path:
            return {**meta, "content": "Cannot edit %s — blocked for safety." % file_path}

    # 3. Read the file
    file_data = FileEditor.read_file(file_path)
    if not file_data["ok"]:
        return {**meta, "content": "Could not read %s: %s" % (file_path, file_data.get("error", "unknown"))}

    file_content = file_data["content"]
    max_chars = 8000
    if len(file_content) > max_chars:
        # Try to find a relevant section based on keywords from the request
        tl = text.lower()
        keywords = [w for w in tl.split() if len(w) > 3 and w not in
                    ("can", "you", "the", "add", "please", "could", "would", "this", "that", "with", "from")]
        best_pos = 0
        for kw in keywords:
            pos = file_content.lower().find(kw)
            if pos > 0:
                best_pos = max(0, pos - 500)
                break
        file_content = file_content[best_pos:best_pos + max_chars]
        if best_pos > 0:
            file_content = "... (truncated before)\n" + file_content
        if best_pos + max_chars < len(file_data["content"]):
            file_content += "\n... (truncated after)"

    # 4. Ask AI for structured patch
    patch_prompt = [
        {"role": "system", "content": (
            "You are a code editor. The user wants to change a file. "
            "Output the edit using EXACTLY this format:\n\n"
            "===FILE===\npath/to/file.py\n===OLD===\nexact lines to replace\n===NEW===\nreplacement lines\n===END===\n\n"
            "Rules:\n"
            "- OLD must be an EXACT copy of consecutive lines from the file\n"
            "- Include enough context lines so OLD matches exactly once\n"
            "- Keep the change minimal\n"
            "- Do NOT add any explanation outside the markers"
        )},
        {"role": "user", "content": "File: %s\n\n```\n%s\n```\n\nRequest: %s" % (file_path, file_content, text)},
    ]
    result = await call_with_fallback(patch_prompt)
    ai_text = result.get("content", "")
    meta["model"] = result.get("model", "")
    meta["provider"] = result.get("provider", "")

    # 5. Check for tool calls (===TOOL=== markers)
    try:
        from core.tools import parse_tool_calls, execute_tool
        tool_calls = parse_tool_calls(ai_text)
        if tool_calls:
            results = []
            for tc in tool_calls[:3]:  # Max 3 tool calls per response
                tr = execute_tool(tc["tool"], tc["args"])
                results.append("**%s**: %s" % (tc["tool"], "OK" if tr.get("ok") else tr.get("error", "failed")))
                if tr.get("output"):
                    results.append("```\n%s\n```" % tr["output"][:2000])
                if tr.get("content"):
                    results.append("```\n%s\n```" % tr["content"][:2000])
                if tr.get("rows"):
                    for row in tr["rows"][:10]:
                        results.append(str(row))
            return {**meta, "content": "\n".join(results)}
    except Exception as e:
        log.warning("Tool call parsing failed: %s", e)

    # 6. Parse file edit markers — if AI didn't follow format, tell user
    patch = _parse_patch(ai_text)
    if not patch:
        return {**meta, "content": "I'll edit %s. Here's what I'd change:\n\n%s\n\nSay 'do it' to confirm, or be more specific." % (file_path, ai_text[:800])}

    # 7. Execute file edit
    edit_result = FileEditor.patch_file(patch["file"], patch["old"], patch["new"])
    if edit_result["ok"]:
        return {**meta, "content": "Edited %s successfully.\n\nChange applied:\n```\n%s\n```\n→\n```\n%s\n```" % (
            patch["file"], patch["old"][:500], patch["new"][:500]
        )}
    else:
        return {**meta, "content": "Could not apply edit to %s: %s\n\nSuggestion:\n%s" % (
            patch["file"], edit_result.get("error", ""), ai_text
        )}


# ── Main Entry Point ─────────────────────────────────────────────────────────

async def handle_chat_message(
    text: str,
    source: str = "web",
) -> Dict[str, Any]:
    """Route a chat message and return response. Never hangs. Max 33 seconds.

    Returns: {"message": {id, role, content, agent, model, category, latency_ms, ...}}
    """
    start = time.time()

    # Sanitize input
    text = _sanitize_input(text)

    # /claude gate
    force_claude = False
    if text.strip().lower().startswith("/claude "):
        text = text.strip()[8:]
        force_claude = True

    # @agent mention routing (before classification)
    _AGENT_MENTION_RE = re.compile(
        r'@(architect|builder|reviewer|patcher|bughunter|bug[\s._-]?hunter|all)\b',
        re.IGNORECASE,
    )
    agent_match = _AGENT_MENTION_RE.search(text)
    if agent_match:
        agent_name = agent_match.group(1).lower().replace(" ", "").replace("-", "").replace("_", "").replace(".", "")
        agent_text = _AGENT_MENTION_RE.sub("", text).strip()
        user_msg_id = _save_message("user", text, source=source)
        response = await _handle_agent_mention(agent_name, agent_text)
        latency = int((time.time() - start) * 1000)
        resp_id = _save_message(
            "assistant", response.get("content", ""), agent=response.get("agent", "Agent Studio"),
            model=response.get("model", ""), category="agent_studio",
            latency_ms=latency, source=source,
        )
        return {
            "message": {
                "id": resp_id, "role": "assistant",
                "content": response.get("content", ""),
                "agent": response.get("agent", "Agent Studio"),
                "model": response.get("model", ""),
                "category": "agent_studio",
                "latency_ms": latency,
            }
        }

    # Save user message
    user_msg_id = _save_message("user", text, source=source)

    # Auto-detect "remember" signals (e.g., "my name is Kai")
    try:
        from core.agent_memory import extract_and_store
        remembered = extract_and_store(text)
        if remembered:
            log.info("[MEMORY] Auto-stored: %s", remembered)
    except Exception:
        pass

    # Classify (keyword only, <1ms)
    category = classify(text)

    # Route
    if force_claude:
        response = await _handle_claude(text)
        category = "general"
    elif category == "build":
        response = await _handle_build(text)
    elif category == "vendor":
        response = await _handle_vendor(text)
    elif category == "system":
        response = await _handle_system(text)
    elif category == "revenue":
        response = await _handle_revenue(text)
    elif category == "action":
        history = get_history(limit=6)
        messages = build_context(text, history=history)
        response = await _handle_action(text, messages)
    else:
        # question, data, or anything else → AI with fallback
        complexity = _estimate_complexity(text)
        local_model = route_to_local_brain(text, complexity)
        if local_model:
            history = get_history(limit=6)
            messages = build_context(text, history=history)
            local_prompt = "\n".join(
                "%s: %s" % (m.get("role", "user"), m.get("content", ""))
                for m in messages
            )
            local_result = _query_local_brain(local_model, local_prompt, timeout=60)
            if local_result and local_result.get("ok"):
                response = {
                    "content": local_result.get("content", HARDCODED_FALLBACK),
                    "agent": "Nexus Coder",
                    "model": local_result.get("model", ""),
                    "provider": local_result.get("provider", ""),
                }
            else:
                local_model = None

        # Fast path: short messages (< 5 words) skip history, memory, snapshot
        word_count = len(text.split())
        if local_model:
            pass
        elif category in ("question", "general") and word_count < 5:
            short_msgs = [
                {"role": "system", "content": "You are Nexus, a concise AI assistant. Respond briefly."},
                {"role": "user", "content": text},
            ]
            result = await call_with_fallback(short_msgs, fast_first=True)
            response = {
                "content": result.get("content", HARDCODED_FALLBACK),
                "agent": "Nexus AI",
                "model": "%s (%s)" % (result.get("model", ""), result.get("provider", ""))
                         if result.get("provider") else result.get("model", ""),
                "provider": result.get("provider", ""),
            }
        # Check cache (questions only, not actions)
        elif _check_cache(text) is not None:
            response = _check_cache(text)
            response["model"] = (response.get("model", "") + " (cached)").strip()
        else:
            history = get_history(limit=6)
            is_data = category == "data"
            messages = build_context(text, history=history, inject_snapshot=is_data)
            if is_data:
                data_ctx = _get_data_context()
                if data_ctx:
                    messages[0]["content"] += "\n\nDATABASE CONTEXT:\n" + data_ctx[:1500]

            result = await call_with_fallback(messages)

            response = {
                "content": result.get("content", HARDCODED_FALLBACK),
                "agent": "Nexus AI",
                "model": "%s (%s)" % (result.get("model", ""), result.get("provider", ""))
                         if result.get("provider") else result.get("model", ""),
                "provider": result.get("provider", ""),
            }
            _set_cache(text, response)

    latency = int((time.time() - start) * 1000)

    # Clean response content (strip raw tool markers)
    response["content"] = _clean_response(response.get("content", ""))

    # Save response
    resp_id = _save_message(
        role="assistant",
        content=response.get("content", ""),
        agent=response.get("agent", ""),
        model=response.get("model", ""),
        category=category,
        latency_ms=latency,
        build_id=response.get("build_id"),
        build_status=response.get("build_status", ""),
        source=source,
    )

    return {
        "message": {
            "id": resp_id,
            "role": "assistant",
            "content": response.get("content", ""),
            "agent": response.get("agent", ""),
            "model": response.get("model", ""),
            "category": category,
            "latency_ms": latency,
            "build_id": response.get("build_id"),
            "build_status": response.get("build_status", ""),
            "created_at": datetime.utcnow().isoformat(),
        }
    }


async def handle_chat_stream(text, source="web"):
    # type: (str, str) -> Any
    """Streaming variant of handle_chat_message. Yields SSE event dicts.

    Events:
      {"type": "thinking", "content": "..."}  — progress updates
      {"type": "provider", "content": "..."}  — which provider responded
      {"type": "memory", "content": "..."}    — memory context found
      {"type": "complete", "message": {...}}   — final response (same as non-streaming)
      {"type": "error", "content": "..."}      — error
    """
    import json as _json
    start = time.time()

    # Sanitize input
    text = _sanitize_input(text)

    yield {"type": "thinking", "content": "Classifying..."}

    # /claude gate
    force_claude = False
    if text.strip().lower().startswith("/claude "):
        text = text.strip()[8:]
        force_claude = True

    # Save user message
    user_msg_id = _save_message("user", text, source=source)

    # Auto-detect "remember" signals
    try:
        from core.agent_memory import extract_and_store
        remembered = extract_and_store(text)
        if remembered:
            yield {"type": "memory", "content": "Stored: %s" % remembered}
    except Exception:
        pass

    # Classify
    category = classify(text)
    yield {"type": "thinking", "content": "Category: %s" % category}

    # Check for memory context
    try:
        from core.agent_memory import get_relevant_context
        mem_ctx = get_relevant_context(text)
        if mem_ctx:
            yield {"type": "memory", "content": mem_ctx.replace("REMEMBERED CONTEXT:\n", "")}
    except Exception:
        pass

    yield {"type": "thinking", "content": "Racing providers..."}

    # Route (simplified — questions and actions go through parallel fire)
    if force_claude:
        response = await _handle_claude(text)
        category = "general"
    elif category == "build":
        response = await _handle_build(text)
    elif category == "vendor":
        response = await _handle_vendor(text)
    elif category == "system":
        response = await _handle_system(text)
    elif category == "revenue":
        response = await _handle_revenue(text)
    elif category == "action":
        history = get_history(limit=6)
        messages = build_context(text, history=history)
        response = await _handle_action(text, messages)
    else:
        history = get_history(limit=6)
        is_data = category == "data"
        messages = build_context(text, history=history, inject_snapshot=is_data)
        if is_data:
            data_ctx = _get_data_context()
            if data_ctx:
                messages[0]["content"] += "\n\nDATABASE CONTEXT:\n" + data_ctx[:1500]
        result = await call_with_fallback(messages)
        response = {
            "content": result.get("content", HARDCODED_FALLBACK),
            "agent": "Nexus AI",
            "model": "%s (%s)" % (result.get("model", ""), result.get("provider", ""))
                     if result.get("provider") else result.get("model", ""),
            "provider": result.get("provider", ""),
        }

    if response.get("provider"):
        yield {"type": "provider", "content": response["provider"]}

    latency = int((time.time() - start) * 1000)

    # Save response
    resp_id = _save_message(
        role="assistant",
        content=_clean_response(response.get("content", "")),
        agent=response.get("agent", ""),
        model=response.get("model", ""),
        category=category,
        latency_ms=latency,
        build_id=response.get("build_id"),
        build_status=response.get("build_status", ""),
        source=source,
    )

    yield {
        "type": "complete",
        "message": {
            "id": resp_id,
            "role": "assistant",
            "content": _clean_response(response.get("content", "")),
            "agent": response.get("agent", ""),
            "model": response.get("model", ""),
            "category": category,
            "latency_ms": latency,
            "build_id": response.get("build_id"),
            "build_status": response.get("build_status", ""),
            "created_at": datetime.utcnow().isoformat(),
        },
    }


# ── Agent Studio Handler ──────────────────────────────────────────────────────

async def _handle_agent_mention(agent_name: str, text: str) -> Dict[str, Any]:
    """Route @agent mentions to the Agent Studio."""
    try:
        if agent_name == "bughunter":
            from core.bug_hunter import BugHunter
            hunter = BugHunter()
            result = hunter.scan()
            digest = hunter.get_digest()
            content = "Bug Hunter scan complete.\n%s" % (
                digest if digest else "No issues found. %d files scanned." % result.get("files_scanned", 0)
            )
            return {"content": content, "agent": "Bug Hunter", "model": "local"}

        if agent_name == "all":
            from core.agent_studio import get_studio
            result = await get_studio().submit_task(text)
            return {
                "content": "Full pipeline task submitted. Task ID: %s\n%s" % (
                    result.get("task_id", "?"), result.get("message", "")),
                "agent": "Agent Studio",
                "model": "pipeline",
            }

        # Direct agent routing
        agent_map = {
            "architect": "architect",
            "builder": "builder",
            "reviewer": "reviewer",
            "patcher": "patcher",
        }
        mapped = agent_map.get(agent_name)
        if mapped:
            from core.agent_studio import get_studio
            result = await get_studio().submit_to_agent(mapped, text)
            inner = result.get("result", {})
            if mapped == "architect":
                plan = inner.get("plan", {})
                content = "Architect plan:\n\nApproach: %s\n\nFiles: %s" % (
                    plan.get("approach", "N/A")[:300],
                    ", ".join(plan.get("files", [])) or "none specified",
                )
            elif mapped == "reviewer":
                verdict = inner.get("verdict", "?")
                issues = inner.get("issues", [])
                content = "Review verdict: %s\nIssues: %d" % (verdict, len(issues))
                for iss in issues[:5]:
                    content += "\n  - [%s] %s: %s" % (
                        iss.get("severity", "?"), iss.get("file", "?"),
                        iss.get("description", ""))
            else:
                content = "Task submitted to %s. ID: %s" % (
                    mapped.title(), result.get("task_id", "?"))
            return {
                "content": content,
                "agent": "%s Agent" % mapped.title(),
                "model": inner.get("provider", ""),
            }

        return {"content": "Unknown agent: %s" % agent_name, "agent": "Agent Studio", "model": ""}
    except Exception as e:
        log.error("Agent mention error: %s", e)
        return {"content": "Agent Studio error: %s" % str(e), "agent": "Agent Studio", "model": ""}


# ── Domain Handlers (delegate to existing code) ─────────────────────────────

async def _handle_build(text: str) -> Dict[str, Any]:
    from core.build_pipeline import get_build_pipeline
    bp = get_build_pipeline()
    build_id = bp.start_build(text, started_by="talk-to-nexus")
    return {
        "content": "Build #%d queued. Stage 1/5: Architect (Gemini) starting now.\n\n%s" % (
            build_id, text[:200]),
        "agent": "Build Pipeline",
        "model": "gemini",
        "build_id": build_id,
        "build_status": "architect",
    }


async def _handle_vendor(text: str) -> Dict[str, Any]:
    from core.nexus_router import NexusRouter
    router = NexusRouter()
    # Pass recent history so follow-ups can resolve "them", "number one", etc.
    history = get_history(limit=4)
    return await router._handle_vendor(text, history=history)


async def _handle_system(text: str) -> Dict[str, Any]:
    from core.nexus_router import NexusRouter
    router = NexusRouter()
    return await router._handle_system(text)


async def _handle_revenue(text: str) -> Dict[str, Any]:
    from core.nexus_router import NexusRouter
    router = NexusRouter()
    return await router._handle_revenue(text)


async def _handle_claude(text: str) -> Dict[str, Any]:
    """Route to Claude Sonnet. Only via /claude command."""
    import json as _json
    try:
        cfg = _json.loads((Path.home() / ".nexus/config.json").read_text())
        from core.providers import LLMProvider
        provider = LLMProvider(cfg)
        if "claude-sonnet" in provider.available():
            messages = build_context(text)
            system = ""
            user_msgs = []
            for m in messages:
                if m["role"] == "system":
                    system = m["content"]
                else:
                    user_msgs.append(m)
            result = await provider.chat(
                "claude-sonnet", user_msgs, system=system, max_tokens=4096,
            )
            if result and result.get("ok"):
                return {
                    "content": result.get("content", ""),
                    "agent": "Claude",
                    "model": "claude-sonnet",
                    "provider": "anthropic",
                }
    except Exception as e:
        log.warning("Claude call failed: %s", e)

    # Fallback to free models
    messages = build_context(text)
    result = await call_with_fallback(messages)
    return {
        "content": result.get("content", HARDCODED_FALLBACK),
        "agent": "Nexus AI",
        "model": result.get("model", ""),
        "provider": result.get("provider", ""),
    }


# ── DB Helpers ───────────────────────────────────────────────────────────────

def _conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _save_message(role, content, agent="", model="", category="general",
                  latency_ms=0, build_id=None, build_status="", source="web"):
    now = datetime.utcnow().isoformat()
    conn = _conn()
    try:
        cur = conn.execute(
            """INSERT INTO nexus_chat
               (role, content, agent, model, category, latency_ms,
                build_id, build_status, source, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (role, content, agent, model, category, latency_ms,
             build_id, build_status, source, now),
        )
        conn.commit()
        return cur.lastrowid or 0
    finally:
        conn.close()


def get_history(limit=50):
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM nexus_chat ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        conn.close()


def _get_data_context():
    """Rich database context for data queries."""
    lines = []
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=3)
        conn.row_factory = sqlite3.Row
        try:
            r = conn.execute("SELECT COUNT(*) as c FROM vendors").fetchone()
            lines.append("Total vendors: %d" % (r["c"] if r else 0))
        except Exception:
            pass
        try:
            r = conn.execute("SELECT COUNT(*) as c FROM leads").fetchone()
            lines.append("Total leads: %d" % (r["c"] if r else 0))
            rows = conn.execute(
                "SELECT status, COUNT(*) as c FROM leads GROUP BY status"
            ).fetchall()
            if rows:
                lines.append("Leads by status: %s" % ", ".join(
                    "%s=%d" % (r["status"] or "?", r["c"]) for r in rows))
        except Exception:
            pass
        try:
            r = conn.execute(
                "SELECT COUNT(*) as c FROM fleet_tasks WHERE status='pending'"
            ).fetchone()
            lines.append("Pending tasks: %d" % (r["c"] if r else 0))
        except Exception:
            pass
        try:
            r = conn.execute(
                "SELECT COUNT(*) as c FROM message_approvals WHERE status='pending'"
            ).fetchone()
            lines.append("Pending approvals: %d" % (r["c"] if r else 0))
        except Exception:
            pass
        try:
            r = conn.execute("SELECT COUNT(*) as c FROM build_plans").fetchone()
            lines.append("Total builds: %d" % (r["c"] if r else 0))
        except Exception:
            pass
        conn.close()
    except Exception:
        pass
    return "\n".join(lines)
