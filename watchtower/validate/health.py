"""`check(result, history)`: is this read trustworthy? The Notice signal (ADR 0002, ADR 0008).

Pure and deterministic: no network, no DB, no model. `history` is built by the pipeline from the store
(`PortalHistory.from_store`). Every problem is one plain sentence: problems become the portal's `reason` and show up
on the dashboard, so they say what is wrong, with numbers, not how the check works.

A read with any problem is not stored: the portal goes to `healing` and the last correct tenders keep being served.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from statistics import median
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from watchtower.engine.result import ReadResult, TenderRecord

if TYPE_CHECKING:  # validate must not import core at runtime (core's pipeline imports validate)
    from watchtower.core.models import Run, StoredTender

# Required contract fields whose sudden disappearance means the template no longer reads the portal.
WATCHED_FIELDS = ("url", "title", "buyerName", "publicationDate", "submissionDeadline")
RECENT_READS = 5
# Sharp drop: fewer than DROP_RATIO × the usual count, and at least DROP_MIN_LOST tenders fewer.
DROP_RATIO = 0.6
DROP_MIN_LOST = 3
# A watched field filled on ≥ FILL_WAS of tenders before, now on ≤ FILL_WAS_FACTOR of that share.
FILL_WAS = 0.5
FILL_NOW_FACTOR = 0.5
# Without history, a read where most tenders lack a title has the wrong selectors.
FIRST_READ_MIN_TITLE_FILL = 0.5
# Pagination repeating pages: duplicate ids ≥ max(DUP_MIN, DUP_RATIO × tenders read).
DUP_MIN = 3
DUP_RATIO = 0.5
# Deadlines jumping (ADR 0007: a portal silently switching date order): ≥ JUMP_MIN_OVERLAP known tenders, more than
# JUMP_RATIO of them with a different deadline.
JUMP_MIN_OVERLAP = 3
JUMP_RATIO = 0.5
MAX_LISTED = 3  # ids/errors quoted in one sentence
FUTURE_PUBLICATION_SLACK = timedelta(days=1)


class Health(BaseModel):
    ok: bool
    problems: list[str] = []


class PortalHistory(BaseModel):
    """What the portal looked like on recent good reads. Empty for the first read (attach)."""

    recent_counts: list[int] = []  # tender counts of the latest good reads, newest first
    field_fill: dict[str, float] = {}  # share of the served tenders with each watched field filled (0..1)
    deadlines: dict[str, str] = {}  # tender id → the deadline we serve now

    @property
    def is_empty(self) -> bool:
        return not self.recent_counts and not self.field_fill

    @classmethod
    def from_store(cls, runs: list[Run], tenders: list[StoredTender]) -> PortalHistory:
        """From `Store.list_runs(portal_id)` and `Store.list_tenders(portal_id)`.

        Good reads are `read` runs with result `ok` and a tender count (audits are recorded as read runs too).
        Field fill and deadlines come from the tenders we currently serve as open.
        """
        good = [r for r in runs if r.kind == "read" and r.result == "ok" and r.tender_count is not None]
        good.sort(key=lambda r: r.started_at, reverse=True)
        served = [t for t in tenders if t.status != "closed"]
        fill = (
            {f: sum(1 for t in served if t.data.get(f) is not None) / len(served) for f in WATCHED_FIELDS}
            if served
            else {}
        )
        return cls(
            recent_counts=[r.tender_count for r in good[:RECENT_READS] if r.tender_count is not None],
            field_fill=fill,
            deadlines={t.tender_id: d for t in served if (d := t.data.get("submissionDeadline"))},
        )


def check(result: ReadResult, history: PortalHistory | None = None, *, today: date | None = None) -> Health:
    history = history or PortalHistory()
    today = today or date.today()
    problems: list[str] = []
    problems += _engine_errors(result)
    problems += _count(result, history)
    if result.tenders:
        problems += _duplicates(result)
        problems += _fields(result.tenders, history)
        problems += _dates(result.tenders, today)
        problems += _deadline_jumps(result.tenders, history)
    return Health(ok=not problems, problems=problems)


# --------------------------------------------------------------------------- checks


def _engine_errors(result: ReadResult) -> list[str]:
    errors = list(dict.fromkeys(result.errors))
    if len(errors) <= MAX_LISTED:
        return errors
    rest = len(errors) - MAX_LISTED
    return [*errors[:MAX_LISTED], f"…and {rest} more read error{'s' if rest > 1 else ''} like these."]


def _count(result: ReadResult, history: PortalHistory) -> list[str]:
    n = len(result.tenders)
    usual = round(median(history.recent_counts)) if history.recent_counts else None
    out: list[str] = []
    if n == 0:
        if usual:
            out.append(f"The read found no tenders; recent reads found {usual}.")
        elif not result.errors:
            out.append("The read found no tenders on the list page.")
    elif usual and n < DROP_RATIO * usual and usual - n >= DROP_MIN_LOST:
        out.append(f"Only {n} tenders were found; recent reads found {usual}. Some tenders are probably being missed.")
    if result.stats.hit_max_pages:
        out.append(
            f"The read stopped at the page limit after {result.stats.list_pages} list pages, so tenders may be missing."
        )
    return out


def _duplicates(result: ReadResult) -> list[str]:
    out: list[str] = []
    counts = Counter(t.id for t in result.tenders)
    if dup := [tid for tid, c in counts.items() if c > 1]:
        out.append(f"{len(dup)} tender id(s) appear more than once ({_ids(dup)}).")
    repeats = result.stats.duplicate_ids
    if repeats >= max(DUP_MIN, DUP_RATIO * len(result.tenders)):
        out.append(
            f"The list pages repeat: {repeats} tenders came back again on later pages. "
            "Pagination is probably not moving to the next page."
        )
    return out


def _fields(tenders: list[TenderRecord], history: PortalHistory) -> list[str]:
    n = len(tenders)
    out: list[str] = []
    for f in WATCHED_FIELDS:
        missing = [t.id for t in tenders if getattr(t, f) is None]
        now = 1 - len(missing) / n
        was = history.field_fill.get(f)
        if was is not None:
            if was >= FILL_WAS and now <= was * FILL_NOW_FACTOR:
                out.append(
                    f"{f} is missing on {len(missing)} of {n} tenders; before, it was shown on {was:.0%} of them."
                )
        elif history.is_empty and f == "title" and now < FIRST_READ_MIN_TITLE_FILL:
            out.append(f"title is missing on {len(missing)} of {n} tenders.")
    return out


def _dates(tenders: list[TenderRecord], today: date) -> list[str]:
    bad_pub: list[str] = []
    future_pub: list[str] = []
    bad_deadline: list[str] = []
    before_pub: list[str] = []
    for t in tenders:
        pub = _date(t.publicationDate)
        if t.publicationDate is not None and pub is None:
            bad_pub.append(t.id)
        elif pub is not None and pub > today + FUTURE_PUBLICATION_SLACK:
            future_pub.append(t.id)
        dl = _datetime(t.submissionDeadline)
        if t.submissionDeadline is not None and dl is None:
            bad_deadline.append(t.id)
        elif dl is not None and pub is not None and dl.date() < pub:
            before_pub.append(t.id)
    out: list[str] = []
    if bad_pub:
        out.append(f"publicationDate is not a valid date on {len(bad_pub)} tender(s) ({_ids(bad_pub)}).")
    if future_pub:
        out.append(f"publicationDate lies in the future on {len(future_pub)} tender(s) ({_ids(future_pub)}).")
    if bad_deadline:
        out.append(
            f"submissionDeadline is not a date-time with an offset on {len(bad_deadline)} tender(s) "
            f"({_ids(bad_deadline)})."
        )
    if before_pub:
        out.append(
            f"submissionDeadline is before publicationDate on {len(before_pub)} tender(s) ({_ids(before_pub)}); "
            "the dates are probably misread."
        )
    return out


def _deadline_jumps(tenders: list[TenderRecord], history: PortalHistory) -> list[str]:
    overlap = [(t.id, t.submissionDeadline, history.deadlines[t.id]) for t in tenders if t.id in history.deadlines]
    overlap = [(tid, new, old) for tid, new, old in overlap if new is not None]
    if len(overlap) < JUMP_MIN_OVERLAP:
        return []
    changed = [tid for tid, new, old in overlap if _datetime(new) != _datetime(old)]
    if len(changed) <= JUMP_RATIO * len(overlap):
        return []
    return [
        f"The deadline changed on {len(changed)} of {len(overlap)} known tenders at once ({_ids(changed)}); "
        "the date format has probably changed."
    ]


# --------------------------------------------------------------------------- helpers


def _ids(ids: list[str]) -> str:
    shown = ", ".join(ids[:MAX_LISTED])
    return shown + (f" and {len(ids) - MAX_LISTED} more" if len(ids) > MAX_LISTED else "")


def _date(v: str | None) -> date | None:
    if v is None:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def _datetime(v: Any) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else None
