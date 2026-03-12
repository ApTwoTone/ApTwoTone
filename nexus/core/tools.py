"""
Tool Registry — Safe, sandboxed tools for the Nexus chat agent.

Tools:
  file_read   — Read a file (allowed directories only)
  file_patch  — Patch a file (find/replace)
  shell_run   — Run a whitelisted shell command (30s timeout)
  db_query    — Read-only SQL query (SELECT only, 5s timeout)

All tools return {"ok": bool, ...} dicts.
Tool calls from AI use markers: ===TOOL===name===ARGS===json===END===
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("tools")

NEXUS_ROOT = Path(__file__).parent.parent
DB_PATH = Path.home() / ".nexus" / "memory.db"

# Allowed directories for file operations
ALLOWED_DIRS = [
    str(NEXUS_ROOT),
    str(Path.home() / "nexus-network"),
]

# Allowed shell commands (whitelist)
ALLOWED_COMMANDS = {"python3", "node", "npm", "git", "ls", "cat", "grep", "find", "wc", "head", "tail"}

# Blocked shell patterns
BLOCKED_PATTERNS = [
    r"rm\s+-rf",
    r"\|\s*curl",
    r"\|\s*wget",
    r">\s*/dev/",
    r"sudo\s",
    r"chmod\s",
    r"chown\s",
    r"mkfs\s",
    r"dd\s+if=",
]


def _is_allowed_path(path_str):
    # type: (str) -> bool
    """Check if a path is within allowed directories."""
    try:
        resolved = str(Path(path_str).resolve())
        return any(resolved.startswith(d) for d in ALLOWED_DIRS)
    except Exception:
        return False


# ── Tool Functions ────────────────────────────────────────────────────────────

def tool_file_read(path):
    # type: (str) -> Dict[str, Any]
    """Read a file. Only allowed in Nexus project directories."""
    fp = Path(path)
    if not fp.is_absolute():
        fp = NEXUS_ROOT / fp

    if not _is_allowed_path(str(fp)):
        return {"ok": False, "error": "Path not in allowed directories"}

    if not fp.exists():
        return {"ok": False, "error": "File not found: %s" % fp}

    try:
        content = fp.read_text()
        # Truncate large files
        if len(content) > 10000:
            content = content[:10000] + "\n... (truncated at 10000 chars)"
        return {"ok": True, "content": content, "path": str(fp), "lines": content.count("\n") + 1}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_file_patch(path, old, new):
    # type: (str, str, str) -> Dict[str, Any]
    """Patch a file using find/replace. Delegates to FileEditor."""
    fp = Path(path)
    if not fp.is_absolute():
        fp = NEXUS_ROOT / fp

    if not _is_allowed_path(str(fp)):
        return {"ok": False, "error": "Path not in allowed directories"}

    from core.chat_handler import FileEditor
    return FileEditor.patch_file(str(fp), old, new)


def tool_shell_run(cmd):
    # type: (str) -> Dict[str, Any]
    """Run a whitelisted shell command. 30s timeout."""
    # Extract base command
    parts = cmd.strip().split()
    if not parts:
        return {"ok": False, "error": "Empty command"}

    base_cmd = parts[0]
    if base_cmd not in ALLOWED_COMMANDS:
        return {"ok": False, "error": "Command '%s' not allowed. Allowed: %s" % (
            base_cmd, ", ".join(sorted(ALLOWED_COMMANDS))
        )}

    # Check blocked patterns
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, cmd):
            return {"ok": False, "error": "Command contains blocked pattern"}

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(NEXUS_ROOT),
        )
        output = result.stdout[:5000]
        if result.stderr:
            output += "\nSTDERR:\n" + result.stderr[:2000]
        return {
            "ok": result.returncode == 0,
            "output": output,
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Command timed out after 30s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def tool_db_query(sql):
    # type: (str) -> Dict[str, Any]
    """Run a read-only SQL query. SELECT only. 5s timeout."""
    sql = sql.strip()

    # Only allow SELECT
    if not sql.upper().startswith("SELECT"):
        return {"ok": False, "error": "Only SELECT queries allowed"}

    # Block dangerous keywords
    for kw in ("DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE", "ATTACH"):
        if kw in sql.upper().split():
            return {"ok": False, "error": "'%s' not allowed in read-only queries" % kw}

    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(sql)
        rows = cursor.fetchmany(100)  # Max 100 rows
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        data = [dict(row) for row in rows]
        conn.close()
        return {"ok": True, "columns": columns, "rows": data, "count": len(data)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Registry ──────────────────────────────────────────────────────────────────

TOOL_REGISTRY = {
    "file_read": {
        "fn": tool_file_read,
        "description": "Read a file",
        "args": ["path"],
        "safe": True,
    },
    "file_patch": {
        "fn": tool_file_patch,
        "description": "Patch a file (find old string, replace with new)",
        "args": ["path", "old", "new"],
        "safe": False,
    },
    "shell_run": {
        "fn": tool_shell_run,
        "description": "Run a shell command (whitelisted commands only)",
        "args": ["cmd"],
        "safe": False,
    },
    "db_query": {
        "fn": tool_db_query,
        "description": "Query the database (SELECT only)",
        "args": ["sql"],
        "safe": True,
    },
}


def execute_tool(tool_name, args):
    # type: (str, Dict[str, Any]) -> Dict[str, Any]
    """Execute a registered tool by name."""
    tool = TOOL_REGISTRY.get(tool_name)
    if not tool:
        return {"ok": False, "error": "Unknown tool: %s. Available: %s" % (
            tool_name, ", ".join(TOOL_REGISTRY.keys())
        )}

    fn = tool["fn"]
    try:
        # Map args dict to positional args based on registered arg names
        positional = [args.get(a, "") for a in tool["args"]]
        return fn(*positional)
    except Exception as e:
        log.error("Tool %s failed: %s", tool_name, e)
        return {"ok": False, "error": str(e)}


def parse_tool_calls(text):
    # type: (str) -> List[Dict[str, Any]]
    """Parse ===TOOL===name===ARGS===json===END=== markers from AI response."""
    pattern = r'===TOOL===(\w+)===ARGS===(.*?)===END==='
    matches = re.findall(pattern, text, re.DOTALL)
    calls = []
    for name, args_str in matches:
        try:
            args = json.loads(args_str.strip())
        except json.JSONDecodeError:
            args = {"raw": args_str.strip()}
        calls.append({"tool": name, "args": args})
    return calls


def get_tool_descriptions():
    # type: () -> str
    """Return tool descriptions for injection into system prompt."""
    lines = ["Available tools:"]
    for name, tool in TOOL_REGISTRY.items():
        args_str = ", ".join(tool["args"])
        lines.append("- %s(%s): %s" % (name, args_str, tool["description"]))
    lines.append("")
    lines.append("To use a tool, output: ===TOOL===tool_name===ARGS==={\"arg\": \"value\"}===END===")
    return "\n".join(lines)
