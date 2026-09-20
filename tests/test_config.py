import copy
from dataclasses import replace
import tempfile
from pathlib import Path
import unittest

from liberdus_moderator.config import Config, RuleSettings, StorageSettings
from liberdus_moderator.models import MessageEvent


def config_data():
    return {
        "schema_version": 1,
        "policy_version": "pilot-1",
        "scope": {
            "guild_id": "1", "bot_user_id": "99",
            "monitored_channel_ids": ["10", "11", "12", "13"],
            "command_channel_ids": ["20"], "operator_user_ids": ["90"],
        },
    }


class ConfigTests(unittest.TestCase):
    def test_minimal_config_safe_defaults_and_stable_hash(self):
        config = Config.from_dict(config_data())
        self.assertEqual(config.mode, "report_only")
        self.assertFalse(config.ai_enabled)
        self.assertFalse(config.actions_enabled)
        self.assertFalse(config.logs_enabled)
        self.assertEqual(config.rules.repeat_min_channels, 3)
        self.assertEqual(config.storage.max_messages, 50000)
        self.assertEqual(config.policy_hash, Config.from_dict(config_data()).policy_hash)
        self.assertNotEqual(config.policy_hash, replace(config, policy_version="pilot-2").policy_hash)
        self.assertNotEqual(config.policy_hash, replace(config, monitored_channel_ids=("10",)).policy_hash)

    def test_missing_required_config_rejected(self):
        for key in ("schema_version", "policy_version", "scope"):
            data = config_data()
            del data[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                Config.from_dict(data)
        for key in ("guild_id", "bot_user_id", "monitored_channel_ids", "command_channel_ids", "operator_user_ids"):
            data = config_data()
            del data["scope"][key]
            with self.subTest(scope_key=key), self.assertRaises(ValueError):
                Config.from_dict(data)

    def test_operator_role_alone_suffices_and_private_log_may_share_command(self):
        data = config_data()
        data["scope"].update(operator_user_ids=[], operator_role_ids=["91"], log_channel_id="20")
        data["logs_enabled"] = True
        self.assertEqual(Config.from_dict(data).log_channel_id, "20")

    def test_scope_and_destination_checks_fail_closed(self):
        changes = [
            {"monitored_channel_ids": []}, {"command_channel_ids": []},
            {"operator_user_ids": []}, {"operator_user_ids": ["90", "90"]},
            {"command_channel_ids": ["10"]}, {"log_channel_id": "11"},
            {"guild_id": 1}, {"bot_user_id": True}, {"guild_id": "０１"},
            {"guild_id": "001"}, {"guild_id": "0"}, {"guild_id": "nope"},
        ]
        for update in changes:
            data = config_data()
            data["scope"].update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):
                Config.from_dict(data)
        data = config_data()
        data["logs_enabled"] = True
        with self.assertRaises(ValueError):
            Config.from_dict(data)

    def test_unknown_fields_cannot_silently_change_behavior(self):
        for section in (None, "scope", "rules", "storage"):
            data = config_data()
            target = data if section is None else data.setdefault(section, {})
            target["unrecognized_setting"] = True
            with self.subTest(section=section), self.assertRaises(ValueError):
                Config.from_dict(data)

    def test_configuration_list_and_text_bounds(self):
        config = Config.from_dict(config_data())
        for update in ({"guild_id": "1" * 21}, {"monitored_channel_ids": tuple(str(i) for i in range(1, 502))}, {"operator_role_ids": tuple(str(i) for i in range(1, 252))}, {"policy_version": "v" * 129}, {"policy_version": "v\x1b[31m"}):
            with self.subTest(update=list(update)), self.assertRaises(ValueError):
                replace(config, **update)
        with self.assertRaises(ValueError):
            RuleSettings(blocked_domains=tuple(f"domain{i}.invalid" for i in range(1001)))

    def test_rejects_enforcement_ai_and_malformed_flags(self):
        for key, value in (("ai_enabled", True), ("actions_enabled", True), ("mode", "enforce"), ("mode", []), ("logs_enabled", "false"), ("actions_enabled", 0), ("schema_version", True), ("schema_version", 3)):
            data = config_data()
            data[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                Config.from_dict(data)

    def test_threshold_and_storage_validation(self):
        for values in ({"repeat_min_channels": 1}, {"same_channel_min_messages": 1}, {"repeat_window_seconds": 0}, {"repeat_min_chars": True}, {"notification_cooldown_seconds": 0}, {"same_channel_window_seconds": 1.5}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                RuleSettings(**values)
        for values in ({"max_messages": 0}, {"max_pending_reports": True}, {"retention_seconds": -1}, {"database_path": ""}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                StorageSettings(**values)
        with self.assertRaises(ValueError):
            replace(Config.from_dict(config_data()), storage=StorageSettings(retention_seconds=10))

    def test_exact_domains_and_scoped_exceptions(self):
        self.assertEqual(RuleSettings(blocked_domains=["SCAM.invalid"]).blocked_domains, ("scam.invalid",))
        for domain in ("*.scam.invalid", "https://scam.invalid", "scam.invalid/path", "scam.invalid:443", "scam..invalid", "scam.invalid.", "-scam.invalid"):
            with self.subTest(domain=domain), self.assertRaises(ValueError):
                RuleSettings(blocked_domains=[domain])
        data = config_data()
        data["rules"] = {"approved_crossposts": [{"author_ids": ["90"], "channel_ids": ["10", "11"], "content": "Approved weekly event reminder"}]}
        self.assertEqual(Config.from_dict(data).rules.approved_crossposts[0].author_ids, ("90",))
        for mutation in ({"author_ids": []}, {"channel_ids": []}, {"content": ""}, {"channel_ids": ["20"]}, {"typo": 1}):
            changed = copy.deepcopy(data)
            changed["rules"]["approved_crossposts"][0].update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                Config.from_dict(changed)

    def test_toml_loader(self):
        toml = '''schema_version = 1
policy_version = "pilot-1"
mode = "off"
[scope]
guild_id = "1"
bot_user_id = "99"
monitored_channel_ids = ["10", "11", "12"]
command_channel_ids = ["20"]
operator_role_ids = ["91"]
[rules]
blocked_domains = ["scam.invalid"]
[[rules.approved_crossposts]]
author_ids = ["90"]
channel_ids = ["10", "11", "12"]
content = "Approved weekly event reminder"
[storage]
database_path = "state/test.sqlite3"
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(toml)
            config = Config.from_file(path)
        self.assertEqual(config.mode, "off")
        self.assertEqual(config.storage.database_path, "state/test.sqlite3")


class MessageTests(unittest.TestCase):
    def event(self, **changes):
        fields = dict(guild_id="1", channel_id="10", message_id="100", author_id="2", content="hello", created_at=100)
        fields.update(changes)
        return MessageEvent(**fields)

    def test_evidence_version_covers_changes_and_json_roundtrip(self):
        event = self.event(author_role_ids=("4", "3"))
        self.assertEqual(event, MessageEvent.from_dict(event.to_dict()))
        self.assertEqual(event.version, self.event(author_role_ids=("3", "4")).version)
        for change in ({"content": "hello!"}, {"edited_at": 101}, {"has_attachments": True}, {"author_role_ids": ("3",)}, {"is_webhook": True}):
            with self.subTest(change=change):
                self.assertNotEqual(event.version, self.event(**change).version)
        self.assertEqual(event.modified_at, 100)
        self.assertEqual(self.event(edited_at=110).modified_at, 110)

    def test_invalid_evidence_rejected_on_constructor_and_loader(self):
        for change in ({"created_at": True}, {"created_at": 0}, {"created_at": float("inf")}, {"created_at": float("nan")}, {"edited_at": 99}, {"author_id": 2}, {"content": None}, {"is_bot": "false"}, {"author_role_ids": "3"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.event(**change)
        data = self.event().to_dict()
        data["extra"] = "untrusted"
        with self.assertRaises(ValueError):
            MessageEvent.from_dict(data)
        with self.assertRaises(ValueError):
            MessageEvent.from_dict({})

    def test_message_bounds_apply_to_library_constructors(self):
        self.assertEqual(len(self.event(content="a" * 4000).content), 4000)
        for change in ({"content": "a" * 4001}, {"message_id": "1" * 21}, {"author_role_ids": tuple(str(i) for i in range(1, 252))}, {"created_at": 10 ** 400}):
            with self.subTest(change=list(change)), self.assertRaises(ValueError):
                self.event(**change)


if __name__ == "__main__":
    unittest.main()
