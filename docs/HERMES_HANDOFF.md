# Continue Liberdus moderation work as hermes

Checkpoint: 2026-09-22, release 0.5.9 prepared. Read `docs/manual-delete-refresh.md` and the latest section of `docs/BUILD_PLAN.md`. Repo: https://github.com/Liberdus/discord-moderator.git, branch `main`. The developer checkout is `/home/developer/projects/discord-moderator`; work as hermes in a separate `~/discord-moderator` checkout. Do not assume the other user's home is accessible.

## Immediate task

The user requested: **patch the manual Delete button for historical incidents and investigate periodic Discord disconnects**. The Delete patch is implemented and tested; deployment is not yet confirmed. The disconnect cause is not known because developer cannot read the hermes-owned profile/journal. The user's attempt to run the diagnostic split the filename across two shell lines and never ran it. A new CLI session as hermes should finish the diagnostics and deployment without restarting this project from scratch.

1. Run `python3 /tmp/mod-check.py` as hermes and analyze its filtered output. Versioned source: `/tmp/liberdus-disconnect-check-20260922.py`, repo `scripts/check_disconnects.py`. The short alias and script contain no credentials. Do not paste raw logs or secrets into chat. Compare service start/restart evidence, SDK resume/new-session, heartbeat and socket error labels; missing logs do not establish a cause.
2. Check the installed plugin manifest version and whether a previous owner deployment already completed. If still a supported pre-0.5.9 release, the prepared owner helper is `python3 /tmp/mod-update.py` (same as `/tmp/liberdus-apply-0.5.9-20260922.py`). It verifies its pinned archive, disables/restarts, installs, enables/restarts. This upgrade is already requested; preserve the user's policy and existing flags. Do not rerun a one-time updater against 0.5.9 or blindly retry a partial update. The scripts reside in `/tmp` on the same VPS and may disappear after cleanup/reboot.
3. Inspect post-upgrade readiness. In private bot-mod the operator can use `!mod status` and `!mod connection`. The latter records new disconnect/recovery metadata after the upgrade. Do not claim an outage cause is fixed without evidence. Inspect current action settings instead of assuming old reported values still apply.
4. Manual Delete now fetches current content, shows it in a new confirmation and rechecks before mutation. Do not use automated live Discord deletion as a test without an explicit target. User prefers code review/regressions over repeated manual test posts.

## What 0.5.9 changes

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
- Authorized operator: 977263877391794217. Private bot-mod commands/reports: 1551252553331642558.
- Monitored seven Community text channels: developers 1293238000313958451; privacy-news 1318757883260964884; self-intro 1453063291961212959; general 1479253446258458644; feedback 1486610685398876170; incoming 746426388050870282; help 746499160823431199.
- Included categories: Community 746426387606274201 and Voice Channels 746426387606274202 (the latter currently adds no ordinary text channel). Always exclude Committers 1318586868136415333. New channels are not added automatically; private/staff/unsupported channels excluded.
- Prior bot-test channels 1551249559819264030, 1551249642216357908, 1551249693399584818 are outside the public monitoring rollout.
- JEV screens eligible human text in report_only mode; no general Hermes LLM call. Hermes supplies the plugin/gateway runtime.
- Public deletion has explicit policy opt-in; manual and automatic deletion have separate persistent switches. Public timeout is blocked, even if a stale flag is on. No bans/kicks.
- Automatic deletion remains limited to fresh single-message sensitive_request scores strictly above 0.90, current evidence, allowed purpose, role/permission checks, delivered private report and action rate limit. Other concerns are staff review only.
- JEV budget settings historically $1/day and $4 total, preserving reservations/unknown charges. Exempt role 1302455329795342377 has a separate flag. Read current settings; never reset counters or broaden scope for testing.
- Staff buttons: Needs attention / Looks okay / Unsure save assessments; Looks okay completes a review, Needs attention/Unsure remain pending. Dismiss completes review without Discord action. Delete and Timeout are separate confirmation flows.

## Source, tests and prior decisions

`liberdus_moderator/manual_delete.py`, `action_transport.py`, `actions.py`, `hermes_adapter.py` implement the patch. `connection_health.py` records fixed metadata. `scripts/apply_manual_delete.py` is the hash-pinned owner helper. See new manual-delete/connection-health tests and integration tests. Run tests in an isolated checkout/runtime, not against the production database. Existing developer test runtime `/tmp/liberdus-package-check-311` and Hermes source mirror `/tmp/liberdus-hermes-c1488/NousResearch-hermes-agent-c1488ac` may have user-specific access restrictions.

Earlier tested flows: cross-channel and same-channel repeat, edit withdrawal, deletion resets, pause/resume including restart persistence, wrong-channel command denial, JEV shadow/batch/single-message evaluation, pilot auto/manual deletion, private confirmation completion, and assessment/pending queue behavior. Do not repeat completed work. Standalone hosting is a later nice-to-have; keep the Hermes plugin now. The main plan already contains future TODOs.

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
cp /tmp/mod-handoff.md ~/mod-handoff.md
codex --no-alt-screen
```

First prompt:

```text
Read ~/mod-handoff.md. Continue the Liberdus moderation task: inspect the filtered disconnect diagnostics, check the installed version, and finish the prepared 0.5.9 update if still needed. Preserve scope, keys, action flags and budgets.
```

The new session can clone the repository into `~/discord-moderator` for future edits. GitHub authentication, if needed, is separate from Codex login. Continue using the existing tmux session if you want terminal continuity. For later sessions created under hermes, `codex resume` can select its saved local conversation.

Official references: [Codex CLI installation](https://learn.chatgpt.com/docs/codex/cli), [headless/device-code login](https://learn.chatgpt.com/docs/auth#login-on-headless-devices), [Codex state locations](https://learn.chatgpt.com/docs/config-file/config-advanced#config-and-state-locations).
