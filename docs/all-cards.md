# Consistent moderation cards — 0.5.16

The owner requested the card style used by moderation reviews and deletion receipts for the rest of the bot. New status, summary, help, connection, pending-review, self-test, settings, action-history, confirmation, action-result, health and error replies now use Discord Components V2 containers. The shared formatter uses headings and natural text wrapping, without ASCII borders or full-message code boxes. Commands and reference IDs can still use compact inline code for copying.

Confirmation previews put their controls in a separate labelled card. The Confirm/Cancel IDs, actor and message binding, 60-second expiry, queue checks and deletion guards are unchanged. Immediate private replies remain ephemeral. Deferred replies edit the existing ephemeral response directly, clearing legacy content, embeds and attachments before installing the card; the returned message ID binds the confirmation. Bounded recovery replies use the same card format and remove confirmation controls after uncertain delivery, without repeating the action.

Saved and freshly fetched message excerpts appear as quoted text inside cards. Excerpts escape Markdown, neutralize mentions and URL activation, remove control/bidi characters, and remain bounded. The stored evidence and content comparisons are unchanged. `!mod explain` now uses normal card text for JEV details as well. Existing staff-assessment/action cards and the shared deletion receipt retain their separate controls and colours.

Channel replies remain private to the configured bot-mod destination, with suppressed mentions, silent delivery and existing nonces. Layout views are stopped after delivery because durable global interaction handling remains authoritative. This is presentation and response-transport work; the owner-approved four-concern >=0.90 auto-delete decision, evaluation version, scope, flags, budgets, timeout policy and at-most-once action ledger are unchanged. Existing posted messages are not bulk edited; reopening an incident or refreshing its assessment uses the new presentation.

## Validation

Pinned-runtime checks exercise actual discord.py 2.7.1 serialization with HTTP mocked: V2 channel payloads for all staff commands, ephemeral immediate replies, original-response edits, cleared legacy fields, returned-ID confirmation binding, separate confirmation controls, mention suppression and nonces. Existing tests cover confirmed/partial/uncertain deletion, duplicate confirmations, lost acknowledgements, failed previews and result-reply recovery. Formatter tests continue to check bounds, source attribution, stored-evidence preservation and hostile excerpt escaping. SDK test transport overrides account for the serialized worker's separate task context; no live network probe is used.

Discord protocol reference: [Components Overview](https://docs.discord.com/developers/components/overview). All visible V2 content is supplied through components rather than traditional content or embeds.

## Deployment

Deployed and verified September 22, 2026 at 14:07 UTC. All 697 checks passed: 469 core/setup and 228 pinned-runtime integration tests. Both gateway restarts completed healthy; moderation and Telegram are connected. Runtime imports and isolated self-test passed (9/9), and all 46 installed files match the tested archive. Both profile configs, policy hashes, action flags and accounting match the pre-deployment snapshot. Deletion/auto-delete remain ON, moderation unpaused, timeout OFF, role exemption OFF, seven channels unchanged. The four-category >=0.90 decision and evaluator decision version `moderator-0.5.15` remain intact.

- Archive `/tmp/liberdus-update-0.5.16-20260922.pyz`, SHA-256 `09f193ba0e7d8435d1a64908fc87700c78e21d32fa8294fa1ce278ab8ae1c0cc`.
- Completed helper `/tmp/liberdus-apply-0.5.16-20260922.py`, SHA-256 `fb49b02c94f7abd004c87576721a43dad6270cf416554ef30fbb96425142bac0`; source `scripts/apply_all_cards.py`. Do not rerun.
- Previous 0.5.15 plugin: `/home/hermes/.hermes/profiles/liberdus-mod/.liberdus-update-backup-7b6e8206/liberdus-moderator`.
- Preservation records: `/tmp/mod-before-0516.json`, `/tmp/mod-after-0516.json`.

Use `!mod summary`, `!mod status` or `!mod auto-delete` in bot-mod to see new cards. Reopen old incidents with `!mod incident ID`; existing posted messages are not bulk rewritten. No bot test messages, direct live deletions or paid AI requests were made for this presentation update. Local source changes remain uncommitted and unpushed.
