"""The single place where model calls happen (ADR 0002: `agent` is the only package that calls a model).

`run_agent` runs any SDK agent with the configured model and key. `run_scenario` runs one scenario (attach or
investigate) with its registered tools and a `Budget` (turns, tokens, wall time), and returns the outcome a tool
recorded. Keeping every call here is what lets T-099 meter usage later without touching scenario code.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

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

from watchtower.agent.config import AgentSettings, load_settings
from watchtower.agent.context import AgentContext, Outcome
from watchtower.agent.prompts import instructions
from watchtower.agent.registry import Scenario, tools_for

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Budget:
    max_turns: int = 30  # model calls
    max_tokens: int = 400_000  # input + output, summed over all calls
    wall_seconds: float = 900.0


@dataclass
class ScenarioRun:
    outcome: Outcome | None  # None: the budget ran out or the model stopped without a report
    stop_reason: str  # "outcome" | "turns" | "tokens" | "time" | "no_outcome"
    requests: int
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    seconds: float
    final_text: str = ""


class BudgetExceeded(Exception):
    pass


async def run_agent(
    agent: Agent[Any],
    input: str,
    *,
    max_turns: int,
    settings: AgentSettings | None = None,
    context: Any = None,
    hooks: RunHooks[Any] | None = None,
    model: Model | None = None,
) -> RunResult:
    """Run `agent` on `input` with the configured model; raises the SDK's errors (e.g. `MaxTurnsExceeded`).

    `model` replaces the configured one (tests pass a scripted model; no key needed then).
    """
    if model is not None:
        run_config = RunConfig(model=model)
    else:
        settings = settings or load_settings()
        run_config = RunConfig(model=settings.model, model_provider=OpenAIProvider(api_key=settings.api_key))
    return await Runner.run(agent, input, max_turns=max_turns, run_config=run_config, context=context, hooks=hooks)


class _BudgetHooks(RunHooks[AgentContext]):
    """Enforces the token budget after each model call, logs tool calls, and keeps the run's usage (it survives an
    aborted run)."""

    def __init__(self, max_tokens: int):
        self.max_tokens = max_tokens
        self.usage: Any = None

    async def on_llm_end(self, context: RunContextWrapper[AgentContext], agent: Agent[Any], response: ModelResponse):
        self.usage = context.usage
        used = context.usage.total_tokens
        log.info("model call %d: %d tokens so far", context.usage.requests, used)
        if used > self.max_tokens:
            raise BudgetExceeded(f"{used} tokens used, budget {self.max_tokens}")

    async def on_tool_start(self, context: RunContextWrapper[AgentContext], agent: Agent[Any], tool: Any) -> None:
        log.info("tool %s", tool.name)


def _stop_when_outcome(ctx: RunContextWrapper[AgentContext], results: list[Any]) -> ToolsToFinalOutputResult:
    done = ctx.context.outcome is not None
    return ToolsToFinalOutputResult(is_final_output=done, final_output="done" if done else None)


async def run_scenario(
    scenario: Scenario,
    scenario_prompt: str,
    task: str,
    ctx: AgentContext,
    *,
    budget: Budget = Budget(),
    settings: AgentSettings | None = None,
    reasoning: str | None = "low",
    model: Model | None = None,
) -> ScenarioRun:
    """Run a scenario's agent until a tool records an outcome or the budget runs out. Never raises on budget limits."""
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
    try:
        async with asyncio.timeout(budget.wall_seconds):
            result = await run_agent(
                agent, task, max_turns=budget.max_turns, settings=settings, context=ctx, hooks=hooks, model=model
            )
        final_text = str(result.final_output or "")
    except MaxTurnsExceeded:
        stop = "turns"
    except BudgetExceeded:
        stop = "tokens"
    except TimeoutError:
        stop = "time"
    if ctx.outcome is not None:
        stop = "outcome"
    usage = hooks.usage
    return ScenarioRun(
        outcome=ctx.outcome,
        stop_reason=stop,
        requests=getattr(usage, "requests", 0),
        input_tokens=getattr(usage, "input_tokens", 0),
        cached_tokens=_cached(usage),
        output_tokens=getattr(usage, "output_tokens", 0),
        seconds=time.monotonic() - started,
        final_text=final_text,
    )


def _cached(usage: Any) -> int:
    details = getattr(usage, "input_tokens_details", None)
    return getattr(details, "cached_tokens", 0) or 0
