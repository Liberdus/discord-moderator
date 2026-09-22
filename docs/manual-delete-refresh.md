# Manual Delete refresh and connection diagnostics — 0.5.9

The reported Delete failure happened because a report referred to historical evidence after a window reset/reconnect. Version 0.5.8 required a current, open incident even for an explicit staff deletion. Version 0.5.9 lets an authorized staff member start a new confirmation from retained source-message references. Deployment completed as hermes on September 22, 2026; see the checkpoint below.

## Delete an older reported message

1. In private bot-mod, click **Delete message(s)** on the report, or use `!mod delete ID REV [MESSAGE_ID]`.
2. The bot fetches the selected messages from Discord and checks today's policy, channel scope and permissions. It shows current text excerpts and clickable original links. Edited messages are marked **CHANGED since saved report**.
3. Review that current content and press **Confirm deletion** within 60 seconds. Only the requesting operator can confirm that specific prompt.
4. The bot fetches the messages again before deletion. An edit, reconnect, pause/resume, policy/action change or scope change invalidates the confirmation. Click Delete again to obtain a new check.

An already absent message gets an explicit response; no new confirmation is created. Missing permissions, unavailable saved references, dismissed/completed evidence and unsupported message content are refused. Up to eight source messages can be selected, but a batch that cannot fit readable previews in one Discord reply must be narrowed with MESSAGE_ID. Text is excerpted and normalized for the portrait display; open the links to review the full messages.

This is a fresh **staff decision**, not a new JEV decision. The original incident revision, saved text and historical classifier result remain intact. The action ledger separately retains the fetched evidence, source revision/hash, fetch time and operator for an attempted deletion. Action evidence follows the existing incident/action retention cascade. `!mod actions ID` reports attempted action outcomes.

Automatic deletion criteria, public scope, staff assessments, Dismiss and timeout rules do not change. In particular, an old model score cannot authorize a new automatic deletion. Discord has no content-conditional delete request, so a narrow race remains between the final fetch and deletion; this patch does not claim to eliminate it. Uncertain attempts are retained and not automatically retried.

## Install from the hermes terminal

The VPS has already completed this upgrade and now runs **0.5.11**; see [the current release](deletion-notices.md). Do not rerun this one-time updater there. The following commands document the completed 0.5.8 to 0.5.9 upgrade.

On the current VPS, the short helper is:

```bash
python3 /tmp/mod-update.py
```

Its versioned equivalent is `/tmp/liberdus-apply-0.5.9-20260922.py`. It verifies the pinned update archive, disables the plugin, waits for the shared gateway restart, installs the code, then enables it and waits for the final restart. This briefly interrupts the shared gateway, including other served profiles. The existing policy, seven monitored channels, action switches, pause state, role exemption, keys and JEV budgets/accounting are preserved. The updater checks the installed runtime import and isolated 9/9 self-test. It backs up the old plugin and stops on failure rather than proceeding to later steps.

In bot-mod afterward:

```text
!mod status
!mod connection
```

Expect version **0.5.9**. Existing action switches retain their values. A previously expired confirmation cannot be reused; click Delete to obtain the fresh preview. The owner has already tested manual deletion in the pilot, so repetitive test-message posting is unnecessary. A real action on an older report remains live verification, not something the automated tests claim to have performed.

## Disconnect investigation

The supplied notices say Discord disconnected and was connected again when the notice was delivered. They do not identify the reason, duration, missed events, or whether the gateway process restarted. The initial developer session could not access the hermes user's logs. The follow-up session ran the filtered diagnostic successfully as hermes; its findings are recorded below.

Run this short command as `hermes`:

```bash
python3 /tmp/mod-check.py
```

It is the same read-only script as `/tmp/liberdus-disconnect-check-20260922.py`, tracked in `scripts/check_disconnects.py`. It reads bounded log tails and recent journal records, then outputs only fixed event labels, times, numeric close codes and whitelisted service properties. It prints no raw logs, message text, tokens or session IDs. Duplicate sources can count an event twice; missing matches cannot prove there was no outage.

The pinned discord.py 2.7.1 client already connects with automatic reconnection enabled. Source inspection shows separate session-resume/new-session callbacks and heartbeat/reconnect diagnostics. Both successful recovery paths intentionally reset moderation coverage and invalidate in-flight actions. We have not established whether these particular outages were server-requested reconnects, network failures, heartbeat stalls or gateway restarts. Do not change VPN/network settings based solely on the notices.

Version 0.5.9 adds `!mod connection`: observed disconnect/resume/new-session counts, the latest recovery duration, an available numeric close code, and a bounded recent UTC event history. Tracking begins with this release; an unknown code stays unknown. Fixed connection metadata is also logged without frames, tokens or message contents. A notice containing only a recovered Discord interruption is now titled **Discord connection restored**. Coverage caveats remain visible; the title does not imply backfill or uninterrupted screening.

Do not turn on global Discord DEBUG logging to diagnose this: the pinned SDK has debug statements that can include raw event data and authentication/resume payloads. Use the filtered diagnostic and new metadata first. A concrete reconnect fix should follow evidence, not a guessed cause.

## Deployment and diagnostic checkpoint — September 22, 2026

The pinned helper completed both shared-gateway restarts and the 0.5.8 to 0.5.9 update. Installed-runtime imports and all nine isolated self-tests passed. All 43 installed release files matched the pinned archive. After GitHub access was configured, all 41 Python modules also matched repository commit `fef4903`. The previous plugin is retained at `/home/hermes/.hermes/profiles/liberdus-mod/.liberdus-update-backup-rphybnzt/liberdus-moderator`.

At 11:26 UTC, the service was active/running and runtime state reported the moderation profile and Telegram connected. Connection tracking recorded one new session at 11:24:39 UTC and no pending disconnect. Both profile configuration files and `moderation.toml` were byte-identical before and after deployment. Saved flags remained: paused OFF, manual deletion ON, automatic deletion ON, timeout OFF and role exemption OFF. JEV remained report_only with $1/day and $4 total budgets; classifier and screening reserved totals were unchanged. No live test deletion, timeout, test message or test JEV request was performed.

Before deployment, retained SDK logs contained 18 new-session and 18 successful-resume entries. The journal showed repeated service restarts on September 21, with several new Discord sessions following them. The service then remained running from September 21 at 22:50:32 UTC until deployment, while Discord successfully resumed on September 22 at 00:53:15, 03:35:11, 07:20:44, 09:19:42 and 10:25:45 UTC. Service restarts therefore explain some new sessions, but not those later resume events.

The filtered logs contained no matching heartbeat-block/timeout, socket-close/error, invalid-session or server-reconnect labels. This does not establish their absence or identify a cause. The service restart counter increased from 37 to 39 during the two planned deployment restarts, so it must not be treated as a crash count. Causes of the earlier restarts and Discord-only interruptions remain unresolved. Next, correlate a naturally occurring interruption's new connection metadata with service/log timestamps; no repeated deployment or artificial live interruption is needed.

## Validation

**621 tests passed:** 430 core/setup and 191 pinned Hermes/Discord runtime integration tests. Added regressions cover old-report clicks, current-content previews, changed content, source immutability, separate action evidence, actor/message binding, single-use confirmations, pause/resume and reconnect invalidation, expiry, action-off/dismissal guards, missing messages/permissions, scope moves, Unicode/fence handling, resumed versus new sessions, bounded diagnostic metadata, filtered-log output and stopped-upgrade state preservation.

The runtime tests use mocked Discord transport. No real message deletion, timeout, JEV call, owner-profile read or gateway restart was performed from the developer account. The archive is separately checked for integrity, compilation and equality with tested source. Previous release artifacts remain unchanged.

Release SHA-256 values:

- `/tmp/liberdus-update-0.5.9-20260922.pyz`: `874fef95b2be89fb209e774ed120a5473cc41e4c78c380ae9713b442365d7dbc`
- `/tmp/liberdus-apply-0.5.9-20260922.py` and `/tmp/mod-update.py`: `7942871d4b29fd856a33bb1e613d44cb72a196d121341f793a6600e0f277250d`
- `/tmp/mod-check.py`: `a53121afbaddcc7c1bf5f2f112168341a1ee1798b79f9b28a72f8d03487136de`
