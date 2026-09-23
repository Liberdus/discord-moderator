# Recovery for the local core

Current runtime behavior: 0.8.0 adds [bounded message catch-up](catch-up.md)
after disconnects and unexpected restarts. The core backup/restore steps below
remain separate from that feature; older pilot limitations describe the original
release.

This guide covers SQLite backup and restore. The optional Hermes adapter has a disabled installer; use [live-pilot.md](live-pilot.md) for its stop/restart procedure and delivery recovery boundaries. Actual installation/live acceptance are still pending.

## What to preserve

- A known repository commit and Python 3.11+ environment.
- The active non-secret configuration, including policy version and channel/operator IDs.
- A consistent SQLite snapshot containing evidence versions, incidents, pause state, and pending report payloads.
- Configuration and database file permissions. Hermes profile credentials require their own protected backup and recovery process.

Databases, local configuration, environments, and backup directories are ignored by Git. Do not commit moderation evidence or secrets. A copied working tree alone does not preserve live state.

## Create a consistent snapshot

Use SQLite's backup API instead of copying an open database and omitting its write-ahead-log files. Run this from the repository root after replacing the source path with the database actually used by the command/service:

```bash
python3 - <<'PY'
from pathlib import Path
import os
import sqlite3

os.umask(0o077)
source = Path('state/moderation.sqlite3').resolve()
target = Path('backups/moderation-snapshot.sqlite3').resolve()
if not source.is_file():
    raise SystemExit(f'Database does not exist: {source}')
if target.exists():
    raise SystemExit(f'Choose a new snapshot name: {target}')
target.parent.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as live:
    with sqlite3.connect(target) as snapshot:
        live.backup(snapshot)
        result = snapshot.execute('PRAGMA integrity_check').fetchone()[0]
        if result != 'ok':
            raise SystemExit(f'Snapshot integrity check failed: {result}')
print(target)
PY
```

Store snapshots separately from the VPS using an authorized private backup destination. Match their retention to the evidence policy; database retention cleanup does not erase older backup copies.

## Verify and restore

1. Stop all processes writing the selected database. The fixture CLI is short-lived; if the optional adapter is active, disable its custom platform and restart the shared gateway first, then confirm disconnection.
2. Preserve the current state before replacing anything. Choose a fresh restore path rather than overwriting the original database or its journal files.
3. Copy the verified snapshot to that fresh path with restrictive permissions. Run `PRAGMA integrity_check` on the copy and require `ok`.
4. Use the same code revision and configuration initially. Run `validate`, then inspect `incidents` and `reports` using `--database` with the restored path. These inspection commands do not activate a new engine configuration. Keep `storage.database_path` unchanged inside the configuration and use the path override: changing any configuration field changes its policy hash when processing or commands next start, clearing active coverage while retaining incident history.
5. Exercise `simulate` and the automated tests before resuming message processing. Simulation must use its independent in-memory store.
6. Recheck mode, pause state, policy version, retention, and scope. Offline inspection leaves pending payloads untouched. Starting the live adapter cancels old-window pending reports and marks interrupted sends uncertain; it starts fresh coverage.

Do not replay a large historical backlog against a live configuration to recreate state; replay is an offline fixture interface. The pilot does not backfill missed history: it resets the active detection window after interruption and exposes the coverage gap. Never reset uncertain delivery rows for automatic retry. Expired evidence or capacity-rejected events cannot be inferred to have been reviewed.

## Restart boundaries

Opening the same database with the same configuration preserves evidence, repeat windows, incidents, and deduplication state within configured retention/capacity. Clock and expiry checks still apply. An explicit pause or a message/incident/evidence capacity failure clears the active message window, invalidates open incidents, and cancels pending reports while preserving incident history. Resuming starts a fresh window because edits may have been missed during the pause. Closing transitions can use one terminal audit revision beyond the configured evidence-version limit. Starting with an empty database loses prior incident context and deduplication history; record that as a recovery gap rather than claiming continuous coverage.

The live adapter adds a stricter restart boundary: it invalidates active evidence even when the core policy is unchanged. Private delivery has a durable attempt ledger, saved Discord message IDs, stable nonces, mention suppression, and scope/revision checks; uncertain outcomes are not automatically retried. AI, public replies, and enforcement remain absent. Future enforcement requires its own attempt ledger and reconciliation design.
