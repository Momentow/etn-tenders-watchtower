"""HTTP API exactly per docs/spec/api-contract.md. No business logic: reads and requests go through `core.store`.

Work is requested with `Store.request_run` (ADR 0005); the scheduler's workers (T-006) do it.
"""

import mimetypes
import re
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from watchtower.api.serialize import portal_entry, run_entry, tender_entry
from watchtower.core.models import Portal
from watchtower.core.store import Store
from watchtower.documents.storage import files_dir

router = APIRouter()

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class NewPortal(BaseModel):
    url: str


def get_store(request: Request) -> Store:
    return request.app.state.store


StoreDep = Annotated[Store, Depends(get_store)]


async def _portal_or_404(store: Store, portal_id: str) -> Portal:
    portal = await store.get_portal(portal_id)
    if portal is None:
        raise HTTPException(status_code=404, detail=f"No portal with id {portal_id!r}.")
    return portal


@router.post("/portals", status_code=201)
async def add_portal(body: NewPortal, store: StoreDep) -> dict:
    try:
        portal, created = await store.add_portal(body.url)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"Not a usable portal address: {e}") from e
    if created:
        await store.request_run(portal.id, "attach", trigger="attach")
    return {"id": portal.id}


@router.get("/portals")
async def list_portals(store: StoreDep) -> list[dict]:
    return [portal_entry(p) for p in await store.list_portals()]


@router.get("/portals/{portal_id}")
async def get_portal(portal_id: str, store: StoreDep) -> dict:
    return portal_entry(await _portal_or_404(store, portal_id))


@router.delete("/portals/{portal_id}", status_code=204)
async def delete_portal(portal_id: str, store: StoreDep) -> Response:
    if not await store.delete_portal(portal_id):
        raise HTTPException(status_code=404, detail=f"No portal with id {portal_id!r}.")
    return Response(status_code=204)


@router.post("/portals/{portal_id}/refresh", status_code=202)
async def refresh_portal(portal_id: str, store: StoreDep) -> dict:
    portal = await _portal_or_404(store, portal_id)
    # A failed portal starts over with a fresh attach (ADR 0002 lifecycle); otherwise a read.
    # If any read or repair is already running, request_run returns that run instead of queueing more work.
    job_kind = "attach" if portal.state == "failed" else "read"
    run, _ = await store.request_run(portal.id, job_kind, trigger="refresh")
    return {"runId": run.id}


@router.get("/portals/{portal_id}/tenders")
async def list_tenders(
    portal_id: str,
    store: StoreDep,
    changed_since: Annotated[str | None, Query(alias="changedSince")] = None,
) -> list[dict]:
    await _portal_or_404(store, portal_id)
    since = _parse_since(changed_since) if changed_since is not None else None
    return [tender_entry(t) for t in await store.list_tenders(portal_id, changed_since=since)]


@router.get("/portals/{portal_id}/runs")
async def list_runs(portal_id: str, store: StoreDep) -> list[dict]:
    await _portal_or_404(store, portal_id)
    return [run_entry(r) for r in await store.list_runs(portal_id)]


@router.get("/files/{file_id}")
async def get_file(file_id: str, store: StoreDep) -> FileResponse:
    record = await store.get_file(file_id) if _SHA256.match(file_id) else None
    path = files_dir() / file_id
    if record is None or not path.is_file():
        raise HTTPException(status_code=404, detail=f"No file with id {file_id!r}.")
    media_type = mimetypes.guess_type(record.file_name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=record.file_name)


def _parse_since(raw: str) -> datetime:
    # An unencoded "+01:00" in a query string arrives as " 01:00"; put the plus back.
    value = raw.strip().replace(" ", "+")
    if value.endswith(("Z", "z")):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail="changedSince must be a date-time with an offset, e.g. 2026-12-09T10:00:00+01:00."
        ) from e
    if parsed.tzinfo is None:
        raise HTTPException(status_code=400, detail="changedSince needs a time-zone offset, e.g. +01:00 or Z.")
    return parsed
