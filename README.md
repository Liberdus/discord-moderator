# Liberdus Discord Moderator

A **report-only moderation core** with an optional, explicitly enabled Hermes Discord pilot plugin. It accepts synthetic Discord-shaped events, applies deterministic rules, and records evidence, review incidents, and pending private-report payloads in SQLite. The moderation core has no runtime dependencies and makes no AI or Discord calls. A separate, explicitly invoked preflight utility makes read-only Discord API requests to verify setup.

**Status, September 20, 2026:** the owner installed the plugin in `liberdus-mod`, enabled the custom moderation platform, and supplied a live `!mod status` response plus a successful three-channel repeat report. The basic Discord collection → rule match → private report path passed. Pause/resume command transitions and a new report after resume are also operator-confirmed. The four-copy single-channel repetition test produced the expected private report, and editing a matching cross-channel copy correctly withdrew its incident at revision 2. Remaining checks include explicit paused-pattern suppression, deletions, authorization and restart behavior. **JEV is deferred because TypeSafe access is pending; its flag remains off.** AI and enforcement stay disabled. See [live pilot checks](docs/live-pilot.md#remaining-code-only-pilot-checks), [JEV deferral and future setup](docs/jev.md), and [integration evidence](docs/integration.md).

## Offline fixture flow

```text
+------------------------------+
| LOCAL SYNTHETIC EVENT        |
+--------------+---------------+
               |
               v
+------------------------------+
| Scope + dedup + edit checks  |
+--------------+---------------+
               |
               v
+------------------------------+
| Code rules; no AI calls      |
+--------------+---------------+
               |
               v
+------------------------------+
| SQLite evidence + incidents  |
+--------------+---------------+
               |
               v
+------------------------------+
| Pending private-report data  |
| CLI does not send to Discord |
+------------------------------+
```

The optional adapter owns one Discord connection inside the existing Hermes gateway, supplies events to the core, and delivers fixed private review reports. It never enters the general Hermes conversation path. Public message content is evidence, not authority to change policy or run tools.

## Try it locally

Use Python 3.11 or newer, from this repository's root. No package installation is needed for these commands:

```bash
python3 -m liberdus_moderator validate \
  --config examples/config.toml

python3 -m liberdus_moderator simulate \
  --config examples/config.toml \
  --events examples/messages.jsonl

python3 -m liberdus_moderator replay \
  --config examples/config.toml \
  --events examples/messages.jsonl \
  --database /tmp/liberdus-demo.sqlite3

python3 -m liberdus_moderator incidents \
  --config examples/config.toml \
  --database /tmp/liberdus-demo.sqlite3

python3 -m liberdus_moderator reports \
  --config examples/config.toml \
  --database /tmp/liberdus-demo.sqlite3

python3 -m liberdus_moderator command \
  --config examples/config.toml \
  --request examples/command.json \
  --database /tmp/liberdus-demo.sqlite3
```

`simulate` uses its own in-memory store. `replay` emits JSON decisions and uses fixture timestamps for reproducible windows. Replaying the same messages into retained state should not count them again. The sample IDs and `.invalid` domains are fictional; the example configuration is not a Liberdus deployment configuration. Keep the same `--database` path when inspecting replay results.

An editable package installation is optional: `python3 -m pip install -e .`. It adds the equivalent `liberdus-moderator` command. Setuptools is only a build dependency.

## Implemented scope

- Strict configuration and event validation; guild/channel boundaries; exclusions for the bot itself, other bots, webhooks, threads, and messages with attachments.
- Blocked-domain review candidates, same-channel repetition, and one member repeating content across multiple monitored channels.
- Cross-channel default: matching content in **3 monitored channels within 120 seconds**. Prose normalization handles case and whitespace; URL spelling is preserved. Fuzzy matching and paraphrase detection are deferred. Scoped approved-crosspost exceptions affect only the cross-channel rule.
- SQLite evidence versions and event deduplication, durable repeat windows, grouped incidents, notification cooldowns, and bounded pending reports.
- Local moderator-command fixtures requiring both an approved command channel and an authorized operator. `pause` clears the active message window and invalidates open incidents while retaining history; `resume` starts a fresh window. `explain` shows saved incident evidence. Enforcement approval is unsupported.
- In-memory rule simulation and automated tests, without model credentials or Discord access.
- Optional JEV shadow evaluations for existing incidents only, with typed results, budget reservations, one attempt per incident, bounded async I/O, and profile-local secrets. Off is the default.

A `no_match` decision means **no implemented code rule matched**. It does not claim that a message is safe or that AI reviewed it. Blocked-domain matches are review candidates even when context might make a link legitimate. Reports observe only the configured channels and supported content.

The CLI prepares report payloads without sending. The optional live adapter records delivery attempts and Discord message IDs; uncertain attempts are never automatically retried. Public replies, deletions, bans, kicks, timeouts, and role changes remain unimplemented. Enforcement remains disabled. Optional JEV shadow evaluation uses schema 2, an explicit AI opt-in, and finite budgets; it never changes Discord reports. See [JEV setup and integration research](docs/jev.md). Private Discord logs are optional in the plan; durable local incident records are independent of them.

## Next milestones

1. Finish the [remaining code-only live checks](docs/live-pilot.md#remaining-code-only-pilot-checks) in the three test channels and `bot-mod`; record each observed result.
2. Observe the approved test channels and review false positives, grouping and report clarity before tuning thresholds or scoped crosspost exceptions.
3. Consider a separately scoped production-channel pilot after the live checks are complete. Enforcement remains unimplemented.
4. Resume JEV later when TypeSafe access is available and the operator explicitly chooses to configure it. Keep the flag off meanwhile.

The successful repeat report proves the basic path, not every integration case or all live phases. Protected-role enforcement, general Hermes conversation, AI-generated rule candidates, review buttons, and action reconciliation remain future work.

## Development and documentation

```bash
python3 -m unittest discover -s tests -v
```

The inactive [CI template](docs/ci-workflow.yml) runs the tests and fixture commands on Python 3.11, 3.12, and 3.13, with read-only repository permissions and no secrets. The 109 core/setup tests pass on Python 3.11.16 and 3.12.3; 23 additional integration tests use the real c1488 Hermes source and discord.py 2.7.1 with network edges mocked. Those integration tests are separate from the CI template and do not constitute a live gateway test; GitHub CI is not enabled yet because the current GitHub CLI login lacks the separate `workflow` scope. Once authorized, move the template to `.github/workflows/ci.yml`. See [operations](docs/operations.md), [recovery](docs/recovery.md), and [integration](docs/integration.md).

[BUILD_PLAN.md](docs/BUILD_PLAN.md) retains the historical planning baseline with dated additions. Section 10.6 records the original JEV proposal; section 10.7 records the selected incident-only shadow implementation. Section 10.8 records the live baseline result and the decision to defer JEV with its flag off. [JEV instructions](docs/jev.md) cover API keys, budgets, and activation. Its original status statements and local workstation links are preserved; this README and the operations guide describe current implementation status. It also records feature ideas from Sentinel AI, Defender/Warden, Zeppelin, Modcord, and Omnicord. This initial core is independently implemented; no third-party bot source has been imported.

Project licensing has not yet been selected. Keep the repository private pending the owner's publishing and licensing decision.
