# Nexus Backend — Agent Context

## Project Overview

FastAPI monolith for Zoar Bathroom Rentals — AI-powered lead generation, marketing automation, and vendor management. This is the Python backend that powers everything.

## Business Goal

Get the luxury restroom trailer booked at least once every 5 days. Every code change must have a direct line of sight to bookings.

## Architecture

- **Framework**: FastAPI (server.py, port 7861)
- **Python**: 3.9+ with async/await
- **Database**: SQLite at ~/.nexus/memory.db (WAL mode)
- **Config**: ~/.nexus/config.json (API keys, never commit)
- **Logs**: ~/.nexus/session_logs/ and ~/.nexus/orchestrator_logs/

## Directory Structure

```
server.py          — FastAPI app, all endpoints, startup scheduler
core/              — 46 modules: orchestrator, lead pipeline, ads, analytics
integrations/      — 26 modules: Google Voice, Gmail, Facebook, Twilio, n8n
agents/            — AI agents: analyst, content, lead finder, metrics
telegram/          — Telegram bot (approval workflows, commands)
scrapers/          — Facebook vendor scraper (Playwright)
scripts/           — Utilities, orchestrator, launchers
```

## Code Conventions

- Imports: stdlib, then third-party, then local
- Logging: `log = logging.getLogger("module_name")` — never print()
- Error handling: log.error/warning with context, don't swallow exceptions
- API responses: `{"status": "ok/error", ...}`
- Config: Use `load_config()` from server.py
- Database: parameterized SQL only via db helpers in core/db_migrate.py
- Security: Action endpoints require `verify_action_auth`
- File paths: `Path.home() / ".nexus"` for user data
- Python 3.9: Use `Optional[str]` not `str | None`

## Critical Safety Rules

1. NEVER send messages to contacts without checking `contact_blocklist` table first
2. Existing contacts (pre 2026-03-04) require explicit Telegram approval before outreach
3. The `outbound_gate()` in integrations/messaging.py enforces blocklist as first operation
4. Never commit API keys or webhook URLs

## Key Files

| File                      | Purpose                                   |
| ------------------------- | ----------------------------------------- |
| server.py                 | FastAPI app, all endpoints                |
| scripts/orchestrator.py   | Task orchestrator (routes to free models) |
| core/lead_pipeline.py     | Async lead lifecycle                      |
| core/approval_system.py   | Multi-gate approval workflows             |
| telegram/bot.py           | Telegram bot and approval handlers        |
| integrations/messaging.py | Outbound gate with blocklist              |

## Running

```bash
cd ~/nexus && source venv/bin/activate && python server.py
```

## Model Usage

Nexus uses a tiered model routing system, with **Gemini 3 Flash** as the primary intelligence for orchestration and planning.

### Parallel Intelligence

- **Interactive Parallelism**: Launch multiple Gemini sessions via `scripts/parallel-gemini.sh` for simultaneous work across git branches.
- **Parallel Agent Studio**: Specialized agents working concurrently on the build pipeline. Monitor via `scripts/studio-monitor.sh`.

Claude is orchestrator-only for architecture and security decisions.
