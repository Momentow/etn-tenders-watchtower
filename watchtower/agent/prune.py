"""Turn pages into compact text the model can reason about cheaply (ADR 0002: HTML is pruned before the model sees it).

- `outline(html)`: the element tree as indented `tag#id.class [attrs] "text"` lines, built on the same parse as the
  engine (`engine.extract.parse_html`), so a selector that matches in the outline matches in the engine.
  Scripts, styles, SVG and the head are dropped; single-child wrapper chains are joined (`div.a > div.b`); runs of
  same-shaped siblings show the first `KEEP_REPEATS` in full and a `… ×N more` line (a tender table shows two rows).
- `focus=<css>`: outline only the matches of a selector, with their count. This is how the model zooms in and checks a
  selector before putting it in a template.
- `summarize_json(value)`: key shape with sample values and array lengths, plus the arrays of objects found (candidate
  tender lists) with their JSONPath.
"""

from __future__ import annotations

import json
import re
from typing import Any

from watchtower.engine.extract import HtmlScope, collapse, css, parse_html

KEEP_REPEATS = 2
TEXT_CHARS = 80
ATTR_CHARS = 90
DEFAULT_MAX_CHARS = 9000
MAX_DEPTH = 40

_DROP = frozenset(
    {"script", "style", "noscript", "svg", "template", "link", "meta", "head", "iframe", "canvas", "path"}
)
# Siblings that each mean something different even when they look alike (columns of a row): never collapsed.
_NEVER_COLLAPSE = frozenset({"td", "th", "dt", "dd", "option"})
_INLINE = frozenset({"span", "b", "strong", "em", "i", "small", "font", "u", "abbr", "sup", "sub", "br", "wbr", "mark"})
_KEEP_ATTRS = ("href", "action", "method", "name", "type", "value", "rel", "role", "for", "src", "datetime", "title")
_HIDDEN_VALUE_CHARS = 20


def outline(html: str, base_url: str = "", *, focus: str | None = None, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Pruned outline of a page, or of the matches of the CSS selector `focus` (with their count)."""
    scope = parse_html(html, base_url)
    lines: list[str] = []
    if focus:
        try:
            matches = css(focus, scope.el)
        except Exception as e:  # cssselect raises several error types for bad selectors
            return f"Invalid CSS selector {focus!r}: {e}"
        lines.append(f"focus {focus!r}: {len(matches)} match(es)")
        for i, el in enumerate(matches[: KEEP_REPEATS + 1]):
            lines.append(f"--- match {i + 1}")
            _walk(el, 0, lines)
        if len(matches) > KEEP_REPEATS + 1:
            lines.append(f"… {len(matches) - KEEP_REPEATS - 1} more matches")
    else:
        lines.extend(page_header(scope))
        body = scope.el.find("body")
        _walk(body if body is not None else scope.el, 0, lines)
    return _cap("\n".join(lines), max_chars, hint="use focus=<css> to zoom into one part")


def page_header(scope: HtmlScope) -> list[str]:
    """Title, generator and pagination hints from the head (dropped from the outline itself)."""
    out = []
    root = scope.el
    title = root.find(".//title")
    if title is not None and (t := collapse(title.text_content())):
        out.append(f"title: {_short(t, 120)}")
    for meta in root.iterfind(".//meta[@name]"):
        if meta.get("name", "").lower() == "generator" and meta.get("content"):
            out.append(f"generator: {_short(meta.get('content'), 80)}")
    for link in root.iterfind(".//link[@rel]"):
        if link.get("rel", "").lower() in ("next", "prev", "canonical") and link.get("href"):
            out.append(f"link rel={link.get('rel')}: {_short(link.get('href'), ATTR_CHARS)}")
    return out


def visible_text(html: str, max_chars: int = 2000) -> str:
    """The page's text, whitespace collapsed (for non-structural checks such as error or login pages)."""
    scope = parse_html(html, "")
    for el in list(scope.el.iter(*_DROP)):
        if el.getparent() is not None:
            el.drop_tree()
    return _cap(collapse(scope.el.text_content()), max_chars)


# --------------------------------------------------------------------------- outline internals


def _walk(el: Any, depth: int, lines: list[str]) -> None:
    if depth > MAX_DEPTH:
        lines.append("  " * depth + "…")
        return
    # Join chains of wrappers with one element child and no text of their own: `div.page > main > div.content`.
    chain = [_label(el)]
    while True:
        kids = _kids(el)
        if len(kids) == 1 and not _own_text(el) and not _is_leaf(el):
            el = kids[0]
            chain.append(_label(el))
        else:
            break
    head = "  " * depth + " > ".join(chain)
    if _is_leaf(el):
        text = collapse(el.text_content())
        lines.append(head + (f' "{_short(text, TEXT_CHARS)}"' if text else ""))
        return
    own = _own_text(el)
    lines.append(head + (f' "{_short(own, TEXT_CHARS)}"' if own else ""))
    kids = _kids(el)
    i = 0
    while i < len(kids):
        sig = _signature(kids[i])
        j = i + 1
        while j < len(kids) and _signature(kids[j]) == sig and kids[i].tag not in _NEVER_COLLAPSE:
            j += 1
        run = j - i
        for k in kids[i : i + (run if run <= KEEP_REPEATS + 1 else KEEP_REPEATS)]:
            _walk(k, depth + 1, lines)
        if run > KEEP_REPEATS + 1:
            lines.append("  " * (depth + 1) + f"… ×{run - KEEP_REPEATS} more {sig}")
        i = j


def _kids(el: Any) -> list[Any]:
    return [c for c in el if isinstance(c.tag, str) and c.tag not in _DROP and not _hidden(c)]


def _hidden(el: Any) -> bool:
    style = (el.get("style") or "").replace(" ", "").lower()
    return "display:none" in style and el.tag not in ("form", "input")


def _is_leaf(el: Any) -> bool:
    """No structure worth showing below: only text and plain inline markup."""
    for c in el.iter():
        if c is el or not isinstance(c.tag, str):
            continue
        if c.tag not in _INLINE or c.get("class") or c.get("id"):
            return False
    return True


def _own_text(el: Any) -> str:
    parts = [el.text or ""] + [c.tail or "" for c in el if isinstance(c.tag, str)]
    return collapse(" ".join(parts))


def _signature(el: Any) -> str:
    cls = ".".join(sorted((el.get("class") or "").split()))
    return el.tag + (f".{cls}" if cls else "")


def _label(el: Any) -> str:
    s = el.tag
    if el.get("id"):
        s += f"#{el.get('id')}"
    if cls := (el.get("class") or "").split():
        s += "." + ".".join(cls)
    attrs = []
    for name in _KEEP_ATTRS:
        v = el.get(name)
        if v is None or name == "title" and el.tag != "a":
            continue
        if name == "value" and (el.get("type") or "").lower() == "hidden":
            v = _short(v, _HIDDEN_VALUE_CHARS)
        attrs.append(f"{name}={_short(v, ATTR_CHARS)}")
    for name, v in el.attrib.items():
        if name.startswith("data-") and len(attrs) < 8:
            attrs.append(f"{name}={_short(v, 40)}")
    if attrs:
        s += "[" + " ".join(attrs) + "]"
    return s


# --------------------------------------------------------------------------- JSON


def summarize_json(value: Any, *, max_chars: int = 4000) -> str:
    """Shape with sample values, then the arrays of objects (likely tender lists) with JSONPath and length."""
    lines = ["shape: " + _shape(value, 0)]
    arrays = list(_object_arrays(value, "$"))
    if arrays:
        lines.append("arrays of objects:")
        for path, n, keys in sorted(arrays, key=lambda a: -a[1])[:8]:
            lines.append(f"  {path}[*]  ({n} items; keys: {', '.join(keys[:25])})")
    return _cap("\n".join(lines), max_chars)


def _shape(v: Any, depth: int) -> str:
    if isinstance(v, dict):
        if depth > 4:
            return "{…}"
        inner = ", ".join(
            f"{json.dumps(k, ensure_ascii=False)}: {_shape(x, depth + 1)}" for k, x in list(v.items())[:30]
        )
        more = f", …+{len(v) - 30} keys" if len(v) > 30 else ""
        return "{" + inner + more + "}"
    if isinstance(v, list):
        if not v:
            return "[]"
        return f"[{len(v)} × {_shape(v[0], depth + 1)}]"
    if isinstance(v, str):
        return json.dumps(_short(v, 50), ensure_ascii=False)
    return json.dumps(v)


def _object_arrays(v: Any, path: str, depth: int = 0):
    if depth > 6:
        return
    if isinstance(v, dict):
        for k, x in v.items():
            key = f"{path}.{k}" if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) else f"{path}['{k}']"
            yield from _object_arrays(x, key, depth + 1)
    elif isinstance(v, list) and v and isinstance(v[0], dict):
        keys = list(dict.fromkeys(k for o in v[:5] if isinstance(o, dict) for k in o))
        yield path, len(v), keys
        yield from _object_arrays(v[0], f"{path}[0]", depth + 1)


# --------------------------------------------------------------------------- helpers


def _short(s: str, n: int) -> str:
    s = collapse(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def _cap(text: str, max_chars: int, hint: str = "") -> str:
    if len(text) <= max_chars:
        return text
    cut = text.rfind("\n", 0, max_chars)
    cut = cut if cut > max_chars // 2 else max_chars
    note = f"\n[truncated: {len(text) - cut} more chars" + (f"; {hint}" if hint else "") + "]"
    return text[:cut] + note
