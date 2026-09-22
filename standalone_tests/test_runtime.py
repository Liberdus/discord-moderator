"""Actual Discord SDK, synthetic transport, and no Hermes imports or installation."""

import asyncio
from datetime import datetime, timezone
import io
import json
import logging
import os
import signal
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from liberdus_moderator.config import Config, ClassifierSettings
from liberdus_moderator.configure_jev import policy_text
from liberdus_moderator.credentials import Credentials
from liberdus_moderator.instance import create_instance, load_policy
from liberdus_moderator.secure_files import write_private
from liberdus_moderator.standalone import SafeLogFilter, StandaloneService, serve


def fake_client(service):
    stopped = asyncio.Event()
    guild = SimpleNamespace(id=1, me=object(), default_role=object(), unavailable=False)
    channels = {}
    for identity in (10, 20):
        channel = Mock(spec=discord.TextChannel)
        channel.id = identity
        channel.guild = guild
        channel.type = discord.ChannelType.text
        channel.category_id = None
        channel.send = AsyncMock(return_value=SimpleNamespace(id=700))
        channel.permissions_for.side_effect = lambda member, identity=identity: SimpleNamespace(
            administrator=False, view_channel=(member is guild.me or identity == 10),
            read_message_history=True, send_messages=True, manage_messages=True)
        channels[identity] = channel

    async def connect(*, reconnect):
        await service.ready()
        await stopped.wait()

    async def close():
        stopped.set()

    http = SimpleNamespace(get_guild_commands=AsyncMock(return_value=[]),
                           bulk_upsert_guild_commands=AsyncMock(return_value=[]),
                           get_global_commands=AsyncMock(return_value=[]),
                           bulk_upsert_global_commands=AsyncMock(return_value=[]))
    return SimpleNamespace(user=SimpleNamespace(id=99), application_id=99, http=http,
                           login=AsyncMock(), connect=connect, close=close,
                           get_channel=channels.get, channels=channels)


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "instance"
        account = patch("liberdus_moderator.secure_files.pwd.getpwuid",
                        return_value=SimpleNamespace(pw_dir=self.temp.name))
        account.start()
        self.addCleanup(account.stop)
        # Unrelated application state can be group-writable; it must not be a
        # prerequisite for our private token lock or cause a permissions change.
        shared = self.home.parent / ".local/state"
        shared.mkdir(parents=True, mode=0o775)
        shared.chmod(0o775)
        self.policy = Config("1", "99", ("10",), ("20",), ("98",),
                             schema_version=2, explicit_channel_scope=True,
                             allow_public_monitored_channels=True)
        create_instance(self.home, self.policy, {"discord": "fake-standalone-token"})
        self.service = StandaloneService(self.home)
        self.service.make_client = lambda: fake_client(self.service)

    async def asyncTearDown(self):
        await self.service.disconnect()

    def message(self, identity, *, channel=10, author=50, content="Repeated test content with enough length."):
        return SimpleNamespace(id=identity, channel=SimpleNamespace(id=channel), guild=SimpleNamespace(id=1),
                               author=SimpleNamespace(id=author, bot=False, _roles=[]), content=content,
                               created_at=datetime.now(timezone.utc), edited_at=None, webhook_id=None, attachments=[])

    async def drain(self):
        await asyncio.wait_for(self.service.queue.join(), timeout=3)

    async def test_start_process_messages_cards_commands_and_stop_without_hermes(self):
        self.assertTrue(await self.service.connect())
        client = self.service.client
        client.login.assert_awaited_once_with("fake-standalone-token")
        for identity in range(100, 104):
            self.service.receive(self.message(identity))
        await self.drain()
        client.channels[20].send.assert_awaited_once()
        args, kwargs = client.channels[20].send.call_args
        self.assertIsInstance(kwargs["view"], discord.ui.LayoutView)
        self.assertEqual(kwargs["allowed_mentions"].to_dict()["parse"], [])
        self.assertEqual(self.service.store.db.execute("SELECT COUNT(*) FROM incidents").fetchone()[0], 1)
        self.service.receive(self.message(200, channel=20, author=98, content="!mod pause"))
        await self.drain()
        self.assertTrue(self.service.store.get_setting("paused"))
        await self.service.disconnect()
        self.assertIsNone(self.service.store)
        self.assertIsNone(self.service.lock_fd)
        self.assertIsNone(self.service.token_context)
        self.assertEqual(json.loads((self.home / "state/runtime.json").read_text())["state"], "stopped")
        import sys
        self.assertFalse(any(name.startswith(("hermes_cli", "gateway.", "agent.")) for name in sys.modules))

    async def test_duplicate_start_does_not_overwrite_live_status(self):
        self.assertTrue(await self.service.connect())
        before = (self.home / "state/runtime.json").read_bytes()
        second = StandaloneService(self.home)
        second.make_client = Mock(side_effect=AssertionError("Must not log in twice"))
        self.assertFalse(await second.connect())
        await second.disconnect()
        self.assertEqual(before, (self.home / "state/runtime.json").read_bytes())
        self.assertTrue(self.service.online)
        second.make_client.assert_not_called()

    async def test_same_token_in_another_directory_is_rejected(self):
        self.assertTrue(await self.service.connect())
        other_home = self.home.parent / "other"
        create_instance(other_home, self.policy, {"discord": "fake-standalone-token"})
        other = StandaloneService(other_home)
        other.make_client = Mock(side_effect=AssertionError("Must not log in"))
        self.assertFalse(await other.connect())
        other.make_client.assert_not_called()
        await other.disconnect()
        self.assertTrue(self.service.online)

    async def test_wrong_token_identity_releases_locks(self):
        client = fake_client(self.service)
        client.user.id = 999
        self.service.make_client = lambda: client
        self.assertFalse(await self.service.connect())
        self.assertIsNone(self.service.lock_fd)
        self.assertIsNone(self.service.token_context)
        replacement = StandaloneService(self.home)
        replacement.make_client = lambda: fake_client(replacement)
        self.assertTrue(await replacement.connect())
        await replacement.disconnect()

    async def test_unauthorized_staff_and_unselected_channels_cannot_change_state(self):
        self.assertTrue(await self.service.connect())
        self.service.receive(self.message(100, channel=20, author=50, content="!mod pause"))
        self.service.receive(self.message(101, channel=999))
        await self.drain()
        self.assertFalse(self.service.store.get_setting("paused"))
        self.service.client.channels[20].send.assert_not_awaited()
        self.assertEqual(self.service.store.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)

    async def test_policy_or_staff_privacy_change_stops_processing(self):
        self.assertTrue(await self.service.connect())
        policy = load_policy(self.home)
        from dataclasses import replace
        write_private(self.home / "moderation.toml", policy_text(replace(policy, policy_version="changed")), replace=True)
        self.assertFalse(self.service.policy_current())
        write_private(self.home / "moderation.toml", policy_text(policy), replace=True)
        channel = self.service.client.channels[20]
        channel.permissions_for.side_effect = lambda member: SimpleNamespace(
            administrator=False, view_channel=True, read_message_history=True, send_messages=True)
        self.assertFalse(self.service.scope_current())
        self.assertFalse(self.service.in_scope(1, 20))

    async def test_jev_uses_only_its_explicit_credential(self):
        self.assertTrue(await self.service.connect())
        Credentials(self.home).put("jev", "instance-only-key")
        self.service.classifier_active = lambda: True
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "unrelated-environment-key"}), \
                patch("liberdus_moderator.jev.evaluate", new_callable=AsyncMock) as evaluate:
            await self.service.evaluate_jev(b"synthetic-payload")
            self.assertEqual(evaluate.call_args.args[1], "instance-only-key")
        (self.home / "credentials/jev-key").unlink()
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "unrelated-environment-key"}), \
                patch("liberdus_moderator.jev.evaluate", new_callable=AsyncMock) as evaluate:
            with self.assertRaises(FileNotFoundError):
                await self.service.evaluate_jev(b"synthetic-payload")
            evaluate.assert_not_awaited()


class LogTests(unittest.TestCase):
    def test_only_fixed_operational_events_can_reach_logs(self):
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        handler.addFilter(SafeLogFilter())
        for name, message, args, exception in (
            ("discord.http", "webhook/secret-test-key", (), None),
            ("liberdus_moderator.service", "connected", (), None),
            ("liberdus_moderator.service", "secret-test-key", (), None),
            ("liberdus_moderator.service", "%s", ("secret-test-key",), None),
            ("liberdus_moderator.service", "runtime_failed", (), (ValueError, ValueError("secret-test-key"), None)),
        ):
            handler.handle(logging.LogRecord(name, logging.ERROR, __file__, 1, message, args, exception))
        self.assertEqual(output.getvalue(), "connected\n")


class SignalTests(unittest.IsolatedAsyncioTestCase):
    async def test_sigterm_cancels_startup_and_closes_resources_promptly(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()
        async def connect():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        service = SimpleNamespace(connect=connect, disconnect=AsyncMock(), logger=Mock(),
                                  finished=asyncio.Event(), error_code=None)
        handlers = {}
        loop = asyncio.get_running_loop()
        with patch("liberdus_moderator.standalone.StandaloneService", return_value=service), \
                patch.object(loop, "add_signal_handler", side_effect=lambda sig, callback: handlers.update({sig: callback})), \
                patch.object(loop, "remove_signal_handler"):
            task = asyncio.create_task(serve("unused"))
            await asyncio.wait_for(started.wait(), timeout=1)
            handlers[signal.SIGTERM]()
            self.assertEqual(await asyncio.wait_for(task, timeout=1), 0)
        self.assertTrue(cancelled.is_set())
        service.disconnect.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
