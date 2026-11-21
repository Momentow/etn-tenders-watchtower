"""Agent settings read from the environment (ADR 0001 env-var table, model tiers in ADR 0015)."""

import os
from dataclasses import dataclass
from typing import Literal

DEFAULT_MODEL = "gpt-5.6-luna"

# strong: runs the scenario (explores, plans, writes templates, judges, decides outcomes).
# light: narrow, repetitive tasks the strong model hands out as `delegate_*` tools.
Tier = Literal["strong", "light"]
TIERS: tuple[Tier, ...] = ("strong", "light")


class AgentConfigError(RuntimeError):
    """The agent cannot run because its configuration is missing or invalid."""


@dataclass(frozen=True)
class AgentSettings:
    api_key: str
    model: str  # the strong model
    light_model: str | None = None  # None: the light tier uses the strong model

    def model_for(self, tier: Tier) -> str:
        return (self.light_model or self.model) if tier == "light" else self.model


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def load_settings() -> AgentSettings:
    """Read `OPENAI_API_KEY` (required) and the model per tier.

    strong: `WATCHTOWER_MODEL_STRONG`, else `WATCHTOWER_MODEL`, else `DEFAULT_MODEL`.
    light: `WATCHTOWER_MODEL_LIGHT`, else the strong model. `WATCHTOWER_MODEL` alone sets both.
    """
    api_key = _env("OPENAI_API_KEY")
    if not api_key:
        raise AgentConfigError("OPENAI_API_KEY is not set")
    strong = _env("WATCHTOWER_MODEL_STRONG") or _env("WATCHTOWER_MODEL") or DEFAULT_MODEL
    light = _env("WATCHTOWER_MODEL_LIGHT") or strong
    return AgentSettings(api_key=api_key, model=strong, light_model=light)
