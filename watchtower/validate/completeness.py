"""Completeness: is this read the portal's whole list of open tenders? (T-036, ADR 0018.)

Pure checks on the engine's evidence (`ReadResult.declared_total`, `scope_dropped`, `more_offered`, `other_notices`),
run by `validate.check`. Any problem fails the read like every other check, so a partial read is never stored and a
template that reads only part of a portal is never promoted to `serving`.

Reads stopped at the caller's `limit` (the agent's dry runs) are not judged on completeness.
"""

from __future__ import annotations

from watchtower.engine.result import ReadResult

TOTAL_TOLERANCE = 0.05  # reached at least 95 % of the portal's own count (T-030 acceptance)
OTHER_NOTICES_MIN = 2
OTHER_NOTICES_RATIO = 0.1


def completeness_problems(result: ReadResult) -> list[str]:
    if result.stats.limited:
        return []
    return [*_declared_total(result), *_more_offered(result), *_other_notices(result)]


def _declared_total(result: ReadResult) -> list[str]:
    declared = result.declared_total
    if not declared:
        return []
    delivered = len(result.tenders)
    reached = delivered + result.scope_dropped
    if reached >= declared * (1 - TOTAL_TOLERANCE):
        return []
    detail = (
        f" ({delivered} delivered, {result.scope_dropped} outside the template's scope)" if result.scope_dropped else ""
    )
    return [
        f"The portal says its list holds {declared}, but the read reached only {reached}{detail}. "
        "Tenders are being missed."
    ]


def _more_offered(result: ReadResult) -> list[str]:
    if not result.more_offered or not result.tenders:
        return []
    n = len(result.tenders)
    return [
        f"The read took {n} tender{'s' if n != 1 else ''} from a single list page, but that page offers more "
        f"({'; '.join(result.more_offered)}). It is probably a short excerpt such as a 'latest tenders' box, "
        "not the full list."
    ]


def _other_notices(result: ReadResult) -> list[str]:
    other = result.other_notices
    n = len(result.tenders)
    if len(other) < max(OTHER_NOTICES_MIN, OTHER_NOTICES_RATIO * n):
        return []
    tid, label = next(iter(other.items()))
    return [
        f"{len(other)} of {n} tenders are other notice types, not calls for competition "
        f"(e.g. {tid}: {label!r}). The list's notice types need filtering."
    ]
