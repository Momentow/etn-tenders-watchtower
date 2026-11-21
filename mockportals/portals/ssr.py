"""`ssr`: a German server-rendered portal (list table + detail page per tender). Matches examples/ssr_list_detail.json.

Real quirks (docs/portals/registry.md): `;jsessionid=` in detail links, deadlines in local time ("Ortszeit"), CPV codes
with a check digit, a tender whose documents are only for registered companies, withdrawn/closed status labels.

Variants:
- `markup`: new class names and labels (Repair must rewrite selectors).
- `pagination`: `rel=next` replaced by an offset pager (`?start=10`); `?seite=` is ignored (reads only page 1).
- `silent-missed`: three new tenders appear in a second table the template doesn't read; everything still parses.
- `silent-swap`: the buyer column now shows the city; still parses, but buyerName is wrong.
- `silent-cut`: the result list is capped at 15 rows (page 2 shows 5, no further pages) while the hit count still says
  20. A 25 % drop passes validation; only the audit's declared-total check sees it (T-041).

Every variant shows the hit count ("20 Treffer") above the table, as real portals do.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from mockportals import data
from mockportals.html import cpv_with_check_digit, e, jsessionid, page, variant

router = APIRouter()
KIND = "ssr"
PAGE_SIZE = 10
CUT_AT = 15  # `silent-cut`: rows shown at most

PROCEDURE = {
    "open": "Offenes Verfahren",
    "restricted": "Nicht offenes Verfahren",
    "negotiated": "Verhandlungsverfahren",
}
NATURE = {"works": "Bauleistung", "services": "Dienstleistung", "supplies": "Lieferleistung"}
STATUS = {"open": "Veröffentlicht", "withdrawn": "aufgehoben", "closed": "abgelaufen"}


def _cls(v: str) -> dict[str, str]:
    if v == "markup":
        return {
            "table": "ergebnisse",
            "ref": "aktenzeichen",
            "title": "bezeichnung",
            "buyer": "vergabestelle",
            "published": "veroeffentlicht",
            "status": "zustand",
            "deadline": "Ablauf der Angebotsfrist",
        }
    return {
        "table": "tenders",
        "ref": "ref",
        "title": "title",
        "buyer": "buyer",
        "published": "published",
        "status": "status",
        "deadline": "Angebotsfrist",
    }


def _row(t: data.MockTender, c: dict[str, str], v: str) -> str:
    buyer = t.city if v == "silent-swap" else t.buyer
    return (
        f"<tr><td class='{c['ref']}'>{e(t.reference)}</td>"
        f"<td class='{c['title']}'>"
        f"<a href='/ssr/ausschreibung/{e(t.id)};jsessionid={jsessionid()}'>{e(t.title)}</a></td>"
        f"<td class='{c['buyer']}'>{e(buyer)}</td>"
        f"<td class='{c['published']}'>{t.published:%d.%m.%Y}</td>"
        f"<td class='{c['status']}'>{STATUS[t.status]}</td></tr>"
    )


def _table(cls: str, rows: str) -> str:
    return (
        f"<table class='{cls}'><thead><tr><th>Aktenzeichen</th><th>Bezeichnung</th><th>Vergabestelle</th>"
        f"<th>Veröffentlicht</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table>"
    )


@router.get("/ssr/", response_class=HTMLResponse)
async def home():
    return page(
        "Vergabeportal Musterland",
        "<h1>Vergabeportal</h1><p><a href='/ssr/ausschreibungen'>Aktuelle Ausschreibungen</a></p>",
        lang="de",
    )


@router.get("/ssr/ausschreibungen", response_class=HTMLResponse)
async def listing(request: Request, seite: int = 1, start: int = 0):
    v = variant(request, KIND)
    c = _cls(v)
    tenders = [t for t in data.visible(KIND, v) if not t.new]
    new = [t for t in data.visible(KIND, v) if t.new]
    if v == "pagination":
        offset = max(start, 0)
    else:
        offset = (max(seite, 1) - 1) * PAGE_SIZE
    hits = len(tenders) + len(new)
    shown = tenders[:CUT_AT] if v == "silent-cut" else tenders
    chunk = shown[offset : offset + PAGE_SIZE]
    body = f"<h1>Aktuelle Ausschreibungen</h1><p class='trefferzahl'>{hits} Treffer</p>"
    if v == "silent-missed" and offset == 0:
        body += "<h2>Neu eingegangen</h2>" + _table("neu-eingegangen", "".join(_row(t, c, v) for t in new))
    body += _table(c["table"], "".join(_row(t, c, v) for t in chunk))
    more = offset + PAGE_SIZE < len(shown)
    if v == "pagination":
        pages = "".join(f"<a href='?start={o}'>{o // PAGE_SIZE + 1}</a> " for o in range(0, len(tenders), PAGE_SIZE))
        body += f"<nav class='pager'>{pages}</nav>"
    elif more:
        body += f"<nav><a rel='next' href='/ssr/ausschreibungen?seite={offset // PAGE_SIZE + 2}'>Weiter</a></nav>"
    return page("Ausschreibungen", body, lang="de")


@router.get("/ssr/ausschreibung/{rest:path}", response_class=HTMLResponse)
async def detail(request: Request, rest: str):
    tid = rest.split(";", 1)[0]  # strip ;jsessionid=…
    v = variant(request, KIND)
    t = next((t for t in data.visible(KIND, v) if t.id == tid), None)
    if t is None:
        raise HTTPException(404)
    c = _cls(v)
    cpv = "".join(f"<li>{cpv_with_check_digit(x)}</li>" for x in t.cpv)
    if t.docs_restricted:
        docs = "<p class='hinweis'>Die Vergabeunterlagen stehen nur registrierten Unternehmen zur Verfügung.</p>"
    else:
        docs = (
            "<ul class='documents'>" + "".join(f"<li><a href='{d.path}'>{e(d.name)}</a></li>" for d in t.docs) + "</ul>"
        )
    body = (
        f"<h1>{e(t.title)}</h1><dl>"
        f"<dt>Aktenzeichen</dt><dd>{e(t.reference)}</dd>"
        f"<dt>Vergabestelle</dt><dd>{e(t.buyer)}</dd>"
        f"<dt>{c['deadline']}</dt><dd>{t.deadline:%d.%m.%Y %H:%M}</dd>"
        f"<dd class='hinweis'>Alle Zeiten in Ortszeit (Europe/Berlin)</dd>"
        f"<dt>Verfahrensart</dt><dd>{PROCEDURE[t.procedure]}</dd>"
        f"<dt>Art des Auftrags</dt><dd>{NATURE[t.nature]}</dd>"
        f"<dt>Status</dt><dd>{STATUS[t.status]}</dd></dl>"
        f"<section class='description'>{e(t.description)}</section>"
        f"<h2>CPV</h2><ul class='cpv'>{cpv}</ul>"
        f"<p class='erfuellungsort'>Erfüllungsort: <span class='plz'>{e(t.postal_code)}</span> "
        f"<span class='ort'>{e(t.city)}</span></p>"
        f"<h2>Vergabeunterlagen</h2>{docs}"
    )
    return page(t.title, body, lang="de")
