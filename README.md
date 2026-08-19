# Nexus Access Mirror

This public repo is a sanitized mirror of the Nexus code surface so restricted Claude sandboxes that can only access `ApTwoTone/ApTwoTone` can still open and edit the project.

## Included

- `nexus/` — backend automation, API, scrapers, SMS control, lead pipeline
- `nexus-network/` — Next.js dashboard and Tauri desktop shell
- `zoar-website/` — website code
- `nexus-workspace.code-workspace` — VS Code multi-root workspace entrypoint
- `Aug 18 Work/` — 2026-08-18 Fable 5 / Bunny landing status (phone tracker; no live data)

## Intentionally Not Included

- Live runtime databases
- Browser profiles
- Local caches and build artifacts
- Private coordination/runtime secrets
- Lead export files under `nexus/output/`

For live runtime context, use the private runtime snapshot repo outside this public mirror.
