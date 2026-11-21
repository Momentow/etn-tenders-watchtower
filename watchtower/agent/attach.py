"""Scenario `generate_template` (T-015, ADR 0002/0009/0010): learn how to read a new portal.

The model explores the portal with the attach tools and ends the run with `submit_template` (accepted only after a GOOD
dry run), `report_blocked`, `report_unreadable` or `report_transient`. This wrapper maps every way a run can end onto an
`AttachOutcome`:

| Run ended with | Outcome |
|---|---|
| an accepted template | `TemplateFound` |
| `report_blocked` after a template passed the dry run, documents included | `TemplateFound` with it (try first) |
| `report_blocked` | `Blocked` |
| `report_unreadable` / `report_transient` | `Unreadable` with the reason |
| budget exhausted or no closing call | `Unreadable` naming what ran out |
| no model key configured | `Unreadable` saying so |

`make_generate_template(client, browser)` returns the function the pipeline calls (`GenerateTemplate` protocol).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from agents import Model

from watchtower.agent.config import AgentConfigError
from watchtower.agent.context import AgentContext
from watchtower.agent.outcomes import (
    AttachOutcome,
    Blocked,
    GenerateTemplate,
    TemplateFound,
    Transient,
    Unreadable,
)
from watchtower.agent.runtime import Budget, ScenarioRun, TieredBudget, run_scenario
from watchtower.engine.template import Template
from watchtower.net import BrowserPool, PoliteClient

log = logging.getLogger(__name__)

# ADR 0015: exploring at 1 req/s is slow, and light checks run inside the strong model's wall time.
ATTACH_BUDGET = TieredBudget(
    strong=Budget(max_turns=40, max_tokens=600_000, wall_seconds=1800),
    light=Budget(max_turns=60, max_tokens=400_000, wall_seconds=120),
)

ATTACH_PROMPT = """\
Attach a new portal. You get only its URL. Produce a template that reads EVERY open tender on the portal (all list
pages) with its contract fields and ALL its documents.

- The URL may be a home page, a search page, a single notice, or a summary/notice page that only links on to the real
  tender page. Find the portal's list of current (open) tenders first; if list entries lead to a summary page, add a
  `detail.resolve` follow step to reach the real tender page with the documents.
- Prefer a list of open/current tenders. If the portal only lists everything, keep the list and add a `status` rule so
  closed and withdrawn tenders are marked.
- Captcha or login boxes: try first, then decide. Only call `report_blocked` after a dry run (or a direct `fetch`)
  shows that the list or the documents really need a login or a solved captcha. A captcha widget on a page whose list
  and document links work without it is NOT a block.
- `report_unreadable` only when there is no public tender list at all (or nothing you can express as a template), and
  `report_transient` when the portal is down or answering with server errors.

End with exactly one call: `submit_template` (verdict GOOD), `report_blocked`, `report_unreadable` or
`report_transient`.
"""


@dataclass
class AttachRun:
    outcome: AttachOutcome
    run: ScenarioRun | None  # None when no model run happened (e.g. no key)


async def run_attach(
    portal_url: str,
    client: PoliteClient,
    browser: BrowserPool | None,
    *,
    budget: Budget | TieredBudget = ATTACH_BUDGET,
    model: Model | None = None,
    light_model: Model | None = None,
) -> AttachRun:
    ctx = AgentContext(portal_url, client, browser)
    try:
        task = f"Portal URL: {portal_url}"
        run = await run_scenario(
            "attach", ATTACH_PROMPT, task, ctx, budget=budget, model=model, light_model=light_model
        )
    except AgentConfigError as e:
        return AttachRun(Unreadable(reason=f"the agent is not configured ({e})"), None)
    log.info(
        "attach %s: %s after %d model calls (%d light), %d+%d tokens (%d cached), %.0fs",
        portal_url,
        run.stop_reason,
        run.requests,
        run.by_tier["light"].requests,
        run.input_tokens,
        run.output_tokens,
        run.cached_tokens,
        run.seconds,
    )
    return AttachRun(_outcome(run, ctx), run)


def make_generate_template(
    client: PoliteClient,
    browser: BrowserPool | None,
    *,
    budget: Budget | TieredBudget = ATTACH_BUDGET,
    model: Model | None = None,
) -> GenerateTemplate:
    """The `generate_template(portal_url)` the pipeline calls, bound to the process's network objects."""

    async def generate_template(portal_url: str) -> AttachOutcome:
        return (await run_attach(portal_url, client, browser, budget=budget, model=model)).outcome

    return generate_template


def _outcome(run: ScenarioRun, ctx: AgentContext) -> AttachOutcome:
    outcome = run.outcome
    if isinstance(outcome, TemplateFound):
        return outcome
    if isinstance(outcome, Blocked):
        if (good := _good_template(ctx)) is not None:
            log.info("attach %s: reported blocked, but a template passed the dry run; using it", ctx.portal_url)
            return TemplateFound(
                template=good,
                notes=f"The model reported a block ({outcome.reason}), but this template read the list and "
                "downloaded documents without it.",
            )
        return outcome
    if isinstance(outcome, Unreadable):
        return outcome
    if isinstance(outcome, Transient):
        return Unreadable(reason=f"the portal could not be read right now ({outcome.reason.rstrip('.')})")
    if outcome is not None:  # an investigate-only outcome: not an answer to attach
        return Unreadable(reason=f"the attach ended with an unexpected {outcome.kind} report")
    return Unreadable(reason=_budget_reason(run.stop_reason))


def _good_template(ctx: AgentContext) -> Template | None:
    """The last template whose dry run was GOOD (ctx.tried keeps insertion order)."""
    good = [key for key, ok in ctx.tried.items() if ok]
    return Template.model_validate_json(good[-1]) if good else None


def _budget_reason(stop: str) -> str:
    what = {
        "turns": "the turn limit",
        "tokens": "the token budget",
        "time": "the time limit",
    }.get(stop)
    if what:
        return f"no working template was found within {what} of the attach"
    return "the agent stopped without finding a working template"
