"""Building CSS selectors the engine can use: for a group of similar elements, and from an item to a value inside it.

Every selector is checked with the engine's own CSS implementation (`engine.extract.css`), so what the model is told
matches what a template will do.
"""

from __future__ import annotations

import re
from typing import Any

from watchtower.engine.extract import css

_SAFE_CLASS = re.compile(r"^-?[A-Za-z_][\w-]*$")
_STATEFUL_CLASS = re.compile(r"^(odd|even|first|last|active|selected|current|hover|open|closed|is-|has-|js-|ng-)", re.I)
MAX_ANCESTORS = 4


def stable_classes(el: Any) -> list[str]:
    return [c for c in (el.get("class") or "").split() if _SAFE_CLASS.match(c) and not _STATEFUL_CLASS.match(c)]


def sig(el: Any, classes: list[str] | None = None) -> str:
    """`tag.class1.class2` (at most two classes)."""
    cls = classes if classes is not None else stable_classes(el)
    return el.tag + "".join(f".{c}" for c in cls[:2])


def common_sig(els: list[Any]) -> str:
    """The signature the elements share: tag plus the classes all of them have."""
    shared = set(stable_classes(els[0]))
    for e in els[1:]:
        shared &= set(stable_classes(e))
    ordered = [c for c in stable_classes(els[0]) if c in shared]
    return sig(els[0], ordered)


def _ancestor_step(el: Any) -> tuple[str, bool]:
    """Selector step for an ancestor, and whether it is anchored by an id (stop climbing)."""
    if el.get("id") and _SAFE_CLASS.match(el.get("id")):
        return f"{el.tag}#{el.get('id')}", True
    return sig(el), False


def group_selector(els: list[Any], root: Any) -> str | None:
    """Shortest descendant selector that matches exactly `els` in `root` (e.g. `table.tenders tbody tr`)."""
    target = set(map(id, els))
    base = common_sig(els)
    candidates = [base]
    steps: list[str] = []
    node = els[0].getparent()
    for _ in range(MAX_ANCESTORS):
        if node is None or node.tag in ("html", "body"):
            break
        step, anchored = _ancestor_step(node)
        steps.insert(0, step)
        candidates.append(" ".join([*steps, base]))
        if anchored:
            break
        node = node.getparent()
    for sel in candidates:
        try:
            found = css(sel, root)
        except Exception:  # an odd class name the CSS translator rejects
            continue
        if set(map(id, found)) == target:
            return sel
    return None


def relative_selector(item: Any, el: Any) -> str:
    """CSS selector from `item` down to `el` (`td.title a`, `td:nth-child(3)`), unique within the item if possible."""
    parts: list[str] = []
    node = el
    while node is not None and node is not item:
        parent = node.getparent()
        step = sig(node)
        if parent is not None:
            same = [s for s in parent if isinstance(s.tag, str) and sig(s) == step]
            if len(same) > 1:
                step = f"{node.tag}:nth-child({[c for c in parent if isinstance(c.tag, str)].index(node) + 1})"
        parts.insert(0, step)
        if "." in step and len(css(" ".join(parts), item)) == 1:
            break
        node = parent
    sel = " ".join(parts)
    # Drop leading steps while the selector still finds exactly this element.
    while len(parts) > 1:
        shorter = " ".join(parts[1:])
        try:
            found = css(shorter, item)
        except Exception:
            break
        if len(found) == 1 and found[0] is el:
            parts = parts[1:]
            sel = shorter
        else:
            break
    return sel
