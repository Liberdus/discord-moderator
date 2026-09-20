# Optional JEV shadow evaluation

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

The developer account cannot access `/home/hermes`. Run these commands as **`hermes`**. The two staged zipapps contain code and policy, no credentials. Rebuild them from the repository if `/tmp` was cleared:

```bash
python3 scripts/build_pilot_bundle.py \
  --config config.local.toml \
  --output /tmp/liberdus-install-pilot-20260920.pyz \
  --jev-output /tmp/liberdus-jev-20260920.pyz
```

First install the pilot, disabled:

```bash
python3 /tmp/liberdus-install-pilot-20260920.pyz
```

It refuses an existing installation instead of replacing files. Follow [live-pilot.md](live-pilot.md): confirm Message Content Intent is saved in the Developer Portal, activate the custom platform only, and verify the baseline three-channel repeat test with JEV off. Keep stock Discord false in both profiles.

When ready for JEV, disable the custom platform and restart before changing setup:

```bash
hermes -p liberdus-mod config set \
  platforms.liberdus_moderator.enabled false
hermes -p default gateway restart
```

Save the key at the hidden prompt, then opt in to shadow evaluation with the limits above:

```bash
python3 /tmp/liberdus-jev-20260920.pyz key
python3 /tmp/liberdus-jev-20260920.pyz shadow
```

The key command updates only this profile's `.env`, mode 0600, preserving its bot token and other entries. The policy command updates only its `moderation.toml`. Both create private backups; neither restarts the gateway nor makes an API call. Optional `--daily-calls`, `--total-calls`, `--daily-microusd`, and `--total-microusd` arguments set different explicit caps (one million microusd = $1).

Activate the custom platform and restart the shared gateway:

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

In `bot-mod`, send `!mod status`. Post the same harmless sentence in `bot-test-1`, `bot-test-2`, and `bot-test-3` within 120 seconds. Expect the usual private rule report; after processing, AI attempts should increase. Inspect the result on the VPS:

```bash
python3 /tmp/liberdus-jev-20260920.pyz results
```

This reads at most 20 historical local records. A successful result is `outcome: ok`; check its revision and recorded purpose/probabilities. `missing_key`, `authentication_failed`, `rate_limited`, `timeout`, `stale`, `input_limit`, or `budget_exhausted` explain degraded evaluation through records/status. Historical `ok` does not mean the evidence is still current. `!mod pause` also stops new evaluation and invalidates outstanding judgments.

To return to the code-only bot: disable the custom platform, restart, run the helper with `off`, then re-enable the platform and restart. The key may remain stored; off mode never reads it.

## Validation and limits

Tests use synthetic events and mocked provider/Discord network edges. They cover default-off key isolation, strict configuration/response validation, immutable evidence binding, budgets/restart accounting, cancellation, stale results, private key storage, and uninterrupted rule reports while a provider call waits. No paid JEV call or live Discord acceptance test was run during implementation.

Before relying on JEV judgments beyond shadow mode, compare labeled legitimate announcements, promotional spam, quoted scam warnings, ordinary messages, multilingual text and adversarial instructions. Review disagreements and confidence calibration with moderators. Current code only sends incidents already caught by rules, so it cannot measure or fix abuse the rules never flag. Report annotations, broader scanning, moderator feedback, and enforcement are separate future changes.
