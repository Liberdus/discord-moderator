"""Inspect visible SDK text/buttons across legacy and component review replies."""
import discord


def visible_text(call):
    body = call.args[0] if call.args else call.kwargs.get("content")
    if body is not None:
        return body
    return "\n\n".join(item.content for item in call.kwargs["view"].walk_children()
                       if isinstance(item, discord.ui.TextDisplay))


def buttons(view):
    return [item for item in view.walk_children() if isinstance(item, discord.ui.Button)]


def payload_text(payload):
    def texts(items):
        for item in items:
            if item['type'] == 10:
                yield item['content']
            yield from texts(item.get('components', []))
    return '\n\n'.join(texts(payload.get('components', [])))
