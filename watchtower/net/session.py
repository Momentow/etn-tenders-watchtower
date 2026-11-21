"""`BrowserSession`: one browser context held across actions (ADR 0014 §1).

Cookies, storage and app state carry from one action to the next, the way a person browses. Every request still waits
for the shared per-host limiter, and the time spent waiting there doesn't count against the session's *active* budget:
a JS app that needs 95 requests boots in ~95 s of polite waiting and a few seconds of active time instead of timing
out.

Created by `BrowserPool.session()`; nothing else constructs one.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import Counter, OrderedDict
from collections.abc import Awaitable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from watchtower.net.actions import (
    Action,
    Click,
    Fill,
    Goto,
    LoadMore,
    Press,
    Scroll,
    Select,
    Tab,
    Target,
    WaitFor,
)
from watchtower.net.limiter import HostRateLimiter, host_of

SKIPPED_RESOURCE_TYPES = frozenset({"image", "media", "font"})
CAPTURED_RESOURCE_TYPES = frozenset({"xhr", "fetch"})
CACHED_RESOURCE_TYPES = frozenset({"script", "stylesheet"})
MAX_CAPTURED_BODY_BYTES = 2_000_000
ELEMENT_TIMEOUT_MS = 15_000  # actionability of one element (visible, enabled, stable)
WAIT_FOR_ACTIVE_SECONDS = 30.0  # a wait_for that never matches gives up after this much active time
STALE_REQUEST_SECONDS = 30.0  # long-polling and streams don't keep a page "busy" forever
SNAPSHOT_MAX_CHARS = 4_000
ITEM_LINK_CHARS = 25  # link text at least this long reads as an item (a tender title), not navigation

T = TypeVar("T")


class SessionBudgetExceeded(RuntimeError):
    """The session used up its active or wall-clock budget; it can't be used any more."""


@dataclass(frozen=True)
class SessionBudget:
    active_seconds: float = 120.0  # time not spent waiting for the rate limiter
    idle_ms: int = 1500  # settled = nothing in flight or queued for this long
    max_seconds: float = 1800.0  # wall clock, waits included


@dataclass
class Exchange:
    """One XHR/fetch or JSON exchange the page made: enough to replay it without the browser."""

    id: int
    method: str
    url: str
    request_headers: dict[str, str]  # without `cookie`
    request_body: str | None
    status: int
    content_type: str
    resource_type: str
    body: str | None  # text up to 2 MB; None when binary or unreadable
    truncated: bool
    action_index: int  # which act() caused it


@dataclass
class ActionResult:
    action: str  # what was done, in words
    url: str  # page URL afterwards
    status: int | None  # main document status, when the action navigated
    network: list[Exchange] = field(default_factory=list)
    seconds_active: float = 0.0
    seconds_waiting: float = 0.0  # spent queued at the rate limiter
    error: str | None = None  # the action failed (element not found, wait timed out…); the session is still usable
    note: str = ""  # e.g. "3 clicks; the control is gone"


class StaticCache:
    """Scripts and stylesheets by URL, shared by a pool's sessions: a second boot of an app costs a few requests."""

    def __init__(self, max_bytes: int = 64_000_000, ttl_seconds: float = 3600.0):
        self.max_bytes, self.ttl = max_bytes, ttl_seconds
        self._items: OrderedDict[str, tuple[float, str, bytes]] = OrderedDict()
        self._bytes = 0

    def get(self, url: str) -> tuple[str, bytes] | None:
        item = self._items.get(url)
        if item is None:
            return None
        stored, content_type, body = item
        if time.monotonic() - stored > self.ttl:
            self._drop(url)
            return None
        self._items.move_to_end(url)
        return content_type, body

    def put(self, url: str, content_type: str, body: bytes) -> None:
        if len(body) > self.max_bytes // 8:
            return
        if url in self._items:
            self._drop(url)
        self._items[url] = (time.monotonic(), content_type, body)
        self._bytes += len(body)
        while self._bytes > self.max_bytes:
            self._drop(next(iter(self._items)))

    def _drop(self, url: str) -> None:
        _, _, body = self._items.pop(url)
        self._bytes -= len(body)

    def __len__(self) -> int:
        return len(self._items)


def _cacheable(cache_control: str) -> bool:
    cc = cache_control.lower()
    return "no-store" not in cc and "no-cache" not in cc and "private" not in cc


def _short(e: BaseException) -> str:
    text = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    return text[:300]


class BrowserSession:
    def __init__(self, context: Any, page: Any, *, limiter: HostRateLimiter, budget: SessionBudget, cache: StaticCache):
        self._context, self._page = context, page
        self._limiter, self._budget, self._cache = limiter, budget, cache
        self._t0 = time.monotonic()
        self._queued = 0
        self._wait_started = 0.0
        self._wait_total = 0.0
        self._inflight: dict[Any, float] = {}
        self._last_activity = self._t0
        self._served_from_cache: set[str] = set()
        self._pending: set[asyncio.Task] = set()
        self._action_index = -1
        self.network: list[Exchange] = []
        self.counts: Counter[str] = Counter()  # requests by resource type, plus "cache_hit" and "aborted"
        self.actions: list[str] = []

    # ------------------------------------------------------------------ wiring (called by BrowserPool.session)

    async def _attach(self) -> None:
        await self._context.route("**/*", self._route)
        self._context.on("request", self._on_request)
        self._context.on("requestfinished", self._done)
        self._context.on("requestfailed", self._done)
        self._page.on("response", self._on_response)

    async def _route(self, route: Any) -> None:
        request = route.request
        if request.resource_type in SKIPPED_RESOURCE_TYPES:
            self.counts["aborted"] += 1
            await route.abort()
            return
        url = request.url
        if request.method == "GET" and request.resource_type in CACHED_RESOURCE_TYPES:
            hit = self._cache.get(url)
            if hit is not None:
                self.counts["cache_hit"] += 1
                self._served_from_cache.add(url)
                await route.fulfill(status=200, content_type=hit[0], body=hit[1])
                return
        self._inflight[request] = time.monotonic()
        self._last_activity = time.monotonic()
        if url.startswith(("http://", "https://")):
            self._enter_wait()
            try:
                await self._limiter.acquire(url)
            finally:
                self._leave_wait()
        self.counts[request.resource_type] += 1
        await route.continue_()

    def _on_request(self, request: Any) -> None:
        self._last_activity = time.monotonic()
        # Chromium follows redirects itself: the hop that redirected never "finishes", so retire it here.
        previous = request.redirected_from
        if previous is not None:
            self._inflight.pop(previous, None)

    def _done(self, request: Any) -> None:
        self._inflight.pop(request, None)
        self._last_activity = time.monotonic()

    def _on_response(self, response: Any) -> None:
        task = asyncio.ensure_future(self._capture(response))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _capture(self, response: Any) -> None:
        request = response.request
        rtype = request.resource_type
        try:
            headers = await response.all_headers()
        except Exception:
            return
        content_type = headers.get("content-type", "").lower()
        url = response.url
        if (
            rtype in CACHED_RESOURCE_TYPES
            and request.method == "GET"
            and response.status == 200
            and url not in self._served_from_cache
            and _cacheable(headers.get("cache-control", ""))
        ):
            try:
                self._cache.put(url, content_type or "application/octet-stream", await response.body())
            except Exception:
                pass
        if rtype not in CAPTURED_RESOURCE_TYPES and "json" not in content_type:
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
        try:
            req_headers = {k: v for k, v in (await request.all_headers()).items() if k.lower() != "cookie"}
        except Exception:
            req_headers = {}
        self.network.append(
            Exchange(
                id=len(self.network),
                method=request.method,
                url=url,
                request_headers=req_headers,
                request_body=request.post_data,
                status=response.status,
                content_type=content_type,
                resource_type=rtype,
                body=body,
                truncated=truncated,
                action_index=self._action_index,
            )
        )

    # ------------------------------------------------------------------ the polite clock

    def _enter_wait(self) -> None:
        if self._queued == 0:
            self._wait_started = time.monotonic()
        self._queued += 1

    def _leave_wait(self) -> None:
        self._queued -= 1
        if self._queued == 0:
            self._wait_total += time.monotonic() - self._wait_started

    def seconds_waiting(self) -> float:
        current = time.monotonic() - self._wait_started if self._queued else 0.0
        return self._wait_total + current

    def seconds_elapsed(self) -> float:
        return time.monotonic() - self._t0

    def seconds_active(self) -> float:
        return self.seconds_elapsed() - self.seconds_waiting()

    def _check_budget(self, active_limit: float | None = None, since_active: float = 0.0) -> None:
        if self.seconds_elapsed() > self._budget.max_seconds:
            raise SessionBudgetExceeded(f"the session ran longer than {self._budget.max_seconds:.0f} s")
        if self.seconds_active() > self._budget.active_seconds:
            raise SessionBudgetExceeded(
                f"the session was busy for more than {self._budget.active_seconds:.0f} s "
                f"(not counting {self.seconds_waiting():.0f} s spent waiting for the rate limit)"
            )
        if active_limit is not None and self.seconds_active() - since_active > active_limit:
            raise TimeoutError(f"gave up after {active_limit:.0f} s")

    async def _guard(self, aw: Awaitable[T], *, active_limit: float | None = None) -> T:
        """Run a Playwright call while enforcing the budget (Playwright's own timeouts are set very high)."""
        start = self.seconds_active()
        task = asyncio.ensure_future(aw)
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=0.2)
                if done:
                    return task.result()
                self._check_budget(active_limit, start)
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except BaseException:
                    pass

    async def settle(self) -> None:
        """Wait until nothing is in flight or queued for `idle_ms`, within the budget."""
        idle = self._budget.idle_ms / 1000
        while True:
            self._check_budget()
            now = time.monotonic()
            for request, since in list(self._inflight.items()):
                if now - since > STALE_REQUEST_SECONDS and self._queued == 0:
                    self._inflight.pop(request, None)
            if not self._inflight and self._queued == 0 and now - self._last_activity >= idle:
                return
            await asyncio.sleep(0.1)

    # ------------------------------------------------------------------ actions

    def locator(self, target: Target) -> Any:
        page = self._page
        if target.role:
            loc = page.get_by_role(target.role, name=target.name) if target.name else page.get_by_role(target.role)
        elif target.text:
            loc = page.get_by_text(target.text)
        elif target.css:
            loc = page.locator(target.css)
        else:
            loc = page.locator(f"xpath={target.xpath}")
        return loc.nth(target.nth)

    async def act(self, action: Action) -> ActionResult:
        """Do one action and wait until the page settles. Action failures come back in `error`; only an exhausted
        budget raises (`SessionBudgetExceeded`)."""
        self._action_index += 1
        self._last_activity = time.monotonic()  # an action is activity: settling waits idle_ms after it at least
        described = _describe(action)
        self.actions.append(described)
        first = len(self.network)
        active0, waiting0 = self.seconds_active(), self.seconds_waiting()
        status: int | None = None
        error: str | None = None
        note = ""
        try:
            status, note = await self._do(action)
        except SessionBudgetExceeded:
            raise
        except Exception as e:  # Playwright errors, element not found, wait timed out
            error = _short(e)
            try:
                await self.settle()
            except SessionBudgetExceeded:
                raise
        if self._pending:
            await asyncio.wait(set(self._pending), timeout=5)
        return ActionResult(
            action=described,
            url=self._page.url,
            status=status,
            network=self.network[first:],
            seconds_active=round(self.seconds_active() - active0, 2),
            seconds_waiting=round(self.seconds_waiting() - waiting0, 2),
            error=error,
            note=note,
        )

    async def _do(self, action: Action) -> tuple[int | None, str]:
        page = self._page
        if isinstance(action, Goto):
            response = await self._guard(page.goto(action.url, wait_until="commit"))
            await self.settle()
            return (None if response is None else response.status), ""
        if isinstance(action, WaitFor):
            await self._wait_for(action)
            await self.settle()
            return None, ""
        if isinstance(action, Scroll):
            for _ in range(action.times):
                await self._guard(page.evaluate("window.scrollTo(0, document.body.scrollHeight)"))
                self._last_activity = time.monotonic()
                await self.settle()
            return None, f"scrolled {action.times}×"
        if isinstance(action, LoadMore):
            return None, await self._load_more(action)

        loc = await self._present(action.target)
        if isinstance(action, Click | Tab):
            await self._guard(loc.click(timeout=ELEMENT_TIMEOUT_MS, no_wait_after=True))
        elif isinstance(action, Fill):
            await self._guard(loc.fill(action.value, timeout=ELEMENT_TIMEOUT_MS))
        elif isinstance(action, Select):
            try:
                await self._guard(loc.select_option(action.value, timeout=ELEMENT_TIMEOUT_MS))
            except Exception:
                await self._guard(loc.select_option(label=action.value, timeout=ELEMENT_TIMEOUT_MS))
        elif isinstance(action, Press):
            await self._guard(loc.press(action.key, timeout=ELEMENT_TIMEOUT_MS, no_wait_after=True))
        await self.settle()
        return None, ""

    async def _present(self, target: Target) -> Any:
        loc = self.locator(target)
        if await self._guard(loc.count()) == 0:
            raise LookupError(f"nothing on the page matches {target.describe()}")
        return loc

    async def _wait_for(self, action: WaitFor) -> None:
        page = self._page
        if action.response is not None:
            pattern = re.compile(action.response)
            if any(pattern.search(e.url) for e in self.network):
                return
            await self._guard(
                page.wait_for_response(lambda r: bool(pattern.search(r.url)), timeout=0),
                active_limit=WAIT_FOR_ACTIVE_SECONDS,
            )
            return
        loc = self.locator(action.target) if action.target else page.get_by_text(action.text).first
        await self._guard(loc.wait_for(state="visible", timeout=0), active_limit=WAIT_FOR_ACTIVE_SECONDS)

    async def _load_more(self, action: LoadMore) -> str:
        page = self._page

        async def measure() -> int:
            if action.until_count:
                return await self._guard(page.locator(action.until_count).count())
            return await self._guard(page.evaluate("document.querySelectorAll('body *').length"))

        clicks = 0
        before = await measure()
        reason = f"stopped at max_clicks={action.max_clicks}"
        while clicks < action.max_clicks:
            loc = self.locator(action.target)
            if await self._guard(loc.count()) == 0 or not await self._guard(loc.is_visible()):
                reason = "the control is gone"
                break
            if not await self._guard(loc.is_enabled()):
                reason = "the control is disabled"
                break
            await self._guard(loc.click(timeout=ELEMENT_TIMEOUT_MS, no_wait_after=True))
            clicks += 1
            self._last_activity = time.monotonic()
            await self.settle()
            after = await measure()
            if after <= before:
                reason = "nothing new appeared"
                break
            before = after
        return f"{clicks} click{'s' if clicks != 1 else ''}; {reason}"

    # ------------------------------------------------------------------ reading the page

    @property
    def url(self) -> str:
        return self._page.url

    async def html(self) -> str:
        return await self._guard(self._page.content())

    async def cookies(self) -> list[dict[str, Any]]:
        return await self._context.cookies()

    async def snapshot(self) -> str:
        """A compact text view for the agent: where we are, what can be clicked, what repeats, visible totals."""
        data = await self._guard(self._page.evaluate(_SNAPSHOT_JS))
        return format_snapshot(self.url, data)


def _describe(action: Action) -> str:
    if isinstance(action, Goto):
        return f"goto {action.url}"
    if isinstance(action, WaitFor):
        if action.target:
            what = action.target.describe()
        else:
            what = f"text {action.text!r}" if action.text else f"response /{action.response}/"
        return f"wait_for {what}"
    if isinstance(action, Scroll):
        return f"scroll ×{action.times}"
    if isinstance(action, Fill | Select):
        return f"{action.do} {action.target.describe()} = {action.value!r}"
    if isinstance(action, Press):
        return f"press {action.key} on {action.target.describe()}"
    if isinstance(action, LoadMore):
        return f"load_more {action.target.describe()} (max {action.max_clicks})"
    return f"{action.do} {action.target.describe()}"


def format_snapshot(url: str, d: dict[str, Any]) -> str:
    lines = [f"URL: {url}", f"Title: {d.get('title', '')}"]
    if d.get("totals"):
        lines.append("Totals shown: " + " | ".join(d["totals"]))
    if d.get("heads"):
        lines.append("Headings: " + " | ".join(d["heads"]))
    if d.get("tabs"):
        lines.append("Tabs: " + " | ".join(d["tabs"]))
    if d.get("buttons"):
        lines.append("Buttons: " + " | ".join(d["buttons"]))
    if d.get("fields"):
        lines.append("Form fields: " + " | ".join(d["fields"]))
    if d.get("blocks"):
        lines.append("Repeated blocks:")
        lines += [f"  {b}" for b in d["blocks"]]
    links = d.get("links", [])
    if links:
        here = host_of(url)
        # Item-like links (long text: tender titles) first, then the rest (navigation), so items survive the cut.
        items = [x for x in links if len(x[0]) >= ITEM_LINK_CHARS]
        links = items[:25] + [x for x in links if len(x[0]) < ITEM_LINK_CHARS][:25]
        lines.append(f"Links ({d.get('nlinks', len(links))}; item-like first):")
        for name, href in links:
            shown = href if host_of(href) != here else re.sub(r"^https?://[^/]+", "", href) or "/"
            lines.append(f"  {name} → {shown}")
    text = "\n".join(lines)
    return text if len(text) <= SNAPSHOT_MAX_CHARS else text[: SNAPSHOT_MAX_CHARS - 20] + "\n… (cut)"


_SNAPSHOT_JS = (Path(__file__).with_name("snapshot.js")).read_text(encoding="utf-8")
