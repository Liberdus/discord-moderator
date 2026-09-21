from dataclasses import replace
import json
import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from liberdus_moderator.commands import CommandRequest, handle_command
from liberdus_moderator.config import Config
from liberdus_moderator.engine import Engine
from liberdus_moderator.evidence_view import units
from liberdus_moderator.live import LiveSession, parse_command
from liberdus_moderator.models import MessageEvent
from liberdus_moderator.moderator_review import TABLE, saved_review, MAX_REVIEWS_PER_INCIDENT
from liberdus_moderator.storage import Store


class ModeratorReviewTests(unittest.TestCase):
    def setUp(self):
        self.config = Config("1", "99", ("10", "11", "12"), ("20",), ("98",))
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.now = 1000.0
        self.engine = Engine(self.config, self.store, clock=lambda: self.now)
        self.live = LiveSession(self.engine)
        self.text = "Friday maintenance. Premium signals. Referral code. Passing this along as received."
        for index, channel in enumerate(self.config.monitored_channel_ids):
            self.engine.process(MessageEvent("1", channel, str(100+index), "50", self.text, 995+index))
        self.incident = self.store.incidents()[0]["id"]
        self.lookup = CommandRequest("1", "20", "98", "incident", arguments=(self.incident,))

    def request(self, label="not-promotion", revision="1"):
        return replace(self.lookup, command="review", arguments=(self.incident, revision, label))

    def protected_state(self):
        return {name: [tuple(row) for row in self.store.db.execute("SELECT * FROM " + name)]
                for name in ("incidents", "incident_versions", "messages", "reports")}

    def delivered(self):
        report = self.live.claim_report()
        self.live.finish_report(report["id"], "700")
        return report

    def test_explicit_review_records_human_revision_without_changing_rules_or_ai(self):
        before = self.protected_state()
        with patch("liberdus_moderator.jev.evaluate", side_effect=AssertionError("No AI")):
            result = handle_command(self.engine, self.request())
        self.assertTrue(result["ok"])
        self.assertEqual(result["ai_calls"], 0)
        self.assertEqual(result["public_actions"], [])
        data = result["data"]
        self.assertEqual((data["label"], data["revision"], data["reviewer_id"], data["state"]),
                         ("not_promotion", 1, "98", "current"))
        self.assertEqual(self.protected_state(), before)
        self.assertEqual(self.engine.status()["ai_attempts"], 0)
        lookup = handle_command(self.engine, self.lookup)["data"]
        self.assertEqual(lookup["classification"], {"outcome": "not_evaluated"})
        self.assertEqual(lookup["moderator_review"]["label"], "not_promotion")
        rendered = self.live.command(replace(self.lookup, command="explain"), "900", True)
        self.assertIn("LEGACY CONTENT LABEL", rendered)
        self.assertIn("Not promotion", rendered)
        self.assertIn("Staff     : Not reviewed", rendered)
        self.assertIn("Friday maintenance", rendered)
        self.assertIn("Open message 1", rendered)
        self.assertLessEqual(units(rendered), 1900)

    def test_reply_resolves_only_recorded_bot_delivery_and_deduplicates(self):
        self.delivered()
        command = parse_command("!mod review promotion", "1", "20", "98", reply_to_message_id="700")
        result = self.live.command(command, "901", True)
        self.assertIn("Moderator review saved", result)
        self.assertIn("Reviewed revision: 1", result)
        self.assertIsNone(self.live.command(command, "901", True))
        self.assertEqual(self.store.db.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0], 1)
        self.assertEqual(saved_review(self.engine, self.store.incident(self.incident))["label"], "promotion")

    def test_reply_cannot_trust_forged_unknown_uncertain_or_wrong_channel_report(self):
        report = self.delivered()
        command = parse_command("!mod review promotion", "1", "20", "98", reply_to_message_id="701")
        self.assertIn("Review not saved", self.live.command(command, "902", True))
        command = replace(command, reply_to_message_id="700")
        self.store.db.execute("UPDATE deliveries SET outcome='uncertain'")
        self.assertIn("Review not saved", self.live.command(command, "903", True))
        self.store.db.execute("UPDATE deliveries SET outcome='sent'")
        payload = report["payload"]
        payload["channel_id"] = "10"
        self.store.db.execute("UPDATE reports SET payload_json=?", (json.dumps(payload),))
        self.assertIn("Review not saved", self.live.command(command, "904", True))
        self.assertFalse(self.store.db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)).fetchone())

    def test_authorization_precedes_evidence_and_review_access(self):
        before = list(self.store.db.iterdump())
        with patch("liberdus_moderator.evidence_view.saved_evidence", side_effect=AssertionError), \
             patch("liberdus_moderator.moderator_review.saved_review", side_effect=AssertionError), \
             patch("liberdus_moderator.moderator_review.record_review", side_effect=AssertionError):
            for change in ({"user_id": "50"}, {"channel_id": "10"}, {"guild_id": "2"}):
                for command in (self.lookup, self.request()):
                    wrong = replace(command, **change)
                    self.assertFalse(handle_command(self.engine, wrong)["authorized"])
                    self.assertIsNone(self.live.command(wrong, "905", True))
        self.assertEqual(before, list(self.store.db.iterdump()))

    def test_read_only_lookup_does_not_initialize_review_tables_or_provider(self):
        before = list(self.store.db.iterdump())
        result = handle_command(self.engine, self.lookup)
        self.assertEqual(result["data"]["moderator_review"], {"state": "not_reviewed"})
        self.assertEqual(before, list(self.store.db.iterdump()))

    def test_corrections_append_history_and_expired_or_changed_evidence_is_historical(self):
        handle_command(self.engine, self.request("promotion"))
        self.now += 1
        handle_command(self.engine, self.request("not-promotion"))
        labels = [row[0] for row in self.store.db.execute(f"SELECT label FROM {TABLE} ORDER BY sequence")]
        self.assertEqual(labels, ["promotion", "not_promotion"])
        self.engine.process(MessageEvent("1", "10", "100", "50", "An edited unrelated support request.", 995, 1002))
        incident = self.store.incident(self.incident)
        view = saved_review(self.engine, incident)
        self.assertEqual(view["state"], "historical")
        self.assertEqual(view["revision"], 1)
        self.assertGreater(incident["revision"], 1)
        # Reviewing an explicitly selected retained old revision stays historical.
        result = handle_command(self.engine, self.request("unsure"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["state"], "historical")
        self.assertEqual(result["data"]["label"], "unsure")

    def test_old_report_reply_stays_bound_to_its_original_revision(self):
        self.delivered()
        self.live.gap("reconnect")
        reply = parse_command("!mod review unsure", "1", "20", "98", reply_to_message_id="700")
        result = self.live.command(reply, "906", True)
        self.assertIn("Reviewed revision: 1 (historical)", result)
        self.assertEqual(self.store.db.execute(f"SELECT revision FROM {TABLE}").fetchone()[0], 1)

    def test_incident_prompt_binding_survives_storage_reopen_and_keeps_displayed_revision(self):
        rendered = self.live.command(self.lookup, "910", True)
        self.assertEqual(rendered.review_target, (self.incident, 1))
        self.live.gap("reconnect")
        self.live.save_review_prompt("710", "20", rendered.review_target)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "moderation.sqlite3"
            with sqlite3.connect(path) as copy:
                self.store.db.backup(copy)
            with Store(str(path)) as reopened:
                live = LiveSession(Engine(self.config, reopened, clock=lambda: self.now))
                reply = parse_command("!mod review unsure", "1", "20", "98", reply_to_message_id="710")
                self.assertEqual(live.review_reply_target(reply), (self.incident, "1"))
                result = live.command(reply, "interaction:810", True)
                self.assertIn("revision: 1 (historical)", result)
        self.store.db.execute("DELETE FROM incidents WHERE id=?", (self.incident,))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM review_prompt_links").fetchone()[0], 0)

    def test_prompt_bindings_are_bounded_and_require_private_channel(self):
        with self.assertRaises(ValueError):
            self.live.save_review_prompt("711", "10", (self.incident, 1))
        self.store.db.executemany("INSERT INTO review_prompt_links VALUES(?,?,?,?,?)",
            ((str(10000+index), "20", self.incident, 1, self.now-index) for index in range(5000)))
        self.now += 1
        self.live.save_review_prompt("712", "20", (self.incident, 1))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM review_prompt_links").fetchone()[0], 5000)
        self.assertIsNotNone(self.store.db.execute("SELECT 1 FROM review_prompt_links WHERE message_id='712'").fetchone())
        reply = parse_command("!mod review unsure", "1", "20", "98", reply_to_message_id="712")
        self.store.db.execute("UPDATE review_prompt_links SET channel_id='10' WHERE message_id='712'")
        self.assertIsNone(self.live.review_reply_target(reply))

    def test_invalid_labels_missing_revisions_and_full_history_do_not_overwrite(self):
        for label, revision in (("ban", "1"), ("promotion", "999"), ("promotion", "01")):
            self.assertFalse(handle_command(self.engine, self.request(label, revision))["ok"])
        for _ in range(MAX_REVIEWS_PER_INCIDENT):
            self.assertTrue(handle_command(self.engine, self.request())["ok"])
        self.assertFalse(handle_command(self.engine, self.request("promotion"))["ok"])
        self.assertEqual(self.store.db.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0], MAX_REVIEWS_PER_INCIDENT)
        self.assertEqual(saved_review(self.engine, self.store.incident(self.incident))["label"], "not_promotion")

    def test_reviews_persist_and_follow_incident_retention(self):
        handle_command(self.engine, self.request())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "moderation.sqlite3"
            with sqlite3.connect(path) as copy:
                self.store.db.backup(copy)
            with Store(str(path)) as reopened:
                engine = Engine(self.config, reopened, clock=lambda: self.now)
                self.assertEqual(saved_review(engine, reopened.incident(self.incident))["label"], "not_promotion")
        self.store.db.execute("DELETE FROM incidents WHERE id=?", (self.incident,))
        self.assertEqual(self.store.db.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0], 0)

    def test_saved_preview_neutralizes_mentions_markdown_links_controls_and_stays_bounded(self):
        malicious = "``` @everyone <@123> [click](https://evil.example) \u202e " + "😀*" * 1500
        store = Store(":memory:")
        self.addCleanup(store.close)
        engine = Engine(self.config, store, clock=lambda: self.now)
        live = LiveSession(engine)
        for index, channel in enumerate(self.config.monitored_channel_ids):
            engine.process(MessageEvent("1", channel, str(200+index), "50", malicious, 995+index))
        identity = store.incidents()[0]["id"]
        request = replace(self.lookup, arguments=(identity,))
        rendered = live.command(request, "907", True)
        self.assertEqual(rendered.count("```"), 4)
        self.assertNotIn("@everyone", rendered)
        self.assertNotIn("<@123>", rendered)
        self.assertNotIn("https://evil.example", rendered)
        self.assertNotIn("\u202e", rendered)
        self.assertIn("https://discord.com/channels/1/10/200", rendered)
        self.assertIn("excerpt", rendered)
        self.assertLessEqual(units(rendered), 1900)
        self.assertEqual(store.incident(identity)["evidence"][0]["content"], malicious)

    def test_full_mobile_panel_with_long_ids_jev_review_and_emoji_fits_discord_limit(self):
        from liberdus_moderator.classification_view import format_incident
        from liberdus_moderator.evidence_view import saved_evidence
        config = Config("12345678901234567890", "12345678901234567891",
            tuple(str(12345678901234567892 + index) for index in range(3)),
            ("12345678901234567895",), ("12345678901234567896",))
        store = Store(":memory:")
        self.addCleanup(store.close)
        engine = Engine(config, store, clock=lambda: self.now)
        for index, channel in enumerate(config.monitored_channel_ids):
            engine.process(MessageEvent(config.guild_id, channel, str(12345678901234567900 + index),
                "12345678901234567897", "Repeated offer with emoji " + "😀" * 1000, 995+index))
        incident = store.incident(store.incidents()[0]["id"])
        incident["classification"] = {"outcome": "ok", "choice": "quoted_warning", "confidence": 0.98,
            "model": "jev-99999.99999.99999", "revision": 1000000000, "age_seconds": 9999999999,
            "evidence_state": "historical", "reason": "clock_changed"}
        incident["moderator_review"] = {"state": "historical", "label": "not_promotion", "revision": 999999999,
            "reviewer_id": config.operator_user_ids[0], "reviewed_at": self.now}
        incident["staff_assessment"] = {"state": "historical", "label": "needs_attention", "revision": 999999999,
            "reviewer_id": config.operator_user_ids[0], "reviewed_at": self.now,
            "applies_to_snapshot": True, "complete": False}
        incident["evidence_view"] = saved_evidence(engine, incident)
        text = format_incident(incident)
        self.assertLessEqual(units(format_incident(incident, details=True)), 1900)
        self.assertLessEqual(units(text), 1900)
        self.assertIn("SAVED MESSAGE", text)
        self.assertIn("😀", text)
        self.assertIn("Open message 3", text)
        panel = text.split("```")[1]
        self.assertTrue(panel.isascii())
        self.assertTrue(all(len(line) <= 32 for line in panel.splitlines()))

    def test_out_of_scope_or_malformed_saved_evidence_is_not_exposed_or_reviewed(self):
        incident = self.store.incident(self.incident)
        bad = incident["evidence"]
        bad[0]["channel_id"] = "999"
        self.store.db.execute("UPDATE incidents SET evidence_json=?", (json.dumps(bad),))
        self.store.db.execute("UPDATE incident_versions SET evidence_json=?", (json.dumps(bad),))
        data = handle_command(self.engine, self.lookup)["data"]
        self.assertFalse(data["evidence_view"]["available"])
        self.assertFalse(handle_command(self.engine, self.request())["ok"])
        rendered = self.live.command(self.lookup, "908", True)
        self.assertNotIn("Friday maintenance", rendered)
        self.assertNotIn("/999/", rendered)
        self.assertIn("Unavailable for this scope", rendered)


if __name__ == "__main__":
    unittest.main()
