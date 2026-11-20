import asyncio
from datetime import timedelta

import pytest

from watchtower.core.scheduler import (
    HealthcheckSettings,
    JobContext,
    Scheduler,
    SchedulerSettings,
    running,
)
from watchtower.core.store import Store
from watchtower.core.times import utcnow

URL = "https://tenders.example.de/list"


@pytest.fixture
async def store(tmp_path):
    s = await Store.open(tmp_path / "w.db")
    yield s
    await s.close()


@pytest.fixture
async def pid(store):
    p, _ = await store.add_portal(URL)
    return p.id


def fast(**kw) -> SchedulerSettings:
    return SchedulerSettings(workers=2, poll_interval=0.01, tick_interval=3600, retry_delay=timedelta(0), **kw)


async def wait_for(predicate, limit=3.0):
    deadline = asyncio.get_running_loop().time() + limit
    while not await predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


async def make_serving(store, pid):
    run, _ = await store.request_run(pid, "attach", trigger="attach")
    await store.finish_run(run.id, "ok")
    for job in await store.active_jobs(pid):
        await store.finish_job(job.id)
    await store.set_portal_state(pid, "serving", "Delivering.")


# ---------------------------------------------------------------- workers


async def test_worker_runs_handler_with_its_run(store, pid):
    async def attach(ctx: JobContext):
        assert ctx.trigger == "attach"
        await ctx.store.finish_run(ctx.run_id, "ok", tender_count=3)
        await ctx.states.apply(ctx.job.portal_id, "attach_succeeded", tender_count=3)

    run, _ = await store.request_run(pid, "attach", trigger="attach")
    async with running(Scheduler(store, {"attach": attach}, settings=fast())):
        await wait_for(lambda: _state_is(store, pid, "serving"))
    assert (await store.get_run(run.id)).result == "ok"
    assert await store.active_jobs(pid) == []
    assert (await store.get_portal(pid)).reason == "Attached: delivering 3 tenders."


async def test_jobs_without_handler_stay_queued(store, pid):
    await store.request_run(pid, "attach", trigger="attach")
    async with running(Scheduler(store, {}, settings=fast())):
        await asyncio.sleep(0.1)
    [job] = await store.active_jobs(pid)
    assert job.status == "queued"


async def test_running_job_resumes_after_restart(store, pid):
    run, _ = await store.request_run(pid, "attach", trigger="attach")
    await store.claim_next_job()  # the old process took it, then died

    seen = []

    async def attach(ctx: JobContext):
        seen.append(ctx.run_id)
        await ctx.store.finish_run(ctx.run_id, "ok")
        await ctx.states.apply(ctx.job.portal_id, "attach_succeeded")

    async with running(Scheduler(store, {"attach": attach}, settings=fast())):
        await wait_for(lambda: _state_is(store, pid, "serving"))
    assert seen == [run.id]  # the same run continues


async def test_retries_then_gives_up_on_attach(store, pid):
    calls = 0

    async def attach(ctx: JobContext):
        nonlocal calls
        calls += 1
        raise RuntimeError("boom")

    run, _ = await store.request_run(pid, "attach", trigger="attach")
    async with running(Scheduler(store, {"attach": attach}, settings=fast())):
        await wait_for(lambda: _state_is(store, pid, "failed"))
    assert calls == 2
    r = await store.get_run(run.id)
    assert r.result == "failed" and "boom" in r.details["error"]
    assert "the attach crashed (RuntimeError: boom)" in (await store.get_portal(pid)).reason


async def test_crashed_read_heals(store, pid):
    await make_serving(store, pid)

    async def read(ctx: JobContext):
        raise ValueError("bad page")

    read_run, _ = await store.request_run(pid, "read", trigger="refresh")
    async with running(Scheduler(store, {"read": read}, settings=fast())):
        await wait_for(lambda: _state_is(store, pid, "healing"))
    assert (await store.get_run(read_run.id)).result == "failed"
    [job] = await store.active_jobs(pid)
    assert job.kind == "investigate" and job.payload["trigger"] == "failure"
    assert (await store.get_run(job.run_id)).kind == "repair"


async def test_timeout_counts_as_failure(store, pid):
    await make_serving(store, pid)
    await store.set_portal_state(pid, "healing", "x")

    async def investigate(ctx: JobContext):
        await asyncio.sleep(10)

    await store.request_run(pid, "investigate", trigger="failure")
    settings = fast(timeouts={"investigate": 0.05}, max_attempts={"investigate": 1})
    async with running(Scheduler(store, {"investigate": investigate}, settings=settings)):
        await wait_for(lambda: _state_is(store, pid, "failed"))
    assert "timed out" in (await store.get_portal(pid)).reason


async def test_attach_on_failed_portal_reattaches_first(store, pid):
    await store.set_portal_state(pid, "failed", "Blocked.")
    states = []

    async def attach(ctx: JobContext):
        states.append((await ctx.store.get_portal(ctx.job.portal_id)).state)
        await ctx.store.finish_run(ctx.run_id, "ok")
        await ctx.states.apply(ctx.job.portal_id, "attach_succeeded")

    await store.request_run(pid, "attach", trigger="refresh")
    async with running(Scheduler(store, {"attach": attach}, settings=fast())):
        await wait_for(lambda: _state_is(store, pid, "serving"))
    assert states == ["integrating"]


async def test_deleted_portal_job_is_cancelled_by_tick(store, pid):
    started = asyncio.Event()
    requests = 0

    async def attach(ctx: JobContext):
        nonlocal requests
        started.set()
        while True:  # a handler that never checks ctx.portal_deleted()
            requests += 1
            await asyncio.sleep(0.01)

    run, _ = await store.request_run(pid, "attach", trigger="attach")
    scheduler = Scheduler(store, {"attach": attach}, settings=fast())
    async with running(scheduler):
        await asyncio.wait_for(started.wait(), 2)
        await store.delete_portal(pid)
        await scheduler.tick()
        await wait_for(lambda: _run_finished(store, run.id))
        before = requests
        await asyncio.sleep(0.05)
        assert requests == before  # stopped
    r = await store.get_run(run.id)
    assert r.result == "failed" and r.details["cancelled"] == "portal deleted"


# ---------------------------------------------------------------- ticks: periodic reads and audits


async def test_tick_requests_due_reads_only(store, pid):
    await make_serving(store, pid)
    other, _ = await store.add_portal("https://other.example/tenders")  # integrating: no reads
    now = utcnow()

    s = Scheduler(store, {}, settings=fast(read_interval=timedelta(hours=1)), clock=lambda: now + timedelta(minutes=30))
    await s.tick()
    assert await store.active_jobs(pid) == []

    s = Scheduler(store, {}, settings=fast(read_interval=timedelta(hours=1)), clock=lambda: now + timedelta(hours=2))
    await s.tick()
    [job] = await store.active_jobs(pid)
    assert job.kind == "read" and job.payload["trigger"] == "scheduled"
    assert await store.active_jobs(other.id) == []  # integrating: never read on a schedule

    await s.tick()  # busy now: no second read
    assert len(await store.active_jobs(pid)) == 1


async def test_proactive_audits_are_staggered_and_recorded_as_reads(store):
    ids = []
    for i in range(3):
        p, _ = await store.add_portal(f"https://p{i}.example/list")
        await make_serving(store, p.id)
        ids.append(p.id)
    hc = HealthcheckSettings(mode="proactive", interval=timedelta(hours=24))
    later = utcnow() + timedelta(hours=49)  # past every first-audit offset (24h × [1, 2))
    s = Scheduler(
        store,
        {},
        settings=fast(read_interval=timedelta(days=30), max_audits_per_tick=1),
        healthcheck=hc,
        clock=lambda: later,
    )
    assert sorted(await s.due_audits()) == sorted(ids)

    await s.tick()
    audited = [i for i in ids if await store.active_jobs(i)]
    assert len(audited) == 1  # one per tick
    [job] = await store.active_jobs(audited[0])
    assert job.kind == "investigate" and job.payload["trigger"] == "audit"
    assert (await store.get_run(job.run_id)).kind == "read"
    assert audited[0] not in await s.due_audits()  # busy


async def test_audit_due_again_one_interval_after_the_last(store, pid):
    await make_serving(store, pid)
    hc = HealthcheckSettings(mode="proactive", interval=timedelta(hours=24))
    run, _ = await store.request_run(pid, "investigate", trigger="audit", run_kind="read")
    await store.finish_run(run.id, "ok")
    for job in await store.active_jobs(pid):
        await store.finish_job(job.id)
    now = utcnow()
    assert await Scheduler(store, {}, healthcheck=hc, clock=lambda: now + timedelta(hours=23)).due_audits() == []
    assert await Scheduler(store, {}, healthcheck=hc, clock=lambda: now + timedelta(hours=25)).due_audits() == [pid]


async def test_reactive_mode_never_audits(store, pid):
    await make_serving(store, pid)
    hc = HealthcheckSettings(mode="reactive")
    s = Scheduler(
        store,
        {},
        healthcheck=hc,
        settings=fast(read_interval=timedelta(days=30)),
        clock=lambda: utcnow() + timedelta(days=10),
    )
    assert await s.due_audits() == []
    await s.tick()
    assert await store.active_jobs(pid) == []


# ---------------------------------------------------------------- settings


def test_healthcheck_settings_from_env(monkeypatch):
    monkeypatch.delenv("WATCHTOWER_HEALTHCHECK_MODE", raising=False)
    monkeypatch.delenv("WATCHTOWER_HEALTHCHECK_INTERVAL_HOURS", raising=False)
    assert HealthcheckSettings.from_env() == HealthcheckSettings("proactive", timedelta(hours=24))

    monkeypatch.setenv("WATCHTOWER_HEALTHCHECK_MODE", "Reactive")
    monkeypatch.setenv("WATCHTOWER_HEALTHCHECK_INTERVAL_HOURS", "0.5")
    assert HealthcheckSettings.from_env() == HealthcheckSettings("reactive", timedelta(minutes=30))

    for mode, hours in [("sometimes", "24"), ("proactive", "soon"), ("proactive", "0")]:
        monkeypatch.setenv("WATCHTOWER_HEALTHCHECK_MODE", mode)
        monkeypatch.setenv("WATCHTOWER_HEALTHCHECK_INTERVAL_HOURS", hours)
        with pytest.raises(ValueError):
            HealthcheckSettings.from_env()


# ---------------------------------------------------------------- helpers


async def _state_is(store, pid, state):
    p = await store.get_portal(pid)
    return p is not None and p.state == state


async def _run_finished(store, run_id):
    return (await store.get_run(run_id)).finished_at is not None
