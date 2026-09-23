import asyncio
from datetime import datetime, timezone
from dataclasses import replace
from types import SimpleNamespace
import time
import unittest
from unittest.mock import AsyncMock, patch

import discord
import test_runtime as runtime
from liberdus_moderator import actions, recovery
from liberdus_moderator.catchup_transport import Catchup
from liberdus_moderator.classifier import RUBRIC
from liberdus_moderator.config import ClassifierSettings
from liberdus_moderator.configure_jev import policy_text
from liberdus_moderator.instance import load_policy
from liberdus_moderator.secure_files import write_private
from liberdus_moderator.screening import CONCERN
from liberdus_moderator.standalone import StandaloneService


def result():
    def answer(choice, rubric):
        return dict(type='choice', choice=choice, confidence=.99,
                    probabilities={key: float(key == choice) for key in rubric['criteria']})
    return dict(model='jev-1.13.0', answers=dict(context=answer('promotion', RUBRIC),
                concern=answer('suspicious_offer', CONCERN)), usage=dict(input_tokens=500, output_tokens=50))


class CatchupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await runtime.RuntimeTests.asyncSetUp(self)
        policy = replace(load_policy(self.home), ai_enabled=True, actions_enabled=True, allow_public_deletion=True,
                         classifier=ClassifierSettings(mode='report_only', min_interval_seconds=1,
                             max_daily_calls=1000, max_total_calls=10000, daily_budget_microusd=1000000,
                             total_budget_microusd=4000000))
        write_private(self.home / 'moderation.toml', policy_text(policy), replace=True)
        from liberdus_moderator.instance import credential_provider
        credential_provider(self.home).put('jev', 'fake-catchup-key')
        self.provider = AsyncMock(return_value=result())
        self.service.evaluate_jev = self.provider
        self.assertTrue(await self.service.connect())
        self.now = time.time()
        self.service.live.engine.clock = lambda: self.now
        self.manager = self.service.catchup
        self.channel = self.service.client.channels[10]
        self.messages = []
        self.history_calls = []
        self.attach_history(self.channel)
        self.service.store.set_setting('auto_delete_enabled', True)
        self.service.store.set_setting('deletion_enabled', True)

    def attach_history(self, channel):
        async def history(**options):
            self.history_calls.append(options)
            for message in self.messages:
                if options['after'].id < message.id < options['before'].id:
                    yield message
        channel.history = history
        channel.fetch_message = AsyncMock(side_effect=lambda identity: next(m for m in self.messages if m.id == identity))

    async def asyncTearDown(self):
        await self.service.disconnect()

    def message(self, offset=1):
        stamp = self.now + offset
        identity = discord.utils.time_snowflake(datetime.fromtimestamp(stamp, timezone.utc)) + 1
        return SimpleNamespace(id=identity, guild=self.channel.guild, channel=self.channel,
            author=SimpleNamespace(id=50, bot=False), webhook_id=None, attachments=[], edited_at=None,
            content='Synthetic recovered offer.', created_at=datetime.fromtimestamp(stamp, timezone.utc))

    async def wait_complete(self):
        for _ in range(80):
            if self.manager.data['status'] in {'complete', 'incomplete'} and all(r['end'] is None for r in self.manager.data['channels'].values()):
                await asyncio.wait_for(self.service.queue.join(), 3)
                return
            await asyncio.sleep(.1)
        self.fail('Catch-up did not finish')

    async def recover(self):
        self.service.lost_connection()
        self.messages.append(self.message())
        self.now += 2
        await self.service.ready(resumed=True)
        await self.wait_complete()

    async def test_disconnect_post_reconnect_screens_once_and_flags_for_staff_only(self):
        await self.recover()
        self.provider.assert_awaited_once()
        incident = self.service.store.incident(self.service.store.incidents()[0]['id'])
        self.assertTrue(recovery.recovered_incident(self.service.live.engine, incident['id']))
        self.assertFalse(actions.automatic_candidate(self.service.live.engine, incident))
        self.assertEqual(self.service.store.db.execute('SELECT count(*) FROM action_attempts_v1').fetchone()[0], 0)
        call = self.service.client.channels[20].send.call_args
        self.assertIn('Recovered after disconnect', str(call.kwargs['view'].to_components()))
        self.assertEqual(self.manager.data['checked'], 1)
        self.service.lost_connection()
        self.now += .2
        await self.service.ready(resumed=True)
        await self.wait_complete()
        self.provider.assert_awaited_once()
        self.assertEqual(self.manager.data['skipped'], 1)

    async def test_first_start_clean_stop_and_pause_do_not_scan_history(self):
        await asyncio.sleep(.05)
        self.assertEqual(self.history_calls, [])
        await self.manager.close(clean=True)
        fresh = Catchup(self.service)
        self.assertFalse(fresh.unclean)
        self.service.catchup = self.manager = fresh
        fresh.ready()
        self.service.live.engine.set_paused(True)
        self.service.lost_connection()
        self.messages.append(self.message())
        self.now += 2
        await self.service.ready(resumed=True)
        await asyncio.sleep(.05)
        self.assertEqual(self.history_calls, [])
        self.provider.assert_not_awaited()
        self.service.live.engine.set_paused(False)
        await asyncio.sleep(1.1)
        self.assertEqual(self.history_calls, [])

    async def test_unexpected_restart_recovers_from_saved_checkpoint(self):
        self.service.lost_connection()
        # Simulate the persisted checkpoint left by a process failure, then open
        # a new service, database connection and screening worker.
        self.manager.data['channels']['10']['cursor'] = self.now - 3
        self.manager.save()
        self.messages.append(self.message(-1))
        self.service.unexpected_shutdown = True
        await self.service.disconnect()
        self.service = StandaloneService(self.home)
        def client():
            value = runtime.fake_client(self.service)
            self.channel = value.channels[10]
            for message in self.messages:
                message.channel, message.guild = self.channel, self.channel.guild
            self.attach_history(self.channel)
            return value
        self.service.make_client = client
        self.service.evaluate_jev = self.provider
        self.assertTrue(await self.service.connect())
        self.manager = self.service.catchup
        await self.wait_complete()
        self.provider.assert_awaited_once()

    async def test_current_exempt_membership_skips_recovered_text(self):
        await self.service.disconnect()
        policy = load_policy(self.home)
        policy = replace(policy, classifier=replace(policy.classifier, exempt_role_ids=('55',)))
        write_private(self.home / 'moderation.toml', policy_text(policy), replace=True)
        self.service = StandaloneService(self.home)
        self.service.make_client = lambda: runtime.fake_client(self.service)
        self.service.evaluate_jev = self.provider
        self.assertTrue(await self.service.connect())
        self.manager = self.service.catchup
        self.channel = self.service.client.channels[10]
        self.attach_history(self.channel)
        self.now = time.time()
        self.service.live.engine.clock = lambda: self.now
        self.channel.guild.fetch_member = AsyncMock(return_value=SimpleNamespace(
            id=50, guild=self.channel.guild, bot=False, _roles=[55]))
        await self.recover()
        self.channel.guild.fetch_member.assert_awaited_once_with(50)
        self.provider.assert_not_awaited()
        self.assertEqual(self.manager.data['status'], 'complete')
        self.assertEqual(self.manager.data['skipped'], 1)

    async def test_second_disconnect_retains_progress_and_does_not_repeat_uncertain_ai(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def provider(payload):
            entered.set()
            await release.wait()
            return result()
        self.provider.side_effect = provider
        self.service.lost_connection()
        self.messages.append(self.message())
        self.now += 2
        await self.service.ready(resumed=True)
        await asyncio.wait_for(entered.wait(), 4)
        self.service.lost_connection()
        self.now += .2
        release.set()
        await self.service.classifier.queue.join()
        await self.service.ready(resumed=True)
        await self.wait_complete()
        self.provider.assert_awaited_once()
        self.assertEqual(self.manager.data['status'], 'incomplete')

    async def test_permission_failure_and_long_gap_warn_without_provider_call(self):
        self.service.lost_connection()
        self.now += 400
        self.channel.permissions_for.side_effect = lambda member: SimpleNamespace(
            administrator=False, view_channel=False, read_message_history=False, send_messages=False)
        await self.service.ready(resumed=True)
        await self.wait_complete()
        self.assertEqual(self.manager.data['status'], 'incomplete')
        self.assertTrue(self.manager.data['notified'])
        self.provider.assert_not_awaited()

    async def test_per_channel_cap_remains_visible_after_brief_resume(self):
        self.service.lost_connection()
        self.messages.extend(self.message(.1 + index * .001) for index in range(101))
        for message in self.messages:
            message.author.bot = True
        self.now += 2
        await self.service.ready(resumed=True)
        await self.wait_complete()
        self.assertEqual(self.manager.data['used'], 100)
        self.assertEqual(self.manager.data['status'], 'incomplete')
        self.assertEqual(self.history_calls[0]['limit'], 101)
        self.provider.assert_not_awaited()
        await self.service.flush_health()
        card = self.service.client.channels[20].send.call_args.kwargs['view'].to_components()
        self.assertIn('Message catch-up incomplete', str(card))

    async def test_budget_exhaustion_does_not_look_like_complete_recovery(self):
        self.service.store.set_setting('screening_total_calls', 10000)
        await self.recover()
        self.assertEqual(self.manager.data['status'], 'incomplete')
        self.provider.assert_not_awaited()

    async def test_full_recovery_storage_records_gap_without_refetch_loop(self):
        with patch('liberdus_moderator.recovery.stage', return_value=False):
            await self.recover()
        self.assertEqual(self.manager.data['status'], 'incomplete')
        self.assertEqual(self.manager.data['used'], 1)
        self.assertEqual(len(self.history_calls), 1)
        self.provider.assert_not_awaited()

    async def test_live_messages_continue_while_history_request_is_waiting(self):
        self.service.store.set_setting('auto_delete_enabled', False)
        entered, release = asyncio.Event(), asyncio.Event()
        async def history(**options):
            entered.set()
            await release.wait()
            if False:
                yield None
        self.channel.history = history
        self.service.lost_connection()
        self.now += 1
        await self.service.ready(resumed=True)
        await asyncio.wait_for(entered.wait(), 4)
        live = self.message(.1)
        self.now += .2
        self.service.receive(live)
        await asyncio.wait_for(self.service.queue.join(), 3)
        await asyncio.wait_for(self.service.classifier.queue.join(), 3)
        await asyncio.wait_for(self.service.queue.join(), 3)
        self.provider.assert_awaited_once()
        incident = self.service.store.incidents()[0]
        self.assertFalse(recovery.recovered_incident(self.service.live.engine, incident['id']))
        release.set()
        await self.wait_complete()

    async def test_bound_reached_is_reported_incomplete(self):
        self.service.lost_connection()
        self.messages.append(self.message())
        self.now += 2
        await self.service.ready(resumed=True)
        self.manager.data['used'] = recovery.TOTAL
        await self.wait_complete()
        self.assertEqual(self.manager.data['status'], 'incomplete')
        self.provider.assert_not_awaited()
