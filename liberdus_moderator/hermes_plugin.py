"""Hermes plugin registration. Importing this module never connects Discord."""

import importlib.util

PLATFORM = "liberdus_moderator"


def explicit_activation(_config=None):
    # The Hermes env pass supplies a synthetic enabled=True config, so checking
    # that argument alone would accidentally auto-enable the platform.
    from hermes_cli.config import read_user_config_raw
    from hermes_constants import get_hermes_home
    home = get_hermes_home()
    if home.name != "liberdus-mod" or home.parent.name != "profiles":
        return False
    try:
        raw = read_user_config_raw()
        platforms = raw.get("platforms", {})
        return (platforms.get(PLATFORM, {}).get("enabled") is True
                and platforms.get("discord", {}).get("enabled") is False)
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def factory(config):
    from .hermes_adapter import ModerationAdapter
    return ModerationAdapter(config)


def register(ctx):
    ctx.register_platform(
        name=PLATFORM, label="Liberdus Moderation", adapter_factory=factory,
        check_fn=lambda: importlib.util.find_spec("discord") is not None,
        is_connected=explicit_activation, validate_config=explicit_activation,
        required_env=[], allow_update_command=False, max_message_length=2000,
        install_hint="Use the verified existing Hermes Discord installation; no automatic dependency installation.",
    )
