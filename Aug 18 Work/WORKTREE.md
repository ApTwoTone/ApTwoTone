# Fable 5 worktree — file-level status

Updated 2026-08-18 ~18:10 PT.

Spark worktree: `/home/kai/Desktop/Project-Bunny-fable5-landing`  
Branch: `fable5/wi-landing`  
Base: `5c260ab2`

## Modified (not committed on Spark)

- `bin/bunny_adjacent_enrichment_cycle.sh` — WI-1 canary + WI-9.7 generalized gate
- `bin/bunny_health_probe.py` — 9.1 / 9.2 / 9.5 / 9.6 / 9.7
- `bin/bunny_phone_retraction.py` — 9.6 adopt
- `bin/bunny_publication_authority_audit.py` — 9.6 adopt
- `bin/bunny_publish_all.py` — 9.6 adopt (publish loop + terminal-advance)
- `bin/tests/test_adjacent_enrichment_cycle.py` — WI-1 + 9.7 tests
- `bin/tests/test_bunny_health_owner_apn_due.py` — 9.1
- `bin/tests/test_bunny_health_yield_vocabulary.py` — 9.2
- `discovery/runtime/netr/la_stageb_bridge.py` — 9.6 (live on discovery cut)
- `discovery/runtime/spark/apn_norm_bare_sweep.py` — 9.6 (live on discovery cut)
- `discovery/spine/mint.py` — 9.6 (live on discovery cut)
- `enrichment/apn/apn_retry.py` — WI-1 `record_lane_fault` + WI-5 clocks
- `enrichment/apn/realist_owner_resolver.py` — WI-1
- `enrichment/apn/resolution_ladder.py` — WI-5 PENDING keys 55/65/115
- `enrichment/providers/bunny_provider_lane.py` — 9.7 (live on save once on `main`)
- `runtime/enrichment/dossier_spine_writeback.py` — 9.6
- `runtime/enrichment/live_nod/core/router.py` — WI-8 strand receipt
- tests for Realist owner resolver / runner / private nod router

## New (not committed on Spark)

- `scripts/rearm_realist_owner_lane.py`
- `enrichment/apn/second_source.py` + RAMOS/DOCTOLERO fixtures + `test_second_source_tiebreak.py`
- `enrichment/apn/tests/test_realist_grid_read.py`
- `enrichment/apn/tests/test_realist_lane_fault_isolation.py`
- `enrichment/apn/tests/test_realist_owner_query_live_names.py`
- `bin/tests/test_lane_canary_gate.py`
- `bin/tests/test_bunny_health_served_adjudication.py`
- `bin/tests/test_actor_attribution_adoption.py`

## Still waiting

1. WI-5 live rungs (TT roll-candidate confirm, Matrix History lift, NARRPR foreclosure).
2. Register GATE_SUITES + MUTANTS in the same commit as the behavior.
3. `scripts/regenerate_release_manifests.sh` for any ENR companion (`bunny_adjacent_enrichment_cycle.sh`).
4. Other session releases Spark `main`.
5. Merge worktree → `main`, then `python3 bin/bunny_deploy --plan` after 18:00 PT.
6. Runtime waivers for `tt` / `matrix_pr` belong in live `data/enrichment-state/` at deploy time, not in git. Do not waive `realist_owner`.

## Refuse list (unchanged)

No invented APN. No copy of Discovery APN onto intake. No `source_active=1`. No `--apply-plan`. No `bunny-enrich.timer`. No adjacent `OnSuccess=skiptrace`. No weaken serve floor / `_market_blocks_call_now`. No VNC fleet restart. Rates ≤2/min/provider.
