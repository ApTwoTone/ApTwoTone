import os, asyncio, httpx

MODELS = {
    "claude-sonnet": {"label":"Claude Sonnet","provider":"anthropic","input":3.00,"output":15.00},
    "claude-haiku":  {"label":"Claude Haiku","provider":"anthropic","input":0.80,"output":4.00},
    "kimi-32k":  {"label":"Kimi 32k","provider":"moonshot","model":"moonshot-v1-32k","input":0.25,"output":0.25},
    "kimi-8k":   {"label":"Kimi 8k","provider":"moonshot","model":"moonshot-v1-8k","input":0.12,"output":0.12},
    "gemini-flash":{"label":"Gemini Flash","provider":"gemini","model":"gemini-1.5-flash","input":0.075,"output":0.30},
    "gpt-4o-mini": {"label":"GPT-4o Mini","provider":"openai","model":"gpt-4o-mini","input":0.15,"output":0.60},
    "ollama":      {"label":"Ollama (free)","provider":"ollama","input":0.00,"output":0.00},
}
ROUTING = {
    "orchestrator":"claude-sonnet","research":"kimi-32k","analysis":"kimi-32k",
    "coding":"claude-haiku","creative":"kimi-32k","ad_creative":"kimi-32k",
    "summarize":"kimi-8k","cta":"kimi-8k","image_prompt":"kimi-8k",
    "self_improve":"claude-haiku","tool_research":"kimi-32k","general":"kimi-8k",
}
# Track which models are currently failing to avoid retry loops
_failing: set = set()

class LLMProvider:
    def __init__(self, config):
        self.config = config

    def available(self):
        a = []
        if self.config.get("anthropic_key"): a += ["claude-sonnet","claude-haiku"]
        if self.config.get("moonshot_key"):  a += ["kimi-8k","kimi-32k"]
        if self.config.get("gemini_key"):    a += ["gemini-flash"]
        if self.config.get("openai_key"):    a += ["gpt-4o-mini"]
        a.append("ollama")
        return a

    def resolve(self, task_type):
        preferred = ROUTING.get(task_type, "kimi-8k")
        avail = [m for m in self.available() if m not in _failing]
        if not avail: avail = self.available()  # reset if all failing
        if preferred in avail: return preferred
        chains = {
            "claude-sonnet":["claude-haiku","kimi-32k","gemini-flash","ollama"],
            "claude-haiku": ["kimi-8k","gemini-flash","ollama"],
            "kimi-32k":     ["kimi-8k","claude-haiku","gemini-flash","ollama"],
            "kimi-8k":      ["claude-haiku","gemini-flash","ollama"],
            "gemini-flash": ["kimi-8k","claude-haiku","ollama"],
        }
        for fb in chains.get(preferred, ["claude-haiku","ollama"]):
            if fb in avail: return fb
        return avail[0] if avail else "ollama"

    def label(self, model_id): return MODELS.get(model_id,{}).get("label", model_id)

    async def chat(self, model_id, messages, system="", tools=None, max_tokens=4096):
        # Try preferred model, then fallback automatically on auth/rate errors
        models_to_try = [model_id]
        chains = {
            "kimi-32k": ["kimi-8k","claude-haiku","gemini-flash"],
            "kimi-8k":  ["claude-haiku","gemini-flash"],
            "gemini-flash": ["kimi-8k","claude-haiku"],
        }
        for fb in chains.get(model_id, []):
            if fb in self.available() and fb != model_id:
                models_to_try.append(fb)

        last_err = ""
        for mid in models_to_try:
            p = MODELS.get(mid,{}).get("provider","ollama")
            if p == "anthropic": resp = await self._claude(mid, messages, system, tools, max_tokens)
            elif p == "moonshot": resp = await self._kimi(mid, messages, system, max_tokens)
            elif p == "gemini":   resp = await self._gemini(mid, messages, system, max_tokens)
            elif p == "openai":   resp = await self._openai(mid, messages, system, max_tokens)
            else:                  resp = await self._ollama(messages, system)

            if resp["ok"]:
                # Clear from failing set if it worked
                _failing.discard(mid)
                resp["model_used"] = mid
                return resp

            last_err = resp["content"]
            # Mark as failing if auth/quota error
            if "401" in last_err or "403" in last_err or "quota" in last_err.lower() or "unauthorized" in last_err.lower():
                print(f"[Nexus] Marking {mid} as failing: {last_err[:80]}")
                _failing.add(mid)
            else:
                break  # Non-auth error, don't try fallback

        return self._err(f"All models failed. Last: {last_err[:200]}")

    async def _claude(self, model_id, messages, system, tools, max_tokens):
        key = self.config.get("anthropic_key","")
        if not key: return self._err("No Anthropic key")
        name = "claude-sonnet-4-6" if "sonnet" in model_id else "claude-haiku-4-5-20251001"
        from anthropic import Anthropic
        client = Anthropic(api_key=key)
        kwargs = dict(model=name, max_tokens=max_tokens, messages=messages)
        if system: kwargs["system"] = system
        if tools:  kwargs["tools"] = tools
        loop = asyncio.get_event_loop()
        try:
            r = await loop.run_in_executor(None, lambda: client.messages.create(**kwargs))
            text = next((b.text for b in r.content if hasattr(b,"text")),"")
            return {"content":text,"model":model_id,"stop_reason":r.stop_reason,"raw":r,"ok":True}
        except Exception as e:
            err = str(e)
            if "401" in err or "unauthorized" in err.lower(): _failing.add(model_id)
            return self._err(f"Claude: {err}")

    async def _kimi(self, model_id, messages, system, max_tokens):
        key = self.config.get("moonshot_key","")
        if not key: return self._err("No Kimi key")
        name = MODELS[model_id]["model"]
        msgs = ([{"role":"system","content":system}] if system else []) + messages
        async with httpx.AsyncClient(timeout=90) as c:
            try:
                r = await c.post("https://api.moonshot.cn/v1/chat/completions",
                    headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},
                    json={"model":name,"messages":msgs,"max_tokens":max_tokens})
                if r.status_code == 401:
                    _failing.add(model_id)
                    return self._err(f"Kimi 401 Unauthorized - check your Moonshot API key")
                if r.status_code == 429:
                    return self._err(f"Kimi rate limited - will retry")
                r.raise_for_status()
                return {"content":r.json()["choices"][0]["message"]["content"],"model":model_id,"stop_reason":"end_turn","raw":r.json(),"ok":True}
            except Exception as e:
                err = str(e)
                if "401" in err: _failing.add(model_id)
                return self._err(f"Kimi: {err}")

    async def _gemini(self, model_id, messages, system, max_tokens):
        key = self.config.get("gemini_key","")
        if not key: return self._err("No Gemini key")
        name = MODELS[model_id]["model"]
        contents = [{"role":"user" if m["role"]=="user" else "model","parts":[{"text":str(m["content"])}]} for m in messages]
        payload = {"contents":contents,"generationConfig":{"maxOutputTokens":max_tokens}}
        if system: payload["systemInstruction"] = {"parts":[{"text":system}]}
        async with httpx.AsyncClient(timeout=60) as c:
            try:
                r = await c.post(f"https://generativelanguage.googleapis.com/v1beta/models/{name}:generateContent?key={key}",json=payload)
                r.raise_for_status()
                return {"content":r.json()["candidates"][0]["content"]["parts"][0]["text"],"model":model_id,"stop_reason":"end_turn","raw":r.json(),"ok":True}
            except Exception as e: return self._err(f"Gemini: {e}")

    async def _openai(self, model_id, messages, system, max_tokens):
        key = self.config.get("openai_key","")
        if not key: return self._err("No OpenAI key")
        msgs = ([{"role":"system","content":system}] if system else []) + messages
        async with httpx.AsyncClient(timeout=60) as c:
            try:
                r = await c.post("https://api.openai.com/v1/chat/completions",
                    headers={"Authorization":f"Bearer {key}"},
                    json={"model":"gpt-4o-mini","messages":msgs,"max_tokens":max_tokens})
                r.raise_for_status()
                return {"content":r.json()["choices"][0]["message"]["content"],"model":model_id,"stop_reason":"end_turn","raw":r.json(),"ok":True}
            except Exception as e: return self._err(f"OpenAI: {e}")

    async def _ollama(self, messages, system):
        url = self.config.get("ollama_url","http://localhost:11434")
        model = self.config.get("ollama_model","llama3")
        msgs = ([{"role":"system","content":system}] if system else []) + messages
        async with httpx.AsyncClient(timeout=120) as c:
            try:
                r = await c.post(f"{url}/api/chat",json={"model":model,"messages":msgs,"stream":False})
                r.raise_for_status()
                return {"content":r.json()["message"]["content"],"model":"ollama","stop_reason":"end_turn","raw":r.json(),"ok":True}
            except Exception as e: return self._err(f"Ollama: {e}")

    def _err(self, msg): return {"content":f"[Error] {msg}","model":"none","stop_reason":"error","raw":{},"ok":False}
