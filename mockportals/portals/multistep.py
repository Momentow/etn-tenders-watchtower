"""`multistep`: a Spanish portal whose list links go to a notice summary (the trap), which links on to the real tender
page; each document opens a download page with the actual file link, and a "download all" ZIP exists.
Matches examples/multi_step_download.json.

One tender also lists a 300 MB `Planos.zip` (served lazily from /files/large/): a correct read may skip it.

Variants:
- `markup`: list cards are `article.expediente-card` with `a.ver-anuncio` instead of `div.licitacion` / `a.anuncio`.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from mockportals import data, files
from mockportals.html import cpv_with_check_digit, e, page, variant

router = APIRouter()
KIND = "multistep"
PAGE_SIZE = 5
BUNDLE_NAME = "Documentacion completa.zip"


def _tender(tid: str) -> data.MockTender:
    t = next((t for t in data.portal(KIND).tenders if t.id == tid), None)
    if t is None:
        raise HTTPException(404)
    return t


def bundle_bytes(tender_id: str) -> bytes:
    """The "download all" ZIP: every normal document of the tender (not the 300 MB one)."""
    t = _tender(tender_id)
    return files.zip_bytes(
        {d.name: files.document(t.id, d.name)[0] for d in t.docs if not d.path.startswith("/files/large/")}
    )


@router.get("/multistep/", response_class=HTMLResponse)
async def home():
    return page(
        "Plataforma de Contratación de Ejemplo",
        "<h1>Perfil del contratante</h1><p><a href='/multistep/licitaciones'>Licitaciones abiertas</a></p>",
        lang="es",
    )


@router.get("/multistep/licitaciones", response_class=HTMLResponse)
async def listing(request: Request, pagina: int = 1):
    v = variant(request, KIND)
    tenders = data.visible(KIND, v)
    start = (max(pagina, 1) - 1) * PAGE_SIZE
    card, link = ("article", "expediente-card"), "ver-anuncio"
    if v != "markup":
        card, link = ("div", "licitacion"), "anuncio"
    cards = "".join(
        f"<{card[0]} class='{card[1]}'><h3>{e(t.title)}</h3>"
        f"<p>Expediente: <span class='expediente'>{e(t.id)}</span></p>"
        f"<p>Órgano: <span class='organo'>{e(t.buyer)}</span></p>"
        f"<p>Publicado: <span class='publicado'>{t.published:%d/%m/%Y}</span></p>"
        f"<a class='{link}' href='/multistep/anuncio/{e(t.id)}'>Ver anuncio</a></{card[0]}>"
        for t in tenders[start : start + PAGE_SIZE]
    )
    return page("Licitaciones", f"<h1>Licitaciones abiertas</h1>{cards}", lang="es")


@router.get("/multistep/anuncio/{tid}", response_class=HTMLResponse)
async def notice(tid: str):
    t = _tender(tid)
    return page(
        "Anuncio",
        f"<h1>Anuncio de licitación</h1><p>{e(t.title)}</p><p>Resumen del anuncio publicado.</p>"
        f"<a class='ver-licitacion' href='/multistep/licitacion/{e(t.id)}'>Ver la licitación completa</a>",
        lang="es",
    )


@router.get("/multistep/licitacion/{tid}", response_class=HTMLResponse)
async def tender(tid: str):
    t = _tender(tid)
    cpv = "".join(f"<tr><th>CPV</th><td class='cpv'>{cpv_with_check_digit(c)}</td></tr>" for c in t.cpv)
    docs = "".join(
        f"<li><a class='pliego' href='/multistep/descarga/{e(t.id)}/{i}'>{e(d.name)}</a></li>"
        for i, d in enumerate(t.docs)
    )
    body = (
        f"<h1>{e(t.title)}</h1><table>"
        f"<tr><th>Expediente</th><td>{e(t.id)}</td></tr>"
        f"<tr><th>Órgano de contratación</th><td>{e(t.buyer)}</td></tr>"
        f"<tr><th>Plazo de presentación</th><td class='plazo'>{t.deadline:%d/%m/%Y %H:%M}</td></tr>{cpv}</table>"
        f"<h2>Pliegos</h2><ul>{docs}</ul>"
        f"<p><a class='descargar-todo' href='/files/multistep/{e(t.id)}/{BUNDLE_NAME}'>"
        "Descargar toda la documentación</a></p>"
        "<p><a class='pliego' href='/multistep/ayuda.html'>Ayuda.html</a></p>"
    )
    return page(t.title, body, lang="es")


@router.get("/multistep/descarga/{tid}/{n}", response_class=HTMLResponse)
async def download_page(tid: str, n: int):
    t = _tender(tid)
    if not 0 <= n < len(t.docs):
        raise HTTPException(404)
    d = t.docs[n]
    return page(
        "Descarga",
        f"<h1>Descarga de documento</h1><p>{e(d.name)}</p><a class='descarga-directa' href='{d.path}'>Descargar</a>",
        lang="es",
    )


@router.get("/multistep/ayuda.html", response_class=HTMLResponse)
async def help_page():
    return page("Ayuda", "<p>Ayuda para licitadores.</p>", lang="es")
