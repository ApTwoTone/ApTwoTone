"""
Nexus Bug Hunter Agent

Independent scanner that runs every 10 minutes via launchd.
Checks Python files for syntax errors, import issues, and obvious bugs.
Recently changed files get AI-assisted deeper analysis via Groq.
Hourly Telegram digest of new findings (batched, not per-bug).

Usage:
    python core/bug_hunter.py              # One-shot scan
    python core/bug_hunter.py --daemon     # Continuous (launchd)
"""
from __future__ import annotations

import ast
import json
import logging
import os
import py_compile
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.telegram_policy import should_send_notification

log = logging.getLogger("bug_hunter")

DB_PATH = Path.home() / ".nexus" / "memory.db"
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # /Users/kai/nexus
SCAN_DIRS = ["core", "integrations", "telegram", "scripts", "agents"]
IGNORE_PATTERNS = ["__pycache__", ".pyc", "venv", "node_modules", ".git"]


class BugHunter:
    """Scans Python files for bugs and reports findings to DB."""

    def __init__(self, db_path: Optional[Path] = None,
                 project_root: Optional[Path] = None):
        self._db_path = db_path or DB_PATH
        self._root = project_root or PROJECT_ROOT
        self._last_digest_time = 0.0

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn

    def scan(self) -> Dict[str, Any]:
        """Run full scan. Returns summary."""
        findings = []
        files_scanned = 0

        for scan_dir in SCAN_DIRS:
            dir_path = self._root / scan_dir
            if not dir_path.exists():
                continue
            for py_file in dir_path.rglob("*.py"):
                if any(p in str(py_file) for p in IGNORE_PATTERNS):
                    continue
                files_scanned += 1
                findings.extend(self._check_file(py_file))

        # Also scan root-level Python files
        for py_file in self._root.glob("*.py"):
            files_scanned += 1
            findings.extend(self._check_file(py_file))

        # Deduplicate against existing reports
        new_findings = self._deduplicate(findings)

        # Save new findings to DB
        saved = self._save_findings(new_findings)

        # AI analysis on recently changed files
        recent_files = self._get_recently_changed(minutes=10)
        ai_findings = []
        if recent_files:
            ai_findings = self._ai_analyze(recent_files)
            new_ai = self._deduplicate(ai_findings)
            saved += self._save_findings(new_ai)

        summary = {
            "files_scanned": files_scanned,
            "findings": len(findings),
            "new_findings": saved,
            "ai_analyzed": len(recent_files),
            "ai_findings": len(ai_findings),
        }
        log.info("Scan complete: %d files, %d findings (%d new)",
                 files_scanned, len(findings), saved)
        try:
            self.create_tasks_from_critical_bugs()
        except Exception as e:
            log.warning("Bug-to-task bridge error: %s", e)
        return summary

    def _check_file(self, filepath: Path) -> List[Dict[str, Any]]:
        """Run syntax and basic AST checks on a single file."""
        findings = []
        rel_path = str(filepath.relative_to(self._root))

        # 1. Syntax check via py_compile
        try:
            py_compile.compile(str(filepath), doraise=True)
        except py_compile.PyCompileError as e:
            findings.append({
                "file_path": rel_path,
                "line_number": getattr(e, "lineno", 0) or 0,
                "severity": "error",
                "category": "syntax",
                "description": str(e).split("\n")[0][:500],
            })
            return findings  # Can't AST parse if syntax is broken

        # 2. AST analysis
        try:
            source = filepath.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except SyntaxError as e:
            findings.append({
                "file_path": rel_path,
                "line_number": e.lineno or 0,
                "severity": "error",
                "category": "syntax",
                "description": "SyntaxError: %s" % e.msg,
            })
            return findings
        except Exception:
            return findings

        # Check for common issues
        findings.extend(self._check_bare_except(tree, rel_path))
        findings.extend(self._check_mutable_defaults(tree, rel_path))

        return findings

    def _check_bare_except(self, tree: ast.AST,
                           rel_path: str) -> List[Dict[str, Any]]:
        """Find bare except clauses (except: without exception type)."""
        findings = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                findings.append({
                    "file_path": rel_path,
                    "line_number": node.lineno,
                    "severity": "warning",
                    "category": "style",
                    "description": "Bare except clause — catches all exceptions including KeyboardInterrupt",
                    "suggested_fix": "Use 'except Exception:' instead",
                })
        return findings

    def _check_mutable_defaults(self, tree: ast.AST,
                                rel_path: str) -> List[Dict[str, Any]]:
        """Find mutable default arguments (def foo(x=[]))."""
        findings = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for default in node.args.defaults + node.args.kw_defaults:
                    if default is not None and isinstance(default, (ast.List, ast.Dict, ast.Set)):
                        findings.append({
                            "file_path": rel_path,
                            "line_number": node.lineno,
                            "severity": "warning",
                            "category": "bug_risk",
                            "description": "Mutable default argument in '%s()'" % node.name,
                            "suggested_fix": "Use None as default and initialize inside function",
                        })
        return findings

    def _get_recently_changed(self, minutes: int = 10) -> List[Path]:
        """Find Python files modified in the last N minutes."""
        cutoff = time.time() - (minutes * 60)
        recent = []
        for scan_dir in SCAN_DIRS:
            dir_path = self._root / scan_dir
            if not dir_path.exists():
                continue
            for py_file in dir_path.rglob("*.py"):
                if any(p in str(py_file) for p in IGNORE_PATTERNS):
                    continue
                try:
                    if py_file.stat().st_mtime > cutoff:
                        recent.append(py_file)
                except OSError:
                    pass
        return recent[:5]  # Limit to 5 files per scan

    def _ai_analyze(self, files: List[Path]) -> List[Dict[str, Any]]:
        """Send recently changed files to Groq for deeper analysis."""
        findings = []
        try:
            from core.key_rotation import get_pool, PROVIDERS
            import requests

            pool = get_pool()
            pair = pool.get_provider_for_task("studio_bug_scan")
            if not pair:
                return findings

            provider_id, key = pair
            prov = PROVIDERS.get(provider_id, {})
            endpoint = prov.get("endpoint", "")
            model = prov.get("model", "")

            for filepath in files:
                rel_path = str(filepath.relative_to(self._root))
                try:
                    source = filepath.read_text()[:3000]
                except Exception:
                    continue

                messages = [
                    {"role": "system", "content": (
                        "You are a Python bug scanner. Analyze the code for bugs only. "
                        "Output JSON array: [{\"line\": N, \"severity\": \"error|warning\", "
                        "\"description\": \"issue\", \"fix\": \"suggestion\"}]. "
                        "Only report real bugs, not style issues. Return [] if no bugs found."
                    )},
                    {"role": "user", "content": "File: %s\n```python\n%s\n```" % (rel_path, source)},
                ]

                headers = dict(prov.get("extra_headers", {}))
                headers["Authorization"] = "Bearer %s" % key
                headers["Content-Type"] = "application/json"

                try:
                    resp = requests.post(
                        endpoint,
                        json={"model": model, "messages": messages, "max_tokens": 1000},
                        headers=headers,
                        timeout=30,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                        pool.report_success(provider_id, key)
                        try:
                            if "```json" in content:
                                content = content.split("```json")[1].split("```")[0].strip()
                            elif "```" in content:
                                content = content.split("```")[1].split("```")[0].strip()
                            issues = json.loads(content)
                            for issue in issues:
                                findings.append({
                                    "file_path": rel_path,
                                    "line_number": issue.get("line", 0),
                                    "severity": issue.get("severity", "warning"),
                                    "category": "ai_detected",
                                    "description": issue.get("description", ""),
                                    "suggested_fix": issue.get("fix", ""),
                                })
                        except (json.JSONDecodeError, IndexError):
                            pass
                    elif resp.status_code == 429:
                        pool.report_rate_limit(provider_id, key)
                        break  # Don't try more files
                except Exception as e:
                    log.warning("AI analysis failed for %s: %s", rel_path, e)
                    break

        except ImportError:
            log.warning("key_rotation not available for AI analysis")
        return findings

    def _deduplicate(self, findings: List[Dict]) -> List[Dict]:
        """Remove findings already in DB."""
        if not findings:
            return []
        conn = self._conn()
        try:
            existing = set()
            rows = conn.execute(
                "SELECT file_path, line_number, description FROM bug_reports "
                "WHERE status = 'open'"
            ).fetchall()
            for r in rows:
                existing.add((r["file_path"], r["line_number"], r["description"][:100]))
            new = []
            for f in findings:
                key = (f["file_path"], f.get("line_number", 0), f["description"][:100])
                if key not in existing:
                    new.append(f)
            return new
        finally:
            conn.close()

    def _save_findings(self, findings: List[Dict]) -> int:
        """Save findings to bug_reports table. Returns count saved."""
        if not findings:
            return 0
        conn = self._conn()
        try:
            for f in findings:
                conn.execute(
                    "INSERT INTO bug_reports "
                    "(file_path, line_number, severity, category, description, suggested_fix) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (f["file_path"], f.get("line_number", 0),
                     f.get("severity", "warning"), f.get("category", "syntax"),
                     f["description"], f.get("suggested_fix", "")),
                )
            conn.commit()
            return len(findings)
        except Exception as e:
            log.error("Failed to save findings: %s", e)
            return 0
        finally:
            conn.close()

    def get_open_bugs(self, severity: Optional[str] = None) -> List[Dict]:
        """Get all open bug reports."""
        conn = self._conn()
        try:
            if severity:
                rows = conn.execute(
                    "SELECT * FROM bug_reports WHERE status='open' AND severity=? "
                    "ORDER BY found_at DESC", (severity,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM bug_reports WHERE status='open' ORDER BY found_at DESC"
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def resolve_bug(self, bug_id: int, resolved_by: str = "manual") -> bool:
        """Mark a bug report as resolved."""
        conn = self._conn()
        try:
            cur = conn.execute(
                "UPDATE bug_reports SET status='resolved', resolved_at=datetime('now'), "
                "resolved_by=? WHERE id=?",
                (resolved_by, bug_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def get_digest(self) -> str:
        """Generate a text digest of recent bugs for Telegram."""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT severity, COUNT(*) as cnt FROM bug_reports "
                "WHERE status='open' GROUP BY severity ORDER BY "
                "CASE severity WHEN 'error' THEN 1 WHEN 'warning' THEN 2 ELSE 3 END"
            ).fetchall()
            if not rows:
                return ""

            total = sum(r["cnt"] for r in rows)
            lines = ["BUG HUNTER DIGEST", "Open issues: %d" % total, ""]
            for r in rows:
                lines.append("  %s: %d" % (r["severity"].upper(), r["cnt"]))

            # Top 5 most recent
            recent = conn.execute(
                "SELECT file_path, severity, description FROM bug_reports "
                "WHERE status='open' ORDER BY found_at DESC LIMIT 5"
            ).fetchall()
            if recent:
                lines.append("")
                lines.append("Recent:")
                for r in recent:
                    lines.append("  [%s] %s: %s" % (
                        r["severity"][0].upper(), r["file_path"],
                        r["description"][:60]))

            return "\n".join(lines)
        finally:
            conn.close()

    def send_telegram_digest(self):
        """Send hourly digest to Telegram (if new bugs found since last digest)."""
        now = time.time()
        if now - self._last_digest_time < 3600:
            return  # Not time yet

        digest = self.get_digest()
        if not digest:
            return

        self._last_digest_time = now
        decision = should_send_notification(
            digest,
            source="core.bug_hunter.digest",
            category="other",
        )
        if not decision.allowed:
            log.info("Telegram digest suppressed: %s", decision.reason)
            return

        try:
            cfg_path = Path.home() / ".nexus" / "config.json"
            if not cfg_path.exists():
                return
            config = json.loads(cfg_path.read_text())
            token = config.get("telegram_bot_token", "")
            chat_ids = config.get("telegram_chat_ids", [])
            if not token or not chat_ids:
                return

            import requests
            for chat_id in chat_ids:
                requests.post(
                    "https://api.telegram.org/bot%s/sendMessage" % token,
                    json={"chat_id": chat_id, "text": digest},
                    timeout=10,
                )
            log.info("Telegram digest sent")
        except Exception as e:
            log.warning("Failed to send Telegram digest: %s", e)

    def create_tasks_from_critical_bugs(self) -> int:
        """Create task_board entries for high-severity open bugs without existing tasks."""
        conn = self._conn()
        try:
            bugs = conn.execute(
                "SELECT * FROM bug_reports "
                "WHERE status = 'open' AND severity = 'error' "
                "AND category = 'ai_detected' "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM task_board "
                "  WHERE description LIKE '%bug_report_id:' || bug_reports.id || '%'"
                ")"
            ).fetchall()

            banned = frozenset([
                "core/security.py", "core/auth.py",
                "core/approval_system.py", "integrations/messaging.py",
            ])
            created = 0
            for bug in bugs:
                fp = bug["file_path"]
                if fp in banned:
                    continue

                title = "Fix: %s (line %d)" % (fp, bug["line_number"])
                description = (
                    "Bug detected by Bug Hunter (bug_report_id:%d)\n\n"
                    "File: %s\nLine: %d\nSeverity: %s\n"
                    "Description: %s\n\nSuggested fix: %s"
                ) % (
                    bug["id"], fp, bug["line_number"],
                    bug["severity"], bug["description"],
                    bug.get("suggested_fix", "") or "",
                )

                conn.execute(
                    "INSERT INTO task_board "
                    "(title, description, priority, status, created_by, "
                    "created_at, updated_at, tags, files_involved, task_type, complexity) "
                    "VALUES (?, ?, 7, 'open', 'bug_hunter', datetime('now'), "
                    "datetime('now'), ?, ?, 'bugfix', 4)",
                    (title[:200], description,
                     json.dumps(["bugfix", "auto-generated"]),
                     json.dumps([fp])),
                )
                created += 1

            if created:
                conn.commit()
                log.info("Created %d task(s) from critical bugs", created)
            return created
        except Exception as e:
            log.error("Failed to create tasks from bugs: %s", e)
            return 0
        finally:
            conn.close()


# ── Standalone Runner ─────────────────────────────────────────────────────────

def main():
    """Run Bug Hunter — one-shot or daemon mode."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # Ensure migration
    try:
        from core.db_migrate import run_migrations
        run_migrations()
    except Exception as e:
        log.warning("Migration check failed: %s", e)

    hunter = BugHunter()
    daemon = "--daemon" in sys.argv

    if daemon:
        log.info("Bug Hunter daemon started (10min interval)")
        while True:
            try:
                hunter.scan()
                hunter.send_telegram_digest()
            except Exception as e:
                log.error("Scan error: %s", e)
            time.sleep(600)  # 10 minutes
    else:
        result = hunter.scan()
        print(json.dumps(result, indent=2))
        digest = hunter.get_digest()
        if digest:
            print("\n" + digest)


if __name__ == "__main__":
    main()
