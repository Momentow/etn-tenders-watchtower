"""Scenario `generate_template` (T-015) on the mock portals with a scripted model: outcomes and pipeline wiring."""

import httpx
import pytest
from scripted import ScriptedModel, call, say

from mockportals.app import create_app
from watchtower.agent.attach import ATTACH_PROMPT, make_generate_template, run_attach
from watchtower.agent.outcomes import Blocked, TemplateFound, Unreadable
from watchtower.agent.runtime import Budget
from watchtower.core import pipeline
from watchtower.core.pipeline import Scenarios, handle_attach
from watchtower.core.scheduler import JobContext
from watchtower.core.state_machine import StateMachine
from watchtower.core.store import Store
from watchtower.engine.examples import _DIR as EXAMPLES_DIR
from watchtower.net import HostRateLimiter, PoliteClient

BASE = "http://localhost:8081"


def example(name: str) -> str:
    return (EXAMPLES_DIR / f"{name}.json").read_text(encoding="utf-8")


@pytest.fixture
async def client():
    c = PoliteClient(limiter=HostRateLimiter(0), transport=httpx.ASGITransport(app=create_app()), max_attempts=1)
    yield c
    await c.aclose()


async def test_accepted_template_is_found_and_the_prompt_is_the_attach_prompt(client):
    model = ScriptedModel([[call("submit_template", template=example("ssr_list_detail"), notes="table + detail")]])
    result = await run_attach(f"{BASE}/ssr/", client, None, model=model)
    assert isinstance(result.outcome, TemplateFound)
    assert result.outcome.template.software == "mock-ssr" and result.outcome.notes == "table + detail"
    assert result.run.stop_reason == "outcome"
    assert "detail.resolve" in model.instructions and ATTACH_PROMPT.splitlines()[0] in model.instructions


async def test_a_block_report_loses_to_a_template_that_already_worked(client):
    # E.g. a captcha widget is shown, but the list and the documents load without it (try first, then decide).
    model = ScriptedModel(
        [
            [call("try_template", template=example("ssr_list_detail"))],
            [call("report_blocked", reason="The portal shows a captcha.")],
        ]
    )
    result = await run_attach(f"{BASE}/ssr/", client, None, model=model)
    assert "VERDICT: GOOD" in model.seen_tool_outputs[0]
    assert isinstance(result.outcome, TemplateFound)
    assert result.outcome.template.software == "mock-ssr"
    assert "reported a block (The portal shows a captcha.)" in result.outcome.notes


async def test_a_real_block_is_reported(client):
    # The evidence T-039 asks for: the documents of two different tenders lead to the login.
    model = ScriptedModel(
        [
            [call("detect_block", url=f"{BASE}/login/verfahren/W-2026-001")],
            [call("detect_block", url=f"{BASE}/login/verfahren/W-2026-002")],
            [call("report_blocked", reason="Documents need a login (redirect to /login).")],
        ]
    )
    result = await run_attach(f"{BASE}/login/", client, None, model=model)
    assert result.outcome == Blocked(reason="Documents need a login (redirect to /login).")


async def test_transient_and_unreadable_reports_become_unreadable(client):
    model = ScriptedModel([[call("report_transient", reason="The portal answers 503.")]])
    result = await run_attach(f"{BASE}/ssr/", client, None, model=model)
    assert result.outcome == Unreadable(reason="the portal could not be read right now (The portal answers 503)")
    model = ScriptedModel([[call("report_unreadable", reason="There is no public tender list.")]])
    assert (await run_attach(f"{BASE}/ssr/", client, None, model=model)).outcome == Unreadable(
        reason="There is no public tender list."
    )


async def test_an_exhausted_budget_is_unreadable_with_the_limit_named(client):
    model = ScriptedModel([[call("fetch", url=f"{BASE}/ssr/")] for _ in range(5)])
    result = await run_attach(f"{BASE}/ssr/", client, None, model=model, budget=Budget(max_turns=2))
    assert result.outcome == Unreadable(reason="no working template was found within the turn limit of the attach")


async def test_a_model_that_never_closes_the_run_is_unreadable(client):
    model = ScriptedModel([[say("done")] for _ in range(5)])
    result = await run_attach(f"{BASE}/ssr/", client, None, model=model)
    assert result.outcome == Unreadable(reason="the agent stopped without finding a working template")


async def test_no_key_is_unreadable_not_a_crash(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = await run_attach(f"{BASE}/ssr/", client, None)
    assert result.run is None
    assert result.outcome == Unreadable(reason="the agent is not configured (OPENAI_API_KEY is not set)")


async def test_pipeline_attaches_a_portal_through_the_scenario(client, tmp_path):
    model = ScriptedModel([[call("submit_template", template=example("ssr_list_detail"), notes="table + detail")]])
    saved = pipeline._config
    pipeline._config = pipeline._Config(
        client=client,
        scenarios=Scenarios(generate_template=make_generate_template(client, None, model=model)),
    )
    store = await Store.open(tmp_path / "w.db")
    try:
        portal, _ = await store.add_portal(f"{BASE}/ssr/")
        run, _ = await store.request_run(portal.id, "attach", trigger="attach")
        job = (await store.active_jobs(portal.id))[0]
        await handle_attach(JobContext(store, StateMachine(store), job))
        portal = await store.get_portal(portal.id)
        assert portal.state == "serving" and portal.current_template_version == 1
        assert (await store.get_run(run.id)).result == "ok"
        assert len(await store.list_tenders(portal.id)) == 20
    finally:
        pipeline._config = saved
        await store.close()
