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
                   "operator_user_ids", "operator_role_ids", "log_channel_id")
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
            return {"mode": config.classifier.mode, "records": records, "accounting": settings,
                    "note": "Historical shadow results; compare evidence revisions, not moderation verdicts."}
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
        if operation == "shadow":
            settings = ClassifierSettings(mode="shadow", **(limits or {}))
            updated = replace(config, schema_version=2, ai_enabled=True, classifier=settings)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("key", "shadow", "off", "results"))
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
