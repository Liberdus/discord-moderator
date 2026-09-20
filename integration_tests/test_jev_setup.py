from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from dotenv import dotenv_values
import yaml

from liberdus_moderator.config import Config, RuleSettings, CrosspostException, StorageSettings
from liberdus_moderator.configure_jev import configure, policy_text


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.profile = Path(self.temp.name) / "profiles/liberdus-mod"
        self.profile.mkdir(parents=True)
        self.profile.joinpath("config.yaml").write_text("platforms:\n  discord:\n    enabled: false\n  liberdus_moderator:\n    enabled: false\n")
        self.config = Config("1", "99", ("10", "11", "12"), ("20",), ("98",), schema_version=1, storage=StorageSettings(database_path=str(self.profile / "state/moderation.sqlite3")),
            rules=RuleSettings(approved_crossposts=(CrosspostException(("98",), ("10", "11"), "Hello \U0001f525\nweekly update"),)))
        self.policy = self.profile / "moderation.toml"
        self.policy.write_text(policy_text(self.config))

    def test_key_preserves_other_secrets_is_private_and_does_not_enable(self):
        env = self.profile / ".env"
        env.write_text("DISCORD_BOT_TOKEN='fake-discord'\nUNRELATED='keep'\nTYPESAFE_API_KEY='old-fake-key'\n")
        result = configure(self.profile, "key", key="new-fake-typesafe-key")
        self.assertFalse(result["platform_enabled"])
        self.assertNotIn("new-fake", str(result))
        self.assertEqual(dotenv_values(env), {"DISCORD_BOT_TOKEN": "fake-discord", "UNRELATED": "keep", "TYPESAFE_API_KEY": "new-fake-typesafe-key"})
        self.assertEqual(env.stat().st_mode & 0o777, 0o600)
        self.assertEqual(Config.from_file(self.policy), self.config)

    def test_migration_round_trip_retains_scope_rules_and_backups(self):
        limits = dict(max_daily_calls=100, max_total_calls=1000, daily_budget_microusd=50000, total_budget_microusd=250000)
        result = configure(self.profile, "shadow", limits=limits)
        self.assertTrue((Path(result["backup"]) / "moderation.toml").is_file())
        updated = Config.from_file(self.policy)
        self.assertEqual(updated.rules, self.config.rules)
        self.assertEqual(updated.monitored_channel_ids, self.config.monitored_channel_ids)
        self.assertTrue(updated.ai_enabled)
        configure(self.profile, "off")
        self.assertFalse(Config.from_file(self.policy).ai_enabled)

    def test_active_profile_and_symlink_refused(self):
        raw = yaml.safe_load((self.profile / "config.yaml").read_text())
        raw["platforms"]["liberdus_moderator"]["enabled"] = True
        (self.profile / "config.yaml").write_text(yaml.safe_dump(raw))
        with self.assertRaises(ValueError): configure(self.profile, "key", key="fake-key-long-enough")
        self.assertFalse((self.profile / ".env").exists())
        raw["platforms"]["liberdus_moderator"]["enabled"] = False
        (self.profile / "config.yaml").write_text(yaml.safe_dump(raw))
        other = Path(self.temp.name) / "other.env"
        other.write_text("UNCHANGED=true\n")
        (self.profile / ".env").symlink_to(other)
        with self.assertRaises(ValueError): configure(self.profile, "key", key="fake-key-long-enough")
        self.assertEqual(other.read_text(), "UNCHANGED=true\n")

    def test_results_reads_while_active_without_creating_or_modifying_state(self):
        from liberdus_moderator.engine import Engine
        from liberdus_moderator.storage import Store
        self.assertEqual(configure(self.profile, "results")["attempts"], 0)
        database = self.profile / "state/moderation.sqlite3"
        self.assertFalse(database.exists())
        with Store(str(database)) as store:
            Engine(self.config, store)
            before = list(store.db.iterdump())
            raw = yaml.safe_load((self.profile / "config.yaml").read_text())
            raw["platforms"]["liberdus_moderator"]["enabled"] = True
            (self.profile / "config.yaml").write_text(yaml.safe_dump(raw))
            self.assertEqual(configure(self.profile, "results")["records"], [])
            self.assertEqual(before, list(store.db.iterdump()))


if __name__ == "__main__":
    unittest.main()
