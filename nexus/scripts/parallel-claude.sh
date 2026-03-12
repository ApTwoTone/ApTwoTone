#!/bin/bash
# parallel-claude.sh — Launch N parallel Claude Code sessions in git worktrees via tmux.
#
# Usage:
#   ./scripts/parallel-claude.sh 3        # Launch 3 parallel sessions
#   ./scripts/parallel-claude.sh 3 "Fix auth" "Add tests" "Update docs"  # With initial prompts
#
# Each session gets its own git worktree + branch, so changes are isolated.
# When done: create a PR from each worktree branch → Claude review → merge to main.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NUM_SESSIONS="${1:-3}"
SESSION_NAME="claude-parallel"

cd "$REPO_ROOT"

# Ensure tmux session exists
if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    tmux new-session -d -s "$SESSION_NAME" -n "control"
    echo "Created tmux session: $SESSION_NAME"
fi

for i in $(seq 1 "$NUM_SESSIONS"); do
    BRANCH="wt-${i}-$(date +%s)"
    WORKTREE_DIR="$REPO_ROOT/.claude/worktrees/$BRANCH"

    # Create worktree with a new branch based on current HEAD
    git worktree add "$WORKTREE_DIR" -b "$BRANCH" 2>/dev/null || {
        echo "Failed to create worktree $i — skipping"
        continue
    }

    # Get prompt if provided (arg index = i + 1)
    ARG_INDEX=$((i + 1))
    PROMPT="${!ARG_INDEX:-Ready for tasks. Session wt-$i is active.}"

    # Launch Claude in a new tmux window
    tmux new-window -t "$SESSION_NAME" -n "wt-$i" \
        "cd '$WORKTREE_DIR' && echo '--- Session wt-$i | Branch: $BRANCH ---' && claude '$PROMPT'; exec bash"

    echo "Launched session wt-$i in worktree: $WORKTREE_DIR (branch: $BRANCH)"
done

echo ""
echo "All $NUM_SESSIONS sessions launched."
echo "Attach with: tmux attach -t $SESSION_NAME"
echo "Switch windows: Ctrl-b then window number (0=control, 1=wt-1, 2=wt-2, ...)"
echo ""
echo "When done with a worktree:"
echo "  git worktree remove .claude/worktrees/<branch-name>"
