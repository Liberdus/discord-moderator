import asyncio
import copy
from dataclasses import asdict, replace
import hashlib
import json
import unittest
from unittest.mock import AsyncMock

from liberdus_moderator.classifier import ShadowClassifier, RUBRIC, validate_response
from liberdus_moderator.config import Config, ClassifierSettings
from liberdus_moderator.configure_jev import policy_text
from liberdus_moderator.engine import Engine
from liberdus_moderator.live import LiveSession
from liberdus_moderator.models import MessageEvent
from liberdus_moderator.storage import Store


def response(choice="promotion", **changes):
    result = {"model": "jev-1.13.0", "answers": {"context": {"type": "choice", "choice": choice,
        "confidence": 0.8, "probabilities": {key: 0.8 if key == choice else 0.05 for key in RUBRIC["criteria"]}}},
        "usage": {"input_tokens": 500, "output_tokens": 40}}
    result.update(changes)
    return result


def shadow_config(**settings):
    limits = dict(mode="shadow", max_daily_calls=100, max_total_calls=1000,
                  daily_budget_microusd=50000, total_budget_microusd=250000)
    limits.update(settings)
    return Config("1", "99", ("10", "11", "12"), ("20",), ("98",), schema_version=2,
                  ai_enabled=True, classifier=ClassifierSettings(**limits))


class ClassifierConfigTests(unittest.TestCase):
    def test_explicit_migration_and_off_gate(self):
        original = Config("1", "99", ("10", "11", "12"), ("20",), ("98",))
        old_data = asdict(original)
        del old_data["classifier"]
        old_hash = hashlib.sha256(json.dumps(old_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(original.policy_hash, old_hash)
        for change in ({"ai_enabled": True}, {"actions_enabled": True},
                       {"classifier": ClassifierSettings(max_daily_calls=1)}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(original, **change)
        import tomllib
        upgraded = replace(original, schema_version=2)
        self.assertEqual(Config.from_dict(tomllib.loads(policy_text(upgraded))), upgraded)
        for changes in ({"mode": "report_only"}, {"model": "jev-latest"}, {"max_daily_calls": True},
                        {"timeout_seconds": 0}, {"queue_capacity": 1001}, {"daily_budget_microusd": -1},
                        {"mode": "shadow"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ClassifierSettings(**changes)
        for changes in ({"ai_enabled": False}, {"mode": "off"}, {"actions_enabled": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(shadow_config(), **changes)

    def test_typed_response_rejects_malformed_numbers_model_and_unknown_choices(self):
        for choice in RUBRIC["criteria"]:
            self.assertEqual(validate_response(response(choice))["choice"], choice)
        invalid = []
        for value in (float("nan"), float("inf"), -1, True, "0.8"):
            r = response()
            r["answers"]["context"]["confidence"] = value
            invalid.append(r)
        for key, value in (("choice", "ban"), ("type", "noul"), ("probabilities", {"promotion": 1.0})):
            r = response()
            r["answers"]["context"][key] = value
            invalid.append(r)
        invalid += [response(model="jev-latest"), response(usage={"input_tokens": True, "output_tokens": 1}), {}, None]
        r = response(); r["answers"]["context"]["choice"] = "other"; invalid.append(r)
        for r in invalid:
            with self.subTest(value=r), self.assertRaises(ValueError):
                validate_response(r)


class ShadowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 1000.0
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.config = shadow_config()
        self.engine = Engine(self.config, self.store, clock=lambda: self.now)
        self.live = LiveSession(self.engine)
        self.provider = AsyncMock(return_value=response())
        self.active = True
        self.worker = ShadowClassifier(self.engine, self.provider, active=lambda: self.active)

    async def asyncTearDown(self):
        await self.worker.close()

    def pattern(self, author="50", start=100, content="Repeated community offer for testing only."):
        for i, channel in enumerate(self.config.monitored_channel_ids):
            self.engine.process(MessageEvent("1", channel, str(start+i), author, content, self.now-5+i))
        return next(i["id"] for i in self.store.incidents() if i["author_id"] == author and i["status"] == "open")

    def attempt(self, identity):
        return dict(self.store.db.execute("SELECT * FROM classifier_attempts WHERE incident_id=?", (identity,)).fetchone())

    async def test_role_exemption_also_prevents_legacy_shadow_evaluation(self):
        self.config = replace(self.config, classifier=replace(self.config.classifier, exempt_role_ids=("77",)))
        self.engine = Engine(self.config, self.store, clock=lambda:self.now)
        self.worker = ShadowClassifier(self.engine,self.provider)
        for i,channel in enumerate(self.config.monitored_channel_ids):
            self.engine.process(MessageEvent("1",channel,str(800+i),"50","Repeated role-exempt message for testing.",
                                            self.now-3+i,author_role_ids=("77",)))
        identity=self.store.incidents()[0]["id"]
        await self.worker.evaluate_one(identity)
        self.provider.assert_not_awaited()
        self.assertTrue(self.store.reports())

    async def test_shadow_only_incidents_no_report_mutation_and_no_copied_ids(self):
        self.assertIsNone(self.worker.snapshot("not-an-incident"))
        identity = self.pattern(content="Repeated offer for <@977263877391794217> see https://example.invalid/test")
        before = self.store.reports()
        await self.worker.evaluate_one(identity)
        self.assertEqual(before, self.store.reports())
        self.assertEqual(self.attempt(identity)["outcome"], "ok")
        payload = json.loads(self.provider.call_args.args[0])
        self.assertEqual(len(payload["state"]["texts"]), 1)
        self.assertEqual(payload["state"]["observed_copies"], 3)
        self.assertNotIn("977263877391794217", self.provider.call_args.args[0].decode())
        self.assertNotIn("author_id", payload["state"])
        self.assertEqual(self.engine.status()["ai_attempts"], 1)

    async def test_once_per_incident_across_revision_and_restart(self):
        identity = self.pattern()
        await self.worker.evaluate_one(identity)
        self.now += 10
        self.engine.process(MessageEvent("1", "10", "104", "50", "Repeated community offer for testing only.", self.now))
        restarted = ShadowClassifier(self.engine, self.provider)
        await restarted.evaluate_one(identity)
        self.provider.assert_awaited_once()
        self.assertEqual(self.engine.status()["ai_attempts"], 1)

    async def test_pause_disconnect_delete_and_flag_change_discard_late_judgment(self):
        for kind in ("pause", "disconnect", "flag"):
            self.now += 10
            identity = self.pattern(author=str(60 + len(self.store.incidents())), start=200 + len(self.store.incidents())*10)
            async def change(_):
                if kind == "pause": self.engine.set_paused(True)
                elif kind == "disconnect": self.live.gap("disconnect")
                else: self.active = False
                return response()
            self.worker.evaluator = change
            await self.worker.evaluate_one(identity)
            attempt = self.attempt(identity)
            self.assertEqual(attempt["outcome"], "stale")
            self.assertNotIn("choice", json.loads(attempt["result_json"]))
            self.assertEqual(json.loads(attempt["result_json"])["input_tokens"], 500)
            self.active = True
            self.engine.set_paused(False)

    async def test_edit_policy_expiry_invalidates_late_results(self):
        for kind in ("edit", "policy", "expiry"):
            self.now += 10
            self.store.set_setting("policy_hash", self.config.policy_hash)
            author = str(70 + len(self.store.incidents()))
            start = 300 + len(self.store.incidents())*10
            identity = self.pattern(author=author, start=start)
            async def change(_):
                if kind == "edit":
                    self.engine.process(MessageEvent("1", "10", str(start), author, "Changed ordinary text", self.now-5, edited_at=self.now))
                elif kind == "policy": self.store.set_setting("policy_hash", "changed")
                else: self.now += 500
                return response()
            self.worker.evaluator = change
            await self.worker.evaluate_one(identity)
            self.assertEqual(self.attempt(identity)["outcome"], "stale")

    async def test_paused_inactive_and_scope_mismatch_never_call(self):
        identity = self.pattern()
        self.active = False
        await self.worker.evaluate_one(identity)
        self.active = True
        self.engine.set_paused(True)
        await self.worker.evaluate_one(identity)
        self.provider.assert_not_awaited()

    async def test_crash_reservation_is_uncertain_not_retried_and_budget_retained(self):
        identity = self.pattern()
        self.assertTrue(self.worker.reserve(self.worker.snapshot(identity)))
        self.now += 10
        restarted = ShadowClassifier(self.engine, self.provider)
        await restarted.evaluate_one(identity)
        self.provider.assert_not_awaited()
        self.assertEqual(self.attempt(identity)["outcome"], "uncertain")
        self.assertEqual(self.engine.status()["ai_reserved_microusd"], 2753)

    async def test_all_budget_limits_reserve_before_io_and_survive_restart(self):
        for field, limit in (("max_daily_calls", 1), ("max_total_calls", 1),
                             ("daily_budget_microusd", 2753), ("total_budget_microusd", 2753)):
            with self.subTest(field=field):
                with Store(":memory:") as store:
                    c = shadow_config(**{field: limit})
                    engine = Engine(c, store, clock=lambda: self.now)
                    worker = ShadowClassifier(engine, self.provider)
                    for author in ("80", "81"):
                        self.now += 10
                        for i, channel in enumerate(c.monitored_channel_ids):
                            engine.process(MessageEvent("1", channel, author+str(i), author, "Repeated budget test message long enough", self.now-5+i))
                        identity = next(x["id"] for x in store.incidents() if x["author_id"] == author)
                        await worker.evaluate_one(identity)
                        worker = ShadowClassifier(engine, self.provider)
                    self.assertEqual(engine.status()["ai_attempts"], 1)

    async def test_daily_reset_keeps_total_accounting_and_rate_limit_survives_restart(self):
        first = self.pattern()
        await self.worker.evaluate_one(first)
        second = self.pattern(author="51", start=200)
        restarted = ShadowClassifier(self.engine, self.provider)
        await restarted.evaluate_one(second)
        self.assertEqual(self.engine.status()["ai_attempts"], 1)
        self.now = 86401
        second = self.pattern(author="52", start=300)
        await restarted.evaluate_one(second)
        self.assertEqual(self.store.get_setting("classifier_daily_calls"), 1)
        self.assertEqual(self.store.get_setting("classifier_total_calls"), 2)
        self.assertEqual(self.store.get_setting("classifier_total_reserved_microusd"), 5506)

    async def test_rechecks_evidence_after_reservation_before_provider(self):
        identity = self.pattern()
        original = self.worker.reserve
        def reserve_and_pause(job):
            reserved = original(job)
            self.engine.set_paused(True)
            return reserved
        self.worker.reserve = reserve_and_pause
        await self.worker.evaluate_one(identity)
        self.provider.assert_not_awaited()
        self.assertEqual(self.attempt(identity)["outcome"], "stale_before_request")

    async def test_invalid_response_failure_timeout_and_no_retries_or_error_text(self):
        for result in (RuntimeError("SECRET BODY DO NOT RECORD"), TimeoutError(), {}):
            self.now += 10
            identity = self.pattern(author=str(80 + len(self.store.incidents())), start=400+10*len(self.store.incidents()))
            provider = AsyncMock(side_effect=result if isinstance(result, Exception) else None, return_value=result)
            self.worker.evaluator = provider
            before = self.store.reports()
            await self.worker.evaluate_one(identity)
            self.now += 10
            await self.worker.evaluate_one(identity)
            provider.assert_awaited_once()
            self.assertEqual(before, self.store.reports())
            self.assertNotIn("SECRET", str(self.attempt(identity)))

    async def test_usage_over_reservation_latches_until_review(self):
        identity = self.pattern()
        self.provider.return_value = response(usage={"input_tokens": 65537, "output_tokens": 40})
        await self.worker.evaluate_one(identity)
        self.assertTrue(self.store.get_setting("classifier_billing_guard"))
        self.now += 10
        await self.worker.evaluate_one(self.pattern(author="51", start=200))
        self.provider.assert_awaited_once()

    async def test_queue_capacity_and_shutdown_unknown_attempt_preserve_baseline(self):
        self.worker.queue = asyncio.Queue(maxsize=1)
        first = self.pattern()
        second = self.pattern(author="51", start=200)
        self.worker.submit([first, first, second])
        self.assertEqual(self.worker.queue.qsize(), 1)
        self.assertEqual(self.store.get_setting("classifier_dropped"), 1)
        started = asyncio.Event()
        async def blocked(_):
            started.set()
            await asyncio.Event().wait()
        self.worker.evaluator = blocked
        before = self.store.reports()
        self.worker.start()
        await asyncio.wait_for(started.wait(), 1)
        await self.worker.close()
        self.assertEqual(self.attempt(first)["outcome"], "uncertain")
        self.assertEqual(before, self.store.reports())

    async def test_input_limit_and_evidence_version_checks(self):
        identity = self.pattern(content="\U0001f525" * 3990 + "test")
        await self.worker.evaluate_one(identity)
        self.provider.assert_not_awaited()
        self.assertEqual(self.store.get_setting("classifier_state"), "input_limit")
        self.now += 10
        identity = self.pattern(author="51", start=200)
        self.store.db.execute("UPDATE messages SET version='modified' WHERE message_id='200'")
        await self.worker.evaluate_one(identity)
        self.provider.assert_not_awaited()

    async def test_retention_removes_results_without_refunding_lifetime_budget(self):
        identity = self.pattern()
        await self.worker.evaluate_one(identity)
        self.now += self.config.storage.retention_seconds + 1
        with self.store.transaction(): self.engine._prune(self.now)
        self.assertIsNone(self.store.db.execute("SELECT * FROM classifier_attempts").fetchone())
        self.assertEqual(self.engine.status()["ai_attempts"], 1)


if __name__ == "__main__":
    unittest.main()
