"""Owner-run profile-local JEV setup. No network calls or service restarts."""

import argparse
from dataclasses import asdict, replace
import getpass
import json
import os
from pathlib import Path
import sys
import tempfile

from .config import Config, ClassifierSettings


def policy_text(config):
    """Serialize only the validated policy schema, including scoped exceptions."""
    data = asdict(config)
    scope_names = ("guild_id", "bot_user_id", "monitored_channel_ids", "command_channel_ids",
                   "operator_user_ids", "operator_role_ids", "log_channel_id", "excluded_category_ids", "included_category_ids")
    scope = {name: data.pop(name) for name in scope_names}
    tables = {name: data.pop(name) for name in ("storage", "rules", "classifier")}
    if config.schema_version == 1:
        del tables["classifier"]
    exceptions = tables["rules"].pop("approved_crossposts")
    def lines(values):
        return [f"{name} = {json.dumps(value, ensure_ascii=False)}" for name, value in values.items() if value is not None]
    result = lines(data) + ["", "[scope]"] + lines(scope)
    for name, values in tables.items():
        result += ["", f"[{name}]"] + lines(values)
    for exception in exceptions:
        result += ["", "[[rules.approved_crossposts]]"] + lines(exception)
    text = "\n".join(result) + "\n"
    import tomllib
    if Config.from_dict(tomllib.loads(text)) != config:
        raise ValueError("Policy serialization mismatch")
    return text


def regular_owned(path, *, optional=False):
    if optional and not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or not path.is_file() or path.stat().st_uid != os.getuid():
        raise ValueError("Expected an owned, regular profile file")


def backup(profile, path):
    directory = Path(tempfile.mkdtemp(prefix=".jev-backup-", dir=profile))
    if path.exists():
        target = directory / path.name
        target.write_bytes(path.read_bytes())
        target.chmod(0o600)
    return directory


def atomic_write(path, content):
    fd, staged = tempfile.mkstemp(prefix=".jev-stage-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, path)
    finally:
        Path(staged).unlink(missing_ok=True)


def configure(profile, operation, *, key=None, limits=None):
    import yaml
    if profile.name != "liberdus-mod" or profile.is_symlink() or profile.stat().st_uid != os.getuid():
        raise ValueError("Expected the owned liberdus-mod profile")
    config_path, policy_path = profile / "config.yaml", profile / "moderation.toml"
    regular_owned(config_path)
    regular_owned(policy_path)
    raw = yaml.safe_load(config_path.read_text())
    platforms = raw.get("platforms", {})
    if operation != "results" and (platforms.get("liberdus_moderator", {}).get("enabled") is not False
            or platforms.get("discord", {}).get("enabled") is not False):
        raise ValueError("Disable the custom platform and stock Discord before JEV setup")
    config = Config.from_file(policy_path)
    if operation == "results":
        import sqlite3
        database = profile / "state/moderation.sqlite3"
        if Path(config.storage.database_path) != database:
            raise ValueError("Expected the profile-local database")
        regular_owned(database, optional=True)
        if not database.exists():
            return {"mode": config.classifier.mode, "records": [], "attempts": 0}
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='classifier_attempts'").fetchone()
            records = [dict(row) for row in connection.execute(
                "SELECT * FROM classifier_attempts ORDER BY started_at DESC LIMIT 20")] if exists else []
            for record in records:
                record["result"] = json.loads(record.pop("result_json") or "null")
            settings = {row["key"]: json.loads(row["value"]) for row in connection.execute(
                "SELECT key,value FROM settings WHERE key IN ('classifier_total_calls','classifier_total_reserved_microusd','classifier_state')")}
            screen_exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='screening_attempts_v1'").fetchone()
            screen_records = [dict(row) for row in connection.execute(
                "SELECT message_id,version,started_at,finished_at,outcome,latency_ms,incident_id,result_json "
                "FROM screening_attempts_v1 ORDER BY started_at DESC LIMIT 20")] if screen_exists else []
            for record in screen_records:
                record["result"] = json.loads(record.pop("result_json") or "null")
            screen_settings = {row["key"]: json.loads(row["value"]) for row in connection.execute(
                "SELECT key,value FROM settings WHERE key IN ('screening_total_calls','screening_daily_calls',"
                "'screening_total_reserved_microusd','screening_daily_reserved_microusd','screening_state',"
                "'screening_checked','screening_flagged','screening_unchecked')")}
            return {"mode": config.classifier.mode, "records": records, "accounting": settings,
                    "screening_records": screen_records, "screening_accounting": screen_settings,
                    "note": "Saved results only; scores are not verdicts, reservations and estimates are not invoices."}
        finally:
            connection.close()
    if operation == "key":
        if (not isinstance(key, str) or not 10 <= len(key) <= 512
                or not key.isascii() or any(character.isspace() or not character.isprintable() for character in key)):
            raise ValueError("Expected a single-line API key")
        env = profile / ".env"
        regular_owned(env, optional=True)
        saved = backup(profile, env)
        # Use Hermes's existing dotenv parser/writer, never shell interpolation.
        from dotenv import set_key
        fd, staged = tempfile.mkstemp(prefix=".jev-env-", dir=profile)
        os.close(fd)
        try:
            Path(staged).write_bytes(env.read_bytes() if env.exists() else b"")
            set_key(staged, "TYPESAFE_API_KEY", key, quote_mode="always")
            os.replace(staged, env)
        finally:
            Path(staged).unlink(missing_ok=True)
    else:
        if operation == "screen":
            # Fixed approved trial allowance; old shadow/batch counters remain untouched.
            settings = ClassifierSettings(exempt_role_ids=config.classifier.exempt_role_ids, mode="report_only", max_daily_calls=10000, max_total_calls=100000,
                daily_budget_microusd=1000000, total_budget_microusd=4000000,
                queue_capacity=100, timeout_seconds=3, min_interval_seconds=1)
            updated = replace(config, schema_version=2, ai_enabled=True, classifier=settings)
        elif operation == "shadow":
            settings = ClassifierSettings(exempt_role_ids=config.classifier.exempt_role_ids, mode="shadow", **(limits or {}))
            updated = replace(config, schema_version=2, ai_enabled=True, classifier=settings)
        elif operation == "exempt-role":
            if config.classifier.mode != "report_only":
                raise ValueError("Existing single-message screening must be configured first")
            roles = tuple(dict.fromkeys((*config.classifier.exempt_role_ids, "1302455329795342377")))
            updated = replace(config, classifier=replace(config.classifier, exempt_role_ids=roles))
        elif operation == "actions":
            updated = replace(config, schema_version=2, actions_enabled=True)
        elif operation == "off":
            updated = replace(config, schema_version=2, ai_enabled=False,
                              classifier=replace(config.classifier, mode="off"))
        else:
            raise ValueError("Unsupported setup operation")
        content = policy_text(updated)
        saved = backup(profile, policy_path)
        atomic_write(policy_path, content)
    return {"configured": operation, "profile": "liberdus-mod", "backup": str(saved),
            "platform_enabled": False, "gateway_restarted": False, "provider_called": False}



def configure_screening(profile, operation="screen"):
    """Activate only the reviewed Liberdus test scope, with the new code stopped."""
    import fcntl
    import yaml
    from . import __version__
    manifest_path = profile / "plugins/liberdus-moderator/plugin.yaml"
    lock_path = profile / "state/moderation.lock"
    default_path = profile.parent.parent / "config.yaml"
    for path in (manifest_path, lock_path, default_path):
        regular_owned(path)
    manifest = yaml.safe_load(manifest_path.read_text())
    if (manifest.get("name") != "liberdus-moderator" or manifest.get("version") != __version__
            or __version__ != "0.8.0"):
        raise ValueError("Install the reviewed 0.8.0 update first")
    default = yaml.safe_load(default_path.read_text())
    if default.get("platforms", {}).get("discord", {}).get("enabled") is not False:
        raise ValueError("Default stock Discord must remain disabled")
    policy = Config.from_file(profile / "moderation.toml")
    if (policy.guild_id != "746426387606274199" or policy.bot_user_id != "1548537340870533150"
            or set(policy.monitored_channel_ids) != {"1551249559819264030", "1551249642216357908", "1551249693399584818"}
            or policy.command_channel_ids != ("1551252553331642558",)
            or policy.operator_user_ids != ("977263877391794217",) or policy.operator_role_ids
            or policy.logs_enabled or policy.log_channel_id
            or Path(policy.storage.database_path) != profile / "state/moderation.sqlite3"):
        raise ValueError("Expected the approved private Liberdus test scope")
    lock = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return configure(profile, operation)
    finally:
        os.close(lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("key", "shadow", "screen", "exempt-role", "actions", "off", "results"))
    parser.add_argument("--daily-calls", type=int, default=100)
    parser.add_argument("--total-calls", type=int, default=1000)
    parser.add_argument("--daily-microusd", type=int, default=50000, help="50000 = $0.05")
    parser.add_argument("--total-microusd", type=int, default=250000, help="250000 = $0.25")
    args = parser.parse_args()
    home = Path.home() / ".hermes"
    python = home / "hermes-agent/venv/bin/python"
    if not python.is_file():
        print("Setup stopped: expected the existing Hermes virtualenv.")
        return 2
    if Path(sys.prefix).resolve() != python.parent.parent.resolve():
        os.execv(str(python), [str(python), "-B", sys.argv[0], *sys.argv[1:]])
    try:
        key = None
        if args.operation == "key":
            if not sys.stdin.isatty():
                raise ValueError("Run key setup interactively for a hidden prompt")
            key = getpass.getpass("TypeSafe API key (hidden): ")
        if args.operation in {"screen", "exempt-role", "actions"}:
            result = configure_screening(home / "profiles/liberdus-mod", args.operation)
        else:
            result = configure(home / "profiles/liberdus-mod", args.operation, key=key, limits={
                "max_daily_calls": args.daily_calls, "max_total_calls": args.total_calls,
                "daily_budget_microusd": args.daily_microusd, "total_budget_microusd": args.total_microusd})
        print(json.dumps(result, indent=2))
        return 0
    except Exception:
        print("Setup stopped. Check the installed policy and disabled platform; no restart or provider call occurred.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
