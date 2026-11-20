"""Per-host politeness: at most one request per `min_interval` seconds to any one host, across the whole process."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

MIN_INTERVAL_SECONDS = 1.0


def host_of(url: str) -> str:
    """The key requests are limited by: the lower-cased host name (port ignored)."""
    return (urlsplit(url).hostname or "").lower().rstrip(".")


class HostRateLimiter:
    """Spaces requests to the same host at least `min_interval` apart; different hosts don't wait for each other.

    One instance per process, shared by `PoliteClient` and `BrowserPool`, so every caller (engine, documents, agent
    tools) draws from the same budget. Waiters on one host are served in arrival order.
    """

    def __init__(
        self,
        min_interval: float = MIN_INTERVAL_SECONDS,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._locks: dict[str, asyncio.Lock] = {}
        self._next_slot: dict[str, float] = {}

    async def acquire(self, url_or_host: str) -> None:
        """Wait for this host's next free slot, then claim it. Call immediately before sending a request."""
        host = host_of(url_or_host) if "://" in url_or_host else url_or_host.lower()
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._next_slot.get(host, 0.0) - self._clock()
            if wait > 0:
                await self._sleep(wait)
            self._next_slot[host] = self._clock() + self.min_interval
