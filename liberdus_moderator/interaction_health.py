"""Bounded button diagnostics. Never retain tokens, content or exception text."""
from datetime import datetime, timezone
import logging

KEY = 'discord_interaction_health_v1'
LOGGER = logging.getLogger('gateway.platforms.liberdus_moderator.interactions')
STAGES = {'received', 'acknowledged', 'queued', 'checking', 'rejected',
          'action_error', 'ack_error', 'reply_error', 'recovery_error', 'recovered', 'replied'}


def record(engine, interaction, stage, error=None):
    if stage not in STAGES:
        raise ValueError('Invalid interaction stage')
    identity = getattr(interaction, 'id', None)
    message = getattr(getattr(interaction, 'message', None), 'id', None)
    event = dict(at=engine._now(), stage=stage,
                 interaction_id=str(identity) if type(identity) is int and 0 < identity < 2**64 else None,
                 message_id=str(message) if type(message) is int and 0 < message < 2**64 else None)
    if error is not None:
        import discord
        event['code'] = ('timeout' if isinstance(error, TimeoutError) else
                         'http' if isinstance(error, discord.HTTPException) else
                         'io' if isinstance(error, OSError) else 'internal')
        if isinstance(error, discord.HTTPException):
            if type(error.status) is int and 100 <= error.status <= 599:
                event['http_status'] = error.status
            if type(error.code) is int and 0 <= error.code < 10**9:
                event['discord_code'] = error.code
    # Diagnostic persistence must not prevent the bounded completion reply.
    try:
        history = engine.store.get_setting(KEY, [])
        history = history if isinstance(history, list) else []
        engine.store.set_setting(KEY, (history + [event])[-24:])
    except Exception:
        LOGGER.warning('Liberdus button diagnostic could not be saved')
    LOGGER.info('Liberdus button stage=%s interaction=%s code=%s http=%s discord=%s',
                stage, event['interaction_id'], event.get('code'),
                event.get('http_status'), event.get('discord_code'))


def failure_lines(store):
    history = store.get_setting(KEY, [])
    if not isinstance(history, list):
        return []
    for item in reversed(history[-24:]):
        if not isinstance(item, dict) or item.get('stage') not in STAGES or item.get('code') not in {'timeout', 'http', 'io', 'internal'}:
            continue
        at = item.get('at')
        if type(at) not in (int, float) or not 0 < at <= 253402300799:
            continue
        stamp = datetime.fromtimestamp(at, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        return ['Last button error: ' + stamp, item['stage'] + ': ' + item['code']]
    return []
