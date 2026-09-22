"""Bounded Markdown for Discord cards; inputs are trusted labels/validated fields.

Member-supplied excerpts must use evidence_view.excerpt before reaching a card.
"""
from .evidence_view import units
import re

def panel(title, lines):
    lines = [str(line) for line in lines]
    rows = ["## " + title]
    for index, line in enumerate(lines):
        if line and set(line) == {'-'}:
            continue
        if index + 1 < len(lines) and lines[index + 1] and set(lines[index + 1]) == {'-'}:
            heading = re.sub(r'\b(jev|ai|utc)\b', lambda match: match[0].upper(), line.capitalize(), flags=re.I)
            rows.append("\n### " + heading)
        elif re.fullmatch(r'(?:[0-9a-f]{32}|[0-9]{15,20})', line) or line.startswith('!mod '):
            rows.append('`' + line + '`')
        else:
            rows.append(line)
    # Leave room for a confirmation controls card and appended fixed notices.
    text = "\n".join(rows)
    if units(text) > 3400:
        text = text.encode('utf-16-le')[:6794].decode('utf-16-le', errors='ignore').rstrip() + '...'
    return text


def framed(content):
    # Kept as the shared text-normalization entry point; card containers provide
    # the visual boundary instead of ASCII borders or a monospace code block.
    text = str(content)
    return text if text.startswith(('## ', '**')) else panel("Liberdus Moderator", text.splitlines())
