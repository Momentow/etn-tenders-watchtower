"""Tools of the `investigate` scenario (T-016): look at the failing read, compare with the live portal, close the run.

The scenario puts its inputs in `ctx.extra` (see `watchtower.agent.investigate`): `trigger`, `portal` (PortalView),
`template`, `result` (the read under investigation) and `health`. A repaired template is submitted with the core
`submit_template` tool; the scenario turns it into `Changed`.
"""

from __future__ import annotations

import json
from statistics import median

from agents import RunContextWrapper

from watchtower.agent import evidence
from watchtower.agent.context import AgentContext
from watchtower.agent.outcomes import Gone, Healthy, Unrepairable
from watchtower.agent.registry import INVESTIGATE, tool
from watchtower.agent.tools.core import _canonical, _list_only, _list_only_v2, _parse_any_template
from watchtower.engine import run_template
from watchtower.engine.template_v2 import AnyTemplate, TemplateV2

Ctx = RunContextWrapper[AgentContext]
COUNT_MAX_PAGES = 30
DROP_RATIO = 0.6  # same rule as validate's "sharp drop"


def expected_count(c: AgentContext) -> int | None:
    portal = c.extra.get("portal")
    counts = getattr(portal, "recent_counts", None) or []
    return round(median(counts)) if counts else None


@tool(scenarios=INVESTIGATE)
async def show_read(ctx: Ctx) -> str:
    """Show the read under investigation: the current template (JSON), what the engine read with it (count, errors,
    first ids, two sample tenders), the validator's problems and the usual tender count from recent good reads."""
    c = ctx.context
    template: AnyTemplate = c.extra["template"]
    result = c.extra["result"]
    health = c.extra["health"]
    lines = [
        "CURRENT TEMPLATE:",
        _canonical(template),
        f"READ: {len(result.tenders)} tenders on {result.stats.list_pages} list page(s)"
        + (f"; usual count {expected_count(c)}" if expected_count(c) is not None else ""),
    ]
    if result.errors:
        lines.append("ENGINE ERRORS:\n- " + "\n- ".join(result.errors[:5]))
    if health.problems:
        lines.append("VALIDATION PROBLEMS:\n- " + "\n- ".join(health.problems[:5]))
    if result.tenders:
        lines.append("first ids: " + ", ".join(t.id for t in result.tenders[:8]))
        for t in result.tenders[:2]:
            data = t.model_dump(exclude_none=True, exclude={"complete", "documents"})
            if isinstance(data.get("description"), str):
                data["description"] = data["description"][:120]
            lines.append("  " + json.dumps(data, ensure_ascii=False)[:800] + f"  documents: {len(t.documents)}")
    return "\n".join(lines)


@tool(scenarios=INVESTIGATE)
async def diff_structure(ctx: Ctx, template: str | None = None) -> str:
    """Compare the structure of the live first list page (as `template`, default the current one, reaches it) with the
    snapshot stored from the last good read. Shows a unified diff of the element outline (HTML) or key shape (JSON):
    renamed classes, moved tables, renamed JSON keys."""
    c = ctx.context
    t = c.extra["template"]
    if template:
        parsed = _parse_any_template(template)
        if isinstance(parsed, str):
            return parsed
        t = parsed
    live = await evidence.live_snapshot(t, c.client, c.browser)
    portal = c.extra.get("portal")
    return evidence.diff_snapshots(getattr(portal, "last_good_snapshot", None), live)


@tool(scenarios=INVESTIGATE)
async def count_list(ctx: Ctx, template: str) -> str:
    """Read only the list pages of `template` (all pages, up to 30; no detail pages) and compare the tender count with
    the usual count from recent good reads. Run it before submitting a repair: a template that reads far fewer tenders
    than usual will be rejected by validation (missed pages or a filtered list)."""
    c = ctx.context
    parsed = _parse_any_template(template)
    if isinstance(parsed, str):
        return parsed
    list_only = _list_only_v2(parsed, COUNT_MAX_PAGES) if isinstance(parsed, TemplateV2) else _list_only(parsed)
    if list_only is None:
        return "Could not reduce the template to its list; check the fields that are read from the list row."
    pg = list_only.list.pagination
    if not isinstance(list_only, TemplateV2) and pg.kind != "none":
        list_only = list_only.model_copy(update={"list": list_only.list.model_copy(update={"pagination": pg.model_copy(
            update={"max_pages": COUNT_MAX_PAGES})})})  # fmt: skip
    r = await run_template(list_only, c.client, c.browser)
    n = len(r.tenders)
    usual = expected_count(c)
    previous = c.extra.get("result")
    lines = [f"LIST COUNT: {n} tenders on {r.stats.list_pages} list page(s)"]
    if r.stats.duplicate_ids:
        lines.append(f"  {r.stats.duplicate_ids} duplicate ids: pagination may repeat pages")
    if r.stats.hit_max_pages:
        lines.append(f"  stopped at {COUNT_MAX_PAGES} pages; more may follow")
    if r.errors:
        lines.append("  errors: " + "; ".join(r.errors[:3]))
    if usual is not None:
        verdict = "OK" if n >= DROP_RATIO * usual or usual - n < 3 else "TOO FEW (validation would reject this)"
        lines.append(f"  usual count from recent good reads: {usual} → {verdict}")
    if previous is not None and previous.tenders:
        before = {t.id for t in previous.tenders}
        now = {t.id for t in r.tenders}
        if lost := sorted(before - now):
            lines.append(f"  ids in the investigated read but not here: {', '.join(lost[:8])}")
        if new := sorted(now - before):
            lines.append(f"  ids here but not in the investigated read: {', '.join(new[:8])}")
    c.extra.setdefault("counted", {})[_canonical(parsed)] = n
    return "\n".join(lines)


# --------------------------------------------------------------------------- ending the run


@tool(scenarios=INVESTIGATE)
async def report_healthy(ctx: Ctx, notes: str) -> str:
    """End an AUDIT: the engine output matches the live portal (no missed tenders, values from the right places), so
    the current template stays. Not allowed when investigating a failed read. `notes`: one sentence on what you
    checked."""
    c = ctx.context
    if c.extra.get("trigger") != "audit":
        return (
            "Not accepted: this is a failed read, not an audit. Repair it (submit_template), or report "
            "report_transient / report_blocked / report_gone / report_unrepairable."
        )
    c.outcome = Healthy(notes=notes)
    return "Reported."


@tool(scenarios=INVESTIGATE)
async def report_gone(ctx: Ctx, reason: str) -> str:
    """End the run: the portal or its tender list no longer exists (shut down, 404/410 everywhere, the domain now
    shows something else). `reason`: one plain sentence for operators."""
    ctx.context.outcome = Gone(reason=reason)
    return "Reported."


@tool(scenarios=INVESTIGATE)
async def report_unrepairable(ctx: Ctx, reason: str) -> str:
    """End the run: the portal still has tenders but no template can read them correctly (a structure the template
    format can't express, or every attempt fails). `reason`: one plain sentence naming what blocks the repair."""
    ctx.context.outcome = Unrepairable(reason=reason)
    return "Reported."
