"""Deterministic evidence for the `investigate` scenario (T-016): computed before, and offered to, the model.

Deterministic first (ADR 0002): a portal that is gone, walled off or down is diagnosed here without a model call, and
an audit that finds nothing suspicious ends here as `Healthy`. The model is only asked when there is something to
judge or repair. Everything goes through `net` (`PoliteClient`); nothing here touches the DB.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlsplit

import httpx

from watchtower.engine.completeness import declared_counts
from watchtower.engine.executor import Page, first_list_page
from watchtower.engine.extract import Scope, collapse, parse_html, parse_json, select
from watchtower.engine.result import ReadResult
from watchtower.engine.snapshot import json_shape, snapshot
from watchtower.engine.template import Template
from watchtower.net import BrowserPool, PoliteClient, detect_block
from watchtower.validate.completeness import TOTAL_TOLERANCE

MAX_BODY = 2_000_000
VALUE_CHECK_TENDERS = 2
MAX_SCRIPTS = 3
MAX_LISTED = 5
DIFF_LINES = 60
_GONE = (404, 410)
_DOC_EXT = re.compile(r"\.(pdf|zip|docx?|xlsx?|odt|ods|rtf|txt|xml|p7m|dwg|jpe?g|png)$", re.I)
_SESSION = re.compile(r";(jsessionid|sid|phpsessid)=[^/?#]*", re.I)

# Labels a portal uses for a field (multilingual, lower case, matched as a prefix of the label).
FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "buyerName": (
        "vergabestelle", "auftraggeber", "öffentlicher auftraggeber", "ausschreibende stelle", "acheteur",
        "pouvoir adjudicateur", "organisme", "entité adjudicatrice", "órgano de contratación", "poder adjudicador",
        "entidad adjudicadora", "zamawiający", "aanbestedende dienst", "opdrachtgever", "stazione appaltante",
        "amministrazione aggiudicatrice", "buyer", "contracting authority", "contracting entity",
    ),
    "referenceNumber": (
        "aktenzeichen", "vergabenummer", "referenz", "référence", "numéro de référence", "expediente",
        "número de expediente", "numer referencyjny", "znak sprawy", "kenmerk", "referentienummer", "reference",
        "reference number", "file number", "cig",
    ),
}  # fmt: skip


# --------------------------------------------------------------------------- pre-check (failure and audit)


@dataclass
class Probe:
    url: str
    status: int | None = None  # None: no answer (network error)
    final_url: str = ""
    error: str = ""
    block: str = ""  # strong login/captcha signal, in words

    @property
    def gone(self) -> bool:
        return self.status in _GONE

    @property
    def down(self) -> bool:
        return self.status is None or self.status >= 500 or self.status == 429

    def describe(self) -> str:
        if self.status is None:
            return f"{self.url}: no answer ({self.error})"
        moved = f" (ended at {self.final_url})" if self.final_url and self.final_url != self.url else ""
        return f"{self.url}: HTTP {self.status}{moved}" + (f", {self.block}" if self.block else "")


@dataclass
class PreCheck:
    probes: list[Probe]
    verdict: str | None = None  # "gone" | "blocked" | "transient" | None (look closer)
    reason: str = ""

    def describe(self) -> str:
        return "\n".join("- " + p.describe() for p in self.probes)


async def probe(client: PoliteClient, url: str) -> Probe:
    p = Probe(url)
    try:
        async with client.stream("GET", url) as resp:
            body = bytearray()
            async for chunk in resp.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BODY:
                    break
            p.status, p.final_url = resp.status_code, str(resp.url)
            ctype = resp.headers.get("content-type", "")
    except httpx.HTTPError as e:
        p.error = f"{type(e).__name__}: {e}".splitlines()[0][:160]
        return p
    if "html" in ctype or not ctype:
        text = bytes(body).decode("utf-8", errors="replace")
        strong = [s for s in detect_block(text, url=p.final_url, status=p.status) if s.strong]
        if strong:
            s = strong[0]
            p.block = f"a {s.kind} page ({'; '.join(s.evidence[:2])})"
    return p


async def precheck(client: PoliteClient, portal_url: str, template: Template) -> PreCheck:
    """Probe the portal address and the template's list address (GET only). Decides the obvious cases."""
    urls = [portal_url]
    req = template.list.request
    if req.method == "GET" and req.url not in urls:
        urls.append(req.url)
    probes = [await probe(client, u) for u in urls]
    pc = PreCheck(probes)
    if all(p.gone for p in probes):
        pc.verdict = "gone"
        pc.reason = f"the portal no longer exists (HTTP {probes[0].status} at {probes[0].url})"
    elif any(p.block for p in probes) and not any(p.status and p.status < 400 and not p.block for p in probes[1:]):
        blocked = next(p for p in probes if p.block)
        pc.verdict = "blocked"
        pc.reason = f"the portal now requires a login or captcha: {blocked.url} leads to {blocked.block}"
    elif all(p.down for p in probes):
        pc.verdict = "transient"
        what = probes[-1]
        pc.reason = f"the portal is not answering ({'HTTP ' + str(what.status) if what.status else what.error})"
    return pc


# --------------------------------------------------------------------------- structure diff (repair)


async def live_snapshot(template: Template, client: PoliteClient, browser: BrowserPool | None) -> dict[str, Any] | str:
    """The first list page as the engine reaches it, as a structural snapshot (or an error sentence)."""
    try:
        page = await first_list_page(template, client, browser)
    except Exception as e:  # ReadError, a missing browser, …
        return f"could not load the first list page: {type(e).__name__}: {e}".splitlines()[0][:200]
    text = page.text.lstrip()
    if text[:1] in ("{", "["):
        try:
            return {"kind": "json", "url": page.url, "shape": json_shape(json.loads(text))}
        except ValueError:
            pass
    return snapshot(parse_html(page.text, page.url), page.url)


def diff_snapshots(old: dict[str, Any] | None, new: dict[str, Any] | str) -> str:
    if isinstance(new, str):
        return new
    if not old:
        return "No snapshot of a good read is stored, so there is nothing to compare with."
    a, b = _snapshot_lines(old), _snapshot_lines(new)
    if a == b:
        return "The list page has the same structure as on the last good read."
    diff = list(difflib.unified_diff(a, b, "last good read", "live now", n=1, lineterm=""))
    shown = diff[:DIFF_LINES]
    more = f"\n… {len(diff) - DIFF_LINES} more diff lines" if len(diff) > DIFF_LINES else ""
    return "\n".join(shown) + more


def _snapshot_lines(s: dict[str, Any]) -> list[str]:
    if s.get("kind") == "json":
        return json.dumps(s.get("shape"), indent=1, ensure_ascii=False, sort_keys=True).splitlines()
    return str(s.get("outline", "")).splitlines()


# --------------------------------------------------------------------------- audit checks


@dataclass
class AuditFindings:
    findings: list[str] = field(default_factory=list)  # suspicious: the model looks closer
    checked: list[str] = field(default_factory=list)  # what was checked and looked fine

    @property
    def suspicious(self) -> bool:
        return bool(self.findings)

    def describe(self) -> str:
        out = []
        if self.findings:
            out.append("Findings:\n" + "\n".join(f"- {f}" for f in self.findings))
        if self.checked:
            out.append("Checked without findings:\n" + "\n".join(f"- {c}" for c in self.checked))
        return "\n".join(out) or "Nothing could be checked."


async def audit(
    client: PoliteClient, browser: BrowserPool | None, portal_url: str, template: Template, result: ReadResult
) -> AuditFindings:
    """Compare what the engine read with the live portal: missed tenders, the portal's own count, and values read
    from the wrong place."""
    af = AuditFindings()
    first = _FirstPage(template, client, browser)
    if template.list.kind == "json":
        await _site_api_calls(client, portal_url, template, af)
    else:
        await _unread_links(first, result, af)
    await _declared_total(first, template, result, af)
    await _value_labels(client, template, result, af)
    return af


class _FirstPage:
    """The template's first list page, fetched at most once per audit (None when it can't be loaded)."""

    def __init__(self, template: Template, client: PoliteClient, browser: BrowserPool | None):
        self.template, self.client, self.browser = template, client, browser
        self._page: Page | None = None
        self._tried = False

    async def get(self) -> Page | None:
        if not self._tried:
            self._tried = True
            try:
                self._page = await first_list_page(self.template, self.client, self.browser)
            except Exception:  # the read passed moments ago; a failing page here is not evidence
                self._page = None
        return self._page


def _norm_url(url: str) -> str:
    url = _SESSION.sub("", url.split("#", 1)[0])
    return url.rstrip("/")


async def _unread_links(first: _FirstPage, result: ReadResult, af: AuditFindings) -> None:
    read_urls = {_norm_url(t.url) for t in result.tenders if t.url}
    if not read_urls:
        return
    # The directory most read tender pages live in, e.g. https://host/ssr/ausschreibung/
    dirs: dict[str, int] = {}
    for u in read_urls:
        dirs[u.rsplit("/", 1)[0] + "/"] = dirs.get(u.rsplit("/", 1)[0] + "/", 0) + 1
    tender_dir, n = max(dirs.items(), key=lambda kv: kv[1])
    if n < 0.8 * len(read_urls):
        return  # tender URLs have no common shape: this check can't tell tender links apart
    page = await first.get()
    if page is None:
        return
    root = parse_html(page.text, page.url).el
    missed: dict[str, str] = {}
    total = 0
    for a in root.iter("a"):
        href = a.get("href")
        if not href:
            continue
        url = _norm_url(urljoin(page.url, href))
        if not url.startswith(tender_dir) or "/" in url[len(tender_dir) :] or _DOC_EXT.search(url):
            continue
        total += 1
        if url not in read_urls:
            missed.setdefault(url, collapse(a.text_content())[:80])
    if missed:
        sample = "; ".join(f"{t or '(no text)'} <{u}>" for u, t in list(missed.items())[:MAX_LISTED])
        af.findings.append(
            f"The first list page links to {len(missed)} tender page(s) the read did not deliver "
            f"(same address pattern {tender_dir}…): {sample}. The template may miss part of the list."
        )
    else:
        af.checked.append(f"all {total} tender links on the first list page were delivered by the read")


async def _declared_total(first: _FirstPage, template: Template, result: ReadResult, af: AuditFindings) -> None:
    """Does the read reach the portal's own count of its list (T-041)? Below 95 % of it, tenders are being missed:
    pagination that stops early, a cap on the result list, a filter. ADR 0018's tolerance."""
    reached = len(result.tenders) + result.scope_dropped
    if result.declared_total:  # v2 `list.total`: `validate.check` already compared it on this very read
        n = result.declared_total
        af.checked.append(f"the portal's own count ({n}) was compared by the read ({reached} reached)")
        return
    if not isinstance(template, Template) or result.stats.limited or not result.tenders:
        return
    page = await first.get()
    if page is None:
        return
    scope = _scope(page)
    if scope is None:
        return
    try:
        items = select(template.list.items, scope)
    except Exception:
        items = []
    counts = declared_counts(scope, items)
    if not counts:
        return
    if len(counts) > 1:
        shown = "; ".join(f"{n} ({why!r})" for n, why in counts)
        af.checked.append(f"the first list page shows several different counts ({shown}), so none was compared")
        return
    ((declared, why),) = counts
    if reached >= declared * (1 - TOTAL_TOLERANCE):
        af.checked.append(f"the portal's own count ({declared}, {why!r}) matches the {reached} tenders read")
        return
    af.findings.append(
        f"The first list page says the list holds {declared} ({why!r}), but the read delivered only {reached}. "
        "The template may miss part of the list: pagination that stops early, a cap on the results, or a filter."
    )


def _scope(page: Page) -> Scope | None:
    text = page.text.lstrip()
    if text[:1] in ("{", "["):
        try:
            return parse_json(text, page.url)
        except ValueError:
            return None
    return parse_html(page.text, page.url)


async def _site_api_calls(client: PoliteClient, portal_url: str, template: Template, af: AuditFindings) -> None:
    """For a JSON list: does the site's own app call the same endpoint with different filters than the template?"""
    api = urlsplit(template.list.request.url)
    ours = dict(parse_qsl(api.query)) | {k: str(v) for k, v in template.list.request.params.items()}
    page_param = getattr(template.list.pagination, "name", None)
    texts: list[tuple[str, str]] = []
    try:
        resp = await client.get(portal_url)
    except httpx.HTTPError:
        return
    texts.append((str(resp.url), resp.text))
    root = parse_html(resp.text, str(resp.url)).el
    for s in [s for s in root.iter("script") if s.get("src")][:MAX_SCRIPTS]:
        src = urljoin(str(resp.url), s.get("src"))
        if urlsplit(src).netloc != urlsplit(str(resp.url)).netloc:
            continue
        try:
            js = await client.get(src)
        except httpx.HTTPError:
            continue
        texts.append((src, js.text))
    calls = []
    for where, text in texts:
        for m in re.finditer(re.escape(api.path) + r"(\?[^'\"`\s)]*)?", text):
            calls.append((where, dict(parse_qsl((m.group(1) or "?")[1:]))))
    if not calls:
        af.checked.append(f"the site's own pages don't reference {api.path} (nothing to compare)")
        return
    diffs = []
    for where, theirs in calls:
        for k, v in theirs.items():
            if k == page_param or "{" in v or "$" in v:
                continue
            if k in ours and ours[k] != v:
                diffs.append(f"{k}={v} (template: {k}={ours[k]}) in {where}")
    if diffs:
        af.findings.append(
            f"The site's own app calls {api.path} with different filters than the template: "
            + "; ".join(dict.fromkeys(diffs[:MAX_LISTED]))
            + ". The template may read a filtered subset of the tenders."
        )
    else:
        af.checked.append(f"the site's own app calls {api.path} with the same filters as the template")


def label_pairs(html: str, url: str) -> list[tuple[str, str]]:
    """(label, value) pairs on a page: dt/dd, th/td rows, and 'Label: value' text."""
    root = parse_html(html, url).el
    pairs: list[tuple[str, str]] = []
    for dt in root.iter("dt"):
        dd = dt.getnext()
        while dd is not None and dd.tag != "dd":
            dd = dd.getnext() if dd.tag != "dt" else None
        if dd is not None:
            pairs.append((collapse(dt.text_content()), collapse(dd.text_content())))
    for tr in root.iter("tr"):
        cells = [c for c in tr if c.tag in ("th", "td")]
        if len(cells) == 2 and cells[0].tag == "th":
            pairs.append((collapse(cells[0].text_content()), collapse(cells[1].text_content())))
    for el in root.iter("p", "li", "div", "span"):
        text = collapse(el.text_content())
        m = re.match(r"^([^:\n]{2,40}):\s+(.{2,200})$", text)
        if m and len(el) <= 3:
            pairs.append((m.group(1), m.group(2)))
    return pairs


def _same(a: str, b: str) -> bool:
    a, b = collapse(a).casefold(), collapse(b).casefold()
    return bool(a and b) and (a == b or a in b or b in a)


async def _value_labels(client: PoliteClient, template: Template, result: ReadResult, af: AuditFindings) -> None:
    """On a few tender pages: is each value we read shown under the label the portal uses for that field?"""
    if template.detail is not None and template.detail.format == "json":
        return
    sampled = [t for t in result.tenders if t.url and t.status == "open"][:VALUE_CHECK_TENDERS]
    checked = 0
    for t in sampled:
        try:
            resp = await client.get(t.url)
        except httpx.HTTPError:
            continue
        if "html" not in resp.headers.get("content-type", "html"):
            continue
        pairs = label_pairs(resp.text, str(resp.url))
        for f, labels in FIELD_LABELS.items():
            ours = getattr(t, f)
            if not ours:
                continue
            shown = [(lab, val) for lab, val in pairs if collapse(lab).casefold().rstrip(":").startswith(labels)]
            if not shown:
                continue
            checked += 1
            if not any(_same(ours, val) for _, val in shown):
                lab, val = shown[0]
                elsewhere = [lab2 for lab2, val2 in pairs if _same(ours, val2)]
                where = f"; the read value appears under '{elsewhere[0]}'" if elsewhere else ""
                af.findings.append(
                    f"Tender {t.id}: {f} was read as {ours!r}, but its page shows '{lab}: {val}'{where}. "
                    "The template may read this field from the wrong place."
                )
    if checked and not any(" was read as " in x for x in af.findings):
        af.checked.append(f"{checked} labelled value(s) on {len(sampled)} tender page(s) match what was read")
