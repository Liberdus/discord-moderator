"""Bounded connection metadata only: no messages, frames, tokens or session IDs."""
from datetime import datetime, timezone
import logging
import math

from .display import panel

KEY = 'discord_connection_health_v1'
LOGGER = logging.getLogger('gateway.platforms.liberdus_moderator.connection')


def state(engine):
    value = engine.store.get_setting(KEY, None)
    if value is None:
        return dict(version=1, disconnects=0, resumes=0, sessions=0, pending_since=None,
                    last_close_code=None, last_recovery=None, last_duration=None, history=[])
    fields = {'version','disconnects','resumes','sessions','pending_since','last_close_code',
              'last_recovery','last_duration','history'}
    if not isinstance(value, dict) or set(value) != fields or value['version'] != 1:
        raise ValueError('Connection metadata unavailable')
    for key in ('disconnects','resumes','sessions'):
        if type(value[key]) is not int or not 0 <= value[key] < 2**63:
            raise ValueError('Invalid connection counter')
    for key in ('pending_since','last_duration'):
        if value[key] is not None and (type(value[key]) not in (int,float)
                or not math.isfinite(value[key]) or value[key] < 0):
            raise ValueError('Invalid connection timing')
    if value['last_recovery'] not in (None,'resumed','new_session'):
        raise ValueError('Invalid recovery state')
    if value['last_close_code'] is not None and (type(value['last_close_code']) is not int or not 1000 <= value['last_close_code'] <= 4999):
        raise ValueError('Invalid close code')
    if not isinstance(value['history'], list) or len(value['history']) > 8:
        raise ValueError('Invalid connection history')
    for item in value['history']:
        if (not isinstance(item,dict) or set(item) != {'event','at'}
                or item['event'] not in ('disconnected','resumed','new_session')
                or type(item['at']) not in (int,float) or not math.isfinite(item['at'])
                or not 0 < item['at'] <= 253402300799):
            raise ValueError('Invalid connection event')
    return value


def record(engine, event, close_code=None):
    if event not in ('disconnected','resumed','new_session'):
        raise ValueError('Invalid connection event')
    with engine.store.transaction():
        value = state(engine)
        now = engine._now()
        counter = {'disconnected':'disconnects','resumed':'resumes','new_session':'sessions'}[event]
        value[counter] = min(2**63-1,value[counter]+1)
        if event == 'disconnected':
            value['pending_since'] = now
            value['last_close_code'] = close_code if type(close_code) is int and 1000 <= close_code <= 4999 else None
        else:
            start=value['pending_since']
            value['last_duration'] = round(now-start,3) if start is not None and now >= start else None
            value['last_recovery'] = event
            value['pending_since'] = None
        value['history'] = (value['history']+[{'event':event,'at':now}])[-8:]
        engine.store.set_setting(KEY,value)
    LOGGER.info('Liberdus Discord connection event=%s close_code=%s outage_seconds=%s',
                event, value['last_close_code'], value['last_duration'])


def summary(engine, connected):
    try:
        value=state(engine)
    except ValueError:
        return panel('Discord connection', ['Saved connection metadata unavailable.', 'No AI call or action.'])
    lines=['CONNECTION', '-'*32, 'Connected now: '+('Yes' if connected else 'No'),
           f"Observed disconnects: {value['disconnects']}", f"Resumed sessions: {value['resumes']}",
           f"New sessions: {value['sessions']}", 'Last recovery: '+(value['last_recovery'] or 'not recorded'),
           'Last outage seconds: '+str(value['last_duration'] if value['last_duration'] is not None else 'unknown'),
           'Observed close code: '+str(value['last_close_code'] if value['last_close_code'] is not None else 'unknown'),
           '', 'RECENT EVENTS (UTC)', '-'*32]
    for item in value['history'][-5:]:
        lines += [datetime.fromtimestamp(item['at'],timezone.utc).strftime('%m-%d %H:%M:%S')+' '+item['event']]
    lines += ['', 'Since connection tracking began.', 'Close code alone is not a cause.',
              'Reconnects invalidate confirmations.', 'Click Delete for a fresh check.', 'Read only; no AI call or action.']
    return panel('Discord connection',lines)
