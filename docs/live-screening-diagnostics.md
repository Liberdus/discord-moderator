# Live screening diagnostics — 0.5.12

**Deployed September 22, 2026, around 12:35 UTC.** The owner reported that a recovery-phrase solicitation in general did not produce a flag. The original failure remains unexplained; this release adds the missing live diagnostics.

## Findings

The live attempt for message `1551930208448159747` started at 12:16:35 UTC and finished as `provider_or_response_error` in 564 ms, without a classification or incident. Moderation was unpaused, role exemption was off, budgets were available, and the Discord session had no recorded disconnect after the 12:03 restart. The subsequent deletion event cleared the message evidence. The failed provider response/exception was not retained, so the specific original cause cannot be recovered. Do not describe a network outage, malformed response, or deployment regression as established.

The owner also tested bot-test-1, which is outside the current seven-channel public monitoring scope. General is included. Repetition rules run separately from AI; deleting messages invalidates the evidence window. The previous UI/receipt releases had not changed screening, provider transport, validation, or deterministic rules.

The first proposed diagnostic was rejected by automatic approval review before execution because external payload submission and spending were not explicitly authorized. The owner then authorized running the investigation. Two bounded synthetic requests used the existing TypeSafe endpoint, rubric, profile key and accounting. Neither made a Discord connection or sent/deleted a Discord message:

| Saved run | UTC time | Result | Latency | Known token estimate |
| --- | --- | --- | --- | --- |
| `incident-check-20260922` | 12:26:10 | impersonation / other, score 0.94, staff-report candidate | 499 ms | $0.000039 |
| `seed-check-20260922` | 12:28:57 | sensitive_request / other, score 0.99, report and automatic-delete candidate assuming all live gates pass | 358 ms | $0.000038 |

The second request used the owner's exact text: `Send me your wallet recovery phrase to verify your account.` Both passed the production acceptance rules. These results show that the provider was responding at those times and the exact example can flag; they do not identify or fix the earlier failure. Total known estimate: $0.000077. The original failed request's unknown-charge reservation remains intact. Saved run names prevent replay of these diagnostic requests.

## Change

The existing detailed evaluation validator is now also used by live screening. It still delegates acceptance to the unchanged production validator. A separate `screening_failures_v1` table stores only a fixed diagnostic code alongside the existing attempt key. It distinguishes connection/TLS/read problems, timeouts, provider rejections, invalid response fields, and unexpected failures. It never stores arbitrary response bodies, exception messages, credentials, or an extra copy of message content. Diagnostics expire with their attempts through a foreign-key cascade.

`!mod status` now includes the last retained failed AI check's UTC time, fixed code and explanation. Older failures without a diagnostic explicitly say that details were not recorded. Later successful checks still show the older failure as historical. Existing outcomes, health-notice thresholds, budgets, unknown-charge reservations, deduplication, no-retry behavior, scope, flags, rubric, report decisions and deletion rules are preserved.

## Validation and deployment

New tests cover live invalid-response diagnosis, safe fixed codes without sensitive exception/response text, cancellation and reservations, duplicate suppression, historical errors, restart persistence, success after failure, authorized status output and cascading retention. The adapter integration test exercises a rejected response followed by a real command path with mocked network edges.

**655 offline checks passed:** 449 core/setup and 206 pinned-runtime integration tests. The first integration run exposed only a whitespace-sensitive assertion against wrapped display text; the corrected integration suite passed. The archive passed integrity, module compilation and exact tested-source equality checks.

Release artifacts:

- `/tmp/liberdus-update-0.5.12-20260922.pyz`: `f3225c158e5bf639de0246e29c44604efdcb4edbbb85dddeb7a817b2b71baaaa`
- `/tmp/liberdus-apply-0.5.12-20260922.py`: `ec3011f333783057b14caec13917c6670a35f59be85371a804e944bb083acacf`

The tracked fixed helper is `scripts/apply_live_diagnostics.py`. Deployment uses the existing stopped-plugin upgrade workflow and preserves the previous plugin directory. Earlier artifacts remain unchanged. Deployment confirmation follows below.

Both gateway restarts completed healthy. Installed-runtime imports and isolated self-test 9/9 passed. All 44 installed files match the pinned archive. Moderation and Telegram are connected. Both profile configuration files and policy are byte-identical to the pre-deployment snapshot; saved flags and accounting totals are unchanged. The new diagnostics table exists, and a read-only status check correctly displays the original failure as having no recorded detail. No additional live Discord tests or provider calls were made during deployment. The previous 0.5.11 code is retained at `/home/hermes/.hermes/profiles/liberdus-mod/.liberdus-update-backup-_1o9bwsq/liberdus-moderator`. Do not rerun the completed updater.
