"""Repair diff: what two templates read differently."""

from __future__ import annotations

import json
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
