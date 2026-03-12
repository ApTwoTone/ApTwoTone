#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Nexus CRM Server — Auto-Restart Wrapper
#
# Usage: ./scripts/start_server.sh
#
# Features:
# - Kills any existing server on port 7860
# - Starts server with auto-restart on crash
# - Saves PID for clean shutdown
# - Logs output to ~/.nexus/logs/server.log
# - Auto-initializes Google Voice after startup
# ─────────────────────────────────────────────────────────────────────────────

NEXUS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$HOME/.nexus/logs"
PID_FILE="$HOME/.nexus/server.pid"
LOG_FILE="$LOG_DIR/server.log"
MAX_RESTARTS=10
RESTART_DELAY=5

mkdir -p "$LOG_DIR"

# Kill existing server
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "[Start] Killing existing server (PID $OLD_PID)"
        kill "$OLD_PID" 2>/dev/null
        sleep 2
    fi
    rm -f "$PID_FILE"
fi

# Also kill anything on port 7860
lsof -ti:7860 | xargs kill 2>/dev/null
sleep 1

echo "[Start] Starting Nexus CRM server..."
echo "[Start] Logs: $LOG_FILE"
echo "[Start] PID file: $PID_FILE"

restart_count=0

while [ $restart_count -lt $MAX_RESTARTS ]; do
    echo "$(date '+%Y-%m-%d %H:%M:%S') [Start] Server starting (attempt $((restart_count + 1))/$MAX_RESTARTS)" >> "$LOG_FILE"

    cd "$NEXUS_DIR"

    # Use python3 (macOS doesn't have 'python' by default)
    PYTHON_CMD="python3"
    if ! command -v python3 &>/dev/null; then
        PYTHON_CMD="python"
    fi

    $PYTHON_CMD server.py >> "$LOG_FILE" 2>&1 &
    SERVER_PID=$!
    echo "$SERVER_PID" > "$PID_FILE"
    echo "[Start] Server running (PID $SERVER_PID) via $PYTHON_CMD"

    # Wait for server to start (up to 15 seconds)
    STARTED=false
    for i in $(seq 1 15); do
        # Check process is still alive first
        if ! kill -0 "$SERVER_PID" 2>/dev/null; then
            echo "[Start] Server process died before becoming healthy"
            break
        fi
        if curl -s http://localhost:7860/api/status > /dev/null 2>&1; then
            echo "[Start] Server is healthy!"
            STARTED=true

            # Auto-init Google Voice (non-blocking, with error handling)
            (
                sleep 3
                echo "[Start] Initializing Google Voice..."
                GV_RESULT=$(curl -s -X POST http://localhost:7860/api/browser/login-gv 2>&1)
                echo "[Start] GV init result: $GV_RESULT"
            ) &

            break
        fi
        sleep 1
    done

    if [ "$STARTED" = false ]; then
        echo "[Start] Server failed to become healthy within 15s"
    fi

    # Wait for server process to exit
    wait $SERVER_PID
    EXIT_CODE=$?

    echo "$(date '+%Y-%m-%d %H:%M:%S') [Start] Server exited with code $EXIT_CODE" >> "$LOG_FILE"

    if [ $EXIT_CODE -eq 0 ]; then
        echo "[Start] Server stopped cleanly."
        rm -f "$PID_FILE"
        exit 0
    fi

    restart_count=$((restart_count + 1))
    echo "[Start] Server crashed! Restarting in ${RESTART_DELAY}s... ($restart_count/$MAX_RESTARTS)"
    sleep $RESTART_DELAY
done

echo "[Start] Max restarts ($MAX_RESTARTS) reached. Giving up."
rm -f "$PID_FILE"
exit 1
