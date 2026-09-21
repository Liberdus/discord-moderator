"""Live observer wiring and private delivery; provider/Discord network edges mocked."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

import test_hermes_adapter as adapter_tests
from test_message_screening import response
from liberdus_moderator.config import ClassifierSettings
from liberdus_moderator.engine import Engine
from liberdus_moderator.health import HealthMonitor, SETTING
from liberdus_moderator.live import LiveSession
from liberdus_moderator.models import MessageEvent
from liberdus_moderator.screening import MessageScreener


class HealthReportingTests(unittest.IsolatedAsyncioTestCase):
    message = adapter_tests.AdapterTests.message
    drain = adapter_tests.AdapterTests.drain
    asyncTearDown = adapter_tests.AdapterTests.asyncTearDown

    async def asyncSetUp(self):
        await adapter_tests.AdapterTests.asyncSetUp(self)
        self.now = 1000.
        self.adapter.policy = replace(self.adapter.policy, schema_version=2, ai_enabled=True,
            classifier=ClassifierSettings(mode='report_only', max_daily_calls=100, max_total_calls=1000,
                daily_budget_microusd=1000000, total_budget_microusd=4000000, min_interval_seconds=1))
        self.adapter.live = LiveSession(Engine(self.adapter.policy, self.adapter.store, clock=lambda:self.now))
        self.adapter.health = HealthMonitor(self.adapter.live.engine)
        self.provider = AsyncMock(return_value=response('none'))
        self.adapter.classifier = MessageScreener(self.adapter.live.engine, self.provider,
            on_health=self.adapter.health_outcome)

    async def check(self, identity):
        self.now += 2
        event = MessageEvent('1','10',str(identity),'50','Unique message '+str(identity),self.now,author_role_ids=('1',))
        self.adapter.live.engine.process(event)
        worker = self.adapter.classifier
        await worker.evaluate_one(worker.snapshot(str(identity)))

    async def fail_three(self):
        self.provider.side_effect = RuntimeError('SECRET PROVIDER BODY')
        for identity in range(801,804):
            await self.check(identity)

    async def test_real_screening_failure_hooks_alert_privately_and_dedupe(self):
        await self.fail_three()
        self.assertEqual(self.adapter.store.get_setting('screening_unchecked'),3)
        await self.adapter.flush_health()
        self.channel.send.assert_awaited_once()
        content = self.channel.send.call_args.args[0]
        self.assertIn('coverage alert',content)
        self.assertNotIn('SECRET',content)
        self.adapter.client.get_channel.assert_called_with(20)
        self.assertEqual(self.channel.send.call_args.kwargs['allowed_mentions'].to_dict()['parse'],[])
        self.assertTrue(self.channel.send.call_args.kwargs['suppress_embeds'])
        self.assertEqual(self.adapter.store.reports(),[])
        self.assertEqual(self.adapter.store.incidents(),[])
        self.now += 301
        await self.adapter.flush_health()
        self.channel.send.assert_awaited_once()
        self.provider.assert_awaited()
        self.assertEqual(self.provider.await_count,3)

    async def test_recovery_requires_real_applied_result_and_obeys_cooldown(self):
        await self.fail_three()
        await self.adapter.flush_health()
        self.provider.side_effect = None
        await self.check(804)
        await self.adapter.flush_health()
        self.channel.send.assert_awaited_once()
        self.now += 301
        await self.adapter.flush_health()
        self.assertEqual(self.channel.send.await_count,2)
        self.assertIn('screening recovered',self.channel.send.call_args.args[0])
        self.assertEqual(self.provider.await_count,4)

    async def test_stale_or_duplicate_result_does_not_claim_recovery(self):
        await self.fail_three()
        await self.adapter.flush_health()
        self.provider.side_effect = None
        self.now += 2
        event = MessageEvent('1','10','810','50','Ordinary new text.',self.now,author_role_ids=('1',))
        self.adapter.live.engine.process(event)
        worker = self.adapter.classifier
        worker.on_result = lambda job:None
        job = worker.snapshot('810')
        await worker.evaluate_one(job)
        before = self.adapter.store.get_setting(SETTING)['success_seq']
        self.adapter.coverage_gap('deleted_message')
        worker.apply(job)
        self.assertEqual(self.adapter.store.get_setting(SETTING)['success_seq'],before)
        self.now += 301
        await self.adapter.flush_health()
        self.channel.send.assert_awaited_once()
        worker.on_result = worker.apply
        await self.check(811)
        successful = self.adapter.store.get_setting(SETTING)['success_seq']
        await worker.evaluate_one(worker.snapshot('811'))
        self.assertEqual(self.adapter.store.get_setting(SETTING)['success_seq'],successful)

    async def test_budget_alert_does_not_need_new_message_or_api_call(self):
        store = self.adapter.store
        store.set_setting('screening_budget_day',0)
        store.set_setting('screening_daily_calls',100)
        store.set_setting('screening_total_calls',100)
        await self.adapter.flush_health()
        self.assertIn('Daily screening call limit',self.channel.send.call_args.args[0])
        self.provider.assert_not_awaited()
        self.assertEqual(store.get_setting('screening_total_calls'),100)

    async def test_outage_is_saved_until_reconnect_without_false_recovery(self):
        self.adapter.lost_connection()
        await self.adapter.flush_health()
        self.channel.send.assert_not_awaited()
        await self.adapter.ready()
        await self.adapter.flush_health()
        text = self.channel.send.call_args.args[0]
        self.assertIn('coverage interruption',text)
        self.assertIn('Discord disconnected',text)
        self.assertIn('not backfilled',' '.join(text.split()))
        self.assertNotIn('screening recovered',text)
        self.provider.assert_not_awaited()

    async def test_normal_delete_gap_and_pause_are_not_failure_alerts(self):
        self.adapter.coverage_gap('deleted_message')
        await self.adapter.flush_health()
        self.channel.send.assert_not_awaited()
        self.adapter.live.engine.set_paused(True)
        self.adapter.store.set_setting('screening_billing_guard',True)
        self.adapter.coverage_gap('disconnect')
        await self.adapter.flush_health()
        self.channel.send.assert_not_awaited()

    async def test_policy_and_private_permission_checks_precede_claim(self):
        await self.fail_three()
        self.adapter.policy_current.return_value = False
        await self.adapter.flush_health()
        self.channel.send.assert_not_awaited()
        self.assertIsNone(self.adapter.store.get_setting(SETTING)['attempt'])
        self.adapter.policy_current.return_value = True
        self.channel.permissions_for.return_value = None
        self.channel.permissions_for.side_effect = lambda member:SimpleNamespace(
            administrator=False,view_channel=True,read_message_history=True,send_messages=True)
        await self.adapter.flush_health()
        self.channel.send.assert_not_awaited()
        self.assertIsNone(self.adapter.store.get_setting(SETTING)['attempt'])

    async def test_uncertain_delivery_is_not_automatically_replayed_after_restart(self):
        await self.fail_three()
        self.channel.send.side_effect = TimeoutError()
        await self.adapter.flush_health()
        self.assertEqual(self.adapter.store.get_setting(SETTING)['attempt']['status'],'uncertain')
        self.adapter.health = HealthMonitor(self.adapter.live.engine)
        self.now += 301
        await self.adapter.flush_health()
        self.channel.send.assert_awaited_once()

    async def test_monitor_loop_runs_when_idle_and_shutdown_cancels_before_store_close(self):
        await self.fail_three()
        self.adapter.health_task = asyncio.create_task(self.adapter.run_health())
        await asyncio.wait_for(self._until_sent(),1)
        task = self.adapter.health_task
        store = self.adapter.store
        await self.adapter.disconnect()
        self.assertTrue(task.done())
        self.assertIsNone(self.adapter.health)
        self.assertIsNone(self.adapter.health_task)
        self.assertIsNone(self.adapter.store)

    async def _until_sent(self):
        while not self.channel.send.await_count:
            await asyncio.sleep(0)

    async def test_monitor_callback_failure_does_not_change_model_result(self):
        self.adapter.classifier.on_health = Mock(side_effect=RuntimeError('BROKEN OBSERVER'))
        await self.check(840)
        self.assertEqual(self.adapter.store.get_setting('screening_checked'),1)
        self.provider.assert_awaited_once()
        self.assertEqual(self.adapter.store.incidents(),[])

    async def test_repeated_shared_rate_limit_skips_alert_without_api_calls(self):
        self.adapter.store.set_setting('screening_last_attempt_at', self.now + 100)
        for identity in range(851, 854):
            await self.check(identity)
        self.assertEqual(self.adapter.store.get_setting('screening_unchecked'), 3)
        await self.adapter.flush_health()
        self.assertIn('local screening rate limit',
                      ' '.join(self.channel.send.call_args.args[0].split()))
        self.provider.assert_not_awaited()

    async def test_summary_command_uses_private_auth_without_model_calls(self):
        self.adapter.receive(self.message(900,20,98,'!mod summary'))
        await self.drain()
        self.assertIn('Moderation summary',self.channel.send.call_args.args[0])
        self.assertLessEqual(len(self.channel.send.call_args.args[0].encode('utf-16-le'))//2,2000)
        self.channel.send.reset_mock()
        self.adapter.receive(self.message(901,20,50,'!mod summary'))
        await self.drain()
        self.channel.send.assert_not_awaited()
        self.provider.assert_not_awaited()
