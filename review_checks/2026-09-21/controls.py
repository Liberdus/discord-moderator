"""Isolated regressions: all network methods mocked, no real profile loaded."""
import asyncio
import contextlib
import json
from test_hermes_adapter import AdapterTests
from test_actions import ActionAdapterTests

async def gap_probe():
    t = AdapterTests('test_busy_and_expired_review_clicks_do_not_write')
    await t.asyncSetUp()
    try:
        await t.create_report()
        t.adapter.worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t.adapter.worker
        click = t.interaction()
        await t.adapter.receive_review_click(click)
        before = t.adapter.queue.qsize()
        t.adapter.deleted(1, 10, (991,))
        print(json.dumps({'scenario': 'deferred review then external deletion',
            'queued_before': before, 'queued_after': t.adapter.queue.qsize(),
            'acknowledged': click.response.defer.await_count,
            'completed': click.followup.send.await_count, 'assessments': len(t.reviews())}))
        t.adapter.receive(t.message(811, 20, 98, '!mod pause'))
        before = t.adapter.queue.qsize()
        t.adapter.deleted(1, 10, (992,))
        t.adapter.worker = asyncio.create_task(t.adapter.run_worker())
        await t.drain()
        print(json.dumps({'scenario': 'pause then external deletion',
            'queued_before': before, 'paused_after_drain': t.adapter.store.get_setting('paused', False)}))
    finally:
        await t.asyncTearDown()

async def rate_probe(command, setting):
    t = ActionAdapterTests('test_off_command_arriving_during_delete_survives_coverage_reset')
    await t.asyncSetUp()
    try:
        if command != 'pause':
            t.toggle(command.split()[0])
        t.adapter.receive(t.message(810, 20, 98, '!mod status'))
        t.adapter.receive(t.message(811, 20, 98, '!mod ' + command))
        await t.drain()
        print(json.dumps({'scenario': 'status immediately followed by ' + command,
            'state_after_drain': t.adapter.store.get_setting(setting, False),
            'outgoing_replies': t.channel.send.await_count,
            'control_receipt_exists': bool(t.adapter.store.db.execute(
                'SELECT 1 FROM command_receipts WHERE message_id=?', ('811',)).fetchone())}))
    finally:
        await t.asyncTearDown()

async def main():
    await gap_probe()
    for command, setting in [('pause', 'paused'), ('deletion off', 'deletion_enabled'),
                             ('auto-delete off', 'auto_delete_enabled'), ('timeout off', 'timeout_enabled')]:
        await rate_probe(command, setting)

asyncio.run(main())
