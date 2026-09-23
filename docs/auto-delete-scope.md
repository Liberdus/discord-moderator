# Four-concern automatic deletion — 0.5.15

Current exception in 0.8.0: [messages recovered after a disconnect](catch-up.md)
always require staff review, regardless of score. The rule below applies to
eligible live results.

The owner explicitly approved automatic deletion for **sensitive requests, impersonation, suspicious offers and targeted abuse at a model score of 0.90 or higher**, retaining the warning, context and evidence exclusions. Previously, only sensitive requests scoring strictly above 0.90 qualified. The approved set is explicit in `actions.py`; adding a future screening label will not automatically authorize deletion for it.

The unrounded concern score is compared against 0.90. Exactly 0.90 qualifies; 0.89999 does not, even if a display rounds it to 0.90. The purpose must still be neither `quoted_warning` nor `unclear`. A concern of `none` or `unclear` does not qualify. `!mod auto-delete` displays the threshold and four categories alongside the existing feature switches.

All existing execution checks remain: deletion and auto-delete enabled; moderation unpaused; active policy; one supported human message in the configured public monitoring scope; current evidence and evaluation at most 60 seconds old; unchanged source after refetch; role exemption checks when enabled; a delivered private staff report; no completed/dismissed review; current bot permissions; no conflicting queued evidence; maximum five automatic attempts per minute; and durable at-most-once action records. Successful deletions retain sender attribution and post the existing non-pinging receipt in bot-mod. Timeout, ban and kick behavior is unchanged.

Deployment affects newly eligible live results. Startup invalidates old evidence and proposals; no historical reports, uncertain attempts or old confirmations are replayed. The monitored seven-channel list and bot-mod placement inside Committers are unchanged.

## Validation and retained model results

Typed-response tests cover every concern/purpose combination and score boundaries, including 0.89999, exactly 0.90 and above. Pinned-runtime integration tests pass real discord.py deletion methods through mocked HTTP for each newly eligible category, verify one shared receipt and no replay, and exercise warning/unclear context, edit, missing-permission, stale-result and failed-report exclusions. Mocked-response tests verify software behavior; they do not measure model accuracy.

The 44 existing synthetic provider results were replayed read-only through the new production decision function, using the newest valid matching response for each fixture. No provider requests, production writes or Discord actions were made. Results: 15/16 harmful examples qualify, 0/20 benign examples qualify, and 1/8 ambiguous examples qualifies. The known ambiguous `unknown_code` example scores exactly 0.90 and now qualifies; the owner was shown that tradeoff before explicitly choosing >=0.90. Its original label expectations and recorded result remain unchanged. These are small synthetic-sample observations, not an accuracy guarantee.

Replay metadata: `/tmp/mod-auto-delete-0515-replay.json`. The evaluator now identifies its decision behavior as `moderator-0.5.15`. Existing 0.5.4 evaluation bundles remain unchanged and retain the older behavior.

## Deployment

Deployed and verified September 22, 2026 at 13:47 UTC. The full checks passed: 465 core/setup and 225 pinned-runtime integration tests (690 total). Both required gateway restarts completed healthy; moderation and Telegram are connected. Installed-runtime imports and isolated self-test passed (9/9); all 45 installed files match the reviewed archive. Read-only inspection confirms the four-category set, inclusive 0.90 threshold, seven monitored channels, deletion and auto-delete ON, moderation unpaused and timeout OFF. Both profile configs, policy hash, flags and budget counters match the pre-deployment snapshot.

- Archive: `/tmp/liberdus-update-0.5.15-20260922.pyz`, SHA-256 `7899255dcc1bd18d11f0e02654e4cf210715d7c47a62022691f7399f70a8b49b`.
- Completed helper: `/tmp/liberdus-apply-0.5.15-20260922.py`, SHA-256 `73ab8170d6dfd24bbab2f2aa52f500dbb2d99a325e864ecc9031b4cc9d857230`; source `scripts/apply_auto_delete_scope.py`. Do not rerun.
- Previous 0.5.14 plugin: `/home/hermes/.hermes/profiles/liberdus-mod/.liberdus-update-backup-q6tdujpm/liberdus-moderator`.
- Before/after verification: `/tmp/mod-before-0515.json`, `/tmp/mod-after-0515.json`.

No synthetic messages were posted to Discord, no direct live deletion probe was made, and no paid AI evaluation was repeated. Future qualifying live messages use the deployed rule. Staff can inspect it with `!mod auto-delete` and use `!mod auto-delete off` to disable automatic deletion. Source changes remain local, uncommitted and unpushed; preserve earlier changes and release artifacts.

The owner subsequently supplied a live automatic-deletion receipt for incident `59bb2145fd464bb182e584e9e4fa9e0a` at 13:51 UTC. Read-only ledger inspection confirmed `automatic=1`, `outcome=done`. This is a completed live observation of the expanded rule, separate from the offline evaluation and mocked SDK tests.
