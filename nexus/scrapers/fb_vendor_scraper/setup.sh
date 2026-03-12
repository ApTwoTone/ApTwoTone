#!/bin/bash
# FB Vendor Scraper — Setup Script
# Run this on your Mac mini to set up the scraper environment.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
BROWSER_DIR="$HOME/.nexus/fb_browser_data"
PLIST_NAME="com.nexus.fb-vendor-scraper"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"

echo "=========================================="
echo " FB Vendor Scraper — Setup"
echo "=========================================="
echo ""

# 1. Create virtual environment
echo "[1/5] Creating Python virtual environment..."
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

# 2. Install dependencies
echo "[2/5] Installing dependencies..."
pip install --upgrade pip
pip install -r "$SCRIPT_DIR/requirements.txt"

# 3. Install Playwright Chromium
echo "[3/5] Installing Playwright Chromium browser..."
python -m playwright install chromium

# 4. Create browser data directory
echo "[4/5] Creating browser data directory..."
mkdir -p "$BROWSER_DIR"

# 5. Create launchd plist for daily scheduling
echo "[5/5] Creating launchd schedule (7 AM Pacific daily)..."
cat > "$PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_NAME</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV_DIR/bin/python</string>
        <string>$SCRIPT_DIR/scraper.py</string>
        <string>--schedule</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$SCRIPT_DIR/scraper_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$SCRIPT_DIR/scraper_stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
        <key>NEXUS_API_KEY</key>
        <string></string>
    </dict>
</dict>
</plist>
EOF

echo ""
echo "=========================================="
echo " Setup Complete!"
echo "=========================================="
echo ""
echo "NEXT STEPS:"
echo ""
echo "1. FIRST RUN — Log into Facebook:"
echo "   cd $SCRIPT_DIR"
echo "   source .venv/bin/activate"
echo "   python scraper.py"
echo "   (A browser will open — log into Facebook manually)"
echo "   (Press Ctrl+C after logging in, your session is saved)"
echo ""
echo "2. CONFIGURE — Edit config.py:"
echo "   - Set NEXUS_BASE_URL to your Cloudflare tunnel URL"
echo "   - Set NEXUS_API_KEY or export it: export NEXUS_API_KEY=your_key"
echo "   - Add your VPS IP to ALLOWED_IPS for remote triggers"
echo ""
echo "3. DISCOVER GROUPS:"
echo "   python scraper.py --discover"
echo "   (This searches Facebook for relevant groups)"
echo "   (Join the ones you want, then add them via Telegram)"
echo ""
echo "4. ADD GROUPS — Via Telegram:"
echo "   /fb_add_group https://facebook.com/groups/example"
echo ""
echo "5. START DAILY SCHEDULE:"
echo "   launchctl load $PLIST_PATH"
echo "   (The scraper will run daily at 7 AM and listen for triggers)"
echo ""
echo "6. MANUAL RUN — Via Telegram:"
echo "   /fb_scrape"
echo ""
echo "To stop the scheduled service:"
echo "   launchctl unload $PLIST_PATH"
echo ""
echo "Logs: $SCRIPT_DIR/scraper.log"
