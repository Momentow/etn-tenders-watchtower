"""State shared by the tools of one scenario run (the Agents SDK run context)."""

from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from watchtower.agent.blocks import BlockEvidence
from watchtower.agent.outcomes import Blocked, Gone, Healthy, TemplateFound, Transient, Unreadable, Unrepairable
from watchtower.engine.template import Template
from watchtower.net import BrowserPool, BrowserSession, PoliteClient, SessionBudget
from watchtower.validate import PortalHistory

if TYPE_CHECKING:
    from watchtower.agent.runtime import LightWorker

# The explorer's browser session (ADR 0015): exploring at 1 req/s is slow, so the wall clock matches the attach budget.
EXPLORE_SESSION_BUDGET = SessionBudget(active_seconds=600.0, max_seconds=1800.0)

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
    # Templates of other portals by software fingerprint, for reuse (`find_template_for_software`).
    known_templates: dict[str, Template] = field(default_factory=dict)
    # Pages already fetched in this run, by requested URL: a second look (another `focus`) costs no request.
    pages: dict[str, Fetched] = field(default_factory=dict)
    # Canonical template JSON → whether its last dry run was good enough to submit.
    tried: dict[str, bool] = field(default_factory=dict)
    outcome: Outcome | None = None
    # What pages and document probes showed about logins/captchas (T-039); `report_blocked` reviews it.
    blocks: BlockEvidence = field(default_factory=BlockEvidence)
    # The light tier for `delegate_*` tools (ADR 0015); set by `run_scenario`, None outside a scenario run.
    light: LightWorker | None = None
    # Free-form per-scenario data (T-016 puts the failing read and its health report here).
    extra: dict[str, Any] = field(default_factory=dict)
    # One browser session per run for the explorer tools (T-034): opened on first use, closed by `aclose()`.
    session: BrowserSession | None = None
    _stack: AsyncExitStack | None = None

    async def browser_session(self) -> BrowserSession:
        """The run's browser session, opened on first use. Raises RuntimeError when there is no browser."""
        if self.session is None:
            if self.browser is None:
                raise RuntimeError("no browser is available in this process")
            stack = AsyncExitStack()
            self.session = await stack.enter_async_context(
                self.browser.session(budget=EXPLORE_SESSION_BUDGET, held=True)
            )
            self._stack = stack
        return self.session

    async def aclose(self) -> None:
        """Close the run's browser session, if one was opened (`run_scenario` calls this when the run ends)."""
        stack, self._stack, self.session = self._stack, None, None
        if stack is not None:
            await stack.aclose()
