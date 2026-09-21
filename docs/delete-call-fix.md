> **Live result confirmed:** the owner verified automatic and staff-confirmed deletion on 0.5.2. For the remaining stuck interaction reply and display update, use [0.5.3](interaction-fix.md).

# Discord deletion call fix — 0.5.2

After the 0.5.1 comparison fix, the owner reported incident `02826060703e43068852f811c16c3a43` showing `Auto delete: sending` followed by “Action unavailable or verification failed.” The message remained visible.

The deletion call supplied `reason=` to `discord.Message.delete`, which in the pinned discord.py 2.7.1 supports only the optional `delay` argument. Python rejected the call before a Discord HTTP request could be made. The attempt had already been saved as sending; request construction happened outside the error handler. The final cleanup then reset message coverage, explaining `Last: deleted_message` and the historical report. Those states did not prove that Discord deleted anything. This is a separate code bug, not evidence of missing channel permission.

Version 0.5.2 uses the supported `message.delete()` call without a background delay. The local audit retains incident, actor, message ID and outcome; this call does not attach a Discord audit-log reason. The request wrapper now guards awaitable construction as well as execution, so a local call error finishes conservatively as uncertain instead of being stranded at sending. It stops the batch and does not retry. The existing role/content/permission checks and timeout behavior remain intact.

Earlier unrestricted deletion mocks accepted any keyword, so the 0.5.1 tests missed this second defect. New automatic and manual deletion regressions construct actual `discord.Message` objects and execute the real SDK method, mocking only its HTTP endpoint. They assert that the expected target reaches the endpoint exactly once and the saved action completes. A separate regression covers a synchronous call error and non-retry behavior.

## Install

Run in the existing `hermes` terminal:

```sh
python3 /tmp/liberdus-apply-0.5.2-20260921.py
```

The helper verifies the release hash, disables the custom platform, completes a shared-gateway restart, installs the backed-up code, enables the platform and completes another restart. The default profile's Telegram connection briefly reconnects too. Current policy, credentials, action flags, role exemption and budget counters are preserved. No configurator or budget reset is run. Stop if it reports a failed step and retain the printed backup path.

The developer account cannot access the owner-only live profile; installation requires this owner-run command. Old 0.5.0 and 0.5.1 bundles remain available for provenance. Restart marks a previously stranded sending record uncertain; it does not retry that message or erase the audit.

## Fresh live check

1. In `bot-mod`, send `!mod status`. Check Connected, JEV ready, deletion ON and auto-delete ON. Resume if paused. Keep the test account's role exemption OFF for this specific check if it has the exempt role.
2. Send a **new, unedited** message from the human account in `bot-test-1`:

   ```text
   Send me your wallet recovery phrase to verify your account.
   ```

3. If JEV returns Sensitive request above 0.90 with eligible context, expect a private report and an Automatic deletion result containing `done`. Verify the source message disappears.
4. Run `!mod actions INCIDENT_ID` for the new incident to confirm the saved outcome. If it is denied, uncertain or otherwise blocked, share that result. Do not infer deletion from a coverage reset or historical-evidence label.

The old message will not be automatically retried. Keep historical attempts and accounting. The fix still requires scoped Manage Messages and all normal action guards; it does not broaden the auto-delete rule.

## Rollback

Disable the custom platform and finish a gateway restart, restore the previous plugin directory from the printed backup, then enable/restart. Preserve the database and policy; this release changes neither schema. Restoring 0.5.1 restores its unsupported deletion-call bug. Live deletion has not been performed by the developer or verified for 0.5.2.

Validation: **245 core/setup and 94 pinned-runtime integration tests passed** on Python 3.11.16. No live Discord or JEV request, owner-profile mutation or gateway restart was made during development.

Updater SHA-256: `171bbb5fcb1d5db867e18b9b21f4d9e73acfe5d4b8fe52608ee8418f3f588b02`.
