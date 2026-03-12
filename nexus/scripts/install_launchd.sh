#!/bin/bash
# Install Nexus as a launchd service (auto-start on boot)
#
# Usage: bash scripts/install_launchd.sh
#
# This will:
# 1. Create log directory
# 2. Copy the plist to ~/Library/LaunchAgents/
# 3. Load the service
# 4. Verify it's running

set -e

PLIST_NAME="com.zoar.nexus"
PLIST_SRC="$(dirname "$0")/${PLIST_NAME}.plist"
PLIST_DST="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"
LOG_DIR="$HOME/.nexus/logs"

echo "🔧 Installing Nexus launchd service..."

# Create log directory
mkdir -p "$LOG_DIR"
echo "✅ Log directory: $LOG_DIR"

# Unload existing service if running
if launchctl list | grep -q "$PLIST_NAME"; then
    echo "📋 Unloading existing service..."
    launchctl unload "$PLIST_DST" 2>/dev/null || true
fi

# Copy plist
cp "$PLIST_SRC" "$PLIST_DST"
echo "✅ Plist installed: $PLIST_DST"

# Load the service
launchctl load "$PLIST_DST"
echo "✅ Service loaded"

# Wait a moment and check status
sleep 2
if launchctl list | grep -q "$PLIST_NAME"; then
    PID=$(launchctl list | grep "$PLIST_NAME" | awk '{print $1}')
    echo "✅ Nexus is running (PID: $PID)"
else
    echo "❌ Service may not have started. Check logs:"
    echo "   tail -f $LOG_DIR/nexus-stderr.log"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Nexus will now auto-start on boot."
echo ""
echo "Useful commands:"
echo "  View logs:    tail -f $LOG_DIR/nexus-stderr.log"
echo "  Stop:         launchctl unload $PLIST_DST"
echo "  Start:        launchctl load $PLIST_DST"
echo "  Restart:      launchctl unload $PLIST_DST && launchctl load $PLIST_DST"
echo "  Health check: curl http://localhost:8080/api/health"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
