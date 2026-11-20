"""Login and captcha detection helpers (used to report `Blocked`).

These only collect evidence. Some portals show a captcha or a login box while the tender list and documents still load
without it, so the caller (the agent's `detect_block` tool, T-015/T-022) must *try first, then decide*.
"""

import re
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser as HTMLParser

BlockKind = Literal["captcha", "login"]

_CAPTCHA_MARKERS: list[tuple[str, str]] = [
    ("g-recaptcha", "reCAPTCHA widget"),
    ("google.com/recaptcha", "reCAPTCHA script"),
    ("recaptcha/api.js", "reCAPTCHA script"),
    ("h-captcha", "hCaptcha widget"),
    ("hcaptcha.com", "hCaptcha script"),
    ("cf-turnstile", "Cloudflare Turnstile widget"),
    ("challenges.cloudflare.com", "Cloudflare challenge"),
    ("cf-chl-", "Cloudflare challenge"),
    ("frc-captcha", "Friendly Captcha widget"),
    ("geetest", "GeeTest captcha"),
    ("captcha-delivery.com", "DataDome captcha"),
]
_CAPTCHA_TITLES = re.compile(r"just a moment|attention required|are you a robot|captcha|access denied", re.I)
_CAPTCHA_FIELD = re.compile(r"captcha", re.I)
_LOGIN_PATH = re.compile(
    r"/(login|log-in|signin|sign-in|logon|anmelden|anmeldung|connexion|identification|logowanie|zaloguj|inloggen"
    r"|accesso|acceso|iniciar-sesion|auth|sso|cas/login)\b",
    re.I,
)
_LOGIN_TEXT = re.compile(
    r"\b(log ?in|sign ?in|anmelden|einloggen|se connecter|connexion|zaloguj|inloggen|accedi|iniciar sesi[oó]n"
    r"|identifiez-vous|bitte melden sie sich an|please log in|login required)\b",
    re.I,
)


@dataclass
class BlockSignal:
    kind: BlockKind
    strong: bool  # True: the page itself looks like the block (a challenge or login page), not just a widget on it
    evidence: list[str] = field(default_factory=list)


def detect_block(
    html: str, *, url: str | None = None, status: int | None = None, headers: dict[str, str] | None = None
) -> list[BlockSignal]:
    """Captcha and login signals found on a page, strongest kind first. Empty list: no sign of a block."""
    signals = [s for s in (_captcha(html, status, headers or {}), _login(html, url, status, headers or {})) if s]
    return sorted(signals, key=lambda s: not s.strong)


def _captcha(html: str, status: int | None, headers: dict[str, str]) -> BlockSignal | None:
    lowered = html.lower()
    evidence = sorted({label for marker, label in _CAPTCHA_MARKERS if marker in lowered})
    tree = HTMLParser(html)
    for node in tree.css("input, form, iframe, div, img"):
        attrs = " ".join(f"{k}={v}" for k, v in node.attributes.items() if v)
        if _CAPTCHA_FIELD.search(attrs):
            evidence.append(f"element <{node.tag}> mentions captcha")
            break
    if not evidence:
        return None
    title = tree.css_first("title")
    title_text = title.text(strip=True) if title else ""
    challenge_title = bool(_CAPTCHA_TITLES.search(title_text))
    if challenge_title:
        evidence.append(f"page title: {title_text!r}")
    lower_headers = {k.lower(): v for k, v in headers.items()}
    if "cf-mitigated" in lower_headers:
        evidence.append("Cloudflare cf-mitigated header")
    strong = challenge_title or "cf-mitigated" in lower_headers or (status in (403, 429, 503) and _thin(tree))
    return BlockSignal("captcha", strong, evidence)


def _login(html: str, url: str | None, status: int | None, headers: dict[str, str]) -> BlockSignal | None:
    evidence: list[str] = []
    lower_headers = {k.lower(): v for k, v in headers.items()}
    if status == 401 or "www-authenticate" in lower_headers:
        evidence.append("HTTP authentication required")
    tree = HTMLParser(html)
    has_password = tree.css_first("input[type=password]") is not None
    if has_password:
        evidence.append("password field on the page")
    login_url = bool(url and _LOGIN_PATH.search(urlsplit(url).path))
    if login_url:
        evidence.append(f"login URL: {urlsplit(url).path}")
    body = tree.body.text(separator=" ", strip=True) if tree.body else ""
    if has_password and _LOGIN_TEXT.search(body):
        evidence.append("login wording next to the form")
    if not evidence:
        return None
    strong = status == 401 or (has_password and (login_url or _thin(tree)))
    if not has_password and status != 401 and "www-authenticate" not in lower_headers:
        return None  # a login-looking URL alone isn't evidence of a block
    return BlockSignal("login", strong, evidence)


def _thin(tree: HTMLParser) -> bool:
    """A page with little besides the form: few links and little text (a challenge/login page, not a tender list)."""
    links = len(tree.css("a[href]"))
    text = tree.body.text(separator=" ", strip=True) if tree.body else ""
    return links < 15 and len(text) < 1500
