"""Bounded private previews of saved evidence; no Discord fetch or provider calls."""

import unicodedata

from .models import MessageEvent


def units(value):
    return len(value.encode("utf-16-le")) // 2


def validated_evidence(config, incident, evidence=None):
    evidence = incident["evidence"] if evidence is None else evidence
    if not isinstance(evidence, list) or not evidence or len(evidence) > config.storage.max_messages:
        raise ValueError("Saved evidence unavailable")
    result = []
    seen = set()
    for item in evidence:
        event = MessageEvent.from_dict({key: value for key, value in item.items()
                                       if key not in {"version", "fingerprint"}})
        if (event.guild_id != config.guild_id or event.channel_id not in config.monitored_channel_ids
                or event.author_id != incident["author_id"] or event.author_id == config.bot_user_id
                or event.is_bot or event.is_webhook or event.is_thread or event.has_attachments
                or item.get("version") != event.version or event.message_id in seen):
            raise ValueError("Saved evidence unavailable")
        seen.add(event.message_id)
        result.append({"content": event.content, "channel_id": event.channel_id,
                       "message_id": event.message_id,
                       "url": f"https://discord.com/channels/{event.guild_id}/{event.channel_id}/{event.message_id}"})
    return result


def saved_evidence(engine, incident):
    try:
        items = validated_evidence(engine.config, incident)
        return {"available": True, "items": items}
    except (KeyError, TypeError, ValueError, AttributeError, UnicodeError):
        return {"available": False, "items": []}


def excerpt(text, budget):
    # Keep original evidence untouched. Flatten whitespace and remove control/bidi
    # characters only in the displayed excerpt; escape Markdown and neutralize links/mentions.
    normalized = " ".join("".join(char if not unicodedata.category(char).startswith("C")
                                   else " " for char in text).split())
    pieces, used = [], 0
    for char in normalized:
        piece = ("\\" + char if char in "\\`*_{}[]()<>#+.!|~-" else
                 char + "\u200b" if char in "@:" else char)
        size = units(piece)
        if used + size > max(0, budget - 3):
            return "".join(pieces) + "...", True
        pieces.append(piece)
        used += size
    return "".join(pieces), normalized != text


def format_evidence(view, budget):
    if not view.get("available"):
        return "**Saved message text**\nUnavailable for this scope.\n"
    items = view["items"]
    # Three clickable links fit the portrait layout and the one-message response limit.
    links = "\n".join(f"[Open message {index}](<{item['url']}>)" for index, item in enumerate(items[:3], 1))
    extra = f"\nShowing 3 of {len(items)} source links." if len(items) > 3 else ""
    sources = "**Source messages**\n" + links + extra + "\n"
    texts = list(dict.fromkeys(item["content"] for item in items))
    header = "**Saved text preview**\n"
    note = "*Saved snapshot; text may now be edited or deleted.*\n"
    if len(texts) > 1:
        note = f"*First of {len(texts)} distinct saved texts.*\n" + note
    available = max(0, budget - units(header + note + sources + "> \n") - 35)
    preview, changed = excerpt(texts[0], min(500, available))
    if changed:
        note = "*Excerpt; formatting normalized.*\n" + note
    result = header + "> " + preview + "\n" + note + sources
    if units(result) > budget:
        result = "**Saved evidence**\nPreview omitted to fit this reply.\n"
    return result if units(result) <= budget else ""
