# Claude MacBook Connect

This is the persistent MacBook connection path for Claude Code sessions that need to coordinate with the Nexus server running on the Mac mini.

## One-Time Setup On The MacBook

On the Mac mini, run:

```bash
./scripts/print_macbook_claude_setup.sh
```

Paste the printed command block into a terminal on the MacBook once. That creates `~/.nexus/claude_coord.env` with the Nexus coordination URL and API key.

If you want the MacBook to edit this exact Mac mini repo directly instead of a separate checkout, also run this on the Mac mini:

```bash
./scripts/print_macbook_remote_edit_setup.sh
```

Paste that printed block into a terminal on the MacBook once. It:
- creates a dedicated SSH key on the MacBook,
- writes a persistent `Host nexus-mini` entry into `~/.ssh/config`,
- installs the MacBook public key into this Mac mini user's `authorized_keys`,
- verifies that `/Users/kai/nexus` is reachable over SSH.

## Same Command Every Session

If you are using VS Code Remote SSH against this Mac mini, open the remote repo with:

```bash
code --folder-uri "vscode-remote://ssh-remote+nexus-mini/Users/kai/nexus"
```

That puts Claude inside the Mac mini working tree, so edits happen here directly.

From the repo window you want Claude to work in, run:

```bash
./scripts/nexus_claude_bootstrap.sh
```

That command:
- loads the shared coordination URL and API key if present,
- checks the Claude session into the Nexus coordination server when write access is available,
- writes the current session exports to `~/.nexus/current_nexus_session.env`,
- prints the live coordination status from the Nexus server,
- syncs the shared activity log from the Nexus server into `coordination/SHARED_AGENT_LOG.md`,
- shows the tail of `coordination/SHARED_AGENT_LOG.md`.

If `pwd` is not `/Users/kai/nexus`, Claude is not editing the Mac mini directly. In that case it is working in a separate local checkout and only coordinating through Nexus.

## Shared Change Log

After every meaningful edit or change, append a shared entry:

```bash
source ~/.nexus/current_nexus_session.env
./scripts/agent_change_log.sh "$NEXUS_SESSION_ID" "Short summary here" path/to/file1 path/to/file2
```

The shared file is:

```text
coordination/SHARED_AGENT_LOG.md
```

The file is now mirrored from the Nexus server, so devices do not need git just to see the latest handoff notes.

## Live Coordination Commands

While the session is active:

```bash
source ~/.nexus/current_nexus_session.env
./scripts/coord_heartbeat.sh "$NEXUS_SESSION_ID" "Current task summary" path/to/file1 path/to/file2
```

When done:

```bash
source ~/.nexus/current_nexus_session.env
./scripts/coord_checkout.sh "$NEXUS_SESSION_ID" "Completed work summary" path/to/file1 path/to/file2
```

`coord_checkout.sh` now also writes a shared activity entry automatically after a successful checkout.

If `~/.nexus/claude_coord.env` is missing, `./scripts/nexus_claude_bootstrap.sh` still works in read-only mode and can read the public coordination status, but it will not register the session.
