"""Security and end-to-end setup checks using synthetic metadata and keys."""

from contextlib import closing, redirect_stderr, redirect_stdout
from copy import deepcopy
from dataclasses import replace
import getpass
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import warnings

from liberdus_moderator.config import Config, ClassifierSettings
from liberdus_moderator.credentials import Credentials, prompt_secret
from liberdus_moderator.doctor import check, inspect_channels, terminal
from liberdus_moderator.instance import create_instance, load_policy
from liberdus_moderator.preflight import VIEW, HISTORY, SEND
from liberdus_moderator.secure_files import SetupError, checked_directory, file_lock, read_private, write_private
from liberdus_moderator.setup_wizard import budget, channel_id, choose, wizard
from liberdus_moderator.standalone_cli import main

DISCORD_SECRET = "synthetic-discord-key-DO-NOT-PRINT"
JEV_SECRET = "synthetic-jev-key-DO-NOT-PRINT"


def metadata():
    return {
        "/users/@me": {"id": "99", "bot": True, "username": "Test bot"},
        "/oauth2/applications/@me": {"id": "99", "bot": {"id": "99"}, "flags": 1 << 19},
        "/users/@me/guilds?limit=200": [{"id": "1", "name": "Test server"}],
        "/guilds/1": {"id": "1", "name": "Test server", "owner_id": "98", "roles": [
            {"id": "1", "permissions": str(VIEW | HISTORY)},
            {"id": "5", "permissions": str(SEND | (1 << 13))}]},
        "/guilds/1/members/99": {"user": {"id": "99", "bot": True}, "roles": ["5"]},
        "/guilds/1/members/98": {"user": {"id": "98", "username": "Moderator"}, "roles": []},
        "/guilds/1/channels": [
            {"id": "10", "name": "general", "guild_id": "1", "type": 0, "parent_id": None, "permission_overwrites": []},
            {"id": "20", "name": "bot-mod", "guild_id": "1", "type": 0, "parent_id": None, "permission_overwrites": [
                {"id": "1", "type": 0, "allow": "0", "deny": str(VIEW)},
                {"id": "5", "type": 0, "allow": str(VIEW), "deny": "0"}]},
        ],
    }


class SecureFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)

    def test_hidden_input_refuses_echo_fallback(self):
        def fallback(label):
            warnings.warn("terminal unavailable", getpass.GetPassWarning)
            return DISCORD_SECRET
        with self.assertRaises(SetupError):
            prompt_secret("Key: ", fallback)

    def test_file_permissions_symlinks_hardlinks_and_oversize_rejected(self):
        credentials = Credentials(self.home)
        credentials.put("discord", DISCORD_SECRET)
        path = self.home / "credentials/discord-token"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(credentials.get("discord"), DISCORD_SECRET)
        path.chmod(0o644)
        with self.assertRaises(SetupError):
            credentials.get("discord")
        path.chmod(0o600)
        link = path.with_name("copy")
        os.link(path, link)
        with self.assertRaises(SetupError):
            credentials.get("discord")
        link.unlink()
        path.rename(link)
        path.symlink_to(link)
        with self.assertRaises(OSError):
            credentials.get("discord")
        path.unlink()
        link.rename(path)
        path.write_text("a" * 4097)
        with self.assertRaises(SetupError):
            credentials.get("discord")

    def test_unsafe_directory_and_parent_symlink_rejected(self):
        nested = self.home / "private"
        checked_directory(nested, create=True)
        nested.chmod(0o755)
        with self.assertRaises(SetupError):
            checked_directory(nested)
        link = self.home / "redirect"
        link.symlink_to(nested, target_is_directory=True)
        with self.assertRaises(SetupError):
            checked_directory(link / "instance", create=True)

    def test_atomic_rotation_does_not_overwrite_on_failure(self):
        credentials = Credentials(self.home)
        credentials.put("discord", DISCORD_SECRET)
        with patch("liberdus_moderator.secure_files.os.replace", side_effect=OSError("failure")):
            with self.assertRaises(OSError):
                credentials.put("discord", "new-key", replace=True)
        self.assertEqual(credentials.get("discord"), DISCORD_SECRET)
        self.assertEqual(list((self.home / "credentials").glob(".write-*")), [])
        with self.assertRaises(SetupError):
            credentials.put("discord", "new-key")

    def test_systemd_is_explicit_and_never_falls_back_to_env_or_files(self):
        portable = Credentials(self.home)
        portable.put("discord", DISCORD_SECRET)
        provider = Credentials(self.home, backend="systemd")
        with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": DISCORD_SECRET}, clear=True):
            with self.assertRaises(SetupError):
                provider.get("discord")
        runtime = checked_directory(self.home / "runtime", create=True)
        write_private(runtime / "discord-token", "runtime-key")
        self.assertEqual(Credentials(self.home, backend="systemd", directory=runtime).get("discord"), "runtime-key")
        self.assertNotIn(DISCORD_SECRET, repr(portable))

    def test_locks_exclude_another_holder_and_release_after_failure(self):
        path = self.home / "active.lock"
        with file_lock(path):
            with self.assertRaises(SetupError):
                with file_lock(path):
                    self.fail("lock admitted a second holder")
        with file_lock(path):
            pass


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "instance"
        account = patch("liberdus_moderator.secure_files.pwd.getpwuid",
                        return_value=SimpleNamespace(pw_dir=self.temp.name))
        account.start()
        self.addCleanup(account.stop)
        self.data = metadata()
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        return deepcopy(self.data[path])

    def run_wizard(self, answers, *, keys=(DISCORD_SECRET,)):
        output = []
        result = wizard(self.home, ask=Mock(side_effect=answers), secret_prompt=Mock(side_effect=keys),
                        output=output.append, reader_factory=lambda key: self.get)
        text = "\n".join(output)
        self.assertNotIn(DISCORD_SECRET, text)
        self.assertNotIn(JEV_SECRET, text)
        self.assertFalse(any("/messages" in path for path in self.calls))
        return result, text

    def test_default_setup_to_doctor_and_no_unintended_deletion_or_ai(self):
        result, text = self.run_wizard(["1", "general", "https://discord.com/channels/1/20", "98", "", "", "y"])
        self.assertTrue(result)
        self.assertIn("not encrypted", text)
        policy = load_policy(self.home)
        self.assertTrue(policy.explicit_channel_scope)
        self.assertEqual(policy.monitored_channel_ids, ("10",))
        self.assertFalse(policy.actions_enabled)
        self.assertFalse(policy.ai_enabled)
        with closing(sqlite3.connect(policy.storage.database_path)) as db:
            saved = dict(db.execute("SELECT key, value FROM settings"))
        self.assertEqual(saved["deletion_enabled"], "false")
        report = check(self.home, reader_factory=lambda token: self.get)
        self.assertTrue(report["ok"])
        self.assertEqual(report["jev"], "off")
        for path in self.home.rglob("*"):
            if path.is_file() and path.parent.name != "credentials":
                self.assertNotIn(DISCORD_SECRET.encode(), path.read_bytes())

    def test_ai_and_auto_delete_require_reviewed_choice_and_preserve_caps(self):
        self.run_wizard(["1", "id:10", "<#20>", "98", "yes", "0.50", "2", "3", "yes"], keys=(DISCORD_SECRET, JEV_SECRET))
        policy = load_policy(self.home)
        self.assertEqual(policy.classifier.daily_budget_microusd, 500000)
        self.assertEqual(policy.classifier.total_budget_microusd, 2000000)
        self.assertTrue(policy.allow_public_deletion)
        with closing(sqlite3.connect(policy.storage.database_path)) as db:
            saved = dict(db.execute("SELECT key, value FROM settings"))
        self.assertEqual(saved["auto_delete_enabled"], "true")
        self.assertEqual(saved["timeout_enabled"], "false")
        self.assertEqual(Credentials(self.home).get("jev"), JEV_SECRET)

    def test_cancel_or_existing_installation_never_overwrites(self):
        result, _ = self.run_wizard(["1", "1", "2", "98", "", "", "n"])
        self.assertFalse(result)
        self.assertFalse(self.home.exists())
        self.run_wizard(["1", "1", "2", "98", "", "", "y"])
        before = (self.home / "moderation.toml").read_bytes()
        with self.assertRaises(SetupError):
            self.run_wizard([])
        self.assertEqual(before, (self.home / "moderation.toml").read_bytes())

    def test_missing_delete_permission_and_public_staff_block_setup(self):
        for mutation in ("delete", "staff"):
            with self.subTest(mutation=mutation):
                self.data = metadata()
                if mutation == "delete":
                    self.data["/guilds/1"]["roles"][1]["permissions"] = str(SEND)
                else:
                    self.data["/guilds/1/channels"][1]["permission_overwrites"] = []
                with self.assertRaises(SetupError):
                    self.run_wizard(["1", "general", "bot-mod", "98", "", "2", "y"])
                self.assertFalse(self.home.exists())

    def test_doctor_blocks_running_instance_and_has_no_provider_call(self):
        self.run_wizard(["1", "1", "2", "98", "", "", "y"])
        with file_lock(self.home / "state/moderation.lock"):
            with self.assertRaises(SetupError):
                check(self.home, offline=True)
        with patch("liberdus_moderator.jev.evaluate", side_effect=AssertionError("No paid test")):
            self.assertTrue(check(self.home, reader_factory=lambda token: self.get)["ok"])

    def test_pasted_ids_links_ambiguity_and_budget_bounds(self):
        self.assertEqual(channel_id("https://discord.com/channels/1/10/123", "1"), "10")
        for link in ("https://discord.com/channels/2/10", "https://evil.invalid/channels/1/10"):
            with self.assertRaises(SetupError):
                channel_id(link, "1")
        with self.assertRaises(SetupError):
            choose("same", [{"id": "10", "name": "same"}, {"id": "20", "name": "same"}])
        for value in ("NaN", "Infinity", "-1", "0", "11", "0.0000001"):
            with self.assertRaises(SetupError):
                budget(value, 10)
        self.assertNotIn("\x1b", terminal("name\x1b[2J"))

    def test_cli_errors_never_print_raw_exception_keys(self):
        output = io.StringIO()
        with patch("liberdus_moderator.setup_wizard.wizard", side_effect=RuntimeError(DISCORD_SECRET + JEV_SECRET)), redirect_stderr(output), redirect_stdout(output):
            self.assertEqual(main(["setup", "--home", str(self.home)]), 2)
        self.assertNotIn(DISCORD_SECRET, output.getvalue())
        self.assertNotIn(JEV_SECRET, output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())

    def test_failed_install_publication_rolls_back_created_files(self):
        policy = Config("1", "99", ("10",), ("20",), ("98",))
        real = os.rename
        def fail_marker(source, target):
            if str(target).endswith("instance.json"):
                raise OSError("synthetic failure")
            return real(source, target)
        with patch("liberdus_moderator.instance.os.rename", side_effect=fail_marker):
            with self.assertRaises(OSError):
                create_instance(self.home, policy, {"discord": DISCORD_SECRET})
        self.assertEqual({p.name for p in self.home.iterdir()}, {"setup.lock"})

    def test_setup_refuses_git_checkout_before_asking_for_keys(self):
        self.home.mkdir()
        (self.home / ".git").mkdir()
        secret = Mock(side_effect=AssertionError("No credential prompt"))
        with self.assertRaises(SetupError):
            wizard(self.home / "private", secret_prompt=secret)
        secret.assert_not_called()

    def test_missing_message_content_or_external_interactions_blocks_setup(self):
        for fields in ({"flags": 0}, {"flags": -1}, {"flags_new": "-1"},
                       {"interactions_endpoint_url": "https://example.invalid/interactions"}):
            self.data = metadata()
            self.data["/oauth2/applications/@me"].update(fields)
            with self.assertRaises(SetupError):
                self.run_wizard([])
            self.assertFalse(self.home.exists())


if __name__ == "__main__":
    unittest.main()
