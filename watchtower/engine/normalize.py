"""Normalisers: raw extracted strings → contract values (T-010), and the tender content hash.

The executor calls one entry point, `normalize_fields(raw, template)`. The pipeline calls `content_hash(tender)` to
decide `lastChangedAt`. Rule throughout: a value we can't read with certainty becomes None (required fields) or is left
out (optional ones). A wrong value is worse than a missing one.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from watchtower.engine.dates import parse_when
from watchtower.engine.template import DateRule, Template

if TYPE_CHECKING:
    from watchtower.engine.result import TenderRecord

Raw = dict[str, str | list[str] | None]

PROCEDURE_TYPES = frozenset({"open", "restricted", "negotiated", "other"})
CONTRACT_NATURES = frozenset({"works", "services", "supplies"})

# Checked in order; the first phrase found in the folded label wins, so "not open" forms come before "open".
_PROCEDURE_PHRASES: tuple[tuple[str, str], ...] = (
    # restricted
    ("nicht offen", "restricted"),
    ("nichtoffen", "restricted"),
    ("beschrankte ausschreibung", "restricted"),
    ("beschranktes verfahren", "restricted"),
    ("procedure restreinte", "restricted"),
    ("appel d'offres restreint", "restricted"),
    ("procedimiento restringido", "restricted"),
    ("niet-openbare", "restricted"),
    ("niet openbare", "restricted"),
    ("przetarg ograniczony", "restricted"),
    ("procedura ristretta", "restricted"),
    ("procedimento restrito", "restricted"),
    ("restricted", "restricted"),
    # negotiated
    ("verhandlungsverfahren", "negotiated"),
    ("verhandlungsvergabe", "negotiated"),
    ("freihandige vergabe", "negotiated"),
    ("procedure negociee", "negotiated"),
    ("procedimiento negociado", "negotiated"),
    ("onderhandelingsprocedure", "negotiated"),
    ("negocjac", "negotiated"),
    ("procedura negoziata", "negotiated"),
    ("procedimento por negociacao", "negotiated"),
    ("negotiated", "negotiated"),
    # other (dialogue, partnership, adapted/simplified procedures)
    ("wettbewerblicher dialog", "other"),
    ("dialogue competitif", "other"),
    ("dialogo competitivo", "other"),
    ("concurrentiegerichte dialoog", "other"),
    ("dialog konkurencyjny", "other"),
    ("competitive dialogue", "other"),
    ("innovationspartnerschaft", "other"),
    ("partenariat d'innovation", "other"),
    ("asociacion para la innovacion", "other"),
    ("innovatiepartnerschap", "other"),
    ("partnerstwo innowacyjne", "other"),
    ("innovation partnership", "other"),
    ("procedure adaptee", "other"),
    ("mapa", "other"),
    ("procedimiento abierto simplificado", "other"),
    # open
    ("offenes verfahren", "open"),
    ("offentliche ausschreibung", "open"),
    ("procedure ouverte", "open"),
    ("appel d'offres ouvert", "open"),
    ("procedimiento abierto", "open"),
    ("openbare procedure", "open"),
    ("przetarg nieograniczony", "open"),
    ("procedura aperta", "open"),
    ("concurso publico", "open"),
    ("open procedure", "open"),
    ("open", "open"),
)
_NATURE_PHRASES: tuple[tuple[str, str], ...] = (
    ("bauleistung", "works"),
    ("bauauftrag", "works"),
    ("bauarbeiten", "works"),
    ("vob", "works"),
    ("travaux", "works"),
    ("obras", "works"),
    ("obra", "works"),
    ("werken", "works"),
    ("roboty budowlane", "works"),
    ("lavori", "works"),
    ("empreitada", "works"),
    ("works", "works"),
    ("dienstleistung", "services"),
    ("services", "services"),
    ("servicios", "services"),
    ("diensten", "services"),
    ("uslugi", "services"),
    ("servizi", "services"),
    ("servicos", "services"),
    ("service", "services"),
    ("lieferleistung", "supplies"),
    ("lieferauftrag", "supplies"),
    ("lieferung", "supplies"),
    ("fournitures", "supplies"),
    ("suministros", "supplies"),
    ("suministro", "supplies"),
    ("leveringen", "supplies"),
    ("levering", "supplies"),
    ("dostawy", "supplies"),
    ("dostawa", "supplies"),
    ("forniture", "supplies"),
    ("fornecimento", "supplies"),
    ("supplies", "supplies"),
    ("supply", "supplies"),
)

# ISO 3166-1 alpha-2 codes of Europe and close partners; EU shorthand EL/UK mapped to ISO.
_COUNTRY_CODES = frozenset(
    "AD AL AM AT AZ BA BE BG BY CH CY CZ DE DK EE ES FI FO FR GB GE GI GR HR HU IE IS IT LI LT LU LV MC MD ME MK MT "
    "NL NO PL PT RO RS RU SE SI SK SM TR UA VA XK US CA".split()
)
_COUNTRY_ALIASES = {
    "EL": "GR",
    "UK": "GB",
    "DEU": "DE",
    "FRA": "FR",
    "ESP": "ES",
    "NLD": "NL",
    "POL": "PL",
    "AUT": "AT",
    "BEL": "BE",
    "ITA": "IT",
    "PRT": "PT",
    "CHE": "CH",
    "LUX": "LU",
    "DNK": "DK",
    "SWE": "SE",
    "FIN": "FI",
    "IRL": "IE",
    "CZE": "CZ",
    "GERMANY": "DE",
    "DEUTSCHLAND": "DE",
    "ALLEMAGNE": "DE",
    "ALEMANIA": "DE",
    "DUITSLAND": "DE",
    "NIEMCY": "DE",
    "FRANCE": "FR",
    "FRANKREICH": "FR",
    "FRANCIA": "FR",
    "FRANKRIJK": "FR",
    "FRANCJA": "FR",
    "SPAIN": "ES",
    "ESPANA": "ES",
    "SPANIEN": "ES",
    "ESPAGNE": "ES",
    "SPANJE": "ES",
    "HISZPANIA": "ES",
    "NETHERLANDS": "NL",
    "NEDERLAND": "NL",
    "NIEDERLANDE": "NL",
    "PAYS-BAS": "NL",
    "PAISES BAJOS": "NL",
    "HOLANDIA": "NL",
    "THE NETHERLANDS": "NL",
    "POLAND": "PL",
    "POLSKA": "PL",
    "POLEN": "PL",
    "POLOGNE": "PL",
    "POLONIA": "PL",
    "AUSTRIA": "AT",
    "OSTERREICH": "AT",
    "AUTRICHE": "AT",
    "OOSTENRIJK": "AT",
    "BELGIUM": "BE",
    "BELGIEN": "BE",
    "BELGIQUE": "BE",
    "BELGIE": "BE",
    "BELGICA": "BE",
    "BELGIA": "BE",
    "ITALY": "IT",
    "ITALIA": "IT",
    "ITALIEN": "IT",
    "ITALIE": "IT",
    "PORTUGAL": "PT",
    "SWITZERLAND": "CH",
    "SCHWEIZ": "CH",
    "SUISSE": "CH",
    "SVIZZERA": "CH",
    "LUXEMBOURG": "LU",
    "LUXEMBURG": "LU",
    "IRELAND": "IE",
    "DENMARK": "DK",
    "DANMARK": "DK",
    "SWEDEN": "SE",
    "SVERIGE": "SE",
    "FINLAND": "FI",
    "SUOMI": "FI",
    "CZECHIA": "CZ",
    "CZECH REPUBLIC": "CZ",
    "UNITED KINGDOM": "GB",
}

_CPV = re.compile(r"(?<!\d)(\d{8})(?:\s*-\s*\d)?(?!\d)")
_POSTAL_PREFIX = re.compile(r"^(?:[A-Z]{1,3})\s*-\s*(?=\d)")
_JSESSIONID = re.compile(r";jsessionid=[^?#/]*", re.I)
_VOLATILE_PARAMS = frozenset(
    {
        "jsessionid",
        "phpsessid",
        "sid",
        "sessionid",
        "session",
        "token",
        "csrf",
        "_",
        "t",
        "ts",
        "timestamp",
        "cache",
        "nocache",
        "rnd",
        "random",
    }
)
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)


def normalize_fields(raw: Raw, template: Template) -> dict[str, Any]:
    """Raw values keyed like `Fields.rules()` (`placeOfPerformance.city` flattened) → contract fields.

    Returns every contract field the engine fills except `status` and `documents`; optional ones are None when not
    shown (the record drops them).
    """
    rules = template.fields.rules()
    tz = ZoneInfo(template.timezone)
    out: dict[str, Any] = {}
    for name in ("id", "title", "buyerName", "referenceNumber", "description"):
        out[name] = text(raw.get(name))
    out["url"] = clean_url(text(raw.get("url")))

    out["publicationDate"] = publication_date(raw.get("publicationDate"), _formats(rules.get("publicationDate")), tz)
    out["submissionDeadline"] = deadline(raw.get("submissionDeadline"), _formats(rules.get("submissionDeadline")), tz)

    out["procedureType"] = procedure_type(raw.get("procedureType"))
    out["contractNature"] = contract_nature(raw.get("contractNature"))
    out["cpvCodes"] = cpv_codes(raw.get("cpvCodes"))

    place = {
        "postalCode": postal_code(raw.get("placeOfPerformance.postalCode")),
        "city": text(raw.get("placeOfPerformance.city")),
        "countryCode": country_code(raw.get("placeOfPerformance.countryCode")),
    }
    out["placeOfPerformance"] = {k: v for k, v in place.items() if v} or None
    return out


# --------------------------------------------------------------------------- single fields


def text(value: str | list[str] | None) -> str | None:
    """Whitespace collapsed, invisible characters removed; empty → None. Lists are joined with spaces."""
    if isinstance(value, list):
        value = " ".join(v for v in value if v)
    if not value:
        return None
    cleaned = " ".join(unicodedata.normalize("NFC", value).translate(_INVISIBLE).split())
    return cleaned or None


def clean_url(url: str | None) -> str | None:
    return _JSESSIONID.sub("", url) if url else None


def publication_date(value: str | list[str] | None, formats: list[str], tz: ZoneInfo) -> str | None:
    """A date, e.g. 2026-11-20. A date-time is converted to the portal's zone first."""
    parsed = parse_when(text(value), formats, tz)
    if isinstance(parsed, datetime):  # (datetime is a date subclass: check it first)
        parsed = parsed.astimezone(tz).date()
    return parsed.isoformat() if isinstance(parsed, date) else None


def deadline(value: str | list[str] | None, formats: list[str], tz: ZoneInfo) -> str | None:
    """Date-time with offset, e.g. 2026-12-09T10:00:00+01:00. A date without a time of day is None: we can't state
    the deadline truthfully."""
    parsed = parse_when(text(value), formats, tz)
    return parsed.isoformat(timespec="seconds") if isinstance(parsed, datetime) else None


def procedure_type(value: str | list[str] | None) -> str | None:
    return _classify(value, PROCEDURE_TYPES, _PROCEDURE_PHRASES)


def contract_nature(value: str | list[str] | None) -> str | None:
    return _classify(value, CONTRACT_NATURES, _NATURE_PHRASES)


def cpv_codes(value: str | list[str] | None) -> list[str] | None:
    """8-digit codes without check digit, in order, without duplicates."""
    items = [value] if isinstance(value, str) else value or []
    codes = [m.group(1) for v in items if v for m in _CPV.finditer(v)]
    return list(dict.fromkeys(codes)) or None


def postal_code(value: str | list[str] | None) -> str | None:
    """Postal code as shown, minus a country prefix (`D-50667` → `50667`, `F-75001` → `75001`)."""
    t = text(value)
    return _POSTAL_PREFIX.sub("", t.upper() if t and t[:1].isalpha() else t) if t else None


def country_code(value: str | list[str] | None) -> str | None:
    """ISO 3166-1 alpha-2 from a code (DE, DEU, EL→GR) or a country name in several languages; unknown → None."""
    t = text(value)
    if not t:
        return None
    key = _fold(t).upper().strip(" .")
    code = _COUNTRY_ALIASES.get(key, key)
    return code if code in _COUNTRY_CODES else None


# --------------------------------------------------------------------------- content hash


def content_hash(tender: TenderRecord) -> str:
    """sha256 of what a person would call the tender's content: contract fields and documents (URL without session
    or cache-busting parameters, and name), order-independent. Status is compared separately by the store, and
    `complete` isn't content. Stable across reads of an unchanged tender."""
    data = tender.contract_data()
    data.pop("status", None)
    docs = sorted({(stable_url(d.url), d.fileName or "") for d in tender.documents})
    payload = json.dumps({"fields": data, "documents": docs}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def stable_url(url: str) -> str:
    """URL without session ids and cache-busting query parameters, with the remaining parameters sorted."""
    parts = urlsplit(_JSESSIONID.sub("", url))
    query = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in _VOLATILE_PARAMS
    )
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), ""))


# --------------------------------------------------------------------------- helpers


def _formats(rule: object) -> list[str]:
    return list(rule.formats) if isinstance(rule, DateRule) else []


def _classify(
    value: str | list[str] | None, allowed: frozenset[str], phrases: tuple[tuple[str, str], ...]
) -> str | None:
    t = text(value)
    if not t:
        return None
    folded = _fold(t)
    if folded in allowed:  # already a contract value (e.g. from the template's `map`)
        return folded
    padded = f" {re.sub(r'[^a-z0-9\'-]+', ' ', folded)} "
    for phrase, result in phrases:
        if f" {phrase} " in padded or (len(phrase) > 6 and phrase in padded):
            return result
    return None


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.lower().replace("ł", "l").replace("ß", "ss"))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip()
