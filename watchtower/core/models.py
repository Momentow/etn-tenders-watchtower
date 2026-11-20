"""Records returned by `core.store`. Field names are internal (snake_case); the API maps them to the contract."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

PortalState = Literal["integrating", "serving", "healing", "failed"]
RunKind = Literal["read", "repair"]
RunResult = Literal["ok", "failed"]
TenderStatus = Literal["open", "closed", "withdrawn"]
JobKind = Literal["attach", "read", "investigate", "download"]
JobStatus = Literal["queued", "running", "done", "failed", "cancelled"]


class Portal(BaseModel):
    id: str
    url: str  # as first submitted
    normalized_url: str
    state: PortalState
    reason: str
    tender_count: int
    last_success_at: datetime | None
    current_template_version: int | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class Run(BaseModel):
    id: str
    portal_id: str
    kind: RunKind
    started_at: datetime
    finished_at: datetime | None
    result: RunResult | None
    template_version: int | None
    tender_count: int | None  # tenders the read returned; feeds the "sharp drop" validator
    details: dict[str, Any]  # internal: trigger (attach/scheduled/refresh/audit), problems, stats, diagnosis…
    snapshot: Any | None  # structural snapshot of the list page, kept for good reads (repair diff)


class TemplateVersion(BaseModel):
    portal_id: str
    version: int
    template: dict[str, Any]
    notes: str
    created_at: datetime
    promoted_at: datetime | None


class DocumentFile(BaseModel):
    """One stored file of a tender (a ZIP yields one row per unpacked member)."""

    id: str
    portal_id: str
    tender_id: str
    source_url: str
    archive_member: str  # "" unless unpacked from a ZIP
    file_name: str
    size_bytes: int
    sha256: str
    created_at: datetime

    @property
    def path(self) -> str:
        return f"/files/{self.sha256}"


class StoredTender(BaseModel):
    portal_id: str
    tender_id: str  # the portal's own identifier (contract `id`)
    data: dict[str, Any]  # contract fields as the engine produced them (url, title, buyerName, …)
    content_hash: str
    status: TenderStatus
    first_seen_at: datetime
    last_seen_at: datetime
    last_changed_at: datetime
    documents: list[DocumentFile] = []


class TenderUpsert(BaseModel):
    """Input to `Store.upsert_tenders`: one tender from a validated read."""

    tender_id: str
    data: dict[str, Any]
    content_hash: str
    status: TenderStatus = "open"


class UpsertSummary(BaseModel):
    new: list[str]
    changed: list[str]
    unchanged: list[str]


class Job(BaseModel):
    id: int
    kind: JobKind
    portal_id: str | None
    payload: dict[str, Any]
    status: JobStatus
    attempts: int
    run_after: datetime
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    run_id: str | None
