"""Loading and probing pages for the agent's tools: one per-run cache, all traffic through `net` (politeness).

page = await get_page(c, url)                 # plain HTTP GET, cached; a str is an error sentence for the model
page = await get_page(c, url, rendered=True)  # headless browser, cached separately
scope = page_scope(page)                      # engine HtmlScope / JsonScope, or None for files
probe = await probe_document(c, url)          # does a document link deliver a file?
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from email.message import Message

import httpx

from watchtower.agent.blocks import tender_key
from watchtower.agent.context import AgentContext, Fetched
from watchtower.engine.extract import HtmlScope, JsonScope, parse_html, parse_json
from watchtower.net import BlockSignal, detect_block

MAX_BODY_BYTES = 3_000_000
DOC_PROBE_BYTES = 16_384
_TEXT_TYPES = ("html", "json", "xml", "text", "javascript")


async def get_page(c: AgentContext, url: str, *, rendered: bool = False) -> Fetched | str:
    """The page at `url` (cached per run). Non-text responses keep only a `[file: …]` description, not the body."""
    if not url.startswith(("http://", "https://")):
        return f"Not an absolute http(s) URL: {url!r}"
    key = ("render:" + url) if rendered else url
    if (page := c.pages.get(key)) is not None:
        return page
    if rendered:
        if c.browser is None:
            return "No browser is available in this run; work with plain fetches."
        try:
            r = await c.browser.render(url)
        except Exception as e:  # Playwright raises its own error types
            return f"Rendering {url} failed: {short_err(e)}"
        page = Fetched(r.url, r.status, "text/html", r.html, r.json_responses())
    else:
        try:
            async with c.client.stream("GET", url) as resp:
                ctype = resp.headers.get("content-type", "")
                if any(t in ctype for t in _TEXT_TYPES) or not ctype:
                    body = bytearray()
                    async for chunk in resp.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_BODY_BYTES:
                            break
                    text = bytes(body[:MAX_BODY_BYTES]).decode(resp.encoding or "utf-8", errors="replace")
                else:
                    size = resp.headers.get("content-length", "unknown")
                    disp = resp.headers.get("content-disposition", "")
                    text = f"[file: {ctype or 'unknown type'}, {size} bytes" + (f", {disp}" if disp else "") + "]"
                page = Fetched(str(resp.url), resp.status_code, ctype, text)
        except httpx.HTTPError as e:
            return f"GET {url} failed: {short_err(e)}"
    c.pages[key] = page
    _record_page_block(c, url, page)
    return page


def _record_page_block(c: AgentContext, requested: str, page: Fetched) -> None:
    """A page that IS a login or captcha wall counts as a block of the tender (or list) it was meant to show: keyed by
    the URL asked for, since every blocked page may redirect to the same login page."""
    if page.text.startswith("[file:") or is_json(page):
        return
    strong = [s for s in detect_block(page.text, url=page.url, status=page.status) if s.strong]
    if strong:
        s = strong[0]
        what = f"{requested} leads to a {s.kind} page ({'; '.join(s.evidence[:2])})"
        c.blocks.add_block(tender_key(requested), requested, what)


def is_json(page: Fetched) -> bool:
    return "json" in page.content_type or page.text.lstrip()[:1] in ("{", "[")


def page_scope(page: Fetched) -> HtmlScope | JsonScope | None:
    """The page parsed exactly as the engine parses it; None for files and unparseable JSON."""
    if page.text.startswith("[file:"):
        return None
    if is_json(page):
        try:
            return parse_json(page.text, page.url)
        except ValueError:
            if "json" in page.content_type:
                return None
    return parse_html(page.text, page.url)


@dataclass
class DocProbe:
    url: str
    final_url: str = ""
    status: int | None = None
    content_type: str = ""
    size: str | None = None  # Content-Length, if sent
    file_name: str | None = None  # from Content-Disposition, else the URL
    is_file: bool = False
    blocks: list[BlockSignal] = field(default_factory=list)  # login/captcha signals on an HTML answer
    error: str | None = None

    def sentence(self) -> str:
        name = self.file_name or self.url.rsplit("/", 1)[-1]
        moved = f" (ended at {self.final_url})" if self.final_url and self.final_url != self.url else ""
        if self.error:
            return f"{name!r}: failed to download ({self.error})"
        if self.status is not None and self.status >= 400:
            return f"{name!r}: HTTP {self.status}{moved}"
        if self.blocks:
            what = " / ".join(f"{s.kind} ({'; '.join(s.evidence[:2])})" for s in self.blocks)
            return f"{name!r}: leads to a {what} page instead of a file{moved}"
        if not self.is_file:
            return f"{name!r}: an HTML page, not a file{moved}"
        return f"{name!r}: {self.content_type or 'unknown type'}, {self.size or '?'} bytes, file OK{moved}"


async def probe_document(
    c: AgentContext, url: str, *, tender: str | None = None, source: str | None = None
) -> DocProbe:
    """Download the first bytes of a document link: a file, or an HTML page (download page, login, captcha)?

    The result is recorded as block evidence (`ctx.blocks`) for `tender` (its page URL or id; else guessed from the
    URL), with `source` the page the link was found on."""
    probe = await _probe(c, url)
    key = tender_key(url, tender=tender, document=True)
    if probe.is_file:
        c.blocks.downloaded.add(key)
    elif probe.blocks or probe.status in (401, 403):
        c.blocks.add_block(key, url, "document " + probe.sentence(), source)
    return probe


async def _probe(c: AgentContext, url: str) -> DocProbe:
    probe = DocProbe(url)
    try:
        async with c.client.stream("GET", url) as resp:
            probe.final_url, probe.status = str(resp.url), resp.status_code
            probe.content_type = resp.headers.get("content-type", "")
            probe.size = resp.headers.get("content-length")
            probe.file_name = _disposition_name(resp.headers.get("content-disposition", ""))
            head = bytearray()
            async for chunk in resp.aiter_bytes():
                head.extend(chunk)
                if len(head) >= DOC_PROBE_BYTES:
                    break
    except httpx.HTTPError as e:
        probe.error = short_err(e)
        return probe
    looks_html = bytes(head[:200]).lstrip().lower().startswith((b"<!doctype html", b"<html"))
    if "html" in probe.content_type or looks_html:
        text = bytes(head).decode("utf-8", errors="replace")
        probe.blocks = detect_block(text, url=probe.final_url, status=probe.status)
    else:
        probe.is_file = probe.status is not None and probe.status < 400
    return probe


def _disposition_name(header: str) -> str | None:
    if not header:
        return None
    msg = Message()
    msg["content-disposition"] = header
    name = msg.get_filename()
    return name or None


def clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def short_err(e: BaseException) -> str:
    return clip(f"{type(e).__name__}: {e}".splitlines()[0], 200)


def json_text(value: object, n: int = 600) -> str:
    return clip(json.dumps(value, ensure_ascii=False), n)
