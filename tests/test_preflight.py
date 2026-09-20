import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from liberdus_moderator.config import Config
from liberdus_moderator.preflight import (
    ADMIN, HISTORY, SEND, VIEW, DiscordReader, NoRedirect, PreflightError,
    inspect, main, permissions, read_token,
)


def overwrite(identity, allow=0, deny=0, kind=0):
    return {"id": identity, "type": kind, "allow": str(allow), "deny": str(deny)}


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.config = Config("1", "99", ("10",), ("20",), ("98",))
        self.guild = {"id": "1", "owner_id": "98", "roles": [
            {"id": "1", "permissions": str(VIEW | HISTORY)},
            {"id": "5", "permissions": str(SEND)},
            {"id": "6", "permissions": "0"},
        ]}
        self.channel = {"id": "10", "guild_id": "1", "type": 0,
                        "permission_overwrites": [overwrite("1", deny=VIEW), overwrite("5", allow=VIEW)]}
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        return {
            "/users/@me": {"id": "99", "bot": True},
            "/guilds/1": self.guild,
            "/guilds/1/members/99": {"user": {"id": "99"}, "roles": ["5"]},
            "/channels/10": self.channel,
            "/channels/20": dict(self.channel, id="20"),
        }[path]

    def test_read_only_paths_and_valid_private_scope(self):
        report = inspect(self.config, self.get)
        self.assertTrue(report["checks_passed"])
        self.assertEqual(self.calls, ["/users/@me", "/guilds/1", "/guilds/1/members/99", "/channels/10", "/channels/20"])
        self.assertEqual(report["channels"][1]["additional_view_overwrite_ids"], ["5"])

    def test_aggregate_role_allow_wins_then_member_deny_wins(self):
        self.channel["permission_overwrites"].append(overwrite("6", deny=VIEW))
        self.assertTrue(permissions(self.guild, ["5", "6"], "99", self.channel) & VIEW)
        self.channel["permission_overwrites"].append(overwrite("99", deny=VIEW, kind=1))
        self.assertFalse(permissions(self.guild, ["5", "6"], "99", self.channel) & VIEW)
        report = inspect(self.config, self.get)
        self.assertFalse(report["checks_passed"])
        self.assertFalse(report["channels"][0]["bot_send"])

    def test_owner_and_admin_bypass_channel_overwrites_but_admin_fails_pilot(self):
        self.assertEqual(permissions(self.guild, [], "98", self.channel), -1)
        self.guild["roles"][1]["permissions"] = str(ADMIN)
        self.assertEqual(permissions(self.guild, ["5"], "99", self.channel), -1)
        report = inspect(self.config, self.get)
        self.assertFalse(report["checks_passed"])
        self.assertTrue(report["channels"][0]["bot_administrator"])

    def test_public_channel_or_missing_send_fails_checks(self):
        self.channel["permission_overwrites"] = []
        self.assertFalse(inspect(self.config, self.get)["checks_passed"])
        self.channel["permission_overwrites"] = [overwrite("1", deny=VIEW), overwrite("5", allow=VIEW)]
        self.guild["roles"][1]["permissions"] = "0"
        report = inspect(self.config, self.get)
        self.assertTrue(report["channels"][0]["required_permissions_ok"])
        self.assertFalse(report["channels"][1]["required_permissions_ok"])

    def test_wrong_bot_stops_before_guild_access(self):
        calls = []
        def wrong(path):
            calls.append(path)
            return {"id": "100", "bot": True}
        with self.assertRaises(PreflightError):
            inspect(self.config, wrong)
        self.assertEqual(calls, ["/users/@me"])

    def test_wrong_guild_or_thread_rejected(self):
        for changes in ({"guild_id": "2"}, {"type": 11}, {"id": "11"}):
            old = dict(self.channel)
            self.channel.update(changes)
            with self.subTest(changes=changes), self.assertRaises(PreflightError):
                inspect(self.config, self.get)
            self.channel = old

    def test_unknown_roles_fail_closed(self):
        with self.assertRaises(PreflightError):
            permissions(self.guild, ["404"], "99", self.channel)

    def test_token_parser_rejects_ambiguity_expansion_and_nonprivate_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("OTHER_KEY=never-print\nexport DISCORD_BOT_TOKEN='fake.token-value' # comment\n")
            path.chmod(0o600)
            self.assertEqual(read_token(path), "fake.token-value")
            for content in ("", "DISCORD_BOT_TOKEN=x\nDISCORD_BOT_TOKEN=y\n", "DISCORD_BOT_TOKEN=${OTHER}\n", "DISCORD_BOT_TOKEN='unterminated\n"):
                path.write_text(content)
                with self.subTest(content=content), self.assertRaises(PreflightError):
                    read_token(path)
            path.write_text("DISCORD_BOT_TOKEN=fake\n")
            path.chmod(0o644)
            with self.assertRaises(PreflightError):
                read_token(path)
            link = Path(directory) / "link"
            link.symlink_to(path)
            with self.assertRaises(OSError):
                read_token(link)

    def test_http_uses_get_and_errors_do_not_expose_secrets(self):
        reader = DiscordReader("fake-secret")
        def fail(request, timeout):
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(request.full_url, "https://discord.com/api/v10/users/@me")
            raise urllib.error.HTTPError(request.full_url, 401, "fake-secret", {}, io.BytesIO(b"fake-secret"))
        with patch.object(reader.opener, "open", side_effect=fail):
            with self.assertRaises(PreflightError) as error:
                reader("/users/@me")
        self.assertNotIn("fake-secret", str(error.exception))
        self.assertIn("401", str(error.exception))

    def test_redirect_refused(self):
        with self.assertRaises(PreflightError):
            NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.invalid")

    def test_main_suppresses_raw_exception_contents(self):
        with patch("liberdus_moderator.preflight.Config.from_file", side_effect=ValueError("fake-secret")):
            with patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(main(["--config", "unused"]), 2)
            self.assertNotIn("fake-secret", output.getvalue())


if __name__ == "__main__":
    unittest.main()
