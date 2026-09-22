from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
import test_public_scope as public_tests
from liberdus_moderator.hermes_adapter import PilotClient
from liberdus_moderator.connection_health import state


class ConnectionAdapterTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=public_tests.PublicScopeTests.asyncSetUp
    asyncTearDown=public_tests.PublicScopeTests.asyncTearDown
    message=public_tests.PublicScopeTests.message
    drain=public_tests.PublicScopeTests.drain

    async def test_disconnect_records_once_and_successful_resume_records_recovery(self):
        self.adapter.client.ws=SimpleNamespace(_close_code=None,socket=SimpleNamespace(close_code=1000))
        self.adapter.client.user=SimpleNamespace(id=99)
        self.adapter.lost_connection();self.adapter.lost_connection()
        self.assertEqual(state(self.adapter.live.engine)['disconnects'],1)
        await self.adapter.ready(resumed=True)
        self.assertTrue(self.adapter.online)
        self.assertEqual(state(self.adapter.live.engine)['last_recovery'],'resumed')
        self.assertEqual(state(self.adapter.live.engine)['last_close_code'],1000)
        self.assertIsNotNone(state(self.adapter.live.engine)['last_duration'])
        self.assertEqual(self.adapter.store.get_setting('last_coverage_gap')['reason'],'reconnect')
        self.channel.send.assert_not_awaited()

    async def test_sdk_resume_and_new_ready_are_distinguished(self):
        target=SimpleNamespace(ready=AsyncMock())
        fake=SimpleNamespace(adapter=target)
        await PilotClient.on_resumed(fake)
        target.ready.assert_awaited_once_with(resumed=True)
        target.ready.reset_mock()
        await PilotClient.on_ready(fake)
        target.ready.assert_awaited_once_with()


if __name__=='__main__':unittest.main()
