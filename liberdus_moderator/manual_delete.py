"""Staff deletion of freshly fetched messages; never refresh a model verdict."""
import re

from . import actions
from .display import panel
from .evidence_view import excerpt, units, validated_evidence
from .staff_review import digest, saved_assessment, snapshot


class DeleteRequest(str):
    def __new__(cls, request):
        value = super().__new__(cls, 'Fetching current message for deletion confirmation.')
        value.request = request
        return value


def source(engine, request):
    incident = engine.store.incident(request['incident_id'])
    if not incident:
        raise actions.ActionError('Saved incident is no longer available. No deletion requested.')
    try:
        evidence = snapshot(engine, incident, request['revision'])
    except (ValueError, KeyError, TypeError):
        raise actions.ActionError('Saved message references are unavailable. No deletion requested.') from None
    # Source references must still be within today's configured scope. Old JEV
    # scores and incident status are deliberately not made current by a refetch.
    for revision in (request['revision'], None):
        assessment = saved_assessment(engine, incident, revision)
        if assessment['state'] == 'unavailable' or assessment['complete']:
            raise actions.ActionError('This evidence is dismissed or review is unavailable.')
    return incident, evidence


def guard(engine, request):
    if (not actions.enabled(engine, 'deletion') or engine.store.get_setting('paused', False)
            or engine.store.get_setting('policy_hash') != engine.config.policy_hash
            or request['policy_hash'] != engine.config.policy_hash
            or request['epoch'] != engine.store.get_setting('action_epoch', 0)
            or request['coverage'] != engine.store.get_setting('coverage_gaps', 0)
            or request['actor'] not in engine.config.operator_user_ids
            or request['channel_id'] not in engine.config.command_channel_ids
            or not 0 <= engine._now() - request['created_at'] <= 60):
        raise actions.ActionError('Deletion settings, connection or confirmation changed. Click Delete again when ready.')
    incident, evidence = source(engine, request)
    if (incident['revision'] != request['latest_revision']
            or digest(evidence) != request['source_hash']
            or digest(incident['evidence']) != request['latest_hash']):
        raise actions.ActionError('Incident changed while preparing deletion. Click Delete again.')
    return incident, evidence


def request_delete(engine, identity, revision, actor, channel_id, message_id=None):
    if not re.fullmatch(r'[a-f0-9]{32}', identity) or not re.fullmatch(r'[1-9][0-9]{0,8}', str(revision)):
        raise actions.ActionError('Invalid incident or revision.')
    request = dict(incident_id=identity, revision=int(revision), actor=actor, channel_id=channel_id,
        policy_hash=engine.config.policy_hash, epoch=engine.store.get_setting('action_epoch', 0),
        coverage=engine.store.get_setting('coverage_gaps', 0), created_at=engine._now())
    incident, evidence = source(engine, request)
    selected = [item for item in evidence if message_id is None or item['message_id'] == message_id]
    if not selected or len(selected) > actions.MAX_TARGETS:
        raise actions.ActionError('Select one message: !mod delete ID REV MESSAGE_ID (maximum 8 per confirmation).')
    request.update(latest_revision=incident['revision'], latest_hash=digest(incident['evidence']),
                   source_hash=digest(evidence), evidence=selected, author_id=incident['author_id'])
    guard(engine, request)
    return DeleteRequest(request)


def checked_refresh(engine, request, events):
    incident, _ = guard(engine, request)
    if len(events) != len(request['evidence']):
        raise actions.ActionError('Not every selected message could be checked. No confirmation created.')
    fresh, changed = [], []
    for saved, event in zip(request['evidence'], events):
        for key in ('guild_id', 'channel_id', 'message_id', 'author_id', 'created_at'):
            if getattr(event, key) != saved[key]:
                raise actions.ActionError('Fetched message identity differs. No confirmation created.')
        item = {**event.to_dict(), 'version': event.version}
        validated_evidence(engine.config, incident, [item])
        fresh.append(item)
        if actions.message_changes(saved, event):
            changed.append(event.message_id)
    payload = {**request, 'kind': 'delete', 'automatic': False, 'refreshed': True,
               'evidence': fresh, 'evidence_hash': digest(fresh), 'changed_messages': changed,
               'refreshed_at': engine._now(), 'created_at': engine._now()}
    return payload


def revalidate(engine, payload):
    if payload['kind'] != 'delete' or payload['automatic'] is not False:
        raise actions.ActionError('Fresh staff evidence cannot authorize an automatic or account action.')
    incident, original = guard(engine, payload)
    validated_evidence(engine.config, incident, payload['evidence'])
    if digest(payload['evidence']) != payload['evidence_hash']:
        raise actions.ActionError('Confirmation evidence changed. Click Delete again.')
    refs = {(item['guild_id'], item['channel_id'], item['message_id'], item['author_id'], item['created_at']) for item in original}
    if any((item['guild_id'], item['channel_id'], item['message_id'], item['author_id'], item['created_at']) not in refs
           for item in payload['evidence']):
        raise actions.ActionError('Confirmation no longer matches the saved message references.')
    return incident


def confirmation(engine, payload):
    revalidate(engine, payload)
    items = payload['evidence']
    lines = ['CURRENT DISCORD MESSAGES', '-' * 32, f"Delete {len(items)} message(s).",
             'Fetched now for staff review.', 'Deletion cannot be undone.',
             'Only you can confirm. Expires in 60 seconds.', 'JEV result remains unchanged.', '',
             'Incident ID', payload['incident_id'], f"Source revision: {payload['revision']}"]
    base = panel('Confirm message deletion', lines)
    headings = []
    for index, item in enumerate(items, 1):
        url = f"https://discord.com/channels/{item['guild_id']}/{item['channel_id']}/{item['message_id']}"
        label = 'CHANGED since saved report' if item['message_id'] in payload['changed_messages'] else 'Matches saved text'
        headings.append(f"\n\n### Message {index} — {label}\n[Open message {index}](<{url}>)\n> ")
    footer = '\nText excerpts; open links for full messages. Confirm only the current content shown.'
    available = (1850 - units(base + footer + ''.join(headings)) - len(items)*5) // len(items)
    if available < 64:
        raise actions.ActionError('Select one message for a readable confirmation: !mod delete ID REV MESSAGE_ID')
    text = base
    for heading, item in zip(headings, items):
        preview, _ = excerpt(item['content'], min(available, 450))
        text += heading + (preview or '(empty text)') + '\n'
    text += footer
    if units(text) > 1900:
        raise actions.ActionError('Select one message for a readable confirmation: !mod delete ID REV MESSAGE_ID')
    return actions.store_proposal(engine, payload, text)
