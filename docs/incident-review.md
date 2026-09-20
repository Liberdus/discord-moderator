# Saved JEV results in private incident replies

Version **0.3.2** implements the selected next feature: `!mod incident ID` and its `!mod explain ID` alias display the stored JEV evaluation in the authorized private command channel. Source and the owner-run update bundle are ready; deployment and a live reply from 0.3.2 remain pending until the owner supplies the result. The previous three successful provider evaluations do not need repeating.

## What the command shows

The existing incident metadata is followed by the saved purpose, model confidence, provider outcome, pinned model, evaluated revision, result age and evidence status. For example, after updating and looking up the prior quoted-warning incident, the JEV portion will look like this (age and history reason vary):

```text
JEV (saved shadow): quoted_warning
Confidence: 1.00 (model score)
Outcome: ok | Model: jev-1.13.0
Evaluated revision: 1 | Age: 1h ago
Evidence: historical (evidence window expired)
Saved result only; no new AI call.
```

- **Current** means the stored evaluation matches the open, unexpired incident, revision, evidence hash, policy, model/rubric and working message versions under the loaded configuration. It is a check against saved evidence, not a new Discord API probe or fresh model judgment.
- **Historical** preserves a past label when evidence expires, changes or becomes unavailable, a revision changes, moderation pauses, coverage resets, or the policy/model/rubric changes. An update restart begins a fresh coverage window, so the three earlier trial results will normally be historical. The evaluated revision can be older than the incident revision shown above it.
- Missing evaluations, running/uncertain attempts, and failures receive explicit fixed outcomes. Malformed or unsupported stored records produce `saved evaluation unavailable` instead of exposing raw data/errors. Off mode can still display existing history; a never-enabled incident has no saved evaluation.

These lookups do not start a classifier worker, access a key, call TypeSafe or Hermes, retry an evaluation, change classifier counters/budgets, prune evidence, or change reports. The normal live command receipt is still recorded for duplicate prevention. Authorization still requires the configured guild, private command channel and operator identity. The sender suppresses mentions and bounds replies; message content and arbitrary provider text are not copied into the JEV section.

## Update the existing pilot on db2

Run these steps as **`hermes`**. The developer account cannot access the live profile. The fixed updater accepts installed versions 0.3.0 or 0.3.1 and installs 0.3.2. It supports the existing validated code-only or shadow policy. It preserves configuration, `.env`, moderation policy, database and budget counters, and retains the old plugin directory for rollback. It makes no provider call or service restart itself.

1. Disable the custom moderation platform, then restart the shared gateway:

   ```bash
   hermes -p liberdus-mod config set \
     platforms.liberdus_moderator.enabled false
   hermes -p default gateway restart
   ```

   Wait for the restart to finish successfully. Telegram may briefly reconnect. Keep stock `platforms.discord.enabled` false in both profiles. The updater requires both the disabled configuration and the released moderation process lock.

2. Run the staged update:

   ```bash
   python3 /tmp/liberdus-update-0.3.2-20260920.pyz
   ```

   Expect `updated: true`, `version: "0.3.2"`, `classifier_mode: "shadow"` for this pilot, `installed_runtime_import: "passed"`, and `isolated_selftest: "9/9 passed"`. Policy/database/configuration change flags and provider/restart flags should be false. Keep the printed `plugin_backup` path. Do not continue on an update failure; the helper reports its fixed stop reason without credentials.

3. After a successful update, reconnect the custom platform:

   ```bash
   hermes -p liberdus-mod config set \
     platforms.liberdus_moderator.enabled true
   hermes -p default gateway restart
   ```

4. In `bot-mod`, look up the existing quoted-warning result:

   ```text
   !mod incident b48ee236ff774e0f8efec35caa8ba447
   ```

   Expect the saved `quoted_warning` label and model confidence 1.00. Historical evidence is expected after expiry/restart. With no new incidents, `!mod status` should still show three AI attempts, shadow mode and disabled enforcement. If moderation was already paused, the update preserves that pause. Resume only when continuing monitoring.

No new test-channel posts or provider evaluations are needed to verify this display. The older announcement and promotion incident IDs in [the trial records](jev.md#initial-trial-complete) can also be inspected. The command returns no saved evaluation if a case was never selected; it does not send old evidence for another paid judgment.

If the user service bus is unavailable, restore the previously used shell environment before retrying the restart:

```bash
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
```

## What JEV is for and what comes next

The code detects patterns, such as a member repeating a message across three channels. JEV supplies a narrow context label for an already-detected incident: promotion, announcement, quoted warning, other or unclear. A moderator can compare that label with the evidence to distinguish likely promotion from community updates or warnings that quote suspicious language. Neither an announcement label nor a promotion label proves permission or abuse.

After confirming the private lookup works, use it to review a small, varied set of deliberately written rule-triggering examples in the existing test channels. Record the intended label, actual label and disagreements, including ordinary discussion repeated innocently, mixed intent, multilingual examples and adversarial instructions. Ordinary messages that never trigger a rule are not evaluated by this integration. The three completed examples establish connectivity and initial behavior, not general accuracy or confidence calibration.

If the labels prove useful, a later selected change can add clearly marked context to automatic private reports or help moderators prioritize review. Report annotations, suppression, broader scanning, production-channel monitoring, automatic actions and launching a general Hermes agent are not implemented by this release. Human review and deterministic rules continue to control the workflow. Shadow mode still applies the existing attempt/spending limits.

## Validation, build and rollback

- 130 core/setup tests pass on Python 3.11.16 and 3.12.3. This includes 15 saved-result cases covering authorization, read-only/no-provider behavior, unchanged budgets, current/historical evidence, expiration without writes, edit/revision/reset/pause/disable behavior, missing/malformed data, bounded text and duplicate commands.
- 32 integration tests pass with reviewed Hermes commit `c1488ac947c9bc33fd65ec464548dc9d8edd6122`, discord.py 2.7.1, aiohttp 3.14.3 and PyYAML 6.0.3. Network requests are blocked/mocked. These include private reply delivery with no secret access/provider call and the stopped-pilot update preserving a shadow policy. Optional unrelated Hermes plugins log missing test-environment dependencies; the Liberdus suite passes.
- The update checks staged code in an isolated temporary profile, runs the fixed nine-case self-test, and retains the previous code for rollback. No real profile, token, live provider request or gateway was accessed during development. A live 0.3.2 command reply is still pending owner verification.

Rebuild bundles from the repository and its validated local policy if `/tmp` was cleared:

```bash
python3 scripts/build_pilot_bundle.py \
  --config config.local.toml \
  --output /tmp/liberdus-install-pilot-0.3.2-20260920.pyz \
  --update-output /tmp/liberdus-update-0.3.2-20260920.pyz
```

Use the updater for the existing installation; the fresh installer refuses to replace it. The staged updater SHA-256 is `e279e77d3ae5de78d32f16b9526e9b46a1d85beebc5073dbfa52a479272e6f5a` (a rebuild may differ because zip metadata includes timestamps).

For rollback, first disable the custom platform and finish the shared gateway restart. Retain the new plugin directory, then restore the previous `liberdus-moderator` directory from the updater's printed `plugin_backup` to the profile's plugins directory. Restore code only: this release introduces no database migration and did not replace the policy, keys or database. Re-enable and restart after verifying the restored directory. Keep stock Discord disabled. Use the [JEV stop procedure](jev.md#stop-the-trial) separately if the intended change is to return to code-only monitoring.
