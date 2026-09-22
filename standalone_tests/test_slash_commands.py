"""Slash registration and authenticated runtime flows with real SDK cards."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import discord
import test_runtime as runtime

from liberdus_moderator.commands import CommandRequest
from liberdus_moderator.configure_jev import policy_text
from liberdus_moderator.instance import load_policy
from liberdus_moderator.secure_files import SetupError, write_private
from liberdus_moderator.slash_commands import COMMANDS, SETTINGS, canonical, definitions, parse, sync_commands
from liberdus_moderator.standalone import StandaloneService, serve


def payload(name, values=None, *, config=False):
    specs = (SETTINGS if config else COMMANDS)[name][1]
    branch = dict(type=1, name=name, options=[dict(type=field["type"], name=field["name"], value=values[field["name"]])
                 for field in specs if values and field["name"] in values])
    return dict(name="mod", type=1, options=[dict(name="config", type=2, options=[branch]) if config else branch])


def interaction(name, values=None, *, config=False, identity=700, channel=20, user=98):
    return SimpleNamespace(id=identity, type=discord.InteractionType.application_command, guild_id=1,
        channel_id=channel, user=SimpleNamespace(id=user, bot=False), data=payload(name, values, config=config),
        message=None, response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        edit_original_response=AsyncMock(return_value=SimpleNamespace(id=identity + 1000)))


def text(click):
    call = click.edit_original_response.call_args or click.response.send_message.call_args
    if call is None:
        return ""
    return "\n".join(item.content for item in call.kwargs["view"].walk_children()
                     if isinstance(item, discord.ui.TextDisplay))


class SchemaTests(unittest.TestCase):
    def test_every_menu_entry_parses_typed_options_and_fits_discord_limits(self):
        definition = definitions()[0]
        self.assertLessEqual(len(definition["options"]), 25)
        for config, catalog in ((False, COMMANDS), (True, SETTINGS)):
            for name, (description, specs) in catalog.items():
                with self.subTest(name=name, config=config):
                    self.assertLessEqual(len(description), 100)
                    values = {}
                    for spec in specs:
                        if spec["name"] == "id":
                            continue
                        self.assertLessEqual(len(spec["description"]), 100)
                        values[spec["name"]] = (spec["choices"][0]["value"] if spec.get("choices") else
                            "a" * 32 if spec["name"] == "incident" else
                            spec.get("min_value", 1) if spec["type"] in (4, 10) else "10")
                    self.assertEqual(parse(payload(name, values, config=config)),
                                     ("config" if config else None, name, values))

    def test_raw_ids_support_removing_deleted_targets_without_allowing_ambiguous_input(self):
        self.assertEqual(parse(payload("alert-role", {"action": "set", "id": "5"}, config=True)),
                         ("config", "alert-role", {"action": "set", "role": "5"}))
        self.assertEqual(parse(payload("alert-role", {"action": "off"}, config=True)),
                         ("config", "alert-role", {"action": "off"}))
        with self.assertRaises(SetupError):
            parse(payload("alert-role", {"action": "off", "role": "5"}, config=True))
        self.assertEqual(parse(payload("monitor", {"action": "remove", "id": "123"}, config=True)),
                         ("config", "monitor", {"action": "remove", "channel": "123"}))
        self.assertEqual(parse(payload("operator", {"action": "remove", "id": "123"}, config=True)),
                         ("config", "operator", {"action": "remove", "user": "123"}))
        for values in ({"action": "add"}, {"action": "add", "id": "123", "channel": "123"}):
            with self.assertRaises(SetupError):
                parse(payload("monitor", values, config=True))

    def test_unrecognized_malformed_and_secret_settings_are_rejected(self):
        cases = [dict(name="hermes", type=1), payload("status", {})]
        cases[1]["options"][0]["options"] = [{"name": "guild_id", "type": 3, "value": "2"}]
        invalid = payload("pending", {"page": True})
        cases += [invalid, payload("delete", {"incident": "not-an-id", "revision": 1})]
        secret = payload("show", config=True)
        secret["options"][0]["options"][0]["name"] = "token"
        cases.append(secret)
        duplicate = payload("pending", {"page": 1})
        duplicate["options"][0]["options"] *= 2
        cases.append(duplicate)
        for case in cases:
            with self.subTest(case=case), self.assertRaises(SetupError):
                parse(case)


class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    def client(self):
        return SimpleNamespace(user=SimpleNamespace(id=99), application_id=99, http=SimpleNamespace(
            get_guild_commands=AsyncMock(return_value=[]), get_global_commands=AsyncMock(return_value=[{"name": "hermes"}]),
            bulk_upsert_guild_commands=AsyncMock(), bulk_upsert_global_commands=AsyncMock()))

    async def test_registers_only_own_guild_then_clears_legacy_global_menu(self):
        client = self.client()
        calls = []
        client.http.bulk_upsert_guild_commands.side_effect = lambda *args, **kwargs: calls.append("guild")
        client.http.bulk_upsert_global_commands.side_effect = lambda *args, **kwargs: calls.append("global")
        await sync_commands(client, SimpleNamespace(bot_user_id="99", guild_id="1"))
        self.assertEqual(calls, ["guild", "global"])
        client.http.bulk_upsert_guild_commands.assert_awaited_once_with(99, 1, payload=definitions())
        client.http.bulk_upsert_global_commands.assert_awaited_once_with(99, payload=[])

    async def test_matching_schema_avoids_writes_and_failed_publish_keeps_legacy_menu(self):
        client = self.client()
        existing = deepcopy(definitions())
        existing[0].update(id="123", application_id="99", guild_id="1", version="400", default_member_permissions=None)
        client.http.get_guild_commands.return_value = existing
        client.http.get_global_commands.return_value = []
        await sync_commands(client, SimpleNamespace(bot_user_id="99", guild_id="1"))
        client.http.bulk_upsert_guild_commands.assert_not_awaited()
        client.http.bulk_upsert_global_commands.assert_not_awaited()
        client.http.get_guild_commands.return_value = []
        client.http.bulk_upsert_guild_commands.side_effect = OSError("synthetic")
        client.http.get_global_commands.reset_mock()
        with self.assertRaises(OSError):
            await sync_commands(client, SimpleNamespace(bot_user_id="99", guild_id="1"))
        client.http.get_global_commands.assert_not_awaited()

    async def test_wrong_identity_never_changes_any_registration(self):
        client = self.client()
        with self.assertRaises(SetupError):
            await sync_commands(client, SimpleNamespace(bot_user_id="98", guild_id="1"))
        client.http.get_guild_commands.assert_not_awaited()

    async def test_pinned_sdk_builds_expected_routes_and_accepts_menu_structure(self):
        from discord.http import HTTPClient
        from discord.app_commands import AppCommand
        from unittest.mock import Mock
        client = self.client()
        client.http = HTTPClient(asyncio.get_running_loop())
        client.http.request = AsyncMock(side_effect=[[], [], [{"name": "old"}], []])
        await sync_commands(client, SimpleNamespace(bot_user_id="99", guild_id="1"))
        calls = client.http.request.call_args_list
        self.assertEqual([call.args[0].method for call in calls], ["GET", "PUT", "GET", "PUT"])
        self.assertTrue(all("/applications/99/" in call.args[0].url for call in calls))
        self.assertEqual(calls[1].kwargs["json"], definitions())
        self.assertEqual(calls[3].kwargs["json"], [])
        parsed = AppCommand(data={**definitions()[0], "id": "100", "application_id": "99", "version": "1"}, state=Mock())
        self.assertEqual(parsed.name, "mod")
        self.assertEqual(len(parsed.options), len(COMMANDS) + 1)


class SlashRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await runtime.RuntimeTests.asyncSetUp(self)
        self.assertTrue(await self.service.connect())

    async def asyncTearDown(self):
        await self.service.disconnect()

    async def dispatch(self, click):
        self.service.next_command_at = 0
        self.assertTrue(await self.service.receive_slash_command(click))
        await asyncio.wait_for(self.service.queue.join(), timeout=4)

    async def test_review_ping_allows_only_selected_role_and_survives_failed_delivery_without_retry(self):
        await self.service.disconnect()
        policy = load_policy(self.home)
        policy = replace(policy, rules=replace(policy.rules, review_alert_role_id="5"))
        write_private(self.home / "moderation.toml", policy_text(policy), replace=True)
        self.service = StandaloneService(self.home)
        self.service.make_client = lambda: runtime.fake_client(self.service)
        self.assertTrue(await self.service.connect())
        channel = self.service.client.channels[20]
        role = SimpleNamespace(id=5, mentionable=True)
        channel.guild.get_role = lambda identity: role if identity == 5 else None
        prior = channel.permissions_for.side_effect
        channel.permissions_for.side_effect = lambda member: (SimpleNamespace(view_channel=True) if member is role else prior(member))
        channel.send.side_effect = OSError('synthetic uncertain send')
        from liberdus_moderator.review_display import ReviewMessage
        review = ReviewMessage('Review with @everyone <@98> <@&6>', 'Staff assessment', 'Actions', 'Reference')
        with self.assertRaises(OSError):
            await self.service.emit('20', review, 'first', reviewable=True, alert_role='5')
        first = channel.send.call_args.kwargs
        self.assertFalse(first['silent'])
        self.assertEqual(first['allowed_mentions'].to_dict(), {'parse': [], 'roles': [5]})
        self.assertIn('<@&5>', str(first['view'].to_components()))
        channel.send.side_effect = None
        await self.service.emit('20', review, 'second', reviewable=True, alert_role='5')
        self.assertTrue(channel.send.call_args.kwargs['silent'])
        self.assertEqual(channel.send.call_args.kwargs['allowed_mentions'].to_dict()['parse'], [])
        await self.service.emit('20', 'Reopened review', 'third', reviewable=True)
        self.assertTrue(channel.send.call_args.kwargs['silent'])

    async def test_status_help_and_pause_resume_work_without_message_prefix(self):
        for index, name in enumerate(("help", "status", "pause", "resume")):
            click = interaction(name, identity=700 + index)
            await self.dispatch(click)
            self.assertTrue(text(click))
            if name == "help":
                self.assertIn("/mod config", text(click))
            if name == "pause":
                self.assertTrue(self.service.store.get_setting("paused"))
                self.assertEqual(self.service.stop_replies, {})
        self.assertFalse(self.service.store.get_setting("paused"))

    async def test_public_channel_unlisted_user_and_changed_policy_cannot_mutate(self):
        for click in (interaction("pause", channel=10), interaction("pause", user=97)):
            await self.dispatch(click)
            self.assertFalse(self.service.store.get_setting("paused"))
            self.assertIn("authorized moderator", text(click))
        policy = load_policy(self.home)
        write_private(self.home / "moderation.toml", policy_text(replace(policy, policy_version="external-edit")), replace=True)
        await self.dispatch(interaction("pause"))
        self.assertFalse(self.service.store.get_setting("paused"))

    async def test_incident_view_uses_public_staff_card_and_durable_review_buttons(self):
        for identity in range(100, 104):
            self.service.receive(runtime.RuntimeTests.message(self, identity))
        await asyncio.wait_for(self.service.queue.join(), timeout=3)
        incident = self.service.store.incidents()[0]
        click = interaction("incident", {"incident": incident["id"]})
        await self.dispatch(click)
        click.response.defer.assert_awaited_once_with(ephemeral=False, thinking=True)
        view = click.edit_original_response.call_args.kwargs["view"]
        self.assertTrue(any(isinstance(item, discord.ui.Button) for item in view.walk_children()))
        row = self.service.store.db.execute("SELECT * FROM review_prompt_links WHERE message_id='1700'").fetchone()
        self.assertEqual(row["incident_id"], incident["id"])

    async def test_deletion_still_uses_fresh_preview_and_bound_confirmation(self):
        await self.service.disconnect()
        policy = load_policy(self.home)
        write_private(self.home / "moderation.toml", policy_text(replace(policy, actions_enabled=True, allow_public_deletion=True)), replace=True)
        self.service = StandaloneService(self.home)
        self.service.make_client = lambda: runtime.fake_client(self.service)
        self.assertTrue(await self.service.connect())
        self.service.store.set_setting("deletion_enabled", True)
        messages = [runtime.RuntimeTests.message(self, identity) for identity in range(100, 104)]
        for message in messages:
            self.service.receive(message)
        await asyncio.wait_for(self.service.queue.join(), timeout=3)
        incident = self.service.store.incidents()[0]
        self.service.client.channels[10].fetch_message = AsyncMock(return_value=messages[0])
        click = interaction("delete", {"incident": incident["id"], "revision": incident["revision"], "message": "100"})
        await self.dispatch(click)
        self.assertIn("Confirm message deletion", text(click))
        view = click.edit_original_response.call_args.kwargs["view"]
        self.assertTrue(any(isinstance(item, discord.ui.Button) and "Confirm" in item.label for item in view.walk_children()))
        self.service.client.channels[10].fetch_message.assert_awaited()

    async def test_pause_supersedes_older_queued_slash_resume(self):
        older = interaction("resume", identity=700)
        self.service.deferred_interactions[id(older)] = (older, True)
        request = CommandRequest("1", "20", "98", "resume")
        self.service.queue.put_nowait((self.service.generation, "slash_command", (older, request, asyncio.get_running_loop().time() + 30)))
        await self.dispatch(interaction("pause", identity=701))
        if self.service.notice_tasks:
            await asyncio.gather(*list(self.service.notice_tasks))
        self.assertTrue(self.service.store.get_setting("paused"))
        self.assertIn("cancelled", text(older))
        self.assertIsNotNone(self.service.store.db.execute("SELECT 1 FROM command_receipts WHERE message_id='700'").fetchone())

    async def test_ack_failure_or_generation_change_does_not_execute_command(self):
        click = interaction("resume")
        self.service.store.set_setting("paused", True)
        click.response.defer.side_effect = TimeoutError()
        await self.dispatch(click)
        self.assertTrue(self.service.store.get_setting("paused"))
        changed = interaction("resume", identity=701)
        changed.response.defer.side_effect = lambda **kwargs: self.service.coverage_gap("disconnect")
        await self.dispatch(changed)
        self.assertTrue(self.service.store.get_setting("paused"))
        self.assertIn("changed", text(changed))

    async def test_configuration_uses_fresh_admin_check_and_preserves_state_through_reload(self):
        guild = {"id": "1", "owner_id": "98", "roles": [
            {"id": "1", "permissions": str((1 << 10) | (1 << 11) | (1 << 16))},
            {"id": "5", "permissions": str((1 << 10) | (1 << 11) | (1 << 16))}]}
        staff_overwrites = [{"id": "1", "type": 0, "deny": str(1 << 10), "allow": "0"},
                            {"id": "5", "type": 0, "deny": "0", "allow": str(1 << 10)}]
        metadata = {"/users/@me": {"id": "99", "bot": True}, "/guilds/1": guild,
            "/guilds/1/members/99": {"user": {"id": "99", "bot": True}, "roles": ["5"]},
            "/guilds/1/members/98": {"user": {"id": "98", "bot": False}, "roles": []},
            "/guilds/1/members/97": {"user": {"id": "97", "bot": False}, "roles": ["5"]},
            "/guilds/1/channels": [
                {"id": "10", "type": 0, "name": "general", "parent_id": None, "permission_overwrites": []},
                {"id": "20", "type": 0, "name": "staff", "parent_id": None, "permission_overwrites": staff_overwrites}]}
        self.service.store.set_setting("paused", True)
        self.service.store.set_setting("shared_total_calls", 71)
        with patch("liberdus_moderator.preflight.DiscordReader", return_value=lambda path: deepcopy(metadata[path])):
            click = interaction("operator", {"action": "add", "user": "97"}, config=True)
            await self.dispatch(click)
        self.assertIn("Configuration saved", text(click))
        self.assertTrue(self.service.finished.is_set())
        self.assertFalse(self.service.in_scope(1, 10))
        self.assertEqual(load_policy(self.home).operator_user_ids, ("98", "97"))
        await self.service.disconnect()
        self.service = StandaloneService(self.home)
        self.service.make_client = lambda: runtime.fake_client(self.service)
        self.assertTrue(await self.service.connect())
        self.assertTrue(self.service.store.get_setting("paused"))
        self.assertEqual(self.service.store.get_setting("shared_total_calls"), 71)
        self.assertEqual(self.service.policy.operator_user_ids, ("98", "97"))

    async def test_runtime_registration_failure_keeps_moderation_available(self):
        await self.service.disconnect()
        self.service = StandaloneService(self.home)
        client = runtime.fake_client(self.service)
        client.http.get_guild_commands.side_effect = OSError("secret-must-not-be-logged")
        self.service.make_client = lambda: client
        self.assertTrue(await self.service.connect())
        self.assertEqual(self.service.store.get_setting("slash_commands_state"), "registration_failed")
        client.http.bulk_upsert_global_commands.assert_not_awaited()

    async def test_settings_validation_race_cannot_overwrite_an_external_edit(self):
        external = replace(load_policy(self.home), policy_version="external-change")
        def concurrent_edit(*args):
            write_private(self.home / "moderation.toml", policy_text(external), replace=True)
            return {"ok": True}
        with patch("liberdus_moderator.remote_settings.validate_edit", side_effect=concurrent_edit):
            click = interaction("operator", {"action": "add", "user": "97"}, config=True)
            await self.dispatch(click)
        self.assertEqual(load_policy(self.home), external)
        self.assertIn("changed during validation", text(click))
        self.assertTrue(self.service.finished.is_set())

    async def test_saved_settings_reconnect_even_when_the_reply_is_lost(self):
        with patch("liberdus_moderator.remote_settings.validate_edit", return_value={"ok": True}):
            click = interaction("operator", {"action": "add", "user": "97"}, config=True)
            click.edit_original_response.side_effect = OSError("synthetic-secret-error")
            await self.dispatch(click)
        self.assertEqual(load_policy(self.home).operator_user_ids, ("98", "97"))
        self.assertTrue(self.service.finished.is_set())
        self.assertEqual(len(self.service.store.get_setting("configuration_changes_v1")), 1)


class ReloadLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_requested_reload_reopens_but_shutdown_does_not(self):
        with patch("liberdus_moderator.standalone.serve_once", new=AsyncMock(side_effect=[75, 0])) as once:
            self.assertEqual(await serve("private-home"), 0)
            self.assertEqual(once.await_count, 2)
