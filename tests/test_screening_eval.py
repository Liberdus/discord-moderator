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
from liberdus_moderator.screening_eval import (decision, format_report, main, report, requests, run_evaluation)
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
                 if row[0] not in ('settings', TABLE)]
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
        self.assertEqual(data['groups']['harmful']['would_qualify_for_auto_delete'], 8)
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
        self.assertEqual(data['benign_auto_delete_cases'], [CASES[0].name])
        self.assertEqual(data['benign_report_cases'], [CASES[0].name, CASES[1].name])
        self.assertEqual(data['harmful_unreported_cases'], [CASES[20].name])
        self.assertEqual(data['groups']['ambiguous']['would_qualify_for_auto_delete'], 1)
        self.assertIn('FALSE DELETE RISK', format_report(data))
        # Report does not count an ambiguous case as an innocent-message error.
        self.assertEqual(len(data['benign_auto_delete_cases']), 1)

    async def test_actual_decision_boundaries_with_saved_result(self):
        case = CASES[20]
        for concern, purpose, score, flagged, auto in (
            ('sensitive_request', 'other', .90, True, False),
            ('sensitive_request', 'other', .90001, True, True),
            ('sensitive_request', 'quoted_warning', 1., True, False),
            ('sensitive_request', 'unclear', 1., True, False),
            ('suspicious_offer', 'promotion', 1., True, False),
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
