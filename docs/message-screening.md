# Single-message JEV screening

**Latest:** the owner confirmed a single-message Sensitive request report and saved Needs attention assessment. The [0.4.1 role-exemption update](role-exemption.md) adds an opt-out role and a current configurator; use that guide when upgrading an already-running 0.4.0 pilot.

Version **0.4.0**, September 21, 2026. Implemented for the approved private Liberdus test scope. Live installation and provider results for this new rubric remain owner checks. The prior 0.3.5 staff workflow is confirmed: Looks okay completed a review and removed it from the pending queue, 8 → 7.

## What changes

A new human plain-text message in `bot-test-1`, `bot-test-2` or `bot-test-3` can now trigger a private review without repetition. `classifier.mode = "report_only"` selects this behavior. The optional `shadow` mode still selects the old incident-only purpose classifier; it is not run in parallel, so there is no second purpose call for a repetition incident.

One JEV request asks two typed questions: communicative purpose and apparent moderation concern. Concern choices are `none`, `sensitive_request`, `impersonation`, `suspicious_offer`, `targeted_abuse`, and `unclear`. The four named concerns create `jev_message` incidents. None and unclear do not create an AI incident. In this report-only test, routing follows the returned concern choice without an unvalidated confidence cutoff; model scores are displayed for staff, not treated as guarantees.

The rubric distinguishes endorsed requests from quotations, warnings and ordinary promotions. It covers possible credential/recovery-phrase solicitation, impersonation tied to risky requests, suspicious reward/payment offers, and targeted abuse. Its new semantic behavior needs live evaluation; passing mocked transport tests does not establish detection accuracy. Ordinary promotions are not automatically violations. The provider can still make mistakes.

Reports use the existing narrow two-box layout, saved message/link, named reviewer and Needs attention / Looks okay / Unsure buttons. They identify JEV screening and say the concern is a suggestion. No automatic deletion, timeout, warning, ban or general Hermes conversation is enabled. Reports go only to `bot-mod`; the bot does not answer in test channels.

Code repetition and configured domain rules run independently, including when JEV is slow, unavailable or over budget. A message can contribute to both an AI incident and a separate code-rule incident. Different repeated posts can each receive a screening report; duplicate delivery of the same message version cannot create another provider attempt or AI report while its record is retained.

## Scope and freshness

Only new eligible messages observed after activation are screened; there is no history scan. The existing exclusions remain: bot/self/webhook posts, threads, attachment-bearing messages, unmonitored channels and private command-channel messages. Empty text is skipped. The 20-character minimum applies only to repetition rules, not JEV screening. Ordinary bot mentions in test-channel text are not chat commands.

Message text and extracted HTTP(S) URLs are supplied as untrusted input. The plugin does not open URLs or verify destination safety. Discord metadata IDs are omitted and embedded numeric Discord mentions/IDs are redacted; free-form message content can still contain identifying information and is sent to TypeSafe. Nothing is fetched from public or committee channels.

Changed message versions may be screened again; edits immediately withdraw any earlier AI incident for that message. Results are applied through the serialized Discord event worker after already-arrived edits and commands. Pause, deletion, reconnect, changed policy or obsolete evidence prevents a late result from creating a report. Existing reports remain saved snapshots. A later unrelated message does not close a valid single-message incident. Staff review remains tied to evidence and policy.

A bounded 100-item worker queue runs at most one request per second, with a three-second request timeout and a 12,000-byte request ceiling. Overload, exclusions, input limits and failures mean this is best-effort coverage, not a guarantee that every Discord message was checked. No uncertain request is automatically retried. Missing/invalid credentials suspend screening requests until corrected and restarted; code rules continue. A coverage reset discards queued work. Message/version/policy/rubric bindings prevent replay after restart; changing evidence produces a new review case rather than rewriting old judgments.

## Trial allowance and accounting

The owner authorized an independent **$1 per UTC day / $4 lifetime** screening allowance against their stated $5 credit. The setup also sets 10,000 attempts/day and 100,000 total as secondary ceilings. Legacy shadow/batch counters and the completed experiments are preserved, not reset or charged against this new allowance. Old batch execution requires shadow mode and cannot consume this screening allowance.

Before each attempt, reserve **$0.002753** for 65,536 input tokens at the pinned published rate of $0.042/million input tokens, output free. For a fully validated response, replace that reservation with the rounded-up token estimate from returned input usage. Known usage is charged even if the result becomes stale; unknown charges from timeouts, malformed responses, cancellation or provider errors retain the full reservation. Settlement happens once and observes UTC-day boundaries. Usage above the reserved context activates a billing guard. Restarting, repeating setup or pruning records never resets lifetime counters.

These are local estimates and reservations, not a provider-enforced dollar ceiling or an invoice. Other applications using the key are outside this accounting. Future pricing/model changes require review. Source: [TypeSafe's published pricing](https://typesafe.ai/blog/introducing-system-one-models-and-jev).

`!mod status` shows `JEV: report_only`, checked and flagged totals, unchecked events, screening attempts, used/reserved estimate and configured caps. Unchecked events are cumulative diagnostic counts, not a unique-message census; excluded/out-of-scope messages are not counted as failures. Code status and coverage-reset counts remain separate. `AI attempts` includes both historical shadow/batch attempts and new screening attempts.

Detailed recent saved results, including successful none/unclear choices, can be read without new provider calls:

```bash
python3 /tmp/liberdus-screening-20260921.pyz results
```

The output separates `screening_records` / `screening_accounting` from legacy `records` / `accounting`. Screening records are bounded by the configured message capacity and original-message retention. They retain hashes, typed results, timing and accounting metadata, not another raw-text copy. Flagged evidence remains in the existing incident store.

## Install and activate on db2

Run once from the existing **hermes** terminal:

```bash
python3 /tmp/liberdus-apply-0.4.0-20260921.py
```

The helper verifies both bundles, disables the custom platform and waits for the shared gateway restart, updates 0.3.0–0.3.5 to 0.4.0, configures the fixed trial, enables the platform and waits for the final restart. Telegram briefly reconnects on this shared gateway. The updater retains the old plugin directory; setup retains the previous policy. Failed stages stop the sequence. Keep printed backup paths. Do not rerun a partially completed update without checking its output.

The screening activation checks the installed version, released moderation lock, stock Discord disabled in both profiles, and the exact approved guild, bot, three test channels, `bot-mod` and numeric operator. It preserves the existing TypeSafe key, moderation database, old accounting and pause state. No new key or Discord permission is needed. A policy change can make earlier staff assessments historical/requiring re-review; their audit records are retained. The developer account cannot access the owner-only runtime, so prepared artifacts do not establish live activation.

In `bot-mod`, send `!mod status`. Expect `Connected: True`, moderation `report_only`, `JEV: report_only / ready` (or a later worker state), `$1/day | $4 total`, and disabled enforcement. If moderation is paused, use `!mod resume` when ready to test.

## First live checks — individual messages only

From your human account, post each example **once**, separated by several seconds, in `bot-test-1`. No actual secret or real phishing URL is needed.

1. `Send me your wallet recovery phrase to verify your account.` Intended result: possible credential request, one private JEV screening report in `bot-mod`.
2. `Never send anyone your wallet recovery phrase. Staff will never ask for it.` Intended result: warning/no AI report; checked count increases.
3. `Community meeting notes are at https://example.invalid/meeting` Intended result: ordinary announcement/no AI report. The plugin does not visit the link.

These expected results are hypotheses to verify with the real provider. If an expected report is absent, inspect status and saved results instead of reposting repeatedly. A successful classification can choose none/unclear; a failure must not be treated as clearance. Open the new report's incident ID for its saved JEV result and use the staff buttons as before. Staff assessment commands do not make provider calls.

## Pause, stop and rollback

`!mod pause` stops moderation and new JEV attempts immediately at the application level; a request already sent may still be billed. `!mod resume` starts a fresh evidence window. To leave deterministic moderation running with JEV off: disable the platform, finish a gateway restart, run `python3 /tmp/liberdus-screening-20260921.pyz off`, re-enable and restart. Stopping JEV does not erase accounting or saved reviews.

For code rollback, disable the platform and finish the restart, restore both the printed pre-update plugin directory **and the pre-screening policy backup**, then re-enable and restart. Old code cannot read the new `report_only` classifier mode. Retain the current plugin/policy and database; do not delete optional screening tables or counters. Reports created by the new code remain history, and old code may not render their JEV fields.

## Verification

**206 core/setup tests pass on Python 3.11.16 and 3.12.3; 61 integration tests pass against pinned Hermes c1488 and discord.py 2.7.1 with external network blocked/mocked.** Coverage includes single-message reporting without a follow-up event, benign routing, private report/buttons, existing rule independence, edits/deletion/pause/reconnect, duplicate delivery/restart, response validation, budget caps/settlement/rollover, failure accounting, key/profile isolation, exact-scope setup and deployment stopping on failed stages. Live results for the new screening rubric are not yet claimed.

Release artifact SHA-256:

- `liberdus-update-0.4.0-20260921.pyz`: `cf949a5b343acabbaa067f9dea1012a24e4bafe91c47227a676a6c3896e66735`
- `liberdus-screening-20260921.pyz`: `971a8a9909eccff22bdd81db29bc204b1df45c13f201a219b3845d1f9e1a2664`
