"""Discovery on an HTML page: candidate tender lists (repeating blocks) and what each link is (detail, document, page).

Both work on the engine's parse (`HtmlScope`), and every selector they suggest is verified against it.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlsplit

from watchtower.agent.analysis.selectors import common_sig, group_selector, relative_selector, stable_classes
from watchtower.engine.extract import HtmlScope, collapse

_SKIP = frozenset({"script", "style", "noscript", "svg", "template", "head", "select", "option"})
_CHROME = frozenset({"nav", "header", "footer", "menu"})
_DATE = re.compile(r"\b\d{1,4}[./-]\d{1,2}[./-]\d{2,4}\b|\b\d{1,2}\.? [A-Za-zÀ-ÿ]{3,}\.? \d{4}\b")
MIN_ITEMS = 3

# --------------------------------------------------------------------------- repeating blocks


@dataclass
class Block:
    selector: str | None
    count: int
    score: float
    with_links: int
    with_dates: int
    avg_text: int
    fields: list[tuple[str, str, str | None]] = field(default_factory=list)  # (relative selector, text, href)


def find_repeating_blocks(scope: HtmlScope, limit: int = 4) -> list[Block]:
    """Groups of ≥3 same-shaped siblings that look like records (text, links, dates), best first."""
    root = scope.el
    groups: list[list[Any]] = []
    for parent in root.iter():
        if not isinstance(parent.tag, str) or parent.tag in _SKIP:
            continue
        by_shape: dict[str, list[Any]] = defaultdict(list)
        for child in parent:
            if isinstance(child.tag, str) and child.tag not in _SKIP:
                by_shape[child.tag + "." + ".".join(sorted(stable_classes(child)))].append(child)
        groups += [g for g in by_shape.values() if len(g) >= MIN_ITEMS]

    blocks = []
    for g in groups:
        texts = [collapse(" ".join(e.itertext())) for e in g]
        avg = sum(map(len, texts)) / len(g)
        links = sum(1 for e in g if e.find(".//a[@href]") is not None or (e.tag == "a" and e.get("href")))
        dates = sum(1 for t in texts if _DATE.search(t))
        distinct = len(set(texts)) / len(g)
        if avg < 12 or distinct < 0.5:
            continue
        score = len(g) * (0.3 + links / len(g)) * (1 + dates / len(g)) * min(avg / 40, 2.0) * distinct
        if _in_chrome(g[0]):
            score *= 0.2
        if g[0].tag in ("li", "a", "option") and not dates:
            score *= 0.5  # menus and link lists
        blocks.append((score, g, links, dates, int(avg)))
    blocks.sort(key=lambda b: -b[0])

    out: list[Block] = []
    seen: set[int] = set()
    for score, g, links, dates, avg in blocks:
        if id(g[0]) in seen:
            continue
        seen.update(id(e) for e in g)
        out.append(
            Block(
                selector=group_selector(g, root),
                count=len(g),
                score=round(score, 1),
                with_links=links,
                with_dates=dates,
                avg_text=avg,
                fields=item_fields(g[0]),
            )
        )
        if len(out) >= limit:
            break
    return out


def item_fields(item: Any, limit: int = 14) -> list[tuple[str, str, str | None]]:
    """The values inside one record: (selector relative to the item, text, href)."""
    out: list[tuple[str, str, str | None]] = []
    for el in item.iter():
        if el is item or not isinstance(el.tag, str) or el.tag in _SKIP:
            continue
        has_element_kids = any(isinstance(c.tag, str) and c.tag not in ("br", "wbr") for c in el)
        text = collapse(el.text_content())
        href = el.get("href") if el.tag == "a" else None
        if (has_element_kids and el.tag != "a") or not (text or href):
            continue
        out.append((relative_selector(item, el), text, href))
        if len(out) >= limit:
            break
    for attr in ("data-id", "data-ref", "id"):
        if item.get(attr):
            out.insert(0, (f"(the item's own `{attr}` attribute; css on the item tag with attr)", item.get(attr), None))
            break
    return out


def _in_chrome(el: Any) -> bool:
    node = el
    while node is not None:
        if node.tag in _CHROME or (node.get("role") or "") in ("navigation", "banner", "contentinfo"):
            return True
        node = node.getparent()
    return False


# --------------------------------------------------------------------------- links

_DOC_EXT = re.compile(
    r"\.(pdf|zip|7z|rar|docx?|xlsx?|odt|ods|rtf|txt|xml|dwg|dxf|p7m|p7s|xsig|gaeb|x8[0-9]|csv)(\?|$|#)", re.I
)
_DOC_WORDS = re.compile(
    r"download|herunterladen|unterlagen|vergabeunterlagen|ausschreibungsunterlagen|anlage|dokument|document|t[ée]l[ée]charg"
    r"|\bdce\b|pi[èe]ces?|dossier de consultation|descarga|pliego|documentaci[óo]n|anexo|pobierz|dokumentacja|\bswz\b"
    r"|za[łl][ąa]cznik|bijlage|documenten|downloaden|allegat|scarica|\bfile\b|attachment|anhang",
    re.I,
)
_PAGE_PARAMS = frozenset({"page", "pagina", "seite", "strona", "pag", "p", "pg", "start", "offset", "skip", "from"})
_ID_SEGMENT = re.compile(r"\d|^[A-Za-z0-9_-]{16,}$")


@dataclass
class LinkGroup:
    kind: str  # detail | document | pagination | other
    pattern: str  # URL pattern, ids as {}
    selector: str | None
    count: int
    links: list[tuple[str, str]]  # samples: (text, absolute url)


def classify_links(scope: HtmlScope) -> list[LinkGroup]:
    """Every link on the page, grouped: tender detail pages, documents, pagination, everything else."""
    anchors = [a for a in scope.el.iter("a") if a.get("href") and not a.get("href").startswith(("#", "javascript:"))]
    by_pattern: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for a in anchors:
        url = urljoin(scope.base_url, a.get("href"))
        kind = _kind_of(a, url)
        by_pattern[(kind, _pattern(url, file_last=kind == "document"))].append(a)

    groups: list[LinkGroup] = []
    other: list[Any] = []
    for (kind, pattern), els in by_pattern.items():
        if kind == "other" and len(els) >= MIN_ITEMS and "{}" in pattern:
            kind = "detail"
        if kind == "other":
            other += els
            continue
        groups.append(LinkGroup(kind, pattern, _link_selector(els, scope), len(els), _samples(els, scope)))
    if other:
        groups.append(LinkGroup("other", "(various)", None, len(other), _samples(other, scope, 8)))
    order = {"detail": 0, "document": 1, "pagination": 2, "other": 3}
    groups.sort(key=lambda g: (order[g.kind], -g.count))
    return groups


def _kind_of(a: Any, url: str) -> str:
    text = collapse(a.text_content())
    parts = urlsplit(url)
    if a.get("download") is not None or _DOC_EXT.search(parts.path):
        return "document"
    # Words alone ("Descarga", "Downloads") also name menu entries: only count them in the page's content.
    if _DOC_WORDS.search(parts.path + " " + text) and parts.path.strip("/") and not _in_chrome(a):
        return "document"
    params = {k.lower() for k, _ in parse_qsl(parts.query)}
    if (a.get("rel") or "").lower() == "next" or (params & _PAGE_PARAMS and (text.isdigit() or len(text) <= 12)):
        return "pagination"
    return "other"


def _pattern(url: str, *, file_last: bool = False) -> str:
    parts = urlsplit(url)
    segs = [("{}" if _ID_SEGMENT.search(s) else s) for s in parts.path.split("/")]
    if file_last and len(segs) > 1:
        segs[-1] = "{file}"  # documents of one tender share a folder, not a name
    segs = [re.sub(r";jsessionid=.*", "", s, flags=re.I) for s in segs]
    query = "&".join(f"{k}={{}}" if _ID_SEGMENT.search(v) else f"{k}={v}" for k, v in parse_qsl(parts.query))
    return f"{parts.netloc}{'/'.join(segs)}" + (f"?{query}" if query else "")


def _link_selector(els: list[Any], scope: HtmlScope) -> str | None:
    sel = group_selector(els, scope.el)
    if sel is None and len(els) > 1:
        sel = common_sig(els) + " (not unique on the page)"
    return sel


def _samples(els: list[Any], scope: HtmlScope, n: int = 3) -> list[tuple[str, str]]:
    return [(collapse(a.text_content())[:60], urljoin(scope.base_url, a.get("href"))) for a in els[:n]]
