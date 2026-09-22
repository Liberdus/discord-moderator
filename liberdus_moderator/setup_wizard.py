"""Guided local setup using read-only Discord metadata and hidden secret entry."""

from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

from .config import ClassifierSettings, Config, StorageSettings
from .credentials import prompt_secret
from .doctor import Inventory, check_application, inspect_channels, terminal
from .instance import create_instance
from .models import validate_id
from .preflight import DiscordReader
from .secure_files import SetupError

PORTAL = "https://discord.com/developers/applications"
INTENTS = "https://docs.discord.com/developers/events/gateway#message-content-intent"


def channel_id(value, guild_id):
    value = value.strip()
    if value.startswith("<#") and value.endswith(">"):
        value = value[2:-1]
    elif value.startswith("https://"):
        parsed = urlparse(value)
        parts = parsed.path.strip("/").split("/")
        if (parsed.netloc not in ("discord.com", "www.discord.com", "discordapp.com")
                or parsed.query or parsed.fragment or len(parts) not in (3, 4)
                or parts[0] != "channels" or parts[1] != guild_id):
            raise SetupError("Paste a Discord channel link from the selected server.")
        value = parts[2]
    validate_id(value, "channel_id")
    return value


def choose(value, choices, *, guild_id=None):
    value = value.strip()
    if value.startswith("id:"):
        identity = value[3:].strip()
    elif value.isdecimal() and 1 <= int(value) <= len(choices):
        identity = choices[int(value) - 1]["id"]
    else:
        matches = [item for item in choices if item.get("name", "").casefold() == value.lstrip("#").casefold()]
        if len(matches) == 1:
            return matches[0]["id"]
        if len(matches) > 1:
            raise SetupError("That name is duplicated. Select its numbered entry or paste its ID.")
        identity = channel_id(value, guild_id) if guild_id else value
    if identity not in {item["id"] for item in choices}:
        raise SetupError("Choose an item from this server's displayed list.")
    return identity


def budget(value, maximum):
    try:
        amount = Decimal(value)
        if not amount.is_finite() or amount <= 0 or amount > maximum or amount.as_tuple().exponent < -6:
            raise InvalidOperation
        return int(amount * 1_000_000)
    except (InvalidOperation, ValueError):
        raise SetupError("Enter a positive dollar amount within the displayed limit (up to six decimal places).") from None


def wizard(home, *, ask=input, secret_prompt=None, output=print, reader_factory=DiscordReader):
    home = Path(home).absolute()
    if any((parent / ".git").exists() for parent in (home, *home.parents)):
        raise SetupError("Choose an installation directory outside the Git checkout before entering credentials.")
    if any((home / name).exists() or (home / name).is_symlink()
           for name in ("instance.json", "moderation.toml", "state", "credentials")):
        raise SetupError("An installation already exists here. Setup will not overwrite it.")
    output("Liberdus Moderator setup — one Discord server per installation")
    output("Create a bot application, enable Message Content intent, and add it to your server.")
    output("Developer Portal: " + PORTAL)
    output("Message Content instructions: " + INTENTS)
    output("Keys are entered here privately and stored outside the code repository.")
    token = prompt_secret("Discord bot token (hidden): ", secret_prompt)
    get = reader_factory(token)
    me = get("/users/@me")
    if me.get("bot") is not True:
        raise SetupError("This credential must belong to a Discord bot application.")
    validate_id(me["id"], "bot_user_id")
    application = check_application(get, me["id"])
    output("Connected to bot " + terminal(me.get("username", "bot")) + " (ID " + me["id"] + ")")
    # The invite grants the maximum permissions offered by this wizard, never
    # Administrator. Channel-level permissions are checked separately below.
    bits = (1 << 10) | (1 << 11) | (1 << 13) | (1 << 16)
    application_id = application["id"]
    validate_id(application_id, "application_id")
    output(f"Bot invite: https://discord.com/oauth2/authorize?client_id={application_id}&scope=bot&permissions={bits}")
    guilds = get("/users/@me/guilds?limit=200")
    for index, guild in enumerate(guilds, 1):
        output(f"  {index}. {terminal(guild.get('name', 'server'))} (ID {guild['id']})")
    selected = ask("Server number or server ID (add the bot first if missing): ").strip()
    if selected.isdecimal() and 1 <= int(selected) <= len(guilds):
        guild_id = guilds[int(selected) - 1]["id"]
    else:
        guild_id = selected.removeprefix("id:").strip()
    validate_id(guild_id, "guild_id")
    inventory = Inventory(get, guild_id)
    guild = inventory(f"/guilds/{guild_id}")
    channels = inventory(f"/guilds/{guild_id}/channels")
    categories = {c["id"]: c.get("name", "category") for c in channels if c.get("type") == 4}
    choices = sorted((c for c in channels if c.get("type") == 0),
                     key=lambda c: (c.get("parent_id") or "", c.get("position", 0), c["id"]))
    if not choices:
        raise SetupError("Create ordinary text channels and a private staff channel in Discord first.")
    output("Select channels by number, exact name, ID, or Discord channel link.")
    for index, channel in enumerate(choices, 1):
        category = categories.get(channel.get("parent_id"), "no category")
        output(f"  {index}. {terminal(channel.get('name', 'channel'))} / {terminal(category)} (ID {channel['id']})")
    monitored = tuple(dict.fromkeys(choose(part, choices, guild_id=guild_id)
                                    for part in ask("Channels to monitor (comma separated): ").split(",")))
    staff = choose(ask("Private staff channel for commands and reports: "), choices, guild_id=guild_id)
    output("In Discord, enable Developer Mode under Advanced, then use Copy User ID on each authorized staff member.")
    operators = tuple(dict.fromkeys(part.strip() for part in ask("Authorized staff user IDs (comma separated, maximum 20): ").split(",")))
    if len(operators) > 20:
        raise SetupError("The setup wizard accepts up to 20 authorized staff users.")
    for identity in operators:
        validate_id(identity, "operator_user_id")
        member = inventory(f"/guilds/{guild_id}/members/{identity}")
        user = member.get("user", {})
        if user.get("id") != identity or user.get("bot"):
            raise SetupError("Each authorized staff ID must identify a human member of this server.")
        output("  Staff: " + terminal(user.get("username", "member")) + " (ID " + identity + ")")
    ai = ask("Enable JEV screening? Selected message text and URLs are sent to JEV. [y/N]: ").strip().lower() in ("y", "yes")
    secrets = {"discord": token}
    classifier = ClassifierSettings()
    if ai:
        secrets["jev"] = prompt_secret("JEV API key (hidden): ", secret_prompt)
        daily = budget(ask("Daily spending cap in USD (maximum 10, default 1): ").strip() or "1", 10)
        total = budget(ask("Lifetime spending cap in USD (maximum 100, default 4): ").strip() or "4", 100)
        if daily > total:
            raise SetupError("The daily spending cap cannot exceed the lifetime cap.")
        classifier = ClassifierSettings(mode="report_only", daily_budget_microusd=daily,
                                       total_budget_microusd=total, max_daily_calls=1000, max_total_calls=10000)
    output("Actions: 1. Reports only (default)  2. Staff-confirmed deletion  3. Staff + automatic deletion")
    mode = ask("Action option [1]: ").strip() or "1"
    if mode not in ("1", "2", "3") or (mode == "3" and not ai):
        raise SetupError("Choose a listed action option. Automatic deletion requires JEV screening.")
    deletion, automatic = mode != "1", mode == "3"
    policy = Config(guild_id=guild_id, bot_user_id=me["id"], monitored_channel_ids=monitored,
                    command_channel_ids=(staff,), operator_user_ids=operators,
                    schema_version=2, policy_version="standalone-1", ai_enabled=ai,
                    classifier=classifier, actions_enabled=deletion,
                    allow_public_monitored_channels=True, allow_public_deletion=deletion,
                    explicit_channel_scope=True,
                    storage=StorageSettings(database_path=str(home / "state/moderation.sqlite3")))
    result = inspect_channels(policy, inventory, deletion=deletion)
    for issue in result["issues"]:
        output("Fix: " + issue)
    for warning in result["warnings"]:
        output("Note: " + warning)
    if not result["ok"]:
        raise SetupError("Correct the listed Discord permissions and run setup again. No installation was saved.")
    output("Review installation for " + terminal(guild.get("name", "server")))
    for row in result["channels"]:
        output(f"  {terminal(row['name'])} (ID {row['channel_id']}): {row['purpose']}")
    output("Monitoring is limited to these channel IDs. New channels are not added automatically.")
    output("Staff user IDs: " + ", ".join(operators))
    output("JEV screening: " + ("on" if ai else "off") + "; staff deletion: " + ("on" if deletion else "off"))
    if ai:
        output(f"JEV caps: ${classifier.daily_budget_microusd / 1_000_000:g}/day; ${classifier.total_budget_microusd / 1_000_000:g} lifetime. No AI test call has been made.")
    output("Automatic deletion: " + ("sensitive requests, impersonation, suspicious offers, targeted abuse at score >= 0.90, with current-evidence safeguards" if automatic else "off"))
    output("Timeout: off. Credential files: owner-only access; not encrypted at rest in portable mode.")
    output("Installation directory: " + terminal(home))
    if ask("Save this installation? [y/N]: ").strip().lower() not in ("y", "yes"):
        output("Setup cancelled; no installation saved.")
        return False
    create_instance(home, policy, secrets, deletion=deletion, auto_delete=automatic)
    output("Installation saved. Run liberdus-moderator doctor, then liberdus-moderator start with this --home directory.")
    return True
