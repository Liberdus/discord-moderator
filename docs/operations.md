# Operations: offline implementation

Status: September 20, 2026. This guide describes the offline CLI and core. The optional Hermes adapter and disabled installer are implemented; the owner has verified bot identity, pilot channel permissions, and installed runtime interfaces. Live installation and acceptance remain pending. See [live-pilot.md](live-pilot.md) for the adapter, commands, activation, delivery, and stop procedure. The historical build plan is not an installation record.

## Environment and configuration

Use Python 3.11 or newer. The core and fixture CLI use the Python standard library; the optional adapter uses the verified existing Hermes and Discord libraries. Run the documented commands from the repository root or install the package in a virtual environment. `python3 -m liberdus_moderator --help` lists local CLI commands.

Copy `examples/config.toml` to the ignored `config.local.toml` before configuring a future pilot. Numeric IDs are strings and the supplied values are synthetic. Do not treat those values as actual Liberdus IDs. Store credentials outside the repository; the TOML policy has no credential fields. The optional adapter reads the bot token from Hermes's profile-scoped secret resolver.

Configuration deliberately separates:

- `scope.monitored_channel_ids`: channels contributing message evidence.
- `scope.command_channel_ids`: approved private command destinations; the first entry receives prepared moderator-report payloads.
- `scope.operator_user_ids` and `scope.operator_role_ids`: either can identify an operator, but the approved guild and private channel checks must also pass.
- `scope.log_channel_id`: optional private log destination, used only with `logs_enabled`; it is not the moderator-report destination.
- `storage`: database path, retention duration, and capacity bounds.
- `rules`: known domains, repeat thresholds, notification cooldown, and scoped crosspost exceptions.

`mode` accepts `report_only` or `off`. `actions_enabled` must be `false`. Schema 1 remains AI-disabled; schema 2 permits `ai_enabled = true` only with an explicitly budgeted `classifier.mode = "shadow"`. Unsupported classifier report annotations are rejected. See [JEV operations](jev.md). An unmatched message produces `no_match`, not a safety clearance. The matcher does not understand intent, satire, quoted scams, or policy context.

Relative `storage.database_path` values resolve from the current working directory. Use `--database` to override the path explicitly. The live adapter requires the absolute moderation-profile state path and one connection owner. Do not point test commands at another service's database.

The engine binds state to a hash of the full configuration, including storage settings and the configured database path. Starting `replay` or a local `command` with a changed configuration clears active message contributions, marks open incidents `policy_changed`, and cancels pending reports; retained incident history remains available. Changing `storage.database_path` inside a copied configuration therefore starts a fresh coverage window, even if other policy settings are unchanged. An explicit `--database` override selects a path without changing the configuration hash. `incidents`, `reports`, and `classifications` inspect retained state without constructing the engine or activating a changed configuration. Use the original configuration and an explicit path override when verifying a restore.

## Fixtures and local commands

Message JSONL requires `guild_id`, `channel_id`, `message_id`, `author_id`, `content`, and `created_at` in Unix seconds. Message content is limited to 4,000 code points; IDs are canonical positive ASCII decimal strings of at most 20 digits. Invalid or unknown fields are rejected. Optional fields include `edited_at`, `author_role_ids`, `is_bot`, `is_webhook`, `is_thread`, and `has_attachments`. Use the original creation time on edits and supply the edit time separately. Exact replayed events must not increase counts; edits update the original message's contribution rather than introducing an additional post.

`examples/messages.jsonl` covers ordinary setup text, cross-channel repetition with case/whitespace variation, a duplicate event, an edit removing one repeated copy, a blocked domain, excluded scope/content, same-channel repetition, and approved announcement crossposts. The domains and account identifiers are synthetic. Replay uses event timestamps deterministically, so old fixtures can still exercise rolling windows; it is a test clock, not a production clock.

`simulate` processes fixtures into an independent in-memory database and does not write the configured deployment database. `replay` persists state at the selected path. `incidents` reads retained cases and `reports` reads pending report payloads; neither sends anything to Discord. A pending outbox payload is not proof of delivery; the optional adapter maintains separate attempted/sent/uncertain outcomes.

`command` accepts a JSON request with this shape:

```json
{
  "guild_id": "1",
  "channel_id": "201",
  "user_id": "42",
  "role_ids": [],
  "command": "status",
  "arguments": []
}
```

The fixtures are local test inputs, **not registered Discord slash commands**. Identity fields are caller-supplied during local tests; the live adapter derives identity from authenticated Discord metadata and restricts the initial pilot to numeric user IDs. Never trust message text to supply operator identity.

| Command | Local behavior |
| --- | --- |
| `status` | Show configured/paused state and record counts. |
| `pause` | Persist a pause, clear the current message window, invalidate open incidents, and cancel pending report payloads while retaining incident history. |
| `resume` | Resume within the configured mode with a fresh message window because edits may have been missed during the pause; does not change the enforcement or AI feature flags (already-configured shadow evaluation can resume). |
| `incident` | Inspect the incident ID in `arguments`. |
| `logs` | Use `arguments: ["on"]` or `["off"]` to persist optional log-payload generation; enabling requires a configured log channel, and disabling cancels pending log payloads. No Discord posting occurs. |
| `explain` | Inspect the incident ID in `arguments` and return saved reasons/evidence without changing live counters. |
| `approve` | Unsupported; no enforcement path exists. |

For an incident request, set `"command": "explain"` and `"arguments": ["INCIDENT_ID_FROM_OUTPUT"]`. Command authorization requires the configured guild, command channel, and an allowed user or role together. An operator posting in a public/monitored channel cannot administer the harness through that channel.

## Rules and limitations

Cross-channel repetition groups one author's matching text within a rolling window across distinct monitored channels. The default is 3 channels in 120 seconds. Same-channel repetition uses its own threshold and window. Original creation times determine participation, so editing an old post cannot indefinitely refresh its window. Common short phrases and URL-only content do not qualify as repeated prose; a known blocked domain can still generate a separate review candidate.

Prose matching normalizes Unicode, case, and whitespace; full HTTP(S) URL spelling remains significant. This is not semantic or fuzzy similarity. An approved crosspost exception is scoped to authors, channels, and content, and only exempts the cross-channel rule. Other rules still run.

Bots, webhooks, threads, and messages containing attachments are excluded visibly by the current engine, including text accompanying an attachment. The attachment itself is never inspected. Every decision/report must preserve the difference between observed scope and unreviewed content. No decision can grant broader monitoring access. Capacity limits constrain retained messages, incident evidence versions, incidents, and pending reports. Exhausted message/incident/evidence capacity pauses processing, clears the current message window, invalidates open incidents, and cancels pending reports while retaining incident history; an exhausted report queue exposes degraded reporting. Inspect these outcomes instead of interpreting them as successful reviews. Closing an incident reserves one terminal audit revision beyond `max_evidence_versions`, so a full evidence history can still record its invalidation or other closing transition.

SQLite is authoritative for current evidence, incidents, pause state, the runtime log toggle, and pending payloads. `logs_enabled` initializes the runtime toggle on a fresh database; later authorized `logs` commands change the persisted value. Notification cooldowns coalesce burst reports. The implemented optional sender suppresses mentions, rechecks destination scope and incident revision, and records uncertain outcomes without application retries. Enforcement and its reconciliation remain unimplemented.

## Before a live pilot

Read [integration notes](integration.md) and [live-pilot.md](live-pilot.md). The owner verified the installed Hermes interfaces through a credential-free probe; the developer account still cannot access the real Hermes profile. Do not start a second gateway or reuse a token from an unverified configuration. The intended integration has one owner for the Discord connection.

Verify the actual bot identity, application ownership, server installation, requested intents, and effective channel permissions. Confirm ordinary messages and relevant edit events reach this core before any command/public-content interpretation by Hermes. Confirm private command identity and private report delivery without inference. Keep the initial pilot report-only with the current AI/actions settings disabled.

The existing shared gateway owns the service. Its moderation token remains profile-local. The optional adapter/sender is ready for disabled installation; production channel selection and live acceptance remain separate future steps. Passing local tests does not establish live bot coverage or complete the build plan.

## GitHub checks

The initial GitHub push could not include an active Actions workflow because the authenticated GitHub CLI OAuth grant lacks the `workflow` scope. The ready-to-use definition is retained as [ci-workflow.yml](ci-workflow.yml), outside `.github/workflows`, so it does not execute. Core/setup tests passed on Python 3.11 and 3.12; the template's Python 3.13 job has not run. To enable it later, authorize the additional GitHub CLI scope and move the file to `.github/workflows/ci.yml`. No bot token or model credential is needed by those checks.
