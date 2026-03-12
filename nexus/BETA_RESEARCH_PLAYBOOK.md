# Beta Research Playbook (Safe Overnight Mode)

## Goal
Run a high-signal lead research loop in **research-only mode**:

`Public Signals -> Collection -> Extraction -> Scoring -> Validation -> Decision -> Launch -> Feedback -> Reinforcement`

This mode is intentionally **isolated** from production lead flow and does **not** message anyone.

## What Was Implemented Tonight

1. New isolated backend engine: `core/beta_research.py`
1. Separate database: `~/.nexus/beta_research.db` (no writes to `memory.db`)
1. New APIs:
   - `GET /api/beta-research/status`
   - `GET /api/beta-research/runs`
   - `GET /api/beta-research/stages`
   - `GET /api/beta-research/leads`
   - `GET /api/beta-research/providers`
   - `POST /api/beta-research/run-cycle`
   - `POST /api/beta-research/feedback`
1. New frontend tab above settings: **Beta Research**
   - File: `nexus-network/src/components/beta-research.tsx`
   - Shows safety status, run controls, stage funnel, provider/key visibility, lead table, feedback buttons
1. New **Runpod Sprint Orchestrator** backend + UI controls:
   - File: `core/runpod_sprint.py`
   - APIs:
     - `POST /api/runpod-sprint/plan`
     - `POST /api/runpod-sprint/start`
     - `GET /api/runpod-sprint/status`
     - `POST /api/runpod-sprint/stop`
     - `POST /api/runpod-sprint/promote`
   - Integrated into Beta Research page with spend meter, phased checkpoints, output counters, and promotion handoff.
   - Default launch mode is now `research_creative_only` (no Meta ad-account writes).
   - Creative source filtering now enforces brand relevance (restroom/trailer/event assets only).
   - Lead phase now produces `cold_email_priority_leads.csv` + `cold_email_top_50.csv` with enrichment, quality scoring, and safe greetings (`Hi <Name>,` or `Hi there,`).
   - When no verified email is found, it writes `cold_email_enrichment_needed.csv` for manual email research instead of guessing fake names/addresses.

## Safety Guarantees

- Outbound disabled by design (no send SMS, no send email, no call)
- Platform login disabled by policy
- Blocked login domains include:
  - `facebook.com`, `instagram.com`, `messenger.com`, `x.com`, `twitter.com`, `linkedin.com`, `tiktok.com`
- Launch stage is queue-only (`queued_review`), not delivery

## Recommended Architecture (Mapped to Your Funnel)

### 1) Public Signals
- Keep inputs constrained to trusted, public sources and explicit target geos/categories.
- Maintain reusable query seeds and region buckets.

### 2) Collection
- Pull from multiple public channels (maps/search/directories), then normalize aggressively.
- Add deterministic idempotency keys to prevent duplicate processing/retries.
  - Reference: Prefect idempotency guidance stresses unique keys to prevent duplicate imports during retries.

### 3) Extraction
- Convert raw records into canonical entities (name/phone/email/domain/city/source).
- Generate machine-readable evidence snippets per lead.

### 4) Scoring
- Use explicit weighted rubric (0-100) and score breakdown fields.
- Keep score explainability visible per lead.

### 5) Validation
- Enforce quality gates:
  - contact-path presence
  - dedup
  - website reachability check
  - confidence/flag labels (`verified`, `inferred`, `needs_review`)
- Promote only above min score.

### 6) Decision
- Route each lead to a single first pitch angle:
  - preferred vendor
  - backup vendor
  - referral partner
  - overflow/peak-date

### 7) Launch
- In beta: **never outreach**.
- Only create queue candidates for manual review.

### 8) Feedback
- Capture explicit human labels (`good`, `bad`, `needs_review`) in UI.
- Tie labels to the exact lead and run.

### 9) Reinforcement
- Use feedback-driven policy updates conservatively (threshold shifts, penalties), versioned in DB.
- Never allow reinforcement to bypass safety lock.

## Why This Structure Is Reliable

- Stateful graph execution with persistence/checkpoint patterns is a proven fit for staged agent loops.
- Data quality gates in orchestration pipelines are standard practice before downstream actions.
- Unified observability and trace/log correlation reduce blind spots when debugging multi-stage automation.

## External References Used

- LangGraph runtime model (stateful graph + persistence + interrupts):
  - https://langchain-ai.github.io/langgraphjs/reference/classes/langgraph.Pregel.html
- LangSmith evaluation feedback loop patterns:
  - https://www.langchain.com/langsmith/evaluation
- Prefect idempotent pipeline guidance:
  - https://www.prefect.io/blog/the-importance-of-idempotent-data-pipelines-for-resilience
- Airflow data quality demo patterns (GitHub):
  - https://github.com/astronomer/airflow-data-quality-demo
- OpenTelemetry logs/traces/metrics correlation (for debugging full pipeline):
  - https://opentelemetry.io/docs/specs/otel/logs/
- Google Apps Script trigger/event model (for sheet-based ingestion guardrails):
  - https://developers.google.com/apps-script/guides/triggers/events
- Lead Ads testing caveat (community-reported): test leads can miss ad metadata fields:
  - https://stackoverflow.com/questions/75767809/facebook-leadgen-webhook-does-not-include-ad-id-nor-adgroup-id

## Notes for Tomorrow

1. Run one safe cycle from **Beta Research** tab.
1. Review top leads and feedback-tag at least 20.
1. Compare beta lead quality against current production source.
1. Only after quality passes, decide what subset should be promoted into production pipelines.
