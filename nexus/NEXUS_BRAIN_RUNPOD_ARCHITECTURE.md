# Nexus Brain: Runpod-First Architecture (H100 NVL)

## Mission
Build the agentic intelligence layer in Runpod first, then deploy proven artifacts into Nexus later.

Core objective:
- More qualified leads
- More bookings
- Better conversion diagnostics
- Less manual overhead for Kai

## 1) System Architecture (Layered)

### A. Planning / Reasoning Layer
- `brain_supervisor` (orchestrator)
- Specialist workers:
  - `lead_ranker`
  - `venue_partner_ranker`
  - `creative_critic`
  - `outreach_angle_selector`
  - `ops_diagnostic_critic`
- Role:
  - decompose goals
  - route tasks
  - request eval
  - extract lessons

### B. Memory Layer
- Four memory classes:
  - episodic (what happened + outcome)
  - semantic (stable facts/domain knowledge)
  - strategic (high-level winning/losing patterns)
  - procedural (playbooks + workflows)
- Retrieval first, then generation.

### C. Evaluation Layer
- Mandatory scoring ledger:
  - lead quality
  - venue fit
  - creative quality
  - outreach quality
  - experiment outcomes
- All key outputs become measurable objects.

### D. Experiment Layer
- Explicit experiment objects:
  - goal
  - hypothesis
  - constraints
  - metric + threshold
  - result
  - lesson
- Lessons are written back into strategic memory.

### E. Data Layer
- Runpod lab artifacts are split into:
  1. `training_artifacts/` (datasets, labels, rubrics, memory snapshots, experiment templates, training configs)
  2. `exports/` (adapter/model export contracts and later produced adapters)
  3. `integration_hooks/` (Nexus import contract only, not active wiring)

### F. Model Layer
- One stronger general reasoner profile + specialist adapters.
- Router decides model-by-task type.

### G. Safety / Approval Layer
- Any risky action path remains approval-gated.
- Lab phase is research/training only (no outbound send automation).

### H. Deployment Layer (later)
- Mac mini local runtime
- adapter registry
- model router profiles
- rollback-safe staged activation

## 2) Framework Stack (Recommended)

### Primary
- LangGraph for durable, explicit state-machine style agent workflows.
- vLLM for high-throughput inference in Runpod.
- Unsloth/LoRA for cost-efficient specialist adaptation.
- Qdrant (or equivalent vector store) for scalable retrieval.
- OpenTelemetry + MLflow for trace + eval observability.
- Ollama for local survival fallback on Mac mini.

### Why this stack
- reliable state transitions
- strong observability
- local survivability
- easy specialist split instead of one giant brittle model

### What not to do initially
- no uncontrolled multi-agent swarm
- no direct autonomous outbound action
- no “train one mega-model and hope”

## 3) Build Order (Runpod-First)

1. Runpod lab pipeline + artifact separation
2. Dataset/label/memory/eval artifact generation
3. Specialist training configs + benchmark suite
4. Export contract definition
5. Train adapters in Runpod
6. Evaluate and gate
7. Integrate into Nexus runtime (downstream phase)

## 4) What to Train in Runpod

- `lead_ranker`: score lead quality and booking intent
- `venue_partner_ranker`: choose best venue partnership angle
- `creative_critic`: score ad creative quality and failure reasons
- `outreach_angle_selector`: choose first message angle/subject/opening
- `ops_diagnostic_critic`: detect funnel/ops weak points and propose low-risk experiments

## 5) What Should Stay Data/Memory-Driven

- hard safety rules
- approval requirements
- pricing constraints
- service-area constraints
- deterministic routing constraints
- post-deploy policy gates

## 6) Agent Hierarchy (Initial)

- Supervisor
  - Lead Intelligence specialist
  - Ads Intelligence specialist
  - Outreach specialist
  - Ops/Critic specialist

All specialists produce scored outputs + rationales.
No production side effects without approval gate.

## 7) Hierarchy Evolution (Later)

- Supervisor can spawn temporary sub-agents only when:
  - a task exceeds complexity threshold
  - benchmark confidence is below threshold
  - decomposition provides measurable value
- Sub-agent outputs must pass critic/eval before promotion.

## 8) Learning Loop (How It Actually Learns)

1. Observe (fresh lead/ad/outreach/ops data)
2. Analyze (specialist reasoning)
3. Score (evaluation ledger)
4. Experiment (structured hypothesis runs)
5. Outcome capture (metrics + qualitative result)
6. Lesson extraction (strategic memory writeback)
7. Behavior update (routing/prompts/model preference)

## 9) Local Deployment Plan (Later)

- Import adapters from `exports/`
- Register in local model router
- Import memory/eval baselines
- Activate recommendation-only mode first
- Compare against baseline for fixed window
- Promote to higher autonomy only if metrics hold
- Keep rollback manifest for every promotion

## 10) Initial Services/Folders

Implemented now:
- `core/runpod_brain_lab.py` (Runpod-first lab builder)
- API endpoints:
  - `POST /api/runpod-brain-lab/plan`
  - `POST /api/runpod-brain-lab/start`
  - `GET /api/runpod-brain-lab/status`
  - `GET /api/runpod-brain-lab/runs`
- `tests/test_runpod_brain_lab.py`

Generated per run:
- `~/.nexus/runpod_brain_lab/run_<id>/runpod_lab/training_artifacts/*`
- `~/.nexus/runpod_brain_lab/run_<id>/runpod_lab/exports/*`
- `~/.nexus/runpod_brain_lab/run_<id>/runpod_lab/integration_hooks/*`

---

This keeps training/adaptation fully separated from live Nexus runtime integration.
