"""Mock-portal server: every portal kind under one app on port 8081 (see docs/test-portals/mock-portals.md).

Variants change a running portal so Notice and Repair can be tested:
- per request: `POST /_mock/variants/{kind}` with `{"variant": "markup"}`;
- at start: `MOCK_VARIANTS="ssr=markup,js=renamed-keys"`;
- in tests: `create_app({"ssr": "markup"})` or `app.state.variants["ssr"] = "markup"`.
"""

from __future__ import annotations

import os
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

from mockportals import data, files
from mockportals.html import e, page
from mockportals.portals import blocked, captcha, form, js_api, multistep, ssr

# Variants every portal supports, handled here before the portal's own routes.
GENERIC_VARIANTS = ("gone", "login-wall")
VARIANTS: dict[str, tuple[str, ...]] = {
    "ssr": ("base", "markup", "pagination", "silent-missed", "silent-swap", *GENERIC_VARIANTS),
    "js": ("base", "renamed-keys", "pagination", "silent-missed", *GENERIC_VARIANTS),
    "multistep": ("base", "markup", *GENERIC_VARIANTS),
    "form": ("base", "pagination", *GENERIC_VARIANTS),
    "captcha": ("base", *GENERIC_VARIANTS),
    "login": ("base", *GENERIC_VARIANTS),
    "robot": ("base", *GENERIC_VARIANTS),
}

# Entry URL of each portal: what you would `POST /portals` to Watchtower.
ENTRY = {
    "ssr": "/ssr/",
    "js": "/js/",
    "multistep": "/multistep/",
    "form": "/form/",
    "captcha": "/captcha/",
    "login": "/login/",
    "robot": "/robot/",
}
# What a correct system reports for each portal in its base variant.
EXPECTED_STATE = {
    "ssr": "serving",
    "js": "serving",
    "multistep": "serving",
    "form": "serving",
    "captcha": "serving",  # captcha shown, but list and documents work without it
    "login": "failed",  # documents need a login
    "robot": "failed",  # documents behind a robot check (captcha)
}


def _variants_from_env() -> dict[str, str]:
    out = {}
    for part in filter(None, os.environ.get("MOCK_VARIANTS", "").split(",")):
        kind, _, v = part.partition("=")
        out[kind.strip()] = v.strip()
    return out


def _kind_of(path: str) -> str | None:
    parts = path.strip("/").split("/")
    if not parts or not parts[0]:
        return None
    if parts[0] == "files" and len(parts) > 1:
        return "multistep" if parts[1] == "large" else parts[1]
    return parts[0] if parts[0] in VARIANTS else None


def create_app(variants: dict[str, str] | None = None) -> FastAPI:
    app = FastAPI(title="Watchtower mock portals", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.variants = {k: "base" for k in VARIANTS} | _variants_from_env() | (variants or {})
    for kind, v in app.state.variants.items():
        if kind not in VARIANTS or v not in VARIANTS[kind]:
            raise ValueError(f"unknown variant {kind}={v}; known: {VARIANTS.get(kind)}")

    @app.middleware("http")
    async def generic_variants(request: Request, call_next):
        kind = _kind_of(request.url.path)
        v = app.state.variants.get(kind) if kind else None
        if v == "gone":
            return page("Plattform eingestellt", "<h1>Diese Plattform wurde eingestellt.</h1>", status_code=410)
        if v == "login-wall":
            return RedirectResponse(f"/_auth/anmelden?ReturnUrl={quote(request.url.path)}", status_code=302)
        return await call_next(request)

    for module in (ssr, js_api, multistep, form, captcha, blocked):
        app.include_router(module.router)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        rows = "".join(
            f"<tr><td><a href='{ENTRY[k]}'>{k}</a></td><td>{e(app.state.variants[k])}</td>"
            f"<td>{e(', '.join(VARIANTS[k]))}</td><td>{EXPECTED_STATE[k]}</td></tr>"
            for k in VARIANTS
        )
        return page(
            "Mock portals",
            "<h1>Mock portals</h1><table><thead><tr><th>Portal</th><th>Variant</th><th>Variants</th>"
            f"<th>Expected state</th></tr></thead><tbody>{rows}</tbody></table>",
        )

    @app.get("/_auth/anmelden", response_class=HTMLResponse)
    async def login_page(ReturnUrl: str = "/"):  # noqa: N803 - the name real portals use
        return page(
            "Anmeldung",
            "<h1>Anmeldung erforderlich</h1><form method='post' action='/_auth/anmelden'>"
            "<label>Benutzername <input name='username'></label>"
            "<label>Passwort <input type='password' name='password'></label>"
            f"<input type='hidden' name='ReturnUrl' value='{e(ReturnUrl)}'><button>Anmelden</button></form>",
            lang="de",
        )

    @app.get("/files/large/{tender_id}/{name}")
    async def large_file(tender_id: str, name: str):
        return StreamingResponse(
            files.large_stream(),
            media_type="application/zip",
            headers={
                "Content-Length": str(files.LARGE_SIZE),
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            },
        )

    @app.get("/files/{kind}/{tender_id}/{name}")
    async def document(kind: str, tender_id: str, name: str):
        if kind in ("login", "robot"):
            return blocked.document_gate(kind, tender_id, name)
        known = (
            {(t.id.replace("/", "-"), d.name) for t in data.portal(kind).tenders for d in t.docs}
            if kind in VARIANTS
            else set()
        )
        if kind == "multistep" and name == multistep.BUNDLE_NAME:
            return Response(
                multistep.bundle_bytes(tender_id),
                media_type="application/zip",
                headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
            )
        if (tender_id, name) not in known:
            raise HTTPException(404)
        body, media = files.document(tender_id, name)
        return Response(
            body, media_type=media, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"}
        )

    @app.get("/_mock/variants")
    async def get_variants():
        return {"current": app.state.variants, "available": VARIANTS, "entry": ENTRY, "expectedState": EXPECTED_STATE}

    @app.post("/_mock/variants/{kind}")
    async def set_variant(kind: str, request: Request):
        body = await request.json()
        v = body.get("variant", "base")
        if kind not in VARIANTS or v not in VARIANTS[kind]:
            return JSONResponse(
                {"error": f"unknown variant {kind}={v}", "available": VARIANTS.get(kind)}, status_code=400
            )
        app.state.variants[kind] = v
        return {"kind": kind, "variant": v}

    return app
