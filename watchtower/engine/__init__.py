"""Engine: templates (ADR 0003, v2: ADR 0014) and their executor (`run_template`, ADR 0002). No DB access, no model
calls."""

from watchtower.engine.executor import run_template
from watchtower.engine.result import DocumentRef, ReadResult, ReadStats, TenderRecord
from watchtower.engine.template_v2 import AnyTemplate, TemplateV2, load_template

__all__ = [
    "AnyTemplate",
    "DocumentRef",
    "ReadResult",
    "ReadStats",
    "TemplateV2",
    "TenderRecord",
    "load_template",
    "run_template",
]
