import importlib
import inspect
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml
from gateway.config import PlatformConfig

from liberdus_moderator.hermes_adapter import VERIFIED_COMMIT
from liberdus_moderator.install_pilot import install
from scripts.build_pilot_bundle import build

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(inspect.getfile(PlatformConfig)).resolve().parents[1]


class InstallationTests(unittest.TestCase):
    def test_install_preserves_other_configuration_and_stays_disabled(self):
        real_run = subprocess.run
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / ".hermes"
            profile = home / "profiles/liberdus-mod"
            profile.mkdir(parents=True)
            (home / "hermes-agent").symlink_to(SOURCE)
            default_text = yaml.safe_dump({"platforms": {"discord": {"enabled": False}, "telegram": {"enabled": True}}})
            (home / "config.yaml").write_text(default_text)
            (profile / "config.yaml").write_text(yaml.safe_dump({"platforms": {"discord": {"enabled": False}}, "model": "preserve-this"}))
            (profile / ".env").write_text("DISCORD_BOT_TOKEN=fake-not-for-reading\n")
            policy = Path(directory) / "policy.toml"
            policy.write_text((ROOT / "examples/config.toml").read_text()
                .replace('operator_role_ids = ["301"]', 'operator_role_ids = []').replace('log_channel_id = "202"\n', '')
                .replace('database_path = "state/moderation.sqlite3"', f'database_path = "{profile}/state/moderation.sqlite3"'))
            bundle = Path(directory) / "install.pyz"
            build(ROOT, policy, bundle)
            def run(command, **kwargs):
                if command[0] == "git":
                    return subprocess.CompletedProcess(command, 0, VERIFIED_COMMIT + "\n", "")
                return real_run(command, **kwargs)
            with patch("liberdus_moderator.install_pilot.subprocess.run", side_effect=run):
                result = install(bundle, home)
            self.assertTrue(result["installed"])
            self.assertFalse(result["platform_enabled"])
            self.assertFalse(result["gateway_restarted"])
            updated = yaml.safe_load((profile / "config.yaml").read_text())
            self.assertEqual(updated["model"], "preserve-this")
            self.assertFalse(updated["platforms"]["liberdus_moderator"]["enabled"])
            self.assertEqual((home / "config.yaml").read_text(), default_text)
            self.assertEqual((profile / ".env").read_text(), "DISCORD_BOT_TOKEN=fake-not-for-reading\n")
            self.assertEqual((profile / "config.yaml").stat().st_mode & 0o777, 0o600)
            # Fresh process: use actual discovery and config loading, no manual registration.
            code = '''from gateway.config import load_gateway_config, Platform
config = load_gateway_config()
assert config.platforms[Platform("liberdus_moderator")].enabled is False
assert config.platforms[Platform.DISCORD].enabled is False
print("COLD_START_DISABLED_OK")
'''
            check = real_run([sys.executable, "-B", "-c", code], env={"HOME": directory, "HERMES_HOME": str(profile), "PYTHONPATH": str(SOURCE), "PATH": "/usr/bin:/bin"},
                             cwd=directory, capture_output=True, text=True, timeout=30)
            self.assertEqual(check.returncode, 0, check.stderr)
            self.assertIn("COLD_START_DISABLED_OK", check.stdout)
            with patch("liberdus_moderator.install_pilot.subprocess.run", side_effect=run):
                with self.assertRaises(ValueError):
                    install(bundle, home)

    def test_active_stock_discord_aborts_before_creating_plugin_files(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            profile = home / "profiles/liberdus-mod"
            profile.mkdir(parents=True)
            (home / "config.yaml").write_text("platforms:\n  discord:\n    enabled: true\n")
            (profile / "config.yaml").write_text("platforms:\n  discord:\n    enabled: false\n")
            with self.assertRaises(ValueError):
                install(Path("unused.pyz"), home)
            self.assertFalse((profile / "plugins").exists())


if __name__ == "__main__":
    unittest.main()
