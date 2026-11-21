"""`app`: a Dutch single-page app shaped like tenderned.nl, for engine v2 (ADR 0014). Matches examples/v2/.

A person opens the home page, clicks **Alle aankondigingen**, gets 10 notices with a **Laad meer** button, opens a
notice and switches to its **Documenten** tab. Every page is a JS shell; the data comes from a JSON API, which a v2
template can also read directly:

- `GET /app/api/publicaties?page=&size=&publicatieType=&sluitingsDatumVanaf=` → `{content, totalElements, …}`.
  The list mixes notice types: calls for competition (`AAO`), award notices (`AGO`), a prior notice (`VAK`) and a call
  that was ended early (`isVroegtijdigeBeeindiging`). Only open calls are open tenders.
- `GET /app/api/publicaties/{id}` → the notice plus description, CPV codes and place.
- `GET /app/api/publicaties/{id}/documenten` → `{documenten: [{documentNaam, links.download.href}]}` (relative hrefs).

Deadlines are local Amsterdam time without an offset (`2027-02-18T12:00:00` in JSON, `18-02-2027 12:00` on pages).

Variants:
- `relabelled`: the link, the button and the tab get new names ("Zoeken in publicaties", "Toon meer resultaten",
  "Stukken"): a browser flow keyed on the old names breaks; the API is unchanged.
- `api-moved`: the API moves to `/app/api/v3/…` and the old paths answer 404; the pages use the new API.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from mockportals import data
from mockportals.html import e, page, variant

router = APIRouter()
KIND = "app"

NOTICE = {"call": ("AAO", "Aankondiging opdracht"), "award": ("AGO", "Aankondiging gegunde opdracht"),
          "prior": ("VAK", "Vooraankondiging")}  # fmt: skip
PROCEDURE = {
    "open": ("OPE", "Openbaar"),
    "restricted": ("NOP", "Niet-openbaar"),
    "negotiated": ("ONG", "Onderhandelingsprocedure"),
    "other": ("CCD", "Concurrentiegerichte dialoog"),
}
NATURE = {"works": ("W", "Werken"), "services": ("D", "Diensten"), "supplies": ("L", "Leveringen")}
STATUS = {"open": ("PUB", "Gepubliceerd"), "withdrawn": ("ING", "Ingetrokken"), "closed": ("GES", "Gesloten")}
PAGE_SIZE = 10


def labels(v: str) -> dict[str, str]:
    if v == "relabelled":
        return {"all": "Zoeken in publicaties", "more": "Toon meer resultaten", "docs": "Stukken"}
    return {"all": "Alle aankondigingen", "more": "Laad meer", "docs": "Documenten"}


def api_base(v: str) -> str:
    return "/app/api/v3" if v == "api-moved" else "/app/api"


def _summary(t: data.MockTender) -> dict:
    code, name = NOTICE[t.notice]
    return {
        "publicatieId": t.id,
        "publicatieDatum": t.published.isoformat(),
        "sluitingsDatum": f"{t.deadline:%Y-%m-%dT%H:%M:%S}",
        "aanbestedingNaam": t.title,
        "opdrachtgeverNaam": t.buyer,
        "kenmerk": t.reference,
        "typePublicatie": {"code": code, "omschrijving": name},
        "isVroegtijdigeBeeindiging": t.early_termination,
        "procedure": dict(zip(("code", "omschrijving"), PROCEDURE[t.procedure], strict=True)),
        "typeOpdracht": dict(zip(("code", "omschrijving"), NATURE[t.nature], strict=True)),
        "publicatiestatus": dict(zip(("code", "omschrijving"), STATUS[t.status], strict=True)),
        "link": {"href": f"{data.BASE}/app/aankondigingen/{t.id}"},
    }


def _detail(t: data.MockTender) -> dict:
    return _summary(t) | {
        "opdrachtBeschrijving": t.description,
        "cpvCodes": [{"code": c, "isHoofdOpdracht": i == 0} for i, c in enumerate(t.cpv)],
        "plaatsVanUitvoering": {"postcode": t.postal_code, "plaats": t.city, "land": t.country},
    }


def _find(tid: str) -> data.MockTender:
    t = next((t for t in data.portal(KIND).tenders if t.id == tid), None)
    if t is None:
        raise HTTPException(404)
    return t


def _check_api(request: Request) -> None:
    """Only the variant's API base answers."""
    if not request.url.path.startswith(api_base(variant(request, KIND)) + "/publicaties"):
        raise HTTPException(404)


def _json(body: dict) -> Response:
    return Response(json.dumps(body, ensure_ascii=False), media_type="application/json")


# --------------------------------------------------------------------------- pages


@router.get("/app/", response_class=HTMLResponse)
async def home(request: Request):
    lb = labels(variant(request, KIND))
    return page(
        "Aanbestedingen Voorbeeld",
        "<h1>Welkom bij Aanbestedingen Voorbeeld</h1><p>Het platform voor overheidsopdrachten.</p>"
        "<nav><a href='/app/over'>Over ons</a> · <a href='/app/aankondigingen'>" + e(lb["all"]) + "</a></nav>",
        lang="nl",
    )


@router.get("/app/over", response_class=HTMLResponse)
async def about():
    return page("Over ons", "<h1>Over ons</h1><p>Een voorbeeldportaal.</p>", lang="nl")


LIST_JS = """
const API = %(api)s, MORE = %(more)s;
let pageNo = 0;
function fmt(iso) { const [d, t] = iso.split('T'); const [y, m, dd] = d.split('-');
  return dd + '-' + m + '-' + y + (t ? ' ' + t.slice(0, 5) : ''); }
function show(d) {
  document.getElementById('count').textContent = d.totalElements.toLocaleString('nl-NL') + ' resultaten';
  const list = document.getElementById('list');
  for (const p of d.content) {
    const a = document.createElement('article');
    a.className = 'publicatie';
    a.dataset.id = p.publicatieId; a.dataset.type = p.typePublicatie.code;
    a.dataset.vb = String(p.isVroegtijdigeBeeindiging);
    a.innerHTML = '<h2><a href="/app/aankondigingen/' + p.publicatieId + '">' + p.aanbestedingNaam + '</a></h2>' +
      '<p><span class="type">' + p.typePublicatie.omschrijving + '</span> · <span class="opdrachtgever">' +
      p.opdrachtgeverNaam + '</span> · kenmerk <span class="kenmerk">' + p.kenmerk + '</span></p>' +
      '<p>Gepubliceerd <time class="gepubliceerd" datetime="' + p.publicatieDatum + '">' + fmt(p.publicatieDatum) +
      '</time> · sluiting <span class="sluiting">' + fmt(p.sluitingsDatum) + '</span> · <span class="status">' +
      p.publicatiestatus.omschrijving + '</span></p>';
    list.appendChild(a);
  }
  const more = document.getElementById('more');
  if (d.last) { more.remove(); } else { more.hidden = false; more.disabled = false; }
}
function load() {
  const more = document.getElementById('more'); if (more) more.disabled = true;
  fetch(API + '/publicaties?page=' + pageNo + '&size=%(size)d', {headers: {Accept: 'application/json'}})
    .then(r => r.json()).then(show);
  pageNo++;
}
document.getElementById('more').textContent = MORE;
document.getElementById('more').addEventListener('click', load);
load();
"""


def _bundle(name: str, request: Request) -> str:
    """Script URL with a version per variant, as real apps hash their bundles: a changed portal ships a new URL, so a
    browser cache (net's StaticCache) never serves the old script after a change."""
    return f"/app/static/{name}.js?v={variant(request, KIND)}"


@router.get("/app/aankondigingen", response_class=HTMLResponse)
async def list_page(request: Request):
    return page(
        "Aankondigingen",
        "<h1>Aankondigingen</h1><p class='result-count' id='count'>Laden…</p><div id='list'></div>"
        f"<button id='more' hidden></button><script src='{_bundle('list', request)}'></script>",
        lang="nl",
    )


@router.get("/app/static/list.js")
async def list_js(request: Request):
    v = variant(request, KIND)
    js = LIST_JS % {"api": json.dumps(api_base(v)), "more": json.dumps(labels(v)["more"]), "size": PAGE_SIZE}
    return Response(js, media_type="application/javascript")


DETAIL_JS = """
const API = %(api)s, DOCS = %(docs)s, ID = document.getElementById('app').dataset.id;
function fmt(iso) { const [d, t] = iso.split('T'); const [y, m, dd] = d.split('-');
  return dd + '-' + m + '-' + y + (t ? ' ' + t.slice(0, 5) : ''); }
function select(tab) {
  for (const b of document.querySelectorAll('[role=tab]')) b.setAttribute('aria-selected', String(b === tab));
  for (const p of document.querySelectorAll('[role=tabpanel]')) p.hidden = p.id !== tab.getAttribute('aria-controls');
}
fetch(API + '/publicaties/' + ID).then(r => r.json()).then(p => {
  const place = p.plaatsVanUitvoering;
  document.getElementById('app').innerHTML =
    '<h1>' + p.aanbestedingNaam + '</h1>' +
    '<div role="tablist"><button role="tab" aria-selected="true" aria-controls="details">Details</button>' +
    '<button role="tab" aria-selected="false" aria-controls="documenten">' + DOCS + '</button></div>' +
    '<section role="tabpanel" id="details"><dl>' +
    '<dt>Opdrachtgever</dt><dd class="opdrachtgever">' + p.opdrachtgeverNaam + '</dd>' +
    '<dt>Kenmerk</dt><dd class="kenmerk">' + p.kenmerk + '</dd>' +
    '<dt>Omschrijving</dt><dd class="omschrijving">' + p.opdrachtBeschrijving + '</dd>' +
    '<dt>Procedure</dt><dd class="procedure">' + p.procedure.omschrijving + '</dd>' +
    '<dt>Type opdracht</dt><dd class="type-opdracht">' + p.typeOpdracht.omschrijving + '</dd>' +
    '<dt>CPV</dt><dd><ul class="cpv">' + p.cpvCodes.map(c => '<li>' + c.code + '</li>').join('') + '</ul></dd>' +
    '<dt>Plaats van uitvoering</dt><dd class="plaats"><span class="postcode">' + place.postcode +
    '</span> <span class="stad">' + place.plaats + '</span> <span class="land">' + place.land + '</span></dd>' +
    '<dt>Sluitingsdatum</dt><dd class="sluiting">' + fmt(p.sluitingsDatum) + '</dd>' +
    '</dl></section><section role="tabpanel" id="documenten" hidden><ul class="documenten"></ul></section>';
  const [details, docs] = document.querySelectorAll('[role=tab]');
  details.addEventListener('click', () => select(details));
  docs.addEventListener('click', () => {
    select(docs);
    fetch(API + '/publicaties/' + ID + '/documenten').then(r => r.json()).then(d => {
      document.querySelector('ul.documenten').innerHTML = d.documenten.map(x =>
        '<li><a class="download" href="' + x.links.download.href + '">' + x.documentNaam + '</a></li>').join('');
    });
  });
});
"""


@router.get("/app/aankondigingen/{tid}", response_class=HTMLResponse)
async def detail_page(tid: str, request: Request):
    _find(tid)
    return page(
        "Aankondiging",
        f"<div id='app' data-id='{e(tid)}'>Laden…</div><script src='{_bundle('detail', request)}'></script>",
        lang="nl",
    )


@router.get("/app/static/detail.js")
async def detail_js(request: Request):
    v = variant(request, KIND)
    js = DETAIL_JS % {"api": json.dumps(api_base(v)), "docs": json.dumps(labels(v)["docs"])}
    return Response(js, media_type="application/javascript")


# --------------------------------------------------------------------------- API (both bases; one answers)


@router.get("/app/api/publicaties")
@router.get("/app/api/v3/publicaties")
async def publicaties(
    request: Request,
    page: int = 0,
    size: int = PAGE_SIZE,
    publicatieType: str | None = None,  # noqa: N803 - the portal's names
    sluitingsDatumVanaf: str | None = None,  # noqa: N803
):
    _check_api(request)
    items = list(data.portal(KIND).tenders)
    if publicatieType:
        items = [t for t in items if NOTICE[t.notice][0] == publicatieType]
    if sluitingsDatumVanaf:
        items = [t for t in items if t.deadline.date().isoformat() >= sluitingsDatumVanaf]
    size = max(1, min(size, 100))
    chunk = items[page * size : (page + 1) * size]
    pages = (len(items) + size - 1) // size
    return _json(
        {
            "content": [_summary(t) for t in chunk],
            "totalElements": len(items),
            "totalPages": pages,
            "number": page,
            "size": size,
            "last": page >= pages - 1,
        }
    )


@router.get("/app/api/publicaties/{tid}")
@router.get("/app/api/v3/publicaties/{tid}")
async def publicatie(request: Request, tid: str):
    _check_api(request)
    return _json(_detail(_find(tid)))


@router.get("/app/api/publicaties/{tid}/documenten")
@router.get("/app/api/v3/publicaties/{tid}/documenten")
async def documenten(request: Request, tid: str):
    _check_api(request)
    t = _find(tid)
    return _json({"documenten": [{"documentNaam": d.name, "links": {"download": {"href": d.path}}} for d in t.docs]})
