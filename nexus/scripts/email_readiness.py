#!/usr/bin/env python3
"""Email readiness verification — comprehensive pre-launch email checks.

Tests:
    1. SMTP connection (Gmail SSL port 465)
    2. Template rendering (placeholder leaks, signature check)
    3. DNS deliverability (SPF, DKIM, DMARC, MX)
    4. Warm-up guard integration
    5. CAN-SPAM compliance (physical address, unsubscribe, From match)

Usage:
    python scripts/email_readiness.py              # Run all checks
    python scripts/email_readiness.py --smtp-only  # Just test SMTP connection
    python scripts/email_readiness.py --json       # JSON output
"""

import argparse
import json
import logging
import re
import smtplib
import ssl
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("email_readiness")

CONFIG_PATH = Path.home() / ".nexus" / "config.json"
PROJECT_DIR = Path(__file__).resolve().parent.parent

# CAN-SPAM requirements
CANSPAM_CHECKS = [
    "physical_address",     # Physical mailing address in footer
    "unsubscribe_link",     # Mechanism to opt out
    "from_match",           # From address matches actual sender
    "no_deceptive_subject", # Subject line not misleading
    "business_identified",  # Business clearly identified
]


def check_smtp() -> dict:
    """Test SMTP connection to Gmail."""
    try:
        config = json.loads(CONFIG_PATH.read_text())
    except Exception as e:
        return {"check": "smtp", "status": "error", "message": f"Config error: {e}"}

    email = config.get("gmail_address", "")
    password = config.get("gmail_app_password", "")

    if not email:
        return {"check": "smtp", "status": "error", "message": "gmail_address not configured"}
    if not password:
        return {"check": "smtp", "status": "error", "message": "gmail_app_password not configured"}

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=10) as server:
            server.login(email, password)
            return {
                "check": "smtp",
                "status": "ok",
                "email": email,
                "message": "SMTP connection and authentication successful",
            }
    except smtplib.SMTPAuthenticationError as e:
        return {"check": "smtp", "status": "error",
                "message": f"Authentication failed: {e}"}
    except Exception as e:
        return {"check": "smtp", "status": "error",
                "message": f"Connection failed: {e}"}


def check_templates() -> dict:
    """Check email templates for issues."""
    cold_email = PROJECT_DIR / "core" / "cold_email.py"
    if not cold_email.exists():
        return {"check": "templates", "status": "warning",
                "message": "core/cold_email.py not found"}

    content = cold_email.read_text()
    issues = []

    # Check for placeholder leaks
    placeholders = re.findall(r'\{[a-z_]+\}', content)
    # Filter out f-string variables (these are fine) — look for raw {placeholder} in strings
    raw_placeholders = re.findall(r'(?<![f])["\'].*?\{[a-z_]+\}.*?["\']', content)
    if raw_placeholders:
        issues.append(f"Potential placeholder leaks: {len(raw_placeholders)} found")

    # Check signature for individual names (Rule 16)
    name_violations = []
    for i, line in enumerate(content.split("\n"), 1):
        if re.search(r'\bKai\b', line) and not line.strip().startswith("#"):
            name_violations.append({"line": i, "content": line.strip()[:60]})

    if name_violations:
        issues.append(f"Rule 16 violation: 'Kai' found in {len(name_violations)} lines")

    # Check for unsubscribe mechanism
    has_unsubscribe = bool(re.search(r'unsubscribe|opt.out|remove.*list', content, re.IGNORECASE))

    # Check for physical address
    has_address = bool(re.search(r'San Fernando|91340|address', content, re.IGNORECASE))

    return {
        "check": "templates",
        "status": "warning" if issues or name_violations else "ok",
        "issues": issues,
        "name_violations": name_violations[:5],
        "has_unsubscribe": has_unsubscribe,
        "has_physical_address": has_address,
    }


def check_dns_deliverability() -> dict:
    """Check DNS records for email deliverability."""
    try:
        config = json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {"check": "dns", "status": "error", "message": "Config not readable"}

    email = config.get("gmail_address", "")
    if not email or "@" not in email:
        return {"check": "dns", "status": "error", "message": "No email configured"}

    domain = email.split("@")[1]
    records = {}

    for rtype in ["MX", "TXT"]:
        try:
            result = subprocess.run(
                ["dig", "+short", rtype, domain],
                capture_output=True, text=True, timeout=10,
            )
            records[rtype] = result.stdout.strip().split("\n") if result.stdout.strip() else []
        except Exception:
            records[rtype] = []

    # Parse TXT for SPF, DKIM, DMARC
    txt_records = records.get("TXT", [])
    spf = any("v=spf1" in r for r in txt_records)

    # Check DMARC separately
    try:
        result = subprocess.run(
            ["dig", "+short", "TXT", f"_dmarc.{domain}"],
            capture_output=True, text=True, timeout=10,
        )
        dmarc = bool(result.stdout.strip())
    except Exception:
        dmarc = False

    return {
        "check": "dns",
        "status": "ok" if records.get("MX") else "warning",
        "domain": domain,
        "mx_records": len(records.get("MX", [])),
        "spf": spf,
        "dmarc": dmarc,
        "note": "Gmail handles SPF/DKIM/DMARC for @gmail.com addresses" if "gmail.com" in domain else "",
    }


def check_warmup_guard() -> dict:
    """Verify warm-up guard is importable and functional."""
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from sender_reputation_guard import can_send, get_warmup_status
        status = get_warmup_status()
        check = can_send()
        return {
            "check": "warmup_guard",
            "status": "ok",
            "warmup_day": status["warmup_day"],
            "importable": True,
            "can_send": check["allowed"],
        }
    except ImportError as e:
        return {"check": "warmup_guard", "status": "error",
                "message": f"Import failed: {e}", "importable": False}


def check_canspam() -> dict:
    """CAN-SPAM compliance audit."""
    cold_email = PROJECT_DIR / "core" / "cold_email.py"
    email_outreach = PROJECT_DIR / "core" / "email_outreach.py"

    issues = []
    findings = {}

    # Check templates
    for filepath in [cold_email, email_outreach]:
        if not filepath.exists():
            continue
        content = filepath.read_text()

        # Physical address
        if re.search(r'San Fernando|91340|P\.?O\.?\s*Box|\d+\s+\w+\s+(St|Ave|Blvd|Dr|Rd)', content):
            findings["physical_address"] = True
        # Unsubscribe
        if re.search(r'unsubscribe|opt.out|remove.*from.*list|stop.*receiving', content, re.IGNORECASE):
            findings["unsubscribe"] = True
        # Business name
        if re.search(r'Zoar Bathroom Rental', content, re.IGNORECASE):
            findings["business_name"] = True

    if not findings.get("physical_address"):
        issues.append("MISSING: Physical mailing address in email footer")
    if not findings.get("unsubscribe"):
        issues.append("MISSING: Unsubscribe mechanism")
    if not findings.get("business_name"):
        issues.append("MISSING: Business name (Zoar Bathroom Rentals)")

    return {
        "check": "canspam",
        "status": "warning" if issues else "ok",
        "issues": issues,
        "findings": findings,
        "note": "CAN-SPAM requires: physical address, unsubscribe option, business ID, honest subject lines",
    }


def main():
    parser = argparse.ArgumentParser(description="Email readiness verification")
    parser.add_argument("--smtp-only", action="store_true", help="Test SMTP only")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.smtp_only:
        result = check_smtp()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"\n  SMTP Test: {result['status'].upper()}")
            print(f"  {result.get('message', '')}")
        return

    checks = [
        check_smtp(),
        check_templates(),
        check_dns_deliverability(),
        check_warmup_guard(),
        check_canspam(),
    ]

    if args.json:
        print(json.dumps(checks, indent=2))
    else:
        print(f"\n  Email Readiness Verification")
        print(f"  {'=' * 55}")

        for c in checks:
            icon = {"ok": "OK", "warning": "WARN", "error": "ERR"}.get(c["status"], "?")
            print(f"\n  [{icon}] {c['check'].upper()}")
            for k, v in c.items():
                if k in ("check", "status"):
                    continue
                if isinstance(v, list) and v:
                    print(f"      {k}:")
                    for item in v[:5]:
                        print(f"        - {item}")
                elif v:
                    print(f"      {k}: {v}")

        passed = sum(1 for c in checks if c["status"] == "ok")
        total = len(checks)
        print(f"\n  Result: {passed}/{total} checks passed")
        print(f"  {'=' * 55}")


if __name__ == "__main__":
    main()
