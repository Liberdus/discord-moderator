"""Validated transport-neutral message evidence; no Discord or model dependencies."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import hashlib
import json
import math
from typing import Any


def validate_id(value: Any, name: str) -> str:
    """Keep Discord IDs as positive ASCII decimal strings without coercion."""
    if not isinstance(value, str) or not 1 <= len(value) <= 20 or not value.isascii() or not value.isdecimal():
        raise ValueError(f"{name} must be a positive decimal string of at most 20 digits")
    if int(value) <= 0 or value.startswith("0"):
        raise ValueError(f"{name} must be a canonical positive decimal string")
    return value


def validate_ids(value: Any, name: str, *, nonempty: bool = False, maximum: int = 1000) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be an array of ID strings")
    if len(value) > maximum:
        raise ValueError(f"{name} allows at most {maximum} IDs")
    result = tuple(validate_id(item, name) for item in value)
    if nonempty and not result:
        raise ValueError(f"{name} cannot be empty")
    if len(set(result)) != len(result):
        raise ValueError(f"{name} cannot contain duplicate IDs")
    return result


def validate_timestamp(value: Any, name: str) -> float:
    if type(value) not in (int, float) or value <= 0 or value > 253402300799 or not math.isfinite(value):
        raise ValueError(f"{name} must be a positive finite Unix timestamp")
    return float(value)


@dataclass(frozen=True)
class MessageEvent:
    guild_id: str
    channel_id: str
    message_id: str
    author_id: str
    content: str
    created_at: float
    edited_at: float | None = None
    author_role_ids: tuple[str, ...] = ()
    is_bot: bool = False
    is_webhook: bool = False
    is_thread: bool = False
    has_attachments: bool = False

    def __post_init__(self) -> None:
        for name in ("guild_id", "channel_id", "message_id", "author_id"):
            validate_id(getattr(self, name), name)
        if not isinstance(self.content, str):
            raise ValueError("content must be a string")
        if len(self.content) > 4000:
            raise ValueError("content exceeds the offline text coverage limit of 4000 codepoints")
        object.__setattr__(self, "created_at", validate_timestamp(self.created_at, "created_at"))
        if self.edited_at is not None:
            edited = validate_timestamp(self.edited_at, "edited_at")
            if edited < self.created_at:
                raise ValueError("edited_at cannot precede created_at")
            object.__setattr__(self, "edited_at", edited)
        object.__setattr__(self, "author_role_ids", validate_ids(self.author_role_ids, "author_role_ids", maximum=250))
        for name in ("is_bot", "is_webhook", "is_thread", "has_attachments"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MessageEvent:
        if not isinstance(data, dict):
            raise ValueError("message must be an object")
        unknown = set(data) - {field.name for field in fields(cls)}
        if unknown:
            raise ValueError(f"unknown message fields: {', '.join(sorted(map(str, unknown)))}")
        try:
            return cls(**data)
        except TypeError as exc:
            raise ValueError(f"invalid message fields: {exc}") from exc

    @property
    def modified_at(self) -> float:
        return self.edited_at if self.edited_at is not None else self.created_at

    @property
    def version(self) -> str:
        evidence = self.to_dict()
        # Roles are a set of observed privileges, not an ordered list.
        evidence["author_role_ids"] = sorted(self.author_role_ids)
        encoded = json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["author_role_ids"] = list(self.author_role_ids)
        return result
