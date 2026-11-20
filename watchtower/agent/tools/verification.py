"""Verification tools (T-022): test one extraction rule on a live page, check a document link.

`try_template` (the full dry run with field coverage) is a core tool (tools/core.py).
"""

from __future__ import annotations

import json

from agents import RunContextWrapper
from pydantic import ValidationError

from watchtower.agent import pages
from watchtower.agent.analysis.discovery import classify_links
from watchtower.agent.context import AgentContext
from watchtower.agent.pages import clip, get_page, page_scope
from watchtower.agent.registry import tool
from watchtower.engine.extract import ExtractError, HtmlScope, select, values
from watchtower.engine.template import Extract

Ctx = RunContextWrapper[AgentContext]
MAX_SCOPES = 5


@tool()
async def test_selector(
    ctx: Ctx,
    url: str,
    by: str,
    expr: str,
    attr: str = "text",
    pattern: str | None = None,
    items: str | None = None,
) -> str:
    """Run one extraction rule exactly as the engine would: `by` css|xpath|jsonpath|regex, `expr`, `attr` (text, html,
    href or another attribute), `pattern` (regex, group 1 kept). With `items` (css/xpath/jsonpath of the list records),
    the rule runs inside each of the first records, as an item field does; otherwise on the whole page, as a detail
    field does. Shows the values found per scope."""
    page = await get_page(ctx.context, url)
    if isinstance(page, str):
        return page
    scope = page_scope(page)
    if scope is None:
        return "Not a page (a file)."
    try:
        rule = Extract(by=by, expr=expr, attr=attr, pattern=pattern, many=True)
        item_rule = Extract(by="jsonpath" if items and items.startswith("$") else "css", expr=items) if items else None
    except ValidationError as e:
        return "Invalid rule: " + "; ".join(err["msg"] for err in e.errors()[:3])
    try:
        scopes = select(item_rule, scope) if item_rule else [scope]
    except ExtractError as e:
        return f"items: {e}"
    if not scopes:
        return f"items {items!r} matches nothing on {page.url}"
    is_url = attr in ("href", "src")
    lines = [f"{page.url}: {len(scopes)} scope(s)" + (f" from items {items!r}" if items else " (whole page)")]
    empty = 0
    for i, s in enumerate(scopes[:MAX_SCOPES], 1):
        try:
            found = values(rule, s, url=is_url)
        except ExtractError as e:
            return f"rule can't run here: {e}"
        empty += not found
        shown = ", ".join(repr(clip(v, 60)) for v in found[:4]) + (f" … +{len(found) - 4}" if len(found) > 4 else "")
        lines.append(f"  {i}: {len(found)} value(s): {shown or '—'}")
    if len(scopes) > MAX_SCOPES:
        rest = scopes[MAX_SCOPES:]
        empty += sum(1 for s in rest if not values(rule, s, url=is_url))
        lines.append(f"  … {len(rest)} more scopes")
    lines.append(f"empty in {empty}/{len(scopes)} scope(s)")
    return "\n".join(lines)


@tool()
async def probe_document(ctx: Ctx, url: str) -> str:
    """Check one document link without downloading it fully: content type, size, file name, and whether it is really a
    file or an HTML page (a download page that needs `per_link` steps, a login or captcha page, an error)."""
    c = ctx.context
    probe = await pages.probe_document(c, url)
    out = probe.sentence()
    if probe.is_file or probe.blocks or probe.error or (probe.status or 0) >= 400:
        return out
    # A download page: find the link on it that delivers the file.
    page = await get_page(c, url)
    scope = page_scope(page) if not isinstance(page, str) else None
    if not isinstance(scope, HtmlScope):
        return out
    docs = [g for g in classify_links(scope) if g.kind == "document"]
    if not docs:
        return out + "\nNo document link on that page."
    g = docs[0]
    link = {"by": "css", "expr": g.selector, "attr": "href"} if g.selector else None
    step = {"action": "follow", "link": link} if link else None
    out += f"\nThe page links to {g.count} document(s), e.g. {g.links[0][1]}"
    if step and "(not unique" not in (g.selector or ""):
        out += "\nAdd to documents.per_link: " + json.dumps(step, ensure_ascii=False)
    return out
