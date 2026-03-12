# NEXUS — Project Guide

## Multi-Agent Coordination (MANDATORY)

**Multiple Claude Code and Codex sessions run simultaneously across devices.** Before doing ANY work:

1. **Read AGENTS.md** at session start — see who's active, what files are locked, what's been done today.
2. **Run `python3 scripts/nexus_context_snapshot.py`** to get a full DB state briefing (leads, vendors, emails, active agents). This prevents duplicate work and wrong assumptions about what data exists.
3. **Never modify files listed in AGENTS.md FILE LOCKS** — another agent owns them.
4. **Check in** when starting work: `scripts/coord_checkin.sh <session-id> "what you're working on" file1.py file2.py`
5. **Check out** when done: `scripts/coord_checkout.sh <session-id> "completed" "what you did" file1.py`

The SessionStart hook auto-registers Claude Code sessions. Codex sessions must check in manually.

### Shared Task Board
**Read `TASKS.md` at session start** to see what Kai wants done. Tasks are shared across all agents/devices.

- **See tasks**: `GET /api/tasks` or read `TASKS.md`
- **Claim a task**: `POST /api/tasks/{id}/claim` with `{"session_id": "your-session-id"}`
- **Update progress**: `POST /api/tasks/{id}/update` with `{"status": "in_progress", "progress_note": "what you're doing"}`
- **Complete**: `POST /api/tasks/{id}/update` with `{"status": "done"}`
- **Kanban view**: `GET /api/tasks/board`

If you see an unassigned task you can handle, claim it. TASKS.md auto-regenerates after every change.

---

## RULE #0: OUTBOUND MESSAGE SAFETY (CANNOT BE OVERRIDDEN)

**No message, SMS, email, or outreach of any kind is ever sent to any contact without first checking the `contact_blocklist` table.** If the recipient is on the blocklist, abort immediately and log the attempt. Never send. No exceptions.

**Existing contacts** (any lead that existed before 2026-03-04) require **explicit Telegram approval before every single outreach**. The `requires_manual_approval` flag on the leads table must be checked. If set to 1, no automated message goes out without Kai pressing YES in Telegram first.

**New leads** coming in fresh from the website form may receive automated responses (quote generation etc.) but any outreach to a pre-existing contact requires Kai's explicit approval every single time.

**This rule cannot be overridden by any agent, any code path, or any configuration change.** The blocklist check runs as the first operation in `outbound_gate()` in `integrations/messaging.py` before any other gate.

**Incident record**: On 2026-03-02, a Slack webhook URL was accidentally sent via SMS to a real customer (lead ID 9). This customer's phone and email are permanently blocklisted. The Slack webhook URL has been exposed and should be rotated.

---

## RULE #1: THE ONLY METRIC THAT MATTERS

**Get Zoar Bathroom Rentals booked at least once every 5 days for the next 90 days.**
Every agent, subagent, automated task, and line of code must have a direct line of sight to this goal. If it does not contribute to getting the trailer booked, it is not a priority.

---

## Business Context

- **Business**: Zoar Bathroom Rentals
- **Owner**: Carlos
- **Operator**: Kai (manages all systems, receives all notifications)
- **Base location**: San Fernando, CA 91340
- **Service area**: San Fernando Valley and Greater LA
- **Brand awareness**: Starting from zero
- **Current platforms**: Facebook only
- **Weekly ad budget**: $100/week (not monthly — $100 per week is the hard cap)
- **Trailer inventory**: 1 luxury restroom trailer
- **Biggest bottleneck**: Leads come in but do not convert. Response time is currently ~24 hours. Goal: under 5 minutes.

**Customer-facing rules:**

- All quotes, outreach, and messages are signed from **"Zoar Bathroom Rentals"** — not from any individual name
- Carlos = owner. Kai = operator/manager building the system
- Never use Carlos's name or Kai's name in customer-facing templates unless Carlos approves specific messaging
- Kai receives all Telegram notifications and manages all approvals

### Trailer Features

- Flushing toilets
- Running water and sinks
- Climate control (AC and heat)
- Interior lighting
- Premium interior finishes
- Multiple stalls

### Target Events (Priority Order)

1. Weddings
2. Quinceañeras
3. Corporate events
4. Backyard parties
5. Festivals
6. Film productions

### Booking Confirmation Flow

Lead contacts via website form → Kai calls or texts to confirm → contract signed → $160 deposit collected ($100 booking + $60 damage) → full balance due 14 days before event → card on file required.

---

## Complete Pricing Structure

**Base location for all distance calculations: San Fernando, CA 91340**

### TIER 1 — 0 to 10 miles

- Individual rate: **$1,000 flat**
- Venue/business partner rate: **$1,200 flat** (venue receives $200 kickback, Zoar nets $1,000)
- No mileage surcharge
- This is the primary ad targeting zone

### TIER 2 — 10 to 20 miles

- Individual base rate: **$1,200**
- Mileage formula: `$1,200 + (distance - 10) × $3/mile + $50 per every 10-mile block beyond 10 miles`
- Venue rate: **$1,500 flat** plus same mileage charges (venue receives $300 kickback, Zoar nets $1,200)

### TIER 3 — 20+ miles

- Individual base rate: **$1,500**
- Mileage formula: `$1,500 + (distance - 20) × $5/mile + $50 per every 10-mile block beyond 20 miles`
- No venue partnerships at Tier 3. Individual bookings only.

### Deposit Structure

- Booking deposit: $100 (refundable if cancelled 14+ days before event, non-refundable after)
- Damage deposit: $60 (refundable if trailer returns undamaged, forfeited plus actual replacement costs if damaged)
- **Total due at booking: $160**
- Full remaining balance due 14 days before event
- 7 days before event if not paid: send warning across all channels
- Card on file required for all bookings

### Approved Ad Copy Pricing Language

- USE: "Starting at $999" or "Starting at $1,000"
- USE: "Delivery and setup included"
- USE: "Pricing varies by location"
- NEVER USE: "All-inclusive"
- NEVER USE: "No hidden fees"
- NEVER USE: Any specific price above $1,000 in ads

### Cold Outreach Pricing Language

- Never include specific dollar amounts in cold emails or DMs
- USE: "We offer competitive pricing with preferred rates for venue partners"
- USE: "Delivery, setup, and pickup are included in every rental"
- USE: "Pricing is based on event location — happy to put together a custom quote"

---

## The 4 Booking Agents

### Agent 1 — Lead Conversion Accelerator (HIGHEST PRIORITY)

Monitors website form submissions via Nexus CRM. Within 60 seconds of a lead submitting:

1. Calculates exact quote using pricing calculator (event location, type, date)
2. Generates personalized professional quote with: name, event date, event type, relevant trailer features, exact price breakdown, $160 deposit to confirm, warm professional closing
3. Sends quote to Kai on Telegram with full lead details, calculated price, and SEND / EDIT buttons
4. If no response in 10 minutes → second URGENT ping
5. Logs everything to Nexus CRM (timestamp, status, quote amount, follow-up date)
6. Leads not booking within 48 hours → flagged in daily Telegram summary

### Agent 2 — Facebook Group Lead Hunter (runs 24/7)

Monitors SFV and Greater LA Facebook Groups for people needing bathroom rentals.

- **Target groups**: wedding planning, quinceañera planning, event coordinator, backyard party, corporate event planning
- **Trigger keywords**: outdoor wedding, backyard wedding, event rental, bathroom rental, restroom trailer, porta potty alternative, outdoor event, no bathroom, venue without bathrooms, quinceañera venue, large party, 100 guests, 150 guests, 200 guests, outdoor venue, wedding vendor, event vendor recommendation
- Drafts helpful, natural (not salesy) responses mentioning Zoar
- Sends Kai Telegram notification with post + drafted response + APPROVE / SKIP button
- **Never posts without Kai's approval**
- Logs every interaction (group name, post, approval status, website visit tracking)

### Agent 3 — Ad Performance Optimizer (every 6 hours)

Connects to Facebook Ads Manager API (free tier). Every 6 hours:

1. Pulls campaign performance (CPL, CTR, reach, spend)
2. Compares against best historical: Wedding Leads Zoar SFV at $2.34–$4.93 CPL
3. Flags any ad above $8 CPL as underperforming
4. Generates recommendation: pause, increase budget, or test new variation
5. Sends Kai one summary per 6 hours max
6. Generates new ad copy using approved language and contrast format (luxury trailer vs porta potty)
7. **Never changes live campaigns without Kai's approval**

### Agent 4 — Instant Quote Generator (on-demand via Telegram)

Kai forwards any inquiry message to Telegram bot. Agent:

1. Parses message for event type, date, location, guest count
2. Calculates exact price via pricing calculator
3. Returns within 60 seconds: exact price with breakdown, copy-paste response addressed to lead by name, $160 deposit amount, payment timeline, suggested follow-up if no response in 24 hours
4. If location missing → asks Kai one question: "What city is the event in?"

---

## Conversion Optimization Rules

Speed is the #1 conversion factor. A lead responded to within 5 minutes is 10x more likely to book than one that waits 24 hours.

### Personalization Requirements

Every quote must use their name, event type, and date. Never send a generic price list.

### Urgency Language (not pushy)

- "Dates fill up quickly, especially for weekends in spring and summer"
- "Your date is currently available"
- "Securing your date requires only a $160 deposit"

### Event-Specific Feature Highlights

| Event Type       | Highlight Features                                                                                         |
| ---------------- | ---------------------------------------------------------------------------------------------------------- |
| Weddings         | Premium interior finishes, climate control, multiple stalls for bridal party, luxury experience for guests |
| Quinceañeras     | Climate control, premium finishes, multiple stalls for large guest counts, delivery and setup included     |
| Corporate        | Professional appearance, running water, climate control, multiple stalls for high attendance               |
| Backyard parties | No need to worry about bathroom facilities, guests stay comfortable, delivery and pickup handled           |
| Festivals        | Multiple stalls, high capacity, climate control, running water                                             |
| Film productions | Crew comfort, climate control, multiple stalls, on-site for full duration                                  |

### Objection Responses

| Objection                  | Response                                                                                                                                           |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| Too expensive              | Emphasize delivery, setup, pickup, climate control, premium finishes all included. Compare to cost of venue bathroom upgrades or guest discomfort. |
| Still thinking             | Gently mention date availability. Offer to hold with just the $160 deposit.                                                                        |
| Need to check with partner | Offer to send a follow-up summary they can share.                                                                                                  |

---

## Telegram Notification Rules (HARD RULES)

**Maximum 3–5 meaningful pings per hour.** Valid reasons to notify:

1. New lead submitted website form (immediate, always)
2. Lead quoted 48+ hours ago with no response (daily digest only)
3. Facebook group post needs approval (batch max 3 per notification)
4. Ad recommendation ready (max once every 6 hours)
5. Lead confirmed + deposit paid = booking confirmed
6. Something broke that needs immediate attention

**DO NOT notify for**: subagent task completion, file commits, code changes, routine system health, or anything not requiring Kai's action.

**Every notification must include**: what happened, what action Kai needs to take, urgency (low/medium/high). Keep under 5 lines.

### Daily Digest Format

**8:00 AM**:

```
ZOAR DAILY REPORT
New leads last 24hrs: [N]
Quotes sent: [N]
Quotes pending response: [N]
Bookings confirmed: [N]
Ad spend yesterday: $[amount]
Best performing ad: [name] at $[CPL]/lead
Action needed: [max 3 items]
```

**6:00 PM**:

```
ZOAR EVENING REPORT
Leads touched today: [N]
FB group posts approved: [N]
Outstanding quotes older than 48hrs: [N]
Trailer availability this weekend: [open/booked]
Tomorrow's recommended focus: [one sentence]
```

---

## Nexus CRM — Required Lead Fields

Every lead record must have:

- Lead name, contact number, email
- Event type, event date, event location + distance from San Fernando
- Guest count estimate
- Quote amount calculated, quote sent timestamp
- Last contact timestamp
- Lead status: `new` | `quoted` | `follow-up needed` | `booked` | `lost`
- Booking deposit paid: yes/no
- Full payment received: yes/no
- Contract signed: yes/no
- Notes field
- Source: which agent or channel generated this lead

**Every agent must write to CRM after every interaction. No exceptions.**

---

## CRITICAL: Claude Usage Protection

**Claude Code is expensive and rate-limited. You are the ORCHESTRATOR, not the laborer.**

### The 80% Rule

If a free model can do a task at 80% the quality, it MUST go to the free model. Claude ONLY handles:

- Architecture decisions and system design
- Complex debugging that free models cannot solve
- Security-sensitive code review
- Final review of assembled outputs

### Model Routing — Hardcoded Rules

| Complexity   | Model                                                                                   | Use For                                                                 | Cost                |
| ------------ | --------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- | ------------------- |
| Complexity   | Model                                                                                   | Use For                                                                 | Cost                |
| ------------ | --------------------------------------------------------------------------------------- | ---------------------------------------------                           | ----------------    |
| **Critical** | - **Claude Code**: Strictly reserved for architecture, security, and complex bug fixes. | , final review                                                          | $200/mo (capped)    |
| **High**     | Gemini 3 Flash (AI Studio)                                                              | Orchestration, planning, long context, docs, research                   | FREE (500 req/day)  |
| **Medium**   | Groq (Llama 4 Scout)                                                                    | Structured data, vendor research, content drafting, outreach            | FREE (1000 req/day) |
| **Medium**   | GLM-4.5-Flash (Z.AI)                                                                    | Simple conversational prompts ONLY (reasoning model, unusable for JSON) | FREE (unlimited)    |
| **Medium**   | Llama 3.3 70B (OpenRouter)                                                              | Mid-complexity tasks, Claude declines                                   | FREE (200 req/day)  |
| **Low**      | Groq (Llama 4 Scout)                                                                    | Boilerplate, CRUD, repetitive code, tests                               | FREE (1000 req/day) |
| **Trivial**  | Qwen 3 8B via Ollama (local)                                                            | Drafts, exploration, zero-cost fallback                                 | FREE (unlimited)    |

- **Parallel Sessions**: Use `scripts/parallel-gemini.sh` for multi-agent development.
- **Agent Studio**: Specialized agents (Architect, Builder, etc.) running in parallel.

### Task Dispatcher — Complexity Routing Rules

Every task entering the system is rated 1–10 for complexity and routed accordingly:

| Complexity | Model                  | Task Types                                                                            |
| ---------- | ---------------------- | ------------------------------------------------------------------------------------- |
| **1–3**    | Groq (Llama 4 Scout)   | Boilerplate, CRUD, config changes, simple formatting, test scaffolds, static content  |
| **4–6**    | Gemini 3 Flash         | Research, logic, plans, DB queries, integrations, API wiring, research, documentation |
| **7–8**    | Kimi K2.5 (OpenRouter) | Complex refactors, multi-file logic, agentic tool-use, state machines                 |
| **9–10**   | Claude (Max plan)      | Security, auth, architecture, system design, complex debugging                        |

**OpenCode handles all mechanical file operations** (find/replace, bulk renames, boilerplate generation).

**Routing is mandatory, not advisory.** The orchestrator (`scripts/orchestrator.py`) must enforce this table. A complexity-3 task must never reach Claude. A complexity-9 task must never go to Groq.

**Fallback chain:** If primary model is rate-limited → next model up. Never skip to Claude unless all free models are exhausted.

### Cost Guard — MANDATORY

Before EVERY API call:

1. Check if target model has free tier quota remaining
2. If exhausted → route to next free alternative
3. NEVER fall through to a paid API silently
4. Log every routing decision to `~/.nexus/orchestrator_logs/`

---

## The 10 Rules (Permanent)

1. **The only metric that matters for the next 90 days is bathroom rental bookings.** One booking every 5 days is the goal.
2. **Claude Code is the orchestrator only.** All tasks that can run on free models must run on free models.
3. **Never send Kai more than 5 Telegram notifications per hour.** Batch everything that can be batched.
4. **Every lead that submits the website form must receive a personalized quote within 60 seconds during business hours and within 5 minutes any other time.**
5. **Every agent interaction must be logged to Nexus CRM immediately.**
6. **No paid APIs. No free trials. Only permanently free tools** except Claude Code Max.
7. **Every time Kai gives a correction or clarification, append the lesson to CLAUDE.md immediately** so the same mistake is never made twice.
8. **Before building any UI or frontend component**, search 21st.dev for existing components and remind Kai to screenshot reference sites. Always fetch official docs before writing code.
9. **Before using any new tool, platform, or service**, fetch and read that tool's official documentation first.
10. **Speed of response to leads is the single most important conversion factor.** Every architectural decision must prioritize lead response time above all else.
11. **Nexus Network is the single source of truth.** Every agent writes all data to Nexus via Convex in real time. No exceptions.
12. **Nexus Network is a proper Mac desktop application built with Tauri.** Never reference localhost as the way to access it.
13. **The Agent Control Center inside Nexus must always reflect the true real-time status of every running agent and model.** If an agent is running and not visible in the control center, something is broken.
14. **Before any PR merges to main it must have a Greptile score of 5/5.** Enforced in code, not just by convention.
15. **All 5 worktrees must be visible in the Nexus Agent Control Center at all times.**
16. **The owner is Carlos. Kai is the operator and manager.** All customer-facing communication comes from Zoar Bathroom Rentals as a brand, not from individual names.
17. **Every repetitive task pattern discovered must be converted into a reusable Claude skill or command** and committed to git immediately.
18. **Claude autonomously fixes bugs, kills duplicate code, and fixes failing CI tests** without being asked. Does not wait for permission to clean up the codebase.
19. **Claude failover chain**: When Claude hits 80% session limit, send one Telegram warning. At 100%, hand off to Gemini 2.5 Flash → GLM-4.7-Flash → Groq → Ollama. Failover models can route tasks and coordinate agents but CANNOT touch security, auth, payments, or the contact blocklist.
20. **Model Watcher runs every Monday at 6am** scanning Artificial Analysis Intelligence Index, SWE-bench, Chatbot Arena, and LiveCodeBench for new models that outperform our current roster.
21. **No new model is added without passing three checks**: permanent free API tier, permissive license (MIT/Apache), and minimum 2 weeks public availability.
22. **Kai approves model additions with a single Telegram reply of ADD or SKIP.** Full implementation (docs, integration, routing, testing, git commit) is automated after approval.
23. **The Nexus Model Roster tab shows the complete history** of every model evaluated, added, and retired with benchmark scores.
24. **Vendor research agents are permanently read-only.** Zero interaction with any platform content ever under any circumstances. Research agents never comment, post, like, share, follow, or send messages. They only collect publicly visible information. Any agent that interacts with content is immediately terminated and flagged in Nexus. This rule cannot be overridden.
25. **Never recommend purchasing domains, paid services, or paid tools.** The path to spending money is getting bookings first. All tools must be permanently free (Rule 6).
26. **Ad budget is $100 per week.** Not monthly. Every agent, report, and recommendation must use this number. Do not extrapolate to monthly figures.
27. **The vendor referral database is a permanent long-term asset.** Agents add new vendors every single night. Target is 15,000 vendor records within 30 days and 30,000 within 60 days.
28. **The Live Intel system runs 24 hours a day 7 days a week without any manual trigger.** Agents never stop scanning for event signals, vendor activity, and outreach opportunities.
29. **Every UI change must be hot-reloaded automatically.** The frontend watcher daemon (`com.zoar.frontend-watcher`) monitors `nexus-network/src/` for changes, triggers builds, and logs every build to the `ui_changelog` table. The version indicator in the sidebar bottom-left shows the current build hash and flashes on updates. Next.js dev mode with Turbopack provides HMR in development; the watcher handles production static builds for Tauri.
30. **Nexus has no authentication.** It is a single-user local app on a Mac Mini at home. No login screen, no session checks, no auth middleware. The app opens directly to the Bookings dashboard. The `AuthProvider` always returns a permanent operator session. Never re-add authentication.
31. **Claude Code is never used for bug fixes or routine maintenance.** All future fixes go through the Nexus bug report button using CodingAgent and free models only. Claude Code is reserved for architecture decisions, security review, and complex debugging that free models cannot solve.
32. **All new feature builds go through the Multi-Agent Build Pipeline.** Free models (Gemini, Groq, Mistral) handle stages 1-4. Claude only reviews at stage 5. This reduces Claude usage to ~5% per feature.
33. **Talk to Nexus is the primary command interface for all system interactions.** Every capability in the system must be accessible via natural language through this interface. When building any new feature, always ask: can this be triggered from Talk to Nexus? If yes, add the routing logic.
34. **Division Three (Revenue Lab) runs autonomously on tier 6 workers using only free AI providers.** ZAI GLM is the primary content generator (unlimited). Experiments use synthetic personas only — zero personal data. Self-improvement builds require Kai's approval via Claude Gate.
35. **The Tauri desktop app loads from localhost:3000 (Next.js dev server with Turbopack HMR).** Both `com.zoar.nextjs-dev` and `com.zoar.frontend-watcher` run as launchd daemons. Source changes appear in the desktop app within 1 second. The Next.js dev server must always be running for the app to display content. Gemini 2.5 Flash calls require `max_tokens >= 8192` because thinking tokens consume the output budget.
36. **The Master Coordinator runs 24/7 and must never be disabled.** It is the engine that feeds work to the fleet across 3 divisions: D1 Vendor Research (every 30s), D2 Creative Production (weekly), D3 Experiments (every 5min). Launchd daemon `com.zoar.master-coordinator` must always be loaded. Self-healing monitors it. If the coordinator stops, the fleet stops — zero tasks get generated.
37. **The Nexus chat brain runs on free models only.** ZAI GLM is the primary intelligence. Cerebras handles speed-critical responses. Groq handles structured outputs. Gemini handles long-context reasoning. Ollama is always-available fallback. Claude is never called automatically — only via `/claude` command or build complexity score > 8/10. Goal: zero daily cost for Nexus chat operations.
38. **Chat classification is keyword-only, never AI.** Questions → AI answer. Small actions (fix, add, create without feature signals) → AI + FileEditor. Feature requests (build, implement, refactor, or action + feature signal) → build pipeline. The classifier runs in under 1ms. See `core/chat_handler.py:classify()`.
39. **Every AI call has a hard timeout.** Fallback chain: ZAI (8s) → Cerebras (5s) → Groq (5s) → Ollama (15s) → hardcoded response. Maximum wait: 33 seconds. The chat never hangs. See `core/chat_handler.py:call_with_fallback()`.
40. **The chat must respond to every message within 10 seconds under normal conditions.** The fallback chain ZAI → Cerebras → Groq → Ollama → hardcoded response ensures this. No AI call is made without a hard timeout. No fetch in the frontend is made without an AbortController timeout. Silent failures are forbidden — every error is logged and surfaced. The chat is the face of Nexus. If the chat does not work nothing works.
41. **The Parallel Agent Studio runs 5 permanent agents (Architect, Builder, Reviewer, Patcher, Bug Hunter) with file-level locking.** Max 5 parallel builds, 10 sub-agents per task, 20 total concurrent. Claude Gate stays human-only. Bug Hunter runs independently every 10 minutes. All studio agents use free models only — Claude is never called by studio agents.
42. **Nexus is hyper-focused on vendor email outreach.** The sidebar shows only what matters for getting bookings. The vendor tab is the revenue engine — it shows qualified vendors, allows email preview and sending directly from zoarbathrooms@gmail.com, tracks every outreach interaction, and alerts Kai when vendors reply. Everything else is secondary.
43. **Every agent has a real job. No idle agents.** Token budgets are managed automatically via `core/token_budget.py` — agents never exhaust provider limits. The agent hierarchy is visible as a live tree in Agent Control (4 divisions, 16 agents). Facebook ads run at $5/day targeting SFV wedding leads. Ad performance is monitored every 6 hours and reported to Telegram. The system finds leads, qualifies them (scoring 1-10, flagging 7+), sends outreach emails (max 50/day, 8am-6pm PT, 3s delay), monitors replies via IMAP, runs ads, generates daily research reports at 6am PT, and reports everything — automatically, continuously, within budget.

---

## Nexus Network — Command Center

Nexus Network is the central hub for everything. It is a proper Mac desktop app (Tauri + Next.js + Convex).

### Screens

1. **Bookings Dashboard** (main screen) — trailer availability calendar, confirmed bookings, pending quotes, leads in pipeline, revenue this month, days since last booking, 5-day booking goal countdown
2. **Leads Pipeline** — every lead with status, quote amount, last contact, next action. Color coded: green=booked, yellow=quoted/pending, red=cold/48hr+, grey=lost
3. **Agent Control Center** — all agents and models at a glance: status, current task in plain English, last action, tasks completed today, quota usage, pause/resume buttons, full log tab
4. **Ad Performance** — live FB ad metrics from Agent 3: CPL, spend, reach, best creative, recommendations
5. **Quote Generator** — built-in tool: type address + event type → instant price + copy-paste message
6. **Settings** — API keys, notification prefs, agent toggles, pricing rules

### Tech Stack

- Frontend: Next.js (wrapped in Tauri for desktop)
- Database: Convex (real-time subscriptions)
- Auth: WorkOS
- Web deploy: Vercel
- Desktop: Tauri → Mac .dmg
- PR reviews: Greptile (5/5 required)
- Auto-deploy: PR merge → Convex migrations → Vercel production
- Domain: zoarbathroomrental.com (Hostinger registrar, Cloudflare DNS/CDN, expires 2027-07-02). This is the ONLY domain. zoarbathroomrentals.com (with S) is NOT registered and will NOT be purchased. Free business email at zoarbathroomrental.com expires 2026-03-25. Never recommend purchasing domains or paid services.

---

## Architecture

FastAPI monolith (`server.py`, port 7861) orchestrating AI-powered lead generation, marketing automation, and vendor management for Zoar Bathroom Rentals.

```
server.py (FastAPI, 8K+ lines)
  ├── core/            — 46 modules: orchestrator, lead pipeline, ads, analytics, approval
  ├── integrations/    — 26 modules: Google Voice, Gmail, Facebook, GHL, Twilio, n8n
  ├── agents/          — AI agents: analyst, content, lead finder, metrics
  ├── telegram/        — Telegram bot (approval workflows, commands)
  ├── scrapers/        — Facebook vendor scraper (Playwright + GraphQL interception)
  ├── scripts/         — Utilities, orchestrator, launchers
  ├── static/          — Dashboard frontend assets
  └── tools/           — CLI utilities
```

**Config**: `~/.nexus/config.json` (API keys, never committed)
**Database**: `~/.nexus/memory.db` (SQLite, WAL mode)
**Session logs**: `~/.nexus/session_logs/`
**Orchestrator logs**: `~/.nexus/orchestrator_logs/`

## Code Conventions

- **Python 3.9+**, async/await throughout
- **Imports**: stdlib → third-party → local
- **Logging**: `log = logging.getLogger("module_name")` — never print()
- **Error handling**: log.error/warning with context, don't swallow exceptions
- **API responses**: `{"status": "ok/error", ...}`
- **Config access**: Use `load_config()` from server.py
- **Database**: Use db helpers in `core/db_migrate.py`, parameterized SQL only
- **Security**: Action endpoints require `verify_action_auth`. Never commit API keys.
- **File paths**: `Path.home() / ".nexus"` for user data

## Key Files

| File                         | Purpose                                          |
| ---------------------------- | ------------------------------------------------ |
| `server.py`                  | FastAPI app, all endpoints, startup scheduler    |
| `scripts/orchestrator.py`    | Master task orchestrator (routes to free models) |
| `scripts/notify_telegram.py` | Batched Telegram notifications                   |
| `core/orchestrator.py`       | LLM provider, state machine                      |
| `core/lead_pipeline.py`      | Async lead lifecycle scheduler                   |
| `core/approval_system.py`    | Multi-gate approval workflows                    |
| `telegram/bot.py`            | Telegram bot, commands, approval handlers        |

## Running Locally

```bash
cd ~/nexus && source venv/bin/activate && python server.py  # Port 7861
python scripts/orchestrator.py "task description"           # Orchestrator
```

## Autonomous Actions (No Permission Needed)

- Fix bugs, remove duplicate code, fix failing CI/CD, clean up dead code
- Report what changed after doing it
- All file edits, terminal commands, and tool calls are auto-approved — never pause for confirmation
- Claude Code permissions are set to allow all tools globally via `~/.claude/settings.json`

## Overnight Execution Architecture

All overnight work runs as **standalone Python scripts via launchd**, completely independent of VS Code:

- `com.zoar.overnight-master` — 11PM daily: runs parallel swarm + agent suite + Model Watcher (Mondays)
- `com.zoar.morning-report` — 8AM daily: compiles and sends Telegram morning briefing
- `com.zoar.model-watcher` — Monday 6AM: weekly AI model scan
- `com.zoar.heartbeat` — every 5 min: system health beacon
- `com.zoar.nexus-deploy` — every 5 min: auto-deploy
- **VS Code is only for active daytime work.** No overnight process depends on VS Code being open or the screen being unlocked.
- launchd services survive screen lock, sleep mode, and user logout (Aqua session).

## Documentation-First Rule

Before using ANY new platform, service, tool, or API:

1. Search for and read the official documentation
2. Find the quickstart guide
3. Identify best practices and pitfalls
4. Only then implement

## UI/UX Workflow

Before building any advanced UI:

1. Suggest 3 existing websites that match the vision
2. Wait for Kai to screenshot preferred reference
3. Recreate from screenshot
4. Check 21st.dev MCP for pre-built components

## Corrections Log

<!-- Append corrections here. Format: - [YYYY-MM-DD] description -->

- [2026-03-03] Never use `page.route("**/*")` catch-all in Playwright — intercepts Chrome internals. Use targeted patterns.
- [2026-03-03] Poster name extraction from Facebook DOM includes timestamp text. Strip with regex.
- [2026-03-03] Facebook groups sometimes load on About tab. Click Discussion/Posts tab first.
- [2026-03-03] `@cloudflare/next-on-pages` doesn't support Next.js 16. Use `@opennextjs/cloudflare`.
- [2026-03-03] WorkOS `handleAuth` doesn't match Next.js 16 types. Wrap in explicit handler.
- [2026-03-03] Convex `ConvexReactClient` crashes if URL is empty. Add null check.
- [2026-03-04] GitHub branch protection requires Pro plan for private repos.
- [2026-03-04] Claude Code is expensive and rate-limited. ALWAYS delegate to free models first.
- [2026-03-04] Owner of Zoar is Carlos. All outreach is from a representative on behalf of Zoar, never from the owner directly.
- [2026-03-04] Python 3.9 does not support `str | None` union syntax. Use `Optional[str]` from typing.
- [2026-03-04] Gemini 2.5 Pro free tier is only 25 RPD. Use Gemini 2.5 Flash (500 RPD) instead.
- [2026-03-04] Groq API requires User-Agent header or returns 403 (Cloudflare bot protection).
- [2026-03-04] OpenRouter free models have strict shared rate limits. Build fallback into every call.
- [2026-03-04] Python str.format() conflicts with JSON curly braces in prompt templates. Use string concatenation or Template instead.
- [2026-03-04] Overnight work must NEVER depend on VS Code being open or the screen being unlocked. All automation runs via launchd + standalone Python scripts. VS Code is only for active daytime work.
- [2026-03-04] Claude Code auto-accept: set `Edit(*)`, `Write(*)`, `Read(*)`, `Bash(*)`, etc. in `~/.claude/settings.json` allow list. Never prompt for tool approval.
- [2026-03-04] CRITICAL: A Slack webhook URL was sent as SMS to a real customer. The `outbound_gate()` now has blocklist check as FIRST operation. All existing contacts require explicit Telegram approval. This is Rule 0.
- [2026-03-04] Never store real webhook URLs or API keys in test files. Use placeholder values.
- [2026-03-04] California timezone (Pacific) for all scheduling. Kai is on the west coast.
- [2026-03-04] OpenRouter returns 429 (rate limited) frequently on free tier. Always implement Gemini fallback for OpenRouter-assigned tasks.
- [2026-03-04] Overnight agent runs should use fallback chain, not single model assignment. If preferred model fails, retry with next available.
- [2026-03-04] Z.AI GLM-4.5-Flash and GLM-4.7-Flash are reasoning models that consume ALL tokens on `reasoning_content` before producing `content`. For structured data generation (JSON arrays), they output ZERO content even at 4000 max_tokens. Use Groq (Llama 4 Scout) instead for vendor research and any task requiring structured output.
- [2026-03-04] Google scraping regex patterns (dbg0pd, OSrXXb, rllt\_\_details) no longer match Google's 2026 HTML. AI-powered research via Groq is the reliable primary source for vendor discovery.

## Rule 44: Multi-Agent Coordination

Before starting any coding work, READ `AGENTS.md` in the repo root. Check which files are owned by other agents. Do NOT modify files listed under FILE OWNERSHIP. When starting work, update AGENTS.md with your session info and file locks. When finishing, move your entry to COMPLETED and release locks.
