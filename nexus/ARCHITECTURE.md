# NEXUS — Multi-Model Orchestration Architecture

## System Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         KAI (Human)                             │
│                    Telegram Notifications                        │
│              (max 3-5 pings/hr, batched summaries)              │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                     ┌─────▼─────┐
                     │ TELEGRAM   │
                     │   BOT      │
                     └─────┬─────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                    ORCHESTRATOR (scripts/orchestrator.py)        │
│                                                                  │
│  1. Accepts high-level goal                                      │
│  2. Breaks into subtasks (via- **Gemini 3 Flash**: Primary reasoning and architect agent. 1M context.)│
│  3. Scores complexity: critical/high/medium/low/trivial          │
│  4. Routes to cheapest capable model                             │
│  5. Tracks completion + quota usage                              │
│  6. Sends batched Telegram summaries                             │
│  7. Writes session logs to ~/.nexus/session_logs/               │
└──────┬──────────┬──────────┬──────────┬──────────┬─────────────┘
       │          │          │          │          │
       ▼          ▼          ▼          ▼          ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│ CLAUDE   │ │ GEMINI   │ │LLAMA 3.3 │ │   GROQ   │ │ QWEN 3   │
│ CODE     │ │2.5 FLASH │ │70B(OpRtr)│ │(Llama 4) │ │ (Ollama) │
│          │ │          │ │          │ │          │ │          │
│ $200/mo  │ │ FREE     │ │ FREE     │ │ FREE     │ │ FREE     │
│ capped   │ │ 500/day  │ │ 200/day  │ │ 1000/day │ │ ∞ local  │
├──────────┤ ├──────────┤ ├──────────┤ ├──────────┤ ├──────────┤
│ONLY:     │ │Planning  │ │Mid-level │ │Boiler-   │ │Drafts    │
│Arch,     │ │Research  │ │coding    │ │plate     │ │Explore   │
│Security, │ │Long ctx  │ │Reasoning │ │CRUD      │ │Fallback  │
│Complex   │ │Docs      │ │Claude    │ │Tests     │ │Zero cost │
│bugs,     │ │Orchestr. │ │declines  │ │Refactors │ │Offline   │
│Final rev │ │          │ │          │ │          │ │          │
└──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    COST GUARD (built into orchestrator)          │
│                                                                  │
│  Before EVERY API call:                                         │
│  1. Check model free tier quota (daily reset)                   │
│  2. If exhausted → auto-route to next free alternative          │
│  3. NEVER fall through to paid API silently                     │
│  4. Log routing decision to ~/.nexus/orchestrator_logs/         │
│  5. Track usage in quota_tracker.json (auto-resets daily)       │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    5 PARALLEL WORKTREES                          │
│                                                                  │
│  wt-scraper       → Facebook vendor scraper fixes               │
│  wt-backend       → Server.py API and core modules              │
│  wt-frontend      → Zoar website + dashboards                   │
│  wt-automation    → Orchestrator, scripts, CI/CD                │
│  wt-integrations  → Gmail, GHL, Twilio, n8n                    │
│                                                                  │
│  Each worktree has its own branch, WORKTREE_TASK.md,            │
│  and can run Claude Code independently in tmux.                 │
│                                                                  │
│  Launch all: ./scripts/parallel-claude.sh 5                     │
│  Attach:    tmux attach -t claude-parallel                      │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    SESSION PERSISTENCE                           │
│                                                                  │
│  ~/.nexus/session_logs/{session_id}.md                          │
│    - Goal, tasks, model assignments, results                    │
│    - Written after every task completion                         │
│    - New sessions read existing logs first                      │
│                                                                  │
│  ~/.nexus/orchestrator_logs/                                    │
│    - routing_YYYYMMDD.jsonl (every routing decision)            │
│    - quota_tracker.json (daily quota counts)                    │
│    - orchestrator.log (full execution log)                      │
│                                                                  │
│  CLAUDE.md Corrections Log                                      │
│    - Every correction/lesson appended automatically             │
│    - Grows with every session (living document)                 │
└─────────────────────────────────────────────────────────────────┘
```

## API Keys Required

| Service           | Env Var                 | Get It                             | Cost            |
| ----------------- | ----------------------- | ---------------------------------- | --------------- |
| Gemini 2.5 Pro    | `GEMINI_API_KEY`        | https://aistudio.google.com/apikey | Free (100/day)  |
| OpenRouter (Kimi) | `OPENROUTER_API_KEY`    | https://openrouter.ai/keys         | Free (200/day)  |
| Groq              | `GROQ_API_KEY`          | https://console.groq.com/keys      | Free (1000/day) |
| Ollama (Qwen 3)   | None                    | `brew services start ollama`       | Free (local)    |
| Claude Code       | Claude Max subscription | Already active                     | $200/mo         |

## Quick Start

```bash
# 1. Set API keys in ~/.zshrc
export GEMINI_API_KEY="your-key"
export OPENROUTER_API_KEY="your-key"
export GROQ_API_KEY="your-key"

# 2. Source them
source ~/.zshrc

# 3. Verify Ollama is running
ollama list

# 4. Check orchestrator status
python3 scripts/orchestrator.py --status

# 5. Run a task
python3 scripts/orchestrator.py "Build a contact form for the zoar website"

# 6. Launch parallel Claude sessions
./scripts/parallel-claude.sh 5
tmux attach -t claude-parallel
```
