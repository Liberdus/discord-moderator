# Moderation plugin code review — September 21, 2026

**Verdict: retain the private test scope and fix the safety-control and role-protection gaps before expanding the pilot or enabling staff timeouts.** The ordinary deletion paths and latest reply patch have useful automated coverage and owner-reported live success, but the passing baseline suite missed the reproduced cases below. More repetitions of the completed deletion tests are not the next priority.

Reviewed version **0.5.3**, commit `eb41018393b75c5ad26c2c5b71a13795c73fd6a1`. Review used the actual pinned Hermes c1488 source, Python 3.11.16 and discord.py 2.7.1. Three independent code reviews covered actions, screening, and staff controls; the primary review reran the full suite and every reproduction, and checked lifecycle/configuration/deployment behavior. Production code, live configuration, credentials, Discord messages, JEV usage and gateway services were not changed. The deployed profile itself was not inspected.

## Reproduced findings

### F1 — High priority: stop controls can be silently discarded

[hermes_adapter.py:529](../../liberdus_moderator/hermes_adapter.py#L529) drops commands inside the one-second shared rate limit. Sending `!mod status` immediately before `!mod pause`, `!mod deletion off`, `!mod auto-delete off` or `!mod timeout off` left pause false or the selected feature enabled. Only status received a response; no command receipt was written for the stop control.

A second path is [coverage_gap:286](../../liberdus_moderator/hermes_adapter.py#L286), which drains the entire queue on an external deletion, disconnect, edit-fetch failure or overflow. An accepted pause followed by an external deletion was removed before execution, leaving the bot running. The own-deletion path preserves queued controls, but the general reset path does not.

**Recommended fix:** give authenticated pause and feature-off commands a bounded control path that is independent of evidence invalidation and response throttling. Recheck authorization and current policy before applying; do not preserve an old destructive confirmation as an executable action after a reset. Throttle replies or return an explicit busy response without silently losing a stop instruction. Test status→stop, review→stop, each reset cause, queue saturation and duplicate controls.

### F2 — Medium priority: resets can leave button interactions at thinking

The [same queue drain](../../liberdus_moderator/hermes_adapter.py#L290) and [stale-generation skip](../../liberdus_moderator/hermes_adapter.py#L498) discard interactions after a successful defer. Reproduction: one acknowledged review click, then an external deletion, produced zero saved assessments and zero completion replies. The 0.5.3 empty-view fix handles the ordinary completion path; it does not cover cancellation after enqueue.

**Recommended fix:** finish discarded, acknowledged interactions with a private cancellation/retry notice where the token is still usable. Never replay the moderation action to repair a response. Cover review, confirmation and cancellation clicks across reset/disconnect/full-queue paths. Delivery during a real outage remains best effort.

### F3 — Medium priority: incomplete role cache can bypass automatic-deletion exemption

[action_transport.py:98](../../liberdus_moderator/action_transport.py#L98) reads `member.roles` after fetching fresh membership. The pinned SDK resolves IDs through the guild role cache and silently omits roles missing from that cache. A real SDK Member returned from mocked REST contained exempt role `77`; the cache lacked that role, so `member.roles` showed only the default role. With exemption ON, the automatic deletion ran once.

This means a fresh membership fetch alone does not prove complete role information. The same resolution loss can weaken custom protected-role checks for timeout; Discord still enforces its own native permission and hierarchy rules.

**Recommended fix:** validate complete membership role IDs independently of cache resolution and refuse destructive action when required role or privilege data cannot be established. Add cases for absent/deleted/unresolved roles, failed role resolution and changed membership during validation. Do not interpret unknown membership as non-exempt.

### F4 — Medium priority: privileged-member timeout protection is incomplete

[action_transport.py:117](../../liberdus_moderator/action_transport.py#L117) protects Administrator, Manage Server, Moderate Members and Manage Messages. It omits Kick Members, Ban Members and Manage Roles. Each omitted permission independently allowed the test target's timeout method to execute, despite the runbook's broad administrative/moderation protection claim.

**Recommended fix:** define the full protected permission set explicitly and test each privileged permission independently, alongside configured exempt roles, owner/operator/bot identities and hierarchy. Keep timeouts disabled in the current pilot until this protection is settled. A staff-confirmed action is still required; this is not an unauthorized-user command bypass.

### F5 — Medium priority: an exempt member's edited message can be sent to JEV

[snapshot:70](../../liberdus_moderator/hermes_adapter.py#L70) maps absent author roles to an empty tuple. The [edit fetch:513](../../liberdus_moderator/hermes_adapter.py#L513) can return a User without membership metadata; processing that event makes [screening.evidence:155](../../liberdus_moderator/screening.py#L155) treat the member as non-exempt. The isolated probe observed zero mock provider calls for the exempt original, then one call for its edit.

The existing role-exemption documentation explicitly permits screening when membership is unavailable, so part of this is an existing policy choice. However, the documentation overstates role availability on fetched edits, and the behavior does not reliably meet the requested skip-role behavior. The separate action-time check normally prevents automatic deletion of a verified exempt member, subject to F3.

**Recommended fix:** resolve membership for edits when role exemption is ON; if it remains unknown, preserve deterministic rules while withholding JEV screening and recording incomplete coverage. Test exempt/non-exempt/unknown memberships, failed fetches and exemption toggles while lookup is pending.

## Workflow limitation

Deleting a message conservatively resets the evidence window through [finish_own_deletions](../../liberdus_moderator/action_transport.py#L166). The incident becomes `needs_revalidation`; a timeout requested on the same incident then fails [actions.current](../../liberdus_moderator/actions.py#L64), even using its latest revision. This was reproduced after successful deletion.

This is intentional stale-evidence protection, not a reason to bypass it. If staff need “delete and timeout,” design an explicit combined workflow with separate, current validation. The present bot cannot perform that sequence through the same incident after deletion. A timeout never occurs automatically.

## What the checks establish

| Area | Evidence from this review | Limit |
|---|---|---|
| Baseline suite | **249 core/setup + 98 pinned-runtime integration tests passed (347 total)** | Existing tests do not cover all queue/cache races |
| Dismiss / staff review | Completion, pending removal, old-revision isolation and changed-evidence reopening covered | No live staff click was sent by this review |
| Authorization | Numeric operator, server/channel, bot-author and durable confirmation bindings; unauthorized/duplicate cases pass | No live server membership/permission audit |
| Deletion | Actual SDK method reaches mocked HTTP once, with durable outcome and duplicate prevention | Owner already verified auto/manual deletion; this review sent no mutation |
| Timeout SDK compatibility | Actual Member.timeout → Member.edit reaches mocked HTTP with correct target, ten-minute aware timestamp and reason; duplicate blocked | Actual bot Moderate Members permission and client behavior remain unverified |
| Role/privilege protections | Existing positive/refusal tests plus targeted probes | F3–F5 remain unresolved |
| JEV routing | **180 combinations**: six concerns × five purposes × six score boundaries | Synthetic supplied labels; no measured real-model false-positive rate |
| Automatic-delete rule | Only sensitive_request, score strictly >0.90, purpose other than quoted_warning/unclear qualifies; existing scope/freshness/flags also required | Confidence is not a calibrated accuracy guarantee |
| Spending/failures | Daily/lifetime caps, durable reservations, unknown-charge retention, auth suspension, stale-result rejection covered | No provider invoice or credit-balance verification |
| Reply/display | Real Webhook.send serialization, ephemeral/mention suppression, confirmation binding, explicit button rows and text bounds pass | F2 can still strand an acknowledged interaction during resets |
| Deployment | Staged 0.5.3 package bytes and helper match audited source and pinned digest; stopped-update preservation tests pass | Owner-only installed profile was not inspected |

The focused reviewers' test counts are subsets of the 347 baseline tests and are not additional tests. The role/edit and control/action probes are additional observations, with defect reproductions clearly distinguished from passing behavior.

The false-positive question cannot be settled from code alone: normal conversations, announcements and warnings are protected **when JEV returns the expected labels**. The current concern rubric still needs representative labelled evaluation to measure how often that happens. No new paid evaluation was run. Earlier context-label examples do not measure the newer concern rubric's accuracy.

## Reproduce without manual Discord testing

Run from the repository with the pinned isolated runtime:

```sh
export PYTHONPATH=/tmp/liberdus-hermes-c1488/NousResearch-hermes-agent-c1488ac:.:integration_tests
/tmp/liberdus-package-check-311/bin/python -B -m unittest discover -s tests
/tmp/liberdus-package-check-311/bin/python -B -m unittest discover -s integration_tests
/tmp/liberdus-package-check-311/bin/python -B review_checks/2026-09-21/controls.py
/tmp/liberdus-package-check-311/bin/python -B review_checks/2026-09-21/actions.py
/tmp/liberdus-package-check-311/bin/python -B review_checks/2026-09-21/screening.py
```

The last three scripts characterize current 0.5.3 behavior, including known defects. Their zero exit code means reproduction succeeded, not that the behavior is acceptable. They mock network/provider boundaries and use disposable stores/profiles. Convert them to regression tests requiring corrected behavior when implementing fixes.

Recommended order: repair stop-command delivery first; then make role resolution and staff-timeout protection conservative; then complete canceled button replies and settle the delete-plus-timeout workflow. Keep the completed deletion checks recorded rather than asking the owner to repeat them. No public-channel expansion or new account penalties are recommended by this review.
