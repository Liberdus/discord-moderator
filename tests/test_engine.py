"""Behavior tests for durable report-only decisions; no network or model calls."""

from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from liberdus_moderator.config import Config
from liberdus_moderator.commands import CommandRequest, handle_command
from liberdus_moderator.engine import Engine
from liberdus_moderator.models import MessageEvent
from liberdus_moderator.storage import Store


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = str(Path(self.temporary.name) / "moderation.sqlite3")
        self.now = 1010.0
        self.config = self.make_config()
        self.store = Store(self.path)
        self.addCleanup(lambda: self.store.close())
        self.engine = Engine(self.config, self.store, clock=lambda: self.now)

    def make_config(self, **changes):
        data = {
            "schema_version": 1,
            "policy_version": "test-1",
            "scope": {
                "guild_id": "1",
                "bot_user_id": "99",
                "monitored_channel_ids": ["10", "11", "12", "13"],
                "command_channel_ids": ["20"],
                "operator_user_ids": ["98"],
                "operator_role_ids": ["97"],
                "log_channel_id": "21",
            },
            "rules": {"blocked_domains": ["blocked.example"]},
            "storage": {"database_path": self.path},
        }
        for key, value in changes.items():
            if isinstance(value, dict):
                data[key].update(value)
            else:
                data[key] = value
        return Config.from_dict(data)

    def configure(self, **changes):
        self.config = self.make_config(**changes)
        self.engine = Engine(self.config, self.store, clock=lambda: self.now)

    def restart(self):
        self.store.close()
        self.store = Store(self.path)
        self.engine = Engine(self.config, self.store, clock=lambda: self.now)

    @staticmethod
    def event(message_id="100", channel_id="10", **changes):
        values = {
            "guild_id": "1", "channel_id": channel_id, "message_id": message_id,
            "author_id": "50", "content": "Please visit our amazing community event today!",
            "created_at": 1000.0,
        }
        values.update(changes)
        return MessageEvent.from_dict(values)

    def pattern(self, channels=("10", "11", "12")):
        events = [self.event(str(100 + index), channel, created_at=1000.0 + index)
                  for index, channel in enumerate(channels)]
        results = [self.engine.process(event) for event in events]
        return events, results

    def count(self, table):
        self.assertIn(table, ("messages", "incidents", "reports", "incident_versions"))
        return self.store.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def command(self, name):
        return handle_command(self.engine, CommandRequest("1", "20", "98", name))

    def test_out_of_scope_and_own_bot_events_never_enter_evidence(self):
        variants = [
            {"guild_id": "2"}, {"channel_id": "20"}, {"channel_id": "999"},
            {"author_id": "99"}, {"is_bot": True}, {"is_webhook": True},
        ]
        for changes in variants:
            with self.subTest(changes=changes):
                self.assertEqual(self.engine.process(self.event(**changes))["disposition"], "ignored")
        self.assertEqual(self.count("messages"), 0)
        self.assertEqual(self.count("incidents"), 0)
        self.assertEqual(self.count("reports"), 0)

    def test_no_rule_match_is_not_an_ai_or_safety_clearance(self):
        decision = self.engine.process(self.event(content="A context-dependent statement."))
        self.assertEqual(decision["disposition"], "no_match")
        self.assertIn("not_ai_clearance", decision["reason"])
        self.assertEqual(decision["coverage"], "code_only")
        self.assertEqual(decision["ai_calls"], 0)
        self.assertEqual(decision["public_actions"], [])

    def test_cross_channel_pattern_is_one_incident_and_replay_stays_deduplicated(self):
        events, results = self.pattern()
        self.assertEqual([item["disposition"] for item in results], ["no_match", "no_match", "review"])
        incident_id = results[-1]["incident_ids"][0]
        self.assertEqual(self.count("incidents"), 1)
        self.assertEqual(len(self.store.reports()), 1)
        self.restart()
        for event in events:
            self.assertEqual(self.engine.process(event)["reason"], "duplicate_event")
        self.assertEqual(self.count("incidents"), 1)
        self.assertEqual(self.count("incident_versions"), 1)
        self.assertEqual(self.store.reports()[0]["incident_id"], incident_id)

    def test_repeated_messages_in_two_channels_do_not_count_as_three(self):
        _, results = self.pattern(("10", "10", "11"))
        self.assertTrue(all(item["disposition"] == "no_match" for item in results))
        self.assertEqual(self.count("incidents"), 0)

    def test_received_together_does_not_replace_original_post_times(self):
        events = [self.event(str(100 + index), channel, created_at=timestamp)
                  for index, (channel, timestamp) in enumerate(
                      (("10", 600.0), ("11", 800.0), ("12", 1000.0)))]
        for event in reversed(events):
            self.assertEqual(self.engine.process(event)["disposition"], "no_match")
        self.assertEqual(self.count("incidents"), 0)

    def test_edit_does_not_turn_old_crossposts_into_a_new_burst(self):
        for index, channel in enumerate(("10", "11", "12")):
            event = self.event(str(100 + index), channel, created_at=700.0 + index)
            self.engine.process(event)
            self.engine.process(replace(event, edited_at=1009.0, content="Edited community event announcement repeated now"))
        self.assertEqual(self.count("incidents"), 0)

    def test_newer_edit_survives_restart_and_stale_replay_cannot_restore_content(self):
        original = self.event()
        current = replace(original, content="A corrected message", edited_at=1009.0)
        self.engine.process(original)
        self.engine.process(current)
        self.restart()
        self.assertEqual(self.engine.process(original)["reason"], "stale_event")
        row = self.store.db.execute("SELECT version, event_json FROM messages WHERE message_id='100'").fetchone()
        self.assertEqual(row["version"], current.version)
        self.assertEqual(json.loads(row["event_json"])["content"], current.content)

    def test_same_source_timestamp_with_conflicting_content_is_rejected(self):
        current = self.event(edited_at=1009.0)
        self.engine.process(current)
        conflicting = replace(current, content="Conflicting timestamp payload")
        self.assertEqual(self.engine.process(conflicting)["reason"], "conflicting_event_version")
        row = self.store.db.execute("SELECT version FROM messages WHERE message_id='100'").fetchone()
        self.assertEqual(row["version"], current.version)

    def test_changed_message_identity_cannot_overwrite_stored_evidence(self):
        original = self.event()
        self.engine.process(original)
        forged = replace(original, author_id="51", edited_at=1009.0)
        self.assertEqual(self.engine.process(forged)["reason"], "message_identity_conflict")
        row = self.store.db.execute("SELECT author_id FROM messages WHERE message_id='100'").fetchone()
        self.assertEqual(row["author_id"], "50")

    def test_edit_breaking_threshold_withdraws_group_and_cancels_pending_report(self):
        events, results = self.pattern()
        incident_id = results[-1]["incident_ids"][0]
        corrected = replace(events[1], content="A unique corrected message", edited_at=1009.0)
        self.assertEqual(self.engine.process(corrected)["disposition"], "no_match")
        self.restart()
        incident = self.store.incident(incident_id)
        self.assertEqual(incident["status"], "withdrawn")
        self.assertEqual(incident["revision"], 2)
        self.assertEqual(incident["history"][-1]["status"], "withdrawn")
        self.assertEqual(self.store.reports(), [])
        self.assertEqual(self.engine.process(events[1])["reason"], "stale_event")
        self.assertEqual(self.store.incident(incident_id)["status"], "withdrawn")

    def test_edit_that_keeps_threshold_updates_one_pending_report(self):
        events, results = self.pattern(("10", "11", "12", "13"))
        incident_id = results[-1]["incident_ids"][0]
        self.engine.process(replace(events[0], content="This copy is now different", edited_at=1009.0))
        incident = self.store.incident(incident_id)
        self.assertEqual(incident["status"], "open")
        self.assertEqual(len(incident["evidence"]), 3)
        self.assertNotIn(events[0].message_id, {item["message_id"] for item in incident["evidence"]})
        reports = self.store.reports()
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["incident_revision"], incident["revision"])
        self.assertEqual(reports[0]["payload"]["incident_revision"], incident["revision"])

    def test_unsupported_attachment_edit_invalidates_previously_supported_group(self):
        events, results = self.pattern()
        changed = replace(events[0], has_attachments=True, edited_at=1009.0)
        self.assertEqual(self.engine.process(changed)["disposition"], "unsupported")
        self.assertEqual(self.store.incident(results[-1]["incident_ids"][0])["status"], "withdrawn")
        self.assertEqual(self.store.reports(), [])

    def test_report_payload_keeps_attacker_text_and_mentions_out(self):
        event = self.event(content="@everyone <@98> follow https://blocked.example/now")
        self.assertEqual(self.engine.process(event)["disposition"], "review")
        report = self.store.reports()[0]["payload"]
        self.assertEqual(report["channel_id"], "20")
        self.assertNotIn("@everyone", report["content"])
        self.assertNotIn("blocked.example", report["content"])
        self.assertEqual(report["allowed_mentions"]["parse"], [])
        self.assertFalse(report["allowed_mentions"]["replied_user"])

    def test_report_queue_capacity_keeps_incidents_and_does_not_repeat_detection(self):
        self.configure(storage={"max_pending_reports": 1})
        first = self.event(content="First https://blocked.example/one")
        second = self.event("101", "11", content="Second https://blocked.example/two")
        self.engine.process(first)
        decision = self.engine.process(second)
        self.assertEqual(decision["disposition"], "review")
        self.assertEqual(self.count("incidents"), 2)
        self.assertEqual(self.count("messages"), 2)
        self.assertEqual(len(self.store.reports()), 1)
        self.assertGreater(self.store.get_setting("dropped_reports", 0), 0)
        self.restart()
        self.assertEqual(self.engine.process(second)["reason"], "duplicate_event")
        self.assertEqual(self.count("incidents"), 2)

    def test_message_capacity_pauses_and_cancels_reports_that_cannot_be_revalidated(self):
        self.configure(storage={"max_messages": 3})
        self.pattern()
        overflow = self.event("200", "13")
        self.assertEqual(self.engine.process(overflow)["disposition"], "capacity")
        self.assertEqual(self.count("messages"), 0)
        self.assertTrue(self.engine.status()["paused"])
        self.assertEqual(self.store.reports(), [])
        self.restart()
        self.assertTrue(self.engine.status()["paused"])
        self.assertEqual(self.engine.process(overflow)["reason"], "moderation_paused")

    def test_evidence_version_capacity_cannot_leave_old_pending_report_sendable(self):
        self.configure(storage={"max_evidence_versions": 1})
        events, _ = self.pattern()
        correction = replace(events[1], edited_at=1009.0)
        self.assertEqual(self.engine.process(correction)["disposition"], "capacity")
        self.assertTrue(self.engine.status()["paused"])
        self.assertEqual(self.store.reports(), [])
        incident = self.store.incidents()[0]
        history = self.store.incident(incident["id"])["history"]
        self.assertNotEqual(incident["status"], "open")
        self.assertEqual(history[-1]["status"], incident["status"])
        # A final status record is permitted beyond the configured evidence cap.
        self.assertEqual(len(history), 2)

    def test_withdrawal_can_record_terminal_history_at_evidence_capacity(self):
        self.configure(storage={"max_evidence_versions": 1})
        events, results = self.pattern()
        correction = replace(events[1], content="Unique corrected text", edited_at=1009.0)
        self.assertEqual(self.engine.process(correction)["disposition"], "no_match")
        self.assertFalse(self.engine.status()["paused"])
        incident = self.store.incident(results[-1]["incident_ids"][0])
        self.assertEqual(incident["status"], "withdrawn")
        self.assertEqual(len(incident["history"]), 2)
        self.assertEqual(incident["history"][-1]["status"], "withdrawn")
        self.assertEqual(self.store.reports(), [])

    def test_incident_capacity_rolls_back_unrecorded_message_and_pauses(self):
        self.configure(storage={"max_incidents": 1})
        self.engine.process(self.event(content="First https://blocked.example/one"))
        overflow = self.event("101", "11", content="Second https://blocked.example/two")
        self.assertEqual(self.engine.process(overflow)["disposition"], "capacity")
        self.assertEqual(self.count("incidents"), 1)
        self.assertEqual(self.count("messages"), 0)
        self.assertTrue(self.engine.status()["paused"])
        self.assertEqual(self.store.reports(), [])

    def test_policy_change_cancels_prior_pending_reports_and_clears_old_evidence(self):
        _, results = self.pattern()
        self.configure(policy_version="test-2")
        self.assertEqual(self.count("messages"), 0)
        self.assertEqual(self.store.reports(), [])
        incident = self.store.incident(results[-1]["incident_ids"][0])
        self.assertEqual(incident["status"], "policy_changed")
        self.assertEqual(incident["history"][-1]["status"], "policy_changed")
        self.assertEqual(incident["history"][-1]["revision"], incident["revision"])

    def test_expiry_preserves_a_final_consistent_audit_revision(self):
        _, results = self.pattern()
        incident_id = results[-1]["incident_ids"][0]
        self.now = 1130.0
        self.engine.process(self.event("200", "13", author_id="51", created_at=1129.0))
        incident = self.store.incident(incident_id)
        self.assertEqual(incident["status"], "expired")
        self.assertEqual(incident["history"][-1]["status"], "expired")
        self.assertEqual(incident["history"][-1]["revision"], incident["revision"])
        self.assertEqual(self.store.reports(), [])

    def test_ignored_edit_during_pause_cannot_complete_a_stale_pattern_after_resume(self):
        first = self.event()
        self.engine.process(first)
        self.engine.process(self.event("101", "11"))
        self.assertTrue(self.command("pause")["ok"])
        correction = replace(first, content="A corrected unique message", edited_at=1009.0)
        self.assertEqual(self.engine.process(correction)["reason"], "moderation_paused")
        self.restart()
        self.assertTrue(self.command("resume")["ok"])
        self.assertEqual(self.engine.process(first)["reason"], "before_current_coverage_window")
        self.assertEqual(self.engine.process(correction)["reason"], "before_current_coverage_window")
        decision = self.engine.process(self.event("102", "12", created_at=self.now))
        self.assertEqual(decision["disposition"], "no_match")
        self.assertEqual(decision["new_incident_ids"], [])
        self.assertEqual(self.count("incidents"), 0)
        self.assertEqual(self.count("messages"), 1)

    def test_pause_preserves_history_but_invalidates_current_pattern_window(self):
        _, results = self.pattern()
        incident_id = results[-1]["incident_ids"][0]
        self.assertTrue(self.command("pause")["ok"])
        self.assertEqual(self.count("messages"), 0)
        incident = self.store.incident(incident_id)
        self.assertNotEqual(incident["status"], "open")
        self.assertEqual(incident["history"][-1]["status"], incident["status"])
        self.assertEqual(len(incident["history"][0]["evidence"]), 3)
        self.assertEqual(self.store.reports(), [])
        self.assertTrue(self.command("resume")["ok"])
        self.assertEqual(self.engine.process(self.event("200", "13", created_at=self.now))["disposition"], "no_match")

    def test_capacity_pause_does_not_reuse_pre_gap_evidence_after_resume(self):
        self.configure(storage={"max_messages": 2})
        first = self.event()
        self.engine.process(first)
        self.engine.process(self.event("101", "11"))
        self.assertEqual(self.engine.process(self.event("102", "12"))["disposition"], "capacity")
        self.assertEqual(self.count("messages"), 0)
        self.engine.process(replace(first, content="Corrected during outage", edited_at=1009.0))
        self.restart()
        self.assertTrue(self.command("resume")["ok"])
        self.assertEqual(self.engine.process(first)["reason"], "before_current_coverage_window")
        self.assertEqual(self.engine.process(self.event("103", "13", created_at=self.now))["disposition"], "no_match")
        self.assertEqual(self.count("incidents"), 0)

    def test_stale_engine_cannot_process_or_authorize_after_policy_activation(self):
        old_engine = self.engine
        new_config = self.make_config(scope={"operator_user_ids": ["96"], "operator_role_ids": []})
        with Store(self.path) as other_store:
            new_engine = Engine(new_config, other_store, clock=lambda: self.now)
            result = old_engine.process(self.event(created_at=self.now))
            self.assertEqual(result["disposition"], "ignored")
            self.assertEqual(result["reason"], "configuration_changed_reload_required")
            denied = handle_command(old_engine, CommandRequest("1", "20", "98", "pause"))
            self.assertFalse(denied["authorized"])
            self.assertFalse(denied["ok"])
            self.assertFalse(new_engine.status()["paused"])
            allowed = handle_command(new_engine, CommandRequest("1", "20", "96", "status"))
            self.assertTrue(allowed["authorized"])
            self.assertEqual(self.count("messages"), 0)

    def test_retention_prunes_old_records_before_accepting_new_evidence(self):
        self.configure(storage={"retention_seconds": 120})
        self.pattern()
        self.now = 1200.0
        self.engine.process(self.event("200", "13", created_at=1199.0, content="Recent harmless text"))
        self.assertEqual(self.count("messages"), 1)
        self.assertEqual(self.count("incidents"), 0)
        self.assertEqual(self.count("reports"), 0)
        self.assertEqual(self.count("incident_versions"), 0)


class StoreTest(unittest.TestCase):
    def test_existing_unrelated_database_is_rejected_without_replacing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "unrelated.sqlite3")
            with sqlite3.connect(path) as database:
                database.execute("CREATE TABLE other_project(value TEXT)")
                database.execute("INSERT INTO other_project VALUES('preserve me')")
            with self.assertRaisesRegex(ValueError, "not a Liberdus"):
                Store(path)
            with sqlite3.connect(path) as database:
                self.assertEqual(database.execute("SELECT value FROM other_project").fetchone()[0], "preserve me")

    def test_database_file_is_private_and_symlink_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "moderation.sqlite3"
            with Store(str(path)):
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            link = Path(directory) / "link.sqlite3"
            link.symlink_to(path)
            with self.assertRaisesRegex(ValueError, "symlink"):
                Store(str(link))


if __name__ == "__main__":
    unittest.main()
