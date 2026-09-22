from layout_helpers import visible_text
"""Role-cache and edit races with real SDK metadata; network edges stay mocked."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

import discord
import test_actions as action_tests
import test_message_screening as screening_tests
from liberdus_moderator import actions
from liberdus_moderator.classifier import ShadowClassifier
from liberdus_moderator.engine import Engine
from liberdus_moderator.hermes_adapter import snapshot
from liberdus_moderator.live import LiveSession
from liberdus_moderator.member_roles import membership_roles, PROTECTED_TIMEOUT_PERMISSIONS
from liberdus_moderator.screening import MessageScreener


class RoleActionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = action_tests.ActionAdapterTests.asyncSetUp
    asyncTearDown = action_tests.ActionAdapterTests.asyncTearDown
    message = action_tests.ActionAdapterTests.message
    drain = action_tests.ActionAdapterTests.drain
    pattern = action_tests.ActionAdapterTests.pattern
    plan = action_tests.ActionAdapterTests.plan
    toggle = action_tests.ActionAdapterTests.toggle
    missing_roles_screening = action_tests.ActionAdapterTests.missing_roles_screening

    def sdk_member(self, ids):
        state=Mock();state.self_id=99
        state.store_user.side_effect=lambda data,**kw:discord.User(state=state,data=data)
        roles={}
        for identity,position in ((1,0),(2,1),(3,5)):
            roles[identity]=discord.Role(guild=self.guild,state=state,data=dict(id=str(identity),name=str(identity),
                permissions='0',position=position,color=0,hoist=False,mentionable=False))
        self.guild.default_role=roles[1];self.guild.get_role=roles.get
        self.me.top_role=roles[3];self.me._roles=[3]
        data=dict(user=dict(id='50',username='test',discriminator='0',avatar=None),
            roles=list(map(str,ids)),joined_at='2026-09-21T00:00:00+00:00',deaf=False,mute=False,flags=0)
        member=discord.Member(data=data,guild=self.guild,state=state)
        self.guild.fetch_member.return_value=member
        state.http.edit_member=AsyncMock(return_value=data)
        return member,state

    async def test_uncached_exempt_role_blocks_auto_delete_from_real_sdk_member(self):
        member,_=self.sdk_member((77,))
        self.assertEqual([role.id for role in member.roles],[1])
        self.assertEqual(membership_roles(member,1),('1','77'))
        message=await self.missing_roles_screening()
        message.delete.assert_not_awaited()
        self.assertIn('currently has an exempt role',' '.join(visible_text(self.channel.send.call_args).split()))
        self.assertFalse(self.adapter.store.db.execute('SELECT 1 FROM action_attempts_v1').fetchone())

    async def test_unknown_membership_field_blocks_auto_delete(self):
        del self.member._roles
        message=await self.missing_roles_screening()
        message.delete.assert_not_awaited()
        self.assertIn('roles unavailable',' '.join(visible_text(self.channel.send.call_args).split()))

    async def test_uncached_target_role_blocks_timeout_before_http(self):
        member,state=self.sdk_member((77,))
        incident=self.pattern();result=await self.adapter.perform_action(self.plan(incident,'timeout'))
        self.assertIn('role privileges unavailable',' '.join(result.split()))
        state.http.edit_member.assert_not_awaited()
        self.assertFalse(actions.history(self.adapter.live.engine,incident['id']))

    async def test_uncached_bot_role_blocks_timeout_before_http(self):
        _,state=self.sdk_member((2,));self.me._roles=[3,88]
        incident=self.pattern();result=await self.adapter.perform_action(self.plan(incident,'timeout'))
        self.assertIn('role privileges unavailable',' '.join(result.split()))
        state.http.edit_member.assert_not_awaited()

    async def test_complete_roles_allow_real_sdk_timeout_once(self):
        _,state=self.sdk_member((2,))
        incident=self.pattern();payload=self.plan(incident,'timeout')
        before=datetime.now(timezone.utc)
        result=await self.adapter.perform_action(payload)
        self.assertIn('Timeout: done',result)
        state.http.edit_member.assert_awaited_once()
        call=state.http.edit_member.call_args
        self.assertEqual(call.args,(1,50))
        until=datetime.fromisoformat(call.kwargs['communication_disabled_until'])
        self.assertTrue(before+timedelta(seconds=599)<=until<=datetime.now(timezone.utc)+timedelta(seconds=601))
        self.assertIn('staff 98',call.kwargs['reason'])
        await self.adapter.perform_action(payload)
        state.http.edit_member.assert_awaited_once()

    async def test_each_protected_permission_blocks_independently(self):
        incident=self.pattern();payload=self.plan(incident,'timeout')
        for name in PROTECTED_TIMEOUT_PERMISSIONS:
            with self.subTest(permission=name):
                setattr(self.member.guild_permissions,name,True)
                result=await self.adapter.perform_action(payload)
                setattr(self.member.guild_permissions,name,False)
                self.assertIn('Protected member',' '.join(result.split()))
                self.member.timeout.assert_not_awaited()
                self.assertFalse(actions.history(self.adapter.live.engine,incident['id']))

    async def test_invalid_or_missing_role_data_blocks_timeout(self):
        incident=self.pattern();payload=self.plan(incident,'timeout')
        for raw in (None,[True],['0'],['77','77'],['not-id']):
            with self.subTest(raw=raw):
                self.member._roles=raw
                result=await self.adapter.perform_action(payload)
                self.assertIn('role privileges unavailable',' '.join(result.split()))
                self.member.timeout.assert_not_awaited()
        self.member._roles=[]
        self.guild.get_role=lambda identity:SimpleNamespace(id=identity,guild=SimpleNamespace(id=2))
        result=await self.adapter.perform_action(payload)
        self.assertIn('role privileges unavailable',' '.join(result.split()))
        self.member.timeout.assert_not_awaited()


class EditRoleTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = screening_tests.ScreeningAdapterTests.asyncTearDown
    message = screening_tests.ScreeningAdapterTests.message
    drain = screening_tests.ScreeningAdapterTests.drain
    screened = screening_tests.ScreeningAdapterTests.screened

    async def asyncSetUp(self):
        await screening_tests.ScreeningAdapterTests.asyncSetUp(self)
        await self.adapter.classifier.close()
        self.adapter.policy=replace(self.adapter.policy,classifier=replace(self.adapter.policy.classifier,exempt_role_ids=('77',)))
        self.adapter.live=LiveSession(Engine(self.adapter.policy,self.adapter.store))
        self.adapter.classifier=MessageScreener(self.adapter.live.engine,self.provider,
            active=lambda:self.adapter.online,on_result=lambda job:self.adapter.enqueue('screen_result',job))
        self.adapter.classifier.start()
        self.guild=SimpleNamespace(id=1)
        self.member=SimpleNamespace(id=50,guild=self.guild,bot=False,_roles=[])
        self.guild.fetch_member=AsyncMock(return_value=self.member)

    async def original(self,identity=100,channel=10):
        message=self.message(identity,channel,guild=self.guild)
        message.author._roles=[77]
        self.adapter.receive(message)
        await self.screened()
        self.provider.assert_not_awaited()
        return message

    async def edited(self,original,**updates):
        edited=self.message(original.id,original.channel.id,guild=self.guild,
            content='Edited ordinary message with sufficient context.',created_at=original.created_at,
            edited_at=datetime.now(timezone.utc),**updates)
        edited.author=SimpleNamespace(id=50,bot=False)  # Real REST User shape: no member metadata.
        self.channel.fetch_message=AsyncMock(return_value=edited)
        self.adapter.edit(1,original.channel.id,original.id)
        await self.screened()
        return edited

    async def test_exempt_edit_uses_fresh_exact_membership_and_skips_provider(self):
        self.member._roles=[77]
        await self.edited(await self.original())
        self.guild.fetch_member.assert_awaited_once_with(50)
        self.provider.assert_not_awaited()
        self.assertEqual(self.adapter.live.status(True)['screening_exempt'],2)
        self.assertEqual(self.adapter.live.status(True)['screening_unchecked'],0)

    async def test_nonexempt_edit_is_screened_after_membership_fetch(self):
        await self.edited(await self.original())
        self.guild.fetch_member.assert_awaited_once_with(50)
        self.provider.assert_awaited_once()
        self.assertEqual(self.adapter.live.status(True)['screening_unchecked'],0)

    async def test_missing_edit_membership_keeps_code_rules_but_never_calls_jev(self):
        originals=[await self.original(100+i,ch) for i,ch in enumerate((10,11,12))]
        self.guild.fetch_member.side_effect=TimeoutError
        for original in originals:
            await self.edited(original)
        self.provider.assert_not_awaited()
        self.assertEqual(self.adapter.live.status(True)['screening_unchecked'],3)
        self.assertEqual(self.adapter.live.status(True)['classifier_state'],'membership_unavailable')
        self.assertTrue(any(row['rule_id']=='cross_channel_repeat' and row['status']=='open'
                            for row in self.adapter.store.incidents()))
        self.assertTrue(all(self.adapter.classifier.snapshot(str(m.id)) is None for m in originals))

    async def test_wrong_identity_guild_and_invalid_roles_skip_jev(self):
        cases=(dict(id=51),dict(guild=SimpleNamespace(id=2)),dict(bot=True),dict(_roles=[True]),dict(_roles=None))
        for index,changes in enumerate(cases):
            original=await self.original(100+index)
            member=SimpleNamespace(id=50,guild=self.guild,bot=False,_roles=[])
            member.__dict__.update(changes)
            self.guild.fetch_member.return_value=member
            await self.edited(original)
        self.provider.assert_not_awaited()
        self.assertEqual(self.adapter.live.status(True)['screening_unchecked'],len(cases))

    async def test_exemption_off_avoids_membership_lookup_for_edit(self):
        original=await self.original()
        self.adapter.store.set_setting('role_exemption_enabled',False)
        await self.edited(original)
        self.guild.fetch_member.assert_not_awaited()
        self.provider.assert_awaited_once()

    async def test_turning_exemption_on_during_message_fetch_blocks_unknown_edit(self):
        original=await self.original()
        self.adapter.store.set_setting('role_exemption_enabled',False)
        edited=self.message(guild=self.guild,content='Edited message',created_at=original.created_at,
            edited_at=datetime.now(timezone.utc))
        edited.author=SimpleNamespace(id=50,bot=False)
        async def fetch(identity):
            self.adapter.store.set_setting('role_exemption_enabled',True)
            return edited
        self.channel.fetch_message=AsyncMock(side_effect=fetch)
        self.member._roles=[77]
        self.adapter.edit(1,10,100)
        await self.screened()
        self.guild.fetch_member.assert_awaited_once()
        self.provider.assert_not_awaited()

    async def test_turning_exemption_off_during_lookup_allows_screening(self):
        original=await self.original()
        async def fetch(identity):
            self.adapter.store.set_setting('role_exemption_enabled',False)
            raise TimeoutError
        self.guild.fetch_member.side_effect=fetch
        await self.edited(original)
        self.provider.assert_awaited_once()
        self.assertEqual(self.adapter.live.status(True)['screening_unchecked'],0)

    async def check_inflight_roles(self, *, cached):
        original=await self.original()
        self.adapter.store.set_setting('role_exemption_enabled',False)
        entered,release=asyncio.Event(),asyncio.Event()
        async def evaluate(payload):
            entered.set();await release.wait();return screening_tests.response()
        self.adapter.classifier.evaluator=evaluate
        edited=self.message(guild=self.guild,content='Edited message',created_at=original.created_at,
            edited_at=datetime.now(timezone.utc))
        edited.author=SimpleNamespace(id=50,bot=False)
        if cached:
            edited.author._roles=[]  # A cached nonexempt Member is still unverified for this edit.
        self.channel.fetch_message=AsyncMock(return_value=edited)
        self.adapter.edit(1,10,100)
        await self.drain();await asyncio.wait_for(entered.wait(),2)
        self.adapter.store.set_setting('role_exemption_enabled',True)
        release.set();await self.screened()
        self.assertFalse(self.adapter.store.incidents())
        self.channel.send.assert_not_awaited()
        self.assertEqual(self.adapter.store.db.execute('SELECT outcome FROM screening_attempts_v1').fetchone()[0],'stale')

    async def test_exemption_on_rejects_inflight_unknown_roles_result(self):
        await self.check_inflight_roles(cached=False)

    async def test_exemption_on_rejects_inflight_cached_roles_result(self):
        await self.check_inflight_roles(cached=True)

    async def test_unknown_shadow_evidence_is_withheld_but_code_report_remains(self):
        await self.adapter.classifier.close()
        self.adapter.policy=replace(self.adapter.policy,classifier=replace(self.adapter.policy.classifier,mode='shadow'))
        self.adapter.live=LiveSession(Engine(self.adapter.policy,self.adapter.store))
        self.adapter.classifier=ShadowClassifier(self.adapter.live.engine,self.provider)
        for i,ch in enumerate((10,11,12)):
            message=self.message(100+i,ch)
            message.author=SimpleNamespace(id=50,bot=False)
            self.adapter.process_evidence(snapshot(message))
        incident=self.adapter.store.incidents()[0]
        await self.adapter.classifier.evaluate_one(incident['id'])
        self.provider.assert_not_awaited()
        self.assertTrue(self.adapter.store.reports())
        self.adapter.store.set_setting('role_exemption_enabled',False)
        self.assertIsNotNone(self.adapter.classifier.snapshot(incident['id']))


class RawSnapshotTests(unittest.TestCase):
    def test_gateway_sdk_uncached_roles_survive_snapshot(self):
        guild=Mock(spec=discord.Guild);guild.id=1;guild.get_member.return_value=None;guild.get_role.return_value=None
        guild.default_role=SimpleNamespace(id=1)
        state=Mock();state.store_user.side_effect=lambda data,**kw:discord.User(state=state,data=data)
        channel=Mock(spec=discord.TextChannel);channel.id=10;channel.guild=guild
        payload=dict(id='1551344942083866777',type=0,content='test message',attachments=[],embeds=[],
            edited_timestamp=None,tts=False,pinned=False,mention_everyone=False,mentions=[],mention_roles=[],
            author=dict(id='50',username='test',discriminator='0',avatar=None),
            member=dict(roles=['77'],joined_at='2026-09-01T00:00:00+00:00',deaf=False,mute=False,flags=0))
        message=discord.Message(state=state,channel=channel,data=payload)
        self.assertEqual([role.id for role in message.author.roles],[1])
        self.assertEqual(snapshot(message).author_role_ids,('1','77'))

    def test_empty_membership_is_known_but_missing_metadata_is_unknown(self):
        self.assertEqual(membership_roles(SimpleNamespace(_roles=[]),1),('1',))
        with self.assertRaises(AttributeError):membership_roles(SimpleNamespace(roles=[]),1)
        for raw in (None,[True],['0'],['77','77'],['bad'],{'77'}):
            with self.subTest(raw=raw),self.assertRaises((TypeError,ValueError)):
                membership_roles(SimpleNamespace(_roles=raw),1)
