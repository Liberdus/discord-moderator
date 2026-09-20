"""Private commands for an adapter to invoke using authenticated Discord metadata.

These are local handlers, not registered Discord slash commands. Never populate
the identity envelope from message text or a model's output.
"""

from dataclasses import dataclass, field
import re

from .engine import Engine


def _id(value):
    return isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,19}", value)


@dataclass(frozen=True)
class CommandRequest:
    guild_id: str
    channel_id: str
    user_id: str
    command: str
    role_ids: tuple[str, ...] = field(default_factory=tuple)
    arguments: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self):
        if not all(_id(value) for value in (self.guild_id, self.channel_id, self.user_id)):
            raise ValueError("Command identity must use numeric Discord ID strings")
        if not isinstance(self.role_ids, tuple) or len(self.role_ids) > 250 or not all(_id(value) for value in self.role_ids):
            raise ValueError("role_ids must be a tuple of numeric ID strings")
        if not isinstance(self.command, str) or not re.fullmatch(r"[a-z-]{1,32}", self.command):
            raise ValueError("Invalid command name")
        if not isinstance(self.arguments, tuple) or len(self.arguments) > 4 or not all(isinstance(value, str) and len(value) <= 200 for value in self.arguments):
            raise ValueError("Invalid command arguments")

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("Command request must be an object")
        data = dict(data)
        if set(data) - {"guild_id", "channel_id", "user_id", "command", "role_ids", "arguments"}:
            raise ValueError("Unknown command request fields")
        for key in ("role_ids", "arguments"):
            if key in data:
                if not isinstance(data[key], list):
                    raise ValueError(f"{key} must be an array")
                data[key] = tuple(data[key])
        try:
            return cls(**data)
        except TypeError as error:
            raise ValueError("Missing or invalid command fields") from error


def handle_command(engine: Engine, request: CommandRequest):
    config, store = engine.config, engine.store
    authorized = (
        store.get_setting("policy_hash") == config.policy_hash
        and request.guild_id == config.guild_id
        and request.channel_id in config.command_channel_ids
        and (request.user_id in config.operator_user_ids or bool(set(request.role_ids) & set(config.operator_role_ids)))
    )
    if not authorized:
        return {"authorized": False, "ok": False, "error": "not_authorized", "ai_calls": 0}
    name, arguments = request.command, request.arguments
    result = {"authorized": True, "ok": True, "ai_calls": 0, "public_actions": []}
    if name == "status" and not arguments:
        result["data"] = engine.status()
    elif name in ("pause", "resume") and not arguments:
        if name == "resume" and config.mode == "off":
            return {**result, "ok": False, "error": "configuration_disables_moderation"}
        engine.set_paused(name == "pause")
        result["data"] = engine.status()
    elif name == "logs" and arguments in (("on",), ("off",)):
        if arguments == ("on",) and not config.log_channel_id:
            return {**result, "ok": False, "error": "no_log_channel_configured"}
        with store.transaction():
            store.set_setting("logs_enabled", arguments == ("on",))
            if arguments == ("off",):
                store.db.execute("UPDATE reports SET status='cancelled' WHERE status='pending' AND kind='log'")
        result["data"] = {"logs_enabled": arguments == ("on",)}
    elif name in ("incident", "explain") and len(arguments) == 1:
        incident = store.incident(arguments[0])
        if incident is None:
            return {**result, "ok": False, "error": "incident_not_found"}
        result["data"] = incident
        if name == "explain":
            result["note"] = "Saved rule evidence only; no actions, policy changes, or AI calls."
    elif name == "selftest" and not arguments:
        from .selftest import run_selftest
        result["data"] = run_selftest()
    elif name == "approve":
        return {**result, "ok": False, "error": "enforcement_not_implemented"}
    else:
        return {**result, "ok": False, "error": "unknown_command_or_arguments"}
    # A future sender must still validate destination and suppress all mentions.
    result["destination"] = {"guild_id": request.guild_id, "channel_id": request.channel_id}
    result["allowed_mentions"] = {"parse": [], "users": [], "roles": [], "replied_user": False}
    return result
