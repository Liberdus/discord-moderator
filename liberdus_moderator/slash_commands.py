"""Guild-scoped application commands using the existing serialized moderation path."""

import asyncio
import math
import re

import discord

from .commands import CommandRequest
from .display import panel
from .secure_files import SetupError


def option(name, description, kind=3, *, required=True, choices=None, **bounds):
    result = dict(name=name, description=description, type=kind, required=required, **bounds)
    if choices:
        result["choices"] = [{"name": label, "value": value} for label, value in choices]
    return result


INCIDENT = option("incident", "Incident ID from a moderation review", min_length=32, max_length=32)
REVISION = option("revision", "Saved revision shown on the review", 4, min_value=1, max_value=1_000_000_000)
STATE = option("state", "Show the setting or turn it on/off", required=False,
               choices=[("Show", "show"), ("On", "on"), ("Off", "off")])
CHANGE = option("action", "Add or remove this ID", choices=[("Add", "add"), ("Remove", "remove")])


def target(name, description, kind, **bounds):
    return [option(name, description, kind, required=False, **bounds),
            option("id", "Raw ID instead of the selector; also works for removed members/channels/roles",
                   required=False, min_length=1, max_length=20)]

# Descriptions/options also form the strict ingress schema. No arbitrary setting
# names, identity envelopes, provider endpoints, credentials or shell arguments.
COMMANDS = {
    "help": ("Show moderation commands", []),
    "status": ("Show moderation status", []),
    "summary": ("Show activity, actions and screening costs", []),
    "connection": ("Show saved connection diagnostics", []),
    "pending": ("List pending reviews", [option("page", "Page number", 4, required=False, min_value=1, max_value=9999)]),
    "incident": ("Open a saved review with staff buttons", [INCIDENT]),
    "explain": ("Open a detailed saved review", [INCIDENT]),
    "actions": ("Show saved action attempts for an incident", [INCIDENT]),
    "assess": ("Save a staff assessment", [INCIDENT, REVISION, option("assessment", "Your assessment", choices=[
        ("Needs attention", "needs-attention"), ("Looks okay", "looks-okay"), ("Unsure", "unsure")])]),
    "dismiss": ("Close a review without deleting messages", [INCIDENT, REVISION]),
    "delete": ("Fetch current messages and ask for deletion confirmation", [INCIDENT, REVISION,
        option("message", "Optional individual message ID", required=False, min_length=1, max_length=20)]),
    "pause": ("Pause moderation immediately", []),
    "resume": ("Resume moderation with a fresh window", []),
    "selftest": ("Run local synthetic checks without AI or Discord actions", []),
    "deletion": ("Show or change the deletion switch", [STATE]),
    "auto-delete": ("Show or change the automatic deletion switch", [STATE]),
    "exempt-role": ("Show or change the role exemption switch", [STATE]),
    "timeout": ("Show or change the timeout switch; public policy blocks timeouts", [STATE]),
    "timeout-user": ("Request a staff timeout only where policy permits it", [INCIDENT, REVISION]),
}
SETTINGS = {
    "show": ("Show editable settings and IDs", []),
    "history": ("Show recent configuration change records", []),
    "monitor": ("Add or remove a monitored text channel", [CHANGE, *target("channel", "Text channel", 7, channel_types=[0])]),
    "staff-channel": ("Change the private staff command/report channel", target("channel", "Private staff text channel", 7, channel_types=[0])),
    "operator": ("Add or remove an authorized human moderator", [CHANGE, *target("user", "Server member", 6)]),
    "category": ("Edit category boundaries for monitored channels", [CHANGE,
        option("boundary", "Category rule", choices=[("Included", "included"), ("Excluded", "excluded")]),
        *target("category", "Category", 7, channel_types=[4])]),
    "exempt-role": ("Add or remove a screening exemption role", [CHANGE, *target("role", "Server role", 8)]),
    "budget": ("Change JEV spending caps without resetting usage", [
        option("daily", "Daily cap in USD", 10, min_value=0.000001, max_value=10),
        option("lifetime", "Lifetime cap in USD", 10, min_value=0.000001, max_value=100)]),
    "call-limits": ("Change JEV call caps without resetting usage", [
        option("daily", "Maximum calls per day", 4, min_value=1, max_value=10000),
        option("lifetime", "Maximum lifetime calls", 4, min_value=1, max_value=100000)]),
}


def definitions():
    options = [dict(type=1, name=name, description=description, options=fields)
               for name, (description, fields) in COMMANDS.items()]
    options.append(dict(type=2, name="config", description="View or edit moderation settings",
                        options=[dict(type=1, name=name, description=description, options=fields)
                                 for name, (description, fields) in SETTINGS.items()]))
    return [dict(type=1, name="mod", description="Liberdus moderation", options=options)]


def canonical(value):
    """Compare command schemas without Discord's generated IDs/default fields."""
    if isinstance(value, list):
        return [canonical(item) for item in value]
    if not isinstance(value, dict):
        return value
    fields = {"type", "name", "description", "options", "required", "choices", "value",
              "min_value", "max_value", "min_length", "max_length", "channel_types"}
    return {key: canonical(item) for key, item in value.items()
            if key in fields and item is not None and item != [] and not (key == "required" and item is False)}


async def sync_commands(client, policy):
    """The dedicated standalone application owns its global/configured-guild menu.

    Publish the working guild menu before removing obsolete global commands.
    Never touch command registrations belonging to another application or guild.
    """
    if str(client.user.id) != policy.bot_user_id or not client.application_id:
        raise SetupError("Slash command registration requires the configured bot identity.")
    application, guild = client.application_id, int(policy.guild_id)
    desired = definitions()
    existing = await client.http.get_guild_commands(application, guild)
    if canonical(existing) != canonical(desired):
        await client.http.bulk_upsert_guild_commands(application, guild, payload=desired)
    if await client.http.get_global_commands(application):
        await client.http.bulk_upsert_global_commands(application, payload=[])


def parse(data):
    if not isinstance(data, dict) or data.get("name") != "mod" or data.get("type") != 1:
        raise SetupError("Use the Liberdus /mod menu. Old Hermes commands are no longer handled by this bot.")
    branches = data.get("options", [])
    if not isinstance(branches, list) or len(branches) != 1 or not isinstance(branches[0], dict):
        raise SetupError("Choose a command from /mod help.")
    branch, group = branches[0], None
    catalog = COMMANDS
    if branch.get("name") == "config" and branch.get("type") == 2:
        branches = branch.get("options", [])
        if not isinstance(branches, list) or len(branches) != 1 or not isinstance(branches[0], dict):
            raise SetupError("Choose a command from /mod config.")
        branch, group, catalog = branches[0], "config", SETTINGS
    name = branch.get("name")
    if branch.get("type") != 1 or not isinstance(name, str) or name not in catalog:
        raise SetupError("Unknown moderation command. Reopen the /mod menu.")
    supplied = branch.get("options", [])
    if not isinstance(supplied, list):
        raise SetupError("Invalid command options.")
    specs = {field["name"]: field for field in catalog[name][1]}
    values = {}
    for item in supplied:
        if (not isinstance(item, dict) or not isinstance(item.get("name"), str)
                or item["name"] not in specs or item["name"] in values):
            raise SetupError("Invalid or duplicate command option.")
        key = item["name"]
        spec, value = specs[key], item.get("value")
        kind = spec["type"]
        valid = (item.get("type") == kind and
                 ((kind in (3, 6, 7, 8) and isinstance(value, str) and 1 <= len(value) <= 200)
                  or (kind == 4 and type(value) is int)
                  or (kind == 10 and type(value) in (int, float) and math.isfinite(value))))
        if not valid or ("choices" in spec and value not in {choice["value"] for choice in spec["choices"]}):
            raise SetupError("Invalid command option value.")
        if kind in (4, 10) and not spec.get("min_value", value) <= value <= spec.get("max_value", value):
            raise SetupError("Command value is outside the allowed range.")
        if kind in (6, 7, 8) or key in ("message", "id"):
            from .models import validate_id
            validate_id(value, "selected_id")
        if key == "incident" and not re.fullmatch(r"[0-9a-f]{32}", value):
            raise SetupError("Use the incident ID shown on a moderation review.")
        values[key] = value
    if any(field["required"] and field["name"] not in values for field in specs.values()):
        raise SetupError("A required command option is missing.")
    selections = {"monitor": "channel", "staff-channel": "channel", "operator": "user",
                  "category": "category", "exempt-role": "role"}
    if group and name in selections:
        field = selections[name]
        if (field in values) == ("id" in values):
            raise SetupError("Choose a selector or enter its raw ID, but not both.")
        if "id" in values:
            values[field] = values.pop("id")
    return group, name, values


def help_card():
    return panel("Moderation commands", [
        "`/mod status` · `/mod summary` · `/mod connection`",
        "`/mod pending` · `/mod incident` · `/mod explain` · `/mod actions`",
        "`/mod assess` · `/mod dismiss` · `/mod delete` (current preview + confirmation)",
        "`/mod pause` · `/mod resume` · `/mod selftest`",
        "`/mod deletion` · `/mod auto-delete` · `/mod exempt-role`",
        "`/mod config show` · `/mod config history`",
        "Edit IDs: `/mod config monitor`, `staff-channel`, `operator`, `category`, `exempt-role`.",
        "Edit caps: `/mod config budget` · `/mod config call-limits`.",
        "Use the private staff channel and an authorized operator account.",
        "Configuration edits also require Manage Server (or server ownership).",
        "Keys and bot/server identity are configured on the VPS. Timeout remains subject to policy.",
    ])


class SlashCommands:
    def slash_authorized(self, interaction):
        return (self.live is not None and self.policy is not None
                and self.in_scope(interaction.guild_id, interaction.channel_id)
                and str(interaction.channel_id) in self.policy.command_channel_ids
                and not interaction.user.bot and str(interaction.user.id) in self.policy.operator_user_ids
                and self.policy_current())

    async def receive_slash_command(self, interaction):
        if interaction.type != discord.InteractionType.application_command:
            return False
        if self.live is None:
            return True
        if not self.slash_authorized(interaction):
            await self.interaction_notice(interaction, "Use the private staff channel with an authorized moderator account.")
            return True
        try:
            group, name, values = parse(interaction.data)
            if group:
                event = (name, values)
            else:
                arguments = tuple(str(values[field["name"]]) for field in COMMANDS[name][1]
                                  if field["name"] in values and values[field["name"]] != "show")
                event = CommandRequest(str(interaction.guild_id), str(interaction.channel_id), str(interaction.user.id),
                                       "timeout" if name == "timeout-user" else name, arguments=arguments)
        except SetupError as error:
            await self.interaction_notice(interaction, str(error))
            return True
        except (ValueError, TypeError):
            await self.interaction_notice(interaction, "Choose valid options from the /mod menu. Raw IDs must be numeric Discord IDs.")
            return True
        if not group and self.stop_control(event):
            # Safety controls bypass backlog just like their message equivalents.
            content = self.apply_stop_control(str(interaction.id), event, notify_channel=False)
            await self.interaction_notice(interaction, content or "This safety command was already processed. Use /mod status.")
            return True
        if self.queue.full():
            await self.interaction_notice(interaction, "Moderation is busy. Try again shortly.")
            return True
        generation = self.generation
        public_review = not group and name in ("incident", "explain")
        if not await self.defer_interaction(interaction, ephemeral=not public_review):
            return True
        if generation != self.generation or not self.slash_authorized(interaction):
            await self.interaction_notice(interaction, "Configuration or connection changed. Please try again.", deferred=True)
            return True
        try:
            self.queue.put_nowait((generation, "slash_config" if group else "slash_command",
                                   (interaction, event, asyncio.get_running_loop().time() + 30)))
        except asyncio.QueueFull:
            await self.interaction_notice(interaction, "Moderation is busy. No command was run.", deferred=True)
        return True

    async def handle_slash_command(self, value, *, configuration=False):
        interaction, event, deadline = value
        now = asyncio.get_running_loop().time()
        if now > deadline or now < self.next_command_at or not self.slash_authorized(interaction):
            await self.interaction_notice(interaction, "Command not run. Recheck the staff channel and try again.", deferred=True)
            return
        self.next_command_at = now + 1
        if configuration:
            from .remote_settings import handle_settings
            await handle_settings(self, interaction, *event, deadline=deadline)
            return
        content = self.live.command(event, str(interaction.id), self.online)
        if content:
            if event.command == "help":
                content = help_card()
            content = await self.prepare_manual_delete(content)
            content = await self.refresh_assessment_messages(content, event)
        await self.interaction_notice(interaction, content or "This command was already processed or is no longer authorized.", deferred=True)
