# Human review buttons and saved message evidence

**Current release: 0.3.5 prepared.** The [staff assessment runbook](staff-assessment.md) supersedes the older interaction and installation instructions below. It adds Needs attention / Looks okay / Unsure, report refreshes and `!mod pending`; old content labels remain distinct. The following sections preserve the earlier release record.

Version **0.3.4**, September 21, 2026. Implemented and tested with synthetic profiles and the pinned Hermes/Discord SDK; installation and live Discord button/display checks are still pending. The last owner-confirmed live version is 0.3.3.

## Review an incident in bot-mod

New automatic reports and `!mod incident ID` / `!mod explain ID` replies offer three buttons:

- **Promotion**: the message contains an endorsed offer or sales pitch.
- **Not promotion**: the message is a notice, discussion, quotation or warning without an endorsed offer.
- **Unsure**: context is insufficient to decide.

These are human context labels. A promotion may be permitted; a repetition rule may still flag a non-promotional message. None of these buttons approves enforcement, dismisses a rule incident, grants an exception, changes JEV's saved answer or retrains it.

Only a configured numeric operator user in the configured private command channel can save a review. Discord supplies the actor identity. Clicking gives that operator a private confirmation. A subsequent incident lookup shows the latest human label, reviewer ID, UTC review time and reviewed revision separately from the saved JEV result. The original report is not edited automatically. A later click appends a correction; earlier reviews remain in the local audit history.

The incident panel retains its 32-column ASCII layout. Below it, **Saved text preview** shows the first distinct saved message text, up to 500 UTF-16 units after escaping. Whitespace/control characters are normalized, Markdown is escaped, and mentions and text URLs are neutralized. Original evidence remains unchanged in SQLite. **Open message 1**, **Open message 2**, etc. are explicit clickable Discord source links outside the code block. The view includes up to three links and states when texts or links are omitted. Automatic reports show up to five links. Total replies stay within 1,900 UTF-16 units, leaving room under Discord's content limit. Source text may have been edited or deleted since capture; opening a source link still requires the viewer's Discord access.

This uses [Discord buttons](https://docs.discord.com/developers/components/reference) with ordinary message content and [supported Markdown](https://support.discord.com/hc/en-us/articles/210298617-Markdown-Text-101-Chat-Formatting-Bold-Italic-Underline). Buttons and links stay outside the code block. Message sending suppresses mentions and embeds. Saved evidence is displayed only in the approved private channel; this adds no public posting permission.

## Install on db2

Run this from the existing **hermes** terminal:

```bash
python3 /tmp/liberdus-apply-0.3.4-20260921.py
```

The fixed helper verifies the staged updater hash, disables the custom platform, waits for a confirmed shared-gateway restart, installs 0.3.4, enables the custom platform and waits for the second restart. It stops if a step fails. The shared gateway also serves Telegram, which may briefly reconnect. Keep the printed plugin backup path. Run the helper once; an already updated version is deliberately refused by the updater.

The updater accepts 0.3.0 through 0.3.3, requires the pinned runtime and released moderation lock, checks imports and the isolated 9/9 self-test, and preserves policy, configuration, credentials, database and classifier budget counters. Its underlying bundle is `/tmp/liberdus-update-0.3.4-20260921.pyz`. The developer account cannot access the live Hermes profile, so this installation step must run in the owner terminal.

After installation, use the existing incident in `bot-mod`:

```text
!mod incident b48ee236ff774e0f8efec35caa8ba447
```

Read its saved warning text and open one source link. Click **Not promotion** if that describes the saved text. Expect a private `Moderator review saved` confirmation, then run the lookup again to see the human review beside JEV's saved `Quoted warning`. Historical evidence is expected after expiry/restarts. If this incident has passed retention, choose another retained incident ID from a report.

Messages already sent by 0.3.3 will not acquire buttons automatically; a fresh incident lookup supplies them. No new test-channel posts, JEV API key or paid evaluation are needed. The buttons themselves make no AI call, alter no classifier counters, and leave enforcement disabled. Already-configured shadow evaluations for unrelated new incidents can still occur under existing caps. A preserved pause stays paused; human review remains available while paused.

## Revisions, recovery and limits

A review is bound to the report or incident view's exact saved revision and evidence hash. A reconnect or edit during sending cannot silently retarget its buttons to a later revision. If evidence is changed, expired, closed or reset, the review is shown as **HISTORICAL**. Only an open, unexpired incident with the same revision, evidence and policy can show **CURRENT**; this is a saved-state check, not a new Discord fetch. Missing or pruned evidence cannot be reviewed.

Successful bot message IDs are recorded locally. A button identifier chooses only the label; the incident/revision comes from that recorded message binding, never from quoted text. Bindings and audit records survive process restarts. The pinned Discord SDK sends component interactions to the client's interaction handler even without retained in-memory per-message callbacks. Checks cover this handler and SQLite recovery; a real gateway restart with deployed buttons remains a live acceptance check.

Clicks must be acknowledged before they can be queued. The existing serialized worker rechecks private channel access and command authorization, bounds queue wait to 30 seconds at execution, and shares the one-response-per-second operator limit. Busy, expired or rate-limited clicks do not save a review; retry after the notice. A disconnect may discard a queued click, so inspect the incident before retrying an unconfirmed operation. The same interaction ID cannot repeat a mutation while its receipt is retained. A lost confirmation does not roll back or automatically retry a stored review.

The optional `moderator_reviews_v1` table retains at most 32 review entries per incident. `review_prompt_links` retains at most 5,000 private view bindings. Both follow incident deletion through foreign keys, including the existing seven-day incident retention policy; command receipts remain bounded to 5,000. An unavailable binding requires a new incident lookup. A full review history refuses further entries rather than overwriting the audit trail.

Text-command fallback is also available: reply to a recorded bot report/view with `!mod review promotion`, `!mod review not-promotion` or `!mod review unsure`. An explicit command is `!mod review INCIDENT_ID REVISION LABEL`. It has the same authorization and saved-revision requirements. No free-form moderator message is sent to Hermes or JEV.

## JEV's role and next work

Code rules continue detecting repeated messages. JEV adds a context label to an already-detected incident in shadow mode. A moderator compares that suggestion with the saved text and records a judgment with the buttons. Disagreements become evidence for later rubric evaluation; no automatic training, suppression or enforcement is added.

The live rubric remains **context-v1**. The completed **precedence-v2** comparison remains experimental with its unresolved fragment, preserving the 9/10 baseline and 4/5 candidate results. No extra paid comparison or budget reset is part of this release. After the live display/button check, collect useful human-reviewed examples before choosing another rubric change.

Validation: 169 core/setup tests pass on Python 3.11.16 and 3.12.3; 44 integration tests pass with the pinned c1488 Hermes source and discord.py 2.7.1, with external network edges blocked/mocked. Coverage includes authorization, spoofed/unknown message bindings, duplicate clicks, private acknowledgements, timeout/permission failures, saved-revision recovery, text escaping and size bounds, unchanged JEV records/counters, retention, and configuration-preserving updates. This is not evidence of a completed live deployment.

## Build and rollback reference

The staged updater SHA-256 is `bacfc7e3593b0cb0a1a0b83d8fc2a93ff24436c496e7bdb225b3e554b83726de`; the apply helper pins these exact bytes. Bundle ZIP timestamps mean a rebuild may have another hash. The optional tables are added when the updated runtime starts or the first review is written, not by opening the live database during the code update.

If temporary artifacts are lost, build a new updater from the reviewed 0.3.4 checkout using `scripts.build_pilot_bundle.build(root, root / "examples/config.toml", target, update=True)`. The packaged example policy is unused by the code updater; the actual existing policy is validated and retained. Give the rebuilt artifact a new path, verify it, then use the disable/restart → update → enable/restart sequence rather than the old helper with its mismatching pinned hash.

For rollback, disable the custom platform and finish the gateway restart before restoring the old plugin directory from the printed `plugin_backup`. Retain the newer directory and database for audit. Version 0.3.3 ignores the new optional review tables; leave their data intact. Keep stock Discord disabled, and enable/restart the restored custom plugin only after checking its directory. Buttons on existing Discord messages require the 0.3.4 handler and will not work while rolled back.
