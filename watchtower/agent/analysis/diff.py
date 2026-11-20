"""Repair diffs: list-page structure now vs the last good read, and what two templates read differently."""

from __future__ import annotations

import difflib
import json
import re
from typing import Any

from watchtower.engine.result import ReadResult

COMPARED = (
    "url",
    "title",
    "buyerName",
    "publicationDate",
    "submissionDeadline",
    "status",
    "referenceNumber",
    "procedureType",
    "contractNature",
    "cpvCodes",
    "placeOfPerformance",
)
_CLASS = re.compile(r"[.#]([\w-]+)")


def diff_snapshots(old: dict[str, Any], new: dict[str, Any], max_lines: int = 60) -> list[str]:
    """Unified diff of two `engine.snapshot` outlines (HTML) or key shapes (JSON), plus vanished/new class names."""
    if old.get("kind") != new.get("kind"):
        return [f"the list changed from {old.get('kind')} to {new.get('kind')}"]
    if new.get("kind") == "json":
        a = json.dumps(old.get("shape"), indent=1, ensure_ascii=False).splitlines()
        b = json.dumps(new.get("shape"), indent=1, ensure_ascii=False).splitlines()
    else:
        a, b = (old.get("outline") or "").splitlines(), (new.get("outline") or "").splitlines()
    out = []
    if old.get("url") != new.get("url"):
        out.append(f"url: {old.get('url')} → {new.get('url')}")
    gone = sorted(set(_CLASS.findall("\n".join(a))) - set(_CLASS.findall("\n".join(b))))
    added = sorted(set(_CLASS.findall("\n".join(b))) - set(_CLASS.findall("\n".join(a))))
    if gone or added:
        out.append(f"names gone: {', '.join(gone[:15]) or '-'} | names new: {', '.join(added[:15]) or '-'}")
    body = [ln for ln in difflib.unified_diff(a, b, "last good", "now", n=1, lineterm="")][2:]
    if not body:
        out.append("no structural difference")
    out += body[:max_lines]
    if len(body) > max_lines:
        out.append(f"… {len(body) - max_lines} more diff lines")
    return out


def diff_reads(a: ReadResult, b: ReadResult, max_lines: int = 30) -> list[str]:
    """Tender ids only in A or B, and per field how many shared tenders differ (with examples)."""
    ta = {t.id: t for t in a.tenders}
    tb = {t.id: t for t in b.tenders}
    only_a, only_b, both = ta.keys() - tb.keys(), tb.keys() - ta.keys(), ta.keys() & tb.keys()
    out = [f"A: {len(ta)} tenders, B: {len(tb)} tenders, {len(both)} shared"]
    if only_a:
        out.append(f"only in A ({len(only_a)}): {', '.join(sorted(only_a)[:8])}")
    if only_b:
        out.append(f"only in B ({len(only_b)}): {', '.join(sorted(only_b)[:8])}")
    for f in COMPARED:
        diffs = [
            (i, getattr(ta[i], f), getattr(tb[i], f)) for i in sorted(both) if getattr(ta[i], f) != getattr(tb[i], f)
        ]
        if diffs:
            i, va, vb = diffs[0]
            out.append(f"{f}: differs on {len(diffs)}/{len(both)}, e.g. {i}: A={_short(va)} B={_short(vb)}")
    docs = [i for i in both if {d.url for d in ta[i].documents} != {d.url for d in tb[i].documents}]
    if docs:
        out.append(f"documents: differ on {len(docs)}/{len(both)}")
    if len(out) == 1:
        out.append("no difference on the shared tenders")
    return out[:max_lines]


def _short(v: Any) -> str:
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= 70 else s[:69] + "…"
