"""Scheduler and workers (ADR 0002 → Scheduling and politeness, Health-check modes; ADR 0005).

- **Workers** (asyncio tasks) take persisted jobs from the store and run the handler registered for the job's kind.
  Jobs left running by a crash are re-queued at start-up and continue with the same run.
- **Ticks** request periodic reads of `serving` portals and, in `proactive` mode, staggered audits. They also cancel
  jobs whose portal was deleted, so a deleted portal sees no more requests within a minute.
- Handlers do the work (the read pipeline, T-017). For attach/read/investigate jobs they must finish
  `ctx.run_id` and report the outcome through `ctx.states`. If a handler raises or times out, the job is retried; after
  the last attempt the scheduler closes the run and applies the failure transition itself, so no portal is left stuck.
"""

import asyncio
import contextlib
import hashlib
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from watchtower.core.models import Job, JobKind
from watchtower.core.state_machine import InvalidTransition, StateMachine
from watchtower.core.store import Store
from watchtower.core.times import utcnow

log = logging.getLogger(__name__)

HealthcheckMode = Literal["reactive", "proactive"]


@dataclass
class HealthcheckSettings:
    mode: HealthcheckMode = "proactive"
    interval: timedelta = timedelta(hours=24)

    @classmethod
    def from_env(cls) -> "HealthcheckSettings":
        mode = os.environ.get("WATCHTOWER_HEALTHCHECK_MODE", "proactive").strip().lower()
        if mode not in ("reactive", "proactive"):
            raise ValueError(f"WATCHTOWER_HEALTHCHECK_MODE must be 'reactive' or 'proactive', not {mode!r}")
        raw_hours = os.environ.get("WATCHTOWER_HEALTHCHECK_INTERVAL_HOURS", "24")
        try:
            hours = float(raw_hours)
        except ValueError as e:
            raise ValueError(f"WATCHTOWER_HEALTHCHECK_INTERVAL_HOURS must be a number, not {raw_hours!r}") from e
        if hours <= 0:
            raise ValueError("WATCHTOWER_HEALTHCHECK_INTERVAL_HOURS must be greater than 0")
        return cls(mode=mode, interval=timedelta(hours=hours))


@dataclass
class JobContext:
    store: Store
    states: StateMachine
    job: Job

    @property
    def run_id(self) -> str | None:
        """The run this job records (attach/read/investigate jobs, ADR 0005)."""
        return self.job.run_id or self.job.payload.get("run_id")

    @property
    def trigger(self) -> str | None:
        return self.job.payload.get("trigger")

    async def portal_deleted(self) -> bool:
        """Check before each request to the portal; stop when True."""
        return self.job.portal_id is not None and await self.store.is_portal_deleted(self.job.portal_id)


Handler = Callable[[JobContext], Awaitable[None]]


@dataclass
class SchedulerSettings:
    workers: int = 4
    poll_interval: float = 1.0  # seconds a worker sleeps when no job is due
    tick_interval: float = 15.0  # seconds between ticks (deleted-portal checks, periodic reads, audits)
    read_interval: timedelta = timedelta(hours=1)  # periodic read of each serving portal
    max_audits_per_tick: int = 1  # staggers audits across portals
    max_attempts: dict[str, int] = field(
        default_factory=lambda: {"attach": 2, "read": 2, "investigate": 2, "download": 3}
    )
    # An attach or investigate job runs the agent (attach: 1,800 s budget; investigate: 900 s) and then the first read
    # with the new template, which may take as long as a read job (3,600 s: a large API list at 1 req/s). A job killed
    # at the agent's own budget loses the agent's outcome, so both get the agent's budget plus a read.
    timeouts: dict[str, float] = field(
        default_factory=lambda: {"attach": 1800 + 3600, "read": 3600, "investigate": 900 + 3600, "download": 3600}
    )
    retry_delay: timedelta = timedelta(seconds=30)  # times the attempt number


class Scheduler:
    def __init__(
        self,
        store: Store,
        handlers: dict[JobKind, Handler],
        *,
        settings: SchedulerSettings | None = None,
        healthcheck: HealthcheckSettings | None = None,
        clock: Callable[[], datetime] = utcnow,
    ):
        self.store = store
        self.states = StateMachine(store)
        self.handlers = dict(handlers)
        self.settings = settings or SchedulerSettings()
        self.healthcheck = healthcheck or HealthcheckSettings()
        self._clock = clock
        self._tasks: list[asyncio.Task] = []
        self._running: dict[int, tuple[Job, asyncio.Task]] = {}

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        requeued = await self.store.requeue_running_jobs()
        if requeued:
            log.info("re-queued %d job(s) left running by the previous process", requeued)
        self._tasks = [asyncio.create_task(self._worker(i), name=f"worker-{i}") for i in range(self.settings.workers)]
        self._tasks.append(asyncio.create_task(self._ticker(), name="scheduler-ticker"))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for _, task in list(self._running.values()):
            task.cancel()
        await asyncio.gather(*self._tasks, *(t for _, t in self._running.values()), return_exceptions=True)
        self._tasks = []

    # ------------------------------------------------------------------ workers

    async def _worker(self, index: int) -> None:
        kinds = list(self.handlers)
        if not kinds:
            return  # nothing registered yet (before T-017): leave jobs queued
        while True:
            try:
                job = await self.store.claim_next_job(kinds, now=self._clock())
            except Exception:
                log.exception("worker %d could not claim a job", index)
                job = None
            if job is None:
                await asyncio.sleep(self.settings.poll_interval)
                continue
            await self.run_job(job)

    async def run_job(self, job: Job) -> None:
        """Run one claimed job to completion (also used directly by tests)."""
        if job.portal_id is not None and await self.store.is_portal_deleted(job.portal_id):
            await self._close_for_deleted(job)
            return
        if job.kind == "attach" and job.portal_id is not None:
            portal = await self.store.get_portal(job.portal_id)
            if portal is not None and portal.state == "failed":
                await self.states.apply(job.portal_id, "reattach")

        ctx = JobContext(self.store, self.states, job)
        task = asyncio.create_task(self.handlers[job.kind](ctx))
        self._running[job.id] = (job, task)
        try:
            await asyncio.wait_for(task, timeout=self.settings.timeouts.get(job.kind))
        except asyncio.CancelledError:
            if job.portal_id is not None and await self.store.is_portal_deleted(job.portal_id):
                await self._close_for_deleted(job)
                return
            raise
        except Exception as e:  # handler crash or timeout
            error = "timed out" if isinstance(e, TimeoutError) else f"{type(e).__name__}: {e}"
            await self._handle_failure(job, error)
        else:
            await self.store.finish_job(job.id)
        finally:
            self._running.pop(job.id, None)

    async def _handle_failure(self, job: Job, error: str) -> None:
        if job.attempts < self.settings.max_attempts.get(job.kind, 1):
            delay = self.settings.retry_delay * job.attempts
            log.warning("job %s (%s) failed, retrying in %s: %s", job.id, job.kind, delay, error)
            await self.store.retry_job(job.id, run_after=self._clock() + delay, error=error)
            return
        log.error("job %s (%s) gave up after %d attempts: %s", job.id, job.kind, job.attempts, error)
        await self.store.finish_job(job.id, ok=False, error=error)
        await self._give_up(job, error)

    async def _give_up(self, job: Job, error: str) -> None:
        """The handler couldn't finish: close its run and apply the failure transition (ADR 0002 lifecycle)."""
        ctx = JobContext(self.store, self.states, job)
        if ctx.run_id is not None:
            run = await self.store.get_run(ctx.run_id)
            if run is not None and run.finished_at is None:
                await self.store.finish_run(run.id, "failed", details={"error": error})
        if job.portal_id is None:
            return
        portal = await self.store.get_portal(job.portal_id)
        if portal is None:
            return
        try:
            if job.kind == "attach" and portal.state == "integrating":
                await self.states.apply(portal.id, "attach_failed", detail=f"the attach crashed ({error})")
            elif job.kind == "read" and portal.state == "serving":
                await self.states.start_healing(portal.id, f"the read crashed ({error})")
            elif job.kind == "investigate" and portal.state == "healing":
                await self.states.apply(portal.id, "repair_failed", detail=f"the repair crashed ({error})")
            elif job.kind == "investigate" and ctx.trigger == "audit" and portal.state == "serving":
                pass  # a crashed audit says nothing about delivery; the next audit is due again later
        except InvalidTransition:
            log.exception("no lifecycle transition after job %s gave up", job.id)

    async def _close_for_deleted(self, job: Job) -> None:
        await self.store.finish_job(job.id, ok=False, error="portal deleted")
        run_id = JobContext(self.store, self.states, job).run_id
        if run_id is not None:
            run = await self.store.get_run(run_id)
            if run is not None and run.finished_at is None:
                await self.store.finish_run(run_id, "failed", details={"cancelled": "portal deleted"})

    # ------------------------------------------------------------------ ticks

    async def _ticker(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("scheduler tick failed")
            await asyncio.sleep(self.settings.tick_interval)

    async def tick(self) -> None:
        """One round: stop work on deleted portals, request due reads, then due audits."""
        await self.cancel_deleted()
        now = self._clock()
        info = await self.store.schedule_info()
        for p in info:
            if p["state"] != "serving" or p["busy"]:
                continue
            last = p["last_read_at"] or p["created_at"]
            if now - last >= self.settings.read_interval:
                await self.store.request_run(p["portal_id"], "read", trigger="scheduled")
        if self.healthcheck.mode == "proactive":
            for portal_id in (await self.due_audits(now))[: self.settings.max_audits_per_tick]:
                await self.store.request_run(portal_id, "investigate", trigger="audit", run_kind="read")

    async def due_audits(self, now: datetime | None = None) -> list[str]:
        """Serving portals whose audit is due, most overdue first (for T-023). Empty in `reactive` mode.

        Each portal's first audit is offset by a stable fraction of the interval, so portals added together don't
        all get audited at the same moment.
        """
        if self.healthcheck.mode != "proactive":
            return []
        now = now or self._clock()
        due: list[tuple[datetime, str]] = []
        for p in await self.store.schedule_info():
            if p["state"] != "serving" or p["busy"]:
                continue
            due_at = self._audit_due_at(p)
            if due_at <= now:
                due.append((due_at, p["portal_id"]))
        return [pid for _, pid in sorted(due)]

    async def audit_schedule(self) -> dict[str, dict]:
        """Per live portal: `last_audit_at` and `next_audit_at` (None in `reactive` mode), for the dashboard (T-023).

        `next_audit_at` is when the portal becomes due; the audit starts at the next tick once it is `serving`, idle,
        and no more overdue portal is ahead of it.
        """
        out = {}
        for p in await self.store.schedule_info():
            nxt = self._audit_due_at(p) if self.healthcheck.mode == "proactive" else None
            out[p["portal_id"]] = {"last_audit_at": p["last_audit_at"], "next_audit_at": nxt}
        return out

    def _audit_due_at(self, p: dict) -> datetime:
        interval = self.healthcheck.interval
        if p["last_audit_at"] is not None:
            return p["last_audit_at"] + interval
        # Each portal's first audit is offset by a stable fraction of the interval (staggering).
        return p["created_at"] + interval * (1 + _stagger(p["portal_id"]))

    async def cancel_deleted(self) -> None:
        for job, task in list(self._running.values()):
            if job.portal_id is not None and await self.store.is_portal_deleted(job.portal_id):
                task.cancel()


def _stagger(portal_id: str) -> float:
    """A stable fraction in [0, 1) per portal."""
    return int(hashlib.sha256(portal_id.encode()).hexdigest()[:8], 16) / 0x1_0000_0000


@contextlib.asynccontextmanager
async def running(scheduler: Scheduler):
    await scheduler.start()
    try:
        yield scheduler
    finally:
        await scheduler.stop()
