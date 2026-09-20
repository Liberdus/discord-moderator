"""Small, fixed-endpoint JEV transport. Imported only by the shadow worker.

No SDK retries, redirects, proxy-environment inheritance, or agent tool dispatch.
"""

import json

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MAX_RESPONSE_BYTES = 16384


class ProviderError(Exception):
    """A fixed diagnostic identifier; never contains provider bodies or secrets."""


async def evaluate(payload, key, timeout_seconds):
    if not isinstance(key, str) or not key.strip():
        raise ProviderError("missing_key")
    import aiohttp

    # One fresh session and one POST. In particular, do not retry an uncertain POST.
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_seconds),
                                     trust_env=False, auto_decompress=False) as client:
        async with client.post(ENDPOINT, data=payload,
                               headers={"Authorization": "Bearer " + key,
                                        "Content-Type": "application/json", "Accept-Encoding": "identity"},
                               allow_redirects=False) as response:
            if response.status != 200:
                code = {401: "authentication_failed", 403: "access_denied", 429: "rate_limited",
                        529: "provider_overloaded"}.get(response.status, "http_error")
                raise ProviderError(code)
            body = bytearray()
            async for chunk in response.content.iter_chunked(4096):
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ProviderError("response_too_large")
            try:
                return json.loads(body)
            except (ValueError, UnicodeError):
                raise ProviderError("invalid_json") from None
