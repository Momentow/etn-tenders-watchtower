"""`BrowserPool`: one shared headless Chromium for JS-only portals (ADR 0002).

Each session (and each render, a one-step session) gets a fresh browser context: no cookies carried between portals or
reads. Every network request the page makes waits for the same per-host limiter as `PoliteClient`. Images, media and
fonts are aborted (never sent): they cost requests and tell us nothing. JSON and XHR/fetch responses are captured, so
the agent and engine can read a portal's API directly instead of scraping the rendered page.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from watchtower.net.actions import Goto
from watchtower.net.client import USER_AGENT
from watchtower.net.limiter import HostRateLimiter
from watchtower.net.session import (
    CAPTURED_RESOURCE_TYPES,
    MAX_CAPTURED_BODY_BYTES,
    SKIPPED_RESOURCE_TYPES,
    BrowserSession,
    SessionBudget,
    StaticCache,
)

DEFAULT_RENDER_TIMEOUT_MS = 45_000  # active time; rate-limiter waits don't count (ADR 0014)
RENDER_MAX_SECONDS = 600.0  # wall clock for one render, waits included

__all__ = ["CAPTURED_RESOURCE_TYPES", "MAX_CAPTURED_BODY_BYTES", "SKIPPED_RESOURCE_TYPES"]


@dataclass
class CapturedResponse:
    url: str
    method: str
    status: int
    content_type: str
    resource_type: str
    body: str | None  # text, or None when binary, too large or unreadable
    truncated: bool = False


@dataclass
class RenderResult:
    url: str  # final URL after redirects
    status: int | None  # main document status
    html: str
    responses: list[CapturedResponse] = field(default_factory=list)

    def json_responses(self) -> list[CapturedResponse]:
        return [r for r in self.responses if "json" in r.content_type and r.body is not None]


class BrowserPool:
    def __init__(
        self,
        *,
        limiter: HostRateLimiter,
        user_agent: str = USER_AGENT,
        max_pages: int = 2,
        max_held: int = 4,
        timeout_ms: int = DEFAULT_RENDER_TIMEOUT_MS,
    ):
        self.limiter = limiter
        self.user_agent = user_agent
        self.timeout_ms = timeout_ms
        self._pages = asyncio.Semaphore(max_pages)  # short sessions: renders and reads
        # Sessions held for a whole agent run (the explorer, ADR 0015) have their own slots, one per worker: they
        # never wait for, or starve, the short sessions they open inside the same run (no slot deadlock).
        self._held = asyncio.Semaphore(max_held)
        self._start_lock = asyncio.Lock()
        self._playwright: Any = None
        self._browser: Any = None
        self.cache = StaticCache()  # scripts/stylesheets shared by this pool's sessions

    async def _ensure_browser(self) -> Any:
        async with self._start_lock:
            if self._browser is None or not self._browser.is_connected():
                from playwright.async_api import async_playwright

                if self._playwright is None:
                    self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=True)
            return self._browser

    @asynccontextmanager
    async def session(
        self, *, budget: SessionBudget | None = None, held: bool = False
    ) -> AsyncIterator[BrowserSession]:
        """One fresh browser context held for several actions (ADR 0014 §1): cookies and app state carry over.
        `held`: the session lives for a whole agent run, so it takes a slot of its own kind (`max_held`)."""
        budget = budget or SessionBudget()
        browser = await self._ensure_browser()
        async with self._held if held else self._pages:
            context = await browser.new_context(user_agent=self.user_agent, java_script_enabled=True)
            # The session enforces its own (polite) budget; Playwright's timeouts only backstop it.
            context.set_default_timeout(budget.max_seconds * 1000)
            try:
                page = await context.new_page()
                session = BrowserSession(context, page, limiter=self.limiter, budget=budget, cache=self.cache)
                await session._attach()
                yield session
            finally:
                await context.close()

    async def render(self, url: str) -> RenderResult:
        """Load `url` in a fresh session; return the rendered HTML plus captured JSON/XHR responses.

        `timeout_ms` is *active* time: waiting for the rate limiter doesn't count, so a slow polite boot still ends."""
        budget = SessionBudget(active_seconds=self.timeout_ms / 1000, max_seconds=RENDER_MAX_SECONDS)
        async with self.session(budget=budget) as s:
            result = await s.act(Goto(do="goto", url=url))
            if result.error:
                raise RuntimeError(f"Rendering {url} failed: {result.error}")
            return RenderResult(
                url=s.url,
                status=result.status,
                html=await s.html(),
                responses=[
                    CapturedResponse(e.url, e.method, e.status, e.content_type, e.resource_type, e.body, e.truncated)
                    for e in s.network
                ],
            )

    async def aclose(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
