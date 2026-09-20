"""The installer bundles the core here using profile-local relative imports."""
from .liberdus_moderator.hermes_plugin import register

__all__ = ["register"]
