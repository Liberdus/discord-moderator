import subprocess
import sys
import unittest
from unittest.mock import patch

from liberdus_moderator.commands import CommandRequest, handle_command
from liberdus_moderator.config import Config
from liberdus_moderator.engine import Engine
from liberdus_moderator.live import LiveSession
from liberdus_moderator.models import MessageEvent
from liberdus_moderator.selftest import format_summary, run_selftest
from liberdus_moderator.storage import Store


class SelfTestTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.config = Config("2", "299", ("210", "211", "212"), ("220",), ("298",))
        self.engine = Engine(self.config, self.store, clock=lambda: 1000)
        self.live = LiveSession(self.engine)
        self.request = CommandRequest("2", "220", "298", "selftest")

    def test_real_checks_pass_in_separate_memory_stores_without_network(self):
        paths = []

        def memory_store(path):
            paths.append(path)
            self.assertEqual(path, ":memory:")
            return Store(path)

        with patch("liberdus_moderator.selftest.Store", side_effect=memory_store), \
                patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")):
            result = run_selftest()
        self.assertTrue(result["passed"], result)
        self.assertEqual(len(paths), 9)
        self.assertEqual(result["ai_calls"], 0)
        summary = format_summary(result)
        self.assertIn("9/9 passed", summary)
        self.assertIn("Live Discord events, permissions and actual restart: not tested", summary)
        self.assertLess(len(summary), 1900)

    def test_running_while_paused_preserves_policy_history_and_state(self):
        for identity, channel in enumerate(("210", "211", "212"), 500):
            self.engine.process(MessageEvent("2", channel, str(identity), "250",
                                            "Existing live evidence must be preserved.", 1000))
        self.engine.set_paused(True)
        before = "\n".join(self.store.db.iterdump())
        response = handle_command(self.engine, self.request)
        self.assertTrue(response["data"]["passed"])
        self.assertEqual(before, "\n".join(self.store.db.iterdump()))
        self.assertEqual(response["destination"]["channel_id"], "220")
        self.assertEqual(response["allowed_mentions"]["parse"], [])

    def test_authorization_and_arguments_gate_runner(self):
        from dataclasses import replace

        with patch("liberdus_moderator.selftest.run_selftest") as runner:
            for wrong in (replace(self.request, user_id="250"), replace(self.request, channel_id="210"),
                          replace(self.request, guild_id="3")):
                self.assertIsNone(self.live.command(wrong, "600", True))
            result = handle_command(self.engine, replace(self.request, arguments=("arbitrary-input",)))
            self.assertFalse(result["ok"])
            runner.assert_not_called()

    def test_live_command_is_deduplicated_and_formats_private_result(self):
        with patch("liberdus_moderator.selftest.run_selftest", wraps=run_selftest) as runner:
            self.assertIn("9/9 passed", self.live.command(self.request, "600", True))
            self.assertIsNone(self.live.command(self.request, "600", True))
            self.assertEqual(runner.call_count, 1)
        self.assertEqual(self.engine.status()["message_count"], 0)
        self.assertEqual(self.engine.status()["incident_count"], 0)

    def test_failed_checks_do_not_pass_or_echo_exception_data(self):
        with patch.object(Engine, "process", side_effect=RuntimeError("private failure details")):
            result = run_selftest()
        self.assertFalse(result["passed"])
        self.assertIn("FAIL:", format_summary(result))
        self.assertNotIn("private failure details", str(result))

    def test_optimized_python_still_checks_expectations(self):
        code = (
            "from liberdus_moderator.selftest import _require, run_selftest\n"
            "try:\n _require(False)\n"
            "except ValueError:\n pass\n"
            "else:\n raise RuntimeError('Checks were optimized away')\n"
            "if not run_selftest()['passed']: raise RuntimeError('Self-test failed')\n"
        )
        result = subprocess.run([sys.executable, "-O", "-c", code], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
