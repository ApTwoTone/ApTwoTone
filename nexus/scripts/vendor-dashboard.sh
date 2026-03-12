#!/bin/bash
# Launch Vendor Leads Dashboard as a desktop app window.
# Uses Chrome --app mode for a frameless, tab-free experience.

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
URL="http://localhost:7860/vendor-leads/"
PROFILE="$HOME/.nexus/chrome-vendor-app"

# Wait for Nexus server to be ready (up to 30s)
echo "Waiting for Nexus server..."
for i in {1..30}; do
    if curl -sf http://localhost:7860/api/health > /dev/null 2>&1; then
        echo "Server ready."
        break
    fi
    sleep 1
done

# Launch Chrome in app mode
exec "$CHROME" --app="$URL" --window-size=1440,900 --user-data-dir="$PROFILE"
