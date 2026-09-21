# Review fixes — 0.5.4

**Owner deployment confirmed — September 21, 2026:** the supplied helper output reports 0.5.4 installed, runtime import passed, isolated self-test 9/9 and both gateway restarts completed. Policy, database, credentials and flags were preserved. The [JEV screening baseline and focused retry](screening-evaluation.md) have separate results and commands.

This release addresses the five findings in the [0.5.3 code review](reviews/2026-09-21-code-review.md). The review scenarios are being converted into automated regression tests requiring the corrected behavior. No live Discord messages or paid JEV requests are part of development validation.

## Corrected behavior

- **F1 — Stop controls:** authenticated `!mod pause`, `!mod deletion off`, `!mod auto-delete off` and `!mod timeout off` commit immediately on receipt after private-channel and current-policy checks. They do not wait behind evidence, JEV or reply work. Older queued on/resume commands that conflict with the stop receive terminal receipts, so duplicate delivery cannot later undo it. A later deliberate on/resume remains possible. Replies are separately throttled and coalesced into at most four fixed control summaries; state is saved even if a reply fails.
- **F2 — Canceled clicks:** resets complete already-acknowledged queued/stale clicks with a private cancellation notice rather than executing them against old evidence. The adapter bounds outstanding acknowledgments/notices and drains admitted interactions during shutdown before closing Discord HTTP, with an interruption notice for uncertain in-flight work. Cancellation never retries the underlying action. A real delivery failure remains best effort.
- **F3 — Exact role IDs:** role exemption uses the pinned SDK's original membership IDs, which retain roles missing from the guild cache. It no longer infers non-membership from the filtered `Member.roles` property. Unknown membership refuses automatic deletion when exemption is enabled. Timeout requires complete target and bot role resolution before deriving privileges and hierarchy.
- **F4 — Protected timeout permissions:** Administrator, Manage Server, Moderate Members, Manage Messages, Kick Members, Ban Members, Manage Roles and Manage Channels each independently block staff timeout of that member. Existing owner/operator/bot/configured-role/hierarchy protections remain in place.
- **F5 — Edited messages:** edits resolve fresh membership while exemption is ON. Known membership includes the guild's default-role ID; an empty role list means unknown. Unknown membership is withheld from JEV in both single-message and shadow modes while exemptions are enabled, including queued/in-flight result rechecks. Deterministic spam rules continue, and single-message status records incomplete checks. Exemption OFF does not require the extra membership lookup. Previously sent JEV requests cannot be recalled.

Message schema, persisted evidence hashes and policy hashing are unchanged. Older empty-role evidence is treated conservatively while an exemption is enabled. Role properties are still subject to Discord gateway timing; complete cache entries are not a claim of atomic server-side permissions.

## Installation

Run once from the existing `hermes` terminal:

```sh
python3 /tmp/liberdus-apply-0.5.4-20260921.py
```

The hash-pinned helper disables the custom platform, waits for a healthy shared-gateway restart, installs the backed-up code update, then enables the platform and waits for another healthy restart. Telegram briefly reconnects because it shares this gateway. The installer preserves policy, credentials, moderation/accounting database, action flags, role exemption and pause state. It does not configure or enable timeouts and does not reset the JEV budget.

The developer account cannot install into the owner-only profile. The owner command is the remaining deployment step; the earlier successful auto/manual deletion checks need no repetition for this release. `!mod status` now includes the plugin version for a simple installed-version check. Keep the printed backup path and stop if the helper reports a failed stage.

## Scope and limits

Automatic deletion remains limited to current sensitive-request evidence with score strictly above 0.90 and eligible purpose. No new channels, thresholds, bans, kicks or automatic timeouts are introduced. In-flight HTTP actions may already have reached Discord before a stop command arrives; the bot blocks subsequent actions without pretending to undo an applied request.

The separate delete-then-timeout limitation is retained: deleting a message invalidates the current evidence, so a timeout cannot then reuse that incident. A combined workflow would need explicit design and fresh validation. This release does not bypass historical-evidence protection.

Automated checks establish behavior given synthetic JEV outputs. They do not measure real-model false-positive rates, confirm the live server's timeout permission, or eliminate Discord's final fetch-to-action race. A genuine network outage can prevent a private completion notice; the bot must not replay an action to repair that notice.

## Rollback

Disable the custom platform and finish a gateway restart, restore the previous plugin directory from the printed backup, then enable/restart. Preserve policy, database and accounting. This release changes no policy/database schema. Restoring 0.5.3 restores the reviewed control and role-protection defects.

## Validation

**254 core/setup + 136 pinned-runtime integration tests passed (390 total)** on Python 3.11.16. The permanent routing-matrix test covers 180 concern/purpose/score combinations. New regression suites cover 18 control/interaction scenarios and 19 role/edit scenarios, including real SDK membership, timeout and webhook paths with HTTP mocked. An independent reviewer inspected the combined implementation and reran targeted tests; no release blockers remained.

The updater is tested from existing 0.5.3 with action policy enabled and checks that policy, credentials and database bytes remain unchanged. Its staged package and owner helper match the reviewed source. No live Discord/JEV calls, real profile access or gateway restarts were performed during implementation.

Updater SHA-256: `287a46612f150397d1ba74e52ae9c219867d901f7cfbc7ea7f14b760be154434`.
