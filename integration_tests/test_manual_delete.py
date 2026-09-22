import json
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
import discord
from layout_helpers import visible_text, buttons
import test_public_deletion as public_tests
import test_actions as action_tests
from liberdus_moderator import actions, manual_delete


class ManualDeleteAdapterTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=public_tests.PublicDeletionAdapterTests.asyncSetUp
    asyncTearDown=public_tests.PublicDeletionAdapterTests.asyncTearDown
    message=public_tests.PublicDeletionAdapterTests.message
    pattern=public_tests.PublicDeletionAdapterTests.pattern
    toggle=public_tests.PublicDeletionAdapterTests.toggle
    drain=public_tests.PublicDeletionAdapterTests.drain
    interaction=action_tests.ActionAdapterTests.interaction

    async def prepare(self):
        incident=self.pattern();self.toggle('deletion')
        self.adapter.live.gap('reconnect')
        self.adapter.live.save_review_prompt('700','20',(incident['id'],1))
        click=self.interaction('liberdus:action:v1:delete')
        await self.adapter.receive_review_click(click);await self.drain()
        view=click.edit_original_response.call_args.kwargs['view']
        return incident,click,buttons(view)[0].custom_id

    async def confirm(self,custom,identity=802):
        click=self.interaction(custom,identity,message=701)
        await self.adapter.receive_review_click(click);await self.drain()
        return click

    async def test_historical_delete_button_fetches_and_confirms_same_message_once(self):
        incident,click,custom=await self.prepare()
        self.assertIn('Fetched now',visible_text(click.edit_original_response.call_args))
        for message in self.messages.values():message.delete.assert_not_awaited()
        await self.confirm(custom)
        for message in self.messages.values():message.delete.assert_awaited_once()
        self.assertEqual(self.adapter.store.db.execute('SELECT count(*) FROM action_evidence_v1').fetchone()[0],3)
        self.assertEqual(self.adapter.store.incident(incident['id'])['status'],'needs_revalidation')
        self.member.timeout.assert_not_awaited()
        for identity in (10,11,12):self.channels[identity].send.assert_not_awaited()

    async def test_success_posts_one_shared_notice_and_duplicate_confirmation_cannot_repeat_it(self):
        incident, _, custom = await self.prepare()
        self.channel.send.reset_mock()
        result = await self.confirm(custom)
        self.channel.send.assert_awaited_once()
        text = visible_text(self.channel.send.call_args)
        self.assertIn('3 messages deleted', text)
        self.assertIn('**Sender:** <@50>', text)
        self.assertIn('**Deleted by:** <@98>', text)
        self.assertIn(incident['id'], text)
        self.assertIn('<#10> · `100`', text)
        self.assertEqual(self.channel.send.call_args.kwargs['allowed_mentions'].to_dict()['parse'], [])
        self.assertTrue(result.response.defer.call_args.kwargs['ephemeral'])
        await self.confirm(custom, identity=803)
        self.channel.send.assert_awaited_once()
        for message in self.messages.values(): message.delete.assert_awaited_once()

    async def test_confirmed_delete_and_shared_receipt_work_in_excluded_staff_category(self):
        self.channels[20].category_id = 200
        self.channels[20].category = self.categories[200]
        self.adapter.refresh_scope()
        await self.test_success_posts_one_shared_notice_and_duplicate_confirmation_cannot_repeat_it()
        for identity in (10,11,12):
            self.channels[identity].send.assert_not_awaited()
        self.member.timeout.assert_not_awaited()

    async def test_partial_batch_notice_lists_only_confirmed_deletions(self):
        _, _, custom = await self.prepare()
        self.messages[101].delete.side_effect = RuntimeError('synthetic lost response')
        self.channel.send.reset_mock()
        await self.confirm(custom)
        text = visible_text(self.channel.send.call_args)
        self.assertIn('1 message deleted', text)
        self.assertIn('1 of 3 selected messages confirmed deleted', text)
        self.assertIn('<#10> · `100`', text)
        self.assertNotIn('<#11> · `101`', text)
        self.messages[102].delete.assert_not_awaited()

    async def test_uncertain_or_already_absent_deletion_never_posts_success_notice(self):
        for kind in ('uncertain', 'absent', 'denied'):
            # Each subcase gets its own clean fixture and durable ledger.
            if kind != 'uncertain':
                await self.asyncTearDown()
                await self.asyncSetUp()
            _, _, custom = await self.prepare()
            error = (RuntimeError('synthetic lost response') if kind == 'uncertain' else
                     discord.NotFound(SimpleNamespace(status=404, reason='missing'), 'missing') if kind == 'absent' else
                     discord.Forbidden(SimpleNamespace(status=403, reason='denied'), 'denied'))
            for message in self.messages.values(): message.delete.side_effect = error
            self.channel.send.reset_mock()
            await self.confirm(custom)
            self.channel.send.assert_not_awaited()

    async def test_shared_notice_failure_preserves_success_and_does_not_retry_deletion(self):
        _, _, custom = await self.prepare()
        self.channel.send.reset_mock()
        self.channel.send.side_effect = RuntimeError('synthetic notice delivery failure')
        result = await self.confirm(custom)
        self.assertIn('done', visible_text(result.edit_original_response.call_args))
        self.assertIn('Shared deletion notice could not be confirmed', visible_text(result.edit_original_response.call_args))
        self.assertEqual(self.adapter.store.db.execute("SELECT count(*) FROM action_attempts_v1 WHERE outcome='done'").fetchone()[0], 3)
        await self.confirm(custom, identity=803)
        self.channel.send.assert_awaited_once()
        for message in self.messages.values(): message.delete.assert_awaited_once()

    async def test_changed_message_is_shown_before_a_new_staff_confirmation(self):
        incident=self.pattern();self.toggle('deletion');self.adapter.live.gap('reconnect')
        self.messages[100].content='New content must be explicitly reviewed.'
        self.messages[100].edited_at=datetime.now(timezone.utc)
        request=manual_delete.request_delete(self.adapter.live.engine,incident['id'],1,'98','20','100')
        text=await self.adapter.prepare_manual_delete(request)
        self.assertIn('CHANGED since saved report',text)
        self.assertIn('New content',text)
        actions.bind(self.adapter.live.engine,text.proposal,'701')
        await self.confirm('liberdus:confirm:v1:'+text.proposal['token'])
        self.messages[100].delete.assert_awaited_once()
        self.messages[101].delete.assert_not_awaited()

    async def test_edit_after_confirmation_preview_prevents_deletion(self):
        incident,click,custom=await self.prepare()
        self.messages[100].content='Changed again after confirmation preview'
        result=await self.confirm(custom)
        self.assertIn('Message changed',visible_text(result.edit_original_response.call_args))
        for message in self.messages.values():message.delete.assert_not_awaited()

    async def test_reconnect_expires_confirmation_but_next_delete_click_can_refresh(self):
        incident,click,custom=await self.prepare()
        self.adapter.coverage_gap('disconnect')
        await self.confirm(custom)
        for message in self.messages.values():message.delete.assert_not_awaited()
        self.adapter.next_command_at=0
        request=manual_delete.request_delete(self.adapter.live.engine,incident['id'],1,'98','20')
        text=await self.adapter.prepare_manual_delete(request)
        self.assertIsInstance(text,actions.ActionText)
        actions.bind(self.adapter.live.engine,text.proposal,'701')
        await self.confirm('liberdus:confirm:v1:'+text.proposal['token'],803)
        for message in self.messages.values():message.delete.assert_awaited_once()

    async def test_absent_message_and_missing_permissions_do_not_create_confirmation(self):
        incident=self.pattern();self.toggle('deletion');self.adapter.live.gap('reconnect')
        request=manual_delete.request_delete(self.adapter.live.engine,incident['id'],1,'98','20')
        self.channels[10].fetch_message.side_effect=discord.NotFound(SimpleNamespace(status=404,reason='not found'),'missing')
        text=await self.adapter.prepare_manual_delete(request)
        self.assertIn('already gone',' '.join(text.split()))
        self.assertFalse(hasattr(text,'proposal'))
        self.channels[10].manage=False
        text=await self.adapter.prepare_manual_delete(request)
        self.assertIn('Manage Messages',text)
        self.assertEqual(self.adapter.store.db.execute('SELECT count(*) FROM action_proposals_v1').fetchone()[0],0)
        for message in self.messages.values():message.delete.assert_not_awaited()

    async def test_scoped_channel_move_during_refresh_and_during_confirmation_prevents_delete(self):
        incident=self.pattern();self.toggle('deletion');self.adapter.live.gap('reconnect')
        request=manual_delete.request_delete(self.adapter.live.engine,incident['id'],1,'98','20')
        async def moved(identity):
            self.channels[10].category_id=200;self.channels[10].category=self.categories[200]
            return self.messages[identity]
        self.channels[10].fetch_message.side_effect=moved
        text=await self.adapter.prepare_manual_delete(request)
        self.assertFalse(hasattr(text,'proposal'))
        for message in self.messages.values():message.delete.assert_not_awaited()


if __name__=='__main__':unittest.main()
