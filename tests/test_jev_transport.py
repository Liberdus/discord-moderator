import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from liberdus_moderator.jev import evaluate, ProviderError, ENDPOINT


class Context:
    def __init__(self, value): self.value = value
    async def __aenter__(self): return self.value
    async def __aexit__(self, *args): return False


class Content:
    def __init__(self, chunks): self.chunks = chunks
    async def iter_chunked(self, _):
        for chunk in self.chunks: yield chunk


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def transport(self, status=200, chunks=None):
        self.response = SimpleNamespace(status=status, content=Content(chunks or [b'{"test":true}']))
        self.client = SimpleNamespace(post=Mock(return_value=Context(self.response)))
        self.session = Mock(return_value=Context(self.client))
        return SimpleNamespace(ClientSession=self.session, ClientTimeout=lambda **kwargs: kwargs)

    async def test_one_fixed_post_no_retry_proxy_redirect_or_key_leak(self):
        module = self.transport()
        with patch.dict("sys.modules", aiohttp=module):
            self.assertEqual(await evaluate(b"{}", "fake-key", 3), {"test": True})
        self.client.post.assert_called_once()
        args, kwargs = self.client.post.call_args
        self.assertEqual(args, (ENDPOINT,))
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer fake-key")
        self.assertFalse(self.session.call_args.kwargs["trust_env"])
        self.assertFalse(self.session.call_args.kwargs["auto_decompress"])

    async def test_missing_key_does_not_construct_client(self):
        module = self.transport()
        with patch.dict("sys.modules", aiohttp=module), self.assertRaises(ProviderError):
            await evaluate(b"{}", None, 3)
        self.session.assert_not_called()

    async def test_http_errors_redirects_malformed_and_oversized_bodies_are_bounded(self):
        for status, chunks in ((401, [b"SENSITIVE"]), (429, [b"SENSITIVE"]), (302, [b"SENSITIVE"]),
                               (200, [b"not JSON"]), (200, [b"x"*10000, b"x"*10000])):
            module = self.transport(status, chunks)
            with self.subTest(status=status), patch.dict("sys.modules", aiohttp=module):
                with self.assertRaises(ProviderError) as caught:
                    await evaluate(b"{}", "fake-key", 3)
                self.assertNotIn("SENSITIVE", str(caught.exception))
                self.client.post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
