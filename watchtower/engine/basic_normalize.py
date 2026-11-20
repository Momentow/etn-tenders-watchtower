"""Minimal normalisation of raw extracted values into contract values, so the executor (T-009) returns contract-shaped
tenders before the real normalisers land.

**T-010 replaces this module** (locale-aware dates, enum mapping, country codes, content hash). Keep the single entry
point `normalize_fields(raw, template)`: the executor calls only that.

Rule throughout: a value we can't read with certainty becomes None (required) or is left out (optional).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from watchtower.engine.template import DateRule, Template

PROCEDURE_TYPES = frozenset({"open", "restricted", "negotiated", "other"})
CONTRACT_NATURES = frozenset({"works", "services", "supplies"})
_TIME_DIRECTIVES = re.compile(r"%[HIMSXcTRp]")
_ISO_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CPV = re.compile(r"(?<!\d)(\d{8})(?:-\d)?(?!\d)")
_JSESSIONID = re.compile(r";jsessionid=[^?#/]*", re.I)

Raw = dict[str, str | list[str] | None]


def normalize_fields(raw: Raw, template: Template) -> dict[str, Any]:
    """Raw values keyed by field name (`placeOfPerformance.city` flattened) → contract fields (no status/documents)."""
    rules = template.fields.rules()
    tz = ZoneInfo(template.timezone)
    out: dict[str, Any] = {}
    for name in ("id", "url", "title", "buyerName", "referenceNumber", "description"):
        out[name] = _text(raw.get(name))
    if out["url"]:
        out["url"] = _JSESSIONID.sub("", out["url"])

    pub_rule = rules.get("publicationDate")
    pub = _parse(raw.get("publicationDate"), pub_rule, tz) if isinstance(pub_rule, DateRule) else None
    if isinstance(pub, datetime):  # (datetime is a date subclass: check it first)
        pub = pub.astimezone(tz).date()
    out["publicationDate"] = pub.isoformat() if pub else None

    dl_rule = rules.get("submissionDeadline")
    dl = _parse(raw.get("submissionDeadline"), dl_rule, tz) if isinstance(dl_rule, DateRule) else None
    # A deadline without a time of day is not a deadline we can state truthfully: leave it out.
    out["submissionDeadline"] = dl.isoformat(timespec="seconds") if isinstance(dl, datetime) else None

    proc = _text(raw.get("procedureType"))
    out["procedureType"] = proc if proc in PROCEDURE_TYPES else None
    nature = _text(raw.get("contractNature"))
    out["contractNature"] = nature if nature in CONTRACT_NATURES else None

    cpv = raw.get("cpvCodes")
    codes = [m.group(1) for v in ([cpv] if isinstance(cpv, str) else cpv or []) for m in _CPV.finditer(v)]
    out["cpvCodes"] = list(dict.fromkeys(codes)) or None

    place = {
        "postalCode": _text(raw.get("placeOfPerformance.postalCode")),
        "city": _text(raw.get("placeOfPerformance.city")),
        "countryCode": _country(raw.get("placeOfPerformance.countryCode")),
    }
    out["placeOfPerformance"] = {k: v for k, v in place.items() if v} or None
    return out


def _text(v: str | list[str] | None) -> str | None:
    if isinstance(v, list):
        v = " ".join(v)
    v = " ".join(v.split()) if v else None
    return v or None


def _country(v: str | list[str] | None) -> str | None:
    t = _text(v)
    return t.upper() if t and re.fullmatch(r"[A-Za-z]{2}", t) else None


def _parse(v: str | list[str] | None, rule: DateRule, tz: ZoneInfo) -> date | datetime | None:
    """date when the value has no time of day, aware datetime otherwise (naive times read in the portal's zone)."""
    s = _text(v)
    if not s:
        return None
    parsed: date | datetime | None = None
    if rule.formats:
        for fmt in rule.formats:
            try:
                dt = datetime.strptime(s, fmt)
            except ValueError:
                continue
            parsed = dt if _TIME_DIRECTIVES.search(fmt) else dt.date()
            break
    else:
        try:
            parsed = date.fromisoformat(s) if _ISO_DATE_ONLY.match(s) else datetime.fromisoformat(s)
        except ValueError:
            parsed = None
    if isinstance(parsed, datetime) and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed
