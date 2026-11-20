"""Discovery tools (T-022): find the tender list, its pagination, the link kinds and the real tender page.

Each tool answers with the selectors and template snippets to use, already checked on the live page.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from agents import RunContextWrapper

from watchtower.agent import prune
from watchtower.agent.analysis import discovery as analysis
from watchtower.agent.analysis.recognition import labelled_values, page_lang
from watchtower.agent.analysis.selectors import group_selector
from watchtower.agent.context import AgentContext, Fetched
from watchtower.agent.hints import pagination_hints
from watchtower.agent.pages import clip, get_page, page_scope
from watchtower.agent.registry import tool
from watchtower.engine.extract import HtmlScope, JsonScope, collapse, css

Ctx = RunContextWrapper[AgentContext]

MAX_PAGE_PROBES = 5
_OFFSET_PARAMS = ("start", "offset", "skip", "from", "first")
_SIZE_PARAMS = re.compile(
    r"^(size|page_?size|per_?page|limit|rows|count|taille|tamano|rozmiar|anzahl|max|n|num|results?|hits|nb)$", re.I
)
_PARAMS_BY_LANG = {
    "de": ("seite", "page", "p"),
    "es": ("pagina", "page", "p"),
    "pt": ("pagina", "page", "p"),
    "it": ("pagina", "page", "p"),
    "pl": ("strona", "page", "p"),
    "nl": ("pagina", "page", "p"),
    "fr": ("page", "p"),
}
_RESOLVE_WORDS = re.compile(
    r"ver (la )?licitaci|ver (el )?expediente|ver detalle|details?|detail|zur ausschreibung|zum verfahren|"
    r"mehr|voir (la )?(consultation|annonce|d[ée]tail)|consulter|zobacz|szczeg|bekijk|meer|view (tender|notice|details)"
    r"|full (notice|tender)|vai al|dettagli",
    re.I,
)


@tool()
async def find_repeating_blocks(ctx: Ctx, url: str) -> str:
    """Find the candidate tender lists on an HTML page: groups of same-shaped elements that look like records.

    For each: a verified CSS selector for `list.items` (one match per record), the count, how many have links and
    dates, and the values inside the first record with their selectors relative to the record (ready for item fields).
    """
    page = await get_page(ctx.context, url)
    if isinstance(page, str):
        return page
    scope = page_scope(page)
    if not isinstance(scope, HtmlScope):
        return "Not an HTML page; use `summarize_json` for JSON."
    blocks = analysis.find_repeating_blocks(scope)
    if not blocks:
        return f"{page.url}: no repeating record-like blocks (an empty shell? try `render`)."
    lines = [f"{page.url}: {len(blocks)} candidate list(s), best first"]
    for i, b in enumerate(blocks, 1):
        sel = b.selector or "(no unique selector found; build one with fetch focus)"
        lines.append(f"{i}. items {sel!r}: {b.count} records, {b.with_links} with links, {b.with_dates} with dates")
        for rel, text, href in b.fields:
            lines.append(f"     {rel}: {clip(text, 60)!r}" + (f" href={clip(href, 70)}" if href else ""))
    return "\n".join(lines)


@tool()
async def classify_links(ctx: Ctx, url: str) -> str:
    """Group every link on a page: tender detail pages, documents (by extension and multilingual words such as
    Vergabeunterlagen, DCE, pliegos, dokumentacja), pagination, and the rest. Gives each group's URL pattern, a CSS
    selector and samples. Use it on the list page (detail links, pagination) and a detail page (documents)."""
    page = await get_page(ctx.context, url)
    if isinstance(page, str):
        return page
    scope = page_scope(page)
    if not isinstance(scope, HtmlScope):
        return "Not an HTML page."
    lines = [f"{page.url}:"]
    for g in analysis.classify_links(scope):
        head = f"{g.kind.upper()} ×{g.count}  {g.pattern}"
        if g.selector:
            head += f"  css {g.selector!r}"
        lines.append(head)
        lines += [f"    {clip(t, 50)!r} → {clip(u, 100)}" for t, u in g.links]
    return "\n".join(lines)


@tool()
async def find_pagination(ctx: Ctx, url: str, items: str | None = None) -> str:
    """Find how the list at `url` pages, and prove it: visible controls (next links, page numbers, page fields, JSON
    totals/cursors), then live probes of likely page parameters (`?seite=2`, `?pagina=2`, `?start=10`…) that check
    the next page lists NEW records. `items`: CSS (HTML) or JSONPath (JSON) of one record; omitted → the best
    repeating block. Answers with the `pagination` JSON to put in the template. Each probe is one request."""
    c = ctx.context
    page = await get_page(c, url)
    if isinstance(page, str):
        return page
    scope = page_scope(page)
    if scope is None:
        return "Not a list page (a file)."
    items = items or _auto_items(scope)
    if not items:
        return "No records found on the page to page through; pass `items`."
    first = _record_keys(scope, items)
    if isinstance(first, str):
        return first
    lines = [f"{page.url}: {len(first)} records with items {items!r}"]
    hints = pagination_hints(page.text, page.url, len(first))
    lines.append("visible controls: " + ("; ".join(hints) if hints else "none"))

    nxt = _next_link(scope)
    if nxt:
        lines.append("SUGGESTED: " + json.dumps({"kind": "next_link", "link": nxt}))
        return "\n".join(lines)
    if any("form field" in h or "name=" in h for h in hints) and not _PAGE_URL.search(page.url + " ".join(hints)):
        lines.append(
            'The page buttons are form fields: use param pagination with location "form" on the POST list request '
            "(name = the field's name)."
        )

    candidates = _candidates(page, scope, hints, len(first))
    for name, value, kind in candidates[:MAX_PAGE_PROBES]:
        probe_url = _with_param(page.url, name, value)
        nxt_page = await get_page(c, probe_url)
        if isinstance(nxt_page, str) or (nxt_page.status or 0) >= 400:
            lines.append(f"probe {name}={value}: failed")
            continue
        nscope = page_scope(nxt_page)
        keys = _record_keys(nscope, items) if nscope is not None else "not a page"
        if isinstance(keys, str):
            lines.append(f"probe {name}={value}: {keys}")
            continue
        new = [k for k in keys if k not in first]
        lines.append(f"probe {name}={value}: {len(keys)} records, {len(new)} new")
        if keys and len(new) >= max(1, len(keys) // 2):
            if kind == "offset":
                snippet = {"kind": "param", "name": name, "location": "query", "start": 0, "step": len(first)}
            else:
                start = await _first_page_number(c, page.url, name, items, first)
                snippet = {"kind": "param", "name": name, "location": "query", "start": start, "step": 1}
                lines.append(f"pages are numbered from {start} ({name}={start} is the first page)")
            lines.append("SUGGESTED: " + json.dumps(snippet))
            return "\n".join(lines)
    lines.append(
        "No working page parameter found: the list may fit on one page (pagination none), or page through a "
        "form or a JSON API (look at `render`)."
    )
    return "\n".join(lines)


_PAGE_URL = re.compile(r"[?&](page|pagina|seite|strona|p|start|offset)=", re.I)


async def _first_page_number(c: AgentContext, url: str, name: str, items: str, first: list[str]) -> int:
    """1 if `name=1` shows the first page again, 0 if it shows a page in between (zero-based numbering)."""
    one = await get_page(c, _with_param(url, name, "1"))
    if isinstance(one, str) or (scope := page_scope(one)) is None:
        return 1
    keys = _record_keys(scope, items)
    if isinstance(keys, list) and keys and not set(keys) & set(first):
        return 0
    return 1


def _auto_items(scope: HtmlScope | JsonScope) -> str | None:
    if isinstance(scope, JsonScope):
        text = prune.summarize_json(scope.value)
        m = re.search(r"^\s+(\$\S*\[\*\])", text, re.M)
        return m.group(1) if m else None
    blocks = analysis.find_repeating_blocks(scope, limit=1)
    return blocks[0].selector if blocks and blocks[0].selector else None


def _record_keys(scope: HtmlScope | JsonScope, items: str) -> list[str] | str:
    try:
        if isinstance(scope, JsonScope):
            from jsonpath_ng.ext import parse

            return [json.dumps(m.value, sort_keys=True, ensure_ascii=False) for m in parse(items).find(scope.value)]
        return [collapse(e.text_content()) for e in css(items, scope.el)]
    except Exception as e:  # bad selector / jsonpath
        return f"items {items!r} can't be used here: {e}"


def _next_link(scope: HtmlScope | JsonScope) -> dict[str, Any] | None:
    if isinstance(scope, JsonScope):
        return None
    for el in scope.el.iter("a"):
        if (el.get("rel") or "").lower() == "next" and el.get("href"):
            return {"by": "css", "expr": "a[rel=next]", "attr": "href"}
    from watchtower.agent.hints import _NEXT_TEXT

    for el in scope.el.iter("a"):
        label = collapse(el.text_content()) or (el.get("aria-label") or "")
        if el.get("href") and _NEXT_TEXT.match(label.strip()):
            sel = group_selector([el], scope.el)
            if sel:
                return {"by": "css", "expr": sel, "attr": "href"}
            return {"by": "xpath", "expr": f"//a[normalize-space()='{label.strip()}']", "attr": "href"}
    return None


def _candidates(
    page: Fetched, scope: HtmlScope | JsonScope, hints: list[str], per_page: int
) -> list[tuple[str, str, str]]:
    """(param name, value for the second page, page|offset), most likely first."""
    names: list[str] = []
    for h in hints:
        names += re.findall(r"[?&;](\w+)=\d", h) + re.findall(r'name="(\w+)"', h)
    for k, v in parse_qsl(urlsplit(page.url).query):
        if v.isdigit():
            names.append(k)
    lang = (page_lang(scope) if isinstance(scope, HtmlScope) else None) or ""
    names += list(_PARAMS_BY_LANG.get(lang[:2].lower(), ())) + ["page", "p", "start", "offset"]
    out: list[tuple[str, str, str]] = []
    seen = set()
    for name in names:
        if name.lower() in seen or _SIZE_PARAMS.match(name):
            continue
        seen.add(name.lower())
        if name.lower() in _OFFSET_PARAMS:
            out.append((name, str(per_page), "offset"))
        else:
            out.append((name, "2", "page"))
    return out


def _with_param(url: str, name: str, value: str) -> str:
    parts = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(parts.query) if k != name] + [(name, value)]
    return urlunsplit(parts._replace(query=urlencode(q)))


@tool()
async def resolve_tender_page(ctx: Ctx, url: str) -> str:
    """Check whether `url` (a link from the list) is the real tender page or only a notice/summary page that links on
    to it (a known trap). Scores the page and its likely onward links by content (labelled fields, dates, document
    links) and, when an onward page is richer, gives the `detail.resolve` follow step to add."""
    c = ctx.context
    page = await get_page(c, url)
    if isinstance(page, str):
        return page
    scope = page_scope(page)
    if not isinstance(scope, HtmlScope):
        return "Not an HTML page."
    base = _richness(scope)
    lines = [f"{page.url}: {_describe_richness(base)}"]
    host = urlsplit(page.url).hostname
    onward = []
    for a in scope.el.iter("a"):
        href, text = a.get("href"), collapse(a.text_content())
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        target = urljoin(page.url, href)
        if urlsplit(target).hostname != host or target.split("#")[0] == page.url:
            continue
        if _RESOLVE_WORDS.search(text) or _RESOLVE_WORDS.search(a.get("title") or ""):
            onward.append((a, target, text))
    if not onward:
        lines.append("No onward 'see the full tender' link: this is the tender page itself.")
        return "\n".join(lines)
    best = None
    for a, target, text in onward[:2]:
        nxt = await get_page(c, target)
        if isinstance(nxt, str):
            lines.append(f"→ {text!r} {target}: {nxt}")
            continue
        nscope = page_scope(nxt)
        if not isinstance(nscope, HtmlScope):
            continue
        r = _richness(nscope)
        lines.append(f"→ {text!r} {nxt.url}: {_describe_richness(r)}")
        if sum(r) > 2 * max(1, sum(base)) and (best is None or sum(r) > best[0]):
            best = (sum(r), a)
    if best is None:
        lines.append("The page itself is at least as rich: no resolve step needed.")
        return "\n".join(lines)
    sel = group_selector([best[1]], scope.el)
    link = (
        {"by": "css", "expr": sel, "attr": "href"}
        if sel
        else {
            "by": "xpath",
            "expr": f"//a[normalize-space()='{collapse(best[1].text_content())}']",
            "attr": "href",
        }
    )
    lines.append("SUMMARY PAGE. Add to detail: " + json.dumps({"resolve": [{"action": "follow", "link": link}]}))
    return "\n".join(lines)


def _richness(scope: HtmlScope) -> tuple[int, int, int]:
    labels = len(labelled_values(scope))
    dates = len(re.findall(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b", " ".join(scope.el.itertext())))
    docs = sum(g.count for g in analysis.classify_links(scope) if g.kind == "document")
    return labels, dates, docs


def _describe_richness(r: tuple[int, int, int]) -> str:
    return f"{r[0]} labelled fields, {r[1]} dates, {r[2]} document links"


@tool()
async def summarize_json(ctx: Ctx, url: str, items: str) -> str:
    """Field statistics over the records of a JSON API: for every key under `items` (JSONPath to the records, e.g.
    `$.data[*]`): type, how many records have it, distinct values and samples. Use it to map JSON keys onto contract
    fields (nested objects show as `parent.child`)."""
    page = await get_page(ctx.context, url)
    if isinstance(page, str):
        return page
    scope = page_scope(page)
    if not isinstance(scope, JsonScope):
        return "Not a JSON response."
    from jsonpath_ng.ext import parse

    try:
        records = [m.value for m in parse(items).find(scope.value)]
    except Exception as e:  # jsonpath-ng raises several types
        return f"Invalid JSONPath {items!r}: {e}"
    records = [r for r in records if isinstance(r, dict)]
    if not records:
        return f"{items!r} matches no JSON objects."
    stats: dict[str, list[Any]] = {}
    for r in records:
        for key, v in _flatten(r):
            stats.setdefault(key, []).append(v)
    lines = [f"{len(records)} records at {items}; key: type, present in, distinct, samples"]
    for key, vals in list(stats.items())[:40]:
        types = sorted({type(v).__name__ for v in vals})
        distinct = len({json.dumps(v, sort_keys=True) for v in vals})
        samples = ", ".join(
            clip(json.dumps(v, ensure_ascii=False), 40) for v in list(dict.fromkeys(map(str, vals)))[:3]
        )
        lines.append(f"  $.{key}: {'/'.join(types)}, {len(vals)}/{len(records)}, {distinct} distinct: {samples}")
    return "\n".join(lines)


def _flatten(obj: dict[str, Any], prefix: str = "", depth: int = 0):
    for k, v in obj.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and depth < 3:
            yield from _flatten(v, key + ".", depth + 1)
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            yield f"{key}[*]", f"[{len(v)} objects]"
            yield from _flatten(v[0], key + "[0].", depth + 1)
        else:
            yield key, v
