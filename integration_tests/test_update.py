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

from liberdus_moderator.config import Config, StorageSettings
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
        self.manifest.write_text(self.manifest.read_text().replace("0.3.1", "0.3.0"))
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
        self.assertEqual(yaml.safe_load(self.manifest.read_text())["version"], "0.3.1")
        self.assertEqual((Path(result["plugin_backup"]) / "previous-version-marker").read_text(), "keep in backup")
        self.assertEqual(self.preserved(), before)
        with self.assertRaisesRegex(ValueError, "requires an existing version"):
            update(self.bundle, self.home)

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
        self.assertEqual(yaml.safe_load(self.manifest.read_text())["version"], "0.3.0")
        self.assertFalse(list(self.profile.glob(".liberdus-update-backup-*")))

    def test_bad_archive_cannot_escape_stage_or_replace_existing_code(self):
        with zipfile.ZipFile(self.bundle, "a") as archive:
            archive.writestr("plugin/../../escape", "bad")
        with self.assertRaisesRegex(ValueError, "Unexpected plugin archive member"):
            update(self.bundle, self.home)
        self.assertEqual(yaml.safe_load(self.manifest.read_text())["version"], "0.3.0")
        self.assertFalse(list(self.target.parent.glob(".liberdus-update-stage-*")))

    def test_failed_import_leaves_previous_plugin_and_profile(self):
        before = self.preserved()
        with patch("liberdus_moderator.update_pilot.subprocess.run") as run:
            run.return_value.returncode = 1
            with self.assertRaisesRegex(ValueError, "import or isolated self-test"):
                update(self.bundle, self.home)
        self.assertEqual(yaml.safe_load(self.manifest.read_text())["version"], "0.3.0")
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
        self.assertEqual(yaml.safe_load(self.manifest.read_text())["version"], "0.3.0")
        self.assertTrue((self.target / "previous-version-marker").exists())


if __name__ == "__main__":
    unittest.main()
