"""Stand-ins for the agent scenarios until T-015 (`generate_template`) and T-016 (`investigate`) merge.

They make no model call. The pipeline uses them by default (`core.pipeline.configure`); the real scenarios replace
them in `app.py`.
"""

from __future__ import annotations

from watchtower.agent.outcomes import AttachOutcome, Diagnosis, Healthy, PortalView, Transient, Trigger, Unreadable
from watchtower.engine.result import ReadResult
from watchtower.engine.template import Template
from watchtower.validate import Health


async def generate_template(portal_url: str) -> AttachOutcome:
    return Unreadable(reason="automatic attaching is not available yet")


async def investigate(
    portal: PortalView, template: Template, result: ReadResult, health: Health, *, trigger: Trigger
) -> Diagnosis:
    # Without a model the only repair is "try again": the pipeline re-reads and keeps the template if that passes.
    if trigger == "audit":
        return Healthy(notes="no audit agent yet")
    return Transient(reason="assumed temporary; no repair agent yet")
