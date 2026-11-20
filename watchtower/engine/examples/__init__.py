"""Example templates, one per mock-portal kind (ADR 0003). Used by tests, the engine (T-009) and the agent's prompts."""

import json
from pathlib import Path

from watchtower.engine.template import Template

_DIR = Path(__file__).parent

EXAMPLES = sorted(p.stem for p in _DIR.glob("*.json"))


def load_example(name: str) -> Template:
    return Template.model_validate(json.loads((_DIR / f"{name}.json").read_text(encoding="utf-8")))
