"""Investigate and repair on format v2 templates (T-037) with a scripted model on the `app` mock portal: a moved API
endpoint (offline, and through the pipeline) and relabelled controls of a browser flow (real browser)."""

import json
from pathlib import Path

import httpx
import pytest
from agents.tool_context import ToolContext
from scripted import ScriptedModel, call

from mockportals import data
from mockportals.app import ENTRY, create_app
from watchtower.agent import evidence
from watchtower.agent.context import AgentContext
from watchtower.agent.investigate import investigate, make_investigate
from watchtower.agent.outcomes import Changed, PortalView, TemplateFound
from watchtower.agent.tools import investigate as inv_tools
from watchtower.core import pipeline
from watchtower.core.pipeline import Scenarios, handle_attach, handle_investigate, handle_read
from watchtower.core.scheduler import JobContext
from watchtower.core.state_machine import StateMachine
from watchtower.core.store import Store
from watchtower.engine import run_template
from watchtower.engine.examples import _DIR_V2
from watchtower.engine.template_v2 import TemplateV2
from watchtower.net import BrowserPool, HostRateLimiter, PoliteClient
from watchtower.validate import PortalHistory, check

BASE = "http://localhost:8081"


def v2(name: str, base: str = BASE, **replace: str) -> str:
    text = (_DIR_V2 / f"{name}.json").read_text(encoding="utf-8").replace(BASE, base)
    for old, new in replace.items():
        text = text.replace(old, new)
    return text


def moved(base: str = BASE) -> str:
    """app_api repaired for the `api-moved` variant: only the endpoint path changed."""
    return v2("app_api", base).replace("/app/api/publicaties", "/app/api/v3/publicaties")


def relabelled(base: str) -> str:
    """app_click repaired for the `relabelled` variant: only the three control names changed."""
    return (
        v2("app_click", base)
        .replace('"Alle aankondigingen"', '"Zoeken in publicaties"')
        .replace('"Laad meer"', '"Toon meer resultaten"')
        .replace('"Documenten"', '"Stukken"')
    )


def assert_open_calls(result) -> None:
    assert result.errors == []
    assert [t.id for t in result.tenders] == [t["id"] for t in data.expected_tenders("app")]
    assert all(t.documents for t in result.tenders)


@pytest.fixture
def mock():
    return create_app()


@pytest.fixture
async def client(mock):
    c = PoliteClient(limiter=HostRateLimiter(0), transport=httpx.ASGITransport(app=mock), max_attempts=1)
    yield c
    await c.aclose()


async def baseline(client, template: TemplateV2) -> tuple[PortalView, PortalHistory]:
    good = await run_template(template, client, None)
    assert_open_calls(good)
    history = PortalHistory(recent_counts=[len(good.tenders)])
    view = PortalView(
        id="p1",
        url=BASE + ENTRY["app"],
        state="serving",
        template_version=1,
        last_good_snapshot=good.snapshot,
        recent_counts=[len(good.tenders)],
    )
    return view, history


def repair_script(fixed: str, notes: str) -> list:
    return [
        [call("show_read")],
        [call("diff_structure")],
        [call("try_template", template=fixed)],
        [call("submit_template", template=fixed, notes=notes)],
    ]


# ---------------------------------------------------------------- offline: the API moved


async def test_a_moved_api_endpoint_is_repaired_in_v2(client, mock):
    t = TemplateV2.model_validate_json(v2("app_api"))
    view, history = await baseline(client, t)
    mock.state.variants["app"] = "api-moved"
    broken = await run_template(t, client, None)
    health = check(broken, history)
    assert not health.ok

    model = ScriptedModel(repair_script(moved(), "the portal moved its API to /app/api/v3"))
    run = await investigate(view, t, broken, health, trigger="failure", client=client, model=model)

    assert isinstance(run.diagnosis, Changed), run.diagnosis
    fixed = run.diagnosis.new_template
    assert isinstance(fixed, TemplateV2) and "/app/api/v3/" in fixed.list.request.url
    assert f"{BASE}/app/api/publicaties: HTTP 404" in run.evidence  # the pre-check probed the v2 list address
    shown, diff = model.seen_tool_outputs[0], model.seen_tool_outputs[1]
    assert '"format_version":2' in shown.replace(" ", "")  # show_read dumps the v2 template as stored
    assert "could not load the first list page" in diff and "HTTP 404" in diff  # the diff says why
    assert_open_calls(await run_template(fixed, client, None))


async def test_precheck_and_count_list_understand_v2(client, mock):
    t = TemplateV2.model_validate_json(v2("app_api"))
    assert evidence.list_address(t) == BASE + "/app/api/publicaties"
    page_t = TemplateV2.model_validate_json(v2("app_click"))
    assert evidence.list_address(page_t) == page_t.entry.url

    view, _ = await baseline(client, t)
    unchanged = evidence.diff_snapshots(view.last_good_snapshot, await evidence.live_snapshot(t, client, None))
    assert "could not" not in unchanged and "No snapshot" not in unchanged, unchanged
    ctx = AgentContext(view.url, client)
    ctx.extra.update(template=t, portal=view, result=await run_template(t, client, None))
    raw = json.dumps({"template": v2("app_api")})
    out = await inv_tools.count_list.on_invoke_tool(
        ToolContext(context=ctx, tool_name="count_list", tool_call_id="t", tool_arguments=raw), raw
    )
    assert "LIST COUNT: 21 tenders" in out, out


async def test_pipeline_heals_a_v2_portal_whose_api_moved(tmp_path, mock, client):
    """attach (v2) → API moves → read fails → healing, last tenders served → repair → serving with a v2 version 2."""
    store = await Store.open(tmp_path / "w.db")
    model = ScriptedModel(repair_script(moved(), "the portal moved its API to /app/api/v3"))

    async def attach_app(url):
        return TemplateFound(template=TemplateV2.model_validate_json(v2("app_api")), notes="learned")

    saved = pipeline._config
    pipeline._config = pipeline._Config(
        client=client, scenarios=Scenarios(attach_app, make_investigate(client, model=model))
    )
    try:
        p, _ = await store.add_portal(BASE + ENTRY["app"])

        async def job(kind, trigger, handler):
            if kind != "investigate":
                await store.request_run(p.id, kind, trigger=trigger)
            j = next(j for j in await store.active_jobs(p.id) if j.kind == kind)
            await handler(JobContext(store, StateMachine(store), j))
            await store.finish_job(j.id)

        await job("attach", "attach", handle_attach)
        assert (await store.get_portal(p.id)).state == "serving"
        served = await store.list_tenders(p.id)
        mock.state.variants["app"] = "api-moved"
        await job("read", "refresh", handle_read)
        assert (await store.get_portal(p.id)).state == "healing"
        assert await store.list_tenders(p.id) == served  # the last correct tenders are still served
        await job("investigate", "failure", handle_investigate)
        portal = await store.get_portal(p.id)
        assert portal.state == "serving" and portal.current_template_version == 2, portal.reason
        stored = (await store.list_template_versions(p.id))[0].template
        assert stored["format_version"] == 2 and "/app/api/v3/" in stored["list"]["request"]["url"]
    finally:
        pipeline._config = saved
        await store.close()


# ---------------------------------------------------------------- real browser: controls relabelled


def _chromium_installed() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


@pytest.mark.skipif(not _chromium_installed(), reason="Playwright Chromium not installed")
async def test_relabelled_flow_controls_are_repaired_with_the_explorer(monkeypatch):
    from mockportals.testing import serve
    from watchtower.engine import executor_v2
    from watchtower.net import SessionBudget

    monkeypatch.setattr(executor_v2, "READ_SESSION_BUDGET", SessionBudget(active_seconds=300, idle_ms=300))
    with serve(port=18086, variants={"app": "relabelled"}) as served:
        base = served.url
        t = TemplateV2.model_validate_json(v2("app_click", base))
        limiter = HostRateLimiter(0)
        browser = BrowserPool(limiter=limiter)
        try:
            async with PoliteClient(limiter=limiter) as c:
                broken = await run_template(t, c, browser)
                assert broken.errors and "Alle aankondigingen" in broken.errors[0]  # the flow step names its target
                history = PortalHistory(recent_counts=[21])
                view = PortalView(id="p1", url=f"{base}/app/", state="serving", template_version=1, recent_counts=[21])
                model = ScriptedModel(
                    [
                        [call("show_read")],
                        [call("open", url=f"{base}/app/")],  # the snapshot shows what the link is called now
                        [call("submit_template", template=relabelled(base), notes="the portal renamed its controls")],
                    ]
                )
                run = await investigate(
                    view, t, broken, check(broken, history), trigger="failure", client=c, browser=browser, model=model
                )
                assert "Zoeken in publicaties" in model.seen_tool_outputs[1]
                assert isinstance(run.diagnosis, Changed), run.diagnosis
                assert_open_calls(await run_template(run.diagnosis.new_template, c, browser))
        finally:
            await browser.aclose()


async def test_an_unchanged_v2_portal_audits_healthy_without_a_model_call(client):
    from watchtower.agent.outcomes import Healthy

    t = TemplateV2.model_validate_json(v2("app_api"))
    view, history = await baseline(client, t)
    good = await run_template(t, client, None)
    model = ScriptedModel([])  # any model call would get "nothing left to do" and no outcome
    run = await investigate(view, t, good, check(good, history), trigger="audit", client=client, model=model)
    assert isinstance(run.diagnosis, Healthy), (run.diagnosis, run.evidence)
    assert run.model_run is None
