import unittest
from dataclasses import replace
from unittest.mock import AsyncMock

from test_screening import config, response
from liberdus_moderator import actions, recovery
from liberdus_moderator.classification_view import incident_view, format_incident
from liberdus_moderator.engine import Engine
from liberdus_moderator.live import LiveSession
from liberdus_moderator.models import MessageEvent
from liberdus_moderator.screening import MessageScreener, SCREENING_HASH
from liberdus_moderator.storage import Store


class RecoveryEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(':memory:')
        self.addCleanup(self.store.close)
        self.now = 1000.
        self.engine = Engine(replace(config(), actions_enabled=True), self.store, clock=lambda: self.now)
        self.live = LiveSession(self.engine)
        self.live.gap('reconnect')
        recovery.initialize(self.engine)
        raw = response()
        raw['answers']['concern']['confidence'] = .99
        self.provider = AsyncMock(return_value=raw)
        self.worker = MessageScreener(self.engine, self.provider)
        self.event = MessageEvent('1', '10', '100', '50', 'Synthetic recovered content.', 999.)

    async def asyncTearDown(self):
        await self.worker.close()

    async def test_recovered_flag_has_real_evidence_and_can_never_auto_delete(self):
        self.assertEqual(self.engine.process(self.event)['reason'], 'before_current_coverage_window')
        self.assertTrue(recovery.stage(self.engine, self.event))
        await self.worker.evaluate_one(self.worker.snapshot('100'))
        incident = self.store.incident(self.store.incidents()[0]['id'])
        view = incident_view(self.engine, incident)
        self.assertTrue(view['evidence_view']['available'])
        self.assertEqual(view['classification']['evidence_state'], 'current')
        self.assertIn('Recovered after disconnect', format_incident(view))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM messages').fetchone()[0], 0)
        self.store.set_setting('deletion_enabled', True)
        self.store.set_setting('auto_delete_enabled', True)
        self.assertFalse(actions.automatic_candidate(self.engine, incident))
        self.assertIsNotNone(self.live.claim_report())
        from liberdus_moderator.manual_delete import request_delete
        self.assertEqual(request_delete(self.engine, incident['id'], 1, '98', '20').request['incident_id'], incident['id'])

    async def test_disconnect_pause_and_edits_invalidate_recovered_evidence(self):
        for reason in ('disconnect', 'pause', 'edit'):
            with self.subTest(reason=reason):
                self.engine.set_paused(False)
                recovery.stage(self.engine, self.event)
                job = self.worker.snapshot('100')
                self.assertIsNotNone(job)
                if reason == 'pause':
                    self.engine.set_paused(True)
                elif reason == 'edit':
                    recovery.invalidate(self.engine, '100')
                else:
                    self.live.gap('disconnect')  # Even if the clock has not advanced.
                await self.worker.evaluate_one(job)
                self.assertIsNone(recovery.evidence(self.engine, '100'))
        self.provider.assert_not_awaited()

    async def test_content_ledger_covers_live_attempts_despite_rest_role_differences(self):
        event = replace(self.event, created_at=self.now, author_role_ids=('1', '55'))
        self.engine.process(event)
        await self.worker.evaluate_one(self.worker.snapshot('100'))
        rest = replace(event, author_role_ids=())
        self.assertNotEqual(event.version, rest.version)
        self.assertEqual(recovery.content_key(event, self.engine.config.policy_hash, SCREENING_HASH),
                         recovery.content_key(rest, self.engine.config.policy_hash, SCREENING_HASH))
        row = self.store.db.execute('SELECT a.outcome FROM screening_versions_v1 v JOIN screening_attempts_v1 a '
                                   'ON a.key=v.attempt_key WHERE v.fingerprint=?',
                                   (recovery.content_key(rest, self.engine.config.policy_hash, SCREENING_HASH),)).fetchone()
        self.assertEqual(row['outcome'], 'ok')
        self.assertNotEqual(recovery.content_key(rest, 'changed-policy', SCREENING_HASH),
                            recovery.content_key(rest, self.engine.config.policy_hash, SCREENING_HASH))

    async def test_budget_stops_recovered_provider_calls(self):
        recovery.stage(self.engine, self.event)
        self.store.set_setting('screening_total_calls', self.engine.config.classifier.max_total_calls)
        await self.worker.evaluate_one(self.worker.snapshot('100'))
        self.provider.assert_not_awaited()
        self.assertEqual(self.store.get_setting('screening_state'), 'budget_exhausted')

    def test_recovery_rejects_unsupported_evidence(self):
        for event in (replace(self.event, is_bot=True), replace(self.event, has_attachments=True),
                      replace(self.event, channel_id='20'), replace(self.event, guild_id='2')):
            recovery.stage(self.engine, event)
            self.assertIsNone(recovery.evidence(self.engine, '100'))

    def test_old_recovery_evidence_expires_and_is_pruned_with_live_retention(self):
        recovery.stage(self.engine, self.event)
        self.now += 601
        self.assertIsNone(recovery.evidence(self.engine, '100'))
        self.now += self.engine.config.storage.retention_seconds
        self.engine.process(replace(self.event, message_id='101', created_at=self.now))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM catchup_evidence_v1').fetchone()[0], 0)
