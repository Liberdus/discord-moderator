"""Isolated review probes: no live JEV, Discord, profiles or secret access."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[2]
for directory in (ROOT, ROOT / 'tests', ROOT / 'integration_tests',
                  Path('/tmp/liberdus-hermes-c1488/NousResearch-hermes-agent-c1488ac')):
    sys.path.insert(0, str(directory))
from test_message_screening import ScreeningAdapterTests
from test_screening import config, response
from liberdus_moderator.engine import Engine
from liberdus_moderator.live import LiveSession
from liberdus_moderator.models import MessageEvent
from liberdus_moderator.screening import MessageScreener, CONCERN, FLAGGED
from liberdus_moderator.storage import Store
from liberdus_moderator.classifier import RUBRIC
from liberdus_moderator import actions


async def exemption_edit_repro():
    test = ScreeningAdapterTests(methodName='test_role_exemption_skips_provider_and_reports_but_not_repetition')
    await test.asyncSetUp()
    try:
        adapter = test.adapter
        await adapter.classifier.close()
        adapter.policy = replace(adapter.policy, classifier=replace(
            adapter.policy.classifier, exempt_role_ids=('1302455329795342377',)))
        adapter.live = LiveSession(Engine(adapter.policy, adapter.store))
        adapter.classifier = MessageScreener(adapter.live.engine, test.provider,
            active=lambda: adapter.online,
            on_result=lambda job: adapter.enqueue('screen_result', job))
        adapter.classifier.start()
        original = test.message(content='Initial exempt member message')
        original.author.roles = [SimpleNamespace(id=1), SimpleNamespace(id=1302455329795342377)]
        adapter.receive(original)
        await test.screened()
        before = test.provider.await_count
        edited = test.message(content='Edited exempt member message',
            created_at=original.created_at, edited_at=datetime.now(timezone.utc))
        # A REST fetch can return discord.User with no member-role field.
        if hasattr(edited.author, 'roles'):
            del edited.author.roles
        test.channel.fetch_message = AsyncMock(return_value=edited)
        adapter.edit(1, 10, 100)
        await test.screened()
        after = test.provider.await_count
        assert before == 0 and after == 1, (before, after)
        print(f'REPRODUCED exemption edit gap: mock JEV calls {before} -> {after}.')
    finally:
        await test.asyncTearDown()
        test.doCleanups()


async def eligibility_matrix():
    count = 0
    for concern in CONCERN['criteria']:
        for purpose in RUBRIC['criteria']:
            for confidence in (0.0, .89, .90, .90001, .99, 1.0):
                with Store(':memory:') as store:
                    engine = Engine(replace(config(), actions_enabled=True), store, clock=lambda: 1000.)
                    actions.initialize(engine)
                    store.set_setting('deletion_enabled', True)
                    store.set_setting('auto_delete_enabled', True)
                    event = MessageEvent('1', '10', '100', '50', 'This is isolated synthetic evidence.', 1000.)
                    engine.process(event)
                    raw = response(concern, purpose)
                    raw['answers']['concern']['confidence'] = confidence
                    worker = MessageScreener(engine, AsyncMock(return_value=raw))
                    await worker.evaluate_one(worker.snapshot('100'))
                    incidents = store.incidents()
                    assert bool(incidents) == (concern in FLAGGED), (concern, purpose, confidence, 'report')
                    actual = bool(incidents and actions.automatic_candidate(
                        engine, store.incident(incidents[0]['id'])))
                    expected = (concern == 'sensitive_request'
                        and purpose not in ('quoted_warning', 'unclear') and confidence > .90)
                    assert actual == expected, (concern, purpose, confidence, actual, expected)
                    count += 1
    print(f'PASS: {count} synthetic concern/purpose/score response-path combinations.')


async def main():
    await exemption_edit_repro()
    await eligibility_matrix()
    print('No live provider, Discord or installed runtime calls. Repository unchanged.')

if __name__ == '__main__':
    asyncio.run(main())
