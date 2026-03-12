#!/usr/bin/env python3
"""Vendor Forensic Snapshot — Pre-purge evidence record.

Captures complete database state before vendor purge:
- Overall counts (2A)
- Category analysis (2B)
- Fabrication evidence (2C)
- Timeline analysis (2D)
- Outbound safety verification (2E)
- Rejection log analysis (2F)

Usage:
    python scripts/vendor_forensic_snapshot.py
    python scripts/vendor_forensic_snapshot.py --report   # Also append to AUDITOR_LOG.md
"""
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

# Ensure nexus root is importable
NEXUS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(NEXUS_ROOT))

DB_PATH = Path.home() / ".nexus" / "memory.db"
REJECTED_LOG = Path.home() / ".nexus" / "rejected_vendors.log"
SNAPSHOT_DIR = Path.home() / ".nexus"
AUDITOR_LOG = NEXUS_ROOT / "AUDITOR_LOG.md"
DB_HEALTH_REPORT = NEXUS_ROOT / "DATABASE_HEALTH_REPORT.md"

# Approved referral categories from CLAUDE.md
APPROVED_CATEGORIES = {
    "wedding planner", "event planner", "event coordinator",
    "quinceañera planner", "party rental", "event venue",
    "outdoor venue", "banquet hall", "catering company",
    "wedding venue", "construction company",
    "catering", "wedding_planner", "event_planner", "event_coordinator",
    "quinceañera_planner", "party_rental", "event_venue",
    "outdoor_venue", "banquet_hall", "catering_company",
    "wedding_venue", "construction_company",
    "dj", "photographer", "florist", "videographer",
    "tent_rental", "table_chair_rental", "lighting_rental",
}

# Known non-vendor entities
NON_VENDOR_ENTITIES = {
    "ups store", "walmart", "starbucks", "mcdonalds", "target",
    "costco", "home depot", "lowes", "walgreens", "cvs",
    "burger king", "taco bell", "subway", "pizza hut",
    "dominos", "wendys", "chick-fil-a", "popeyes",
}


def get_conn():
    """Read-only database connection."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


# ── 2A: Overall Counts ──────────────────────────────────────────────────────

def section_overall_counts(conn):
    c = conn.cursor()
    result = {}

    result["total"] = c.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]

    # By vetting_status
    rows = c.execute(
        "SELECT vetting_status, COUNT(*) as cnt FROM vendors GROUP BY vetting_status ORDER BY cnt DESC"
    ).fetchall()
    result["by_vetting_status"] = {r["vetting_status"]: r["cnt"] for r in rows}

    # By outreach_status
    rows = c.execute(
        "SELECT outreach_status, COUNT(*) as cnt FROM vendors GROUP BY outreach_status ORDER BY cnt DESC"
    ).fetchall()
    result["by_outreach_status"] = {r["outreach_status"]: r["cnt"] for r in rows}

    # By status
    rows = c.execute(
        "SELECT status, COUNT(*) as cnt FROM vendors GROUP BY status ORDER BY cnt DESC"
    ).fetchall()
    result["by_status"] = {r["status"]: r["cnt"] for r in rows}

    # By campaign_eligible
    result["campaign_eligible_1"] = c.execute(
        "SELECT COUNT(*) FROM vendors WHERE campaign_eligible = 1"
    ).fetchone()[0]
    result["campaign_eligible_0"] = c.execute(
        "SELECT COUNT(*) FROM vendors WHERE campaign_eligible = 0"
    ).fetchone()[0]

    # Contact info coverage
    result["with_email"] = c.execute(
        "SELECT COUNT(*) FROM vendors WHERE email != '' AND email IS NOT NULL"
    ).fetchone()[0]
    result["with_phone"] = c.execute(
        "SELECT COUNT(*) FROM vendors WHERE phone != '' AND phone IS NOT NULL"
    ).fetchone()[0]
    result["with_website"] = c.execute(
        "SELECT COUNT(*) FROM vendors WHERE website != '' AND website IS NOT NULL"
    ).fetchone()[0]
    result["with_all_three"] = c.execute(
        "SELECT COUNT(*) FROM vendors WHERE email != '' AND phone != '' AND website != ''"
    ).fetchone()[0]
    result["with_none"] = c.execute(
        "SELECT COUNT(*) FROM vendors WHERE (email = '' OR email IS NULL) "
        "AND (phone = '' OR phone IS NULL) AND (website = '' OR website IS NULL)"
    ).fetchone()[0]

    return result


# ── 2B: Category Analysis ───────────────────────────────────────────────────

def section_category_analysis(conn):
    c = conn.cursor()
    result = {}

    # Full category distribution
    rows = c.execute(
        "SELECT category, COUNT(*) as cnt FROM vendors GROUP BY category ORDER BY cnt DESC"
    ).fetchall()
    result["distribution"] = {r["category"]: r["cnt"] for r in rows}

    # Classify each category
    approved = {}
    unapproved = {}
    for r in rows:
        cat = r["category"]
        cat_lower = cat.lower().replace(" ", "_")
        if cat_lower in APPROVED_CATEGORIES or cat.lower() in APPROVED_CATEGORIES:
            approved[cat] = r["cnt"]
        else:
            unapproved[cat] = r["cnt"]

    result["approved_categories"] = approved
    result["approved_total"] = sum(approved.values())
    result["unapproved_categories"] = unapproved
    result["unapproved_total"] = sum(unapproved.values())

    return result


# ── 2C: Fabrication Evidence ─────────────────────────────────────────────────

def section_fabrication_evidence(conn):
    c = conn.cursor()
    result = {}

    # Phone numbers shared by 4+ vendors
    rows = c.execute(
        "SELECT phone, COUNT(*) as cnt FROM vendors "
        "WHERE phone != '' AND phone IS NOT NULL "
        "GROUP BY phone HAVING cnt >= 4 ORDER BY cnt DESC LIMIT 50"
    ).fetchall()
    result["duplicate_phones"] = [{"phone": r["phone"], "count": r["cnt"]} for r in rows]
    result["duplicate_phone_vendor_count"] = sum(r["cnt"] for r in rows)

    # Phone pattern analysis: 555 prefix
    count_555 = c.execute(
        "SELECT COUNT(*) FROM vendors WHERE phone LIKE '%555%'"
    ).fetchone()[0]
    result["phones_with_555"] = count_555

    # Phone pattern: repeated last 4 digits (1111, 2222, etc.)
    all_phones = c.execute(
        "SELECT id, name, phone, city FROM vendors WHERE phone != '' AND length(phone) >= 7"
    ).fetchall()
    repeated_pattern = []
    sequential_pattern = []
    for row in all_phones:
        digits = re.sub(r"\D", "", row["phone"])
        if len(digits) >= 4:
            last4 = digits[-4:]
            # Repeated: 1111, 2222, etc.
            if len(set(last4)) == 1:
                repeated_pattern.append({
                    "id": row["id"], "name": row["name"],
                    "phone": row["phone"], "city": row["city"]
                })
            # Sequential: 1234, 2345, 4321, etc.
            if last4 in ("1234", "2345", "3456", "4567", "5678", "6789",
                         "4321", "5432", "6543", "7654", "8765", "9876",
                         "0123", "3210"):
                sequential_pattern.append({
                    "id": row["id"], "name": row["name"],
                    "phone": row["phone"], "city": row["city"]
                })

    result["repeated_last4_count"] = len(repeated_pattern)
    result["repeated_last4_examples"] = repeated_pattern[:20]
    result["sequential_last4_count"] = len(sequential_pattern)
    result["sequential_last4_examples"] = sequential_pattern[:20]

    # Business names in 5+ cities
    rows = c.execute(
        "SELECT name, COUNT(DISTINCT city) as city_cnt, GROUP_CONCAT(DISTINCT city) as cities "
        "FROM vendors GROUP BY name HAVING city_cnt >= 5 ORDER BY city_cnt DESC LIMIT 30"
    ).fetchall()
    result["names_in_5plus_cities"] = [
        {"name": r["name"], "city_count": r["city_cnt"], "cities": r["cities"]}
        for r in rows
    ]

    # Non-vendor entities (chains, retail, etc.)
    non_vendor_matches = []
    for entity in NON_VENDOR_ENTITIES:
        cnt = c.execute(
            "SELECT COUNT(*) FROM vendors WHERE LOWER(name) LIKE ?",
            ("%" + entity + "%",)
        ).fetchone()[0]
        if cnt > 0:
            non_vendor_matches.append({"entity": entity, "count": cnt})
    result["non_vendor_entities"] = non_vendor_matches

    # Fake email patterns
    fake_domains = c.execute(
        "SELECT email, COUNT(*) as cnt FROM vendors "
        "WHERE email LIKE '%@example.com' OR email LIKE '%@test.com' "
        "OR email LIKE '%@fake.com' OR email LIKE '%@mail.com' "
        "OR email LIKE '%@email.com' "
        "GROUP BY email ORDER BY cnt DESC LIMIT 20"
    ).fetchall()
    result["fake_email_patterns"] = [{"email": r["email"], "count": r["cnt"]} for r in fake_domains]

    # Email domain distribution (top 30)
    rows = c.execute(
        "SELECT SUBSTR(email, INSTR(email, '@')+1) as domain, COUNT(*) as cnt "
        "FROM vendors WHERE email != '' AND email LIKE '%@%' "
        "GROUP BY domain ORDER BY cnt DESC LIMIT 30"
    ).fetchall()
    result["top_email_domains"] = [{"domain": r["domain"], "count": r["cnt"]} for r in rows]

    # Website pattern analysis: placeholder/fake
    fake_sites = c.execute(
        "SELECT COUNT(*) FROM vendors WHERE "
        "website LIKE '%example.com%' OR website LIKE '%test.com%' "
        "OR website LIKE '%placeholder%' OR website LIKE '% %' "
        "OR (website != '' AND website NOT LIKE '%.%')"
    ).fetchone()[0]
    result["fake_website_count"] = fake_sites

    return result


# ── 2D: Timeline Analysis ───────────────────────────────────────────────────

def section_timeline_analysis(conn):
    c = conn.cursor()
    result = {}

    # Time range
    first = c.execute("SELECT MIN(created_at) FROM vendors").fetchone()[0]
    last = c.execute("SELECT MAX(created_at) FROM vendors").fetchone()[0]
    result["first_vendor_created"] = first
    result["last_vendor_created"] = last

    # Pre-March-6 vendors (potentially legitimate)
    pre_march6 = c.execute(
        "SELECT COUNT(*) FROM vendors WHERE created_at < '2026-03-06'"
    ).fetchone()[0]
    result["created_before_march_6"] = pre_march6

    # March 6 vendors
    march6 = c.execute(
        "SELECT COUNT(*) FROM vendors WHERE created_at >= '2026-03-06' AND created_at < '2026-03-07'"
    ).fetchone()[0]
    result["created_march_6"] = march6

    # Inserts per hour on March 6
    rows = c.execute(
        "SELECT SUBSTR(created_at, 1, 13) as hour, COUNT(*) as cnt "
        "FROM vendors WHERE created_at >= '2026-03-06' "
        "GROUP BY hour ORDER BY hour"
    ).fetchall()
    result["inserts_per_hour"] = [{"hour": r["hour"], "count": r["cnt"]} for r in rows]

    # Inserts per day (last 7 days)
    rows = c.execute(
        "SELECT DATE(created_at) as day, COUNT(*) as cnt "
        "FROM vendors GROUP BY day ORDER BY day DESC LIMIT 7"
    ).fetchall()
    result["inserts_per_day"] = [{"day": r["day"], "count": r["cnt"]} for r in rows]

    # Source distribution
    rows = c.execute(
        "SELECT source, COUNT(*) as cnt FROM vendors GROUP BY source ORDER BY cnt DESC"
    ).fetchall()
    result["by_source"] = {r["source"]: r["cnt"] for r in rows}

    return result


# ── 2E: Outbound Safety Verification ────────────────────────────────────────

def section_outbound_safety(conn):
    c = conn.cursor()
    result = {}

    # Outbound log
    try:
        total_outbound = c.execute("SELECT COUNT(*) FROM outbound_log").fetchone()[0]
        result["outbound_log_total"] = total_outbound

        # By result
        rows = c.execute(
            "SELECT result, COUNT(*) as cnt FROM outbound_log GROUP BY result ORDER BY cnt DESC"
        ).fetchall()
        result["outbound_by_result"] = {r["result"]: r["cnt"] for r in rows}

        # Any sent to vendor emails?
        sent_to_vendors = c.execute(
            "SELECT ol.* FROM outbound_log ol "
            "INNER JOIN vendors v ON ol.recipient = v.email "
            "WHERE ol.result = 'sent' LIMIT 10"
        ).fetchall()
        result["sent_to_vendor_emails"] = len(sent_to_vendors)
        result["sent_to_vendor_details"] = [dict(r) for r in sent_to_vendors]

        # Recent outbound entries
        recent = c.execute(
            "SELECT * FROM outbound_log ORDER BY id DESC LIMIT 10"
        ).fetchall()
        result["recent_outbound"] = [dict(r) for r in recent]
    except sqlite3.OperationalError:
        result["outbound_log_total"] = 0
        result["outbound_log_error"] = "table not found"

    # Contact blocklist
    try:
        blocklist = c.execute("SELECT * FROM contact_blocklist WHERE active = 1").fetchall()
        result["blocklist_entries"] = [dict(r) for r in blocklist]
        result["blocklist_count"] = len(blocklist)
    except sqlite3.OperationalError:
        result["blocklist_count"] = 0
        result["blocklist_error"] = "table not found"

    # Vendors with outreach sent/replied
    try:
        sent = c.execute(
            "SELECT COUNT(*) FROM vendors WHERE outreach_status IN ('sent', 'replied')"
        ).fetchone()[0]
        result["vendors_with_outreach_sent"] = sent
    except sqlite3.OperationalError:
        result["vendors_with_outreach_sent"] = 0

    # Vendor outreach table
    try:
        vo_total = c.execute("SELECT COUNT(*) FROM vendor_outreach").fetchone()[0]
        result["vendor_outreach_total"] = vo_total
        rows = c.execute(
            "SELECT status, COUNT(*) as cnt FROM vendor_outreach GROUP BY status ORDER BY cnt DESC"
        ).fetchall()
        result["vendor_outreach_by_status"] = {r["status"]: r["cnt"] for r in rows}
    except sqlite3.OperationalError:
        result["vendor_outreach_total"] = 0

    return result


# ── 2F: Rejection Log Analysis ──────────────────────────────────────────────

def section_rejection_log():
    result = {}

    if not REJECTED_LOG.exists():
        # Try vendor_rejections table instead
        try:
            conn = get_conn()
            total = conn.execute("SELECT COUNT(*) FROM vendor_rejections").fetchone()[0]
            result["source"] = "vendor_rejections table"
            result["total_rejections"] = total

            rows = conn.execute(
                "SELECT reason, COUNT(*) as cnt FROM vendor_rejections "
                "GROUP BY reason ORDER BY cnt DESC LIMIT 20"
            ).fetchall()
            result["by_reason"] = {r["reason"]: r["cnt"] for r in rows}

            recent = conn.execute(
                "SELECT * FROM vendor_rejections ORDER BY rowid DESC LIMIT 20"
            ).fetchall()
            result["recent_rejections"] = [dict(r) for r in recent]
            conn.close()
        except Exception as e:
            result["source"] = "none"
            result["error"] = str(e)
            result["total_rejections"] = 0
        return result

    # Parse log file
    result["source"] = str(REJECTED_LOG)
    lines = REJECTED_LOG.read_text().strip().split("\n") if REJECTED_LOG.stat().st_size > 0 else []
    result["total_rejections"] = len(lines)

    reasons = Counter()
    for line in lines:
        # Try to extract reason from log line
        if "reason:" in line.lower():
            reason = line.split("reason:")[-1].strip()
            reasons[reason] += 1
        elif "|" in line:
            parts = line.split("|")
            if len(parts) >= 3:
                reasons[parts[2].strip()] += 1
        else:
            reasons["unknown"] += 1

    result["by_reason"] = dict(reasons.most_common(20))
    result["last_20_lines"] = lines[-20:] if lines else []

    return result


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    report_mode = "--report" in sys.argv

    print("=" * 60)
    print("VENDOR FORENSIC SNAPSHOT")
    print("=" * 60)
    print("Database:", DB_PATH)
    print("Timestamp:", datetime.now().isoformat())
    print()

    conn = get_conn()

    # Run all sections
    print("[2A] Overall counts...")
    counts = section_overall_counts(conn)

    print("[2B] Category analysis...")
    categories = section_category_analysis(conn)

    print("[2C] Fabrication evidence...")
    fabrication = section_fabrication_evidence(conn)

    print("[2D] Timeline analysis...")
    timeline = section_timeline_analysis(conn)

    print("[2E] Outbound safety verification...")
    outbound = section_outbound_safety(conn)

    conn.close()

    print("[2F] Rejection log analysis...")
    rejections = section_rejection_log()

    # Assemble snapshot
    db_size = DB_PATH.stat().st_size / (1024 * 1024)
    snapshot = {
        "timestamp": datetime.now().isoformat(),
        "db_path": str(DB_PATH),
        "db_size_mb": round(db_size, 1),
        "overall_counts": counts,
        "category_analysis": categories,
        "fabrication_evidence": fabrication,
        "timeline_analysis": timeline,
        "outbound_safety": outbound,
        "rejection_log": rejections,
    }

    # Save JSON
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = SNAPSHOT_DIR / ("vendor_forensic_snapshot_%s.json" % ts)
    out_path.write_text(json.dumps(snapshot, indent=2, default=str))
    print("\nSnapshot saved to:", out_path)

    # Print human-readable summary
    print("\n" + "=" * 60)
    print("FORENSIC SUMMARY")
    print("=" * 60)

    print("\n[2A] OVERALL COUNTS")
    print("  Total vendors:", counts["total"])
    print("  By vetting:", json.dumps(counts["by_vetting_status"], indent=4))
    print("  Campaign eligible:", counts["campaign_eligible_1"])
    print("  With email:", counts["with_email"])
    print("  With phone:", counts["with_phone"])
    print("  With website:", counts["with_website"])
    print("  With all three:", counts["with_all_three"])
    print("  With none:", counts["with_none"])

    print("\n[2B] CATEGORY ANALYSIS")
    print("  Approved categories: %d vendors" % categories["approved_total"])
    print("  Unapproved categories: %d vendors" % categories["unapproved_total"])
    print("  Top 10 categories:")
    for cat, cnt in list(categories["distribution"].items())[:10]:
        marker = "OK" if cat.lower().replace(" ", "_") in APPROVED_CATEGORIES or cat.lower() in APPROVED_CATEGORIES else "BAD"
        print("    [%s] %s: %d" % (marker, cat, cnt))

    print("\n[2C] FABRICATION EVIDENCE")
    print("  Phones shared by 4+ vendors: %d phones (%d vendors)" % (
        len(fabrication["duplicate_phones"]), fabrication["duplicate_phone_vendor_count"]))
    print("  Phones with 555: %d" % fabrication["phones_with_555"])
    print("  Repeated last-4 digits (1111 etc): %d" % fabrication["repeated_last4_count"])
    print("  Sequential last-4 (1234 etc): %d" % fabrication["sequential_last4_count"])
    print("  Names in 5+ cities: %d" % len(fabrication["names_in_5plus_cities"]))
    if fabrication["names_in_5plus_cities"]:
        for item in fabrication["names_in_5plus_cities"][:5]:
            print("    %s (%d cities): %s" % (item["name"], item["city_count"], item["cities"][:80]))
    print("  Non-vendor entities found: %d" % len(fabrication["non_vendor_entities"]))
    for item in fabrication["non_vendor_entities"]:
        print("    %s: %d vendors" % (item["entity"], item["count"]))
    print("  Fake email patterns: %d" % len(fabrication["fake_email_patterns"]))
    print("  Fake/bad websites: %d" % fabrication["fake_website_count"])

    print("\n[2D] TIMELINE")
    print("  First vendor:", timeline["first_vendor_created"])
    print("  Last vendor:", timeline["last_vendor_created"])
    print("  Created before March 6:", timeline["created_before_march_6"])
    print("  Created on March 6:", timeline["created_march_6"])
    print("  By source:", json.dumps(timeline["by_source"], indent=4))
    print("  Inserts per hour (March 6):")
    for item in timeline["inserts_per_hour"]:
        bar = "#" * min(int(item["count"] / 50), 40)
        print("    %s: %4d %s" % (item["hour"], item["count"], bar))

    print("\n[2E] OUTBOUND SAFETY")
    print("  Outbound log entries:", outbound.get("outbound_log_total", 0))
    print("  By result:", json.dumps(outbound.get("outbound_by_result", {}), indent=4))
    print("  Sent to vendor emails:", outbound.get("sent_to_vendor_emails", 0))
    print("  Blocklist entries:", outbound.get("blocklist_count", 0))
    print("  Vendors with outreach sent:", outbound.get("vendors_with_outreach_sent", 0))
    print("  Vendor outreach records:", outbound.get("vendor_outreach_total", 0))

    print("\n[2F] REJECTION LOG")
    print("  Source:", rejections.get("source", "unknown"))
    print("  Total rejections:", rejections.get("total_rejections", 0))
    if rejections.get("by_reason"):
        print("  Top reasons:")
        for reason, cnt in list(rejections["by_reason"].items())[:10]:
            print("    %s: %d" % (reason, cnt))

    # Acceptance rate
    total_vendors = counts["total"]
    total_rejections = rejections.get("total_rejections", 0)
    total_attempts = total_vendors + total_rejections
    if total_attempts > 0:
        acceptance_rate = total_vendors / total_attempts * 100
        print("  Acceptance rate: %.1f%% (%d accepted / %d attempted)" % (
            acceptance_rate, total_vendors, total_attempts))

    print("\n" + "=" * 60)
    print("SNAPSHOT COMPLETE — %s" % out_path)
    print("=" * 60)

    # Append to report files if --report
    if report_mode:
        summary = _build_summary(counts, categories, fabrication, timeline, outbound, rejections)
        _append_to_file(AUDITOR_LOG, summary, "TASK 2 — FORENSIC SNAPSHOT")
        _append_to_file(DB_HEALTH_REPORT, summary, "ROUND 7 — FORENSIC SNAPSHOT")
        print("\nAppended summary to AUDITOR_LOG.md and DATABASE_HEALTH_REPORT.md")


def _build_summary(counts, categories, fabrication, timeline, outbound, rejections):
    lines = []
    lines.append("Total vendors: %d" % counts["total"])
    lines.append("Vetting: %s" % json.dumps(counts["by_vetting_status"]))
    lines.append("Campaign eligible: %d" % counts["campaign_eligible_1"])
    lines.append("With email: %d | phone: %d | website: %d" % (
        counts["with_email"], counts["with_phone"], counts["with_website"]))
    lines.append("Approved category vendors: %d | Unapproved: %d" % (
        categories["approved_total"], categories["unapproved_total"]))
    lines.append("Fabrication: %d duplicate phones, %d with 555, %d repeated-digit, %d sequential" % (
        len(fabrication["duplicate_phones"]), fabrication["phones_with_555"],
        fabrication["repeated_last4_count"], fabrication["sequential_last4_count"]))
    lines.append("Names in 5+ cities: %d" % len(fabrication["names_in_5plus_cities"]))
    lines.append("Timeline: first=%s, last=%s" % (
        timeline["first_vendor_created"], timeline["last_vendor_created"]))
    lines.append("Pre-March-6 vendors: %d" % timeline["created_before_march_6"])
    lines.append("Outbound sent to vendors: %d" % outbound.get("sent_to_vendor_emails", 0))
    lines.append("Blocklist entries: %d" % outbound.get("blocklist_count", 0))
    lines.append("Total rejections: %d" % rejections.get("total_rejections", 0))
    return "\n".join(lines)


def _append_to_file(path, content, header):
    with open(path, "a") as f:
        f.write("\n\n## %s\n\nTIMESTAMP: %s\n\n```\n%s\n```\n" % (
            header, datetime.now().strftime("%Y-%m-%d %H:%M PT"), content))


if __name__ == "__main__":
    main()
