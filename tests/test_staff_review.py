from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from liberdus_moderator.commands import CommandRequest, handle_command
from liberdus_moderator.config import Config
from liberdus_moderator.engine import Engine
from liberdus_moderator.evidence_view import units
from liberdus_moderator.live import LiveSession, parse_command
from liberdus_moderator.models import MessageEvent
from liberdus_moderator.moderator_review import record_review
from liberdus_moderator.staff_review import TABLE, MAX_ASSESSMENTS, pending_page, saved_assessment
from liberdus_moderator.storage import Store


class StaffReviewTests(unittest.TestCase):
    def setUp(self):
        self.config = Config("1", "99", ("10", "11", "12"), ("20",), ("98",))
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.now = 1000.0
        self.engine = Engine(self.config, self.store, clock=lambda: self.now)
        self.live = LiveSession(self.engine)
        self.text = 'Warning: messages saying "claim your free reward" may be scams.'
        self.identity = self.incident(0)
        self.request = CommandRequest("1", "20", "98", "assess", arguments=(self.identity, "1", "looks-okay"))

    def incident(self, index):
        for offset, channel in enumerate(self.config.monitored_channel_ids):
            result = self.engine.process(MessageEvent("1", channel, str(100+index*10+offset),
                str(50+index), self.text, 995+offset))
        return result["incident_ids"][0]

    def assess(self, label="looks-okay", revision="1", identity=None):
        return handle_command(self.engine, replace(self.request, arguments=(identity or self.identity, revision, label)))

    def view(self):
        return saved_assessment(self.engine, self.store.incident(self.identity))

    def protected(self):
        return {table: [tuple(row) for row in self.store.db.execute("SELECT * FROM " + table)]
                for table in ("incidents", "incident_versions", "messages", "reports")}

    def test_three_choices_drive_staff_queue_without_modifying_rules_or_ai(self):
        before = self.protected()
        with patch("liberdus_moderator.jev.evaluate", side_effect=AssertionError("No model call")):
            for label, expected_count in (("needs-attention", 1), ("unsure", 1), ("looks-okay", 0)):
                self.assertTrue(self.assess(label)["ok"])
                self.assertEqual(pending_page(self.engine)["total"], expected_count)
        self.assertEqual(before, self.protected())
        self.assertEqual(self.engine.status()["ai_attempts"], 0)
        self.assertEqual(self.view()["reviewer_id"], "98")
        self.assertEqual(self.view()["reviewed_at"], self.now)
        self.assertEqual(self.store.db.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0], 3)

    def test_legacy_promotion_labels_are_preserved_without_mapping_to_staff_completion(self):
        record_review(self.engine, self.identity, "1", "not-promotion", "98")
        old = list(self.store.db.execute("SELECT * FROM moderator_reviews_v1"))
        self.assertEqual(pending_page(self.engine)["total"], 1)
        self.assertEqual(self.view()["state"], "not_reviewed")
        self.assess()
        self.assertEqual(old, list(self.store.db.execute("SELECT * FROM moderator_reviews_v1")))
        for label in ("promotion", "not-promotion", "ban"):
            self.assertFalse(self.assess(label)["ok"])
        detailed = self.live.command(replace(self.request, command="explain", arguments=(self.identity,)), "900", True)
        self.assertIn("Legacy content label", detailed)
        self.assertIn("Not promotion", detailed)
        self.assertIn("**Looks okay** · Complete", detailed)

    def test_unauthorized_assessment_and_queue_never_read_or_write_private_state(self):
        before = list(self.store.db.iterdump())
        with patch("liberdus_moderator.staff_review.record_assessment", side_effect=AssertionError), \
             patch("liberdus_moderator.staff_review.pending_page", side_effect=AssertionError):
            for changes in ({"user_id":"50"}, {"guild_id":"2"}, {"channel_id":"10"}):
                for request in (self.request, replace(self.request, command="pending", arguments=())):
                    self.assertFalse(handle_command(self.engine, replace(request, **changes))["authorized"])
                    self.assertIsNone(self.live.command(replace(request, **changes), "901", True))
        self.assertEqual(before, list(self.store.db.iterdump()))

    def test_pending_lookup_is_read_only_paginated_and_prioritizes_attention(self):
        ids = [self.identity] + [self.incident(index) for index in range(1, 8)]
        self.assess("unsure", identity=ids[1])
        self.assess("needs-attention", identity=ids[2])
        self.assess("looks-okay", identity=ids[3])
        before = list(self.store.db.iterdump())
        page = pending_page(self.engine)
        self.assertEqual((page["total"], page["pages"], len(page["items"])), (7, 2, 5))
        self.assertEqual([item["id"] for item in page["items"][:2]], [ids[2], ids[1]])
        self.assertEqual(len(pending_page(self.engine, 2)["items"]), 2)
        self.assertEqual(before, list(self.store.db.iterdump()))
        request = replace(self.request, command="pending", arguments=())
        rendered = self.live.command(request, "902", True)
        self.assertIn("!mod pending 2", rendered)
        self.assertLessEqual(units(rendered), 1900)
        for value in ("0", "-1", "x", "99"):
            self.assertFalse(handle_command(self.engine, replace(request, arguments=(value,)))["ok"])

    def test_completion_survives_expiration_pause_and_storage_reopen_with_same_evidence(self):
        self.assess()
        self.now += 300
        self.engine.set_paused(True)
        self.live.gap("reconnect")
        self.assertEqual(self.view()["state"], "historical")
        self.assertTrue(self.view()["complete"])
        self.assertEqual(pending_page(self.engine)["total"], 0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"moderation.sqlite3"
            with sqlite3.connect(path) as target:
                self.store.db.backup(target)
            with Store(str(path)) as store:
                engine = Engine(self.config, store, clock=lambda: self.now)
                self.assertEqual(pending_page(engine)["total"], 0)

    def test_new_evidence_reopens_review_and_old_click_cannot_override_new_assessment(self):
        self.assess()
        self.now += 1
        self.engine.process(MessageEvent("1", "10", "109", "50", self.text, self.now))
        current = self.store.incident(self.identity)
        self.assertGreater(current["revision"], 1)
        self.assertFalse(self.view()["complete"])
        self.assertEqual(pending_page(self.engine)["items"][0]["label"], "Review changed evidence")
        self.assess(revision=str(current["revision"]))
        old = self.assess("needs-attention")
        self.assertTrue(old["data"]["latest_complete"])
        self.assertEqual(self.view()["label"], "looks_okay")
        self.assertEqual(pending_page(self.engine)["total"], 0)
        old_report = self.live.render_snapshot(self.identity, 1)
        self.assertIn("**Needs attention**", old_report)
        self.assertIn("latest revision 2", old_report)

    def test_reply_binding_is_required_and_replayed_interaction_is_deduplicated(self):
        report = self.live.claim_report(); self.live.finish_report(report["id"], "700")
        request = parse_command("!mod assess looks-okay", "1", "20", "98", reply_to_message_id="700")
        result = self.live.command(request, "interaction:800", True)
        self.assertIn("Staff assessment saved", result)
        self.assertEqual(result.assessment["revision"], 1)
        self.assertIsNone(self.live.command(request, "interaction:800", True))
        self.assertEqual(self.store.db.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0], 1)
        forged = replace(request, reply_to_message_id="701")
        self.assertIn("Review not saved", self.live.command(forged, "interaction:801", True))

    def test_policy_change_requires_new_assessment_and_clock_rollback_does_not_complete(self):
        self.assess()
        self.now -= 1
        self.assertFalse(self.view()["complete"])
        self.now += 2
        config = replace(self.config, policy_version="new-policy")
        engine = Engine(config, self.store, clock=lambda:self.now)
        self.assertEqual(pending_page(engine)["total"], 1)
        result = handle_command(engine, self.request)
        self.assertTrue(result["ok"])
        self.assertEqual(pending_page(engine)["total"], 0)

    def test_history_is_bounded_and_follows_incident_retention(self):
        for _ in range(MAX_ASSESSMENTS):
            self.assertTrue(self.assess()["ok"])
        self.assertFalse(self.assess("needs-attention")["ok"])
        self.assertTrue(self.view()["complete"])
        self.store.db.execute("DELETE FROM incidents WHERE id=?", (self.identity,))
        self.assertEqual(self.store.db.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0], 0)

    def test_invalid_storage_or_out_of_scope_evidence_cannot_complete_review(self):
        self.assertFalse(self.assess(revision="999")["ok"])
        self.assess()
        self.store.db.execute(f"UPDATE {TABLE} SET label='ban'")
        self.assertEqual(self.view()["state"], "unavailable")
        self.assertEqual(pending_page(self.engine)["total"], 1)
        incident = self.store.incident(self.identity)
        incident["evidence"][0]["channel_id"] = "999"
        self.store.db.execute("UPDATE incidents SET evidence_json=?", (json.dumps(incident["evidence"]),))
        self.assertEqual(pending_page(self.engine)["total"], 0)

    def test_pending_excludes_records_past_retention_without_deleting_them(self):
        self.now += self.config.storage.retention_seconds + 1
        before = list(self.store.db.iterdump())
        self.assertEqual(pending_page(self.engine)["total"], 0)
        self.assertEqual(before, list(self.store.db.iterdump()))

    def test_older_report_never_shows_newer_jev_result_as_matching_its_snapshot(self):
        from liberdus_moderator.classification_view import incident_view
        self.now += 1
        self.engine.process(MessageEvent("1", "10", "109", "50", self.text, self.now))
        current = self.store.incident(self.identity)
        with patch("liberdus_moderator.classification_view.saved_classification", return_value={
            "outcome":"ok", "revision":2, "evidence_state":"current", "reason":"matches"}):
            view = incident_view(self.engine, current, 1)
        self.assertEqual(view["classification"]["evidence_state"], "historical")
        self.assertEqual(view["classification"]["reason"], "displayed_revision")

    def test_review_sections_keep_plain_punctuation_and_named_reviewer_without_ai(self):
        self.assess("needs-attention")
        request = replace(self.request, command="incident", arguments=(self.identity,))
        text = self.live.command(request, "903", True)
        self.assertNotIn("```", text)
        self.assertIn("### Saved message", text)
        self.assertIn("Incident ID: `"+self.identity, text)
        self.assertIn("scams\\.", text)  # Rendered punctuation is literal Markdown text.
        self.assertIn("Reviewed by <@98>", text)
        self.assertIn("### Staff assessment", text)
        self.assertIn("possible issue, acceptable, or needs context", text)
        self.assertLessEqual(units(text), 1900)


if __name__ == '__main__':
    unittest.main()
