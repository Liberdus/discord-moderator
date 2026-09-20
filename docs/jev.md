# Optional JEV shadow evaluation

**Current status — September 20, 2026: initial three-case trial complete.** All three operator-supplied local records have `outcome: ok` and their intended labels: announcement, promotion and quoted warning. Accounting records three attempts; the latest result took 375 ms. This verifies these examples and the live integration, not general accuracy or calibrated confidence. The supplied mode remains `shadow`; documentation updates did not change the live profile. Completed setup/tests need no repetition. Keep the prescribed trial limits and disabled enforcement unchanged. See the [completed trial and proposed next work](#initial-trial-complete).

Implemented in 0.3.0; **disabled in the installation bundle**. The chosen scope is existing code-rule incidents only. This does not scan every message, create new incidents, suppress rule reports, annotate Discord reports, invoke Hermes's model/tools, or perform moderation actions. `report_only` classifier annotations remain future work and are rejected by configuration today.

## Integration review before implementation — September 20, 2026

| Option reviewed | What it provides | Decision for `liberdus-mod` |
| --- | --- | --- |
| [Official TypeSafe skill](https://docs.typesafe.ai/agent-skill), [source](https://github.com/typesafe-ai/skills) | API development guidance, distributed as an agent skill / Claude Code plugin | Useful reference for developing the repository. Installing it in the moderation profile would not attach it to our platform worker. |
| [itsmostafa/typesafe-mcp](https://github.com/itsmostafa/typesafe-mcp), [HTTP source](https://github.com/itsmostafa/typesafe-mcp/blob/main/cmd/evaluate/client.go) | Community MCP evaluation tool; automatic retries; configurable provider/model | Useful for separate interactive experiments. Our worker needs incident bindings, fixed budgets, and no automatic retry. Do not install it for this pilot. |
| [y0usaf/typesafe-mcp](https://github.com/y0usaf/typesafe-mcp), [source](https://github.com/y0usaf/typesafe-mcp/blob/main/index.js) | Community MCP tool exposing arbitrary state/questions/model | Also an agent tool, with no built-in connection to our moderation incident lifecycle. Do not install it for this pilot. |
| [Official HTTP API](https://docs.typesafe.ai/api) | Typed evaluation at the TypeSafe endpoint | Selected: a small direct async client using the aiohttp already installed with Hermes. No new SDK, MCP process, or conversational agent turn. |

The plugin directory search returned no JEV/TypeSafe entry. Official docs and TypeSafe's public repositories exposed a development skill and SDKs; no official Hermes runtime plugin or official MCP was identified. This is a scoped research finding, not a claim that no other integration exists. The reviewed Hermes commit supports profile-scoped MCP tools, but this platform intentionally does not enter its agent tool loop.

## Configuration and behavior

Schema 1 remains code-only and retains its existing policy hashes. Schema 2 requires an explicit `[classifier]` table. Unknown options fail validation. An enabled worker requires **both** `ai_enabled = true` and `classifier.mode = "shadow"`, positive call/spending limits, and normal report-only moderation. `actions_enabled` must always remain false. The setup helper performs the migration and makes a protected backup. Policy changes start a fresh coverage window on restart.

See [jev-shadow.toml](../examples/jev-shadow.toml) for a synthetic policy example. The packaged real pilot starts with `ai_enabled = false`, `classifier.mode = "off"`, and zero spending limits. Off mode creates no JEV client and performs no TypeSafe key lookup or provider request. The fixture CLI remains offline even with a shadow configuration; only the live adapter starts the worker.

```text
Test-channel message
        |
   Scope + code rules
        |
   Existing incident
      /       \
     v         v
Fixed report   JEV flag?
to bot-mod        |
            Bounded queue
                  |
            TypeSafe API
                  |
            Local shadow
            result only
```

- One worker, at most one provider attempt per incident. New copies/revisions, errors, timeouts, or restarts do not automatically buy another evaluation for that incident. Results are bound to the incident revision, full evidence hash, policy hash, model, and rubric hash.
- The rubric classifies apparent purpose as promotion, announcement, quoted warning, other, or unclear. An announcement label does **not** establish authorization; a promotion label does **not** prove abuse. Probabilities/confidence are model outputs, not validated accuracy or permission to act.
- Input contains distinct incident texts and numeric counts. No neighboring conversation, private moderator messages, usernames, role lists, or metadata IDs are included. Embedded Discord IDs/mentions are redacted. Free-form message text can still contain identifying or sensitive material; it is sent to TypeSafe when shadow mode is enabled. Redaction is not a guarantee of anonymity.
- The input cap is 12,000 UTF-8 JSON bytes and 32 evidence messages. Oversized incidents are skipped without truncation. The queue holds 20 incident IDs. Timeout is three seconds, concurrency one, and the minimum interval six seconds. Admission/rate/input-limit drops are visible; this is sampled incident evaluation, not guaranteed complete coverage.
- Scope, pause, connection, current evidence and policy are checked before provider I/O and again on return. Edits, deletes, coverage resets, expiry, or changed policy make a late judgment unusable. A policy file change blocks new calls before restart; commands show the loaded mode until restart.
- HTTP uses one POST to `https://api.typesafe.ai/v1/systemone`, pinned model `jev-1.13.0`, bounded responses, no redirects or automatic retries. Exceptions/provider response bodies never become Discord text or persisted diagnostics. Failures leave deterministic reporting available.
- SQLite stores typed successful results, token usage, estimated cost, latency and fixed outcome codes. Stale judgments are discarded while their valid usage is retained. Attempts interrupted by shutdown/crash are uncertain and never replayed. Results follow incident retention/capacity; lifetime budget counters survive retention pruning. Do not delete the database to reset a spending cap.
- `!mod status` shows JEV mode/state and cumulative AI **attempts**, including reserved/uncertain attempts. Code-rule decisions still correctly report zero synchronous AI calls: evaluation is separate. Shadow labels are inspected locally, not posted to Discord.

## Account, key, and cost

1. Open the [TypeSafe console](https://console.typesafe.ai/) and sign in/create an account. If your account is waitlisted or lacks API access, complete the displayed access process; the bot can run with JEV off meanwhile.
2. Open its API-key area (the console's [keys route](https://console.typesafe.ai/keys)), create a key for this pilot, and copy it. The official [quick start](https://docs.typesafe.ai/introduction/quickstart) identifies the dashboard as the key source. Authenticated screen labels/access were not inspected here.
3. Check your account's billing/credits and any available provider spending control. The direct integration uses `TYPESAFE_API_KEY`, not your Hermes model key or Discord token. Keep it in the `liberdus-mod` profile only.

The published [JEV model price](https://docs.typesafe.ai/models) is **$0.042 per million input tokens**, with output tokens free, as checked September 20, 2026. No free allowance is assumed. The local estimator is pinned to that price; provider pricing/account conditions can change. Review them before enabling.

The helper's initial settings are $0.05/day and $0.25 total, plus 100/day and 1,000 total attempt ceilings. Before **every** attempt it durably reserves $0.002753: 65,536 input tokens (covering the documented 64k context) at that price. Reservations are never refunded, including failures. Consequently the spending limits allow at most **18 attempts/day and 90 total**, usually much more conservatively than actual token charges. This avoids treating a timeout as free. UTC day rollover resets only daily counters. The total cap persists until deliberately raised.

These are local conservative accounting limits, **not a guarantee of the provider's invoice**. Request bytes are bounded; the API has no documented caller-supplied hard input-token cap. Returned usage exceeding the reservation stops further classification for operator review. Review unexpected costs against the account dashboard. Consult TypeSafe's [privacy policy](https://typesafe.ai/legal/privacy-policy) before exporting server text; do not infer zero retention from a no-training policy.

## Owner-run setup on db2

**Already completed for this pilot.** The following setup commands remain a reference. All three initial comparisons have finished; use the [current checkpoint](#initial-trial-complete) for next steps without repeating setup.

The developer account cannot access `/home/hermes`. Run these commands in the VPS terminal as **`hermes`**. The operator has already installed the pilot and received `!mod selftest` 9/9; **do not rerun the pilot installer or self-test updater** for this trial. The staged `/tmp/liberdus-jev-20260920.pyz` helper was checked against the current repository's setup, configuration and classifier source. It contains no credentials.

Disable the custom platform and restart the shared gateway before changing setup:

```bash
hermes -p liberdus-mod config set \
  platforms.liberdus_moderator.enabled false
hermes -p default gateway restart
```

Wait for the restart to finish successfully before continuing. The helper checks configuration on disk, not whether the previous process has stopped. Keep stock `platforms.discord.enabled` false in both profiles. A shared restart may briefly reconnect Telegram and can wait for in-flight turns to finish.

Save the key at the hidden prompt, then opt in to shadow evaluation with the limits above:

```bash
python3 /tmp/liberdus-jev-20260920.pyz key
```

Paste the TypeSafe key only at its hidden terminal prompt. Continue after the helper reports `"configured": "key"`; do not paste the key into Discord/chat or put it in a shell command. Then run:

```bash
python3 /tmp/liberdus-jev-20260920.pyz shadow \
  --daily-microusd 50000 \
  --total-microusd 250000
```

The key command updates only this profile's `.env`, mode 0600, preserving its bot token and other entries. The policy command updates only its `moderation.toml`. Both create private backups; neither restarts the gateway nor makes an API call. Optional `--daily-calls`, `--total-calls`, `--daily-microusd`, and `--total-microusd` arguments set different explicit caps (one million microusd = $1).

After the helper reports `"configured": "shadow"`, activate the custom platform and restart the shared gateway:

```bash
hermes -p liberdus-mod config set \
  platforms.liberdus_moderator.enabled true
hermes -p default gateway restart
```

Telegram may briefly reconnect during this shared restart. If the user service bus is unavailable, use:

```bash
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
```

In `bot-mod`, send `!mod status` after the restart. Expect `report_only`, `Connected: True`, JEV mode `shadow`, and enforcement disabled. `ready` means the worker is ready locally; it does not prove the key works. Zero AI attempts is normal before the first new qualifying incident. If moderation is paused, use the already-authorized `!mod resume` in `bot-mod` before testing.

### First live shadow trial

**Completed:** tests A, B and C returned `ok` with `announcement`, `promotion` and `quoted_warning` respectively. The instructions below preserve the original sequence for reference; no additional repetition is required. See the [current checkpoint](#initial-trial-complete) for results, limits and proposed follow-on work.

Use your normal authorized **human account**. The bot ignores its own messages and other bots/webhooks. `!mod selftest` remains synthetic and makes no provider calls, so it cannot test JEV authentication.

1. Post this identical fictional announcement once in each of `bot-test-1`, `bot-test-2`, and `bot-test-3`, within 120 seconds:

   ```text
   JEV test A: The community meeting is Friday at 18:00 UTC. Agenda: project updates and questions.
   ```

2. Wait for the usual private `cross_channel_repeat` report in `bot-mod`, then allow several seconds for the separate JEV worker. Leave the copies unchanged until the result arrives; edits/deletions can invalidate the evaluation. Run `!mod status` again: AI attempts should have increased.
3. Inspect the local result on the VPS:

   ```bash
   python3 /tmp/liberdus-jev-20260920.pyz results
   ```

4. Match the new report's incident ID to a returned record. A successful API evaluation has `"outcome": "ok"` and a typed `result` including `choice`, probabilities/confidence, model and token usage. `announcement` is the comparison label for this example, not a guaranteed model response or an authorization verdict. Record the actual choice, latency and any disagreement. An increased attempt counter alone does not prove success.

The helper reads at most 20 historical local records and makes no provider request. `missing_key`, `authentication_failed`, `access_denied`, `rate_limited`, `timeout`, `stale`, `input_limit`, or `budget_exhausted` explain degraded evaluation through records/status. Historical `ok` does not mean the evidence is still current. If the first attempt fails, inspect its fixed outcome before posting more examples. There is no automatic retry for the same incident.

After the first `ok`, compare these two additional fictional examples, **one case at a time**. Post each identical sentence once per test channel within 120 seconds, wait for its report/result, and leave at least six seconds between incident triggers:

- Promotion comparison: `JEV test B: Try our new premium plan today and use code DEMO for a discount. Sign up now!`
- Quoted-warning comparison: `JEV test C: Warning: messages saying "claim your free reward" may be scams. Do not follow their instructions.`

All three patterns should still produce ordinary code-rule reports regardless of JEV's choice. JEV results appear only in local records; no extra AI reply or label is posted to Discord. Use a fresh phrase for any later retest because evaluations are limited to one attempt per incident. Earlier incidents are not automatically evaluated when the flag is enabled. These examples test connectivity and initial label behavior, not classification accuracy across the server.

### Stop the trial

`!mod pause` in `bot-mod` immediately stops new moderation/evaluation and invalidates outstanding judgments; it leaves shadow mode configured. To restore the connected code-only bot with JEV off:

```bash
hermes -p liberdus-mod config set \
  platforms.liberdus_moderator.enabled false
hermes -p default gateway restart
```

After that restart completes:

```bash
python3 /tmp/liberdus-jev-20260920.pyz off
```

After the helper reports `"configured": "off"`:

```bash
hermes -p liberdus-mod config set \
  platforms.liberdus_moderator.enabled true
hermes -p default gateway restart
```

Check `!mod status` for `JEV: off / off` and disabled enforcement. If you paused earlier, use `!mod resume` to resume code-only monitoring. Historical AI attempts remain counted. The key may remain stored; off mode never reads it.

### Rebuild a missing helper

If `/tmp` was cleared, rebuild from the repository with its local pilot policy before running setup as `hermes`:

```bash
python3 scripts/build_pilot_bundle.py \
  --config config.local.toml \
  --output /tmp/liberdus-install-pilot-20260920.pyz \
  --jev-output /tmp/liberdus-jev-20260920.pyz
```

Use only the rebuilt JEV helper for an existing installation. For a genuinely new installation, follow [live-pilot.md](live-pilot.md) and establish the code-only baseline before enabling shadow mode.

## First live result

The dated result sections preserve the sequence of the trial, including the next-test instructions given at each stage. Use the [latest checkpoint](#initial-trial-complete) for current next steps.

**Operator evidence — September 20, 2026.** The supplied Discord report and local result share incident `9a7c69df8eb7447188e7ebfd09d2209b`, revision 1. The report identifies `cross_channel_repeat`, author `977263877391794217`, three copies in the approved test channels, and report-only with no public action.

| Field | Observed value |
| --- | --- |
| Provider outcome / model | `ok` / `jev-1.13.0` |
| Purpose | `announcement` |
| Model confidence | 0.98; a model score, not measured accuracy |
| Probabilities | announcement 0.98; quoted warning 0.02; other, promotion and unclear 0.0 |
| Recorded latency | 275 ms for this request |
| Usage | 543 input tokens; 54 output tokens |
| Local estimated cost | 23 microusd = $0.000023; not an authenticated invoice |
| Lifetime accounting | 1 attempt; 2,753 microusd = $0.002753 reserved against local caps |
| Worker state in results | `ok` |

This confirms that this request was authenticated, returned a response accepted by the strict client, and was saved with the matching incident. It also shows that the ordinary private rule report still arrived. The earlier status showed `JEV: shadow / ready` and zero attempts; the later result records the completed first attempt. `Coverage: code only` is the fixed detection-coverage label: rules find incidents and the separate JEV worker classifies selected incidents afterward. A new status request should include the recorded attempt.

The reserved amount is conservative local budget accounting, separate from the smaller token-based estimate. Neither value verifies the provider invoice. A single successful example establishes connectivity and the observed label only; it does not establish calibrated confidence, general accuracy, latency guarantees or review of every message. The developer recorded owner-supplied evidence without reading the profile/key or making another provider call. Continue with test B, then test C, one case at a time; no code, policy, scope or budget change is needed.

## Promotion result

**Operator evidence — September 20, 2026.** The new local record is incident `36b174a7f4014708b6e905422c8989c4`, revision 1, with the same policy hash, rubric hash and pinned model as the announcement case. It records `outcome: ok`, `choice: promotion`, confidence 0.99, latency 318 ms, 541 input tokens and 54 output tokens. Returned probabilities are promotion 1.0 and all other choices 0.0; preserve those separately from the confidence value, without interpreting them as proven certainty. This matches the intended promotional example. The supplied excerpt contains the local result, not a separate Discord report transcript for this case.

The earlier announcement record remains present. Lifetime accounting now records two attempts, worker state `ok`, and 5,506 microusd ($0.005506) reserved against local caps. Each result's token-based cost estimate is 23 microusd; their sum is 46 microusd ($0.000046). These estimates and conservative reservations do not verify provider billing. Two matching examples are an initial comparison, not an accuracy benchmark.

**Next:** post `JEV test C: Warning: messages saying "claim your free reward" may be scams. Do not follow their instructions.` identically once in each of the three approved test channels within 120 seconds, from the human operator account. Wait for the ordinary private report and JEV processing, leave the copies unchanged, then run `python3 /tmp/liberdus-jev-20260920.pyz results`. Match the new incident and compare its actual label with `quoted_warning`. This example tests whether quoted promotional language is recognized in a warning context. Its result is still pending; no policy, budget, scope or runtime change was made while recording the promotion evidence.

## Initial trial complete

**Quoted-warning result — September 20, 2026.** The operator supplied incident `b48ee236ff774e0f8efec35caa8ba447`, revision 1, with `outcome: ok`, model `jev-1.13.0`, `choice: quoted_warning`, confidence 1.0 and recorded latency 375 ms. Usage is 541 input tokens and 55 output tokens; the local estimated cost is 23 microusd. The probability map gives quoted warning 1.0 and all other choices 0.0. These are returned model scores, not proof of certainty. The policy and rubric hashes match the preceding two cases. This excerpt contains local results, not a separate Discord report transcript for the third case.

| Intended comparison | Returned choice | Model confidence | Recorded latency |
| --- | --- | --- | --- |
| Announcement | `announcement` | 0.98 | 275 ms |
| Promotion | `promotion` | 0.99 | 318 ms |
| Quoted warning | `quoted_warning` | 1.0 | 375 ms |

All three records have `outcome: ok`. Lifetime accounting records three attempts, worker state `ok`, and 8,259 microusd ($0.008259) conservatively reserved against the local caps. The three individual token-based estimates sum to 69 microusd ($0.000069), distinct from reservations and from an authenticated provider invoice.

**Conclusion:** the initial three examples are complete and matched their intended labels, including promotional wording used inside a warning. This verifies initial provider connectivity, typed-response validation and local persistence on these live incidents. Three deliberately written examples do not establish a general accuracy rate, confidence calibration, production readiness, guaranteed latency or detection of messages the rules never select. Do not repeat these completed examples just to advance the project.

**Current runtime:** the operator's result still reports `mode: shadow`; this checkpoint did not turn it off or change the live profile. While shadow remains enabled, future eligible incidents in the approved test scope may produce provider calls under the existing limits. The [stop procedure](#stop-the-trial) restores code-only monitoring when desired. Classifications currently remain local, fixed Discord reports still come from code rules, and enforcement remains disabled.

**Proposed next improvement, not implemented:** add an on-demand saved JEV result to the authorized private `!mod incident <id>` lookup, so the operator can review it in `bot-mod` without repeatedly opening SSH. Preserve existing command authorization. Display only stored typed data and its incident revision, age and current/historical state; make no new provider call from the lookup and do not change report selection, suppression or enforcement. This is a recommendation for a separate code change, not a feature enabled by this trial.

Before allowing JEV to influence reports or actions, review a broader set of manually labeled examples and disagreements, including ordinary discussion, mixed/unclear intent, multilingual messages and adversarial instructions. Wider message scanning, production-channel access and enforcement remain separate decisions. The developer recorded the supplied results without accessing credentials, sending Discord messages, calling TypeSafe or restarting the gateway.

## Validation and limits

Tests use synthetic events and mocked provider/Discord network edges. They cover default-off key isolation, strict configuration/response validation, immutable evidence binding, budgets/restart accounting, cancellation, stale results, private key storage, and uninterrupted rule reports while a provider call waits. No paid JEV call or live Discord acceptance test was run during implementation.

Before relying on JEV judgments beyond shadow mode, compare labeled legitimate announcements, promotional spam, quoted scam warnings, ordinary messages, multilingual text and adversarial instructions. Review disagreements and confidence calibration with moderators. Current code only sends incidents already caught by rules, so it cannot measure or fix abuse the rules never flag. Report annotations, broader scanning, moderator feedback, and enforcement are separate future changes.
