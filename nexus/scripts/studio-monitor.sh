#!/bin/bash
# studio-monitor.sh — Agent Studio tmux dashboard.
# Visualizes Architect, Builder, Reviewer, and Patcher in parallel.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SESSION_NAME="agent-studio"
LOG_DIR="$HOME/.nexus/orchestrator_logs"

cd "$REPO_ROOT"

# Ensure tmux session exists
if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    tmux new-session -d -s "$SESSION_NAME" -n "monitor"
    echo "Created tmux session: $SESSION_NAME"
fi

# Pane 1 (Top Left): Architect
tmux split-window -v -p 50 -t "$SESSION_NAME:monitor"
tmux split-window -h -p 50 -t "$SESSION_NAME:monitor.0"
tmux split-window -h -p 50 -t "$SESSION_NAME:monitor.2"

# Map logs to panes
# Pane 0: Architect
tmux send-keys -t "$SESSION_NAME:monitor.0" "echo '--- Architect (Gemini 3 Flash) ---' && tail -f $LOG_DIR/orchestrator.log | grep --line-buffered 'Architect'" C-m
# Pane 1: Builder
tmux send-keys -t "$SESSION_NAME:monitor.2" "echo '--- Builder (Ollama/Groq) ---' && tail -f $LOG_DIR/orchestrator.log | grep --line-buffered 'Builder'" C-m
# Pane 2: Reviewer
tmux send-keys -t "$SESSION_NAME:monitor.1" "echo '--- Reviewer (ZAI GLM) ---' && tail -f $LOG_DIR/orchestrator.log | grep --line-buffered 'Reviewer'" C-m
# Pane 3: Patcher
tmux send-keys -t "$SESSION_NAME:monitor.3" "echo '--- Patcher (ZAI/Groq) ---' && tail -f $LOG_DIR/orchestrator.log | grep --line-buffered 'Patcher'" C-m

tmux select-layout -t "$SESSION_NAME:monitor" tiled

echo "Studio monitor ready."
echo "Attach with: tmux attach -t $SESSION_NAME"
echo ""
echo "Note: Ensure 'python core/agent_studio.py' is running in the background."
