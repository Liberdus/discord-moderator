"""Mock Discord mutation endpoints; exercise the real serialized adapter and durable guards."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

import discord
import test_hermes_adapter as existing
from test_message_screening import response
from liberdus_moderator import actions
from liberdus_moderator.commands import CommandRequest, handle_command
from liberdus_moderator.config import ClassifierSettings
from liberdus_moderator.engine import Engine
from liberdus_moderator.hermes_adapter import snapshot
from liberdus_moderator.live import LiveSession
from liberdus_moderator.screening import MessageScreener


class ActionAdapterTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = existing.AdapterTests.asyncTearDown
    drain = existing.AdapterTests.drain
    message = existing.AdapterTests.message

    async def asyncSetUp(self):
        await existing.AdapterTests.asyncSetUp(self)
        self.adapter.policy = replace(self.adapter.policy,schema_version=2,actions_enabled=True,
            ai_enabled=True,classifier=ClassifierSettings(mode='report_only',max_daily_calls=100,max_total_calls=1000,
                daily_budget_microusd=1000000,total_budget_microusd=4000000,min_interval_seconds=1))
        self.adapter.live=LiveSession(Engine(self.adapter.policy,self.adapter.store))
        self.adapter.classifier_active=Mock(return_value=True)
        self.me=SimpleNamespace(id=99,top_role=5,guild_permissions=SimpleNamespace(moderate_members=True,administrator=False))
        self.guild=SimpleNamespace(id=1,me=self.me,default_role=object(),owner_id=98)
        self.member=SimpleNamespace(id=50,guild=self.guild,bot=False,top_role=1,roles=[],timed_out_until=None,
            guild_permissions=SimpleNamespace(administrator=False,manage_guild=False,moderate_members=False,manage_messages=False),timeout=AsyncMock())
        self.guild.fetch_member=AsyncMock(return_value=self.member)
        self.channels={}
        self.messages={}
        for ch in (10,11,12,20):
            channel=Mock(spec=discord.TextChannel)
            channel.id=ch;channel.guild=self.guild
            channel.permissions_for.side_effect=lambda member:SimpleNamespace(administrator=False,
                view_channel=member is self.me,read_message_history=True,send_messages=True,manage_messages=True)
            channel.fetch_message=AsyncMock(side_effect=lambda identity:self.messages[identity])
            channel.send=AsyncMock(return_value=SimpleNamespace(id=700))
            self.channels[ch]=channel
        self.channel=self.channels[20]
        self.adapter.client.get_channel.side_effect=self.channels.get
        self.operator='98'

    def toggle(self,name,on=True):
        result=handle_command(self.adapter.live.engine,CommandRequest('1','20',self.operator,name,arguments=('on' if on else 'off',)))
        self.assertTrue(result['ok'])

    def pattern(self):
        for i,ch in enumerate((10,11,12)):
            message=self.message(100+i,ch,author=50,guild=self.guild)
            message.channel=self.channels[ch]
            message.author.roles=[]
            message.delete=AsyncMock(side_effect=lambda **kw:None)
            self.messages[100+i]=message
            self.adapter.process_evidence(snapshot(message))
        return self.adapter.store.incidents()[0]

    def plan(self,incident,kind='delete'):
        self.toggle('deletion' if kind=='delete' else 'timeout')
        return actions.plan(self.adapter.live.engine,incident['id'],incident['revision'],kind,self.operator,'20')

    async def test_delete_selected_messages_and_callbacks_are_audited(self):
        incident=self.pattern();payload=self.plan(incident)
        for message in self.messages.values():
            async def deleted(message=message,**kw): self.adapter.deleted(1,message.channel.id,(message.id,))
            message.delete.side_effect=deleted
        result=await self.adapter.perform_action(payload)
        self.assertIn('done',result)
        for message in self.messages.values(): message.delete.assert_awaited_once()
        rows=actions.history(self.adapter.live.engine,incident['id'])
        self.assertEqual(len(rows),3);self.assertTrue(all(row['outcome']=='done' for row in rows))
        await self.adapter.perform_action(payload)
        for message in self.messages.values(): message.delete.assert_awaited_once()
        self.member.timeout.assert_not_awaited()

    async def test_one_edited_message_prevents_entire_batch(self):
        incident=self.pattern();payload=self.plan(incident)
        self.messages[102].content='Edited innocent replacement'
        result=await self.adapter.perform_action(payload)
        self.assertIn('Message changed',result)
        for message in self.messages.values(): message.delete.assert_not_awaited()

    async def test_missing_permission_wrong_identity_and_disk_disable_prevent_delete(self):
        incident=self.pattern();payload=self.plan(incident)
        self.adapter.classifier_active.return_value=False
        await self.adapter.perform_action(payload)
        self.adapter.classifier_active.return_value=True
        original=self.messages[102].author.id;self.messages[102].author.id=51
        await self.adapter.perform_action(payload);self.messages[102].author.id=original
        self.channels[10].permissions_for.side_effect=lambda m:SimpleNamespace(administrator=False,
            view_channel=m is self.me,read_message_history=True,send_messages=True,manage_messages=False)
        text=await self.adapter.perform_action(payload)
        self.assertIn('Manage Messages',text)
        for message in self.messages.values(): message.delete.assert_not_awaited()

    async def test_incoming_edit_during_fetch_aborts_before_mutation(self):
        incident=self.pattern();payload=self.plan(incident)
        # Keep the worker from consuming the queued edit while this direct invocation runs.
        self.adapter.worker.cancel()
        try: await self.adapter.worker
        except asyncio.CancelledError: pass
        async def fetch(identity):
            self.adapter.edit(1,10,identity)
            return self.messages[identity]
        self.channels[10].fetch_message.side_effect=fetch
        result=await self.adapter.perform_action(payload)
        self.assertIn('incoming evidence changed',' '.join(result.split()))
        for message in self.messages.values(): message.delete.assert_not_awaited()

    async def test_uncertain_delete_stops_batch_and_restart_never_retries(self):
        incident=self.pattern();payload=self.plan(incident)
        self.messages[100].delete.side_effect=TimeoutError
        result=await self.adapter.perform_action(payload)
        self.assertIn('uncertain',result)
        self.messages[100].delete.assert_awaited_once();self.messages[101].delete.assert_not_awaited()
        self.adapter.live=LiveSession(Engine(self.adapter.policy,self.adapter.store))
        await self.adapter.perform_action(payload)
        self.messages[100].delete.assert_awaited_once()

    async def test_timeout_is_ten_minutes_staff_only_and_never_shortens_existing(self):
        incident=self.pattern();payload=self.plan(incident,'timeout')
        now=datetime.now(timezone.utc)
        result=await self.adapter.perform_action(payload)
        self.assertIn('Timeout: done',result)
        self.member.timeout.assert_awaited_once()
        until=self.member.timeout.call_args.args[0]
        self.assertTrue(now+timedelta(seconds=599)<=until<=datetime.now(timezone.utc)+timedelta(seconds=601))
        self.assertIn('staff 98',self.member.timeout.call_args.kwargs['reason'])
        await self.adapter.perform_action(payload)
        self.member.timeout.assert_awaited_once()
        for message in self.messages.values(): message.delete.assert_not_awaited()

    async def test_timeout_existing_and_protected_members_are_refused(self):
        incident=self.pattern();payload=self.plan(incident,'timeout')
        scenarios=[('bot',True),('id',98),('top_role',5),('timed_out_until',datetime.now(timezone.utc)+timedelta(hours=1))]
        for key,value in scenarios:
            original=getattr(self.member,key);setattr(self.member,key,value)
            await self.adapter.perform_action(payload);setattr(self.member,key,original)
        for perm in ('administrator','manage_guild','moderate_members','manage_messages'):
            setattr(self.member.guild_permissions,perm,True)
            await self.adapter.perform_action(payload)
            setattr(self.member.guild_permissions,perm,False)
        self.me.guild_permissions.moderate_members=False
        await self.adapter.perform_action(payload)
        self.member.timeout.assert_not_awaited()
        self.assertFalse(actions.history(self.adapter.live.engine,incident['id']))

    async def test_exempt_role_stays_protected_from_timeout_when_screening_exemption_off(self):
        self.adapter.policy=replace(self.adapter.policy,classifier=replace(self.adapter.policy.classifier,exempt_role_ids=('77',)))
        self.adapter.live=LiveSession(Engine(self.adapter.policy,self.adapter.store))
        incident=self.pattern();payload=self.plan(incident,'timeout')
        self.adapter.store.set_setting('role_exemption_enabled',False)
        self.member.roles=[SimpleNamespace(id=77)]
        result=await self.adapter.perform_action(payload)
        self.assertIn('Protected member',result)
        self.member.timeout.assert_not_awaited()

    async def test_off_command_arriving_during_delete_survives_coverage_reset(self):
        incident=self.pattern();payload=self.plan(incident)
        self.adapter.worker.cancel()
        try: await self.adapter.worker
        except asyncio.CancelledError: pass
        async def deleted(**kw):
            command=CommandRequest('1','20','98','deletion',arguments=('off',))
            self.adapter.enqueue('command',('999',command))
        self.messages[100].delete.side_effect=deleted
        await self.adapter.perform_action(payload)
        self.messages[100].delete.assert_awaited_once()
        self.messages[101].delete.assert_not_awaited()
        self.assertEqual(self.adapter.queue.qsize(),1)
        self.adapter.worker=asyncio.create_task(self.adapter.run_worker())
        await self.drain()
        self.assertFalse(actions.enabled(self.adapter.live.engine,'deletion'))

    async def test_timeout_off_and_pause_block_prepared_payload(self):
        incident=self.pattern();payload=self.plan(incident,'timeout')
        self.toggle('timeout',False)
        await self.adapter.perform_action(payload)
        self.toggle('timeout')
        payload=actions.plan(self.adapter.live.engine,incident['id'],1,'timeout',self.operator,'20')
        self.adapter.live.engine.set_paused(True)
        await self.adapter.perform_action(payload)
        self.member.timeout.assert_not_awaited()

    async def test_auto_delete_real_typed_result_after_report_no_account_action(self):
        self.toggle('deletion');self.toggle('auto-delete')
        raw=response('sensitive_request');raw['answers']['context']['choice']='other'
        raw['answers']['context']['probabilities']={key:1. if key=='other' else 0. for key in raw['answers']['context']['probabilities']}
        provider=AsyncMock(return_value=raw)
        self.adapter.classifier=MessageScreener(self.adapter.live.engine,provider,
            on_result=lambda job:self.adapter.enqueue('screen_result',job))
        message=self.message(100,10,guild=self.guild,content='Send me your wallet recovery phrase.')
        message.channel=self.channels[10]
        message.author.roles=[];message.delete=AsyncMock();self.messages[100]=message
        self.adapter.live.engine.process(snapshot(message))
        job=self.adapter.classifier.snapshot('100')
        await self.adapter.classifier.evaluate_one(job)
        await self.drain()
        message.delete.assert_awaited_once()
        self.member.timeout.assert_not_awaited()
        self.assertGreaterEqual(self.channel.send.await_count,2)
        self.assertEqual(provider.await_count,1)
        record=self.adapter.store.db.execute('SELECT * FROM action_attempts_v1').fetchone()
        self.assertEqual(record['automatic'],1);self.assertEqual(record['outcome'],'done')

    def interaction(self,custom,identity=800,author=98,message=700):
        return SimpleNamespace(id=identity,type=discord.InteractionType.component,data={'component_type':2,'custom_id':custom},
            guild_id=1,channel_id=20,user=SimpleNamespace(id=author,bot=False),
            message=SimpleNamespace(id=message,author=SimpleNamespace(id=99),channel=self.channel),
            response=SimpleNamespace(defer=AsyncMock(),send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=701))))

    async def test_delete_button_proposes_then_bound_confirmation_executes_once(self):
        incident=self.pattern();self.toggle('deletion')
        self.adapter.live.save_review_prompt('700','20',(incident['id'],1))
        click=self.interaction('liberdus:action:v1:delete')
        await self.adapter.receive_review_click(click);await self.drain()
        view=click.followup.send.call_args.kwargs['view']
        self.assertTrue(click.followup.send.call_args.kwargs['ephemeral'])
        for message in self.messages.values(): message.delete.assert_not_awaited()
        custom=view.children[0].custom_id
        unauthorized=self.interaction(custom,801,author=50,message=701)
        await self.adapter.receive_review_click(unauthorized);await self.drain()
        for message in self.messages.values(): message.delete.assert_not_awaited()
        confirm=self.interaction(custom,802,message=701)
        await self.adapter.receive_review_click(confirm);await self.drain()
        for message in self.messages.values(): message.delete.assert_awaited_once()
        duplicate=self.interaction(custom,803,message=701)
        await self.adapter.receive_review_click(duplicate);await self.drain()
        for message in self.messages.values(): message.delete.assert_awaited_once()

    async def test_dismiss_button_records_only_and_closes_queue(self):
        from liberdus_moderator.staff_review import pending_page
        incident=self.pattern()
        self.adapter.live.save_review_prompt('700','20',(incident['id'],1))
        click=self.interaction('liberdus:action:v1:dismiss')
        await self.adapter.receive_review_click(click);await self.drain()
        self.assertEqual(pending_page(self.adapter.live.engine)['total'],0)
        self.member.timeout.assert_not_awaited()
        for message in self.messages.values(): message.delete.assert_not_awaited()
