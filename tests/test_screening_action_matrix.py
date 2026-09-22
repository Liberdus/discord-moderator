"""Typed-response policy boundaries; fixtures do not measure model accuracy."""
from dataclasses import replace
import unittest
from unittest.mock import AsyncMock

from test_screening import config, response
from liberdus_moderator import actions
from liberdus_moderator.classifier import RUBRIC
from liberdus_moderator.engine import Engine
from liberdus_moderator.models import MessageEvent
from liberdus_moderator.screening import MessageScreener, CONCERN, FLAGGED
from liberdus_moderator.storage import Store


class ScreeningActionMatrixTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_concerns_purposes_and_score_boundaries(self):
        for concern in CONCERN['criteria']:
            for purpose in RUBRIC['criteria']:
                for confidence in (0.0, .89, .89999, .90, .90001, .99, 1.0):
                    with self.subTest(concern=concern,purpose=purpose,confidence=confidence), Store(':memory:') as store:
                        engine=Engine(replace(config(),actions_enabled=True),store,clock=lambda:1000.)
                        actions.initialize(engine)
                        store.set_setting('deletion_enabled',True)
                        store.set_setting('auto_delete_enabled',True)
                        engine.process(MessageEvent('1','10','100','50','Synthetic review evidence.',1000.))
                        raw=response(concern,purpose)
                        raw['answers']['concern']['confidence']=confidence
                        provider=AsyncMock(return_value=raw)
                        worker=MessageScreener(engine,provider)
                        try:
                            await worker.evaluate_one(worker.snapshot('100'))
                            provider.assert_awaited_once()
                            incidents=store.incidents()
                            self.assertEqual(bool(incidents),concern in FLAGGED)
                            actual=bool(incidents and actions.automatic_candidate(engine,store.incident(incidents[0]['id'])))
                            expected=(concern in ('sensitive_request','impersonation','suspicious_offer','targeted_abuse')
                                      and confidence>=.90
                                      and purpose not in ('quoted_warning','unclear'))
                            self.assertEqual(actual,expected)
                        finally:
                            await worker.close()
