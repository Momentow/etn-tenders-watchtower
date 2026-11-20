"""PoliteClient and BrowserPool: the only way to talk to portals, at most 1 req/s per host (ADR 0002).

Create one `HostRateLimiter` per process and pass it to both, so HTTP and browser traffic share one budget per host:

    limiter = HostRateLimiter()
    client = PoliteClient(limiter=limiter)
    browser = BrowserPool(limiter=limiter)
"""

from watchtower.net.blocks import BlockSignal, detect_block
from watchtower.net.browser import BrowserPool, CapturedResponse, RenderResult
from watchtower.net.client import USER_AGENT, PoliteClient
from watchtower.net.limiter import HostRateLimiter, host_of

__all__ = [
    "USER_AGENT",
    "BlockSignal",
    "BrowserPool",
    "CapturedResponse",
    "HostRateLimiter",
    "PoliteClient",
    "RenderResult",
    "detect_block",
    "host_of",
]
