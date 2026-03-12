#!/bin/bash
# Server Watchdog — Checks if server.py is running, restarts if dead.
# Runs via launchd every 5 minutes.
# Separate from the in-process watchdog in core/watchdog.py.

REPO_DIR="/Users/kai/nexus"
PIDFILE="/Users/kai/.nexus/server.pid"
LOG="/Users/kai/.nexus/watchdog-external.log"
PORT=7860

# Check if server is responding
if curl -sf http://localhost:$PORT/ > /dev/null 2>&1; then
    exit 0  # Server is healthy
fi

echo "$(date): Server not responding on port $PORT" >> "$LOG"

# Check if process exists
if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "$(date): Process $PID exists but not responding — killing" >> "$LOG"
        kill "$PID" 2>/dev/null
        sleep 3
        kill -9 "$PID" 2>/dev/null || true
    fi
fi

# Also kill any orphaned server processes
pkill -f "python.*server.py" 2>/dev/null || true
sleep 2

# Restart
echo "$(date): Starting server..." >> "$LOG"
cd "$REPO_DIR" && nohup venv/bin/python server.py > /Users/kai/.nexus/server.log 2>&1 &
NEW_PID=$!
echo $NEW_PID > "$PIDFILE"
echo "$(date): Server restarted (PID $NEW_PID)" >> "$LOG"

# Notify (max 1 notification per restart)
cd "$REPO_DIR" && venv/bin/python -c "
import json
from urllib.request import Request, urlopen
from pathlib import Path
cfg = json.loads((Path.home() / '.nexus' / 'config.json').read_text())
token = cfg.get('telegram_token','')
chat = cfg.get('telegram_chat_ids',[''])[0]
if token and chat:
    msg = 'WATCHDOG: Server was down. Auto-restarted (PID $NEW_PID).'
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    data = json.dumps({'chat_id': str(chat), 'text': msg}).encode()
    req = Request(url, data=data, headers={'Content-Type': 'application/json'})
    urlopen(req, timeout=10)
" 2>/dev/null || true
