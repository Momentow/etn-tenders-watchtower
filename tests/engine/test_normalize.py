from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from watchtower.engine.dates import parse_when
from watchtower.engine.normalize import (
    content_hash,
    contract_nature,
    country_code,
    cpv_codes,
    deadline,
    normalize_fields,
    postal_code,
    procedure_type,
    publication_date,
    stable_url,
    text,
)
from watchtower.engine.result import DocumentRef, TenderRecord
from watchtower.engine.template import Template

BERLIN = ZoneInfo("Europe/Berlin")
PARIS = ZoneInfo("Europe/Paris")
MADRID = ZoneInfo("Europe/Madrid")
AMSTERDAM = ZoneInfo("Europe/Amsterdam")
WARSAW = ZoneInfo("Europe/Warsaw")


# ---------------------------------------------------------------- deadlines by locale (no formats: auto-detect)


@pytest.mark.parametrize(
    "raw, tz, expected",
    [
        # DE
        ("09.12.2026 10:00 Uhr", BERLIN, "2026-12-09T10:00:00+01:00"),
        ("Mittwoch, 9. Dezember 2026, 10:00 Uhr", BERLIN, "2026-12-09T10:00:00+01:00"),
        ("9. Dez. 2026 10 Uhr", BERLIN, "2026-12-09T10:00:00+01:00"),
        ("15.07.2026 12:00", BERLIN, "2026-07-15T12:00:00+02:00"),  # summer time
        ("31. März 2026, 09:30", BERLIN, "2026-03-31T09:30:00+02:00"),
        # FR
        ("09/12/2026 à 10h00", PARIS, "2026-12-09T10:00:00+01:00"),
        ("mercredi 9 décembre 2026 à 10h", PARIS, "2026-12-09T10:00:00+01:00"),
        ("1er juillet 2026 à 16h30", PARIS, "2026-07-01T16:30:00+02:00"),
        # ES
        ("09/12/2026 10:00", MADRID, "2026-12-09T10:00:00+01:00"),
        ("9 de diciembre de 2026 a las 10:00 horas", MADRID, "2026-12-09T10:00:00+01:00"),
        ("09-12-2026 14:00:00", MADRID, "2026-12-09T14:00:00+01:00"),
        # NL
        ("9 december 2026 om 10:00", AMSTERDAM, "2026-12-09T10:00:00+01:00"),
        ("09-12-2026 10.00 uur", AMSTERDAM, "2026-12-09T10:00:00+01:00"),
        ("9 mrt. 2026 12:00", AMSTERDAM, "2026-03-09T12:00:00+01:00"),
        # PL
        ("2026-12-09 10:00", WARSAW, "2026-12-09T10:00:00+01:00"),
        ("9 grudnia 2026 r. godz. 10:00", WARSAW, "2026-12-09T10:00:00+01:00"),
        ("09.12.2026 r., godz. 10:00", WARSAW, "2026-12-09T10:00:00+01:00"),
        ("15 października 2026 11:00", WARSAW, "2026-10-15T11:00:00+02:00"),
        # explicit offsets and UTC win over the portal zone
        ("2026-12-09T10:00:00+01:00", WARSAW, "2026-12-09T10:00:00+01:00"),
        ("2026-12-09T09:00:00Z", BERLIN, "2026-12-09T09:00:00+00:00"),
        ("09.12.2026 09:00 UTC", BERLIN, "2026-12-09T09:00:00+00:00"),
        ("09.12.2026 10:00 +0100", PARIS, "2026-12-09T10:00:00+01:00"),
        # odd but real
        ("09.12.2026 24:00", BERLIN, "2026-12-10T00:00:00+01:00"),
        ("09.12.26 10:00", BERLIN, "2026-12-09T10:00:00+01:00"),
        ("Dec 9, 2026 3:00 pm", BERLIN, "2026-12-09T15:00:00+01:00"),
        ("09.12.2026 10:00 (bis 15.12.2026)", BERLIN, "2026-12-09T10:00:00+01:00"),
    ],
)
def test_deadlines_by_locale(raw, tz, expected):
    assert deadline(raw, [], tz) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "09.12.2026",  # no time of day: not a deadline we can state
        "9 décembre 2026",
        "",
        None,
        "sofort",
        "32.12.2026 10:00",  # impossible day
        "09.12.2026 25:00",  # impossible hour
        "09.12.1899 10:00",  # implausible year
    ],
)
def test_unreadable_deadlines_are_none(raw):
    assert deadline(raw, [], BERLIN) is None


@pytest.mark.parametrize(
    "raw, tz, expected",
    [
        ("20.11.2026", BERLIN, "2026-11-20"),
        ("20 novembre 2026", PARIS, "2026-11-20"),
        ("20 de noviembre de 2026", MADRID, "2026-11-20"),
        ("20 november 2026", AMSTERDAM, "2026-11-20"),
        ("20 listopada 2026 r.", WARSAW, "2026-11-20"),
        ("2026-11-20", WARSAW, "2026-11-20"),
        ("2026-11-19T23:30:00Z", BERLIN, "2026-11-20"),  # a date-time becomes the portal's local date
        ("20.11.2026 08:15", BERLIN, "2026-11-20"),
    ],
)
def test_publication_dates(raw, tz, expected):
    assert publication_date(raw, [], tz) == expected


# ---------------------------------------------------------------- template formats


@pytest.mark.parametrize(
    "raw, formats, tz, expected",
    [
        ("09.12.2026 10:00", ["%d.%m.%Y %H:%M"], BERLIN, "2026-12-09T10:00:00+01:00"),
        ("9. Dezember 2026 10:00", ["%d. %B %Y %H:%M"], BERLIN, "2026-12-09T10:00:00+01:00"),
        ("9 déc. 2026 10:00", ["%d %b. %Y %H:%M"], PARIS, "2026-12-09T10:00:00+01:00"),
        ("9 grudnia 2026, 10:00", ["%d %B %Y, %H:%M"], WARSAW, "2026-12-09T10:00:00+01:00"),
        # the format doesn't fit (portal added "Uhr"): falls back to auto-detection
        ("09.12.2026 10:00 Uhr", ["%d.%m.%Y %H:%M"], BERLIN, "2026-12-09T10:00:00+01:00"),
        # a format without time directives gives a date, so no deadline
        ("09.12.2026", ["%d.%m.%Y"], BERLIN, None),
    ],
)
def test_template_formats(raw, formats, tz, expected):
    assert deadline(raw, formats, tz) == expected


def test_day_first_never_month_first():
    assert parse_when("03/04/2026", [], PARIS) == date(2026, 4, 3)


def test_dst_gap_and_overlap_keep_an_offset():
    for raw in ("29.03.2026 02:30", "25.10.2026 02:30"):
        value = parse_when(raw, [], BERLIN)
        assert isinstance(value, datetime) and value.utcoffset() is not None


# ---------------------------------------------------------------- CPV, enums, country, postal code


def test_cpv_codes():
    assert cpv_codes(["45000000-7", "45233140-2 Straßenbauarbeiten", "45000000"]) == ["45000000", "45233140"]
    assert cpv_codes("CPV: 72000000-5, 72200000 - 7; 79000000") == ["72000000", "72200000", "79000000"]
    assert cpv_codes("Ref 123456789") is None  # 9 digits is not a CPV code
    assert cpv_codes([]) is None and cpv_codes(None) is None


@pytest.mark.parametrize(
    "label, expected",
    [
        ("open", "open"),
        ("Offenes Verfahren", "open"),
        ("Öffentliche Ausschreibung", "open"),
        ("Nicht offenes Verfahren", "restricted"),
        ("Beschränkte Ausschreibung mit Teilnahmewettbewerb", "restricted"),
        ("Verhandlungsverfahren ohne Teilnahmewettbewerb", "negotiated"),
        ("Freihändige Vergabe", "negotiated"),
        ("Procédure ouverte", "open"),
        ("Appel d'offres restreint", "restricted"),
        ("Procédure adaptée (MAPA)", "other"),
        ("Procedimiento abierto", "open"),
        ("Procedimiento negociado sin publicidad", "negotiated"),
        ("Diálogo competitivo", "other"),
        ("Openbare procedure", "open"),
        ("Niet-openbare procedure", "restricted"),
        ("Przetarg nieograniczony", "open"),
        ("Przetarg ograniczony", "restricted"),
        ("Negocjacje z ogłoszeniem", "negotiated"),
        ("Sonstiges", None),
        ("", None),
    ],
)
def test_procedure_type(label, expected):
    assert procedure_type(label) == expected


@pytest.mark.parametrize(
    "label, expected",
    [
        ("Bauleistung", "works"),
        ("Dienstleistung", "services"),
        ("Lieferleistung", "supplies"),
        ("Travaux", "works"),
        ("Fournitures", "supplies"),
        ("Servicios", "services"),
        ("Suministros", "supplies"),
        ("Werken", "works"),
        ("Diensten", "services"),
        ("Leveringen", "supplies"),
        ("Roboty budowlane", "works"),
        ("Usługi", "services"),
        ("Dostawy", "supplies"),
        ("Mixed", None),
    ],
)
def test_contract_nature(label, expected):
    assert contract_nature(label) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("de", "DE"),
        ("DEU", "DE"),
        ("Deutschland", "DE"),
        ("Österreich", "AT"),
        ("Pays-Bas", "NL"),
        ("España", "ES"),
        ("Polska", "PL"),
        ("EL", "GR"),
        ("UK", "GB"),
        ("XX", None),
        ("Atlantis", None),
        ("", None),
    ],
)
def test_country_code(value, expected):
    assert country_code(value) == expected


def test_postal_code_and_text():
    assert postal_code("D-50667") == "50667"
    assert postal_code("F - 75001") == "75001"
    assert postal_code("1012 AB") == "1012 AB"
    assert postal_code("SW1A 1AA") == "SW1A 1AA"
    assert text("  Stadt Köln ​ ") == "Stadt Köln"
    assert text(["Road", "", "works"]) == "Road works"
    assert text("   ") is None


# ---------------------------------------------------------------- normalize_fields (the executor's entry point)


def test_normalize_fields_end_to_end():
    template = Template.model_validate(
        {
            "timezone": "Europe/Berlin",
            "list": {"kind": "html", "request": {"url": "https://p.example/l"}, "items": {"by": "css", "expr": "tr"}},
            "fields": {
                "id": {"by": "css", "expr": ".id"},
                "url": {"by": "css", "expr": "a", "attr": "href"},
                "publicationDate": {"by": "css", "expr": ".pub"},
                "submissionDeadline": {"by": "css", "expr": ".dl", "formats": ["%d.%m.%Y %H:%M"]},
            },
        }
    )
    raw = {
        "id": " VG-1 ",
        "url": "https://p.example/t/1;jsessionid=ABC?x=1",
        "title": "Straßen bau",
        "buyerName": None,
        "publicationDate": "20.11.2026",
        "submissionDeadline": "09.12.2026 10:00",
        "procedureType": "Offenes Verfahren",
        "contractNature": "Bauleistung",
        "cpvCodes": ["45000000-7"],
        "placeOfPerformance.postalCode": "D-50667",
        "placeOfPerformance.city": "Köln",
        "placeOfPerformance.countryCode": "Deutschland",
    }
    out = normalize_fields(raw, template)
    assert out == {
        "id": "VG-1",
        "url": "https://p.example/t/1?x=1",
        "title": "Straßen bau",
        "buyerName": None,
        "referenceNumber": None,
        "description": None,
        "publicationDate": "2026-11-20",
        "submissionDeadline": "2026-12-09T10:00:00+01:00",
        "procedureType": "open",
        "contractNature": "works",
        "cpvCodes": ["45000000"],
        "placeOfPerformance": {"postalCode": "50667", "city": "Köln", "countryCode": "DE"},
    }


# ---------------------------------------------------------------- content hash


def _tender(**kw) -> TenderRecord:
    base = dict(
        id="A",
        url="https://p.example/t/A",
        title="Road works",
        buyerName="Stadt",
        publicationDate="2026-11-20",
        submissionDeadline="2026-12-09T10:00:00+01:00",
        documents=[
            DocumentRef(url="https://p.example/d/1.pdf?sid=111", fileName="a.pdf"),
            DocumentRef(url="https://p.example/d/2.pdf", fileName="b.pdf"),
        ],
    )
    return TenderRecord(**(base | kw))


def test_content_hash_is_stable_and_ignores_noise():
    h = content_hash(_tender())
    assert h == content_hash(_tender())
    assert len(h) == 64
    # document order, session ids, cache busters, status and completeness don't count
    reordered = _tender(
        documents=[
            DocumentRef(url="https://p.example/d/2.pdf?_=1700000000", fileName="b.pdf"),
            DocumentRef(url="https://p.example/d/1.pdf?sid=222", fileName="a.pdf"),
        ]
    )
    assert content_hash(reordered) == h
    assert content_hash(_tender(status="withdrawn", complete=False)) == h


@pytest.mark.parametrize(
    "change",
    [
        {"title": "Road works, lot 2"},
        {"submissionDeadline": "2026-12-10T10:00:00+01:00"},
        {"cpvCodes": ["45000000"]},
        {"documents": [DocumentRef(url="https://p.example/d/1.pdf", fileName="a.pdf")]},
        {
            "documents": [
                DocumentRef(url="https://p.example/d/1.pdf?version=2", fileName="a.pdf"),
                DocumentRef(url="https://p.example/d/2.pdf", fileName="b.pdf"),
            ]
        },
    ],
)
def test_content_hash_changes_with_content(change):
    assert content_hash(_tender(**change)) != content_hash(_tender())


def test_stable_url():
    assert stable_url("HTTPS://P.example/d;jsessionid=X?b=2&sid=9&a=1#top") == "https://p.example/d?a=1&b=2"
