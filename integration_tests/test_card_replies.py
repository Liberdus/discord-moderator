"""Real SDK payloads for commands, private notices and V2 response recovery."""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import discord
import test_hermes_adapter as adapter_tests
import test_review_layout as review_tests
from layout_helpers import payload_text
from liberdus_moderator.commands import CommandRequest
from liberdus_moderator.display import panel


class CardReplyTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = adapter_tests.AdapterTests.asyncSetUp
    asyncTearDown = adapter_tests.AdapterTests.asyncTearDown
    message = adapter_tests.AdapterTests.message
    drain = adapter_tests.AdapterTests.drain
    create_report = adapter_tests.AdapterTests.create_report
    sdk_transport = review_tests.ReviewLayoutTests.sdk_transport

    def assert_card_payload(self, payload):
        self.assertEqual(payload['flags'] & (1 << 15), 1 << 15)
        self.assertIsNone(payload.get('content'))
        self.assertFalse(payload.get('embeds'))
        self.assertEqual(payload['allowed_mentions']['parse'], [])
        self.assertNotIn('```', payload_text(payload))
        self.assertNotIn('━━━━━━━━', payload_text(payload))
        self.assertEqual(payload['components'][0]['type'], 17)
        self.assertLessEqual(len(payload_text(payload).encode('utf-16-le')) // 2, 4000)

    async def test_every_staff_command_uses_cards_and_keeps_scope_nonce_and_mentions(self):
        identity = await self.create_report()
        state, _ = self.sdk_transport()
        self.channel.send = lambda *args, **kwargs: discord.abc.Messageable.send(self.channel, *args, **kwargs)
        commands = ('help', 'status', 'summary', 'connection', 'pending', 'selftest',
                    'exempt-role', 'deletion', 'auto-delete', 'timeout', 'actions', 'incident', 'explain')
        for index, name in enumerate(commands):
            with self.subTest(command=name):
                event = CommandRequest('1', '20', '98', name,
                                       arguments=(identity,) if name in ('actions', 'incident', 'explain') else ())
                content = self.adapter.live.command(event, 'card-' + str(index), True)
                await self.adapter.emit('20', content, 'card-nonce-' + str(index),
                                        reviewable=getattr(content, 'review_target', None) is not None)
                payload = state.http.send_message.call_args.kwargs['params'].payload
                self.assert_card_payload(payload)
                self.assertEqual(payload['nonce'], 'card-nonce-' + str(index))
                self.assertTrue(payload['enforce_nonce'])
                self.assertFalse(payload['flags'] & 64)
                self.adapter.client.get_channel.assert_called_with(20)

    async def test_immediate_error_reply_is_ephemeral_v2_with_actual_response_sdk(self):
        state, _ = self.sdk_transport()
        state.http.proxy = state.http.proxy_auth = None
        interaction = SimpleNamespace(id=800, token='synthetic', _state=state, _session=object(),
                                      type=discord.InteractionType.component, channel=self.channel)
        interaction.response = discord.InteractionResponse(interaction)
        transport = SimpleNamespace(create_interaction_response=AsyncMock(
            return_value={'interaction': {'id': '800', 'response_message_id': '701'}}))
        with patch('discord.interactions.async_context', SimpleNamespace(get=lambda: transport)):
            await self.adapter.interaction_notice(interaction, 'Moderation is busy. No action taken.')
        transport.create_interaction_response.assert_awaited_once()
        request = transport.create_interaction_response.call_args.kwargs['params'].payload
        self.assertEqual(request['type'], 4)
        payload = request['data']
        self.assert_card_payload(payload)
        self.assertEqual(payload['flags'] & 64, 64)
        self.assertIn('Moderation is busy', payload_text(payload))
        self.assertTrue(state.store_view.call_args.args[0].is_finished())



if __name__ == '__main__':
    unittest.main()
