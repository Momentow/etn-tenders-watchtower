"""Truthful blocks (T-039, ADR 0015 → Truthful blocks): `Blocked` only on evidence.

The smoke run reported dtvp.de and marches-publics.gouv.fr blocked after ONE document link redirected to a login,
although documents download anonymously with a session cookie or through an anonymous-download option. So:

- `pages.get_page` and `pages.probe_document` record what they see in `ctx.blocks` (`BlockEvidence`): which tenders'
  pages or documents led to a login/captcha, which tenders' documents downloaded.
- `report_blocked` calls `review()` before accepting. It accepts when the entry page itself is a login/captcha wall or
  the portal's firewall refuses every path. Otherwise it needs the block on at least two tenders whose documents never
  downloaded, it re-tries blocked documents itself after loading the page they came from (session cookies), and it
  follows the anonymous-download links seen on the pages. Anything missing or disproved comes back as a refusal; the
  model goes on.
- `report_transient` calls `firewall_wall()`: HTTP 403 on every path tried is a block ("access refused by the
  portal's firewall"), not a temporary failure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit, urlunsplit

if TYPE_CHECKING:
    from watchtower.agent.context import AgentContext

MIN_TENDERS = 2  # the block must be seen on this many different tenders
SESSION_RETRIES = 2  # blocked documents re-tried after reloading their source page
ANON_TRIES = 3  # anonymous-download links followed per review
FIREWALL_PATHS = 3  # distinct paths answering 403 before it's the firewall, not one page

# Link or button texts that offer documents without an account (multilingual).
ANONYMOUS = re.compile(
    r"anonym|anoniem|sans (identification|inscription|authentification|compte)|"
    r"without (registration|registering|login|logging in|an account)|as (a )?guest|"
    r"ohne (registrierung|anmeldung|login|konto)|als gast|"
    r"sin (registro|registrarse|identificaci)|senza registrazione|bez (rejestracji|logowania)|"
    r"zonder (registratie|inloggen|account)|utan inloggning|download (all )?(documents )?anonymously",
    re.I,
)
_FILE_EXT = re.compile(r"\.(pdf|zip|docx?|xlsx?|odt|rtf|txt|xml|7z|rar|gaeb|d8[1-6]|x8[1-6])$", re.I)


@dataclass
class BlockEvent:
    tender: str  # tender key: its page URL (normalized) or id; for an orphan document, the document's folder
    url: str  # the page or document that led to the block
    source: str | None  # page the document link was found on (reloaded for the session retry)
    what: str  # sentence: what was seen


@dataclass
class BlockEvidence:
    blocked: list[BlockEvent] = field(default_factory=list)
    downloaded: set[str] = field(default_factory=set)  # tender keys with at least one document that is a file
    anonymous_tried: set[str] = field(default_factory=set)
    session_retried: set[str] = field(default_factory=set)
    # The last refused report: the model's reason and what the evidence lacked (for a truthful reason if the run ends
    # without an outcome).
    refused: str | None = None
    refused_why: str = ""

    def blocked_tenders(self) -> list[str]:
        return list(dict.fromkeys(e.tender for e in self.blocked if e.tender not in self.downloaded))

    def add_block(self, tender: str, url: str, what: str, source: str | None = None) -> None:
        if not any(e.url == url for e in self.blocked):
            self.blocked.append(BlockEvent(tender, url, source, what))


def norm_url(url: str) -> str:
    """Scheme, host and path, without `;jsessionid`, query or fragment: one key per page."""
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc.lower(), p.path.split(";", 1)[0].rstrip("/") or "/", "", ""))


def tender_key(url: str | None = None, *, tender: str | None = None, document: bool = False) -> str:
    """The key a block or download counts under: the tender (its page URL or id) when known. For a document of an
    unknown tender, its folder when the path names a file (a tender's documents usually share one), else the URL."""
    if tender:
        return norm_url(tender) if tender.startswith(("http://", "https://")) else tender
    key = norm_url(url or "")
    if document:
        if _FILE_EXT.search(key):
            return key.rsplit("/", 1)[0] or key
        query = urlsplit(url or "").query
        return f"{key}?{query}" if query else key
    return key


@dataclass
class Route:
    text: str
    href: str | None  # None: a button or form without a plain link
    page: str


def anonymous_routes(c: AgentContext) -> list[Route]:
    """Links and buttons on the run's HTML pages that offer documents without an account."""
    from watchtower.agent.pages import page_scope  # pages imports this module
    from watchtower.engine.extract import HtmlScope, collapse

    out: list[Route] = []
    seen: set[str] = set()
    for page in list(c.pages.values()):
        if not ANONYMOUS.search(page.text):
            continue
        scope = page_scope(page)
        if not isinstance(scope, HtmlScope):
            continue
        for el in scope.el.iter("a", "button", "input", "label"):
            text = collapse(el.text_content() if el.tag != "input" else el.get("value") or "")
            title = el.get("title") or el.get("aria-label") or ""
            if not ANONYMOUS.search(text) and not ANONYMOUS.search(title):
                continue
            href = el.get("href") if el.tag == "a" else None
            if href and href.startswith(("#", "javascript:")):
                href = None
            href = urljoin(page.url, href) if href else None
            key = href or f"{page.url}#{text}"
            if key not in seen:
                seen.add(key)
                out.append(Route(text or title, href, page.url))
    return out


def firewall_wall(c: AgentContext) -> str | None:
    """A block reason when every page fetched in this run on the portal's host answered 403, on enough paths."""
    host = urlsplit(c.portal_url).netloc.lower()
    statuses: dict[str, int | None] = {}
    sample = ""
    for page in c.pages.values():
        if urlsplit(page.url).netloc.lower() != host:
            continue
        statuses[norm_url(page.url)] = page.status
        if page.status == 403 and not sample:
            sample = _gateway_name(page.text)
    if len(statuses) < FIREWALL_PATHS or any(s != 403 for s in statuses.values()):
        return None
    by = f" ({sample})" if sample else ""
    return f"access refused by the portal's firewall{by}: HTTP 403 on every path tried ({len(statuses)} paths)"


def _gateway_name(text: str) -> str:
    m = re.search(
        r"(Microsoft-Azure-Application-Gateway[^\s<]*|cloudflare|Akamai|Imperva|Incapsula|AWS WAF|"
        r"F5 BIG-IP|Sucuri|ModSecurity|FortiWeb|Barracuda)",
        text,
        re.I,
    )
    return m.group(1) if m else ""


async def probe_firewall(c: AgentContext) -> str | None:
    """`firewall_wall`, after fetching a few more paths on the host if the run has seen too few."""
    from watchtower.agent.pages import get_page

    entry = await get_page(c, c.portal_url)
    if isinstance(entry, str) or entry.status != 403:
        return None
    p = urlsplit(c.portal_url)
    for path in ("/", "/robots.txt", "/favicon.ico"):
        if firewall_wall(c) is not None:
            break
        await get_page(c, urlunsplit((p.scheme, p.netloc, path, "", "")))
    return firewall_wall(c)


async def review(c: AgentContext, reason: str) -> str | None:
    """None: the block is proven, accept the report. Otherwise the refusal text for the model."""
    from watchtower.agent.pages import get_page, probe_document
    from watchtower.net import detect_block

    entry = await get_page(c, c.portal_url)
    if not isinstance(entry, str):
        strong = [s for s in detect_block(entry.text, url=entry.url, status=entry.status) if s.strong]
        if strong:
            return None  # the whole site is behind the wall
    if await probe_firewall(c) is not None:
        return None

    ev = c.blocks
    disproved: list[str] = []
    missing: list[str] = []

    # Session: reload the page each blocked document came from, then try the document again.
    retry = [e for e in ev.blocked if e.url not in ev.session_retried and e.tender not in ev.downloaded]
    for e in retry[:SESSION_RETRIES]:
        ev.session_retried.add(e.url)
        try:
            await c.client.get(e.source or c.portal_url)  # a fresh request: sets whatever cookies the page sets
        except Exception:  # noqa: BLE001 - the retry below tells the story either way
            pass
        probe = await probe_document(c, e.url, tender=e.tender, source=e.source)
        if probe.is_file:
            disproved.append(
                f"{e.url} downloads once {e.source or c.portal_url} was loaded first (session cookies): not a block. "
                "The engine keeps cookies, so read the documents from the pages that link to them"
            )

    # Anonymous routes offered on the pages seen.
    routes = anonymous_routes(c)
    for r in [r for r in routes if r.href and r.href not in ev.anonymous_tried][:ANON_TRIES]:
        ev.anonymous_tried.add(r.href)
        probe = await probe_document(c, r.href, source=r.page)
        if probe.is_file or (not probe.blocks and (probe.status or 0) < 400 and not probe.error):
            disproved.append(
                f"the anonymous route {r.text!r} on {r.page} works ({probe.sentence()}): use it for the documents"
                + ("" if probe.is_file else " (it is a page: follow it to the files with `per_link`)")
            )
    buttons = [r for r in routes if r.href is None and f"{r.page}#{r.text}" not in ev.anonymous_tried]
    for r in buttons[:ANON_TRIES]:
        ev.anonymous_tried.add(f"{r.page}#{r.text}")
        missing.append(f"{r.page} offers {r.text!r} (a button or form): try that route before reporting a block")

    tenders = ev.blocked_tenders()
    if len(tenders) < MIN_TENDERS:
        seen = f"on {len(tenders)} tender(s)" if tenders else "on no tender's pages or documents in this run"
        missing.append(
            f"the block was seen {seen}; it must hold for at least {MIN_TENDERS} different tenders. Open another "
            "tender's page and try its documents (`detect_block` on detail pages, `probe_document`, `try_template`)"
        )
    if not disproved and not missing:
        return None
    lines = ["Not accepted: the evidence doesn't show a block yet."]
    lines += [f"- DISPROVED: {d}" for d in disproved]
    lines += [f"- MISSING: {m}" for m in missing]
    lines.append("Go on with the attach; report a block only when every point above is settled.")
    ev.refused = reason.rstrip(".")
    ev.refused_why = "; ".join(disproved + missing)
    return "\n".join(lines)
