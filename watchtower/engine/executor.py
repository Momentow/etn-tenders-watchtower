"""`run_template`: read a portal by executing its template (ADR 0002, ADR 0003). No DB access, no model call.

Flow: list request → navigation `steps` → list pages (pagination) → per tender: detail page (+ `resolve` hops), fields,
documents (+ `per_link` hops), status → normalisation. All traffic goes through `PoliteClient` / `BrowserPool`.

Failures never raise: they become plain-sentence `errors` on the result, and the read keeps whatever it could read.
A tender whose detail page or document hop failed is still returned, with `complete=False`.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from watchtower.engine import normalize
from watchtower.engine.extract import (
    ExtractError,
    HtmlScope,
    Scope,
    css,
    first,
    parse_html,
    parse_json,
    select,
    value,
    values,
)
from watchtower.engine.result import DocumentRef, ReadResult, ReadStats, TenderRecord, TenderStatus
from watchtower.engine.snapshot import snapshot
from watchtower.engine.template import (
    CursorPagination,
    FollowStep,
    NextLinkPagination,
    ParamPagination,
    Request,
    SubmitStep,
    Template,
)
from watchtower.net import BrowserPool, PoliteClient, detect_block


class ReadError(Exception):
    """A fetch or navigation failed; the message is a plain sentence for `errors`."""


@dataclass(frozen=True)
class Req:
    method: str
    url: str
    params: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    form: dict[str, str] | None = None
    json_body: Any = None

    @classmethod
    def of(cls, r: Request) -> Req:
        return cls(r.method, r.url, dict(r.params), dict(r.headers), r.form, r.json_body)

    def full_url(self) -> str:
        if not self.params:
            return self.url
        parts = urlsplit(self.url)
        query = urlencode([*parse_qsl(parts.query, keep_blank_values=True), *self.params.items()])
        return urlunsplit(parts._replace(query=query))

    def with_value(self, name: str, v: str, location: str) -> Req:
        if location == "query":
            return replace(self, params={**self.params, name: v})
        if location == "form":
            return replace(self, form={**(self.form or {}), name: v})
        if not isinstance(self.json_body, dict | None):
            raise ReadError("Pagination in the JSON body needs the request body to be a JSON object.")
        return replace(self, json_body={**(self.json_body or {}), name: _json_number(v)})


@dataclass
class Page:
    url: str  # final URL, after redirects
    status: int | None
    text: str
    headers: dict[str, str] = field(default_factory=dict)

    def scope(self, kind: str) -> Scope:
        if kind == "json":
            try:
                return parse_json(self.text, self.url)
            except ValueError as e:
                raise ReadError(f"{self.url} did not return JSON ({e}).") from e
        return parse_html(self.text, self.url)


@dataclass
class _Item:
    scope: Scope
    raw: dict[str, Any]  # item-field values, keyed like Fields.rules()
    list_url: str


async def run_template(
    template: Template, client: PoliteClient, browser: BrowserPool | None, *, limit: int | None = None
) -> ReadResult:
    """Read the portal. `limit` stops after that many tenders (a dry run for the agent's `try_template`)."""
    return await _Run(template, client, browser).run(limit)


class _Run:
    def __init__(self, template: Template, client: PoliteClient, browser: BrowserPool | None):
        self.t = template
        self.client = client
        self.browser = browser
        self.stats = ReadStats()
        self.errors: list[str] = []
        self.snapshot: dict[str, Any] | None = None
        self.rules = template.fields.rules()

    async def run(self, limit: int | None) -> ReadResult:
        started = time.monotonic()
        tenders: list[TenderRecord] = []
        if self.t.needs_browser and self.browser is None:
            self.errors.append("The template needs a browser (render: true), but none was available.")
        else:
            items = await self._read_list(limit)
            self.stats.list_seconds = time.monotonic() - started
            detail_start = time.monotonic()
            for item in items:
                tenders.append(await self._read_tender(item))
            self.stats.detail_seconds = time.monotonic() - detail_start
        self.stats.total_seconds = time.monotonic() - started
        return ReadResult(tenders=tenders, errors=self.errors, stats=self.stats, snapshot=self.snapshot)

    # ------------------------------------------------------------------ fetching

    async def fetch(self, req: Req, *, render: bool = False) -> Page:
        self.stats.requests += 1
        if render:
            if req.method != "GET" or req.form is not None or req.json_body is not None:
                raise ReadError(f"Can't render a {req.method} request with the browser ({req.url}).")
            assert self.browser is not None
            try:
                r = await self.browser.render(req.full_url())
            except Exception as e:  # Playwright raises its own error types (timeouts, navigation errors)
                raise ReadError(f"Rendering {req.full_url()} failed: {_short(e)}.") from e
            page = Page(r.url, r.status, r.html)
        else:
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
        if page.status is not None and page.status >= 400:
            raise ReadError(f"{req.method} {req.full_url()} returned HTTP {page.status}.")
        return page

    # ------------------------------------------------------------------ list

    async def _read_list(self, limit: int | None) -> list[_Item]:
        lst = self.t.list
        render = lst.kind == "html" and lst.render
        items: list[_Item] = []
        try:
            req = await self._navigate(Req.of(lst.request), getattr(lst, "steps", []), render)
            pg = lst.pagination
            max_pages = getattr(pg, "max_pages", 1)
            seen_ids: set[str] = set()
            visited: set[str] = set()
            page_req = req
            for n in range(max_pages):
                if isinstance(pg, ParamPagination):
                    page_req = req.with_value(pg.name, str(pg.start + n * pg.step), pg.location)
                visited.add(page_req.full_url())
                page = await self.fetch(page_req, render=render)
                self.stats.list_pages += 1
                scope = page.scope(lst.kind)
                if self.snapshot is None:
                    self.snapshot = snapshot(scope, page.url)
                found = select(lst.items, scope)
                if n == 0 and not found and isinstance(scope, HtmlScope):
                    self._explain_empty(page)
                new = 0
                for s in found:
                    raw = {n: value(r, s, url=n == "url") for n, r in self.rules.items() if r.on == "item"}
                    tid = raw.get("id")
                    if not tid:
                        self.stats.skipped_items += 1
                        continue
                    if tid in seen_ids:
                        self.stats.duplicate_ids += 1
                        continue
                    seen_ids.add(tid)
                    items.append(_Item(s, raw, page.url))
                    new += 1
                    if limit is not None and len(items) >= limit:
                        return items
                if not found or new == 0:
                    break
                if isinstance(pg, NextLinkPagination):
                    nxt = first(pg.link, scope, url=True)
                    if not nxt or nxt in visited:
                        break
                    page_req = Req("GET", nxt, headers=req.headers)
                elif isinstance(pg, CursorPagination):
                    cursor = first(pg.cursor, scope)
                    if not cursor:
                        break
                    page_req = req.with_value(pg.name, cursor, pg.location)
                elif not isinstance(pg, ParamPagination):
                    break
                if n == max_pages - 1:
                    self.stats.hit_max_pages = True
        except (ReadError, ExtractError) as e:
            self.errors.append(f"Reading the tender list failed: {e}")
        if self.stats.skipped_items:
            self.errors.append(f"{self.stats.skipped_items} list item(s) had no id and were skipped.")
        return items

    async def _navigate(self, req: Req, steps: list[FollowStep | SubmitStep], render: bool) -> Req:
        """Run the navigation steps; returns the request for the list page (not yet fetched)."""
        for i, step in enumerate(steps):
            page = await self.fetch(req, render=render)
            self.stats.other_pages += 1
            scope = page.scope("html")
            assert isinstance(scope, HtmlScope)
            if isinstance(step, FollowStep):
                link = first(step.link, scope, url=True)
                if not link:
                    raise ReadError(f"navigation step {i + 1} found no link on {page.url}")
                req = Req("GET", link, headers=req.headers)
            else:
                req = _submit(step, scope, req.headers)
        return req

    def _explain_empty(self, page: Page) -> None:
        for sig in detect_block(page.text, url=page.url, status=page.status, headers=page.headers):
            what = "a login" if sig.kind == "login" else "a captcha"
            self.errors.append(
                f"The list page {page.url} shows no tenders and asks for {what} ({', '.join(sig.evidence)})."
            )

    # ------------------------------------------------------------------ one tender

    async def _read_tender(self, item: _Item) -> TenderRecord:
        raw = dict(item.raw)
        tid = str(raw["id"])
        complete = True
        detail_scope: Scope | None = None
        detail_url: str | None = None
        d = self.t.detail
        if d is not None:
            try:
                detail_url, detail_scope = await self._read_detail(item)
            except (ReadError, ExtractError) as e:
                self.errors.append(f"Tender {tid}: {e}")
                complete = False
            if detail_scope is not None:
                for name, rule in self.rules.items():
                    if rule.on == "detail":
                        raw[name] = value(rule, detail_scope, url=name == "url")
        if self.rules.get("url") is None:
            raw["url"] = detail_url

        documents: list[DocumentRef] = []
        docs = self.t.documents
        if docs is not None:
            scope = item.scope if docs.on == "item" else detail_scope
            if scope is not None:
                try:
                    documents = await self._documents(scope)
                except (ReadError, ExtractError) as e:
                    self.errors.append(f"Tender {tid}: documents: {e}")
                    complete = False

        status: TenderStatus = "open"
        if self.t.status is not None:
            rule = self.t.status.value
            scope = item.scope if rule.on == "item" else detail_scope
            label = first(rule, scope) if scope is not None else None
            if label is not None:
                if any(re.search(rx, label, re.I) for rx in self.t.status.withdrawn):
                    status = "withdrawn"
                elif any(re.search(rx, label, re.I) for rx in self.t.status.closed):
                    status = "closed"

        fields = normalize.normalize_fields(raw, self.t)
        fields["id"] = tid
        return TenderRecord(**fields, status=status, documents=documents, complete=complete)

    async def _read_detail(self, item: _Item) -> tuple[str, Scope]:
        d = self.t.detail
        assert d is not None
        if d.link is not None:
            url = first(d.link, item.scope, url=True)
            if not url:
                raise ReadError("the list item has no detail link")
        else:
            assert d.url_template is not None
            fills = {k: quote(str(v if not isinstance(v, list) else ",".join(v)), safe="") for k, v in item.raw.items()}
            try:
                url = d.url_template.format_map(fills)
            except (KeyError, IndexError, ValueError) as e:
                raise ReadError(f"detail.url_template could not be filled ({e})") from e
        page = await self.fetch(Req("GET", url), render=d.render)
        self.stats.detail_pages += 1
        for i, step in enumerate(d.resolve):
            link = first(step.link, page.scope(d.format), url=True)
            if not link:
                raise ReadError(f"detail.resolve step {i + 1} found no link on {page.url}")
            page = await self.fetch(Req("GET", link), render=d.render)
            self.stats.detail_pages += 1
        return page.url, page.scope(d.format)

    async def _documents(self, scope: Scope) -> list[DocumentRef]:
        docs = self.t.documents
        assert docs is not None
        bundle = first(docs.bundle, scope, url=True) if docs.bundle is not None else None
        if bundle:
            return [DocumentRef(url=bundle)]
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
                    break  # already the file
                nxt = first(step.link, page.scope("html"), url=True)
                if not nxt:
                    raise ReadError(f"documents.per_link step {j + 1} found no link on {page.url}")
                link = nxt
            if link not in seen:
                seen.add(link)
                refs.append(DocumentRef(url=link, fileName=name))
        return refs

    async def _fetch_if_html(self, url: str) -> Page | None:
        """GET an intermediate download page; None (body not read) when the URL already serves a file."""
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


def _submit(step: SubmitStep, scope: HtmlScope, headers: dict[str, str]) -> Req:
    forms = [f for f in css(step.form, scope.el) if f.tag == "form"]
    if not forms:
        raise ReadError(f"navigation found no form {step.form!r} on {scope.base_url}")
    form = forms[0]
    fields = dict(form.form_values()) | step.values
    action = urljoin(scope.base_url, form.get("action") or scope.base_url)
    if (form.get("method") or "get").upper() == "POST":
        return Req("POST", action, headers=headers, form=fields)
    parts = urlsplit(action)
    return Req("GET", urlunsplit(parts._replace(query="")), params=fields, headers=headers)


def _json_number(v: str) -> Any:
    try:
        return int(v)
    except ValueError:
        return v


def _short(e: BaseException) -> str:
    msg = str(e).strip().splitlines()[0] if str(e).strip() else ""
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__
