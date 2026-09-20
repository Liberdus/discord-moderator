# Hermes report-only pilot

The optional adapter is implemented and tested offline against Hermes commit `c1488ac947c9bc33fd65ec464548dc9d8edd6122`, Python 3.11.16, discord.py 2.7.1, and aiohttp 3.14.3. The operator's runtime inspection confirms those installed versions and unchanged inspected interfaces. The owner has now verified the live report path and several recovery/boundary cases, and received 9/9 from the installed synthetic self-test; the checklist below distinguishes observed and unconfirmed cases. The initial [JEV shadow trial](jev.md#initial-trial-complete) is complete: announcement, promotion and quoted warning all returned successful matching labels. This establishes the three initial examples, not general classifier accuracy. The owner confirmed the 0.3.2 private saved-JEV lookup. The owner subsequently supplied the [0.3.3 formatted incident reply](incident-review.md), confirming the narrow ASCII panel and retained result. Existing scope and disabled enforcement remain unchanged.

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

Supported private text commands are `!mod status`, `!mod pause`, `!mod resume`, `!mod incident ID`, and `!mod explain ID`. Version 0.3.1 also adds `!mod selftest` after the [owner-run update](selftest.md); it returns nine synthetic check results without changing live moderation state. Commands require both the configured channel and operator ID. DMs, unauthorized private commands, own/bot/webhook messages, and out-of-scope channels receive no reply. Public bot mentions are ordinary evidence, not commands. Command responses are limited to one per second; duplicate command message IDs are retained in a bounded receipt table and never replayed while retained. `explain` returns saved incident metadata; it does not quote source text or call a model. Version 0.3.2 adds saved JEV outcome/label, model confidence, age, evaluated revision and current/historical evidence status to both `incident` and `explain`. Version 0.3.3 formats those details in a narrow code block; see [update instructions](incident-review.md).

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

The operator initially deferred JEV while awaiting TypeSafe access, then obtained a key and explicitly authorized a small shadow trial. The operator has since supplied the first successful live provider result; continue the comparison cases in the [JEV trial](jev.md#first-live-shadow-trial) without repeating setup. Shadow results stay local and never alter private rule reports. The default profile needs no JEV plugin, skill, MCP or model change.

## Live baseline recorded — September 20, 2026

The operator supplied installation success, custom-platform enablement and shared gateway restart (PID `876774` at that time), a successful `!mod status` reply (`Connected: True`, `report_only`, `JEV: off / off`, zero AI attempts, enforcement disabled), and this report:

- Incident: `1ce20048a42442bc92784aaaa9184d69`, revision 1.
- Rule: `cross_channel_repeat`; author: `977263877391794217`.
- Three observed copies and links to `bot-test-1`, `bot-test-2`, and `bot-test-3`, delivered to `bot-mod`.

This is operator-reported live evidence for collection, matching and private delivery. It does not verify every exclusion, permission boundary, edit, or reconnect case. No new profile edit or restart is needed to keep JEV off. The initial 19-character phrase was too short for a repeat report; the subsequent qualifying phrase worked. Short messages should still contribute to the received-message count, so length alone did not explain the earlier zero count.

## Remaining code-only pilot checks

The baseline results below were obtained with JEV off and enforcement disabled. Do not repeat passed tests solely to enable the authorized shadow trial. Use the same approved scope, human accounts, normal text channels, plain text without attachments, and a distinct phrase per pattern. Allow at least one second between commands. `Pending reports: 0` is normal after successful delivery.

| Check | Recorded result | Status / remaining scope |
| --- | --- | --- |
| Pause/resume | Command transitions, resumed detection and paused-state persistence across restart are operator-confirmed. | Passed for those behaviors. The supplied excerpts do not explicitly pair paused-pattern posts with absence of a report; retain that narrower evidence gap without restarting the completed test sequence. |
| Same-channel repetition | Incident `9b8820010d384c94ac575a2c97a31ce1`, revision 1, four copies in `bot-test-1`. | Passed: private `same_channel_repeat` report. |
| Edited evidence | Incident `0e4677d84316465bb19dbe2c788bb283`, revision 2, `withdrawn`. | Passed: saved evidence and the old report remain historical. |
| Deleted evidence | Coverage resets increased from 4 to 5, last reason `deleted_message`, current messages 0. | Passed: connected and report-only; four historical incidents retained. |
| Operator command in wrong channel | Operator confirmed `!mod pause` in a test channel did not pause; authorized status remained `report_only`. | Passed. |
| Unauthorized member in `bot-mod` | Operator chose to skip the other-member test for now. | Explicitly deferred; do not mark passed or widen channel access to run it. |
| DM | Operator reported no DM response; authorized status remained unchanged. | Passed for the reported DM test. A dedicated mention/general-dispatch live case is not separately evidenced. |
| Actual gateway restart | After the instructed restart, status remained `paused`, connected, with four incidents; resume produced a fresh cross-channel report. | Passed for pause persistence and fresh detection. No exhaustive live duplicate-delivery test is claimed. |
| `!mod selftest` | Installed command returned 9/9 passed and zero self-test AI calls/public actions. | Synthetic logic coverage only; does not fill unobserved live cases or authenticate JEV. |

The three initial [JEV shadow comparisons](jev.md#initial-trial-complete) are now complete; neither these examples nor the passed code-only cases need repeating. The operator selected the private saved-result lookup; 0.3.2 implements it, with [deployment and live inspection](incident-review.md) as the next step. Continue normal-message and false-positive observation in the existing test channels. Record additional evidence when available; a broader channel pilot and enforcement remain later decisions.

The following dated sections preserve earlier results and the test instructions given at each checkpoint. Their “next test” text is historical; use the current checklist above and JEV runbook for the next task.

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

The operator asked to reduce manual testing. `!mod selftest` now runs nine fixed, isolated checks and returns one private summary, including deletion-reset and pause/recovery logic. The moderator continues to ignore its own output and other bots/webhooks. Self-tests do not post test messages or change the real pause state, policy or evidence. The operator subsequently installed the update and supplied a 9/9 response. [Self-test deployment and coverage](selftest.md) retains the update procedure for older installations; this pilot does not need another update.

The new command verifies code paths with synthetic inputs and a default test policy. It does not verify real Discord permissions/events or an actual service restart. Keep unobserved live cases explicitly pending. The self-test needs no new Discord permissions, bot account or API key and makes no JEV calls even when shadow mode is enabled.

## Later live results and JEV trial decision — September 20, 2026

The operator's 3:07 PM self-test response reported all nine synthetic cases passed, with zero self-test AI calls and public actions. At 3:29 PM, the supplied deletion comparison showed coverage resets 4 → 5 and `Last: deleted_message`, while working messages stayed at zero, four incidents remained retained, and the bot stayed connected/report-only. The operator then confirmed that a test-channel `!mod pause` did not pause the bot and that a DM received no response; the supplied authorized status remained report-only.

At 3:48 PM, after the instructed paused restart, `!mod status` returned `paused`, `Connected: True`, four retained incidents, no pending/uncertain reports, and eight coverage resets with `reconnect` last. `!mod resume` returned `report_only`, followed by fresh incident `dfc2c81d0c584ef6b04c9bb33713f04c`, revision 1, `cross_channel_repeat`, author `977263877391794217`, three test-channel copies. This confirms paused-state persistence across the real restart and resumed detection. These statuses all showed JEV off, zero AI attempts and disabled enforcement. The unauthorized-other-member check was explicitly deferred by the operator.

The operator then obtained a TypeSafe key and authorized the small existing-incident shadow trial. The current [JEV runbook](jev.md) uses a hidden key prompt and $0.05/day / $0.25 total local accounting limits. No profile was changed, key read, provider request made, or gateway restarted while preparing these instructions. At that checkpoint, activation and a successful typed provider result still needed owner-run evidence; the subsequent result is recorded below. The bot continues to exclude bot/webhook posts even though the operator subsequently allowed it to send in test channels; the JEV trial uses human-authored messages.

## First live JEV result recorded — September 20, 2026

The operator supplied `report_only`, `Connected: True`, `JEV: shadow / ready`, disabled enforcement and zero attempts before a fresh cross-channel report. Incident `9a7c69df8eb7447188e7ebfd09d2209b`, revision 1, has three approved test-channel copies. The subsequent local result matches that incident/revision and records `outcome: ok`, model `jev-1.13.0`, `choice: announcement`, confidence 0.98, latency 275 ms, 543 input tokens and 54 output tokens. Accounting records one attempt, worker state `ok`, and a conservative reservation of 2,753 microusd; the token-based estimate is 23 microusd.

Mark the first live JEV connectivity, typed-response validation and result-persistence case passed from operator evidence. The first label matches the announcement comparison. Keep broader semantic accuracy unverified. The earlier zero-attempt status precedes the evaluation; the saved result now records one call. `Coverage: code only` describes incident detection, while JEV remains a separate shadow classifier. The next test is the fresh promotion example in [docs/jev.md](jev.md#first-live-shadow-trial), followed by a quoted warning. No additional restart or configuration change is required for these examples. Recording this result did not read credentials, change the live profile or call TypeSafe.

## Live JEV promotion result recorded — September 20, 2026

The operator supplied the local result for new incident `36b174a7f4014708b6e905422c8989c4`, revision 1: `outcome: ok`, model `jev-1.13.0`, `choice: promotion`, confidence 0.99, latency 318 ms, 541 input tokens and 54 output tokens. The policy and rubric hashes match the first test. The probability map records promotion 1.0 and all other choices 0.0, separate from the confidence value. The retained announcement record is unchanged. Accounting records two attempts, state `ok`, and 5,506 microusd reserved; both returned estimates sum to 46 microusd.

Mark the promotion comparison passed from the supplied local evidence. A separate Discord report transcript was not included for this case, and two matching examples do not establish general accuracy or calibrated confidence. The next case is the quoted warning in [docs/jev.md](jev.md#promotion-result). The active shadow setup and approved test scope need no change. Recording this result did not access credentials, send Discord messages, call TypeSafe or restart the gateway.

## Quoted warning passed; initial JEV trial complete — September 20, 2026

The operator supplied a third local result: incident `b48ee236ff774e0f8efec35caa8ba447`, revision 1, `outcome: ok`, model `jev-1.13.0`, choice `quoted_warning`, confidence 1.0 and latency 375 ms. Usage is 541 input tokens and 55 output tokens; the estimate is 23 microusd. Policy and rubric hashes match the prior cases. The other two successful records remain present. Lifetime accounting records three attempts, worker state `ok`, and 8,259 microusd reserved; the three estimates sum to 69 microusd.

Mark the quoted-warning comparison and initial three-case trial complete from operator evidence. The 1.0 model scores do not establish certainty or calibrated accuracy. No separate third-case Discord report excerpt was supplied, and broader semantic accuracy remains unverified. The supplied mode is still shadow; no runtime or budget change accompanied this documentation checkpoint. See [current JEV next steps](jev.md#initial-trial-complete) for the proposed on-demand saved-result lookup and later labeled review. These are future work; enforcement, automatic report changes and expanded channel scope remain outside the completed trial.

## Saved JEV result lookup implemented — September 20, 2026

The operator requested private saved-result inspection and an explanation of JEV's next role. Version 0.3.2 implements it for `!mod incident` and `!mod explain`, preserving authorization, fixed reports, policy, budgets and provider behavior. The view distinguishes matching current saved evidence from historical evaluations after expiry, edits, pauses, resets or policy changes, without pruning or retrying. Tests pass for the core, real pinned transport interfaces and a code updater that preserves an existing shadow policy. The new updater is staged at `/tmp/liberdus-update-0.3.2-20260920.pyz`; no live profile update, restart or Discord reply was performed by the developer. Follow [docs/incident-review.md](incident-review.md) and inspect an existing trial incident to verify the deployed feature.

## Saved lookup confirmed; narrow reply layout ready — September 20, 2026

The owner supplied a live reply for `!mod incident b48ee236ff774e0f8efec35caa8ba447`: incident revision 2, `needs_revalidation`, three evidence items; saved JEV `quoted_warning`, confidence 1.00, `ok`, model `jev-1.13.0`, evaluated revision 1, age 40m. Evidence was correctly marked historical after closure/window reset, and the reply stated no new AI call. This confirms the deployed lookup and retained evaluation; no separate accounting delta was supplied.

The owner requested better Discord formatting for a portrait phone. Version 0.3.3 gives incident/explain replies a bold heading, a 32-column ASCII code block and full IDs outside the block for copying. Existing metadata and historical warnings remain visible; status replies, automatic reports and moderation behavior are unchanged. The source and `/tmp/liberdus-update-0.3.3-20260920.pyz` are ready. The developer has not deployed this update or posted a Discord preview. Follow the [owner-run update](incident-review.md) and repeat only the existing incident lookup to check the live layout; no new key or paid test is needed.

## Formatted incident reply confirmed live — September 20, 2026

The owner supplied the 0.3.3-format reply for incident `b48ee236ff774e0f8efec35caa8ba447`: separate code-rule and saved-JEV sections, aligned readable labels, wrapped historical reason, full IDs and both footer statements. Incident revision 2 remains `Needs recheck`; the saved evaluation is revision 1, `Quoted warning`, confidence 1.00, `OK`, model `jev-1.13.0`, age 1h and `HISTORICAL`. This matches retained history after the restart. The pasted reply confirms the live formatting step, superseding the pending status above. No repeat update or paid example is needed. The developer recorded this evidence without accessing the live profile or changing runtime behavior.
