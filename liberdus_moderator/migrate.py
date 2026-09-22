"""Copy a disabled, stopped Hermes moderation instance into a new standalone home."""

from contextlib import closing
from pathlib import Path
import shlex
import sqlite3
import tomllib

from .config import Config
from .credentials import validate_secret
from .instance import create_instance
from .secure_files import SetupError, checked_directory, file_lock, read_private
from .storage import Store


def profile_secrets(data, *, ai):
    needed = {"DISCORD_BOT_TOKEN": "discord"}
    if ai:
        needed["TYPESAFE_API_KEY"] = "jev"
    result = {}
    for line in data.decode("utf-8").splitlines():
        line = line.strip().removeprefix("export ").lstrip()
        name, separator, value = line.partition("=")
        if separator and name.strip() in needed:
            name = needed[name.strip()]
            try:
                values = shlex.split(value, comments=True)
            except ValueError:
                raise SetupError("A required profile credential is not a literal single-line value.") from None
            if name in result or len(values) != 1:
                raise SetupError("Expected exactly one literal value for each required profile credential.")
            result[name] = validate_secret(values[0])
    if set(result) != set(needed.values()):
        raise SetupError("The stopped profile is missing a required credential.")
    return result


def migrate(profile, home):
    try:
        import yaml
    except ImportError:
        raise SetupError("Hermes migration requires the optional migration extra: install '.[discord,migration]'.") from None
    profile = checked_directory(profile)
    home = Path(home).absolute()
    if home == profile or profile in home.parents or home in profile.parents:
        raise SetupError("Choose a separate standalone directory outside the Hermes profile.")
    configuration = yaml.safe_load(read_private(profile / "config.yaml"))
    if (not isinstance(configuration, dict)
            or configuration.get("platforms", {}).get("liberdus_moderator", {}).get("enabled") is not False):
        raise SetupError("Disable the Hermes liberdus_moderator platform and stop its running instance before migrating.")
    policy = Config.from_dict(tomllib.loads(read_private(profile / "moderation.toml").decode("utf-8")))
    database = profile / "state/moderation.sqlite3"
    if Path(policy.storage.database_path) != database:
        raise SetupError("The source database must be inside the stopped moderation profile.")
    checked_directory(database.parent)
    with file_lock(database.parent / "moderation.lock"):
        # SQLite backup copies committed WAL data as well. Opening the source
        # read-only and holding the bot's lock keeps source history intact.
        if database.is_symlink() or not database.is_file():
            raise SetupError("The source moderation database is unavailable.")
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
            if (source.execute("PRAGMA application_id").fetchone()[0] != Store.APPLICATION_ID
                    or source.execute("PRAGMA user_version").fetchone()[0] != Store.SCHEMA_VERSION
                    or source.execute("PRAGMA quick_check").fetchone()[0] != "ok"):
                raise SetupError("The source database failed identity, schema, or integrity checks.")
            import json
            guild = source.execute("SELECT value FROM settings WHERE key='guild_id'").fetchone()
            if guild is None or json.loads(guild[0]) != policy.guild_id:
                raise SetupError("The source database belongs to a different server.")
            secrets = profile_secrets(read_private(profile / ".env"), ai=policy.ai_enabled)

            def copy_database(target):
                from .secure_files import write_private
                write_private(target, b"")
                with closing(sqlite3.connect(target)) as destination:
                    source.backup(destination)
                    if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise SetupError("The migrated database failed its integrity check.")

            create_instance(home, policy, secrets, database_copy=copy_database)
    return {"migrated": True, "history_preserved": True, "action_flags_preserved": True,
            "spending_counters_preserved": True, "source_changed": False,
            "started": False}
