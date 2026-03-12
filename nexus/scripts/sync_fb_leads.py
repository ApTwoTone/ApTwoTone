#!/usr/bin/env python3
"""
FB Lead Sync Daemon — Standalone launcher for launchd.

Runs core/fb_lead_sync.py on a schedule. Can run one-shot or as daemon.

Usage:
  python scripts/sync_fb_leads.py              # one-shot sync
  python scripts/sync_fb_leads.py --daemon     # poll every 5 min
  python scripts/sync_fb_leads.py --daemon --poll=10  # poll every 10 min
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.fb_lead_sync import main

if __name__ == "__main__":
    main()
