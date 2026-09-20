# Liberdus Discord Moderator

A **report-only moderation core** with an optional, explicitly enabled Hermes Discord pilot plugin. It accepts synthetic Discord-shaped events, applies deterministic rules, and records evidence, review incidents, and pending private-report payloads in SQLite. The moderation core has no runtime dependencies and makes no AI or Discord calls. A separate, explicitly invoked preflight utility makes read-only Discord API requests to verify setup.

**Status, September 20, 2026:** the operator supplied the pilot IDs and created the `liberdus-mod` Hermes profile. Its token was moved into that profile, and stock Discord remains disabled in both profiles. The pilot configuration validates locally; the owner-run Discord preflight confirms bot identity and required channel permissions. The owner also verified the installed Hermes interfaces and dependency versions. The optional adapter and default-off, incident-only JEV shadow worker are implemented and tested offline; installation, Portal intent confirmation, and live message/delivery acceptance remain pending. See [live pilot installation and recovery](docs/live-pilot.md), [preflight instructions](docs/preflight.md) and [integration notes](docs/integration.md).

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

1. Install the disabled pilot plugin in the verified Hermes profile and confirm Message Content Intent in the Developer Portal.
2. Activate only the custom moderation platform and complete live tests for ordinary messages, edits, private command authorization, reconnects, and zero-inference private reports.
3. Optionally enable the implemented JEV shadow worker after the baseline test; review its local results before considering report annotations or moderator feedback.
4. Design version-bound approvals and narrowly scoped warnings/single-message deletion before any enforcement. Production expansion requires its own authorization.

The offline work advances part of Phase 5 while Phase 4 integration verification remains open; it does not complete the live phases. Protected-role enforcement, AI-generated rule candidates, review buttons, and action reconciliation remain future work.

## Development and documentation

```bash
python3 -m unittest discover -s tests -v
```

The inactive [CI template](docs/ci-workflow.yml) runs the tests and fixture commands on Python 3.11, 3.12, and 3.13, with read-only repository permissions and no secrets. The 109 core/setup tests pass on Python 3.11.16 and 3.12.3; 23 additional integration tests use the real c1488 Hermes source and discord.py 2.7.1 with network edges mocked. Those integration tests are separate from the CI template and do not constitute a live gateway test; GitHub CI is not enabled yet because the current GitHub CLI login lacks the separate `workflow` scope. Once authorized, move the template to `.github/workflows/ci.yml`. See [operations](docs/operations.md), [recovery](docs/recovery.md), and [integration](docs/integration.md).

[BUILD_PLAN.md](docs/BUILD_PLAN.md) retains the historical planning baseline with dated additions. Section 10.6 records the original JEV proposal; section 10.7 records the selected incident-only shadow implementation. It is disabled until explicitly configured. [JEV instructions](docs/jev.md) cover API keys, budgets, and activation. Its original status statements and local workstation links are preserved; this README and the operations guide describe current implementation status. It also records feature ideas from Sentinel AI, Defender/Warden, Zeppelin, Modcord, and Omnicord. This initial core is independently implemented; no third-party bot source has been imported.

Project licensing has not yet been selected. Keep the repository private pending the owner's publishing and licensing decision.
