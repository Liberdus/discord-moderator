"""Optional Hermes host for the shared Discord moderation service."""
import importlib.metadata
import inspect
from pathlib import Path
import subprocess

import discord
from agent.secret_scope import current_secret_scope
from gateway.config import Platform, load_gateway_config
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.platforms._shared import get_scoped_secret
from hermes_cli.config import read_user_config_raw
from hermes_constants import get_hermes_home

from .config import Config
from .hermes_plugin import PLATFORM, explicit_activation
from .discord_service import (DiscordService, PilotClient, snapshot, assessment_buttons,
                              REVIEW_BUTTONS, LEGACY_REVIEW_BUTTONS)

VERIFIED_COMMIT = "c1488ac947c9bc33fd65ec464548dc9d8edd6122"


def verify_runtime():
    root = Path(inspect.getfile(BasePlatformAdapter)).resolve().parents[2]
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                            capture_output=True, text=True, timeout=5)
    if result.returncode or result.stdout.strip() != VERIFIED_COMMIT:
        raise ValueError("Hermes commit requires compatibility review")
    changed = subprocess.run(["git", "-C", str(root), "diff", "HEAD", "--quiet", "--",
                              "gateway", "hermes_cli/plugins.py", "agent/secret_scope.py"],
                             capture_output=True, timeout=5)
    if changed.returncode or importlib.metadata.version("discord.py") != "2.7.1":
        raise ValueError("Runtime interfaces or Discord library require compatibility review")


class ModerationAdapter(DiscordService, BasePlatformAdapter):
    def __init__(self, config):
        BasePlatformAdapter.__init__(self, config, Platform(PLATFORM))
        self.initialize_service()

    def load_startup(self):
        if not self.config.enabled or not explicit_activation():
            raise ValueError("Explicit moderation activation is required")
        verify_runtime()
        home = get_hermes_home().resolve()
        # Check both profiles, then check the effective native-platform config.
        default = read_user_config_raw(home.parent.parent / "config.yaml")
        if default.get("platforms", {}).get("discord", {}).get("enabled") is not False:
            raise ValueError("Default Discord must be explicitly disabled")
        stock = load_gateway_config().platforms.get(Platform.DISCORD)
        if stock is not None and stock.enabled:
            raise ValueError("Stock Discord must be disabled")
        path = home / "moderation.toml"
        if path.is_symlink():
            raise ValueError("Moderation configuration must be profile-local")
        self.policy = Config.from_file(path)
        database = home / "state/moderation.sqlite3"
        if Path(self.policy.storage.database_path) != database or self.policy.mode != "report_only":
            raise ValueError("Expected the profile-local report-only pilot configuration")
        if self.policy.logs_enabled or self.policy.log_channel_id or self.policy.operator_role_ids or len(self.policy.command_channel_ids) != 1:
            raise ValueError("Initial live pilot supports numeric operator users and one private report channel")
        token = get_scoped_secret("DISCORD_BOT_TOKEN", None)
        if not isinstance(token, str) or not token:
            raise ValueError("Profile bot token is missing")
        return self.policy, database, token

    def make_client(self):
        return PilotClient(self)

    def current_policy(self):
        path = get_hermes_home() / "moderation.toml"
        if path.is_symlink() or not explicit_activation():
            raise ValueError("Moderation profile is disabled")
        return Config.from_file(path)

    def jev_key(self):
        # No process environment or cross-profile fallback.
        secrets = current_secret_scope()
        return secrets.get("TYPESAFE_API_KEY") if secrets is not None else None

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        return SendResult(success=False, error="Generic Hermes delivery is disabled for the moderation platform")

    async def get_chat_info(self, chat_id):
        return {"name": "Liberdus moderation", "type": "channel"}

    async def handle_message(self, event):
        # Defense in depth: this platform cannot enter the general Hermes agent path.
        return None
