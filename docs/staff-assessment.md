# Staff assessments and pending reviews

Version **0.3.5**, September 21, 2026. Owner-supplied output confirms the display, named reviewer, Needs attention remaining pending, and Looks okay completing a review and reducing the queue from 8 to 7. No repeat installation is needed. The later [0.4.0 screening update](message-screening.md) retains this staff workflow.

## What staff see and do

Reports and incident lookups ask **Staff assessment - does this need attention?** The buttons mean:

| Choice | Meaning | Staff review status |
| --- | --- | --- |
| Needs attention | There may be a moderation issue. | Pending follow-up. |
| Looks okay | The saved message seems acceptable in context. | Complete for that evidence. |
| Unsure | More context or another opinion is needed. | Pending. |

Only configured numeric operators in the private command channel can save an assessment. A click records the choice, reviewer ID, UTC time, saved revision, evidence hash and policy hash. It then attempts to refresh the clicked bot message and the latest successfully delivered private report for the incident. The report shows the assessment and a non-notifying reviewer mention, which Discord can display as the member's name. At most two messages are edited per assessment; older report copies are not all rewritten.

The operator receives a private confirmation. If fetching or editing a report fails, the assessment stays saved, and the confirmation explains that the display could not be refreshed. There is no replacement post or automatic replay of the assessment. A fresh incident lookup always reads the saved state.

**Looks okay completes the staff review, not the code-rule incident.** No choice deletes a message, warns a member, grants permission, dismisses the rule evidence, changes JEV's answer, trains a model or enables enforcement. A permitted promotion may look okay; the overall staff assessment is separate from JEV's content category. Needs attention and Unsure stay pending until staff change the assessment. Recording a separately resolved moderation action remains future work.

## Commands in bot-mod

- `!mod pending` lists pending staff reviews, five per page. Needs attention appears first, then Unsure, then other pending items, including unreviewed incidents. `!mod pending 2` opens the next page. The list covers retained, valid evidence in the configured scope, even if the detection window has expired; it is not the outgoing report-delivery queue.
- `!mod incident ID` shows the compact summary, JEV suggestion, staff status and saved evidence, with the three assessment buttons.
- `!mod explain ID` includes the technical JEV fields and any legacy content review, subject to the same message-size bound.
- Reply to a recorded bot report/view with `!mod assess needs-attention`, `!mod assess looks-okay` or `!mod assess unsure` as a text fallback. Explicit form: `!mod assess INCIDENT_ID REVISION LABEL`.

The layout uses two narrow code boxes. The first contains the summary and saved JEV context; the second contains literal saved message text and full incident/author IDs. Punctuation is plain, rather than Markdown-escaped inside the code box. Backticks, mentions, control characters and text URLs are neutralized for display without rewriting original evidence. Portrait wrapping accounts for wide characters; total content stays within 1,900 UTF-16 units. The first distinct text is previewed, with up to three clickable source links outside the boxes and a note about truncation, normalization or differing texts. Messages may have changed since capture. The question and brief button explanations sit immediately above the buttons.

No new provider call is made for an assessment, queue listing, lookup or report refresh. Already-enabled JEV shadow processing for new rule incidents remains subject to its existing caps. The live rubric stays context-v1 and the experimental precedence-v2 comparison remains separate.

## Revisions and previous feedback

Each click stays bound to the displayed revision. Completion is checked against the evidence and policy hashes, so an expiry, pause or reconnect that retains the same saved messages does not reopen completed staff work. Changed saved evidence or a changed configuration/policy hash requires another assessment. A newer assessment of different evidence cannot be overwritten by clicking an older report. Historical/current labels still describe evidence freshness; a historical snapshot can have a completed staff review.

An old report is refreshed using its original evidence revision. The display also identifies the latest incident revision if it differs. JEV results covering a different displayed revision are marked historical, so a newer evaluation cannot appear to validate older displayed text.

The old Promotion / Not promotion / Unsure records remain untouched in `moderator_reviews_v1`. They are **not** converted into Needs attention / Looks okay / Unsure decisions. Technical lookups show the old label separately. Old buttons explain that a new incident lookup is needed; they do not silently acquire a new meaning. The old `!mod review` text command remains a legacy content-label operation and does not complete the staff queue.

New assessments use `staff_assessments_v1`, created only on the first authorized write. The append-only history is bounded to 32 entries per incident and follows incident deletion through a foreign key. Existing seven-day incident retention and message-binding/receipt bounds still apply. Queue listings are read-only and exclude records beyond retention without deleting them. Unsupported/corrupt assessment data cannot complete a review. Busy or expired clicks are refused; duplicate interaction IDs cannot repeat a saved mutation while receipts are retained. A lost confirmation can be checked with an incident lookup.

## Historical 0.3.5 install and verification procedure

From the existing **hermes** terminal:

```bash
python3 /tmp/liberdus-apply-0.3.5-20260921.py
```

The helper verifies the bundle hash, disables the custom platform, confirms the shared gateway restart, installs 0.3.5, enables the platform and confirms the second restart. Telegram may briefly reconnect because it shares that gateway. Stop on an error and keep the printed plugin backup path. The updater accepts versions 0.3.0 through 0.3.4 and preserves policy, credentials, database, counters and pause state. New optional state is created later by the runtime; the updater does not open the real moderation database. The developer account cannot access the live Hermes profile, so the owner must run this step.

In `bot-mod`, inspect an existing saved incident:

```text
!mod incident b48ee236ff774e0f8efec35caa8ba447
```

Check the two-box layout and source links, then choose the appropriate assessment. For the saved warning example, Looks okay is appropriate if staff consider it acceptable in context. Expect the view and retained latest report to show that assessment and reviewer. `!mod pending` should omit that evidence after Looks okay and include it after Needs attention or Unsure. Other unreviewed test incidents may also appear. Use another retained incident if this one has expired from storage. These checks need no new test-channel messages or paid JEV evaluation. A later normal gateway restart can confirm live button recovery; offline checks already cover stored bindings and assessment persistence.

The underlying updater is `/tmp/liberdus-update-0.3.5-20260921.pyz`, SHA-256 `491153e2b2d8fcc623a11e33883060d827b5579591a255e54fef6ae7eafcc24b`. The helper pins those exact bytes. A rebuilt ZIP may have a different hash because timestamps are included; do not replace the staged file beneath an old pinned helper. From the reviewed 0.3.5 checkout, `scripts.build_pilot_bundle.build(root, root / "examples/config.toml", target, update=True)` builds a new updater at a new path; its packaged example policy is unused by the update. Use the documented disable/restart, update, enable/restart sequence for a separately verified rebuild.

For rollback, disable the custom platform, finish the gateway restart and restore the prior plugin directory from the printed backup. Retain the newer directory and database. Version 0.3.4 ignores the new optional assessment table; do not delete it. New assessment buttons require the 0.3.5 handler. Stock Discord remains disabled in both profiles.

## Validation and later use of feedback

182 core/setup tests pass on Python 3.11.16 and 3.12.3; 50 integration tests use the pinned c1488 Hermes source and discord.py 2.7.1 with external network blocked/mocked. Checks cover staff queue transitions, authorization, legacy-label separation, pagination, retention, unchanged-evidence completion after restart, changed evidence/policy, stale clicks, report editing and failures, duplicate interactions, non-notifying mentions, display bounds and updates preserving existing configuration. The owner subsequently supplied live evidence for the review/display/queue path described above; unobserved live cases remain unverified.

Completed assessments provide an audit trail for later review of unnecessary alerts and useful cases. They are not automatic ground-truth labels for JEV's promotion category. Future rule/rubric changes should be chosen and tested explicitly. No automatic training, threshold tuning, enforcement or new paid batch is part of this release.
