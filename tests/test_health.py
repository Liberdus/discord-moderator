import json
from dataclasses import replace
import unittest

from liberdus_moderator.classifier import RESERVED_MICROUSD
from liberdus_moderator.config import ClassifierSettings, Config
from liberdus_moderator.engine import Engine
from liberdus_moderator.health import (HealthMonitor, SETTING, FAILURE_CODES,
                                     FAILURE_THRESHOLD, GAP_REASONS, MAX_COUNT)
from liberdus_moderator.storage import Store


def config(**limits):
    settings = dict(mode='report_only', max_daily_calls=100, max_total_calls=1000,
                    daily_budget_microusd=1000000, total_budget_microusd=4000000)
    settings.update(limits)
    return Config('1', '99', ('10', '11', '12'), ('20', '21'), ('98',),
                  schema_version=2, ai_enabled=True, classifier=ClassifierSettings(**settings))


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.now = 5 * 86400 + 1000.
        self.store = Store(':memory:')
        self.addCleanup(self.store.close)
        self.engine = Engine(config(), self.store, clock=lambda: self.now)
        self.monitor = HealthMonitor(self.engine)

    def failures(self, code='timeout', count=3):
        for _ in range(count):
            self.assertTrue(self.monitor.record_failure(code))

    def settings(self, **values):
        for name, value in values.items():
            self.store.set_setting('screening_' + name, value)

    def text(self, notice):
        return ' '.join(notice['content'].split())

    def others(self):
        return {row['key']: row['value'] for row in self.store.db.execute(
            'SELECT * FROM settings WHERE key != ?', (SETTING,))}

    def test_three_failures_in_window_claim_once_with_safe_fixed_panel(self):
        self.failures(count=2)
        self.assertIsNone(self.monitor.sample(True))
        self.failures(count=1)
        before = self.store.get_setting(SETTING)
        preview = self.monitor.sample(True)
        self.assertEqual(self.store.get_setting(SETTING), before)
        notice = self.monitor.claim(True)
        self.assertEqual(notice['content'], preview['content'])
        self.assertEqual(notice['kind'], 'warning')
        self.assertEqual(notice['reasons'], ['repeated_screening_failures'])
        self.assertEqual(notice['channel_id'], '20')
        self.assertEqual(notice['revision'], 1)
        self.assertRegex(notice['token'], '^[a-f0-9]{32}$')
        self.assertRegex(notice['nonce'], '^[a-f0-9]{24}$')
        self.assertLess(len(notice['content']), 1900)
        self.assertTrue(notice['content'].startswith('## '))
        self.assertNotIn('```', notice['content'])
        self.assertIn('3 checks failed or were skipped within 5 minutes', self.text(notice))
        self.assertIsNone(self.monitor.claim(True))

    def test_window_pruning_and_real_success_reset_streak(self):
        self.failures(count=2)
        self.now += 301
        self.failures(count=1)
        self.assertIsNone(self.monitor.sample(True))
        self.monitor.record_success()
        self.failures(count=2)
        self.assertIsNone(self.monitor.sample(True))
        self.failures(count=1)
        self.assertIsNotNone(self.monitor.sample(True))
        self.now += 3600
        self.assertIsNotNone(self.monitor.sample(True))  # Latched until a valid live check.
        self.monitor.record_success()
        self.assertIsNone(self.monitor.sample(True))  # No warning was delivered yet.

    def test_key_access_failure_immediate_and_sanitized(self):
        for code in ('missing_key', 'authentication_failed', 'access_denied'):
            with self.subTest(code=code):
                self.store.set_setting(SETTING, None)
                monitor = HealthMonitor(self.engine)
                self.assertTrue(monitor.record_failure(code))
                notice = monitor.claim(True)
                self.assertEqual(notice['reasons'], ['jev_unavailable'])
                self.assertIn('key or access failure', self.text(notice))
                self.assertNotIn('3 checks', self.text(notice))

    def test_skipped_membership_input_queue_checks_are_grouped_not_api_errors(self):
        for code in ('membership_unavailable', 'input_limit', 'queue_full'):
            self.assertTrue(self.monitor.record_failure(code))
        notice = self.monitor.claim(True)
        self.assertEqual(notice['reasons'], ['repeated_screening_failures'])
        self.assertIn('failed or were skipped', self.text(notice))
        self.assertNotIn('network error', self.text(notice))
        self.assertFalse(self.monitor.record_failure('https://secret.invalid/token'))
        self.assertFalse(self.monitor.record_failure({'code': 'timeout'}))

    def test_sent_and_uncertain_attempts_never_auto_repeat_after_restart(self):
        for completion in (True, False, None):
            with self.subTest(completion=completion):
                self.store.set_setting(SETTING, None)
                monitor = HealthMonitor(self.engine)
                for _ in range(3):
                    monitor.record_failure('timeout')
                notice = monitor.claim(True)
                if completion is not None:
                    self.assertTrue(monitor.finish(notice['token'], completion))
                self.now += 3600
                restarted = HealthMonitor(self.engine)
                self.assertIsNone(restarted.claim(True))
                expected = 'sent' if completion is True else 'uncertain'
                self.assertEqual(self.store.get_setting(SETTING)['attempt']['status'], expected)
                self.assertFalse(restarted.finish(notice['token'], True))
                self.assertEqual(self.store.get_setting(SETTING)['attempt']['status'], expected)

    def test_finish_rejects_unknown_duplicate_and_malformed_completions(self):
        self.failures()
        notice = self.monitor.claim(True)
        self.assertFalse(self.monitor.finish('0' * 32, True))
        with self.assertRaises(ValueError):
            self.monitor.finish('a\nsecret', True)
        with self.assertRaises(ValueError):
            self.monitor.finish(notice['token'], 1)
        self.assertTrue(self.monitor.finish(notice['token'], False))
        self.assertFalse(self.monitor.finish(notice['token'], True))
        self.assertEqual(self.store.get_setting(SETTING)['attempt']['status'], 'uncertain')

    def test_global_cooldown_coalesces_new_failures_gaps_and_budget_changes(self):
        self.failures()
        first = self.monitor.claim(True)
        self.monitor.finish(first['token'], True)
        self.settings(daily_calls=100, total_calls=100)
        for reason in ('disconnect', 'queue_full', 'disconnect'):
            self.monitor.gap(reason)
        self.now += 299
        self.assertIsNone(self.monitor.claim(True))
        self.now += 1
        notice = self.monitor.claim(True)
        self.assertEqual(notice['reasons'], ['daily_calls', 'repeated_screening_failures'])
        self.assertEqual(notice['gaps'], {'disconnect': 2, 'queue_full': 1})
        self.assertEqual(notice['revision'], 2)
        self.assertNotEqual(notice['nonce'], first['nonce'])
        self.now += 500
        self.assertIsNone(self.monitor.claim(True))

    def test_recovery_requires_valid_live_success_and_no_current_blocker(self):
        self.failures()
        self.monitor.claim(True)
        self.now += 600
        self.assertIsNone(self.monitor.claim(True))
        self.settings(total_calls=1000)
        self.monitor.record_success()
        still_blocked = self.monitor.claim(True)
        self.assertEqual(still_blocked['reasons'], ['total_calls'])
        self.settings(total_calls=999)
        self.now += 600
        self.assertIsNone(self.monitor.claim(True))  # Need success after this warning too.
        self.monitor.record_success()
        recovered = self.monitor.claim(True)
        self.assertEqual(recovered['kind'], 'recovery')
        self.assertIn('valid live JEV check completed', self.text(recovered))
        self.assertIn('Missed checks are not backfilled', self.text(recovered))
        self.now += 600
        self.assertIsNone(self.monitor.claim(True))
        self.failures()
        self.assertEqual(self.monitor.claim(True)['kind'], 'warning')

    def test_disconnect_waits_for_reconnect_and_gap_only_has_no_fake_recovery(self):
        self.monitor.gap('disconnect')
        self.assertIsNone(self.monitor.claim(False))
        self.assertIsNone(self.monitor.claim(None))
        notice = self.monitor.claim(True)
        self.assertEqual(notice['kind'], 'interruption')
        self.assertEqual(notice['gaps'], {'disconnect': 1})
        self.assertIn('Gateway is connected now', self.text(notice))
        self.assertIn('Missed checks are not backfilled', self.text(notice))
        self.assertNotIn('recovered', notice['content'])
        self.now += 600
        self.monitor.record_success()
        self.assertIsNone(self.monitor.claim(True))

    def test_gap_and_success_combine_with_recovery_without_claiming_backfill(self):
        self.failures()
        self.monitor.claim(True)
        self.monitor.gap('disconnect')
        self.now += 600
        self.monitor.record_success()
        notice = self.monitor.claim(True)
        self.assertEqual(notice['kind'], 'recovery')
        self.assertEqual(notice['gaps'], {'disconnect': 1})
        self.assertIn('Missed checks are not backfilled', self.text(notice))

    def test_daily_and_total_call_caps_detected_without_new_message(self):
        self.settings(daily_calls=100, total_calls=1000)
        notice = self.monitor.claim(True)
        self.assertEqual(notice['reasons'], ['daily_calls', 'total_calls'])
        self.assertEqual(self.store.get_setting(SETTING)['failure_times'], [])

    def test_budget_reservation_boundary_and_spending_caps(self):
        daily = self.engine.config.classifier.daily_budget_microusd
        total = self.engine.config.classifier.total_budget_microusd
        self.settings(daily_reserved_microusd=daily-RESERVED_MICROUSD,
                      total_reserved_microusd=total-RESERVED_MICROUSD)
        self.assertIsNone(self.monitor.sample(True))
        self.settings(daily_reserved_microusd=daily-RESERVED_MICROUSD+1,
                      total_reserved_microusd=total-RESERVED_MICROUSD+1)
        self.assertEqual(self.monitor.claim(True)['reasons'], ['daily_budget', 'total_budget'])

    def test_daily_rollover_does_not_clear_lifetime_or_billing_blocker(self):
        self.settings(budget_day=int(self.now//86400), daily_calls=100, total_calls=1000,
                      billing_guard=True)
        first = self.monitor.claim(True)
        self.assertEqual(first['reasons'], ['billing_guard', 'daily_calls', 'total_calls'])
        before = self.others()
        self.now += 86400
        self.monitor.record_success()
        notice = self.monitor.claim(True)
        self.assertEqual(notice['reasons'], ['billing_guard', 'total_calls'])
        self.assertEqual(self.others(), before)  # Reporting never rolls live counters over.

    def test_daily_rollover_requires_live_success_before_recovery(self):
        self.settings(budget_day=int(self.now//86400), daily_calls=100, total_calls=100)
        self.monitor.claim(True)
        self.now += 86400
        self.assertIsNone(self.monitor.claim(True))
        self.monitor.record_success()
        self.assertEqual(self.monitor.claim(True)['kind'], 'recovery')

    def test_malformed_counters_fail_closed_without_mutation(self):
        for key, value in (('daily_calls', -1), ('total_calls', True),
                           ('daily_reserved_microusd', '7'), ('total_reserved_microusd', float('nan')),
                           ('budget_day', []), ('billing_guard', 1), ('schema_version', True),
                           ('daily_calls', 1), ('daily_reserved_microusd', 1)):
            with self.subTest(key=key, value=value):
                self.store.db.execute("DELETE FROM settings WHERE key LIKE 'screening_%'")
                self.store.set_setting(SETTING, None)
                self.settings(**{key: value})
                monitor = HealthMonitor(self.engine)
                before = self.others()
                notice = monitor.claim(True)
                self.assertIn('accounting_invalid', notice['reasons'])
                self.assertEqual(self.others(), before)

    def test_future_usage_day_is_blocker_not_false_budget_reset(self):
        self.settings(budget_day=int(self.now//86400)+1)
        self.assertEqual(self.monitor.claim(True)['reasons'], ['clock_rollback'])

    def test_incident_capacity_remains_blocking_after_benign_success(self):
        from liberdus_moderator.rules import Match
        self.engine.config = replace(self.engine.config,
            storage=replace(self.engine.config.storage, max_incidents=1))
        self.store.set_setting('policy_hash', self.engine.config.policy_hash)
        self.engine._record(Match('group', 'jev_message', '50', 'Synthetic concern', (), self.now+100), self.now)
        self.settings(state='incident_capacity')
        self.assertEqual(self.monitor.claim(True)['reasons'], ['incident_capacity'])
        self.now += 600
        self.settings(state='ok')
        self.monitor.record_success()
        self.assertIsNone(self.monitor.claim(True))  # Benign checks cannot prove saving a concern works.
        self.store.db.execute('DELETE FROM incidents')
        self.monitor.record_success()
        self.assertEqual(self.monitor.claim(True)['kind'], 'recovery')

    def test_storage_capacity_ignores_expired_completed_rows_but_keeps_running(self):
        self.engine.config = replace(self.engine.config,
            storage=replace(self.engine.config.storage, max_messages=1))
        self.store.set_setting('policy_hash', self.engine.config.policy_hash)
        self.store.db.execute('CREATE TABLE screening_attempts_v1(created_at REAL, outcome TEXT)')
        expired = self.now-self.engine.config.storage.retention_seconds-1
        self.store.db.execute('INSERT INTO screening_attempts_v1 VALUES(?,?)', (expired, 'ok'))
        self.assertIsNone(self.monitor.sample(True))
        self.store.db.execute("UPDATE screening_attempts_v1 SET outcome='running'")
        self.assertEqual(self.monitor.claim(True)['reasons'], ['storage_limit'])
        self.now += 600
        self.settings(state='ok')
        self.monitor.record_success()
        self.assertIsNone(self.monitor.claim(True))
        self.store.db.execute("UPDATE screening_attempts_v1 SET outcome='ok'")
        self.monitor.record_success()
        self.assertEqual(self.monitor.claim(True)['kind'], 'recovery')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM screening_attempts_v1').fetchone()[0], 1)

    def test_success_then_new_failure_does_not_report_recovery_until_next_success(self):
        self.failures()
        self.monitor.claim(True)
        self.now += 1
        self.monitor.record_success()
        self.now += 1
        self.monitor.record_failure('timeout')
        self.now += 600
        self.assertIsNone(self.monitor.claim(True))
        self.monitor.record_success()
        self.assertEqual(self.monitor.claim(True)['kind'], 'recovery')

    def test_pause_off_shadow_and_policy_change_suppress_deliberate_controls(self):
        baseline = self.engine.config
        for mode in ('paused', 'ai_off', 'classifier_off', 'shadow', 'policy_changed'):
            with self.subTest(mode=mode):
                self.engine.config = baseline
                self.store.set_setting('policy_hash', baseline.policy_hash)
                self.store.set_setting('paused', False)
                if mode == 'paused':
                    self.store.set_setting('paused', True)
                elif mode == 'ai_off':
                    self.engine.config = replace(baseline, ai_enabled=False,
                                                 classifier=ClassifierSettings())
                    self.store.set_setting('policy_hash', self.engine.config.policy_hash)
                elif mode in ('classifier_off', 'shadow'):
                    settings = replace(baseline.classifier, mode='off' if mode == 'classifier_off' else 'shadow')
                    self.engine.config = replace(baseline, classifier=settings, ai_enabled=mode != 'classifier_off')
                    self.store.set_setting('policy_hash', self.engine.config.policy_hash)
                else:
                    self.store.set_setting('policy_hash', 'different')
                before = self.store.get_setting(SETTING)
                self.assertFalse(self.monitor.record_failure('timeout'))
                self.assertFalse(self.monitor.record_success())
                self.assertFalse(self.monitor.gap('disconnect'))
                self.assertIsNone(self.monitor.claim(True))
                self.assertEqual(self.store.get_setting(SETTING), before)

    def test_noncoverage_events_and_unknown_codes_do_not_trigger_or_store_text(self):
        before = self.store.get_setting(SETTING)
        for code in ('deleted_message', 'reconnect', 'startup', 'pause', 'role_exempt', 'arbitrary secret'):
            self.assertFalse(self.monitor.gap(code))
            self.assertFalse(self.monitor.record_failure(code))
        self.assertEqual(self.store.get_setting(SETTING), before)
        self.assertIsNone(self.monitor.claim(True))

    def test_state_is_bounded_and_only_health_setting_changes(self):
        before = self.others()
        tables_before = list(self.store.db.execute("SELECT name FROM sqlite_master WHERE type='table'"))
        for index in range(1000):
            self.monitor.record_failure(tuple(sorted(FAILURE_CODES))[index % len(FAILURE_CODES)])
            self.monitor.gap(tuple(sorted(GAP_REASONS))[index % len(GAP_REASONS)])
        notice = self.monitor.claim(True)
        self.monitor.finish(notice['token'], False)
        state = self.store.get_setting(SETTING)
        self.assertLess(len(json.dumps(state)), 8192)
        self.assertLessEqual(len(state['failure_times']), FAILURE_THRESHOLD)
        self.assertEqual(self.others(), before)
        self.assertEqual(list(self.store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")), tables_before)
        self.assertEqual(self.store.incidents(), [])
        self.assertEqual(self.store.reports(), [])
        self.assertLess(len(notice['content']), 1900)

    def test_counter_saturation_and_malformed_durable_state_rejected(self):
        state = self.store.get_setting(SETTING)
        state['gaps']['disconnect'] = MAX_COUNT
        self.store.set_setting(SETTING, state)
        self.monitor.gap('disconnect')
        self.assertEqual(self.store.get_setting(SETTING)['gaps']['disconnect'], MAX_COUNT)
        for key, invalid in (('failure_code', []), ('auth_code', {}), ('failure_times', [1]*4),
                             ('failure_active', 'yes'), ('announced_reasons', ['arbitrary secret'])):
            with self.subTest(key=key):
                bad = {**state, key: invalid}
                self.store.set_setting(SETTING, bad)
                with self.assertRaisesRegex(ValueError, 'Invalid saved moderation health'):
                    HealthMonitor(self.engine)


if __name__ == '__main__':
    unittest.main()
