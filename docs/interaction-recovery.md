# Button acknowledgement and reply recovery — 0.5.14

The owner reported an indefinite ephemeral “thinking…” message after **Confirm deletion** for incident `77161671133f429eb4e467a3a2d0c767`. Read-only production inspection found a bound, pending proposal and zero deletion attempts. The gateway was connected and deletion enabled. The old handler discarded acknowledgement and reply exceptions, so the exact historical error cannot be recovered. This is not evidence that moving bot-mod caused a deletion permission failure.

The old two-second acknowledgement timeout could abandon a click after Discord had already accepted it and displayed the placeholder. A delayed receipt and an SDK parsing failure after acknowledgement reproduce that failure path with mocked HTTP and the pinned SDK. Version 0.5.14 sends the acknowledgement immediately, allows up to five seconds for its receipt, and never queues a moderation action after an uncertain acknowledgement. It makes one bounded attempt to replace the placeholder with a cancellation message. Discord still enforces its own initial-response deadline; the local timeout does not extend that deadline.

If delivery of a completed action result fails, the bot attempts to edit the original ephemeral response with the same result. It never retries the deletion, changes an uncertain outcome to success, or sends another shared deletion receipt. If a confirmation preview's delivery is uncertain, recovery removes its buttons, explains the failure and leaves the proposal unbound. A network outage can still prevent the recovery reply.

The last 24 button lifecycle events are retained in `discord_interaction_health_v1`: timestamps, numeric interaction/source-message IDs, fixed stages/reason codes, and numeric HTTP/Discord codes where available. No interaction tokens, proposal tokens, message text, raw exceptions or provider bodies are stored or logged. `!mod status` shows the most recent retained button error. Diagnostic persistence failure cannot block reply recovery. Shutdown waits for admitted acknowledgement/recovery tasks before closing the Discord session.

Regression coverage includes real discord.py 2.7.1 acknowledgement and edit serialization, delayed/lost/malformed receipts, expired confirmations, completed and uncertain deletion results, uncertain preview delivery, sanitized bounded metadata and upgrade preservation from 0.5.13. All Discord HTTP and message deletions in these tests are mocked; there is no paid AI call or live destructive probe.

After installation, the owner should open `!mod incident 77161671133f429eb4e467a3a2d0c767` in bot-mod, click **Delete message(s)**, review the current preview, then click **Confirm deletion** within 60 seconds. Old confirmations are expired and are never replayed. Successful deletion should show its private result and one shared receipt in bot-mod. The action ledger remains authoritative: `!mod actions 77161671133f429eb4e467a3a2d0c767`.

Protocol reference: Discord documents [deferred responses, initial deadlines and editing the original response](https://docs.discord.com/developers/interactions/receiving-and-responding). The original response to this bot's type-5 ephemeral deferral is its own reply, separate from the source staff review card.

Deployed September 22, 2026, verified at 13:12 UTC. Both required restarts completed healthy; moderation and Telegram are connected. Runtime imports and isolated self-test passed (9/9), and all 45 installed files match the reviewed archive. The full offline suite passed 460 core/setup + 220 integration tests (680). Both profile configurations, policy hashes, action flags and accounting match the pre-deployment snapshot `/tmp/mod-before-0514.json`. The reported incident still has zero action attempts; its old proposal is expired. The owner subsequently completed the live check at 13:28 UTC: the reported message has one staff deletion recorded as done, and the owner supplied both the private result and shared bot-mod receipt. No old confirmation was replayed.

Release artifacts:

- Archive `/tmp/liberdus-update-0.5.14-20260922.pyz`, SHA-256 `44280ab3e88e07074fe38cb72bde459e22f347d1928f98b5b260e15726271608`.
- Completed helper `/tmp/liberdus-apply-0.5.14-20260922.py`, SHA-256 `75be3f1d0f07f46d60e329daabac28cc1d4a21b57e8cadab09ac895022ef5438`; source `scripts/apply_interaction_recovery.py`. Do not rerun.
- Previous 0.5.13 plugin: `/home/hermes/.hermes/profiles/liberdus-mod/.liberdus-update-backup-hovcoq3j/liberdus-moderator`.

Source changes remain local, uncommitted and unpushed. Preserve earlier release artifacts and backups.

Read-only follow-up confirmed automatic deletion enabled, moderation connected and screening healthy. Current Discord metadata verified deletion permissions in all seven configured monitored channels. The retained ledger has three confirmed automatic deletions: one in general and two in earlier test-channel runs. Its one uncertain automatic attempt is also from an earlier test channel. The MANUAL-01 example was classified as suspicious_offer (model score 1.0), purpose promotion; it is outside the sensitive_request-only automatic rule and correctly required staff action. No settings or Discord permissions were changed by this follow-up.
