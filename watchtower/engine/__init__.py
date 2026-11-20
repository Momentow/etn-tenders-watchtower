"""Engine: templates (ADR 0003) and their executor (`run_template`, ADR 0002). No DB access, no model calls."""

from watchtower.engine.executor import run_template
from watchtower.engine.result import DocumentRef, ReadResult, ReadStats, TenderRecord

__all__ = ["DocumentRef", "ReadResult", "ReadStats", "TenderRecord", "run_template"]
