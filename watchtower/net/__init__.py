"""PoliteClient and BrowserPool: the only way to talk to portals, at most 1 req/s per host (ADR 0002).

Create one `HostRateLimiter` per process and pass it to both, so HTTP and browser traffic share one budget per host:

    limiter = HostRateLimiter()
    client = PoliteClient(limiter=limiter)
    browser = BrowserPool(limiter=limiter)

A browser *session* (ADR 0014) holds one context across actions: `async with browser.session() as s: await s.act(...)`.
"""

from watchtower.net.actions import Action, Target
from watchtower.net.blocks import BlockSignal, detect_block
from watchtower.net.browser import BrowserPool, CapturedResponse, RenderResult
from watchtower.net.client import USER_AGENT, PoliteClient
from watchtower.net.limiter import HostRateLimiter, host_of
from watchtower.net.session import ActionResult, BrowserSession, Exchange, SessionBudget, SessionBudgetExceeded

__all__ = [
    "USER_AGENT",
    "Action",
    "ActionResult",
    "BlockSignal",
    "BrowserPool",
    "BrowserSession",
    "CapturedResponse",
    "Exchange",
    "HostRateLimiter",
    "PoliteClient",
    "RenderResult",
    "SessionBudget",
    "SessionBudgetExceeded",
    "Target",
    "detect_block",
    "host_of",
]
