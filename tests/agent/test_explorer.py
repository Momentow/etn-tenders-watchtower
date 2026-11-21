"""Agent v2 explorer (T-034, ADR 0015): browser tools, replay → API mode, v2 dry runs and v2 templates on attach."""

import json
import re
from pathlib import Path

import httpx
import pytest
from agents.tool_context import ToolContext
from scripted import ScriptedModel, call

from mockportals.app import create_app
from watchtower.agent.attach import ATTACH_PROMPT, run_attach
from watchtower.agent.context import AgentContext
from watchtower.agent.outcomes import TemplateFound
from watchtower.agent.tools import explore
from watchtower.agent.tools.core import _dry_run
from watchtower.engine.examples import _DIR_V2
from watchtower.engine.template_v2 import TemplateV2
from watchtower.net import BrowserPool, HostRateLimiter, PoliteClient

MOCK = "http://localhost:8081"


def v2_example(name: str, base: str = MOCK) -> str:
    return (_DIR_V2 / f"{name}.json").read_text(encoding="utf-8").replace(MOCK, base)


def _chromium_installed() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


browser_only = pytest.mark.skipif(not _chromium_installed(), reason="Playwright Chromium not installed")


@pytest.fixture
async def client():
    c = PoliteClient(limiter=HostRateLimiter(0), transport=httpx.ASGITransport(app=create_app()), max_attempts=1)
    yield c
    await c.aclose()


# ---------------------------------------------------------------- offline: no browser needed


def test_the_attach_prompt_teaches_the_explorer_and_v2():
    for needle in ("`replay`", "SAME DATA WITHOUT A BROWSER", '"format_version": 2', "tenderned.nl, API mode"):
        assert needle in ATTACH_PROMPT


async def test_a_v2_dry_run_reports_the_total_scope_and_a_good_verdict(client):
    ctx = AgentContext(f"{MOCK}/app/", client)
    report, t = await _dry_run(ctx, v2_example("app_api"), 3)
    assert isinstance(t, TemplateV2)
    # The API's own filters leave 22 records; scope drops the early-terminated call: 21 open calls.
    assert "portal's own total: 22 (list check: 21 kept + 1 dropped)" in report
    assert "scope dropped 1 record(s)" in report
    assert report.rstrip().endswith("VERDICT: GOOD"), report


async def test_a_v2_template_is_submitted_as_v2(client):
    from watchtower.agent.tools.core import submit_template

    ctx = AgentContext(f"{MOCK}/app/", client)
    raw = json.dumps({"template": v2_example("app_api"), "notes": "API mode"})
    tc = ToolContext(context=ctx, tool_name="submit_template", tool_call_id="t", tool_arguments=raw)
    answer = await submit_template.on_invoke_tool(tc, raw)
    assert answer == "Accepted."
    assert isinstance(ctx.outcome, TemplateFound) and isinstance(ctx.outcome.template, TemplateV2)


async def test_browser_tools_say_so_when_there_is_no_browser(client):
    ctx = AgentContext(f"{MOCK}/app/", client)  # browser=None
    r, err = await explore._act(ctx, explore._ACTION.validate_python({"do": "goto", "url": f"{MOCK}/app/"}))
    assert r is None and "No browser session" in err


def test_records_and_params_helpers():
    value = {"page": {"n": 2}, "content": [{"id": 1}, {"id": 2}, {"id": 3}], "links": [{"rel": "next"}]}
    assert explore._records(value) == ("$.content", [{"id": 1}, {"id": 2}, {"id": 3}])
    assert explore._with_params("https://x.nl/api?page=0&size=10", {"page": "2"}) == "https://x.nl/api?page=2&size=10"


# ---------------------------------------------------------------- live browser on the served mock portal


@pytest.fixture
def served():
    from mockportals.testing import serve

    with serve(port=18085) as s:
        yield s


@browser_only
async def test_explorer_clicks_through_finds_the_api_and_attaches_in_api_mode(served):
    base = served.url
    replay_answers: list[str] = []

    def replay_the_list_call(outputs: list[str]):
        m = re.search(r"#(\d+) GET \S+/app/api/publicaties\S*", outputs[-1])
        assert m, outputs[-1]
        return [call("replay", call=int(m.group(1)))]

    def submit(outputs: list[str]):
        replay_answers.append(outputs[-1])
        return [call("submit_template", template=v2_example("app_api", base), notes="API mode, found by replay")]

    model = ScriptedModel(
        [
            [call("open", url=f"{base}/app/")],
            [
                call(
                    "act", action=json.dumps({"do": "click", "target": {"role": "link", "name": "Alle aankondigingen"}})
                )
            ],
            [call("network", filter="publicaties")],
            replay_the_list_call,
            submit,
        ]
    )
    limiter = HostRateLimiter(0)
    browser = BrowserPool(limiter=limiter)
    try:
        async with PoliteClient(limiter=limiter) as c:
            run = await run_attach(f"{base}/app/", c, browser, model=model)
    finally:
        await browser.aclose()

    opened, clicked = model.seen_tool_outputs[0], model.seen_tool_outputs[1]
    assert "Alle aankondigingen" in opened  # the snapshot shows the link a person would click
    assert "/app/aankondigingen" in clicked and "/app/api/publicaties" in clicked  # the click caused the list call
    assert "SAME DATA WITHOUT A BROWSER" in replay_answers[0], replay_answers[0]
    assert isinstance(run.outcome, TemplateFound), run.outcome
    assert isinstance(run.outcome.template, TemplateV2) and run.outcome.template.list.kind == "api"
