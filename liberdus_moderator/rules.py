"""Deterministic review candidates, independently implemented without upstream code."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
import re
import unicodedata
from urllib.parse import urlsplit

from .config import Config
from .models import validate_timestamp


_URL = re.compile(r"https?://[^\s<>\u0000-\u001f]+", re.IGNORECASE)


def normalize_content(text: str) -> str:
    """Normalize prose conservatively; preserve full URL spelling and case."""
    if not isinstance(text, str):
        raise ValueError("content must be a string")
    pieces = []
    end = 0
    for match in _URL.finditer(text):
        pieces.append(unicodedata.normalize("NFC", text[end:match.start()]).casefold())
        pieces.append(match.group())
        end = match.end()
    pieces.append(unicodedata.normalize("NFC", text[end:]).casefold())
    return " ".join("".join(pieces).split())


def content_fingerprint(text: str) -> str:
    return hashlib.sha256(normalize_content(text).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Match:
    group_key: str
    rule_id: str
    author_id: str
    reason: str
    evidence: tuple[dict, ...]
    expires_at: float


def _key(rule_id: str, *values: str) -> str:
    encoded = json.dumps([rule_id, *values], separators=(",", ":"))
    return f"{rule_id}:{hashlib.sha256(encoded.encode()).hexdigest()}"


def _order(row: dict) -> tuple:
    return (row["created_at"], row["channel_id"], row["message_id"], row.get("version", ""))


def _substantive(text: str, min_chars: int) -> bool:
    normalized = normalize_content(text)
    prose = _URL.sub("", text)
    return len(normalized) >= min_chars and any(character.isalnum() for character in prose)


def _blocked_domains(content: str, blocked: set[str]) -> set[str]:
    found = set()
    for match in _URL.finditer(content):
        # Discord/Markdown punctuation around a URL is not part of its hostname.
        candidate = match.group().rstrip(".,;:!?)]}'\"")
        try:
            parsed = urlsplit(candidate)
            hostname = parsed.hostname
            if hostname is None:
                continue
            hostname = hostname.rstrip(".").encode("idna").decode("ascii").lower()
        except (ValueError, UnicodeError):
            continue
        if hostname in blocked:
            found.add(hostname)
    return found


def find_matches(config: Config, rows: list[dict], now: float) -> list[Match]:
    """Find active patterns in current rows; original post times define windows.

    The caller supplies validated current versions. Defensive scope checks and
    message-ID deduplication also keep accidental mixed snapshots from counting.
    Attachments are never inspected: only a message's supplied text is matched.
    """
    now = validate_timestamp(now, "now")
    if config.mode == "off":
        return []
    current: dict[tuple[str, str, str], dict] = {}
    for row in rows:
        if row["guild_id"] != config.guild_id or row["channel_id"] not in config.monitored_channel_ids:
            continue
        if row["author_id"] == config.bot_user_id or any(row.get(flag, False) for flag in ("is_bot", "is_webhook", "is_thread")):
            continue
        if row["created_at"] > now or (row.get("edited_at") or row["created_at"]) > now or not row["content"].strip():
            continue
        identity = (row["guild_id"], row["channel_id"], row["message_id"])
        previous = current.get(identity)
        rank = ((row.get("edited_at") or row["created_at"]), row.get("version", ""))
        if previous is None or rank > ((previous.get("edited_at") or previous["created_at"]), previous.get("version", "")):
            current[identity] = row
    evidence = sorted(current.values(), key=_order)
    matches: list[Match] = []
    cross: dict[tuple[str, str], list[dict]] = defaultdict(list)
    local: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    exceptions = [(item, content_fingerprint(item.content)) for item in config.rules.approved_crossposts]
    blocked = set(config.rules.blocked_domains)
    for row in evidence:
        author = row["author_id"]
        channel = row["channel_id"]
        fingerprint = content_fingerprint(row["content"])
        domains = _blocked_domains(row["content"], blocked)
        if domains and row["created_at"] > now - config.storage.retention_seconds:
            matches.append(Match(
                _key("blocked_domain", config.guild_id, channel, row["message_id"]),
                "blocked_domain", author, f"Configured exact blocked domain: {', '.join(sorted(domains))}. Context requires moderator review.",
                (dict(row),), row["created_at"] + config.storage.retention_seconds,
            ))
        if not _substantive(row["content"], config.rules.repeat_min_chars):
            continue
        if row["created_at"] > now - config.rules.same_channel_window_seconds:
            local[(author, fingerprint, channel)].append(row)
        exempt = any(author in item.author_ids and channel in item.channel_ids and fingerprint == approved for item, approved in exceptions)
        if row["created_at"] > now - config.rules.repeat_window_seconds and not exempt:
            cross[(author, fingerprint)].append(row)
    for (author, fingerprint), group in sorted(cross.items()):
        latest_by_channel: dict[str, float] = {}
        for row in group:
            latest_by_channel[row["channel_id"]] = max(latest_by_channel.get(row["channel_id"], 0), row["created_at"])
        if len(latest_by_channel) < config.rules.repeat_min_channels:
            continue
        expiry = sorted(latest_by_channel.values(), reverse=True)[config.rules.repeat_min_channels - 1] + config.rules.repeat_window_seconds
        matches.append(Match(
            _key("cross_channel_repeat", config.guild_id, author, fingerprint), "cross_channel_repeat", author,
            f"Same member posted normalized matching text in {len(latest_by_channel)} distinct monitored channels within {config.rules.repeat_window_seconds} seconds.",
            tuple(dict(row) for row in group), expiry,
        ))
    for (author, fingerprint, channel), group in sorted(local.items()):
        if len(group) < config.rules.same_channel_min_messages:
            continue
        expiry = sorted((row["created_at"] for row in group), reverse=True)[config.rules.same_channel_min_messages - 1] + config.rules.same_channel_window_seconds
        matches.append(Match(
            _key("same_channel_repeat", config.guild_id, author, fingerprint, channel), "same_channel_repeat", author,
            f"Same member posted normalized matching text {len(group)} times in one monitored channel within {config.rules.same_channel_window_seconds} seconds.",
            tuple(dict(row) for row in group), expiry,
        ))
    return sorted(matches, key=lambda match: match.group_key)
