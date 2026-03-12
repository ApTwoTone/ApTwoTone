import os, subprocess
from pathlib import Path
import httpx

WORKSPACE = Path.home() / "nexus_workspace"
WORKSPACE.mkdir(exist_ok=True)
(WORKSPACE / "results").mkdir(exist_ok=True)

try:
    from integrations.n8n import N8N_TOOLS
except ImportError:
    N8N_TOOLS = []

BUILTIN_TOOLS = [
    {"name":"web_search","description":"Search the web via DuckDuckGo.",
     "input_schema":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}},
    {"name":"fetch_url","description":"Fetch and read a URL's text content.",
     "input_schema":{"type":"object","properties":{"url":{"type":"string"}},"required":["url"]}},
    {"name":"read_file","description":"Read a file from workspace.",
     "input_schema":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}},
    {"name":"write_file","description":"Write content to a file in workspace.",
     "input_schema":{"type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"}},"required":["path","content"]}},
    {"name":"list_files","description":"List files in workspace.",
     "input_schema":{"type":"object","properties":{"path":{"type":"string","default":""}}}},
    {"name":"run_code","description":"Execute Python code.",
     "input_schema":{"type":"object","properties":{"code":{"type":"string"},"timeout":{"type":"integer","default":30}},"required":["code"]}},
    {"name":"run_shell","description":"Run a shell command.",
     "input_schema":{"type":"object","properties":{"command":{"type":"string"}},"required":["command"]}},
    {"name":"install_package","description":"Install a Python package.",
     "input_schema":{"type":"object","properties":{"package":{"type":"string"}},"required":["package"]}},
    {"name":"save_result","description":"Save a result/artifact to workspace.",
     "input_schema":{"type":"object","properties":{"name":{"type":"string"},"content":{"type":"string"}},"required":["name","content"]}},
] + N8N_TOOLS

async def web_search(query):
    try:
        async with httpx.AsyncClient(timeout=15,follow_redirects=True) as c:
            r = await c.get("https://api.duckduckgo.com/",
                params={"q":query,"format":"json","no_html":1,"skip_disambig":1})
            data = r.json()
            out = []
            if data.get("AbstractText"): out.append(f"Summary: {data['AbstractText']}")
            for t in data.get("RelatedTopics",[])[:8]:
                if isinstance(t,dict) and t.get("Text"):
                    out.append(f"• {t['Text']}\n  {t.get('FirstURL','')}")
            return "\n\n".join(out) if out else "No results."
    except Exception as e: return f"Search error: {e}"

async def fetch_url(url):
    try:
        async with httpx.AsyncClient(timeout=20,follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent":"Mozilla/5.0"})
            import re
            text = re.sub(r'<[^>]+>',' ',r.text)
            text = re.sub(r'\s+',' ',text).strip()
            return text[:6000]
    except Exception as e: return f"Fetch error: {e}"

def read_file(path):
    try: return (WORKSPACE/path).read_text(encoding="utf-8")
    except Exception as e: return f"Error: {e}"

def write_file(path, content):
    try:
        fp = WORKSPACE/path; fp.parent.mkdir(parents=True,exist_ok=True)
        fp.write_text(content,encoding="utf-8"); return f"Written {len(content)} chars to {path}"
    except Exception as e: return f"Error: {e}"

def list_files(path=""):
    try:
        t = WORKSPACE/path if path else WORKSPACE
        return "\n".join(f"{'DIR' if f.is_dir() else 'FILE'} {f.name}" for f in sorted(t.iterdir())) or "Empty."
    except Exception as e: return f"Error: {e}"

def run_code(code, timeout=30):
    try:
        r = subprocess.run(["python3","-c",code],capture_output=True,text=True,timeout=timeout,cwd=str(WORKSPACE))
        return (("STDOUT:\n"+r.stdout) if r.stdout else "")+((("\nSTDERR:\n"+r.stderr) if r.stderr else "")) or f"Exit:{r.returncode}"
    except Exception as e: return f"Error: {e}"

def run_shell(command):
    try:
        r = subprocess.run(command,shell=True,capture_output=True,text=True,timeout=30,cwd=str(WORKSPACE))
        return (r.stdout+r.stderr).strip() or f"Exit:{r.returncode}"
    except Exception as e: return f"Error: {e}"

def install_package(package):
    try:
        r = subprocess.run(["pip","install",package,"--quiet"],capture_output=True,text=True,timeout=120)
        return f"Installed {package}" if r.returncode==0 else f"Failed: {r.stderr}"
    except Exception as e: return f"Error: {e}"

def save_result(name, content):
    try:
        p = WORKSPACE/"results"/name; p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(content,encoding="utf-8"); return f"Saved to results/{name}"
    except Exception as e: return f"Error: {e}"

async def execute_tool(name, inp, dynamic_tools=None):
    if dynamic_tools and name in dynamic_tools:
        try:
            ns={}; exec(dynamic_tools[name]["code"],ns)
            fn=ns.get("run") or ns.get(name)
            if fn:
                import asyncio
                r=fn(**inp); return str(await r if asyncio.iscoroutine(r) else r)
        except Exception as e: return f"Dynamic tool error: {e}"
    if name=="web_search":    return await web_search(inp["query"])
    elif name=="fetch_url":   return await fetch_url(inp["url"])
    elif name=="read_file":   return read_file(inp["path"])
    elif name=="write_file":  return write_file(inp["path"],inp["content"])
    elif name=="list_files":  return list_files(inp.get("path",""))
    elif name=="run_code":    return run_code(inp["code"],inp.get("timeout",30))
    elif name=="run_shell":   return run_shell(inp["command"])
    elif name=="install_package": return install_package(inp["package"])
    elif name=="save_result": return save_result(inp["name"],inp["content"])
    elif name.startswith("n8n_"):
        from integrations.n8n import execute_n8n_tool
        return await execute_n8n_tool(name, inp)
    return f"Unknown tool: {name}"
