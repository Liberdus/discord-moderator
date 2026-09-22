"""Configuration edits validate real policy and fresh metadata, without Discord I/O."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from liberdus_moderator.config import Config, ClassifierSettings
from liberdus_moderator.instance import create_instance, load_policy
from liberdus_moderator.remote_settings import candidate, validate_edit, persist_change, AUDIT_KEY
from liberdus_moderator.preflight import VIEW, SEND, HISTORY
from liberdus_moderator.secure_files import SetupError, write_private
from liberdus_moderator.configure_jev import policy_text
from liberdus_moderator.storage import Store


def inventory():
    return {
        "/users/@me": {"id": "99", "bot": True},
        "/guilds/1": {"id": "1", "owner_id": "98", "roles": [
            {"id": "1", "permissions": str(VIEW | SEND | HISTORY)},
            {"id": "5", "permissions": str(VIEW | SEND | HISTORY | (1 << 13))},
            {"id": "6", "permissions": str(1 << 5)}]},
        "/guilds/1/members/99": {"user": {"id": "99", "bot": True}, "roles": ["5"]},
        "/guilds/1/members/98": {"user": {"id": "98", "bot": False}, "roles": []},
        "/guilds/1/members/97": {"user": {"id": "97", "bot": False}, "roles": ["5"]},
        "/guilds/1/channels": [
            {"id": "10", "type": 0, "parent_id": None, "name": "general", "permission_overwrites": []},
            {"id": "30", "type": 0, "parent_id": None, "name": "other", "permission_overwrites": []},
            {"id": "20", "type": 0, "parent_id": None, "name": "staff", "permission_overwrites": [
                {"id": "1", "type": 0, "allow": "0", "deny": str(VIEW)},
                {"id": "5", "type": 0, "allow": str(VIEW), "deny": "0"}]},
            {"id": "40", "type": 4, "parent_id": None, "name": "category", "permission_overwrites": []},
        ],
    }


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.policy = Config("1", "99", ("10",), ("20",), ("98",), schema_version=2,
                             explicit_channel_scope=True, allow_public_monitored_channels=True,
                             ai_enabled=True, classifier=ClassifierSettings(mode="report_only", max_daily_calls=100,
                                 max_total_calls=1000, daily_budget_microusd=1_000_000, total_budget_microusd=4_000_000))
        self.data = inventory()

    def validate(self, operation, values, actor="98"):
        new = candidate(self.policy, operation, values, actor)
        return validate_edit(self.policy, new, operation, values, actor, lambda path: deepcopy(self.data[path]))

    def test_allowlisted_edits_preserve_identity_secrets_scope_flags_and_other_policy(self):
        for operation, values, field in (
            ("monitor", {"action": "add", "channel": "30"}, "monitored_channel_ids"),
            ("staff-channel", {"channel": "30"}, "command_channel_ids"),
            ("operator", {"action": "add", "user": "97"}, "operator_user_ids"),
            ("category", {"action": "add", "boundary": "excluded", "category": "40"}, "excluded_category_ids"),
            ("exempt-role", {"action": "add", "role": "5"}, "classifier"),
            ("budget", {"daily": 2, "lifetime": 5}, "classifier"),
            ("call-limits", {"daily": 200, "lifetime": 2000}, "classifier"),
        ):
            with self.subTest(operation=operation):
                new = candidate(self.policy, operation, values, "98")
                self.assertEqual(replace(new, **{field: getattr(self.policy, field)}), self.policy)
        for operation in ("token", "guild_id", "bot_user_id", "endpoint", "storage", "threshold"):
            with self.assertRaises(SetupError):
                candidate(self.policy, operation, {}, "98")

    def test_fresh_metadata_accepts_owner_or_manage_server_but_rejects_plain_operator(self):
        self.assertTrue(self.validate("monitor", {"action": "add", "channel": "30"})["ok"])
        self.data["/guilds/1"]["owner_id"] = "90"
        with self.assertRaises(SetupError):
            self.validate("monitor", {"action": "add", "channel": "30"})
        self.data["/guilds/1/members/98"]["roles"] = ["5", "6"]
        self.assertTrue(self.validate("monitor", {"action": "add", "channel": "30"})["ok"])
        self.data["/guilds/1/members/98"]["user"]["bot"] = True
        with self.assertRaises(SetupError):
            self.validate("monitor", {"action": "add", "channel": "30"})

    def test_privacy_membership_categories_and_deletion_permissions_are_checked(self):
        with self.assertRaises(SetupError):
            self.validate("staff-channel", {"channel": "30"})  # public
        self.data["/guilds/1/members/97"]["user"]["bot"] = True
        with self.assertRaises(SetupError):
            self.validate("operator", {"action": "add", "user": "97"})
        with self.assertRaises(SetupError):
            self.validate("category", {"action": "add", "boundary": "excluded", "category": "30"})
        with self.assertRaises(SetupError):
            self.validate("exempt-role", {"action": "add", "role": "1"})
        self.policy = replace(self.policy, actions_enabled=True, allow_public_deletion=True)
        self.data["/guilds/1"]["roles"][1]["permissions"] = str(VIEW | SEND | HISTORY)
        with self.assertRaises(SetupError):
            self.validate("monitor", {"action": "add", "channel": "30"})

    def test_no_self_lockout_last_monitor_removal_or_invalid_budget(self):
        for operation, values in (
            ("operator", {"action": "remove", "user": "98"}),
            ("monitor", {"action": "remove", "channel": "10"}),
            ("budget", {"daily": 10, "lifetime": 4}),
            ("budget", {"daily": float("nan"), "lifetime": 4}),
            ("budget", {"daily": 11, "lifetime": 20}),
            ("call-limits", {"daily": 10001, "lifetime": 20000}),
        ):
            with self.subTest(operation=operation, values=values), self.assertRaises(ValueError):
                candidate(self.policy, operation, values, "98")

    def test_alert_role_requires_staff_visibility_and_mention_permission(self):
        values = {"action": "set", "role": "5"}
        with self.assertRaises(SetupError):
            self.validate("alert-role", values)
        self.data["/guilds/1"]["roles"][1]["mentionable"] = True
        self.assertTrue(self.validate("alert-role", values)["ok"])
        with self.assertRaises(SetupError):
            self.validate("alert-role", {"action": "set", "role": "1"})
        self.data["/guilds/1/channels"][2]["permission_overwrites"].pop()
        with self.assertRaises(SetupError):
            self.validate("alert-role", values)
        configured = candidate(self.policy, "alert-role", values, "98")
        self.assertIsNone(candidate(configured, "alert-role", {"action": "off"}, "98").rules.review_alert_role_id)

    def test_atomic_policy_change_preserves_history_flags_counters_and_records_actor(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root) / "instance"
            create_instance(home, self.policy, {"discord": "fake-discord", "jev": "fake-jev"})
            old = load_policy(home)
            new = candidate(old, "budget", {"daily": 2, "lifetime": 5}, "98")
            with Store(old.storage.database_path) as store:
                store.set_setting("shared_total_calls", 71)
                store.set_setting("shared_total_reserved_microusd", 8170)
                store.set_setting("paused", True)
                persist_change(home, store, old, new, "98", "budget", {"daily": 2, "lifetime": 5}, "700", 1.0)
                self.assertEqual(load_policy(home), new)
                self.assertEqual(store.get_setting("shared_total_calls"), 71)
                self.assertEqual(store.get_setting("shared_total_reserved_microusd"), 8170)
                self.assertTrue(store.get_setting("paused"))
                record = store.get_setting(AUDIT_KEY)[-1]
                self.assertEqual((record["actor"], record["state"]), ("98", "saved"))
                with self.assertRaises(SetupError):
                    persist_change(home, store, old, new, "98", "budget", {}, "701", 2.0)
            self.assertEqual((home / "moderation.toml").stat().st_mode & 0o777, 0o600)

    def test_failed_file_write_retains_policy_and_unconfirmed_audit(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root) / "instance"
            create_instance(home, self.policy, {"discord": "fake-discord", "jev": "fake-jev"})
            old = load_policy(home)
            new = candidate(old, "budget", {"daily": 2, "lifetime": 5}, "98")
            with Store(old.storage.database_path) as store, \
                    patch("liberdus_moderator.secure_files.os.replace", side_effect=OSError("synthetic failure")):
                with self.assertRaises(OSError):
                    persist_change(home, store, old, new, "98", "budget", {}, "700", 1.0)
                self.assertEqual(load_policy(home), old)
                self.assertEqual(store.get_setting(AUDIT_KEY)[-1]["state"], "requested")
