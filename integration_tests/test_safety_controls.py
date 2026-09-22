from layout_helpers import visible_text
"""Stop delivery and acknowledged-interaction lifecycle regressions; no live Discord."""
import asyncio
from contextlib import suppress
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from liberdus_moderator import actions
from liberdus_moderator.commands import CommandRequest, handle_command
from liberdus_moderator.engine import Engine
from liberdus_moderator.live import LiveSession
import test_hermes_adapter as existing


class SafetyControlTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = existing.AdapterTests.asyncSetUp
    asyncTearDown = existing.AdapterTests.asyncTearDown
    message = existing.AdapterTests.message
    interaction = existing.AdapterTests.interaction
    create_report = existing.AdapterTests.create_report
    reviews = existing.AdapterTests.reviews
    drain = existing.AdapterTests.drain

    async def stop_worker(self):
        self.adapter.worker.cancel()
        with suppress(asyncio.CancelledError):
            await self.adapter.worker

    async def notices(self):
        if self.adapter.notice_tasks:
            await asyncio.wait_for(asyncio.gather(*list(self.adapter.notice_tasks)), 1)

    def enable_actions(self):
        self.adapter.policy = replace(self.adapter.policy, schema_version=2, actions_enabled=True)
        self.adapter.live = LiveSession(Engine(self.adapter.policy, self.adapter.store))
        for name in ('deletion', 'auto-delete', 'timeout'):
            handle_command(self.adapter.live.engine, CommandRequest('1','20','98',name,arguments=('on',)))

    def send_stop(self, command, identity=811):
        self.adapter.receive(self.message(identity,20,98,'!mod '+command))

    def receipt(self, identity):
        return self.adapter.store.db.execute('SELECT 1 FROM command_receipts WHERE message_id=?', (str(identity),)).fetchone()

    async def test_each_stop_commits_after_status_without_waiting_for_worker(self):
        self.enable_actions()
        await self.stop_worker()
        self.adapter.receive(self.message(800,20,98,'!mod status'))
        for index, (command, setting, expected) in enumerate((('pause','paused',True),
                ('deletion off','deletion_enabled',False),('auto-delete off','auto_delete_enabled',False),
                ('timeout off','timeout_enabled',False))):
            self.send_stop(command,811+index)
            self.assertEqual(self.adapter.store.get_setting(setting),expected)
            self.assertTrue(self.receipt(811+index))
        self.assertLessEqual(len(self.adapter.stop_replies),4)
        self.adapter.next_command_at = asyncio.get_running_loop().time()+100
        self.adapter.worker = asyncio.create_task(self.adapter.run_worker())
        await self.drain()
        self.assertIn('Safety controls applied',visible_text(self.channel.send.call_args))

    async def test_stop_during_review_reply_await_is_immediate(self):
        await self.create_report()
        entered, release = asyncio.Event(), asyncio.Event()
        click=self.interaction()
        async def send(*args,**kwargs):
            entered.set(); await release.wait()
        click.edit_original_response.side_effect=send
        await self.adapter.receive_review_click(click)
        await asyncio.wait_for(entered.wait(),1)
        self.send_stop('pause')
        self.assertTrue(self.adapter.store.get_setting('paused'))
        self.assertTrue(self.receipt(811))
        release.set(); await self.drain()

    async def test_stop_survives_every_evidence_reset_and_disconnect(self):
        self.enable_actions()
        await self.stop_worker()
        for index, reason in enumerate(('startup','disconnect','reconnect','queue_full','unavailable_edit',
                                        'deleted_message','invalid_event','worker_failure')):
            self.adapter.store.set_setting('paused',False)
            self.send_stop('pause',820+index)
            self.adapter.coverage_gap(reason)
            self.assertTrue(self.adapter.store.get_setting('paused'),reason)
            self.assertTrue(self.receipt(820+index))
            self.assertEqual(self.adapter.queue.qsize(),1)
        self.adapter.lost_connection()
        self.assertTrue(self.adapter.store.get_setting('paused'))
        self.assertEqual(self.adapter.queue.qsize(),1)

    async def test_full_queue_and_duplicate_flood_keep_bounded_durable_stop(self):
        await self.stop_worker()
        self.adapter.queue=asyncio.Queue(maxsize=1)
        self.adapter.enqueue('message',None)
        self.send_stop('pause')
        self.assertTrue(self.adapter.store.get_setting('paused'))
        self.assertEqual(self.adapter.queue.qsize(),1)
        for _ in range(300): self.send_stop('pause')
        self.assertEqual(self.adapter.queue.qsize(),1)
        self.assertEqual(len(self.adapter.stop_replies),1)
        self.assertEqual(self.adapter.store.db.execute('SELECT count(*) FROM command_receipts').fetchone()[0],1)

    async def test_earlier_enable_cannot_override_stop_even_when_redelivered(self):
        self.enable_actions()
        await self.stop_worker()
        for index,(enable,stop,setting) in enumerate((('resume','pause','paused'),
                ('deletion on','deletion off','deletion_enabled'),('auto-delete on','auto-delete off','auto_delete_enabled'),
                ('timeout on','timeout off','timeout_enabled'))):
            old=self.message(850+index,20,98,'!mod '+enable)
            self.adapter.receive(old)
            self.send_stop(stop,860+index)
            self.assertTrue(self.receipt(old.id))
            self.adapter.receive(old)
        self.adapter.worker=asyncio.create_task(self.adapter.run_worker())
        await self.drain()
        self.assertTrue(self.adapter.store.get_setting('paused'))
        for setting in ('deletion_enabled','auto_delete_enabled','timeout_enabled'):
            self.assertFalse(self.adapter.store.get_setting(setting))
        self.adapter.next_command_at=0
        self.adapter.receive(self.message(870,20,98,'!mod resume'))
        await self.drain()
        self.assertFalse(self.adapter.store.get_setting('paused'))
        self.adapter.next_command_at=0
        self.adapter.receive(self.message(871,20,98,'!mod deletion on'))
        await self.drain()
        self.assertTrue(self.adapter.store.get_setting('deletion_enabled'))

    async def test_unauthorized_invalid_policy_or_private_channel_stop_has_no_mutation(self):
        for index,(author,channel) in enumerate(((50,20),(98,10),(98,999))):
            self.adapter.receive(self.message(880+index,channel,author,'!mod pause'))
        self.assertFalse(self.adapter.store.get_setting('paused',False))
        self.adapter.policy_current.return_value=False
        self.send_stop('pause',884)
        self.assertFalse(self.receipt(884))
        self.adapter.policy_current.return_value=True
        self.channel.permissions_for.side_effect=lambda member:SimpleNamespace(administrator=False,view_channel=True,
            read_message_history=True,send_messages=True)
        self.send_stop('pause',885)
        self.assertFalse(self.receipt(885))
        self.assertFalse(self.adapter.store.get_setting('paused',False))

    async def test_reply_failure_does_not_undo_stop_or_cause_replay(self):
        self.channel.send.side_effect=TimeoutError()
        self.send_stop('pause');await self.drain()
        self.assertTrue(self.adapter.store.get_setting('paused'))
        self.adapter.store.set_setting('paused',False)
        self.send_stop('pause');await self.drain()
        self.assertFalse(self.adapter.store.get_setting('paused'))
        self.channel.send.assert_awaited_once()

    async def test_cancel_review_on_reset_completes_ack_without_assessment(self):
        await self.create_report();await self.stop_worker()
        click=self.interaction()
        await self.adapter.receive_review_click(click)
        self.adapter.deleted(1,10,(991,));await self.notices()
        click.response.defer.assert_awaited_once()
        click.edit_original_response.assert_awaited_once()
        self.assertIn('cancelled',' '.join(visible_text(click.edit_original_response.call_args).split()))
        self.assertEqual(self.reviews(),[])
        self.assertEqual(self.adapter.deferred_interactions,{})

    async def test_stale_generation_review_completes_without_assessment(self):
        await self.create_report();await self.stop_worker()
        click=self.interaction()
        await self.adapter.receive_review_click(click)
        self.adapter.generation+=1
        self.adapter.worker=asyncio.create_task(self.adapter.run_worker())
        await self.drain();await self.notices()
        click.edit_original_response.assert_awaited_once()
        self.assertEqual(self.reviews(),[])

    async def test_confirm_and_cancel_clicks_reset_without_consuming_or_replaying(self):
        self.enable_actions();identity=await self.create_report();await self.stop_worker()
        for index, prefix in enumerate(('liberdus:confirm:v1:','liberdus:cancel:v1:')):
            # Proposal token is deliberately nonexistent: cancellation must never consume it.
            click=self.interaction(900+index,data={'component_type':2,'custom_id':prefix+'a'*32})
            await self.adapter.receive_review_click(click)
            self.adapter.finish_own_deletions();await self.notices()
            click.edit_original_response.assert_awaited_once()
            self.assertIn('cancelled',visible_text(click.edit_original_response.call_args))
        self.assertEqual(actions.history(self.adapter.live.engine,identity),[])

    async def test_disconnect_finishes_pending_ack_before_closing_http_session(self):
        await self.create_report();await self.stop_worker()
        click=self.interaction();events=[]
        click.edit_original_response.side_effect=lambda *args,**kwargs:events.append('notice')
        self.adapter.client.close.side_effect=lambda:events.append('close')
        await self.adapter.receive_review_click(click)
        await self.adapter.disconnect()
        self.assertEqual(events,['notice','close'])
        self.assertEqual(self.adapter.deferred_interactions,{})

    async def test_interrupted_active_reply_gets_completion_before_shutdown(self):
        await self.create_report()
        click=self.interaction();entered=asyncio.Event();events=[]
        async def send(*args,**kwargs):
            events.append('notice')
            if len(events)==1:
                entered.set();await asyncio.Event().wait()
        click.edit_original_response.side_effect=send
        self.adapter.client.close.side_effect=lambda:events.append('close')
        await self.adapter.receive_review_click(click)
        await asyncio.wait_for(entered.wait(),1)
        await self.adapter.disconnect()
        self.assertEqual(events,['notice','notice','close'])
        self.assertIn('Processing interrupted',visible_text(click.edit_original_response.call_args))

    async def test_full_queue_after_defer_completes_without_mutation(self):
        await self.create_report();await self.stop_worker()
        self.adapter.queue=asyncio.Queue(maxsize=1)
        click=self.interaction()
        async def fill(**kwargs):self.adapter.queue.put_nowait((self.adapter.generation,'unused',None))
        click.response.defer.side_effect=fill
        await self.adapter.receive_review_click(click)
        click.edit_original_response.assert_awaited_once()
        self.assertIn('busy',visible_text(click.edit_original_response.call_args))
        self.assertEqual(self.reviews(),[])
        self.assertEqual(self.adapter.deferred_interactions,{})

    async def test_outstanding_acknowledgements_and_notices_are_bounded(self):
        await self.create_report();await self.stop_worker()
        for index in range(200):
            click=self.interaction(1000+index)
            self.assertTrue(await self.adapter.defer_interaction(click))
        overflow=self.interaction(1200)
        self.assertFalse(await self.adapter.defer_interaction(overflow))
        overflow.response.defer.assert_not_awaited()
        overflow.response.send_message.assert_awaited_once()
        self.assertEqual(len(self.adapter.deferred_interactions),200)
        await self.adapter.disconnect()
        self.assertFalse(self.adapter.notice_tasks)
        self.assertFalse(self.adapter.deferred_interactions)

    async def test_every_gap_reason_completes_review_ack_once(self):
        await self.create_report();await self.stop_worker()
        for index, reason in enumerate(('startup','disconnect','reconnect','queue_full','unavailable_edit',
                                        'deleted_message','invalid_event','worker_failure')):
            click=self.interaction(1300+index)
            await self.adapter.receive_review_click(click)
            self.adapter.coverage_gap(reason)
            self.adapter.coverage_gap(reason)
            await self.notices()
            click.edit_original_response.assert_awaited_once()
        self.assertEqual(self.reviews(),[])
        self.assertFalse(self.adapter.deferred_interactions)

    async def test_stop_reply_tail_flushes_reports_without_another_event(self):
        self.enable_actions();await self.stop_worker()
        for index, channel in enumerate((10,11,12)):
            self.adapter.receive(self.message(1400+index,channel))
        self.send_stop('deletion off',1410)
        self.adapter.worker=asyncio.create_task(self.adapter.run_worker())
        await self.drain()
        self.assertEqual(self.adapter.store.db.execute("SELECT count(*) FROM reports WHERE status='sent'").fetchone()[0],1)
        self.assertEqual(self.channel.send.await_count,2)

    async def test_stop_notice_throttle_cannot_throttle_the_state_change(self):
        self.send_stop('pause');await self.drain()
        self.adapter.store.set_setting('paused',False)
        self.send_stop('pause',1500);await self.drain()
        self.assertTrue(self.adapter.store.get_setting('paused'))
        self.assertTrue(self.receipt(1500))
        self.channel.send.assert_awaited_once()

    async def test_shutdown_drains_inflight_defer_before_http_close(self):
        await self.create_report();await self.stop_worker()
        click=self.interaction();entered,release=asyncio.Event(),asyncio.Event();events=[]
        async def defer(**kwargs):
            entered.set();await release.wait()
            events.append('acknowledged')
        async def complete(*args,**kwargs):
            self.assertNotIn('close',events)
            events.append('notice')
        click.response.defer.side_effect=defer
        click.edit_original_response.side_effect=complete
        self.adapter.client.close.side_effect=lambda:events.append('close')
        receiving=asyncio.create_task(self.adapter.receive_review_click(click))
        await asyncio.wait_for(entered.wait(),1)
        shutdown=asyncio.create_task(self.adapter.disconnect())
        await asyncio.sleep(0)
        self.assertTrue(self.adapter.closing)
        self.assertNotIn('close',events)
        denied=self.interaction(1800)
        self.assertFalse(await self.adapter.defer_interaction(denied))
        denied.response.defer.assert_not_awaited()
        release.set()
        await asyncio.wait_for(asyncio.gather(receiving,shutdown),1)
        self.assertEqual(events,['acknowledged','notice','close'])
        click.edit_original_response.assert_awaited_once()
        self.assertFalse(self.adapter.deferred_interactions)
        self.assertFalse(self.adapter.interaction_receivers)
