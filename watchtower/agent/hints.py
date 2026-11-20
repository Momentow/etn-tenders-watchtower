"""Deterministic hints the dry run adds to its report, so the model doesn't have to spot them itself."""

from __future__ import annotations

import json
import re
from typing import Any

from watchtower.engine.extract import collapse, parse_html

_NEXT_TEXT = re.compile(
    r"^(next|next page|weiter|nächste( seite)?|vor|siguiente|suivante?|page suivante|następna|dalej|volgende"
    r"|successiva|avanti|próxima|seuraava|nästa|další|›|»|>|>>|→)$",
    re.I,
)
_PAGE_PARAM = re.compile(
    r"^(page|pagina|seite|strona|pag|p|start|offset|from|skip|pageno|pagenumber|page_number)$", re.I
)
_PAGE_IN_URL = re.compile(r"[?&;](page|pagina|seite|strona|pag|p|start|offset|from|skip|pageno)=\d", re.I)
_TOTAL_KEYS = re.compile(
    r"^(total|totalcount|total_count|totalelements|totalitems|count|nbresults|numfound|hits)$", re.I
)
_NEXT_KEYS = re.compile(r"^(next|nextpage|next_page|nexturl|cursor|nextcursor|next_cursor|continuation)$", re.I)
MAX_HINTS = 4


def pagination_hints(text: str, url: str = "", items_on_page: int | None = None) -> list[str]:
    """Signs that a list page has more pages: next links, numbered page controls, page fields, JSON totals/cursors."""
    stripped = text.lstrip()
    if stripped[:1] in ("{", "["):
        try:
            return _json_hints(json.loads(stripped), items_on_page)
        except ValueError:
            pass
    return _html_hints(text, url)


def _html_hints(text: str, url: str) -> list[str]:
    root = parse_html(text, url).el
    hints: list[str] = []
    for el in root.iter("a", "link", "button", "input"):
        label = collapse(el.text_content()) if el.tag in ("a", "button") else ""
        href = el.get("href") or ""
        name = el.get("name") or ""
        rel = (el.get("rel") or "").lower()
        aria = (el.get("aria-label") or el.get("title") or "").strip()
        if rel == "next" and el.tag in ("a", "link"):
            hints.append(f'<{el.tag} rel="next" href="{href[:80]}">')
        elif el.tag in ("a", "button") and (_NEXT_TEXT.match(label) or _NEXT_TEXT.match(aria)):
            hints.append(f"<{el.tag}> {label or aria!r}" + (f" href={href[:80]}" if href else "") + _named(el))
        elif el.tag in ("a", "button") and label.isdigit() and 2 <= int(label) <= 9 and (href or name):
            hints.append(f"numbered page control <{el.tag}> {label!r}" + (f" href={href[:80]}" if href else _named(el)))
        elif el.tag == "a" and _PAGE_IN_URL.search(href):
            hints.append(f"link to a page: {href[:80]}")
        elif el.tag == "input" and _PAGE_PARAM.match(name):
            hints.append(f'form field name="{name}" value="{(el.get("value") or "")[:20]}"')
        if len(hints) >= MAX_HINTS:
            break
    return list(dict.fromkeys(hints))


def _named(el: Any) -> str:
    name = el.get("name")
    return f' name="{name}" value="{el.get("value") or ""}"' if name else ""


def _json_hints(value: Any, items_on_page: int | None) -> list[str]:
    hints: list[str] = []
    if not isinstance(value, dict):
        return hints
    for k, v in value.items():
        if _TOTAL_KEYS.match(k) and isinstance(v, int) and (items_on_page is None or v > items_on_page):
            hints.append(f'"{k}": {v}' + (f" (but {items_on_page} items on this page)" if items_on_page else ""))
        elif _NEXT_KEYS.match(k) and v:
            hints.append(f'"{k}": {json.dumps(v)[:80]}')
        elif isinstance(v, dict):
            hints += [f"{k}.{h}" for h in _json_hints(v, items_on_page)]
    return hints[:MAX_HINTS]
