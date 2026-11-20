"""Repair tool (T-022, investigate only): what a new template reads differently from the one in use.

The structure diff is the investigate scenario's own `diff_structure` (tools/investigate.py)."""

from __future__ import annotations

from agents import RunContextWrapper

from watchtower.agent.analysis.diff import diff_reads
from watchtower.agent.context import AgentContext
from watchtower.agent.registry import INVESTIGATE, tool
from watchtower.agent.tools.core import _parse_template
from watchtower.engine import run_template

Ctx = RunContextWrapper[AgentContext]
DIFF_SAMPLE = 5


@tool(scenarios=INVESTIGATE)
async def diff_outputs(ctx: Ctx, new_template: str, old_template: str | None = None) -> str:
    """Read a sample with two templates and compare: tenders only one of them finds, and per field how many shared
    tenders get a different value. `old_template` defaults to the template in use. Use it to check that a repair
    restores what the old template read (and that an audit finding is real)."""
    c = ctx.context
    new = _parse_template(new_template)
    if isinstance(new, str):
        return "new_template: " + new
    old = _parse_template(old_template) if old_template else c.extra.get("template")
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
