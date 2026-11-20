"""The single place where model calls happen (ADR 0002: `agent` is the only package that calls a model).

Scenarios build an `Agent` without a model and run it through `run_agent`, which applies the configured model and key.
Keeping every call here is what lets T-099 meter usage later without touching scenario code.
"""

from typing import Any

from agents import Agent, OpenAIProvider, RunConfig, Runner, RunResult

from watchtower.agent.config import AgentSettings, load_settings


async def run_agent(
    agent: Agent[Any],
    input: str,
    *,
    max_turns: int,
    settings: AgentSettings | None = None,
) -> RunResult:
    """Run `agent` on `input` with the configured model; raises the SDK's errors (e.g. `MaxTurnsExceeded`)."""
    settings = settings or load_settings()
    run_config = RunConfig(
        model=settings.model,
        model_provider=OpenAIProvider(api_key=settings.api_key),
    )
    return await Runner.run(agent, input, max_turns=max_turns, run_config=run_config)
