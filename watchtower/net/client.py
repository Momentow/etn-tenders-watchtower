"""`PoliteClient`: the only HTTP client for portal traffic (ADR 0002).

Every request, including each redirect hop and each retry, waits for the shared per-host limiter. Transient failures
(connection errors, timeouts, 429 and 5xx gateway errors) are retried with exponential backoff, honouring `Retry-After`.
"""

import asyncio
import email.utils
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx

from watchtower.net.limiter import HostRateLimiter

USER_AGENT = (
    "WatchtowerBot/0.1 (automated public-procurement tender reader; "
    "+https://github.com/Momentow/etn-tenders-watchtower)"
)
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER_SECONDS = 60.0


class PoliteClient:
    def __init__(
        self,
        *,
        limiter: HostRateLimiter | None = None,
        user_agent: str = USER_AGENT,
        timeout: httpx.Timeout | float = DEFAULT_TIMEOUT,
        max_attempts: int = 3,
        backoff_base: float = 1.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.limiter = limiter or HostRateLimiter()
        self.user_agent = user_agent
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent},
            timeout=timeout,
            follow_redirects=True,
            event_hooks={"request": [self._before_request]},
            transport=transport,
        )

    async def _before_request(self, request: httpx.Request) -> None:
        # httpx calls this for every request it sends, redirect hops included, so nothing skips the limiter.
        await self.limiter.acquire(str(request.url))

    async def get(self, url: str, **kw) -> httpx.Response:
        """GET with politeness and retries. The body is read; check `response.status_code` yourself."""
        return await self.request("GET", url, **kw)

    async def request(self, method: str, url: str, **kw) -> httpx.Response:
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = await self._client.request(method, url, **kw)
            except httpx.TransportError:
                if attempt == self.max_attempts:
                    raise
                await asyncio.sleep(self._backoff(attempt))
                continue
            if response.status_code in RETRY_STATUSES and attempt < self.max_attempts:
                await asyncio.sleep(self._backoff(attempt, response))
                continue
            return response
        raise AssertionError("unreachable")

    @asynccontextmanager
    async def stream(self, method: str, url: str, **kw) -> AsyncIterator[httpx.Response]:
        """Streamed request for large downloads; the body is never fully in memory.

        Retries happen only before the response is handed over. Once you read the body, errors propagate.
        """
        response: httpx.Response | None = None
        for attempt in range(1, self.max_attempts + 1):
            request = self._client.build_request(method, url, **kw)
            try:
                response = await self._client.send(request, stream=True)
            except httpx.TransportError:
                if attempt == self.max_attempts:
                    raise
                await asyncio.sleep(self._backoff(attempt))
                continue
            if response.status_code in RETRY_STATUSES and attempt < self.max_attempts:
                await response.aclose()
                await asyncio.sleep(self._backoff(attempt, response))
                continue
            break
        assert response is not None
        try:
            yield response
        finally:
            await response.aclose()

    def _backoff(self, attempt: int, response: httpx.Response | None = None) -> float:
        if response is not None:
            retry_after = parse_retry_after(response.headers.get("Retry-After"))
            if retry_after is not None:
                return min(retry_after, MAX_RETRY_AFTER_SECONDS)
        delay = self.backoff_base * 2 ** (attempt - 1)
        return delay + random.uniform(0, delay / 2)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "PoliteClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()


def parse_retry_after(value: str | None) -> float | None:
    """`Retry-After` as seconds from now: either delta-seconds or an HTTP date. None if absent or unparseable."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())
