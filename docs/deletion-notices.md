# Shared deletion receipts — 0.5.11

**Deployed September 22, 2026.** Both gateway restarts and runtime imports/9-of-9 self-tests succeeded. Moderation and Telegram were connected afterward. Policy, both profile configurations, saved action/pause/exemption flags and reserved accounting totals were preserved. All 44 installed plugin files matched the tested archive. The previous 0.5.10 code is retained at `/home/hermes/.hermes/profiles/liberdus-mod/.liberdus-update-backup-5cjkb4oc/liberdus-moderator`. Do not rerun the one-time updater. Live receipt rendering awaits the next naturally occurring successful deletion; no live action was created as a test.

Confirmed successful staff deletions now post a receipt in the private command channel where staff confirmed the action (bot-mod in the deployed policy). The confirmation and detailed interaction response stay private to the requesting moderator. Automatic deletion already posted a shared action result; its success result now uses the same receipt format.

The receipt identifies the sender by clickable mention and retained Discord ID, the acting staff member or automatic moderation, completion time, deleted message IDs and source channels, incident ID and the `!mod actions ID` lookup. It is a compact Components V2 card without action buttons. All mentions are suppressed and the existing silent delivery setting is retained: the message is visible in bot-mod, but does not ping the sender or staff.

Only newly attempted actions whose durable outcome is `done` contribute to a success receipt. A partial batch reports the confirmed subset and explicitly identifies it as a partial result. Already absent, denied, uncertain, canceled, stale and previously attempted actions do not become successful-deletion receipts. Timeout results are unchanged. Existing report refreshes continue to show retained action history.

Each receipt uses a stable Discord nonce derived from the successful action keys. Consumed confirmations and durable target deduplication prevent repeated deletion/receipt attempts through replayed clicks. Notice delivery is best effort: a lost response, disconnect or process exit can leave the receipt unavailable. There is no automatic notification replay or deletion retry. The action ledger remains authoritative; if shared notice delivery cannot be confirmed during a staff action, the private result retains the deletion outcome and says to check `!mod actions ID`.

No message content is copied into the receipt. Existing incident evidence, author retention, authorization, scope, policy/action switches, JEV calls/accounting and action-rate limits remain unchanged. This release includes the separate review cards and retained sender display from [0.5.10](review-layout.md).

## Release checks

**642 tests passed:** 438 core/setup and 204 pinned-runtime integration tests. Offline regressions cover shared manual and automatic success receipts, sender/actor attribution, exact confirmed targets, partial batches, absent/denied/uncertain requests, duplicate confirmations, notification transport failure without mutation retry, and real discord.py serialization with mentions suppressed. No live deletion test or paid JEV request was performed.

The fixed owner helper is `scripts/apply_deletion_notices.py`, staged at `/tmp/liberdus-apply-0.5.11-20260922.py`. It uses the existing pinned code-only upgrade workflow with stopped-plugin validation, rollback copy and two shared-gateway restarts. Consult the latest BUILD_PLAN checkpoint before using it; do not rerun it after 0.5.11 is installed.

Release SHA-256 values:

- `/tmp/liberdus-update-0.5.11-20260922.pyz`: `a6af3760f60a6cb415f499fd45d58a0141ae3f077e69f8aabe5b2d9188abc775`
- `/tmp/liberdus-apply-0.5.11-20260922.py`: `15908ec8eea8f9b2f7a61585137595e3d7ef08dbd7e076f9cedb711380b44df1`

The archive passed integrity, compilation and exact source-equality checks. Earlier release artifacts remain intact.
