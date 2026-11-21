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
