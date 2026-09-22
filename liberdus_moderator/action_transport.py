"""Discord actions reachable only through reviewed proposals or the narrow auto rule."""
import asyncio
from datetime import datetime, timedelta, timezone
import re

import discord

from . import actions
from .commands import CommandRequest
from .display import panel
from .card_display import card_view
from .live import AssessmentText, delivery_nonce
from .member_roles import checked_membership, require_complete_role_cache, PROTECTED_TIMEOUT_PERMISSIONS

ACTION_BUTTONS = {'liberdus:action:v1:delete': 'delete', 'liberdus:action:v1:dismiss': 'dismiss',
                  'liberdus:action:v1:timeout': 'timeout'}
CONFIRM = 'liberdus:confirm:v1:'
CANCEL = 'liberdus:cancel:v1:'


class DeletionNotice(str):
    """Shared receipt for deletions confirmed done during this one action attempt."""

    def __new__(cls, payload, deleted, timestamp):
        count, selected = len(deleted), len(payload['evidence'])
        actor = 'Automatic moderation' if payload['automatic'] else f"<@{payload['actor']}>"
        lines = [f"## {count} message{'s' if count != 1 else ''} deleted",
                 f"**Sender:** <@{payload['author_id']}> · ID `{payload['author_id']}`",
                 f"**Deleted by:** {actor}", f"**Completed:** <t:{int(timestamp)}:f>"]
        if count != selected:
            lines += [f"**Partial result: {count} of {selected} selected messages confirmed deleted.**",
                      "Check the action record for the remaining messages."]
        lines += ['\n**Deleted messages**']
        lines += [f"<#{channel}> · `{message}`" for _, channel, message in deleted]
        lines += [f"\nIncident: `{payload['incident_id']}`", f"Action record: `!mod actions {payload['incident_id']}`"]
        result = super().__new__(cls, '\n'.join(lines))
        result.nonce = delivery_nonce('deleted:' + ':'.join(sorted(key for key, _, _ in deleted)))
        result.partial = count != selected
        return result


class ActionResult(str):
    def __new__(cls, content, notice=None):
        result = super().__new__(cls, content)
        result.deletion_notice = notice
        return result


def confirm_buttons(proposal, content):
    label = 'Confirm deletion' if proposal['kind'] == 'delete' else 'Timeout 10 min (server-wide)'
    return card_view(content, controls=(
        discord.ui.Button(label=label, style=discord.ButtonStyle.danger, custom_id=CONFIRM+proposal['token']),
        discord.ui.Button(label='Cancel', style=discord.ButtonStyle.secondary, custom_id=CANCEL+proposal['token'])),
        accent_colour=0xF0B232)


class ActionTransport:
    async def prepare_manual_delete(self, content):
        from .manual_delete import DeleteRequest, guard, checked_refresh, confirmation
        if not isinstance(content, DeleteRequest):
            return content
        request = content.request
        generation = self.generation
        def check():
            if (generation != self.generation or not self.online or self.closing
                    or not self.queue.empty() or not self.classifier_active()):
                raise actions.ActionError('Connection, policy or incoming evidence changed. Click Delete again.')
            self.checked_channel(request['channel_id'], sending=True)
            guard(self.live.engine, request)
        try:
            check()
            refreshed = []
            for item in request['evidence']:
                channel = self.checked_channel(item['channel_id'])
                if not channel.permissions_for(channel.guild.me).manage_messages:
                    raise actions.ActionError('Missing Manage Messages in the monitored channel.')
                message = await asyncio.wait_for(channel.fetch_message(int(item['message_id'])), timeout=5)
                from .discord_service import snapshot
                refreshed.append(snapshot(message))
                check()
                self.checked_channel(item['channel_id'])
            payload = checked_refresh(self.live.engine, request, refreshed)
            return confirmation(self.live.engine, payload)
        except discord.NotFound:
            text = 'A selected message is already gone. No deletion or confirmation created.'
        except discord.Forbidden:
            text = 'Cannot read a selected message. Check the bot permissions. No deletion requested.'
        except actions.ActionError as error:
            text = str(error)
        except asyncio.CancelledError:
            raise
        except Exception:
            text = 'Could not verify current message content. No deletion requested; try Delete again.'
        return panel('Deletion unavailable', [text])

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
        if not await self.defer_interaction(interaction):
            return True
        if generation != self.generation or not self.online or self.closing:
            await self.interaction_notice(interaction, 'Connection changed. Request a new confirmation.', deferred=True)
            return True
        try:
            self.queue.put_nowait((generation, 'action_confirm', (interaction, identity[len(prefix):], prefix == CANCEL,
                                                               asyncio.get_running_loop().time()+30)))
            from .interaction_health import record
            record(self.live.engine, interaction, 'queued')
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
        deleted = []
        attempted_delete = False
        try:
            self.action_guard(payload, generation)
            fetched = []
            # Fetch every selected message before acting; never delete an edited replacement.
            for item in payload['evidence']:
                channel = self.checked_channel(item['channel_id'])
                if payload['kind'] == 'delete' and not channel.permissions_for(channel.guild.me).manage_messages:
                    raise actions.ActionError('Missing Manage Messages in the monitored channel.')
                message = await asyncio.wait_for(channel.fetch_message(int(item['message_id'])), timeout=5)
                from .discord_service import snapshot
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
                    roles = checked_membership(member, self.policy.guild_id, payload['author_id'])
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
                try:
                    roles = checked_membership(member, self.policy.guild_id, payload['author_id'])
                    require_complete_role_cache(member)
                    if me is None or str(me.id) != self.policy.bot_user_id or str(me.guild.id) != self.policy.guild_id:
                        raise ValueError('Bot membership unavailable')
                    require_complete_role_cache(me)
                except (AttributeError, ValueError, TypeError) as error:
                    raise actions.ActionError('Current role privileges unavailable. Timeout skipped.') from error
                if not me.guild_permissions.moderate_members or me.guild_permissions.administrator:
                    raise actions.ActionError('Bot needs Moderate Members, without Administrator.')
                perms = member.guild_permissions
                if (str(member.id) != payload['author_id'] or str(member.guild.id) != self.policy.guild_id
                        or member.bot or member.id == guild.owner_id
                        or str(member.id) in self.policy.operator_user_ids
                        or any(getattr(perms, name) for name in PROTECTED_TIMEOUT_PERMISSIONS)
                        or set(roles) & set(self.policy.classifier.exempt_role_ids)
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
                    if outcome == 'done':
                        deleted.append((key, str(message.channel.id), str(message.id)))
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
        notice = DeletionNotice(payload, deleted, engine._now()) if deleted else None
        return ActionResult(panel('Automatic deletion' if payload['automatic'] else 'Staff action result',
                     ['RESULT', '--------------------------------', *completed, '', 'REFERENCE', '--------------------------------', 'Incident ID', payload['incident_id'], f"By: {payload['actor']}",
                      'No new AI call. Attempts are saved.', 'Audit: !mod actions ID']), notice)

    def finish_own_deletions(self):
        # Stop state is already durable. Cancel old interactions rather than
        # replaying a confirmation after its evidence generation changed.
        self.coverage_gap('deleted_message')

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
        from .interaction_health import record
        interaction, token, cancel, deadline = value
        record(self.live.engine, interaction, 'checking')
        event = CommandRequest(str(interaction.guild_id), str(interaction.channel_id), str(interaction.user.id),
                               'status', reply_to_message_id=str(interaction.message.id))
        try:
            if asyncio.get_running_loop().time() > deadline:
                raise actions.ActionError('Confirmation expired. Request it again.')
            self.checked_channel(event.channel_id, sending=True)
            payload = actions.consume(self.live.engine, token, event.user_id, event.channel_id,
                                      str(interaction.message.id), cancel=cancel)
            text = panel('Action cancelled', ['No changes made.']) if cancel else await self.perform_action(payload)
            if not cancel:
                notice = getattr(text, 'deletion_notice', None)
                if notice is not None:
                    try:
                        await self.emit(payload['channel_id'], notice, notice.nonce)
                    except Exception:
                        # Deletion has already completed. Never turn a delivery failure
                        # into a failed action or retry the mutation/uncertain notice.
                        text = str(text) + '\nShared deletion notice could not be confirmed. Check !mod actions ID.'
                refresh = AssessmentText('', {'incident_id': payload['incident_id']})
                await self.refresh_assessment_messages(refresh, event)
        except actions.ActionError as error:
            record(self.live.engine, interaction, 'rejected')
            text = str(error)
        except Exception as error:
            record(self.live.engine, interaction, 'action_error', error)
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
                notice = getattr(text, 'deletion_notice', None)
                await self.emit(event.channel_id, notice if notice is not None else text,
                                notice.nonce if notice is not None else delivery_nonce('auto-action:'+identity))
            except actions.ActionError:
                continue  # Ordinary reporting still works when automatic actions are ineligible/off.
            except Exception:
                continue  # Durable attempt is authoritative; no uncertain mutation replay.
