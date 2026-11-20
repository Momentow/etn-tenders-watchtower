"""What the two agent scenarios return to the read pipeline (ADR 0002 interfaces, fixed by ADR 0009).

Plain data only: the pipeline (`core.pipeline`) validates every template before it is used, and only the state
machine turns an outcome into a portal state and reason. `reason`/`notes` are short plain-language phrases; they end up
in the portal's `reason` sentence.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel

from watchtower.engine.result import ReadResult
from watchtower.engine.template import Template
from watchtower.validate import Health

# ------------------------------------------------------------------ generate_template (attach)


class TemplateFound(BaseModel):
    kind: Literal["template_found"] = "template_found"
    template: Template
    notes: str = ""


class Blocked(BaseModel):
    """A login or captcha stands between us and the tenders (no logins, no captcha solving)."""

    kind: Literal["blocked"] = "blocked"
    reason: str


class Unreadable(BaseModel):
    kind: Literal["unreadable"] = "unreadable"
    reason: str


AttachOutcome = TemplateFound | Blocked | Unreadable

# ------------------------------------------------------------------ investigate (notice → repair, audits)


class Healthy(BaseModel):
    """Only for `trigger="audit"`: the engine output matches the live portal."""

    kind: Literal["healthy"] = "healthy"
    notes: str = ""


class Transient(BaseModel):
    """A temporary error (outage, timeout): the current template is still right. The pipeline re-reads to confirm."""

    kind: Literal["transient"] = "transient"
    reason: str


class Changed(BaseModel):
    """The portal changed; `new_template` reads it again. Promoted only after it passes validation."""

    kind: Literal["changed"] = "changed"
    new_template: Template
    reason: str


class Gone(BaseModel):
    kind: Literal["gone"] = "gone"
    reason: str


class Unrepairable(BaseModel):
    kind: Literal["unrepairable"] = "unrepairable"
    reason: str


Diagnosis = Healthy | Transient | Changed | Blocked | Gone | Unrepairable
Trigger = Literal["failure", "audit"]


class PortalView(BaseModel):
    """What the agent may know about the portal it investigates (read-only; the agent never touches the DB)."""

    id: str
    url: str
    state: str
    template_version: int | None
    last_good_snapshot: dict[str, Any] | None = None  # list-page structure from the last good read (repair diff)
    recent_counts: list[int] = []  # tender counts of recent good reads, newest first


# ------------------------------------------------------------------ the scenario signatures the pipeline calls


class GenerateTemplate(Protocol):
    async def __call__(self, portal_url: str) -> AttachOutcome: ...


class Investigate(Protocol):
    async def __call__(
        self, portal: PortalView, template: Template, result: ReadResult, health: Health, *, trigger: Trigger
    ) -> Diagnosis: ...
