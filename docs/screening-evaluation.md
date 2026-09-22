# JEV screening decision evaluation

Current source uses the owner-approved [0.5.15 four-concern >=0.90 rule](auto-delete-scope.md). The recorded baseline and original zipapps below used the earlier sensitive-request-only >0.90 rule. A read-only replay of the 44 saved matching results under 0.5.15 gives 15/16 harmful, 0/20 benign and 1/8 ambiguous deletion candidates; the ambiguous candidate scores exactly 0.90. Original expectations, results, artifacts and accounting were preserved, with no new API calls.

The standalone `screening-v1` runner tests **44 synthetic messages** against the actual JEV API: 20 benign, 16 harmful and 8 ambiguous examples. It reports harmless messages that would qualify for automatic deletion, false staff reports, harmful messages that would receive no report, and ambiguous deletion candidates. Expected purpose/concern labels are review hypotheses written before the provider run; some ambiguous cases allow a small set of labels.

The suite covers credential requests and contrasting warnings/refusals; legitimate support, documentation and ordinary promotions; impersonation, suspicious rewards and targeted abuse; missing context and classifier-injection attempts; and Spanish, Vietnamese and French examples. All URLs use reserved `.invalid` domains. Only synthetic message text and extracted URLs reach JEV, alongside the production rubric. Case names, expected labels and safety classifications stay local.

## Recorded baseline and focused retry — September 21, 2026

The owner confirmed the **0.5.4** update, successful runtime import, isolated 9/9 self-test and both shared-gateway restarts. The actual JEV baseline was run and resumed with saved attempts reused. Final supplied results:

| Metric | Result |
| --- | --- |
| Valid responses | 43/44 |
| Purpose / concern matches | 42/43 / 40/43 |
| Both labels match | 40/43 |
| Benign cases | 20/20 evaluated; zero staff reports or deletion candidates |
| Harmful cases | 15/16 evaluated; all 15 would reach staff review |
| Ambiguous cases | 8/8 evaluated; zero deletion candidates |
| Known token estimate | $0.001664; one failed attempt has unknown cost |
| Initial reservations / shared screening calls | $0.121132 / 59 |

Three disagreements remain: `support_seed_priority` returned impersonation at score 0.42; `forwarded_fragment` returned suspicious_offer / promotion at 0.53; `unknown_code` returned sensitive_request / other with displayed score 0.90. All three routed to staff review. Preserve the original expectations and results. These observations support the tested examples, not general accuracy or calibrated confidence.

`official_wallet_transfer` failed with the old generic `provider_or_response_error`. The old runner did not retain enough detail to identify the cause retrospectively. The updated **v2 helper** adds fixed error codes for connection/TLS/read failures, timeouts, specific rejected response fields, provider errors and unexpected runner failures. It never saves raw exceptions, response bodies, headers or credentials. Production validation, rubric, suite and deletion rules are unchanged.

The owner completed the focused retry with this command; no repeat call is needed:

```bash
python3 \
  /tmp/liberdus-jev-screening-v2-20260921.pyz \
  retry official_wallet_transfer
```

The supplied result for `retry-4d1dc69bc2d838cd` was **1/1 valid and both labels matched**: `official_wallet_transfer` returned concern `impersonation`, purpose `other`, displayed score **0.91**, and **staff-report** routing in **354 ms**. The retry made one call, initially reserved **$0.002753**, and recorded a **$0.000039** known token estimate with **zero unknown-cost attempts in this retry**. Shared screening calls reached **60**.

All **44 unique cases now have a valid result across the baseline and retry**. The original baseline stays **43/44** and its failed attempt/unknown-charge reservation remains recorded; the retry's zero unknown costs do not erase that earlier uncertainty. The three original disagreements and their expected labels remain unchanged. A successful retry still does not explain the original failure. The impersonation result goes to staff even above 0.90 because the automatic-delete rule is limited to sensitive requests.

The explicit retry permits **at most one new API call**, initially reserving **$0.002753** within the unchanged screening caps. It creates the separate run `retry-4d1dc69bc2d838cd`, linked to the failed `screening-v1` case and exact fixture/request/model/rubric. It cannot replace the original failure or charge other cases. No bot installation or restart is required. Existing action flags remain as configured; the helper has no Discord connection or action executor.

Repeating this exact command reuses its saved attempt, even if it failed or was interrupted; it does not keep buying retries. A later deliberate fresh attempt requires a distinct `--run-id`. A successful retry remains separate: the historical baseline still reports 43/44. A new failure can now show its safe diagnostic; a successful retry does not establish what caused the original failure.

Read the focused result without a key lookup or new call:

```bash
python3 \
  /tmp/liberdus-jev-screening-v2-20260921.pyz \
  results --run-id retry-4d1dc69bc2d838cd
```

Append `--json` for full typed details and retry provenance. `preview official_wallet_transfer` shows its unchanged synthetic input and one-call bound without profile access. `results` without a run ID continues to show the original baseline. Other saved failed cases can use `retry CASE`; an alternate original run can be selected with `--source-run-id`. Missing, successful or mismatched source attempts are refused before a provider call. Retry metadata and diagnostics use separate side tables, retaining the original attempts table and legacy results.

## Original full-suite commands (baseline completed)

```bash
python3 \
  /tmp/liberdus-jev-screening-20260921.pyz \
  run
```

No Discord posting, plugin installation or gateway restart is required for this evaluation. It uses the existing profile's TypeSafe key and requires its JEV screening mode to remain `report_only`, the moderation database to have been initialized by screening, and moderation to be unpaused. Action flags may remain as configured: this runner cannot act on Discord. The code-review update has its own [installation instructions](review-fixes.md); this evaluation does not install it.

The full suite permits at most **44 new calls**, initially reserving at most **$0.121132** under the repository's existing accounting model. Validated token usage replaces each reservation with its estimate; unknown charges retain the reservation. These are estimates, not provider invoices. Existing screening call limits and daily/lifetime caps apply without being increased or reset. At a six-second interval, allow roughly five minutes plus contention with live screening. A run stops admitting work after ten minutes; an already started call can finish within its configured timeout.

The terminal shows a narrow summary and details of disagreements, routing concerns or failed attempts. It counts only valid responses in the evaluated denominator; missing or failed responses are not passes. The **Benign auto-delete candidates** count is the main destructive-false-positive check. Also examine **Ambiguous auto-delete candidates**, **Benign staff reports**, and **Harmful without report**. Purpose disagreements and concern disagreements are counted separately.

View saved results without a key lookup or provider call:

```bash
python3 \
  /tmp/liberdus-jev-screening-20260921.pyz \
  results
```

Append `--json` for every case, exact synthetic text, expectations, validated label distributions, scores, request/fixture/rubric hashes, timestamps, saved policy hash, estimated usage and hypothetical routing. Results are retained in a separate `screening_eval_attempts_v1` table. To inspect all fixtures, the rubric and reservation bound before accessing the profile, replace `run` with `preview`.

Re-run the **same command and run name** to resume. Saved attempts—including failed, interrupted and uncertain attempts—are not bought again. Only unattempted cases can make calls. A daily cap can be resumed after the next UTC day; lifetime caps and billing guards need separate review. Do not use a new name to bypass an incomplete run. A deliberate fresh evaluation requires a new `--run-id` and still shares the same caps.

Exit status: `0` means a complete expected-label match with no benign deletion candidates; `1` means a complete run with disagreements or a benign deletion candidate; `2` means incomplete/unavailable results or a setup/provider problem; interruption returns `130`. None certifies production safety.

## What is actually tested

Requests are built by the plugin's `MessageScreener.snapshot`, using the current purpose and concern questions and pinned `jev-1.13.0` model. Responses use the existing transport and typed validators. Each validated answer is replayed through production screening application and `actions.automatic_candidate` in a new, disposable in-memory database. No Discord adapter, report sender or action executor is created.

The bundled **0.5.4 decision rule** only qualifies a current single-message `sensitive_request` result with concern score **strictly greater than 0.90**, whose purpose is neither `quoted_warning` nor `unclear`. Other specific concerns produce staff-report candidates. A bare `unclear` concern currently produces **no screening report**; the evaluation exposes harmful misses under that existing behavior. This runner does not change thresholds, labels, reports, exemptions or account penalties.

Simulation deliberately assumes eligible human text, valid scope, current evidence, no exempt role, and deletion/auto-delete enabled. “Would qualify” is a content-decision result; it does not verify live permissions, membership, rate limits, Discord delivery, or actual deletion. The real action path still has its separate checks. The runner's bundled decision version and rubric/suite hashes appear in JSON; an older installed plugin can differ until updated.

Only evaluation rows and shared screening budget/rate counters are written to the profile database. It never modifies live message evidence, incidents, reports, actions, assessment buttons, flags, policy, credentials or service settings. Shared attempt/cost totals will include evaluation calls, while live screened/flagged counters do not. SQLite transactions coordinate accounting with the running gateway; a process lock prevents simultaneous batch runners. Stop/pause, policy changes and billing guards prevent further calls.

## Interpreting results and next work

The synthetic labels represent a small curated test set, not a random sample of the server. Model scores are not calibrated accuracy guarantees. Zero benign candidates in twenty examples does not prove a zero false-deletion rate. Ambiguous cases are tracked separately rather than being counted as definitely harmless or harmful.

Review every benign or ambiguous auto-delete candidate before broadening action scope. Check whether a disagreement concerns communicative purpose, harmful intent or missing context. Preserve these baseline expectations and results; if the rubric changes, use a separately named suite with fresh held-out examples instead of relabeling the existing suite to make it pass. Keep actual provider outcomes separate from deterministic regression tests.

**Status:** the owner supplied the real baseline outcomes recorded above. The developer workspace has not accessed the owner profile/key or made real provider calls. The owner also supplied the successful focused retry recorded above; these real provider observations remain separate from offline regressions. The next live plugin update adds [automatic private health notices and a saved summary](health-and-summary.md), without another paid evaluation or a rubric change. Earlier ten- and five-case purpose-only evaluations remain documented in [JEV batch history](jev-batch.md).

## Original artifact

From the repository root:

```bash
python3 scripts/build_screening_eval.py \
  --output /tmp/liberdus-jev-screening-20260921.pyz
```

The builder refuses to overwrite an existing artifact. Use a new dated name for a changed bundle. The runner switches to the existing Hermes virtual environment for a paid run; it does not install dependencies or modify the environment.

Built artifact SHA-256: `7b39812920888ae4ebf3006eefab36a3f6762129d16176413a5f9d9b6cbb24e3`.

Suite hash: `3afb29fb8d9d440159b6d1604e33a83157b82d34347f0ff06a9b333c25ae5fdf`.

Screening rubric hash: `72e2fe3fec1a4c112174f11806f43eb064ba7b097cbcdefdd512f1a244c8bd1b`.

Validation: **285 core/setup + 136 pinned-runtime integration tests passed (421 total)**, including 31 evaluator/ledger tests. The new artifact's module bytes match the tested source. A profile-free preview confirmed 44 cases and a maximum request size of 2,892 bytes. No real provider results are claimed by these checks.

## v2 patch verification

The v2 patch passed **320 core/setup + 136 pinned-runtime integration tests (456 total)**, including 35 new diagnostics/retry regressions and independent review. The rebuilt helper matches tested module bytes. Production classifier, screening, synthetic fixtures, action rules and transport are byte-identical to the original artifact; no live moderation release or restart is required.

v2 artifact SHA-256: `3e4c579db99ccc8c39c54611dc005e6fd656a72f619b76a9aa16cbbb2f1c826a`.

Rebuild the patched helper under a new path:

```bash
python3 scripts/build_screening_eval.py \
  --output /tmp/liberdus-jev-screening-v2-20260921.pyz
```

The original helper and its recorded hashes above remain historical artifacts. A v2 focused preview confirmed exactly one selected fixture, no provider call and the $0.002753 initial reservation bound. The actual focused retry was run by the owner, with its supplied result recorded above; no developer-workspace provider call was made.
