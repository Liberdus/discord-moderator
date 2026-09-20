# Liberdus Discord Moderator

An initial **offline, report-only moderation core** for a planned Hermes plugin. It accepts synthetic Discord-shaped events, applies deterministic rules, and records evidence, review incidents, and pending private-report payloads in SQLite. The moderation core has no runtime dependencies and makes no AI or Discord calls. A separate, explicitly invoked preflight utility makes read-only Discord API requests to verify setup.

**Status, September 20, 2026:** the operator supplied the pilot IDs and created the `liberdus-mod` Hermes profile. Its token was moved into that profile, and stock Discord remains disabled in both profiles. The pilot configuration validates locally; the owner-run Discord preflight confirms bot identity and required channel permissions. Intents, event ingestion, and report delivery remain unverified. See [preflight instructions](docs/preflight.md) and [integration notes](docs/integration.md).

## Current flow

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
| No Discord delivery yet      |
+------------------------------+
```

The future Hermes adapter will supply events and deliver approved private reports through the existing bot connection. Public message content is evidence, not authority to change policy or run tools.

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

A `no_match` decision means **no implemented code rule matched**. It does not claim that a message is safe or that AI reviewed it. Blocked-domain matches are review candidates even when context might make a link legitimate. Reports observe only the configured channels and supported content.

The report outbox contains prepared payloads only: no sender, delivery confirmation, public replies, deletions, bans, kicks, timeouts, or role changes exist in this version. AI and action configuration must remain disabled. Private Discord logs are optional in the plan; durable local incident records are independent of them.

## Next milestones

1. Verify the installed Hermes interfaces, including unmentioned messages, cached/uncached edits, private command identity, and direct private delivery with zero inference.
2. Connect the core through the verified ingress path and evaluate it in approved test channels in report-only mode.
3. Add bounded contextual AI, usage accounting, and moderator feedback after the transport and core are proven.
4. Design version-bound approvals and narrowly scoped warnings/single-message deletion before any enforcement. Production expansion requires its own authorization.

The offline work advances part of Phase 5 while Phase 4 integration verification remains open; it does not complete the live phases. Protected-role enforcement, AI-generated rule candidates, review buttons, and action reconciliation remain future work.

## Development and documentation

```bash
python3 -m unittest discover -s tests -v
```

The inactive [CI template](docs/ci-workflow.yml) runs the tests and fixture commands on Python 3.11, 3.12, and 3.13, with read-only repository permissions and no secrets. All 81 local tests passed on Python 3.11.16 and 3.12.3; GitHub CI is not enabled yet because the current GitHub CLI login lacks the separate `workflow` scope. Once authorized, move the template to `.github/workflows/ci.yml`. See [operations](docs/operations.md), [recovery](docs/recovery.md), and [integration](docs/integration.md).

[BUILD_PLAN.md](docs/BUILD_PLAN.md) is a verbatim historical planning baseline. Its original status statements and local workstation links are preserved; this README and the operations guide describe current implementation status. It also records feature ideas from Sentinel AI, Defender/Warden, Zeppelin, Modcord, and Omnicord. This initial core is independently implemented; no third-party bot source has been imported.

Project licensing has not yet been selected. Keep the repository private pending the owner's publishing and licensing decision.
