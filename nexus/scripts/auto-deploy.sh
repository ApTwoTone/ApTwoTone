#!/bin/bash
# Auto-deploy: pull latest from main and restart server
# Run via cron or launchd: every 5 minutes check for updates
#
# Add to crontab:
#   */5 * * * * /Users/kai/nexus/scripts/auto-deploy.sh >> /Users/kai/.nexus/deploy.log 2>&1

set -e

REPO_DIR="/Users/kai/nexus"
PIDFILE="/Users/kai/.nexus/server.pid"
LOG="/Users/kai/.nexus/deploy.log"

cd "$REPO_DIR"

# Only auto-deploy if we're actually on the main branch.
# If on a feature branch, skip entirely.
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$CURRENT_BRANCH" != "main" ]; then
    exit 0  # Not on main — skip deploy
fi

# Fetch latest
git fetch origin main --quiet

# Check if there are new commits on main
LOCAL=$(git rev-parse main)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
    exit 0  # Already up to date
fi

echo "$(date): New commits detected, deploying..."
echo "  Local:  $LOCAL"
echo "  Remote: $REMOTE"

# Pull changes
git pull origin main --ff-only

# Run any database migrations
if [ -f "core/db_migrate.py" ]; then
    echo "$(date): Running migrations..."
    cd "$REPO_DIR" && venv/bin/python core/db_migrate.py || true
fi

# Restart server
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cat "$PIDFILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "$(date): Stopping old server (PID $OLD_PID)..."
        kill "$OLD_PID" || true
        sleep 2
    fi
fi

echo "$(date): Starting server..."
cd "$REPO_DIR" && nohup venv/bin/python server.py > /Users/kai/.nexus/server.log 2>&1 &
echo $! > "$PIDFILE"
echo "$(date): Server started (PID $(cat $PIDFILE))"

# Notify via Telegram
venv/bin/python -c "
import json, urllib.request
cfg = json.load(open('$HOME/.nexus/config.json'))
token = cfg.get('telegram_token','')
chat = cfg.get('telegram_chat_ids',[''])[0]
if token and chat:
    msg = '🚀 Auto-deploy complete — server restarted with latest from main'
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    data = json.dumps({'chat_id': chat, 'text': msg}).encode()
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    urllib.request.urlopen(req)
" 2>/dev/null || true

echo "$(date): Deploy complete!"
