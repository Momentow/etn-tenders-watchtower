"""Recognition tools (T-022): which software runs the portal, which label is which field, how dates are written, and
whether a login or captcha really blocks the tenders."""

from __future__ import annotations

import json
from urllib.parse import urlsplit, urlunsplit

from agents import RunContextWrapper

from watchtower import net
from watchtower.agent import pages
from watchtower.agent.analysis import discovery as analysis
from watchtower.agent.analysis import recognition as rec
from watchtower.agent.context import AgentContext
from watchtower.agent.pages import clip, get_page, page_scope
from watchtower.agent.registry import tool
from watchtower.engine.extract import HtmlScope

Ctx = RunContextWrapper[AgentContext]


@tool()
async def fingerprint_software(ctx: Ctx, url: str) -> str:
    """Identify the portal software from the URL, `meta generator`, footer and markup (e.g. cosinex Vergabemarktplatz,
    Atexo, PLACSP, TenderNed). If a template for the same software is known from another portal, say so: reusing it
    (`find_template_for_software`) is much cheaper than starting from scratch. Put the name in the template's
    `software` field."""
    c = ctx.context
    page = await get_page(c, url)
    if isinstance(page, str):
        return page
    scope = page_scope(page)
    html = scope if isinstance(scope, HtmlScope) else None
    fp = rec.fingerprint(page.url, html, page.text)
    country = rec.guess_country(page.url, rec.page_lang(html))
    platform = f"  (web platform: {', '.join(fp.platform)})" if fp.platform else ""
    lines = [f"software: {fp.software or 'unknown'}{platform}"]
    lines.append(f"country guess: {country or 'unknown'} (from the domain and page language)")
    if fp.evidence:
        lines.append("evidence: " + "; ".join(fp.evidence))
    if fp.software and fp.software in c.known_templates:
        lines.append(f"A template for {fp.software!r} is known: call find_template_for_software({fp.software!r}).")
    elif c.known_templates:
        lines.append(f"Known software templates: {', '.join(sorted(c.known_templates))}")
    return "\n".join(lines)


@tool()
async def find_template_for_software(ctx: Ctx, software: str) -> str:
    """The template of another portal running the same software, with its URLs moved to this portal's host. Dry-run it
    with `try_template` and adjust what differs (list URL, search values, labels) instead of starting from scratch."""
    c = ctx.context
    t = c.known_templates.get(software)
    if t is None:
        known = ", ".join(sorted(c.known_templates)) or "none"
        return f"No template known for {software!r}. Known: {known}."
    data = t.model_dump(mode="json", exclude_defaults=True)
    target = urlsplit(c.portal_url)
    text = json.dumps(data, ensure_ascii=False)
    hosts = {urlsplit(t.list.request.url)[:2]}
    for scheme, netloc in hosts:
        text = text.replace(f"{scheme}://{netloc}", urlunsplit((target.scheme, target.netloc, "", "", "")))
    return f"Template for {software!r} (URLs moved to {target.netloc}):\n{text}"


@tool()
async def map_field_labels(ctx: Ctx, url: str) -> str:
    """Find the label/value pairs on a page (detail page or list) and map each label to a contract field with a
    multilingual synonym dictionary (Angebotsfrist, Date limite, Plazo de presentación, Termin składania ofert, …).
    Gives a ready XPath (plus `pattern` when the label is in the same text) for each, usable as an `on: detail` rule."""
    page = await get_page(ctx.context, url)
    if isinstance(page, str):
        return page
    scope = page_scope(page)
    if not isinstance(scope, HtmlScope):
        return "Not an HTML page; for JSON use `summarize_json`."
    pairs = rec.labelled_values(scope)
    if not pairs:
        return f"{page.url}: no label/value pairs found; look at the outline with `fetch`."
    lines = [f"{page.url}: field ← label = value | rule"]
    for p in pairs:
        rule = {"by": "xpath", "expr": p.xpath}
        if p.pattern:
            rule["pattern"] = p.pattern
        lines.append(
            f"  {p.field or '?'} ← {p.label!r} = {clip(p.value, 50)!r} | {json.dumps(rule, ensure_ascii=False)}"
        )
    return "\n".join(lines)


@tool()
async def probe_dates(ctx: Ctx, samples: list[str], country: str | None = None) -> str:
    """How dates are written: give 2–5 raw date strings from the portal and its country (ISO alpha-2). Answers with the
    strptime `formats` that read all of them, the time zone, what the engine makes of each (ISO with offset), and
    ambiguity warnings (day/month order). Use the result for date rules and the template's `timezone`."""
    r = rec.probe_dates(samples, country)
    lines = [f"timezone: {r.zone}"]
    if r.formats:
        lines.append(f"formats: {json.dumps(r.formats)}")
    elif all(v for _, v in r.parsed):
        lines.append("formats: none needed (the engine reads these without formats)")
    else:
        lines.append("formats: none fits all samples")
    lines += [f"  {s!r} → {v or 'NOT READ'}" for s, v in r.parsed]
    lines += [f"note: {n}" for n in r.notes]
    return "\n".join(lines)


@tool()
async def detect_block(ctx: Ctx, url: str) -> str:
    """Is the portal really blocked by a login or captcha? Looks for block signals on the page AND tries what matters:
    whether the tender list shows records and whether a document link on the page downloads a file. A captcha widget on
    a page whose documents still download is NOT a block. Use on the list page and on a detail page with documents."""
    c = ctx.context
    page = await get_page(c, url)
    if isinstance(page, str):
        return page
    lines = [f"{page.url} → HTTP {page.status}"]
    signals = net.detect_block(page.text, url=page.url, status=page.status)
    for s in signals:
        lines.append(
            f"signal: {s.kind} ({'the page IS the block' if s.strong else 'widget on the page'}): "
            + "; ".join(s.evidence[:3])
        )
    if not signals:
        lines.append("no login/captcha signals on the page")
    scope = page_scope(page)
    if isinstance(scope, HtmlScope):
        blocks = analysis.find_repeating_blocks(scope, limit=1)
        lines.append(f"records on the page: {blocks[0].count if blocks else 0}")
        docs = [g for g in analysis.classify_links(scope) if g.kind == "document"]
        if docs:
            probe = await pages.probe_document(c, docs[0].links[0][1])
            lines.append("document probe: " + probe.sentence())
            if probe.blocks:
                lines.append("VERDICT: BLOCKED — documents need a " + probe.blocks[0].kind + " (report_blocked)")
                return "\n".join(lines)
            if probe.is_file:
                lines.append("VERDICT: NOT BLOCKED — documents download without logging in")
                return "\n".join(lines)
    strong = [s for s in signals if s.strong]
    if strong:
        lines.append(f"VERDICT: the page itself is a {strong[0].kind} wall; try other entry points before reporting")
    else:
        lines.append("VERDICT: no block found here; check a document link on a detail page to be sure")
    return "\n".join(lines)
