# Fable 5 worktree — file-level status

Updated 2026-08-18 ~19:10 PT.

**Shipped.** Spark `main` `43ecc294` is live as **v0.1.10** (`bunny deploy` run `20260819T014308Z-43ecc2947798`).

Isolated worktree `/home/kai/Desktop/Project-Bunny-fable5-landing` branch `fable5/wi-landing` was merged into `main` and is no longer the writer.

## Commits that went live

- `4018467b` Fable-5 WI-1/5/8/9 landing
- `5d9da8a2` merge of main (GIS timeout, intake CAS, TT runtime_generation, unreadable-grid pin)
- `72bf01e2` pin enrichment companion hashes after that merge
- `43ecc294` retarget `realist_unreadable_recorded_as_absence` mutant at the WI-1 `header_by_id` parse shape

First deploy of `72bf01e2` rolled back on golden gate (stale mutant anchor). Second deploy of `43ecc294` passed golden gate, waited out the in-flight NETR proof cycle, flipped, smoked.

`BOARD_CONTRACT` stayed **1**. Mac app was relaunched, not rebuilt.

## Still waiting (not a deploy miss)

1. WI-5 live TT / Matrix History / NARRPR rungs — provider day only.
2. One metered Realist multi-result canary before that lane arms.
3. This week’s remaining OWNER_APN / skip-trace work — 08:00 PT Wednesday.
4. WI-11 corroboration — Carlos A/B/C.

## Refuse list (unchanged)

No invented APN. No copy of Discovery APN onto intake. No `source_active=1`. No `--apply-plan`. No `bunny-enrich.timer`. No adjacent `OnSuccess=skiptrace`. No weaken serve floor / `_market_blocks_call_now`. No VNC fleet restart. Rates ≤2/min/provider.
