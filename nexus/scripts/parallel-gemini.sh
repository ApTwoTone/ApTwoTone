#!/bin/bash
# parallel-gemini.sh — Launch N parallel Gemini sessions in git worktrees via tmux.
#
# Usage:
#   ./scripts/parallel-gemini.sh 3        # Launch 3 parallel sessions
#   ./scripts/parallel-gemini.sh 3 "Fix auth" "Add tests" "Update docs"  # With prompts
#
# Isolated changes via git worktrees. Mirror of parallel-claude.sh strategy.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NUM_SESSIONS="${1:-2}"
SESSION_NAME="gemini-parallel"

cd "$REPO_ROOT"

# Ensure tmux session exists
if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    tmux new-session -d -s "$SESSION_NAME" -n "control"
    echo "Created tmux session: $SESSION_NAME"
fi

for i in $(seq 1 "$NUM_SESSIONS"); do
    BRANCH="gemini-wt-${i}-$(date +%s)"
    WORKTREE_DIR="$REPO_ROOT/.gemini/worktrees/$BRANCH"

    echo "Setting up session $i..."

    # Create worktree
    git worktree add "$WORKTREE_DIR" -b "$BRANCH" 2>/dev/null || {
        echo "Failed to create worktree $i — skipping"
        continue
    }

    # Get prompt if provided
    ARG_INDEX=$((i + 1))
    PROMPT="${!ARG_INDEX:-}"

    # Launch Gemini Chat in a new tmux window
    if [ -n "$PROMPT" ]; then
        tmux new-window -t "$SESSION_NAME" -n "gemini-$i" \
            "cd '$WORKTREE_DIR' && printf \"${PROMPT}\\n\" | python3 scripts/gemini_chat.py; exec bash"
    else
        tmux new-window -t "$SESSION_NAME" -n "gemini-$i" \
            "cd '$WORKTREE_DIR' && python3 scripts/gemini_chat.py; exec bash"
    fi

    echo "Launched session gemini-$i in worktree: $WORKTREE_DIR"
done

echo ""
echo "All $NUM_SESSIONS sessions launched."
echo "Attach with: tmux attach -t $SESSION_NAME"
echo ""
echo "Cleanup: git worktree remove .gemini/worktrees/<branch>"
