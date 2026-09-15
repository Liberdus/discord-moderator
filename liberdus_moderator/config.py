"""Fail-closed, versioned TOML configuration for the offline report-only core."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
import hashlib
import json
from pathlib import Path
import re
import tomllib
from typing import Any

from .models import validate_id, validate_ids


def _object(value: Any, name: str, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object/table")
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"unknown {name} fields: {', '.join(sorted(map(str, unknown)))}")
    return value


def _positive_int(value: Any, name: str, minimum: int = 1) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def _construct(cls: type, data: dict[str, Any], name: str) -> Any:
    _object(data, name, {f.name for f in fields(cls)})
    try:
        return cls(**data)
    except TypeError as exc:
        raise ValueError(f"invalid {name} fields: {exc}") from exc


@dataclass(frozen=True)
class CrosspostException:
    author_ids: tuple[str, ...]
    channel_ids: tuple[str, ...]
    content: str

    def __post_init__(self) -> None:
        for name in ("author_ids", "channel_ids"):
            object.__setattr__(self, name, validate_ids(getattr(self, name), name, nonempty=True, maximum=500 if name == "channel_ids" else 1000))
        if not isinstance(self.content, str) or not self.content.strip() or len(self.content) > 4000:
            raise ValueError("approved crosspost content must be nonempty text of at most 4000 codepoints")


@dataclass(frozen=True)
class RuleSettings:
    repeat_window_seconds: int = 120
    repeat_min_channels: int = 3
    repeat_min_chars: int = 20
    same_channel_window_seconds: int = 30
    same_channel_min_messages: int = 4
    notification_cooldown_seconds: int = 300
    blocked_domains: tuple[str, ...] = ()
    approved_crossposts: tuple[CrosspostException, ...] = ()

    def __post_init__(self) -> None:
        for name in ("repeat_window_seconds", "repeat_min_chars", "same_channel_window_seconds", "notification_cooldown_seconds"):
            _positive_int(getattr(self, name), name)
        _positive_int(self.repeat_min_channels, "repeat_min_channels", 2)
        _positive_int(self.same_channel_min_messages, "same_channel_min_messages", 2)
        if not isinstance(self.blocked_domains, (list, tuple)):
            raise ValueError("blocked_domains must be an array of domain names")
        if len(self.blocked_domains) > 1000:
            raise ValueError("blocked_domains allows at most 1000 domains")
        domains = []
        for domain in self.blocked_domains:
            if not isinstance(domain, str) or len(domain) > 253 or not domain.isascii():
                raise ValueError("blocked_domains requires ASCII DNS names (use punycode for IDNs)")
            domain = domain.lower()
            if not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in domain.split(".")):
                raise ValueError("blocked_domains accepts exact DNS names, without schemes, paths, wildcards, or ports")
            domains.append(domain)
        if len(set(domains)) != len(domains):
            raise ValueError("blocked_domains cannot contain duplicate names")
        object.__setattr__(self, "blocked_domains", tuple(domains))
        if not isinstance(self.approved_crossposts, (list, tuple)):
            raise ValueError("approved_crossposts must be an array")
        if len(self.approved_crossposts) > 500:
            raise ValueError("approved_crossposts allows at most 500 exceptions")
        exceptions = []
        for item in self.approved_crossposts:
            if isinstance(item, dict):
                item = _construct(CrosspostException, item, "approved_crosspost")
            if not isinstance(item, CrosspostException):
                raise ValueError("approved_crossposts entries must be scoped exception objects")
            exceptions.append(item)
        object.__setattr__(self, "approved_crossposts", tuple(exceptions))


@dataclass(frozen=True)
class StorageSettings:
    database_path: str = "state/moderation.sqlite3"
    retention_seconds: int = 604800
    max_messages: int = 50000
    max_incidents: int = 5000
    max_pending_reports: int = 500
    max_evidence_versions: int = 100

    def __post_init__(self) -> None:
        if not isinstance(self.database_path, str) or not self.database_path.strip() or "\x00" in self.database_path or len(self.database_path) > 4096:
            raise ValueError("database_path must be a nonempty filesystem path")
        for item in fields(self):
            if item.name != "database_path":
                _positive_int(getattr(self, item.name), item.name)


@dataclass(frozen=True)
class Config:
    guild_id: str
    bot_user_id: str
    monitored_channel_ids: tuple[str, ...]
    command_channel_ids: tuple[str, ...]
    operator_user_ids: tuple[str, ...] = ()
    operator_role_ids: tuple[str, ...] = ()
    log_channel_id: str | None = None
    policy_version: str = "pilot-1"
    mode: str = "report_only"
    logs_enabled: bool = False
    rules: RuleSettings = field(default_factory=RuleSettings)
    storage: StorageSettings = field(default_factory=StorageSettings)
    schema_version: int = 1
    ai_enabled: bool = False
    actions_enabled: bool = False

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("only schema_version = 1 is supported")
        validate_id(self.guild_id, "guild_id")
        validate_id(self.bot_user_id, "bot_user_id")
        for name in ("monitored_channel_ids", "command_channel_ids", "operator_user_ids", "operator_role_ids"):
            maximum = 250 if name == "operator_role_ids" else 1000 if name == "operator_user_ids" else 500
            object.__setattr__(self, name, validate_ids(getattr(self, name), name, nonempty=name in ("monitored_channel_ids", "command_channel_ids"), maximum=maximum))
        if not self.operator_user_ids and not self.operator_role_ids:
            raise ValueError("at least one operator user or role ID is required")
        if set(self.monitored_channel_ids) & set(self.command_channel_ids):
            raise ValueError("command channels cannot overlap monitored channels")
        if self.log_channel_id is not None:
            validate_id(self.log_channel_id, "log_channel_id")
            if self.log_channel_id in self.monitored_channel_ids:
                raise ValueError("log channel cannot overlap monitored channels")
        if not isinstance(self.policy_version, str) or not self.policy_version.strip() or len(self.policy_version) > 128 or not self.policy_version.isprintable():
            raise ValueError("policy_version must be nonempty printable text of at most 128 codepoints")
        if not isinstance(self.mode, str) or self.mode not in ("off", "report_only"):
            raise ValueError("mode must be off or report_only; enforcement is not implemented")
        for name in ("logs_enabled", "ai_enabled", "actions_enabled"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")
        if self.ai_enabled or self.actions_enabled:
            raise ValueError("AI and enforcement must remain disabled in this offline build")
        if self.logs_enabled and self.log_channel_id is None:
            raise ValueError("logs_enabled requires log_channel_id")
        if not isinstance(self.rules, RuleSettings) or not isinstance(self.storage, StorageSettings):
            raise ValueError("rules and storage must use their validated settings classes")
        if self.storage.retention_seconds < max(self.rules.repeat_window_seconds, self.rules.same_channel_window_seconds):
            raise ValueError("retention_seconds must cover all repeat rule windows")
        for exception in self.rules.approved_crossposts:
            if not set(exception.channel_ids) <= set(self.monitored_channel_ids):
                raise ValueError("approved crosspost channels must be monitored channels")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        root_keys = {"schema_version", "policy_version", "mode", "logs_enabled", "ai_enabled", "actions_enabled", "scope", "rules", "storage"}
        _object(data, "configuration", root_keys)
        for required in ("schema_version", "policy_version", "scope"):
            if required not in data:
                raise ValueError(f"missing configuration field: {required}")
        scope_keys = {"guild_id", "bot_user_id", "monitored_channel_ids", "command_channel_ids", "operator_user_ids", "operator_role_ids", "log_channel_id"}
        scope = _object(data["scope"], "scope", scope_keys)
        kwargs = {key: value for key, value in data.items() if key not in ("scope", "rules", "storage")}
        kwargs.update(scope)
        kwargs["rules"] = _construct(RuleSettings, data.get("rules", {}), "rules")
        kwargs["storage"] = _construct(StorageSettings, data.get("storage", {}), "storage")
        return _construct(cls, kwargs, "configuration")

    @classmethod
    def from_file(cls, path: str | Path) -> Config:
        with open(path, "rb") as stream:
            return cls.from_dict(tomllib.load(stream))

    @property
    def policy_hash(self) -> str:
        # Bind persisted decisions to the entire effective configuration, including scope.
        encoded = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
