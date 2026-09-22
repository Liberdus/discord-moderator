"""Exercise real SDK serialization for new and converted review messages."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

import discord
import test_hermes_adapter as adapter_tests
from layout_helpers import buttons, visible_text
from liberdus_moderator.commands import CommandRequest
from liberdus_moderator.hermes_adapter import assessment_buttons
from liberdus_moderator.review_display import ReviewMessage


class ReviewLayoutTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = adapter_tests.AdapterTests.asyncSetUp
    asyncTearDown = adapter_tests.AdapterTests.asyncTearDown
    message = adapter_tests.AdapterTests.message
    drain = adapter_tests.AdapterTests.drain
    create_report = adapter_tests.AdapterTests.create_report
    interaction = adapter_tests.AdapterTests.interaction

    def sdk_transport(self):
        state = Mock()
        state.allowed_mentions = discord.AllowedMentions.none()
        state._get_guild.return_value = self.channel.guild
        state.store_user.side_effect = lambda data, **kwargs: discord.User(state=state, data=data)
        data = dict(id='700', channel_id='20', type=0, content='Old legacy report', flags=0,
                    attachments=[], embeds=[], edited_timestamp=None, tts=False, pinned=False,
                    mention_everyone=False, mentions=[], mention_roles=[],
                    author=dict(id='99', username='testbot', discriminator='0', avatar=None, bot=True))
        self.channel.id = 20
        self.channel._state = state
        self.channel._get_channel = AsyncMock(return_value=self.channel)
        state.create_message.side_effect = lambda channel, data: discord.Message(state=state, channel=channel, data=data)
        state.http.send_message = AsyncMock(return_value=data)
        state.http.edit_message = AsyncMock(return_value=data)
        return state, discord.Message(state=state, channel=self.channel, data=data)

    async def test_assessment_and_action_rows_are_in_distinct_labelled_containers(self):
        await self.create_report()
        call = self.channel.send.call_args
        self.assertIsNone(call.args[0])
        view = call.kwargs['view']
        self.assertIsInstance(view, discord.ui.LayoutView)
        components = view.to_components()
        self.assertEqual([item['type'] for item in components], [17, 17, 17, 10])
        for section, title, labels in ((components[1], 'Staff assessment', ['Needs attention', 'Looks okay', 'Unsure']),
                                      (components[2], 'Actions', ['Delete message(s)', 'Dismiss', 'Timeout 10 min'])):
            text, row = section['components']
            self.assertEqual(text['type'], 10)
            self.assertIn(title, text['content'])
            self.assertEqual(row['type'], 1)
            self.assertEqual([item['label'] for item in row['components']], labels)
        self.assertTrue(view.is_finished())
        self.assertLessEqual(view.content_length(), 1900)

    async def test_actual_sdk_send_sets_v2_flag_and_omits_legacy_content(self):
        identity = await self.create_report()
        content = self.adapter.live.render_snapshot(identity, 1)
        state, _ = self.sdk_transport()
        self.channel.send = lambda *args, **kwargs: discord.abc.Messageable.send(self.channel, *args, **kwargs)
        await self.adapter.emit('20', content, 'layout-nonce', reviewable=True)
        payload = state.http.send_message.call_args.kwargs['params'].payload
        self.assertEqual(payload['flags'] & (1 << 15), 1 << 15)
        self.assertIsNone(payload.get('content'))
        self.assertFalse(payload.get('embeds'))
        self.assertEqual(payload['allowed_mentions']['parse'], [])
        self.assertEqual(payload['nonce'], 'layout-nonce')
        self.assertTrue(payload['enforce_nonce'])
        self.assertEqual(len(payload['components']), 4)

    async def test_shared_deletion_notice_serializes_as_non_ping_component_message(self):
        from liberdus_moderator.action_transport import DeletionNotice
        state, _ = self.sdk_transport()
        self.channel.send = lambda *args, **kwargs: discord.abc.Messageable.send(self.channel, *args, **kwargs)
        result = DeletionNotice({'automatic': False, 'actor': '98', 'author_id': '50', 'incident_id': 'a' * 32,
                                 'evidence': [{}]}, [('saved-action-key', '10', '100')], 1000)
        await self.adapter.emit('20', result, result.nonce)
        payload = state.http.send_message.call_args.kwargs['params'].payload
        self.assertEqual(payload['flags'] & (1 << 15), 1 << 15)
        self.assertFalse(payload['flags'] & 64)  # Shared channel message, not ephemeral.
        self.assertEqual(payload['allowed_mentions']['parse'], [])
        self.assertIsNone(payload.get('content'))
        self.assertIn('1 message deleted', payload['components'][0]['components'][0]['content'])

    async def test_actual_sdk_converts_old_report_and_preserves_durable_button_binding(self):
        identity = await self.create_report()
        state, message = self.sdk_transport()
        self.channel.fetch_message = AsyncMock(return_value=message)
        event = CommandRequest('1', '20', '98', 'assess', arguments=('looks-okay',), reply_to_message_id='700')
        reply = self.adapter.live.command(event, 'interaction:800', True)
        result = await self.adapter.refresh_assessment_messages(reply, event)
        self.assertIn('Report display updated', result)
        payload = state.http.edit_message.call_args.kwargs['params'].payload
        self.assertIsNone(payload['content'])
        self.assertEqual(payload['embeds'], [])
        self.assertEqual(payload['attachments'], [])
        self.assertEqual(payload['flags'] & (1 << 15), 1 << 15)
        self.assertEqual(payload['allowed_mentions']['parse'], [])
        self.assertIn('**Looks okay** · Complete', payload['components'][1]['components'][0]['content'])
        self.assertEqual(self.adapter.live.review_reply_target(event), (identity, '1'))
        self.assertEqual(state.http.send_message.await_count, 0)

    async def test_rendered_sections_survive_command_pipeline_and_reconnect(self):
        identity = await self.create_report()
        self.adapter.coverage_gap('reconnect')
        self.channel.send.return_value = SimpleNamespace(id=701)
        self.adapter.receive(self.message(310, 20, 98, '!mod incident ' + identity))
        await self.drain()
        view = self.channel.send.call_args.kwargs['view']
        self.assertIsInstance(view, discord.ui.LayoutView)
        event = CommandRequest('1', '20', '98', 'assess', arguments=('unsure',), reply_to_message_id='701')
        self.assertEqual(self.adapter.live.review_reply_target(event), (identity, '2'))
        self.assertIn('Needs recheck', visible_text(self.channel.send.call_args))

    async def test_public_timeout_stays_disabled_even_with_stale_enabled_flag(self):
        identity = await self.create_report()
        engine = self.adapter.live.engine
        engine.config = replace(engine.config, schema_version=2, actions_enabled=True, allow_public_monitored_channels=True,
                                allow_public_deletion=True, included_category_ids=('100',), excluded_category_ids=('200',))
        engine.store.set_setting('deletion_enabled', True)
        engine.store.set_setting('timeout_enabled', True)
        content = self.adapter.live.render_snapshot(identity, 1)
        self.assertIsInstance(content, ReviewMessage)
        view = assessment_buttons(engine, content=content)
        self.addCleanup(view.stop)
        controls = {item.label: item for item in buttons(view)}
        self.assertFalse(controls['Delete message(s)'].disabled)
        self.assertTrue(controls['Timeout 10 min'].disabled)
        self.assertIn('Timeout is disabled by policy', content.action_section)

    async def test_deleted_source_still_identifies_sender_without_fetching_or_pinging(self):
        identity = await self.create_report()
        self.adapter.deleted(1, 10, (100,))
        self.channel.fetch_message = AsyncMock(side_effect=AssertionError('No Discord lookup for saved identity'))
        content = self.adapter.live.render_snapshot(identity, 1)
        self.assertIn('**Sender:** <@50>', content.overview)
        self.assertIn('Author ID: `50`', content.overview)
        self.assertIn('Repeated test message', content.overview)
        await self.adapter.emit('20', content, 'deleted-source-review', reviewable=True)
        self.channel.fetch_message.assert_not_awaited()
        mentions = self.channel.send.call_args.kwargs['allowed_mentions'].to_dict()
        self.assertEqual(mentions['parse'], [])
        self.assertFalse(self.channel.send.call_args.kwargs['allowed_mentions'].replied_user)
