"""Portal state machine: the only code that changes a portal's `state` / `reason` (ADR 0002 → Portal lifecycle).

Callers (the pipeline, the scheduler) report *what happened* as an event; this module decides the new state, writes a
plain-language reason, and refuses transitions the lifecycle doesn't allow.
"""

from typing import Literal

from watchtower.core.models import Portal, PortalState
from watchtower.core.store import Store

Event = Literal[
    "attach_succeeded",  # integrating → serving
    "attach_failed",  # integrating → failed   (Blocked / Unreadable / budget exhausted)
    "read_succeeded",  # serving → serving
    "read_failed",  # serving → healing       (exception or failed validation)
    "audit_healthy",  # serving → serving
    "audit_found_problem",  # serving → healing
    "repair_succeeded",  # healing → serving   (Transient re-read ok, or Changed template promoted)
    "repair_failed",  # healing → failed       (Blocked / Gone / Unrepairable / budget exhausted)
    "reattach",  # failed → integrating        (refresh on a failed portal)
]

TRANSITIONS: dict[tuple[PortalState, Event], PortalState] = {
    ("integrating", "attach_succeeded"): "serving",
    ("integrating", "attach_failed"): "failed",
    ("serving", "read_succeeded"): "serving",
    ("serving", "read_failed"): "healing",
    ("serving", "audit_healthy"): "serving",
    ("serving", "audit_found_problem"): "healing",
    ("healing", "repair_succeeded"): "serving",
    ("healing", "repair_failed"): "failed",
    ("failed", "reattach"): "integrating",
}

_REASONS: dict[Event, str] = {
    "attach_succeeded": "Attached: delivering {count}.",
    "attach_failed": "Could not attach: {detail}.",
    "read_succeeded": "Delivering {count}.",
    "read_failed": "The last read failed: {detail}. Repairing; the last correct tenders are still served.",
    "audit_healthy": "The scheduled audit found no problems. Delivering {count}.",
    "audit_found_problem": "The scheduled audit found a problem: {detail}. Repairing; the last correct tenders are "
    "still served.",
    "repair_succeeded": "Repaired: {detail}. Delivering {count}.",
    "repair_failed": "Cannot deliver: {detail}. A person is needed.",
    "reattach": "Attaching the portal again: learning how to read it.",
}


class InvalidTransition(RuntimeError):
    pass


class StateMachine:
    def __init__(self, store: Store):
        self.store = store

    async def apply(self, portal_id: str, event: Event, *, detail: str = "", tender_count: int | None = None) -> Portal:
        """Apply `event` to the portal. `detail` is a short plain-language explanation (no trailing period needed)."""
        portal = await self.store.get_portal(portal_id)
        if portal is None:
            raise InvalidTransition(f"portal {portal_id} does not exist or was deleted")
        target = TRANSITIONS.get((portal.state, event))
        if target is None:
            raise InvalidTransition(f"{event} is not allowed while the portal is {portal.state}")
        count = portal.tender_count if tender_count is None else tender_count
        reason = _REASONS[event].format(
            detail=_clean(detail) or "no details", count=f"{count} tender{'' if count == 1 else 's'}"
        )
        return await self.store.set_portal_state(portal_id, target, reason)

    async def start_healing(self, portal_id: str, detail: str, *, found_by_audit: bool = False) -> str:
        """serving → healing, then queue the `investigate` job (a `repair` run). Returns that run's id.

        Call after finishing the failed read or audit run (ADR 0005), so the repair run isn't blocked by it.
        """
        await self.apply(portal_id, "audit_found_problem" if found_by_audit else "read_failed", detail=detail)
        run, _ = await self.store.request_run(
            portal_id, "investigate", trigger="audit_finding" if found_by_audit else "failure"
        )
        return run.id


def _clean(detail: str) -> str:
    return " ".join(detail.split()).rstrip(".")
