from dataclasses import replace
import json
import unittest
from unittest.mock import AsyncMock, patch

from liberdus_moderator.classifier import ShadowClassifier
from liberdus_moderator.classification_view import saved_classification
from liberdus_moderator.commands import CommandRequest, handle_command
from liberdus_moderator.config import Config
from liberdus_moderator.engine import Engine
from liberdus_moderator.live import LiveSession
from liberdus_moderator.models import MessageEvent
from liberdus_moderator.storage import Store
from test_classifier import response, shadow_config


class SavedClassificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 1000.0
        self.config = shadow_config()
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.engine = Engine(self.config, self.store, clock=lambda: self.now)
        self.live = LiveSession(self.engine)
        self.provider = AsyncMock(return_value=response("quoted_warning"))
        self.worker = ShadowClassifier(self.engine, self.provider)
        for i, channel in enumerate(self.config.monitored_channel_ids):
            self.engine.process(MessageEvent("1", channel, str(100+i), "50",
                'Warning about "claim a reward" messages. @everyone', self.now-5+i))
        self.identity = self.store.incidents()[0]["id"]
        await self.worker.evaluate_one(self.identity)
        self.request = CommandRequest("1", "20", "98", "incident", arguments=(self.identity,))

    async def asyncTearDown(self):
        await self.worker.close()

    def view(self):
        return handle_command(self.engine, self.request)["data"]["classification"]

    def dump(self):
        return "\n".join(self.store.db.iterdump())

    async def test_lookup_is_read_only_and_does_not_initialize_or_call_provider(self):
        before = self.dump()
        with patch("liberdus_moderator.classifier.ShadowClassifier", side_effect=AssertionError("no worker")), \
                patch("liberdus_moderator.jev.evaluate", side_effect=AssertionError("no provider")):
            result = handle_command(self.engine, self.request)
            alias = handle_command(self.engine, replace(self.request, command="explain"))
        view = result["data"]["classification"]
        self.assertEqual(view["choice"], "quoted_warning")
        self.assertEqual(view["evidence_state"], "current")
        self.assertEqual(view["age_seconds"], 0)
        self.assertEqual(view["revision"], 1)
        self.assertEqual(result["ai_calls"], 0)
        self.assertEqual(result["public_actions"], [])
        self.assertEqual(alias["data"]["classification"], view)
        self.assertEqual(self.dump(), before)
        self.provider.assert_awaited_once()

    async def test_private_rendering_is_bounded_and_includes_safe_saved_excerpt(self):
        text = self.live.command(self.request, "900", True)
        self.assertIn("Label     : Quoted warning", text)
        self.assertIn("Score     : 0.80 (model score)", text)
        self.assertIn("Evaluated : Revision 1", text)
        self.assertIn("Revision 1, 0s ago", text)
        self.assertIn("Evidence  : CURRENT", text)
        self.assertEqual(text.count("```"), 4)
        panel = text.split("```\n", 1)[1].split("\n```", 1)[0]
        self.assertTrue(panel.isascii())
        self.assertLessEqual(max(map(len, panel.splitlines())), 32)
        self.assertIn("Incident ID\n" + self.identity, text)
        self.assertIn("no new AI call", text)
        self.assertIn("claim a reward", text)
        self.assertIn("[Open message 1](<https://discord.com/channels/1/10/100>)", text)
        self.assertNotIn("@everyone", text)
        self.assertLess(len(text), 1900)
        self.assertIsNone(self.live.command(self.request, "900", True))
        self.provider.assert_awaited_once()

    async def test_authorization_precedes_saved_result_access(self):
        before = self.dump()
        with patch("liberdus_moderator.classification_view.saved_classification", side_effect=AssertionError("unauthorized read")):
            for change in ({"user_id": "50"}, {"channel_id": "10"}, {"guild_id": "2"}):
                request = replace(self.request, **change)
                self.assertFalse(handle_command(self.engine, request)["authorized"])
                self.assertIsNone(self.live.command(request, "901", True))
        self.assertEqual(before, self.dump())

    async def test_expiration_is_historical_without_pruning_or_writes(self):
        self.now += 3600
        before = self.dump()
        view = self.view()
        self.assertEqual(view["choice"], "quoted_warning")
        self.assertEqual(view["evidence_state"], "historical")
        self.assertEqual(view["reason"], "expired")
        self.assertEqual(view["age_seconds"], 3600)
        self.assertEqual(self.store.incident(self.identity)["status"], "open")
        self.assertEqual(self.dump(), before)
        text = self.live.command(self.request, "902", True)
        self.assertIn("Revision 1, 1h ago", text)
        self.assertIn("Evidence  : HISTORICAL", text)
        self.assertIn("JEV is historical: evidence window expired", text)

    async def test_edit_withdrawal_preserves_result_as_history(self):
        self.now += 10
        self.engine.process(MessageEvent("1", "10", "100", "50", "A corrected ordinary message", 995, edited_at=self.now))
        view = self.view()
        self.assertEqual(view["evidence_state"], "historical")
        self.assertEqual(view["revision"], 1)
        self.assertEqual(view["choice"], "quoted_warning")
        self.assertEqual(self.store.incident(self.identity)["status"], "withdrawn")

    async def test_new_copy_marks_prior_revision_historical(self):
        self.now += 10
        self.engine.process(MessageEvent("1", "10", "104", "50",
            'Warning about "claim a reward" messages. @everyone', self.now))
        self.assertEqual(self.view()["reason"], "revision_changed")
        self.assertEqual(self.view()["revision"], 1)
        self.assertEqual(self.store.incident(self.identity)["revision"], 2)

    async def test_reconnect_reset_and_pause_keep_saved_result_historical(self):
        self.live.gap("reconnect")
        view = self.view()
        self.assertEqual(view["reason"], "incident_changed")
        self.assertEqual(view["choice"], "quoted_warning")
        self.engine.set_paused(True)
        self.assertEqual(self.view()["reason"], "paused")

    async def test_missing_changed_or_ineligible_working_evidence_is_historical(self):
        for query in ("UPDATE messages SET version='changed' WHERE message_id='100'",
                      "UPDATE messages SET eligible=0 WHERE message_id='100'",
                      "DELETE FROM messages WHERE message_id='100'"):
            with self.subTest(query=query):
                self.store.db.execute("SAVEPOINT fixture")
                self.store.db.execute(query)
                self.assertEqual(self.view()["reason"], "evidence_changed")
                self.store.db.execute("ROLLBACK TO fixture")
                self.store.db.execute("RELEASE fixture")
        incident = self.store.incident(self.identity)
        incident["evidence"][0]["content"] = "changed snapshot"
        self.assertEqual(saved_classification(self.engine, incident)["reason"], "evidence_changed")

    async def test_shadow_disabled_or_changed_policy_keeps_old_label_available(self):
        config = replace(self.config, ai_enabled=False, classifier=replace(self.config.classifier, mode="off"))
        self.engine = Engine(config, self.store, clock=lambda: self.now)
        before = self.dump()
        view = self.view()
        self.assertEqual(view["evidence_state"], "historical")
        self.assertEqual(view["reason"], "policy_changed")
        self.assertEqual(view["choice"], "quoted_warning")
        self.assertEqual(self.dump(), before)

    async def test_old_rubric_model_and_clock_are_explicitly_historical(self):
        self.store.db.execute("UPDATE classifier_attempts SET rubric_hash=?", ("0"*64,))
        self.assertEqual(self.view()["reason"], "classifier_changed")
        raw = json.loads(self.store.db.execute("SELECT result_json FROM classifier_attempts").fetchone()[0])
        raw["model"] = "jev-1.12.0"
        self.store.db.execute("UPDATE classifier_attempts SET model=?, result_json=?", (raw["model"], json.dumps(raw)))
        self.assertEqual(self.view()["choice"], "quoted_warning")
        self.assertEqual(self.view()["evidence_state"], "historical")
        self.now -= 1
        # Model/rubric mismatch takes precedence over the clock warning.
        self.assertIsNone(self.view()["age_seconds"])

    async def test_failures_running_and_uncertain_never_display_a_label(self):
        for index, outcome in enumerate(("timeout", "authentication_failed", "running", "uncertain", "stale")):
            with self.subTest(outcome=outcome):
                self.store.db.execute("UPDATE classifier_attempts SET outcome=?, result_json=NULL", (outcome,))
                view = self.view()
                self.assertEqual(view["outcome"], outcome)
                self.assertNotIn("choice", view)
                text = self.live.command(self.request, str(950 + index), True)
                self.assertNotIn("Confidence:", text)
                self.assertIn("Result    :", text)
                self.assertIn(outcome.split("_")[0].capitalize(), text)

    async def test_no_attempt_row_is_read_only_and_not_retried(self):
        self.store.db.execute("DELETE FROM classifier_attempts WHERE incident_id=?", (self.identity,))
        before = self.dump()
        self.assertEqual(self.view(), {"outcome": "not_evaluated"})
        self.assertEqual(before, self.dump())
        self.provider.assert_awaited_once()

    async def test_clock_rollback_is_historical_and_extra_provider_text_is_omitted(self):
        raw = json.loads(self.store.db.execute("SELECT result_json FROM classifier_attempts").fetchone()[0])
        raw["explanation"] = "@everyone arbitrary provider text"
        self.store.db.execute("UPDATE classifier_attempts SET result_json=?", (json.dumps(raw),))
        self.now -= 1
        self.assertEqual(self.view()["reason"], "clock_changed")
        text = self.live.command(self.request, "960", True)
        self.assertIn("Revision 1, unknown", text)
        self.assertIn("Evidence  : HISTORICAL", text)
        self.assertNotIn("@everyone", text)
        self.assertNotIn("arbitrary provider", text)

    async def test_corrupt_records_return_fixed_unavailable_without_leaks(self):
        original = dict(self.store.db.execute("SELECT * FROM classifier_attempts").fetchone())
        bad = json.loads(original["result_json"])
        bad["choice"] = "@everyone steal the token"
        for field, value in (("result_json", "not JSON @everyone"), ("result_json", json.dumps(bad)),
                             ("result_json", "x"*9000), ("model", "@everyone"),
                             ("outcome", "@everyone"), ("revision", -1), ("finished_at", None),
                             ("evidence_hash", "bad"), ("started_at", -1), ("latency_ms", -3)):
            with self.subTest(field=field):
                self.store.db.execute(f"UPDATE classifier_attempts SET {field}=?", (value,))
                self.assertEqual(self.view(), {"outcome": "unavailable"})
                self.store.db.execute(f"UPDATE classifier_attempts SET {field}=?", (original[field],))
        self.store.set_setting("classifier_schema_version", 2)
        self.assertEqual(self.view(), {"outcome": "unavailable"})


class MissingClassificationTests(unittest.TestCase):
    def test_never_enabled_does_not_create_classifier_state_or_table(self):
        config = Config("1", "99", ("10", "11", "12"), ("20",), ("98",))
        with Store(":memory:") as store:
            engine = Engine(config, store, clock=lambda: 1000)
            for i, channel in enumerate(config.monitored_channel_ids):
                engine.process(MessageEvent("1", channel, str(100+i), "50", "Repeated message from a human", 995+i))
            request = CommandRequest("1", "20", "98", "incident", arguments=(store.incidents()[0]["id"],))
            before = "\n".join(store.db.iterdump())
            self.assertEqual(handle_command(engine, request)["data"]["classification"], {"outcome": "not_evaluated"})
            self.assertEqual(before, "\n".join(store.db.iterdump()))
            self.assertFalse(store.db.execute("SELECT 1 FROM sqlite_master WHERE name='classifier_attempts'").fetchone())
