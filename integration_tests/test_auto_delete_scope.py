"""Approved four-concern auto deletion through the real SDK, with mocked HTTP."""
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import discord

from layout_helpers import visible_text
import test_actions as action_tests
import test_public_deletion as public_tests
from test_message_screening import response
from liberdus_moderator.hermes_adapter import snapshot
from liberdus_moderator.screening import MessageScreener


CONCERNS = ('sensitive_request', 'impersonation', 'suspicious_offer', 'targeted_abuse')


class AutomaticScopeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = public_tests.PublicDeletionAdapterTests.asyncSetUp
    asyncTearDown = public_tests.PublicDeletionAdapterTests.asyncTearDown
    message = action_tests.ActionAdapterTests.message
    toggle = action_tests.ActionAdapterTests.toggle
    sdk_message = action_tests.ActionAdapterTests.sdk_message
    drain = action_tests.ActionAdapterTests.drain

    async def fresh_fixture(self):
        await self.asyncTearDown()
        await self.asyncSetUp()

    async def screen(self, concern, score=.90, *, purpose='other', block=None):
        self.toggle('deletion')
        self.toggle('auto-delete')
        # Real configured staff-channel exception inside an excluded category.
        self.channel.category_id = 200
        self.channel.category = self.categories[200]
        self.adapter.refresh_scope()
        raw = response(concern)
        raw['answers']['concern']['confidence'] = score
        raw['answers']['context']['choice'] = purpose
        raw['answers']['context']['probabilities'] = {
            key: float(key == purpose) for key in raw['answers']['context']['probabilities']}
        provider = AsyncMock(return_value=raw)
        self.adapter.classifier = MessageScreener(self.adapter.live.engine, provider,
            on_result=lambda job: self.adapter.enqueue('screen_result', job))
        message, http_delete = self.sdk_message()
        # Snowflakes round to milliseconds. Put the synthetic message after
        # fixture startup's coverage reset without depending on wall-clock races.
        del self.messages[message.id]
        self.now = self.adapter.live.engine._now() + 1
        self.adapter.live.engine.clock = lambda: self.now
        message.id = discord.utils.time_snowflake(datetime.fromtimestamp(self.now, timezone.utc))
        self.messages[message.id] = message
        message.content = 'Synthetic message for the typed ' + concern + ' response.'
        self.adapter.live.engine.process(snapshot(message))
        job = self.adapter.classifier.snapshot(str(message.id))
        self.assertIsNotNone(job)
        if block == 'edit':
            message.content = 'Changed message after screening snapshot.'
        elif block == 'permission':
            self.channels[10].manage = False
        elif block == 'report':
            self.channel.send.side_effect = TimeoutError('Synthetic report delivery failure')
        elif block == 'stale':
            async def report(*args, **kwargs):
                self.now += 61
                return SimpleNamespace(id=700)
            self.channel.send.side_effect = report
        await self.adapter.classifier.evaluate_one(job)
        await self.drain()
        provider.assert_awaited_once()
        self.member.timeout.assert_not_awaited()
        for identity in (10, 11, 12):
            self.channels[identity].send.assert_not_awaited()
        return message, http_delete

    async def test_each_concern_at_point_ninety_deletes_once_and_posts_shared_receipt(self):
        for index, concern in enumerate(CONCERNS):
            if index:
                await self.fresh_fixture()
            with self.subTest(concern=concern):
                message, http_delete = await self.screen(concern)
                http_delete.assert_awaited_once_with(10, message.id)
                rows = self.adapter.store.db.execute('SELECT incident_id,automatic,outcome FROM action_attempts_v1').fetchall()
                self.assertEqual(len(rows), 1)
                self.assertEqual((rows[0]['automatic'], rows[0]['outcome']), (1, 'done'))
                notices = [visible_text(call) for call in self.channel.send.call_args_list]
                self.assertEqual(sum('1 message deleted' in text for text in notices), 1)
                self.assertTrue(any('Automatic moderation' in text for text in notices))
                for call in self.channel.send.call_args_list:
                    self.assertEqual(call.kwargs['allowed_mentions'].to_dict()['parse'], [])
                # Re-queueing a consumed candidate cannot replay its deletion.
                self.adapter.auto_delete_candidates.add(rows[0]['incident_id'])
                await self.adapter.flush_automatic_actions()
                http_delete.assert_awaited_once()

    async def test_all_four_concerns_just_below_threshold_remain_staff_review(self):
        for index, concern in enumerate(CONCERNS):
            if index:
                await self.fresh_fixture()
            with self.subTest(concern=concern):
                _, http_delete = await self.screen(concern, .89999)
                http_delete.assert_not_awaited()
                self.assertEqual(self.adapter.store.db.execute('SELECT count(*) FROM action_attempts_v1').fetchone()[0], 0)
                self.channel.send.assert_awaited_once()

    async def test_expanded_concerns_keep_warning_and_unclear_context_exclusions(self):
        first = True
        for concern in CONCERNS:
            for purpose in ('quoted_warning', 'unclear'):
                if not first:
                    await self.fresh_fixture()
                first = False
                with self.subTest(concern=concern, purpose=purpose):
                    _, http_delete = await self.screen(concern, 1., purpose=purpose)
                    http_delete.assert_not_awaited()
                    self.assertEqual(self.adapter.store.db.execute('SELECT count(*) FROM action_attempts_v1').fetchone()[0], 0)

    async def test_newly_eligible_offer_keeps_edit_permission_freshness_and_report_guards(self):
        for index, block in enumerate(('edit', 'permission', 'stale', 'report')):
            if index:
                await self.fresh_fixture()
            with self.subTest(block=block):
                _, http_delete = await self.screen('suspicious_offer', 1., purpose='promotion', block=block)
                http_delete.assert_not_awaited()
                self.assertEqual(self.adapter.store.db.execute('SELECT count(*) FROM action_attempts_v1').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
