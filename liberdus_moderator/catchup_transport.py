"""Serial, bounded history reconciliation that yields priority to live moderation."""
import asyncio
from datetime import datetime, timezone
import hashlib
import math

import discord

from . import recovery
from .member_roles import checked_membership
from .screening import SCREENING_HASH
from .classifier import encoded


class Catchup:
    def __init__(self, service):
        self.service = service
        self.engine, self.store = service.live.engine, service.store
        recovery.initialize(self.engine)
        saved = self.store.get_setting(recovery.KEY)
        self.task = None
        self.connected_once = False
        self.next_admission = 0
        self.unclean = False
        if saved is not None:
            self.validate(saved)
            self.unclean = saved['running'] and self.same_settings(saved)
        self.data = saved if self.unclean else self.fresh()
        self.save()

    def validate(self, data):
        if (not isinstance(data, dict) or data.get('version') != 1
                or type(data.get('running')) is not bool or not isinstance(data.get('channels'), dict)
                or len(data['channels']) > 500 or not isinstance(data.get('policy'), str)
                or type(data.get('epoch')) is not int
                or data.get('status') not in {'idle', 'pending', 'running', 'complete', 'incomplete', 'paused', 'off'}
                or any(type(data.get(key)) is not int or not 0 <= data[key] <= recovery.TOTAL
                       for key in ('used', 'checked', 'skipped'))
                or type(data.get('notified')) is not bool):
            raise ValueError('Invalid catch-up progress')
        for identity, row in data['channels'].items():
            if (not isinstance(identity, str) or not identity.isdecimal() or not isinstance(row, dict) or set(row) != {'cursor', 'end', 'after', 'count'}
                    or any(value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value <= 0)
                           for value in (row['cursor'], row['end']))
                    or row['cursor'] is None or type(row['count']) is not int or not 0 <= row['count'] <= recovery.PER_CHANNEL
                    or (row['after'] is not None and (not isinstance(row['after'], str) or not row['after'].isdecimal()))):
                raise ValueError('Invalid catch-up channel progress')

    def fresh(self):
        now = self.engine._now()
        return dict(version=1, running=False, policy=self.engine.config.policy_hash,
                    epoch=self.store.get_setting('action_epoch', 0), status='idle', used=0, checked=0,
                    skipped=0, notified=False, channels={identity: dict(cursor=now, end=None, after=None, count=0)
                        for identity in self.engine.config.monitored_channel_ids})

    def same_settings(self, data):
        return (data['policy'] == self.engine.config.policy_hash
                and data['epoch'] == self.store.get_setting('action_epoch', 0)
                and set(data['channels']) == set(self.engine.config.monitored_channel_ids))

    def save(self):
        self.store.set_setting(recovery.KEY, self.data)

    def enabled(self):
        return (self.engine.config.ai_enabled and self.engine.config.classifier.mode == 'report_only'
                and not self.store.get_setting('paused', False))

    def problem(self):
        self.data['status'] = 'incomplete'
        if not self.data['notified']:
            self.data['notified'] = True
            if self.service.health is not None:
                self.service.health.gap('catchup_incomplete')
        self.save()

    def ready(self):
        now = self.engine._now()
        if not self.enabled() or not self.same_settings(self.data):
            self.data = self.fresh()
            self.data['status'] = 'paused' if self.store.get_setting('paused', False) else 'off' if not self.enabled() else 'idle'
        elif self.connected_once or self.unclean:
            pending = any(row['end'] is not None for row in self.data['channels'].values())
            if not pending:
                self.data.update(used=0, checked=0, skipped=0, notified=False, status='pending')
            for row in self.data['channels'].values():
                start = row['cursor'] if row['end'] is not None else row['cursor'] - recovery.OVERLAP
                floor = now - recovery.LOOKBACK
                if start < floor or start > now:
                    self.problem()
                    start, row['after'] = max(floor, min(start, now)), None
                row['cursor'], row['end'] = start, now
        else:
            # First installation, upgrade without checkpoints, or intentional stop.
            self.data = self.fresh()
        self.connected_once = True
        self.unclean = False
        self.data['running'] = True
        self.save()
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run())

    def valid(self, generation):
        return (self.service.online and not self.service.closing and generation == self.service.generation
                and self.enabled() and self.same_settings(self.data) and self.service.policy_current())

    async def idle(self, generation):
        # Do not fill the shared screening queue with historical work.
        for _ in range(600):
            if not self.valid(generation):
                return False
            if self.service.queue.empty() and not self.service.classifier.queued:
                return True
            await asyncio.sleep(.1)
        self.problem()
        return False

    def done_channel(self, row):
        row.update(cursor=row['end'], end=None, after=None, count=0)
        if all(item['end'] is None for item in self.data['channels'].values()):
            self.data['status'] = 'incomplete' if self.data['notified'] else 'complete'
        self.save()

    async def message(self, channel, message, row, generation):
        from .discord_service import snapshot
        if not self.valid(generation):
            return False
        if message.author.bot or message.webhook_id is not None:
            self.data['skipped'] += 1
            return True
        try:
            expected = str(message.id)
            message = await asyncio.wait_for(channel.fetch_message(message.id), timeout=8)
            roles = ()
            if (self.engine.config.classifier.exempt_role_ids
                    and self.store.get_setting('role_exemption_enabled', True)):
                member = await asyncio.wait_for(channel.guild.fetch_member(message.author.id), timeout=5)
                roles = checked_membership(member, self.engine.config.guild_id, str(message.author.id))
            event = snapshot(message, author_role_ids=roles)
            self.service.checked_channel(str(channel.id))
            if (event.message_id != expected or event.guild_id != self.engine.config.guild_id or event.channel_id != str(channel.id)
                    or not row['cursor'] <= event.created_at <= row['end']):
                raise ValueError('Recovery identity or interval mismatch')
        except (discord.HTTPException, ValueError, AttributeError, TypeError, TimeoutError):
            if not self.valid(generation):
                return False
            self.problem()
            self.data['skipped'] += 1
            return True
        if not await self.idle(generation):
            return False
        if (event.is_bot or event.is_webhook or event.is_thread or event.has_attachments or not event.content.strip()
                or self.engine.role_exempt(event.author_role_ids)):
            self.data['skipped'] += 1
            return True
        key = hashlib.sha256(encoded([event.message_id, event.version, self.engine.config.policy_hash, SCREENING_HASH])).hexdigest()
        attempt = self.store.db.execute('SELECT a.outcome FROM screening_versions_v1 v '
            'JOIN screening_attempts_v1 a ON a.key=v.attempt_key WHERE v.fingerprint=?',
            (recovery.content_key(event, self.engine.config.policy_hash, SCREENING_HASH),)).fetchone()
        if attempt is None:
            attempt = self.store.db.execute('SELECT outcome FROM screening_attempts_v1 WHERE key=?', (key,)).fetchone()
        if attempt is not None:
            self.data['skipped'] += 1
            if attempt['outcome'] != 'ok':
                self.problem()  # Unknown/failed charges are never automatically retried.
            return True
        self.next_admission = (self.store.get_setting('screening_last_attempt_at', 0)
                               + self.engine.config.classifier.min_interval_seconds)
        if self.engine._now() < self.next_admission:
            return False  # Leave room for live work instead of queuing a sleeping recovery job.
        if not recovery.stage(self.engine, event):
            self.problem()
            self.data['skipped'] += 1
            return True
        self.service.classifier.submit([event.message_id])
        if not await self.idle(generation):
            return False
        attempt = self.store.db.execute('SELECT outcome FROM screening_attempts_v1 WHERE key=?', (key,)).fetchone()
        if attempt is not None and attempt['outcome'] == 'ok':
            self.data['checked'] += 1
        else:
            self.data['skipped'] += 1
            self.problem()
        return True

    async def scan(self, identity, row, generation):
        if self.engine._now() - row['end'] > recovery.MAX_RUN_SECONDS or self.data['used'] >= recovery.TOTAL:
            self.problem()
            self.done_channel(row)
            return
        if self.engine._now() < self.next_admission:
            return
        if not await self.idle(generation):
            return
        try:
            channel = self.service.checked_channel(identity)
            start = datetime.fromtimestamp(row['cursor'], timezone.utc)
            end = datetime.fromtimestamp(row['end'], timezone.utc)
            after = discord.Object(id=int(row['after'])) if row['after'] else discord.Object(id=discord.utils.time_snowflake(start) - 1)
            before = discord.Object(id=discord.utils.time_snowflake(end, high=True))
            limit = min(recovery.PER_CHANNEL - row['count'], recovery.TOTAL - self.data['used'])
            async def fetch():
                return [message async for message in channel.history(limit=limit + 1, after=after, before=before, oldest_first=True)]
            messages = await asyncio.wait_for(fetch(), timeout=20)
            if not self.valid(generation):
                return
            previous = int(row['after']) if row['after'] else 0
            for message in messages[:limit]:
                if (message.id <= previous or str(message.guild.id) != self.engine.config.guild_id
                        or str(message.channel.id) != identity):
                    raise ValueError('Invalid recovery ordering')
                if not await self.message(channel, message, row, generation):
                    return
                previous = message.id
                row['after'] = str(message.id)
                row['count'] += 1
                self.data['used'] += 1
                self.save()  # Checkpoint after each terminal result, never before screening.
                await asyncio.sleep(0)  # Even batches of ignored bots yield to live events.
            if len(messages) > limit:
                self.problem()
            self.done_channel(row)
        except (discord.HTTPException, ValueError, AttributeError, TypeError, TimeoutError):
            if self.valid(generation):
                self.problem()
                self.done_channel(row)

    async def run(self):
        try:
            while not self.service.closing:
                if self.service.online:
                    self.service.refresh_scope()
                    if not self.same_settings(self.data) or not self.enabled():
                        self.data = self.fresh()
                        self.data['running'] = True
                        self.data['status'] = 'paused' if self.store.get_setting('paused', False) else 'off' if not self.enabled() else 'idle'
                        self.save()
                    elif self.service.policy_current():
                        pending = next(((identity, row) for identity, row in self.data['channels'].items() if row['end'] is not None), None)
                        if pending:
                            if not self.data['notified']:
                                self.data['status'] = 'running'
                            await self.scan(*pending, self.service.generation)
                        elif self.service.queue.empty() and not self.service.classifier.queued:
                            now = self.engine._now()
                            for row in self.data['channels'].values():
                                row['cursor'] = now
                            self.save()
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Fixed advisory state only; no payloads, tokens, or exception strings.
            self.problem()

    async def close(self, clean):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.data['running'] = not clean
        self.save()
