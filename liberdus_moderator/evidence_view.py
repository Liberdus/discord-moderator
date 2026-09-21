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


def code_excerpt(text, budget):
    """Literal, portrait-width text; no Markdown escapes or injectable code fences."""
    normalized = " ".join("".join(char if not unicodedata.category(char).startswith("C") else " "
                                   for char in text).split())
    normalized = normalized.replace("`", "'").replace("@", "@\u200b").replace(":", ":\u200b")
    # Bound before wrapping; account for newlines and non-BMP characters afterward.
    chars, count = [], 0
    for char in normalized:
        if count + units(char) > max(0, budget - 3):
            break
        chars.append(char)
        count += units(char)
    clipped = len(chars) < len(normalized)
    candidate = "".join(chars) + ("..." if clipped else "")
    # Each UTF-16 unit counts as at most one column here; wide CJK is also charged two.
    lines, line, width = [], "", 0
    for word in candidate.split(" "):
        word_width = sum(0 if c == "\u200b" or unicodedata.combining(c) else
                         2 if unicodedata.east_asian_width(c) in {"W", "F"} else 1 for c in word)
        if line and width + 1 + word_width > 32:
            lines.append(line); line, width = "", 0
        if line:
            line += " "; width += 1
        for char in word:
            size = (0 if char == "\u200b" or unicodedata.combining(char) else
                    2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1)
            if width + size > 32:
                lines.append(line); line, width = "", 0
            line += char; width += size
    if line:
        lines.append(line)
    result = "\n".join(lines)
    while units(result) > budget and result:
        result = result[:-1]
        clipped = True
    if clipped and not result.endswith("..."):
        while result and units(result) > max(0, budget - 3):
            result = result[:-1]
        result += "." * min(3, budget)
    return result, clipped or normalized != text


def format_saved_box(view, incident, budget):
    """Second code box holds text and IDs; source links remain outside it."""
    reference = ("\n\nREFERENCE\n" + "-" * 32 + "\nIncident ID\n" + incident["id"]
                 + "\n\nAuthor ID\n" + incident["author_id"])
    frame = "```\nSAVED MESSAGE\n" + "-" * 32 + "\n"
    if not view.get("available"):
        return frame + "Unavailable for this scope." + reference + "\n```\n"
    items = view["items"]
    texts = list(dict.fromkeys(item["content"] for item in items))
    links = "**Open original messages**\n" + "\n".join(
        f"[Open message {index}](<{item['url']}>)" for index, item in enumerate(items[:3], 1)) + "\n"
    if len(items) > 3:
        links += f"Showing 3 of {len(items)} source links.\n"
    note = "*Saved excerpt; layout normalized. Messages may have changed.*\n"
    if len(texts) > 1:
        note += f"*First of {len(texts)} distinct saved texts.*\n"
    fixed = frame + reference + "\n```\n" + links + note
    available = max(0, min(500, budget - units(fixed)))
    preview, _ = code_excerpt(texts[0], available)
    return frame + preview + reference + "\n```\n" + links + note
