# Saved JEV results in private incident replies

The owner confirmed the **0.3.2** lookup live in `bot-mod`: the quoted-warning incident returned its saved label, confidence 1.00, evaluated revision 1 and historical status while the incident was revision 2, `needs_revalidation`. The supplied reply said no new AI call; no separate before/after accounting transcript was supplied.

Version **0.3.3** gives `!mod incident ID` and its `!mod explain ID` alias a narrow, aligned layout. The owner has supplied the new formatted reply, confirming the live incident display. The lookup and formatting step is complete; no repeat update or provider evaluation is needed.

## What the command shows

The reply uses a bold heading and an ASCII code block with a maximum width of 32 characters per line. Long values wrap within the panel; full IDs remain outside it for copying. This single-column layout is intended for a portrait phone screen, with no wide table or side borders. Bold text and fenced code blocks are supported by [Discord's Markdown guide](https://support.discord.com/hc/en-us/articles/210298617-Markdown-Text-101-Chat-Formatting-Bold-Italic-Underline). Actual fit depends on the device and font settings.

Example using the owner's confirmed incident (age and history reason vary):

**Moderation review**

```text
CODE RULE
--------------------------------
Rule      : Cross-channel repeat
State     : Needs recheck
Revision  : 2
Evidence  : 3 messages

JEV - SAVED RESULT
--------------------------------
Label     : Quoted warning
Confidence: 1.00 (model score)
Outcome   : OK
Model     : jev-1.13.0
Eval rev  : 1
Age       : 40m ago
Evidence  : HISTORICAL
Reason    : Incident closed or
            window reset
```

**Incident ID**
`b48ee236ff774e0f8efec35caa8ba447`

**Author ID** `977263877391794217`

*Saved snapshot. Enforcement disabled.*

*Saved result only; no new AI call.*

Friendly display names replace internal underscores: `needs_revalidation` appears as `Needs recheck`, for example. This changes presentation only. Status replies and automatic reports keep their existing format.

- **Current** means the stored evaluation matches the open, unexpired incident, revision, evidence hash, policy, model/rubric and working message versions under the loaded configuration. It is a check against saved evidence, not a new Discord API probe or fresh model judgment.
- **Historical** preserves a past label when evidence expires, changes or becomes unavailable, a revision changes, moderation pauses, coverage resets, or the policy/model/rubric changes. An update restart begins a fresh coverage window, so the three earlier trial results will normally be historical. The evaluated revision can be older than the incident revision shown above it.
- Missing evaluations, running/uncertain attempts, and failures receive explicit fixed outcomes. Malformed or unsupported stored records produce `Saved evaluation unavailable` instead of exposing raw data/errors. Off mode can still display existing history; a never-enabled incident has no saved evaluation.

These lookups do not start a classifier worker, access a key, call TypeSafe or Hermes, retry an evaluation, change classifier counters/budgets, prune evidence, or change reports. The normal live command receipt is still recorded for duplicate prevention. Authorization still requires the configured guild, private command channel and operator identity. The sender suppresses mentions and bounds replies; message content and arbitrary provider text are not copied into the panel.

## Update the existing pilot on db2

**Completed for this pilot:** the owner supplied the formatted reply shown here. These steps are retained for older installations and recovery; do not rerun the fixed updater on the already updated plugin.

Run these steps as **`hermes`**. The developer account cannot access the live profile. The fixed updater accepts installed versions 0.3.0, 0.3.1 or 0.3.2 and installs 0.3.3. It supports the existing validated code-only or shadow policy. It preserves configuration, `.env`, moderation policy, database and budget counters, and retains the old plugin directory for rollback. It makes no provider call or service restart itself.

1. Disable the custom moderation platform, then restart the shared gateway:

   ```bash
   hermes -p liberdus-mod config set \
     platforms.liberdus_moderator.enabled false
   hermes -p default gateway restart
   ```

   Wait for the restart to finish successfully. Telegram may briefly reconnect. Keep stock `platforms.discord.enabled` false in both profiles. The updater requires both the disabled configuration and the released moderation process lock.

2. Run the staged update:

   ```bash
   python3 /tmp/liberdus-update-0.3.3-20260920.pyz
   ```

   Expect `updated: true`, `version: "0.3.3"`, `classifier_mode: "shadow"` for this pilot, `installed_runtime_import: "passed"`, and `isolated_selftest: "9/9 passed"`. Policy/database/configuration change flags and provider/restart flags should be false. Keep the printed `plugin_backup` path. Do not continue on an update failure; the helper reports its fixed stop reason without credentials.

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

   Expect the panel above with `Quoted warning` and model confidence 1.00. Check alignment on the portrait phone screen and confirm the full incident ID is easy to copy. Historical evidence is expected after expiry/restart. With no new incidents, `!mod status` should still show three AI attempts, shadow mode and disabled enforcement. If moderation was already paused, the update preserves that pause. Resume only when continuing monitoring.

No new test-channel posts or provider evaluations are needed to verify this display. The older announcement and promotion incident IDs in [the trial records](jev.md#initial-trial-complete) can also be inspected. No new API key or JEV setup is needed.

If the user service bus is unavailable, restore the previously used shell environment before retrying the restart:

```bash
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
```

## What JEV is for and what comes next

The code detects patterns, such as a member repeating a message across three channels. JEV supplies a narrow context label for an already-detected incident: promotion, announcement, quoted warning, other or unclear. A moderator can compare that label with the evidence to distinguish likely promotion from community updates or warnings that quote suspicious language. Neither an announcement label nor a promotion label proves permission or abuse.

The private lookup and new reply format have been confirmed. The [JEV batch runner](jev-batch.md) now automates the next comparison using ten synthetic incident examples and the real provider, without posting across test channels. Run it once and review the expected/actual labels. Record the intended label, actual label and disagreements, including ordinary discussion repeated innocently, mixed intent, multilingual examples and adversarial instructions. Ordinary messages that never trigger a rule are not evaluated by this integration. The three completed examples establish connectivity and initial behavior, not general accuracy or confidence calibration.

If the labels prove useful, a later selected change can add clearly marked context to automatic private reports or help moderators prioritize review. Report annotations, suppression, broader scanning, production-channel monitoring, automatic actions and launching a general Hermes agent are not implemented by this release. Human review and deterministic rules continue to control the workflow. Shadow mode still applies the existing attempt/spending limits.

## Validation, build and rollback

- 130 core/setup tests pass on Python 3.11.16 and 3.12.3. This includes 15 saved-result cases covering authorization, read-only/no-provider behavior, unchanged budgets, current/historical evidence, expiration without writes, edit/revision/reset/pause/disable behavior, missing/malformed data, bounded text and duplicate commands.
- 32 integration tests pass with reviewed Hermes commit `c1488ac947c9bc33fd65ec464548dc9d8edd6122`, discord.py 2.7.1, aiohttp 3.14.3 and PyYAML 6.0.3. Network requests are blocked/mocked. These include private reply delivery with no secret access/provider call and the stopped-pilot update preserving a shadow policy. Optional unrelated Hermes plugins log missing test-environment dependencies; the Liberdus suite passes.
- Existing rendering checks also verify ASCII panel width, balanced code fences and intact incident IDs. The example is 593 characters, with a maximum panel width of 32; this is a local text preview, not a live Discord screenshot.
- The update checks staged code in an isolated temporary profile, runs the fixed nine-case self-test, and retains the previous code for rollback. No real profile, token, live provider request or gateway was accessed during development. The owner confirmed the 0.3.2 lookup and subsequently supplied the 0.3.3 formatted incident reply. The supplied evidence is pasted reply text, rather than a device screenshot or separate deployment log.

Rebuild bundles from the repository and its validated local policy if `/tmp` was cleared:

```bash
python3 scripts/build_pilot_bundle.py \
  --config config.local.toml \
  --output /tmp/liberdus-install-pilot-0.3.3-20260920.pyz \
  --update-output /tmp/liberdus-update-0.3.3-20260920.pyz
```

Use the updater for the existing installation; the fresh installer refuses to replace it. Both bundles include the reviewed 0.3.3 plugin source. Zip metadata includes timestamps, so a rebuild may have a different archive hash.

For rollback, first disable the custom platform and finish the shared gateway restart. Retain the new plugin directory, then restore the previous `liberdus-moderator` directory from the updater's printed `plugin_backup` to the profile's plugins directory. Restore code only: this release introduces no database migration and did not replace the policy, keys or database. Re-enable and restart after verifying the restored directory. Keep stock Discord disabled. Use the [JEV stop procedure](jev.md#stop-the-trial) separately if the intended change is to return to code-only monitoring.
