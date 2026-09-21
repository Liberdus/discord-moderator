"""Discord actions reachable only through reviewed proposals or the narrow auto rule."""
import asyncio
from datetime import datetime, timedelta, timezone
import re

import discord

from . import actions
from .commands import CommandRequest
from .display import panel
from .live import AssessmentText, delivery_nonce

ACTION_BUTTONS = {'liberdus:action:v1:delete': 'delete', 'liberdus:action:v1:dismiss': 'dismiss',
                  'liberdus:action:v1:timeout': 'timeout'}
CONFIRM = 'liberdus:confirm:v1:'
CANCEL = 'liberdus:cancel:v1:'


def confirm_buttons(proposal):
    view = discord.ui.View(timeout=None)
    label = 'Confirm deletion' if proposal['kind'] == 'delete' else 'Timeout 10 min (server-wide)'
    view.add_item(discord.ui.Button(label=label, style=discord.ButtonStyle.danger, custom_id=CONFIRM+proposal['token']))
    view.add_item(discord.ui.Button(label='Cancel', style=discord.ButtonStyle.secondary, custom_id=CANCEL+proposal['token']))
    return view


class ActionTransport:
    async def receive_action_confirmation(self, interaction):
        data = interaction.data
        identity = data.get('custom_id', '') if isinstance(data, dict) else ''
        if not isinstance(identity, str):
            return False
        prefix = CONFIRM if identity.startswith(CONFIRM) else CANCEL if identity.startswith(CANCEL) else None
        if prefix is None:
            return False
        message = interaction.message
        if (interaction.type != discord.InteractionType.component or data.get('component_type') != 2
                or not re.fullmatch(r'[a-f0-9]{32}', identity[len(prefix):])
                or not self.in_scope(interaction.guild_id, interaction.channel_id)
                or str(interaction.channel_id) not in self.policy.command_channel_ids
                or interaction.user.bot or str(interaction.user.id) not in self.policy.operator_user_ids
                or message is None or str(message.author.id) != self.policy.bot_user_id
                or message.channel.id != interaction.channel_id):
            await self.interaction_notice(interaction, 'Action not authorized here.')
            return True
        if self.queue.full():
            await self.interaction_notice(interaction, 'Moderation is busy. Try the confirmation again.')
            return True
        generation = self.generation
        try:
            await asyncio.wait_for(interaction.response.defer(ephemeral=True, thinking=True), timeout=2)
        except Exception:
            return True
        if generation != self.generation or not self.online or self.closing:
            await self.interaction_notice(interaction, 'Connection changed. Request a new confirmation.', deferred=True)
            return True
        try:
            self.queue.put_nowait((generation, 'action_confirm', (interaction, identity[len(prefix):], prefix == CANCEL,
                                                               asyncio.get_running_loop().time()+30)))
        except asyncio.QueueFull:
            await self.interaction_notice(interaction, 'Moderation is busy. No action taken; try again.', deferred=True)
        return True

    def action_guard(self, payload, generation):
        if (generation != self.generation or not self.online or self.closing
                or not self.queue.empty() or not self.classifier_active()):
            raise actions.ActionError('Connection, policy or incoming evidence changed. Request the action again.')
        self.checked_channel(payload['channel_id'], sending=True)
        actions.revalidate(self.live.engine, payload)

    async def perform_action(self, payload):
        engine = self.live.engine
        generation = self.generation
        completed = []
        attempted_delete = False
        try:
            self.action_guard(payload, generation)
            fetched = []
            # Fetch every selected message before acting; never delete an edited replacement.
            for item in payload['evidence']:
                channel = self.checked_channel(item['channel_id'])
                if payload['kind'] == 'delete' and not channel.permissions_for(channel.guild.me).manage_messages:
                    raise actions.ActionError('Missing Manage Messages in the test channel.')
                message = await asyncio.wait_for(channel.fetch_message(int(item['message_id'])), timeout=5)
                from .hermes_adapter import snapshot
                changes = actions.message_changes(item, snapshot(message))
                if changes:
                    raise actions.ActionError('Message changed (' + ', '.join(changes) + '). No action taken.')
                self.action_guard(payload, generation)
                fetched.append(message)
            if (payload['automatic'] and self.policy.classifier.exempt_role_ids
                    and self.store.get_setting('role_exemption_enabled', True) is True):
                # A fetched Message.author can be a User or a cached Member. Neither
                # proves current roles. Fetch membership explicitly before auto-delete.
                try:
                    member = await asyncio.wait_for(fetched[0].guild.fetch_member(int(payload['author_id'])), timeout=5)
                    from .models import validate_ids
                    roles = validate_ids(tuple(str(role.id) for role in member.roles), 'current author roles', maximum=250)
                    if (str(member.id) != payload['author_id'] or str(member.guild.id) != self.policy.guild_id or member.bot):
                        raise ValueError('Unexpected member identity')
                except Exception as error:
                    raise actions.ActionError('Current author roles unavailable. Automatic deletion skipped.') from error
                self.action_guard(payload, generation)
                if engine.role_exempt(roles):
                    raise actions.ActionError('Author currently has an exempt role. Automatic deletion skipped.')
            if payload['kind'] == 'timeout':
                guild = fetched[0].guild
                member = await asyncio.wait_for(guild.fetch_member(int(payload['author_id'])), timeout=5)
                self.action_guard(payload, generation)
                me = guild.me
                if me is None or not me.guild_permissions.moderate_members or me.guild_permissions.administrator:
                    raise actions.ActionError('Bot needs Moderate Members, without Administrator.')
                perms = member.guild_permissions
                if (str(member.id) != payload['author_id'] or str(member.guild.id) != self.policy.guild_id
                        or member.bot or member.id == guild.owner_id
                        or str(member.id) in self.policy.operator_user_ids
                        or perms.administrator or perms.manage_guild or perms.moderate_members or perms.manage_messages
                        or set(str(role.id) for role in member.roles) & set(self.policy.classifier.exempt_role_ids)
                        or member.top_role >= me.top_role):
                    raise actions.ActionError('Protected member or role hierarchy prevents timeout.')
                now = datetime.now(timezone.utc)
                if member.timed_out_until is not None and member.timed_out_until > now:
                    raise actions.ActionError('Member already timed out. Existing timeout left unchanged.')
                key = actions.reserve(engine, payload, payload['author_id'])
                if key is None:
                    raise actions.ActionError('This timeout was already attempted; check the action record.')
                await self.action_request(key, lambda: member.timeout(now+timedelta(seconds=actions.TIMEOUT_SECONDS),
                    reason=f"Liberdus incident {payload['incident_id']}; staff {payload['actor']}; 10m"))
                completed.append(f"Timeout: {self.action_outcome(key)}")
            else:
                for message in fetched:
                    self.action_guard(payload, generation)
                    channel = self.checked_channel(str(message.channel.id))
                    if not channel.permissions_for(channel.guild.me).manage_messages:
                        raise actions.ActionError('Manage Messages permission changed.')
                    key = actions.reserve(engine, payload, str(message.id))
                    if key is None:
                        completed.append(f'{message.id}: already attempted')
                        continue
                    self.action_deletions.add(str(message.id))
                    attempted_delete = True
                    # Message.delete has no audit-reason parameter in discord.py 2.7.1.
                    # The local durable ledger records incident, actor and target.
                    await self.action_request(key, message.delete)
                    outcome = self.action_outcome(key)
                    completed.append(f'{message.id}: {outcome}')
                    if outcome not in ('done', 'already_absent'):
                        break  # Never continue a destructive batch after an uncertain/failing request.
        except actions.ActionError as error:
            completed.append(str(error))
        except (discord.NotFound, discord.Forbidden):
            completed.append('Message/member unavailable or permission denied. No further action.')
        except asyncio.CancelledError:
            raise
        except Exception:
            completed.append('Action unavailable or verification failed. Check the saved action record.')
        finally:
            if attempted_delete:
                self.action_deletions.clear()
                self.finish_own_deletions()
        return panel('Automatic deletion' if payload['automatic'] else 'Staff action result',
                     [*completed, 'Incident:', payload['incident_id'], f"By: {payload['actor']}",
                      'No new AI call. Attempts are saved.', 'Audit: !mod actions ID'])

    def finish_own_deletions(self):
        # Retain already-arrived staff controls (especially pause/off) across the
        # conservative evidence reset caused by our own deletion batch.
        controls = []
        while not self.queue.empty():
            _, kind, value = self.queue.get_nowait()
            self.queue.task_done()
            if kind in ('command', 'review_click', 'action_confirm'):
                controls.append((kind, value))
        self.coverage_gap('deleted_message')
        for kind, value in controls:
            self.enqueue(kind, value)

    async def action_request(self, key, request_factory):
        try:
            # Construct the awaitable inside the guard as well: a local argument
            # error must not strand the durable attempt in "sending".
            await asyncio.wait_for(request_factory(), timeout=8)
        except discord.NotFound:
            actions.finish(self.live.engine, key, 'already_absent')
        except discord.Forbidden:
            actions.finish(self.live.engine, key, 'denied')
        except asyncio.CancelledError:
            actions.finish(self.live.engine, key, 'uncertain')
            raise
        except Exception:
            # Discord may have applied a request despite a lost response. Never retry here.
            actions.finish(self.live.engine, key, 'uncertain')
        else:
            actions.finish(self.live.engine, key, 'done')

    def action_outcome(self, key):
        return self.store.db.execute('SELECT outcome FROM action_attempts_v1 WHERE key=?', (key,)).fetchone()[0]

    async def handle_action_confirmation(self, value):
        interaction, token, cancel, deadline = value
        event = CommandRequest(str(interaction.guild_id), str(interaction.channel_id), str(interaction.user.id),
                               'status', reply_to_message_id=str(interaction.message.id))
        try:
            if asyncio.get_running_loop().time() > deadline:
                raise actions.ActionError('Confirmation expired. Request it again.')
            self.checked_channel(event.channel_id, sending=True)
            payload = actions.consume(self.live.engine, token, event.user_id, event.channel_id,
                                      str(interaction.message.id), cancel=cancel)
            text = 'Action cancelled. No changes made.' if cancel else await self.perform_action(payload)
            if not cancel:
                refresh = AssessmentText('', {'incident_id': payload['incident_id']})
                await self.refresh_assessment_messages(refresh, event)
        except actions.ActionError as error:
            text = str(error)
        except Exception:
            text = 'Action unavailable. Check !mod incident ID before trying again.'
        await self.interaction_notice(interaction, text, deferred=True)

    async def flush_automatic_actions(self):
        candidates = self.auto_delete_candidates
        self.auto_delete_candidates = set()
        for identity in candidates:
            try:
                incident = self.store.incident(identity)
                if not incident or not self.queue.empty():
                    continue
                # Only act after the matching staff report has been delivered; no backlog replay.
                if not self.store.db.execute("SELECT 1 FROM reports WHERE incident_id=? AND incident_revision=? AND kind='moderator' AND status='sent'",
                                             (identity, incident['revision'])).fetchone():
                    continue
                payload = actions.plan(self.live.engine, identity, incident['revision'], 'delete', 'auto',
                                       self.policy.command_channel_ids[0], automatic=True)
                text = await self.perform_action(payload)
                event = CommandRequest(self.policy.guild_id, self.policy.command_channel_ids[0],
                                       self.policy.operator_user_ids[0], 'status')
                await self.refresh_assessment_messages(AssessmentText('', {'incident_id': identity}), event)
                await self.emit(event.channel_id, text, delivery_nonce('auto-action:'+identity))
            except actions.ActionError:
                continue  # Ordinary reporting still works when automatic actions are ineligible/off.
            except Exception:
                continue  # Durable attempt is authoritative; no uncertain mutation replay.
