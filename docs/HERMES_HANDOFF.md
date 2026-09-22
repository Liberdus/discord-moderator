# Continue Liberdus moderation work as hermes

Latest checkpoint: **0.7.1 Committers review alerts**. The owner chose @committers
for new flagged reviews below 0.90. This release adds opt-in `/mod config alert-role`
with role selector/raw ID and Off. Follow `docs/review-alerts.md` after the normal
VPS wheel update: select the real Committers role in bot-mod; its ID was not
provided and no live role/permission/configuration was changed here. Reports use
only the selected role in allowed_mentions and enable normal notifications, with
a durable five-minute cooldown. No historical/reopened-card pings, retries, or
changes to the >=0.90 automatic rule. Default/off preserves prior policy hashes.
Read BUILD_PLAN 10.56 for validation. The active VPS still needs installing and
configuring; keep the old Hermes moderation profile disabled.

Previous checkpoint: **0.7.0 native slash commands and configuration**. The owner
requested a repository-specific Discord menu instead of the inherited Hermes
commands and `!mod`, plus editing IDs. The standalone runner registers `/mod` in
its configured guild and then clears its own application's obsolete global
commands. It reuses the existing moderation/action pipeline and offers a guarded
`/mod config` menu for IDs and spending/call caps. See `docs/slash-commands.md`
for the destination VPS wheel update and command reference; BUILD_PLAN 10.55
records review and tests. All 766 tests passed; 66 relevant tests also passed
against the installed wheel without Hermes. Publication does not update the
other VPS or its Discord registrations. The new menu appears when that service
runs the release. Keep the old moderator profile on this host disabled.

Previous checkpoint: **0.6.1 systemd credential fix**. The owner supplied evidence
from the other VPS: systemd 255 exposes root-owned `0550` credential directories
and `0440` files on a read-only mount; 0.6.0 rejected these, and the deployed copy
was hand-patched. This commit fixes the systemd backend in the repository, with
trusted root/service owner and primary-group checks and exact read-only modes.
Portable owner-only rules remain unchanged. All 741 tests passed; 33 setup and
credential tests also passed against the installed wheel. Follow the existing
service upgrade in `docs/standalone.md` to replace the other host's local patch.
No access to that VPS or live installation change was performed for this fix.
The old `liberdus-mod` profile here was disabled at the owner's request at 16:01
UTC September 22; its database lock was released and the shared gateway's other
integrations reconnected. Keep it disabled. Earlier publication/deployment notes
below are historical; read BUILD_PLAN section 10.54 for the current release.

Checkpoint: 2026-09-22, standalone 0.6.0 reviewed for owner-authorized publication; live Hermes plugin remains 0.5.16. Read `docs/new-vps.md`, `docs/standalone.md` and BUILD_PLAN sections 10.52–10.53 first. Release 0.5.16 was previously deployed and verified as hermes. Read `docs/all-cards.md`, `docs/auto-delete-scope.md`, `docs/interaction-recovery.md`, `docs/staff-channel-category.md`, `docs/live-screening-diagnostics.md`, `docs/deletion-notices.md`, `docs/review-layout.md` and the latest section of `docs/BUILD_PLAN.md`. Repo: https://github.com/Liberdus/discord-moderator.git, branch `main`. GitHub authentication works and the separate checkout is `/home/hermes/discord-moderator`, originally cloned at `fef4903`. This checkpoint accompanies publication of the accumulated 0.5.10–0.6.0 source, tests and documentation; inspect Git status before continuing. The developer checkout is `/home/developer/projects/discord-moderator`; do not assume the other user's home is accessible.

## Immediate task

The latest owner request is to review and push the standalone implementation and provide instructions for a different VPS, explicitly accepting a fresh database. The 0.6.0 implementation passed 734 tests again during the publication review. Follow `docs/new-vps.md`: prepare the destination service without `--start`, enter only the Discord/JEV credentials privately, select the seven channel IDs and action option 3, and restore the old category boundaries. At cutover, disable the old moderation profile and restart the shared gateway before starting the new service. The current VPS has not been changed or restarted by this publication task. New-VPS access has not been supplied and no destination deployment has been performed. The owner's fresh-database choice intentionally resets local history, review bindings and spending counters; the old database may remain on this host. Do not invoke migration or transfer it unless the owner changes that choice. No real key should be pasted into chat or command arguments.

Previous completed deployment (retain as history):

Latest change: the owner requested the moderation-review/deletion-receipt card style everywhere. Version 0.5.16 is deployed: status, summary, help, connection, pending reviews, self-test, settings, action history, confirmations, results, health notices and errors all use Components V2 cards. No ASCII borders/full-message code boxes; excerpts use escaped quoted text and natural wrapping. Confirmation controls have their own card. Deferred private replies edit their existing ephemeral response directly, clear legacy fields for V2, and bind the returned message ID. Recovery uses a card without confirmation controls; no action is replayed. Existing posted messages are not bulk edited; new/reopened views use the new style. The four-concern >=0.90 rule and evaluation decision version remain unchanged. See docs/all-cards.md for protocol, tests and release hashes.

Previous change: the owner explicitly chose all four specific concerns at **>=0.90** for automatic deletion, including exactly 0.90. Version 0.5.15 is deployed. Sensitive requests, impersonation, suspicious offers and targeted abuse qualify subject to the existing purpose/context, fresh evidence, scope, permissions, private-report, rate-limit and at-most-once checks. Quoted warnings and unclear context remain excluded. `!mod auto-delete` now displays the rule. All seven monitored channels are unchanged. The owner also supplied a successful automatic-deletion receipt for incident 59bb2145fd464bb182e584e9e4fa9e0a at 13:51 UTC; the ledger confirms automatic=1/done. Do not repeat that completed live check. The owner was informed that the saved ambiguous unknown_code example at exactly 0.90 would become eligible and explicitly approved >=0.90; do not ask again or silently change it to >0.90. Replaying 44 saved valid synthetic results gives 15/16 harmful, 0/20 benign and 1/8 ambiguous candidates with no new provider calls, production writes or Discord actions. Historical reports/confirmations were not replayed. See docs/auto-delete-scope.md for release hashes, tests and limits.

Previous change: after the owner reported thinking forever after Confirm deletion, read-only inspection found a bound pending proposal but no deletion attempts for incident 77161671133f429eb4e467a3a2d0c767. The old code discarded acknowledgement/reply exceptions, so the original cause remains unknown. Version 0.5.14 fixes reproducible lost/slow acknowledgement and result-reply paths, adds safe bounded diagnostics and a status hint, and never replays a deletion. See docs/interaction-recovery.md. The old proposal is now expired. The owner subsequently confirmed the live check at 13:28 UTC: the reported message has one staff deletion marked done and a shared bot-mod receipt. Do not repeat this completed check. A later read-only audit verified auto-delete ON and deletion permissions in all seven monitored channels; one of three retained confirmed automatic deletions occurred in general, with the other two in earlier test channels. MANUAL-01 was suspicious_offer/promotion, outside the then-current sensitive_request-only automatic rule. No automated live destructive probe or AI request was made.

Previous change: the owner moved the existing private bot-mod channel into Committers and explicitly authorized the code exception. 0.5.13 now permits that configured staff destination for commands/reports while all other Committers channels stay outside monitoring. Actual guild metadata confirmed private visibility and all required bot permissions; no Discord permissions were changed. Runtime startup and read-only seven-channel deletion verification passed. See docs/staff-channel-category.md for the direct REST preflight limitation, tests and release hashes.

Latest investigation: the owner's general-channel recovery-phrase example failed its live AI check at 12:16:35 UTC with an old generic error. Two owner-authorized synthetic diagnostics subsequently passed, including the exact sentence (sensitive_request, score 0.99); estimated total cost $0.000077. The original cause is unknown because details were discarded. 0.5.12 now retains safe per-attempt diagnostics and displays them in !mod status. Do not claim the original failure was identified or fixed, and do not repeat the completed probes or completed updater. Full findings, hashes and costs: docs/live-screening-diagnostics.md. Bot-test-1 remains outside scope.

The requested historical Delete fix, clearer separated button sections, retained sender display and shared successful-deletion receipts are deployed in 0.5.11. New review messages use separate assessment/action cards. Sender mentions and IDs come from retained incidents, so source deletion does not remove attribution. Confirmed manual/automatic deletions produce non-pinging receipts in bot-mod; partial batches list only confirmed targets. Receipts are best effort and never cause mutation retries. The owner completed the fresh manual deletion and receipt check at 13:28 UTC; no assistant-run live destructive probe was performed. The earlier authorized synthetic JEV diagnostics are recorded above. Historical disconnect logs do not establish an underlying cause; continue using the new connection metadata.

1. Latest deployment/readiness confirmed at 14:07 UTC September 22: 0.5.16, healthy gateway, moderation and Telegram connected, imports and isolated self-test 9/9. All 46 installed files match. Offline checks: 469 core/setup + 228 integration (697). Config/policy hashes, flags and accounting matched the pre-deployment snapshot. Previous 0.5.15 code: `/home/hermes/.hermes/profiles/liberdus-mod/.liberdus-update-backup-7b6e8206/liberdus-moderator`. Do not rerun completed one-time updaters.
2. After a naturally occurring interruption, use `python3 /tmp/mod-check.py` and saved `!mod connection` metadata. Versioned diagnostic source: `/tmp/liberdus-disconnect-check-20260922.py`, repo `scripts/check_disconnects.py`. Compare service start/restart evidence, SDK resume/new-session, heartbeat and socket error labels. Do not paste raw logs or secrets into chat; missing logs do not establish a cause. `/tmp` artifacts may disappear after cleanup/reboot.
3. At deployment, policy and both profile configurations were unchanged; paused OFF, manual/automatic deletion ON, timeout OFF, role exemption OFF, JEV report_only, budgets $1/day and $4 total. Reserved totals were preserved. Inspect current settings before future actions. Connection metadata cannot reconstruct outages before tracking began. The release guide contains the timestamped diagnostic findings.
4. Manual Delete now fetches current content, shows it in a new confirmation and rechecks before mutation. Do not use automated live Discord deletion as a test without an explicit target. User prefers code review/regressions over repeated manual test posts.

## Historical 0.5.9 behavior retained in the current release

Manual Delete on a historical report uses retained message references under current authorization, scope and permissions. It presents current excerpts/links and explicitly marks changed text. Confirmations are actor/message-bound, expire after 60 seconds, and are invalidated by connection, pause/resume, action policy or evidence changes. The fresh action evidence is audited separately; the old incident/JEV result stays historical. Automatic deletion rules are unchanged. Completed/dismissed evidence and unavailable sources are refused.

`!mod connection` displays bounded connection metadata with no raw event payloads. Disconnect-only recovery notices are labeled Discord connection restored; missed checks are still not backfilled. discord.py already reconnects automatically. Do not enable global Discord DEBUG: source inspection found debug statements that can log raw frames and authentication/resume data.

Validation: 430 unit/setup + 191 pinned-runtime integration tests passed (621 total). The developer performed no live Discord actions, JEV requests, owner secret reads or gateway restarts. Packaging/source integrity is recorded in the release guide. Earlier artifacts stay preserved.

## Live deployment and scope

- OS account/home: hermes, `/home/hermes`.
- Hermes Agent v0.21.3, source c1488ac947c9bc33fd65ec464548dc9d8edd6122, Python 3.11.16, discord.py 2.7.1.
- Hermes source/runtime: `/home/hermes/.hermes/hermes-agent`, its `venv/bin/python`.
- Profile: `/home/hermes/.hermes/profiles/liberdus-mod`.
- Plugin: `plugins/liberdus-moderator` under that profile.
- Policy: profile `moderation.toml`; state: `state/moderation.sqlite3`.
- Shared user service: `hermes-gateway.service`, managed via `hermes -p default gateway restart`. Default profile also serves Telegram. Avoid needless/shared restarts and never start a second Discord connection with the same token.
- Guild: 746426387606274199; bot Liberdus Moderator: 1548537340870533150.
- Authorized operator: 977263877391794217. Private bot-mod commands/reports: 1551252553331642558, now inside Committers; only this configured destination has the category exception.
- Monitored seven Community text channels: developers 1293238000313958451; privacy-news 1318757883260964884; self-intro 1453063291961212959; general 1479253446258458644; feedback 1486610685398876170; incoming 746426388050870282; help 746499160823431199.
- Included categories: Community 746426387606274201 and Voice Channels 746426387606274202 (the latter currently adds no ordinary text channel). Committers 1318586868136415333 remains excluded from monitoring. The explicitly configured private bot-mod destination is allowed there for commands/reports only. New channels are not added automatically; other private/staff/unsupported channels remain excluded.
- Prior bot-test channels 1551249559819264030, 1551249642216357908, 1551249693399584818 are outside the public monitoring rollout.
- JEV screens eligible human text in report_only mode; no general Hermes LLM call. Hermes supplies the plugin/gateway runtime.
- Public deletion has explicit policy opt-in; manual and automatic deletion have separate persistent switches. Public timeout is blocked, even if a stale flag is on. No bans/kicks.
- Automatic deletion covers fresh single-message sensitive_request, impersonation, suspicious_offer and targeted_abuse results at unrounded scores >=0.90. Current evidence, allowed purpose, role/permission checks, delivered private report and the action rate limit still apply. None/unclear concerns and quoted_warning/unclear purpose do not qualify; lower scores and historical results remain outside automatic deletion.
- JEV budget settings historically $1/day and $4 total, preserving reservations/unknown charges. Exempt role 1302455329795342377 has a separate flag. Read current settings; never reset counters or broaden scope for testing.
- Staff buttons: Needs attention / Looks okay / Unsure save assessments; Looks okay completes a review, Needs attention/Unsure remain pending. Dismiss completes review without Discord action. Delete and Timeout are separate confirmation flows.

## Source, tests and prior decisions

`liberdus_moderator/manual_delete.py`, `action_transport.py`, `actions.py`, `hermes_adapter.py` implement the patch. `connection_health.py` records fixed metadata. `scripts/apply_manual_delete.py` is the hash-pinned owner helper. See new manual-delete/connection-health tests and integration tests. Run tests in an isolated checkout/runtime, not against the production database. Existing developer test runtime `/tmp/liberdus-package-check-311` and Hermes source mirror `/tmp/liberdus-hermes-c1488/NousResearch-hermes-agent-c1488ac` may have user-specific access restrictions.

Earlier tested flows: cross-channel and same-channel repeat, edit withdrawal, deletion resets, pause/resume including restart persistence, wrong-channel command denial, JEV shadow/batch/single-message evaluation, pilot auto/manual deletion, private confirmation completion, and assessment/pending queue behavior. Do not repeat completed work. Standalone hosting is now implemented; the live Hermes plugin remains active until the separate new-VPS cutover.

## Start a Codex CLI session as hermes

This is a new CLI conversation using this handoff, not a guaranteed migration of the exact app thread. Local CLI history normally belongs to the current user's `~/.codex`; logging in as a different Linux account does not automatically copy it. Use your own login and keep credentials out of the repo.

From the `hermes@db2` terminal, install using the official macOS/Linux installer if Codex is not already available:

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
```

Follow the installer's PATH instruction or open a new shell if `codex` is not found. Then sign in with a browser on your phone:

```bash
codex login --device-auth
```

Open the displayed URL and enter the one-time code. If the account has device-code login disabled, enable it in ChatGPT security settings or ask the workspace administrator as applicable. This is a browser login, not necessarily a GitHub-style mobile push notification.

Keep a private copy of the handoff and start in your home directory:

```bash
cp ~/discord-moderator/docs/HERMES_HANDOFF.md ~/mod-handoff.md
codex --no-alt-screen
```

First prompt:

```text
Read ~/mod-handoff.md and the latest docs/BUILD_PLAN.md checkpoint in ~/discord-moderator. Version 0.5.16 is already deployed; do not rerun a completed updater. Continue the disconnect investigation using new connection metadata and review the remaining repo tasks. Preserve scope, keys, action flags and budgets.
```

The existing checkout is `~/discord-moderator` and GitHub authentication is configured for hermes. Preserve any uncommitted work when continuing. Continue using the existing tmux session if you want terminal continuity. For later sessions created under hermes, `codex resume` can select its saved local conversation.

Official references: [Codex CLI installation](https://learn.chatgpt.com/docs/codex/cli), [headless/device-code login](https://learn.chatgpt.com/docs/auth#login-on-headless-devices), [Codex state locations](https://learn.chatgpt.com/docs/config-file/config-advanced#config-and-state-locations).
