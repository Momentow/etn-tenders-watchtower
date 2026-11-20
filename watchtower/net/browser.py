"""`BrowserPool`: one shared headless Chromium for JS-only portals (ADR 0002).

Each render gets a fresh browser context (no cookies carried between portals). Every network request the page makes
waits for the same per-host limiter as `PoliteClient`. Images, media and fonts are aborted (never sent): they cost
requests and tell us nothing. JSON and XHR/fetch responses are captured, so the agent and engine can read a portal's
API directly instead of scraping the rendered page.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from watchtower.net.client import USER_AGENT
from watchtower.net.limiter import HostRateLimiter

SKIPPED_RESOURCE_TYPES = frozenset({"image", "media", "font"})
CAPTURED_RESOURCE_TYPES = frozenset({"xhr", "fetch"})
MAX_CAPTURED_BODY_BYTES = 2_000_000
DEFAULT_RENDER_TIMEOUT_MS = 45_000


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
        timeout_ms: int = DEFAULT_RENDER_TIMEOUT_MS,
    ):
        self.limiter = limiter
        self.user_agent = user_agent
        self.timeout_ms = timeout_ms
        self._pages = asyncio.Semaphore(max_pages)
        self._start_lock = asyncio.Lock()
        self._playwright: Any = None
        self._browser: Any = None

    async def _ensure_browser(self) -> Any:
        async with self._start_lock:
            if self._browser is None or not self._browser.is_connected():
                from playwright.async_api import async_playwright

                if self._playwright is None:
                    self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=True)
            return self._browser

    async def render(self, url: str, *, wait_until: str = "networkidle") -> RenderResult:
        """Load `url` in a fresh context; return the rendered HTML plus captured JSON/XHR responses."""
        browser = await self._ensure_browser()
        async with self._pages:
            context = await browser.new_context(user_agent=self.user_agent, java_script_enabled=True)
            context.set_default_timeout(self.timeout_ms)
            captured: list[CapturedResponse] = []
            pending: set[asyncio.Task] = set()
            try:
                await context.route("**/*", self._route)
                page = await context.new_page()

                def on_response(response: Any) -> None:
                    task = asyncio.ensure_future(self._capture(response, captured))
                    pending.add(task)
                    task.add_done_callback(pending.discard)

                page.on("response", on_response)
                main = await page.goto(url, wait_until=wait_until, timeout=self.timeout_ms)
                html = await page.content()
                if pending:
                    await asyncio.wait(set(pending), timeout=5)
                return RenderResult(
                    url=page.url,
                    status=None if main is None else main.status,
                    html=html,
                    responses=list(captured),
                )
            finally:
                await context.close()

    async def _route(self, route: Any) -> None:
        request = route.request
        if request.resource_type in SKIPPED_RESOURCE_TYPES:
            await route.abort()
            return
        if request.url.startswith(("http://", "https://")):
            await self.limiter.acquire(request.url)
        await route.continue_()

    @staticmethod
    async def _capture(response: Any, sink: list[CapturedResponse]) -> None:
        request = response.request
        content_type = (await response.header_value("content-type") or "").lower()
        if request.resource_type not in CAPTURED_RESOURCE_TYPES and "json" not in content_type:
            return
        body: str | None = None
        truncated = False
        try:
            raw = await response.body()
            if len(raw) > MAX_CAPTURED_BODY_BYTES:
                raw, truncated = raw[:MAX_CAPTURED_BODY_BYTES], True
            body = raw.decode("utf-8", errors="replace")
        except Exception:  # redirects, aborted or evicted bodies: keep the metadata only
            body = None
        sink.append(
            CapturedResponse(
                url=response.url,
                method=request.method,
                status=response.status,
                content_type=content_type,
                resource_type=request.resource_type,
                body=body,
                truncated=truncated,
            )
        )

    async def aclose(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
