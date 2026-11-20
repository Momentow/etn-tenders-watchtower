"""Health-check modes (T-023), end to end: scheduler ticks → pipeline → investigate (scripted model) on mock portals.

`reactive` repairs failed reads only; `proactive` also audits serving portals and catches silent breakage that the
validators can't see.
"""

from datetime import timedelta

import httpx
import pytest
from scripted import ScriptedModel, call

from mockportals.app import ENTRY, create_app
from watchtower.agent.investigate import make_investigate
from watchtower.agent.outcomes import TemplateFound
from watchtower.core import pipeline
from watchtower.core.handlers import HANDLERS
from watchtower.core.pipeline import Scenarios
from watchtower.core.scheduler import HealthcheckSettings, Scheduler, SchedulerSettings
from watchtower.core.store import Store
from watchtower.core.times import utcnow
from watchtower.engine.examples import load_example
from watchtower.engine.template import Template
from watchtower.net import HostRateLimiter, PoliteClient

SSR = load_example("ssr_list_detail")
PORTAL = "http://localhost:8081" + ENTRY["ssr"]


def edit(t: Template, change) -> Template:
    data = t.model_dump(mode="json", exclude_none=True)
    change(data)
    return Template.model_validate(data)


def _buyer_from_detail(d):
    d["fields"]["buyerName"] = {
        "by": "xpath",
        "expr": "//dt[normalize-space()='Vergabestelle']/following-sibling::dd[1]",
        "on": "detail",
    }


def _markup(d):
    d["list"]["items"]["expr"] = "table.ergebnisse tbody tr"
    d["detail"]["link"]["expr"] = "td.bezeichnung a"
    f = d["fields"]
    f["id"]["expr"] = f["referenceNumber"]["expr"] = "td.aktenzeichen"
    f["title"]["expr"] = "td.bezeichnung a"
    f["buyerName"]["expr"] = "td.vergabestelle"
    f["publicationDate"]["expr"] = "td.veroeffentlicht"
    f["submissionDeadline"]["expr"] = "//dt[normalize-space()='Ablauf der Angebotsfrist']/following-sibling::dd[1]"
    d["status"]["value"]["expr"] = "td.zustand"


def submit(template: Template, notes: str) -> list:
    t = template.model_dump_json(exclude_defaults=True)
    return [[call("try_template", template=t, sample=2)], [call("submit_template", template=t, notes=notes)]]


class World:
    """One portal on the mock site, the real pipeline handlers and a scheduler with a controllable clock."""

    def __init__(self, store, mock, client, mode, model):
        self.store, self.mock, self.client, self.model = store, mock, client, model
        self.now = utcnow()
        self.hc = HealthcheckSettings(mode=mode, interval=timedelta(hours=24))
        self.scheduler = Scheduler(
            store, HANDLERS, settings=SchedulerSettings(retry_delay=timedelta(0)), healthcheck=self.hc,
            clock=lambda: self.now,
        )  # fmt: skip
        self.pid = ""

    async def drain(self) -> None:
        """Run every queued attach/read/investigate job until none is left, like the workers would. Downloads stay
        queued: they aren't what these tests check, and they'd need the document store."""
        kinds = ["attach", "read", "investigate"]
        while job := await self.store.claim_next_job(kinds, now=self.now + timedelta(days=365)):
            await self.scheduler.run_job(job)

    async def attach(self) -> None:
        p, _ = await self.store.add_portal(PORTAL)
        self.pid = p.id
        await self.store.request_run(p.id, "attach", trigger="attach")
        await self.drain()
        assert (await self.portal()).state == "serving"

    async def later(self, hours: float) -> None:
        self.now += timedelta(hours=hours)
        # Two ticks (15 s apart in production): a due read and a due audit never run at once for one portal, so
        # the audit follows the read on the next tick.
        for _ in range(2):
            await self.scheduler.tick()
            await self.drain()

    async def portal(self):
        return await self.store.get_portal(self.pid)

    async def runs(self):
        return list(reversed(await self.store.list_runs(self.pid)))  # oldest first


@pytest.fixture
async def world(tmp_path, monkeypatch):
    store = await Store.open(tmp_path / "w.db")
    mock = create_app()
    client = PoliteClient(limiter=HostRateLimiter(0), transport=httpx.ASGITransport(app=mock), max_attempts=1)
    saved = pipeline._config
    worlds = []

    async def attach_ssr(url):
        return TemplateFound(template=SSR, notes="learned")

    def make(mode: str, turns: list) -> World:
        model = ScriptedModel(turns)
        w = World(store, mock, client, mode, model)
        # The store stamps runs and audits with the same clock the scheduler compares them with.
        monkeypatch.setattr("watchtower.core.store.utcnow", lambda: w.now)
        pipeline._config = pipeline._Config(
            client=client, scenarios=Scenarios(attach_ssr, make_investigate(client, model=model))
        )
        worlds.append(w)
        return w

    yield make
    pipeline._config = saved
    await client.aclose()
    await store.close()


def kinds(runs) -> list[str]:
    return [("audit" if r.details.get("trigger") == "audit" else r.kind) + ":" + (r.result or "running") for r in runs]


# ---------------------------------------------------------------- the silent problem: only an audit sees it


async def test_reactive_mode_misses_a_silent_problem(world):
    w = world("reactive", [])
    await w.attach()
    w.mock.state.variants["ssr"] = "silent-swap"  # the buyer column now shows the city; everything still parses
    await w.later(30)  # past the audit interval: a periodic read, but no audit in reactive mode
    runs = await w.runs()
    assert kinds(runs) == ["read:ok", "read:ok"]
    assert (await w.portal()).state == "serving"  # wrong buyer names are served and nobody notices
    assert w.model.instructions is None  # no model call
    assert not await w.scheduler.due_audits()


async def test_proactive_audit_catches_a_silent_problem_and_repairs_it(world):
    fixed = edit(SSR, _buyer_from_detail)
    w = world("proactive", submit(fixed, "the buyer column now shows the city; the buyer is read from the detail page"))
    await w.attach()
    tenders_before = {t.tender_id: t.data["buyerName"] for t in await w.store.list_tenders(w.pid)}
    w.mock.state.variants["ssr"] = "silent-swap"

    await w.later(49)  # the first audit is due within 24–48 h (staggered)
    runs = await w.runs()
    assert kinds(runs)[-3:] == ["read:ok", "audit:failed", "repair:ok"]
    audit_run, repair_run = runs[-2], runs[-1]
    assert audit_run.kind == "read" and audit_run.details["diagnosis"] == "changed"
    assert "buyer" in audit_run.details["problems"][0]
    assert repair_run.details["trigger"] == "audit_finding" and repair_run.details["promoted"] == 2

    portal = await w.portal()
    assert portal.state == "serving" and portal.current_template_version == 2
    assert portal.reason.startswith("Repaired: the buyer column now shows the city")
    after = {t.tender_id: t.data["buyerName"] for t in await w.store.list_tenders(w.pid)}
    assert after == tenders_before  # the correct buyer names again, not the cities

    # The next audit, one interval later, finds nothing and needs no model call.
    calls = len(w.model.turns)
    await w.later(25)
    last = (await w.runs())[-1]
    assert kinds([last]) == ["audit:ok"] and last.details["diagnosis"] == "healthy"
    assert len(w.model.turns) == calls


async def test_proactive_audit_of_a_healthy_portal_costs_no_model_call(world):
    w = world("proactive", [])
    await w.attach()
    await w.later(49)
    last = (await w.runs())[-1]
    assert kinds([last]) == ["audit:ok"]
    assert last.details["diagnosis"] == "healthy" and "tender links" in last.details["diagnosis_reason"]
    assert (await w.portal()).reason.startswith("The scheduled audit found no problems.")
    assert w.model.instructions is None


async def test_audit_finding_that_cannot_be_repaired_fails_truthfully(world):
    w = world("proactive", [[call("report_unrepairable", reason="buyer names are only in a scanned PDF")]])
    await w.attach()
    w.mock.state.variants["ssr"] = "silent-swap"
    await w.later(49)
    runs = await w.runs()
    assert kinds(runs)[-2:] == ["audit:failed", "repair:failed"]
    portal = await w.portal()
    # Not "repaired: the failure was temporary": the read still passes, but the silent problem is still there.
    assert portal.state == "failed"
    assert portal.reason == "Cannot deliver: buyer names are only in a scanned PDF. A person is needed."


# ---------------------------------------------------------------- reactive behaviour (both modes)


@pytest.mark.parametrize("mode", ["reactive", "proactive"])
async def test_failed_read_is_investigated_and_repaired(world, mode):
    fixed = edit(SSR, _markup)
    w = world(mode, submit(fixed, "the result table and its labels were renamed"))
    await w.attach()
    w.mock.state.variants["ssr"] = "markup"
    await w.later(2)  # a periodic read (hourly) fails validation → healing → repair
    runs = await w.runs()
    assert kinds(runs) == ["read:ok", "read:failed", "repair:ok"]
    assert runs[1].details["problems"] and runs[2].details["diagnosis"] == "changed"
    portal = await w.portal()
    assert portal.state == "serving" and portal.current_template_version == 2


@pytest.mark.parametrize("variant, reason", [("gone", "no longer exists"), ("login-wall", "requires a login")])
async def test_failed_read_ends_failed_with_an_exact_reason(world, variant, reason):
    w = world("reactive", [])
    await w.attach()
    w.mock.state.variants["ssr"] = variant
    await w.later(2)
    portal = await w.portal()
    assert portal.state == "failed" and reason in portal.reason
    assert w.model.instructions is None  # decided by the deterministic pre-check
