"""Repair tools (T-022, investigate only): what changed on the portal, and what a new template reads differently."""

from __future__ import annotations

from agents import RunContextWrapper

from watchtower.agent.analysis.diff import diff_reads, diff_snapshots
from watchtower.agent.context import AgentContext, Fetched
from watchtower.agent.pages import get_page, page_scope
from watchtower.agent.registry import INVESTIGATE, tool
from watchtower.agent.tools.core import _parse_template
from watchtower.engine import run_template
from watchtower.engine.executor import first_list_page
from watchtower.engine.snapshot import snapshot

Ctx = RunContextWrapper[AgentContext]
DIFF_SAMPLE = 5


@tool(scenarios=INVESTIGATE)
async def diff_structure(ctx: Ctx, url: str | None = None) -> str:
    """Compare the list page's structure now with the snapshot from the last good read: renamed classes, moved or
    removed elements, a changed JSON shape. Shows exactly what broke the template. `url`: the page to compare (default:
    the first list page the current template reaches, after its form/navigation steps)."""
    c = ctx.context
    if c.last_good_snapshot is None:
        return "No snapshot of a good read is available for this portal."
    if url:
        page = await get_page(c, url)
        if isinstance(page, str):
            return page
        scope, final = page_scope(page), page.url
    elif c.template is not None:
        try:
            p = await first_list_page(c.template, c.client, c.browser)
        except Exception as e:  # ReadError or a missing browser
            return f"Could not load the list page: {e}"
        scope, final = page_scope(Fetched(p.url, p.status, p.headers.get("content-type", ""), p.text)), p.url
    else:
        return "Give the list page `url`."
    if scope is None:
        return "Not a page (a file)."
    return "\n".join(diff_snapshots(c.last_good_snapshot, snapshot(scope, final)))


@tool(scenarios=INVESTIGATE)
async def diff_outputs(ctx: Ctx, new_template: str, old_template: str | None = None) -> str:
    """Read a sample with two templates and compare: tenders only one of them finds, and per field how many shared
    tenders get a different value. `old_template` defaults to the template in use. Use it to check that a repair
    restores what the old template read (and that an audit finding is real)."""
    c = ctx.context
    new = _parse_template(new_template)
    if isinstance(new, str):
        return "new_template: " + new
    old = _parse_template(old_template) if old_template else c.template
    if old is None:
        return "No template in use; pass `old_template`."
    if isinstance(old, str):
        return "old_template: " + old
    a = await run_template(old, c.client, c.browser, limit=DIFF_SAMPLE)
    b = await run_template(new, c.client, c.browser, limit=DIFF_SAMPLE)
    lines = diff_reads(a, b)
    if a.errors:
        lines.append(f"A errors: {'; '.join(a.errors[:2])}")
    if b.errors:
        lines.append(f"B errors: {'; '.join(b.errors[:2])}")
    return "\n".join(["A = old, B = new (first tenders only)", *lines])
