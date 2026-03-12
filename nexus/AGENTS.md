# Agent Coordination — Live Status

## ACTIVE SESSIONS
| Session | Device | Agent Type | Working On | Files Locked | Started | Last Update |
|---------|--------|------------|------------|--------------|---------|-------------|
| codex-owner-public-access-mirror-v1 | Kai’s Mac mini | codex | Publish a sanitized Nexus access mirror into ApTwoTone/ApTwoTone so restricted Claude sandboxes can read the full code surface | AGENTS.md | 2026-03-12 01:15:00 | 2026-03-12 01:15:00 |
| claude-kai-s-mac-mi-230534 | Kai’s Mac mini | claude_code | Starting session... | (none) | 2026-03-12 06:05:34 | 2026-03-12 06:05:34 |
| claude-kai-s-mac-mi-224059 | Kai’s Mac mini | claude_code | Starting session... | (none) | 2026-03-12 05:40:59 | 2026-03-12 05:41:00 |
| claude-kai-s-mac-mi-213842 | Kai’s Mac mini | claude_code | Starting session... | (none) | 2026-03-12 04:40:28 | 2026-03-12 04:40:28 |
| claude-kai-s-mac-mi-213844 | Kai’s Mac mini | claude_code | Starting session... | (none) | 2026-03-12 04:40:28 | 2026-03-12 04:40:28 |
| nexus-server | Kais-Mac-mini.local | server | FastAPI server running on port 7860 | (none) | 2026-03-12 04:37:40 | 2026-03-12 04:37:40 |
| nexus-server | Kais-Mac-mini.local | server | FastAPI server running on port 7860 | (none) | 2026-03-12 04:07:06 | 2026-03-12 04:07:06 |

## COMPLETED TODAY
| Session | What | Files Changed | Time |
|---------|------|---------------|------|
| codex-owner-push-all-nexus-v1 | Pushed the Nexus backend repo, published the Nexus Network frontend repo, pushed website-side Convex files, and created private runtime/workspace repos so Claude can reach the full code surface plus live runtime context. | AGENTS.md | 2026-03-12 01:12:16 |
| codex-owner-system-audit-v1 | Audited the live Nexus stack across Facebook ads, command/control, key rotation, Facebook scraping, SMS, email ingest, and email marketing, with runtime/process/DB validation to separate working components from stale dashboards and broken integrations. | AGENTS.md | 2026-03-12 00:42:52 |
| codex-owner-fb-priority-v1 | Added a lightweight Facebook/CRM runtime bootstrap so lead alerts can recover even when full app startup is skipped, fixed empty-config service-control handling, and updated the booking playbook with tomorrow's Facebook ads and lead-quality priorities. | AGENTS.md, server.py, core/service_control.py, docs/BOOKING_OUTREACH_PLAYBOOK.md, tests/test_server_lazy_startup.py, tests/test_service_control.py, tests/test_process_manager.py | 2026-03-12 00:06:30 |
| codex-owner-gv-hide-v1 | Made Google Voice automation more aggressively hidden by pushing Chrome far off-screen and adding a background rehider loop, then restarted the SMS control daemon. | AGENTS.md, integrations/google_voice.py, tests/test_google_voice.py | 2026-03-11 23:46:36 |
| codex-owner-safety-centralize-v1 | Added centralized pause/resume controls for Google Voice automation and the Facebook lead hunter, fixed ProcessManager service start/disable handling, and exposed the controls through both a CLI and the live API. | AGENTS.md, core/service_control.py, scripts/process_manager.py, server.py, scripts/service_control.py, tests/test_service_control.py, tests/test_process_manager.py | 2026-03-11 23:32:24 |
| codex-owner-fb-agent-v1 | Tightened Facebook lead detection for event-host requests, repaired old Facebook lead metadata, enabled the live scraper again, and kept Google Voice browser automation in background mode. | AGENTS.md, scripts/facebook_group_scraper.py, integrations/google_voice.py, scrapers/fb_vendor_scraper/browser_utils.py, tests/test_facebook_group_scraper.py, tests/test_google_voice.py | 2026-03-11 22:38:11 |
| codex-owner-outreach-refresh-v1 | Tightened aggressive-outreach contact-name validation, removed sticky fallback noise during refresh, reran noisy-target cleanup, and cleared remaining placeholder email/name artifacts from the stored outreach dataset. | AGENTS.md, core/aggressive_outreach.py, core/outreach_targeting.py, tests/test_aggressive_outreach.py, tests/test_outreach_targeting.py | 2026-03-11 20:53:33 |
| codex-owner-unify-outreach-v1 | Integrated aggressive outreach into the main Nexus app, restored CRM lead pages with DB fallbacks, tightened bounce suppression, and relabeled Entrepreneur Lab revenue as modeled rather than verified payout. | AGENTS.md, server.py, core/email_queue_manager.py, core/vendor_enrichment.py, core/aggressive_outreach.py, core/division_three.py, ../nexus-network/src/app/page.tsx, ../nexus-network/src/components/sidebar.tsx, ../nexus-network/src/components/aggressive-outreach.tsx, ../nexus-network/src/components/leads-pipeline.tsx, ../nexus-network/src/components/entrepreneur-lab.tsx, ../nexus-network/src/lib/api.ts | 2026-03-11 20:28:31 |
| codex-owner-deep-outreach-v1 | Deepened aggressive outreach research with stricter contact-name validation, richer sales/offer intelligence, visible research-stack UI details, and refreshed live targets with safer greetings. | AGENTS.md, core/aggressive_outreach.py, core/outreach_targeting.py, static/aggressive-outreach/index.html, static/aggressive-outreach/js/aggressive-app.js, static/aggressive-outreach/css/aggressive.css, tests/test_aggressive_outreach.py, tests/test_outreach_targeting.py | 2026-03-11 19:06:05 |
| codex-owner-outreach-pipeline-v1 | Minimized Google Voice browser disruption, added the aggressive but compliant outreach pipeline with live API/UI, seeded 20 researched leads, and verified dashboard queueing end to end. | integrations/google_voice.py, core/aggressive_outreach.py, server.py, static/aggressive-outreach/index.html, static/aggressive-outreach/js/aggressive-app.js, static/aggressive-outreach/css/aggressive.css, tests/test_aggressive_outreach.py, AGENTS.md | 2026-03-11 18:43:18 |
| codex-owner-outreach-v1 | Refocused Nexus outreach on fast-booking SFV/LA leads with booking-first email selection, updated follow-up copy, a generated call-first lead pack, and tomorrow's booking-priority follow-up queue. | core/cold_email.py, core/email_sequences.py, core/email_queue_manager.py, core/outreach_targeting.py, scripts/send_outreach_batch.py, scripts/generate_booking_hunt.py, docs/BOOKING_OUTREACH_PLAYBOOK.md, tests/test_outreach_targeting.py, output/booking_hunt/sfv_la_call_list_latest.csv, output/booking_hunt/sfv_la_call_list_latest.md | 2026-03-11 17:54:08 |
| codex-owner-convo-voice-v1 | Researched Google Voice limits and prepared Nexus for Twilio conversational voice transport | core/voice_control.py, scripts/voice_control_server.py, core/sms_control.py, tests/test_voice_control.py, docs/VOICE_AI_SETUP.md | 2026-03-11 16:56:55 |
| codex-owner-gv-test-v1 | Validated Google Voice alert-call path and placed a live owner test call | core/sms_control.py, integrations/google_voice.py, scripts/sms_control_daemon.py | 2026-03-11 16:44:03 |
| codex-owner-ssh-debug-v1 | Verified Mac mini accepts SSH public-key auth; issue is on MacBook connection path | - | 2026-03-11 16:31:13 |
| codex-owner-ssh-output-v1 | Provided MacBook remote-edit setup command block output | - | 2026-03-11 16:25:28 |
| codex-owner-remote-edit-v1 | Enabled MacBook Remote SSH direct editing into the Mac mini Nexus repo | scripts/print_macbook_remote_edit_setup.sh, docs/CLAUDE_MACBOOK_CONNECT.md, docs/CLAUDE_MACBOOK_SESSION_PROMPT.md | 2026-03-11 16:20:30 |
| codex-owner-multiagent-v1 | Added server-backed real-time coordination log and bootstrap sync | server.py, scripts/agent_change_log.sh, scripts/coord_checkout.sh, scripts/coord_sync_shared_log.sh, scripts/nexus_claude_bootstrap.sh, scripts/nexus_context_snapshot.py, coordination/SHARED_AGENT_LOG.md, docs/CLAUDE_MACBOOK_CONNECT.md, docs/CLAUDE_MACBOOK_SESSION_PROMPT.md | 2026-03-11 16:15:21 |
| claude-bootstrap-realtime | Realtime bootstrap validation complete | - | 2026-03-11 16:15:16 |
| codex-owner-claude-connect-v1 | Built persistent Claude MacBook Nexus bootstrap and shared change log workflow | scripts/nexus_claude_env.sh, scripts/nexus_claude_bootstrap.sh, scripts/print_macbook_claude_setup.sh, scripts/agent_change_log.sh, scripts/coord_checkout.sh, coordination/SHARED_AGENT_LOG.md, docs/CLAUDE_MACBOOK_CONNECT.md, docs/CLAUDE_MACBOOK_SESSION_PROMPT.md | 2026-03-11 16:07:15 |
| claude-bootstrap-test | Bootstrap validation complete | - | 2026-03-11 16:05:59 |
| codex-owner-phone-calling-v2 | Implemented Google Voice alert-call fallback for owner phone control | core/sms_control.py, integrations/google_voice.py, scripts/sms_control_daemon.py, tests/test_sms_control.py | 2026-03-11 15:58:57 |
| codex-owner-calling-coord-v1 | Restored Nexus server reachability via launchd-managed uvicorn on 7860 | server.py | 2026-03-11 15:35:43 |

## FILE OWNERSHIP (DO NOT TOUCH)
Files currently being modified by another agent — hands off:
- (none)

## RULES
1. Before modifying ANY file, check this doc. If another agent owns it, skip.
2. When starting work: add your row to ACTIVE SESSIONS.
3. When done: move to COMPLETED TODAY, release file ownership.
4. Read this file at the START of every conversation and after every major task.
5. Run `scripts/coord_context.sh <session_id>` at session start to load live DB + coordination context.
