"""Role metadata for the pinned discord.py runtime.

Member.roles omits uncached roles. _roles retains the exact membership IDs.
Known membership includes @everyone, so an empty snapshot means unavailable.
"""
from .models import validate_ids


PROTECTED_TIMEOUT_PERMISSIONS = (
    'administrator', 'manage_guild', 'moderate_members', 'manage_messages',
    'kick_members', 'ban_members', 'manage_roles', 'manage_channels',
)


def membership_roles(member, guild_id):
    raw = member._roles  # Pinned SDK interface; never fall back to cached Member.roles.
    if not isinstance(raw, (list, tuple)):
        # discord.py SnowflakeList subclasses array.array.
        from array import array
        if not isinstance(raw, array):
            raise ValueError('Membership roles unavailable')
    if any(type(role) not in (int, str) for role in raw):
        raise ValueError('Invalid membership role ID')
    roles = validate_ids(tuple(str(role) for role in raw), 'membership roles', maximum=250)
    guild_id = str(guild_id)
    return validate_ids(tuple(sorted(set(roles) | {guild_id})), 'membership roles', maximum=250)


def checked_membership(member, guild_id, user_id):
    if (str(member.id) != str(user_id) or str(member.guild.id) != str(guild_id)
            or type(member.bot) is not bool or member.bot):
        raise ValueError('Unexpected member identity')
    return membership_roles(member, guild_id)


def require_complete_role_cache(member):
    """Do not derive privilege or hierarchy from an incomplete guild-role cache."""
    identities = membership_roles(member, member.guild.id)
    for identity in identities:
        role = member.guild.get_role(int(identity))
        if role is None or str(role.id) != identity or str(role.guild.id) != str(member.guild.id):
            raise ValueError('Role privilege or hierarchy data unavailable')
    return identities
