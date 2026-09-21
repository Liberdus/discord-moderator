# Deletion comparison fix — 0.5.1

Discord gateway messages carry member roles. A later REST message fetch can return a User without those roles, even when the message is unchanged. Version 0.5.0 compared the complete event hash and treated missing roles as an edit. The owner observed the Sensitive request report followed by “Message changed since the report”; this refusal occurs before a deletion request. It is not a Discord permission-denial response.

Version 0.5.1 compares saved and fetched message text, guild/channel/message/author IDs, creation/edit times, and bot/webhook/thread/attachment flags separately from membership. A real difference still blocks the action; the notice names the differing fields without printing their contents. Stored event hashes, historical evidence, policy and JEV provenance are unchanged.

When automatic deletion has a configured role exemption enabled, it fetches the author's current membership before acting. An exempt role or unverifiable membership blocks deletion. With exemption OFF, a member lookup is not required for deletion. Manual deletion remains an explicit staff decision. Timeout still fetches the member and applies its existing protected-member, permissions and hierarchy checks. Scope, pause, duplicate prevention and strict score-above-0.90 sensitive-request rules remain in place.

## Install from the hermes terminal

```sh
python3 /tmp/liberdus-apply-0.5.1-20260921.py
```

The hash-pinned helper disables the custom moderation platform, waits for a healthy shared-gateway restart, installs the backed-up code update, then enables the platform and waits for another healthy restart. The shared restart also briefly reconnects the default profile's Telegram connection. It stops after any failed step. Keep the printed backup path; do not run subsequent steps manually after a failure without checking its output.

The updater preserves the current moderation policy, credentials, database, action switches, role exemption and budget accounting. It does not run the earlier action configurator or reset flags. Only the platform enabled setting is toggled for the restart sequence. Restart invalidates old live evidence; historical incidents are not automatically retried. The developer account cannot access the hermes-owned runtime, so this helper must be run by its owner.

## One fresh live check

1. In `bot-mod`, send `!mod status`. Check Connected, deletion ON, auto-delete ON and JEV ready. If paused, use `!mod resume`. The owner's last supplied state had role exemption OFF and timeout OFF; those switches should remain as set.
2. As the human test user, send this **new, unedited** plain-text message in `bot-test-1`:

   ```text
   Send me your wallet recovery phrase to verify your account.
   ```

3. Expect a private report. If JEV returns Sensitive request above 0.90 with an eligible purpose, expect an Automatic deletion notice containing `done`, and the source message should disappear.
4. In `bot-mod`, run `!mod actions INCIDENT_ID` using the new incident ID. Expect a saved automatic deletion attempt marked `done`. A lower/different JEV result may produce only a report; do not infer a permission problem from that.

Do not reuse the old incident for this auto-delete check. If the action is blocked, share its new notice and audit output. The fix does not remove genuine edit/permission/exemption guards. Discord offers no conditional-delete operation, so a final fetch-to-action race remains.

## Validation and rollback

**Validation:** 241 core/setup tests and 90 pinned Hermes/Discord integration tests passed on Python 3.11.16.

Development checks use synthetic profiles, real Discord SDK message construction and mocked mutation/provider endpoints. They do not prove live deletion and make no Discord posts, paid JEV requests or live service changes. Core, adapter and updater checks include missing REST role metadata, real edits, role exemption and lookup failure, pause during membership fetch, protected timeout targets and preservation of the active 0.5.0 profile.

For rollback, disable the custom platform and finish the gateway restart, restore the previous plugin directory from the updater's printed backup, then enable/restart. This release changes no policy or database schema; preserve the database and accounting. Restoring 0.5.0 also restores its known false-comparison bug.

Staged updater SHA-256: `abf3a8c8a5de0784c3b29021f156845a304d0ba7e4bac7fa9481488e37bc2f09`. The helper checks this before changing configuration.
