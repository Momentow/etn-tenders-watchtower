"""Model usage metering (T-099, ADR 0017): prices, the collector, and usage stored per run through the real pipeline."""

from datetime import timedelta

import pytest
from scripted import ScriptedModel, call
from test_e2e_api import attach_script, e2e  # noqa: F401 - the e2e fixture
from test_investigate import _markup, edit, repair_script

from watchtower.agent import usage
from watchtower.agent.runtime import run_scenario
from watchtower.core.times import utcnow
from watchtower.engine.examples import load_example

SCRIPTED = '{"ScriptedModel": [1.0, 0.1, 2.0]}'  # USD per 1M tokens: input, cached, output


def test_cost_uses_the_cached_rate_for_cached_tokens():
    # 1M input of which 400k cached, 100k output: 600k x 0.20 + 400k x 0.02 + 100k x 1.20 = 0.248 USD
    assert usage.cost_usd("gpt-5.6-luna", 1_000_000, 400_000, 100_000) == pytest.approx(0.248)


def test_unknown_models_have_no_cost_and_env_prices_override(monkeypatch):
    monkeypatch.delenv(usage.PRICES_ENV, raising=False)
    assert usage.cost_usd("some-other-model", 1000, 0, 1000) is None
    monkeypatch.setenv(usage.PRICES_ENV, '{"some-other-model": [2, 0.5, 8], "gpt-5.6-luna": [1, 0.1, 6]}')
    assert usage.cost_usd("some-other-model", 1_000_000, 0, 1_000_000) == pytest.approx(10.0)
    assert usage.price_for("gpt-5.6-luna") == (1.0, 0.1, 6.0)
    monkeypatch.setenv(usage.PRICES_ENV, "not json")
    assert usage.price_for("gpt-5.6-luna") == usage.PRICES["gpt-5.6-luna"]  # a bad value is ignored, with a warning


def test_record_without_a_collector_is_a_noop_and_zero_calls_are_not_recorded():
    assert usage.record("attach", "strong", "m", requests=0, input_tokens=0, cached_tokens=0, output_tokens=0) is None
    u = usage.record("attach", "strong", "m", requests=1, input_tokens=5, cached_tokens=0, output_tokens=1)
    assert u is not None and u.cost_usd is None
    with usage.collect() as used:
        usage.record("attach", "light", "m", requests=2, input_tokens=5, cached_tokens=0, output_tokens=1)
    assert [x.tier for x in used] == ["light"]


async def test_a_scenario_run_reports_each_tier_that_made_calls(monkeypatch):
    from test_model_tiers import answer

    from watchtower.agent.context import AgentContext

    monkeypatch.setenv(usage.PRICES_ENV, SCRIPTED)
    strong = ScriptedModel(
        [[call("delegate_classify_notices", notice_types=["a"])], [call("report_unreadable", reason="t")]]
    )
    light = ScriptedModel([answer(classes=[{"notice_type": "a", "open_call": "yes", "reason": "r"}])])
    with usage.collect() as used:
        await run_scenario("attach", "A.", "go", AgentContext("http://x/", None), model=strong, light_model=light)
    by_tier = {u.tier: u for u in used}
    assert by_tier["strong"].requests == 2 and by_tier["light"].requests == 1
    assert by_tier["strong"].model == "ScriptedModel" and by_tier["strong"].scenario == "attach"
    assert by_tier["strong"].cost_usd == pytest.approx(200 / 1_000_000)  # 2 calls x 100 input tokens at 1 USD/1M


async def test_attach_and_repair_usage_is_stored_per_run_and_reads_add_none(e2e, monkeypatch):  # noqa: F811
    monkeypatch.setenv(usage.PRICES_ENV, SCRIPTED)
    fixed = edit(load_example("ssr_list_detail"), _markup)
    e2e.use_models(attach_script(e2e.template("ssr_list_detail")), repair_script(fixed, "markup renamed"))
    store = e2e.app.state.store
    pid = await e2e.add("ssr")

    [attach] = await store.model_usage_summary(pid)
    assert (attach["scenario"], attach["tier"], attach["runs"], attach["requests"]) == ("attach", "strong", 1, 2)
    assert attach["cost_usd"] == pytest.approx(200 / 1_000_000)

    # A regular read makes no model call, so it adds no usage.
    await e2e.api.post(f"/portals/{pid}/refresh")
    read = await store.claim_next_job(["read"], now=utcnow() + timedelta(days=1))
    await e2e.app.state.scheduler.run_job(read)
    portal = await e2e.get(f"/portals/{pid}")
    assert portal["state"] == "serving"
    assert len(await store.model_usage_summary(pid)) == 1

    # The portal changes: the repair's investigate run is metered on the repair run.
    e2e.mock.state.variants["ssr"] = "markup"
    await e2e.api.post(f"/portals/{pid}/refresh")
    await e2e.drain()
    assert (await e2e.get(f"/portals/{pid}"))["state"] == "serving"
    rows = {r["scenario"]: r for r in await store.model_usage_summary(pid)}
    assert set(rows) == {"attach", "investigate"} and rows["investigate"]["requests"] > 0
