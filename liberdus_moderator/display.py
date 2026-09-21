"""Narrow fixed-text panels and transport-level message boundaries."""
import textwrap

WIDTH = 32
BOUNDARY = "━━━━━━━━━━━━━━━━━━━━"


def panel(title, lines):
    rows = []
    for line in lines:
        rows.extend(textwrap.wrap(str(line), width=WIDTH, break_on_hyphens=False) or [""])
    return "**" + title + "**\n```\n" + "\n".join(rows)[:1850] + "\n```"


def framed(content):
    # Preserve existing Markdown and clickable links. No extra Discord messages.
    text = str(content)
    if "```" not in text:
        text = panel("Liberdus Moderator", text.replace("**", "").replace("`", "").splitlines())
    return BOUNDARY + "\n" + text + "\n" + BOUNDARY
