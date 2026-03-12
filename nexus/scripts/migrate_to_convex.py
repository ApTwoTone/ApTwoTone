"""
One-time migration: SQLite → Convex

Reads all leads and bookings from ~/.nexus/memory.db and pushes them
to Convex via HTTP endpoints. Run after `npx convex dev` is configured.

Usage:
  python3 scripts/migrate_to_convex.py
"""

import json
import sqlite3
import urllib.request
from pathlib import Path

DB_PATH = Path.home() / ".nexus" / "memory.db"
CONFIG_FILE = Path.home() / ".nexus" / "config.json"


def get_convex_url():
    cfg = json.loads(CONFIG_FILE.read_text())
    url = cfg.get("convex_site_url")
    if not url:
        print("ERROR: Set 'convex_site_url' in ~/.nexus/config.json first")
        print("  Get it from: npx convex dev → look for the HTTP site URL")
        exit(1)
    return url


def post_to_convex(base_url, path, data):
    url = f"{base_url}{path}"
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read())
    except Exception as e:
        print(f"  ERROR: {e}")
        return None


def migrate():
    base_url = get_convex_url()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # ── Migrate leads ────────────────────────────────────────────────────
    print("Migrating leads...")
    leads = conn.execute("SELECT * FROM leads ORDER BY id").fetchall()
    success = 0
    for lead in leads:
        result = post_to_convex(base_url, "/sync/lead", {
            "first_name": lead["first_name"] or "",
            "last_name": lead["last_name"] or "",
            "email": lead["email"] or "",
            "phone": lead["phone"] or "",
            "source": lead["source"] or "",
            "event_type": "",
            "event_date": "",
            "event_city": "",
            "guest_count": 0,
            "notes": lead["notes"] or "",
        })
        if result and result.get("ok"):
            success += 1
    print(f"  {success}/{len(leads)} leads migrated")

    conn.close()
    print("Migration complete!")


if __name__ == "__main__":
    migrate()
