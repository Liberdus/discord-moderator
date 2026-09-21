"""Owner-run 0.3.0–0.5.1 -> 0.5.2 code update for a disabled, stopped pilot.

Keep configuration, policy, credentials and moderation state in place. Retain
the previous plugin directory for rollback. Never restart the gateway here.
"""

import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import zipfile

from .config import Config
from .configure_jev import regular_owned
from .install_pilot import SMOKE


CHECK = SMOKE + r'''
sys.path.insert(0, sys.argv[2])
from liberdus_moderator import __version__
from liberdus_moderator.selftest import run_selftest
from liberdus_moderator.classification_view import saved_classification, format_classification
from liberdus_moderator.evidence_view import saved_evidence, format_evidence
from liberdus_moderator.moderator_review import saved_review, record_review
from liberdus_moderator.staff_review import saved_assessment, record_assessment, pending_page
from liberdus_moderator.screening import MessageScreener, validate_screening, SCREENING_HASH
if __version__ != "0.5.2" or not run_selftest()["passed"]:
    raise RuntimeError("Updated self-test check failed")
print("LIBERDUS_UPDATE_SELFTEST_OK")
'''


def _directory(path):
    if path.is_symlink() or not path.is_dir() or path.stat().st_uid != os.getuid():
        raise ValueError("Expected owned profile and plugin directories without symlinks")


def _stage(archive_path, stage):
    with zipfile.ZipFile(archive_path) as archive:
        entries = [item for item in archive.infolist() if item.filename.startswith("plugin/")]
        names = [item.filename for item in entries]
        if len(set(names)) != len(names) or sum(item.file_size for item in entries) > 5_000_000:
            raise ValueError("Invalid plugin archive")
        for item in entries:
            name = item.filename.removeprefix("plugin/")
            if name not in {"__init__.py", "plugin.yaml"} and not re.fullmatch(r"liberdus_moderator/[a-z_]+\.py", name):
                raise ValueError("Unexpected plugin archive member")
            path = stage / name
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_bytes(archive.read(item))
            path.chmod(0o600)


def update(archive_path, home):
    import yaml
    from .hermes_adapter import verify_runtime

    profile = home / "profiles/liberdus-mod"
    target = profile / "plugins/liberdus-moderator"
    for path in (home, home / "profiles", profile, target.parent, target, profile / "state"):
        _directory(path)
    paths = (home / "config.yaml", profile / "config.yaml", profile / "moderation.toml")
    for path in (*paths, target / "plugin.yaml", profile / "state/moderation.lock"):
        regular_owned(path)
    saved = {path: path.read_bytes() for path in paths}
    default, configuration = (yaml.safe_load(saved[path]) for path in paths[:2])
    for data in (default, configuration):
        if not isinstance(data, dict) or data.get("platforms", {}).get("discord", {}).get("enabled") is not False:
            raise ValueError("Stock Discord must remain explicitly disabled in both profiles")
    if configuration.get("platforms", {}).get("liberdus_moderator", {}).get("enabled") is not False:
        raise ValueError("Disable liberdus_moderator and restart the gateway before updating")
    policy = Config.from_file(profile / "moderation.toml")
    if (policy.classifier.mode not in {"off", "shadow", "report_only"}
            or policy.mode != "report_only" or Path(policy.storage.database_path) != profile / "state/moderation.sqlite3"):
        raise ValueError("Expected the existing report-only, profile-local pilot policy")
    manifest = yaml.safe_load((target / "plugin.yaml").read_text())
    if not isinstance(manifest, dict) or manifest.get("name") != "liberdus-moderator" or manifest.get("version") not in {"0.3.0", "0.3.1", "0.3.2", "0.3.3", "0.3.4", "0.3.5", "0.4.0", "0.4.1", "0.4.2", "0.5.0", "0.5.1"}:
        raise ValueError("This updater requires an existing version 0.3.0, 0.3.1, 0.3.2, 0.3.3, 0.3.4, 0.3.5, 0.4.0, 0.4.1, 0.4.2, 0.5.0 or 0.5.1 installation")
    # Back up the actual installed tree, but never follow links out of it.
    if any(path.is_symlink() for path in target.rglob("*")):
        raise ValueError("Plugin tree must not contain symlinks")
    verify_runtime()
    lock = os.open(profile / "state/moderation.lock", os.O_RDWR | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("Pilot is still running; finish the disabled gateway restart first") from error
        with tempfile.TemporaryDirectory(prefix=".liberdus-update-stage-", dir=target.parent) as directory:
            stage = Path(directory) / "plugin"
            _stage(archive_path, stage)
            staged_manifest = yaml.safe_load((stage / "plugin.yaml").read_text())
            if (not isinstance(staged_manifest, dict) or staged_manifest.get("name") != "liberdus-moderator"
                    or staged_manifest.get("version") != "0.5.2"):
                raise ValueError("Expected the reviewed version 0.5.2 update")
            with tempfile.TemporaryDirectory(prefix="liberdus-update-check-") as scratch:
                probe = subprocess.run(
                    [sys.executable, "-I", "-B", "-c", CHECK, str(home / "hermes-agent"), str(stage)],
                    env={"HOME": scratch, "HERMES_HOME": scratch, "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                    cwd=scratch, capture_output=True, text=True, timeout=45,
                )
                if probe.returncode or "LIBERDUS_UPDATE_SELFTEST_OK" not in probe.stdout.splitlines():
                    raise ValueError("Installed-runtime import or isolated self-test check failed; raw logs omitted")
            if any(path.read_bytes() != content for path, content in saved.items()):
                raise ValueError("Configuration changed during validation; no plugin files replaced")
            backup = Path(tempfile.mkdtemp(prefix=".liberdus-update-backup-", dir=profile))
            previous = backup / "liberdus-moderator"
            os.replace(target, previous)
            try:
                os.replace(stage, target)
            except BaseException:
                os.replace(previous, target)
                raise
    finally:
        os.close(lock)
    return {"updated": True, "version": "0.5.2", "platform_enabled": False,
            "plugin_directory": str(target), "plugin_backup": str(previous),
            "installed_runtime_import": "passed", "isolated_selftest": "9/9 passed",
            "configuration_changed": False, "policy_changed": False, "database_changed": False,
            "gateway_restarted": False, "token_read": False, "discord_connected": False,
            "classifier_mode": policy.classifier.mode, "provider_called": False}


def main():
    home = Path.home() / ".hermes"
    python = home / "hermes-agent/venv/bin/python"
    if not python.is_file():
        print("Update stopped: expected the existing Hermes virtualenv.")
        return 2
    if len(sys.argv) != 1:
        print("Update stopped: this fixed pilot updater takes no arguments.")
        return 2
    if Path(sys.prefix).resolve() != python.parent.parent.resolve():
        os.execv(str(python), [str(python), "-B", sys.argv[0]])
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(home / "hermes-agent"))
    with tempfile.TemporaryDirectory(prefix="liberdus-updater-home-") as scratch:
        os.environ["HERMES_HOME"] = scratch
        try:
            result = update(Path(sys.argv[0]), home)
        except ValueError as error:
            print(f"Update stopped: {error}. No activation or restart requested.")
            return 2
        except Exception:
            print("Update stopped. Check the owned profile, disabled/stopped pilot and verified runtime. "
                  "No activation or restart requested.")
            return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
