"""Small HTML helpers shared by the mock portals."""

from __future__ import annotations

import secrets
from html import escape as e

from fastapi import Request
from fastapi.responses import HTMLResponse

__all__ = ["e", "page", "variant", "jsessionid", "cpv_with_check_digit"]


def page(title: str, body: str, *, lang: str = "en", status_code: int = 200, head: str = "") -> HTMLResponse:
    html = (
        f"<!doctype html><html lang='{lang}'><head><meta charset='utf-8'><title>{e(title)}</title>{head}</head>"
        f"<body><header><a href='/'>{e(title)}</a> · <a href='#login'>Login</a></header><main>{body}</main>"
        "<footer>Mock portal for Watchtower tests</footer></body></html>"
    )
    return HTMLResponse(html, status_code=status_code)


def variant(request: Request, kind: str) -> str:
    return request.app.state.variants.get(kind, "base")


def jsessionid() -> str:
    """A fresh session id on every response, like real Java portals put into links (the url must not depend on it)."""
    return secrets.token_hex(8).upper()


def cpv_with_check_digit(code: str) -> str:
    """Portals print CPV codes with a check digit (`45212225-1`); the contract wants it stripped."""
    return f"{code}-{sum(int(c) for c in code) % 10}"
