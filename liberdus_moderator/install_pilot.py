"""Owner-run installation of the pilot plugin, always disabled, without restart."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile


SMOKE = r'''
import os, sys
sys.path.insert(0, sys.argv[1])
def no_network(event, args):
    if event.startswith("socket."):
        raise RuntimeError("Network forbidden during installation validation")
sys.addaudithook(no_network)
from hermes_cli.plugins import PluginManager, PluginManifest
from gateway.platform_registry import platform_registry
from gateway.config import PlatformConfig
manager = PluginManager()
manifest = PluginManifest(name="liberdus-moderator", kind="platform", source="user", path=sys.argv[2])
manager._load_plugin(manifest)
loaded = manager._plugins.get("liberdus-moderator")
if loaded is None or not loaded.enabled or loaded.error:
    raise RuntimeError("Plugin did not load")
entry = platform_registry.get("liberdus_moderator")
assert entry is not None
assert entry.is_connected(PlatformConfig(enabled=True)) is False
adapter = entry.adapter_factory(PlatformConfig(enabled=False))
assert not adapter.__abstractmethods__
print("LIBERDUS_INSTALL_IMPORT_OK")
'''


def install(archive_path, home):
    # Called only by the installation's interpreter, after re-exec in main().
    import yaml
    from .config import Config
    from .hermes_adapter import VERIFIED_COMMIT, verify_runtime  # Imports interfaces, never connects.

    profile = home / "profiles/liberdus-mod"
    source = home / "hermes-agent"
    if not profile.is_dir() or profile.is_symlink() or profile.stat().st_uid != os.getuid():
        raise ValueError("Expected an existing liberdus-mod profile owned by this user")
    for path in (home / "config.yaml", profile / "config.yaml"):
        if not path.is_file() or path.is_symlink():
            raise ValueError("Expected regular profile configuration files")
    default = yaml.safe_load((home / "config.yaml").read_text())
    configuration = yaml.safe_load((profile / "config.yaml").read_text())
    for data in (default, configuration):
        if not isinstance(data, dict) or data.get("platforms", {}).get("discord", {}).get("enabled") is not False:
            raise ValueError("Stock Discord must already be explicitly disabled in both profiles")
    if configuration.get("platforms", {}).get("liberdus_moderator", {}).get("enabled") is True:
        raise ValueError("Refusing to replace an active pilot")
    result = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
    if result.returncode or result.stdout.strip() != VERIFIED_COMMIT:
        raise ValueError("Installed Hermes commit does not match the verified version")
    import importlib.metadata
    if importlib.metadata.version("discord.py") != "2.7.1":
        raise ValueError("Installed discord.py version does not match the verified version")
    verify_runtime()
    target = profile / "plugins/liberdus-moderator"
    policy_path = profile / "moderation.toml"
    if target.exists() or target.is_symlink() or policy_path.exists() or policy_path.is_symlink():
        raise ValueError("Pilot files already exist; no files replaced. Use the recorded installation or a reviewed update.")
    with zipfile.ZipFile(archive_path) as archive:
        policy_text = archive.read("pilot.toml").decode()
        import tomllib
        policy = Config.from_dict(tomllib.loads(policy_text))
        if (Path(policy.storage.database_path) != profile / "state/moderation.sqlite3"
                or policy.mode != "report_only" or policy.logs_enabled or policy.ai_enabled or policy.actions_enabled
                or policy.operator_role_ids or policy.log_channel_id or len(policy.command_channel_ids) != 1):
            raise ValueError("Bundle policy is not the expected disabled-action pilot")
        if target.parent.is_symlink():
            raise ValueError("Plugin directory must not be a symlink")
        target.parent.mkdir(mode=0o700, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".liberdus-stage-", dir=target.parent) as stage_dir:
            stage = Path(stage_dir)
            for name in archive.namelist():
                if not name.startswith("plugin/") or name.endswith("/"):
                    continue
                relative = Path(name.removeprefix("plugin/"))
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("Invalid bundle member")
                dest = stage / relative
                dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                dest.write_bytes(archive.read(name))
                dest.chmod(0o600)
            with tempfile.TemporaryDirectory(prefix="liberdus-install-check-") as scratch:
                probe = subprocess.run([sys.executable, "-I", "-B", "-c", SMOKE, str(source), str(stage)],
                    env={"HOME": scratch, "HERMES_HOME": scratch, "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                    cwd=scratch, capture_output=True, text=True, timeout=45)
                if probe.returncode or "LIBERDUS_INSTALL_IMPORT_OK" not in probe.stdout.splitlines():
                    raise ValueError("Installed-runtime plugin import check failed; raw logs omitted")
            plugins = configuration.setdefault("plugins", {})
            enabled = plugins.setdefault("enabled", [])
            disabled = plugins.get("disabled", [])
            if not isinstance(enabled, list) or not isinstance(disabled, list):
                raise ValueError("Unexpected plugin enablement configuration")
            if "liberdus-moderator" in disabled:
                raise ValueError("Plugin is explicitly denied in this profile")
            if "liberdus-moderator" not in enabled:
                enabled.append("liberdus-moderator")
            configuration["platforms"]["liberdus_moderator"] = {
                "enabled": False, "gateway_restart_notification": False, "typing_indicator": False,
            }
            backup = Path(tempfile.mkdtemp(prefix=".liberdus-install-backup-", dir=profile))
            (backup / "config.yaml").write_bytes((profile / "config.yaml").read_bytes())
            (backup / "config.yaml").chmod(0o600)
            fd, config_temp = tempfile.mkstemp(prefix=".liberdus-config-", dir=profile)
            try:
                with os.fdopen(fd, "w") as stream:
                    yaml.safe_dump(configuration, stream, sort_keys=False)
                policy_path.write_text(policy_text)
                policy_path.chmod(0o600)
                # Publish code before publishing its disabled configuration.
                shutil.copytree(stage, target)
                os.replace(config_temp, profile / "config.yaml")
            except BaseException:
                # These paths did not exist before this installation attempt.
                if target.exists():
                    shutil.rmtree(target)
                policy_path.unlink(missing_ok=True)
                raise
            finally:
                Path(config_temp).unlink(missing_ok=True)
    return {"installed": True, "platform_enabled": False, "plugin_directory": str(target),
            "policy_file": str(policy_path), "config_backup": str(backup / "config.yaml"),
            "installed_runtime_import": "passed", "gateway_restarted": False,
            "token_read": False, "discord_connected": False}


def main():
    home = Path.home() / ".hermes"
    source = home / "hermes-agent"
    python = source / "venv/bin/python"
    if not python.is_file():
        print("Installation stopped: expected the previously verified Hermes virtualenv.")
        return 2
    if Path(sys.prefix).resolve() != python.parent.parent.resolve():
        os.execv(str(python), [str(python), "-B", sys.argv[0]])
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source))
    # Never import Hermes against the real profile; install() reads only the two
    # YAML configuration files explicitly. No .env/token load is needed.
    with tempfile.TemporaryDirectory(prefix="liberdus-installer-home-") as scratch:
        os.environ["HERMES_HOME"] = scratch
        try:
            result = install(Path(sys.argv[0]), home)
        except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError, ImportError):
            print("Installation stopped without activation. No gateway restart was requested; inspect runtime, profile paths, or existing pilot files before retrying.")
            return 2
    print(json.dumps(result, indent=2))
    return 0
