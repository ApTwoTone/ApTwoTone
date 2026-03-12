# Nexus Brain — Codex Handoff Document

**Read this first.** Everything on the Mac (Nexus) side is fully wired and waiting.
Your only job is to finish training, quantize, and transfer. Then the system goes live.

---

## What the Mac Already Has (Zero Work Needed Here)

| Component | Status |
|-----------|--------|
| `GET /api/brain/status` — reports online/pending per specialist | ✅ Live on port 7860 |
| `POST /api/brain/query` — routes to Ollama, logs latency | ✅ Live |
| `POST /api/brain/feedback` — logs booked/lost outcomes for future fine-tuning | ✅ Live |
| `GET /api/brain/insights` / `/knowledge` / `/discussions` — brain UI tabs | ✅ Live |
| 13 Ollama Modelfiles (system prompts + parameters pre-configured) | ✅ At `~/.nexus/brain_lab/modelfiles/` |
| Post-transfer smoke test (creates Ollama models + tests + Telegram alert to Kai) | ✅ `scripts/post_transfer_smoke_test.py` |
| Feedback loop: marking a lead "booked" or "lost" auto-logs to feedback JSONL | ✅ `core/services/lead_service.py` |
| Lead scorer calls specialist blend (60% heuristic + 40% nexus-lead-ranker) | ✅ `core/lead_scoring.py` |
| Brain agent executor (5 agents poll task queue, route to specialists, store memory) | ✅ `scripts/brain_agent_runner.py` + launchd daemon |
| nexus-coder routing in Nexus Talk chat (coding tasks → nexus-coder first) | ✅ `core/chat_handler.py` |
| Division Three autonomous entrepreneur stream (AI-run side experiments) | ✅ `core/division_three.py` |
| Phase 6 event lead discovery (Craigslist events → leads table → Telegram if score ≥ 60) | ✅ `scripts/lead_discovery_daemon.py` |

**The agents run right now using free models (Groq, Gemini, ZAI) as fallback.
The moment a GGUF lands in `~/.nexus/brain_lab/exports/`, that specialist goes live automatically.**

---

## Your Remaining Tasks (x → y → z)

### x — Complete the specialist training queue

Train in this order (highest business value first):

| Priority | Specialist | Dataset | Approx VRAM | Notes |
|----------|-----------|---------|------------|-------|
| 1 | `conversion_specialist` | conversion_specialist.jsonl | ~75GB | Currently training |
| 2 | `objection_resolver` | objection_resolver.jsonl | ~75GB | |
| 3 | `fb_media_buyer` | fb_media_buyer.jsonl | ~75GB | |
| 4 | `venue_partnership_closer` | venue_partnership_closer.jsonl | ~75GB | |
| 5 | `follow_up_cadence_specialist` | follow_up_cadence_specialist.jsonl | ~75GB | |
| 6 | `quote_personalizer` | quote_personalizer.jsonl | ~75GB | |
| 7 | `ad_creative_specialist` | ad_creative_specialist.jsonl | ~75GB | |
| 8 | `vendor_referral_specialist` | vendor_referral_specialist.jsonl | ~75GB | |
| 9 | `seasonal_demand_predictor` | seasonal_demand_predictor.jsonl | ~75GB | |
| 10 | `nexus_coder` | combined_dataset.jsonl | ~75GB | **Train last** — biggest dataset |

`lead_ranker`, `venue_partner_ranker`, `outreach_angle_selector` were trained in the original queue.
If they're done, start their quantization immediately (see step y).

---

### y — Quantize each completed model to GGUF Q4_K_M

After each specialist finishes training, run:

```bash
# Option A: Unsloth built-in export (preferred if using Unsloth)
python -c "
from unsloth import FastLanguageModel
model, tokenizer = FastLanguageModel.from_pretrained('/workspace/nexus_brain_lab/run_14/outputs/{SPECIALIST_NAME}')
model.save_pretrained_gguf('{SPECIALIST_NAME}', tokenizer, quantization_method='q4_k_m')
"

# Option B: llama.cpp quantize
./quantize /workspace/nexus_brain_lab/run_14/outputs/{SPECIALIST_NAME}/model.bin \
           /workspace/nexus_brain_lab/run_14/runpod_lab/exports/{SPECIALIST_NAME}.Q4_K_M.gguf \
           Q4_K_M
```

**Output files must be named exactly:**

| GGUF filename | Ollama name (pre-configured) | Role |
|--------------|----------------------------|------|
| `lead_ranker.Q4_K_M.gguf` | `nexus-lead-ranker` | Lead scoring 0-100 |
| `conversion_specialist.Q4_K_M.gguf` | `nexus-conversion` | Quote response writing |
| `objection_resolver.Q4_K_M.gguf` | `nexus-objections` | Handles price/hesitation |
| `fb_media_buyer.Q4_K_M.gguf` | `nexus-fb-buyer` | Facebook Ads optimization |
| `venue_partnership_closer.Q4_K_M.gguf` | `nexus-venue-closer` | Venue referral closing |
| `follow_up_cadence_specialist.Q4_K_M.gguf` | `nexus-followup` | Follow-up timing & tone |
| `quote_personalizer.Q4_K_M.gguf` | `nexus-quote` | Personalized quote copy |
| `ad_creative_specialist.Q4_K_M.gguf` | `nexus-ad-creative` | Ad copy generation |
| `vendor_referral_specialist.Q4_K_M.gguf` | `nexus-vendor-referral` | Vendor outreach |
| `seasonal_demand_predictor.Q4_K_M.gguf` | `nexus-seasonal` | Demand prediction |
| `venue_partner_ranker.Q4_K_M.gguf` | `nexus-venue-ranker` | Venue partner ranking |
| `outreach_angle_selector.Q4_K_M.gguf` | `nexus-outreach-angle` | Best outreach angle |
| `nexus_coder.Q4_K_M.gguf` | `nexus-coder` | Coding agent (trains last) |

Collect all GGUF files into one flat directory:
`/workspace/nexus_brain_lab/run_14/runpod_lab/exports/`

---

### z — rsync to Mac + notify Kai

```bash
# Replace <MAC_LOCAL_IP> with the Mac Mini's LAN IP (e.g. 192.168.1.x)
rsync -avz --progress \
  /workspace/nexus_brain_lab/run_14/runpod_lab/exports/ \
  kai@<MAC_LOCAL_IP>:~/.nexus/brain_lab/exports/
```

After rsync completes, send Kai a Telegram message (chat ID: `8540603351`):

```
Brain transfer complete. X/13 GGUF files transferred.
Kai: run `python scripts/post_transfer_smoke_test.py` to go live.
```

Then Kai runs one command on the Mac:

```bash
cd ~/nexus && source venv/bin/activate
python scripts/post_transfer_smoke_test.py
```

This script:
1. Calls `ollama create nexus-{name}` for every GGUF that has landed
2. Sends a test prompt to each model via Nexus
3. Prints PASS/FAIL table
4. Sends Kai a Telegram summary: "X/13 models online. Brain is live."

**Partial transfers are supported.** Run the smoke test after each batch of GGUFs arrives.
The script skips models whose GGUF hasn't transferred yet and processes what's there.

---

## How the Feedback Loop Works (Future Fine-Tuning)

Every time a lead is marked "booked" or "lost" in Nexus:
- A background thread fires `POST /api/brain/feedback`
- Outcome is logged to `~/.nexus/brain_lab/feedback/positive_examples.jsonl` or `negative_examples.jsonl`
- These files accumulate real booking data for the next training run
- After 500+ examples, run a fine-tuning pass using these as preference data (DPO or SFT)

To check feedback accumulation:
```bash
wc -l ~/.nexus/brain_lab/feedback/positive_examples.jsonl
wc -l ~/.nexus/brain_lab/feedback/negative_examples.jsonl
```

---

## Architecture Reference (How It All Connects)

```
RunPod H100 NVL (training)          Mac Mini (live inference)
─────────────────────────           ──────────────────────────────────────
Qwen3.5-35B-A3B base model          FastAPI server (port 7860)
  └── LoRA fine-tune per specialist    ├── /api/brain/query → Ollama :11434
  └── Quantize to Q4_K_M GGUF         ├── /api/brain/status → specialist list
  └── rsync → Mac exports dir  ───────►├── ~/.nexus/brain_lab/exports/*.gguf
                                       │
Ollama on Mac                          ├── brain_agent_runner.py (5 agents)
  └── ollama create nexus-*  ◄─────────│   claim tasks → specialists → memory
  └── serves via :11434                │
                                       ├── lead_scoring.py (60/40 blend)
                                       ├── lead_service.py (feedback on booked/lost)
                                       └── chat_handler.py (nexus-coder routing)
```

---

## If Something Goes Wrong

| Problem | Fix |
|---------|-----|
| rsync fails (connection refused) | Check Mac is on same network, SSH enabled in System Settings > Sharing |
| `ollama create` fails | Verify GGUF file isn't truncated: `ls -lh ~/.nexus/brain_lab/exports/` |
| Smoke test shows all FAIL | Check Nexus server is running: `curl http://localhost:7860/api/ping` |
| Model responds but latency > 30s | Normal for Q4_K_M on CPU. Add Ollama GPU offload: `PARAMETER num_gpu 99` in Modelfile |
| Feedback JSONL not growing | Ensure leads are being marked "booked"/"lost" via Nexus CRM, not direct DB writes |
