"""Structural snapshot of a list page, stored with each good read so repair can diff the live page against it (T-022
`diff_structure`).

- HTML: an indented outline of the element tree (`tag#id.class`), runs of same-shaped siblings collapsed to one line
  with a count (`tr.row ×10`), scripts/styles dropped.
  Text is left out: it changes on every read, the structure doesn't.
- JSON: the key shape (`{"resultats": [{"id": "str", …}], "total": "int"}`), arrays reduced to their first element.
"""

from __future__ import annotations

from typing import Any

from watchtower.engine.extract import HtmlScope, JsonScope

MAX_DEPTH = 25
MAX_LINES = 400
_SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "template", "head"})


def snapshot(scope: HtmlScope | JsonScope, url: str) -> dict[str, Any]:
    if isinstance(scope, JsonScope):
        return {"kind": "json", "url": url, "shape": json_shape(scope.value)}
    lines: list[str] = []
    _outline(scope.el, 0, lines)
    return {"kind": "html", "url": url, "outline": "\n".join(lines[:MAX_LINES])}


def json_shape(v: Any, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        return "…"
    if isinstance(v, dict):
        return {k: json_shape(x, depth + 1) for k, x in v.items()}
    if isinstance(v, list):
        return [json_shape(v[0], depth + 1)] if v else []
    if v is None:
        return "null"
    return type(v).__name__


def _signature(el: Any) -> str:
    sig = el.tag
    if el.get("id"):
        sig += "#" + el.get("id")
    classes = sorted((el.get("class") or "").split())
    if classes:
        sig += "." + ".".join(classes[:4])
    return sig


def _children(el: Any) -> list[Any]:
    return [c for c in el if isinstance(c.tag, str) and c.tag not in _SKIP_TAGS]


def _outline(el: Any, depth: int, lines: list[str]) -> None:
    if depth > MAX_DEPTH or len(lines) >= MAX_LINES:
        return
    lines.append("  " * depth + _signature(el))
    kids = _children(el)
    i = 0
    while i < len(kids):
        sig = _signature(kids[i])
        j = i
        while j + 1 < len(kids) and _signature(kids[j + 1]) == sig:
            j += 1
        if j > i:
            lines.append("  " * (depth + 1) + f"{sig} ×{j - i + 1}")
            for k in _children(kids[i]):  # the first of the run stands for all of them
                _outline(k, depth + 2, lines)
        else:
            _outline(kids[i], depth + 1, lines)
        i = j + 1
