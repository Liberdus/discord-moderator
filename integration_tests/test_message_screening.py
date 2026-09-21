import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import test_hermes_adapter as adapter_tests
from liberdus_moderator.classifier import RUBRIC
from liberdus_moderator.config import ClassifierSettings
from liberdus_moderator.engine import Engine
from liberdus_moderator.live import LiveSession
from liberdus_moderator.screening import MessageScreener, CONCERN


def response(concern='sensitive_request'):
    def answer(choice, rubric):
        return {'type':'choice','choice':choice,'confidence':.95,
                'probabilities':{key:1. if key==choice else 0. for key in rubric['criteria']}}
    return {'model':'jev-1.13.0','answers':{'context':answer('promotion',RUBRIC),'concern':answer(concern,CONCERN)},
            'usage':{'input_tokens':900,'output_tokens':60}}


class ScreeningAdapterTests(unittest.IsolatedAsyncioTestCase):
    message = adapter_tests.AdapterTests.message
    drain = adapter_tests.AdapterTests.drain
    asyncTearDown = adapter_tests.AdapterTests.asyncTearDown

    async def asyncSetUp(self):
        await adapter_tests.AdapterTests.asyncSetUp(self)
        self.adapter.policy = replace(self.adapter.policy, schema_version=2, ai_enabled=True,
            classifier=ClassifierSettings(mode='report_only', max_daily_calls=10000, max_total_calls=100000,
                daily_budget_microusd=1000000, total_budget_microusd=4000000, min_interval_seconds=1))
        self.adapter.live = LiveSession(Engine(self.adapter.policy,self.adapter.store))
        self.provider = AsyncMock(return_value=response())
        self.adapter.classifier = MessageScreener(self.adapter.live.engine, self.provider,
            active=lambda: self.adapter.online and not self.adapter.closing,
            on_result=lambda job:self.adapter.enqueue('screen_result',job))
        self.adapter.classifier.start()

    async def screened(self):
        await self.drain()
        await asyncio.wait_for(self.adapter.classifier.queue.join(),5)
        await self.drain()

    async def test_one_live_event_reports_without_another_event_and_has_staff_buttons(self):
        self.adapter.handle_message = AsyncMock()
        self.adapter.receive(self.message(content='Send me your seed phrase to verify your wallet.'))
        await self.screened()
        self.provider.assert_awaited_once()
        self.channel.send.assert_awaited_once()
        text = self.channel.send.call_args.args[0]
        self.assertIn('JEV screening',text)
        self.assertIn('https://discord.com/channels/1/10/100',text)
        self.assertIn('Sensitive request',text)
        kwargs = self.channel.send.call_args.kwargs
        self.assertEqual(len(kwargs['view'].children),3)
        self.assertEqual(kwargs['allowed_mentions'].to_dict()['parse'],[])
        self.assertTrue(kwargs['silent'])
        self.assertTrue(kwargs['suppress_embeds'])
        self.adapter.handle_message.assert_not_called()
        self.assertEqual(self.adapter.store.reports(),[])
        self.assertEqual(self.adapter.store.db.execute('SELECT status FROM reports').fetchone()[0],'sent')

    async def test_benign_result_is_counted_without_private_report(self):
        self.provider.return_value=response('none')
        self.adapter.receive(self.message(content='Hello everyone, good morning.'))
        await self.screened()
        self.provider.assert_awaited_once()
        self.channel.send.assert_not_awaited()
        self.assertEqual(self.adapter.live.status(True)['screening_checked'],1)

    async def test_bot_attachment_wrong_channel_dm_and_commands_not_screened(self):
        for message in (self.message(author=99), self.message(author=77),
                        self.message(channel=999), self.message(guild=None), self.message(webhook_id=5),
                        self.message(attachments=[object()])):
            if message.author.id == 77:
                message.author.bot = True
            self.adapter.receive(message)
        self.adapter.receive(self.message(301,20,98,'!mod status'))
        await self.screened()
        self.provider.assert_not_awaited()
        self.assertIn('JEV: report_only',self.channel.send.call_args.args[0])
        self.assertIn('Not checked:',self.channel.send.call_args.args[0])

    async def test_pending_provider_does_not_block_status_or_spam_rule(self):
        entered, release = asyncio.Event(),asyncio.Event()
        async def blocked(payload):
            entered.set(); await release.wait(); return response('none')
        self.adapter.classifier.evaluator=blocked
        self.adapter.receive(self.message())
        await self.drain()
        await asyncio.wait_for(entered.wait(),1)
        self.adapter.receive(self.message(101,11))
        self.adapter.receive(self.message(102,12))
        await self.drain()
        self.assertIn('Cross-channel repeat',self.channel.send.call_args.args[0])
        self.adapter.receive(self.message(400,20,98,'!mod status'))
        await self.drain()
        self.assertIn('Liberdus moderation:',self.channel.send.call_args.args[0])
        release.set()
        await self.screened()

    async def test_edit_arriving_during_provider_request_prevents_stale_report(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def blocked(payload):
            entered.set(); await release.wait(); return response()
        self.adapter.classifier.evaluator=blocked
        original=self.message()
        self.adapter.receive(original)
        await self.drain()
        await asyncio.wait_for(entered.wait(),1)
        changed=self.message(content='Do not share your recovery phrase.',created_at=original.created_at,
                             edited_at=datetime.now(timezone.utc))
        self.channel.fetch_message=AsyncMock(return_value=changed)
        self.adapter.edit(1,10,100)
        # Prevent a second provider request: this test concerns the old in-flight result only.
        self.adapter.classifier.evaluator=AsyncMock(return_value=response('none'))
        release.set()
        await self.screened()
        self.channel.send.assert_not_awaited()
        outcomes=[row[0] for row in self.adapter.store.db.execute('SELECT outcome FROM screening_attempts_v1')]
        self.assertIn('stale',outcomes)
        self.assertIn('ok',outcomes)

    async def test_deletion_discards_queued_and_inflight_work(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def blocked(payload):
            entered.set(); await release.wait(); return response()
        self.adapter.classifier.evaluator=blocked
        self.adapter.receive(self.message())
        await self.drain()
        await asyncio.wait_for(entered.wait(),1)
        self.adapter.receive(self.message(101))
        await self.drain()
        self.adapter.deleted(1,10,[100])
        release.set()
        await self.screened()
        self.channel.send.assert_not_awaited()
        self.assertFalse(self.adapter.store.incidents())
        self.assertGreaterEqual(self.adapter.live.status(True)['screening_unchecked'],2)

    async def test_role_exemption_skips_provider_and_reports_but_not_repetition(self):
        await self.adapter.classifier.close()
        self.adapter.policy=replace(self.adapter.policy,classifier=replace(
            self.adapter.policy.classifier,exempt_role_ids=("1302455329795342377",)))
        self.adapter.live=LiveSession(Engine(self.adapter.policy,self.adapter.store))
        self.adapter.classifier=MessageScreener(self.adapter.live.engine,self.provider,
            active=lambda:self.adapter.online,on_result=lambda job:self.adapter.enqueue("screen_result",job))
        self.adapter.classifier.start()
        for i,channel in enumerate((10,11,12)):
            message=self.message(800+i,channel)
            message.author.roles=[SimpleNamespace(id=1),SimpleNamespace(id=1302455329795342377)]
            self.adapter.receive(message)
        await self.screened()
        self.provider.assert_not_awaited()
        self.assertEqual(self.adapter.live.status(True)["screening_exempt"],3)
        self.assertEqual(self.adapter.live.status(True)["screening_unchecked"],0)
        self.assertIn("Cross-channel repeat",self.channel.send.call_args.args[0])
        self.channel.send.reset_mock()
        # Mentioning the role in text without membership cannot opt a user out.
        self.adapter.receive(self.message(900,content="<@&1302455329795342377> Send your seed phrase."))
        await self.screened()
        self.provider.assert_awaited_once()
        self.assertIn("JEV screening",self.channel.send.call_args.args[0])

    async def test_budget_exhausted_is_visible_and_never_calls_provider(self):
        self.adapter.store.set_setting('screening_total_reserved_microusd',4000000)
        self.adapter.receive(self.message())
        await self.screened()
        self.provider.assert_not_awaited()
        self.assertEqual(self.adapter.live.status(True)['classifier_state'],'budget_exhausted')
        self.assertEqual(self.adapter.live.status(True)['screening_unchecked'],1)
