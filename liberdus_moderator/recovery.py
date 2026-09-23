"""Bounded recovery evidence and durable progress, separate from live rule windows."""
import json
import hashlib

from .models import MessageEvent
from .rules import content_fingerprint

KEY = 'catchup_v1'
LOOKBACK = 300
PER_CHANNEL = 100
TOTAL = 200
OVERLAP = 2
MAX_RUN_SECONDS = 600
PREFIX = 'Recovered after disconnect: '


def content_key(event, policy_hash, rubric_hash):
    values = event.to_dict()
    values.pop('author_role_ids')  # REST membership can differ without the text changing.
    return hashlib.sha256(json.dumps([values, policy_hash, rubric_hash], sort_keys=True,
                                    separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def initialize(engine):
    db = engine.store.db
    db.execute('CREATE TABLE IF NOT EXISTS catchup_evidence_v1('
               'message_id TEXT PRIMARY KEY, policy_hash TEXT NOT NULL, window REAL NOT NULL, '
               'epoch INTEGER NOT NULL, coverage INTEGER NOT NULL, created_at REAL NOT NULL, event_json TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS catchup_flags_v1('
               'incident_id TEXT PRIMARY KEY REFERENCES incidents(id) ON DELETE CASCADE)')


def present(engine, table):
    return engine.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def recovered_incident(engine, identity):
    return (present(engine, 'catchup_flags_v1') and engine.store.db.execute(
        'SELECT 1 FROM catchup_flags_v1 WHERE incident_id=?', (identity,)).fetchone() is not None)


def evidence(engine, identity):
    if not present(engine, 'catchup_evidence_v1'):
        return None
    row = engine.store.db.execute('SELECT * FROM catchup_evidence_v1 WHERE message_id=?', (identity,)).fetchone()
    if (row is None or row['policy_hash'] != engine.config.policy_hash
            or row['window'] != engine.store.get_setting('coverage_started_at', 0)
            or row['epoch'] != engine.store.get_setting('action_epoch', 0)
            or row['coverage'] != engine.store.get_setting('coverage_gaps', 0)
            or not 0 <= engine._now() - row['window'] <= MAX_RUN_SECONDS
            or engine.store.get_setting('paused', False)):
        return None
    event = MessageEvent.from_dict(json.loads(row['event_json']))
    if (event.message_id != identity or event.guild_id != engine.config.guild_id
            or event.channel_id not in engine.config.monitored_channel_ids
            or event.is_bot or event.is_webhook or event.is_thread or event.has_attachments
            or event.author_id == engine.config.bot_user_id or not event.content.strip()
            or not engine.role_evidence_available(event.author_role_ids) or engine.role_exempt(event.author_role_ids)
            or event.created_at <= engine._now() - engine.config.storage.retention_seconds
            or event.modified_at > engine._now()):
        return None
    return {**event.to_dict(), 'version': event.version, 'fingerprint': content_fingerprint(event.content)}


def stage(engine, event):
    """Only the verified history transport calls this; never alter live coverage."""
    engine.store.db.execute('DELETE FROM catchup_evidence_v1 WHERE created_at<?',
                           (engine._now() - engine.config.storage.retention_seconds,))
    # Bound retained recovery snapshots even under repeated interruption.
    if (engine.store.db.execute('SELECT count(*) FROM catchup_evidence_v1').fetchone()[0] >= min(2000, engine.config.storage.max_messages)
            and not engine.store.db.execute('SELECT 1 FROM catchup_evidence_v1 WHERE message_id=?', (event.message_id,)).fetchone()):
        return False
    engine.store.db.execute('INSERT OR REPLACE INTO catchup_evidence_v1 VALUES(?,?,?,?,?,?,?)',
        (event.message_id, engine.config.policy_hash, engine.store.get_setting('coverage_started_at', 0),
         engine.store.get_setting('action_epoch', 0), engine.store.get_setting('coverage_gaps', 0),
         event.created_at, json.dumps(event.to_dict())))
    return True


def invalidate(engine, identity):
    if present(engine, 'catchup_evidence_v1'):
        engine.store.db.execute('UPDATE catchup_evidence_v1 SET window=-1 WHERE message_id=?', (identity,))


def status(engine):
    saved = engine.store.get_setting(KEY)
    if not isinstance(saved, dict):
        return 'Not started'
    labels = {'idle': 'Ready', 'pending': 'Pending', 'running': 'Running', 'complete': 'Complete',
              'incomplete': 'Incomplete — staff review may be needed', 'paused': 'Paused', 'off': 'Off'}
    label = labels.get(saved.get('status'), 'Unavailable')
    return f"{label}; checked {saved.get('checked', 0)}, skipped {saved.get('skipped', 0)}"
