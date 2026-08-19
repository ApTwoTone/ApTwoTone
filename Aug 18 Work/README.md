# Aug 18 Work — Fable 5 landing (phone tracker)

**Repo:** this folder on [ApTwoTone/ApTwoTone](https://github.com/ApTwoTone/ApTwoTone/tree/main/Aug%2018%20Work)  
**When:** Tuesday 2026-08-18, evening PT  
**What:** finish Claude’s unfinished Bunny enrichment-repair session, without touching Spark `main` while another session holds GIS/TT.

This is a **status mirror only**. No live databases, no secrets, no Spark checkout.

## Right now

Spark `main` is still held by the other Cursor session (`job5-tt-pulse intake-cas gis-timeout realist-golden`).  
All Fable 5 code landed on **worktree branch `fable5/wi-landing`** at HEAD `5c260ab2`.

**Not production.** Nothing here has been `bunny deploy`’d. Provider window closed 18:00 PT; deploy is after that, and only after `main` is free.

## Landed on the worktree (tests on Spark)

| Item | Result |
|---|---|
| WI-1 Realist owner-lane repair | 81 + 12 TT collateral pass. Lane stays gated until one metered canary. |
| WI-5 stage 2 — offline second-source spine | 43 pass. Ladder keys 55/65/115 are PENDING. No live TT/Matrix/NARRPR I/O yet. |
| WI-8 router silent-strand receipt | 6 pass |
| WI-9.1 starvation ages on arrival + `unit=` | 20 pass |
| WI-9.2 Kern wall leaves yield numerator | 9 pass / 2 skip (live-DB halves) |
| WI-9.5 served-adjudication (`not_ready` never scored) | 11 pass |
| WI-9.6 actor attribution (adoption, not inserts) | 22 pass |
| WI-9.7 canary gate | 29 pass. `tt`/`matrix_pr` waived **in the worktree only**. `realist_owner` not waived. |

## Deliberately not done

| Item | Why |
|---|---|
| WI-6 TT reconciler | Intake write-back already PASS. Hash-less stamp would close remaining dispatch paths. |
| WI-9.4 freeze receipt | Same: would mute a working lane. Package kept ready off-box. |
| WI-8 Matrix autonomy recut | Already lifted by today’s deploys. |
| WI-5 live rungs B/C/A | Still waiting on the last recut agent (TT confirm / Matrix History / NARRPR). |
| GATE_SUITES + MUTANTS merge | Parent merge, one commit with the behavior. |
| Merge to Spark `main` | Other session still holds the edit lock. |
| `bunny deploy` | After 18:00 PT, clean tree, golden gate. `bunny-enrich.timer` stays disabled. |
| WI-11 corroboration | Needs Carlos to pick A / B / C. |

## How this session ran

Claude Desktop built landing packages locally against older HEAD `a014dec8` and died on a usage limit (WI-5 + WI-9 agents). Nothing was written to Spark.

This Cursor session recut those appliers against live `5c260ab2`, applied them on an isolated Spark worktree, and left `main` alone.

## Open this on the phone

https://github.com/ApTwoTone/ApTwoTone/blob/main/Aug%2018%20Work/README.md
