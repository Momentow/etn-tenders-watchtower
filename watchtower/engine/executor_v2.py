"""The v2 executor (ADR 0014 §2): `run_template` hands v2 templates here. No DB access, no model call.

Flow: session (if any) → list (`api` over HTTP, or `page`: entry + flow actions in the read's browser session, then
pagination) → scope filter on each item → per tender: detail (`api` JSON, or the page plus its `views`), fields,
documents, status → normalisation → `open_by_deadline`.

One browser session at most per read, opened only when the template needs one; every request in it still waits for the
shared per-host limiter. As in v1, failures never raise: they become plain-sentence `errors`, and a tender whose detail
or documents failed is still returned with `complete=False`.
"""

from __future__ import annotations

import copy
import json
import re
import time
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import httpx
import lxml.html

from watchtower.engine import normalize
from watchtower.engine.completeness import more_offered, other_notice
from watchtower.engine.executor import Page, ReadError, Req, _short
from watchtower.engine.extract import (
    ExtractError,
    HtmlScope,
    JsonScope,
    Scope,
    css,
    first,
    parse_html,
    select,
    value,
    values,
)
from watchtower.engine.result import DocumentRef, ReadResult, ReadStats, TenderRecord, TenderStatus
from watchtower.engine.snapshot import snapshot
from watchtower.engine.template import CursorPagination, NextLinkPagination, NoPagination, ParamPagination
from watchtower.engine.template_v2 import (
    _DOUBLE,
    _SINGLE,
    ApiDetail,
    ApiList,
    ClickPagination,
    Condition,
    ItemGroup,
    LoadMorePagination,
    PageDetail,
    PageList,
    RequestV2,
    ScrollPagination,
    TemplateV2,
    UrlRules,
)
from watchtower.net import BrowserPool, BrowserSession, PoliteClient, SessionBudget, SessionBudgetExceeded, detect_block
from watchtower.net.actions import Action, Click, Goto, LoadMore, Scroll

# A read may visit many pages in one browser session: allow far more active time than an agent's single look.
READ_SESSION_BUDGET = SessionBudget(active_seconds=900, max_seconds=3300)


class _Item:
    __slots__ = ("scope", "raw", "list_url")

    def __init__(self, scope: Scope, raw: dict[str, Any], list_url: str):
        self.scope, self.raw, self.list_url = scope, raw, list_url


async def run_template_v2(
    template: TemplateV2, client: PoliteClient, browser: BrowserPool | None, *, limit: int | None = None
) -> ReadResult:
    return await _RunV2(template, client, browser).run(limit)


class _RunV2:
    def __init__(self, template: TemplateV2, client: PoliteClient, browser: BrowserPool | None):
        self.t = template
        self.client = client
        self.browser = browser
        self.stats = ReadStats()
        self.errors: list[str] = []
        self.snapshot: dict[str, Any] | None = None
        self.declared_total: int | None = None
        self.scope_dropped = 0
        self.rules = template.fields.rules()
        self.tz = ZoneInfo(template.timezone)
        self.session_values: dict[str, str] = {}
        self._stack: AsyncExitStack | None = None
        self._bs: BrowserSession | None = None
        self._last_note = ""
        self._seen: set[str] = set()
        self.more: list[str] = []
        self.other: dict[str, str] = {}

    async def run(self, limit: int | None) -> ReadResult:
        started = time.monotonic()
        tenders: list[TenderRecord] = []
        async with AsyncExitStack() as stack:
            self._stack = stack
            if self.t.needs_browser and self.browser is None:
                self.errors.append("The template needs a browser, but none was available.")
            elif await self._open_session():
                items = await self._read_list(limit)
                self.stats.list_seconds = time.monotonic() - started
                detail_start = time.monotonic()
                for item in items:
                    record = await self._read_tender(item)
                    if record is None:
                        self.scope_dropped += 1
                    else:
                        tenders.append(record)
                        if label := other_notice(item.scope):
                            self.other[record.id] = label
                self.stats.detail_seconds = time.monotonic() - detail_start
        self.stats.total_seconds = time.monotonic() - started
        return ReadResult(
            tenders=tenders,
            errors=self.errors,
            stats=self.stats,
            snapshot=self.snapshot,
            declared_total=self.declared_total,
            scope_dropped=self.scope_dropped,
            more_offered=self.more,
            other_notices=self.other,
        )

    # ------------------------------------------------------------------ browser and http

    async def _session(self) -> BrowserSession:
        if self._bs is None:
            assert self.browser is not None and self._stack is not None
            self._bs = await self._stack.enter_async_context(self.browser.session(budget=READ_SESSION_BUDGET))
        return self._bs

    async def _act(self, action: Action, what: str) -> None:
        bs = await self._session()
        try:
            r = await bs.act(action)
        except SessionBudgetExceeded as e:
            raise ReadError(f"the browser session ran out of time ({e})") from e
        self.stats.requests += len(r.network) + (1 if isinstance(action, Goto) else 0)
        if r.error:
            raise ReadError(f"{what}: {r.action} failed ({r.error})")
        if r.status is not None and r.status >= 400:
            raise ReadError(f"{what}: {r.action} returned HTTP {r.status}")
        if isinstance(action, LoadMore | Scroll):
            self._last_note = r.note

    async def _page_html(self) -> Page:
        bs = await self._session()
        try:
            html = await bs.html()
        except SessionBudgetExceeded as e:
            raise ReadError(f"the browser session ran out of time ({e})") from e
        return Page(bs.url, None, html)

    async def fetch(self, req: Req) -> Page:
        self.stats.requests += 1
        try:
            resp = await self.client.request(
                req.method,
                req.url,
                params=req.params or None,
                headers=req.headers or None,
                data=req.form,
                json=req.json_body,
            )
        except httpx.HTTPError as e:
            raise ReadError(f"{req.method} {req.full_url()} failed: {_short(e)}.") from e
        page = Page(str(resp.url), resp.status_code, resp.text, dict(resp.headers))
        if resp.status_code >= 400:
            raise ReadError(f"{req.method} {req.full_url()} returned HTTP {resp.status_code}.")
        return page

    # ------------------------------------------------------------------ placeholders

    def _fill(self, s: str, fills: dict[str, Any] | None, *, in_url: bool) -> str:
        def double(m: re.Match[str]) -> str:
            name = m.group(1)
            if name == "today":
                return datetime.now(UTC).astimezone(self.tz).date().isoformat()
            key = name.removeprefix("session.")
            if key not in self.session_values:
                raise ReadError(f"the session value {key!r} was not captured")
            return self.session_values[key]

        def single(m: re.Match[str]) -> str:
            v = (fills or {}).get(m.group(1))
            if v is None:
                raise ReadError(f"the item has no {m.group(1)!r} for the request")
            text = ",".join(v) if isinstance(v, list) else str(v)
            return quote(text, safe="") if in_url else text

        s = _DOUBLE.sub(double, s)
        return _SINGLE.sub(single, s) if fills is not None else s

    def _req(self, r: RequestV2, fills: dict[str, Any] | None = None) -> Req:
        def fill_json(v: Any) -> Any:
            if isinstance(v, str):
                return self._fill(v, fills, in_url=False)
            if isinstance(v, dict):
                return {k: fill_json(x) for k, x in v.items()}
            if isinstance(v, list):
                return [fill_json(x) for x in v]
            return v

        return Req(
            r.method,
            self._fill(r.url, fills, in_url=True),
            {k: self._fill(v, fills, in_url=False) for k, v in r.params.items()},
            {k: self._fill(v, fills, in_url=False) for k, v in r.headers.items()},
            None if r.form is None else {k: self._fill(v, fills, in_url=False) for k, v in r.form.items()},
            fill_json(r.json_body),
        )

    # ------------------------------------------------------------------ session

    async def _open_session(self) -> bool:
        s = self.t.session
        if s is None:
            return True
        try:
            if s.via == "browser":
                for i, action in enumerate(s.steps):
                    await self._act(action, f"session step {i + 1}")  # type: ignore[arg-type]
                bs = await self._session()
                exchanges = bs.network
                cookies = await bs.cookies()
                for c in s.capture:
                    self.session_values[c.name] = self._capture_browser(c, exchanges, cookies)
            else:
                responses: list[httpx.Response] = []
                for step in s.steps:
                    req = self._req(step)  # type: ignore[arg-type]
                    self.stats.requests += 1
                    try:
                        resp = await self.client.request(
                            req.method,
                            req.url,
                            params=req.params or None,
                            headers=req.headers or None,
                            data=req.form,
                            json=req.json_body,
                        )
                    except httpx.HTTPError as e:
                        raise ReadError(f"{req.method} {req.full_url()} failed: {_short(e)}") from e
                    if resp.status_code >= 400:
                        raise ReadError(f"{req.method} {req.full_url()} returned HTTP {resp.status_code}")
                    responses.extend([*resp.history, resp])
                    for c in s.capture:  # later steps may use values captured by earlier ones
                        try:
                            self.session_values[c.name] = self._capture_http(c, responses)
                        except ReadError:
                            pass
                for c in s.capture:
                    self.session_values[c.name] = self._capture_http(c, responses)
        except (ReadError, ExtractError) as e:
            self.errors.append(f"Getting the portal's anonymous session failed: {e}")
            return False
        return True

    def _capture_browser(self, c, exchanges, cookies: list[dict[str, Any]]) -> str:
        rx = re.compile(c.match)
        if c.from_ == "cookie":
            return _cookie_value(c, [(x.get("domain", ""), x["name"], x["value"]) for x in cookies], rx)
        for ex in reversed(exchanges):
            if not rx.search(ex.url):
                continue
            if c.from_ == "request_header":
                headers = {k.lower(): v for k, v in ex.request_headers.items()}
                if c.key.lower() in headers:
                    return headers[c.key.lower()]
            elif c.from_ == "response_json" and ex.body:
                found = _json_key(ex.body, c.key, ex.url)
                if found is not None:
                    return found
        raise ReadError(f"no request matching {c.match!r} carried {c.key!r} (capture {c.name!r})")

    def _capture_http(self, c, responses: list[httpx.Response]) -> str:
        rx = re.compile(c.match)
        if c.from_ == "cookie":
            jar = [(r.url.host, k, v) for r in responses for k, v in r.cookies.items()]
            return _cookie_value(c, jar, rx)
        for resp in reversed(responses):
            if not rx.search(str(resp.url)):
                continue
            if c.from_ == "response_header" and c.key in resp.headers:
                return resp.headers[c.key]
            if c.from_ == "response_json":
                found = _json_key(resp.text, c.key, str(resp.url))
                if found is not None:
                    return found
        raise ReadError(f"no response matching {c.match!r} had {c.key!r} (capture {c.name!r})")

    # ------------------------------------------------------------------ list

    async def _read_list(self, limit: int | None) -> list[_Item]:
        items: list[_Item] = []
        try:
            if isinstance(self.t.list, ApiList):
                await self._api_list(self.t.list, items, limit)
            elif self.t.list.via == "browser":
                await self._browser_list(self.t.list, items, limit)
            else:
                await self._http_list(self.t.list, items, limit)
        except _Enough:
            self.stats.limited = True
        except (ReadError, ExtractError) as e:
            self.errors.append(f"Reading the tender list failed: {e}")
        if self.stats.skipped_items:
            self.errors.append(f"{self.stats.skipped_items} list item(s) had no id and were skipped.")
        return items

    def _take(self, page: Page, scope: Scope, items: list[_Item], limit: int | None, *, first_page: bool) -> int:
        """Read the items of one list page; returns how many new tenders it had. Raises `_Enough` at `limit`."""
        lst = self.t.list
        self.stats.list_pages += 1
        if self.snapshot is None:
            self.snapshot = snapshot(scope, page.url)
        if first_page and lst.total is not None:
            self.declared_total = _count(first(lst.total, scope))
        found = select(lst.items, scope)
        if isinstance(lst, PageList) and lst.items.group is not None:
            found = _grouped(found, lst.items.group)
        if first_page and not found and isinstance(scope, HtmlScope):
            self._explain_empty(page)
        if first_page and found and isinstance(lst.pagination, NoPagination):
            self.more = more_offered(scope, found)
        new = 0
        for s in found:
            raw = {n: value(r, s, url=n == "url") for n, r in self.rules.items() if r.on == "item"}
            tid = raw.get("id")
            if not tid:
                self.stats.skipped_items += 1
                continue
            if tid in self._seen:
                self.stats.duplicate_ids += 1
                continue
            self._seen.add(tid)
            new += 1
            if not self._in_scope(s, raw):
                self.scope_dropped += 1
                continue
            items.append(_Item(s, raw, page.url))
            if limit is not None and len(items) >= limit:
                raise _Enough
        return new

    async def _api_list(self, lst: ApiList, items: list[_Item], limit: int | None) -> None:
        req = self._req(lst.request)
        pg = lst.pagination
        max_pages = getattr(pg, "max_pages", 1)
        visited: set[str] = set()
        page_req = req
        for n in range(max_pages):
            if isinstance(pg, ParamPagination):
                page_req = req.with_value(pg.name, str(pg.start + n * pg.step), pg.location)
            visited.add(page_req.full_url())
            page = await self.fetch(page_req)
            scope = page.scope("json")
            new = self._take(page, scope, items, limit, first_page=n == 0)
            if new == 0:
                return
            if isinstance(pg, NextLinkPagination):
                nxt = first(pg.link, scope, url=True)
                if not nxt or nxt in visited:
                    return
                page_req = Req("GET", nxt, headers=req.headers)
            elif isinstance(pg, CursorPagination):
                cursor = first(pg.cursor, scope)
                if not cursor:
                    return
                page_req = req.with_value(pg.name, cursor, pg.location)
            elif not isinstance(pg, ParamPagination):
                return
            if n == max_pages - 1:
                self.stats.hit_max_pages = True

    async def _http_list(self, lst: PageList, items: list[_Item], limit: int | None) -> None:
        req = Req("GET", self.t.entry.url)
        pg = lst.pagination
        max_pages = getattr(pg, "max_pages", 1)
        visited: set[str] = set()
        page_req = req
        for n in range(max_pages):
            if isinstance(pg, ParamPagination):
                page_req = req.with_value(pg.name, str(pg.start + n * pg.step), "query")
            visited.add(page_req.full_url())
            page = await self.fetch(page_req)
            scope = page.scope("html")
            if self._take(page, scope, items, limit, first_page=n == 0) == 0:
                return
            if isinstance(pg, NextLinkPagination):
                nxt = first(pg.link, scope, url=True)
                if not nxt or nxt in visited:
                    return
                page_req = Req("GET", nxt)
            elif not isinstance(pg, ParamPagination):
                return
            if n == max_pages - 1:
                self.stats.hit_max_pages = True

    async def _browser_list(self, lst: PageList, items: list[_Item], limit: int | None) -> None:
        await self._act(Goto(do="goto", url=self.t.entry.url), "opening the entry page")
        self.stats.other_pages += 1
        for i, action in enumerate(lst.flow):
            await self._act(action, f"list.flow step {i + 1}")
        pg = lst.pagination
        self._last_note = ""
        if isinstance(pg, LoadMorePagination):
            await self._act(LoadMore(do="load_more", target=pg.target, max_clicks=pg.max_clicks), "loading more")
            if "max_clicks" in self._last_note:
                self.stats.hit_max_pages = True
        elif isinstance(pg, ScrollPagination):
            await self._act(Scroll(do="scroll", times=pg.times), "scrolling the list")
        max_pages = pg.max_pages if isinstance(pg, ClickPagination | NextLinkPagination | ParamPagination) else 1
        list_url = (await self._session()).url
        visited = {list_url}
        for n in range(max_pages):
            page = await self._page_html()
            scope = parse_html(page.text, page.url)
            if self._take(page, scope, items, limit, first_page=n == 0) == 0:
                return
            if n == max_pages - 1:
                self.stats.hit_max_pages = max_pages > 1
                return
            if isinstance(pg, ClickPagination):
                bs = await self._session()
                if await bs.locator(pg.target).count() == 0:
                    return  # no "next" control: the last page
                await self._act(Click(do="click", target=pg.target), "the next list page")
            elif isinstance(pg, NextLinkPagination):
                nxt = first(pg.link, scope, url=True)
                if not nxt or nxt in visited:
                    return
                visited.add(nxt)
                await self._act(Goto(do="goto", url=nxt), "the next list page")
            elif isinstance(pg, ParamPagination):
                nxt = Req("GET", list_url).with_value(pg.name, str(pg.start + (n + 1) * pg.step), "query")
                await self._act(Goto(do="goto", url=nxt.full_url()), "the next list page")
            else:
                return

    def _explain_empty(self, page: Page) -> None:
        for sig in detect_block(page.text, url=page.url, status=page.status, headers=page.headers):
            what = "a login" if sig.kind == "login" else "a captcha"
            self.errors.append(
                f"The list page {page.url} shows no tenders and asks for {what} ({', '.join(sig.evidence)})."
            )

    # ------------------------------------------------------------------ scope

    def _in_scope(self, item_scope: Scope, raw: dict[str, Any]) -> bool:
        sc = self.t.scope
        if not all(_holds(c, item_scope) is True for c in sc.include):
            return False
        if any(_holds(c, item_scope) is True for c in sc.exclude):
            return False
        rule = self.rules.get("submissionDeadline")
        if sc.open_by_deadline and rule is not None and rule.on == "item":
            return not self._past(raw.get("submissionDeadline"))
        return True

    def _past(self, raw_deadline: Any) -> bool:
        formats = list(getattr(self.rules.get("submissionDeadline"), "formats", []))
        iso = normalize.deadline(raw_deadline, formats, self.tz)
        return iso is not None and datetime.fromisoformat(iso) < datetime.now(UTC)

    # ------------------------------------------------------------------ one tender

    async def _read_tender(self, item: _Item) -> TenderRecord | None:
        """The tender, or None when it turns out not to be an open tender (deadline passed, read on the detail)."""
        raw = dict(item.raw)
        tid = str(raw["id"])
        complete = True
        scopes: dict[str, Scope] = {"item": item.scope}
        detail_url: str | None = None
        if self.t.detail is not None:
            try:
                detail_url = await self._read_detail(item, scopes)
            except (ReadError, ExtractError) as e:
                self.errors.append(f"Tender {tid}: {e}")
                complete = False
        for name, rule in self.rules.items():
            if rule.on != "item" and rule.on in scopes:
                raw[name] = value(rule, scopes[rule.on], url=name == "url")
        if self.rules.get("url") is None:
            raw["url"] = detail_url
        if raw.get("url"):
            raw["url"] = _clean_url(str(raw["url"]), self.t.urls)

        documents: list[DocumentRef] = []
        docs = self.t.documents
        if docs is not None:
            try:
                if docs.request is not None:
                    page = await self.fetch(self._req(docs.request, item.raw))
                    self.stats.other_pages += 1
                    documents = await self._documents(page.scope("json"))
                elif docs.on in scopes:
                    documents = await self._documents(scopes[docs.on])
            except (ReadError, ExtractError) as e:
                self.errors.append(f"Tender {tid}: documents: {e}")
                complete = False

        status: TenderStatus = "open"
        if self.t.status is not None:
            rule = self.t.status.value
            scope = scopes.get(rule.on)
            label = first(rule, scope) if scope is not None else None
            if label is not None:
                if any(re.search(rx, label, re.I) for rx in self.t.status.withdrawn):
                    status = "withdrawn"
                elif any(re.search(rx, label, re.I) for rx in self.t.status.closed):
                    status = "closed"

        fields = normalize.normalize_fields(raw, self.t)  # type: ignore[arg-type] - duck-typed: fields + timezone
        fields["id"] = tid
        if self.t.scope.open_by_deadline and fields.get("submissionDeadline"):
            if datetime.fromisoformat(fields["submissionDeadline"]) < datetime.now(UTC):
                return None
        return TenderRecord(**fields, status=status, documents=documents, complete=complete)

    async def _read_detail(self, item: _Item, scopes: dict[str, Scope]) -> str | None:
        d = self.t.detail
        if isinstance(d, ApiDetail):
            page = await self.fetch(self._req(d.request, item.raw))
            self.stats.detail_pages += 1
            scopes["detail"] = page.scope("json")
            return self._fill(d.page_url, item.raw, in_url=True) if d.page_url else None
        assert isinstance(d, PageDetail)
        if d.link is not None:
            url = first(d.link, item.scope, url=True)
            if not url:
                raise ReadError("the list item has no detail link")
        else:
            assert d.url_template is not None
            url = self._fill(d.url_template, item.raw, in_url=True)
        if d.via == "http":
            page = await self.fetch(Req("GET", url))
            self.stats.detail_pages += 1
            for i, step in enumerate(d.resolve):
                link = first(step.link, page.scope("html"), url=True)
                if not link:
                    raise ReadError(f"detail.resolve step {i + 1} found no link on {page.url}")
                page = await self.fetch(Req("GET", link))
                self.stats.detail_pages += 1
            scopes["detail"] = page.scope("html")
            return page.url
        await self._act(Goto(do="goto", url=url), "opening the tender page")
        self.stats.detail_pages += 1
        page = await self._page_html()
        scopes["detail"] = parse_html(page.text, page.url)
        detail_url = page.url
        for name, actions in d.views.items():
            for action in actions:
                await self._act(action, f"detail view {name!r}")
            view = await self._page_html()
            scopes[f"detail.{name}"] = parse_html(view.text, view.url)
        return detail_url

    async def _documents(self, scope: Scope) -> list[DocumentRef]:
        docs = self.t.documents
        assert docs is not None
        bundle = first(docs.bundle, scope, url=True) if docs.bundle is not None else None
        if bundle:
            return [DocumentRef(url=_clean_url(bundle, self.t.urls))]
        links = values(docs.links, scope, url=True)
        names = values(docs.names, scope) if docs.names is not None else []
        refs: list[DocumentRef] = []
        seen: set[str] = set()
        for i, link in enumerate(links):
            name = names[i] if i < len(names) else None
            texts = [link, *([name] if name else [])]
            if docs.include is not None and not any(re.search(docs.include, s) for s in texts):
                continue
            if docs.exclude is not None and any(re.search(docs.exclude, s) for s in texts):
                continue
            for j, step in enumerate(docs.per_link):
                page = await self._fetch_if_html(link)
                if page is None:
                    break
                nxt = first(step.link, page.scope("html"), url=True)
                if not nxt:
                    raise ReadError(f"documents.per_link step {j + 1} found no link on {page.url}")
                link = nxt
            link = _clean_url(link, self.t.urls)
            if link not in seen:
                seen.add(link)
                refs.append(DocumentRef(url=link, fileName=name))
        return refs

    async def _fetch_if_html(self, url: str) -> Page | None:
        self.stats.requests += 1
        self.stats.other_pages += 1
        try:
            async with self.client.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    raise ReadError(f"GET {url} returned HTTP {resp.status_code}.")
                if "html" not in resp.headers.get("content-type", "").lower():
                    return None
                await resp.aread()
                return Page(str(resp.url), resp.status_code, resp.text, dict(resp.headers))
        except httpx.HTTPError as e:
            raise ReadError(f"GET {url} failed: {_short(e)}.") from e


class _Enough(Exception):
    """`limit` reached."""


# --------------------------------------------------------------------------- helpers


def _grouped(found: list[Scope], group: ItemGroup) -> list[Scope]:
    """Merge sibling matches into one scope per tender: copies of the members under one wrapper element."""
    elements = [s for s in found if isinstance(s, HtmlScope)]
    groups: list[list[HtmlScope]] = []
    if group.size is not None:
        groups = [elements[i : i + group.size] for i in range(0, len(elements), group.size)]
    else:
        assert group.starts_at is not None
        for s in elements:
            if s.el in css(group.starts_at, s.el):
                groups.append([s])
            elif groups:
                groups[-1].append(s)
    out: list[Scope] = []
    for members in groups:
        wrapper = lxml.html.Element("div")
        wrapper.set("data-watchtower-group", str(len(members)))
        for m in members:
            el = copy.deepcopy(m.el)
            el.tail = None
            wrapper.append(el)
        out.append(HtmlScope(wrapper, members[0].base_url))
    return out


def _canon(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _holds(c: Condition, scope: Scope) -> bool | None:
    """True / False, or None when the source has no value."""
    found = values(c.source, scope)
    if not found:
        return None
    if c.in_ is not None:
        allowed = {_canon(x) for x in c.in_}
        return any(_canon(v) in allowed for v in found)
    if c.matches is not None:
        return any(re.search(c.matches, v) for v in found)
    return any(_canon(v) == _canon(c.equals) for v in found)


def _count(text: str | None) -> int | None:
    """ "2.474 resultaten", "1,234", "2 474" → the number; None when there's none."""
    if not text:
        return None
    m = re.search(r"\d[\d.,\s  ']*", text)
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(0))
    return int(digits) if digits else None


def _cookie_value(c, jar: list[tuple[str, str, str]], rx: re.Pattern[str]) -> str:
    matching = [(name, val) for domain, name, val in jar if rx.search(domain)]
    if c.key == "*":
        if not matching:
            raise ReadError(f"no cookie on a domain matching {c.match!r} (capture {c.name!r})")
        return "; ".join(f"{n}={v}" for n, v in dict(matching).items())
    for name, val in reversed(matching):
        if name == c.key:
            return val
    raise ReadError(f"no cookie {c.key!r} on a domain matching {c.match!r} (capture {c.name!r})")


def _json_key(body: str, path: str, url: str) -> str | None:
    try:
        scope = JsonScope(json.loads(body), url)
    except ValueError:
        return None
    from watchtower.engine.template import Extract

    return first(Extract(by="jsonpath", expr=path), scope)


def _clean_url(url: str, rules: UrlRules) -> str:
    parts = urlsplit(url)
    path = parts.path
    for name in rules.drop_matrix:
        path = re.sub(rf";{re.escape(name)}=[^/;?#]*", "", path, flags=re.I)
    query = parts.query
    if query and (rules.drop_params or rules.drop_numeric_query):
        kept = []
        for k, v in parse_qsl(query, keep_blank_values=True):
            if k in rules.drop_params:
                continue
            if rules.drop_numeric_query and v == "" and re.match(r"^\d+(-|$)", k):
                continue
            kept.append((k, v))
        query = urlencode(kept)
    return urlunsplit(parts._replace(path=path, query=query))
