import pytest

from watchtower.core.models import TenderUpsert
from watchtower.core.state_machine import TRANSITIONS, InvalidTransition, StateMachine
from watchtower.core.store import Store

URL = "https://tenders.example.de/list"
STATES = ("integrating", "serving", "healing", "failed")
EVENTS = sorted({e for _, e in TRANSITIONS})


@pytest.fixture
async def store(tmp_path):
    s = await Store.open(tmp_path / "w.db")
    yield s
    await s.close()


@pytest.fixture
async def pid(store):
    p, _ = await store.add_portal(URL)
    return p.id


def test_table_matches_adr_0002_lifecycle():
    assert TRANSITIONS == {
        ("integrating", "attach_succeeded"): "serving",
        ("integrating", "attach_failed"): "failed",
        ("serving", "read_succeeded"): "serving",
        ("serving", "read_failed"): "healing",
        ("serving", "audit_healthy"): "serving",
        ("serving", "audit_found_problem"): "healing",
        ("healing", "repair_succeeded"): "serving",
        ("healing", "repair_failed"): "failed",
        ("failed", "reattach"): "integrating",
    }


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("event", EVENTS)
async def test_every_state_event_pair(store, pid, state, event):
    await store.set_portal_state(pid, state, "setup")
    sm = StateMachine(store)
    target = TRANSITIONS.get((state, event))
    if target is None:
        with pytest.raises(InvalidTransition):
            await sm.apply(pid, event, detail="x")
        assert (await store.get_portal(pid)).state == state
    else:
        portal = await sm.apply(pid, event, detail="x")
        assert portal.state == target
        assert portal.reason and portal.reason != "setup"


async def test_reasons_are_plain_sentences(store, pid):
    sm = StateMachine(store)
    await store.upsert_tenders(pid, [TenderUpsert(tender_id="A", data={}, content_hash="h")])
    p = await sm.apply(pid, "attach_succeeded")
    assert p.reason == "Attached: delivering 1 tender."
    p = await sm.apply(pid, "read_succeeded", tender_count=12)
    assert p.reason == "Delivering 12 tenders."
    p = await sm.apply(pid, "read_failed", detail="  zero tenders found on the list page. ")
    assert p.reason.startswith("The last read failed: zero tenders found on the list page. Repairing")
    p = await sm.apply(pid, "repair_failed", detail="the portal now requires a login")
    assert p.reason == "Cannot deliver: the portal now requires a login. A person is needed."


async def test_unknown_or_deleted_portal(store, pid):
    sm = StateMachine(store)
    with pytest.raises(InvalidTransition):
        await sm.apply("nope", "attach_succeeded")
    await store.delete_portal(pid)
    with pytest.raises(InvalidTransition):
        await sm.apply(pid, "attach_succeeded")


async def test_start_healing_queues_investigate(store, pid):
    sm = StateMachine(store)
    await sm.apply(pid, "attach_succeeded")
    read, _ = await store.request_run(pid, "read", trigger="scheduled")
    await store.finish_run(read.id, "failed")
    for job in await store.active_jobs(pid):
        await store.finish_job(job.id, ok=False)

    repair_id = await sm.start_healing(pid, "the tender count dropped from 40 to 0")
    assert (await store.get_portal(pid)).state == "healing"
    [job] = await store.active_jobs(pid)
    assert job.kind == "investigate" and job.payload == {"run_id": repair_id, "trigger": "failure"}
    assert (await store.get_run(repair_id)).kind == "repair"


async def test_audit_finding_uses_its_own_trigger(store, pid):
    sm = StateMachine(store)
    await sm.apply(pid, "attach_succeeded")
    repair_id = await sm.start_healing(pid, "tenders were missed", found_by_audit=True)
    assert (await store.get_run(repair_id)).details == {"trigger": "audit_finding"}
    assert "scheduled audit found a problem" in (await store.get_portal(pid)).reason
