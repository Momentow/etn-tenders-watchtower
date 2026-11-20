"""Core tools (T-014): look at pages, dry-run templates, and end the run with a template or a report.

Every tool returns compact text, never raw pages, and goes through `net` (politeness). Fetched pages are cached per run,
so looking at the same page again (another `focus`) costs no request.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from agents import RunContextWrapper
from pydantic import ValidationError

from watchtower.agent.context import AgentContext, Fetched
from watchtower.agent.hints import pagination_hints
from watchtower.agent.outcomes import Blocked, TemplateFound, Transient, Unreadable
from watchtower.agent.prune import outline, summarize_json
from watchtower.agent.registry import tool
from watchtower.engine import ReadResult, run_template
from watchtower.engine.executor import first_list_page
from watchtower.engine.template import Template
from watchtower.net import detect_block
from watchtower.validate import check

log = logging.getLogger(__name__)

MAX_BODY_BYTES = 3_000_000
LIST_CHECK_PAGES = 3
DEFAULT_SAMPLE = 3
MAX_SAMPLE = 10
_TEXT_TYPES = ("html", "json", "xml", "text", "javascript")
CHECKED_FIELDS = (
    "url",
    "title",
    "buyerName",
    "publicationDate",
    "submissionDeadline",
    "referenceNumber",
    "description",
    "procedureType",
    "contractNature",
    "cpvCodes",
    "placeOfPerformance",
)

Ctx = RunContextWrapper[AgentContext]


# --------------------------------------------------------------------------- looking at pages


@tool()
async def fetch(ctx: Ctx, url: str, focus: str | None = None) -> str:
    """GET a URL over plain HTTP (no JavaScript) and summarize it.

    HTML → a pruned outline (tag#id.class[attrs] "text"; repeated siblings collapsed). JSON → key shape with sample
    values and the arrays of objects with their JSONPath. Files → type, size and file name only.
    `focus`: a CSS selector (HTML) or JSONPath (JSON); shows only its matches and their count. Use it to zoom in and to
    check a selector before using it in a template. Repeated calls for the same URL are free (cached).
    """
    page = await _get(ctx.context, url)
    if isinstance(page, str):
        return page
    return _describe(page, focus)


@tool()
async def render(ctx: Ctx, url: str, focus: str | None = None) -> str:
    """Load a URL in a headless browser (runs JavaScript) and summarize the rendered page plus the JSON/XHR responses
    it loaded. Use it when `fetch` shows an empty shell; a JSON API found here is best read directly with a `json`
    list template (no browser needed). Slower than `fetch`: use only when needed. `focus` as in `fetch` (CSS).
    """
    c = ctx.context
    if c.browser is None:
        return "No browser is available in this run; work with `fetch`."
    key = "render:" + url
    page = c.pages.get(key)
    if page is None:
        try:
            r = await c.browser.render(url)
        except Exception as e:  # Playwright raises its own error types
            return f"Rendering {url} failed: {_short_err(e)}"
        page = Fetched(r.url, r.status, "text/html", r.html, r.json_responses())
        c.pages[key] = page
    out = [_describe(page, focus, max_chars=5000)]
    seen: set[str] = set()
    apis = []
    for resp in page.responses:
        if resp.url in seen:
            continue
        seen.add(resp.url)
        try:
            value = json.loads(resp.body or "")
        except ValueError:
            continue
        apis.append(
            f"{resp.method} {resp.url} → {resp.status}"
            + (" (truncated)" if resp.truncated else "")
            + "\n"
            + summarize_json(value, max_chars=1500)
        )
    out.append(f"JSON responses captured: {len(apis)}" + ("" if apis else " (none)"))
    out.extend(apis[:5])
    return "\n".join(out)


async def _get(c: AgentContext, url: str) -> Fetched | str:
    if not url.startswith(("http://", "https://")):
        return f"Not an absolute http(s) URL: {url!r}"
    if (page := c.pages.get(url)) is not None:
        return page
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
        return f"GET {url} failed: {_short_err(e)}"
    c.pages[url] = page
    return page


def _describe(page: Fetched, focus: str | None, max_chars: int = 9000) -> str:
    head = f"{page.url} → HTTP {page.status}, {page.content_type or 'no content type'}"
    lines = [head]
    if page.text.startswith("[file:"):
        return head + "\n" + page.text
    if "json" in page.content_type or page.text.lstrip()[:1] in ("{", "["):
        try:
            value = json.loads(page.text)
        except ValueError:
            value = None
        if value is not None:
            lines.append(_json_focus(value, focus) if focus else summarize_json(value, max_chars=max_chars))
            return "\n".join(lines)
    for s in detect_block(page.text, url=page.url, status=page.status):
        lines.append(
            f"block signal: {s.kind} ({'page is the block' if s.strong else 'widget on the page'}): "
            + "; ".join(s.evidence[:3])
        )
    lines.append(outline(page.text, page.url, focus=focus, max_chars=max_chars))
    return "\n".join(lines)


def _json_focus(value: Any, expr: str) -> str:
    from jsonpath_ng.ext import parse

    try:
        matches = [m.value for m in parse(expr).find(value)]
    except Exception as e:  # jsonpath-ng raises several error types
        return f"Invalid JSONPath {expr!r}: {e}"
    shown = [_clip(json.dumps(v, ensure_ascii=False), 600) for v in matches[:3]]
    more = f"\n… {len(matches) - 3} more matches" if len(matches) > 3 else ""
    return f"focus {expr!r}: {len(matches)} match(es)\n" + "\n".join(shown) + more


# --------------------------------------------------------------------------- dry runs


@tool()
async def try_template(ctx: Ctx, template: str, sample: int = DEFAULT_SAMPLE) -> str:
    """Dry-run a template (JSON text of a Template) with the real engine and report what it reads.

    Reports: schema errors with their path; a list check (up to 3 list pages: item count, pagination, duplicate ids);
    a sample read of `sample` tenders through the detail pages (field coverage, documents, values, errors); and the
    validator's verdict. Call it after every change; submit only a template whose verdict is GOOD.
    """
    report, _ = await _dry_run(ctx.context, template, max(1, min(sample, MAX_SAMPLE)))
    return report


async def _dry_run(c: AgentContext, raw: str, sample: int) -> tuple[str, Template | None]:
    parsed = _parse_template(raw)
    if isinstance(parsed, str):
        return parsed, None
    t = parsed
    lines: list[str] = []
    problems: list[str] = []  # block submission
    warnings: list[str] = []  # likely incomplete; fix or rule out before submitting

    list_only = _list_only(t)
    listed = None
    if list_only is not None:
        lr = await run_template(list_only, c.client, c.browser)
        listed = len(lr.tenders)
        lines.append(
            f"LIST: {listed} tenders on {lr.stats.list_pages} list page(s)"
            + (" (stopped at the check's page cap; more pages may follow)" if lr.stats.hit_max_pages else "")
            + (f", {lr.stats.duplicate_ids} duplicate ids" if lr.stats.duplicate_ids else "")
            + (f", {lr.stats.skipped_items} items without id" if lr.stats.skipped_items else "")
        )
        if lr.tenders:
            lines.append("  first ids: " + ", ".join(x.id for x in lr.tenders[:5]))
        problems += [f"list: {e}" for e in lr.errors]
        if listed == 0:
            problems.append("the list reads no tenders")
        if lr.stats.duplicate_ids and lr.stats.duplicate_ids >= listed:
            problems.append("pagination repeats the same items")

    if listed == 0 or t.list.pagination.kind == "none":
        lines += await _first_page_checks(c, t, listed, warnings)

    rr = await run_template(t, c.client, c.browser, limit=sample)
    lines.append(f"SAMPLE: {len(rr.tenders)} tender(s) read with details ({rr.stats.requests} requests)")
    lines += _coverage(rr)
    lines += _samples(rr)
    problems += rr.errors
    if not rr.tenders:
        problems.append("the sample read returned no tenders")
    if c.history.is_empty and rr.tenders:
        problems += check(rr, c.history).problems
    missing_title = [x.id for x in rr.tenders if not x.title]
    if missing_title:
        problems.append(f"no title for {', '.join(missing_title[:3])}")
    if rr.tenders:
        warnings += _completeness_warnings(t, rr)

    good = not problems
    c.tried[_canonical(t)] = good
    if warnings:
        lines.append("WARNINGS (fix each, or be sure it doesn't apply to this portal):\n- " + "\n- ".join(warnings))
    lines.append("VERDICT: GOOD" if good else "VERDICT: NOT GOOD\n- " + "\n- ".join(problems[:10]))
    return "\n".join(lines), t


async def _first_page_checks(c: AgentContext, t: Template, listed: int | None, warnings: list[str]) -> list[str]:
    """Look at the first list page as the engine reaches it: show it when nothing was read, find missed pagination."""
    try:
        page = await first_list_page(t, c.client, c.browser)
    except Exception as e:  # ReadError, or a missing browser
        return [f"  (could not load the first list page: {_short_err(e)})"]
    out = []
    if listed == 0:
        if "json" in page.headers.get("content-type", "") or page.text.lstrip()[:1] in ("{", "["):
            out.append("  first list page (JSON) as the engine sees it:\n" + _describe(_as_fetched(page), None, 2500))
        else:
            out.append("  first list page as the engine sees it:\n" + outline(page.text, page.url, max_chars=2500))
    if t.list.pagination.kind == "none":
        hints = pagination_hints(page.text, page.url, listed)
        if hints:
            warnings.append(
                "the list page shows more pages but the template has no pagination, so later pages are missed: "
                + "; ".join(hints)
            )
    return out


def _as_fetched(page: Any) -> Fetched:
    return Fetched(page.url, page.status, page.headers.get("content-type", ""), page.text)


def _completeness_warnings(t: Template, r: ReadResult) -> list[str]:
    out = []
    n = len(r.tenders)
    if not any(x.documents for x in r.tenders):
        where = "the detail page" if t.detail else "the list row"
        out.append(
            f"no documents on any of the {n} sampled tenders: look for document links (or a page that lists them) on "
            f"{where}; leave `documents` out only if the portal publishes none"
        )
    for f in ("buyerName", "publicationDate", "submissionDeadline"):
        if all(getattr(x, f) is None for x in r.tenders):
            out.append(f"`{f}` is empty on every sampled tender: add a rule if the portal shows it anywhere")
    return out


def _parse_template(raw: str) -> Template | str:
    try:
        data = json.loads(raw)
    except ValueError as e:
        return f"Template is not valid JSON: {e}"
    try:
        return Template.model_validate(data)
    except ValidationError as e:
        errs = []
        for err in e.errors()[:8]:
            loc = ".".join(str(p) for p in err["loc"])
            errs.append(f"- {loc}: {err['msg']}")
        more = f"\n… {e.error_count() - 8} more" if e.error_count() > 8 else ""
        return "Template rejected by the schema:\n" + "\n".join(errs) + more


def _list_only(t: Template) -> Template | None:
    """The template reduced to its list (item fields only, no detail pages, ≤ LIST_CHECK_PAGES pages)."""
    d = t.model_dump(mode="json", exclude_none=True)
    detail = d.pop("detail", None)
    fields = {k: v for k, v in d["fields"].items() if k != "placeOfPerformance" and v.get("on", "item") == "item"}
    if "url" not in fields:
        if detail and detail.get("link"):
            fields["url"] = detail["link"]
        else:
            fields["url"] = {"by": "const", "expr": t.list.request.url}
    d["fields"] = fields
    if d.get("documents", {}).get("on", "detail") == "detail":
        d.pop("documents", None)
    if d.get("status", {}).get("value", {}).get("on", "item") == "detail":
        d.pop("status", None)
    pg = d["list"].get("pagination", {})
    if "max_pages" in pg or pg.get("kind", "none") != "none":
        pg["max_pages"] = min(pg.get("max_pages", 50), LIST_CHECK_PAGES)
    try:
        return Template.model_validate(d)
    except ValidationError:
        return None


def _coverage(r: ReadResult) -> list[str]:
    n = len(r.tenders)
    if not n:
        return []
    parts = []
    for f in CHECKED_FIELDS:
        filled = sum(1 for x in r.tenders if getattr(x, f) not in (None, [], {}))
        parts.append(f"{f} {filled}/{n}")
    docs = [len(x.documents) for x in r.tenders]
    parts.append(f"documents {sum(1 for d in docs if d)}/{n} ({sum(docs)} files)")
    incomplete = sum(1 for x in r.tenders if not x.complete)
    if incomplete:
        parts.append(f"incomplete {incomplete}/{n}")
    return ["  coverage: " + ", ".join(parts)]


def _samples(r: ReadResult) -> list[str]:
    out = []
    for x in r.tenders[:2]:
        data = x.model_dump(exclude_none=True, exclude={"complete"})
        docs = data.pop("documents", [])
        if isinstance(data.get("description"), str):
            data["description"] = _clip(data["description"], 120)
        line = "  " + _clip(json.dumps(data, ensure_ascii=False), 900)
        if docs:
            names = [f"{d.get('fileName') or '?'} <{_clip(d['url'], 90)}>" for d in docs[:4]]
            line += f"\n    documents ({len(docs)}): " + "; ".join(names)
        out.append(line)
    return out


def _canonical(t: Template) -> str:
    return t.model_dump_json(exclude_defaults=True)


# --------------------------------------------------------------------------- ending the run


@tool()
async def submit_template(ctx: Ctx, template: str, notes: str) -> str:
    """Submit the final template. It must be a template whose last `try_template` verdict was GOOD (it is dry-run
    again if it wasn't tried). `notes`: one or two sentences on how the portal is read and anything unusual.
    """
    c = ctx.context
    parsed = _parse_template(template)
    if isinstance(parsed, str):
        return parsed
    if not c.tried.get(_canonical(parsed)):
        report, _ = await _dry_run(c, template, DEFAULT_SAMPLE)
        if not c.tried.get(_canonical(parsed)):
            return "Not accepted: the dry run is not GOOD. Fix the problems and try again.\n" + report
    c.outcome = TemplateFound(template=parsed.model_copy(update={"notes": parsed.notes or notes}), notes=notes)
    return "Accepted."


@tool()
async def report_blocked(ctx: Ctx, reason: str) -> str:
    """End the run: the tenders or their documents can't be read without logging in or solving a captcha. Only after
    checking: a captcha or login box on a page is not a block if the list and the documents still load without it.
    `reason`: one plain sentence for operators, e.g. "Documents require a login (redirect to /login)."
    """
    ctx.context.outcome = Blocked(reason=reason)
    return "Reported."


@tool()
async def report_unreadable(ctx: Ctx, reason: str) -> str:
    """End the run: the site has no public tender list we can read (not a procurement portal, no tenders shown, or a
    structure a template can't express). `reason`: one plain sentence for operators.
    """
    ctx.context.outcome = Unreadable(reason=reason)
    return "Reported."


@tool()
async def report_transient(ctx: Ctx, reason: str) -> str:
    """End the run: the portal is temporarily failing (timeouts, 5xx errors, maintenance page), so nothing can be
    learned now; it will be retried later. `reason`: one plain sentence with what was seen.
    """
    ctx.context.outcome = Transient(reason=reason)
    return "Reported."


# --------------------------------------------------------------------------- helpers


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _short_err(e: BaseException) -> str:
    return _clip(f"{type(e).__name__}: {e}".splitlines()[0], 200)
