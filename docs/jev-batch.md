# JEV batch evaluation

The standalone **context-v1** runner evaluates ten synthetic examples with the real JEV API and prints one narrow summary. It is ready for the owner to run; no live batch results have been supplied yet.

It reuses the plugin's incident request builder, pinned model, rubric, transport and typed-response validator. Each example becomes a disposable three-channel repeat incident in memory. Expected labels and test names never enter the provider request. No real Discord messages are read, sent or changed.

## Run from the existing hermes terminal

The existing liberdus-mod JEV shadow setup and stored TypeSafe key are sufficient. The installed moderator can keep running. No plugin installation, browser session, additional bot, gateway restart or new key is needed.

Run the full batch:

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

The default run name is baseline. Repeating the run command reuses attempts already recorded for that name and request hash, including failures and interrupted attempts. Only previously unattempted cases can make new calls. If a run was interrupted or hit the daily limit, this is the resume command; it does not restart the entire paid batch.

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

Exit status is 0 when all ten examples match, 1 when all return valid results but at least one needs label review, 2 for incomplete/failed/stopped runs, and 130 for an interrupted command. Hermes or another terminal runner can use those statuses and the JSON summary. A label disagreement does not stop the remaining examples; a provider, timeout or response-validation failure does.

## Shared spending limits and state

Each attempt reserves 2,753 microusd before provider I/O. Ten new attempts reserve at most **27,530 microusd ($0.027530)**. This is conservative local accounting, not the expected invoice. Known token estimates are shown separately; failed requests can have unknown charges. Reservations are never refunded or automatically retried.

The runner uses the existing profile's daily and lifetime call/spending caps, shared with live JEV incident evaluation. It atomically updates the same budget counters and last-attempt timestamp. It cannot create another allowance by changing run names or restarting. Under the current $0.05/day cap, fewer than ten attempts may remain after other live activity; a partial run is expected if the budget is exhausted. Do not delete state or reset counters to work around the cap.

Consequently, !mod status includes batch attempts in its AI-attempt total. Batch results live in a separate jev_batch_attempts_v1 table inside the existing profile state/moderation.sqlite3. They do not create live moderation incidents or appear in !mod incident. Use the batch results command to inspect them. The batch table retains at most 1,000 records and has no automatic pruning that would turn a saved case into a new paid attempt.

The helper makes short SQLite transactions outside network waits. It does not construct the moderation engine on the live database, change evidence or reports, reset the live worker's in-flight attempts, modify the policy, or restart the gateway. A separate owner-only lock prevents two batch runners from running together. An unexpectedly oversized provider usage report activates the existing billing guard for both batch and live JEV calls.

New calls require the loaded and stored policies to agree, JEV shadow mode enabled, moderation unpaused, and budget/billing guards clear. A policy/pause change during a call makes its result stale; typed usage is retained but the label is discarded. A provider failure stops that invocation, and the saved attempt is not automatically retried. Fix the problem before starting another invocation.

The key is read only from the owned, private liberdus-mod/.env file, without environment interpolation or default-profile/shell fallback. Never put a key in the command, a Discord post or a report.

## What this verifies

This is live JEV classification of synthetic incident inputs. It does not verify Discord message delivery, permissions, edits/deletions, reconnects, human-account authorization or general classification accuracy. The existing self-test and previously recorded live Discord tests cover other layers. A dedicated Discord test bot remains separate future work; all current bot exclusions are unchanged.

The developer account cannot access the real hermes profile. Development used synthetic profiles, mock provider replies and blocked external network edges. Real batch performance and disagreements remain pending the owner's run.

## Build and validation

Rebuild the standalone helper from the repository:

```bash
python3 scripts/build_jev_batch.py \
  --output /tmp/liberdus-jev-batch-20260920.pyz
```

This packages source only; no profile, policy, token or database is included. The helper is independent of the installed 0.3.3 plugin, so deploying it does not require replacing or restarting the plugin.

The 148 core/setup tests pass on Python 3.11.16 and 3.12.3; 35 integration tests pass with the pinned Hermes runtime and mocked/blocked external network edges. Batch-specific coverage includes shared budget exclusion in both directions, matching/disagreement results, cached runs, readonly results, interruption/crash/timeout accounting, daily rollover, profile/policy boundaries, key isolation and packaged preview behavior.
