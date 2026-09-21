import asyncio
from dataclasses import replace
import hashlib
import json
import unittest
from unittest.mock import AsyncMock

from liberdus_moderator.classifier import RUBRIC, RESERVED_MICROUSD
from liberdus_moderator.config import Config, ClassifierSettings
from liberdus_moderator.engine import Engine
from liberdus_moderator.live import LiveSession
from liberdus_moderator.models import MessageEvent
from liberdus_moderator.storage import Store
from liberdus_moderator.screening import (MessageScreener, CONCERN, FLAGGED, SCREENING_HASH,
                                        validate_screening, saved_screening)
from liberdus_moderator.classification_view import incident_view, format_incident
from liberdus_moderator.staff_review import record_assessment, pending_page


def config(**changes):
    settings = dict(mode='report_only', max_daily_calls=10000, max_total_calls=100000,
                    daily_budget_microusd=1000000, total_budget_microusd=4000000,
                    min_interval_seconds=1, queue_capacity=100)
    settings.update(changes)
    return Config('1', '99', ('10', '11', '12'), ('20',), ('98',), schema_version=2,
                  ai_enabled=True, classifier=ClassifierSettings(**settings))


def response(concern='sensitive_request', purpose='promotion', tokens=500):
    def answer(choice, rubric):
        return dict(type='choice', choice=choice, confidence=.9,
                    probabilities={key: 1. if key == choice else 0. for key in rubric['criteria']})
    return dict(model='jev-1.13.0', answers=dict(context=answer(purpose, RUBRIC), concern=answer(concern, CONCERN)),
                usage=dict(input_tokens=tokens, output_tokens=50))


class ScreeningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 1000.
        self.store = Store(':memory:')
        self.addCleanup(self.store.close)
        self.engine = Engine(config(), self.store, clock=lambda: self.now)
        self.live = LiveSession(self.engine)
        self.provider = AsyncMock(return_value=response())
        self.active = True
        self.worker = MessageScreener(self.engine, self.provider, active=lambda: self.active)

    async def asyncTearDown(self):
        await self.worker.close()

    def event(self, identity='100', text='Send your seed phrase to claim the reward.', **changes):
        values = dict(guild_id='1', channel_id='10', message_id=identity, author_id='50',
                      content=text, created_at=self.now)
        values.update(changes)
        return MessageEvent(**values)

    def prepare(self, event=None):
        event = event or self.event()
        self.engine.process(event)
        return self.worker.snapshot(event.message_id)

    def attempt(self, job):
        return dict(self.store.db.execute('SELECT * FROM screening_attempts_v1 WHERE key=?', (job.key,)).fetchone())

    async def test_one_message_private_incident_view_and_staff_completion(self):
        job = self.prepare()
        self.assertFalse(self.store.incidents())
        await self.worker.evaluate_one(job)
        self.assertEqual(self.attempt(job)['outcome'], 'ok')
        incident = self.store.incidents()[0]
        self.assertEqual(incident['rule_id'], 'jev_message')
        self.assertEqual(len(incident['evidence']), 1)
        self.assertEqual(self.store.reports()[0]['payload']['channel_id'], '20')
        view = incident_view(self.engine, self.store.incident(incident['id']))
        self.assertEqual(view['classification']['choice'], 'sensitive_request')
        self.assertEqual(view['classification']['evidence_state'], 'current')
        rendered = format_incident(view)
        self.assertIn('JEV screening', rendered)
        self.assertIn('https://discord.com/channels/1/10/100', rendered)
        self.assertLessEqual(len(rendered.encode('utf-16-le')) // 2, 1900)
        self.assertIsNotNone(self.live.claim_report())
        self.assertEqual(self.store.get_setting('screening_checked'), 1)
        self.assertEqual(self.store.get_setting('screening_flagged'), 1)
        self.assertEqual(self.store.get_setting('screening_total_reserved_microusd'), 21)
        self.assertEqual(self.store.get_setting('classifier_total_calls', 0), 0)
        record_assessment(self.engine, incident['id'], '1', 'looks_okay', '98')
        self.assertTrue(incident_view(self.engine, self.store.incident(incident['id']))['staff_assessment']['complete'])
        self.assertEqual(pending_page(self.engine)['total'], 0)

    async def test_all_concern_choices_route_only_explicit_flags(self):
        for i, concern in enumerate(CONCERN['criteria']):
            self.now += 2
            job = self.prepare(self.event(str(200+i)))
            self.provider.return_value = response(concern)
            before = len(self.store.incidents())
            await self.worker.evaluate_one(job)
            self.assertEqual(len(self.store.incidents()) - before, int(concern in FLAGGED))
        self.assertEqual(self.store.get_setting('screening_checked'), len(CONCERN['criteria']))

    async def test_plain_promotion_warning_conversation_are_not_violation_labels(self):
        for i, purpose in enumerate(('promotion', 'quoted_warning', 'other', 'announcement')):
            self.now += 2
            self.provider.return_value = response('none', purpose)
            await self.worker.evaluate_one(self.prepare(self.event(str(300+i), text='Unique benign example '+purpose)))
        self.assertFalse(self.store.incidents())

    async def test_duplicate_version_not_billed_or_reported_twice_across_restart(self):
        job = self.prepare()
        await self.worker.evaluate_one(job)
        self.now += 2
        await self.worker.evaluate_one(job)
        restarted = MessageScreener(self.engine, self.provider)
        await restarted.evaluate_one(job)
        self.provider.assert_awaited_once()
        self.assertEqual(len(self.store.incidents()), 1)
        self.worker.apply(job)
        self.assertEqual(self.store.get_setting('screening_flagged'), 1)

    async def test_edit_retracts_only_its_incident_and_gets_new_evaluation(self):
        event = self.event()
        job = self.prepare(event)
        await self.worker.evaluate_one(job)
        identity = self.store.incidents()[0]['id']
        self.now += 2
        self.engine.process(self.event('101', text='Unrelated ordinary conversation.'))
        self.assertEqual(self.store.incident(identity)['status'], 'open')
        self.engine.process(replace(event, content='Never share your seed phrase.', edited_at=self.now))
        self.assertEqual(self.store.incident(identity)['status'], 'withdrawn')
        self.provider.return_value = response('none', 'quoted_warning')
        await self.worker.evaluate_one(self.worker.snapshot('100'))
        self.assertEqual(self.provider.await_count, 2)
        self.assertFalse(self.store.reports())
        self.assertEqual(saved_screening(self.engine, self.store.incident(identity))['evidence_state'], 'historical')

    async def test_inflight_edit_pause_disconnect_or_policy_change_never_alerts(self):
        for kind in ('edit', 'pause', 'gap', 'flag'):
            with self.subTest(kind=kind):
                self.now += 2
                self.engine.set_paused(False)
                self.active = True
                event = self.event(str(400+len(self.store.incidents())+int(self.now)))
                job = self.prepare(event)
                async def mutate(payload):
                    if kind == 'edit':
                        self.engine.process(replace(event, content='Correction: never share keys.', edited_at=self.now+0.1))
                    elif kind == 'pause': self.engine.set_paused(True)
                    elif kind == 'gap': self.live.gap('deleted_message')
                    else: self.active = False
                    return response()
                self.worker.evaluator = mutate
                await self.worker.evaluate_one(job)
                self.assertEqual(self.attempt(job)['outcome'], 'stale')
                self.assertFalse(self.store.incidents())
                self.assertEqual(self.attempt(job)['result_json'] is not None, True)

    async def test_apply_is_deferred_and_revalidates_after_queued_edit(self):
        jobs = []
        self.worker.on_result = jobs.append
        event = self.event()
        await self.worker.evaluate_one(self.prepare(event))
        self.assertFalse(self.store.incidents())
        self.assertEqual(self.attempt(jobs[0])['outcome'], 'awaiting_apply')
        self.now += 2
        self.engine.process(replace(event, edited_at=self.now, content='This was a warning, do not share keys.'))
        self.worker.apply(jobs[0])
        self.assertFalse(self.store.incidents())
        self.assertEqual(self.attempt(jobs[0])['outcome'], 'stale')

    async def test_boundaries_no_provider_calls_for_excluded_inputs(self):
        for changes in ({'guild_id':'2'}, {'channel_id':'20'}, {'is_bot':True}, {'is_webhook':True},
                        {'is_thread':True}, {'has_attachments':True}, {'author_id':'99'}, {'content':'  '}):
            event = replace(self.event(), **changes)
            self.engine.process(event)
            self.assertIsNone(self.worker.snapshot(event.message_id), changes)
        self.provider.assert_not_awaited()

    async def test_payload_contains_text_and_urls_not_discord_metadata_or_secret(self):
        job = self.prepare(self.event(text='Hello <@977263877391794217> see https://example.invalid/path'))
        payload = json.loads(job.payload)
        self.assertEqual(payload['state']['urls'], ['https://example.invalid/path'])
        self.assertNotIn('977263877391794217', job.payload.decode())
        self.assertNotIn('author_id', payload['state'])
        self.assertEqual(set(payload['questions']), {'context', 'concern'})
        await self.worker.evaluate_one(job)
        self.provider.assert_awaited_once()

    async def test_malformed_response_and_failed_requests_hold_reservation_no_retry(self):
        for i, raw in enumerate(({}, response(tokens=65537))):
            self.now += 2
            self.provider.return_value = raw
            job = self.prepare(self.event(str(500+i)))
            await self.worker.evaluate_one(job)
            await self.worker.evaluate_one(job)
            self.assertFalse(self.store.incidents())
        self.assertEqual(self.provider.await_count, 2)
        self.assertEqual(self.store.get_setting('screening_total_reserved_microusd'), RESERVED_MICROUSD*2)
        self.assertTrue(self.store.get_setting('screening_billing_guard'))

    async def test_timeout_and_cancel_keep_unknown_charges(self):
        self.provider.side_effect = TimeoutError()
        job = self.prepare()
        await self.worker.evaluate_one(job)
        self.assertEqual(self.attempt(job)['outcome'], 'timeout')
        self.assertEqual(self.store.get_setting('screening_total_reserved_microusd'), RESERVED_MICROUSD)
        self.now += 2
        self.provider.side_effect = asyncio.CancelledError()
        job2 = self.prepare(self.event('101'))
        with self.assertRaises(asyncio.CancelledError): await self.worker.evaluate_one(job2)
        self.assertEqual(self.attempt(job2)['outcome'], 'uncertain')
        self.assertEqual(self.store.get_setting('screening_total_reserved_microusd'), RESERVED_MICROUSD*2)

    async def test_daily_and_total_limits_do_not_touch_shadow_counters(self):
        self.store.set_setting('classifier_total_calls', 18)
        self.store.set_setting('classifier_total_reserved_microusd', 49554)
        for prefix, limit in (('daily', 1000000), ('total', 4000000)):
            self.store.set_setting('screening_'+prefix+'_reserved_microusd', limit-RESERVED_MICROUSD+1)
            await self.worker.evaluate_one(self.prepare())
            self.assertEqual(self.store.get_setting('screening_state'), 'budget_exhausted')
            self.store.set_setting('screening_'+prefix+'_reserved_microusd', 0)
        self.provider.assert_not_awaited()
        self.assertEqual(self.store.get_setting('classifier_total_calls'), 18)
        self.assertEqual(self.store.get_setting('classifier_total_reserved_microusd'), 49554)

    async def test_settlement_once_and_next_day_accounting(self):
        job = self.prepare()
        async def next_day(payload):
            self.now += 86400
            self.store.set_setting('screening_budget_day', int(self.now//86400))
            self.store.set_setting('screening_daily_reserved_microusd', 123)
            return response()
        self.worker.evaluator = next_day
        await self.worker.evaluate_one(job)
        self.assertEqual(self.store.get_setting('screening_total_reserved_microusd'), 21)
        self.assertEqual(self.store.get_setting('screening_daily_reserved_microusd'), 123)
        self.worker.finish(job, 'awaiting_apply', 0, response())
        self.assertEqual(self.store.get_setting('screening_total_reserved_microusd'), 21)

    async def test_restart_abandons_unapplied_results_without_new_charge(self):
        self.worker.on_result = lambda job: None
        job = self.prepare()
        await self.worker.evaluate_one(job)
        MessageScreener(self.engine, self.provider)
        self.assertEqual(self.attempt(job)['outcome'], 'uncertain')
        self.worker.apply(job)
        self.assertFalse(self.store.incidents())
        self.provider.assert_awaited_once()

    async def test_long_input_queue_full_and_capacity_visible(self):
        self.worker.settings = replace(self.worker.settings, max_request_bytes=2048)
        self.prepare()
        self.worker.submit(['100'])
        self.assertEqual(self.store.get_setting('screening_state'), 'input_limit')
        self.worker.settings = self.engine.config.classifier
        self.worker.queue = asyncio.Queue(maxsize=1)
        self.worker.submit(['100'])
        self.prepare(self.event('101'))
        self.worker.submit(['101'])
        self.assertEqual(self.store.get_setting('screening_state'), 'queue_full')
        self.assertGreaterEqual(self.store.get_setting('screening_unchecked'), 2)

    async def test_code_rules_continue_when_provider_budget_exhausted(self):
        self.store.set_setting('screening_total_reserved_microusd', 4000000)
        for i, channel in enumerate(('10','11','12')):
            job = self.prepare(self.event(str(600+i), channel_id=channel))
            await self.worker.evaluate_one(job)
        self.provider.assert_not_awaited()
        self.assertEqual(self.store.incidents()[0]['rule_id'], 'cross_channel_repeat')
        self.assertIsNotNone(self.live.claim_report())

    async def test_auth_failure_stops_repeated_requests_until_restart(self):
        from liberdus_moderator.jev import ProviderError
        self.provider.side_effect = ProviderError("authentication_failed")
        await self.worker.evaluate_one(self.prepare())
        self.now += 2
        self.prepare(self.event("102"))
        self.worker.submit(["102"])
        self.provider.assert_awaited_once()
        self.assertTrue(self.worker.provider_blocked)
        self.assertEqual(self.store.get_setting("screening_state"), "authentication_failed")

    async def test_claim_pending_report_is_cancelled_after_delete_or_disable(self):
        await self.worker.evaluate_one(self.prepare())
        self.live.gap("deleted_message")
        self.assertIsNone(self.live.claim_report())
        self.assertEqual(self.store.incidents()[0]["status"], "needs_revalidation")

    async def test_retention_capacity_and_clock_rollback_never_bypass_caps(self):
        job = self.prepare()
        self.store.set_setting("screening_budget_day", 1)
        await self.worker.evaluate_one(job)
        self.assertEqual(self.store.get_setting("screening_state"), "clock_rollback")
        self.provider.assert_not_awaited()
        self.store.set_setting("screening_budget_day", 0)
        self.provider.side_effect = TimeoutError()
        await self.worker.evaluate_one(job)
        self.now += self.engine.config.storage.retention_seconds + 2
        self.provider.side_effect = None
        self.provider.return_value = response("none")
        await self.worker.evaluate_one(self.prepare(self.event("103")))
        self.assertIsNone(self.store.db.execute("SELECT * FROM screening_attempts_v1 WHERE key=?", (job.key,)).fetchone())
        self.assertEqual(self.store.get_setting("screening_total_calls"), 2)
        self.assertEqual(self.store.get_setting("screening_total_reserved_microusd"), RESERVED_MICROUSD+21)

    async def test_exempt_role_blocks_screening_even_with_direct_worker_submission(self):
        conf = config(exempt_role_ids=("1302455329795342377",))
        self.engine = Engine(conf, self.store, clock=lambda: self.now)
        self.worker = MessageScreener(self.engine, self.provider)
        for i, channel in enumerate(("10", "11", "12")):
            self.engine.process(self.event(str(800+i), channel_id=channel,
                                           author_role_ids=("1302455329795342377",)))
            self.assertIsNone(self.worker.snapshot(str(800+i)))
        self.provider.assert_not_awaited()
        self.assertEqual(self.store.get_setting("screening_total_calls", 0), 0)
        self.assertEqual(self.store.incidents()[0]["rule_id"], "cross_channel_repeat")
        self.now += 2
        self.engine.process(self.event("900", author_role_ids=("88",)))
        await self.worker.evaluate_one(self.worker.snapshot("900"))
        self.provider.assert_awaited_once()

    def test_empty_exemption_preserves_existing_policy_hash_and_invalid_roles_rejected(self):
        from dataclasses import asdict
        original = config()
        data = asdict(original)
        del data["classifier"]["exempt_role_ids"]
        expected = hashlib.sha256(json.dumps(data,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(original.policy_hash, expected)
        for roles in (("not-a-role",), ("0",), ("88","88"), "88"):
            with self.assertRaises(ValueError): config(exempt_role_ids=roles)

    def test_all_typed_answer_fields_are_validated(self):
        for label in CONCERN['criteria']:
            self.assertEqual(validate_screening(response(label))['concern'], label)
        for field, value in (('confidence', True), ('confidence', float('nan')), ('choice','delete'),
                             ('probabilities', {'none':1}), ('type','score')):
            raw = response(); raw['answers']['concern'][field] = value
            with self.assertRaises(ValueError): validate_screening(raw)
