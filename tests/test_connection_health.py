import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
from liberdus_moderator.config import Config
from liberdus_moderator.engine import Engine
from liberdus_moderator.storage import Store
from liberdus_moderator.connection_health import KEY, record, state, summary
from liberdus_moderator.commands import CommandRequest, handle_command


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(':memory:');self.addCleanup(self.store.close);self.now=1000.
        self.engine=Engine(Config('1','99',('10',),('20',),('98',)),self.store,clock=lambda:self.now)

    def test_connection_metadata_records_resume_time_code_and_bounded_history(self):
        record(self.engine,'new_session');record(self.engine,'disconnected',1000)
        self.now+=3.5;record(self.engine,'resumed')
        value=state(self.engine)
        self.assertEqual((value['disconnects'],value['resumes'],value['sessions']),(1,1,1))
        self.assertEqual(value['last_duration'],3.5)
        self.assertEqual(value['last_close_code'],1000)
        for _ in range(10):record(self.engine,'new_session')
        self.assertEqual(len(state(self.engine)['history']),8)
        self.assertLess(len(summary(self.engine,True)),1900)

    def test_command_remains_private_and_invalid_metadata_is_not_echoed(self):
        self.assertFalse(handle_command(self.engine,CommandRequest('1','10','98','connection'))['authorized'])
        self.assertTrue(handle_command(self.engine,CommandRequest('1','20','98','connection'))['ok'])
        self.store.set_setting(KEY,{'secret':'not-for-output'})
        text=summary(self.engine,True)
        self.assertNotIn('not-for-output',text)
        self.assertIn('unavailable',text)

    def test_invalid_close_code_and_clock_rollback_have_no_false_duration(self):
        record(self.engine,'disconnected','token-secret')
        self.now-=1;record(self.engine,'new_session')
        self.assertIsNone(state(self.engine)['last_duration'])
        self.assertIsNone(state(self.engine)['last_close_code'])
        self.assertNotIn('token-secret',summary(self.engine,True))

    def test_owner_diagnostic_only_emits_fixed_labels_times_and_numeric_codes(self):
        spec=importlib.util.spec_from_file_location('check_disconnects',Path(__file__).resolve().parents[1]/'scripts/check_disconnects.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        result=module.scan('2026-09-22 04:01:03 discord.client: Shard ID None has successfully RESUMED session secret-session\n'
                           '2026-09-22 04:02:01 discord.errors.ConnectionClosed: WebSocket closed with 1000 secret-token\n'
                           'Private Discord text and credentials never printed')
        self.assertEqual(result['counts']['discord_session_resumed'],1)
        self.assertEqual(result['last_events'][1]['close_code'],1000)
        self.assertNotIn('secret',str(result))
        self.assertNotIn('credentials',str(result))


if __name__=='__main__':unittest.main()
