# Aug 18 Work — Fable 5 landing (phone tracker)

**Repo:** this folder on [ApTwoTone/ApTwoTone](https://github.com/ApTwoTone/ApTwoTone/tree/main/Aug%2018%20Work)  
**When:** Tuesday 2026-08-18 night PT (updated ~19:10 PT)  
**What:** Claude’s unfinished Bunny enrichment-repair session, shipped.

This is a **status mirror only**. No live databases, no secrets, no Spark checkout.

## Live now

| Piece | Status |
|---|---|
| Backend | **`v0.1.10+43ecc2947798`** via `bunny deploy` (journal `20260819T014308Z-43ecc2947798`, outcome success, smoke passed) |
| Board contract | **1** (Mac app did not need a rebuild) |
| Native API `/health` | `ok: true`, routers flyers/operations/aod/netr all true |
| Mac app | Dock `dist/Bunny.app` relaunched via launchd `com.zoar.bunny-app` so the footer can pick up v0.1.10. Git SHA on disk remains `a3ad21bc070c`. |
| `bunny-enrich.timer` | still **disabled** (by design) |
| Provider window | closed 18:00 PT — no overnight skip-trace / TT / Realist / Matrix / NARRPR |

GPU at deploy: `gpu_guard_passed`, compute 46403 MiB, load ~10–19, swap ~20%.

## This week’s NODs (honest)

Window 2026-08-12..18 PT, **recorded by the county** (what “came out this week” means):

- **164 recorded** across the six adjacent counties. **164 named.**
- **0 Los Angeles** — LA’s own source is still published through **2026-08-10**.
- Kern / Orange / Riverside / San Bernardino have **not published through today**; a low count is lag, not loss.
- **353 minted** into Bunny in the same calendar window (includes older recordings that arrived this week). Those are a different set.

Recorded-this-week stages (spine, 164 rows):

| Stage | Count |
|---|---|
| OWNER_APN (named, still resolving parcel / address) | 90 |
| SKIPTRACE | 65 |
| DNC | 5 |
| PUBLISHED | 4 |

103 of 164 have an APN. 61 named rows have no APN yet (ambiguous parcel, timeshare, multi-house, no-parcel, uncovered DNC, serve-floor HOLD). Those are product refusals, not missing software.

**Not fully skip-traced overnight.** Metered provider work does not run after 18:00 PT. Tomorrow 08:00 PT the catch-up lane continues. Realist owner / TT / Matrix PR stay fail-closed until a metered canary proves the repaired parsers (`lane_canary` WARN: those three lanes refuse to arm).

Board: **982** rows, **112 CALL NOW**.

## What shipped in v0.1.10

Spark `main` `43ecc294` (includes the four commits that were already on main but not live: GIS 25 min, intake CAS, TT runtime_generation refresh, unreadable-grid pin).

| Item | Live? |
|---|---|
| WI-1 Realist owner-lane repair + grid canary | yes, **gated** until one metered canary |
| WI-5 stage 2 — offline second-source spine, PENDING ladder 55/65/115 | yes (no live TT/Matrix/NARRPR rungs) |
| WI-8 router silent-strand receipt | yes |
| WI-9.1 starvation ages + `unit=` | yes |
| WI-9.2 Kern wall leaves yield numerator | yes |
| WI-9.5 served-adjudication (`not_ready` never scored) | yes |
| WI-9.6 actor attribution (adoption, not inserts) | yes |
| WI-9.7 lane canary gate | yes. No live waivers. `realist_owner` not waived. |

## Still not done (deliberate)

| Item | Why |
|---|---|
| WI-5 live rungs B/C/A | Need a provider day. Not armed after 18:00. |
| WI-6 TT reconciler | Intake write-back already PASS. |
| WI-9.4 freeze receipt | Would mute a working lane. |
| WI-11 corroboration | Needs Carlos A/B/C. |
| Finish this week’s 90 OWNER_APN + skip-trace remainder | Window closed. Resume Wed 08:00 PT. |

## Open this on the phone

https://github.com/ApTwoTone/ApTwoTone/blob/main/Aug%2018%20Work/README.md
