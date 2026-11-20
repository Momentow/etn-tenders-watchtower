"""Live smoke test of the model setup: one real call that uses a tool and returns structured output.

    uv run --env-file .env python -m watchtower.agent.smoke

Exits 0 and prints the model, the answer and token usage when the key, the model name, tool calling and structured
output all work; exits 1 with the error otherwise. Costs a few hundred tokens.
"""

import asyncio
import sys
import time

from agents import Agent, function_tool
from pydantic import BaseModel

from watchtower.agent.config import load_settings
from watchtower.agent.runtime import run_agent

_SECRET = "watchtower-7731"


@function_tool
def lookup_code() -> str:
    """Return the verification code the user asks for."""
    return _SECRET


class SmokeAnswer(BaseModel):
    code: str


async def main() -> int:
    try:
        settings = load_settings()
        agent = Agent(
            name="smoke",
            instructions="Call lookup_code exactly once and return the code it gives you.",
            tools=[lookup_code],
            output_type=SmokeAnswer,
        )
        started = time.monotonic()
        result = await run_agent(agent, "What is the verification code?", max_turns=3, settings=settings)
    except Exception as exc:  # report any failure plainly; this is a CLI check
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    usage = result.context_wrapper.usage
    answer = result.final_output
    tool_called = any(item.type == "tool_call_item" for item in result.new_items)
    print(f"model:       {settings.model}")
    print(f"answer:      {answer!r}")
    print(f"tool called: {tool_called}")
    print(f"usage:       {usage.requests} requests, {usage.input_tokens} in / {usage.output_tokens} out tokens")
    print(f"time:        {time.monotonic() - started:.1f}s")
    if not tool_called or not isinstance(answer, SmokeAnswer) or answer.code != _SECRET:
        print("FAIL: the model did not return the tool's code", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
