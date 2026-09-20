# Hermes report-only pilot

The optional adapter is implemented and tested offline against Hermes commit `c1488ac947c9bc33fd65ec464548dc9d8edd6122`, Python 3.11.16, discord.py 2.7.1, and aiohttp 3.14.3. The operator's runtime inspection confirms those installed versions and unchanged inspected interfaces. The owner has now supplied successful installation, connected private-command status, and a three-channel repeat report. The basic live path passed; the remaining cases below are still pending.

## Installation and activation are separate

Build an installation zipapp from the repository and an operator-specific, validated local policy:

```bash
python3 scripts/build_pilot_bundle.py \
  --config config.local.toml \
  --output /tmp/liberdus-install-pilot-20260920.pyz \
  --jev-output /tmp/liberdus-jev-20260920.pyz
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

Messages never enter Hermes's general conversation handler. Generic Hermes/cron outbound sending is refused. The adapter does not register tools, slash commands, reactions, threads, or public actions. Its separate optional JEV shadow worker makes direct provider calls only after explicit opt-in; see [JEV setup](jev.md).

Supported private text commands are `!mod status`, `!mod pause`, `!mod resume`, `!mod incident ID`, and `!mod explain ID`. Version 0.3.1 also adds `!mod selftest` after the [owner-run update](selftest.md); it returns nine synthetic check results without changing live moderation state. Commands require both the configured channel and operator ID. DMs, unauthorized private commands, own/bot/webhook messages, and out-of-scope channels receive no reply. Public bot mentions are ordinary evidence, not commands. Command responses are limited to one per second; duplicate command message IDs are retained in a bounded receipt table and never replayed while retained. `explain` returns saved incident metadata; it does not quote source text or call a model.

## Failure and delivery behavior

- One Discord client owns the connection. The adapter shares Hermes's `discord-bot-token` lock and also takes an exclusive profile state-file lock. Startup requires stock Discord disabled in the current and default profile. Never run stock Discord concurrently with this bot or reuse its credential in another profile.
- The configured guild, bot identity, normal text-channel type, bot view/history permissions, private-channel send permission, no Administrator, and everyone-hidden state are checked at readiness. Destination permissions are checked again for each outbound message. This does not audit all other role/member access.
- Queue capacity is 200. Connection loss, overflow, relevant deletion, or an unavailable/malformed edit clears queued evidence and the current detection window. Old incidents are marked for revalidation and pending reports cancelled. Paused moderation stays paused. `!mod status` reports coverage resets. No historical catch-up is claimed.
- Uncached edits are fetched serially, at most four per second, with an eight-second timeout. No-content-change embed updates are ignored. Changed content/attachments are reprocessed through the core's version checks. A relevant deletion conservatively resets the whole window, including queued evidence.
- Reports are validated against the current policy, incident revision, expiry, and destination before an attempt is durably marked `sending`. Success stores the Discord message ID. A timeout, error, or process crash leaves an `uncertain` outcome that is **not automatically retried**. discord.py 2.7.1 sets `enforce_nonce` for the supplied stable nonce, reducing duplicates during SDK-level retries; this is not a claim of permanent exactly-once delivery.
- Reports and command responses disable mentions, embeds, and notifications. Reports contain IDs and jump links, not copied source text. Already-posted reports are historical snapshots; subsequent edits/deletions do not automatically edit those Discord posts. Use the incident command to inspect current status.
- A runtime or Discord worker failure closes the moderation connection and records a fatal state. It cannot enable a conversational fallback. An unreviewed Hermes update or Discord-library version change blocks startup until compatibility is rechecked.

## Live acceptance checklist

Only use the approved test channels and `bot-mod`:

1. Verify current-process runtime status for `liberdus-mod:liberdus_moderator`; bot online alone is insufficient. In `bot-mod`, the operator sends `!mod status` and receives code-only, enforcement-disabled status.
2. Post the same substantive test sentence of at least 20 characters once in each of the three test channels within 120 seconds. Expect one private review report, no public reply, and no AI turn. Include an unmentioned message and a message mentioning another member.
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

## JEV implementation checkpoint — September 20, 2026

The selected existing-incident classifier is implemented in version 0.3.0, default off. The installer uses schema 2 with an explicit disabled classifier table and zero budgets; neither installation nor ordinary fixture replay calls a model. The original discussion is preserved in build-plan section 10.6 and the implementation checkpoint appended in section 10.7.

The operator has deferred JEV because TypeSafe access is pending. Complete the remaining acceptance checks with JEV off. [JEV setup](jev.md) is retained for a later explicit resumption; do not activate shadow mode during the current pilot stage. Shadow results stay local and never alter the private rule reports. The default profile needs no JEV plugin, skill, MCP or model change.

## Live baseline recorded — September 20, 2026

The operator supplied installation success, custom-platform enablement and shared gateway restart (PID `876774` at that time), a successful `!mod status` reply (`Connected: True`, `report_only`, `JEV: off / off`, zero AI attempts, enforcement disabled), and this report:

- Incident: `1ce20048a42442bc92784aaaa9184d69`, revision 1.
- Rule: `cross_channel_repeat`; author: `977263877391794217`.
- Three observed copies and links to `bot-test-1`, `bot-test-2`, and `bot-test-3`, delivered to `bot-mod`.

This is operator-reported live evidence for collection, matching and private delivery. It does not verify every exclusion, permission boundary, edit, or reconnect case. No new profile edit or restart is needed to keep JEV off. The initial 19-character phrase was too short for a repeat report; the subsequent qualifying phrase worked. Short messages should still contribute to the received-message count, so length alone did not explain the earlier zero count.

## Remaining code-only pilot checks

Keep JEV off, AI/enforcement disabled, and the approved scope unchanged. Use the normal text channels (not threads), plain text without attachments, the same author for each pattern, and a distinct phrase per test. Wait for command replies and allow at least one second between commands. `Pending reports: 0` is normal after successful delivery.

| Check | Action in approved test scope | Expected result |
| --- | --- | --- |
| Pause/resume | State changes and a new report after resume are operator-confirmed; see the checkpoint below. | Paused-pattern suppression still needs explicit confirmation that the paused test messages were posted without a report. |
| Same-channel repetition — passed | Operator supplied incident `9b8820010d384c94ac575a2c97a31ce1`, revision 1. | `same_channel_repeat`, four copies and four evidence links in `bot-test-1`, reported privately. |
| Edited evidence — passed | Operator supplied the lookup for incident `0e4677d84316465bb19dbe2c788bb283`, revision 2, after the instructed edit. | `State: withdrawn`; the three saved evidence items and original report remain historical snapshots. |
| Deleted evidence | Delete one of your disposable test messages, then inspect `!mod status`. | A coverage reset with `deleted_message`; current detection evidence is cleared. Already-delivered reports remain historical. |
| Command authorization | Try your own `!mod pause` in `bot-test-1`; have a non-operator who already has access try it in `bot-mod`. Check status from your authorized account. | No state change or privileged command response for either attempt. Do not grant extra access just for this test. |
| DMs/mentions | DM the bot and mention it in an approved test channel. | No general conversation or tool dispatch. A test-channel mention is ordinary rule evidence; it is not an admin command. |
| Restart/duplicate delivery | While paused, restart the shared gateway during a quiet test period, then check status, resume and create a fresh pattern. | Pause persists; old reports are not replayed; connection returns and a new pattern works. Telegram may briefly reconnect. |

Start with pause/resume:

1. In `bot-mod`, send `!mod pause` and wait for the reply to show `paused`.
2. Post `Liberdus paused moderation test.` once in each of the three test channels within 120 seconds. There should be no new report for this phrase.
3. In `bot-mod`, send `!mod resume` and wait for the `report_only` reply. The paused messages are not replayed.
4. Post `Liberdus resumed moderation test.` once in each of the three test channels within 120 seconds. Expect a new private `cross_channel_repeat` report.
5. Send `!mod status`: JEV should still be `off / off`, AI attempts zero, and enforcement disabled. Record the observed result before proceeding to the other checks.

Unconfirmed expectations in this table remain pending; the checkpoints record the operator evidence received so far. After they pass, use a short observation period in the approved test channels to review false positives and report usefulness. Adjust thresholds or explicitly scoped announcement exceptions from those observations. A broader channel pilot and any enforcement implementation are later decisions; TypeSafe access is not a prerequisite for the current work.

## Pause/resume results recorded — September 20, 2026

The supplied `bot-mod` transcript shows `!mod pause` returning `paused`, followed by `!mod resume` returning `report_only`. Both responses are connected, preserve one historical incident, and show zero current messages. A new three-channel report then arrives: incident `715a93e021a74959a6e812bfa9ed256a`, revision 1, rule `cross_channel_repeat`, author `977263877391794217`, three copies.

This confirms command state transitions and detection/reporting after resume. Keeping the old incident while clearing working messages is expected. The unchanged coverage-gap count of 2 is also expected: manual pause/resume does not increment that transport counter. JEV remains off with zero AI attempts and disabled enforcement in both responses.

The supplied excerpt contains no report between pause and resume, but does not include the paused test-channel posts. Confirming that those messages were actually posted with no resulting report would complete the suppression portion of this test. Other checks can proceed independently. Next: send `Liberdus single-channel spam test.` four times in `bot-test-1` within 30 seconds; expect one private `same_channel_repeat` report with four copies.

## Single-channel result recorded — September 20, 2026

The operator supplied incident `9b8820010d384c94ac575a2c97a31ce1`, revision 1, `same_channel_repeat`, author `977263877391794217`, four copies and four links in `bot-test-1`. This is the expected private report for the single-channel test. Mark this detection/reporting case passed from operator evidence. JEV remains deferred/off and enforcement disabled.

Next, test edits using a fresh incident:

1. Post `Liberdus message editing test.` once in each of the three approved test channels, promptly enough to leave time for an edit within 120 seconds of the first post.
2. Wait for the new private report, then edit the `bot-test-3` copy to `This message has been corrected.` while that window is still active.
3. Allow a few seconds for processing. In `bot-mod`, run `!mod incident NEW_ID` with the ID from this new report, not the earlier single-channel report.
4. Expect `State: withdrawn`. The original report stays visible as a historical snapshot. If the result is `expired`, the window elapsed; use a fresh phrase and retry to verify edit handling.

The operator subsequently supplied the expected withdrawn result; see the checkpoint below.

## Edit withdrawal recorded — September 20, 2026

The operator supplied the corrected lookup for incident `0e4677d84316465bb19dbe2c788bb283`: revision 2, `cross_channel_repeat`, `State: withdrawn`, author `977263877391794217`, three saved evidence items, enforcement disabled. This matches the instructed edit test and confirms live withdrawal after a matching copy changes. The three evidence items are the retained triggering snapshot, not a count of currently matching messages. The original report remains visible as history.

Next, test deletion handling:

1. Run `!mod status` in `bot-mod` and note the current `Coverage resets` count.
2. Post `Liberdus deletion handling test.` once in `bot-test-1`. Wait a few seconds, then delete that disposable message from `bot-test-1`.
3. Wait a few seconds and run `!mod status` in `bot-mod`, without sending other test-channel messages in between.
4. Expect `Coverage resets` to increase, `Last: deleted_message`, and `Messages: 0`. Connection should remain true, mode `report_only`, JEV `off / off`, AI attempts zero and enforcement disabled.

A monitored-channel deletion conservatively clears the entire working detection window and cancels pending reports; open incidents become `needs_revalidation`. Historical incidents and delivered reports remain. The already-withdrawn edit incident keeps its withdrawn state. This test does not require another spam report. Record the live status result before marking deletion handling passed.

## Automated self-test available in 0.3.1 — September 20, 2026

The operator asked to reduce manual testing. `!mod selftest` now runs nine fixed, isolated checks and returns one private summary, including deletion-reset and pause/recovery logic. The moderator continues to ignore its own output and other bots/webhooks. Self-tests do not post test messages or change the real pause state, policy or evidence. Follow [self-test deployment and coverage](selftest.md) for the one-time owner-run update; the running 0.3.0 installation has not yet been updated by the developer.

The new command verifies code paths with synthetic inputs and a default test policy. It does not verify real Discord permissions/events or an actual service restart. Keep the remaining live acceptance results explicitly pending until observed. JEV is still deferred/off. No new Discord permissions, bot account or API key is needed.
