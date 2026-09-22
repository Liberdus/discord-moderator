from layout_helpers import visible_text
"""Actual restart recovery rows drive notices; no provider or Discord network I/O."""
import time
import unittest

import test_health_reporting as existing
from test_message_screening import response
from liberdus_moderator.health import SETTING
from liberdus_moderator.models import MessageEvent
from liberdus_moderator.screening import MessageScreener, validate_screening


class RestartLossHealthTests(unittest.IsolatedAsyncioTestCase):
    message = existing.HealthReportingTests.message
    drain = existing.HealthReportingTests.drain
    asyncSetUp = existing.HealthReportingTests.asyncSetUp
    asyncTearDown = existing.HealthReportingTests.asyncTearDown

    def reserve(self, identity):
        self.now += 2
        event = MessageEvent('1', '10', str(identity), '50',
            'Synthetic unfinished screening ' + str(identity), self.now, author_role_ids=('1',))
        self.adapter.live.engine.process(event)
        job = self.adapter.classifier.snapshot(str(identity))
        self.assertIsNotNone(job)
        self.assertTrue(self.adapter.classifier.reserve(job))
        return job

    def budget(self):
        return {key: self.adapter.store.get_setting('screening_' + key, 0) for key in (
            'budget_day', 'daily_calls', 'total_calls', 'daily_reserved_microusd',
            'total_reserved_microusd', 'last_attempt_at')}

    async def restart_worker(self):
        old = self.adapter.classifier
        await old.close()
        self.adapter.classifier = MessageScreener(self.adapter.live.engine, self.provider,
            on_health=self.adapter.health_outcome)

    async def test_running_and_awaiting_apply_loss_alert_once_without_budget_or_model_changes(self):
        first = self.reserve(901)
        second = self.reserve(902)
        self.adapter.classifier.finish(second, 'awaiting_apply', time.monotonic(),
            validate_screening(response('none')))
        store = self.adapter.store
        before_budget = self.budget()
        before_unchecked = store.get_setting('screening_unchecked', 0)
        before_checked = store.get_setting('screening_checked', 0)
        self.assertEqual({row[0] for row in store.db.execute(
            'SELECT outcome FROM screening_attempts_v1')}, {'running', 'awaiting_apply'})
        self.adapter.online = False
        await self.restart_worker()
        self.assertEqual(store.get_setting('screening_unchecked'), before_unchecked + 2)
        self.assertEqual(store.get_setting('screening_checked', 0), before_checked)
        self.assertEqual(self.budget(), before_budget)
        self.assertEqual([row[0] for row in store.db.execute(
            'SELECT outcome FROM screening_attempts_v1')], ['uncertain', 'uncertain'])
        self.assertEqual(store.get_setting(SETTING)['gaps'].get('restart_lost'), 1)
        await self.adapter.flush_health()
        self.channel.send.assert_not_awaited()
        self.adapter.online = True
        await self.adapter.flush_health()
        self.channel.send.assert_awaited_once()
        text = visible_text(self.channel.send.call_args)
        self.assertIn('coverage interruption', text)
        self.assertIn('restart', text.lower())
        self.assertNotIn('screening recovered', text)
        self.assertEqual(self.channel.send.call_args.kwargs['allowed_mentions'].to_dict()['parse'], [])
        self.assertEqual(store.incidents(), [])
        self.assertEqual(store.reports(), [])
        self.assertEqual(store.db.execute('SELECT count(*) FROM action_attempts_v1').fetchone()[0], 0)
        self.provider.assert_not_awaited()
        await self.restart_worker()
        self.now += 301
        await self.adapter.flush_health()
        self.channel.send.assert_awaited_once()
        self.assertEqual(self.budget(), before_budget)
        self.assertEqual(store.get_setting('screening_unchecked'), before_unchecked + 2)

    async def test_empty_or_already_finalized_restart_has_no_interruption_notice(self):
        await self.restart_worker()
        await self.adapter.flush_health()
        self.channel.send.assert_not_awaited()
        self.assertNotIn('restart_lost', self.adapter.store.get_setting(SETTING)['gaps'])
        job = self.reserve(910)
        self.adapter.classifier.finish(job, 'uncertain', time.monotonic())
        before_budget = self.budget()
        before_unchecked = self.adapter.store.get_setting('screening_unchecked')
        await self.restart_worker()
        await self.adapter.flush_health()
        self.channel.send.assert_not_awaited()
        self.provider.assert_not_awaited()
        self.assertEqual(self.budget(), before_budget)
        self.assertEqual(self.adapter.store.get_setting('screening_unchecked'), before_unchecked)
        self.assertNotIn('restart_lost', self.adapter.store.get_setting(SETTING)['gaps'])


if __name__ == '__main__':
    unittest.main()
