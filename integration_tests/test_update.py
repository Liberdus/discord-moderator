import fcntl
import inspect
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import yaml
from gateway.config import PlatformConfig

from dataclasses import replace
from liberdus_moderator.config import Config, StorageSettings, ClassifierSettings
from liberdus_moderator.configure_jev import policy_text
from liberdus_moderator.update_pilot import update
from scripts.build_pilot_bundle import build


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(inspect.getfile(PlatformConfig)).resolve().parents[1]


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / ".hermes"
        self.profile = self.home / "profiles/liberdus-mod"
        self.target = self.profile / "plugins/liberdus-moderator"
        self.target.parent.mkdir(parents=True)
        shutil.copytree(ROOT / "hermes_plugin", self.target, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "liberdus_moderator", self.target / "liberdus_moderator",
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.manifest = self.target / "plugin.yaml"
        self.manifest.write_text(self.manifest.read_text().replace("0.8.0", "0.3.4"))
        (self.target / "previous-version-marker").write_text("keep in backup")
        (self.home / "hermes-agent").symlink_to(SOURCE)
        self.config = self.profile / "config.yaml"
        settings = {"platforms": {"discord": {"enabled": False}, "liberdus_moderator": {"enabled": False}},
                    "model": "preserve-this"}
        self.config.write_text(yaml.safe_dump(settings))
        (self.home / "config.yaml").write_text("platforms:\n  discord:\n    enabled: false\n  telegram:\n    enabled: true\n")
        self.policy = self.profile / "moderation.toml"
        self.policy.write_text(policy_text(Config("1", "99", ("10", "11", "12"), ("20",), ("98",),
            storage=StorageSettings(database_path=str(self.profile / "state/moderation.sqlite3")), schema_version=2)))
        (self.profile / "state").mkdir()
        (self.profile / "state/moderation.lock").touch()
        (self.profile / "state/moderation.sqlite3").write_bytes(b"do not read, open or change the live database")
        (self.profile / ".env").write_text("DISCORD_BOT_TOKEN=do-not-read\n")
        self.bundle = Path(self.temp.name) / "update.pyz"
        build(ROOT, self.policy, self.bundle, update=True)
        self.verify = patch("liberdus_moderator.hermes_adapter.verify_runtime")
        self.verify.start()
        self.addCleanup(self.verify.stop)

    def preserved(self):
        return {str(p): p.read_bytes() for p in (
            self.home / "config.yaml", self.config, self.policy, self.profile / ".env",
            self.profile / "state/moderation.sqlite3",
        )}

    def test_update_validates_real_loader_and_selftest_preserving_profile(self):
        before = self.preserved()
        result = update(self.bundle, self.home)
        self.assertTrue(result["updated"])
        self.assertEqual(result["isolated_selftest"], "9/9 passed")
        self.assertFalse(result["platform_enabled"])
        self.assertFalse(result["gateway_restarted"])
        self.assertEqual(yaml.safe_load(self.manifest.read_text())["version"], "0.8.0")
        self.assertEqual((Path(result["plugin_backup"]) / "previous-version-marker").read_text(), "keep in backup")
        self.assertEqual(self.preserved(), before)
        with self.assertRaisesRegex(ValueError, "requires an existing version"):
            update(self.bundle, self.home)

    def test_shadow_update_preserves_key_policy_database_and_budgets(self):
        policy = Config.from_file(self.policy)
        policy = replace(policy, ai_enabled=True, classifier=ClassifierSettings(mode="shadow",
            max_daily_calls=100, max_total_calls=1000, daily_budget_microusd=50000, total_budget_microusd=250000))
        self.policy.write_text(policy_text(policy))
        (self.profile / ".env").write_text("DISCORD_BOT_TOKEN=synthetic-bot\nTYPESAFE_API_KEY=synthetic-jev\n")
        before = self.preserved()
        result = update(self.bundle, self.home)
        self.assertEqual(self.preserved(), before)
        self.assertEqual(result["classifier_mode"], "shadow")
        self.assertFalse(result["provider_called"])
        self.assertFalse(result["database_changed"])
        self.assertFalse(result["policy_changed"])
        self.assertFalse(result["token_read"])
        self.assertEqual(result["version"], "0.8.0")

    def test_current_035_pilot_can_upgrade(self):
        self.manifest.write_text(self.manifest.read_text().replace("0.3.4", "0.3.5"))
        self.assertEqual(update(self.bundle, self.home)["version"], "0.8.0")

    def test_current_042_pilot_can_upgrade(self):
        self.manifest.write_text(self.manifest.read_text().replace("0.3.4", "0.4.2"))
        self.assertEqual(update(self.bundle, self.home)["version"], "0.8.0")

    def test_050_actions_enabled_update_preserves_policy_flags_and_budget(self):
        self.manifest.write_text(self.manifest.read_text().replace("0.3.4", "0.5.0"))
        policy=replace(Config.from_file(self.policy),actions_enabled=True,ai_enabled=True,
            classifier=ClassifierSettings(mode="report_only",max_daily_calls=10000,max_total_calls=100000,
                daily_budget_microusd=1000000,total_budget_microusd=4000000,exempt_role_ids=('77',)))
        self.policy.write_text(policy_text(policy))
        before=self.preserved()
        self.assertEqual(update(self.bundle,self.home)["version"],"0.8.0")
        self.assertEqual(self.preserved(),before)

    def test_051_can_upgrade_preserving_existing_state(self):
        self.manifest.write_text(self.manifest.read_text().replace("0.3.4", "0.5.1"))
        before=self.preserved()
        self.assertEqual(update(self.bundle,self.home)["version"],"0.8.0")
        self.assertEqual(self.preserved(),before)

    def test_052_can_upgrade_preserving_existing_state(self):
        self.manifest.write_text(self.manifest.read_text().replace("0.3.4", "0.5.2"))
        before=self.preserved()
        self.assertEqual(update(self.bundle,self.home)["version"],"0.8.0")
        self.assertEqual(self.preserved(),before)

    def test_053_can_upgrade_preserving_actions_and_runtime_state(self):
        self.manifest.write_text(self.manifest.read_text().replace("0.3.4", "0.5.3"))
        policy=replace(Config.from_file(self.policy),actions_enabled=True,ai_enabled=True,
            classifier=ClassifierSettings(mode="report_only",max_daily_calls=10000,max_total_calls=100000,
                daily_budget_microusd=1000000,total_budget_microusd=4000000,exempt_role_ids=('77',)))
        self.policy.write_text(policy_text(policy))
        before=self.preserved()
        self.assertEqual(update(self.bundle,self.home)["version"],"0.8.0")
        self.assertEqual(self.preserved(),before)

    def test_054_upgrade_preserves_live_flags_budget_keys_and_pending_state(self):
        import sqlite3
        self.manifest.write_text(self.manifest.read_text().replace("0.3.4", "0.5.4"))
        policy=replace(Config.from_file(self.policy),actions_enabled=True,ai_enabled=True,
            classifier=ClassifierSettings(mode="report_only",max_daily_calls=10000,max_total_calls=100000,
                daily_budget_microusd=1000000,total_budget_microusd=4000000,exempt_role_ids=('77',)))
        self.policy.write_text(policy_text(policy))
        (self.profile / ".env").write_text("DISCORD_BOT_TOKEN=synthetic-bot\nTYPESAFE_API_KEY=synthetic-jev\n")
        database=self.profile / "state/moderation.sqlite3"
        database.unlink()
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)")
            connection.executemany("INSERT INTO settings VALUES(?,?)", (
                ('deletion_enabled','true'),('auto_delete_enabled','true'),('timeout_enabled','false'),
                ('role_exemption_enabled','false'),('screening_total_reserved_microusd','19234'),
                ('screening_total_calls','42'),('paused','false')))
            connection.execute("CREATE TABLE pending_review(incident_id TEXT,revision INTEGER)")
            connection.execute("INSERT INTO pending_review VALUES('saved-incident',3)")
        before=self.preserved()
        result=update(self.bundle,self.home)
        self.assertEqual(result["version"],"0.8.0")
        self.assertEqual(result["isolated_selftest"],"9/9 passed")
        self.assertEqual(self.preserved(),before)
        self.assertFalse(result["database_changed"])
        self.assertFalse(result["token_read"])
        self.assertFalse(result["gateway_restarted"])
        self.assertTrue((Path(result["plugin_backup"])/"previous-version-marker").exists())

    def test_055_upgrade_preserves_private_policy_health_usage_and_all_flags(self):
        from liberdus_moderator.storage import Store
        from liberdus_moderator.engine import Engine
        from liberdus_moderator.health import HealthMonitor
        self.manifest.write_text(self.manifest.read_text().replace("0.3.4", "0.5.5"))
        policy=replace(Config.from_file(self.policy),actions_enabled=True,ai_enabled=True,
            classifier=ClassifierSettings(mode="report_only",max_daily_calls=10000,max_total_calls=100000,
                daily_budget_microusd=1000000,total_budget_microusd=4000000,exempt_role_ids=('77',)))
        self.policy.write_text(policy_text(policy))
        (self.profile / ".env").write_text("DISCORD_BOT_TOKEN=synthetic-bot\nTYPESAFE_API_KEY=synthetic-jev\n")
        database=self.profile / "state/moderation.sqlite3"
        database.unlink()
        with Store(str(database)) as store:
            health=HealthMonitor(Engine(policy,store))
            health.record_failure('timeout')
            health.gap('disconnect')
            for key,value in (('deletion_enabled',True),('auto_delete_enabled',True),('timeout_enabled',False),
                    ('role_exemption_enabled',False),('paused',True),('screening_total_calls',60),
                    ('screening_total_reserved_microusd',4456)):
                store.set_setting(key,value)
        before=self.preserved()
        result=update(self.bundle,self.home)
        self.assertEqual(result["version"],"0.8.0")
        self.assertEqual(result["isolated_selftest"],"9/9 passed")
        self.assertEqual(self.preserved(),before)
        self.assertFalse(result["policy_changed"])
        self.assertFalse(result["database_changed"])
        self.assertFalse(result["token_read"])
        self.assertFalse(result["gateway_restarted"])
        self.assertEqual(yaml.safe_load(self.manifest.read_text())["version"],"0.8.0")

    def test_056_public_policy_upgrades_without_widening_scope_or_resetting_state(self):
        self.manifest.write_text(self.manifest.read_text().replace("0.3.4","0.5.6"))
        policy=replace(Config.from_file(self.policy),allow_public_monitored_channels=True,
                       excluded_category_ids=('1318586868136415333',))
        self.policy.write_text(policy_text(policy).replace('included_category_ids = []\n',''))
        before=self.preserved()
        self.assertEqual(update(self.bundle,self.home)['version'],'0.8.0')
        self.assertEqual(self.preserved(),before)

    def test_057_category_policy_upgrades_without_changing_live_settings(self):
        self.manifest.write_text(self.manifest.read_text().replace("0.3.4", "0.5.7"))
        policy = replace(Config.from_file(self.policy), allow_public_monitored_channels=True,
                         included_category_ids=('100',), excluded_category_ids=('200',))
        self.policy.write_text(policy_text(policy).replace('allow_public_deletion = false\n', ''))
        before = self.preserved()
        self.assertEqual(update(self.bundle, self.home)['version'], '0.8.0')
        self.assertEqual(self.preserved(), before)

    def test_058_public_deletion_upgrade_preserves_policy_flags_and_usage(self):
        self.manifest.write_text(self.manifest.read_text().replace("0.3.4", "0.5.8"))
        policy=replace(Config.from_file(self.policy),schema_version=2,actions_enabled=True,
            allow_public_monitored_channels=True,allow_public_deletion=True,
            included_category_ids=('100',),excluded_category_ids=('200',))
        self.policy.write_text(policy_text(policy))
        before=self.preserved()
        self.assertEqual(update(self.bundle,self.home)['version'],'0.8.0')
        self.assertEqual(self.preserved(),before)

    def test_missing_new_runtime_module_refuses_update_without_replacing_plugin(self):
        before=self.preserved()
        original=self.bundle.read_bytes()
        for module in ('health', 'summary', 'manual_delete', 'connection_health', 'review_display'):
            with self.subTest(module=module):
                self.bundle.write_bytes(original)
                with zipfile.ZipFile(self.bundle) as archive:
                    entries={name:archive.read(name) for name in archive.namelist()
                             if name != f'plugin/liberdus_moderator/{module}.py'}
                with zipfile.ZipFile(self.bundle,'w') as archive:
                    for name,payload in entries.items(): archive.writestr(name,payload)
                with self.assertRaisesRegex(ValueError,"import or isolated self-test"):
                    update(self.bundle,self.home)
                self.assertEqual(yaml.safe_load(self.manifest.read_text())["version"],"0.3.4")
                self.assertEqual(self.preserved(),before)
                self.assertFalse(list(self.profile.glob(".liberdus-update-backup-*")))

    def test_059_public_deletion_upgrade_preserves_policy_flags_and_usage(self):
        self.manifest.write_text(self.manifest.read_text().replace("0.3.4", "0.5.9"))
        policy = replace(Config.from_file(self.policy), schema_version=2, actions_enabled=True,
                         allow_public_monitored_channels=True, allow_public_deletion=True,
                         included_category_ids=('100',), excluded_category_ids=('200',))
        self.policy.write_text(policy_text(policy))
        before = self.preserved()
        self.assertEqual(update(self.bundle, self.home)['version'], '0.8.0')
        self.assertEqual(self.preserved(), before)

    def test_0510_layout_release_can_upgrade_without_changing_state(self):
        self.manifest.write_text(self.manifest.read_text().replace('0.3.4', '0.5.10'))
        before = self.preserved()
        self.assertEqual(update(self.bundle, self.home)['version'], '0.8.0')
        self.assertEqual(self.preserved(), before)

    def test_0511_receipts_release_can_upgrade_without_changing_state(self):
        self.manifest.write_text(self.manifest.read_text().replace('0.3.4', '0.5.11'))
        before = self.preserved()
        self.assertEqual(update(self.bundle, self.home)['version'], '0.8.0')
        self.assertEqual(self.preserved(), before)

    def test_0512_diagnostic_release_can_upgrade_without_changing_state(self):
        self.manifest.write_text(self.manifest.read_text().replace('0.3.4', '0.5.12'))
        before = self.preserved()
        self.assertEqual(update(self.bundle, self.home)['version'], '0.8.0')
        self.assertEqual(self.preserved(), before)

    def test_0513_staff_category_release_can_upgrade_without_changing_state(self):
        self.manifest.write_text(self.manifest.read_text().replace('0.3.4', '0.5.13'))
        before = self.preserved()
        self.assertEqual(update(self.bundle, self.home)['version'], '0.8.0')
        self.assertEqual(self.preserved(), before)

    def test_0514_interaction_release_can_upgrade_without_changing_state(self):
        self.manifest.write_text(self.manifest.read_text().replace('0.3.4', '0.5.14'))
        before = self.preserved()
        self.assertEqual(update(self.bundle, self.home)['version'], '0.8.0')
        self.assertEqual(self.preserved(), before)

    def test_0515_auto_delete_scope_release_can_upgrade_without_changing_state(self):
        self.manifest.write_text(self.manifest.read_text().replace('0.3.4', '0.5.15'))
        before = self.preserved()
        self.assertEqual(update(self.bundle, self.home)['version'], '0.8.0')
        self.assertEqual(self.preserved(), before)

    def test_0516_cards_release_can_upgrade_without_changing_state(self):
        self.manifest.write_text(self.manifest.read_text().replace('0.3.4', '0.5.16'))
        before = self.preserved()
        self.assertEqual(update(self.bundle, self.home)['version'], '0.8.0')
        self.assertEqual(self.preserved(), before)

    def test_060_release_can_upgrade_without_changing_state(self):
        self.manifest.write_text(self.manifest.read_text().replace('0.3.4', '0.6.0'))
        before = self.preserved()
        self.assertEqual(update(self.bundle, self.home)['version'], '0.8.0')
        self.assertEqual(self.preserved(), before)

    def test_current_041_pilot_can_upgrade(self):
        self.manifest.write_text(self.manifest.read_text().replace("0.3.4", "0.4.1"))
        self.assertEqual(update(self.bundle, self.home)["version"], "0.8.0")

    def test_061_release_can_upgrade_without_changing_state(self):
        self.manifest.write_text(self.manifest.read_text().replace('0.3.4', '0.6.1'))
        before = self.preserved()
        self.assertEqual(update(self.bundle, self.home)['version'], '0.8.0')
        self.assertEqual(self.preserved(), before)

    def test_071_release_can_upgrade_without_changing_state(self):
        self.manifest.write_text(self.manifest.read_text().replace('0.3.4', '0.7.1'))
        before = self.preserved()
        self.assertEqual(update(self.bundle, self.home)['version'], '0.8.0')
        self.assertEqual(self.preserved(), before)

    def test_072_release_can_upgrade_without_changing_state(self):
        self.manifest.write_text(self.manifest.read_text().replace('0.3.4', '0.7.2'))
        before = self.preserved()
        self.assertEqual(update(self.bundle, self.home)['version'], '0.8.0')
        self.assertEqual(self.preserved(), before)

    def test_live_040_screening_update_preserves_settings(self):
        self.manifest.write_text(self.manifest.read_text().replace("0.3.4", "0.4.0"))
        policy=Config.from_file(self.policy)
        policy=replace(policy,ai_enabled=True,classifier=ClassifierSettings(mode="report_only",
            max_daily_calls=10000,max_total_calls=100000,daily_budget_microusd=1000000,total_budget_microusd=4000000))
        self.policy.write_text(policy_text(policy))
        before=self.preserved()
        self.assertEqual(update(self.bundle,self.home)["version"],"0.8.0")
        self.assertEqual(self.preserved(),before)

    def test_original_030_pilot_can_also_upgrade(self):
        self.manifest.write_text(self.manifest.read_text().replace("0.3.4", "0.3.0"))
        self.assertEqual(update(self.bundle, self.home)["version"], "0.8.0")

    def test_enabled_or_still_running_pilot_is_refused(self):
        original = self.config.read_text()
        self.config.write_text(original.replace("liberdus_moderator:\n    enabled: false", "liberdus_moderator:\n    enabled: true"))
        with self.assertRaisesRegex(ValueError, "Disable liberdus_moderator"):
            update(self.bundle, self.home)
        self.config.write_text(original)
        lock = os.open(self.profile / "state/moderation.lock", os.O_RDWR)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, "still running"):
                update(self.bundle, self.home)
        finally:
            os.close(lock)
        self.assertEqual(yaml.safe_load(self.manifest.read_text())["version"], "0.3.4")
        self.assertFalse(list(self.profile.glob(".liberdus-update-backup-*")))

    def test_bad_archive_cannot_escape_stage_or_replace_existing_code(self):
        with zipfile.ZipFile(self.bundle, "a") as archive:
            archive.writestr("plugin/../../escape", "bad")
        with self.assertRaisesRegex(ValueError, "Unexpected plugin archive member"):
            update(self.bundle, self.home)
        self.assertEqual(yaml.safe_load(self.manifest.read_text())["version"], "0.3.4")
        self.assertFalse(list(self.target.parent.glob(".liberdus-update-stage-*")))

    def test_failed_import_leaves_previous_plugin_and_profile(self):
        before = self.preserved()
        with patch("liberdus_moderator.update_pilot.subprocess.run") as run:
            run.return_value.returncode = 1
            with self.assertRaisesRegex(ValueError, "import or isolated self-test"):
                update(self.bundle, self.home)
        self.assertEqual(yaml.safe_load(self.manifest.read_text())["version"], "0.3.4")
        self.assertEqual(self.preserved(), before)

    def test_publish_failure_restores_old_plugin(self):
        real_replace = os.replace

        def fail_new_publish(source, destination):
            if ".liberdus-update-stage-" in str(source):
                raise OSError("simulated publish failure")
            return real_replace(source, destination)

        with patch("liberdus_moderator.update_pilot.os.replace", side_effect=fail_new_publish):
            with self.assertRaisesRegex(OSError, "simulated publish failure"):
                update(self.bundle, self.home)
        self.assertEqual(yaml.safe_load(self.manifest.read_text())["version"], "0.3.4")
        self.assertTrue((self.target / "previous-version-marker").exists())


if __name__ == "__main__":
    unittest.main()
