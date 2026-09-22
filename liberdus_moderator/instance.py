"""Standalone instance configuration and atomic installation, without network I/O."""

from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import tomllib

from .config import Config
from .configure_jev import policy_text
from .credentials import Credentials
from .secure_files import SetupError, checked_directory, file_lock, read_private, write_private


def default_home():
    return Path.home() / ".liberdus-moderator"


def settings(home):
    home = checked_directory(home)
    value = json.loads(read_private(home / "instance.json"))
    if (not isinstance(value, dict) or set(value) != {"schema", "credentials"}
            or type(value["schema"]) is not int or value["schema"] != 1
            or value["credentials"] not in ("file", "systemd")):
        raise SetupError("Invalid instance settings. Run doctor to check the installation.")
    return value


def load_policy(home):
    home = checked_directory(home)
    policy = Config.from_dict(tomllib.loads(read_private(home / "moderation.toml").decode("utf-8")))
    if (Path(policy.storage.database_path) != home / "state/moderation.sqlite3"
            or policy.mode != "report_only" or policy.logs_enabled or policy.log_channel_id
            or policy.operator_role_ids or len(policy.command_channel_ids) != 1):
        raise SetupError("Expected instance-local storage, user-ID operators, and one private staff channel.")
    checked_directory(home / "state")
    for name in ("moderation.sqlite3", "moderation.sqlite3-wal", "moderation.sqlite3-shm"):
        path = home / "state" / name
        if path.exists() or path.is_symlink():
            # Validate metadata without reading retained message evidence.
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or info.st_mode & 0o077 or info.st_nlink != 1):
                    raise SetupError("The moderation database has unsafe ownership or permissions.")
            finally:
                os.close(fd)
    return policy


def credential_provider(home):
    return Credentials(home, backend=settings(home)["credentials"])


def create_instance(home, policy, secrets, *, deletion=False, auto_delete=False, database_copy=None):
    """Publish a new installation last; never overwrite an existing instance.

    database_copy(stage_database) may copy a stopped migration source. It must
    preserve the entire database, including flags and accounting.
    """
    from .storage import Store
    candidate = Path(home).absolute()
    if any((parent / ".git").exists() for parent in (candidate, *candidate.parents)):
        raise SetupError("Choose an installation directory outside a Git checkout so credentials and evidence cannot be committed.")
    home = checked_directory(home, create=True)
    with file_lock(home / "setup.lock"):
        names = ("moderation.toml", "instance.json", "state", "credentials")
        if any((home / name).exists() or (home / name).is_symlink() for name in names):
            raise SetupError("An installation already exists here. It was not overwritten.")
        if auto_delete and (not deletion or not policy.ai_enabled):
            raise SetupError("Automatic deletion requires deletion and JEV screening.")
        if deletion and not policy.actions_enabled:
            raise SetupError("The reviewed policy does not allow deletion.")
        policy = replace(policy, storage=replace(policy.storage, database_path=str(home / "state/moderation.sqlite3")))
        stage = Path(tempfile.mkdtemp(prefix=".setup-", dir=home))
        published = []
        try:
            checked_directory(stage / "state", create=True)
            credentials = Credentials(stage)
            credentials.put("discord", secrets["discord"])
            if policy.ai_enabled:
                credentials.put("jev", secrets["jev"])
            write_private(stage / "moderation.toml", policy_text(policy))
            database = stage / "state/moderation.sqlite3"
            if database_copy is not None:
                database_copy(database)
            else:
                with Store(str(database)) as store:
                    store.set_setting("guild_id", policy.guild_id)
                    store.set_setting("deletion_enabled", deletion)
                    store.set_setting("auto_delete_enabled", auto_delete)
                    store.set_setting("timeout_enabled", False)
                    store.set_setting("paused", False)
            write_private(stage / "instance.json", json.dumps({"schema": 1, "credentials": "file"}) + "\n")
            # instance.json is the commit marker, after all required files.
            for name in names:
                if name == "instance.json":
                    continue
                os.rename(stage / name, home / name)
                published.append(home / name)
            os.rename(stage / "instance.json", home / "instance.json")
            published.append(home / "instance.json")
        except BaseException:
            for path in reversed(published):
                shutil.rmtree(path) if path.is_dir() else path.unlink()
            raise
        finally:
            shutil.rmtree(stage)
    return policy
