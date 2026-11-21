"""The single place where model calls happen (ADR 0002: `agent` is the only package that calls a model).

`run_agent` runs any SDK agent with the configured model of a tier (ADR 0015: `strong` runs scenarios, `light` does
the narrow tasks the strong model delegates) and the key. `run_scenario` runs one scenario (attach or investigate) with
its registered tools and a `TieredBudget` (turns, tokens, wall time per tier), and returns the outcome a tool recorded
plus usage per tier. `LightWorker` is how `delegate_*` tools call the light model: one structured call per task,
counted against the light budget. Keeping every call here is what lets T-099 meter usage without touching scenario code.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, TypeVar

from agents import (
    Agent,
    MaxTurnsExceeded,
    Model,
    ModelSettings,
    OpenAIProvider,
    RunConfig,
    RunContextWrapper,
    RunHooks,
    Runner,
    RunResult,
    ToolsToFinalOutputResult,
)
from agents.items import ModelResponse
from openai.types.shared import Reasoning

from watchtower.agent import usage
from watchtower.agent.config import TIERS, AgentSettings, Tier, load_settings
from watchtower.agent.context import AgentContext, Outcome
from watchtower.agent.prompts import instructions
from watchtower.agent.registry import Scenario, tools_for

log = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class Budget:
    max_turns: int = 30  # model calls
    max_tokens: int = 400_000  # input + output, summed over all calls
    wall_seconds: float = 900.0  # strong: the whole run, tools included; light: each single call


# Light budget for a scenario given a plain `Budget` (ADR 0015: investigate's light tier).
DEFAULT_LIGHT = Budget(max_turns=30, max_tokens=200_000, wall_seconds=120.0)


@dataclass(frozen=True)
class TieredBudget:
    """Budgets per tier (ADR 0015). The run's wall time is `strong.wall_seconds`; light calls happen inside it."""

    strong: Budget = Budget()
    light: Budget = DEFAULT_LIGHT

    @classmethod
    def of(cls, budget: Budget | TieredBudget) -> TieredBudget:
        return budget if isinstance(budget, TieredBudget) else cls(strong=budget)


@dataclass
class TierUsage:
    requests: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0

    def add(self, usage: Any) -> None:
        self.requests += getattr(usage, "requests", 0) or 0
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.cached_tokens += _cached(usage)
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class ScenarioRun:
    outcome: Outcome | None  # None: the budget ran out or the model stopped without a report
    stop_reason: str  # "outcome" | "turns" | "tokens" | "time" | "no_outcome" (strong-tier limits)
    requests: int  # the usage fields sum both tiers; `by_tier` splits them
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    seconds: float
    final_text: str = ""
    by_tier: dict[Tier, TierUsage] = field(default_factory=lambda: {t: TierUsage() for t in TIERS})


class BudgetExceeded(Exception):
    pass


async def run_agent(
    agent: Agent[Any],
    input: str | list[Any],
    *,
    max_turns: int,
    tier: Tier = "strong",
    settings: AgentSettings | None = None,
    context: Any = None,
    hooks: RunHooks[Any] | None = None,
    model: Model | None = None,
) -> RunResult:
    """Run `agent` on `input` with the tier's configured model; raises the SDK's errors (e.g. `MaxTurnsExceeded`).

    `model` replaces the configured one (tests pass a scripted model; no key needed then). Every model call of the
    service goes through here, tagged with its `tier` (the run's workflow name), so T-099 can meter it per tier.
    """
    if model is not None:
        run_config = RunConfig(model=model, workflow_name=f"watchtower:{tier}")
    else:
        settings = settings or load_settings()
        run_config = RunConfig(
            model=settings.model_for(tier),
            model_provider=OpenAIProvider(api_key=settings.api_key),
            workflow_name=f"watchtower:{tier}",
        )
    return await Runner.run(agent, input, max_turns=max_turns, run_config=run_config, context=context, hooks=hooks)


class LightWorker:
    """Runs the light tier's tasks for one scenario run (ADR 0015): each is one model call with fixed instructions and
    a structured output, counted against `budget` (`max_turns` = calls). Independent tasks may run concurrently.

    `run` never raises: a refused or failed call returns a sentence for the strong model instead of a result.
    """

    def __init__(
        self,
        budget: Budget = DEFAULT_LIGHT,
        *,
        settings: AgentSettings | None = None,
        model: Model | None = None,
        reasoning: str | None = "low",
    ):
        self.budget = budget
        self.settings = settings
        self.model = model
        self.reasoning = reasoning
        self.usage = TierUsage()
        self.started = 0  # calls started (reserved before awaiting, so concurrent calls can't overrun the budget)

    def remaining(self) -> int:
        return max(0, self.budget.max_turns - self.started)

    async def run(self, task: str, instructions: str, input: str, output_type: type[T]) -> T | str:
        if self.started >= self.budget.max_turns:
            return f"The light model's budget is used up ({self.budget.max_turns} calls); do this check yourself."
        if self.usage.total_tokens >= self.budget.max_tokens:
            return f"The light model's token budget is used up ({self.budget.max_tokens}); do this check yourself."
        self.started += 1
        effort = ModelSettings(reasoning=Reasoning(effort=self.reasoning)) if self.reasoning else ModelSettings()
        agent = Agent[Any](
            name=f"light:{task}", instructions=instructions, output_type=output_type, model_settings=effort
        )
        try:
            async with asyncio.timeout(self.budget.wall_seconds):
                result = await run_agent(
                    agent, input, max_turns=1, tier="light", settings=self.settings, model=self.model
                )
        except TimeoutError:
            return f"The light model did not answer within {self.budget.wall_seconds:.0f} s."
        except Exception as e:  # SDK/model errors must not end the strong model's run
            log.warning("light task %s failed: %s", task, e)
            return f"The light model failed on this task ({type(e).__name__}); do this check yourself."
        self.usage.add(result.context_wrapper.usage)
        log.info("light task %s: %d light calls, %d tokens so far", task, self.usage.requests, self.usage.total_tokens)
        out = result.final_output
        if not isinstance(out, output_type):
            return "The light model gave no structured answer; do this check yourself."
        return out


class _BudgetHooks(RunHooks[AgentContext]):
    """Enforces the token budget after each model call and logs tool calls. Usage is summed over every `Runner.run`
    of the scenario (a nudge starts a new one) and survives an aborted run."""

    def __init__(self, max_tokens: int):
        self.max_tokens = max_tokens
        self.done: list[Any] = []  # usage of finished runs
        self.current: Any = None

    def total(self, field: str) -> int:
        runs = [*self.done, self.current] if self.current is not None else self.done
        return sum(getattr(u, field, 0) for u in runs)

    def cached(self) -> int:
        runs = [*self.done, self.current] if self.current is not None else self.done
        return sum(_cached(u) for u in runs)

    def next_run(self) -> None:
        if self.current is not None:
            self.done.append(self.current)
            self.current = None

    async def on_llm_end(self, context: RunContextWrapper[AgentContext], agent: Agent[Any], response: ModelResponse):
        self.current = context.usage
        used = self.total("total_tokens")
        log.info("strong model call %d: %d tokens so far", self.total("requests"), used)
        if used > self.max_tokens:
            raise BudgetExceeded(f"{used} tokens used, budget {self.max_tokens}")

    async def on_tool_start(self, context: RunContextWrapper[AgentContext], agent: Agent[Any], tool: Any) -> None:
        log.info("tool %s", tool.name)


def _stop_when_outcome(ctx: RunContextWrapper[AgentContext], results: list[Any]) -> ToolsToFinalOutputResult:
    done = ctx.context.outcome is not None
    return ToolsToFinalOutputResult(is_final_output=done, final_output="done" if done else None)


NUDGE = (
    "You stopped without ending the run. Continue the task and end it by calling exactly one of the tools that "
    "submit a template or report an outcome."
)
MAX_NUDGES = 2


async def run_scenario(
    scenario: Scenario,
    scenario_prompt: str,
    task: str,
    ctx: AgentContext,
    *,
    budget: Budget | TieredBudget = TieredBudget(),
    settings: AgentSettings | None = None,
    reasoning: str | None = "low",
    model: Model | None = None,
    light_model: Model | None = None,
) -> ScenarioRun:
    """Run a scenario's agent until a tool records an outcome or the budget runs out. Never raises on budget limits.

    A model that answers with text instead of a closing tool call is reminded (up to `MAX_NUDGES` times). The strong
    tier runs the scenario; `ctx.light` is set to a `LightWorker` for the `delegate_*` tools. `model` and
    `light_model` replace the configured models (tests); the light one defaults to `model`, as the light tier defaults
    to the strong model.
    """
    tiers = TieredBudget.of(budget)
    budget = tiers.strong
    light = LightWorker(tiers.light, settings=settings, model=light_model or model, reasoning=reasoning)
    ctx.light = light
    agent = Agent[AgentContext](
        name=scenario,
        instructions=instructions(scenario_prompt),
        tools=tools_for(scenario),
        tool_use_behavior=_stop_when_outcome,
        model_settings=ModelSettings(reasoning=Reasoning(effort=reasoning)) if reasoning else ModelSettings(),
    )
    hooks = _BudgetHooks(budget.max_tokens)
    started = time.monotonic()
    stop = "no_outcome"
    final_text = ""
    run_input: str | list[Any] = task
    try:
        async with asyncio.timeout(budget.wall_seconds):
            for attempt in range(MAX_NUDGES + 1):
                turns_left = budget.max_turns - hooks.total("requests")
                if turns_left <= 0:
                    stop = "turns"
                    break
                hooks.next_run()
                result = await run_agent(
                    agent, run_input, max_turns=turns_left, settings=settings, context=ctx, hooks=hooks, model=model
                )
                final_text = str(result.final_output or "")
                if ctx.outcome is not None:
                    break
                log.info("no outcome after run %d; nudging: %s", attempt + 1, final_text[:200])
                run_input = [*result.to_input_list(), {"role": "user", "content": NUDGE}]
    except MaxTurnsExceeded:
        stop = "turns"
    except BudgetExceeded:
        stop = "tokens"
    except TimeoutError:
        stop = "time"
    finally:
        await ctx.aclose()  # the explorer's browser session lives as long as the run
    if ctx.outcome is not None:
        stop = "outcome"
    strong = TierUsage(
        requests=hooks.total("requests"),
        input_tokens=hooks.total("input_tokens"),
        cached_tokens=hooks.cached(),
        output_tokens=hooks.total("output_tokens"),
    )
    by_tier: dict[Tier, TierUsage] = {"strong": strong, "light": light.usage}
    _meter(scenario, by_tier, settings=settings, model=model, light_model=light_model or model)
    return ScenarioRun(
        outcome=ctx.outcome,
        stop_reason=stop,
        requests=strong.requests + light.usage.requests,
        input_tokens=strong.input_tokens + light.usage.input_tokens,
        cached_tokens=strong.cached_tokens + light.usage.cached_tokens,
        output_tokens=strong.output_tokens + light.usage.output_tokens,
        seconds=time.monotonic() - started,
        final_text=final_text,
        by_tier=by_tier,
    )


def _meter(
    scenario: Scenario,
    by_tier: dict[Tier, TierUsage],
    *,
    settings: AgentSettings | None,
    model: Model | None,
    light_model: Model | None,
) -> None:
    """Report each tier's usage to the active collector (ADR 0017; T-099)."""
    for tier, u in by_tier.items():
        if u.requests <= 0:
            continue
        given = model if tier == "strong" else light_model
        if given is not None:
            name = str(getattr(given, "model", None) or type(given).__name__)
        else:
            try:
                name = (settings or load_settings()).model_for(tier)
            except Exception:  # noqa: BLE001 - metering must never fail a run
                name = "unknown"
        usage.record(
            scenario,
            tier,
            name,
            requests=u.requests,
            input_tokens=u.input_tokens,
            cached_tokens=u.cached_tokens,
            output_tokens=u.output_tokens,
        )


def _cached(usage: Any) -> int:
    details = getattr(usage, "input_tokens_details", None)
    return getattr(details, "cached_tokens", 0) or 0
