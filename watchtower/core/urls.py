"""Portal URL normalisation: the same address always maps to the same portal id."""

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_url(url: str) -> str:
    """Canonical form of a portal address.

    Lower-cases scheme and host, drops the default port, the fragment and a trailing slash, sorts query parameters,
    and assumes https when no scheme is given. Path case and query values are kept: servers may treat them as distinct.
    """
    raw = url.strip()
    if not raw:
        raise ValueError("empty URL")
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        raise ValueError(f"unsupported URL scheme: {parts.scheme!r}")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise ValueError(f"URL has no host: {url!r}")
    netloc = host
    if parts.port is not None and parts.port != _DEFAULT_PORTS[scheme]:
        netloc = f"{host}:{parts.port}"
    path = parts.path.rstrip("/") or "/"
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit((scheme, netloc, path, query, ""))


def portal_id_for(url: str) -> str:
    """Stable id derived from the normalised URL (same address → same id, even across fresh databases)."""
    return hashlib.sha256(normalize_url(url).encode()).hexdigest()[:12]
