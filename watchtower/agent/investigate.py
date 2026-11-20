"""Scenario `investigate` (T-016): Notice → Repair, and the proactive audit. Returns an ADR 0009 `Diagnosis`.

Deterministic first, then the model:

1. **Pre-check** (both triggers, no model): probe the portal address and the template's list address.
   All 404/410 → `Gone`; a login or captcha page → `Blocked`; no answer / 5xx everywhere → `Transient`.
2. **Audit evidence** (`trigger="audit"`, no model): tender links on the list page that the read didn't deliver, the
   site's own app calling the JSON list with other filters, values read from under the wrong label. Nothing
   suspicious → `Healthy` without a model call.
3. **Agent** (`run_scenario("investigate", …)`): gets the failure report or the audit findings plus the structure diff,
   and ends with a repaired template (`submit_template` → `Changed`) or a report. Out of budget → `Unrepairable`
   (failure) or `Transient` (audit: the read itself passed validation, so it keeps being served).

The pipeline validates every `Changed` template before promoting it (ADR 0009).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from agents.models.interface import Model

from watchtower.agent import evidence
from watchtower.agent.config import AgentConfigError, load_settings
from watchtower.agent.context import AgentContext
from watchtower.agent.outcomes import (
    Blocked,
    Changed,
    Diagnosis,
    Gone,
    Healthy,
    Investigate,
    PortalView,
    TemplateFound,
    Transient,
    Trigger,
    Unreadable,
    Unrepairable,
)
from watchtower.agent.runtime import Budget, ScenarioRun, run_scenario
from watchtower.engine.result import ReadResult
from watchtower.engine.template import Template
from watchtower.net import BrowserPool, PoliteClient
from watchtower.validate import Health

log = logging.getLogger(__name__)

FAILURE_BUDGET = Budget(max_turns=25, max_tokens=300_000, wall_seconds=900)
AUDIT_BUDGET = Budget(max_turns=15, max_tokens=150_000, wall_seconds=600)

SCENARIO_PROMPT = """\
INVESTIGATE a portal we already read with a template. Either a regular read failed validation (trigger "failure"),
or a scheduled AUDIT found signs that the read, although it passes validation, misses tenders or reads values from the
wrong place (trigger "audit"). The task below gives the trigger, the problems or findings, and the structure diff of
the list page against the last good read.

Method:
1. Call `show_read` for the current template and what it read. Use the diff, `fetch` and `render` to see what changed
   on the live portal. Start from the current template and change only what the portal changed (selectors, labels,
   JSON keys, pagination, list filters); keep everything that still works.
2. `try_template` the repaired template until the verdict is GOOD, then `count_list` it: the count must be close to
   the usual count (for missed tenders: higher than before). Then `submit_template` with notes saying what changed
   on the portal, in one sentence (it becomes the operators' explanation).
3. Close with exactly one tool:
   - `submit_template`: the portal changed and the repaired template reads it correctly;
   - `report_healthy` (audits only): you checked the findings and the current template is right after all;
   - `report_transient`: a temporary outage or error; the current template is still right;
   - `report_blocked`: tenders or documents now need a login or a captcha;
   - `report_gone`: the portal or its tender list no longer exists;
   - `report_unrepairable`: tenders are there but no template can read them; say why.
A wrong value is worse than a missing one: never submit a template that reads a field from the wrong place.
"""


@dataclass
class InvestigateRun:
    """What one investigation did (for logs and, later, T-099 metering)."""

    diagnosis: Diagnosis
    model_run: ScenarioRun | None  # None: decided without a model call
    evidence: str


async def investigate(
    portal: PortalView,
    template: Template,
    result: ReadResult,
    health: Health,
    *,
    trigger: Trigger,
    client: PoliteClient,
    browser: BrowserPool | None = None,
    budget: Budget | None = None,
    model: Model | None = None,
) -> InvestigateRun:
    pc = await evidence.precheck(client, portal.url, template)
    if pc.verdict == "gone":
        return InvestigateRun(Gone(reason=pc.reason), None, pc.describe())
    if pc.verdict == "blocked":
        return InvestigateRun(Blocked(reason=pc.reason), None, pc.describe())
    if pc.verdict == "transient":
        return InvestigateRun(Transient(reason=pc.reason), None, pc.describe())

    if trigger == "audit":
        findings = await evidence.audit(client, browser, portal.url, template, result)
        if not findings.suspicious:
            notes = "; ".join(findings.checked) or "nothing suspicious found"
            return InvestigateRun(Healthy(notes=notes), None, findings.describe())
        report = findings.describe()
    else:
        report = "Problems with the failed read:\n" + "\n".join(f"- {p}" for p in health.problems or result.errors)

    if model is None:
        try:
            load_settings()
        except AgentConfigError as e:
            why = f"no model is configured ({e})"
            no_model: Diagnosis = (
                Transient(reason=f"the audit was inconclusive: {why}")
                if trigger == "audit"
                else Unrepairable(reason=f"the portal changed, but {why}")
            )
            return InvestigateRun(no_model, None, report)

    live = await evidence.live_snapshot(template, client, browser)
    diff = evidence.diff_snapshots(portal.last_good_snapshot, live)
    task = (
        f"Trigger: {trigger}\nPortal: {portal.url}\nTemplate version: {portal.template_version}\n"
        f"Usual tender count (recent good reads): {', '.join(map(str, portal.recent_counts)) or 'unknown'}\n"
        f"Read under investigation: {len(result.tenders)} tenders\n\n{report}\n\n"
        f"Address checks:\n{pc.describe()}\n\nList page structure, last good read → live now:\n{diff}\n"
    )
    ctx = AgentContext(portal.url, client, browser)
    ctx.extra.update(trigger=trigger, portal=portal, template=template, result=result, health=health)
    run = await run_scenario(
        "investigate",
        SCENARIO_PROMPT,
        task,
        ctx,
        budget=budget or (AUDIT_BUDGET if trigger == "audit" else FAILURE_BUDGET),
        model=model,
    )
    return InvestigateRun(to_diagnosis(run, trigger), run, task)


def to_diagnosis(run: ScenarioRun, trigger: Trigger) -> Diagnosis:
    o = run.outcome
    if isinstance(o, TemplateFound):
        return Changed(new_template=o.template, reason=o.notes or "the portal changed and the template was rewritten")
    if isinstance(o, Unreadable):
        return Unrepairable(reason=o.reason)
    if isinstance(o, Healthy):
        if trigger == "audit":
            return o
        return Unrepairable(reason="no cause for the failed read was found")
    if isinstance(o, Transient | Blocked | Gone | Unrepairable):
        return o
    limit = {"turns": "turn", "tokens": "token", "time": "time"}.get(run.stop_reason)
    why = f"the repair agent ran out of its {limit} budget" if limit else "the repair agent stopped without a result"
    if trigger == "audit":
        # The read passed validation; an inconclusive audit keeps it served (Transient → audit_healthy, ADR 0009).
        return Transient(reason=f"the audit was inconclusive: {why}")
    return Unrepairable(reason=why)


def make_investigate(
    client: PoliteClient,
    browser: BrowserPool | None = None,
    *,
    budget: Budget | None = None,
    model: Model | None = None,
) -> Investigate:
    """The `Investigate` callable the pipeline is configured with (`core.pipeline.Scenarios`)."""

    async def _investigate(
        portal: PortalView, template: Template, result: ReadResult, health: Health, *, trigger: Trigger
    ) -> Diagnosis:
        run = await investigate(
            portal, template, result, health, trigger=trigger, client=client, browser=browser, budget=budget,
            model=model,
        )  # fmt: skip
        m = run.model_run
        log.info(
            "investigate %s (%s): %s%s",
            portal.id,
            trigger,
            run.diagnosis.kind,
            f" after {m.requests} model calls, {m.input_tokens + m.output_tokens} tokens" if m else " (no model call)",
        )
        return run.diagnosis

    return _investigate
