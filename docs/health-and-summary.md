# Private health alerts and moderation summary — 0.5.5

Version 0.5.5 adds automatic private notices when live JEV screening loses coverage, plus `!mod summary` for saved counts and cost estimates. Neither feature makes a new AI request or changes moderation decisions, permissions, action flags, thresholds or spending limits. Existing automatic deletion remains limited to the previously configured sensitive-request rule.

## Install from the Hermes terminal

```bash
python3 /tmp/liberdus-apply-0.5.5-20260921.py
```

The owner-run helper verifies the staged update hash, disables the custom platform and waits for the shared gateway restart, backs up and updates the plugin, then enables it and waits for the final restart. The stopped updater checks the pinned runtime, new module imports and isolated 9/9 self-test. It preserves configuration, policy, keys, database, action/exemption flags, pause state and existing usage accounting. Older release bundles remain available.

After the helper reports completion, use these in `bot-mod`:

```text
!mod status
!mod summary
```

Status should show `Version: 0.5.5`. Summary is restricted to the same authorized operators and private command channels as the existing moderation commands. Health notices are automatic; there is no `!mod health` command. No repeated deletion test or new JEV batch is required for this update.

## Automatic health notices

The bot checks operational state every ten seconds while running. Notices apply to active, unpaused single-message JEV `report_only` screening; legacy shadow/off modes do not emit these alerts. They go to the configured private command channel (`bot-mod` in the pilot), with the existing narrow panel formatting and mention suppression.

| Condition | Notice behavior |
| --- | --- |
| Repeated failed or skipped live checks | Three recorded failures within five minutes open a screening warning. A successful applied check clears the failure streak. One ordinary failure alone does not send a warning. |
| Missing/rejected API key or denied access | Becomes eligible for a warning after the first failure because the worker suspends further provider calls. |
| Daily/lifetime call or allowance cap | Becomes eligible for a warning when another check cannot be admitted or reserved, without waiting for three failures. |
| Billing/accounting, storage or incident-capacity blocker | Becomes eligible for a warning as soon as the monitor detects the blocker. |
| Disconnect, queue overflow, worker stop, unavailable edit, invalid event or unfinished checks recovered at restart | Records an interruption. Discord cannot receive a notice while disconnected; the bot reports recorded gaps once it is connected, active and can access the private destination. |
| Recovery | Requires a valid live result applied to current evidence after the warning episode, with no remaining blocker or subsequent recorded failure. Merely reconnecting, waiting or restarting does not prove screening recovered. |

All notice types share a limit of **at most one delivery attempt per five minutes**. New reasons and gap counts are grouped into a notice; an unchanged warning is not posted repeatedly. “Immediate” eligibility for a cap or authentication failure does not bypass that interval. The monitor runs independently of message traffic, so shared allowance consumed by an evaluation can surface without a new Discord message.

Live screening outcomes drive failure/recovery notices, including recorded local-rate-limit skips. Evaluation batches do not count as live screening successes or failures. A restart that recovers unfinished running/awaiting-application screening attempts marks them uncertain and records an interruption for delivery after connection; a clean restart by itself stays silent. A successful provider response that becomes stale before application does not establish recovery. Missed messages are not backfilled, and the independent code rules are not presented as JEV coverage.

Saved health state contains bounded counters and fixed reason codes, not message text, member IDs, URLs, raw provider errors or credentials. Delivery is best effort: an interrupted or uncertain attempt is recorded and that notice is not automatically replayed. Existing pause, activation/policy checks and private-channel permission checks apply before delivery.

## Read `!mod summary`

The reply groups current switches, live screening counters, retained moderation history and screening costs. It uses saved data and produces no new AI request, assessment, deletion or timeout.

| Section | What its numbers mean |
| --- | --- |
| State | Current mode, connection state and deletion, auto-delete, timeout and role-exemption switches. |
| Live JEV lifetime counters | Recorded checked versions, unchecked events and role-exempt events. These are not unique messages and may overlap across edits or processing events. |
| Retained history | Currently retained incidents and action outcomes. Successful automatic and staff deletions are separate; already-absent, uncertain, sending and denied deletions are not counted as successful deletions. Completed timeouts are separate. |
| Staff assessment events | All retained Needs attention, Looks okay, Unsure and Dismissed records, including repeated reviews and historical revisions. This is not the current pending-review count or a count of unique incidents. Use `!mod pending` for the queue. |
| Live/evaluation cost split | Retained screening attempts and estimates from validated results, with separate conservative reservations for unknown costs. Live Discord screening and synthetic evaluation are shown separately. |
| Shared screening lifetime accounting | The shared call total and used/reserved allowance, including evaluation attempts and usage whose detailed rows have since been pruned. |

Retained history can shrink as older records are pruned. The lifetime counters do not become per-day statistics, and the live/evaluation retained split need not add up to shared lifetime totals. Legacy purpose-only shadow/batch calls and costs are excluded from this screening summary.

Cost scans examine at most 5,000 retained rows per source and bound each saved result read. Partial scans show `>=` lower bounds; unavailable values show `?`. Estimates and unknown reservations are not provider invoices, and an unknown charge is not silently treated as free. Requesting the summary does not prune records, initialize missing optional tables or change the allowance.

## Evaluation checkpoint

The owner supplied the focused `official_wallet_transfer` retry: a valid `impersonation` / `other` result at displayed score 0.91, routed to staff review, in 354 ms. That completes valid results for all 44 unique synthetic cases across the original baseline and separate retry. The historical baseline remains 43/44, including its original unknown-cost failure. See the [complete evaluation record](screening-evaluation.md#recorded-baseline-and-focused-retry--september-21-2026).

## Verification and remaining work

The owner subsequently supplied live status and summary replies showing version 0.5.5 and an active Discord connection. Both command paths are confirmed in that supplied output. Automatic health failure/recovery delivery was not exercised by those replies. The working Hermes deployment is retained; standalone hosting is a deferred nice-to-have in BUILD_PLAN section 10.39.

Automated checks use disposable stores/profiles and mocked Discord/JEV boundaries. They cover health grouping, thresholds, outage/recovery behavior, delivery uncertainty and summary scope/read behavior. Owner deployment is separate from those checks; no live outage or real provider call was induced to test this feature.

- **511 automated tests passed:** 359 core/setup and 152 integration tests against the pinned Hermes/Discord runtime. The focused health checks were rerun after the final notice wording change.
- Update bundle modules match the tested source byte-for-byte; ZIP integrity and module compilation passed.
- Update bundle SHA-256: `27e7da1ecf1e46775eda9c052ecc0040044f529770b80e737898c0187f4a4e1c`.

Keep the current private pilot and narrow automatic-delete policy. The synthetic results do not establish a general false-positive rate. Live timeout permission verification and any separately designed combined delete-and-timeout workflow remain outstanding; this update does not enable either.
