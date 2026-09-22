import asyncio
import unittest
from unittest.mock import AsyncMock

from liberdus_moderator.classifier import RESERVED_MICROUSD
from liberdus_moderator.commands import CommandRequest
from liberdus_moderator.engine import Engine
from liberdus_moderator.jev import ProviderError
from liberdus_moderator.live import LiveSession
from liberdus_moderator.models import MessageEvent
from liberdus_moderator.screening import MessageScreener
from liberdus_moderator.screening_diagnostics import DIAGNOSTICS, live_failure_lines
from liberdus_moderator.storage import Store
from test_screening import config, response


class LiveDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(':memory:')
        self.addCleanup(self.store.close)
        self.now = 1000.
        self.engine = Engine(config(), self.store, clock=lambda: self.now)
        self.live = LiveSession(self.engine)
        self.provider = AsyncMock(return_value=response())
        self.worker = MessageScreener(self.engine, self.provider)

    async def asyncTearDown(self):
        await self.worker.close()

    def job(self, identity='100'):
        self.engine.process(MessageEvent('1', '10', identity, '50', 'Synthetic screening test.', self.now))
        return self.worker.snapshot(identity)

    def diagnostic(self, job):
        row = self.store.db.execute('SELECT diagnostic FROM screening_failures_v1 WHERE attempt_key=?', (job.key,)).fetchone()
        return row[0] if row else None

    async def test_invalid_response_keeps_exact_reason_without_payload_or_retry(self):
        raw = response()
        raw['answers']['concern']['probabilities']['none'] = .3
        raw['private_debug'] = 'secret-response-sentinel'
        self.provider.return_value = raw
        job = self.job()
        await self.worker.evaluate_one(job)
        await self.worker.evaluate_one(job)
        self.provider.assert_awaited_once()
        self.assertEqual(self.diagnostic(job), 'concern.probability_sum')
        self.assertEqual(self.store.get_setting('screening_state'), 'provider_or_response_error')
        self.assertEqual(self.store.get_setting('screening_total_reserved_microusd'), RESERVED_MICROUSD)
        self.assertFalse(self.store.incidents())
        self.assertNotIn('secret-response-sentinel', '\n'.join(self.store.db.iterdump()))

    async def test_request_errors_only_retain_fixed_codes(self):
        for index, (error, code) in enumerate((
            (OSError('secret-network-sentinel'), 'network.io'),
            (ProviderError('rate_limited'), 'provider.rate_limited'),
            (RuntimeError('secret-internal-sentinel'), 'internal.request'),
            (TimeoutError('secret-timeout-sentinel'), 'request.timeout'),
        )):
            self.now += 2
            self.provider.side_effect = error
            job = self.job(str(200+index))
            await self.worker.evaluate_one(job)
            self.assertEqual(self.diagnostic(job), code)
        dump = '\n'.join(self.store.db.iterdump())
        self.assertNotIn('sentinel', dump)
        self.assertEqual(self.store.get_setting('screening_total_reserved_microusd'), 4 * RESERVED_MICROUSD)

    async def test_interrupted_request_preserves_cancel_and_reservation(self):
        self.provider.side_effect = asyncio.CancelledError()
        job = self.job()
        with self.assertRaises(asyncio.CancelledError):
            await self.worker.evaluate_one(job)
        self.assertEqual(self.diagnostic(job), 'request.interrupted')
        self.assertEqual(self.store.get_setting('screening_total_reserved_microusd'), RESERVED_MICROUSD)

    async def test_failure_survives_restart_success_and_shows_in_authorized_status(self):
        self.provider.return_value = {}
        job = self.job()
        await self.worker.evaluate_one(job)
        restarted = MessageScreener(self.engine, self.provider)
        await restarted.close()
        self.assertEqual(self.diagnostic(job), 'response.model')
        self.now += 2
        self.provider.return_value = response()
        await self.worker.evaluate_one(self.job('101'))
        self.assertEqual(self.store.get_setting('screening_state'), 'ok')
        self.assertEqual(len(self.store.incidents()), 1)
        text = self.live.command(CommandRequest('1', '20', '98', 'status'), '300', True)
        self.assertIn('State: ok', text)
        self.assertIn('LAST FAILED AI CHECK', text)
        self.assertIn('response.model', text)
        self.assertIn('### Actions', text)
        self.assertLess(len(text), 1950)
        self.assertIsNone(self.live.command(CommandRequest('1', '10', '50', 'status'), '301', True))

    async def test_legacy_failure_is_unknown_and_new_success_does_not_invent_a_cause(self):
        self.provider.return_value = {}
        await self.worker.evaluate_one(self.job())
        self.store.db.execute('DROP TABLE screening_failures_v1')
        lines = live_failure_lines(self.store)
        self.assertIn('Details were not recorded.', lines)
        self.assertFalse(any(code in '\n'.join(lines) for code in DIAGNOSTICS))

    async def test_pruning_attempt_removes_diagnostic(self):
        self.provider.return_value = {}
        job = self.job()
        await self.worker.evaluate_one(job)
        self.store.db.execute('DELETE FROM screening_attempts_v1 WHERE key=?', (job.key,))
        self.assertIsNone(self.diagnostic(job))
        self.assertEqual(live_failure_lines(self.store), [])

    async def test_unknown_saved_code_is_never_displayed(self):
        self.provider.return_value = {}
        await self.worker.evaluate_one(self.job())
        self.store.db.execute("UPDATE screening_failures_v1 SET diagnostic='secret-untrusted-sentinel'")
        lines = live_failure_lines(self.store)
        self.assertIn('Details were not recorded.', lines)
        self.assertNotIn('sentinel', '\n'.join(lines))
