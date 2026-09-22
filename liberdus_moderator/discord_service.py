"""Shared Discord moderation service; no Hermes imports or conversational dispatch."""

import asyncio
import contextlib
import fcntl
import importlib.metadata
import os
from pathlib import Path

import discord

from .commands import CommandRequest
from .action_transport import ActionTransport, ACTION_BUTTONS, DeletionNotice, confirm_buttons
from . import actions
from .config import Config
from .engine import Engine
from .display import framed, panel
from .card_display import card_view
from .live import LiveSession, delivery_nonce, parse_command
from .models import MessageEvent
from .member_roles import checked_membership, membership_roles
from .storage import Store
from .health import HealthMonitor
from .review_display import ReviewMessage
from .slash_commands import SlashCommands

REVIEW_BUTTONS = {"liberdus:assess:v1:" + label: label for label in ("needs-attention", "looks-okay", "unsure")}
LEGACY_REVIEW_BUTTONS = {"liberdus:review:v1:" + label for label in ("promotion", "not-promotion", "unsure")}


def assessment_buttons(engine=None, *, content=None):
    assessment_items, action_items = [], []
    labels = {"needs-attention": "Needs attention", "looks-okay": "Looks okay", "unsure": "Unsure"}
    for identity, label in REVIEW_BUTTONS.items():
        assessment_items.append(discord.ui.Button(label=labels[label], style=discord.ButtonStyle.secondary, custom_id=identity, row=0))
    if engine is not None:
        for identity, name in ACTION_BUTTONS.items():
            label = {"delete": "Delete message(s)", "dismiss": "Dismiss", "timeout": "Timeout 10 min"}[name]
            disabled = (name == "delete" and not actions.enabled(engine, "deletion")
                        or name == "timeout" and not actions.enabled(engine, "timeout"))
            action_items.append(discord.ui.Button(label=label, style=discord.ButtonStyle.secondary if name == "dismiss" else discord.ButtonStyle.danger,
                                                 custom_id=identity, row=1, disabled=disabled))
    if isinstance(content, ReviewMessage):
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(discord.ui.Container(discord.ui.TextDisplay(content.overview), accent_colour=0x5865F2))
        view.add_item(discord.ui.Container(discord.ui.TextDisplay(content.staff_section), discord.ui.ActionRow(*assessment_items)))
        action_section = discord.ui.Container(discord.ui.TextDisplay(content.action_section))
        if action_items:
            action_section.add_item(discord.ui.ActionRow(*action_items))
        view.add_item(action_section)
        view.add_item(discord.ui.TextDisplay(content.reference))
    else:
        view = discord.ui.View(timeout=None)
        for item in (*assessment_items, *action_items):
            view.add_item(item)
    return view

def verify_discord_runtime():
    if importlib.metadata.version("discord.py") != "2.7.1":
        raise ValueError("Discord library requires compatibility review")


def snapshot(message, *, author_role_ids=None):
    """Copy SDK evidence immediately; never hand mutable SDK objects to the queue."""
    if author_role_ids is None:
        try:
            author_role_ids = membership_roles(message.author, message.guild.id)
        except (AttributeError, ValueError, TypeError):
            author_role_ids = ()  # Unknown, never evidence that the member has no roles.
    return MessageEvent(
        guild_id=str(message.guild.id), channel_id=str(message.channel.id), message_id=str(message.id),
        author_id=str(message.author.id), content=message.content, created_at=message.created_at.timestamp(),
        author_role_ids=author_role_ids,
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
        await self.adapter.ready(resumed=True)

    async def on_disconnect(self):
        self.adapter.lost_connection()

    async def on_message(self, message):
        self.adapter.receive(message)

    async def on_guild_channel_update(self, before, after):
        self.adapter.channel_changed(after)

    async def on_guild_channel_delete(self, channel):
        self.adapter.channel_changed(channel)

    async def on_guild_channel_create(self, channel):
        self.adapter.channel_changed(channel)

    async def on_interaction(self, interaction):
        if await self.adapter.receive_slash_command(interaction):
            return
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


class DiscordService(SlashCommands, ActionTransport):
    def initialize_service(self):
        self.client = None
        self.store = self.live = self.policy = None
        self.worker = self.receiver = None
        self.classifier = None
        self.health = self.health_task = None
        self.processing_evidence = False
        self.classifier_candidates = set()
        self.auto_delete_candidates = set()
        self.action_deletions = set()
        self.queue = asyncio.Queue(maxsize=200)
        self.ready_event = asyncio.Event()
        self.generation = 0
        self.monitored_scope_signature = None
        self.online = False
        self.closing = False
        self.lock_fd = None
        self.owns_token_lock = False
        self.next_command_at = 0.0
        self.stop_replies = {}
        self.stop_reply_pending = False
        self.next_stop_reply_at = 0.0
        self.deferred_interactions = {}
        self.interaction_receivers = set()
        self.notice_tasks = set()

    async def connect(self, *, is_reconnect=False):
        if self.client is not None:
            return self.online
        try:
            self.policy, database, token = self.load_startup()
            database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.lock_fd = os.open(database.parent / "moderation.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not self._acquire_platform_lock("discord-bot-token", token, "Discord bot token"):
                raise ValueError("Bot credential already owned by another gateway")
            self.owns_token_lock = True
            self.store = Store(str(database))
            self.live = LiveSession(Engine(self.policy, self.store))
            self.health = HealthMonitor(self.live.engine)
            self.coverage_gap("startup")
            self.closing = False
            self.client = self.make_client()
            await asyncio.wait_for(self.client.login(token), timeout=20)
            if str(self.client.user.id) != self.policy.bot_user_id:
                raise ValueError("Bot identity mismatch")
            await self.register_application_commands()
            if self.policy.classifier.mode == "shadow":
                from .classifier import ShadowClassifier
                self.classifier = ShadowClassifier(self.live.engine, self.evaluate_jev, active=self.classifier_active)
                self.classifier.start()
            elif self.policy.classifier.mode == "report_only":
                from .screening import MessageScreener
                self.classifier = MessageScreener(self.live.engine, self.evaluate_jev, active=self.classifier_active,
                                                 on_result=lambda job: self.enqueue("screen_result", job),
                                                 on_health=self.health_outcome)
                self.classifier.start()
            self.worker = asyncio.create_task(self.run_worker())
            self.receiver = asyncio.create_task(self.run_receiver())
            self.health_task = asyncio.create_task(self.run_health())
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
            self._set_fatal_error("liberdus_startup_failed", "Moderation startup failed; check the runtime, configuration, credentials, bot intents, and private-channel access.", retryable=False)
            await self.disconnect()
            return False

    def classifier_active(self):
        # A disk flag/policy change stops new requests and invalidates late results
        # even before a gateway restart. Never fetch another profile's key here.
        if (not self.online or self.closing or (self.policy.classifier.mode == "shadow"
                and (not self.queue.empty() or self.processing_evidence))):
            return False
        if self.guarded_scope():
            try:
                for identity in self.policy.command_channel_ids:
                    self.checked_channel(identity, sending=True)
            except (ValueError, AttributeError, TypeError):
                return False  # Do not spend on screening without a private report destination.
        return self.scope_current() and self.policy_current()

    def make_client(self):
        return PilotClient(self)

    async def register_application_commands(self):
        # The dedicated standalone runner owns its command menu. Hermes hosts
        # keep their existing application registration lifecycle.
        pass

    def policy_current(self):
        """Recheck disk policy and activation before privileged controls."""
        try:
            return (self.current_policy().policy_hash == self.policy.policy_hash
                    and self.scope_current())
        except (OSError, ValueError, TypeError):
            return False

    async def evaluate_jev(self, payload):
        if not self.classifier_active():
            raise ValueError("Moderation scope changed before JEV request")
        from .jev import evaluate
        return await evaluate(payload, self.jev_key(), self.policy.classifier.timeout_seconds)

    def process_evidence(self, evidence):
        if self.guarded_scope() and not self.in_scope(evidence.guild_id, evidence.channel_id):
            return {"disposition": "ignored", "reason": "outside_current_scope", "incident_ids": []}
        result = self.live.engine.process(evidence)
        if self.classifier is not None:
            if self.policy.classifier.mode == "report_only":
                if result["disposition"] in {"no_match", "review"} and evidence.content.strip():
                    if not self.live.engine.role_evidence_available(evidence.author_role_ids):
                        self.classifier_candidates.discard(evidence.message_id)
                        self.store.set_setting("screening_unchecked", self.store.get_setting("screening_unchecked", 0) + 1)
                        self.store.set_setting("screening_state", "membership_unavailable")
                        self.health_outcome("membership_unavailable")
                    elif self.live.engine.role_exempt(evidence.author_role_ids):
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

    def guarded_scope(self):
        return bool(self.policy and (self.policy.explicit_channel_scope or self.policy.allow_public_monitored_channels or self.policy.excluded_category_ids
                                    or self.policy.included_category_ids))

    def checked_channel(self, channel_id, *, sending=False):
        allowed = self.policy.command_channel_ids if sending else (*self.policy.monitored_channel_ids, *self.policy.command_channel_ids)
        if channel_id not in allowed:
            raise ValueError("Channel outside configured scope")
        channel = self.client.get_channel(int(channel_id))
        if (not isinstance(channel, discord.TextChannel) or str(channel.guild.id) != self.policy.guild_id
                or getattr(channel.guild, "unavailable", False) is True):
            raise ValueError("Configured text channel unavailable")
        if self.guarded_scope():
            # TextChannel also represents announcement channels. Public collection
            # is restricted to ordinary text channels with known current metadata.
            if channel.type != discord.ChannelType.text:
                raise ValueError("Only ordinary guild text channels are monitored")
            category_id = getattr(channel, "category_id", ...)
            if (channel_id in self.policy.monitored_channel_ids and self.policy.included_category_ids
                    and str(category_id) not in self.policy.included_category_ids):
                raise ValueError("Channel is outside the included categories")
            if category_id is not None:
                if type(category_id) is not int or category_id <= 0:
                    raise ValueError("Channel category metadata unavailable")
                # Category exclusions govern collection. The explicitly configured
                # private staff destination may live in an excluded category.
                if (channel_id not in self.policy.command_channel_ids
                        and str(category_id) in self.policy.excluded_category_ids):
                    raise ValueError("Channel belongs to an excluded category")
                category = getattr(channel, "category", None)
                if (not isinstance(category, discord.CategoryChannel)
                        or category.id != category_id or str(category.guild.id) != self.policy.guild_id):
                    raise ValueError("Channel category cache unavailable")
        me = channel.guild.me
        if me is None:
            raise ValueError("Bot membership unavailable")
        perms = channel.permissions_for(me)
        everyone = channel.permissions_for(channel.guild.default_role)
        public_allowed = self.policy.allow_public_monitored_channels and channel_id in self.policy.monitored_channel_ids
        if (perms.administrator or (everyone.view_channel and not public_allowed)
                or (public_allowed and not self.policy.explicit_channel_scope and not everyone.view_channel)
                or not perms.view_channel or not perms.read_message_history or (sending and not perms.send_messages)):
            raise ValueError("Configured channel permissions no longer match")
        return channel

    def current_scope_signature(self):
        # Bound to the explicit policy IDs: new guild channels never join scope.
        signature = []
        for identity in (*self.policy.monitored_channel_ids, *self.policy.command_channel_ids):
            try:
                channel = self.checked_channel(identity, sending=identity in self.policy.command_channel_ids)
                signature.append((identity, True, channel.category_id))
            except (ValueError, AttributeError, TypeError):
                signature.append((identity, False, None))
        return tuple(signature)

    def scope_current(self):
        # Pure after initial capture: safe during the classifier's SQLite transaction.
        # A changed cache stops outbound calls/results before the gateway callback runs.
        if not self.guarded_scope():
            return True
        signature = self.current_scope_signature()
        if self.monitored_scope_signature is None:
            self.monitored_scope_signature = signature
        return signature == self.monitored_scope_signature

    def refresh_scope(self):
        if not self.guarded_scope():
            return False
        signature = self.current_scope_signature()
        prior = self.monitored_scope_signature
        self.monitored_scope_signature = signature
        if prior is not None and signature != prior:
            self.coverage_gap("scope_changed")
            return True
        return False

    def channel_changed(self, channel):
        if (self.policy is None or self.closing or not self.guarded_scope()
                or str(getattr(getattr(channel, "guild", None), "id", "")) != self.policy.guild_id):
            return
        self.refresh_scope()

    async def ready(self, *, resumed=False):
        if self.closing:
            return
        try:
            if str(self.client.user.id) != self.policy.bot_user_id:
                raise ValueError("Bot identity mismatch")
            for channel in self.policy.monitored_channel_ids:
                try:
                    self.checked_channel(channel)
                except ValueError:
                    if not self.guarded_scope():
                        raise
                    # Moved, removed, inaccessible, or unknown monitors are excluded
                    # individually. The private report destination must still be valid.
            for channel in self.policy.command_channel_ids:
                self.checked_channel(channel, sending=True)
            self.refresh_scope()
            if self.online:
                return
            with contextlib.suppress(Exception):
                from .connection_health import record
                record(self.live.engine, 'resumed' if resumed else 'new_session')
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
            _, kind, value = self.queue.get_nowait()
            self.cancel_queued_interaction(kind, value)
            self.queue.task_done()
        # Restrictive controls already committed synchronously; only their
        # bounded, coalesced receipt remains independent of evidence generations.
        self.stop_reply_pending = False
        if self.stop_replies and not self.closing:
            self.queue.put_nowait((self.generation, "stop_reply", None))
            self.stop_reply_pending = True
        if self.live is not None:
            self.live.gap(reason)
        if self.health is not None:
            self.health.gap(reason)
        if self.classifier is not None and self.policy.classifier.mode == "report_only":
            self.classifier.invalidate()

    def lost_connection(self):
        if self.closing or not self.online:
            return
        with contextlib.suppress(Exception):
            from .connection_health import record
            ws = getattr(self.client, 'ws', None)
            code = getattr(ws, '_close_code', None) or getattr(getattr(ws, 'socket', None), 'close_code', None)
            record(self.live.engine, 'disconnected', code)
        self.online = False
        self.ready_event.clear()
        self.coverage_gap("disconnect")
        self._mark_disconnected()

    def in_scope(self, guild_id, channel_id):
        if not (self.online and not getattr(self, "configuration_reload_pending", False)
                and guild_id is not None and str(guild_id) == self.policy.guild_id
                and str(channel_id) in (*self.policy.monitored_channel_ids, *self.policy.command_channel_ids)):
            return False
        if self.guarded_scope():
            try:
                self.checked_channel(str(channel_id), sending=str(channel_id) in self.policy.command_channel_ids)
            except (ValueError, AttributeError, TypeError):
                return False
        return True

    @staticmethod
    def stop_control(event):
        return ((event.command == "pause" and not event.arguments)
                or (event.command in {"deletion", "auto-delete", "timeout"}
                    and event.arguments == ("off",)))

    def apply_stop_control(self, message_id, event, *, notify_channel=True):
        # No await or evidence queue: acknowledged stop state is durable before
        # the next action guard. Only four fixed restrictive operations qualify.
        if (self.live is None or not self.online or self.closing
                or event.guild_id != self.policy.guild_id
                or event.channel_id not in self.policy.command_channel_ids
                or event.user_id not in self.policy.operator_user_ids
                or not self.policy_current()):
            return
        try:
            self.checked_channel(event.channel_id, sending=True)
        except ValueError:
            return
        content = self.live.command(event, message_id, self.online)
        if content is None:
            return  # Durable receipt prevents duplicate stop delivery/replay.
        # An earlier enable/resume must not execute after this newer stop.
        queued = []
        while not self.queue.empty():
            item = self.queue.get_nowait()
            _, kind, value = item
            superseded = (kind in {"command", "slash_command"} and
                ((event.command == "pause" and value[1].command == "resume")
                 or (value[1].command == event.command and value[1].arguments == ("on",))))
            if superseded:
                # Terminal receipt: duplicate gateway delivery of this older
                # enable must not undo a newer stop after queue invalidation.
                with self.store.transaction():
                    receipt = str(value[0].id) if kind == "slash_command" else value[0]
                    self.store.db.execute("INSERT OR IGNORE INTO command_receipts VALUES(?,?)",
                                          (receipt, self.live.engine._now()))
                    self.store.db.execute("DELETE FROM command_receipts WHERE message_id IN ("
                        "SELECT message_id FROM command_receipts ORDER BY created_at DESC LIMIT -1 OFFSET 5000)")
                self.cancel_queued_interaction(kind, value)
            else:
                queued.append(item)
            self.queue.task_done()
        for item in queued:
            self.queue.put_nowait(item)
        if not notify_channel:
            return content
        self.stop_replies[event.command] = (message_id, event)
        if not self.stop_reply_pending:
            try:
                self.queue.put_nowait((self.generation, "stop_reply", None))
                self.stop_reply_pending = True
            except asyncio.QueueFull:
                self.coverage_gap("queue_full")

        return content

    def enqueue(self, kind, value):
        if kind == "command" and self.stop_control(value[1]):
            self.apply_stop_control(*value)
            return
        try:
            self.queue.put_nowait((self.generation, kind, value))
        except asyncio.QueueFull:
            self.cancel_queued_interaction(kind, value)
            self.coverage_gap("queue_full")

    async def defer_interaction(self, interaction, *, ephemeral=True):
        # Reserve before the acknowledgement await, bounding concurrent receives
        # as well as queued/in-flight cancellation notices to 200 interactions.
        if self.closing or not self.online:
            return False
        receiver = asyncio.current_task()
        self.interaction_receivers.difference_update(task for task in tuple(self.interaction_receivers) if task.done())
        if (len(self.deferred_interactions) >= 200
                or (receiver not in self.interaction_receivers and len(self.interaction_receivers) >= 200)):
            await self.interaction_notice(interaction, "Moderation is busy. Please try again shortly.")
            return False
        key = id(interaction)
        self.deferred_interactions[key] = (interaction, False)
        # The SDK callback can still be waiting for its defer acknowledgement at
        # shutdown. Keep its bounded admitted task alive through the closing
        # check and completion reply before closing the shared HTTP session.
        if receiver not in self.interaction_receivers:
            self.interaction_receivers.add(receiver)
            receiver.add_done_callback(self.interaction_receivers.discard)
        from .interaction_health import record
        record(self.live.engine, interaction, 'received')
        try:
            # Send immediately. Discord enforces the initial response deadline;
            # allow a slow receipt to arrive after Discord accepted the request.
            await asyncio.wait_for(interaction.response.defer(ephemeral=ephemeral, thinking=True), timeout=5)
        except asyncio.CancelledError:
            self.deferred_interactions.pop(key, None)
            raise
        except Exception as error:
            record(self.live.engine, interaction, 'ack_error', error)
            try:
                # Discord may already show a thinking placeholder. Only finish
                # that reply; an uncertain acknowledgement never queues work.
                await self.recover_interaction_reply(interaction,
                    'This click was cancelled before processing because Discord acknowledgement could not be confirmed. '
                    'Open !mod incident ID again and request a new confirmation.')
            finally:
                self.deferred_interactions.pop(key, None)
            return False
        self.deferred_interactions[key] = (interaction, True)
        record(self.live.engine, interaction, 'acknowledged')
        return True

    def cancel_queued_interaction(self, kind, value):
        if kind not in {"review_click", "action_confirm", "slash_command", "slash_config"}:
            return
        interaction = value[0]
        record = self.deferred_interactions.get(id(interaction))
        if record is None or not record[1]:
            return
        # Remove from this cancellation path immediately; the single task owns
        # completion and never executes/replays the underlying assessment/action.
        self.deferred_interactions[id(interaction)] = (interaction, False)
        task = asyncio.create_task(self.interaction_notice(interaction,
            "Evidence or connection changed. This click was cancelled. "
            "Open !mod incident ID again. No action was replayed.", deferred=True))
        self.notice_tasks.add(task)
        task.add_done_callback(self.notice_tasks.discard)

    def receive(self, message):
        if self.guarded_scope() and str(getattr(message.guild, "id", "")) == self.policy.guild_id:
            self.refresh_scope()
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
        if self.guarded_scope() and str(guild_id) == self.policy.guild_id:
            self.refresh_scope()
        if self.in_scope(guild_id, channel_id) and str(channel_id) in self.policy.monitored_channel_ids:
            self.enqueue("edit", (str(channel_id), int(message_id)))

    def deleted(self, guild_id, channel_id, message_ids):
        if self.guarded_scope() and str(guild_id) == self.policy.guild_id:
            self.refresh_scope()
        if all(str(identity) in self.action_deletions for identity in message_ids):
            return  # The bounded action batch invalidates coverage on completion.
        if self.in_scope(guild_id, channel_id) and str(channel_id) in self.policy.monitored_channel_ids:
            # A deleted message may still be queued and absent from SQLite. Reset
            # conservatively so queued evidence cannot resurrect it.
            self.coverage_gap("deleted_message")

    async def recover_interaction_reply(self, interaction, content):
        from .interaction_health import record
        view = card_view(content)
        try:
            # Type-5 deferrals have their own ephemeral response. Editing it
            # cannot replay an action or modify the staff's source review card.
            await asyncio.wait_for(interaction.edit_original_response(content=None, embeds=[], attachments=[],
                allowed_mentions=discord.AllowedMentions.none(), view=view), timeout=5)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            record(self.live.engine, interaction, 'recovery_error', error)
        else:
            record(self.live.engine, interaction, 'recovered')
        finally:
            view.stop()

    async def interaction_notice(self, interaction, content, *, deferred=False):
        from .interaction_health import record
        proposal = getattr(content, "proposal", None)
        target = getattr(content, "review_target", None)
        view = (confirm_buttons(proposal, content) if proposal else
                assessment_buttons(self.live.engine, content=content) if target else card_view(content))
        interrupted = False
        try:
            if deferred:
                # Edit the type-5 ephemeral placeholder directly. V2 conversion
                # requires clearing legacy fields; its original privacy remains.
                sent = await asyncio.wait_for(interaction.edit_original_response(content=None, embeds=[], attachments=[],
                    allowed_mentions=discord.AllowedMentions.none(), view=view), timeout=10)
            else:
                await asyncio.wait_for(interaction.response.send_message(None, ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(), view=view), timeout=10)
                sent = None
            if proposal and sent is not None:
                actions.bind(self.live.engine, proposal, str(sent.id))
            if target and sent is not None:
                self.live.save_review_prompt(str(sent.id), str(interaction.channel_id), target)
            record(self.live.engine, interaction, 'replied')
        except asyncio.CancelledError:
            interrupted = True
            raise
        except Exception as error:
            record(self.live.engine, interaction, 'reply_error', error)
            if deferred:
                # Never re-deliver or bind an uncertain confirmation preview.
                # For a finished action preserve the exact result, including
                # uncertain/partial outcomes, without repeating the mutation.
                recovery = ('Confirmation delivery could not be verified. No action was started. '
                            'Open !mod incident ID again and request a new confirmation.'
                            if proposal else content)
                try:
                    await self.recover_interaction_reply(interaction, recovery)
                except asyncio.CancelledError:
                    interrupted = True
                    raise
        finally:
            if deferred and not interrupted:
                self.deferred_interactions.pop(id(interaction), None)
            if view is not None:
                view.stop()

    async def receive_review_click(self, interaction):
        try:
            return await self._receive_review_click(interaction)
        finally:
            self.interaction_receivers.discard(asyncio.current_task())

    async def _receive_review_click(self, interaction):
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
        if not await self.defer_interaction(interaction):
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
        view = (confirm_buttons(proposal, content) if proposal else
                assessment_buttons(self.live.engine, content=content) if reviewable else card_view(content))
        if isinstance(content, DeletionNotice):
            view = card_view(content, accent_colour=0xF0B232 if content.partial else 0x2ECC71)
        body = None if isinstance(view, discord.ui.LayoutView) else framed(content)
        try:
            sent = await asyncio.wait_for(channel.send(body, allowed_mentions=discord.AllowedMentions.none(),
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
                view = assessment_buttons(self.live.engine, content=updated)
                # Converting a legacy report to components v2 must clear its old body.
                await asyncio.wait_for(message.edit(content=None, embeds=[], attachments=[], view=view, suppress=True,
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

    def health_outcome(self, outcome):
        # Advisory bookkeeping cannot alter the classifier result or action decision.
        if self.health is not None:
            with contextlib.suppress(Exception):
                if outcome == "ok":
                    self.health.record_success()
                elif outcome == "restart_lost":
                    self.health.gap("restart_lost")
                else:
                    self.health.record_failure(outcome)

    async def flush_health(self):
        if self.health is None or self.closing:
            return
        if self.online:
            # Recheck current activation/policy and the private destination before
            # reserving a delivery. A failed permission check makes no send attempt.
            if not self.policy_current():
                return
            try:
                self.checked_channel(self.policy.command_channel_ids[0], sending=True)
            except ValueError:
                return
        notice = self.health.claim(self.online)
        if notice is None:
            return
        try:
            await self.emit(notice["channel_id"], notice["content"], delivery_nonce("health:" + notice["token"]))
            self.health.finish(notice["token"], True)
        except asyncio.CancelledError:
            self.health.finish(notice["token"], False)
            raise
        except Exception:
            self.health.finish(notice["token"], False)

    async def run_health(self):
        # Independent of message traffic: budgets can be consumed by evaluation,
        # and outage notices must not require a new monitored message to deliver.
        try:
            while not self.closing:
                await self.flush_health()
                await asyncio.sleep(10)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.fail("health_monitor_failure")

    async def flush_reports(self):
        while self.online:
            self.refresh_scope()
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
                    self.refresh_scope()
                    if kind == "stop_reply":
                        replies, self.stop_replies = self.stop_replies, {}
                        self.stop_reply_pending = False
                        now = asyncio.get_running_loop().time()
                        if replies and self.online and not self.closing and now >= self.next_stop_reply_at:
                            self.next_stop_reply_at = now + 1
                            message_id, event = next(reversed(replies.values()))
                            labels = {"pause": "Moderation paused", "deletion": "Deletion OFF",
                                      "auto-delete": "Auto-delete OFF", "timeout": "Timeout OFF"}
                            content = panel("Safety controls applied", [*(labels[name] for name in replies),
                                "Saved across restarts.", "Use !mod status for current settings."])
                            try:
                                await self.emit(event.channel_id, content, delivery_nonce("stop:" + message_id))
                            except Exception:
                                pass  # Reply is best effort; durable stop has already applied.
                        if self.queue.empty() and self.online and not self.closing:
                            if self.classifier is not None:
                                self.classifier.submit(sorted(self.classifier_candidates))
                                self.classifier_candidates.clear()
                            await self.flush_reports()
                            await self.flush_automatic_actions()
                        continue
                    if generation != self.generation or not self.online:
                        self.cancel_queued_interaction(kind, value)
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
                    elif kind in {"slash_command", "slash_config"}:
                        await self.handle_slash_command(value, configuration=kind == "slash_config")
                    elif kind == "edit":
                        self.processing_evidence = True
                        try:
                            channel = self.checked_channel(value[0])
                            message = await asyncio.wait_for(channel.fetch_message(value[1]), timeout=8)
                            # Even a REST Member can come from cache. Only the explicit
                            # membership fetch below establishes roles for this edit.
                            evidence = snapshot(message, author_role_ids=())
                            if (evidence.guild_id != self.policy.guild_id or evidence.channel_id != value[0]
                                    or evidence.message_id != str(value[1])):
                                raise ValueError("Fetched edit identity mismatch")
                            # REST authors can be Users or cached Members. Neither establishes
                            # current membership for the exemption; resolve it while ON.
                            if (self.policy.classifier.exempt_role_ids
                                    and self.store.get_setting("role_exemption_enabled", True) is True
                                    and not evidence.is_bot and not evidence.is_webhook):
                                roles = ()
                                try:
                                    member = await asyncio.wait_for(message.guild.fetch_member(int(evidence.author_id)), timeout=5)
                                    roles = checked_membership(member, self.policy.guild_id, evidence.author_id)
                                except (discord.HTTPException, ValueError, TypeError, AttributeError, TimeoutError):
                                    pass  # Keep code-rule evidence; unknown membership cannot enter JEV.
                                evidence = snapshot(message, author_role_ids=roles)
                            self.refresh_scope()
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
                            content = await self.prepare_manual_delete(content)
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
                            content = await self.prepare_manual_delete(content)
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
        while not self.queue.empty():
            _, kind, value = self.queue.get_nowait()
            self.cancel_queued_interaction(kind, value)
            self.queue.task_done()
        self.stop_replies.clear()
        self.stop_reply_pending = False
        current = asyncio.current_task()
        for task in (self.health_task, self.worker, self.receiver):
            if task is not None and task is not current:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self.worker = self.receiver = self.health_task = None
        receiving = {task for task in self.interaction_receivers if task is not current and not task.done()}
        if receiving:
            # Deferral is capped at 5s, completion at 10s, recovery at 5s. No new deferred
            # admissions are allowed while closing; this wait is bounded.
            _, pending = await asyncio.wait(receiving, timeout=20.5)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        self.interaction_receivers.clear()
        # A worker cancelled mid-action can have an uncertain result. Finish its
        # acknowledgement without claiming failure or attempting the action again.
        for interaction, acknowledged in list(self.deferred_interactions.values()):
            if acknowledged:
                task = asyncio.create_task(self.interaction_notice(interaction,
                    "Processing interrupted. Check !mod incident ID and !mod actions ID "
                    "before retrying. No action was replayed.", deferred=True))
                self.notice_tasks.add(task)
                task.add_done_callback(self.notice_tasks.discard)
        if self.notice_tasks:
            await asyncio.gather(*list(self.notice_tasks), return_exceptions=True)
        self.deferred_interactions.clear()
        # Webhook completion needs the SDK HTTP session. Close it only after
        # best-effort notices, while closing/offline already block new actions.
        if self.client is not None:
            with contextlib.suppress(Exception):
                await self.client.close()
        self.client = None
        if self.classifier is not None:
            await self.classifier.close()
            self.classifier = None
        if self.store is not None:
            self.store.close()
            self.store = self.live = None
        self.health = None
        if self.owns_token_lock:
            self._release_platform_lock()
            self.owns_token_lock = False
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None
        self._mark_disconnected()
