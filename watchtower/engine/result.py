"""What one engine run returns (ADR 0002 `ReadResult`): plain data for validate, the pipeline and the agent."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

TenderStatus = Literal["open", "closed", "withdrawn"]

# Contract fields (api-contract.md → Tender) the engine fills. `documents` and `lastChangedAt` are the pipeline's.
REQUIRED_FIELDS = ("id", "url", "title", "buyerName", "publicationDate", "submissionDeadline", "status")
OPTIONAL_FIELDS = (
    "referenceNumber",
    "description",
    "procedureType",
    "contractNature",
    "cpvCodes",
    "placeOfPerformance",
)


class DocumentRef(BaseModel):
    """A document to download: the file URL (after any `per_link` hops) and the portal's name for it, if shown."""

    url: str
    fileName: str | None = None  # noqa: N815 - contract name


class TenderRecord(BaseModel):
    """One tender, normalized and contract-shaped. Required fields the portal doesn't show are None."""

    id: str
    url: str | None
    title: str | None
    buyerName: str | None  # noqa: N815
    publicationDate: str | None  # noqa: N815 - ISO date, e.g. 2026-11-20
    submissionDeadline: str | None  # noqa: N815 - ISO 8601 date-time with offset
    status: TenderStatus = "open"
    referenceNumber: str | None = None  # noqa: N815
    description: str | None = None
    procedureType: str | None = None  # noqa: N815
    contractNature: str | None = None  # noqa: N815
    cpvCodes: list[str] | None = None  # noqa: N815
    placeOfPerformance: dict[str, str] | None = None  # noqa: N815
    documents: list[DocumentRef] = []
    complete: bool = True  # False: a detail page or document hop failed, so some fields/documents are missing

    def contract_data(self) -> dict[str, Any]:
        """Contract fields without `documents`: required ones always (None if not shown), optional ones if shown."""
        out: dict[str, Any] = {k: getattr(self, k) for k in REQUIRED_FIELDS}
        out |= {k: v for k in OPTIONAL_FIELDS if (v := getattr(self, k)) is not None}
        return out


class ReadStats(BaseModel):
    list_pages: int = 0
    detail_pages: int = 0  # detail fetches, `resolve` hops included
    other_pages: int = 0  # navigation steps and document `per_link` hops
    requests: int = 0  # requests the engine made (redirect hops and retries not counted)
    duplicate_ids: int = 0  # list items whose id was already seen in this read
    skipped_items: int = 0  # list items without an id
    hit_max_pages: bool = False  # pagination stopped at `max_pages` with pages possibly left
    limited: bool = False  # the read stopped at the caller's `limit` (a dry run): not every tender was read
    list_seconds: float = 0.0
    detail_seconds: float = 0.0
    total_seconds: float = 0.0


class ReadResult(BaseModel):
    tenders: list[TenderRecord] = []
    errors: list[str] = []  # plain sentences; any error means the read is not trustworthy
    stats: ReadStats = Field(default_factory=ReadStats)
    snapshot: dict[str, Any] | None = None  # structure of the first list page (snapshot.py), for repair diffs
    # v2 templates (ADR 0014), for completeness checks (T-036):
    declared_total: int | None = None  # the portal's own count of what the list holds (`list.total`)
    scope_dropped: int = 0  # records read but not open tenders in the template's `scope` (never returned)
    # Completeness evidence (engine/completeness.py, T-036), checked by `validate.check`:
    more_offered: list[str] = []  # a list read without pagination: the page's controls/counts showing there's more
    other_notices: dict[str, str] = {}  # delivered tender id → the label marking it as another notice type

    @property
    def ok(self) -> bool:
        return not self.errors
