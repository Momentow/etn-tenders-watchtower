"""Store records → contract JSON (docs/spec/api-contract.md). The only place the contract's field names are produced."""

from datetime import datetime
from typing import Any

from watchtower.core.models import DocumentFile, Portal, Run, StoredTender

# Required tender fields are always present (null when the portal doesn't show them);
# if-shown fields are left out when missing.
REQUIRED_TENDER_FIELDS = ("url", "title", "buyerName", "publicationDate", "submissionDeadline")
OPTIONAL_TENDER_FIELDS = (
    "referenceNumber",
    "description",
    "procedureType",
    "contractNature",
    "cpvCodes",
    "placeOfPerformance",
)


def timestamp(value: datetime | None) -> str | None:
    """Date-time with an explicit offset, e.g. 2026-11-20T10:00:00+00:00."""
    return None if value is None else value.isoformat(timespec="seconds")


def portal_entry(p: Portal) -> dict[str, Any]:
    return {
        "id": p.id,
        "url": p.url,
        "state": p.state,
        "reason": p.reason,
        "tenderCount": p.tender_count,
        "lastSuccessAt": timestamp(p.last_success_at),
    }


def run_entry(r: Run) -> dict[str, Any]:
    return {
        "id": r.id,
        "kind": r.kind,
        "startedAt": timestamp(r.started_at),
        "finishedAt": timestamp(r.finished_at),
        "result": r.result,
    }


def document_entry(d: DocumentFile) -> dict[str, Any]:
    return {"fileName": d.file_name, "sizeBytes": d.size_bytes, "sha256": d.sha256, "path": d.path}


def tender_entry(t: StoredTender) -> dict[str, Any]:
    data = t.data
    out: dict[str, Any] = {"id": t.tender_id}
    for name in REQUIRED_TENDER_FIELDS:
        out[name] = data.get(name)
    out["status"] = t.status
    out["documents"] = [document_entry(d) for d in t.documents]
    out["lastChangedAt"] = timestamp(t.last_changed_at)
    for name in OPTIONAL_TENDER_FIELDS:
        value = data.get(name)
        if value is None or value == [] or value == {}:
            continue
        if name == "placeOfPerformance":
            value = {k: v for k, v in value.items() if k in ("postalCode", "city", "countryCode") and v is not None}
            if not value:
                continue
        out[name] = value
    return out
