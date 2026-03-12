#!/usr/bin/env python3
"""One-time script to register Higgsfield ad creatives for Notion embedding.

Images are served from the nexus FastAPI server (/ad_creatives route)
via the active ngrok tunnel. Splits 31 images into 4 sets (A/B/C/D)
and saves the public URLs to ~/.nexus/config.json as notion_image_sets.

The nexus server must be running (port 7860) and ngrok must be active.

Usage:
    python scripts/upload_notion_images.py          # register images
    python scripts/upload_notion_images.py --dry-run  # preview only
    python scripts/upload_notion_images.py --status   # show saved sets
"""

import argparse
import json
import logging
import sys
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("upload_images")

CONFIG_PATH = Path.home() / ".nexus" / "config.json"
AD_CREATIVES_DIR = Path.home() / "Documents" / "ad_creatives"
IMAGES_PER_SET = 8  # A=8, B=8, C=8, D=7


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    return json.loads(CONFIG_PATH.read_text())


def save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def get_ngrok_url() -> str:
    """Get the current ngrok public URL from the local ngrok API."""
    try:
        resp = urllib.request.urlopen("http://localhost:4040/api/tunnels", timeout=5)
        data = json.loads(resp.read())
        tunnels = data.get("tunnels", [])
        for t in tunnels:
            url = t.get("public_url", "")
            if url.startswith("https://"):
                return url.rstrip("/")
        log.warning("No HTTPS ngrok tunnel found")
        return ""
    except Exception as e:
        log.warning("Could not reach ngrok API: %s", e)
        return ""


def get_image_files() -> list:
    """Return sorted list of image file paths."""
    if not AD_CREATIVES_DIR.exists():
        log.error("Ad creatives directory not found: %s", AD_CREATIVES_DIR)
        sys.exit(1)
    exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    files = sorted([f for f in AD_CREATIVES_DIR.iterdir() if f.suffix.lower() in exts])
    log.info("Found %d image files", len(files))
    return files


def assign_sets(files: list) -> dict:
    """Split files into 4 photo sets A/B/C/D."""
    sets = {"A": [], "B": [], "C": [], "D": []}
    labels = ["A", "B", "C", "D"]
    for i, f in enumerate(files):
        label = labels[min(i // IMAGES_PER_SET, 3)]
        sets[label].append(f)
    return sets


def main():
    parser = argparse.ArgumentParser(description="Register ad creatives for Notion")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no save")
    parser.add_argument("--status", action="store_true", help="Show saved image sets")
    args = parser.parse_args()

    cfg = load_config()

    if args.status:
        sets = cfg.get("notion_image_sets", {})
        if not sets:
            print("No image sets saved. Run without --status to register.")
            return
        for label, urls in sets.items():
            print(f"Set {label}: {len(urls)} images")
            for url in urls[:2]:
                print(f"  {url}")
            if len(urls) > 2:
                print(f"  ... and {len(urls) - 2} more")
        return

    ngrok_url = get_ngrok_url()
    if not ngrok_url:
        print("\nERROR: ngrok is not running or has no HTTPS tunnel.")
        print("Start it with: launchctl start com.nexus.ngrok")
        sys.exit(1)

    log.info("Using ngrok URL: %s", ngrok_url)

    files = get_image_files()
    image_sets = assign_sets(files)

    print(f"\nImage sets ({len(files)} files, ngrok: {ngrok_url}):")
    notion_sets = {"A": [], "B": [], "C": [], "D": []}
    for label, set_files in image_sets.items():
        print(f"  Set {label}: {len(set_files)} images")
        for f in set_files:
            url = f"{ngrok_url}/ad_creatives/{f.name}"
            notion_sets[label].append(url)
            print(f"    {url}")

    if args.dry_run:
        print("\nDry run — nothing saved.")
        return

    cfg["notion_image_sets"] = notion_sets
    cfg["notion_image_base_url"] = ngrok_url
    save_config(cfg)

    total = sum(len(v) for v in notion_sets.values())
    print(f"\nSaved {total} image URLs to config (sets A/B/C/D).")
    print("Run notion_posting_scheduler.py to generate a board with embedded images.")


if __name__ == "__main__":
    main()
