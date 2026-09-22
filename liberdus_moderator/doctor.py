"""Bounded setup diagnostics. No Discord writes, gateway login, or JEV call."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3

from .instance import credential_provider, load_policy, settings
from .preflight import DiscordReader, inspect, permissions
from .secure_files import SetupError, file_lock, token_lock

MANAGE_MESSAGES = 1 << 13


def check_application(get, bot_id):
    app = get("/oauth2/applications/@me")
    bot = app.get("bot")
    if bot is not None and bot.get("id") != bot_id:
        raise SetupError("The Discord application belongs to a different bot.")
    raw = app.get("flags_new", app.get("flags"))
    if ((type(raw) is not int and not (isinstance(raw, str) and raw.isdecimal())) or int(raw) < 0):
        raise SetupError("Discord did not provide application intent metadata.")
    # Discord Application Flags: GATEWAY_MESSAGE_CONTENT and its LIMITED form.
    # https://docs.discord.com/developers/resources/application#application-flags
    if not int(raw) & ((1 << 18) | (1 << 19)):
        raise SetupError("Enable Message Content intent on the Bot page in the Discord Developer Portal, then run setup or doctor again.")
    if app.get("interactions_endpoint_url"):
        raise SetupError("Clear the Interactions Endpoint URL in the Discord Developer Portal. This bot receives its review buttons through the Gateway.")
    return app


def terminal(value):
    """Quote names and escape terminal control characters from Discord."""
    return json.dumps(str(value)[:160], ensure_ascii=True)


class Inventory:
    def __init__(self, get, guild_id):
        self.get = get
        self.guild_id = guild_id
        self.cache = {}

    def __call__(self, path):
        if path not in self.cache:
            if path.startswith("/channels/"):
                channels = self(f"/guilds/{self.guild_id}/channels")
                for channel in channels:
                    if isinstance(channel, dict) and channel.get("id"):
                        if channel.get("guild_id", self.guild_id) != self.guild_id:
                            raise SetupError("Discord returned a channel from an unexpected server.")
                        self.cache["/channels/" + channel["id"]] = {**channel, "guild_id": self.guild_id}
                if path not in self.cache:
                    raise SetupError("A selected channel is unavailable in the selected server.")
            else:
                self.cache[path] = self.get(path)
        return self.cache[path]


def inspect_channels(policy, get, *, deletion=False):
    inventory = get if isinstance(get, Inventory) else Inventory(get, policy.guild_id)
    report = inspect(policy, inventory)
    guild = inventory(f"/guilds/{policy.guild_id}")
    bot = inventory(f"/guilds/{policy.guild_id}/members/{policy.bot_user_id}")
    issues, warnings = [], []
    for row in report["channels"]:
        identity = row["channel_id"]
        channel = inventory(f"/channels/{identity}")
        label = f"{terminal(channel.get('name', 'channel'))} (ID {identity})"
        row["name"] = channel.get("name", "channel")
        if row["bot_administrator"]:
            issues.append(f"Remove Administrator from the bot; grant individual permissions for {label}.")
        for key, title in (("bot_view", "View Channel"), ("bot_read_history", "Read Message History")):
            if not row[key]:
                issues.append(f"Missing {title} in {label}.")
        if row["private_required"] and not row["everyone_hidden"]:
            issues.append(f"Hide {label} from @everyone before using it for private reports.")
        if row["public_required"] and row["everyone_hidden"]:
            issues.append(f"The existing policy requires public visibility for {label}.")
        if not row["category_allowed"]:
            issues.append(f"The category of {label} is outside the configured scope.")
        if row["purpose"] == "commands_and_reports":
            if not row["bot_send"]:
                issues.append(f"Missing Send Messages in {label}.")
            if row["additional_view_overwrite_ids"]:
                warnings.append(f"Review additional role/member access to {label}; hidden from @everyone does not mean only authorized operators can read it.")
        elif deletion:
            bits = permissions(guild, bot["roles"], policy.bot_user_id, channel)
            if not bits & MANAGE_MESSAGES:
                issues.append(f"Missing Manage Messages in {label}.")
    return {"ok": report["checks_passed"] and not issues, "issues": issues,
            "warnings": warnings, "channels": report["channels"]}


def check(home, *, offline=False, reader_factory=DiscordReader):
    home = Path(home)
    policy = load_policy(home)
    backend = settings(home)["credentials"]
    credentials = credential_provider(home)
    token = credentials.get("discord")
    if policy.ai_enabled:
        credentials.get("jev")  # Validate storage, not provider authorization.
    with token_lock(token), file_lock(home / "state/moderation.lock"):
        path = home / "state/moderation.sqlite3"
        if not path.is_file():
            raise SetupError("The moderation database is missing. Restore the instance before starting.")
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as database:
            from .storage import Store
            if (database.execute("PRAGMA application_id").fetchone()[0] != Store.APPLICATION_ID
                    or database.execute("PRAGMA user_version").fetchone()[0] != Store.SCHEMA_VERSION):
                raise SetupError("The moderation database has an unsupported identity or schema.")
            if database.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise SetupError("The moderation database failed its integrity check.")
            row = database.execute("SELECT value FROM settings WHERE key='guild_id'").fetchone()
            if row is None or json.loads(row[0]) != policy.guild_id:
                raise SetupError("The moderation database belongs to a different server.")
    result = {"ok": True, "credentials": backend, "database": "ok", "issues": [],
              "warnings": [], "discord": "not_checked" if offline else "checked",
              "jev": "configured_not_called" if policy.ai_enabled else "off"}
    if not offline:
        get = reader_factory(token)
        check_application(get, policy.bot_user_id)
        result.update(inspect_channels(policy, get, deletion=policy.actions_enabled))
        result["warnings"].append("Message Content intent is enabled; gateway readiness is verified on startup. JEV authorization has not been tested by an AI call.")
    return result


def print_report(report, output=print):
    output("Installation checks passed." if report["ok"] else "Installation needs attention.")
    output("Credential storage: " + report["credentials"])
    output("Database: " + report["database"])
    output("Discord: " + report["discord"] + "; JEV: " + report["jev"])
    for row in report.get("channels", []):
        output(f"  {terminal(row['name'])} (ID {row['channel_id']}): {row['purpose']}")
    for issue in report["issues"]:
        output("Fix: " + issue)
    for warning in report["warnings"]:
        output("Note: " + warning)
