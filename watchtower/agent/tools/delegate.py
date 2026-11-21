"""Light-tier tasks the strong model hands out (T-035, ADR 0015 → Model tiers).

Each `delegate_*` tool gathers its input deterministically (pages through `net`, dry runs through the engine), makes
ONE structured call to the light model through `ctx.light` (`runtime.LightWorker`, the light budget), then checks what
it can of the answer (a proposed rule must extract its value, a quoted link must be on the page) before returning it.
The output is labelled as a light-model proposal: the strong model reviews it and decides. A light task never ends a
scenario run.
"""

from __future__ import annotations

import asyncio
import json
from typing import Literal

from agents import RunContextWrapper
from pydantic import BaseModel

from watchtower.agent import prune
from watchtower.agent.analysis import recognition as rec
from watchtower.agent.context import AgentContext, Fetched
from watchtower.agent.pages import clip, get_page, page_scope
from watchtower.agent.registry import tool
from watchtower.engine import run_template
from watchtower.engine.extract import ExtractError, HtmlScope, collapse, values
from watchtower.engine.result import TenderRecord
from watchtower.engine.template import Extract

Ctx = RunContextWrapper[AgentContext]

# Contract fields a rule can fill (`id`, `url` and `documents` come from the template's structure, not a label).
CONTRACT_FIELDS = [f for f in TenderRecord.model_fields if f not in ("id", "url", "documents", "complete")]
MAX_VERIFY = 5
PAGE_TEXT_CHARS = 8_000
REVIEW = "LIGHT-MODEL PROPOSAL: review it before using it; dry-run anything you adopt."


def _no_light(c: AgentContext) -> str | None:
    return None if c.light is not None else "The light model is not available in this run; do this yourself."


def page_text(page: Fetched, limit: int = PAGE_TEXT_CHARS) -> str:
    """The visible text of an HTML page (or the raw JSON/text), whitespace collapsed and clipped."""
    if isinstance(page_scope(page), HtmlScope):
        return prune.visible_text(page.text, max_chars=limit)
    return clip(page.text, limit)


def _norm(s: str) -> str:
    return rec.fold(s)


# --------------------------------------------------------------------------- map_fields


class FieldPick(BaseModel):
    index: int  # the pair's number in the input
    field: str | None  # a contract field name, or null when the label is no contract field


class FieldMap(BaseModel):
    picks: list[FieldPick]


MAP_FIELDS = f"""\
You map labels on a public-procurement tender page to contract fields. Fields: {", ".join(CONTRACT_FIELDS)}.
- title: the tender's name; buyerName: the contracting authority; publicationDate: when the notice was published;
  submissionDeadline: the deadline for tenders/requests to participate; referenceNumber: the buyer's file/reference
  number; procedureType: open/restricted/negotiated…; contractNature: works/supplies/services; cpvCodes: CPV codes;
  placeOfPerformance: where the contract is performed; description: what is procured; status: open/closed/awarded.
- Labels can be in any language. Pick a field only when the label (and its value) clearly means it; otherwise null.
  A wrong mapping is worse than none. Use each field at most once.
Answer with one pick per input pair, by its index."""


@tool()
async def delegate_map_fields(ctx: Ctx, url: str) -> str:
    """Light model: map EVERY label/value pair on a page (detail page or list) to a contract field, including labels
    the synonym dictionary of `map_field_labels` doesn't know. Each proposed rule is run on the page to confirm it
    extracts the value; the dictionary's own mapping is shown next to the model's. Use when `map_field_labels`
    leaves important fields unmapped."""
    c = ctx.context
    if err := _no_light(c):
        return err
    page = await get_page(c, url)
    if isinstance(page, str):
        return page
    scope = page_scope(page)
    if not isinstance(scope, HtmlScope):
        return "Not an HTML page; for JSON use `summarize_json`."
    pairs = rec.labelled_values(scope)
    if not pairs:
        return f"{page.url}: no label/value pairs found; look at the outline with `fetch`."
    task = "\n".join(f"{i}. {p.label!r} = {clip(p.value, 80)!r}" for i, p in enumerate(pairs))
    out = await c.light.run("map_fields", MAP_FIELDS, task, FieldMap)
    if isinstance(out, str):
        return out
    lines = [REVIEW, f"{page.url}: field ← label = value | rule | check"]
    used: set[str] = set()
    for pick in out.picks:
        if not 0 <= pick.index < len(pairs) or not pick.field:
            continue
        p = pairs[pick.index]
        if pick.field not in CONTRACT_FIELDS:
            lines.append(f"  (ignored: {pick.field!r} is no contract field, for {p.label!r})")
            continue
        rule = {"by": "xpath", "expr": p.xpath} | ({"pattern": p.pattern} if p.pattern else {})
        check = _check_rule(rule, scope, p.value)
        if pick.field in used:
            check += "; field picked twice: choose one"
        used.add(pick.field)
        if p.field and p.field != pick.field:
            check += f"; the dictionary says {p.field}"
        lines.append(
            f"  {pick.field} ← {p.label!r} = {clip(p.value, 50)!r} | {json.dumps(rule, ensure_ascii=False)} | {check}"
        )
    if len(lines) == 2:
        lines.append("  the light model mapped no label to a contract field")
    return "\n".join(lines)


def _check_rule(rule: dict[str, str], scope: HtmlScope, expected: str) -> str:
    try:
        found = values(Extract(**rule), scope)
    except (ExtractError, ValueError) as e:
        return f"rule fails: {clip(str(e), 80)}"
    if not found:
        return "rule extracts nothing"
    if collapse(found[0])[:60] == collapse(expected)[:60]:
        return "rule extracts the value"
    return f"rule gives {clip(found[0], 50)!r}"


# --------------------------------------------------------------------------- verify_tenders


class FieldVerdict(BaseModel):
    field: str
    verdict: Literal["right", "wrong", "not_on_page"]
    evidence: str  # the text on the page that shows it, or why it's wrong


class TenderCheck(BaseModel):
    verdicts: list[FieldVerdict]


VERIFY_TENDER = """\
You check values a program extracted from one public-procurement tender page against that page's text. For each field
answer: "right" (the page shows this value for this field; dates may be reformatted to ISO 8601 and time zones added),
"wrong" (the page shows a different value for this field, or the value belongs to another field or another tender) or
"not_on_page" (you can't find it). Quote the page text you used as `evidence` (short). Be strict: a value from the
wrong label is wrong."""


@tool()
async def delegate_verify_tenders(ctx: Ctx, template: str, tenders: int = 3, rendered: bool = False) -> str:
    """Light model, in parallel: dry-run `template` on the first `tenders` (≤ 5) tenders, open each tender's page and
    check every extracted value against what the page says (right / wrong / not on the page, with evidence). Values
    found verbatim on the page are confirmed without the model. `rendered`: load the tender pages in the browser. Use
    it before submitting, to catch values read from the wrong label."""
    from watchtower.agent.tools.core import _parse_any_template  # the same parsing and schema errors as try_template

    c = ctx.context
    if err := _no_light(c):
        return err
    t = _parse_any_template(template)
    if isinstance(t, str):
        return t
    n = max(1, min(tenders, MAX_VERIFY))
    rr = await run_template(t, c.client, c.browser, limit=n)
    if not rr.tenders:
        return "The dry run read no tenders: " + ("; ".join(rr.errors[:3]) or "nothing to verify")
    results = await asyncio.gather(*(_verify_one(c, x, rendered) for x in rr.tenders[:n]))
    return "\n".join([REVIEW, *results])


async def _verify_one(c: AgentContext, x: TenderRecord, rendered: bool) -> str:
    head = f"{x.id} ({x.url or 'no url'}):"
    if not x.url:
        return f"{head} no tender URL, nothing to check against"
    page = await get_page(c, x.url, rendered=rendered)
    if isinstance(page, str):
        return f"{head} {page}"
    text = page_text(page)
    folded = _norm(text)
    data = {k: v for k, v in x.contract_data().items() if v is not None and k not in ("id", "url", "status")}
    verbatim: list[str] = []
    ask: dict[str, str] = {}
    for k, v in data.items():
        if isinstance(v, str) and len(v) > 2 and _norm(v) in folded:
            verbatim.append(k)
        else:
            ask[k] = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    lines = [head]
    if verbatim:
        lines.append(f"  on the page verbatim: {', '.join(verbatim)}")
    if ask:
        task = "Extracted values:\n" + "\n".join(f"- {k}: {v}" for k, v in ask.items()) + f"\n\nPage text:\n{text}"
        out = await c.light.run("verify_tender", VERIFY_TENDER, task, TenderCheck)
        if isinstance(out, str):
            lines.append(f"  {out}")
        else:
            for v in out.verdicts:
                if v.field in ask:
                    lines.append(f"  {v.field}: {v.verdict.upper()} {ask[v.field]!r}: {clip(v.evidence, 120)!r}")
            if missing := [k for k in ask if k not in {v.field for v in out.verdicts}]:
                lines.append(f"  not judged: {', '.join(missing)}")
    if not data:
        lines.append("  no field values were extracted")
    return "\n".join(lines)


# --------------------------------------------------------------------------- classify_notices


class NoticeClass(BaseModel):
    notice_type: str
    open_call: Literal["yes", "no", "unsure"]
    reason: str


class NoticeClasses(BaseModel):
    classes: list[NoticeClass]


CLASSIFY_NOTICES = """\
A procurement portal's list mixes notice types. For each notice type, say whether its notices are OPEN CALLS FOR
COMPETITION (a buyer invites tenders or requests to participate now: contract notices, prior information notices used
as a call, calls in a dynamic purchasing system, qualification-system notices with a call) — "yes"; or not (contract
award notices, results, voluntary ex-ante transparency notices, modifications, cancellations, early terminations,
corrections of other notices, plain prior information notices) — "no"; "unsure" when the name and examples don't
tell. Names can be in any language or codes (e.g. eForms subtypes). Give one short reason each."""


@tool()
async def delegate_classify_notices(ctx: Ctx, notice_types: list[str], examples: list[str] | None = None) -> str:
    """Light model: which notice types in a list are open calls for competition (for the template's `scope`).
    `notice_types`: the distinct values of the list's notice-type field (from `summarize_json` or the list page);
    `examples`: optional sample titles or records, written "type: title". Unknown types come back as unsure."""
    c = ctx.context
    if err := _no_light(c):
        return err
    types = list(dict.fromkeys(t.strip() for t in notice_types if t.strip()))
    if not types:
        return "Give at least one notice type."
    task = "Notice types:\n" + "\n".join(f"- {t}" for t in types)
    if examples:
        task += "\n\nExamples:\n" + "\n".join(f"- {clip(e, 160)}" for e in examples[:30])
    out = await c.light.run("classify_notices", CLASSIFY_NOTICES, task, NoticeClasses)
    if isinstance(out, str):
        return out
    by_type = {x.notice_type: x for x in out.classes if x.notice_type in types}
    lines = [REVIEW]
    for t in types:
        x = by_type.get(t)
        lines.append(f"  {t!r}: {x.open_call} ({clip(x.reason, 120)})" if x else f"  {t!r}: unsure (not answered)")
    return "\n".join(lines)


# --------------------------------------------------------------------------- describe_page


class Route(BaseModel):
    kind: Literal["link", "button", "form", "this_page"]
    text: str  # the visible text or label, exactly as on the page
    href: str | None = None
    why: str


class PageDescription(BaseModel):
    list_on_this_page: bool
    total_shown: str | None  # a result count the page shows ("2,474 results"), verbatim
    routes: list[Route]  # best first


DESCRIBE_PAGE = """\
You look at the outline of one page of a public-procurement portal and say where the list of ALL current (open)
tenders probably is and how a person reaches it: a link or menu entry (Ausschreibungen, Aanbestedingen, Consultations,
Licitaciones, Tenders, "all notices", search), a button (search, show all) or a form to submit. Say whether this page
already shows that list, and quote any result count it shows. Prefer a full list or search over a "latest N" widget.
Quote texts and hrefs exactly as in the outline; give at most 5 routes, best first."""


@tool()
async def delegate_describe_page(ctx: Ctx, url: str, rendered: bool = False) -> str:
    """Light model: where the full list of open tenders probably is, seen from the page at `url` (start or menu page):
    candidate links/buttons/forms with a reason, whether this page is already the list, and the result count it shows.
    Each quoted text/link is checked to be on the page. `rendered`: load the page in the browser first."""
    c = ctx.context
    if err := _no_light(c):
        return err
    page = await get_page(c, url, rendered=rendered)
    if isinstance(page, str):
        return page
    scope = page_scope(page)
    if not isinstance(scope, HtmlScope):
        return "Not an HTML page."
    outline = prune.outline(page.text, page.url)
    out = await c.light.run("describe_page", DESCRIBE_PAGE, f"Page {page.url}:\n{outline}", PageDescription)
    if isinstance(out, str):
        return out
    raw = page.text
    lines = [REVIEW, f"{page.url}: list on this page: {'yes' if out.list_on_this_page else 'no'}"]
    if out.total_shown:
        seen = _norm(out.total_shown) in _norm(page_text(page, 200_000))
        lines.append(f"  total shown: {out.total_shown!r}" + ("" if seen else " (NOT found on the page)"))
    for r in out.routes[:5]:
        found = (r.href is None or r.href in raw) and (not r.text or _norm(r.text) in _norm(raw))
        where = f" → {r.href}" if r.href else ""
        lines.append(
            f"  {r.kind} {r.text!r}{where}: {clip(r.why, 100)}" + ("" if found else " (NOT found on the page)")
        )
    return "\n".join(lines)
