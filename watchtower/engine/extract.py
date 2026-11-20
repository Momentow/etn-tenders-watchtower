"""Running `Extract` rules (ADR 0003) against a scope: an HTML element/page or a JSON value.

HTML is parsed with lexbor (an HTML5 parser, so the tree matches what a browser and the agent see, e.g. inserted
`<tbody>`) and re-parsed into lxml, so CSS (via cssselect) and XPath run on the same tree and a template may mix them.
CSS selectors also match the scope element itself (`article.x` with `attr: data-id` on an `article.x` item).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import urljoin

import lxml.html
from cssselect import HTMLTranslator
from jsonpath_ng.ext import parse as jsonpath_parse
from lxml import etree
from selectolax.lexbor import LexborHTMLParser

from watchtower.engine.template import Extract

URL_ATTRS = frozenset({"href", "src", "action", "data-href", "data-url"})
_NOT_A_LINK = re.compile(r"^(javascript:|mailto:|tel:|data:|#)", re.I)


class ExtractError(Exception):
    """A rule can't be applied to this scope (e.g. list.items selected text instead of elements)."""


@dataclass
class HtmlScope:
    el: Any  # lxml HtmlElement: the whole document or one list item
    base_url: str

    @property
    def text(self) -> str:
        return collapse(self.el.text_content())


@dataclass
class JsonScope:
    value: Any
    base_url: str

    @property
    def text(self) -> str:
        return json.dumps(self.value, ensure_ascii=False)


Scope = HtmlScope | JsonScope


def parse_html(text: str, base_url: str) -> HtmlScope:
    normalized = LexborHTMLParser(text).html or "<html></html>"
    try:
        root = lxml.html.document_fromstring(normalized)
    except ValueError:  # str with an XML encoding declaration
        root = lxml.html.document_fromstring(normalized.encode("utf-8"), parser=_UTF8_PARSER)
    return HtmlScope(root, base_url)


def parse_json(text: str, base_url: str) -> JsonScope:
    return JsonScope(json.loads(text), base_url)


_UTF8_PARSER = lxml.html.HTMLParser(encoding="utf-8")


def collapse(s: str) -> str:
    return " ".join(s.split())


# --------------------------------------------------------------------------- selecting


@lru_cache(maxsize=1024)
def _css_xpath(expr: str) -> etree.XPath:
    return etree.XPath(HTMLTranslator().css_to_xpath(expr, prefix="descendant-or-self::"))


@lru_cache(maxsize=1024)
def _xpath(expr: str) -> etree.XPath:
    return etree.XPath(expr)


@lru_cache(maxsize=1024)
def _jsonpath(expr: str) -> Any:
    return jsonpath_parse(expr)


@lru_cache(maxsize=1024)
def _regex(expr: str) -> re.Pattern[str]:
    return re.compile(expr)


def css(expr: str, el: Any) -> list[Any]:
    return [x for x in _css_xpath(expr)(el) if isinstance(x, etree._Element)]


def select(ex: Extract, scope: Scope) -> list[Scope]:
    """One sub-scope per match: list items, the tender objects of a JSON list."""
    if isinstance(scope, HtmlScope):
        if ex.by == "css":
            found = _css_xpath(ex.expr)(scope.el)
        elif ex.by == "xpath":
            found = _xpath(ex.expr)(scope.el)
        else:
            raise ExtractError(f"`{ex.by}` can't select HTML elements; use css or xpath")
        if not isinstance(found, list) or any(not isinstance(x, etree._Element) for x in found):
            raise ExtractError(f"{ex.by} {ex.expr!r} must select elements, not text or attributes")
        return [HtmlScope(x, scope.base_url) for x in found if not _is_comment(x)]
    if ex.by != "jsonpath":
        raise ExtractError(f"`{ex.by}` can't select JSON objects; use jsonpath")
    out: list[Scope] = []
    for match in _jsonpath(ex.expr).find(scope.value):
        if isinstance(match.value, list) and not ex.expr.rstrip().endswith("]"):
            out.extend(JsonScope(v, scope.base_url) for v in match.value)  # `$.items` given instead of `$.items[*]`
        else:
            out.append(JsonScope(match.value, scope.base_url))
    return out


def _is_comment(el: Any) -> bool:
    return not isinstance(el.tag, str)


# --------------------------------------------------------------------------- values


def values(ex: Extract, scope: Scope, *, url: bool = False) -> list[str]:
    """Every value the rule yields, after `pattern` and `map` (`join` is not applied).

    `url=True` (links, document URLs, the tender URL): values are made absolute and non-links are dropped.
    """
    raw = _raw(ex, scope)
    out: list[str] = []
    for v in raw:
        v = v.strip()
        if ex.pattern is not None:
            m = _regex(ex.pattern).search(v)
            if m is None:
                continue
            v = (m.group(1) if m.re.groups else m.group(0)) or ""
            v = v.strip()
        if ex.map is not None:
            if v not in ex.map:
                continue
            v = ex.map[v]
        if url or (ex.attr in URL_ATTRS and ex.by in ("css", "xpath")):
            if not v or _NOT_A_LINK.match(v):
                continue
            v = urljoin(scope.base_url, v)
        if v:
            out.append(v)
    return out


def value(ex: Extract, scope: Scope, *, url: bool = False) -> str | list[str] | None:
    """The rule's result: the first value, or for `many` the list (or one joined string with `join`)."""
    found = values(ex, scope, url=url)
    if not ex.many:
        return found[0] if found else None
    if ex.join is not None:
        return ex.join.join(found) if found else None
    return found


def first(ex: Extract, scope: Scope, *, url: bool = False) -> str | None:
    found = values(ex, scope, url=url)
    return found[0] if found else None


def _raw(ex: Extract, scope: Scope) -> list[str]:
    if ex.by == "const":
        return [ex.expr]
    if ex.by == "regex":
        pattern = _regex(ex.expr)
        return [m.group(1) if pattern.groups else m.group(0) for m in pattern.finditer(scope.text)]
    if isinstance(scope, HtmlScope):
        if ex.by == "css":
            found = _css_xpath(ex.expr)(scope.el)
        elif ex.by == "xpath":
            found = _xpath(ex.expr)(scope.el)
        else:
            raise ExtractError(f"`{ex.by}` can't read HTML")
        if not isinstance(found, list):  # xpath number/bool/string functions
            found = [found]
        return [s for x in found if (s := _html_value(x, ex.attr)) is not None]
    if ex.by != "jsonpath":
        raise ExtractError(f"`{ex.by}` can't read JSON")
    return [s for m in _jsonpath(ex.expr).find(scope.value) if (s := _json_value(m.value)) is not None]


def _html_value(x: Any, attr: str) -> str | None:
    if isinstance(x, etree._Element):
        if _is_comment(x):
            return None
        if attr == "text":
            return collapse(x.text_content())
        if attr == "html":
            return etree.tostring(x, encoding="unicode", method="html", with_tail=False)
        return x.get(attr)
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return collapse(str(x))


def _json_value(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return str(v)
    return json.dumps(v, ensure_ascii=False)
