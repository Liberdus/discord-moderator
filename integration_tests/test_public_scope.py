"""Public-channel scope boundaries and category movement; all I/O mocked."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord
import test_hermes_adapter as adapter_tests
import test_message_screening as screening_tests
from liberdus_moderator.config import ClassifierSettings
from liberdus_moderator.engine import Engine
from liberdus_moderator.hermes_adapter import PilotClient, snapshot
from liberdus_moderator.live import LiveSession
from liberdus_moderator.screening import MessageScreener


class PublicScopeTests(unittest.IsolatedAsyncioTestCase):
    message = adapter_tests.AdapterTests.message
    drain = adapter_tests.AdapterTests.drain
    asyncTearDown = adapter_tests.AdapterTests.asyncTearDown

    async def asyncSetUp(self):
        await adapter_tests.AdapterTests.asyncSetUp(self)
        self.adapter.policy = replace(self.adapter.policy, allow_public_monitored_channels=True,
                                      excluded_category_ids=('200',))
        self.adapter.live = LiveSession(Engine(self.adapter.policy, self.adapter.store))
        self.guild = SimpleNamespace(id=1, me=object(), default_role=object())
        self.categories = {}
        for identity in (100, 200, 300):
            category = Mock(spec=discord.CategoryChannel)
            category.id, category.guild = identity, self.guild
            self.categories[identity] = category
        self.channels = {}
        for identity in (10, 11, 12, 20):
            channel = Mock(spec=discord.TextChannel)
            channel.id, channel.guild, channel.type = identity, self.guild, discord.ChannelType.text
            channel.category_id, channel.category = 100, self.categories[100]
            channel.public = identity != 20
            channel.permissions_for.side_effect = lambda member, c=channel: SimpleNamespace(
                administrator=False, view_channel=member is self.guild.me or c.public,
                read_message_history=True, send_messages=True)
            channel.send = AsyncMock(return_value=SimpleNamespace(id=700))
            channel.fetch_message = AsyncMock()
            self.channels[identity] = channel
        self.channel = self.channels[20]
        self.adapter.client.get_channel.side_effect = self.channels.get
        self.adapter.refresh_scope()

    def move(self, identity, category_id, *, notify=True):
        channel = self.channels[identity]
        channel.category_id = category_id
        channel.category = self.categories.get(category_id)
        if notify:
            self.adapter.channel_changed(channel)

    async def enable_screening(self):
        self.adapter.policy = replace(self.adapter.policy, schema_version=2, ai_enabled=True,
            classifier=ClassifierSettings(mode='report_only', max_daily_calls=10000, max_total_calls=100000,
                daily_budget_microusd=1000000, total_budget_microusd=4000000, min_interval_seconds=1))
        self.adapter.live = LiveSession(Engine(self.adapter.policy, self.adapter.store))
        self.provider = AsyncMock(return_value=screening_tests.response())
        self.adapter.classifier = MessageScreener(self.adapter.live.engine, self.provider,
            active=self.adapter.classifier_active, on_result=lambda job: self.adapter.enqueue('screen_result', job))
        self.adapter.classifier.start()

    async def screened(self):
        await self.drain()
        await asyncio.wait_for(self.adapter.classifier.queue.join(), 5)
        await self.drain()

    async def test_public_messages_are_monitored_but_all_reports_stay_private(self):
        for index, identity in enumerate((10, 11, 12)):
            self.adapter.receive(self.message(100+index, identity))
        await self.drain()
        self.channel.send.assert_awaited_once()
        for identity in (10, 11, 12):
            self.channels[identity].send.assert_not_awaited()
        self.assertEqual(len(self.adapter.store.incidents()), 1)

    async def test_legacy_flag_off_still_requires_private_monitors(self):
        self.adapter.policy = replace(self.adapter.policy, allow_public_monitored_channels=False,
                                      excluded_category_ids=())
        with self.assertRaisesRegex(ValueError, 'permissions'):
            self.adapter.checked_channel('10')
        self.assertIs(self.adapter.checked_channel('20', sending=True), self.channel)

    async def test_public_scope_does_not_enable_commands_outside_private_staff_channel(self):
        self.adapter.receive(self.message(100, 10, 98, '!mod pause'))
        await self.drain()
        self.assertFalse(self.adapter.store.get_setting('paused'))
        self.channel.send.assert_not_awaited()
        self.adapter.receive(self.message(101, 20, 98, '!mod pause'))
        await self.drain()
        self.assertTrue(self.adapter.store.get_setting('paused'))
        self.channel.send.assert_awaited_once()

    async def test_staff_channel_cannot_be_public_or_in_excluded_category(self):
        for mode in ('public', 'excluded'):
            with self.subTest(mode=mode):
                self.channel.public = mode == 'public'
                self.move(20, 200 if mode == 'excluded' else 100)
                with self.assertRaises(ValueError):
                    self.adapter.checked_channel('20', sending=True)
                self.adapter.receive(self.message(101, 20, 98, '!mod pause'))
                await self.drain()
                self.assertFalse(self.adapter.store.get_setting('paused'))
                self.channel.send.assert_not_awaited()
                with self.assertRaises(ValueError):
                    await self.adapter.emit('20', 'private data', 'nonce')

    async def test_unknown_category_metadata_fails_closed_per_message(self):
        channel = self.channels[10]
        for metadata in ('missing', 'invalid', 'uncached', 'wrongguild', 'wrongid'):
            with self.subTest(metadata=metadata):
                channel.category_id, channel.category = 100, self.categories[100]
                if metadata == 'missing':
                    del channel.category_id
                elif metadata == 'invalid':
                    channel.category_id = True
                elif metadata == 'uncached':
                    channel.category = None
                elif metadata == 'wrongguild':
                    channel.category = Mock(spec=discord.CategoryChannel, id=100, guild=SimpleNamespace(id=2))
                else:
                    channel.category = self.categories[300]
                self.adapter.receive(self.message())
                self.assertFalse(self.adapter.in_scope(1, 10))
                self.assertTrue(self.adapter.in_scope(1, 11))
        await self.drain()
        self.assertFalse(self.adapter.store.db.execute('SELECT 1 FROM messages').fetchone())
        self.assertTrue(self.adapter.online)
        self.adapter._set_fatal_error.assert_not_called()

    async def test_known_uncategorized_channel_allowed_but_announcement_and_new_channel_not_added(self):
        self.move(10, None)
        self.assertTrue(self.adapter.in_scope(1, 10))
        self.channels[11].type = discord.ChannelType.news
        self.adapter.channel_changed(self.channels[11])
        self.assertFalse(self.adapter.in_scope(1, 11))
        self.channels[99] = self.channels[12]
        self.adapter.channel_changed(self.channels[99])
        self.adapter.receive(self.message(100, 99))
        await self.drain()
        self.assertFalse(self.adapter.in_scope(1, 99))
        self.assertEqual(self.adapter.policy.monitored_channel_ids, ('10', '11', '12'))

    async def test_move_excluded_invalidates_queued_messages_and_does_not_stop_other_channels(self):
        self.adapter.receive(self.message(100, 10))
        prior = self.adapter.generation
        self.move(10, 200)
        self.assertGreater(self.adapter.generation, prior)
        await self.drain()
        self.assertFalse(self.adapter.store.db.execute('SELECT 1 FROM messages').fetchone())
        self.adapter.receive(self.message(101, 10))
        self.adapter.receive(self.message(102, 11))
        await self.drain()
        self.assertEqual([row[0] for row in self.adapter.store.db.execute('SELECT channel_id FROM messages')], ['11'])
        self.assertTrue(self.adapter.online)
        self.adapter._set_fatal_error.assert_not_called()
        self.assertEqual(self.adapter.store.get_setting('last_coverage_gap')['reason'], 'scope_changed')

    async def test_worker_detects_move_before_channel_callback_and_rejects_old_queue(self):
        self.adapter.receive(self.message(100, 10))
        self.move(10, 200, notify=False)
        await self.drain()
        self.assertFalse(self.adapter.store.db.execute('SELECT 1 FROM messages').fetchone())
        self.assertTrue(self.adapter.online)

    async def test_excluded_edits_and_deletes_do_not_fetch_or_reset_valid_evidence(self):
        self.move(10, 200)
        self.adapter.receive(self.message(101, 11))
        await self.drain()
        prior = self.adapter.generation
        self.adapter.edit(1, 10, 100)
        self.adapter.deleted(1, 10, (100,))
        await self.drain()
        self.assertEqual(self.adapter.generation, prior)
        self.channels[10].fetch_message.assert_not_awaited()
        self.assertTrue(self.adapter.store.db.execute('SELECT 1 FROM messages').fetchone())

    async def test_move_during_edit_fetch_prevents_reinsert_after_await(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def fetch(identity):
            entered.set()
            await release.wait()
            return self.message(identity, 10, content='Changed message content.', edited_at=datetime.now(timezone.utc))
        self.channels[10].fetch_message.side_effect = fetch
        self.adapter.edit(1, 10, 100)
        await asyncio.wait_for(entered.wait(), 1)
        self.move(10, 200, notify=False)
        release.set()
        await self.drain()
        self.assertFalse(self.adapter.store.db.execute('SELECT 1 FROM messages').fetchone())
        self.assertTrue(self.adapter.online)

    async def test_move_during_jev_request_discards_result_and_preserves_other_channels(self):
        await self.enable_screening()
        entered, release = asyncio.Event(), asyncio.Event()
        async def provider(payload):
            entered.set()
            await release.wait()
            return screening_tests.response()
        self.adapter.classifier.evaluator = provider
        self.adapter.receive(self.message())
        await self.drain()
        await asyncio.wait_for(entered.wait(), 1)
        self.move(10, 200)
        release.set()
        await self.screened()
        self.assertFalse(self.adapter.store.incidents())
        self.channel.send.assert_not_awaited()
        self.assertEqual(self.adapter.store.db.execute('SELECT outcome FROM screening_attempts_v1').fetchone()[0], 'stale')
        self.assertTrue(self.adapter.classifier_active())
        self.assertTrue(self.adapter.in_scope(1, 11))

    async def test_cached_move_blocks_jev_before_callback_without_provider_call(self):
        await self.enable_screening()
        self.adapter.process_evidence(snapshot(self.message()))
        job = self.adapter.classifier.snapshot('100')
        self.assertIsNotNone(job)
        self.move(10, 200, notify=False)
        self.assertFalse(self.adapter.classifier_active())
        await self.adapter.classifier.evaluate_one(job)
        self.provider.assert_not_awaited()
        with patch('liberdus_moderator.jev.evaluate', new_callable=AsyncMock) as outbound:
            with self.assertRaises(ValueError):
                await self.adapter.evaluate_jev(b'{}')
            outbound.assert_not_awaited()

    async def test_pending_report_from_moved_channel_is_cancelled_before_delivery(self):
        for index, identity in enumerate((10, 11, 12)):
            self.adapter.process_evidence(snapshot(self.message(100+index, identity)))
        self.assertTrue(self.adapter.store.reports())
        self.move(10, 200, notify=False)
        await self.adapter.flush_reports()
        self.channel.send.assert_not_awaited()
        self.assertFalse(self.adapter.store.reports())
        self.assertEqual(self.adapter.store.incidents()[0]['status'], 'needs_revalidation')

    async def test_public_channel_becoming_private_is_excluded_without_category_change(self):
        self.adapter.receive(self.message())
        before = self.adapter.generation
        self.channels[10].public = False
        self.adapter.channel_changed(self.channels[10])
        await self.drain()
        self.assertGreater(self.adapter.generation, before)
        self.assertFalse(self.adapter.in_scope(1, 10))
        self.assertTrue(self.adapter.in_scope(1, 11))
        self.assertFalse(self.adapter.store.db.execute('SELECT 1 FROM messages').fetchone())
        self.adapter.receive(self.message(101, 10))
        await self.drain()
        self.assertFalse(self.adapter.store.db.execute('SELECT 1 FROM messages').fetchone())
        self.assertTrue(self.adapter.online)

    async def test_invalid_private_destination_blocks_startup_and_new_jev_calls(self):
        await self.enable_screening()
        self.adapter.process_evidence(snapshot(self.message()))
        job = self.adapter.classifier.snapshot('100')
        self.assertIsNotNone(job)
        self.channel.public = True
        self.adapter.refresh_scope()
        self.assertFalse(self.adapter.classifier_active())
        await self.adapter.classifier.evaluate_one(job)
        self.provider.assert_not_awaited()
        self.adapter.fail = AsyncMock()
        await self.adapter.ready()
        self.adapter.fail.assert_awaited_once_with('pilot_readiness_failed')

    async def test_unavailable_guild_cache_cannot_authorize_collection(self):
        self.guild.unavailable = True
        self.adapter.receive(self.message())
        await self.drain()
        self.assertFalse(self.adapter.in_scope(1, 10))
        self.assertFalse(self.adapter.classifier_active())
        self.assertFalse(self.adapter.store.db.execute('SELECT 1 FROM messages').fetchone())

    async def test_public_policy_keeps_actions_disabled_even_with_old_persisted_on_flags(self):
        from liberdus_moderator import actions
        await self.enable_screening()
        for name in ('deletion', 'auto_delete', 'timeout'):
            self.adapter.store.set_setting(name+'_enabled', True)
            self.assertFalse(actions.enabled(self.adapter.live.engine, name))
        self.adapter.receive(self.message())
        await self.screened()
        self.provider.assert_awaited_once()
        self.assertEqual(len(self.adapter.store.incidents()), 1)
        self.assertFalse(self.adapter.auto_delete_candidates)
        controls = self.channel.send.call_args.kwargs['view'].children
        self.assertTrue(next(item for item in controls if item.label == 'Delete message(s)').disabled)
        self.assertTrue(next(item for item in controls if item.label == 'Timeout 10 min').disabled)

    async def test_existing_saved_incident_remains_visible_privately_after_scope_reset(self):
        for index, identity in enumerate((10, 11, 12)):
            self.adapter.process_evidence(snapshot(self.message(100+index, identity)))
        incident = self.adapter.store.incidents()[0]
        self.move(10, 200)
        self.adapter.receive(self.message(400, 20, 98, '!mod incident '+incident['id']))
        await self.drain()
        self.channel.send.assert_awaited_once()
        self.assertIn(incident['id'], self.channel.send.call_args.args[0])
        self.assertFalse(self.adapter.store.db.execute('SELECT 1 FROM messages WHERE eligible=1').fetchone())

    async def test_ready_and_channel_callbacks_ignore_excluded_channel_without_fatal_shutdown(self):
        self.move(10, 200)
        await self.adapter.ready()
        self.assertTrue(self.adapter.online)
        self.adapter._set_fatal_error.assert_not_called()
        for name in ('on_guild_channel_update', 'on_guild_channel_delete', 'on_guild_channel_create'):
            client = SimpleNamespace(adapter=self.adapter)
            method = getattr(PilotClient, name)
            args = (self.channels[10], self.channels[10]) if name.endswith('update') else (self.channels[10],)
            await method(client, *args)
        self.assertTrue(self.adapter.online)
        self.assertFalse(self.adapter.in_scope(1, 10))
        self.assertTrue(self.adapter.in_scope(1, 11))


if __name__ == '__main__':
    unittest.main()
