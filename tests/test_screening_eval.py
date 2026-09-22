"""Evaluation integrity with synthetic profiles and a mocked provider, never real JEV."""
import asyncio
import contextlib
from dataclasses import replace
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import zipfile

from test_screening import config, response
from liberdus_moderator.classifier import RESERVED_MICROUSD, RUBRIC
from liberdus_moderator.configure_jev import policy_text
from liberdus_moderator.engine import Engine
from liberdus_moderator.jev import ProviderError
from liberdus_moderator.screening import CONCERN, QUESTIONS, SCREENING_HASH, MessageScreener, validate_screening
from liberdus_moderator.screening_cases import CASES, SUITE
from liberdus_moderator.screening_eval import (decision, format_report, main, report, requests, run_evaluation, retry_run_name)
from liberdus_moderator.screening_eval_ledger import ScreeningEvalLedger, TABLE
from liberdus_moderator.storage import Store
from scripts.build_screening_eval import build


def result(concern='none', purpose='other', score=1.):
    raw = response(concern, purpose)
    raw['answers']['concern']['confidence'] = score
    return raw


class EvaluationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.profile = self.home / '.hermes/profiles/liberdus-mod'
        self.profile.joinpath('state').mkdir(parents=True)
        self.database = self.profile / 'state/moderation.sqlite3'
        base = config()
        self.config = replace(base, actions_enabled=True, storage=replace(base.storage, database_path=str(self.database)))
        self.profile.joinpath('moderation.toml').write_text(policy_text(self.config))
        self.live = Store(self.database)
        self.addCleanup(self.live.close)
        self.engine = Engine(self.config, self.live, clock=lambda: 1000.)
        self.worker = MessageScreener(self.engine, None)
        self.ledger = ScreeningEvalLedger(self.database, write=True)
        self.addCleanup(self.ledger.close)
        self.prepared = requests(self.config.classifier)
        self.now = 1000.
        self.monotonic = 50.
        self.provider = AsyncMock(return_value=result())

    async def sleep(self, seconds):
        self.now += seconds
        self.monotonic += seconds

    async def run_cases(self, prepared=None, **kwargs):
        return await run_evaluation(self.ledger, self.config, self.prepared if prepared is None else prepared,
            self.provider, clock=lambda: self.now, monotonic=lambda: self.monotonic,
            sleep=self.sleep, **kwargs)

    def live_state(self):
        names = [row[0] for row in self.live.db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                 if row[0] != 'settings' and not row[0].startswith('screening_eval_')]
        data = {name: [tuple(row) for row in self.live.db.execute('SELECT * FROM ' + name)] for name in names}
        data['settings'] = [tuple(row) for row in self.live.db.execute('SELECT * FROM settings ORDER BY key')
                            if not row['key'].startswith(('screening_daily_', 'screening_total_'))
                            and row['key'] not in ('screening_budget_day', 'screening_last_attempt_at')]
        return data

    async def test_full_suite_requests_and_successful_run_are_isolated(self):
        self.assertEqual(len(CASES), 44)
        self.assertEqual(len({c.name for c in CASES}), 44)
        self.assertEqual(len({c.text for c in CASES}), 44)
        for case, payload, digest in self.prepared:
            value = json.loads(payload)
            self.assertEqual(set(value), {'model', 'state', 'questions'})
            self.assertEqual(value['questions'], QUESTIONS)
            self.assertEqual(set(value['state']), {'texts', 'urls'})
            self.assertEqual(value['state']['texts'], [case.text])
            self.assertLess(len(payload), self.config.classifier.max_request_bytes)
            self.assertTrue(set(case.expected_purposes) <= RUBRIC['criteria'].keys())
            self.assertTrue(set(case.expected_concerns) <= CONCERN['criteria'].keys())
        before = self.live_state()
        self.provider.side_effect = [result(c.expected_concerns[0], c.expected_purposes[0]) for c in CASES]
        data = await self.run_cases()
        self.assertEqual((data['successful'], data['matched'], data['new_attempts']), (44, 44, 44))
        self.assertEqual(data['benign_auto_delete_cases'], [])
        self.assertEqual(data['groups']['benign']['evaluated'], 20)
        self.assertEqual(data['groups']['harmful']['would_qualify_for_auto_delete'], 16)
        self.assertEqual(data['groups']['ambiguous']['evaluated'], 8)
        self.assertEqual(self.live_state(), before)
        self.assertEqual(self.live.get_setting('screening_total_calls'), 44)
        self.assertEqual(self.live.get_setting('screening_total_reserved_microusd'), 44 * 21)
        self.assertLessEqual(max(map(len, format_report(data).splitlines())), 32)
        again = await self.run_cases()
        self.assertEqual(again['new_attempts'], 0)
        self.assertEqual(self.provider.await_count, 44)

    async def test_benign_false_delete_report_and_harmful_misses(self):
        picked = [self.prepared[0], self.prepared[1], self.prepared[20], self.prepared[36]]
        self.provider.side_effect = [result('sensitive_request', 'other'), result('suspicious_offer', 'promotion'),
                                     result('none', 'other'), result('sensitive_request', 'other')]
        data = await self.run_cases(picked)
        self.assertEqual(data['benign_auto_delete_cases'], [CASES[0].name, CASES[1].name])
        self.assertEqual(data['benign_report_cases'], [CASES[0].name, CASES[1].name])
        self.assertEqual(data['harmful_unreported_cases'], [CASES[20].name])
        self.assertEqual(data['groups']['ambiguous']['would_qualify_for_auto_delete'], 1)
        self.assertIn('FALSE DELETE RISK', format_report(data))
        # Report does not count an ambiguous case as an innocent-message error.
        self.assertEqual(len(data['benign_auto_delete_cases']), 2)

    async def test_actual_decision_boundaries_with_saved_result(self):
        case = CASES[20]
        for concern, purpose, score, flagged, auto in (
            ('sensitive_request', 'other', .89999, True, False),
            ('sensitive_request', 'other', .90, True, True),
            ('sensitive_request', 'other', .90001, True, True),
            ('sensitive_request', 'quoted_warning', 1., True, False),
            ('sensitive_request', 'unclear', 1., True, False),
            ('suspicious_offer', 'promotion', .90, True, True),
            ('impersonation', 'other', .90, True, True),
            ('targeted_abuse', 'other', .90, True, True),
            ('none', 'promotion', 1., False, False),
            ('unclear', 'unclear', 1., False, False)):
            with self.subTest(concern=concern, purpose=purpose, score=score):
                self.assertEqual(decision(case, validate_screening(result(concern, purpose, score))),
                    {'would_report': flagged, 'would_qualify_for_auto_delete': auto})

    async def test_invalid_timeout_and_provider_failure_not_passed_or_retried(self):
        for index, failure in enumerate(({}, TimeoutError(), ProviderError('rate_limited'), RuntimeError('SECRET VALUE'))):
            with self.subTest(failure=type(failure).__name__):
                self.provider.side_effect = failure if isinstance(failure, Exception) else None
                self.provider.return_value = failure
                run_id = 'failure-' + str(index)
                data = await self.run_cases(self.prepared[:2], run_id=run_id)
                self.assertEqual((data['successful'], data['new_attempts']), (0, 1))
                self.assertEqual(data['groups']['benign']['evaluated'], 0)
                self.assertEqual(data['unknown_cost_attempts'], 1)
                self.assertNotIn('SECRET', json.dumps(data))
                self.assertEqual(data['records'][1]['outcome'], 'not_run')
                calls = self.provider.await_count
                saved = await self.run_cases(self.prepared[:1], run_id=run_id)
                self.assertEqual(saved['new_attempts'], 0)
                self.assertEqual(self.provider.await_count, calls)

    async def test_policy_change_after_provider_saves_usage_without_decision(self):
        current = True
        async def provider(payload):
            nonlocal current
            current = False
            return result('sensitive_request', 'other')
        self.provider.side_effect = provider
        data = await self.run_cases(self.prepared[:1], current=lambda: current)
        self.assertEqual(data['stopped'], 'stale')
        self.assertEqual(data['successful'], 0)
        self.assertIsNone(data['records'][0]['would_qualify_for_auto_delete'])
        self.assertEqual(data['estimated_microusd'], 21)
        self.assertEqual(self.live.get_setting('screening_total_reserved_microusd'), 21)

    async def test_cancel_retains_reservation_and_resume_does_not_retry(self):
        self.provider.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.run_cases(self.prepared[:1])
        self.assertEqual(self.live.get_setting('screening_total_reserved_microusd'), RESERVED_MICROUSD)
        data = await self.run_cases(self.prepared[:1])
        self.assertEqual(data['records'][0]['outcome'], 'uncertain')
        self.assertEqual(data['new_attempts'], 0)
        self.provider.assert_awaited_once()

    async def test_usage_guard_and_request_or_fixture_mismatch(self):
        raw = result()
        raw['usage']['input_tokens'] = 65537
        self.provider.return_value = raw
        data = await self.run_cases(self.prepared[:1])
        self.assertEqual(data['stopped'], 'usage_exceeds_reservation')
        self.assertTrue(self.live.get_setting('screening_billing_guard'))
        self.assertIsNone(data['records'][0]['would_report'])
        self.assertEqual(data['estimated_microusd'], (65537 * 42 + 999) // 1000)
        with self.assertRaisesRegex(ValueError, 'fixed screening'):
            await self.run_cases([(CASES[0], b'changed', self.prepared[0][2])])
        self.ledger.db.execute(f'UPDATE {TABLE} SET fixture_hash=?', ('0' * 64,))
        data = report(self.ledger, SUITE, self.prepared[:1])
        self.assertEqual(data['records'][0]['outcome'], 'unavailable')

    async def test_daily_cap_partial_resume_only_unattempted_cases(self):
        self.config = replace(self.config, classifier=replace(self.config.classifier, max_daily_calls=1))
        self.live.set_setting('policy_hash', self.config.policy_hash)
        data = await self.run_cases(self.prepared[:2])
        self.assertEqual((data['new_attempts'], data['stopped']), (1, 'budget_exhausted'))
        self.now += 86400
        data = await self.run_cases(self.prepared[:2])
        self.assertEqual((data['new_attempts'], data['successful']), (1, 2))
        self.assertEqual(self.provider.await_count, 2)

    async def test_cli_preview_no_profile_access_and_results_no_key_or_provider(self):
        with patch('sys.argv', ['evaluation', 'preview']), patch('liberdus_moderator.screening_eval.load_policy') as load, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(), 0)
        load.assert_not_called()
        self.assertFalse(json.loads(out.getvalue())['provider_called'])
        with patch('sys.argv', ['evaluation', 'results', '--json']), patch('pathlib.Path.home', return_value=self.home), \
                patch('liberdus_moderator.screening_eval.profile_key') as key, \
                patch('liberdus_moderator.screening_eval.evaluate') as provider, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(), 2)
        key.assert_not_called()
        provider.assert_not_called()
        self.assertEqual(json.loads(out.getvalue())['successful'], 0)

    async def test_bundle_preview_and_preservation_of_existing_artifact(self):
        root = Path(__file__).resolve().parents[1]
        output = self.home / 'evaluation.pyz'
        build(root, output)
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(archive.read('liberdus_moderator/screening.py'), (root / 'liberdus_moderator/screening.py').read_bytes())
        process = subprocess.run([sys.executable, '-B', str(output), 'preview'], capture_output=True, text=True, check=True)
        preview = json.loads(process.stdout)
        self.assertEqual(preview['maximum_attempts'], 44)
        self.assertEqual(preview['maximum_initial_reservation_microusd'], 121132)
        self.assertEqual(preview['rubric_hash'], SCREENING_HASH)
        with self.assertRaises(ValueError):
            build(root, output)

    def seed_legacy_failure(self, prepared=None, source_run=SUITE):
        case, _, digest = (prepared or self.prepared)[0]
        self.ledger.initialize()
        self.assertEqual(self.ledger.reserve(source_run, case, digest, self.config, self.now), 'reserved')
        self.ledger.finish(source_run, digest, 'provider_or_response_error', self.now + .1, 100)
        return case, digest

    async def test_focused_retry_preserves_baseline_and_cannot_repeat_charge(self):
        picked = [self.prepared[29]]
        case, digest = self.seed_legacy_failure(picked)
        baseline = dict(self.ledger.row(SUITE, digest))
        target = retry_run_name(SUITE, case)
        self.provider.return_value = result('impersonation', 'other')
        data = await self.run_cases(picked, run_id=target, source_run_id=SUITE)
        self.provider.assert_awaited_once_with(picked[0][1])
        self.assertEqual((len(data['records']), data['successful'], data['new_attempts']), (1, 1, 1))
        self.assertEqual(data['retry']['source_run_id'], SUITE)
        self.assertEqual(data['retry']['source_outcome'], 'provider_or_response_error')
        self.assertEqual(dict(self.ledger.row(SUITE, digest)), baseline)
        self.assertEqual(self.live.get_setting('screening_total_calls'), 2)
        self.assertEqual(self.live.get_setting('screening_total_reserved_microusd'), RESERVED_MICROUSD + 21)
        self.assertIn('Original result preserved.', format_report(data))
        self.assertIn('AUTO-DELETE CANDIDATE', format_report(data))
        self.assertEqual(data['decision_version'], 'moderator-0.5.15')
        self.assertIn('Got concern: impersonation', format_report(data))
        again = await self.run_cases(picked, run_id=target, source_run_id=SUITE)
        self.assertEqual(again['new_attempts'], 0)
        self.provider.assert_awaited_once()
        original = report(self.ledger, SUITE, picked)
        self.assertEqual(original['successful'], 0)
        self.assertIn('details were not recorded', original['records'][0]['diagnostic_message'])
        self.assertLessEqual(max(map(len, format_report(data).splitlines())), 32)

    async def test_retry_failure_has_diagnostic_but_no_automatic_second_attempt(self):
        picked = [self.prepared[29]]
        case, _ = self.seed_legacy_failure(picked)
        target = retry_run_name(SUITE, case)
        raw = result('impersonation', 'other')
        raw['answers']['concern']['probabilities']['impersonation'] = .8
        raw['secret_extra'] = 'SECRET TOKEN RESPONSE'
        self.provider.return_value = raw
        data = await self.run_cases(picked, run_id=target, source_run_id=SUITE)
        self.assertEqual(data['records'][0]['outcome'], 'invalid_response')
        self.assertEqual(data['records'][0]['diagnostic'], 'concern.probability_sum')
        self.assertNotIn('SECRET', json.dumps(data))
        self.assertIn('concern.probability_sum', format_report(data))
        self.assertEqual(self.live.get_setting('screening_total_reserved_microusd'), 2 * RESERVED_MICROUSD)
        self.assertEqual((await self.run_cases(picked, run_id=target, source_run_id=SUITE))['new_attempts'], 0)
        self.provider.assert_awaited_once()

    async def test_retry_rejects_missing_successful_source_and_multiple_cases(self):
        case, _, _ = self.prepared[29]
        target = retry_run_name(SUITE, case)
        with self.assertRaises(ValueError):
            await self.run_cases([self.prepared[29]], run_id=target, source_run_id=SUITE)
        self.provider.assert_not_awaited()
        self.provider.return_value = result('impersonation', 'other')
        await self.run_cases([self.prepared[29]])
        before = self.live.get_setting('screening_total_calls')
        self.provider.reset_mock()
        with self.assertRaises(ValueError):
            await self.run_cases([self.prepared[29]], run_id=target, source_run_id=SUITE)
        with self.assertRaises(ValueError):
            await self.run_cases(self.prepared[:2], run_id=target, source_run_id=SUITE)
        with self.assertRaises(ValueError):
            await self.run_cases([self.prepared[29]], run_id=SUITE, source_run_id=SUITE)
        self.provider.assert_not_awaited()
        self.assertEqual(self.live.get_setting('screening_total_calls'), before)

    async def test_bound_retry_cannot_accidentally_run_whole_suite(self):
        picked = [self.prepared[29]]
        case, digest = self.seed_legacy_failure(picked)
        target = retry_run_name(SUITE, case)
        self.ledger.bind_retry(SUITE, target, case, digest)
        with self.assertRaisesRegex(ValueError, 'only the case'):
            await self.run_cases(run_id=target)
        self.provider.assert_not_awaited()
        self.assertEqual(self.live.get_setting('screening_total_calls'), 1)

    async def test_runner_network_and_validation_failures_have_separate_codes(self):
        for run_id, value, outcome, code in (
            ('connection', ConnectionError('SECRET URL AND TOKEN'), 'network_error', 'network.io'),
            ('internal', RuntimeError('SECRET BODY'), 'internal_error', 'internal.request'),
            ('timeout', TimeoutError('SECRET DETAIL'), 'timeout', 'request.timeout')):
            with self.subTest(run=run_id):
                self.provider.side_effect = value
                data = await self.run_cases(self.prepared[:1], run_id=run_id)
                self.assertEqual(data['records'][0]['outcome'], outcome)
                self.assertEqual(data['records'][0]['diagnostic'], code)
                self.assertNotIn('SECRET', json.dumps(data))

    def test_retry_cli_one_call_success_exit_and_results_use_binding(self):
        picked = [self.prepared[29]]
        case, digest = self.seed_legacy_failure(picked)
        target = retry_run_name(SUITE, case)
        python = self.home / '.hermes/hermes-agent/venv/bin/python'
        python.parent.mkdir(parents=True)
        python.touch()
        provider = AsyncMock(return_value=result('impersonation', 'other'))
        with patch('sys.argv', ['evaluation', 'retry', case.name, '--json']), \
                patch('pathlib.Path.home', return_value=self.home), \
                patch('sys.prefix', str(python.parent.parent)), \
                patch('liberdus_moderator.screening_eval.profile_key', return_value='SECRET KEY'), \
                patch('liberdus_moderator.screening_eval.evaluate', provider), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(), 0)
        data = json.loads(out.getvalue())
        self.assertEqual((data['successful'], data['run_id']), (1, target))
        self.assertEqual(len(data['records']), 1)
        provider.assert_awaited_once()
        with patch('sys.argv', ['evaluation', 'results', '--run-id', target, '--json']), \
                patch('pathlib.Path.home', return_value=self.home), \
                patch('liberdus_moderator.screening_eval.profile_key') as key, \
                patch('liberdus_moderator.screening_eval.evaluate') as api, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(), 0)
        key.assert_not_called()
        api.assert_not_called()
        self.assertEqual(len(json.loads(out.getvalue())['records']), 1)

    def test_one_case_preview_is_profile_free_and_has_stable_retry_name(self):
        case = CASES[29]
        with patch('sys.argv', ['evaluation', 'preview', case.name]), \
                patch('liberdus_moderator.screening_eval.load_policy') as policy, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(), 0)
        preview = json.loads(out.getvalue())
        self.assertEqual(preview['maximum_attempts'], 1)
        self.assertEqual(preview['maximum_initial_reservation_microusd'], RESERVED_MICROUSD)
        self.assertEqual(preview['run_id'], retry_run_name(SUITE, case))
        self.assertEqual(preview['cases'][0]['name'], case.name)
        self.assertFalse(preview['provider_called'])
        policy.assert_not_called()
        self.assertNotEqual(retry_run_name('other-source', case), preview['run_id'])

    def test_retry_cli_rejects_bad_or_missing_case_before_profile_access(self):
        for argv in (['evaluation', 'retry'], ['evaluation', 'retry', 'unknown'],
                     ['evaluation', 'run', CASES[29].name], ['evaluation', 'results', CASES[29].name]):
            with self.subTest(argv=argv), patch('sys.argv', argv), \
                    patch('liberdus_moderator.screening_eval.load_policy') as policy, \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main()
            self.assertEqual(error.exception.code, 2)
            policy.assert_not_called()
