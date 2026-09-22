Latest prepared: **0.5.9** fixes staff Delete on historical reports by fetching current Discord content for a new confirmation. It also adds `!mod connection` and filtered reconnect diagnostics. [Install and behavior](docs/manual-delete-refresh.md) · [Continue as hermes in Codex CLI](docs/HERMES_HANDOFF.md). Deployment is pending; the reported disconnect cause still needs owner log evidence. Existing scope and action settings are preserved.

# Liberdus Discord Moderator

**Automated JEV screening evaluation:** a separate 44-case runner checks harmful and benign messages, including false automatic-deletion candidates, using the production decision rules. No Discord actions or manual test messages. See [run the evaluation](docs/screening-evaluation.md). The owner baseline returned 43/44 valid responses, with zero benign or ambiguous auto-delete candidates. The separate focused retry succeeded, completing valid results for all 44 unique cases while preserving the original baseline and its failed attempt.

**0.4.1 prepared:** [JEV role exemption](docs/role-exemption.md) for `1302455329795342377`, with observed membership, a separate exemption counter and unchanged repetition checks. The owner has now confirmed 0.4.0 single-message reporting and staff review; live exemption activation remains pending.

A **moderation detection core** with an optional, explicitly enabled Hermes Discord pilot plugin. It accepts synthetic Discord-shaped events, applies deterministic rules, and records evidence, review incidents, and pending private-report payloads in SQLite. The moderation core has no runtime dependencies and makes no AI or Discord calls. A separate, explicitly invoked preflight utility makes read-only Discord API requests to verify setup.

**Status, September 20, 2026:** the owner has verified live cross-channel and same-channel reports, edit withdrawal, deletion resets, pause/resume, wrong-channel command rejection, ignored DMs, and paused-state recovery across a gateway restart followed by a fresh report. The installed pilot also returned `!mod selftest` **9/9 passed**; these are separate synthetic checks. A live command from another, unauthorized member was explicitly deferred. See the [live evidence and remaining checks](docs/live-pilot.md#remaining-code-only-pilot-checks).

**Initial JEV shadow trial complete: three examples matched.** Announcement, promotion and quoted warning all returned `ok` with their intended labels; recorded latencies were 275, 318 and 375 ms. Accounting records three attempts. This verifies the initial examples and live integration, not general accuracy or calibrated confidence. See the [trial checkpoint](docs/jev.md#initial-trial-complete). The owner confirmed the **0.3.2** private saved-JEV lookup, including historical-evidence status. Version **0.3.3** formats `!mod incident` / `!mod explain` replies as a narrow ASCII panel with copyable IDs. The owner has supplied the new formatted reply, confirming the [0.3.3 live incident display](docs/incident-review.md). That completed 0.3.3 check needs no repetition. At that checkpoint automatic reports were rule-based. Enforcement stays disabled and installation defaults remain off; the opt-in 0.4.0 screening change is described below.

**JEV baseline completed:** the owner supplied ten valid provider responses, nine matching the initial expected labels. The mixed announcement/sales-pitch example returned promotion instead of unclear and remains a review case. Recorded latency averaged 317.4 ms; the batch token estimate was $0.000232, distinct from its $0.027530 budget reservation. Shared accounting reports 13 attempts. See the [baseline results and review](docs/jev-batch.md#baseline-results--september-20-2026); no repeat run is needed.

**Focused JEV comparison completed:** `precedence-v2` returned five valid responses and four expected-label matches. The ambiguous forwarded fragment returned promotion at displayed score 0.65 instead of unclear and remains a review case. Recorded latency averaged 296.8 ms; shared accounting now reports 18 calls. See the [candidate results](docs/jev-batch.md#precedence-v2-results--september-20-2026). The candidate remains experimental; the live classifier still uses its original rubric. The completed comparison needs no repeat run. The later staff workflow and new single-message screening have separate deployment records below.

**0.3.5 staff workflow confirmed live — September 21, 2026:** Needs attention remained pending; Looks okay completed the review and removed the incident from the queue (8 → 7). The owner supplied the updated display, reviewer, private confirmation and pending list. [Staff-review behavior](docs/staff-assessment.md) remains available; this completed check needs no repeat installation.

**0.4.0 prepared — single-message JEV reports:** an explicit `classifier.mode = "report_only"` screens eligible human text in the approved test channels, using one purpose/concern request per message version. Specific concerns create private incidents with saved text, links and staff buttons; ordinary promotions, warnings and none/unclear results are not automatically violations. Deterministic spam detection stays independent. The approved trial uses separate $1/day and $4 lifetime accounting, with validated usage settlement and conservative reservations for unknown charges. Follow [the owner-run screening update and first tests](docs/message-screening.md). The owner later confirmed single-message reporting, including a sensitive-request incident; 0.5.0 action installation was subsequently confirmed; see the 0.5.1 fix above.

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
- Local moderator-command fixtures requiring both an approved command channel and an authorized operator. `pause` clears the active message window and invalidates open incidents while retaining history; `resume` starts a fresh window. `explain` shows saved incident evidence. Generic enforcement approval is unsupported; explicitly enabled deletion and staff timeouts use bound confirmations.
- Private staff assessment buttons, visible report updates, a paginated pending-review list, bounded audit history and saved evidence links. Completion tracks the assessed evidence; rule/JEV decisions and enforcement remain unchanged.
- In-memory rule simulation and automated tests, without model credentials or Discord access.
- Optional JEV shadow evaluations for existing incidents only, with typed results, budget reservations, one attempt per incident, bounded async I/O, and profile-local secrets. Saved labels can be inspected through authorized private incident commands, with current/historical evidence status and no new AI call. Off is the default.

A `no_match` decision means **no implemented code rule matched**. It does not claim that a message is safe or that AI reviewed it. Blocked-domain matches are review candidates even when context might make a link legitimate. Reports observe only the configured channels and supported content.

The CLI prepares report payloads without sending. The optional live adapter records delivery attempts and Discord message IDs; uncertain attempts are never automatically retried. The 0.5.0 adapter supports opt-in, evidence-bound message deletion and staff-confirmed 10-minute timeouts. Public replies, bans, kicks and role changes remain unimplemented. Existing policies keep actions disabled until the explicit owner migration; runtime action switches then default OFF. Optional JEV shadow evaluation uses schema 2, an explicit AI opt-in, and finite budgets; it never changes Discord reports. See [JEV setup and integration research](docs/jev.md). Private Discord logs are optional in the plan; durable local incident records are independent of them.

## Next milestones

1. Install 0.5.0 and complete the [scoped deletion, dismissal and staff-timeout live checks](docs/moderation-actions.md#first-live-checks). Existing successful reporting checks do not need to be repeated.
2. Collect staff judgments on false positives before expanding eligible auto-delete categories or confidence policies. Automatic account penalties are not implemented.
3. Revisit the deferred unauthorized-member live check and remaining checks in the [pilot checklist](docs/live-pilot.md#remaining-code-only-pilot-checks).
4. Consider a separately approved public-channel pilot after private observation. The present action scope remains the private test channels; a staff timeout, if explicitly enabled and confirmed, affects the member server-wide.

## Development and documentation

```bash
python3 -m unittest discover -s tests -v
```

The inactive [CI template](docs/ci-workflow.yml) runs the tests and fixture commands on Python 3.11, 3.12, and 3.13, with read-only repository permissions and no secrets. The 235 core/setup tests pass on Python 3.11.16; 80 additional integration tests use the real c1488 Hermes source and discord.py 2.7.1 with network edges mocked. Those integration tests are separate from the CI template and do not constitute a live gateway test; GitHub CI is not enabled yet because the current GitHub CLI login lacks the separate `workflow` scope. Once authorized, move the template to `.github/workflows/ci.yml`. See [operations](docs/operations.md), [recovery](docs/recovery.md), and [integration](docs/integration.md).

[BUILD_PLAN.md](docs/BUILD_PLAN.md) retains the historical planning baseline with dated additions. Section 10.6 records the original JEV proposal; section 10.7 records the selected incident-only shadow implementation. Section 10.8 records the earlier deferral; section 10.13 records the subsequent live checks and trial authorization; sections 10.14–10.16 record the announcement, promotion and quoted-warning results and completion of the initial three-case trial. Section 10.17 records the private saved-result lookup implementation; section 10.18 records the lookup confirmation and formatting implementation; section 10.19 records the owner's live formatted reply; section 10.20 records the standalone JEV batch runner; section 10.21 records the completed baseline and mixed-purpose review; section 10.22 records the selected candidate rubric and focused comparison; section 10.23 records its completed results and unresolved fragment; section 10.24 records the original content-review buttons and saved message evidence; section 10.25 records the staff assessment workflow and revised display. [JEV instructions](docs/jev.md) cover API keys, budgets, and activation. Its original status statements and local workstation links are preserved; the latest release note and moderation-actions guide describe current action behavior. It also records feature ideas from Sentinel AI, Defender/Warden, Zeppelin, Modcord, and Omnicord. This initial core is independently implemented; no third-party bot source has been imported.

Project licensing has not yet been selected. Keep the repository private pending the owner's publishing and licensing decision.
