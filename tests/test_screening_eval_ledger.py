"""Standalone screening evaluations share allowance, never live incident state."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock

from liberdus_moderator.classifier import MODEL, RESERVED_INPUT_TOKENS, RESERVED_MICROUSD, RUBRIC
from liberdus_moderator.config import ClassifierSettings, Config, StorageSettings
from liberdus_moderator.engine import Engine
from liberdus_moderator.models import MessageEvent
from liberdus_moderator.screening import CONCERN, SCREENING_HASH, MessageScreener, validate_screening
from liberdus_moderator.screening_eval_ledger import (ScreeningEvalLedger, TABLE, MAX_RECORDS,
                                                      DIAGNOSTICS_TABLE, RETRY_TABLE)
from liberdus_moderator.screening_diagnostics import DIAGNOSTIC_CODES
from liberdus_moderator.storage import Store

BUDGET_KEYS = {'screening_' + name for name in ('budget_day', 'daily_calls', 'total_calls',
    'daily_reserved_microusd', 'total_reserved_microusd', 'last_attempt_at', 'billing_guard')}


def result(tokens=543):
    answers = {}
    for name, criteria, choice in (('context', RUBRIC['criteria'], 'other'),
                                    ('concern', CONCERN['criteria'], 'none')):
        answers[name] = dict(type='choice', choice=choice, confidence=1.,
            probabilities={label: float(label == choice) for label in criteria})
    return validate_screening(dict(model=MODEL, answers=answers,
        usage=dict(input_tokens=tokens, output_tokens=54)))


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


class ScreeningEvalLedgerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'moderation.sqlite3'
        self.now = 1000.
        self.config = Config('1', '99', ('10', '11', '12'), ('20',), ('98',),
            schema_version=2, ai_enabled=True, actions_enabled=True,
            classifier=ClassifierSettings(mode='report_only', max_daily_calls=100, max_total_calls=1000,
                daily_budget_microusd=1000000, total_budget_microusd=4000000),
            storage=StorageSettings(database_path=str(self.path)))
        self.live = Store(str(self.path))
        self.addCleanup(self.live.close)
        self.engine = Engine(self.config, self.live, clock=lambda: self.now)
        self.worker = MessageScreener(self.engine, Mock())
        for index, channel in enumerate(('10', '11', '12')):
            self.engine.process(MessageEvent('1', channel, str(100 + index), '50',
                'Retained live repetition evidence.', self.now, author_role_ids=('1',)))
        self.ledger = ScreeningEvalLedger(self.path, write=True)
        self.addCleanup(self.ledger.close)
        self.ledger.initialize()
        self.case = SimpleNamespace(name='ordinary', fixture_hash=digest('fixed-fixture'))
        self.request = digest('fixed-request')

    def reserve(self, run='first', request=None, now=None, config=None):
        return self.ledger.reserve(run, self.case, request or self.request,
                                   config or self.config, self.now if now is None else now)

    def settings(self):
        return {row['key']: row['value'] for row in self.live.db.execute('SELECT * FROM settings')}

    def live_state(self):
        tables = [row[0] for row in self.live.db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                  if row[0] not in ('settings', TABLE, DIAGNOSTICS_TABLE, RETRY_TABLE)]
        return {table: [tuple(row) for row in self.live.db.execute('SELECT * FROM ' + table)] for table in tables}

    def test_success_settles_once_and_preserves_all_live_state(self):
        before, settings = self.live_state(), self.settings()
        self.assertIsNone(self.ledger.gate(self.config))  # Actions ON does not grant this runner actions.
        self.assertEqual(self.reserve(), 'reserved')
        self.ledger.finish('first', self.request, 'ok', self.now + .3, 300, result())
        amount = (543 * 42 + 999) // 1000
        self.assertEqual(self.live.get_setting('screening_total_calls'), 1)
        self.assertEqual(self.live.get_setting('screening_daily_calls'), 1)
        self.assertEqual(self.live.get_setting('screening_total_reserved_microusd'), amount)
        self.assertEqual(self.live.get_setting('screening_daily_reserved_microusd'), amount)
        row = self.ledger.row('first', self.request)
        self.assertEqual(row['outcome'], 'ok')
        self.assertEqual(json.loads(row['result_json'])['estimated_microusd'], amount)
        self.assertEqual((row['model'], row['rubric_hash'], row['fixture_hash']),
                         (MODEL, SCREENING_HASH, self.case.fixture_hash))
        self.assertEqual(row['policy_hash'], self.config.policy_hash)
        self.assertEqual(row['day'], 0)
        after = self.settings()
        self.assertTrue({key for key in settings.keys() | after.keys() if settings.get(key) != after.get(key)} <= BUDGET_KEYS)
        self.assertEqual(self.live_state(), before)
        self.ledger.finish('first', self.request, 'ok', self.now + 1, 1000, result(10000))
        self.assertEqual(dict(self.ledger.row('first', self.request)), dict(row))
        self.assertEqual(self.settings(), after)

    def test_recovery_marks_only_evaluation_running_and_retains_unknown_charge(self):
        job = self.worker.snapshot('100')
        self.assertTrue(self.worker.reserve(job))
        self.now += 6
        self.assertEqual(self.reserve(), 'reserved')
        before, settings = self.live_state(), self.settings()
        self.ledger.initialize()
        self.assertEqual(self.ledger.row('first', self.request)['outcome'], 'uncertain')
        self.assertEqual(self.live_state(), before)
        self.assertEqual(self.settings(), settings)
        self.assertEqual(self.reserve(), 'cached')
        self.ledger.finish('first', self.request, 'ok', self.now + 1, 100, result())
        self.assertEqual(self.ledger.row('first', self.request)['outcome'], 'uncertain')
        self.assertEqual(self.settings(), settings)

    def test_cached_attempt_is_never_replayed_even_if_policy_or_mode_changes(self):
        self.assertEqual(self.reserve(), 'reserved')
        self.ledger.finish('first', self.request, 'timeout', self.now + 1, 1000)
        self.live.set_setting('paused', True)
        self.assertEqual(self.reserve(), 'cached')
        self.assertEqual(self.live.get_setting('screening_total_calls'), 1)
        self.assertEqual(self.reserve(run='new'), 'moderation_paused')
        self.live.set_setting('paused', False)
        self.assertEqual(self.reserve(run='new', now=self.now + 6), 'reserved')
        self.assertEqual(self.live.get_setting('screening_total_calls'), 2)

    def test_mode_policy_pause_schema_and_billing_gates(self):
        for config, reason in ((replace(self.config, ai_enabled=False, classifier=replace(self.config.classifier, mode='off')), 'report_only_required'),
                               (replace(self.config, classifier=replace(self.config.classifier, mode='shadow')), 'report_only_required')):
            self.assertEqual(self.ledger.gate(config), reason)
        for key, value, reason in (('policy_hash', 'different', 'policy_changed'),
                ('paused', True, 'moderation_paused'), ('screening_schema_version', 2, 'unsupported_screening_schema'),
                ('screening_schema_version', True, 'unsupported_screening_schema'),
                ('screening_billing_guard', True, 'billing_guard'),
                ('paused', 'false', 'invalid_shared_state'), ('screening_billing_guard', None, 'invalid_shared_state')):
            old = self.live.get_setting(key)
            with self.subTest(key=key, value=value):
                self.live.set_setting(key, value)
                self.assertEqual(self.reserve(), reason)
                self.assertIsNone(self.ledger.row('first', self.request))
            if old is None:
                self.live.db.execute('DELETE FROM settings WHERE key=?', (key,))
            else:
                self.live.set_setting(key, old)
        self.live.db.execute("DELETE FROM settings WHERE key='screening_schema_version'")
        self.assertEqual(self.reserve(), 'unsupported_screening_schema')

    def test_rate_and_budget_are_shared_between_two_ledger_connections(self):
        other = ScreeningEvalLedger(self.path, write=True)
        self.addCleanup(other.close)
        self.assertEqual(self.reserve(), 'reserved')
        self.assertEqual(other.reserve('second', self.case, self.request, self.config, self.now), 'wait')
        self.assertEqual(other.reserve('second', self.case, self.request, self.config, self.now + 6), 'reserved')
        self.assertEqual(self.live.get_setting('screening_total_calls'), 2)
        self.assertEqual(self.live.get_setting('screening_total_reserved_microusd'), 2 * RESERVED_MICROUSD)

    def test_concurrent_connections_cannot_both_reserve_the_same_rate_slot(self):
        barrier = Barrier(2)
        def reserve(run):
            with ScreeningEvalLedger(self.path, write=True) as ledger:
                barrier.wait()
                return ledger.reserve(run, self.case, self.request, self.config, self.now)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(reserve, name) for name in ('one', 'two')]
            results = [future.result(timeout=5) for future in futures]
        self.assertCountEqual(results, ['reserved', 'wait'])
        self.assertEqual(self.live.get_setting('screening_total_calls'), 1)

    def test_eval_reservation_blocks_live_worker_at_shared_call_limit(self):
        config = replace(self.config, classifier=replace(self.config.classifier, max_total_calls=1))
        self.live.set_setting('policy_hash', config.policy_hash)
        self.engine.config = config
        worker = MessageScreener(self.engine, Mock())
        self.assertEqual(self.reserve(config=config), 'reserved')
        self.now += 6
        self.assertFalse(worker.reserve(worker.snapshot('100')))
        self.assertEqual(self.live.get_setting('screening_total_calls'), 1)

    def test_live_worker_reservation_blocks_eval_at_shared_call_limit(self):
        config = replace(self.config, classifier=replace(self.config.classifier, max_total_calls=1))
        self.live.set_setting('policy_hash', config.policy_hash)
        self.engine.config = config
        worker = MessageScreener(self.engine, Mock())
        self.assertTrue(worker.reserve(worker.snapshot('100')))
        self.now += 6
        before = self.live_state()
        self.assertEqual(self.reserve(config=config), 'budget_exhausted')
        self.assertIsNone(self.ledger.row('first', self.request))
        self.assertEqual(self.live_state(), before)

    def test_each_limit_stops_before_a_new_reservation(self):
        limits = (dict(max_daily_calls=1), dict(max_total_calls=1),
                  dict(daily_budget_microusd=RESERVED_MICROUSD), dict(total_budget_microusd=RESERVED_MICROUSD))
        self.assertEqual(self.reserve(), 'reserved')
        self.now += 6
        for changes in limits:
            config = replace(self.config, classifier=replace(self.config.classifier, **changes))
            self.live.set_setting('policy_hash', config.policy_hash)
            before = self.settings()
            self.assertEqual(self.reserve(run='second', config=config), 'budget_exhausted')
            self.assertEqual(self.settings(), before)
        self.assertEqual(self.live.get_setting('screening_total_calls'), 1)

    def test_day_rollover_late_settlement_does_not_refund_new_day(self):
        self.assertEqual(self.reserve(), 'reserved')
        self.now = 86401.
        self.assertEqual(self.reserve(run='next-day'), 'reserved')
        self.assertEqual(self.live.get_setting('screening_daily_calls'), 1)
        self.assertEqual(self.live.get_setting('screening_daily_reserved_microusd'), RESERVED_MICROUSD)
        self.ledger.finish('first', self.request, 'stale', self.now + 1, 100, result())
        amount = (543 * 42 + 999) // 1000
        self.assertEqual(self.live.get_setting('screening_total_reserved_microusd'), RESERVED_MICROUSD + amount)
        self.assertEqual(self.live.get_setting('screening_daily_reserved_microusd'), RESERVED_MICROUSD)
        self.ledger.finish('next-day', self.request, 'ok', self.now + 2, 100, result())
        self.assertEqual(self.live.get_setting('screening_total_reserved_microusd'), 2 * amount)
        self.assertEqual(self.live.get_setting('screening_daily_reserved_microusd'), amount)

    def test_clock_rollback_never_resets_accounting(self):
        self.assertEqual(self.reserve(now=86401.), 'reserved')
        before = self.settings()
        self.assertEqual(self.reserve(run='earlier', now=1000), 'clock_rollback')
        self.assertEqual(self.reserve(run='earlier', now=86400), 'clock_rollback')
        self.assertEqual(self.settings(), before)

    def test_malformed_or_negative_counters_fail_closed_even_on_rollover(self):
        for key in ('screening_daily_calls', 'screening_total_calls', 'screening_daily_reserved_microusd',
                    'screening_total_reserved_microusd', 'screening_budget_day'):
            for bad in (-1, True, 1.5, '1', None, [], 2**100):
                with self.subTest(key=key, bad=bad):
                    self.live.set_setting(key, bad)
                    before = self.settings()
                    with self.assertRaises(ValueError):
                        self.reserve(now=86401.)
                    self.assertEqual(self.settings(), before)
                    self.assertIsNone(self.ledger.row('first', self.request))
                self.live.db.execute('DELETE FROM settings WHERE key=?', (key,))
        for bad in (-1, True, '1', None, float('nan'), float('inf'), 2**2000):
            self.live.set_setting('screening_last_attempt_at', bad)
            with self.assertRaises(ValueError):self.reserve()
        self.live.db.execute("DELETE FROM settings WHERE key='screening_last_attempt_at'")

    def test_unknown_charge_outcomes_keep_full_reservation(self):
        for index, outcome in enumerate(('timeout', 'uncertain', 'authentication_failed', 'invalid_json', 'stale')):
            run = 'run-' + str(index)
            self.assertEqual(self.reserve(run=run, now=self.now + 6 * index), 'reserved')
            self.ledger.finish(run, self.request, outcome, self.now + 6 * index + 1, 100)
        self.assertEqual(self.live.get_setting('screening_total_reserved_microusd'), 5 * RESERVED_MICROUSD)
        self.assertEqual(self.live.get_setting('screening_total_calls'), 5)

    def test_excess_usage_settles_known_cost_once_and_blocks_future_calls(self):
        self.assertEqual(self.reserve(), 'reserved')
        tokens = RESERVED_INPUT_TOKENS + 10000
        self.ledger.finish('first', self.request, 'ok', self.now + 1, 100, result(tokens))
        amount = (tokens * 42 + 999) // 1000
        self.assertEqual(self.live.get_setting('screening_total_reserved_microusd'), amount)
        self.assertEqual(self.live.get_setting('screening_daily_reserved_microusd'), amount)
        self.assertTrue(self.live.get_setting('screening_billing_guard'))
        self.assertEqual(self.ledger.row('first', self.request)['outcome'], 'usage_exceeds_reservation')
        self.assertEqual(self.reserve(run='next', now=self.now + 6), 'billing_guard')
        before = self.settings()
        self.ledger.finish('first', self.request, 'usage_exceeds_reservation', self.now + 2, 100, result(tokens))
        self.assertEqual(self.settings(), before)

    def test_unknown_excess_usage_keeps_reservation_and_sets_guard(self):
        self.assertEqual(self.reserve(), 'reserved')
        self.ledger.finish('first', self.request, 'usage_exceeds_reservation', self.now + 1, 100)
        self.assertEqual(self.live.get_setting('screening_total_reserved_microusd'), RESERVED_MICROUSD)
        self.assertTrue(self.live.get_setting('screening_billing_guard'))

    def test_invalid_result_cannot_refund_or_change_attempt(self):
        self.assertEqual(self.reserve(), 'reserved')
        before, row = self.settings(), dict(self.ledger.row('first', self.request))
        invalid = ({}, {**result(), 'input_tokens': -1}, {**result(), 'concern': 'invented'},
                   {**result(), 'model': 'wrong'}, {**result(), 'confidence': float('nan')})
        for bad in invalid:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.ledger.finish('first', self.request, 'ok', self.now + 1, 100, bad)
            self.assertEqual(self.settings(), before)
            self.assertEqual(dict(self.ledger.row('first', self.request)), row)
        with self.assertRaises(ValueError):self.ledger.finish('first', self.request, 'ok', self.now + 1, 100)
        self.ledger.finish('first', self.request, 'provider_or_response_error', self.now + 1, 100)
        self.assertEqual(self.live.get_setting('screening_total_reserved_microusd'), RESERVED_MICROUSD)

    def test_corrupt_settlement_counters_cannot_become_negative(self):
        self.assertEqual(self.reserve(), 'reserved')
        self.live.set_setting('screening_daily_reserved_microusd', 0)
        self.live.set_setting('screening_total_reserved_microusd', 0)
        before = self.settings()
        with self.assertRaises(ValueError):
            self.ledger.finish('first', self.request, 'ok', self.now + 1, 100, result())
        self.assertEqual(self.settings(), before)
        self.assertEqual(self.ledger.row('first', self.request)['outcome'], 'running')

    def test_bounded_read_hides_oversized_or_nontext_saved_results(self):
        self.assertEqual(self.reserve(), 'reserved')
        for value in ('x' * 8193, '😀' * 3000, b'not-text'):
            self.ledger.db.execute(f'UPDATE {TABLE} SET result_json=?', (value,))
            self.assertIsNone(self.ledger.row('first', self.request)['result_json'])
        self.ledger.db.execute(f'UPDATE {TABLE} SET result_json=?', ('x' * 8192,))
        self.assertEqual(len(self.ledger.row('first', self.request)['result_json']), 8192)

    def test_history_capacity_is_bounded_and_no_budget_is_reserved_on_full(self):
        self.assertEqual(self.reserve(), 'reserved')
        row = tuple(self.ledger.db.execute(f'SELECT * FROM {TABLE}').fetchone())
        with self.ledger.transaction():
            for index in range(MAX_RECORDS - 1):
                self.ledger.db.execute(f'INSERT INTO {TABLE} VALUES({",".join("?" for _ in row)})',
                                      ('filler-' + str(index),) + row[1:])
        before = self.settings()
        self.assertEqual(self.reserve(run='new', now=self.now + 6), 'history_full')
        self.assertEqual(self.settings(), before)
        self.assertEqual(self.reserve(), 'cached')

    def test_readonly_results_do_not_create_table_or_modify_database(self):
        self.ledger.db.execute(f'DROP TABLE {TABLE}')
        before = list(self.live.db.iterdump())
        with ScreeningEvalLedger(self.path) as readonly:
            self.assertIsNone(readonly.row('first', self.request))
        self.assertEqual(list(self.live.db.iterdump()), before)

    def test_invalid_arguments_do_not_write(self):
        before = list(self.live.db.iterdump())
        for name in ('', 'A', 'a' * 33, None):
            with self.assertRaises(ValueError):self.reserve(run=name)
        for value in ('not-digest', 'f' * 63, 'A' * 64):
            with self.assertRaises(ValueError):self.reserve(request=value)
        for value in (0, -1, True, float('nan'), float('inf')):
            with self.assertRaises(ValueError):self.reserve(now=value)
        for fixture in (SimpleNamespace(name='bad\nname', fixture_hash=digest('x')),
                        SimpleNamespace(name='valid', fixture_hash='invalid')):
            with self.assertRaises(ValueError):
                self.ledger.reserve('first', fixture, self.request, self.config, self.now)
        self.assertEqual(list(self.live.db.iterdump()), before)

    def failed_source(self, run='first', *, diagnostic=None):
        self.assertEqual(self.reserve(run=run), 'reserved')
        self.ledger.finish(run, self.request, 'timeout', self.now + 1, 1000, diagnostic=diagnostic)
        return dict(self.ledger.row(run, self.request))

    def test_fixed_diagnostics_are_atomic_private_and_never_overwritten(self):
        self.assertIn('request.timeout', DIAGNOSTIC_CODES)
        before, settings = self.live_state(), self.settings()
        self.assertEqual(self.reserve(), 'reserved')
        self.ledger.finish('first', self.request, 'timeout', self.now + 1, 1000, diagnostic='request.timeout')
        self.assertEqual(self.ledger.diagnostic('first', self.request), 'request.timeout')
        self.assertEqual(self.live.get_setting('screening_total_reserved_microusd'), RESERVED_MICROUSD)
        row = dict(self.ledger.row('first', self.request))
        budget = self.settings()
        self.ledger.finish('first', self.request, 'internal_error', self.now + 2, 2000, diagnostic='request.interrupted')
        self.assertEqual(self.ledger.diagnostic('first', self.request), 'request.timeout')
        self.assertEqual(dict(self.ledger.row('first', self.request)), row)
        self.assertEqual(self.settings(), budget)
        self.assertEqual(self.live_state(), before)
        self.assertTrue({key for key in settings.keys() | budget.keys() if settings.get(key) != budget.get(key)} <= BUDGET_KEYS)

    def test_arbitrary_diagnostics_never_enter_database_or_refund_budget(self):
        self.assertEqual(self.reserve(), 'reserved')
        before = list(self.ledger.db.iterdump())
        for diagnostic in ('Secret token abc123', 'request.timeout\nprovider body', 'x' * 100000, [], 42):
            with self.subTest(kind=type(diagnostic).__name__), self.assertRaises(ValueError):
                self.ledger.finish('first', self.request, 'invalid_response', self.now + 1, 100, diagnostic=diagnostic)
            self.assertEqual(list(self.ledger.db.iterdump()), before)
        self.ledger.finish('first', self.request, 'invalid_response', self.now + 1, 100)
        self.assertIsNone(self.ledger.diagnostic('first', self.request))
        self.assertEqual(self.live.get_setting('screening_total_reserved_microusd'), RESERVED_MICROUSD)

    def test_corrupt_stored_diagnostic_is_hidden_on_read(self):
        self.failed_source(diagnostic='request.timeout')
        for value in ('arbitrary provider body', 'x' * 10000, b'raw response'):
            self.ledger.db.execute(f'UPDATE {DIAGNOSTICS_TABLE} SET code=?', (value,))
            self.assertIsNone(self.ledger.diagnostic('first', self.request))

    def test_new_eval_failure_outcomes_keep_unknown_charges(self):
        for index, outcome in enumerate(('invalid_response', 'network_error', 'internal_error')):
            run = 'failure-' + str(index)
            self.assertEqual(self.reserve(run=run, now=self.now + 6 * index), 'reserved')
            self.ledger.finish(run, self.request, outcome, self.now + 6 * index + 1, 100)
            self.assertEqual(self.ledger.row(run, self.request)['outcome'], outcome)
        self.assertEqual(self.live.get_setting('screening_total_reserved_microusd'), 3 * RESERVED_MICROUSD)

    def test_targeted_retry_preserves_baseline_and_costs_only_one_new_attempt(self):
        baseline = self.failed_source(diagnostic='request.timeout')
        before, settings = self.live_state(), self.settings()
        binding = self.ledger.bind_retry('first', 'retry-one', self.case, self.request)
        self.assertEqual(binding['source_run_id'], 'first')
        self.assertEqual(binding['case_name'], self.case.name)
        self.assertEqual(binding['request_hash'], self.request)
        self.assertEqual(binding['fixture_hash'], self.case.fixture_hash)
        self.assertEqual(binding['model'], MODEL)
        self.assertEqual(binding['rubric_hash'], SCREENING_HASH)
        self.assertEqual(self.ledger.retry_binding('retry-one'), binding)
        self.assertEqual(self.ledger.bind_retry('first', 'retry-one', self.case, self.request), binding)
        self.assertEqual(self.settings(), settings)
        self.assertEqual(self.reserve(run='retry-one', now=self.now + 6), 'reserved')
        self.ledger.finish('retry-one', self.request, 'ok', self.now + 7, 100, result())
        self.assertEqual(self.reserve(run='retry-one', now=self.now + 12), 'cached')
        self.assertEqual(self.ledger.bind_retry('first', 'retry-one', self.case, self.request), binding)
        self.assertEqual(self.live.get_setting('screening_total_calls'), 2)
        self.assertEqual(dict(self.ledger.row('first', self.request)), baseline)
        self.assertEqual(self.ledger.diagnostic('first', self.request), 'request.timeout')
        self.assertEqual(self.live_state(), before)

    def test_retry_source_must_be_matching_terminal_failure(self):
        for source in ('unattempted',):
            with self.assertRaises(ValueError):self.ledger.bind_retry(source, 'retry-one', self.case, self.request)
        self.assertEqual(self.reserve(), 'reserved')
        with self.assertRaises(ValueError):self.ledger.bind_retry('first', 'retry-one', self.case, self.request)
        self.ledger.finish('first', self.request, 'ok', self.now + 1, 100, result())
        with self.assertRaises(ValueError):self.ledger.bind_retry('first', 'retry-one', self.case, self.request)
        self.assertIsNone(self.ledger.retry_binding('retry-one'))
        self.assertEqual(self.live.get_setting('screening_total_calls'), 1)

    def test_retry_refuses_same_run_and_changed_source_provenance(self):
        baseline = self.failed_source()
        with self.assertRaises(ValueError):self.ledger.bind_retry('first', 'first', self.case, self.request)
        for field, value in (('case_name', 'another'), ('fixture_hash', digest('changed')),
                             ('suite', 'other-suite'), ('model', 'other-model'),
                             ('rubric_hash', digest('new-rubric')), ('outcome', 'unknown-error')):
            self.ledger.db.execute(f'UPDATE {TABLE} SET {field}=? WHERE run_id=?', (value, 'first'))
            before = list(self.ledger.db.iterdump())
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.ledger.bind_retry('first', 'retry-one', self.case, self.request)
            self.assertEqual(list(self.ledger.db.iterdump()), before)
            self.ledger.db.execute(f'UPDATE {TABLE} SET {field}=? WHERE run_id=?', (baseline[field], 'first'))
        other_case = SimpleNamespace(name=self.case.name, fixture_hash=digest('revised-fixture'))
        with self.assertRaises(ValueError):self.ledger.bind_retry('first', 'retry-one', other_case, self.request)
        with self.assertRaises(ValueError):self.ledger.bind_retry('first', 'retry-one', self.case, digest('other-request'))
        self.assertIsNone(self.ledger.retry_binding('retry-one'))

    def test_target_run_cannot_adopt_existing_attempt_even_matching_case(self):
        self.failed_source()
        self.assertEqual(self.reserve(run='existing', now=self.now + 6), 'reserved')
        before = list(self.ledger.db.iterdump())
        with self.assertRaises(ValueError):
            self.ledger.bind_retry('first', 'existing', self.case, self.request)
        self.assertEqual(list(self.ledger.db.iterdump()), before)
        self.assertIsNone(self.ledger.retry_binding('existing'))

    def test_retry_binding_cannot_be_rebound_and_restricts_new_charges(self):
        self.failed_source()
        first = self.ledger.bind_retry('first', 'retry-one', self.case, self.request)
        self.assertEqual(self.reserve(run='second-source', now=self.now + 6), 'reserved')
        self.ledger.finish('second-source', self.request, 'network_error', self.now + 7, 100)
        before = self.settings()
        with self.assertRaises(ValueError):
            self.ledger.bind_retry('second-source', 'retry-one', self.case, self.request)
        with self.assertRaises(ValueError):self.reserve(run='retry-one', request=digest('other'), now=self.now + 12)
        other_case = SimpleNamespace(name='different', fixture_hash=self.case.fixture_hash)
        with self.assertRaises(ValueError):
            self.ledger.reserve('retry-one', other_case, self.request, self.config, self.now + 12)
        self.assertEqual(self.ledger.retry_binding('retry-one'), first)
        self.assertEqual(self.settings(), before)
        self.assertIsNone(self.ledger.row('retry-one', self.request))

    def test_explicit_failed_retry_can_source_new_retry_without_baseline_changes(self):
        baseline = self.failed_source()
        self.ledger.bind_retry('first', 'retry-one', self.case, self.request)
        self.assertEqual(self.reserve(run='retry-one', now=self.now + 6), 'reserved')
        self.ledger.finish('retry-one', self.request, 'timeout', self.now + 7, 100)
        self.assertEqual(self.ledger.bind_retry('retry-one', 'retry-two', self.case, self.request)['source_run_id'], 'retry-one')
        with self.assertRaises(ValueError):self.ledger.bind_retry('retry-one', 'first', self.case, self.request)
        self.assertIsNone(self.ledger.retry_binding('first'))
        self.assertEqual(dict(self.ledger.row('first', self.request)), baseline)
        self.assertEqual(self.live.get_setting('screening_total_calls'), 2)

    def test_legacy_attempt_schema_and_readonly_diagnostics_remain_compatible(self):
        baseline = self.failed_source()
        original_schema = self.ledger.db.execute("SELECT sql FROM sqlite_master WHERE name=?", (TABLE,)).fetchone()[0]
        self.ledger.db.execute(f'DROP TABLE {DIAGNOSTICS_TABLE}')
        self.ledger.db.execute(f'DROP TABLE {RETRY_TABLE}')
        before = list(self.live.db.iterdump())
        with ScreeningEvalLedger(self.path) as readonly:
            self.assertEqual(dict(readonly.row('first', self.request)), baseline)
            self.assertIsNone(readonly.diagnostic('first', self.request))
            self.assertIsNone(readonly.retry_binding('first'))
        self.assertEqual(list(self.live.db.iterdump()), before)
        settings, state = self.settings(), self.live_state()
        self.ledger.initialize()
        self.assertEqual(self.ledger.db.execute("SELECT sql FROM sqlite_master WHERE name=?", (TABLE,)).fetchone()[0], original_schema)
        self.assertEqual(dict(self.ledger.row('first', self.request)), baseline)
        self.assertEqual(self.settings(), settings)
        self.assertEqual(self.live_state(), state)

    def test_conflicting_retry_bindings_are_atomic_across_connections(self):
        self.failed_source()
        self.assertEqual(self.reserve(run='second-source', now=self.now + 6), 'reserved')
        self.ledger.finish('second-source', self.request, 'timeout', self.now + 7, 100)
        barrier = Barrier(2)
        def bind(source):
            with ScreeningEvalLedger(self.path, write=True) as ledger:
                barrier.wait()
                try:
                    return ledger.bind_retry(source, 'one-target', self.case, self.request)['source_run_id']
                except ValueError:
                    return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(bind, source) for source in ('first', 'second-source')]
            results = [future.result(timeout=5) for future in futures]
        self.assertEqual(results.count('rejected'), 1)
        self.assertIn(self.ledger.retry_binding('one-target')['source_run_id'], ('first', 'second-source'))
        self.assertEqual(self.live.get_setting('screening_total_calls'), 2)
