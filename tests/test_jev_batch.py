"""Batch runner checks with synthetic profiles and a mocked provider."""

import asyncio
import contextlib
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from liberdus_moderator.classifier import ShadowClassifier, RESERVED_MICROUSD, RUBRIC
from liberdus_moderator.config import Config, ClassifierSettings, StorageSettings
from liberdus_moderator.configure_jev import policy_text
from liberdus_moderator.engine import Engine
from liberdus_moderator.jev import ProviderError
from liberdus_moderator.jev_batch import Ledger, batch_lock, run_batch, format_report, load_policy, main, run_name, TABLE
from liberdus_moderator.jev_cases import (CASES, PRECEDENCE_CASES, PRECEDENCE_SUITE, SUITE,
                                         get_suite, request, requests)
from liberdus_moderator.models import MessageEvent
from liberdus_moderator.storage import Store


def response(choice="announcement", tokens=543):
    return {"model": "jev-1.13.0", "answers": {"context": {
        "type": "choice", "choice": choice, "confidence": 0.98,
        "probabilities": {key: 0.98 if key == choice else 0.005 for key in RUBRIC["criteria"]}}},
        "usage": {"input_tokens": tokens, "output_tokens": 54}}


class BatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.profile = Path(self.temporary.name) / ".hermes/profiles/liberdus-mod"
        self.profile.joinpath("state").mkdir(parents=True)
        self.database = self.profile / "state/moderation.sqlite3"
        self.now = 1000.0
        self.monotonic = 50.0
        self.settings = ClassifierSettings(mode="shadow", max_daily_calls=100, max_total_calls=1000,
                                            daily_budget_microusd=50000, total_budget_microusd=250000)
        self.config = Config("1", "99", ("10", "11", "12"), ("20",), ("98",),
                             schema_version=2, ai_enabled=True, classifier=self.settings,
                             storage=StorageSettings(database_path=str(self.database)))
        self.policy = self.profile / "moderation.toml"
        self.policy.write_text(policy_text(self.config))
        self.live = Store(str(self.database))
        self.addCleanup(self.live.close)
        self.engine = Engine(self.config, self.live, clock=lambda: self.now)
        self.worker = ShadowClassifier(self.engine, AsyncMock(return_value=response()))
        for index, channel in enumerate(self.config.monitored_channel_ids):
            self.engine.process(MessageEvent("1", channel, str(500 + index), "50",
                                            "A retained live moderation fixture.", self.now))
        self.incident = self.live.incidents()[0]["id"]
        self.prepared = requests(self.settings)
        self.ledger = Ledger(self.database, write=True)
        self.addCleanup(self.ledger.close)
        self.provider = AsyncMock(return_value=response())

    async def sleep(self, seconds):
        self.now += seconds
        self.monotonic += seconds

    async def run_cases(self, prepared=None, **kwargs):
        return await run_batch(self.ledger, self.config, prepared or self.prepared, self.provider,
                               clock=lambda: self.now, monotonic=lambda: self.monotonic,
                               sleep=self.sleep, **kwargs)

    def live_state(self):
        return {name: [tuple(row) for row in self.live.db.execute("SELECT * FROM " + name)]
                for name in ("messages", "incidents", "incident_versions", "reports", "classifier_attempts")}

    async def test_requests_reuse_incident_builder_without_label_or_metadata_leakage(self):
        self.assertEqual(len(self.prepared), 10)
        self.assertEqual(len({digest for _, _, digest in self.prepared}), 10)
        for case, payload, _ in self.prepared:
            data = json.loads(payload)
            self.assertEqual(data["state"], {"texts": [case.text], "observed_copies": 3,
                                            "distinct_channels": 3, "same_member": True})
            self.assertNotIn("expected", data)
            self.assertNotIn("case_name", data)
            self.assertNotIn("author_id", data["state"])
            changed = replace(case, expected="other" if case.expected != "other" else "promotion")
            self.assertEqual(request(changed, self.settings), payload)

    async def test_candidate_preserves_baseline_identity_and_evidence_without_expected_label_leakage(self):
        self.assertEqual(get_suite(SUITE).rubric_hash,
                         "eb0a90b73588716140dd118bb73afdd12d78159e3c552e7a47102d601ba2452f")
        self.assertEqual(self.prepared[4][2],
                         "86138cb4dc48043280dacd5bddfa6d7adbe9a4decc81f53654d762522da1db0f")
        self.assertEqual(CASES[4].expected, "unclear")
        candidate = requests(self.settings, suite=PRECEDENCE_SUITE)
        self.assertEqual(len(candidate), 5)
        self.assertNotEqual(get_suite(PRECEDENCE_SUITE).rubric_hash, get_suite(SUITE).rubric_hash)
        self.assertEqual(candidate[0][0].text, CASES[4].text)
        self.assertEqual(candidate[0][0].expected, "promotion")
        for case, payload, _ in candidate:
            before = json.loads(request(case, self.settings))
            after = json.loads(payload)
            self.assertNotEqual(before.pop("questions"), after.pop("questions"))
            self.assertEqual(before, after)
            changed = replace(case, expected="other")
            self.assertEqual(payload, request(changed, self.settings, suite=PRECEDENCE_SUITE))
        self.assertEqual(set(get_suite(PRECEDENCE_SUITE).rubric["criteria"]), set(RUBRIC["criteria"]))

    async def test_candidate_size_limit_applies_after_rubric_substitution(self):
        case = replace(PRECEDENCE_CASES[0], text=PRECEDENCE_CASES[0].text + " extra" * 70)
        settings = replace(self.settings, max_request_bytes=2048)
        self.assertLessEqual(len(request(case, settings)), 2048)
        with self.assertRaisesRegex(ValueError, "input limits"):
            request(case, settings, suite=PRECEDENCE_SUITE)

    async def test_candidate_result_is_separate_and_never_rescores_or_reuses_baseline(self):
        self.provider.side_effect = [response(case.expected if case.name != "mixed" else "promotion")
                                     for case, _, _ in self.prepared]
        baseline = await self.run_cases()
        self.assertEqual(baseline["matched"], 9)
        saved = [tuple(row) for row in self.ledger.db.execute(f"SELECT * FROM {TABLE}")]
        before = self.live_state()
        candidate = requests(self.settings, suite=PRECEDENCE_SUITE)
        self.provider.side_effect = [response(case.expected) for case, _, _ in candidate]
        # Even an explicitly reused run name cannot confuse two different rubric hashes.
        result = await self.run_cases(candidate, suite=PRECEDENCE_SUITE)
        self.assertEqual((result["matched"], result["successful"], result["new_attempts"]), (5, 5, 5))
        self.assertEqual(result["reserved_microusd"], 13765)
        self.assertEqual(result["suite"], PRECEDENCE_SUITE)
        self.assertEqual(result["rubric_hash"], get_suite(PRECEDENCE_SUITE).rubric_hash)
        after = self.ledger.report("baseline", self.prepared)
        self.assertEqual(after["matched"], 9)
        self.assertEqual(after["records"][4]["expected_at_evaluation"], "unclear")
        self.assertEqual(saved, [tuple(row) for row in self.ledger.db.execute(
            f"SELECT * FROM {TABLE} WHERE suite=?", (SUITE,))])
        again = await self.run_cases(candidate, suite=PRECEDENCE_SUITE)
        self.assertEqual(again["new_attempts"], 0)
        self.assertEqual(self.provider.await_count, 15)
        self.assertEqual(self.live_state(), before)
        self.assertEqual(self.live.get_setting("classifier_state"), "ready")
        self.assertEqual(json.loads(self.worker.snapshot(self.incident).payload)["questions"]["context"], RUBRIC)
        self.assertLessEqual(max(map(len, format_report(result).splitlines())), 32)

    async def test_candidate_stops_at_shared_daily_cap_and_resumes_only_unattempted_cases(self):
        for name, value in (("classifier_budget_day", 0), ("classifier_daily_calls", 16),
                            ("classifier_total_calls", 16),
                            ("classifier_daily_reserved_microusd", 16 * RESERVED_MICROUSD),
                            ("classifier_total_reserved_microusd", 16 * RESERVED_MICROUSD)):
            self.live.set_setting(name, value)
        candidate = requests(self.settings, suite=PRECEDENCE_SUITE)
        self.provider.side_effect = [response(case.expected) for case, _, _ in candidate]
        result = await self.run_cases(candidate, suite=PRECEDENCE_SUITE, run_id=PRECEDENCE_SUITE)
        self.assertEqual(result["stopped"], "budget_exhausted")
        self.assertEqual(result["new_attempts"], 2)
        self.assertEqual(result["shared_total_calls"], 18)
        self.assertEqual(result["shared_total_reserved_microusd"], 49554)
        self.now = 86401.0
        resumed = await self.run_cases(candidate, suite=PRECEDENCE_SUITE, run_id=PRECEDENCE_SUITE)
        self.assertEqual((resumed["new_attempts"], resumed["matched"], resumed["successful"]), (3, 5, 5))
        self.assertEqual(self.provider.await_count, 5)
        self.assertEqual(resumed["reserved_microusd"], 13765)
        self.assertEqual(self.live.get_setting("classifier_daily_calls"), 3)

    async def test_mismatched_suite_requests_fail_before_database_write_or_provider(self):
        before = list(self.live.db.iterdump())
        candidate = requests(self.settings, suite=PRECEDENCE_SUITE)
        for prepared, suite in ((candidate, SUITE), (self.prepared, PRECEDENCE_SUITE)):
            with self.subTest(suite=suite), self.assertRaisesRegex(ValueError, "matching the selected suite"):
                await self.run_cases(prepared, suite=suite)
        self.assertEqual(before, list(self.live.db.iterdump()))
        self.provider.assert_not_awaited()

    async def test_saved_candidate_provenance_mismatch_is_unavailable_without_retry(self):
        candidate = requests(self.settings, suite=PRECEDENCE_SUITE)[:1]
        self.provider.return_value = response("promotion")
        await self.run_cases(candidate, suite=PRECEDENCE_SUITE)
        for field, bad, correct in (("suite", SUITE, PRECEDENCE_SUITE),
                                    ("rubric_hash", get_suite(SUITE).rubric_hash,
                                     get_suite(PRECEDENCE_SUITE).rubric_hash),
                                    ("expected", "unclear", "promotion")):
            with self.subTest(field=field):
                self.ledger.db.execute(f"UPDATE {TABLE} SET {field}=?", (bad,))
                result = await self.run_cases(candidate, suite=PRECEDENCE_SUITE)
                self.assertEqual(result["records"][0]["outcome"], "unavailable")
                self.assertIsNone(result["records"][0]["actual"])
                self.assertEqual(result["new_attempts"], 0)
                self.ledger.db.execute(f"UPDATE {TABLE} SET {field}=?", (correct,))
        self.provider.assert_awaited_once()

    async def test_success_mismatch_and_shared_accounting_preserve_live_state(self):
        before = self.live_state()
        self.provider.side_effect = [response(case.expected if i != 4 else "promotion")
                                     for i, (case, _, _) in enumerate(self.prepared)]
        result = await self.run_cases()
        self.assertEqual(result["new_attempts"], 10)
        self.assertEqual(result["records"][0]["expected_at_evaluation"], CASES[0].expected)
        self.assertEqual(result["records"][0]["policy_hash"], self.config.policy_hash)
        self.assertEqual(result["successful"], 10)
        self.assertEqual(result["matched"], 9)
        self.assertEqual(result["reserved_microusd"], 27530)
        self.assertEqual(result["shared_total_calls"], 10)
        self.assertEqual(self.live_state(), before)
        self.assertEqual(self.live.get_setting("classifier_state"), "ready")
        self.assertEqual(self.engine.status()["ai_attempts"], 10)
        self.assertEqual(load_policy(self.profile), self.config)
        text = format_report(result)
        self.assertIn("[REVIEW]", text)
        self.assertNotIn(CASES[0].text, text)
        self.assertLessEqual(max(map(len, text.splitlines())), 32)

    async def test_same_run_reuses_attempts_new_name_is_explicit_and_charged(self):
        result = await self.run_cases(self.prepared[:1])
        self.assertEqual(result["new_attempts"], 1)
        again = await self.run_cases(self.prepared[:1])
        self.assertEqual(again["new_attempts"], 0)
        self.provider.assert_awaited_once()
        fresh = await self.run_cases(self.prepared[:1], run_id="second")
        self.assertEqual(fresh["new_attempts"], 1)
        self.assertEqual(fresh["shared_total_calls"], 2)

    async def test_live_worker_and_batch_share_budget_across_connections(self):
        self.config = replace(self.config, classifier=replace(self.settings, max_total_calls=1))
        self.live.set_setting("policy_hash", self.config.policy_hash)
        self.engine.config = self.config
        worker = ShadowClassifier(self.engine, self.provider)
        self.live.db.execute("UPDATE incidents SET policy_hash=?", (self.config.policy_hash,))
        self.assertTrue(worker.reserve(worker.snapshot(self.incident)))
        self.now += 6
        result = await self.run_cases()
        self.assertEqual(result["stopped"], "budget_exhausted")
        self.assertEqual(result["new_attempts"], 0)
        self.assertEqual(result["shared_total_calls"], 1)
        self.provider.assert_not_awaited()
        self.assertEqual(self.live.db.execute("SELECT outcome FROM classifier_attempts").fetchone()[0], "running")

    async def test_batch_reservation_blocks_live_worker_at_shared_limit(self):
        await self.run_cases(self.prepared[:1])
        self.now += 6
        worker = ShadowClassifier(self.engine, self.provider)
        worker.settings = replace(worker.settings, max_total_calls=1)
        self.assertFalse(worker.reserve(worker.snapshot(self.incident)))
        self.assertEqual(self.live.get_setting("classifier_state"), "budget_exhausted")
        self.assertEqual(self.live.get_setting("classifier_total_calls"), 1)

    async def test_daily_call_and_spend_and_total_spend_limits(self):
        for field, limit in (("max_daily_calls", 1), ("daily_budget_microusd", RESERVED_MICROUSD),
                             ("total_budget_microusd", RESERVED_MICROUSD)):
            with self.subTest(field=field):
                self.config = replace(self.config, classifier=replace(self.settings, **{field: limit}))
                self.live.set_setting("policy_hash", self.config.policy_hash)
                if not self.live.get_setting("classifier_total_calls", 0):
                    await self.run_cases(self.prepared[:1])
                result = await self.run_cases(self.prepared[1:2])
                self.assertEqual(result["new_attempts"], 0)
                self.assertEqual(result["stopped"], "budget_exhausted")

    async def test_provider_error_stops_batch_and_never_records_raw_error(self):
        self.provider.side_effect = RuntimeError("TOP-SECRET arbitrary provider body")
        result = await self.run_cases()
        self.provider.assert_awaited_once()
        self.assertEqual(result["stopped"], "provider_or_response_error")
        self.assertEqual(result["new_attempts"], 1)
        self.assertNotIn("TOP-SECRET", json.dumps(result))
        self.assertNotIn("TOP-SECRET", "\n".join(self.ledger.db.iterdump()))
        replay = await self.run_cases(self.prepared[:1])
        self.assertEqual(replay["new_attempts"], 0)

    async def test_timeout_cancellation_and_crash_keep_reservations(self):
        self.provider.side_effect = TimeoutError()
        result = await self.run_cases(self.prepared[:1])
        self.assertEqual(result["records"][0]["outcome"], "timeout")
        self.provider.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.run_cases(self.prepared[1:2])
        self.assertEqual(self.ledger.row("baseline", self.prepared[1][2])["outcome"], "uncertain")
        self.now += 6
        case, _, digest = self.prepared[2]
        self.assertEqual(self.ledger.reserve("baseline", case, digest, self.config, self.now), "reserved")
        result = await self.run_cases(self.prepared[:3])
        self.assertEqual(result["records"][2]["outcome"], "uncertain")
        self.assertEqual(result["new_attempts"], 0)
        self.assertEqual(result["shared_total_calls"], 3)
        self.assertEqual(result["reserved_microusd"], 3 * RESERVED_MICROUSD)

    async def test_policy_pause_and_off_gates_before_provider_call(self):
        for condition in ("changed", "paused", "off", "billing"):
            with self.subTest(condition=condition):
                self.live.set_setting("policy_hash", self.config.policy_hash)
                self.live.set_setting("paused", condition == "paused")
                self.live.set_setting("classifier_billing_guard", condition == "billing")
                if condition == "off":
                    self.config = replace(self.config, ai_enabled=False, classifier=ClassifierSettings())
                    self.live.set_setting("policy_hash", self.config.policy_hash)
                result = await self.run_cases(current=lambda: condition != "changed")
                self.assertEqual(result["new_attempts"], 0)
        self.provider.assert_not_awaited()

    async def test_late_policy_change_discards_judgment(self):
        async def change(_):
            self.live.set_setting("paused", True)
            return response()
        self.provider.side_effect = change
        result = await self.run_cases()
        self.assertEqual(result["stopped"], "stale")
        self.assertIsNone(result["records"][0]["actual"])
        self.assertEqual(result["shared_total_calls"], 1)
        self.assertEqual(result["estimated_microusd"], 23)

    async def test_daily_rollover_and_clock_rollback(self):
        await self.run_cases(self.prepared[:1])
        self.now = 86401.0
        result = await self.run_cases(self.prepared[1:2])
        self.assertEqual(result["shared_total_calls"], 2)
        self.assertEqual(self.live.get_setting("classifier_daily_calls"), 1)
        self.now = 1001
        self.live.set_setting("classifier_last_attempt_at", 0)
        result = await self.run_cases(self.prepared[2:3])
        self.assertEqual(result["stopped"], "clock_rollback")
        self.assertEqual(result["new_attempts"], 0)

    async def test_budget_overrun_stops_both_batch_and_live_classifier(self):
        self.provider.return_value = response(tokens=65537)
        result = await self.run_cases()
        self.assertEqual(result["stopped"], "usage_exceeds_reservation")
        self.assertFalse(self.worker.eligible())
        self.provider.assert_awaited_once()

    async def test_corrupt_saved_result_is_unavailable_without_retry(self):
        await self.run_cases(self.prepared[:1])
        self.ledger.db.execute(f"UPDATE {TABLE} SET result_json=?", ('{"secret":"DO-NOT-DISPLAY"}',))
        result = await self.run_cases(self.prepared[:1])
        self.assertEqual(result["records"][0]["outcome"], "unavailable")
        self.assertEqual(result["new_attempts"], 0)
        self.assertNotIn("DO-NOT-DISPLAY", format_report(result))
        self.provider.assert_awaited_once()

    async def test_read_only_results_work_before_first_run(self):
        before = list(self.live.db.iterdump())
        with Ledger(self.database) as reader:
            result = reader.report("baseline", self.prepared)
        self.assertEqual(result["attempted"], 0)
        self.assertEqual(before, list(self.live.db.iterdump()))

    async def test_run_time_limit_stops_waiting_without_reservation(self):
        self.live.set_setting("classifier_last_attempt_at", self.now + 3600)
        result = await self.run_cases()
        self.assertEqual(result["stopped"], "time_limit")
        self.assertEqual(result["new_attempts"], 0)
        self.provider.assert_not_awaited()

    async def test_batch_lock_and_profile_paths_refuse_conflicts(self):
        with batch_lock(self.profile), self.assertRaisesRegex(ValueError, "Another batch"):
            with batch_lock(self.profile):
                pass
        path = self.profile / "state/jev-batch.lock"
        path.unlink()
        path.symlink_to(self.database)
        with self.assertRaises(ValueError):
            with batch_lock(self.profile):
                pass
        self.policy.unlink()
        self.policy.symlink_to(self.database)
        with self.assertRaises(ValueError):
            load_policy(self.profile)

    async def test_preview_never_reads_profile_or_starts_provider(self):
        output = io.StringIO()
        with patch("sys.argv", ["batch", "preview"]), patch("pathlib.Path.home", side_effect=AssertionError), \
             patch("liberdus_moderator.jev_batch.evaluate", side_effect=AssertionError), contextlib.redirect_stdout(output):
            self.assertEqual(main(), 0)
        preview = json.loads(output.getvalue())
        self.assertEqual(preview["maximum_reserved_microusd"], 27530)
        self.assertFalse(preview["provider_called"])
        for value in ("../escape", "Upper", "a" * 33, "", "with space"):
            with self.assertRaises(ValueError):
                run_name(value)

    async def test_cli_run_and_results_reuse_paid_records_without_another_key_read(self):
        from liberdus_moderator import jev_batch as module
        virtualenv = self.profile.parent.parent / "hermes-agent/venv"
        virtualenv.joinpath("bin").mkdir(parents=True)
        virtualenv.joinpath("bin/python").touch()
        original = module.run_batch
        async def fast_run(ledger, config, prepared, evaluator, **kwargs):
            return await original(ledger, config, prepared, evaluator, clock=lambda: self.now,
                                  monotonic=lambda: self.monotonic, sleep=self.sleep, **kwargs)
        with patch("pathlib.Path.home", return_value=Path(self.temporary.name)), \
             patch("sys.prefix", str(virtualenv)), patch.object(module, "run_batch", side_effect=fast_run), \
             patch.object(module, "evaluate", new=AsyncMock(return_value=response())) as provider:
            output = io.StringIO()
            with patch("sys.argv", ["batch.pyz", "run", "--json"]), \
                 patch.object(module, "profile_key", return_value="synthetic-key") as key, \
                 contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(await asyncio.to_thread(main), 1)  # Valid responses, label disagreements.
            key.assert_called_once()
            result = json.loads(output.getvalue())
            self.assertEqual(result["new_attempts"], 10)
            self.assertEqual(provider.await_count, 10)
            for command in ("run", "results"):
                output = io.StringIO()
                with patch("sys.argv", ["batch.pyz", command, "--json"]), \
                     patch.object(module, "profile_key", side_effect=AssertionError("No key access on saved results")), \
                     contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(await asyncio.to_thread(main), 1)
                self.assertEqual(json.loads(output.getvalue())["new_attempts"], 0)
            self.assertEqual(provider.await_count, 10)

    async def test_candidate_cli_defaults_to_separate_run_and_results_work_after_switching_off(self):
        from liberdus_moderator import jev_batch as module
        virtualenv = self.profile.parent.parent / "hermes-agent/venv"
        virtualenv.joinpath("bin").mkdir(parents=True)
        virtualenv.joinpath("bin/python").touch()
        original = module.run_batch
        async def fast_run(ledger, config, prepared, evaluator, **kwargs):
            return await original(ledger, config, prepared, evaluator, clock=lambda: self.now,
                                  monotonic=lambda: self.monotonic, sleep=self.sleep, **kwargs)
        provider = AsyncMock(side_effect=[response(case.expected) for case in PRECEDENCE_CASES])
        with patch("pathlib.Path.home", return_value=Path(self.temporary.name)), \
             patch("sys.prefix", str(virtualenv)), patch.object(module, "run_batch", side_effect=fast_run), \
             patch.object(module, "evaluate", new=provider):
            output = io.StringIO()
            with patch("sys.argv", ["batch.pyz", "run", "--suite", PRECEDENCE_SUITE, "--json"]), \
                 patch.object(module, "profile_key", return_value="synthetic-key"), \
                 contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(await asyncio.to_thread(main), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["run_id"], PRECEDENCE_SUITE)
            self.assertEqual(result["new_attempts"], 5)
            off = replace(self.config, ai_enabled=False, classifier=ClassifierSettings())
            self.policy.write_text(policy_text(off))
            output = io.StringIO()
            before = list(self.live.db.iterdump())
            with patch("sys.argv", ["batch.pyz", "results", "--suite", PRECEDENCE_SUITE, "--json"]), \
                 patch.object(module, "profile_key", side_effect=AssertionError("No key access for results")), \
                 contextlib.redirect_stdout(output):
                self.assertEqual(await asyncio.to_thread(main), 0)
            self.assertEqual(json.loads(output.getvalue())["new_attempts"], 0)
            self.assertEqual(before, list(self.live.db.iterdump()))
            self.assertEqual(provider.await_count, 5)


if __name__ == "__main__":
    unittest.main()
