"""Example templates, one per mock-portal kind (ADR 0003). Used by tests, the engine (T-009) and the agent's prompts.

`v2/` holds format-v2 examples (ADR 0014): the `app` mock portal read through its API and through a click-through
browser flow, and a hand-written template for tenderned.nl.
"""

import json
from pathlib import Path

from watchtower.engine.template import Template
from watchtower.engine.template_v2 import TemplateV2

_DIR = Path(__file__).parent
_DIR_V2 = _DIR / "v2"

EXAMPLES = sorted(p.stem for p in _DIR.glob("*.json"))
EXAMPLES_V2 = sorted(p.stem for p in _DIR_V2.glob("*.json"))


def load_example(name: str) -> Template:
    return Template.model_validate(json.loads((_DIR / f"{name}.json").read_text(encoding="utf-8")))


def load_example_v2(name: str) -> TemplateV2:
    return TemplateV2.model_validate(json.loads((_DIR_V2 / f"{name}.json").read_text(encoding="utf-8")))
