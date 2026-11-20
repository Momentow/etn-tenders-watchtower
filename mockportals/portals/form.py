"""`form`: a Polish portal whose tender list is the result of a POSTed search form, paged by a form field.
Matches examples/search_form.json. Everything is on the list row (no detail page needed); rows link to a tender page.

Variants:
- `pagination`: the `strona` form field is ignored; pages move to a `?page=N` query parameter on the POST.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from mockportals import data
from mockportals.html import e, page, variant

router = APIRouter()
KIND = "form"
PAGE_SIZE = 5

FORM = (
    "<form id='szukaj' method='post' action='/form/przetargi'>"
    "<label>Status <select name='status'><option value='otwarte'>Otwarte</option>"
    "<option value='zamkniete'>Zamknięte</option></select></label>"
    "<input type='hidden' name='strona' value='1'><button>Szukaj</button></form>"
)


def _doc_links(t: data.MockTender) -> str:
    return " ".join(f'<a href="{d.path}">{e(d.name)}</a>' for d in t.docs)


@router.get("/form/", response_class=HTMLResponse)
async def home():
    return page(
        "Platforma Zakupowa Przykład",
        "<h1>Przetargi</h1><p><a href='/form/przetargi'>Wyszukiwarka przetargów</a></p>",
        lang="pl",
    )


@router.get("/form/przetargi", response_class=HTMLResponse)
async def search_page():
    return page("Wyszukiwarka", f"<h1>Wyszukaj przetargi</h1>{FORM}", lang="pl")


@router.post("/form/przetargi", response_class=HTMLResponse)
async def results(request: Request, status: str = Form("otwarte"), strona: int = Form(1)):
    v = variant(request, KIND)
    n = int(request.query_params.get("page", 1)) if v == "pagination" else strona
    tenders = data.visible(KIND, v) if status == "otwarte" else []
    start = (max(n, 1) - 1) * PAGE_SIZE
    rows = "".join(
        f"<tr><td>{e(t.id)}</td><td><a href='/form/przetarg/{e(t.id)}'>{e(t.title)}</a></td><td>{e(t.buyer)}</td>"
        f"<td>{t.published:%Y-%m-%d}</td><td>{t.deadline:%Y-%m-%d %H:%M}</td>"
        f"<td>{_doc_links(t)}</td></tr>"
        for t in tenders[start : start + PAGE_SIZE]
    )
    pager = (
        "<p>Strony: "
        + " ".join(
            f"<button form='szukaj' name='strona' value='{i}'>{i}</button>"
            for i in range(1, (len(tenders) - 1) // PAGE_SIZE + 2)
        )
        + "</p>"
        if v != "pagination"
        else "<p>"
        + " ".join(f"<a href='?page={i}'>{i}</a>" for i in range(1, (len(tenders) - 1) // PAGE_SIZE + 2))
        + "</p>"
    )
    body = (
        f"{FORM}<table id='wyniki'><thead><tr><th>Nr</th><th>Nazwa</th><th>Zamawiający</th><th>Data publikacji</th>"
        f"<th>Termin składania ofert</th><th>Dokumenty</th></tr></thead><tbody>{rows}</tbody></table>{pager}"
    )
    return page("Wyniki wyszukiwania", body, lang="pl")


@router.get("/form/przetarg/{tid}", response_class=HTMLResponse)
async def tender(tid: str):
    t = next((t for t in data.portal(KIND).tenders if t.id == tid), None)
    if t is None:
        raise HTTPException(404)
    return page(
        t.title,
        f"<h1>{e(t.title)}</h1><p>Zamawiający: {e(t.buyer)}</p>"
        f"<p>Termin składania ofert: {t.deadline:%Y-%m-%d %H:%M}</p>",
        lang="pl",
    )
