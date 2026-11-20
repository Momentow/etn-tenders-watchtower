"""`captcha`: a Dutch portal that shows a captcha widget, yet lists tenders and serves documents without it (the
trap named in the brief, seen on achatpublic.com). Matches examples/captcha_bypassable.json.

The search form (`form#zoeken`) leads to a page whose results are built by JavaScript from inline data, so reading the
list needs the browser (or reading the inline JSON). Correct state: `serving`, not `failed`.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from mockportals import data
from mockportals.html import e, page, variant

router = APIRouter()
KIND = "captcha"

CAPTCHA = (
    "<div class='g-recaptcha captcha' data-sitekey='mock-site-key'>"
    "<p>Bevestig dat u geen robot bent.</p><input type='checkbox' name='geen-robot'></div>"
)

RENDER_JS = """
const data = JSON.parse(document.getElementById('data').textContent);
document.getElementById('resultaten').innerHTML = data.map(t =>
  `<article class="aanbesteding" data-id="${t.id}"><h2><a href="/captcha/aanbesteding/${t.id}">${t.title}</a></h2>
   <span class="aanbestedende-dienst">${t.buyer}</span>
   <time class="publicatie" datetime="${t.published}">${t.publishedText}</time></article>`).join('');
"""


@router.get("/captcha/", response_class=HTMLResponse)
async def home():
    return page(
        "Aanbestedingen Voorbeeld",
        "<h1>Aanbestedingen</h1><p><a href='/captcha/aanbestedingen'>Zoeken</a></p>",
        lang="nl",
    )


@router.get("/captcha/aanbestedingen", response_class=HTMLResponse)
async def search():
    return page(
        "Aanbestedingen zoeken",
        f"<h1>Zoek aanbestedingen</h1>{CAPTCHA}"
        "<form id='zoeken' method='get' action='/captcha/zoeken'><input name='q'><button>Zoeken</button></form>",
        lang="nl",
    )


@router.get("/captcha/zoeken", response_class=HTMLResponse)
async def results(request: Request, q: str = ""):
    tenders = [t for t in data.visible(KIND, variant(request, KIND)) if q.lower() in t.title.lower()]
    payload = [
        {
            "id": t.id,
            "title": t.title,
            "buyer": t.buyer,
            "published": t.published.isoformat(),
            "publishedText": f"{t.published:%d-%m-%Y}",
        }
        for t in tenders
    ]
    return page(
        "Zoekresultaten",
        f"{CAPTCHA}<div id='resultaten'>Laden…</div>"
        f"<script type='application/json' id='data'>{json.dumps(payload, ensure_ascii=False)}</script>"
        f"<script>{RENDER_JS}</script>",
        lang="nl",
    )


@router.get("/captcha/aanbesteding/{tid}", response_class=HTMLResponse)
async def detail(tid: str):
    t = next((t for t in data.portal(KIND).tenders if t.id == tid), None)
    if t is None:
        raise HTTPException(404)
    docs = "".join(f"<li><a download href='{d.path}'>{e(d.name)}</a></li>" for d in t.docs)
    return page(
        t.title,
        f"<h1>{e(t.title)}</h1>{CAPTCHA}<p>Aanbestedende dienst: {e(t.buyer)}</p>"
        f"<p>Sluitingsdatum: <time class='sluiting' datetime='{t.deadline.isoformat()}'>"
        f"{t.deadline:%d-%m-%Y %H:%M}</time></p>"
        f"<h2>Documenten</h2><ul>{docs}</ul>",
        lang="nl",
    )
