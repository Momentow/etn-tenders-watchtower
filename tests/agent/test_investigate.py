"""Scenario `investigate` (T-016) on the mock portals: deterministic diagnoses, scripted repairs, audits, pipeline."""

import httpx
import pytest
from scripted import ScriptedModel, call, say

from mockportals.app import ENTRY, create_app
from watchtower.agent import evidence
from watchtower.agent.investigate import investigate, make_investigate
from watchtower.agent.outcomes import Blocked, Changed, Gone, Healthy, PortalView, Transient, Unrepairable
from watchtower.agent.runtime import Budget
from watchtower.core import pipeline
from watchtower.core.pipeline import Scenarios, handle_attach, handle_investigate, handle_read
from watchtower.core.scheduler import JobContext
from watchtower.core.state_machine import StateMachine
from watchtower.core.store import Store
from watchtower.engine import run_template
from watchtower.engine.examples import load_example
from watchtower.engine.template import Template
from watchtower.net import HostRateLimiter, PoliteClient
from watchtower.validate import PortalHistory, check

BASE = "http://localhost:8081"
SSR = load_example("ssr_list_detail")
JS = load_example("js_json_api")


def edit(t: Template, change) -> Template:
    data = t.model_dump(mode="json", exclude_none=True)
    change(data)
    return Template.model_validate(data)


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


def _pagination(d):
    d["list"]["pagination"] = {"kind": "param", "name": "start", "start": 0, "step": 10}


def _missed(d):
    d["list"]["items"]["expr"] = "table.tenders tbody tr, table.neu-eingegangen tbody tr"


def _swap(d):
    d["fields"]["buyerName"] = {
        "by": "xpath",
        "expr": "//dt[normalize-space()='Vergabestelle']/following-sibling::dd[1]",
        "on": "detail",
    }


def _renamed(d):
    d["list"]["items"]["expr"] = "$.items[*]"
    f = d["fields"]
    f["title"]["expr"] = "$.intitule"
    f["buyerName"]["expr"] = "$.organisme.libelle"
    f["submissionDeadline"]["expr"] = "$.dateLimite"


def _tous(d):
    d["list"]["request"]["params"]["etat"] = "tous"


FIXES = {
    ("ssr", "markup"): (SSR, _markup),
    ("ssr", "pagination"): (SSR, _pagination),
    ("ssr", "silent-missed"): (SSR, _missed),
    ("ssr", "silent-swap"): (SSR, _swap),
    ("js", "renamed-keys"): (JS, _renamed),
    ("js", "silent-missed"): (JS, _tous),
}


@pytest.fixture
def mock():
    return create_app()


@pytest.fixture
async def client(mock):
    c = PoliteClient(limiter=HostRateLimiter(0), transport=httpx.ASGITransport(app=mock), max_attempts=1)
    yield c
    await c.aclose()


async def baseline(client, mock, kind):
    """A good read of the unchanged portal: the snapshot and counts a real history would hold."""
    template = SSR if kind == "ssr" else JS
    mock.state.variants[kind] = "base"
    good = await run_template(template, client, None)
    view = PortalView(
        id="p1",
        url=BASE + ENTRY[kind],
        state="serving",
        template_version=1,
        last_good_snapshot=good.snapshot,
        recent_counts=[len(good.tenders)] * 3,
    )
    return template, good, view


async def broken_read(client, mock, kind, variant, template):
    mock.state.variants[kind] = variant
    result = await run_template(template, client, None)
    return result, check(result, PortalHistory(recent_counts=[20, 20, 20]))


def repair_script(template: Template, notes: str) -> list:
    t = template.model_dump_json(exclude_defaults=True)
    return [
        [call("show_read")],
        [call("diff_structure")],
        [call("try_template", template=t, sample=2)],
        [call("count_list", template=t)],
        [call("submit_template", template=t, notes=notes)],
    ]


# ---------------------------------------------------------------- deterministic diagnoses (no model call)


@pytest.mark.parametrize("variant, expected", [("gone", Gone), ("login-wall", Blocked)], ids=["gone", "login-wall"])
async def test_gone_and_login_wall_without_a_model_call(client, mock, variant, expected):
    template, _, view = await baseline(client, mock, "ssr")
    result, health = await broken_read(client, mock, "ssr", variant, template)
    model = ScriptedModel([])
    run = await investigate(view, template, result, health, trigger="failure", client=client, model=model)
    assert isinstance(run.diagnosis, expected), run.diagnosis
    assert run.model_run is None and model.instructions is None
    assert "410" in run.diagnosis.reason if variant == "gone" else "login" in run.diagnosis.reason


async def test_portal_down_is_transient_without_a_model_call():
    def down(request):
        raise httpx.ConnectError("connection refused", request=request)

    client = PoliteClient(limiter=HostRateLimiter(0), transport=httpx.MockTransport(down), max_attempts=1)
    view = PortalView(id="p", url=BASE + "/ssr/", state="healing", template_version=1)
    from watchtower.engine.result import ReadResult
    from watchtower.validate import Health

    run = await investigate(
        view, SSR, ReadResult(errors=["x"]), Health(ok=False, problems=["x"]), trigger="failure", client=client,
        model=ScriptedModel([]),
    )  # fmt: skip
    assert isinstance(run.diagnosis, Transient) and "not answering" in run.diagnosis.reason
    await client.aclose()


async def test_healthy_audit_without_a_model_call(client, mock):
    for kind in ("ssr", "js"):
        template, good, view = await baseline(client, mock, kind)
        model = ScriptedModel([])
        run = await investigate(view, template, good, check(good), trigger="audit", client=client, model=model)
        assert isinstance(run.diagnosis, Healthy), (kind, run.evidence)
        assert run.model_run is None and model.instructions is None


@pytest.mark.parametrize("kind, variant", [("ssr", "silent-missed"), ("ssr", "silent-swap"), ("js", "silent-missed")])
async def test_audit_evidence_spots_silent_problems(client, mock, kind, variant):
    template, _, view = await baseline(client, mock, kind)
    mock.state.variants[kind] = variant
    result = await run_template(template, client, None)
    assert check(result, PortalHistory(recent_counts=view.recent_counts)).ok  # the validators can't see it
    findings = await evidence.audit(client, None, view.url, template, result)
    assert findings.suspicious, findings.describe()
    text = findings.describe()
    expected = {"silent-missed": ("did not deliver", "etat=tous"), "silent-swap": ("buyerName was read as",)}[variant]
    assert any(e in text for e in expected), text


# ---------------------------------------------------------------- repairs (scripted model)


@pytest.mark.parametrize(
    "kind, variant", [("ssr", "markup"), ("ssr", "pagination"), ("js", "renamed-keys")], ids=lambda x: x
)
async def test_failure_repairs_each_mutated_variant(client, mock, kind, variant):
    template, good, view = await baseline(client, mock, kind)
    result, health = await broken_read(client, mock, kind, variant, template)
    history = PortalHistory(recent_counts=view.recent_counts)
    assert not check(result, history).ok
    base, change = FIXES[(kind, variant)]
    fixed = edit(base, change)
    model = ScriptedModel(repair_script(fixed, f"the portal's {variant} changed"))
    run = await investigate(view, template, result, health, trigger="failure", client=client, model=model)
    assert isinstance(run.diagnosis, Changed), (run.diagnosis, model.seen_tool_outputs[-1:])
    assert run.diagnosis.reason == f"the portal's {variant} changed"
    # The prompt got the evidence, and the repaired template passes the pipeline's validation with history.
    assert "Trigger: failure" in run.evidence and "List page structure" in run.evidence
    assert "INVESTIGATE a portal" in model.instructions
    reread = await run_template(run.diagnosis.new_template, client, None)
    assert check(reread, history).ok and len(reread.tenders) == len(good.tenders)


@pytest.mark.parametrize("kind, variant", [("ssr", "silent-missed"), ("ssr", "silent-swap"), ("js", "silent-missed")])
async def test_audit_finding_leads_to_a_repair(client, mock, kind, variant):
    template, good, view = await baseline(client, mock, kind)
    mock.state.variants[kind] = variant
    result = await run_template(template, client, None)
    base, change = FIXES[(kind, variant)]
    fixed = edit(base, change)
    model = ScriptedModel(repair_script(fixed, "the audit found tenders read wrongly"))
    run = await investigate(view, template, result, check(result), trigger="audit", client=client, model=model)
    assert isinstance(run.diagnosis, Changed), run.diagnosis
    reread = await run_template(run.diagnosis.new_template, client, None)
    assert not (await evidence.audit(client, None, view.url, run.diagnosis.new_template, reread)).suspicious
    if variant == "silent-missed":
        assert len(reread.tenders) > len(result.tenders)


async def test_tool_outputs_show_read_diff_and_count(client, mock):
    template, _, view = await baseline(client, mock, "ssr")
    result, health = await broken_read(client, mock, "ssr", "markup", template)
    fixed = edit(SSR, _markup)
    model = ScriptedModel(repair_script(fixed, "renamed classes"))
    run = await investigate(view, template, result, health, trigger="failure", client=client, model=model)
    # The model saw the outputs of the first four tools; submit_template ended the run.
    show, diff, tried, counted = model.seen_tool_outputs
    assert "CURRENT TEMPLATE:" in show and "usual count 20" in show
    assert "+      table.ergebnisse" in diff
    assert "VERDICT: GOOD" in tried
    assert "LIST COUNT: 20 tenders" in counted and "→ OK" in counted
    assert isinstance(run.diagnosis, Changed) and run.model_run.stop_reason == "outcome"


# ---------------------------------------------------------------- closing rules and fallbacks


async def test_report_healthy_is_refused_for_a_failed_read(client, mock):
    template, _, view = await baseline(client, mock, "ssr")
    result, health = await broken_read(client, mock, "ssr", "markup", template)
    model = ScriptedModel(
        [[call("report_healthy", notes="looks fine")], [call("report_unrepairable", reason="the list is an image")]]
    )
    run = await investigate(view, template, result, health, trigger="failure", client=client, model=model)
    assert "Not accepted" in model.seen_tool_outputs[0]
    assert isinstance(run.diagnosis, Unrepairable) and run.diagnosis.reason == "the list is an image"


async def test_audit_can_clear_a_false_alarm(client, mock):
    template, _, view = await baseline(client, mock, "ssr")
    mock.state.variants["ssr"] = "silent-swap"
    result = await run_template(template, client, None)
    model = ScriptedModel([[call("report_healthy", notes="checked: fine")]])
    run = await investigate(view, template, result, check(result), trigger="audit", client=client, model=model)
    assert run.diagnosis == Healthy(notes="checked: fine")


@pytest.mark.parametrize("trigger, expected", [("failure", Unrepairable), ("audit", Transient)])
async def test_budget_exhausted(client, mock, trigger, expected):
    template, _, view = await baseline(client, mock, "ssr")
    mock.state.variants["ssr"] = "markup" if trigger == "failure" else "silent-swap"
    result = await run_template(template, client, None)
    model = ScriptedModel([[say("thinking")]] * 5)
    run = await investigate(
        view, template, result, check(result), trigger=trigger, client=client, model=model,
        budget=Budget(max_turns=2),
    )  # fmt: skip
    assert isinstance(run.diagnosis, expected) and "budget" in run.diagnosis.reason


async def test_without_a_model_key(client, mock, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    template, _, view = await baseline(client, mock, "ssr")
    result, health = await broken_read(client, mock, "ssr", "markup", template)
    run = await investigate(view, template, result, health, trigger="failure", client=client)
    assert isinstance(run.diagnosis, Unrepairable) and "OPENAI_API_KEY" in run.diagnosis.reason


# ---------------------------------------------------------------- through the pipeline


async def test_pipeline_heals_a_changed_portal(tmp_path, mock, client):
    """attach → markup change → read fails → healing → investigate repairs → serving with a new template version."""
    store = await Store.open(tmp_path / "w.db")
    fixed = edit(SSR, _markup)
    model = ScriptedModel(repair_script(fixed, "the result table and its labels were renamed"))

    async def attach_ssr(url):
        from watchtower.agent.outcomes import TemplateFound

        return TemplateFound(template=SSR, notes="learned")

    saved = pipeline._config
    pipeline._config = pipeline._Config(
        client=client, scenarios=Scenarios(attach_ssr, make_investigate(client, model=model))
    )
    try:
        p, _ = await store.add_portal(BASE + ENTRY["ssr"])

        async def job(kind, trigger, handler):
            if kind != "investigate":
                await store.request_run(p.id, kind, trigger=trigger)
            j = next(j for j in await store.active_jobs(p.id) if j.kind == kind)
            await handler(JobContext(store, StateMachine(store), j))
            await store.finish_job(j.id)

        await job("attach", "attach", handle_attach)
        served = await store.list_tenders(p.id)
        mock.state.variants["ssr"] = "markup"
        await job("read", "refresh", handle_read)
        assert (await store.get_portal(p.id)).state == "healing"
        assert await store.list_tenders(p.id) == served  # last correct tenders still served
        await job("investigate", "failure", handle_investigate)
        portal = await store.get_portal(p.id)
        assert portal.state == "serving" and portal.current_template_version == 2
        assert portal.reason.startswith("Repaired: the result table and its labels were renamed")
        [repair_run, *_] = await store.list_runs(p.id)
        assert repair_run.kind == "repair" and repair_run.result == "ok"
        assert repair_run.details["diagnosis"] == "changed"
    finally:
        pipeline._config = saved
        await store.close()
