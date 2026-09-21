> **0.5.1 update:** the owner installed 0.5.0 and enabled deletion/auto-delete, but the preflight comparison falsely reported changed content. Use [the deletion fix](deletion-fix.md) to update while preserving current flags and budgets. The initial installation procedure below is retained for reference.

# Moderation actions — 0.5.0

Prepared September 21, 2026. This release adds message deletion, Dismiss, and staff-confirmed 10-minute timeouts. Automatic deletion is limited to fresh JEV `sensitive_request` results above **0.90**. There are no automatic timeouts, bans, kicks, escalating strikes, public warnings or DMs. No running profile was changed during development.

## Install from the existing `hermes` terminal

```bash
python3 /tmp/liberdus-apply-0.5.0-20260921.py
```

The helper verifies the staged hashes, disables the custom platform, waits for a healthy shared-gateway restart, installs 0.5.0, retains the requested JEV exempt role, enables `actions_enabled = true` in the scoped policy, and restarts with the platform enabled. It stops after any failure. It can upgrade the existing 0.4.0–0.4.2 single-message screening pilot directly. Credentials, model rubric, budgets, counters, evidence and assessment history are preserved. The explicit policy change makes old detections historical; use a **new** test incident for actions. Paused moderation remains paused.

Deletion, auto-delete and timeout are separate persisted switches; each defaults **OFF**. Installation does not send Discord messages, run paid evaluations, delete content or time out members. The default shared gateway serves other profiles too, so its two restarts also reconnect those adapters.

## Discord permissions

For `Liberdus Moderator`:

1. Allow **Manage Messages** in `bot-test-1`, `bot-test-2` and `bot-test-3`. Keep existing View Channel and Read Message History access. Avoid granting this permission across public channels.
2. To test timeout, grant the bot role **Moderate Members / Timeout Members** at the server level and put it above the ordinary test member's highest role. Keep it below staff roles. Administrator must remain OFF.
3. Keep bot-mod private and the existing send/history permissions intact.

Deleting another member's message requires Manage Messages. Timeouts require Moderate Members and affect the member across the server, even when the evidence originated in a private test channel. [Discord message API](https://docs.discord.com/developers/resources/message#delete-message), [Discord member API](https://docs.discord.com/developers/resources/guild#modify-guild-member), [timeout FAQ](https://support.discord.com/hc/en-us/articles/4413305239191-Time-Out-FAQ).

The plugin itself still accepts action evidence only from the three configured private test channels. Timeout targets cannot be the server owner, bot accounts, configured operators, members with administrative/moderation permissions, members at/above the bot's role, or members with a configured JEV exempt role. That timeout protection remains active even if `!mod exempt-role off` allows those members' text to be screened. Use a consenting ordinary test member for a timeout test, not the operator account. Staff can remove an existing timeout through Discord's member context menu.

## Controls in bot-mod

Only the existing authorized operator can use these commands or buttons.

| Command | Meaning |
| --- | --- |
| `!mod deletion` | Show current switches. |
| `!mod deletion on` / `off` | Permit/block manual and automatic message deletion. |
| `!mod auto-delete on` / `off` | Permit/block the narrow automatic-deletion rule; deletion must also be ON. |
| `!mod timeout on` / `off` | Permit/block staff-confirmed 10-minute timeouts. OFF does not remove an existing timeout. |
| `!mod delete ID REV` | Prepare deletion of the exact evidence messages shown in the confirmation (at most 8). |
| `!mod delete ID REV MESSAGE_ID` | Select one evidence message from a larger incident. |
| `!mod timeout ID REV` | Prepare a server-wide 10-minute timeout for the incident's author. |
| `!mod dismiss ID REV` | Close staff review for that evidence without acting on the member or messages. |
| `!mod actions ID` | Read the last 8 action attempts, targets, operators, revisions, times and outcomes. |
| `!mod pause` | Pause detection and prevent actions; current evidence becomes historical. |
| `!mod help` / `status` | Show usage and switches. |

Settings survive restarts. A policy-level `actions_enabled = false` overrides all action switches. The existing detection mode remains `report_only`; the separate ACTIONS status section explicitly shows whether deletion and staff timeout capabilities are enabled. The role-exemption switch still affects JEV only; repetition checks remain active.

## Buttons and confirmations

New incident views show the original assessment buttons plus **Delete message(s)**, **Dismiss**, and **Timeout 10 min**. Open `!mod incident ID` after enabling a feature to get refreshed buttons. Old messages are refreshed on review/action when possible; turning a switch on doesn't rewrite every old message.

Delete and Timeout first produce a confirmation listing the exact member, incident revision and source links. Only the requesting operator can confirm, in the same private channel, within 60 seconds. Each confirmation is single-use and expires on restart. A button request uses an ephemeral confirmation; a typed command creates a bound confirmation in bot-mod. Deletion cannot be undone. An unchanged active timeout is never shortened, replaced or extended. Timeout has a one-attempt-per-incident guard and a 10-minute member cooldown for successful/uncertain attempts.

**Dismiss** records a separate `Dismissed` assessment and completes review of those saved contents. It does not whitelist future messages, delete content, undo a prior deletion, lift a timeout, or change model scores. Assessment buttons still record assessments only. A dismissed or Looks okay incident is ineligible for a pending action while that assessment applies. Changed evidence needs a new review.

## Automatic deletion

All of the following must hold:

- Policy action capability, deletion, and auto-delete are enabled; moderation is unpaused.
- A newly applied JEV screening result concerns exactly one supported, in-scope human message.
- Concern is `sensitive_request`, score is strictly greater than 0.90, and purpose is neither `quoted_warning` nor `unclear`.
- The result is current and no more than 60 seconds old; role exemption does not apply.
- The matching staff report has been delivered; evidence has not been dismissed.
- The bot refetches and validates the message and its permissions before deletion.

Other concerns, low scores and historical results remain staff reports. Turning auto-delete ON does not scan or delete a backlog. Attempts are capped at five automatic deletions per minute. The score is not a calibrated probability of correctness. Repetition incidents never cause automatic timeouts or automatic deletion in this release.

## Action records and limits

Every mutation attempt is reserved durably before the API call. Confirmed success, permission denial, already-absent targets and uncertain outcomes are recorded. Repeated clicks cannot repeat an action. An interrupted request becomes uncertain after restart and is not automatically replayed. A partial deletion batch stops on failure and records each attempted message separately. Permission/identity/content failures before a mutation produce a private refusal, not a success record. `!mod actions ID` is authoritative when refreshing a report fails.

Before acting, the adapter rechecks the active on-disk profile/policy, pause and feature switches, incident revision, source identity/content/version, channel scope/privacy and relevant permissions. Incoming evidence cancels a pending action; already-arrived staff controls survive a deletion's conservative coverage reset. Staff evidence links and audit records follow the existing retention policy.

Discord offers no conditional "delete only if content still matches" operation, so a narrow race remains between the last fetch and mutation. Timeouts also cannot be made atomically conditional on an unchanged membership record. Already-sent API calls cannot be recalled by switching a feature off. The plugin records uncertain results rather than retrying them. A deletion resets the active evidence window conservatively; further pending results can become historical and are not automatically replayed.

## First live checks

1. Run `!mod status` and `!mod help`; confirm all new action switches are OFF.
2. After granting scoped Manage Messages, run `!mod deletion on`. Create a fresh flagged test message, open its incident and click Delete; verify the confirmation targets the right message, then confirm. Check the message is gone and `!mod actions ID` says `done`. Repeat the confirmation; it must not repeat the deletion.
3. Create a fresh report and Dismiss it. Verify the source remains and the report leaves `!mod pending`.
4. Run `!mod auto-delete on`. A fresh nonexempt human message asking for a recovery phrase is eligible only if the current JEV result meets the rule. Check the staff report and saved action record. Lower scores/other concerns must remain review-only.
5. After granting Moderate Members, run `!mod timeout on`. With an ordinary consenting test member's fresh incident, click Timeout 10 min and confirm the server-wide effect. Verify expiry/reason in Discord. Switch `!mod timeout off`; a new timeout must be refused. Remove the test timeout through Discord if desired.
6. Leave any feature OFF when not wanted. No extra JEV call is made by buttons, flags, or history commands.

These live checks are pending. Development validation passed **235 core/setup tests** on Python 3.11 and **80 integration tests** with pinned Hermes c1488 / discord.py 2.7.1 and mocked network edges. The new action tests cover authorization, typed-result thresholds, durable confirmation bindings, stale edits, partial/uncertain outcomes, permission gates, protected roles, restart recovery and incoming off commands. No real deletion or timeout was tested by the developer session.

## Artifacts and recovery

- `/tmp/liberdus-update-0.5.0-20260921.pyz` — SHA256 `0af22e8095d9d11dc9f96cc90e2215a1b1fec5390735b4ea6f4af2adf4030501`
- `/tmp/liberdus-actions-20260921.pyz` — SHA256 `2d9368ec73d88dd08dfc5917f920a4739c627f43f35ef077724c0983934c0311`
- `/tmp/liberdus-apply-0.5.0-20260921.py` — identical to `scripts/apply_actions_update.py`.

Keep the printed code and policy backups. For rollback, disable the platform and finish the restart first, then restore compatible code and its prior policy together. Do not reset the moderation database or budget counters. Older releases may display the new dismissal label as unavailable; action evidence remains retained in the database. Rolling back cannot restore deleted Discord messages or remove an already-applied timeout.
