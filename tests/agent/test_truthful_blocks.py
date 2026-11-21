"""Truthful blocks (T-039): regression fixtures for the false blocks of the first live smoke run, and the true ones.

Small local portals reproduce what the smoke run met (docs/portals/registry.md → Smoke runs):
- dtvp.de: document links redirect to a Keycloak login until the portal's session cookie exists;
- marches-publics.gouv.fr (PLACE): the DCE link asks for a login, but "Téléchargement anonyme" delivers the file;
- s2c.mercell.com: an Azure Application Gateway answers HTTP 403 on every path (a block, not "temporary").
"""

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from scripted import ScriptedModel, call

from mockportals.app import create_app as create_mock
from watchtower.agent.attach import run_attach
from watchtower.agent.context import AgentContext
from watchtower.agent.outcomes import Blocked, Transient, Unreadable
from watchtower.agent.runtime import run_scenario
from watchtower.net import HostRateLimiter, PoliteClient

PDF = b"%PDF-1.7\n" + b"0" * 2000
ZIP = b"PK\x03\x04" + b"0" * 2000
LOGIN = (
    "<html><head><title>Sign in to dtvp</title></head><body><form id='kc-form-login' method='post'>"
    "<input name='username'><input type='password' name='password'><button>Sign in</button></form></body></html>"
)


def html(body: str, title: str = "Portal") -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><html><head><title>{title}</title></head><body>{body}</body></html>")


def session_portal() -> FastAPI:
    """dtvp.de: the list sets a session cookie; documents without it redirect to the login."""
    app = FastAPI()

    def with_cookie(resp: Response) -> Response:
        resp.set_cookie("SESSION", "abc")
        return resp

    @app.get("/")
    async def home():
        return with_cookie(html("<a href='/list'>Bekanntmachungen</a>"))

    @app.get("/list")
    async def listing():
        rows = "".join(f"<li><a href='/tender/T{i}'>Tender {i}</a></li>" for i in range(1, 4))
        return with_cookie(html(f"<ul class='tenders'>{rows}</ul>"))

    @app.get("/tender/{tid}")
    async def tender(tid: str):
        return html(f"<h1>{tid}</h1><a href='/docs/{tid}/unterlagen.pdf'>Vergabeunterlagen</a>")

    @app.get("/docs/{tid}/{name}")
    async def doc(tid: str, name: str, request: Request):
        if request.cookies.get("SESSION"):
            return Response(PDF, media_type="application/pdf")
        return RedirectResponse("/auth/realms/dtvp/login", status_code=302)

    @app.get("/auth/realms/dtvp/login")
    async def login():
        return HTMLResponse(LOGIN)

    return app


def anonymous_portal() -> FastAPI:
    """PLACE: the DCE link asks for a login; an anonymous-download link delivers it."""
    app = FastAPI()

    @app.get("/")
    async def home():
        rows = "".join(f"<li><a href='/consultation/C{i}'>Consultation {i}</a></li>" for i in range(1, 4))
        return html(f"<ul>{rows}</ul>")

    @app.get("/consultation/{cid}")
    async def consultation(cid: str):
        return html(
            f"<h1>{cid}</h1><a href='/dce/{cid}.zip'>Dossier de consultation (DCE)</a> "
            f"<a href='/dce/{cid}/anonyme'>Téléchargement anonyme</a>"
        )

    @app.get("/dce/{name}")
    async def dce(name: str):
        return RedirectResponse("/connexion", status_code=302)

    @app.get("/dce/{cid}/anonyme")
    async def anonyme(cid: str):
        return Response(
            ZIP, media_type="application/zip", headers={"content-disposition": f"attachment; filename={cid}.zip"}
        )

    @app.get("/connexion")
    async def connexion():
        return html(
            "<form><input name='login'><input type='password' name='password'><button>Se connecter</button></form>",
            title="Connexion",
        )

    return app


GATEWAY_403 = (
    "<html><head><title>403 Forbidden</title></head><body><center><h1>403 Forbidden</h1></center><hr>"
    "<center>Microsoft-Azure-Application-Gateway/v2</center></body></html>"
)


def firewall_portal(open_path: str | None = None) -> FastAPI:
    """s2c.mercell.com: 403 from the gateway on every path (or every path but `open_path`)."""
    app = FastAPI()

    @app.get("/{path:path}")
    async def anything(path: str):
        if open_path is not None and f"/{path}" == open_path:
            return html("<p>ok</p>")
        return HTMLResponse(GATEWAY_403, status_code=403)

    return app


@pytest.fixture
async def clients():
    made: list[PoliteClient] = []

    def make(app) -> PoliteClient:
        c = PoliteClient(limiter=HostRateLimiter(0), transport=httpx.ASGITransport(app=app), max_attempts=1)
        made.append(c)
        return c

    yield make
    for c in made:
        await c.aclose()


async def run(client: PoliteClient, url: str, turns: list) -> tuple[AgentContext, ScriptedModel]:
    ctx = AgentContext(url, client)
    model = ScriptedModel(turns)
    await run_scenario("attach", "Attach.", "go", ctx, model=model)
    return ctx, model


def outputs(model: ScriptedModel) -> str:
    return "\n----\n".join(model.seen_tool_outputs)


# --------------------------------------------------------------------------- the smoke run's false blocks


async def test_dtvp_documents_that_need_only_a_session_are_not_a_block(clients):
    base = "http://dtvp.test"
    ctx, model = await run(
        clients(session_portal()),
        f"{base}/",
        [
            [call("probe_document", url=f"{base}/docs/T1/unterlagen.pdf")],
            [call("probe_document", url=f"{base}/docs/T2/unterlagen.pdf")],
            [call("report_blocked", reason="Documents redirect to a Keycloak login.")],
            [call("report_unreadable", reason="stop")],
        ],
    )
    out = outputs(model)
    assert out.count("leads to a login (") >= 2  # both probes did see the login, without a session
    assert "Not accepted" in out and "DISPROVED" in out and "session cookies" in out
    assert not isinstance(ctx.outcome, Blocked)


async def test_place_anonymous_download_route_is_not_a_block(clients):
    base = "http://place.test"
    ctx, model = await run(
        clients(anonymous_portal()),
        f"{base}/",
        [
            [call("detect_block", url=f"{base}/consultation/C1")],
            [call("detect_block", url=f"{base}/consultation/C2")],
            [call("report_blocked", reason="The DCE download redirects to a login.")],
            [call("report_unreadable", reason="stop")],
        ],
    )
    out = outputs(model)
    assert out.count("VERDICT: BLOCKED HERE") == 2
    assert "DISPROVED: the anonymous route 'Téléchargement anonyme'" in out and "file OK" in out
    assert not isinstance(ctx.outcome, Blocked)


async def test_a_block_seen_on_one_tender_is_refused_and_the_attach_says_so(clients):
    client = clients(create_mock())
    model = ScriptedModel(
        [
            [call("detect_block", url="http://localhost:8081/login/verfahren/W-2026-001")],
            [call("report_blocked", reason="Documents need a login.")],
        ]
    )
    result = await run_attach("http://localhost:8081/login/", client, None, model=model)
    assert "seen on 1 tender(s)" in model.seen_tool_outputs[-1]
    assert isinstance(result.outcome, Unreadable)
    assert result.outcome.reason.startswith("a login or captcha block was suspected (Documents need a login) but")
    assert "1 tender(s)" in result.outcome.reason


# --------------------------------------------------------------------------- true blocks stay blocks


async def test_documents_blocked_on_two_tenders_are_a_block(clients):
    b = "http://localhost:8081"
    ctx, _ = await run(
        clients(create_mock()),
        f"{b}/login/",
        [
            [call("detect_block", url=f"{b}/login/verfahren/W-2026-001")],
            [call("detect_block", url=f"{b}/login/verfahren/W-2026-002")],
            [call("report_blocked", reason="Documents need a login.")],
        ],
    )
    assert ctx.outcome == Blocked(reason="Documents need a login.")


async def test_a_login_wall_on_the_entry_page_is_a_block_at_once(clients):
    ctx, _ = await run(
        clients(create_mock({"ssr": "login-wall"})),
        "http://localhost:8081/ssr/",
        [[call("report_blocked", reason="Every page redirects to a login.")]],
    )
    assert ctx.outcome == Blocked(reason="Every page redirects to a login.")


async def test_a_gateway_403_on_every_path_is_a_block_not_temporary(clients):
    base = "http://s2c.test"
    ctx, _ = await run(
        clients(firewall_portal()),
        f"{base}/start",
        [[call("fetch", url=f"{base}/start")], [call("report_transient", reason="HTTP 403 everywhere.")]],
    )
    assert isinstance(ctx.outcome, Blocked)
    assert ctx.outcome.reason.startswith("Access refused by the portal's firewall (Microsoft-Azure-Application-Gateway")
    assert "HTTP 403 on every path tried" in ctx.outcome.reason


async def test_a_403_on_one_page_only_stays_temporary(clients):
    base = "http://s2c.test"
    ctx, _ = await run(
        clients(firewall_portal(open_path="/robots.txt")),
        f"{base}/start",
        [[call("report_transient", reason="HTTP 403 on the start page.")]],
    )
    assert ctx.outcome == Transient(reason="HTTP 403 on the start page.")
