from copy import deepcopy
from dataclasses import replace
import json
import time
import unittest
from unittest.mock import AsyncMock, patch

from liberdus_moderator import actions
from liberdus_moderator.classifier import RESERVED_MICROUSD
from liberdus_moderator.commands import CommandRequest, handle_command
from liberdus_moderator.config import ClassifierSettings, Config
from liberdus_moderator.display import framed
from liberdus_moderator.engine import Engine
from liberdus_moderator.live import LiveSession
from liberdus_moderator.models import MessageEvent
from liberdus_moderator.screening import MessageScreener, validate_screening
from liberdus_moderator.staff_review import record_assessment
from liberdus_moderator.storage import Store
from liberdus_moderator.summary import summary_snapshot, format_summary
from test_screening import response


class SummaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 1000.
        settings = ClassifierSettings(mode='report_only', max_daily_calls=10000, max_total_calls=100000,
            daily_budget_microusd=1000000, total_budget_microusd=4000000, min_interval_seconds=1)
        self.config = Config('1', '99', ('10', '11', '12'), ('20',), ('98',), schema_version=2,
            ai_enabled=True, actions_enabled=True, classifier=settings)
        self.store = Store(':memory:')
        self.addCleanup(self.store.close)
        self.engine = Engine(self.config, self.store, clock=lambda: self.now)
        self.live = LiveSession(self.engine)
        self.provider = AsyncMock(return_value=response('none', 'other'))
        self.worker = MessageScreener(self.engine, self.provider)
        self.addAsyncCleanup(self.worker.close)

    def request(self, **changes):
        return replace(CommandRequest('1', '20', '98', 'summary'), **changes)

    def pattern(self):
        for index, channel in enumerate(('10', '11', '12')):
            self.engine.process(MessageEvent('1', channel, str(100+index), '50',
                'Repeated synthetic example for a retained incident.', self.now-3+index))
        return self.store.incidents()[0]['id']

    async def checked(self, identity='500'):
        event = MessageEvent('1', '10', identity, '51', 'Ordinary conversation ' + identity, self.now)
        self.engine.process(event)
        await self.worker.evaluate_one(self.worker.snapshot(identity))

    def database(self):
        return list(self.store.db.iterdump())

    def evaluation_table(self):
        self.store.db.execute('CREATE TABLE screening_eval_attempts_v1(result_json TEXT)')

    def test_summary_requires_existing_command_authorization_and_no_arguments(self):
        before = self.database()
        for changes in ({'guild_id': '2'}, {'channel_id': '10'}, {'user_id': '50'}):
            result = handle_command(self.engine, self.request(**changes))
            self.assertFalse(result['authorized'])
            self.assertIsNone(self.live.command(self.request(**changes), '800', True))
        self.assertEqual(before, self.database())
        result = handle_command(self.engine, self.request(arguments=('unexpected',)))
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'unknown_command_or_arguments')
        self.store.set_setting('policy_hash', 'changed-policy')
        self.assertFalse(handle_command(self.engine, self.request())['authorized'])
        self.provider.assert_not_awaited()

    def test_authorized_summary_is_read_only_and_suppresses_mentions(self):
        self.pattern()
        before = self.database()
        result = handle_command(self.engine, self.request())
        self.assertTrue(result['authorized'])
        self.assertTrue(result['ok'])
        self.assertEqual(result['ai_calls'], 0)
        self.assertEqual(result['public_actions'], [])
        self.assertEqual(result['allowed_mentions']['parse'], [])
        self.assertEqual(result['destination'], {'guild_id': '1', 'channel_id': '20'})
        self.assertEqual(before, self.database())
        self.assertEqual(result['data']['incidents']['retained'], 1)
        self.provider.assert_not_awaited()

    async def test_live_lifetime_events_are_separate_from_evaluation_attempts(self):
        await self.checked()
        self.store.set_setting('screening_unchecked', 7)
        self.store.set_setting('screening_exempt', 4)
        self.evaluation_table()
        result = validate_screening(response('sensitive_request', 'other', tokens=1000))
        self.store.db.execute('INSERT INTO screening_eval_attempts_v1 VALUES(?)', (json.dumps(result),))
        self.store.db.execute('INSERT INTO screening_eval_attempts_v1 VALUES(NULL)')
        self.store.set_setting('screening_total_calls', 30)  # Includes evaluation and pruned attempts.
        self.store.set_setting('screening_total_reserved_microusd', 9000)
        before = self.database()
        data = summary_snapshot(self.engine)
        self.assertEqual(data['live_lifetime_events'],
            {'checked_versions': 1, 'unchecked_events': 7, 'role_exempt_events': 4})
        self.assertEqual(data['live_retained']['attempts'], 1)
        self.assertEqual(data['live_retained']['known_microusd'], 21)
        self.assertEqual(data['evaluation_retained']['attempts'], 2)
        self.assertEqual(data['evaluation_retained']['known_microusd'], 42)
        self.assertEqual(data['evaluation_retained']['unknown_reserved_microusd'], RESERVED_MICROUSD)
        self.assertEqual(data['shared_screening_lifetime'], {'attempts': 30, 'used_reserved_microusd': 9000})
        self.assertEqual(before, self.database())
        self.provider.assert_awaited_once()  # Summary made no additional evaluation.
        rendered = format_summary(data)
        self.assertIn('Checked versions: 1', rendered)
        self.assertIn('Shared includes eval/pruned use.', rendered)
        self.assertIn('Legacy shadow/batch excluded.', rendered)

    def test_action_outcomes_and_staff_review_events_are_not_lifetime_deletions(self):
        identity = self.pattern()
        records = [('delete', 1, 'done'), ('delete', 0, 'done'), ('delete', 0, 'already_absent'),
            ('delete', 0, 'uncertain'), ('delete', 0, 'sending'), ('delete', 0, 'denied'), ('timeout', 0, 'done')]
        for index, (kind, automatic, outcome) in enumerate(records):
            self.store.db.execute('INSERT INTO action_attempts_v1 VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                ('key' + str(index), identity, 1, kind, str(900+index), 'auto' if automatic else '98',
                 automatic, self.now, self.now if outcome != 'sending' else None, outcome, ''))
        for label in ('needs-attention', 'looks-okay', 'needs-attention', 'unsure', 'dismissed'):
            record_assessment(self.engine, identity, '1', label, '98')
        data = summary_snapshot(self.engine)
        self.assertEqual(data['actions'], {'auto_delete_done': 1, 'staff_delete_done': 1,
            'delete_already_absent': 1, 'delete_uncertain': 1, 'delete_sending': 1, 'delete_denied': 1, 'timeout_done': 1})
        self.assertEqual(data['staff'], {'events': 5, 'needs_attention': 2, 'looks_okay': 1, 'unsure': 1, 'dismissed': 1})
        text = format_summary(data)
        self.assertIn('RETAINED HISTORY', text)
        self.assertIn('Includes old/repeated reviews.', text)
        self.assertNotIn('Lifetime deletions', text)
        self.store.db.execute('DELETE FROM incidents WHERE id=?', (identity,))
        after = summary_snapshot(self.engine)
        self.assertEqual(after['incidents']['retained'], 0)
        self.assertEqual(after['actions']['auto_delete_done'], 0)
        self.assertEqual(after['staff']['events'], 0)

    async def test_pruned_attempts_do_not_erase_lifetime_usage_or_event_counters(self):
        await self.checked()
        self.store.db.execute('DELETE FROM screening_attempts_v1')
        data = summary_snapshot(self.engine)
        self.assertEqual(data['live_retained']['attempts'], 0)
        self.assertEqual(data['live_retained']['known_microusd'], 0)
        self.assertEqual(data['live_lifetime_events']['checked_versions'], 1)
        self.assertEqual(data['shared_screening_lifetime']['attempts'], 1)
        self.assertEqual(data['shared_screening_lifetime']['used_reserved_microusd'], 21)

    def test_missing_optional_tables_are_not_created_and_partial_schemas_are_visible(self):
        self.store.db.execute('DROP TABLE screening_attempts_v1')
        self.store.db.execute('DROP TABLE action_attempts_v1')
        before = self.database()
        data = summary_snapshot(self.engine)
        self.assertEqual(data['live_retained']['attempts'], 0)
        self.assertEqual(data['evaluation_retained']['attempts'], 0)
        self.assertEqual(data['actions']['auto_delete_done'], 0)
        self.assertEqual(data['staff']['events'], 0)
        self.assertEqual(before, self.database())
        self.store.db.execute('CREATE TABLE staff_assessments_v1(invalid_column TEXT)')
        self.store.db.execute('CREATE TABLE screening_eval_attempts_v1(invalid_column TEXT)')
        data = summary_snapshot(self.engine)
        self.assertIsNone(data['staff']['events'])
        self.assertIsNone(data['evaluation_retained']['known_microusd'])
        self.assertIn('Eval known est: ?', format_summary(data))

    def test_malformed_or_oversized_results_keep_unknown_reservations_without_leaking_text(self):
        self.evaluation_table()
        for value in ('SENTINEL_SECRET <@977263877391794217>', '{bad-json', 'x'*9000, None):
            self.store.db.execute('INSERT INTO screening_eval_attempts_v1 VALUES(?)', (value,))
        self.store.set_setting('screening_unchecked', '<@977263877391794217> SENTINEL_SECRET')
        data = summary_snapshot(self.engine)
        self.assertEqual(data['evaluation_retained']['known_microusd'], 0)
        self.assertEqual(data['evaluation_retained']['unknown_reserved_microusd'], 4*RESERVED_MICROUSD)
        self.assertIsNone(data['live_lifetime_events']['unchecked_events'])
        text = format_summary(data)
        self.assertNotIn('SENTINEL_SECRET', text)
        self.assertNotIn('<@', text)
        self.assertIn('Unchecked events: ?', text)

    def test_known_stale_usage_and_bounded_cost_scan_are_labeled_correctly(self):
        self.evaluation_table()
        saved = json.dumps(validate_screening(response('none', 'other', tokens=1000)))
        for value in (saved, None, saved):
            self.store.db.execute('INSERT INTO screening_eval_attempts_v1 VALUES(?)', (value,))
        with patch('liberdus_moderator.summary.MAX_COST_ROWS', 2):
            data = summary_snapshot(self.engine)
        self.assertEqual(data['evaluation_retained']['attempts'], 3)
        self.assertEqual(data['evaluation_retained']['scanned'], 2)
        self.assertTrue(data['evaluation_retained']['partial'])
        self.assertEqual(data['evaluation_retained']['known_microusd'], 42)
        self.assertEqual(data['evaluation_retained']['unknown_reserved_microusd'], RESERVED_MICROUSD)
        self.assertIn('Eval known est: >=$0.000042', format_summary(data))
        self.assertIn('>=$0.002753', format_summary(data))

    def test_flags_follow_effective_policy_and_pause_does_not_change_counters(self):
        for feature in ('deletion', 'auto_delete', 'timeout'):
            self.store.set_setting(feature + '_enabled', True)
        self.store.set_setting('role_exemption_enabled', False)
        data = summary_snapshot(self.engine)
        state = self.engine.status()
        for key in ('deletion_enabled', 'auto_delete_enabled', 'timeout_enabled', 'role_exemption_enabled'):
            self.assertEqual(data['state'][key], state[key])
        self.assertIn('Connected: Yes', format_summary(data, True))
        self.assertIn('Connected: No', format_summary(data, False))
        self.engine.config = replace(self.config, actions_enabled=False)
        state = summary_snapshot(self.engine)['state']
        self.assertFalse(state['deletion_enabled'])
        self.assertFalse(state['auto_delete_enabled'])
        self.assertFalse(state['timeout_enabled'])
        self.engine.set_paused(True)
        self.assertEqual(summary_snapshot(self.engine)['state']['mode'], 'paused')

    def test_read_snapshot_preserves_callers_open_transaction(self):
        with self.store.transaction():
            self.store.set_setting('test_marker', 123)
            summary_snapshot(self.engine)
            self.assertTrue(self.store.db.in_transaction)
            self.assertEqual(self.store.get_setting('test_marker'), 123)
        self.assertEqual(self.store.get_setting('test_marker'), 123)

    def test_live_command_help_and_maximum_rendering_fit_discord(self):
        self.assertIn('!mod summary', self.live.command(self.request(command='help'), '700', True))
        text = self.live.command(self.request(), '701', True)
        self.assertIn('**Moderation summary**', text)
        self.assertIsNone(self.live.command(self.request(), '701', True))
        data = summary_snapshot(self.engine)
        for group in ('live_lifetime_events', 'incidents', 'actions', 'staff', 'shared_screening_lifetime'):
            for name in data[group]:
                data[group][name] = 2**63-1
        for group in ('live_retained', 'evaluation_retained'):
            for name in data[group]:
                data[group][name] = True if name == 'partial' else 2**63-1
        rendered = framed(format_summary(data, True))
        self.assertLessEqual(len(rendered.encode('utf-16-le'))//2, 1900)
        self.assertEqual(rendered.count('```'), 2)
        self.assertTrue(all(len(line) <= 32 for line in rendered.split('```')[1].splitlines()))
        self.assertIn('Read only; no AI call or action.', rendered)
        self.assertNotIn('Repeated synthetic example', rendered)


if __name__ == '__main__':
    unittest.main()
