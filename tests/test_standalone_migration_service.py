from contextlib import closing
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
import zipfile
from unittest.mock import Mock, patch

from liberdus_moderator.config import Config, ClassifierSettings, StorageSettings
from liberdus_moderator.configure_jev import policy_text
from liberdus_moderator.credentials import Credentials
from liberdus_moderator.instance import load_policy
from liberdus_moderator.linux_service import service_unit
from liberdus_moderator.migrate import migrate, profile_secrets
from liberdus_moderator.secure_files import SetupError, checked_directory, file_lock, write_private
from liberdus_moderator.storage import Store


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = checked_directory(self.root / "hermes-profile", create=True)
        self.destination = self.root / "standalone"
        checked_directory(self.profile / "state", create=True)
        write_private(self.profile / "config.yaml", "platforms:\n  liberdus_moderator:\n    enabled: false\n")
        write_private(self.profile / ".env", 'DISCORD_BOT_TOKEN="fake-discord"\nTYPESAFE_API_KEY="fake-jev"\nUNRELATED_SECRET="do-not-copy"\n')
        classifier = ClassifierSettings(mode="report_only", max_daily_calls=100, max_total_calls=1000,
                                        daily_budget_microusd=1000000, total_budget_microusd=4000000)
        self.policy = Config("1", "99", ("10",), ("20",), ("98",), schema_version=2,
                             ai_enabled=True, classifier=classifier, actions_enabled=True,
                             allow_public_monitored_channels=True, allow_public_deletion=True,
                             included_category_ids=("30",), excluded_category_ids=("40",),
                             storage=StorageSettings(database_path=str(self.profile / "state/moderation.sqlite3")))
        write_private(self.profile / "moderation.toml", policy_text(self.policy))
        with Store(self.policy.storage.database_path) as store:
            for key, value in {"guild_id": "1", "paused": True, "deletion_enabled": True,
                               "auto_delete_enabled": True, "timeout_enabled": False,
                               "shared_total_calls": 71, "shared_total_reserved_microusd": 8170}.items():
                store.set_setting(key, value)
            store.db.execute("CREATE TABLE migration_evidence_check(id TEXT PRIMARY KEY, saved_text TEXT)")
            store.db.execute("INSERT INTO migration_evidence_check VALUES ('incident-test','saved message')")

    def test_migration_copies_full_history_flags_and_counters_without_source_mutation(self):
        before_policy = (self.profile / "moderation.toml").read_bytes()
        with closing(sqlite3.connect(self.policy.storage.database_path)) as db:
            before = db.execute("SELECT * FROM settings ORDER BY key").fetchall()
        result = migrate(self.profile, self.destination)
        self.assertTrue(result["history_preserved"])
        self.assertFalse(result["started"])
        migrated = load_policy(self.destination)
        self.assertEqual(replace(migrated, storage=self.policy.storage), self.policy)
        self.assertNotEqual(migrated.policy_hash, self.policy.policy_hash)
        self.assertEqual(before_policy, (self.profile / "moderation.toml").read_bytes())
        with closing(sqlite3.connect(migrated.storage.database_path)) as db:
            self.assertEqual(before, db.execute("SELECT * FROM settings ORDER BY key").fetchall())
            self.assertEqual(db.execute("SELECT saved_text FROM migration_evidence_check").fetchone()[0], "saved message")
        self.assertEqual(Credentials(self.destination).get("discord"), "fake-discord")
        self.assertEqual(Credentials(self.destination).get("jev"), "fake-jev")
        self.assertEqual({p.name for p in (self.destination / "credentials").iterdir()}, {"discord-token", "jev-key"})

    def test_enabled_or_running_source_cannot_be_migrated(self):
        write_private(self.profile / "config.yaml", "platforms:\n  liberdus_moderator:\n    enabled: true\n", replace=True)
        with self.assertRaises(SetupError):
            migrate(self.profile, self.destination)
        write_private(self.profile / "config.yaml", "platforms:\n  liberdus_moderator:\n    enabled: false\n", replace=True)
        with file_lock(self.profile / "state/moderation.lock"):
            with self.assertRaises(SetupError):
                migrate(self.profile, self.destination)
        self.assertFalse(self.destination.exists())

    def test_source_mismatch_and_destination_overwrite_rejected(self):
        with Store(self.policy.storage.database_path) as store:
            store.set_setting("guild_id", "2")
        with self.assertRaises(SetupError):
            migrate(self.profile, self.destination)
        with Store(self.policy.storage.database_path) as store:
            store.set_setting("guild_id", "1")
        migrate(self.profile, self.destination)
        with self.assertRaises(SetupError):
            migrate(self.profile, self.destination)

    def test_literal_parser_never_evaluates_shell_or_borrows_other_keys(self):
        values = profile_secrets(b'DISCORD_BOT_TOKEN="$(evil)"\nTYPESAFE_API_KEY="${OTHER_KEY}"\n', ai=True)
        self.assertEqual(values, {"discord": "$(evil)", "jev": "${OTHER_KEY}"})
        for data in (b"DISCORD_BOT_TOKEN=one\nDISCORD_BOT_TOKEN=two\n", b"OTHER_KEY=fake\n"):
            with self.assertRaises(SetupError):
                profile_secrets(data, ai=False)


class ServiceFilesTests(unittest.TestCase):
    def test_unit_uses_dedicated_account_credentials_and_minimal_privileges(self):
        unit = service_unit(ai=True)
        self.assertIn("User=liberdus-mod", unit)
        self.assertIn("LoadCredentialEncrypted=discord-token:", unit)
        self.assertIn("LoadCredentialEncrypted=jev-key:", unit)
        self.assertIn("NoNewPrivileges=true", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("LimitCORE=0", unit)
        self.assertNotIn("Environment=DISCORD", unit)
        self.assertNotIn("Environment=TYPESAFE", unit)
        self.assertNotIn("hermes", unit.lower())
        portable = service_unit(ai=False, encrypted=False)
        self.assertIn("LoadCredential=discord-token:", portable)
        self.assertNotIn("jev-key", portable)

    def test_installer_requires_explicit_admin_execution(self):
        from liberdus_moderator.linux_service import install
        with patch("liberdus_moderator.linux_service.os.geteuid", return_value=1001), \
                patch("liberdus_moderator.linux_service.subprocess.run") as process:
            with self.assertRaises(SetupError):
                install("untrusted.whl")
            process.assert_not_called()

    def test_encryption_uses_stdin_and_discards_sensitive_subprocess_output(self):
        from liberdus_moderator.linux_service import _store_credential
        with tempfile.TemporaryDirectory() as directory:
            seen = []
            def encrypt(arguments, *, data, timeout):
                self.assertEqual(data, b"secret-test-only")
                self.assertNotIn("secret-test-only", " ".join(arguments))
                self.assertEqual(arguments[-2], "-")
                seen.append(arguments)
                Path(arguments[-1]).write_bytes(b"encrypted-test-data")
            with patch("liberdus_moderator.linux_service.SECRETS", Path(directory)), \
                    patch("liberdus_moderator.linux_service._run", side_effect=encrypt):
                _store_credential("discord-token", b"secret-test-only", True)
            self.assertEqual((Path(directory) / "discord-token.cred").read_bytes(), b"encrypted-test-data")
            self.assertEqual(len(seen), 1)

    def test_plaintext_fallback_is_explicit_root_only_storage(self):
        from liberdus_moderator.linux_service import _store_credential
        with tempfile.TemporaryDirectory() as directory:
            with patch("liberdus_moderator.linux_service.SECRETS", Path(directory)), \
                    patch("liberdus_moderator.linux_service.os.fchown"):
                _store_credential("discord-token", b"secret-test-only", False)
            path = Path(directory) / "discord-token"
            self.assertEqual(path.stat().st_mode & 0o777, 0o400)

    def install_fixture(self, *, cancelled=False, start=False, start_failure=False):
        from contextlib import ExitStack
        from liberdus_moderator import __version__
        from liberdus_moderator.instance import create_instance
        from liberdus_moderator.linux_service import install
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        locations = {"HOME": root / "data", "PREFIX": root / "code", "SECRETS": root / "secrets", "UNIT": root / "bot.service"}
        wheel = root / f"liberdus_discord_moderator-{__version__}-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("liberdus_moderator/standalone.py", "# synthetic package")
        commands = []

        def run(arguments, **options):
            commands.append(arguments)
            if arguments[0] == "runuser":
                if cancelled:
                    return
                policy = Config("1", "99", ("10",), ("20",), ("98",), schema_version=2,
                                explicit_channel_scope=True, allow_public_monitored_channels=True)
                create_instance(locations["HOME"], policy, {"discord": "synthetic-secret"})
            elif arguments[0] == "systemd-creds":
                self.assertEqual(options["data"], b"synthetic-secret")
                self.assertNotIn("synthetic-secret", " ".join(arguments))
                Path(arguments[-1]).write_bytes(b"encrypted-synthetic")
            elif arguments[:3] == ["systemctl", "enable", "--now"] and start_failure:
                raise SetupError("synthetic start failure")

        with ExitStack() as stack:
            for name, value in locations.items():
                stack.enter_context(patch("liberdus_moderator.linux_service." + name, value))
            stack.enter_context(patch("liberdus_moderator.linux_service.os.geteuid", return_value=0))
            stack.enter_context(patch("liberdus_moderator.linux_service.os.chown"))
            stack.enter_context(patch("liberdus_moderator.linux_service.os.fchown"))
            stack.enter_context(patch("liberdus_moderator.linux_service.shutil.which", return_value="/usr/bin/test-command"))
            stack.enter_context(patch("liberdus_moderator.linux_service.pwd.getpwnam", side_effect=[KeyError(), SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())]))
            stack.enter_context(patch("liberdus_moderator.linux_service._fresh_directory", side_effect=lambda path, mode: path.mkdir(mode=mode)))
            stack.enter_context(patch("liberdus_moderator.linux_service._run", side_effect=run))
            if cancelled or start_failure:
                with self.assertRaises(SetupError):
                    install(wheel, start=start, output=lambda line: None)
            else:
                install(wheel, start=start, output=lambda line: None)
        return locations, commands

    def test_complete_service_install_publishes_credentials_before_start(self):
        paths, commands = self.install_fixture(start=True)
        self.assertEqual(json.loads((paths["HOME"] / "instance.json").read_text())["credentials"], "systemd")
        self.assertFalse((paths["HOME"] / "credentials").exists())
        self.assertEqual((paths["SECRETS"] / "discord-token.cred").read_bytes(), b"encrypted-synthetic")
        self.assertTrue(paths["UNIT"].is_file())
        self.assertEqual(commands[-1], ["systemctl", "enable", "--now", "liberdus-moderator.service"])

    def test_cancelled_service_setup_removes_only_new_installation(self):
        paths, commands = self.install_fixture(cancelled=True)
        self.assertTrue(all(not path.exists() for path in paths.values()))
        self.assertIn(["userdel", "liberdus-mod"], commands)

    def test_failed_service_start_preserves_published_data_for_recovery(self):
        paths, commands = self.install_fixture(start=True, start_failure=True)
        self.assertTrue(paths["UNIT"].is_file())
        self.assertTrue((paths["HOME"] / "state/moderation.sqlite3").is_file())
        self.assertNotIn(["userdel", "liberdus-mod"], commands)


if __name__ == "__main__":
    unittest.main()
