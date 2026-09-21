"""Version-pinned, scoped Hermes moderation adapter. No conversational dispatch."""

import asyncio
import contextlib
import fcntl
import importlib.metadata
import inspect
import os
from pathlib import Path
import subprocess

import discord
from agent.secret_scope import current_secret_scope
from gateway.config import Platform, load_gateway_config
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.platforms._shared import get_scoped_secret
from hermes_cli.config import read_user_config_raw
from hermes_constants import get_hermes_home

from .commands import CommandRequest
from .action_transport import ActionTransport, ACTION_BUTTONS, confirm_buttons
from . import actions
from .config import Config
from .engine import Engine
from .display import framed
from .hermes_plugin import PLATFORM, explicit_activation
from .live import LiveSession, delivery_nonce, parse_command
from .models import MessageEvent
from .storage import Store

REVIEW_BUTTONS = {"liberdus:assess:v1:" + label: label for label in ("needs-attention", "looks-okay", "unsure")}
LEGACY_REVIEW_BUTTONS = {"liberdus:review:v1:" + label for label in ("promotion", "not-promotion", "unsure")}


def assessment_buttons(engine=None):
    view = discord.ui.View(timeout=None)
    labels = {"needs-attention": "Needs attention", "looks-okay": "Looks okay", "unsure": "Unsure"}
    for identity, label in REVIEW_BUTTONS.items():
        view.add_item(discord.ui.Button(label=labels[label], style=discord.ButtonStyle.secondary, custom_id=identity))
    if engine is not None:
        for identity, name in ACTION_BUTTONS.items():
            label = {"delete": "Delete message(s)", "dismiss": "Dismiss", "timeout": "Timeout 10 min"}[name]
            disabled = (name == "delete" and not actions.enabled(engine, "deletion")
                        or name == "timeout" and not actions.enabled(engine, "timeout"))
            view.add_item(discord.ui.Button(label=label, style=discord.ButtonStyle.secondary if name == "dismiss" else discord.ButtonStyle.danger,
                                           custom_id=identity, row=1, disabled=disabled))
    return view

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


def snapshot(message):
    """Copy SDK evidence immediately; never hand mutable SDK objects to the queue."""
    return MessageEvent(
        guild_id=str(message.guild.id), channel_id=str(message.channel.id), message_id=str(message.id),
        author_id=str(message.author.id), content=message.content, created_at=message.created_at.timestamp(),
        author_role_ids=tuple(str(role.id) for role in getattr(message.author, "roles", ())),
        edited_at=message.edited_at.timestamp() if message.edited_at else None,
        is_bot=message.author.bot, is_webhook=message.webhook_id is not None,
        is_thread=isinstance(message.channel, discord.Thread), has_attachments=bool(message.attachments),
    )


class PilotClient(discord.Client):
    def __init__(self, adapter):
        intents = discord.Intents.none()
        intents.guilds = intents.guild_messages = intents.message_content = True
        super().__init__(intents=intents, max_messages=None, chunk_guilds_at_startup=False,
                         allowed_mentions=discord.AllowedMentions.none())
        self.adapter = adapter

    async def on_ready(self):
        await self.adapter.ready()

    async def on_resumed(self):
        await self.adapter.ready()

    async def on_disconnect(self):
        self.adapter.lost_connection()

    async def on_message(self, message):
        self.adapter.receive(message)

    async def on_interaction(self, interaction):
        await self.adapter.receive_review_click(interaction)

    async def on_raw_message_edit(self, payload):
        if "content" in payload.data or "attachments" in payload.data:
            self.adapter.edit(payload.guild_id, payload.channel_id, payload.message_id)

    async def on_raw_message_delete(self, payload):
        self.adapter.deleted(payload.guild_id, payload.channel_id, (payload.message_id,))

    async def on_raw_bulk_message_delete(self, payload):
        self.adapter.deleted(payload.guild_id, payload.channel_id, payload.message_ids)

    async def on_error(self, event_method, *args, **kwargs):
        # Override discord.py's default traceback printing for message callbacks.
        await self.adapter.fail("callback_failure")


class ModerationAdapter(ActionTransport, BasePlatformAdapter):
    def __init__(self, config):
        super().__init__(config, Platform(PLATFORM))
        self.client = None
        self.store = self.live = self.policy = None
        self.worker = self.receiver = None
        self.classifier = None
        self.processing_evidence = False
        self.classifier_candidates = set()
        self.auto_delete_candidates = set()
        self.action_deletions = set()
        self.queue = asyncio.Queue(maxsize=200)
        self.ready_event = asyncio.Event()
        self.generation = 0
        self.online = False
        self.closing = False
        self.lock_fd = None
        self.owns_token_lock = False
        self.next_command_at = 0.0

    async def connect(self, *, is_reconnect=False):
        if self.client is not None:
            return self.online
        try:
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
            database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.lock_fd = os.open(database.parent / "moderation.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not self._acquire_platform_lock("discord-bot-token", token, "Discord bot token"):
                raise ValueError("Bot credential already owned by another gateway")
            self.owns_token_lock = True
            self.store = Store(str(database))
            self.live = LiveSession(Engine(self.policy, self.store))
            self.coverage_gap("startup")
            self.closing = False
            self.client = PilotClient(self)
            await asyncio.wait_for(self.client.login(token), timeout=20)
            if str(self.client.user.id) != self.policy.bot_user_id:
                raise ValueError("Bot identity mismatch")
            if self.policy.classifier.mode == "shadow":
                from .classifier import ShadowClassifier
                self.classifier = ShadowClassifier(self.live.engine, self.evaluate_jev, active=self.classifier_active)
                self.classifier.start()
            elif self.policy.classifier.mode == "report_only":
                from .screening import MessageScreener
                self.classifier = MessageScreener(self.live.engine, self.evaluate_jev, active=self.classifier_active,
                                                 on_result=lambda job: self.enqueue("screen_result", job))
                self.classifier.start()
            self.worker = asyncio.create_task(self.run_worker())
            self.receiver = asyncio.create_task(self.run_receiver())
            waiter = asyncio.create_task(self.ready_event.wait())
            try:
                await asyncio.wait((waiter, self.receiver), timeout=40, return_when=asyncio.FIRST_COMPLETED)
            finally:
                waiter.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await waiter
            if not self.online:
                raise ValueError("Discord did not establish verified pilot readiness")
            return True
        except asyncio.CancelledError:
            await self.disconnect()
            raise
        except Exception:
            self._set_fatal_error("liberdus_startup_failed", "Moderation startup failed; check the pinned runtime, profile activation, bot intents, and private-channel access.", retryable=False)
            await self.disconnect()
            return False

    def classifier_active(self):
        # A disk flag/policy change stops new requests and invalidates late results
        # even before a gateway restart. Never fetch another profile's key here.
        if (not self.online or self.closing or (self.policy.classifier.mode == "shadow"
                and (not self.queue.empty() or self.processing_evidence))):
            return False
        try:
            path = get_hermes_home() / "moderation.toml"
            return (not path.is_symlink() and explicit_activation()
                    and Config.from_file(path).policy_hash == self.policy.policy_hash)
        except (OSError, ValueError, TypeError):
            return False

    async def evaluate_jev(self, payload):
        from .jev import evaluate
        # The generic helper permits process-env fallback outside multiplexing.
        # JEV must always use only this adapter task's installed profile scope.
        secrets = current_secret_scope()
        key = secrets.get("TYPESAFE_API_KEY") if secrets is not None else None
        return await evaluate(payload, key, self.policy.classifier.timeout_seconds)

    def process_evidence(self, evidence):
        result = self.live.engine.process(evidence)
        if self.classifier is not None:
            if self.policy.classifier.mode == "report_only":
                if result["disposition"] in {"no_match", "review"} and evidence.content.strip():
                    if self.live.engine.role_exempt(evidence.author_role_ids):
                        self.store.set_setting("screening_exempt", self.store.get_setting("screening_exempt", 0) + 1)
                    else:
                        self.classifier_candidates.add(evidence.message_id)
            else:
                self.classifier_candidates.update(result["incident_ids"])
        return result

    async def run_receiver(self):
        try:
            await self.client.connect(reconnect=True)
            if not self.closing:
                await self.fail("discord_connection_ended")
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self.closing:
                await self.fail("discord_connection_failed")

    def checked_channel(self, channel_id, *, sending=False):
        allowed = self.policy.command_channel_ids if sending else (*self.policy.monitored_channel_ids, *self.policy.command_channel_ids)
        if channel_id not in allowed:
            raise ValueError("Channel outside pilot scope")
        channel = self.client.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel) or str(channel.guild.id) != self.policy.guild_id:
            raise ValueError("Pilot text channel unavailable")
        me = channel.guild.me
        if me is None:
            raise ValueError("Bot membership unavailable")
        perms = channel.permissions_for(me)
        everyone = channel.permissions_for(channel.guild.default_role)
        if (perms.administrator or everyone.view_channel or not perms.view_channel or not perms.read_message_history
                or (sending and not perms.send_messages)):
            raise ValueError("Pilot permissions no longer match")
        return channel

    async def ready(self):
        if self.closing:
            return
        try:
            if str(self.client.user.id) != self.policy.bot_user_id:
                raise ValueError("Bot identity mismatch")
            for channel in self.policy.monitored_channel_ids:
                self.checked_channel(channel)
            for channel in self.policy.command_channel_ids:
                self.checked_channel(channel, sending=True)
            if self.online:
                return
            self.coverage_gap("reconnect")
            self.online = True
            self._mark_connected()
            self.ready_event.set()
        except Exception:
            await self.fail("pilot_readiness_failed")

    def coverage_gap(self, reason):
        self.generation += 1
        self.classifier_candidates.clear()
        self.auto_delete_candidates.clear()
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()
        if self.live is not None:
            self.live.gap(reason)
        if self.classifier is not None and self.policy.classifier.mode == "report_only":
            self.classifier.invalidate()

    def lost_connection(self):
        if self.closing or not self.online:
            return
        self.online = False
        self.ready_event.clear()
        self.coverage_gap("disconnect")
        self._mark_disconnected()

    def in_scope(self, guild_id, channel_id):
        return (self.online and guild_id is not None and str(guild_id) == self.policy.guild_id
                and str(channel_id) in (*self.policy.monitored_channel_ids, *self.policy.command_channel_ids))

    def enqueue(self, kind, value):
        try:
            self.queue.put_nowait((self.generation, kind, value))
        except asyncio.QueueFull:
            self.coverage_gap("queue_full")

    def receive(self, message):
        if (not self.in_scope(getattr(message.guild, "id", None), message.channel.id)
                or message.author.bot or message.webhook_id is not None):
            return
        try:
            channel = str(message.channel.id)
            if channel in self.policy.command_channel_ids:
                if str(message.author.id) not in self.policy.operator_user_ids:
                    return
                reference = getattr(message, "reference", None)
                reply_id = None
                if reference is not None:
                    if (getattr(reference, "type", discord.MessageReferenceType.default) != discord.MessageReferenceType.default
                            or getattr(reference, "guild_id", None) not in (None, message.guild.id)
                            or getattr(reference, "channel_id", None) != message.channel.id):
                        return
                    identity = getattr(reference, "message_id", None)
                    reply_id = str(identity) if identity is not None else None
                command = parse_command(message.content, str(message.guild.id), channel, str(message.author.id),
                                        reply_to_message_id=reply_id)
                if command:
                    self.enqueue("command", (str(message.id), command))
            else:
                self.enqueue("message", snapshot(message))
        except (ValueError, TypeError, AttributeError):
            self.coverage_gap("invalid_event")

    def edit(self, guild_id, channel_id, message_id):
        if self.in_scope(guild_id, channel_id) and str(channel_id) in self.policy.monitored_channel_ids:
            self.enqueue("edit", (str(channel_id), int(message_id)))

    def deleted(self, guild_id, channel_id, message_ids):
        if all(str(identity) in self.action_deletions for identity in message_ids):
            return  # The bounded action batch invalidates coverage on completion.
        if self.in_scope(guild_id, channel_id) and str(channel_id) in self.policy.monitored_channel_ids:
            # A deleted message may still be queued and absent from SQLite. Reset
            # conservatively so queued evidence cannot resurrect it.
            self.coverage_gap("deleted_message")

    async def interaction_notice(self, interaction, content, *, deferred=False):
        proposal = getattr(content, "proposal", None)
        view = confirm_buttons(proposal) if proposal else None
        try:
            if deferred:
                sent = await asyncio.wait_for(interaction.followup.send(framed(content), ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(), view=view, wait=True), timeout=10)
            else:
                await asyncio.wait_for(interaction.response.send_message(framed(content), ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(), view=view), timeout=10)
                sent = None
            if proposal and sent is not None:
                actions.bind(self.live.engine, proposal, str(sent.id))
        except Exception:
            pass  # No mutation/replay after an uncertain confirmation delivery.
        finally:
            if view is not None:
                view.stop()

    async def receive_review_click(self, interaction):
        if await self.receive_action_confirmation(interaction):
            return
        data = interaction.data
        if not isinstance(data, dict) or not isinstance(data.get("custom_id"), str):
            return
        if (interaction.type != discord.InteractionType.component or data.get("component_type") != 2
                or data.get("custom_id") not in set(REVIEW_BUTTONS) | LEGACY_REVIEW_BUTTONS | set(ACTION_BUTTONS)):
            return
        message = interaction.message
        if (not self.in_scope(interaction.guild_id, interaction.channel_id)
                or str(interaction.channel_id) not in self.policy.command_channel_ids
                or interaction.user.bot or str(interaction.user.id) not in self.policy.operator_user_ids
                or message is None or str(message.author.id) != self.policy.bot_user_id
                or message.channel.id != interaction.channel_id):
            await self.interaction_notice(interaction, "Review unavailable here or for this account.")
            return
        if data["custom_id"] in LEGACY_REVIEW_BUTTONS:
            await self.interaction_notice(interaction, "These old buttons record content labels. Use !mod incident ID for the new staff assessment buttons.")
            return
        event = CommandRequest(str(interaction.guild_id), str(interaction.channel_id), str(interaction.user.id),
                               ACTION_BUTTONS.get(data["custom_id"], "assess"),
                               arguments=() if data["custom_id"] in ACTION_BUTTONS else (REVIEW_BUTTONS[data["custom_id"]],),
                               reply_to_message_id=str(message.id))
        if self.live.review_reply_target(event) is None:
            await self.interaction_notice(interaction, "Saved report link unavailable. Request a new !mod incident ID view.")
            return
        if self.queue.full():
            await self.interaction_notice(interaction, "Moderation is busy. Try the review button again shortly.")
            return
        generation = self.generation
        try:
            await asyncio.wait_for(interaction.response.defer(ephemeral=True, thinking=True), timeout=2)
        except Exception:
            return  # No mutation without a confirmed acknowledgement.
        if generation != self.generation or not self.online or self.closing:
            await self.interaction_notice(interaction, "Connection changed. No review saved; try again.", deferred=True)
            return
        try:
            self.queue.put_nowait((generation, "review_click", (interaction, event, asyncio.get_running_loop().time() + 30)))
        except asyncio.QueueFull:
            await self.interaction_notice(interaction, "Moderation is busy. No review saved; try again shortly.", deferred=True)

    async def emit(self, channel_id, content, nonce, *, reviewable=False):
        if not self.online or self.closing:
            raise ValueError("Moderation transport is offline")
        channel = self.checked_channel(channel_id, sending=True)
        proposal = getattr(content, "proposal", None)
        view = confirm_buttons(proposal) if proposal else assessment_buttons(self.live.engine) if reviewable else None
        try:
            sent = await asyncio.wait_for(channel.send(framed(content), allowed_mentions=discord.AllowedMentions.none(),
                nonce=nonce, suppress_embeds=True, silent=True, view=view), timeout=20)
            if proposal:
                actions.bind(self.live.engine, proposal, str(sent.id))
            return sent
        finally:
            if view is not None:
                # Interactions use PilotClient.on_interaction plus durable message bindings.
                # No per-message callbacks need to survive in memory or be re-registered after restart.
                view.stop()

    async def refresh_assessment_messages(self, content, event):
        assessment = getattr(content, "assessment", None)
        if assessment is None:
            return content
        generation = self.generation
        succeeded, failed = 0, 0
        try:
            targets = self.live.assessment_messages(event, assessment["incident_id"])
        except Exception:
            return str(content) + "\nReport display unavailable. Use !mod incident ID to see the saved assessment."
        for message_id, identity, revision in targets:
            view = None
            try:
                if generation != self.generation or not self.online or self.closing:
                    raise ValueError("Connection changed")
                channel = self.checked_channel(event.channel_id, sending=True)
                message = await asyncio.wait_for(channel.fetch_message(int(message_id)), timeout=5)
                if (str(message.id) != message_id or str(message.author.id) != self.policy.bot_user_id
                        or str(message.channel.id) != event.channel_id or str(message.guild.id) != self.policy.guild_id
                        or generation != self.generation or not self.online or self.closing):
                    raise ValueError("Saved bot report unavailable")
                self.checked_channel(event.channel_id, sending=True)
                updated = self.live.render_snapshot(identity, revision)
                view = assessment_buttons(self.live.engine)
                await asyncio.wait_for(message.edit(content=framed(updated), view=view, suppress=True,
                    allowed_mentions=discord.AllowedMentions.none()), timeout=5)
                succeeded += 1
            except Exception:
                failed += 1  # Stored assessment remains valid; no send fallback or mutation retry.
            finally:
                if view is not None:
                    view.stop()
        if failed:
            return str(content) + "\nSome report displays could not be updated. Use !mod incident ID to see the saved assessment."
        if succeeded:
            return str(content) + "\nReport display updated."
        return str(content) + "\nNo retained report to update. Use !mod incident ID to see the saved assessment."

    async def flush_reports(self):
        while self.online:
            report = self.live.claim_report()
            if report is None:
                return
            try:
                content = (self.live.render_snapshot(report["incident_id"], report["incident_revision"])
                           if report["kind"] == "moderator" else report["payload"]["content"])
                sent = await self.emit(report["payload"]["channel_id"], content,
                                       delivery_nonce("report:" + report["id"]), reviewable=report["kind"] == "moderator")
                self.live.finish_report(report["id"], str(sent.id))
            except asyncio.CancelledError:
                self.live.finish_report(report["id"])
                raise
            except Exception:
                # No application retry: the server may have accepted the request.
                self.live.finish_report(report["id"])

    async def run_worker(self):
        try:
            while True:
                generation, kind, value = await self.queue.get()
                try:
                    if generation != self.generation or not self.online:
                        continue
                    if kind == "message":
                        self.process_evidence(value)
                    elif kind == "screen_result":
                        if self.classifier is not None and self.policy.classifier.mode == "report_only":
                            identity = self.classifier.apply(value)
                            if identity and actions.enabled(self.live.engine, "auto_delete"):
                                self.auto_delete_candidates.add(identity)
                    elif kind == "action_confirm":
                        await self.handle_action_confirmation(value)
                    elif kind == "edit":
                        self.processing_evidence = True
                        try:
                            channel = self.checked_channel(value[0])
                            message = await asyncio.wait_for(channel.fetch_message(value[1]), timeout=8)
                            evidence = snapshot(message)
                            if evidence.channel_id != value[0] or evidence.message_id != str(value[1]):
                                raise ValueError("Fetched edit identity mismatch")
                            if generation == self.generation and self.online:
                                result = self.process_evidence(evidence)
                                if result["reason"] == "conflicting_event_version":
                                    self.coverage_gap("unavailable_edit")
                        except (discord.HTTPException, ValueError, TypeError, AttributeError, TimeoutError):
                            self.coverage_gap("unavailable_edit")
                        finally:
                            self.processing_evidence = False
                        # One bounded fetch at a time, at most four per second.
                        await asyncio.sleep(0.25)
                    elif kind == "command":
                        now = asyncio.get_running_loop().time()
                        if now < self.next_command_at:
                            continue
                        self.next_command_at = now + 1
                        content = self.live.command(value[1], value[0], self.online)
                        if content:
                            content = await self.refresh_assessment_messages(content, value[1])
                            try:
                                target = getattr(content, "review_target", None)
                                sent = await self.emit(value[1].channel_id, content, delivery_nonce("command:" + value[0]),
                                                       reviewable=target is not None)
                                if target is not None:
                                    self.live.save_review_prompt(str(sent.id), value[1].channel_id, target)
                            except Exception:
                                pass  # Never replay a command after an uncertain response.
                    elif kind == "review_click":
                        interaction, event, deadline = value
                        now = asyncio.get_running_loop().time()
                        if now > deadline or now < self.next_command_at:
                            await self.interaction_notice(interaction, "Review not saved. Please try the button again.", deferred=True)
                            continue
                        # Recheck private-channel access and policy at execution, after queued events.
                        try:
                            self.checked_channel(event.channel_id, sending=True)
                        except ValueError:
                            await self.interaction_notice(interaction, "Private review channel unavailable. No review saved.", deferred=True)
                            continue
                        self.next_command_at = now + 1
                        content = self.live.command(event, "interaction:" + str(interaction.id), self.online)
                        if content:
                            content = await self.refresh_assessment_messages(content, event)
                        await self.interaction_notice(interaction, content or "This interaction was already processed or is no longer authorized.",
                                                      deferred=True)
                    # Drain already-arrived edits/messages before handing a report to the network.
                    if self.queue.empty() and generation == self.generation:
                        if self.classifier is not None:
                            self.classifier.submit(sorted(self.classifier_candidates))
                            self.classifier_candidates.clear()
                        await self.flush_reports()
                        await self.flush_automatic_actions()
                finally:
                    self.queue.task_done()
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.fail("worker_failure")

    async def fail(self, code):
        self.online = False
        self.ready_event.clear()
        with contextlib.suppress(Exception):
            self.coverage_gap("worker_failure")
        self._set_fatal_error(code, "Liberdus moderation stopped; no conversational fallback. Inspect setup before restarting.", retryable=False)
        await self.disconnect()

    async def disconnect(self):
        self.closing = True
        self.online = False
        self.ready_event.clear()
        if self.client is not None:
            with contextlib.suppress(Exception):
                await self.client.close()
        current = asyncio.current_task()
        for task in (self.worker, self.receiver):
            if task is not None and task is not current:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self.worker = self.receiver = self.client = None
        if self.classifier is not None:
            await self.classifier.close()
            self.classifier = None
        if self.store is not None:
            self.store.close()
            self.store = self.live = None
        if self.owns_token_lock:
            self._release_platform_lock()
            self.owns_token_lock = False
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None
        self._mark_disconnected()

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        return SendResult(success=False, error="Generic Hermes delivery is disabled for the moderation platform")

    async def get_chat_info(self, chat_id):
        return {"name": "Liberdus moderation", "type": "channel"}

    async def handle_message(self, event):
        # Defense in depth: this platform cannot enter the general Hermes agent path.
        return None
