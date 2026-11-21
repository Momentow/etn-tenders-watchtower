"""Read-only view models for the dashboard. Everything comes from `core.store`; nothing here writes state."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from watchtower.core.models import Portal, PortalState, Run, StoredTender, TemplateVersion
from watchtower.core.store import Store
from watchtower.core.times import utcnow

STATES: tuple[PortalState, ...] = ("serving", "healing", "integrating", "failed")

STATE_LABELS: dict[str, str] = {
    "serving": "Serving",
    "healing": "Healing",
    "integrating": "Integrating",
    "failed": "Failed",
}

# Run triggers (ADR 0005 and the scheduler) in words.
TRIGGER_LABELS: dict[str, str] = {
    "attach": "Attach",
    "refresh": "Refresh requested",
    "scheduled": "Scheduled read",
    "audit": "Health audit",
    "failure": "Investigate failure",
}

# Detail keys shown in their own way; anything else is listed as key/value.
_KNOWN_DETAIL_KEYS = {
    "trigger", "problems", "error", "cancelled", "diagnosis", "diagnosis_reason", "audit", "audit_finding", "stats",
    "template_version", "tenders",
}  # fmt: skip
_RECENT_RUNS = 20


@dataclass
class RunView:
    id: str
    kind: str
    label: str
    trigger: str | None
    started_at: datetime
    finished_at: datetime | None
    result: str | None
    tender_count: int | None
    template_version: int | None
    problems: list[str] = field(default_factory=list)
    error: str | None = None
    diagnosis: str | None = None
    extra: list[tuple[str, str]] = field(default_factory=list)

    @property
    def running(self) -> bool:
        return self.finished_at is None

    @property
    def status(self) -> str:
        """`running` | `ok` | `failed`, for styling."""
        return "running" if self.running else (self.result or "failed")

    @property
    def duration_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()


@dataclass
class ModelCost:
    """What the agent's model calls cost for one portal (T-099, ADR 0017), summed from `store.model_usage_summary`."""

    rows: list[dict[str, Any]]  # per scenario, tier and model
    requests: int
    tokens: int
    cost_usd: float | None  # None: a model without a price was used

    @classmethod
    def of(cls, rows: list[dict[str, Any]]) -> "ModelCost | None":
        if not rows:
            return None
        costs = [r["cost_usd"] for r in rows]
        return cls(
            rows=rows,
            requests=sum(r["requests"] for r in rows),
            tokens=sum(r["input_tokens"] + r["output_tokens"] for r in rows),
            cost_usd=None if any(c is None for c in costs) else sum(costs),
        )

    def scenario_cost(self, scenario: str) -> float | None:
        costs = [r["cost_usd"] for r in self.rows if r["scenario"] == scenario]
        return None if not costs or any(c is None for c in costs) else sum(costs)


@dataclass
class PortalRow:
    portal: Portal
    last_run: RunView | None
    running: RunView | None
    last_repair: RunView | None
    activity: str  # what the system did / is doing, in words
    last_audit: RunView | None = None
    next_audit_at: datetime | None = None  # None: reactive mode, or not known
    model_cost: ModelCost | None = None  # None: no model call for this portal yet


@dataclass
class HealthChecks:
    """The health-check mode (ADR 0002 → Health-check modes), for the page header."""

    mode: str  # "reactive" | "proactive"
    interval_hours: float

    @property
    def summary(self) -> str:
        if self.mode == "proactive":
            hours = f"{self.interval_hours:g} h"
            return f"Proactive health checks: failed reads are repaired, and every portal is audited every {hours}."
        return "Reactive health checks: failed reads are repaired; no scheduled audits."


@dataclass
class Overview:
    rows: list[PortalRow]
    counts: dict[str, int]
    total_tenders: int
    generated_at: datetime
    health: HealthChecks | None = None
    total_cost_usd: float | None = None  # model cost over all portals; None: none yet, or a model without a price


@dataclass
class PortalDetail:
    row: PortalRow
    runs: list[RunView]
    templates: list[TemplateVersion]
    tenders: list[StoredTender]
    open_tenders: int
    document_count: int
    health: HealthChecks | None = None


def run_view(run: Run) -> RunView:
    details = run.details or {}
    trigger = details.get("trigger")
    if run.kind == "repair":
        label = "Repair after audit" if trigger == "audit_finding" else "Repair"
    elif trigger in TRIGGER_LABELS:
        label = TRIGGER_LABELS[trigger]
    else:
        label = "Read"
    problems = details.get("problems") or []
    if isinstance(problems, str):
        problems = [problems]
    error = details.get("error")
    if error is None and details.get("cancelled"):
        error = f"Cancelled: {details['cancelled']}"
    extra = [(k, _short(v)) for k, v in details.items() if k not in _KNOWN_DETAIL_KEYS and v not in (None, "", [], {})]
    if isinstance(changes := details.get("tenders"), dict):
        parts = [f"{changes[k]} {k}" for k in ("new", "changed", "closed") if changes.get(k)]
        extra.insert(0, ("tenders", ", ".join(parts) or "no changes"))
    version = run.template_version or details.get("template_version")
    return RunView(
        id=run.id,
        kind=run.kind,
        label=label,
        trigger=trigger,
        started_at=run.started_at,
        finished_at=run.finished_at,
        result=run.result,
        tender_count=run.tender_count,
        template_version=version if isinstance(version, int) else None,
        problems=[str(p) for p in problems],
        error=None if error is None else str(error),
        diagnosis=_diagnosis(details.get("diagnosis"), details.get("diagnosis_reason")),
        extra=extra,
    )


def _diagnosis(value: Any, reason: Any = None) -> str | None:
    if value in (None, "", {}):
        return None
    if isinstance(value, str) and reason:
        return f"{value.capitalize()}: {reason}"
    if isinstance(value, dict):
        kind = value.get("kind") or value.get("type")
        why = value.get("reason") or value.get("notes")
        if kind and why:
            return f"{kind}: {why}"
        return _short(value)
    return str(value)


def _short(value: Any, limit: int = 240) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def activity(portal: Portal, last_run: RunView | None, running: RunView | None, last_repair: RunView | None) -> str:
    """One line on what the system did or is doing for this portal."""
    if running is not None:
        if running.kind == "repair":
            return "Repairing now"
        if portal.state == "integrating":
            return "Attaching now"
        return f"{running.label} running"
    if portal.state == "healing":
        return "Repair queued"
    if last_repair is not None and last_run is not None and last_repair.id == last_run.id:
        return "Repaired" if last_repair.result == "ok" else "Repair failed"
    if last_run is None:
        return "Waiting for first read"
    if last_run.result == "ok":
        return f"{last_run.label} ok"
    return f"{last_run.label} failed"


async def _portal_row(
    store: Store,
    portal: Portal,
    runs: list[Run] | None = None,
    audits: dict[str, dict] | None = None,
    usage: list[dict[str, Any]] | None = None,
) -> tuple[PortalRow, list[RunView]]:
    views = [run_view(r) for r in (runs if runs is not None else await store.list_runs(portal.id, limit=_RECENT_RUNS))]
    running = next((v for v in views if v.running), None)
    last_run = next((v for v in views if not v.running), None)
    last_repair = next((v for v in views if v.kind == "repair" and not v.running), None)
    row = PortalRow(
        portal=portal,
        last_run=last_run,
        running=running,
        last_repair=last_repair,
        activity=activity(portal, last_run, running, last_repair),
        last_audit=next((v for v in views if v.trigger == "audit" and not v.running), None),
        next_audit_at=(audits or {}).get(portal.id, {}).get("next_audit_at"),
        model_cost=ModelCost.of(usage or []),
    )
    return row, views


async def _health(scheduler: Any) -> tuple[HealthChecks | None, dict[str, dict]]:
    if scheduler is None:
        return None, {}
    hc = scheduler.healthcheck
    health = HealthChecks(mode=hc.mode, interval_hours=hc.interval.total_seconds() / 3600)
    return health, await scheduler.audit_schedule()


async def overview(store: Store, scheduler: Any = None) -> Overview:
    health, audits = await _health(scheduler)
    portals = await store.list_portals()
    usage: dict[str, list[dict[str, Any]]] = {}
    for u in await store.model_usage_summary():
        usage.setdefault(u["portal_id"], []).append(u)
    rows = [(await _portal_row(store, p, audits=audits, usage=usage.get(p.id)))[0] for p in portals]
    # Problems first: failed, healing, integrating, then serving; newest first inside a state.
    order = {"failed": 0, "healing": 1, "integrating": 2, "serving": 3}
    rows.sort(key=lambda r: (order.get(r.portal.state, 9), -r.portal.created_at.timestamp()))
    counts = {s: 0 for s in STATES}
    for r in rows:
        counts[r.portal.state] = counts.get(r.portal.state, 0) + 1
    return Overview(
        rows=rows,
        counts=counts,
        total_tenders=sum(r.portal.tender_count for r in rows),
        generated_at=utcnow(),
        health=health,
        total_cost_usd=_total_cost([r.model_cost for r in rows if r.model_cost]),
    )


def _total_cost(costs: list[ModelCost]) -> float | None:
    if not costs or any(c.cost_usd is None for c in costs):
        return None
    return sum(c.cost_usd for c in costs if c.cost_usd is not None)


async def portal_detail(
    store: Store, portal_id: str, *, with_tenders: bool = True, scheduler: Any = None
) -> PortalDetail | None:
    portal = await store.get_portal(portal_id)
    if portal is None:
        return None
    health, audits = await _health(scheduler)
    runs_ = await store.list_runs(portal.id, limit=200)
    row, runs = await _portal_row(store, portal, runs_, audits, await store.model_usage_summary(portal.id))
    tenders = await store.list_tenders(portal.id) if with_tenders else []
    tenders.sort(key=lambda t: t.last_changed_at, reverse=True)
    return PortalDetail(
        row=row,
        runs=runs,
        templates=await store.list_template_versions(portal.id),
        tenders=tenders,
        open_tenders=sum(1 for t in tenders if t.status == "open"),
        document_count=sum(len(t.documents) for t in tenders),
        health=health,
    )


# ---------------------------------------------------------------- formatting (Jinja filters)


def ago(value: datetime | None, now: datetime | None = None) -> str:
    if value is None:
        return "never"
    seconds = int(((now or utcnow()) - value).total_seconds())
    if seconds < 0:
        return "in " + _span(-seconds)
    if seconds < 10:
        return "just now"
    return _span(seconds) + " ago"


def due(value: datetime | None, now: datetime | None = None) -> str:
    """When a scheduled audit is due: "in 5 h", or "due now" once the time has passed."""
    if value is None:
        return "—"
    seconds = int((value - (now or utcnow())).total_seconds())
    return "due now" if seconds <= 0 else "in " + _span(seconds)


def _span(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 86400:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} d"


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 1:
        return "<1 s"
    if seconds < 60:
        return f"{seconds:.0f} s"
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes} min {secs} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min"


def iso(value: datetime | None) -> str:
    return "" if value is None else value.isoformat(timespec="seconds")


def safe_href(value: Any) -> str | None:
    """Only http(s) links from portal data become clickable."""
    if isinstance(value, str) and value.lower().startswith(("http://", "https://")):
        return value
    return None


def usd(value: float | None) -> str:
    """A model cost: `$0.0123`, `<$0.0001`, or `price unknown`."""
    if value is None:
        return "price unknown"
    if 0 < value < 0.0001:
        return "<$0.0001"
    return f"${value:.4f}" if value < 1 else f"${value:.2f}"


def tokens(value: int | None) -> str:
    if not value:
        return "0"
    return f"{value / 1000:.1f}k" if value < 1_000_000 else f"{value / 1_000_000:.2f}M"


def filesize(size: int | None) -> str:
    if size is None:
        return "—"
    step = 1024.0
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < step or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= step
    return f"{size} B"
