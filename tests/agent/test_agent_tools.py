"""Core tools on the mock portals (no network, no model), and the scenario loop with a scripted model."""

import json

import httpx
import pytest
from scripted import ScriptedModel, call, say

from mockportals.app import create_app
from watchtower.agent.context import AgentContext
from watchtower.agent.outcomes import TemplateFound, Unreadable
from watchtower.agent.pages import get_page
from watchtower.agent.registry import ATTACH, tool, tools_for
from watchtower.agent.runtime import Budget, run_scenario
from watchtower.agent.tools import core
from watchtower.engine.examples import _DIR as EXAMPLES_DIR

BASE = "http://localhost:8081"


def example(name: str) -> str:
    return (EXAMPLES_DIR / f"{name}.json").read_text(encoding="utf-8")


@pytest.fixture
def make_ctx():
    clients = []

    def make(variants: dict[str, str] | None = None) -> AgentContext:
        from watchtower.net import HostRateLimiter, PoliteClient

        client = PoliteClient(
            limiter=HostRateLimiter(0), transport=httpx.ASGITransport(app=create_app(variants)), max_attempts=1
        )
        clients.append(client)
        return AgentContext(f"{BASE}/ssr/", client)

    return make


def test_registry_has_the_core_tools():
    names = {t.name for t in tools_for("attach")}
    assert {
        "fetch",
        "render",
        "try_template",
        "submit_template",
        "report_blocked",
        "report_unreadable",
        "report_transient",
    } <= names
    with pytest.raises(ValueError, match="already registered"):

        @tool(scenarios=ATTACH)
        async def fetch(ctx, url: str) -> str:  # same name as the core tool
            return ""


async def test_fetch_outlines_html_summarizes_json_and_caches(make_ctx):
    ctx = make_ctx()
    page = await get_page(ctx, f"{BASE}/ssr/ausschreibungen")
    out = core._describe(page, None)
    assert "table.tenders" in out and "… ×8 more tr" in out and "a[href=/ssr/ausschreibungen?seite=2 rel=next]" in out
    assert "focus 'table.tenders tbody tr': 10 match(es)" in core._describe(page, "table.tenders tbody tr")
    assert await get_page(ctx, f"{BASE}/ssr/ausschreibungen") is page  # cached: no second request
    api = core._describe(await get_page(ctx, f"{BASE}/js/api/consultations"), None)
    assert "$.resultats[*]" in api
    doc = await get_page(ctx, f"{BASE}/files/ssr/VG-2026-001/Leistungsverzeichnis.pdf")
    assert doc.text.startswith("[file: application/pdf")


@pytest.mark.parametrize("name", ["ssr_list_detail", "js_json_api", "multi_step_download", "search_form"])
async def test_example_templates_dry_run_good(make_ctx, name):
    report, template = await core._dry_run(make_ctx(), example(name), 2)
    assert template is not None
    assert "VERDICT: GOOD" in report, report
    assert "coverage: url 2/2, title 2/2" in report


async def test_dry_run_reports_a_broken_template(make_ctx):
    report, _ = await core._dry_run(make_ctx({"ssr": "markup"}), example("ssr_list_detail"), 2)
    assert "VERDICT: NOT GOOD" in report and "the list reads no tenders" in report


async def test_dry_run_reports_schema_errors_with_paths(make_ctx):
    bad = json.loads(example("ssr_list_detail"))
    bad["fields"]["title"]["by"] = "jsonpath"
    report, template = await core._dry_run(make_ctx(), json.dumps(bad), 2)
    assert template is None and report.startswith("Template rejected") and "fields.title" in report
    report, _ = await core._dry_run(make_ctx(), "{not json", 2)
    assert report.startswith("Template is not valid JSON")


async def test_scenario_ends_on_an_accepted_template(make_ctx):
    ctx = make_ctx()
    model = ScriptedModel(
        [
            [call("fetch", url=f"{BASE}/ssr/ausschreibungen", focus="table.tenders tbody tr")],
            [call("submit_template", template=example("ssr_list_detail"), notes="table list + detail pages")],
            [say("should never be reached")],
        ]
    )
    run = await run_scenario("attach", "Attach the portal.", f"Portal: {BASE}/ssr/", ctx, model=model)
    assert run.stop_reason == "outcome" and isinstance(run.outcome, TemplateFound)
    assert run.outcome.template.list.kind == "html" and run.requests == 2
    assert "10 match(es)" in model.seen_tool_outputs[0]
    assert "Template reference" in model.instructions and "Example: js_json_api" in model.instructions


async def test_refused_submit_lets_the_model_continue(make_ctx):
    ctx = make_ctx({"ssr": "markup"})
    model = ScriptedModel(
        [
            [call("submit_template", template=example("ssr_list_detail"), notes="")],
            [call("report_unreadable", reason="The list markup can't be read.")],
        ]
    )
    run = await run_scenario("attach", "Attach.", "go", ctx, model=model)
    assert "Not accepted" in model.seen_tool_outputs[0]
    assert run.outcome == Unreadable(reason="The list markup can't be read.") and run.requests == 2


@pytest.mark.parametrize(
    ("budget", "reason"),
    [(Budget(max_turns=3), "turns"), (Budget(max_tokens=250), "tokens")],
)
async def test_budgets_stop_the_run(make_ctx, budget, reason):
    turns = [[call("fetch", url=f"{BASE}/ssr/")] for _ in range(10)]
    run = await run_scenario("attach", "Attach.", "go", make_ctx(), model=ScriptedModel(turns), budget=budget)
    assert run.outcome is None and run.stop_reason == reason


async def test_a_text_answer_is_nudged_until_the_run_ends_with_a_tool(make_ctx):
    model = ScriptedModel(
        [
            [say("I think the portal is fine.")],
            [call("report_unreadable", reason="No tender list on the site.")],
        ]
    )
    run = await run_scenario("attach", "Attach.", "go", make_ctx(), model=model)
    assert run.stop_reason == "outcome" and run.requests == 2 and run.input_tokens == 200


async def test_nudges_are_limited(make_ctx):
    model = ScriptedModel([[say("done")] for _ in range(10)])
    run = await run_scenario("attach", "Attach.", "go", make_ctx(), model=model)
    assert run.outcome is None and run.stop_reason == "no_outcome" and run.requests == 3


async def test_dry_run_warns_about_missed_pagination_documents_and_fields(make_ctx):
    t = json.loads(example("search_form"))
    t["list"]["pagination"] = {"kind": "none"}
    del t["documents"], t["fields"]["publicationDate"]
    report, _ = await core._dry_run(make_ctx(), json.dumps(t), 2)
    assert "VERDICT: GOOD" in report  # warnings don't block, they steer
    assert "numbered page control <button> '2' name=\"strona\"" in report
    assert "no documents on any of the 2 sampled tenders" in report
    assert "`publicationDate` is empty on every sampled tender" in report


async def test_dry_run_flags_documents_behind_a_login(make_ctx):
    t = {
        "timezone": "Europe/Vienna",
        "list": {
            "kind": "html",
            "request": {"url": f"{BASE}/login/ausschreibungen"},
            "items": {"by": "css", "expr": "table.verfahren tbody tr"},
        },
        "detail": {"link": {"by": "css", "expr": "td a", "attr": "href"}},
        "fields": {"id": {"by": "css", "expr": "td:nth-child(1)"}, "title": {"by": "css", "expr": "td:nth-child(2)"}},
        "documents": {"links": {"by": "css", "expr": "ul.unterlagen a", "attr": "href", "many": True}},
    }
    report, _ = await core._dry_run(make_ctx(), json.dumps(t), 2)
    assert "VERDICT: NOT GOOD" in report and "leads to a login" in report and "report_blocked" in report
