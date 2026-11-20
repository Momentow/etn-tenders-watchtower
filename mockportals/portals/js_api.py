"""`js`: a French JS app whose HTML is an empty shell; data comes from a JSON API. Matches examples/js_json_api.json.

The shell's script calls the API, so a browser render (BrowserPool) captures `/js/api/consultations` as XHR; a template
should then read that JSON directly. Publication dates are ISO; deadlines are `dd/mm/yyyy HH:MM` Paris time.

Variants:
- `renamed-keys`: `resultats`→`items`, `objet`→`intitule`, `acheteur.nom`→`organisme.libelle`,
  `dateLimiteRemise`→`dateLimite`.
- `pagination`: `page` is ignored, `offset` works, page size fixed at 20 (a template paging by `page` sees repeats).
- `silent-missed`: three new consultations are only listed with `etat=tous`; `etat=en_cours` silently omits them.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from mockportals import data
from mockportals.html import e, page, variant

router = APIRouter()
KIND = "js"
BASE = data.BASE

PROCEDURE = {"open": "AOO", "restricted": "AOR", "negotiated": "PN", "other": "MAPA"}
NATURE = {"works": "TRAVAUX", "services": "SERVICES", "supplies": "FOURNITURES"}
STATUS = {"open": "EN_COURS", "withdrawn": "ANNULEE", "closed": "CLOTUREE"}

APP_JS = """
fetch('/js/api/consultations?etat=en_cours&taille=50&page=0', {headers: {Accept: 'application/json'}})
  .then(r => r.json())
  .then(d => {
    const rows = (d.resultats || d.items || []).map(c =>
      `<li class="consultation"><a href="${c.lienPublic}">${c.objet || c.intitule}</a>
       — ${(c.acheteur || {}).nom || (c.organisme || {}).libelle || ''}</li>`);
    document.getElementById('app').innerHTML = `<h1>Consultations en cours</h1><ul>${rows.join('')}</ul>`;
  });
"""


def _summary(t: data.MockTender, v: str) -> dict:
    s = {
        "id": t.id,
        "lienPublic": f"{BASE}/js/consultation/{t.id}",
        "objet": t.title,
        "acheteur": {"nom": t.buyer, "siret": "21750001600019"},
        "datePublication": t.published.isoformat(),
        "dateLimiteRemise": f"{t.deadline:%d/%m/%Y %H:%M}",
        "reference": t.reference,
        "procedure": PROCEDURE[t.procedure],
        "categorie": NATURE[t.nature],
        "statut": STATUS[t.status],
    }
    if v == "renamed-keys":
        s["intitule"] = s.pop("objet")
        s["organisme"] = {"libelle": s.pop("acheteur")["nom"]}
        s["dateLimite"] = s.pop("dateLimiteRemise")
    return s


def _detail(t: data.MockTender, v: str) -> dict:
    return _summary(t, v) | {
        "description": t.description,
        "cpv": list(t.cpv),
        "lieuExecution": {"codePostal": t.postal_code, "ville": t.city},
        "dce": [{"nomFichier": d.name, "url": d.url, "taille": None} for d in t.docs],
    }


@router.get("/js/", response_class=HTMLResponse)
async def shell():
    return page(
        "Marchés publics de l'Exemple",
        "<div id='app'>Chargement…</div><noscript>Activez JavaScript.</noscript>",
        lang="fr",
        head="<script defer src='/js/app.js'></script>",
    )


@router.get("/js/app.js")
async def app_js(request: Request):
    js = APP_JS
    if variant(request, KIND) == "silent-missed":
        js = js.replace("etat=en_cours", "etat=tous")  # the site's own app now lists everything
    return Response(js, media_type="application/javascript")


@router.get("/js/consultation/{tid}", response_class=HTMLResponse)
async def public_page(tid: str):
    return page(
        "Consultation",
        f"<div id='app' data-id='{e(tid)}'>Chargement…</div>",
        lang="fr",
        head="<script defer src='/js/app.js'></script>",
    )


@router.get("/js/api/consultations")
async def consultations(
    request: Request, etat: str = "en_cours", taille: int = 50, page: int = 0, offset: int | None = None
):
    v = variant(request, KIND)
    items = [t for t in data.visible(KIND, v) if etat == "tous" or not t.new]
    if v == "pagination":
        size, start = 20, offset or 0  # `page` ignored
    else:
        size = max(1, min(taille, 100))
        start = offset if offset is not None else page * size
    chunk = items[start : start + size]
    key = "items" if v == "renamed-keys" else "resultats"
    return JSONResponse({key: [_summary(t, v) for t in chunk], "total": len(items), "page": page, "taille": size})


@router.get("/js/api/consultations/{tid}")
async def consultation(request: Request, tid: str):
    v = variant(request, KIND)
    t = next((t for t in data.visible(KIND, v) if t.id == tid), None)
    if t is None:
        raise HTTPException(404)
    return Response(json.dumps(_detail(t, v), ensure_ascii=False), media_type="application/json")
