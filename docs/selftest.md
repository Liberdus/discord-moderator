# Private moderation self-test

**Current release:** the 0.3.1 installation steps below are historical. The running pilot already passed its self-test. For the selected saved-JEV-result feature, use the [0.3.2 update instructions](incident-review.md); the same nine-case self-test remains available.

Version 0.3.1 adds `!mod selftest` for an authorized operator in `bot-mod`.
The command runs nine fixed checks through the moderation engine and command
handlers, then returns one private pass/fail summary. It accepts no arguments.
The normal operator/channel checks, duplicate-command suppression, command rate
limit and mention suppression still apply. It also works while moderation is paused.

Each check gets a separate in-memory database, synthetic IDs/text, a controlled
clock, and the default test policy. No live configuration or evidence is used.
It does not pause/resume the real bot, create real incidents, restart a service,
read credentials, invoke Hermes or JEV, or post/delete test-channel messages.
The ordinary command receipt is retained in the live database; the test results
are sent only as the command response.

## Coverage

| Automated check | What it exercises |
| --- | --- |
| Cross-channel repetition | Three matching channels create one incident and pending report. |
| Same-channel repetition | Four matching copies create a same-channel incident. |
| Edit withdrawal | Changing a copy withdraws the incident at revision 2 and cancels the pending report. |
| Deletion reset logic | A synthetic deletion gap clears working evidence and invalidates open incidents. |
| Pause/resume logic | Paused fixtures are ignored; a fresh resumed pattern is detected. |
| Command authorization | Wrong guild/channel/user is refused; an authorized command works and duplicates are ignored. |
| Bot and scope exclusions | Own output, other bots, webhooks and wrong scope do not enter evidence. |
| Duplicate delivery prevention | Replayed fixtures do not generate a second report delivery. |
| Simulated session recovery | Recreating the session retains pause state and marks an interrupted delivery uncertain. |

The response labels the results as synthetic. It does **not** verify the current
live policy, Discord permissions/intents, real message/edit/delete event delivery,
DM handling, network reconnection, disk durability, or an actual gateway restart.
Existing adapter integration tests cover additional paths with mocked networking.
Keep any unobserved live acceptance cases pending in [the pilot record](live-pilot.md).
Successful self-tests do not change that record to live-passed.

The moderator account cannot test ordinary member-message detection by posting to
itself: its output and other bot/webhook messages are intentionally excluded. The
current test-channel permissions also deny it sending. Preserve these exclusions;
no extra bot, member credential or permission change is needed for self-tests.

## Update the existing 0.3.0 pilot

The operator runs these commands as the existing `hermes` user on db2. The staged
update is `/tmp/liberdus-update-selftest-20260920.pyz`. The running installation
does not gain this command until the code update and final restart succeed.

1. Disable the custom platform, then wait for the shared gateway restart to finish:

   ```bash
   hermes -p liberdus-mod config set \
     platforms.liberdus_moderator.enabled false
   hermes -p default gateway restart
   ```

2. Apply the stopped-plugin update:

   ```bash
   python3 /tmp/liberdus-update-selftest-20260920.pyz
   ```

   Expect `updated: true`, version `0.3.1`, `installed_runtime_import: passed`,
   and `isolated_selftest: 9/9 passed`. The output records the protected previous
   plugin directory. If the update stops, leave the platform disabled and review
   the result before continuing.

3. Enable the custom platform again and wait for the restart to finish:

   ```bash
   hermes -p liberdus-mod config set \
     platforms.liberdus_moderator.enabled true
   hermes -p default gateway restart
   ```

4. In `bot-mod`, send `!mod status`, then `!mod selftest` after the status reply.
   Expect connected status, JEV `off / off`, zero AI attempts and disabled
   enforcement, then a self-test summary beginning `Liberdus self-test: 9/9 passed`.
   If the bot was paused before the update, it stays paused; the self-test still works.

Both shared gateway restarts may briefly reconnect Telegram. They start fresh
moderation coverage windows and cancel pending old-window reports; retained
incident history remains. The updater itself preserves YAML, policy, credentials
and SQLite files byte-for-byte. It refuses an enabled or still-running pilot,
checks the pinned Hermes/Discord runtime, validates imports and the self-test in
an empty temporary profile with networking blocked, and keeps the old code for
rollback. It does not restart anything or enable either Discord platform. JEV
must already be off, and remains off.

If the user-service bus environment is missing in a new terminal, restore it
before running the gateway commands:

```bash
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
```

For rollback, first disable this platform and restart as in step 1. With the
moderation process stopped, preserve the new plugin directory under another name
and restore only the previous plugin directory printed by the updater to
`~/.hermes/profiles/liberdus-mod/plugins/liberdus-moderator`. Do not replace the
policy, credentials, SQLite state, or unrelated configuration from a backup.

## Local checks and packaging

The same smoke checks can run without a Discord account:

```bash
python3 -m liberdus_moderator.selftest
```

This emits JSON and exits nonzero if any check fails. Fixed failure labels are
reported without exception details; checks remain active with optimized Python.

To build the owner-run bundles from the repository's validated local policy:

```bash
python3 scripts/build_pilot_bundle.py \
  --config config.local.toml \
  --output /tmp/liberdus-install-pilot-0.3.2-20260920.pyz \
  --update-output /tmp/liberdus-update-0.3.2-20260920.pyz
```

The fresh installer still refuses to overwrite an existing installation. The
current updater handles the reviewed 0.3.0/0.3.1 to 0.3.2 code transition and preserves an existing off or shadow policy. The original 0.3.1 updater described above remains a historical artifact.
