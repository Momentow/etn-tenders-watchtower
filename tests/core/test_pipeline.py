"""Read pipeline (T-017): attach, read, repair and audit flows on the mock portals, with fake agent scenarios."""

from datetime import timedelta

import httpx
import pytest

from mockportals.app import create_app
from watchtower.agent.outcomes import Blocked, Changed, Healthy, TemplateFound, Transient, Unrepairable
from watchtower.core import pipeline
from watchtower.core.models import TenderUpsert
from watchtower.core.pipeline import Scenarios, handle_attach, handle_investigate, handle_read
from watchtower.core.scheduler import JobContext
from watchtower.core.state_machine import StateMachine
from watchtower.core.store import Store
from watchtower.core.times import utcnow
from watchtower.engine.examples import load_example
from watchtower.engine.template import Template
from watchtower.net import HostRateLimiter, PoliteClient

URL = "http://localhost:8081/ssr/ausschreibungen"
SSR = load_example("ssr_list_detail")


def _broken():
    data = SSR.model_dump(mode="json", exclude_none=True)
    data["list"]["items"]["expr"] = "table.nope tr"  # reads no tenders
    return Template.model_validate(data)


BROKEN = _broken()


class Agent:
    """Fake scenarios: return what the test sets, and record the calls."""

    def __init__(self):
        self.attach_outcome = TemplateFound(template=SSR, notes="learned")
        self.diagnosis = Transient(reason="the portal was briefly down")
        self.calls: list[str] = []

    async def generate_template(self, portal_url):
        self.calls.append("generate_template")
        return self.attach_outcome

    async def investigate(self, portal, template, result, health, *, trigger):
        self.calls.append(f"investigate:{trigger}")
        return self.diagnosis


@pytest.fixture
async def store(tmp_path):
    s = await Store.open(tmp_path / "w.db")
    yield s
    await s.close()


@pytest.fixture
def mock():
    return create_app()


@pytest.fixture
async def agent(mock):
    a = Agent()
    saved = pipeline._config
    client = PoliteClient(limiter=HostRateLimiter(0), transport=httpx.ASGITransport(app=mock))
    pipeline._config = pipeline._Config(client=client, scenarios=Scenarios(a.generate_template, a.investigate))
    yield a
    pipeline._config = saved
    await client.aclose()


@pytest.fixture
async def pid(store):
    p, _ = await store.add_portal(URL)
    return p.id


async def run(store, pid, kind, trigger, handler, *, run_kind=None):
    """Request the work the way the API/scheduler does, then run the job's handler like a worker."""
    run_, created = await store.request_run(pid, kind, trigger=trigger, run_kind=run_kind)
    assert created
    job = next(j for j in await store.active_jobs(pid) if j.run_id == run_.id)
    await handler(JobContext(store, StateMachine(store), job))
    await store.finish_job(job.id)
    return await store.get_run(run_.id)


async def attach(store, pid):
    return await run(store, pid, "attach", "attach", handle_attach)


async def read(store, pid):
    return await run(store, pid, "read", "refresh", handle_read)


async def repair(store, pid, trigger="failure"):
    job = next(j for j in await store.active_jobs(pid, ["investigate"]))
    await handle_investigate(JobContext(store, StateMachine(store), job))
    await store.finish_job(job.id)
    return await store.get_run(job.run_id)


async def audit(store, pid):
    return await run(store, pid, "investigate", "audit", handle_investigate, run_kind="read")


async def downloads(store, pid):
    return [j for j in await store.active_jobs(pid, ["download"])]


# ---------------------------------------------------------------- attach


async def test_attach_promotes_template_stores_tenders_and_queues_downloads(store, pid, agent):
    r = await attach(store, pid)
    portal = await store.get_portal(pid)
    assert portal.state == "serving"
    assert r.kind == "read" and r.result == "ok" and r.tender_count == 20 and r.snapshot is not None
    assert r.details["template_version"] == 1 and r.details["tenders"]["new"] == 20
    assert portal.current_template_version == 1
    tenders = await store.list_tenders(pid)
    assert len(tenders) == 20
    assert all(t.data["submissionDeadline"] for t in tenders)
    assert await downloads(store, pid)
    open_count = sum(t.status == "open" for t in tenders)
    assert portal.reason == f"Attached: delivering {open_count} tenders."


async def test_attach_blocked_fails_with_the_reason(store, pid, agent):
    agent.attach_outcome = Blocked(reason="the tender list is behind a login")
    r = await attach(store, pid)
    portal = await store.get_portal(pid)
    assert (portal.state, r.result) == ("failed", "failed")
    assert portal.reason == "Could not attach: the tender list is behind a login."
    assert await store.list_tenders(pid) == []


async def test_attach_with_a_template_that_fails_validation_is_not_promoted(store, pid, agent):
    agent.attach_outcome = TemplateFound(template=BROKEN)
    r = await attach(store, pid)
    portal = await store.get_portal(pid)
    assert portal.state == "failed" and "does not pass validation" in portal.reason
    assert r.details["problems"]
    assert portal.current_template_version is None
    assert [v.promoted_at for v in await store.list_template_versions(pid)] == [None]


# ---------------------------------------------------------------- read


async def test_unchanged_read_keeps_last_changed_at_and_queues_nothing(store, pid, agent):
    await attach(store, pid)
    before = {t.tender_id: t.last_changed_at for t in await store.list_tenders(pid)}
    for j in await downloads(store, pid):
        await store.finish_job(j.id)
    r = await read(store, pid)
    assert r.result == "ok" and r.details["tenders"]["unchanged"] == 20
    assert {t.tender_id: t.last_changed_at for t in await store.list_tenders(pid)} == before
    assert await downloads(store, pid) == []
    assert (await store.get_portal(pid)).state == "serving"


async def test_unlisted_and_expired_tenders_are_closed(store, pid, agent):
    await attach(store, pid)
    yesterday = (utcnow() - timedelta(days=1)).isoformat()
    await store.upsert_tenders(
        pid,
        [
            TenderUpsert(tender_id="GONE-1", data={"title": "no longer listed"}, content_hash="x"),
        ],
    )
    r = await read(store, pid)
    assert r.details["tenders"]["closed"] == 1
    assert (await store.get_tender(pid, "GONE-1")).status == "closed"
    assert pipeline._deadline_passed(yesterday, utcnow())
    assert not pipeline._deadline_passed(None, utcnow())


async def test_failed_read_heals_and_keeps_serving_last_tenders(store, pid, agent, mock):
    await attach(store, pid)
    served = await store.list_tenders(pid)
    mock.state.variants["ssr"] = "markup"
    r = await read(store, pid)
    portal = await store.get_portal(pid)
    assert r.result == "failed" and r.details["problems"]
    assert portal.state == "healing" and portal.reason.startswith("The last read failed:")
    assert await store.list_tenders(pid) == served
    assert await store.active_jobs(pid, ["investigate"])


# ---------------------------------------------------------------- repair


async def test_temporary_failure_is_repaired_by_rereading_without_the_agent(store, pid, agent, mock):
    await attach(store, pid)
    mock.state.variants["ssr"] = "markup"
    await read(store, pid)
    mock.state.variants["ssr"] = "base"
    r = await repair(store, pid)
    assert r.kind == "repair" and r.result == "ok"
    assert (await store.get_portal(pid)).state == "serving"
    assert agent.calls == ["generate_template"]


async def test_changed_template_is_validated_and_promoted(store, pid, agent):
    agent.attach_outcome = TemplateFound(template=SSR)
    await attach(store, pid)
    # The portal "changed": the current template no longer reads it.
    v2 = await store.add_template_version(pid, pipeline._dump(BROKEN))
    await store.promote_template_version(pid, v2.version)
    await read(store, pid)
    agent.diagnosis = Changed(new_template=SSR, reason="the tender table was renamed")
    r = await repair(store, pid)
    portal = await store.get_portal(pid)
    assert r.result == "ok" and r.details["promoted"] == 3
    assert portal.state == "serving" and portal.current_template_version == 3
    assert portal.reason.startswith("Repaired: the tender table was renamed.")


async def test_changed_template_that_still_fails_is_not_promoted(store, pid, agent, mock):
    await attach(store, pid)
    mock.state.variants["ssr"] = "markup"
    await read(store, pid)
    agent.diagnosis = Changed(new_template=BROKEN, reason="guessing")
    r = await repair(store, pid)
    portal = await store.get_portal(pid)
    assert r.result == "failed" and portal.state == "failed"
    assert portal.current_template_version == 1
    assert "still fails validation" in portal.reason


async def test_unrepairable_fails_the_portal_with_the_reason(store, pid, agent, mock):
    await attach(store, pid)
    mock.state.variants["ssr"] = "markup"
    await read(store, pid)
    agent.diagnosis = Unrepairable(reason="the portal now needs a login")
    await repair(store, pid)
    portal = await store.get_portal(pid)
    assert (
        portal.state == "failed"
        and portal.reason == "Cannot deliver: the portal now needs a login. A person is needed."
    )
    assert len(await store.list_tenders(pid)) == 20  # still served


# ---------------------------------------------------------------- audit


async def test_healthy_audit_is_a_good_read(store, pid, agent):
    await attach(store, pid)
    agent.diagnosis = Healthy()
    r = await audit(store, pid)
    portal = await store.get_portal(pid)
    assert r.kind == "read" and r.result == "ok" and r.details["audit"] is True
    assert portal.state == "serving" and portal.reason.startswith("The scheduled audit found no problems.")


async def test_audit_finding_queues_a_repair_that_tries_the_proposed_template_first(store, pid, agent):
    await attach(store, pid)
    agent.diagnosis = Changed(new_template=SSR, reason="three tenders on page 2 were missed")
    r = await audit(store, pid)
    portal = await store.get_portal(pid)
    assert r.result == "failed" and portal.state == "healing"
    assert portal.reason.startswith("The scheduled audit found a problem: three tenders on page 2 were missed.")
    calls = len(agent.calls)
    rr = await repair(store, pid)
    portal = await store.get_portal(pid)
    assert rr.result == "ok" and rr.details["promoted"] == 2
    assert portal.state == "serving" and portal.current_template_version == 2
    assert len(agent.calls) == calls  # no second agent call


# ---------------------------------------------------------------- housekeeping


async def test_deleted_portal_closes_the_run_without_reading(store, pid, agent):
    run_, _ = await store.request_run(pid, "attach", trigger="attach")
    job = (await store.active_jobs(pid))[0]
    await store.delete_portal(pid)
    await handle_attach(JobContext(store, StateMachine(store), job))
    r = await store.get_run(run_.id)
    assert r.result == "failed" and r.details["cancelled"] == "portal deleted"
    assert agent.calls == []


def test_first_problem_mentions_the_rest():
    from watchtower.validate import Health

    assert pipeline.first_problem(Health(ok=False, problems=["A.", "B.", "C."])) == "A (and 2 more problems)"
    assert pipeline.first_problem(Health(ok=False, problems=["A."])) == "A"
