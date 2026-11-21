"""Model tiers (T-035, ADR 0015): settings per tier, tiered budgets, the light worker and the `delegate_*` tools.

Scripted models stand in for both tiers; the mock portals serve the pages. No key, no network.
"""

import json

import httpx
import pytest
from pydantic import BaseModel
from scripted import ScriptedModel, call, say

from mockportals.app import create_app
from watchtower.agent import runtime
from watchtower.agent.config import DEFAULT_MODEL, AgentSettings, load_settings
from watchtower.agent.context import AgentContext
from watchtower.agent.outcomes import Unreadable
from watchtower.agent.registry import ATTACH, INVESTIGATE, tools_for
from watchtower.agent.runtime import Budget, LightWorker, TieredBudget, run_scenario
from watchtower.engine.examples import _DIR as EXAMPLES_DIR

BASE = "http://localhost:8081"
DETAIL = f"{BASE}/ssr/ausschreibung/VG-2026-001"
DELEGATES = {"delegate_map_fields", "delegate_verify_tenders", "delegate_classify_notices", "delegate_describe_page"}


@pytest.fixture
def make_ctx():
    def make() -> AgentContext:
        from watchtower.net import HostRateLimiter, PoliteClient

        client = PoliteClient(
            limiter=HostRateLimiter(0), transport=httpx.ASGITransport(app=create_app()), max_attempts=1
        )
        return AgentContext(f"{BASE}/ssr/", client)

    return make


def answer(**data) -> list:
    """One light-model turn: its structured output as JSON text."""
    return [say(json.dumps(data))]


# --------------------------------------------------------------------------- settings


@pytest.mark.parametrize(
    ("env", "strong", "light"),
    [
        ({}, DEFAULT_MODEL, DEFAULT_MODEL),
        ({"WATCHTOWER_MODEL": "m"}, "m", "m"),
        ({"WATCHTOWER_MODEL": "m", "WATCHTOWER_MODEL_STRONG": "s"}, "s", "s"),
        ({"WATCHTOWER_MODEL_LIGHT": "l"}, DEFAULT_MODEL, "l"),
        ({"WATCHTOWER_MODEL": "m", "WATCHTOWER_MODEL_STRONG": "s", "WATCHTOWER_MODEL_LIGHT": "l"}, "s", "l"),
    ],
)
def test_models_per_tier_fall_back_strong_then_model_then_default(monkeypatch, env, strong, light):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    for name in ("WATCHTOWER_MODEL", "WATCHTOWER_MODEL_STRONG", "WATCHTOWER_MODEL_LIGHT"):
        monkeypatch.delenv(name, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    s = load_settings()
    assert (s.model, s.model_for("strong"), s.model_for("light")) == (strong, strong, light)


async def test_run_agent_picks_the_tiers_model_and_tags_the_call(monkeypatch):
    seen = []

    async def fake_run(agent, input, *, run_config, **kw):
        seen.append((run_config.model, run_config.workflow_name))

    monkeypatch.setattr(runtime.Runner, "run", fake_run)
    settings = AgentSettings(api_key="sk-test", model="big", light_model="small")
    agent = runtime.Agent(name="x")
    await runtime.run_agent(agent, "hi", max_turns=1, settings=settings)
    await runtime.run_agent(agent, "hi", max_turns=1, tier="light", settings=settings)
    assert seen == [("big", "watchtower:strong"), ("small", "watchtower:light")]


def test_a_plain_budget_is_the_strong_tier_with_the_default_light_budget():
    t = TieredBudget.of(Budget(max_turns=5))
    assert t.strong.max_turns == 5 and t.light == runtime.DEFAULT_LIGHT
    assert TieredBudget.of(t) is t


# --------------------------------------------------------------------------- light worker


class Pick(BaseModel):
    word: str


async def test_light_worker_counts_usage_and_refuses_past_its_budget():
    w = LightWorker(Budget(max_turns=2, max_tokens=10_000), model=ScriptedModel([answer(word="a"), answer(word="b")]))
    assert await w.run("t", "Pick a word.", "go", Pick) == Pick(word="a")
    assert await w.run("t", "Pick a word.", "go", Pick) == Pick(word="b")
    refused = await w.run("t", "Pick a word.", "go", Pick)
    assert isinstance(refused, str) and "budget is used up" in refused
    assert w.usage.requests == 2 and w.usage.input_tokens == 200


async def test_light_worker_stops_at_its_token_budget():
    w = LightWorker(Budget(max_turns=10, max_tokens=150), model=ScriptedModel([answer(word="a")] * 3))
    await w.run("t", "i", "go", Pick)
    await w.run("t", "i", "go", Pick)  # 200 tokens now: over the budget
    assert "token budget" in await w.run("t", "i", "go", Pick)


async def test_light_worker_turns_a_bad_answer_into_a_sentence():
    w = LightWorker(model=ScriptedModel([[say("not json")]]))
    out = await w.run("t", "i", "go", Pick)
    assert isinstance(out, str) and "do this check yourself" in out


# --------------------------------------------------------------------------- scenario + delegate tools


def test_delegate_tools_are_offered_to_both_scenarios():
    for scenario in (ATTACH, INVESTIGATE):
        (name,) = scenario
        assert DELEGATES <= {t.name for t in tools_for(name)}


async def test_strong_model_delegates_and_usage_is_split_by_tier(make_ctx):
    strong = ScriptedModel(
        [
            [call("delegate_classify_notices", notice_types=["Aankondiging opdracht", "Gegunde opdracht", "X"])],
            [call("report_unreadable", reason="test")],
        ]
    )
    light = ScriptedModel(
        [
            answer(
                classes=[
                    {"notice_type": "Aankondiging opdracht", "open_call": "yes", "reason": "contract notice"},
                    {"notice_type": "Gegunde opdracht", "open_call": "no", "reason": "award"},
                    {"notice_type": "invented", "open_call": "yes", "reason": "-"},
                ]
            )
        ]
    )
    run = await run_scenario("attach", "Attach.", "go", make_ctx(), model=strong, light_model=light)
    assert isinstance(run.outcome, Unreadable)
    out = strong.seen_tool_outputs[0]
    assert out.startswith("LIGHT-MODEL PROPOSAL")
    assert "'Aankondiging opdracht': yes" in out and "'Gegunde opdracht': no" in out
    assert "'X': unsure (not answered)" in out and "invented" not in out
    assert run.by_tier["strong"].requests == 2 and run.by_tier["light"].requests == 1
    assert run.requests == 3 and run.input_tokens == 300


async def test_light_budget_used_up_is_told_to_the_strong_model(make_ctx):
    strong = ScriptedModel(
        [[call("delegate_classify_notices", notice_types=["a"])], [call("report_unreadable", reason="t")]]
    )
    budget = TieredBudget(strong=Budget(), light=Budget(max_turns=0))
    run = await run_scenario(
        "attach", "A.", "go", make_ctx(), model=strong, light_model=ScriptedModel([]), budget=budget
    )
    assert "budget is used up" in strong.seen_tool_outputs[0] and run.by_tier["light"].requests == 0


async def test_map_fields_checks_each_proposed_rule_on_the_page(make_ctx):
    ctx = make_ctx()
    ctx.light = LightWorker(
        model=ScriptedModel(
            [
                answer(
                    picks=[
                        {"index": 1, "field": "buyerName"},
                        {"index": 4, "field": "contractNature"},
                        {"index": 0, "field": "notAField"},
                        {"index": 99, "field": "title"},
                    ]
                )
            ]
        )
    )
    from watchtower.agent.tools import delegate

    out = await delegate.delegate_map_fields.on_invoke_tool(_wrap(ctx), json.dumps({"url": DETAIL}))
    assert out.startswith("LIGHT-MODEL PROPOSAL")
    assert "buyerName ← 'Vergabestelle' = 'Stadt Musterstadt'" in out and "rule extracts the value" in out
    assert "contractNature ← 'Art des Auftrags'" in out
    assert "'notAField' is no contract field" in out and "title" not in out.split("check", 1)[1]


async def test_verify_tenders_asks_only_about_values_not_on_the_page_verbatim(make_ctx):
    ctx = make_ctx()
    light = ScriptedModel(
        [
            answer(verdicts=[{"field": "submissionDeadline", "verdict": "right", "evidence": "Angebotsfrist 26.11"}]),
            answer(verdicts=[{"field": "procedureType", "verdict": "wrong", "evidence": "Verfahrensart: …"}]),
        ]
    )
    ctx.light = LightWorker(model=light)
    from watchtower.agent.tools import delegate

    template = (EXAMPLES_DIR / "ssr_list_detail.json").read_text(encoding="utf-8")
    args = json.dumps({"template": template, "tenders": 2})
    out = await delegate.delegate_verify_tenders.on_invoke_tool(_wrap(ctx), args)
    assert out.startswith("LIGHT-MODEL PROPOSAL") and out.count("on the page verbatim:") == 2
    assert "title" in out.split("on the page verbatim:")[1].splitlines()[0]
    assert "submissionDeadline: RIGHT" in out and "procedureType: WRONG" in out
    assert "not judged:" in out  # the scripted answers skip most asked fields
    assert ctx.light.usage.requests == 2


async def test_describe_page_flags_routes_that_are_not_on_the_page(make_ctx):
    ctx = make_ctx()
    ctx.light = LightWorker(
        model=ScriptedModel(
            [
                answer(
                    list_on_this_page=False,
                    total_shown=None,
                    routes=[
                        {"kind": "link", "text": "Login", "href": "#login", "why": "menu"},
                        {"kind": "link", "text": "Alle Ausschreibungen", "href": "/alle", "why": "invented"},
                    ],
                )
            ]
        )
    )
    from watchtower.agent.tools import delegate

    out = await delegate.delegate_describe_page.on_invoke_tool(_wrap(ctx), json.dumps({"url": DETAIL}))
    lines = out.splitlines()
    assert any("'Login'" in x and "NOT found" not in x for x in lines)
    assert any("'Alle Ausschreibungen'" in x and "NOT found on the page" in x for x in lines)


async def test_delegates_outside_a_scenario_say_the_light_model_is_unavailable(make_ctx):
    from watchtower.agent.tools import delegate

    out = await delegate.delegate_classify_notices.on_invoke_tool(
        _wrap(make_ctx()), json.dumps({"notice_types": ["a"]})
    )
    assert "not available" in out


def _wrap(ctx: AgentContext):
    from agents.tool_context import ToolContext

    return ToolContext(context=ctx, tool_name="t", tool_call_id="c", tool_arguments="{}")
