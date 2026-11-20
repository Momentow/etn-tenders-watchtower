"""Portals a correct system must report as `failed`, modelled on what T-001 found:

- `login` (AT, like provia.at): list and detail pages are public, but every document redirects to a login form.
- `robot` (BE, like cloud.3p.eu): each document opens a page with a robot check; submitting it without solving fails.

Neither has a way through without logging in or solving a captcha, which Watchtower must never do.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from mockportals import data
from mockportals.html import e, page

router = APIRouter()

_TEXT = {
    "login": {
        "lang": "de",
        "list": "/login/ausschreibungen",
        "detail": "/login/verfahren",
        "title": "Bieterportal",
        "h": "Veröffentlichte Verfahren",
        "deadline": "Angebotsfrist",
        "docs": "Bereitgestellte Unterlagen",
    },
    "robot": {
        "lang": "nl",
        "list": "/robot/opdrachten",
        "detail": "/robot/opdracht",
        "title": "Opdrachtenportaal",
        "h": "Lopende opdrachten",
        "deadline": "Uiterste indiendatum",
        "docs": "Opdrachtdocumenten",
    },
}


def _list(kind: str) -> HTMLResponse:
    x = _TEXT[kind]
    rows = "".join(
        f"<tr><td>{e(t.reference)}</td><td><a href='{x['detail']}/{quote(t.id, safe='')}'>{e(t.title)}</a></td>"
        f"<td>{e(t.buyer)}</td><td>{t.published:%d.%m.%Y}</td></tr>"
        for t in data.portal(kind).tenders
    )
    return page(x["title"], f"<h1>{x['h']}</h1><table class='verfahren'><tbody>{rows}</tbody></table>", lang=x["lang"])


def _detail(kind: str, tid: str) -> HTMLResponse:
    x = _TEXT[kind]
    t = next((t for t in data.portal(kind).tenders if t.id == tid), None)
    if t is None:
        raise HTTPException(404)
    docs = "".join(f"<li><a href='{d.path}'>{e(d.name)}</a></li>" for d in t.docs)
    return page(
        t.title,
        f"<h1>{e(t.title)}</h1><p>{e(t.buyer)}</p><p>{x['deadline']}: {t.deadline:%d.%m.%Y %H:%M}</p>"
        f"<h2>{x['docs']}</h2><ul class='unterlagen'>{docs}</ul>",
        lang=x["lang"],
    )


def document_gate(kind: str, tender_id: str, name: str):
    """What a document link returns on a blocked portal (called from the shared /files route)."""
    if kind == "login":
        return RedirectResponse(
            f"/login/anmelden?ReturnUrl={quote(f'/files/login/{tender_id}/{name}')}", status_code=302
        )
    return RedirectResponse(f"/robot/verificatie?doc={quote(f'{tender_id}/{name}')}", status_code=302)


for _kind in _TEXT:
    router.add_api_route(
        f"/{_kind}/",
        lambda k=_kind: page(
            _TEXT[k]["title"], f"<a href='{_TEXT[k]['list']}'>{_TEXT[k]['h']}</a>", lang=_TEXT[k]["lang"]
        ),
        methods=["GET"],
        response_class=HTMLResponse,
    )
    router.add_api_route(_TEXT[_kind]["list"], lambda k=_kind: _list(k), methods=["GET"], response_class=HTMLResponse)
    router.add_api_route(
        _TEXT[_kind]["detail"] + "/{tid:path}",
        lambda tid, k=_kind: _detail(k, tid),
        methods=["GET"],
        response_class=HTMLResponse,
    )


@router.get("/login/anmelden", response_class=HTMLResponse)
async def login_form(ReturnUrl: str = "/"):  # noqa: N803
    return page(
        "Anmeldung",
        "<h1>Anmeldung</h1><p>Bitte melden Sie sich an, um die Unterlagen herunterzuladen.</p>"
        "<form method='post' action='/login/anmelden'><input name='Benutzername'>"
        f"<input type='password' name='Passwort'><input type='hidden' name='ReturnUrl' value='{e(ReturnUrl)}'>"
        "<button>Anmelden</button></form><p><a href='/login/registrierung'>Interesse bekunden / registrieren</a></p>",
        lang="de",
    )


@router.get("/robot/verificatie", response_class=HTMLResponse)
async def robot_check(doc: str = ""):
    return page(
        "Verificatie",
        "<h1>Documenten downloaden</h1><form method='post' action='/robot/verificatie'>"
        "<div class='g-recaptcha' data-sitekey='mock-site-key'></div>"
        f"<input type='hidden' name='doc' value='{e(doc)}'><button>Verifieer</button></form>",
        lang="nl",
    )


@router.post("/robot/verificatie", response_class=HTMLResponse)
async def robot_check_submit():
    return page(
        "Verificatie", "<p class='fout'>De verificatie tegen robots is mislukt. Probeer het opnieuw.</p>", lang="nl"
    )
