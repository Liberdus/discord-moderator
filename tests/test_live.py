from dataclasses import replace
import unittest

from liberdus_moderator.commands import CommandRequest
from liberdus_moderator.config import Config
from liberdus_moderator.engine import Engine
from liberdus_moderator.live import LiveSession, delivery_nonce, parse_command
from liberdus_moderator.models import MessageEvent
from liberdus_moderator.storage import Store


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.config = Config("1", "99", ("10", "11", "12"), ("20",), ("98",))
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.now = 1000
        self.engine = Engine(self.config, self.store, clock=lambda: self.now)
        self.live = LiveSession(self.engine)

    def pattern(self):
        for i, channel in enumerate(self.config.monitored_channel_ids):
            self.engine.process(MessageEvent("1", channel, str(100+i), "50", "Repeated community offer for testing only.", 995+i))

    def test_report_claim_sent_and_no_replay(self):
        self.pattern()
        report = self.live.claim_report()
        self.assertEqual(report["payload"]["channel_id"], "20")
        self.assertIsNone(self.live.claim_report())
        self.live.finish_report(report["id"], "12345")
        self.assertEqual(self.store.db.execute("SELECT status FROM reports").fetchone()[0], "sent")
        self.assertEqual(self.store.db.execute("SELECT message_id FROM deliveries").fetchone()[0], "12345")

    def test_crash_after_claim_is_uncertain_and_never_retried(self):
        self.pattern()
        self.live.claim_report()
        restarted = LiveSession(self.engine)
        self.assertEqual(restarted.status(True)["uncertain_reports"], 1)
        self.assertIsNone(restarted.claim_report())

    def test_send_timeout_retains_uncertain_outcome(self):
        self.pattern()
        report = self.live.claim_report()
        self.live.finish_report(report["id"])
        self.assertIsNone(self.live.claim_report())
        self.assertEqual(self.live.status(True)["uncertain_reports"], 1)

    def test_expired_or_changed_evidence_cannot_be_claimed(self):
        self.pattern()
        self.now = 2000
        self.assertIsNone(self.live.claim_report())
        self.assertEqual(self.store.db.execute("SELECT status FROM reports").fetchone()[0], "cancelled")

    def test_payload_scope_tampering_cancels_delivery(self):
        self.pattern()
        self.store.db.execute("UPDATE reports SET payload_json=json_set(payload_json,'$.channel_id','10')")
        self.assertIsNone(self.live.claim_report())

    def test_gap_invalidates_pending_evidence_without_auto_resuming_pause(self):
        self.pattern()
        self.engine.set_paused(True)
        self.live.gap("disconnect")
        self.assertTrue(self.live.status(False)["paused"])
        self.assertEqual(self.live.status(False)["message_count"], 0)
        self.assertIsNone(self.live.claim_report())

    def test_commands_require_channel_and_operator_and_deduplicate(self):
        request = CommandRequest("1", "20", "98", "pause")
        for wrong in (replace(request, user_id="50"), replace(request, channel_id="10"), replace(request, guild_id="2")):
            self.assertIsNone(self.live.command(wrong, "100", True))
        self.assertFalse(self.engine.status()["paused"])
        self.assertIn("paused", self.live.command(request, "100", True))
        self.assertIsNone(self.live.command(replace(request, command="resume"), "100", True))
        self.assertTrue(self.engine.status()["paused"])

    def test_explain_shows_saved_text_and_explicit_source_links(self):
        self.pattern()
        incident = self.store.incidents()[0]
        result = self.live.command(CommandRequest("1", "20", "98", "explain", arguments=(incident["id"],)), "200", True)
        self.assertIn("Repeated community", result)
        self.assertIn("Open message 1", result)
        self.assertNotIn("<@", result)
        self.assertIn(incident["id"], result)

    def test_parser_is_explicit_and_nonce_stable_bounded(self):
        self.assertIsNone(parse_command("please pause", "1", "20", "98"))
        self.assertIsNone(parse_command("!mod " + "x"*1001, "1", "20", "98"))
        self.assertEqual(parse_command("!mod status", "1", "20", "98").command, "status")
        self.assertEqual(delivery_nonce("report:123"), delivery_nonce("report:123"))
        self.assertNotEqual(delivery_nonce("report:123"), delivery_nonce("command:123"))
        self.assertLessEqual(len(delivery_nonce("report:123")), 25)


if __name__ == "__main__":
    unittest.main()
