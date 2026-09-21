"""Offline review probes. Uses isolated temporary profiles and mocked Discord HTTP.

Run from repository root with the pinned Hermes runtime:
PYTHONPATH=/tmp/liberdus-hermes-c1488/NousResearch-hermes-agent-c1488ac:integration_tests:. \
  /tmp/liberdus-package-check-311/bin/python -B review_checks/2026-09-21/actions.py

Assertions preserve observed 0.5.3 behavior, including reproduced defects. The
expected/observed records below distinguish a passing capability from a defect.
No live profile, credentials, Discord or JEV endpoints are accessed.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from unittest.mock import Mock, AsyncMock

import discord
from test_actions import ActionAdapterTests
from liberdus_moderator import actions


def sdk_roles(case, state):
    roles = {}
    for identity, position in ((1, 0), (2, 1), (3, 5)):
        roles[identity] = discord.Role(guild=case.guild, state=state, data=dict(
            id=str(identity), name=str(identity), permissions='0', position=position,
            color=0, hoist=False, mentionable=False))
    case.guild.default_role = roles[1]
    case.guild.get_role = roles.get
    case.me.top_role = roles[3]
    return roles


def sdk_member(case, state, role_ids):
    state.self_id = 99
    state.store_user.side_effect = lambda data, **kw: discord.User(state=state, data=data)
    data = dict(user=dict(id='50', username='ordinary', discriminator='0', avatar=None),
        roles=list(map(str, role_ids)), joined_at='2026-09-21T00:00:00+00:00',
        deaf=False, mute=False, flags=0)
    member = discord.Member(data=data, guild=case.guild, state=state)
    case.guild.fetch_member.return_value = member
    return member, data


async def with_case(probe):
    case = ActionAdapterTests('test_timeout_is_ten_minutes_staff_only_and_never_shortens_existing')
    await case.asyncSetUp()
    try:
        return await probe(case)
    finally:
        await case.asyncTearDown()


async def real_timeout(case):
    incident = case.pattern()
    payload = case.plan(incident, 'timeout')
    state = Mock()
    sdk_roles(case, state)
    member, data = sdk_member(case, state, (2,))
    state.http.edit_member = AsyncMock(return_value=data)
    before = datetime.now(timezone.utc)
    result = await case.adapter.perform_action(payload)
    call = state.http.edit_member.call_args
    assert state.http.edit_member.await_count == 1
    assert call.args == (1, 50)
    until = datetime.fromisoformat(call.kwargs['communication_disabled_until'])
    assert before + timedelta(seconds=599) <= until <= datetime.now(timezone.utc) + timedelta(seconds=601)
    assert f"Liberdus incident {incident['id']}; staff 98; 10m" == call.kwargs['reason']
    assert actions.history(case.adapter.live.engine, incident['id'])[0]['outcome'] == 'done'
    assert 'Timeout: done' in result
    await case.adapter.perform_action(payload)
    assert state.http.edit_member.await_count == 1
    return dict(probe='real_sdk_timeout', expected='One 10-minute HTTP mutation with audit reason; duplicate blocked',
                observed='Matches expected', verdict='PASS')


def omitted_permission(permission):
    async def probe(case):
        incident = case.pattern()
        payload = case.plan(incident, 'timeout')
        setattr(case.member.guild_permissions, permission, True)
        await case.adapter.perform_action(payload)
        assert case.member.timeout.await_count == 1
        return dict(probe='staff_protection_' + permission, expected='Administrative/moderation permission blocks timeout',
                    observed='Member.timeout called once', verdict='DEFECT')
    return probe


async def delete_then_timeout(case):
    incident = case.pattern()
    await case.adapter.perform_action(case.plan(incident))
    latest = case.adapter.store.incident(incident['id'])
    case.toggle('timeout')
    try:
        actions.plan(case.adapter.live.engine, incident['id'], latest['revision'], 'timeout', case.operator, '20')
    except actions.ActionError as error:
        assert latest['status'] == 'needs_revalidation'
        assert 'Evidence changed' in str(error)
    else:
        raise AssertionError('Expected current conservative evidence guard to refuse timeout')
    case.member.timeout.assert_not_awaited()
    return dict(probe='delete_then_timeout', expected='Current design must refuse historical evidence; a combined workflow is unavailable',
                observed='Delete invalidates incident; timeout refused even at latest revision', verdict='WORKFLOW_LIMIT')


async def unresolved_role(case):
    state = Mock()
    sdk_roles(case, state)
    member, _ = sdk_member(case, state, (77,))
    # A fresh REST member response can precede the role-cache update. Member.roles
    # resolves IDs through guild.get_role and silently drops unknown role 77.
    assert list(member._roles) == [77]
    assert [role.id for role in member.roles] == [1]
    message = await case.missing_roles_screening()
    assert case.adapter.policy.classifier.exempt_role_ids == ('77',)
    assert case.adapter.store.get_setting('role_exemption_enabled') is True
    assert message.delete.await_count == 1
    return dict(probe='unresolved_exempt_role', expected='Fresh membership containing exempt role 77 blocks auto-delete',
                observed='SDK member.roles omits uncached 77; message deleted once', verdict='DEFECT')


async def main():
    probes = [real_timeout, *(omitted_permission(p) for p in ('kick_members', 'ban_members', 'manage_roles')),
              delete_then_timeout, unresolved_role]
    results = [await with_case(probe) for probe in probes]
    print(json.dumps(dict(source_version='0.5.3', offline_only=True, probes=results), indent=2))


if __name__ == '__main__':
    asyncio.run(main())
