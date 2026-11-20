"""Agent settings read from the environment (ADR 0001 env-var table)."""

import os
from dataclasses import dataclass

DEFAULT_MODEL = "gpt-5.6-luna"


class AgentConfigError(RuntimeError):
    """The agent cannot run because its configuration is missing or invalid."""


@dataclass(frozen=True)
class AgentSettings:
    api_key: str
    model: str


def load_settings() -> AgentSettings:
    """Read `OPENAI_API_KEY` (required) and `WATCHTOWER_MODEL` (optional)."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise AgentConfigError("OPENAI_API_KEY is not set")
    model = os.environ.get("WATCHTOWER_MODEL", "").strip() or DEFAULT_MODEL
    return AgentSettings(api_key=api_key, model=model)
