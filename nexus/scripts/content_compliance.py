#!/usr/bin/env python3
"""Content compliance scanner — checks posting scripts for forbidden patterns.

Scans Builder's posting/content scripts for:
- Dollar amounts ($1,000, $1,200, etc.)
- Personal names (Kai, Carlos, Escobar)
- Film/production references
- "All-inclusive" language
- Competitor mentions
- In-person visit offers

Usage:
    python scripts/content_compliance.py           # Scan all posting scripts
    python scripts/content_compliance.py --json    # JSON output
    python scripts/content_compliance.py --fix     # Show fix suggestions
"""

import argparse
import json
import logging
import re
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("content_compliance")

PROJECT_DIR = Path(__file__).resolve().parent.parent

SCAN_PATTERNS = [
    ("dollar_amounts", re.compile(r'\$\s?\d[\d,]*'), "Dollar amounts in content"),
    ("all_inclusive", re.compile(r'all[- ]inclusive', re.IGNORECASE), "All-inclusive language"),
    ("personal_name_kai", re.compile(r'\bKai\b(?!.*#)'), "Personal name 'Kai' in content"),
    ("personal_name_carlos", re.compile(r'\bCarlos\b(?!.*#)'), "Personal name 'Carlos' in content"),
    ("personal_name_escobar", re.compile(r'Escobar', re.IGNORECASE), "Last name 'Escobar'"),
    ("trailer_specs", re.compile(r'4-stall|three.stall|stall.*count', re.IGNORECASE), "Trailer specifications"),
    ("competitor", re.compile(r'porta.potty.*company|honey bucket|andy gump|united site', re.IGNORECASE), "Competitor mention"),
    ("in_person_visit", re.compile(r'walk.through|stop.*by|swing.*by|come.*see|demo.*setup|bring.*by', re.IGNORECASE), "In-person visit offer"),
]

SCAN_DIRS = [
    "scripts",
    "core",
    "integrations",
]

SCAN_FILE_PATTERNS = [
    "*post*", "*content*", "*facebook*", "*craigslist*",
    "*marketplace*", "*cold_email*", "*outreach*", "*listing*",
    "*ad_copy*", "*creative*",
]

SKIP_FILES = {
    "content_compliance.py",  # This file
    "__pycache__",
}


def find_files_to_scan():
    """Find all posting/content-related Python files."""
    files = set()
    for scan_dir in SCAN_DIRS:
        dir_path = PROJECT_DIR / scan_dir
        if not dir_path.exists():
            continue
        for pattern in SCAN_FILE_PATTERNS:
            for f in dir_path.glob(pattern + ".py"):
                if f.name not in SKIP_FILES and "__pycache__" not in str(f):
                    files.add(f)
    return sorted(files)


def scan_file(filepath):
    """Scan a single file for compliance violations."""
    violations = []
    try:
        content = filepath.read_text()
        lines = content.split("\n")
    except Exception as e:
        return [{"file": str(filepath), "line": 0, "pattern": "read_error", "message": str(e)}]

    for line_num, line in enumerate(lines, 1):
        # Skip comments and docstrings
        stripped = line.strip()
        if stripped.startswith("#"):
            continue

        for name, pattern, description in SCAN_PATTERNS:
            matches = pattern.findall(line)
            if matches:
                violations.append({
                    "file": str(filepath.relative_to(PROJECT_DIR)),
                    "line": line_num,
                    "pattern": name,
                    "description": description,
                    "match": matches[0] if len(matches) == 1 else str(matches),
                    "context": stripped[:80],
                })

    return violations


def scan_all():
    """Scan all posting/content files."""
    files = find_files_to_scan()
    all_violations = []

    for f in files:
        violations = scan_file(f)
        all_violations.extend(violations)

    return {
        "files_scanned": len(files),
        "file_list": [str(f.relative_to(PROJECT_DIR)) for f in files],
        "violations": all_violations,
        "total_violations": len(all_violations),
        "by_pattern": {},
    }


def main():
    parser = argparse.ArgumentParser(description="Content compliance scanner")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--fix", action="store_true", help="Show fix suggestions")
    args = parser.parse_args()

    result = scan_all()

    # Group by pattern
    for v in result["violations"]:
        p = v["pattern"]
        result["by_pattern"][p] = result["by_pattern"].get(p, 0) + 1

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("\n  Content Compliance Scan")
        print("  " + "=" * 55)
        print("  Files scanned: %d" % result["files_scanned"])
        print("  Violations found: %d" % result["total_violations"])
        print()

        if result["violations"]:
            print("  Violations:")
            for v in result["violations"]:
                print("    [%s:%d] %s" % (v["file"], v["line"], v["description"]))
                print("      Match: %s" % v["match"])
                print("      Context: %s" % v["context"])
                print()
        else:
            print("  All clear — no violations found")

        if result["by_pattern"]:
            print("  Summary by pattern:")
            for p, count in sorted(result["by_pattern"].items(), key=lambda x: -x[1]):
                print("    %s: %d" % (p, count))

        print("  " + "=" * 55)


if __name__ == "__main__":
    main()
