# Hermes integration evidence and next steps

**Latest checkpoint — September 20, 2026:** installation, private `!mod status`, and the three-channel repeat report are operator-confirmed. JEV is deferred with its flag off because TypeSafe access is pending. Earlier checkpoints below retain their historical scope; see the [latest live evidence](#live-baseline-and-jev-deferral--september-20-2026) and [remaining pilot checks](live-pilot.md#remaining-code-only-pilot-checks).

## September 20 operator-reported checkpoint

This checkpoint supersedes unknowns in the September 15 inventory below; the older source review is retained as historical evidence.

- Installed Hermes: v0.21.3, upstream `c1488ac9` (full upstream SHA `c1488ac947c9bc33fd65ec464548dc9d8edd6122`), Python 3.11.16, source under `/home/hermes/.hermes/hermes-agent`. The matching public upstream archive has been obtained for review; local installation modifications have not been inspected.
- Dedicated profile created at `/home/hermes/.hermes/profiles/liberdus-mod`. The owner-run transfer helper moved the bot token from default to this profile, retained a numeric operator allowlist, disabled allow-all settings, and made protected backups. No token is stored in this repository.
- `platforms.discord.enabled` is false in both profiles. The owner restarted the shared user gateway, now PID 814234 serving default and liberdus-mod. Telegram's runtime status belongs to that PID. The recorded Discord connection belongs to old PID 800855 and is stale, not a live connection probe.
- The operator supplied the server, bot, three test-channel, private command-channel, and operator IDs. They are saved in ignored `config.local.toml`, with AI, enforcement, and optional logs disabled. Numeric validation is not identity verification.
- The core and preflight tests pass on Python 3.11.16 and 3.12.3 (77 tests). The package now accepts Python >=3.11. This is compatibility evidence for this package, not evidence of successful loading into Hermes.
- The separate [read-only preflight](preflight.md) checks token identity, guild membership, text-channel scope, and effective permissions without reading or sending messages or starting a gateway. The operator ran it and supplied passing live metadata checks on September 20; details below.

At that initial checkpoint the adapter and installed interfaces were pending. The subsequent sections record verified installed interfaces and completed offline adapter work. Later operator evidence confirms installation and the basic live report path; full Phase 4 acceptance remains open.

## September 20 Discord preflight result

The operator ran the owner-side preflight and supplied its output. Token identity matches bot `1548537340870533150` in server `746426387606274199`.

| Channel | ID | Bot can view/history | Bot can send | Everyone hidden | Administrator |
| --- | --- | --- | --- | --- | --- |
| bot-test-1 | 1551249559819264030 | yes | no | yes | no |
| bot-test-2 | 1551249642216357908 | yes | no | yes | no |
| bot-test-3 | 1551249693399584818 | yes | no | yes | no |
| bot-mod | 1551252553331642558 | yes | yes | yes | no |

All required metadata permission checks passed. An additional role/member overwrite `1302455329795342377` grants view access in all four channels; its identity and membership have not been audited. The preflight neither read nor sent messages. This preflight alone did not verify gateway intents, event capture or delivery and did not activate the bot. Subsequent live evidence is recorded below.

### Selected integration direction

Use a dedicated registered Hermes platform adapter for this code-only pilot, with stock Discord disabled in both profiles. The normal Discord plugin's native listeners cannot prevent its conversational handler from also seeing events. The moderation adapter must own the single bot connection, convert native events into core inputs, and route only independently authorized private commands and fixed reports. It must never invoke the base adapter's conversational `handle_message` path or expose generic outbound sending to agent/cron tools.

The exact c1488 upstream source supports `PluginContext.register_platform`, profile-scoped secret lookup, and a common Discord-token connection lock (`discord-bot-token`). Its plugin enablement pass can automatically enable registered platforms, so registration alone is not a sufficient off switch: explicit activation and stock-platform exclusion must be tested through the real config loader before installation.

`scripts/inspect_hermes_runtime.py` is the next owner-run check. It reads installed source/version metadata and imports the required interfaces using the installation's Python under an empty temporary HOME/HERMES_HOME. It omits inherited credentials and raw import logs, disables bytecode writes, and blocks network/process creation during the child import probe. It does not call the real profile configuration loader, load plugins, change packages, or restart services. This probe passed its interface checks against the public c1488 source with isolated Python 3.11.16, discord.py 2.7.1, and aiohttp 3.14.3; the operator then supplied the installed-environment result: all interfaces passed, the full commit matches, inspected interface files are unmodified, and Python/discord.py/aiohttp/PyYAML versions match (3.11.16 / 2.7.1 / 3.14.3 / 6.0.3).

The dedicated adapter is now implemented with bounded event/edit handling, code-only private commands, durable delivery outcomes, and no conversational fallback. The disabled installer and real Hermes loader/config boundaries pass offline tests. The subsequent live checkpoint confirms installation and the basic report path; remaining live acceptance is pending. Keep both stock Discord configurations disabled. See [live pilot instructions](live-pilot.md) for installation, activation conditions, tested behavior, and recovery.

## Historical September 15 review

Checked: September 15, 2026. This document records a read-only VPS inventory and a review of public upstream source. It does **not** certify the installed Hermes version or mark Phase 4 of `BUILD_PLAN.md` complete. The repository's offline harness work can proceed with synthetic events; live Discord testing remains gated on the integration proof below.

## VPS inventory

The VPS has a separate `hermes` OS account and an active process named `hermes`, owned by that account. At inspection time, PID `18430` belonged to:

```text
/user.slice/user-1001.slice/user@1001.service/app.slice/hermes-gateway.service
```

This is evidence of a running Hermes gateway under a different account. It is not evidence that Discord is configured or that this gateway uses the intended bot identity.

The `developer` account has no `hermes` command on its PATH and no importable `hermes_cli`, `hermes_agent`, or `discord` package in its default Python environment. `/home/hermes` is not accessible to `developer`; reading the process executable or working directory is also denied. Passwordless sudo is unavailable. Therefore the installed commit/version, installation path, active profile, platform configuration, bot identity, and supported plugin interfaces remain unknown.

No existing service was restarted, no Hermes installation or configuration was changed, and no credential file was read. Do not install a second Hermes instance to work around this inventory gap.

## Reviewed upstream interface

Public source was inspected at commit **`f9ea3a5328f3c73c8845b2af1076653dac64ac2f`** of `NousResearch/hermes-agent`. The temporary checkout used for the review is not a project dependency. The commit is a source reference, not a requested upgrade or a tested minimum supported version.

| Requirement | Evidence and proposed boundary |
| --- | --- |
| Reuse one Discord connection | [`PluginContext.register_platform_handler`](https://github.com/NousResearch/hermes-agent/blob/f9ea3a5328f3c73c8845b2af1076653dac64ac2f/hermes_cli/plugins.py#L831) registers a factory receiving the existing native Discord `commands.Bot` and a read-only adapter handle. |
| Observe ordinary messages | Add a native `on_message` listener to that existing client, then convert the SDK object into a framework-independent immutable event. The core handler's admission and mention filters do not establish our moderation coverage. |
| Observe cached and uncached edits | Add `on_raw_message_edit`. The reviewed [core edit handler](https://github.com/NousResearch/hermes-agent/blob/f9ea3a5328f3c73c8845b2af1076653dac64ac2f/plugins/platforms/discord/adapter.py#L1551) handles cached edits; no native raw-edit handler was found in this source. Fetch missing content/identity through a bounded, scoped transport path, or record unavailable evidence without classifying it. |
| Authorize private commands | Require exact guild and command-channel IDs, then an authorized user or role from actual Discord metadata. Public content cannot supply these identities. A generic `register_command` handler receives raw arguments, so it is insufficient on its own for this authorization check. |
| Send fixed reports without inference | A report sink can use the same native client's channel API with mentions disabled. The reviewed [`adapter.send`](https://github.com/NousResearch/hermes-agent/blob/f9ea3a5328f3c73c8845b2af1076653dac64ac2f/plugins/platforms/discord/adapter.py#L2827) is another direct delivery path, but its routing, formatting, and mention settings must be verified before choosing it. Neither path needs a model call. |

The current [plugin authoring guide](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/#register-native-platform-handlers-any-platform) documents this native-handler extension. Its existence in upstream documentation does not establish its presence in the installed runtime.

Keep the integration thin: SDK events become immutable harness inputs; harness decisions become explicit report-sink requests. Code rules, SQLite state, command authorization, and report formatting should be testable without importing Hermes or connecting Discord. Avoid internal adapter imports or monkey-patching the running gateway. If the installed version lacks the needed public interface, record that gap and choose a version-pinned adapter plugin or tracked upstream extension before live use.

## Separate conversational routing from moderation collection

Adding a native listener does **not** consume an event or prevent Hermes's normal handler from running. The [core message admission path](https://github.com/NousResearch/hermes-agent/blob/f9ea3a5328f3c73c8845b2af1076653dac64ac2f/plugins/platforms/discord/adapter.py#L1380) and [conversational channel/mention routing](https://github.com/NousResearch/hermes-agent/blob/f9ea3a5328f3c73c8845b2af1076653dac64ac2f/plugins/platforms/discord/adapter.py#L5663) are separate from our listener.

Before enabling the plugin, prove that monitored messages, explicit bot mentions, unauthorized private commands, and DMs cannot fall through to general Hermes inference or tools. For the initial code-only pilot, the ordinary conversational path should remain disabled for the moderation deployment. Do not widen core sender authorization to make collection work. The private command gate belongs to the harness even when Hermes has its own allowlists.

Use a dedicated moderation profile and verify that only one gateway owns the intended bot credential. Profiles isolate configuration and state; they do not replace OS permissions. Do not alter the currently running profile until its purpose and existing integrations are understood.

## Startup, failure, and evidence gaps

- In the reviewed [Discord connection implementation](https://github.com/NousResearch/hermes-agent/blob/f9ea3a5328f3c73c8845b2af1076653dac64ac2f/plugins/platforms/discord/adapter.py#L1321), factories are wired **after** readiness. Messages arriving before listener registration may be missed. Test this startup interval and use a bounded, explicitly scoped recovery scan if needed. Report incomplete coverage instead of claiming all messages were reviewed.
- [`_wire_plugin_handlers`](https://github.com/NousResearch/hermes-agent/blob/f9ea3a5328f3c73c8845b2af1076653dac64ac2f/gateway/platforms/base.py#L2141) logs a factory exception and lets the platform remain connected. Bot online status is not proof that moderation is active. A deployment readiness check must confirm successful listener registration and harness health; a failed plugin must never enable conversational fallback.
- Verify reconnects do not register duplicate listeners or create duplicate reports. Use durable event identity/version deduplication as an additional guard.
- Copy incoming evidence immediately. The reviewed core handler can modify a message object's content during mention normalization, so concurrent native handlers must not share mutable SDK objects with deferred work. Prove that the chosen callback preserves the original evidence or use an appropriate raw event boundary.
- Raw edit events may be partial, delayed, or out of order. Keep source timestamps separate from local observation time, and never let an older event restore superseded evidence. Fetches may fail or observe a later edit; record this accurately.
- An edit can invalidate a grouped cross-channel incident. Revalidate current evidence before delivery or future enforcement; do not treat a stored incident as permanently true.
- Limit queue size, retry attempts, and recovery fetches. Delivery failures must not rerun classification. A send accepted by Discord but interrupted before its ID is saved is an uncertain outcome, not a guaranteed safe retry.

## Scoped access needed for the installed runtime

The next inventory should be performed by the `hermes` account owner or through an administrator-provided, narrowly scoped read-only inspection. Appropriate options are an owner-run command transcript containing only non-secret metadata, or temporary read access to the identified installation source and selected service metadata. There is no need to make `/home/hermes` broadly readable, share bot/provider tokens, or give the project access to personal conversations.

The owner can collect these facts without stopping the gateway:

1. Resolve the installed executable and source checkout, report its version/commit and whether local code changes exist. Read the applicable installation `AGENTS.md` files before inspecting source.
2. Report the service's active state, main PID, service unit location, and working directory. Inspect the unit locally to identify its runtime/profile path; omit environment values and redact any credentials embedded in command arguments.
3. Report only the non-secret effective moderation-relevant settings: enabled platforms, active profile name, event intents, channel scope, DM policy, mention behavior, and public conversational access. Do not dump complete configuration files or the environment.
4. Verify the actual plugin registration, event dispatch, and direct-delivery interfaces in that installed source. Check whether native handlers run before/after readiness and how errors/reconnects behave.
5. Confirm whether the current gateway already owns the proposed bot identity, using non-secret application/bot IDs. Do not expose the token used to establish that identity.

Where paths or options are unknown, resolve them through the owner's existing installation rather than guessing commands or upgrading. This inspection does not require `chmod` changes, service restarts, new credentials, or a live Discord message.

## Phase 4 acceptance evidence still required

Keep inference and enforcement disabled while running the integration prototype in approved test channels. Record the exact installed version, selected transport, relevant configuration, and observed results for:

1. An ordinary member's unmentioned message and a message mentioning another member reaching the harness with correct content and IDs.
2. Cached and uncached edits, no-content-change updates, delayed events, and unavailable-message fetches producing correct evidence versions or explicit coverage gaps.
3. Own posts, duplicate events, wrong guilds/channels, excluded bot/webhook content, and unsupported surfaces being handled as declared without entering classification.
4. Private commands succeeding only when both channel and operator checks pass, including an unauthorized user in the correct channel and an authorized user in a public channel.
5. A fixed private report using zero model calls, no unintended mentions, and the intended destination.
6. Public bot mentions, DMs, malformed events, plugin failure, startup gaps, and reconnects never creating privileged conversational access or duplicate processing.

Offline unit tests and source inspection provide useful preparation but cannot satisfy these live integration checks. Phase 4 remains open until the installed runtime and approved Discord test prove them; later live phases must not depend on an assumed integration.

## Optional JEV worker — September 20, 2026

Version 0.3.0 implements default-off JEV shadow evaluation alongside the rules and report sender. The selected API integration bypasses the general agent loop and uses the owning profile's TypeSafe key. No JEV skill or MCP is installed in `liberdus-mod`. See [integration comparison, account/key setup and budgets](jev.md); build-plan section 10.7 records the selected scope. The basic zero-inference baseline has now passed. Shadow opt-in is deferred by the operator while TypeSafe access is unavailable.

## Live baseline and JEV deferral — September 20, 2026

The owner supplied a successful installer result, enabled the custom moderation platform, and restarted the shared service to PID `876774` at that checkpoint. `!mod status` in `bot-mod` returned connected/report-only status, JEV off, zero AI attempts and disabled enforcement. A fresh qualifying message repeated across all three approved test channels produced private incident report `1ce20048a42442bc92784aaaa9184d69`, revision 1, rule `cross_channel_repeat`, author `977263877391794217`, three copies and the expected channel links.

This establishes the basic collection/match/private-delivery path from operator evidence. It does not complete the remaining edit/delete, pause/resume, exclusion, authorization, or restart/duplicate-delivery checks. Follow the [remaining code-only pilot checks](live-pilot.md#remaining-code-only-pilot-checks). TypeSafe access is unavailable and the operator explicitly deferred JEV; keep the flag off and continue the existing pilot. No provider or runtime change was made while recording this checkpoint.
