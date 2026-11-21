"""Evidence the engine records for completeness checks (T-036, `validate.check`): deterministic, no network.

- `more_offered(scope, items)`: on a list read with **no pagination**, the controls or counts showing the portal has
  more than this page: a "next" or numbered page control, an "all tenders" / "load more" link, `rel=next`, or a JSON
  total larger than the page. Controls inside the items themselves don't count (a tender's own "more" link).
  This is the evergabe.nrw.de case: a home-page "latest 20" widget read as if it were every open tender.
- `other_notice(scope)`: the label marking a list item as a notice that is not a call for competition (an award, a
  prior information notice, an early termination, a market consultation…), or None. Only short texts are looked at
  (labels, badges, type fields), never descriptions, so a call that merely mentions an award isn't flagged.
"""

from __future__ import annotations

import re
from typing import Any

from watchtower.engine.extract import HtmlScope, JsonScope, Scope, collapse

MAX_EVIDENCE = 4
LABEL_MAX_CHARS = 90

_NEXT_TEXT = re.compile(
    r"^(next|next page|weiter|nächste( seite)?|siguiente|suivante?|page suivante|następna|dalej|volgende"
    r"|successiva|avanti|próxima|›|»|>>|→)$",
    re.I,
)
_TENDER_NOUNS = (
    r"(ausschreibung|vergabe|bekanntmachung|veröffentlichung|tender|notice|opportunit|procurement|aankondiging"
    r"|aanbested|opdracht|publicatie|consultation|marchés?|avis|annonce|licitaci|anuncio|contrat|przetarg|ogłosze"
    r"|postępowa|zamówie|gar[ae]|bandi|appalt|procedur|projekt|project|result|ergebnis|resultat|treffer)"
)
_MORE_TEXT = re.compile(
    r"^(("
    r"show more|load more|see more|view more|laad meer|meer laden|toon meer|meer resultaten|mehr anzeigen|mehr laden"
    r"|weitere laden|weitere anzeigen|voir plus|afficher plus|charger plus|ver más|cargar más|mostrar más|pokaż więcej"
    r"|załaduj więcej|mostra altri|carica altri|show all|view all|see all|alle anzeigen|alle ansehen|zur übersicht"
    r"|tout voir|voir tout|voir toutes?|ver todos?|ver todas|bekijk alle|toon alle|pokaż wszystkie"
    r"|vedi tutti|vedi tutte"
    r")\b.{0,40}"
    r"|(alle|all|toutes? les|tous les|todos los|todas las|wszystkie|tutti i|tutte le|weitere|more|meer|plus de|más)\s"
    r".{0,30}" + _TENDER_NOUNS + r".{0,30})$",
    re.I,
)
_TOTAL_KEYS = re.compile(
    r"^(total|totalcount|total_count|totalelements|totalitems|totalresults|total_results|nbresults|numfound|hits)$",
    re.I,
)
_NEXT_KEYS = re.compile(r"^(next|nextpage|next_page|nexturl|next_url|nextcursor|next_cursor|continuation)$", re.I)

# Notice types that aren't calls for competition, by language (casefolded substrings of a short label).
_OTHER_NOTICES = (
    # awards and results
    "contract award notice", "award notice", "notice of award", "aankondiging gegunde opdracht", "gegunde opdracht",
    "bekanntmachung vergebener", "vergebene aufträge", "vergebener auftrag", "zuschlagsbekanntmachung",
    "avis d'attribution", "avis d’attribution", "anuncio de adjudicación", "anuncio de formalización",
    "ogłoszenie o udzieleniu zamówienia", "ogłoszenie o wyniku", "avviso di aggiudicazione", "esito di gara",
    "esito gara", "voluntary ex ante", "vrijwillige transparantie", "freiwillige ex-ante",
    # prior information and market consultation
    "prior information notice", "vooraankondiging", "vorinformation", "avis de préinformation",
    "avis de pré-information", "anuncio de información previa", "avviso di preinformazione",
    "wstępne ogłoszenie informacyjne", "marktconsultatie", "market consultation", "markterkundung",
    # ended early
    "vroegtijdige beëindiging",
    # announced, not yet open (German "intended tender" notices, §20 VOB/A)
    "beabsichtigte ausschreibung",
)  # fmt: skip


def more_offered(scope: Scope, items: list[Scope]) -> list[str]:
    """Signs that the list holds more than this page (evidence sentences, at most 4)."""
    if isinstance(scope, JsonScope):
        return _json_more(scope.value, len(items))
    assert isinstance(scope, HtmlScope)
    # lxml reuses one proxy per element while it's referenced: `items` keeps the item elements alive, so identity works
    # for them (not for throwaway proxies of their descendants).
    item_els = {id(x.el) for x in items if isinstance(x, HtmlScope)}
    out: list[str] = []
    for el in scope.el.iter("a", "link", "button"):
        if any(id(a) in item_els for a in (el, *el.iterancestors())):
            continue
        label = collapse(el.text_content()) if el.tag != "link" else ""
        aria = (el.get("aria-label") or el.get("title") or "").strip()
        href = el.get("href") or ""
        text = label or aria
        if (el.get("rel") or "").lower() == "next":
            out.append(f'a rel="next" link to {href[:80]}')
        elif text and len(text) <= 80 and _NEXT_TEXT.match(text):
            out.append(f"a {_kind(el)} {text!r}" + (f" to {href[:80]}" if href else ""))
        elif label.isdigit() and 2 <= int(label) <= 9 and (href or el.get("name")):
            out.append(f"a page {label} {_kind(el)}" + (f" to {href[:80]}" if href else ""))
        elif text and len(text) <= 80 and _MORE_TEXT.match(text):
            out.append(f"a {_kind(el)} {text!r}" + (f" to {href[:80]}" if href else ""))
        if len(out) >= MAX_EVIDENCE:
            break
    return list(dict.fromkeys(out))


def _kind(el: Any) -> str:
    return "button" if el.tag == "button" else "link"


def _json_more(value: Any, items_on_page: int, path: str = "") -> list[str]:
    out: list[str] = []
    if not isinstance(value, dict):
        return out
    for k, v in value.items():
        key = f"{path}{k}"
        if _TOTAL_KEYS.match(k) and isinstance(v, int) and not isinstance(v, bool) and v > items_on_page:
            out.append(f'"{key}": {v} while the response holds {items_on_page}')
        elif _NEXT_KEYS.match(k) and v and isinstance(v, str | int | dict):
            out.append(f'"{key}" points to a next page')
        elif isinstance(v, dict) and not path:  # one level deep: {"page": {"totalElements": …}}
            out += _json_more(v, items_on_page, f"{k}.")
    return out[:MAX_EVIDENCE]


def other_notice(scope: Scope) -> str | None:
    """The short label in a list item that marks it as another notice type, if any."""
    for label in _labels(scope):
        low = label.casefold()
        for phrase in _OTHER_NOTICES:
            if phrase in low:
                return label
    return None


def _labels(scope: Scope) -> list[str]:
    if isinstance(scope, JsonScope):
        return [s for s in _json_strings(scope.value) if len(s) <= LABEL_MAX_CHARS]
    assert isinstance(scope, HtmlScope)
    out = []
    for el in scope.el.iter():
        if not isinstance(el.tag, str) or el.tag in ("script", "style"):
            continue
        text = collapse(el.text_content())
        if text and len(text) <= LABEL_MAX_CHARS:
            out.append(text)
        for attr in ("title", "aria-label", "data-type"):
            if (v := el.get(attr)) and len(v) <= LABEL_MAX_CHARS:
                out.append(v)
    return out


def _json_strings(v: Any, depth: int = 0) -> list[str]:
    if depth > 6:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [s for x in v.values() for s in _json_strings(x, depth + 1)]
    if isinstance(v, list):
        return [s for x in v[:20] for s in _json_strings(x, depth + 1)]
    return []


# --------------------------------------------------------------------------- declared counts (audits, T-041)

_NUM = r"(\d{1,3}(?:[.,\s  '’]\d{3})+|\d+)"
_COUNT_NOUNS = (
    r"(results?|treffer|ergebnis(?:se)?|résultats?|resultats?|resultados?|resultaten|resultaat|wyników|wyniki|risultati"
    r"|hits|records|entries|einträge|tenders|ausschreibungen|bekanntmachungen|vergaben|verfahren|consultations"
    r"|aankondigingen|opdrachten|avis|annonces|licitaciones|anuncios|expedientes|przetargi|postępowania|postępowań"
    r"|ogłoszeń|ogłoszenia|bandi|gare|notices|opportunities|procedures|procédures)"
)
_COUNT_PATTERNS = (
    # "1–10 von 20", "Showing 1 to 10 of 2,474"
    re.compile(
        _NUM + r"\s*(?:-|–|—|to|bis|à|a|tot|do|al)\s*" + _NUM + r"\s+(?:of|von|sur|de|van|z|di|aus)\s+" + _NUM + r"\b"
    ),
    # "20 Treffer", "2.474 resultaten gevonden", "33 offene Ausschreibungen"
    re.compile(r"(?:^|\s|\()" + _NUM + r"\s+(?:[^\W\d]+\s+)?" + _COUNT_NOUNS + r"\b"),
    # "Treffer: 20", "Résultats : 2 474", "Gefunden: 33"
    re.compile(
        r"(?:" + _COUNT_NOUNS[1:-1] + r"|gefunden|found|trouvés?|encontrados?|gevonden|znaleziono|trovati|total|gesamt"
        r"|insgesamt|anzahl|totaal)\s*:\s*" + _NUM + r"\s*\)?$"
    ),
)
_PER_PAGE = re.compile(
    r"pro seite|je seite|per page|par page|por página|per pagina|op pagina|na stron|anzeigen pro|weergeven per", re.I
)
_COUNT_MAX_CHARS = 80
_NOT_TEXT = ("script", "style", "option", "select", "textarea", "head", "title")


def declared_counts(scope: Scope, items: list[Scope]) -> list[tuple[int, str]]:
    """The portal's own counts of what its list holds, found on the first list page: (count, evidence), distinct counts.

    JSON: a total key (`total`, `totalElements`, …) at the top or one level deep. HTML: a short text outside the list
    items such as "20 Treffer", "Ergebnisse: 20" or "1–10 von 20"; "10 per page" choices and form options don't count.
    Used by proactive audits for templates that declare no `list.total` (v1). More than one distinct count means the
    page is ambiguous; the caller decides.
    """
    found: dict[int, str] = {}
    if isinstance(scope, JsonScope):
        for key, n in _json_totals(scope.value):
            found.setdefault(n, f'"{key}": {n}')
        return list(found.items())[:MAX_EVIDENCE]
    assert isinstance(scope, HtmlScope)
    item_els = {id(x.el) for x in items if isinstance(x, HtmlScope)}
    # Elements holding list items (the table, the list) read the items' text too. `holders` keeps their proxies alive so
    # their ids stay valid while the page is walked (see `more_offered`).
    holders = [a for x in items if isinstance(x, HtmlScope) for a in x.el.iterancestors()]
    holder_ids = {id(a) for a in holders}
    for el in scope.el.iter():
        if not isinstance(el.tag, str) or el.tag in _NOT_TEXT or id(el) in holder_ids:
            continue
        text = collapse(el.text_content())
        if not text or len(text) > _COUNT_MAX_CHARS or not any(c.isdigit() for c in text) or _PER_PAGE.search(text):
            continue
        if any(id(a) in item_els or a.tag in ("select", "option") for a in (el, *el.iterancestors())):
            continue
        n = _claimed(text)
        if n is not None:
            found.setdefault(n, text)
        if len(found) >= MAX_EVIDENCE:
            break
    return list(found.items())


def _claimed(text: str) -> int | None:
    low = text.casefold()
    for i, rx in enumerate(_COUNT_PATTERNS):
        m = rx.search(low)
        if m:
            return _int(m.group(3) if i == 0 else m.group(1))
    return None


def _int(s: str) -> int:
    return int(re.sub(r"\D", "", s))


def _json_totals(value: Any, path: str = "") -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    if not isinstance(value, dict):
        return out
    for k, v in value.items():
        if _TOTAL_KEYS.match(k) and isinstance(v, int) and not isinstance(v, bool) and v >= 0:
            out.append((f"{path}{k}", v))
        elif isinstance(v, dict) and not path:
            out += _json_totals(v, f"{k}.")
    return out
