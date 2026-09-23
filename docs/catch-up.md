# Message catch-up — 0.8.0

When JEV single-message screening is enabled, the moderator checks a bounded
slice of recent history after a Discord disconnect or an unexpected process
restart. It works after both a resumed session and a new session. No additional
IDs, keys or Discord command registration are needed.

Recovered concerns appear in the usual private moderation cards with
**Recovered after disconnect · Staff review only**. They never qualify for
automatic deletion, even at a score of 0.90 or higher. Staff can assess, dismiss,
or use Delete to fetch the current message and confirm deletion. Sender IDs,
saved excerpts, action records and deletion receipts work as before.

## Limits and progress

| Setting | Limit |
| --- | --- |
| Lookback | Most recent five minutes, starting from saved channel progress |
| Overlap | Two seconds before saved progress to cover boundary events |
| Per channel | 100 history entries per recovery |
| Across all monitored channels | 200 history entries per recovery |
| Unfinished recovery | Stops accepting work more than ten minutes after reconnect |

These are fixed limits in this release. Entries already checked, bots and other
excluded messages count toward the history limits. One extra history entry can
be fetched to detect truncation. If the outage exceeds the lookback, or a limit,
permission failure, request failure or budget prevents completion, the bot records
**Message catch-up incomplete; some messages may remain unchecked**. This uses
the existing private health notice and its five-minute cooldown. It can appear
even after a brief resume whose ordinary disconnect notice was suppressed.

Use `/mod status` (or `!mod status`) to see Ready, Running, Complete or Incomplete
and the checked/skipped counts for the latest recovery. Counts are not a promise
that all Discord messages were screened. Skips include exclusions, duplicates
and failed checks; incomplete coverage remains visible. `/mod connection` still
shows the original connection events and durations.

## Startup and interruptions

The bot saves progress independently for each selected channel. The first start
after installing this release establishes a baseline and does not scan old
history. A clean service stop/restart also establishes a new baseline. Pausing,
changing policy or changing relevant action settings discards pending recovery;
resuming does not scan messages posted during the pause.

An unexpected stop leaves a durable running marker. On the next successful
connection, the bot resumes from saved progress within the limits above. A
second disconnect during catch-up preserves completed entries, remaining work
and the original message caps. Progress is committed after each terminal result.

Every disconnect still clears the live detection window and invalidates
in-flight screening and deletion confirmations. Recovery uses a separate,
bounded evidence store. It does not restore old confirmations or mark an
interruption as continuous coverage.

## Scope, cost and privacy

Catch-up uses the configured monitored channels and the same current channel,
category, permission and role-exemption checks. It fetches the current message
again before screening, and fetches current membership when exemptions apply.
It does not replay staff commands, code-rule spam windows, threads, attachments,
bots or webhook messages.

Recovered requests use the existing JEV worker, rate limits, call caps and
spending allowance. Catch-up waits for queued live work and admits one recovered
message at a time. History requests run separately from live message processing;
a provider call already in progress can still delay the next live JEV request.

A retained ledger binds a message's content version to its policy and rubric.
Role metadata differences between gateway and history responses do not cause
duplicate screening of unchanged content. Previously attempted versions,
including uncertain or failed requests that may have been charged, are not
automatically retried. Editing the content produces a different version.

Recovery progress contains channel IDs and cursors; recovered text stays in the
private instance database under the existing retention policy. Recovery snapshots
are capped at the smaller of 2,000 and the configured message capacity, and are
pruned when new evidence is processed. Incident markers and deduplication entries
are pruned with their parent incident/attempt records. No new secrets or raw
message/provider logging is introduced.

This first version recovers **new messages created during the interruption**, with
the small overlap above. It cannot retrieve a message deleted before the history
fetch, or discover all edits to messages created before that interval. It does not
guarantee complete coverage of a busy channel or prolonged outage.

Install the wheel and restart the active VPS using the
[existing-service update instructions](standalone.md#update-an-existing-06x-or-07x-system-service-to-080).
Keep the old Hermes moderation profile disabled.
