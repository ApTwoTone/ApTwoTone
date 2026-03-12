Paste this into a Claude Code session on the MacBook at the start of work:

```text
You are working in the Nexus repo and must coordinate with the Mac mini Nexus server.

Before doing work:
1. Confirm `pwd` is `/Users/kai/nexus`. If not, stop and reconnect through VS Code Remote SSH to `nexus-mini` and open `code --folder-uri "vscode-remote://ssh-remote+nexus-mini/Users/kai/nexus"`, because direct edits must happen on the Mac mini working tree.
2. Run `./scripts/nexus_claude_bootstrap.sh`.
3. Read `coordination/SHARED_AGENT_LOG.md`.
4. If `~/.nexus/current_nexus_session.env` exists, source it before coordination commands.

Required coordination behavior:
- After every meaningful edit or change, run:
  `source ~/.nexus/current_nexus_session.env && ./scripts/agent_change_log.sh "$NEXUS_SESSION_ID" "<summary>" <files...>`
- During work, update live status with:
  `source ~/.nexus/current_nexus_session.env && ./scripts/coord_heartbeat.sh "$NEXUS_SESSION_ID" "<working_on>" <files...>`
- If you need the newest cross-device handoff notes again, run:
  `./scripts/coord_sync_shared_log.sh && tail -n 40 coordination/SHARED_AGENT_LOG.md`
- When finished, run:
  `source ~/.nexus/current_nexus_session.env && ./scripts/coord_checkout.sh "$NEXUS_SESSION_ID" "<completed_work>" <files...>`

Do not skip the shared change log. Read it again after any major task so the MacBook and Mac mini sessions stay aligned.
```
