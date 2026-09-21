# Scoped public deletion — 0.5.8

The owner reports public JEV screening is working and requested deletion in the seven approved Community channels. Version 0.5.7 deliberately required observation-only public policies, so the existing `!mod deletion on` command was refused. Version 0.5.8 adds an explicit `allow_public_deletion` opt-in alongside `actions_enabled`, retaining ordinary observation as the default. Installation on the owner gateway is not yet confirmed.

The fixed owner helper accepts only the active rollout containing these seven channel IDs. It never expands monitoring:

| Community channel | ID |
| --- | --- |
| developers | 1293238000313958451 |
| privacy-news | 1318757883260964884 |
| self-intro | 1453063291961212959 |
| general | 1479253446258458644 |
| feedback | 1486610685398876170 |
| incoming | 746426388050870282 |
| help | 746499160823431199 |

The included categories remain `746426387606274201` and `746426387606274202`. Committers `1318586868136415333` remains excluded. New channels do not join automatically; private or unsupported channels are not eligible. Commands and reports remain in private bot-mod. Moving a monitored channel outside the included categories, into an excluded category, or changing its public visibility/type/read permissions prevents actions against it.

## Owner installation

1. In Discord, allow **Manage Messages** for the Liberdus Moderator bot member in each of the seven monitored channels, alongside existing View Channel and Read Message History. Administrator is not needed. Manage Messages is required to delete other users' messages: [Discord Delete Message](https://docs.discord.com/developers/resources/message#delete-message).
2. From the existing `hermes` terminal, run:

   ```bash
   python3 /tmp/liberdus-apply-deletion-0.5.8-20260921.py
   ```

3. After the helper confirms success, enable in private bot-mod:

   ```text
   !mod deletion on
   !mod auto-delete on
   !mod status
   ```

Expect version **0.5.8**, **Policy: deletion only**, deletion ON, auto-delete ON and staff timeout OFF. The existing `report_only` detection label remains; the separate ACTIONS section describes enforcement. Pause is preserved; if paused, use `!mod resume` when ready.

The helper verifies pinned bundle hashes, the existing seven-channel policy and fresh Discord metadata before stopping anything. Missing Manage Messages is reported with affected channel IDs. It then disables moderation, completes the shared gateway restart, installs 0.5.8, rechecks scope and permissions while holding the stopped moderation lock, backs up policy/database, allows public deletion with all runtime switches OFF, and restarts. Existing keys, JEV settings, costs, budgets, role exemptions and pause state are preserved. Scope does not change. No messages are deleted by the helper; the two bot-mod commands enable future deletion.

If a step fails, later steps do not run. A failure after the first restart may leave moderation disabled; inspect the printed output before proceeding. The restrictive flags commit before the policy change. The updater retains the old plugin, and the policy helper retains policy/database backups. Do not rerun the one-time updater after a partial install without checking the installed version and which step failed. Reverting a policy/code pair cannot restore deleted messages; keep the live database and accounting intact.

Optional read-only preflight, without stopping the bot:

```bash
python3 /tmp/liberdus-public-deletion-20260921.pyz verify
```

## What deletion does

The existing automatic rule is unchanged. Only a newly screened, current single message classified as `sensitive_request` with a concern score **strictly above 0.90** is eligible. A quoted warning or unclear purpose is excluded. Roles, pause state, private report delivery, unchanged evidence, fresh message fetch, current channel permissions and action limits are still checked. A score is not an accuracy guarantee. Other JEV concerns and repeated-message incidents remain available for staff review and confirmed manual deletion.

Manual **Delete message(s)** still requires the existing operator-bound, single-use confirmation of exact evidence. Automatic deletion is limited to five attempts per minute, with durable action records and no automatic retry of uncertain attempts. Turning it on does not process an old backlog. The policy change makes previous evidence historical; actions require fresh current evidence. Staff assessments and Dismiss keep their existing meanings.

**Timeouts are blocked by the public policy**, including the command, buttons, proposals and transport revalidation. A persisted timeout flag cannot bypass this. Private-pilot timeout behavior remains unchanged. No bans, kicks or automatic account penalties are introduced.

To stop only automatic deletion:

```text
!mod auto-delete off
```

To stop manual and automatic deletion while keeping screening:

```text
!mod deletion off
```

Inspect attempted actions with `!mod actions ID`. Discord does not offer a conditional delete tied to the fetched content version, so the pre-existing narrow race between the last validation and the request remains.

## Validation

597 automated tests passed: 415 core/setup and 182 against the pinned Hermes/Discord runtime. New checks cover explicit opt-in, policy hashes, scoped Manage Messages, unchanged seven-channel scope, backups, stopped locks, failed writes, installation ordering, timeout rejection despite stale flags, and actual SDK delete method calls with HTTP mocked. Additional targeted checks passed after refining public-policy display text. No real Discord deletion, timeout, JEV request, owner secret access or gateway restart occurred in development. The live install and public deletion outcome remain owner verification steps.

The updater retains its isolated 9/9 synthetic self-test. Bundles are checked for integrity, compilation and equality to tested source. Earlier versioned artifacts are preserved. Older setup/evaluation archives predate this public-deletion field; do not use them to rewrite the current policy. The 0.5.7 observation guide remains historical; this explicitly authorized opt-in supersedes its action prohibition for these seven channels only.

Release artifact SHA-256 values:

- `liberdus-update-0.5.8-20260921.pyz`: `5ed37995ce30b44f2fee1d21f66b8bfa3a33471bd139983a5d334a90f7c2d9de`
- `liberdus-public-deletion-20260921.pyz`: `5b1f52b50916b60b5f37551f7531d75c1e23e56695b44a16674b6449a270a0ab`
- `liberdus-apply-deletion-0.5.8-20260921.py`: `457ac576ab020cf375017c99a6a688419832fa7e57e33ae61976ab44fe142409`
