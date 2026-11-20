"""Read pipeline: the attach, read and investigate job handlers (ADR 0002 → Read pipeline, lifecycle; ADR 0006, 0009).

Every flow ends the same way: finish the job's run, then report one event to the state machine.

- **read** (scheduled / refresh): engine run → `validate.check` → store the tenders → `read_succeeded`, or
  `start_healing` with the first problem. Failed reads store nothing, so the last correct tenders keep being served.
- **attach**: `generate_template` → the candidate is stored as a new (unpromoted) version → engine run + check →
  promoted and stored → `attach_succeeded`; otherwise `attach_failed`.
- **investigate**, repair (portal `healing`): an unpromoted audit candidate first, then a plain re-read (a temporary
  failure needs no model call), then `investigate(trigger="failure")`. Only a template that passes validation is
  promoted → `repair_succeeded`; anything else → `repair_failed`.
- **investigate**, audit (portal `serving`, recorded as a `read` run): re-read + check, then
  `investigate(trigger="audit")` → `audit_healthy`, or `start_healing(found_by_audit=True)`. A template the audit
  proposes is stored unpromoted and tried first by the repair.

No model call here: the agent scenarios are called through `Scenarios` (stubs until T-015/T-016 merge).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from watchtower.agent import stubs
from watchtower.agent.outcomes import (
    Blocked,
    Changed,
    GenerateTemplate,
    Gone,
    Healthy,
    Investigate,
    PortalView,
    TemplateFound,
    Transient,
    Unreadable,
    Unrepairable,
)
from watchtower.core.handlers import register
from watchtower.core.models import Portal, TemplateVersion, TenderUpsert
from watchtower.core.scheduler import JobContext
from watchtower.core.state_machine import StateMachine
from watchtower.core.store import Store
from watchtower.core.times import utcnow
from watchtower.documents.handler import enqueue_downloads
from watchtower.engine import ReadResult, TenderRecord, run_template
from watchtower.engine.normalize import content_hash
from watchtower.engine.template import Template
from watchtower.net import BrowserPool, PoliteClient
from watchtower.validate import Health, PortalHistory, check

log = logging.getLogger(__name__)


@dataclass
class Scenarios:
    generate_template: GenerateTemplate = stubs.generate_template
    investigate: Investigate = stubs.investigate


@dataclass
class _Config:
    client: PoliteClient | None = None
    browser: BrowserPool | None = None
    scenarios: Scenarios = field(default_factory=Scenarios)


_config = _Config()


def configure(
    *, client: PoliteClient | None = None, browser: BrowserPool | None = None, scenarios: Scenarios | None = None
) -> None:
    """Set the process's shared network objects and the agent scenarios (called once from `app.py`)."""
    if client is not None:
        _config.client = client
    if browser is not None:
        _config.browser = browser
    if scenarios is not None:
        _config.scenarios = scenarios


def _client() -> PoliteClient:
    if _config.client is None:
        _config.client = PoliteClient()
    return _config.client


@dataclass
class Checked:
    """One engine run and its verdict."""

    template: Template
    version: int
    result: ReadResult
    health: Health

    @property
    def ok(self) -> bool:
        return self.health.ok

    @property
    def problem(self) -> str:
        return first_problem(self.health)


class Pipeline:
    def __init__(
        self,
        store: Store,
        states: StateMachine,
        client: PoliteClient,
        browser: BrowserPool | None,
        scenarios: Scenarios,
        *,
        clock: Callable[[], datetime] = utcnow,
    ):
        self.store = store
        self.states = states
        self.client = client
        self.browser = browser
        self.scenarios = scenarios
        self._clock = clock

    # ------------------------------------------------------------------ flows

    async def attach(self, portal: Portal, run_id: str) -> None:
        outcome = await self.scenarios.generate_template(portal.url)
        if isinstance(outcome, Blocked | Unreadable):
            await self.store.finish_run(
                run_id, "failed", details={"outcome": outcome.kind, "problems": [outcome.reason]}
            )
            await self.states.apply(portal.id, "attach_failed", detail=outcome.reason)
            return
        assert isinstance(outcome, TemplateFound)
        tv = await self.store.add_template_version(portal.id, _dump(outcome.template), notes=outcome.notes)
        # A fresh attach is judged on its own: history from an earlier life of the portal does not apply.
        checked = await self._read(portal.id, outcome.template, tv.version, history=PortalHistory())
        if not checked.ok:
            await self._finish_failed(run_id, checked, outcome="template_found")
            await self.states.apply(
                portal.id, "attach_failed", detail=f"the learned template does not pass validation ({checked.problem})"
            )
            return
        await self.store.promote_template_version(portal.id, tv.version)
        count = await self._deliver(portal.id, run_id, checked, details={"outcome": "template_found"})
        await self.states.apply(portal.id, "attach_succeeded", tender_count=count)

    async def read(self, portal: Portal, run_id: str) -> None:
        tv = await self._current(portal.id)
        checked = await self._read(portal.id, _load(tv), tv.version)
        if not checked.ok:
            await self._finish_failed(run_id, checked)
            await self.states.start_healing(portal.id, checked.problem)
            return
        count = await self._deliver(portal.id, run_id, checked)
        await self.states.apply(portal.id, "read_succeeded", tender_count=count)

    async def repair(self, portal: Portal, run_id: str, trigger: str | None) -> None:
        tv = await self._current(portal.id)
        template = _load(tv)

        # 1. A template proposed by the audit that found the problem (stored unpromoted, newer than the current one).
        finding: dict[str, Any] = {}
        if trigger == "audit_finding":
            finding = await self._audit_finding(portal.id)
            if candidate := await self._newest_candidate(portal.id, tv.version):
                checked = await self._read(portal.id, _load(candidate), candidate.version)
                if checked.ok:
                    detail = candidate.notes or finding.get("diagnosis_reason") or "the portal changed"
                    await self._promote_and_deliver(portal.id, run_id, checked, detail, details=finding)
                    return

        # 2. Deterministic first: a temporary failure is fixed by reading again, without a model call.
        checked = await self._read(portal.id, template, tv.version)
        if checked.ok:
            if trigger == "audit_finding":
                # The audit's read passed validation too: a re-read proves nothing about a silent problem (ADR 0012).
                # Without a working proposed template, the audit's diagnosis stands.
                reason = finding.get("diagnosis_reason") or "the audit's finding could not be repaired"
                count = len(checked.result.tenders)
                details = {**finding, "problems": [reason]}
                await self.store.finish_run(run_id, "failed", tender_count=count, details=details)
                await self.states.apply(portal.id, "repair_failed", detail=reason)
                return
            await self._repaired(
                portal.id, run_id, checked, "a fresh read passed validation, the failure was temporary"
            )
            return

        # 3. Ask the agent.
        view = await self._view(portal, tv.version)
        diagnosis = await self.scenarios.investigate(view, template, checked.result, checked.health, trigger="failure")
        details = _diagnosis_details(diagnosis)
        if isinstance(diagnosis, Transient):
            again = await self._read(portal.id, template, tv.version)
            if again.ok:
                await self._repaired(portal.id, run_id, again, diagnosis.reason, details=details)
                return
            await self._finish_failed(run_id, again, **details)
            await self.states.apply(
                portal.id, "repair_failed", detail=f"reads keep failing after a temporary error ({again.problem})"
            )
        elif isinstance(diagnosis, Changed):
            new = await self.store.add_template_version(
                portal.id, _dump(diagnosis.new_template), notes=diagnosis.reason
            )
            checked = await self._read(portal.id, diagnosis.new_template, new.version)
            if checked.ok:
                await self._promote_and_deliver(portal.id, run_id, checked, diagnosis.reason, details=details)
                return
            await self._finish_failed(run_id, checked, **details)
            await self.states.apply(
                portal.id, "repair_failed", detail=f"the repaired template still fails validation ({checked.problem})"
            )
        elif isinstance(diagnosis, Blocked | Gone | Unrepairable):
            await self._finish_failed(run_id, checked, **details, reason=diagnosis.reason)
            await self.states.apply(portal.id, "repair_failed", detail=diagnosis.reason)
        else:  # Healthy is not an answer to a failed read
            await self._finish_failed(run_id, checked, **details)
            await self.states.apply(
                portal.id, "repair_failed", detail=f"no cause was found, but reads still fail ({checked.problem})"
            )

    async def audit(self, portal: Portal, run_id: str) -> None:
        tv = await self._current(portal.id)
        template = _load(tv)
        checked = await self._read(portal.id, template, tv.version)
        details: dict[str, Any] = {"audit": True}
        if not checked.ok:  # the validators already see it: an ordinary failed read
            await self._finish_failed(run_id, checked, **details)
            await self.states.start_healing(portal.id, checked.problem)
            return
        view = await self._view(portal, tv.version)
        diagnosis = await self.scenarios.investigate(view, template, checked.result, checked.health, trigger="audit")
        details |= _diagnosis_details(diagnosis)
        if isinstance(diagnosis, Healthy | Transient):
            count = await self._deliver(portal.id, run_id, checked, details=details)
            await self.states.apply(portal.id, "audit_healthy", tender_count=count)
            return
        if isinstance(diagnosis, Changed):
            # Tried first by the repair this queues (step 1 there); promoted only if it passes validation.
            await self.store.add_template_version(portal.id, _dump(diagnosis.new_template), notes=diagnosis.reason)
        reason = getattr(diagnosis, "reason", "the audit reported a problem")
        await self.store.finish_run(
            run_id, "failed", tender_count=len(checked.result.tenders), details={**details, "problems": [reason]}
        )
        await self.states.start_healing(portal.id, reason, found_by_audit=True)

    # ------------------------------------------------------------------ steps

    async def _read(
        self, portal_id: str, template: Template, version: int, *, history: PortalHistory | None = None
    ) -> Checked:
        result = await run_template(template, self.client, self.browser)
        if history is None:
            history = PortalHistory.from_store(
                await self.store.list_runs(portal_id), await self.store.list_tenders(portal_id)
            )
        return Checked(template, version, result, check(result, history, today=self._clock().date()))

    async def _deliver(
        self, portal_id: str, run_id: str, checked: Checked, *, details: dict[str, Any] | None = None
    ) -> int:
        """Store a validated read, queue its downloads and finish the run `ok`. Returns the open tenders delivered."""
        now = self._clock()
        result = checked.result
        tenders = _unique(result.tenders)
        upserts: list[TenderUpsert] = []
        refs: dict[str, TenderRecord] = {}
        kept_incomplete = 0
        for t in tenders:
            if not t.complete and await self.store.get_tender(portal_id, t.id) is not None:
                kept_incomplete += 1  # a detail/document hop failed: keep what we served, don't overwrite it
                continue
            status = "closed" if t.status == "open" and _deadline_passed(t.submissionDeadline, now) else t.status
            upserts.append(
                TenderUpsert(tender_id=t.id, data=t.contract_data(), content_hash=content_hash(t), status=status)
            )
            refs[t.id] = t
        summary = await self.store.upsert_tenders(portal_id, upserts, now=now)
        closed: list[str] = []
        if not result.stats.hit_max_pages:  # pages may be left unread: missing ≠ no longer listed
            closed = await self.store.close_unlisted_tenders(portal_id, [t.id for t in tenders], now=now)
        for tid in summary.new + summary.changed:
            t = refs[tid]
            if t.documents or tid in summary.changed:
                await enqueue_downloads(self.store, portal_id, tid, t.documents)
        open_count = sum(1 for u in upserts if u.status == "open") + kept_incomplete
        await self.store.finish_run(
            run_id,
            "ok",
            tender_count=len(tenders),
            snapshot=result.snapshot,
            details={
                **(details or {}),
                "template_version": checked.version,
                "stats": result.stats.model_dump(),
                "tenders": {
                    "new": len(summary.new),
                    "changed": len(summary.changed),
                    "unchanged": len(summary.unchanged),
                    "closed": len(closed),
                    "kept_incomplete": kept_incomplete,
                },
            },
        )
        return open_count

    async def _finish_failed(self, run_id: str, checked: Checked, **details: Any) -> None:
        await self.store.finish_run(
            run_id,
            "failed",
            tender_count=len(checked.result.tenders),
            details={
                **details,
                "template_version": checked.version,
                "problems": checked.health.problems,
                "stats": checked.result.stats.model_dump(),
            },
        )

    async def _repaired(
        self, portal_id: str, run_id: str, checked: Checked, detail: str, *, details: dict[str, Any] | None = None
    ) -> None:
        count = await self._deliver(portal_id, run_id, checked, details=details)
        await self.states.apply(portal_id, "repair_succeeded", detail=detail, tender_count=count)

    async def _promote_and_deliver(
        self, portal_id: str, run_id: str, checked: Checked, detail: str, *, details: dict[str, Any] | None = None
    ) -> None:
        await self.store.promote_template_version(portal_id, checked.version)
        await self._repaired(
            portal_id, run_id, checked, detail, details={**(details or {}), "promoted": checked.version}
        )

    async def _current(self, portal_id: str) -> TemplateVersion:
        tv = await self.store.get_current_template(portal_id)
        if tv is None:
            raise RuntimeError(f"portal {portal_id} has no promoted template")
        return tv

    async def _audit_finding(self, portal_id: str) -> dict[str, Any]:
        """Diagnosis details of the newest audit that found a problem (the one that started this repair)."""
        for r in await self.store.list_runs(portal_id, limit=20):  # newest first
            if r.details.get("audit") and r.result == "failed":
                reason = r.details.get("diagnosis_reason") or next(iter(r.details.get("problems") or []), None)
                return {"audit_finding": r.id, "diagnosis": r.details.get("diagnosis"), "diagnosis_reason": reason}
        return {}

    async def _newest_candidate(self, portal_id: str, current: int) -> TemplateVersion | None:
        for tv in await self.store.list_template_versions(portal_id):  # newest first
            if tv.version > current and tv.promoted_at is None:
                return tv
        return None

    async def _view(self, portal: Portal, version: int) -> PortalView:
        history = PortalHistory.from_store(await self.store.list_runs(portal.id), [])
        return PortalView(
            id=portal.id,
            url=portal.url,
            state=portal.state,
            template_version=version,
            last_good_snapshot=await self.store.last_good_snapshot(portal.id),
            recent_counts=history.recent_counts,
        )


# ---------------------------------------------------------------------- job handlers


def _pipeline(ctx: JobContext) -> Pipeline:
    return Pipeline(ctx.store, ctx.states, _client(), _config.browser, _config.scenarios)


async def _start(ctx: JobContext, expected: tuple[str, ...]) -> tuple[Portal, str] | None:
    """The portal and run for this job, or None when the job has nothing to do (its run is closed here)."""
    run_id = ctx.run_id
    if run_id is None:
        raise RuntimeError(f"{ctx.job.kind} job {ctx.job.id} has no run")
    portal = await ctx.store.get_portal(ctx.job.portal_id) if ctx.job.portal_id else None
    if portal is None or await ctx.portal_deleted():
        await ctx.store.finish_run(run_id, "failed", details={"cancelled": "portal deleted"})
        return None
    if portal.state not in expected:
        log.info("skipping %s job %s: portal %s is %s", ctx.job.kind, ctx.job.id, portal.id, portal.state)
        await ctx.store.finish_run(run_id, "failed", details={"skipped": f"portal is {portal.state}"})
        return None
    return portal, run_id


@register("attach")
async def handle_attach(ctx: JobContext) -> None:
    if started := await _start(ctx, ("integrating",)):
        await _pipeline(ctx).attach(*started)


@register("read")
async def handle_read(ctx: JobContext) -> None:
    if started := await _start(ctx, ("serving",)):
        await _pipeline(ctx).read(*started)


@register("investigate")
async def handle_investigate(ctx: JobContext) -> None:
    if ctx.trigger == "audit":
        if started := await _start(ctx, ("serving",)):
            await _pipeline(ctx).audit(*started)
    elif started := await _start(ctx, ("healing",)):
        await _pipeline(ctx).repair(*started, ctx.trigger)


# ---------------------------------------------------------------------- helpers


def first_problem(health: Health) -> str:
    if not health.problems:
        return "no details"
    more = len(health.problems) - 1
    return health.problems[0].rstrip(".") + (f" (and {more} more problem{'s' if more > 1 else ''})" if more else "")


def _diagnosis_details(diagnosis: Any) -> dict[str, Any]:
    """Run details for a diagnosis: its kind and, for the dashboard, its reason or notes (T-023)."""
    out: dict[str, Any] = {"diagnosis": diagnosis.kind}
    why = getattr(diagnosis, "reason", None) or getattr(diagnosis, "notes", None)
    if why:
        out["diagnosis_reason"] = why
    return out


def _unique(tenders: list[TenderRecord]) -> list[TenderRecord]:
    seen: set[str] = set()
    out = []
    for t in tenders:
        if t.id not in seen:
            seen.add(t.id)
            out.append(t)
    return out


def _deadline_passed(deadline: str | None, now: datetime) -> bool:
    if not deadline:
        return False
    try:
        dt = datetime.fromisoformat(deadline)
    except ValueError:
        return False
    return dt.tzinfo is not None and dt < now


def _dump(template: Template) -> dict[str, Any]:
    return template.model_dump(mode="json", exclude_none=True)


def _load(tv: TemplateVersion) -> Template:
    return Template.model_validate(tv.template)
