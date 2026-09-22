"""Lost Discord receipts must not strand a spinner or replay a deletion."""
import asyncio
import json
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord
from discord.webhook.async_ import async_context

from layout_helpers import visible_text, payload_text, buttons
import test_manual_delete as manual
from liberdus_moderator.commands import CommandRequest
from liberdus_moderator.interaction_health import KEY, failure_lines, record


class ConfirmationReplyTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = manual.ManualDeleteAdapterTests.asyncSetUp
    asyncTearDown = manual.ManualDeleteAdapterTests.asyncTearDown
    message = manual.ManualDeleteAdapterTests.message
    pattern = manual.ManualDeleteAdapterTests.pattern
    toggle = manual.ManualDeleteAdapterTests.toggle
    drain = manual.ManualDeleteAdapterTests.drain
    interaction = manual.ManualDeleteAdapterTests.interaction
    prepare = manual.ManualDeleteAdapterTests.prepare
    confirm = manual.ManualDeleteAdapterTests.confirm

    def sdk(self, click, *, response=None, error=None):
        """Real SDK defer + edit serialization; synthetic HTTP responses only."""
        state = Mock()
        state.allowed_mentions = discord.AllowedMentions.none()
        state.http.proxy = state.http.proxy_auth = None
        state.store_user.side_effect = lambda data, **kw: discord.User(state=state, data=data)
        click._state = state
        click._session = object()
        click.token = 'synthetic-interaction-token-never-store'
        click.application_id = 99
        click.channel = self.channel
        click.response = discord.InteractionResponse(click)
        click.edit_original_response = MethodType(discord.Interaction.edit_original_response, click)
        result = dict(id='702', channel_id='20', type=0, content='Synthetic reply', attachments=[], embeds=[],
                      edited_timestamp=None, tts=False, pinned=False, mention_everyone=False, mentions=[], mention_roles=[],
                      author=dict(id='99', username='testbot', discriminator='0', avatar=None, bot=True))
        return SimpleNamespace(create_interaction_response=AsyncMock(
            return_value={'interaction': {'id': str(click.id)}} if response is None else response,
            side_effect=error), edit_original_interaction_response=AsyncMock(return_value=result))

    def history(self):
        return self.adapter.store.get_setting(KEY, [])

    def assert_no_action(self):
        for message in self.messages.values():
            message.delete.assert_not_awaited()
        self.assertEqual(self.adapter.store.db.execute('SELECT count(*) FROM action_attempts_v1').fetchone()[0], 0)
        self.assertEqual(self.adapter.store.db.execute('SELECT state FROM action_proposals_v1').fetchone()[0], 'pending')

    async def test_sdk_failure_after_accepted_ack_clears_placeholder_without_consuming_confirmation(self):
        _, _, custom = await self.prepare()
        click = self.interaction(custom, message=701)
        # SDK marks this acknowledged, then fails parsing a malformed receipt.
        transport = self.sdk(click, response={})
        token = async_context.set(transport)
        try:
            await self.adapter.receive_review_click(click)
            await self.drain()
        finally:
            async_context.reset(token)
        self.assertTrue(click.response.is_done())
        self.assert_no_action()
        transport.create_interaction_response.assert_awaited_once()
        transport.edit_original_interaction_response.assert_awaited_once()
        payload = transport.edit_original_interaction_response.call_args.kwargs['payload']
        self.assertIn('cancelled before', payload_text(payload))
        self.assertEqual(payload['allowed_mentions']['parse'], [])
        self.assertEqual(payload['components'][0]['type'], 17)
        self.assertEqual(payload['flags'] & (1 << 15), 1 << 15)
        self.assertIsNone(payload['content'])
        self.assertEqual([item['stage'] for item in self.history()][-3:], ['received', 'ack_error', 'recovered'])
        self.assertFalse(self.adapter.deferred_interactions)
        self.assertFalse(self.adapter.interaction_receivers)

    async def test_lost_ack_receipt_is_not_retried_and_can_finish_original_reply(self):
        _, _, custom = await self.prepare()
        click = self.interaction(custom, message=701)
        transport = self.sdk(click, error=TimeoutError('secret-error-body'))
        token = async_context.set(transport)
        try:
            await self.adapter.receive_review_click(click)
            await self.drain()
        finally:
            async_context.reset(token)
        self.assert_no_action()
        transport.create_interaction_response.assert_awaited_once()
        transport.edit_original_interaction_response.assert_awaited_once()
        self.assertIn('ack_error: timeout', failure_lines(self.adapter.store))
        self.assertNotIn('secret-error-body', json.dumps(self.history()))
        self.assertNotIn(click.token, json.dumps(self.history()))

    async def test_slow_accepted_ack_receipt_can_finish_and_delete_once(self):
        _, _, custom = await self.prepare()
        click = self.interaction(custom, message=701)
        transport = self.sdk(click)
        async def slow_receipt(*args, **kwargs):
            await asyncio.sleep(2.05)  # Old 2s timeout cancelled an accepted click.
            return {'interaction': {'id': str(click.id)}}
        transport.create_interaction_response.side_effect = slow_receipt
        # The serialized worker was created in the fixture's context, before
        # this test's ContextVar override. Mock SDK HTTP in both task contexts.
        with patch('discord.interactions.async_context', SimpleNamespace(get=lambda: transport)):
            await self.adapter.receive_review_click(click)
            await self.drain()
        for message in self.messages.values():
            message.delete.assert_awaited_once()
        transport.edit_original_interaction_response.assert_awaited_once()
        self.assertIn('done', payload_text(transport.edit_original_interaction_response.call_args.kwargs['payload']))

    async def test_lost_action_result_reply_recovers_exact_result_without_repeating_deletion(self):
        _, _, custom = await self.prepare()
        self.channel.send.reset_mock()
        click = self.interaction(custom, message=701)
        click.edit_original_response = AsyncMock(side_effect=[TimeoutError('secret-webhook-url'), None])
        await self.adapter.receive_review_click(click)
        await self.drain()
        self.assertEqual(click.edit_original_response.await_count, 2)
        self.assertEqual(visible_text(click.edit_original_response.call_args_list[0]), visible_text(click.edit_original_response.call_args_list[1]))
        self.assertIn('done', visible_text(click.edit_original_response.call_args))
        self.assertEqual(click.edit_original_response.call_args.kwargs['allowed_mentions'].to_dict()['parse'], [])
        self.channel.send.assert_awaited_once()
        await self.confirm(custom, identity=803)
        for message in self.messages.values():
            message.delete.assert_awaited_once()
        self.channel.send.assert_awaited_once()

    async def test_uncertain_deletion_result_stays_uncertain_when_reply_is_recovered(self):
        _, _, custom = await self.prepare()
        self.messages[100].delete.side_effect = TimeoutError('lost delete response')
        self.channel.send.reset_mock()
        click = self.interaction(custom, message=701)
        click.edit_original_response = AsyncMock(side_effect=[TimeoutError(), None])
        await self.adapter.receive_review_click(click)
        await self.drain()
        self.assertIn('uncertain', visible_text(click.edit_original_response.call_args))
        self.messages[100].delete.assert_awaited_once()
        self.messages[101].delete.assert_not_awaited()
        self.channel.send.assert_not_awaited()

    async def test_expired_confirmation_and_reply_failure_finish_with_rejection(self):
        _, _, custom = await self.prepare()
        self.adapter.store.db.execute('UPDATE action_proposals_v1 SET expires_at=0')
        click = self.interaction(custom, message=701)
        click.edit_original_response = AsyncMock(side_effect=[TimeoutError(), None])
        await self.adapter.receive_review_click(click)
        await self.drain()
        self.assert_no_action()
        self.assertIn('Confirmation expired', visible_text(click.edit_original_response.call_args))
        self.assertIn('rejected', [item['stage'] for item in self.history()])

    async def test_unknown_interaction_and_recovery_errors_are_bounded_and_sanitized(self):
        _, _, custom = await self.prepare()
        click = self.interaction(custom, message=701)
        click.response.defer.side_effect = discord.NotFound(SimpleNamespace(status=404, reason='private'),
            {'code': 10062, 'message': 'secret-payload'})
        click.edit_original_response = AsyncMock(side_effect=TimeoutError('secret-payload'))
        await self.adapter.receive_review_click(click)
        await self.drain()
        self.assert_no_action()
        event = next(item for item in self.history() if item['stage'] == 'ack_error')
        self.assertEqual((event['http_status'], event['discord_code']), (404, 10062))
        self.assertNotIn('secret-payload', json.dumps(self.history()))
        self.assertFalse(self.adapter.deferred_interactions)
        status = self.adapter.live.command(CommandRequest('1', '20', '98', 'status'), 'status-test', True)
        self.assertIn('Last button error', status)
        self.assertIn('recovery_error: timeout', status)
        for _ in range(40):
            record(self.adapter.live.engine, click, 'replied')
        self.assertEqual(len(self.history()), 24)

    async def test_uncertain_preview_delivery_is_not_bound_or_recreated(self):
        incident = self.pattern()
        self.toggle('deletion')
        self.adapter.live.save_review_prompt('700', '20', (incident['id'], 1))
        click = self.interaction('liberdus:action:v1:delete')
        click.edit_original_response = AsyncMock(side_effect=[TimeoutError(), None])
        await self.adapter.receive_review_click(click)
        await self.drain()
        self.assertEqual(click.edit_original_response.await_count, 2)
        self.assertEqual(buttons(click.edit_original_response.call_args.kwargs['view']), [])
        self.assertIn('Confirmation delivery could not', visible_text(click.edit_original_response.call_args))
        self.assertIsNone(self.adapter.store.db.execute('SELECT message_id FROM action_proposals_v1').fetchone()[0])
        self.assert_no_action()


if __name__ == '__main__':
    unittest.main()
