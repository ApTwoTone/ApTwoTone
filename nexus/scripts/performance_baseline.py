#!/usr/bin/env python3
"""Performance baseline — benchmarks API endpoints and DB operations.

Usage:
    python scripts/performance_baseline.py              # Run benchmarks
    python scripts/performance_baseline.py --json       # JSON output
    python scripts/performance_baseline.py --report     # Append to PERFORMANCE_REPORT.md
"""

import argparse
import json
import logging
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("perf_baseline")

DB_PATH = Path.home() / ".nexus" / "memory.db"
SERVER_PORT = 7860
REPORT_PATH = Path(__file__).resolve().parent.parent / "PERFORMANCE_REPORT.md"

ENDPOINTS = [
    "/api/health",
    "/api/vendors",
    "/api/vendors?limit=10",
    "/api/leads",
    "/api/agents/status",
    "/api/token-budget",
    "/api/memory",
    "/api/system/health",
    "/api/facebook/campaigns",
]

DB_QUERIES = [
    ("vendor_count", "SELECT COUNT(*) FROM vendors"),
    ("lead_count", "SELECT COUNT(*) FROM leads"),
    ("outbound_log_count", "SELECT COUNT(*) FROM outbound_log"),
    ("blocklist_count", "SELECT COUNT(*) FROM contact_blocklist"),
    ("audit_trail_count", "SELECT COUNT(*) FROM audit_trail"),
    ("token_usage_count", "SELECT COUNT(*) FROM token_usage"),
]


def benchmark_endpoint(path: str) -> dict:
    """Benchmark a single API endpoint."""
    url = f"http://localhost:{SERVER_PORT}{path}"
    try:
        req = urllib.request.Request(url, method="GET")
        start = time.time()
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read()
            elapsed = time.time() - start
            return {
                "endpoint": path,
                "status": resp.getcode(),
                "latency_ms": round(elapsed * 1000),
                "size_bytes": len(body),
                "result": "ok",
            }
    except urllib.error.HTTPError as e:
        elapsed = time.time() - start
        return {
            "endpoint": path,
            "status": e.code,
            "latency_ms": round(elapsed * 1000),
            "size_bytes": 0,
            "result": "http_error",
        }
    except Exception as e:
        return {
            "endpoint": path,
            "status": None,
            "latency_ms": None,
            "size_bytes": 0,
            "result": f"error: {str(e)[:50]}",
        }


def benchmark_db() -> list:
    """Benchmark database queries."""
    if not DB_PATH.exists():
        return [{"query": "n/a", "result": "db_missing"}]

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA query_only = ON")
    results = []

    for name, sql in DB_QUERIES:
        try:
            start = time.time()
            row = conn.execute(sql).fetchone()
            elapsed = time.time() - start
            results.append({
                "query": name,
                "latency_ms": round(elapsed * 1000, 2),
                "result": row[0] if row else None,
            })
        except Exception as e:
            results.append({
                "query": name,
                "latency_ms": None,
                "result": f"error: {str(e)[:50]}",
            })

    conn.close()
    return results


def run_baseline() -> dict:
    """Run full performance baseline."""
    log.info("Running performance baseline...")

    # API endpoints
    api_results = []
    for ep in ENDPOINTS:
        log.info("  Testing %s...", ep)
        api_results.append(benchmark_endpoint(ep))

    # DB queries
    db_results = benchmark_db()

    # Summary
    working = [r for r in api_results if r["status"] == 200]
    avg_latency = (sum(r["latency_ms"] for r in working) / len(working)) if working else 0
    slow = [r for r in working if r["latency_ms"] > 100]

    return {
        "timestamp": datetime.now().isoformat(),
        "api_endpoints": api_results,
        "db_queries": db_results,
        "summary": {
            "endpoints_tested": len(api_results),
            "endpoints_ok": len(working),
            "endpoints_error": len(api_results) - len(working),
            "avg_latency_ms": round(avg_latency),
            "slow_endpoints": len(slow),
            "db_queries_tested": len(db_results),
        },
    }


def write_report(baseline: dict):
    """Append baseline results to PERFORMANCE_REPORT.md."""
    lines = [
        f"\n\n## Performance Baseline — {datetime.now().strftime('%Y-%m-%d %H:%M PT')}",
        "",
        "### API Endpoints",
        "",
        "| Endpoint | Status | Latency | Size |",
        "|----------|--------|---------|------|",
    ]
    for r in baseline["api_endpoints"]:
        lines.append(f"| {r['endpoint']} | {r['status']} | {r['latency_ms']}ms | {r['size_bytes']}B |")

    lines.extend(["", "### Database Queries", "",
                   "| Query | Latency | Result |",
                   "|-------|---------|--------|"])
    for r in baseline["db_queries"]:
        lines.append(f"| {r['query']} | {r['latency_ms']}ms | {r['result']} |")

    s = baseline["summary"]
    lines.extend(["",
                   f"### Summary",
                   f"- Endpoints OK: {s['endpoints_ok']}/{s['endpoints_tested']}",
                   f"- Average latency: {s['avg_latency_ms']}ms",
                   f"- Slow endpoints (>100ms): {s['slow_endpoints']}",
                   "", "---"])

    with open(REPORT_PATH, "a") as f:
        f.write("\n".join(lines))
    log.info("Report written to %s", REPORT_PATH)


def main():
    parser = argparse.ArgumentParser(description="Performance baseline")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--report", action="store_true", help="Append to PERFORMANCE_REPORT.md")
    args = parser.parse_args()

    baseline = run_baseline()

    if args.json:
        print(json.dumps(baseline, indent=2))
    else:
        print(f"\n  Performance Baseline — {baseline['timestamp']}")
        print(f"  {'=' * 60}")

        print(f"\n  API Endpoints:")
        for r in baseline["api_endpoints"]:
            icon = "OK" if r["status"] == 200 else str(r["status"] or "ERR")
            lat = f"{r['latency_ms']}ms" if r["latency_ms"] is not None else "N/A"
            print(f"    [{icon:>4}] {r['endpoint']:<35} {lat:>6} {r['size_bytes']:>8}B")

        print(f"\n  Database Queries:")
        for r in baseline["db_queries"]:
            lat = f"{r['latency_ms']}ms" if r["latency_ms"] is not None else "N/A"
            print(f"    {r['query']:<25} {lat:>8}  result={r['result']}")

        s = baseline["summary"]
        print(f"\n  Summary: {s['endpoints_ok']}/{s['endpoints_tested']} OK, "
              f"avg {s['avg_latency_ms']}ms, {s['slow_endpoints']} slow")
        print(f"  {'=' * 60}")

    if args.report:
        write_report(baseline)


if __name__ == "__main__":
    main()
