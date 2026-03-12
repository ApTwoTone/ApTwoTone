#!/usr/bin/env python3
"""Generate a call-first booking hunt list for SFV/LA outreach."""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.email_queue_manager import evaluate_vendor_candidate
from core.outreach_targeting import (
    booking_priority_score,
    booking_priority_sort_key,
    call_opening_line,
    has_phone,
    is_fast_booking_category,
    next_step_hint,
    recommended_outreach_angle,
)

DB_PATH = Path.home() / ".nexus" / "memory.db"
OUTPUT_DIR = Path("output") / "booking_hunt"


def get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_candidates(limit: int):
    conn = get_db()
    rows = conn.execute(
        """
        SELECT v.id, v.name, v.category, v.city, v.contact_name, v.phone, v.email,
               v.outreach_status, v.referral_score, v.phone_valid, v.email_valid,
               v.website_status, v.website, v.campaign_quality, v.source, v.notes,
               v.contact_name_source, v.contact_name_confidence, v.contact_name_verified,
               (
                   SELECT MAX(timestamp)
                   FROM outbound_log ol
                   WHERE ol.channel = 'email'
                     AND lower(ol.recipient) = lower(v.email)
                     AND ol.result IN ('allowed', 'sent')
               ) AS last_email_at
        FROM vendors v
        WHERE v.campaign_eligible = 1
          AND COALESCE(v.phone, '') != ''
          AND COALESCE(v.name, '') != ''
          AND COALESCE(v.outreach_status, 'none') NOT IN (
              'replied', 'bounced', 'blacklisted', 'booked', 'converted', 'closed', 'do_not_contact'
          )
          AND (
              lower(v.category) LIKE '%venue%' OR
              lower(v.category) LIKE '%planner%' OR
              lower(v.category) LIKE '%rental%' OR
              lower(v.category) LIKE '%cater%' OR
              lower(v.category) IN ('banquet_hall', 'party_rental', 'tent_rental', 'event_planner', 'wedding_planner', 'quinceanera_venue', 'country_club', 'community_center', 'church', 'church_hall', 'hotel_venue', 'winery', 'winery_venue')
          )
        ORDER BY v.referral_score DESC, v.campaign_quality DESC, v.id ASC
        LIMIT ?
        """,
        (max(200, limit * 15),),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def build_ranked_rows(limit: int):
    ranked = []
    for vendor in fetch_candidates(limit):
        decision = evaluate_vendor_candidate(vendor)
        if not is_fast_booking_category(vendor.get("category")):
            continue
        if not has_phone(vendor):
            continue
        if "category_hard_block" in decision.get("quality_flags", []):
            continue
        if "low_intent" in decision.get("quality_flags", []):
            continue
        if int(decision.get("quality_score", 0)) < 45:
            continue

        vendor["priority_score"] = booking_priority_score(vendor, decision)
        vendor["quality_score"] = int(decision.get("quality_score", 0))
        vendor["outreach_angle"] = recommended_outreach_angle(vendor)
        vendor["call_opening"] = call_opening_line(vendor)
        vendor["next_step"] = next_step_hint(vendor)
        vendor["generic_inbox"] = "yes" if decision.get("generic_inbox") else "no"
        ranked.append(vendor)

    ranked.sort(key=lambda vendor: booking_priority_sort_key(vendor, {"quality_score": vendor["quality_score"], "generic_inbox": vendor["generic_inbox"] == "yes"}))
    return ranked[:limit]


def write_csv(rows, path: Path):
    fieldnames = [
        "rank",
        "priority_score",
        "quality_score",
        "name",
        "category",
        "city",
        "contact_name",
        "phone",
        "email",
        "outreach_status",
        "last_email_at",
        "generic_inbox",
        "outreach_angle",
        "call_opening",
        "next_step",
    ]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for idx, row in enumerate(rows, start=1):
            writer.writerow(
                {
                    "rank": idx,
                    "priority_score": row.get("priority_score", 0),
                    "quality_score": row.get("quality_score", 0),
                    "name": row.get("name", ""),
                    "category": row.get("category", ""),
                    "city": row.get("city", ""),
                    "contact_name": row.get("contact_name", ""),
                    "phone": row.get("phone", ""),
                    "email": row.get("email", ""),
                    "outreach_status": row.get("outreach_status", ""),
                    "last_email_at": row.get("last_email_at", ""),
                    "generic_inbox": row.get("generic_inbox", ""),
                    "outreach_angle": row.get("outreach_angle", ""),
                    "call_opening": row.get("call_opening", ""),
                    "next_step": row.get("next_step", ""),
                }
            )


def write_markdown(rows, path: Path):
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M %Z")
    lines = [
        "# SFV/LA Booking Hunt",
        "",
        f"Generated: {generated_at}",
        "",
        "Call these first. Goal: get to the event or vendor-list decision maker and ask the backup-vendor question directly.",
        "",
        "| Rank | Business | Category | City | Phone | Email | Status | Next Move |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for idx, row in enumerate(rows, start=1):
        next_move = row.get("next_step", "").replace("|", "/")
        lines.append(
            "| {rank} | {name} | {category} | {city} | {phone} | {email} | {status} | {next_move} |".format(
                rank=idx,
                name=row.get("name", ""),
                category=row.get("category", ""),
                city=row.get("city", ""),
                phone=row.get("phone", ""),
                email=row.get("email", ""),
                status=row.get("outreach_status", ""),
                next_move=next_move,
            )
        )

    lines.extend(
        [
            "",
            "## Talk Track",
            "",
        ]
    )

    for idx, row in enumerate(rows[:15], start=1):
        lines.extend(
            [
                f"### {idx}. {row.get('name', '')}",
                "",
                f"- Angle: {row.get('outreach_angle', '')}",
                f"- Opener: {row.get('call_opening', '')}",
                f"- Next step: {row.get('next_step', '')}",
                "",
            ]
        )

    path.write_text("\n".join(lines).strip() + "\n")


def main():
    parser = argparse.ArgumentParser(description="Generate an SFV/LA booking hunt lead pack.")
    parser.add_argument("--count", type=int, default=40, help="Number of ranked leads to export")
    args = parser.parse_args()

    rows = build_ranked_rows(max(1, int(args.count)))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / "sfv_la_call_list_latest.csv"
    md_path = OUTPUT_DIR / "sfv_la_call_list_latest.md"
    write_csv(rows, csv_path)
    write_markdown(rows, md_path)

    print("Generated %d leads" % len(rows))
    print(csv_path)
    print(md_path)
    for idx, row in enumerate(rows[:10], start=1):
        print(
            "%2d. %s | %s | %s | %s | %s"
            % (
                idx,
                row.get("name", ""),
                row.get("category", ""),
                row.get("city", ""),
                row.get("phone", ""),
                row.get("email", ""),
            )
        )


if __name__ == "__main__":
    main()
