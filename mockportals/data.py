"""Deterministic tender data behind every mock portal, plus the contract-shaped answers tests compare against.

Dates are relative to `today` (default: the real date, or `MOCK_TODAY=YYYY-MM-DD`), so open tenders stay open
whenever the tests run. Every portal shows its own local-time strings; `expected_tenders()` gives the contract
values (ISO 8601 with the portal's offset).
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

BASE = "http://localhost:8081"


@dataclass(frozen=True)
class Doc:
    name: str
    path: str  # path on the mock server, e.g. /files/ssr/VG-2026-001/Leistungsverzeichnis.pdf

    @property
    def url(self) -> str:
        return BASE + self.path


@dataclass(frozen=True)
class MockTender:
    kind: str
    id: str
    title: str
    buyer: str
    published: date
    deadline: datetime  # aware, in the portal's time zone
    reference: str
    description: str
    procedure: str  # contract value: open | restricted | negotiated | other
    nature: str  # contract value: works | services | supplies
    cpv: tuple[str, ...]  # 8 digits, no check digit
    postal_code: str
    city: str
    country: str
    status: str = "open"  # open | closed | withdrawn
    docs: tuple[Doc, ...] = ()
    docs_restricted: bool = False  # documents shown only to registered users (no links on the page)
    new: bool = False  # only visible in the `silent-missed` variant


@dataclass(frozen=True)
class Portal:
    kind: str
    tz: str
    country: str
    tenders: tuple[MockTender, ...] = field(default=())


def today() -> date:
    env = os.environ.get("MOCK_TODAY")
    return date.fromisoformat(env) if env else date.today()


_SUBJECTS = {
    "DE": [
        ("Sanierung der Sporthalle", "works", "45212225"),
        ("Reinigung von Verwaltungsgebäuden", "services", "90911200"),
        ("Lieferung von Schulmöbeln", "supplies", "39160000"),
        ("Neubau einer Kindertagesstätte", "works", "45214100"),
        ("Winterdienst auf Kreisstraßen", "services", "90620000"),
        ("IT-Hardware für Schulen", "supplies", "30213000"),
        ("Fahrbahnerneuerung Ortsdurchfahrt", "works", "45233142"),
        ("Planungsleistungen Brückenbau", "services", "71322000"),
    ],
    "FR": [
        ("Travaux de réhabilitation du gymnase", "works", "45212225"),
        ("Nettoyage des locaux administratifs", "services", "90911200"),
        ("Fourniture de mobilier scolaire", "supplies", "39160000"),
        ("Maîtrise d'œuvre pour la médiathèque", "services", "71000000"),
        ("Entretien des espaces verts", "services", "77310000"),
        ("Fourniture de véhicules électriques", "supplies", "34144900"),
    ],
    "ES": [
        ("Obras de reforma del polideportivo", "works", "45212225"),
        ("Servicio de limpieza de edificios municipales", "services", "90911200"),
        ("Suministro de mobiliario escolar", "supplies", "39160000"),
        ("Mantenimiento de alumbrado público", "services", "50232100"),
        ("Suministro de equipos informáticos", "supplies", "30213000"),
    ],
    "PL": [
        ("Remont hali sportowej", "works", "45212225"),
        ("Sprzątanie budynków urzędu", "services", "90911200"),
        ("Dostawa mebli szkolnych", "supplies", "39160000"),
        ("Budowa drogi gminnej", "works", "45233140"),
        ("Dostawa sprzętu komputerowego", "supplies", "30213000"),
    ],
    "NL": [
        ("Renovatie sporthal", "works", "45212225"),
        ("Schoonmaak gemeentelijke gebouwen", "services", "90911200"),
        ("Levering schoolmeubilair", "supplies", "39160000"),
        ("Onderhoud openbare verlichting", "services", "50232100"),
    ],
    "AT": [
        ("Generalsanierung Volksschule", "works", "45214200"),
        ("Gebäudereinigung Bezirkshauptmannschaft", "services", "90911200"),
        ("Lieferung von Feuerwehrfahrzeugen", "supplies", "34144210"),
    ],
    "BE": [
        ("Renovatie van het gemeentehuis", "works", "45212350"),
        ("Levering van ICT-materiaal", "supplies", "30213000"),
        ("Onderhoud van groenzones", "services", "77310000"),
    ],
}
_BUYERS = {
    "DE": [("Stadt Musterstadt", "12345", "Musterstadt"), ("Landkreis Beispielkreis", "54321", "Beispielhausen")],
    "FR": [("Commune de Villeneuve", "75012", "Paris"), ("Département de l'Exemple", "69003", "Lyon")],
    "ES": [("Ayuntamiento de Villanueva", "28001", "Madrid"), ("Diputación de Ejemplo", "08001", "Barcelona")],
    "PL": [("Gmina Przykładowo", "00-950", "Warszawa"), ("Powiat Wzorcowy", "30-001", "Kraków")],
    "NL": [("Gemeente Voorbeeld", "1012 AB", "Amsterdam"), ("Provincie Proef", "3511 AA", "Utrecht")],
    "AT": [("Marktgemeinde Musterdorf", "1010", "Wien")],
    "BE": [("Gemeente Voorbeeldegem", "1000", "Brussel")],
}
_LOT_WORDS = {"DE": ["Rahmenvertrag", "Einzelauftrag"], "AT": ["Rahmenvertrag"], "*": ["Lot 1", "Lote 1", "Część 1"]}
_PROCEDURES = ["open", "open", "open", "restricted", "negotiated", "other"]

# kind -> (time zone, country, reference prefix, count, file kinds per tender)
_SPECS = {
    "ssr": ("Europe/Berlin", "DE", "VG-{y}-", 23),
    "js": ("Europe/Paris", "FR", "{y}-AO-", 57),
    "multistep": ("Europe/Madrid", "ES", "EXP-{y}/", 12),
    "form": ("Europe/Warsaw", "PL", "ZP.271.", 14),
    "captcha": ("Europe/Amsterdam", "NL", "AB-{y}-", 8),
    "login": ("Europe/Vienna", "AT", "W-{y}-", 6),
    "robot": ("Europe/Brussels", "BE", "B/{y}/", 6),
}
KINDS = tuple(_SPECS)

_DOC_NAMES = {
    "DE": ["Leistungsverzeichnis.pdf", "Vergabeunterlagen.zip", "Formblätter.pdf"],
    "FR": ["RC.pdf", "CCAP.pdf", "DCE.zip"],
    "ES": ["Pliego de cláusulas administrativas.pdf", "Pliego de prescripciones técnicas.pdf"],
    "PL": ["SWZ.pdf", "Załączniki.zip"],
    "NL": ["Aanbestedingsleidraad.pdf", "Bijlagen.zip"],
    "AT": ["Ausschreibungsunterlagen.pdf"],
    "BE": ["Bestek.pdf"],
}


def _build(kind: str, base: date) -> Portal:
    tz_name, country, prefix, count = _SPECS[kind]
    tz = ZoneInfo(tz_name)
    rnd = random.Random(f"mock-{kind}")  # same data on every run
    subjects, buyers = _SUBJECTS[country], _BUYERS[country]
    # The DE example template maps only open/restricted/negotiated labels, so the SSR portal shows no "other" procedure.
    procedures = [p for p in _PROCEDURES if p != "other"] if kind == "ssr" else _PROCEDURES
    tenders = []
    for i in range(count):
        subj, nature, cpv = subjects[i % len(subjects)]
        buyer, plz, city = buyers[i % len(buyers)]
        ref = prefix.format(y=base.year) + f"{i + 1:03d}"
        tid = {"js": f"{700000 + i * 7}", "multistep": f"EXP-{base.year}-{i + 1:04d}"}.get(kind, ref)
        if kind == "multistep":
            ref = tid  # the list shows one "expediente" number, used as id and reference
        published = base - timedelta(days=1 + (i * 3) % 28)
        # Deadlines in local time; one lands on a DST-change-free winter date, some in summer for offset variety.
        deadline = datetime.combine(base + timedelta(days=5 + (i * 11) % 200), time(10 + i % 3, 0 if i % 2 else 30), tz)
        status = "open"
        if kind in ("ssr", "js") and i == 4:
            status = "withdrawn"
        if kind in ("ssr", "js") and i == 6:
            status = "closed"
            deadline = datetime.combine(base - timedelta(days=2), time(12, 0), tz)
        names = _DOC_NAMES[country][: 1 + i % len(_DOC_NAMES[country])]
        safe_id = tid.replace("/", "-")
        docs = tuple(Doc(n, f"/files/{kind}/{safe_id}/{n}") for n in names)
        if kind == "multistep" and i == 0:
            docs += (Doc("Planos.zip", f"/files/large/{safe_id}/Planos.zip"),)  # 300 MB: may be skipped
        restricted = kind == "ssr" and i == 9
        tenders.append(
            MockTender(
                kind=kind,
                id=tid,
                title=f"{subj} – Los {i + 1}" if i >= len(subjects) else subj,
                buyer=buyer,
                published=published,
                deadline=deadline,
                reference=ref,
                description=f"{subj}. {rnd.choice(_LOT_WORDS.get(country, _LOT_WORDS['*']))} ({ref}).",
                procedure=procedures[i % len(procedures)],
                nature=nature,
                cpv=(cpv,) + (("71000000",) if i % 3 == 0 and cpv != "71000000" else ()),
                postal_code=plz,
                city=city,
                country=country,
                status=status,
                docs=() if restricted else docs,
                docs_restricted=restricted,
                new=kind in ("ssr", "js") and i >= count - 3,
            )
        )
    return Portal(kind, tz_name, country, tuple(tenders))


def unpacked_names(tender_id: str, doc: Doc) -> list[str]:
    """File names after unpacking (see files.document): ZIPs hold two PDFs and a nested ZIP with one more PDF."""
    if doc.path.startswith("/files/large/"):
        return []  # over 250 MB: a correct read may leave it out
    if doc.name.lower().endswith(".zip"):
        return [f"{tender_id} Teil 1.pdf", f"{tender_id} Teil 2.pdf", "Plan.pdf"]
    return [doc.name]


def portal(kind: str, base: date | None = None) -> Portal:
    return _build(kind, base or today())


def visible(kind: str, variant: str = "base", base: date | None = None) -> list[MockTender]:
    """Tenders the portal lists in this variant (the `new` ones appear only in `silent-missed`)."""
    p = portal(kind, base)
    return [t for t in p.tenders if variant == "silent-missed" or not t.new]


def expected_tenders(kind: str, variant: str = "base", base: date | None = None) -> list[dict]:
    """Contract-shaped expectation (api-contract.md) for what a correct read of this portal returns.

    Fields the portal never shows are left out (optional) or None (required). `documents` lists the file names a correct
    read stores, after unpacking ZIPs and skipping files over 250 MB (sha256/path depend on our store). `url` is None
    because it depends on how the engine follows links.
    """
    out = []
    for t in visible(kind, variant, base):
        rec = {
            "id": t.id,
            "url": None,
            "title": t.title,
            "buyerName": t.buyer,
            "publicationDate": t.published.isoformat(),
            "submissionDeadline": t.deadline.isoformat(),
            "status": t.status,
            "documents": sorted(n for d in t.docs for n in unpacked_names(t.id, d)),
            "referenceNumber": t.reference,
        }
        if kind in ("ssr", "js"):
            rec |= {
                "description": t.description,
                "procedureType": t.procedure,
                "contractNature": t.nature,
                "cpvCodes": list(t.cpv),
                "placeOfPerformance": {"postalCode": t.postal_code, "city": t.city, "countryCode": t.country},
            }
        if kind == "multistep":
            rec["cpvCodes"] = list(t.cpv)
        if kind in ("form", "captcha"):
            rec.pop("referenceNumber")
        out.append(rec)
    return out
