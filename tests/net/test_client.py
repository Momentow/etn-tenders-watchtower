import asyncio
import time

import httpx
import pytest

from watchtower.net import USER_AGENT, HostRateLimiter, PoliteClient
from watchtower.net.client import parse_retry_after


class Recorder:
    """Mock transport that records when each request hit the 'server'."""

    def __init__(self, handler=None):
        self.hits: list[tuple[float, str]] = []
        self.requests: list[httpx.Request] = []
        self._handler = handler or (lambda req, n: httpx.Response(200, text="ok"))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.hits.append((time.monotonic(), request.url.host))
        self.requests.append(request)
        return self._handler(request, len(self.requests))

    def gaps(self, host: str) -> list[float]:
        times = [t for t, h in self.hits if h == host]
        return [b - a for a, b in zip(times, times[1:], strict=False)]


def make_client(recorder, *, interval=0.2, **kw) -> PoliteClient:
    return PoliteClient(
        limiter=HostRateLimiter(interval), transport=httpx.MockTransport(recorder), backoff_base=0.01, **kw
    )


async def test_one_request_per_second_per_host():
    """The acceptance test: the real 1 s limit, with concurrent callers."""
    rec = Recorder()
    async with PoliteClient(transport=httpx.MockTransport(rec)) as client:
        start = time.monotonic()
        await asyncio.gather(*(client.get(f"https://portal.example/p{i}") for i in range(3)))
        elapsed = time.monotonic() - start
    assert len(rec.hits) == 3
    assert all(g >= 0.99 for g in rec.gaps("portal.example"))
    assert elapsed >= 1.99


async def test_hosts_do_not_wait_for_each_other():
    rec = Recorder()
    async with make_client(rec, interval=1.0) as client:
        start = time.monotonic()
        await asyncio.gather(*(client.get(f"https://h{i}.example/") for i in range(5)))
        assert time.monotonic() - start < 0.5


async def test_limit_shared_between_clients_and_case_insensitive():
    rec = Recorder()
    limiter = HostRateLimiter(0.2)
    a = PoliteClient(limiter=limiter, transport=httpx.MockTransport(rec))
    b = PoliteClient(limiter=limiter, transport=httpx.MockTransport(rec))
    await asyncio.gather(
        a.get("https://Portal.example/a"), b.get("https://portal.example/b"), a.get("https://portal.example:8443/c")
    )
    await a.aclose()
    await b.aclose()
    assert all(g >= 0.19 for g in rec.gaps("portal.example"))


async def test_redirect_hops_are_rate_limited():
    def handler(req, n):
        if req.url.path == "/start":
            return httpx.Response(302, headers={"Location": "/end"})
        return httpx.Response(200, text="done")

    rec = Recorder(handler)
    async with make_client(rec) as client:
        r = await client.get("https://portal.example/start")
    assert r.text == "done" and str(r.url) == "https://portal.example/end"
    assert len(rec.hits) == 2 and rec.gaps("portal.example")[0] >= 0.19


async def test_honest_user_agent():
    rec = Recorder()
    async with make_client(rec) as client:
        await client.get("https://portal.example/")
    assert rec.requests[0].headers["user-agent"] == USER_AGENT
    assert "WatchtowerBot" in USER_AGENT


async def test_retries_transient_statuses_then_succeeds():
    rec = Recorder(lambda req, n: httpx.Response(503 if n < 3 else 200, text=str(n)))
    async with make_client(rec, interval=0.01) as client:
        r = await client.get("https://portal.example/")
    assert r.status_code == 200 and len(rec.hits) == 3


async def test_gives_up_after_max_attempts_and_returns_last_response():
    rec = Recorder(lambda req, n: httpx.Response(502))
    async with make_client(rec, interval=0.01, max_attempts=2) as client:
        r = await client.get("https://portal.example/")
    assert r.status_code == 502 and len(rec.hits) == 2


async def test_does_not_retry_client_errors():
    rec = Recorder(lambda req, n: httpx.Response(404))
    async with make_client(rec, interval=0.01) as client:
        r = await client.get("https://portal.example/")
    assert r.status_code == 404 and len(rec.hits) == 1


async def test_retries_connection_errors_then_raises():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("refused", request=request)

    async with PoliteClient(
        limiter=HostRateLimiter(0.01), transport=httpx.MockTransport(handler), backoff_base=0.01
    ) as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("https://portal.example/")
    assert calls == 3


async def test_retry_after_is_honoured():
    rec = Recorder(lambda req, n: httpx.Response(429, headers={"Retry-After": "1"}) if n == 1 else httpx.Response(200))
    async with make_client(rec, interval=0.01) as client:
        start = time.monotonic()
        r = await client.get("https://portal.example/")
    assert r.status_code == 200 and time.monotonic() - start >= 0.99


async def test_stream_retries_before_handing_over_and_streams_body():
    rec = Recorder(lambda req, n: httpx.Response(503) if n == 1 else httpx.Response(200, content=b"x" * 100_000))
    async with make_client(rec, interval=0.01) as client:
        async with client.stream("GET", "https://portal.example/file.zip") as r:
            assert r.status_code == 200
            size = 0
            async for chunk in r.aiter_bytes():
                size += len(chunk)
    assert size == 100_000 and len(rec.hits) == 2


def test_parse_retry_after():
    assert parse_retry_after("5") == 5.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("soon") is None
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0
