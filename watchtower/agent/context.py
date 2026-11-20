"""State shared by the tools of one scenario run (the Agents SDK run context)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from watchtower.agent.outcomes import Blocked, Gone, Healthy, TemplateFound, Transient, Unreadable, Unrepairable
from watchtower.engine.template import Template
from watchtower.net import BrowserPool, PoliteClient
from watchtower.validate import PortalHistory

# What a closing tool records; each scenario maps it onto its own outcome type (ADR 0009).
Outcome = TemplateFound | Blocked | Unreadable | Transient | Healthy | Gone | Unrepairable


@dataclass
class Fetched:
    url: str  # final URL after redirects
    status: int | None
    content_type: str
    text: str
    responses: list[Any] = field(default_factory=list)  # CapturedResponse, for rendered pages


@dataclass
class AgentContext:
    portal_url: str
    client: PoliteClient
    browser: BrowserPool | None = None
    history: PortalHistory = field(default_factory=PortalHistory)
    # Investigate: the template in use and the list-page structure of the last good read (repair diffs, T-022).
    template: Template | None = None
    last_good_snapshot: dict[str, Any] | None = None
    # Templates of other portals by software fingerprint, for reuse (`find_template_for_software`).
    known_templates: dict[str, Template] = field(default_factory=dict)
    # Pages already fetched in this run, by requested URL: a second look (another `focus`) costs no request.
    pages: dict[str, Fetched] = field(default_factory=dict)
    # Canonical template JSON → whether its last dry run was good enough to submit.
    tried: dict[str, bool] = field(default_factory=dict)
    outcome: Outcome | None = None
    # Free-form per-scenario data (T-016 puts the failing read and its health report here).
    extra: dict[str, Any] = field(default_factory=dict)
