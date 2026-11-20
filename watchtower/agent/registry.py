"""Tool registry: scenarios ask for their tools by scenario name, so new tools (T-022) are added without editing them.

    from watchtower.agent.registry import tool

    @tool(scenarios=ATTACH_AND_INVESTIGATE)
    async def find_pagination(ctx: RunContextWrapper[AgentContext], url: str) -> str:
        '''Docstring = the description the model sees.'''

Put the module in `watchtower/agent/tools/`; every module there is imported by `load_tools()`. A run ends
as soon as a tool records `ctx.outcome` (a report or an accepted template); a refused call lets it continue.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from agents import FunctionTool, function_tool

Scenario = str  # "attach" | "investigate"
ATTACH = frozenset({"attach"})
INVESTIGATE = frozenset({"investigate"})
ATTACH_AND_INVESTIGATE = ATTACH | INVESTIGATE


@dataclass(frozen=True)
class Registered:
    tool: FunctionTool
    scenarios: frozenset[Scenario]


_TOOLS: dict[str, Registered] = {}


def tool(*, scenarios: Iterable[Scenario] = ATTACH_AND_INVESTIGATE) -> Callable[[Callable[..., Any]], FunctionTool]:
    def decorator(fn: Callable[..., Any]) -> FunctionTool:
        ft = function_tool(fn)
        if ft.name in _TOOLS:
            raise ValueError(f"tool {ft.name!r} is already registered")
        _TOOLS[ft.name] = Registered(ft, frozenset(scenarios))
        return ft

    return decorator


def load_tools() -> None:
    """Import every module in `watchtower.agent.tools` so their `@tool`s register (idempotent)."""
    import watchtower.agent.tools as pkg

    for mod in pkgutil.iter_modules(pkg.__path__):
        importlib.import_module(f"{pkg.__name__}.{mod.name}")


def tools_for(scenario: Scenario) -> list[FunctionTool]:
    load_tools()
    return [r.tool for r in _TOOLS.values() if scenario in r.scenarios]
