"""Public deletion reaches the SDK only within the current guarded scope; no network."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import discord

import test_actions as action_tests
from liberdus_moderator import actions
from liberdus_moderator.commands import CommandRequest, handle_command
from liberdus_moderator.engine import Engine
from liberdus_moderator.live import LiveSession


class PublicDeletionAdapterTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = action_tests.ActionAdapterTests.asyncTearDown
    drain = action_tests.ActionAdapterTests.drain
    message = action_tests.ActionAdapterTests.message
    pattern = action_tests.ActionAdapterTests.pattern
    plan = action_tests.ActionAdapterTests.plan
    toggle = action_tests.ActionAdapterTests.toggle
    sdk_message = action_tests.ActionAdapterTests.sdk_message

    async def asyncSetUp(self):
        await action_tests.ActionAdapterTests.asyncSetUp(self)
        self.adapter.policy = replace(self.adapter.policy, allow_public_monitored_channels=True,
            allow_public_deletion=True, included_category_ids=('100',), excluded_category_ids=('200',))
        self.adapter.live = LiveSession(Engine(self.adapter.policy, self.adapter.store))
        self.categories = {i: Mock(spec=discord.CategoryChannel,id=i,guild=self.guild) for i in (100,200,300)}
        for identity, channel in self.channels.items():
            channel.type = discord.ChannelType.text
            channel.category_id = 100 if identity != 20 else 300
            channel.category = self.categories[channel.category_id]
            channel.public, channel.manage, channel.admin = identity != 20, True, False
            channel.permissions_for.side_effect = lambda member,c=channel: SimpleNamespace(
                administrator=c.admin if member is self.me else False,
                view_channel=member is self.me or c.public, read_message_history=True,
                send_messages=True,manage_messages=c.manage)
        self.adapter.refresh_scope()

    async def test_public_auto_delete_reaches_real_sdk_once_and_reports_privately(self):
        await action_tests.ActionAdapterTests.test_auto_delete_through_real_sdk_method_reaches_http_once(self)
        for identity in (10,11,12): self.channels[identity].send.assert_not_awaited()
        self.member.timeout.assert_not_awaited()

    async def test_public_manual_delete_reaches_real_sdk_with_existing_confirmation_guards(self):
        await action_tests.ActionAdapterTests.test_manual_delete_through_real_sdk_method_reaches_http_once(self)
        self.member.timeout.assert_not_awaited()

    async def test_scope_permission_and_audience_changes_after_proposal_prevent_mutation(self):
        incident=self.pattern(); payload=self.plan(incident)
        channel=self.channels[10]
        for scenario in ('excluded','outside','uncategorized','private','permission','admin','type'):
            with self.subTest(scenario=scenario):
                channel.category_id=100;channel.category=self.categories[100]
                channel.public=channel.manage=True;channel.admin=False;channel.type=discord.ChannelType.text
                if scenario in ('excluded','outside','uncategorized'):
                    channel.category_id={'excluded':200,'outside':300,'uncategorized':None}[scenario]
                    channel.category=self.categories.get(channel.category_id)
                if scenario=='private': channel.public=False
                if scenario=='permission': channel.manage=False
                if scenario=='admin': channel.admin=True
                if scenario=='type': channel.type=discord.ChannelType.news
                await self.adapter.perform_action(payload)
                for message in self.messages.values(): message.delete.assert_not_awaited()
                self.assertFalse(actions.history(self.adapter.live.engine,incident['id']))

    async def test_channel_move_during_fetch_aborts_before_delete(self):
        incident=self.pattern();payload=self.plan(incident)
        async def fetch(identity):
            self.channels[10].category_id=300;self.channels[10].category=self.categories[300]
            return self.messages[identity]
        self.channels[10].fetch_message.side_effect=fetch
        await self.adapter.perform_action(payload)
        for message in self.messages.values(): message.delete.assert_not_awaited()

    async def test_timeout_remains_blocked_at_transport_even_with_stale_true_flag(self):
        incident=self.pattern();payload=self.plan(incident)
        self.adapter.store.set_setting('timeout_enabled',True)
        payload['kind']='timeout'
        await self.adapter.perform_action(payload)
        self.member.timeout.assert_not_awaited()
        self.guild.fetch_member.assert_not_awaited()
        for message in self.messages.values(): message.delete.assert_not_awaited()
        response=handle_command(self.adapter.live.engine,CommandRequest('1','20','98','timeout',arguments=('on',)))
        self.assertFalse(response['ok'])
        self.assertFalse(self.adapter.live.engine.status()['timeout_enabled'])

    async def test_off_controls_edits_and_disk_disable_still_prevent_public_deletion(self):
        await action_tests.ActionAdapterTests.test_one_edited_message_prevents_entire_batch(self)
        for message in self.messages.values(): message.delete.assert_not_awaited()
        self.adapter.classifier_active.return_value=False
        incident=self.adapter.store.incidents()[0]
        await self.adapter.perform_action(self.plan(incident))
        for message in self.messages.values(): message.delete.assert_not_awaited()


if __name__ == '__main__': unittest.main()
