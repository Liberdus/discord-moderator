# Hermes report-only pilot

The optional adapter is implemented and tested offline against Hermes commit `c1488ac947c9bc33fd65ec464548dc9d8edd6122`, Python 3.11.16, discord.py 2.7.1, and aiohttp 3.14.3. The operator's runtime inspection confirms those installed versions and unchanged inspected interfaces. Live Discord message acceptance tests remain pending.

## Installation and activation are separate

Build an installation zipapp from the repository and an operator-specific, validated local policy:

```bash
python3 scripts/build_pilot_bundle.py \
  --config config.local.toml \
  --output /tmp/liberdus-install-pilot-20260920.pyz
```

The `hermes` account owner runs:

```bash
python3 /tmp/liberdus-install-pilot-20260920.pyz
```

This re-executes using the verified Hermes virtualenv. It checks the version, imports the staged plugin through Hermes's loader under a temporary empty profile with networking blocked, backs up the moderation profile's YAML, then installs:

- `~/.hermes/profiles/liberdus-mod/plugins/liberdus-moderator/` — a profile-local plugin including its own copy of the core; no pip installation or global import-path mutation.
- `~/.hermes/profiles/liberdus-mod/moderation.toml` — the supplied policy, containing IDs and settings but no token.
- `plugins.enabled` includes `liberdus-moderator`, while `platforms.liberdus_moderator.enabled` is **false**. Both stock Discord configurations must already be false. Registration alone cannot auto-enable the platform.

The installer does not read the token file, change default-profile configuration, restart a service, or connect Discord. Its JSON output includes the backup path. An existing installation is not overwritten. A failed publish removes the new policy/plugin files and preserves the old configuration; the protected backup remains.

Before activation, confirm the installation output and that **Message Content Intent** is enabled and saved in the Discord Developer Portal. The adapter requests guilds, guild messages, and message content only. It does not request DMs or the member-list intent; this pilot uses numeric operator IDs and message author metadata. Do not enable the stock Hermes Discord platform.

Once the intended pilot activation has been authorized and those checks pass, the owner can enable only the custom platform and restart the shared gateway:

```bash
hermes -p liberdus-mod config set platforms.liberdus_moderator.enabled true
hermes -p default gateway restart
```

The shared restart can briefly reconnect Telegram. If the user service bus environment is missing, use the previously established `XDG_RUNTIME_DIR` and `DBUS_SESSION_BUS_ADDRESS` values for the `hermes` user. No separate gateway process is needed.

## Flow and commands

```text
Discord test-channel message
            |
      Scope / bot filter
            |
      Bounded event queue
            |
      Rules + SQLite state
            |
      Current report revision
            |
      Fixed text in bot-mod

bot-mod: !mod status
            |
      Operator-ID check
            |
      Local command handler
            |
      Fixed private response
```

Messages never enter Hermes's general conversation handler. Generic Hermes/cron outbound sending is refused. The adapter does not register tools, model calls, slash commands, reactions, threads, or public actions.

Supported private text commands are `!mod status`, `!mod pause`, `!mod resume`, `!mod incident ID`, and `!mod explain ID`. Commands require both the configured channel and operator ID. DMs, unauthorized private commands, own/bot/webhook messages, and out-of-scope channels receive no reply. Public bot mentions are ordinary evidence, not commands. Command responses are limited to one per second; duplicate command message IDs are retained in a bounded receipt table and never replayed while retained. `explain` returns saved incident metadata; it does not quote source text or call a model.

## Failure and delivery behavior

- One Discord client owns the connection. The adapter shares Hermes's `discord-bot-token` lock and also takes an exclusive profile state-file lock. Startup requires stock Discord disabled in the current and default profile. Never run stock Discord concurrently with this bot or reuse its credential in another profile.
- The configured guild, bot identity, normal text-channel type, bot view/history permissions, private-channel send permission, no Administrator, and everyone-hidden state are checked at readiness. Destination permissions are checked again for each outbound message. This does not audit all other role/member access.
- Queue capacity is 200. Connection loss, overflow, relevant deletion, or an unavailable/malformed edit clears queued evidence and the current detection window. Old incidents are marked for revalidation and pending reports cancelled. Paused moderation stays paused. `!mod status` reports coverage resets. No historical catch-up is claimed.
- Uncached edits are fetched serially, at most four per second, with an eight-second timeout. No-content-change embed updates are ignored. Changed content/attachments are reprocessed through the core's version checks. A relevant deletion conservatively resets the whole window, including queued evidence.
- Reports are validated against the current policy, incident revision, expiry, and destination before an attempt is durably marked `sending`. Success stores the Discord message ID. A timeout, error, or process crash leaves an `uncertain` outcome that is **not automatically retried**. discord.py 2.7.1 sets `enforce_nonce` for the supplied stable nonce, reducing duplicates during SDK-level retries; this is not a claim of permanent exactly-once delivery.
- Reports and command responses disable mentions, embeds, and notifications. Reports contain IDs and jump links, not copied source text. Already-posted reports are historical snapshots; subsequent edits/deletions do not automatically edit those Discord posts. Use the incident command to inspect current status.
- A runtime or worker failure closes the moderation connection and records a fatal state. It cannot enable a conversational fallback. An unreviewed Hermes update or Discord-library version change blocks startup until compatibility is rechecked.

## Live acceptance checklist

Only use the approved test channels and `bot-mod`:

1. Verify current-process runtime status for `liberdus-mod:liberdus_moderator`; bot online alone is insufficient. In `bot-mod`, the operator sends `!mod status` and receives code-only, enforcement-disabled status.
2. Post the same substantive test sentence once in each of the three test channels within 120 seconds. Expect one private review report, no public reply, and no AI turn. Include an unmentioned message and a message mentioning another member.
3. Edit evidence, including an older uncached message, and inspect the incident status. Exercise an unavailable/deleted message and confirm a visible coverage reset. Already-delivered reports remain snapshots.
4. Check a public bot mention, a DM, an unauthorized user's private command, duplicate events/reconnects, and a deliberate pause/resume. None may create AI/tool access or duplicate private reports.
5. Verify model usage remains zero for these paths and record observed results. Offline tests do not close this live acceptance step.

## Stop and recovery

Disable only this platform and restart the shared gateway:

```bash
hermes -p liberdus-mod config set platforms.liberdus_moderator.enabled false
hermes -p default gateway restart
```

Confirm its current-process status is disconnected before copying/restoring SQLite. Keep stock Discord disabled. The state database and YAML backup paths are profile-local. Follow [SQLite backup instructions](recovery.md), including WAL-safe backup; do not copy only a live main database file. Restart begins a fresh coverage window, cancels pending old-window reports, and marks interrupted sends uncertain. Never change uncertain deliveries back to pending without checking Discord and designing an explicit reconciliation step.

Plugin updates are manual and version-reviewed. Do not restore a saved configuration wholesale after unrelated configuration changes; use the protected backup to inspect/revert only the pilot changes. Telegram belongs to the default profile and should remain configured as before.

## JEV discussion checkpoint — September 20, 2026

The operator paused installation to discuss an optional JEV classifier. [Build plan section 10.6](BUILD_PLAN.md#106-optional-jev-classifier--discussion-proposal-september-20-2026) records the researched proposal and default-off/shadow/report-only design. The existing installer remains code-only and has no JEV client, key, or runtime flag. Resume deployment after the discussion resolves the intended scope.
