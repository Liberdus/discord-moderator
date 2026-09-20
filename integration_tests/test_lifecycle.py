import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace

from gateway.config import PlatformConfig
from gateway.platform_registry import platform_registry, PlatformEntry
from hermes_constants import set_hermes_home_override, reset_hermes_home_override

from liberdus_moderator.hermes_adapter import ModerationAdapter
from liberdus_moderator.hermes_plugin import PLATFORM


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        home = Path(self.temp.name)
        self.profile = home / "profiles/liberdus-mod"
        self.profile.mkdir(parents=True)
        (home / "config.yaml").write_text("platforms:\n  discord:\n    enabled: false\n")
        (self.profile / "config.yaml").write_text("platforms:\n  discord:\n    enabled: false\n  liberdus_moderator:\n    enabled: true\n")
        (self.profile / "moderation.toml").write_text(f'''schema_version = 1
policy_version = "test"
[scope]
guild_id = "1"
bot_user_id = "99"
monitored_channel_ids = ["10", "11", "12"]
command_channel_ids = ["20"]
operator_user_ids = ["98"]
[storage]
database_path = "{self.profile}/state/moderation.sqlite3"
''')
        token = set_hermes_home_override(self.profile)
        self.addCleanup(reset_hermes_home_override, token)
        platform_registry.register(PlatformEntry(PLATFORM, "Test", lambda c: None, lambda: True))
        self.instances = []

    def adapter(self):
        adapter = ModerationAdapter(PlatformConfig(enabled=True))
        for method in ("_mark_connected", "_mark_disconnected", "_set_fatal_error", "_release_platform_lock"):
            setattr(adapter, method, Mock())
        adapter._acquire_platform_lock = Mock(return_value=True)
        adapter.checked_channel = Mock()
        self.instances.append(adapter)
        return adapter

    def fake_client(self, adapter):
        stopped = asyncio.Event()
        async def connect(*, reconnect):
            await adapter.ready()
            await stopped.wait()
        async def close():
            stopped.set()
        return SimpleNamespace(user=SimpleNamespace(id=99), login=AsyncMock(), connect=connect, close=close)

    async def asyncTearDown(self):
        for adapter in self.instances:
            await adapter.disconnect()

    async def test_start_readiness_same_process_lock_and_clean_shutdown(self):
        first = self.adapter()
        second = self.adapter()
        with patch("liberdus_moderator.hermes_adapter.verify_runtime"), patch("liberdus_moderator.hermes_adapter.get_scoped_secret", return_value="fake-token"), patch("liberdus_moderator.hermes_adapter.PilotClient", side_effect=self.fake_client):
            self.assertTrue(await first.connect())
            self.assertTrue(first.online)
            first.client.login.assert_awaited_once_with("fake-token")
            self.assertFalse(await second.connect())
            self.assertIsNone(second.store)
            self.assertTrue(first.online)
            await first.disconnect()
            self.assertIsNone(first.store)
            self.assertIsNone(first.lock_fd)
            first._release_platform_lock.assert_called_once()

    async def test_bad_token_identity_closes_before_receiving(self):
        adapter = self.adapter()
        client = self.fake_client(adapter)
        client.user.id = 77
        client.connect = AsyncMock()
        with patch("liberdus_moderator.hermes_adapter.verify_runtime"), patch("liberdus_moderator.hermes_adapter.get_scoped_secret", return_value="fake-token"), patch("liberdus_moderator.hermes_adapter.PilotClient", return_value=client):
            self.assertFalse(await adapter.connect())
        client.connect.assert_not_awaited()
        self.assertIsNone(adapter.store)
        self.assertIsNone(adapter.lock_fd)
        self.assertFalse(adapter.online)


if __name__ == "__main__":
    unittest.main()
