# Private bot-mod in Committers — 0.5.13

**Deployed September 22, 2026, around 12:54 UTC.** The owner moved the existing bot-mod channel into Committers and explicitly authorized the bot-side exception.

The private command/report channel `1551252553331642558` is now allowed inside an excluded monitoring category. This is an exception for the explicitly configured staff destination only: no other Committers channel is added to monitoring, and ordinary bot-mod messages are not screened. The seven public monitored channel IDs, included categories, Committers exclusion, command channel ID and authorized operator IDs remain unchanged.

Runtime validation, read-only preflight and public rollout/deletion verification now agree on this distinction. The command channel still requires an ordinary text channel in the configured guild, verifiable category metadata, private visibility, and bot View Channel, Read Message History and Send Messages permissions without Administrator. Existing staff authorization, report bindings, action confirmations and public action restrictions still apply. A channel move or restart invalidates pending confirmations; staff must request a fresh confirmation as before.

Read-only Discord metadata verified that bot-mod is in Committers (`1318586868136415333`), hidden from `@everyone`, and accessible to the bot with all three required permissions and no Administrator. No Discord permission or channel changes were needed or made by the assistant. The owner had already moved the channel, preserving its ID and history. No message history was fetched and no messages, deletions or JEV requests were used for verification.

Tests cover startup, authorized commands, ordinary staff-text exclusion, continued exclusion of monitored channels moved into Committers, private report delivery with buttons, confirmed deletion receipts and duplicate suppression, permission loss, public visibility, category metadata, read-only rollout/deletion verification and upgrade preservation.

**667 offline checks passed:** 456 core/setup and 211 pinned-runtime integration tests. The release archive passed integrity, module compilation and exact tested-source equality checks.

Release artifacts:

- `/tmp/liberdus-update-0.5.13-20260922.pyz`: `0da67b793c7b23a79cb451dc09d1db56d843989977dcb01920a37aa2cf261271`
- `/tmp/liberdus-apply-0.5.13-20260922.py`: `c85a476c0fcfef0c6d1415ee90aa6fe33531700d6ed4646d1f693717a21311cd`

The fixed owner helper is `scripts/apply_staff_category.py`. It uses the existing stopped-plugin update workflow and retains a rollback copy. Earlier artifacts remain unchanged. Deployment confirmation follows below.

Both shared-gateway restarts completed healthy. Runtime imports and isolated self-test 9/9 passed; all 44 installed release files match the pinned archive. Moderation and Telegram connected. Both profile configuration files and the policy are byte-identical to the pre-deployment snapshot; saved action/pause/exemption flags and accounting totals are unchanged. Previous 0.5.12 code is retained at `/home/hermes/.hermes/profiles/liberdus-mod/.liberdus-update-backup-xza7yoj9/liberdus-moderator`. Do not rerun the completed updater.

Installed-code verification using Discord guild channel metadata and effective permissions passed for the private bot-mod destination and the unchanged seven-channel public deletion scope. A separate direct per-channel REST preflight returned HTTP 403 before completing; the error did not identify which endpoint failed. No additional category permissions were granted. The successful guild-inventory verification uses the existing `public_deletion.verify_deletion` checks and does not require a direct REST fetch of every category. Runtime startup also verified the configured command destination using the SDK's guild cache. No live command/report/deletion test was sent.
