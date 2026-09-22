"""Run with the verified Hermes source on PYTHONPATH and its optional dependencies.

All network edges are mocked; the real Hermes loader/config and Discord SDK are used.
"""

from layout_helpers import visible_text, buttons
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import shutil
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord
import yaml
from gateway.config import Platform, PlatformConfig, load_gateway_config
from gateway.platform_registry import platform_registry, PlatformEntry
from hermes_constants import set_hermes_home_override, reset_hermes_home_override
from hermes_cli.plugins import PluginManager, PluginManifest

from liberdus_moderator.config import Config
from liberdus_moderator.engine import Engine
from liberdus_moderator.hermes_plugin import PLATFORM, explicit_activation
from liberdus_moderator.hermes_adapter import ModerationAdapter, PilotClient
from liberdus_moderator.live import LiveSession
from liberdus_moderator.storage import Store


ROOT = Path(__file__).resolve().parents[1]


class PluginIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def profile(self, name, enabled=None):
        profile = self.root / name / "profiles/liberdus-mod"
        profile.mkdir(parents=True)
        plugin = profile / "plugins/liberdus-moderator"
        shutil.copytree(ROOT / "hermes_plugin", plugin, ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(ROOT / "liberdus_moderator", plugin / "liberdus_moderator", ignore=shutil.ignore_patterns('__pycache__'))
        config = {"plugins": {"enabled": ["liberdus-moderator"]}, "platforms": {"discord": {"enabled": False}}}
        if enabled is not None:
            config["platforms"][PLATFORM] = {"enabled": enabled}
        (profile / "config.yaml").write_text(yaml.safe_dump(config))
        return profile

    def test_real_loader_and_config_default_disabled_and_profile_isolation(self):
        disabled = self.profile("A")
        enabled = self.profile("B", True)
        for profile, expected in ((disabled, False), (enabled, True), (disabled, False)):
            with self.subTest(profile=str(profile), expected=expected):
                token = set_hermes_home_override(profile)
                try:
                    manager = PluginManager()
                    manager._load_plugin(PluginManifest(name="liberdus-moderator", kind="platform", source="user", path=str(profile / "plugins/liberdus-moderator")))
                    self.assertTrue(manager._plugins["liberdus-moderator"].enabled, manager._plugins["liberdus-moderator"].error)
                    self.assertEqual(explicit_activation(), expected)
                    # This invokes Hermes's actual YAML merge and env-enable pass.
                    config = load_gateway_config()
                    platform = config.platforms.get(Platform(PLATFORM))
                    self.assertEqual(bool(platform and platform.enabled), expected)
                    self.assertFalse(config.platforms[Platform.DISCORD].enabled)
                    entry = platform_registry.get(PLATFORM)
                    adapter = entry.adapter_factory(PlatformConfig(enabled=False))
                    self.assertFalse(adapter.__abstractmethods__)
                finally:
                    reset_hermes_home_override(token)

    def test_explicit_false_and_stock_discord_exclusion(self):
        profile = self.profile("C", False)
        token = set_hermes_home_override(profile)
        try:
            self.assertFalse(explicit_activation(PlatformConfig(enabled=True)))
            raw = yaml.safe_load((profile / "config.yaml").read_text())
            raw["platforms"][PLATFORM]["enabled"] = True
            raw["platforms"]["discord"]["enabled"] = True
            (profile / "config.yaml").write_text(yaml.safe_dump(raw))
            self.assertFalse(explicit_activation())
        finally:
            reset_hermes_home_override(token)


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        platform_registry.register(PlatformEntry(PLATFORM, "Test", lambda c: None, lambda: True))
        self.adapter = ModerationAdapter(PlatformConfig(enabled=False))
        self.adapter.policy = Config("1", "99", ("10", "11", "12"), ("20",), ("98",))
        self.adapter.store = Store(":memory:")
        self.adapter.live = LiveSession(Engine(self.adapter.policy, self.adapter.store))
        self.adapter.online = True
        self.adapter.policy_current = Mock(return_value=True)
        self.channel = Mock(spec=discord.TextChannel)
        self.channel.guild = SimpleNamespace(id=1, me=object(), default_role=object())
        self.channel.permissions_for.side_effect = lambda member: SimpleNamespace(
            administrator=False, view_channel=member is self.channel.guild.me,
            read_message_history=True, send_messages=True)
        self.channel.send = AsyncMock(return_value=SimpleNamespace(id=700))
        self.adapter.client = Mock()
        self.adapter.client.get_channel.return_value = self.channel
        self.adapter.client.close = AsyncMock()
        self.adapter.client.user = SimpleNamespace(id=99)
        self.adapter._mark_disconnected = Mock()
        self.adapter._mark_connected = Mock()
        self.adapter._set_fatal_error = Mock()
        self.adapter.worker = asyncio.create_task(self.adapter.run_worker())

    async def asyncTearDown(self):
        await self.adapter.disconnect()

    def message(self, identity=100, channel=10, author=50, content="Repeated test message with sufficient length.", **updates):
        values = dict(id=identity, channel=SimpleNamespace(id=channel), guild=SimpleNamespace(id=1),
                      author=SimpleNamespace(id=author, bot=False, _roles=[]), content=content,
                      created_at=datetime.now(timezone.utc), edited_at=None, webhook_id=None, attachments=[])
        values.update(updates)
        return SimpleNamespace(**values)

    async def drain(self):
        await asyncio.wait_for(self.adapter.queue.join(), timeout=3)

    async def test_ordinary_messages_and_mentions_report_privately_without_agent_dispatch(self):
        self.adapter.handle_message = AsyncMock(side_effect=AssertionError("No AI dispatch permitted"))
        for i, channel in enumerate((10,11,12)):
            self.adapter.receive(self.message(100+i, channel, content="Repeated content mentioning <@123> for pilot testing."))
        await self.drain()
        self.channel.send.assert_awaited_once()
        args, kwargs = self.channel.send.call_args
        self.assertNotIn("<@123>", visible_text(self.channel.send.call_args))
        self.assertEqual(kwargs["allowed_mentions"].to_dict()["parse"], [])
        self.assertTrue(kwargs["silent"])
        self.assertEqual(self.adapter.store.db.execute("SELECT status FROM reports").fetchone()[0], "sent")
        self.adapter.handle_message.assert_not_called()

    async def test_dm_wrong_scope_bots_webhooks_and_private_unauthorized_silent(self):
        for message in (self.message(guild=None), self.message(guild=SimpleNamespace(id=2)),
                        self.message(channel=999), self.message(author=99, author_override=True),
                        self.message(webhook_id=88), self.message(channel=20, content="!mod pause")):
            self.adapter.receive(message)
        await self.drain()
        # Own bot identity is also excluded by the core, independently of bot metadata.
        self.assertEqual(self.adapter.live.status(True)["message_count"], 0)
        self.assertFalse(self.adapter.live.status(True)["paused"])
        self.channel.send.assert_not_awaited()

    async def test_private_owner_command_no_public_or_generic_send(self):
        self.adapter.receive(self.message(300,20,98,"!mod pause"))
        await self.drain()
        self.assertTrue(self.adapter.live.status(True)["paused"])
        self.channel.send.assert_awaited_once()
        result = await self.adapter.send("10", "forbidden")
        self.assertFalse(result.success)
        self.assertIsNone(await self.adapter.handle_message(object()))

    async def test_private_selftest_reports_without_live_evidence_or_agent_dispatch(self):
        self.adapter.handle_message = AsyncMock(side_effect=AssertionError("No AI dispatch permitted"))
        before = self.adapter.live.engine.status()
        self.adapter.receive(self.message(301, 20, 98, "!mod selftest"))
        await self.drain()
        self.assertEqual(self.adapter.live.engine.status(), before)
        self.channel.send.assert_awaited_once()
        args, kwargs = self.channel.send.call_args
        self.assertIn("9/9 passed", visible_text(self.channel.send.call_args))
        self.assertIn("Live Discord events, permissions and actual restart: not tested", " ".join(visible_text(self.channel.send.call_args).split()))
        self.assertEqual(kwargs["allowed_mentions"].to_dict()["parse"], [])
        self.adapter.handle_message.assert_not_called()

    async def test_private_incident_displays_saved_jev_without_secret_or_provider_access(self):
        from dataclasses import replace
        from liberdus_moderator.classifier import ShadowClassifier, RUBRIC
        from liberdus_moderator.config import ClassifierSettings
        self.adapter.policy = replace(self.adapter.policy, schema_version=2, ai_enabled=True,
            classifier=ClassifierSettings(mode="shadow", max_daily_calls=10, max_total_calls=10,
                daily_budget_microusd=50000, total_budget_microusd=50000))
        self.adapter.live = LiveSession(Engine(self.adapter.policy, self.adapter.store))
        provider = AsyncMock(return_value={"model": "jev-1.13.0", "answers": {"context": {
            "type": "choice", "choice": "quoted_warning", "confidence": 0.8,
            "probabilities": {key: 0.8 if key == "quoted_warning" else 0.05 for key in RUBRIC["criteria"]}}},
            "usage": {"input_tokens": 500, "output_tokens": 40}})
        worker = ShadowClassifier(self.adapter.live.engine, provider)
        for i, channel in enumerate((10, 11, 12)):
            self.adapter.receive(self.message(100+i, channel))
        await self.drain()
        identity = self.adapter.store.incidents()[0]["id"]
        await worker.evaluate_one(identity)
        self.channel.send.reset_mock()
        before = self.adapter.live.engine.status()
        with patch("liberdus_moderator.hermes_adapter.current_secret_scope", side_effect=AssertionError("no secret read")), \
                patch("liberdus_moderator.jev.evaluate", side_effect=AssertionError("no provider call")):
            self.adapter.receive(self.message(302, 20, 98, "!mod incident " + identity))
            await self.drain()
        self.assertEqual(self.adapter.live.engine.status(), before)
        self.channel.send.assert_awaited_once()
        args, kwargs = self.channel.send.call_args
        self.assertIn("**Quoted warning**", visible_text(self.channel.send.call_args))
        self.assertNotIn("```", visible_text(self.channel.send.call_args))
        self.assertIn("no new AI call", visible_text(self.channel.send.call_args))
        self.assertIn("Repeated test message", visible_text(self.channel.send.call_args))
        self.assertIn("Open message 1", visible_text(self.channel.send.call_args))
        self.assertLess(len(visible_text(self.channel.send.call_args)), 1900)
        self.assertEqual(kwargs["allowed_mentions"].to_dict()["parse"], [])
        provider.assert_awaited_once()
        await worker.close()

    async def test_private_report_reply_records_human_label_and_lookup_shows_evidence(self):
        from liberdus_moderator.moderator_review import TABLE
        for index, channel in enumerate((10, 11, 12)):
            self.adapter.receive(self.message(100+index, channel,
                content="Repeated saved message mentioning @everyone and a quoted offer."))
        await self.drain()
        identity = self.adapter.store.incidents()[0]["id"]
        reference = discord.MessageReference(message_id=700, channel_id=20, guild_id=1)
        reply = self.message(303, 20, 98, "!mod review not-promotion", reference=reference)
        self.channel.send.reset_mock()
        self.channel.send.return_value = SimpleNamespace(id=701)
        with patch("liberdus_moderator.hermes_adapter.current_secret_scope", side_effect=AssertionError("no key")), \
             patch("liberdus_moderator.jev.evaluate", side_effect=AssertionError("no AI")):
            self.adapter.receive(reply)
            await self.drain()
            self.assertIn("Moderator review saved", visible_text(self.channel.send.call_args))
            self.adapter.next_command_at = 0
            self.adapter.receive(reply)
            await self.drain()
            self.assertEqual(self.channel.send.await_count, 1)
            self.adapter.next_command_at = 0
            self.adapter.receive(self.message(304, 20, 98, "!mod explain " + identity))
            await self.drain()
        text = visible_text(self.channel.send.call_args)
        self.assertIn("Not promotion", text)
        self.assertIn("Legacy content label", text)
        self.assertIn("Repeated saved message", text)
        self.assertNotIn("@everyone", text)
        self.assertIn("https://discord.com/channels/1/10/100", text)
        self.assertEqual(self.channel.send.call_args.kwargs["allowed_mentions"].to_dict()["parse"], [])
        self.assertTrue(self.channel.send.call_args.kwargs["suppress_embeds"])
        self.assertEqual(self.adapter.store.db.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0], 1)
        self.assertEqual(self.adapter.live.status(True)["ai_attempts"], 0)

    async def test_review_reply_rejects_unauthorized_wrong_reference_and_preserves_saved_jev(self):
        from dataclasses import replace
        from liberdus_moderator.classifier import ShadowClassifier, RUBRIC
        from liberdus_moderator.config import ClassifierSettings
        self.adapter.policy = replace(self.adapter.policy, schema_version=2, ai_enabled=True,
            classifier=ClassifierSettings(mode="shadow", max_daily_calls=10, max_total_calls=10,
                daily_budget_microusd=50000, total_budget_microusd=50000))
        self.adapter.live = LiveSession(Engine(self.adapter.policy, self.adapter.store))
        provider = AsyncMock(return_value={"model": "jev-1.13.0", "answers": {"context": {
            "type": "choice", "choice": "promotion", "confidence": 0.8,
            "probabilities": {key: 0.8 if key == "promotion" else 0.05 for key in RUBRIC["criteria"]}}},
            "usage": {"input_tokens": 500, "output_tokens": 40}})
        worker = ShadowClassifier(self.adapter.live.engine, provider)
        for i, channel in enumerate((10, 11, 12)):
            self.adapter.receive(self.message(100+i, channel))
        await self.drain()
        identity = self.adapter.store.incidents()[0]["id"]
        await worker.evaluate_one(identity)
        before = [tuple(row) for row in self.adapter.store.db.execute("SELECT * FROM classifier_attempts")]
        self.channel.send.reset_mock()
        for user, channel, guild, kind in ((50, 20, 1, discord.MessageReferenceType.default),
                                           (98, 10, 1, discord.MessageReferenceType.default),
                                           (98, 20, 2, discord.MessageReferenceType.default),
                                           (98, 20, 1, discord.MessageReferenceType.forward)):
            reference = discord.MessageReference(message_id=700, channel_id=channel, guild_id=guild, type=kind)
            self.adapter.receive(self.message(305, 20, user, "!mod review not-promotion", reference=reference))
        await self.drain()
        self.channel.send.assert_not_awaited()
        with patch("liberdus_moderator.jev.evaluate", side_effect=AssertionError("no extra AI")):
            self.adapter.receive(self.message(306, 20, 98, "!mod review " + identity + " 1 not-promotion"))
            await self.drain()
        self.assertIn("Not promotion", visible_text(self.channel.send.call_args))
        self.assertEqual(before, [tuple(row) for row in self.adapter.store.db.execute("SELECT * FROM classifier_attempts")])
        self.assertEqual(self.adapter.live.status(True)["ai_attempts"], 1)
        provider.assert_awaited_once()
        await worker.close()

    def interaction(self, identity=800, label="needs-attention", **updates):
        values = dict(id=identity, type=discord.InteractionType.component,
            data={"component_type": 2, "custom_id": "liberdus:assess:v1:" + label},
            guild_id=1, channel_id=20, user=SimpleNamespace(id=98, bot=False),
            message=SimpleNamespace(id=700, author=SimpleNamespace(id=99), channel=SimpleNamespace(id=20)),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            edit_original_response=AsyncMock(return_value=SimpleNamespace(id=701)))
        values.update(updates)
        return SimpleNamespace(**values)

    async def create_report(self):
        for index, channel in enumerate((10, 11, 12)):
            self.adapter.receive(self.message(100+index, channel))
        await self.drain()
        return self.adapter.store.incidents()[0]["id"]

    def reviews(self):
        from liberdus_moderator.staff_review import TABLE
        if not self.adapter.store.db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)).fetchone():
            return []
        return list(self.adapter.store.db.execute(f"SELECT * FROM {TABLE} ORDER BY sequence"))

    async def test_three_buttons_store_labels_ephemerally_without_ai_or_rule_changes(self):
        await self.create_report()
        view = self.channel.send.call_args.kwargs["view"]
        self.assertEqual([child.label for child in buttons(view)[:3]], ["Needs attention", "Looks okay", "Unsure"])
        self.assertTrue(view.is_persistent())
        self.assertTrue(view.is_finished())  # No unbounded per-message SDK callback cache.
        self.assertEqual(len(view.to_components()[1]["components"][1]["components"]), 3)
        before = [tuple(row) for row in self.adapter.store.db.execute("SELECT * FROM incident_versions")]
        with patch("liberdus_moderator.jev.evaluate", side_effect=AssertionError("No provider call")), \
             patch("liberdus_moderator.hermes_adapter.current_secret_scope", side_effect=AssertionError("No key")):
            for index, label in enumerate(("needs-attention", "looks-okay", "unsure")):
                self.adapter.next_command_at = 0
                interaction = self.interaction(800+index, label)
                await self.adapter.receive_review_click(interaction)
                await self.drain()
                interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
                self.assertIn("Staff assessment saved", visible_text(interaction.edit_original_response.call_args))
                self.assertTrue(interaction.response.defer.call_args.kwargs["ephemeral"])
                self.assertEqual(interaction.edit_original_response.call_args.kwargs["allowed_mentions"].to_dict()["parse"], [])
        self.assertEqual([row["label"] for row in self.reviews()], ["needs_attention", "looks_okay", "unsure"])
        self.assertEqual(before, [tuple(row) for row in self.adapter.store.db.execute("SELECT * FROM incident_versions")])
        self.channel.send.assert_awaited_once()  # Click acknowledgement is private to the clicker.
        self.assertEqual(self.adapter.live.status(True)["ai_attempts"], 0)

    async def test_duplicate_click_and_confirmation_failure_never_replay_review(self):
        await self.create_report()
        first = self.interaction()
        first.edit_original_response.side_effect = TimeoutError()
        await self.adapter.receive_review_click(first)
        await self.drain()
        self.adapter.next_command_at = 0
        duplicate = self.interaction()
        await self.adapter.receive_review_click(duplicate)
        await self.drain()
        self.assertEqual(len(self.reviews()), 1)
        self.assertIn("already processed", " ".join(visible_text(duplicate.edit_original_response.call_args).split()))

    async def test_button_authentication_and_known_message_binding_are_required(self):
        await self.create_report()
        for changes in ({"user": SimpleNamespace(id=50, bot=False)},
                        {"user": SimpleNamespace(id=98, bot=True)},
                        {"guild_id": 2}, {"channel_id": 10},
                        {"message": self.message(700, 20, 50)},
                        {"message": self.message(700, 10, 99)},
                        {"message": self.message(999, 20, 99)}, {"message": None}):
            with self.subTest(changes=changes):
                interaction = self.interaction(**changes)
                await self.adapter.receive_review_click(interaction)
                await self.drain()
                interaction.response.defer.assert_not_awaited()
                interaction.response.send_message.assert_awaited_once()
                self.assertTrue(interaction.response.send_message.call_args.kwargs["ephemeral"])
        foreign = self.interaction(data={"component_type": 2, "custom_id": "liberdus:review:v1:ban"})
        await self.adapter.receive_review_click(foreign)
        foreign.response.defer.assert_not_awaited()
        self.assertEqual(self.reviews(), [])

    async def test_no_review_when_acknowledgement_fails_or_connection_changes(self):
        await self.create_report()
        failed = self.interaction()
        failed.response.defer.side_effect = TimeoutError()
        await self.adapter.receive_review_click(failed)
        await self.drain()
        changed = self.interaction(801)
        async def reconnect(**kwargs):
            self.adapter.coverage_gap("reconnect")
        changed.response.defer.side_effect = reconnect
        await self.adapter.receive_review_click(changed)
        await self.drain()
        self.assertEqual(self.reviews(), [])
        self.assertIn("Connection changed", visible_text(changed.edit_original_response.call_args))

    async def test_review_rechecks_private_channel_after_deferral(self):
        await self.create_report()
        interaction = self.interaction()
        async def make_public(**kwargs):
            self.channel.permissions_for.side_effect = lambda member: SimpleNamespace(
                administrator=False, view_channel=True, read_message_history=True, send_messages=True)
        interaction.response.defer.side_effect = make_public
        await self.adapter.receive_review_click(interaction)
        await self.drain()
        self.assertEqual(self.reviews(), [])
        self.assertIn("Private review channel unavailable", " ".join(visible_text(interaction.edit_original_response.call_args).split()))

    async def test_incident_buttons_capture_displayed_revision_and_new_client_handles_old_click(self):
        identity = await self.create_report()
        async def send_and_reconnect(*args, **kwargs):
            self.adapter.coverage_gap("reconnect")
            return SimpleNamespace(id=701)
        self.channel.send.side_effect = send_and_reconnect
        self.adapter.receive(self.message(309, 20, 98, "!mod incident " + identity))
        await self.drain()
        self.assertEqual([child.label for child in buttons(self.channel.send.call_args.kwargs["view"])[:3]],
                         ["Needs attention", "Looks okay", "Unsure"])
        self.assertGreater(self.adapter.store.incident(identity)["revision"], 1)
        # A fresh SDK client has no registered per-message views. Raw interaction
        # dispatch still resolves the persisted message -> displayed revision binding.
        client = PilotClient(self.adapter)
        self.adapter.next_command_at = 0
        interaction = self.interaction(message=self.message(701, 20, 99))
        await client.on_interaction(interaction)
        await self.drain()
        await client.close()
        self.assertEqual(self.reviews()[0]["revision"], 1)
        self.assertIn("revision: 1 (historical)", " ".join(visible_text(interaction.edit_original_response.call_args).split()))

    async def test_busy_and_expired_review_clicks_do_not_write(self):
        await self.create_report()
        self.adapter.worker.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await self.adapter.worker
        self.adapter.queue = asyncio.Queue(maxsize=1)
        self.adapter.queue.put_nowait((self.adapter.generation, "unused", None))
        busy = self.interaction()
        await self.adapter.receive_review_click(busy)
        busy.response.defer.assert_not_awaited()
        self.assertIn("busy", visible_text(busy.response.send_message.call_args))
        self.adapter.queue.get_nowait()
        self.adapter.queue.task_done()
        expired = self.interaction(801)
        await self.adapter.receive_review_click(expired)
        generation, kind, (interaction, event, _) = self.adapter.queue.get_nowait()
        self.adapter.queue.task_done()
        self.adapter.queue.put_nowait((generation, kind, (interaction, event, 0)))
        self.adapter.worker = asyncio.create_task(self.adapter.run_worker())
        await self.drain()
        self.assertEqual(self.reviews(), [])
        self.assertIn("Review not saved", visible_text(expired.edit_original_response.call_args))

    def editable_reports(self, *identities):
        messages = {identity: self.message(identity, 20, 99, content="Untrusted fetched body is not used")
                    for identity in identities}
        for message in messages.values():
            message.edit = AsyncMock()
        self.channel.fetch_message = AsyncMock(side_effect=lambda identity: messages[identity])
        return messages

    async def test_assessment_updates_original_report_and_completes_staff_queue(self):
        from liberdus_moderator.staff_review import pending_page
        identity = await self.create_report()
        messages = self.editable_reports(700)
        with patch("liberdus_moderator.jev.evaluate", side_effect=AssertionError("No model call")):
            click = self.interaction(label="looks-okay")
            await self.adapter.receive_review_click(click)
            await self.drain()
        messages[700].edit.assert_awaited_once()
        update = messages[700].edit.call_args.kwargs
        self.assertIn("**Looks okay**", visible_text(messages[700].edit.call_args))
        self.assertIn("· Complete", visible_text(messages[700].edit.call_args))
        self.assertIn("Reviewed by <@98>", visible_text(messages[700].edit.call_args))
        self.assertIn("Incident ID: `" + identity, visible_text(messages[700].edit.call_args))
        self.assertNotIn("Untrusted fetched body", visible_text(messages[700].edit.call_args))
        self.assertTrue(update["suppress"])
        self.assertEqual(update["allowed_mentions"].to_dict()["parse"], [])
        self.assertEqual([child.label for child in buttons(update["view"])[:3]], ["Needs attention", "Looks okay", "Unsure"])
        self.assertEqual(pending_page(self.adapter.live.engine)["total"], 0)
        self.assertIn("Report display updated", visible_text(click.edit_original_response.call_args))
        self.channel.send.assert_awaited_once()

    async def test_click_on_incident_view_refreshes_both_it_and_latest_report(self):
        identity = await self.create_report()
        self.channel.send.return_value = SimpleNamespace(id=701)
        self.adapter.receive(self.message(310, 20, 98, "!mod incident " + identity))
        await self.drain()
        messages = self.editable_reports(700, 701)
        self.adapter.next_command_at = 0
        click = self.interaction(message=self.message(701, 20, 99))
        await self.adapter.receive_review_click(click)
        await self.drain()
        for message in messages.values():
            message.edit.assert_awaited_once()
            self.assertIn("**Needs attention**", visible_text(message.edit.call_args))
        self.assertEqual(self.channel.send.await_count, 2)

    async def test_failed_report_edit_preserves_assessment_and_never_replays_mutation(self):
        from liberdus_moderator.staff_review import pending_page
        await self.create_report()
        messages = self.editable_reports(700)
        messages[700].edit.side_effect = TimeoutError()
        first = self.interaction(label="looks-okay")
        await self.adapter.receive_review_click(first)
        await self.drain()
        self.assertIn("could not be updated", " ".join(visible_text(first.edit_original_response.call_args).split()))
        self.assertEqual(pending_page(self.adapter.live.engine)["total"], 0)
        self.assertEqual(len(self.reviews()), 1)
        self.adapter.next_command_at = 0
        duplicate = self.interaction(label="looks-okay")
        await self.adapter.receive_review_click(duplicate)
        await self.drain()
        messages[700].edit.assert_awaited_once()
        self.assertEqual(len(self.reviews()), 1)
        self.channel.send.assert_awaited_once()
        self.assertTrue(self.adapter.online)

    async def test_refresh_refuses_wrong_author_and_permission_change_after_fetch(self):
        await self.create_report()
        messages = self.editable_reports(700)
        messages[700].author.id = 50
        first = self.interaction()
        await self.adapter.receive_review_click(first)
        await self.drain()
        messages[700].edit.assert_not_awaited()
        self.assertIn("could not be updated", " ".join(visible_text(first.edit_original_response.call_args).split()))
        messages[700].author.id = 99
        def changed_permissions(identity):
            self.channel.permissions_for.side_effect = lambda member: SimpleNamespace(
                administrator=False, view_channel=True, read_message_history=True, send_messages=True)
            return messages[identity]
        self.channel.fetch_message.side_effect = changed_permissions
        self.adapter.next_command_at = 0
        second = self.interaction(801, "unsure")
        await self.adapter.receive_review_click(second)
        await self.drain()
        messages[700].edit.assert_not_awaited()
        self.assertEqual(len(self.reviews()), 2)

    async def test_legacy_buttons_explain_upgrade_without_reinterpreting_old_label(self):
        await self.create_report()
        legacy = self.interaction(data={"component_type": 2, "custom_id": "liberdus:review:v1:promotion"})
        await self.adapter.receive_review_click(legacy)
        await self.drain()
        legacy.response.defer.assert_not_awaited()
        self.assertIn("old buttons record content labels", " ".join(visible_text(legacy.response.send_message.call_args).split()))
        self.assertEqual(self.reviews(), [])

    async def test_text_assessment_refreshes_report_and_pending_is_authorized(self):
        identity = await self.create_report()
        messages = self.editable_reports(700)
        self.adapter.receive(self.message(311, 20, 98, "!mod assess " + identity + " 1 unsure"))
        await self.drain()
        messages[700].edit.assert_awaited_once()
        self.assertIn("Staff assessment saved", visible_text(self.channel.send.call_args))
        self.adapter.next_command_at = 0
        self.adapter.receive(self.message(312, 20, 98, "!mod pending"))
        await self.drain()
        self.assertIn("1 pending", visible_text(self.channel.send.call_args))
        self.assertIn("Unsure", visible_text(self.channel.send.call_args))
        count = self.channel.send.await_count
        for user, channel in ((50, 20), (98, 10)):
            self.adapter.next_command_at = 0
            self.adapter.receive(self.message(313+channel, channel, user, "!mod pending"))
            await self.drain()
        self.assertEqual(self.channel.send.await_count, count)

    async def test_edit_fetch_failure_records_gap_and_discards_pending_window(self):
        self.adapter.receive(self.message())
        await self.drain()
        self.channel.fetch_message = AsyncMock(side_effect=TimeoutError())
        self.adapter.edit(1,10,100)
        await self.drain()
        self.assertEqual(self.adapter.live.status(True)["message_count"], 0)
        self.assertEqual(self.adapter.live.status(True)["last_coverage_gap"]["reason"], "unavailable_edit")

    async def test_uncached_edit_replaces_original_and_cancels_report(self):
        originals = [self.message(100+i,channel) for i,channel in enumerate((10,11,12))]
        for message in originals:
            self.adapter.receive(message)
        edited = self.message(102,12, content="Corrected message", created_at=originals[2].created_at,
                              edited_at=datetime.fromtimestamp(time.time()+0.001, timezone.utc))
        self.channel.fetch_message = AsyncMock(return_value=edited)
        self.adapter.edit(1,12,102)
        await self.drain()
        self.channel.send.assert_not_awaited()
        self.assertEqual(self.adapter.store.incidents()[0]["status"], "withdrawn")

    async def test_duplicate_message_does_not_duplicate_report(self):
        messages = [self.message(100+i,channel) for i,channel in enumerate((10,11,12))]
        for message in messages + messages:
            self.adapter.receive(message)
        await self.drain()
        self.channel.send.assert_awaited_once()

    async def test_queue_overflow_and_queued_delete_invalidate_old_evidence(self):
        self.adapter.queue = asyncio.Queue(maxsize=1)
        self.adapter.receive(self.message())
        self.adapter.receive(self.message(identity=101))
        self.assertEqual(self.adapter.live.status(True)["last_coverage_gap"]["reason"], "queue_full")
        self.adapter.receive(self.message(identity=102))
        self.adapter.deleted(1,10,[102])
        self.assertTrue(self.adapter.queue.empty())

    async def test_disconnect_clears_queued_events_and_reports_not_online(self):
        self.adapter.receive(self.message())
        self.adapter.lost_connection()
        self.assertFalse(self.adapter.online)
        self.assertTrue(self.adapter.queue.empty())
        self.assertEqual(self.adapter.live.status(False)["last_coverage_gap"]["reason"], "disconnect")

    async def test_sdk_nonce_enforced_and_dm_intents_disabled(self):
        from discord.http import handle_message_parameters
        with handle_message_parameters(content="test", nonce="pilot") as params:
            self.assertTrue(params.payload["enforce_nonce"])
        client = PilotClient(self.adapter)
        self.assertTrue(client.intents.message_content)
        self.assertFalse(client.intents.dm_messages)
        self.assertFalse(client.intents.members)
        await client.close()

    async def test_shadow_provider_wait_cannot_block_rule_report_or_call_hermes(self):
        from dataclasses import replace
        from liberdus_moderator.classifier import ShadowClassifier, RUBRIC
        from liberdus_moderator.config import ClassifierSettings
        self.adapter.policy = replace(self.adapter.policy, schema_version=2, ai_enabled=True,
            classifier=ClassifierSettings(mode="shadow", max_daily_calls=10, max_total_calls=10,
                daily_budget_microusd=50000, total_budget_microusd=50000))
        self.adapter.live = LiveSession(Engine(self.adapter.policy, self.adapter.store))
        started, release = asyncio.Event(), asyncio.Event()
        async def blocked(payload):
            started.set()
            await release.wait()
            return {"model": "jev-1.13.0", "answers": {"context": {"type": "choice", "choice": "promotion",
                "probabilities": {key: 0.8 if key == "promotion" else 0.05 for key in RUBRIC["criteria"]}, "confidence": 0.8}},
                "usage": {"input_tokens": 500, "output_tokens": 20}}
        self.adapter.classifier = ShadowClassifier(self.adapter.live.engine, blocked,
            active=lambda: self.adapter.online and not self.adapter.closing)
        self.adapter.classifier.start()
        self.adapter.handle_message = AsyncMock()
        for i, channel in enumerate((10, 11, 12)):
            self.adapter.receive(self.message(100+i, channel))
        await self.drain()
        await asyncio.wait_for(started.wait(), 1)
        self.channel.send.assert_awaited_once()
        self.assertEqual(self.adapter.store.db.execute("SELECT status FROM reports").fetchone()[0], "sent")
        self.adapter.handle_message.assert_not_called()
        self.adapter.deleted(1, 10, [100])
        release.set()
        await asyncio.wait_for(self.adapter.classifier.queue.join(), 1)
        record = self.adapter.store.db.execute("SELECT outcome, result_json FROM classifier_attempts").fetchone()
        self.assertEqual(record["outcome"], "stale")
        self.assertNotIn("choice", record["result_json"])

    async def test_jev_key_uses_current_profile_and_does_not_borrow_default_key(self):
        from agent.secret_scope import set_secret_scope, reset_secret_scope
        with patch.dict("os.environ", {"TYPESAFE_API_KEY": "default-fake-key"}), patch("liberdus_moderator.jev.evaluate", new_callable=AsyncMock) as provider:
            for scope, expected in ((None, None), ({}, None), ({"TYPESAFE_API_KEY": "profile-fake-key"}, "profile-fake-key"), ({}, None)):
                token = set_secret_scope(scope)
                try:
                    await self.adapter.evaluate_jev(b"{}")
                    self.assertEqual(provider.call_args.args[1], expected)
                finally:
                    reset_secret_scope(token)

    async def test_disabled_start_never_logs_into_discord(self):
        fresh = ModerationAdapter(PlatformConfig(enabled=False))
        fresh._set_fatal_error = Mock()
        fresh._mark_disconnected = Mock()
        with patch.object(discord.Client, "login", new_callable=AsyncMock) as login:
            self.assertFalse(await fresh.connect())
            login.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
