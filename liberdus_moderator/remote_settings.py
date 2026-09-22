"""Allowlisted Discord configuration edits; keys and identity remain local."""

import asyncio
from dataclasses import replace

from .configure_jev import policy_text
from .display import panel
from .doctor import Inventory, inspect_channels
from .instance import load_policy
from .models import validate_id
from .preflight import permissions, VIEW, SEND
from .secure_files import SetupError, write_private
from .setup_wizard import budget

AUDIT_KEY = "configuration_changes_v1"
MANAGE_GUILD = 1 << 5


def changed_ids(current, action, identity):
    validate_id(identity, "selected_id")
    if action == "add":
        return tuple(dict.fromkeys((*current, identity)))
    if action == "remove":
        return tuple(item for item in current if item != identity)
    raise SetupError("Choose add or remove.")


def candidate(policy, operation, values, actor):
    """Construct only the explicitly editable fields, preserving all others."""
    if operation == "alert-role":
        if values["action"] not in {"set", "off"}:
            raise SetupError("Choose Set or Off.")
        role = values["role"] if values["action"] == "set" else None
        if role == policy.guild_id:
            raise SetupError("Choose a staff role other than @everyone.")
        return replace(policy, rules=replace(policy.rules, review_alert_role_id=role))
    if operation == "monitor":
        return replace(policy, monitored_channel_ids=changed_ids(policy.monitored_channel_ids, values["action"], values["channel"]))
    if operation == "staff-channel":
        validate_id(values["channel"], "staff_channel")
        return replace(policy, command_channel_ids=(values["channel"],))
    if operation == "operator":
        if values["action"] == "remove" and values["user"] == actor:
            raise SetupError("You cannot remove your own operator access through Discord. Use another authorized administrator.")
        return replace(policy, operator_user_ids=changed_ids(policy.operator_user_ids, values["action"], values["user"]))
    if operation == "category":
        fields = {"included": "included_category_ids", "excluded": "excluded_category_ids"}
        if values["boundary"] not in fields:
            raise SetupError("Choose an included or excluded category boundary.")
        field = fields[values["boundary"]]
        return replace(policy, **{field: changed_ids(getattr(policy, field), values["action"], values["category"])})
    if operation == "exempt-role":
        roles = changed_ids(policy.classifier.exempt_role_ids, values["action"], values["role"])
        return replace(policy, classifier=replace(policy.classifier, exempt_role_ids=roles))
    if operation in ("budget", "call-limits"):
        if not policy.ai_enabled:
            raise SetupError("Enable JEV with its private credential on the VPS before changing screening caps.")
        if operation == "budget":
            daily, lifetime = budget(str(values["daily"]), 10), budget(str(values["lifetime"]), 100)
            fields = {"daily_budget_microusd": daily, "total_budget_microusd": lifetime}
        else:
            daily, lifetime = values["daily"], values["lifetime"]
            fields = {"max_daily_calls": daily, "max_total_calls": lifetime}
        if daily > lifetime:
            raise SetupError("The daily cap cannot exceed the lifetime cap.")
        return replace(policy, classifier=replace(policy.classifier, **fields))
    raise SetupError("This setting cannot be changed through Discord.")


def validate_edit(policy, proposed, operation, values, actor, get):
    inventory = Inventory(get, policy.guild_id)
    guild = inventory(f"/guilds/{policy.guild_id}")
    member = inventory(f"/guilds/{policy.guild_id}/members/{actor}")
    user = member.get("user", {})
    if (guild.get("id") != policy.guild_id or user.get("id") != actor or user.get("bot")
            or not permissions(guild, member["roles"], actor, {"permission_overwrites": []}) & MANAGE_GUILD):
        raise SetupError("Configuration edits require Manage Server or server ownership, plus an authorized operator ID.")
    old_channel = inventory(f"/channels/{policy.command_channel_ids[0]}")
    old_access = permissions(guild, member["roles"], actor, old_channel)
    if not old_access & VIEW or not old_access & SEND:
        raise SetupError("Your staff-channel access changed. No configuration was saved.")
    # Recheck the original channel's privacy as well as the proposed scope.
    if permissions(guild, (), None, old_channel) & VIEW:
        raise SetupError("The current staff channel must remain private.")
    report = inspect_channels(proposed, inventory, deletion=proposed.actions_enabled)
    if not report["ok"]:
        raise SetupError("Configuration not saved. " + " ".join(report["issues"])[:900])
    staff = inventory(f"/channels/{proposed.command_channel_ids[0]}")
    access = permissions(guild, member["roles"], actor, staff)
    if not access & VIEW or not access & SEND:
        raise SetupError("You need View Channel and Send Messages in the new staff channel.")
    if operation == "operator" and values["action"] == "add":
        target = inventory(f"/guilds/{policy.guild_id}/members/{values['user']}")
        user = target.get("user", {})
        if user.get("id") != values["user"] or user.get("bot"):
            raise SetupError("The operator must be a human member of this server.")
        access = permissions(guild, target["roles"], user["id"], staff)
        if not access & VIEW or not access & SEND:
            raise SetupError("Give that member access to the private staff channel in Discord first.")
    if operation == "category" and values["action"] == "add":
        channel = inventory(f"/channels/{values['category']}")
        if channel.get("type") != 4:
            raise SetupError("Choose a category from this server.")
    if operation == "exempt-role" and values["action"] == "add":
        if values["role"] == policy.guild_id or values["role"] not in {role["id"] for role in guild["roles"]}:
            raise SetupError("Choose a server role other than @everyone.")
    if proposed.rules.review_alert_role_id:
        role = next((role for role in guild["roles"] if role["id"] == proposed.rules.review_alert_role_id), None)
        if role is None or role["id"] == policy.guild_id:
            raise SetupError("Choose an available staff alert role other than @everyone.")
        bot = inventory(f"/guilds/{policy.guild_id}/members/{policy.bot_user_id}")
        if not role.get("mentionable") and not permissions(guild, bot["roles"], policy.bot_user_id, staff) & (1 << 17):
            raise SetupError("Allow this role to be mentioned, or grant the bot Mention @everyone, @here, and All Roles in the staff channel. The bot only pings the selected role.")
        if not permissions(guild, (role["id"],), None, staff) & VIEW:
            raise SetupError("Give the alert role View Channel access to the private staff channel first.")
    return report


def persist_change(home, store, before, after, actor, operation, values, interaction_id, now):
    if load_policy(home).policy_hash != before.policy_hash:
        raise SetupError("The configuration changed during validation. Reopen /mod config show and try again.")
    record = dict(interaction_id=interaction_id, actor=actor, operation=operation, values=values,
                  before=before.policy_hash, after=after.policy_hash, at=now, state="requested")
    history = store.get_setting(AUDIT_KEY, [])
    history = (history if isinstance(history, list) else [])[-49:] + [record]
    # Record the intent before the atomic file replacement. A crash or disk
    # error can leave 'requested'; never misreport that as a completed change.
    store.set_setting(AUDIT_KEY, history)
    write_private(home / "moderation.toml", policy_text(after), replace=True)
    record["state"] = "saved"
    store.set_setting(AUDIT_KEY, history)


def settings_card(policy, store):
    def ids(items):
        return ", ".join(items) or "None"
    return panel("Moderation settings", [
        "Staff channel: " + ids(policy.command_channel_ids),
        "Operators: " + ids(policy.operator_user_ids),
        "Monitored channels: " + ids(policy.monitored_channel_ids),
        "Included categories: " + ids(policy.included_category_ids),
        "Excluded categories: " + ids(policy.excluded_category_ids),
        "Exempt roles: " + ids(policy.classifier.exempt_role_ids),
        "Review alert role: " + (policy.rules.review_alert_role_id or "Off") + " · flagged score below 0.90 · five-minute cooldown",
        f"JEV caps: ${policy.classifier.daily_budget_microusd / 1_000_000:g}/day; ${policy.classifier.total_budget_microusd / 1_000_000:g} lifetime",
        f"Call caps: {policy.classifier.max_daily_calls}/day; {policy.classifier.max_total_calls} lifetime",
        "Automatic deletion: four approved concerns at score >= 0.90, with current-evidence checks.",
        "Use /mod config to edit IDs or caps. Changes reconnect the bot and invalidate old evidence.",
        "History, action switches and spending counters are preserved. Keys are managed on the VPS.",
        "Slash registration: " + str(store.get_setting("slash_commands_state", "not_registered")),
    ])


def history_card(store):
    from datetime import datetime, timezone
    rows = store.get_setting(AUDIT_KEY, [])
    lines = []
    for row in reversed(rows[-10:] if isinstance(rows, list) else []):
        stamp = datetime.fromtimestamp(row["at"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"{stamp} · {row['operation']} · {row['state']} · Staff ID {row['actor']}")
    return panel("Configuration history", lines or ["No configuration edits recorded."])


async def handle_settings(service, interaction, operation, values, *, deadline):
    before = service.policy
    reload_needed = False
    content = None
    try:
        if operation == "show":
            content = settings_card(before, service.store)
        elif operation == "history":
            content = history_card(service.store)
        elif not hasattr(service, "save_configuration"):
            raise SetupError("Discord configuration edits require the standalone runner. Edit this Hermes profile on its host.")
        else:
            identity = str(interaction.id)
            if service.store.db.execute("SELECT 1 FROM command_receipts WHERE message_id=?", (identity,)).fetchone():
                raise SetupError("This configuration command was already processed. Use /mod config show.")
            service.store.db.execute("INSERT INTO command_receipts VALUES(?,?)", (identity, service.live.engine._now()))
            service.store.db.execute("DELETE FROM command_receipts WHERE message_id IN (SELECT message_id FROM command_receipts ORDER BY created_at DESC LIMIT -1 OFFSET 5000)")
            proposed = candidate(before, operation, values, str(interaction.user.id))
            generation = service.generation
            from .preflight import DiscordReader
            get = DiscordReader(service.credentials.get("discord"))
            await asyncio.wait_for(asyncio.to_thread(validate_edit, before, proposed, operation, values,
                                                     str(interaction.user.id), get), timeout=20)
            if (generation != service.generation or asyncio.get_running_loop().time() > deadline
                    or not service.slash_authorized(interaction)):
                raise SetupError("Configuration or connection changed during validation. No configuration was saved.")
            if proposed.policy_hash == before.policy_hash:
                content = "The requested values are already configured. No reconnect is needed."
            else:
                service.save_configuration(proposed, interaction, operation, values)
                content = panel("Configuration saved", ["Setting: " + operation,
                    *(f"{key}: {value}" for key, value in values.items()),
                    "The bot will reconnect to apply this change. History and spending counters are preserved.",
                    "Previous evidence and confirmations become historical. Use /mod config show after reconnecting."])
    except SetupError as error:
        content = str(error)
    except Exception:
        content = "The configuration change could not be verified. Check /mod config show and history before retrying. Sensitive error details were withheld."
    finally:
        # This also detects replacement followed by a disk/audit-write failure.
        # Stop admission immediately, finish the reply, then reopen with the
        # actual on-disk policy rather than running against mismatched state.
        try:
            reload_needed = service.current_policy().policy_hash != before.policy_hash
        except Exception:
            reload_needed = True
        if reload_needed and hasattr(service, "request_configuration_reload"):
            service.configuration_reload_pending = True
            service.coverage_gap("scope_changed")
        try:
            await service.interaction_notice(interaction, content or "Configuration command interrupted. Check /mod config show.", deferred=True)
        finally:
            if reload_needed and hasattr(service, "request_configuration_reload"):
                service.request_configuration_reload()
