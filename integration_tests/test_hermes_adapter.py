"""Run with the verified Hermes source on PYTHONPATH and its optional dependencies.

All network edges are mocked; the real Hermes loader/config and Discord SDK are used.
"""

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
                      author=SimpleNamespace(id=author, bot=False), content=content,
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
        self.assertNotIn("<@123>", args[0])
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
        self.assertIn("9/9 passed", args[0])
        self.assertIn("Live Discord events, permissions and actual restart: not tested", args[0])
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
        self.assertIn("JEV (saved shadow): quoted_warning", args[0])
        self.assertIn("no new AI call", args[0])
        self.assertNotIn("Repeated test message", args[0])
        self.assertLess(len(args[0]), 1900)
        self.assertEqual(kwargs["allowed_mentions"].to_dict()["parse"], [])
        provider.assert_awaited_once()
        await worker.close()

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
