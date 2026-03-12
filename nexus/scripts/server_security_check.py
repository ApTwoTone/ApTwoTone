#!/usr/bin/env python3
"""Server security check — scans for common security issues.

Checks:
    1. Server bind address (0.0.0.0 vs 127.0.0.1)
    2. Debug mode detection
    3. Hardcoded secrets in Python files
    4. Auth endpoint verification (Rule 30: no auth)
    5. CORS configuration

Usage:
    python scripts/server_security_check.py        # Run all checks
    python scripts/server_security_check.py --json  # JSON output
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("server_security")

PROJECT_DIR = Path(__file__).resolve().parent.parent
SERVER_PY = PROJECT_DIR / "server.py"


def check_bind_address() -> dict:
    """Check if server binds to 0.0.0.0 (all interfaces) vs 127.0.0.1."""
    if not SERVER_PY.exists():
        return {"check": "bind_address", "status": "error", "message": "server.py not found"}

    content = SERVER_PY.read_text()
    binds = re.findall(r'host\s*=\s*["\']([^"\']+)["\']', content)
    binds += re.findall(r'\.run\([^)]*host\s*=\s*["\']([^"\']+)["\']', content)

    external = [b for b in binds if b == "0.0.0.0"]
    status = "warning" if external else "ok"

    return {
        "check": "bind_address",
        "status": status,
        "binds_found": list(set(binds)),
        "external_bind": bool(external),
        "note": "0.0.0.0 exposes server to network" if external else "Local only",
    }


def check_debug_mode() -> dict:
    """Check for debug mode in production."""
    if not SERVER_PY.exists():
        return {"check": "debug_mode", "status": "error", "message": "server.py not found"}

    content = SERVER_PY.read_text()
    debug_patterns = [
        re.compile(r'debug\s*=\s*True', re.IGNORECASE),
        re.compile(r'DEBUG\s*=\s*True'),
        re.compile(r'reload\s*=\s*True'),
    ]

    findings = []
    for i, line in enumerate(content.split("\n"), 1):
        for pat in debug_patterns:
            if pat.search(line) and not line.strip().startswith("#"):
                findings.append({"line": i, "content": line.strip()[:80]})

    return {
        "check": "debug_mode",
        "status": "warning" if findings else "ok",
        "findings": findings,
    }


def check_hardcoded_secrets() -> dict:
    """Scan Python files for hardcoded secrets."""
    secret_patterns = [
        re.compile(r'(?:api_key|apikey|secret|password|token)\s*=\s*["\'][a-zA-Z0-9]{20,}["\']', re.IGNORECASE),
        re.compile(r'sk-[a-zA-Z0-9]{20,}'),  # OpenAI-style keys
        re.compile(r'gsk_[a-zA-Z0-9]{20,}'),  # Groq keys
    ]

    findings = []
    scan_dirs = [PROJECT_DIR / "core", PROJECT_DIR / "integrations",
                 PROJECT_DIR / "scripts", PROJECT_DIR / "telegram"]

    for scan_dir in scan_dirs:
        if not scan_dir.exists():
            continue
        for py_file in scan_dir.glob("*.py"):
            try:
                content = py_file.read_text()
                for i, line in enumerate(content.split("\n"), 1):
                    if line.strip().startswith("#"):
                        continue
                    for pat in secret_patterns:
                        if pat.search(line):
                            # Skip test files and known safe patterns
                            if "test" in py_file.name or "placeholder" in line.lower():
                                continue
                            findings.append({
                                "file": str(py_file.relative_to(PROJECT_DIR)),
                                "line": i,
                                "pattern": pat.pattern[:40],
                            })
            except Exception:
                pass

    return {
        "check": "hardcoded_secrets",
        "status": "warning" if findings else "ok",
        "findings": findings[:10],  # Limit output
        "total_findings": len(findings),
    }


def check_auth_endpoints() -> dict:
    """Verify no auth endpoints exist (Rule 30: no auth in Nexus)."""
    if not SERVER_PY.exists():
        return {"check": "auth_endpoints", "status": "error"}

    content = SERVER_PY.read_text()
    auth_patterns = [
        re.compile(r'@app\.(get|post|put|delete)\s*\(\s*["\'].*/auth', re.IGNORECASE),
        re.compile(r'@app\.(get|post|put|delete)\s*\(\s*["\'].*/login', re.IGNORECASE),
        re.compile(r'@app\.(get|post|put|delete)\s*\(\s*["\'].*/signup', re.IGNORECASE),
    ]

    findings = []
    for i, line in enumerate(content.split("\n"), 1):
        for pat in auth_patterns:
            if pat.search(line):
                findings.append({"line": i, "content": line.strip()[:80]})

    return {
        "check": "auth_endpoints",
        "status": "warning" if findings else "ok",
        "findings": findings,
        "note": "Rule 30: Nexus has no authentication" if not findings else "Auth endpoints found — may violate Rule 30",
    }


def check_cors() -> dict:
    """Check CORS configuration."""
    if not SERVER_PY.exists():
        return {"check": "cors", "status": "error"}

    content = SERVER_PY.read_text()
    cors_all = bool(re.search(r'allow_origins\s*=\s*\[\s*["\']\*["\']', content))
    has_cors = bool(re.search(r'CORSMiddleware|cors', content, re.IGNORECASE))

    status = "ok"
    if cors_all:
        status = "warning"  # Open CORS — acceptable for local-only app

    return {
        "check": "cors",
        "status": status,
        "has_cors": has_cors,
        "allow_all_origins": cors_all,
        "note": "Open CORS acceptable for local single-user app (Rule 30)" if cors_all else "",
    }


def main():
    parser = argparse.ArgumentParser(description="Server security check")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    checks = [
        check_bind_address(),
        check_debug_mode(),
        check_hardcoded_secrets(),
        check_auth_endpoints(),
        check_cors(),
    ]

    if args.json:
        print(json.dumps(checks, indent=2))
    else:
        print(f"\n  Server Security Check")
        print(f"  Project: {PROJECT_DIR}")
        print(f"  {'=' * 50}")

        for c in checks:
            icon = {"ok": "OK", "warning": "WARN", "critical": "CRIT", "error": "ERR"}.get(c["status"], "?")
            print(f"\n  [{icon}] {c['check'].upper()}")
            for k, v in c.items():
                if k not in ("check", "status"):
                    if isinstance(v, list) and v:
                        print(f"      {k}:")
                        for item in v[:5]:
                            print(f"        {item}")
                    elif v:
                        print(f"      {k}: {v}")

        overall = "SECURE" if all(c["status"] == "ok" for c in checks) else "ISSUES FOUND"
        print(f"\n  Overall: {overall}")
        print(f"  {'=' * 50}")


if __name__ == "__main__":
    main()
