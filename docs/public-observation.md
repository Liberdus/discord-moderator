> **0.8.0 update:** [Bounded message catch-up](catch-up.md) now covers recent new messages after disconnects and unexpected restarts. Recovered flags require staff review. Earlier no-backfill descriptions below are historical.

> **Current 0.5.13 exception:** the configured private bot-mod channel may be inside Committers for commands/reports. Other Committers channels remain excluded from monitoring. See [staff-channel placement](staff-channel-category.md). The original rollout rules below are historical.

> **0.5.8 public-deletion opt-in:** the owner requested deletion in the seven approved Community channels. See [installation and controls](public-deletion.md). The historical observation/action instructions below describe their original releases.

# Public-channel observation rollout — 0.5.7 prepared

The owner narrowed observation to public text channels in categories **`746426387606274202`** and **`746426387606274201`**, skipping the staff guide/private-pilot/feedback prerequisites listed as items 1–3 in the previous plan. The rollout excludes **Committers**, category **`1318586868136415333`**, and disables **all Discord moderation actions**. Reports, staff assessments and operational notices stay in private `bot-mod` (`1551252553331642558`).

The code and rollout tools are prepared. The developer workspace has not inspected the owner's live channel inventory, applied this policy or restarted the owner gateway. Public observation is **not confirmed active**. Installing 0.5.7 code alone preserves the existing private pilot; the separate saved-plan application changes scope and switches actions off.

## Scope of this rollout

Selection uses the actual channel metadata returned by Discord, with these explicit boundaries:

| Channel or setting | Treatment |
| --- | --- |
| Ordinary guild text channel, type 0, visible to `@everyone` | Included only inside the two approved categories, outside excluded categories, and if the bot has View Channel and Read Message History without Administrator. |
| Channels outside the two approved categories | Omitted; missing read permissions there do not block the plan. |
| Committers category and its channels | Always excluded by ID, independent of names. |
| Additional excluded categories | Can be added by category ID when generating the plan. |
| Role-gated channel not visible to `@everyone` | Omitted as audience unverified. The tool does not assume every role-gated channel is public or safe to include. |
| `bot-mod` | Must remain private, readable and writable by the bot, outside exclusions. It is the command/report destination, not a monitored public channel. |
| Previous `bot-test-1`, `bot-test-2`, `bot-test-3` | Omitted from the new monitored list. Saved test history remains subject to normal retention. |
| Threads, forums, announcement channels and other channel types | Unsupported by this rollout. Non-text types returned by inventory are listed as omitted. Threads are not enumerated by this endpoint. |
| New channels created later | Not automatically added. A new plan and deliberate application are required. |

Discord's Get Guild Channels endpoint returns channel metadata and explicitly excludes threads. The inventory describes what that endpoint returns; it is not proof of full-server coverage. See [Discord Get Guild Channels](https://docs.discord.com/developers/resources/guild#get-guild-channels).

If an otherwise eligible public text channel inside the approved categories lacks the bot's read permissions, or grants Administrator, plan generation stops instead of silently claiming complete selection. Missing Committers metadata, invalid category IDs or an unsafe `bot-mod` destination also stop preparation. The inventory does not read message history, post messages, alter Discord permissions or call JEV.

## First: inventory or save the plan

Run these as the existing `hermes` user. To inspect returned channel/category IDs and permissions without saving a rollout plan:

```bash
python3 /tmp/liberdus-category-rollout-20260921.pyz inventory
```

To select eligible channels and save a plan:

```bash
python3 /tmp/liberdus-category-rollout-20260921.pyz plan
```

Committers is excluded automatically. To add another category exclusion, replace the placeholder with its numeric ID:

```bash
python3 /tmp/liberdus-category-rollout-20260921.pyz \
  plan --exclude-category ADDITIONAL_CATEGORY_ID
```

The option can be repeated. No token should be pasted into a command or shared; the owner-run tool reads the existing profile token for metadata GET requests and does not print it.

The plan output lists selected channels, omitted channels and reasons, exclusions, the selected count and `active_policy_changed: false`. Inspect or share that output before applying: the concrete channel names/IDs are not yet known in the developer workspace. Unexpected omissions need audience or permission clarification, not an assumption that the bot covers them.

The saved plan is private to the owner profile and expires after **24 hours**. It binds the current policy hash, selected channel IDs and parent category IDs, and explicit category inclusions/exclusions. This version requires a fresh category plan; earlier broad plans cannot be applied. Changed scope/categories, a changed baseline policy or an expired plan require a fresh plan. A channel rename alone does not redefine the ID-based scope.

## Then: install and apply the selected scope

After the selected and omitted list matches the intended audience, run:

```bash
python3 /tmp/liberdus-apply-categories-0.5.7-20260921.py
```

The owner helper verifies the pinned artifacts and rechecks the saved plan against current metadata **before** disabling anything. It then disables the plugin, waits for the shared gateway restart, installs the reviewed 0.5.7 code, applies the plan while moderation is stopped, and enables/restarts the gateway. Apply rechecks metadata/policy and requires the stopped moderation lock. Failures stop later steps; do not interpret a partial helper run as an active rollout.

The code update retains a plugin backup. Applying scope backs up the policy and database, then sets:

```text
Policy actions_enabled: false
Deletion flag:          OFF
Auto-delete flag:       OFF
Timeout flag:           OFF
```

The restrictive runtime flags commit before the policy change. If a later write fails, the helper leaves the platform stopped with actions off rather than continuing activation. The public-monitoring Config also rejects `actions_enabled = true`; ordinary `!mod ... on` controls cannot override that disabled policy. Staff assessments and Dismiss remain review records, with no Discord deletion or account penalty.

The new policy enables only the explicit selected text-channel IDs, records included and excluded category IDs, and drops obsolete crosspost exceptions referring outside the new scope. It preserves the private command/operator list, JEV mode/rubric, role-exemption setting, pause state, credentials, existing usage accounting and spending caps. The current trial allowance remains $1/day and $4 total unless separately changed; public traffic still gets best-effort screening within those existing limits.

This is an observation rollout: no automatic or staff-triggered deletion, timeout, ban or kick. The bot does not post in monitored public channels. Only authorized private commands, reports, assessment replies and health notices are sent to `bot-mod`.

## Verify the resulting state

After the helper completes, use private `bot-mod`:

```text
!mod status
!mod summary
```

Expect version **0.5.7**, action policy disabled, deletion OFF, auto-delete OFF and staff timeout OFF. The mode remains paused if it was paused before installation; pause is deliberately preserved. The apply result records the exact selected channels and exclusions. No new manual spam or deletion test is required to install observation mode.

The adapter checks current cached channel type, category, visibility and bot permissions before admitting monitored events and before JEV requests/results. Moving a monitored channel outside either approved category (including removing its category), into Committers or another excluded category, making it private, losing necessary permissions or losing required cache metadata excludes it. Observed scope changes invalidate queued/in-flight evidence and pending work. These are checks against received Discord metadata, not an atomic guarantee against a permission change that has not yet reached the gateway.

New channels do not enter scope automatically. Threads and unsupported channel types remain excluded. There is no history backfill. Existing role exemptions, text/attachment limitations, queue/rate limits and usage caps still limit screening. [Private health notices and summary](health-and-summary.md) explain the recorded coverage and cost limitations; absence of a report does not certify every message was checked.

## Release verification and remaining work

The updater's isolated smoke check includes the public-scope Config interfaces, existing health/summary imports and 9/9 synthetic self-test. Stopped-upgrade tests preserve the previous policy, keys, database, flags, pause state and accounting; the explicit rollout step is what changes scope and disables actions.

- **576 automated tests passed:** 401 core/setup and 175 integration tests against the pinned Hermes/Discord runtime. Coverage includes public/private/category changes, queued and in-flight evidence, metadata-only planning, policy changes during preflight, stopped deployment, backups and restrictive failure handling.
- Both bundles passed archive integrity and module compilation checks; bundled runtime copies match tested source byte-for-byte. The rollout CLI help works without accessing a profile. The owner helper pins both bundle hashes.
- `liberdus-update-0.5.7-20260921.pyz` SHA-256: `17a731c307b486b39be0e6ff465b6cd5cd2cf93ce08b960c994db53b6d89f8cf`.
- `liberdus-category-rollout-20260921.pyz` SHA-256: `6880bb50753445dc39b6543371f38d824f24309727076e7bae2811994941e951`.
- `liberdus-apply-categories-0.5.7-20260921.py` SHA-256: `5f42e9a15316929470a733b0c825a0a457567beadd316ac461c852ede763d200`.

Owner inventory/plan output, exact selected coverage and completed rollout remain unverified until supplied. Keep all actions disabled during public observation. Any later enforcement or support for role-gated audiences/threads/other channel types needs a separate explicit scope and policy change. Earlier release artifacts remain preserved. Earlier setup/evaluation archives predate the public-scope fields and target the private pilot; use tooling from this release or a later reviewed build for subsequent setup/evaluation. The versioned installation helper is for the initial upgrade; later scope-only changes need a fresh plan applied with the existing plugin stopped, without rerunning the one-time code updater.
