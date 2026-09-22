from dataclasses import replace
import unittest
from unittest.mock import AsyncMock

from test_screening import config, response
from liberdus_moderator.engine import Engine
from liberdus_moderator.live import LiveSession
from liberdus_moderator.models import MessageEvent
from liberdus_moderator.review_alerts import eligible_role, reserve, CONCERNS
from liberdus_moderator.screening import MessageScreener
from liberdus_moderator.storage import Store


class ReviewAlertTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_screening_score_boundaries_and_stale_evidence(self):
        for concern in (*CONCERNS, "none"):
            for score in (0.0, .49, .89999, .90, 1.0):
                with self.subTest(concern=concern, score=score), Store(':memory:') as store:
                    policy = config()
                    policy = replace(policy, rules=replace(policy.rules, review_alert_role_id="5"))
                    engine = Engine(policy, store, clock=lambda: 1000.)
                    live = LiveSession(engine)
                    raw = response(concern)
                    raw['answers']['concern']['confidence'] = score
                    worker = MessageScreener(engine, AsyncMock(return_value=raw))
                    try:
                        engine.process(MessageEvent('1', '10', '100', '50', 'Synthetic flagged evidence.', 1000.))
                        await worker.evaluate_one(worker.snapshot('100'))
                        report = live.claim_report()
                        if concern == 'none':
                            self.assertIsNone(report)
                            continue
                        self.assertEqual(eligible_role(engine, report), '5' if score < .9 else None)
                        store.db.execute("UPDATE incidents SET status='needs_revalidation'")
                        self.assertIsNone(eligible_role(engine, report))
                    finally:
                        await worker.close()

    def test_cooldown_is_durable_and_clock_rollback_does_not_repeat_ping(self):
        with Store(':memory:') as store:
            now = [1000.]
            engine = Engine(config(), store, clock=lambda: now[0])
            self.assertTrue(reserve(engine))
            self.assertFalse(reserve(engine))
            now[0] = 999.
            self.assertFalse(reserve(engine))
            now[0] = 1299.
            self.assertFalse(reserve(engine))
            now[0] = 1300.
            self.assertTrue(reserve(engine))
