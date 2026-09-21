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

    def test_screening_setup_preserves_scope_and_uses_separate_fixed_trial_allowance(self):
        result = configure(self.profile, "screen")
        updated = Config.from_file(self.policy)
        self.assertEqual(updated.classifier.mode, "report_only")
        self.assertEqual(updated.classifier.daily_budget_microusd, 1000000)
        self.assertEqual(updated.classifier.total_budget_microusd, 4000000)
        self.assertEqual(updated.classifier.min_interval_seconds, 1)
        self.assertEqual(updated.monitored_channel_ids, self.config.monitored_channel_ids)
        self.assertEqual(updated.command_channel_ids, self.config.command_channel_ids)
        self.assertEqual(updated.rules, self.config.rules)
        self.assertFalse(updated.actions_enabled)
        self.assertFalse(result["provider_called"])
        self.assertFalse((self.profile / "state/moderation.sqlite3").exists())
        self.assertFalse((self.profile / ".env").exists())

    def test_owner_screening_activation_requires_exact_scope_new_code_and_released_lock(self):
        import fcntl, os
        from liberdus_moderator.configure_jev import configure_screening
        from liberdus_moderator.config import RuleSettings
        policy = replace(self.config, guild_id="746426387606274199", bot_user_id="1548537340870533150",
            monitored_channel_ids=("1551249559819264030", "1551249642216357908", "1551249693399584818"),
            command_channel_ids=("1551252553331642558",), operator_user_ids=("977263877391794217",),
            rules=RuleSettings())
        self.policy.write_text(policy_text(policy))
        (self.profile.parent.parent / "config.yaml").write_text("platforms:\n  discord:\n    enabled: false\n")
        manifest = self.profile / "plugins/liberdus-moderator/plugin.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("name: liberdus-moderator\nversion: 0.4.1\n")
        (self.profile / "state").mkdir()
        lock = self.profile / "state/moderation.lock"
        lock.touch()
        fd = os.open(lock,os.O_RDWR)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError): configure_screening(self.profile)
        finally:
            os.close(fd)
        self.policy.write_text(policy_text(replace(policy,monitored_channel_ids=(*policy.monitored_channel_ids,"999"))))
        with self.assertRaisesRegex(ValueError,"approved private"): configure_screening(self.profile)
        self.policy.write_text(policy_text(policy))
        manifest.write_text("name: liberdus-moderator\nversion: 0.3.5\n")
        with self.assertRaisesRegex(ValueError,"0.4.1"): configure_screening(self.profile)
        manifest.write_text("name: liberdus-moderator\nversion: 0.4.1\n")
        configure_screening(self.profile)
        self.assertEqual(Config.from_file(self.policy).classifier.mode,"report_only")
        self.assertFalse((self.profile / "state/moderation.sqlite3").exists())

    def test_role_setup_changes_only_exemption_and_is_idempotent(self):
        configure(self.profile,"screen")
        before=Config.from_file(self.policy)
        configure(self.profile,"exempt-role")
        after=Config.from_file(self.policy)
        self.assertEqual(after,replace(before,classifier=replace(before.classifier,
            exempt_role_ids=("1302455329795342377",))))
        configure(self.profile,"exempt-role")
        self.assertEqual(Config.from_file(self.policy),after)
        self.assertFalse((self.profile / "state/moderation.sqlite3").exists())

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
