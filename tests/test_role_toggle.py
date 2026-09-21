import unittest
from dataclasses import replace
from liberdus_moderator.commands import CommandRequest, handle_command
from liberdus_moderator.engine import Engine
from liberdus_moderator.live import LiveSession
from liberdus_moderator.display import framed, panel
import test_screening as screening_tests
from test_screening import config

class RoleToggleTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = screening_tests.ScreeningTests.asyncSetUp
    asyncTearDown = screening_tests.ScreeningTests.asyncTearDown
    event = screening_tests.ScreeningTests.event
    async def test_toggle_authorization_persistence_and_inflight(self):
        self.engine.config = config(exempt_role_ids=('77',))
        self.engine = Engine(self.engine.config, self.store, clock=lambda:self.now)
        self.worker.engine = self.engine
        self.worker.config = self.engine.config
        self.worker.settings = self.engine.config.classifier
        def command(user='98', channel='20', arg='off'):
            return handle_command(self.engine, CommandRequest('1',channel,user,'exempt-role',arguments=(arg,)))
        self.assertFalse(command(user='50')['authorized'])
        self.assertFalse(command(channel='10')['authorized'])
        event=self.event(author_role_ids=('77',))
        self.engine.process(event)
        self.assertIsNone(self.worker.snapshot(event.message_id))
        self.assertTrue(command()['ok'])
        job=self.worker.snapshot(event.message_id)
        self.assertIsNotNone(job)
        restarted=Engine(self.engine.config,self.store,clock=lambda:self.now)
        self.assertFalse(restarted.role_exempt(('77',)))
        self.assertFalse(command(arg='bad')['ok'])
        self.assertTrue(command(arg='on')['ok'])
        self.assertFalse(self.worker.current(job))
        self.assertEqual(self.store.get_setting('screening_total_calls',0),0)

class DisplayTests(unittest.TestCase):
    def test_mobile_width_and_link_preservation(self):
        result=panel('Status',['A long line ' * 12])
        self.assertTrue(all(len(line)<=32 for line in result.split('```')[1].splitlines()))
        text=result+'\n[Open message](https://discord.com/channels/1/2/3)'
        self.assertIn('[Open message](https://discord.com/channels/1/2/3)',framed(text))
        self.assertLess(len(framed('x'*1900)),2000)

    def test_help_status_and_private_toggle(self):
        from liberdus_moderator.storage import Store
        store=Store(':memory:')
        self.addCleanup(store.close)
        engine=Engine(config(exempt_role_ids=('77',)),store)
        live=LiveSession(engine)
        for i,name in enumerate(('help','status','exempt-role')):
            result=live.command(CommandRequest('1','20','98',name),str(100+i),True)
            self.assertIn('```', result)
            self.assertLess(len(framed(result)),2000)
        self.assertIsNone(live.command(CommandRequest('1','10','98','exempt-role',arguments=('off',)),'200',True))
