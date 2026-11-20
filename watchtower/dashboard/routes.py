"""Operator dashboard (T-018): server-rendered Jinja2 pages with htmx polling. Read-only: it never writes state.

Pages: `GET /` (every portal) and `GET /dashboard/portals/{id}` (one portal's runs, templates and tenders). The
`/dashboard/partials/...` routes return the fragments htmx swaps in on each poll.
"""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from watchtower.core.store import Store
from watchtower.dashboard import views

_HERE = Path(__file__).parent
_STATIC = {"htmx.min.js": "text/javascript", "dashboard.css": "text/css"}

router = APIRouter(include_in_schema=False)

env = Environment(
    loader=FileSystemLoader(_HERE / "templates"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)
env.filters.update(
    ago=views.ago,
    duration=views.duration,
    iso=views.iso,
    safe_href=views.safe_href,
    filesize=views.filesize,
)
env.globals.update(STATE_LABELS=views.STATE_LABELS, STATES=views.STATES)


def _store(request: Request) -> Store:
    return request.app.state.store


def _render(name: str, status_code: int = 200, **context) -> HTMLResponse:
    return HTMLResponse(env.get_template(name).render(**context), status_code=status_code)


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return _render("index.html", ov=await views.overview(_store(request)))


@router.get("/dashboard/partials/portals", response_class=HTMLResponse)
async def portals_partial(request: Request) -> HTMLResponse:
    return _render("_overview.html", ov=await views.overview(_store(request)))


@router.get("/dashboard/portals/{portal_id}", response_class=HTMLResponse)
async def portal_page(portal_id: str, request: Request) -> HTMLResponse:
    detail = await views.portal_detail(_store(request), portal_id)
    if detail is None:
        return _render("not_found.html", status_code=404, portal_id=portal_id)
    return _render("portal.html", d=detail)


@router.get("/dashboard/partials/portals/{portal_id}/live", response_class=HTMLResponse)
async def portal_live_partial(portal_id: str, request: Request) -> HTMLResponse:
    detail = await views.portal_detail(_store(request), portal_id, with_tenders=False)
    if detail is None:
        raise HTTPException(status_code=404)
    return _render("_portal_live.html", d=detail)


@router.get("/dashboard/partials/portals/{portal_id}/data", response_class=HTMLResponse)
async def portal_data_partial(portal_id: str, request: Request) -> HTMLResponse:
    detail = await views.portal_detail(_store(request), portal_id)
    if detail is None:
        raise HTTPException(status_code=404)
    return _render("_portal_data.html", d=detail)


@router.get("/dashboard/portals/{portal_id}/templates/{version}.json")
async def template_json(portal_id: str, version: int, request: Request) -> JSONResponse:
    for tv in await _store(request).list_template_versions(portal_id):
        if tv.version == version:
            return JSONResponse(json.loads(json.dumps(tv.template, default=str)))
    raise HTTPException(status_code=404, detail="No such template version.")


@router.get("/dashboard/static/{name}")
async def static(name: str) -> FileResponse:
    if name not in _STATIC:
        raise HTTPException(status_code=404)
    return FileResponse(_HERE / "static" / name, media_type=_STATIC[name], headers={"Cache-Control": "max-age=3600"})
