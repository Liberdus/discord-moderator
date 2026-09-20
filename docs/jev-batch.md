# JEV batch evaluation

The standalone runner evaluates synthetic examples with the real JEV API and prints one narrow summary. The original **context-v1** baseline contains ten examples. The owner has completed the baseline: ten valid responses, nine matches and one mixed-purpose review case. The [recorded results](#baseline-results--september-20-2026) below supersede the earlier pending-run status. No repeat run is needed.

It reuses the plugin's incident evidence builder, pinned model, transport and typed-response validator. The explicit suite selects either the original rubric or the candidate described below. Each example becomes a disposable three-channel repeat incident in memory. Expected labels and test names never enter the provider request. No real Discord messages are read, sent or changed.

## Next: focused precedence-v2 comparison

The selected clarification is implemented as an **experimental batch rubric**, with five fixed examples. It has not been promoted to the live Discord classifier. The existing 0.3.3 plugin continues using context-v1 in shadow mode. No plugin update, restart, new key or Discord posting is required for this comparison.

The candidate gives an explicit offer endorsed by the author priority as **promotion**, even inside an announcement or warning. Quoted, described or negated sales language without endorsement does not count as a promotion; a warning remains **quoted_warning**. Community notices without an endorsed offer remain **announcement**, and genuinely indeterminate fragments remain **unclear**. These labels describe apparent purpose, not permission or a moderation verdict.

| Targeted example | Expected under precedence-v2 |
| --- | --- |
| Original maintenance notice plus sales pitch | promotion |
| Original community meeting notice | announcement |
| Original quoted scam warning | quoted_warning |
| Warning followed by the author's own sales pitch | promotion |
| Forwarded fragment with insufficient context | unclear |

The first three reuse baseline text. The mixed example intentionally has a different expected label under the revised definition; the original context-v1 expectation remains unclear and its 9/10 score remains intact. The final two are new contrast cases. This is a targeted check of the revised definition using historical reference cases, not a paid rerun of both rubrics or evidence of higher general accuracy.

From the existing hermes terminal, run the new staged helper:

```bash
python3 \
  /tmp/liberdus-jev-batch-v2-20260920.pyz \
  run --suite precedence-v2
```

Its default run name is precedence-v2. It makes at most **five new attempts**, with a maximum conservative reservation of **$0.013765**, using the same profile key, model and shared caps. It normally takes about 30 seconds with the current six-second interval, subject to live-worker activity. Results appear in one narrow summary; compare expected/actual labels and retain disagreements for review. Real candidate results remain pending the owner's run.

At the reported 13-call checkpoint, five more reservations fit within the current $0.05 daily cap; additional live calls may leave less room. If the daily budget stops a partial run, use the **same command and run name** after the next UTC day begins. Saved attempts are reused, and only cases that have never been attempted can spend again. A lifetime cap or billing guard requires separate review; waiting for midnight does not clear those limits.

To see the exact candidate wording and examples without profile access or calls, replace run with preview. To see saved results without a key read or new calls:

```bash
python3 \
  /tmp/liberdus-jev-batch-v2-20260920.pyz \
  results --suite precedence-v2
```

Add --json for complete hashes, recorded expectations and timing. Candidate rubric hash: `73adf752254deaf5cbfc57b33a7411d0680cefde8a8c50106a36cbc127a7e626`. The summary identifies the selected suite. Every attempt is bound to its exact request hash, suite, rubric hash and expected label. Saved results whose provenance does not match are unavailable and are not retried automatically. The original ten payload hashes are unchanged, so the new helper can also inspect context-v1 history with --suite context-v1.

Review the new output before promoting this rubric to the incident worker. No live policy or saved incident label is changed by this comparison. Enforcement stays disabled.

## Completed baseline: commands for reference

The existing liberdus-mod JEV shadow setup and stored TypeSafe key are sufficient. The installed moderator can keep running. No plugin installation, browser session, additional bot, gateway restart or new key is needed.

The completed baseline used this command, retained for reference and resuming incomplete runs:

```bash
python3 /tmp/liberdus-jev-batch-20260920.pyz run
```

With the current six-second interval, a full batch normally takes about a minute, plus any waiting for the live worker's shared rate limit. The runner stops admitting work after 180 seconds; an already started request can finish within its configured timeout. Progress appears as cases finish, followed by the summary.

To inspect the examples without reading the profile or making calls:

```bash
python3 /tmp/liberdus-jev-batch-20260920.pyz preview
```

To inspect saved results without accessing a key or making calls:

```bash
python3 /tmp/liberdus-jev-batch-20260920.pyz results
```

Add --json for structured results:

```bash
python3 /tmp/liberdus-jev-batch-20260920.pyz results --json
```

For context-v1, the default run name is baseline. Repeating the run command reuses attempts already recorded for that name and request hash, including failures and interrupted attempts. Only previously unattempted cases can make new calls. If a run was interrupted or hit the daily limit, this is the resume command; it does not restart the entire paid batch.

To deliberately buy a fresh comparison later, choose a new run name:

```bash
python3 /tmp/liberdus-jev-batch-20260920.pyz run --run-id comparison-2
```

A new name still shares the same total/daily budget. Do not change names merely to resume an incomplete run.

## Examples and interpretation

| Example | Expected purpose |
| --- | --- |
| Community meeting notice | announcement |
| Referral offer | promotion |
| Quoted phishing warning | quoted_warning |
| Development support question | other |
| Maintenance notice mixed with an unrelated offer | unclear |
| Instructions attempting to override the classifier | unclear |
| Spanish maintenance update | announcement |
| Vietnamese scam warning | quoted_warning |
| Ordinary conversation | other |
| Spanish course promotion | promotion |

Labels are initial human judgments, especially for mixed or adversarial examples. REVIEW means the returned purpose disagrees with the expected purpose; inspect the example and rubric before deciding whether the model or annotation needs correction. Confidence is a model score, not a correctness guarantee.

The summary shows expected/actual labels, MATCH or REVIEW, confidence, latency, valid responses, new attempts, conservative reservations and known token-based estimates. Structured results also retain request/rubric/policy hashes, timestamps, token usage and the expected label recorded at evaluation time. Model results are validated again before display. Raw provider prose and errors are not stored or shown.

Exit status is 0 when all examples in the selected suite match, 1 when all return valid results but at least one needs label review, 2 for incomplete/failed/stopped runs, and 130 for an interrupted command. Hermes or another terminal runner can use those statuses and the JSON summary. A label disagreement does not stop the remaining examples; a provider, timeout or response-validation failure does.

## Shared spending limits and state

Each attempt reserves 2,753 microusd before provider I/O. Ten new attempts reserve at most **27,530 microusd ($0.027530)**. This is conservative local accounting, not the expected invoice. Known token estimates are shown separately; failed requests can have unknown charges. Reservations are never refunded or automatically retried.

The runner uses the existing profile's daily and lifetime call/spending caps, shared with live JEV incident evaluation. It atomically updates the same budget counters and last-attempt timestamp. It cannot create another allowance by changing run names or restarting. Under the current $0.05/day cap, fewer than ten attempts may remain after other live activity; a partial run is expected if the budget is exhausted. Do not delete state or reset counters to work around the cap.

Consequently, !mod status includes batch attempts in its AI-attempt total. Batch results live in a separate jev_batch_attempts_v1 table inside the existing profile state/moderation.sqlite3. They do not create live moderation incidents or appear in !mod incident. Use the batch results command to inspect them. The batch table retains at most 1,000 records and has no automatic pruning that would turn a saved case into a new paid attempt.

The helper makes short SQLite transactions outside network waits. It does not construct the moderation engine on the live database, change evidence or reports, reset the live worker's in-flight attempts, modify the policy, or restart the gateway. A separate owner-only lock prevents two batch runners from running together. An unexpectedly oversized provider usage report activates the existing billing guard for both batch and live JEV calls.

New calls require the loaded and stored policies to agree, JEV shadow mode enabled, moderation unpaused, and budget/billing guards clear. A policy/pause change during a call makes its result stale; typed usage is retained but the label is discarded. A provider failure stops that invocation, and the saved attempt is not automatically retried. Fix the problem before starting another invocation.

The key is read only from the owned, private liberdus-mod/.env file, without environment interpolation or default-profile/shell fallback. Never put a key in the command, a Discord post or a report.

## What this verifies

This is live JEV classification of synthetic incident inputs. It does not verify Discord message delivery, permissions, edits/deletions, reconnects, human-account authorization or general classification accuracy. The existing self-test and previously recorded live Discord tests cover other layers. A dedicated Discord test bot remains separate future work; all current bot exclusions are unchanged.

The developer account cannot access the real hermes profile. Development used synthetic profiles, mock provider replies and blocked external network edges. The owner subsequently supplied the completed baseline below. This confirms real-provider execution for these examples; other live Discord and general accuracy claims remain outside this batch.

## Build and validation

Rebuild the standalone helper from the repository:

```bash
python3 scripts/build_jev_batch.py \
  --output /tmp/liberdus-jev-batch-v2-20260920.pyz
```

This packages source only; no profile, policy, token or database is included. The helper is independent of the installed 0.3.3 plugin, so deploying it does not require replacing or restarting the plugin.

The 155 core/setup tests pass on Python 3.11.16 and 3.12.3; 35 integration tests pass with the pinned Hermes runtime and mocked/blocked external network edges. Batch-specific coverage includes shared budget exclusion in both directions, matching/disagreement results, cached runs, readonly results, interruption/crash/timeout accounting, daily rollover, profile/policy boundaries, key isolation and packaged preview behavior. Candidate checks also verify baseline preservation, request/rubric binding, input size after substitution, separate cached results, shared-cap resume, and read-only results after JEV is switched off.

## Baseline results — September 20, 2026

The owner supplied the terminal summary from `run`, suite context-v1, run name baseline. All ten cases returned `ok`; nine matched the initial expected labels. The scores below are the displayed, rounded model scores from that summary.

| Example | Expected | Actual | Displayed score | Recorded latency |
| --- | --- | --- | --- | --- |
| Community event | announcement | announcement | 1.00 | 472 ms |
| Referral offer | promotion | promotion | 1.00 | 309 ms |
| Quoted scam warning | quoted_warning | quoted_warning | 0.99 | 275 ms |
| Support question | other | other | 1.00 | 300 ms |
| Mixed purpose | unclear | promotion | 0.61 | 286 ms |
| Adversarial instructions | unclear | unclear | 0.46 | 340 ms |
| Spanish update | announcement | announcement | 1.00 | 299 ms |
| Vietnamese warning | quoted_warning | quoted_warning | 1.00 | 280 ms |
| Ordinary conversation | other | other | 1.00 | 288 ms |
| Spanish promotion | promotion | promotion | 1.00 | 325 ms |

Mean recorded latency is 317.4 ms, median 299.5 ms, range 275–472 ms. The summary reports ten new attempts, $0.027530 reserved and a known token-based estimate of $0.000232. The shared total is 13 calls, consistent with the three earlier incident evaluations plus ten batch attempts. No authenticated provider invoice or separate timing trace was supplied.

**Review case:** the input combines a community maintenance notice with an explicit request to buy an unrelated subscription using a referral code. The current rubric describes promotion as promoting an offer and unclear as including mixed purpose, so both criteria apply. Promotion at displayed confidence 0.61 is a plausible reading, but it disagrees with the initial expected label. Preserve this as REVIEW and keep the baseline at 9/10; neither the original expectation nor the context-v1 rubric has been changed.

**Recommendation made at the baseline checkpoint:** clarify precedence for mixed content. An explicit endorsed sales pitch could take the promotion label even when packaged with an announcement; quoted or negated sales language in warnings should remain quoted_warning. Genuinely ambiguous intent can remain unclear. The owner subsequently selected this distinction; the precedence-v2 batch candidate above is now implemented and awaits a real-provider comparison before any live adoption. Do not relabel the baseline merely to make every case match, and do not use the displayed confidence as a calibrated decision threshold.

The adversarial example returned unclear rather than the announcement requested inside the text; the multilingual examples also matched in this run. Those observations do not establish general prompt-injection resistance, language coverage or a 90 percent production accuracy rate. Keep JEV advisory in shadow mode and enforcement disabled. Automatic report annotations and Discord test-bot automation remain future changes.

This is operator-supplied live-provider evidence. The developer updated documentation only; no key/profile access, provider call, Discord message or gateway restart accompanied this checkpoint.
