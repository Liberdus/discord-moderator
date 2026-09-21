"""Read-only permission preflight for explicit public-channel rollout."""
from copy import deepcopy
from dataclasses import replace
import unittest

from liberdus_moderator.config import Config
from liberdus_moderator.preflight import ADMIN, HISTORY, SEND, VIEW, PreflightError, inspect


class PublicPreflightTests(unittest.TestCase):
    def setUp(self):
        self.config = Config("1", "99", ("10",), ("20",), ("98",),
                             allow_public_monitored_channels=True, excluded_category_ids=("40",))
        self.metadata = {
            "/users/@me": {"id": "99", "bot": True},
            "/guilds/1": {"id": "1", "owner_id": "98", "roles": [
                {"id": "1", "permissions": str(VIEW | HISTORY)},
                {"id": "5", "permissions": str(SEND)}]},
            "/guilds/1/members/99": {"user": {"id": "99"}, "roles": ["5"]},
            "/channels/10": {"id": "10", "guild_id": "1", "type": 0, "parent_id": "30",
                             "permission_overwrites": []},
            "/channels/20": {"id": "20", "guild_id": "1", "type": 0, "parent_id": "30",
                             "permission_overwrites": [
                                 {"id": "1", "type": 0, "allow": "0", "deny": str(VIEW)},
                                 {"id": "5", "type": 0, "allow": str(VIEW), "deny": "0"}]},
            "/channels/30": {"id": "30", "guild_id": "1", "type": 4},
            "/channels/40": {"id": "40", "guild_id": "1", "type": 4},
        }
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        return deepcopy(self.metadata[path])

    def test_public_monitor_opt_in_and_private_command_with_shared_category(self):
        report = inspect(self.config, self.get)
        self.assertTrue(report["checks_passed"])
        monitor, command = report["channels"]
        self.assertFalse(monitor["everyone_hidden"])
        self.assertFalse(monitor["private_required"])
        self.assertTrue(monitor["public_required"])
        self.assertTrue(command["everyone_hidden"])
        self.assertTrue(command["private_required"])
        self.assertFalse(command["public_required"])
        self.assertTrue(all(row["category_allowed"] for row in report["channels"]))
        self.assertEqual(self.calls.count("/channels/30"), 1)
        self.assertFalse(any("messages" in path for path in self.calls))
        self.assertFalse(inspect(replace(self.config, allow_public_monitored_channels=False), self.get)["checks_passed"])

    def test_public_flag_does_not_make_command_public(self):
        self.metadata["/channels/20"]["permission_overwrites"] = []
        report = inspect(self.config, self.get)
        self.assertFalse(report["checks_passed"])
        self.assertFalse(report["channels"][1]["everyone_hidden"])
        self.assertTrue(report["channels"][1]["private_required"])

    def test_excluded_category_denies_monitor_and_command(self):
        for channel in ("10", "20"):
            with self.subTest(channel=channel):
                self.metadata[f"/channels/{channel}"]["parent_id"] = "40"
                report = inspect(self.config, self.get)
                self.assertFalse(report["checks_passed"])
                row = next(row for row in report["channels"] if row["channel_id"] == channel)
                self.assertFalse(row["category_allowed"])
                self.metadata[f"/channels/{channel}"]["parent_id"] = "30"

    def test_known_uncategorized_channels_allowed(self):
        for channel in ("10", "20"):
            self.metadata[f"/channels/{channel}"]["parent_id"] = None
        report = inspect(self.config, self.get)
        self.assertTrue(report["checks_passed"])
        self.assertTrue(all(row["category_id"] is None for row in report["channels"]))
        self.assertNotIn("/channels/30", self.calls)

    def test_guarded_scope_rejects_missing_or_malformed_parent_for_either_channel(self):
        for config in (self.config, replace(self.config, allow_public_monitored_channels=False),
                       replace(self.config, excluded_category_ids=())):
            for channel in ("10", "20"):
                original = self.metadata[f"/channels/{channel}"]
                for value in ("missing", "", "0", "030", "category-secret", 30, True, [], {}):
                    with self.subTest(channel=channel, value=value, config=config):
                        current = dict(original, parent_id=value)
                        if value == "missing":
                            del current["parent_id"]
                        self.metadata[f"/channels/{channel}"] = current
                        with self.assertRaises(PreflightError) as error:
                            inspect(config, self.get)
                        self.assertNotIn("category-secret", str(error.exception))
                self.metadata[f"/channels/{channel}"] = original

    def test_parent_must_resolve_to_category_in_same_guild(self):
        for parent in ({"id": "31", "guild_id": "1", "type": 4},
                       {"id": "30", "guild_id": "2", "type": 4},
                       {"id": "30", "guild_id": "1", "type": 0}, {}, None):
            with self.subTest(parent=parent):
                self.metadata["/channels/30"] = parent
                with self.assertRaises(PreflightError):
                    inspect(self.config, self.get)

    def test_public_opt_in_preserves_bot_permissions_and_type_guards(self):
        self.metadata["/guilds/1"]["roles"][1]["permissions"] = str(ADMIN)
        self.assertFalse(inspect(self.config, self.get)["checks_passed"])
        self.metadata["/guilds/1"]["roles"][1]["permissions"] = str(SEND)
        for denied in (VIEW, HISTORY):
            self.metadata["/channels/10"]["permission_overwrites"] = [
                {"id": "99", "type": 1, "allow": "0", "deny": str(denied)}]
            with self.subTest(denied=denied):
                self.assertFalse(inspect(self.config, self.get)["checks_passed"])
        self.metadata["/channels/10"]["permission_overwrites"] = []
        for kind in (5, 10, 11, 12, 15, 2, False, "0"):
            self.metadata["/channels/10"]["type"] = kind
            with self.subTest(kind=kind), self.assertRaises(PreflightError):
                inspect(self.config, self.get)

    def test_frozen_channel_becoming_private_fails_public_mode_but_legacy_private_remains_valid(self):
        self.metadata["/channels/10"]["permission_overwrites"] = deepcopy(
            self.metadata["/channels/20"]["permission_overwrites"])
        self.assertFalse(inspect(self.config, self.get)["checks_passed"])
        self.assertTrue(inspect(replace(self.config, allow_public_monitored_channels=False), self.get)["checks_passed"])


if __name__ == "__main__":
    unittest.main()
