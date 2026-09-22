"""Discord Components V2 cards shared by channel and ephemeral replies."""
import discord

from .display import framed


def card_view(content, *, controls=(), accent_colour=0x5865F2):
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(discord.ui.TextDisplay(framed(content)), accent_colour=accent_colour))
    if controls:
        view.add_item(discord.ui.Container(discord.ui.TextDisplay('### Confirmation\nReview the current content above.'),
                                          discord.ui.ActionRow(*controls)))
    return view
