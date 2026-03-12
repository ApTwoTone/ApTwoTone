import sqlite3
import logging
import os
import sys
from pathlib import Path

# Add project root to sys.path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from core.vendor_scoring import compute_campaign_quality

# Logger Setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("mass_scoring")

def run_mass_scoring(db_path=None, batch_size=2000, min_eligible_score=40):
    """Compute and save campaign_quality for all vendors in the database."""
    path = db_path or (Path.home() / ".nexus" / "memory.db")
    if not os.path.exists(path):
        log.error(f"Database not found at {path}")
        return

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row

    # Get total count
    total = conn.execute("SELECT COUNT(*) FROM vendors").fetchone()[0]
    log.info(f"Starting mass scoring for {total} vendors...")

    processed = 0
    updated_count = 0

    while processed < total:
        # Fetch a batch
        rows = conn.execute(
            "SELECT id, name, email, website, phone, city, address, category, rating, review_count "
            "FROM vendors LIMIT ? OFFSET ?", (batch_size, processed)
        ).fetchall()

        if not rows:
            break

        updates = []
        for row in rows:
            vendor = dict(row)
            # Compute the refined quality score
            score = compute_campaign_quality(vendor)

            # eligible if score matches threshold AND has an email
            eligible = 1 if (score >= min_eligible_score and vendor.get("email")) else 0

            updates.append((score, eligible, row["id"]))

        if updates:
            conn.executemany(
                "UPDATE vendors SET campaign_quality = ?, campaign_eligible = ? WHERE id = ?",
                updates
            )
            conn.commit()
            updated_count += len(updates)

        processed += len(rows)
        if processed % 10000 == 0 or processed >= total:
            log.info(f"Progress: {processed}/{total} ({(processed/total)*100:.1f}%)")

    conn.close()
    log.info(f"Mass scoring complete! Updated {updated_count} records.")

if __name__ == "__main__":
    # Min quality matches the default in vendor_api.py
    run_mass_scoring(min_eligible_score=40)
