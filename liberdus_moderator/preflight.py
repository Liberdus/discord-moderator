"""Read-only Discord identity and channel permission checks; never connects a gateway."""

import argparse
import json
import os
from pathlib import Path
import shlex
import stat
import urllib.error
import urllib.request

from .config import Config
from .models import validate_id

VIEW = 1 << 10
SEND = 1 << 11
HISTORY = 1 << 16
ADMIN = 1 << 3


class PreflightError(Exception):
    """Only fixed, credential-free messages may be used with this exception."""


def read_token(path):
    """Read a literal token without executing or interpolating dotenv content."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise PreflightError("Token file must be a regular file owned by this user.")
        if info.st_mode & 0o077:
            raise PreflightError("Token file must have private permissions (chmod 600).")
        content = stream.read(1_000_001)
    if len(content) > 1_000_000:
        raise PreflightError("Token file exceeds the size limit.")
    tokens = []
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        if separator and key.strip() == "DISCORD_BOT_TOKEN":
            try:
                parts = shlex.split(value, comments=True)
            except ValueError:
                raise PreflightError("Invalid token entry format.") from None
            if len(parts) != 1 or not parts[0] or not all(
                c.isascii() and (c.isalnum() or c in "._-") for c in parts[0]
            ):
                raise PreflightError("Expected one literal bot token in the profile token entry.")
            tokens.append(parts[0])
    if len(tokens) != 1:
        raise PreflightError("Expected exactly one DISCORD_BOT_TOKEN entry in this profile.")
    return tokens[0]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PreflightError("Discord returned an unexpected redirect; request stopped.")


class DiscordReader:
    def __init__(self, token):
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect())

    def __call__(self, path):
        request = urllib.request.Request(
            "https://discord.com/api/v10" + path,
            headers={"Authorization": "Bot " + self.token,
                     "User-Agent": "LiberdusModeratorPreflight/0.1"},
            method="GET",
        )
        try:
            with self.opener.open(request, timeout=20) as response:
                body = response.read(4_000_001)
            if len(body) > 4_000_000:
                raise PreflightError("Discord response exceeded the size limit.")
            return json.loads(body)
        except urllib.error.HTTPError as error:
            # Never print the response body, headers, request, or token.
            raise PreflightError(f"Discord API returned HTTP {error.code}; no changes made.") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise PreflightError("Discord API connection failed; no changes made.") from None
        except (ValueError, UnicodeError):
            raise PreflightError("Discord returned an invalid JSON response.") from None


def permissions(guild, role_ids, member_id, channel):
    """Discord's base-role, everyone, aggregate-role, member overwrite order."""
    if member_id is not None and member_id == guild.get("owner_id"):
        return -1
    roles = {role["id"]: int(role["permissions"]) for role in guild["roles"]}
    if guild["id"] not in roles or any(role not in roles for role in role_ids):
        raise PreflightError("Guild role metadata is incomplete.")
    result = roles[guild["id"]]
    for role_id in role_ids:
        result |= roles[role_id]
    if result & ADMIN:
        return -1
    overwrites = channel["permission_overwrites"]
    for item in overwrites:
        if item["type"] == 0 and item["id"] == guild["id"]:
            result = (result & ~int(item["deny"])) | int(item["allow"])
    deny = allow = 0
    for item in overwrites:
        if item["type"] == 0 and item["id"] != guild["id"] and item["id"] in role_ids:
            deny |= int(item["deny"])
            allow |= int(item["allow"])
    result = (result & ~deny) | allow
    for item in overwrites:
        if item["type"] == 1 and item["id"] == member_id:
            result = (result & ~int(item["deny"])) | int(item["allow"])
    return result


def inspect(config, get):
    me = get("/users/@me")
    if me.get("id") != config.bot_user_id or me.get("bot") is not True:
        raise PreflightError("Saved token does not belong to the configured bot ID.")
    guild = get(f"/guilds/{config.guild_id}")
    if guild.get("id") != config.guild_id:
        raise PreflightError("Guild identity did not match configuration.")
    member = get(f"/guilds/{config.guild_id}/members/{config.bot_user_id}")
    if member.get("user", {}).get("id") != config.bot_user_id:
        raise PreflightError("Bot guild membership did not match configuration.")
    results = []
    category_checks = {}
    guarded_scope = config.allow_public_monitored_channels or bool(config.excluded_category_ids)
    for channel_id in (*config.monitored_channel_ids, *config.command_channel_ids):
        channel = get(f"/channels/{channel_id}")
        if (channel.get("id") != channel_id or channel.get("guild_id") != config.guild_id
                or type(channel.get("type")) is not int or channel.get("type") != 0):
            raise PreflightError("A configured channel is not a text channel in the configured server.")
        category_id = channel.get("parent_id")
        if guarded_scope:
            if "parent_id" not in channel:
                raise PreflightError("A configured channel has unavailable category metadata.")
            if category_id is not None:
                try:
                    validate_id(category_id, "category_id")
                except (ValueError, TypeError):
                    raise PreflightError("A configured channel has invalid category metadata.") from None
                if category_id not in category_checks:
                    category = get(f"/channels/{category_id}")
                    if (not isinstance(category, dict) or category.get("id") != category_id
                            or category.get("guild_id") != config.guild_id or category.get("type") != 4):
                        raise PreflightError("A configured channel's category could not be verified.")
                    category_checks[category_id] = True
        command_channel = channel_id in config.command_channel_ids
        private_required = command_channel or not config.allow_public_monitored_channels
        bits = permissions(guild, member["roles"], config.bot_user_id, channel)
        required = VIEW | HISTORY
        if command_channel:
            required |= SEND
        everyone = permissions(guild, [], None, channel)
        results.append({
            "channel_id": channel_id,
            "purpose": "commands_and_reports" if command_channel else "monitoring",
            "category_id": category_id,
            "category_allowed": category_id not in config.excluded_category_ids,
            "private_required": private_required,
            "public_required": not private_required,
            "bot_view": bool(bits & VIEW),
            "bot_read_history": bool(bits & VIEW and bits & HISTORY),
            "bot_send": bool(bits & VIEW and bits & SEND),
            "bot_administrator": bool(bits & ADMIN),
            "everyone_hidden": not bool(everyone & VIEW),
            "required_permissions_ok": bits & required == required,
            # This only identifies possible additional access. Role membership is not audited.
            "additional_view_overwrite_ids": sorted({item["id"] for item in channel["permission_overwrites"]
                if item["id"] not in (config.guild_id, config.bot_user_id, *config.operator_user_ids)
                and int(item["allow"]) & VIEW}),
        })
    return {
        "bot_identity_matches": True, "guild_id": config.guild_id,
        "channels": results,
        "checks_passed": all(r["required_permissions_ok"] and r["category_allowed"]
                             and r["everyone_hidden"] == r["private_required"]
                             and not r["bot_administrator"] for r in results),
        "limitations": "No messages read or sent. Other role membership, gateway intents, and live moderation remain unverified.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--token-file", type=Path,
                        default=Path.home() / ".hermes/profiles/liberdus-mod/.env")
    args = parser.parse_args(argv)
    try:
        config = Config.from_file(args.config)
        report = inspect(config, DiscordReader(read_token(args.token_file)))
    except PreflightError as error:
        print(f"Preflight stopped: {error}")
        return 2
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        print("Preflight stopped: unable to read valid configuration, token file, or API metadata.")
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0 if report["checks_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
