"""Bounded action proposals and at-most-once audit; no Discord I/O here."""
import hashlib
import json
import re
from uuid import uuid4

from .display import panel
from .evidence_view import validated_evidence
from .staff_review import digest, saved_assessment

TIMEOUT_SECONDS = 600
MAX_TARGETS = 8


class ActionError(ValueError):
    """Fixed, safe operator-facing reason."""


class ActionText(str):
    def __new__(cls, text, proposal):
        value = super().__new__(cls, text)
        value.proposal = proposal
        return value


def permitted(config, name):
    return (config.actions_enabled and config.mode == 'report_only'
            and name in ('deletion', 'auto_delete', 'timeout')
            and (not config.allow_public_monitored_channels
                 or (config.allow_public_deletion and name in ('deletion', 'auto_delete'))))


def enabled(engine, name):
    return permitted(engine.config, name) and engine.store.get_setting(name + '_enabled', False) is True


def initialize(engine):
    store = engine.store
    with store.transaction():
        if store.get_setting('actions_schema', 1) != 1:
            raise ValueError('Unsupported action storage')
        store.db.execute('''CREATE TABLE IF NOT EXISTS action_proposals_v1(
            token TEXT PRIMARY KEY, actor TEXT NOT NULL, channel_id TEXT NOT NULL,
            message_id TEXT, expires_at REAL NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL)''')
        store.db.execute('''CREATE TABLE IF NOT EXISTS action_attempts_v1(
            key TEXT PRIMARY KEY, incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL, kind TEXT NOT NULL, target_id TEXT NOT NULL,
            actor TEXT NOT NULL, automatic INTEGER NOT NULL, started_at REAL NOT NULL,
            finished_at REAL, outcome TEXT NOT NULL, detail TEXT NOT NULL)''')
        store.db.execute('''CREATE TABLE IF NOT EXISTS action_evidence_v1(
            action_key TEXT PRIMARY KEY REFERENCES action_attempts_v1(key) ON DELETE CASCADE,
            source_revision INTEGER NOT NULL, source_hash TEXT NOT NULL,
            refreshed_at REAL NOT NULL, evidence_hash TEXT NOT NULL, evidence_json TEXT NOT NULL)''')
        store.db.execute("UPDATE action_proposals_v1 SET state='expired' WHERE state='pending'")
        store.db.execute("UPDATE action_attempts_v1 SET outcome='uncertain',detail='restart' WHERE outcome='sending'")
        store.set_setting('actions_schema', 1)


def message_changes(evidence, fetched):
    """Compare message data only; membership is verified separately at action time.

    Keep persisted event hashes unchanged: they still bind original role metadata
    to the classifier's evidence. REST messages need not carry gateway members.
    """
    from .models import MessageEvent
    saved = MessageEvent.from_dict({key: value for key, value in evidence.items()
                                    if key not in {'version', 'fingerprint'}})
    before, after = saved.to_dict(), fetched.to_dict()
    return tuple(key for key in before if key != 'author_role_ids' and before[key] != after[key])


def current(engine, identity, revision, *, allow_complete=False):
    incident = engine.store.incident(identity)
    if (not incident or incident['revision'] != revision or incident['status'] != 'open'
            or incident['expires_at'] <= engine._now()
            or incident['policy_hash'] != engine.config.policy_hash
            or engine.store.get_setting('policy_hash') != engine.config.policy_hash
            or engine.store.get_setting('paused', False)):
        raise ActionError('Evidence changed, expired or paused. Open a current incident.')
    validated_evidence(engine.config, incident)
    assessment = saved_assessment(engine, incident)
    if assessment['state'] == 'unavailable' or (assessment['complete'] and not allow_complete):
        raise ActionError('This evidence is dismissed or review is unavailable.')
    return incident


def plan(engine, identity, revision, kind, actor, channel_id, *, automatic=False, message_id=None):
    if kind not in ('delete', 'timeout') or not enabled(engine, 'deletion' if kind == 'delete' else 'timeout'):
        raise ActionError('Feature is off. Check !mod deletion or !mod timeout.')
    if (channel_id not in engine.config.command_channel_ids
            or (not automatic and actor not in engine.config.operator_user_ids)
            or (automatic and (kind != 'delete' or actor != 'auto'))):
        raise ActionError('Action not authorized here.')
    incident = current(engine, identity, revision)
    evidence = incident['evidence']
    if message_id is not None:
        evidence = [item for item in evidence if item['message_id'] == message_id]
    if not evidence or len(evidence) > MAX_TARGETS:
        raise ActionError('Select one message: !mod delete ID REV MESSAGE_ID (maximum 8 per confirmation).')
    if automatic and not automatic_candidate(engine, incident):
        raise ActionError('Not eligible for automatic deletion.')
    return {'incident_id': identity, 'revision': revision, 'kind': kind, 'actor': actor,
            'channel_id': channel_id, 'automatic': automatic, 'author_id': incident['author_id'],
            'policy_hash': engine.config.policy_hash, 'evidence_hash': digest(incident['evidence']),
            'epoch': engine.store.get_setting('action_epoch', 0), 'evidence': evidence,
            'created_at': engine._now()}


def automatic_candidate(engine, incident):
    from .screening import saved_screening
    if not enabled(engine, 'auto_delete') or not enabled(engine, 'deletion') or incident['rule_id'] != 'jev_message':
        return False
    view = saved_screening(engine, incident)
    return (len(incident['evidence']) == 1 and view.get('evidence_state') == 'current'
            and view.get('choice') == 'sensitive_request' and view.get('confidence', 0) > .90
            and view.get('purpose') not in ('quoted_warning', 'unclear')
            and view.get('age_seconds') is not None and 0 <= view['age_seconds'] <= 60
            and not engine.role_exempt(incident['evidence'][0].get('author_role_ids', ())))


def revalidate(engine, payload):
    if payload.get("refreshed") is True:
        from .manual_delete import revalidate as revalidate_manual
        return revalidate_manual(engine, payload)
    if (payload['epoch'] != engine.store.get_setting('action_epoch', 0)
            or payload['policy_hash'] != engine.config.policy_hash
            or not 0 <= engine._now() - payload['created_at'] <= 60):
        raise ActionError('Action expired or settings changed. Request it again.')
    incident = current(engine, payload['incident_id'], payload['revision'])
    if digest(incident['evidence']) != payload['evidence_hash']:
        raise ActionError('Evidence changed. Request a current review.')
    if not enabled(engine, 'deletion' if payload['kind'] == 'delete' else 'timeout'):
        raise ActionError('Feature is off.')
    if payload['automatic'] and not automatic_candidate(engine, incident):
        raise ActionError('Automatic deletion no longer eligible.')
    if not payload['automatic'] and payload['actor'] not in engine.config.operator_user_ids:
        raise ActionError('Operator no longer authorized.')
    return incident


def propose(engine, identity, revision, kind, actor, channel_id, message_id=None):
    payload = plan(engine, identity, int(revision), kind, actor, channel_id, message_id=message_id)
    title = 'Confirm message deletion' if kind == 'delete' else 'Confirm 10-minute timeout'
    lines = ["ACTION", "--------------------------------", f"Revision: {revision}", "Member ID", payload['author_id']]
    lines += ([f"Delete {len(payload['evidence'])} message(s).", 'Deletion cannot be undone.'] if kind == 'delete'
              else ['10-minute SERVER-WIDE timeout.', 'No automatic account penalties.'])
    text = panel(title, lines + ['', 'Only you can confirm.', 'Expires in 60 seconds.', '', 'REFERENCE', '--------------------------------', 'Incident ID', identity])
    for index, item in enumerate(payload['evidence'], 1):
        url = f"https://discord.com/channels/{engine.config.guild_id}/{item['channel_id']}/{item['message_id']}"
        text += f"\n[Message {index}: {item['message_id']}](<{url}>)"
    return store_proposal(engine, payload, text)


def store_proposal(engine, payload, text):
    kind, actor, channel_id = payload['kind'], payload['actor'], payload['channel_id']
    token = uuid4().hex
    store = engine.store
    with store.transaction():
        store.db.execute("DELETE FROM action_proposals_v1 WHERE expires_at < ? OR state!='pending'", (engine._now(),))
        if store.db.execute('SELECT count(*) FROM action_proposals_v1').fetchone()[0] >= 100:
            raise ActionError('Too many outstanding confirmations.')
        store.db.execute('INSERT INTO action_proposals_v1 VALUES(?,?,?,NULL,?,\'pending\',?)',
                         (token, actor, channel_id, engine._now()+60, json.dumps(payload)))
    return ActionText(text, {'token': token, 'kind': kind, 'actor': actor, 'channel_id': channel_id})


def bind(engine, proposal, message_id):
    if not re.fullmatch(r'[1-9][0-9]{0,19}', str(message_id)):
        raise ActionError('Confirmation message unavailable.')
    engine.store.db.execute("UPDATE action_proposals_v1 SET message_id=? WHERE token=? AND message_id IS NULL AND state='pending'",
                            (str(message_id), proposal['token']))


def consume(engine, token, actor, channel_id, message_id, *, cancel=False):
    with engine.store.transaction():
        row = engine.store.db.execute('SELECT * FROM action_proposals_v1 WHERE token=?', (token,)).fetchone()
        if (not row or row['actor'] != actor or actor not in engine.config.operator_user_ids
                or row['channel_id'] != channel_id or channel_id not in engine.config.command_channel_ids
                or row['message_id'] != str(message_id) or row['state'] != 'pending' or row['expires_at'] <= engine._now()):
            raise ActionError('Confirmation expired, already used, or belongs to another operator.')
        engine.store.db.execute("UPDATE action_proposals_v1 SET state=? WHERE token=?", ('cancelled' if cancel else 'used', token))
        payload = json.loads(row['payload'])
    if not cancel:
        revalidate(engine, payload)
    return payload


def reserve(engine, payload, target_id):
    # Deletions dedupe across reports; one timeout per incident, with a member cooldown.
    kind = payload['kind']
    key = hashlib.sha256(f"{kind}:{engine.config.guild_id}:{target_id if kind == 'delete' else payload['incident_id']}".encode()).hexdigest()
    now = engine._now()
    with engine.store.transaction():
        if engine.store.db.execute('SELECT 1 FROM action_attempts_v1 WHERE key=?', (key,)).fetchone():
            return None
        if kind == 'timeout' and engine.store.db.execute(
                "SELECT 1 FROM action_attempts_v1 WHERE kind='timeout' AND target_id=? AND started_at>? AND outcome IN ('sending','done','uncertain')",
                (target_id, now-TIMEOUT_SECONDS)).fetchone():
            raise ActionError('Member already had a recent timeout attempt. Check Discord first.')
        if payload['automatic'] and engine.store.db.execute(
                "SELECT count(*) FROM action_attempts_v1 WHERE automatic=1 AND started_at>?", (now-60,)).fetchone()[0] >= 5:
            raise ActionError('Automatic deletion limit reached; staff review required.')
        engine.store.db.execute('INSERT INTO action_attempts_v1 VALUES(?,?,?,?,?,?,?,?,NULL,\'sending\',\'\')',
            (key, payload['incident_id'], payload['revision'], kind, target_id, payload['actor'], int(payload['automatic']), now))
        if payload.get('refreshed') is True:
            selected = [item for item in payload['evidence'] if item['message_id'] == target_id]
            engine.store.db.execute('INSERT INTO action_evidence_v1 VALUES(?,?,?,?,?,?)',
                (key, payload['revision'], payload['source_hash'], payload['refreshed_at'],
                 digest(selected), json.dumps(selected, ensure_ascii=True)))
    return key


def finish(engine, key, outcome, detail=''):
    engine.store.db.execute("UPDATE action_attempts_v1 SET outcome=?,detail=?,finished_at=? WHERE key=? AND outcome='sending'",
                            (outcome, detail, engine._now(), key))


def history(engine, identity):
    exists = engine.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='action_attempts_v1'").fetchone()
    if not exists:
        return []
    return [dict(row) for row in engine.store.db.execute(
        'SELECT kind,target_id,actor,automatic,outcome,revision,started_at FROM action_attempts_v1 WHERE incident_id=? ORDER BY started_at DESC LIMIT 8', (identity,))]
