"""
FB Vendor Scraper — Configuration constants.
"""
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
SCRAPER_DIR = Path(__file__).parent
GROUPS_FILE = SCRAPER_DIR / "groups.json"
PENDING_FILE = SCRAPER_DIR / "pending_uploads.json"
LOG_FILE = SCRAPER_DIR / "scraper.log"
STATE_FILE = SCRAPER_DIR / "scraper_state.json"
DISCOVERED_FILE = SCRAPER_DIR / "discovered_groups.json"
BROWSER_DATA_DIR = Path.home() / ".nexus" / "fb_browser_data"

# ── Nexus Server ─────────────────────────────────────────────────────────────
# Server runs on this same Mac mini, so use localhost
import os
NEXUS_BASE_URL = os.environ.get("NEXUS_BASE_URL", "http://localhost:7860")
NEXUS_API_KEY = os.environ.get("NEXUS_API_KEY", "")

# ── Listener ─────────────────────────────────────────────────────────────────
LISTENER_PORT = 7899
ALLOWED_IPS = {"127.0.0.1", "::1", "localhost"}  # Add VPS IP here

# ── Browser (uses installed Google Chrome with persistent profile) ────────────
VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 800

# ── Timing (seconds) ────────────────────────────────────────────────────────
# Tuned for safe read-only scraping. Joining uses separate JOIN_ delays.
SCROLL_DELAY_MIN = 1.5
SCROLL_DELAY_MAX = 3.0
ACTION_DELAY_MIN = 2.0
ACTION_DELAY_MAX = 5.0
MINOR_DELAY_MIN = 1.0
MINOR_DELAY_MAX = 2.5
GROUP_BREAK_MIN = 8.0
GROUP_BREAK_MAX = 20.0
PROFILE_BATCH_BREAK_MIN = 30.0
PROFILE_BATCH_BREAK_MAX = 60.0
PROFILE_BATCH_SIZE = 15
JOIN_DELAY_MIN = 5.0
JOIN_DELAY_MAX = 10.0
JOIN_BATCH_SIZE = 10
JOIN_BATCH_BREAK_MIN = 60.0
JOIN_BATCH_BREAK_MAX = 120.0
BROWSE_IDLE_MIN = 3.0
BROWSE_IDLE_MAX = 6.0

# ── Scraping Limits ──────────────────────────────────────────────────────────
MAX_SESSION_MINUTES = 120
MARATHON_SESSION_MINUTES = 480    # 8-hour extended sessions
WARM_RESTART_MINUTES = 120        # Restart browser every 2 hours to prevent sluggishness
FIRST_RUN_LOOKBACK_DAYS = 90
DAILY_SCHEDULE_HOUR = 7  # 7 AM Pacific

# ── Group Discovery Search Terms ────────────────────────────────────────────
# Focused on finding VENDOR networking groups (where every poster is a lead)
# rather than consumer/bride groups.
SEARCH_TERMS = [
    # Vendor-to-vendor networking
    "Los Angeles event vendor network",
    "LA wedding vendor referral",
    "SoCal event professionals group",
    "Southern California wedding pros",
    "LA vendor to vendor referrals",
    "SoCal event industry professionals",
    # Party rental specific
    "Los Angeles party rental vendors",
    "SoCal tent rental event companies",
    "LA event rental companies",
    # By category (high-value referral partners)
    "LA event planners coordinators group",
    "Los Angeles caterers event vendors",
    "SoCal wedding planner network",
    "San Fernando Valley event services",
    # Regional
    "Ventura County event vendors",
    "Orange County wedding event vendors",
    "Inland Empire event vendor network",
    "Santa Clarita Valencia event vendors",
]
